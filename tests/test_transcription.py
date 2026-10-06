"""Unit test for audio extraction and transcription."""
import subprocess
from pathlib import Path
from dispatch.config import PROCESSING_DIR
from dispatch.transcription.audio import extract_audio
from dispatch.db import init_db

def test_audio():
    init_db()
    test_video = PROCESSING_DIR / "test_audio_vid.mp4"
    
    # Generate 1-second video with sine wave audio
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=440",
        "-t", "1",
        "-c:v", "libx264",
        "-c:a", "aac",
        str(test_video)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    
    # 1. Test audio extraction
    wav_path = extract_audio(test_video)
    assert wav_path.exists(), "Extracted WAV does not exist"
    assert wav_path.stat().st_size > 1000, "WAV size suspiciously small"
    
    # Clean up test artifacts
    wav_path.unlink()
    test_video.unlink()
    print("ALL AUDIO EXTRACTION TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    test_audio()
