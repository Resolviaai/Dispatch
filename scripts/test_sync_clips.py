import os
import json
import uuid
import logging
from pathlib import Path
from datetime import datetime, timezone
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_sync")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
WORKER_URL = os.getenv("DISPATCH_WORKER_URL") or "https://dispatch-cloud.resolviaai.workers.dev"
WORKER_TOKEN = os.getenv("DISPATCH_WORKER_TOKEN")
DEFAULT_USER_ID = "5ddfb570-3d35-4901-9d0a-40da3f62bd64"

def sync_clip_to_cloud(clip_id: str, clip: dict):
    user_id = clip.get("user_id") or DEFAULT_USER_ID
    logger.info("Syncing clip %s for user %s", clip_id, user_id)

    status_raw = clip.get("status") or "ready_review"
    status_map = {
        "ready": "ready_review",
        "ready_review": "ready_review",
        "approved": "approved",
        "published": "published",
        "rejected": "rejected"
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

    # 1. Upsert into clips_cloud
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

    r_clip = requests.post(
        f"{SUPABASE_URL}/rest/v1/clips_cloud?on_conflict=user_id,local_clip_id",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=representation"
        },
        json=clip_payload,
        timeout=10
    )
    if not r_clip.ok:
        logger.error("Failed to upsert clips_cloud: %d %s", r_clip.status_code, r_clip.text)
        return False

    cloud_clip = r_clip.json()[0] if r_clip.json() else {}
    cloud_clip_id = cloud_clip.get("id")
    logger.info("Upserted clips_cloud: %s -> cloud_id %s", clip_id, cloud_clip_id)

    # 2. Check if media exists locally and needs R2 upload
    video_path = clip.get("video_path")
    if not video_path or not Path(video_path).exists():
        logger.info("Clip %s has no local video file on disk, skipping R2 upload.", clip_id)
        return True

    vp = Path(video_path)
    file_size = vp.stat().st_size
    if file_size <= 0:
        return True

    # Check if media_objects or edit_windows already has this clip uploaded
    r_check = requests.get(
        f"{SUPABASE_URL}/rest/v1/edit_windows?user_id=eq.{user_id}&local_clip_id=eq.{clip_id}&status=eq.ready&select=id,media_object_id",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
        timeout=10
    )
    if r_check.ok and r_check.json():
        logger.info("Clip %s already has ready edit_window in cloud.", clip_id)
        return True

    # Reserve upload via Worker
    logger.info("Reserving R2 upload for clip %s (%d bytes)...", clip_id, file_size)
    client_token = str(uuid.uuid4())
    r_res = requests.post(
        f"{WORKER_URL}/reserve",
        headers={
            "Authorization": f"Bearer {WORKER_TOKEN}",
            "X-Dispatch-User-Id": user_id,
            "Content-Type": "application/json"
        },
        json={
            "kind": "edit_window",
            "mime_type": "video/mp4",
            "expected_bytes": file_size,
            "local_clip_id": clip_id,
            "client_token": client_token
        },
        timeout=10
    )
    if not r_res.ok:
        logger.error("Reserve failed: %d %s", r_res.status_code, r_res.text)
        return False

    res_data = r_res.json()
    reservation_id = res_data["reservation_id"]
    upload_url = res_data["upload_url"]
    media_object_id = res_data["media_object_id"]

    # Upload proxy bytes to R2
    logger.info("Uploading %d bytes to R2 presigned URL...", file_size)
    with open(vp, "rb") as vf:
        r_put = requests.put(upload_url, data=vf, headers={"Content-Type": "video/mp4"}, timeout=300)
    if not r_put.ok:
        logger.error("R2 PUT failed: %d %s", r_put.status_code, r_put.text)
        return False

    # Complete upload via Worker
    logger.info("Finalizing R2 upload with Worker...")
    r_comp = requests.post(
        f"{WORKER_URL}/complete",
        headers={
            "Authorization": f"Bearer {WORKER_TOKEN}",
            "X-Dispatch-User-Id": user_id,
            "Content-Type": "application/json"
        },
        json={"reservation_id": reservation_id},
        timeout=15
    )
    if not r_comp.ok:
        logger.error("Complete failed: %d %s", r_comp.status_code, r_comp.text)
        return False

    logger.info("Upload completed: media_object_id %s", media_object_id)

    # Link to edit_windows in Supabase
    ew_payload = {
        "user_id": user_id,
        "local_clip_id": clip_id,
        "window_start": s_time,
        "window_end": e_time,
        "status": "ready",
        "media_object_id": media_object_id,
        "staged_at": datetime.now(timezone.utc).isoformat()
    }
    requests.post(
        f"{SUPABASE_URL}/rest/v1/edit_windows",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json"
        },
        json=ew_payload,
        timeout=10
    )

    # If cloud_clip_id exists, link media_object to clip_id
    if cloud_clip_id:
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/media_objects?id=eq.{media_object_id}",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json"
            },
            json={"clip_id": cloud_clip_id},
            timeout=10
        )

    logger.info("Successfully synced clip %s to cloud and R2!", clip_id)
    return True

if __name__ == "__main__":
    import sqlite3
    conn = sqlite3.connect("storage/database/dispatch.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM clips WHERE status IN ('ready_review', 'published', 'approved')")
    rows = cur.fetchall()
    print(f"Found {len(rows)} clips to sync")
    for r in rows:
        clip_data = dict(r)
        sync_clip_to_cloud(clip_data["id"], clip_data)
