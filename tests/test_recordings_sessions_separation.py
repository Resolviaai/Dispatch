import unittest
import tempfile
import os
from pathlib import Path
from fastapi.testclient import TestClient

from dispatch.db import init_db, create_session, register_chunk, save_clip, update_clip_media, get_db_connection
from dispatch.web.app import app

class TestRecordingsSessionsSeparation(unittest.TestCase):
    def setUp(self):
        init_db()
        self.client = TestClient(app)

    def test_recordings_query_never_duplicates_or_leaks_clip_videos(self):
        # 1. Create a session with 2 chunks
        session_id = create_session(notes="Test Separation Session")
        
        # Create dummy source files
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f1:
            f1.write(b"raw source chunk 1 content" * 100)
            chunk1_path = f1.name
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f2:
            f2.write(b"raw source chunk 2 content" * 100)
            chunk2_path = f2.name

        try:
            chunk1_id = register_chunk(session_id, "chunk_1.mp4", chunk1_path, "hash_chunk_1")
            chunk2_id = register_chunk(session_id, "chunk_2.mp4", chunk2_path, "hash_chunk_2")

            # 2. Add 3 generated clips to chunk 1, with fake rendered video paths
            clip1 = save_clip(
                session_id=session_id,
                chunk_id=chunk1_id,
                start_time=0.0,
                end_time=10.0,
                title="Clip 1 Subtitled",
                hook="Hook 1",
                description="Desc 1",
                hashtags="#one",
                virality_score=90,
                layout_mode="crop_follow",
            )
            update_clip_media(clip1, "/path/to/rendered_clip_1_with_subs.mp4", None)

            clip2 = save_clip(
                session_id=session_id,
                chunk_id=chunk1_id,
                start_time=10.0,
                end_time=20.0,
                title="Clip 2 Subtitled",
                hook="Hook 2",
                description="Desc 2",
                hashtags="#two",
                virality_score=85,
                layout_mode="crop_follow",
            )
            update_clip_media(clip2, "/path/to/rendered_clip_2_with_subs.mp4", None)

            clip3 = save_clip(
                session_id=session_id,
                chunk_id=chunk1_id,
                start_time=20.0,
                end_time=30.0,
                title="Clip 3 Subtitled",
                hook="Hook 3",
                description="Desc 3",
                hashtags="#three",
                virality_score=80,
                layout_mode="crop_follow",
            )
            update_clip_media(clip3, "/path/to/rendered_clip_3_with_subs.mp4", None)

            # Mark clip1 as approved
            with get_db_connection() as conn:
                conn.execute("UPDATE clips SET status = 'approved' WHERE id = ?", (clip1,))

            # 3. Call GET /api/recordings
            res = self.client.get("/api/recordings")
            self.assertEqual(res.status_code, 200)
            recordings = res.json()

            # Filter to our test session's chunks
            session_recs = [r for r in recordings if r["sessionId"] == session_id]

            # Invariant 1: Exactly 2 chunks returned, NO row duplication from the 3 clips!
            self.assertEqual(len(session_recs), 2, f"Expected 2 segments for session, got {len(session_recs)}")

            chunk1_rec = next(r for r in session_recs if r["segmentId"] == chunk1_id)
            chunk2_rec = next(r for r in session_recs if r["segmentId"] == chunk2_id)

            # Invariant 2: videoUrl points ONLY to raw source endpoint, NEVER /clips/...
            self.assertEqual(chunk1_rec["videoUrl"], f"/api/recordings/{chunk1_id}/source")
            self.assertNotIn("rendered_clip", chunk1_rec["videoUrl"])
            self.assertNotIn("/clips/", chunk1_rec["videoUrl"])

            # Invariant 3: clipCount and approvedClipCount are accurate
            self.assertEqual(chunk1_rec["clipCount"], 3)
            self.assertEqual(chunk1_rec["approvedClipCount"], 1)
            self.assertEqual(chunk2_rec["clipCount"], 0)
            self.assertEqual(chunk2_rec["approvedClipCount"], 0)

            # Invariant 4: Source video endpoint works and serves raw video
            source_res = self.client.get(f"/api/recordings/{chunk1_id}/source")
            self.assertIn(source_res.status_code, [200, 206])
            self.assertIn(b"raw source chunk 1 content", source_res.content)

        finally:
            if os.path.exists(chunk1_path):
                os.remove(chunk1_path)
            if os.path.exists(chunk2_path):
                os.remove(chunk2_path)

if __name__ == "__main__":
    unittest.main()
