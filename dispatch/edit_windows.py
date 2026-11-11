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

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dispatch.transcription.subprocess_utils import low_priority_subprocess_kwargs

logger = logging.getLogger("dispatch.edit_windows")

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


def select_words_for_range(
    words: List[Dict[str, Any]],
    window_start: float,
    window_end: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Filter cached SOURCE-relative words down to a window's local timeline."""
    win_hi = float(window_end) if window_end is not None else (window_start + MAX_WINDOW_SECONDS)
    out = []
    for w in words:
        ws, we = float(w.get("start", 0.0)), float(w.get("end", 0.0))
        if we > window_start and ws < win_hi:
            out.append({**w, "start": round(max(ws, window_start) - window_start, 3),
                        "end": round(min(we, win_hi) - window_start, 3)})
    return out


def validate_selection(sel_start: float, sel_end: float) -> Optional[str]:
    """5-180 s product limit for the FINAL selection. Returns error or None."""
    dur = sel_end - sel_start
    if dur < MIN_SELECTION_SECONDS - 1e-6:
        return f"Selection must be at least {MIN_SELECTION_SECONDS:.0f} seconds"
    if dur > MAX_SELECTION_SECONDS + 1e-6:
        return f"Selection must be at most {MAX_SELECTION_SECONDS:.0f} seconds"
    return None


def _fit_proxy_dimensions(source_width: int, source_height: int) -> Tuple[int, int]:
    """Compute even 720p-class proxy dimensions preserving source orientation & aspect."""
    sw = max(2, int(source_width))
    sh = max(2, int(source_height))
    if sh > sw:
        max_w, max_h = PROXY_HEIGHT, PROXY_WIDTH  # 720 x 1280 for portrait
    else:
        max_w, max_h = PROXY_WIDTH, PROXY_HEIGHT  # 1280 x 720 for landscape/square
    scale = min(max_w / float(sw), max_h / float(sh))
    tw = max(2, min(max_w, int(round((sw * scale) / 2.0)) * 2))
    th = max(2, min(max_h, int(round((sh * scale) / 2.0)) * 2))
    return tw, th


def ffprobe_validate_proxy(
    meta: Dict[str, Any],
    expected_duration: float,
    source_meta: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Verify a staged proxy preserves duration/orientation/aspect/audio. Error or None."""
    if abs(float(meta.get("duration", 0.0)) - expected_duration) > 1.0:
        return f"proxy duration {meta.get('duration')} != window {expected_duration}"
    if not meta.get("has_audio"):
        return "proxy is missing the audio track"
    w, h = int(meta.get("width", 0)), int(meta.get("height", 0))
    if w < 2 or h < 2 or (w % 2 != 0) or (h % 2 != 0):
        return f"proxy is {w}x{h}, expected even positive dimensions"
    if h > w:
        if w > PROXY_HEIGHT or h > PROXY_WIDTH:
            return f"portrait proxy {w}x{h} exceeds 720x1280 cap"
    else:
        if w > PROXY_WIDTH or h > PROXY_HEIGHT:
            return f"proxy {w}x{h} exceeds 1280x720 cap"
    if isinstance(source_meta, dict):
        sw = int(source_meta.get("effective_width") or source_meta.get("width") or 0)
        sh = int(source_meta.get("effective_height") or source_meta.get("height") or 0)
        src_orient = str(source_meta.get("orientation") or "").strip().lower()
        if sw > 0 and sh > 0:
            src_is_portrait = sh > sw
            if src_is_portrait and not (h > w):
                return f"proxy orientation {w}x{h} lost portrait source {sw}x{sh}"
            if sw > sh and not (w > h):
                return f"proxy orientation {w}x{h} lost landscape source {sw}x{sh}"
            src_ratio = sw / float(sh)
            proxy_ratio = w / float(h)
            if abs(proxy_ratio - src_ratio) > 0.06:
                return f"proxy aspect ratio {w}x{h} ({proxy_ratio:.2f}) != source {sw}x{sh} ({src_ratio:.2f})"
        elif src_orient == "portrait" and not (h > w):
            return f"proxy {w}x{h} must remain portrait for portrait source"
        elif src_orient == "landscape" and not (w >= h):
            return f"proxy {w}x{h} must remain landscape for landscape source"
    return None


# ------------------------------------------------------------------ staging

def build_proxy_cmd(
    source: str,
    out_path: str,
    window_start: float,
    window_end: float,
    *,
    source_width: Optional[int] = None,
    source_height: Optional[int] = None,
    orientation: Optional[str] = None,
) -> List[str]:
    """720p30 edit proxy of the bounded window. Preserves source orientation and aspect
    ratio without forced 16:9 letterboxing; bitrate is a target, actual bytes verified."""
    dur = round(window_end - window_start, 3)
    if source_width and source_height and int(source_width) > 0 and int(source_height) > 0:
        tw, th = _fit_proxy_dimensions(int(source_width), int(source_height))
        vf = f"scale={tw}:{th},setsar=1"
    elif orientation and str(orientation).strip().lower() == "portrait":
        vf = (
            f"scale={PROXY_HEIGHT}:{PROXY_WIDTH}:force_original_aspect_ratio=decrease,"
            "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1"
        )
    elif orientation and str(orientation).strip().lower() == "landscape":
        vf = (
            f"scale={PROXY_WIDTH}:{PROXY_HEIGHT}:force_original_aspect_ratio=decrease,"
            "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1"
        )
    else:
        # Orientation-aware even scaling within 720p portrait/landscape bounds, zero letterbox padding.
        vf = (
            "scale="
            f"w='trunc(iw*min(if(gt(ih\\,iw)\\,{PROXY_HEIGHT}\\,{PROXY_WIDTH})/iw\\,"
            f"if(gt(ih\\,iw)\\,{PROXY_WIDTH}\\,{PROXY_HEIGHT})/ih)/2)*2':"
            f"h='trunc(ih*min(if(gt(ih\\,iw)\\,{PROXY_HEIGHT}\\,{PROXY_WIDTH})/iw\\,"
            f"if(gt(ih\\,iw)\\,{PROXY_WIDTH}\\,{PROXY_HEIGHT})/ih)/2)*2',setsar=1"
        )
    return [
        "ffmpeg", "-y", "-ss", f"{window_start:.3f}", "-i", source,
        "-t", f"{dur:.3f}",
        "-vf", vf,
        "-r", str(PROXY_FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-b:v", PROXY_VIDEO_BITRATE,
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path,
    ]


def _invoke_ffmpeg(run_ffmpeg: Optional[Any], cmd: List[str], timeout: int) -> Any:
    if run_ffmpeg is not None:
        try:
            return run_ffmpeg(cmd, capture_output=True, text=True, timeout=timeout)
        except TypeError:
            return run_ffmpeg(cmd)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        **low_priority_subprocess_kwargs(),
    )


def stage_edit_window(
    clip_id: str, user_id: str, source_video: str,
    window_start: float, window_end: float,
    *, transcribe_fn, media_store, db_ops,
    ffprobe_meta, run_ffmpeg=None,
) -> Dict[str, Any]:
    """Stage one bounded edit window: extract proxy -> verify -> transcribe
    (window-local audio) -> upload via MediaStore -> persist. `db_ops` isolates
    Supabase RPCs for testing. Returns the persisted window record."""
    span = window_end - window_start
    if span <= 0 or span > MAX_WINDOW_SECONDS + 1e-6:
        raise ValueError(f"window span {span:.1f}s outside the {MAX_WINDOW_SECONDS:.0f}s cap")

    source_meta: Optional[Dict[str, Any]] = None
    if ffprobe_meta is not None and source_video and Path(source_video).exists():
        try:
            probed = ffprobe_meta(source_video)
            if isinstance(probed, dict):
                source_meta = probed
        except Exception as exc:
            logger.debug("Source probe skipped for %s: %s", source_video, exc)

    sw = int(source_meta.get("effective_width") or source_meta.get("width") or 0) if source_meta else 0
    sh = int(source_meta.get("effective_height") or source_meta.get("height") or 0) if source_meta else 0
    sorient = str(source_meta.get("orientation") or "").strip().lower() if source_meta else ""

    window_id = db_ops.create_window(clip_id, user_id, window_start, window_end)
    try:
        with tempfile.TemporaryDirectory(prefix="editwin_") as td:
            proxy = str(Path(td) / "proxy.mp4")
            audio = str(Path(td) / "audio.mp3")
            cmd = build_proxy_cmd(
                source_video, proxy, window_start, window_end,
                source_width=sw or None,
                source_height=sh or None,
                orientation=sorient or None,
            )
            res = _invoke_ffmpeg(run_ffmpeg, cmd, timeout=600)
            if res.returncode != 0:
                stderr_tail = (getattr(res, "stderr", "") or "")[-200:]
                raise RuntimeError(f"proxy encode failed: {stderr_tail}")
            meta = ffprobe_meta(proxy)
            err = ffprobe_validate_proxy(meta, span, source_meta=source_meta)
            if err:
                raise RuntimeError(f"proxy rejected: {err}")

            # transcription: window-local audio from raw source when available, source-relative words
            if source_video and Path(source_video).exists():
                audio_cmd = [
                    "ffmpeg", "-y", "-ss", f"{window_start:.3f}", "-i", source_video,
                    "-t", f"{round(span, 3):.3f}", "-vn", "-c:a", "libmp3lame", "-q:a", "5", audio,
                ]
            else:
                audio_cmd = ["ffmpeg", "-y", "-i", proxy, "-vn", "-c:a", "libmp3lame", "-q:a", "5", audio]
            ares = _invoke_ffmpeg(run_ffmpeg, audio_cmd, timeout=300)
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
