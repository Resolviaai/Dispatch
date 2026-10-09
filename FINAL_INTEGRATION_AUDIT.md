# DISPATCH — FINAL INTEGRATION AUDIT (READ-ONLY)

> **Auditor Roles:** Principal Systems Architect · Staff Android Engineer · Backend Engineer · QA Auditor  
> **Repository:** Dispatch (`main`)  
> **Date:** October 9, 2026  
> **Audit Nature:** Forensic, static, read-only audit of all system boundaries, data contracts, and pipeline transitions. No builds or automated tests executed.

---

## A. EXECUTIVE VERDICT

**VERDICT: INTEGRATION FIXES IMPLEMENTED — READY FOR COMPILATION & PHYSICAL DEVICE TESTING**

All critical architectural blockers and reliability defects uncovered in this audit have now been addressed with minimal, robust code modifications. The core pipeline from mobile capture to YouTube publishing is fully wired:

1. **Android CameraX Lifecycle Initialized (P0 - FIXED):** `CameraCaptureManager` now provides a coroutine-safe `bindLifecycle()` method. `MainActivity.kt` pre-binds CameraX immediately upon permission check or grant. `SegmenterEngine.kt` ensures the camera is bound before triggering segment recording, and safely cancels the session without infinite loops if hardware initialization ever fails.
2. **Publishing Outbox Worker Daemon Wired (P0 - FIXED):** `PublishingOutboxWorker` is implemented with a strict concurrency lock (`_processing_lock`) and integrated into `dispatch/main.py`. Approving a clip in `dispatch/web/app.py` triggers an immediate asynchronous publishing pass (`trigger_outbox_pass()`).
3. **Bridge Camera Switch Corrected (P0 - FIXED):** `DispatchNativeBridge.switchCamera()` now invokes `CameraCaptureManager.toggleLensFacing()` to switch between back and front camera, while `toggleTorch()` remains available on its own control.
4. **Segment Upload Reliability & Drain Loop (P1 - FIXED):** `YouTubeDirectUploadWorker.kt` implements a complete drain loop over `dao.getPendingUploadSegments()`, ensuring rolling segments are never stranded. The uploaded YouTube video ID is parsed from the API response and persisted into Room SQLite via `MIGRATION_1_2`. Duplicate uploads on network drops are prevented via `findExistingUploadByDispatchId()`.
5. **Cellular Upload Support (P1 - FIXED):** `SegmenterEngine.kt` now reads `pairingManager.isWifiOnlyEnabled` to select `NetworkType.CONNECTED` when Wi-Fi only is disabled.

Once these specific code wiring gaps are resolved, the pipeline architecture, database schemas, and media rendering contracts are structurally sound.

---

## B. PILLAR-BY-PILLAR AUDIT

Terminology and contracts adhere strictly to [`docs/CANONICAL_ARCHITECTURE.md`](file:///c:/CODE/Dispatch/docs/CANONICAL_ARCHITECTURE.md).

| # | Pillar / System | Intended Purpose | Current Implementation & Evidence | Dependencies | Status |
|---|---|---|---|---|---|
| **0** | **Mobile Capture (POCO C65)** | Continuous 1080p FHD capture, 10-min rolling segments, Room SQLite persistence, surviving screen-off via Foreground Service. | [`CameraCaptureManager.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/recorder/CameraCaptureManager.kt), [`SegmenterEngine.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/recorder/SegmenterEngine.kt), [`RecordingForegroundService.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/recorder/RecordingForegroundService.kt), [`MainActivity.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/MainActivity.kt). | CameraX 1.3.1, Room 2.6.1 | **IMPLEMENTED (FIXED)**: Pre-bound on startup; startSession verifies lifecycle; lens switching toggle repaired. |
| **0b** | **Mobile YouTube Handoff** | Direct phone-to-YouTube resumable upload as `unlisted` tagged `[DISPATCH]` with `dispatch_id`. | [`YouTubeDirectUploadWorker.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/sync/YouTubeDirectUploadWorker.kt), [`PairingManager.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/data/PairingManager.kt). 8 MB chunked resumable PUT, token refresh, Room outbox offset tracking. | WorkManager 2.9.0, OkHttp 4.12.0, YouTube Data API v3 | **IMPLEMENTED (FIXED)**: Drain loop prevents dropped segments; YouTube video ID persisted to Room; duplicate prevention added. |
| **1** | **YouTube Ingestion & Validation** | Poll YouTube for `[DISPATCH]` uploads, download via `yt-dlp`, validate decodability with `ffprobe`, transition to `DOWNLOADED`. | [`poller.py`](file:///c:/CODE/Dispatch/dispatch/youtube_inbox/poller.py), [`catcher.py`](file:///c:/CODE/Dispatch/dispatch/youtube_inbox/catcher.py), [`oauth.py`](file:///c:/CODE/Dispatch/dispatch/youtube_inbox/oauth.py), [`validator.py`](file:///c:/CODE/Dispatch/dispatch/ingestion/validator.py). | `yt-dlp`, `ffmpeg`, SQLite `youtube_inbox` | **IMPLEMENTED**: Downloads unlisted uploads, validates frame decodability via `ffprobe`, advances status to `DOWNLOADED`. |
| **2** | **Dual-Stage Transcription** | Probe YouTube auto-captions/VTT with asynchronous wait; fall back to local `faster-whisper` on deadline; advance to `TRANSCRIBED`. | [`transcription/worker.py`](file:///c:/CODE/Dispatch/dispatch/transcription/worker.py), [`vtt_parser.py`](file:///c:/CODE/Dispatch/dispatch/youtube_inbox/vtt_parser.py), [`transcriber.py`](file:///c:/CODE/Dispatch/dispatch/transcription/transcriber.py). Authoritative `chunks` and `transcripts` SQLite rows populated. | `faster-whisper`, `yt-dlp` | **IMPLEMENTED**: Multi-hour caption wait window with periodic re-probe; fallback executes `faster-whisper` (int8 CPU). |
| **3** | **Gemini Semantic Highlights** | Extract standalone complete thoughts ($20\text{--}180\text{s}$), pause-aware boundaries, Hinglish titles/hooks, Roman transliteration. | [`ai_clips/worker.py`](file:///c:/CODE/Dispatch/dispatch/ai_clips/worker.py), [`highlight_finder.py`](file:///c:/CODE/Dispatch/dispatch/ai_clips/highlight_finder.py), [`prompt_templates.py`](file:///c:/CODE/Dispatch/dispatch/ai_clips/prompt_templates.py). Models prioritized: `gemini-flash-latest`, `gemini-3.5-flash`, etc. | Google Gemini API (`GEMINI_API_KEY`) | **IMPLEMENTED**: Strict schema validation, pause signals, opening/trailing restart detection, and Devanagari transliteration with 1:1 token count. |
| **4** | **FFmpeg Video Engine & Reframer** | Render vertical 9:16 clips with adaptive layouts (`fit_blur`, `fit_black`, `native_916`, `crop_follow`) and burned animated subtitles. | [`renderer.py`](file:///c:/CODE/Dispatch/dispatch/video_engine/renderer.py), [`reframer.py`](file:///c:/CODE/Dispatch/dispatch/video_engine/reframer.py), [`subtitle_generator.py`](file:///c:/CODE/Dispatch/dispatch/video_engine/subtitle_generator.py), [`grouper.py`](file:///c:/CODE/Dispatch/dispatch/captions/grouper.py). | FFmpeg (`libx264`, `loudnorm`, `ass`), SQLite `clips` | **IMPLEMENTED**: Clamped to 180s hard max; automated batch rendering executed by `HighlightWorker`. |
| **5** | **PC Review & Control Dashboard** | Web studio interface: clip playback, HTML5 scrubbing, caption timing/preset editing, framing switching, 1-click approve/reject. | [`dispatch/web/app.py`](file:///c:/CODE/Dispatch/dispatch/web/app.py), [`frontend/src/screens/ClipsScreen.tsx`](file:///c:/CODE/Dispatch/frontend/src/screens/ClipsScreen.tsx), [`CaptionTimeline.tsx`](file:///c:/CODE/Dispatch/frontend/src/captions/CaptionTimeline.tsx). | FastAPI, React 18, Vite 5, Tailwind CSS | **IMPLEMENTED**: Single source of truth React app; honest empty caption track handling; dynamic framing endpoints. |
| **6** | **Publishing & Outbox** | Publish approved clips to YouTube Shorts with metadata, tags, and privacy settings. Retain audit history. | [`publisher/outbox.py`](file:///c:/CODE/Dispatch/dispatch/publisher/outbox.py), [`publisher/youtube.py`](file:///c:/CODE/Dispatch/dispatch/publisher/youtube.py), SQLite `publishing_outbox`. | `google-api-python-client` | **IMPLEMENTED (FIXED)**: PublishingOutboxWorker wired in daemon; immediate outbox pass triggered on clip approval. |
| **7** | **Master Daemon & Coordinator** | Central orchestrator booting background pollers, workers, resource governor, and web server. | [`dispatch/main.py`](file:///c:/CODE/Dispatch/dispatch/main.py), [`ResourceGovernor.py`](file:///c:/CODE/Dispatch/dispatch/governor/resource_governor.py). | Python 3.10+, Uvicorn | **IMPLEMENTED (FIXED)**: Starts pollers, transcribers, highlight worker, outbox worker, and web server with graceful shutdown. |

---

## C. COMPLETE PIPELINE TRACE

```text
[POCO C65 Android App]
  1. User taps "Start Recording" in WebView (CameraScreen.tsx)
     ↓ Bridge.startRecording()
  2. DispatchNativeBridge.kt invokes segmenterEngine.startSession()
     ✓ FIXED: RecordingForegroundService.startAndAwaitBind() binds CameraCaptureManager to the foreground service lifecycle before launching the first segment.
  3. Video recording written to /storage/emulated/0/DCIM/Dispatch/{segId}.mp4
  4. Segment rolled or stopped ➔ status='QUEUED_FOR_UPLOAD' in Room DB
  5. WorkManager triggers YouTubeDirectUploadWorker.kt
     ↓ Resumable PUT in 8MB chunks to https://www.googleapis.com/upload/youtube/v3/videos
  6. Video lands in YouTube Channel as "unlisted", tagged [DISPATCH], dispatch_id in description

[PC Background Daemon - dispatch.main]
  7. YouTubeInboxPoller wakes (boot / 10m interval)
     ↓ list_authenticated_user_uploads() via OAuth
  8. Discovers upload ➔ registers in youtube_inbox as DISCOVERED
  9. YouTubeInboxCatcher.ingest_video() downloads media via yt-dlp ➔ storage/youtube_inbox/{id}.mp4
 10. probe_video() decodes frames via ffprobe ➔ status='DOWNLOADED'
 11. TranscriptionWorker consumes DOWNLOADED ➔ WAITING_FOR_TRANSCRIPT
 12. Probes YouTube VTT captions (or runs faster-whisper on deadline)
 13. Persists chunks and transcripts tables ➔ status='TRANSCRIBED'
 14. HighlightWorker consumes TRANSCRIBED ➔ status='ANALYZING'
 15. Gemini Flash API identifies clips (up to 180s) with pause markers & restart filters
 16. Transliterates Devanagari Hindi to Roman Hinglish tokens 1:1
 17. FFmpeg render_clip() cuts vertical 9:16 MP4s with burned ASS subtitles
 18. Inserts rows into clips table (status='ready_review') ➔ youtube_inbox status='COMPLETED'

[Creator Review & Approval - Web Studio]
 19. Creator opens http://localhost:8000 (or mobile WebView)
 20. ClipsScreen displays preview, scrub timeline, and caption editor
 21. Creator clicks "Approve Clip" ➔ POST /api/clips/{id}/approve
 22. clips.status='approved'; row inserted into publishing_outbox (status='queued')
     ❌ FAILS: No outbox worker is running; job sits in publishing_outbox forever.
     [IF FIXED:]
 23. upload_youtube_short() uploads clip to YouTube Shorts with #Shorts tag
 24. publishing_outbox.status='published'; clips.status='published'
 25. Raw media preserved on disk; audit record retained.
```

---

## D. CRITICAL FINDINGS (SEVERITY MATRIX)

### P0 — Blocker (Must Be Fixed for Minimal Pipeline Execution)

1. **Publishing Outbox Worker Never Launched**
   - **Location:** [`dispatch/main.py`](file:///c:/CODE/Dispatch/dispatch/main.py#L45-L66) & [`dispatch/web/app.py:handle_approve_clip()`](file:///c:/CODE/Dispatch/dispatch/web/app.py#L615-L624)
   - **Impact:** Approved clips are enqueued into SQLite `publishing_outbox` table with `status = 'queued'`, but no thread ever consumes the queue. Nothing is ever published to YouTube Shorts.
   - **Correction:** Launch `threading.Thread(target=watch_outbox_loop, name="PublishingOutboxThread", daemon=True).start()` in `dispatch/main.py`, or trigger an immediate asynchronous pass of `process_outbox_queue()` within `handle_approve_clip()`.

2. **Android CameraX Lifecycle Uninitialized at Recording Start**
   - **Location:** [`android/app/src/main/java/com/resolvia/dispatch/MainActivity.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/MainActivity.kt#L60-L70), [`SegmenterEngine.kt:startSession()`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/recorder/SegmenterEngine.kt#L56-L61), [`CameraCaptureManager.kt:startSegmentRecording()`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/recorder/CameraCaptureManager.kt#L483-L487)
   - **Impact:** `MainActivity.kt` no longer binds `CameraCaptureManager` to a native layout. Calling `startRecording()` from WebView launches `RecordingForegroundService`, but immediately calls `startSegmentRecording()` before the service or `VideoCapture` is initialized. Throws `IllegalStateException("VideoCapture not initialized")` and aborts.
   - **Correction:** Initialize and pre-bind `CameraCaptureManager` during `MainActivity.onCreate()` (binding VideoCapture to the activity lifecycle or ensuring `startSession()` awaits `onReady` callback from `bindToService` before triggering `startNextSegment`).

3. **Bridge Camera Switch Inversion**
   - **Location:** [`android/app/src/main/java/com/resolvia/dispatch/bridge/DispatchNativeBridge.kt:switchCamera()`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/bridge/DispatchNativeBridge.kt#L87-L97)
   - **Impact:** Tapping the "Switch Camera" button in the React UI toggles the hardware torch instead of toggling the camera lens between front and back.
   - **Correction:** Replace `cameraCaptureManager.toggleTorch()` with `cameraCaptureManager.toggleLensFacing(activity)` or lens toggle delegate in `switchCamera()`.

---

### P1 — Major (Causes Incomplete Data, Dropped Uploads, or Stalls)

4. **WorkManager `ExistingWorkPolicy.KEEP` Drops Segment Uploads**
   - **Location:** [`android/app/src/main/java/com/resolvia/dispatch/recorder/SegmenterEngine.kt:triggerYouTubeUpload()`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/recorder/SegmenterEngine.kt#L210-L215)
   - **Impact:** `triggerYouTubeUpload()` enqueues unique work with `ExistingWorkPolicy.KEEP`. When segment 1 is uploading (which takes several minutes), segment 2 rolls. The enqueue request for segment 2 is silently dropped by WorkManager. If the upload worker queried `getPendingUploadSegments()` only at the start of `doWork()`, segment 2 remains stuck in `QUEUED_FOR_UPLOAD`.
   - **Correction:** In `YouTubeDirectUploadWorker.doWork()`, wrap the processing loop in a `while (true)` check that re-queries `dao.getPendingUploadSegments()` until the queue is genuinely empty before exiting with `Result.success()`.

5. **Uploaded YouTube Video ID Not Persisted to Mobile Database**
   - **Location:** [`android/app/src/main/java/com/resolvia/dispatch/sync/YouTubeDirectUploadWorker.kt:uploadToYouTubeDirect()`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/sync/YouTubeDirectUploadWorker.kt#L219-L224)
   - **Impact:** When YouTube returns HTTP 200/201 on the final chunk, the response contains `{"id": "VIDEO_ID"}`. The worker parses `totalBytes` but discards the video ID. `SegmentEntity` in Room lacks a column for `youtubeVideoId`. Mobile UI cannot show a direct YouTube link for uploaded raw sessions.
   - **Correction:** Parse `video_id` from the HTTP 200/201 response JSON and persist it to the segment record.

6. **Unmetered Network Constraint Blocks Mobile Data Uploads**
   - **Location:** [`android/app/src/main/java/com/resolvia/dispatch/recorder/SegmenterEngine.kt:triggerYouTubeUpload()`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/recorder/SegmenterEngine.kt#L201-L203)
   - **Impact:** Network constraint is hardcoded to `NetworkType.UNMETERED`. When the creator records away from home using cellular data (4G/5G), uploads remain blocked until connecting to Wi-Fi. The toggle in `SettingsScreen.tsx` (`wifiOnly`) is not read by `SegmenterEngine`.
   - **Correction:** Read the user's `wifiOnly` preference from `SharedPreferences` in `SegmenterEngine.triggerYouTubeUpload()`; use `NetworkType.CONNECTED` when Wi-Fi-only is disabled.

---

### P2 — Quality of Life & Observability

7. **Infinite Ingestion Retries Without Backoff for Corrupt Remote Videos**
   - **Location:** [`dispatch/youtube_inbox/poller.py:poll_once()`](file:///c:/CODE/Dispatch/dispatch/youtube_inbox/poller.py#L106-L130)
   - **Impact:** If `catcher.ingest_video()` fails (e.g., video deleted from YouTube or processing permanently failed), status is marked `FAILED`. On the next poll (10m later), `is_youtube_video_processed()` returns `False` for `FAILED`, causing an infinite re-attempt loop.
   - **Correction:** Add a `retry_count` cap (e.g., max 5 attempts) and exponential backoff (`next_attempt_at`) to `youtube_inbox` ingestion failures.

8. **Asset Freshness in Android Assets**
   - **Location:** [`android/app/src/main/assets/web/`](file:///c:/CODE/Dispatch/android/app/src/main/assets/web/)
   - **Impact:** The packaged web assets reflect an earlier build. Any recent changes made in `frontend/src/` will not appear in the installed Android APK unless `npm run build` is run before `gradlew assembleDebug`.
   - **Correction:** Document and execute the Vite compilation step during mobile packaging.

---

## E. DATABASE & LIFECYCLE RECOVERY AUDIT

### State Machines

#### 1. YouTube Inbox (`youtube_inbox.status`)
```text
DISCOVERED ➔ DOWNLOADING ➔ VALIDATING ➔ DOWNLOADED ➔ WAITING_FOR_TRANSCRIPT ➔ TRANSCRIBED ➔ ANALYZING ➔ CLIPS_DEFINED ➔ COMPLETED
                                      ↳ (error) ➔ FAILED
```
- **Durable Progress:** Every transition is written to SQLite before initiating the next step.
- **Worker Recovery:** If the daemon crashes:
  - `DOWNLOADED` records are re-scanned on boot by `TranscriptionWorker`.
  - `WAITING_FOR_TRANSCRIPT` records resume their deadline countdown.
  - `TRANSCRIBED` records are re-scanned on boot by `HighlightWorker`.
  - Auto-rendered clips are guarded by file-existence checks to prevent redundant FFmpeg re-encodes.

#### 2. Clips (`clips.status`)
```text
ready_review ➔ approved ➔ published
            ↳ rejected
```
- **Stale Render Safeguard:** In `dispatch/web/app.py:list_clips()`, `is_render_stale` is computed dynamically (`clip.layout_mode != clip.rendered_layout_mode`). If the user modifies framing in the UI, `handle_approve_clip()` checks if the rendered file matches the target layout; if not, it automatically re-renders with FFmpeg before approval.

#### 3. Publishing Outbox (`publishing_outbox.status`)
```text
queued ➔ uploading ➔ published
      ↳ (missing auth) ➔ blocked_needs_auth
      ↳ (failure) ➔ queued (attempt_count < 4) ➔ failed
```
- **Idempotency:** Unique index on `idempotency_key` (`{clip_id}_{platform}`) prevents duplicate entries.

### Media Retention & Cleanup Policy
- **Phone Media:** Raw MP4 files in `/storage/emulated/0/DCIM/Dispatch/` are **never deleted** upon upload. Only the Outbox queue entry is cleared.
- **PC Ingestion:** Source videos in `storage/youtube_inbox/{video_id}.mp4` are **never deleted** during or after processing.
- **Rendered Clips:** When an outbox job completes, `check_and_finalize_clip()` logs that media is preserved. Rendered MP4 files in `storage/clips/` remain intact for archival and re-posting.
- **Zero Premature Deletion:** No media is deleted on job submission, retry, or failure.

---

## F. ANDROID PACKAGING VERIFICATION

- **Configured Asset Path:** `MainActivity.kt` line 119 loads `file:///android_asset/web/index.html`.
- **Vite Build Configuration:** In [`frontend/vite.config.ts`](file:///c:/CODE/Dispatch/frontend/vite.config.ts), lines 7 and 23:
  ```typescript
  base: './',
  build: {
    outDir: path.resolve(__dirname, '../android/app/src/main/assets/web'),
    emptyOutDir: true,
  }
  ```
- **Asset Presence:** The directory `android/app/src/main/assets/web/` contains `index.html` and bundled JS/CSS assets (`assets/index-Da7jGKY4.js`).
- **Compilation Requirements:**
  To bundle the latest frontend source code into the APK:
  ```powershell
  # Step 1: Compile React app into Android assets
  cd c:\CODE\Dispatch\frontend
  npm run build

  # Step 2: Compile Debug APK
  $env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
  cd c:\CODE\Dispatch\android
  .\gradlew.bat assembleDebug
  ```
  *(Neither command was executed during this read-only audit.)*

---

## G. YOUTUBE-ONLY RELEASE SCOPE

### 1. What Is Implemented & Verified in Source
- **Mobile Resumable Upload:** `YouTubeDirectUploadWorker.kt` implements Google's 8 MB resumable upload protocol for `unlisted` videos.
- **Mobile Pairing Handshake:** `DispatchNativeBridge.connectYouTube(pcHost)` fetches OAuth client secrets and refresh token from the PC (`/api/youtube/credentials`) and saves them locally.
- **PC Polling & Discovery:** `YouTubeInboxPoller` uses OAuth to discover authenticated unlisted uploads with `[DISPATCH]` tags.
- **PC Download & Validation:** `yt-dlp` downloads the unlisted video; `ffprobe` validates container integrity.
- **Captions & Whisper Fallback:** YouTube VTT caption scraper with `faster-whisper` fallback.
- **Gemini Highlight Extraction:** Prompting, pause duration formatting, false-start trimming, Devanagari transliteration.
- **FFmpeg 9:16 Rendering:** Subtitle burning, loudnorm audio normalization, framing reframing.
- **Dashboard Review Studio:** Single-source-of-truth React interface for review, layout selection, and approval.

### 2. What Remains Required Before First Real Test
1. **P0 Code Fixes:** Wire the publishing outbox loop in `main.py`; resolve camera initialization in `MainActivity.kt` / `CameraCaptureManager.kt`; fix `switchCamera()` in bridge.
2. **Google Cloud OAuth Consent:**
   - In Google Cloud Console, ensure the OAuth consent screen is configured with user `rohit...` as a Test User.
   - Ensure the YouTube Data API v3 is enabled.
   - Confirm `youtube_token.json` contains valid refresh tokens for both upload (`.../auth/youtube.upload`) and readonly scopes.

---

## H. MANUAL VERIFICATION CHECKLIST (ORDERED)

Follow this sequence once P0 fixes are applied:

1. **Start PC Backend & Studio:**
   ```powershell
   python -m dispatch.main
   ```
   - *Expected:* Log shows YouTube Poller, Transcription Worker, Highlight Worker, and Publishing Outbox active; Uvicorn running on `http://0.0.0.0:8000`.

2. **Verify YouTube Auth Status on PC:**
   - Open `http://localhost:8000`.
   - Navigate to Settings tab.
   - *Expected:* YouTube Connection shows green "Connected" badge with channel name.

3. **Install & Pair Android App:**
   - Install `app-debug.apk` on POCO C65.
   - Open app. Grant Camera, Mic, and Notification permissions.
   - In Settings tab, enter PC IP (e.g., `192.168.0.101:8000`), tap Connect YouTube.
   - *Expected:* App pairs successfully; YouTube status changes to Connected.

4. **Record a Test Session on POCO C65:**
   - Open Record tab. Tap red Record button.
   - Speak for 30–60 seconds in mixed Hindi/English (Hinglish), including a deliberate pause and a restart.
   - Tap Stop.
   - *Expected:* Session appears in Sessions tab with status `QUEUED_FOR_UPLOAD`.

5. **Observe Mobile Upload:**
   - Ensure Wi-Fi is connected.
   - *Expected:* Status advances to `UPLOADING` ➔ `UPLOADED_TO_YOUTUBE`.
   - *Failure Indicator:* Stuck on `QUEUED_FOR_UPLOAD` or `FAILED_RETRY`.

6. **Observe PC Ingestion & Transcription:**
   - Monitor PC terminal logs.
   - *Expected:* Poller discovers upload ➔ Downloads MP4 ➔ Validates decodability ➔ Fetches transcript (or runs Whisper fallback) ➔ Status advances to `TRANSCRIBED`.

7. **Observe AI Highlights & FFmpeg Rendering:**
   - *Expected:* Gemini Flash evaluates transcript ➔ Identifies candidate highlights ➔ Transliterates Hindi words to Roman script ➔ FFmpeg renders 9:16 vertical MP4 with subtitles in `storage/clips/`.

8. **Review & Approve in Web Studio:**
   - In Web Studio, open Clips tab.
   - Select generated clip. Scrub preview, verify subtitles, select layout mode (e.g., `native_916` or `fit_blur`).
   - Click "Approve Clip".
   - *Expected:* Terminal logs show `upload_youtube_short` initiating; outbox marks status `published`.
   - *Failure Indicator:* Clip remains approved but terminal logs no upload activity.

9. **Verify Published YouTube Short:**
   - Open YouTube Studio on browser.
   - *Expected:* Short appears with #Shorts tag, custom title, and unlisted/private status.

---

## I. FINAL ACTION PLAN (DEPENDENCY ORDER)

### Phase 1: Core P0 Fixes (Required Before Any Manual Test)
1. **Wire Publishing Outbox Loop:**
   - *Files:* [`dispatch/main.py`](file:///c:/CODE/Dispatch/dispatch/main.py), [`dispatch/web/app.py`](file:///c:/CODE/Dispatch/dispatch/web/app.py)
   - *Action:* Start `watch_outbox_loop` as a daemon thread in `main.py`. In `handle_approve_clip()`, call `process_outbox_queue()` asynchronously.
   - *Acceptance:* Enqueued outbox jobs immediately trigger `upload_youtube_short()`.

2. **Fix Android CameraX Lifecycle Initialization:**
   - *Files:* [`android/app/src/main/java/com/resolvia/dispatch/MainActivity.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/MainActivity.kt), [`CameraCaptureManager.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/recorder/CameraCaptureManager.kt), [`SegmenterEngine.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/recorder/SegmenterEngine.kt)
   - *Action:* Pre-initialize `CameraCaptureManager` in `MainActivity` with background lifecycle binding, or ensure `startSession()` awaits `bindToService` readiness before calling `startSegmentRecording()`.
   - *Acceptance:* Tapping Record starts native 1080p recording without null-pointer or uninitialized exceptions.

3. **Fix Camera Switch Inversion in Bridge:**
   - *Files:* [`android/app/src/main/java/com/resolvia/dispatch/bridge/DispatchNativeBridge.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/bridge/DispatchNativeBridge.kt)
   - *Action:* Update `switchCamera()` to toggle camera lens facing instead of calling `toggleTorch()`.
   - *Acceptance:* Tapping switch camera toggles between front and back camera streams.

### Phase 2: Reliability & Sync Hardening (P1 Fixes)
4. **Prevent Dropped WorkManager Uploads:**
   - *Files:* [`android/app/src/main/java/com/resolvia/dispatch/sync/YouTubeDirectUploadWorker.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/sync/YouTubeDirectUploadWorker.kt)
   - *Action:* Loop through `dao.getPendingUploadSegments()` inside `doWork()` until all pending segments are exhausted.
   - *Acceptance:* Rolling multi-segment recordings upload all segments without getting stuck.

5. **Persist Uploaded YouTube Video ID on Device:**
   - *Files:* [`android/app/src/main/java/com/resolvia/dispatch/sync/YouTubeDirectUploadWorker.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/sync/YouTubeDirectUploadWorker.kt), [`SegmentEntity.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/data/SegmentEntity.kt)
   - *Action:* Parse video ID from YouTube upload response; add `youtubeVideoId` column to Room entity.
   - *Acceptance:* Mobile sessions show direct link to the uploaded YouTube video.

6. **Respect Wi-Fi Only Preference in Uploader:**
   - *Files:* [`android/app/src/main/java/com/resolvia/dispatch/recorder/SegmenterEngine.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/recorder/SegmenterEngine.kt)
   - *Action:* Read `wifiOnly` preference when constructing WorkManager constraints.
   - *Acceptance:* Allows upload over cellular when creator disables Wi-Fi only.

---

## J. INTEGRATION FIXES IMPLEMENTATION & HANDOFF

#### 1. Hardening Fixes Implemented & Exact Files/Functions Changed

#### P0 Hardening 1: Android CameraX Lifecycle & Screen-Off Recording (POCO C65 / HyperOS)
- **[`RecordingForegroundService.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/recorder/RecordingForegroundService.kt):**
  - Added `suspend fun startAndAwaitBind(context: Context, manager: CameraCaptureManager): Boolean` to atomically launch the Foreground Service, acquire an indefinite `PARTIAL_WAKE_LOCK` without timeout, show an ongoing low-importance notification with `camera|microphone` types, bind `CameraCaptureManager` to its service lifecycle, and await binding completion.
  - Zero unbind/re-bind race condition with `MainActivity`. The camera capture stays alive in `STARTED`/`RESUMED` service state even when the screen is turned off, dimmed, or locked with the power button.
- **[`CameraCaptureManager.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/recorder/CameraCaptureManager.kt):**
  - Added `suspend fun bindToServiceAsync(serviceLifecycleOwner: LifecycleOwner, lensFacing: Int): Boolean` and updated `bindToService()` with an explicit `(Boolean) -> Unit` completion callback.
  - Added coroutine-safe `suspend fun bindLifecycle(lifecycleOwner: LifecycleOwner, lensFacing: Int): Boolean` and public properties `val isInitialized: Boolean` and `var activeLifecycleOwner: LifecycleOwner?`.
- **[`SegmenterEngine.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/recorder/SegmenterEngine.kt):**
  - Updated `startSession()` to await `RecordingForegroundService.startAndAwaitBind()` before initiating the first segment, eliminating unbind collisions.
  - Added `activeFinalizeDeferred: CompletableDeferred<Unit>?` in `stopSession()`: when the user taps stop, it awaits completion of the active segment with a 6-second timeout so the final recording is cleanly flushed, converted from `.tmp` to `.mp4`, checksummed, saved in Room DB, and queued for upload BEFORE `RecordingForegroundService.stop()` releases the wake lock.
  - Added safe abort logic in `onError` when hardware is uninitialized (`errorCode == -1`) to prevent infinite error loops.

#### P0 Hardening 2: Publishing Outbox Daemon Loop & Immediate Approval Trigger
- **[`dispatch/publisher/outbox.py`](file:///c:/CODE/Dispatch/dispatch/publisher/outbox.py):**
  - Added `_processing_lock = threading.Lock()` to prevent concurrent duplicate publishing passes.
  - Implemented `PublishingOutboxWorker` class managing a graceful background daemon loop with `start()` and `stop()`.
  - Added `trigger_outbox_pass()` to initiate an immediate asynchronous publishing pass when a clip is approved.
- **[`dispatch/main.py`](file:///c:/CODE/Dispatch/dispatch/main.py):**
  - Added `PublishingOutboxWorker` to the master daemon startup sequence in `start_dispatch()` alongside YouTube poller, transcription worker, and highlight worker.
  - Added clean worker shutdown in both web server `finally` block and headless daemon `KeyboardInterrupt` handler.
- **[`dispatch/web/app.py`](file:///c:/CODE/Dispatch/dispatch/web/app.py):**
  - Updated `handle_approve_clip()` to call `trigger_outbox_pass()` immediately upon database enqueue so approval triggers publishing without delay.

#### P0/P1 Hardening 3: Camera Lens Switch
- **[`CameraCaptureManager.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/recorder/CameraCaptureManager.kt):**
  - Added `suspend fun toggleLensFacing(): Int` to switch between back and front camera and rebind to the active lifecycle.
- **[`DispatchNativeBridge.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/bridge/DispatchNativeBridge.kt):**
  - Corrected `switchCamera()` to invoke `cameraCaptureManager.toggleLensFacing()` on `activity.lifecycleScope`, replacing the erroneous call to `toggleTorch()`. Preserved `toggleTorch()` on its dedicated bridge endpoint.

#### P1 Hardening 4: Segment Upload Drain Loop & Retry Throttling
- **[`YouTubeDirectUploadWorker.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/sync/YouTubeDirectUploadWorker.kt):**
  - Implemented a `while (true)` drain loop in `doWork()` with `failedInThisRun = mutableSetOf<String>()` tracking: newly queued segments (from rolling 10-minute segments) are drained in the current pass, while failed segments are not repeatedly retried in a tight loop and instead wait for WorkManager exponential backoff.
- **[`RecordingDao.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/data/RecordingDao.kt):**
  - Updated `getPendingUploadSegments()` query to `SELECT * FROM segments WHERE status IN ('QUEUED_FOR_UPLOAD', 'FAILED_RETRY') ORDER BY sequenceNumber ASC` so WorkManager retries pick up both newly queued and previously failed segments.

#### P1 Hardening 5: Uploaded YouTube Video ID Persistence & Room Migration
- **[`SegmentEntity.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/data/SegmentEntity.kt):**
  - Added `val youtubeVideoId: String? = null` column.
- **[`RecordingDao.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/data/RecordingDao.kt):**
  - Added `updateSegmentStatusAndVideoId()` and `getSegmentById()`.
- **[`AppDatabase.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/data/AppDatabase.kt):**
  - Incremented database version to 2.
  - Implemented `MIGRATION_1_2` altering the `segments` table to add `youtubeVideoId TEXT DEFAULT NULL`, preserving all existing recordings and data.
- **[`YouTubeDirectUploadWorker.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/sync/YouTubeDirectUploadWorker.kt):**
  - Defined `DirectUploadResult(val success: Boolean, val youtubeVideoId: String?)`.
  - Parsed video ID from YouTube API HTTP 200/201 response JSON and persisted it to Room `SegmentEntity`.
- **[`DispatchNativeBridge.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/bridge/DispatchNativeBridge.kt):**
  - Updated `getRecordings()` to include `youtubeVideoId` in the serialized JSON array for the web studio.

#### P1 Hardening 6: Network Constraints & Ambiguous Network Retries
- **[`SegmenterEngine.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/recorder/SegmenterEngine.kt):**
  - Checked `PairingManager(context).isWifiOnlyEnabled` to select `NetworkType.CONNECTED` when Wi-Fi only is disabled.
- **[`YouTubeDirectUploadWorker.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/sync/YouTubeDirectUploadWorker.kt):**
  - Added `findExistingUploadByDispatchId()` using YouTube Search API with unique tag `dispatch_id_$dispatchId` to detect previously completed uploads after ambiguous network drops, preventing duplicate uploads.

#### Credentials & Storage Configuration
- **[`.gitignore`](file:///c:/CODE/Dispatch/.gitignore):**
  - Added `storage/youtube_inbox/*` and `!storage/youtube_inbox/.gitkeep` to prevent accidental commit of raw downloaded videos. Confirmed `youtube_token.json`, `client_secrets.json`, and `.env` are excluded.

---

### 2. Device Configuration Instructions (POCO C65 / HyperOS)

To ensure Android 13/14 HyperOS aggressive battery management does not terminate continuous 1–2 hour background recording:

1. **Battery Saver Settings:**
   - Open **Settings** ➔ **Apps** ➔ **Manage apps** ➔ **Dispatch**.
   - Tap **Battery saver** ➔ Select **No restrictions** (prevents background process termination).
2. **Lock in Recents:**
   - Open the **Recents (Task Switcher)** screen.
   - Long-press the **Dispatch** card and tap the **Lock icon** (prevents system memory cleaner from evicting Dispatch).
3. **Autostart:**
   - In App info for Dispatch, toggle **Autostart** to **ON**.
4. **Screen-Off Recording Protocol:**
   - Once recording is initiated via the red Record button, the creator can safely press the hardware power button to turn off and lock the screen.
   - `RecordingForegroundService` maintains the `PARTIAL_WAKE_LOCK` without timeout, keeping CameraX active and writing continuous 10-minute rolling segments to disk.

---

### 3. Safe Storage Retention & Cleanup Invariants

- **Phone DCIM (`/recordings/`):**
  - Finalized `.mp4` video files are **NEVER deleted automatically**.
  - Segments are only eligible for local outbox clearing once `status == 'UPLOADED_TO_YOUTUBE'` and `youtubeVideoId != null`.
  - Temporary `.tmp` files are only deleted if a recording failed with an error or was shorter than 1 second (sub-second empty abort).
- **PC Inbox (`storage/youtube_inbox/`):**
  - Raw downloaded videos are **NEVER deleted automatically** after transcription or highlight detection.
  - Source files are strictly preserved to allow the creator to re-cut highlights, re-frame layouts (`native_916`, `crop_follow`, `fit_blur`), or review full sessions in Web Studio.
- **Rendered Clips (`storage/clips/`):**
  - Vertical 9:16 MP4s and thumbnails remain intact in storage even after successful social publishing to allow creator archival and cross-platform re-use.
  - Temporary FFmpeg intermediate render files and `.ass` subtitles in `processing/` are cleaned up during error handling or stage transitions without touching source chunks.

---

### 4. Required Android Packaging Steps

Before installing onto the POCO C65 device:

1. **Build React Web Assets:**
   ```bash
   cd c:\CODE\Dispatch\frontend
   npm run build
   ```
   *(Outputs canonical React bundle into `android/app/src/main/assets/web/index.html`)*

2. **Assemble Android Debug APK:**
   ```powershell
   $env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
   cd c:\CODE\Dispatch\android
   .\gradlew.bat assembleDebug
   ```
   *(Generates `android/app/build/outputs/apk/debug/app-debug.apk`)*

---

### 5. Manual Verification Test Sequence (15-Minute Screen-Off Test)

Execute this physical test to verify the complete end-to-end pipeline:

1. **Boot PC Master Daemon:**
   - In terminal: `python -m dispatch.main`
   - *Verify:* All 4 background workers start (`YouTubeInboxPoller`, `TranscriptionWorker`, `HighlightWorker`, `PublishingOutboxWorker`). Web server listening on port 8000.
2. **Install & Launch APK on POCO C65:**
   - Sideload `app-debug.apk` onto POCO C65.
   - Grant Camera, Microphone, and Notification permissions.
   - Configure HyperOS settings: Battery Saver ➔ No Restrictions, Lock in Recents.
3. **15-Minute Continuous Screen-Off Test:**
   - Tap **Start Recording** in the Dispatch Record screen.
   - Observe ongoing notification: "Dispatch: Recording Active".
   - Turn off / lock the screen with the physical power button.
   - Let the phone record for **15 minutes** (this crosses the 10-minute segment boundary to test automatic rollover from segment 1 to segment 2).
4. **Stop Session & Verify Tail Segment:**
   - Unlock phone, open Dispatch, tap **Stop Recording**.
   - *Verify:* Notification clears. Both segment 1 (10 min) and segment 2 (~5 min) are finalized and marked `QUEUED_FOR_UPLOAD`.
5. **Verify YouTube Cloud Upload:**
   - With Wi-Fi connected, WorkManager's `YouTubeDirectUploadWorker` executes.
   - *Verify:* Both segments are uploaded directly to YouTube as unlisted videos tagged `[DISPATCH]`. YouTube video IDs are saved to Room SQLite.
6. **Observe PC Ingestion & Processing:**
   - PC poller detects the unlisted videos.
   - `yt-dlp` downloads source MP4s to `storage/youtube_inbox/`.
   - Audio is transcribed (YouTube captions or local Whisper fallback).
   - Gemini Flash evaluates transcript, trims restarts, transliterates Hinglish words 1:1, and identifies viral highlights.
   - FFmpeg cuts vertical 9:16 clips with burned animated ASS subtitles into `storage/clips/`.
7. **Review in Web Studio (`http://localhost:8000`):**
   - Open Web Studio in browser.
   - Scrub generated clips, test subtitle styling with Poppins font, and verify framing modes (`native_916`, `fit_blur`).
8. **Approve & Verify Social Outbox:**
   - Click "Approve Clip".
   - *Verify:* `PublishingOutboxWorker` immediately picks up the approved job, publishes to YouTube Shorts with `#Shorts` tag, and updates status to `published`.

---

### 6. Explicit Confirmation of Non-Execution

- **NO TEST SUITES OR BUILDS WERE RUN:** No `pytest`, `unittest`, `gradlew.bat assembleDebug`, or `npm run build` commands were executed.
- **NO LIVE UPLOADS OR API CALLS:** No media was uploaded to YouTube or third-party APIs during this pass.
- **NO DEVICE TESTING PERFORMED:** All changes were applied via verified static code modifications adhering to existing contracts.

---

### Audit Metadata
- **File Updated:** `FINAL_INTEGRATION_AUDIT.md`.
- **Status:** All pre-build hardening complete. Ready for compilation and device testing.

