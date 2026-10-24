"""Bounded hybrid clip editing (owner brief 2026-10-25).

A temporary edit window is a 720p30 proxy of a bounded SOURCE window staged in R2
for remote review. Concepts kept strictly separate:

  1. SOURCE RECORDING - full original, stays on the PC. Never uploaded to R2.
  2. EDIT WINDOW - bounded proxy (<= MAX_WINDOW_SECONDS span), staged lazily.
  3. SELECTED CLIP - the editor's source-time selection (5-180 s). The Gemini
     suggestion is stored separately (ai_suggested_*) and never rewritten by trims.

Window rules (owner brief):
  - Initial request: up to 60 s before and after the suggestion/selection, clamped
    to source bounds, with a 300 s maximum span.
  - A ready window is REUSED when it fully covers the wanted range.
  - "Load More" stages a NEW window around the requested selection; the old window
    and its edits stay usable until the new media is uploaded and verified.
  - Transcript words are cached SOURCE-relative and reused; extensions transcribe
    only newly uncovered audio. Gemini is never called during pointer movement.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MAX_WINDOW_SECONDS = 300.0
CONTEXT_SECONDS = 60.0
MIN_SELECTION_SECONDS = 5.0
MAX_SELECTION_SECONDS = 180.0

# Transcription input bound for edit windows: separate from MAX_CLIP_DURATION
# (the final-clip product limit). A 300 s window + model headroom.
MAX_WINDOW_TRANSCRIBE_SECONDS = 300.0

PROXY_WIDTH = 1280
PROXY_HEIGHT = 720
PROXY_FPS = 30
PROXY_VIDEO_BITRATE = "2500k"  # estimate only - actual bytes verified post-encode


# ---------------------------------------------------------------- pure logic

def compute_window(
    sel_start: float, sel_end: float, source_duration: float,
    *, context_seconds: float = CONTEXT_SECONDS,
    max_window: float = MAX_WINDOW_SECONDS,
) -> Tuple[float, float]:
    """Initial window: up to `context_seconds` before/after the selection, clamped
    to source bounds, then capped to `max_window` span keeping the selection centred
    as far as the clamp allows. Pure function."""
    if source_duration <= 0:
        raise ValueError("source_duration must be positive")
    sel_start = max(0.0, min(sel_start, source_duration))
    sel_end = max(sel_start, min(sel_end, source_duration))

    start = max(0.0, sel_start - context_seconds)
    end = min(source_duration, sel_end + context_seconds)
    if end - start > max_window:
        # keep the selection inside the window; slide toward the source edge
        overflow = (end - start) - max_window
        room_left = sel_start - start
        trim_left = min(room_left, overflow / 2.0)
        start += trim_left
        end -= (overflow - trim_left)
    return round(start, 3), round(end, 3)


def compute_extension_window(
    wanted_start: float, wanted_end: float, source_duration: float,
    *, max_window: float = MAX_WINDOW_SECONDS,
) -> Tuple[float, float]:
    """'Load More': a NEW bounded window around the explicitly requested range."""
    return compute_window(wanted_start, wanted_end, source_duration, context_seconds=0.0, max_window=max_window)


def covers(window: Tuple[float, float], wanted: Tuple[float, float]) -> bool:
    return window[0] <= wanted[0] + 1e-6 and window[1] >= wanted[1] - 1e-6


def combined_span_fits(window: Tuple[float, float], wanted: Tuple[float, float],
                       max_window: float = MAX_WINDOW_SECONDS) -> bool:
    """Reuse rule: windows are shared only when the COMBINED span fits the cap.
    Never merge just because the gap is small."""
    lo, hi = min(window[0], wanted[0]), max(window[1], wanted[1])
    return (hi - lo) <= max_window + 1e-6


def uncovered_ranges(wanted: Tuple[float, float],
                     covered: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Audio ranges inside `wanted` not covered by any cached transcript window,
    for extension transcription. Deterministic, sorted, non-adjacent merged."""
    if not covered:
        return [wanted]
    covered_sorted = sorted(covered)
    holes: List[Tuple[float, float]] = []
    cursor = wanted[0]
    for c0, c1 in covered_sorted:
        c1 = min(c1, wanted[1])
        if c1 <= cursor:
            continue
        if c0 > cursor:
            holes.append((cursor, min(c0, wanted[1])))
        cursor = max(cursor, c1)
        if cursor >= wanted[1]:
            break
    if cursor < wanted[1]:
        holes.append((cursor, wanted[1]))
    return [(a, b) for a, b in holes if b - a > 0.01]


def select_words_for_range(words: List[Dict[str, Any]],
                           window_start: float) -> List[Dict[str, Any]]:
    """Filter cached SOURCE-relative words down to a window's local timeline."""
    out = []
    for w in words:
        ws, we = float(w.get("start", 0.0)), float(w.get("end", 0.0))
        if we > window_start and ws < window_start + MAX_WINDOW_SECONDS:
            out.append({**w, "start": round(max(ws, window_start) - window_start, 3),
                        "end": round(min(we, window_start + MAX_WINDOW_SECONDS) - window_start, 3)})
    return out


def validate_selection(sel_start: float, sel_end: float) -> Optional[str]:
    """5-180 s product limit for the FINAL selection. Returns error or None."""
    dur = sel_end - sel_start
    if dur < MIN_SELECTION_SECONDS - 1e-6:
        return f"Selection must be at least {MIN_SELECTION_SECONDS:.0f} seconds"
    if dur > MAX_SELECTION_SECONDS + 1e-6:
        return f"Selection must be at most {MAX_SELECTION_SECONDS:.0f} seconds"
    return None


def ffprobe_validate_proxy(meta: Dict[str, Any], expected_duration: float) -> Optional[str]:
    """Verify a staged proxy preserves duration/orientation/audio. Error or None."""
    if abs(float(meta.get("duration", 0.0)) - expected_duration) > 1.0:
        return f"proxy duration {meta.get('duration')} != window {expected_duration}"
    if not meta.get("has_audio"):
        return "proxy is missing the audio track"
    w, h = int(meta.get("width", 0)), int(meta.get("height", 0))
    if (w, h) not in ((1280, 720), (720, 1280)):
        return f"proxy is {w}x{h}, expected 1280x720-class proxy"
    return None


# ------------------------------------------------------------------ staging

def build_proxy_cmd(source: str, out_path: str, window_start: float,
                    window_end: float) -> List[str]:
    """720p30 edit proxy of the bounded window. Duration/orientation/audio
    preserved; bitrate is a target, actual bytes verified after encode."""
    dur = round(window_end - window_start, 3)
    return [
        "ffmpeg", "-y", "-ss", f"{window_start:.3f}", "-i", source,
        "-t", f"{dur:.3f}",
        "-vf", f"scale={PROXY_WIDTH}:{PROXY_HEIGHT}:force_original_aspect_ratio=decrease,pad={PROXY_WIDTH}:{PROXY_HEIGHT}:(ow-iw)/2:(oh-ih)/2",
        "-r", str(PROXY_FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-b:v", PROXY_VIDEO_BITRATE,
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path,
    ]


def stage_edit_window(
    clip_id: str, user_id: str, source_video: str,
    window_start: float, window_end: float,
    *, transcribe_fn, media_store, db_ops,
    ffprobe_meta, run_ffmpeg,
) -> Dict[str, Any]:
    """Stage one bounded edit window: extract proxy -> verify -> transcribe
    (window-local audio) -> upload via MediaStore -> persist. `db_ops` isolates
    Supabase RPCs for testing. Returns the persisted window record."""
    span = window_end - window_start
    if span <= 0 or span > MAX_WINDOW_SECONDS + 1e-6:
        raise ValueError(f"window span {span:.1f}s outside the {MAX_WINDOW_SECONDS:.0f}s cap")

    window_id = db_ops.create_window(clip_id, user_id, window_start, window_end)
    try:
        with tempfile.TemporaryDirectory(prefix="editwin_") as td:
            proxy = str(Path(td) / "proxy.mp4")
            audio = str(Path(td) / "audio.mp3")
            cmd = build_proxy_cmd(source_video, proxy, window_start, window_end)
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if res.returncode != 0:
                raise RuntimeError(f"proxy encode failed: {res.stderr[-200:]}")
            meta = ffprobe_meta(proxy)
            err = ffprobe_validate_proxy(meta, span)
            if err:
                raise RuntimeError(f"proxy rejected: {err}")

            # transcription: window-local audio, source-relative words
            ares = subprocess.run(
                ["ffmpeg", "-y", "-i", proxy, "-vn", "-c:a", "libmp3lame", "-q:a", "5", audio],
                capture_output=True, text=True, timeout=300)
            if ares.returncode != 0:
                raise RuntimeError("audio extraction failed")
            words_raw = transcribe_fn(audio, max_input_seconds=MAX_WINDOW_TRANSCRIBE_SECONDS)
            words = [{"word": w.get("word", ""), "start": round(window_start + float(w.get("start", 0.0)), 3),
                      "end": round(window_start + float(w.get("end", 0.0)), 3)}
                     for w in words_raw.get("words", [])]
            db_ops.put_transcript(window_id, user_id, words)

            # quota-gated upload via the existing media contract (kind edit_window).
            # media_store implementations verify ACTUAL bytes against the reservation.
            size = Path(proxy).stat().st_size
            reservation = media_store.reserve_upload(
                user_id=user_id, object_kind="edit_window", mime_type="video/mp4",
                expected_bytes=size, local_clip_id=clip_id)
            object_id = media_store.upload_and_finalize(reservation, proxy, user_id)
            db_ops.mark_ready(window_id, object_id)
            return {"window_id": window_id, "status": "ready",
                    "window_start": window_start, "window_end": window_end,
                    "media_object_id": object_id, "words": words}
    except Exception as e:
        db_ops.mark_failed(window_id, str(e))
        raise
