"""Offline clip commands: cloud reject/restore queue + 24h grace purge.

Covers:
- SQLite tombstone columns exist after init_db.
- soft_reject_clip preserves learning + media during grace (Reject != Delete).
- restore_rejected_clip restores inside the grace window.
- purge_expired_rejections is idempotent, no-throw, and keeps learning rows.
- drain_clip_commands claims/applies/acks against the worker (mocked HTTP).
"""
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from dispatch.cloud import sync as cloud_sync
from dispatch.db import get_db_connection, init_db, purge_expired_rejections, soft_reject_clip


def _insert_clip(clip_id: str, video_path: str = "", thumbnail_path: str = "", status: str = "ready_review"):
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO clips (id, start_time, end_time, duration, title, hook, video_path, thumbnail_path, status)
            VALUES (?, 0.0, 5.0, 5.0, 'Offline Reject Clip', 'hook', ?, ?, ?)
        """, (clip_id, video_path, thumbnail_path, status))
        conn.commit()


def _fetch_clip(clip_id: str):
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        return dict(row) if row is not None else None


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

    def test_soft_reject_keeps_media_and_records_learning(self):
        clip_id = "rej_soft_01"
        video, thumb = self._make_media_files(clip_id)
        _insert_clip(clip_id, video_path=video, thumbnail_path=thumb)

        purge_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        ok = cloud_sync.apply_clip_command({
            "id": "cmd-1", "kind": "reject", "local_clip_id": clip_id,
            "payload": {"reason": "offline reject test"},
        })
        self.assertEqual(ok, "done")

        clip = _fetch_clip(clip_id)
        self.assertEqual(clip["status"], "rejected")
        # media retained through the grace window
        self.assertTrue(os.path.exists(video))
        self.assertTrue(os.path.exists(thumb))
        self.assertIsNotNone(clip["cloud_reject_purge_at"])
        self.assertEqual(clip["cloud_reject_reason"], "offline reject test")

        with get_db_connection() as conn:
            learning = conn.execute(
                "SELECT reason FROM rejections WHERE clip_id = ?", (clip_id,)
            ).fetchone()
        self.assertIsNotNone(learning)

        # Idempotent duplicate delivery: no second learning row, no throw.
        ok2 = cloud_sync.apply_clip_command({
            "id": "cmd-1", "kind": "reject", "local_clip_id": clip_id,
            "payload": {"reason": "duplicate"},
        })
        self.assertEqual(ok2, "done")
        with get_db_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM rejections WHERE clip_id = ?", (clip_id,)
            ).fetchone()["c"]
        self.assertEqual(count, 1)

    def test_restore_within_grace_restores_clip(self):
        clip_id = "rej_restore_01"
        video, thumb = self._make_media_files(clip_id)
        _insert_clip(clip_id, video_path=video, thumbnail_path=thumb)
        cloud_sync.apply_clip_command({"id": "c-r1", "kind": "reject", "local_clip_id": clip_id, "payload": {}})

        verdict = cloud_sync.apply_clip_command({"id": "c-r2", "kind": "restore", "local_clip_id": clip_id, "payload": {}})
        self.assertEqual(verdict, "done")
        clip = _fetch_clip(clip_id)
        self.assertEqual(clip["status"], "ready_review")
        self.assertIsNone(clip["cloud_reject_purge_at"])
        # media untouched by restore
        self.assertTrue(os.path.exists(video))

    def test_purge_after_grace_removes_media_keeps_learning_idempotent(self):
        clip_id = "rej_purge_01"
        video, thumb = self._make_media_files(clip_id)
        _insert_clip(clip_id, video_path=video, thumbnail_path=thumb)

        # Set an already-elapsed purge deadline directly through db.
        purge_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        self.assertTrue(soft_reject_clip(clip_id, reason="expired grace", purge_at=purge_at))

        purged = purge_expired_rejections()
        self.assertEqual(purged, 1)
        self.assertFalse(os.path.exists(video))
        self.assertFalse(os.path.exists(thumb))
        clip = _fetch_clip(clip_id)
        self.assertEqual(clip["status"], "rejected")
        self.assertIsNone(clip["cloud_reject_purge_at"])
        with get_db_connection() as conn:
            learning = conn.execute(
                "SELECT COUNT(*) AS c FROM rejections WHERE clip_id = ?", (clip_id,)
            ).fetchone()["c"]
        self.assertEqual(learning, 1)

        # Idempotency: second sweep purges nothing and never throws.
        purged_again = purge_expired_rejections()
        self.assertEqual(purged_again, 0)

        # A late restore after purge stays no-throw; tombstone row is cleared.
        verdict = cloud_sync.apply_clip_command({"id": "c-late", "kind": "restore", "local_clip_id": clip_id, "payload": {}})
        self.assertEqual(verdict, "done")
        clip = _fetch_clip(clip_id)
        self.assertIsNone(clip["cloud_reject_purge_at"])

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
                return FakeResponse(200, {"commands": claims})
            if url.endswith("/sync/commands/ack"):
                acks.append(json)
                return FakeResponse(200, {"ok": True})
            return FakeResponse(404, {})

        with patch.object(cloud_sync.requests, "post", side_effect=fake_post):
            applied = cloud_sync.drain_clip_commands(limit=5)
        self.assertEqual(applied, 1)
        self.assertEqual(len(acks), 1)
        self.assertEqual(acks[0]["command_id"], "2f0d4a3b-1111-2222-3333-444455556666")
        self.assertEqual(acks[0]["result"], "done")
