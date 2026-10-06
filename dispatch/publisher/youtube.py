"""YouTube Shorts publishing adapter using YouTube Data API v3."""
import os
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("dispatch.publisher.youtube")


def upload_youtube_short(
    video_path: Path,
    title: str,
    description: str,
    tags: str,
    privacy_status: str = "private",
    credentials_path: Optional[Path] = None
) -> Dict[str, Any]:
    """Upload video to YouTube as a Short.
    
    Args:
        video_path: Local path to .mp4 video file.
        title: Title of the Short (must include #Shorts).
        description: Description text.
        tags: Comma-separated or hashtag-separated tags.
        privacy_status: 'private', 'unlisted', or 'public'.
        credentials_path: Optional path to OAuth client credentials.
        
    Returns:
        Dict with status, remote_id, and url.
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video file missing: {video_path}")

    # Ensure #Shorts is present in title or description
    if "#Shorts" not in title and "#shorts" not in title:
        title = f"{title} #Shorts"

    safe_tags = tags or ""
    tag_list = [t.strip().lstrip("#") for t in safe_tags.replace(",", " ").split() if t.strip()]

    # Check for credentials
    token_file = credentials_path or Path("youtube_token.json")
    if not token_file.exists() and not os.getenv("YOUTUBE_ACCESS_TOKEN"):
        logger.info("[MOCK/SIMULATION] YouTube upload configured for '%s' (Privacy: %s).", title, privacy_status)
        return {
            "status": "published",
            "remote_id": f"yt_sim_{video_path.stem}",
            "remote_url": f"https://youtube.com/shorts/sim_{video_path.stem}",
            "is_simulation": True
        }

    # If real credentials exist, execute actual Google API client upload
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_file(str(token_file))
        youtube = build("youtube", "v3", credentials=creds)

        body = {
            "snippet": {
                "title": title[:100],
                "description": description,
                "tags": tag_list,
                "categoryId": "22"  # People & Blogs
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False
            }
        }

        media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = request.execute()

        video_id = response.get("id")
        return {
            "status": "published",
            "remote_id": video_id,
            "remote_url": f"https://youtube.com/shorts/{video_id}",
            "is_simulation": False
        }

    except Exception as e:
        logger.error("Real YouTube upload failed: %s", e)
        raise
