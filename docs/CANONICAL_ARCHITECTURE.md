# Dispatch Canonical Architecture & Operating Manual

> **Source of Truth** — Established 2026-10-07.  
> All development, validation, and maintenance must strictly align with this document.

---

## 1. Overall System Pipeline

```text
PHONE (Capture & Cloud Handoff Track)
CameraX (FHD 1080p, 10m rolling segments, background foreground service)
   ↓
WorkManager (Resumable upload with exponential backoff)
   ↓
YouTube Cloud Inbox (Unlisted transport visibility, [DISPATCH] tags, dispatch_id)
   ↓
PC PIPELINE (Pillars 1 to 7)
PILLAR 1: YouTube Ingestion & Media Validation
   ↓ (status: DOWNLOADED)
PILLAR 2: Dual-Stage Timestamped Transcription
   ↓ (status: TRANSCRIBED)
PILLAR 3: Gemini Semantic Highlight Selection
   ↓ (status: CLIPS_DEFINED)
PILLAR 4: FFmpeg Adaptive 9:16 Reframer & Subtitle Renderer
   ↓ (status: CLIPS_CREATED)
PILLAR 5: PC Local Review & Control Dashboard
   ↓ (user action: APPROVED / REJECTED)
PILLAR 6: Multi-Platform Publishing Outbox
   ↓ (status: PUBLISHED)
PILLAR 7: Master Daemon & Coordinator (surrounds and coordinates PC system)
```

---

## 2. The 7 PC Pillars

| # | Pillar | Core Responsibility | Input → Output Contract | Status |
|---|---|---|---|---|
| **1** | **YouTube Ingestion & Media Validation** | Discover uploads via authenticated API, download media, validate file with ffprobe + full frame decode, create durable state | YouTube → `DISCOVERED` → `DOWNLOADING` → `VALIDATING` → **`DOWNLOADED`** | ✅ **Done / Closed** |
| **2** | **Transcription Engine** | Obtain reliable timestamped transcript with asynchronous YouTube caption polling and explicit Whisper fallback | `DOWNLOADED` → `WAITING_FOR_TRANSCRIPT` → **`TRANSCRIBED`** | 🔨 **Next Focus** |
| **3** | **AI Highlights** | Gemini evaluates full transcript to pick 20-90s complete thoughts, hooks, and Roman Hinglish packaging | Transcript → **Clip definitions** (`clips` table) | ⏳ Not verified |
| **4** | **Video Engine & Renderer** | Render 9:16 vertical short-form video with adaptive layout (fit-blur/crop) and burned animated subtitles | Clip definitions + source video → **9:16 MP4 clips** | ⏳ Not verified |
| **5** | **Review Dashboard** | Modern local web interface to play clips, edit titles/tags, toggle public/private, and 1-click approve/reject | Rendered clips → **Approved / Rejected** | ⏳ Needs verification |
| **6** | **Publishing & Outbox** | Upload approved clips to target platforms (YouTube Shorts, Instagram, etc.) with idempotency & retry tracking | Approved clips → **Published media** | ⏳ Partially implemented |
| **7** | **Master Daemon / Coordinator** | Single entry point booting DB, background poller, job queue, and local web dashboard on PC | System boot → **Active coordinator** | ✅ Wired |

---

## 3. Dedicated Track: Phone (POCO C65)

The phone application is a standalone client track, **not** one of the 7 PC pillars.

### Contract:
$$\text{CameraX Recording} \longrightarrow \text{Local Room SQLite} \longrightarrow \text{WorkManager Upload} \longrightarrow \text{YouTube (Unlisted)}$$

### Core Requirements:
1. **Zero Phone↔PC Networking:** No LAN sync, no HTTP receivers, no UDP beacons, no Tailscale handoffs. YouTube is the **only** handoff.
2. **Transport Visibility:** Videos are uploaded as **`unlisted`** with title prefix `[DISPATCH]` and description marker `dispatch_id: <id>`.
3. **10-Minute Rolling Segments:** Continuous background capture split cleanly into rolling segments.
4. **Foreground Service & WakeLocks:** Recording survives screen dim, lock screen, and system Doze.
5. **Durable Local Storage:** Room database tracks segments; files pruned only after upload confirmation.

---

## 4. Pillar 2 Detailed Specification (Transcription Engine)

Pillar 2 governs the transition from `DOWNLOADED` to `TRANSCRIBED`.

```text
DOWNLOADED
    ↓
Check YouTube Captions Available?
    ├── YES ──→ Parse VTT ──→ Normalize Segments ──→ TRANSCRIBED (source='youtube')
    └── NO  ──→ WAITING_FOR_TRANSCRIPT (asynchronous poll window)
                     ↓ (timeout window exceeded)
                Fallback to Local Whisper (Hinglish prompt)
                     ↓
                Normalize Segments ──→ TRANSCRIBED (source='whisper')
```

### Key Decisions & Rules:
1. **Primary Source:** YouTube captions (auto-generated or creator-provided).
2. **Asynchronous Handling:** YouTube captions take minutes to process; the system must transition to `WAITING_FOR_TRANSCRIPT` and check periodically without blocking or failing prematurely.
3. **Explicit Fallback:** If captions remain unavailable after the defined wait timeout window, explicitly transition to local `faster-whisper`.
4. **Source Tracking:** The resulting record must record `transcript_source: 'youtube'` or `transcript_source: 'whisper'`. No hidden fallback.
5. **Unified Output Contract:** Whether from YouTube VTT or Whisper, output must normalize into identical JSON segment structure:
   ```json
   [
     {
       "id": 0,
       "start": 0.00,
       "end": 4.52,
       "text": "Yeh step automate ho chuka hai.",
       "words": [{"word": "Yeh", "start": 0.00, "end": 0.35}]
     }
   ]
   ```
6. **Stop Condition:** Pillar 2 stops strictly at **`TRANSCRIBED`**. It does not call Gemini or FFmpeg.

---

## 5. Architectural Invariants

- **Idempotency:** Any process restart at any stage must recognize existing state and resume without duplicating DB rows or corrupting files.
- **Durable Progress:** State in SQLite advances only *after* the required artifact exists and passes validation.
- **Failures Surface:** If any stage fails, record `status = 'FAILED'` with a descriptive `last_error`; never swallow errors or fake success.
