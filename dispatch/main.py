"""Master Dispatch Daemon & Pipeline Orchestrator.
Runs the complete autonomous loop:
- File Ingestion Watcher (Incoming Phone Sync)
- Speech Transcription (faster-whisper)
- AI Semantic Highlight Detection (Gemini Flash / Local Heuristic)
- Video Assembly & Subtitle Rendering (FFmpeg + ASS)
- Publishing Outbox Worker (YouTube Shorts + Instagram Reels)
- Web Control Dashboard (FastAPI / Uvicorn)
"""
import sys
import time
import shutil
import logging
import threading
from pathlib import Path
from typing import Dict, Any

import uvicorn

from dispatch.config import (
    INCOMING_DIR,
    PROCESSING_DIR,
    CLIPS_DIR,
    WEB_HOST,
    WEB_PORT,
    DEFAULT_PUBLISH_MODE
)
from dispatch import db
from dispatch.ingestion.watcher import scan_incoming
from dispatch.orchestrator.job_queue import init_job_queue_schema, enqueue_job, claim_job
from dispatch.orchestrator.recovery import recover_laptop_orchestrator
from dispatch.orchestrator.pipeline_runner import PipelineStageRunner
from dispatch.governor.resource_governor import ResourceGovernor
from dispatch.transcription.transcriber import transcribe_video
from dispatch.ai_clips.highlight_finder import identify_and_save_highlights
from dispatch.video_engine.renderer import render_clip
from dispatch.publisher.outbox import process_outbox_queue
from dispatch.youtube_inbox.poller import YouTubeInboxPoller
from dispatch.transport.discovery import DiscoveryBeaconServer
from dispatch.web.app import app

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("dispatch.master")
governor = ResourceGovernor()
stage_runner = PipelineStageRunner(governor=governor)


def process_single_chunk(chunk_info: Dict[str, Any]):
    """End-to-end processing pipeline for a single staged video chunk using durable checkpoints."""
    chunk_id = chunk_info["chunk_id"]
    session_id = chunk_info.get("session_id")
    filepath = Path(chunk_info["filepath"])
    metadata = chunk_info.get("metadata", {})
    duration = metadata.get("duration", 0.0)

    logger.info(">>> STARTING CHECKPOINTED PIPELINE for chunk %s (%s, %.1fs)", chunk_id, filepath.name, duration)
    job_id = enqueue_job(chunk_id=chunk_id, session_id=session_id)
    worker_id = "direct_runner"

    # Drive the job through its discrete checkpoints synchronously
    for _ in range(10):  # Maximum 10 step iterations (typically 5 stages)
        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, current_stage FROM pipeline_jobs WHERE job_id = ?", (job_id,))
            j_status = cursor.fetchone()
            if not j_status or j_status["status"] in ("COMPLETED", "FAILED_PERMANENT"):
                break

        job = claim_job(worker_id=worker_id, lease_duration_seconds=120)
        if job and job["job_id"] == job_id:
            stage_runner.process_job_step(job, worker_id)
        else:
            time.sleep(0.5)

    logger.info("<<< PIPELINE FINISHED for chunk %s", chunk_id)


def pipeline_worker_loop():
    """Autonomous background worker executing checkpointed stages and outbox publishing."""
    logger.info("Dispatch Background Checkpointed Pipeline Worker started")
    worker_id = "laptop_worker_01"
    last_watchdog_time = time.time()

    while True:
        try:
            # 1. Watchdog: Periodically reclaim stale/zombie worker leases
            now = time.time()
            if now - last_watchdog_time > 45.0:
                last_watchdog_time = now
                recovery_res = recover_laptop_orchestrator()
                if recovery_res.get("reclaimed_jobs"):
                    logger.warning("Watchdog reclaimed %d stalled jobs with expired leases", len(recovery_res["reclaimed_jobs"]))

            # 2. Ingestion: scan incoming folder for newly arrived phone sync files
            staged_chunks = scan_incoming()
            for chunk in staged_chunks:
                enqueue_job(chunk_id=chunk["chunk_id"], session_id=chunk.get("session_id"))

            # 3. Orchestration: claim and execute the next checkpointed job stage
            job = claim_job(worker_id=worker_id, lease_duration_seconds=60)
            if job:
                stage_runner.process_job_step(job, worker_id)
                time.sleep(0.5)
                continue

            # 4. Publishing Outbox: process queued social uploads
            process_outbox_queue()

        except Exception as e:
            logger.error("Error in background pipeline worker loop: %s", e)

        time.sleep(3.0)


def start_dispatch(run_web: bool = True):
    """Start all Dispatch autonomous services with crash recovery and resource governance."""
    logger.info("=" * 60)
    logger.info("BOOTING DISPATCH: AUTONOMOUS PERSONAL CONTENT ENGINE")
    logger.info("=" * 60)

    # 1. Initialize SQLite database & Job Queue Schema
    db.init_db()
    init_job_queue_schema()
    logger.info("Database & Job Queue initialized at %s", db.DB_PATH)

    # 2. Adjust process priority to low/background to prevent laptop freezing
    governor.set_low_process_priority()

    # 3. Laptop Boot Crash Recovery (scan expired leases, resume interrupted jobs)
    recovery_report = recover_laptop_orchestrator()
    if recovery_report["reclaimed_jobs"]:
        logger.info("Laptop crash recovery: Reclaimed %d interrupted jobs at last durable checkpoint",
                    len(recovery_report["reclaimed_jobs"]))

    # 4. Verify FFmpeg
    if not shutil.which("ffmpeg"):
        logger.critical("FFmpeg executable not found in PATH! Exiting.")
        sys.exit(1)
    logger.info("FFmpeg verified in system PATH")

    # 5. Start background checkpointed pipeline worker thread
    worker_thread = threading.Thread(target=pipeline_worker_loop, daemon=True)
    worker_thread.start()
    logger.info("Autonomous pipeline worker thread running in background")

    # 6. Start autonomous YouTube Cloud Inbox Poller daemon
    youtube_poller = YouTubeInboxPoller()
    youtube_poller.start()
    logger.info("Autonomous YouTube Cloud Inbox poller active (Checks immediately on boot + every 10 min)")

    # 7. Start autonomous UDP Zero-Config Discovery Beacon
    discovery_server = DiscoveryBeaconServer()
    discovery_server.start()
    logger.info("Autonomous UDP Zero-Config Discovery Beacon active on port 8765")

    # 8. Start Web Dashboard
    if run_web:
        logger.info("Launching Web Dashboard on http://%s:%d", WEB_HOST, WEB_PORT)
        logger.info("Review clips on PC: http://localhost:%d", WEB_PORT)
        logger.info("Review clips on Phone: http://<your-pc-ip>:%d", WEB_PORT)
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
