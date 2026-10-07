"""YouTube Data API v3 OAuth and Authenticated Ingestion Helper.
Provides access to authenticated YouTube channels, listing private/unlisted uploads,
and discovering videos created by the Dispatch phone app.
"""
import os
import re
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("dispatch.youtube_inbox.oauth")

# Regex to extract dispatch_id from YouTube description or tags
DISPATCH_ID_REGEX = re.compile(r"dispatch_id[:=]\s*([a-zA-Z0-9_-]+)", re.IGNORECASE)


def extract_dispatch_id(description: str, tags: Optional[List[str]] = None) -> Optional[str]:
    """Extract dispatch_id from description or tags if present."""
    if description:
        match = DISPATCH_ID_REGEX.search(description)
        if match:
            return match.group(1).strip()

    if tags:
        for t in tags:
            if t.lower().startswith("dispatch_id_"):
                return t[len("dispatch_id_"):].strip()
            match = DISPATCH_ID_REGEX.search(t)
            if match:
                return match.group(1).strip()

    return None


def get_youtube_service(token_path: Optional[Path] = None):
    """Return authenticated YouTube Data API v3 service resource if credentials exist."""
    candidates = [
        token_path,
        Path("youtube_token.json"),
        Path(os.getenv("DISPATCH_YOUTUBE_TOKEN_PATH", "youtube_token.json")),
    ]

    token_file = None
    for c in candidates:
        if c and c.exists():
            token_file = c
            break

    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials

        if token_file and token_file.exists():
            creds = Credentials.from_authorized_user_file(str(token_file))
            return build("youtube", "v3", credentials=creds)

        # Check environment variables
        access_token = os.getenv("YOUTUBE_ACCESS_TOKEN")
        refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN")
        client_id = os.getenv("YOUTUBE_CLIENT_ID")
        client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")

        if refresh_token and client_id and client_secret:
            creds = Credentials(
                token=access_token,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret
            )
            return build("youtube", "v3", credentials=creds)

    except Exception as e:
        logger.debug("YouTube API service not initialized: %s", e)

    return None


def list_authenticated_user_uploads(
    max_results_per_page: int = 50,
    stop_on_known_page: bool = True
) -> List[Dict[str, Any]]:
    """Query YouTube Data API v3 for the authenticated user's uploads (including Unlisted and Private).
    Paginates dynamically until nextPageToken is exhausted OR until an entire page consists of
    already-ingested videos (early-stop to avoid wasteful API quota consumption).
    Returns list of discovered videos with metadata and extracted dispatch_id.
    """
    from dispatch import db

    service = get_youtube_service()
    if not service:
        return []

    discovered = []
    try:
        # 1. Fetch channel's uploads playlist ID
        channel_resp = service.channels().list(mine=True, part="contentDetails").execute()
        items = channel_resp.get("items", [])
        if not items:
            return []

        uploads_playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        # 2. Query uploads playlist with pageToken pagination
        page_token = None
        per_page = min(max_results_per_page, 50)

        while True:
            kwargs = {
                "playlistId": uploads_playlist_id,
                "part": "snippet,status",
                "maxResults": per_page
            }
            if page_token:
                kwargs["pageToken"] = page_token

            playlist_resp = service.playlistItems().list(**kwargs).execute()
            page_items = playlist_resp.get("items", [])
            if not page_items:
                break

            page_has_new_item = False

            for item in page_items:
                snippet = item.get("snippet", {})
                video_id = snippet.get("resourceId", {}).get("videoId")
                if not video_id:
                    continue

                title = snippet.get("title", "")
                description = snippet.get("description", "")
                tags = snippet.get("tags", [])
                published_at = snippet.get("publishedAt", "")
                privacy = item.get("status", {}).get("privacyStatus", "unlisted")

                dispatch_id = extract_dispatch_id(description, tags)

                # Check if this is a Dispatch upload:
                # Matches if dispatch_id is present, OR "[DISPATCH]" is in title, OR "dispatch" in tags
                is_dispatch = (
                    dispatch_id is not None
                    or "[dispatch]" in title.lower()
                    or any("dispatch" in t.lower() for t in tags)
                )

                if is_dispatch:
                    if not dispatch_id:
                        dispatch_id = f"dsp_{published_at[:10]}_{video_id[:6]}"

                    discovered.append({
                        "video_id": video_id,
                        "dispatch_id": dispatch_id,
                        "title": title,
                        "description": description,
                        "published_at": published_at,
                        "privacy_status": privacy
                    })

                    # Check if this video is already ingested/completed in the database
                    if not db.is_youtube_video_processed(video_id):
                        page_has_new_item = True

            # Early-stop optimization: If a full page was inspected and contained ONLY already-processed
            # videos, stop paging further back in history.
            if stop_on_known_page and not page_has_new_item and len(page_items) >= per_page:
                logger.debug("Early-stop: Page contained only already-ingested videos. Halting pagination.")
                break

            page_token = playlist_resp.get("nextPageToken")
            if not page_token:
                break

    except Exception as e:
        logger.warning("Error querying authenticated YouTube uploads: %s", e)

    return discovered
