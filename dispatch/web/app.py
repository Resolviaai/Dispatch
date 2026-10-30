"""FastAPI Web Dashboard for Dispatch.
Provides the local PC dashboard for reviewing, approving, and publishing clips.
"""
import os
import json
import asyncio
import psutil
import logging
import threading
import time
import re
import subprocess
import sys
import uuid
import ipaddress
import urllib.parse
import email.utils
from functools import wraps
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Header, Response, Body
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from dispatch.config import (
    CLIPS_DIR,
    ROOT_DIR,
    DEFAULT_PUBLISH_MODE,
    PROCESSING_DIR,
    INCOMING_DIR,
    STORAGE_DIR,
    FONTS_DIR,
    MAX_CLIP_DURATION,
    MIN_CLIP_DURATION,
    MAX_REQUEST_BODY_BYTES,
    DISPATCH_TRUST_LAN
)
from dispatch import db
from dispatch.video_engine import (
    render_clip,
    ensure_clip_thumbnail,
    resolve_clip_source_path,
    resolve_active_clip_video,
    populate_clip_media_urls,
    get_file_fingerprint,
    get_thumbnail_version_token,
    get_thumbnail_version,
)
from dispatch.youtube_inbox.catcher import (
    YouTubeInboxCatcher,
    extract_youtube_video_id,
    is_ingestion_in_flight
)
from dispatch.captions import (
    CaptionTrack,
    group_words_into_captions,
    apply_template_to_track,
    CAPTION_PRESETS,
    export_to_ass,
    export_to_json
)
from dispatch.captions.presets import get_default_preset_id, get_preset
from dispatch.ai_clips.highlight_finder import transliterate_segments_to_hinglish

from dispatch.sync.receiver import router as sync_router

from dispatch.sync.receiver import is_trusted_localhost
from dispatch.cloud.sync import import_cloud_captions_for_clip, authorized_pc_owner_ids
from dispatch.publisher.stats import public_publication, refresh_publication_stats

logger = logging.getLogger("dispatch.web")
_BODY_CAP_EXEMPT_PATHS = frozenset({
    "/api/media/upload",       # D5 exemption: bulk media uploads
    "/api/recordings/upload",  # D5 exemption: phone recording upload
    "/api/fonts/import",       # D5 exemption: font import (has its own 20MB cap, D4)
})
_CLIP_MUTATION_LOCKS: Dict[str, Any] = {}
_CLIP_MUTATION_LOCKS_GUARD = threading.Lock()


def _get_clip_mutation_lock(clip_id: str) -> Any:
    with _CLIP_MUTATION_LOCKS_GUARD:
        return _CLIP_MUTATION_LOCKS.setdefault(clip_id, threading.Lock())


async def _acquire_clip_mutation_lock(lock: Any) -> None:
    acquire_task = asyncio.create_task(asyncio.to_thread(lock.acquire))
    try:
        await asyncio.shield(acquire_task)
    except asyncio.CancelledError:
        # The worker thread survives cancellation of to_thread. Release the lock if
        # this request disappears while queued, or later requests for this clip hang.
        def release_abandoned_lock(task: asyncio.Task) -> None:
            if not task.cancelled() and task.exception() is None and task.result():
                lock.release()

        acquire_task.add_done_callback(release_abandoned_lock)
        raise


def _serialize_clip_mutation(handler):
    @wraps(handler)
    async def serialized(clip_id: str, *args, **kwargs):
        lock = _get_clip_mutation_lock(clip_id)
        await _acquire_clip_mutation_lock(lock)
        try:
            return await handler(clip_id, *args, **kwargs)
        finally:
            lock.release()
    return serialized

app = FastAPI(title="Dispatch Dashboard", version="1.0.0")

ALLOWED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://appassets.androidplatform.net",
    "http://appassets.androidplatform.net",
    "https://dispatch-cloud.resolviaai.workers.dev",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|appassets\.androidplatform\.net)(:\d+)?$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"

    # D5 hardening: reject oversized request bodies early. Upload endpoints are exempt
    # from the cap itself (they still enforce their own auth and file-size rules).
    content_length = request.headers.get("content-length")
    if content_length and request.url.path not in _BODY_CAP_EXEMPT_PATHS:
        try:
            declared_length = int(content_length)
        except (TypeError, ValueError):
            declared_length = None
        if declared_length is not None and declared_length > MAX_REQUEST_BODY_BYTES:
            logger.warning(
                "Rejected %s %s from %s: request body %d bytes exceeds the %d-byte request cap",
                request.method, request.url.path, client_ip, declared_length, MAX_REQUEST_BODY_BYTES
            )
            return JSONResponse(
                status_code=413,
                content={"detail": f"Request body too large: {declared_length} bytes exceeds the {MAX_REQUEST_BODY_BYTES}-byte limit"},
            )

    logger.info("--> [HTTP REQ] %s %s from %s (Origin: %s, Range: %s)",
                request.method, request.url.path, client_ip,
                request.headers.get("origin"), request.headers.get("range"))
    try:
        response = await call_next(request)
        logger.info("<-- [HTTP RES] %s %s status=%d", request.method, request.url.path, response.status_code)
        return response
    except Exception as e:
        logger.error("<-- [HTTP ERR] %s %s error: %s", request.method, request.url.path, e)
        raise

app.include_router(sync_router)


def get_authenticated_user(request: Request) -> Dict[str, Any]:
    """Identity boundary: verifies the Supabase JWT from the Authorization header
    or x-auth-token header against Supabase itself (signature + expiry validated
    server-side by Supabase). Returns {user_id, email}. Fails closed with 401 for
    missing/invalid tokens. Loopback dashboard calls keep their existing
    device-token path. Remediation D3: the legacy `?token=` query-parameter
    acceptance was removed (no frontend call site consumes it; tokens in URLs leak
    into logs/history)."""
    auth = request.headers.get("authorization") or ""
    token = None
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    elif request.headers.get("x-auth-token"):
        token = request.headers.get("x-auth-token").strip()

    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        import httpx
        globals()['httpx'] = httpx  # patchable in tests
        resp = httpx.get(
            f"{os.getenv('SUPABASE_URL', '')}/auth/v1/user",
            headers={"apikey": os.getenv("SUPABASE_ANON_KEY", ""), "Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
    except Exception as e:
        logger.warning("Auth verification unavailable: %s", e)
        raise HTTPException(status_code=503, detail="Auth service unreachable")
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    user = resp.json()
    return {"user_id": user.get("id", ""), "email": user.get("email", "")}


def resolve_caller_user_id(request: Request, allow_lan_peer: bool = False) -> Optional[str]:
    """Resolve caller identity:
    - Loopback callers (localhost, 127.0.0.1, ::1) are trusted by default.
      If a loopback caller supplies a Supabase Bearer token, resolve and return the user_id.
      Otherwise return None (system/all clips).
    - Non-loopback callers (LAN) authenticate via Supabase session token or paired device token.
      A valid paired device token (x-auth-token) resolves to this PC's configured owner
      account id (DISPATCH_PC_OWNER_USER_IDS, first entry — mirrors
      dispatch/cloud/sync.py:authorized_pc_owner_ids) so phone-originated requests are
      attributed to the operator instead of "system" (remediation work item D3).
    - If allow_lan_peer is True and caller is on the local network (or loopback), returns None
      when unauthenticated instead of raising 401. This allows media streams and thumbnails
      rendered via standard HTML <img> and <video> tags to load without custom headers.
    """
    client_host = request.client.host if request.client else "unknown"
    if is_trusted_localhost(client_host):
        auth = request.headers.get("authorization") or ""
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if token:
            try:
                user = get_authenticated_user(request)
                return user.get("user_id") or None
            except HTTPException as e:
                # A credential was presented but is invalid/expired: never
                # downgrade it to system scope (which can read/mutate all jobs).
                if e.status_code == 401:
                    raise
                return None
            except Exception:
                return None
        return None

    # LAN caller: first attempt Supabase session token authentication
    try:
        user = get_authenticated_user(request)
        return user.get("user_id") or None
    except HTTPException:
        pass

    # LAN caller fallback: paired device authentication (DispatchBridge x-auth-token)
    device_token = request.headers.get("x-auth-token")
    if device_token:
        try:
            from dispatch.sync.receiver import verify_token
            verify_token(device_token)
            owners = authorized_pc_owner_ids()
            return owners[0] if owners else None
        except HTTPException:
            pass

    if allow_lan_peer and _is_local_network_peer(client_host):
        return None

    raise HTTPException(status_code=401, detail="Authentication required")


def require_secure_credential_transport(request: Request, client_host: str) -> None:
    """P0 security invariant: YouTube refresh tokens / client secrets must never be
    returned to a NON-LOOPBACK caller over plaintext HTTP. Pairing codes gate WHO
    receives credentials; they cannot protect an unencrypted channel. Fails closed
    (426) with an actionable message; loopback PC dashboard use is unaffected."""
    if request.url.scheme == "http" and not is_trusted_localhost(client_host):
        logger.warning("Refused credential transfer over plaintext HTTP to %s", client_host)
        raise HTTPException(
            status_code=426,
            detail=("Credentials are not sent over unencrypted HTTP. Enable TLS on the PC "
                    "Dispatch server (see DISPATCH_WEB_TLS in the README) and pair again over https://."),
        )


# D1 LAN-trust policy: GET paths a (non-loopback) local-network caller may load
# WITHOUT a paired device token while DISPATCH_TRUST_LAN is off. These are the
# media streams consumed by HTML <img>/<video>/<source> tags and CSS @font-face
# loads, which cannot attach custom headers:
#   /clips/{filename}                        — rendered clip videos + thumbnails
#   /api/clips/{clip_id}/source              — original source video (Range HTTP)
#   /api/recordings/{segment_id}/source      — raw recording chunks (Range HTTP)
#   /api/clips/{clip_id}/filmstrip           — filmstrip frame strips
#   /api/fonts/{filename}                    — font binaries for the studio canvas
#   /api/template/sample-video               — template-editing reference video
#   /download/dispatch.apk                   — phone first-run APK transfer
_MEDIA_STREAM_EXACT_PATHS = frozenset({"/api/template/sample-video", "/download/dispatch.apk"})
_MEDIA_STREAM_SUFFIXES = ("/source", "/filmstrip")


def _is_media_stream_get_path(path: str) -> bool:
    """True when `path` is one of the tokenless read-only media-stream routes."""
    raw_path = path or ""
    if raw_path.startswith("/clips/") or raw_path.startswith("/api/fonts/"):
        return True
    if raw_path.endswith("/source") or raw_path.endswith("/filmstrip"):
        return True
    return raw_path in _MEDIA_STREAM_EXACT_PATHS


def _validate_local_network_trust(request: Request, client_host: str) -> None:
    """Shared Host-header + Origin hygiene for local (loopback or LAN) callers.
    DNS-rebinding + forged-origin defence; never authenticates on its own."""
    raw_host = request.headers.get("host", "").strip()
    parsed_host = raw_host.split(":")[0].strip().lower()
    if parsed_host and not (
        parsed_host in ("localhost", "127.0.0.1", "::1", "testserver")
        or _is_local_network_peer(parsed_host)
    ):
        logger.warning("Rejected request to %s: untrusted host header '%s'", request.url.path, raw_host)
        raise HTTPException(status_code=403, detail="Forbidden: Untrusted Host header")

    origin_header = request.headers.get("origin", "").strip()
    if origin_header:
        try:
            split_origin = urllib.parse.urlsplit(origin_header)
            origin_scheme = split_origin.scheme.lower()
            origin_hostname = (split_origin.hostname or "").lower()
            origin_port = split_origin.port

            # Allow loopback/testserver origins, Android appassets, and local network peer origins
            is_valid = (
                origin_scheme in ("http", "https")
                and (
                    origin_hostname in ("localhost", "127.0.0.1", "::1", "testserver", "appassets.androidplatform.net")
                    or _is_local_network_peer(origin_hostname)
                )
                and (origin_port in (8000, 5173, None) or origin_hostname in ("testserver", "appassets.androidplatform.net"))
            )
            if not is_valid:
                logger.warning("Rejected request to %s: untrusted origin '%s'", request.url.path, origin_header)
                raise HTTPException(status_code=403, detail="Forbidden: Untrusted Origin header")
        except Exception as e:
            if isinstance(e, HTTPException):
                raise
            logger.warning("Malformed Origin header rejected: %s", origin_header)
            raise HTTPException(status_code=403, detail="Forbidden: Malformed Origin header")


def verify_dashboard_auth(request: Request) -> None:
    """Enforce token validation or secure origin matching.
    Protects mutating endpoints and sensitive routes against unauthorized callers,
    CSRF, and DNS rebinding attacks while allowing first-party Android WebView and PC Studio.

    D1 LAN-trust policy (hardening 2026-10-28):
    - Explicit credentials win first (paired device token or Supabase session) from
      any address, any method.
    - When DISPATCH_TRUST_LAN is off (default): loopback callers keep the legacy
      full trust (Host/Origin hygiene, then allow); any other private-IP caller is
      restricted to the read-only media-stream allowlist on GET/HEAD — every
      mutating request and every non-media GET raises 401.
    - When DISPATCH_TRUST_LAN is on: legacy full-trust behavior for local-network
      callers (Host/Origin hygiene, then allow), guarded by a startup warning.
    """
    token = request.headers.get("x-auth-token") or request.headers.get("authorization")
    if token:
        try:
            from dispatch.sync.receiver import verify_token
            verify_token(token)
            return
        except HTTPException:
            pass
        try:
            get_authenticated_user(request)
            return
        except HTTPException:
            pass

    client_host = request.client.host if request.client else "unknown"
    if is_trusted_localhost(client_host):
        # Loopback callers keep full legacy trust (Host/Origin hygiene only).
        _validate_local_network_trust(request, client_host)
        return

    if _is_local_network_peer(client_host):
        if not DISPATCH_TRUST_LAN:
            method = (getattr(request, "method", "GET") or "GET").strip().upper()
            request_path = (request.url.path or "/")
            if method not in ("GET", "HEAD") or not _is_media_stream_get_path(request_path):
                logger.warning(
                    "Rejected unauthenticated %s %s from local-network peer %s: DISPATCH_TRUST_LAN is disabled; "
                    "LAN callers may only read approved media streams (pair the device for full access)",
                    method, request_path, client_host
                )
                raise HTTPException(status_code=401, detail="Authentication required for this operation")
        # Legacy LAN behavior (or allowlisted unauthenticated media stream):
        # keep Host/Origin hygiene, then allow.
        _validate_local_network_trust(request, client_host)
        return

    raise HTTPException(status_code=401, detail="Authentication required for this operation")


_YOUTUBE_AUTH_LOCK = threading.Lock()
_YOUTUBE_AUTH_PROCESS: Optional[subprocess.Popen] = None
_YOUTUBE_AUTH_STATE: Dict[str, Any] = {
    "status": "idle",
    "message": "YouTube sign-in has not been started.",
}
_OAUTH_STATE_PREFIX = "DISPATCH_OAUTH_STATE="


def _monitor_youtube_auth(process: subprocess.Popen) -> None:
    """Read only the helper's safe status markers; never log its raw output."""
    global _YOUTUBE_AUTH_PROCESS
    exit_code = -1
    try:
        if process.stdout is not None:
            for line in process.stdout:
                if not line.startswith(_OAUTH_STATE_PREFIX):
                    continue
                try:
                    event = json.loads(line[len(_OAUTH_STATE_PREFIX):])
                except (TypeError, json.JSONDecodeError):
                    continue
                status = event.get("status")
                if status not in {"starting", "awaiting_authorization", "authenticated", "failed"}:
                    continue
                safe_state = {
                    "status": status,
                    "message": str(event.get("message", ""))[:240],
                }
                for field in ("channel_title", "channel_id"):
                    value = event.get(field)
                    if isinstance(value, str):
                        safe_state[field] = value[:160]
                with _YOUTUBE_AUTH_LOCK:
                    _YOUTUBE_AUTH_STATE.clear()
                    _YOUTUBE_AUTH_STATE.update(safe_state)
        exit_code = process.wait()
    except Exception:
        exit_code = -1
    finally:
        with _YOUTUBE_AUTH_LOCK:
            current_status = _YOUTUBE_AUTH_STATE.get("status")
            if current_status not in {"authenticated", "failed"}:
                _YOUTUBE_AUTH_STATE.clear()
                _YOUTUBE_AUTH_STATE.update({
                    "status": "failed",
                    "message": "The Google sign-in process ended before Dispatch could verify the account.",
                })
            elif current_status == "authenticated" and exit_code != 0:
                _YOUTUBE_AUTH_STATE.clear()
                _YOUTUBE_AUTH_STATE.update({
                    "status": "failed",
                    "message": "The Google sign-in process exited unexpectedly. Try again.",
                })
            if _YOUTUBE_AUTH_PROCESS is process:
                _YOUTUBE_AUTH_PROCESS = None


def _is_local_network_peer(client_host: str) -> bool:
    if is_trusted_localhost(client_host):
        return True
    try:
        address = ipaddress.ip_address(client_host.split("%", 1)[0])
        if getattr(address, "ipv4_mapped", None):
            address = address.ipv4_mapped
        return address.is_private or address.is_link_local or address in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False

class ApproveRequest(BaseModel):
    title: Optional[str] = None
    hashtags: Optional[str] = None
    publish_mode: Optional[str] = None  # "private" or "public"
    platforms: Optional[str] = None     # "youtube,instagram"
    account_ids: Optional[Dict[str, str]] = None # {"youtube_shorts": "channel_id", "instagram": "ig_id"}
    layout_mode: Optional[str] = None   # "fit_blur", "crop_follow", "landscape"
    caption_track: Optional[Dict[str, Any]] = None
    range_revision: Optional[int] = None


class RejectRequest(BaseModel):
    reason: Optional[str] = "User rejected"


class ClipMetadataRequest(BaseModel):
    """InspectorPane metadata editor save (remediation work item D8).
    Only reviewable (ready_review) clips accept metadata edits; the save is
    revision-fenced like every other ready-review mutation."""
    title: Optional[str] = None
    hook: Optional[str] = None
    hashtags: Optional[str] = None
    platform_targets: Optional[str] = None               # comma-separated, e.g. "youtube,instagram"
    expected_range_revision: Optional[int] = None        # omit to trust the current revision


class TrimClipRequest(BaseModel):
    start_time: float
    end_time: float
    range_revision: Optional[int] = None


class LayoutModeRequest(BaseModel):
    layout_mode: str


class YouTubeIngestRequest(BaseModel):
    url: str
    force_whisper: Optional[bool] = False


class SettingsRequest(BaseModel):
    publish_mode: Optional[str] = None
    auto_process: Optional[str] = None


class ResolveReconciliationRequest(BaseModel):
    action: str  # "mark_published", "retry", "mark_failed"
    remote_id: Optional[str] = None
    remote_url: Optional[str] = None
    note: Optional[str] = None


@app.api_route("/clips/{filename}", methods=["GET", "HEAD"])
async def get_clip_file(filename: str, request: Request):
    """Stream clip video or image thumbnail directly from storage with path traversal protection,
    freshness validation, and HTTP cache invalidation (RFC 9111 ETag & 304 Not Modified).
    """
    target_path = (CLIPS_DIR / filename).resolve()
    if not target_path.is_relative_to(CLIPS_DIR.resolve()):
        raise HTTPException(status_code=403, detail="Access denied: path traversal detected")

    caller_user_id = resolve_caller_user_id(request, allow_lan_peer=True)
    if caller_user_id:
        stem = Path(filename).stem
        clip_cand_id = stem
        if stem.startswith("thumb_"):
            clip_cand_id = stem[6:]
        elif stem.startswith("thumbnail_"):
            clip_cand_id = stem[10:]
        cand_clip = db.get_clip_by_id(clip_cand_id)
        if cand_clip and cand_clip.get("user_id") and cand_clip["user_id"] != caller_user_id:
            raise HTTPException(status_code=403, detail="Forbidden: Access to another user's clip media is denied")

    # If an image thumbnail is requested, ensure freshness on disk against active video
    is_image = filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
    if is_image:
        stem = Path(filename).stem
        cand_ids = [stem]
        if stem.startswith("thumb_"):
            cand_ids.append(stem[6:])
        elif stem.startswith("thumbnail_"):
            cand_ids.append(stem[10:])

        for cid in cand_ids:
            try:
                fresh_p = ensure_clip_thumbnail(cid)
                if fresh_p and fresh_p.exists():
                    target_path = fresh_p
                    break
            except Exception as e:
                logger.debug("Failed ensuring thumbnail for %s in get_clip_file: %s", cid, e)

    if not target_path.exists() or not target_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    st = target_path.stat()
    mtime = int(st.st_mtime)
    mtime_ns = getattr(st, "st_mtime_ns", int(mtime * 1e9))
    size = st.st_size
    etag = f'"{mtime_ns}-{size}"'
    legacy_etag = f'"{mtime}-{size}"'
    last_modified = email.utils.formatdate(mtime, usegmt=True)

    is_versioned = bool(request.query_params.get("v"))
    if is_versioned:
        cache_control = "public, max-age=86400, stale-while-revalidate=60"
    else:
        cache_control = "no-cache, must-revalidate"

    # Handle conditional GET for non-range requests
    if not request.headers.get("range") and request.method in ("GET", "HEAD"):
        if_none_match = request.headers.get("if-none-match")
        if_modified_since = request.headers.get("if-modified-since")
        is_not_modified = False

        if if_none_match:
            clean_inm = if_none_match.strip()
            if clean_inm in (etag, legacy_etag, f"W/{etag}", f"W/{legacy_etag}", "*"):
                is_not_modified = True
        elif if_modified_since:
            try:
                parsed_time = email.utils.parsedate_to_datetime(if_modified_since)
                if parsed_time and parsed_time.timestamp() >= mtime:
                    is_not_modified = True
            except Exception:
                pass

        if is_not_modified:
            return Response(
                status_code=304,
                headers={
                    "ETag": etag,
                    "Last-Modified": last_modified,
                    "Cache-Control": cache_control,
                }
            )

    media_type = "video/mp4" if filename.endswith(".mp4") else "image/jpeg"
    headers = {
        "ETag": etag,
        "Last-Modified": last_modified,
        "Cache-Control": cache_control,
        "Accept-Ranges": "bytes",
    }
    return FileResponse(target_path, media_type=media_type, headers=headers)


@app.api_route("/download/dispatch.apk", methods=["GET", "HEAD"])
async def download_android_apk():
    """Download the native Dispatch Android APK compiled for POCO C65."""
    candidates = [
        ROOT_DIR / "android" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk",
        STORAGE_DIR / "dispatch.apk",
        STORAGE_DIR / "download" / "dispatch.apk",
    ]
    apk_path = next((p for p in candidates if p.exists() and p.is_file() and p.stat().st_size > 0), None)
    if not apk_path:
        raise HTTPException(
            status_code=404,
            detail="The Android APK is currently compiling or not built yet."
        )
    return FileResponse(
        str(apk_path),
        media_type="application/vnd.android.package-archive",
        filename="Dispatch-POCO-C65-v1.apk"
    )


@app.get("/api/pipeline/jobs")
async def get_pipeline_jobs():
    """Return live checkpointed jobs with current stage, status, leases, and retry timers."""
    jobs = list(db.get_active_pipeline_jobs())
    try:
        active_inbox = db.list_youtube_inbox(limit=25)
        for item in active_inbox:
            st = item.get("status")
            if st in (
                "DOWNLOADING", "VALIDATING", "WAITING_FOR_TRANSCRIPT",
                "TRANSCRIBED", "ANALYZING", "CLIPS_DEFINED",
                "RENDERING", "WAITING_FOR_YOUTUBE_PROCESSING", "STALLED_YOUTUBE_PROCESSING", "FAILED"
            ):
                youtube_failure = st == "FAILED"
                jobs.append({
                    "id": f"yt_{item['video_id']}",
                    "chunk_id": item.get("dispatch_id") or item["video_id"],
                    "session_id": item.get("session_id") or "",
                    "current_stage": "waiting_for_youtube_processing" if st == "STALLED_YOUTUBE_PROCESSING" else st.lower(),
                    "status": "FAILED" if youtube_failure else "STALLED" if st == "STALLED_YOUTUBE_PROCESSING" else "processing",
                    "title": item.get("title", ""),
                    "video_id": item["video_id"],
                    "last_error": item.get("last_error") or "",
                    "created_at": item.get("discovered_at") or item.get("upload_time"),
                    "updated_at": item.get("updated_at")
                })
    except Exception as e:
        logger.debug("Error aggregating youtube_inbox into pipeline jobs: %s", e)
    return {"jobs": jobs}


@app.get("/api/status")
async def get_system_status():
    """Return system resource metrics and pipeline counters."""
    disk = psutil.disk_usage(str(ROOT_DIR))
    free_gb = round(disk.free / (1024 ** 3), 1)
    total_gb = round(disk.total / (1024 ** 3), 1)

    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM clips WHERE status = 'ready_review'")
        ready_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM clips WHERE status = 'approved'")
        approved_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM clips WHERE status = 'published'")
        published_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM chunks")
        chunks_count = cursor.fetchone()[0]

    return {
        "disk_free_gb": free_gb,
        "disk_total_gb": total_gb,
        "disk_percent_used": disk.percent,
        "ready_clips": ready_count,
        "approved_clips": approved_count,
        "published_clips": published_count,
        "total_chunks": chunks_count,
        "publish_mode": db.get_setting("publish_mode", DEFAULT_PUBLISH_MODE),
        "auto_process": db.get_setting("auto_process", "true")
    }


def resolve_clip_source_path(clip: Dict[str, Any]) -> Optional[Path]:
    """Safely resolve the original raw source video chunk for a clip."""
    # 1. Resolve through chunk_id in chunks table
    chunk_id = clip.get("chunk_id")
    if chunk_id:
        with db.get_db_connection() as conn:
            row = conn.execute("SELECT filepath FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
            if row and row["filepath"]:
                cand = Path(row["filepath"])
                if cand.exists() and cand.is_file() and cand.stat().st_size > 0:
                    return cand

    # 2. Check YouTube inbox by video_id, session_id, or dispatch_id
    yt_id = clip.get("youtube_video_id")
    sess_id = clip.get("session_id")
    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        yt_row = None
        if yt_id:
            yt_row = cursor.execute("SELECT local_video_path FROM youtube_inbox WHERE video_id = ?", (yt_id,)).fetchone()
        if not yt_row and sess_id:
            yt_row = cursor.execute("SELECT local_video_path FROM youtube_inbox WHERE session_id = ?", (sess_id,)).fetchone()
        if not yt_row and chunk_id:
            yt_row = cursor.execute("SELECT local_video_path FROM youtube_inbox WHERE dispatch_id = ? OR video_id = ?", (chunk_id, chunk_id)).fetchone()
        if yt_row and yt_row["local_video_path"]:
            cand = Path(yt_row["local_video_path"])
            if cand.exists() and cand.is_file() and cand.stat().st_size > 0:
                return cand

    # 3. Check storage/incoming and storage/processing using session_id or chunk_id
    for search_dir in (PROCESSING_DIR, INCOMING_DIR):
        if clip.get("session_id"):
            sess = clip["session_id"]
            for cand in search_dir.glob(f"*{sess}*.mp4"):
                if cand.exists() and cand.is_file() and cand.stat().st_size > 0:
                    return cand
        if chunk_id:
            for cand in search_dir.glob(f"*{chunk_id}*.mp4"):
                if cand.exists() and cand.is_file() and cand.stat().st_size > 0:
                    return cand

    # 4. Fallback: if clip video_path exists, use it so preview never hard-crashes
    if clip.get("video_path"):
        cand = Path(clip["video_path"])
        if cand.exists() and cand.is_file() and cand.stat().st_size > 0:
            return cand

    return None


def resolve_clip_origin(clip: Dict[str, Any]) -> Dict[str, Any]:
    """Derive true origin (phone vs youtube), youtube_video_id, and parent session_id."""
    chunk_id = clip.get("chunk_id")
    session_id = clip.get("session_id")
    yt_id = None
    source_title = None

    if chunk_id:
        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT ch.session_id, ch.file_hash, yi.video_id, yi.title
                FROM chunks ch
                LEFT JOIN youtube_inbox yi ON (ch.file_hash = 'yt_' || yi.video_id OR yi.local_video_path = ch.filepath)
                WHERE ch.id = ?
            """, (chunk_id,))
            row = cursor.fetchone()
            if row:
                if not session_id and row["session_id"]:
                    session_id = row["session_id"]
                if row["video_id"]:
                    yt_id = row["video_id"]
                    source_title = row["title"]
                elif row["file_hash"] and str(row["file_hash"]).startswith("yt_"):
                    yt_id = str(row["file_hash"])[3:]

    if not yt_id and session_id:
        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT video_id, title FROM youtube_inbox WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if row and row["video_id"]:
                yt_id = row["video_id"]
                source_title = row["title"]

    origin = "youtube" if yt_id else "phone"
    return {
        "origin": origin,
        "session_id": session_id,
        "youtube_video_id": yt_id,
        "source_title": source_title,
    }


def get_clip_framing_context(clip: Dict[str, Any]) -> Dict[str, Any]:
    """Derive authentic source orientation, allowed framing modes, and target dimensions.
    The original session recording is the single source of truth.
    """
    orientation = "landscape"
    source_w = None
    source_h = None
    source_aspect = "16:9"

    # 1. Query chunk metadata from database
    chunk_id = clip.get("chunk_id")
    if chunk_id:
        with db.get_db_connection() as conn:
            row = conn.execute("SELECT width, height, aspect_ratio FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
            if row:
                source_w = row["width"]
                source_h = row["height"]
                if row["aspect_ratio"]:
                    source_aspect = str(row["aspect_ratio"]).strip()
                if source_h and source_w:
                    orientation = "portrait" if source_h > source_w else "landscape"
                elif source_aspect in ("9:16", "portrait"):
                    orientation = "portrait"

    # 2. If inconclusive or chunk dimensions missing/zero, probe source file directly
    if source_w is None or source_h is None or source_w <= 0 or source_h <= 0:
        source_path = resolve_clip_source_path(clip)
        # CRITICAL GUARD: Never probe clip["video_path"] (rendered 9:16 output) to infer source orientation!
        rendered_vp = clip.get("video_path")
        if source_path and rendered_vp and str(source_path.resolve()) == str(Path(rendered_vp).resolve()):
            source_path = None

        if source_path and source_path.exists():
            from dispatch.ingestion.validator import inspect_video_orientation
            info = inspect_video_orientation(source_path)
            if info.get("valid"):
                orientation = info.get("orientation", "landscape")
                source_w = info.get("effective_width") or info.get("width")
                source_h = info.get("effective_height") or info.get("height")
                source_aspect = "9:16" if orientation == "portrait" else "16:9"
                if chunk_id and source_w and source_h:
                    try:
                        with db.get_db_connection() as conn:
                            conn.execute("UPDATE chunks SET width = ?, height = ?, aspect_ratio = ? WHERE id = ?",
                                         (source_w, source_h, source_aspect, chunk_id))
                            conn.commit()
                    except Exception:
                        pass
        else:
            # Fallback for cloud/YouTube clips where raw source isn't local:
            # YouTube imports are inherently 16:9 landscape
            orig_info = resolve_clip_origin(clip)
            if orig_info.get("origin") == "youtube" or orig_info.get("youtube_video_id"):
                orientation = "landscape"
                source_aspect = "16:9" 

    # 3. Dynamic options based strictly on original session orientation
    if orientation == "portrait":
        # Case A: Original input is Vertical / 9:16 -> EXACTLY 3 possible outputs
        available_modes = [
            {
                "id": "fit_black",
                "label": "Fit + Black",
                "target_aspect": "16:9",
                "description": "16:9 widescreen output with black pillarbox bars"
            },
            {
                "id": "fit_blur",
                "label": "Fit + Blur",
                "target_aspect": "16:9",
                "description": "16:9 widescreen output with blurred background"
            },
            {
                "id": "native_916",
                "label": "Native 9:16",
                "target_aspect": "9:16",
                "description": "100% full original vertical video (no bars, no crop)"
            }
        ]
        valid_ids = {"fit_black", "fit_blur", "native_916", "native_portrait"}
        cur = (clip.get("layout_mode") or "").strip().lower()
        if cur == "native_portrait":
            cur = "native_916"
        if cur not in ("fit_black", "fit_blur", "native_916"):
            cur = "native_916"
        target_aspect = "16:9" if cur in ("fit_black", "fit_blur") else "9:16"
    else:
        # Case B: Original input is Horizontal / 16:9 -> EXACTLY 4 possible outputs
        available_modes = [
            {
                "id": "crop_916",
                "label": "Crop 9:16",
                "target_aspect": "9:16",
                "description": "Full vertical crop to fill 9:16"
            },
            {
                "id": "fit_blur",
                "label": "Fit + Blur",
                "target_aspect": "9:16",
                "description": "Vertical 9:16 with blurred top/bottom bars"
            },
            {
                "id": "fit_black",
                "label": "Fit + Black",
                "target_aspect": "9:16",
                "description": "Vertical 9:16 with black top/bottom bars"
            },
            {
                "id": "native_169",
                "label": "Native 16:9",
                "target_aspect": "16:9",
                "description": "100% full original horizontal 16:9 (no bars, no crop)"
            }
        ]
        valid_ids = {"crop_916", "crop_follow", "fit_blur", "fit_black", "native_169", "landscape"}
        cur = (clip.get("layout_mode") or "").strip().lower()
        if cur == "crop_follow":
            cur = "crop_916"
        elif cur == "landscape":
            cur = "native_169"
        if cur not in ("crop_916", "fit_blur", "fit_black", "native_169"):
            cur = "crop_916"
        target_aspect = "16:9" if cur == "native_169" else "9:16"

    return {
        "source_orientation": orientation,
        "source_aspect_ratio": "9:16" if orientation == "portrait" else "16:9",
        "available_layout_modes": available_modes,
        "layout_mode": cur,
        "target_aspect": target_aspect,
        "valid_mode_ids": valid_ids,
        "source_width": source_w,
        "source_height": source_h,
    }


def clip_render_is_stale(clip: Dict[str, Any]) -> bool:
    """A trim or framing change invalidates the previously rendered media."""
    return bool(clip.get("render_stale")) or bool(
        clip.get("rendered_layout_mode")
        and not is_matching_layout(clip.get("rendered_layout_mode"), clip.get("layout_mode"))
    )


@app.get("/api/clips")
async def list_clips(request: Request):
    """List clips grouped by status with source media URLs and stale render flags."""
    verify_dashboard_auth(request)
    caller_user_id = resolve_caller_user_id(request, allow_lan_peer=True)
    ready_clips = db.get_clips_for_review(user_id=caller_user_id)
    for c in ready_clips:
        chunk = db.get_chunk_by_id(c.get("chunk_id")) if c.get("chunk_id") else None
        c["source_duration"] = float(chunk["duration"]) if chunk and chunk.get("duration") else None
        populate_clip_media_urls(c)
        ctx = get_clip_framing_context(c)
        c["source_orientation"] = ctx["source_orientation"]
        c["source_aspect_ratio"] = ctx["source_aspect_ratio"]
        c["available_layout_modes"] = ctx["available_layout_modes"]
        c["layout_mode"] = ctx["layout_mode"]
        c["target_aspect"] = ctx["target_aspect"]
        c["is_render_stale"] = clip_render_is_stale(c)
        orig_info = resolve_clip_origin(c)
        c["origin"] = orig_info["origin"]
        if not c.get("session_id") and orig_info["session_id"]:
            c["session_id"] = orig_info["session_id"]
        c["youtube_video_id"] = orig_info["youtube_video_id"]
        c["source_title"] = orig_info["source_title"]

    # D7: the 7-day published-clip purge moved to the scheduled janitor in main.py
    # (daemon thread "dispatch-published-clip-janitor", first sweep 5 min after
    # boot, then every 6 h). This request-path cleanup flush is gone.

    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        if caller_user_id:
            cursor.execute("""
                SELECT cl.*, 
                       COALESCE(po.remote_url, CASE WHEN po.remote_id IS NOT NULL THEN 'https://youtu.be/' || po.remote_id END) as remote_url,
                       po.remote_id
                FROM clips cl 
                LEFT JOIN publishing_outbox po ON po.clip_id = cl.id AND po.status = 'published'
                WHERE cl.status IN ('approved', 'published') AND cl.user_id = ?
                GROUP BY cl.id
                ORDER BY COALESCE(cl.published_at, cl.approved_at, cl.created_at) DESC 
                LIMIT 50
            """, (caller_user_id,))
        else:
            cursor.execute("""
                SELECT cl.*, 
                       COALESCE(po.remote_url, CASE WHEN po.remote_id IS NOT NULL THEN 'https://youtu.be/' || po.remote_id END) as remote_url,
                       po.remote_id
                FROM clips cl 
                LEFT JOIN publishing_outbox po ON po.clip_id = cl.id AND po.status = 'published'
                WHERE cl.status IN ('approved', 'published') 
                GROUP BY cl.id
                ORDER BY COALESCE(cl.published_at, cl.approved_at, cl.created_at) DESC 
                LIMIT 50
            """)
        published_clips = [dict(r) for r in cursor.fetchall()]
        publication_map = db.get_publications_for_clips([str(c.get("id")) for c in published_clips])
        for c in published_clips:
            chunk = db.get_chunk_by_id(c.get("chunk_id")) if c.get("chunk_id") else None
            c["source_duration"] = float(chunk["duration"]) if chunk and chunk.get("duration") else None
            populate_clip_media_urls(c)
            c["published_at"] = str(c.get("published_at") or c.get("approved_at") or c.get("created_at") or "")
            publications = [
                public_publication(row)
                for row in publication_map.get(str(c.get("id")), [])
            ]
            c["publications"] = publications
            primary = next((p for p in publications if p["platform"] in ("youtube", "youtube_shorts")), None) \
                or (publications[0] if publications else None)
            if primary:
                c["remote_id"] = c.get("remote_id") or primary.get("remote_id")
                c["remote_url"] = c.get("remote_url") or primary.get("remote_url")
            ctx = get_clip_framing_context(c)
            c["source_orientation"] = ctx["source_orientation"]
            c["source_aspect_ratio"] = ctx["source_aspect_ratio"]
            c["available_layout_modes"] = ctx["available_layout_modes"]
            c["layout_mode"] = ctx["layout_mode"]
            c["target_aspect"] = ctx["target_aspect"]
            c["is_render_stale"] = clip_render_is_stale(c)
            orig_info = resolve_clip_origin(c)
            c["origin"] = orig_info["origin"]
            if not c.get("session_id") and orig_info["session_id"]:
                c["session_id"] = orig_info["session_id"]
            c["youtube_video_id"] = orig_info["youtube_video_id"]
            c["source_title"] = orig_info["source_title"]

    queued_clips = db.get_queued_transcription_clips(limit=50, user_id=caller_user_id)
    queue_stats = db.get_transcription_queue_stats()

    return {
        "ready": ready_clips,
        "recent_approved": published_clips,
        "queued": queued_clips,
        "queue_stats": queue_stats
    }


@app.get("/api/clips/{clip_id}")
async def get_clip_detail(clip_id: str, request: Request):
    """Return single clip metadata with source URL and render status."""
    caller_user_id = resolve_caller_user_id(request, allow_lan_peer=True)
    clip = db.get_clip_by_id(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if caller_user_id and (not clip.get("user_id") or clip["user_id"] != caller_user_id):
        raise HTTPException(status_code=403, detail="Forbidden: Clip belongs to another user")
    populate_clip_media_urls(clip)
    chunk = db.get_chunk_by_id(clip.get("chunk_id")) if clip.get("chunk_id") else None
    clip["source_duration"] = float(chunk["duration"]) if chunk and chunk.get("duration") else None
    ctx = get_clip_framing_context(clip)
    clip["source_orientation"] = ctx["source_orientation"]
    clip["source_aspect_ratio"] = ctx["source_aspect_ratio"]
    clip["available_layout_modes"] = ctx["available_layout_modes"]
    clip["layout_mode"] = ctx["layout_mode"]
    clip["target_aspect"] = ctx["target_aspect"]
    clip["is_render_stale"] = clip_render_is_stale(clip)
    orig_info = resolve_clip_origin(clip)
    clip["origin"] = orig_info["origin"]
    if not clip.get("session_id") and orig_info["session_id"]:
        clip["session_id"] = orig_info["session_id"]
    clip["youtube_video_id"] = orig_info["youtube_video_id"]
    clip["source_title"] = orig_info["source_title"]
    return clip


@app.get("/api/clips/{clip_id}/publications")
async def get_clip_publications(clip_id: str, request: Request):
    """Published destinations with cached metrics refreshed at most once per TTL."""
    verify_dashboard_auth(request)
    caller_user_id = resolve_caller_user_id(request)
    clip = db.get_clip_by_id(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if caller_user_id and (not clip.get("user_id") or clip["user_id"] != caller_user_id):
        raise HTTPException(status_code=403, detail="Forbidden: Clip belongs to another user")

    async def refresh(publication: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(refresh_publication_stats, publication),
                timeout=15,
            )
        except Exception as exc:
            logger.info("Stats refresh skipped for publication %s: %s", publication.get("id"), exc)
            return publication

    rows = db.get_publications_for_clips([clip_id]).get(clip_id, [])
    refreshed_rows = await asyncio.gather(*(refresh(publication) for publication in rows))
    return {"publications": [public_publication(row) for row in refreshed_rows]}


@app.api_route("/api/clips/{clip_id}/source", methods=["GET", "HEAD"])
async def get_clip_source_media(clip_id: str, request: Request):
    """Safely resolve and stream the original raw source video chunk for this clip or chunk.
    Supports HTTP Range requests (206 Partial Content) for smooth HTML5 video scrubbing
    and conditional RFC 9111 revalidation (ETag, 304 Not Modified).
    """
    caller_user_id = resolve_caller_user_id(request, allow_lan_peer=True)
    clip = db.get_clip_by_id(clip_id)
    if clip and caller_user_id and clip.get("user_id") and clip["user_id"] != caller_user_id:
        raise HTTPException(status_code=403, detail="Forbidden: Access to another user's source media is denied")
    source_path = None
    headers = {
        "Accept-Ranges": "bytes",
    }
    if clip:
        source_path = resolve_clip_source_path(clip)
        headers["X-Clip-Start"] = str(clip.get("start_time", 0.0))
        headers["X-Clip-End"] = str(clip.get("end_time", clip.get("duration", 0.0)))
        headers["X-Clip-Duration"] = str(clip.get("duration", 0.0))
    else:
        # Check if clip_id is actually a chunk_id
        chunk = db.get_chunk_by_id(clip_id)
        if chunk and chunk.get("filepath"):
            cp = Path(chunk["filepath"])
            if cp.exists():
                source_path = cp
                headers["X-Clip-Duration"] = str(chunk.get("duration", 0.0))
        if not source_path:
            clean_yt_id = clip_id.replace("yt_", "").strip()
            yt_row = db.get_youtube_video(clean_yt_id)
            if yt_row and yt_row.get("local_video_path"):
                yp = Path(yt_row["local_video_path"])
                if yp.exists():
                    source_path = yp
                    headers["X-Clip-Duration"] = str(yt_row.get("duration", 0.0))

    if not source_path or not source_path.exists():
        raise HTTPException(status_code=404, detail="Source media chunk not found on disk")

    resolved_path = source_path.resolve()
    if not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="Invalid source media file")

    st = resolved_path.stat()
    mtime = int(st.st_mtime)
    size = st.st_size
    etag = f'"{mtime}-{size}"'
    last_modified = email.utils.formatdate(mtime, usegmt=True)

    is_versioned = bool(request.query_params.get("v"))
    if is_versioned:
        cache_control = "public, max-age=86400, stale-while-revalidate=60"
    else:
        cache_control = "no-cache, must-revalidate"

    headers["ETag"] = etag
    headers["Last-Modified"] = last_modified
    headers["Cache-Control"] = cache_control

    # Handle conditional GET for non-range requests
    if not request.headers.get("range") and request.method in ("GET", "HEAD"):
        if_none_match = request.headers.get("if-none-match")
        if_modified_since = request.headers.get("if-modified-since")
        is_not_modified = False

        if if_none_match:
            clean_inm = if_none_match.strip()
            if clean_inm in (etag, f"W/{etag}", "*"):
                is_not_modified = True
        elif if_modified_since:
            try:
                parsed_time = email.utils.parsedate_to_datetime(if_modified_since)
                if parsed_time and parsed_time.timestamp() >= mtime:
                    is_not_modified = True
            except Exception:
                pass

        if is_not_modified:
            return Response(
                status_code=304,
                headers={
                    "ETag": etag,
                    "Last-Modified": last_modified,
                    "Cache-Control": cache_control,
                }
            )

    return FileResponse(
        path=str(resolved_path),
        media_type="video/mp4",
        headers=headers
    )


@app.api_route("/api/recordings/{segment_id}/source", methods=["GET", "HEAD"])
async def get_recording_source_media(segment_id: str, request: Request):
    """Safely stream the original raw source video chunk for a session segment."""
    return await get_clip_source_media(segment_id, request)


def is_matching_layout(a: Optional[str], b: Optional[str]) -> bool:
    """Check if two layout modes are functionally identical (including aliases)."""
    if not a or not b:
        return False
    if a == b:
        return True
    alias_groups = [
        {"crop_916", "crop_follow"},
        {"native_169", "landscape"},
        {"native_916", "native_portrait"},
    ]
    return any(a in group and b in group for group in alias_groups)


@app.post("/api/clips/{clip_id}/layout")
@_serialize_clip_mutation
async def handle_update_clip_layout(clip_id: str, payload: LayoutModeRequest, request: Request):
    """Persist chosen layout framing mode for a clip immediately."""
    verify_dashboard_auth(request)
    caller_user_id = resolve_caller_user_id(request)
    clip = db.get_clip_by_id(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if caller_user_id and (not clip.get("user_id") or clip["user_id"] != caller_user_id):
        raise HTTPException(status_code=403, detail="Forbidden: You do not have access to this clip")
    if clip.get("status") != "ready_review":
        raise HTTPException(status_code=409, detail="Only reviewable clips can change layout")

    ctx = get_clip_framing_context(clip)
    raw_mode = (payload.layout_mode or "").strip().lower()
    if raw_mode not in ctx["valid_mode_ids"]:
        allowed_list = [m["id"] for m in ctx["available_layout_modes"]]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid layout_mode '{payload.layout_mode}' for {ctx['source_orientation']} source. Allowed: {allowed_list}"
        )

    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE clips SET layout_mode = ? WHERE id = ?", (raw_mode, clip_id))
        conn.commit()

    updated_clip = db.get_clip_by_id(clip_id)
    updated_ctx = get_clip_framing_context(updated_clip)
    rendered_mode = updated_clip.get("rendered_layout_mode") if updated_clip else None
    is_stale = bool(updated_clip and updated_clip.get("render_stale")) or bool(
        rendered_mode and not is_matching_layout(rendered_mode, raw_mode)
    )

    return {
        "status": "success",
        "clip_id": clip_id,
        "layout_mode": raw_mode,
        "source_orientation": updated_ctx["source_orientation"],
        "target_aspect": updated_ctx["target_aspect"],
        "available_layout_modes": updated_ctx["available_layout_modes"],
        "rendered_layout_mode": rendered_mode,
        "is_render_stale": is_stale
    }


@app.post("/api/clips/{clip_id}/render")
@_serialize_clip_mutation
async def handle_render_clip(clip_id: str, request: Request, payload: Optional[LayoutModeRequest] = None):
    """Explicitly re-render a clip from source media using the current or specified layout_mode."""
    verify_dashboard_auth(request)
    caller_user_id = resolve_caller_user_id(request)
    clip = db.get_clip_by_id(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if caller_user_id and (not clip.get("user_id") or clip["user_id"] != caller_user_id):
        raise HTTPException(status_code=403, detail="Forbidden: You do not have access to this clip")
    if clip.get("status") != "ready_review":
        raise HTTPException(status_code=409, detail="Only reviewable clips can be rendered")

    ctx = get_clip_framing_context(clip)
    raw_mode = (payload.layout_mode if payload and payload.layout_mode else None) or clip.get("layout_mode") or ctx["layout_mode"]
    raw_mode = str(raw_mode).strip().lower()

    if raw_mode not in ctx["valid_mode_ids"]:
        raw_mode = ctx["layout_mode"]

    target_layout = raw_mode

    source_path = resolve_clip_source_path(clip)
    if not source_path or not source_path.exists():
        raise HTTPException(status_code=400, detail="Original source video chunk not found on disk")

    segments = None
    if clip.get("caption_data"):
        try:
            cap = json.loads(clip["caption_data"])
            groups = cap.get("groups", [])
            if groups:
                segments = [{"id": g["id"], "start": g["start"], "end": g["end"], "text": g.get("text", ""),
                             "words": g.get("words", [])} for g in groups]
        except Exception:
            pass

    if not segments and clip.get("chunk_id"):
        with db.get_db_connection() as conn:
            t_row = conn.execute("SELECT segments_json FROM transcripts WHERE chunk_id = ?", (clip["chunk_id"],)).fetchone()
            if t_row and t_row["segments_json"]:
                try:
                    segments = json.loads(t_row["segments_json"])
                except Exception:
                    pass

    start_t = float(clip["start_time"])
    end_t = min(float(clip["end_time"]), start_t + MAX_CLIP_DURATION)

    try:
        render_res = await asyncio.to_thread(
            render_clip,
            source_video=source_path,
            clip_id=clip_id,
            start_time=start_t,
            end_time=end_t,
            aspect_ratio=ctx["source_aspect_ratio"],
            layout_mode=target_layout,
            segments=segments,
            subtitle_options={"enabled": False, "preserve_timing": True},
            expected_range_revision=int(clip.get("range_revision") or 0),
        )
        out_file = Path(render_res["video_path"])
        if not out_file.exists() or out_file.stat().st_size == 0:
            raise RuntimeError("Render output file is missing or zero bytes")

        thumb_path_str = str(render_res["thumbnail_path"]) if render_res.get("thumbnail_path") else None
        thumb_name = Path(thumb_path_str).name if thumb_path_str else None
        with db.get_db_connection() as conn:
            cursor = conn.execute(
                "UPDATE clips SET layout_mode = ?, rendered_layout_mode = ?, video_path = ?, thumbnail_path = COALESCE(?, thumbnail_path), render_stale = 0 WHERE id = ? AND range_revision = ?",
                (target_layout, target_layout, str(out_file), thumb_path_str, clip_id, int(clip.get("range_revision") or 0))
            )
            conn.commit()
            if cursor.rowcount != 1:
                raise HTTPException(status_code=409, detail="Clip range changed while rendering. Reload and retry.")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        logger.error("Failed to render clip %s: %s", clip_id, e)
        raise HTTPException(status_code=500, detail=f"Couldn't render selected framing: {str(e)}")

    updated_clip = db.get_clip_by_id(clip_id)
    updated_ctx = get_clip_framing_context(updated_clip)

    return {
        "status": "success",
        "clip_id": clip_id,
        "layout_mode": target_layout,
        "source_orientation": updated_ctx["source_orientation"],
        "target_aspect": updated_ctx["target_aspect"],
        "available_layout_modes": updated_ctx["available_layout_modes"],
        "rendered_layout_mode": target_layout,
        "video_url": f"/clips/{out_file.name}",
        "thumbnail_url": f"/thumbnails/{thumb_name}" if thumb_name else None,
        "is_render_stale": False
    }


@app.post("/api/clips/{clip_id}/trim")
@_serialize_clip_mutation
async def handle_update_clip_trim(clip_id: str, payload: TrimClipRequest, request: Request):
    """Update clip active trim boundaries (CapCut-style trimming with safety handles)."""
    verify_dashboard_auth(request)
    caller_user_id = resolve_caller_user_id(request)
    clip = db.get_clip_by_id(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if caller_user_id and (not clip.get("user_id") or clip["user_id"] != caller_user_id):
        raise HTTPException(status_code=403, detail="Forbidden: You do not have access to this clip")
    if clip.get("status") != "ready_review":
        raise HTTPException(status_code=409, detail="Only reviewable clips can be trimmed")

    source_duration = None
    chunk_id = clip.get("chunk_id")
    chunk = db.get_chunk_by_id(chunk_id) if chunk_id else None
    if chunk and float(chunk.get("duration") or 0) > 0:
        source_duration = float(chunk["duration"])
    else:
        source_path = resolve_clip_source_path(clip)
        if source_path:
            from dispatch.ingestion.validator import inspect_video_orientation
            source_info = inspect_video_orientation(source_path)
            if source_info.get("valid") and float(source_info.get("duration") or 0) > 0:
                source_duration = float(source_info["duration"])

    success = db.update_clip_trim(
        clip_id, payload.start_time, payload.end_time, source_duration=source_duration,
        expected_range_revision=payload.range_revision,
    )
    if not success:
        latest = db.get_clip_by_id(clip_id)
        if payload.range_revision is not None and latest and int(latest.get("range_revision") or 0) != payload.range_revision:
            raise HTTPException(status_code=409, detail="The selected range changed elsewhere. Reload the clip and retry.")
        raise HTTPException(
            status_code=400,
            detail="Trim must stay inside the source and be between 5 and 180 seconds.",
        )

    updated = db.get_clip_by_id(clip_id)
    return {
        "status": "success",
        "clip_id": clip_id,
        "start_time": updated["start_time"],
        "end_time": updated["end_time"],
        "duration": updated["duration"],
        "range_revision": updated["range_revision"],
        "source_duration": source_duration,
        "padded_start": updated.get("padded_start"),
        "padded_end": updated.get("padded_end"),
        "is_render_stale": clip_render_is_stale(updated),
    }


@app.post("/api/clips/{clip_id}/approve")
@_serialize_clip_mutation
async def handle_approve_clip(clip_id: str, payload: ApproveRequest, request: Request):
    verify_dashboard_auth(request)
    caller_user_id = resolve_caller_user_id(request)
    return await _approve_clip_with_render(clip_id, payload, caller_user_id=caller_user_id)


async def _approve_clip_with_render(clip_id: str, payload: ApproveRequest, caller_user_id: Optional[str] = None):
    """Render the submitted caption snapshot and chosen layout before enqueueing publishing."""
    clip = db.get_clip_by_id(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if caller_user_id and (not clip.get("user_id") or clip["user_id"] != caller_user_id):
        raise HTTPException(status_code=403, detail="Forbidden: You do not have access to this clip")
    if clip.get("status") in ("approved", "published"):
        return {"status": "success", "message": f"Clip {clip_id} is already approved", "idempotent": True}
    if clip.get("status") != "ready_review":
        raise HTTPException(status_code=409, detail=f"Clip cannot be approved from status '{clip.get('status')}'")

    # Cloud caption reconciliation: the phone may have saved newer caption
    # edits in Dispatch Cloud while this PC was offline. Pull them (monotonic
    # revision guard, ownership-checked) BEFORE this render decides what
    # caption data is authoritative, so an offline edit can never be silently
    # skipped by the final render. Never fatal: on cloud errors the local
    # state remains what it was.
    try:
        import_cloud_captions_for_clip(clip_id)
        clip = db.get_clip_by_id(clip_id) or clip
    except Exception as e:
        logger.warning("Cloud caption reconciliation before approve failed for %s: %s", clip_id, e)

    current_revision = int(clip.get("range_revision") or 0)
    if payload.range_revision is not None and payload.range_revision != current_revision:
        raise HTTPException(status_code=409, detail="The selected range changed. Reload captions before approving.")

    ctx = get_clip_framing_context(clip)
    raw_mode = payload.layout_mode or clip.get("layout_mode") or ctx["layout_mode"]
    raw_mode = str(raw_mode).strip().lower()
    if raw_mode not in ctx["valid_mode_ids"]:
        raw_mode = ctx["layout_mode"]

    target_layout = raw_mode
    if payload.caption_track is not None:
        try:
            caption_track = CaptionTrack.from_dict(payload.caption_track)
            if caption_track.clip_id and caption_track.clip_id != clip_id:
                raise ValueError("Caption track belongs to another clip")
            caption_snapshot = caption_track.to_dict()
            saved = db.save_clip_caption_data(
                clip_id, json.dumps(caption_snapshot, ensure_ascii=False),
                expected_range_revision=current_revision,
                source_range_start=float(clip["start_time"]),
                source_range_end=float(clip["end_time"]),
                timing_warning=payload.caption_track.get("timing_warning"),
            )
            if not saved:
                raise HTTPException(status_code=409, detail="The selected range changed. Reload captions before approving.")
            clip = db.get_clip_by_id(clip_id) or clip
        except Exception as e:
            if isinstance(e, HTTPException):
                raise
            raise HTTPException(status_code=400, detail=f"Invalid caption track: {e}") from e
    else:
        try:
            caption_snapshot = json.loads(clip["caption_data"]) if clip.get("caption_data") else None
        except Exception:
            caption_snapshot = None
        if caption_snapshot and int(clip.get("caption_range_revision") or 0) != current_revision:
            raise HTTPException(status_code=409, detail="Saved captions belong to an older clip range. Reload and save captions before approving.")

    if clip.get("caption_timing_warning"):
        raise HTTPException(status_code=422, detail=str(clip["caption_timing_warning"]))
    if caption_snapshot:
        unreliable = [
            group for group in caption_snapshot.get("groups", [])
            if (not group.get("type") or group.get("type") == "caption")
            and (not group.get("has_word_timing", True) or group.get("timing_mode", "word") != "word")
        ]
        if unreliable:
            raise HTTPException(
                status_code=422,
                detail="These captions do not have reliable word-level timings. Retry transcription or remove the inexact caption groups before publishing.",
            )

    source_path = resolve_clip_source_path(clip)
    if not source_path or not source_path.is_file():
        raise HTTPException(
            status_code=400,
            detail="Cannot approve this clip: original source video is unavailable for final rendering."
        )

    # Never treat the rendered output as the "original source". Re-rendering from
    # a previously rendered clip would silently degrade quality and could pick
    # the wrong coordinate domain.
    if clip.get("video_path"):
        try:
            if Path(clip["video_path"]).resolve() == source_path.resolve():
                raise HTTPException(
                    status_code=400,
                    detail="Cannot approve this clip: the original source recording is missing; only a rendered copy exists.",
                )
        except OSError:
            pass

    # Honest failure for multi-segment coordinates. Session-global times must
    # not be applied to a single chunk file; until a stitched source is built,
    # refuse rather than render the wrong content.
    if clip.get("chunk_id"):
        with db.get_db_connection() as conn:
            chunk_row = conn.execute("SELECT duration FROM chunks WHERE id = ?", (clip["chunk_id"],)).fetchone()
        chunk_duration = float(chunk_row["duration"] or 0.0) if chunk_row else 0.0
        if chunk_duration > 0 and float(clip["end_time"]) > chunk_duration + 1.0:
            raise HTTPException(
                status_code=409,
                detail="This clip spans multiple recording segments. Approve it only after a stitched source is available; rendering a single chunk would use the wrong content.",
            )

    segments = None
    if caption_snapshot:
        groups = caption_snapshot.get("groups", [])
        if groups:
            segments = [{"id": g.get("id", index), "start": g.get("start", 0), "end": g.get("end", 0),
                         "text": g.get("text", ""), "words": g.get("words", [])}
                        for index, g in enumerate(groups)]
    if not segments and clip.get("chunk_id"):
        with db.get_db_connection() as conn:
            t_row = conn.execute("SELECT segments_json FROM transcripts WHERE chunk_id = ?", (clip["chunk_id"],)).fetchone()
            if t_row and t_row["segments_json"]:
                try:
                    segments = json.loads(t_row["segments_json"])
                except Exception:
                    segments = None

    start_t = float(clip["start_time"])
    end_t = min(float(clip["end_time"]), start_t + MAX_CLIP_DURATION)
    try:
        render_res = await asyncio.to_thread(
            render_clip,
            source_video=source_path,
            clip_id=clip_id,
            start_time=start_t,
            end_time=end_t,
            aspect_ratio=ctx["source_aspect_ratio"],
            layout_mode=target_layout,
            segments=segments,
            subtitle_options={"enabled": bool(caption_snapshot or segments), "caption_track": caption_snapshot, "preserve_timing": True},
            expected_range_revision=current_revision,
        )
        out_file = Path(render_res["video_path"])
        if not out_file.is_file() or out_file.stat().st_size == 0:
            raise RuntimeError("Final render is missing or empty")
        from dispatch.ingestion.validator import probe_video
        is_valid, probe_data, validation_error = probe_video(out_file)
        if not is_valid:
            raise RuntimeError(f"Final render failed playback validation: {validation_error or 'invalid media'}")
        actual_duration = float(probe_data.get("duration") or 0.0) if isinstance(probe_data, dict) else 0.0
        expected_duration = end_t - start_t
        if actual_duration > 0 and abs(actual_duration - expected_duration) > 1.0:
            raise RuntimeError(
                f"Final render duration {actual_duration:.2f}s does not match selected range {expected_duration:.2f}s"
            )
    except Exception as e:
        logger.error("Failed to render clip %s with layout %s: %s", clip_id, target_layout, e)
        raise HTTPException(
            status_code=500,
            detail=f"Couldn't create a valid final render: {str(e)}"
        ) from e

    latest = db.get_clip_by_id(clip_id)
    if not latest or int(latest.get("range_revision") or 0) != current_revision:
        with db.get_db_connection() as conn:
            conn.execute("UPDATE clips SET render_stale = 1 WHERE id = ?", (clip_id,))
            conn.commit()
        raise HTTPException(status_code=409, detail="The clip range changed while rendering. The render was marked stale; review and approve again.")

    db.approve_clip(
        clip_id=clip_id,
        custom_title=payload.title,
        custom_tags=payload.hashtags,
        custom_mode=payload.publish_mode,
        custom_platforms=payload.platforms,
        custom_layout=target_layout,
        custom_accounts=payload.account_ids
    )

    # Trigger immediate outbox pass so clip publishing starts immediately (unless disabled in tests)
    if not os.getenv("DISPATCH_DISABLE_AUTO_OUTBOX"):
        try:
            from dispatch.publisher.outbox import trigger_outbox_pass
            trigger_outbox_pass()
        except Exception as e:
            logger.warning("Could not trigger immediate outbox pass for clip %s: %s", clip_id, e)

    return {"status": "success", "message": f"Clip {clip_id} approved and enqueued for publishing", "layout_mode": target_layout}


@app.post("/api/clips/{clip_id}/reject")
@_serialize_clip_mutation
async def handle_reject_clip(clip_id: str, payload: RejectRequest, request: Request):
    """Reject a clip and record owner-scoped negative preference data."""
    verify_dashboard_auth(request)
    caller_user_id = resolve_caller_user_id(request)
    clip = db.get_clip_by_id(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if caller_user_id and (not clip.get("user_id") or clip["user_id"] != caller_user_id):
        raise HTTPException(status_code=403, detail="Forbidden: You do not have access to this clip")
    if clip.get("status") == "rejected":
        return {"status": "success", "message": f"Clip {clip_id} already rejected"}
    if clip.get("status") not in ("ready_review", "approved"):
        raise HTTPException(
            status_code=409,
            detail=f"Clip cannot be rejected from status '{clip.get('status')}'",
        )
    if not db.reject_clip(clip_id=clip_id, reason=payload.reason or "User rejected"):
        raise HTTPException(status_code=409, detail="Clip could not be rejected in its current state")
    return {"status": "success", "message": f"Clip {clip_id} rejected"}


@app.post("/api/clips/{clip_id}/metadata")
@_serialize_clip_mutation
async def handle_update_clip_metadata(clip_id: str, payload: ClipMetadataRequest, request: Request):
    """Persist InspectorPane metadata edits (title/hook/hashtags/platform targets).

    D8: wired to db.update_clip_metadata — enforced ready_review-only and
    revision-fenced inside the DB helper (approved/rejected/published clips keep
    their published metadata; stale revisions are refused).
    """
    verify_dashboard_auth(request)
    caller_user_id = resolve_caller_user_id(request)
    clip = db.get_clip_by_id(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if caller_user_id and (not clip.get("user_id") or clip["user_id"] != caller_user_id):
        raise HTTPException(status_code=403, detail="Forbidden: You do not have access to this clip")

    updated = db.update_clip_metadata(
        clip_id,
        title=payload.title,
        hook=payload.hook,
        hashtags=payload.hashtags,
        platform_targets=payload.platform_targets,
        expected_range_revision=payload.expected_range_revision,
    )
    if not updated:
        if clip.get("status") != "ready_review":
            raise HTTPException(status_code=409, detail="Only reviewable (ready) clips can edit metadata")
        raise HTTPException(
            status_code=409,
            detail="Clip changed while editing (stale range revision); reload the clip and retry"
        )

    return {
        "status": "success",
        "clip_id": clip_id,
        "saved": {
            "title": payload.title,
            "hook": payload.hook,
            "hashtags": payload.hashtags,
            "platform_targets": payload.platform_targets,
        },
    }


@app.get("/api/publisher/outbox")
async def get_publisher_outbox(request: Request, status: Optional[str] = None):
    """List publishing outbox jobs, optionally filtered by status.

    Owner-scoped: a non-system caller only sees its own jobs. The local owner
    (trusted loopback, no user header) retains full administrative visibility.
    """
    verify_dashboard_auth(request)
    caller_user_id = resolve_caller_user_id(request)
    clauses: list[str] = []
    params: list[Any] = []
    if caller_user_id:
        clauses.append("user_id = ?")
        params.append(caller_user_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM publishing_outbox{where} ORDER BY created_at DESC", tuple(params))
        jobs = [dict(row) for row in cursor.fetchall()]
    return {"jobs": jobs, "count": len(jobs)}


@app.get("/api/publisher/reconcile")
async def get_reconcile_jobs(request: Request):
    """List outbox jobs that require manual or automated reconciliation before retry."""
    verify_dashboard_auth(request)
    caller_user_id = resolve_caller_user_id(request)
    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        if caller_user_id:
            cursor.execute(
                "SELECT * FROM publishing_outbox WHERE status = 'reconciliation_required' AND user_id = ? ORDER BY updated_at DESC",
                (caller_user_id,),
            )
        else:
            cursor.execute("SELECT * FROM publishing_outbox WHERE status = 'reconciliation_required' ORDER BY updated_at DESC")
        jobs = [dict(row) for row in cursor.fetchall()]
    return {"jobs": jobs, "count": len(jobs)}


@app.post("/api/publisher/reconcile/{job_id}/resolve")
async def resolve_reconciliation_job(job_id: str, payload: ResolveReconciliationRequest, request: Request):
    """Manually resolve a reconciliation_required outbox job."""
    verify_dashboard_auth(request)
    caller_user_id = resolve_caller_user_id(request)
    action = payload.action.lower()
    if action not in ("mark_published", "retry", "mark_failed"):
        raise HTTPException(status_code=400, detail="Action must be one of: 'mark_published', 'retry', 'mark_failed'")

    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM publishing_outbox WHERE id = ?", (job_id,))
        job = cursor.fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="Outbox job not found")

        job_dict = dict(job)
        if caller_user_id and (job_dict.get("user_id") or "") != caller_user_id:
            raise HTTPException(status_code=403, detail="Forbidden: You do not have access to this job")
        if action == "mark_published":
            remote_id = payload.remote_id or job_dict.get("remote_id") or "manual_resolution"
            remote_url = payload.remote_url or job_dict.get("remote_url") or ""
            cursor.execute("""
                UPDATE publishing_outbox 
                SET status = 'published',
                    remote_id = ?,
                    remote_url = ?,
                    updated_at = CURRENT_TIMESTAMP,
                    published_at = CURRENT_TIMESTAMP,
                    last_error = ?
                WHERE id = ?
            """, (remote_id, remote_url, f"Resolved manually: {payload.note or 'Confirmed published'}", job_id))
            # Commit before the aggregator opens its own connection; otherwise the
            # second writer blocks on this transaction and the resolution rolls back.
            conn.commit()
            from dispatch.publisher.outbox import check_and_finalize_clip
            vpath = Path(job_dict["video_path"]) if job_dict.get("video_path") else None
            check_and_finalize_clip(job_dict["clip_id"], vpath)
            return {"status": "success", "message": f"Job {job_id} marked as published", "action": action}

        elif action == "retry":
            cursor.execute("""
                UPDATE publishing_outbox 
                SET status = 'queued',
                    updated_at = CURRENT_TIMESTAMP,
                    next_retry_at = CURRENT_TIMESTAMP,
                    last_error = ?
                WHERE id = ?
            """, (f"Reconciliation resolved: retry queued. Note: {payload.note or 'Manual retry'}", job_id))
            if not os.getenv("DISPATCH_DISABLE_AUTO_OUTBOX"):
                from dispatch.publisher.outbox import trigger_outbox_pass
                trigger_outbox_pass()
            return {"status": "success", "message": f"Job {job_id} requeued for publishing", "action": action}

        elif action == "mark_failed":
            cursor.execute("""
                UPDATE publishing_outbox 
                SET status = 'failed',
                    updated_at = CURRENT_TIMESTAMP,
                    last_error = ?
                WHERE id = ?
            """, (f"Reconciliation resolved as failed: {payload.note or 'User marked failed'}", job_id))
            return {"status": "success", "message": f"Job {job_id} marked as failed", "action": action}


@app.post("/api/publisher/reconcile/{job_id}/auto-reconcile")
async def auto_reconcile_job(job_id: str, request: Request):
    """Automatically reconcile an outbox job against platform API."""
    verify_dashboard_auth(request)
    caller_user_id = resolve_caller_user_id(request)
    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM publishing_outbox WHERE id = ?", (job_id,))
        job = cursor.fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="Outbox job not found")
        job_dict = dict(job)
    if caller_user_id and (job_dict.get("user_id") or "") != caller_user_id:
        raise HTTPException(status_code=403, detail="Forbidden: You do not have access to this job")

    platform = job_dict.get("platform", "").lower()
    if platform not in ("youtube", "youtube_shorts"):
        return {
            "status": "unsupported",
            "message": f"Automated remote reconciliation is currently only supported for YouTube. Platform '{platform}' requires manual resolution.",
            "reconciled": False
        }

    from dispatch.publisher.outbox import reconcile_youtube_job, check_and_finalize_clip
    result = reconcile_youtube_job(job_dict)

    if not result.get("reconciled"):
        return {
            "status": "inconclusive",
            "message": f"Automated reconciliation inconclusive: {result.get('reason')}",
            "reconciled": False
        }

    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        if result.get("published"):
            vid = result["remote_id"]
            vurl = result.get("remote_url", f"https://www.youtube.com/watch?v={vid}")
            cursor.execute("""
                UPDATE publishing_outbox 
                SET status = 'published',
                    remote_id = ?,
                    remote_url = ?,
                    updated_at = CURRENT_TIMESTAMP,
                    published_at = CURRENT_TIMESTAMP,
                    last_error = 'Auto-reconciled: found confirmed upload on YouTube'
                WHERE id = ?
            """, (vid, vurl, job_id))
            # Commit the job transition before the clip aggregator reads from a
            # second connection (avoids the SQLite write-lock deadlock).
            conn.commit()
            vpath = Path(job_dict["video_path"]) if job_dict.get("video_path") else None
            check_and_finalize_clip(job_dict["clip_id"], vpath)
            return {
                "status": "success",
                "message": f"Upload verified on YouTube (Video ID: {vid}). Marked published.",
                "reconciled": True,
                "published": True,
                "remote_id": vid,
                "remote_url": vurl
            }
        else:
            cursor.execute("""
                UPDATE publishing_outbox 
                SET status = 'queued',
                    next_retry_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP,
                    last_error = 'Auto-reconciled: confirmed absent on YouTube; safe to retry'
                WHERE id = ?
            """, (job_id,))
            if not os.getenv("DISPATCH_DISABLE_AUTO_OUTBOX"):
                from dispatch.publisher.outbox import trigger_outbox_pass
                trigger_outbox_pass()
            return {
                "status": "success",
                "message": "Video confirmed absent on YouTube. Requeued for safe publishing retry.",
                "reconciled": True,
                "published": False
            }


_filmstrip_locks: Dict[str, asyncio.Lock] = {}
_filmstrip_locks_guard = asyncio.Lock()


async def get_filmstrip_lock(clip_id: str) -> asyncio.Lock:
    """Retrieve or create an asyncio Lock dedicated to a specific clip ID."""
    async with _filmstrip_locks_guard:
        if clip_id not in _filmstrip_locks:
            _filmstrip_locks[clip_id] = asyncio.Lock()
        return _filmstrip_locks[clip_id]


@app.get("/api/clips/{clip_id}/filmstrip")
async def get_clip_filmstrip(clip_id: str, request: Request):
    """Return an array of thumbnail frames spanning the video duration for the timeline filmstrip.
    Guarantees freshness: automatically detects when underlying video has changed and regenerates frames.
    Serializes concurrent extraction per clip_id and uses atomic temporary files.
    """
    caller_user_id = resolve_caller_user_id(request)
    clip = db.get_clip_by_id(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if caller_user_id and (not clip.get("user_id") or clip["user_id"] != caller_user_id):
        raise HTTPException(status_code=403, detail="Forbidden: You do not have access to this clip")

    active_video = resolve_active_clip_video(clip)
    if not active_video or not active_video.exists():
        thumb = ensure_clip_thumbnail(clip)
        if thumb and thumb.exists():
            v_tok = get_thumbnail_version_token(thumb) or str(int(thumb.stat().st_mtime))
            return {"frames": [f"/clips/{thumb.name}?v={v_tok}"]}
        return {"frames": []}

    video_mtime_ns = getattr(active_video.stat(), "st_mtime_ns", int(active_video.stat().st_mtime * 1e9))
    video_fingerprint = get_file_fingerprint(active_video)

    lock = await get_filmstrip_lock(clip_id)
    async with lock:
        # Check for existing pre-extracted frames and validate freshness against active video
        existing_frames = []
        is_filmstrip_stale = False
        for i in range(12):
            frame_name = f"{clip_id}_frame_{i}.jpg"
            frame_path = CLIPS_DIR / frame_name
            meta_path = CLIPS_DIR / f"{clip_id}_frame_{i}.jpg.meta"
            if frame_path.exists():
                if meta_path.exists():
                    try:
                        m_data = json.loads(meta_path.read_text(encoding="utf-8"))
                        if video_mtime_ns > m_data.get("video_mtime_ns", 0) or (video_fingerprint and video_fingerprint != m_data.get("video_fingerprint")):
                            is_filmstrip_stale = True
                            break
                    except Exception:
                        is_filmstrip_stale = True
                        break
                else:
                    f_st = frame_path.stat()
                    f_mtime_ns = getattr(f_st, "st_mtime_ns", int(f_st.st_mtime * 1e9))
                    if f_mtime_ns < video_mtime_ns:
                        is_filmstrip_stale = True
                        break
                frame_mtime_ns = getattr(frame_path.stat(), "st_mtime_ns", int(frame_path.stat().st_mtime * 1e9))
                existing_frames.append(f"/clips/{frame_name}?v={frame_mtime_ns}")

        if len(existing_frames) >= 4 and not is_filmstrip_stale:
            return {"frames": existing_frames}

        # Extract dynamically if video exists on disk
        def _extract_frames() -> List[str]:
            count = 8
            dur = max(1.0, float(clip.get("duration") or 10.0))
            dyn_frames = []
            for i in range(count):
                t = (i + 0.5) * (dur / count)
                frame_name = f"{clip_id}_frame_{i}.jpg"
                out_path = CLIPS_DIR / frame_name
                nonce = uuid.uuid4().hex[:6]
                tmp_path = CLIPS_DIR / f"{clip_id}_frame_{i}.tmp_{nonce}.jpg"
                tmp_meta_path = CLIPS_DIR / f"{clip_id}_frame_{i}.tmp_{nonce}.jpg.meta"
                try:
                    res = subprocess.run([
                        "ffmpeg", "-y", "-ss", str(round(t, 3)), "-i", str(active_video),
                        "-frames:v", "1", "-q:v", "3", "-vf", "scale=160:-1",
                        str(tmp_path)
                    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                    if res.returncode == 0 and tmp_path.exists() and tmp_path.stat().st_size > 0:
                        meta = {
                            "video_mtime_ns": video_mtime_ns,
                            "video_fingerprint": video_fingerprint,
                        }
                        tmp_meta_path.write_text(json.dumps(meta), encoding="utf-8")
                        os.replace(str(tmp_path), str(out_path))
                        if tmp_meta_path.exists():
                            os.replace(str(tmp_meta_path), str(CLIPS_DIR / f"{clip_id}_frame_{i}.jpg.meta"))
                        frame_mtime_ns = getattr(out_path.stat(), "st_mtime_ns", int(out_path.stat().st_mtime * 1e9))
                        dyn_frames.append(f"/clips/{frame_name}?v={frame_mtime_ns}")
                except Exception as e:
                    logger.debug("Failed extracting filmstrip frame %d for %s: %s", i, clip_id, e)
                finally:
                    if tmp_path.exists():
                        try:
                            tmp_path.unlink()
                        except Exception:
                            pass
                    if tmp_meta_path.exists():
                        try:
                            tmp_meta_path.unlink()
                        except Exception:
                            pass
            return dyn_frames

        frames = await asyncio.to_thread(_extract_frames)
        if frames:
            return {"frames": frames}

        # Fallback to clip thumbnail
        thumb = ensure_clip_thumbnail(clip)
        if thumb and thumb.exists():
            v_tok = get_thumbnail_version_token(thumb) or str(int(thumb.stat().st_mtime))
            return {"frames": [f"/clips/{thumb.name}?v={v_tok}"]}

        return {"frames": []}


@app.get("/api/presets/captions")
async def get_caption_presets():
    """Return all curated CapCut/viral caption style presets."""
    return {
        preset_id: {
            "id": data["id"],
            "name": data["name"],
            "description": data["description"],
            "style": data["style"].__dict__,
            "animation": data["animation"].__dict__,
            "layout": data["layout"].__dict__,
        }
        for preset_id, data in CAPTION_PRESETS.items()
    }


@app.get("/api/clips/{clip_id}/captions")
async def get_clip_captions(clip_id: str, request: Request):
    """Return saved captions or derive a lightweight preview from the stored transcript."""
    caller_user_id = resolve_caller_user_id(request)
    clip = db.get_clip_by_id(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if caller_user_id and (not clip.get("user_id") or clip["user_id"] != caller_user_id):
        raise HTTPException(status_code=403, detail="Forbidden: You do not have access to this clip")

    cached_json = db.get_clip_caption_data(clip_id)
    current_revision = int(clip.get("range_revision") or 0)
    if cached_json:
        try:
            cached_track = json.loads(cached_json)
            if (
                isinstance(cached_track, dict)
                and isinstance(cached_track.get("groups"), list)
                and int(clip.get("caption_range_revision") if clip.get("caption_range_revision") is not None else current_revision) == current_revision
            ):
                # Ensure Roman Hinglish transliteration on cached tracks
                groups = cached_track.get("groups", [])
                tokens = []
                positions = []
                for g_idx, grp in enumerate(groups):
                    for w_idx, w in enumerate(grp.get("words", [])):
                        tok = w.get("word") or w.get("text") or ""
                        tokens.append(tok)
                        positions.append((g_idx, w_idx))
                from dispatch.ai_clips.highlight_finder import has_devanagari, transliterate_tokens_to_hinglish
                if any(has_devanagari(t) for t in tokens):
                    transliterated = transliterate_tokens_to_hinglish(tokens)
                    for (g_idx, w_idx), new_word in zip(positions, transliterated):
                        w = groups[g_idx]["words"][w_idx]
                        if "word" in w:
                            w["word"] = new_word
                        if "text" in w:
                            w["text"] = new_word
                    for grp in groups:
                        words = grp.get("words", [])
                        if words:
                            grp["text"] = " ".join((w.get("word") or w.get("text") or "") for w in words)
                    db.save_clip_caption_data(clip_id, json.dumps(cached_track), expected_range_revision=current_revision)

                cached_track["range_revision"] = current_revision
                cached_track["timing_warning"] = clip.get("caption_timing_warning")
                if not cached_track["timing_warning"] and any(
                    not group.get("has_word_timing", True) or group.get("timing_mode") == "phrase"
                    for group in cached_track.get("groups", [])
                    if group.get("type", "caption") == "caption"
                ):
                    cached_track["timing_warning"] = "Some captions have phrase-level timing only. Retry transcription before publishing."
                return cached_track
        except Exception:
            pass

    chunk_id = clip.get("chunk_id")
    session_id = clip.get("session_id")
    clip_start = float(clip.get("start_time", 0.0))
    clip_end = float(clip.get("end_time", clip_start + 15.0))
    if (clip_end - clip_start) > MAX_CLIP_DURATION:
        clip_end = clip_start + MAX_CLIP_DURATION

    # Fetch transcript segments strictly matching this clip's chunk or session
    segments = []
    with db.get_db_connection() as conn:
        cursor = conn.cursor()

        # 1. Direct match on chunk_id in transcripts
        if chunk_id:
            cursor.execute("SELECT segments_json FROM transcripts WHERE chunk_id = ?", (chunk_id,))
            t_row = cursor.fetchone()
            if t_row and t_row["segments_json"]:
                try:
                    segments = json.loads(t_row["segments_json"])
                except Exception:
                    segments = []

        # 2. Match on youtube_inbox via chunk's file_hash (e.g. yt_{video_id})
        if not segments and chunk_id:
            cursor.execute("SELECT file_hash FROM chunks WHERE id = ?", (chunk_id,))
            chk_row = cursor.fetchone()
            if chk_row and chk_row["file_hash"] and str(chk_row["file_hash"]).startswith("yt_"):
                yt_vid = str(chk_row["file_hash"])[3:]
                cursor.execute("SELECT segments_json FROM youtube_inbox WHERE video_id = ?", (yt_vid,))
                yt_row = cursor.fetchone()
                if yt_row and yt_row["segments_json"]:
                    try:
                        segments = json.loads(yt_row["segments_json"])
                    except Exception:
                        segments = []

        # 3. Match on session_id in transcripts
        if not segments and session_id:
            cursor.execute("SELECT segments_json FROM transcripts WHERE session_id = ? LIMIT 1", (session_id,))
            s_row = cursor.fetchone()
            if s_row and s_row["segments_json"]:
                try:
                    segments = json.loads(s_row["segments_json"])
                except Exception:
                    segments = []

        # 4. Match on youtube_inbox by dispatch_id or video_id matching session_id
        if not segments and session_id:
            cursor.execute("SELECT segments_json FROM youtube_inbox WHERE dispatch_id = ? OR video_id = ? LIMIT 1", (session_id, session_id))
            yts_row = cursor.fetchone()
            if yts_row and yts_row["segments_json"]:
                try:
                    segments = json.loads(yts_row["segments_json"])
                except Exception:
                    segments = []

    # If no transcript exists for that specific source, handle honestly instead of fabricating words
    if not segments:
        preset_data = get_preset(get_default_preset_id())
        empty_track = CaptionTrack(
            clip_id=clip_id,
            language="en",
            groups=[],
            default_style=preset_data["style"],
            default_layout=preset_data["layout"],
            default_animation=preset_data["animation"]
        )
        track_dict = empty_track.to_dict()
        timing_warning = None
        if cached_json:
            try:
                from dispatch.captions.range_sync import rebase_caption_track
                old_track = json.loads(cached_json)
                old_start = float(clip.get("caption_range_start") if clip.get("caption_range_start") is not None else clip_start)
                old_end = float(clip.get("caption_range_end") if clip.get("caption_range_end") is not None else clip_end)
                track_dict, needs_warning = rebase_caption_track(old_track, old_start, clip_start, clip_end)
                if needs_warning or clip_start < old_start or clip_end > old_end:
                    timing_warning = "The selected range exceeds saved caption coverage, and no persisted source transcript is available to align the added footage. Retry transcription before publishing."
            except Exception as error:
                logger.warning("Could not safely rebase captions for clip %s: %s", clip_id, error)
                timing_warning = "Saved captions could not be aligned safely to the selected range. Retry transcription before publishing."
        if any(
            not group.get("has_word_timing", True) or group.get("timing_mode") == "phrase"
            for group in track_dict.get("groups", []) if group.get("type", "caption") == "caption"
        ):
            timing_warning = "Some captions have phrase-level timing only. Retry transcription before publishing."
        track_dict["range_revision"] = current_revision
        track_dict["timing_warning"] = timing_warning
        return track_dict

    # Transliterate Devanagari Hindi text to natural Roman Hinglish with 1:1 token mapping
    clean_segments = transliterate_segments_to_hinglish(segments)

    effective_user_id = caller_user_id or clip.get("user_id")
    user_tpl = db.get_default_template(user_id=effective_user_id)
    tpl_captions = user_tpl.get("captions", {}) if isinstance(user_tpl, dict) else {}
    preset_candidate = tpl_captions.get("preset_id")
    effective_preset_id = preset_candidate if (preset_candidate and preset_candidate != "custom") else get_default_preset_id()

    track = group_words_into_captions(
        raw_segments=clean_segments,
        clip_start=clip_start,
        clip_end=clip_end,
        clip_id=clip_id,
        preset_id=effective_preset_id
    )
    apply_template_to_track(track, tpl_captions)

    track_dict = track.to_dict()
    timing_warning = None
    if any(not group.has_word_timing or group.timing_mode != "word" for group in track.groups if group.type == "caption"):
        timing_warning = "Some transcript captions have phrase-level timing only. Retry transcription before publishing."

    # If a saved track belongs to an older selection, rebase its clip-relative
    # words and append exact words newly exposed by the range expansion.
    if cached_json:
        try:
            from dispatch.captions.range_sync import rebase_caption_track
            cached_track = json.loads(cached_json)
            old_start = float(clip.get("caption_range_start") if clip.get("caption_range_start") is not None else clip["start_time"])
            old_end = float(clip.get("caption_range_end") if clip.get("caption_range_end") is not None else clip["end_time"])
            transcript_dict = track_dict if segments else None
            track_dict, needs_warning = rebase_caption_track(
                cached_track, old_start, clip_start, clip_end, transcript_dict
            )
            if needs_warning:
                timing_warning = "The expanded range contains transcript without reliable word-level timings. Retry transcription before publishing."
            elif not any(
                not group.get("has_word_timing", True) or group.get("timing_mode") == "phrase"
                for group in track_dict.get("groups", []) if group.get("type", "caption") == "caption"
            ):
                timing_warning = None
        except Exception as error:
            logger.warning("Could not safely rebase captions for clip %s: %s", clip_id, error)
            timing_warning = "Saved captions could not be aligned safely to the selected range. Retry transcription before publishing."
            track_dict = track.to_dict() if segments else {**track_dict, "groups": []}

    track_dict["range_revision"] = current_revision
    track_dict["timing_warning"] = timing_warning
    return track_dict


@app.post("/api/clips/{clip_id}/captions")
@_serialize_clip_mutation
async def save_clip_captions(clip_id: str, request: Request):
    """Save customized CaptionTrack JSON and export updated ASS file for FFmpeg render parity."""
    verify_dashboard_auth(request)
    caller_user_id = resolve_caller_user_id(request)
    clip = db.get_clip_by_id(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if caller_user_id and (not clip.get("user_id") or clip["user_id"] != caller_user_id):
        raise HTTPException(status_code=403, detail="Forbidden: You do not have access to this clip")
    if clip.get("status") != "ready_review":
        raise HTTPException(status_code=409, detail="Only reviewable clips can change captions")

    data = await request.json()
    track = CaptionTrack.from_dict(data)
    if track.clip_id and track.clip_id != clip_id:
        raise HTTPException(status_code=409, detail="Caption track belongs to another clip")
    expected_revision = data.get("range_revision", int(clip.get("range_revision") or 0))
    if expected_revision != int(clip.get("range_revision") or 0):
        raise HTTPException(status_code=409, detail="The selected range changed. Reload captions before saving.")
    current_captions_revision = int(clip.get("captions_revision") or 0)
    expected_captions_revision: Optional[int] = None
    if data.get("base_revision") is not None:
        try:
            expected_captions_revision = int(data.get("base_revision"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="base_revision must be an integer")
        if expected_captions_revision != current_captions_revision:
            raise HTTPException(
                status_code=409,
                detail="Captions changed on another device. Reload the clip before saving.",
            )
    track_dict = track.to_dict()
    timing_warning = data.get("timing_warning")
    if any(not group.has_word_timing or group.timing_mode != "word" for group in track.groups if group.type in ("caption", "")):
        timing_warning = "Some transcript captions have phrase-level timing only. Retry transcription before publishing."
    if not db.save_clip_caption_data(
        clip_id, json.dumps(track_dict), expected_range_revision=expected_revision,
        source_range_start=float(clip["start_time"]), source_range_end=float(clip["end_time"]),
        timing_warning=timing_warning,
        expected_captions_revision=expected_captions_revision,
    ):
        raise HTTPException(
            status_code=409,
            detail="Captions or the selected range changed while editing. Reload the clip before saving.",
        )

    # Export ASS file with exact parity
    ass_path = PROCESSING_DIR / f"{clip_id}.ass"
    export_to_ass(track, ass_path)

    return {
        "status": "success",
        "message": "Captions saved and exported to ASS",
        "range_revision": expected_revision,
        "captions_revision": current_captions_revision + 1,
    }


@app.post("/api/media/upload")
async def upload_editor_media(request: Request, file: UploadFile = File(...)):
    """Accept user-imported overlay, audio track, or voiceover recording.
    Validates file type and size, writes durably to CLIPS_DIR, and returns a permanent URL.
    """
    verify_dashboard_auth(request)
    import uuid

    filename = file.filename or "media_upload"
    ext = Path(filename).suffix.lower()
    allowed_exts = {
        ".mp3", ".wav", ".aac", ".m4a", ".ogg", ".webm",
        ".mp4", ".mov",
        ".png", ".jpg", ".jpeg", ".webp"
    }
    if ext not in allowed_exts:
        raise HTTPException(status_code=400, detail=f"Unsupported file format: {ext}. Allowed: {', '.join(sorted(allowed_exts))}")

    media_type = "audio" if ext in {".mp3", ".wav", ".aac", ".m4a", ".ogg"} else (
        "video" if ext in {".mp4", ".mov"} else (
            "audio" if "audio" in (file.content_type or "") else (
                "video" if "video" in (file.content_type or "") else "image"
            )
        )
    )

    safe_name = f"media_{int(time.time())}_{uuid.uuid4().hex[:8]}{ext}"
    dest_path = CLIPS_DIR / safe_name
    MAX_MEDIA_BYTES = 100 * 1024 * 1024  # 100MB

    total_bytes = 0
    try:
        with open(dest_path, "wb") as f:
            while True:
                chunk = await file.read(64 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_MEDIA_BYTES:
                    dest_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="Upload file exceeds maximum limit of 100MB")
                f.write(chunk)
    except Exception:
        dest_path.unlink(missing_ok=True)
        raise

    logger.info("Editor media uploaded durably: %s (%d bytes)", safe_name, total_bytes)
    return {
        "status": "success",
        "filename": safe_name,
        "url": f"/clips/{safe_name}",
        "media_type": media_type,
        "size_bytes": total_bytes
    }


@app.post("/api/recordings/upload")
async def upload_web_recording(request: Request, file: UploadFile = File(...)):
    """Accept test recording from browser camera, save to storage, register session and chunk, extract clip, and prepare for review."""
    verify_dashboard_auth(request)
    import subprocess
    import shutil
    from dispatch.ingestion.validator import inspect_video_orientation, calculate_file_hash

    timestamp = int(time.time())
    session_id = f"sess_web_{timestamp}"
    chunk_id = f"chk_web_{timestamp}"
    clip_id = f"clip_web_{timestamp}"

    # 1. Save uploaded WebM file using chunked streaming with size limit (max 500MB)
    MAX_RECORDING_UPLOAD_BYTES = 500 * 1024 * 1024
    temp_webm = PROCESSING_DIR / f"{chunk_id}.webm"
    total_bytes = 0
    try:
        with open(temp_webm, "wb") as f:
            while True:
                chunk = await file.read(64 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_RECORDING_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Upload file exceeds maximum limit of 500MB")
                f.write(chunk)
    except Exception:
        temp_webm.unlink(missing_ok=True)
        raise

    # 2. Standardize raw source chunk to MP4 in PROCESSING_DIR
    chunk_path = PROCESSING_DIR / f"{chunk_id}.mp4"
    cmd = [
        "ffmpeg", "-y", "-i", str(temp_webm),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(chunk_path)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if temp_webm.exists():
        temp_webm.unlink(missing_ok=True)

    if not chunk_path.exists() or chunk_path.stat().st_size == 0:
        raise HTTPException(status_code=500, detail="Failed to transcode uploaded recording.")

    # 3. Inspect video orientation, dimensions, and duration
    info = inspect_video_orientation(chunk_path)
    width = info.get("effective_width") or info.get("width") or 1080
    height = info.get("effective_height") or info.get("height") or 1920
    orientation = info.get("orientation", "portrait")
    aspect_ratio = "9:16" if orientation == "portrait" else "16:9"
    duration = max(1.0, float(info.get("duration") or 30.0))
    file_hash = calculate_file_hash(chunk_path)

    # 4. Prepare initial clip in CLIPS_DIR
    clip_video_path = CLIPS_DIR / f"{clip_id}.mp4"
    shutil.copy2(chunk_path, clip_video_path)

    # Generate thumbnail
    thumb_path = CLIPS_DIR / f"{clip_id}.jpg"
    thumb_time = min(1.0, duration / 2.0)
    subprocess.run([
        "ffmpeg", "-y", "-ss", str(round(thumb_time, 3)), "-i", str(clip_video_path),
        "-vframes", "1", "-q:v", "2", str(thumb_path)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Extract filmstrip frames for the timeline
    for i in range(8):
        t = (i + 0.5) * (duration / 8)
        frame_name = f"{clip_id}_frame_{i}.jpg"
        subprocess.run([
            "ffmpeg", "-y", "-ss", str(round(t, 3)), "-i", str(clip_video_path),
            "-frames:v", "1", "-q:v", "3", "-vf", "scale=160:-1",
            str(CLIPS_DIR / frame_name)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Initial layout mode: portrait -> native_916, landscape -> crop_916
    initial_layout = "native_916" if orientation == "portrait" else "crop_916"

    # 5. Insert into sessions, chunks, and clips tables atomically
    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO sessions (id, status) VALUES (?, 'completed')", (session_id,))
        cursor.execute("""
            INSERT OR REPLACE INTO chunks (
                id, session_id, filename, filepath, file_hash, duration,
                width, height, aspect_ratio, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'processed')
        """, (chunk_id, session_id, chunk_path.name, str(chunk_path), file_hash, duration, width, height, aspect_ratio))
        cursor.execute("""
            INSERT OR REPLACE INTO clips (
                id, session_id, chunk_id, start_time, end_time, duration,
                title, hook, description, hashtags, virality_score,
                layout_mode, rendered_layout_mode, status, publish_mode, platform_targets,
                video_path, thumbnail_path
            ) VALUES (
                ?, ?, ?, 0.0, ?, ?,
                'Web Studio Recording', 'Consistency beats motivation every single day',
                'Recorded directly from Dispatch Web Studio. #Shorts #Dispatch',
                '#Shorts #Dispatch #Creator', 92,
                ?, ?, 'ready_review', 'public', 'youtube,instagram',
                ?, ?
            )
        """, (
            clip_id, session_id, chunk_id, duration, duration,
            initial_layout, initial_layout,
            str(clip_video_path), str(thumb_path) if thumb_path.exists() else None
        ))
        conn.commit()

    return {
        "status": "success",
        "clip_id": clip_id,
        "session_id": session_id,
        "chunk_id": chunk_id,
        "video_url": f"/clips/{clip_video_path.name}",
        "duration": duration,
        "message": "Recording uploaded and ready for review!"
    }


@app.get("/api/recordings")
async def get_recordings(request: Request):
    """Return list of recordings/chunks formatted for the Sessions tab.
    Scoped strictly to the authenticated user.
    """
    caller_user_id = resolve_caller_user_id(request, allow_lan_peer=True)
    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        if caller_user_id:
            cursor.execute("""
                SELECT 
                    c.id as chunk_id,
                    c.session_id,
                    c.filename,
                    c.filepath,
                    COALESCE(NULLIF(c.duration, 0.0), y.duration, 0.0) as duration,
                    c.status as chunk_status,
                    c.created_at,
                    s.status as session_status,
                    s.created_at as session_created_at,
                    COUNT(cl.id) as clip_count,
                    COUNT(CASE WHEN cl.status = 'approved' OR cl.status = 'published' THEN 1 END) as approved_clip_count,
                    ROW_NUMBER() OVER (
                        PARTITION BY COALESCE(c.session_id, c.id) 
                        ORDER BY c.created_at ASC
                    ) as seq_num,
                    y.video_id as youtube_video_id
                FROM chunks c
                LEFT JOIN sessions s ON c.session_id = s.id
                LEFT JOIN clips cl ON cl.chunk_id = c.id
                LEFT JOIN youtube_inbox y ON (c.file_hash = 'yt_' || y.video_id OR y.local_video_path = c.filepath OR c.filename LIKE '%' || y.video_id || '%')
                WHERE c.user_id = ?
                GROUP BY c.id
                ORDER BY c.created_at DESC
            """, (caller_user_id,))
            rows = cursor.fetchall()

            cursor.execute("""
                SELECT 
                    y.video_id,
                    y.session_id,
                    y.title,
                    y.local_video_path,
                    y.duration,
                    y.status as inbox_status,
                    y.discovered_at,
                    y.sequence_number
                FROM youtube_inbox y
                WHERE y.user_id = ? AND y.video_id NOT IN (
                    SELECT REPLACE(c.file_hash, 'yt_', '') FROM chunks c WHERE c.file_hash LIKE 'yt_%'
                )
                ORDER BY y.discovered_at DESC
            """, (caller_user_id,))
            inbox_rows = cursor.fetchall()
        else:
            cursor.execute("""
                SELECT 
                    c.id as chunk_id,
                    c.session_id,
                    c.filename,
                    c.filepath,
                    COALESCE(NULLIF(c.duration, 0.0), y.duration, 0.0) as duration,
                    c.status as chunk_status,
                    c.created_at,
                    s.status as session_status,
                    s.created_at as session_created_at,
                    COUNT(cl.id) as clip_count,
                    COUNT(CASE WHEN cl.status = 'approved' OR cl.status = 'published' THEN 1 END) as approved_clip_count,
                    ROW_NUMBER() OVER (
                        PARTITION BY COALESCE(c.session_id, c.id) 
                        ORDER BY c.created_at ASC
                    ) as seq_num,
                    y.video_id as youtube_video_id
                FROM chunks c
                LEFT JOIN sessions s ON c.session_id = s.id
                LEFT JOIN clips cl ON cl.chunk_id = c.id
                LEFT JOIN youtube_inbox y ON (c.file_hash = 'yt_' || y.video_id OR y.local_video_path = c.filepath OR c.filename LIKE '%' || y.video_id || '%')
                GROUP BY c.id
                ORDER BY c.created_at DESC
            """)
            rows = cursor.fetchall()

            cursor.execute("""
                SELECT 
                    y.video_id,
                    y.session_id,
                    y.title,
                    y.local_video_path,
                    y.duration,
                    y.status as inbox_status,
                    y.discovered_at,
                    y.sequence_number
                FROM youtube_inbox y
                WHERE y.video_id NOT IN (
                    SELECT REPLACE(c.file_hash, 'yt_', '') FROM chunks c WHERE c.file_hash LIKE 'yt_%'
                )
                ORDER BY y.discovered_at DESC
            """)
            inbox_rows = cursor.fetchall()

    recordings = []
    for r in rows:
        chunk_id = r["chunk_id"]
        session_id = r["session_id"] or f"sess_{chunk_id}"
        yt_id = r["youtube_video_id"]
        
        # Genuine granular status mapping (never fake success)
        c_status = (r["chunk_status"] or "").lower()
        if c_status in ("processed", "transcribed", "verified") or yt_id:
            st = "UPLOADED_TO_YOUTUBE"
        elif c_status == "transcribing":
            st = "PROCESSING"
        elif c_status == "incoming":
            st = "QUEUED_FOR_UPLOAD"
        elif c_status == "failed":
            st = "FAILED_RETRY"
        else:
            st = "QUEUED_FOR_UPLOAD"

        # Strictly raw source video preview — NEVER captioned clips!
        video_url = None
        if r["filepath"] and Path(r["filepath"]).exists():
            video_url = f"/api/recordings/{chunk_id}/source"
        elif Path(r["filepath"] or "").name:
            fallback_path = STORAGE_DIR / "incoming" / Path(r["filepath"]).name
            if fallback_path.exists():
                video_url = f"/api/recordings/{chunk_id}/source"

        file_size = 0
        try:
            if r["filepath"]:
                fp = Path(r["filepath"])
                if fp.exists():
                    file_size = fp.stat().st_size
        except Exception:
            pass

        created_ts = int(time.time() * 1000)
        try:
            if r["created_at"]:
                dt = datetime.fromisoformat(str(r["created_at"]).replace(" ", "T"))
                created_ts = int(dt.timestamp() * 1000)
        except Exception:
            pass

        recordings.append({
            "segmentId": chunk_id,
            "sessionId": session_id,
            "sequenceNumber": int(r["seq_num"] or 1),
            "filename": r["filename"] or f"{chunk_id}.mp4",
            "filepath": r["filepath"] or "",
            "fileSizeBytes": file_size,
            "sha256Hash": "",
            "status": st,
            "chunkStatus": r["chunk_status"],
            "sessionStatus": r["session_status"],
            "createdAt": created_ts,
            "finalizedAt": created_ts,
            "durationSeconds": float(r["duration"] or 0),
            "videoUrl": video_url,
            "clipCount": int(r["clip_count"] or 0),
            "approvedClipCount": int(r["approved_clip_count"] or 0),
            "youtubeVideoId": yt_id,
        })

    for iy in inbox_rows:
        vid = iy["video_id"]
        sess_id = iy["session_id"] or f"sess_{vid}"
        inbox_st = (iy["inbox_status"] or "").upper()
        if inbox_st in ("DOWNLOADING", "VALIDATING"):
            st = "PROCESSING"
        elif inbox_st == "FAILED":
            st = "FAILED_RETRY"
        else:
            st = "UPLOADED_TO_YOUTUBE"

        video_url = None
        file_size = 0
        local_path = iy["local_video_path"]
        if local_path and Path(local_path).exists():
            video_url = f"/api/recordings/{vid}/source"
            file_size = Path(local_path).stat().st_size

        created_ts = int(time.time() * 1000)
        try:
            if iy["discovered_at"]:
                dt = datetime.fromisoformat(str(iy["discovered_at"]).replace(" ", "T"))
                created_ts = int(dt.timestamp() * 1000)
        except Exception:
            pass

        recordings.append({
            "segmentId": f"yt_{vid}",
            "sessionId": sess_id,
            "sequenceNumber": int(iy["sequence_number"] or 1),
            "filename": f"{vid}.mp4",
            "filepath": local_path or "",
            "fileSizeBytes": file_size,
            "sha256Hash": "",
            "status": st,
            "chunkStatus": inbox_st.lower(),
            "sessionStatus": "recording",
            "createdAt": created_ts,
            "finalizedAt": created_ts,
            "durationSeconds": float(iy["duration"] or 0),
            "videoUrl": video_url,
            "clipCount": 0,
            "approvedClipCount": 0,
            "youtubeVideoId": vid,
        })

    return recordings


@app.get("/api/settings")
async def get_settings():
    return {
        "publish_mode": db.get_setting("publish_mode", DEFAULT_PUBLISH_MODE),
        "auto_process": db.get_setting("auto_process", "true")
    }


@app.post("/api/settings")
async def update_settings(payload: SettingsRequest, request: Request):
    verify_dashboard_auth(request)
    if payload.publish_mode:
        db.set_setting("publish_mode", payload.publish_mode)
    if payload.auto_process:
        db.set_setting("auto_process", payload.auto_process)
    return {"status": "success", "message": "Settings updated"}


@app.get("/api/youtube/inbox")
async def get_youtube_inbox_items(request: Request):
    """List YouTube Inbox items and their current processing status."""
    caller_user_id = resolve_caller_user_id(request)
    return {"items": db.list_youtube_inbox(limit=30, user_id=caller_user_id)}

@app.delete("/api/youtube/inbox/{video_id}")
async def delete_youtube_inbox_entry(video_id: str, request: Request):
    """Delete a session or YouTube video from the local inbox."""
    verify_dashboard_auth(request)
    deleted = db.delete_youtube_inbox_item(video_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found in inbox or already deleted")
    return {"status": "success", "deleted": True}


@app.post("/api/youtube/ingest")
async def ingest_youtube_video(payload: YouTubeIngestRequest, request: Request):
    """Trigger on-demand download, transcription, and clip rendering from a YouTube video URL/ID."""
    verify_dashboard_auth(request)
    video_id = extract_youtube_video_id(payload.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL or Video ID")

    # Truthful duplicate handling: check the shared single-flight guard BEFORE launching
    # another background thread, so repeat submissions get a distinct status instead of a
    # misleading processing_queued. The guard inside ingest_video() remains the backstop
    # against the residual check-then-spawn race (worst case: one extra no-op thread).
    if is_ingestion_in_flight(video_id):
        return {
            "status": "already_processing",
            "video_id": video_id,
            "duplicate": True,
            "message": f"YouTube video {video_id} is already being ingested. Track its progress in the inbox."
        }

    user_id = None
    try:
        user_info = get_authenticated_user(request)
        user_id = user_info.get("id")
    except Exception:
        pass

    # Start background processing thread so API returns immediately to client
    catcher = YouTubeInboxCatcher()

    def _run_bg_catcher():
        try:
            catcher.ingest_video(payload.url, user_id=user_id)
        except Exception as e:
            logger.error("Error in background YouTube ingestion for %s: %s", video_id, e)

    t = threading.Thread(target=_run_bg_catcher, daemon=True)
    t.start()

    return {
        "status": "processing_queued",
        "video_id": video_id,
        "message": f"YouTube video {video_id} accepted for Pillar 1 ingestion (download & media validation)."
    }


# --- Integrations & Authentication APIs ---

class GeminiKeyRequest(BaseModel):
    api_key: str


class PairingCodeIssueRequest(BaseModel):
    understands_http_risk: bool = False


class PairingCredentialsRequest(BaseModel):
    pairing_code: Optional[str] = None


@app.get("/api/integrations/status")
async def get_integrations_status():
    """Return live status of YouTube OAuth and Gemini AI integrations."""
    from dispatch.youtube_inbox.oauth import get_youtube_service
    from dispatch.config import GEMINI_API_KEY

    yt_service = get_youtube_service()
    yt_connected = False
    channel_title = ""
    channel_id = ""
    youtube_error = ""
    if yt_service:
        try:
            resp = yt_service.channels().list(mine=True, part="snippet").execute()
            items = resp.get("items", [])
            if items:
                yt_connected = True
                channel_title = items[0]["snippet"].get("title", "")
                channel_id = items[0].get("id", "")
            else:
                youtube_error = "Google authorization returned no YouTube channel."
        except Exception as e:
            logger.warning("YouTube account verification failed (%s).", type(e).__name__)
            youtube_error = "YouTube could not be verified from this PC. Check its internet/DNS connection and retry."

    client_secrets_exist = (ROOT_DIR / "client_secrets.json").exists()
    token_exists = (ROOT_DIR / "youtube_token.json").exists()

    gemini_key = GEMINI_API_KEY or os.getenv("GEMINI_API_KEY", "")
    gemini_configured = bool(gemini_key and len(gemini_key) > 15 and (gemini_key.startswith("AIzaSy") or gemini_key.startswith("AQ.")))

    return {
        "youtube": {
            "connected": yt_connected,
            "channel_title": channel_title,
            "channel_id": channel_id,
            "has_client_secrets": client_secrets_exist,
            "has_token": token_exists,
            "error": youtube_error,
        },
        "gemini": {
            "configured": gemini_configured,
            "masked_key": f"{gemini_key[:8]}...{gemini_key[-4:]}" if len(gemini_key) > 12 else ""
        }
    }


@app.post("/api/integrations/gemini")
async def update_gemini_key(payload: GeminiKeyRequest, request: Request):
    """Save and activate a Gemini API key directly from the dashboard."""
    verify_dashboard_auth(request)
    new_key = payload.api_key.strip()
    if not new_key:
        raise HTTPException(status_code=400, detail="API key cannot be empty")

    if any(c in new_key for c in ("\r", "\n", "\x00")):
        raise HTTPException(status_code=400, detail="Invalid character in API key")

    gemini_key_regex = re.compile(r"^(AIza[\w-]{30,50}|AQ\.[\w.-]{20,120})$")
    if not gemini_key_regex.match(new_key):
        raise HTTPException(status_code=400, detail="Invalid Gemini API key format")

    # P0 fix (audit 2026-10-24): the env path MUST be overridable so the test suite
    # cannot write the real .env. Tests set DISPATCH_ENV_PATH to an isolated file.
    env_path = Path(os.getenv("DISPATCH_ENV_PATH", str(ROOT_DIR / ".env")))
    env_content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    if "GEMINI_API_KEY=" in env_content:
        env_content = re.sub(r"GEMINI_API_KEY=.*", f"GEMINI_API_KEY={new_key}", env_content)
    else:
        env_content += f"\nGEMINI_API_KEY={new_key}\n"
    env_path.write_text(env_content, encoding="utf-8")

    os.environ["GEMINI_API_KEY"] = new_key
    import dispatch.config
    dispatch.config.GEMINI_API_KEY = new_key
    import dispatch.ai_clips.highlight_finder
    dispatch.ai_clips.highlight_finder.GEMINI_API_KEY = new_key

    return {"status": "success", "message": "Gemini API key saved and activated"}


@app.post("/api/integrations/youtube/start-auth")
async def start_youtube_auth(request: Request):
    """Start one desktop OAuth flow using this Dispatch runtime's Python interpreter."""
    verify_dashboard_auth(request)
    global _YOUTUBE_AUTH_PROCESS
    client_secrets = ROOT_DIR / "client_secrets.json"
    if not client_secrets.exists():
        raise HTTPException(
            status_code=400,
            detail="client_secrets.json not found. Please ensure Google OAuth Client ID is configured."
        )

    with _YOUTUBE_AUTH_LOCK:
        if _YOUTUBE_AUTH_PROCESS is not None and _YOUTUBE_AUTH_PROCESS.poll() is None:
            return JSONResponse(status_code=202, content=dict(_YOUTUBE_AUTH_STATE))

        _YOUTUBE_AUTH_STATE.clear()
        _YOUTUBE_AUTH_STATE.update({"status": "starting", "message": "Starting Google sign-in."})
        try:
            process = subprocess.Popen(
                [sys.executable, str(ROOT_DIR / "scripts" / "auth_youtube.py")],
                cwd=str(ROOT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as error:
            logger.error("Unable to launch OAuth helper (%s).", type(error).__name__)
            _YOUTUBE_AUTH_STATE.update({
                "status": "failed",
                "message": "Dispatch could not start Google sign-in with its active Python environment.",
            })
            raise HTTPException(status_code=500, detail=_YOUTUBE_AUTH_STATE["message"])
        _YOUTUBE_AUTH_PROCESS = process

    threading.Thread(
        target=_monitor_youtube_auth,
        args=(process,),
        name="YouTubeOAuthMonitor",
        daemon=True,
    ).start()
    return JSONResponse(status_code=202, content=dict(_YOUTUBE_AUTH_STATE))


@app.get("/api/integrations/youtube/auth-status")
async def get_youtube_auth_status():
    """Return the actual state reported by the OAuth helper process."""
    with _YOUTUBE_AUTH_LOCK:
        return dict(_YOUTUBE_AUTH_STATE)


@app.post("/api/integrations/youtube/disconnect")
async def disconnect_youtube(request: Request):
    """Disconnect YouTube by removing the cached token."""
    verify_dashboard_auth(request)
    token_file = ROOT_DIR / "youtube_token.json"
    if token_file.exists():
        token_file.unlink()
    return {"status": "success", "message": "YouTube account disconnected"}


# --- Admin (Workstream B): allowlist bootstrap, backend-enforced role ---
def _admin_emails() -> set:
    return {e.strip().lower() for e in os.getenv("DISPATCH_ADMIN_EMAILS", "").split(",") if e.strip()}


def require_admin(request: Request) -> Dict[str, Any]:
    """Backend-enforced admin authorization: verified Supabase identity whose email
    is in the DISPATCH_ADMIN_EMAILS allowlist. No client-supplied role is trusted."""
    user = get_authenticated_user(request)
    if user["email"].lower() not in _admin_emails():
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@app.get("/api/admin/whoami")
async def admin_whoami(request: Request):
    try:
        user = require_admin(request)
        return {"is_admin": True, "email": user["email"]}
    except HTTPException as e:
        if e.status_code == 401:
            raise
        return {"is_admin": False, "email": getattr(e, "detail", "")}


@app.get("/api/admin/users")
async def admin_list_users(request: Request):
    require_admin(request)
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not key:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    import httpx
    auth_resp = httpx.get(f"{os.getenv('SUPABASE_URL', '')}/auth/v1/admin/users?per_page=100",
                          headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=15.0)
    if auth_resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Could not list users")
    prof_resp = httpx.get(f"{os.getenv('SUPABASE_URL', '')}/rest/v1/profiles?select=user_id,storage_quota_bytes,queue_priority",
                          headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=15.0)
    prof_map = {}
    total_allocated = 0
    if prof_resp.status_code == 200:
        prof_data = prof_resp.json()
        if isinstance(prof_data, list):
            for p in prof_data:
                if isinstance(p, dict):
                    uid = p.get("user_id")
                    if uid:
                        prof_map[uid] = p
                        total_allocated += int(p.get("storage_quota_bytes") or 0)
    pool_remaining = max(0, 10_000_000_000 - total_allocated)
    pool_remaining_gb = round(pool_remaining / 1_000_000_000, 2)

    users = []
    for u in auth_resp.json().get("users", []):
        uid = u.get("id")
        p = prof_map.get(uid, {})
        q_bytes = int(p.get("storage_quota_bytes") or 0)
        q_gb = round(q_bytes / 1_000_000_000, 2)
        prio = int(p.get("queue_priority") or 100)
        users.append({
            "id": uid,
            "email": u.get("email"),
            "created_at": u.get("created_at"),
            "last_sign_in": u.get("last_sign_in_at"),
            "banned": bool(u.get("banned_until")),
            "quota_gb": q_gb,
            "quota_bytes": q_bytes,
            "queue_priority": prio,
        })
    return {"users": users, "pool_remaining_gb": pool_remaining_gb, "pool_total_gb": 10.0}


@app.post("/api/admin/users/{user_id}/ban")
async def admin_ban_user(user_id: str, request: Request):
    require_admin(request)
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    import httpx
    resp = httpx.put(f"{os.getenv('SUPABASE_URL', '')}/auth/v1/admin/users/{user_id}",
                     headers={"apikey": key, "Authorization": f"Bearer {key}",
                              "Content-Type": "application/json"},
                     content=json.dumps({"ban_duration": "876000h"}))  # ~100 years
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Ban failed")
    logger.info("Admin banned user %s", user_id)
    return {"status": "banned"}


@app.post("/api/admin/users/{user_id}/unban")
async def admin_unban_user(user_id: str, request: Request):
    require_admin(request)
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    import httpx
    resp = httpx.put(f"{os.getenv('SUPABASE_URL', '')}/auth/v1/admin/users/{user_id}",
                     headers={"apikey": key, "Authorization": f"Bearer {key}",
                              "Content-Type": "application/json"},
                     content=json.dumps({"ban_duration": "none"}))
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Unban failed")
    logger.info("Admin unbanned user %s", user_id)
    return {"status": "active"}


@app.post("/api/admin/users/{user_id}/quota")
async def admin_set_user_quota(user_id: str, request: Request):
    require_admin(request)
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not key:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    body = await request.json()
    try:
        gb = float(body.get("quota_gb", 0))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="quota_gb must be a number between 0.1 and 10")
    if gb <= 0 or gb > 10.0:
        raise HTTPException(status_code=400, detail="quota_gb must be between 0.1 and 10 (decimal GB)")
    quota_bytes = round(gb * 1_000_000_000)
    import httpx
    resp = httpx.post(f"{os.getenv('SUPABASE_URL', '')}/rest/v1/rpc/cloud_set_user_quota",
                      headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      json={"p_user_id": user_id, "p_quota_bytes": quota_bytes},
                      timeout=15.0)
    if resp.status_code not in (200, 204):
        err = ""
        try:
            err = resp.json().get("message")
        except Exception:
            err = resp.text
        raise HTTPException(status_code=400, detail=err or "Quota update failed")
    logger.info("Admin set quota for user %s to %s GB", user_id, gb)
    return {"quota_gb": gb, "quota_bytes": quota_bytes}


@app.post("/api/admin/users/{user_id}/priority")
async def admin_set_user_priority(user_id: str, request: Request):
    require_admin(request)
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not key:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    body = await request.json()
    try:
        prio = int(body.get("priority", 100))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="priority must be an integer between 1 and 1000")
    if prio < 1 or prio > 1000:
        raise HTTPException(status_code=400, detail="priority must be an integer between 1 and 1000")
    import httpx
    resp = httpx.post(f"{os.getenv('SUPABASE_URL', '')}/rest/v1/rpc/cloud_set_user_priority",
                      headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      json={"p_user_id": user_id, "p_priority": prio},
                      timeout=15.0)
    if resp.status_code not in (200, 204):
        err = ""
        try:
            err = resp.json().get("message")
        except Exception:
            err = resp.text
        raise HTTPException(status_code=502, detail=err or "Priority update failed")
    logger.info("Admin set priority for user %s to %s", user_id, prio)
    return {"priority": prio}


@app.get("/api/admin/stats")
async def admin_stats(request: Request):
    """High-level processing counters from the local store (no user content)."""
    require_admin(request)
    with db.get_db_connection() as conn:
        cur = conn.cursor()
        clips = cur.execute("SELECT status, COUNT(*) c FROM clips GROUP BY status").fetchall()
        jobs = cur.execute("SELECT status, COUNT(*) c FROM processing_jobs GROUP BY status").fetchall() if True else []
    return {"clips": {r["status"]: r["c"] for r in clips}, "remote_jobs": {}}


@app.get("/api/auth/session")
async def auth_session(request: Request):
    """Who am I? Verifies the app's Supabase session over the local link."""
    user = get_authenticated_user(request)
    return {"user_id": user["user_id"], "email": user["email"]}


@app.api_route("/api/youtube/credentials", methods=["GET", "POST"])
async def youtube_credentials_removed():
    """REMOVED (2026-10-24 architecture pivot): the phone performs its own
    on-device Google/YouTube OAuth. The PC never transfers refresh tokens or
    client secrets to any client under any transport or pairing scheme."""
    raise HTTPException(status_code=410, detail="PC credential transfer removed. Connect YouTube from the phone app (on-device sign-in).")


@app.post("/api/integrations/youtube/pairing-code")
async def pairing_code_removed():
    """REMOVED (2026-10-24): pairing-code credential transfer no longer exists."""
    raise HTTPException(status_code=410, detail="Pairing codes removed. Connect YouTube from the phone app.")


# --- Embedded HTML Dashboard ---

# D14 (remediation): the dashboard no longer loads Tailwind from cdn.tailwindcss.com
# (remote code injection vector + offline breakage). The lead-generated precompiled
# stylesheet is read from DASHBOARD_CSS_PATH and inlined as a <style> block; when the
# file has not been generated yet the style block is omitted gracefully (page renders
# unstyled, no crash).
DASHBOARD_CSS_PATH = Path(__file__).parent / "dashboard.css"
_TAILWIND_CDN_TAG = '<script src="https://cdn.tailwindcss.com"></script>'
_DASHBOARD_CSS_PLACEHOLDER = "__DASHBOARD_CSS_INLINE__"


def _dashboard_css_style_block() -> str:
    """Return the precompiled dashboard CSS wrapped in a <style> block (empty when absent)."""
    if not DASHBOARD_CSS_PATH.exists():
        return ""
    try:
        css = DASHBOARD_CSS_PATH.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Could not read %s; serving dashboard without precompiled CSS: %s", DASHBOARD_CSS_PATH, e)
        return ""
    if not css.strip():
        return ""
    return "<style>\n" + css + "\n</style>"


def _apply_dashboard_css(html: str) -> str:
    """Swap the Tailwind CDN script / CSS placeholder for the inlined stylesheet."""
    style_block = _dashboard_css_style_block()
    if _TAILWIND_CDN_TAG in html:
        return html.replace(_TAILWIND_CDN_TAG, style_block)
    return html.replace(_DASHBOARD_CSS_PLACEHOLDER, style_block)


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Dispatch | Autonomous Content Control</title>
  __DASHBOARD_CSS_INLINE__
  <style>
    body { background-color: #0B0F17; color: #F1F5F9; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .surface { background-color: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.08); }
    .surface-input { background-color: rgba(0, 0, 0, 0.4); border: 1px solid rgba(255, 255, 255, 0.1); }
  </style>
</head>
<body class="min-h-screen antialiased flex flex-col">

  <!-- Header -->
  <header class="border-b border-white/10 bg-[#0B0F17]/90 backdrop-blur sticky top-0 z-50 px-4 lg:px-8 py-3.5 flex items-center justify-between">
    <div class="flex items-center space-x-3">
      <div class="w-8 h-8 rounded bg-indigo-600/20 border border-indigo-500/40 flex items-center justify-center">
        <svg class="w-4 h-4 text-indigo-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z"/>
        </svg>
      </div>
      <div>
        <h1 class="text-sm font-semibold tracking-wider uppercase text-white">Dispatch</h1>
        <p class="text-[11px] text-slate-400 font-mono">Autonomous Engine Active</p>
      </div>
    </div>

    <!-- Quick Stats & Settings -->
    <div class="flex items-center space-x-3 text-xs">
      <div class="hidden sm:flex items-center space-x-2 px-3 py-1.5 rounded surface text-slate-300">
        <span class="w-2 h-2 rounded-full bg-emerald-400"></span>
        <span id="disk-info" class="font-mono text-[11px]">Disk: -- GB free</span>
      </div>

      <div class="flex items-center bg-black/40 border border-white/10 rounded p-0.5">
        <button id="mode-private-btn" onclick="setPublishMode('private')" class="px-2.5 py-1 text-[11px] font-medium rounded transition">
          Private / Draft
        </button>
        <button id="mode-public-btn" onclick="setPublishMode('public')" class="px-2.5 py-1 text-[11px] font-medium rounded transition">
          Auto Public
        </button>
      </div>
    </div>
  </header>

  <!-- Main Content -->
  <main class="flex-1 max-w-7xl w-full mx-auto px-4 lg:px-8 py-6 space-y-6">

    <!-- KPI Metrics -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
      <div class="surface rounded-lg p-3.5 flex flex-col justify-between">
        <span class="text-[11px] uppercase tracking-wider text-slate-400 font-medium">Ready For Review</span>
        <span id="stat-ready" class="text-2xl font-semibold tracking-tight text-white mt-1">0</span>
      </div>
      <div class="surface rounded-lg p-3.5 flex flex-col justify-between">
        <span class="text-[11px] uppercase tracking-wider text-slate-400 font-medium">Approved / Queued</span>
        <span id="stat-approved" class="text-2xl font-semibold tracking-tight text-indigo-400 mt-1">0</span>
      </div>
      <div class="surface rounded-lg p-3.5 flex flex-col justify-between">
        <span class="text-[11px] uppercase tracking-wider text-slate-400 font-medium">Published</span>
        <span id="stat-published" class="text-2xl font-semibold tracking-tight text-emerald-400 mt-1">0</span>
      </div>
      <div class="surface rounded-lg p-3.5 flex flex-col justify-between">
        <span class="text-[11px] uppercase tracking-wider text-slate-400 font-medium">Storage Health</span>
        <span id="stat-storage" class="text-2xl font-semibold tracking-tight text-slate-200 mt-1">--%</span>
      </div>
    </div>

    <!-- Review Section Header -->
    <div class="flex items-center justify-between pt-2">
      <div>
        <h2 class="text-base font-semibold text-white">Clips Awaiting Approval</h2>
        <p class="text-xs text-slate-400 mt-0.5">Review generated short-form clips, edit tags, and dispatch.</p>
      </div>
      <button onclick="fetchClips()" class="text-xs px-3 py-1.5 rounded surface hover:bg-white/[0.06] text-slate-300 flex items-center space-x-1.5 transition">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
        </svg>
        <span>Refresh</span>
      </button>
    </div>

    <!-- Clips Grid -->
    <div id="clips-container" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
      <!-- Dynamic Clip Cards injected here -->
    </div>

    <!-- Empty State -->
    <div id="empty-state" class="hidden surface rounded-lg p-12 text-center">
      <svg class="w-10 h-10 mx-auto text-slate-500 mb-3" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"/>
      </svg>
      <h3 class="text-sm font-medium text-slate-200">No clips pending review</h3>
      <p class="text-xs text-slate-400 mt-1 max-w-sm mx-auto">When your phone completes recording and syncs to your PC, Dispatch automatically cuts and subtitles candidates here.</p>
    </div>

  </main>

  <script>
    let currentPublishMode = "private";

    async function loadStatus() {
      try {
        const res = await fetch("/api/status");
        const data = await res.json();
        document.getElementById("stat-ready").innerText = data.ready_clips;
        document.getElementById("stat-approved").innerText = data.approved_clips;
        document.getElementById("stat-published").innerText = data.published_clips;
        document.getElementById("stat-storage").innerText = data.disk_percent_used + "%";
        document.getElementById("disk-info").innerText = `Disk: ${data.disk_free_gb} GB free`;
        currentPublishMode = data.publish_mode;
        updateModeUI();
      } catch (e) {
        console.error("Status load failed", e);
      }
    }

    function updateModeUI() {
      const privBtn = document.getElementById("mode-private-btn");
      const pubBtn = document.getElementById("mode-public-btn");
      if (currentPublishMode === "public") {
        pubBtn.className = "px-2.5 py-1 text-[11px] font-medium rounded bg-indigo-600 text-white shadow";
        privBtn.className = "px-2.5 py-1 text-[11px] font-medium rounded text-slate-400 hover:text-white";
      } else {
        privBtn.className = "px-2.5 py-1 text-[11px] font-medium rounded bg-slate-700 text-white shadow";
        pubBtn.className = "px-2.5 py-1 text-[11px] font-medium rounded text-slate-400 hover:text-white";
      }
    }

    async function setPublishMode(mode) {
      currentPublishMode = mode;
      updateModeUI();
      await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ publish_mode: mode })
      });
    }

    async function fetchClips() {
      loadStatus();
      try {
        const res = await fetch("/api/clips");
        const data = await res.json();
        const container = document.getElementById("clips-container");
        const emptyState = document.getElementById("empty-state");

        container.innerHTML = "";
        if (!data.ready || data.ready.length === 0) {
          emptyState.classList.remove("hidden");
          return;
        }
        emptyState.classList.add("hidden");

        data.ready.forEach(clip => {
          const card = document.createElement("div");
          card.className = "surface rounded-lg overflow-hidden flex flex-col justify-between";
          card.id = `clip-card-${clip.id}`;

          const videoSrc = clip.video_url || "";
          const thumbSrc = clip.thumbnail_url || "";

          card.innerHTML = `
            <div class="relative bg-black aspect-[9/16] max-h-[380px] w-full overflow-hidden flex items-center justify-center">
              <video src="${videoSrc}" poster="${thumbSrc}" controls preload="metadata" class="w-full h-full object-contain"></video>
              <div class="absolute top-2 right-2 px-2 py-0.5 rounded bg-black/70 backdrop-blur border border-white/10 text-[10px] font-mono text-emerald-400">
                Score: ${clip.virality_score}/100
              </div>
            </div>

            <div class="p-4 space-y-3 flex-1 flex flex-col justify-between">
              <div class="space-y-2">
                <div>
                  <label class="text-[10px] uppercase tracking-wider text-slate-400 font-semibold block mb-1">Title (Roman Hinglish)</label>
                  <input type="text" id="title-${clip.id}" value="${clip.title || ''}" class="w-full text-xs px-2.5 py-1.5 rounded surface-input text-white focus:outline-none focus:border-indigo-500">
                </div>

                <div>
                  <label class="text-[10px] uppercase tracking-wider text-slate-400 font-semibold block mb-1">Hashtags</label>
                  <input type="text" id="tags-${clip.id}" value="${clip.hashtags || ''}" class="w-full text-xs px-2.5 py-1.5 rounded surface-input text-slate-300 focus:outline-none focus:border-indigo-500">
                </div>

                <div class="flex items-center space-x-3 text-[11px] text-slate-400 pt-1">
                  <label class="flex items-center space-x-1.5 cursor-pointer">
                    <input type="checkbox" checked id="yt-${clip.id}" class="rounded border-white/20 text-indigo-600 focus:ring-0">
                    <span>YouTube Shorts</span>
                  </label>
                  <label class="flex items-center space-x-1.5 cursor-pointer">
                    <input type="checkbox" checked id="ig-${clip.id}" class="rounded border-white/20 text-indigo-600 focus:ring-0">
                    <span>Instagram Reels</span>
                  </label>
                </div>
              </div>

              <!-- Action Buttons -->
              <div class="grid grid-cols-2 gap-2 pt-2 border-t border-white/5">
                <button onclick="approveClip('${clip.id}')" class="h-10 text-xs font-semibold rounded bg-emerald-600/90 hover:bg-emerald-500 active:scale-95 text-white transition flex items-center justify-center space-x-1">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/>
                  </svg>
                  <span>Approve</span>
                </button>
                <button onclick="rejectClip('${clip.id}')" class="h-10 text-xs font-semibold rounded bg-rose-600/20 hover:bg-rose-600/30 active:scale-95 text-rose-300 border border-rose-500/30 transition flex items-center justify-center space-x-1">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
                  </svg>
                  <span>Reject</span>
                </button>
              </div>
            </div>
          `;
          container.appendChild(card);
        });
      } catch (e) {
        console.error("Fetch clips error", e);
      }
    }

    async function approveClip(clipId) {
      const title = document.getElementById(`title-${clipId}`).value;
      const tags = document.getElementById(`tags-${clipId}`).value;
      const yt = document.getElementById(`yt-${clipId}`).checked;
      const ig = document.getElementById(`ig-${clipId}`).checked;

      const platforms = [];
      if (yt) platforms.push("youtube");
      if (ig) platforms.push("instagram");

      const card = document.getElementById(`clip-card-${clipId}`);
      if (card) card.style.opacity = "0.4";

      try {
        await fetch(`/api/clips/${clipId}/approve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title: title,
            hashtags: tags,
            publish_mode: currentPublishMode,
            platforms: platforms.join(",")
          })
        });
        if (card) card.remove();
        loadStatus();
      } catch (e) {
        alert("Approve failed: " + e);
        if (card) card.style.opacity = "1";
      }
    }

    async function rejectClip(clipId) {
      const card = document.getElementById(`clip-card-${clipId}`);
      if (card) card.style.opacity = "0.4";

      try {
        await fetch(`/api/clips/${clipId}/reject`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: "User tapped reject" })
        });
        if (card) card.remove();
        loadStatus();
      } catch (e) {
        alert("Reject failed: " + e);
        if (card) card.style.opacity = "1";
      }
    }

    // Auto-refresh every 10 seconds
    setInterval(fetchClips, 10000);
    fetchClips();
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def dashboard_home():
    """Serve the single-page dashboard HTML with the precompiled stylesheet inlined (D14)."""
    template_path = Path(__file__).parent / "templates" / "index.html"
    if template_path.exists():
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=_apply_dashboard_css(f.read()))
    return HTMLResponse(content=_apply_dashboard_css(DASHBOARD_HTML))


# Brand & Favicon Static Assets
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/favicon.ico", include_in_schema=False)
async def get_favicon_ico():
    ico = STATIC_DIR / "favicon.ico"
    if ico.exists():
        return FileResponse(ico, media_type="image/x-icon")
    raise HTTPException(status_code=404, detail="Favicon not found")


@app.get("/favicon.svg", include_in_schema=False)
async def get_favicon_svg():
    svg = STATIC_DIR / "favicon.svg"
    if svg.exists():
        return FileResponse(svg, media_type="image/svg+xml")
    raise HTTPException(status_code=404, detail="Favicon not found")


@app.get("/apple-touch-icon.png", include_in_schema=False)
async def get_apple_touch_icon():
    png = STATIC_DIR / "apple-touch-icon.png"
    if png.exists():
        return FileResponse(png, media_type="image/png")
    raise HTTPException(status_code=404, detail="Apple touch icon not found")


@app.get("/site.webmanifest", include_in_schema=False)
async def get_site_manifest():
    manifest = STATIC_DIR / "site.webmanifest"
    if manifest.exists():
        return FileResponse(manifest, media_type="application/manifest+json")
    raise HTTPException(status_code=404, detail="Manifest not found")


# React Studio Static Assets & Route (/studio)
STUDIO_DIR = ROOT_DIR / "android" / "app" / "src" / "main" / "assets" / "web"
if STUDIO_DIR.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/studio/assets", StaticFiles(directory=str(STUDIO_DIR / "assets")), name="studio-assets")

    @app.get("/studio")
    async def redirect_studio_slash():
        """Redirect /studio to /studio/ so relative asset paths like ./assets/ resolve to /studio/assets/."""
        return RedirectResponse(url="/studio/", status_code=307)

    @app.get("/studio/", response_class=HTMLResponse)
    @app.get("/studio/{full_path:path}", response_class=HTMLResponse)
    async def serve_studio(full_path: str = ""):
        # Check if requesting a direct static file in STUDIO_DIR (e.g. favicon, manifest)
        target = (STUDIO_DIR / full_path).resolve()
        if full_path and target.exists() and target.is_file() and str(target).startswith(str(STUDIO_DIR.resolve())):
            return FileResponse(target)
        index_file = STUDIO_DIR / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        raise HTTPException(status_code=404, detail="Studio interface not found")


# Font Library & Custom Font Import APIs
@app.get("/api/fonts")
async def list_fonts():
    """List all available fonts in the Dispatch creator library (bundled and custom)."""
    manifest_path = FONTS_DIR / "fonts_manifest.json"
    fonts = []
    if manifest_path.exists():
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                fonts = json.load(f)
        except Exception as e:
            logger.error("Failed to read fonts manifest: %s", e)
    
    # Also discover any custom fonts present in FONTS_DIR not yet recorded
    existing_files = {f.get("filename") for f in fonts if isinstance(f, dict)}
    for font_file in FONTS_DIR.glob("*.*"):
        if font_file.suffix.lower() in (".ttf", ".otf") and font_file.name not in existing_files:
            try:
                from fontTools.ttLib import TTFont
                tt = TTFont(str(font_file))
                detected_family = font_file.stem
                for record in tt["name"].names:
                    if record.nameID in (1, 4, 16):
                        try:
                            s = record.toUnicode()
                            if s and len(s) > 1:
                                detected_family = s
                                break
                        except Exception:
                            pass
                weight = "400"
                if "OS/2" in tt:
                    weight = str(getattr(tt["OS/2"], "usWeightClass", 400))
                fonts.append({
                    "family": detected_family,
                    "category": "Custom",
                    "weights": [weight],
                    "filename": font_file.name,
                    "license": "Custom Imported",
                    "is_valid": True,
                    "is_custom": True,
                    "url": f"/api/fonts/{font_file.name}"
                })
            except Exception as e:
                logger.warning("Could not parse font %s: %s", font_file.name, e)
    return {"fonts": fonts}


_FONT_IMPORT_MAX_BYTES = 20 * 1024 * 1024  # D4: fonts are vector outline data, not video


@app.post("/api/fonts/import")
async def import_custom_font(request: Request, file: UploadFile = File(...)):
    """Validate, register, and save a custom TTF or OTF font file."""
    verify_dashboard_auth(request)

    # D4 hardening: reject oversized font uploads from Content-Length before the
    # handler reads them into memory (413 with an explicit, actionable detail).
    content_length_header = request.headers.get("content-length")
    if content_length_header:
        try:
            declared_length = int(content_length_header)
        except (TypeError, ValueError):
            declared_length = None
        if declared_length is not None and declared_length > _FONT_IMPORT_MAX_BYTES:
            logger.warning("Rejected font import from %s: declared %d bytes exceeds the %d-byte cap",
                           request.client.host if request.client else "unknown", declared_length, _FONT_IMPORT_MAX_BYTES)
            raise HTTPException(
                status_code=413,
                detail=f"Font file too large: {declared_length} bytes exceeds the {_FONT_IMPORT_MAX_BYTES}-byte (20 MB) import limit"
            )

    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing font file")
    
    ext = Path(file.filename).suffix.lower()
    if ext not in (".ttf", ".otf"):
        raise HTTPException(status_code=400, detail="Only .ttf and .otf font files are supported")
    
    content = await file.read()
    if len(content) < 1024:
        raise HTTPException(status_code=400, detail="Invalid or corrupt font file (file too small)")
    if len(content) > _FONT_IMPORT_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Font file too large: {len(content)} bytes exceeds the {_FONT_IMPORT_MAX_BYTES}-byte (20 MB) import limit"
        )
    
    # Validate with fontTools
    try:
        from io import BytesIO
        from fontTools.ttLib import TTFont
        tt = TTFont(BytesIO(content))
        detected_family = None
        for record in tt["name"].names:
            if record.nameID in (1, 4, 16):
                try:
                    s = record.toUnicode()
                    if s and len(s) > 1 and not detected_family:
                        detected_family = s
                except Exception:
                    pass
        if not detected_family:
            detected_family = Path(file.filename).stem

        weight = "400"
        if "OS/2" in tt:
            weight = str(getattr(tt["OS/2"], "usWeightClass", 400))
    except Exception as e:
        logger.error("Failed to parse font %s: %s", file.filename, e)
        raise HTTPException(status_code=400, detail=f"Corrupt or invalid font format: {str(e)}")

    clean_name = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", file.filename)
    dest_path = FONTS_DIR / clean_name
    with open(dest_path, "wb") as f:
        f.write(content)

    font_entry = {
        "family": detected_family,
        "category": "Custom",
        "weights": [weight],
        "filename": clean_name,
        "license": "Custom Imported",
        "is_valid": True,
        "is_custom": True,
        "url": f"/api/fonts/{clean_name}"
    }

    manifest_path = FONTS_DIR / "fonts_manifest.json"
    manifest = []
    if manifest_path.exists():
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            manifest = []
    
    manifest = [m for m in manifest if m.get("filename") != clean_name and m.get("family") != detected_family]
    manifest.append(font_entry)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    logger.info("Successfully imported custom font: %s (%s)", detected_family, clean_name)
    return {"success": True, "font": font_entry}


@app.get("/api/fonts/{filename}")
async def serve_font(filename: str):
    """Serve font binary asset with proper MIME type."""
    clean_filename = Path(filename).name
    target_file = (FONTS_DIR / clean_filename).resolve()
    if not target_file.exists() or not str(target_file).startswith(str(FONTS_DIR.resolve())):
        raise HTTPException(status_code=404, detail="Font file not found")
    
    ext = target_file.suffix.lower()
    media_type = "font/ttf" if ext == ".ttf" else ("font/otf" if ext == ".otf" else "application/octet-stream")
    return FileResponse(target_file, media_type=media_type)





# --- Default Editing Template Endpoints ---

@app.get("/api/template/default")
@app.get("/api/captions/templates")
@app.get("/api/template")
async def get_default_template_endpoint(request: Request):
    """Retrieve the current default editing template for the caller or system."""
    caller_user_id = resolve_caller_user_id(request, allow_lan_peer=True)
    template = db.get_default_template(user_id=caller_user_id)
    return template

get_caption_template = get_default_template_endpoint


@app.post("/api/template/default")
@app.post("/api/captions/templates")
@app.post("/api/template")
async def save_default_template_endpoint(request: Request, payload: Optional[Any] = Body(None)):
    """Save or update the caller's default editing template."""
    verify_dashboard_auth(request)
    caller_user_id = resolve_caller_user_id(request, allow_lan_peer=True)

    raw_data: Any = payload
    if raw_data is None:
        try:
            raw_data = await request.json()
        except Exception:
            try:
                body_bytes = await request.body()
                raw_data = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
            except Exception:
                raw_data = {}

    if isinstance(raw_data, str):
        try:
            raw_data = json.loads(raw_data)
        except Exception:
            raw_data = {}

    if not isinstance(raw_data, dict):
        raw_data = {}

    if "template" in raw_data and isinstance(raw_data["template"], dict):
        raw_data = raw_data["template"]

    default_tpl = db.DEFAULT_EDITING_TEMPLATE
    sanitized: Dict[str, Any] = {
        "name": str(raw_data.get("name") or "Creator Standard")[:80],
        "framing": dict(raw_data.get("framing") or default_tpl.get("framing", {})),
        "captions": dict(raw_data.get("captions") or default_tpl.get("captions", {})),
        "hook": dict(raw_data.get("hook") or default_tpl.get("hook", {})),
        "publish_mode": str(raw_data.get("publish_mode") or "private"),
        "platform_targets": list(raw_data.get("platform_targets") or ["youtube", "instagram"]),
        "updated_at": raw_data.get("updated_at") or datetime.now(timezone.utc).isoformat(),
    }

    saved = db.save_default_template(sanitized, user_id=caller_user_id)
    return {
        "status": "success",
        "template": saved,
        "revision": saved.get("revision", 1),
        "message": "Template saved successfully",
    }

save_caption_template = save_default_template_endpoint
update_default_template = save_default_template_endpoint


@app.get("/api/template/sample-video")
async def get_sample_template_video(orientation: str = "portrait"):
    """Serve reference sample video for template editor preview."""
    root_dir = Path(__file__).resolve().parent.parent.parent
    if orientation == "landscape":
        sample_path = root_dir / "frontend" / "public" / "sample_landscape.mp4"
    else:
        sample_path = root_dir / "frontend" / "public" / "sample_portrait.mp4"
    
    if not sample_path.exists():
        sample_path = root_dir / "tests" / "fixtures" / "tiny_valid.mp4"
    if sample_path.exists():
        return FileResponse(str(sample_path), media_type="video/mp4")
    raise HTTPException(status_code=404, detail="Sample preview video not found")
