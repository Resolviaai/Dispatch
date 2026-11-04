# Chunk 2: OpusClip MCP Server & API Architecture

> **Research Scope:** Comprehensive technical specification of the Model Context Protocol (MCP) servers, REST API infrastructure, authentication patterns, tool interfaces, client integrations (Claude, Cursor, ChatGPT), and autonomous agent automation pipelines.
> **Source Materials:** `c:\CODE\Dispatch\Opus_pro_research\data\mcp\*.md` (27 MCP documents and recipes), `c:\CODE\Dispatch\Opus_pro_research\data\core_features\api.md`, and `c:\CODE\Dispatch\Opus_pro_research\data\agent\mcp.md`.

---

## 1. Architectural Overview

OpusClip provides two complementary programmatic interfaces designed for machine-to-machine and agent-to-agent automation:
1. **The Model Context Protocol (MCP) Gateway:** Enables native, tool-calling agent interaction inside LLMs (Claude Code, Cursor, ChatGPT, Cline, Windsurf) using standard streamable HTTP transport with OAuth 2.0.
2. **The OpusClip REST API & Webhook Service:** Provides high-throughput server-to-server video processing with dedicated endpoints for project submission, status polling, and webhook callbacks.

```
   ┌────────────────────────────────────────────────────────┐
   │            CLIENTS & AGENT PLATFORMS                   │
   │  Claude Code | Cursor | ChatGPT | Custom Python Agent   │
   └───────────┬────────────────────────────────┬───────────┘
               │ (Streamable HTTP + OAuth)      │ (REST + Bearer Token)
               ▼                                ▼
   ┌───────────────────────┐        ┌───────────────────────┐
   │   OPUSCLIP MCP GATEWAY│        │   OPUSCLIP REST API   │
   │  https://mcp.opus.pro │        │  https://api.opus.pro │
   └───────────┬───────────┘        └───────────┬───────────┘
               │                                │
               └───────────────┬────────────────┘
                               ▼
   ┌────────────────────────────────────────────────────────┐
   │            OPUS PROCESSING CLOUD ENGINES               │
   │  - Whisper ASR Transcription & Multi-Language ASR      │
   │  - AI Curation & Multimodal Virality Scoring           │
   │  - AI Reframe & Dynamic Speaker Tracking               │
   │  - Agent Opus Generative Diffusion Video Models        │
   │  - Social Publishing Queue (TikTok, Reels, Shorts, X)   │
   └────────────────────────────────────────────────────────┘
```

---

## 2. Dual MCP Server Ecosystem

OpusClip operates two specialized MCP servers catering to distinct video workflows:

### 2.1 Video Repurposing MCP Server
* **Endpoint:** `https://mcp.opus.pro/mcp`
* **Transport:** `http` / `sse` (Streamable HTTP)
* **Primary Scope:** Repurposing long-form video (YouTube, podcasts, webinars) into viral vertical shorts.
* **Core Capabilities:** Highlight detection, virality scoring, kinetic caption burning, dynamic 9:16 reframing, and social scheduling.

### 2.2 AgentOpus Generative Video MCP Server
* **Endpoint:** `https://api.opus.pro/api/agent-mcp`
* **Transport:** `http` / `sse` (Streamable HTTP)
* **Primary Scope:** Generative video creation from text prompts, scripts, songs, or image references.
* **Core Capabilities:** Autonomous video planning (`prepare`), asynchronous generation (`render`), 25+ visual style presets, custom voice cloning (from 10–30s samples), brand anchors (up to 8 assets per video), and music video generation with lip-sync.

---

## 3. Client Installation & Transport Configuration

### 3.1 Claude Code CLI
```bash
# Add Video Repurposing MCP
claude mcp add --transport http opusclip https://mcp.opus.pro/mcp

# Add AgentOpus Generative Video MCP
claude mcp add --transport http agentopus https://api.opus.pro/api/agent-mcp
```
*After executing the command, running `/mcp` in Claude Code launches an OAuth browser window to authorize your OpusClip subscription without manually handling API keys.*

### 3.2 Cursor IDE
Configure in `.cursor/mcp.json` or through Cursor Settings → Features → MCP:
```json
{
  "mcpServers": {
    "opusclip": {
      "url": "https://mcp.opus.pro/mcp",
      "type": "http"
    },
    "agentopus": {
      "url": "https://api.opus.pro/api/agent-mcp",
      "type": "http"
    }
  }
}
```

### 3.3 ChatGPT (Desktop & Web Connectors)
1. Open ChatGPT → Settings / Plugins / Connectors.
2. Click **+ Add Custom Connector**.
3. Set Server URL to `https://mcp.opus.pro/mcp` or `https://api.opus.pro/api/agent-mcp`.
4. Complete the OAuth verification modal.

---

## 4. The 29 MCP Tools Catalog

The combined OpusClip MCP suite exposes 29 granular tools organized into functional tiers:

### Tier 1: Ingestion, Analysis & Discovery
| Tool Identifier | Description | Input Parameters | Return Object |
| :--- | :--- | :--- | :--- |
| `submit_video` | Submits a long video URL or local file for ingestion | `video_url: string`, `title?: string` | `project_id: string`, `duration: number` |
| `find_viral_moments` | Analyzes footage and extracts top viral candidates | `project_id: string`, `min_score?: number` | `moments: Array<{start, end, score, hook_text}>` |
| `get_transcript` | Retrieves word-level timestamped transcript | `project_id: string`, `language?: string` | `transcript: Array<{word, start_ms, end_ms}>` |
| `search_library` | Vector search across historical user video catalog | `query: string`, `date_range?: string` | `results: Array<{clip_id, timestamp, transcript_snippet}>` |

### Tier 2: Framing, Captions & Repurposing
| Tool Identifier | Description | Input Parameters | Return Object |
| :--- | :--- | :--- | :--- |
| `clip` | Cuts a defined segment into an independent short | `project_id: string`, `start_sec: number`, `end_sec: number` | `clip_id: string` |
| `reframe` | Converts aspect ratio with subject tracking | `clip_id: string`, `aspect_ratio: "9:16"\|"1:1"\|"16:9"` | `status: string`, `preview_url: string` |
| `caption` | Applies dynamic animated captions & highlights | `clip_id: string`, `template_id: string`, `censor?: bool` | `rendered_clip_id: string` |
| `add_broll` | Injects contextual stock or AI B-roll | `clip_id: string`, `source: "stock"\|"generative"`, `query?: string` | `clip_id: string`, `broll_count: number` |
| `enhance_speech` | Removes background noise & room reverberation | `clip_id: string`, `strength?: number` | `audio_status: string` |
| `remove_fillers` | Strips filler words ("um", "uh") and dead silences | `clip_id: string`, `threshold_sec?: number` | `cut_count: number`, `saved_seconds: number` |
| `dub_video` | Translates and dubs dialogue with voice cloning | `clip_id: string`, `target_language: string` | `task_id: string` |

### Tier 3: AgentOpus Generative Pipeline
| Tool Identifier | Description | Input Parameters | Return Object |
| :--- | :--- | :--- | :--- |
| `whoami` | Checks connected account status and credits | *None* | `email: string`, `tier: string`, `credits_remaining: number` |
| `prepare` | Cost-free planning step; structures prompt/script into plan | `prompt?: string`, `script?: string`, `style_id?: string` | `plan_id: string`, `cost_credits: number`, `breakdown: object` |
| `render` | Executes generation of prepared video plan | `plan_id: string` | `task_handle: string`, `estimated_time: number` |
| `poll_status` | Checks progress of rendering video job | `task_handle: string` | `status: "queued"\|"rendering"\|"completed"`, `progress: number`, `video_url?: string` |
| `clone_voice` | Creates reusable voice clone from audio sample | `audio_url: string`, `voice_name: string` | `voice_id: string` |
| `create_style` | Creates visual style preset from image or text prompt | `style_name: string`, `reference_image_url?: string`, `prompt: string` | `style_id: string` |
| `register_brand_anchor`| Registers logo, product or character image anchor | `asset_url: string`, `asset_type: "logo"\|"product"\|"character"` | `anchor_id: string` |
| `generate_music_video`| Creates lip-synced music video from uploaded song | `song_url: string`, `style_id: string`, `lyrics?: string` | `task_handle: string` |
| `create_motion_graphic`| Generates short animated motion graphic (up to 10s) | `prompt: string`, `style_preset: string`, `duration_sec: number` | `video_url: string` |

### Tier 4: Publishing & Social Distribution
| Tool Identifier | Description | Input Parameters | Return Object |
| :--- | :--- | :--- | :--- |
| `schedule_post` | Schedules finished clip to social platforms | `clip_id: string`, `platforms: string[]`, `publish_time_iso: string`, `caption: string` | `schedule_id: string` |
| `publish_now` | Triggers immediate post to social platforms | `clip_id: string`, `platforms: string[]`, `caption: string` | `post_urls: object` |
| `list_scheduled` | Queries active scheduling queue | `limit?: number` | `queue: Array<{schedule_id, publish_time, platform}>` |
| `cancel_scheduled` | Cancels a queued publication | `schedule_id: string` | `success: boolean` |
| `generate_copy` | Produces platform-tailored title, description & hashtags | `clip_id: string`, `platform: "tiktok"\|"youtube"\|"reels"\|"linkedin"` | `title: string`, `description: string`, `hashtags: string[]` |

---

## 5. OpusClip REST API Specification

### 5.1 Base Endpoint & Authentication
* **Base URL:** `https://api.opus.pro/api`
* **Authentication Header:** `Authorization: Bearer <OPUS_API_KEY>`
* **Content Type:** `application/json`

### 5.2 Project Submission Endpoint: `/api/clip-projects`
Submits a long video for automated AI chaptering, virality scoring, and rendering.

#### Request Schema
```json
{
  "videoUrl": "https://www.youtube.com/watch?v=LXvv6CbGg8A",
  "conclusionActions": [
    {
      "type": "WEBHOOK",
      "url": "https://api.yourdomain.com/webhooks/opusclip",
      "notifyFailure": true
    },
    {
      "type": "EMAIL",
      "email": "producer@company.com",
      "notifyFailure": true
    }
  ],
  "curationPref": {
    "range": {
      "startSec": 0,
      "endSec": 3600
    },
    "clipDurations": [[0, 60], [60, 90]],
    "topicKeywords": ["AI", "Architecture", "Engineering"],
    "genre": "Auto",
    "skipCurate": false
  },
  "importPref": {
    "sourceLang": "auto"
  },
  "brandTemplateId": "preset-fancy-Karaoke"
}
```

#### Field Definitions
* `videoUrl` *(string, required)*: Publicly accessible URL (`https://...`) or YouTube link.
* `conclusionActions` *(array)*: Notification handlers executed upon task completion or error (`WEBHOOK`, `EMAIL`).
* `curationPref.range` *(object)*: Window boundaries in seconds (`startSec`, `endSec`) to restrict clipping.
* `curationPref.clipDurations` *(array of tuples)*: Target duration brackets in seconds (e.g. `[[0, 60]]`).
* `curationPref.topicKeywords` *(array of strings)*: Keyword filters prioritizing segments covering specific subjects.
* `curationPref.skipCurate` *(boolean)*: If `true`, disables AI highlight detection and clips the exact range provided.
* `brandTemplateId` *(string)*: Preset ID or custom brand template ID configured in the workspace.

---

## 6. Real-World Autonomous Agent Recipes

### Recipe 1: Perplexity Trend-Reactive Auto-Clipper
**Concept:** An autonomous agent queries Perplexity API daily for breaking topics in the user's domain, queries OpusClip library for historical commentary on that topic, and auto-posts it before news cycles peak.
```python
import requests, os

# 1. Query Perplexity for breaking topic
perplexity_res = requests.post(
    "https://api.perplexity.ai/chat/completions",
    headers={"Authorization": f"Bearer {os.getenv('PERPLEXITY_KEY')}"},
    json={"model": "sonar-medium-online", "messages": [{"role": "user", "content": "What is the biggest tech story today?"}]}
).json()
trending_topic = perplexity_res['choices'][0]['message']['content']

# 2. Agent calls OpusSearch to find matching archive clips
search_res = requests.post(
    "https://api.opus.pro/api/search",
    headers={"Authorization": f"Bearer {os.getenv('OPUS_KEY')}"},
    json={"query": trending_topic, "minViralityScore": 80}
).json()

# 3. Schedule top matching clip to TikTok & X
top_clip = search_res['clips'][0]
requests.post(
    "https://api.opus.pro/api/schedule",
    headers={"Authorization": f"Bearer {os.getenv('OPUS_KEY')}"},
    json={
        "clipId": top_clip['id'],
        "platforms": ["tiktok", "x", "linkedin"],
        "caption": f"Our thoughts on {trending_topic} 👇"
    }
)
```

### Recipe 2: Longform-to-Beehiiv Newsletter Pipeline
**Concept:** Pulls the transcript and generated clips from the weekly episode, writes a 5-point newsletter summary, embeds the top 3 video clips, and schedules a draft in Beehiiv.

### Recipe 3: Morning Slack Approval Bot
**Concept:** Scans OpusClip for highest-scoring unposted clips every morning at 8:00 AM, posts MP4 preview and title options into `#content-approvals`. If a team member reacts with `✅`, the bot immediately publishes the clip.

---

## 7. Enterprise Security & Infrastructure Guarantees

* **Authentication Architecture:** OAuth 2.0 PKCE tokens for desktop/CLI agents; HMAC-signed webhooks for backend servers.
* **Security Certifications:** **SOC 2 Type II** compliant infrastructure.
* **Data Privacy:** Customer video uploads and generated clips are strictly isolated per tenant; raw inputs are never used to train public foundational AI models without enterprise consent.
* **Throughput:** API infrastructure processes **1,000+ videos daily** per enterprise account with parallel GPU rendering clusters and sub-90-second turnaround times for 10-clip batches.
