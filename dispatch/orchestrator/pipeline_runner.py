"""Discrete Checkpointed Pipeline Stage Runner.
Executes media processing stage by stage, validating outputs before advancing checkpoints.
Never re-runs already-completed stages when recovering from crashes or interruptions.
"""
import os
import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from dispatch.config import (
    PROCESSING_DIR,
    DEFAULT_PUBLISH_MODE
)
from dispatch import db
from dispatch.ingestion.validator import probe_video
from dispatch.transcription.transcriber import transcribe_video
from dispatch.ai_clips.highlight_finder import identify_and_save_highlights
from dispatch.video_engine.renderer import render_clip
from dispatch.orchestrator.state_machine import PipelineStage, JobStatus
from dispatch.orchestrator.job_queue import (
    complete_stage_checkpoint,
    fail_stage_job,
    renew_heartbeat,
    LeaseLostError
)
from dispatch.orchestrator.heartbeat import HeartbeatThread
from dispatch.orchestrator.retry_engine import RetryEngine
from dispatch.governor.resource_governor import ResourceGovernor

logger = logging.getLogger("dispatch.orchestrator.runner")


class PipelineStageRunner:
    """Dispatches and validates individual processing stages with durable checkpoints."""

    def __init__(self, governor: Optional[ResourceGovernor] = None, retry_engine: Optional[RetryEngine] = None):
        self.governor = governor or ResourceGovernor()
        self.retry_engine = retry_engine or RetryEngine()

    def process_job_step(self, job: Dict[str, Any], worker_id: str) -> bool:
        """Processes the current checkpointed stage of a job.
        
        Returns True if stage was completed, False if deferred or failed.
        """
        job_id = job["job_id"]
        chunk_id = job["chunk_id"]
        current_stage = PipelineStage(job["current_stage"])
        attempt_count = job.get("attempt_count", 1)

        # 1. Resource Policy Gate Check
        # For heavy compute stages (TRANSCRIBE, RENDER), check system health first
        if current_stage in (PipelineStage.TRANSCRIBE, PipelineStage.RENDER):
            can_run, reason = self.governor.can_process_heavy_task()
            if not can_run:
                logger.info("Job %s deferring stage %s: %s", job_id, current_stage.value, reason)
                fail_stage_job(
                    job_id=job_id,
                    error_message=f"Resource governor hold: {reason}",
                    wait_state=JobStatus.WAITING_FOR_RESOURCES,
                    delay_seconds=30
                )
                return False

        # 2. Retrieve chunk metadata from database
        chunk = db.get_chunk_by_id(chunk_id)
        if not chunk:
            fail_stage_job(
                job_id=job_id,
                error_message=f"Chunk {chunk_id} not found in database",
                wait_state=JobStatus.FAILED_PERMANENT,
                delay_seconds=0
            )
            return False

        filepath = Path(chunk["filepath"])

        # 3. Dispatch to discrete stage handler
        try:
            renew_heartbeat(job_id, worker_id, extend_seconds=60)

            if current_stage == PipelineStage.VERIFY:
                self._run_verify_stage(job, chunk, filepath)
            elif current_stage == PipelineStage.TRANSCRIBE:
                self._run_transcribe_stage(job, chunk, filepath, worker_id)
            elif current_stage == PipelineStage.ANALYZE:
                self._run_analyze_stage(job, chunk, filepath)
            elif current_stage == PipelineStage.RENDER:
                self._run_render_stage(job, chunk, filepath, worker_id)
            elif current_stage == PipelineStage.FINALIZE:
                self._run_finalize_stage(job, chunk, filepath)
            else:
                logger.warning("Unknown stage %s for job %s", current_stage, job_id)
                return False

            # Checkpoint stage completion with fencing lease check
            complete_stage_checkpoint(job_id, current_stage, worker_id=worker_id)
            return True

        except LeaseLostError as le:
            logger.warning("Fencing lease lost for job %s at stage %s: %s. Safely abandoning step.",
                           job_id, current_stage.value, le)
            return False
        except Exception as e:
            logger.exception("Error processing stage %s for job %s: %s", current_stage.value, job_id, e)
            target_status, is_permanent, reason = self.retry_engine.classify_error(e)
            delay = 0 if is_permanent else self.retry_engine.compute_backoff_seconds(attempt_count)
            fail_stage_job(
                job_id=job_id,
                error_message=f"Stage {current_stage.value} failed: {e} ({reason})",
                wait_state=target_status if not is_permanent else JobStatus.FAILED_PERMANENT,
                delay_seconds=delay
            )
            return False

    def _run_verify_stage(self, job: Dict[str, Any], chunk: Dict[str, Any], filepath: Path):
        """Stage 1: Verify source media container, size, and codecs."""
        if not filepath.exists():
            raise FileNotFoundError(f"Source video file not found at {filepath}")

        if filepath.stat().st_size == 0:
            raise ValueError(f"Source video file {filepath} is 0 bytes")

        is_valid, meta, err = probe_video(filepath, chunk_id=chunk["id"])
        if not is_valid:
            raise ValueError(f"Corrupt or invalid media file: {err}")

        logger.info("VERIFY passed for job %s: %.1fs, %s, %s",
                    job["job_id"], meta.get("duration", 0), meta.get("aspect_ratio"), meta.get("resolution"))

    def _run_transcribe_stage(self, job: Dict[str, Any], chunk: Dict[str, Any], filepath: Path, worker_id: str):
        """Stage 2: Transcribe audio to word-level timestamps. Reuses DB if already present."""
        chunk_id = chunk["id"]

        # Check if already transcribed from a prior interrupted run
        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT full_text, segments_json FROM transcripts WHERE chunk_id = ?", (chunk_id,))
            existing = cursor.fetchone()

        if existing and existing["segments_json"]:
            logger.info("Transcribe checkpoint: Reusing existing transcript for chunk %s", chunk_id)
            return

        # Perform transcription with active background heartbeat
        with HeartbeatThread(job_id=job["job_id"], worker_id=worker_id, interval_seconds=15, lease_seconds=300):
            transcript_data = transcribe_video(
                video_path=filepath,
                chunk_id=chunk_id,
                session_id=chunk.get("session_id")
            )

        if not transcript_data or not transcript_data.get("segments"):
            logger.warning("Empty transcription segments for chunk %s (audio may be silent)", chunk_id)

    def _run_analyze_stage(self, job: Dict[str, Any], chunk: Dict[str, Any], filepath: Path):
        """Stage 3: AI highlight identification and packaging. Reuses DB if already present."""
        chunk_id = chunk["id"]

        # Check if clips already generated
        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM clips WHERE chunk_id = ?", (chunk_id,))
            existing_clips = cursor.fetchall()

        if existing_clips and len(existing_clips) > 0:
            logger.info("Analyze checkpoint: Reusing %d existing clips for chunk %s", len(existing_clips), chunk_id)
            return

        # Retrieve transcript
        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT segments_json FROM transcripts WHERE chunk_id = ?", (chunk_id,))
            t_row = cursor.fetchone()

        import json
        segments = json.loads(t_row["segments_json"]) if t_row and t_row["segments_json"] else []

        # Validate duration
        is_valid, meta, _ = probe_video(filepath)
        duration = meta.get("duration", 60.0) if is_valid else 60.0

        publish_mode = db.get_setting("publish_mode", DEFAULT_PUBLISH_MODE)
        clip_ids = identify_and_save_highlights(
            chunk_id=chunk_id,
            session_id=chunk.get("session_id"),
            segments=segments,
            total_duration=duration,
            publish_mode=publish_mode
        )

        logger.info("Analyze completed for job %s: Identified %d clips", job["job_id"], len(clip_ids))

    def _run_render_stage(self, job: Dict[str, Any], chunk: Dict[str, Any], filepath: Path, worker_id: str):
        """Stage 4: Render candidate clips. Skips already-rendered clips atomically."""
        chunk_id = chunk["id"]
        is_valid, meta, _ = probe_video(filepath)
        aspect_ratio = meta.get("aspect_ratio", "16:9") if is_valid else "16:9"

        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT segments_json FROM transcripts WHERE chunk_id = ?", (chunk_id,))
            t_row = cursor.fetchone()
            cursor.execute("SELECT * FROM clips WHERE chunk_id = ?", (chunk_id,))
            clips = cursor.fetchall()

        import json
        segments = json.loads(t_row["segments_json"]) if t_row and t_row["segments_json"] else []

        if not clips:
            logger.warning("No clips found to render for chunk %s", chunk_id)
            return

        with HeartbeatThread(job_id=job["job_id"], worker_id=worker_id, interval_seconds=15, lease_seconds=300):
            for clip in clips:
                cid = clip["id"]
                rendered_path = Path(clip["video_path"]) if clip["video_path"] else None

                # Skip if already rendered and file is valid AND rendered_layout_mode matches
                if (rendered_path and rendered_path.exists() and rendered_path.stat().st_size > 0
                        and clip.get("rendered_layout_mode") == clip.get("layout_mode")):
                    logger.info("Render checkpoint: Clip %s already rendered at %s", cid, rendered_path.name)
                    continue

                render_clip(
                    source_video=filepath,
                    clip_id=cid,
                    start_time=clip["start_time"],
                    end_time=clip["end_time"],
                    aspect_ratio=aspect_ratio,
                    layout_mode=clip["layout_mode"],
                    segments=segments
                )

    def _run_finalize_stage(self, job: Dict[str, Any], chunk: Dict[str, Any], filepath: Path):
        """Stage 5: Finalize chunk and mark processed. Preserves raw source video to prevent data loss."""
        chunk_id = chunk["id"]

        with db.get_db_connection() as conn:
            conn.execute("UPDATE chunks SET status = 'processed' WHERE id = ?", (chunk_id,))

        logger.info("Finalized job %s and chunk %s successfully! (Source video preserved at %s)",
                    job["job_id"], chunk_id, filepath.name)
