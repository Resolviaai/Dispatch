# Chunk 1: OpusClip Core Architecture, Systems & Feature Specifications

> **Research Scope:** Comprehensive breakdown of OpusClip's core engine, multimodal video ingestion, AI Producer, curation algorithms, framing optics, typography, audio enhancement, export pipelines, and tier specifications.
> **Source Materials:** `c:\CODE\Dispatch\Opus_pro_research\data\core_features\*.md` (30 primary feature documents, pricing matrices, and technical whitepapers).

---

## 1. System Architecture Overview

OpusClip (currently OpusClip 4.0 / Agent Opus) is a multimodal generative video repurposing platform designed to transform raw long-form footage, podcasts, webinars, live streams, and multi-camera shoots into viral-optimized short-form videos (TikTok, YouTube Shorts, Instagram Reels, LinkedIn).

```
   ┌─────────────────────────────────────────────────────────────┐
   │                     VIDEO INGESTION LAYER                   │
   │  Local Upload (up to 30GB) | Public MP4 URL | YouTube Sync  │
   └──────────────────────────────┬──────────────────────────────┘
                                  ▼
   ┌─────────────────────────────────────────────────────────────┐
   │                  AI ANALYSIS & CURATION ENGINE              │
   │  - Audio Transcription (Whisper ASR, 20+ languages)        │
   │  - Visual Object & Face Detection                           │
   │  - Multimodal Cue Extraction (Visual + Audio + Sentiment)   │
   │  - Semantic Topic Segmentation & Chaptering                 │
   │  - Hook & Virality Scoring Engine (0-99 score)              │
   └──────────────────────────────┬──────────────────────────────┘
                                  ▼
   ┌─────────────────────────────────────────────────────────────┐
   │               OPUSCLIP AI PRODUCER / EDITOR AGENT           │
   │  - Conversational Video Directing ("Make hook punchier")     │
   │  - ClipAnything Multimodal Prompt Parser                    │
   │  - Speech Cleanup (Filler removal, Bad takes elimination)   │
   │  - Non-linear Repurposing (Stitching non-adjacent sections) │
   └──────────────────────────────┬──────────────────────────────┘
                                  ▼
   ┌─────────────────────────────────────────────────────────────┐
   │             VISUAL, AUDIO & TYPOGRAPHIC RENDERING           │
   │  - AI Reframe: Face/Object Tracking (9:16, 1:1, 16:9)       │
   │  - Multi-Speaker Split Screen & Gameplay/Screen Layouts     │
   │  - Animated Captions (Karaoke highlight, 99% accuracy)      │
   │  - Contextual AI & Stock B-Roll Injection (Agent Opus/Pexels)│
   │  - AI Dubbing (29+ languages) & Voiceover Synthesis         │
   │  - Audio Enhancer (Reverb suppression, Denoising, Leveling) │
   └──────────────────────────────┬──────────────────────────────┘
                                  ▼
   ┌─────────────────────────────────────────────────────────────┐
   │                   DISTRIBUTION & EXPORT                     │
   │  - XML Non-Destructive Export (Premiere Pro, DaVinci)       │
   │  - Direct MP4 Render (Up to 4K 60fps)                       │
   │  - Social Scheduler (TikTok, YouTube Shorts, Reels, LinkedIn)│
   │  - OpusSearch Semantic Video Asset Library & Auto-Catalog   │
   └─────────────────────────────────────────────────────────────┘
```

---

## 2. Ingestion & Video Input Constraints

OpusClip supports multi-channel ingest with differentiated quotas by plan tier:
* **Supported Input Types:**
  * Direct Local Video Uploads (`.mp4`, `.mov`, `.mkv`, `.webm`, `.avi`).
  * Direct Public MP4 / Cloud storage URLs (AWS S3, Google Cloud Storage, Dropbox, Google Drive).
  * Auto-Sync from Verified YouTube Accounts: Connect channel OAuth to automatically pull newly uploaded videos.
  * Zoom, StreamYard, Riverside, and Google Meet cloud recordings.
* **Size & Length Thresholds:**
  * **Free / Starter:** Up to **10 GB** per video file; max video length 2 hours.
  * **Pro / Team:** Up to **30 GB** per video file; public MP4 URLs supported; max video length up to 5 hours.
  * **Enterprise:** Custom file size limits (>30 GB), dedicated high-throughput cloud endpoints, and direct CMS webhook integrations.

---

## 3. The Curation & Clipping Pipeline

### 3.1 AI Curation & Chaptering
Unlike basic clippers that perform sliding-window silence detection, OpusClip uses a semantic understanding engine trained on millions of viral social videos:
1. **Whole-Video Semantic Analysis:** Evaluates the entire transcript and visual timeline against current platform virality curves.
2. **Contextual Segmentation:** Splits content into discrete logical themes/chapters rather than arbitrary timestamps.
3. **Non-Adjacent Stitching:** Identifies the core premise in minute 2 and stitches it seamlessly to the punchline or conclusion in minute 14, buffing the audio/video transition so it sounds continuous.
4. **Internal Benchmark Metrics:** Produces **63% more shareable clips** and has **57% lower rate of incoherent cuts** compared to standard silence/cut-based clippers.

### 3.2 ClipAnything: Multimodal Prompting
ClipAnything allows users to bypass algorithmic selection and extract precise moments via natural language:
* **Multimodal Triangulation:** Understands visual cues (e.g., "when someone kicks a ball", "when the product is shown"), audio cues (e.g., "screaming", "laughter", "applause"), and sentiment cues (e.g., "heated debate", "nostalgic story").
* **Target Moment Extraction:** Enables prompt queries such as:
  * *"Find when the guest talks about the first million dollars."*
  * *"Clip the funny reaction when the experiment fails."*
  * *"Extract the 30-second summary explaining quantum computing."*
* **Reprompting & Iteration:** Users can reprompt existing clips to lengthen, trim, or change focus without re-uploading the source.

### 3.3 Target Clip Lengths & Copilot Settings
Users can restrict or guide the extraction window across 5 preset durations:
* `0 - 1 minute` (Optimized for YouTube Shorts, Instagram Reels, TikTok)
* `1 - 3 minutes` (Optimized for TikTok long-form, LinkedIn, X/Twitter)
* `3 - 5 minutes` (Mid-form analysis, Facebook Reels)
* `5 - 10 minutes` (In-depth tutorials, Facebook Watch)
* `10 - 15 minutes` (YouTube long-form repurposing)

---

## 4. Visual Layout & AI Reframe Optics

Repurposing horizontal 16:9 widescreen video into mobile vertical 9:16 requires intelligent visual attention tracking:

### 4.1 Tracking Modes
* **Active Speaker Tracking:** Neural networks continuously identify facial bounding boxes and mouth movement, centering the current speaker dynamically with smooth camera pans rather than jarring instant cuts.
* **Multi-Speaker Split Screen:** Detects 2 or more conversationalists and automatically composes a vertical split-screen (Speaker A top, Speaker B bottom), highlighting active audio cues.
* **Gameplay & Screenshare Layout:** For software demos, streaming, and gaming, detects high-information screen areas (e.g., slides, IDE, game UI) and places the presenter's facecam in a floating bubble or top box with full-bleed content below.
* **Moving Object Tracking:** Extends beyond faces to follow products, athletic movements, demonstrations, and physical props.
* **Supported Aspect Ratios:**
  * `9:16` (Vertical Full-Bleed mobile)
  * `1:1` (Square carousel / LinkedIn feed)
  * `16:9` (Standard landscape widescreen / YouTube)

---

## 5. Kinetic Typography & Captions Engine

Captions are the primary retention driver in short-form content. OpusClip integrates an end-to-end Whisper-derived transcription engine delivering **99% accuracy**:

* **Multi-Language Support (20+ Languages):** English, German, Spanish, French, Portuguese, Italian, Dutch, Russian, Polish, Indonesian, Ukrainian, Swedish, Turkish, Norwegian, Croatian, Romanian, Slovak, Greek, Danish, Finnish, Japanese, Korean, and Mandarin.
* **Karaoke Word-by-Word Highlighting:** Real-time synchronized highlighting where spoken syllables light up in primary brand colors.
* **Automatic Emoji Injection:** Context-aware placement of relevant animated SVG emojis (e.g., 💰 when talking about money, 🧠 when talking about psychology) synchronized to the exact word timestamp.
* **Keyword Highlighting:** Automatically emphasizes high-retention trigger words with contrasting colors (e.g., electric yellow, neon green).
* **Speaker-Differentiated Colors:** In dual-speaker setups, automatically assigns distinct color palettes to Speaker 1 and Speaker 2.
* **Profanity Censoring:** Configurable toggle to automatically bleep or asterisk curse words using standard FCC dictionaries or customizable enterprise blacklists.
* **Custom Typography:** Upload custom `.ttf` or `.otf` brand font packages (up to 2 on Pro, unlimited on Enterprise).

---

## 6. Media Enhancement, AI B-Roll & Generative Media

### 6.1 AI B-Roll Injection
To prevent viewer fatigue during static talking-head segments, OpusClip introduces context-driven B-roll:
* **Semantic Analysis:** Reads the transcript context (e.g., speaker says *"the market crashed in 2008"*) and queries video assets matching "Wall Street stock panic".
* **Dual-Engine Sourcing:**
  1. *Stock B-Roll:* Millions of curated 4K copyright-free clips from Pexels.
  2. *AI Generative Video B-Roll:* Native generation powered by **Agent Opus** (utilizing cutting-edge video models).
* **Interactive Timeline Adjustments:** Users can drag, trim, swap, or regenerate specific B-roll segments directly on the timeline or prompt-revise the generative query.

### 6.2 Audio Enhancement & Cleaning
* **Speech Enhancement:** Neural audio cleanup isolating voice frequencies, removing room reverberation, HVAC hum, and street noise.
* **Pause & Filler Word Removal:** Detects unnatural pauses (>1.2s) and speech fillers ("um", "uh", "you know", "like") and cuts them at the zero-crossing mark to maintain seamless speech cadence.
* **Bad Takes Removal:** Identifies repeated phrases, false starts, and throat-clearing, preserving only the clean final take.
* **AI Dubbing & Voice Cloning:** Translates and dubs dialogue into 29+ languages while matching the original speaker's vocal timbre, pitch, and cadence.
* **AI Voice-Overs & Sound Effects (SFX):** Automated risers, whooshes, and pop sound effects placed at visual transition points.
* **Eye Contact Correction:** AI gaze-redirection model subtle adjusting eye orientation toward the lens.

---

## 7. Export Pipelines & Post-Production Handoff

Unlike closed-loop AI tools that lock users into an MP4-only silo, OpusClip features non-destructive post-production interoperability:

* **Export to XML (Premiere Pro & DaVinci Resolve):**
  * Generates an XML sequence file linking directly back to the original camera master files.
  * Translates every AI cut, reframe pan, caption layer, audio track, and B-roll clip into native timeline tracks.
  * Allows professional color grading, custom motion graphics, and audio mixing in Adobe Premiere Pro, DaVinci Resolve Studio, or Final Cut Pro.
* **Bulk Export Engine:** High-speed parallel rendering of multiple clips simultaneously.
* **Direct MP4 Renders:** Watermark-free renders up to **1080p** and **4K 60fps** with optimized H.264/H.265 bitrate encoding.

---

## 8. Enterprise Infrastructure, OpusSearch & Social Publishing

* **OpusSearch & Auto-Cataloging:**
  * Vector embedding index of the creator's entire video archive.
  * Semantic search across spoken transcripts, visual scenes, and topics.
  * Instant compilation creation: groups related clips from episodes recorded years apart.
* **Social Media Scheduler:**
  * Direct OAuth publishing to TikTok, YouTube Shorts, Instagram Reels, and LinkedIn.
  * Built-in AI title, description, and hashtag generator optimized for social search algorithms.
  * Native 4K scheduling queue with multi-account support.
* **Team Workspaces & Security:**
  * Multi-seat permissions (Owner, Editor, Viewer).
  * Brand Templates: Shared logo bugs, intro/outro stingers, fonts, and color palettes.
  * Enterprise Compliance: SOC II Type 2 certified, Single Sign-On (SAML/SSO), Software License Management (SLM), and custom Master Service Agreements (MSA).

---

## 9. Comprehensive Plan Tier Matrix

| Feature / Quota | Free Plan | Starter Plan | Pro Plan | Enterprise Plan |
| :--- | :--- | :--- | :--- | :--- |
| **Monthly Video Ingest** | 60 mins (one-time) | 150 mins / mo | 300 - 3,600 mins / mo | Custom (>5,000 mins) |
| **Max File Upload** | 10 GB | 10 GB | 30 GB + Public MP4 URLs | Custom (>50 GB) + CMS |
| **Watermark** | Watermarked | Watermark-free | Watermark-free | Watermark-free |
| **Export Resolution** | 1080p (3-day expiry) | 1080p (30-day expiry) | 1080p / 4K (No expiry) | 4K Uncompressed |
| **Aspect Ratios** | 9:16 only | 9:16 only | 9:16, 1:1, 16:9 | 9:16, 1:1, 16:9, Custom |
| **AI Curation & Virality** | Basic | Standard | Advanced Virality Score | Custom Virality Weighting |
| **ClipAnything Prompts** | Limited | Standard | Full Multimodal Copilot | Full Multimodal Copilot |
| **Custom Length Presets** | 0-1m, 1-3m | 0-1m, 1-3m, 3-5m | All (0-1m, 1-3m, 3-5m, 5-10m, 10-15m) | Fully customizable |
| **Stock B-Roll** | None | 3 clips / mo | Unlimited Pexels 4K | Unlimited Enterprise Stock |
| **AI Video B-Roll (Agent Opus)** | None | None | 50 generations / day | Unlimited high-speed |
| **Export to XML (Premiere/DaVinci)**| None | None | Included | Included |
| **Audio Enhancement & Denoise** | None | None | 10 runs / day | Unlimited |
| **Speech Cleanup (Filler/Bad Take)**| None | Pause removal only | Full Bad Take & Filler Removal | Full Suite |
| **Custom Brand Fonts** | None | None | Up to 2 custom fonts | Unlimited custom fonts |
| **Brand Templates** | 1 template | 1 template | Up to 4 templates | Custom Brand Systems |
| **Team Workspace & Seats** | 1 seat | 1 seat | 2 seats included (up to 4) | Unlimited custom seats |
| **Cloud Storage** | 3 days | 29 days | 100 GB fixed persistent | Custom dedicated S3 bucket |
| **Social Media Scheduler** | None | Single profile | Multi-profile & 4K queue | Multi-brand enterprise queue |
| **API & MCP Access** | None | None | Included (API + MCP) | Full SLA API + Webhooks |
