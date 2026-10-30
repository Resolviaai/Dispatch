import sys
import shutil
import logging
import time
import os
import atexit
from logging.handlers import RotatingFileHandler

import uvicorn

from dispatch.config import (
    WEB_HOST,
    WEB_PORT,
    STORAGE_DIR,
)
from dispatch import db
from dispatch.governor.resource_governor import ResourceGovernor
from dispatch.orchestrator.recovery import recover_laptop_orchestrator
from dispatch.web.app import app

# Configure logging with both console and rotating file output
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
log_file = STORAGE_DIR / "dispatch_daemon.log"

formatter = logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s", datefmt="%H:%M:%S")
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Console handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)
root_logger.addHandler(console_handler)

# Rotating file handler (10 MB per file, max 5 backups)
file_handler = RotatingFileHandler(str(log_file), maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
file_handler.setFormatter(formatter)
root_logger.addHandler(file_handler)

logger = logging.getLogger("dispatch.master")
governor = ResourceGovernor()

LOCK_FILE = STORAGE_DIR / "dispatch.lock"


def acquire_single_instance_lock() -> bool:
    """Enforce single-instance execution via lock file and PID inspection."""
    import psutil
    if LOCK_FILE.exists():
        try:
            content = LOCK_FILE.read_text().strip()
            if content.isdigit():
                old_pid = int(content)
                if old_pid != os.getpid() and psutil.pid_exists(old_pid):
                    proc = psutil.Process(old_pid)
                    if "python" in proc.name().lower():
                        logger.warning("Another Dispatch instance is already active (PID %d). Halting launch.", old_pid)
                        return False
        except Exception as e:
            logger.debug("Lock file read check exception: %s", e)

    try:
        LOCK_FILE.write_text(str(os.getpid()))
        atexit.register(release_single_instance_lock)
        return True
    except Exception as e:
        logger.warning("Could not write instance lock file %s: %s", LOCK_FILE, e)
        return True


def release_single_instance_lock():
    """Remove lock file on clean shutdown."""
    try:
        if LOCK_FILE.exists():
            content = LOCK_FILE.read_text().strip()
            if content == str(os.getpid()):
                LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _ensure_tls_certificates() -> tuple[str, str] | None:
    """Generate/reuse a per-install self-signed TLS cert (10y) under storage/tls/.
    Returns (cert_path, key_path) or None if generation fails (server then runs
    plain HTTP, and the fail-closed credential gates keep secrets off the wire)."""
    try:
        from pathlib import Path
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import datetime
        base = Path("storage/tls")
        base.mkdir(parents=True, exist_ok=True)
        cert_p, key_p = base / "dispatch.crt", base / "dispatch.key"
        if cert_p.exists() and key_p.exists():
            return str(cert_p), str(key_p)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Dispatch-PC")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=3650))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName("Dispatch-PC")]), critical=False)
                .sign(key, hashes.SHA256()))
        key_p.write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
        cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        logger.info("Generated self-signed TLS certificate for the dashboard: %s", cert_p)
        return str(cert_p), str(key_p)
    except Exception as e:
        logger.warning("TLS certificate generation failed; dashboard runs without TLS: %s", e)
        return None


def start_dispatch(run_web: bool = True):
    """Start all Dispatch autonomous services with crash recovery and resource governance."""
    if not acquire_single_instance_lock():
        sys.exit(0)

    logger.info("=" * 60)
    logger.info("BOOTING DISPATCH: AUTONOMOUS PERSONAL CONTENT ENGINE")
    logger.info("=" * 60)

    # 1. Initialize SQLite database
    db.init_db()
    logger.info("Database initialized at %s", db.DB_PATH)

    # 2. Crash & reboot recovery: reclaim stale leases and clean temporary files
    try:
        recovery_stats = recover_laptop_orchestrator()
        logger.info("Boot recovery: %d stale job leases reclaimed, %d temp files cleaned",
                    len(recovery_stats.get("reclaimed_jobs", [])),
                    recovery_stats.get("cleaned_temp_files", 0))
    except Exception as e:
        logger.error("Boot crash recovery encountered error: %s", e)

    # 3. Adjust process priority to low/background to prevent laptop freezing
    governor.set_low_process_priority()

    # 4. Verify FFmpeg
    if not shutil.which("ffmpeg"):
        logger.critical("FFmpeg executable not found in PATH! Exiting.")
        sys.exit(1)
    logger.info("FFmpeg verified in system PATH")

    # 5. Video ingestion is driven autonomously via RemoteJobPoller from the cloud queue.

    # 6. Pillar 2: Autonomous Transcription Worker (YouTube captions with Whisper fallback)
    from dispatch.transcription.worker import TranscriptionWorker
    transcription_worker = TranscriptionWorker()
    transcription_worker.start()
    logger.info("Autonomous Transcription worker active (Consumes DOWNLOADED -> WAITING_FOR_TRANSCRIPT -> TRANSCRIBED)")

    # 7. Pillar 3: Autonomous AI Highlight Selection Worker (Gemini Semantic Extraction)
    from dispatch.ai_clips.worker import HighlightWorker
    highlight_worker = HighlightWorker()
    highlight_worker.start()
    logger.info("Autonomous AI Highlight worker active (Consumes TRANSCRIBED -> ANALYZING -> CLIPS_DEFINED)")

    # 8. Pillar 6: Autonomous Publishing Outbox Worker (YouTube Shorts publishing)
    from dispatch.publisher.outbox import PublishingOutboxWorker
    outbox_worker = PublishingOutboxWorker()
    outbox_worker.start()
    logger.info("Autonomous Publishing Outbox worker active (Consumes queued -> uploading -> published/failed)")

    # 9b. Remote processing jobs (friends submit from anywhere; this PC claims them)
    if os.getenv("DISPATCH_REMOTE_JOBS", "1") == "1":
        try:
            import threading
            from dispatch.youtube_connections import RemoteJobPoller
            from dispatch.youtube_inbox.catcher import YouTubeInboxCatcher
            _catcher = YouTubeInboxCatcher()
            _poller = RemoteJobPoller(ingest_video=_catcher.ingest_video)
            threading.Thread(target=_poller.run_forever, daemon=True,
                             name="dispatch-remote-jobs").start()
            logger.info("Remote processing job poller active (claims queued jobs from the cloud)")
        except Exception as e:
            logger.warning("Remote job poller not started: %s", e)

    # 9c. Pillar 8: Autonomous Cloud Sync Worker (syncs local clips & edit windows to Supabase and Cloudflare R2)
    try:
        from dispatch.cloud.sync import start_cloud_sync_worker
        start_cloud_sync_worker()
        logger.info("Autonomous Cloud Sync worker active (Syncs clips to Supabase and R2)")
    except Exception as e:
        logger.warning("Cloud Sync worker not started: %s", e)

    # 9d. Housekeeping (C2/D7, audit 2026-10-27): purge heavy rendered MP4 media for
    # expired published clips. Previously GET /api/clips flushed this implicitly —
    # now it runs on a fixed schedule so the review API stays read-only. First sweep
    # 5 minutes after boot (frees disk soon after a publish completes), then every
    # 6 hours. cleanup_expired_published_clips() only purges VERIFIED published clips
    # (outbox row with a real remote_id) and always preserves thumbnails + metadata.
    try:
        import threading

        def _published_clip_janitor() -> None:
            first = True
            while True:
                try:
                    time.sleep(300 if first else 6 * 60 * 60)
                    purged = db.cleanup_expired_published_clips(retention_days=7)
                    if purged:
                        logger.info("Published-clip janitor purged media for %d expired clip(s).", purged)
                except Exception as sweep_err:
                    logger.warning("Published-clip janitor sweep failed: %s", sweep_err)
                finally:
                    first = False

        def _storage_retention_janitor() -> None:
            first = True
            while True:
                try:
                    time.sleep(120 if first else 6 * 60 * 60)
                    from dispatch.storage.retention import enforce_retention_policy
                    stats = enforce_retention_policy()
                    total_pruned = stats["pruned_7_day"] + stats["pruned_72_hour_published"] + stats["pruned_ceiling_fifo"]
                    if total_pruned > 0:
                        logger.info(
                            "Storage retention janitor pruned %d source video(s) (freed %d MB): 7d=%d, 72h=%d, FIFO_ceiling=%d",
                            total_pruned,
                            stats["freed_bytes"] // (1024 * 1024),
                            stats["pruned_7_day"],
                            stats["pruned_72_hour_published"],
                            stats["pruned_ceiling_fifo"],
                        )
                except Exception as sweep_err:
                    logger.warning("Storage retention janitor sweep failed: %s", sweep_err)
                finally:
                    first = False

        threading.Thread(target=_published_clip_janitor, daemon=True,
                          name="dispatch-published-clip-janitor").start()
        threading.Thread(target=_storage_retention_janitor, daemon=True,
                          name="dispatch-storage-retention-janitor").start()
        logger.info("Published-clip and 3-Tier Storage retention janitors active")
    except Exception as e:
        logger.warning("Storage janitors not started: %s", e)

    # 9. Start Web Dashboard
    if run_web:
        # Optional TLS (strongly recommended when pairing phones over LAN: the
        # credential endpoints refuse plaintext for non-loopback callers).
        tls_files = _ensure_tls_certificates() if os.getenv("DISPATCH_WEB_TLS", "0") == "1" else None
        scheme = "https" if tls_files else "http"
        logger.info("Launching Web Dashboard on %s://%s:%d", scheme, WEB_HOST, WEB_PORT)
        logger.info("Review clips on PC: %s://localhost:%d", scheme, WEB_PORT)
        try:
            uvicorn.run(app, host=WEB_HOST, port=WEB_PORT, log_level="warning",
                        ssl_certfile=tls_files[0] if tls_files else None,
                        ssl_keyfile=tls_files[1] if tls_files else None)
        finally:
            logger.info("Stopping Dispatch background workers...")
            outbox_worker.stop()
            highlight_worker.stop()
            transcription_worker.stop()
            release_single_instance_lock()
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
            release_single_instance_lock()
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
