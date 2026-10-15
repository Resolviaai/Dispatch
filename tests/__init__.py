"""Test suite isolation harness.
Prepares an isolated temporary storage and SQLite database directory
BEFORE application modules are imported or run.
"""
import os
import atexit
import shutil
import tempfile
from pathlib import Path

# Setup unique test sandbox directory
_test_sandbox_dir = tempfile.mkdtemp(prefix="dispatch_test_sandbox_")
_storage_dir = Path(_test_sandbox_dir) / "storage"
_db_dir = _storage_dir / "database"
_db_dir.mkdir(parents=True, exist_ok=True)
_test_db_path = _db_dir / "test_dispatch.db"

# Set environment overrides before ANY dispatch module is imported
os.environ["DISPATCH_STORAGE_DIR"] = str(_storage_dir)
os.environ["DISPATCH_DB_PATH"] = str(_test_db_path)
os.environ["DISPATCH_DISABLE_AUTO_OUTBOX"] = "1"
os.environ["DISPATCH_DISABLE_BACKGROUND_POLLERS"] = "1"

# Initialize SQLite database tables in isolated sandbox
from dispatch.db import init_db
init_db()

# Cleanup sandbox on test suite completion
def _cleanup_test_sandbox():
    try:
        shutil.rmtree(_test_sandbox_dir, ignore_errors=True)
    except Exception:
        pass

atexit.register(_cleanup_test_sandbox)
