# Dispatch - Launch Day Master Tracking Document (1 November 2026)

> **Document Status:** Active & Canonical Master Tracker  
> **Date:** November 1, 2026  
> **Milestone:** Post-launch hardening — owner dictates the task list; every task is verified before it is closed.  
> **Protocol Rule:** This document is updated incrementally after **every single mini-task**. Any incoming AI agent or developer can resume work directly from this file without context loss.

---

## 1. Executive Summary & Objective

Dispatch is in production for the owner and early creators. This tracker carries the work that follows the 30 October launch: reliability fixes, UX polish, and any new capability the owner requests, executed with the same standard — root cause, no shortcuts, no regressions.

The owner supplies tasks one by one. Each task is added to §4 (backlog), broken down in §5, and logged in §7 when verified.

---

## 2. System State & Canonical Environment

| Component | Target / Specification | Current Live State |
| :--- | :--- | :--- |
| **Android Package** | `com.resolvia.dispatch` | Version `1.7.15` (`versionCode = 83`) |
| **Release Tag** | Monotonic SemVer Git Tag | `v1.7.15-alpha` (Commit `c4de00d`) |
| **Release Certificate** | Trusted keystore | SHA-256 fingerprint ends in `...3804` |
| **Release Distribution** | GitHub Releases & `gh-pages` | Hosted at `https://resolviaai.github.io/Dispatch/` |
| **PC Daemon API** | FastAPI / SQLite / Gemini Transcribe & Flash Lite / FFmpeg | `http://127.0.0.1:8000` |
| **Cloudflare Worker** | Cloud API & Edge Storage Gateway | `https://dispatch-cloud.resolviaai.workers.dev` (version `2fa28346…`) |
| **Supabase Cloud** | Auth, Profiles, Cloud DB | `https://aldzlbdxplqkcnyjhzmm.supabase.co` (migration `20261101000025` applied) |
| **Web Review Studio** | Vite + React + Tailwind v3 | Embedded in Android Assets & PC Review UI |

---

## 3. Operational Protocols & Collaboration Invariants

### 3.1 The Cross-Questioning & Verification Protocol (User-Agent Ground Rule)
When the user proposes a solution, asks to implement a feature, or reports a bug:
1. **Never blindly execute:** do not apply hasty patches or superficial workarounds.
2. **Deep file inspection:** read the actual source files and trace the complete lifecycle of data/state.
3. **Critical cross-examination:** analyse side effects, failure modes, race conditions, and architectural conflicts.
4. **Evaluate superior alternatives:** assess cleaner or more robust solutions; present comparisons.
5. **Acknowledge and align:** obtain alignment before destructive or high-impact architectural changes.

### 3.2 Continuous Incremental Persistence
- This document and the active conversation must be updated after **every completed mini-task**.
- If context resets or an agent switches, this file is the immediate ground truth.

### 3.3 Zero Unsolicited Process Spawning
- Do not run daemon loops, servers, or background tasks unsolicited.
- Keep the workspace clean; inspect logs rather than altering running states prematurely.

### 3.4 Release Governance
- Every Android release bumps `versionCode` monotonically and synchronizes manifests with `scripts/bump_version.ps1`.
- Every APK is verified with `scripts/prepare_release.ps1`, the trusted certificate `94f778…3804`, a GitHub Release, and a `gh-pages` mirror.

---

## 4. Prioritized Backlog & Problem Register

Tasks are added here by the owner, in the order they must be executed.

```
[ ] (awaiting owner task list)
```

Candidate issues already identified (not yet approved):
- **Clip floor after trim:** `MIN_CLIP_DURATION = 5.0` exists, but the Stage-2 word-snap trim can shorten a clip below it. Candidate fix: skip the trim when it would drop the clip under 5s (direct uploads already skip the trim entirely).
- **In-app updater on the phone:** reported "package appears to be invalid"; server files verified valid — suspected truncated browser download; needs device-side confirmation of the downloaded size.

---

## 5. Task Breakdown & Technical Context

### [TASK — placeholder]
- **User Requirement:** _(to be dictated)_
- **Root Cause & Scope:** _(to be filled after investigation)_
- **Key Files:** _(to be filled)_
- **Resolution & Implementation:** _(to be filled)_
- **Status:** Not started

---

## 6. Architectural & Design System Invariants

Every code change must adhere to the Dispatch UI/UX & Mobile Engineering Invariants (v3):

1. **The Single-Direction Law:** on mobile viewports every screen section moves along one axis (vertical card stack OR horizontal snap carousel). No multi-column grids on phones.
2. **Biomechanical 44px Hitboxes:** every interactive element ≥ 44×44px with ≥ 8px separation.
3. **4-Layer Elevation Architecture (60/30/10):**
   - Layer 0 (Canvas Root): `#161616` (`bg-canvas`, `bg-studio`)
   - Layer 1 (Cards): `#1C1C1C` with border `#2E2E2E` (`bg-surface-100`, `border-border`)
   - Layer 2 (Recessed Wells): `#232323` (`bg-surface-200`)
   - Layer 3 (Hero CTA): Electric Creator Blue `#2563EB`; semantic colours only for status.
4. **Law of the Single Hero CTA:** exactly one primary action per viewport; secondary actions stay neutral.
5. **Typographic Limit & Numeric Alignment:** max 4 sizes (18/14/12/10 px), 2 weights; numbers use `font-mono tabular-nums`.
6. **Zero OS Native Emojis:** vector icons only (`lucide-react`, 1.5–2.0px strokes).
7. **Concentric Geometry:** `R_inner = R_outer − padding`.
8. **No harsh shadows or neon gradients**; tactile `active:scale-[0.98]` feedback on every interactive surface.

---

## 7. Live Progress & Execution Log

| Timestamp (IST) | Sub-task / Action | Files Modified | Outcome & Verification |
| :--- | :--- | :--- | :--- |
| **2026-11-01 02:00** | Tracker created; baseline recorded | `docs/2026-11-01_LAUNCH_DAY_MASTER.md` | Baseline v1.7.15 / code 83 (Route C direct uploads, published-clip delete, server icon indicator). Standing by for the owner's task list. |

---

*End of Active Master Tracker. Next entry to be appended upon completion of the next task.*

---

## 8. Planned Feature — Segment Timeline (v1.8.0)

**Goal:** a non-destructive editing timeline (CapCut model): multiple kept ranges per clip, split at the playhead, select/delete/ripple, per-edge trim, undo/redo, continuous preview.

**Why:** a single `start_time`/`end_time` pair cannot represent "keep 0–12 s and 17–30 s, drop the pause". Today a Split creates two independent review clips instead.

### Interaction contract (this is the behaviour that must ship)

Owner-stated requirement, 1 Nov — CapCut semantics, not "one cut = one new clip":

1. **Split never creates a clip.** One card in the library, one row in `clips`, before and after any number of cuts. The row-split endpoint is retired, not kept in parallel — its only caller is the Studio (`ClipsScreen.handleSplitClip`).
2. **A cut is visible on the timeline.** Splitting once shows two pieces; splitting twice shows three, in order, as distinct blocks with a visible boundary at each cut.
3. **Every piece has its own two trim edges.** Trimming the right edge of piece 1 must not move piece 2, and trimming the left edge of piece 3 must not move piece 2.
4. **Deleting a middle piece closes the gap without joining.** A | B | C with B deleted becomes A | C: two separate pieces, adjacent, no blank between them, and A and C must not fuse into one range. They stay independently trimmable, which is why the representation is an ordered list of kept ranges rather than a merged union.
5. **A lone split changes nothing on export.** Two adjacent kept ranges render identically to the uncut range, so cutting is safe to do while thinking.
6. **Undo/redo steps through cut, trim and delete**, and the preview follows.

### Design

- **Data:** `clips.segments_json` — `[[start,end], …]` source-relative kept ranges; `segments_revision` for CAS. `NULL` = legacy single range (unchanged behavior).
- **API:** `PUT /api/clips/{id}/segments` with `{segments, base_revision}`. Validate: non-overlapping, sorted, inside source, each ≥ 0.1 s, total ≥ `MIN_CLIP_DURATION`. Split/delete/ripple are client-side array edits persisted once.
- **Render:** concatenate kept ranges with FFmpeg concat into the final MP4; thumbnail from the first kept frame; `range_revision`/`is_render_stale` unchanged.
- **Captions:** re-anchor word timings onto the concatenated timeline; drop words inside removed ranges; preserve word-level precision.
- **Player:** preview plays kept ranges continuously (seek across gaps); split uses the clip-relative playhead clock (fixed in 1.7.20).
- **Undo/redo:** extend the existing caption history to the segment array; one Save persists.
- **Compatibility:** legacy clips keep the single range; publishing still consumes the rendered output.

### Acceptance criteria
1. Split at the playhead cuts at that frame (≤1 frame error at 30 fps) in both source and rendered playback.
2. Split → split → select → delete → ripple on a 30 s clip with a 5 s pause yields a 25 s render.
3. A 2 s intermediate piece is valid; the 5 s minimum applies only to the saved clip total.
4. Undo restores the previous segment list and preview.
5. Legacy clips render and publish identically after migration.
6. **The library card count is unchanged by any number of splits, trims or deletes** — no split may insert a `clips` row. Asserted by a test that splits twice and counts rows.
7. **Delete-middle leaves two separate pieces with no gap**: `[[0,3],[6,29]]` after removing the middle of three, and trimming piece 2's left edge afterwards must not move piece 1's end.

### Tests
Segment math (split/delete/ripple/trim), concat render output duration, caption re-anchoring, CAS conflict, legacy fallback.

**Status:** Not started — spec ready.
