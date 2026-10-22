"""Source-range trim bounds and render invalidation regressions."""

import uuid
import unittest

from dispatch import db


class TestClipTrimContract(unittest.TestCase):
    def test_range_can_extend_past_gemini_suggestion_within_source_and_marks_render_stale(self):
        suffix = uuid.uuid4().hex
        chunk_id = f"trim-chunk-{suffix}"
        clip_id = f"trim-clip-{suffix}"
        with db.get_db_connection() as conn:
            conn.execute(
                "INSERT INTO chunks (id, filename, filepath, duration) VALUES (?, ?, ?, ?)",
                (chunk_id, "trim-source.mp4", "trim-source.mp4", 600.0),
            )
            conn.execute(
                """INSERT INTO clips (
                    id, chunk_id, start_time, end_time, duration, title,
                    padded_start, padded_end, video_path, rendered_layout_mode,
                    ai_suggested_start_time, ai_suggested_end_time, caption_data
                ) VALUES (?, ?, 120, 150, 30, 'trim test', 116, 154, 'render.mp4', 'fit_blur', 120, 150, '{"groups":[]}')""",
                (clip_id, chunk_id),
            )
            conn.commit()

        try:
            db.init_db()
            db.init_db()  # migration is idempotent and preserves the legacy record
            migrated = db.get_clip_by_id(clip_id)
            self.assertEqual(migrated["caption_data"], '{"groups":[]}')
            self.assertEqual(migrated["video_path"], "render.mp4")
            self.assertEqual(migrated["caption_range_start"], 120.0)
            self.assertEqual(migrated["caption_range_end"], 150.0)
            self.assertTrue(db.update_clip_trim(clip_id, 20.0, 190.0))
            updated = db.get_clip_by_id(clip_id)
            self.assertEqual(updated["start_time"], 20.0)
            self.assertEqual(updated["end_time"], 190.0)
            self.assertEqual(updated["duration"], 170.0)
            self.assertEqual(updated["render_stale"], 1)
            self.assertEqual(updated["ai_suggested_start_time"], 120.0)
            self.assertEqual(updated["ai_suggested_end_time"], 150.0)
            self.assertEqual(updated["range_revision"], 1)
            self.assertFalse(db.update_clip_trim(clip_id, 30.0, 200.0, expected_range_revision=0))
            self.assertTrue(db.update_clip_trim(clip_id, 20.0, 190.0, expected_range_revision=1))
            self.assertEqual(db.get_clip_by_id(clip_id)["range_revision"], 1, "an unchanged range does not create a new revision")

            self.assertFalse(db.save_clip_caption_data(clip_id, '{"groups":[]}', expected_range_revision=0, source_range_start=20, source_range_end=190))
            self.assertTrue(db.save_clip_caption_data(clip_id, '{"groups":[]}', expected_range_revision=1, source_range_start=20, source_range_end=190))
            saved = db.get_clip_by_id(clip_id)
            self.assertEqual(saved["caption_range_revision"], 1)
            self.assertEqual(saved["caption_range_start"], 20.0)
            self.assertEqual(saved["caption_range_end"], 190.0)

            self.assertFalse(db.update_clip_media(
                clip_id, "obsolete-render.mp4", None, "fit_blur", expected_range_revision=0
            ))
            self.assertEqual(db.get_clip_by_id(clip_id)["video_path"], "render.mp4")
            self.assertEqual(db.get_clip_by_id(clip_id)["render_stale"], 1)

            db.update_clip_media(clip_id, "fresh-render.mp4", None, "fit_blur")
            self.assertEqual(db.get_clip_by_id(clip_id)["render_stale"], 0)

            self.assertFalse(db.update_clip_trim(clip_id, -1, 100))
            self.assertFalse(db.update_clip_trim(clip_id, 20, 201))
            self.assertFalse(db.update_clip_trim(clip_id, 20, 205))
            self.assertFalse(db.update_clip_trim(clip_id, 20, 200.1))
            self.assertFalse(db.update_clip_trim(clip_id, 20, 24))
            self.assertFalse(db.update_clip_trim(clip_id, float("nan"), 200))
        finally:
            with db.get_db_connection() as conn:
                conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
                conn.execute("DELETE FROM chunks WHERE id = ?", (chunk_id,))
                conn.commit()


if __name__ == "__main__":
    unittest.main()
