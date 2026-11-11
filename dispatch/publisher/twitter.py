"""X (formerly Twitter) video publishing adapter using X API v2 and v1.1 Media Upload."""
import os
import time
import logging
import requests
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("dispatch.publisher.twitter")

X_APPEND_CHUNK_BYTES = 5 * 1024 * 1024  # 5 MiB chunk size


def _parse_json_safe(resp: requests.Response) -> Dict[str, Any]:
    try:
        if not getattr(resp, "content", None) and not getattr(resp, "text", None):
            return {}
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _raise_for_x_auth_or_status(resp: requests.Response, context: str) -> None:
    if resp.status_code in (401, 403):
        data = _parse_json_safe(resp)
        msg = str(data.get("detail") or data.get("title") or data.get("error") or resp.text or f"HTTP {resp.status_code}")[:240]
        raise PermissionError(
            f"X authorization expired or insufficient permissions during {context} ({resp.status_code}): {msg}"
        )
    resp.raise_for_status()


def _extract_media_id(payload: Dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    raw = data.get("id") or payload.get("media_id_string") or payload.get("media_id")
    return str(raw).strip() if raw is not None else ""


def upload_x_video(
    video_path: Path,
    text: str,
    bearer_token: Optional[str] = None,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
    oauth_token: Optional[str] = None,
    oauth_secret: Optional[str] = None,
    poll_interval_seconds: float = 2.0,
) -> Dict[str, Any]:
    """Publish short-form video tweet to X / Twitter.
    
    Supports both OAuth 2.0 User Access Tokens (Bearer) via X API v2 media
    upload endpoints and OAuth 1.0a credentials with clean fallback.
    """
    token = bearer_token or os.getenv("X_BEARER_TOKEN") or os.getenv("TWITTER_BEARER_TOKEN")
    key = api_key or os.getenv("X_API_KEY")
    secret = api_secret or os.getenv("X_API_SECRET")
    o_tok = oauth_token or os.getenv("X_ACCESS_TOKEN")
    o_sec = oauth_secret or os.getenv("X_ACCESS_SECRET")

    has_oauth1 = bool(key and secret and o_tok and o_sec)
    if not token and not has_oauth1:
        raise PermissionError("X / Twitter API credentials missing (X_BEARER_TOKEN or OAuth 1.0a credentials).")

    if not video_path.exists():
        raise FileNotFoundError(f"Video file missing: {video_path}")

    file_size = video_path.stat().st_size
    if file_size <= 0:
        raise ValueError(f"X video upload refused: video file is empty ({video_path})")

    oauth = None
    if has_oauth1:
        from requests_oauthlib import OAuth1
        oauth = OAuth1(key, secret, o_tok, o_sec)

    # Prefer user-context OAuth2 Bearer when provided without OAuth1; if OAuth1 is available and Bearer fails, fallback safely.
    bearer_headers = {"Authorization": f"Bearer {token}"} if token else {}
    use_bearer = bool(token)
    auth_kwargs: Dict[str, Any] = {"headers": bearer_headers} if use_bearer else {"auth": oauth}

    try:
        mode = "v2_rest" if use_bearer else "v1"
        media_id = ""

        # Step 1: INIT
        if mode == "v2_rest":
            init_res = requests.post(
                "https://api.x.com/2/media/upload/initialize",
                headers={**bearer_headers, "Content-Type": "application/json"},
                json={
                    "total_bytes": file_size,
                    "media_type": "video/mp4",
                    "media_category": "tweet_video",
                },
                timeout=(15, 30),
            )
            if init_res.status_code in (401, 403):
                if oauth is not None:
                    mode = "v1"
                    auth_kwargs = {"auth": oauth}
                else:
                    _raise_for_x_auth_or_status(init_res, "media INIT")
            elif init_res.status_code in (404, 405):
                mode = "v2_command"
            else:
                _raise_for_x_auth_or_status(init_res, "media INIT")
                media_id = _extract_media_id(_parse_json_safe(init_res))

        if not media_id and mode in ("v2_command", "v1"):
            cmd_url = (
                "https://api.x.com/2/media/upload"
                if mode == "v2_command"
                else "https://upload.twitter.com/1.1/media/upload.json"
            )
            cmd_auth = {"headers": bearer_headers} if (mode == "v2_command" and use_bearer) else {"auth": oauth}
            init_res = requests.post(
                cmd_url,
                **cmd_auth,
                data={
                    "command": "INIT",
                    "media_type": "video/mp4",
                    "total_bytes": str(file_size),
                    "media_category": "tweet_video",
                },
                timeout=(15, 30),
            )
            if init_res.status_code in (404, 405) and mode == "v2_command" and oauth is not None:
                mode = "v1"
                init_res = requests.post(
                    "https://upload.twitter.com/1.1/media/upload.json",
                    auth=oauth,
                    data={
                        "command": "INIT",
                        "media_type": "video/mp4",
                        "total_bytes": str(file_size),
                        "media_category": "tweet_video",
                    },
                    timeout=(15, 30),
                )
            _raise_for_x_auth_or_status(init_res, "media INIT")
            media_id = _extract_media_id(_parse_json_safe(init_res))
            auth_kwargs = {"headers": bearer_headers} if (mode != "v1" and use_bearer) else {"auth": oauth}

        if not media_id:
            raise ValueError("X media INIT did not return a media_id")

        # Step 2: APPEND chunks (5 MiB slices)
        segment_id = 0
        append_url = (
            f"https://api.x.com/2/media/upload/{media_id}/append"
            if mode == "v2_rest"
            else (
                "https://api.x.com/2/media/upload"
                if mode == "v2_command"
                else "https://upload.twitter.com/1.1/media/upload.json"
            )
        )
        with open(video_path, "rb") as f:
            while True:
                chunk = f.read(X_APPEND_CHUNK_BYTES)
                if not chunk:
                    break
                append_data = (
                    {"segment_index": str(segment_id)}
                    if mode == "v2_rest"
                    else {"command": "APPEND", "media_id": media_id, "segment_index": str(segment_id)}
                )
                append_res = requests.post(
                    append_url,
                    **auth_kwargs,
                    data=append_data,
                    files={"media": ("clip.mp4", chunk, "video/mp4")},
                    timeout=(15, 60),
                )
                _raise_for_x_auth_or_status(append_res, f"media APPEND segment {segment_id}")
                segment_id += 1

        # Step 3: FINALIZE
        fin_url = (
            f"https://api.x.com/2/media/upload/{media_id}/finalize"
            if mode == "v2_rest"
            else (
                "https://api.x.com/2/media/upload"
                if mode == "v2_command"
                else "https://upload.twitter.com/1.1/media/upload.json"
            )
        )
        fin_kwargs = dict(auth_kwargs)
        if mode != "v2_rest":
            fin_kwargs["data"] = {"command": "FINALIZE", "media_id": media_id}
        fin_res = requests.post(fin_url, **fin_kwargs, timeout=(15, 30))
        _raise_for_x_auth_or_status(fin_res, "media FINALIZE")
        fin_json = _parse_json_safe(fin_res)

        # Step 3b: Poll processing_info STATUS for async video transcoding
        proc_info = fin_json.get("processing_info") or (fin_json.get("data") or {}).get("processing_info")
        status_base = (
            "https://upload.twitter.com/1.1/media/upload.json"
            if mode == "v1"
            else "https://api.x.com/2/media/upload"
        )
        for _ in range(20):
            if not isinstance(proc_info, dict):
                break
            state = str(proc_info.get("state") or "").lower()
            if state == "succeeded":
                break
            if state == "failed":
                err_detail = (proc_info.get("error") or {}).get("message") or "processing failed"
                raise RuntimeError(f"X video processing failed: {err_detail}")
            if state not in ("pending", "in_progress"):
                break
            wait_secs = float(proc_info.get("check_after_secs") or 2.0)
            sleep_for = min(poll_interval_seconds, wait_secs) if poll_interval_seconds > 0 else 0.0
            if sleep_for > 0:
                time.sleep(sleep_for)
            st_res = requests.get(
                status_base,
                **auth_kwargs,
                params={"command": "STATUS", "media_id": media_id},
                timeout=(10, 15),
            )
            _raise_for_x_auth_or_status(st_res, "media STATUS")
            st_json = _parse_json_safe(st_res)
            proc_info = st_json.get("processing_info") or (st_json.get("data") or {}).get("processing_info")
        else:
            if isinstance(proc_info, dict) and str(proc_info.get("state") or "").lower() in ("pending", "in_progress"):
                raise TimeoutError(f"X media {media_id} timed out processing")

        # Step 4: Post Tweet via v2
        tweet_url = "https://api.twitter.com/2/tweets"
        tweet_body = {
            "text": text[:280],
            "media": {"media_ids": [media_id]},
        }
        tweet_auth = {"headers": bearer_headers} if use_bearer else {"auth": oauth}
        tweet_res = requests.post(tweet_url, **tweet_auth, json=tweet_body, timeout=(15, 30))
        _raise_for_x_auth_or_status(tweet_res, "create tweet")
        tweet_id = _parse_json_safe(tweet_res).get("data", {}).get("id") or media_id
        permalink = f"https://x.com/i/status/{tweet_id}"

        logger.info("Successfully published video to X: %s", permalink)
        return {
            "status": "published",
            "remote_id": tweet_id,
            "remote_url": permalink,
            "is_simulation": False,
        }

    except Exception as e:
        logger.error("X video publish failed: %s", e)
        raise
