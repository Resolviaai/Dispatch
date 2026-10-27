# Dispatch Master Architecture & Agent Handover Constitution
> **Target Audience:** Next Agent (`Z code GLM 5.3 flash`) / Engineering Lead  
> **Repository:** `c:\CODE\Dispatch` (`https://github.com/Resolviaai/Dispatch`)  
> **Last Updated Release:** `v1.3.0-alpha` (Android `versionCode: 45`)  
> **Author:** Antigravity (Google DeepMind Pair Programming Assistant)  
> **Date:** October 27, 2026

---

## 1. Executive Product & Architecture Overview

**Dispatch** is an autonomous AI-native vertical video clipping workstation and creator studio. It transforms long, raw talking sessions filmed on an Android phone into polished, captioned, virality-scored 9:16 vertical shorts (YouTube Shorts, Instagram Reels, TikTok, X).

```
 ┌──────────────────────┐        Direct LAN / Cellular        ┌─────────────────────────┐
 │   Android Phone      │ ──────────────────────────────────> │ Local PC Daemon         │
 │ (Jetpack Compose Web │ <────────────────────────────────── │ (FastAPI, PyTorch,     │
 │  WebView + Camera2)  │        Status, Presets, WS          │  Whisper, Gemini 2.5)   │
 └──────────────────────┘                                     └─────────────────────────┘
            │                                                              │
            │ Direct Unlisted Upload / Cellular Backup                     │ Staging Proxies (Mode 2)
            ▼                                                              ▼
 ┌──────────────────────┐                                     ┌─────────────────────────┐
 │ YouTube Ingest Cloud │                                     │ Cloudflare Edge + R2    │
 │ (Cloud Poller Ingest)│ ──────────────────────────────────> │ (Worker Presigned URLs) │
 └──────────────────────┘                                     └─────────────────────────┘
                                                                           │
                                                                           ▼
                                                              ┌─────────────────────────┐
                                                              │ Supabase Cloud DB       │
                                                              │ (Profiles, Cloud Clips) │
                                                              └─────────────────────────┘
```

### The 3 Clip Operating Modes
1. **Mode 1: Gemini Suggestion (Offline Cloud)**  
   Lightweight metadata (titles, hooks, virality scores, suggested start/end ranges, AI rationale). Available in Supabase `clips_cloud` even if the PC laptop is powered off.
2. **Mode 2: Bounded 720p Edit Window (Offline Cloud)**  
   A bounded 720p proxy video (`MAX_WINDOW_SECONDS <= 300s`) staged in Cloudflare R2 (`dispatch-clips`). Allows the creator to scrub, preview, adjust trim ranges, and edit captions on their phone via presigned streaming URLs without needing local PC access.
3. **Mode 3: High-Res Master Render (Local PC Only)**  
   Full-resolution source video composition, multi-track dynamic canvas rendering, hardware-accelerated FFmpeg export (`libx264`/`h264_nvenc`), and direct unlisted/public publishing.

---

## 2. Infrastructure, Cloud & Secret Credentials

All production secrets live in `.env` (git-ignored) and Cloudflare Worker environment variables. **Never leak secret keys or service role tokens to client-side bundles.**

### Cloud Infrastructure
| Service | Environment / Endpoint | Purpose |
|---|---|---|
| **Supabase Project** | `https://aldzlbdxplqkcnyjhzmm.supabase.co` | Auth, profiles, `clips_cloud`, `edit_windows`, `media_objects`, `processing_jobs` |
| **Cloudflare Worker** | `https://dispatch-cloud.resolviaai.workers.dev` | Remote API gateway, AWS SigV4 R2 presigning, OAuth ticketing, token vault |
| **Cloudflare R2** | Bucket: `dispatch-clips` (Account `75d8978aa7345a0df28e89bff1e847ec`) | Video proxy chunks, rendered MP4s, thumbnail JPEGs |
| **GitHub Pages** | `https://resolviaai.github.io/Dispatch/` | Landing page, direct APK download mirror, `update.json` manifest |
| **GitHub Releases** | `https://github.com/Resolviaai/Dispatch/releases` | Release tag downloads (`Dispatch.apk`, `update.json`) |

### Key Auth Headers & RPC Protocols
- **Worker Auth**: Handled via `Authorization: Bearer <DISPATCH_WORKER_TOKEN>` and user context via `X-Dispatch-User-Id: <UUID>`.
- **Client App Auth**: Communicates with Cloudflare Worker via Supabase JWT (`Bearer <access_token>`).
- **Postgres Quota Functions**: `cloud_reserve_upload` and `cloud_complete_upload` enforce 512&nbsp;MB cap for `clip_video` and `edit_window`, and 8&nbsp;MB for `thumbnail`.

---

## 3. UI/UX Constitution & Invariant Rules (`AGENTS.md`)

Every change to the mobile web frontend (`frontend/src/...`) and Android views must adhere to the **Dispatch Design System v3**:

1. **The 4-Layer Elevation Architecture (60/30/10 Rule)**:
   - **Layer 0 (Canvas Root - 60%)**: `#161616` (Deep charcoal studio anchor, `bg-canvas`, `bg-studio`).
   - **Layer 1 (Cards & Elevated Surfaces - 30%)**: `#1C1C1C` with border `#2E2E2E` (`bg-surface`, `bg-surface-100`, `border-border`).
   - **Layer 2 (Recessed Wells & Inputs)**: `#232323` (`bg-surface-2`, `bg-surface-200`) with deeper tracks at `#282828` (`bg-surface-300`).
   - **Layer 3 (Hero Brand CTA & Focus - 10%)**: Electric Creator Blue `#2563EB` (`bg-primary`, hover `#3B82F6`, shadow `shadow-hero-glow`).
   - **Semantic Colors**: Emerald `#22C55E` (Success), Amber `#F59E0B` (Warning), Rose `#EF4444` (Destructive/Error). Never use semantic colors for decorative backgrounds.
2. **The Single Hero CTA Law**: Exactly **one** glowing primary CTA button per screen. Secondary/destructive actions remain recessed neutral surfaces.
3. **Biomechanical 44px Touch Hitboxes**: Every interactive touch target must be at least $44\text{px} \times 44\text{px}$.
4. **Single-Direction Scroll Law**: Every mobile section moves along strictly **one** physical axis (vertical stack OR horizontal card carousel). No 2D multi-axis scrolling.
5. **Zero OS Native Emojis**: Never use native emojis (🚀, 💡, 🔥, ⚡, 📦). Use crisp SVG vector icons (`lucide-react`) with 1.5px–2.0px stroke widths.
6. **Typographic Hierarchy & Numeric Place-Value**:
   - Operational dashboard screens use max 4 font sizes (18px, 14px, 12px, 10px).
   - All numbers, durations, timestamps, and percentages must strictly enforce `font-mono tabular-nums`.
7. **Concentric Curvature Geometry**: Inner corners inside cards follow $R_{\text{inner}} = R_{\text{outer}} - \text{Padding}$.
8. **Caption Studio Invariant**: The caption workstation (`frontend/src/captions/*`, `frontend/src/studio/*`) is deliberately untouched by v3 redesigns. Do not restyle it without explicit instruction.

---

## 4. Android Governance & Release Invariants

1. **Strict `versionCode` Monotonicity**: Android `versionCode` is the **sole update authority**. Every release MUST increment `versionCode` by at least $+1$ ($43 \rightarrow 44$). Reusing a versionCode will reject install.
2. **Multi-File Synchronization**: When releasing, always run `scripts/bump_version.ps1` to atomically update:
   - `android/app/build.gradle.kts`
   - `frontend/package.json`
   - `site/update.json`
   - `site/index.html`
   - `README.md`
3. **Trusted Keystore Signature**:
   - Release signing is governed by `android/keystore.properties` + `android/keystore/dispatch-release.jks`.
   - Trusted SHA-256 Fingerprint: `94f778490fde8ffd2c7e644d0c8169249f2e79829d769b5f71aa0301c5643804`.
   - Verified via `scripts/prepare_release.ps1`.

---

## 5. Session Forensic History: What Was Just Implemented & Fixed

### A. Camera Continuous Zoom Jog-Shuttle Dial (`v1.2.16-alpha`)
- **Resting State**: The zoom circle is anchored at exact **50% center**. It displays the current zoom level inside the circle (e.g. `1.0X`, `1.3X`). The readout above the track is hidden.
- **Dragging State**:
  - Circle thumb expands (`w-[32px] h-[32px]` with a white ring border).
  - The zoom level appears in large bold text centered **above the track** (e.g. `5.1X`).
  - **Drag Right (50% to 100%)**: Smoothly scales zoom from `startRatio` up to `maxZoom`.
  - **Drag Left (50% down to 0%)**: Smoothly scales zoom down from `startRatio` to `1.0X` (or `minZoom` if ultrawide). When already at 1.0x, it clamps at 1.0x.
  - Active white track segment symmetrically fills between 50% and thumb position.
- **On Release (`pointerup`/`touchend`)**:
  - The thumb circle smoothly **snaps back to 50% center** with a spring transition (`transition: left 200ms ease-out`).
  - The camera retains the achieved zoom level.
  - The zoom readout smoothly moves back **inside the circle**.
- **Files**: [`frontend/src/screens/CameraScreen.tsx`](file:///c:/CODE/Dispatch/frontend/src/screens/CameraScreen.tsx), [`frontend/src/camera/cameraInteractionState.ts`](file:///c:/CODE/Dispatch/frontend/src/camera/cameraInteractionState.ts) (`zoomRatioFromJogDrag`), [`frontend/test_camera_interactions.js`](file:///c:/CODE/Dispatch/frontend/test_camera_interactions.js).

### B. Offline Clips System & Direct Video Playback Fix
- **Root Cause of Non-Playing Clips**:
  1. `apiUrl(path, pcHost)` in `bridge.ts` previously prepended `http://pcHost:8000/` even to absolute URLs (`https://...r2.cloudflarestorage.com/...`). Fixed to bypass absolute URLs.
  2. Cloudflare Worker `GET /clips` previously returned `source_video_url: videoUrl`. In `FramedVideoStage`, this caused `isSourceTimebase = true`. The player attempted to seek `video.currentTime = clipStart` (e.g. 35.6s) inside a 23s rendered proxy video, causing playback to crash/halt.
  3. Detail view had `preferRendered={false}`. Changed to `preferRendered={true}`.
  4. Worker `GET /clips` now sets `source_video_url: undefined` for cloud clips so clips play from 0.0s to duration.
- **Thumbnail Sync to Cloudflare R2**:
  - Added thumbnail upload engine in [`dispatch/cloud/sync.py`](file:///c:/CODE/Dispatch/dispatch/cloud/sync.py) and executed [`scripts/sync_thumbnails.py`](file:///c:/CODE/Dispatch/scripts/sync_thumbnails.py). All 11 existing clip thumbnails ($80\text{--}117\text{ KB}$ each) were uploaded to R2 and linked to `media_objects` (`kind = 'thumbnail'`).
  - Deployed updated Cloudflare Worker (`Version ID: 42bc0c72-c90e-4f48-ab72-721833fa3123`). Worker `GET /clips` now returns valid presigned `thumbnail_url` for every clip.
  - Verified live: Clips screen now renders crisp video thumbnails on all clip cards and plays video immediately upon tap.

### C. Sessions Page Offline & Cloud Fallback
- **Status Correction**: In `RecordingsScreen.tsx`, `fetchClips` previously only ran when `pcHost && pcStatus.online`. When PC was offline, `clips` array was empty, so native recording sessions were stuck at status `"Queued"`. Added cloud fallback to `${WORKER_BASE}/clips`. Sessions now calculate `clipCount > 0` and correctly transition to status **`Ready`**.
- **Session Thumbnails**: Added `thumbnailUrl` fallback to `UnifiedSession`. If YouTube ID is not yet synced, the session card displays `sessClips[0].thumbnail_url`.
- **YouTube Failed Jobs**: Clarified why the two YouTube URLs in Screenshot 4 failed in cloud worker: the YouTube video visibility returned `'unknown'` / private, requiring public/unlisted visibility.

---

## 5b. Session Forensic History: v1.3.0-alpha (2026-10-27)

### D. Optimistic Reject & Caption Timeline Resilience
- **ClipsScreen**: reject now removes the clip instantly (`optimisticRejectIdsRef`), restores it on failure, 12&nbsp;s abort timeout with list re-download; cloud-only reject shows "Connect to your PC" notice; confirmation dialog text shortened (44px buttons).
- **db.py `reject_clip`**: idempotent — a repeat request on an already-rejected clip returns success without duplicate preference records; `app.py` maps a missing clip to real 404.
- **CaptionDock / StudioWorkstation / ClipsScreen**: timeline stays mounted while captions load (10&nbsp;s timeout, retry path, cloud-mode explainer) instead of a stuck full-panel spinner.

### E. Direct Phone-to-YouTube Cloud Upload Sessions
- **cloud-worker** new endpoints: `POST /youtube/upload-sessions` (resumable upload init via raw-vault refresh token), `POST /youtube/uploads/lookup`, `POST /youtube/videos/status` (processing tracker). Worker `tsc` OK, 38/38 tests pass.
- **PairingManager**: encrypted `dispatch_cloud_secure` prefs for short-lived cloud JWT; `rawVaultAccountId` + cloud-session marker APIs.
- **DispatchNativeBridge / CameraScreen / App**: native emits `recording_stopped` / `recording_stop_failed`; stop button shows finalizing state (disabled) and navigates to Sessions on completion.

### F. Open Item (needs user input)
- A video that finished YouTube processing still shows **Failed** locally. Waiting on the exact "Processing Issue" text from the failed session's detail view; two unexplained red YouTube entries likely = same video via two URL formats or a private/unlisted visibility gate.

---

## 6. Directory Layout & Key Modules

```
Dispatch/
├── android/                             # Native Android Jetpack Compose app
│   ├── app/build.gradle.kts             # versionCode & versionName authority
│   ├── keystore.properties              # Signing key credentials (git-ignored)
│   └── src/main/java/com/resolvia/dispatch/
│       ├── bridge/DispatchNativeBridge.kt # JavaScript-to-Android interface
│       ├── camera/                      # Camera2/CameraX native implementation
│       └── updater/DispatchUpdateManager.kt # In-app APK updater
├── cloud-worker/                        # Cloudflare Workers TypeScript API
│   ├── src/index.ts                     # Auth, /reserve, /complete, /clips, /jobs
│   └── wrangler.toml                    # Cloudflare R2 & Worker bindings
├── dispatch/                            # Local PC Python Pipeline Daemon
│   ├── main.py                          # Master entrypoint & worker starter
│   ├── config.py                        # Paths, ports, environment configuration
│   ├── edit_windows.py                  # 720p bounded proxy generation (FFmpeg)
│   ├── cloud/sync.py                    # Autonomous cloud sync (Supabase & R2)
│   ├── ai_clips/worker.py               # Gemini highlight extraction worker
│   ├── transcription/worker.py          # Whisper / YouTube captioning
│   ├── publisher/outbox.py              # YouTube Shorts publishing
│   └── web/app.py                       # FastAPI local LAN backend
├── frontend/                            # Vite + React + Tailwind Mobile Studio
│   ├── src/screens/CameraScreen.tsx     # Camera viewfinder & jog zoom dial
│   ├── src/screens/ClipsScreen.tsx      # Clip list, detail player, Studio editor
│   ├── src/screens/RecordingsScreen.tsx # Sessions list & YouTube ingest
│   ├── src/screens/SettingsScreen.tsx   # Accounts, Storage, In-App Update checker
│   ├── src/bridge.ts                    # Native Android Bridge JS wrapper
│   └── src/camera/cameraInteractionState.ts # Mathematical zoom & AE/AF state
├── scripts/
│   ├── bump_version.ps1                 # Atomic SemVer + versionCode bumper
│   ├── prepare_release.ps1              # Compiles and signs release APK
│   ├── deploy_gh_pages.ps1              # Deploys APK + manifest to GitHub Pages
│   └── sync_thumbnails.py               # Batch thumbnail uploader to R2
└── site/                                # GitHub Pages landing page & update manifest
    ├── index.html                       # Download landing page
    └── update.json                      # Release metadata & SHA-256 for updater
```

---

## 7. Standard Release Runbook

When releasing an update or bugfix, execute the following four commands in sequence:

```powershell
# 1. Bump version and synchronize all manifest files
powershell -ExecutionPolicy Bypass -File scripts\bump_version.ps1 -Type patch

# 2. Build frontend production bundle
cd frontend; npm run build; cd ..

# 3. Compile and sign the Android Release APK
cmd /c "set JAVA_HOME=C:\Program Files\Android\Android Studio\jbr&& powershell -ExecutionPolicy Bypass -File scripts\prepare_release.ps1"

# 4. Commit, tag, push, and publish to GitHub Releases
git commit -am "chore(release): bump to vX.Y.Z-alpha (code N)"
git tag vX.Y.Z-alpha
git push origin main --tags
gh release create vX.Y.Z-alpha release-out/Dispatch.apk release-out/update.json --title "Dispatch vX.Y.Z-alpha" --notes "Release notes..."

# 5. Deploy direct APK download mirror and update manifest to GitHub Pages
powershell -ExecutionPolicy Bypass -File scripts\deploy_gh_pages.ps1 -Tag vX.Y.Z-alpha

# 6. Deploy Cloudflare Worker (if cloud-worker/ was touched)
cd cloud-worker; npx wrangler deploy; cd ..
```

---

## 8. Immediate Next Steps & Strategic Guidance for Agent GLM 5.3 Flash

1. **Test Real Device Verification**:
   - Have the user update to `v1.2.16-alpha` (Code 44) via the in-app update checker in Settings or the direct APK mirror.
   - Verify that the zoom slider starts at center with `1.0X` inside, drags right to zoom in, drags left to zoom out, and snaps back to center on release.
   - Verify that all clip cards in the Clips tab render their thumbnails and tap-to-play works smoothly.
2. **YouTube Ingestion Visibility Notice**:
   - The user's two pasted YouTube URLs (`4xOAmken0z0`) failed because the video privacy is set to private or unlisted without public metadata accessible by the Cloudflare Worker. If the user wants to ingest YouTube links, ensure the YouTube video is set to **Unlisted** or **Public**.
3. **Keep Design System Integrity**:
   - Always adhere to the 4-layer elevation and biomechanical 44px hitboxes. Do not add emojis or arbitrary colors.
