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

from dispatch import db
from dispatch.youtube_inbox.oauth import list_authenticated_user_uploads
from dispatch.youtube_inbox.catcher import (
    YouTubeInboxCatcher,
    scan_channel_for_dispatch_uploads
)

logger = logging.getLogger("dispatch.youtube_inbox.poller")


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
        """Main daemon loop. Runs immediately on boot, then waits poll_interval."""
        # 1. IMMEDIATE RUN ON BOOT: Catch recordings uploaded while laptop was off
        logger.info("Laptop boot discovery: Checking YouTube for recordings created while laptop was asleep/off...")
        try:
            self.poll_once()
        except Exception as e:
            logger.error("Initial YouTube boot scan error: %s", e)

        # 2. PERIODIC RUN: Wake up every poll_interval
        while not self._stop_event.is_set():
            # Wait with interruptibility
            if self._stop_event.wait(timeout=self.poll_interval):
                break

            try:
                self.poll_once()
            except Exception as e:
                logger.error("Error during scheduled YouTube inbox poll: %s", e)

    def poll_once(self) -> List[Dict[str, Any]]:
        """Execute a single check against YouTube and process newly discovered videos."""
        with self._lock:
            self.last_poll_time = time.time()
            discovered_videos = []
            auth_succeeded = False

            # 1. Check Authenticated YouTube Data API (Finds Private & Unlisted uploads on user channel)
            try:
                auth_uploads = list_authenticated_user_uploads(max_results=15)
                auth_succeeded = True
                for item in auth_uploads:
                    discovered_videos.append(item)
            except Exception as e:
                logger.debug("Authenticated YouTube upload check failed/skipped: %s", e)

            # 2. Check channel via Channel ID or yt-dlp only if unauthenticated
            if not auth_succeeded:
                channel_id = db.get_setting("youtube_channel_id", os.getenv("DISPATCH_YOUTUBE_CHANNEL_ID", ""))
                marker = db.get_setting("youtube_inbox_marker", "[DISPATCH]")
                if channel_id:
                    try:
                        channel_uploads = scan_channel_for_dispatch_uploads(channel_id=channel_id, marker=marker, limit=10)
                        for item in channel_uploads:
                            if not any(v["video_id"] == item["video_id"] for v in discovered_videos):
                                discovered_videos.append(item)
                    except Exception as e:
                        logger.debug("Channel scanner encountered error: %s", e)

            if not discovered_videos:
                logger.debug("YouTube poll complete: 0 new uploads found.")
                return []

            logger.info("YouTube poll found %d matching Dispatch upload(s). Checking processing status...", len(discovered_videos))

            processed_items = []
            for item in discovered_videos:
                video_id = item["video_id"]
                title = item.get("title", f"Dispatch Video {video_id}")
                dispatch_id = item.get("dispatch_id")
                pub_time = item.get("published_at")

                # Idempotency check: Skip if already finished
                if db.is_youtube_video_processed(video_id):
                    continue

                existing = db.get_youtube_video(video_id)
                if not existing:
                    logger.info("Discovered NEW phone upload on YouTube: ID=%s, DispatchID=%s, Title='%s'",
                                video_id, dispatch_id, title)
                    db.register_youtube_video(
                        video_id=video_id,
                        title=title,
                        upload_time=pub_time,
                        dispatch_id=dispatch_id
                    )

                # Process the video automatically
                try:
                    logger.info("Starting autonomous processing for YouTube video %s (%s)", video_id, dispatch_id)
                    result = self.catcher.process_video(
                        url_or_id=video_id,
                        dispatch_id=dispatch_id
                    )
                    processed_items.append(result)
                except Exception as e:
                    logger.error("Failed to process YouTube video %s: %s", video_id, e)

            return processed_items
