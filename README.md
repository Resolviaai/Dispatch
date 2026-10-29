# Dispatch: Capture, Review, and Publish

<p align="center">
  <img src="branding/dist/dispatch-lockup-horizontal-white.svg#gh-dark-mode-only" alt="Dispatch Logo" width="380" />
  <img src="branding/dist/dispatch-lockup-horizontal-black.svg#gh-light-mode-only" alt="Dispatch Logo" width="380" />
</p>

> You create the raw signal. Dispatch carries it through review and publishing.

Dispatch turns long speaking or work sessions into short, reviewable videos. It has two input routes: recording in the Android app, or submitting an existing YouTube link. Video processing and rendering are designed to run on the Windows PC; Android records, stores, and uploads Route A segments.

**Current source version:** Android 1.6.4 (version code 61). A version number or successful build is not proof of live-service or physical-phone behavior. Check the current source and verification records before relying on older status claims.

## How the workflow works

### Route A — Record on Android

1. Dispatch records through CameraX and rolls a long session into segments of about ten minutes.
2. Room stores session, segment, and upload state on the phone.
3. WorkManager resumably uploads each segment to the connected YouTube account as **unlisted**, tagged with Dispatch identifiers.
4. The PC polls YouTube, waits for processing, downloads the segment, and continues the local processing pipeline.
5. The phone retains its source file until the PC confirms ingestion with matching identifiers and integrity data.

The phone does not transfer recordings to the PC over LAN. A YouTube upload alone is not confirmation that the PC has safely ingested a segment.

### Route B — Submit a YouTube link

1. Submit a public or unlisted video through the Dispatch Studio.
2. The PC downloads and probes the source, then persists its processing state.
3. Dispatch obtains YouTube captions where available and uses the configured transcription fallback when needed.
4. Gemini proposes highlight ranges. FFmpeg prepares candidate clips and captions.
5. Review the candidate, adjust its range or captions, then approve or reject it.
6. Approved output enters the publishing outbox. Local upload completion and YouTube’s later processing status are separate states.

Private videos are rejected by the manual URL-ingestion visibility gate.

### Review and publishing

The React Studio is available at http://localhost:8000/studio/ when the PC daemon is running. The root URL (http://localhost:8000/) is the legacy dashboard. Approval remains an explicit review step; do not assume a clip will be publicly published automatically. Publishing availability depends on the connected account, provider authorization, and selected visibility.

## Editing and media limits

- The Gemini range is a suggestion, not the boundary of the user's edit.
- Final selected clips are limited to **5–180 seconds**.
- Keep source timestamps, temporary edit-window timestamps, and rendered-clip timestamps distinct. AI context padding must not restrict the user's selectable range.
- Long audio is processed in bounded windows with overlap and timestamp offsets. Stage 2 extraction must remain bounded; failure must not trigger transcription of an entire multi-hour source.
- The product's storage budget is **10,000,000,000 bytes total** to control cost. This is Dispatch's budget policy, not a claim about the storage provider's technical maximum.
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
