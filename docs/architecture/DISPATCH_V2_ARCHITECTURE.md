# Dispatch V2 — Canonical Hybrid Architecture Specification

> **Document Status:** Authoritative Repository Standard  
> **Effective Date:** November 5–7, 2026  
> **Release Authority:** Dispatch V2 (`v1.7.32`, Android `versionCode = 100`)  
> **Supersedes:** Legacy Whisper PC editions, speculative mobile-first/cloud-workflow blueprints.

---

## 1. System Topology & Infrastructure Architecture

Dispatch V2 operates on a **hybrid control-plane / local-workstation execution topology**:
- **Windows PC Workstation:** The **sole engine** for Google Gemini AI transcription, semantic highlight reasoning, dynamic subtitle layout, and hardware-accelerated FFmpeg video rendering.
- **Cloud Tier (Cloudflare Worker + Supabase + Cloudflare R2):** The **control plane and media store** handling authentication, user profiles, storage quotas, presigned R2 URLs, and remote job queues. Cloudflare Workers **never** execute FFmpeg or cloud AI batch workflows.
- **Android Client (Native + Web Review Studio):** Dedicated ingestion, camera recording, timeline review, and remote control interface.

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                   DISPATCH V2 SYSTEM TOPOLOGY                                   │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘

     [ Route A: Camera ]                  [ Route B: YouTube URL ]           [ Route C: Direct Video ]
     Android Phone                        Web / Studio Client                Android / Web Client
           │                                      │                                      │
           ▼                                      ▼                                      ▼
     CameraX 10m Segments                   Submit URL                         Presigned PUT to R2
     Room Outbox Queue                            │                            POST /jobs/direct
     WorkManager Resumable Upload                 │                                      │
           │                                      │                                      │
           ▼                                      │                                      ▼
     YouTube (Unlisted)                           │                            Cloudflare R2 Bucket
     Tag: [DISPATCH]                              │                            (Staged Raw Video)
           │                                      │                                      │
           │ (Does NOT use processing_jobs)       │ (Queues in processing_jobs)          │ (Queues in processing_jobs)
           │                                      └───────────────────────┬──────────────┘
           │                                                              │
           ▼                                                              ▼
   YouTube Data API v3                                        ┌───────────────────────┐
           │                                                  │   CLOUDFLARE WORKER   │
           │                                                  │     CONTROL PLANE     │
           │                                                  │ • Supabase Auth & DB  │
           │                                                  │ • processing_jobs     │
           │                                                  │ • POST /media-url     │
           │                                                  │ • Storage & Quotas    │
           │                                                  └───────────┬───────────┘
           │                                                              │
           │                                      Atomically Claimed via `cloud_claim_job`
           │                                                              │
           ▼                                                              ▼
 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                   WINDOWS PC WORKSTATION                                   │
 │                             (Sole Processing & Rendering Engine)                           │
 ├────────────────────────────────────────────────────────────────────────────────────────────┤
 │ 1. Ingestion Daemons:                                                                      │
 │    • Route A: YouTubeInboxPoller (Discovers & downloads unlisted YouTube MP4s)             │
 │    • Route B: RemoteJobPoller (Claims job, downloads via catcher.py / yt-dlp)              │
 │    • Route C: RemoteJobPoller (Claims job, calls POST /media-url, streams dir_<id>.mp4)    │
 │                                                                                            │
 │ 2. AI Processing Pipeline:                                                                 │
 │    • gemini-3.5-flash-lite (Stage 1 full audio transcription & semantic highlight mining) │
 │    • gemini-3.5-transcribe (Stage 2 millisecond-accurate word-level timestamps)            │
 │    • Waveform energy auto-cut (-32dB RMS floor, 100ms attack, 150ms release)               │
 │                                                                                            │
 │ 3. Media Rendering:                                                                        │
 │    • Multithreaded FFmpeg (AMD Ryzen hardware acceleration)                                │
 │    • Framing layouts: Crop 9:16, Fit + Blur, Fit + Black, Native 16:9                      │
 │                                                                                            │
 │ 4. Mobile Retention Handoff:                                                               │
 │    • Serves GET /api/sync/youtube-ack                                                      │
 │    • Verifies physical file existence & SHA-256 against actual disk bytes                  │
 │    • Returns cryptographically signed HMAC-SHA256 receipt for safe phone deletion          │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Ingestion Route Matrix & Protocols

| Attribute | Route A: Android Camera Recording | Route B: YouTube URL Ingestion | Route C: Direct Video Upload |
| :--- | :--- | :--- | :--- |
| **Input Source** | Android CameraX 1080p/30 fps (~10m chunks) | Public or Unlisted YouTube URL | Pre-recorded MP4 (User file / device) |
| **Transport** | Resumable PUT to YouTube Data API v3 | yt-dlp direct download on PC | Presigned PUT to Cloudflare R2 |
| **Queue Mechanism** | **YouTube Inbox Poller** (No `processing_jobs`) | Supabase `processing_jobs` (`source='youtube'`) | Supabase `processing_jobs` (`source='direct'`) |
| **Queue Consumer** | `YouTubeInboxPoller` (Periodic/Boot daemon) | `RemoteJobPoller` (`claim_remote_job`) | `RemoteJobPoller` (`claim_remote_job`) |
| **PC Media Handler** | `youtube_inbox.catcher.ingest_video()` | `youtube_inbox.catcher.ingest_video()` | `dispatch.direct_ingest._direct_handler()` |
| **Processing Scope** | AI highlights + full clip candidate set | AI highlights + full clip candidate set | **One full-length captioned clip** ($\le 180.5s$) |
| **Object Caps** | ~600 MB / 10-minute segment | No strict upload cap (local disk bound) | **512 MiB** hard cap, **180.5s** duration max |
| **Storage Lifecycle** | Phone retains until PC HMAC ACK verified | YouTube hosted; local PC 3-tier retention | Staged in R2; streamed to PC; 3-tier retention |

### 2.1 Route A Ingestion Protocol
1. Android records through CameraX and rolls recordings into ~10-minute segments.
2. Room stores session and segment state (`SegmentEntity`, `RecordingOutboxEntity`).
3. `YouTubeDirectUploadWorker.kt` resumably uploads each segment to YouTube as unlisted, tagged `[DISPATCH]`.
4. **Queue Isolation:** Route A does **NOT** use `processing_jobs`. The PC background daemon `YouTubeInboxPoller` polls YouTube Data API v3, downloads the segment to `YOUTUBE_INBOX_DIR`, and persists it in `youtube_inbox`.
5. PC executes Gemini transcription, highlight extraction, dynamic captions, and FFmpeg rendering.
6. Android verifies PC ingestion via `GET /api/sync/youtube-ack` and only deletes local footage upon receiving a valid HMAC-SHA256 receipt.

### 2.2 Route B Ingestion Protocol
1. User submits YouTube link via Dispatch Studio or Android client.
2. Row inserted into Supabase `processing_jobs` with `source = 'youtube'`.
3. PC `RemoteJobPoller` claims job atomically via `cloud_claim_job`.
4. PC `catcher.py` downloads video using `yt-dlp` and processes via Gemini and FFmpeg.

### 2.3 Route C Ingestion Protocol
1. Client calls `POST /reserve` with `{ kind: "clip_video", mime_type, expected_bytes, client_token }` -> receives `{ reservation_id, media_object_id, upload_url, expires_in }`.
2. Client PUTs video binary directly to Cloudflare R2 staging key.
3. Client calls `POST /complete` with `{ reservation_id }` -> Cloudflare Worker verifies size via `env.BUCKET.head()`, copies to immutable final key, and activates media object.
4. Client calls `POST /jobs/direct` with `{ media_object_id, title }` -> queues job into `processing_jobs` (`source = 'direct'`).
5. PC `RemoteJobPoller` claims job via `cloud_claim_job`.
6. PC `direct_handler` in `dispatch/direct_ingest.py` calls `POST /media-url` with `{ media_object_id }` to get a presigned GET URL (10-minute TTL).
7. PC streams object to disk as `dir_<object_id>.mp4` (`.part` during transfer) and invokes `youtube_inbox.catcher.ingest_direct_video()`.
8. Route C generates **one full-length captioned clip** (capped at 512 MiB and $\le 180.5 seconds$), not AI-selected highlights.

---

## 3. Route A Mobile Storage-Pressure & Retention Contract

### 3.1 Long-Session Bandwidth & Accumulation Realities
At 1080p/30 fps and a standard 8 Mbps encoding bitrate:
- Bitrate: $8\textext{ Mbps} = 1\textext{ MB/s}$.
- 10-Minute Segment: $600\textext{ seconds} \textimes 1\textext{ MB/s} pprox 600\textext{ MB}$.
- 3-Hour Recording: $18\textext{ segments} \textimes 600\textext{ MB} pprox 10.8\textext{ GB}$.

### 3.2 Failure Modes & Mobile Backlog Handling
Finalized segments are queued and uploaded immediately by WorkManager. However, local files are deleted **ONLY after verified PC ingestion**. If upload or PC processing lags, files accumulate on phone storage:
1. **PC Offline Scenario:**
   - Phone successfully uploads segments to YouTube, but the PC is offline and cannot acknowledge.
   - Segments transition to `UPLOADED_AWAITING_PROCESSING`.
   - Local phone copies **MUST BE RETAINED**. The phone must have at least 11–12 GB of free storage to complete a 3-hour recording without PC connectivity.
2. **Slow Network / Upload Lag Scenario:**
   - WorkManager uploads 8 MB chunks with resumable `Content-Range: bytes */total` tracking.
   - If uplink speed is slower than 8 Mbps (real-time recording rate), the upload backlog grows on device.
3. **Delayed YouTube Processing Scenario:**
   - YouTube may take 15–45 minutes to process 1080p video.
   - Android's `reconcileYouTubeProcessingStatuses()` periodically polls YouTube status up to 96 checks (~3.2 hours) at 2-minute intervals.
4. **Emergency Storage Stop Floor:**
   - `RecordingSafetyPolicy.MINIMUM_FREE_BYTES = 512 MB` (536,870,912 bytes) is an **emergency safety stop floor**, NOT a capacity guarantee.
   - `SegmenterEngine` checks disk space every 5 seconds. If free space drops below 512 MB, recording safely halts immediately with `RecordingStorageException`, safely finalizing and committing the active segment to prevent corruption or system lockups.

### 3.3 Phone Deletion vs. PC Retention Law
- **Phone-Side Deletion:** The phone deletes local video segments **only** when `GET /api/sync/youtube-ack` returns `acknowledged=true`, `ingested=true`, matching SHA-256 checksum, and a valid HMAC-SHA256 receipt signed with the paired secret.
- **PC-Side Retention (3-Tier Law):**
  - **Tier 1 (7-Day Source Retention):** Downloaded source segments are retained on PC for 168 hours (7 days) for re-clipping and trim expansions.
  - **Tier 2 (72-Hour Post-Publish Early Release):** Once all clips from a source video are published or approved, source files are released after 72 hours.
  - **Tier 3 (10 GB Disk Safety Ceiling):** If PC inbox storage exceeds 10 GB, oldest sources past 72 hours are pruned FIFO.

---

## 4. The Canonical Transcription Contract (Gemini-Only)

Dispatch V2 **completely eliminates local Whisper, faster-whisper, whisper.cpp, and YouTube caption fallback loops**. All speech-to-text operations execute via official Google Gemini models under strict primary/fallback order:

| Role | Model Identifier | Quota / Protocol | Responsibility |
| :--- | :--- | :--- | :--- |
| **PRIMARY** | `gemini-3.5-transcribe` | 25 RPD (Metered) | Full verbatim transcription, word-level millisecond timestamps, speaker diarization |
| **FALLBACK** | `gemini-3.5-flash-lite` | 500+ RPD | Verbatim phrase/segment transcription with interpolated word-level bounds |

### 4.1 Fault-Tolerant Failover Rules
1. **Direct Audio Bounded Chunks:** Audio is extracted once into 16kHz compressed MP3 and split into 600s chunks with 5.0s boundary overlap (`split_audio_for_transcription`).
2. **Quota-Guarded Primary Execution:** `check_and_increment_daily_transcribe_quota(max_daily_requests=25)` atomically checks the date and count in SQLite. If available, `gemini-3.5-transcribe` is attempted with `AudioTranscriptionConfig(word_timestamp=True, diarization=True)`.
3. **Recoverable Failover to Flash-Lite:** If the primary model encounters a temporary error (HTTP 503, HTTP 429 rate limit, 25/25 daily limit exhausted, or returns empty words), the quota is refunded and execution immediately falls back to `gemini-3.5-flash-lite`.
4. **Permanent Authentication Guard:** If Gemini returns HTTP 401, HTTP 403, `API_KEY_INVALID`, or `PERMISSION_DENIED`, execution raises `PermanentAuthError` immediately. It does **NOT** retry in a loop and does **NOT** burn fallback calls with an invalid key.
5. **Canonical Schema Normalization:** Both models normalize into the identical JSON data structure:
   ```json
   {
     "full_text": "verbatim text...",
     "segments": [
       {
         "id": 0,
         "start": 0.12,
         "end": 2.45,
         "text": "phrase text",
         "speaker": "spk:0",
         "words": [
           {"word": "phrase", "start": 0.12, "end": 0.85, "probability": 1.0, "speaker": "spk:0"}
         ]
       }
     ],
     "source": "gemini_transcribe",
     "model": "gemini-3.5-transcribe"
   }
   ```
6. **Boundary Deduplication:** Seams between adjacent 600s chunks are unified via `_deduplicate_overlapping_segments`, ensuring zero duplicate phrases.

---

## 5. Waveform-Aware Auto-Cut & Rohit's Speaking Style Directive

Automatic clipping and silence trimming must **never** truncate natural speech or amputate word endings.

### 5.1 Audio Waveform Energy Envelope Check
* **The Root Cause:** Transcript word boundaries indicate lexical timing, but human phoneme decay, consonant release, and trailing breaths extend past lexical boundaries.
* **The Waveform Guard:** When evaluating candidate cut points (e.g. trimming a pause or false start), the auto-cut engine checks the audio waveform RMS energy:
  - Scans forward from the word boundary to confirm energy drops below the ambient noise floor ($-32\textext{dB}$).
  - Enforces minimum acoustic padding: **$100\textext{ms}$ pre-speech attack** and **$150\textext{ms}$ post-speech release**.
  - Snaps cut seams to zero-crossings or local energy minima so transitions are click-free.

### 5.2 Rohit's Speaking Style Invariants
* **Mechanical Restart Loops** (e.g. *"I mean, I mean, I mean"*): Propose cutting redundant stutters; preserve the final complete formulation.
* **Real-Time Corrections** (e.g. *"I think… actually, no, here's why"*): **PRESERVE**; self-correction represents the core thought process and insight.
* **Conversational Fillers** (*"so"*, *"like"*, *"you know"*, *"the thing is"*): **PRESERVE** natural speaker rhythm; never sterile-purge.
* **Rhetorical Repetition**: Preserve repeated assertions used for comedic or persuasive emphasis.
* **Dead Air vs. Thinking Pauses**: Distinguish true dead air ($>2.5\textext{s}$ with no speaker presence) from contemplative pauses ($1.0\textext{s}\textext{--}2.0\textext{s}$). Always present proposed cuts as reviewable candidates.

---

## 6. Execution Boundaries & Cloudflare Worker Limits

| Infrastructure Component | Workloads Permitted | Hard Invariants & Limits |
| :--- | :--- | :--- |
| **Cloudflare Worker** | Auth routing, presigned URLs, quota accounting, `processing_jobs` queueing | **No FFmpeg rendering, no AI execution.** 50ms CPU limit, 128MB RAM limit. |
| **Supabase** | User auth, profiles, cloud clip states, timeline revisions, `processing_jobs` | Authoritative source of truth for all multi-device metadata. |
| **Cloudflare R2** | Route C staging, exported MP4s, thumbnail JPEGs | Zero egress fee media store. |
| **Windows PC Workstation** | Sole AI transcription, highlight mining, waveform auto-cut, multithreaded FFmpeg rendering | **The authoritative processing workhorse.** Single consumer of `processing_jobs`. |
| **Android Client** | Camera capture, WorkManager upload, timeline review studio, remote control | Low memory and CPU usage; lightweight client. |

---

## 7. Mobile CapCut Interaction Model & Unified EDL

1. **Main Video Track:** Main filmstrip renders visual segment blocks (`pieces`) separated by 2px cut seams. A clip with no edits is a single piece.
2. **Contextual Thumb Toolbar:**
   - Playhead position determines split point.
   - 44px thumb touch target for **[ ✂️ Split ]** and **[ 🗑️ Delete ]**.
   - No confusing scissors inside text layers.
   - Ripple delete automatically cascades remaining segments forward.
3. **Identical EDL Across Stack:** `frontend/src/captions/timelineEngine.ts` and `dispatch/segments.py` consume identical source-time ranges `[source_start, source_end)` across preview, ASS export, Media3, and FFmpeg.
