# Dispatch Master Architectural & Codebase Audit Report

**Audit Date:** 3 November 2026  
**Auditor Swarm:** Elephant-Goldfish Model (EGM) Multi-Agent Swarm  
**Participating Specialist Personas:**
- **Skeptical Architect** (`egm-zoom-out`): High-altitude simplicity, YAGNI, premature SaaSification, and scope bloat.
- **Hostile Design Critic** (`egm-design`): System scalability, memory bounds, quota economics, security boundaries, and failure modes.
- **Pragmatic Tech Lead** (`egm-implementation-readiness`): Execution roadblocks, test gaps, milestone ambiguities, and deployment gates.
- **Pedantic Quality Auditor** (`egm-codebase-details`): Line-by-line contract drift between specifications and physical implementation.
- **Master Synthesizer** (`egm-synthesizer`): Deduplication, severity triage, root-cause analysis, and actionable remediation roadmap.

**Scope of Audit:** `PROJECT.md`, `design.md`, `AGENTS.md`, `docs/`, `android/`, `frontend/`, `dispatch/`, `cloud-worker/`, and `tests/`.  
**Repository Policy Compliance:** Strictly read-only audit. **Zero functional source code was modified.** All findings, blueprints, and reports reside exclusively within `audit-2026-11-03/`.

---

## 1. Executive Summary

Dispatch is an ambitious, dual-use content automation system designed to eliminate the friction between capturing video on a smartphone and publishing vertical short-form clips (YouTube Shorts, Reels, TikTok, X, LinkedIn) with AI-selected highlights and animated word-level captions.

Our comprehensive multi-agent audit reveals that while the individual engineering subsystems demonstrate high sophistication (custom CameraX segmenters, faster-whisper int8 inference, ASS subtitle styling, Cloudflare edge workers), **the project suffers from severe architectural drift, premature multi-tenant SaaSification, and multiple critical reliability flaws that will cause immediate production outages under realistic creator workloads.**

### Key Verdicts:
1. **Critical Memory Ceiling Crash (Blocker):** The Cloudflare Worker control plane attempts to buffer entire video objects (up to 512 MB) in worker memory (`r2Object.arrayBuffer()`), which immediately exceeds Cloudflare's 128 MB RAM limit and terminates the publishing daemon.
2. **Fatal Ingestion Quota Trap (Blocker):** Using YouTube as the primary video transport mechanism burns 1,600 Data API units per 10-minute segment. A single 1-hour session consumes 9,600 of the 10,000 daily project units, triggering HTTP 403 quota exhaustion for the entire platform.
3. **Simulated Social Media Publishing (Blocker):** The LinkedIn and X (Twitter) publishing integrations do not upload any video bytes; they post text-only updates (`shareMediaCategory: "NONE"`).
4. **Severe Architectural Drift (Blocker):** ~80% of `PROJECT.md` specifies decommissioned LAN chunked sync and UDP beacons while claiming the mobile tier consists of "6 Jetpack Compose screens" (the real app runs a React SPA inside a transparent Android WebView).
5. **Premature Multi-Tenant SaaSification (Major):** Engineering capacity was diverted to building multi-user administration tools (10 GB storage pools, decimal quota tracking, user bans) while primary capture on the target POCO C65 physical device remains unverified end-to-end.

---

## 2. Risk Heat Map & Consolidated Scorecard

```
+---------------------------------------------------------------------------------------+
|                                SEVERITY BREAKDOWN                                     |
|  🔴 BLOCKERS (4)     |  🟡 MAJOR ARCHITECTURAL (10)  |  🟢 NITS & SPEC DRIFT (3)      |
+---------------------------------------------------------------------------------------+
| • Worker 128MB OOM   | • 4-hr YouTube ASR Delay      | • AGENTS.md #3B82F6 token drift|
| • YouTube Quota DOS  | • SQLite Lock Contention      | • AdminScreen unrouted tag     |
| • Fake Video Publishes| • Phantom Gemini Model Names  | • Frozen remediation ledger    |
| • Stale LAN Sync Spec | • 10 GB Storage Exhaustion    |                                |
|                      | • 150 SigV4 Crypto Storm / TTL |                                |
|                      | • MIUI Screen-Off Task Kill   |                                |
|                      | • Single AES OAuth Vault Key  |                                |
|                      | • Master Token Impersonation  |                                |
|                      | • Stalled Job DLQ Absence     |                                |
|                      | • Compose vs WebView Conflict |                                |
+---------------------------------------------------------------------------------------+
```

---

## 3. High-Priority Finding Matrix

| ID | Domain | Severity | Defect / Vulnerability | Physical Location | Root Cause |
|:---|:---|:---:|:---|:---|:---|
| **CRIT-01** | Cloud Edge | 🔴 **Blocker** | `arrayBuffer()` loads 512 MB video into 128 MB Worker RAM | `cloud-worker/src/index.ts:753` | Buffering video bytes in memory instead of streaming chunked or delegating |
| **CRIT-02** | Ingestion | 🔴 **Blocker** | 1hr recording session burns 9,600 / 10,000 YouTube daily quota units | `cloud-worker/src/index.ts:1704`, `android/.../YouTubeDirectUploadWorker.kt` | Using YouTube Data API as private video transport instead of direct R2 upload |
| **CRIT-03** | Publishing | 🔴 **Blocker** | LinkedIn & Twitter publishers send text-only posts; zero video uploaded | `cloud-worker/src/index.ts:988, 1060` | Incomplete mock implementations left in production publishing queue |
| **CRIT-04** | Architecture | 🔴 **Blocker** | `PROJECT.md` documents retired LAN sync contracts returning HTTP 410 | `PROJECT.md:66`, `dispatch/sync/receiver.py:691` | Architectural pivot to cloud occurred without updating institutional spec |
| **CRIT-05** | Pipeline | 🟡 **Major** | 4-hour delay polling YouTube captions before running 25s local Whisper | `dispatch/transcription/worker.py:201` | Cloud-first prioritization assuming instant YouTube cloud indexing |
| **CRIT-06** | Database | 🟡 **Major** | SQLite connection churn runs `PRAGMA WAL` on every concurrent thread | `dispatch/db.py:88`, `dispatch/main.py:155` | Lack of a persistent connection pool with serialized writer queue |
| **CRIT-07** | AI Engine | 🟡 **Major** | Non-existent Gemini models (`gemini-3.5-flash-lite`) crash on 429 retry | `dispatch/ai_clips/highlight_finder.py:23` | Speculative model naming without SDK catalog validation |
| **CRIT-08** | Storage | 🟡 **Major** | 10 GB global quota pool is exhausted after ~2 recording sessions | `docs/ADMIN_OPERATIONS_DASHBOARD.md:20` | Lack of automated downscale to 720p proxies and aggressive staging purge |
| **CRIT-09** | Cloud Auth | 🟡 **Major** | 150 SigV4 computations per `/clips` call; 10-min URL expiry breaks editor | `cloud-worker/src/index.ts:2818` | Upfront presigning of entire list payloads instead of on-demand generation |
| **CRIT-10** | Mobile | 🟡 **Major** | HyperOS/MIUI kills WorkManager background uploads when screen locks | `android/.../SegmenterEngine.kt:392` | Terminating ForegroundService immediately when recording stops |
| **CRIT-11** | Security | 🟡 **Major** | Single static symmetric AES key encrypts all OAuth tokens across all users | `cloud-worker/src/oauth.ts:49` | Missing per-user HKDF cryptographic domain separation |
| **CRIT-12** | Security | 🟡 **Major** | Master token `DISPATCH_WORKER_TOKEN` in plain `.env` permits user impersonation | `cloud-worker/src/index.ts:582` | Symmetric shared secret used instead of asymmetric public-key signatures |
| **CRIT-13** | Queue | 🟡 **Major** | Orphaned jobs remain in `STALLED_YOUTUBE_PROCESSING` indefinitely | `dispatch/youtube_inbox/`, `PROGRESS.md:210` | Missing lease timeout monitor and Dead Letter Queue (DLQ) state machine |
| **CRIT-14** | Mobile UI | 🟡 **Major** | Dead Compose screens (`RecordScreen.kt`) contradict live React WebView | `PROJECT.md:14`, `android/.../MainActivity.kt:254` | Abandoned Compose implementation kept in tree after React migration |

---

## 4. Root Cause Synthesis

The findings above stem from three core engineering dynamics:
1. **The Elephant-Goldfish Memory Decoupling:** As development velocity accelerated, tactical solutions (like wrapping the web studio in an Android WebView and moving video storage to R2) were committed rapidly. However, the foundational design and architecture specifications (`PROJECT.md`) were never reconciled. As a result, new subagents and contributors plan against an obsolete architectural mental model.
2. **Premature Multi-Tenant Scaling:** Before the core physical capture-to-render loop was fully battle-tested on the physical POCO C65 smartphone, engineering effort pivoted to building SaaS governance tools (quotas, admin screens, user bans).
3. **Transport Hijacking:** Attempting to use YouTube as a free unlisted video ingestion channel introduced severe API quota starvation (10,000 units/day) and processing latency (waiting 4 hours for captions) that negate the benefits of local PC processing.

---

## 5. Prioritized Remediation Roadmap

```
+----------------------------------------------------------------------------------------------------+
|                                    FOUR-PHASE REMEDIATION ROADMAP                                  |
+-----------------------------------+-----------------------------------+----------------------------+
| Phase 1: Stability & Security     | Phase 2: Ingestion & Pipeline     | Phase 3: Spec & UI Sync    |
| (Immediate Blockers)              | (Throughput & Quota)              | (Institutional Memory)     |
+-----------------------------------+-----------------------------------+----------------------------+
| 1. Stream video in Worker or      | 1. Route phone uploads directly   | 1. Synchronize PROJECT.md  |
|    delegate to direct R2 presign  |    to R2; bypass YouTube ingest   |    to Tri-Tier topology    |
| 2. Fix Gemini fallback models     | 2. Run faster-whisper immediately | 2. Align AGENTS.md tokens  |
|    to gemini-2.5-flash            |    (25s) with 90s timeout         |    to Design System v3     |
| 3. Retain ForegroundService       | 3. Add Dead Letter Queue (DLQ)    | 3. Archive dead Compose    |
|    during mobile background upload|    and 15-min lease reaper        |    code (RecordScreen.kt)  |
| 4. Derive per-user HKDF keys      | 4. Enforce 720p proxy transcode   | 4. Unmount retired LAN     |
|    for OAuth token vault          |    to preserve 10 GB cloud budget |    sync router in app.py   |
| 5. Implement real X & LinkedIn    | 5. Single-writer SQLite pool      | 5. Create CameraX test     |
|    video upload protocols         |    with PRAGMA WAL init-once      |    fixture for offline CI  |
+-----------------------------------+-----------------------------------+----------------------------+
```

---

## 6. Audit Deliverables Index

All detailed investigation artifacts and technical blueprints are preserved in this audit folder:

1. **Findings by Persona:**
   - [Architectural Zoom-Out Analysis](file:///c:/CODE/Dispatch/audit-2026-11-03/findings/zoom_out_analysis.md) (`findings/zoom_out_analysis.md`) — High-altitude architecture, YAGNI, and premature SaaSification critique.
   - [Implementation Readiness Audit](file:///c:/CODE/Dispatch/audit-2026-11-03/findings/implementation_readiness_audit.md) (`findings/implementation_readiness_audit.md`) — Blocked milestones, execution barriers, and DLQ requirements.
   - [Adversarial Design Critique](file:///c:/CODE/Dispatch/audit-2026-11-03/findings/design_critique.md) (`findings/design_critique.md`) — Worker OOM, quota exhaustion, SQLite lock churn, and security vulnerabilities.
   - [Codebase Quality & Contract Audit](file:///c:/CODE/Dispatch/audit-2026-11-03/findings/codebase_details_audit.md) (`findings/codebase_details_audit.md`) — Verified line-by-line contradictions between specifications and code.
2. **Drift Detection Report:**
   - [EGM Drift Report](file:///c:/CODE/Dispatch/audit-2026-11-03/2026-11-03_DRIFT_REPORT.md) (`2026-11-03_DRIFT_REPORT.md`) — Exhaustive catalogue of spec-vs-code drift.
3. **Architecture Blueprints & Component Contracts:**
   - [Clean Architecture Blueprints](file:///c:/CODE/Dispatch/audit-2026-11-03/blueprints/ARCHITECTURE_BLUEPRINTS.md) (`blueprints/ARCHITECTURE_BLUEPRINTS.md`) — Formal interface contracts, boundary invariants, and target clean topology.
