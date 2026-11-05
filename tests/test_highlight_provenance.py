import unittest
import sqlite3
import os
import tempfile
from unittest.mock import patch

from dispatch import db


class TestHighlightProvenance(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_dispatch.db")
        self.patcher = patch("dispatch.db.DB_PATH", self.db_path)
        self.patcher.start()
        db.init_db()

    def tearDown(self):
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_schema_has_provenance_columns_and_feedback_table(self):
        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(clips);")
            cols = {row["name"] for row in cursor.fetchall()}
            self.assertIn("extraction_source", cols)
            self.assertIn("extraction_model", cols)

            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='clip_feedback_events';")
            self.assertIsNotNone(cursor.fetchone())

    def test_save_clip_stores_provenance(self):
        clip_id = db.save_clip(
            session_id=None,
            chunk_id=None,
            start_time=10.0,
            end_time=35.0,
            title="Test Clip Provenance",
            hook="Hook text",
            description="Description text",
            hashtags="#shorts",
            virality_score=85,
            layout_mode="fit_blur",
            extraction_source="gemini",
            extraction_model="gemini-3.5-flash-lite",
            user_id="user_123"
        )
        clip = db.get_clip_by_id(clip_id)
        self.assertIsNotNone(clip)
        self.assertEqual(clip.get("extraction_source"), "gemini")
        self.assertEqual(clip.get("extraction_model"), "gemini-3.5-flash-lite")

    def test_save_clip_definitions_stores_provenance(self):
        defs = [{
            "start_time": 0.0,
            "end_time": 25.0,
            "title": "Def Clip",
            "hook": "Hook",
            "description": "Desc",
            "hashtags": "#test",
            "virality_score": 90,
            "layout_mode": "crop_follow"
        }]
        ids = db.save_clip_definitions(
            chunk_id=None,
            session_id=None,
            video_id="vid_test_1",
            clip_defs=defs,
            user_id="user_123",
            extraction_source="heuristic_fallback",
            extraction_model="heuristic_fallback"
        )
        self.assertEqual(len(ids), 1)
        clip = db.get_clip_by_id(ids[0])
        self.assertEqual(clip.get("extraction_source"), "heuristic_fallback")
        self.assertEqual(clip.get("extraction_model"), "heuristic_fallback")

    def test_feedback_events_recording_and_stats(self):
        clip_id = "clip_fb_test_1"
        fid1 = db.record_clip_feedback(
            clip_id=clip_id,
            event_type="approve",
            extraction_source="gemini",
            extraction_model="gemini-flash-latest",
            user_id="user_123"
        )
        self.assertTrue(fid1)

        fid2 = db.record_clip_feedback(
            clip_id=clip_id,
            event_type="reject",
            details={"reason": "Pacing too slow"},
            extraction_source="gemini",
            extraction_model="gemini-flash-latest",
            user_id="user_123"
        )
        self.assertTrue(fid2)

        stats = db.get_clip_feedback_stats(user_id="user_123")
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["by_event"].get("approve"), 1)
        self.assertEqual(stats["by_event"].get("reject"), 1)
        self.assertEqual(stats["by_source"].get("gemini"), 2)


if __name__ == "__main__":
    unittest.main()
