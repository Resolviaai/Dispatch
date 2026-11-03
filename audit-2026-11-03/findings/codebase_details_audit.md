# Codebase vs Documentation Quality Audit (Pedantic Auditor)

**Auditor Persona:** Codebase Details Goldfish (`egm-codebase-details` - Pedantic Quality Auditor)  
**Target Scope:** `PROJECT.md`, `design.md`, and `AGENTS.md` vs. implementation in `frontend/src/`, `android/app/src/`, `dispatch/`, `cloud-worker/`, and `tests/`.  
**Date:** 2026-11-03  
**Verdict:** 14 Total Verified Discrepancies (2 Blocker, 9 Major, 3 Nit). Severe documentation rot and structural divergence between specification and code.

---

## 1. Contradicts the Code

### Finding 1.1: Retired LAN Chunked Sync Endpoints Documented as Active Interface Contracts
- **Severity:** 🔴 Blocker
- **Affected Scope:** `PROJECT.md#L32-L34`, `PROJECT.md#L66-L70` vs `dispatch/sync/receiver.py#L691-L723`
- **Discrepancy Description & Rationale:**
  `PROJECT.md#L66-L70` defines an active Interface Contract for `dispatch/sync/receiver.py`:
  - `POST /api/sync/upload/init`: Body `InitUploadRequest`, returns `{status: str, remote_offset: int, verified: bool}`.
  - `PATCH /api/sync/upload/chunk`: Headers `x-auth-token`, `x-segment-id`, `x-upload-offset`, etc., returns `{status: "chunk_appended" | "completed", current_offset: int}`.
  - Features 1–3 in the Feature Inventory also describe LAN chunked upload with offset check.
  
  **Actual Code:** In `dispatch/sync/receiver.py#L691-L723`, `init_upload_disabled` (`POST /api/sync/upload/init`), `upload_chunk_disabled` (`PATCH /api/sync/upload/chunk`), and `upload_direct_file_disabled` (`POST /api/sync/upload/direct`) have `include_in_schema=False` and unconditionally raise:
  ```python
  HTTPException(status_code=410, detail="Chunked LAN upload retired. Recordings arrive via YouTube.")
  ```
  Furthermore, the schema `InitUploadRequest` does not exist in `receiver.py` or anywhere in the codebase. Citing retired 410 endpoints as active interface contracts causes any incoming engineer or client implementer to build against dead byte receivers.
- **Recommendation:** Replace the retired `/api/sync/upload/*` interface contract in `PROJECT.md` with the active ingestion contracts: `POST /api/sync/reconcile`, `GET /api/sync/verify-chunk`, and `GET /api/sync/youtube-ack`.

---

### Finding 1.2: Mobile Tier Documented as "6 Pro Compose UI Screens" vs Real React WebView Architecture
- **Severity:** 🔴 Blocker
- **Affected Scope:** `PROJECT.md#L14`, `PROJECT.md#L40`, `PROJECT.md#L89`, `AGENTS.md#L3`, `AGENTS.md#L9` vs `android/app/src/main/java/com/resolvia/dispatch/MainActivity.kt#L254-L460` and `frontend/src/App.tsx#L15-L52`
- **Discrepancy Description & Rationale:**
  `PROJECT.md` asserts that the Android capture tier comprises *"6 Pro Compose UI screens (Record Home, FullScreen Active Viewfinder, Sessions & Upload Gauge, Live Processing Timeline, 9:16 Clip Reviewer, Settings & Connection)"* and lists the Android project as *"Compose, CameraX, Room, WorkManager"*. `AGENTS.md` also frames the frontend scope as *"Web Review Studio & Android Native Jetpack Compose"*.
  
  **Actual Code:** In `MainActivity.kt#L254-L460`, Android has **zero** Compose screens. The root view is a native `FrameLayout` embedding a CameraX `PreviewView` underneath, overlaid by a full-bleed transparent `android.webkit.WebView` (`webView.loadUrl("http://appassets.androidplatform.net/assets/web/index.html")`). The entire interface is driven by the React application in `frontend/src/` (`CameraScreen`, `RecordingsScreen`, `ClipsScreen`, `SettingsScreen`, and `StudioWorkstation`) communicating over `DispatchNativeBridge.kt`.
- **Recommendation:** Update `PROJECT.md` and `AGENTS.md` to truthfully reflect the hybrid architecture: Android Native Hardware Tier (CameraX + Room + WorkManager + `RecordingForegroundService`) + Full-Bleed Transparent WebView hosting the React SPA via `DispatchNativeBridge`.

---

### Finding 1.3: Gemini Highlight Model Name and Silent Local Heuristic Fallback Drift
- **Severity:** 🟡 Major
- **Affected Scope:** `PROJECT.md#L22`, `PROJECT.md#L46` vs `dispatch/ai_clips/highlight_finder.py#L3-L5`, `dispatch/ai_clips/highlight_finder.py#L22-L28`, and `dispatch/ai_clips/highlight_finder.py#L696-L717`
- **Discrepancy Description & Rationale:**
  `PROJECT.md` specifies the AI highlight extraction model as `gemini-2.5-flash` and claims the pipeline includes *"local heuristic fallback"*.
  
  **Actual Code:** In `dispatch/ai_clips/highlight_finder.py#L22-L28`, the default candidate models are:
  ```python
  DEFAULT_GEMINI_MODELS = [
      "gemini-flash-latest",
      "gemini-3.5-flash-lite",
      "gemini-3.1-flash-lite"
  ]
  ```
  Furthermore, the module docstring and implementation explicitly mandate:
  > *"Pillar 3 authority: strictly relies on Gemini API with structured candidate validation. Never substitutes a silent local heuristic."*
  
  In `call_gemini_generate_content#L696-L699`, if all Gemini candidate models fail, it raises `RuntimeError(full_err)`. `extract_clips_local_heuristic` is retained only for offline test suites, not used as a runtime fallback in production worker execution.
- **Recommendation:** Align `PROJECT.md` with `DEFAULT_GEMINI_MODELS` and clarify that highlight generation fails loudly on quota exhaustion/error rather than silently substituting canned heuristic clips.

---

### Finding 1.4: Orchestrator `claim_job` and `fail_stage_job` Contract Signatures Drift
- **Severity:** 🟡 Major
- **Affected Scope:** `PROJECT.md#L73-L75` vs `dispatch/orchestrator/job_queue.py#L93-L130`, `dispatch/orchestrator/job_queue.py#L203-L237`
- **Discrepancy Description & Rationale:**
  `PROJECT.md` defines:
  - `claim_job(worker_id, lease_duration_seconds=60)`
  - `fail_stage_job(job_id, worker_id, error_message, max_attempts=5)`
  
  **Actual Code:**
  - In `job_queue.py#L93`, default lease duration is 300 seconds:
    `claim_job(worker_id: str, lease_duration_seconds: int = 300)`.
  - In `job_queue.py#L203-L208`, the actual signature is:
    `fail_stage_job(job_id: str, error_message: str, wait_state: Optional[JobStatus] = None, delay_seconds: int = 30)`.
    Neither `worker_id` nor `max_attempts` exist in the function parameter list; `max_attempts` is queried dynamically from SQLite `pipeline_jobs.max_attempts`.
- **Recommendation:** Correct the interface contracts in `PROJECT.md` Section 2 to match the actual Python function signatures in `dispatch/orchestrator/job_queue.py`.

---

### Finding 1.5: Design Token Discrepancies between `AGENTS.md`, `design.md`, and Tailwind/CSS
- **Severity:** 🟢 Nit
- **Affected Scope:** `AGENTS.md#L27` vs `design.md#L6`, `frontend/src/index.css#L23`, and `frontend/tailwind.config.js#L25`
- **Discrepancy Description & Rationale:**
  `AGENTS.md#L27` specifies:
  `Layer 3 (Hero Brand CTA & Focus - 10%): Electric Creator Blue #2563EB (bg-primary, hover #3B82F6, soft glow rgba(37,99,235,0.15))`
  In contrast:
  - `design.md#L6` specifies: `accent: "#2563EB" (both themes; hover "#1D4ED8")`.
  - `frontend/src/index.css#L23` sets `--ds-accent-hover: #1D4ED8;`.
  - `frontend/tailwind.config.js#L25` sets `primary.hover: '#1D4ED8'`.
  `#3B82F6` is an uncalibrated lighter blue from Design System v2 that does not meet the v3 hover contrast specification. While `AGENTS.md` notes that "v3 wins", the hardcoded `#3B82F6` in Section 3 confuses agents reading `AGENTS.md`.
- **Recommendation:** Replace `#3B82F6` with `#1D4ED8` in `AGENTS.md` line 27.

---

## 2. Non-Existent References

### Finding 2.1: Non-Existent File `TEST_READY.md` Cited in Milestone Dependencies
- **Severity:** 🟡 Major
- **Affected Scope:** `PROJECT.md#L49`, `PROJECT.md#L60`
- **Discrepancy Description & Rationale:**
  `PROJECT.md` Feature 18 and Milestone 5 explicitly reference `TEST_READY.md`:
  > *"Comprehensive opaque-box test suite across Tiers 1-4, published via `TEST_READY.md`..."*  
  > *"M5 Dependencies: M1, M2, M3, M4, TEST_READY.md"*
  
  **Actual Codebase:** `TEST_READY.md` does not exist in the root or any subfolder of `c:/CODE/Dispatch`.
- **Recommendation:** Either commit `TEST_READY.md` if it was intended as a test handoff brief, or update `PROJECT.md` to reference the actual test files (`tests/test_destructive.py`, `tests/test_pipeline_runner.py`, `tests/test_api.py`, etc.).

---

### Finding 2.2: Omission of Entire Cloud Tier Subsystem (`cloud-worker/` & Supabase/R2) in `PROJECT.md`
- **Severity:** 🟡 Major
- **Affected Scope:** `PROJECT.md#L7-L26`, `PROJECT.md#L88-L102` vs `cloud-worker/src/index.ts`, `dispatch/cloud/sync.py`
- **Discrepancy Description & Rationale:**
  `PROJECT.md` claims:
  > *"Dispatch is a dual-tier autonomous personal content engine: 1. Android Capture Tier (POCO C65) ... 2. PC Media & Orchestration Tier (Windows 11 PC)"*
  
  In reality, the system has evolved into a tri-tier architecture. A 4,366-line Cloudflare Worker lives in `cloud-worker/src/index.ts` handling:
  - Presigned S3/R2 direct video and thumbnail upload tickets (`aws4fetch`).
  - Supabase end-user JWT authentication and profile quota allocation.
  - Social OAuth token vault and callbacks (YouTube, Instagram, Facebook, LinkedIn, Twitter/X).
  - Background publishing queue drain and token refresh crons.
  - PC caption synchronization (`GET /sync/pc/captions`).
  Moreover, `dispatch/cloud/sync.py` runs an autonomous cloud sync worker on the PC. None of this is documented in `PROJECT.md`.
- **Recommendation:** Update `PROJECT.md` Architecture to formally include "3. Cloud Control-Plane & Storage Tier (Cloudflare Workers + Supabase + Cloudflare R2)".

---

### Finding 2.3: Inactive `DiscoveryBeaconServer` Cited as Live PC Capability
- **Severity:** 🟡 Major
- **Affected Scope:** `PROJECT.md#L18`, `PROJECT.md#L34` vs `dispatch/main.py#L122-L284`
- **Discrepancy Description & Rationale:**
  `PROJECT.md` line 18 cites: *"Discovery beacon server (`dispatch/transport/discovery.py`) on UDP 8765"*.
  
  **Actual Code:** `dispatch/transport/discovery.py` implements `DiscoveryBeaconServer`, but it is **never imported, instantiated, or started** in `dispatch/main.py` or `dispatch/web/app.py`. Because LAN discovery was superseded by YouTube inbox and cloud worker polling, the UDP server is dead code.
- **Recommendation:** Either remove the UDP discovery beacon reference from `PROJECT.md` or explicitly mark it as dormant/legacy code.

---

## 3. Missing Logic Details & Undocumented Edge Cases

### Finding 3.1: Undocumented `/api/sync/youtube-ack` Endpoint and HMAC-SHA256 Receipts
- **Severity:** 🟡 Major
- **Affected Scope:** `PROJECT.md#L66-L71` vs `dispatch/sync/receiver.py#L515-L689`
- **Discrepancy Description & Rationale:**
  `PROJECT.md` lists `GET /api/sync/verify-chunk` as the sole verification mechanism before phone pruning.
  
  **Actual Code:** In `receiver.py#L515-L689`, the authoritative PC ingestion acknowledgement endpoint used by Android is `GET /api/sync/youtube-ack`. It enforces:
  1. Full physical byte hash verification on PC disk (refusing to rely on database metadata alone).
  2. Cryptographic signed receipt generation using a domain-separated HMAC key derived via `hmac(auth_token, "dispatch-ack-receipt-v1", sha256)`.
  3. Acceptance of items across 9 active pipeline statuses (`DOWNLOADED`, `WAITING_FOR_TRANSCRIPT`, `CLIPS_DEFINED`, etc.) so mobile devices do not loop re-uploads during downstream transcription/rendering.
- **Recommendation:** Document `GET /api/sync/youtube-ack` and its receipt schema in `PROJECT.md` Interface Contracts.

---

### Finding 3.2: Storage Retention Janitors and 3-Tier Source Pruning Policy Unmentioned
- **Severity:** 🟡 Major
- **Affected Scope:** `PROJECT.md#L36`, `PROJECT.md#L76` vs `dispatch/main.py#L202-L250`, `dispatch/storage/retention.py`
- **Discrepancy Description & Rationale:**
  `PROJECT.md` specifies that raw source video files must never be deleted during `_run_finalize_stage`. While accurate for `_run_finalize_stage`, it omits the background retention janitors running in `dispatch/main.py`:
  - `_published_clip_janitor`: Purges heavy rendered MP4 video files for published clips after 3 days (`cleanup_expired_published_clips(retention_days=3)`), keeping thumbnails and DB metadata intact.
  - `_storage_retention_janitor`: Enforces `enforce_retention_policy()` with 3 tiers: 7-day source rule, 72-hour published source rule, and emergency FIFO pruning when disk ceilings are breached.
- **Recommendation:** Document the 3-Tier Storage Retention Policy in `PROJECT.md` to prevent false expectations of unbounded local disk retention.

---

### Finding 3.3: Ambiguous Network Error Handling in Publishing Outbox
- **Severity:** 🟡 Major
- **Affected Scope:** `PROJECT.md#L42`, `PROJECT.md#L78-L81` vs `dispatch/publisher/outbox.py#L33-L98`, `dispatch/publisher/outbox.py#L551-L567`
- **Discrepancy Description & Rationale:**
  `PROJECT.md` describes outbox retry caps and `blocked_needs_auth` for missing credentials.
  
  **Actual Code:** In `outbox.py#L33-L98`, Dispatch implements strict network idempotency via `is_ambiguous_network_error`. When socket timeouts, disconnects, or TCP resets occur during an upload, Dispatch **never** retries the post automatically. Instead, it transitions the outbox job immediately to `reconciliation_required` to eliminate duplicate posts on social platforms.
- **Recommendation:** Document the `reconciliation_required` state transition and network error classification in `PROJECT.md` Section 3.

---

## 4. Test Infrastructure: Mocks vs Real Behavior

### Finding 4.1: High Mocking Density of Ingestion and AI Boundaries in Test Suites
- **Severity:** 🟡 Major
- **Affected Scope:** `tests/test_pipeline_runner.py#L69-L100`, `tests/test_destructive.py#L150-L203` vs `PROGRESS.md#L80-L83`
- **Discrepancy Description & Rationale:**
  - In `test_pipeline_runner.py#L71`, `probe_video` and `transcribe_video` are patched with static dictionaries and dummy byte streams.
  - In `test_destructive.py#L150`, test comments note: *"The LAN receiver is retired; corruption now means a bad file sitting on PC disk under the segment id"*, demonstrating test accommodation for retired paths.
  - As recorded in `PROGRESS.md#L80-L83`, `from google import genai` previously failed due to virtualenv packaging discrepancies, and live end-to-end runs with POCO C65 and real YouTube uploads remain unexecuted in standard unit test runs.
  - The Python test suite extensively exercises the legacy `pipeline_jobs` table checkpointing, while production traffic flows through `youtube_inbox` and `RemoteJobPoller`.
- **Recommendation:** Consolidate integration tests around the active `youtube_inbox` and `cloud-worker` paths and document clearly in `PROJECT.md` which tests rely on mock boundaries versus live hardware/APIs.

---

## Summary Scorecard

| Category | Blocker | Major | Nit | Total |
| :--- | :---: | :---: | :---: | :---: |
| **1. Contradicts the Code** | 2 | 2 | 2 | 6 |
| **2. Non-Existent References** | 0 | 3 | 0 | 3 |
| **3. Missing Logic & Edge Cases** | 0 | 3 | 1 | 4 |
| **4. Test Infrastructure Drift** | 0 | 1 | 0 | 1 |
| **Total Findings** | **2** | **9** | **3** | **14** |
