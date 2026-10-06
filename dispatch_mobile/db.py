"""Mobile client SQLite storage layer.
Provides crash-safe WAL mode persistence for sessions, segments, and upload outbox.
Serves as the durable source of truth on the mobile device.
"""
import sqlite3
import hashlib
import contextlib
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

MOBILE_STORAGE_DIR = Path(__file__).resolve().parent.parent / "storage" / "mobile"
MOBILE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
MOBILE_DB_PATH = MOBILE_STORAGE_DIR / "dispatch_mobile.db"


@contextlib.contextmanager
def get_mobile_db():
    """Context manager yielding a thread-safe connection to the mobile database with auto-commit."""
    conn = sqlite3.connect(str(MOBILE_DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_mobile_db():
    """Initialize mobile schema for sessions, segments, and outbox."""
    with get_mobile_db() as conn:
        cursor = conn.cursor()

        # Sessions Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mobile_sessions (
                session_id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'RECORDING', -- RECORDING, FINALIZED, ABANDONED
                total_segments INTEGER DEFAULT 0,
                notes TEXT
            );
        """)

        # Segments Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mobile_segments (
                segment_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                sequence_number INTEGER NOT NULL,
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL,
                file_size_bytes INTEGER DEFAULT 0,
                sha256_hash TEXT,
                status TEXT NOT NULL DEFAULT 'RECORDING',
                -- Status states: RECORDING, FINALIZED, QUEUED_FOR_UPLOAD, UPLOADING, UPLOADED, VERIFIED_BY_LAPTOP, DELETED_FROM_PHONE
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finalized_at TIMESTAMP,
                verified_at TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES mobile_sessions (session_id) ON DELETE CASCADE
            );
        """)

        # Upload Outbox Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mobile_outbox (
                segment_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                remote_offset INTEGER DEFAULT 0,
                attempt_count INTEGER DEFAULT 0,
                next_retry_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_error TEXT,
                status TEXT NOT NULL DEFAULT 'QUEUED_FOR_UPLOAD',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (segment_id) REFERENCES mobile_segments (segment_id) ON DELETE CASCADE
            );
        """)

        # Settings Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mobile_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

        cursor.execute("""
            INSERT OR IGNORE INTO mobile_settings (key, value) VALUES
            ('cleanup_policy', 'DELETE_AFTER_VERIFIED_COPY'),
            ('network_policy', 'ANY_NETWORK'),
            ('laptop_url', 'http://127.0.0.1:8000'),
            ('auth_token', 'dispatch_paired_secret_default');
        """)


def calculate_sha256(filepath: Path, block_size: int = 65536) -> str:
    """Calculate SHA-256 hash for strict end-to-end media verification."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(block_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
