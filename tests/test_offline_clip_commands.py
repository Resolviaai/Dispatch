"""Offline clip commands: durable reject/restore state machine (v1.5).

Covers:
- SQLite tombstone columns exist after init_db.
- soft_reject preserves learning + media during grace (Reject != Delete).
- The 24h deadline is measured from the ORIGINAL user request (payload
  purge_after), not from whenever the PC reconnects.
- Duplicate deliveries are idempotent (learning row + first deadline kept).
- restore inside grace fully reverses (status + learning row deleted).
- Ownership isolation: one account's commands can never mutate another
  account's clips; NULL-owner legacy clips adopt the first legitimate account.
- Local purge after grace keeps cloud_reject_purge_at until the cloud/R2 flush
  confirms deletion (mark_cloud_media_purged), so failures retry.
- restore of purged media is truthful ('gone' -> ack 'failed').
- Unknown-clip commands are acked 'unknown' (re-delivered to the owning
  machine), never swallowed as done/voided (audit 1A).
- A failed local unlink RETAINS the media path; cloud purge confirmation is
  gated on local completion (mark_cloud_media_purged).
- The drain loop acks per-command results (mocked worker HTTP).
"""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from dispatch.cloud import sync as cloud_sync
from dispatch.db import (
    get_db_connection,
    init_db,
    import_cloud_caption_data,
    list_expired_cloud_rejections,
    mark_cloud_media_purged,
    purge_expired_rejections,
    soft_reject_clip,
)

USER_A = "aaaaaaaa-1111-1111-1111-111111111111"
USER_B = "bbbbbbbb-2222-2222-2222-222222222222"


def _insert_clip(clip_id: str, video_path: str = "", thumbnail_path: str = "",
                 status: str = "ready_review", user_id: str = None):
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO clips (id, start_time, end_time, duration, title, hook,
                               video_path, thumbnail_path, status, user_id)
            VALUES (?, 0.0, 5.0, 5.0, 'Offline Reject Clip', 'hook', ?, ?, ?, ?)
        """, (clip_id, video_path, thumbnail_path, status, user_id))
        conn.commit()


def _fetch_clip(clip_id: str):
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        return dict(row) if row is not None else None


def _rejection_count(clip_id: str) -> int:
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM rejections WHERE clip_id = ?", (clip_id,)
        ).fetchone()["c"]


class TestOfflineClipCommands(unittest.TestCase):

    def setUp(self):
        init_db()
        self._tmp = tempfile.mkdtemp(prefix="dispatch_offline_reject_")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def _make_media_files(self, clip_id: str):
        video = os.path.join(self._tmp, f"{clip_id}.mp4")
        thumb = os.path.join(self._tmp, f"{clip_id}.jpg")
        with open(video, "wb") as f:
            f.write(b"video")
        with open(thumb, "wb") as f:
            f.write(b"thumb")
        return video, thumb

    def test_tombstone_columns_exist(self):
        with get_db_connection() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(clips)").fetchall()}
        self.assertIn("cloud_reject_purge_at", cols)
        self.assertIn("cloud_reject_reason", cols)
        self.assertIn("cloud_rejected_at", cols)
        self.assertIn("cloud_caption_revision", cols)
        self.assertIn("cloud_r2_purged_at", cols)

    def test_soft_reject_keeps_media_and_records_learning(self):
        clip_id = "rej_soft_01"
        video, thumb = self._make_media_files(clip_id)
        _insert_clip(clip_id, video_path=video, thumbnail_path=thumb)

        purge_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        verdict = cloud_sync.apply_clip_command({
            "id": "cmd-1", "kind": "reject", "local_clip_id": clip_id,
            "payload": {"reason": "offline reject test", "purge_after": purge_at},
        })
        self.assertEqual(verdict, "done")

        clip = _fetch_clip(clip_id)
        self.assertEqual(clip["status"], "rejected")
        # media retained through the grace window
        self.assertTrue(os.path.exists(video))
        self.assertTrue(os.path.exists(thumb))
        self.assertIsNotNone(clip["cloud_reject_purge_at"])
        self.assertEqual(clip["cloud_reject_reason"], "offline reject test")

        self.assertEqual(_rejection_count(clip_id), 1)

    def test_reject_uses_request_time_purge_deadline_not_reconnect_time(self):
        """Problem C fix: the grace is anchored to the phone's request stamp."""
        clip_id = "rej_purgewhen_01"
        _insert_clip(clip_id)
        # Phone requested at T; the PC only sees the command 3 days later.
        requested = (datetime.now(timezone.utc) - timedelta(days=3))
        purge_at = (requested + timedelta(days=1)).isoformat()
        assert requested + timedelta(days=1) < datetime.now(timezone.utc)  # already elapsed

        verdict = cloud_sync.apply_clip_command({
            "id": "cmd-t", "kind": "reject", "local_clip_id": clip_id,
            "payload": {"purge_after": purge_at},
        })
        self.assertEqual(verdict, "done")
        clip = _fetch_clip(clip_id)
        self.assertEqual(clip["cloud_reject_purge_at"], purge_at,
                         "deadline must be the request-time stamp, not PC-apply time")

        # The request-expired tombstone is a flush candidate for the purge sweep
        # (its media-free state's counting is covered by the dedicated purge test).
        self.assertIn(clip_id, [r["id"] for r in list_expired_cloud_rejections()])
        self.assertTrue(mark_cloud_media_purged(clip_id))

    def test_duplicate_reject_delivery_is_idempotent(self):
        clip_id = "rej_dup_01"
        _insert_clip(clip_id)
        purge_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        cmd = {"id": "cmd-1", "kind": "reject", "local_clip_id": clip_id,
               "payload": {"reason": "first", "purge_after": purge_at}}
        self.assertEqual(cloud_sync.apply_clip_command(cmd), "done")
        self.assertEqual(cloud_sync.apply_clip_command(
            {**cmd, "payload": {"reason": "second", "purge_after": purge_at}}), "done")
        self.assertEqual(_rejection_count(clip_id), 1)
        self.assertEqual(_fetch_clip(clip_id)["cloud_reject_reason"], "first",
                         "the original first-tap tombstone wins")

    def test_restore_within_grace_fully_reverses(self):
        clip_id = "rej_restore_01"
        video, thumb = self._make_media_files(clip_id)
        _insert_clip(clip_id, video_path=video, thumbnail_path=thumb)
        cloud_sync.apply_clip_command({"id": "c-r1", "kind": "reject", "local_clip_id": clip_id, "payload": {}})

        verdict = cloud_sync.apply_clip_command({"id": "c-r2", "kind": "restore", "local_clip_id": clip_id, "payload": {}})
        self.assertEqual(verdict, "done")
        clip = _fetch_clip(clip_id)
        self.assertEqual(clip["status"], "ready_review")
        self.assertIsNone(clip["cloud_reject_purge_at"])
        # Undo is a FULL reversal: the learning row created by the reject goes too.
        self.assertEqual(_rejection_count(clip_id), 0)
        self.assertTrue(os.path.exists(video))

    def test_account_cannot_mutate_another_accounts_clip(self):
        clip_id = "rej_owned_B"
        _insert_clip(clip_id, user_id=USER_B)

        verdict = cloud_sync.apply_clip_command({
            "id": "c-a1", "kind": "reject", "local_clip_id": clip_id,
            "payload": {}, }, context_user_id=USER_A)
        self.assertEqual(verdict, "failed", "USER_A command on USER_B clip denied")
        clip = _fetch_clip(clip_id)
        self.assertEqual(clip["status"], "ready_review", "no tombstone applied")
        self.assertEqual(_rejection_count(clip_id), 0)
        self.assertIsNone(clip["cloud_reject_purge_at"])

        # Same for restores.
        verdict_restore = cloud_sync.apply_clip_command({
            "id": "c-a2", "kind": "restore", "local_clip_id": clip_id,
            "payload": {}, }, context_user_id=USER_A)
        self.assertEqual(verdict_restore, "failed")

    def test_owner_of_record_can_reject_and_unowned_clip_adopts_first_account(self):
        clip_id = "rej_owned_A"
        _insert_clip(clip_id, user_id=USER_A)
        verdict = cloud_sync.apply_clip_command({
            "id": "c-a", "kind": "reject", "local_clip_id": clip_id, "payload": {},
        }, context_user_id=USER_A)
        self.assertEqual(verdict, "done")

        legacy = "rej_legacy_unowned"
        _insert_clip(legacy)
        self.assertEqual(cloud_sync.apply_clip_command({
            "id": "c-l", "kind": "reject", "local_clip_id": legacy, "payload": {},
        }, context_user_id=USER_A), "done")
        # Adoption binds the clip to USER_A: USER_B can no longer mutate it.
        self.assertEqual(cloud_sync.apply_clip_command({
            "id": "c-l2", "kind": "restore", "local_clip_id": legacy, "payload": {},
        }, context_user_id=USER_B), "failed")

    def test_local_purge_keeps_anchor_until_cloud_flush_confirms(self):
        clip_id = "rej_purge_01"
        video, thumb = self._make_media_files(clip_id)
        _insert_clip(clip_id, video_path=video, thumbnail_path=thumb,
                     user_id=USER_A)

        purge_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        self.assertEqual(soft_reject_clip(clip_id, reason="expired grace",
                                          purge_at=purge_at), "applied")

        purged = purge_expired_rejections()
        self.assertEqual(purged, 1)
        self.assertFalse(os.path.exists(video))
        self.assertFalse(os.path.exists(thumb))
        clip = _fetch_clip(clip_id)
        self.assertEqual(clip["status"], "rejected")
        # v1.5: anchor KEPT while cloud/R2 flush is still pending (retries).
        self.assertIsNotNone(clip["cloud_reject_purge_at"])
        self.assertEqual(_rejection_count(clip_id), 1)

        # Idempotency: the second local sweep purges nothing new.
        self.assertEqual(purge_expired_rejections(), 0)

        # Flush sweep identifies the row ...
        expired = list_expired_cloud_rejections()
        self.assertIn(clip_id, [r["id"] for r in expired])

        # ... and local confirmation only lands once R2 deletion succeeds.
        self.assertTrue(mark_cloud_media_purged(clip_id))
        clip = _fetch_clip(clip_id)
        self.assertIsNone(clip["cloud_reject_purge_at"])
        self.assertIsNotNone(clip["cloud_r2_purged_at"])
        self.assertEqual(purge_expired_rejections(), 0)
        self.assertEqual(list_expired_cloud_rejections(), [])

    def test_late_restore_after_media_purge_is_truthful_failure(self):
        clip_id = "rej_late_restore"
        _insert_clip(clip_id, user_id=USER_A)
        purge_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        soft_reject_clip(clip_id, reason="expired", purge_at=purge_at)
        # Simulate a completed purge: media gone, clip still status rejected.
        with get_db_connection() as conn:
            conn.execute("UPDATE clips SET video_path = NULL, thumbnail_path = NULL WHERE id = ?", (clip_id,))
            conn.commit()

        verdict = cloud_sync.apply_clip_command({
            "id": "c-late", "kind": "restore", "local_clip_id": clip_id, "payload": {},
        }, context_user_id=USER_A)
        self.assertEqual(verdict, "failed", "a restore cannot resurrect purged media")
        clip = _fetch_clip(clip_id)
        self.assertEqual(clip["status"], "rejected", "tombstone survives a late restore")
        # Cleanup so later suites keep a tidy DB (post-grace state is terminal).
        self.assertTrue(mark_cloud_media_purged(clip_id))

    def test_cloud_purge_flush_marks_only_after_r2_success(self):
        clip_id = "rej_r2_flush"
        _insert_clip(clip_id, user_id=USER_A)
        purge_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        soft_reject_clip(clip_id, reason="grace over", purge_at=purge_at)
        with get_db_connection() as conn:
            conn.execute("UPDATE clips SET video_path = NULL, thumbnail_path = NULL WHERE id = ?", (clip_id,))
            conn.commit()

        class FakeResponse:
            def __init__(self, status, body):
                self.status_code = status
                self.text = ""
                self._body = body

            @property
            def ok(self):
                return self.status_code < 400

            def json(self):
                return self._body

        urls = []

        def fake_post(url, headers=None, json=None, timeout=None):
            urls.append(url)
            return FakeResponse(502, {"error": "r2 down"})

        with patch.object(cloud_sync.requests, "post", side_effect=fake_post):
            flushed = cloud_sync._flush_cloud_purges("https://w.test", "tok")
        self.assertEqual(flushed, 0)
        self.assertTrue(urls and urls[0].endswith(f"/sync/clips/{clip_id}/purge-media"),
                        "flush targets the owned worker endpoint")
        self.assertIsNone(_fetch_clip(clip_id)["cloud_r2_purged_at"])

        # After R2 accepts, the marker flips.
        def fake_post_ok(url, headers=None, json=None, timeout=None):
            urls.append(url)
            return FakeResponse(200, {"purged": 1, "remaining": 0, "cloud_clip_id": "cc1"})

        with patch.object(cloud_sync.requests, "post", side_effect=fake_post_ok):
            flushed = cloud_sync._flush_cloud_purges("https://w.test", "tok")
        self.assertEqual(flushed, 1)
        self.assertIsNotNone(_fetch_clip(clip_id)["cloud_r2_purged_at"])

    def test_unknown_clip_command_is_acked_unknown_for_redelivery(self):
        """Audit 1A: a command for a clip this PC does not hold must be acked
        'unknown' so the Worker re-delivers it to the owning machine. Acking
        done/voided would swallow the user's request forever."""
        ghost_id = "rej_not_on_this_pc"

        class FakeResponse:
            def __init__(self, status, body):
                self.status_code = status
                self.text = json.dumps(body)
                self._body = body

            @property
            def ok(self):
                return self.status_code < 400

            def json(self):
                return self._body

        claims = [{
            "id": "2f0d4a3b-1111-2222-3333-444455556667",
            "local_clip_id": ghost_id,
            "kind": "reject",
            "payload": {"reason": "for whatever machine holds it"},
            "attempts": 1,
        }]
        acks = []

        def fake_post(url, headers=None, json=None, timeout=None):
            if url.endswith("/sync/commands/claim"):
                return FakeResponse(200, {"commands": claims})
            if url.endswith("/sync/commands/ack"):
                acks.append(json)
                return FakeResponse(200, {"ok": True})
            return FakeResponse(404, {})

        def fake_get(url, headers=None, timeout=None):
            return FakeResponse(200, [])

        with patch.object(cloud_sync.requests, "post", side_effect=fake_post), \
             patch.object(cloud_sync.requests, "get", side_effect=fake_get), \
             patch.object(cloud_sync, "_resolve_drain_user_ids", return_value=[USER_A]):
            applied = cloud_sync.drain_clip_commands(limit=5)
        self.assertEqual(applied, 1)
        self.assertEqual(len(acks), 1)
        self.assertEqual(acks[0]["result"], "unknown", "missing clips are re-delivered, not voided")
        self.assertTrue(acks[0].get("error"))
        self.assertIsNone(_fetch_clip(ghost_id), "nothing was created locally by the ghost command")

    def test_reject_race_vanished_clip_acks_unknown(self):
        """If the clip disappears between the local precheck and the apply,
        soft_reject_clip reports 'unknown'; the drain must surface that verbatim
        instead of coercing it into 'failed'."""
        with patch.object(cloud_sync.db, "soft_reject_clip", return_value="unknown"):
            verdict, reason = cloud_sync._apply_clip_command(
                {"id": "c-race", "kind": "reject", "local_clip_id": "rej_race_vanish",
                 "payload": {"reason": "raced away"}},
                context_user_id=USER_A,
            )
        self.assertEqual(verdict, "unknown")
        self.assertIn("re-deliver", str(reason))

    def test_purge_retains_path_when_unlink_fails(self):
        """A locked/failed unlink must retain the media path (retry next sweep)
        and must NOT let the cloud purge confirmation land while local media
        still exists (mark_cloud_media_purged is gated on local completion)."""
        clip_id = "rej_purge_retry"
        video, thumb = self._make_media_files(clip_id)
        _insert_clip(clip_id, video_path=video, thumbnail_path=thumb,
                     user_id=USER_A)

        purge_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        self.assertEqual(soft_reject_clip(clip_id, reason="expired grace",
                                          purge_at=purge_at), "applied")

        fake_file = MagicMock()
        fake_file.exists.return_value = True
        fake_file.is_file.return_value = True
        fake_file.unlink.side_effect = OSError("file locked by renderer")
        with patch("dispatch.db.Path", return_value=fake_file):
            purged = purge_expired_rejections()
        self.assertEqual(purged, 0, "nothing was cleared: the unlink failed")

        clip = _fetch_clip(clip_id)
        self.assertEqual(clip["video_path"], video, "failed unlink retains the path for retry")
        self.assertEqual(clip["thumbnail_path"], thumb)
        self.assertIsNotNone(clip["cloud_reject_purge_at"], "purge anchor kept while local media persists")
        self.assertTrue(os.path.exists(video), "file untouched on failed unlink")

        # Gate: cloud confirmation must not land while a local path remains.
        self.assertFalse(mark_cloud_media_purged(clip_id))

        # Once the lock clears, the retry completes the local purge ...
        self.assertEqual(purge_expired_rejections(), 1)
        self.assertFalse(os.path.exists(video))
        self.assertFalse(os.path.exists(thumb))
        clip = _fetch_clip(clip_id)
        self.assertIsNone(clip["video_path"])
        self.assertIsNone(clip["thumbnail_path"])
        # ... and only then does the cloud confirmation flip the anchor.
        self.assertTrue(mark_cloud_media_purged(clip_id))
        self.assertIsNone(_fetch_clip(clip_id)["cloud_reject_purge_at"])

    def test_drain_claims_applies_and_acks(self):
        clip_id = "rej_drain_01"
        _insert_clip(clip_id)
        claims = [{
            "id": "2f0d4a3b-1111-2222-3333-444455556666",
            "local_clip_id": clip_id,
            "kind": "reject",
            "payload": {"reason": "drain test"},
            "attempts": 1,
        }]
        acks = []
        claim_owners = []

        class FakeResponse:
            def __init__(self, status, body):
                self.status_code = status
                self.text = ""
                self._body = body

            @property
            def ok(self):
                return self.status_code < 400

            def json(self):
                return self._body

        def fake_post(url, headers=None, json=None, timeout=None):
            if url.endswith("/sync/commands/claim"):
                claim_owners.append(headers.get("X-Dispatch-User-Id"))
                return FakeResponse(200, {"commands": claims})
            if url.endswith("/sync/commands/ack"):
                acks.append(json)
                return FakeResponse(200, {"ok": True})
            return FakeResponse(404, {})

        def fake_get(url, headers=None, timeout=None):
            # Keep the caption reconciliation pass hermetic (no live Supabase).
            return FakeResponse(200, [])

        with patch.object(cloud_sync.requests, "post", side_effect=fake_post), \
             patch.object(cloud_sync.requests, "get", side_effect=fake_get):
            with patch.object(cloud_sync, "_resolve_drain_user_ids", return_value=[USER_A]):
                applied = cloud_sync.drain_clip_commands(limit=5)
        self.assertEqual(applied, 1)
        self.assertEqual(claim_owners, [USER_A],
                         "commands are claimed per account, not via 'latest profile'")
        self.assertEqual(len(acks), 1)
        self.assertEqual(acks[0]["command_id"], "2f0d4a3b-1111-2222-3333-444455556666")
        self.assertEqual(acks[0]["result"], "done")
        self.assertEqual(_fetch_clip(clip_id)["cloud_reject_reason"], "drain test")


if __name__ == "__main__":
    unittest.main()
