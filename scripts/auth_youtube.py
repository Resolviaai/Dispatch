"""Run the desktop YouTube OAuth flow and report safe status updates to Dispatch."""
import json
import os
import socket
import sys
import tempfile
import urllib.error
from pathlib import Path
from typing import Any, Dict, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
CLIENT_SECRETS_FILE = ROOT_DIR / "client_secrets.json"
TOKEN_FILE = ROOT_DIR / "youtube_token.json"
STATE_PREFIX = "DISPATCH_OAUTH_STATE="

SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def emit_state(status: str, message: str, **details: Any) -> None:
    """Emit one machine-readable, non-secret state line for the parent process."""
    payload: Dict[str, Any] = {"status": status, "message": message}
    payload.update(details)
    print(STATE_PREFIX + json.dumps(payload, separators=(",", ":")), flush=True)


def _is_network_error(error: BaseException) -> bool:
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, (socket.gaierror, TimeoutError, ConnectionError, urllib.error.URLError)):
            return True
        reason = getattr(current, "reason", None)
        if isinstance(reason, BaseException):
            pending.append(reason)
        for nested in (current.__cause__, current.__context__):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return False


def _save_token_atomically(token_json: str) -> None:
    """Write a completed token without replacing an existing token on partial failure."""
    fd, temp_name = tempfile.mkstemp(prefix="youtube_token_", suffix=".tmp", dir=ROOT_DIR)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as token_file:
            token_file.write(token_json)
            token_file.flush()
            os.fsync(token_file.fileno())
        os.replace(temp_path, TOKEN_FILE)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def run_authentication() -> Optional[Dict[str, str]]:
    """Complete OAuth, verify the resulting token against YouTube, then save it."""
    if not CLIENT_SECRETS_FILE.exists():
        raise FileNotFoundError("client_secrets.json is missing from the Dispatch folder.")

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as error:
        raise RuntimeError(
            "The active Dispatch Python environment is missing Google OAuth packages. "
            "Run python -m pip install -r requirements.txt with the same Python executable."
        ) from error

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_FILE), scopes=SCOPES)
    emit_state("awaiting_authorization", "Complete Google sign-in in the browser opened by Dispatch.")
    credentials = flow.run_local_server(
        port=0,
        open_browser=True,
        access_type="offline",
        prompt="consent",
    )

    if not credentials.refresh_token:
        raise RuntimeError("Google did not return a refresh token. Revoke Dispatch access and try again.")

    service = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    response = service.channels().list(part="snippet", mine=True, maxResults=1).execute()
    channels = response.get("items", [])
    if not channels:
        raise RuntimeError("Google authorization succeeded, but no YouTube channel was returned.")

    _save_token_atomically(credentials.to_json())
    channel = channels[0]
    return {
        "channel_title": str(channel.get("snippet", {}).get("title", "")),
        "channel_id": str(channel.get("id", "")),
    }


def main() -> int:
    emit_state("starting", "Starting Google authorization.")
    try:
        channel = run_authentication()
        emit_state("authenticated", "YouTube account connected and verified.", **(channel or {}))
        print("YouTube OAuth completed and verified.", flush=True)
        return 0
    except Exception as error:
        if _is_network_error(error):
            message = "Google could not be reached due to a DNS or network error. Check the PC connection and retry."
        elif isinstance(error, FileNotFoundError):
            message = str(error)
        elif isinstance(error, RuntimeError):
            message = str(error)
        else:
            message = "Google authorization failed. Check the OAuth client setup and retry."
        emit_state("failed", message)
        print("YouTube OAuth failed: " + message, file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
