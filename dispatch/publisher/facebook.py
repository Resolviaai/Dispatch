"""Facebook Reels & Video publishing adapter using Meta Graph API."""
import os
import time
import logging
import requests
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger("dispatch.publisher.facebook")

GRAPH_API_VERSION = "v21.0"
GRAPH_BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


def _parse_json_safe(resp: requests.Response) -> Dict[str, Any]:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _is_meta_auth_failure(resp: requests.Response) -> bool:
    data = _parse_json_safe(resp)
    err = data.get("error") if isinstance(data.get("error"), dict) else {}
    return bool(
        resp.status_code in (401, 403)
        or err.get("code") in (190, 102, 200, 10)
        or err.get("type") == "OAuthException"
    )


def _raise_for_fb_auth_or_status(resp: requests.Response, context: str) -> None:
    if _is_meta_auth_failure(resp):
        data = _parse_json_safe(resp)
        err = data.get("error") if isinstance(data.get("error"), dict) else {}
        msg = str(err.get("message") or resp.text or f"HTTP {resp.status_code}")[:240]
        raise PermissionError(
            f"Facebook authorization expired or revoked during {context} ({resp.status_code}): {msg}"
        )
    resp.raise_for_status()


def _resolve_page_token(
    token: str,
    page_id: Optional[str],
    preferred_page_id: Optional[str] = None,
) -> Tuple[str, str]:
    """Resolve an explicit Page ID + Page Access Token when a User token or blank page_id is passed.

    `preferred_page_id` carries a stored credential page_id (see
    dispatch.youtube_connections.get_account_credentials, which copies the brokered
    `page_id` field into the credential payload): when /me/accounts lists several
    Pages, the Page matching it is selected instead of raising. The explicit
    ambiguity error is kept only when no preferred id is given (or none matches).
    """
    headers = {"Authorization": f"Bearer {token}"}
    if page_id:
        res = requests.get(
            f"{GRAPH_BASE_URL}/{page_id}",
            headers=headers,
            params={"fields": "id,access_token"},
            timeout=(10, 15),
        )
        if _is_meta_auth_failure(res):
            _raise_for_fb_auth_or_status(res, "page token lookup")
        if res.status_code == 200:
            data = _parse_json_safe(res)
            resolved_tok = str(data.get("access_token") or "").strip()
            if resolved_tok:
                return resolved_tok, str(data.get("id") or page_id)
        return token, page_id

    accounts_res = requests.get(
        f"{GRAPH_BASE_URL}/me/accounts",
        headers=headers,
        params={"fields": "id,name,access_token"},
        timeout=(10, 15),
    )
    _raise_for_fb_auth_or_status(accounts_res, "me/accounts page lookup")
    pages = _parse_json_safe(accounts_res).get("data") or []
    if not isinstance(pages, list) or not pages:
        raise PermissionError("No Facebook Page found for this account.")
    if len(pages) > 1:
        preferred = str(preferred_page_id or "").strip()
        if preferred:
            match = next(
                (pg for pg in pages if str(pg.get("id") or "").strip() == preferred),
                None,
            )
            if match is not None:
                resolved_id = str(match.get("id") or "").strip()
                resolved_tok = str(match.get("access_token") or token).strip()
                if resolved_id and resolved_tok:
                    return resolved_tok, resolved_id
        raise PermissionError("Multiple Facebook Pages found; explicit page_id is required.")
    page = pages[0]
    resolved_id = str(page.get("id") or "").strip()
    resolved_tok = str(page.get("access_token") or token).strip()
    if not resolved_id or not resolved_tok:
        raise PermissionError("Facebook Page credentials missing access_token or id.")
    return resolved_tok, resolved_id


def _probe_rupload_offset(upload_url: str, token: str) -> int:
    try:
        probe = requests.get(
            upload_url,
            headers={"Authorization": f"OAuth {token}"},
            timeout=(10, 15),
        )
        if _is_meta_auth_failure(probe):
            _raise_for_fb_auth_or_status(probe, "resumable offset probe")
        if probe.status_code == 200:
            data = _parse_json_safe(probe)
            raw_offset = data.get("offset")
            if raw_offset is None:
                raw_offset = data.get("bytes_transferred", 0)
            return max(0, int(raw_offset or 0))
    except PermissionError:
        raise
    except Exception as exc:
        logger.debug("Facebook rupload offset probe skipped: %s", exc)
    return 0


class _FileSliceReader:
    """File-like wrapper streaming from offset without buffering the entire file."""

    def __init__(self, fh, remaining_bytes: int):
        self._fh = fh
        self._remaining = max(0, int(remaining_bytes))

    def __len__(self) -> int:
        return self._remaining

    def read(self, size: int = -1) -> bytes:
        if self._remaining <= 0:
            return b""
        if size is None or size < 0 or size > self._remaining:
            size = self._remaining
        chunk = self._fh.read(size)
        self._remaining -= len(chunk)
        return chunk


def upload_facebook_reel(
    video_path: Path,
    description: str,
    access_token: Optional[str] = None,
    page_id: Optional[str] = None,
    poll_interval_seconds: float = 5.0,
    credentials: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Upload vertical video to Facebook Reels via Meta Graph API.

    Args:
        video_path: Local path to .mp4 video file.
        description: Reel description and hashtags.
        access_token: Page Access Token with pages_manage_posts and pages_read_engagement.
        page_id: Facebook Page ID.
        poll_interval_seconds: Seconds between status polls.
        credentials: Optional full credential payload (as returned by
            dispatch.youtube_connections.get_account_credentials). Its stored
            page_id is used to disambiguate multi-Page accounts when no explicit
            page_id is available.

    Returns:
        Dict with status, remote_id, and url.
    """
    token = access_token or os.getenv("FACEBOOK_ACCESS_TOKEN")
    target_page_id = page_id or os.getenv("FACEBOOK_PAGE_ID")
    # Stored credential page_id (optional brokered field) disambiguates the
    # /me/accounts listing when no explicit Page id was provided.
    preferred_page_id = str((credentials or {}).get("page_id") or "").strip() or None

    if not token:
        raise PermissionError("Facebook Graph API credentials missing (FACEBOOK_ACCESS_TOKEN).")

    if not target_page_id:
        token, target_page_id = _resolve_page_token(token, None, preferred_page_id=preferred_page_id)

    if not video_path.exists():
        raise FileNotFoundError(f"Video file missing: {video_path}")

    file_size = video_path.stat().st_size
    if file_size <= 0:
        raise ValueError(f"Facebook Reel upload refused: video file is empty ({video_path})")

    try:
        logger.info("Initiating Facebook Reels session for page %s (%d bytes)", target_page_id, file_size)

        # Step 1: Start Resumable Reel Upload Session (exchange user token -> page token if needed)
        init_url = f"{GRAPH_BASE_URL}/{target_page_id}/video_reels"
        res = requests.post(
            init_url,
            headers={"Authorization": f"Bearer {token}"},
            data={"upload_phase": "start"},
            timeout=(15, 30),
        )
        if res.status_code in (400, 403):
            err_msg = str(_parse_json_safe(res).get("error", {}).get("message", "")).lower()
            if "page access token" in err_msg or "impersonating" in err_msg or res.status_code == 403:
                token, target_page_id = _resolve_page_token(token, target_page_id)
                init_url = f"{GRAPH_BASE_URL}/{target_page_id}/video_reels"
                res = requests.post(
                    init_url,
                    headers={"Authorization": f"Bearer {token}"},
                    data={"upload_phase": "start"},
                    timeout=(15, 30),
                )
        _raise_for_fb_auth_or_status(res, "video_reels start")
        init_data = _parse_json_safe(res)
        video_id = init_data.get("video_id")
        upload_url = init_data.get("upload_url")

        if not video_id or not upload_url:
            raise ValueError(f"Facebook Reels initialization failed: {init_data}")

        # Step 2: Stream Video Bytes with resumable offset recovery
        logger.info("Uploading %d bytes to Facebook upload URL...", file_size)
        offset = 0
        for attempt in range(2):
            with open(video_path, "rb") as f:
                if offset > 0:
                    f.seek(offset)
                remaining = max(0, file_size - offset)
                headers = {
                    "Authorization": f"OAuth {token}",
                    "offset": str(offset),
                    "file_size": str(file_size),
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(remaining),
                }
                payload = _FileSliceReader(f, remaining) if offset > 0 else f
                upload_res = requests.post(upload_url, headers=headers, data=payload, timeout=(15, 120))
            if _is_meta_auth_failure(upload_res):
                _raise_for_fb_auth_or_status(upload_res, "binary upload")
            if upload_res.status_code >= 500 and attempt == 0:
                probed = _probe_rupload_offset(upload_url, token)
                if probed >= file_size:
                    break
                offset = probed
                continue
            _raise_for_fb_auth_or_status(upload_res, "binary upload")
            break

        # Step 3: Finish and Publish Reel
        logger.info("Finalizing and publishing Facebook Reel %s...", video_id)
        finish_params = {
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": description,
        }
        finish_res = requests.post(
            init_url,
            headers={"Authorization": f"Bearer {token}"},
            data=finish_params,
            timeout=(15, 30),
        )
        _raise_for_fb_auth_or_status(finish_res, "video_reels finish")
        finish_data = _parse_json_safe(finish_res)

        if not finish_data.get("success", False) and "video_id" not in finish_data:
            raise RuntimeError(f"Facebook Reel finish call failed: {finish_data}")

        # Step 4: Verify Video Status
        status_url = f"{GRAPH_BASE_URL}/{video_id}"
        is_ready = False
        for idx in range(12):  # Poll up to 60 seconds (12 * 5s)
            status_res = requests.get(
                status_url,
                headers={"Authorization": f"Bearer {token}"},
                params={"fields": "status"},
                timeout=(10, 15),
            )
            if _is_meta_auth_failure(status_res):
                _raise_for_fb_auth_or_status(status_res, "status poll")
            if status_res.status_code == 200:
                sdata = _parse_json_safe(status_res)
                st_obj = sdata.get("status") if isinstance(sdata.get("status"), dict) else {}
                st = str(st_obj.get("video_status") or "").lower()
                proc_st = str((st_obj.get("processing_phase") or {}).get("status") or "").lower()
                if st in ("ready", "complete") or proc_st == "complete":
                    is_ready = True
                    break
                if st == "error" or proc_st == "error":
                    raise RuntimeError(f"Facebook Reel processing failed: {sdata}")
            if idx < 11 and poll_interval_seconds > 0:
                time.sleep(poll_interval_seconds)

        # Only report 'published' once Meta confirmed the video is ready; falling
        # through while Meta is still processing would mark the outbox row published
        # before the Reel actually exists publicly.
        if not is_ready:
            raise TimeoutError(f"Facebook Reel {video_id} timed out processing")

        remote_url = f"https://www.facebook.com/reel/{video_id}"
        logger.info("Facebook Reel %s published: %s", video_id, remote_url)

        return {
            "status": "published",
            "remote_id": video_id,
            "remote_url": remote_url,
            "is_simulation": False,
        }

    except Exception as e:
        logger.error("Facebook Reel publication failed: %s", e)
        raise
