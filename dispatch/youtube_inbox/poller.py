"""Autonomous Background Poller for YouTube Cloud Inbox.
Periodically queries YouTube for new [DISPATCH] uploads with dispatch_id markers.
Executes immediately upon laptop boot and wakes every 10-15 minutes, driving the
complete zero-touch pipeline:
Phone (Record -> Stop -> Auto-Upload) -> YouTube (Inbox) -> Laptop Catcher (Auto-Process) -> Review.
"""
import os
import time
import logging
import threading
from typing import Dict, Any, List, Optional

from datetime import datetime, timezone
from dispatch import db
from dispatch.youtube_inbox.oauth import list_authenticated_user_uploads
from dispatch.youtube_inbox.catcher import YouTubeInboxCatcher

logger = logging.getLogger("dispatch.youtube_inbox.poller")


def parse_iso_datetime(ts_val: Any) -> Optional[datetime]:
    """Parse ISO-8601 timestamps supporting optional timezone and fractional seconds."""
    if not ts_val:
        return None
    s = str(ts_val).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


class YouTubeInboxPoller:
    """Background daemon that monitors YouTube for Dispatch phone recordings."""

    def __init__(
        self,
        poll_interval_seconds: Optional[int] = None,
        catcher: Optional[YouTubeInboxCatcher] = None
    ):
        # Default 10 minutes (600 seconds)
        env_interval = os.getenv("DISPATCH_YOUTUBE_POLL_INTERVAL")
        default_val = int(env_interval) if env_interval and env_interval.isdigit() else 600
        self.poll_interval = poll_interval_seconds or default_val

        self.catcher = catcher or YouTubeInboxCatcher()
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.last_poll_time: float = 0.0

    def start(self):
        """Start the background polling thread."""
        if self._worker_thread and self._worker_thread.is_alive():
            logger.warning("YouTube inbox poller is already running.")
            return

        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=self._run_loop, name="YouTubePollerThread", daemon=True)
        self._worker_thread.start()
        logger.info("YouTube Cloud Inbox autonomous poller started (Poll interval: %ds / %.1f min)",
                    self.poll_interval, self.poll_interval / 60.0)

    def stop(self):
        """Signal poller to stop."""
        self._stop_event.set()
        if self._worker_thread:
            self._worker_thread.join(timeout=5)
            logger.info("YouTube Cloud Inbox poller stopped.")

    def _run_loop(self):
        """Main daemon loop. Runs immediately on boot, then waits poll_interval with backoff on failure."""
        # 1. IMMEDIATE RUN ON BOOT: Catch recordings uploaded while laptop was off
        logger.info("Laptop boot discovery: Checking YouTube for recordings created while laptop was asleep/off...")
        try:
            self.poll_once()
        except Exception as e:
            logger.error("Initial YouTube boot scan error: %s", e)

        # 2. PERIODIC RUN: Wake up every poll_interval with error backoff
        consecutive_errors = 0
        while not self._stop_event.is_set():
            sleep_duration = self.poll_interval
            if consecutive_errors > 0:
                backoff_extra = min(300, 30 * (2 ** min(consecutive_errors - 1, 4)))
                sleep_duration += backoff_extra
                logger.info("Poller backoff active (%d consecutive errors): waiting %ds before next poll",
                            consecutive_errors, sleep_duration)

            if self._stop_event.wait(timeout=sleep_duration):
                break

            try:
                self.poll_once()
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                logger.error("Error during scheduled YouTube inbox poll (failures=%d): %s",
                             consecutive_errors, e)

    def poll_once(self) -> List[Dict[str, Any]]:
        """Execute a single check against YouTube and process newly discovered or ready videos."""
        from datetime import datetime, timezone
        from dispatch.youtube_inbox.oauth import get_youtube_processing_status

        with self._lock:
            self.last_poll_time = time.time()
            discovered_videos: List[Dict[str, Any]] = []

            # 1. Fetch recent authenticated uploads
            try:
                auth_uploads = list_authenticated_user_uploads(max_results_per_page=15)
                for item in auth_uploads:
                    discovered_videos.append(item)
            except Exception as e:
                logger.warning("Authenticated YouTube upload check failed: %s", e)

            # 2. Recheck waiting and stalled videos so a delayed YouTube job can recover without re-uploading.
            try:
                waiting_in_db = []
                # Recheck also covers pre-download visibility failures and caption-gap
                # rows so neither terminal-sink statuses nor YouTube-side fixes escape
                # observation when the video scrolls out of the recent-uploads page.
                for status in (
                    "WAITING_FOR_YOUTUBE_PROCESSING", "STALLED_YOUTUBE_PROCESSING",
                    "WAITING_FOR_VISIBILITY_CHECK", "CAPTIONS_UNAVAILABLE",
                ):
                    waiting_in_db.extend(db.list_youtube_inbox(status=status, limit=50))
                known_ids = {v["video_id"] for v in discovered_videos}
                for w in waiting_in_db:
                    vid = w["video_id"]
                    if vid not in known_ids:
                        discovered_videos.append({
                            "video_id": vid,
                            "title": w.get("title", f"Dispatch Video {vid}"),
                            "dispatch_id": w.get("dispatch_id"),
                            "session_id": w.get("session_id"),
                            "sequence_number": w.get("sequence_number"),
                            "published_at": w.get("upload_time") or w.get("discovered_at")
                        })
            except Exception as e:
                logger.warning("Error fetching waiting YouTube videos from DB: %s", e)

            if not discovered_videos:
                logger.debug("YouTube poll complete: 0 new uploads found.")
                return []

            # Filter candidates that are not already finished downstream
            candidate_items = [
                item for item in discovered_videos
                if not db.is_youtube_video_processed(item["video_id"])
            ]

            if not candidate_items:
                logger.debug("YouTube poll complete: All %d discovered videos already processed.", len(discovered_videos))
                return []

            logger.info("YouTube poll found %d candidate video(s) to verify status.", len(candidate_items))

            # 3. Batch query authoritative YouTube processing status
            candidate_ids = [item["video_id"] for item in candidate_items]
            status_map: Dict[str, Dict[str, Any]] = {}
            status_query_error: Optional[str] = None
            try:
                status_map = get_youtube_processing_status(candidate_ids)
            except Exception as e:
                status_query_error = str(e)
                logger.warning("Batch YouTube processing status check failed: %s", e)

            processed_items = []
            timeout_minutes = int(os.getenv("DISPATCH_YOUTUBE_PROCESSING_TIMEOUT_MINUTES", "60"))

            for item in candidate_items:
                video_id = item["video_id"]
                title = item.get("title", f"Dispatch Video {video_id}")
                dispatch_id = item.get("dispatch_id")
                session_id = item.get("session_id")
                sequence_number = item.get("sequence_number")
                pub_time = item.get("published_at")

                existing = db.get_youtube_video(video_id)
                if not existing:
                    logger.info("Discovered NEW phone upload on YouTube: ID=%s, DispatchID=%s, SessionID=%s, Seq=%s, Title='%s'",
                                video_id, dispatch_id, session_id, sequence_number, title)
                    db.register_youtube_video(
                        video_id=video_id,
                        title=title,
                        upload_time=pub_time,
                        dispatch_id=dispatch_id,
                        session_id=session_id,
                        sequence_number=sequence_number
                    )
                    existing = db.get_youtube_video(video_id)

                st_info = status_map.get(video_id)

                if not st_info:
                    # A failed status query is not evidence that YouTube is still processing or
                    # that the source should be downloaded again. Keep the current durable state.
                    if existing and existing.get("status") != "FAILED":
                        query_detail = status_query_error or "YouTube returned no status for this video ID"
                        db.update_youtube_video(
                            video_id,
                            status=existing.get("status") or "DISCOVERED",
                            last_error=f"[YOUTUBE_STATUS_UNAVAILABLE] {query_detail}; will check again without re-uploading."
                        )
                    continue

                p_status = st_info.get("processing_status")
                u_status = st_info.get("upload_status")
                fail_reason = st_info.get("failure_reason")

                # Check terminal states before the broad uploaded/in-progress condition below.
                if p_status in ("failed", "terminated") or u_status in ("failed", "rejected", "deleted"):
                    err_msg = (
                        "[YOUTUBE_PROCESSING_FAILED] "
                        f"upload_status={u_status or 'unknown'}; processing_status={p_status or 'unknown'}; "
                        f"failure_reason={fail_reason or 'not provided'}; duration={st_info.get('duration') or 'unknown'}"
                    )
                    logger.error("Video %s failed processing on YouTube: %s", video_id, err_msg)
                    if not existing or existing.get("status") != "FAILED" or existing.get("last_error") != err_msg:
                        db.update_youtube_video(
                            video_id, status="FAILED", last_error=err_msg,
                            expected_status=existing.get("status") if existing else None,
                        )
                    continue

                if existing and existing.get("status") == "FAILED" and not (
                    p_status == "succeeded" or u_status == "processed"
                ):
                    # Do not turn a durable terminal failure into a generic waiting state
                    # when the API later omits or weakens its processing status.
                    continue

                # Case A: Video is still processing on YouTube
                if p_status == "processing" or (u_status == "uploaded" and p_status != "succeeded"):
                    # Calculate elapsed processing time from the best available clock.
                    # Try the published time first, then fall back to the local
                    # discovered_at; if BOTH are missing or unparseable there is no
                    # evidence of forward progress, so treat the wait as stalled
                    # instead of looping "waiting" forever with a fake 0m elapsed.
                    try_dt = parse_iso_datetime(pub_time) if pub_time else None
                    if try_dt is None and existing:
                        try_dt = parse_iso_datetime(existing.get("discovered_at"))
                    now_utc = datetime.now(timezone.utc)
                    if try_dt:
                        elapsed_min = max(0.0, (now_utc - try_dt).total_seconds() / 60.0)
                    else:
                        elapsed_min = float(timeout_minutes)
                        logger.warning(
                            "Video %s has no parseable timestamp (published_at=%r, discovered_at=%r); "
                            "marking stalled because elapsed time cannot be tracked.",
                            video_id, pub_time, existing.get("discovered_at") if existing else None
                        )

                    if elapsed_min >= timeout_minutes:
                        warn_msg = (
                            f"[STALLED_YOUTUBE_PROCESSING] Processing exceeded {timeout_minutes}m "
                            f"({elapsed_min:.0f}m elapsed); upload_status={u_status or 'unknown'}, "
                            f"processing_status={p_status or 'unknown'}, failure_reason={fail_reason or 'not provided'}. "
                            "Check YouTube Studio or sync directly over LAN."
                        )
                        logger.warning(
                            "[STALLED] YouTube processing for %s (%s) has exceeded %d minutes (elapsed: %.1f min).",
                            video_id, title, timeout_minutes, elapsed_min
                        )
                        db.update_youtube_video(
                            video_id, status="STALLED_YOUTUBE_PROCESSING", last_error=warn_msg,
                            expected_status=existing.get("status") if existing else None,
                        )
                    else:
                        logger.info(
                            "Video %s is still processing on YouTube (%.1fm elapsed). Waiting for completion without downloading.",
                            video_id, elapsed_min
                        )
                        db.update_youtube_video(
                            video_id,
                            status="WAITING_FOR_YOUTUBE_PROCESSING",
                            last_error=(
                                f"[WAITING_FOR_YOUTUBE_PROCESSING] upload_status={u_status or 'unknown'}; "
                                f"processing_status={p_status or 'unknown'}; elapsed={elapsed_min:.0f}m; "
                                f"failure_reason={fail_reason or 'not provided'}"
                            ),
                            expected_status=existing.get("status") if existing else None,
                        )
                    continue

                # Case B: Video processing confirmed SUCCEEDED on YouTube
                if p_status == "succeeded" or u_status == "processed":
                    # FAILED rows are deliberately resurrected when YouTube itself says the
                    # bytes are processed — but only a bounded number of times. retry_count
                    # is incremented whenever a FAILED write lands; past this cap the row
                    # becomes a real terminal failure instead of an eternal Gemini re-spend.
                    if existing and existing.get("status") == "FAILED":
                        failed_retries = int(existing.get("retry_count") or 0)
                        if failed_retries >= 5:
                            stop_msg = (
                                f"[RECOVERY_STOPPED] Auto-recovery refused after {failed_retries} "
                                "failed ingest attempts; treat FAILED as terminal and review manually."
                            )
                            logger.warning(stop_msg)
                            db.update_youtube_video(
                                video_id, status="FAILED", last_error=stop_msg,
                                expected_status="FAILED",
                            )
                            continue
                    logger.info(
                        "YouTube video %s processing confirmed SUCCEEDED (Duration: %s). Proceeding to Pillar 1 media download.",
                        video_id, st_info.get("duration")
                    )
                    if existing:
                        db.update_youtube_video(
                            video_id, status=existing.get("status") or "DISCOVERED", last_error="",
                            expected_status=existing.get("status"),
                        )
                else:
                    # Unknown API states are not permission to attempt a media download.
                    state = existing.get("status") if existing else "DISCOVERED"
                    db.update_youtube_video(
                        video_id,
                        status=state or "DISCOVERED",
                        last_error=(
                            f"[YOUTUBE_STATUS_UNAVAILABLE] Unrecognized upload_status={u_status or 'unknown'}; "
                            f"processing_status={p_status or 'unknown'}; retrying status check without re-uploading."
                        ),
                        expected_status=existing.get("status") if existing else None,
                    )
                    continue

                # Ingestion: Discover -> Download -> Validate -> DOWNLOADED
                try:
                    logger.info("Starting Pillar 1 Ingestion for YouTube video %s (%s, session %s)", video_id, dispatch_id, session_id)
                    result = self.catcher.ingest_video(
                        url_or_id=video_id,
                        dispatch_id=dispatch_id,
                        session_id=session_id,
                        sequence_number=sequence_number
                    )
                    processed_items.append(result)
                except Exception as e:
                    logger.error("Failed to ingest YouTube video %s: %s", video_id, e)
                    db.update_youtube_video(video_id, status="FAILED", last_error=str(e))

            return processed_items
