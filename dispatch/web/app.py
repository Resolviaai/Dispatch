"""FastAPI Web Dashboard for Dispatch.
Provides the local PC dashboard for reviewing, approving, and publishing clips.
"""
import os
import json
import psutil
import logging
import threading
import time
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Header
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from pydantic import BaseModel

from dispatch.config import (
    CLIPS_DIR,
    ROOT_DIR,
    DEFAULT_PUBLISH_MODE,
    PROCESSING_DIR,
    INCOMING_DIR,
    MAX_CLIP_DURATION,
    MIN_CLIP_DURATION
)
from dispatch import db
from dispatch.video_engine.renderer import render_clip
from dispatch.youtube_inbox.catcher import (
    YouTubeInboxCatcher,
    extract_youtube_video_id
)
from dispatch.captions import (
    CaptionTrack,
    group_words_into_captions,
    CAPTION_PRESETS,
    export_to_ass,
    export_to_json
)
from dispatch.captions.presets import get_preset
from dispatch.ai_clips.highlight_finder import transliterate_segments_to_hinglish

from dispatch.sync.receiver import router as sync_router

logger = logging.getLogger("dispatch.web")

app = FastAPI(title="Dispatch Dashboard", version="1.0.0")
app.include_router(sync_router)

class ApproveRequest(BaseModel):
    title: Optional[str] = None
    hashtags: Optional[str] = None
    publish_mode: Optional[str] = None  # "private" or "public"
    platforms: Optional[str] = None     # "youtube,instagram"
    layout_mode: Optional[str] = None   # "fit_blur", "crop_follow", "landscape"


class RejectRequest(BaseModel):
    reason: Optional[str] = "User rejected"


class LayoutModeRequest(BaseModel):
    layout_mode: str


class YouTubeIngestRequest(BaseModel):
    url: str
    force_whisper: Optional[bool] = False


class SettingsRequest(BaseModel):
    publish_mode: Optional[str] = None
    auto_process: Optional[str] = None


@app.get("/clips/{filename}")
async def get_clip_file(filename: str):
    """Stream clip video or image thumbnail directly from storage."""
    file_path = CLIPS_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    media_type = "video/mp4" if filename.endswith(".mp4") else "image/jpeg"
    return FileResponse(file_path, media_type=media_type)


@app.get("/download/dispatch.apk")
async def download_android_apk():
    """Download the native Dispatch Android APK compiled for POCO C65."""
    apk_path = ROOT_DIR / "android" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
    if not apk_path.exists():
        raise HTTPException(
            status_code=404,
            detail="The Android APK is currently compiling or not built yet."
        )
    return FileResponse(
        str(apk_path),
        media_type="application/vnd.android.package-archive",
        filename="Dispatch-POCO-C65-v1.apk"
    )


@app.get("/api/pipeline/jobs")
async def get_pipeline_jobs():
    """Return live checkpointed jobs with current stage, status, leases, and retry timers."""
    return {"jobs": db.get_active_pipeline_jobs()}


@app.get("/api/status")
async def get_system_status():
    """Return system resource metrics and pipeline counters."""
    disk = psutil.disk_usage(str(ROOT_DIR))
    free_gb = round(disk.free / (1024 ** 3), 1)
    total_gb = round(disk.total / (1024 ** 3), 1)

    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM clips WHERE status = 'ready_review'")
        ready_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM clips WHERE status = 'approved'")
        approved_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM clips WHERE status = 'published'")
        published_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM chunks")
        chunks_count = cursor.fetchone()[0]

    return {
        "disk_free_gb": free_gb,
        "disk_total_gb": total_gb,
        "disk_percent_used": disk.percent,
        "ready_clips": ready_count,
        "approved_clips": approved_count,
        "published_clips": published_count,
        "total_chunks": chunks_count,
        "publish_mode": db.get_setting("publish_mode", DEFAULT_PUBLISH_MODE),
        "auto_process": db.get_setting("auto_process", "true")
    }


def resolve_clip_source_path(clip: Dict[str, Any]) -> Optional[Path]:
    """Safely resolve the original raw source video chunk for a clip."""
    # 1. Resolve through chunk_id in chunks table
    chunk_id = clip.get("chunk_id")
    if chunk_id:
        with db.get_db_connection() as conn:
            row = conn.execute("SELECT filepath FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
            if row and row["filepath"]:
                cand = Path(row["filepath"])
                if cand.exists() and cand.is_file() and cand.stat().st_size > 0:
                    return cand

    # 2. Check storage/incoming and storage/processing using session_id or chunk_id
    for search_dir in (PROCESSING_DIR, INCOMING_DIR):
        if clip.get("session_id"):
            sess = clip["session_id"]
            for cand in search_dir.glob(f"*{sess}*.mp4"):
                if cand.exists() and cand.is_file() and cand.stat().st_size > 0:
                    return cand
        if chunk_id:
            for cand in search_dir.glob(f"*{chunk_id}*.mp4"):
                if cand.exists() and cand.is_file() and cand.stat().st_size > 0:
                    return cand

    # 3. Fallback: if clip video_path exists, use it so preview never hard-crashes
    if clip.get("video_path"):
        cand = Path(clip["video_path"])
        if cand.exists() and cand.is_file() and cand.stat().st_size > 0:
            return cand

    return None


def get_clip_framing_context(clip: Dict[str, Any]) -> Dict[str, Any]:
    """Derive authentic source orientation, allowed framing modes, and target dimensions.
    The original session recording is the single source of truth.
    """
    orientation = "landscape"
    source_w = None
    source_h = None
    source_aspect = "16:9"

    # 1. Query chunk metadata from database
    chunk_id = clip.get("chunk_id")
    if chunk_id:
        with db.get_db_connection() as conn:
            row = conn.execute("SELECT width, height, aspect_ratio FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
            if row:
                source_w = row["width"]
                source_h = row["height"]
                if row["aspect_ratio"]:
                    source_aspect = str(row["aspect_ratio"]).strip()
                if source_h and source_w:
                    orientation = "portrait" if source_h > source_w else "landscape"
                elif source_aspect in ("9:16", "portrait"):
                    orientation = "portrait"

    # 2. If inconclusive or chunk dimensions missing/zero, probe source file directly
    if source_w is None or source_h is None or source_w <= 0 or source_h <= 0:
        source_path = resolve_clip_source_path(clip)
        if source_path and source_path.exists():
            from dispatch.ingestion.validator import inspect_video_orientation
            info = inspect_video_orientation(source_path)
            if info.get("valid"):
                orientation = info.get("orientation", "landscape")
                source_w = info.get("effective_width") or info.get("width")
                source_h = info.get("effective_height") or info.get("height")
                source_aspect = "9:16" if orientation == "portrait" else "16:9"
                if chunk_id and source_w and source_h:
                    try:
                        with db.get_db_connection() as conn:
                            conn.execute("UPDATE chunks SET width = ?, height = ?, aspect_ratio = ? WHERE id = ?",
                                         (source_w, source_h, source_aspect, chunk_id))
                            conn.commit()
                    except Exception:
                        pass

    # 3. Dynamic options based strictly on original session orientation
    if orientation == "portrait":
        # Case A: Original input is Vertical / 9:16 -> EXACTLY 3 possible outputs
        available_modes = [
            {
                "id": "fit_black",
                "label": "Fit + Black",
                "target_aspect": "16:9",
                "description": "16:9 widescreen output with black pillarbox bars"
            },
            {
                "id": "fit_blur",
                "label": "Fit + Blur",
                "target_aspect": "16:9",
                "description": "16:9 widescreen output with blurred background"
            },
            {
                "id": "native_916",
                "label": "Native 9:16",
                "target_aspect": "9:16",
                "description": "100% full original vertical video (no bars, no crop)"
            }
        ]
        valid_ids = {"fit_black", "fit_blur", "native_916", "native_portrait"}
        cur = (clip.get("layout_mode") or "").strip().lower()
        if cur == "native_portrait":
            cur = "native_916"
        if cur not in ("fit_black", "fit_blur", "native_916"):
            cur = "native_916"
        target_aspect = "16:9" if cur in ("fit_black", "fit_blur") else "9:16"
    else:
        # Case B: Original input is Horizontal / 16:9 -> EXACTLY 4 possible outputs
        available_modes = [
            {
                "id": "crop_916",
                "label": "Crop 9:16",
                "target_aspect": "9:16",
                "description": "Full vertical crop to fill 9:16"
            },
            {
                "id": "fit_blur",
                "label": "Fit + Blur",
                "target_aspect": "9:16",
                "description": "Vertical 9:16 with blurred top/bottom bars"
            },
            {
                "id": "fit_black",
                "label": "Fit + Black",
                "target_aspect": "9:16",
                "description": "Vertical 9:16 with black top/bottom bars"
            },
            {
                "id": "native_169",
                "label": "Native 16:9",
                "target_aspect": "16:9",
                "description": "100% full original horizontal 16:9 (no bars, no crop)"
            }
        ]
        valid_ids = {"crop_916", "crop_follow", "fit_blur", "fit_black", "native_169", "landscape"}
        cur = (clip.get("layout_mode") or "").strip().lower()
        if cur == "crop_follow":
            cur = "crop_916"
        elif cur == "landscape":
            cur = "native_169"
        if cur not in ("crop_916", "fit_blur", "fit_black", "native_169"):
            cur = "crop_916"
        target_aspect = "16:9" if cur == "native_169" else "9:16"

    return {
        "source_orientation": orientation,
        "source_aspect_ratio": "9:16" if orientation == "portrait" else "16:9",
        "available_layout_modes": available_modes,
        "layout_mode": cur,
        "target_aspect": target_aspect,
        "valid_mode_ids": valid_ids,
        "source_width": source_w,
        "source_height": source_h,
    }


@app.get("/api/clips")
async def list_clips():
    """List clips grouped by status with source media URLs and stale render flags."""
    ready_clips = db.get_clips_for_review()
    for c in ready_clips:
        if c.get("video_path"):
            c["video_url"] = f"/clips/{Path(c['video_path']).name}"
        if c.get("thumbnail_path"):
            c["thumbnail_url"] = f"/clips/{Path(c['thumbnail_path']).name}"
        c["source_video_url"] = f"/api/clips/{c['id']}/source"
        ctx = get_clip_framing_context(c)
        c["source_orientation"] = ctx["source_orientation"]
        c["source_aspect_ratio"] = ctx["source_aspect_ratio"]
        c["available_layout_modes"] = ctx["available_layout_modes"]
        c["layout_mode"] = ctx["layout_mode"]
        c["target_aspect"] = ctx["target_aspect"]
        c["is_render_stale"] = bool(
            c.get("rendered_layout_mode") and c["layout_mode"] != c.get("rendered_layout_mode")
        )

    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM clips WHERE status IN ('approved', 'published') ORDER BY created_at DESC LIMIT 20")
        published_clips = [dict(r) for r in cursor.fetchall()]
        for c in published_clips:
            if c.get("video_path"):
                c["video_url"] = f"/clips/{Path(c['video_path']).name}"
            if c.get("thumbnail_path"):
                c["thumbnail_url"] = f"/clips/{Path(c['thumbnail_path']).name}"
            c["source_video_url"] = f"/api/clips/{c['id']}/source"
            ctx = get_clip_framing_context(c)
            c["source_orientation"] = ctx["source_orientation"]
            c["source_aspect_ratio"] = ctx["source_aspect_ratio"]
            c["available_layout_modes"] = ctx["available_layout_modes"]
            c["layout_mode"] = ctx["layout_mode"]
            c["target_aspect"] = ctx["target_aspect"]
            c["is_render_stale"] = bool(
                c.get("rendered_layout_mode") and c["layout_mode"] != c.get("rendered_layout_mode")
            )

    return {
        "ready": ready_clips,
        "recent_approved": published_clips
    }


@app.get("/api/clips/{clip_id}")
async def get_clip_detail(clip_id: str):
    """Return single clip metadata with source URL and render status."""
    clip = db.get_clip_by_id(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if clip.get("video_path"):
        clip["video_url"] = f"/clips/{Path(clip['video_path']).name}"
    if clip.get("thumbnail_path"):
        clip["thumbnail_url"] = f"/clips/{Path(clip['thumbnail_path']).name}"
    clip["source_video_url"] = f"/api/clips/{clip['id']}/source"
    ctx = get_clip_framing_context(clip)
    clip["source_orientation"] = ctx["source_orientation"]
    clip["source_aspect_ratio"] = ctx["source_aspect_ratio"]
    clip["available_layout_modes"] = ctx["available_layout_modes"]
    clip["layout_mode"] = ctx["layout_mode"]
    clip["target_aspect"] = ctx["target_aspect"]
    clip["is_render_stale"] = bool(
        clip.get("rendered_layout_mode") and clip["layout_mode"] != clip.get("rendered_layout_mode")
    )
    return clip


@app.get("/api/clips/{clip_id}/source")
async def get_clip_source_media(clip_id: str):
    """Safely resolve and stream the original raw source video chunk for this clip.
    Supports HTTP Range requests (206 Partial Content) for smooth HTML5 video scrubbing.
    """
    clip = db.get_clip_by_id(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    source_path = resolve_clip_source_path(clip)
    if not source_path or not source_path.exists():
        raise HTTPException(status_code=404, detail="Source media chunk not found on disk")

    resolved_path = source_path.resolve()
    if not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="Invalid source media file")

    return FileResponse(
        path=str(resolved_path),
        media_type="video/mp4",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=3600",
            "X-Clip-Start": str(clip.get("start_time", 0.0)),
            "X-Clip-End": str(clip.get("end_time", clip.get("duration", 0.0))),
            "X-Clip-Duration": str(clip.get("duration", 0.0)),
        }
    )


def is_matching_layout(a: Optional[str], b: Optional[str]) -> bool:
    """Check if two layout modes are functionally identical (including aliases)."""
    if not a or not b:
        return False
    if a == b:
        return True
    alias_groups = [
        {"crop_916", "crop_follow"},
        {"native_169", "landscape"},
        {"native_916", "native_portrait"},
    ]
    return any(a in group and b in group for group in alias_groups)


@app.post("/api/clips/{clip_id}/layout")
async def handle_update_clip_layout(clip_id: str, payload: LayoutModeRequest):
    """Persist chosen layout framing mode for a clip immediately."""
    clip = db.get_clip_by_id(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    ctx = get_clip_framing_context(clip)
    raw_mode = (payload.layout_mode or "").strip().lower()
    if raw_mode not in ctx["valid_mode_ids"]:
        allowed_list = [m["id"] for m in ctx["available_layout_modes"]]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid layout_mode '{payload.layout_mode}' for {ctx['source_orientation']} source. Allowed: {allowed_list}"
        )

    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE clips SET layout_mode = ? WHERE id = ?", (raw_mode, clip_id))
        conn.commit()

    updated_clip = db.get_clip_by_id(clip_id)
    updated_ctx = get_clip_framing_context(updated_clip)
    rendered_mode = updated_clip.get("rendered_layout_mode") if updated_clip else None
    is_stale = bool(rendered_mode and not is_matching_layout(rendered_mode, raw_mode))

    return {
        "status": "success",
        "clip_id": clip_id,
        "layout_mode": raw_mode,
        "source_orientation": updated_ctx["source_orientation"],
        "target_aspect": updated_ctx["target_aspect"],
        "available_layout_modes": updated_ctx["available_layout_modes"],
        "rendered_layout_mode": rendered_mode,
        "is_render_stale": is_stale
    }


@app.post("/api/clips/{clip_id}/render")
async def handle_render_clip(clip_id: str, payload: Optional[LayoutModeRequest] = None):
    """Explicitly re-render a clip from source media using the current or specified layout_mode."""
    clip = db.get_clip_by_id(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    ctx = get_clip_framing_context(clip)
    raw_mode = (payload.layout_mode if payload and payload.layout_mode else None) or clip.get("layout_mode") or ctx["layout_mode"]
    raw_mode = str(raw_mode).strip().lower()

    if raw_mode not in ctx["valid_mode_ids"]:
        raw_mode = ctx["layout_mode"]

    target_layout = raw_mode

    source_path = resolve_clip_source_path(clip)
    if not source_path or not source_path.exists():
        raise HTTPException(status_code=400, detail="Original source video chunk not found on disk")

    segments = None
    if clip.get("caption_data"):
        try:
            cap = json.loads(clip["caption_data"])
            groups = cap.get("groups", [])
            if groups:
                segments = [{"id": g["id"], "start": g["start"], "end": g["end"], "text": g.get("text", ""),
                             "words": g.get("words", [])} for g in groups]
        except Exception:
            pass

    if not segments and clip.get("chunk_id"):
        with db.get_db_connection() as conn:
            t_row = conn.execute("SELECT segments_json FROM transcripts WHERE chunk_id = ?", (clip["chunk_id"],)).fetchone()
            if t_row and t_row["segments_json"]:
                try:
                    segments = json.loads(t_row["segments_json"])
                except Exception:
                    pass

    start_t = float(clip["start_time"])
    end_t = min(float(clip["end_time"]), start_t + MAX_CLIP_DURATION)

    try:
        render_res = render_clip(
            source_video=source_path,
            clip_id=clip_id,
            start_time=start_t,
            end_time=end_t,
            aspect_ratio=ctx["source_aspect_ratio"],
            layout_mode=target_layout,
            segments=segments
        )
        out_file = Path(render_res["video_path"])
        if not out_file.exists() or out_file.stat().st_size == 0:
            raise RuntimeError("Render output file is missing or zero bytes")

        with db.get_db_connection() as conn:
            conn.execute(
                "UPDATE clips SET layout_mode = ?, rendered_layout_mode = ?, video_path = ? WHERE id = ?",
                (target_layout, target_layout, str(out_file), clip_id)
            )
            conn.commit()
    except Exception as e:
        logger.error("Failed to render clip %s: %s", clip_id, e)
        raise HTTPException(status_code=500, detail=f"Couldn't render selected framing: {str(e)}")

    updated_clip = db.get_clip_by_id(clip_id)
    updated_ctx = get_clip_framing_context(updated_clip)

    return {
        "status": "success",
        "clip_id": clip_id,
        "layout_mode": target_layout,
        "source_orientation": updated_ctx["source_orientation"],
        "target_aspect": updated_ctx["target_aspect"],
        "available_layout_modes": updated_ctx["available_layout_modes"],
        "rendered_layout_mode": target_layout,
        "video_url": f"/clips/{out_file.name}",
        "is_render_stale": False
    }


@app.post("/api/clips/{clip_id}/approve")
async def handle_approve_clip(clip_id: str, payload: ApproveRequest):
    """Approve a clip and enqueue it to the publishing outbox.
    Ensures rendered media matches the chosen layout_mode before approval.
    """
    clip = db.get_clip_by_id(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    ctx = get_clip_framing_context(clip)
    raw_mode = payload.layout_mode or clip.get("layout_mode") or ctx["layout_mode"]
    raw_mode = str(raw_mode).strip().lower()
    if raw_mode not in ctx["valid_mode_ids"]:
        raw_mode = ctx["layout_mode"]

    target_layout = raw_mode
    rendered_layout = clip.get("rendered_layout_mode")
    vpath = Path(clip["video_path"]) if clip.get("video_path") else None

    # Only re-render if:
    # (a) rendered_layout is recorded and differs from target_layout (explicit framing change)
    # OR
    # (b) video_path is missing or empty, AND source_path is available to render it
    source_path = resolve_clip_source_path(clip)
    needs_rerender = False
    if rendered_layout and not is_matching_layout(rendered_layout, target_layout):
        needs_rerender = True
    elif (vpath is None or not vpath.exists() or vpath.stat().st_size == 0) and source_path and source_path.exists():
        needs_rerender = True

    if needs_rerender:
        logger.info("Rendered media for clip %s requires re-rendering (rendered: %s, target: %s).",
                    clip_id, rendered_layout, target_layout)
        if not source_path or not source_path.exists():
            raise HTTPException(
                status_code=400,
                detail="Cannot render selected framing: original source video chunk not found on disk."
            )

        segments = None
        if clip.get("caption_data"):
            try:
                cap = json.loads(clip["caption_data"])
                groups = cap.get("groups", [])
                if groups:
                    segments = [{"id": g["id"], "start": g["start"], "end": g["end"], "text": g.get("text", ""),
                                 "words": g.get("words", [])} for g in groups]
            except Exception:
                pass

        if not segments and clip.get("chunk_id"):
            with db.get_db_connection() as conn:
                t_row = conn.execute("SELECT segments_json FROM transcripts WHERE chunk_id = ?", (clip["chunk_id"],)).fetchone()
                if t_row and t_row["segments_json"]:
                    try:
                        segments = json.loads(t_row["segments_json"])
                    except Exception:
                        pass

        start_t = float(clip["start_time"])
        end_t = min(float(clip["end_time"]), start_t + MAX_CLIP_DURATION)

        try:
            render_res = render_clip(
                source_video=source_path,
                clip_id=clip_id,
                start_time=start_t,
                end_time=end_t,
                aspect_ratio=ctx["source_aspect_ratio"],
                layout_mode=target_layout,
                segments=segments
            )
            out_file = Path(render_res["video_path"])
            if not out_file.exists() or out_file.stat().st_size == 0:
                raise RuntimeError("Render output file is missing or zero bytes")
        except Exception as e:
            logger.error("Failed to render clip %s with layout %s: %s", clip_id, target_layout, e)
            raise HTTPException(
                status_code=500,
                detail=f"Couldn't render selected framing: {str(e)}"
            )

    db.approve_clip(
        clip_id=clip_id,
        custom_title=payload.title,
        custom_tags=payload.hashtags,
        custom_mode=payload.publish_mode,
        custom_platforms=payload.platforms,
        custom_layout=target_layout
    )

    # Trigger immediate outbox pass so clip publishing starts immediately (unless disabled in tests)
    if not os.getenv("DISPATCH_DISABLE_AUTO_OUTBOX"):
        try:
            from dispatch.publisher.outbox import trigger_outbox_pass
            trigger_outbox_pass()
        except Exception as e:
            logger.warning("Could not trigger immediate outbox pass for clip %s: %s", clip_id, e)

    return {"status": "success", "message": f"Clip {clip_id} approved and enqueued for publishing", "layout_mode": target_layout}


@app.post("/api/clips/{clip_id}/reject")
async def handle_reject_clip(clip_id: str, payload: RejectRequest):
    """Reject a clip and record negative preference learning data."""
    db.reject_clip(clip_id=clip_id, reason=payload.reason or "User rejected")
    return {"status": "success", "message": f"Clip {clip_id} rejected"}


@app.get("/api/clips/{clip_id}/filmstrip")
async def get_clip_filmstrip(clip_id: str):
    """Return an array of thumbnail frames spanning the video duration for the timeline filmstrip."""
    clip = db.get_clip_by_id(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    # Check for existing pre-extracted frames
    existing_frames = []
    for i in range(12):
        frame_name = f"{clip_id}_frame_{i}.jpg"
        if (CLIPS_DIR / frame_name).exists():
            existing_frames.append(f"/clips/{frame_name}")

    if len(existing_frames) >= 4:
        return {"frames": existing_frames}

    # Extract dynamically if video exists on disk
    vpath = clip.get("video_path")
    if vpath and Path(vpath).exists():
        count = 8
        dur = max(1.0, float(clip.get("duration") or 10.0))
        frames = []
        for i in range(count):
            t = (i + 0.5) * (dur / count)
            frame_name = f"{clip_id}_frame_{i}.jpg"
            out_path = CLIPS_DIR / frame_name
            try:
                subprocess.run([
                    "ffmpeg", "-y", "-ss", str(t), "-i", str(vpath),
                    "-frames:v", "1", "-q:v", "3", "-vf", "scale=160:-1",
                    str(out_path)
                ], capture_output=True, timeout=5)
                if out_path.exists():
                    frames.append(f"/clips/{frame_name}")
            except Exception:
                pass
        if frames:
            return {"frames": frames}

    # Fallback to clip thumbnail if single frame
    thumb = clip.get("thumbnail_path")
    if thumb and (CLIPS_DIR / Path(thumb).name).exists():
        return {"frames": [f"/clips/{Path(thumb).name}"]}

    return {"frames": []}


@app.get("/api/presets/captions")
async def get_caption_presets():
    """Return all curated CapCut/viral caption style presets."""
    return {
        preset_id: {
            "id": data["id"],
            "name": data["name"],
            "description": data["description"],
            "style": data["style"].__dict__,
            "animation": data["animation"].__dict__,
            "layout": data["layout"].__dict__,
        }
        for preset_id, data in CAPTION_PRESETS.items()
    }


@app.get("/api/clips/{clip_id}/captions")
async def get_clip_captions(clip_id: str):
    """Retrieve canonical CaptionTrack JSON for a clip. Auto-generates from transcript if missing."""
    cached_json = db.get_clip_caption_data(clip_id)
    if cached_json:
        try:
            return json.loads(cached_json)
        except Exception:
            pass

    clip = db.get_clip_by_id(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    chunk_id = clip.get("chunk_id")
    session_id = clip.get("session_id")

    # Fetch transcript segments strictly matching this clip's chunk or session
    segments = []
    with db.get_db_connection() as conn:
        cursor = conn.cursor()

        # 1. Direct match on chunk_id in transcripts
        if chunk_id:
            cursor.execute("SELECT segments_json FROM transcripts WHERE chunk_id = ?", (chunk_id,))
            t_row = cursor.fetchone()
            if t_row and t_row["segments_json"]:
                try:
                    segments = json.loads(t_row["segments_json"])
                except Exception:
                    segments = []

        # 2. Match on youtube_inbox via chunk's file_hash (e.g. yt_{video_id})
        if not segments and chunk_id:
            cursor.execute("SELECT file_hash FROM chunks WHERE id = ?", (chunk_id,))
            chk_row = cursor.fetchone()
            if chk_row and chk_row["file_hash"] and str(chk_row["file_hash"]).startswith("yt_"):
                yt_vid = str(chk_row["file_hash"])[3:]
                cursor.execute("SELECT segments_json FROM youtube_inbox WHERE video_id = ?", (yt_vid,))
                yt_row = cursor.fetchone()
                if yt_row and yt_row["segments_json"]:
                    try:
                        segments = json.loads(yt_row["segments_json"])
                    except Exception:
                        segments = []

        # 3. Match on session_id in transcripts
        if not segments and session_id:
            cursor.execute("SELECT segments_json FROM transcripts WHERE session_id = ? LIMIT 1", (session_id,))
            s_row = cursor.fetchone()
            if s_row and s_row["segments_json"]:
                try:
                    segments = json.loads(s_row["segments_json"])
                except Exception:
                    segments = []

        # 4. Match on youtube_inbox by dispatch_id or video_id matching session_id
        if not segments and session_id:
            cursor.execute("SELECT segments_json FROM youtube_inbox WHERE dispatch_id = ? OR video_id = ? LIMIT 1", (session_id, session_id))
            yts_row = cursor.fetchone()
            if yts_row and yts_row["segments_json"]:
                try:
                    segments = json.loads(yts_row["segments_json"])
                except Exception:
                    segments = []

    # If no transcript exists for that specific source, handle honestly instead of fabricating words
    if not segments:
        preset_data = get_preset("yellow_pop")
        empty_track = CaptionTrack(
            clip_id=clip_id,
            language="en",
            groups=[],
            default_style=preset_data["style"],
            default_layout=preset_data["layout"],
            default_animation=preset_data["animation"]
        )
        return empty_track.to_dict()

    # Transliterate Devanagari Hindi text to natural Roman Hinglish with 1:1 token mapping
    clean_segments = transliterate_segments_to_hinglish(segments)

    clip_start = float(clip.get("start_time", 0.0))
    clip_end = float(clip.get("end_time", clip_start + 15.0))
    # Hard clamp duration to MAX_CLIP_DURATION (180s)
    if (clip_end - clip_start) > MAX_CLIP_DURATION:
        clip_end = clip_start + MAX_CLIP_DURATION

    track = group_words_into_captions(
        raw_segments=clean_segments,
        clip_start=clip_start,
        clip_end=clip_end,
        clip_id=clip_id,
        preset_id="yellow_pop"
    )

    track_dict = track.to_dict()
    db.save_clip_caption_data(clip_id, json.dumps(track_dict))

    # Export ASS file with exact parity
    ass_path = PROCESSING_DIR / f"{clip_id}.ass"
    try:
        export_to_ass(track, ass_path)
    except Exception as e:
        logger.warning("Failed to export ASS subtitles for clip %s: %s", clip_id, e)

    return track_dict


@app.post("/api/clips/{clip_id}/captions")
async def save_clip_captions(clip_id: str, request: Request):
    """Save customized CaptionTrack JSON and export updated ASS file for FFmpeg render parity."""
    data = await request.json()
    track = CaptionTrack.from_dict(data)
    track_dict = track.to_dict()
    db.save_clip_caption_data(clip_id, json.dumps(track_dict))

    # Export ASS file with exact parity
    ass_path = Path("storage/processing") / f"{clip_id}.ass"
    export_to_ass(track, ass_path)

    return {"status": "success", "message": "Captions saved and exported to ASS"}


@app.post("/api/recordings/upload")
async def upload_web_recording(file: UploadFile = File(...)):
    """Accept test recording from browser camera, save to storage, register session and chunk, extract clip, and prepare for review."""
    import subprocess
    import shutil
    from dispatch.ingestion.validator import inspect_video_orientation, calculate_file_hash

    timestamp = int(time.time())
    session_id = f"sess_web_{timestamp}"
    chunk_id = f"chk_web_{timestamp}"
    clip_id = f"clip_web_{timestamp}"

    # 1. Save uploaded WebM file
    content = await file.read()
    temp_webm = PROCESSING_DIR / f"{chunk_id}.webm"
    with open(temp_webm, "wb") as f:
        f.write(content)

    # 2. Standardize raw source chunk to MP4 in PROCESSING_DIR
    chunk_path = PROCESSING_DIR / f"{chunk_id}.mp4"
    cmd = [
        "ffmpeg", "-y", "-i", str(temp_webm),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(chunk_path)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if temp_webm.exists():
        temp_webm.unlink(missing_ok=True)

    if not chunk_path.exists() or chunk_path.stat().st_size == 0:
        raise HTTPException(status_code=500, detail="Failed to transcode uploaded recording.")

    # 3. Inspect video orientation, dimensions, and duration
    info = inspect_video_orientation(chunk_path)
    width = info.get("effective_width") or info.get("width") or 1080
    height = info.get("effective_height") or info.get("height") or 1920
    orientation = info.get("orientation", "portrait")
    aspect_ratio = "9:16" if orientation == "portrait" else "16:9"
    duration = max(1.0, float(info.get("duration") or 30.0))
    file_hash = calculate_file_hash(chunk_path)

    # 4. Prepare initial clip in CLIPS_DIR
    clip_video_path = CLIPS_DIR / f"{clip_id}.mp4"
    shutil.copy2(chunk_path, clip_video_path)

    # Generate thumbnail
    thumb_path = CLIPS_DIR / f"{clip_id}.jpg"
    thumb_time = min(1.0, duration / 2.0)
    subprocess.run([
        "ffmpeg", "-y", "-ss", str(round(thumb_time, 3)), "-i", str(clip_video_path),
        "-vframes", "1", "-q:v", "2", str(thumb_path)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Extract filmstrip frames for the timeline
    for i in range(8):
        t = (i + 0.5) * (duration / 8)
        frame_name = f"{clip_id}_frame_{i}.jpg"
        subprocess.run([
            "ffmpeg", "-y", "-ss", str(round(t, 3)), "-i", str(clip_video_path),
            "-frames:v", "1", "-q:v", "3", "-vf", "scale=160:-1",
            str(CLIPS_DIR / frame_name)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Initial layout mode: portrait -> native_916, landscape -> crop_916
    initial_layout = "native_916" if orientation == "portrait" else "crop_916"

    # 5. Insert into sessions, chunks, and clips tables atomically
    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO sessions (id, status) VALUES (?, 'completed')", (session_id,))
        cursor.execute("""
            INSERT OR REPLACE INTO chunks (
                id, session_id, filename, filepath, file_hash, duration,
                width, height, aspect_ratio, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'processed')
        """, (chunk_id, session_id, chunk_path.name, str(chunk_path), file_hash, duration, width, height, aspect_ratio))
        cursor.execute("""
            INSERT OR REPLACE INTO clips (
                id, session_id, chunk_id, start_time, end_time, duration,
                title, hook, description, hashtags, virality_score,
                layout_mode, rendered_layout_mode, status, publish_mode, platform_targets,
                video_path, thumbnail_path
            ) VALUES (
                ?, ?, ?, 0.0, ?, ?,
                'Web Studio Recording', 'Consistency beats motivation every single day',
                'Recorded directly from Dispatch Web Studio. #Shorts #Dispatch',
                '#Shorts #Dispatch #Creator', 92,
                ?, ?, 'ready_review', 'public', 'youtube,instagram',
                ?, ?
            )
        """, (
            clip_id, session_id, chunk_id, duration, duration,
            initial_layout, initial_layout,
            str(clip_video_path), str(thumb_path) if thumb_path.exists() else None
        ))
        conn.commit()

    return {
        "status": "success",
        "clip_id": clip_id,
        "session_id": session_id,
        "chunk_id": chunk_id,
        "video_url": f"/clips/{clip_video_path.name}",
        "duration": duration,
        "message": "Recording uploaded and ready for review!"
    }


@app.get("/api/recordings")
async def get_recordings():
    """Return list of recordings/chunks formatted for the Sessions tab."""
    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                c.id as chunk_id,
                c.session_id,
                c.filename,
                c.filepath,
                c.duration,
                c.status as chunk_status,
                c.created_at,
                cl.id as clip_id,
                cl.video_path,
                s.status as session_status
            FROM chunks c
            LEFT JOIN sessions s ON c.session_id = s.id
            LEFT JOIN clips cl ON cl.chunk_id = c.id
            ORDER BY c.created_at DESC
        """)
        rows = cursor.fetchall()

    recordings = []
    for r in rows:
        chunk_id = r["chunk_id"]
        session_id = r["session_id"] or f"sess_{chunk_id}"
        st = "UPLOADED_TO_YOUTUBE" if r["chunk_status"] in ("processed", "transcribed", "verified") else "QUEUED_FOR_UPLOAD"
        
        video_url = None
        if r["clip_id"] and r["video_path"]:
            video_url = f"/clips/{Path(r['video_path']).name}"
        elif r["filepath"] and Path(r["filepath"]).exists():
            video_url = f"/api/clips/{chunk_id}/source"

        file_size = 0
        try:
            if r["filepath"]:
                fp = Path(r["filepath"])
                if fp.exists():
                    file_size = fp.stat().st_size
        except Exception:
            pass

        created_ts = int(time.time() * 1000)
        try:
            if r["created_at"]:
                dt = datetime.fromisoformat(str(r["created_at"]).replace(" ", "T"))
                created_ts = int(dt.timestamp() * 1000)
        except Exception:
            pass

        recordings.append({
            "segmentId": chunk_id,
            "sessionId": session_id,
            "sequenceNumber": 1,
            "filename": r["filename"] or f"{chunk_id}.mp4",
            "filepath": r["filepath"] or "",
            "fileSizeBytes": file_size,
            "sha256Hash": "",
            "status": st,
            "createdAt": created_ts,
            "finalizedAt": created_ts,
            "durationSeconds": float(r["duration"] or 0),
            "videoUrl": video_url,
        })

    return recordings


@app.get("/api/settings")
async def get_settings():
    return {
        "publish_mode": db.get_setting("publish_mode", DEFAULT_PUBLISH_MODE),
        "auto_process": db.get_setting("auto_process", "true")
    }


@app.post("/api/settings")
async def update_settings(payload: SettingsRequest):
    if payload.publish_mode:
        db.set_setting("publish_mode", payload.publish_mode)
    if payload.auto_process:
        db.set_setting("auto_process", payload.auto_process)
    return {"status": "success", "message": "Settings updated"}


@app.get("/api/youtube/inbox")
async def get_youtube_inbox_items():
    """List YouTube Inbox items and their current processing status."""
    return {"items": db.list_youtube_inbox(limit=30)}


@app.post("/api/youtube/ingest")
async def ingest_youtube_video(payload: YouTubeIngestRequest):
    """Trigger on-demand download, transcription, and clip rendering from a YouTube video URL/ID."""
    video_id = extract_youtube_video_id(payload.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL or Video ID")

    # Start background processing thread so API returns immediately to client
    catcher = YouTubeInboxCatcher()

    def _run_bg_catcher():
        try:
            catcher.ingest_video(payload.url)
        except Exception as e:
            logger.error("Error in background YouTube ingestion for %s: %s", video_id, e)

    t = threading.Thread(target=_run_bg_catcher, daemon=True)
    t.start()

    return {
        "status": "processing_queued",
        "video_id": video_id,
        "message": f"YouTube video {video_id} accepted for Pillar 1 ingestion (download & media validation)."
    }


# --- Integrations & Authentication APIs ---

class GeminiKeyRequest(BaseModel):
    api_key: str


@app.get("/api/integrations/status")
async def get_integrations_status():
    """Return live status of YouTube OAuth and Gemini AI integrations."""
    from dispatch.youtube_inbox.oauth import get_youtube_service
    from dispatch.config import GEMINI_API_KEY

    yt_service = get_youtube_service()
    yt_connected = yt_service is not None
    channel_title = ""
    channel_id = ""
    if yt_connected:
        try:
            resp = yt_service.channels().list(mine=True, part="snippet").execute()
            items = resp.get("items", [])
            if items:
                channel_title = items[0]["snippet"].get("title", "")
                channel_id = items[0].get("id", "")
        except Exception as e:
            logger.debug("Could not fetch channel snippet: %s", e)

    client_secrets_exist = (ROOT_DIR / "client_secrets.json").exists()
    token_exists = (ROOT_DIR / "youtube_token.json").exists()

    gemini_key = GEMINI_API_KEY or os.getenv("GEMINI_API_KEY", "")
    gemini_configured = bool(gemini_key and len(gemini_key) > 15 and (gemini_key.startswith("AIzaSy") or gemini_key.startswith("AQ.")))

    return {
        "youtube": {
            "connected": yt_connected,
            "channel_title": channel_title,
            "channel_id": channel_id,
            "has_client_secrets": client_secrets_exist,
            "has_token": token_exists
        },
        "gemini": {
            "configured": gemini_configured,
            "masked_key": f"{gemini_key[:8]}...{gemini_key[-4:]}" if len(gemini_key) > 12 else ""
        }
    }


@app.post("/api/integrations/gemini")
async def update_gemini_key(payload: GeminiKeyRequest):
    """Save and activate a Gemini API key directly from the dashboard."""
    new_key = payload.api_key.strip()
    if not new_key:
        raise HTTPException(status_code=400, detail="API key cannot be empty")

    env_path = ROOT_DIR / ".env"
    env_content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    if "GEMINI_API_KEY=" in env_content:
        env_content = re.sub(r"GEMINI_API_KEY=.*", f"GEMINI_API_KEY={new_key}", env_content)
    else:
        env_content += f"\nGEMINI_API_KEY={new_key}\n"
    env_path.write_text(env_content, encoding="utf-8")

    import dispatch.config
    dispatch.config.GEMINI_API_KEY = new_key
    import dispatch.ai_clips.highlight_finder
    dispatch.ai_clips.highlight_finder.GEMINI_API_KEY = new_key

    return {"status": "success", "message": "Gemini API key saved and activated"}


@app.post("/api/integrations/youtube/start-auth")
async def start_youtube_auth():
    """Trigger the YouTube OAuth login flow."""
    client_secrets = ROOT_DIR / "client_secrets.json"
    if not client_secrets.exists():
        raise HTTPException(
            status_code=400,
            detail="client_secrets.json not found. Please ensure Google OAuth Client ID is configured."
        )

    import subprocess
    cmd = ["python", str(ROOT_DIR / "scripts" / "auth_youtube.py")]
    subprocess.Popen(cmd, cwd=str(ROOT_DIR))

    return {
        "status": "started",
        "message": "Authentication window started. Authorize in your browser."
    }


@app.post("/api/integrations/youtube/disconnect")
async def disconnect_youtube():
    """Disconnect YouTube by removing the cached token."""
    token_file = ROOT_DIR / "youtube_token.json"
    if token_file.exists():
        token_file.unlink()
    return {"status": "success", "message": "YouTube account disconnected"}


@app.get("/api/youtube/credentials")
async def get_youtube_credentials(
    request: Request,
    authorization: Optional[str] = Header(None)
):
    """Return YouTube OAuth client credentials and refresh token for mobile app pairing.
    Protected: Accessible only via localhost / loopback OR with valid Authorization token.
    """
    client_host = request.client.host if request.client else "unknown"
    is_localhost = client_host in ("127.0.0.1", "::1", "localhost", "testclient")

    if not is_localhost:
        from dispatch.sync.receiver import verify_token
        token = authorization or request.query_params.get("auth_token")
        verify_token(token)
    token_file = ROOT_DIR / "youtube_token.json"
    client_secrets = ROOT_DIR / "client_secrets.json"

    refresh_token = ""
    client_id = ""
    client_secret = ""

    if token_file.exists():
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                refresh_token = data.get("refresh_token", "")
                client_id = data.get("client_id", "")
                client_secret = data.get("client_secret", "")
        except Exception as e:
            logger.error("Failed to read youtube_token.json: %s", e)

    if (not client_id or not client_secret) and client_secrets.exists():
        try:
            with open(client_secrets, "r", encoding="utf-8") as f:
                cs = json.load(f)
                installed = cs.get("installed", {}) or cs.get("web", {})
                if not client_id:
                    client_id = installed.get("client_id", "")
                if not client_secret:
                    client_secret = installed.get("client_secret", "")
        except Exception as e:
            logger.error("Failed to read client_secrets.json: %s", e)

    return {
        "configured": bool(refresh_token and client_id and client_secret),
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret
    }



# --- Embedded HTML Dashboard ---

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Dispatch | Autonomous Content Control</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    body { background-color: #0B0F17; color: #F1F5F9; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .surface { background-color: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.08); }
    .surface-input { background-color: rgba(0, 0, 0, 0.4); border: 1px solid rgba(255, 255, 255, 0.1); }
  </style>
</head>
<body class="min-h-screen antialiased flex flex-col">

  <!-- Header -->
  <header class="border-b border-white/10 bg-[#0B0F17]/90 backdrop-blur sticky top-0 z-50 px-4 lg:px-8 py-3.5 flex items-center justify-between">
    <div class="flex items-center space-x-3">
      <div class="w-8 h-8 rounded bg-indigo-600/20 border border-indigo-500/40 flex items-center justify-center">
        <svg class="w-4 h-4 text-indigo-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z"/>
        </svg>
      </div>
      <div>
        <h1 class="text-sm font-semibold tracking-wider uppercase text-white">Dispatch</h1>
        <p class="text-[11px] text-slate-400 font-mono">Autonomous Engine Active</p>
      </div>
    </div>

    <!-- Quick Stats & Settings -->
    <div class="flex items-center space-x-3 text-xs">
      <div class="hidden sm:flex items-center space-x-2 px-3 py-1.5 rounded surface text-slate-300">
        <span class="w-2 h-2 rounded-full bg-emerald-400"></span>
        <span id="disk-info" class="font-mono text-[11px]">Disk: -- GB free</span>
      </div>

      <div class="flex items-center bg-black/40 border border-white/10 rounded p-0.5">
        <button id="mode-private-btn" onclick="setPublishMode('private')" class="px-2.5 py-1 text-[11px] font-medium rounded transition">
          Private / Draft
        </button>
        <button id="mode-public-btn" onclick="setPublishMode('public')" class="px-2.5 py-1 text-[11px] font-medium rounded transition">
          Auto Public
        </button>
      </div>
    </div>
  </header>

  <!-- Main Content -->
  <main class="flex-1 max-w-7xl w-full mx-auto px-4 lg:px-8 py-6 space-y-6">

    <!-- KPI Metrics -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
      <div class="surface rounded-lg p-3.5 flex flex-col justify-between">
        <span class="text-[11px] uppercase tracking-wider text-slate-400 font-medium">Ready For Review</span>
        <span id="stat-ready" class="text-2xl font-semibold tracking-tight text-white mt-1">0</span>
      </div>
      <div class="surface rounded-lg p-3.5 flex flex-col justify-between">
        <span class="text-[11px] uppercase tracking-wider text-slate-400 font-medium">Approved / Queued</span>
        <span id="stat-approved" class="text-2xl font-semibold tracking-tight text-indigo-400 mt-1">0</span>
      </div>
      <div class="surface rounded-lg p-3.5 flex flex-col justify-between">
        <span class="text-[11px] uppercase tracking-wider text-slate-400 font-medium">Published</span>
        <span id="stat-published" class="text-2xl font-semibold tracking-tight text-emerald-400 mt-1">0</span>
      </div>
      <div class="surface rounded-lg p-3.5 flex flex-col justify-between">
        <span class="text-[11px] uppercase tracking-wider text-slate-400 font-medium">Storage Health</span>
        <span id="stat-storage" class="text-2xl font-semibold tracking-tight text-slate-200 mt-1">--%</span>
      </div>
    </div>

    <!-- Review Section Header -->
    <div class="flex items-center justify-between pt-2">
      <div>
        <h2 class="text-base font-semibold text-white">Clips Awaiting Approval</h2>
        <p class="text-xs text-slate-400 mt-0.5">Review generated short-form clips, edit tags, and dispatch.</p>
      </div>
      <button onclick="fetchClips()" class="text-xs px-3 py-1.5 rounded surface hover:bg-white/[0.06] text-slate-300 flex items-center space-x-1.5 transition">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
        </svg>
        <span>Refresh</span>
      </button>
    </div>

    <!-- Clips Grid -->
    <div id="clips-container" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
      <!-- Dynamic Clip Cards injected here -->
    </div>

    <!-- Empty State -->
    <div id="empty-state" class="hidden surface rounded-lg p-12 text-center">
      <svg class="w-10 h-10 mx-auto text-slate-500 mb-3" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"/>
      </svg>
      <h3 class="text-sm font-medium text-slate-200">No clips pending review</h3>
      <p class="text-xs text-slate-400 mt-1 max-w-sm mx-auto">When your phone completes recording and syncs to your PC, Dispatch automatically cuts and subtitles candidates here.</p>
    </div>

  </main>

  <script>
    let currentPublishMode = "private";

    async function loadStatus() {
      try {
        const res = await fetch("/api/status");
        const data = await res.json();
        document.getElementById("stat-ready").innerText = data.ready_clips;
        document.getElementById("stat-approved").innerText = data.approved_clips;
        document.getElementById("stat-published").innerText = data.published_clips;
        document.getElementById("stat-storage").innerText = data.disk_percent_used + "%";
        document.getElementById("disk-info").innerText = `Disk: ${data.disk_free_gb} GB free`;
        currentPublishMode = data.publish_mode;
        updateModeUI();
      } catch (e) {
        console.error("Status load failed", e);
      }
    }

    function updateModeUI() {
      const privBtn = document.getElementById("mode-private-btn");
      const pubBtn = document.getElementById("mode-public-btn");
      if (currentPublishMode === "public") {
        pubBtn.className = "px-2.5 py-1 text-[11px] font-medium rounded bg-indigo-600 text-white shadow";
        privBtn.className = "px-2.5 py-1 text-[11px] font-medium rounded text-slate-400 hover:text-white";
      } else {
        privBtn.className = "px-2.5 py-1 text-[11px] font-medium rounded bg-slate-700 text-white shadow";
        pubBtn.className = "px-2.5 py-1 text-[11px] font-medium rounded text-slate-400 hover:text-white";
      }
    }

    async function setPublishMode(mode) {
      currentPublishMode = mode;
      updateModeUI();
      await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ publish_mode: mode })
      });
    }

    async function fetchClips() {
      loadStatus();
      try {
        const res = await fetch("/api/clips");
        const data = await res.json();
        const container = document.getElementById("clips-container");
        const emptyState = document.getElementById("empty-state");

        container.innerHTML = "";
        if (!data.ready || data.ready.length === 0) {
          emptyState.classList.remove("hidden");
          return;
        }
        emptyState.classList.add("hidden");

        data.ready.forEach(clip => {
          const card = document.createElement("div");
          card.className = "surface rounded-lg overflow-hidden flex flex-col justify-between";
          card.id = `clip-card-${clip.id}`;

          const videoSrc = clip.video_url || "";
          const thumbSrc = clip.thumbnail_url || "";

          card.innerHTML = `
            <div class="relative bg-black aspect-[9/16] max-h-[380px] w-full overflow-hidden flex items-center justify-center">
              <video src="${videoSrc}" poster="${thumbSrc}" controls preload="metadata" class="w-full h-full object-contain"></video>
              <div class="absolute top-2 right-2 px-2 py-0.5 rounded bg-black/70 backdrop-blur border border-white/10 text-[10px] font-mono text-emerald-400">
                Score: ${clip.virality_score}/100
              </div>
            </div>

            <div class="p-4 space-y-3 flex-1 flex flex-col justify-between">
              <div class="space-y-2">
                <div>
                  <label class="text-[10px] uppercase tracking-wider text-slate-400 font-semibold block mb-1">Title (Roman Hinglish)</label>
                  <input type="text" id="title-${clip.id}" value="${clip.title || ''}" class="w-full text-xs px-2.5 py-1.5 rounded surface-input text-white focus:outline-none focus:border-indigo-500">
                </div>

                <div>
                  <label class="text-[10px] uppercase tracking-wider text-slate-400 font-semibold block mb-1">Hashtags</label>
                  <input type="text" id="tags-${clip.id}" value="${clip.hashtags || ''}" class="w-full text-xs px-2.5 py-1.5 rounded surface-input text-slate-300 focus:outline-none focus:border-indigo-500">
                </div>

                <div class="flex items-center space-x-3 text-[11px] text-slate-400 pt-1">
                  <label class="flex items-center space-x-1.5 cursor-pointer">
                    <input type="checkbox" checked id="yt-${clip.id}" class="rounded border-white/20 text-indigo-600 focus:ring-0">
                    <span>YouTube Shorts</span>
                  </label>
                  <label class="flex items-center space-x-1.5 cursor-pointer">
                    <input type="checkbox" checked id="ig-${clip.id}" class="rounded border-white/20 text-indigo-600 focus:ring-0">
                    <span>Instagram Reels</span>
                  </label>
                </div>
              </div>

              <!-- Action Buttons -->
              <div class="grid grid-cols-2 gap-2 pt-2 border-t border-white/5">
                <button onclick="approveClip('${clip.id}')" class="h-10 text-xs font-semibold rounded bg-emerald-600/90 hover:bg-emerald-500 active:scale-95 text-white transition flex items-center justify-center space-x-1">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/>
                  </svg>
                  <span>Approve</span>
                </button>
                <button onclick="rejectClip('${clip.id}')" class="h-10 text-xs font-semibold rounded bg-rose-600/20 hover:bg-rose-600/30 active:scale-95 text-rose-300 border border-rose-500/30 transition flex items-center justify-center space-x-1">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
                  </svg>
                  <span>Reject</span>
                </button>
              </div>
            </div>
          `;
          container.appendChild(card);
        });
      } catch (e) {
        console.error("Fetch clips error", e);
      }
    }

    async function approveClip(clipId) {
      const title = document.getElementById(`title-${clipId}`).value;
      const tags = document.getElementById(`tags-${clipId}`).value;
      const yt = document.getElementById(`yt-${clipId}`).checked;
      const ig = document.getElementById(`ig-${clipId}`).checked;

      const platforms = [];
      if (yt) platforms.push("youtube");
      if (ig) platforms.push("instagram");

      const card = document.getElementById(`clip-card-${clipId}`);
      if (card) card.style.opacity = "0.4";

      try {
        await fetch(`/api/clips/${clipId}/approve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title: title,
            hashtags: tags,
            publish_mode: currentPublishMode,
            platforms: platforms.join(",")
          })
        });
        if (card) card.remove();
        loadStatus();
      } catch (e) {
        alert("Approve failed: " + e);
        if (card) card.style.opacity = "1";
      }
    }

    async function rejectClip(clipId) {
      const card = document.getElementById(`clip-card-${clipId}`);
      if (card) card.style.opacity = "0.4";

      try {
        await fetch(`/api/clips/${clipId}/reject`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: "User tapped reject" })
        });
        if (card) card.remove();
        loadStatus();
      } catch (e) {
        alert("Reject failed: " + e);
        if (card) card.style.opacity = "1";
      }
    }

    // Auto-refresh every 10 seconds
    setInterval(fetchClips, 10000);
    fetchClips();
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def dashboard_home():
    """Serve the single-page dashboard HTML."""
    template_path = Path(__file__).parent / "templates" / "index.html"
    if template_path.exists():
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content=DASHBOARD_HTML)


# Brand & Favicon Static Assets
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/favicon.ico", include_in_schema=False)
async def get_favicon_ico():
    ico = STATIC_DIR / "favicon.ico"
    if ico.exists():
        return FileResponse(ico, media_type="image/x-icon")
    raise HTTPException(status_code=404, detail="Favicon not found")


@app.get("/favicon.svg", include_in_schema=False)
async def get_favicon_svg():
    svg = STATIC_DIR / "favicon.svg"
    if svg.exists():
        return FileResponse(svg, media_type="image/svg+xml")
    raise HTTPException(status_code=404, detail="Favicon not found")


@app.get("/apple-touch-icon.png", include_in_schema=False)
async def get_apple_touch_icon():
    png = STATIC_DIR / "apple-touch-icon.png"
    if png.exists():
        return FileResponse(png, media_type="image/png")
    raise HTTPException(status_code=404, detail="Apple touch icon not found")


@app.get("/site.webmanifest", include_in_schema=False)
async def get_site_manifest():
    manifest = STATIC_DIR / "site.webmanifest"
    if manifest.exists():
        return FileResponse(manifest, media_type="application/manifest+json")
    raise HTTPException(status_code=404, detail="Manifest not found")

