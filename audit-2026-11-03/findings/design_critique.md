# Adversarial Architectural Critique & Audit Report: Dispatch System Architecture

**Auditor Persona:** Design Critic Goldfish (`egm-design` - Hostile Systems Architect)  
**Target Scope:** `PROJECT.md`, `PROGRESS.md`, `docs/ADMIN_OPERATIONS_DASHBOARD.md`, `design.md`, `cloud-worker/`, `dispatch/`, and `android/`  
**Date:** 2026-11-03  
**Verdict:** High Architectural Fragility. Multiple Blocker and Major failure modes identified across memory ceilings, quota starvation, mock publishing, and concurrency.

---

## 1. Executive Summary

Dispatch attempts an ambitious hybrid topology (POCO C65 Mobile Capture → YouTube Cloud Vault / R2 Storage → Cloudflare Worker Orchestration → Windows Local Heavy PC Processing → Multi-Platform Publishing). However, the system currently harbors critical architectural contradictions, unbacked assumptions about third-party platform behavior, fatal worker memory limits, and quota economics that will cause catastrophic failures in production.

---

## 2. Dimension 1: Architectural Bottlenecks & Single Points of Failure

### 2.1 Cloudflare Worker Memory Limit & OOM Crash in Publishing Queue
- **Severity:** 🔴 Blocker
- **Affected Scope:** `cloud-worker/src/index.ts` (lines 57–60, 748–756, 815–823)
- **Detailed Finding:**
  The Cloudflare Worker control plane proudly declares on line 8 that *"Video bytes NEVER pass through this Worker: clients upload/fetch private R2 directly via short-lived presigned URLs"*. However, in `drainPublishingQueue`:
  ```typescript
  const r2Object = await env.BUCKET.get(r2Key);
  const videoBytes = await r2Object.arrayBuffer(); // LINE 753
  ...
  body: videoBytes // LINE 821
  ```
  `CAP_BYTES` for `clip_video` and `render` is set to **512 MiB** (`536,870,912 bytes`). Standard Cloudflare Workers have a hard memory ceiling of **128 MB** (or 256–512 MB on paid plans). Loading a full 1080p 60s vertical MP4 (typically 60–150 MB) into memory via `r2Object.arrayBuffer()` immediately triggers an uncatchable Worker Out-Of-Memory (OOM) termination (`Error 1101: Worker exceeded memory limit`).
- **Failure Mode:**
  Any user publishing a high-bitrate or >100 MB clip causes the worker daemon to crash mid-execution, dropping all batch jobs in `drainPublishingQueue`, corrupting the queue state, and failing the publishing lifecycle.
- **Actionable Remediation:**
  Never buffer video payloads in Worker memory. YouTube Resumable Upload protocol must either stream via chunked `ReadableStream` through `fetch` without `arrayBuffer()`, or the upload must be delegated to client presigned URLs / PC daemon rather than piping video bytes through the Worker memory stack.

---

### 2.2 YouTube Data API Daily Quota Exhaustion as a Fatal Ingestion Bottleneck
- **Severity:** 🔴 Blocker
- **Affected Scope:** `cloud-worker/src/index.ts` (lines 1704–1760), `dispatch/youtube_inbox/oauth.py`, `android/app/src/main/java/com/resolvia/dispatch/sync/YouTubeDirectUploadWorker.kt`
- **Detailed Finding:**
  Dispatch migrated its primary phone-to-PC ingestion pipeline to unlisted YouTube uploads (`YouTubeDirectUploadWorker`). The Google YouTube Data API v3 enforces a default project quota of **10,000 units per day**.
  - A single video upload (`videos.insert`) costs **1,600 units**.
  - A title/marker lookup (`search.list`) in `/youtube/uploads/lookup` costs **100 units**.
  - Publishing a YouTube Short costs another **1,600 units**.
  A single solo creator recording a standard 1-hour session creates six 10-minute segments:
  $$\text{Ingest Cost} = 6 \times 1,600 = 9,600\text{ units}$$
  Publishing just **one** clip or performing **4 upload status lookups** exceeds 10,000 units, completely exhausting the quota for the entire application for 24 hours. In a multi-user environment sharing the OAuth client credentials, a single user's recording session will DOS all other users.
- **Failure Mode:**
  Google returns HTTP 403 `quotaExceeded`. The mobile app's WorkManager fails with exponential backoff; PC poller cannot discover uploads; all ingestion and publishing halts globally.
- **Actionable Remediation:**
  Eliminate YouTube as an ingestion transport mechanism. Ingestion must use direct Cloudflare R2 presigned PUTs (or local LAN resumable transfer). Reserve YouTube Data API quota strictly for approved final short-form publishing.

---

### 2.3 4-Hour Processing Latency Trap on YouTube Auto-Captions
- **Severity:** 🟡 Major
- **Affected Scope:** `dispatch/transcription/worker.py`, `PROGRESS.md` (lines 200–215)
- **Detailed Finding:**
  The architecture specifies: `youtube_inbox.status = 'DOWNLOADED' -> WAITING_FOR_TRANSCRIPT -> TRANSCRIBED`. `TranscriptionWorker` sets a `transcript_wait_deadline` of **4 hours (14,400 seconds)**, polling YouTube captions every 5 minutes before falling back to faster-whisper.
  YouTube's automated speech recognition (ASR) backend routinely delays caption generation for unlisted/private videos by several hours, or fails entirely for code-mixed Roman Hinglish/Hindi audio. Waiting up to 4 hours defeats the core value proposition of an "autonomous real-time personal content engine".
- **Failure Mode:**
  Creator records content expecting clips within minutes; instead, the video sits idle in `WAITING_FOR_TRANSCRIPT` for 4 hours before local Whisper finally kicks in.
- **Actionable Remediation:**
  Invert the priority or drastically reduce the deadline: Trigger local `faster-whisper` immediately upon download (or within a 90-second timeout). Whisper int8 on AMD Ryzen 5 CPU processes a 10-minute segment in ~25 seconds. Do not block the pipeline on YouTube's black-box cloud indexing.

---

### 2.4 SQLite Connection Churn & Multi-Threaded WAL Lock Contention
- **Severity:** 🟡 Major
- **Affected Scope:** `dispatch/db.py` (lines 88–110), `dispatch/main.py` (lines 155–250)
- **Detailed Finding:**
  `dispatch/main.py` launches at least 7 concurrent background daemon threads: `TranscriptionWorker`, `HighlightWorker`, `PublishingOutboxWorker`, `RemoteJobPoller`, `CloudSyncWorker`, `PublishedClipJanitor`, and `StorageRetentionJanitor`, plus multi-worker FastAPI HTTP routes.
  Every database operation invokes `get_db_connection()`, which connects, runs 4 PRAGMA statements (`PRAGMA journal_mode = WAL;`, `PRAGMA synchronous = NORMAL;`, `PRAGMA busy_timeout = 30000;`, `PRAGMA foreign_keys = ON;`), commits, and closes the connection. Executing `PRAGMA journal_mode = WAL;` on every connection open acquires an exclusive lock check. Under write load (e.g. video engine updating chunk progress while cloud sync commits clip documents and outbox queries pending rows), this design causes high lock contention and intermittent `sqlite3.OperationalError: database is locked`.
- **Actionable Remediation:**
  Set WAL mode and PRAGMAs **once** during `init_db()`. Use a shared thread-safe connection pool or dedicate write operations to a serialized transaction queue, while allowing concurrent read-only WAL connections.

---

## 3. Dimension 2: Cost & Quota Economics

### 3.1 Transliteration Token Bloat & Phantom Gemini Models
- **Severity:** 🟡 Major
- **Affected Scope:** `dispatch/ai_clips/highlight_finder.py` (lines 23–27, 270–315, 620–670)
- **Detailed Finding:**
  1. **Phantom Model Fallbacks:** `DEFAULT_GEMINI_MODELS` declares:
     ```python
     DEFAULT_GEMINI_MODELS = ["gemini-flash-latest", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
     ```
     `gemini-3.5-flash-lite` and `gemini-3.1-flash-lite` do not exist in the Google Gemini API catalog. When `gemini-flash-latest` encounters rate limits (HTTP 429), the sequential fallback tries these non-existent models, failing with HTTP 404 and crashing the highlight worker.
  2. **Token Economic Waste in Transliteration:** `transliterate_tokens_to_hinglish` sends the entire word token list to Gemini to convert Devanagari to Roman Hinglish. For a 30-minute transcript (~4,500 words), this consumes substantial input/output tokens and burns through Gemini 15 RPM / free-tier quotas on secondary formatting instead of reserving LLM budget for semantic analysis.
- **Actionable Remediation:**
  Replace model fallbacks with current valid SDK identifiers (`gemini-2.5-flash`, `gemini-2.0-flash`). Transliterate via deterministic offline phonetic mapping or transliteration libraries (`indic-transliteration`) rather than burning frontier LLM tokens on token-by-token string mapping.

---

### 3.2 Storage Economics: The 10 GB Global Pool vs. 1080p Video Footprint
- **Severity:** 🟡 Major
- **Affected Scope:** `docs/ADMIN_OPERATIONS_DASHBOARD.md` (lines 20–25), `cloud-worker/src/index.ts`
- **Detailed Finding:**
  The Admin Console and Worker enforce an unallocated quota pool calculated from a hard global ceiling:
  $$\text{Global Quota} = 10,000,000,000\text{ bytes (10 GB)}$$
  CameraX continuous 1080p 30fps capture produces ~15–20 Mbps H.264 streams:
  $$\text{Storage Rate} \approx 120\text{ MB/min} \implies 7.2\text{ GB per 60-min session}$$
  A single recording session consumes >70% of the entire system's storage budget. Adding 720p edit window proxies (capped at 512 MB) and vertical clip renders (50–150 MB each) will exhaust the 10 GB limit within **two sessions**, locking out all users with HTTP 413 Quota Exceeded.
- **Actionable Remediation:**
  Document and enforce aggressive R2 lifecycle rules: Raw capture videos must never reside permanently in R2. Immediate transcode down to 720p 1.5 Mbps proxies for editing, and delete staging/proxies immediately upon final clip export.

---

### 3.3 Presigned URL Cryptographic Storm & 10-Minute Expiry Invalidation
- **Severity:** 🟡 Major
- **Affected Scope:** `cloud-worker/src/index.ts` (lines 2660–2835)
- **Detailed Finding:**
  When a client calls `GET /clips?limit=50`:
  The Worker loops over all 50 clips and executes `presign(env, "GET", ...)` up to **3 times per clip** (for `videoUrl`, `editWindowUrl`, and `thumbUrl`). This triggers up to **150 AWS SigV4 HMAC-SHA256 computations** synchronously inside a single worker invocation, driving high subrequest CPU latency.
  Moreover, `PRESIGN_EXPIRES = 600` (10 minutes). If a creator leaves the review editor or mobile studio open for 11 minutes while editing captions, all video and thumbnail URLs expire. Attempting playback or trim fails with HTTP 403 Forbidden without any automatic client refresh mechanism.
- **Actionable Remediation:**
  Do not generate presigned URLs upfront for every item in list endpoints. Return object IDs or stable CDN URLs (protected via signed session cookies or short-lived auth headers), or fetch media URLs on-demand when a clip is selected for preview. Increase presigned expiry to 2–4 hours for active review sessions.

---

## 4. Dimension 3: Faulty Assumptions & Functional Defects

### 4.1 Fake Social Publishing for LinkedIn and X (Twitter)
- **Severity:** 🔴 Blocker
- **Affected Scope:** `cloud-worker/src/index.ts` (lines 977–999, 1060–1075)
- **Detailed Finding:**
  The codebase claims to provide multi-platform publishing for video clips to LinkedIn and X/Twitter. Inspecting `cloud-worker/src/index.ts`:
  - **LinkedIn:**
    ```typescript
    specificContent: {
      "com.linkedin.ugc.ShareContent": {
        shareCommentary: { text: ... },
        shareMediaCategory: "NONE", // LINE 992
      }
    }
    ```
  - **Twitter / X:**
    ```typescript
    const tweetText = `${job.title}...`.slice(0, 280);
    const twRes = await fetch("https://api.twitter.com/2/tweets", {
      body: JSON.stringify({ text: tweetText }), // LINE 1067
    });
    ```
  Neither integration uploads any video bytes. LinkedIn is set to `shareMediaCategory: "NONE"` (a pure text status update), and Twitter posts a text tweet without invoking the Twitter Media Upload API (`upload.twitter.com/1.1/media/upload.json` chunked upload protocol).
- **Failure Mode:**
  Users approve vertical video clips for multi-platform distribution; Dispatch posts text snippets with no video attached, silently failing user expectations.
- **Actionable Remediation:**
  Implement true video upload workflows: Twitter requires chunked media upload (`INIT`, `APPEND`, `FINALIZE`) followed by status polling; LinkedIn requires registering an upload with `restli` asset services and uploading binary video to the upload mechanism before creating a video share.

---

### 4.2 Unbacked Assumptions on Android Background Upload Survival (HyperOS / MIUI)
- **Severity:** 🟡 Major
- **Affected Scope:** `android/app/src/main/java/com/resolvia/dispatch/recorder/SegmenterEngine.kt` (lines 401–432), `android/app/src/main/java/com/resolvia/dispatch/sync/YouTubeDirectUploadWorker.kt`
- **Detailed Finding:**
  `SegmenterEngine` acquires a `WakeLock` during active recording via `RecordingForegroundService`. However, once the session stops (`stopSession`), the foreground service terminates (`RecordingForegroundService.stop(context)` at line 392). The segment upload is handed off to WorkManager (`dispatch_youtube_uploads`).
  On MediaTek devices running Xiaomi HyperOS / MIUI (the target POCO C65), background WorkManager tasks are aggressively throttled, CPU cores are frozen, and network sockets are severed within 60 seconds of screen lock unless the app is running an active Foreground Service with an ongoing notification.
- **Failure Mode:**
  When a user stops recording and locks their phone, uploads stall indefinitely until the user manually re-opens the app.
- **Actionable Remediation:**
  Keep the upload process inside an active Foreground Service (`dataSync` foreground service type) with a visible ongoing upload notification until the outbox queue is completely drained.

---

## 5. Dimension 4: Security & Isolation Risks

### 5.1 Master Token Impersonation Risk (`DISPATCH_WORKER_TOKEN`)
- **Severity:** 🟡 Major
- **Affected Scope:** `cloud-worker/src/index.ts` (lines 582–616), `dispatch/cloud/sync.py` (lines 45–56)
- **Detailed Finding:**
  The Cloudflare Worker accepts `Authorization: Bearer <DISPATCH_WORKER_TOKEN>` accompanied by `X-Dispatch-User-Id: <UUID>`. The PC daemon stores `DISPATCH_WORKER_TOKEN` in a plain `.env` file on disk.
  If this token is exposed or exfiltrated, it grants bearer access to any user UUID specified in `PC_OWNER_USER_IDS` (or all users if `PC_OWNER_USER_IDS` is unset or misconfigured). The bearer can invoke administrative RPCs, wipe R2 storage objects, claim jobs, and delete clips.
- **Actionable Remediation:**
  Enforce asymmetric mutual authentication (mTLS) or public-key cryptographic request signing (e.g. Ed25519 request signatures with public keys registered per PC device in Supabase), eliminating static shared master bearer tokens.

---

### 5.2 Single Symmetric Encryption Key for Multi-Platform OAuth Vault
- **Severity:** 🟡 Major
- **Affected Scope:** `cloud-worker/src/oauth.ts` (lines 49–90), `cloud-worker/src/index.ts`
- **Detailed Finding:**
  All third-party OAuth refresh tokens (Google, Meta, LinkedIn, X) across all users are encrypted using a single static symmetric key: `TOKEN_VAULT_KEY` (AES-256-GCM). There is no user-specific key derivation (such as HKDF using `TOKEN_VAULT_KEY` + `user_id` as salt).
  A compromise of `TOKEN_VAULT_KEY` compromises every connected social account across every registered user in the entire database.
- **Actionable Remediation:**
  Derive per-user encryption keys:
  $$\text{UserKey} = \text{HKDF-SHA256}(\text{IKM}=\text{TOKEN\_VAULT\_KEY}, \text{Salt}=\text{user\_id}, \text{Info}=\text{"dispatch-user-vault"})$$
  This ensures cryptographic domain separation between tenant records.

---

## 6. Audit Findings Matrix & Action Plan

| ID | Subsystem / Scope | Severity | Flaw / Bottleneck | Actionable Remediation |
|---|---|---|---|---|
| **CRIT-01** | `cloud-worker/src/index.ts:753` | **Blocker** | `arrayBuffer()` loads 512MB video into Worker memory, exceeding 128MB RAM limit | Remove memory buffering; stream directly or delegate video transport |
| **CRIT-02** | `cloud-worker/src/index.ts:1704` | **Blocker** | Ingesting via YouTube consumes 1,600 units/video; 1hr session burns 9,600/10,000 units | Ingest via R2 presigned URLs; use YouTube API only for Shorts publishing |
| **CRIT-03** | `cloud-worker/src/index.ts:988, 1060` | **Blocker** | LinkedIn and Twitter publishers only post text, zero video bytes uploaded | Implement real chunked video upload pipelines for X and LinkedIn |
| **CRIT-04** | `dispatch/ai_clips/highlight_finder.py:23` | **Major** | Phantom Gemini models (`gemini-3.5-flash-lite`) crash on 429 fallback | Replace with valid models (`gemini-2.5-flash`, `gemini-2.0-flash`) |
| **CRIT-05** | `dispatch/transcription/worker.py:201` | **Major** | 4-hour caption poll delay before local Whisper fallback stalls engine | Invert order: run local faster-whisper immediately (25s latency) |
| **CRIT-06** | `dispatch/db.py:88` | **Major** | SQLite connection churn executing PRAGMA WAL on every single thread call | Initialize PRAGMAs once; use connection pool with serialized writer |
| **CRIT-07** | `cloud-worker/src/index.ts:2818` | **Major** | 150 SigV4 signatures per `/clips` call; 10-min expiry breaks active editor | Switch to stable media endpoints / on-demand presigning; increase TTL |
| **CRIT-08** | `android/.../SegmenterEngine.kt:392` | **Major** | WorkManager uploads killed by HyperOS battery restrictions when locked | Retain Foreground Service with active notification until queue empty |
| **CRIT-09** | `cloud-worker/src/oauth.ts:49` | **Major** | Single static AES key for all user tokens across database | Implement HKDF per-user key derivation |
| **CRIT-10** | `docs/ADMIN_OPERATIONS_DASHBOARD.md` | **Major** | 10 GB global quota pool is exhausted after ~2 recording sessions | Transcode to 720p proxies; enforce immediate purge of raw staging |
