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

    return output_wav_path


def extract_compressed_audio(
    video_path: Path,
    output_mp3_path: Optional[Path] = None,
    bitrate: str = "96k"
) -> Path:
    """Extract lightweight compressed MP3 audio from video file for cloud API transcription.
    
    Achieves 95-98% size reduction compared to video file.
    
    Args:
        video_path: Path to the input video.
        output_mp3_path: Optional destination path for .mp3 file.
        bitrate: Audio bitrate (default 96k, ideal for speech recognition).
        
    Returns:
        Path to the extracted .mp3 file.
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    if output_mp3_path is None:
        output_mp3_path = video_path.with_suffix(".mp3")

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "libmp3lame",
        "-b:a", bitrate,
        "-ar", "44100",
        str(output_mp3_path)
    ]

    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg MP3 audio extraction failed: {result.stderr.strip()}")

    if not output_mp3_path.exists() or output_mp3_path.stat().st_size == 0:
        raise RuntimeError("Extracted MP3 file is missing or empty")

    return output_mp3_path

