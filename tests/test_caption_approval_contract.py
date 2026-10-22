import json
import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from dispatch import db
from dispatch.captions import CaptionTrack
from dispatch.web.app import app, _acquire_clip_mutation_lock


client = TestClient(app)


class TestCaptionApprovalContract(unittest.TestCase):
    def setUp(self):
        db.init_db()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"test source")
        self.clip_id = db.save_clip(
            session_id=None,
            chunk_id=None,
            start_time=0,
            end_time=24,
            title="Approval contract test",
            hook="test",
            description="",
            hashtags="#test",
            virality_score=50,
            layout_mode="fit_blur",
        )

    def tearDown(self):
        self.temp_dir.cleanup()
        with db.get_db_connection() as conn:
            conn.execute("DELETE FROM publishing_outbox WHERE clip_id = ?", (self.clip_id,))
            conn.execute("DELETE FROM clips WHERE id = ?", (self.clip_id,))

    def _caption_snapshot(self):
        response = client.get(f"/api/clips/{self.clip_id}/captions")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _render_mocks(self, calls):
        output = self.root / "final.mp4"

        def render(**kwargs):
            calls.append("render")
            output.write_bytes(b"validated by patched probe")
            db.update_clip_media(kwargs["clip_id"], str(output), None, kwargs["layout_mode"])
            return {"video_path": str(output)}

        def probe(_path):
            calls.append("probe")
            return True, {"duration": 24.0}, None

        return render, probe

    def test_caption_get_does_not_write_or_export(self):
        with patch("dispatch.web.app.db.save_clip_caption_data") as save_track, \
             patch("dispatch.web.app.export_to_ass") as export_ass:
            response = client.get(f"/api/clips/{self.clip_id}/captions")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["groups"], [])
        save_track.assert_not_called()
        export_ass.assert_not_called()

    def test_stale_range_cannot_save_or_approve_captions(self):
        stale_track = {"clip_id": self.clip_id, "range_revision": 7, "groups": []}
        save_response = client.post(f"/api/clips/{self.clip_id}/captions", json=stale_track)
        self.assertEqual(save_response.status_code, 409)
        self.assertIsNone(db.get_clip_by_id(self.clip_id)["caption_data"])

        approve_response = client.post(
            f"/api/clips/{self.clip_id}/approve",
            json={"range_revision": 7, "platforms": "youtube"},
        )
        self.assertEqual(approve_response.status_code, 409)
        self.assertEqual(db.get_clip_by_id(self.clip_id)["status"], "ready_review")

    def test_phrase_only_caption_timing_cannot_be_published(self):
        phrase_track = {
            "clip_id": self.clip_id,
            "groups": [{
                "id": "phrase", "type": "caption", "start": 0, "end": 2,
                "has_word_timing": False, "timing_mode": "phrase",
                "words": [{"id": "phrase-word", "text": "unverified timing", "start": 0, "end": 2, "is_exact": False}],
            }],
        }
        response = client.post(
            f"/api/clips/{self.clip_id}/approve",
            json={"caption_track": phrase_track, "range_revision": 0},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(db.get_clip_by_id(self.clip_id)["status"], "ready_review")
        with db.get_db_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM publishing_outbox WHERE clip_id = ?", (self.clip_id,)).fetchone()[0]
        self.assertEqual(count, 0)

    def test_failed_final_render_keeps_edits_and_never_queues_publish(self):
        caption_snapshot = self._caption_snapshot()
        normalized_snapshot = CaptionTrack.from_dict(caption_snapshot).to_dict()
        with patch("dispatch.web.app.resolve_clip_source_path", return_value=self.source), \
             patch("dispatch.web.app.render_clip", side_effect=RuntimeError("render failed")):
            response = client.post(
                f"/api/clips/{self.clip_id}/approve",
                json={"caption_track": caption_snapshot, "platforms": "youtube"},
            )

        self.assertEqual(response.status_code, 500)
        clip = db.get_clip_by_id(self.clip_id)
        self.assertEqual(clip["status"], "ready_review")
        self.assertEqual(json.loads(clip["caption_data"]), normalized_snapshot)
        with db.get_db_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM publishing_outbox WHERE clip_id = ?", (self.clip_id,)
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_successful_approval_renders_submitted_track_before_queueing(self):
        caption_snapshot = self._caption_snapshot()
        normalized_snapshot = CaptionTrack.from_dict(caption_snapshot).to_dict()
        calls = []
        render, probe = self._render_mocks(calls)
        original_approve = db.approve_clip

        def approve(*args, **kwargs):
            calls.append("approve")
            return original_approve(*args, **kwargs)

        with patch("dispatch.web.app.resolve_clip_source_path", return_value=self.source), \
             patch("dispatch.web.app.render_clip", side_effect=render) as render_clip, \
             patch("dispatch.ingestion.validator.probe_video", side_effect=probe), \
             patch("dispatch.web.app.db.approve_clip", side_effect=approve):
            response = client.post(
                f"/api/clips/{self.clip_id}/approve",
                json={"caption_track": caption_snapshot, "platforms": "youtube"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(render_clip.call_args.kwargs["subtitle_options"]["caption_track"], normalized_snapshot)
        self.assertEqual(calls, ["render", "probe", "approve"])
        self.assertEqual(db.get_clip_by_id(self.clip_id)["status"], "approved")
        retry = client.post(
            f"/api/clips/{self.clip_id}/approve",
            json={"caption_track": caption_snapshot, "platforms": "youtube"},
        )
        self.assertEqual(retry.status_code, 200)
        self.assertTrue(retry.json()["idempotent"])
        self.assertEqual(render_clip.call_count, 1, "approval retry must not replace the published render")
        with db.get_db_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM publishing_outbox WHERE clip_id = ?", (self.clip_id,)
            ).fetchone()[0]
        self.assertEqual(count, 1)


class TestClipMutationLockCancellation(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_waiter_does_not_strand_clip_lock(self):
        lock = threading.Lock()
        lock.acquire()
        waiter = asyncio.create_task(_acquire_clip_mutation_lock(lock))
        await asyncio.sleep(0.02)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter

        lock.release()
        deadline = asyncio.get_running_loop().time() + 1
        while asyncio.get_running_loop().time() < deadline:
            if lock.acquire(blocking=False):
                lock.release()
                return
            await asyncio.sleep(0.01)
        self.fail("abandoned waiter must eventually release its acquired lock")


if __name__ == "__main__":
    unittest.main()
