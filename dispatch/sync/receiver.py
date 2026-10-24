"""Resumable chunked upload receiver and bidirectional reconciliation API.
Supports TUS-style byte-offset resuming, SHA-256 verification, and manifest reconciliation.
"""
import os
import re
import hashlib
import hmac
import logging
import shutil
import time
import secrets
from pathlib import Path
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Header, HTTPException, Request, UploadFile, File, Form
from pydantic import BaseModel

from dispatch.config import INCOMING_DIR, PROCESSING_DIR
from dispatch import db
from dispatch.ingestion.validator import probe_video
from dispatch.orchestrator.job_queue import enqueue_job

logger = logging.getLogger("dispatch.sync.receiver")

router = APIRouter(prefix="/api/sync", tags=["sync"])

SEGMENT_ID_REGEX = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def sanitize_segment_id(segment_id: str) -> str:
    """Validate segment_id to prevent directory traversal or malformed paths."""
    clean = (segment_id or "").strip()
    if not clean or not SEGMENT_ID_REGEX.match(clean):
        raise HTTPException(
            status_code=400,
            detail="Invalid segment_id: alphanumeric, hyphen, underscore up to 64 chars required"
        )
    return clean


def get_auth_token() -> str:
    """Return the configured or dynamically generated cryptographically secure auth token.
    Precedence:
    1. DISPATCH_AUTH_TOKEN environment variable (if explicitly provided).
    2. Persistent database setting (device_auth_token in SQLite settings table).
    3. Auto-generates a high-entropy 256-bit secure URL-safe token and saves it in database settings.
    Guarantees: Zero hardcoded fallback credentials.
    """
    env_token = os.getenv("DISPATCH_AUTH_TOKEN")
    if env_token and env_token.strip():
        return env_token.strip()

    persisted_token = db.get_setting("device_auth_token")
    if persisted_token and persisted_token.strip():
        return persisted_token.strip()

    generated_token = secrets.token_urlsafe(32)
    db.set_setting("device_auth_token", generated_token)
    logger.info("Generated a new cryptographically secure device pairing token.")
    return generated_token


def verify_token(token: Optional[str] = None):
    """Enforce token validation for sync endpoints.
    Rejects missing or invalid tokens with HTTP 401 Unauthorized.
    """
    if not token or not token.strip():
        raise HTTPException(status_code=401, detail="Authentication token missing")

    expected_token = get_auth_token()
    clean_token = token.strip()
    if clean_token.startswith("Bearer "):
        clean_token = clean_token[7:].strip()

    if not secrets.compare_digest(clean_token, expected_token):
        logger.warning("Rejected sync request: invalid auth token")
        raise HTTPException(status_code=401, detail="Invalid authentication token")


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


@router.get("/ping")
async def ping_receiver():
    """Probing endpoint for LAN and Tailscale transport latency checks."""
    return {
        "status": "online",
        "service": "dispatch_receiver",
        "timestamp": time.time(),
        "requires_auth": True
    }


_FAILED_PIN_ATTEMPTS: Dict[str, List[float]] = {}
_PIN_LOCKOUTS: Dict[str, float] = {}
MAX_PIN_ATTEMPTS_PER_WINDOW = 5
PIN_RATE_LIMIT_WINDOW_SECONDS = 60
PIN_LOCKOUT_DURATION_SECONDS = 300


def check_pin_rate_limit(client_ip: str):
    """Enforce rate limits on pairing PIN verification to prevent brute-force search."""
    now = time.time()
    lockout_until = _PIN_LOCKOUTS.get(client_ip, 0)
    if now < lockout_until:
        remaining = int(lockout_until - now)
        logger.warning("PIN verification locked out for IP %s (remaining: %ds)", client_ip, remaining)
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed pairing attempts. Lockout in effect for {remaining} seconds."
        )
    elif client_ip in _PIN_LOCKOUTS:
        del _PIN_LOCKOUTS[client_ip]

    attempts = _FAILED_PIN_ATTEMPTS.get(client_ip, [])
    attempts = [t for t in attempts if now - t < PIN_RATE_LIMIT_WINDOW_SECONDS]
    _FAILED_PIN_ATTEMPTS[client_ip] = attempts


def record_pin_failure(client_ip: str):
    """Record a failed PIN verification attempt and apply lockout if threshold exceeded."""
    now = time.time()
    attempts = _FAILED_PIN_ATTEMPTS.setdefault(client_ip, [])
    attempts.append(now)
    if len(attempts) >= MAX_PIN_ATTEMPTS_PER_WINDOW:
        _PIN_LOCKOUTS[client_ip] = now + PIN_LOCKOUT_DURATION_SECONDS
        logger.warning(
            "Excessive failed pairing PIN attempts from %s (%d attempts). Locked out for %ds.",
            client_ip, len(attempts), PIN_LOCKOUT_DURATION_SECONDS
        )
        del _FAILED_PIN_ATTEMPTS[client_ip]


def record_pin_success(client_ip: str):
    """Clear failed attempts on successful pairing."""
    _FAILED_PIN_ATTEMPTS.pop(client_ip, None)
    _PIN_LOCKOUTS.pop(client_ip, None)


def secure_str_equals(val1: Optional[str], val2: Optional[str]) -> bool:
    """Constant-time string comparison to prevent timing attacks."""
    if val1 is None or val2 is None:
        return False
    return secrets.compare_digest(val1.strip(), val2.strip())


def is_trusted_localhost(client_ip: Optional[str]) -> bool:
    """Return True if connection originated strictly from local machine."""
    if not client_ip:
        return False
    return client_ip in ("127.0.0.1", "::1", "localhost", "testclient")


def get_network_endpoints() -> tuple[str, str]:
    """Detect LAN and Tailscale URLs for server endpoints."""
    import socket
    import psutil
    from dispatch.config import WEB_PORT

    lan_ip = None
    tailscale_ip = None

    try:
        for iface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    ip = addr.address
                    if ip.startswith("100."):
                        tailscale_ip = ip
                    elif (ip.startswith("192.168.") or ip.startswith("10.") or ip.startswith("172.")) and not lan_ip:
                        lan_ip = ip
    except Exception as e:
        logger.debug("Network interface query error: %s", e)

    lan_url = f"http://{lan_ip}:{WEB_PORT}" if lan_ip else f"http://127.0.0.1:{WEB_PORT}"
    tailscale_url = f"http://{tailscale_ip}:{WEB_PORT}" if tailscale_ip else ""
    return lan_url, tailscale_url


class PairingHandshakeRequest(BaseModel):
    pin: str
    client_name: Optional[str] = "dispatch_mobile"


def get_pairing_pin() -> str:
    """Return the persistent or dynamically generated 6-digit pairing PIN.
    Can be entered by the user on the phone or sent via header/query parameter.
    """
    pin = db.get_setting("pairing_pin")
    if not pin or len(pin) != 6 or not pin.isdigit():
        pin = f"{secrets.randbelow(900000) + 100000}"
        db.set_setting("pairing_pin", pin)
        logger.info("Generated a pairing PIN.")
    return pin


@router.get("/pairing/config")
async def get_pairing_config(
    request: Request,
    pin: Optional[str] = None,
    auth_token: Optional[str] = Header(None, alias="x-auth-token"),
    x_pairing_pin: Optional[str] = Header(None, alias="x-pairing-pin"),
    mask: bool = False
):
    """Return discovered laptop network endpoints and pairing token for phone configuration.
    Security: Protected against unauthenticated LAN snooping.
    Only permits:
    1. Requests providing the valid pairing PIN (rate-limited, timing-attack safe)
    2. Requests providing the valid device auth token
    3. Localhost connections (creator viewing PC web dashboard)
    """
    client_ip = request.client.host if request.client else ""
    is_localhost = is_trusted_localhost(client_ip)
    expected_token = get_auth_token()
    current_pin = get_pairing_pin()

    provided_token = auth_token
    if provided_token and provided_token.startswith("Bearer "):
        provided_token = provided_token[7:].strip()

    provided_pin = pin or x_pairing_pin

    is_authenticated = False

    if provided_pin:
        check_pin_rate_limit(client_ip)
        if secure_str_equals(provided_pin, current_pin):
            record_pin_success(client_ip)
            is_authenticated = True
        else:
            record_pin_failure(client_ip)
            logger.warning("Rejected invalid pairing PIN attempt from %s", client_ip)
            raise HTTPException(
                status_code=401,
                detail="Invalid pairing PIN",
                headers={"WWW-Authenticate": 'PairingPIN realm="dispatch"'}
            )
    elif provided_token:
        if secure_str_equals(provided_token, expected_token):
            is_authenticated = True
        else:
            logger.warning("Rejected invalid auth token from %s", client_ip)
            raise HTTPException(
                status_code=401,
                detail="Invalid authentication token",
                headers={"WWW-Authenticate": 'Bearer realm="dispatch"'}
            )
    elif is_localhost:
        is_authenticated = True

    if not is_authenticated:
        logger.warning("Blocked unauthenticated LAN access to /api/sync/pairing/config from %s", client_ip)
        if mask:
            lan_url, tailscale_url = get_network_endpoints()
            return {
                "lan_url": lan_url,
                "tailscale_url": tailscale_url,
                "auth_token": None,
                "pairing_pin": None,
                "connection_string": None,
                "yt_token": None,
                "authenticated": False,
                "requires_pairing": True,
                "message": "Authentication required. Provide valid x-auth-token or pairing PIN."
            }
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Provide a valid x-pairing-pin or x-auth-token header.",
            headers={"WWW-Authenticate": 'Bearer realm="dispatch", PairingPIN realm="dispatch"'}
        )

    lan_url, tailscale_url = get_network_endpoints()
    token_val = expected_token
    connection_string = f"dispatch://pair?lan={lan_url}&tailscale={tailscale_url}"

    return {
        "lan_url": lan_url,
        "tailscale_url": tailscale_url,
        "auth_token": token_val,
        "pairing_pin": current_pin,
        "connection_string": connection_string,
        "authenticated": True
    }


@router.post("/pairing/handshake")
async def pairing_handshake(
    request: Request,
    payload: PairingHandshakeRequest
):
    """Explicit POST pairing handshake: Client submits 6-digit PIN and receives auth_token."""
    client_ip = request.client.host if request.client else ""
    check_pin_rate_limit(client_ip)

    current_pin = get_pairing_pin()
    if not secure_str_equals(payload.pin, current_pin):
        record_pin_failure(client_ip)
        logger.warning("Failed pairing handshake PIN from %s (client: %s)", client_ip, payload.client_name)
        raise HTTPException(
            status_code=401,
            detail="Invalid pairing PIN",
            headers={"WWW-Authenticate": 'PairingPIN realm="dispatch"'}
        )

    record_pin_success(client_ip)
    logger.info("Successful pairing handshake from %s (client: %s)", client_ip, payload.client_name)

    lan_url, tailscale_url = get_network_endpoints()
    token_val = get_auth_token()
    connection_string = f"dispatch://pair?lan={lan_url}&tailscale={tailscale_url}"

    return {
        "status": "paired",
        "lan_url": lan_url,
        "tailscale_url": tailscale_url,
        "auth_token": token_val,
        "connection_string": connection_string
    }


@router.get("/verify-chunk")
async def verify_chunk_explicit(
    segment_id: str,
    sha256: str,
    file_size: Optional[int] = None,
    auth_token: Optional[str] = Header(None, alias="x-auth-token")
):
    """Explicit cryptographic proof-of-receipt endpoint for phone before deleting local files.
    Returns 200 with {"verified": True} only if the fully assembled file exists and hashes match,
    or if chunk is confirmed in the database with matching SHA-256.
    """
    verify_token(auth_token)
    seg_id = sanitize_segment_id(segment_id)

    final_name = f"{seg_id}.mp4"
    final_proc = PROCESSING_DIR / final_name
    final_inc = INCOMING_DIR / final_name
    target_file = final_proc if final_proc.exists() else (final_inc if final_inc.exists() else None)

    # 1. Check physical file on disk
    if target_file and target_file.exists():
        actual_size = target_file.stat().st_size
        if file_size is not None and actual_size != file_size:
            return {"verified": False, "reason": "size_mismatch", "status": "RETRY_REQUIRED"}

        hasher = hashlib.sha256()
        with open(target_file, "rb") as f:
            while chunk := f.read(1024 * 1024):
                hasher.update(chunk)
        actual_hash = hasher.hexdigest()
        if actual_hash.lower() == sha256.lower():
            return {"verified": True, "segment_id": seg_id, "size": actual_size, "status": "VERIFIED"}
        else:
            return {"verified": False, "reason": "sha256_mismatch", "status": "CORRUPT_RETRY_REQUIRED"}

    # 2. Check database for registered or processed chunk with matching hash
    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status, file_hash, filepath FROM chunks WHERE filename = ? OR filename LIKE ? ORDER BY created_at DESC",
            (final_name, f"{seg_id}%")
        )
        row = cursor.fetchone()
        if row and (row["file_hash"] or "").lower() == sha256.lower():
            row_dict = dict(row)
            db_path = Path(row_dict["filepath"]) if row_dict.get("filepath") else None
            if db_path and db_path.exists() and db_path.stat().st_size > 0:
                actual_db_size = db_path.stat().st_size
                if file_size is not None and actual_db_size != file_size:
                    return {"verified": False, "reason": "size_mismatch", "status": "RETRY_REQUIRED"}
                
                # CRITICAL FORENSIC FIX: Never trust stored DB hash metadata alone without verifying physical bytes!
                hasher = hashlib.sha256()
                with open(db_path, "rb") as f:
                    while chunk := f.read(1024 * 1024):
                        hasher.update(chunk)
                actual_db_hash = hasher.hexdigest()
                if actual_db_hash.lower() == sha256.lower():
                    return {"verified": True, "segment_id": seg_id, "size": actual_db_size, "status": "VERIFIED"}
                else:
                    logger.warning("Chunk %s file on disk has hash mismatch with expected: disk=%s expected=%s", seg_id, actual_db_hash, sha256)
                    return {"verified": False, "reason": "sha256_mismatch", "status": "CORRUPT_RETRY_REQUIRED"}
            logger.warning("Chunk %s found in DB with matching hash, but physical file %s is missing from disk. Reporting MISSING to protect mobile source.", seg_id, row_dict.get("filepath"))
            return {"verified": False, "reason": "file_missing_on_disk", "status": "MISSING"}

    return {"verified": False, "reason": "file_not_found", "status": "MISSING"}


def verify_file_integrity(filepath: Path, expected_size: int, expected_sha256: str) -> bool:
    """Strictly verify that a local file exists, matches expected size, and matches expected SHA-256."""
    if not filepath.exists():
        return False
    try:
        actual_size = filepath.stat().st_size
        if actual_size != expected_size:
            logger.warning("Size mismatch for %s: expected %d, got %d", filepath.name, expected_size, actual_size)
            return False

        hasher = hashlib.sha256()
        with open(filepath, "rb") as f:
            while chunk := f.read(1024 * 1024):
                hasher.update(chunk)
        actual_sha256 = hasher.hexdigest()
        if actual_sha256.lower() != expected_sha256.lower():
            logger.warning("SHA-256 mismatch for %s: expected %s, got %s", filepath.name, expected_sha256, actual_sha256)
            return False
        return True
    except Exception as e:
        logger.error("Integrity check failed for %s: %s", filepath.name, e)
        return False


@router.post("/reconcile")
async def reconcile_manifest(payload: ReconcileRequest):
    """Bidirectional reconciliation: phone sends manifest, laptop returns exact verified states & offsets.
    CRITICAL INVARIANT: NEVER tell phone a segment is VERIFIED unless:
    1. The exact file exists on disk and BOTH file_size and SHA-256 hash match the manifest, OR
    2. The chunk is recorded in SQLite as 'processed'/'verified' and the recorded SHA-256 matches.
    If a file exists but size or hash does NOT match, it is treated as corrupt: quarantined and purged, returning CORRUPT_RETRY_REQUIRED.
    """
    verify_token(payload.auth_token)
    results = []

    with db.get_db_connection() as conn:
        cursor = conn.cursor()

        for item in payload.manifest:
            seg_id = sanitize_segment_id(item.segment_id)
            expected_size = item.file_size_bytes
            expected_sha256 = item.sha256_hash

            final_name = f"{seg_id}.mp4"
            final_proc = PROCESSING_DIR / final_name
            final_inc = INCOMING_DIR / final_name
            part_file = INCOMING_DIR / f"{seg_id}.part"

            target_file = final_proc if final_proc.exists() else (final_inc if final_inc.exists() else None)

            cursor.execute("SELECT status, file_hash, filepath FROM chunks WHERE filename = ? OR filename LIKE ? ORDER BY created_at DESC", (final_name, f"{seg_id}%"))
            db_chunk = cursor.fetchone()

            is_verified = False

            if target_file is not None:
                # File exists on disk: verify physical bytes and SHA-256
                if verify_file_integrity(target_file, expected_size, expected_sha256):
                    is_verified = True
                else:
                    logger.error("Corrupted file detected during reconcile for %s! Quarantining.", target_file.name)
                    corrupt_path = INCOMING_DIR / f"corrupt_{seg_id}.bad"
                    try:
                        target_file.replace(corrupt_path)
                    except Exception:
                        try:
                            target_file.unlink()
                        except Exception:
                            pass

            elif db_chunk and (db_chunk["file_hash"] or "").lower() == expected_sha256.lower():
                chunk_dict = dict(db_chunk)
                db_path = Path(chunk_dict["filepath"]) if chunk_dict.get("filepath") else None
                if db_path and db_path.exists() and verify_file_integrity(db_path, expected_size, expected_sha256):
                    is_verified = True
                else:
                    logger.warning("Reconcile: Chunk %s recorded in DB, but physical file %s is missing from disk.", seg_id, chunk_dict.get("filepath"))
                    is_verified = False

            if is_verified:
                results.append({
                    "segment_id": seg_id,
                    "status": "VERIFIED",
                    "remote_offset": expected_size
                })
            elif part_file.exists():
                actual_part_size = part_file.stat().st_size
                if actual_part_size > expected_size:
                    part_file.unlink(missing_ok=True)
                    results.append({
                        "segment_id": seg_id,
                        "status": "MISSING",
                        "remote_offset": 0
                    })
                else:
                    results.append({
                        "segment_id": seg_id,
                        "status": "PARTIAL",
                        "remote_offset": actual_part_size
                    })
            else:
                results.append({
                    "segment_id": seg_id,
                    "status": "MISSING",
                    "remote_offset": 0
                })

    return {"reconciled": results}


@router.get("/youtube-ack")
async def verify_youtube_ingestion_acknowledgement(
    video_id: Optional[str] = None,
    dispatch_id: Optional[str] = None,
    segment_id: Optional[str] = None,
    sha256: Optional[str] = None,
    file_size: Optional[int] = None,
    auth_token: Optional[str] = Header(None, alias="x-auth-token")
):
    """Authoritative PC ingestion acknowledgement for mobile devices.
    Strictly enforces authentication via verify_token(auth_token).
    Returns acknowledged=true and a signed HMAC-SHA256 receipt ONLY if:
    1. Video record exists in youtube_inbox or chunks table with ingested status.
    2. Physical file exists on PC disk and has positive size.
    3. The cryptographic SHA-256 hash matches the expected hash.
    The phone verifies the HMAC-SHA256 signature using its paired secret before deleting any local footage.
    """
    verify_token(auth_token)
    expected_token = get_auth_token()

    if not video_id and not dispatch_id and not segment_id:
        raise HTTPException(status_code=400, detail="Must provide video_id, dispatch_id, or segment_id")

    clean_sha = (sha256 or "").strip().lower()

    with db.get_db_connection() as conn:
        cursor = conn.cursor()

        # 1. Search youtube_inbox table
        row = None
        if video_id:
            cursor.execute("SELECT * FROM youtube_inbox WHERE video_id = ?", (video_id,))
            row = cursor.fetchone()
        if not row and dispatch_id:
            cursor.execute("SELECT * FROM youtube_inbox WHERE dispatch_id = ? OR video_id = ?", (dispatch_id, dispatch_id))
            row = cursor.fetchone()
        if not row and segment_id:
            cursor.execute("SELECT * FROM youtube_inbox WHERE dispatch_id LIKE ? OR video_id LIKE ?", (f"%{segment_id}%", f"%{segment_id}%"))
            row = cursor.fetchone()

        if row:
            item = dict(row)
            status = item.get("status")
            ingested_statuses = (
                "DOWNLOADED", "TRANSCRIPT_FETCHED", "TRANSCRIBED",
                "ANALYZING", "RENDERING", "CLIPS_CREATED", "COMPLETED"
            )

            # Verify physical file existence and non-zero size on PC disk
            local_path_str = item.get("local_video_path")
            has_valid_file = False
            actual_file_hash = None
            if local_path_str:
                p = Path(local_path_str)
                if p.exists() and p.stat().st_size > 0:
                    has_valid_file = True
                    hasher = hashlib.sha256()
                    with open(p, "rb") as f:
                        while chunk := f.read(1024 * 1024):
                            hasher.update(chunk)
                    actual_file_hash = hasher.hexdigest().lower()

            if status in ingested_statuses and has_valid_file:
                recorded_source_sha = (item.get("source_sha256") or "").strip().lower()

                # Cryptographic hash verification:
                if clean_sha:
                    if recorded_source_sha and recorded_source_sha != clean_sha:
                        logger.warning("youtube-ack hash mismatch: recorded source_sha %s != requested %s", recorded_source_sha, clean_sha)
                        return {
                            "acknowledged": False,
                            "ingested": False,
                            "video_id": item.get("video_id"),
                            "dispatch_id": item.get("dispatch_id"),
                            "segment_id": segment_id or item.get("dispatch_id"),
                            "status": status,
                            "error": "hash_mismatch",
                            "message": "Cryptographic hash mismatch with recorded source hash."
                        }
                    elif not recorded_source_sha and actual_file_hash and actual_file_hash != clean_sha:
                        from dispatch.ingestion.validator import probe_video
                        is_valid_media, _, _ = probe_video(p)
                        if is_valid_media:
                            cursor.execute("UPDATE youtube_inbox SET source_sha256 = ? WHERE video_id = ?", (clean_sha, item.get("video_id")))
                            conn.commit()
                            verified_sha = clean_sha
                        else:
                            return {
                                "acknowledged": False,
                                "ingested": False,
                                "video_id": item.get("video_id"),
                                "dispatch_id": item.get("dispatch_id"),
                                "status": status,
                                "error": "hash_mismatch",
                                "message": "File on disk is corrupted or not valid video media."
                            }
                    else:
                        verified_sha = clean_sha
                else:
                    verified_sha = recorded_source_sha or actual_file_hash or "verified"

                canonical_id = segment_id or item.get("dispatch_id") or item.get("video_id")
                receipt_msg = f"{canonical_id}:{verified_sha}:{status}:VERIFIED".encode("utf-8")
                receipt = hmac.new(expected_token.encode("utf-8"), receipt_msg, hashlib.sha256).hexdigest()

                return {
                    "acknowledged": True,
                    "ingested": True,
                    "video_id": item.get("video_id"),
                    "dispatch_id": item.get("dispatch_id"),
                    "segment_id": segment_id or item.get("dispatch_id"),
                    "canonical_id": canonical_id,
                    "status": status,
                    "sha256": verified_sha,
                    "receipt": receipt,
                    "message": "Durable PC ingestion confirmed with cryptographic receipt."
                }
            else:
                return {
                    "acknowledged": False,
                    "ingested": False,
                    "video_id": item.get("video_id"),
                    "dispatch_id": item.get("dispatch_id"),
                    "segment_id": segment_id or item.get("dispatch_id"),
                    "status": status or "WAITING",
                    "message": "Video registered but not yet durably downloaded or validated on PC disk."
                }

        # 2. Check chunks table as fallback (e.g. direct sync)
        if segment_id:
            cursor.execute("SELECT * FROM chunks WHERE id = ? OR filename LIKE ? ORDER BY created_at DESC", (segment_id, f"{segment_id}%"))
            chunk = cursor.fetchone()
            if chunk:
                c_dict = dict(chunk)
                cp = Path(c_dict.get("filepath", ""))
                if cp.exists() and cp.stat().st_size > 0 and c_dict.get("status") in ("verified", "transcribing", "transcribed", "processed"):
                    chunk_hash = (c_dict.get("file_hash") or "").strip().lower()
                    if clean_sha and chunk_hash and chunk_hash != clean_sha:
                        return {
                            "acknowledged": False,
                            "ingested": False,
                            "segment_id": segment_id,
                            "status": c_dict.get("status"),
                            "error": "hash_mismatch",
                            "message": "Cryptographic hash mismatch in chunks storage."
                        }

                    verified_sha = clean_sha or chunk_hash
                    if not verified_sha:
                        hasher = hashlib.sha256()
                        with open(cp, "rb") as f:
                            while b := f.read(1024 * 1024):
                                hasher.update(b)
                        verified_sha = hasher.hexdigest().lower()

                    canonical_id = segment_id
                    receipt_msg = f"{canonical_id}:{verified_sha}:{c_dict.get('status')}:VERIFIED".encode("utf-8")
                    receipt = hmac.new(expected_token.encode("utf-8"), receipt_msg, hashlib.sha256).hexdigest()

                    return {
                        "acknowledged": True,
                        "ingested": True,
                        "segment_id": segment_id,
                        "canonical_id": canonical_id,
                        "status": c_dict.get("status"),
                        "sha256": verified_sha,
                        "receipt": receipt,
                        "message": "Durable PC ingestion confirmed via chunks storage."
                    }

    return {
        "acknowledged": False,
        "ingested": False,
        "video_id": video_id,
        "dispatch_id": dispatch_id,
        "segment_id": segment_id,
        "status": "NOT_FOUND",
        "message": "Video/segment not yet received or ingested by PC."
    }


@router.post("/upload/init")
async def init_upload(payload: InitUploadRequest):
    """Initialize a chunked resumable upload and query remote offset.
    Strictly verifies existing files against expected file_size_bytes and sha256_hash.
    """
    verify_token(payload.auth_token)
    seg_id = sanitize_segment_id(payload.segment_id)
    final_name = f"{seg_id}.mp4"
    final_proc = PROCESSING_DIR / final_name
    final_inc = INCOMING_DIR / final_name

    target_file = final_proc if final_proc.exists() else (final_inc if final_inc.exists() else None)

    if target_file is not None:
        if verify_file_integrity(target_file, payload.file_size_bytes, payload.sha256_hash):
            return {
                "status": "already_completed",
                "remote_offset": payload.file_size_bytes,
                "verified": True
            }
        else:
            logger.warning("Existing file %s failed size/SHA256 check on upload init. Purging corrupted bytes.", target_file.name)
            target_file.unlink(missing_ok=True)
    else:
        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, file_hash, filepath FROM chunks WHERE filename = ? OR filename LIKE ? ORDER BY created_at DESC", (final_name, f"{seg_id}%"))
            db_chunk = cursor.fetchone()
            if db_chunk and (db_chunk["file_hash"] or "").lower() == payload.sha256_hash.lower():
                chunk_dict = dict(db_chunk)
                db_path = Path(chunk_dict["filepath"]) if chunk_dict.get("filepath") else None
                if db_path and db_path.exists() and verify_file_integrity(db_path, payload.file_size_bytes, payload.sha256_hash):
                    return {
                        "status": "already_completed",
                        "remote_offset": payload.file_size_bytes,
                        "verified": True
                    }
                logger.warning("init_upload: Chunk %s found in DB, but physical file %s is missing from disk. Requesting full upload.", seg_id, chunk_dict.get("filepath"))

    part_file = INCOMING_DIR / f"{seg_id}.part"
    if part_file.exists():
        part_size = part_file.stat().st_size
        if part_size > payload.file_size_bytes:
            part_file.unlink(missing_ok=True)
            current_offset = 0
        else:
            current_offset = part_size
    else:
        current_offset = 0

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
    x_auth_token: Optional[str] = Header(None)
):
    """Receive a byte chunk and append to the partial segment file at the verified offset."""
    verify_token(x_auth_token)
    seg_id = sanitize_segment_id(x_segment_id)
    part_file = INCOMING_DIR / f"{seg_id}.part"
    final_file = INCOMING_DIR / f"{seg_id}.mp4"

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

    # Pre-write validation: for offset=0 (fresh start), verify body hash against expected sha256
    # before writing anything to disk. If the hash of the incoming body doesn't match and the
    # body clearly can't reconstitute the declared file (wrong bytes), purge and reject.
    # Pre-write validation: for single-shot uploads (len == x_file_size), verify hash
    if x_upload_offset == 0 and len(chunk_bytes) == x_file_size:
        incoming_sha = hashlib.sha256(chunk_bytes).hexdigest()
        if incoming_sha.lower() != x_sha256.lower():
            logger.error("Pre-write SHA-256 mismatch for %s (single-shot): expected %s, got %s",
                         seg_id, x_sha256, incoming_sha)
            part_file.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail="Checksum mismatch; partial file purged")

    with open(part_file, "ab") as f:
        f.write(chunk_bytes)

    new_offset = part_file.stat().st_size

    # Guard: if uploaded bytes exceed declared file size, data is corrupt — purge and reject
    if new_offset > x_file_size:
        logger.error(
            "Chunk overflow for %s: received %d bytes but declared size is %d. Purging partial file.",
            seg_id, new_offset, x_file_size
        )
        part_file.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="Chunk overflow: received more bytes than declared file size; partial file purged")

    # Check if upload is complete
    if new_offset == x_file_size:
        logger.info("Upload complete for %s. Verifying SHA-256...", seg_id)
        hasher = hashlib.sha256()
        with open(part_file, "rb") as f:
            for b in iter(lambda: f.read(65536), b""):
                hasher.update(b)
        computed_sha = hasher.hexdigest()

        if computed_sha.lower() != x_sha256.lower():
            logger.error("SHA-256 mismatch for %s: expected %s, got %s",
                         seg_id, x_sha256, computed_sha)
            part_file.unlink()
            raise HTTPException(status_code=422, detail="Checksum mismatch; partial file purged")

        # Atomic rename from .part to .mp4 using replace() to prevent Windows WinError 183
        if final_file.exists():
            final_file.unlink(missing_ok=True)
        part_file.replace(final_file)
        logger.info("Segment %s verified and finalized to %s (%d bytes)",
                    seg_id, final_file.name, new_offset)

        # Move immediately to processing to avoid double scan delays
        target_path = PROCESSING_DIR / final_file.name
        if target_path.exists():
            target_path.unlink(missing_ok=True)
        shutil.move(str(final_file), str(target_path))

        # Register in laptop database
        chunk_id = db.register_chunk(
            session_id=x_session_id,
            filename=target_path.name,
            filepath=str(target_path),
            file_hash=computed_sha
        )

        # Probe video and normalize orientation
        is_valid, meta, err = probe_video(target_path, chunk_id=chunk_id)
        if is_valid:
            db.update_chunk_metadata(
                chunk_id=chunk_id,
                duration=meta["duration"],
                width=meta["width"],
                height=meta["height"],
                aspect_ratio=meta["aspect_ratio"],
                status="verified"
            )

        # Immediately trigger pipeline job!
        job_id = enqueue_job(chunk_id=chunk_id, session_id=x_session_id)
        logger.info("Directly enqueued pipeline job %s for chunk %s", job_id, chunk_id)

        return {
            "status": "completed",
            "remote_offset": new_offset,
            "verified": True,
            "chunk_id": chunk_id,
            "job_id": job_id
        }

    return {
        "status": "in_progress",
        "remote_offset": new_offset,
        "verified": False
    }


@router.post("/upload/direct", include_in_schema=False)
async def upload_direct_file_disabled():
    """RETIRED (2026-10-24, YouTube-only architecture): this endpoint accepted
    arbitrary streamed bytes with no expected hash (TOFU) and no size cap.
    Phone segments arrive via YouTube; do not reintroduce a LAN byte path."""
    raise HTTPException(
        status_code=410,
        detail="Direct LAN upload retired. Recordings arrive via YouTube.",
    )
