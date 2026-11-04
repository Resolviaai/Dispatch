# OpusClip / Agent Opus: Comprehensive Technical Research Dossier

> **Executive Scope:** Deep forensic research report compiling the complete feature set, system architecture, Model Context Protocol (MCP) servers, REST API infrastructure, multi-model generative orchestration, virality algorithms, benchmark data from 13.5M clips, competitive teardowns, and utility tools ecosystem.
> **Source Corpus:** 1,865 primary documentation pages, technical specs, research whitepapers, competitor teardowns, and API schemas fetched from `opus.pro`.
> **Research Location:** Strictly confined to `c:\CODE\Dispatch\Opus_pro_research`.

---

## 1. Master Research Architecture & Chunk Index

To prevent information loss across 1,865 extracted documents, this research is systematically modularized into five comprehensive chunk reports:

```
c:\CODE\Dispatch\Opus_pro_research\reports\
├── 00_master_opus_pro_dossier.md                 ◄ You are here (Executive Synthesis)
├── 01_core_architecture_and_features.md          ◄ Core Engine, Ingestion, Reframe, Captions, Pricing
├── 02_mcp_server_and_api_architecture.md         ◄ Dual MCP Gateway, 29 Tools, REST API, Agent Recipes
├── 03_agent_ecosystem_and_workflows.md           ◄ Agent Opus, 12 Video Models, 604 Workflows, Prompts
├── 04_research_virality_and_benchmarks.md         ◄ 13.5M Clip Study, Virality Algorithm, Retention Curves
└── 05_competitive_analysis_and_tools_matrix.md   ◄ Teardowns vs CapCut/Descript/Riverside, 266 Web Tools
```

### Direct Navigation Links
1. [01_core_architecture_and_features.md](file:///c:/CODE/Dispatch/Opus_pro_research/reports/01_core_architecture_and_features.md): Deep dive into OpusClip 4.0, AI Producer, ClipAnything multimodal prompting, AI Reframe, kinetic captions, speech cleanup, XML non-destructive export, and the full pricing matrix.
2. [02_mcp_server_and_api_architecture.md](file:///c:/CODE/Dispatch/Opus_pro_research/reports/02_mcp_server_and_api_architecture.md): The Dual MCP Server specs (`mcp.opus.pro/mcp` and `api.opus.pro/api/agent-mcp`), complete catalog of all 29 tools, Claude Code / Cursor / ChatGPT setups, and REST API schemas.
3. [03_agent_ecosystem_and_workflows.md](file:///c:/CODE/Dispatch/Opus_pro_research/reports/03_agent_ecosystem_and_workflows.md): Multi-model routing across 12 AI video models (Google Veo 3, Gemini Omni, Kling, Hailuo, Luma), brand anchor persistence, and the 604 workflow directory.
4. [04_research_virality_and_benchmarks.md](file:///c:/CODE/Dispatch/Opus_pro_research/reports/04_research_virality_and_benchmarks.md): The Virality Score (0–99) formula, empirical findings from 13.5 million clips, 3-second hook mechanics, retention curve archetypes, and vertical caption strategies.
5. [05_competitive_analysis_and_tools_matrix.md](file:///c:/CODE/Dispatch/Opus_pro_research/reports/05_competitive_analysis_and_tools_matrix.md): Competitive teardowns against CapCut, Descript, Riverside, Munch, Klap, Submagic, OpusClip's 5 proprietary moats, and directory of 266 web utilities.

---

## 2. Executive System Architecture

OpusClip operates as an end-to-end multimodal video platform combining automated repurposing with agentic generative creation:

```
                      ┌─────────────────────────────────┐
                      │    INGESTION & INPUT SURFACE    │
                      │  Local Upload (up to 30GB)      │
                      │  Public MP4 URL / S3            │
                      │  YouTube Sync / Zoom / Meet     │
                      └────────────────┬────────────────┘
                                       │
                                       ▼
                      ┌─────────────────────────────────┐
                      │    MULTIMODAL INTELLIGENCE      │
                      │  - Whisper ASR (20+ Languages)  │
                      │  - Face/Object Motion Tracking  │
                      │  - Audio Sentiment & Energy     │
                      │  - Semantic Topic Chaptering    │
                      └────────────────┬────────────────┘
                                       │
            ┌──────────────────────────┴──────────────────────────┐
            ▼                                                     ▼
┌───────────────────────────────┐             ┌───────────────────────────────┐
│     AI REPURPOSING ENGINE     │             │     AGENT OPUS GENERATOR      │
│  - Virality Score (0-99)      │             │  - Multi-Model Orchestration  │
│  - ClipAnything Natural Prompt│             │  - Veo 3 / Kling / Gemini Omni│
│  - Dynamic Reframe (9:16/1:1) │             │  - Script-to-Video / Songs    │
│  - Animated Kinetic Captions  │             │  - Motion Graphics (up to 10s)│
│  - AI B-Roll (Stock + AI)     │             │  - Latent Brand Anchors       │
└───────────────┬───────────────┘             └───────────────┬───────────────┘
                │                                             │
                └──────────────────────┬──────────────────────┘
                                       │
                                       ▼
                      ┌─────────────────────────────────┐
                      │    PROGRAMMATIC GATEWAYS        │
                      │  - Dual MCP Servers (29 Tools)  │
                      │  - REST API & Webhooks (SOC-2)  │
                      │  - Native Mobile App (iOS)      │
                      └────────────────┬────────────────┘
                                       │
                                       ▼
                      ┌─────────────────────────────────┐
                      │     EXPORT & DISTRIBUTION       │
                      │  - Non-Destructive XML Sequence │
                      │    (Premiere Pro & DaVinci)     │
                      │  - 4K 60fps MP4 Cloud Rendering │
                      │  - Multi-Platform Auto-Scheduler│
                      │  - OpusSearch Semantic Vault    │
                      └─────────────────────────────────┘
```

---

## 3. Key Findings Across Research Chunks

### 3.1 Core Capabilities (Chunk 1)
* **ClipAnything:** First-of-its-kind multimodal prompt parser that triangulates visual bounding boxes, audio amplitude, and sentiment spikes, allowing natural-language extraction (e.g., *"Find the funniest reaction"*).
* **AI Producer:** Conversational AI directing agent that performs non-linear video editing—extracting non-adjacent gold nuggets from minute 2 and minute 14 and stitching them with smoothed zero-crossing audio transitions.
* **Non-Destructive Post-Production Bridge:** Direct XML export to **Adobe Premiere Pro** and **DaVinci Resolve**, translating reframes, cuts, B-roll, and caption layers into native timeline tracks.
* **Tier Ceilings:** Free tier provides 60 minutes with watermarks; Pro tier unlocks 30GB uploads, 4K rendering, 100GB persistent storage, 50 AI video B-roll generations/day, and XML exports.

### 3.2 Programmatic & Agent Architecture (Chunk 2)
* **Two Dedicated MCP Servers:**
  1. `https://mcp.opus.pro/mcp`: Video repurposing (clipping, reframing, captioning, scheduling).
  2. `https://api.opus.pro/api/agent-mcp`: Generative video creation (prompt-to-video, voice cloning, brand styles).
* **Streamable HTTP + OAuth:** Direct integration into Claude Code (`claude mcp add --transport http ...`), Cursor (`.cursor/mcp.json`), and ChatGPT without manually exposing or managing raw API keys.
* **The 29 MCP Tools:** Complete lifecycle coverage: `submit_video`, `find_viral_moments`, `prepare`, `render`, `poll_status`, `add_broll`, `reframe`, `caption`, `schedule_post`.

### 3.3 Multi-Model Generative Orchestrator (Chunk 3)
* **Overcoming Single-Shot AI Video Limitations:** Individual AI video models max out at 5–10 seconds with facial drift. Agent Opus splits long scripts into scenes and dynamically routes each beat to the optimal model:
  * **Google Veo 3:** Native 4K cinematic landscapes and ambient audio.
  * **Google Gemini Omni:** Conversational multi-turn video editing and unified multimodality.
  * **Kling AI:** Complex mechanical and athletic physics up to 120s.
  * **Hailuo AI:** Nuanced facial micro-expressions and portrait character dialogues.
  * **Luma AI:** Extreme 3D spatial transformations and drone fly-throughs.
* **Latent Brand Anchors:** Enforces visual consistency by injecting up to 8 persistent product/character anchors into the diffusion cross-attention layers.

### 3.4 Virality & Retention Analytics (Chunk 4)
* **13.5 Million Clip Empirical Study:**
  * **80.2%** of top viral short-form clips feature on-screen captions.
  * **78.6%** utilize dynamic animated karaoke highlighting; only **1.6%** use static subtitles.
  * **75% reduction in viewer drop-off** achieved by injecting context-aware B-roll every 3.5 to 5.0 seconds.
* **The Virality Score (0–99):** Evaluates Hook strength (35%), Flow cadence (25%, optimal 145–175 WPM), Value density (25%), and Social trend alignment (15%).
* **The 3-Second Hold Rule:** Swipe-away exceeding 35% in the opening 3 seconds leads to immediate platform throttling.

### 3.5 Competitive Position & Utility Ecosystem (Chunk 5)
* **Competitive Moats:** While CapCut dominates manual mobile timeline editing and Descript leads document-style transcript editing, OpusClip owns **autonomous long-to-short semantic curation**, **multimodal prompting (ClipAnything)**, and **developer agent protocols (MCP)**.
* **Ecosystem Scale:** 266 specialized web utilities driving high-intent inbound search for specific clipping, captioning, dubbing, and aspect-ratio tasks.

---

## 4. Local Workspace Verification & File Map

All raw scraped data, metadata indices, analytical scripts, and completed chunk reports are stored exclusively within `c:\CODE\Dispatch\Opus_pro_research`:

* **Metadata Index:** [metadata.jsonl](file:///c:/CODE/Dispatch/Opus_pro_research/metadata.jsonl) (Structured JSON Lines index of all 1,865 fetched URLs)
* **Raw Content Directory:** `c:\CODE\Dispatch\Opus_pro_research\data\`
  * `agent/` (695 files)
  * `alternatives/` (46 files)
  * `blog/` (525 files)
  * `business/` (11 files)
  * `core_features/` (30 files)
  * `demo/` (11 files)
  * `mcp/` (27 files)
  * `other/` (22 files)
  * `research/` (232 files)
  * `tools/` (266 files)
* **Completed Research Reports:** `c:\CODE\Dispatch\Opus_pro_research\reports\`
  * [00_master_opus_pro_dossier.md](file:///c:/CODE/Dispatch/Opus_pro_research/reports/00_master_opus_pro_dossier.md)
  * [01_core_architecture_and_features.md](file:///c:/CODE/Dispatch/Opus_pro_research/reports/01_core_architecture_and_features.md)
  * [02_mcp_server_and_api_architecture.md](file:///c:/CODE/Dispatch/Opus_pro_research/reports/02_mcp_server_and_api_architecture.md)
  * [03_agent_ecosystem_and_workflows.md](file:///c:/CODE/Dispatch/Opus_pro_research/reports/03_agent_ecosystem_and_workflows.md)
  * [04_research_virality_and_benchmarks.md](file:///c:/CODE/Dispatch/Opus_pro_research/reports/04_research_virality_and_benchmarks.md)
  * [05_competitive_analysis_and_tools_matrix.md](file:///c:/CODE/Dispatch/Opus_pro_research/reports/05_competitive_analysis_and_tools_matrix.md)
