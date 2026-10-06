"""Mobile Resumable Sync Client.
Pushes queued segments to the laptop using byte-offset chunking and queries reconciliation status.
Resumes interrupted uploads from the exact byte offset without restarting from 0%.
"""
import time
import logging
import requests
from pathlib import Path
from typing import Dict, Any, List, Optional

from dispatch_mobile.db import get_mobile_db
from dispatch_mobile.retention import cleanup_verified_segments

logger = logging.getLogger("dispatch_mobile.sync_client")

DEFAULT_CHUNK_SIZE = 512 * 1024  # 512 KB chunks for smooth mobile progress


class MobileSyncClient:
    """Handles network synchronization between the mobile outbox and the laptop receiver."""

    def __init__(
        self,
        laptop_url: Optional[str] = None,
        auth_token: Optional[str] = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE
    ):
        self.chunk_size = chunk_size
        self._load_config(laptop_url, auth_token)

    def _load_config(self, laptop_url: Optional[str], auth_token: Optional[str]):
        with get_mobile_db() as conn:
            cursor = conn.cursor()
            if not laptop_url:
                cursor.execute("SELECT value FROM mobile_settings WHERE key = 'laptop_url'")
                row = cursor.fetchone()
                self.laptop_url = row["value"] if row else "http://127.0.0.1:8000"
            else:
                self.laptop_url = laptop_url

            if not auth_token:
                cursor.execute("SELECT value FROM mobile_settings WHERE key = 'auth_token'")
                row = cursor.fetchone()
                self.auth_token = row["value"] if row else "dispatch_paired_secret_default"
            else:
                self.auth_token = auth_token

    def reconcile_with_laptop(self) -> Dict[str, Any]:
        """Send local manifest to laptop and reconcile verified segments & partial offsets."""
        with get_mobile_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT segment_id, session_id, file_size_bytes, sha256_hash 
                FROM mobile_segments 
                WHERE status IN ('FINALIZED', 'QUEUED_FOR_UPLOAD', 'UPLOADING')
            """)
            rows = cursor.fetchall()

        if not rows:
            return {"status": "up_to_date", "reconciled_count": 0}

        manifest = [
            {
                "segment_id": r["segment_id"],
                "session_id": r["session_id"],
                "file_size_bytes": r["file_size_bytes"],
                "sha256_hash": r["sha256_hash"]
            }
            for r in rows
        ]

        url = f"{self.laptop_url}/api/sync/reconcile"
        try:
            resp = requests.post(
                url,
                json={"auth_token": self.auth_token, "manifest": manifest},
                timeout=10
            )
            if resp.status_code != 200:
                logger.warning("Reconciliation rejected by laptop: %s", resp.text)
                return {"status": "error", "error": resp.text}

            data = resp.json()
            reconciled = data.get("reconciled", [])

            with get_mobile_db() as conn:
                for item in reconciled:
                    seg_id = item["segment_id"]
                    status = item["status"]
                    remote_offset = item["remote_offset"]

                    if status == "VERIFIED":
                        conn.execute("""
                            UPDATE mobile_segments 
                            SET status = 'VERIFIED_BY_LAPTOP', verified_at = CURRENT_TIMESTAMP
                            WHERE segment_id = ?
                        """, (seg_id,))
                        conn.execute("DELETE FROM mobile_outbox WHERE segment_id = ?", (seg_id,))
                        logger.info("Reconciliation confirmed segment %s as VERIFIED on laptop", seg_id)

                    elif status == "PARTIAL":
                        conn.execute("""
                            UPDATE mobile_outbox 
                            SET remote_offset = ?, status = 'QUEUED_FOR_UPLOAD', updated_at = CURRENT_TIMESTAMP
                            WHERE segment_id = ?
                        """, (remote_offset, seg_id))
                        logger.info("Reconciliation updated segment %s remote offset to %d bytes",
                                    seg_id, remote_offset)

            # Trigger retention cleanup if any segments became verified
            cleanup_verified_segments()

            return {"status": "success", "reconciled_count": len(reconciled)}

        except requests.RequestException as e:
            logger.info("Laptop unreachable during reconciliation: %s", e)
            return {"status": "laptop_unreachable", "error": str(e)}

    def upload_pending_segments(self) -> Dict[str, Any]:
        """Process mobile outbox and upload queued segments with resumable chunking."""
        # 1. Reconcile first
        self.reconcile_with_laptop()

        with get_mobile_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT o.segment_id, o.session_id, o.remote_offset, s.filepath, s.file_size_bytes, s.sha256_hash
                FROM mobile_outbox o
                JOIN mobile_segments s ON o.segment_id = s.segment_id
                WHERE o.status IN ('QUEUED_FOR_UPLOAD', 'UPLOADING')
                ORDER BY s.created_at ASC
            """)
            pending = cursor.fetchall()

        if not pending:
            return {"status": "idle", "uploaded_count": 0}

        uploaded_count = 0

        for item in pending:
            seg_id = item["segment_id"]
            sess_id = item["session_id"]
            file_path = Path(item["filepath"])
            total_size = item["file_size_bytes"]
            sha256 = item["sha256_hash"]

            if not file_path.exists():
                logger.error("Outbox media file missing on phone: %s", file_path)
                continue

            try:
                # Step 1: Init upload & verify offset with server
                init_url = f"{self.laptop_url}/api/sync/upload/init"
                init_res = requests.post(init_url, json={
                    "session_id": sess_id,
                    "segment_id": seg_id,
                    "filename": file_path.name,
                    "file_size_bytes": total_size,
                    "sha256_hash": sha256,
                    "auth_token": self.auth_token
                }, timeout=10)

                if init_res.status_code != 200:
                    logger.warning("Init upload failed for %s: %s", seg_id, init_res.text)
                    continue

                init_data = init_res.json()
                if init_data.get("status") == "already_completed":
                    logger.info("Segment %s already safely on laptop", seg_id)
                    with get_mobile_db() as conn:
                        conn.execute("""
                            UPDATE mobile_segments 
                            SET status = 'VERIFIED_BY_LAPTOP', verified_at = CURRENT_TIMESTAMP
                            WHERE segment_id = ?
                        """, (seg_id,))
                        conn.execute("DELETE FROM mobile_outbox WHERE segment_id = ?", (seg_id,))
                    cleanup_verified_segments()
                    uploaded_count += 1
                    continue

                remote_offset = init_data.get("remote_offset", 0)

                # Step 2: Stream remaining chunks starting from confirmed remote_offset
                logger.info("Beginning resumable transfer for %s from byte %d / %d (%.1f%%)",
                            seg_id, remote_offset, total_size, (remote_offset / total_size) * 100 if total_size else 0)

                with open(file_path, "rb") as f:
                    f.seek(remote_offset)

                    while remote_offset < total_size:
                        chunk = f.read(self.chunk_size)
                        if not chunk:
                            break

                        headers = {
                            "X-Segment-ID": seg_id,
                            "X-Upload-Offset": str(remote_offset),
                            "X-File-Size": str(total_size),
                            "X-Sha256": sha256,
                            "X-Session-ID": sess_id,
                            "X-Auth-Token": self.auth_token,
                            "Content-Type": "application/octet-stream"
                        }

                        chunk_url = f"{self.laptop_url}/api/sync/upload/chunk"
                        chunk_res = requests.patch(chunk_url, data=chunk, headers=headers, timeout=15)

                        if chunk_res.status_code != 200:
                            logger.error("Chunk upload failed at offset %d: %s", remote_offset, chunk_res.text)
                            break

                        chunk_data = chunk_res.json()
                        remote_offset = chunk_data.get("remote_offset", remote_offset + len(chunk))

                        # Save durable offset in mobile DB after every single chunk!
                        with get_mobile_db() as conn:
                            conn.execute("""
                                UPDATE mobile_outbox 
                                SET remote_offset = ?, status = 'UPLOADING', updated_at = CURRENT_TIMESTAMP
                                WHERE segment_id = ?
                            """, (remote_offset, seg_id))

                        if chunk_data.get("verified") is True:
                            logger.info("Upload confirmed and verified for segment %s!", seg_id)
                            with get_mobile_db() as conn:
                                conn.execute("""
                                    UPDATE mobile_segments 
                                    SET status = 'VERIFIED_BY_LAPTOP', verified_at = CURRENT_TIMESTAMP
                                    WHERE segment_id = ?
                                """, (seg_id,))
                                conn.execute("DELETE FROM mobile_outbox WHERE segment_id = ?", (seg_id,))
                            uploaded_count += 1
                            cleanup_verified_segments()
                            break

            except requests.RequestException as net_err:
                logger.info("Network dropped while syncing %s: %s. Offset preserved at %d bytes.",
                            seg_id, net_err, remote_offset)
                with get_mobile_db() as conn:
                    conn.execute("""
                        UPDATE mobile_outbox 
                        SET status = 'QUEUED_FOR_UPLOAD',
                            last_error = ?,
                            attempt_count = attempt_count + 1,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE segment_id = ?
                    """, (str(net_err), seg_id))

        return {"status": "completed", "uploaded_count": uploaded_count}
