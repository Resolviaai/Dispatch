"""Dispatch Mobile Client Package.
Provides local SQLite outbox, continuous segmenter, crash recovery, and storage retention.
"""
from dispatch_mobile.db import init_mobile_db, get_mobile_db, calculate_sha256
from dispatch_mobile.segmenter import RecordingSessionManager
from dispatch_mobile.recovery import recover_mobile_state
from dispatch_mobile.retention import cleanup_verified_segments

__all__ = [
    "init_mobile_db",
    "get_mobile_db",
    "calculate_sha256",
    "RecordingSessionManager",
    "recover_mobile_state",
    "cleanup_verified_segments"
]
