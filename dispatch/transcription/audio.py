"""Audio extraction module using FFmpeg."""
import subprocess
import math
from pathlib import Path
from typing import List, Optional, Tuple

from dispatch.transcription.subprocess_utils import run_checked


def _probe_audio_duration(audio_path: Path) -> float:
    probe = run_checked(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path),
        ],
        timeout_seconds=60,
        label="ffprobe audio duration probe",
    )
    if probe.returncode != 0:
        raise RuntimeError(f"Could not inspect transcription audio: {probe.stderr.strip() if isinstance(probe.stderr, str) else ''}")
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

    result = run_checked(
        cmd,
        timeout_seconds=600,
        label="ffmpeg WAV audio extraction",
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg audio extraction failed: {result.stderr.strip() if isinstance(result.stderr, str) else ''}")

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

    result = run_checked(
        cmd,
        timeout_seconds=600,
        label="ffmpeg MP3 audio extraction",
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg MP3 audio extraction failed: {result.stderr.strip() if isinstance(result.stderr, str) else ''}")

    if not output_mp3_path.exists() or output_mp3_path.stat().st_size == 0:
        raise RuntimeError("Extracted MP3 file is missing or empty")

    return output_mp3_path


def split_audio_for_transcription(
    audio_path: Path,
    output_dir: Path,
    chunk_seconds: int = 600,
    overlap_seconds: float = 2.0,
) -> List[Tuple[Path, float]]:
    """Create bounded, overlapping audio windows with exact source-time offsets."""
    if chunk_seconds <= 0:
        raise ValueError("chunk_seconds must be positive")
    if overlap_seconds < 0:
        raise ValueError("overlap_seconds cannot be negative")
    if not audio_path.is_file() or audio_path.stat().st_size == 0:
        raise FileNotFoundError(f"Audio file is missing or empty: {audio_path}")

    duration = _probe_audio_duration(audio_path)
    if duration <= chunk_seconds:
        return [(audio_path, 0.0)]

    # Small test/custom windows still need overlap without making the step invalid.
    overlap = min(float(overlap_seconds), chunk_seconds * 0.1)
    step = chunk_seconds - overlap
    if step <= 0:
        raise ValueError("overlap_seconds must be smaller than chunk_seconds")

    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.glob("audio_part_*.mp3")):
        raise FileExistsError(f"Transcription part directory is not empty: {output_dir}")
    if any(output_dir.glob("audio_part_*.mp3")):
        raise FileExistsError(f"Transcription part directory is not empty: {output_dir}")
    parts: List[Tuple[Path, float]] = []
    source_offset = 0.0
    part_index = 0
    while source_offset < duration:
        window_duration = min(float(chunk_seconds), duration - source_offset)
        part_path = output_dir / f"audio_part_{part_index:03d}.mp3"
        split = run_checked(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(audio_path), "-ss", f"{source_offset:.3f}",
                "-t", f"{window_duration:.3f}", "-map", "0:a:0", "-vn",
                "-c:a", "libmp3lame", "-b:a", "96k", "-ar", "44100",
                str(part_path),
            ],
            timeout_seconds=600,
            label=f"ffmpeg transcription window split {part_index + 1}",
        )
        if split.returncode != 0:
            raise RuntimeError(f"Could not extract transcription window {part_index + 1}: {split.stderr.strip() if isinstance(split.stderr, str) else ''}")
        if not part_path.is_file() or part_path.stat().st_size == 0:
            raise RuntimeError(f"Transcription window {part_index + 1} is missing or empty")
        part_duration = _probe_audio_duration(part_path)
        if part_duration <= 0 or part_duration > chunk_seconds + 0.25:
            raise RuntimeError(f"Transcription window {part_index + 1} has invalid duration {part_duration:.3f}s")
        parts.append((part_path, source_offset))
        source_offset = round(source_offset + step, 3)
        part_index += 1

    if len(parts) < 2 or parts[-1][1] + _probe_audio_duration(parts[-1][0]) < duration - 0.25:
        raise RuntimeError("Long audio split did not cover the complete source timeline")
    return parts

