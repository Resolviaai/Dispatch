"""Audio extraction module using FFmpeg."""
import subprocess
from pathlib import Path
from typing import Optional


def extract_audio(video_path: Path, output_wav_path: Optional[Path] = None) -> Path:
    """Extract 16kHz mono WAV audio from video file for speech processing.
    
    Args:
        video_path: Path to the input video.
        output_wav_path: Optional destination path for .wav file.
        
    Returns:
        Path to the extracted .wav file.
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    if output_wav_path is None:
        output_wav_path = video_path.with_suffix(".wav")

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(output_wav_path)
    ]

    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg audio extraction failed: {result.stderr.strip()}")

    if not output_wav_path.exists() or output_wav_path.stat().st_size == 0:
        raise RuntimeError("Extracted audio file is missing or empty")

    return output_wav_path
