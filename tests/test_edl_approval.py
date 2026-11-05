import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

from dispatch import db
from dispatch.web.app import app

client = TestClient(app)


class TestEDLApprovalContract(unittest.TestCase):
    """
    Acceptance test fixture for T02:
    Validates that approving a clip with multiple EDL segments (where effective duration
    differs from end_time - start_time) validates actual rendered duration against
    eff_duration (render_res['expected_duration']) rather than raw end_time - start_time.
    """

    def setUp(self):
        db.init_db()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"test source")
        self.clip_id = db.save_clip(
            session_id=None,
            chunk_id=None,
            start_time=0.0,
            end_time=30.0,
            title="Multi-segment EDL approval test",
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

    def test_multi_segment_cut_approval_uses_expected_duration(self):
        # Clip outer range is 0.0 -> 30.0 (30s)
        # But project_document has 2 segments: [0.0, 10.0] and [20.0, 30.0] -> eff_duration = 20.0s
        output_file = self.root / "final_cut.mp4"

        def mock_render(**kwargs):
            output_file.write_bytes(b"rendered media")
            db.update_clip_media(kwargs["clip_id"], str(output_file), None, kwargs["layout_mode"])
            return {
                "video_path": str(output_file),
                "expected_duration": 20.0,  # Returned from renderer eff_duration
            }

        def mock_probe(_path):
            # Probe finds actual rendered duration is 20.0s
            return True, {"duration": 20.0}, None

        with patch("dispatch.web.app.resolve_clip_source_path", return_value=self.source), \
             patch("dispatch.web.app.render_clip", side_effect=mock_render), \
             patch("dispatch.ingestion.validator.probe_video", side_effect=mock_probe):
            response = client.post(
                f"/api/clips/{self.clip_id}/approve",
                json={"platforms": "youtube"},
            )

        self.assertEqual(response.status_code, 200, f"Approval should succeed with 200, got: {response.text}")
        clip = db.get_clip_by_id(self.clip_id)
        self.assertEqual(clip["status"], "approved")

    def test_multi_segment_duration_mismatch_raises_500(self):
        # If actual duration is 15.0s but expected is 20.0s (difference > 1.0s), must fail
        output_file = self.root / "final_bad.mp4"

        def mock_render(**kwargs):
            output_file.write_bytes(b"rendered media")
            db.update_clip_media(kwargs["clip_id"], str(output_file), None, kwargs["layout_mode"])
            return {
                "video_path": str(output_file),
                "expected_duration": 20.0,
            }

        def mock_probe(_path):
            # Broken render resulted in 15.0s instead of 20.0s
            return True, {"duration": 15.0}, None

        with patch("dispatch.web.app.resolve_clip_source_path", return_value=self.source), \
             patch("dispatch.web.app.render_clip", side_effect=mock_render), \
             patch("dispatch.ingestion.validator.probe_video", side_effect=mock_probe):
            response = client.post(
                f"/api/clips/{self.clip_id}/approve",
                json={"platforms": "youtube"},
            )

        self.assertEqual(response.status_code, 500)
        self.assertIn("does not match selected range", response.text)


if __name__ == "__main__":
    unittest.main()
