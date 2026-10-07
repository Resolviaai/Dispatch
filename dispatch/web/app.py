"""FastAPI Web Dashboard for Dispatch.
Provides the local PC dashboard for reviewing, approving, and publishing clips.
"""
import os
import psutil
import logging
import threading
import time
import re
from pathlib import Path
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from pydantic import BaseModel

from dispatch.config import (
    CLIPS_DIR,
    ROOT_DIR,
    DEFAULT_PUBLISH_MODE
)
from dispatch import db
from dispatch.youtube_inbox.catcher import (
    YouTubeInboxCatcher,
    extract_youtube_video_id
)

logger = logging.getLogger("dispatch.web")

app = FastAPI(title="Dispatch Dashboard", version="1.0.0")

class ApproveRequest(BaseModel):
    title: Optional[str] = None
    hashtags: Optional[str] = None
    publish_mode: Optional[str] = None  # "private" or "public"
    platforms: Optional[str] = None     # "youtube,instagram"


class RejectRequest(BaseModel):
    reason: Optional[str] = "User rejected"


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
    source_dir = ROOT_DIR / "android" / "app" / "src" / "main"
    source_updated_at = max(
        (path.stat().st_mtime for path in source_dir.rglob("*") if path.is_file()),
        default=0,
    )
    if not apk_path.exists() or apk_path.stat().st_mtime < source_updated_at:
        raise HTTPException(
            status_code=404,
            detail="The current Android source has not been built into an APK yet."
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


@app.get("/api/clips")
async def list_clips():
    """List clips grouped by status."""
    ready_clips = db.get_clips_for_review()
    for c in ready_clips:
        if c.get("video_path"):
            c["video_url"] = f"/clips/{Path(c['video_path']).name}"
        if c.get("thumbnail_path"):
            c["thumbnail_url"] = f"/clips/{Path(c['thumbnail_path']).name}"

    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM clips WHERE status IN ('approved', 'published') ORDER BY created_at DESC LIMIT 20")
        published_clips = [dict(r) for r in cursor.fetchall()]
        for c in published_clips:
            if c.get("video_path"):
                c["video_url"] = f"/clips/{Path(c['video_path']).name}"
            if c.get("thumbnail_path"):
                c["thumbnail_url"] = f"/clips/{Path(c['thumbnail_path']).name}"

    return {
        "ready": ready_clips,
        "recent_approved": published_clips
    }


@app.post("/api/clips/{clip_id}/approve")
async def handle_approve_clip(clip_id: str, payload: ApproveRequest):
    """Approve a clip and enqueue it to the publishing outbox."""
    db.approve_clip(
        clip_id=clip_id,
        custom_title=payload.title,
        custom_tags=payload.hashtags,
        custom_mode=payload.publish_mode,
        custom_platforms=payload.platforms
    )
    return {"status": "success", "message": f"Clip {clip_id} approved"}


@app.post("/api/clips/{clip_id}/reject")
async def handle_reject_clip(clip_id: str, payload: RejectRequest):
    """Reject a clip and record negative preference learning data."""
    db.reject_clip(clip_id=clip_id, reason=payload.reason or "User rejected")
    return {"status": "success", "message": f"Clip {clip_id} rejected"}


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
    gemini_configured = bool(gemini_key and len(gemini_key) > 20 and gemini_key.startswith("AIzaSy"))

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
