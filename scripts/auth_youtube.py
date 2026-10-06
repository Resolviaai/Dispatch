"""YouTube OAuth Authentication Helper.
Authenticates with YouTube Data API v3 and generates youtube_token.json for Dispatch.
"""
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
CLIENT_SECRETS_FILE = ROOT_DIR / "client_secrets.json"
TOKEN_FILE = ROOT_DIR / "youtube_token.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly"
]


def main():
    print("=" * 65)
    print("YOUTUBE OAUTH AUTHENTICATION SETUP")
    print("=" * 65)

    if not CLIENT_SECRETS_FILE.exists():
        print(f"[ERROR] '{CLIENT_SECRETS_FILE.name}' was not found in:")
        print(f"        {CLIENT_SECRETS_FILE}")
        print("\nPlease follow these steps first:")
        print("1. Go to https://console.cloud.google.com/apis/credentials")
        print("2. Create an OAuth 2.0 Client ID (Application type: Desktop App).")
        print("3. Download the JSON file and rename it to 'client_secrets.json'.")
        print(f"4. Move it to: {CLIENT_SECRETS_FILE}")
        sys.exit(1)

    sys.stdout.reconfigure(line_buffering=True)
    print(f"[1/3] Found client secrets: {CLIENT_SECRETS_FILE.name}", flush=True)
    print("[2/3] Launching local browser for Google account authorization...", flush=True)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(
            str(CLIENT_SECRETS_FILE),
            scopes=SCOPES
        )
        print("Waiting for you to complete authorization in your browser...", flush=True)
        credentials = flow.run_local_server(port=0, open_browser=True)

        # Save credentials for future sessions
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(credentials.to_json())

        print(f"[3/3] Token saved successfully: {TOKEN_FILE.resolve()}", flush=True)
        print("\n" + "=" * 65, flush=True)
        print("YOUTUBE OAUTH SETUP COMPLETED SUCCESSFULLY!", flush=True)
        print("Dispatch can now autonomously check private uploads and publish clips.", flush=True)
        print("=" * 65, flush=True)

    except Exception as e:
        print(f"\n[ERROR] Authentication failed: {e}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
