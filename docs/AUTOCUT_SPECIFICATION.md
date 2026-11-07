# Dispatch AutoCut (Speech Cleanup) Engineering Specification

> **Status:** Production Standard (v1.7.40 / versionCode 108)  
> **Target Viewport:** Android Native Web-Studio & Desktop Review Studio  
> **Source Modules:**  
> - Frontend Component: [`SpeechCleanupWorkspace.tsx`](file:///c:/CODE/Dispatch/frontend/src/captions/SpeechCleanupWorkspace.tsx)  
> - Mini-Filmstrip & Context Window: [`MiniFilmstrip.tsx`](file:///c:/CODE/Dispatch/frontend/src/captions/MiniFilmstrip.tsx)  
> - Direct AI Studio Engine: [`geminiSpeechCleanup.ts`](file:///c:/CODE/Dispatch/frontend/src/captions/geminiSpeechCleanup.ts)  
> - Timeline & Playback Authority: [`ClipsScreen.tsx`](file:///c:/CODE/Dispatch/frontend/src/screens/ClipsScreen.tsx)  
> - Timeline Math & Splitting: [`timelineEngine.ts`](file:///c:/CODE/Dispatch/frontend/src/captions/timelineEngine.ts)

---

## 1. Executive Summary & Purpose

The **AutoCut Speech Cleanup** engine in Dispatch enables creators to remove filler words, mechanical stutters, false starts, and dead air silences in short-form videos with a single tap.

Unlike traditional English-only regex rules that mistakenly cut meaningful words or sound choppy, Dispatch combines:
1. **Client-Side Gemini 3.5 Flash-Lite**: Directly prompted from the mobile device to Google AI Studio with deep understanding of **Hindi, Hinglish, and conversational English**.
2. **Deterministic Fallback & Hybrid Ensemble**: Catching all acoustic gaps, word-level repetitions, and hesitation grunts so no candidate is missed.
3. **Audition-First Context Preview**: Instant seamless jump-cut preview allowing the creator to hear the before/after before applying changes.
4. **Non-Destructive EDL Timeline Mutation**: Commits clean cut intervals into the multi-piece edit decision list (EDL) with instant undo/redo.

---

## 2. High-Altitude Architecture

```
+-----------------------------------------------------------------------------------+
|                            MOBILE DEVICE (DISPATCH)                                |
|                                                                                   |
|  [Word-Level Captions] ----+                                                      |
|                            |                                                      |
|                            v                                                      |
|               +-------------------------+                                         |
|               |  computeTranscriptSig   |                                         |
|               +------------+------------+                                         |
|                            |                                                      |
|                   +--------+--------+                                             |
|                   | Cache Hit?      |                                             |
|                   +--------+--------+                                             |
|                   YES      |       NO                                             |
|                    |       |                                                      |
|  [L1 Memory / L2 Storage]  |       +-------------------------------------------+  |
|          |                 |       | DIRECT PHONE-TO-GOOGLE AI STUDIO          |  |
|          |                 +-----> | Model: gemini-3.5-flash-lite (REST)       |  |
|          |                         | - Hindi / Hinglish semantic awareness     |  |
|          |                         | - Exhaustive full-duration audit          |  |
|          |                         +---------------------+---------------------+  |
|          |                                               |                        |
|          |                                               v                        |
|          |                         +-------------------------------------------+  |
|          |                         | HYBRID ENSEMBLE ENGINE                    |  |
|          |                         | - Overlap disambiguation (Gemini priority)|  |
|          |                         | - Non-overlapping candidates (REVIEW tab) |  |
|          |                         | - Acoustic pause gap detection (>= 2.5s)  |  |
|          |                         +---------------------+---------------------+  |
|          |                                               |                        |
|          +-----------------+-----------------------------+                        |
|                            |                                                      |
|                            v                                                      |
|             +-------------------------------+                                     |
|             |  SpeechCleanupWorkspace (UI)  |                                     |
|             |  - 60fps Memoized Rows        |                                     |
|             |  - 44px Biomechanical Touch   |                                     |
|             |  - Equal 50/50 Footer Action  |                                     |
|             +---------------+---------------+                                     |
|                             |                                                     |
|         +-------------------+-------------------+                                 |
|         |                                       |                                 |
|         v                                       v                                 |
|  [Context Jump-Cut Audition]             [Apply Auto-Cut (EDL)]                   |
|  - High-frequency RAF check              - Non-destructive fragment               |
|  - Universal timebase support            - Reversible undo stack                  |
+-----------------------------------------------------------------------------------+
```

---

## 3. Gemini Flash-Lite Hindi/Hinglish Prompt Architecture

### 3.1 Direct Client-to-API Topology
Requests are executed directly from the phone via `fetch('https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent')` using the user's stored Google AI Studio key.
* **Zero PC dependency**: Works even when the creator's PC is turned off, sleep mode, or offline.
* **Model Hierarchy**: `gemini-3.5-flash-lite` $\rightarrow$ fallback `gemini-3.1-flash-lite` $\rightarrow$ `gemini-2.5-flash-lite`.

### 3.2 Exhaustive Multi-Pass Instruction System
```typescript
const systemInstruction = `You are an expert speech cleanup editor specializing in short-form video editing for Indian creators speaking Hindi, Hinglish, and English.
You are given a chronological word-level transcript with start and end timestamps in seconds.

EXHAUSTIVE AUDIT MANDATE:
- You MUST scan the entire transcript chronologically from start (${clipStart.toFixed(2)}s) to end (${clipEnd.toFixed(2)}s).
- Do NOT stop after 1 or 2 examples. Identify EVERY instance throughout the ENTIRE duration.
- Flag ALL occurrences of:
  1. Conversational fillers & hesitation sounds:
     - English / Hinglish: 'like', 'um', 'uh', 'umm', 'uhh', 'you know', 'basically', 'actually', 'sort of', 'literally'.
     - Hindi stalling markers: 'matlab', 'haan toh', 'samjho', 'arre', 'yaar' when used as an empty hesitation filler before starting a phrase.
  2. Mechanical stutters & false starts:
     - Repeated 1-3 words (e.g., "phir tut... phir tut jaati", "woh woh", "I I think") -> propose cutting the redundant initial attempt.
  3. Stalling pauses and abandoned phrase attempts.

CLASSIFICATION & PRESERVATION RULES:
- Unambiguous hesitation sounds ('um', 'uh', pure grunts, repeated stutter syllables) -> "decision": "AUTO-REMOVE".
- Ambiguous discourse markers ('like', 'matlab', 'so', 'basically') -> "decision": "REVIEW" so the creator can audition and decide.
- Strictly PRESERVE legitimate meaningful vocabulary:
  * 'matlab' when genuinely asking or giving meaning/definition ('iska matlab kya hai', 'mere kehne ka matlab')
  * 'achha' when an adjective or compliment ('achha kaam', 'bohot achha')
  * 'like' when used as a verb ('I like this') or comparison ('looks like', 'feels like')
  * 'toh' when used as a grammatical connector ('agar ... toh')
- Strictly PRESERVE deliberate rhetorical pauses and dramatic emphasis.
`;
```

---

## 4. The Hybrid Ensemble & Deduplication Engine

To solve the dual failures of "AI under-reporting" and "heuristic false-positives", Dispatch implements an **ensemble union**:

1. **Deterministic Run**:
   - `generateLocalAutoCutProposals()` deterministically extracts all repeated words within 2.5 seconds (`clean1 === clean2`), hesitation vocables (`um`, `uh`), and filler tokens (`like`).
2. **Gemini Run**:
   - Evaluates full context, sentence grammar, and cultural semantics.
3. **Union Merge**:
   - For any time interval where Gemini detected a cut, **Gemini's decision rules authority**.
   - Any candidate detected by local heuristics that **did not overlap** with a Gemini proposal is **retained under the REVIEW category**.
   - Result: 100% of stutter candidates and conversational markers are surfaced without omissions.

---

## 5. Two-Tier In-Memory & Persistent Caching

AutoCut analysis runs at **0&nbsp;ms** on subsequent drawer openings:

1. **Transcript Signature (`computeTranscriptSignature`)**:
   $$\text{sig} = \text{clipId} : \text{clipStart}-\text{clipEnd} : \text{wordCount} : \text{firstWordStart}-\text{lastWordEnd}$$
   - Any manual edit to caption text or trim adjustment immediately invalidates the signature, triggering fresh analysis.
2. **Tier 1 (L1 In-Memory)**:
   - `autoCutCacheRef: Map<string, { signature: string; proposals: AutoCutProposal[] }>`
   - Provides instant 0ms restoration when switching between timeline view and AutoCut drawer.
3. **Tier 2 (L2 LocalStorage)**:
   - Key: `dispatch_autocut_<clip_id>`
   - Preserves proposals and custom user Cut/Keep toggles across app backgrounding, tab switching, and cold app restarts.
4. **Manual Re-Analysis**:
   - A dedicated "Re-analyze" action in the filter bar bypasses both L1 and L2 caches to fetch fresh Gemini recommendations.

---

## 6. Contextual Jump-Cut Audition Engine

Creators must never be forced to guess how a jump-cut sounds.

1. **Context Window Calculation (`computeContextWindow`)**:
   - For a cut at $[T_{\text{cut\_start}}, T_{\text{cut\_end}}]$, plays a 2.5s pre-roll and 2.5s post-roll:
     $$T_{\text{win\_start}} = \max(0, T_{\text{cut\_start}} - 2.5)$$
     $$T_{\text{win\_end}} = \min(T_{\text{total}}, T_{\text{cut\_end}} + 2.5)$$
2. **Seamless Frame-Accurate Jump (`checkJump`)**:
   - High-frequency `requestAnimationFrame` loop monitors `video.currentTime`.
   - If the proposal is set to `Cut`: when `currentTime >= cutStart - 0.03s`, the player jumps instantaneously to `cutEnd` without audio pops.
   - If the proposal is set to `Keep`: plays straight through with no jump, allowing instant A/B auditory comparison.
3. **Universal Timebase Independence**:
   - Detects whether the active video element is running against raw source timestamps or trimmed edit-window coordinates:
     $$t_{\text{offset}} = (\text{isSourceTimebase} \land \text{duration} \ge \text{clipEnd} - 1.0) \ ? \ 0 : \text{clipStart}$$
   - Safely subtracts $t_{\text{offset}}$ from all seek targets and preview boundaries, preventing out-of-bounds seeks on rendered clips.

---

## 7. EDL Timeline Non-Destructive Commit

When the creator taps **Apply Auto-Cut**:
1. Merges overlapping cut ranges to prevent negative-duration segments.
2. Slices the active timeline segments using `timelineEngine.ts`.
3. Validates against whole-clip deletion: if all segments would be removed, the operation safely aborts with user feedback.
4. Commits to project state with action label `auto_cut`.
5. Pushes previous timeline state to undo history (`canUndo = true`).

---

## 8. Mobile Ergonomics & UI/UX Standards

Per repo invariants (`AGENTS.md`):
* **Equal 50/50 CSS Grid Footer**:
  `grid grid-cols-2 gap-2.5` ensures "Cancel" and "Apply Auto-Cut" have identical width, height (`h-11 min-h-[44px]`), and corner radiuses (`rounded-xl`).
* **Law of Single Hero CTA**:
  "Apply Auto-Cut" uses `bg-primary text-white shadow-hero-glow`, while "Cancel" remains neutral recessed `bg-surface-2 border-border text-text-main`.
* **Biomechanical Touch Targets**:
  All card headers and interactive buttons maintain minimum $44\text{px} \times 44\text{px}$ touch patches.
* **60fps Playback Smoothness**:
  Individual rows are wrapped in `React.memo` with a custom comparator that suppresses all 30fps/60fps `timeupdate` re-renders for collapsed cards.
* **Empty Area Dismissal**:
  Tapping outside the video frame on the letterbox/pillarbox areas instantly closes the drawer and stops preview playback.
