"""Gemini Two-Stage Transcription Engine.

Provides:
- Stage 1: Coarse sentence-level transcription via gemini-3.5-flash-lite (high quota: 500 RPD)
- Stage 2: Precision word-level millisecond alignment via gemini-3.5-transcribe (25 RPD)
- Quota tracking & safety guard
"""
import os
import json
import time
import logging
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from dispatch import db
from dispatch.config import GEMINI_API_KEY
from dispatch.transcription.audio import (
    _probe_audio_duration,
    extract_compressed_audio,
    split_audio_for_transcription,
)
from dispatch.config import MAX_CLIP_DURATION, PROCESSING_DIR

logger = logging.getLogger("dispatch.transcription.gemini")

# Five seconds preserves ordinary sentence context at ten-minute seams with
# less than 1% additional audio per Stage 1 window.
STAGE1_WINDOW_OVERLAP_SECONDS = 5.0


def get_gemini_client(api_key: Optional[str] = None):
    """Lazy initialize and return Google GenAI Client with active API key."""
    key = api_key or os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY
    if not key:
        raise ValueError("GEMINI_API_KEY is not configured in environment or .env")
    from google import genai
    return genai.Client(api_key=key)


def _parse_offset_to_seconds(offset_str: Any) -> float:
    """Parse Gemini offset strings like '0.100s' or '1.450s' into float seconds."""
    if isinstance(offset_str, (int, float)):
        return float(offset_str)
    if not offset_str:
        return 0.0
    s = str(offset_str).strip()
    if s.endswith("s"):
        s = s[:-1]
    try:
        return float(s)
    except ValueError:
        return 0.0


def check_and_increment_daily_transcribe_quota(max_daily_requests: int = 25) -> bool:
    """Check if gemini-3.5-transcribe is within the daily limit and atomically reserve one request.

    Single conditional UPDATE replaces the old two-setting read-modify-write, which
    let two worker threads both observe 24/25 and both fire the API call. Returns
    True if the slot is reserved, False if the daily limit is reached.
    """
    from datetime import datetime, timezone
    import sqlite3

    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES ('transcribe_quota_date', ?)",
                (today_utc,)
            )
            cursor.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES ('transcribe_quota_count', '0')"
            )
            # Read the stored date; if it is stale, reset the counter and ADVANCE
            # THE DATE inside the same transaction, then take the slot with a
            # conditional increment. (The date must be written on roll-over:
            # resetting the counter while leaving yesterday's date would zero
            # the counter on EVERY call and defeat the limit entirely.)
            cursor.execute("SELECT value FROM settings WHERE key = 'transcribe_quota_date'")
            stored_date_row = cursor.fetchone()
            if stored_date_row and stored_date_row["value"] != today_utc:
                cursor.execute("UPDATE settings SET value = '0' WHERE key = 'transcribe_quota_count'")
                cursor.execute("UPDATE settings SET value = ? WHERE key = 'transcribe_quota_date'", (today_utc,))
            cursor.execute(
                "UPDATE settings SET value = CAST(COALESCE(value, '0') AS INTEGER) + 1 "
                "WHERE key = 'transcribe_quota_count' AND CAST(COALESCE(value, '0') AS INTEGER) < ?",
                (max_daily_requests,)
            )
            reserved = cursor.rowcount == 1
            conn.commit()
            return reserved
        except sqlite3.Error as e:
            logger.error("Daily transcribe quota reservation failed: %s", e)
            conn.rollback()
            # Reserve nothing on failure so we never spend on a broken counter.
            return False


def refund_daily_transcribe_quota() -> None:
    """Return a reserved transcribe slot (floor 0) when the request never reached Gemini.

    Called after a Stage-2 failure that happened before/without the provider call,
    so a stuck render cannot race the daily counter upward (audit 2026-10-27).
    """
    from datetime import datetime, timezone

    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        # Only refund against today's counter to avoid gifting yesterday's
        # unused slot to a different day by accident.
        cursor.execute(
            "UPDATE settings SET value = MAX(CAST(value AS INTEGER) - 1, 0) "
            "WHERE key = 'transcribe_quota_count' AND value IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM settings WHERE key = 'transcribe_quota_date' "
            "AND value = ?)",
            (today_utc,)
        )
        conn.commit()


def transcribe_stage1_flash_lite(
    media_path: Path,
    prompt: Optional[str] = None,
    api_key: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Stage 1: Coarse sentence-level transcription using gemini-3.5-flash-lite.
    
    Args:
        media_path: Local path to .mp4 video or .mp3 audio file.
        prompt: Optional custom transcription prompt.
        api_key: Optional Gemini API key override (e.g. user vaulted key).
        
    Returns:
        List of segment dicts: [{'start': float, 'end': float, 'text': str, 'speaker': str}]
    """
    client = get_gemini_client(api_key=api_key)
    from google.genai import types

    system_instruction = (
        "You are an expert audio transcription engine. "
        "Transcribe the spoken audio verbatim in original language (detect Hindi, English, Hinglish). "
        "Provide precise timestamps for each spoken segment/phrase in valid JSON format: "
        '[{"start": "00:00.00", "end": "00:02.50", "text": "...", "speaker": "Speaker 1"}]. '
        "Return ONLY the JSON array."
    )
    normalized_segments: List[Dict[str, Any]] = []
    is_video = media_path.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm")

    # Keep the original source and its single inbox job intact. Bound each Gemini
    # request to ten minutes and restore each part's timestamps to source time.
    with tempfile.TemporaryDirectory(prefix="dispatch_stage1_") as work_dir:
        work_path = Path(work_dir)
        audio_path = (
            extract_compressed_audio(media_path, work_path / "source.mp3")
            if is_video else media_path
        )
        chunks = split_audio_for_transcription(
            audio_path, work_path / "parts", chunk_seconds=600,
            overlap_seconds=STAGE1_WINDOW_OVERLAP_SECONDS,
        )
        logger.info("Stage 1: Transcribing %s as %d bounded audio part(s)", media_path.name, len(chunks))

        for part_index, (part_path, source_offset) in enumerate(chunks):
            uploaded_file = None
            try:
                logger.info("Stage 1: Uploading %s (part %d/%d) to Gemini Files API...",
                            part_path.name, part_index + 1, len(chunks))
                uploaded_file = client.files.upload(file=str(part_path), config=dict(mime_type="audio/mp3"))
                t0 = time.time()
                response = client.models.generate_content(
                    model="gemini-3.5-flash-lite",
                    contents=[uploaded_file, prompt or system_instruction],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.0,
                    ),
                )
                logger.info("Stage 1 part %d/%d completed in %.2fs",
                            part_index + 1, len(chunks), time.time() - t0)
                segments_raw = json.loads(response.text or "[]")
                if not isinstance(segments_raw, list):
                    raise ValueError(f"Gemini returned a non-list transcript for part {part_index + 1}")

                for segment in segments_raw:
                    if not isinstance(segment, dict):
                        continue
                    start = _parse_stage1_time(segment.get("start", 0.0)) + source_offset
                    end = _parse_stage1_time(segment.get("end", 0.0)) + source_offset
                    if end < start:
                        continue
                    normalized_segments.append({
                        "start": start,
                        "end": end,
                        "text": str(segment.get("text", "")).strip(),
                        "speaker": str(segment.get("speaker", "spk:0")).strip(),
                        "_source_part_index": part_index,
                    })
            finally:
                if uploaded_file is not None:
                    try:
                        client.files.delete(name=uploaded_file.name)
                    except Exception as cleanup_error:
                        logger.warning("Could not delete Gemini file for part %d: %s",
                                        part_index + 1, cleanup_error)

    return _deduplicate_overlapping_segments(
        normalized_segments, overlap_seconds=STAGE1_WINDOW_OVERLAP_SECONDS
    )


def _deduplicate_overlapping_segments(
    segments: List[Dict[str, Any]], overlap_seconds: float
) -> List[Dict[str, Any]]:
    """Merge exact duplicate phrases reported by adjacent overlapping windows."""
    ordered = sorted(segments, key=lambda segment: (segment["start"], segment["end"]))
    kept: List[Dict[str, Any]] = []
    for segment in ordered:
        part_index = int(segment.get("_source_part_index", 0))
        normalized_text = re.sub(r"[^\w]+", " ", segment.get("text", "").casefold()).strip()
        duplicate = None
        if part_index > 0 and normalized_text:
            for previous in reversed(kept):
                if int(previous.get("_source_part_index", 0)) < part_index - 1:
                    break
                if int(previous.get("_source_part_index", 0)) != part_index - 1:
                    continue
                if previous["end"] < segment["start"] - overlap_seconds - 0.5:
                    break
                previous_text = re.sub(r"[^\w]+", " ", previous.get("text", "").casefold()).strip()
                intersection = min(previous["end"], segment["end"]) - max(previous["start"], segment["start"])
                if previous_text == normalized_text and intersection >= -0.35:
                    duplicate = previous
                    break
        if duplicate is None:
            kept.append(dict(segment))
        else:
            duplicate["start"] = min(duplicate["start"], segment["start"])
            duplicate["end"] = max(duplicate["end"], segment["end"])
            if len(segment.get("text", "")) > len(duplicate.get("text", "")):
                duplicate["text"] = segment["text"]

    return [
        {key: value for key, value in segment.items() if key != "_source_part_index"}
        for segment in kept
    ]


def _parse_stage1_time(value: Any) -> float:
    """Parse Gemini seconds or MM:SS / HH:MM:SS timestamps."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if text.endswith("s"):
        text = text[:-1]
    if ":" in text:
        parts = text.split(":")
        try:
            if len(parts) == 2:
                return float(parts[0]) * 60 + float(parts[1])
            if len(parts) == 3:
                return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        except ValueError:
            return 0.0
    return _parse_offset_to_seconds(text)


def transcribe_stage2_precision_words(
    media_path: Path,
    max_input_seconds: Optional[float] = None,
    api_key: Optional[str] = None
) -> Dict[str, Any]:
    """Stage 2: Precision word-level alignment using gemini-3.5-transcribe.
    
    Args:
        media_path: Local path to sliced 30-60s clip video or audio file.
        max_input_seconds: Optional override for max audio length.
        api_key: Optional Gemini API key override (e.g. user vaulted key).
        
    Returns:
        Dict with:
          - full_text: complete verbatim transcription
          - words: list of [{'word': str, 'speaker': str, 'start': float, 'end': float}]
          - speech_start: float timestamp of first spoken word
          - speech_end: float timestamp of last spoken word
    """
    # Final clips use MAX_CLIP_DURATION + headroom; bounded edit windows pass
    # their own 300 s bound explicitly. Full-source transcription stays disabled.
    max_stage2_seconds = max_input_seconds if max_input_seconds else MAX_CLIP_DURATION + 16.0
    if media_path.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm"):
        from dispatch.ingestion.validator import inspect_video_orientation
        info = inspect_video_orientation(media_path)
        media_duration = float(info.get("duration") or 0.0) if info.get("valid") else 0.0
    else:
        media_duration = _probe_audio_duration(media_path)
    if media_duration <= 0 or media_duration > max_stage2_seconds:
        raise ValueError(
            f"Stage 2 accepts only a bounded clip audio input (<= {max_stage2_seconds:.0f}s); "
            f"received {media_duration:.1f}s. Full-source transcription is disabled."
        )

    client = get_gemini_client(api_key=api_key)
    from google.genai import types

    if media_path.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm"):
        audio_path = extract_compressed_audio(media_path)
        cleanup_audio = True
    else:
        audio_path = media_path
        cleanup_audio = False

    try:
        logger.info("Stage 2: Uploading cut clip audio %s to Gemini...", audio_path.name)
        uploaded_file = client.files.upload(file=str(audio_path), config=dict(mime_type="audio/mp3"))

        logger.info("Stage 2: Calling gemini-3.5-transcribe with word timestamps...")
        t0 = time.time()
        response = client.models.generate_content(
            model="gemini-3.5-transcribe",
            contents=[uploaded_file],
            config=types.GenerateContentConfig(
                audio_transcription_config=types.AudioTranscriptionConfig(
                    word_timestamp=True,
                    diarization=True
                )
            )
        )
        elapsed = time.time() - t0
        logger.info("Stage 2 completed in %.2fs", elapsed)

        words: List[Dict[str, Any]] = []
        for cand in getattr(response, "candidates", []) or []:
            for part in getattr(getattr(cand, "content", None), "parts", []) or []:
                trans = getattr(part, "audio_transcription", None)
                if trans:
                    spk = getattr(trans, "speaker_label", "")
                    for w in getattr(trans, "words", []) or []:
                        w_start = _parse_offset_to_seconds(getattr(w, "start_offset", 0.0))
                        w_end = _parse_offset_to_seconds(getattr(w, "end_offset", 0.0))
                        words.append({
                            "word": getattr(w, "word", ""),
                            "speaker": spk,
                            "start": w_start,
                            "end": w_end
                        })

        speech_start = words[0]["start"] if words else 0.0
        speech_end = words[-1]["end"] if words else 0.0

        # Cleanup remote cloud file
        try:
            client.files.delete(name=uploaded_file.name)
        except Exception:
            pass

        return {
            "full_text": response.text or "",
            "words": words,
            "speech_start": speech_start,
            "speech_end": speech_end
        }

    finally:
        if cleanup_audio and audio_path.exists() and audio_path != media_path:
            try:
                audio_path.unlink()
            except Exception:
                pass


def extract_stage2_clip_audio(
    source_path: Path,
    start_seconds: float,
    end_seconds: float,
    output_path: Path,
    attempts: int = 2,
) -> Path:
    """Extract only a bounded clip window; never substitute the full source."""
    duration = end_seconds - start_seconds
    if start_seconds < 0 or duration <= 0 or duration > MAX_CLIP_DURATION + 16.0:
        raise ValueError(f"Invalid bounded Stage 2 range [{start_seconds:.2f}, {end_seconds:.2f}]")
    last_error = "no extraction attempt completed"
    for attempt in range(1, max(1, attempts) + 1):
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", f"{start_seconds:.3f}", "-i", str(source_path),
                    "-t", f"{duration:.3f}", "-map", "0:a:0", "-vn",
                    "-acodec", "libmp3lame", "-b:a", "96k", str(output_path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
                check=False,
            )
            if result.returncode == 0 and output_path.is_file() and output_path.stat().st_size > 0:
                return output_path
            last_error = result.stderr.strip() or f"ffmpeg exited {result.returncode}"
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_error = str(exc)
        if output_path.exists():
            output_path.unlink()
        logger.warning(
            "Stage 2 bounded audio extraction attempt %d/%d failed for %s: %s",
            attempt, max(1, attempts), source_path.name, last_error,
        )

    raise RuntimeError(
        f"Stage 2 bounded audio extraction failed after {max(1, attempts)} attempts; "
        f"full-source transcription is disabled. {last_error}"
    )


def drain_queued_transcriptions(max_batch: int = 10) -> int:
    """FIFO Rolling Backlog Auto-Drain.
    
    Checks if quota is available today, and drains yesterday's leftover
    queued_transcription clips first before processing new incoming recordings.
    """
    queued_clips = db.get_queued_transcription_clips(limit=max_batch)
    if not queued_clips:
        return 0

    from dispatch.captions.grouper import group_words_into_captions
    from dispatch.captions.presets import get_default_preset_id
    from dispatch.video_engine.renderer import render_clip
    import subprocess

    drained = 0
    for clip in queued_clips:
        clip_id = clip["id"]

        chunk_id = clip.get("chunk_id")
        chunk = db.get_chunk_by_id(chunk_id) if chunk_id else None
        source_path = Path(chunk["filepath"]) if chunk and chunk.get("filepath") else None

        if not source_path or not source_path.exists():
            logger.warning("Cannot drain queued clip %s: source video not found on disk", clip_id)
            continue

        try:
            c_start = float(clip["start_time"])
            c_end = float(clip["end_time"])
            range_revision = int(clip.get("range_revision") or 0)
            p_start = float(clip["padded_start"]) if clip.get("padded_start") is not None else max(0.0, c_start - 4.0)
            p_end = float(clip["padded_end"]) if clip.get("padded_end") is not None else (c_end + 4.0)

            slice_duration = p_end - p_start
            if p_start < 0 or slice_duration <= 0 or slice_duration > MAX_CLIP_DURATION + 16.0:
                raise ValueError(f"Invalid bounded Stage 2 range [{p_start:.2f}, {p_end:.2f}]")

            user_id = clip.get("user_id")
            active_key = ""
            if user_id:
                try:
                    from dispatch.youtube_connections import get_user_gemini_key
                    active_key = get_user_gemini_key(user_id) or ""
                except Exception:
                    pass
            from dispatch.config import GEMINI_API_KEY
            active_key = active_key or os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY

            PROCESSING_DIR.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=f"dispatch_stage2_{clip_id}_", dir=str(PROCESSING_DIR)) as temp_dir:
                slice_mp3 = Path(temp_dir) / "clip_audio.mp3"
                extract_stage2_clip_audio(source_path, p_start, p_end, slice_mp3)
                # Charge the daily quota only after the source and bounded
                # audio slice are known-good; missing sources must never burn
                # the 25 request/day budget.
                if not check_and_increment_daily_transcribe_quota(max_daily_requests=25):
                    logger.info("Drain halted: daily transcribe quota exhausted (25/25 requests reached).")
                    break
                stage2_res = transcribe_stage2_precision_words(slice_mp3, api_key=active_key)

            words = stage2_res.get("words", [])
            if words:
                speech_start = p_start + stage2_res.get("speech_start", 0.0)
                speech_end = p_start + stage2_res.get("speech_end", c_end - p_start)
                snapped_start = max(p_start, round(speech_start - 0.2, 2))
                snapped_end = min(p_end, round(speech_end + 0.3, 2))
                range_trimmed = db.update_clip_trim(
                    clip_id, snapped_start, snapped_end,
                    expected_range_revision=range_revision,
                )
                if range_trimmed:
                    c_start = snapped_start
                    c_end = snapped_end
                latest = db.get_clip_by_id(clip_id)
                if not latest or abs(float(latest["start_time"]) - c_start) > 0.01 or abs(float(latest["end_time"]) - c_end) > 0.01:
                    logger.info("Skipping stale Stage 2 result for %s after its range changed", clip_id)
                    continue
                range_revision = int(latest.get("range_revision") or 0)
                if not range_trimmed:
                    logger.info(
                        "Keeping original clip range for %s because the speech-tightened range "
                        "is outside source or product limits.", clip_id
                    )

                clean_words = []
                for w in words:
                    w_s = round(p_start + w.get("start", 0.0), 3)
                    w_e = round(p_start + w.get("end", 0.0), 3)
                    if w_e > c_start and w_s < c_end:
                        clean_words.append({
                            "word": w.get("word", ""),
                            "start": w_s,
                            "end": w_e
                        })

                track = group_words_into_captions(
                    raw_segments=[{"id": 0, "start": c_start, "end": c_end, "words": clean_words}],
                    clip_start=c_start,
                    clip_end=c_end,
                    clip_id=clip_id,
                    preset_id=get_default_preset_id()
                )
                if not db.save_clip_caption_data(
                    clip_id, json.dumps(track.to_dict()), expected_range_revision=range_revision,
                    source_range_start=c_start, source_range_end=c_end,
                ):
                    logger.info("Skipping stale caption save for %s after its range changed", clip_id)
                    continue

            render_clip(
                source_video=source_path,
                clip_id=clip_id,
                start_time=c_start,
                end_time=c_end,
                aspect_ratio=None,
                layout_mode=clip.get("layout_mode", "fit_blur"),
                subtitle_options={"enabled": False, "preserve_timing": True},
                expected_range_revision=range_revision,
            )

            with db.get_db_connection() as conn:
                conn.cursor().execute("UPDATE clips SET status = 'ready_review' WHERE id = ?", (clip_id,))
                conn.commit()

            drained += 1
            logger.info("Successfully drained queued clip %s -> ready_review", clip_id)

        except Exception as e:
            logger.error("Failed to drain queued clip %s: %s", clip_id, e)

    return drained

