"""Heartbeat Thread and Context Manager for Long-Running Pipeline Stages.
Periodically renews worker leases in the database during stages like TRANSCRIBE and RENDER
so that the watchdog does not reclaim the job while work is actively progressing.
"""
import time
import threading
import logging
from typing import Optional
from dispatch.orchestrator.job_queue import renew_heartbeat, LeaseLostError

logger = logging.getLogger("dispatch.orchestrator.heartbeat")


class HeartbeatThread:
    """Background thread that periodically pings the job queue to extend the worker lease."""

    def __init__(
        self,
        job_id: str,
        worker_id: str,
        interval_seconds: float = 15.0,
        lease_seconds: int = 300
    ):
        self.job_id = job_id
        self.worker_id = worker_id
        self.interval_seconds = interval_seconds
        self.lease_seconds = lease_seconds
        self.stop_event = threading.Event()
        self.lease_lost = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start the background heartbeat thread."""
        self.stop_event.clear()
        self.lease_lost = False
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"heartbeat_{self.job_id}",
            daemon=True
        )
        self._thread.start()
        logger.debug("Started heartbeat thread for job %s (worker: %s, interval: %.1fs, lease: %ds)",
                     self.job_id, self.worker_id, self.interval_seconds, self.lease_seconds)

    def stop(self):
        """Stop the heartbeat thread and wait for it to join."""
        if self._thread and self._thread.is_alive():
            self.stop_event.set()
            self._thread.join(timeout=2.0)
            logger.debug("Stopped heartbeat thread for job %s", self.job_id)

    def _run_loop(self):
        """Loop running in background, periodically renewing the lease."""
        while not self.stop_event.wait(self.interval_seconds):
            try:
                renewed = renew_heartbeat(
                    job_id=self.job_id,
                    worker_id=self.worker_id,
                    extend_seconds=self.lease_seconds
                )
                if not renewed:
                    logger.warning("Heartbeat renewal failed for job %s (worker: %s): lease lost or reclaimed!",
                                   self.job_id, self.worker_id)
                    self.lease_lost = True
                    break
                else:
                    logger.debug("Heartbeat extended lease for job %s (+%ds)", self.job_id, self.lease_seconds)
            except Exception as e:
                logger.error("Exception in heartbeat renewal for job %s: %s", self.job_id, e)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        if self.lease_lost and exc_type is None:
            raise LeaseLostError(f"Lease was lost during stage execution for job {self.job_id}")
        return False
