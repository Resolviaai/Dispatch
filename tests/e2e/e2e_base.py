"""Base test case and fixture harness for Dispatch E2E test suites (Tiers 1-4).
Provides strict isolation, temporary test storage, clean SQLite state,
and synthetic media generation helpers.
"""
import os
import shutil
import tempfile
import hashlib
import subprocess
import unittest
from pathlib import Path
from typing import Dict, Any, List, Optional
from fastapi.testclient import TestClient

from dispatch.web.app import app
from dispatch.config import (
    INCOMING_DIR,
    PROCESSING_DIR,
    CLIPS_DIR,
    YOUTUBE_INBOX_DIR,
    QUARANTINE_DIR,
    DB_PATH
)
from dispatch.db import (
    init_db,
    get_db_connection,
    register_chunk,
    save_clip,
    create_session
)
from dispatch.orchestrator.job_queue import (
    init_job_queue_schema,
    enqueue_job
)
from dispatch_mobile.db import (
    init_mobile_db,
    get_mobile_db
)

E2E_DEFAULT_AUTH_TOKEN = "dispatch_e2e_test_token_secret_9988"


class DispatchE2EBaseTestCase(unittest.TestCase):
    """Base class for all E2E test cases across Tiers 1-4."""

    auth_token = E2E_DEFAULT_AUTH_TOKEN

    def setUp(self):
        super().setUp()
        os.environ["DISPATCH_AUTH_TOKEN"] = self.auth_token

        # Initialize databases and schemas
        init_db()
        init_job_queue_schema()
        init_mobile_db()

        # Isolate database tables by clearing test rows
        with get_db_connection() as conn:
            conn.execute("DELETE FROM pipeline_jobs;")
            conn.execute("DELETE FROM publishing_outbox;")
            conn.execute("DELETE FROM clips;")
            conn.execute("DELETE FROM chunks;")
            conn.execute("DELETE FROM sessions;")
            conn.execute("DELETE FROM transcripts;")
            conn.execute("DELETE FROM youtube_inbox;")
            conn.execute("DELETE FROM rejections;")
            conn.commit()

        with get_mobile_db() as conn:
            conn.execute("DELETE FROM mobile_outbox;")
            conn.execute("DELETE FROM mobile_segments;")
            conn.execute("DELETE FROM mobile_sessions;")
            conn.commit()

        # Create temporary working directory for test-generated files
        self.temp_dir = Path(tempfile.mkdtemp(prefix="dispatch_e2e_"))
        self.client = TestClient(app)

    def tearDown(self):
        super().tearDown()
        if self.temp_dir and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def create_synthetic_mp4(
        self,
        target_path: Path,
        duration_seconds: float = 1.0,
        width: int = 1280,
        height: int = 720,
        with_audio: bool = True
    ) -> Path:
        """Create a lightweight, valid MP4 video using FFmpeg lavfi filters."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate=25"]
        if with_audio:
            cmd.extend(["-f", "lavfi", "-i", "sine=frequency=440"])
            cmd.extend(["-c:a", "aac"])
        cmd.extend([
            "-t", str(duration_seconds),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(target_path)
        ])
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to generate synthetic video: {result.stderr}")
        return target_path

    def create_mock_payload_bytes(self, size_bytes: int = 1024, fill_byte: bytes = b"A") -> bytes:
        """Generate deterministic binary payload for chunked sync tests."""
        return fill_byte * size_bytes

    def calculate_bytes_sha256(self, payload: bytes) -> str:
        """Calculate SHA-256 hash of byte payload."""
        return hashlib.sha256(payload).hexdigest()

    def calculate_file_sha256(self, file_path: Path) -> str:
        """Calculate SHA-256 hash of a file on disk."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    def register_test_chunk_and_job(
        self,
        session_id: str = "sess_e2e_001",
        filename: str = "test_chunk.mp4",
        file_bytes: Optional[bytes] = None,
        duration: float = 45.0
    ) -> Dict[str, Any]:
        """Convenience helper to stage a valid chunk and pipeline job in database."""
        video_path = self.temp_dir / filename
        if file_bytes is not None:
            video_path.write_bytes(file_bytes)
        elif not video_path.exists():
            self.create_synthetic_mp4(video_path, duration_seconds=min(1.0, duration))

        sha256_hash = self.calculate_file_sha256(video_path)
        chunk_id = register_chunk(
            session_id=session_id,
            filename=filename,
            filepath=str(video_path),
            file_hash=sha256_hash
        )
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE chunks SET duration = ?, width = 1280, height = 720, aspect_ratio = '16:9' WHERE id = ?",
                (duration, chunk_id)
            )
        job_id = enqueue_job(chunk_id=chunk_id, session_id=session_id)
        return {
            "chunk_id": chunk_id,
            "job_id": job_id,
            "filepath": video_path,
            "sha256": sha256_hash,
            "session_id": session_id
        }
