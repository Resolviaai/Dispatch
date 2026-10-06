"""Incoming video watcher and stabilization manager.
Monitors the incoming folder, ensures transfers are complete, validates with ffprobe,
and stages video files into processing.
"""
import time
import shutil
import logging
from pathlib import Path
from typing import Callable, Optional, List, Dict, Any
from dispatch.config import (
    INCOMING_DIR,
    PROCESSING_DIR,
    QUARANTINE_DIR,
    ALLOWED_VIDEO_EXTENSIONS,
    FILE_STABILIZE_SECONDS,
)
from dispatch.ingestion.validator import probe_video, calculate_file_hash
from dispatch import db

logger = logging.getLogger("dispatch.ingestion")


def is_file_stable(filepath: Path, wait_seconds: float = FILE_STABILIZE_SECONDS) -> bool:
    """Check if file size remains constant over wait_seconds to avoid reading half-synced files."""
    try:
        initial_size = filepath.stat().st_size
        if initial_size == 0:
            return False
        time.sleep(wait_seconds)
        final_size = filepath.stat().st_size
        return initial_size == final_size
    except Exception as e:
        logger.debug("File %s not accessible yet: %s", filepath.name, e)
        return False


def process_incoming_file(filepath: Path, session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Validate and stage a single incoming file into processing directory."""
    if not filepath.exists() or filepath.suffix.lower() not in ALLOWED_VIDEO_EXTENSIONS:
        return None

    logger.info("Checking incoming file: %s", filepath.name)

    # 1. Stabilization check
    if not is_file_stable(filepath):
        logger.info("File %s is still writing or empty, skipping for now.", filepath.name)
        return None

    # 2. Probe with ffprobe
    is_valid, metadata, error = probe_video(filepath)
    if not is_valid:
        logger.error("Invalid video %s: %s. Quarantining file.", filepath.name, error)
        try:
            quarantine_path = QUARANTINE_DIR / filepath.name
            shutil.move(str(filepath), str(quarantine_path))
        except Exception as q_err:
            logger.warning("Could not quarantine %s: %s", filepath.name, q_err)
        return None

    # 3. Calculate hash
    file_hash = calculate_file_hash(filepath)

    # 4. Atomically move to processing directory
    target_path = PROCESSING_DIR / filepath.name
    # Avoid collision if same name exists
    if target_path.exists():
        target_path = PROCESSING_DIR / f"{filepath.stem}_{int(time.time())}{filepath.suffix}"

    try:
        shutil.move(str(filepath), str(target_path))
    except Exception as e:
        logger.error("Failed to move %s to processing: %s", filepath.name, e)
        return None

    # 5. Register in DB
    chunk_id = db.register_chunk(
        session_id=session_id,
        filename=target_path.name,
        filepath=str(target_path),
        file_hash=file_hash
    )
    db.update_chunk_metadata(
        chunk_id=chunk_id,
        duration=metadata["duration"],
        width=metadata["width"],
        height=metadata["height"],
        aspect_ratio=metadata["aspect_ratio"],
        status="verified"
    )

    logger.info("Chunk %s registered and verified at %s (Aspect Ratio: %s, Duration: %.1fs)",
                chunk_id, target_path.name, metadata['aspect_ratio'], metadata['duration'])

    return {
        "chunk_id": chunk_id,
        "filepath": target_path,
        "metadata": metadata,
        "file_hash": file_hash
    }


def scan_incoming(session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Perform a one-shot scan of incoming folder."""
    staged = []
    for item in INCOMING_DIR.iterdir():
        if item.is_file() and item.suffix.lower() in ALLOWED_VIDEO_EXTENSIONS:
            result = process_incoming_file(item, session_id=session_id)
            if result:
                staged.append(result)
    return staged


def watch_incoming_loop(callback: Optional[Callable[[Dict[str, Any]], None]] = None, poll_interval: float = 5.0):
    """Background polling loop for incoming video drops."""
    logger.info("Starting incoming folder watcher on %s", INCOMING_DIR)
    while True:
        try:
            staged = scan_incoming()
            for item in staged:
                if callback:
                    callback(item)
        except Exception as e:
            logger.error("Error in watcher loop: %s", e)
        time.sleep(poll_interval)
