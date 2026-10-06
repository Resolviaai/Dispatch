"""Mobile storage retention and cleanup manager.
Enforces strict cleanup policies: never deletes a phone file merely on an HTTP 200.
Requires verified byte count and SHA-256 confirmation from the laptop before deleting local media.
"""
import logging
from pathlib import Path
from typing import Dict, Any, List

from dispatch_mobile.db import get_mobile_db

logger = logging.getLogger("dispatch_mobile.retention")


def cleanup_verified_segments() -> int:
    """Delete local video files for segments whose copies have been strictly verified by the laptop.
    
    Returns:
        Number of cleaned-up local files.
    """
    cleaned_count = 0

    with get_mobile_db() as conn:
        cursor = conn.cursor()

        # Check policy
        cursor.execute("SELECT value FROM mobile_settings WHERE key = 'cleanup_policy'")
        row = cursor.fetchone()
        policy = row["value"] if row else "DELETE_AFTER_VERIFIED_COPY"

        if policy == "NEVER_DELETE":
            logger.debug("Cleanup policy is NEVER_DELETE; skipping phone storage cleanup.")
            return 0

        # Find segments confirmed VERIFIED_BY_LAPTOP that haven't been deleted yet
        cursor.execute("""
            SELECT segment_id, filepath FROM mobile_segments
            WHERE status = 'VERIFIED_BY_LAPTOP'
        """)
        eligible = cursor.fetchall()

        for item in eligible:
            seg_id = item["segment_id"]
            file_path = Path(item["filepath"])

            if file_path.exists():
                try:
                    file_path.unlink()
                    logger.info("Safely deleted local segment %s (%s) after verified laptop receipt.",
                                seg_id, file_path.name)
                    cleaned_count += 1
                except Exception as e:
                    logger.warning("Failed to delete local segment file %s: %s", file_path.name, e)

            cursor.execute("""
                UPDATE mobile_segments
                SET status = 'DELETED_FROM_PHONE'
                WHERE segment_id = ?
            """, (seg_id,))

            # Remove from outbox
            cursor.execute("DELETE FROM mobile_outbox WHERE segment_id = ?", (seg_id,))

    return cleaned_count
