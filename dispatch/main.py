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

    # 7. Pillar 6: Autonomous Publishing Outbox Worker (YouTube Shorts publishing)
    from dispatch.publisher.outbox import PublishingOutboxWorker
    outbox_worker = PublishingOutboxWorker()
    outbox_worker.start()
    logger.info("Autonomous Publishing Outbox worker active (Consumes queued -> uploading -> published/failed)")

    # 8. Start Web Dashboard
    if run_web:
        logger.info("Launching Web Dashboard on http://%s:%d", WEB_HOST, WEB_PORT)
        logger.info("Review clips on PC: http://localhost:%d", WEB_PORT)
        try:
            uvicorn.run(app, host=WEB_HOST, port=WEB_PORT, log_level="warning")
        finally:
            logger.info("Stopping Dispatch background workers...")
            outbox_worker.stop()
            highlight_worker.stop()
            transcription_worker.stop()
            youtube_poller.stop()
    else:
        logger.info("Running in headless daemon mode. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Dispatch daemon stopping workers...")
            outbox_worker.stop()
            highlight_worker.stop()
            transcription_worker.stop()
            youtube_poller.stop()
            logger.info("Dispatch daemon stopped.")


def process_single_chunk(chunk_info: dict):
    """End-to-end processing helper for a single staged video chunk using durable checkpoints."""
    from dispatch.orchestrator.job_queue import enqueue_job, claim_job
    from dispatch.orchestrator.pipeline_runner import PipelineStageRunner
    from pathlib import Path
    import time
    governor_instance = ResourceGovernor()
    stage_runner = PipelineStageRunner(governor=governor_instance)

    chunk_id = chunk_info["chunk_id"]
    session_id = chunk_info.get("session_id")
    job_id = enqueue_job(chunk_id=chunk_id, session_id=session_id)
    worker_id = "direct_runner"

    for _ in range(10):
        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM pipeline_jobs WHERE job_id = ?", (job_id,))
            j_status = cursor.fetchone()
            if not j_status or j_status["status"] in ("COMPLETED", "FAILED_PERMANENT"):
                break

        job = claim_job(worker_id=worker_id, lease_duration_seconds=120)
        if job and job["job_id"] == job_id:
            stage_runner.process_job_step(job, worker_id)
        else:
            time.sleep(0.5)


if __name__ == "__main__":
    start_dispatch(run_web=True)

