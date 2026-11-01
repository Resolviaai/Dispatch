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
