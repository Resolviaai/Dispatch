"""Dispatch Cloud Synchronization Engine.

Synchronizes local clips and edit windows from the PC to Supabase and Cloudflare R2:
- Mode 1: Gemini Suggest / Clip Metadata (synced to Supabase `clips_cloud`).
- Mode 2: Temporary Edit Window / 720p Proxy (staged to Cloudflare R2 via worker /reserve and /complete).
- Mode 3: Local full-access source / rendered clip.

Auth:
- Talks to Cloudflare Worker using DISPATCH_WORKER_TOKEN and X-Dispatch-User-Id.
- Talks to Supabase REST using SUPABASE_SERVICE_ROLE_KEY.
"""

from __future__ import annotations

import os
import json
import uuid
import logging
import threading
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import requests

from dispatch import db

logger = logging.getLogger("dispatch.cloud.sync")


def get_default_user_id() -> str:
    """Resolve a default user_id from Supabase profiles if a local clip has no user_id."""
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not supabase_key:
        return "5ddfb570-3d35-4901-9d0a-40da3f62bd64"

    try:
        res = requests.get(
            f"{supabase_url}/rest/v1/profiles?select=user_id,display_name&order=created_at.desc&limit=1",
            headers={"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"},
            timeout=5,
        )
        if res.ok and res.json():
            return res.json()[0]["user_id"]
    except Exception:
        pass
    return "5ddfb570-3d35-4901-9d0a-40da3f62bd64"


def sync_clip_to_cloud(clip_id: str, clip: Optional[Dict[str, Any]] = None) -> bool:
    """Sync a single clip and its video proxy / edit window to Supabase and R2."""
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    worker_url = os.getenv("DISPATCH_WORKER_URL") or "https://dispatch-cloud.resolviaai.workers.dev"
    worker_token = os.getenv("DISPATCH_WORKER_TOKEN")

    if not supabase_url or not supabase_key or not worker_token:
        logger.debug("Cloud credentials missing; skipping cloud sync for clip %s", clip_id)
        return False

    if not clip:
        clip = db.get_clip_by_id(clip_id)
    if not clip:
        logger.warning("Clip %s not found in local database; skipping sync.", clip_id)
        return False

    user_id = clip.get("user_id") or get_default_user_id()

    status_raw = clip.get("status") or "ready_review"
    status_map = {
        "ready": "ready_review",
        "ready_review": "ready_review",
        "approved": "approved",
        "published": "published",
        "rejected": "rejected",
    }
    status = status_map.get(status_raw, "ready_review")

    s_time = float(clip.get("start_time") or 0.0)
    e_time = float(clip.get("end_time") or (s_time + 10.0))
    if e_time <= s_time:
        e_time = s_time + 10.0

    ai_s = float(clip.get("ai_suggested_start_time") or s_time)
    ai_e = float(clip.get("ai_suggested_end_time") or e_time)
    if ai_e <= ai_s:
        ai_e = ai_s + 10.0

    # 1. Mode 1: Upsert into clips_cloud (AI suggestions & clip bounds)
    clip_payload = {
        "user_id": user_id,
        "local_clip_id": clip_id,
        "title": clip.get("title") or "Clip Highlight",
        "status": status,
        "ai_suggested_start_time": ai_s,
        "ai_suggested_end_time": ai_e,
        "start_time": s_time,
        "end_time": e_time,
        "range_revision": int(clip.get("range_revision") or 0),
        "render_stale": False,
    }

    try:
        r_clip = requests.post(
            f"{supabase_url}/rest/v1/clips_cloud?on_conflict=user_id,local_clip_id",
            headers={
                "apikey": supabase_key,
                "Authorization": f"Bearer {supabase_key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=representation",
            },
            json=clip_payload,
            timeout=10,
        )
        if not r_clip.ok:
            logger.error("Failed to upsert clips_cloud for %s: %d %s", clip_id, r_clip.status_code, r_clip.text)
            return False
        cloud_clip = r_clip.json()[0] if r_clip.json() else {}
        cloud_clip_id = cloud_clip.get("id")
    except Exception as e:
        logger.error("Network error upserting clips_cloud for %s: %s", clip_id, e)
        return False

    # 2. Mode 2: Check if local media exists and stage to Cloudflare R2
    video_path = clip.get("video_path")
    if not video_path or not Path(video_path).exists():
        logger.info("Clip %s has no local video file on disk; metadata synced to cloud.", clip_id)
        return True

    vp = Path(video_path)
    file_size = vp.stat().st_size
    if file_size <= 0:
        return True

    # Check if already staged in edit_windows
    try:
        r_check = requests.get(
            f"{supabase_url}/rest/v1/edit_windows?user_id=eq.{user_id}&local_clip_id=eq.{clip_id}&status=eq.ready&select=id,media_object_id",
            headers={"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"},
            timeout=10,
        )
        if r_check.ok and r_check.json():
            logger.debug("Clip %s already staged in R2.", clip_id)
            return True
    except Exception:
        pass

    # Reserve upload via Worker
    try:
        client_token = str(uuid.uuid4())
        r_res = requests.post(
            f"{worker_url}/reserve",
            headers={
                "Authorization": f"Bearer {worker_token}",
                "X-Dispatch-User-Id": user_id,
                "Content-Type": "application/json",
            },
            json={
                "kind": "edit_window",
                "mime_type": "video/mp4",
                "expected_bytes": file_size,
                "local_clip_id": clip_id,
                "client_token": client_token,
            },
            timeout=10,
        )
        if not r_res.ok:
            logger.error("R2 reserve failed for %s: %d %s", clip_id, r_res.status_code, r_res.text)
            return False

        res_data = r_res.json()
        reservation_id = res_data["reservation_id"]
        upload_url = res_data["upload_url"]
        media_object_id = res_data["media_object_id"]

        # Upload proxy bytes directly to Cloudflare R2
        with open(vp, "rb") as vf:
            r_put = requests.put(upload_url, data=vf, headers={"Content-Type": "video/mp4"}, timeout=300)
        if not r_put.ok:
            logger.error("R2 PUT failed for %s: %d %s", clip_id, r_put.status_code, r_put.text)
            return False

        # Finalize upload with Worker
        r_comp = requests.post(
            f"{worker_url}/complete",
            headers={
                "Authorization": f"Bearer {worker_token}",
                "X-Dispatch-User-Id": user_id,
                "Content-Type": "application/json",
            },
            json={"reservation_id": reservation_id},
            timeout=15,
        )
        if not r_comp.ok:
            logger.error("R2 complete failed for %s: %d %s", clip_id, r_comp.status_code, r_comp.text)
            return False

        # Link to edit_windows in Supabase
        ew_payload = {
            "user_id": user_id,
            "local_clip_id": clip_id,
            "window_start": s_time,
            "window_end": e_time,
            "status": "ready",
            "media_object_id": media_object_id,
            "staged_at": datetime.now(timezone.utc).isoformat(),
        }
        r_ew = requests.post(
            f"{supabase_url}/rest/v1/edit_windows",
            headers={
                "apikey": supabase_key,
                "Authorization": f"Bearer {supabase_key}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            json=ew_payload,
            timeout=10,
        )
        window_id = r_ew.json()[0]["id"] if (r_ew.ok and r_ew.json()) else None

        # Link media_object to cloud_clip_id
        if cloud_clip_id:
            requests.patch(
                f"{supabase_url}/rest/v1/media_objects?id=eq.{media_object_id}",
                headers={
                    "apikey": supabase_key,
                    "Authorization": f"Bearer {supabase_key}",
                    "Content-Type": "application/json",
                },
                json={"clip_id": cloud_clip_id},
                timeout=10,
            )

        # Transcripts: if caption data exists, extract words and insert into window_transcripts
        caption_json = clip.get("caption_data")
        if caption_json and window_id:
            try:
                cap_data = json.loads(caption_json) if isinstance(caption_json, str) else caption_json
                words = cap_data.get("words", [])
                if words:
                    requests.post(
                        f"{supabase_url}/rest/v1/window_transcripts",
                        headers={
                            "apikey": supabase_key,
                            "Authorization": f"Bearer {supabase_key}",
                            "Content-Type": "application/json",
                            "Prefer": "resolution=merge-duplicates",
                        },
                        json={
                            "window_id": window_id,
                            "user_id": user_id,
                            "words": words,
                            "transcribed_at": datetime.now(timezone.utc).isoformat(),
                        },
                        timeout=10,
                    )
            except Exception as e:
                logger.debug("Could not sync window transcripts for %s: %s", clip_id, e)

        logger.info("Successfully synced clip %s to Cloudflare R2 & Supabase!", clip_id)
        return True

    except Exception as e:
        logger.error("Error staging clip %s to R2: %s", clip_id, e)
        return False


def sync_all_clips_to_cloud():
    """Scan local SQLite for ready/approved clips and synchronize missing items to cloud."""
    try:
        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM clips WHERE status IN ('ready_review', 'published', 'approved')")
            rows = cursor.fetchall()

        logger.info("Starting background cloud sync for %d local clips...", len(rows))
        for r in rows:
            clip_dict = dict(r)
            sync_clip_to_cloud(clip_dict["id"], clip_dict)
    except Exception as e:
        logger.error("Error in sync_all_clips_to_cloud: %s", e)


def start_cloud_sync_worker():
    """Start the periodic cloud sync daemon thread."""
    def _run():
        sync_all_clips_to_cloud()
        while True:
            time.sleep(300)
            sync_all_clips_to_cloud()

    t = threading.Thread(target=_run, daemon=True, name="dispatch-cloud-sync")
    t.start()
