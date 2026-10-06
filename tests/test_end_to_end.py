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
import asyncio
import subprocess
from pathlib import Path
import edge_tts

from dispatch.config import INCOMING_DIR, PROCESSING_DIR, CLIPS_DIR
from dispatch.ingestion.watcher import process_incoming_file
from dispatch.main import process_single_chunk
from dispatch.db import init_db, get_clips_for_review, approve_clip, get_db_connection
from dispatch.publisher.outbox import process_outbox_queue


async def generate_speech_audio(output_audio_path: Path):
    """Generate synthesized speech audio using edge-tts."""
    text = (
        "Suno aaj hum dekhenge how Dispatch automates content creation. "
        "Basically you just hit record on your phone and forget everything else. "
        "The entire pipeline runs locally on your PC and generates polished shorts automatically."
    )
    communicate = edge_tts.Communicate(text, "hi-IN-MadhurNeural")
    await communicate.save(str(output_audio_path))


def test_full_autonomous_pipeline():
    init_db()

    # 1. Synthesize real speech audio
    temp_speech_file = PROCESSING_DIR / "speech_synth.mp3"
    asyncio.run(generate_speech_audio(temp_speech_file))

    # 2. Combine speech audio with 1280x720 video (approx 22 seconds)
    incoming_video = INCOMING_DIR / "phone_sync_chunk_speech.mp4"
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
    assert staged_info is not None, "Failed to ingest incoming phone recording"
    assert not incoming_video.exists(), "Raw incoming file should be moved from incoming"
    assert staged_info["filepath"].exists(), "Staged file missing in processing"

    # 4. Run end-to-end processing pipeline on the staged chunk
    process_single_chunk(staged_info)

    # 5. Verify raw chunk file was automatically deleted by the cleanup policy
    assert not staged_info["filepath"].exists(), "Raw source chunk should have been purged after rendering"

    # 6. Verify clips were generated and are ready for review
    ready_clips = get_clips_for_review()
    assert len(ready_clips) >= 1, "Expected at least 1 candidate clip ready for review"
    clip = ready_clips[0]
    clip_id = clip["id"]
    video_path = Path(clip["video_path"])
    thumb_path = Path(clip["thumbnail_path"])

    assert video_path.exists(), f"Rendered clip file missing: {video_path}"
    assert thumb_path.exists(), f"Thumbnail file missing: {thumb_path}"

    # 7. Simulate user 1-click Approval on dashboard
    approve_clip(clip_id, custom_mode="private")

    # 8. Run outbox queue worker
    success_count = process_outbox_queue()
    assert success_count >= 2, f"Expected YouTube and Instagram outbox jobs to succeed, got {success_count}"

    # 9. Verify clip marked published in DB
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM clips WHERE id = ?", (clip_id,))
        final_status = cursor.fetchone()[0]
        assert final_status == "published", f"Expected published status, got {final_status}"

    # 10. Verify post-publish local clip cleanup
    assert not video_path.exists(), "Published clip video should be automatically purged from local disk"

    # Clean up thumbnail artifact
    if thumb_path.exists():
        thumb_path.unlink()

    print("=" * 60)
    print("FULL DISPATCH AUTONOMOUS PIPELINE PASSED 100% SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    test_full_autonomous_pipeline()
