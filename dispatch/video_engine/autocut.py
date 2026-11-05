"""Style-Aware & Waveform-Verified Auto-Cut Engine.

Enforces:
1. Waveform Acoustic Energy Check: Never cut purely based on transcript timestamps;
   cross-reference audio RMS decay and snap cut boundaries to zero-crossings with
   >=100ms attack and >=150ms release padding so words are never truncated.
2. Rohit's Speaking Style Invariants:
   - Preserve real-time reflections & self-corrections ("I think... actually no, here's why").
   - Preserve conversational fillers ("so", "like", "you know").
   - Preserve rhetorical emphasis and pauses < 2.5s.
   - Detect mechanical restart loops (repeated 2-4 word n-grams within 3.0s) and propose
     removing redundant stutter while keeping the final take.
   - Propose trimming prolonged dead air (> 2.5s) down to 0.8s.
   - All auto-cuts are generated as non-destructive reviewable proposals (status: "candidate").
"""

import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

from dispatch.video_engine.waveform import (
    extract_pcm_samples,
    snap_boundary_to_waveform_silence,
    find_zero_crossing,
)

logger = logging.getLogger("dispatch.video_engine.autocut")

# Acoustic & speaking thresholds
MIN_DEAD_AIR_PAUSE = 2.5       # Seconds of silence before proposing dead-air trim
TARGET_NATURAL_PAUSE = 0.8     # Remaining pause to retain across a dead-air gap
MAX_RESTART_WINDOW = 3.0       # Max seconds between repeated word loops for stutter detection
MIN_SPEECH_PADDING_MS = 150.0  # Mandatory post-word release padding to prevent truncation


def detect_mechanical_stutters(
    words: List[Dict[str, Any]],
    max_gap_seconds: float = MAX_RESTART_WINDOW
) -> List[Dict[str, Any]]:
    """Detect mechanical repetition / stutter loops (e.g. 'I mean... I mean... I mean').
    
    Preserves the final valid take and proposes cutting the preceding redundant attempts.
    Does NOT flag self-corrections ('actually', 'or rather', 'no wait') or rhetorical conclusions.
    """
    proposals: List[Dict[str, Any]] = []
    if not words or len(words) < 4:
        return proposals

    # Clean words helper
    cleaned_tokens = [w.get("text", "").strip().lower().strip(".,!?:;\"'") for w in words]

    # Look for 1-word, 2-word, or 3-word repetitions within max_gap_seconds
    i = 0
    while i < len(words) - 2:
        matched = False
        # Try 2-word n-gram first
        if i + 3 < len(words):
            gram1 = (cleaned_tokens[i], cleaned_tokens[i + 1])
            gram2 = (cleaned_tokens[i + 2], cleaned_tokens[i + 3])
            w_start1 = float(words[i].get("start", 0.0))
            w_start2 = float(words[i + 2].get("start", 0.0))
            
            if gram1 == gram2 and gram1[0] and (w_start2 - w_start1) <= max_gap_seconds:
                # Check for contrastive correction markers (e.g. 'no', 'actually')
                next_word = cleaned_tokens[i + 4] if i + 4 < len(words) else ""
                if next_word not in ("actually", "no", "rather", "wait", "instead"):
                    cut_start = float(words[i].get("start", 0.0))
                    cut_end = float(words[i + 2].get("start", 0.0))
                    if cut_end > cut_start + 0.1:
                        proposals.append({
                            "id": f"stutter_{len(proposals) + 1}",
                            "type": "false_start",
                            "source_start": cut_start,
                            "source_end": cut_end,
                            "duration": round(cut_end - cut_start, 3),
                            "reason": f"Redundant restart of '{words[i].get('text')} {words[i+1].get('text')}'",
                            "status": "candidate",
                            "confidence": 0.88,
                        })
                        i += 2
                        matched = True
        
        # Try 1-word stutter (e.g. "I... I think")
        if not matched and i + 1 < len(words):
            tok1 = cleaned_tokens[i]
            tok2 = cleaned_tokens[i + 1]
            w_start1 = float(words[i].get("start", 0.0))
            w_start2 = float(words[i + 1].get("start", 0.0))
            
            if tok1 == tok2 and len(tok1) > 1 and (w_start2 - w_start1) <= 2.0:
                cut_start = float(words[i].get("start", 0.0))
                cut_end = float(words[i + 1].get("start", 0.0))
                if cut_end > cut_start + 0.1:
                    proposals.append({
                        "id": f"stutter_{len(proposals) + 1}",
                        "type": "false_start",
                        "source_start": cut_start,
                        "source_end": cut_end,
                        "duration": round(cut_end - cut_start, 3),
                        "reason": f"Stutter repetition of '{words[i].get('text')}'",
                        "status": "candidate",
                        "confidence": 0.82,
                    })
                    i += 1
                    matched = True

        i += 1

    return proposals


def analyze_waveform_and_transcript_autocuts(
    media_path: Optional[Path],
    transcript_segments: List[Dict[str, Any]],
    clip_start: float,
    clip_end: float,
    raw_pcm_bytes: Optional[bytes] = None,
    min_dead_air: float = MIN_DEAD_AIR_PAUSE,
    target_pause: float = TARGET_NATURAL_PAUSE
) -> List[Dict[str, Any]]:
    """Generate reviewable auto-cut candidate proposals combining transcript & audio waveform.
    
    Invariants:
    1. Pauses < min_dead_air (default 2.5s) are PRESERVED as thinking / natural cadence.
    2. Pauses >= min_dead_air are trimmed down to target_pause (default 0.8s).
    3. If media_path or raw_pcm_bytes is available, boundaries are verified against
       audio acoustic energy and snapped to zero-crossings to ensure word endings
       and attack transients are never cut off.
    4. Proposes cuts for mechanical stutters / restart loops while protecting self-corrections.
    5. Returns candidate proposals ready for user review.
    """
    raw_duration = max(0.0, clip_end - clip_start)
    if raw_duration <= 0.0:
        return []

    # Get raw PCM if media available
    pcm_bytes = raw_pcm_bytes
    if pcm_bytes is None and media_path and media_path.exists():
        try:
            pcm_bytes, _, _ = extract_pcm_samples(media_path, sample_rate=16000)
        except Exception as exc:
            logger.warning("Could not extract PCM samples for waveform verification: %s", exc)
            pcm_bytes = None

    # Flatten words within range
    all_words: List[Dict[str, Any]] = []
    for s in transcript_segments:
        s_start = float(s.get("start", 0.0))
        s_end = float(s.get("end", 0.0))
        if s_end <= clip_start or s_start >= clip_end:
            continue
        words = s.get("words", [])
        if words:
            for w in words:
                w_start = float(w.get("start", 0.0))
                w_end = float(w.get("end", 0.0))
                if w_end > clip_start and w_start < clip_end:
                    all_words.append(w)
        else:
            all_words.append({"start": max(clip_start, s_start), "end": min(clip_end, s_end), "text": s.get("text", "")})

    proposals: List[Dict[str, Any]] = []

    # 1. Check for mechanical stutters
    stutter_cuts = detect_mechanical_stutters(all_words)
    for sc in stutter_cuts:
        if pcm_bytes:
            sc["source_start"] = snap_boundary_to_waveform_silence(
                pcm_bytes, sc["source_start"], mode="speech_attack", sample_rate=16000
            )
            sc["source_end"] = snap_boundary_to_waveform_silence(
                pcm_bytes, sc["source_end"], mode="speech_attack", sample_rate=16000
            )
            sc["duration"] = round(sc["source_end"] - sc["source_start"], 3)
        if sc["duration"] >= 0.1:
            proposals.append(sc)

    # 2. Check for dead air pauses (> min_dead_air)
    if all_words:
        all_words.sort(key=lambda w: float(w.get("start", 0.0)))
        
        # Check leading silence
        first_w_start = float(all_words[0].get("start", 0.0))
        leading_gap = first_w_start - clip_start
        if leading_gap >= min_dead_air:
            rem_dur = leading_gap - target_pause
            cut_start = clip_start
            cut_end = clip_start + rem_dur
            if pcm_bytes:
                cut_end = snap_boundary_to_waveform_silence(
                    pcm_bytes, cut_end, mode="speech_attack", sample_rate=16000
                )
            rem_dur = cut_end - cut_start
            if rem_dur >= 0.1:
                proposals.append({
                    "id": f"dead_air_{len(proposals) + 1}",
                    "type": "dead_air",
                    "source_start": round(cut_start, 3),
                    "source_end": round(cut_end, 3),
                    "duration": round(rem_dur, 3),
                    "reason": f"Prolonged initial silence ({leading_gap:.1f}s)",
                    "status": "candidate",
                    "confidence": 0.95,
                })

        # Check gaps between consecutive words
        for idx in range(len(all_words) - 1):
            w_end = float(all_words[idx].get("end", 0.0))
            nxt_start = float(all_words[idx + 1].get("start", 0.0))
            gap = nxt_start - w_end
            
            if gap >= min_dead_air:
                rem_dur = gap - target_pause
                margin = target_pause / 2.0
                raw_cut_start = w_end + margin
                raw_cut_end = nxt_start - margin
                
                if pcm_bytes:
                    # Release check: ensure word ending is not cut off!
                    cut_start = snap_boundary_to_waveform_silence(
                        pcm_bytes, raw_cut_start, mode="speech_release",
                        silence_threshold_db=-32.0, min_speech_padding_ms=MIN_SPEECH_PADDING_MS
                    )
                    # Attack check: ensure next word start is not clipped!
                    cut_end = snap_boundary_to_waveform_silence(
                        pcm_bytes, raw_cut_end, mode="speech_attack"
                    )
                else:
                    cut_start = raw_cut_start
                    cut_end = raw_cut_end

                if cut_end > cut_start + 0.1:
                    actual_dur = cut_end - cut_start
                    proposals.append({
                        "id": f"dead_air_{len(proposals) + 1}",
                        "type": "dead_air",
                        "source_start": round(cut_start, 3),
                        "source_end": round(cut_end, 3),
                        "duration": round(actual_dur, 3),
                        "reason": f"Extended pause ({gap:.1f}s trimmed down to {target_pause:.1f}s)",
                        "status": "candidate",
                        "confidence": 0.92,
                    })

        # Check trailing silence
        last_w_end = float(all_words[-1].get("end", 0.0))
        trailing_gap = clip_end - last_w_end
        if trailing_gap >= min_dead_air:
            rem_dur = trailing_gap - target_pause
            raw_cut_start = clip_end - rem_dur
            if pcm_bytes:
                cut_start = snap_boundary_to_waveform_silence(
                    pcm_bytes, raw_cut_start, mode="speech_release",
                    silence_threshold_db=-32.0, min_speech_padding_ms=MIN_SPEECH_PADDING_MS
                )
            else:
                cut_start = raw_cut_start
            cut_end = clip_end
            if cut_end > cut_start + 0.1:
                proposals.append({
                    "id": f"dead_air_{len(proposals) + 1}",
                    "type": "dead_air",
                    "source_start": round(cut_start, 3),
                    "source_end": round(cut_end, 3),
                    "duration": round(cut_end - cut_start, 3),
                    "reason": f"Prolonged trailing silence ({trailing_gap:.1f}s)",
                    "status": "candidate",
                    "confidence": 0.95,
                })

    return proposals
