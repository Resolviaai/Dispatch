"""X (formerly Twitter) video publishing adapter using X API v2 and v1.1 Media Upload."""
import os
import time
import logging
import requests
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("dispatch.publisher.twitter")


def upload_x_video(
    video_path: Path,
    text: str,
    bearer_token: Optional[str] = None,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
    oauth_token: Optional[str] = None,
    oauth_secret: Optional[str] = None
) -> Dict[str, Any]:
    """Publish short-form video tweet to X / Twitter.
    
    Args:
        video_path: Local path to .mp4 video file.
        text: Tweet text with hashtags.
        bearer_token: X API v2 Bearer Token.
        api_key, api_secret, oauth_token, oauth_secret: OAuth 1.0a credentials for media upload.
        
    Returns:
        Dict with status, remote_id, remote_url, is_simulation.
    """
    token = bearer_token or os.getenv("X_BEARER_TOKEN") or os.getenv("TWITTER_BEARER_TOKEN")
    key = api_key or os.getenv("X_API_KEY")

    if not token and not key:
        raise PermissionError("X / Twitter API credentials missing (X_API_KEY, X_BEARER_TOKEN).")

    if not video_path.exists():
        raise FileNotFoundError(f"Video file missing: {video_path}")

    file_size = video_path.stat().st_size

    try:
        from requests_oauthlib import OAuth1
        oauth = OAuth1(
            key or os.getenv("X_API_KEY"),
            api_secret or os.getenv("X_API_SECRET"),
            oauth_token or os.getenv("X_ACCESS_TOKEN"),
            oauth_secret or os.getenv("X_ACCESS_SECRET")
        )

        upload_url = "https://upload.twitter.com/1.1/media/upload.json"

        # Step 1: INIT
        init_res = requests.post(
            upload_url,
            auth=oauth,
            data={
                "command": "INIT",
                "media_type": "video/mp4",
                "total_bytes": str(file_size),
                "media_category": "tweet_video"
            },
            timeout=30
        )
        init_res.raise_for_status()
        media_id = init_res.json()["media_id_string"]

        # Step 2: APPEND chunks (5MB slices)
        segment_id = 0
        with open(video_path, "rb") as f:
            while True:
                chunk = f.read(5 * 1024 * 1024)
                if not chunk:
                    break
                append_res = requests.post(
                    upload_url,
                    auth=oauth,
                    data={"command": "APPEND", "media_id": media_id, "segment_index": str(segment_id)},
                    files={"media": chunk},
                    timeout=60
                )
                append_res.raise_for_status()
                segment_id += 1

        # Step 3: FINALIZE
        fin_res = requests.post(upload_url, auth=oauth, data={"command": "FINALIZE", "media_id": media_id}, timeout=30)
        fin_res.raise_for_status()

        # Step 4: Post Tweet via v2
        tweet_url = "https://api.twitter.com/2/tweets"
        tweet_body = {
            "text": text[:280],
            "media": {"media_ids": [media_id]}
        }
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        tweet_res = requests.post(tweet_url, auth=oauth if not token else None, headers=headers, json=tweet_body, timeout=30)
        tweet_res.raise_for_status()
        tweet_id = tweet_res.json().get("data", {}).get("id", media_id)
        permalink = f"https://x.com/i/status/{tweet_id}"

        logger.info("Successfully published video to X: %s", permalink)
        return {
            "status": "published",
            "remote_id": tweet_id,
            "remote_url": permalink,
            "is_simulation": False
        }

    except Exception as e:
        logger.error("X video publish failed: %s", e)
        raise
