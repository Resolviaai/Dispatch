"""Instagram Reels publishing adapter using Instagram Graph API."""
import os
import time
import logging
import requests
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("dispatch.publisher.instagram")

GRAPH_API_VERSION = "v21.0"
GRAPH_BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


def _parse_json_safe(resp: requests.Response) -> Dict[str, Any]:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _raise_for_meta_auth_or_status(resp: requests.Response, context: str) -> None:
    data = _parse_json_safe(resp)
    err = data.get("error") if isinstance(data.get("error"), dict) else {}
    err_code = err.get("code")
    err_type = str(err.get("type") or "")
    err_msg = str(err.get("message") or resp.text or f"HTTP {resp.status_code}")[:240]
    if resp.status_code in (401, 403) or err_code in (190, 102, 200, 10) or err_type == "OAuthException":
        raise PermissionError(
            f"Instagram authorization expired or revoked during {context} ({resp.status_code}): {err_msg}"
        )
    resp.raise_for_status()


def _probe_rupload_offset(upload_uri: str, token: str) -> int:
    try:
        probe = requests.get(
            upload_uri,
            headers={"Authorization": f"OAuth {token}"},
            timeout=(10, 15),
        )
        if probe.status_code in (401, 403):
            _raise_for_meta_auth_or_status(probe, "resumable offset probe")
        if probe.status_code == 200:
            data = _parse_json_safe(probe)
            raw_offset = data.get("offset")
            if raw_offset is None:
                raw_offset = data.get("bytes_transferred", 0)
            return max(0, int(raw_offset or 0))
    except PermissionError:
        raise
    except Exception as exc:
        logger.debug("Instagram rupload offset probe skipped: %s", exc)
    return 0


class _FileSliceReader:
    """File-like wrapper streaming from offset without loading the whole video into RAM."""

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


def upload_instagram_reel(
    video_path: Path,
    caption: str,
    access_token: Optional[str] = None,
    ig_user_id: Optional[str] = None,
    api_base: Optional[str] = None,
    poll_interval_seconds: float = 5.0,
) -> Dict[str, Any]:
    """Upload video to Instagram Reels via the Instagram Graph API.
    
    Args:
        video_path: Local path to .mp4 video file.
        caption: Caption text with hashtags.
        access_token: Instagram user access token.
        ig_user_id: Instagram Professional account ID.
        api_base: API host for this connection. Instagram-Login credentials
            carry ``https://graph.instagram.com/v21.0``; legacy Facebook-Login
            credentials fall back to the Facebook Graph host.
        poll_interval_seconds: Seconds between container status polls.
        
    Returns:
        Dict with status, remote_id, and url.
    """
    token = access_token or os.getenv("INSTAGRAM_ACCESS_TOKEN")
    user_id = ig_user_id or os.getenv("INSTAGRAM_USER_ID")
    base_url = (api_base or os.getenv("INSTAGRAM_API_BASE") or GRAPH_BASE_URL).rstrip("/")

    if not token or not user_id:
        raise PermissionError("Instagram Graph API credentials missing (access token and account id).")

    if not video_path.exists():
        raise FileNotFoundError(f"Video file missing: {video_path}")

    file_size = video_path.stat().st_size
    if file_size <= 0:
        raise ValueError(f"Instagram upload refused: video file is empty ({video_path})")

    auth_headers = {"Authorization": f"Bearer {token}"}

    try:
        logger.info("Initiating Instagram Reels container creation for %s (%d bytes)", user_id, file_size)

        # Step 1: Create Resumable Media Container
        container_url = f"{base_url}/{user_id}/media"
        params = {
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": caption,
        }
        res = requests.post(container_url, headers=auth_headers, data=params, timeout=(15, 30))
        _raise_for_meta_auth_or_status(res, "container creation")
        container_data = _parse_json_safe(res)
        container_id = container_data.get("id")
        upload_uri = container_data.get("uri")

        if not container_id:
            raise ValueError(f"No container ID returned: {container_data}")

        # Step 2: Upload Video Bytes with resumable offset recovery on transient 5xx
        if upload_uri:
            logger.info("Uploading %d video bytes to Instagram upload URI...", file_size)
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
                    upload_res = requests.post(upload_uri, headers=headers, data=payload, timeout=(15, 120))
                if upload_res.status_code in (401, 403):
                    _raise_for_meta_auth_or_status(upload_res, "binary upload")
                if upload_res.status_code >= 500 and attempt == 0:
                    probed = _probe_rupload_offset(upload_uri, token)
                    if probed >= file_size:
                        break
                    offset = probed
                    continue
                _raise_for_meta_auth_or_status(upload_res, "binary upload")
                break

        # Step 3: Poll Container Processing Status
        logger.info("Polling container %s processing status...", container_id)
        status_url = f"{base_url}/{container_id}"
        is_ready = False
        for idx in range(24):  # Up to 2 minutes (24 * 5s)
            status_res = requests.get(
                status_url,
                headers=auth_headers,
                params={"fields": "status_code,status"},
                timeout=(10, 15),
            )
            if status_res.status_code in (401, 403):
                _raise_for_meta_auth_or_status(status_res, "container status poll")
            if status_res.status_code == 200:
                sdata = _parse_json_safe(status_res)
                code = str(sdata.get("status_code") or "").upper()
                if code in ("FINISHED", "PUBLISHED"):
                    is_ready = True
                    break
                if code in ("ERROR", "EXPIRED"):
                    raise RuntimeError(f"Instagram processing error: {sdata.get('status') or code}")
            else:
                sdata = _parse_json_safe(status_res)
                if is_meta_oauth := bool((sdata.get("error") or {}).get("code") in (190, 102)):
                    _raise_for_meta_auth_or_status(status_res, "container status poll")
            if idx < 23 and poll_interval_seconds > 0:
                time.sleep(poll_interval_seconds)

        if not is_ready:
            raise TimeoutError(f"Instagram container {container_id} timed out processing")

        # Step 4: Publish Container
        logger.info("Publishing container %s to Instagram Reels...", container_id)
        publish_url = f"{base_url}/{user_id}/media_publish"
        pub_res = requests.post(
            publish_url,
            headers=auth_headers,
            data={"creation_id": container_id},
            timeout=(15, 30),
        )
        _raise_for_meta_auth_or_status(pub_res, "media_publish")
        media_id = _parse_json_safe(pub_res).get("id") or container_id

        # Step 5: Fetch permalink
        permalink = f"https://www.instagram.com/reel/{media_id}"
        try:
            p_res = requests.get(
                f"{base_url}/{media_id}",
                headers=auth_headers,
                params={"fields": "permalink"},
                timeout=(10, 10),
            )
            if p_res.status_code == 200:
                permalink = _parse_json_safe(p_res).get("permalink") or permalink
        except Exception:
            pass

        logger.info("Successfully published Reel to Instagram: %s", permalink)
        return {
            "status": "published",
            "remote_id": media_id,
            "remote_url": permalink,
            "is_simulation": False,
        }

    except Exception as e:
        logger.error("Instagram publish failed: %s", e)
        raise
