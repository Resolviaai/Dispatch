"""Facebook Reels & Video publishing adapter using Meta Graph API."""
import os
import time
import logging
import requests
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("dispatch.publisher.facebook")

GRAPH_API_VERSION = "v21.0"
GRAPH_BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


def upload_facebook_reel(
    video_path: Path,
    description: str,
    access_token: Optional[str] = None,
    page_id: Optional[str] = None
) -> Dict[str, Any]:
    """Upload vertical video to Facebook Reels via Meta Graph API.
    
    Args:
        video_path: Local path to .mp4 video file.
        description: Reel description and hashtags.
        access_token: Page Access Token with pages_manage_posts and pages_read_engagement.
        page_id: Facebook Page ID.
        
    Returns:
        Dict with status, remote_id, and url.
    """
    token = access_token or os.getenv("FACEBOOK_ACCESS_TOKEN")
    target_page_id = page_id or os.getenv("FACEBOOK_PAGE_ID")

    if not token or not target_page_id:
        raise PermissionError("Facebook Graph API credentials missing (FACEBOOK_ACCESS_TOKEN and FACEBOOK_PAGE_ID).")

    if not video_path.exists():
        raise FileNotFoundError(f"Video file missing: {video_path}")

    file_size = video_path.stat().st_size

    try:
        logger.info("Initiating Facebook Reels session for page %s (%d bytes)", target_page_id, file_size)

        # Step 1: Start Resumable Reel Upload Session
        init_url = f"{GRAPH_BASE_URL}/{target_page_id}/video_reels"
        init_params = {
            "upload_phase": "start",
            "access_token": token
        }
        res = requests.post(init_url, data=init_params, timeout=30)
        res.raise_for_status()
        init_data = res.json()
        video_id = init_data.get("video_id")
        upload_url = init_data.get("upload_url")

        if not video_id or not upload_url:
            raise ValueError(f"Facebook Reels initialization failed: {init_data}")

        # Step 2: Upload Video Bytes
        logger.info("Uploading %d bytes to Facebook upload URL...", file_size)
        with open(video_path, "rb") as f:
            headers = {
                "Authorization": f"OAuth {token}",
                "offset": "0",
                "file_size": str(file_size)
            }
            upload_res = requests.post(upload_url, headers=headers, data=f, timeout=120)
            upload_res.raise_for_status()

        # Step 3: Finish and Publish Reel
        logger.info("Finalizing and publishing Facebook Reel %s...", video_id)
        finish_params = {
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": description,
            "access_token": token
        }
        finish_res = requests.post(init_url, data=finish_params, timeout=30)
        finish_res.raise_for_status()
        finish_data = finish_res.json()

        if not finish_data.get("success", False) and "video_id" not in finish_data:
            logger.warning("Facebook finish call returned unexpected payload: %s", finish_data)

        # Step 4: Verify Video Status
        status_url = f"{GRAPH_BASE_URL}/{video_id}"
        is_ready = False
        for _ in range(12):  # Poll up to 60 seconds (12 * 5s)
            status_res = requests.get(
                status_url,
                params={"fields": "status", "access_token": token},
                timeout=15
            )
            if status_res.status_code == 200:
                sdata = status_res.json()
                st = sdata.get("status", {}).get("video_status")
                if st in ("ready", "complete"):
                    is_ready = True
                    break
                elif st == "error":
                    raise RuntimeError(f"Facebook Reel processing failed: {sdata}")
            time.sleep(5)

        remote_url = f"https://www.facebook.com/reel/{video_id}"
        logger.info("Facebook Reel %s published: %s", video_id, remote_url)

        return {
            "status": "published",
            "remote_id": video_id,
            "remote_url": remote_url,
            "is_simulation": False
        }

    except Exception as e:
        logger.error("Facebook Reel publication failed: %s", e)
        raise
