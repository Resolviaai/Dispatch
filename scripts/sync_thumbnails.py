import os
import uuid
import logging
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sync_thumbnails")

from dispatch import db

supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
worker_url = os.getenv("DISPATCH_WORKER_URL") or "https://dispatch-cloud.resolviaai.workers.dev"
worker_token = os.getenv("DISPATCH_WORKER_TOKEN")
DEFAULT_USER_ID = "5ddfb570-3d35-4901-9d0a-40da3f62bd64"

def sync_all_thumbnails():
    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, thumbnail_path, user_id FROM clips WHERE status IN ('ready_review', 'published', 'approved')")
        clips = [dict(r) for r in cursor.fetchall()]

    print(f"Checking thumbnails for {len(clips)} clips...")
    for c in clips:
        clip_id = c["id"]
        user_id = c.get("user_id") or DEFAULT_USER_ID
        tp_str = c.get("thumbnail_path")
        if not tp_str or not Path(tp_str).exists():
            print(f"Skipping {clip_id}: no thumbnail on disk ({tp_str})")
            continue
        tp = Path(tp_str)
        file_size = tp.stat().st_size
        if file_size <= 0:
            continue

        # Get cloud clip id
        r_c = requests.get(
            f"{supabase_url}/rest/v1/clips_cloud?user_id=eq.{user_id}&local_clip_id=eq.{clip_id}&select=id",
            headers={"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"},
            timeout=5,
        )
        if not r_c.ok or not r_c.json():
            print(f"Clip {clip_id} not in clips_cloud")
            continue
        cloud_clip_id = r_c.json()[0]["id"]

        # Check existing thumbnail
        r_chk = requests.get(
            f"{supabase_url}/rest/v1/media_objects?user_id=eq.{user_id}&clip_id=eq.{cloud_clip_id}&kind=eq.thumbnail&status=eq.active&select=id",
            headers={"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"},
            timeout=5,
        )
        if r_chk.ok and r_chk.json():
            print(f"Thumbnail for {clip_id} already exists in R2.")
            continue

        # Reserve
        client_token = str(uuid.uuid4())
        r_res = requests.post(
            f"{worker_url}/reserve",
            headers={
                "Authorization": f"Bearer {worker_token}",
                "X-Dispatch-User-Id": user_id,
                "Content-Type": "application/json",
            },
            json={
                "kind": "thumbnail",
                "mime_type": "image/jpeg",
                "expected_bytes": file_size,
                "local_clip_id": clip_id,
                "client_token": client_token,
            },
            timeout=10,
        )
        if not r_res.ok:
            print(f"Reserve failed for {clip_id}: {r_res.status_code} {r_res.text}")
            continue

        res_data = r_res.json()
        res_id = res_data["reservation_id"]
        up_url = res_data["upload_url"]
        mo_id = res_data["media_object_id"]

        with open(tp, "rb") as tf:
            r_put = requests.put(up_url, data=tf, headers={"Content-Type": "image/jpeg"}, timeout=60)
        if not r_put.ok:
            print(f"PUT failed for {clip_id}: {r_put.status_code}")
            continue

        r_comp = requests.post(
            f"{worker_url}/complete",
            headers={
                "Authorization": f"Bearer {worker_token}",
                "X-Dispatch-User-Id": user_id,
                "Content-Type": "application/json",
            },
            json={"reservation_id": res_id},
            timeout=10,
        )
        if not r_comp.ok:
            print(f"Complete failed for {clip_id}: {r_comp.status_code} {r_comp.text}")
            continue

        # Link to cloud_clip_id
        requests.patch(
            f"{supabase_url}/rest/v1/media_objects?id=eq.{mo_id}",
            headers={
                "apikey": supabase_key,
                "Authorization": f"Bearer {supabase_key}",
                "Content-Type": "application/json",
            },
            json={"clip_id": cloud_clip_id},
            timeout=10,
        )
        print(f"Successfully uploaded thumbnail for {clip_id} ({file_size} bytes)")

if __name__ == "__main__":
    sync_all_thumbnails()
