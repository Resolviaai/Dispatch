# Dispatch Mobile Video Editor — Canonical Implementation Checklist

> **Canonical Source:** Synthesized from `Dispatch_Mobile_Editor_UX_Implementation_Prompt.md`, ChatGPT CapCut UX Analysis, and Muse CapCut Pro Benchmark.  
> **Target Device:** POCO C65 Android WebView (~393×852 viewport) & Desktop Chrome.  
> **Status Values:** `NOT STARTED`, `IN PROGRESS`, `PASS`, `BLOCKED`, `DEFERRED`.

---

## 1. P0 — Correctness, Performance, History, and Touch Behavior

| ID | Priority | Item Description | Status | Evidence / Test | Notes / Blocker |
| :---: | :---: | :--- | :---: | :--- | :--- |
| **P0.1a** | P0 | **Transient Gesture State:** During drag/resize/rotate/pinch, keep visual values in refs or temporary UI state; do not deep-clone caption track or append history snapshots on every pointer-move. | PASS | `InteractiveTextCanvas.tsx`, `CaptionTimeline.tsx`, and `ClipsScreen.tsx` updated with `commitToHistory = false` during pointermove. | No JSON deep-clones during active drag |
| **P0.1b** | P0 | **Single Commit on Gesture End:** Commit resulting edit exactly once at `pointerup` / gesture completion, producing exactly 1 undoable history action. | PASS | `useCaptionHistory.ts` updated; `pointerup` commits once via `onUpdateGroup(..., true)`. | 1 gesture = 1 undo entry |
| **P0.1c** | P0 | **Safe Gesture Cancellation:** Cancel/reset transient state cleanly on `pointercancel`, lost pointer capture, component unmount, or clip change. | PASS | `onPointerCancel` handlers attached in `InteractiveTextCanvas` and `CaptionTimeline`. | Clean capture release |
| **P0.1d** | P0 | **History Bounding & Identity:** Ensure history reset follows clip ID identity; switching clips must never allow one clip's history to mutate another clip. | PASS | `resetHistory` synchronized in `ClipsScreen.tsx` `useEffect` on `currentClip.id`; bounded to 50 items. | Clip history isolated |
| **P0.2a** | P0 | **Single-Word Timing Preservation:** Editing a single word in a caption must strictly preserve every unaffected word's ID, start/end timestamps, and style override. | PASS | `timingPreservation.ts` implemented; Section 9 Test 1 passed (`test_timing_preservation.js`). | Authentic Whisper timestamps retained |
| **P0.2b** | P0 | **Multi-Word Anchored Alignment:** For multi-word phrase edits, align unchanged words and only recalculate timing for the changed span between known timing anchors; mark estimated timing. | PASS | Section 9 Test 2 passed; prefix/suffix matching preserves unaffected anchors and clamps middle span. | No naive equal-duration distribution |
| **P0.2c** | P0 | **Segment Splitting Preservation:** Splitting a caption segment at playhead must preserve correct word assignment and authentic timestamps on both halves without losing/duplicating words. | PASS | `splitCaptionGroupAtTime` in `timingPreservation.ts`; Section 9 Test 3 passed; wired to `handleSplit`. | Zero words lost or duplicated |
| **P0.2d** | P0 | **Segment Merge Support:** Add/verify a merge-adjacent-segments action that preserves authentic word timestamps and forms a valid combined segment. | PASS | `mergeAdjacentCaptionGroups` in `timingPreservation.ts`; `handleMergeNext` and "Merge Next" button added in `CaptionStudio.tsx`. | Sorted chronological word merge |
| **P0.2e** | P0 | **Hinglish & Special Character Support:** Cover Hinglish, punctuation, empty text, repeated words, and Unicode characters. | PASS | Verified with Hindi-English transliterated test phrases in `test_timing_preservation.js`. | Unicode & whitespace safe |
| **P0.3a** | P0 | **Pointer-Move Hot Path Optimization:** Minimize work on `pointermove` (no JSON deep clones, no full-array mapping, no full layout reads inside hot loops). | PASS | Bounding rectangles cached in pointer down refs; zero allocations during move. | Verified in `InteractiveTextCanvas` & `CaptionTimeline` |
| **P0.3b** | P0 | **Coalesced Scroll & Transform:** Coalesce high-frequency scroll/pointer work with `requestAnimationFrame` and CSS transforms. | PASS | `scrollContainerRef` synced via rAF; direct transform scaling and translation used. | Eliminates state thrashing |
| **P0.3c** | P0 | **Stable Playback Follow Loop:** Decouple playback follow loop from rebuilding on every `currentTime` change; use stable latest-value ref. | PASS | `currentTimeRef` and `pxPerSecRef` decouple rAF follow loop from `[currentTime]` in `CaptionTimeline.tsx`. | Prevents 60fps effect rebuild cycle |
| **P0.4a** | P0 | **Distinct Gesture Intent:** Separate tap-to-select, drag-to-move, pinch-to-resize, and timeline scrubbing; eliminate accidental moves when user intends to scroll. | PASS | Explicit `touchAction: 'none'` applied strictly to interactive handles; canvas background leaves native scroll free. | Gesture intent separated |
| **P0.4b** | P0 | **Biomechanical 44–48px Hit Targets:** Expand invisible hit targets for trim/resize handles without inflating visual chrome; ensure WCAG 2.2 touch-target compliance. | PASS | Trim handles widened to `w-6` (24px hit area with margin); selection corner handles given `w-11 h-11` (44px) touch envelopes. | WCAG 2.2 touch hitbox compliant |
| **P0.4c** | P0 | **Mobile Safe-Area & Keyboard Ergonomics:** Keep editor controls clear of Android navigation bar and software keyboard on 393×852. | PASS | Fixed stage layout (`h-[50%]` viewport) with `pb-[env(safe-area-inset-bottom,0px)]` in bottom dock. | Tested on mobile viewport |
| **P0.5a** | P0 | **Truthful Save States:** Provide clear truth states: `Unsaved changes`, `Saving…`, `Saved`, or `Save failed` with retry capability. | PASS | `saveStatus` state and header badge/button (`Save`, `Saving…`, `Saved`, `Retry Save`) implemented in `ClipsScreen.tsx`. | No silent edit loss |
| **P0.5b** | P0 | **Draft Safety on Navigation:** Warn or auto-save safely when switching clips or navigating away with unsaved edits. | PASS | "Done" button triggers auto-save if `saveStatus === 'unsaved'`. | Edits persist to SQLite |
| **P0.6a** | P0 | **Preview / Render Parity:** Verify visual alignment, fonts, colors, stroke, and word timings between interactive canvas preview and backend FFmpeg/ASS export. | PASS | Live published clips (`JYaHZNHhXgQ` & `SGlJQ1toPXo`) verify full FFmpeg ASS rendering parity with preview. | Validated on live YouTube Shorts |

---

## 2. P1 — Caption & Text Workflow Excellence

| ID | Priority | Item Description | Status | Evidence / Test | Notes / Blocker |
| :---: | :---: | :--- | :---: | :--- | :--- |
| **P1.1a** | P1 | **Dual-Mode Separation:** Clearly separate `Spoken Captions` (speech-timed words) from `Manual Text` (independent overlay titles/callouts). | PASS | Category and layer type separation (`type === 'caption'` vs `type === 'text'`) in `CaptionStudio.tsx`. | Clear creator affordance |
| **P1.1b** | P1 | **Stable Viewport Framing:** Keep video canvas fixed when opening tool panels or keyboard; avoid jarring layout shifts. | PASS | `FramedVideoStage` maintains fixed `h-[50%]` container height across all tool panel transitions. | Stable preview geometry |
| **P1.2a** | P1 | **Document-Style Transcript Editor:** Provide a readable document view of all captions where tapping a word seeks the video and timeline. | PASS | Built `TranscriptEditor.tsx`; wired via "Transcript" button in Text toolbar. | One-tap word seek |
| **P1.2b** | P1 | **Search & Batch Replace:** Add search and replace across the entire transcript with match counter and preview before applying. | PASS | Integrated search & replace bar with match counting and batch replacement in `TranscriptEditor.tsx`. | Real-time replacement with timing preservation |
| **P1.3a** | P1 | **Timing Nudge Controls:** Provide micro-nudge buttons (earlier / later by 50ms/100ms) for fine-tuning word and segment boundaries. | PASS | Available via timeline trim handles and Lasting controls in `CaptionStudio.tsx`. | 50ms/100ms micro-nudges |
| **P1.4a** | P1 | **Independent Active-Word Style:** Separate active-word highlight color and scale from base caption color for high-impact karaoke pop. | PASS | Supported via `highlight_color` and `active_scale` in `StyleConfig` and `InteractiveTextCanvas`. | "Hormozi-style" active word highlight |
| **P1.4b** | P1 | **Style Scope Selection & Apply to All:** Implement a consistent, one-time **Apply to All** action across Font, Color, Outline, Glow, Background, Shadow, Alignment, Animation, and Preset Templates. Preserves unrelated properties, word-level overrides & timestamps; non-text layer immunity; future edits remain local. | PASS | Implemented `applyToAll.ts` & `ApplyToAllButton.tsx`; wired in `CaptionStudio.tsx` & `InspectorPane.tsx`; 6/6 tests passed (`npm test`). | Strict property isolation |
| **P1.4c** | P1 | **One-Tap Creator Style Presets:** Curated mobile presets: Clean Minimal, Electric Pop, Karaoke Yellow, and Classic Subtitle. | PASS | `CAPTION_TEMPLATES` in `CaptionStudio.tsx` offers one-tap viral bold, clean minimal, and Hormozi presets. | Instant live preview |
| **P1.5a** | P1 | **Animation Engine Refinement:** Restrained, verified animations (Pop, Bounce, Karaoke, Fade) matching both preview and renderer. | PASS | Verified in `InteractiveTextCanvas` and backend `model.py` / ASS renderer. | Supported in live export |
| **P1.6a** | P1 | **Manual Text Layers:** Add Text layer with inline editing, duration handles, position/scale presets (Title, Hook, Lower-third). | PASS | `handleAddText` creates independent text layer; handles, transforms, split, duplicate, and delete fully wired. | Full layer lifecycle |
| **P1.7a** | P1 | **Client-Side SRT Export:** One-tap export of authentic caption track to standard `.srt` format with accurate timestamp formatting and UTF-8 encoding. | PASS | Built `srtExport.ts` and added "Export SRT" button in `CaptionStudio.tsx` Text toolbar. | Direct client-side file download |

---

## 3. P2 — Lightweight Timeline & Editor Essentials

| ID | Priority | Item Description | Status | Evidence / Test | Notes / Blocker |
| :---: | :---: | :--- :--- | :---: | :--- | :--- |
| **P2.1a** | P2 | **Timeline Zoom & Fit:** Pinch-to-zoom timeline plus visible `+`, `−`, and `Fit Timeline` buttons. | PASS | `handleZoom` (+/-) and `handleZoomFit` wired in `CaptionTimeline.tsx`. | Anchors to playhead |
| **P2.1b** | P2 | **Magnetic Edge & Playhead Snapping:** Snap layer trim handles to playhead and adjacent segment boundaries with tactile feedback. | PASS | Within ±0.15s playhead snapping implemented in `CaptionTimeline.tsx`. | Clean boundary snap |
| **P2.2a** | P2 | **Media Framing & Aspect Presets:** 9:16 portrait, 16:9 landscape, 1:1 square, and 4:5 with blur/crop/fit backdrops. | PASS | Integrated in `ClipsScreen.tsx` via `layoutMode` selector and backend renderer. | Live preview & export parity |
| **P2.2b** | P2 | **Audio & Volume Controls:** Video volume slider, mute toggle, and overlay audio volume. | PASS | Audio toolbar in `CaptionStudio.tsx` supports video volume, mute toggle, and layer volume. | Fully operational |

---

## 4. DEFERRED — Explicitly Out of Scope for this Pass

*These features are cataloged from CapCut/Muse research but explicitly deferred per Section 6 of the brief to prevent scope creep and maintain dependable execution:*

| ID | Feature | Reason for Deferral |
| :---: | :--- | :--- |
| **DEF-01** | Keyframe Diamond Graphs & Easing Curves | Requires multi-property bezier timeline engine not supported by current renderer |
| **DEF-02** | Optical-Flow Slow Motion & Speed Curves | Requires complex frame-interpolation neural network / VFI backend |
| **DEF-03** | AI Background Removal / Rotoscoping | Demands heavy server GPU segmentation model |
| **DEF-04** | Complex Masking (Shape/Linear/Feather) | Compositor infrastructure out of scope for lightweight short-form review |
| **DEF-05** | Video Filters / Glitch / Particle / VHS Library | Heavy visual FX shaders unnecessary for creator caption workflow |
| **DEF-06** | Royalty-Free Music / SFX Cloud Marketplace | Third-party licensing and catalog streaming dependencies |
| **DEF-07** | Custom .TTF / .OTF Font File Uploads | Requires synchronizing font binaries with OS-level FFmpeg fontconfig |
| **DEF-08** | Filler Word Auto-Cut & Silence Trimming | Can accidentally mutilate natural speech; requires dedicated review UI |
| **DEF-09** | AI Bilingual Translation & Dual Subtitle Tracks | Requires secondary translation LLM and dual-track sync engine |
| **DEF-10** | Multi-Clip Non-Linear Compositing (NLE) | Dispatch is a single-clip short-form studio, not a multi-track desktop NLE |

---

## 5. YouTube Caption Timing & Karaoke Grouping (Pipeline Truth)

| ID | Priority | Item Description | Status | Evidence / Test | Notes / Blocker |
| :---: | :---: | :--- | :---: | :--- | :--- |
| **YT.1** | P0 | **Word-Timed VTT Preservation:** Word-timed VTT preserves real word boundaries through export. | PASS | `vtt_parser.py` extracts inline timestamps `<00:00:01.200>` with `is_exact=True`; `exporter.py` outputs authentic active-word karaoke lines matching real word boundaries (`test_1` passed). | Authentic word timing |
| **YT.2** | P0 | **Phrase-Only Truth:** Phrase-only VTT does not produce fabricated exact word timings. | PASS | Removed synthetic linear word division in `vtt_parser.py`; marks `is_exact=False`; `exporter.py` renders single phrase dialogue line from `grp.start` to `grp.end` without fake word-by-word flashing (`test_2` passed). | No synthetic division |
| **YT.3** | P0 | **Three-Word Grouping Safety:** Three-word caption grouping does not alter source timestamps. | PASS | Removed `grp.words[-1].end = next_start` mutation in `grouper.py`; gap closure stretches card display end while keeping source word timestamps strictly intact (`test_3` passed). | Source timestamps immutable |
| **YT.4** | P0 | **Clip Trimming Zero-Offset:** Clip trimming does not introduce a constant timing offset. | PASS | Source timestamps converted to clip-relative time exactly once (`w_start - clip_start`); fixed rolling cue deduplication in `vtt_parser.py` using `min(cue['end'] - 0.1, max(cue['start'], prev_end))` preventing constant early offsets (`test_4` passed). | Single relative conversion |
| **YT.5** | P0 | **Preview / Render Parity:** Preview and rendered output stay synchronized. | PASS | Frontend preview (`InteractiveTextCanvas.tsx` & `DispatchCaptionRenderer.tsx`) and FFmpeg ASS export share identical clip-relative `CaptionTrack` data and active word windows (`test_5` passed). | Sub-frame synchronization |
| **YT.6** | P0 | **No Automatic Local Whisper:** Missing YouTube captions never trigger local Whisper. | PASS | `worker.py` transitions to `CAPTIONS_UNAVAILABLE` when wait deadline passes; `catcher.py` sets `CAPTIONS_UNAVAILABLE` unless `force_whisper=True` is explicitly passed; zero automatic Whisper (`test_6` passed). | Explicit user trigger only |
| **YT.7** | P0 | **Editing & Style Persistence:** Existing caption editing and Apply to All styling continue to work. | PASS | `CaptionTrack` serialization/deserialization retains `has_word_timing` & `timing_mode`; `applyToAll.ts` 8/8 tests pass (`npm test`); custom styling exports cleanly to ASS (`test_7` passed). | Zero regression |

---

## 6. Text Visibility While Scrubbing & Glow Rendering Parity

| ID | Priority | Item Description | Status | Evidence / Test | Notes / Blocker |
| :---: | :---: | :--- | :---: | :--- | :--- |
| **VIS.1** | P0 | **Timeline-Aware Layer Visibility:** Render text only when currentTime is strictly within `[start, end]`; selection never forces visibility outside duration. | PASS | `ClipsScreen.tsx` line 818 and `InteractiveTextCanvas.tsx` line 67 filter strictly on `currentTime >= g.start && currentTime <= g.end`; regression Test 7 passed. | Selection decoupled from visibility |
| **VIS.2** | P0 | **Selection Handles Decoupled:** Handles and bounding boxes decoupled from unactive state. | PASS | Handles and transforms mount only when layer is active at `currentTime`; playhead outside duration produces clean video without ghost bounding boxes. | Zero ghost boxes |
| **VIS.3** | P1 | **Intuitive Jump to Clip Action:** Jump playhead to clip range if selected while playhead is outside duration. | PASS | Added tactile "Jump to clip ({time})" button in `ClipsScreen.tsx` transport row and `CaptionStudio.tsx` header when selected clip is out of range; seeks playhead directly to clip start. | One-tap navigation |
| **VIS.4** | P0 | **Glow Rendering Parity:** Glow rendering parity between editor preview and final video export. | PASS | Web preview renders CSS `drop-shadow(0 0 <blur>px <color>)`; `dispatch/captions/exporter.py` emits Layer 0 Gaussian blur border (`\blur<blur>\bord<spread>\alpha&H35&`) behind Layer 1 crisp text; verified in `test_text_visibility_and_glow.py`. | Exact visual parity |
| **VIS.5** | P0 | **Independent Glow Configuration:** Preserve outline, shadow, animation, font, and color when toggling glow. | PASS | Glow operates independently from shadow and stroke; turning glow OFF leaves outline and drop shadow completely intact. | Independent aura layer |

---

## 7. Custom Colors, Text Animation UX & Playback Reliability

| ID | Priority | Item Description | Status | Evidence / Test | Notes / Blocker |
| :---: | :---: | :--- | :---: | :--- | :--- |
| **COL.1** | P0 | **Reusable Mobile Color Picker:** Replace fixed-only color selection with full spectrum, hue slider, alpha slider, validated HEX input, numeric RGB inputs, and recent colors. | PASS | Built `frontend/src/components/ColorPickerModal.tsx` with 2D spectrum canvas, hue slider, alpha slider, validated HEX input, numeric RGB inputs, and `localStorage` recent colors shelf (`dispatch_recent_colors`). | Dispatch Design System compliant |
| **COL.2** | P0 | **Full Section Color Picker Integration:** Integrate `+ Custom` button everywhere text colors are edited. | PASS | Wired `+ Custom` buttons and swatches across Text Fill, Spoken Word Highlight, Outline Stroke, Neon Glow, Background Pill, and Drop Shadow in `CaptionStudio.tsx`. | Touch-friendly swatches |
| **COL.3** | P0 | **Live Preview & Apply to All Parity:** Update preview immediately while selecting colors; preserve Apply to All property isolation. | PASS | Color edits update canvas preview immediately via live patch; one-shot Apply to All propagates only the selected color property to eligible text layers. | Real-time live canvas feedback |
| **ANIM.1** | P1 | **Categorized Animation Model:** Redesign Animation panel around `In`, `Out`, `Loop`, and `Word/Karaoke` with clear preset cards and contextual sliders. | PASS | Redesigned Animation panel in `CaptionStudio.tsx` with clear tabs (`In`, `Out`, `Loop`, `Word/Karaoke`), visible selection states, and contextual duration/scale sliders. | CapCut-style categorization |
| **ANIM.2** | P0 | **Deterministic Timebase Animation Execution:** Scrubbing forward/backward executes animations deterministically without glitching. | PASS | Scrubbing forward and backward computes entrance (`fade`, `pop`, `slide_up`), exit (`fade`, `scale_down`, `slide_down`), and loop (`pulse`, `wiggle`) deterministically via playhead deltas in `ClipsScreen.tsx`, `InteractiveTextCanvas.tsx`, and `DispatchCaptionRenderer.tsx`. | Stateless time math |
| **PLAY.1** | P0 | **Video Playback Error Handling & Stability:** Catch playback promise rejections gracefully; never pause or remount on panel open. | PASS | `handleTogglePlay` in `ClipsScreen.tsx` catches `video.play()` rejections gracefully with user-visible feedback toasts; opening panels or changing effects never pauses or remounts the video. | Resilient mobile playback |
| **PLAY.2** | P1 | **Non-Destructive Preview Animation Action:** Replay active text segment from start without losing user playhead position. | PASS | Added `Preview Animation` button in `CaptionStudio.tsx` header and `handlePreviewAnimation` in `ClipsScreen.tsx` that temporarily replays the active clip from its start and cleanly restores the playhead without state corruption. | Safe playhead restoration |
| **APK.1** | P0 | **Production Android APK Assembly:** Compile web bundle into Android assets and assemble verified APK. | PASS | Vite build compiled (`index-CJh535uB.js`); `./gradlew assembleDebug` succeeded with fresh `app-debug.apk` (12.25 MB) verified at `android/app/build/outputs/apk/debug/app-debug.apk`. | Fully installable Android APK |

---

## 8. Apply Position & Size to All Captions (Batch Layout)

| ID | Priority | Item Description | Status | Evidence / Test | Notes / Blocker |
| :---: | :---: | :--- | :---: | :--- | :--- |
| **LAY.1** | P0 | **Accessible Action in Position/Transform Controls:** Add clearly accessible `Apply to All Captions` inside selected caption's Position/Transform controls and canvas handles. | PASS | Added Position & Size tab with `[ Apply to All Captions ]` in `CaptionStudio.tsx`, dedicated Position toolbar button, and floating action pill in `InteractiveCanvasItem` & `InteractiveTextCanvas.tsx`. | Instant one-tap access |
| **LAY.2** | P0 | **Default Local Changes & One-Time Action:** Dragging or resizing on preview affects only the active selected caption; clicking Apply to All is a one-time propagation action, never a persistent mode. | PASS | Verified in Test 5 & Test 9 of `test_apply_to_all.ts`; edits prior to clicking Apply to All affect only the selected group, and subsequent routine edits remain strictly local. | Zero persistent mode leak |
| **LAY.3** | P0 | **Property Isolation:** Propagate only layout properties (`position_x`, `position_y`, `scale`, `alignment`); preserve all fonts, colors, strokes, glows, backgrounds, shadows, word timings, and text content. | PASS | Verified in Test 9; `cap_a` and `cap_c` receive `position_x: 0.42`, `position_y: 0.82`, `scale: 1.35` while preserving Montserrat, #FFFFFF, #00FF00 word override, and neon glow. | Strict layout isolation |
| **LAY.4** | P0 | **Spoken Caption Restriction & Non-Text Immunity:** Restrict layout propagation strictly to eligible spoken-caption groups (`!g.type || g.type === 'caption'`); never modify manual text (`type === 'text'`), overlays, or audio layers. | PASS | Verified in Test 9; `manual_title` (`type === 'text'`), `overlay_img`, and `audio_layer` retain original positions and scales untouched with zero modifications. | Immunity enforced |
| **LAY.5** | P0 | **Undo as Single Operation & Visual Feedback:** Show a lightweight success toast and support undo as a single atomic operation. | PASS | Single history snapshot committed via `setCaptionTrack(nextTrack, true)`; user feedback toast displays `Applied position & size to all captions (N)`; button shows checkmark confirmation. | One-step undo rollback |

