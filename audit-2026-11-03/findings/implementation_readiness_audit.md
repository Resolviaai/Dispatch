# Implementation Readiness Audit (Pragmatic Tech Lead)

**Auditor Persona:** Pragmatic Tech Lead Goldfish (`egm-implementation-readiness`)  
**Target Scope:** `PROGRESS.md`, `PROJECT.md`, `DISPATCH_REMEDIATION_LEDGER.md`, `docs/architecture/cloud-migration-plan.md`, `tests/`  
**Date:** 2026-11-03  
**Verdict:** Implementation Stalled / Blocked by Unverified Hardware Dependencies and Stale Milestone Definitions.

---

## 1. Executive Summary

The implementation roadmap for Dispatch is currently impaired by:
1. Milestone descriptions in `PROJECT.md` that reflect a decommissioned 2026-Q1 architecture rather than the current cloud hybrid model.
2. Incomplete executable specifications for Phase E (PC <-> Cloud Sync Layer).
3. Critical failure modes in YouTube authentication via `yt-dlp` for private clips.
4. Hard execution blockers: physical POCO C65 hardware gating automated release sign-off without mock CameraX instrumentation.
5. Indefinite stalls on orphaned jobs with no automated Dead Letter Queue (DLQ) timeout.

---

## 2. Key Findings & Detailed Analysis

### Finding 1: Architectural Drift & Obsolete Milestones in `PROJECT.md`
- **Severity:** 🔴 Blocker
- **Affected Scope:** `PROJECT.md` (Milestones M1–M5, lines 55–64)
- **Analysis:**
  `PROJECT.md` defines Milestones M1 through M5 around UDP auto-discovery beacons on port 8765, LAN chunked streaming with SHA-256 rolling verification, and device pairing configs.
  Because LAN sync was abandoned in favor of YouTube and Cloudflare R2 ingestion, these milestone definitions provide zero guidance or verification criteria for current active components (Cloudflare Worker, Supabase auth, R2 presigned URLs, remote job poller).

### Finding 2: Lack of Executable Specification for Phase E (PC <-> Cloud Sync Layer)
- **Severity:** 🔴 Blocker
- **Affected Scope:** `docs/architecture/cloud-migration-plan.md`, `dispatch/cloud/sync.py`
- **Analysis:**
  Phase E (PC Sync) is marked as the critical next step in the cloud migration plan, but the specification lacks:
  - Exact SQLite schema for `cloud_sync_queue`.
  - Polling interval, backoff schedule, and thread lifecycle in `dispatch/main.py`.
  - Bi-directional conflict resolution when edits occur simultaneously in the local SQLite database and Supabase Postgres.
  - Concrete specifications for pulling remote approval states.

### Finding 3: `yt-dlp` Authentication Deadlock on Private Remote Videos
- **Severity:** 🔴 Blocker
- **Affected Scope:** `cloud-worker/src/index.ts#L1700-L1750`, `dispatch/youtube_inbox/catcher.py`
- **Analysis:**
  When a user submits a YouTube video via `/jobs/submit`, `yt-dlp` on the PC daemon fails if the video privacy status is set to `private` because `yt-dlp` cannot authenticate via OAuth Bearer tokens without exported browser cookies.
  The system currently lacks:
  - Strict validation enforcing `unlisted` or `public` status during submission.
  - Fail-fast error reporting in `catcher.py` before launching lengthy retry cycles.

### Finding 4: Physical POCO C65 Hardware Gate Blocks Autonomous Release Verification
- **Severity:** 🔴 Blocker
- **Affected Scope:** `scripts/prepare_release.ps1`, `android/app/build.gradle.kts`
- **Analysis:**
  The project invariants require verifying CameraX 1080p recording on physical POCO C65 hardware before release. Because no connected hardware is available in CI/CD or local test environments, and no mock CameraX instrumentation exists, the deployment pipeline is indefinitely blocked from automated verification.

### Finding 5: Undefined Architecture for Route A (Cross-Segment Continuity)
- **Severity:** 🟡 Major
- **Affected Scope:** `PROGRESS.md#L450-L480`, `dispatch/ai_clips/highlight_finder.py`
- **Analysis:**
  The roadmap outlines "Route A" for cross-segment continuity (joining highlights spanning across 10-minute segment boundaries). However, there is no mathematical or algorithmic specification for transcript boundary reconciliation, audio crossfading, or ffmpeg filter graphs required to execute seamless segment stitching.

### Finding 6: Stalled YouTube Processing Jobs Lack Automated Timeout & DLQ
- **Severity:** 🟡 Major
- **Affected Scope:** `dispatch/youtube_inbox/`, `PROGRESS.md#L210`
- **Analysis:**
  Job `AHaEgBYcHoo`-session part 1 has been stuck at `STALLED_YOUTUBE_PROCESSING` since October 2026. The pipeline has no automated timeout monitor, no retry cap that transitions poisoned jobs to a Dead Letter Queue (DLQ), and no administrative alerting for stranded tasks.

### Finding 7: R2 Presigned PUT Expiry on Mobile Links & Storage Quota Bypass
- **Severity:** 🟡 Major
- **Affected Scope:** `cloud-worker/src/index.ts#L2680-L2720`
- **Analysis:**
  Presigned R2 upload URLs expire after 600 seconds (10 minutes). On slow mobile 4G/LTE cellular uplinks, uploading a 500 MB video segment often exceeds 10 minutes, resulting in HTTP 403 Forbidden mid-stream. Furthermore, R2 presigned URLs do not strictly enforce `Content-Length` headers, allowing clients to initiate oversized uploads that consume cloud storage until cleanup janitors trigger.

### Finding 8: Dead Native Jetpack Compose Views vs Live React WebView
- **Severity:** 🟡 Major
- **Affected Scope:** `android/app/src/main/java/com/resolvia/dispatch/ui/RecordScreen.kt`
- **Analysis:**
  `RecordScreen.kt` and supporting Compose preview functions remain in the Android tree, consuming maintenance bandwidth and misleading developers, while the production UI is rendered entirely via `android.webkit.WebView` loading the React single-page application.

### Finding 9: `DISPATCH_REMEDIATION_LEDGER.md` Frozen at Phase 10
- **Severity:** 🟢 Nit
- **Affected Scope:** `DISPATCH_REMEDIATION_LEDGER.md`
- **Analysis:**
  The remediation ledger halts tracking at Phase 10 and has not been updated with subsequent cloud migration, Supabase auth, or administrative dashboard tasks.

---

## 3. Actionable Remediation Plan

| Priority | Task | Description |
|:---|:---|:---|
| **P0** | **Rewrite Milestone Spec** | Replace M1–M5 in `PROJECT.md` with active Cloud Hybrid milestones. |
| **P0** | **Enforce YouTube Privacy Validation** | Reject `private` YouTube URLs immediately at submission; require `unlisted`. |
| **P0** | **Create CameraX Test Fixture** | Implement mock video source in Android to enable automated end-to-end verification without physical POCO C65. |
| **P1** | **Implement DLQ & Job Timeout** | Add automated 15-minute lease expiry and DLQ state transition for stalled inbox jobs. |
| **P1** | **Dynamic Presigned TTL** | Increase presigned upload URL TTL to 3600 seconds (1 hour) for mobile video payloads. |
| **P2** | **Purge Dead Compose Views** | Delete unused Compose screens or archive them to prevent architectural confusion. |
