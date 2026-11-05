"""Audio Waveform Extraction, RMS Energy Profiling, and Boundary Snapping.

Ensures intelligent auto-cuts never amputate trailing word phonemes, trailing consonants,
or speech decay based purely on lexical timestamps. Snaps cut boundaries to zero-crossings
and local energy minima with mandatory acoustic padding.
"""

import math
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import List, Tuple, Optional


def extract_pcm_samples(media_path: Path, sample_rate: int = 16000) -> Tuple[bytes, int, int]:
    """Extract raw 16-bit mono PCM samples at specified sample_rate using FFmpeg."""
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(media_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", str(sample_rate),
        "-ac", "1",
        "-f", "s16le",
        "pipe:1"
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    raw_bytes = proc.stdout
    num_samples = len(raw_bytes) // 2
    return raw_bytes, sample_rate, num_samples


def compute_rms_profile(
    raw_bytes: bytes,
    sample_rate: int = 16000,
    frame_duration_ms: float = 20.0
) -> List[Tuple[float, float]]:
    """Compute RMS energy profile (time_sec, db_level) at frame_duration_ms intervals."""
    samples_per_frame = int(sample_rate * (frame_duration_ms / 1000.0))
    if samples_per_frame <= 0:
        samples_per_frame = 320
    
    total_samples = len(raw_bytes) // 2
    profile: List[Tuple[float, float]] = []
    
    for offset in range(0, total_samples, samples_per_frame):
        chunk_count = min(samples_per_frame, total_samples - offset)
        chunk_bytes = raw_bytes[offset * 2 : (offset + chunk_count) * 2]
        if not chunk_bytes:
            break
        
        # Unpack 16-bit signed shorts
        fmt = f"<{chunk_count}h"
        samples = struct.unpack(fmt, chunk_bytes)
        
        # Calculate RMS
        sum_sq = sum(s * s for s in samples)
        mean_sq = sum_sq / chunk_count
        rms = math.sqrt(mean_sq)
        
        # Convert to dB relative to full-scale (32768)
        if rms > 0.0001:
            db = 20.0 * math.log10(rms / 32768.0)
        else:
            db = -96.0
            
        t_sec = offset / float(sample_rate)
        profile.append((round(t_sec, 3), round(db, 2)))
        
    return profile


def find_zero_crossing(
    raw_bytes: bytes,
    target_time: float,
    search_window_ms: float = 25.0,
    sample_rate: int = 16000
) -> float:
    """Find the nearest zero-crossing around target_time to eliminate audio click/pop."""
    target_sample = int(target_time * sample_rate)
    window_samples = int((search_window_ms / 1000.0) * sample_rate)
    total_samples = len(raw_bytes) // 2
    
    start_sample = max(0, target_sample - window_samples)
    end_sample = min(total_samples - 2, target_sample + window_samples)
    
    if end_sample <= start_sample:
        return target_time

    best_sample = target_sample
    best_dist = float("inf")
    
    # Read chunk around target
    count = end_sample - start_sample + 2
    chunk_bytes = raw_bytes[start_sample * 2 : (start_sample + count) * 2]
    fmt = f"<{count}h"
    samples = struct.unpack(fmt, chunk_bytes)
    
    for i in range(len(samples) - 1):
        s1 = samples[i]
        s2 = samples[i + 1]
        # Zero-crossing: signs differ or one is zero
        if (s1 <= 0 and s2 >= 0) or (s1 >= 0 and s2 <= 0):
            current_idx = start_sample + i
            dist = abs(current_idx - target_sample)
            if dist < best_dist:
                best_dist = dist
                best_sample = current_idx
                
    return round(best_sample / float(sample_rate), 4)


def snap_boundary_to_waveform_silence(
    raw_bytes: bytes,
    raw_time: float,
    mode: str,  # 'speech_release' (start of cut) or 'speech_attack' (end of cut)
    silence_threshold_db: float = -32.0,
    min_speech_padding_ms: float = 150.0,
    sample_rate: int = 16000
) -> float:
    """Snap a cut boundary so that word endings and attack transients are never amputated.
    
    Invariants:
      - 'speech_release' (start of a cut interval):
        Transcript marks word ending at raw_time. Audio acoustic decay often persists
        for 80-150ms. We verify energy decay falls <= silence_threshold_db and enforce
        at least min_speech_padding_ms (default 150ms) padding before the cut starts.
      - 'speech_attack' (end of a cut interval):
        Transcript marks word starting at raw_time. We back up by at least 100ms
        to preserve speech intake/breath/plosives, ensuring the cut ends before the attack.
    """
    total_samples = len(raw_bytes) // 2
    total_duration = total_samples / float(sample_rate)
    
    if mode == "speech_release":
        # Speech ended, cut begins. Ensure we give at least 150ms release padding
        padding_sec = max(0.150, min_speech_padding_ms / 1000.0)
        candidate_time = min(total_duration, raw_time + padding_sec)
        
        # Check energy profile up to +350ms to ensure trailing sibilants/fricatives are done
        search_samples = int(0.350 * sample_rate)
        start_samp = int(raw_time * sample_rate)
        end_samp = min(total_samples, start_samp + search_samples)
        
        if end_samp > start_samp + 320:
            count = end_samp - start_samp
            chunk_bytes = raw_bytes[start_samp * 2 : (start_samp + count) * 2]
            fmt = f"<{count}h"
            samples = struct.unpack(fmt, chunk_bytes)
            
            # Step in 20ms frames (320 samples)
            for f_idx in range(0, count - 320, 320):
                frame = samples[f_idx : f_idx + 320]
                rms = math.sqrt(sum(s * s for s in frame) / 320.0)
                db = 20.0 * math.log10(rms / 32768.0) if rms > 0.0001 else -96.0
                frame_time = raw_time + (f_idx / float(sample_rate))
                
                # If energy drops below threshold, this is a clean release boundary
                if db <= silence_threshold_db and frame_time >= candidate_time:
                    candidate_time = frame_time
                    break
                    
        # Snap candidate_time to zero crossing
        return find_zero_crossing(raw_bytes, candidate_time, search_window_ms=25.0, sample_rate=sample_rate)
        
    elif mode == "speech_attack":
        # Speech starts, cut ends. Ensure at least 100ms attack padding
        padding_sec = 0.100
        candidate_time = max(0.0, raw_time - padding_sec)
        return find_zero_crossing(raw_bytes, candidate_time, search_window_ms=25.0, sample_rate=sample_rate)
        
    else:
        return find_zero_crossing(raw_bytes, raw_time, search_window_ms=25.0, sample_rate=sample_rate)
