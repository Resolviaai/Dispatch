# 📋 Dispatch Implementation Checklist: Two-Stage Funnel, CapCut Trimming & Quota Queue

This document tracks all newly discussed architectural designs, UX patterns, and pipeline improvements that have been agreed upon and are pending implementation in Dispatch.

---

## 1. Two-Stage Funnel Transcription Pipeline
Decouple long-form session ingestion from high-precision clip captioning.

- [x] **1.1 Lightweight Audio Extraction Pipeline**
  - Extract low-bitrate MP3/AAC audio track (`~64-96 kbps`) from incoming 10-minute session chunks using FFmpeg before sending to cloud.
  - Achieves $98\%$ bandwidth reduction ($\approx 6\text{ MB}$ per 10-minute chunk instead of $150\text{--}300\text{ MB}$ video).
  - *Files:* `dispatch/transcription/audio.py`, `dispatch/transcription/worker.py`

- [x] **1.2 Stage 1: Coarse Hook & Segment Discovery (`gemini-3.5-flash-lite`)**
  - Pass the 10-minute audio track to `gemini-3.5-flash-lite` (500 RPD / 250K TPM quota).
  - Ingests in $\approx 2.8\text{ seconds}$ and returns candidate viral moments with coarse timestamps and hook titles.
  - *Files:* `dispatch/transcription/gemini_engine.py`, `dispatch/transcription/worker.py`

- [x] **1.3 Stage 2: Precision Polish (`gemini-3.5-transcribe`)**
  - Send **only** the sliced 30–60s clip audio to `gemini-3.5-transcribe` (25 RPD / 10K TPM quota).
  - Consumes only $\approx 750\text{ tokens}$ per clip.
  - Returns millisecond word-by-word offsets (`start_offset`, `end_offset`, `speaker_label`) in $\approx 1.5\text{ seconds}$.
  - *Files:* `dispatch/transcription/gemini_engine.py`, `dispatch/ai_clips/worker.py`

- [x] **1.4 YouTube Role Separation**
  - Completely remove YouTube unlisted upload from the transcription path.
  - Reserve YouTube Data API v3 strictly for final approved clip publishing to preserve the 100-upload/1,600-unit quota and prevent channel rate limits.
  - *Files:* `dispatch/publisher/youtube.py`, `dispatch/transcription/worker.py`

---

## 2. Padded "Soft Cut" with Head & Tail Safety Margins (Handles)
Eliminate clipped opening words or chopped punchlines caused by coarse timestamp delays.

- [x] **2.1 Bidirectional Padding in Highlight Slicer & Storage**
  - When defining candidate clips from session footage, add a $+4\text{s}$ safety buffer to both ends (`padded_start` and `padded_end`).
  - Saved durably in SQLite `clips` schema with schema migrations.
  - *Files:* `dispatch/ai_clips/highlight_finder.py`, `dispatch/db.py`

- [x] **2.2 Automatic Word-Snap via Gemini 3.5 Transcribe**
  - Transcribe detects the exact millisecond of the first spoken syllable and the final spoken word.
  - Default export auto-snaps to `[first_word.start - 0.2s]` and `[last_word.end + 0.3s]` for a tight, professional cut with zero silence.
  - *Files:* `dispatch/transcription/gemini_engine.py`, `dispatch/ai_clips/worker.py`

---

## 3. Pure CapCut-Style Timeline Trimming (Editor UI)
Non-destructive timeline trimming without cluttering buttons.

- [x] **3.1 Clean Dual Drag Handles**
  - Strictly no extra nudge or micro-step buttons.
  - Left handle drags backward into the padded head margin to reveal earlier speech.
  - Right handle drags forward into the padded tail margin to extend ending speech.
  - Boundary constrained strictly by the physical padded clip cut (`padded_start` to `padded_end`).
  - *Files:* `frontend/src/captions/CaptionTimeline.tsx`

- [x] **3.2 Real-Time Scrub & Instant State Persistence**
  - Scrubbing the handles updates the HTML5 video preview in real-time with zero lag.
  - Selected trim range updates `clip.start_time` and `clip.end_time` in SQLite via `POST /api/clips/{id}/trim`.
  - *Files:* `frontend/src/screens/ClipsScreen.tsx`, `dispatch/web/app.py`

---

## 4. FIFO Rolling Backlog Queue (Quota & Day-to-Day Overflow)
Zero data loss when recording sessions exceed daily free tier limits.

- [x] **4.1 Daily Request Budget Tracker**
  - Track daily requests sent to `gemini-3.5-transcribe` (budget: 25 requests/day, resetting at midnight PT / 12:30 PM IST).
  - *Files:* `dispatch/transcription/gemini_engine.py`, `dispatch/db.py`

- [x] **4.2 Backlog State & Overflow Routing**
  - When clip count exceeds 25 in a single day, remaining clips are marked as `queued_transcription` in SQLite.
  - UI displays queue depth (e.g., *"18 clips ready · 7 queued for tomorrow's reset"*).
  - *Files:* `dispatch/db.py`, `dispatch/web/app.py`, `frontend/src/screens/ClipsScreen.tsx`

- [x] **4.3 Quota Reset Auto-Drain Poller**
  - Background scheduler checks at daily reset time.
  - Drains yesterday's leftover backlog first before processing new incoming recordings.
  - *Files:* `dispatch/transcription/worker.py`, `dispatch/main.py`

---

## 5. Kinetic Word-Highlight Subtitle Renderer
CapCut-style animated captions powered by word-level timestamps.

- [x] **5.1 Word-by-Word Caption Data Mapping**
  - Store word-level JSON in `clip.caption_data` (`[{"word": "...", "start": 0.1, "end": 0.4}]`).
  - *Files:* `dispatch/db.py`, `dispatch/ai_clips/worker.py`

- [x] **5.2 Animated Pop-Up Subtitle Burner**
  - Render karaoke-style highlighted words synced to audio during final video export.
  - *Files:* `dispatch/captions/exporter.py`, `dispatch/video_engine/renderer.py`
