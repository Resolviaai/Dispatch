"""Resumable chunked upload receiver and bidirectional reconciliation API.
Supports TUS-style byte-offset resuming, SHA-256 verification, and manifest reconciliation.
"""
import os
import hashlib
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from dispatch.config import INCOMING_DIR, PROCESSING_DIR
from dispatch import db
from dispatch.ingestion.validator import probe_video

logger = logging.getLogger("dispatch.sync.receiver")

router = APIRouter(prefix="/api/sync", tags=["sync"])

AUTH_TOKEN = os.getenv("DISPATCH_AUTH_TOKEN", "dispatch_paired_secret_default")


class InitUploadRequest(BaseModel):
    session_id: str
    segment_id: str
    filename: str
    file_size_bytes: int
    sha256_hash: str
    auth_token: str


class ReconcileItem(BaseModel):
    segment_id: str
    session_id: str
    file_size_bytes: int
    sha256_hash: str


class ReconcileRequest(BaseModel):
    auth_token: str
    manifest: List[ReconcileItem]


def verify_token(token: str):
    if token != AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized: invalid device token")


@router.get("/ping")
async def ping_receiver(auth_token: Optional[str] = None):
    """Probing endpoint for LAN and Tailscale transport latency checks."""
    import time
    return {
        "status": "online",
        "service": "dispatch_receiver",
        "timestamp": time.time(),
        "requires_auth": True
    }


@router.post("/reconcile")
async def reconcile_manifest(payload: ReconcileRequest):
    """Bidirectional reconciliation: phone sends manifest, laptop returns exact verified states & offsets."""
    verify_token(payload.auth_token)
    results = []

    with db.get_db_connection() as conn:
        cursor = conn.cursor()

        for item in payload.manifest:
            seg_id = item.segment_id
            expected_size = item.file_size_bytes
            expected_sha256 = item.sha256_hash

            # Check if finalized file exists in processing or incoming
            final_name = f"{seg_id}.mp4"
            final_proc = PROCESSING_DIR / final_name
            final_inc = INCOMING_DIR / final_name
            part_file = INCOMING_DIR / f"{seg_id}.part"

            cursor.execute("SELECT status, file_hash FROM chunks WHERE filename = ?", (final_name,))
            db_chunk = cursor.fetchone()

            if (final_proc.exists() or final_inc.exists()) and db_chunk and db_chunk["status"] != "failed":
                results.append({
                    "segment_id": seg_id,
                    "status": "VERIFIED",
                    "remote_offset": expected_size
                })
            elif part_file.exists():
                results.append({
                    "segment_id": seg_id,
                    "status": "PARTIAL",
                    "remote_offset": part_file.stat().st_size
                })
            else:
                results.append({
                    "segment_id": seg_id,
                    "status": "MISSING",
                    "remote_offset": 0
                })

    return {"reconciled": results}


@router.post("/upload/init")
async def init_upload(payload: InitUploadRequest):
    """Initialize a chunked resumable upload and query remote offset."""
    verify_token(payload.auth_token)
    seg_id = payload.segment_id
    final_name = f"{seg_id}.mp4"

    # Check if already completed and verified
    if (PROCESSING_DIR / final_name).exists() or (INCOMING_DIR / final_name).exists():
        return {
            "status": "already_completed",
            "remote_offset": payload.file_size_bytes,
            "verified": True
        }

    part_file = INCOMING_DIR / f"{seg_id}.part"
    current_offset = part_file.stat().st_size if part_file.exists() else 0

    return {
        "status": "in_progress",
        "remote_offset": current_offset,
        "verified": False
    }


@router.patch("/upload/chunk")
async def upload_chunk(
    request: Request,
    x_segment_id: str = Header(...),
    x_upload_offset: int = Header(...),
    x_file_size: int = Header(...),
    x_sha256: str = Header(...),
    x_session_id: str = Header(...),
    x_auth_token: str = Header(...)
):
    """Receive a byte chunk and append to the partial segment file at the verified offset."""
    verify_token(x_auth_token)
    part_file = INCOMING_DIR / f"{x_segment_id}.part"
    final_file = INCOMING_DIR / f"{x_segment_id}.mp4"

    # Offset consistency check
    current_size = part_file.stat().st_size if part_file.exists() else 0
    if x_upload_offset != current_size:
        raise HTTPException(
            status_code=409,
            detail=f"Offset mismatch: client has {x_upload_offset}, server has {current_size}"
        )

    # Read binary body chunk
    chunk_bytes = await request.body()
    if not chunk_bytes:
        return {"remote_offset": current_size, "completed": False}

    with open(part_file, "ab") as f:
        f.write(chunk_bytes)

    new_offset = part_file.stat().st_size

    # Check if upload is complete
    if new_offset == x_file_size:
        logger.info("Upload complete for %s. Verifying SHA-256...", x_segment_id)
        hasher = hashlib.sha256()
        with open(part_file, "rb") as f:
            for b in iter(lambda: f.read(65536), b""):
                hasher.update(b)
        computed_sha = hasher.hexdigest()

        if computed_sha.lower() != x_sha256.lower():
            logger.error("SHA-256 mismatch for %s: expected %s, got %s",
                         x_segment_id, x_sha256, computed_sha)
            part_file.unlink()
            raise HTTPException(status_code=422, detail="Checksum mismatch; partial file purged")

        # Atomic rename from .part to .mp4
        part_file.rename(final_file)
        logger.info("Segment %s verified and finalized to %s (%d bytes)",
                    x_segment_id, final_file.name, new_offset)

        # Register in laptop database
        chunk_id = db.register_chunk(
            session_id=x_session_id,
            filename=final_file.name,
            filepath=str(final_file),
            file_hash=computed_sha
        )

        # Probe video
        is_valid, meta, err = probe_video(final_file)
        if is_valid:
            db.update_chunk_metadata(
                chunk_id=chunk_id,
                duration=meta["duration"],
                width=meta["width"],
                height=meta["height"],
                aspect_ratio=meta["aspect_ratio"],
                status="verified"
            )

        return {
            "status": "completed",
            "remote_offset": new_offset,
            "verified": True,
            "chunk_id": chunk_id
        }

    return {
        "status": "in_progress",
        "remote_offset": new_offset,
        "verified": False
    }
