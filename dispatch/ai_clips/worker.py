"""Pillar 3: Autonomous Highlight Selection Worker for YouTube Inbox.
Consumes records in youtube_inbox with status = 'TRANSCRIBED',
calls Gemini semantic highlight detection with live verified models and prompt conditioning,
validates and deduplicates candidates, and transitions state:
  TRANSCRIBED -> ANALYZING -> CLIPS_DEFINED.
"""
import os
import json
import time
import logging
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple

from dispatch import db
from dispatch.config import DEFAULT_PUBLISH_MODE, GEMINI_API_KEY, PROCESSING_DIR
from dispatch.video_engine.renderer import render_clip
from dispatch.captions import group_words_into_captions, export_to_ass
from dispatch.ai_clips.highlight_finder import (
    call_gemini_generate_content,
    validate_and_filter_candidates,
    transliterate_segments_to_hinglish,
    MAX_HIGHLIGHTS_PER_CHUNK,
)
from dispatch.ai_clips.prompt_templates import HIGHLIGHT_SYSTEM_PROMPT, build_highlight_user_prompt
from dispatch.ai_clips.preference_learner import get_negative_feedback_prompt

logger = logging.getLogger("dispatch.ai_clips.worker")


def _parse_iso_or_sqlite_timestamp(ts_str: Optional[str]) -> Optional[datetime]:
    """Parse timestamp string from SQLite (YYYY-MM-DD HH:MM:SS or ISO-8601)."""
    if not ts_str:
        return None
    clean_ts = ts_str.strip().replace(" ", "T")
    try:
        dt = datetime.fromisoformat(clean_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            dt = datetime.strptime(ts_str.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


class HighlightWorker:
    """Standalone worker for Pillar 3 highlight selection contract.
    
    States:
      TRANSCRIBED -> ANALYZING -> CLIPS_DEFINED
    """

    def __init__(
        self,
        poll_interval_seconds: Optional[int] = None,
        max_attempts: int = 5,
        base_backoff_seconds: int = 30
    ):
        env_interval = os.getenv("DISPATCH_HIGHLIGHT_POLL_INTERVAL")
        self.poll_interval = poll_interval_seconds or (int(env_interval) if env_interval and env_interval.isdigit() else 15)
        self.max_attempts = max_attempts
        self.base_backoff_seconds = base_backoff_seconds

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self):
        """Start the background highlight worker thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("Highlight worker is already running.")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="HighlightWorkerThread", daemon=True)
        self._thread.start()
        logger.info(
            "Highlight worker started (Poll interval: %ds, Max attempts: %d)",
            self.poll_interval, self.max_attempts
        )

    def stop(self):
        """Signal worker to stop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            logger.info("Highlight worker stopped.")

    def _run_loop(self):
        """Periodic loop scanning for TRANSCRIBED records."""
        try:
            self.process_pending()
        except Exception as e:
            logger.error("Initial highlight scan error: %s", e)

        while not self._stop_event.is_set():
            if self._stop_event.wait(timeout=self.poll_interval):
                break
            try:
                self.process_pending()
            except Exception as e:
                logger.error("Error in highlight worker cycle: %s", e)

    def process_pending(self) -> List[Dict[str, Any]]:
        """Scan DB for items requiring highlight extraction or unrendered clips."""
        with self._lock:
            processed = []
            pending_items = db.list_youtube_inbox(status="TRANSCRIBED", limit=20)
            now_dt = datetime.now(timezone.utc)

            for item in pending_items:
                video_id = item["video_id"]
                next_attempt_str = item.get("next_ai_attempt_at")
                if next_attempt_str:
                    next_attempt_dt = _parse_iso_or_sqlite_timestamp(next_attempt_str)
                    if next_attempt_dt and now_dt < next_attempt_dt:
                        wait_sec = (next_attempt_dt - now_dt).total_seconds()
                        logger.debug("Skipping %s for highlight analysis: backoff active (%.1fs remaining)",
                                     video_id, wait_sec)
                        continue

                try:
                    result = self.process_video_highlights(video_id)
                    if result:
                        processed.append(result)
                except Exception as e:
                    logger.error("Error processing highlights for %s: %s", video_id, e)

            # Also verify and auto-render any existing CLIPS_DEFINED items
            defined_items = db.list_youtube_inbox(status="CLIPS_DEFINED", limit=10)
            for item in defined_items:
                video_id = item["video_id"]
                try:
                    chunk_id, session_id, segments, duration = self._resolve_chunk_and_transcript(video_id, item)
                    with db.get_db_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT id FROM clips WHERE chunk_id = ?", (chunk_id,))
                        rows = cursor.fetchall()
                        clip_ids = [r["id"] for r in rows] if rows else []

                    local_path_str = item.get("local_video_path")
                    src_path = Path(local_path_str) if local_path_str else None
                    if not src_path or not src_path.exists():
                        if chunk_id:
                            chk = db.get_chunk_by_id(chunk_id)
                            if chk and chk.get("filepath"):
                                cp = Path(chk["filepath"])
                                if cp.exists():
                                    src_path = cp

                    self._render_clips_for_video(video_id, clip_ids, segments, src_path)
                    db.update_youtube_video(video_id=video_id, status="COMPLETED")
                except Exception as e:
                    logger.error("Error rendering CLIPS_DEFINED item %s: %s", video_id, e)

            return processed

    def _resolve_chunk_and_transcript(self, video_id: str, item: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], List[Dict[str, Any]], float]:
        """Resolve chunk_id, session_id, transcript segments, and duration."""
        file_hash = f"yt_{video_id}"
        chunk = db.get_chunk_by_file_hash(file_hash)
        chunk_id = chunk["id"] if chunk else None
        session_id = (chunk.get("session_id") if chunk else None) or item.get("session_id")
        duration = float(item.get("duration", 0.0) or 0.0)

        if chunk and chunk.get("duration"):
            duration = float(chunk["duration"])

        # Try loading segments from item first
        segments: List[Dict[str, Any]] = []
        raw_seg = item.get("segments_json")
        if raw_seg:
            try:
                segments = json.loads(raw_seg)
            except Exception:
                segments = []

        # If not found or empty, try from transcripts table
        if not segments and chunk_id:
            with db.get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT segments_json FROM transcripts WHERE chunk_id = ?", (chunk_id,))
                t_row = cursor.fetchone()
                if t_row and t_row["segments_json"]:
                    try:
                        segments = json.loads(t_row["segments_json"])
                    except Exception:
                        segments = []

        return chunk_id, session_id, segments, duration

    def _render_clips_for_video(
        self,
        video_id: str,
        saved_ids: List[str],
        segments: List[Dict[str, Any]],
        source_video_path: Optional[Path]
    ):
        """Render candidate clips with Roman Hinglish subtitles and FFmpeg."""
        if not saved_ids:
            return

        for clip_id in saved_ids:
            clip = db.get_clip_by_id(clip_id)
            if not clip:
                continue

            c_start = float(clip["start_time"])
            c_end = float(clip["end_time"])
            c_layout = clip.get("layout_mode", "fit_blur")

            # Check if clip is already rendered and non-empty
            if clip.get("video_path"):
                vp = Path(clip["video_path"])
                if vp.exists() and vp.stat().st_size > 0:
                    logger.debug("Clip %s already rendered (%s). Skipping duplicate render.", clip_id, vp.name)
                    continue

            # Filter segments covering this clip
            clip_segs = [s for s in segments if s.get("end", 0.0) > c_start and s.get("start", 0.0) < c_end]
            clean_segs = transliterate_segments_to_hinglish(clip_segs)

            # Generate and cache canonical CaptionTrack
            try:
                track = group_words_into_captions(
                    raw_segments=clean_segs,
                    clip_start=c_start,
                    clip_end=c_end,
                    clip_id=clip_id,
                    preset_id="yellow_pop"
                )
                db.save_clip_caption_data(clip_id, json.dumps(track.to_dict()))
                ass_path = PROCESSING_DIR / f"{clip_id}.ass"
                export_to_ass(track, ass_path)
            except Exception as e:
                logger.warning("Failed to prepare captions for clip %s: %s", clip_id, e)

            # FFmpeg render
            if source_video_path and source_video_path.exists():
                try:
                    logger.info("Auto-rendering clip %s (%.1fs - %.1fs) with FFmpeg...", clip_id, c_start, c_end)
                    render_clip(
                        source_video=source_video_path,
                        clip_id=clip_id,
                        start_time=c_start,
                        end_time=c_end,
                        aspect_ratio=None,
                        layout_mode=c_layout,
                        segments=clean_segs
                    )
                except Exception as e:
                    logger.error("Auto-render failed for clip %s: %s", clip_id, e)
            else:
                logger.warning("Source video file not found on disk for auto-rendering clip %s", clip_id)

    def process_video_highlights(self, video_id: str) -> Optional[Dict[str, Any]]:
        """Run Pillar 3 highlight analysis pipeline for a single video.
        
        Transitions:
          1. TRANSCRIBED -> ANALYZING
          2. Zero-speech: marks CLIPS_DEFINED with 0 clips directly.
          3. Call Gemini (strictly fails on error, never silent heuristic).
          4. Candidate validation and deduplication.
          5. Save clip definitions, transliterate to Roman Hinglish, and auto-render with FFmpeg -> COMPLETED.
        """
        item = db.get_youtube_video(video_id)
        if not item:
            logger.warning("Video %s not found in inbox. Skipping.", video_id)
            return None

        if item.get("status") not in ("TRANSCRIBED", "CLIPS_DEFINED"):
            logger.info("Video %s is in status '%s', expected 'TRANSCRIBED' or 'CLIPS_DEFINED'. Skipping.",
                        video_id, item.get("status"))
            return None

        chunk_id, session_id, segments, duration = self._resolve_chunk_and_transcript(video_id, item)

        # Transition to ANALYZING
        db.update_youtube_video(video_id=video_id, status="ANALYZING")
        logger.info("Transitioned %s to ANALYZING", video_id)

        # Handle zero-speech / empty transcript immediately
        if not segments or not any(s.get("text", "").strip() for s in segments):
            logger.info("No speech detected for %s. Marking COMPLETED with 0 clips.", video_id)
            db.save_clip_definitions(
                chunk_id=chunk_id,
                session_id=session_id,
                video_id=video_id,
                clip_defs=[]
            )
            db.update_youtube_video(video_id=video_id, status="COMPLETED")
            return {
                "video_id": video_id,
                "status": "COMPLETED",
                "clips_count": 0,
                "model_used": None
            }

        # Format transcript lines with timestamps and pause markers for LLM
        transcript_lines = []
        prev_end = 0.0
        for s in segments:
            s_start = float(s.get("start", 0.0))
            s_end = float(s.get("end", 0.0))
            if prev_end > 0.0:
                pause_gap = s_start - prev_end
                if pause_gap >= 0.5:
                    transcript_lines.append(f"[PAUSE: {pause_gap:.1f}s]")

            start_m, start_s = divmod(s_start, 60)
            end_m, end_s = divmod(s_end, 60)
            transcript_lines.append(f"[{int(start_m):02d}:{start_s:05.2f} -> {int(end_m):02d}:{end_s:05.2f}] {s.get('text', '')}")
            prev_end = max(prev_end, s_end)

        transcript_text = "\n".join(transcript_lines)

        negative_context = get_negative_feedback_prompt()
        user_prompt = build_highlight_user_prompt(transcript_text, negative_context)

        attempt_count = int(item.get("ai_attempt_count") or 0) + 1

        try:
            # Strictly call Gemini API: NO fallback to extract_clips_local_heuristic
            raw_clips, model_used = call_gemini_generate_content(user_prompt)

            # Validate, filter, snap, clamp, and deduplicate
            validated_clips = validate_and_filter_candidates(
                raw_candidates=raw_clips,
                segments=segments,
                total_duration=duration,
                max_clips=MAX_HIGHLIGHTS_PER_CHUNK
            )

            publish_mode = db.get_setting("publish_mode", DEFAULT_PUBLISH_MODE)

            # Save clip definitions atomically and transition to CLIPS_DEFINED
            saved_ids = db.save_clip_definitions(
                chunk_id=chunk_id,
                session_id=session_id,
                video_id=video_id,
                clip_defs=validated_clips,
                publish_mode=publish_mode
            )

            # Resolve source video file path for auto-rendering
            local_path_str = item.get("local_video_path")
            src_path = Path(local_path_str) if local_path_str else None
            if not src_path or not src_path.exists():
                if chunk_id:
                    chk = db.get_chunk_by_id(chunk_id)
                    if chk and chk.get("filepath"):
                        cp = Path(chk["filepath"])
                        if cp.exists():
                            src_path = cp

            # Automatic FFmpeg rendering with Roman Hinglish subtitles
            self._render_clips_for_video(video_id, saved_ids, segments, src_path)

            # Reset error fields on success and transition to COMPLETED
            db.update_youtube_video(
                video_id=video_id,
                status="COMPLETED",
                ai_attempt_count=0,
                next_ai_attempt_at="",
                last_ai_error=""
            )

            logger.info(
                "Pillar 3 Highlight SUCCESS for %s -> COMPLETED (Model: %s, Raw: %d, Accepted/Rendered: %d, ClipIDs: %s)",
                video_id, model_used, len(raw_clips), len(saved_ids), saved_ids
            )

            return {
                "video_id": video_id,
                "status": "COMPLETED",
                "clips_count": len(saved_ids),
                "clip_ids": saved_ids,
                "model_used": model_used
            }

        except Exception as e:
            err_msg = str(e)
            logger.error("Gemini highlight extraction failed for %s (attempt %d/%d): %s",
                         video_id, attempt_count, self.max_attempts, err_msg)

            # Determine whether error is permanent or retryable
            is_permanent = (
                not GEMINI_API_KEY
                or "API_KEY" in err_msg.upper()
                or "AUTHENTICATION FAILED" in err_msg.upper()
                or attempt_count >= self.max_attempts
            )

            if is_permanent:
                logger.critical("Permanent failure for %s in Pillar 3: %s", video_id, err_msg)
                db.update_youtube_video(
                    video_id=video_id,
                    status="FAILED",
                    ai_attempt_count=attempt_count,
                    last_ai_error=err_msg
                )
            else:
                backoff_sec = self.base_backoff_seconds * (2 ** (attempt_count - 1))
                next_dt = datetime.now(timezone.utc) + timedelta(seconds=backoff_sec)
                logger.warning("Scheduling retry for %s in %ds (at %s)",
                               video_id, backoff_sec, next_dt.strftime("%Y-%m-%d %H:%M:%S"))
                db.update_youtube_video(
                    video_id=video_id,
                    status="TRANSCRIBED",  # reset to TRANSCRIBED for future cycle
                    ai_attempt_count=attempt_count,
                    next_ai_attempt_at=next_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    last_ai_error=err_msg
                )

            raise
