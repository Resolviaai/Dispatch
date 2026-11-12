Pixabay can become Dispatch's free stock-video source for automatic B-roll.

The idea is simple: Dispatch understands what the speaker is talking about, searches Pixabay for matching footage, chooses the most relevant video, and inserts it into the editing timeline automatically.

You don't need to build your own stock-video library from scratch.

## 1. How Auto B-roll should work

## Dispatch Auto B-roll pipeline

Original video + timestamped transcript

From Dispatch's existing transcription pipeline

1\. AI identifies visual opportunities

Example: "We lost ₹10 lakh because we didn't automate our inventory."

Concepts: financial loss, inventory, automation

2\. Generate stock-search queries

- warehouse inventory
- warehouse automation
- business analytics

3\. Search Pixabay video API

[Lessons from a Warehouse Leader: Top Mistakes to Avoid in Inventory Control](https://images.openai.com/static-rsc-4/vC0MskPfso6PS09jFd6cEwmlNIRYnlBs75KYB5q4s3cbQjH14G4j3vHJwxLlFxxIYantrtZrT4RtunOx2qlEl03kYUZbF8N1mrrWfgeh8Gzs4pBrSHXJT_G_W43M-9fejf_Y7UDLZyNYEGcDMlE9sN9CFbA51Hfgcc9BWany7Wo?purpose=inline)

[linkedin.com](https://www.linkedin.com/pulse/lessons-from-warehouse-leader-top-mistakes-avoid-control-akinduro-kmdif?utm_source=chatgpt.com)

Retrieve video thumbnails, metadata and available MP4 versions.

4\. AI selects the best footage

Compare visual relevance, video duration, resolution and aspect ratio.

5\. Insert into Dispatch timeline

Automatic placement

Place B-roll over the relevant spoken phrase while retaining the original speaker's audio.

Final video with contextual B-roll

Editable, replaceable and renderable

## 2. How to search Pixabay

Pixabay exposes a video endpoint:

`https://pixabay.com/api/videos/`

You need an API key from your Pixabay account.

Example request:

```
GET https://pixabay.com/api/videos/
    ?key=YOUR_API_KEY
    &q=warehouse+inventory
    &video_type=film
    &safesearch=true
    &per_page=12
    &order=popular
```

Important parameters:

| Parameter    | Purpose                            |
| ------------ | ---------------------------------- |
| `q`          | Search phrase                      |
| `video_type` | Film or animation                  |
| `safesearch` | Filter unsuitable results          |
| `per_page`   | Number of results                  |
| `order`      | Popular or latest                  |
| `min_width`  | Minimum video width                |
| `min_height` | Minimum video height               |
| `category`   | Business, nature, technology, etc. |

The response gives you:

- Video ID.
- Pixabay source page.
- Tags.
- Duration.
- Different MP4 URLs.
- Resolution and file size.
- Thumbnail.
- Contributor information.

Official documentation: [Pixabay video API documentation](https://pixabay.com/api/docs/?utm_source=chatgpt.com).&#x20;

[image](https://www.google.com/s2/favicons?domain=https://pixabay.com\&sz=32)

pixabay.com+1



## 3. Choosing the right video quality

Pixabay returns multiple video versions.

| Version | Typical resolution | Recommended use                       |
| ------- | ------------------ | ------------------------------------- |
| Large   | 3840 × 2160        | High-resolution export when available |
| Medium  | 1920 × 1080        | Default B-roll source                 |
| Small   | 1280 × 720         | Lower resource usage                  |
| Tiny    | 960 × 540          | Lightweight preview                   |

These are typical dimensions; the actual metadata should always be checked. Medium is available across the video catalog.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://pixabay.com\&sz=32)

pixabay.com



For Dispatch, I'd default to Medium, then use FFmpeg to crop or scale it to the output aspect ratio.

For vertical Shorts, use portrait footage when available; otherwise use a suitable horizontal shot and intelligently crop it.

## 4. How to integrate this into existing Dispatch

I checked the current repository, and there is already some useful infrastructure.

Already present:

- `frontend/src/screens/ClipsScreen.tsx` has video overlay preview logic.
- `CaptionGroup` supports `overlay_url` and `overlay_type`.
- `project_document` has a `tracks` structure.
- `dispatch/segments.py` has source-to-concatenated timeline mapping helpers.
- `dispatch/video_engine/renderer.py` already uses FFmpeg for rendering.

The missing part: the Python final renderer currently uses project tracks for fingerprinting but does not composite arbitrary video overlay tracks into the final output. A preview overlay is not enough; the actual export must render it too.

I'd build this as a dedicated B-roll subsystem rather than mixing stock footage with caption groups.

### Proposed architecture

| Component                    | File                                     |
| ---------------------------- | ---------------------------------------- |
| Pixabay API adapter          | `dispatch/broll/providers/pixabay.py`    |
| B-roll opportunity detection | `dispatch/broll/opportunity_detector.py` |
| Semantic query generation    | `dispatch/broll/query_generator.py`      |
| Asset relevance ranking      | `dispatch/broll/asset_ranker.py`         |
| Download and cache           | `dispatch/broll/asset_manager.py`        |
| B-roll placement             | `dispatch/broll/placement_engine.py`     |
| B-roll rendering             | `dispatch/broll/renderer.py`             |

The frontend could have an Auto B-roll action inside the Clips editor, with individual footage choices and editable placements.

### How I would store a B-roll placement

Use a dedicated B-roll layer in `project_document`.

For example:

```
{
  "kind": "broll",
  "id": "broll_001",
  "provider": "pixabay",
  "provider_asset_id": 12345,
  "source_page_url": "https://pixabay.com/videos/...",
  "creator": "Contributor",
  "asset_uri": "media://broll/12345.mp4",
  "source_start": 18.5,
  "source_end": 22.0,
  "asset_in": 1.0,
  "asset_out": 4.5,
  "fit_mode": "cover",
  "audio_policy": "mute"
}
```

Here, `source_start` and `source_end` refer to the original recording timeline, not the concatenated edited timeline.

This matters enormously because Dispatch supports trimming and deleting sections.

Use the existing `intersect_source_interval()` mapping helper to split and remap a B-roll placement when the underlying A-roll timeline is edited. Don't let B-roll drift away from the sentence it illustrates.

## 5. The most important part: intelligent B-roll selection

Do not search Pixabay using every transcript sentence.

That produces generic, repetitive and irrelevant footage.

Instead, let the LLM identify visual opportunities.

Example:

Spoken sentence

"We spent six months building the product, but nobody wanted to buy it."

Visual opportunities

[Analytics Vidhya | The ultimate place for Generative AI, Data Science and Data Engineering](https://images.openai.com/static-rsc-4/LQQKvpdfOfwCNymfFmSRqxDSvJU2NVsFEWNqgcKKnRCIWZMlx-sBi58chI7VdqwNpah9K4iRMLu_gH5bQJ7JivlViBfdZGdfozJwaeq6DHjDunhkn7MQlOryxYh4jgwwvBilTLTHuFueKU0Ry1be0ku4LpqlGQ9fd1BZv8vLQNg?purpose=inline)

Building a product

Search: `software developer coding`

[Konganak az ürességtől az irodák egy európai nagyvárosban, meglepő ok van a háttérben - Portfolio.hu](https://images.openai.com/static-rsc-4/HSdJX1m0sh4WTNFCo_WkuYBn6wmoD3o7MwrNMuPO9rEyCWe8rqQlrzwJmAFdvbzZqyPUZ4PJi5fb-WvDulPNa9y94ZOj--M_IUpAKUMS3KN6reQlwCf67pjT4eg3aPrhCXOwnMi_O_bqNxYmJ7dE5fKTucpxFdoL_IhR5klCbvM?purpose=inline)

Failed business / no customers

Search: `empty startup office`

[Site vitrine mobile-first en 2026 : pourquoi Google juge votre site sur sa version smartphone et ce que cela change pour vos demandes de devis - 2025](https://images.openai.com/static-rsc-4/elYu-8XvDgJ0L8M9fLovcqyzEki-4XEdplJ6uknaBOItnpAu3OYMCyrSZuvRAtIIlouHpNQYq3k6h1Ho7WMRAzDLY_pVDqkK9wo27h2lUKEQg93k2zQ-vCx01VvnFdrId-xL8uwJ_gjDavOH0K-ZDrj_Dsy9CKGaKVnxPSCniKY?purpose=inline)

Product-market validation

Search: `business analytics dashboard`

The detector should output something like:

```
{
  "opportunities": [
    {
      "anchor_text": "spent six months building the product",
      "source_start": 18.5,
      "source_end": 21.5,
      "concept": "software development",
      "visual_type": "literal",
      "search_queries": [
        "software developer coding"
      ],
      "priority": "high"
    },
    {
      "anchor_text": "nobody wanted to buy it",
      "source_start": 21.5,
      "source_end": 24.5,
      "concept": "empty startup office",
      "visual_type": "conceptual",
      "search_queries": [
        "empty startup office"
      ],
      "priority": "medium"
    }
  ]
}
```

The timestamp values are illustrative.

### What should receive B-roll?

| Content                | B-roll decision                     |
| ---------------------- | ----------------------------------- |
| Concrete objects       | Search for the actual object        |
| Locations              | Search for location footage         |
| Processes              | Show the process                    |
| Business operations    | Show relevant workflows             |
| Numbers and statistics | Prefer an animated chart            |
| Software explanations  | Prefer screen recordings            |
| Emotional stories      | Use relevant contextual footage     |
| Abstract philosophy    | Use restrained metaphorical visuals |
| Jokes and punchlines   | Usually preserve the speaker's face |
| Generic introductions  | No B-roll needed                    |

Important: Sometimes the best visual is the original speaker. Do not cover every interesting sentence with stock footage.

### Candidate ranking

For each opportunity, rank the search results using:

1. Semantic relevance to what was spoken.
2. Actual visual relevance from the thumbnail.
3. Correct duration.
4. Suitable resolution and aspect ratio.
5. Footage diversity.
6. Safe and appropriate visual content.

Use Pixabay tags for initial filtering, then a visual understanding model to rerank the best few candidates. Never assume the tags fully describe what appears in the footage.

## 6. API restrictions and licensing — important

There are some rules we need to build into Dispatch.

| Requirement        | Pixabay's current guidance                                              |
| ------------------ | ----------------------------------------------------------------------- |
| Default rate limit | 100 requests per 60 seconds                                             |
| Response caching   | Mandatory 24-hour cache                                                 |
| Search results     | Show users that results come from Pixabay                               |
| Video embedding    | Allowed                                                                 |
| Downloading        | Recommended to store selected videos on your server                     |
| Bulk automation    | Lots of automated queries and systematic mass downloads are not allowed |

[image](https://www.google.com/s2/favicons?domain=https://pixabay.com\&sz=32)

pixabay.com

Pixabay's Content License permits free commercial use and modification, subject to restrictions. You cannot redistribute stock footage as a standalone asset. Depicted people, trademarks and other third-party rights may also require additional permissions.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://pixabay.com\&sz=32)

Pixabay+1



My recommendation for Dispatch:

- Search only when the user initiates Auto B-roll.
- Cache search responses for 24 hours.
- Download only selected footage.
- Preserve contributor and source-page metadata.
- Display a Pixabay source label in the asset picker.
- Obtain confirmation from Pixabay before scaling this into high-volume automated searches and downloads.

Also, keep the API key server-side and redact it from request logs.

## 7. Where to store the footage

Given Dispatch's local-first architecture, I'd use:

Default: PC-local B-roll cache

`Dispatch/media/broll/pixabay/`

Store downloaded, selected footage locally for PC rendering.

Cloud mode: Cloudflare R2

Upload only selected B-roll assets when cloud rendering actually requires them.

Keep asset metadata and hashes so the same video isn't uploaded repeatedly.

This avoids filling your limited cloud storage with hundreds of unnecessary stock videos.

## 8. My recommended V1 settings

These are starting defaults to test, not fixed editorial rules.

| Setting                             | Initial value             |
| ----------------------------------- | ------------------------- |
| B-roll insertion duration           | 2–4 seconds               |
| Typical spacing                     | 8–12 seconds where useful |
| Search results per query            | 12                        |
| Candidates retained per opportunity | 3                         |
| Default stock quality               | 720p or higher            |
| B-roll audio                        | Muted                     |
| Original speaker audio              | Always preserved          |
| Repeated footage                    | Avoid                     |
| Manual replacement                  | Supported                 |
| Undo/redo                           | Supported                 |

### One critical rendering detail

B-roll must be composited after the base footage is reframed and before captions are burned in, so captions remain visible above it.

The original speaker's audio must continue underneath the stock footage.

Build matching rendering support for both Python FFmpeg and Android Media3. The desktop preview working alone is not enough.

## 9. The final product experience I'd build

[Add Free Stock Footage to Video Online | Fast & Easy - Flixier](https://images.openai.com/static-rsc-4/qyJnVMhBol3pXde-RvZ-ThWii6TIxnlkxtZF0Zn1-K0J5aiUz0vyaA1EoAKcf2f0pT2Nm4pTMiGAl8I1epfF1HBDNq_4wBhR1uC4vu0xQBiohbuMXGVUaUTFGSKbhZNwMtO-udk4OBWf-d6OHcMKzSlJCcKq8q55bFXTAToSesg?purpose=inline)

[The Beginner’s Guide to DaVinci Resolve 18.5 – A New Exclusive MZed Course is Live Now | CineD](https://images.openai.com/static-rsc-4/k5SCgHSLmmod3vrkQregzjsIJWTXu58j_M9X9AMj6raDR6yzXLjtsDoYay2NIl8ADimz5STW7LlfeQUuwiwjVzCXi2JWDs_4CoW5ALlYiLgoGmH-FsqBgGK98NSX3XB8zTYWJ1EhdBgTto-4_H0p8N4USPJqIkAzNbspDmhFE0k?purpose=inline)

[A-Roll and B-Roll Explained and How to Use Them in Your Videos | Fstoppers](https://images.openai.com/static-rsc-4/KOoT3xt9sDWhHZnereuoeCtrM4nXvhRb1andFAL7objV950BX1Hv2ql_Fp8piXJ-VbzOFmDB3bjSfyN6xScrQx9RlAPEY5j1QF_yahkezpy4q9Jb7-QnDmWP9wEtTgcWF2Bb6zOHDqhYWrnA0qN4M-JsyulQ26MNyoZgtqzHLNE?purpose=inline)

10

Inside Dispatch's Clips editor:

1. Click Auto B-roll.
2. Dispatch identifies visual opportunities.
3. Pixabay returns relevant footage.
4. AI selects the strongest candidates.
5. Dispatch inserts the footage at the correct transcript timestamps.
6. User can replace, trim, delete or regenerate individual B-roll segments.
7. Apply and render.

The user should never have to manually search Pixabay for every sentence.

My strongest recommendation: Build transcript-aware visual opportunity detection first, then Pixabay matching, then automatic timeline placement. Don't start by simply connecting Pixabay search to a button. The intelligence that chooses what should be shown and when is what makes this a real Auto B-roll feature.

That will turn Pixabay from a stock-video search engine into a useful part of Dispatch's editing engine.



implementation  plan >>>

# Implementation Plan: Rewrite `AUTOB-ROLL_SPECIFICATION.md`

## Goal Description

Rewrite [AUTOB-ROLL_SPECIFICATION.md](file:///c:/CODE/Dispatch/docs/AUTOB-ROLL_SPECIFICATION.md) from a generic ChatGPT draft into an authoritative, implementation-ready Dispatch engineering specification — matching the rigor of [AUTOCUT_SPECIFICATION.md](file:///c:/CODE/Dispatch/docs/AUTOCUT_SPECIFICATION.md) and grounded in Dispatch's actual codebase, [CANONICAL_ARCHITECTURE.md](file:///c:/CODE/Dispatch/docs/CANONICAL_ARCHITECTURE.md), and [ENGINEERING_MISTAKE_MAP.md](file:///c:/CODE/Dispatch/docs/ENGINEERING_MISTAKE_MAP.md).

### Why the Current Draft Fails Against Dispatch's Real Architecture

Our read-only swarm audit across frontend, Python FFmpeg, Android Media3, Cloudflare Worker, and modern auto-video editors found **7 load-bearing contradictions** in the current draft:

1. **Wrong Execution Topology (PC-Only vs. Mobile-First):** The draft places opportunity detection, query generation, ranking, download, and Pixabay keys inside Python (`dispatch/broll/*`). In reality, Dispatch runs in **Cloud / Mobile-Only Mode** when the creator's PC is off (see [AUTOCUT_SPECIFICATION.md](file:///c:/CODE/Dispatch/docs/AUTOCUT_SPECIFICATION.md)), and **PC → Cloud `project_document` sync does not exist** (`POST /sync/pc/clips` in [sync.py](file:///c:/CODE/Dispatch/dispatch/cloud/sync.py) omits `project_document`), whereas Phone → Cloud (`PUT /clips/{id}/timeline`) → PC pull (`GET /sync/pc/timeline`) already works.
2. **Cloudflare Worker Rendering Misconception:** Section 7 assumes "cloud rendering" consumes R2 uploads. In reality, Cloudflare Worker ([wrangler.toml](file:///c:/CODE/Dispatch/cloud-worker/wrangler.toml)) has a 50 ms CPU / 128 MB RAM limit and **never** runs FFmpeg; final renders happen only on **PC FFmpeg** ([renderer.py](file:///c:/CODE/Dispatch/dispatch/video_engine/renderer.py)) or **Android Media3** ([AndroidClipRenderer.kt](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/renderer/AndroidClipRenderer.kt)).
3. **Pixabay Portrait Blind Spot:** Pixabay's Video API (`https://pixabay.com/api/videos/`) has **no `orientation` filter** (only Pixabay's Image API has `orientation`), prohibits permanent hotlinking, and enforces a 24-hour cache. For 9:16 Reels/Shorts, **Pexels Video API** natively supports `orientation=portrait` and returns `video_pictures[]` (multiple frame thumbnails for multimodal reranking).
4. **Imaginary Paths & URIs:** The draft invents `Dispatch/media/broll/pixabay/` (ignoring `STORAGE_DIR` and `retention.py`'s 10 GB ceiling) and `media://broll/12345.mp4` (unsupported by React preview, FFmpeg, Media3, and Cloudflare R2).
5. **Existing `CaptionGroup` Overlay Traps:** Although [types.ts](file:///c:/CODE/Dispatch/frontend/src/captions/types.ts) defines `overlay_url`/`overlay_type`, using `CaptionGroup` for Auto B-roll is a **Wrong Abstraction** ([ENGINEERING_MISTAKE_MAP.md](file:///c:/CODE/Dispatch/docs/ENGINEERING_MISTAKE_MAP.md)): `InteractiveCanvasItem` locks overlays into a 100–280 px sticker box, `DispatchCaptionRenderer` ignores overlays entirely, `normalizeTrack` injects dummy empty `words[]`, and `CaptionStudio` resets `activePanel` when a non-text layer is selected.
6. **Silent Persistence & Single-Segment Rendering Bugs:** `ClipsScreen` splits saves between `/captions` and `/timeline` (`commitTimeline`); `approval.ts` in `pc_workstation` mode omits `project_document`; `renderer.py` and `AndroidClipRenderer.kt` both skip track remapping when `len(timeline_spans) == 1`; and `AndroidClipRenderer.kt` currently uses a single `EditedMediaItemSequence` that ignores all non-text overlay tracks.
7. **Missing Modern Editorial & Visual QA Guardrails:** The draft has no frame-0 / hook guard, no visual thumbnail reranking contract (relying on unreliable uploader tags), no safe-band floating-card option for 16:9 media in 9:16 frames, and no explicit V1 non-goals.

### Benchmark Synthesis: What Trending Auto-Editors Do (and What We Reject)

| Pattern / Editor | What Works (Adopt in Dispatch V1) | What Is Overengineering (Reject for V1) |
|---|---|---|
| **Remotion** (`Editor Starter`, `skills`) | Normalized separation of `tracks[]`, placements (`items`), and media `assets` metadata; keeping video + captions synced under shared source intervals. | React + headless Chromium + ~950 MB `node_modules` / Remotion Lambda in Dispatch's render pipeline. |
| **HyperFrames** (`heygen-com/hyperframes`) | Muting B-roll `<video>` by default; strict 9:16 safe band (`y = 168..1330 px` clear of top chrome and bottom captions); 6–8 s cadence; keeping Frame 0 as speaker/hook. | HTML/CSS/GSAP headless Chrome frame-capture renderer or synthetic charts/SVG motion graphics on Media3. |
| **OpusClip** (`AI B-Roll`, `ClipAnything`) | **Two-phase UX ("Find Spots → Review/Apply")** before fetching/baking; distinguishing flexible-duration *still images* from clamped-duration *stock video*; unique clip dedupe. | AI video-to-video / diffusion generation (`AI Style`) and per-frame active saliency tracking (`ReframeAnything`). |
| **Submagic & Captions.ai** | Transcript-triggered B-roll + **Ken Burns still images/screenshots** (high ROI for SaaS/numbers where stock video fails); toggleable cutaway vs. overlay; clean cuts. | Bundled meme/GIF engines (Giphy forbids caching/downloading + IP risk) and automated SFX whooshes/BGM mixing in V1. |
| **Descript** (`Scenes` + `Underlord`) | Anchoring B-roll to transcript/source intervals so deleting words or AutoCut silences automatically trims/splits B-roll via `intersect_source_interval()`. | Storing B-roll in mutable output-timeline seconds. |
| **MoneyPrinterTurbo + TwelveLabs** | Filtering `orientation=portrait` + minimum duration, picking smallest covering rendition, muting stock (`audio=False`), capping at 2–5 s, deduplicating source clips, and **multimodal VLM reranking** of candidate thumbnails. | Dedicated TwelveLabs Marengo/Pegasus vector indexing (overkill for ephemeral public stock search; 1 batched Gemini multimodal thumbnail call is 10x simpler). |

---

## User Review Required

> [!IMPORTANT]
> **Major Architectural Pivots (Karpathy Principle 1 — Push Back & Surface Tradeoffs):**
> Instead of the draft's PC-only Pixabay Python pipeline, our research points to a **mobile-first, minimal architecture** matching [AUTOCUT_SPECIFICATION.md](file:///c:/CODE/Dispatch/docs/AUTOCUT_SPECIFICATION.md):
> 1. **Dedicated `broll` track in `project_document.tracks`** (source-time anchored `[source_start, source_end)`), **not** overloading `CaptionGroup.overlay_url`.
> 2. **Phone-first AI + Edge Stock Proxy:** Client-side Gemini Flash-Lite (reusing `getGeminiApiKey` + L1/L2 caching in **2 batched calls**: Call 1 = spots + queries, Call 2 = batched `inlineData` thumbnail rerank) + lightweight **`cloud-worker` stock search proxy** (`GET /broll/search` using `caches.default` 24 h cache + Wrangler secrets, with optional PC `/api/broll/search` fallback).
> 3. **Pexels primary + Pixabay fallback** behind a tiny `BrollProvider` adapter, because Pexels supports native `orientation=portrait` and `video_pictures[]` multi-frame thumbnails whereas Pixabay Video API has no `orientation` filter.
> 4. **On-demand local asset caching (`STORAGE_DIR/broll_cache/` on PC, `cacheDir/broll/` on Android)** from durable provider asset descriptors—avoiding wasting Dispatch's 2 GB/user R2 quota on public stock files.

---

## Open Questions

> [!WARNING]
> Please confirm or override these **4 decisions** before we rewrite [AUTOB-ROLL_SPECIFICATION.md](file:///c:/CODE/Dispatch/docs/AUTOB-ROLL_SPECIFICATION.md):

1. **Stock Provider Strategy:** Should the spec standardize on **Pexels (primary, native portrait) + Pixabay (fallback)** behind a tiny `BrollProvider` contract, OR stay **Pixabay-only** (accepting manual `width < height` over-fetch filtering)? *(Recommended: Pexels primary + Pixabay fallback)*
2. **Execution & Key Topology:** Should Auto B-roll run **Mobile/Cloud-First** (client-side Gemini BYOK + `cloud-worker` `GET /broll/search` proxy with 24 h edge cache + PC fallback), OR **PC-Workstation-Only** in V1? *(Recommended: Mobile/Cloud-First, matching AutoCut)*
3. **V1 Visual Modalities & Framing:** Should V1 specify:
   - **(A) Simplest V1:** Fullscreen `cover` muted stock videos only (cleanest Python FFmpeg + Android Media3 `Composition` parity), OR
   - **(B) High-ROI V1:** Fullscreen `cover` + safe-band `card` overlay (`y = 168..1330 px`) + **Ken Burns still images** (covers 16:9 software/metric explainers cleanly without awkward vertical cropping)?
   *(Recommended: Specify **Fullscreen `cover` Stock Video** as V1 core with **`card` overlay + Ken Burns Stills** either in V1b or explicitly scoped if Android `BitmapOverlay` parity is acceptable)*
4. **Renderer Parity Timing:** Should the spec mandate **simultaneous PC FFmpeg + Android Media3 parity** for V1, OR allow **PC FFmpeg + Live Mobile Preview first, followed by Android Media3 Composition** as Milestone 2? *(Recommended: Live Mobile Preview + PC FFmpeg as Phase 1, Android Media3 2-sequence `Composition` as Phase 2 within the same spec so neither breaks `render_clip`)*

---

## Proposed Changes

### Documentation Specification

#### [MODIFY] [AUTOB-ROLL_SPECIFICATION.md](file:///c:/CODE/Dispatch/docs/AUTOB-ROLL_SPECIFICATION.md)

Surgical rewrite of [AUTOB-ROLL_SPECIFICATION.md](file:///c:/CODE/Dispatch/docs/AUTOB-ROLL_SPECIFICATION.md) (removing all broken `images.openai.com` / `utm_source=chatgpt.com` paste artifacts) into a clean, canonical Dispatch spec structured as follows:

1. **Executive Summary & Design Philosophy**
   - Grounded in Karpathy's 4 Principles & [ENGINEERING_MISTAKE_MAP.md](file:///c:/CODE/Dispatch/docs/ENGINEERING_MISTAKE_MAP.md).
   - **Explicit V1 Non-Goals:** No headless Chromium HTML/React renderers (Remotion/HyperFrames), no diffusion AI video generation, no TwelveLabs vector DBs, no Giphy/meme APIs, no auto-SFX/BGM mixing, no per-frame saliency cropping.
2. **High-Altitude Architecture (Mobile-First + PC + Cloud)**
   - Mermaid topology diagram showing: Clean Source Slice → Client Gemini Call 1 (Spots + Queries) → Cloud Worker Stock Proxy (`caches.default` 24 h) → Client Gemini Call 2 (Multimodal Thumbnail Rerank) → Audition & Review Drawer → `project_document.tracks[kind="broll"]` (`PUT /timeline`) → Live Preview (`z-15` under `z-20` captions) → Single Final Bake (PC FFmpeg or Android Media3).
3. **Canonical Schema & Timebase Contract (`project_document.tracks`)**
   - Exact JSON schema for `kind: "broll"` track (`coordinate_system: "source_relative"`), separating track items from `CaptionGroup` (`Wrong Abstraction` avoidance).
   - Explicit resolution of `asset_ref` (`provider`, `provider_asset_id`, `download_url`, `thumbnail_url`, `source_page_url`, `creator`, `width`, `height`, `duration`, optional `r2_key` for custom uploads) — replacing the imaginary `media://` URI.
   - How `intersect_source_interval()` ([segments.py](file:///c:/CODE/Dispatch/dispatch/segments.py) / [timelineEngine.ts](file:///c:/CODE/Dispatch/frontend/src/captions/timelineEngine.ts)) and `compute_render_fingerprint()` handle non-destructive trims, splits, and AutoCut edits.
4. **2-Call AI Opportunity & Multimodal Reranking Pipeline**
   - **Call 1 (Structured JSON `responseSchema`):** Transcript audit with **editorial hard guards**: Frame-0 / hook protection (`source_start >= 3.0s`), min spacing (`6–8s`), duration bounds (`2.0–4.0s`), punchline/CTA/emotion speaker preservation, max slots by clip length.
   - **Stock Retrieval (`BrollProvider`):** Pexels (`orientation=portrait` when output is 9:16) + Pixabay fallback via `cloud-worker` proxy (`wrangler secret put`, Edge `caches.default` ≥ 24 h cache, provider attribution preserved).
   - **Call 2 (Single Batched Multimodal Rerank):** Fetch ~300–400 px preview thumbnails client-side and send as `inlineData` in 1 batch Gemini call to visually reject tag mismatches/watermarks and enforce clip-wide asset deduplication (`<= 2 RPM` total budget, 2-tier transcript-signature cache matching AutoCut).
5. **UI, Audition Preview & Live Layer Compositing**
   - Two-phase **Find Spots → Review/Swap/Edit → Apply** drawer matching [AUTOCUT_SPECIFICATION.md](file:///c:/CODE/Dispatch/docs/AUTOCUT_SPECIFICATION.md) and [AGENTS.md](file:///c:/CODE/Dispatch/AGENTS.md) mobile invariants (44 px targets, single Hero CTA, 50/50 footer, `React.memo`).
   - Live browser/WebView rendering on `z-15` (between base `<video>` `z-10` and captions `z-20`) across `InteractiveCaptionCanvas`, `DispatchCaptionRenderer`, and fullscreen preview, avoiding `CaptionGroup`'s 280 px sticker-box trap.
6. **Delivery Rendering Parity (Python FFmpeg & Android Media3) + Asset Staging**
   - **Python FFmpeg ([renderer.py](file:///c:/CODE/Dispatch/dispatch/video_engine/renderer.py), [reframer.py](file:///c:/CODE/Dispatch/dispatch/video_engine/reframer.py)):** Composite post-EDL `[timeline_start, timeline_end)` B-roll inputs between `[base]` and `subtitles=` burn-in while keeping A-roll audio untouched.
   - **Android Media3 ([AndroidClipRenderer.kt](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/renderer/AndroidClipRenderer.kt), [RenderForegroundService.kt](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/renderer/RenderForegroundService.kt)):** Pre-download B-roll assets into `cacheDir/broll/`, refactor single-sequence render into 2-sequence `Composition` (continuous A-roll audio sequence + alternating A/B visual sequence) with composition-level `OverlayEffect(captionOverlay)`.
   - **Local Cache Retention:** Bounded `STORAGE_DIR/broll_cache/` LRU policy integrated with [retention.py](file:///c:/CODE/Dispatch/dispatch/storage/retention.py)'s 10 GB cap.
7. **Known Codebase Preconditions & Bugs to Fix Before Implementation**
   - Concise checklist of real bugs found during audit (`ClipsScreen` `/captions` vs `/timeline` split-brain save, `approval.ts` `pc_workstation` omitting `project_document` & `mobile_render_cloud` skipping `captionTrack` when `otherTracks` exists, single-segment remap skip in `renderer.py:124` and `AndroidClipRenderer.kt:673`, `useCaptionHistory` scope).
8. **Goal-Driven Verification & Acceptance Criteria**
   - Concrete, testable acceptance checks (EDL split/AutoCut survival, render fingerprint invalidation, PC FFmpeg + Android Media3 output checks, offline/429 fallback, licensing attribution, zero secret leakage).

---

## Verification Plan

Since this task modifies a technical specification document (no application source edits), verification will be conducted via an automated **Swarm Verifier Pass** (`verifier` worker + coordinator check) after rewriting [AUTOB-ROLL_SPECIFICATION.md](file:///c:/CODE/Dispatch/docs/AUTOB-ROLL_SPECIFICATION.md):

### Automated / Static Verification Checks
1. **Symbol & Path Grounding Audit:** Verify with `rg` / `read_file` that every referenced Dispatch file path, function/method name, endpoint, and line contract cited in the new `AUTOB-ROLL_SPECIFICATION.md` actually exists in the current repository (zero hallucinated paths/symbols).
2. **Paste-Artifact & Hygiene Check:** Verify zero leftover `images.openai.com`, `utm_source=chatgpt.com`, stray numbers, or broken markdown links remain (`rg -n "openai|chatgpt|media://" docs/AUTOB-ROLL_SPECIFICATION.md`).
3. **Mistake-Map & Karpathy Compliance Audit:** Dedicated `verifier` swarm worker checks the rewritten spec against:
   - Every relevant failure mode in [ENGINEERING_MISTAKE_MAP.md](file:///c:/CODE/Dispatch/docs/ENGINEERING_MISTAKE_MAP.md) (Schema drift, Data inconsistency, Broken contract, Secret leakage, Wrong abstraction, Overengineering, Incomplete task closure).
   - Every architectural invariant in [CANONICAL_ARCHITECTURE.md](file:///c:/CODE/Dispatch/docs/CANONICAL_ARCHITECTURE.md) and [AGENTS.md](file:///c:/CODE/Dispatch/AGENTS.md).
4. **Markdown & Diagram Sanity:** Check Mermaid syntax (quoted node labels, supported `flowchart` syntax) and inspect final diff via `git diff docs/AUTOB-ROLL_SPECIFICATION.md`.

### Manual Verification
- User review of the rewritten [AUTOB-ROLL_SPECIFICATION.md](file:///c:/CODE/Dispatch/docs/AUTOB-ROLL_SPECIFICATION.md) to confirm V1 scope, provider choice, and rendering phasing align with product roadmap before any implementation work begins.
