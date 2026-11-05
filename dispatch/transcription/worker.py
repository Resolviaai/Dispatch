"""Pillar 2: Autonomous Transcription Worker for YouTube Inbox and Direct Uploads.

Consumes items in status 'DOWNLOADED' and 'WAITING_FOR_TRANSCRIPT',
running canonical Gemini transcription (Primary: 3.5-transcribe, Fallback: 3.5-flash-lite).
Zero local Whisper fallback. Zero YouTube captions fallback.
"""
import os
import json
import time
import logging
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from dispatch import db
from dispatch.youtube_inbox.catcher import YouTubeInboxCatcher
from dispatch.transcription.gemini_engine import (
    transcribe_media,
    PermanentAuthError,
    RecoverableTranscriptionError,
)

logger = logging.getLogger("dispatch.transcription.worker")


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


class TranscriptionWorker:
    """Standalone worker for Pillar 2 transcription contract.
    
    States:
      DOWNLOADED -> TRANSCRIBING -> TRANSCRIBED (source: 'gemini_transcribe' or 'gemini_flash_lite')
    """

    def __init__(
        self,
        poll_interval_seconds: Optional[int] = None,
        caption_wait_seconds: Optional[int] = None,
        caption_reprobe_seconds: Optional[int] = None,
        catcher: Optional[YouTubeInboxCatcher] = None
    ):
        env_interval = os.getenv("DISPATCH_TRANSCRIPTION_POLL_INTERVAL")
        self.poll_interval = poll_interval_seconds or (int(env_interval) if env_interval and env_interval.isdigit() else 15)
        self.caption_wait_seconds = caption_wait_seconds or 0
        self.caption_reprobe_seconds = caption_reprobe_seconds or 300
        self.catcher = catcher or YouTubeInboxCatcher()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self):
        """Start the background transcription worker thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("Transcription worker is already running.")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="TranscriptionWorkerThread", daemon=True)
        self._thread.start()
        logger.info(
            "Transcription worker started with Gemini pipeline (Poll interval: %ds)",
            self.poll_interval
        )

    def stop(self):
        """Signal worker to stop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            logger.info("Transcription worker stopped.")

    def _run_loop(self):
        """Periodic loop scanning for DOWNLOADED and WAITING_FOR_TRANSCRIPT records."""
        try:
            self.process_pending()
        except Exception as e:
            logger.error("Initial transcription scan error: %s", e)

        while not self._stop_event.is_set():
            if self._stop_event.wait(timeout=self.poll_interval):
                break
            try:
                self.process_pending()
            except Exception as e:
                logger.error("Error in transcription worker cycle: %s", e)

    def process_pending(self) -> List[Dict[str, Any]]:
        """Scan DB for items requiring transcription processing and step their states."""
        with self._lock:
            processed = []

            # 0. FIFO Rolling Backlog: drain any queued transcriptions from yesterday / available quota
            try:
                from dispatch.transcription.gemini_engine import drain_queued_transcriptions
                drained = drain_queued_transcriptions(max_batch=5)
                if drained > 0:
                    logger.info("Drained %d queued transcription clips from backlog.", drained)
            except Exception as e:
                logger.error("Error running drain_queued_transcriptions: %s", e)

            # 1. Process DOWNLOADED items immediately with Gemini
            downloaded_items = db.list_youtube_inbox(status="DOWNLOADED", limit=20)
            for item in downloaded_items:
                res = self.process_downloaded_item(item)
                if res:
                    processed.append(res)

            # 2. Process any legacy WAITING_FOR_TRANSCRIPT items
            waiting_items = db.list_youtube_inbox(status="WAITING_FOR_TRANSCRIPT", limit=20)
            for item in waiting_items:
                res = self.process_waiting_item(item)
                if res:
                    processed.append(res)

            return processed

    def _resolve_api_key_for_item(self, item: Dict[str, Any]) -> str:
        """Resolve Gemini API key: user-specific key if vaulted, else system GEMINI_API_KEY."""
        user_id = item.get("user_id")
        vaulted_key = ""
        if user_id:
            try:
                from dispatch.youtube_connections import get_user_gemini_key
                vaulted_key = get_user_gemini_key(user_id) or ""
            except Exception as e:
                logger.debug("Failed to retrieve user gemini key: %s", e)
        from dispatch.config import GEMINI_API_KEY
        return vaulted_key if vaulted_key else (os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY)

    def process_downloaded_item(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Transcribe DOWNLOADED item directly via canonical Gemini engine."""
        video_id = item["video_id"]
        local_path_str = item.get("local_video_path")
        duration = item.get("duration", 0.0)

        if not local_path_str or not Path(local_path_str).exists() or Path(local_path_str).stat().st_size == 0:
            err = f"Local video file missing or empty for {video_id}: {local_path_str}"
            logger.warning(err)
            db.update_youtube_video(video_id=video_id, status="FAILED", last_error=err)
            return None

        api_key = self._resolve_api_key_for_item(item)
        video_path = Path(local_path_str)

        try:
            from dispatch.youtube_connections import update_job_stage_by_video_id
            update_job_stage_by_video_id(video_id, "transcribing")
        except Exception:
            pass

        db.update_youtube_video(video_id=video_id, status="TRANSCRIBING")
        logger.info("Transcribing %s with canonical Gemini engine (Primary: 3.5-transcribe, Fallback: 3.5-flash-lite)...", video_id)

        try:
            res = transcribe_media(video_path, api_key=api_key)
            segments = res.get("segments", [])
            source = res.get("source", "gemini_transcribe")

            if not segments:
                err = f"Gemini returned 0 transcript segments for {video_id}"
                logger.warning(err)
                db.update_youtube_video(video_id=video_id, status="FAILED", last_error=err)
                return None

            return self._finalize_transcription(
                video_id=video_id,
                local_video_path=local_path_str,
                duration=duration,
                source=source,
                segments=segments
            )
        except PermanentAuthError as auth_err:
            err = f"Gemini authentication failed for {video_id}: {auth_err}"
            logger.error(err)
            db.update_youtube_video(video_id=video_id, status="FAILED", last_error=err)
            return None
        except Exception as e:
            err = f"Gemini transcription failed for {video_id}: {e}"
            logger.error(err)
            db.update_youtube_video(video_id=video_id, status="FAILED", last_error=err)
            return None

    def process_waiting_item(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Legacy WAITING_FOR_TRANSCRIPT items are immediately stepped to Gemini transcription."""
        return self.process_downloaded_item(item)

    def _resolve_source_media_metadata(
        self,
        local_video_path: Optional[str],
        fallback_duration: float
    ) -> Tuple[float, int, int, str]:
        """Extract REAL source media dimensions and aspect ratio. Never invent 1080x1920."""
        from dispatch.ingestion.validator import probe_video

        width = 0
        height = 0
        aspect_ratio = "unknown"
        duration = fallback_duration

        if local_video_path:
            vpath = Path(local_video_path)
            if vpath.exists() and vpath.stat().st_size > 0:
                is_valid, meta, _ = probe_video(vpath)
                if is_valid and meta:
                    duration = meta.get("duration", fallback_duration)
                    width = meta.get("width", 0)
                    height = meta.get("height", 0)
                    aspect_ratio = meta.get("aspect_ratio", "unknown")

        return duration, width, height, aspect_ratio

    def _finalize_transcription(
        self,
        video_id: str,
        local_video_path: Optional[str],
        duration: float,
        source: str,
        segments: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Resolve authoritative chunk, persist transcript, then update status = TRANSCRIBED."""
        full_text = " ".join(seg.get("text", "").strip() for seg in segments if seg.get("text")).strip()

        yt_record = db.get_youtube_video(video_id)
        session_id = yt_record.get("session_id") if yt_record else None

        # 1. Resolve or create chunk record preserving REAL source media metadata
        file_hash = f"yt_{video_id}"
        chunk = db.get_chunk_by_file_hash(file_hash)
        if not chunk and local_video_path:
            vpath = Path(local_video_path)
            real_duration, real_width, real_height, real_aspect = self._resolve_source_media_metadata(
                local_video_path, duration
            )
            chunk_id = db.register_chunk(
                session_id=session_id,
                filename=vpath.name,
                filepath=str(vpath),
                file_hash=file_hash
            )
            db.update_chunk_metadata(
                chunk_id=chunk_id,
                duration=real_duration,
                width=real_width,
                height=real_height,
                aspect_ratio=real_aspect,
                status="verified"
            )
        elif chunk:
            chunk_id = chunk["id"]
            if session_id and not chunk.get("session_id"):
                with db.get_db_connection() as conn:
                    conn.cursor().execute("UPDATE chunks SET session_id = ? WHERE id = ?", (session_id, chunk_id))
                    conn.commit()
        else:
            chunk_id = f"chk_yt_{video_id}"

        # 2. Persist transcript into SQLite first
        try:
            transcript_id = db.save_transcript(
                chunk_id=chunk_id,
                session_id=session_id,
                full_text=full_text,
                segments=segments
            )
        except Exception as e:
            err = f"Failed to persist transcript for {video_id} (chunk {chunk_id}): {e}"
            logger.error(err)
            db.update_youtube_video(video_id=video_id, status="FAILED", last_error=err)
            raise RuntimeError(err) from e

        if not transcript_id:
            err = f"db.save_transcript returned empty ID for {video_id} (chunk {chunk_id})"
            logger.error(err)
            db.update_youtube_video(video_id=video_id, status="FAILED", last_error=err)
            raise RuntimeError(err)

        # 3. Only after successful persistence, update youtube_inbox to TRANSCRIBED
        db.update_youtube_video(
            video_id=video_id,
            status="TRANSCRIBED",
            transcript_source=source,
            segments_json=json.dumps(segments, ensure_ascii=False),
            duration=duration if duration and duration > 0 else None
        )
        try:
            from dispatch.youtube_connections import update_job_stage_by_video_id
            update_job_stage_by_video_id(video_id, "analyzing")
        except Exception:
            pass

        # 4. Post-transcription AI metadata enrichment (Route A YouTube uploads)
        if full_text and not video_id.startswith("dir_"):
            try:
                from dispatch.youtube_inbox.enricher import enrich_youtube_video_metadata
                enrich_youtube_video_metadata(
                    video_id=video_id,
                    transcript_text=full_text,
                    user_id=yt_record.get("user_id") if yt_record else None,
                    account_id=yt_record.get("account_id") if yt_record else None,
                )
            except Exception as enrich_err:
                logger.warning("Post-transcription metadata enrichment error for %s: %s", video_id, enrich_err)

        logger.info(
            "Pillar 2 Transcription SUCCESS for %s -> TRANSCRIBED (Source: %s, Segments: %d, Words: %d, TranscriptID: %s)",
            video_id, source, len(segments), len(full_text.split()), transcript_id
        )

        return {
            "video_id": video_id,
            "status": "TRANSCRIBED",
            "source": source,
            "segments_count": len(segments),
            "words_count": len(full_text.split()),
            "transcript_id": transcript_id,
            "chunk_id": chunk_id
        }
