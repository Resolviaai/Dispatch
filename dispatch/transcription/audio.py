"""Audio extraction module using FFmpeg."""
import subprocess
import math
from pathlib import Path
from typing import List, Optional, Tuple


def _probe_audio_duration(audio_path: Path) -> float:
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        raise RuntimeError(f"Could not inspect transcription audio: {probe.stderr.strip()}")
    try:
        duration = float(probe.stdout.strip())
    except ValueError as exc:
        raise RuntimeError("Could not read transcription audio duration") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError("Transcription audio has no positive finite duration")
    return duration


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


def split_audio_for_transcription(
    audio_path: Path,
    output_dir: Path,
    chunk_seconds: int = 600,
) -> List[Tuple[Path, float]]:
    """Split long audio for bounded API requests; return each part with source offset."""
    if chunk_seconds <= 0:
        raise ValueError("chunk_seconds must be positive")
    if not audio_path.is_file() or audio_path.stat().st_size == 0:
        raise FileNotFoundError(f"Audio file is missing or empty: {audio_path}")

    duration = _probe_audio_duration(audio_path)
    if duration <= chunk_seconds:
        return [(audio_path, 0.0)]

    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.glob("audio_part_*.mp3")):
        raise FileExistsError(f"Transcription part directory is not empty: {output_dir}")
    output_pattern = output_dir / "audio_part_%03d.mp3"
    split = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(audio_path), "-map", "0:a:0", "-c:a", "copy",
            "-f", "segment", "-segment_time", str(chunk_seconds),
            "-reset_timestamps", "1", str(output_pattern),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if split.returncode != 0:
        raise RuntimeError(f"Could not split long transcription audio: {split.stderr.strip()}")

    part_paths = sorted(output_dir.glob("audio_part_*.mp3"))
    if len(part_paths) < 2 or any(not path.is_file() or path.stat().st_size == 0 for path in part_paths):
        raise RuntimeError("Long audio split did not produce complete sequential parts")

    parts: List[Tuple[Path, float]] = []
    source_offset = 0.0
    for part_path in part_paths:
        parts.append((part_path, source_offset))
        source_offset += _probe_audio_duration(part_path)
    if abs(source_offset - duration) > max(1.0, len(part_paths) * 0.05):
        raise RuntimeError(
            f"Long audio split duration mismatch: source={duration:.3f}s parts={source_offset:.3f}s"
        )
    return parts

