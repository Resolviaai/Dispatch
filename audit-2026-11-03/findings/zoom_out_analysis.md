# Dispatch Architectural Zoom-Out Audit (Skeptical Architect)

**Auditor Persona:** Skeptical Architect Goldfish (`egm-zoom-out`)  
**Target Scope:** High-altitude architecture, YAGNI violations, premature optimization, dead code, and specification bloat across `PROJECT.md`, `PROGRESS.md`, `design.md`, `dispatch/`, `cloud-worker/`, `android/`, and `frontend/`.  
**Date:** 2026-11-03  
**Verdict:** High Architectural Drift & Premature SaaSification. The project has expanded into multi-tenant SaaS infrastructure before verifying primary on-device capture and rendering on target hardware.

---

## 1. Executive Summary

Dispatch began as an ambitious, high-utility tool: an autonomous personal content engine converting raw smartphone video capture into polished, 9:16 social clips with AI highlights and burned-in animated captions.

However, a high-altitude architectural examination reveals severe scope sprawl:
1. **Architectural Triplication:** Three mutually redundant ingestion models coexist in code and documentation.
2. **Premature Multi-Tenant SaaSification:** Substantial engineering time has been invested in multi-user quota allocation (10 GB global ceiling, decimal byte allowances), user bans, and priority queue bumping, while core on-device recording on the physical POCO C65 remains unverified.
3. **Monolithic God-Files:** `dispatch/web/app.py` has expanded to 3,750 lines and `cloud-worker/src/index.ts` to 4,366 lines, entangling disparate domains (cryptography, UI delivery, database polling, video encoding).
4. **Specification Rot:** `PROJECT.md` describes a retired architecture (UDP beacons, LAN chunked streaming) and completely omits the Cloudflare Worker and R2 cloud tiers.

---

## 2. Key Findings & Skeptical Analysis

### Finding 1: Architectural Triplication & Vestigial Synchronization Paths
- **Severity:** 🔴 Blocker
- **Affected Scope:** `PROJECT.md#L32-L34`, `dispatch/sync/receiver.py`, `dispatch/youtube_inbox/`, `cloud-worker/src/index.ts`, `dispatch/cloud/sync.py`
- **Analysis:**
  The system currently houses three separate ingestion architectures:
  - **Path A (LAN Chunked Sync):** Documented in `PROJECT.md` as active. In reality, `dispatch/sync/receiver.py` (723 lines) returns HTTP 410 Gone for chunk uploads, yet `sync_router` remains mounted in `dispatch/web/app.py`.
  - **Path B (YouTube Cloud Ingestion):** Phone records segments, uploads unlisted MP4s to YouTube via `YouTubeDirectUploadWorker.kt`. PC runs `CatcherWorker` polling YouTube Data API, downloading via `yt-dlp`.
  - **Path C (Cloudflare Worker & R2 Control Plane):** Mobile or web clients request presigned R2 PUT URLs from Cloudflare Worker, upload directly to R2, and PC poller fetches jobs from the Worker queue.
  Running three distinct ingestion paradigms creates unnecessary cognitive overhead, fragmented error handling, and test debt.

### Finding 2: Premature SaaSification & Multi-Tenant Over-Engineering (YAGNI)
- **Severity:** 🔴 Blocker
- **Affected Scope:** `docs/ADMIN_OPERATIONS_DASHBOARD.md`, `cloud-worker/src/index.ts`, `frontend/src/AdminScreen.tsx`
- **Analysis:**
  A solo creator / single-user tool was prematurely expanded into a multi-tenant administration platform:
  - 10 GB global storage pool with decimal quota divisions.
  - User ban mechanisms and privilege escalation logic.
  - Priority queue nudging and administrative audit ledgers.
  All of this multi-tenant control plane was built before the primary hardware loop (recording on POCO C65 CameraX -> reliable transfer -> local Whisper transcription -> final 1080x1920 export) has been verified end-to-end on physical hardware.

### Finding 3: Specification Rot & Stale Contracts in `PROJECT.md`
- **Severity:** 🟡 Major
- **Affected Scope:** `PROJECT.md` (lines 7–105)
- **Analysis:**
  Approximately 80% of `PROJECT.md` is obsolete:
  - Describes UDP auto-discovery beacons on port 8765.
  - Details LAN chunked uploads (`POST /api/sync/upload/init`, `PATCH /api/sync/upload/chunk`).
  - References non-existent Kotlin classes (`LiveSyncManager`, `ResumableSyncWorker`).
  - Completely omits the Cloudflare Worker, Supabase authentication, and R2 storage layers.

### Finding 4: Monolithic Ledger Disease in `PROGRESS.md`
- **Severity:** 🟡 Major
- **Affected Scope:** `PROGRESS.md` (614 lines)
- **Analysis:**
  `PROGRESS.md` has become an unsearchable append-only transcript of micro-level operational history (scratch paths, transient process IDs, binary byte counts, commit hashes). It lacks a clear, current-state dashboard of what works, what is broken, and what remains pending.

### Finding 5: Monolithic God-Files in Core Backends
- **Severity:** 🟡 Major
- **Affected Scope:** `dispatch/web/app.py` (3,750 lines), `cloud-worker/src/index.ts` (4,366 lines)
- **Analysis:**
  Both primary backend entry points violate single-responsibility principles:
  - `dispatch/web/app.py`: Combines static file serving, token authentication, SQLite connection pooling, video encoding progress endpoints, and legacy sync routing.
  - `cloud-worker/src/index.ts`: Combines social OAuth token encryption, S3/R2 SigV4 generation, Supabase JWT validation, publishing queue draining, and administrative user management.

### Finding 6: Documentation Contradiction in `design.md`
- **Severity:** 🟢 Nit
- **Affected Scope:** `design.md#L79-L80` vs `PROGRESS.md`
- **Analysis:**
  `design.md` marks `AdminScreen.tsx` as unrouted dead code, while recent progress notes and PRs describe it as a fully deployed operations dashboard.

---

## 3. High-Altitude Remediation Directives

1. **Prune Path A (LAN Sync):** Unmount and delete `dispatch/sync/receiver.py`. Remove retired 410 endpoints.
2. **Align on One Ingestion Standard:** Either standardize on direct Cloudflare R2 presigned PUTs or local USB/LAN transfer. Stop relying on YouTube Data API for video transport.
3. **Freeze SaaS Expansion:** Halt further development of multi-tenant quota accounting and admin panels until physical POCO C65 recording and rendering pipelines are verified.
4. **Decompose God-Files:** Modularize `dispatch/web/app.py` and `cloud-worker/src/index.ts` into discrete feature routers and service classes.
5. **Re-baseline `PROJECT.md`:** Rewrite `PROJECT.md` to truthfully reflect the active tri-tier architecture.
