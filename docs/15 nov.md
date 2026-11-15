# Dispatch - Master Task List & Architecture Roadmap (15 November 2026)

> **Document Status:** Active & Canonical Master Tracker  
> **Date:** November 15, 2026 (15 Nov)  
> **Milestone:** Core Video Engine Stabilization & Strict 2-Window Architecture Simplification  
> **Authoritative Baseline:** Architecture Simplification Directive (Eliminating Raw-Access Over-Engineering, Multi-Coordinate Drift & Quota Blocking)  
> **Protocol Rule:** This document is updated incrementally after every single completed task. Any incoming AI agent or developer can resume work directly from this file without context loss.

---

## 1. Executive Summary & Architectural Objective

**Dispatch** is an AI-powered short-form video extraction workstation. Over the past month, the project suffered from **feature over-engineering**, specifically around multi-coordinate transformations, raw-access full video streaming, dynamic extension window rebasing, and multi-tenant quota blocking. This caused trimming desyncs, subtitle timecode drift, and video playback freezes on the mobile review frame and workstation.

### The Objective:
Strip down the architecture to its core, low-friction essentials so the creator can reliably generate content without bugs.

Enforce the foundational architectural invariant:
> **"The Temporary Edit Window IS the Master File."**

The start of the temporary edit window proxy is strictly treated as `0.000`. All timeline scrubbing, waveforms, filmstrips, subtitle timestamps, and user trim handles operate strictly in the zero-based interval $[0.0, \text{edit\_window\_duration}]$.

---

## 2. Canonical System State & Environment

| Component | Target / Specification | Current Live State |
| :--- | :--- | :--- |
| **Android Package** | `com.resolvia.dispatch` | Version `1.7.50` (`versionCode = 118`) |
| **Release Tag** | Monotonic SemVer Git Tag | `v1.7.50-alpha` |
| **Release Certificate** | Trusted keystore | SHA-256 fingerprint ends in `...3804` |
| **Release Distribution** | GitHub Releases & `gh-pages` | Hosted at `https://resolviaai.github.io/Dispatch/` |
| **PC Daemon API** | FastAPI / SQLite / Gemini Transcribe & Flash Lite / FFmpeg | `http://127.0.0.1:8000` |
| **Cloudflare Worker** | Cloud API & Edge Storage Gateway | `https://dispatch-cloud.resolviaai.workers.dev` |
| **Supabase Cloud** | Auth, Profiles, Cloud DB | `https://aldzlbdxplqkcnyjhzmm.supabase.co` |
| **Web Review Studio** | Vite + React + Tailwind v3 (Dispatch Design System v3) | Embedded in Android Assets & PC Review UI |

---

## 3. Operational Invariants: Protected Modules vs. What Gets Simplified

### ✅ PROTECTED MODULES (DO NOT TOUCH OR REMOVE)
1. **Route A Ingestion (Android → YouTube Unlisted → PC Poller)**:
   - File paths: [`dispatch/youtube_inbox/`](file:///c:/CODE/Dispatch/dispatch/youtube_inbox/), [`dispatch/transport/`](file:///c:/CODE/Dispatch/dispatch/transport/)
   - **Reason**: Fully functional, low-friction automated transport moving footage from phone to PC. Kept 100% intact.
2. **Android 10-Minute Rolling Chunks**:
   - File paths: [`android/`](file:///c:/CODE/Dispatch/android/), `CameraCaptureManager`
   - **Reason**: Essential hardware safety. Rolling every 10 minutes prevents total footage corruption if the phone overheats, drains battery, or crashes.
3. **Publisher Module**:
   - File path: [`dispatch/publisher/`](file:///c:/CODE/Dispatch/dispatch/publisher/)
   - **Reason**: Fully functional multi-platform publishing (YouTube Shorts, Instagram, Facebook, LinkedIn). Prevents upload procrastination.
4. **Existing Supabase & Cloudflare Auth/Schema**:
   - Do **NOT** rewrite database schemas or run destructive migrations.

---

## 4. Prioritized Task Register & Implementation Stages (Phase 1)

| Task ID | Category | Feature / Problem | Canonical Implementation | Priority | Status |
| :---: | :--- | :--- | :--- | :---: | :---: |
| **T01** | Architecture | Kill Full Raw Access & Multi-Coordinates | Enforce strict 2-Window model with zeroed time base (`0.000`) on edit window proxies | P0 | **COMPLETED & VERIFIED** |
| **T02** | Captions | Eliminate Coordinate Space Drift | Remove `source_relative` and `window_relative` shifts in `range_sync.py`, `renderer.py`, `db.py` | P0 | **COMPLETED & VERIFIED** |
| **T03** | Video Engine | Zero PTS Trimming Alignment | Add `-avoid_negative_ts make_zero` in `renderer.py`, `edit_windows.py` | P0 | **COMPLETED & VERIFIED** |
| **T04** | Video Engine | Clean Fading Transitions | Standardize `fade=t=in:st=0:d=0.3,fade=t=out:st=<duration-0.3>:d=0.3` and `afade` | P1 | **COMPLETED & VERIFIED** |
| **T05** | Cloud & Quota | Single-User Quota Simplification | Allocate full 10 GB quota floor to owner (`resolviaai@gmail.com`) and bypass workstation quota blocks | P0 | **COMPLETED & VERIFIED** |

---

## 5. Task Breakdown & Deep Technical Context

### Task 1: Simplify to a Strict 2-Window Architecture (Kill Full Raw Access)

#### The Problem:
Previously, the codebase attempted to manage three conflicting coordinate systems:
1. `source_relative` (full 2-hour master video coordinates)
2. `window_relative` (temporary bounded window coordinates)
3. `clip_relative` (individual short clip coordinates)

It also introduced complex fallback logic to stream the full raw master video from the PC (`/api/clips/{clip_id}/full-proxy`), recalculate extension windows (`compute_extension_window`), and merge uncovered audio spans. This multi-coordinate switching caused subtitle drift, timeline confusion, and trimming bugs.

#### The Solution:
Enforce the rule: **"The Temporary Edit Window IS the Master File."**
1. **The 2 Windows**:
   - **Window 1 (Gemini Suggestion)**: The raw AI-detected highlight (e.g., 20s–30s).
   - **Window 2 (Temporary Edit Window)**: The editing sandbox with context padding:
     $$\text{window\_start} = \max(0.0, \text{suggest\_start} - 60.0)$$
     $$\text{window\_end} = \min(\text{source\_duration}, \text{suggest\_end} + 60.0)$$
     Capped at a maximum duration of $300.0\text{ s}$ (5 minutes).
2. **Time Base Zeroing (`0.000`)**:
   - Cut this Temporary Edit Window once into a local faststart proxy file.
   - **Crucial**: Treat the start of this edit window file strictly as `0.000`.
   - All timeline scrubbing, waveform displays, filmstrips, subtitle timestamps, and user trim handles operate strictly in the interval $[0.0, \text{edit\_window\_duration}]$.
3. **Deprecate / Suppress**:
   - Suppress and remove `source_relative` and `window_relative` translations in:
     - [`dispatch/captions/range_sync.py`](file:///c:/CODE/Dispatch/dispatch/captions/range_sync.py)
     - [`dispatch/video_engine/renderer.py`](file:///c:/CODE/Dispatch/dispatch/video_engine/renderer.py)
     - [`dispatch/db.py`](file:///c:/CODE/Dispatch/dispatch/db.py)
     - [`dispatch/edit_windows.py`](file:///c:/CODE/Dispatch/dispatch/edit_windows.py)
   - Remove the "Full Raw Access" streaming fallback and dynamic extension window merging. If the user wants to trim, they trim within the generous $\pm 60\text{ s}$ (up to $300\text{ s}$) edit window.

---

### Task 2: Stabilize FFmpeg Trimming, Fading & PTS Alignment

#### The Problem:
- Trimming without resetting presentation timestamps causes negative timestamps, frozen first frames, and audio desync.
- Fading was tangled in monolithic filter complexes, causing syntax failures when calculating durations.

#### The Solution:
1. **Frame-Accurate Cuts with Zeroed PTS**:
   When trimming the final approved clip from the edit window, always apply `-avoid_negative_ts make_zero`:
   ```bash
   ffmpeg -y -ss <start> -i <edit_window_proxy> -t <duration> \
     -c:v libx264 -preset fast -crf 18 \
     -c:a aac -b:a 192k \
     -avoid_negative_ts make_zero \
     <output_clip_path>
   ```
2. **Clean Fading Transitions**:
   - When applying fade-in/fade-out, apply video and audio fades cleanly on normalized segments:
     - Video: `fade=t=in:st=0:d=0.3,fade=t=out:st=<duration-0.3>:d=0.3`
     - Audio: `afade=t=in:ss=0:d=0.3,afade=t=out:st=<duration-0.3>:d=0.3`
   - For multi-scene concatenations, normalize clips to uniform resolution/FPS first, then use `xfade` and `acrossfade` rather than fragile manual filter chains.

---

### Task 3: Single-User Quota Simplification

#### The Problem:
Multi-tenant quota checks, reservation ceilings (2 GB default), and friend-banning checks during local workstation processing create artificial barriers for the owner workstation.

#### The Solution:
- Do **NOT** modify or destroy Supabase tables.
- Ensure the owner account (`resolviaai@gmail.com`) is allocated the full 10 GB quota floor (`10_000_000_000` bytes) in [`dispatch/cloud/config.py`](file:///c:/CODE/Dispatch/dispatch/cloud/config.py).
- Bypass or suppress runtime multi-tenant quota blocking and friend-banning checks during local workstation processing in [`dispatch/cloud/quota.py`](file:///c:/CODE/Dispatch/dispatch/cloud/quota.py) and [`dispatch/web/app.py`](file:///c:/CODE/Dispatch/dispatch/web/app.py).

---

## 6. Deferred Roadmap: What We Are NOT Doing Now (Phase 2)

> **DO NOT IMPLEMENT THESE NOW.** Keep them in mind for architectural compatibility only:

1. **Pluggable Caption Styling Engine**:
   - Declarative preset architecture for TikTok/Hormozi word-pill boxes, word-pop scaling, and contextual emoji injection will be added in Phase 2.
2. **Pyannote Speaker Diarization**:
   - Biometric voice separation (Host vs. Guest auto-framing) is deferred.
3. **Automated Stock B-Roll Injection**:
   - Pexels/Pixabay automated insertion is deferred.

---

## 7. Architectural & Design System Invariants

Every change must strictly obey the repository-wide frontend constitution (`design.md` v3 / `AGENTS.md`):

1. **The Single-Direction Law:** On mobile viewports, every screen section moves along strictly **ONE** physical axis (vertical card stack OR horizontal snap card carousel). Never 2D multi-column grids.
2. **Biomechanical 44px Hitboxes & Safe Areas:** Minimum touch bounding box of $44\text{px} \times 44\text{px}$ with $\ge 8\text{px}$ separation and safe area clearance.
3. **4-Layer Elevation Architecture (60/30/10 Rule):**
   - Layer 0 (Canvas Root - 60%): `#161616` (`bg-studio`, `bg-background`).
   - Layer 1 (Cards & Elevated Surfaces - 30%): `#1C1C1C` with border `#2E2E2E` (`bg-surface-100`, `border-border`).
   - Layer 2 (Recessed Wells & Inputs): `#232323` (`bg-surface-200`) with tracks at `#282828`.
   - Layer 3 (Hero Brand CTA & Focus - 10%): Electric Creator Blue `#2563EB` (`bg-primary`).
   - Semantic Colors (Status Only): Success `#22C55E`, Warning `#F59E0B`, Danger `#EF4444`. Red is strictly reserved for destructive and recording states.
4. **Law of the Single Hero CTA:** Each viewport must have **exactly ONE primary glowing hero action button**. Secondary actions remain neutral recessed surfaces.
5. **Senior Typographic Limit & Numeric Alignment:** Maximum 4 font sizes (18px, 14px, 12px, 10px), 2 weights. Mobile body text is 14px. Numbers strictly enforce `font-mono tabular-nums` and right alignment.
6. **Zero Native OS Emojis:** Never use native OS emojis (🚀, 💡, 🔥, ⚡, 📦) as interface icons. Use crisp SVG vector icons (`lucide-react`, Phosphor, 1.5px–2px stroke).
7. **Concentric Geometry:** $R_{\text{inner}} = R_{\text{outer}} - \text{Padding}$.
8. **Tactile Feedback & Micro-Interactions:** Immediate tactile feedback on touch/click: `active:scale-[0.98] transition-all duration-150`.

---

## 8. Live Progress & Execution Log

| Timestamp (IST) | Sub-task / Action | Files Modified | Outcome & Verification |
| :--- | :--- | :--- | :--- |
| **2026-11-15 11:35** | Architectural Audit Completed | `dispatch/edit_windows.py`, `renderer.py`, `reframer.py`, `range_sync.py`, `worker.py` | Analyzed coordinate drift, PTS cut flags, and quota rules. Confirmed Route A, CameraX chunks, and Publisher invariants. |
| **2026-11-15 11:37** | Execution Plan Confirmed | `docs/15 nov.md`, `15 nov.md` | User approved execution plan. Master tracking document initialized. |
| **2026-11-15 11:40** | Task 1: Strict 2-Window & Zeroed Time Base | `dispatch/edit_windows.py`, `dispatch/video_engine/renderer.py`, `dispatch/db.py`, `dispatch/web/app.py` | Enforced zeroed $0.000$ time base on edit window proxy; word timings stored window-relative; suppressed full raw proxy streaming fallback and multi-coordinate shifts. |
| **2026-11-15 11:42** | Task 2: FFmpeg PTS & Fading Stabilization | `dispatch/video_engine/renderer.py`, `dispatch/video_engine/reframer.py`, `dispatch/edit_windows.py` | Added `-avoid_negative_ts make_zero` across all trims; implemented `build_fade_filters()` and `build_xfade_filter()`; added clean fading in `reframer.py`. |
| **2026-11-15 11:44** | Task 3: Single-User Quota Simplification | `dispatch/cloud/config.py`, `dispatch/cloud/quota.py`, `dispatch/web/app.py` | Configured 10 GB owner quota floor for `resolviaai@gmail.com`; added workstation quota bypass in `quota.py`. |
| **2026-11-15 11:45** | Regression Test Verification | `tests/test_edit_windows.py`, `tests/test_cloud_quota.py`, `tests/test_caption_range_sync.py`, `tests/test_renderer_caption_contract.py` | 43/43 unit tests passed (100% pass rate in 4.80s). |
| **2026-11-15 12:00** | Full Multi-Tier Test Suite | `frontend/`, `cloud-worker/`, `tests/test_pending_pc_renders.py` | 140/140 Worker tests passed; 9/9 pending PC render tests passed; all frontend unit/separation/integrity tests passed 100%. |
| **2026-11-15 12:10** | SemVer Bump & Asset Compilation | `android/app/build.gradle.kts`, `frontend/package.json`, `README.md`, `site/update.json` | Bumped to `v1.7.50` (`versionCode = 118`); frontend rebuilt to `android/app/src/main/assets/web/`. |
| **2026-11-15 12:16** | Release Assembly & Verification | `scripts/prepare_release.ps1` | `Dispatch.apk` generated (26,694,638 bytes, SHA-256 `4c2931ba83e1e19fb9299e3b60a367a4684354cbf5bb52c9dec86571c02d60d7`); verified signature `...3804`. |
| **2026-11-15 12:18** | Release Artifact Synchronization | `site/update.json` | Updated in-app update descriptor with verified SHA-256 and byte size. |
| **2026-11-15 12:21** | Remote Push & Tag Publishing | `origin/main`, `v1.7.50-alpha` | Pushed commits and tag `v1.7.50-alpha` to GitHub remote `Resolviaai/Dispatch`. |
| **2026-11-15 12:22** | GitHub Pages Deployment | `scripts/deploy_gh_pages.ps1` | Deployed `Dispatch.apk` and `update.json` to `gh-pages` branch; live at `https://resolviaai.github.io/Dispatch/`. |
| **2026-11-15 12:33** | Emulator App Update | `emulator-5554`, `adb install -r` | Installed `v1.7.50` (`versionCode = 118`) onto `emulator-5554`; verified launch and active `MainActivity` focus without errors. |
