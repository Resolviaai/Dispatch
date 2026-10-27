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
from datetime import datetime, timedelta, timezone
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

    # Tombstone guard: if Dispatch Cloud still holds a durable reject
    # tombstone for this clip while the local row is ACTIVE, the phone's
    # offline reject happened after this row's last push. Never let this
    # metadata refresh resurrect the clip: skip the whole sync (including
    # any R2 staging) until the cloud state is restored by the user or the
    # drain applies the tombstone locally.
    if status != "rejected":
        try:
            r_tomb = requests.get(
                f"{supabase_url}/rest/v1/clips_cloud?user_id=eq.{user_id}"
                f"&local_clip_id=eq.{clip_id}&select=id,status,reject_requested_at&limit=1",
                headers={"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"},
                timeout=8,
            )
            tomb_rows = r_tomb.json() if r_tomb.ok else []
            if tomb_rows and tomb_rows[0].get("status") == "rejected":
                logger.info(
                    "Skipping cloud sync for clip %s: cloud row holds an active reject tombstone (requested %s)",
                    clip_id, tomb_rows[0].get("reject_requested_at"),
                )
                return True
        except Exception as e:
            logger.debug("Tombstone check failed for clip %s (continuing): %s", clip_id, e)

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

        # Stage Thumbnail to R2 (if exists)
        thumb_path = clip.get("thumbnail_path")
        if thumb_path and Path(thumb_path).exists() and cloud_clip_id:
            try:
                tp = Path(thumb_path)
                tb_size = tp.stat().st_size
                if tb_size > 0:
                    tb_client_token = str(uuid.uuid4())
                    r_tb = requests.post(
                        f"{worker_url}/reserve",
                        headers={
                            "Authorization": f"Bearer {worker_token}",
                            "X-Dispatch-User-Id": user_id,
                            "Content-Type": "application/json",
                        },
                        json={
                            "kind": "thumbnail",
                            "mime_type": "image/jpeg",
                            "expected_bytes": tb_size,
                            "local_clip_id": clip_id,
                            "client_token": tb_client_token,
                        },
                        timeout=10,
                    )
                    if r_tb.ok:
                        tb_data = r_tb.json()
                        with open(tp, "rb") as tf:
                            requests.put(tb_data["upload_url"], data=tf, headers={"Content-Type": "image/jpeg"}, timeout=60)
                        requests.post(
                            f"{worker_url}/complete",
                            headers={
                                "Authorization": f"Bearer {worker_token}",
                                "X-Dispatch-User-Id": user_id,
                                "Content-Type": "application/json",
                            },
                            json={"reservation_id": tb_data["reservation_id"]},
                            timeout=10,
                        )
                        requests.patch(
                            f"{supabase_url}/rest/v1/media_objects?id=eq.{tb_data['media_object_id']}",
                            headers={
                                "apikey": supabase_key,
                                "Authorization": f"Bearer {supabase_key}",
                                "Content-Type": "application/json",
                            },
                            json={"clip_id": cloud_clip_id},
                            timeout=10,
                        )
            except Exception as e:
                logger.debug("Could not sync thumbnail for %s: %s", clip_id, e)

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


# ---------------- Offline Reject/Restore command drain (phone -> PC) ----------------

REJECT_PURGE_GRACE_SECONDS = 24 * 3600  # literal 24h cloud-administered grace


def _cloud_headers(worker_token: str, user_id: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {worker_token}",
        "X-Dispatch-User-Id": user_id,
        "Content-Type": "application/json",
    }


def _resolve_drain_user_ids() -> List[str]:
    """Accounts whose clip commands this PC is authorized to drain.

    Owner set = every account that owns local clips, merged with the cloud
    profiles registered on this Supabase project. A per-command ownership
    check still gates every mutation (multi-account PCs cannot cross-apply
    commands to another account's clips); the profile merge only covers
    single-profile adoption of legacy clips without a local owner.
    """
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    ordered: List[str] = []

    # 1. Locally-owned clips are the authoritative set.
    try:
        with db.get_db_connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT user_id FROM clips "
                "WHERE user_id IS NOT NULL AND user_id != '' "
                "ORDER BY user_id"
            ).fetchall()
        for r in rows:
            uid = str(r["user_id"] or "").strip()
            if uid and uid not in ordered:
                ordered.append(uid)
    except Exception as e:
        logger.warning("drain owner lookup (local clips) failed: %s", e)

    # 2. Registered cloud profiles: covers legacy unowned clips via the
    # single-profile adoption rule in the DB layer.
    if supabase_url and supabase_key:
        try:
            res = requests.get(
                f"{supabase_url}/rest/v1/profiles?select=user_id&order=created_at.desc&limit=25",
                headers={"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"},
                timeout=5,
            )
            if res.ok and res.json():
                for row in res.json():
                    uid = str(row.get("user_id") or "").strip()
                    if uid and uid not in ordered:
                        ordered.append(uid)
        except Exception as e:
            logger.warning("drain owner lookup (profiles) failed: %s", e)

    if not ordered:
        # Legacy single-user PCs: keep draining for the default account. Every
        # command is still ownership-checked per-clip at apply time.
        ordered.append(get_default_user_id())
    return ordered


def _payload_purge_after(payload: Dict[str, Any]) -> Optional[str]:
    """The 24h deadline MUST be measured from the original user request (the
    phone enqueues `purge_after` at REQUEST time), not from whenever the PC
    reconnects; a stale/missing value only exists for pre-0016 commands."""
    raw = str(payload.get("purge_after") or "").strip()
    return raw or None


def _apply_clip_command(command: Dict[str, Any], context_user_id: Optional[str] = None) -> tuple:
    """Apply a single queued clip command locally. Returns (verdict, error).
    verdict: 'done' | 'voided' | 'failed'. Never raises. 'failed' is truthful:
    the phone/cloud keeps tombstoned state until the PC genuinely resolves the
    command (a restore whose media is gone must NOT ack as done — the clip
    cannot resurrect)."""
    kind = command.get("kind")
    clip_id = str(command.get("local_clip_id", ""))
    payload = command.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        if kind == "reject":
            purge_at = _payload_purge_after(payload) or (
                datetime.now(timezone.utc) + timedelta(seconds=REJECT_PURGE_GRACE_SECONDS)
            ).isoformat()
            outcome = db.soft_reject_clip(
                clip_id,
                reason=str(payload.get("reason") or "Rejected from mobile"),
                purge_at=purge_at,
                expected_user_id=context_user_id,
            )
            if outcome == "applied":
                return "done", None
            if outcome == "denied":
                return "failed", "account mismatch; command not applied to another user's clip"
            return "voided", None  # unknown clip locally -> nothing to apply
        if kind == "restore":
            outcome = db.restore_rejected_clip(clip_id, expected_user_id=context_user_id)
            if outcome in ("restored", "kept"):
                return "done", None
            if outcome == "denied":
                return "failed", "account mismatch; command not applied to another user's clip"
            # 'gone': media purged past the original grace window. The cloud
            # tombstone MUST stay (media cannot resurrect) — report failure.
            return "failed", "media purged after grace expiry; clip cannot be restored"
        return "voided", None
    except Exception as e:
        logger.error("Failed to apply clip command %s: %s", command.get("id"), e)
        return "failed", "local apply error; see PC log"


def apply_clip_command(command: Dict[str, Any], context_user_id: Optional[str] = None) -> str:
    """Compat wrapper: apply a commanded action and return only the verdict."""
    verdict, _reason = _apply_clip_command(command, context_user_id)
    return verdict


def _drain_for_user(worker_url: str, worker_token: str, user_id: str, limit: int) -> int:
    applied = 0
    try:
        res = requests.post(
            f"{worker_url}/sync/commands/claim",
            headers=_cloud_headers(worker_token, user_id),
            json={"limit": limit, "lease_seconds": 600},
            timeout=15,
        )
        if not res.ok:
            logger.warning(
                "clip command claim failed for %s: HTTP %s %s",
                user_id, res.status_code, res.text[:150],
            )
            return 0
        commands = res.json().get("commands") or []
        for command in commands:
            command_id = command.get("id")
            if not command_id:
                continue
            verdict, reason = _apply_clip_command(command, context_user_id=user_id)
            ack_payload = {"command_id": command_id, "result": verdict}
            if verdict == "failed":
                ack_payload["error"] = reason or "apply failed locally; see PC log"
            ack = requests.post(
                f"{worker_url}/sync/commands/ack",
                headers=_cloud_headers(worker_token, user_id),
                json=ack_payload,
                timeout=15,
            )
            if ack.ok:
                applied += 1
            else:
                logger.warning("clip command ack failed: HTTP %s", ack.status_code)
    except Exception as e:
        logger.error("drain_clip_commands(%s) failed: %s", user_id, e)
    return applied


def _flush_cloud_purges(worker_url: str, worker_token: str) -> int:
    """Post-grace cloud/R2 deletion sweep. The LOCAL purge (files) already ran
    in purge_expired_rejections; this flush removes the corresponding R2
    objects + cloud media rows through the owner-validated worker endpoint and
    marks local confirmation. Failures keep the purge anchor so the next sync
    cycle retries (idempotent on the worker side)."""
    flushed = 0
    for row in db.list_expired_cloud_rejections():
        owner = str(row.get("user_id") or "").strip()
        if not owner:
            logger.warning(
                "Clip %s expired for cloud purge but has no owner; skipping cloud flush",
                row["id"],
            )
            continue
        try:
            res = requests.post(
                f"{worker_url}/sync/clips/{row['id']}/purge-media",
                headers=_cloud_headers(worker_token, owner),
                json={},
                timeout=30,
            )
            if not res.ok:
                logger.warning(
                    "cloud purge-media for %s failed: HTTP %s %s",
                    row["id"], res.status_code, res.text[:120],
                )
                continue
            body = res.json() or {}
            remaining = int(body.get("remaining") or 0)
            if remaining:
                logger.warning(
                    "cloud purge-media for %s left %d object(s); retrying next cycle",
                    row["id"], remaining,
                )
                continue
            if db.mark_cloud_media_purged(row["id"]):
                flushed += 1
                logger.info("Cloud/R2 assets confirmed purged for clip %s", row["id"])
        except Exception as e:
            logger.error("cloud purge-media for %s errored: %s", row["id"], e)
    return flushed


def import_cloud_captions_for_clips(user_ids: Optional[List[str]] = None) -> int:
    """Cloud-to-PC caption reconciliation: pull phone caption edits saved in
    the cloud while the PC was offline and apply them under a monotonic
    revision guard. Returns the number of tracks applied."""
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not supabase_key:
        return 0
    if not user_ids:
        user_ids = _resolve_drain_user_ids()

    applied = 0
    for user_id in user_ids:
        try:
            res = requests.get(
                f"{supabase_url}/rest/v1/clips_cloud?user_id=eq.{user_id}"
                "&caption_revision=gt.0&select=local_clip_id,caption_data,caption_revision,range_revision",
                headers={"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"},
                timeout=10,
            )
            if not res.ok:
                logger.warning(
                    "cloud caption fetch failed for %s: HTTP %s",
                    user_id, res.status_code,
                )
                continue
            for row in (res.json() or []):
                track = row.get("caption_data")
                if not track:
                    continue
                outcome = db.import_cloud_caption_data(
                    str(row.get("local_clip_id") or ""),
                    json.dumps(track, ensure_ascii=False) if not isinstance(track, str) else track,
                    int(row.get("caption_revision") or 0),
                    expected_user_id=user_id,
                    cloud_range_revision=int(row.get("range_revision") or 0) or None,
                )
                if outcome in ("applied", "stale", "unknown", "invalid"):
                    if outcome == "applied":
                        applied += 1
                else:
                    logger.warning(
                        "cloud caption import for clip %s denied: owner mismatch with %s",
                        row.get("local_clip_id"), user_id,
                    )
        except Exception as e:
            logger.error("import_cloud_captions_for_clips(%s) failed: %s", user_id, e)
    return applied


def import_cloud_captions_for_clip(clip_id: str) -> int:
    """Targeted, consent-free reconciliation used right before a render that
    depends on this clip (approve). Never throws; returns applied count."""
    try:
        clip = db.get_clip_by_id(clip_id)
        if not clip:
            return 0
        owner = str(clip.get("user_id") or "").strip()
        return import_cloud_captions_for_clips([owner] if owner else None)
    except Exception as e:
        logger.error("import_cloud_captions_for_clip(%s) failed: %s", clip_id, e)
        return 0


def drain_clip_commands(limit: int = 25) -> int:
    """Claim queued phone commands from Dispatch Cloud, apply them locally, and
    acknowledge. Runs every sync cycle; safe to run repeatedly."""
    worker_url = os.getenv("DISPATCH_WORKER_URL") or "https://dispatch-cloud.resolviaai.workers.dev"
    worker_token = os.getenv("DISPATCH_WORKER_TOKEN")
    if not worker_token:
        return 0

    applied = 0
    for user_id in _resolve_drain_user_ids():
        applied += _drain_for_user(worker_url, worker_token, user_id, limit)
        # Reconcile phone caption edits saved offline before any dependent
        # render can start (monotonic guard is inside db.import_cloud_caption_data).
        try:
            import_cloud_captions_for_clips([user_id])
        except Exception as e:
            logger.error("cloud caption reconciliation for %s failed: %s", user_id, e)

    try:
        _flush_cloud_purges(worker_url, worker_token)
    except Exception as e:
        logger.error("_flush_cloud_purges failed: %s", e)

    try:
        db.purge_expired_rejections()
    except Exception as e:
        logger.error("purge_expired_rejections failed: %s", e)
    return applied


def start_cloud_sync_worker():
    """Start the periodic cloud sync daemon thread."""
    def _run():
        sync_all_clips_to_cloud()
        while True:
            time.sleep(300)
            sync_all_clips_to_cloud()
            drain_clip_commands()

    t = threading.Thread(target=_run, daemon=True, name="dispatch-cloud-sync")
    t.start()
