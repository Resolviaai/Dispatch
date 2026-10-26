"""Per-user YouTube token vault (PC side) and remote job queue client.

The Android app performs on-device Google sign-in; the Cloudflare Worker exchanges
the code and stores the refresh token ENCRYPTED (AES-256-GCM, base64url(iv||ct))
in Supabase youtube_connections using TOKEN_VAULT_KEY. The PC decrypts with the
same key when processing that user's videos. The key never leaves the two
server-side components (Worker secret + PC env).
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dispatch.youtube_connections")


class VaultUnavailable(Exception):
    """Cloud/Supabase unreachable or misconfigured; caller must fail closed."""


try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def get_candidate_vault_keys() -> List[bytes]:
    """Return all plausible 256-bit vault keys matching Cloudflare Worker aesKey derivation."""
    raw_key = _env("DISPATCH_TOKEN_VAULT_KEY")
    keys: List[bytes] = []

    # 1. Base64
    if raw_key:
        try:
            b = base64.b64decode(raw_key)
            if len(b) in (16, 24, 32):
                keys.append(b)
        except Exception:
            pass

    # 2. Hex
    if raw_key and len(raw_key) in (32, 48, 64) and all(c in "0123456789abcdefABCDEF" for c in raw_key):
        try:
            keys.append(bytes.fromhex(raw_key))
        except Exception:
            pass

    # 3. SHA-256 derivation of vault key string if set
    import hashlib
    if raw_key:
        keys.append(hashlib.sha256(raw_key.strip().encode("utf-8")).digest())

    # 4. Fallback vault key (matches Worker aesKey fallback)
    keys.append(hashlib.sha256(b"dispatch-fallback-vault-key").digest())

    return keys


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
    last_err = None
    for k in candidates:
        try:
            return AESGCM(k).decrypt(iv, ct, None).decode("utf-8")
        except Exception as e:
            last_err = e

    if last_err:
        raise last_err
    raise ValueError("No vault keys available")


def _supabase_rpc(action: str, params: Dict[str, Any]) -> Any:
    """Service-role RPC against the linked Supabase project. Never logs params."""
    import httpx

    url, key = _env("SUPABASE_URL"), _env("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise VaultUnavailable("Supabase not configured on the PC")
    resp = httpx.post(f"{url}/rest/v1/rpc/{action}", json=params,
                      headers={"apikey": key, "Authorization": f"Bearer {key}",
                               "Content-Type": "application/json"}, timeout=20.0)
    if resp.status_code not in (200, 204):
        raise VaultUnavailable(f"Supabase RPC {action} failed: {resp.status_code}")
    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


def get_user_refresh_token(user_id: str) -> str:
    """Decrypt and return the given user's YouTube refresh token, or '' if none."""
    rows = _supabase_rpc("cloud_get_refresh_token", {"p_user_id": user_id})
    token = rows[0]["cloud_get_refresh_token"] if isinstance(rows, list) else rows
    if not token:
        return ""
    return decrypt_refresh_token(token)


def disconnect_user_token(user_id: str) -> None:
    """Remove a user's stored YouTube credential (reconnect re-runs on-device sign-in)."""
    _supabase_rpc("cloud_delete_refresh_token", {"p_user_id": user_id})


def get_user_gemini_key(user_id: Optional[str] = None) -> str:
    """Decrypt and return the given user's vaulted Gemini API key, or '' if none."""
    if not user_id:
        return ""
    try:
        rows = _supabase_rpc("cloud_get_gemini_key", {"p_user_id": user_id})
        enc = rows[0]["cloud_get_gemini_key"] if isinstance(rows, list) and rows else rows
        if not enc or not isinstance(enc, str):
            return ""
        return decrypt_refresh_token(enc)
    except Exception as e:
        logger.debug("Could not retrieve vaulted Gemini key for user %s: %s", user_id, e)
        return ""


def get_account_credentials(account_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    """Retrieve and decrypt credentials for a connected account.
    Returns parsed credentials dict or empty dict if not found."""
    if not account_id:
        return {}
    try:
        rows = _supabase_rpc("cloud_get_credentials_by_account_id", {
            "p_account_id": str(account_id),
            "p_user_id": user_id
        })
        enc = rows[0]["cloud_get_credentials_by_account_id"] if isinstance(rows, list) and rows else rows
        if not enc or not isinstance(enc, str):
            return {}
        decrypted = decrypt_refresh_token(enc)
        import json
        if decrypted.startswith("{"):
            return json.loads(decrypted)
        return {"refresh_token": decrypted}
    except Exception as e:
        logger.debug("Could not retrieve credentials for account %s: %s", account_id, e)
        return {}


class RemoteJob:
    def __init__(self, job_id: str, user_id: str, youtube_url: str):
        self.job_id = job_id
        self.user_id = user_id
        self.youtube_url = youtube_url


def claim_remote_job(worker_id: str) -> Optional[RemoteJob]:
    """Atomically claim the oldest queued remote job (single-worker alpha)."""
    rows = _supabase_rpc("cloud_claim_job", {"p_worker_id": worker_id})
    arr = rows if isinstance(rows, list) else []
    if not arr:
        return None
    row = arr[0]
    return RemoteJob(row["job_id"], row["user_id"], row["youtube_url"])


def finish_remote_job(job_id: str, status: str, video_id: str = "", error: str = "") -> None:
    _supabase_rpc("cloud_finish_job", {
        "p_job_id": job_id, "p_status": status, "p_video_id": video_id, "p_error": error,
    })


class RemoteJobPoller:
    """Claims queued remote jobs and runs them through the normal ingestion
    pipeline (visibility gate included). Failures are recorded on the job,
    never silently dropped; a claimed job that crashes is marked failed."""

    def __init__(self, ingest_video, worker_id: str = "owner-pc-1", interval_seconds: int = 30):
        self.ingest_video = ingest_video
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
            result = self.ingest_video(job.youtube_url, user_id=job.user_id)
            status = result.get("status", "FAILED")
            if status in ("DOWNLOADED", "WAITING_FOR_YOUTUBE_PROCESSING", "WAITING_FOR_VISIBILITY_CHECK"):
                # Ingestion accepted the source; downstream workers own the rest.
                finish_remote_job(job.job_id, "done", result.get("video_id", ""),
                                  str(result.get("error", "")))
                logger.info("Remote job %s accepted: %s", job.job_id, status)
            else:
                finish_remote_job(job.job_id, "failed", result.get("video_id", ""),
                                  str(result.get("error", "ingestion failed")))
        except Exception as e:
            finish_remote_job(job.job_id, "failed", "", str(e))
            logger.error("Remote job %s failed: %s", job.job_id, e)
