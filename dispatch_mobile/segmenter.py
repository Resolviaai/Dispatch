"""Mobile Recording Segmenter.
Splits continuous recording into atomic, durable segments to guarantee crash and power safety.
If phone dies or crashes, at most the currently-active segment is affected; all previous segments survive intact.
"""
import uuid
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

from dispatch_mobile.db import get_mobile_db, calculate_sha256, MOBILE_STORAGE_DIR

logger = logging.getLogger("dispatch_mobile.segmenter")


class RecordingSessionManager:
    """Manages active mobile recording sessions and rolling segment creation."""

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = storage_dir or (MOBILE_STORAGE_DIR / "recordings")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.current_session_id: Optional[str] = None
        self.current_segment_id: Optional[str] = None
        self.current_sequence: int = 0
        self.current_tmp_path: Optional[Path] = None

    def start_session(self, notes: str = "") -> str:
        """Start a new recording session."""
        self.current_session_id = f"sess_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self.current_sequence = 0

        with get_mobile_db() as conn:
            conn.execute("""
                INSERT INTO mobile_sessions (session_id, status, total_segments, notes)
                VALUES (?, 'RECORDING', 0, ?)
            """, (self.current_session_id, notes))

        logger.info("Started mobile recording session %s", self.current_session_id)
        # Immediately start first segment
        self.start_next_segment()
        return self.current_session_id

    def start_next_segment(self) -> Dict[str, Any]:
        """Start a new rolling segment (.tmp) under the active session."""
        if not self.current_session_id:
            raise RuntimeError("Cannot start segment without an active session")

        # If there's an ongoing segment, finalize it first
        if self.current_segment_id and self.current_tmp_path and self.current_tmp_path.exists():
            self.finalize_current_segment()

        self.current_sequence += 1
        self.current_segment_id = f"{self.current_session_id}_seg_{self.current_sequence:04d}"
        self.current_tmp_path = self.storage_dir / f"{self.current_segment_id}.tmp"

        # Touch/create the temporary file
        self.current_tmp_path.touch()

        with get_mobile_db() as conn:
            conn.execute("""
                INSERT INTO mobile_segments (
                    segment_id, session_id, sequence_number, filename, filepath, status
                ) VALUES (?, ?, ?, ?, ?, 'RECORDING')
            """, (
                self.current_segment_id,
                self.current_session_id,
                self.current_sequence,
                f"{self.current_segment_id}.mp4",
                str(self.storage_dir / f"{self.current_segment_id}.mp4")
            ))

        logger.info("Started recording segment %s -> %s", self.current_segment_id, self.current_tmp_path.name)
        return {
            "session_id": self.current_session_id,
            "segment_id": self.current_segment_id,
            "tmp_path": self.current_tmp_path
        }

    def finalize_current_segment(self) -> Optional[Dict[str, Any]]:
        """Atomically finalize current temporary segment (.tmp -> .mp4) and enqueue for upload."""
        if not self.current_segment_id or not self.current_tmp_path:
            return None

        final_mp4_path = self.storage_dir / f"{self.current_segment_id}.mp4"

        if not self.current_tmp_path.exists() or self.current_tmp_path.stat().st_size == 0:
            logger.warning("Segment %s temp file is missing or empty, removing record", self.current_segment_id)
            with get_mobile_db() as conn:
                conn.execute("DELETE FROM mobile_segments WHERE segment_id = ?", (self.current_segment_id,))
            return None

        # 1. Compute checksum and file size
        file_size = self.current_tmp_path.stat().st_size
        sha256 = calculate_sha256(self.current_tmp_path)

        # 2. Atomic rename from .tmp to .mp4
        self.current_tmp_path.rename(final_mp4_path)

        # 3. Update SQLite status to FINALIZED & QUEUED_FOR_UPLOAD
        with get_mobile_db() as conn:
            conn.execute("""
                UPDATE mobile_segments
                SET status = 'QUEUED_FOR_UPLOAD',
                    file_size_bytes = ?,
                    sha256_hash = ?,
                    finalized_at = CURRENT_TIMESTAMP
                WHERE segment_id = ?
            """, (file_size, sha256, self.current_segment_id))

            # Enqueue to mobile outbox
            conn.execute("""
                INSERT OR REPLACE INTO mobile_outbox (
                    segment_id, session_id, remote_offset, status, updated_at
                ) VALUES (?, ?, 0, 'QUEUED_FOR_UPLOAD', CURRENT_TIMESTAMP)
            """, (self.current_segment_id, self.current_session_id))

            # Update session total segments
            conn.execute("""
                UPDATE mobile_sessions 
                SET total_segments = total_segments + 1 
                WHERE session_id = ?
            """, (self.current_session_id,))

        logger.info("Finalized segment %s (%d bytes, SHA256: %s)",
                    self.current_segment_id, file_size, sha256[:12])

        info = {
            "segment_id": self.current_segment_id,
            "session_id": self.current_session_id,
            "filepath": final_mp4_path,
            "size_bytes": file_size,
            "sha256": sha256
        }

        self.current_segment_id = None
        self.current_tmp_path = None
        return info

    def stop_session(self) -> Dict[str, Any]:
        """Stop recording session, finalize the last segment, and seal session as FINALIZED."""
        if not self.current_session_id:
            raise RuntimeError("No active recording session to stop")

        # Finalize the last active segment
        if self.current_segment_id and self.current_tmp_path and self.current_tmp_path.exists():
            self.finalize_current_segment()

        session_id = self.current_session_id
        with get_mobile_db() as conn:
            conn.execute("""
                UPDATE mobile_sessions 
                SET status = 'FINALIZED' 
                WHERE session_id = ?
            """, (session_id,))

            cursor = conn.cursor()
            cursor.execute("SELECT total_segments FROM mobile_sessions WHERE session_id = ?", (session_id,))
            total = cursor.fetchone()[0]

        logger.info("Recording session %s stopped and sealed (total %d segments)", session_id, total)

        self.current_session_id = None
        self.current_sequence = 0

        return {
            "session_id": session_id,
            "total_segments": total,
            "status": "FINALIZED"
        }
