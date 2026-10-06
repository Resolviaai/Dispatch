"""Database layer for Dispatch.
Provides thread-safe SQLite connection with WAL mode and schema management.
"""
import sqlite3
import json
import uuid
import contextlib
from datetime import datetime
from typing import Any, Dict, List, Optional
from pathlib import Path
from dispatch.config import DB_PATH, DATABASE_DIR, DEFAULT_PUBLISH_MODE

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


@contextlib.contextmanager
def get_db_connection():
    """Context manager yielding a thread-safe SQLite connection with auto-commit and guaranteed close."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
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
                duration REAL NOT NULL,
                title TEXT NOT NULL,
                hook TEXT,
                description TEXT,
                hashtags TEXT,
                virality_score INTEGER DEFAULT 0,
                layout_mode TEXT DEFAULT 'fit_blur', -- crop_follow, fit_blur, native_portrait
                status TEXT NOT NULL DEFAULT 'ready_review', -- ready_review, approved, rejected, published
                publish_mode TEXT NOT NULL DEFAULT 'private', -- private, public
                platform_targets TEXT DEFAULT 'youtube,instagram',
                video_path TEXT,
                thumbnail_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                approved_at TIMESTAMP,
                published_at TIMESTAMP,
                FOREIGN KEY (chunk_id) REFERENCES chunks (id) ON DELETE SET NULL
            );
        """)

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
                status TEXT NOT NULL DEFAULT 'queued', -- queued, uploading, published, failed
                publish_mode TEXT NOT NULL DEFAULT 'private', -- private, public
                attempt_count INTEGER DEFAULT 0,
                last_error TEXT,
                idempotency_key TEXT UNIQUE NOT NULL,
                remote_id TEXT,
                remote_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
                title TEXT NOT NULL,
                channel_id TEXT,
                upload_time TIMESTAMP,
                duration REAL DEFAULT 0.0,
                status TEXT NOT NULL DEFAULT 'DISCOVERED', -- DISCOVERED, DOWNLOADED, TRANSCRIPT_FETCHED, CLIPS_CREATED, COMPLETED, FAILED
                local_video_path TEXT,
                transcript_source TEXT,                    -- 'youtube' or 'whisper'
                segments_json TEXT,                        -- JSON array of timestamped segments
                last_error TEXT,
                retry_count INTEGER DEFAULT 0,
                discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_youtube_status 
            ON youtube_inbox (status, updated_at);
        """)

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


def create_session(notes: str = "") -> str:
    session_id = f"sess_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO sessions (id, status, notes) VALUES (?, 'recording', ?)",
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
    platform_targets: str = "youtube,instagram"
) -> str:
    clip_id = f"clip_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    duration = end_time - start_time
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO clips (
                id, session_id, chunk_id, start_time, end_time, duration,
                title, hook, description, hashtags, virality_score,
                layout_mode, status, publish_mode, platform_targets
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready_review', ?, ?)
        """, (
            clip_id, session_id, chunk_id, start_time, end_time, duration,
            title, hook, description, hashtags, virality_score,
            layout_mode, publish_mode, platform_targets
        ))
        conn.commit()
    return clip_id


def update_clip_media(clip_id: str, video_path: str, thumbnail_path: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE clips 
            SET video_path = ?, thumbnail_path = ?, status = 'ready_review'
            WHERE id = ?
        """, (video_path, thumbnail_path, clip_id))
        conn.commit()


def approve_clip(
    clip_id: str,
    custom_title: Optional[str] = None,
    custom_tags: Optional[str] = None,
    custom_mode: Optional[str] = None,
    custom_platforms: Optional[str] = None
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

        cursor.execute("""
            UPDATE clips 
            SET status = 'approved', title = ?, hashtags = ?, publish_mode = ?, platform_targets = ?, approved_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (title, hashtags, publish_mode, target_platforms, clip_id))

        # Enqueue to publishing outbox for each selected platform
        platforms = [p.strip() for p in target_platforms.split(",") if p.strip()]
        for plat in platforms:
            idempotency_key = f"{clip_id}_{plat}"
            cursor.execute("""
                INSERT OR IGNORE INTO publishing_outbox (
                    id, clip_id, platform, status, publish_mode, idempotency_key
                ) VALUES (?, ?, ?, 'queued', ?, ?)
            """, (f"pub_{uuid.uuid4().hex[:8]}", clip_id, plat, publish_mode, idempotency_key))

        conn.commit()


def reject_clip(clip_id: str, reason: str = "User rejected"):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM clips WHERE id = ?", (clip_id,))
        clip = cursor.fetchone()
        if not clip:
            return

        cursor.execute("UPDATE clips SET status = 'rejected' WHERE id = ?", (clip_id,))
        # Store in rejections for negative preference learning
        cursor.execute("""
            INSERT INTO rejections (id, clip_id, title, transcript_snippet, reason)
            VALUES (?, ?, ?, ?, ?)
        """, (f"rej_{uuid.uuid4().hex[:8]}", clip_id, clip["title"], clip["hook"], reason))
        conn.commit()


def get_all_rejections(limit: int = 20) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM rejections ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]


def get_clips_for_review() -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
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
    duration: float = 0.0
) -> bool:
    """Register discovered YouTube video in inbox idempotently.
    Returns True if newly inserted, False if already present.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT video_id FROM youtube_inbox WHERE video_id = ?", (video_id,))
        if cursor.fetchone():
            return False

        cursor.execute("""
            INSERT INTO youtube_inbox (video_id, title, channel_id, upload_time, duration, status)
            VALUES (?, ?, ?, ?, ?, 'DISCOVERED')
        """, (video_id, title, channel_id, upload_time, duration))
        conn.commit()
        return True


def get_youtube_video(video_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve YouTube inbox record by video ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM youtube_inbox WHERE video_id = ?", (video_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def update_youtube_video(
    video_id: str,
    status: str,
    local_video_path: Optional[str] = None,
    transcript_source: Optional[str] = None,
    segments_json: Optional[str] = None,
    last_error: Optional[str] = None
):
    """Update status, paths, or errors on YouTube inbox item."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        updates = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
        params: List[Any] = [status]

        if local_video_path is not None:
            updates.append("local_video_path = ?")
            params.append(local_video_path)

        if transcript_source is not None:
            updates.append("transcript_source = ?")
            params.append(transcript_source)

        if segments_json is not None:
            updates.append("segments_json = ?")
            params.append(segments_json)

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


def is_youtube_video_processed(video_id: str) -> bool:
    """Check if a video has already been completely processed or clips created."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status FROM youtube_inbox WHERE video_id = ?",
            (video_id,)
        )
        row = cursor.fetchone()
        if not row:
            return False
        return row["status"] in ("CLIPS_CREATED", "COMPLETED")

