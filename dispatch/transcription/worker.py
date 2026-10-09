"""Pillar 2: Autonomous Transcription Worker for YouTube Inbox.
Consumes items in status 'DOWNLOADED' and 'WAITING_FOR_TRANSCRIPT',
implements an explicit asynchronous wait for YouTube captions,
and falls back to local faster-whisper only after the deadline.
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
from dispatch.transcription.transcriber import transcribe_video

logger = logging.getLogger("dispatch.transcription.worker")


def _parse_iso_or_sqlite_timestamp(ts_str: Optional[str]) -> Optional[datetime]:
    """Parse timestamp string from SQLite (YYYY-MM-DD HH:MM:SS or ISO-8601)."""
    if not ts_str:
        return None
    # Replace space with T if needed
    clean_ts = ts_str.strip().replace(" ", "T")
    try:
        dt = datetime.fromisoformat(clean_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass
    # Fallback formats
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
      DOWNLOADED -> WAITING_FOR_TRANSCRIPT -> TRANSCRIBED (source: 'youtube' or 'whisper')
    """

    def __init__(
        self,
        poll_interval_seconds: Optional[int] = None,
        caption_wait_seconds: Optional[int] = None,
        caption_reprobe_seconds: Optional[int] = None,
        catcher: Optional[YouTubeInboxCatcher] = None
    ):
        # Poll interval: check DB for downloadable/transcribable items every 15s by default
        env_interval = os.getenv("DISPATCH_TRANSCRIPTION_POLL_INTERVAL")
        self.poll_interval = poll_interval_seconds or (int(env_interval) if env_interval and env_interval.isdigit() else 15)

        # Wait window for YouTube captions before Whisper fallback (default 4 hours = 14400s)
        env_wait = os.getenv("DISPATCH_TRANSCRIPT_WAIT_SECONDS")
        self.caption_wait_seconds = caption_wait_seconds or (int(env_wait) if env_wait and env_wait.isdigit() else 14400)

        # Reprobe backoff interval for checking YouTube captions (default 5 minutes = 300s)
        env_reprobe = os.getenv("DISPATCH_CAPTION_REPROBE_SECONDS")
        self.caption_reprobe_seconds = caption_reprobe_seconds or (int(env_reprobe) if env_reprobe and env_reprobe.isdigit() else 300)

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
            "Transcription worker started (Poll interval: %ds, Caption wait window: %ds / %.1fh, Reprobe interval: %ds)",
            self.poll_interval, self.caption_wait_seconds, self.caption_wait_seconds / 3600.0, self.caption_reprobe_seconds
        )

    def stop(self):
        """Signal worker to stop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            logger.info("Transcription worker stopped.")

    def _run_loop(self):
        """Periodic loop scanning for DOWNLOADED and WAITING_FOR_TRANSCRIPT records."""
        # Initial run on boot
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

            # 1. Promote DOWNLOADED items to WAITING_FOR_TRANSCRIPT
            downloaded_items = db.list_youtube_inbox(status="DOWNLOADED", limit=20)
            for item in downloaded_items:
                res = self.process_downloaded_item(item)
                if res:
                    processed.append(res)

            # 2. Check WAITING_FOR_TRANSCRIPT items
            waiting_items = db.list_youtube_inbox(status="WAITING_FOR_TRANSCRIPT", limit=20)
            for item in waiting_items:
                res = self.process_waiting_item(item)
                if res:
                    processed.append(res)

            return processed

    def process_downloaded_item(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Transition DOWNLOADED item into WAITING_FOR_TRANSCRIPT and probe captions immediately."""
        video_id = item["video_id"]
        now_dt = datetime.now(timezone.utc)
        deadline_dt = now_dt + timedelta(seconds=self.caption_wait_seconds)
        next_probe_dt = now_dt + timedelta(seconds=self.caption_reprobe_seconds)

        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        deadline_str = deadline_dt.strftime("%Y-%m-%d %H:%M:%S")
        next_probe_str = next_probe_dt.strftime("%Y-%m-%d %H:%M:%S")

        logger.info(
            "Video %s: Entering WAITING_FOR_TRANSCRIPT (Deadline: %s, Wait window: %ds, Reprobe: %ds)",
            video_id, deadline_str, self.caption_wait_seconds, self.caption_reprobe_seconds
        )

        db.update_youtube_video(
            video_id=video_id,
            status="WAITING_FOR_TRANSCRIPT",
            transcript_wait_started_at=now_str,
            transcript_wait_deadline=deadline_str,
            next_caption_probe_at=next_probe_str
        )

        # Check YouTube captions immediately once
        return self._try_fetch_captions_or_wait(
            video_id=video_id,
            local_video_path=item.get("local_video_path"),
            duration=item.get("duration", 0.0),
            deadline_dt=deadline_dt,
            is_probe_due=True
        )

    def process_waiting_item(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check captions for WAITING_FOR_TRANSCRIPT item, or fall back to Whisper if deadline passed."""
        video_id = item["video_id"]
        deadline_str = item.get("transcript_wait_deadline")
        deadline_dt = _parse_iso_or_sqlite_timestamp(deadline_str)

        now_dt = datetime.now(timezone.utc)
        if deadline_dt is None:
            # If no deadline was set, calculate from started_at or set to now + wait window
            deadline_dt = now_dt + timedelta(seconds=self.caption_wait_seconds)
            db.update_youtube_video(
                video_id=video_id,
                status="WAITING_FOR_TRANSCRIPT",
                transcript_wait_deadline=deadline_dt.strftime("%Y-%m-%d %H:%M:%S")
            )

        # Check if caption re-probe is due based on persisted next_caption_probe_at
        next_probe_str = item.get("next_caption_probe_at")
        next_probe_dt = _parse_iso_or_sqlite_timestamp(next_probe_str)
        is_probe_due = (next_probe_dt is None or now_dt >= next_probe_dt)

        return self._try_fetch_captions_or_wait(
            video_id=video_id,
            local_video_path=item.get("local_video_path"),
            duration=item.get("duration", 0.0),
            deadline_dt=deadline_dt,
            is_probe_due=is_probe_due
        )

    def _try_fetch_captions_or_wait(
        self,
        video_id: str,
        local_video_path: Optional[str],
        duration: float,
        deadline_dt: datetime,
        is_probe_due: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Attempt to fetch YouTube captions if probe is due. If unavailable and past deadline, run Whisper."""
        now_dt = datetime.now(timezone.utc)
        remaining = (deadline_dt - now_dt).total_seconds()

        # 1. Attempt to fetch YouTube captions only if probe is due
        if is_probe_due:
            logger.info("Caption probe is due for %s. Probing YouTube captions...", video_id)
            segments = None
            try:
                segments = self.catcher.fetch_youtube_captions(video_id)
            except Exception as e:
                logger.warning("Error probing YouTube captions for %s: %s", video_id, e)
                segments = None

            # Schedule the next caption probe timestamp durably
            next_probe_dt = now_dt + timedelta(seconds=self.caption_reprobe_seconds)
            db.update_youtube_video(
                video_id=video_id,
                status="WAITING_FOR_TRANSCRIPT",
                next_caption_probe_at=next_probe_dt.strftime("%Y-%m-%d %H:%M:%S")
            )

            if segments is not None:
                logger.info("Found YouTube captions for %s (%d segments). Transitioning to TRANSCRIBED.",
                            video_id, len(segments))
                return self._finalize_transcription(
                    video_id=video_id,
                    local_video_path=local_video_path,
                    duration=duration,
                    source="youtube",
                    segments=segments
                )
        else:
            logger.debug("Caption probe is NOT yet due for %s (rate-limiting YouTube checks).", video_id)

        # 2. Captions unavailable. Check deadline.
        if remaining > 0:
            logger.info(
                "YouTube captions not yet available for %s. Waiting (%.1f minutes / %.0fs remaining until Whisper fallback).",
                video_id, remaining / 60.0, remaining
            )
            return None

        # 3. Deadline passed! Explicit Whisper fallback.
        logger.info(
            "Caption wait deadline passed for %s. Triggering local faster-whisper fallback.",
            video_id
        )
        return self._run_whisper_fallback(
            video_id=video_id,
            local_video_path=local_video_path,
            duration=duration
        )

    def _run_whisper_fallback(
        self,
        video_id: str,
        local_video_path: Optional[str],
        duration: float
    ) -> Dict[str, Any]:
        """Execute local faster-whisper transcription on the downloaded video."""
        if not local_video_path:
            err = f"Cannot run Whisper fallback for {video_id}: local_video_path is missing"
            logger.error(err)
            db.update_youtube_video(video_id=video_id, status="FAILED", last_error=err)
            raise FileNotFoundError(err)

        video_path = Path(local_video_path)
        if not video_path.exists() or video_path.stat().st_size == 0:
            err = f"Cannot run Whisper fallback for {video_id}: local file not found at {video_path}"
            logger.error(err)
            db.update_youtube_video(video_id=video_id, status="FAILED", last_error=err)
            raise FileNotFoundError(err)

        try:
            logger.info("Running local faster-whisper on %s (%.2f MB)...",
                        video_path.name, video_path.stat().st_size / (1024 * 1024))
            whisper_result = transcribe_video(video_path=video_path)
            segments = whisper_result.get("segments", [])
            logger.info("Whisper finished for %s with %d segments.", video_id, len(segments))

            return self._finalize_transcription(
                video_id=video_id,
                local_video_path=local_video_path,
                duration=duration,
                source="whisper",
                segments=segments
            )
        except Exception as e:
            err = f"Whisper transcription failed for {video_id}: {e}"
            logger.error(err)
            db.update_youtube_video(video_id=video_id, status="FAILED", last_error=err)
            raise RuntimeError(err) from e

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
        """P0 & P1: Resolve authoritative chunk, persist transcript, then update status = TRANSCRIBED.
        
        Strict order:
          1. Resolve / create authoritative chunk preserving real media metadata.
          2. Save authoritative transcript idempotently.
          3. Confirm transcript persistence succeeded.
          4. Only then update youtube_inbox to status = 'TRANSCRIBED'.
        """
        full_text = " ".join(seg.get("text", "").strip() for seg in segments if seg.get("text")).strip()

        # Resolve session_id from youtube_inbox if present
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

        # 3. Confirm transcript persistence succeeded
        if not transcript_id:
            err = f"db.save_transcript returned empty ID for {video_id} (chunk {chunk_id})"
            logger.error(err)
            db.update_youtube_video(video_id=video_id, status="FAILED", last_error=err)
            raise RuntimeError(err)

        # 4. Only after successful persistence, update youtube_inbox to TRANSCRIBED
        db.update_youtube_video(
            video_id=video_id,
            status="TRANSCRIBED",
            transcript_source=source,
            segments_json=json.dumps(segments, ensure_ascii=False)
        )

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
