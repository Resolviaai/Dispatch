"""Dispatch Cloud Synchronization Engine.

Synchronizes local clips and edit windows from the PC to Supabase and R2:
- Mode 1: Gemini Suggest / Clip Metadata (synced to Supabase `clips_cloud`).
- Mode 2: Temporary Edit Window / 720p Proxy (staged to Cloudflare R2 via worker /reserve and /complete).
- Mode 3: Local full-access source / rendered clip.

Auth (audit 2026-10-27):
- The PC talks ONLY to the Cloudflare Worker using DISPATCH_WORKER_TOKEN and
  X-Dispatch-User-Id. The Supabase service-role key never leaves the Worker
  (trusted server side); privileged database operations run through the
  owner-scoped /sync/pc/* worker endpoints.
- Ownership isolation: every mutated local clip must belong to the account the
  PC is acting for. Commands for unknown clips are acknowledged 'unknown' so
  they are re-delivered to the machine that holds them, never swallowed.
"""

from __future__ import annotations

import os
import uuid
import json
import logging
import threading
import time
import re
from pathlib import Path
from urllib.parse import quote as urlquote
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
import requests

from dispatch import db

logger = logging.getLogger("dispatch.cloud.sync")

_WORKER_URL_DEFAULT = "https://dispatch-cloud.resolviaai.workers.dev"
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _worker_base() -> str:
    return os.getenv("DISPATCH_WORKER_URL") or _WORKER_URL_DEFAULT


def get_worker_token() -> str:
    return os.getenv("DISPATCH_WORKER_TOKEN") or ""


def _worker_headers(user_id: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {get_worker_token()}",
        "Content-Type": "application/json",
    }
    if user_id:
        headers["X-Dispatch-User-Id"] = user_id
    return headers


def _cloud_headers(worker_token: str, user_id: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {worker_token}",
        "X-Dispatch-User-Id": user_id,
        "Content-Type": "application/json",
    }


def authorized_pc_owner_ids() -> List[str]:
    """Accounts the operator explicitly authorized this PC installation for.

    Set DISPATCH_PC_OWNER_USER_IDS as a comma/semicolon-separated list of
    account ids. Explicit configuration is the ONLY multi-profile authorization
    path: auditing 2026-10-27 removed the old "any profile on the shared
    project" draining, which let one machine process (and swallow) another
    account's commands.
    """
    raw = os.getenv("DISPATCH_PC_OWNER_USER_IDS", "")
    owners: List[str] = []
    for part in raw.replace(";", ",").split(","):
        uid = part.strip()
        if uid and _UUID_RE.match(uid) and uid not in owners:
            owners.append(uid)
    return owners


def resolve_single_profile_owner() -> Optional[str]:
    """Safe ownership resolution for legacy unowned clips.

    Audit 2026-10-27 (P0-B): an unowned legacy clip must NEVER be assigned to
    "whatever profile was created most recently". The cloud can hand a PC its
    account only when the shared project has exactly ONE profile (legacy
    single-user machine). Any multi-profile project returns None and requires
    explicit configuration (DISPATCH_PC_OWNER_USER_IDS) before unowned data is
    pushed — an unowned clip is not pushed to another user's account on a guess.
    (The dummy X-Dispatch-User-Id header satisfies the Worker's authentications
    by shape; the endpoint does not use it for the lookup — safe because the
    caller already holds the shared worker token.)
    """
    token = get_worker_token()
    if not token:
        return None
    try:
        res = requests.get(
            f"{_worker_base()}/sync/pc/eligible-owner",
            headers={
                "Authorization": f"Bearer {token}",
                # Shape-valid placeholder: authenticate() requires a UUID header
                # for worker tokens; this endpoint does not read the actor id.
                "X-Dispatch-User-Id": "00000000-0000-0000-0000-000000000000",
            },
            timeout=8,
        )
        if res.ok:
            body = res.json() if res.text else {}
            uid = str(body.get("user_id") or "").strip() if isinstance(body, dict) else ""
            if body.get("reason") in ("multiple_profiles", "no_profiles") or not uid:
                logger.warning(
                    "No safe default owner: profiles=%s. Set DISPATCH_PC_OWNER_USER_IDS on this PC.",
                    body.get("reason") if isinstance(body, dict) else "?",
                )
                return None
            return uid
    except Exception as e:
        logger.error("single-profile owner lookup failed: %s", e)
    return None


def _normalize_words(raw_words: Any) -> List[Dict[str, Any]]:
    """Canonical word shape: {word, start, end} in SOURCE-relative seconds."""
    out: List[Dict[str, Any]] = []
    if not isinstance(raw_words, list):
        return out
    for entry in raw_words:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("word") or entry.get("w") or entry.get("text") or "").strip()
        try:
            start = float(entry.get("start"))
            end = float(entry.get("end"))
        except (TypeError, ValueError):
            continue
        if text and end > start:
            out.append({"word": text, "start": round(start, 3), "end": round(end, 3)})
    return out


def _clip_source_window(clip: Dict[str, Any]) -> tuple:
    """(start, end) of this clip inside its source video, in seconds.

    Returns (None, None) when the clip carries no usable range, which is the
    caller's signal to leave words unfiltered rather than drop them all.
    """
    lo = clip.get("caption_range_start")
    if lo is None:
        lo = clip.get("start_time")
    hi = clip.get("caption_range_end")
    if hi is None:
        hi = clip.get("end_time")
    try:
        lo_f, hi_f = float(lo), float(hi)
    except (TypeError, ValueError):
        return None, None
    if not (hi_f > lo_f):
        return None, None
    return lo_f, hi_f


def _words_in_window(words: List[Dict[str, Any]], lo: Optional[float], hi: Optional[float]) -> List[Dict[str, Any]]:
    """Keep words that overlap [lo, hi]; a word straddling the cut is still on screen."""
    if lo is None or hi is None:
        return words
    return [w for w in words if float(w["end"]) >= lo and float(w["start"]) <= hi]


def extract_source_words(clip: Dict[str, Any]) -> List[Dict[str, Any]]:
    """SOURCE-relative word timings for THIS CLIP ONLY.

    Order: edited caption_data.words, then persisted transcript segments. Never
    invents words. An empty result means transcription readiness is genuinely
    missing and the window must not claim otherwise.

    The transcript table stores the whole chunk/session, so every path is
    clipped to the clip's source window: an 8-second window must not receive the
    424-second source transcript, and `only_if_missing` re-registration would
    otherwise preserve that mistake permanently.
    """
    lo, hi = _clip_source_window(clip)
    raw = clip.get("caption_data")
    if raw:
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            data = None
        if isinstance(data, dict):
            # Local CaptionTrack groups store CLIP-relative times. Convert them
            # to SOURCE-relative before they cross into the cloud transcript
            # contract (the Worker subtracts window_start downstream).
            base = float(clip.get("caption_range_start") or 0.0)
            grouped: List[Dict[str, Any]] = []
            groups = data.get("groups")
            if isinstance(groups, list) and clip.get("caption_coordinate_system") == "clip_relative":
                for group in groups:
                    if not isinstance(group, dict):
                        continue
                    for word in group.get("words", []) if isinstance(group.get("words"), list) else []:
                        if not isinstance(word, dict):
                            continue
                        try:
                            start = base + float(word.get("start"))
                            end = base + float(word.get("end"))
                        except (TypeError, ValueError):
                            continue
                        grouped.append({**word, "start": start, "end": end})
            grouped_words = _words_in_window(_normalize_words(grouped), lo, hi)
            if grouped_words:
                return grouped_words
            # Legacy top-level words are treated as SOURCE-relative.
            words = _words_in_window(_normalize_words(data.get("words")), lo, hi)
            if words:
                return words

    words: List[Dict[str, Any]] = []
    try:
        with db.get_db_connection() as conn:
            rows = []
            if clip.get("chunk_id"):
                rows = conn.execute(
                    "SELECT segments_json FROM transcripts WHERE chunk_id = ?",
                    (clip["chunk_id"],),
                ).fetchall()
            if not rows and clip.get("session_id"):
                rows = conn.execute(
                    "SELECT segments_json FROM transcripts WHERE session_id = ?",
                    (clip["session_id"],),
                ).fetchall()
            for row in rows:
                try:
                    segments = json.loads(row["segments_json"] or "[]")
                except Exception:
                    continue
                for segment in segments if isinstance(segments, list) else []:
                    if isinstance(segment, dict):
                        words.extend(_normalize_words(segment.get("words")))
    except Exception as e:
        logger.debug("transcript word lookup failed for clip %s: %s", clip.get("id"), e)

    words.sort(key=lambda w: w["start"])
    return _words_in_window(words, lo, hi)


def _render_fingerprint(clip: Dict[str, Any], video_path: Path) -> str:
    """Cheap identity for the exact rendered bytes (size + mtime + range rev)."""
    stat = video_path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}:{int(clip.get('range_revision') or 0)}"


def _publication_sync_payload(clip_id: str) -> List[Dict[str, Any]]:
    """Published destination metadata for the cloud; stats stay cloud-owned."""
    payload: List[Dict[str, Any]] = []
    for row in db.get_publications_for_clips([clip_id]).get(clip_id, []):
        account_ref = str(row.get("account_id") or "").strip()
        remote_id = str(row.get("remote_id") or "").strip()
        if not account_ref or not remote_id:
            continue
        published_at = row.get("published_at")
        if not published_at:
            continue
        try:
            parsed = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            published_at = parsed.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            continue
        payload.append({
            "platform": str(row.get("platform") or "").lower(),
            "account_id": account_ref,
            "remote_id": remote_id,
            "remote_url": row.get("remote_url"),
            "published_at": published_at,
            "publish_mode": row.get("publish_mode"),
        })
    return payload


def push_window_transcript(clip_id: str, clip: Optional[Dict[str, Any]] = None, only_if_missing: bool = True,
                           owner_user_id: Optional[str] = None) -> str:
    """Persist words for an ALREADY-STAGED window without re-uploading media.

    Returns: pushed | skipped_existing | no_words | not_staged | unauthorized_owner |
    failed | no_credentials. Backfill uses `only_if_missing=True` so it can never
    overwrite a newer cloud transcript.
    """
    if not get_worker_token():
        return "no_credentials"
    if not clip:
        clip = db.get_clip_by_id(clip_id)
    if not clip:
        return "failed"

    user_id = str(clip.get("user_id") or "").strip()
    if not user_id and owner_user_id:
        # Legacy unowned clip: an operator may explicitly assert the owning account,
        # but only for an account this PC is authorized to act as. The Worker still
        # enforces row ownership server-side, so a wrong assertion fails closed.
        asserted = owner_user_id.strip()
        if asserted not in authorized_pc_owner_ids():
            logger.warning("Transcript backfill refused for %s: asserted owner is not an authorized PC owner.", clip_id)
            return "unauthorized_owner"
        user_id = asserted
    if not user_id:
        user_id = resolve_single_profile_owner() or ""
    if not user_id or not _UUID_RE.match(user_id):
        return "unauthorized_owner"
    try:
        r_state = requests.get(
            f"{_worker_base()}/sync/pc/clips/{urlquote(clip_id, safe='')}",
            headers=_cloud_headers(get_worker_token(), user_id),
            timeout=15,
        )
        state = r_state.json() if (r_state.ok and r_state.text) else None
    except Exception:
        state = None
    if not isinstance(state, dict) or not state.get("already_staged"):
        return "not_staged"
    media_id = str(state.get("media_object_id") or "").strip()
    if not media_id:
        return "failed"
    window_id = str(state.get("window_id") or "").strip()
    words = extract_source_words(clip)
    if not words:
        return "no_words"
    try:
        res = requests.post(
            f"{_worker_base()}/sync/pc/windows",
            headers=_cloud_headers(get_worker_token(), user_id),
            json={
                "local_clip_id": clip_id,
                "media_object_id": media_id,
                "window_id": window_id or None,
                "window_start": float(state.get("window_start") or clip.get("start_time") or 0.0),
                "window_end": float(state.get("window_end") or clip.get("end_time") or 0.0),
                "words": words,
                "render_fingerprint": state.get("render_fingerprint"),
                "only_if_missing": bool(only_if_missing),
            },
            timeout=20,
        )
        if not res.ok:
            logger.error("Transcript backfill failed for %s: %d %s", clip_id, res.status_code, res.text[:200])
            return "failed"
        body = res.json() if res.text else {}
        if body.get("transcript_skipped") == "existing":
            return "skipped_existing"
        if body.get("transcripts_stored") is False:
            return "failed"
        return "pushed"
    except Exception as e:
        logger.error("Transcript backfill errored for %s: %s", clip_id, e)
        return "failed"


def sync_clip_to_cloud(clip_id: str, clip: Optional[Dict[str, Any]] = None) -> bool:
    """Sync a single clip and its video proxy / edit window to Supabase and R2."""
    if not get_worker_token():
        logger.debug("Cloud credentials missing; skipping cloud sync for clip %s", clip_id)
        return False

    if not clip:
        clip = db.get_clip_by_id(clip_id)
    if not clip:
        logger.warning("Clip %s not found in local database; skipping sync.", clip_id)
        return False

    user_id = str(clip.get("user_id") or "").strip()
    if not user_id:
        user_id = resolve_single_profile_owner()
    if not user_id:
        logger.error(
            "Clip %s has no owner and no safe default account is available; "
            "skipping cloud sync (set DISPATCH_PC_OWNER_USER_IDS to authorize this PC).",
            clip_id,
        )
        return False
    if not _UUID_RE.match(user_id):
        logger.error("Clip %s carries an invalid user_id; skipping cloud sync.", clip_id)
        return False

    status_raw = clip.get("status") or "ready_review"
    status_map = {
        "ready": "ready_review",
        "ready_review": "ready_review",
        "approved": "approved",
        "published": "published",
        "rejected": "rejected",
    }
    status = status_map.get(status_raw, "ready_review")

    # Owner-scoped cloud state: tombstone guard + staging bookkeeping, computed
    # inside the Worker (the PC no longer reads clips_cloud/edit_windows
    # directly — audit 2026-10-27).
    try:
        r_state = requests.get(
            f"{_worker_base()}/sync/pc/clips/{urlquote(clip_id, safe='')}",
            headers=_cloud_headers(get_worker_token(), user_id),
            timeout=15,
        )
        state_dict = r_state.json() if (r_state.ok and r_state.text) else None
        if not isinstance(state_dict, dict):
            state_dict = None
    except Exception:
        state_dict = None

    # Tombstone guard: if Dispatch Cloud still holds a durable reject
    # tombstone for this clip while the local row is ACTIVE, the phone's
    # offline reject happened after this row's last push. Never let this
    # metadata refresh resurrect the clip: skip the whole sync (including
    # any R2 staging) until the cloud state is restored by the user or the
    # drain applies the tombstone locally.
    if status != "rejected" and state_dict and state_dict.get("tombstoned"):
        logger.info(
            "Skipping cloud sync for clip %s: cloud row holds an active reject tombstone (requested %s)",
            clip_id, state_dict.get("reject_requested_at"),
        )
        return True

    s_time = float(clip.get("start_time") or 0.0)
    e_time = float(clip.get("end_time") or (s_time + 10.0))
    if e_time <= s_time:
        e_time = s_time + 10.0

    ai_s = float(clip.get("ai_suggested_start_time") or s_time)
    ai_e = float(clip.get("ai_suggested_end_time") or e_time)
    if ai_e <= ai_s:
        ai_e = ai_s + 10.0

    # 1. Mode 1: Upsert into clips_cloud (AI suggestions & clip bounds) — the
    # Worker enforces the tombstone guard server-side as well.
    try:
        from dispatch.web.app import get_clip_framing_context
        framing_ctx = get_clip_framing_context(clip)
        raw_caption_data = clip.get("caption_data")
        caption_payload = None
        if raw_caption_data:
            try:
                import json
                caption_payload = json.loads(raw_caption_data) if isinstance(raw_caption_data, str) else raw_caption_data
            except Exception:
                pass

        r_clip = requests.post(
            f"{_worker_base()}/sync/pc/clips",
            headers=_cloud_headers(get_worker_token(), user_id),
            json={
                "local_clip_id": clip_id,
                "title": clip.get("title") or "Clip Highlight",
                "status": status,
                "start_time": s_time,
                "end_time": e_time,
                "ai_suggested_start_time": ai_s,
                "ai_suggested_end_time": ai_e,
                "range_revision": int(clip.get("range_revision") or 0),
                "source_orientation": framing_ctx.get("source_orientation") or "landscape",
                "source_aspect_ratio": framing_ctx.get("source_aspect_ratio") or "16:9",
                "layout_mode": clip.get("layout_mode") or framing_ctx.get("layout_mode") or "fit_blur",
                "available_layout_modes": framing_ctx.get("available_layout_modes") or [],
                "caption_data": caption_payload,
                "caption_revision": int(clip.get("caption_range_revision") or clip.get("captions_revision") or 0),
                "publications": _publication_sync_payload(clip_id),
            },
            timeout=20,
        )
        if not r_clip.ok:
            logger.error("Failed to upsert clips_cloud for %s: %d %s", clip_id, r_clip.status_code, r_clip.text[:200])
            return False
        upsert_body = r_clip.json() if r_clip.text else {}
        if upsert_body.get("publications_error"):
            logger.warning("Cloud publication metadata incomplete for %s: %s", clip_id, upsert_body.get("publications_error"))
        if upsert_body.get("skipped_tombstoned"):
            logger.info(
                "Cloud accepted clip %s as tombstoned-skipped; metadata not refreshed.",
                clip_id,
            )
            return True
        cloud_clip_id = upsert_body.get("cloud_clip_id")
    except Exception as e:
        logger.error("Network error upserting clips_cloud for %s: %s", clip_id, e)
        return False

    caption_words = extract_source_words(clip)

    # Fingerprint the exact local render. A ready cloud window is only fresh
    # when its stored fingerprint matches these bytes; existence is not version
    # equality (audit F10).
    video_path = clip.get("video_path")
    local_video = Path(video_path) if video_path and Path(video_path).exists() else None
    if local_video is not None and local_video.stat().st_size <= 0:
        local_video = None
    current_fingerprint = _render_fingerprint(clip, local_video) if local_video is not None else None
    staged_fingerprint = state_dict.get("render_fingerprint") if state_dict else None
    staged_covers_range = False
    if state_dict and state_dict.get("window_start") is not None and state_dict.get("window_end") is not None:
        try:
            staged_covers_range = (
                float(state_dict["window_start"]) <= s_time + 1e-6
                and float(state_dict["window_end"]) >= e_time - 1e-6
            )
        except (TypeError, ValueError):
            staged_covers_range = False
    staged_is_fresh = bool(
        state_dict
        and state_dict.get("already_staged")
        and current_fingerprint is not None
        and staged_fingerprint == current_fingerprint
        and staged_covers_range
    )

    # Already staged in edit_windows (server-side check from the state call).
    # Media readiness is not transcript readiness: re-register the window with
    # words when we now have them, without re-uploading the proxy. This runs
    # BEFORE the local-file check so a staged cloud window can be repaired even
    # if the PC-side rendered file was cleaned up. A fingerprint mismatch falls
    # through to a fresh proxy upload instead.
    if state_dict and state_dict.get("already_staged") and (staged_is_fresh or current_fingerprint is None):
        staged_media_id = str(state_dict.get("media_object_id") or "").strip()
        if not caption_words:
            logger.warning(
                "Clip %s edit window is staged but has no transcript words; "
                "leaving transcript_status=missing (run the transcript backfill).",
                clip_id,
            )
            return True
        if not staged_media_id:
            logger.error("Clip %s is staged but the Worker returned no media_object_id; cannot persist transcript.", clip_id)
            return False
        try:
            r_tr = requests.post(
                f"{_worker_base()}/sync/pc/windows",
                headers=_cloud_headers(get_worker_token(), user_id),
                json={
                    "local_clip_id": clip_id,
                    "media_object_id": staged_media_id,
                    # Reuse the EXACT staged bounds so the Worker's idempotent
                    # window lookup cannot create a second window.
                    "window_start": float(state_dict.get("window_start") or s_time),
                    "window_end": float(state_dict.get("window_end") or e_time),
                    "words": caption_words,
                    # Preserve the existing fingerprint; this path uploads no bytes.
                    "render_fingerprint": staged_fingerprint,
                },
                timeout=20,
            )
            if not r_tr.ok:
                logger.error("Transcript re-registration failed for %s: %d %s", clip_id, r_tr.status_code, r_tr.text[:200])
                return False
            body = r_tr.json() if r_tr.text else {}
            if body.get("transcripts_stored") is False:
                logger.error("Worker did not persist transcript words for %s (status=%s)", clip_id, body.get("transcript_status"))
                return False
            logger.info("Transcript words persisted for already-staged clip %s (%d words).", clip_id, len(caption_words))
            return True
        except Exception as e:
            logger.error("Transcript re-registration errored for %s: %s", clip_id, e)
            return False

    # 2. Mode 2: Check if local media exists and stage to Cloudflare R2
    if local_video is None:
        logger.info("Clip %s has no local video file on disk; metadata synced to cloud.", clip_id)
        return True

    vp = local_video
    file_size = vp.stat().st_size

    # Reserve upload via Worker
    try:
        client_token = str(uuid.uuid4())
        r_res = requests.post(
            f"{_worker_base()}/reserve",
            headers=_cloud_headers(get_worker_token(), user_id),
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
            logger.error("R2 reserve failed for %s: %d %s", clip_id, r_res.status_code, r_res.text[:200])
            return False

        res_data = r_res.json()
        reservation_id = res_data["reservation_id"]
        upload_url = res_data["upload_url"]
        media_object_id = res_data["media_object_id"]

        # Upload proxy bytes directly to Cloudflare R2
        with open(vp, "rb") as vf:
            r_put = requests.put(upload_url, data=vf, headers={"Content-Type": "video/mp4"}, timeout=300)
        if not r_put.ok:
            logger.error("R2 PUT failed for %s: %d %s", clip_id, r_put.status_code, r_put.text[:200])
            return False

        # Finalize upload with Worker
        r_comp = requests.post(
            f"{_worker_base()}/complete",
            headers=_cloud_headers(get_worker_token(), user_id),
            json={"reservation_id": reservation_id},
            timeout=15,
        )
        if not r_comp.ok:
            logger.error("R2 complete failed for %s: %d %s", clip_id, r_comp.status_code, r_comp.text[:200])
            return False

        # Register the ready edit window server-side: creates the row, links the
        # media object to the cloud clip, and stores window transcript words.
        # (The PC no longer writes edit_windows/media_objects/window_transcripts
        # directly with a service-role key — audit 2026-10-27.)
        window_words: Optional[list] = caption_words or None

        try:
            r_window = requests.post(
                f"{_worker_base()}/sync/pc/windows",
                headers=_cloud_headers(get_worker_token(), user_id),
                json={
                    "local_clip_id": clip_id,
                    "media_object_id": media_object_id,
                    "window_start": s_time,
                    "window_end": e_time,
                    "words": window_words,
                    "render_fingerprint": current_fingerprint,
                },
                timeout=20,
            )
            if not r_window.ok:
                logger.error("edit window registration failed for %s: %d %s", clip_id, r_window.status_code, r_window.text[:200])
                return False
            window_body = r_window.json() if r_window.text else {}
            if window_body.get("transcripts_stored") is False and window_words:
                # The media is staged, but claiming success here is exactly the
                # silent-empty-captions failure. Retry; the next pass takes the
                # already_staged branch and persists words without re-uploading.
                logger.error(
                    "Window registered for %s but transcript words were not stored (status=%s); "
                    "returning failure so the next sync retries the transcript only.",
                    clip_id, window_body.get("transcript_status"),
                )
                return False
            if not window_words:
                logger.warning(
                    "Window registered for %s with no transcript words; transcript_status=missing.",
                    clip_id,
                )
        except Exception as e:
            logger.error("edit window registration errored for %s: %s", clip_id, e)
            return False

        # Stage Thumbnail to R2 (if exists)
        thumb_path = clip.get("thumbnail_path")
        if thumb_path and Path(thumb_path).exists() and cloud_clip_id:
            try:
                tp = Path(thumb_path)
                tb_size = tp.stat().st_size
                if tb_size > 0:
                    tb_client_token = str(uuid.uuid4())
                    r_tb = requests.post(
                        f"{_worker_base()}/reserve",
                        headers=_cloud_headers(get_worker_token(), user_id),
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
                            f"{_worker_base()}/complete",
                            headers=_cloud_headers(get_worker_token(), user_id),
                            json={"reservation_id": tb_data["reservation_id"]},
                            timeout=10,
                        )
                        requests.post(
                            f"{_worker_base()}/sync/pc/media-link",
                            headers=_cloud_headers(get_worker_token(), user_id),
                            json={
                                "media_object_id": tb_data["media_object_id"],
                                "local_clip_id": clip_id,
                            },
                            timeout=15,
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
            rows = conn.execute(
                "SELECT * FROM clips WHERE status IN ('ready_review', 'published', 'approved')"
            ).fetchall()

        logger.info("Starting background cloud sync for %d local clips...", len(rows))
        for r in rows:
            clip_dict = dict(r)
            sync_clip_to_cloud(clip_dict["id"], clip_dict)
    except Exception as e:
        logger.error("Error in sync_all_clips_to_cloud: %s", e)


# ---------------- Offline Reject/Restore command drain (phone -> PC) ----------------

REJECT_PURGE_GRACE_SECONDS = 24 * 3600  # literal 24h cloud-administered grace


def _resolve_drain_user_ids() -> List[str]:
    """Accounts whose clip commands this PC is authorized to drain (audit 1A).

    Authorization here is EXPLICIT, never "any profile that exists":
    1. accounts that own local clips on this machine, and
    2. explicitly configured owners (DISPATCH_PC_OWNER_USER_IDS).
    When neither yields an account, the legacy single-profile project rule
    applies at most one account — and only via the Worker's owner hint, which
    refuses multi-profile projects. Every command remains ownership-checked
    per-clip at apply time as defense in depth.
    """
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
            if uid and _UUID_RE.match(uid) and uid not in ordered:
                ordered.append(uid)
    except Exception as e:
        logger.warning("drain owner lookup (local clips) failed: %s", e)

    # 2. Explicitly configured owners for this installation.
    for uid in authorized_pc_owner_ids():
        if uid not in ordered:
            ordered.append(uid)

    # 3. Legacy single-profile fallback (Worker refuses multi-profile projects).
    if not ordered:
        uid = resolve_single_profile_owner()
        if uid and uid not in ordered:
            ordered.append(uid)
    return ordered


def _payload_purge_after(payload: Dict[str, Any]) -> Optional[str]:
    """The 24h deadline MUST be measured from the original user request (the
    phone enqueues `purge_after` at REQUEST time), not from whenever the PC
    reconnects; a stale/missing value only exists for pre-0016 commands."""
    raw = str(payload.get("purge_after") or "").strip()
    return raw or None


def _apply_clip_command(command: Dict[str, Any], context_user_id: Optional[str] = None) -> tuple:
    """Apply a single queued clip command locally. Returns (verdict, error).
    verdict: 'done' | 'voided' | 'failed' | 'unknown'. Never raises. 'failed' is
    truthful: the phone/cloud keeps tombstoned state until the PC genuinely
    resolves the command (a restore whose media is gone must NOT ack as done —
    the clip cannot resurrect).

    'unknown' (audit 1A): the target clip does not exist locally. This may mean
    the wrong machine claimed the command; acking it done/voided would swallow
    the user's request forever. The Worker instead re-delivers it so the PC
    that actually holds the clip applies it.
    """
    kind = command.get("kind")
    clip_id = str(command.get("local_clip_id", ""))
    payload = command.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        if kind not in ("reject", "restore"):
            return "voided", None
        with db.get_db_connection() as conn:
            row = conn.execute(
                "SELECT id, user_id FROM clips WHERE id = ?", (clip_id,)
            ).fetchone()
        if row is None:
            return "unknown", "clip not present on this PC; re-deliver to the owning machine"
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
            if outcome == "unknown":
                # Race: the clip vanished between the local precheck and the
                # apply. Ack it unknown so the Worker re-delivers the command
                # to the machine that actually holds the clip.
                return "unknown", "clip disappeared during apply; re-deliver to the owning machine"
            return "failed", "reject could not be applied locally; see PC log"  # defensive
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
        if commands:
            logger.info("Claimed %d clip command(s) for %s", len(commands), user_id)
        for command in commands:
            command_id = command.get("id")
            if not command_id:
                continue
            verdict, reason = _apply_clip_command(command, context_user_id=user_id)
            ack_payload = {"command_id": command_id, "result": verdict}
            if verdict == "failed":
                ack_payload["error"] = reason or "apply failed locally; see PC log"
            elif verdict == "unknown":
                ack_payload["error"] = reason or "clip not present on this PC"
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
            if res.status_code == 409:
                # Server explicitly refuses: either not tombstoned anymore or
                # the grace window has not elapsed yet — retry next cycle.
                logger.info(
                    "cloud purge-media for %s deferred: %s",
                    row["id"], res.text[:120],
                )
                continue
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
            else:
                # Local file deletion has not completed yet (purge anchor kept
                # by design); the next sweep retires the anchor once both local
                # paths are cleared.
                logger.info(
                    "Cloud/R2 assets confirmed for %s; local deletion still pending "
                    "so the purge anchor is kept for the next sweep.",
                    row["id"],
                )
        except Exception as e:
            logger.error("cloud purge-media for %s errored: %s", row["id"], e)
    return flushed


def import_cloud_captions_for_clips(user_ids: Optional[List[str]] = None) -> int:
    """Cloud-to-PC caption reconciliation: pull phone caption edits saved in
    the cloud while the PC was offline and apply them under a monotonic
    revision guard. Returns the number of tracks applied."""
    token = get_worker_token()
    if not token:
        return 0
    if not user_ids:
        user_ids = _resolve_drain_user_ids()

    applied = 0
    for user_id in user_ids:
        try:
            res = requests.get(
                f"{_worker_base()}/sync/pc/captions",
                headers=_cloud_headers(token, user_id),
                timeout=15,
            )
            if not res.ok:
                logger.warning(
                    "cloud caption fetch failed for %s: HTTP %s",
                    user_id, res.status_code,
                )
                continue
            body = res.json() if res.text else []
            rows = body.get("clips") if isinstance(body, dict) else body
            for row in (rows or []):
                if not isinstance(row, dict):
                    continue
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
        if owner:
            return import_cloud_captions_for_clips([owner])
        return import_cloud_captions_for_clips(None)
    except Exception as e:
        logger.error("import_cloud_captions_for_clip(%s) failed: %s", clip_id, e)
        return 0


def sync_cloud_template_for_user(worker_url: str, worker_token: str, user_id: str) -> bool:
    """Fetch user's default editing template from Cloud and persist to local SQLite."""
    try:
        res = requests.get(
            f"{worker_url}/template/default",
            headers=_cloud_headers(worker_token, user_id),
            timeout=10,
        )
        if res.ok:
            data = res.json() if res.text else {}
            tpl = data.get("template")
            if tpl and isinstance(tpl, dict) and tpl.get("captions"):
                db.save_default_template(tpl, user_id=user_id)
                logger.info("Synchronized cloud default template for user %s", user_id)
                return True
    except Exception as e:
        logger.debug("Failed syncing template for user %s: %s", user_id, e)
    return False


def drain_clip_commands(limit: int = 25, user_ids: Optional[List[str]] = None) -> int:
    """Claim queued phone commands from Dispatch Cloud, apply them locally, and
    acknowledge. Runs every sync cycle and immediately at daemon startup; safe
    to run repeatedly. `user_ids` lets an operator drain one explicit account."""
    worker_url = _worker_base()
    worker_token = get_worker_token()
    if not worker_token:
        return 0

    targets = user_ids if user_ids is not None else _resolve_drain_user_ids()
    if not targets:
        logger.warning(
            "Clip command drain has no authorized owner. Set DISPATCH_PC_OWNER_USER_IDS "
            "or ensure this PC owns at least one clip; queued phone commands cannot be claimed."
        )

    applied = 0
    for user_id in targets:
        applied += _drain_for_user(worker_url, worker_token, user_id, limit)
        # Reconcile phone caption edits saved offline before any dependent
        # render can start (monotonic guard is inside db.import_cloud_caption_data).
        try:
            import_cloud_captions_for_clips([user_id])
        except Exception as e:
            logger.error("cloud caption reconciliation for %s failed: %s", user_id, e)
        try:
            sync_cloud_template_for_user(worker_url, worker_token, user_id)
        except Exception as e:
            logger.debug("cloud template reconciliation for %s failed: %s", user_id, e)

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
        # Drain immediately: a command queued while the PC was off must not wait
        # an extra five minutes (or be stranded by frequent restarts).
        drain_clip_commands()
        while True:
            time.sleep(300)
            sync_all_clips_to_cloud()
            drain_clip_commands()

    t = threading.Thread(target=_run, daemon=True, name="dispatch-cloud-sync")
    t.start()
