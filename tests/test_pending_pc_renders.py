"""Phase 4 dual render fallback: PC-side pending render queue drain.

Covers poll_pending_pc_renders / _execute_pending_pc_render with mocked HTTP,
DB, and renderer so no real render or network happens."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from dispatch.cloud import sync

USER_ID = "11111111-1111-1111-1111-111111111111"
CLIP_ID = "clip_local_1"


class _FakeResponse:
    def __init__(self, ok=True, payload=None, status_code=200, text=""):
        self.ok = ok
        self.status_code = status_code
        self.text = text
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def _pending_item(**overrides):
    item = {
        "id": "c-cloud-uuid",
        "local_clip_id": CLIP_ID,
        "clip_id": CLIP_ID,
        "render_requested_at": "2026-11-14T00:00:00Z",
        "range_revision": 3,
        "render_fingerprint": "fp-render",
        "rendered_fingerprint": "fp-rendered",
    }
    item.update(overrides)
    return item


def _clip(owner=USER_ID):
    return {
        "clip_id": CLIP_ID,
        "user_id": owner,
        "start_time": 10.0,
        "end_time": 45.0,
        "layout_mode": "fit_blur",
    }


class TestPollPendingPcRenders(unittest.TestCase):

    def setUp(self):
        self.token_patcher = patch.object(sync, "get_worker_token", return_value="tok")
        self.token_patcher.start()
        self.addCleanup(self.token_patcher.stop)
        self.base_patcher = patch.object(sync, "_worker_base", return_value="https://w.test")
        self.base_patcher.start()
        self.addCleanup(self.base_patcher.stop)
        self.report = MagicMock(return_value=True)
        self.report_patcher = patch.object(sync, "report_render_complete_to_cloud", self.report)
        self.report_patcher.start()
        self.addCleanup(self.report_patcher.stop)
        self.render_clip = MagicMock(return_value={
            "video_path": "C:/fake/render.mp4",
            "render_fingerprint": "fp-render",
            "duration": 35.0,
        })
        self.render_patcher = patch("dispatch.video_engine.renderer.render_clip", self.render_clip)
        self.render_patcher.start()
        self.addCleanup(self.render_patcher.stop)
        # The executor stats and uploads the render output, so it must exist.
        fd, self.render_output = tempfile.mkstemp(suffix=".mp4", prefix="pc_render_test_")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.render_output) and os.remove(self.render_output))
        self.render_clip.return_value["video_path"] = self.render_output

    def _store_mock(self, media_object_id="mo-1", final_key="renders/x.mp4", byte_count=123):
        store = MagicMock()
        store.reserve_upload.return_value = {"reservation": True}
        store.upload_and_finalize.return_value = SimpleNamespace(
            media_object_id=media_object_id, final_key=final_key, byte_count=byte_count)
        return store

    def test_returns_zero_without_worker_token(self):
        with patch.object(sync, "get_worker_token", return_value=""):
            self.assertEqual(sync.poll_pending_pc_renders([USER_ID]), 0)

    def test_empty_queue_processes_nothing(self):
        with patch("dispatch.cloud.sync.requests.get", return_value=_FakeResponse(payload={"renders": []})), \
             patch("dispatch.cloud.sync.requests.post") as post:
            self.assertEqual(sync.poll_pending_pc_renders([USER_ID]), 0)
            post.assert_not_called()

    def test_list_failure_is_contained_per_user(self):
        with patch("dispatch.cloud.sync.requests.get", return_value=_FakeResponse(ok=False, status_code=500)), \
             patch("dispatch.cloud.sync.requests.post") as post:
            self.assertEqual(sync.poll_pending_pc_renders([USER_ID]), 0)
            post.assert_not_called()

    def test_fresh_fingerprint_only_clears_the_flag(self):
        with patch("dispatch.cloud.sync.requests.get", return_value=_FakeResponse(
                payload={"renders": [_pending_item(rendered_fingerprint="fp-render")]})), \
             patch("dispatch.cloud.sync.requests.post", return_value=_FakeResponse()) as post, \
             patch.object(sync, "db") as db:
            db.get_clip_by_id.return_value = _clip()
            completed = sync.poll_pending_pc_renders([USER_ID])
            self.assertEqual(completed, 1)
            self.render_clip.assert_not_called()
            post.assert_called_once()
            self.assertIn("/sync/pc/renders/complete", post.call_args.args[0])

    def test_happy_path_renders_uploads_links_reports_and_clears(self):
        store = self._store_mock()
        posts = []

        def fake_post(url, **kwargs):
            posts.append((url, kwargs))
            return _FakeResponse()

        with patch("dispatch.cloud.sync.requests.get", return_value=_FakeResponse(
                payload={"renders": [_pending_item()]})), \
             patch("dispatch.cloud.sync.requests.post", side_effect=fake_post), \
             patch.object(sync, "db") as db, \
             patch.object(sync, "_resolve_raw_source_and_duration",
                          return_value=(Path("C:/fake/source.mp4"), 120.0)), \
             patch.object(sync, "_WorkerEditWindowMediaStore", MagicMock(return_value=store)):
            db.get_clip_by_id.return_value = _clip()
            completed = sync.poll_pending_pc_renders([USER_ID])

        self.assertEqual(completed, 1)
        kwargs = self.render_clip.call_args.kwargs
        self.assertEqual(kwargs["clip_id"], CLIP_ID)
        self.assertEqual(kwargs["start_time"], 10.0)
        self.assertEqual(kwargs["end_time"], 45.0)
        self.assertEqual(kwargs["layout_mode"], "fit_blur")
        reserve_kwargs = store.reserve_upload.call_args.kwargs
        self.assertEqual(reserve_kwargs["object_kind"], "render")
        self.assertEqual(reserve_kwargs["local_clip_id"], CLIP_ID)
        media_link = [u for u, _ in posts if "/sync/pc/media-link" in u]
        complete = [u for u, _ in posts if "/sync/pc/renders/complete" in u]
        self.assertEqual(len(media_link), 1)
        self.assertEqual(len(complete), 1)
        self.assertEqual(posts[[u for u, _ in posts].index(media_link[0])][1]["json"],
                         {"media_object_id": "mo-1", "local_clip_id": CLIP_ID})
        self.report.assert_called_once_with(
            CLIP_ID, "fp-render", 35.0, r2_key="renders/x.mp4", byte_count=123)

    def test_missing_local_clip_is_skipped_without_clearing(self):
        with patch("dispatch.cloud.sync.requests.get", return_value=_FakeResponse(
                payload={"renders": [_pending_item()]})), \
             patch("dispatch.cloud.sync.requests.post") as post, \
             patch.object(sync, "db") as db:
            db.get_clip_by_id.return_value = None
            self.assertEqual(sync.poll_pending_pc_renders([USER_ID]), 0)
            self.render_clip.assert_not_called()
            post.assert_not_called()

    def test_owner_mismatch_is_skipped_without_clearing(self):
        with patch("dispatch.cloud.sync.requests.get", return_value=_FakeResponse(
                payload={"renders": [_pending_item()]})), \
             patch("dispatch.cloud.sync.requests.post") as post, \
             patch.object(sync, "db") as db:
            db.get_clip_by_id.return_value = _clip(owner="22222222-2222-2222-2222-222222222222")
            self.assertEqual(sync.poll_pending_pc_renders([USER_ID]), 0)
            self.render_clip.assert_not_called()
            post.assert_not_called()

    def test_missing_raw_source_fails_open_for_retry(self):
        with patch("dispatch.cloud.sync.requests.get", return_value=_FakeResponse(
                payload={"renders": [_pending_item()]})), \
             patch("dispatch.cloud.sync.requests.post") as post, \
             patch.object(sync, "db") as db, \
             patch.object(sync, "_resolve_raw_source_and_duration", return_value=(None, None)):
            db.get_clip_by_id.return_value = _clip()
            self.assertEqual(sync.poll_pending_pc_renders([USER_ID]), 0)
            self.render_clip.assert_not_called()
            post.assert_not_called()

    def test_render_failure_keeps_flag_for_next_cycle(self):
        self.render_clip.side_effect = RuntimeError("encoder exploded")
        with patch("dispatch.cloud.sync.requests.get", return_value=_FakeResponse(
                payload={"renders": [_pending_item()]})), \
             patch("dispatch.cloud.sync.requests.post") as post, \
             patch.object(sync, "db") as db, \
             patch.object(sync, "_resolve_raw_source_and_duration",
                          return_value=(Path("C:/fake/source.mp4"), 120.0)):
            db.get_clip_by_id.return_value = _clip()
            self.assertEqual(sync.poll_pending_pc_renders([USER_ID]), 0)
            self.render_clip.assert_called_once()
            post.assert_not_called()
            self.report.assert_not_called()


if __name__ == "__main__":
    unittest.main()
