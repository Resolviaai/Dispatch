# Dispatch: Capture, Review, and Publish

<p align="center">
  <img src="branding/dist/dispatch-lockup-horizontal-white.svg#gh-dark-mode-only" alt="Dispatch Logo" width="380" />
  <img src="branding/dist/dispatch-lockup-horizontal-black.svg#gh-light-mode-only" alt="Dispatch Logo" width="380" />
</p>

> You create the raw signal. Dispatch carries it through review and publishing.

Dispatch turns long speaking or work sessions into short, reviewable videos. It supports three input routes: recording in the Android app (Route A), submitting an existing YouTube link (Route B), or directly uploading an existing video (Route C). All video processing, Gemini AI transcription, and FFmpeg rendering run strictly on the Windows PC Workstation; Cloudflare Workers and Supabase act as the control plane (auth, job queueing, and presigned R2 storage), and Android acts as a capture, review, and control client.

**Current source version:** Android 1.7.49 (version code 117). A version number or successful build is not proof of live-service or physical-phone behavior. Check the current source and verification records before relying on older status claims.

## How the workflow works

### Route A — Record on Android (Phone Camera → YouTube → PC)

1. Dispatch records via CameraX and rolls long recordings into sequential segments of approximately 10 minutes.
2. Room stores session, segment, and upload state locally on the phone.
3. WorkManager resumably uploads each segment to the user's connected YouTube account as **unlisted**, tagged with `[DISPATCH]` and session identifiers.
4. **Queue Disambiguation:** Route A does **NOT** use the Supabase `processing_jobs` queue. Instead, the PC background daemon `YouTubeInboxPoller` discovers new segments via YouTube Data API v3, downloads the source MP4 to `YOUTUBE_INBOX_DIR`, and ingests it into the local processing pipeline.
5. The PC pipeline executes Gemini transcription (`gemini-3.5-transcribe` / `gemini-3.5-flash-lite`), highlight extraction, dynamic captions, and FFmpeg rendering.
6. **Safe Phone Retention Release:** The phone retains its local recording until the PC confirms successful ingestion via `GET /api/sync/youtube-ack`. The PC verifies that the physical file exists on disk, confirms its SHA-256 matches actual disk bytes, and signs an HMAC-SHA256 receipt. Only after Android cryptographically verifies the receipt signature is the local phone file deleted.
7. **Long-Session Storage Law:** At 8 Mbps (1080p/30 fps), a 3-hour recording generates ~10.8 GB (~18 segments @ ~600 MB each). The 512 MB free-space threshold in `RecordingSafetyPolicy.kt` is an emergency safety stop floor, not a capacity guarantee. If the PC is offline or uploads lag, segments queue on the phone; if storage reaches the 512 MB safety floor, recording halts safely to protect existing footage. Once acknowledged by the PC, the phone frees local space while the PC retains the master source under its 3-Tier Retention Policy.

The phone does not transfer recordings to the PC over LAN. A YouTube upload alone is not confirmation that the PC has safely ingested a segment.

8. **YouTube Channel Identity Alignment:** The PC's local YouTube OAuth credentials (`youtube_token.json`) must be authenticated to the **EXACT SAME YouTube channel** that the phone uploads to via the cloud credential vault. If the accounts mismatch, the PC cannot discover the phone's unlisted uploads, and the phone will indefinitely retain its source footage awaiting ingestion verification.

### Route B — Submit a YouTube link (Remote URL → PC)

1. Submit a public or unlisted video URL through Dispatch Studio or the mobile client.
2. The submission creates a row in Supabase `processing_jobs` (`source = 'youtube'`).
3. The PC `RemoteJobPoller` atomically claims the job via `cloud_claim_job`.
4. The PC downloads the source via `catcher.py` (`yt-dlp`), probes the video, and persists its processing state.
5. Dispatch uses Google Gemini speech-to-text directly for verbatim audio transcription (`gemini-3.5-transcribe` primary with `gemini-3.5-flash-lite` fallback). YouTube caption downloading is not used as a transcription fallback.
6. Gemini proposes highlight ranges. FFmpeg extracts bounded candidate audio, which is aligned at exact millisecond word levels using **`gemini-3.5-transcribe`** (with **`gemini-3.5-flash-lite`** word-timestamp fallback).
7. Dynamic caption grouping and layout rendering format the clip into the user's choice of 4 framing options: **Crop 9:16**, **Fit + Blur**, **Fit + Black**, or **Native 16:9**.
8. Review the candidate in Studio, adjust its range or captions, and approve or reject it.
9. Approved output enters the publishing outbox for multi-platform distribution.

Private videos are rejected by the manual URL-ingestion visibility gate.

### Route C — Direct Video Upload (R2 Staging → PC)

1. Android or Web clients stage short pre-recorded videos directly into Cloudflare R2:
   - Client calls `POST /reserve` with `{ kind: "clip_video", mime_type, expected_bytes, client_token }` to reserve storage.
   - Client PUTs video bytes directly to the returned presigned R2 upload URL.
   - Client calls `POST /complete` with `{ reservation_id }`. The Cloudflare Worker verifies the object in R2 via `head()`, copies it to the immutable final key, and commits the active media object in Supabase.
   - Client calls `POST /jobs/direct` with `{ media_object_id, title }`, which inserts a job into Supabase `processing_jobs` (`source = 'direct'`).
2. The PC `RemoteJobPoller` claims the direct job via `cloud_claim_job`.
3. The PC `direct_handler` in `dispatch/direct_ingest.py` calls `POST /media-url` with `{ media_object_id }` and `X-Dispatch-User-Id` to obtain a short-lived presigned GET URL (10-minute TTL).
4. The PC streams the object from R2 to local disk as `dir_<object_id>.mp4` (using a `.part` temporary file during transfer) and invokes `youtube_inbox.catcher.ingest_direct_video()`.
5. **Route C Scope & Limits:**
   - Maximum upload size: **512 MiB** (`DIRECT_DOWNLOAD_CAP_BYTES = 536870912 + 16 * 1024 * 1024`).
   - Maximum duration: **$\le 180.5 seconds$** (~3 minutes).
   - Generates **one full-length captioned clip**, NOT AI-selected highlights.
   - The PC executes Gemini transcription and FFmpeg rendering identically to Route B.

### Review and publishing

The React Studio is available at http://localhost:8000/studio/ when the PC daemon is running. The root URL (http://localhost:8000/) is the legacy dashboard. Approval remains an explicit review step; do not assume a clip will be publicly published automatically. Publishing availability depends on the connected account, provider authorization, and selected visibility.

## Editing and media limits

- The Gemini range is a suggestion, not the boundary of the user's edit.
- Final selected clips are limited to **5–180 seconds**.
- Keep source timestamps, temporary edit-window timestamps, and rendered-clip timestamps distinct. AI context padding must not restrict the user's selectable range.
- Long audio is processed in bounded windows with overlap and timestamp offsets. Stage 2 extraction must remain bounded; failure must not trigger transcription of an entire multi-hour source.
- **3-Tier Smart Storage Retention Law:**
  - **Tier 1 (7-Day Source Retention):** Full source video chunks are retained on PC for 168 hours (7 days) to enable creator re-clipping and trim expansions.
  - **Tier 2 (72-Hour Post-Publish Early Release):** Once all clips extracted from a source video have been published or approved, the full source video is released after 72 hours.
  - **Tier 3 (10 GB Disk Safety Ceiling):** If local storage exceeds 10 GB (10,000,000,000 bytes), the oldest source videos past 72 hours are automatically pruned FIFO until storage drops below the ceiling.
- The current cloud media contract caps an individual clip/edit-window object at **512 MiB** and a thumbnail at **8 MiB**. A **2,000,000,000-byte** per-user starting quota is configurable by the administrator.
- Full recording sources belong in the YouTube/PC workflow, not R2. Cloud edit-window staging and phone-side consumption have had separate implementation/deployment gates; verify current migrations and live services before assuming remote editing is fully available.

## Accounts, keys, and administration

- Users connect publishing accounts using OAuth in Dispatch. Connecting a YouTube or social account is separate from providing a Gemini API key.
- Each user can configure their own Gemini key in Settings. User keys are stored through the encrypted key-vault flow; never put keys in source code, screenshots, prompts, logs, or committed files.
- The web/cloud admin tools include user and job visibility, adjustable storage quotas, processing priorities, and reversible account status controls. Lower priority numbers are processed first.
- Supabase and Cloudflare components exist in the repository, including account, quota, job, admin, and media-storage paths. Their deployed state and completeness can differ from the local source; verify the relevant live service before changing migrations or claiming an end-to-end cloud feature is live.

## Requirements

- Windows 11 is the primary PC environment.
- Python **3.10+** and FFmpeg/ffprobe available on PATH.
- Node.js/npm for building and testing the React frontend.
- JDK 17 and Android SDK for Android builds.
- Google/YouTube OAuth configuration for YouTube account access; Gemini API access for AI features.
- Target phone: POCO C65, with 1080p/30 fps as the recording target. Verify actual encoding settings and output from a real sample.
- An Android device is required to verify recording, camera, thermal, and OEM lifecycle behavior.

## Run Dispatch on Windows

### One-Click Desktop Launcher
Double-click `scripts/Start-Dispatch.bat` or run:
```cmd
scripts\Start-Dispatch.bat
```
This launcher checks Python, verifies FFmpeg on PATH, creates storage folders, and starts the uvicorn server on `http://127.0.0.1:8000`.

### Manual PowerShell Setup
From the repository root in PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .

cd frontend
npm ci
npm run build
cd ..

.\.venv\Scripts\python.exe -m dispatch.main
```

Install FFmpeg separately and confirm both ffmpeg and ffprobe work from the same terminal. The frontend build writes the web bundle into android/app/src/main/assets/web; the PC Studio serves that bundle at /studio/.

The PC server binds to 127.0.0.1 by default. Do not expose it to the internet. If LAN access is deliberately configured, protect the host, use the supported authentication flow, and enable TLS for credential transfers (DISPATCH_WEB_TLS=1). Changing the bind address exposes the server to the local network.

### Local environment and YouTube OAuth

Optional local environment values belong in an uncommitted .env file:

```ini
GEMINI_API_KEY=your_local_gemini_key
DISPATCH_WEB_HOST=127.0.0.1
DISPATCH_WEB_PORT=8000
DISPATCH_PUBLISH_MODE=private
```

For the PC YouTube poller, place the Google OAuth desktop client file at client_secrets.json and run:

```powershell
.\.venv\Scripts\python.exe scripts/auth_youtube.py
```

The script saves the authorized token as youtube_token.json. Keep both files private and out of Git. On Android, connect the user's YouTube account through the app's account flow; do not paste refresh tokens or client secrets into the app.

### Android debug build

After building the frontend bundle:

```powershell
cd android
.\gradlew.bat :app:testDebugUnitTest :app:assembleDebug
```

The debug APK is generated at android/app/build/outputs/apk/debug/app-debug.apk. A source change does not update an already-installed phone app; build and install the APK before checking the change on-device.

### Install a published Android release

Published release assets are listed at https://github.com/Resolviaai/Dispatch/releases. Use an APK attached to a release; a Git tag or a local build does not mean an APK was published or installed.

## Tests and verification

Run only the suites relevant to the change, and record exact commands and outcomes:

```powershell
# Python backend
.\.venv\Scripts\python.exe -m unittest discover -s tests

# React frontend
cd frontend
npm test
npm run build
cd ..

# Cloudflare Worker
npm test --prefix cloud-worker

# Android unit tests and debug APK
cd android
.\gradlew.bat :app:testDebugUnitTest :app:assembleDebug
```

Mocks prove only the mocked code paths. They do not prove a live Gemini request, YouTube upload/processing, Cloudflare/Supabase deployment, browser interaction, or POCO camera behavior. Never mark camera recording verified without testing on the actual POCO C65.

Tests must use isolated fixtures and must not overwrite the owner's .env, OAuth files, databases, or media. Verify test paths before running security or destructive tests.

## Windows startup helper

To register the optional hidden background startup task:

```cmd
scripts\install_windows_startup.bat
```

To remove it:

```cmd
scripts\uninstall_windows_startup.bat
```

## Important project documents

- AGENTS.md — current repository UI and mobile engineering invariants.
- design.md — current design-system specification.
- PROGRESS.md and AGENT_HANDOFF.md — checkpoint and handoff notes; check their recorded commit before trusting their claims.
- docs/ROUTE_B_IMPLEMENTATION_HANDOFF.md — Route B workflow and earlier automated verification.
- docs/CANONICAL_ARCHITECTURE.md — pipeline and data boundaries.
- docs/BOUNDED_EDITING_20261025.md — bounded edit-window design and implementation gates.
- docs/ADMIN_OPERATIONS_DASHBOARD.md — admin, account, quota, priority, and cloud-control-plane notes.

The current code is authoritative when it disagrees with an older handoff. At the time this README was refreshed, main was clean at c508988 (v1.1.23-alpha); several handoff documents still described older revisions and verification runs.

At this checkpoint, the source contains Route A transcript stitching and media handling for highlights across recording segments. The focused tests cover transcript offsets and source selection, but they do not prove a full real-media clip spanning a segment boundary. Camera and recording behavior also remain unverified on the POCO C65 unless a device run is recorded against this revision.
