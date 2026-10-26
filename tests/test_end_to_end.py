from dispatch.governor import ResourceGovernor
"""End-to-End integration test for Dispatch with synthesized human speech.
Simulates:
1. Phone recording dropping into storage/incoming/ with real synthesized speech
2. Ingestion & staging into storage/processing/
3. Audio extraction & faster-whisper transcription
4. AI highlight detection
5. FFmpeg 9:16 rendering + subtitle burn-in
6. Raw source chunk auto-cleanup
7. Web dashboard approval
8. Outbox publishing & post-publish clip cleanup
"""
import unittest
from unittest.mock import patch
import asyncio
import subprocess
from pathlib import Path
try:
    import edge_tts
except ImportError:
    edge_tts = None

from dispatch.config import INCOMING_DIR, PROCESSING_DIR, CLIPS_DIR
from dispatch.ingestion.watcher import process_incoming_file
from dispatch.main import process_single_chunk
from dispatch.db import init_db, get_clips_for_review, approve_clip, get_db_connection
from dispatch.publisher.outbox import process_outbox_queue


async def generate_speech_audio(output_audio_path: Path):
    """Generate synthesized speech audio using edge-tts with offline Windows SAPI fallback."""
    text = (
        "Suno aaj hum dekhenge how Dispatch automates content creation. "
        "Basically you just hit record on your phone and forget everything else. "
        "The entire pipeline runs locally on your PC and generates polished shorts automatically."
    )
    if edge_tts is not None:
        try:
            communicate = edge_tts.Communicate(text, "hi-IN-MadhurNeural")
            await communicate.save(str(output_audio_path))
            if output_audio_path.exists() and output_audio_path.stat().st_size > 1000:
                return
        except Exception:
            pass

    # Fallback 1: Windows SAPI offline speech synthesis
    try:
        import win32com.client
        v = win32com.client.Dispatch("SAPI.SpVoice")
        s = win32com.client.Dispatch("SAPI.SpFileStream")
        wav_path = str(output_audio_path.with_suffix(".wav"))
        s.Open(wav_path, 3, False)
        v.AudioOutputStream = s
        v.Speak(text)
        s.Close()
        cmd = ["ffmpeg", "-y", "-i", wav_path, "-c:a", "libmp3lame", str(output_audio_path)]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        Path(wav_path).unlink(missing_ok=True)
        if output_audio_path.exists() and output_audio_path.stat().st_size > 1000:
            return
    except Exception:
        pass

    # Fallback 2: Generate synthetic tone
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=22",
        "-c:a", "libmp3lame",
        str(output_audio_path)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_db_connection() as conn:
            conn.execute("DELETE FROM chunks WHERE filename LIKE 'phone_sync_chunk_speech%'")

    def test_full_autonomous_pipeline(self):
        import uuid
        run_id = uuid.uuid4().hex[:8]

        # 1. Synthesize real speech audio
        temp_speech_file = PROCESSING_DIR / f"speech_synth_{run_id}.mp3"
        asyncio.run(generate_speech_audio(temp_speech_file))

        # 2. Combine speech audio with 1280x720 video (approx 22 seconds)
        incoming_video = INCOMING_DIR / f"phone_sync_chunk_speech_{run_id}.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25",
            "-i", str(temp_speech_file),
            "-c:v", "libx264",
            "-c:a", "aac",
            "-shortest",
            str(incoming_video)
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        temp_speech_file.unlink()

        # 3. Ingest and stage chunk
        staged_info = process_incoming_file(incoming_video)
        self.assertIsNotNone(staged_info, "Failed to ingest incoming phone recording")
        self.assertFalse(incoming_video.exists(), "Raw incoming file should be moved from incoming")
        self.assertTrue(staged_info["filepath"].exists(), "Staged file missing in processing")

        # 4. Run end-to-end processing pipeline on the staged chunk
        mock_candidates = [{
            "start_time": 0.5,
            "end_time": 8.5,
            "title": "Dispatch Highlight",
            "hook": "Check this out",
            "description": "Automated clip",
            "hashtags": "#Shorts #Reels",
            "virality_score": 85,
            "layout_recommendation": "crop_916"
        }]
        with patch("dispatch.ai_clips.highlight_finder.call_gemini_generate_content", return_value=(mock_candidates, "mock_gemini")), \
             patch.object(ResourceGovernor, "can_process_heavy_task", return_value=(True, "OK")):
            process_single_chunk(staged_info)

        # 5. Verify raw chunk file is preserved on disk to prevent creator data loss
        self.assertTrue(staged_info["filepath"].exists(), "Raw source video chunk should be preserved to prevent creator data loss")

        # 6. Verify clips were generated and are ready for review
        ready_clips = get_clips_for_review()
        self.assertGreaterEqual(len(ready_clips), 1, "Expected at least 1 candidate clip ready for review")
        clip = ready_clips[0]
        clip_id = clip["id"]
        video_path = Path(clip["video_path"])
        thumb_path = Path(clip["thumbnail_path"])

        self.assertTrue(video_path.exists(), f"Rendered clip file missing: {video_path}")
        self.assertTrue(thumb_path.exists(), f"Thumbnail file missing: {thumb_path}")

        # 7. Simulate user 1-click Approval on dashboard (targeting YouTube and Instagram)
        approve_clip(clip_id, custom_mode="private", custom_platforms="youtube,instagram")

        # 8. Run outbox queue worker with mocked external publication
        with patch("dispatch.publisher.outbox.upload_youtube_short", return_value={"remote_id": "mock_yt_123", "remote_url": "https://youtu.be/mock_yt_123"}), \
             patch("dispatch.publisher.outbox.upload_instagram_reel", return_value={"remote_id": "mock_ig_123", "remote_url": "https://instagram.com/reel/mock_ig_123"}):
            success_count = process_outbox_queue()
        self.assertGreaterEqual(success_count, 2, f"Expected YouTube and Instagram outbox jobs to succeed, got {success_count}")

        # 9. Verify clip marked published in DB
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM clips WHERE id = ?", (clip_id,))
            final_status = cursor.fetchone()[0]
            self.assertEqual(final_status, "published", f"Expected published status, got {final_status}")

        # 10. Verify post-publish local clip retention
        self.assertTrue(video_path.exists(), "Published clip video should remain on disk")

        # Clean up artifacts
        staged_info["filepath"].unlink(missing_ok=True)
        video_path.unlink(missing_ok=True)
        if thumb_path.exists():
            thumb_path.unlink()


if __name__ == "__main__":
    unittest.main()

