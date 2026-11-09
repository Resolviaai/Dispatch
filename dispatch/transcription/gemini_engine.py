"""Gemini Transcription Engine for Dispatch V2.

Canonical transcription contract:
- Primary: gemini-3.5-transcribe (full transcript generation, word timestamps, diarization)
- Fallback: gemini-3.5-flash-lite (verbatim phrase/segment transcription, timestamp normalization)
- Zero local Whisper fallback. Zero YouTube captions fallback.
- Quota tracking & safety guard (daily limit for transcribe model).
- Strict error classification (PermanentAuthError vs RecoverableTranscriptionError).
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
from dispatch.config import GEMINI_API_KEY, MAX_CLIP_DURATION, PROCESSING_DIR
from dispatch.transcription.audio import (
    _probe_audio_duration,
    extract_compressed_audio,
    split_audio_for_transcription,
)

logger = logging.getLogger("dispatch.transcription.gemini")

# Five seconds preserves ordinary sentence context at ten-minute seams with
# less than 1% additional audio per window.
STAGE1_WINDOW_OVERLAP_SECONDS = 5.0


class PermanentAuthError(RuntimeError):
    """Raised when Gemini API key is missing or permanently invalid (401/403)."""
    pass


class RecoverableTranscriptionError(RuntimeError):
    """Raised when transcription fails for recoverable reasons (429, 503, quota, etc.)."""
    pass


def _safe_parse_json(raw_text: str) -> Any:
    """Safely extracts and parses JSON payload from raw LLM output, handling markdown fences and trailing text."""
    if not raw_text or not raw_text.strip():
        return []
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```"):
            if lines[-1].startswith("```"):
                cleaned = "\n".join(lines[1:-1]).strip()
            else:
                cleaned = "\n".join(lines[1:]).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # Extract bracket bounds for JSON array
    start_bracket = cleaned.find("[")
    end_bracket = cleaned.rfind("]")
    if start_bracket != -1 and end_bracket != -1 and end_bracket > start_bracket:
        try:
            return json.loads(cleaned[start_bracket:end_bracket + 1])
        except Exception:
            pass

    # Fallback to decoder.raw_decode
    decoder = json.JSONDecoder()
    if start_bracket != -1:
        try:
            obj, _ = decoder.raw_decode(cleaned[start_bracket:])
            return obj
        except Exception:
            pass

    start_brace = cleaned.find("{")
    end_brace = cleaned.rfind("}")
    if start_brace != -1 and end_brace != -1 and end_brace > start_brace:
        try:
            return json.loads(cleaned[start_brace:end_brace + 1])
        except Exception:
            pass
    if start_brace != -1:
        try:
            obj, _ = decoder.raw_decode(cleaned[start_brace:])
            return obj
        except Exception:
            pass

    raise RecoverableTranscriptionError(f"gemini-3.5-flash-lite returned unparseable JSON: {cleaned[:160]}")


def is_permanent_auth_error(exc: Exception) -> bool:
    """Check if an exception indicates a permanent authentication or authorization failure."""
    if isinstance(exc, PermanentAuthError):
        return True
    msg = str(exc).lower()
    return any(term in msg for term in [
        "401", "403", "api_key_invalid", "permission_denied",
        "api key not valid", "unauthenticated", "invalid api key"
    ])


def get_gemini_client(api_key: Optional[str] = None):
    """Lazy initialize and return Google GenAI Client with active API key."""
    key = api_key or os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY
    if not key:
        raise PermanentAuthError("GEMINI_API_KEY is not configured in environment or .env")
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


def check_and_increment_daily_transcribe_quota(max_daily_requests: int = 25) -> bool:
    """Check if gemini-3.5-transcribe is within the daily limit and atomically reserve one request."""
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
            return False


def refund_daily_transcribe_quota() -> None:
    """Return a reserved transcribe slot (floor 0) when the request never reached Gemini."""
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE settings SET value = MAX(CAST(value AS INTEGER) - 1, 0) "
            "WHERE key = 'transcribe_quota_count' AND value IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM settings WHERE key = 'transcribe_quota_date' "
            "AND value = ?)",
            (today_utc,)
        )
        conn.commit()


def _group_words_into_segments(words: List[Dict[str, Any]], max_gap_seconds: float = 0.8) -> List[Dict[str, Any]]:
    """Group consecutive words into sentence-level phrase segments."""
    if not words:
        return []
    segments = []
    current_words: List[Dict[str, Any]] = []
    current_start = words[0]["start"]
    current_speaker = words[0].get("speaker", "spk:0")

    for idx, w in enumerate(words):
        w_start = w["start"]
        w_end = w["end"]
        w_word = w["word"].strip()
        w_spk = w.get("speaker", "spk:0")

        # Check segment break conditions
        is_break = False
        if current_words:
            prev_end = current_words[-1]["end"]
            gap = w_start - prev_end
            if gap > max_gap_seconds or w_spk != current_speaker or len(current_words) >= 12:
                is_break = True
            elif current_words[-1]["word"].rstrip().endswith((".", "?", "!")):
                is_break = True

        if is_break and current_words:
            seg_text = " ".join(item["word"] for item in current_words)
            segments.append({
                "id": len(segments),
                "start": current_start,
                "end": current_words[-1]["end"],
                "text": seg_text,
                "speaker": current_speaker,
                "words": current_words,
            })
            current_words = []
            current_start = w_start
            current_speaker = w_spk

        current_words.append({
            "word": w_word,
            "start": w_start,
            "end": w_end,
            "probability": w.get("probability", 1.0),
            "speaker": w_spk,
        })

    if current_words:
        seg_text = " ".join(item["word"] for item in current_words)
        segments.append({
            "id": len(segments),
            "start": current_start,
            "end": current_words[-1]["end"],
            "text": seg_text,
            "speaker": current_speaker,
            "words": current_words,
        })

    return segments


def _transcribe_chunk_primary(
    client: Any,
    audio_part_path: Path,
    prompt: Optional[str] = None
) -> Dict[str, Any]:
    """Primary: Full transcript generation via gemini-3.5-transcribe with word timestamps."""
    from google.genai import types

    if not check_and_increment_daily_transcribe_quota(max_daily_requests=25):
        raise RecoverableTranscriptionError("Daily transcribe quota limit (25/25) reached for gemini-3.5-transcribe")

    uploaded_file = None
    try:
        uploaded_file = client.files.upload(file=str(audio_part_path), config=dict(mime_type="audio/mp3"))
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
        logger.info("Primary gemini-3.5-transcribe completed in %.2fs", time.time() - t0)

        words: List[Dict[str, Any]] = []
        full_text = getattr(response, "text", "") or ""

        for cand in getattr(response, "candidates", []) or []:
            for part in getattr(getattr(cand, "content", None), "parts", []) or []:
                trans = getattr(part, "audio_transcription", None)
                if trans:
                    spk = getattr(trans, "speaker_label", "") or "spk:0"
                    for w in getattr(trans, "words", []) or []:
                        w_start = _parse_offset_to_seconds(getattr(w, "start_offset", 0.0))
                        w_end = _parse_offset_to_seconds(getattr(w, "end_offset", 0.0))
                        words.append({
                            "word": getattr(w, "word", ""),
                            "speaker": spk,
                            "start": w_start,
                            "end": w_end,
                            "probability": 1.0
                        })

        if not words and not full_text:
            refund_daily_transcribe_quota()
            raise RecoverableTranscriptionError("gemini-3.5-transcribe returned empty transcript")

        if not full_text and words:
            full_text = " ".join(w["word"] for w in words)

        segments = _group_words_into_segments(words)
        return {
            "full_text": full_text,
            "segments": segments,
            "words": words,
            "source": "gemini_transcribe",
            "model": "gemini-3.5-transcribe"
        }
    except Exception as exc:
        if is_permanent_auth_error(exc):
            refund_daily_transcribe_quota()
            raise PermanentAuthError(f"Gemini authentication failed on primary: {exc}") from exc
        refund_daily_transcribe_quota()
        raise RecoverableTranscriptionError(f"gemini-3.5-transcribe failed: {exc}") from exc
    finally:
        if uploaded_file is not None:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass


def _transcribe_chunk_fallback(
    client: Any,
    audio_part_path: Path,
    prompt: Optional[str] = None
) -> Dict[str, Any]:
    """Fallback: Transcription via gemini-3.5-flash-lite normalized to canonical schema."""
    from google.genai import types

    uploaded_file = None
    try:
        uploaded_file = client.files.upload(file=str(audio_part_path), config=dict(mime_type="audio/mp3"))
        t0 = time.time()
        system_instruction = prompt or (
            "You are an expert audio transcription engine. "
            "Transcribe the spoken audio verbatim in original language (detect Hindi, English, Hinglish). "
            "Provide precise timestamps for each spoken segment/phrase in valid JSON format: "
            '[{"start": "00:00.00", "end": "00:02.50", "text": "...", "speaker": "Speaker 1"}]. '
            "Return ONLY the JSON array."
        )
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=[uploaded_file, system_instruction],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0,
            ),
        )
        logger.info("Fallback gemini-3.5-flash-lite completed in %.2fs", time.time() - t0)

        raw_segments = _safe_parse_json(getattr(response, "text", "") or "[]")
        if not isinstance(raw_segments, list):
            raise RecoverableTranscriptionError("gemini-3.5-flash-lite returned non-list transcript")

        segments: List[Dict[str, Any]] = []
        all_words: List[Dict[str, Any]] = []
        full_text_pieces = []

        for idx, seg in enumerate(raw_segments):
            if not isinstance(seg, dict):
                continue
            st = _parse_stage1_time(seg.get("start", 0.0))
            en = _parse_stage1_time(seg.get("end", 0.0))
            if en < st:
                continue
            text = str(seg.get("text") or seg.get("word") or "").strip()
            spk = str(seg.get("speaker", "spk:0")).strip()
            if text:
                full_text_pieces.append(text)

            seg_words = []
            if "word" in seg and "text" not in seg:
                w_item = {
                    "word": text,
                    "start": st,
                    "end": en,
                    "probability": float(seg.get("probability", 0.9)),
                    "speaker": spk,
                }
                seg_words.append(w_item)
                all_words.append(w_item)
            elif "words" in seg and isinstance(seg["words"], list):
                for w in seg["words"]:
                    if isinstance(w, dict):
                        w_item = {
                            "word": str(w.get("word", "")),
                            "start": _parse_stage1_time(w.get("start", st)),
                            "end": _parse_stage1_time(w.get("end", en)),
                            "probability": float(w.get("probability", 0.9)),
                            "speaker": str(w.get("speaker", spk)),
                        }
                        seg_words.append(w_item)
                        all_words.append(w_item)
            else:
                tokens = text.split()
                if tokens:
                    duration = max(0.05, en - st)
                    step = duration / len(tokens)
                    for w_idx, tok in enumerate(tokens):
                        w_st = round(st + w_idx * step, 3)
                        w_en = round(st + (w_idx + 1) * step, 3)
                        seg_words.append({
                            "word": tok,
                            "start": w_st,
                            "end": w_en,
                            "probability": 0.9,
                            "speaker": spk,
                        })
                        all_words.append(seg_words[-1])

            segments.append({
                "id": idx,
                "start": st,
                "end": en,
                "text": text,
                "speaker": spk,
                "words": seg_words,
            })

        full_text = " ".join(full_text_pieces)
        return {
            "full_text": full_text,
            "segments": segments,
            "words": all_words,
            "source": "gemini_flash_lite",
            "model": "gemini-3.5-flash-lite"
        }
    except Exception as exc:
        if is_permanent_auth_error(exc):
            raise PermanentAuthError(f"Gemini authentication failed on fallback: {exc}") from exc
        raise RecoverableTranscriptionError(f"gemini-3.5-flash-lite failed: {exc}") from exc
    finally:
        if uploaded_file is not None:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass


def transcribe_media(
    media_path: Path,
    api_key: Optional[str] = None,
    prompt: Optional[str] = None,
    language: Optional[str] = None,
) -> Dict[str, Any]:
    """Single canonical transcription interface for Dispatch.
    
    Tries PRIMARY (gemini-3.5-transcribe) first.
    If recoverable failure, falls back to FALLBACK (gemini-3.5-flash-lite).
    Zero Whisper fallback. Zero YouTube captions fallback.
    
    Returns:
        Dict with 'full_text', 'segments' (with word timings), 'source', 'model'.
    """
    if not media_path.exists():
        raise FileNotFoundError(f"Media file not found: {media_path}")

    client = get_gemini_client(api_key=api_key)
    is_video = media_path.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm")

    with tempfile.TemporaryDirectory(prefix="dispatch_transcribe_") as work_dir:
        work_path = Path(work_dir)
        audio_path = (
            extract_compressed_audio(media_path, work_path / "source.mp3")
            if is_video else media_path
        )
        chunks = split_audio_for_transcription(
            audio_path, work_path / "parts", chunk_seconds=600,
            overlap_seconds=STAGE1_WINDOW_OVERLAP_SECONDS,
        )
        logger.info("Transcribing %s as %d bounded audio part(s) using Gemini", media_path.name, len(chunks))

        all_segments: List[Dict[str, Any]] = []
        full_text_pieces: List[str] = []
        effective_source = "gemini_transcribe"
        effective_model = "gemini-3.5-transcribe"

        for part_index, (part_path, source_offset) in enumerate(chunks):
            chunk_result = None

            # 1. Attempt Primary: gemini-3.5-transcribe
            try:
                logger.info("Part %d/%d: Calling primary gemini-3.5-transcribe...", part_index + 1, len(chunks))
                chunk_result = _transcribe_chunk_primary(client, part_path, prompt=prompt)
            except PermanentAuthError:
                raise
            except RecoverableTranscriptionError as primary_err:
                logger.warning("Part %d/%d: Primary failed (%s). Attempting fallback gemini-3.5-flash-lite...",
                               part_index + 1, len(chunks), primary_err)
                # 2. Attempt Fallback: gemini-3.5-flash-lite
                try:
                    chunk_result = _transcribe_chunk_fallback(client, part_path, prompt=prompt)
                    effective_source = "gemini_flash_lite"
                    effective_model = "gemini-3.5-flash-lite"
                except PermanentAuthError:
                    raise
                except RecoverableTranscriptionError as fallback_err:
                    logger.error("Part %d/%d: Fallback also failed: %s", part_index + 1, len(chunks), fallback_err)
                    raise RecoverableTranscriptionError(
                        f"Both Gemini transcription providers failed for part {part_index + 1}: "
                        f"Primary: {primary_err} | Fallback: {fallback_err}"
                    ) from fallback_err

            if not chunk_result:
                raise RecoverableTranscriptionError(f"No transcription produced for part {part_index + 1}")

            if chunk_result.get("full_text"):
                full_text_pieces.append(chunk_result["full_text"])

            for seg in chunk_result.get("segments", []):
                seg_copy = dict(seg)
                seg_copy["start"] = round(seg_copy["start"] + source_offset, 3)
                seg_copy["end"] = round(seg_copy["end"] + source_offset, 3)
                seg_copy["_source_part_index"] = part_index
                if "words" in seg_copy:
                    seg_copy["words"] = [
                        {
                            **w,
                            "start": round(w["start"] + source_offset, 3),
                            "end": round(w["end"] + source_offset, 3),
                        }
                        for w in seg_copy["words"]
                    ]
                all_segments.append(seg_copy)

    # Deduplicate overlapping segments at part seams
    deduped_segments = _deduplicate_overlapping_segments(
        all_segments, overlap_seconds=STAGE1_WINDOW_OVERLAP_SECONDS
    )

    full_text = " ".join(full_text_pieces).strip()
    if not full_text and deduped_segments:
        full_text = " ".join(s["text"] for s in deduped_segments)

    # Re-index segments sequentially
    for i, s in enumerate(deduped_segments):
        s["id"] = i

    return {
        "full_text": full_text,
        "segments": deduped_segments,
        "source": effective_source,
        "model": effective_model,
        "language": language or "auto",
    }


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
            if "words" in segment and "words" in duplicate:
                if len(segment["words"]) > len(duplicate["words"]):
                    duplicate["words"] = segment["words"]

    return [
        {key: value for key, value in segment.items() if key != "_source_part_index"}
        for segment in kept
    ]


# Backward compatibility aliases for existing callers/tests
def transcribe_stage1_flash_lite(
    media_path: Path,
    prompt: Optional[str] = None,
    api_key: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Backward compatibility helper for coarse transcription (uses fallback flash-lite)."""
    client = get_gemini_client(api_key=api_key)
    is_video = media_path.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm")
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
        all_segments = []
        for part_index, (part_path, source_offset) in enumerate(chunks):
            res = _transcribe_chunk_fallback(client, part_path, prompt=prompt)
            for seg in res.get("segments", []):
                seg_copy = dict(seg)
                seg_copy["start"] = round(seg_copy["start"] + source_offset, 3)
                seg_copy["end"] = round(seg_copy["end"] + source_offset, 3)
                seg_copy["_source_part_index"] = part_index
                all_segments.append(seg_copy)
    return _deduplicate_overlapping_segments(all_segments, overlap_seconds=STAGE1_WINDOW_OVERLAP_SECONDS)


def transcribe_stage2_precision_words(
    media_path: Path,
    max_input_seconds: Optional[float] = None,
    api_key: Optional[str] = None
) -> Dict[str, Any]:
    """Precision word-level alignment for cut clip audio (primary: transcribe, fallback: flash-lite)."""
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
    is_video = media_path.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm")
    if is_video:
        audio_path = extract_compressed_audio(media_path)
        cleanup = True
    else:
        audio_path = media_path
        cleanup = False

    try:
        try:
            res = _transcribe_chunk_primary(client, audio_path)
        except RecoverableTranscriptionError:
            res = _transcribe_chunk_fallback(client, audio_path)

        words = res.get("words", [])
        return {
            "full_text": res.get("full_text", ""),
            "words": words,
            "speech_start": words[0]["start"] if words else 0.0,
            "speech_end": words[-1]["end"] if words else 0.0,
            "source": res.get("source", "gemini_transcribe"),
            "model": res.get("model", "gemini-3.5-transcribe"),
        }
    finally:
        if cleanup and audio_path.exists() and audio_path != media_path:
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
    """FIFO Rolling Backlog Auto-Drain."""
    queued_clips = db.get_queued_transcription_clips(limit=max_batch)
    if not queued_clips:
        return 0

    from dispatch.captions.grouper import group_words_into_captions, apply_template_to_track
    from dispatch.captions.presets import get_default_preset_id
    from dispatch.video_engine.renderer import render_clip

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
            active_key = active_key or os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY

            PROCESSING_DIR.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=f"dispatch_stage2_{clip_id}_", dir=str(PROCESSING_DIR)) as temp_dir:
                slice_mp3 = Path(temp_dir) / "clip_audio.mp3"
                extract_stage2_clip_audio(source_path, p_start, p_end, slice_mp3)
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

                from dispatch.ai_clips.highlight_finder import transliterate_segments_to_hinglish
                raw_stage2_segments = [{"id": 0, "start": c_start, "end": c_end, "words": clean_words}]
                clean_stage2_segments = transliterate_segments_to_hinglish(raw_stage2_segments)
                if clean_stage2_segments and clean_stage2_segments[0].get("words"):
                    clean_words = clean_stage2_segments[0]["words"]

                user_tpl = db.get_default_template(user_id=clip.get("user_id"))
                tpl_captions = user_tpl.get("captions", {}) if isinstance(user_tpl, dict) else {}
                preset_cand = tpl_captions.get("preset_id")
                effective_preset_id = preset_cand if (preset_cand and preset_cand != "custom") else get_default_preset_id()

                track = group_words_into_captions(
                    raw_segments=[{"id": 0, "start": c_start, "end": c_end, "words": clean_words}],
                    clip_start=c_start,
                    clip_end=c_end,
                    clip_id=clip_id,
                    preset_id=effective_preset_id
                )
                apply_template_to_track(track, tpl_captions)
                track_dict = track.to_dict()
                track_dict["words"] = clean_words
                if not db.save_clip_caption_data(
                    clip_id, json.dumps(track_dict), expected_range_revision=range_revision,
                    source_range_start=c_start, source_range_end=c_end,
                ):
                    logger.info("Skipping stale caption save for %s after its range changed", clip_id)
                    continue

            user_tpl = db.get_default_template(user_id=clip.get("user_id"))
            tpl_layout = user_tpl.get("framing", {}).get("horizontal_mode") or "crop_916"
            c_layout = clip.get("layout_mode") or tpl_layout

            render_clip(
                source_video=source_path,
                clip_id=clip_id,
                start_time=c_start,
                end_time=c_end,
                aspect_ratio=None,
                layout_mode=c_layout,
                segments=clean_words if words else None,
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
