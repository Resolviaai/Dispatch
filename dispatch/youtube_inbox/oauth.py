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
SESSION_ID_REGEX = re.compile(r"session_id[:=]\s*([a-zA-Z0-9_-]+)", re.IGNORECASE)
SEGMENT_ID_REGEX = re.compile(r"segment_id[:=]\s*([a-zA-Z0-9_-]+)", re.IGNORECASE)
SEQUENCE_NUM_REGEX = re.compile(r"sequence_number[:=]\s*(\d+)", re.IGNORECASE)


def extract_session_metadata(description: str, tags: Optional[List[str]] = None) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    """Extract session_id, segment_id, sequence_number from description or tags."""
    session_id = None
    segment_id = None
    sequence_number = None

    if description:
        m_sess = SESSION_ID_REGEX.search(description)
        if m_sess:
            session_id = m_sess.group(1).strip()
        m_seg = SEGMENT_ID_REGEX.search(description)
        if m_seg:
            segment_id = m_seg.group(1).strip()
        m_seq = SEQUENCE_NUM_REGEX.search(description)
        if m_seq:
            try:
                sequence_number = int(m_seq.group(1).strip())
            except ValueError:
                pass

    if tags:
        for t in tags:
            t_clean = t.strip()
            if t_clean.lower().startswith("dispatch_session_"):
                if not session_id:
                    session_id = t_clean[len("dispatch_session_"):].strip()
            elif t_clean.lower().startswith("dispatch_seg_"):
                if sequence_number is None:
                    try:
                        sequence_number = int(t_clean[len("dispatch_seg_"):].strip())
                    except ValueError:
                        pass
            elif t_clean.lower().startswith("dispatch_id_"):
                if not segment_id:
                    segment_id = t_clean[len("dispatch_id_"):].strip()

    return session_id, segment_id, sequence_number


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


from dispatch.config import ROOT_DIR, YOUTUBE_TOKEN_PATH

def get_youtube_service(token_path: Optional[Path] = None):
    """Return authenticated YouTube Data API v3 service resource if credentials exist."""
    candidates = [
        token_path,
        Path(os.getenv("DISPATCH_YOUTUBE_TOKEN_PATH", str(YOUTUBE_TOKEN_PATH))),
        YOUTUBE_TOKEN_PATH,
        ROOT_DIR / "youtube_token.json",
        Path("youtube_token.json"),
    ]

    token_file = None
    for c in candidates:
        if c and c.exists():
            token_file = c
            break

    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request as GoogleAuthRequest

        if token_file and token_file.exists():
            creds = Credentials.from_authorized_user_file(str(token_file))
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(GoogleAuthRequest())
                    if hasattr(creds, "to_json"):
                        with open(token_file, "w", encoding="utf-8") as f:
                            f.write(creds.to_json())
                        logger.info("Refreshed and persisted YouTube OAuth token to %s", token_file)
                except Exception as refresh_err:
                    logger.warning("Failed to refresh YouTube OAuth token: %s", refresh_err)
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
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(GoogleAuthRequest())
                except Exception as refresh_err:
                    logger.warning("Failed to refresh env-based YouTube OAuth token: %s", refresh_err)
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
                session_id, segment_id, sequence_number = extract_session_metadata(description, tags)
                if not dispatch_id and segment_id:
                    dispatch_id = segment_id
                if not dispatch_id and session_id:
                    seq_str = f"{sequence_number:04d}" if sequence_number is not None else "0001"
                    dispatch_id = f"{session_id}_seg_{seq_str}"

                # Check if this is a Dispatch upload:
                # Matches if dispatch_id or session_id is present, OR "[DISPATCH]" is in title, OR "dispatch" in tags
                is_dispatch = (
                    dispatch_id is not None
                    or session_id is not None
                    or "[dispatch]" in title.lower()
                    or any("dispatch" in t.lower() for t in tags)
                )

                if is_dispatch:
                    if not dispatch_id:
                        dispatch_id = f"dsp_{published_at[:10]}_{video_id[:6]}"

                    discovered.append({
                        "video_id": video_id,
                        "dispatch_id": dispatch_id,
                        "session_id": session_id,
                        "sequence_number": sequence_number,
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


def get_youtube_processing_status(video_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Query authoritative YouTube Data API v3 for uploadStatus, processingStatus,
    failure reasons, and content duration for a list of video IDs.

    Batches up to 50 video IDs per request to respect quota and network efficiency.
    Returns a dict mapping video_id to structured metadata:
        {
            "video_id": str,
            "upload_status": str,       # 'uploaded', 'processed', 'failed', 'rejected', 'deleted'
            "processing_status": str,   # 'processing', 'succeeded', 'failed', 'terminated', or None
            "failure_reason": str|None, # 'transcodingFailed', 'uploadFailed', 'other', or None
            "duration": str,            # ISO 8601 string e.g. 'PT56S', 'P0D'
            "title": str,
            "published_at": str,
            "privacy_status": str
        }
    """
    if not video_ids:
        return {}

    service = get_youtube_service()
    if not service:
        logger.debug("YouTube API service unavailable; skipping authoritative status query.")
        return {}

    results: Dict[str, Dict[str, Any]] = {}
    batch_size = 50

    for i in range(0, len(video_ids), batch_size):
        chunk = video_ids[i:i + batch_size]
        try:
            resp = service.videos().list(
                part="snippet,status,processingDetails,contentDetails",
                id=",".join(chunk)
            ).execute()

            for item in resp.get("items", []):
                vid = item.get("id")
                if not vid:
                    continue
                st = item.get("status", {})
                pr = item.get("processingDetails", {})
                cd = item.get("contentDetails", {})
                snip = item.get("snippet", {})

                results[vid] = {
                    "video_id": vid,
                    "upload_status": st.get("uploadStatus"),
                    "processing_status": pr.get("processingStatus"),
                    "failure_reason": pr.get("processingFailureReason") or st.get("rejectionReason"),
                    "duration": cd.get("duration"),
                    "title": snip.get("title", ""),
                    "published_at": snip.get("publishedAt", ""),
                    "privacy_status": st.get("privacyStatus", "unlisted")
                }
        except Exception as e:
            logger.warning("Error querying authoritative YouTube video status for batch %s: %s", chunk, e)

    return results
