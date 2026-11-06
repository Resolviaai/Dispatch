# Dispatch - Master Task List & Architecture Roadmap (5 November 2026)

> **Document Status:** Active & Canonical Master Tracker  
> **Date:** November 5, 2026  
> **Milestone:** Stage 3 CapCut-Grade Timeline Engine, Unified EDL & Style-Aware Auto-Edit  
> **Authoritative Architecture:** `docs/architecture/DISPATCH_V2_ARCHITECTURE.md` (Current Release: `v1.7.32`, Build 100)  
> **Protocol Rule:** This document is updated incrementally after every single completed task. Any incoming AI agent or developer can resume work directly from this file without context loss.

---

## 1. Executive Summary & Product Architecture

Dispatch is in production (`v1.7.32`, versionCode 100). A rigorous audit of the main branch at commit `e219074` verified that while Dispatch has a capable timeline math engine (`timelineEngine.ts`, `segments.py`), its UI presentation and editing ergonomics fail to match modern standards (CapCut / Premiere).

### 1.1 The Core UI/UX Shift: CapCut Interaction Model
* **The Problem:** 
  1. The video track currently renders as a single continuous filmstrip with outer trim handles, regardless of how many cuts have been made.
  2. Multi-piece cuts are rendered as an auxiliary "Pieces (N)" chip strip above the timeline with tiny 16px delete buttons.
  3. A confusing scissors icon is embedded inside text/overlay layer blocks.
  4. `CaptionStudio.handleSplit()` creates ambiguity: it checks text/overlay selection first, then overlapping layers, and only as a fallback splits the video.
* **The Solution:**
  1. **One Code Path:** Always render visual segment blocks (`pieces`) on the main video track. A clip with no cuts is simply a single piece.
  2. **Clear Seams & Selection:** Adjacent EDL pieces display a clear cut seam. Tapping a piece selects it, rendering a high-contrast bounding frame and 44px dual trim handles on that specific block.
  3. **Contextual Toolbar:** Distinct operations in the main toolbar:
     - **Split:** Explicitly labeled (*"Split video at playhead"* or *"Split selected layer"*). Shortcut: `Ctrl+B` / `Cmd+B`.
     - **Trim:** Edge trim (changes piece duration) and Roll trim (dragging shared seam between adjacent pieces moves cut point while preserving overall duration).
     - **Delete + Ripple:** Tapping Delete removes the selected piece and ripples remaining pieces forward.
  4. **Remove In-Layer Scissors:** Delete the tiny scissors button inside layer blocks to eliminate visual clutter.

### 1.2 Unified EDL Architecture: Manual & Auto-Edit on the Same Model
Manual edits and AI automatic cuts share the **exact same Edit Decision List (EDL)**:
* Manual Split $\to$ creates an EDL boundary.
* Manual Trim $\to$ adjusts EDL piece boundaries.
* Ripple Delete $\to$ removes an EDL piece and closes the gap.
* AI Auto-Cut $\to$ proposes EDL intervals to remove.
* Accepting an AI cut $\to$ updates the exact same EDL segments via `commitTimeline()`.
* Preview playback, ASS subtitle export, Android Media3 render, and PC FFmpeg render all consume that identical EDL.

### 1.3 Style-Aware Auto-Edit (Rohit's Speaking Style & Thinking Pattern)
Rohit's recordings feature real-time thinking, associative idea development, self-corrections, rhetorical repetition, and deep reasoning chains. A naive filler-word remover would destroy the natural rhythm and personality.
* **Mechanical Restart Loops** (e.g. *"I mean, I mean, I mean"*): Propose removing redundant stutters.
* **Self-Corrections** (e.g. *"I think… actually, no…"* followed by a better explanation): **PRESERVE** the correction and insight.
* **Conversational Fillers** (*"so"*, *"okay"*, *"like"*, *"you know"*): Do **NOT** blindly purge; preserve natural cadence.
* **Rhetorical Repetition**: Preserve repeated conclusions that add comedic or emotional emphasis.
* **Dead Air vs. Thinking Pauses**: Distinguish empty dead air ($>2.5\text{s}$) from dramatic/thinking beats.
* **Reviewable Proposal Layer:** Auto-edit suggestions are presented as non-destructive candidate cards and timeline highlights (*"Keep footage"* vs. *"Accept cut"*), with 1-tap *"Apply All"* or selective review.

---

## 2. Canonical System State & Environment

| Component | Target / Specification | Current Live State |
| :--- | :--- | :--- |
| **Android Package** | `com.resolvia.dispatch` | Version `1.7.29` (`versionCode = 97`) |
| **Git Commit Authority** | Monotonic git commit | `e219074` |
| **Release Tag** | Monotonic SemVer Git Tag | `v1.7.29-alpha` |
| **Release Certificate** | Trusted keystore | SHA-256 fingerprint ends in `...3804` |
| **Release Distribution** | GitHub Releases & `gh-pages` | Hosted at `https://resolviaai.github.io/Dispatch/` |
| **PC Daemon API** | FastAPI / SQLite / Gemini Transcribe & Flash Lite | `http://127.0.0.1:8000` |
| **Cloudflare Worker** | Cloud API & Edge Storage Gateway | `https://dispatch-cloud.resolviaai.workers.dev` |
| **Supabase Cloud** | Auth, Profiles, Cloud DB | `https://aldzlbdxplqkcnyjhzmm.supabase.co` |
| **Web Review Studio** | Vite + React + Tailwind v3 | Embedded in Android Assets & PC Review UI |

---

## 3. Prioritized Task Register & Implementation Stages

| Stage | ID | Category | Feature / Problem | Canonical Implementation | Priority | Status |
| :---: | :--- | :--- | :--- | :--- | :---: | :---: |
| **0** | **WHISPER** | Transcription | Complete Faster-Whisper Purge & Gemini Standardization | Standardize on Gemini 3.5 Transcribe (primary) and Flash-Lite (fallback); zero local Whisper | P0 | **[x] VERIFIED** |
| **1** | **P0-A** | Data Safety | Caption edits silently revert on split/trim/delete | Live `captionTrack` overrides stale `parsedDoc?.tracks` in all timeline handlers | P0 | **[x] VERIFIED** |
| **1** | **P0-B** | Android Render | On-device Media3 render has 0 captions on untouched clips | Seed complete `project_document` at approve time with full range and live captions | P0 | **[x] VERIFIED** |
| **1** | **P1-C** | State Safety | Unify timeline operations & add PUT rollback | Consolidate handlers into `commitTimeline(ranges, action)` with toast rollback on 409/error | P1 | **[x] VERIFIED** |
| **1** | **T02** | Backend Render | Approval duration validation 500 error on cut clips | Return `eff_duration` from `renderer.py` and validate against it in `app.py:1688` | P0 | **[x] VERIFIED** |
| **1** | **T01** | Editor / Playback | Multi-piece EDL Playback gap skipping | Use existing `timelineEngine.ts` mapping with `requestVideoFrameCallback` to skip deleted gaps | P0 | **[x] COMPLETED** |
| **1** | **T03** | Subtitles | Stored captions clip-relative; remap at consumers | Remap source times $\to$ output times at ASS export, web overlay, and Android Media3 | P0 | **[x] VERIFIED** |
| **2** | **UI-01** | Timeline UX | CapCut-Style Multi-Segment Video Track | Render `pieces` as distinct visual blocks with cut seams, selection rings & dual handles | P0 | **[x] COMPLETED** |
| **2** | **UI-02** | Toolbar UX | Contextual [Split] & [Delete + Ripple] Buttons | Explicit action labels, 44px hit-targets, 0.1s minimum piece guard, remove chip strip & in-layer scissors | P0 | **[x] COMPLETED** |
| **3** | **T06** | AI / Prompt | Balanced Editorial Highlight Selection | Extract best cohesive standalone moments; remove 'viral only' bar; allow clean `[]` only if unclip-worthy | P1 | **[x] VERIFIED** |
| **3** | **T05** | AI / Sorting | Eliminate Synthetic Virality Score | Remove 1–100 score; sort queue chronologically by session flow; leave DB column intact | P1 | **[x] VERIFIED** |
| **3** | **T07** | AI / Feedback | De-bloat Negative Preference Memory | Backend ignores generic `"Rejected from review studio"`; stops polluting prompt tokens | P1 | **[x] VERIFIED** |
| **3** | **T08** | Studio UI | Hide Unsupported Media Layers | Hide non-functional background music/stickers UI until export parity is built | P1 | **[x] COMPLETED** |
| **4** | **T09** | Auto-Edit | Style-Aware Waveform Auto-Cut Engine | 20ms RMS acoustic energy check, zero-crossing snapping, >=150ms release padding so words are never cut off | P0 | **[x] VERIFIED** |

---

## 4. Technical Architecture: CapCut Timeline & Video Track Specification

```
┌────────────────────────────────────────────────────────────────────────┐
│  [ Select ]   [ ✂️ Split Video ]   [ 🗑️ Delete Piece ]   [ ↶ Undo ] [ ↷ Redo ] │
├────────────────────────────────────────────────────────────────────────┤
│  00:00        00:05        00:10        00:15        00:20             │
│  ───────────────────────────────────────────────────────────────────   │
│  VIDEO  [   Piece 1   ]│[     Piece 2 (Selected)     ]│[   Piece 3   ] │
│                        ↑                             ↑                 │
│                     Cut Seam                      Cut Seam             │
│                    (Roll Trim)                   (Roll Trim)           │
│                                                                        │
│  CAPTIONS [ word ............ ][ word ............................. ]  │
│                                                                        │
│  AI PROPOSALS ░░░░ Pause (2.4s) ░░░░                                  │
└────────────────────────────────────────────────────────────────────────┘
```

### 4.1 Visual Pieces on the Video Track
1. **Output-Clock Layout:**
   Pieces are rendered adjacent to one another along the output presentation clock $t_{\text{out}} \in [0, \text{total\_kept\_duration}]$.
   $$\text{width}_i = \frac{\text{segment}_i.\text{end} - \text{segment}_i.\text{start}}{\text{kept\_duration}} \times \text{timeline\_width}$$
2. **Cut Seams:**
   A 2px vertical seam (`border-r-2 border-[#121418]`) separates contiguous pieces. Tapping the seam enters Roll Trim mode (moving the edit point without altering overall clip duration).
3. **Selection & Dual Trim Handles:**
   Tapping Piece $i$ highlights it with `ring-2 ring-white rounded-lg`.
   CapCut-style amber/white vertical trim handles appear at the left and right ends of the selected piece, with 44px touch bounding boxes.
   Dragging the left handle modifies `source_start`; dragging the right handle modifies `source_end`.
4. **Contextual Toolbar Actions:**
   - When Main Video is selected:
     - **Split:** Slices the piece under the playhead into two pieces via `splitAt()`. Disabled if playhead is within 0.1s of an existing cut.
     - **Delete:** Ripple-deletes the selected piece via `deletePiece()`. Disabled if only 1 piece remains or duration $< 5.0\text{s}$.
   - When a Text/Caption group is selected:
     - **Split:** Calls `splitCaptionGroupAtTime()`.
     - **Delete:** Removes the caption group.

---

## 5. Style-Aware Auto-Edit Rules (Rohit's Speaking Style)

| Spoken Pattern | Classification | Engine Action | User Control |
| :--- | :--- | :--- | :--- |
| Mechanical repetition (*"I mean, I mean, I mean"*) | False Start / Stutter | Propose cut of redundant takes; keep final take | Reviewable card + timeline badge |
| Correction (*"I think… actually, no, here's why"*) | Real-Time Reflection | **PRESERVE**; crucial for authenticity and insight | Never auto-cut |
| Conversational filler (*"so"*, *"like"*, *"you know"*) | Natural Cadence | **PRESERVE**; preserve natural conversational flow | Never auto-cut |
| Repeated conclusion for emphasis | Rhetorical Emphasis | **PRESERVE**; essential for comedic/persuasive impact | Never auto-cut |
| Extended dead air ($>2.5\text{s}$) after a thought | Dead Air | Propose trimming pause down to 0.8s | Reviewable card + timeline badge |
| Abandoned sentence fragment ($<3$ words, dropped) | Abandoned Thought | Propose cut if next take starts a fresh point | Reviewable card + timeline badge |

---

## 6. Regression Verification Fixtures & Acceptance Criteria

1. **Caption Edits Survival Fixture (`frontend`):**
   - Clip split $\to$ edit a caption group's text $\to$ delete another piece $\to$ verify edited caption text is preserved in `project_document.tracks`.
2. **Untouched On-Device Render Fixture (`android`):**
   - Create clip with zero timeline edits $\to$ tap Approve $\to$ verify Android Media3 renders the exact source range with complete ASS captions.
3. **Approval Duration Check Fixture (`tests/test_edl_approval.py`):**
   - Clip with 30s outer range and 10–20s cut out (kept duration = 20s) $\to$ approve $\to$ verify 200 OK and no duration mismatch 500 error.
4. **EDL Playback Frame Callback Fixture (`frontend`):**
   - Clip with cut gap at 10.0s–20.0s $\to$ playback reaches 10.0s $\to$ immediately seeks to 20.0s without rendering frames from the deleted interval.
5. **CapCut Video Track Interaction Fixture (`frontend`):**
   - Verify `CaptionTimeline` renders distinct piece blocks with cut dividers, selected piece ring, and dual trim handles.
   - Verify Split button splits the video piece when video is selected, and splits the caption group when caption is selected.

---

## 7. Incremental Verification Log & Automated Test Evidence

### [x] WHISPER-01 through WHISPER-08: Complete Whisper Purge & Gemini Standardization
- **Verified Files:** `dispatch/transcription/gemini_engine.py`, `dispatch/transcription/transcriber.py`, `dispatch/transcription/worker.py`, `pyproject.toml`, `requirements.txt`.
- **Test Evidence:**
  - `tests/test_pillar2_transcription.py`: 6/6 tests passed (Primary `gemini-3.5-transcribe`, recoverable fallback `gemini-3.5-flash-lite`, PermanentAuthError on 401/403, zero Whisper fallback).
  - `tests/test_gemini_two_stage.py`: 11/11 tests passed.
  - `tests/test_youtube_caption_timing_fix.py`: 7/7 tests passed.
  - `tests/test_youtube_inbox.py`: 6/6 tests passed.
  - `tests/test_end_to_end.py`: End-to-end ingest -> orientation -> Gemini transcribe -> analyze -> render -> publish PASSED in 51.01s.

### [x] P0-A, P0-B, P1-C: Timeline Data Safety & State Machine
- **P0-A Verified:** In `frontend/src/screens/ClipsScreen.tsx` (`commitTimeline`), live `captionTrack` explicitly overrides stale `parsedDoc?.tracks`.
- **P0-B Verified:** In `frontend/src/screens/ClipsScreen.tsx` (`handleApprove`), untouched clips synthesize a complete `project_document` with full range and live captions before dispatching to Android Media3.
- **P1-C Verified:** Unified `commitTimeline` provides optimistic UI update and rolls back with user toast feedback if cloud PUT returns 409 or network error.

### [x] T02: EDL Approval Duration Validation Fix
- **Verified Files:** `dispatch/video_engine/renderer.py:401`, `dispatch/web/app.py:1688`.
- **Test Evidence:** `tests/test_edl_approval.py`: 2/2 tests passed (`test_multi_segment_cut_approval_uses_expected_duration`, `test_multi_segment_duration_mismatch_raises_500`).

### [x] T05 & T06: Balanced Editorial Selection & Virality Score Cleanup
- **T06 Verified:** `dispatch/ai_clips/prompt_templates.py` instructs model to extract cohesive standalone thoughts without demanding a 10/10 sensational viral bar.
- **T05 Verified:** Removed synthetic `{clip.virality_score}%` badge from review card in `ClipsScreen.tsx`; sorting strictly enforced by session flow (`ORDER BY c.created_at ASC`).

### [x] T07: De-bloat Negative Preference Memory
- **Verified Files:** `dispatch/ai_clips/preference_learner.py`.
- **Test Evidence:** `tests/test_ai_highlight_editorial_quality.py`: 16/16 tests passed. Generic strings ("Rejected from review studio", "No reason provided") are ignored and never pollute prompt tokens.

### [x] T09: Waveform-Aware Auto-Cut Engine
- **Verified Files:** `dispatch/video_engine/waveform.py`, `dispatch/video_engine/autocut.py`, `dispatch/web/app.py` (`/api/clips/{clip_id}/autocut/proposals`).
- **Waveform Invariants:**
  - 20ms RMS acoustic energy profiling (`compute_rms_profile`).
  - Zero-crossing snapping (`find_zero_crossing`).
  - Speech release check enforces >= 150ms acoustic padding so word endings / trailing consonants are never amputated based purely on transcript timestamps.
  - Speech attack check enforces >= 100ms pre-speech padding.
  - Rohit's conversational rules: preserves self-corrections (*"I think... actually no"*), preserves conversational fillers (*"so"*, *"like"*), detects mechanical restart loops while keeping the final take.
- **Test Evidence:** `tests/test_waveform_autocut.py`: 6/6 tests passed.

### [x] T01: Multi-Piece EDL Playback Gap Skipping
- **Verified Files:** `frontend/src/screens/ClipsScreen.tsx`, `frontend/src/captions/timelineEngine.ts`.
- **Implementation Highlights:**
  - `handleTimeUpdate` enforces frame-accurate gap skipping using `source_start` and `source_end` bounds:
    - When `cur >= piece.source_end` on piece $i < N - 1$, video immediately jumps forward: `video.currentTime = nextPiece.source_start`, and updates presentation clock via `sourceToConcat`.
    - When `cur >= lastPiece.source_end`, playback cleanly pauses and resets to `firstPiece.source_start`.
    - When `cur < firstPiece.source_start` (underflow), video resets to `firstPiece.source_start`.
    - Dual sync with `bgVideoRef` ensures background canvas remains synchronized across gap jumps.
  - `handleSeek` converts presentation-time targets to source video timestamps via `concatToSource(effectiveRanges, safeTarget, totalSourceDur)` with try/catch fallback to prevent unhandled crashes.
  - `handleTogglePlay` resumes from the start of the first piece if playhead has reached or exceeded playback end.
- **Test Evidence:** `frontend/test_edl_playback_gap_skip.ts`: 7/7 tests passed:
  1. `single piece baseline concatToSource / sourceToConcat`
  2. `2-piece EDL golden vector (0-10s, 20-30s)`
  3. `3-piece multi-gap mapping and duration sum`
  4. `gap-skipping playback loop simulation jumps past deleted segment`
  5. `presentation-clock splitAt maps accurately to source-time split`
  6. `ripple delete and edge trim preserve EDL continuity`
  7. `boundary and out-of-range error guards`

### [x] UI-01: CapCut-Style Multi-Segment Video Track
- **Verified Files:** `frontend/src/captions/CaptionTimeline.tsx`, `frontend/src/screens/ClipsScreen.tsx`.
- **Implementation Highlights:**
  - In `CaptionTimeline.tsx`, main video track accepts `segments`, `selectedPieceIndex`, `onSelectPiece`, and `onTrimPiece`. Defaults cleanly to a single piece if segments are empty or omitted.
  - Video pieces render as distinct adjacent blocks separated by 2px vertical cut seams (`border-r-2 border-[#121418]`).
  - Tapping a piece selects it: renders high-contrast bounding frame (`ring-2 ring-white rounded-md`) and CapCut-style dual 44px trim handles (`w-[44px]` touch target, `w-3.5` white bar with 2 grip ridges) with `touch-none`.
  - Pointer drag updates `onTrimPiece` with `MIN_PIECE_DURATION = 0.1s`.
  - Auxiliary 'Pieces (N)' chip strip removed from `ClipsScreen.tsx`.
  - In-layer scissors button removed from caption/overlay blocks in `CaptionTimeline.tsx`.
- **Build Evidence:** `npm --prefix frontend run build` (`tsc --noEmit && vite build`) passed with exit code 0.

### [x] UI-02: Contextual [Split] & [Delete] Buttons & Invariant Refinements
- **Verified Files:** `frontend/src/captions/CaptionStudio.tsx`, `frontend/src/screens/ClipsScreen.tsx`.
- **Implementation Highlights:**
  - In `CaptionStudio.tsx`, props expanded to include `onDeletePiece`, `canDeletePiece`, `piecesCount`, `selectedPieceIndex`; `areCaptionStudioPropsEqual` comparator updated.
  - Video 'edit' toolbar: 44px [ Split ] and [ Delete ] buttons positioned at slots 1 and 2 directly following [ Back ]. Video split threshold relaxed from $\ge 1.0\text{s}$ to $\ge 0.1\text{s}$. [ Delete ] button wired to `onDeletePiece` (disabled when `canDeletePiece === false` or `piecesCount <= 1`).
  - Text 'text' toolbar: 44px [ Split ] and [ Delete ] buttons positioned at slots 1 and 2 directly following [ Back ]. `handleSplit` disambiguated to operate strictly on `currentGroup`, preventing unintended auto-targeting of other overlapping groups.
  - In `ClipsScreen.tsx`, `handleSplitClip` maps presentation time to source time via `concatToSource` before invoking `splitAt`.
  - Active button micro-interaction standard enforced: `active:scale-[0.98] transition-all duration-150`.
- **Build Evidence:** `npm --prefix frontend run build` passed with exit code 0.

### [x] T08: Hide Unsupported Media Layers
- **Verified Files:** `frontend/src/captions/CaptionStudio.tsx`.
- **Implementation Highlights:** Non-functional background music import, voiceover record, and sound effects buttons gated behind `ENABLE_EXPERIMENTAL_AUDIO_LAYERS = false`, eliminating non-functional export paths while preserving functional volume and mute controls.
- **Test Evidence:** All 13 frontend test suites passed in `npm --prefix frontend test`.

### [x] T03: Subtitles Consumer Remapping (Python ASS, Web Overlay & Android Media3)
- **Verified Files:**
  - `dispatch/captions/exporter.py` & `dispatch/captions/__init__.py`: Implemented `remap_caption_track_to_timeline(track, spans, clip_start)` using `intersect_source_interval`.
  - `dispatch/video_engine/renderer.py`: Multi-segment clips and silence-cut intervals automatically remap caption tracks to presentation time prior to FFmpeg ASS burn-in.
  - `dispatch/web/app.py`: `/api/clips/{clip_id}/captions` save endpoint remaps output ASS to match multi-piece EDL.
  - `frontend/src/captions/timelineEngine.ts`: Implemented `remapCaptionTrackToTimeline` for browser presentation.
  - `frontend/src/screens/ClipsScreen.tsx`: Wired `presentationCaptionTrack` to `InteractiveCaptionCanvas` and `DispatchCaptionRenderer` so preview playback across cuts remains 100% synchronized with the video while preserving authentic source coordinates for timeline editing.
  - `android/app/src/main/java/com/resolvia/dispatch/renderer/AndroidClipRenderer.kt`: Implemented `remapOverlayGroupsToTimeline(groups, segmentSpans, clipStartSec)`; Media3 Transformer renders frame-accurate subtitles matching concatenated video segments.
- **Test Evidence:**
  - `tests/test_subtitle_edl_remapping.py`: 4/4 tests passed (multi-segment cut drops deleted words and shifts surviving words, ASS export dialogue times never exceed effective duration, single segment identity mapping, and 3-piece 2-gap mapping).
  - `AndroidClipRendererTest.kt`: `remapOverlayGroupsToTimeline_multiSegment_shiftsSurvivingWordsAndDropsGaps` passed in `:app:testDebugUnitTest`.
  - Full Python regression test suite: 34/34 tests passed.
  - Frontend test suite: All 13 suites passed in `node frontend/run_tests.js`.
  - Frontend build: `npm run build` (`tsc --noEmit && vite build`) succeeded in 17.5s.


---

## 4. Release & Distribution Deployment Verification (Build 98 / v1.7.30-alpha)

### [x] Production Release & In-App / Landing Page Distribution
- **Version Governance Monotonicity:**
  - Bumped to `v1.7.30-alpha` (`versionCode = 98`) across `android/app/build.gradle.kts`, `frontend/package.json`, `site/update.json`, `site/index.html`, `README.md`, and `dispatch-landing-page/src/data/site.ts`.
- **Release Verification & Keystore Identity:**
  - Signed release APK built via `scripts/prepare_release.ps1` with trusted certificate SHA-256 ending in `...3804`.
  - APK size: `26,594,331` bytes.
  - APK SHA-256: `d78f5cdeb7cabc0dcf394c7b6df508e3755f063c1977eceebf00ad5cf7b88b76`.
- **GitHub Release Created & Published:**
  - Tag: `v1.7.30-alpha`.
  - Assets attached: `Dispatch.apk` (26,594,331 bytes), `update.json`.
  - Release URL: `https://github.com/Resolviaai/Dispatch/releases/tag/v1.7.30-alpha`.
- **GitHub Pages Deployed (`gh-pages` branch):**
  - Executed `scripts/deploy_gh_pages.ps1 -Tag v1.7.30-alpha`.
  - Manifest URL verified live: `https://resolviaai.github.io/Dispatch/update.json` (returns HTTP 200, versionCode 98).
  - Landing page APK download verified live: `https://resolviaai.github.io/Dispatch/Dispatch.apk` (returns HTTP 200, Content-Length 26594331).
  - Landing page UI updated: `https://resolviaai.github.io/Dispatch/` features direct 1-tap download button for `Dispatch v1.7.30-alpha (Build 98)` alongside GitHub release link.
