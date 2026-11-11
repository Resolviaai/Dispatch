"""Cloud Worker broker client and remote job queue client for the owner PC.

Security boundary (Wave 0):
- Cloudflare Worker is the sole owner of SUPABASE_SERVICE_ROLE_KEY,
  DISPATCH_TOKEN_VAULT_KEY, YOUTUBE_CLIENT_SECRET, raw OAuth refresh_tokens,
  and raw user Gemini BYOK API keys.
- The PC authenticates exclusively with DISPATCH_WORKER_TOKEN + X-Dispatch-User-Id
  to scoped `/sync/pc/*` Worker broker routes and receives ONLY short-lived
  provider access_tokens or proxied API outputs.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dispatch.youtube_connections")
_TOKEN_FILE_LOCK = threading.Lock()


def atomic_write_token_file(token_file: Path, content: str) -> None:
    """Thread-safe atomic token JSON write (shared lock across publisher & inbox OAuth)."""
    token_file.parent.mkdir(parents=True, exist_ok=True)
    with _TOKEN_FILE_LOCK:
        fd, tmp_name = tempfile.mkstemp(
            dir=str(token_file.parent),
            prefix=f".{token_file.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_f:
                tmp_f.write(content)
                tmp_f.flush()
                os.fsync(tmp_f.fileno())
            for attempt in range(5):
                try:
                    os.replace(tmp_name, str(token_file))
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.01 * (attempt + 1))
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise


class VaultUnavailable(Exception):
    """Cloud Worker broker unreachable or misconfigured; caller must fail closed."""


try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _is_standalone_mode() -> bool:
    return (
        _env("DISPATCH_STANDALONE").lower() in ("1", "true", "yes")
        or _env("DISPATCH_OFFLINE_MODE").lower() in ("1", "true", "yes")
    )


def _worker_config() -> tuple[str, str]:
    worker_url = (_env("DISPATCH_WORKER_URL") or _env("DISPATCH_CLOUD_WORKER_URL")).rstrip("/")
    worker_token = _env("DISPATCH_WORKER_TOKEN")
    return worker_url, worker_token


def _resolve_default_user_id(user_id: Optional[str] = None) -> Optional[str]:
    uid = (user_id or _env("DISPATCH_USER_ID")).strip()
    if uid:
        return uid
    try:
        from dispatch.cloud.sync import authorized_pc_owner_ids
        owners = authorized_pc_owner_ids()
        if len(owners) == 1:
            return owners[0]
    except Exception:
        pass
    return None


def _worker_headers(user_id: Optional[str] = None) -> Dict[str, str]:
    _, worker_token = _worker_config()
    headers = {
        "Authorization": f"Bearer {worker_token}",
        "Content-Type": "application/json",
    }
    resolved_uid = _resolve_default_user_id(user_id)
    if resolved_uid:
        headers["X-Dispatch-User-Id"] = resolved_uid
    return headers


def _worker_post(
    path: str,
    payload: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    timeout_seconds: float = 20.0,
) -> tuple[int, Dict[str, Any]]:
    import httpx

    worker_url, worker_token = _worker_config()
    if not worker_url or not worker_token:
        raise VaultUnavailable(
            "Cloud worker broker not configured (missing DISPATCH_WORKER_URL or DISPATCH_WORKER_TOKEN)"
        )
    resp = httpx.post(
        f"{worker_url}{path}",
        json=payload if payload is not None else {},
        headers=_worker_headers(user_id),
        timeout=timeout_seconds,
    )
    try:
        data = resp.json() if resp.content else {}
    except Exception:
        data = {"error": resp.text[:200]}
    if not isinstance(data, dict):
        data = {"result": data}
    return resp.status_code, data


def _worker_get(
    path: str,
    user_id: Optional[str] = None,
    params: Optional[Dict[str, str]] = None,
    timeout_seconds: float = 20.0,
) -> tuple[int, Dict[str, Any]]:
    import httpx

    worker_url, worker_token = _worker_config()
    if not worker_url or not worker_token:
        raise VaultUnavailable(
            "Cloud worker broker not configured (missing DISPATCH_WORKER_URL or DISPATCH_WORKER_TOKEN)"
        )
    resp = httpx.get(
        f"{worker_url}{path}",
        params=params,
        headers=_worker_headers(user_id),
        timeout=timeout_seconds,
    )
    try:
        data = resp.json() if resp.content else {}
    except Exception:
        data = {"error": resp.text[:200]}
    if not isinstance(data, dict):
        data = {"result": data}
    return resp.status_code, data


def _worker_delete(
    path: str,
    user_id: Optional[str] = None,
    params: Optional[Dict[str, str]] = None,
    timeout_seconds: float = 20.0,
) -> tuple[int, Dict[str, Any]]:
    import httpx

    worker_url, worker_token = _worker_config()
    if not worker_url or not worker_token:
        raise VaultUnavailable(
            "Cloud worker broker not configured (missing DISPATCH_WORKER_URL or DISPATCH_WORKER_TOKEN)"
        )
    resp = httpx.delete(
        f"{worker_url}{path}",
        params=params,
        headers=_worker_headers(user_id),
        timeout=timeout_seconds,
    )
    try:
        data = resp.json() if resp.content else {}
    except Exception:
        data = {"error": resp.text[:200]}
    if not isinstance(data, dict):
        data = {"result": data}
    return resp.status_code, data


def get_candidate_vault_keys() -> List[bytes]:
    """Return plausible 256-bit vault keys matching Cloudflare Worker aesKey derivation.

    Fails closed: no key is returned when DISPATCH_TOKEN_VAULT_KEY is missing or
    does not encode exactly 32 bytes. The old publicly-known fallback key was
    removed (audit 2026-10-28, F04) because it made stored credentials
    decryptable from source.
    """
    raw_key = _env("DISPATCH_TOKEN_VAULT_KEY")
    keys: List[bytes] = []
    if not raw_key:
        return keys

    # 1. Base64/base64url (AES-256 only), with padding (Worker accepts both)
    try:
        normalized = raw_key.replace("-", "+").replace("_", "/")
        b = base64.b64decode(normalized + "=" * (-len(normalized) % 4))
        if len(b) == 32:
            keys.append(b)
    except Exception:
        pass

    # 2. Hex (AES-256 only: 64 hex characters)
    if len(raw_key) == 64 and all(c in "0123456789abcdefABCDEF" for c in raw_key):
        try:
            keys.append(bytes.fromhex(raw_key))
        except Exception:
            pass

    # 3. SHA-256 derivation of the configured passphrase (never a constant fallback)
    import hashlib
    keys.append(hashlib.sha256(raw_key.encode("utf-8")).digest())

    # De-duplicate while preserving order.
    seen = set()
    unique: List[bytes] = []
    for key in keys:
        if key not in seen:
            seen.add(key)
            unique.append(key)
    return unique


def vault_key() -> bytes:
    raw_key = _env("DISPATCH_TOKEN_VAULT_KEY")
    if not raw_key:
        raise VaultUnavailable("DISPATCH_TOKEN_VAULT_KEY not configured")
    candidates = get_candidate_vault_keys()
    if candidates:
        return candidates[0]
    raise VaultUnavailable("DISPATCH_TOKEN_VAULT_KEY not configured")


def decrypt_refresh_token(encrypted_b64url: str) -> str:
    """Decrypt a Worker-encrypted refresh token (base64url(iv||ciphertext), AES-GCM)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    data = base64.urlsafe_b64decode(encrypted_b64url + "=" * (-len(encrypted_b64url) % 4))
    if len(data) < 28:
        raise ValueError("Encrypted token payload too short")
    iv, ct = data[:12], data[12:]

    candidates = get_candidate_vault_keys()
    if not candidates:
        raise VaultUnavailable("DISPATCH_TOKEN_VAULT_KEY not configured")
    last_err = None
    for k in candidates:
        try:
            return AESGCM(k).decrypt(iv, ct, None).decode("utf-8")
        except Exception as e:
            last_err = e

    if last_err:
        raise last_err
    raise ValueError("No vault keys available")


def get_user_refresh_token(user_id: str) -> str:
    """Fail-closed stub: raw OAuth refresh tokens never leave the Cloud Worker."""
    if user_id:
        logger.debug("Refusing raw refresh_token export on PC for user %s; use Worker access-token broker.", user_id)
    return ""


def disconnect_user_token(user_id: str) -> None:
    """Remove a user's stored YouTube credential via the scoped Worker broker."""
    if not user_id:
        return
    status, data = _worker_post("/sync/pc/disconnect", {}, user_id=user_id, timeout_seconds=20.0)
    if status >= 400:
        raise VaultUnavailable(f"Worker disconnect failed ({status}): {data.get('error', '')}")


def get_user_gemini_key(user_id: Optional[str] = None) -> str:
    """Fail-closed compatibility symbol: user Gemini BYOK keys remain sealed in Cloud Worker."""
    if user_id:
        logger.debug("User Gemini BYOK keys are restricted to Cloud Worker proxy (user=%s).", user_id)
    return ""


def has_user_gemini_proxy(user_id: Optional[str] = None, timeout_seconds: float = 10.0) -> bool:
    """Return True if the Cloud Worker has a vaulted Gemini BYOK key for user_id."""
    uid = _resolve_default_user_id(user_id)
    if not uid:
        return False
    try:
        status, data = _worker_get("/sync/pc/gemini/status", user_id=uid, timeout_seconds=timeout_seconds)
        return status == 200 and bool(data.get("configured"))
    except Exception as e:
        logger.debug("Could not query Gemini proxy status for user %s: %s", uid, e)
        return False


def call_user_gemini_proxy(
    user_id: str,
    contents: Any,
    model: str = "gemini-2.5-flash",
    generation_config: Optional[Dict[str, Any]] = None,
    system_instruction: Optional[Any] = None,
    safety_settings: Optional[Any] = None,
    tools: Optional[Any] = None,
    timeout_seconds: float = 45.0,
) -> Dict[str, Any]:
    """Execute Gemini generateContent on Cloud Worker without exposing the user API key to PC."""
    if not user_id:
        raise ValueError("user_id is required for call_user_gemini_proxy")
    payload: Dict[str, Any] = {"model": model, "contents": contents}
    if generation_config is not None:
        payload["generationConfig"] = generation_config
    if system_instruction is not None:
        payload["systemInstruction"] = system_instruction
    if safety_settings is not None:
        payload["safetySettings"] = safety_settings
    if tools is not None:
        payload["tools"] = tools

    status, data = _worker_post(
        "/sync/pc/gemini/generate",
        payload,
        user_id=user_id,
        timeout_seconds=timeout_seconds,
    )
    if status == 404 or data.get("code") == "gemini_key_missing":
        raise PermissionError("User Gemini API key is not configured in Dispatch Cloud Vault")
    if status >= 400:
        raise VaultUnavailable(f"Worker Gemini generate proxy failed ({status}): {data.get('error', '')}")
    return data


def upload_user_gemini_media(
    user_id: str,
    file_path: str,
    mime_type: str = "video/mp4",
    display_name: Optional[str] = None,
    timeout_seconds: float = 120.0,
) -> Dict[str, Any]:
    """Start a Gemini Files resumable upload via Worker and stream bytes directly to the session URL."""
    import httpx

    p = Path(file_path)
    num_bytes = p.stat().st_size
    status, init_data = _worker_post(
        "/sync/pc/gemini/upload",
        {
            "mime_type": mime_type,
            "num_bytes": num_bytes,
            "display_name": display_name or p.name,
        },
        user_id=user_id,
        timeout_seconds=20.0,
    )
    if status == 404 or init_data.get("code") == "gemini_key_missing":
        raise PermissionError("User Gemini API key is not configured in Dispatch Cloud Vault")
    if status >= 400 or not init_data.get("upload_url"):
        raise VaultUnavailable(f"Worker Gemini upload init failed ({status}): {init_data.get('error', '')}")

    upload_url = str(init_data["upload_url"])
    with open(p, "rb") as f:
        up_resp = httpx.post(
            upload_url,
            content=f.read(),
            headers={
                "Content-Length": str(num_bytes),
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
            },
            timeout=timeout_seconds,
        )
    if up_resp.status_code not in (200, 201):
        raise VaultUnavailable(f"Gemini resumable media upload failed ({up_resp.status_code})")
    return up_resp.json() if up_resp.content else {}


def _infer_provider_candidates(account_id: str, provider: Optional[str]) -> List[str]:
    if provider:
        norm = provider.strip().lower()
        if norm in ("youtube", "youtube_shorts", "google"):
            return ["youtube"]
        if norm in ("x", "twitter"):
            return ["twitter"]
        return [norm]
    ref = str(account_id).strip()
    if ref.startswith("urn:li:"):
        return ["linkedin"]
    if ref.startswith("UC"):
        return ["youtube"]
    return ["youtube", "instagram", "facebook", "linkedin", "twitter"]


def get_account_credentials(
    account_id: str,
    user_id: Optional[str] = None,
    timeout_seconds: float = 20.0,
    provider: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch a short-lived provider access token via the Cloud Worker broker.

    Never returns raw refresh_tokens, OAuth client secrets, or vault keys.
    Returns `{"needs_reauth": True, ...}` if the upstream provider revoked consent.
    """
    if not account_id and not user_id:
        return {}
    uid = _resolve_default_user_id(user_id)
    if not uid:
        return {}

    candidates = _infer_provider_candidates(account_id or "", provider)
    try:
        for cand_provider in candidates:
            status, data = _worker_post(
                "/sync/pc/oauth/access-token",
                {
                    "provider": cand_provider,
                    "account_id": str(account_id or ""),
                    "require_source_vault": False,
                },
                user_id=uid,
                timeout_seconds=timeout_seconds,
            )
            if status == 200 and data.get("access_token"):
                # Defense-in-depth: never propagate long-lived refresh/client secrets.
                out: Dict[str, Any] = {
                    "access_token": data["access_token"],
                    "token": data["access_token"],
                    "expires_at": data.get("expires_at"),
                    "ttl_seconds": data.get("ttl_seconds"),
                    "provider": data.get("provider") or cand_provider,
                    "account_id": data.get("account_id") or account_id,
                    "account_name": data.get("account_name"),
                    "account_email": data.get("account_email"),
                    "needs_reauth": False,
                }
                for key in ("page_id", "ig_user_id", "author_urn", "api_base", "auth_mode", "username", "open_id"):
                    if data.get(key) is not None:
                        out[key] = data[key]
                return out
            if data.get("code") == "needs_reauth" or data.get("needs_reauth") is True:
                return {
                    "needs_reauth": True,
                    "code": "needs_reauth",
                    "provider": cand_provider,
                    "account_id": data.get("account_id") or account_id,
                    "error": data.get("error") or f"{cand_provider} connection requires re-authentication",
                }
            if status != 404:
                break
        return {}
    except Exception as e:
        logger.debug("Could not broker short-lived access token for account %s: %s", account_id, e)
        return {}


def get_user_youtube_access_token(
    user_id: str,
    account_id: str = "",
    timeout_seconds: float = 20.0,
) -> Dict[str, Any]:
    """Fetch user's short-lived YouTube access token (or needs_reauth dict) via Worker broker."""
    if not user_id:
        return {}
    return get_account_credentials(
        account_id=account_id,
        user_id=user_id,
        timeout_seconds=timeout_seconds,
        provider="youtube",
    )


def reconcile_youtube_via_worker(
    account_id: str,
    idempotency_key: str,
    user_id: Optional[str] = None,
    timeout_seconds: float = 20.0,
) -> Dict[str, Any]:
    """Run YouTube Data API search reconciliation server-side via Cloud Worker."""
    uid = _resolve_default_user_id(user_id)
    if not uid:
        return {"reconciled": False, "reason": "No target user_id available for YouTube reconciliation"}
    try:
        status, data = _worker_post(
            "/sync/pc/youtube/reconcile",
            {"account_id": account_id or "", "idempotency_key": idempotency_key, "max_results": 5},
            user_id=uid,
            timeout_seconds=timeout_seconds,
        )
        if data.get("needs_reauth") or data.get("code") == "needs_reauth":
            raise PermissionError(str(data.get("error") or "YouTube authorization revoked; reconnect required"))
        if status == 404:
            return {"reconciled": False, "reason": "No connected YouTube account"}
        if status >= 400:
            return {"reconciled": False, "reason": f"Worker reconcile failed ({status}): {data.get('error', '')}"}
        items = data.get("items") or []
        if items:
            first = items[0]
            vid = first.get("id", {}).get("videoId") if isinstance(first.get("id"), dict) else first.get("videoId")
            if vid:
                return {
                    "reconciled": True,
                    "published": True,
                    "remote_id": vid,
                    "remote_url": f"https://www.youtube.com/watch?v={vid}",
                }
        return {"reconciled": True, "published": False}
    except PermissionError:
        raise
    except Exception as e:
        return {"reconciled": False, "reason": str(e)}


def get_youtube_status_via_worker(
    video_ids: List[str],
    user_id: Optional[str] = None,
    account_id: Optional[str] = None,
    timeout_seconds: float = 20.0,
) -> Dict[str, Dict[str, Any]]:
    """Query YouTube video status/processingDetails via Cloud Worker without PC credentials."""
    if not video_ids:
        return {}
    uid = _resolve_default_user_id(user_id)
    if not uid:
        return {}
    try:
        status, data = _worker_post(
            "/sync/pc/youtube/status",
            {"account_id": account_id or "", "video_ids": list(video_ids)[:50]},
            user_id=uid,
            timeout_seconds=timeout_seconds,
        )
        if status != 200:
            return {}
        videos = data.get("videos")
        return videos if isinstance(videos, dict) else {}
    except Exception as e:
        logger.debug("Worker youtube status check failed: %s", e)
        return {}


class RemoteJob:
    def __init__(
        self,
        job_id: str,
        user_id: str,
        youtube_url: str,
        source: str = "youtube",
        upload_object_id: str = "",
        source_title: str = "",
    ):
        self.job_id = job_id
        self.user_id = user_id
        self.youtube_url = youtube_url
        self.source = source or "youtube"
        self.upload_object_id = upload_object_id or ""
        self.source_title = source_title or ""


def claim_remote_job(worker_id: str) -> Optional[RemoteJob]:
    """Atomically claim the oldest queued remote job via Cloud Worker broker."""
    status, data = _worker_post("/sync/pc/remote-jobs/claim", {"worker_id": worker_id})
    if status >= 400:
        raise VaultUnavailable(f"Worker claim_remote_job failed ({status}): {data.get('error', '')}")
    row = data.get("job")
    if not row or not isinstance(row, dict) or not row.get("job_id"):
        return None
    return RemoteJob(
        row["job_id"],
        row["user_id"],
        row.get("youtube_url") or "",
        source=row.get("source") or "youtube",
        upload_object_id=row.get("upload_object_id") or "",
        source_title=row.get("source_title") or "",
    )


def update_remote_job_stage(job_id: str, stage: str, video_id: str = "", duration: Optional[float] = None) -> None:
    """Report granular processing stage via Cloud Worker broker."""
    if not job_id:
        return
    try:
        worker_url, worker_token = _worker_config()
        if not worker_url or not worker_token:
            return
        _worker_post(
            "/sync/pc/remote-jobs/stage",
            {"job_id": job_id, "stage": stage, "video_id": video_id, "duration": duration},
            timeout_seconds=8.0,
        )
    except Exception as e:
        logger.debug("Failed to update remote job stage for %s: %s", job_id, e)


def update_job_title_by_video_id(video_id: str, title: str) -> None:
    """Report enriched AI title for a video ID back to matching cloud jobs via Worker."""
    if not video_id or not title:
        return
    try:
        worker_url, worker_token = _worker_config()
        if not worker_url or not worker_token:
            return
        _worker_post(
            "/sync/pc/remote-jobs/title-by-video",
            {"video_id": video_id, "title": title},
            timeout_seconds=8.0,
        )
    except Exception as e:
        logger.debug("Failed to update title by video_id %s: %s", video_id, e)


def get_account_id_for_video(video_id: str, user_id: Optional[str] = None) -> Optional[str]:
    """Look up bound account_id for a given YouTube video via Cloud Worker broker."""
    if not video_id:
        return None
    try:
        worker_url, worker_token = _worker_config()
        if not worker_url or not worker_token:
            return None
        status, data = _worker_post(
            "/sync/pc/remote-jobs/account-for-video",
            {"video_id": video_id, "user_id": user_id},
            user_id=user_id,
            timeout_seconds=8.0,
        )
        if status == 200 and data.get("account_id"):
            return str(data["account_id"])
    except Exception as e:
        logger.debug("Failed to query account_id for video %s: %s", video_id, e)
    return None


_UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def is_valid_youtube_channel_id(val: Any) -> bool:
    """Validate whether an identifier is an authentic YouTube channel ID vs email or user UUID."""
    if not val or not isinstance(val, str):
        return False
    val = val.strip()
    if "@" in val or " " in val:
        return False
    if _UUID_PATTERN.match(val):
        return False
    if val.startswith("UC"):
        return True
    if val.startswith("ch-") or val.startswith("channel-"):
        return True
    return len(val) >= 8 and not val.endswith(".com")


def get_cloud_connected_youtube_channel(
    user_id: Optional[str] = None,
    channel_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Look up bound YouTube account/channel via Cloud Worker broker (`/sync/pc/youtube/channel`)."""
    standalone_flag = _is_standalone_mode()
    worker_url, worker_token = _worker_config()
    if not worker_url or not worker_token:
        if standalone_flag:
            return {"status": "standalone_mode", "matched": True}
        return {
            "status": "cloud_unavailable",
            "error": (
                "Cloud worker broker not configured (missing DISPATCH_WORKER_URL or DISPATCH_WORKER_TOKEN). "
                "Set credentials or enable DISPATCH_STANDALONE=1."
            ),
        }

    target_user_id = (user_id or _env("DISPATCH_USER_ID")).strip() or None
    if not target_user_id:
        try:
            from dispatch.cloud.sync import authorized_pc_owner_ids
            owners = authorized_pc_owner_ids()
            if len(owners) == 1:
                target_user_id = owners[0]
            elif len(owners) > 1:
                logger.warning(
                    "Multiple PC owners configured (%d owners found in DISPATCH_PC_OWNER_USER_IDS). "
                    "Set DISPATCH_USER_ID to explicitly select the Route A creator.",
                    len(owners),
                )
                return {
                    "status": "ambiguous_owner",
                    "account_count": len(owners),
                    "owners": owners,
                    "error": (
                        f"Multiple PC owners configured ({len(owners)} users in DISPATCH_PC_OWNER_USER_IDS). "
                        "Set DISPATCH_USER_ID to explicitly select the Route A creator."
                    ),
                }
        except Exception as e:
            logger.debug("Failed to inspect authorized_pc_owner_ids: %s", e)

    if not target_user_id:
        if standalone_flag:
            return {"status": "standalone_mode", "matched": True}
        return {
            "status": "cloud_unavailable",
            "error": "No target user_id resolved (set DISPATCH_USER_ID or single UUID in DISPATCH_PC_OWNER_USER_IDS).",
        }

    explicit_channel = (
        channel_id
        or _env("DISPATCH_YOUTUBE_CHANNEL_ID")
        or _env("DISPATCH_YOUTUBE_ACCOUNT_ID")
        or None
    )

    try:
        import httpx
        status, data = _worker_post(
            "/sync/pc/youtube/channel",
            {"channel_id": explicit_channel},
            user_id=target_user_id,
            timeout_seconds=8.0,
        )
    except httpx.TimeoutException as e:
        logger.error("Cloud Worker YouTube channel lookup timed out: %s", e)
        return {"status": "cloud_unavailable", "error": f"Worker lookup timed out: {e}"}
    except httpx.RequestError as e:
        logger.error("Cloud Worker YouTube channel lookup failed: %s", e)
        return {"status": "cloud_unavailable", "error": f"Worker request failed: {e}"}
    except Exception as e:
        logger.error("Cloud Worker YouTube channel lookup error: %s", e)
        return {"status": "cloud_unavailable", "error": f"Worker lookup error: {e}"}

    if status != 200:
        return {"status": "cloud_unavailable", "error": f"Cloud Worker returned HTTP {status}: {data.get('error', '')}"}

    w_status = data.get("status")
    if w_status == "not_found":
        if standalone_flag:
            return {"status": "standalone_mode", "matched": True}
        reason = data.get("reason")
        if reason == "channel_not_connected_to_user" and explicit_channel:
            return {
                "status": "ambiguous_owner",
                "account_count": 0,
                "user_id": target_user_id,
                "error": f"Explicit channel '{explicit_channel}' is not connected for user {target_user_id}.",
            }
        return {
            "status": "no_cloud_account",
            "user_id": target_user_id,
            "error": (
                "No YouTube account connected in Dispatch Cloud Vault. "
                "Connect your YouTube channel in Dispatch Settings or enable DISPATCH_STANDALONE=1 for local offline mode."
            ),
        }

    if w_status == "ambiguous_owner":
        channels = data.get("channels") or []
        return {
            "status": "ambiguous_owner",
            "account_count": len(channels),
            "accounts": [c.get("channel_id") for c in channels if isinstance(c, dict)],
            "user_id": target_user_id,
            "error": (
                f"User {target_user_id} has {len(channels)} connected YouTube accounts. "
                "Set DISPATCH_YOUTUBE_CHANNEL_ID or DISPATCH_YOUTUBE_ACCOUNT_ID to select the active channel."
            ),
        }

    if w_status in ("legacy_reauth_required", "needs_reauth"):
        raw_acc_id = str(data.get("channel_id") or "")
        legacy_type = str(data.get("legacy_id_type") or ("email" if "@" in raw_acc_id else "legacy"))
        return {
            "status": "legacy_account_needs_reconnect",
            "account_id": raw_acc_id,
            "account_name": str(data.get("channel_title") or data.get("account_name") or ""),
            "user_id": str(data.get("user_id") or target_user_id),
            "id": str(data.get("row_id") or ""),
            "needs_reauth": w_status == "needs_reauth",
            "error": (
                "YouTube account requires reconnection in Dispatch Settings."
                if w_status == "needs_reauth"
                else (
                    f"Cloud connected account has a legacy {legacy_type} identifier ({raw_acc_id}) "
                    "instead of a verified YouTube Channel ID. Please reconnect your YouTube channel in Dispatch Settings."
                )
            ),
        }

    if w_status == "connected":
        chan_id = str(data.get("channel_id") or "").strip()
        if not is_valid_youtube_channel_id(chan_id):
            return {
                "status": "legacy_account_needs_reconnect",
                "account_id": chan_id,
                "account_name": str(data.get("channel_title") or ""),
                "user_id": str(data.get("user_id") or target_user_id),
                "id": str(data.get("row_id") or ""),
                "error": "Cloud Worker returned invalid YouTube Channel ID; please reconnect.",
            }
        return {
            "status": "ok",
            "account_id": chan_id,
            "raw_account_id": chan_id,
            "account_name": str(data.get("channel_title") or ""),
            "user_id": str(data.get("user_id") or target_user_id),
            "id": str(data.get("row_id") or ""),
        }

    return {"status": "cloud_unavailable", "error": f"Unexpected Worker channel status: {w_status}"}


def update_job_stage_by_video_id(video_id: str, stage: str) -> None:
    """Report downstream processing stage for a video ID via Cloud Worker broker."""
    if not video_id or not stage:
        return
    try:
        worker_url, worker_token = _worker_config()
        if not worker_url or not worker_token:
            return
        _worker_post(
            "/sync/pc/remote-jobs/stage-by-video",
            {"video_id": video_id, "stage": stage},
            timeout_seconds=8.0,
        )
    except Exception as e:
        logger.debug("Failed to update stage by video_id %s: %s", video_id, e)


def finish_remote_job(job_id: str, status: str, video_id: str = "", error: str = "") -> None:
    if not job_id:
        return
    http_status, data = _worker_post(
        "/sync/pc/remote-jobs/finish",
        {"job_id": job_id, "status": status, "video_id": video_id, "error": error},
    )
    if http_status >= 400:
        raise VaultUnavailable(f"Worker finish_remote_job failed ({http_status}): {data.get('error', '')}")


class RemoteJobPoller:
    """Claims queued remote jobs and runs them through the normal ingestion
    pipeline (visibility gate included). Failures are recorded on the job,
    never silently dropped; a claimed job that crashes is marked failed."""

    def __init__(
        self,
        ingest_video,
        direct_handler=None,
        worker_id: str = "owner-pc-1",
        interval_seconds: int = 30,
    ):
        self.ingest_video = ingest_video
        # Direct-route handler (Route C): downloads a user-uploaded R2 object and
        # registers it in the inbox. Optional for backward compatibility; a queued
        # direct job without a handler fails loudly instead of being swallowed.
        self.direct_handler = direct_handler
        self.worker_id = worker_id
        self.interval_seconds = interval_seconds
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run_forever(self) -> None:
        import time
        while not self._stop:
            try:
                job = claim_remote_job(self.worker_id)
                if job is None:
                    time.sleep(self.interval_seconds)
                    continue
                self._process(job)
            except VaultUnavailable as e:
                logger.warning("Remote jobs paused (cloud unavailable): %s", e)
                time.sleep(self.interval_seconds * 2)
            except Exception as e:  # keep the poller alive
                logger.error("Remote job poller error: %s", e)
                time.sleep(self.interval_seconds)

    def _process(self, job: RemoteJob) -> None:
        try:
            update_remote_job_stage(job.job_id, "downloading")
            if (getattr(job, "source", "youtube") or "youtube") == "direct":
                if self.direct_handler is None:
                    raise RuntimeError("direct job claimed but no direct handler configured on this PC")
                result = self.direct_handler(job)
            else:
                result = self.ingest_video(job.youtube_url, user_id=job.user_id)
            status = result.get("status", "FAILED")
            vid = result.get("video_id", "")
            dur = (result.get("metadata") or {}).get("duration") or result.get("duration")
            if status in ("DOWNLOADED", "WAITING_FOR_YOUTUBE_PROCESSING", "WAITING_FOR_VISIBILITY_CHECK"):
                update_remote_job_stage(job.job_id, "transcribing", video_id=vid, duration=dur)
                finish_remote_job(job.job_id, "done", vid, str(result.get("error", "")))
                logger.info("Remote job %s accepted: %s", job.job_id, status)
            else:
                finish_remote_job(job.job_id, "failed", vid, str(result.get("error", "ingestion failed")))
        except Exception as e:
            finish_remote_job(job.job_id, "failed", "", str(e))
            logger.error("Remote job %s failed: %s", job.job_id, e)
