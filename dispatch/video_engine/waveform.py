"""Audio Waveform Extraction, RMS Energy Profiling, and Boundary Snapping.

Ensures intelligent auto-cuts never amputate trailing word phonemes, trailing consonants,
or speech decay based purely on lexical timestamps. Snaps cut boundaries to zero-crossings
and local energy minima with mandatory acoustic padding.
"""

import array
import math
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple, Optional


def _pcm_samples(raw_bytes: bytes) -> "array.array":
    """Decode little-endian s16le PCM into a sequence of shorts.

    array.array('h') over a memoryview avoids struct.unpack materializing an
    O(filesize) tuple of Python ints for the whole-buffer detectors. A
    truncated odd-length tail byte is ignored to keep the buffer 2-byte
    aligned; byte order is normalized for big-endian hosts.
    """
    samples = array.array("h")
    usable = len(raw_bytes) - (len(raw_bytes) % 2)
    if usable > 0:
        samples.frombytes(memoryview(raw_bytes)[:usable])
        if sys.byteorder != "little":
            samples.byteswap()
    return samples


def extract_pcm_samples(
    media_path: Path,
    sample_rate: int = 16000,
    clip_start: Optional[float] = None,
    clip_end: Optional[float] = None,
) -> Tuple[bytes, int, int]:
    """Extract raw 16-bit mono PCM samples at specified sample_rate using FFmpeg.

    Without a range the full-file PCM contract is unchanged. When clip_start
    (and optionally clip_end) are given, only that source window is decoded
    (ffmpeg -ss/-t, avoiding the cost of decoding unneeded audio) and sample 0
    of the returned buffer corresponds to source time ``clip_start``. A final
    sample-offset slice trims any demuxer overshoot so the buffer never
    exceeds the requested window.
    """
    has_range = clip_start is not None or clip_end is not None
    start_f = max(0.0, float(clip_start or 0.0)) if has_range else None
    cmd = ["ffmpeg", "-y"]
    if start_f is not None:
        cmd += ["-ss", f"{start_f:.6f}"]
    cmd += ["-i", str(media_path)]
    if has_range and clip_end is not None:
        window = float(clip_end) - float(clip_start or 0.0)
        if window > 0:
            cmd += ["-t", f"{window:.6f}"]
    cmd += [
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", str(sample_rate),
        "-ac", "1",
        "-f", "s16le",
        "pipe:1"
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    raw_bytes = proc.stdout
    if has_range and clip_end is not None:
        # Correctness net: cap the buffer at exactly the requested window so
        # consumers indexing by [clip_start, clip_end) never read past it.
        expected_samples = int(round((float(clip_end) - float(clip_start or 0.0)) * sample_rate))
        if expected_samples > 0:
            raw_bytes = raw_bytes[: expected_samples * 2]
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



def is_waveform_interval_silent(
    raw_bytes: bytes,
    start_time: float,
    end_time: float,
    *,
    sample_rate: int = 16000,
    threshold_db: float = -42.0,
    minimum_silent_ratio: float = 0.80,
    frame_duration_ms: float = 20.0,
) -> bool:
    """VAD-style RMS silence verification for transcript-identified gaps."""
    if not raw_bytes or end_time <= start_time or start_time < 0:
        return False
    total_samples = len(raw_bytes) // 2
    first_sample = max(0, int(start_time * sample_rate))
    last_sample = min(total_samples, int(end_time * sample_rate + 0.999999))
    if last_sample <= first_sample:
        return False
    interval_bytes = raw_bytes[first_sample * 2:last_sample * 2]
    profile = compute_rms_profile(interval_bytes, sample_rate, frame_duration_ms)
    if not profile:
        return False
    silent_ratio = sum(1 for _, db in profile if db <= threshold_db) / len(profile)
    return silent_ratio >= minimum_silent_ratio


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


def detect_audio_clipping(
    raw_bytes: bytes,
    sample_rate: int = 16000,
    threshold_db: float = -0.5
) -> List[dict]:
    """Detect audio clipping events where sample amplitudes saturate near 0 dBFS."""
    if not raw_bytes:
        return []
    threshold_amp = int(32768.0 * (10.0 ** (threshold_db / 20.0)))
    total_samples = len(raw_bytes) // 2
    samples = _pcm_samples(raw_bytes)
    events = []
    in_clip = False
    start_idx = 0
    count = 0
    for i, smp in enumerate(samples):
        if abs(smp) >= threshold_amp:
            if not in_clip:
                in_clip = True
                start_idx = i
                count = 1
            else:
                count += 1
        else:
            if in_clip:
                if count >= 3:
                    start_t = round(start_idx / float(sample_rate), 3)
                    end_t = round(i / float(sample_rate), 3)
                    events.append({
                        "type": "clipping",
                        "source_start": start_t,
                        "source_end": end_t,
                        "duration": round(end_t - start_t, 3),
                        "samples_clipped": count,
                        "confidence": 0.95,
                        "decision": "REVIEW",
                        "reason": f"Audio clipping defect ({count} saturated samples, > {threshold_db} dBFS)",
                    })
                in_clip = False
                count = 0
    if in_clip and count >= 3:
        start_t = round(start_idx / float(sample_rate), 3)
        end_t = round(total_samples / float(sample_rate), 3)
        events.append({
            "type": "clipping",
            "source_start": start_t,
            "source_end": end_t,
            "duration": round(end_t - start_t, 3),
            "samples_clipped": count,
            "confidence": 0.95,
            "decision": "REVIEW",
            "reason": f"Audio clipping defect ({count} saturated samples, > {threshold_db} dBFS)",
        })
    return events


def detect_transient_clicks(
    raw_bytes: bytes,
    sample_rate: int = 16000,
    speech_intervals: Optional[List[Tuple[float, float]]] = None,
) -> List[dict]:
    """Detect brief transient clicks, mouth pops, or mic bumps using peak-to-RMS crest factor.

    When `speech_intervals` (list of (start, end) source seconds) is provided,
    only clicks OUTSIDE speech are flagged: broadband transients inside active
    speech are usually plosives/consonants, not mic defects. Without intervals
    the legacy ungated behavior is preserved.
    """
    if not raw_bytes:
        return []
    total_samples = len(raw_bytes) // 2
    samples = _pcm_samples(raw_bytes)
    frame_len = int(sample_rate * 0.010)  # 10ms frame
    if frame_len <= 0:
        frame_len = 160
    frame_sec = frame_len / float(sample_rate)

    def _in_speech(t0: float, t1: float) -> bool:
        for s_start, s_end in speech_intervals or []:
            if t0 < float(s_end) and t1 > float(s_start):
                return True
        return False

    clicks = []
    for offset in range(0, total_samples - frame_len, frame_len):
        t0 = offset / float(sample_rate)
        if speech_intervals is not None and _in_speech(t0, t0 + frame_sec):
            continue
        frame = samples[offset:offset + frame_len]
        peak = max(abs(s) for s in frame)
        if peak < 3200:
            continue
        rms = math.sqrt(sum(s * s for s in frame) / float(len(frame)))
        crest = peak / (rms + 1e-6)
        if crest > 4.5 and peak > 5000:
            t = round(t0, 3)
            clicks.append({
                "type": "transient_click",
                "source_start": t,
                "source_end": round(t + 0.025, 3),
                "duration": 0.025,
                "confidence": 0.78,
                "decision": "REVIEW",
                "reason": f"Transient mouth click / mic pop (crest factor {crest:.1f})",
            })
    return clicks


def detect_breaths_and_coughs(
    raw_bytes: bytes,
    speech_intervals: List[Tuple[float, float]],
    sample_rate: int = 16000
) -> List[dict]:
    """Detect audible breath intakes and coughs / throat-clears in non-speech intervals."""
    if not raw_bytes:
        return []
    total_samples = len(raw_bytes) // 2
    samples = _pcm_samples(raw_bytes)
    total_dur = total_samples / float(sample_rate)

    gaps = []
    prev_end = 0.0
    for s_start, s_end in sorted(speech_intervals):
        if s_start > prev_end + 0.15:
            gaps.append((prev_end, s_start))
        prev_end = max(prev_end, s_end)
    if total_dur > prev_end + 0.15:
        gaps.append((prev_end, total_dur))

    results = []
    frame_ms = 50.0
    samples_per_frame = int(sample_rate * (frame_ms / 1000.0))
    cough_join_gap = 0.100  # seconds of tolerated silence between joined cough frames

    for gap_start, gap_end in gaps:
        if gap_end - gap_start < 0.20:
            continue
        g_start_idx = int(gap_start * sample_rate)
        g_end_idx = int(gap_end * sample_rate)
        gap_samples = samples[g_start_idx:g_end_idx]

        active_breath_start = None
        breath_frames = 0
        cough_start = None
        last_cough_frame = None
        cough_peak_db = -96.0

        def _flush_cough() -> None:
            nonlocal cough_start, last_cough_frame, cough_peak_db
            if cough_start is None or last_cough_frame is None:
                return
            # A cough frame claims the same trailing burst window as the legacy
            # per-frame proposal; a coalesced run spans first..last + burst.
            cough_end = min(gap_end, last_cough_frame + 0.25)
            results.append({
                "type": "cough",
                "source_start": round(cough_start, 3),
                "source_end": round(cough_end, 3),
                "duration": round(max(0.0, cough_end - cough_start), 3),
                "confidence": 0.85,
                "decision": "REVIEW",
                "reason": f"Acoustic cough/throat-clear burst ({cough_peak_db:.1f} dBFS)",
            })
            cough_start = None
            last_cough_frame = None
            cough_peak_db = -96.0

        for f_idx in range(0, len(gap_samples) - samples_per_frame, samples_per_frame):
            frame = gap_samples[f_idx:f_idx + samples_per_frame]
            rms = math.sqrt(sum(s * s for s in frame) / float(len(frame)))
            db = 20.0 * math.log10(rms / 32768.0) if rms > 0.0001 else -96.0

            zcr = sum(
                1 for i in range(len(frame) - 1)
                if (frame[i] <= 0 and frame[i + 1] > 0) or (frame[i] >= 0 and frame[i + 1] < 0)
            ) / float(len(frame))
            t_frame = gap_start + (f_idx / float(sample_rate))

            if db > -18.0:
                # Coalesce consecutive cough frames (and runs separated by one
                # short sub-tolerance lull) into a single proposal spanning the
                # burst instead of emitting one per 50ms frame.
                if cough_start is not None and (t_frame - last_cough_frame) > cough_join_gap:
                    _flush_cough()
                if cough_start is None:
                    cough_start = t_frame
                last_cough_frame = t_frame
                cough_peak_db = max(cough_peak_db, db)
            else:
                if cough_start is not None and (t_frame - last_cough_frame) > cough_join_gap:
                    _flush_cough()
                if -45.0 <= db <= -22.0 and zcr >= 0.08:
                    if active_breath_start is None:
                        active_breath_start = t_frame
                    breath_frames += 1
                else:
                    if active_breath_start is not None:
                        b_dur = breath_frames * (frame_ms / 1000.0)
                        if 0.18 <= b_dur <= 1.2:
                            results.append({
                                "type": "breath",
                                "source_start": round(active_breath_start, 3),
                                "source_end": round(active_breath_start + b_dur, 3),
                                "duration": round(b_dur, 3),
                                "confidence": 0.80,
                                "decision": "REVIEW",
                                "reason": f"Audible breath intake ({b_dur:.2f}s)",
                            })
                        active_breath_start = None
                        breath_frames = 0

        _flush_cough()

        if active_breath_start is not None:
            b_dur = breath_frames * (frame_ms / 1000.0)
            if 0.18 <= b_dur <= 1.2:
                results.append({
                    "type": "breath",
                    "source_start": round(active_breath_start, 3),
                    "source_end": round(active_breath_start + b_dur, 3),
                    "duration": round(b_dur, 3),
                    "confidence": 0.80,
                    "decision": "REVIEW",
                    "reason": f"Audible breath intake ({b_dur:.2f}s)",
                })

    return results
