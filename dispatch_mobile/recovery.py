"""Mobile Crash & Battery Death Recovery Manager.
Executes automatically on app startup to recover unfinished sessions, finalize interrupted segments,
and re-enqueue pending uploads without requiring any user intervention.
"""
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from dispatch_mobile.db import get_mobile_db, calculate_sha256, MOBILE_STORAGE_DIR

logger = logging.getLogger("dispatch_mobile.recovery")


def recover_mobile_state(storage_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Scan and recover unfinished recording sessions after phone reboot or crash."""
    recordings_dir = storage_dir or (MOBILE_STORAGE_DIR / "recordings")
    recovered_segments = []
    quarantined_segments = []
    sealed_sessions = []

    with get_mobile_db() as conn:
        cursor = conn.cursor()

        # 1. Find sessions interrupted while RECORDING
        cursor.execute("SELECT session_id FROM mobile_sessions WHERE status = 'RECORDING'")
        interrupted_sessions = [row["session_id"] for row in cursor.fetchall()]

        for sess_id in interrupted_sessions:
            logger.info("Found interrupted session: %s. Performing crash recovery.", sess_id)

            # Find segments left in RECORDING status
            cursor.execute("""
                SELECT segment_id, sequence_number FROM mobile_segments
                WHERE session_id = ? AND status = 'RECORDING'
            """, (sess_id,))
            pending_segments = cursor.fetchall()

            for seg in pending_segments:
                seg_id = seg["segment_id"]
                tmp_file = recordings_dir / f"{seg_id}.tmp"
                mp4_file = recordings_dir / f"{seg_id}.mp4"

                if tmp_file.exists() and tmp_file.stat().st_size > 0:
                    # File has data: finalize it as a valid partial segment
                    file_size = tmp_file.stat().st_size
                    sha256 = calculate_sha256(tmp_file)
                    tmp_file.rename(mp4_file)

                    cursor.execute("""
                        UPDATE mobile_segments
                        SET status = 'QUEUED_FOR_UPLOAD',
                            file_size_bytes = ?,
                            sha256_hash = ?,
                            finalized_at = CURRENT_TIMESTAMP
                        WHERE segment_id = ?
                    """, (file_size, sha256, seg_id))

                    cursor.execute("""
                        INSERT OR REPLACE INTO mobile_outbox (
                            segment_id, session_id, remote_offset, status, updated_at
                        ) VALUES (?, ?, 0, 'QUEUED_FOR_UPLOAD', CURRENT_TIMESTAMP)
                    """, (seg_id, sess_id))

                    recovered_segments.append(seg_id)
                    logger.info("Successfully recovered segment %s (%d bytes)", seg_id, file_size)

                elif tmp_file.exists() and tmp_file.stat().st_size == 0:
                    # Empty file: safely delete
                    tmp_file.unlink()
                    cursor.execute("DELETE FROM mobile_segments WHERE segment_id = ?", (seg_id,))
                    quarantined_segments.append(seg_id)
                else:
                    cursor.execute("DELETE FROM mobile_segments WHERE segment_id = ?", (seg_id,))
                    quarantined_segments.append(seg_id)

            # Seal the session
            cursor.execute("""
                UPDATE mobile_sessions 
                SET status = 'FINALIZED',
                    total_segments = (SELECT COUNT(*) FROM mobile_segments WHERE session_id = ? AND status != 'RECORDING')
                WHERE session_id = ?
            """, (sess_id, sess_id))
            sealed_sessions.append(sess_id)

        # 2. Check for any finalized segments missing from outbox
        cursor.execute("""
            SELECT s.segment_id, s.session_id FROM mobile_segments s
            LEFT JOIN mobile_outbox o ON s.segment_id = o.segment_id
            WHERE s.status IN ('FINALIZED', 'QUEUED_FOR_UPLOAD') AND o.segment_id IS NULL
        """)
        orphaned_outbox = cursor.fetchall()
        for o in orphaned_outbox:
            cursor.execute("""
                INSERT OR REPLACE INTO mobile_outbox (
                    segment_id, session_id, remote_offset, status, updated_at
                ) VALUES (?, ?, 0, 'QUEUED_FOR_UPLOAD', CURRENT_TIMESTAMP)
            """, (o["segment_id"], o["session_id"]))
            logger.info("Re-enqueued orphaned segment %s into outbox", o["segment_id"])

    logger.info("Mobile crash recovery complete. Recovered %d segments across %d sessions.",
                len(recovered_segments), len(sealed_sessions))

    return {
        "recovered_segments": recovered_segments,
        "quarantined_segments": quarantined_segments,
        "sealed_sessions": sealed_sessions
    }
