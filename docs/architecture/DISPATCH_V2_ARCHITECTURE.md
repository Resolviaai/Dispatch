# Dispatch V2 — Canonical Cloud-First Architecture Specification

> **Document Status:** Authoritative Repository Standard  
> **Effective Date:** November 5, 2026  
> **Release Authority:** Dispatch V2 (`v1.7.29-alpha`, Android `versionCode = 97`)  
> **Supersedes:** `docs/CANONICAL_ARCHITECTURE.md` (legacy Whisper PC edition), `docs/architecture/cloud-migration-plan.md` (legacy PC-centric blueprint).

---

## 1. System Topology & Infrastructure Architecture

Dispatch V2 operates on a **cloud-first, hybrid execution topology** designed for minimal PC dependence, zero mandatory VPS hosting costs, and thumb-driven mobile ergonomics.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        DISPATCH V2 SYSTEM TOPOLOGY                     │
└────────────────────────────────────────────────────────────────────────┘

    ┌────────────────────────┐         ┌────────────────────────┐
    │     Android Client     │         │   Web Review Studio    │
    │  (POCO C65 / Native)   │         │ (Vite / React / Canvas)│
    └───────────┬────────────┘         └───────────┬────────────┘
                │ Direct Upload / State            │ WebSocket / REST
                ▼                                  ▼
    ┌───────────────────────────────────────────────────────────┐
    │                       CLOUD TIER                          │
    │  • Supabase: Auth, PostgreSQL State, Row-Level Security   │
    │  • Cloudflare Worker: API Gateway, Command Queuing        │
    │  • Cloudflare R2: Master Raw Media & Rendered Clips Store │
    └───────────────────────────┬───────────────────────────────┘
                                │
                                ▼
    ┌───────────────────────────────────────────────────────────┐
    │                     GEMINI AI TIER                        │
    │  • Primary Transcription: gemini-3.5-transcribe           │
    │  • Fallback Transcription: gemini-3.5-flash-lite          │
    │  • Semantic Highlight Extraction: gemini-3.5-flash-lite   │
    │  • Dynamic Script Enrichment & Metadata Reasoning         │
    └───────────────────────────┬───────────────────────────────┘
                                │ Job Dispatch (When Needed)
                                ▼
    ┌───────────────────────────────────────────────────────────┐
    │                 OPTIONAL PC EXECUTION WORKER              │
    │  • Local Hardware FFmpeg (AMD Ryzen multithreading)       │
    │  • Heavy batch export / complex canvas video assembly     │
    │  • Never authoritative for database or transcription      │
    └───────────────────────────────────────────────────────────┘
```

---

## 2. The Canonical Transcription Contract (Gemini-Only)

Dispatch V2 **completely eliminates local Whisper, faster-whisper, whisper.cpp, and YouTube caption fallback loops**. All speech-to-text operations execute via official Google Gemini models under strict primary/fallback order:

| Role | Model Identifier | Quota / Protocol | Responsibility |
| :--- | :--- | :--- | :--- |
| **PRIMARY** | `gemini-3.5-transcribe` | 25 RPD (Metered) | Full verbatim transcription, word-level millisecond timestamps, speaker diarization |
| **FALLBACK** | `gemini-3.5-flash-lite` | 500+ RPD | Verbatim phrase/segment transcription with interpolated word-level bounds |

### 2.1 Fault-Tolerant Failover Rules
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

## 3. Waveform-Aware Auto-Cut & Rohit's Speaking Style Directive

Automatic clipping and silence trimming must **never** truncate natural speech or amputate word endings.

### 3.1 Audio Waveform Energy Envelope Check
* **The Root Cause:** Transcript word boundaries indicate lexical timing, but human phoneme decay, consonant release, and trailing breaths extend past lexical boundaries.
* **The Waveform Guard:** When evaluating candidate cut points (e.g. trimming a pause or false start), the auto-cut engine checks the audio waveform RMS energy:
  - Scans forward from the word boundary to confirm energy drops below the ambient noise floor ($-32\text{dB}$).
  - Enforces minimum acoustic padding: **$100\text{ms}$ pre-speech attack** and **$150\text{ms}$ post-speech release**.
  - Snaps cut seams to zero-crossings or local energy minima so transitions are click-free.

### 3.2 Rohit's Speaking Style Invariants
Rohit's sessions involve real-time reasoning, associative ideation, and conversational authenticity:
* **Mechanical Restart Loops** (e.g. *"I mean, I mean, I mean"*): Propose cutting redundant stutters; preserve the final complete formulation.
* **Real-Time Corrections** (e.g. *"I think… actually, no, here's why"*): **PRESERVE**; self-correction represents the core thought process and insight.
* **Conversational Fillers** (*"so"*, *"like"*, *"you know"*, *"the thing is"*): **PRESERVE** natural speaker rhythm; never sterile-purge.
* **Rhetorical Repetition**: Preserve repeated assertions used for comedic or persuasive emphasis.
* **Dead Air vs. Thinking Pauses**: Distinguish true dead air ($>2.5\text{s}$ with no speaker presence) from contemplative pauses ($1.0\text{s}\text{--}2.0\text{s}$). Always present proposed cuts as reviewable candidates.

---

## 4. Execution Boundaries & Cloudflare Worker Limits

| Infrastructure Component | Workloads Permitted | Hard Invariants & Limits |
| :--- | :--- | :--- |
| **Cloudflare Worker** | Auth routing, job claiming/acking, metadata sync, command drain | **No FFmpeg rendering.** 50ms CPU time limit on free tier, 128MB memory limit, 100MB body limit. Media streams pipe directly to R2. |
| **Supabase** | User auth, profiles, cloud clip states, timeline revisions, RLS | Authoritative source of truth for all multi-device state. |
| **Cloudflare R2** | Raw chunk storage, exported MP4s, thumbnail JPEGs | Zero egress fee media distribution. |
| **Android Client (Media3)** | Camera recording, local preview, on-device hardware render | Uses Media3 Transformer + Transformer ASS subtitle overlay for zero-cloud client export. |
| **Optional PC Worker** | Heavy FFmpeg rendering, batch re-framing | Optional accelerator. App functions completely when PC is offline. |

---

## 5. Mobile CapCut Interaction Model & Unified EDL

1. **Main Video Track:** Main filmstrip renders visual segment blocks (`pieces`) separated by 2px cut seams. A clip with no edits is a single piece.
2. **Contextual Thumb Toolbar:**
   - Playhead position determines split point.
   - 44px thumb touch target for **[ ✂️ Split ]** and **[ 🗑️ Delete ]**.
   - No confusing scissors inside text layers.
   - Ripple delete automatically cascades remaining segments forward.
3. **Identical EDL Across Stack:** `frontend/src/captions/timelineEngine.ts` and `dispatch/segments.py` consume identical source-time ranges `[source_start, source_end)` across preview, ASS export, Media3, and FFmpeg.
