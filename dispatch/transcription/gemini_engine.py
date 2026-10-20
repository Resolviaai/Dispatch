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
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from dispatch import db
from dispatch.config import GEMINI_API_KEY
from dispatch.transcription.audio import extract_compressed_audio

logger = logging.getLogger("dispatch.transcription.gemini")


def get_gemini_client():
    """Lazy initialize and return Google GenAI Client with active API key."""
    api_key = os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not configured in environment or .env")
    from google import genai
    return genai.Client(api_key=api_key)


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
    """Check if gemini-3.5-transcribe is within daily limit, and increment on approval.
    
    Returns True if quota is available, False if daily limit is reached.
    """
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    current_date = db.get_setting("transcribe_quota_date", "")
    current_count = int(db.get_setting("transcribe_quota_count", "0") or 0)

    if current_date != today_utc:
        # Reset counter for new day
        db.set_setting("transcribe_quota_date", today_utc)
        db.set_setting("transcribe_quota_count", "1")
        return True

    if current_count >= max_daily_requests:
        logger.warning(
            "gemini-3.5-transcribe daily quota reached (%d/%d requests for %s). Overflow guard active.",
            current_count, max_daily_requests, today_utc
        )
        return False

    db.set_setting("transcribe_quota_count", str(current_count + 1))
    return True


def transcribe_stage1_flash_lite(
    media_path: Path,
    prompt: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Stage 1: Coarse sentence-level transcription using gemini-3.5-flash-lite.
    
    Args:
        media_path: Local path to .mp4 video or .mp3 audio file.
        prompt: Optional custom transcription prompt.
        
    Returns:
        List of segment dicts: [{'start': float, 'end': float, 'text': str, 'speaker': str}]
    """
    client = get_gemini_client()
    from google.genai import types

    # Extract lightweight MP3 if passed a video
    if media_path.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm"):
        audio_path = extract_compressed_audio(media_path)
        cleanup_audio = True
    else:
        audio_path = media_path
        cleanup_audio = False

    try:
        logger.info("Stage 1: Uploading %s to Gemini Files API...", audio_path.name)
        uploaded_file = client.files.upload(file=str(audio_path), config=dict(mime_type="audio/mp3"))

        system_instruction = (
            "You are an expert audio transcription engine. "
            "Transcribe the spoken audio verbatim in original language (detect Hindi, English, Hinglish). "
            "Provide precise timestamps for each spoken segment/phrase in valid JSON format: "
            '[{"start": "00:00.00", "end": "00:02.50", "text": "...", "speaker": "Speaker 1"}]. '
            "Return ONLY the JSON array."
        )

        logger.info("Stage 1: Calling gemini-3.5-flash-lite...")
        t0 = time.time()
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=[uploaded_file, prompt or system_instruction],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0
            )
        )
        elapsed = time.time() - t0
        logger.info("Stage 1 completed in %.2fs", elapsed)

        raw_text = response.text or "[]"
        segments_raw = json.loads(raw_text)

        # Normalize segment structures to float start/end seconds
        normalized_segments: List[Dict[str, Any]] = []
        for s in segments_raw:
            st = s.get("start", 0.0)
            en = s.get("end", 0.0)

            # Convert mm:ss.ms string or float to float seconds
            if isinstance(st, str):
                parts = st.split(":")
                if len(parts) == 2:
                    st = float(parts[0]) * 60 + float(parts[1])
                else:
                    st = _parse_offset_to_seconds(st)
            if isinstance(en, str):
                parts = en.split(":")
                if len(parts) == 2:
                    en = float(parts[0]) * 60 + float(parts[1])
                else:
                    en = _parse_offset_to_seconds(en)

            normalized_segments.append({
                "start": float(st),
                "end": float(en),
                "text": str(s.get("text", "")).strip(),
                "speaker": str(s.get("speaker", "spk:0")).strip()
            })

        # Cleanup remote cloud file
        try:
            client.files.delete(name=uploaded_file.name)
        except Exception:
            pass

        return normalized_segments

    finally:
        if cleanup_audio and audio_path.exists() and audio_path != media_path:
            try:
                audio_path.unlink()
            except Exception:
                pass


def transcribe_stage2_precision_words(
    media_path: Path
) -> Dict[str, Any]:
    """Stage 2: Precision word-level alignment using gemini-3.5-transcribe.
    
    Args:
        media_path: Local path to sliced 30-60s clip video or audio file.
        
    Returns:
        Dict with:
          - full_text: complete verbatim transcription
          - words: list of [{'word': str, 'speaker': str, 'start': float, 'end': float}]
          - speech_start: float timestamp of first spoken word
          - speech_end: float timestamp of last spoken word
    """
    client = get_gemini_client()
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


def drain_queued_transcriptions(max_batch: int = 10) -> int:
    """FIFO Rolling Backlog Auto-Drain.
    
    Checks if quota is available today, and drains yesterday's leftover
    queued_transcription clips first before processing new incoming recordings.
    """
    queued_clips = db.get_queued_transcription_clips(limit=max_batch)
    if not queued_clips:
        return 0

    from dispatch.captions.grouper import group_words_into_captions
    from dispatch.video_engine.renderer import render_clip
    import subprocess

    drained = 0
    for clip in queued_clips:
        clip_id = clip["id"]
        if not check_and_increment_daily_transcribe_quota(max_daily_requests=25):
            logger.info("Drain halted: daily transcribe quota exhausted (25/25 requests reached).")
            break

        chunk_id = clip.get("chunk_id")
        chunk = db.get_chunk_by_id(chunk_id) if chunk_id else None
        source_path = Path(chunk["filepath"]) if chunk and chunk.get("filepath") else None

        if not source_path or not source_path.exists():
            logger.warning("Cannot drain queued clip %s: source video not found on disk", clip_id)
            continue

        try:
            c_start = float(clip["start_time"])
            c_end = float(clip["end_time"])
            p_start = float(clip["padded_start"]) if clip.get("padded_start") is not None else max(0.0, c_start - 4.0)
            p_end = float(clip["padded_end"]) if clip.get("padded_end") is not None else (c_end + 4.0)

            from dispatch.config import PROCESSING_DIR
            slice_mp3 = PROCESSING_DIR / f"{clip_id}_slice.mp3"
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(round(p_start, 3)),
                "-to", str(round(p_end, 3)),
                "-i", str(source_path),
                "-vn", "-acodec", "libmp3lame", "-b:a", "96k",
                str(slice_mp3)
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)

            if slice_mp3.exists() and slice_mp3.stat().st_size > 0:
                stage2_res = transcribe_stage2_precision_words(slice_mp3)
                try:
                    slice_mp3.unlink()
                except Exception:
                    pass
            else:
                stage2_res = transcribe_stage2_precision_words(source_path)

            words = stage2_res.get("words", [])
            if words:
                speech_start = p_start + stage2_res.get("speech_start", 0.0)
                speech_end = p_start + stage2_res.get("speech_end", c_end - p_start)
                snapped_start = max(p_start, round(speech_start - 0.2, 2))
                snapped_end = min(p_end, round(speech_end + 0.3, 2))
                db.update_clip_trim(clip_id, snapped_start, snapped_end)
                c_start = snapped_start
                c_end = snapped_end

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
                    preset_id="yellow_pop"
                )
                db.save_clip_caption_data(clip_id, json.dumps(track.to_dict()))

            render_clip(
                source_video=source_path,
                clip_id=clip_id,
                start_time=c_start,
                end_time=c_end,
                aspect_ratio=None,
                layout_mode=clip.get("layout_mode", "fit_blur")
            )

            with db.get_db_connection() as conn:
                conn.cursor().execute("UPDATE clips SET status = 'ready_review' WHERE id = ?", (clip_id,))
                conn.commit()

            drained += 1
            logger.info("Successfully drained queued clip %s -> ready_review", clip_id)

        except Exception as e:
            logger.error("Failed to drain queued clip %s: %s", clip_id, e)

    return drained

