"""Database layer for Dispatch.
Provides thread-safe SQLite connection with WAL mode and schema management.
"""
import os
import sys
import sqlite3
import json
import uuid
import contextlib
import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional
from pathlib import Path
from dispatch.config import (
    DB_PATH, DATABASE_DIR, DEFAULT_PUBLISH_MODE,
    MAX_CLIP_DURATION, MIN_CLIP_DURATION,
)

logger = logging.getLogger("dispatch.db")

CURRENT_SCHEMA_VERSION = 2


def verify_db_integrity() -> bool:
    """Run PRAGMA integrity_check to verify SQLite database health."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        res = cursor.fetchone()
        return res[0] == "ok" if res else False


def backup_database(backup_dir: Optional[Path] = None) -> Path:
    """Create a non-blocking hot SQLite snapshot backup using sqlite3.Connection.backup().
    Preserves rolling 5 latest snapshots.
    """
    target_dir = backup_dir or (DATABASE_DIR / "backups")
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = target_dir / f"dispatch_backup_{timestamp}.db"

    source_conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    dest_conn = sqlite3.connect(str(backup_file))
    try:
        source_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        source_conn.close()

    # Retention: keep latest 5 backups
    existing_backups = sorted(target_dir.glob("dispatch_backup_*.db"), key=lambda p: p.stat().st_mtime)
    while len(existing_backups) > 5:
        oldest = existing_backups.pop(0)
        try:
            oldest.unlink()
        except Exception:
            pass

    return backup_file


_default_db_path = DB_PATH
_test_db_initialized = False

def _ensure_test_schema(db_file: str):
    global _test_db_initialized
    if _test_db_initialized:
        return
    is_test = "unittest" in sys.modules or "pytest" in sys.modules or bool(os.getenv("DISPATCH_STORAGE_DIR"))
    if not is_test:
        return
    try:
        raw_conn = sqlite3.connect(str(db_file), timeout=10.0)
        try:
            cur = raw_conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions';")
            if not cur.fetchone():
                init_db()
            _test_db_initialized = True
        finally:
            raw_conn.close()
    except Exception:
        pass

@contextlib.contextmanager
def get_db_connection():
    """Context manager yielding a thread-safe SQLite connection with auto-commit and guaranteed close."""
    import os
    if DB_PATH != _default_db_path:
        db_file = str(DB_PATH)
    else:
        db_file = os.getenv("DISPATCH_DB_PATH") or str(DB_PATH)
    _ensure_test_schema(db_file)
    conn = sqlite3.connect(str(db_file), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Initialize database tables, migrations, and initial settings."""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Check and update schema version
        cursor.execute("PRAGMA user_version;")
        user_ver_row = cursor.fetchone()
        user_ver = user_ver_row[0] if user_ver_row else 0

        # Sessions Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'recording', -- recording, processing, completed, failed
                total_chunks INTEGER DEFAULT 0,
                notes TEXT
            );
        """)

        # Video Chunks Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL,
                file_hash TEXT,
                duration REAL DEFAULT 0.0,
                width INTEGER DEFAULT 0,
                height INTEGER DEFAULT 0,
                aspect_ratio TEXT DEFAULT 'unknown', -- '16:9', '9:16', 'unknown'
                status TEXT NOT NULL DEFAULT 'incoming', -- incoming, verified, transcribing, transcribed, processed, purged
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE SET NULL
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_filename ON chunks (filename);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_session ON chunks (session_id);")

        # Transcripts Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transcripts (
                id TEXT PRIMARY KEY,
                chunk_id TEXT NOT NULL,
                session_id TEXT,
                full_text TEXT NOT NULL,
                segments_json TEXT NOT NULL, -- JSON array of timestamped words/segments
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chunk_id) REFERENCES chunks (id) ON DELETE CASCADE
            );
        """)

        # Extracted Candidate Clips Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS clips (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                chunk_id TEXT,
                start_time REAL NOT NULL,
                end_time REAL NOT NULL,
                ai_suggested_start_time REAL,
                ai_suggested_end_time REAL,
                duration REAL NOT NULL,
                range_revision INTEGER NOT NULL DEFAULT 0,
                caption_range_revision INTEGER,
                caption_range_start REAL,
                caption_range_end REAL,
                caption_timing_warning TEXT,
                title TEXT NOT NULL,
                hook TEXT,
                description TEXT,
                hashtags TEXT,
                virality_score INTEGER DEFAULT 0,
                layout_mode TEXT DEFAULT 'fit_blur', -- crop_follow, fit_blur, native_portrait
                rendered_layout_mode TEXT,           -- records the framing mode used for current video_path
                render_stale INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ready_review', -- ready_review, approved, rejected, published
                publish_mode TEXT NOT NULL DEFAULT 'private', -- private, public
                platform_targets TEXT DEFAULT 'youtube,instagram',
                video_path TEXT,
                thumbnail_path TEXT,
                padded_start REAL,
                padded_end REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                approved_at TIMESTAMP,
                published_at TIMESTAMP,
                user_id TEXT,
                FOREIGN KEY (chunk_id) REFERENCES chunks (id) ON DELETE SET NULL
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_clips_chunk ON clips (chunk_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_clips_status ON clips (status);")

        # User Rejections Table (for negative feedback learning)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rejections (
                id TEXT PRIMARY KEY,
                clip_id TEXT,
                title TEXT,
                transcript_snippet TEXT,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Publishing Outbox (idempotent multi-platform publishing)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS publishing_outbox (
                id TEXT PRIMARY KEY,
                clip_id TEXT NOT NULL,
                platform TEXT NOT NULL, -- youtube, instagram, linkedin, x
                account_id TEXT,
                status TEXT NOT NULL DEFAULT 'queued', -- queued, uploading, published, failed, blocked_needs_auth, reconciliation_required
                publish_mode TEXT NOT NULL DEFAULT 'private', -- private, public
                attempt_count INTEGER DEFAULT 0,
                last_error TEXT,
                idempotency_key TEXT UNIQUE NOT NULL,
                user_id TEXT,
                remote_id TEXT,
                remote_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                attempt_started_at TIMESTAMP,
                next_retry_at TIMESTAMP,
                published_at TIMESTAMP,
                FOREIGN KEY (clip_id) REFERENCES clips (id) ON DELETE CASCADE
            );
        """)

        # Global Key-Value Settings
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

        # Checkpointed Pipeline Jobs (Durable State Machine, Leases, Heartbeats)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_jobs (
                job_id TEXT PRIMARY KEY,
                chunk_id TEXT NOT NULL,
                session_id TEXT,
                current_stage TEXT NOT NULL DEFAULT 'VERIFY', -- VERIFY, TRANSCRIBE, ANALYZE, RENDER, FINALIZE, COMPLETED
                status TEXT NOT NULL DEFAULT 'QUEUED',        -- QUEUED, PROCESSING, WAITING_FOR_AI, WAITING_FOR_RESOURCES, RETRY_PENDING, COMPLETED, FAILED_PERMANENT
                worker_id TEXT,
                lease_expires_at TIMESTAMP,
                heartbeat_at TIMESTAMP,
                attempt_count INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 5,
                last_error TEXT,
                next_retry_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chunk_id) REFERENCES chunks (id) ON DELETE CASCADE
            );
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_claim 
            ON pipeline_jobs (status, next_retry_at, lease_expires_at);
        """)

        # YouTube Transport / Inbox Table (Idempotent cloud queue)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS youtube_inbox (
                video_id TEXT PRIMARY KEY,
                dispatch_id TEXT,
                session_id TEXT,
                sequence_number INTEGER,
                title TEXT NOT NULL,
                channel_id TEXT,
                upload_time TIMESTAMP,
                duration REAL DEFAULT 0.0,
                status TEXT NOT NULL DEFAULT 'DISCOVERED', -- DISCOVERED, DOWNLOADING, VALIDATING, DOWNLOADED, WAITING_FOR_TRANSCRIPT, TRANSCRIBED, CLIPS_CREATED, COMPLETED, FAILED
                local_video_path TEXT,
                transcript_source TEXT,                    -- 'youtube' or 'whisper'
                segments_json TEXT,                        -- JSON array of timestamped segments
                last_error TEXT,
                retry_count INTEGER DEFAULT 0,
                discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                transcript_wait_started_at TIMESTAMP,
                transcript_wait_deadline TIMESTAMP,
                next_caption_probe_at TIMESTAMP,
                ai_attempt_count INTEGER DEFAULT 0,
                next_ai_attempt_at TIMESTAMP,
                last_ai_error TEXT,
                source_sha256 TEXT,
                source_file_size INTEGER,
                user_id TEXT
            );
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_youtube_status 
            ON youtube_inbox (status, updated_at);
        """)

        # Migration: Ensure dispatch_id, session_id, sequence_number, transcript wait, and AI retry columns exist if table was created in older schema
        cursor.execute("PRAGMA table_info(youtube_inbox);")
        yt_cols = [col[1] for col in cursor.fetchall()]
        if "dispatch_id" not in yt_cols:
            cursor.execute("ALTER TABLE youtube_inbox ADD COLUMN dispatch_id TEXT;")
        if "session_id" not in yt_cols:
            cursor.execute("ALTER TABLE youtube_inbox ADD COLUMN session_id TEXT;")
        if "sequence_number" not in yt_cols:
            cursor.execute("ALTER TABLE youtube_inbox ADD COLUMN sequence_number INTEGER;")
        if "transcript_wait_started_at" not in yt_cols:
            cursor.execute("ALTER TABLE youtube_inbox ADD COLUMN transcript_wait_started_at TIMESTAMP;")
        if "transcript_wait_deadline" not in yt_cols:
            cursor.execute("ALTER TABLE youtube_inbox ADD COLUMN transcript_wait_deadline TIMESTAMP;")
        if "next_caption_probe_at" not in yt_cols:
            cursor.execute("ALTER TABLE youtube_inbox ADD COLUMN next_caption_probe_at TIMESTAMP;")
        if "ai_attempt_count" not in yt_cols:
            cursor.execute("ALTER TABLE youtube_inbox ADD COLUMN ai_attempt_count INTEGER DEFAULT 0;")
        if "next_ai_attempt_at" not in yt_cols:
            cursor.execute("ALTER TABLE youtube_inbox ADD COLUMN next_ai_attempt_at TIMESTAMP;")
        if "last_ai_error" not in yt_cols:
            cursor.execute("ALTER TABLE youtube_inbox ADD COLUMN last_ai_error TEXT;")
        if "source_sha256" not in yt_cols:
            cursor.execute("ALTER TABLE youtube_inbox ADD COLUMN source_sha256 TEXT;")
        if "source_file_size" not in yt_cols:
            cursor.execute("ALTER TABLE youtube_inbox ADD COLUMN source_file_size INTEGER;")
        if "user_id" not in yt_cols:
            cursor.execute("ALTER TABLE youtube_inbox ADD COLUMN user_id TEXT;");
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_youtube_user_id ON youtube_inbox (user_id);")

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_youtube_dispatch_id 
            ON youtube_inbox (dispatch_id);
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_youtube_session_id 
            ON youtube_inbox (session_id);
        """)

        # Migration: Ensure caption_data and rendered_layout_mode columns exist on clips table
        cursor.execute("PRAGMA table_info(clips);")
        clip_cols = [col[1] for col in cursor.fetchall()]
        if "caption_data" not in clip_cols:
            cursor.execute("ALTER TABLE clips ADD COLUMN caption_data TEXT;")
        if "rendered_layout_mode" not in clip_cols:
            cursor.execute("ALTER TABLE clips ADD COLUMN rendered_layout_mode TEXT;")
            cursor.execute("UPDATE clips SET rendered_layout_mode = layout_mode WHERE rendered_layout_mode IS NULL AND video_path IS NOT NULL;")
        if "render_stale" not in clip_cols:
            cursor.execute("ALTER TABLE clips ADD COLUMN render_stale INTEGER NOT NULL DEFAULT 0;")
        if "padded_start" not in clip_cols:
            cursor.execute("ALTER TABLE clips ADD COLUMN padded_start REAL;")
        if "padded_end" not in clip_cols:
            cursor.execute("ALTER TABLE clips ADD COLUMN padded_end REAL;")
        if "ai_suggested_start_time" not in clip_cols:
            cursor.execute("ALTER TABLE clips ADD COLUMN ai_suggested_start_time REAL;")
        if "ai_suggested_end_time" not in clip_cols:
            cursor.execute("ALTER TABLE clips ADD COLUMN ai_suggested_end_time REAL;")
        if "range_revision" not in clip_cols:
            cursor.execute("ALTER TABLE clips ADD COLUMN range_revision INTEGER NOT NULL DEFAULT 0;")
        if "caption_range_revision" not in clip_cols:
            cursor.execute("ALTER TABLE clips ADD COLUMN caption_range_revision INTEGER;")
        if "caption_range_start" not in clip_cols:
            cursor.execute("ALTER TABLE clips ADD COLUMN caption_range_start REAL;")
        if "caption_range_end" not in clip_cols:
            cursor.execute("ALTER TABLE clips ADD COLUMN caption_range_end REAL;")
        if "caption_timing_warning" not in clip_cols:
            cursor.execute("ALTER TABLE clips ADD COLUMN caption_timing_warning TEXT;")
        if "user_id" not in clip_cols:
            cursor.execute("ALTER TABLE clips ADD COLUMN user_id TEXT;");
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_clips_user_id ON clips (user_id);")

        # Suggestions are immutable candidate metadata. Legacy selected ranges
        # are the only safe backfill source; never rewrite their active bounds.
        cursor.execute("""
            UPDATE clips
            SET ai_suggested_start_time = COALESCE(ai_suggested_start_time, start_time),
                ai_suggested_end_time = COALESCE(ai_suggested_end_time, end_time)
            WHERE ai_suggested_start_time IS NULL OR ai_suggested_end_time IS NULL
        """)
        # Legacy caption tracks used the active clip's relative timebase. Record
        # that anchor once so later trims can rebase saved words safely.
        cursor.execute("""
            UPDATE clips
            SET caption_range_revision = COALESCE(caption_range_revision, range_revision, 0),
                caption_range_start = COALESCE(caption_range_start, start_time),
                caption_range_end = COALESCE(caption_range_end, end_time)
            WHERE caption_data IS NOT NULL AND caption_range_revision IS NULL
        """)

        # Migration: Ensure outbox columns exist if created with older schema
        cursor.execute("PRAGMA table_info(publishing_outbox);")
        outbox_cols = [col[1] for col in cursor.fetchall()]
        if "updated_at" not in outbox_cols:
            cursor.execute("ALTER TABLE publishing_outbox ADD COLUMN updated_at TIMESTAMP;")
        if "attempt_started_at" not in outbox_cols:
            cursor.execute("ALTER TABLE publishing_outbox ADD COLUMN attempt_started_at TIMESTAMP;")
        if "next_retry_at" not in outbox_cols:
            cursor.execute("ALTER TABLE publishing_outbox ADD COLUMN next_retry_at TIMESTAMP;")
        if "account_id" not in outbox_cols:
            cursor.execute("ALTER TABLE publishing_outbox ADD COLUMN account_id TEXT;")
        if "user_id" not in outbox_cols:
            cursor.execute("ALTER TABLE publishing_outbox ADD COLUMN user_id TEXT;");
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_outbox_user_id ON publishing_outbox (user_id);")

        # Default settings if not already present
        cursor.execute("""
            INSERT OR IGNORE INTO settings (key, value) VALUES
            ('publish_mode', ?),
            ('auto_process', 'true'),
            ('youtube_enabled', 'true'),
            ('instagram_enabled', 'true'),
            ('youtube_inbox_enabled', 'true'),
            ('youtube_inbox_marker', '[DISPATCH]');
        """, (DEFAULT_PUBLISH_MODE,))

        conn.commit()


# --- Database Helper Functions ---

def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()


def create_session(notes: str = "", session_id: Optional[str] = None) -> str:
    if not session_id:
        session_id = f"sess_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO sessions (id, status, notes) VALUES (?, 'recording', ?)",
            (session_id, notes)
        )
        conn.commit()
    return session_id


def register_chunk(session_id: Optional[str], filename: str, filepath: str, file_hash: str) -> str:
    chunk_id = f"chk_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if session_id:
            cursor.execute("INSERT OR IGNORE INTO sessions (id, status) VALUES (?, 'recording')", (session_id,))
        cursor.execute("""
            INSERT INTO chunks (id, session_id, filename, filepath, file_hash, status)
            VALUES (?, ?, ?, ?, ?, 'incoming')
        """, (chunk_id, session_id, filename, filepath, file_hash))
        conn.commit()
    return chunk_id


def get_chunk_by_id(chunk_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_chunk_by_file_hash(file_hash: str) -> Optional[Dict[str, Any]]:
    """Retrieve chunk by its file_hash identifier."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM chunks WHERE file_hash = ?", (file_hash,))
        row = cursor.fetchone()
        return dict(row) if row else None


def update_chunk_metadata(chunk_id: str, duration: float, width: int, height: int, aspect_ratio: str, status: str = 'verified'):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE chunks 
            SET duration = ?, width = ?, height = ?, aspect_ratio = ?, status = ?
            WHERE id = ?
        """, (duration, width, height, aspect_ratio, status, chunk_id))
        conn.commit()


def save_transcript(chunk_id: str, session_id: Optional[str], full_text: str, segments: List[Dict[str, Any]]) -> str:
    transcript_id = f"tx_{uuid.uuid4().hex[:8]}"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Enforce idempotency: one authoritative transcript per chunk_id
        cursor.execute("DELETE FROM transcripts WHERE chunk_id = ?", (chunk_id,))
        cursor.execute("""
            INSERT INTO transcripts (id, chunk_id, session_id, full_text, segments_json)
            VALUES (?, ?, ?, ?, ?)
        """, (transcript_id, chunk_id, session_id, full_text, json.dumps(segments, ensure_ascii=False)))
        cursor.execute("UPDATE chunks SET status = 'transcribed' WHERE id = ?", (chunk_id,))
        conn.commit()
    return transcript_id


def save_clip(
    session_id: Optional[str],
    chunk_id: Optional[str],
    start_time: float,
    end_time: float,
    title: str,
    hook: str,
    description: str,
    hashtags: str,
    virality_score: int,
    layout_mode: str,
    publish_mode: str = "private",
    platform_targets: str = "youtube",
    rendered_layout_mode: Optional[str] = None,
    duration: Optional[float] = None,
    padded_start: Optional[float] = None,
    padded_end: Optional[float] = None
) -> str:
    clip_id = f"clip_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    final_duration = round(float(duration), 2) if duration is not None else round(end_time - start_time, 2)
    p_start = padded_start if padded_start is not None else max(0.0, start_time - 4.0)
    p_end = padded_end if padded_end is not None else end_time + 4.0

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO clips (
                id, session_id, chunk_id, start_time, end_time,
                ai_suggested_start_time, ai_suggested_end_time, range_revision, duration,
                title, hook, description, hashtags, virality_score,
                layout_mode, rendered_layout_mode, status, publish_mode, platform_targets,
                padded_start, padded_end
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, 'ready_review', ?, ?, ?, ?)
        """, (
            clip_id, session_id, chunk_id, start_time, end_time,
            start_time, end_time, final_duration,
            title, hook, description, hashtags, virality_score,
            layout_mode, rendered_layout_mode, publish_mode, platform_targets,
            p_start, p_end
        ))
        conn.commit()
    return clip_id


def update_clip_trim(
    clip_id: str,
    start_time: float,
    end_time: float,
    source_duration: Optional[float] = None,
    expected_range_revision: Optional[int] = None,
) -> bool:
    """Persist a source-relative range within source and product duration limits."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT cl.video_path, cl.start_time, cl.end_time, cl.range_revision,
                   ch.duration AS source_duration
            FROM clips cl LEFT JOIN chunks ch ON ch.id = cl.chunk_id
            WHERE cl.id = ?
        """, (clip_id,))
        row = cursor.fetchone()
        if not row:
            return False
        if expected_range_revision is not None and int(row["range_revision"] or 0) != expected_range_revision:
            return False

        known_source_duration = source_duration
        if known_source_duration is None and row["source_duration"] is not None:
            known_source_duration = float(row["source_duration"])
        if (
            known_source_duration is None
            or not math.isfinite(float(known_source_duration))
            or known_source_duration <= 0
            or not math.isfinite(float(start_time))
            or not math.isfinite(float(end_time))
        ):
            return False

        clamped_start = round(float(start_time), 3)
        clamped_end = round(float(end_time), 3)
        new_duration = clamped_end - clamped_start
        if (
            clamped_start < 0
            or clamped_end > known_source_duration
            or new_duration < MIN_CLIP_DURATION
            or new_duration > MAX_CLIP_DURATION
        ):
            return False

        new_duration = round(new_duration, 2)
        changed = (
            abs(clamped_start - float(row["start_time"])) > 0.001
            or abs(clamped_end - float(row["end_time"])) > 0.001
        )
        cursor.execute("""
            UPDATE clips 
            SET start_time = ?, end_time = ?, duration = ?,
                range_revision = range_revision + ?,
                render_stale = CASE WHEN ? = 1 AND video_path IS NOT NULL THEN 1 ELSE render_stale END,
                caption_timing_warning = CASE WHEN ? = 1 THEN NULL ELSE caption_timing_warning END
            WHERE id = ? AND range_revision = ?
        """, (
            clamped_start, clamped_end, new_duration, int(changed), int(changed), int(changed),
            clip_id, int(row["range_revision"] or 0),
        ))
        conn.commit()
        return cursor.rowcount == 1


def update_clip_media(
    clip_id: str,
    video_path: str,
    thumbnail_path: Optional[str],
    rendered_layout_mode: Optional[str] = None,
    expected_range_revision: Optional[int] = None,
) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        revision_clause = " AND range_revision = ?" if expected_range_revision is not None else ""
        if rendered_layout_mode:
            cursor.execute("""
                UPDATE clips 
                SET video_path = ?, thumbnail_path = ?, rendered_layout_mode = ?, render_stale = 0, status = 'ready_review'
                WHERE id = ?""" + revision_clause,
                (video_path, thumbnail_path, rendered_layout_mode, clip_id)
                + ((expected_range_revision,) if expected_range_revision is not None else ()))
        else:
            cursor.execute("""
                UPDATE clips 
                SET video_path = ?, thumbnail_path = ?, status = 'ready_review'
                WHERE id = ?""" + revision_clause,
                (video_path, thumbnail_path, clip_id)
                + ((expected_range_revision,) if expected_range_revision is not None else ()))
        conn.commit()
        return cursor.rowcount == 1


def approve_clip(
    clip_id: str,
    custom_title: Optional[str] = None,
    custom_tags: Optional[str] = None,
    custom_mode: Optional[str] = None,
    custom_platforms: Optional[str] = None,
    custom_layout: Optional[str] = None,
    custom_accounts: Optional[Dict[str, str]] = None
):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM clips WHERE id = ?", (clip_id,))
        clip = cursor.fetchone()
        if not clip:
            return

        title = custom_title if custom_title is not None else clip["title"]
        hashtags = custom_tags if custom_tags is not None else clip["hashtags"]
        publish_mode = custom_mode if custom_mode is not None else clip["publish_mode"]
        target_platforms = custom_platforms if custom_platforms is not None else (clip["platform_targets"] or "youtube,instagram")
        layout_mode = custom_layout if custom_layout is not None else (clip["layout_mode"] or "fit_blur")

        cursor.execute("""
            UPDATE clips 
            SET status = 'approved', title = ?, hashtags = ?, publish_mode = ?, platform_targets = ?, layout_mode = ?, approved_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (title, hashtags, publish_mode, target_platforms, layout_mode, clip_id))

        # Enqueue to publishing outbox for each selected platform with its bound account_id
        clip_user_id = clip["user_id"] if "user_id" in clip.keys() else None
        platforms = [p.strip() for p in target_platforms.split(",") if p.strip()]
        account_map = custom_accounts or {}
        for plat in platforms:
            idempotency_key = f"{clip_id}_{plat}"
            acct_id = account_map.get(plat) or account_map.get(f"{plat}_shorts") or account_map.get("youtube" if plat == "youtube_shorts" else plat)
            cursor.execute("""
                INSERT OR IGNORE INTO publishing_outbox (
                    id, clip_id, user_id, platform, account_id, status, publish_mode, idempotency_key
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
            """, (f"pub_{uuid.uuid4().hex[:8]}", clip_id, clip_user_id, plat, acct_id, publish_mode, idempotency_key))

        conn.commit()


def get_clip_caption_data(clip_id: str) -> Optional[str]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT caption_data FROM clips WHERE id = ?", (clip_id,))
        row = cursor.fetchone()
        return row["caption_data"] if row and row["caption_data"] else None


def save_clip_caption_data(
    clip_id: str,
    caption_json: str,
    expected_range_revision: Optional[int] = None,
    source_range_start: Optional[float] = None,
    source_range_end: Optional[float] = None,
    timing_warning: Optional[str] = None,
) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if expected_range_revision is None or source_range_start is None or source_range_end is None:
            cursor.execute(
                "SELECT range_revision, start_time, end_time FROM clips WHERE id = ?", (clip_id,)
            )
            clip = cursor.fetchone()
            if not clip:
                return False
            if expected_range_revision is None:
                expected_range_revision = int(clip["range_revision"] or 0)
            if source_range_start is None:
                source_range_start = float(clip["start_time"] or 0.0)
            if source_range_end is None:
                source_range_end = float(clip["end_time"] or 0.0)
        cursor.execute("""
            UPDATE clips
            SET caption_data = ?, caption_range_revision = ?, caption_range_start = ?, caption_range_end = ?,
                caption_timing_warning = ?
            WHERE id = ? AND range_revision = ?
        """, (
            caption_json, expected_range_revision, source_range_start, source_range_end,
            timing_warning, clip_id, expected_range_revision,
        ))
        conn.commit()
        return cursor.rowcount == 1


def get_clip_by_id(clip_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM clips WHERE id = ?", (clip_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def reject_clip(clip_id: str, reason: str = "User rejected") -> bool:
    """Reject a clip: immediately unlinks physical MP4 & thumbnail files from disk,
    records rejection in rejections table for negative preference learning,
    and updates clip status to 'rejected'.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM clips WHERE id = ?", (clip_id,))
        clip = cursor.fetchone()
        if not clip:
            return False

        # 1. Immediately delete physical rendered media files (MP4 & thumbnail) from disk
        for file_key in ("video_path", "thumbnail_path"):
            val = clip[file_key]
            if val:
                try:
                    p = Path(val)
                    if p.exists() and p.is_file():
                        p.unlink()
                        logger.info("Deleted %s (%s) for rejected clip %s", file_key, p.name, clip_id)
                except Exception as e:
                    logger.warning("Failed to delete %s for rejected clip %s: %s", file_key, clip_id, e)

        # 2. Record in rejections table for negative preference learning
        cursor.execute("""
            INSERT INTO rejections (id, clip_id, title, transcript_snippet, reason)
            VALUES (?, ?, ?, ?, ?)
        """, (f"rej_{uuid.uuid4().hex[:8]}", clip_id, clip["title"], clip["hook"], reason))

        # 3. Mark status as 'rejected' and clear file paths
        cursor.execute("UPDATE clips SET status = 'rejected', video_path = NULL, thumbnail_path = NULL WHERE id = ?", (clip_id,))
        conn.commit()
        return True


def cleanup_expired_published_clips(retention_days: int = 7) -> int:
    """Purge heavy local rendered MP4 files for published clips older than retention_days.
    Strictly PRESERVES thumbnail_path, remote_url, and clip metadata so creator still
    has full thumbnail preview and redirection to the published live video.
    """
    purged_count = 0
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Find published clips with published_at older than retention_days that still have a local video_path
        cursor.execute("""
            SELECT id, video_path, thumbnail_path, published_at, created_at 
            FROM clips 
            WHERE status IN ('published', 'approved') 
              AND video_path IS NOT NULL 
              AND datetime(COALESCE(published_at, approved_at, created_at)) <= datetime('now', '-' || ? || ' days')
        """, (retention_days,))
        expired_clips = cursor.fetchall()

        for c in expired_clips:
            vpath = c["video_path"]
            if vpath:
                try:
                    vp = Path(vpath)
                    if vp.exists() and vp.is_file():
                        vp.unlink()
                        logger.info("Purged expired local video file %s (Published > %d days ago)", vp.name, retention_days)
                except Exception as e:
                    logger.warning("Failed to unlink video file %s: %s", vpath, e)
            
            # Clear video_path from DB while PRESERVING thumbnail_path and all clip records
            cursor.execute("UPDATE clips SET video_path = NULL WHERE id = ?", (c["id"],))
            purged_count += 1

        if purged_count > 0:
            conn.commit()

    return purged_count


def get_all_rejections(limit: int = 20) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM rejections ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]


def get_clips_for_review(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if user_id:
            cursor.execute("SELECT * FROM clips WHERE status = 'ready_review' AND user_id = ? ORDER BY created_at DESC", (user_id,))
        else:
            cursor.execute("SELECT * FROM clips WHERE status = 'ready_review' ORDER BY created_at DESC")
        return [dict(row) for row in cursor.fetchall()]


def get_outbox_queue() -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT po.*, c.title, c.description, c.hashtags, c.video_path, c.thumbnail_path
            FROM publishing_outbox po
            JOIN clips c ON po.clip_id = c.id
            WHERE po.status = 'queued'
              AND (po.next_retry_at IS NULL OR po.next_retry_at <= CURRENT_TIMESTAMP)
            ORDER BY po.created_at ASC
        """)
        return [dict(row) for row in cursor.fetchall()]


def get_active_pipeline_jobs() -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT j.*, c.filename, c.duration, c.aspect_ratio
            FROM pipeline_jobs j
            LEFT JOIN chunks c ON j.chunk_id = c.id
            ORDER BY j.created_at DESC
            LIMIT 50
        """)
        return [dict(row) for row in cursor.fetchall()]


# --- YouTube Inbox Helpers ---

def register_youtube_video(
    video_id: str,
    title: str,
    channel_id: Optional[str] = None,
    upload_time: Optional[str] = None,
    duration: float = 0.0,
    dispatch_id: Optional[str] = None,
    session_id: Optional[str] = None,
    sequence_number: Optional[int] = None,
    source_sha256: Optional[str] = None,
    source_file_size: Optional[int] = None,
    user_id: Optional[str] = None
) -> bool:
    """Register discovered YouTube video in inbox idempotently.
    Returns True if newly inserted, False if already present.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT video_id FROM youtube_inbox WHERE video_id = ?", (video_id,))
        if cursor.fetchone():
            return False

        if session_id:
            cursor.execute("INSERT OR IGNORE INTO sessions (id, status) VALUES (?, 'recording')", (session_id,))

        cursor.execute("""
            INSERT INTO youtube_inbox (video_id, dispatch_id, session_id, sequence_number, title, channel_id, upload_time, duration, status, source_sha256, source_file_size, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'DISCOVERED', ?, ?, ?)
        """, (video_id, dispatch_id, session_id, sequence_number, title, channel_id, upload_time, duration, source_sha256, source_file_size, user_id))
        conn.commit()
        return True


def get_youtube_video(video_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve YouTube inbox record by video ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM youtube_inbox WHERE video_id = ?", (video_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_youtube_video_by_dispatch_id(dispatch_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve YouTube inbox record by unique phone dispatch_id."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM youtube_inbox WHERE dispatch_id = ?", (dispatch_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def update_youtube_video(
    video_id: str,
    status: str,
    dispatch_id: Optional[str] = None,
    session_id: Optional[str] = None,
    sequence_number: Optional[int] = None,
    local_video_path: Optional[str] = None,
    transcript_source: Optional[str] = None,
    segments_json: Optional[str] = None,
    last_error: Optional[str] = None,
    transcript_wait_started_at: Optional[str] = None,
    transcript_wait_deadline: Optional[str] = None,
    next_caption_probe_at: Optional[str] = None,
    ai_attempt_count: Optional[int] = None,
    next_ai_attempt_at: Optional[str] = None,
    last_ai_error: Optional[str] = None,
    source_sha256: Optional[str] = None,
    source_file_size: Optional[int] = None,
    user_id: Optional[str] = None
):
    """Update status, paths, errors, or retry schedule on YouTube inbox item."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        updates = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
        params: List[Any] = [status]

        if dispatch_id is not None:
            updates.append("dispatch_id = ?")
            params.append(dispatch_id)

        if session_id is not None:
            updates.append("session_id = ?")
            params.append(session_id)
            cursor.execute("INSERT OR IGNORE INTO sessions (id, status) VALUES (?, 'recording')", (session_id,))

        if sequence_number is not None:
            updates.append("sequence_number = ?")
            params.append(sequence_number)

        if local_video_path is not None:
            updates.append("local_video_path = ?")
            params.append(local_video_path)

        if transcript_source is not None:
            updates.append("transcript_source = ?")
            params.append(transcript_source)

        if segments_json is not None:
            updates.append("segments_json = ?")
            params.append(segments_json)

        if transcript_wait_started_at is not None:
            updates.append("transcript_wait_started_at = ?")
            params.append(transcript_wait_started_at)

        if transcript_wait_deadline is not None:
            updates.append("transcript_wait_deadline = ?")
            params.append(transcript_wait_deadline)

        if next_caption_probe_at is not None:
            updates.append("next_caption_probe_at = ?")
            params.append(next_caption_probe_at)

        if ai_attempt_count is not None:
            updates.append("ai_attempt_count = ?")
            params.append(ai_attempt_count)

        if next_ai_attempt_at is not None:
            updates.append("next_ai_attempt_at = ?")
            params.append(next_ai_attempt_at)

        if last_ai_error is not None:
            updates.append("last_ai_error = ?")
            params.append(last_ai_error)

        if source_sha256 is not None:
            updates.append("source_sha256 = ?")
            params.append(source_sha256)

        if source_file_size is not None:
            updates.append("source_file_size = ?")
            params.append(source_file_size)

        if user_id is not None:
            updates.append("user_id = ?")
            params.append(user_id)

        if last_error is not None:
            updates.append("last_error = ?")
            params.append(last_error)
            if status == "FAILED":
                updates.append("retry_count = retry_count + 1")

        query = f"UPDATE youtube_inbox SET {', '.join(updates)} WHERE video_id = ?"
        params.append(video_id)
        cursor.execute(query, params)
        conn.commit()


def list_youtube_inbox(status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """List YouTube inbox items with optional status filtering."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if status:
            cursor.execute(
                "SELECT * FROM youtube_inbox WHERE status = ? ORDER BY discovered_at DESC LIMIT ?",
                (status, limit)
            )
        else:
            cursor.execute(
                "SELECT * FROM youtube_inbox ORDER BY discovered_at DESC LIMIT ?",
                (limit,)
            )
        return [dict(row) for row in cursor.fetchall()]


def delete_youtube_inbox_item(video_id: str) -> bool:
    """Delete an item from youtube_inbox by video_id, dispatch_id, or session_id."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM youtube_inbox WHERE video_id = ? OR dispatch_id = ? OR session_id = ?",
            (video_id, video_id, video_id)
        )
        conn.commit()
        return cursor.rowcount > 0


def get_session_inbox_items(session_id: str) -> List[Dict[str, Any]]:
    """Return all youtube_inbox entries for a session ordered by sequence_number."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM youtube_inbox WHERE session_id = ? ORDER BY sequence_number ASC, discovered_at ASC",
            (session_id,)
        )
        return [dict(row) for row in cursor.fetchall()]


def is_youtube_video_processed(video_id: str) -> bool:
    """Check if a video has already been ingested or completely processed."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status, local_video_path FROM youtube_inbox WHERE video_id = ?",
            (video_id,)
        )
        row = cursor.fetchone()
        if not row:
            return False
        # If completed or analyzing downstream, return True directly
        if row["status"] in ("ANALYZING", "CLIPS_DEFINED", "CLIPS_CREATED", "COMPLETED"):
            return True
        # If in progress with downloaded media, check if local file is intact
        if row["status"] in ("DOWNLOADED", "WAITING_FOR_TRANSCRIPT", "TRANSCRIBED", "TRANSCRIPT_FETCHED"):
            from pathlib import Path
            path = Path(row["local_video_path"]) if row["local_video_path"] else None
            return bool(path and path.exists() and path.stat().st_size > 0)
        return False


def save_clip_definitions(
    chunk_id: Optional[str],
    session_id: Optional[str],
    video_id: str,
    clip_defs: List[Dict[str, Any]],
    publish_mode: str = "private",
    platform_targets: str = "youtube",
    user_id: Optional[str] = None
) -> List[str]:
    """Atomically persist validated clip definitions in a single transaction.
    
    1. Removes any prior unrendered/candidate clips for this chunk to prevent duplicate clips on retry.
    2. Inserts all validated clip definitions into `clips` with video_path=None, thumbnail_path=None, status='ready_review'.
    3. Updates youtube_inbox status to 'CLIPS_DEFINED'.
    All performed in a single database transaction.
    """
    saved_clip_ids: List[str] = []
    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")

    effective_user_id = user_id
    if not effective_user_id and video_id:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT user_id FROM youtube_inbox WHERE video_id = ?", (video_id,))
            r = c.fetchone()
            if r and r["user_id"]:
                effective_user_id = r["user_id"]

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 1. Clean up prior candidate/unrendered clips for this chunk to ensure idempotency
        if chunk_id:
            cursor.execute("""
                DELETE FROM clips 
                WHERE chunk_id = ? AND (video_path IS NULL OR video_path = '') AND status = 'ready_review'
            """, (chunk_id,))

        # 2. Insert all validated clips
        for idx, c in enumerate(clip_defs):
            clip_id = f"clip_{now_str}_{uuid.uuid4().hex[:6]}"
            start_t = float(c["start_time"])
            end_t = float(c["end_time"])
            duration = round(float(c.get("duration", end_t - start_t)), 2)
            title = c.get("title", "Dispatch Highlight")
            hook = c.get("hook", "")
            description = c.get("description", "")
            hashtags = c.get("hashtags", "#Shorts #Reels")
            virality_score = int(c.get("virality_score", 70))
            layout_mode = c.get("layout_recommendation", "fit_blur")

            p_start = float(c.get("padded_start", max(0.0, start_t - 4.0)))
            p_end = float(c.get("padded_end", end_t + 4.0))

            cursor.execute("""
                INSERT INTO clips (
                    id, session_id, chunk_id, start_time, end_time, duration,
                    ai_suggested_start_time, ai_suggested_end_time, range_revision,
                    title, hook, description, hashtags, virality_score,
                    layout_mode, status, publish_mode, platform_targets,
                    video_path, thumbnail_path, padded_start, padded_end, user_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, 'ready_review', ?, ?, NULL, NULL, ?, ?, ?)
            """, (
                clip_id, session_id, chunk_id, start_t, end_t, duration,
                start_t, end_t,
                title, hook, description, hashtags, virality_score,
                layout_mode, publish_mode, platform_targets,
                p_start, p_end, effective_user_id
            ))
            saved_clip_ids.append(clip_id)

        # 3. Atomically transition youtube_inbox to CLIPS_DEFINED
        cursor.execute("""
            UPDATE youtube_inbox 
            SET status = 'CLIPS_DEFINED', updated_at = CURRENT_TIMESTAMP
            WHERE video_id = ?
        """, (video_id,))

        conn.commit()

    return saved_clip_ids


def get_queued_transcription_clips(limit: int = 50, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve clips waiting in FIFO backlog for transcription quota reset."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if user_id:
            cursor.execute("""
                SELECT * FROM clips 
                WHERE status = 'queued_transcription' AND user_id = ?
                ORDER BY created_at ASC 
                LIMIT ?
            """, (user_id, limit))
        else:
            cursor.execute("""
                SELECT * FROM clips 
                WHERE status = 'queued_transcription' 
                ORDER BY created_at ASC 
                LIMIT ?
            """, (limit,))
        return [dict(r) for r in cursor.fetchall()]


def get_transcription_queue_stats() -> Dict[str, Any]:
    """Return ready count, backlog queue depth, and daily transcribe quota usage."""
    from datetime import datetime, timezone
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    current_date = get_setting("transcribe_quota_date", "")
    current_count = int(get_setting("transcribe_quota_count", "0") or 0) if current_date == today_utc else 0

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM clips WHERE status = 'queued_transcription'")
        queued_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM clips WHERE status = 'ready_review'")
        ready_count = cursor.fetchone()[0]

    return {
        "ready_count": ready_count,
        "queued_count": queued_count,
        "daily_quota_used": current_count,
        "daily_quota_max": 25,
        "quota_date": today_utc
    }


