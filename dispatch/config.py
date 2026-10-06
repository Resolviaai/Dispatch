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

# Storage directories
STORAGE_DIR = ROOT_DIR / "storage"
INCOMING_DIR = STORAGE_DIR / "incoming"
PROCESSING_DIR = STORAGE_DIR / "processing"
CLIPS_DIR = STORAGE_DIR / "clips"
DATABASE_DIR = STORAGE_DIR / "database"

# Ensure directories exist
for path in [INCOMING_DIR, PROCESSING_DIR, CLIPS_DIR, DATABASE_DIR]:
    path.mkdir(parents=True, exist_ok=True)

# Database path
DB_PATH = DATABASE_DIR / "dispatch.db"

# Gemini API configuration
# Strictly loaded from environment variable (.env) - never hardcoded
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Ingestion settings
FILE_STABILIZE_SECONDS = 3.0  # Wait time to verify incoming file is not still writing
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}

# Video Processing Defaults
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
MIN_CLIP_DURATION = 20.0  # seconds
MAX_CLIP_DURATION = 90.0  # seconds

# Subtitle Styling Defaults (ASS)
SUBTITLE_FONT = "Arial"
SUBTITLE_FONT_SIZE = 24
SUBTITLE_PRIMARY_COLOR = "&H00FFFFFF"  # White
SUBTITLE_OUTLINE_COLOR = "&H00000000"  # Black
SUBTITLE_HIGHLIGHT_COLOR = "&H0000FFFF"  # Yellow for active word

# Default Application Modes
DEFAULT_PUBLISH_MODE = os.getenv("DISPATCH_PUBLISH_MODE", "private")  # "private" or "public"
WEB_HOST = os.getenv("DISPATCH_WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("DISPATCH_WEB_PORT", "8000"))
