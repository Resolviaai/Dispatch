"""Dispatch PC daemon: YouTube upload polling, local clip processing, and dashboard."""
import sys
import shutil
import logging
import time

import uvicorn

from dispatch.config import (
    WEB_HOST,
    WEB_PORT,
)
from dispatch import db
from dispatch.governor.resource_governor import ResourceGovernor
from dispatch.youtube_inbox.poller import YouTubeInboxPoller
from dispatch.web.app import app

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("dispatch.master")
governor = ResourceGovernor()
def start_dispatch(run_web: bool = True):
    """Start all Dispatch autonomous services with crash recovery and resource governance."""
    logger.info("=" * 60)
    logger.info("BOOTING DISPATCH: AUTONOMOUS PERSONAL CONTENT ENGINE")
    logger.info("=" * 60)

    # 1. Initialize SQLite database
    db.init_db()
    logger.info("Database initialized at %s", db.DB_PATH)

    # 2. Adjust process priority to low/background to prevent laptop freezing
    governor.set_low_process_priority()

    # 3. Verify FFmpeg
    if not shutil.which("ffmpeg"):
        logger.critical("FFmpeg executable not found in PATH! Exiting.")
        sys.exit(1)
    logger.info("FFmpeg verified in system PATH")

    # 4. YouTube is the only automatic video ingest path on PC (Pillar 1).
    youtube_poller = YouTubeInboxPoller()
    youtube_poller.start()
    logger.info("Autonomous YouTube Cloud Inbox poller active (Checks immediately on boot + every 10 min)")

    # 5. Pillar 2: Autonomous Transcription Worker (YouTube captions with Whisper fallback)
    from dispatch.transcription.worker import TranscriptionWorker
    transcription_worker = TranscriptionWorker()
    transcription_worker.start()
    logger.info("Autonomous Transcription worker active (Consumes DOWNLOADED -> WAITING_FOR_TRANSCRIPT -> TRANSCRIBED)")

    # 6. Pillar 3: Autonomous AI Highlight Selection Worker (Gemini Semantic Extraction)
    from dispatch.ai_clips.worker import HighlightWorker
    highlight_worker = HighlightWorker()
    highlight_worker.start()
    logger.info("Autonomous AI Highlight worker active (Consumes TRANSCRIBED -> ANALYZING -> CLIPS_DEFINED)")

    # 7. Start Web Dashboard
    if run_web:
        logger.info("Launching Web Dashboard on http://%s:%d", WEB_HOST, WEB_PORT)
        logger.info("Review clips on PC: http://localhost:%d", WEB_PORT)
        uvicorn.run(app, host=WEB_HOST, port=WEB_PORT, log_level="warning")
    else:
        logger.info("Running in headless daemon mode. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Dispatch daemon stopped.")


if __name__ == "__main__":
    start_dispatch(run_web=True)
