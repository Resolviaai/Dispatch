"""YouTube Shorts publishing adapter using YouTube Data API v3."""
import os
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from dispatch.youtube_connections import atomic_write_token_file as _atomic_write_token_file

logger = logging.getLogger("dispatch.publisher.youtube")


def _is_revoked_refresh_error(err: Exception) -> bool:
    msg = str(err).lower()
    return any(tok in msg for tok in ("invalid_grant", "revoked", "expired or revoked", "unauthorized_client"))


def _credentials_from_data(credentials_data: Dict[str, Any]):
    """Build Google OAuth2 Credentials from either short-lived access_token or local refresh_token."""
    from google.oauth2.credentials import Credentials

    if credentials_data.get("needs_reauth"):
        raise PermissionError(
            str(credentials_data.get("error") or "YouTube account authorization expired or revoked; reconnect required.")
        )
    ref_tok = credentials_data.get("refresh_token") or credentials_data.get("token")
    acc_tok = credentials_data.get("access_token")
    # Short-lived brokered credentials carry access_token only (never refresh_token / client_secret).
    if acc_tok and not ref_tok:
        return Credentials(token=acc_tok)
    if ref_tok and acc_tok and ref_tok == acc_tok and credentials_data.get("provider"):
        return Credentials(token=acc_tok)
    return Credentials(
        token=acc_tok,
        refresh_token=ref_tok,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("YOUTUBE_CLIENT_ID"),
        client_secret=os.getenv("YOUTUBE_CLIENT_SECRET"),
    )


DEFAULT_YOUTUBE_TIMEOUT_SECONDS = 120.0
YOUTUBE_RESUMABLE_CHUNK_BYTES = 8 * 1024 * 1024  # 8 MiB (multiple of 256 KiB)


def _is_youtube_auth_http_error(err: Exception) -> bool:
    status = getattr(getattr(err, "resp", None), "status", None)
    if status == 401:
        return True
    msg = str(err).lower()
    if status == 403 and any(
        tok in msg
        for tok in (
            "invalidcredentials",
            "autherror",
            "insufficientpermissions",
            "unauthorized",
            "forbidden",
            "accessnotconfigured",
        )
    ):
        return True
    return _is_revoked_refresh_error(err)


def build_youtube_service_from_credentials(
    credentials_data: Dict[str, Any],
    timeout_seconds: Optional[float] = DEFAULT_YOUTUBE_TIMEOUT_SECONDS,
):
    """Build a Google YouTube v3 Resource object from credentials_data dict."""
    from googleapiclient.discovery import build
    from google.auth.transport.requests import Request as GoogleAuthRequest

    creds = _credentials_from_data(credentials_data)
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleAuthRequest())
        except Exception as refresh_err:
            if _is_revoked_refresh_error(refresh_err):
                raise PermissionError(f"YouTube OAuth token expired or revoked: {refresh_err}") from refresh_err
            logger.warning("Failed to refresh account-specific YouTube OAuth token: %s", refresh_err)
    if timeout_seconds is not None:
        import httplib2
        from google_auth_httplib2 import AuthorizedHttp
        authorized_http = AuthorizedHttp(creds, http=httplib2.Http(timeout=timeout_seconds))
        return build("youtube", "v3", http=authorized_http)
    return build("youtube", "v3", credentials=creds)


def upload_youtube_short(
    video_path: Path,
    title: str,
    description: str,
    tags: str,
    privacy_status: str = "private",
    credentials_path: Optional[Path] = None,
    credentials_data: Optional[Dict[str, Any]] = None,
    timeout_seconds: float = DEFAULT_YOUTUBE_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Upload video to YouTube as a Short.
    
    Args:
        video_path: Local path to .mp4 video file.
        title: Title of the Short (must include #Shorts).
        description: Description text.
        tags: Comma-separated or hashtag-separated tags.
        privacy_status: 'private', 'unlisted', or 'public'.
        credentials_path: Optional path to OAuth client credentials.
        credentials_data: Optional brokered short-lived credentials dict.
        timeout_seconds: Bounded socket timeout for YouTube API calls.
        
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
    from dispatch.config import ROOT_DIR, YOUTUBE_TOKEN_PATH

    if credentials_path is not None:
        # Caller explicitly requested a specific credentials file — honour that contract strictly.
        # Do NOT silently fall back to the system-wide token when a specific path was requested.
        if credentials_path.exists():
            token_file = credentials_path
        else:
            token_file = None
    else:
        # Auto-discovery: scan well-known locations
        env_token_path = os.getenv("DISPATCH_YOUTUBE_TOKEN_PATH")
        candidates = [
            Path(env_token_path) if env_token_path else None,
            YOUTUBE_TOKEN_PATH,
            ROOT_DIR / "youtube_token.json",
            Path("youtube_token.json"),
        ]
        token_file = None
        for c in candidates:
            if c and c.exists():
                token_file = c
                break

    has_account_creds = bool(credentials_data and (credentials_data.get("refresh_token") or credentials_data.get("token") or credentials_data.get("access_token")))

    if not token_file and not os.getenv("YOUTUBE_ACCESS_TOKEN") and not has_account_creds:
        raise PermissionError("YouTube OAuth credentials missing (youtube_token.json). Please authenticate via Settings.")

    # If real credentials exist, execute actual Google API client upload
    try:
        import httplib2
        from google_auth_httplib2 import AuthorizedHttp
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request as GoogleAuthRequest

        if has_account_creds and credentials_data:
            creds = _credentials_from_data(credentials_data)
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(GoogleAuthRequest())
                except Exception as refresh_err:
                    if _is_revoked_refresh_error(refresh_err):
                        raise PermissionError(f"YouTube OAuth token expired or revoked: {refresh_err}") from refresh_err
                    logger.warning("Failed to refresh account-specific YouTube OAuth token: %s", refresh_err)
        elif token_file and token_file.exists():
            creds = Credentials.from_authorized_user_file(str(token_file))
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(GoogleAuthRequest())
                    if hasattr(creds, "to_json"):
                        _atomic_write_token_file(token_file, creds.to_json())
                        logger.info("Refreshed and persisted YouTube OAuth token in publisher to %s", token_file)
                except Exception as refresh_err:
                    if _is_revoked_refresh_error(refresh_err):
                        raise PermissionError(f"YouTube OAuth token expired or revoked: {refresh_err}") from refresh_err
                    logger.warning("Failed to refresh YouTube OAuth token in publisher: %s", refresh_err)
        else:
            creds = Credentials(
                token=os.getenv("YOUTUBE_ACCESS_TOKEN"),
                refresh_token=os.getenv("YOUTUBE_REFRESH_TOKEN"),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=os.getenv("YOUTUBE_CLIENT_ID"),
                client_secret=os.getenv("YOUTUBE_CLIENT_SECRET")
            )
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(GoogleAuthRequest())
                except Exception as refresh_err:
                    if _is_revoked_refresh_error(refresh_err):
                        raise PermissionError(f"YouTube OAuth token expired or revoked: {refresh_err}") from refresh_err
                    logger.warning("Failed to refresh env-based token in publisher: %s", refresh_err)

        authorized_http = AuthorizedHttp(creds, http=httplib2.Http(timeout=timeout_seconds))
        youtube = build("youtube", "v3", http=authorized_http)

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

        media = MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            chunksize=YOUTUBE_RESUMABLE_CHUNK_BYTES,
            resumable=True,
        )
        response = None
        try:
            request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
            if hasattr(request, "next_chunk"):
                transient_retries = 0
                while response is None:
                    try:
                        _, response = request.next_chunk()
                    except HttpError as chunk_err:
                        if _is_youtube_auth_http_error(chunk_err):
                            raise PermissionError(f"YouTube upload authorization expired or revoked: {chunk_err}") from chunk_err
                        status_code = getattr(getattr(chunk_err, "resp", None), "status", None)
                        if status_code in (500, 502, 503, 504) and transient_retries < 3:
                            transient_retries += 1
                            continue
                        raise
            else:
                response = request.execute()
        finally:
            fd = getattr(media, "_fd", None)
            if fd is not None and hasattr(fd, "close"):
                try:
                    fd.close()
                except Exception:
                    pass

        video_id = (response or {}).get("id")
        if not video_id:
            raise RuntimeError("YouTube upload completed without returning a video ID")

        # Check YouTube Data API processing status before declaring completion
        upload_status = (response or {}).get("status", {}).get("uploadStatus", "uploaded")
        try:
            status_resp = youtube.videos().list(part="status,processingDetails", id=video_id).execute()
            items = status_resp.get("items", [])
            if items:
                upload_status = items[0].get("status", {}).get("uploadStatus", upload_status)
        except Exception as check_err:
            if _is_youtube_auth_http_error(check_err):
                raise PermissionError(f"YouTube status check authorization error: {check_err}") from check_err
            logger.debug("Failed to query initial processing status for %s: %s", video_id, check_err)

        if upload_status in ("failed", "rejected"):
            raise RuntimeError(f"YouTube rejected uploaded video {video_id}: {upload_status}")

        return {
            "status": "published" if upload_status == "processed" else "processing",
            "upload_status": upload_status,
            "remote_id": video_id,
            "remote_url": f"https://youtube.com/shorts/{video_id}",
            "is_simulation": False
        }

    except PermissionError:
        raise
    except Exception as e:
        if _is_youtube_auth_http_error(e):
            raise PermissionError(f"YouTube upload authorization expired or revoked: {e}") from e
        logger.error("Real YouTube upload failed: %s", e)
        raise

def get_authenticated_youtube_service(
    account_id: Optional[str] = None,
    user_id: Optional[str] = None,
    credentials_path: Optional[Path] = None,
    credentials_data: Optional[Dict[str, Any]] = None,
    timeout_seconds: Optional[float] = None,
):
    """Retrieve an authorized YouTube Data API v3 client.
    
    Tries credentials in order:
    1. Direct credentials_data dict
    2. Account-specific credentials from Supabase vault via account_id / user_id
    3. User refresh token from Supabase vault via user_id
    4. Local OAuth token file (youtube_token.json)
    5. Environment variables (YOUTUBE_ACCESS_TOKEN / YOUTUBE_REFRESH_TOKEN)
    """
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from dispatch.config import ROOT_DIR, YOUTUBE_TOKEN_PATH

    creds = None
    refreshed_token_file: Optional[Path] = None

    # 1. Direct credentials data
    if credentials_data and (credentials_data.get("needs_reauth") or credentials_data.get("refresh_token") or credentials_data.get("token") or credentials_data.get("access_token")):
        creds = _credentials_from_data(credentials_data)

    # 2. Account-specific Worker broker lookup (short-lived access token only)
    if not creds and account_id:
        try:
            from dispatch.youtube_connections import get_account_credentials
            acc_creds = get_account_credentials(account_id, user_id=user_id, provider="youtube")
            if acc_creds and acc_creds.get("needs_reauth"):
                raise PermissionError(
                    str(acc_creds.get("error") or "YouTube account authorization revoked; reconnect required.")
                )
            if acc_creds and (acc_creds.get("access_token") or acc_creds.get("refresh_token") or acc_creds.get("token")):
                creds = _credentials_from_data(acc_creds)
        except PermissionError:
            raise
        except Exception as e:
            logger.debug("Failed to resolve account credentials for %s: %s", account_id, e)

    # 3. User-default Worker access-token broker lookup (no raw refresh_token on PC)
    if not creds and user_id:
        try:
            from dispatch.youtube_connections import get_user_youtube_access_token
            user_tok = get_user_youtube_access_token(user_id)
            if user_tok and user_tok.get("needs_reauth"):
                raise PermissionError(
                    str(user_tok.get("error") or "YouTube account authorization revoked; reconnect required.")
                )
            if user_tok and user_tok.get("access_token"):
                creds = _credentials_from_data(user_tok)
        except PermissionError:
            raise
        except Exception as e:
            logger.debug("Failed to resolve user access token for %s: %s", user_id, e)

    # 4. Local file token lookup
    if not creds:
        token_file = None
        if credentials_path and credentials_path.exists():
            token_file = credentials_path
        else:
            env_token_path = os.getenv("DISPATCH_YOUTUBE_TOKEN_PATH")
            candidates = [
                Path(env_token_path) if env_token_path else None,
                YOUTUBE_TOKEN_PATH,
                ROOT_DIR / "youtube_token.json",
                Path("youtube_token.json"),
            ]
            for c in candidates:
                if c and c.exists():
                    token_file = c
                    break
        if token_file and token_file.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(token_file))
                refreshed_token_file = token_file
            except Exception as e:
                logger.debug("Failed to load local token file %s: %s", token_file, e)

    # 5. Environment tokens
    if not creds and (os.getenv("YOUTUBE_REFRESH_TOKEN") or os.getenv("YOUTUBE_ACCESS_TOKEN")):
        creds = Credentials(
            token=os.getenv("YOUTUBE_ACCESS_TOKEN"),
            refresh_token=os.getenv("YOUTUBE_REFRESH_TOKEN"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.getenv("YOUTUBE_CLIENT_ID"),
            client_secret=os.getenv("YOUTUBE_CLIENT_SECRET")
        )

    if not creds:
        return None

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleAuthRequest())
            if refreshed_token_file is not None and hasattr(creds, "to_json"):
                _atomic_write_token_file(refreshed_token_file, creds.to_json())
        except Exception as refresh_err:
            if _is_revoked_refresh_error(refresh_err):
                raise PermissionError(f"YouTube OAuth token expired or revoked: {refresh_err}") from refresh_err
            logger.warning("Failed to refresh YouTube OAuth token: %s", refresh_err)

    if timeout_seconds is not None:
        import httplib2
        from google_auth_httplib2 import AuthorizedHttp
        authorized_http = AuthorizedHttp(creds, http=httplib2.Http(timeout=timeout_seconds))
        return build("youtube", "v3", http=authorized_http)

    return build("youtube", "v3", credentials=creds)


def update_youtube_video_snippet(
    video_id: str,
    title: str,
    description: str,
    account_id: Optional[str] = None,
    user_id: Optional[str] = None,
    credentials_data: Optional[Dict[str, Any]] = None,
    youtube_service=None,
) -> bool:
    """Update title and description on an existing YouTube video.
    
    Preserves all existing tags, categoryId, and localized audio/language fields.
    Returns True on success, False on failure. Never raises an unhandled exception.
    """
    try:
        service = youtube_service or get_authenticated_youtube_service(
            account_id=account_id,
            user_id=user_id,
            credentials_data=credentials_data,
        )
        if not service:
            logger.warning("No authenticated YouTube service available to update video %s", video_id)
            return False

        resp = service.videos().list(part="snippet", id=video_id).execute()
        items = resp.get("items", [])
        if not items:
            logger.warning("YouTube video %s not found for snippet update", video_id)
            return False

        snippet = items[0].get("snippet", {})
        snippet["title"] = title[:100]
        snippet["description"] = description[:5000]
        if not snippet.get("categoryId"):
            snippet["categoryId"] = "22"

        service.videos().update(
            part="snippet",
            body={
                "id": video_id,
                "snippet": snippet
            }
        ).execute()

        logger.info("Successfully updated YouTube video %s snippet (Title: '%s')", video_id, snippet["title"])
        return True
    except Exception as e:
        logger.warning("Failed to update YouTube video %s snippet: %s", video_id, e)
        return False

