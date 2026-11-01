import json
import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from dispatch.web.app import app
from dispatch import db
from dispatch.cloud import sync


class TestProjectDocumentStage2(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        db.init_db()

    def test_retired_split_endpoint_returns_410(self):
        """HTTP 410 must be returned when calling legacy POST /api/clips/{id}/split."""
        with patch("dispatch.web.app.verify_dashboard_auth", return_value=True), \
             patch("dispatch.web.app.resolve_caller_user_id", return_value="test-user"):
            res = self.client.post(
                "/api/clips/clip_dummy_123/split",
                json={"split_time": 5.0, "is_source_time": False},
            )
            self.assertEqual(res.status_code, 410)
            data = res.json()
            self.assertIn("retired", data.get("detail", ""))
            self.assertIn("mobile timeline editing", data.get("detail", ""))

    def test_import_cloud_timeline_data_monotonic_authority(self):
        """Monotonic revision authority: strictly newer revisions apply, older or equal are stale."""
        clip_id = "test_clip_mono_stage2"
        user_id = "user_mono_123"
        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
            cursor.execute("""
                INSERT INTO clips (id, title, user_id, start_time, end_time, duration, status, segments_revision, active_state_revision)
                VALUES (?, 'Mono Test', ?, 10.0, 30.0, 20.0, 'ready_review', 2, 2)
            """, (clip_id, user_id))
            conn.commit()

        doc_rev1 = {
            "version": 1,
            "timeline": {
                "source_duration": 120.0,
                "segments": [{"id": "s1", "source_start": 10.0, "source_end": 30.0}],
            },
            "tracks": [],
        }

        # 1. Revision 1 (older than local rev 2) -> stale
        res1 = db.import_cloud_timeline_data(
            clip_id,
            {
                "segments_revision": 1,
                "active_state_revision": 1,
                "project_document": doc_rev1,
                "media_fingerprint": "fp_m1",
                "render_fingerprint": "fp_r1",
                "kept_duration": 20.0,
            },
            expected_user_id=user_id,
        )
        self.assertEqual(res1, "stale")

        # 2. Revision 2 (equal to local rev 2) -> stale
        res2 = db.import_cloud_timeline_data(
            clip_id,
            {
                "segments_revision": 2,
                "active_state_revision": 2,
                "project_document": doc_rev1,
                "media_fingerprint": "fp_m2",
                "render_fingerprint": "fp_r2",
                "kept_duration": 20.0,
            },
            expected_user_id=user_id,
        )
        self.assertEqual(res2, "stale")

        # 3. Revision 3 (strictly newer) -> applied
        doc_rev3 = {
            "version": 1,
            "timeline": {
                "source_duration": 120.0,
                "segments": [
                    {"id": "s1", "source_start": 10.0, "source_end": 20.0},
                    {"id": "s2", "source_start": 20.0, "source_end": 30.0},
                ],
            },
            "tracks": [
                {
                    "id": "trk_cap",
                    "kind": "captions",
                    "groups": [{"id": "g1", "start": 0.0, "end": 2.0, "text": "Hello"}],
                }
            ],
        }
        res3 = db.import_cloud_timeline_data(
            clip_id,
            {
                "segments_revision": 3,
                "active_state_revision": 3,
                "project_document": doc_rev3,
                "media_fingerprint": "fp_m3",
                "render_fingerprint": "fp_r3",
                "kept_duration": 20.0,
                "operation_id": "op_split_3",
            },
            expected_user_id=user_id,
        )
        self.assertEqual(res3, "applied")

        clip = db.get_clip_by_id(clip_id)
        self.assertEqual(clip["segments_revision"], 3)
        self.assertEqual(clip["active_state_revision"], 3)
        self.assertEqual(clip["render_fingerprint"], "fp_r3")
        self.assertIsNotNone(clip["project_document"])
        self.assertIn("Hello", clip["caption_data"])

    def test_import_cloud_timeline_split_invariance_preserves_fresh_render(self):
        """Split-only cut invariance: if rendered_fingerprint == incoming render_fingerprint, render_stale is 0."""
        clip_id = "test_clip_split_invar"
        user_id = "user_invar_123"
        matching_fp = "93811ef8b09f25f3a38d243b4a678b36d81c20e9b119f6e286b3cc574f09185f"

        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
            cursor.execute("""
                INSERT INTO clips (
                    id, title, user_id, start_time, end_time, duration, status,
                    segments_revision, active_state_revision, render_fingerprint, rendered_fingerprint, render_stale
                ) VALUES (?, 'Invar Test', ?, 10.0, 30.0, 20.0, 'ready_review', 1, 1, ?, ?, 0)
            """, (clip_id, user_id, matching_fp, matching_fp))
            conn.commit()

        # Mobile user split segment at 20.0s -> identical render_fingerprint!
        doc_split = {
            "version": 1,
            "timeline": {
                "source_duration": 120.0,
                "segments": [
                    {"id": "s1", "source_start": 10.0, "source_end": 20.0},
                    {"id": "s2", "source_start": 20.0, "source_end": 30.0},
                ],
            },
            "tracks": [],
        }

        res = db.import_cloud_timeline_data(
            clip_id,
            {
                "segments_revision": 2,
                "active_state_revision": 2,
                "project_document": doc_split,
                "media_fingerprint": "media_fp_same",
                "render_fingerprint": matching_fp,
                "kept_duration": 20.0,
            },
            expected_user_id=user_id,
        )
        self.assertEqual(res, "applied")

        clip = db.get_clip_by_id(clip_id)
        self.assertEqual(clip["segments_revision"], 2)
        # Invariant: render remains fresh (0), not stale!
        self.assertEqual(clip["render_stale"], 0)

        # But if a new revision changes text/layout so render_fingerprint differs:
        res_stale = db.import_cloud_timeline_data(
            clip_id,
            {
                "segments_revision": 3,
                "active_state_revision": 3,
                "project_document": doc_split,
                "media_fingerprint": "media_fp_same",
                "render_fingerprint": "new_different_fingerprint_xyz",
                "kept_duration": 20.0,
            },
            expected_user_id=user_id,
        )
        self.assertEqual(res_stale, "applied")
        clip_after = db.get_clip_by_id(clip_id)
        self.assertEqual(clip_after["render_stale"], 1)

    def test_record_render_completion(self):
        """record_render_completion sets rendered_fingerprint and clears render_stale."""
        clip_id = "test_clip_render_comp"
        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
            cursor.execute("""
                INSERT INTO clips (id, title, start_time, end_time, duration, status, render_stale)
                VALUES (?, 'Comp Test', 0.0, 10.0, 10.0, 'ready_review', 1)
            """, (clip_id,))
            conn.commit()

        success = db.record_render_completion(
            clip_id=clip_id,
            rendered_fingerprint="fp_render_final_123",
            video_path="/clips/test.mp4",
            thumbnail_path="/clips/test.jpg",
        )
        self.assertTrue(success)

        clip = db.get_clip_by_id(clip_id)
        self.assertEqual(clip["rendered_fingerprint"], "fp_render_final_123")
        self.assertEqual(clip["render_stale"], 0)
        self.assertEqual(clip["video_path"], "/clips/test.mp4")
        self.assertEqual(clip["thumbnail_path"], "/clips/test.jpg")

    @patch("dispatch.cloud.sync.requests.get")
    @patch("dispatch.cloud.sync.get_worker_token", return_value="test-worker-token")
    def test_sync_pc_timeline_sweep(self, mock_token, mock_get):
        """Headless PC sync sweep pulls GET /sync/pc/timeline and applies updates."""
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_data = {
            "status": "ok",
            "timelines": [
                {
                    "clip_id": "clip_sweep_1",
                    "cloud_clip_id": "cloud_clip_1",
                    "user_id": "user_sync_1",
                    "segments_revision": 5,
                    "active_state_revision": 5,
                    "media_fingerprint": "mfp",
                    "render_fingerprint": "rfp",
                    "project_document": {
                        "version": 1,
                        "timeline": {"source_duration": 60.0, "segments": [{"id": "s1", "source_start": 0.0, "source_end": 10.0}]},
                        "tracks": [],
                    },
                    "kept_duration": 10.0,
                    "render_stale": 1,
                }
            ],
        }
        mock_response.text = json.dumps(mock_data)
        mock_response.json.return_value = mock_data
        mock_get.return_value = mock_response

        with patch("dispatch.db.import_cloud_timeline_data", return_value="applied") as mock_import:
            count = sync.import_cloud_timelines_for_clips(user_ids=["user_sync_1"])
            self.assertEqual(count, 1)
            mock_import.assert_called_once()
            args, kwargs = mock_import.call_args
            self.assertEqual(args[0], "clip_sweep_1")
            self.assertEqual(kwargs.get("expected_user_id"), "user_sync_1")

    @patch("dispatch.cloud.sync.requests.post")
    @patch("dispatch.cloud.sync.get_worker_token", return_value="test-worker-token")
    def test_report_render_complete_to_cloud(self, mock_token, mock_post):
        """Headless PC reports render completion to Cloudflare Worker."""
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        with patch("dispatch.db.get_clip_by_id", return_value={"id": "clip_comp_1", "user_id": "user_1"}):
            ok = sync.report_render_complete_to_cloud(
                clip_id="clip_comp_1",
                rendered_fingerprint="fp_rendered_456",
                duration=12.5,
            )
            self.assertTrue(ok)
            mock_post.assert_called_once()
            _, kwargs = mock_post.call_args
            body = kwargs.get("json", {})
            self.assertEqual(body.get("clip_id"), "clip_comp_1")
            self.assertEqual(body.get("rendered_fingerprint"), "fp_rendered_456")
            self.assertEqual(body.get("duration"), 12.5)


if __name__ == "__main__":
    unittest.main()
