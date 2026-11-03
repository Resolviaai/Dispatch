# Dispatch System Architecture Blueprints & Component Contracts

**Standard:** Elephant-Goldfish Model (EGM) Architecture Blueprint Standard  
**Date:** 2026-11-03  
**Status:** Canonical Target Architecture (Post-Audit Synthesis)  
**Applies to:** `android/`, `frontend/`, `dispatch/`, `cloud-worker/`

---

## 1. System Topology Overview

Dispatch operates across three primary physical tiers:
1. **Tier 1: Android Mobile Hardware Tier (`android/`)**
   - POCO C65 physical smartphone.
   - CameraX 1080p continuous recording with 10-minute segment rolling.
   - Hosts transparent webview rendering the user interface.
2. **Tier 2: Unified Frontend Tier (`frontend/`)**
   - React 18 + Vite single-page application.
   - Runs in dual environments: inside Android WebView via JavaScript bridge AND as standalone desktop Web Review Studio.
   - Enforces Design System v3 (4-layer elevation, 44px hitboxes, single-direction scroll, numeric tabular alignment).
3. **Tier 3: PC Heavy Media & Processing Engine (`dispatch/`)**
   - Windows 11 PC workstation (AMD Ryzen CPU + GPU).
   - High-throughput video ingestion, faster-whisper transcription, Gemini highlight extraction, FFmpeg 9:16 rendering with animated karaoke captions.
4. **Tier 4: Cloud Control Plane & Storage Tier (`cloud-worker/`)**
   - Cloudflare Worker serverless edge runtime.
   - Supabase PostgreSQL (auth, user quota accounting, metadata).
   - Cloudflare R2 bucket (presigned direct upload/fetch of video proxies and clips).
   - Social OAuth vault & multi-platform publishing scheduler.

```
+--------------------------------------------------------------------------+
|                       TIER 1: ANDROID CAPTURE (POCO C65)                 |
|  +----------------------------+   +------------------------------------+ |
|  | Native CameraX Engine      |   | Transparent Android WebView        | |
|  | (1080p 30fps Segmenter)    |   | (Runs Tier 2 React Application)    | |
|  +--------------+-------------+   +-----------------+------------------+ |
|                 | (WakeLock + ForegroundService)    | (DispatchNativeBridge)
+-----------------|-----------------------------------|--------------------+
                  |                                   |
                  v (Direct Upload)                   v (Auth / Queue RPCs)
+--------------------------------------------------------------------------+
|                       TIER 4: CLOUD CONTROL PLANE                        |
|  +---------------------------+       +---------------------------------+ |
|  | Cloudflare R2             | <---> | Cloudflare Worker (Edge)        | |
|  | (Direct Presigned S3/PUT) |       | (JWT Auth, Quota, OAuth Vault)  | |
|  +---------------------------+       +----------------+----------------+ |
|                                                       | (PostgreSQL)     |
|                                                       v                  |
|                                              +-----------------+         |
|                                              | Supabase DB     |         |
|                                              +-----------------+         |
+-------------------------------------------------------+------------------+
                                                        |
                                                        v (RemoteJobPoller)
+--------------------------------------------------------------------------+
|                       TIER 3: PC HEAVY PROCESSING ENGINE                 |
|  +-------------------+  +-------------------+  +-----------------------+ |
|  | Ingestion Worker  |  | faster-whisper    |  | Gemini Highlight      | |
|  | (R2 / YouTube DL) |->| (Local CPU ASR)   |->| Extraction (LLM)      | |
|  +-------------------+  +-------------------+  +-----------+-----------+ |
|                                                            |             |
|  +---------------------------------------------------------v-----------+ |
|  | FFmpeg Video Render Engine (9:16 Crop, Animated Word-Level Captions)| |
|  +---------------------------------------------------------+-----------+ |
|                                                            |             |
|                                                            v             |
|                                                +-----------------------+ |
|                                                | Multi-Platform Outbox | |
|                                                +-----------------------+ |
+--------------------------------------------------------------------------+
```

---

## 2. Component Package Contracts

### Package Contract 1: Android Native Tier (`android/`)
- **Package Root:** `com.resolvia.dispatch`
- **Allowed Inbound Dependencies:** None (Hardware root).
- **Allowed Outbound Dependencies:**
  - Android Jetpack (CameraX, Room, WorkManager, Coroutines).
  - WebKit (`android.webkit.WebView`).
  - Google HTTP Client / OkHttp (presigned R2 upload).
- **Core Invariants:**
  1. *Foreground Service Continuity:* Video recording and subsequent uploads MUST run inside an active `RecordingForegroundService` (type `camera` and `dataSync`) with an ongoing notification. Never delegate upload survival to un-notified WorkManager on MIUI/HyperOS.
  2. *Bridge Encapsulation:* All native capabilities (flash, camera flip, battery status, record start/stop) MUST pass exclusively through `DispatchNativeBridge.kt`.
  3. *Zero Compose Bloat:* No production screens rendered in Jetpack Compose; Compose dependencies retained only if required for future native widgets.

---

### Package Contract 2: Unified Frontend Tier (`frontend/`)
- **Package Root:** `frontend/src/`
- **Allowed Inbound Dependencies:**
  - Invoked by Android WebView via `window.DispatchNativeBridge`.
  - Invoked by browser via HTTP / Web Review Studio.
- **Allowed Outbound Dependencies:**
  - React 18, Tailwind CSS, Lucide React icons.
  - Native Bridge API (`frontend/src/bridge/`).
  - Cloudflare Worker REST API (`/api/*`).
- **Core Invariants:**
  1. *Design System v3 Conformance:*
     - Mobile body text minimum 14px (12px reserved for metadata pills).
     - 44px minimum hitboxes on all clickable elements.
     - 4-layer elevation architecture (`bg-studio` #161616 -> `bg-surface-100` #1C1C1C -> `bg-surface-200` #232323 -> `accent` #2563EB).
     - Hover accent strictly `#1D4ED8`.
  2. *Single-Direction Scroll:* Mobile viewport screens must scroll along strictly ONE axis (no nested dual-axis scrolling).
  3. *Bridge Polyfill:* When running outside Android WebView, `DispatchNativeBridge` must provide safe desktop browser fallbacks for preview mode.

---

### Package Contract 3: PC Orchestration Engine (`dispatch/`)
- **Package Root:** `dispatch/`
- **Allowed Inbound Dependencies:**
  - Local FastAPI web interface (`dispatch/web/`).
  - Remote poller pulling jobs from Cloudflare Worker (`dispatch/cloud/sync.py`).
- **Allowed Outbound Dependencies:**
  - SQLite (WAL mode, serialized single-writer connection pool).
  - `faster-whisper` (int8 CPU transcription).
  - Google GenAI SDK (`google.genai`).
  - FFmpeg / ffprobe system binaries.
- **Core Invariants:**
  1. *Fail-Fast Ingestion:* Video processing must never wait on black-box cloud transcription for more than 90 seconds before triggering local `faster-whisper`.
  2. *No Silent Fallbacks:* Highlight extraction must fail with clear retryable errors when Gemini quota is exhausted; never substitute unverified heuristic clips into user feeds.
  3. *Storage Ceiling Enforced:* 3-tier retention daemon must ensure local disk usage never exceeds configured threshold (default 50 GB).

---

### Package Contract 4: Cloud Control Plane (`cloud-worker/`)
- **Package Root:** `cloud-worker/src/`
- **Allowed Inbound Dependencies:**
  - HTTPS requests from Mobile App, Web Review Studio, and PC Poller.
- **Allowed Outbound Dependencies:**
  - Supabase REST / Auth.
  - Cloudflare R2 bucket (`env.BUCKET`).
  - Social Platform APIs (YouTube, Instagram, Facebook, LinkedIn, X).
- **Core Invariants:**
  1. *Zero Video Buffering in Worker Memory:* The Worker MUST NEVER load video files into memory via `r2Object.arrayBuffer()`. All video transfers must stream chunked or use direct presigned URLs.
  2. *Domain-Separated Cryptography:* OAuth tokens in Supabase MUST be encrypted using HKDF-derived keys unique to each `user_id`.
  3. *True Media Publishing:* Social media publishing handlers must execute valid binary media upload protocols before creating status posts.

---

## 3. Communication & Data Flow Contracts

### Flow A: Mobile Capture & Direct Cloud Upload
```
[User Taps Record] -> [Bridge.startRecording()] -> [SegmenterEngine.kt]
         |
    (10-minute segment completes or User stops)
         |
         v
[Android] -- POST /api/uploads/reserve --> [Cloudflare Worker]
         <-- Returns Presigned R2 PUT URL --
         |
         v (Direct PUT with progress)
[Android] --------------------------------> [Cloudflare R2 Bucket]
         |
         v
[Android] -- POST /jobs/submit -----------> [Cloudflare Worker] -> [Supabase DB]
```

### Flow B: Autonomous PC Processing & Cloud Sync
```
[PC Poller] -- GET /jobs/claim -----------> [Cloudflare Worker]
         <-- Returns Job (R2 Key) ---------
         |
         v (Direct GET)
[Cloudflare R2] --------------------------> [PC Disk (staging)]
         |
         v
[faster-whisper] -> Transcribes audio to word-level JSON (25 seconds)
         |
         v
[Gemini API]     -> Analyzes transcript & returns highlight candidate timestamps
         |
         v
[FFmpeg Engine]  -> Crops 9:16, burns animated captions, encodes final MP4
         |
         v
[PC Daemon] ---- POST /sync/clips ---------> [Cloudflare Worker] -> [Supabase DB]
```

---

## 4. Failure Isolation & Circuit Breakers

| Failure Scenario | Circuit Breaker Mechanism | Recovery Path |
|:---|:---|:---|
| **Gemini API 429 Quota Exceeded** | Worker enters exponential backoff (15s, 30s, 60s, 120s). Max 5 attempts. | Job held in `WAITING_FOR_LLM`. Manual retry or token reset. |
| **Worker R2 Presign Expiration** | Client intercepts HTTP 403; invokes `/api/uploads/refresh-ticket`. | Fetches fresh presigned URL without restarting upload from zero. |
| **Ambiguous Social Network Error** | Outbox transitions job immediately to `reconciliation_required`. | Halts automated reposts; prompts user with one-click verification check. |
| **MIUI App Lock Freeze** | Ongoing Foreground Service with persistent notification channel. | Prevents Android OS from killing network sockets during upload. |
| **SQLite DB Lock Contention** | Dedicated writer queue with busy timeout set to 30,000ms. | Concurrent threads read WAL without blocking writer. |
