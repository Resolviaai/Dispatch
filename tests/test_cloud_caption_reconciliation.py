"""Cloud-to-PC caption reconciliation (v1.5, offline editing §6).

The phone can save caption edits to Dispatch Cloud while the PC is offline.
The PC must:
- import them under a MONOTONIC revision guard (no silent last-write-wins),
- refuse cross-account tracks (ownership guard),
- validate the payload shape (invalid data never replaces a valid track),
- mark the local render stale and stamp the authored range revision,
- expose a sync wrapper that pulls newer tracks per account (mocked HTTP).
"""
import json
import unittest
from unittest.mock import patch

from dispatch.cloud import sync as cloud_sync
from dispatch.db import get_db_connection, import_cloud_caption_data, init_db

USER_A = "aaaaaaaa-1111-1111-1111-111111111111"
USER_B = "bbbbbbbb-2222-2222-2222-222222222222"

TRACK_V1 = {
    "groups": [{"id": "g1", "start": 0.0, "end": 1.5, "text": "hi", "words": []}],
}
TRACK_V2 = {
    "groups": [{"id": "g1", "start": 0.0, "end": 2.0, "text": "hi there", "words": []}],
}


def _insert_clip(clip_id: str, user_id=None, range_revision: int = 3):
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO clips (id, start_time, end_time, duration, title, hook, status,
                               user_id, range_revision)
            VALUES (?, 0.0, 5.0, 5.0, 'Caption Sync Clip', 'hook', 'ready_review', ?, ?)
        """, (clip_id, user_id, range_revision))
        conn.commit()


def _fetch(clip_id: str):
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT caption_data, cloud_caption_revision, caption_range_revision, render_stale "
            "FROM clips WHERE id = ?",
            (clip_id,)
        ).fetchone()
        return dict(row) if row else None


class TestCloudCaptionReconciliation(unittest.TestCase):

    def setUp(self):
        init_db()

    def test_import_applies_newer_revision_and_marks_render_stale(self):
        clip_id = "cap_sync_01"
        _insert_clip(clip_id, user_id=USER_A)
        outcome = import_cloud_caption_data(
            clip_id, TRACK_V1, cloud_revision=5, expected_user_id=USER_A,
        )
        self.assertEqual(outcome, "applied")
        row = _fetch(clip_id)
        self.assertEqual(row["cloud_caption_revision"], 5)
        self.assertIn("hi", str(row["caption_data"]))
        self.assertEqual(row["render_stale"], 1, "the old preview must not survive an offline edit")

    def test_import_is_stale_guarded_monotonic(self):
        clip_id = "cap_sync_02"
        _insert_clip(clip_id, user_id=USER_A)
        self.assertEqual(import_cloud_caption_data(clip_id, TRACK_V1, 4, expected_user_id=USER_A), "applied")
        # An OLDER revision arriving later (out-of-order sync, stale device) never wins.
        self.assertEqual(import_cloud_caption_data(clip_id, TRACK_V2, 3, expected_user_id=USER_A), "stale")
        self.assertEqual(_fetch(clip_id)["cloud_caption_revision"], 4)
        self.assertNotIn("hi there", str(_fetch(clip_id)["caption_data"]))
        # Same-revision replay is stale too (idempotent acknowledgement).
        self.assertEqual(import_cloud_caption_data(clip_id, TRACK_V2, 4, expected_user_id=USER_A), "stale")

    def test_import_refuses_cross_account_tracks(self):
        clip_id = "cap_sync_03"
        _insert_clip(clip_id, user_id=USER_B)
        outcome = import_cloud_caption_data(clip_id, TRACK_V1, 6, expected_user_id=USER_A)
        self.assertEqual(outcome, "denied")
        row = _fetch(clip_id)
        self.assertIsNone(row["caption_data"])
        self.assertEqual(row["cloud_caption_revision"], 0)

    def test_import_rejects_invalid_payload_shapes(self):
        clip_id = "cap_sync_04"
        _insert_clip(clip_id, user_id=USER_A)
        self.assertEqual(import_cloud_caption_data(clip_id, {"no_groups": True}, 6, expected_user_id=USER_A), "invalid")
        self.assertEqual(import_cloud_caption_data(clip_id, "definitely-not{json", 6, expected_user_id=USER_A), "invalid")
        self.assertEqual(import_cloud_caption_data(clip_id, TRACK_V1, 0, expected_user_id=USER_A), "invalid")
        self.assertIsNone(_fetch(clip_id)["caption_data"])

    def test_import_stamps_authored_range_revision(self):
        """The track carries the range revision it was authored against; if the
        local clip has since been trimmed, approval must 409 instead of
        rendering stale captions."""
        clip_id = "cap_sync_05"
        _insert_clip(clip_id, user_id=USER_A, range_revision=3)
        outcome = import_cloud_caption_data(
            clip_id, TRACK_V1, cloud_revision=2, expected_user_id=USER_A,
            cloud_range_revision=2,
        )
        self.assertEqual(outcome, "applied")
        row = _fetch(clip_id)
        self.assertEqual(row["caption_range_revision"], 2)
        self.assertNotEqual(row["cloud_caption_revision"], 0)

    def test_import_unknown_clip_reports_unknown(self):
        self.assertEqual(
            import_cloud_caption_data("cap_ghost", TRACK_V1, 7, expected_user_id=USER_A),
            "unknown",
        )

    def test_sync_wrapper_pulls_newer_tracks_per_account(self):
        clip_id = "cap_sync_06"
        _insert_clip(clip_id, user_id=USER_A)

        class FakeResponse:
            def __init__(self, status, body):
                self.status_code = status
                self.ok = status < 400
                self.text = json.dumps(body)
                self._body = body

            def json(self):
                return self._body

        seen = []

        def fake_get(url, headers=None, timeout=None):
            seen.append((url, headers))
            assert url.endswith("/sync/pc/captions"), (
                "caption pulls go through the owner-scoped worker endpoint"
            )
            assert headers.get("X-Dispatch-User-Id") == USER_A, (
                "each pull is scoped per account"
            )
            return FakeResponse(200, {"clips": [{
                "local_clip_id": clip_id,
                "caption_data": TRACK_V2,
                "caption_revision": 9,
                "range_revision": 3,
            }]})

        with patch.object(cloud_sync.requests, "get", side_effect=fake_get), \
             patch.object(cloud_sync, "get_worker_token", return_value="ci-worker-token"):
            applied = cloud_sync.import_cloud_captions_for_clips([USER_A])
        self.assertEqual(applied, 1)
        url, headers = seen[0]
        self.assertTrue(url.endswith("/sync/pc/captions"))
        self.assertEqual(headers.get("X-Dispatch-User-Id"), USER_A)
        row = _fetch(clip_id)
        self.assertEqual(row["cloud_caption_revision"], 9)
        self.assertIn("hi there", str(row["caption_data"]))

        # A repeat pass is a no-op (monotonic guard at the wrapper level too).
        with patch.object(cloud_sync.requests, "get", side_effect=fake_get), \
             patch.object(cloud_sync, "get_worker_token", return_value="ci-worker-token"):
            self.assertEqual(cloud_sync.import_cloud_captions_for_clips([USER_A]), 0)

    def test_sync_wrapper_tolerates_bare_list_body(self):
        """Legacy/compat: the endpoint may respond with a bare clip array; the
        wrapper must still import it."""
        clip_id = "cap_sync_06b"
        _insert_clip(clip_id, user_id=USER_A)

        class FakeResponse:
            def __init__(self, body):
                self.status_code = 200
                self.ok = True
                self.text = json.dumps(body)
                self._body = body

            def json(self):
                return self._body

        def fake_get(url, headers=None, timeout=None):
            self.assertTrue(url.endswith("/sync/pc/captions"))
            return FakeResponse([{
                "local_clip_id": clip_id,
                "caption_data": TRACK_V1,
                "caption_revision": 4,
                "range_revision": 3,
            }])

        with patch.object(cloud_sync.requests, "get", side_effect=fake_get), \
             patch.object(cloud_sync, "get_worker_token", return_value="ci-worker-token"):
            self.assertEqual(cloud_sync.import_cloud_captions_for_clips([USER_A]), 1)
        self.assertIn("hi", str(_fetch(clip_id)["caption_data"]))


    def test_sync_wrapper_wraps_track_objects_before_import(self):
        """The worker hands caption_data back as an OBJECT (jsonb); the
        wrapper must serialize it rather than choke on the type."""
        clip_id = "cap_sync_07"
        _insert_clip(clip_id, user_id=USER_A)

        class FakeResponse:
            def __init__(self, body):
                self.status_code = 200
                self.ok = True
                self.text = json.dumps(body)
                self._body = body

            def json(self):
                return self._body

        def fake_get(url, headers=None, timeout=None):
            return FakeResponse({"clips": [{
                "local_clip_id": clip_id,
                "caption_data": TRACK_V1,
                "caption_revision": 2,
                "range_revision": 3,
            }]})

        with patch.object(cloud_sync.requests, "get", side_effect=fake_get), \
             patch.object(cloud_sync, "get_worker_token", return_value="ci-worker-token"):
            self.assertEqual(cloud_sync.import_cloud_captions_for_clips([USER_A]), 1)
        self.assertIn("hi", str(_fetch(clip_id)["caption_data"]))


if __name__ == "__main__":
    unittest.main()
