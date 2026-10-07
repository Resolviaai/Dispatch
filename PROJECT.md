# Project: Dispatch — Autonomous Personal Content Engine

## Current Architecture (2026-10-07)

The active target is **Phone → Record → YouTube; PC → detect upload → download original → YouTube captions (Whisper only when unavailable) → Gemini → FFmpeg clip → PC dashboard**. Phone-to-PC LAN/Tailscale sync and discovery are obsolete. The current handoff and milestone status are in `PROGRESS.md`; the architecture and feature tables below are retained as historical project context and are not current requirements where they conflict with this section.

## Architecture
Dispatch is a dual-tier autonomous personal content engine:
1. **Android Capture Tier (POCO C65)**:
   - High-definition 1080p FHD continuous video capture via CameraX.
   - Foreground service (`RecordingForegroundService`) with partial `PowerManager.WakeLock` to survive screen-off/dim sleep on HyperOS.
   - Non-blocking rolling segmenter (`SegmenterEngine`) with asynchronous background hashing and instant segment rolling (zero dropped footage).
   - Multi-transport sync engine (`LiveSyncManager`, `ResumableSyncWorker`, `YouTubeDirectUploadWorker`) with cryptographic verification before local pruning.
   - 6 Pro Compose UI screens (Record Home, FullScreen Active Viewfinder, Sessions & Upload Gauge, Live Processing Timeline, 9:16 Clip Reviewer, Settings & Connection).
2. **PC Media & Orchestration Tier (Windows 11 PC)**:
   - Ingestion receiver (`dispatch/sync/receiver.py`) supporting chunked resumable upload with SHA-256 verification and atomic renaming.
   - Secured pairing endpoint (`/api/sync/pairing/config`) preventing unauthorized secret exposure to LAN sniffers.
   - Discovery beacon server (`dispatch/transport/discovery.py`) on UDP 8765.
   - YouTube Cloud Inbox poller and catcher (`dispatch/youtube_inbox/`) with VTT subtitle parsing, rolling deduplication, and faster-whisper fallback.
   - Checkpointed 5-stage pipeline (`VERIFY` -> `TRANSCRIBE` -> `ANALYZE` -> `RENDER` -> `FINALIZE`) with lease renewal heartbeats and raw source retention.
   - Transcription engine (`faster-whisper`, base, int8) primed with Roman Hinglish vocabulary.
   - AI highlight picker (`gemini-2.5-flash`) via header authentication with complete-thought scoring (20-90s), zero canned fake titles, and local heuristic fallback.
   - Video renderer (FFmpeg) generating 9:16 vertical MP4s with animated karaoke ASS subtitles (active-word yellow illumination).
   - Publishing outbox (`dispatch/publisher/outbox.py`) supporting YouTube Shorts, Instagram Reels, LinkedIn, and X, strictly verifying credentials and marking unauthenticated jobs as `blocked_needs_auth`.
   - Web control dashboard (FastAPI + Tailwind 4-layer dark canvas SPA) with real-time pipeline status, vertical clip player, and APK distribution endpoint (`/download/dispatch.apk`).

---

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Pairing Security Hardening | Require auth or mask credentials on `/api/sync/pairing/config`; prevent token leak to LAN sniffers; secure UDP discovery beacon. | M1 | R2 / Acceptance Criteria |
| 2 | Cryptographic Verification & Pruning | LAN chunked upload with offset check; cryptographic SHA-256 verification via `/api/sync/verify-chunk` before local segment pruning. | M1 | R2 / Survey |
| 3 | UDP & Subnet Auto-Discovery | Broadcast on UDP 8765 and /24 subnet sweep for seamless laptop server pairing. | M1 | R2 / Survey |
| 4 | Pipeline Checkpointing & Stage Reset | Checkpoint each stage in DB; reset `attempt_count = 0` across stages in `complete_stage_checkpoint()` to prevent premature lockouts. | M2 | R1 / Survey |
| 5 | Raw Source Video Preservation | Never delete raw source videos in `storage/incoming/` during the finalize stage (`_run_finalize_stage`). | M2 | R1 / R4 / Acceptance Criteria |
| 6 | Retry Cap & Permanent Failure Classification | Transition failing jobs to `FAILED_PERMANENT` after max attempts (5) and classify fatal container corruption immediately. | M2 | R1 / Acceptance Criteria |
| 7 | Non-Blocking Segment Roll | Decouple SHA-256 hashing in `SegmenterEngine.kt` to background IO; immediately start next segment to guarantee zero dropped footage. | M3 | R3 / Acceptance Criteria |
| 8 | Screen-Off / Dim Wake Lock | Add `WAKE_LOCK` permission in `AndroidManifest.xml` and acquire/release `PowerManager.WakeLock` in `RecordingForegroundService.kt`. | M3 | R3 / Survey |
| 9 | 6-Screen Compose UI Polish | Wire camera flip stub, tap-to-focus/AE/AF gestures on Active Viewfinder, real-time pipeline polling on Sessions, and clip video playback. | M3 | R3 / Survey |
| 10 | Gradle Build & Compiler Cleanliness | Configure JDK 17 in Gradle properties/wrapper; fix all 14 Kotlin compiler warnings so `assembleDebug` passes with 0 errors and 0 warnings. | M3 | R3 / Acceptance Criteria |
| 11 | Authentic Publishing & `blocked_needs_auth` | Reject missing credentials with `PermissionError` and mark jobs as `blocked_needs_auth` across all 4 platforms (YouTube, IG, LinkedIn, X). | M4 | R5 / Survey |
| 12 | Web Control Dashboard & APK Distribution | Real-time pipeline visualizer, 9:16 clip playback, integration modals, and verified `/download/dispatch.apk` download link. | M4 | R5 / Acceptance Criteria |
| 13 | Python Test Suite Synchronization | Align test assertions with raw retention and `blocked_needs_auth`; make all test files discoverable via `unittest.TestCase`; 100% pass rate. | M4 | R1 / Acceptance Criteria |
| 14 | Roman Hinglish Whisper Transcription | Whisper configured with Roman Hinglish initial prompt, word timestamps, and VAD filter. | M2 | R4 / Survey |
| 15 | Gemini Flash Highlight Extraction | Authenticated header API call, complete thought 20-90s, Roman Hinglish titles/hooks, zero fake titles, local heuristic fallback. | M2 | R4 / Survey |
| 16 | 9:16 Vertical Video & ASS Karaoke Subtitles | ASS subtitle generator with yellow active-word illumination, FFmpeg fit_blur / crop_follow 9:16 rendering. | M2 | R1 / Survey |
| 17 | YouTube Cloud Inbox Polling & Catcher | Background poller for `[DISPATCH]` private uploads, WebVTT caption parsing with deduplication, and video download. | M1 | R2 / Survey |
| 18 | E2E Test Suite & Adversarial Hardening | Comprehensive opaque-box test suite across Tiers 1-4, published via `TEST_READY.md`, followed by Tier 5 adversarial hardening. | M5 | Acceptance Criteria / E2E Track |

---

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Ingestion, Transport & Pairing Security | Secure `/api/sync/pairing/config` against unauthenticated secret leakage; secure UDP beacon; verify LAN chunked upload and YouTube Cloud Inbox. | None | PLANNED |
| M2 | Media Pipeline Resilience & Stage Checkpointing | Reset `attempt_count` across stage checkpoints in `job_queue.py`; verify raw video retention; verify retry cap (5 attempts -> `FAILED_PERMANENT`). | None | PLANNED |
| M3 | Android Mobile Engine & UI Hardening | Fix `SegmenterEngine.kt` non-blocking segment rolling; add `WAKE_LOCK` to manifest and foreground service; resolve 14 compiler warnings; verify Gradle build with 0 warnings. | M1 | PLANNED |
| M4 | Publishing Outbox, Web Dashboard & Unit Test Sync | Align test assertions (`test_pipeline_runner.py`, `test_destructive.py`); wrap standalone test files into `unittest.TestCase`; ensure `blocked_needs_auth` is asserted; verify web dashboard & APK endpoint. | M1, M2 | PLANNED |
| M5 | Final Milestone: 100% E2E Integration & Verification | Pass 100% of E2E test suite (Tiers 1-4); Tier 5 adversarial hardening; execute end-to-end 5-stage media pipeline with real sample video; verify APK build. | M1, M2, M3, M4, TEST_READY.md | PLANNED |

---

## Interface Contracts

### 1. Ingestion / Sync ↔ Receiver (`dispatch/sync/receiver.py`)
- `POST /api/sync/upload/init`: Headers `x-auth-token` (required). Body: `InitUploadRequest`. Returns `{status: str, remote_offset: int, verified: bool}`.
- `PATCH /api/sync/upload/chunk`: Headers `x-auth-token`, `x-segment-id`, `x-upload-offset`, `x-file-size`, `x-sha256`, `x-session-id`. Returns `{status: "chunk_appended" | "completed", current_offset: int}`.
- `GET /api/sync/verify-chunk`: Headers `x-auth-token`, query `segment_id`, `sha256`, `file_size`. Returns `{verified: bool, status: "VERIFIED" | "HASH_MISMATCH" | "NOT_FOUND"}`.
- `GET /api/sync/pairing/config`: Must NOT return plaintext master auth token or cloud OAuth secrets to unauthenticated clients. If unauthenticated, returns pairing prompt or masked discovery endpoint.

### 2. Orchestrator Job Queue ↔ Pipeline Stages (`dispatch/orchestrator/`)
- `claim_job(worker_id, lease_duration_seconds=60)`: Returns claimed job dictionary.
- `complete_stage_checkpoint(job_id, completed_stage, worker_id)`: Advances job to next stage; resets `attempt_count = 0`.
- `fail_stage_job(job_id, worker_id, error_message, max_attempts=5)`: Marks retry or sets `status = 'FAILED_PERMANENT'` if `attempts >= max_attempts` or unrecoverable fatal error.
- Raw video files in `storage/incoming/` MUST NOT be deleted during `_run_finalize_stage`.

### 3. Publishing Outbox ↔ Platform Adapters (`dispatch/publisher/`)
- `upload_*_*(clip, ...)`: If credentials missing, raises `PermissionError`.
- `process_outbox_queue()`: Catches `PermissionError` and sets outbox job status to `'blocked_needs_auth'`.

### 4. Android App ↔ Hardware / Service
- `SegmenterEngine`: Rolls segments without stopping CameraX capture during hashing. Background coroutine handles disk hashing and database updates.
- `RecordingForegroundService`: Holds `PARTIAL_WAKE_LOCK` throughout active capture session.

---

## Code Layout
- `android/`: Android Studio project (Compose, CameraX, Room, WorkManager).
- `dispatch/`: Python media engine, FastAPI dashboard, pipeline orchestrator, publishers.
  - `ai_clips/`: Gemini Flash highlight extractor and prompt templates.
  - `orchestrator/`: Pipeline runner, state machine, job queue, retry engine.
  - `publisher/`: Multi-platform publishers (YouTube, IG, LinkedIn, X) and outbox processor.
  - `sync/`: Receiver for chunked uploads and pairing endpoints.
  - `transcription/`: Faster-whisper transcriber with Roman Hinglish prompts.
  - `transport/`: Discovery beacon server and network probing.
  - `video_engine/`: Subtitle generator (ASS karaoke), reframer (9:16), and renderer (FFmpeg).
  - `web/`: FastAPI dashboard and templates.
  - `youtube_inbox/`: YouTube Cloud Inbox poller, catcher, and VTT parser.
- `tests/`: Automated unit and integration test suites.
- `storage/`: Media storage directories (`incoming`, `processing`, `clips`, `quarantine`, `youtube_inbox`, `database`).
