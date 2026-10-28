"""Dispatch configuration module.
Handles paths, environment variables, and system parameters.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env if present
load_dotenv()

# Root directory of the project
ROOT_DIR = Path(__file__).resolve().parent.parent

# Test environment detection & automatic sandbox isolation
# Guarantees tests run under 'python -m unittest discover -s tests' or pytest NEVER touch production database
import sys
import tempfile
import atexit
import shutil

_is_test_env = (
    "unittest" in sys.modules
    or "pytest" in sys.modules
    or any("unittest" in str(arg).lower() or "pytest" in str(arg).lower() for arg in sys.argv)
    or any(str(arg).endswith(".py") and "test" in Path(str(arg)).stem.lower() for arg in sys.argv)
)

if _is_test_env and not os.getenv("DISPATCH_STORAGE_DIR"):
    _test_sandbox_dir = tempfile.mkdtemp(prefix="dispatch_test_sandbox_")
    _sandbox_storage = Path(_test_sandbox_dir) / "storage"
    _sandbox_db_dir = _sandbox_storage / "database"
    _sandbox_db_dir.mkdir(parents=True, exist_ok=True)
    _sandbox_db_path = _sandbox_db_dir / "test_dispatch.db"

    os.environ["DISPATCH_STORAGE_DIR"] = str(_sandbox_storage)
    os.environ["DISPATCH_DB_PATH"] = str(_sandbox_db_path)
    os.environ["DISPATCH_DISABLE_AUTO_OUTBOX"] = "1"
    os.environ["DISPATCH_DISABLE_BACKGROUND_POLLERS"] = "1"

    def _cleanup_test_sandbox():
        try:
            shutil.rmtree(_test_sandbox_dir, ignore_errors=True)
        except Exception:
            pass

    atexit.register(_cleanup_test_sandbox)

# Storage directories
STORAGE_DIR = Path(os.getenv("DISPATCH_STORAGE_DIR", str(ROOT_DIR / "storage")))
INCOMING_DIR = STORAGE_DIR / "incoming"
PROCESSING_DIR = STORAGE_DIR / "processing"
CLIPS_DIR = STORAGE_DIR / "clips"
DATABASE_DIR = STORAGE_DIR / "database"
QUARANTINE_DIR = STORAGE_DIR / "quarantine"
YOUTUBE_INBOX_DIR = STORAGE_DIR / "youtube_inbox"
FONTS_DIR = STORAGE_DIR / "fonts"
BUNDLED_FONTS_DIR = ROOT_DIR / "storage" / "fonts"

# Ensure directories exist
for path in [INCOMING_DIR, PROCESSING_DIR, CLIPS_DIR, DATABASE_DIR, QUARANTINE_DIR, YOUTUBE_INBOX_DIR, FONTS_DIR]:
    path.mkdir(parents=True, exist_ok=True)

# Seed font manifest into test sandbox or fresh storage if present in project
if FONTS_DIR != BUNDLED_FONTS_DIR and (BUNDLED_FONTS_DIR / "fonts_manifest.json").exists() and not (FONTS_DIR / "fonts_manifest.json").exists():
    try:
        shutil.copy2(BUNDLED_FONTS_DIR / "fonts_manifest.json", FONTS_DIR / "fonts_manifest.json")
    except Exception:
        pass

# Database path
DB_PATH = Path(os.getenv("DISPATCH_DB_PATH", str(DATABASE_DIR / "dispatch.db")))

# Canonical YouTube OAuth Token Path
YOUTUBE_TOKEN_PATH = ROOT_DIR / "youtube_token.json"

# Gemini API configuration
# Strictly loaded from environment variable (.env) - never hardcoded
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Ingestion settings
FILE_STABILIZE_SECONDS = 3.0  # Wait time to verify incoming file is not still writing
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}

# Video Processing Defaults
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
MIN_CLIP_DURATION = 5.0  # Canonical minimum highlight duration (seconds)
MAX_CLIP_DURATION = 180.0  # Canonical maximum highlight duration (seconds)

# Subtitle Styling Defaults (ASS)
SUBTITLE_FONT = "Arial"
SUBTITLE_FONT_SIZE = 24
SUBTITLE_PRIMARY_COLOR = "&H00FFFFFF"  # White
SUBTITLE_OUTLINE_COLOR = "&H00000000"  # Black
SUBTITLE_HIGHLIGHT_COLOR = "&H0000FFFF"  # Yellow for active word

# Default Application Modes
DEFAULT_PUBLISH_MODE = os.getenv("DISPATCH_PUBLISH_MODE", "private")  # "private" or "public"
WEB_HOST = os.getenv("DISPATCH_WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("DISPATCH_WEB_PORT", "8000"))

# Dashboard LAN-trust switch (dashboard hardening 2026-10-28, remediation work item D1).
# "0" (default): unauthenticated local-network (private-IP, non-loopback) callers may
#   only read approved media streams (clip videos/thumbnails, clip & recording source
#   renders, filmstrips, font binaries). Every other request requires the paired
#   device token (x-auth-token) or a Supabase session token.
# "1": restores legacy full trust for local-network callers (dashboard reachable and
#   origin-check gated from the LAN). Only enable on private, isolated networks.
DISPATCH_TRUST_LAN = os.getenv("DISPATCH_TRUST_LAN", "0").strip().lower() in ("1", "true", "yes", "on")

# Request body ceiling enforced by the web middleware (remediation work item D5).
# Uploads to /api/media/upload, /api/recordings/upload and /api/fonts/import are exempt
# from the cap itself (they still enforce their own auth/size rules).
MAX_REQUEST_BODY_BYTES = int(os.getenv("DISPATCH_MAX_BODY_BYTES", str(2 * 1024 * 1024)))

import logging

_logger = logging.getLogger("dispatch.config")

if WEB_HOST not in ("127.0.0.1", "localhost", "::1"):
    _logger.warning(
        "DISPATCH_WEB_HOST is set to non-loopback address '%s'. Server is exposed to the local network. "
        "Unauthenticated local-network callers are restricted to read-only media streams while "
        "DISPATCH_TRUST_LAN is '0'; set DISPATCH_TRUST_LAN=1 only on private networks to restore legacy full LAN trust.",
        WEB_HOST
    )

if DISPATCH_TRUST_LAN:
    _logger.warning(
        "DISPATCH_TRUST_LAN is enabled: local-network callers receive legacy full trust and the dashboard "
        "is reachable without a paired device token from the local network. Use only on private, isolated networks."
    )

