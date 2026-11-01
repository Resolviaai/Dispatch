"""Regression tests for PC -> cloud clip status reconciliation.

A clip rejected on the PC used to stay ``ready_review`` in ``clips_cloud``
forever, so the phone resurrected it the moment it fell back to Cloud Mode.
"""
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from dispatch import db
from dispatch.cloud import sync as cloud_sync


class _FakeResponse:
    def __init__(self, payload=None, ok=True, status_code=200):
        self._payload = payload if payload is not None else {}
        self.ok = ok
        self.status_code = status_code
        self.text = "ok"

    def json(self):
        return self._payload


class TestCloudRejectPropagation(unittest.TestCase):
    def setUp(self):
        os.environ["DISPATCH_WORKER_TOKEN"] = "test-worker-token"
        db.init_db()
        self._tmp = Path(tempfile.mkdtemp(prefix="dispatch_reject_sync_"))
        self.clip_ids = []

    def tearDown(self):
        with db.get_db_connection() as conn:
            for clip_id in self.clip_ids:
                conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
            conn.commit()
        os.environ.pop("DISPATCH_WORKER_TOKEN", None)

    def _make_clip(self, status, video_path=None):
        clip_id = f"clip_test_{uuid.uuid4().hex[:8]}"
        with db.get_db_connection() as conn:
            conn.execute(
                """INSERT INTO clips
                   (id, title, status, start_time, end_time, duration, user_id, video_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (clip_id, "Reject propagation test", status, 10.0, 40.0, 30.0,
                 str(uuid.uuid4()), str(video_path) if video_path else None),
            )
            conn.commit()
        self.clip_ids.append(clip_id)
        return clip_id

    def test_sweeper_reconciles_rejected_clips(self):
        """Rejected rows must be swept upward, and without re-staging media."""
        rejected = self._make_clip("rejected")
        ready = self._make_clip("ready_review")
        calls = []

        def fake_sync(clip_id, clip=None, status_only=False):
            calls.append((clip_id, clip.get("status") if clip else None, status_only))
            return True

        with patch.object(cloud_sync, "sync_clip_to_cloud", side_effect=fake_sync):
            cloud_sync.sync_all_clips_to_cloud()

        by_id = {c[0]: c for c in calls}
        self.assertIn(rejected, by_id, "rejected clip was never offered to the cloud")
        self.assertTrue(by_id[rejected][2], "rejected clip must sync status only")
        self.assertIn(ready, by_id)
        self.assertFalse(by_id[ready][2], "a reviewable clip still needs its media staged")

    def test_status_only_stops_after_the_clips_row_upsert(self):
        """The status push must not upload bytes for a clip nobody will approve."""
        render = self._tmp / "render.mp4"
        render.write_bytes(b"x" * 64)
        clip_id = self._make_clip("rejected", video_path=render)
        seen = []

        def fake_get(url, headers=None, timeout=None):
            seen.append(("GET", url, None))
            return _FakeResponse(payload={})

        def fake_post(url, headers=None, json=None, timeout=None):
            seen.append(("POST", url, (json or {}).get("status")))
            return _FakeResponse(payload={"cloud_clip_id": "cloud-1"})

        with patch.object(cloud_sync.requests, "get", side_effect=fake_get), \
                patch.object(cloud_sync.requests, "post", side_effect=fake_post):
            ok = cloud_sync.sync_clip_to_cloud(clip_id, status_only=True)

        self.assertTrue(ok)
        clip_posts = [s for s in seen if s[0] == "POST" and s[1].endswith("/sync/pc/clips")]
        self.assertEqual(len(clip_posts), 1, seen)
        self.assertEqual(clip_posts[0][2], "rejected")
        self.assertEqual(
            [s for s in seen if s[0] == "POST" and not s[1].endswith("/sync/pc/clips")],
            [],
            "status-only sync must not stage media",
        )


if __name__ == "__main__":
    unittest.main()
