# Dispatch - Launch Day Master Tracking Document (30 October 2026)

> **Document Status:** Active & Canonical Master Tracker  
> **Date:** October 30, 2026  
> **Launch Milestone:** Final Release & Production Deployment Day  
> **Protocol Rule:** This document is updated incrementally after **every single mini-task**. Any incoming AI agent or developer can resume work directly from this file without context loss.

---

## 1. Executive Summary & Launch Day Objective

Today is **October 30, 2026**, the final day of launch optimization on **Dispatch**. At the end of the day, the app is scheduled to launch:
- Shared with co-founders and early creator friends.
- Put into daily full-scale production use by the owner.

The objective today is **zero shortcuts, zero loose ends, and hardened reliability**. Every remaining bug, edge case, and user experience friction point will be systematically addressed, verified from fundamental root causes, and documented.

---

## 2. System State & Canonical Environment

| Component | Target / Specification | Current Live State |
| :--- | :--- | :--- |
| **Android Package** | `com.resolvia.dispatch` | Version `1.6.10` (`versionCode = 67`) |
| **Release Tag** | Monotonic SemVer Git Tag | `v1.6.10-alpha` (Commit `cd0f1f5`) |
| **Release Certificate** | Trusted keystore | SHA-256 fingerprint ends in `...3804` |
| **Release Distribution** | GitHub Releases & `gh-pages` | Hosted at `https://resolviaai.github.io/Dispatch/` |
| **PC Daemon API** | FastAPI / SQLite / Whisper / FFmpeg | `http://127.0.0.1:8000` (Stopped, waiting user command) |
| **Cloudflare Worker** | Cloud API & Edge Storage Gateway | `https://dispatch-cloud.resolviaai.workers.dev` |
| **Supabase Cloud** | Auth, Profiles, Cloud DB | `https://aldzlbdxplqkcnyjhzmm.supabase.co` |
| **Web Review Studio** | Vite + React + Tailwind v3 | Embedded in Android Assets & PC Review UI |

---

## 3. Operational Protocols & Collaboration Invariants

### 3.1 The Cross-Questioning & Verification Protocol (User-Agent Ground Rule)
When the user proposes a solution, asks to implement a feature, or reports a bug:
1. **Never blindly execute:** Do not apply hasty patches or superficial workarounds.
2. **Deep File Inspection:** Read the actual source files and trace the complete lifecycle of data/state.
3. **Critical Cross-Examination:** Analyze potential side effects, failure modes, race conditions, and architectural conflicts.
4. **Evaluate Superior Alternatives:** Assess if there is a cleaner, more robust, or more idiomatic solution. Present comparisons clearly to the user.
5. **Acknowledge and Align:** Obtain alignment before making destructive or high-impact architectural changes.

### 3.2 Continuous Incremental Persistence
- This document (`docs/2026-10-30_LAUNCH_DAY_MASTER.md`) and the active conversation artifact must be updated after **every single completed mini-task**.
- If context window resets or agent switches occur, the document serves as the immediate ground truth.

### 3.3 Zero Unsolicited Process Spawning
- Do not run daemon loops, servers, or background tasks unsolicited.
- Keep workspace clean; inspect logs rather than altering running states prematurely.

---

## 4. Prioritized Launch Backlog & Problem Register

The backlog reflects the exact 14 tasks arranged in user-specified execution sequence:

```
[X] TASK 1: Minimal Badging on Sessions & Clips Lists + Clean Origin/ID Inspector in Details
[ ] TASK 2: Notification Center & Activity Sheet (Replacing Header Banners & Refresh Buttons with Pull-to-Refresh)
[ ] TASK 3: Fix 00:00 Duration Display for YouTube / Cloud-Ingested Sessions
[ ] TASK 4: Reconcile "Ingestion Failed" Session Status with Ready Clips & Fix Clip Video Playback
[ ] TASK 5: Camera Screen Optical Micro-Polishing (Zoom Shadow, Adaptive Chips & Concentric Shutter)
[ ] TASK 6: Cellular / Wi-Fi Network Upload Selector (Clear 2-Option Radio Switch in Settings)
[ ] TASK 7: Granular Pipeline Stage Status in Sessions Screen & Session Detail
[ ] TASK 8: Input Orientation Classification & Multi-Layout System Hardening
[ ] TASK 9: Layout-Specific Thumbnails & Preview Frame Generation
[ ] TASK 10: Captions Generation, Whisper Alignment Audit & Saved Default Template Sync
[ ] TASK 11: Social Account OAuth Connection Stability (LinkedIn, Instagram, Facebook)
[ ] TASK 12: Google Cloud Console App Verification & Brand Identity Assets
[ ] TASK 13: One-Click Desktop Server Launcher & Multi-Tier Smart Storage Retention Policy (7-Day / 72-Hour / 10 GB)
--- STOP & REPORT TO USER BEFORE TOUCHING TASK 14 ---
[ ] TASK 14: Direct Video File Upload Route (<= 3 min files) via Clips Screen
```

---

### Task Breakdown & Technical Context

### [TASK 1] Minimal Badging on Sessions & Clips Lists + Clean Origin/ID Inspector in Details
- **User Requirement:**
  - **List Views (Sessions & Clips Library):** Keep the UI ultra-minimal. Do not show technical IDs (like `Session Direct Ingestion` or noisy raw hashes). Show only a clean visual indicator: **Camera badge** (for phone camera recordings) or **YouTube badge** (for YouTube imports).
  - **Details Views (Session Detail & Clip Review/Detail):** Dedicated, uncluttered space where the user can view technical metadata when needed:
    - In **Session Detail**: Session ID (`sess_179`), Origin type (`phone` camera vs `youtube`), and YouTube link/video ID if applicable.
    - In **Clip Detail / Review**: Clip ID (`clip_...`), parent Session ID or source video ID/title, duration, and origin badge.
    - Must remain unobtrusive and clean so it doesn't clutter creator workflow.
- **Root Cause & Scope:**
  - `ClipsScreen.tsx`: Group headers currently display raw fallback strings like `Session Direct Ingestion` or `Session 179`. In list items, clips do not have the origin icon (Camera vs YouTube) that Sessions have.
  - `RecordingsScreen.tsx`: Session list has badges, but Session Detail can present ID metadata more cleanly without visual clutter.
- **Key Files:**
  - `frontend/src/screens/ClipsScreen.tsx`
  - `frontend/src/screens/RecordingsScreen.tsx`

---

### [TASK 2] Notification Center & Activity Sheet (Replacing Header Banners & Refresh Buttons)
- **User Analysis & Problem Formulation:**
  - In `RecordingsScreen.tsx`: Green alert banner (`"Video added! Your PC will create clips."`) pushes content down.
  - In `ClipsScreen.tsx`: Two stacked full-width alert strips (`"2 cloud job(s) in progress - analyzing highlights..."` and `"2 queued · daily quota 0/10 · auto-drains at midnight"`) crowd the header area.
  - Refresh buttons in top-right headers consume valuable navbar real estate across Sessions, Clips, and Settings screens.
- **Proposed Architectural Solution & Trade-offs:**
  1. **Top-Right Bell Icon (Global Notification Trigger):** Place a consistent Bell icon (`<Bell size={18} />`) in the top-right header across Sessions, Clips, and Settings. When background activity is running (cloud jobs, queue items, or recent alerts), a subtle primary dot indicator appears on the bell.
  2. **Activity / Notification Drawer (Bottom Sheet):** Tapping the Bell opens a sleek bottom sheet ("Activity & Notifications") containing:
     - Active cloud rendering/analysis jobs (`2 cloud jobs in progress`).
     - Daily cloud queue backlog status (`2 queued · quota 0/10`).
     - Transient system confirmations (`"Video added! Your PC will create clips."`).
  3. **Pull-to-Refresh Gesture (Native Mobile UX):** Replace dedicated refresh buttons with mobile-standard pull-to-refresh on scrollable views, retaining a fallback manual pull/spin indicator.
- **Key Files:**
  - `frontend/src/screens/RecordingsScreen.tsx`
  - `frontend/src/screens/ClipsScreen.tsx`
  - `frontend/src/screens/SettingsScreen.tsx`
  - `frontend/src/components/NotificationCenterModal.tsx` (new clean component)

---

### [TASK 3] Fix 00:00 Duration Display for YouTube / Cloud-Ingested Sessions
- **User Analysis & Problem Formulation:**
  - On the Sessions tab (`Ready` filter), YouTube imported sessions show `00:00` duration badge on the thumbnail and in the session stats row.
- **Root Cause & Scope:**
  - In `RecordingsScreen.tsx#L589`: `totalDuration: item.duration || 0`. When jobs come from Cloudflare Worker `/jobs/status` or PC `/api/youtube/inbox`, `item.duration` is `undefined` or `null` before audio analysis or if metadata was not saved into the job payload.
  - When clips have already been generated for that session (`itemClips.length > 0`), the session can compute its duration from the clips or fetch source duration from the first clip's metadata / source metadata, rather than defaulting to `00:00`.
- **Key Files:**
  - `frontend/src/screens/RecordingsScreen.tsx`
  - `dispatch/api/routers/youtube.py`
  - `cloudflare-worker/src/index.ts`

---

### [TASK 4] Reconcile "Ingestion Failed" Session Status with Ready Clips & Fix Clip Video Playback
- **Database & Pipeline Audit Findings:**
  1. **Contradictory Status in Session Detail:**
     - In `dispatch.db`, session `sess_1793271393453` (part `_zjj4F0m4IQ.mp4`, 36.85s) and `sess_1793299790105` (part `TyK7m2Vxqq8.mp4`, 106.4s) have **already generated full clips** (`clip_...cd64dd` and `clip_...43bf03`) with complete word-level caption tracks!
     - However, in `RecordingsScreen.tsx`, while the list card marks them ready (because `clipCount > 0`), inside the **Session Detail View**, `currentSessionDetail` evaluates `allStatuses.some(st => st.startsWith('FAILED_'))`. A transient upload or chunk state caused `failureStatus` to trigger the red error banner: `"This video could not be processed (ingestion failed). Tap below to retry."`
     - **Architectural Rule:** If clips have been generated (`clipCount > 0`), the session is inherently **READY / SUCCESSFUL**, regardless of any intermediate retries in `rawNative.segments`. The failure banner must never be displayed over an already-processed session.
  2. **Clip Video Not Loading in Clips Page:**
     - The local daemon was stopped (`Test-NetConnection 127.0.0.1:8000` failed). When the phone is disconnected from PC or PC daemon is down, `apiUrl('/api/clips/...', pcHost)` URLs fail to load video bytes unless they are streamed from Cloudflare R2 / presigned cloud URLs.
     - In `ClipsScreen.tsx`, if the PC host is unreachable or video fails to load, fallback streaming logic must gracefully stream the uploaded YouTube source video or presigned R2 cloud video instead of showing a black dead player.
- **Key Files:**
  - `frontend/src/screens/RecordingsScreen.tsx`
  - `frontend/src/screens/ClipsScreen.tsx`
  - `frontend/src/captions/FramedVideoStage.tsx`

---

### [TASK 5] Camera Screen Optical Micro-Polishing (Zoom Shadow, Adaptive Chips & Concentric Shutter)
- **User Analysis & Problem Formulation:**
  1. **Zoom Slider Contrast over Light Scenes:**
     - When pointing the camera at a bright/white surface, the white/semi-transparent zoom track (`bg-white/40`) and thumb become invisible.
     - **The Fix:** Add a subtle, diffuse drop-shadow (`drop-shadow-[0_1px_3px_rgba(0,0,0,0.7)]` and subtle outline) to the zoom slider container and thumb so it stays visible over both pitch-black and pure-white backgrounds without harsh shadows.
  2. **Top Chips in Light Theme:**
     - The top status chip (`1080P 30FPS`), Switch Camera button, and Torch button have hardcoded `bg-black/65` and `text-white`. In Light Theme, they look like jarring black blobs rather than adapting.
     - **The Fix:** Adapt to `[data-theme='light']`: In light theme, make chips wider, frosted white surface (`bg-white/80 border-black/10 text-text-main shadow-md backdrop-blur-md`) with dark glyphs, maintaining optimal contrast and concentric geometry.
  3. **Record Button Gap Elimination:**
     - The red record circle (`w-14 h-14 bg-danger`) currently sits inside a larger outer ring (`w-20 h-20 border-4 border-white/80 bg-white/10`) with a visible empty gap.
     - **The Fix:** Expand the inner red circle or adjust concentric sizing so the red surface fits snugly against the white border (`w-full h-full` or concentric fit with 0 empty gap), creating a solid, cohesive shutter button.
- **Key Files:**
  - `frontend/src/screens/CameraScreen.tsx`

---

### [TASK 6] Cellular / Wi-Fi Network Upload Selector (Clear 2-Option Radio Switch in Settings)
- **User Problem & UX Ambiguity:**
  - In `SettingsScreen.tsx#L915`, the setting is currently labeled:
    `Wi-Fi only upload · Never uses mobile data` with a single binary toggle switch.
  - When the user turns the toggle off, it is visually ambiguous what state is active.
- **Proposed Architectural Solution (YouTube / Google Photos Standard):**
  - Replace the ambiguous single toggle with a **2-Option Segmented Selector** (identical to the clean 3-option Appearance selector right above it in Settings):
    1. **"Wi-Fi Only"** (`<Wifi size={16} />`): "Uploads wait for unmetered Wi-Fi connection."
    2. **"Wi-Fi & Cellular"** (`<Smartphone size={16} />` or `<Radio size={16} />`): "Uploads immediately over any available connection."
  - This maps directly to the underlying boolean `Bridge.setWifiOnly(true/false)` and Android `PairingManager.isWifiOnlyEnabled`, eliminating all ambiguity.
- **Key Files:**
  - `frontend/src/screens/SettingsScreen.tsx`

---

### [TASK 7] Granular Pipeline Stage Status in Sessions Screen & Session Detail
- **Reported Issue:**
  - In Sessions screen, status chips sometimes jump between `Processing` without showing whether it is ingesting, transcribing with Whisper, generating AI highlights, or rendering clips.
- **Key Files:**
  - `frontend/src/screens/RecordingsScreen.tsx`
  - `dispatch/pipeline/manager.py`

---

### [TASK 8] Input Orientation Classification & Multi-Layout System Hardening
- **Reported Issue:**
  - A horizontal YouTube link was ingested and generated a 38-second clip, but was misclassified as short-form portrait, giving only 3 layout options instead of 4 (missing `Crop 9:16`, `Fit + Blur`, `Fit + Black`, `Native 16:9`).
  - Layout switching glitches:
    - `Crop 9:16`: Does not center-crop properly.
    - `Fit + Blur`: Preview flips between horizontal and vertical intermittently.
    - `Fit + Black`: Top and bottom bars fail to remain black, or preview flips.
    - `Native 16:9`: Left/right pillarboxes do not persist predictably.
- **Key Files:**
  - `frontend/src/screens/ClipsScreen.tsx`
  - `frontend/src/captions/FramedVideoStage.tsx`
  - `dispatch/pipeline/stages/clipping.py`
  - `dispatch/models.py`

---

### [TASK 9] Layout-Specific Thumbnails & Preview Frame Generation
- **Reported Issue:**
  - Thumbnails in the clips browser are generic or visually mismatched with the chosen layout.
- **Key Files:**
  - `frontend/src/screens/ClipsScreen.tsx`
  - `dispatch/pipeline/stages/rendering.py`

---

### [TASK 10] Captions Generation, Whisper Alignment Audit & Saved Default Template Sync
- **Reported Issue:**
  - Clips generated from YouTube / recordings show no animated captions or do not automatically inherit the user's saved Default Template styles.
  - In Template Studio, clicking "Save Template" spins indefinitely or fails to reliably write down to PC daemon SQLite.
- **Key Files:**
  - `frontend/src/captions/CaptionStudio.tsx`
  - `frontend/src/captions/TemplateStudioModal.tsx`
  - `dispatch/pipeline/stages/transcription.py`
  - `dispatch/pipeline/stages/alignment.py`
  - `dispatch/cloud/sync.py`

---

### [TASK 11] Social Account OAuth Connection Stability (LinkedIn, Instagram, Facebook)
- **Reported Issue:**
  - When clicking "Connect" for LinkedIn, Instagram, or Facebook in Settings, browser opens for a fraction of a second and immediately closes.
- **Key Files:**
  - `android/app/src/main/AndroidManifest.xml`
  - `android/app/src/main/java/com/resolvia/dispatch/MainActivity.kt`
  - `frontend/src/screens/SettingsScreen.tsx`
  - `cloudflare-worker/src/index.ts`

---

### [TASK 12] Google Cloud Console App Verification & Brand Identity Assets
- **Reported Issue:**
  - Google verification warning for `https://resolviaai.github.io/Dispatch/` and OAuth branding approval.
- **Key Files:**
  - `site/index.html`
  - `site/google-site-verification.html`
  - `android/app/src/main/res/mipmap-*`

---

### [TASK 13] One-Click Desktop Server Launcher & Multi-Tier Smart Storage Retention Policy
- **User Architecture & Forensic Requirements:**
  - **Desktop One-Click Launcher:** A clean Windows batch file (`Start-Dispatch.bat`) placed directly on Windows Desktop to start the server.
  - **The 3-Tier Storage Retention Law:**
    1. **Primary Time-Based Rule: 7-Day Source Retention (168 hours):** Full source video remains on PC for 7 days so creator can re-clip or expand trims.
    2. **Publication-Based Early Release Rule (72-Hour Post-Publish Grace):** If ALL clips extracted from that source video have been published/approved, full source video can be cleaned up after 72 hours.
    3. **Hard Disk Safety Ceiling (10 GB):** If `storage/` exceeds 10 GB, oldest source videos past 72 hours are pruned FIFO.
- **Key Files:**
  - `scripts/Start-Dispatch.bat`
  - `dispatch/storage/retention.py`
  - `dispatch/main.py`

---

### [TASK 14] Direct Video File Upload Route (Future Route C, <= 3 Minutes)
- **Planned Feature:**
  - Direct video upload button inside Clips screen modal for files up to 3 minutes.
  - *Execution Boundary:* Only to be planned and implemented after Tasks 1-13 are 100% verified and user gives explicit signoff.

---

## 5. Architectural & Design System Invariants

Every code change must adhere to the **Dispatch UI/UX & Mobile Engineering Invariants (v3)**:

1. **The Single-Direction Law:** On mobile viewports, every screen moves along strictly ONE physical axis (vertical card stack OR horizontal snap carousel). Never use multi-column grids on phone screens.
2. **Biomechanical 44px Hitboxes:** Every interactive element must maintain a minimum touch bounding box of $44\text{px} \times 44\text{px}$ with $\ge 8\text{px}$ spacing between adjacent targets.
3. **4-Layer Elevation Architecture (60/30/10):**
   - Layer 0 (Canvas Root): `#161616` (`bg-studio`, `bg-background`)
   - Layer 1 (Elevated Cards): `#1C1C1C` with border `#2E2E2E` (`bg-surface-100`, `border-border`)
   - Layer 2 (Recessed Wells & Inputs): `#232323` (`bg-surface-200`)
   - Layer 3 (Hero Brand CTA): Electric Creator Blue `#2563EB` (`bg-primary`, hover `#3B82F6`)
   - Semantic colors strictly for status (Success `#22C55E`, Warning `#F59E0B`, Danger `#EF4444`).
4. **Law of the Single Hero CTA:** Exactly ONE glowing hero action per viewport. Secondary actions remain neutral recessed surfaces.
5. **Typographic Limit & Numeric Alignment:** Maximum 4 font sizes ($18\text{px}$, $14\text{px}$, $12\text{px}$, $10\text{px}$) and 2 font weights. Numbers and durations must use `font-mono tabular-nums` and be right-aligned in tables/lists.
6. **Zero OS Native Emojis:** Use vector SVG icons (Lucide) exclusively.
7. **Monotonic SemVer Release Rule:** Every Android release must bump `versionCode` by at least $+1$ and synchronize across all manifests using `scripts/bump_version.ps1`.

---

## 6. Live Progress & Execution Log

| Timestamp (IST) | Sub-task / Action | Files Modified | Outcome & Verification |
| :--- | :--- | :--- | :--- |
| **2026-10-30 10:55** | Master Tracking Document Created | `docs/2026-10-30_LAUNCH_DAY_MASTER.md` | Initialized canonical tracker; standing by for first specific task. |
| **2026-10-30 12:05** | Master Backlog Order Finalized | `docs/2026-10-30_LAUNCH_DAY_MASTER.md` | Reordered tasks 1 to 14 according to user sequence. Ready to commence Task 1. |
| **2026-10-30 12:35** | TASK 1: Minimal Badging & Origin/ID Inspector | `dispatch/web/app.py`, `frontend/src/screens/ClipsScreen.tsx`, `frontend/src/screens/RecordingsScreen.tsx`, `tests/test_web.py` | Commit `5b1894d`: Ultra-minimal list badging (Camera vs YouTube icon on thumbnails), eliminated technical strings from headers, integrated origin/session/YouTube ID inspector in Details & Options sheets. Verified by tests & Vite production build. |

---

*End of Active Master Tracker. Next entry to be appended upon completion of Task 2.*
