"""Centralized Retry Engine & Error Classifier.
Calculates jittered exponential backoff and distinguishes transient from permanent failures.
"""
import random
import logging
from typing import Tuple, Type
from dispatch.orchestrator.state_machine import JobStatus

logger = logging.getLogger("dispatch.orchestrator.retry")


class RetryEngine:
    """Calculates backoff delays and classifies error types for resilient pipeline recovery."""

    def __init__(
        self,
        base_delay_seconds: int = 15,
        max_delay_seconds: int = 900,  # Max 15 minutes
        jitter_factor: float = 0.25
    ):
        self.base_delay = base_delay_seconds
        self.max_delay = max_delay_seconds
        self.jitter_factor = jitter_factor

    def compute_backoff_seconds(self, attempt_count: int) -> int:
        """Calculates exponential backoff with random jitter."""
        attempt = max(1, attempt_count)
        raw_backoff = self.base_delay * (2 ** (attempt - 1))
        capped = min(self.max_delay, raw_backoff)
        jitter = capped * self.jitter_factor * (random.uniform(-1.0, 1.0))
        final_delay = max(5, int(capped + jitter))
        return final_delay

    @staticmethod
    def classify_error(exc: Exception) -> Tuple[JobStatus, bool, str]:
        """Analyzes an exception and returns (JobStatus, is_permanent, reason).
        
        Returns:
            Tuple of:
            - target_status: JobStatus to transition into
            - is_permanent: True if unrecoverable, False if retryable
            - reason: human-readable classification
        """
        exc_str = str(exc).lower()
        exc_type = type(exc).__name__

        # 1. AI API Rate Limit / Transient Service Failures
        if any(term in exc_str for term in ["429", "resource_exhausted", "quota", "rate limit"]):
            return JobStatus.WAITING_FOR_AI, False, "AI API rate limit or quota exceeded (429)"

        if any(term in exc_str for term in ["503", "502", "504", "service unavailable", "overloaded", "timeout"]):
            return JobStatus.WAITING_FOR_AI, False, "AI API service temporarily unavailable or timed out"

        # 2. Disk Space Exhaustion
        if any(term in exc_str for term in ["no space left", "enospc", "disk full", "not enough space"]):
            return JobStatus.WAITING_FOR_RESOURCES, False, "Disk space exhausted"

        # 3. Windows File Locks / Transient I/O Contention
        if "used by another process" in exc_str or "winerror 32" in exc_str:
            return JobStatus.RETRY_PENDING, False, "File temporarily locked by another OS process"

        # 4. Permanent Corruptions or Unrecoverable Formats
        if any(term in exc_str for term in [
            "invalid data found when processing input",
            "could not find codec parameters",
            "moov atom not found",
            "file is empty",
            "0 bytes",
            "format not supported"
        ]):
            return JobStatus.FAILED_PERMANENT, True, f"Unrecoverable media corruption: {exc}"

        if isinstance(exc, FileNotFoundError) and "storage/incoming" in exc_str:
            return JobStatus.FAILED_PERMANENT, True, "Source file permanently missing"

        # 5. Default generic retryable failure
        return JobStatus.RETRY_PENDING, False, f"Transient pipeline error ({exc_type}): {exc}"
