"""Instagram Reels publishing adapter using Instagram Graph API."""
import os
import time
import logging
import requests
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("dispatch.publisher.instagram")

GRAPH_API_VERSION = "v19.0"
GRAPH_BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


def upload_instagram_reel(
    video_path: Path,
    caption: str,
    access_token: Optional[str] = None,
    ig_user_id: Optional[str] = None
) -> Dict[str, Any]:
    """Upload video to Instagram Reels via Instagram Graph API.
    
    Args:
        video_path: Local path to .mp4 video file.
        caption: Caption text with hashtags.
        access_token: Instagram Graph API User Access Token.
        ig_user_id: Instagram Business/Creator Account ID.
        
    Returns:
        Dict with status, remote_id, and url.
    """
    token = access_token or os.getenv("INSTAGRAM_ACCESS_TOKEN")
    user_id = ig_user_id or os.getenv("INSTAGRAM_USER_ID")

    if not token or not user_id:
        logger.info("[MOCK/SIMULATION] Instagram Reel publishing simulated for '%s'", video_path.name)
        logger.info("Instagram credentials (INSTAGRAM_ACCESS_TOKEN / INSTAGRAM_USER_ID) not configured.")
        return {
            "status": "published",
            "remote_id": f"ig_sim_{video_path.stem}",
            "remote_url": f"https://instagram.com/reel/sim_{video_path.stem}",
            "is_simulation": True
        }

    if not video_path.exists():
        raise FileNotFoundError(f"Video file missing: {video_path}")

    file_size = video_path.stat().st_size

    try:
        logger.info("Initiating Instagram Reels container creation for %s (%d bytes)", user_id, file_size)

        # Step 1: Create Resumable Media Container
        container_url = f"{GRAPH_BASE_URL}/{user_id}/media"
        params = {
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": caption,
            "access_token": token
        }
        res = requests.post(container_url, data=params, timeout=30)
        res.raise_for_status()
        container_data = res.json()
        container_id = container_data.get("id")
        upload_uri = container_data.get("uri")

        if not container_id:
            raise ValueError(f"No container ID returned: {container_data}")

        # Step 2: Upload Video Bytes
        if upload_uri:
            logger.info("Uploading %d video bytes to Instagram upload URI...", file_size)
            with open(video_path, "rb") as f:
                headers = {
                    "Authorization": f"OAuth {token}",
                    "offset": "0",
                    "file_size": str(file_size)
                }
                upload_res = requests.post(upload_uri, headers=headers, data=f, timeout=120)
                upload_res.raise_for_status()

        # Step 3: Poll Container Processing Status
        logger.info("Polling container %s processing status...", container_id)
        status_url = f"{GRAPH_BASE_URL}/{container_id}"
        is_ready = False
        for _ in range(24):  # Up to 2 minutes (24 * 5s)
            status_res = requests.get(
                status_url,
                params={"fields": "status_code,status", "access_token": token},
                timeout=15
            )
            if status_res.status_code == 200:
                sdata = status_res.json()
                code = sdata.get("status_code")
                if code == "FINISHED":
                    is_ready = True
                    break
                elif code == "ERROR":
                    raise RuntimeError(f"Instagram processing error: {sdata.get('status')}")
            time.sleep(5)

        if not is_ready:
            raise TimeoutError(f"Instagram container {container_id} timed out processing")

        # Step 4: Publish Container
        logger.info("Publishing container %s to Instagram Reels...", container_id)
        publish_url = f"{GRAPH_BASE_URL}/{user_id}/media_publish"
        pub_res = requests.post(publish_url, data={"creation_id": container_id, "access_token": token}, timeout=30)
        pub_res.raise_for_status()
        media_id = pub_res.json().get("id", container_id)

        # Step 5: Fetch permalink
        permalink = f"https://www.instagram.com/reel/{media_id}"
        try:
            p_res = requests.get(
                f"{GRAPH_BASE_URL}/{media_id}",
                params={"fields": "permalink", "access_token": token},
                timeout=10
            )
            if p_res.status_code == 200:
                permalink = p_res.json().get("permalink", permalink)
        except Exception:
            pass

        logger.info("Successfully published Reel to Instagram: %s", permalink)
        return {
            "status": "published",
            "remote_id": media_id,
            "remote_url": permalink,
            "is_simulation": False
        }

    except Exception as e:
        logger.error("Instagram publish failed: %s", e)
        raise
