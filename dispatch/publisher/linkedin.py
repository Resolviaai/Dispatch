"""LinkedIn video publishing adapter using LinkedIn Videos & Posts APIs."""
import os
import time
import logging
import urllib.parse
import requests
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger("dispatch.publisher.linkedin")

# LinkedIn versioned API header (YYYYMM). The pinned 202501 version was sunset by
# LinkedIn on 2026-01-15 (every request since fails with HTTP 400), so the default
# tracks a current version and operators can pin another via DISPATCH_LINKEDIN_API_VERSION.
LINKEDIN_API_VERSION = os.getenv("DISPATCH_LINKEDIN_API_VERSION", "202610")


def _parse_json_safe(resp: requests.Response) -> Dict[str, Any]:
    try:
        if not getattr(resp, "content", None) and not getattr(resp, "text", None):
            return {}
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _raise_for_linkedin_auth_or_status(resp: requests.Response, context: str) -> None:
    if resp.status_code in (401, 403):
        data = _parse_json_safe(resp)
        msg = str(data.get("message") or resp.text or f"HTTP {resp.status_code}")[:240]
        raise PermissionError(
            f"LinkedIn authorization expired or insufficient permissions during {context} ({resp.status_code}): {msg}"
        )
    resp.raise_for_status()


class _FileSliceReader:
    """Bounded file reader for a single LinkedIn uploadInstruction byte range."""

    def __init__(self, fh, length_bytes: int):
        self._fh = fh
        self._remaining = max(0, int(length_bytes))

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


def _extract_etag(resp: requests.Response) -> str:
    headers = getattr(resp, "headers", None) or {}
    for key in ("etag", "ETag", "Etag"):
        val = headers.get(key)
        if val:
            return str(val).replace('"', "").strip()
    return ""


def _extract_post_urn(resp: requests.Response, fallback_urn: str) -> str:
    headers = getattr(resp, "headers", None) or {}
    for key in ("x-restli-id", "X-RestLi-Id", "X-Restli-Id", "x-linkedin-id"):
        val = headers.get(key)
        if val:
            return str(val).strip()
    data = _parse_json_safe(resp)
    if data.get("id"):
        return str(data["id"]).strip()
    return fallback_urn


def upload_linkedin_video(
    video_path: Path,
    commentary: str,
    title: Optional[str] = None,
    access_token: Optional[str] = None,
    author_urn: Optional[str] = None,
    poll_interval_seconds: float = 2.0,
) -> Dict[str, Any]:
    """Publish video to LinkedIn personal profile or company page.
    
    Args:
        video_path: Path to local .mp4 video file.
        commentary: Post text commentary with hashtags.
        title: Optional video title.
        access_token: OAuth 2.0 access token with w_member_social or w_organization_social.
        author_urn: urn:li:person:XXXX or urn:li:organization:XXXX.
        poll_interval_seconds: Seconds between video processing status checks.
        
    Returns:
        Dict with status, remote_id, remote_url, is_simulation.
    """
    token = access_token or os.getenv("LINKEDIN_ACCESS_TOKEN")
    raw_author = author_urn or os.getenv("LINKEDIN_AUTHOR_URN")

    if not token or not raw_author:
        raise PermissionError("LinkedIn API credentials missing (LINKEDIN_ACCESS_TOKEN and LINKEDIN_AUTHOR_URN).")

    author = str(raw_author).strip()
    if not author.startswith("urn:li:"):
        author = f"urn:li:person:{author}"

    if not video_path.exists():
        raise FileNotFoundError(f"Video file missing: {video_path}")

    file_size = video_path.stat().st_size
    if file_size <= 0:
        raise ValueError(f"LinkedIn upload refused: video file is empty ({video_path})")

    headers = {
        "Authorization": f"Bearer {token}",
        "Linkedin-Version": LINKEDIN_API_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }

    try:
        logger.info("Registering LinkedIn video upload for %s (%d bytes)", author, file_size)

        # Step 1: Initialize upload
        init_url = "https://api.linkedin.com/rest/videos?action=initializeUpload"
        init_body = {
            "initializeUploadRequest": {
                "owner": author,
                "fileSizeBytes": file_size,
                "uploadCaptions": False,
                "uploadThumbnail": False,
            }
        }
        init_res = requests.post(init_url, headers=headers, json=init_body, timeout=(15, 30))
        _raise_for_linkedin_auth_or_status(init_res, "initializeUpload")

        val = _parse_json_safe(init_res).get("value") or {}
        video_urn = val.get("video")
        upload_token = str(val.get("uploadToken") or "")
        instructions = val.get("uploadInstructions") or []

        if not video_urn or not instructions or not instructions[0].get("uploadUrl"):
            raise ValueError(f"LinkedIn video upload initialization failed: {val}")

        # Step 2: Upload every binary part without leaking LinkedIn Bearer to pre-signed CDN URLs
        logger.info("Uploading %d video bytes across %d LinkedIn part(s)...", file_size, len(instructions))
        uploaded_part_ids: List[str] = []
        with open(video_path, "rb") as f:
            for inst in instructions:
                part_url = inst.get("uploadUrl")
                if not part_url:
                    continue
                first_byte = int(inst.get("firstByte", 0) or 0)
                last_byte = int(inst.get("lastByte", file_size - 1) if inst.get("lastByte") is not None else file_size - 1)
                part_len = max(0, min(file_size, last_byte + 1) - max(0, first_byte))
                if part_len <= 0:
                    continue
                f.seek(first_byte)
                part_reader = _FileSliceReader(f, part_len)
                up_headers = {
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(part_len),
                }
                up_res = requests.put(part_url, headers=up_headers, data=part_reader, timeout=(15, 180))
                up_res.raise_for_status()
                etag = _extract_etag(up_res)
                if etag:
                    uploaded_part_ids.append(etag)

        # Step 3: Finalize multi-part upload
        fin_url = "https://api.linkedin.com/rest/videos?action=finalizeUpload"
        fin_body = {
            "finalizeUploadRequest": {
                "video": video_urn,
                "uploadToken": upload_token,
                "uploadedPartIds": uploaded_part_ids,
            }
        }
        fin_res = requests.post(fin_url, headers=headers, json=fin_body, timeout=(15, 30))
        _raise_for_linkedin_auth_or_status(fin_res, "finalizeUpload")

        # Step 4: Poll video status until AVAILABLE. Transient failures (HTTP >= 500
        # or 429) are retried within the same poll deadline; only a definitive 4xx
        # aborts polling early. Post creation proceeds solely once the video is
        # confirmed AVAILABLE (never while LinkedIn is still PROCESSING).
        encoded_urn = urllib.parse.quote(str(video_urn), safe="")
        status_url = f"https://api.linkedin.com/rest/videos/{encoded_urn}"
        is_ready = False
        for idx in range(10):
            st_res = requests.get(status_url, headers=headers, timeout=(10, 15))
            if st_res.status_code in (401, 403):
                _raise_for_linkedin_auth_or_status(st_res, "video status poll")
            if st_res.status_code >= 500 or st_res.status_code == 429:
                # Transient: retry within the existing poll deadline.
                if idx < 9 and poll_interval_seconds > 0:
                    time.sleep(poll_interval_seconds)
                continue
            if st_res.status_code != 200:
                break  # definitive 4xx: re-polling will not change the answer
            st_val = str(_parse_json_safe(st_res).get("status") or "").upper()
            if st_val == "AVAILABLE":
                is_ready = True
                break
            if st_val == "PROCESSING_FAILED":
                raise RuntimeError(f"LinkedIn video processing failed for {video_urn}")
            if idx < 9 and poll_interval_seconds > 0:
                time.sleep(poll_interval_seconds)

        if not is_ready:
            raise TimeoutError(f"LinkedIn video {video_urn} timed out processing")

        # Step 5: Create post via Posts API
        logger.info("Creating LinkedIn post via Posts API...")
        post_url = "https://api.linkedin.com/rest/posts"
        post_body = {
            "author": author,
            "commentary": commentary[:3000],
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "content": {
                "media": {
                    "title": (title or commentary[:50])[:200],
                    "id": video_urn,
                }
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        p_res = requests.post(post_url, headers=headers, json=post_body, timeout=(15, 30))
        _raise_for_linkedin_auth_or_status(p_res, "create post")
        post_urn = _extract_post_urn(p_res, str(video_urn))
        remote_url = f"https://www.linkedin.com/feed/update/{post_urn}"

        logger.info("Successfully published video to LinkedIn: %s", remote_url)
        return {
            "status": "published",
            "remote_id": post_urn,
            "remote_url": remote_url,
            "is_simulation": False,
        }

    except Exception as e:
        logger.error("LinkedIn publish failed: %s", e)
        raise
