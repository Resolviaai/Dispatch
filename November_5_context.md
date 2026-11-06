# November 5 Context: Dispatch Architecture, Verification & Handoff
**Date:** November 5–7, 2026  
**Status:** Architecture Reconciled & Verified / Ready for Production Continuation  
**Current Release:** v1.7.32 (Build 100) — Built, signed, verified, and pushed to GitHub `origin/main`.  
**Latest Git Hash:** `ca19e8b` (Route A multi-account, fail-closed cloud resilience, multi-owner resolution, and legacy account migration)

---

> [!IMPORTANT]
> **Implementation vs. Design Specification Note:** Documented architectural requirements and milestone specifications represent approved technical contracts. They do not constitute proof of implementation completion until the corresponding test suites, release builds, and exit gates are run, passed, and independently verified. Physical-device long-session recording stress tests require actual hardware execution.

## 1. Executive Summary & Verified UI/UX Fixes

All 5 core UI/UX issues plus 1 network CORS blocker (6 total verified items) reported on Android Emulator (`emulator-5554`) have been implemented, tested, and verified live on-device:

1. **Server Status Badge (Issue 1):**
   - In `frontend/src/screens/RecordingsScreen.tsx`, the header now strictly displays **Server Online** (green icon + pulsing text when PC studio is reachable) or **Server Offline** (muted icon + tap-to-retry when offline).
   - The cyan "Cloud" badge was removed from this slot to prevent user confusion.

2. **Route C Direct Video Loading (Issue 2):**
   - Direct video uploads (`#dir_...` / `VID_20260819_021023`) now display full thumbnails, formatted duration (`01:16`), status (`Ready`), and launch directly into the Session Detail and Studio Editor.

3. **Timeline Filmstrip Video Thumbnails (Issue 3):**
   - In `frontend/src/captions/CaptionTimeline.tsx`, clips now continuously render chronological video frame thumbnails across their entire block width (side-by-side tiling at `slotSec = 2.0`), eliminating white/blank timeline strips.

4. **Piece Trim Handles / Levers (Issue 5):**
   - In `frontend/src/captions/CaptionTimeline.tsx`, selecting any video piece (`74.1s`) renders dual 44px touch bounding box handles (`[ ... ]`) on the left and right edges with two-column tactile grip indicators.

5. **Auto-Cut Speech Cleanup Drawer (Issue 6):**
   - In `frontend/src/screens/ClipsScreen.tsx`, converted Auto-Cut into a docked bottom drawer (`top-[50%] fixed inset-x-0 bottom-0 z-40 bg-surface-100/95 backdrop-blur-md border-t border-border`), preserving full video preview visibility at the top.
   - Restyled using strict 4-layer design tokens: Layer 0 Canvas `#161616`, Layer 1 Card `#1C1C1C`, Layer 2 Recessed `#232323`, and Electric Hero Blue `#2563EB`.

6. **Cloudflare Worker CORS Resolution:**
   - In `cloud-worker/src/index.ts`, `/youtube/duration` now serves wildcard CORS headers (`*`), eliminating webview fetch blocks from `http://appassets.androidplatform.net`.

---

## 2. Reconciled V2 Hybrid Architecture: PC Workstation as Sole Processing Engine

### The Three Ingestion Routes
* **Route A (Android Camera Recording — Restored V2 Workflow):**
  - **YouTube Channel Identity Alignment & Enforcement:** PC local YouTube credentials (`youtube_token.json`) must match the phone's cloud vault channel identity. Enforced via `verify_channel_alignment(user_id=...)`.
  - **Fail-Closed Poller Guard:** In `dispatch/youtube_inbox/poller.py`, if `alignment.get("matched")` is False (`mismatch`, `unauthenticated`, `cloud_unavailable`, `ambiguous_owner`, or `legacy_account_needs_reconnect`), `poll_once()` actively halts and returns `[]`, preventing silent polling or orphaned recordings.
  - **Strict Multi-Account & Multi-Owner Disambiguation:** In `dispatch/youtube_connections.py`, multiple YouTube accounts for the same user return `ambiguous_owner` unless `DISPATCH_YOUTUBE_CHANNEL_ID` is explicitly configured. Multiple PC owners in `DISPATCH_PC_OWNER_USER_IDS` require `DISPATCH_USER_ID` rather than arbitrarily picking `owners[0]`.
  - **Fail-Closed Cloud Resilience:** HTTP 500, request timeouts, and connection errors return `status: "cloud_unavailable"` and halt Route A. Intentional offline operation requires explicit opt-in via `DISPATCH_STANDALONE=1`.
  - **Legacy Migrated Account Guard:** Accounts with email or UUID identities in `connected_accounts.account_id` without channel metadata return `status: "legacy_account_needs_reconnect"` with explicit instructions to reconnect in Settings, preventing false channel mismatch errors. Cloud Worker OAuth callback enforces genuine YouTube channel retrieval.
  - Android records 1080p/30 fps MP4 via CameraX, rolling long sessions into ~10-minute segments (~600 MB each at 8 Mbps).
  - Room stores session and segment state (`SegmentEntity`, `RecordingOutboxEntity`).
  - WorkManager resumably uploads each segment to YouTube as unlisted, tagged `[DISPATCH]`.
  - **Queue Disambiguation:** Route A does **NOT** use `processing_jobs`. The PC background daemon `YouTubeInboxPoller` discovers new segments via YouTube Data API v3, downloads the source MP4 to `YOUTUBE_INBOX_DIR`, and processes it locally via Gemini + FFmpeg.
  - **Safe Phone Retention Release:** Android retains local recordings until the PC verifies ingestion via `GET /api/sync/youtube-ack`. The PC confirms physical disk file existence and SHA-256 match, returning a signed HMAC-SHA256 receipt before the phone deletes local media.

* **Route B (YouTube Link Ingestion — Unchanged):**
  - User submits a public or unlisted YouTube URL via Dispatch Studio or mobile client.
  - Row queued in Supabase `processing_jobs` (`source = 'youtube'`).
  - PC `RemoteJobPoller` claims the job via `cloud_claim_job`.
  - PC downloads source via `catcher.py` (`yt-dlp`), processes through Gemini transcription + highlight extraction + FFmpeg rendering.

* **Route C (Direct Video Upload — Unchanged & PC-Processed):**
  - User uploads video directly to Cloudflare R2: `POST /reserve` → presigned PUT to R2 → `POST /complete` → `POST /jobs/direct` (`source = 'direct'`).
  - PC `RemoteJobPoller` claims the direct job via `cloud_claim_job`.
  - PC `direct_handler` in `dispatch/direct_ingest.py` calls `POST /media-url` to get a presigned GET URL (10-minute TTL).
  - PC streams object to disk as `dir_<object_id>.mp4` and invokes `youtube_inbox.catcher.ingest_direct_video()`.
  - **Route C Scope & Limits:** Maximum upload: **512 MiB** (`DIRECT_DOWNLOAD_CAP_BYTES = 536870912 + 16 * 1024 * 1024`); maximum duration: **$\le 180.5 seconds$** (~3 minutes). Generates **one full-length captioned clip**, NOT AI-selected highlights.

---

## 3. Route A Mobile Storage-Pressure & 3-Hour Recording Risk

### Bandwidth & Storage Realities
* At 8 Mbps (1080p/30 fps), a 3-hour recording produces **~10.8 GB** of video data across ~18 sequential segments (~600 MB each).
* Finalized segments upload immediately via WorkManager.
* **Storage Accumulation Risk:** Local phone files are deleted **ONLY** after verified PC ingestion. If the PC is offline, upload lags, or YouTube processing is delayed, files accumulate on the phone.
* **Safety Floor vs. Capacity:** `RecordingSafetyPolicy.MINIMUM_FREE_BYTES = 512 MB` is an emergency safety stop floor, **NOT** a capacity guarantee. A 3-hour recording without PC connectivity requires at least **11–12 GB of free phone storage**.
* If free storage reaches the 512 MB safety threshold, `SegmenterEngine` halts recording immediately to protect existing footage from corruption.
* Once the PC acknowledges ingestion, the phone frees local space while the PC retains the source master under the 3-Tier Retention Law (7 days / 72 hours post-publish / 10 GB FIFO ceiling).

---

## 4. Verification Checkpoints & Results

Automated regression verification:
1. `npm --prefix cloud-worker test`: **102/102 PASS (100%)**
2. `python -m unittest tests.test_youtube_channel_identity tests.test_sync_engine tests.test_cluster_c_remediation tests.test_edl_approval tests.test_waveform_autocut`: **65/65 PASS (100%)** (168 tests pass across full CI Python suite)
3. `npm --prefix frontend test -- --run`: **ALL PASS (100%)**
4. CI Workflow Integration: Added `tests.test_youtube_channel_identity` to `.github/workflows/ci.yml`.
4. Android build & keystore verification:
   `powershell scripts/prepare_release.ps1`
   - Release APK compiled and signed: `v1.7.32` (Build 100).
5. **Outstanding Physical Verification:** Physical 3-hour hardware recording stress test with real POCO C65 camera thermal, battery, and background WorkManager behavior remains to be performed on physical hardware.

---

## 5. Architectural Invariants & Governance

1. **PC as Sole Processing Engine:** PC workstation is the exclusive engine for Gemini AI transcription, highlight reasoning, and FFmpeg rendering.
2. **Control Plane Boundary:** Cloudflare Workers + Supabase act as control plane and staging storage only. Zero GCP, AWS, VPS, or cloud FFmpeg dependencies.
3. **Queue Disambiguation:** `processing_jobs` is exclusively used by Route B (`source='youtube'`) and Route C (`source='direct'`). Route A does **NOT** use `processing_jobs`.
4. **Single-Consumer Authority:** Supabase `cloud_claim_job` is called exclusively by the PC `RemoteJobPoller`. Cloudflare Workers never claim or execute jobs.
5. **Zero Premature Phone Deletion:** Local video files on Android are never deleted without cryptographic HMAC-SHA256 receipt verification from PC `/api/sync/youtube-ack`.
6. **Route A Identity Lock:** Poller immediately aborts discovery if local PC YouTube channel does not match the cloud vault channel or if multiple accounts exist without explicit configuration.
