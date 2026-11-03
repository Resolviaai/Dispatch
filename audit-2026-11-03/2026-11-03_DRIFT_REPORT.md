# Elephant vs. Goldfish Drift Report: Dispatch Repository

**Audit Date:** 2026-11-03  
**Audit Standard:** Elephant-Goldfish Model (EGM) Drift Detection Specification  
**Scope:** Repository-wide comparison of canonical specifications (`PROJECT.md`, `design.md`, `AGENTS.md`, `docs/architecture/`) vs. physical codebase implementations (`android/`, `frontend/`, `dispatch/`, `cloud-worker/`, `tests/`).  
**Status:** High Systemic Drift (Elephant Memory Decoupled from Physical Code)

---

## 1. Executive Summary & Drift Metrics

In the Elephant-Goldfish Model, the **Elephant** represents the long-term institutional specification and memory, while **Goldfish** represent active agents executing individual sprints. Systemic drift occurs when fast-moving changes modify the code without synchronizing the Elephant, leaving incoming engineers and agents operating on obsolete assumptions.

### Overall Drift Scorecard

| Subsystem Scope | Documented Spec | Actual Physical Reality | Drift Classification | Impact |
|:---|:---|:---|:---|:---|
| **Capture UI Tier** | 6 Pro Jetpack Compose screens (`RecordScreen.kt`) | Full-bleed transparent WebView hosting React SPA (`frontend/`) | 🔴 Total Inversion | High |
| **Ingestion Protocol** | LAN Chunked Sync (`/api/sync/upload/*`) + UDP 8765 | Retired (HTTP 410); YouTube Direct Upload + Cloudflare R2 | 🔴 Decommissioned Path | Critical |
| **System Topology** | Dual-tier (Android Capture + Windows PC Engine) | Tri-tier (Android + PC Daemon + Cloudflare Worker/R2/Supabase) | 🟡 Uncharted Subsystem | Critical |
| **AI Highlight Engine** | `gemini-2.5-flash` with local heuristic fallback | `gemini-flash-latest` + invalid fallback models; zero heuristic | 🟡 API & Model Drift | High |
| **Job Queue API** | `claim_job(worker_id, 60s)` with 5 max attempts | `claim_job(worker_id, 300s)` with DB-driven max attempts | 🟡 Interface Signature Drift | Medium |
| **Theme Design Tokens** | Primary hover `#3B82F6` in `AGENTS.md` | Primary hover `#1D4ED8` in `design.md` v3 and CSS | 🟢 Token Divergence | Low |

---

## 2. Exhaustive Drift Inventory

### Drift 01: Ingestion Protocol Decommissioning
- **Canonical Specification:** [`PROJECT.md#L32-L34`](file:///c:/CODE/Dispatch/PROJECT.md#L32-L34), [`PROJECT.md#L66-L70`](file:///c:/CODE/Dispatch/PROJECT.md#L66-L70)
  - Spec specifies:
    ```http
    POST /api/sync/upload/init
    PATCH /api/sync/upload/chunk
    POST /api/sync/upload/direct
    ```
    With `InitUploadRequest` returning `{status, remote_offset, verified}`.
- **Physical Code Reality:** [`dispatch/sync/receiver.py#L691-L723`](file:///c:/CODE/Dispatch/dispatch/sync/receiver.py#L691-L723)
  - All three endpoints have `include_in_schema=False` and unconditionally return:
    ```python
    raise HTTPException(
        status_code=410,
        detail="Chunked LAN upload retired. Recordings arrive via YouTube."
    )
    ```
- **Drift Mechanism:** Phase 10 architectural pivot to cloud/YouTube ingestion retired the LAN receiver, but the Elephant memory in `PROJECT.md` was never updated.
- **Remediation:** Remove retired LAN upload contracts from `PROJECT.md`. Formalize `POST /api/sync/reconcile`, `GET /api/sync/verify-chunk`, and `GET /api/sync/youtube-ack`.

---

### Drift 02: Mobile UI Architecture Inversion (Compose vs WebView)
- **Canonical Specification:** [`PROJECT.md#L14`](file:///c:/CODE/Dispatch/PROJECT.md#L14), [`PROJECT.md#L40`](file:///c:/CODE/Dispatch/PROJECT.md#L40), [`AGENTS.md#L3`](file:///c:/CODE/Dispatch/AGENTS.md#L3)
  - Spec specifies:
    > "Android Native Jetpack Compose... 6 Pro Compose UI screens (Record Home, FullScreen Active Viewfinder, Sessions & Upload Gauge, Live Processing Timeline, 9:16 Clip Reviewer, Settings & Connection)"
- **Physical Code Reality:** [`android/app/src/main/java/com/resolvia/dispatch/MainActivity.kt#L254-L460`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/MainActivity.kt#L254-L460), [`frontend/src/App.tsx`](file:///c:/CODE/Dispatch/frontend/src/App.tsx)
  - Android native UI contains **zero** active Compose screens.
  - Root layout is a `FrameLayout` embedding CameraX `PreviewView` beneath a transparent `android.webkit.WebView`.
  - All screens (`CameraScreen`, `RecordingsScreen`, `ClipsScreen`, `SettingsScreen`) are React components in `frontend/src/`.
- **Drift Mechanism:** The web review studio was transplanted into a WebView for cross-platform velocity, leaving Compose files (`RecordScreen.kt`) dead and orphaned.
- **Remediation:** Update `PROJECT.md` and `AGENTS.md` to specify the hybrid CameraX + Full-Bleed Transparent WebView architecture. Either delete or archive `android/.../ui/RecordScreen.kt`.

---

### Drift 03: The Phantom Cloudflare Worker & Supabase Tier
- **Canonical Specification:** [`PROJECT.md#L7-L26`](file:///c:/CODE/Dispatch/PROJECT.md#L7-L26)
  - Spec specifies a strictly dual-tier system:
    1. Android Capture Tier (POCO C65)
    2. PC Media & Orchestration Tier (Windows 11)
- **Physical Code Reality:** [`cloud-worker/src/index.ts`](file:///c:/CODE/Dispatch/cloud-worker/src/index.ts) (4,366 lines), [`dispatch/cloud/sync.py`](file:///c:/CODE/Dispatch/dispatch/cloud/sync.py)
  - A massive third tier exists: Cloudflare Worker, Supabase Postgres, R2 Cloud Storage.
  - Controls authentication, user quotas, presigned URLs, social OAuth vaults, and multi-platform publishing.
- **Drift Mechanism:** Cloud migration was developed in a dedicated workstream (`cloud-worker/`) but was never integrated back into the core system documentation.
- **Remediation:** Elevate `PROJECT.md` to a formal Tri-Tier Architecture specification.

---

### Drift 04: AI Highlight Generation & Model Catalog Drift
- **Canonical Specification:** [`PROJECT.md#L22`](file:///c:/CODE/Dispatch/PROJECT.md#L22), [`PROJECT.md#L46`](file:///c:/CODE/Dispatch/PROJECT.md#L46)
  - Spec specifies: AI highlight extraction using `gemini-2.5-flash` with "local heuristic fallback".
- **Physical Code Reality:** [`dispatch/ai_clips/highlight_finder.py#L22-L28`](file:///c:/CODE/Dispatch/dispatch/ai_clips/highlight_finder.py#L22-L28)
  - Active models configured:
    ```python
    DEFAULT_GEMINI_MODELS = [
        "gemini-flash-latest",
        "gemini-3.5-flash-lite", # NON-EXISTENT
        "gemini-3.1-flash-lite"  # NON-EXISTENT
    ]
    ```
  - Code explicitly mandates: *"Pillar 3 authority: strictly relies on Gemini API with structured candidate validation. Never substitutes a silent local heuristic."*
  - Fails loudly with `RuntimeError` rather than falling back.
- **Drift Mechanism:** Model strings were updated speculatively to future model names without checking the Gemini API catalog; policy on silent fallbacks was inverted to prevent uncalibrated clips.
- **Remediation:** Correct model list to valid models (`gemini-2.5-flash`, `gemini-2.0-flash`). Update specification to note that highlight extraction fails loudly on exhaustion.

---

### Drift 05: Job Queue Interface Signatures
- **Canonical Specification:** [`PROJECT.md#L73-L75`](file:///c:/CODE/Dispatch/PROJECT.md#L73-L75)
  - Spec specifies:
    ```python
    claim_job(worker_id, lease_duration_seconds=60)
    fail_stage_job(job_id, worker_id, error_message, max_attempts=5)
    ```
- **Physical Code Reality:** [`dispatch/orchestrator/job_queue.py#L93`](file:///c:/CODE/Dispatch/dispatch/orchestrator/job_queue.py#L93), [`#L203`](file:///c:/CODE/Dispatch/dispatch/orchestrator/job_queue.py#L203)
  - Physical signatures:
    ```python
    claim_job(worker_id: str, lease_duration_seconds: int = 300)
    fail_stage_job(job_id: str, error_message: str, wait_state: Optional[JobStatus] = None, delay_seconds: int = 30)
    ```
  - Default lease is 300s (not 60s). Parameters `worker_id` and `max_attempts` do not exist in `fail_stage_job`.
- **Drift Mechanism:** Internal queue mechanics were refined to handle heavy Whisper transcription times without updating the specification.
- **Remediation:** Update `PROJECT.md` Section 2 Interface Contracts to match actual Python signatures.

---

### Drift 06: Storage Retention & Pruning Invariants
- **Canonical Specification:** [`PROJECT.md#L36`](file:///c:/CODE/Dispatch/PROJECT.md#L36), [`PROJECT.md#L76`](file:///c:/CODE/Dispatch/PROJECT.md#L76)
  - Spec specifies: *"Source video is never deleted during `_run_finalize_stage`."*
- **Physical Code Reality:** [`dispatch/main.py#L202-L250`](file:///c:/CODE/Dispatch/dispatch/main.py#L202-L250), [`dispatch/storage/retention.py`](file:///c:/CODE/Dispatch/dispatch/storage/retention.py)
  - Two autonomous background daemons enforce multi-tiered pruning:
    - `_published_clip_janitor`: Deletes heavy rendered MP4 files after 3 days.
    - `_storage_retention_janitor`: Enforces 7-day source video pruning, 72-hour published source pruning, and emergency FIFO deletion when disk thresholds are exceeded.
- **Drift Mechanism:** Disk space management was implemented defensively in daemon workers without updating the Elephant document.
- **Remediation:** Add Section 3.4 "Autonomous Storage Retention Policy" to `PROJECT.md`.

---

### Drift 07: Design Token Accent Hover Divergence
- **Canonical Specification:** [`AGENTS.md#L27`](file:///c:/CODE/Dispatch/AGENTS.md#L27)
  - Spec specifies: `Electric Creator Blue #2563EB (bg-primary, hover #3B82F6, soft glow rgba(37,99,235,0.15))`
- **Physical Code Reality:** [`design.md#L6`](file:///c:/CODE/Dispatch/design.md#L6), [`frontend/src/index.css#L23`](file:///c:/CODE/Dispatch/frontend/src/index.css#L23), [`frontend/tailwind.config.js#L25`](file:///c:/CODE/Dispatch/frontend/tailwind.config.js#L25)
  - Both `design.md` v3 and CSS tokens specify hover as `#1D4ED8` (darker blue for contrast compliance).
- **Drift Mechanism:** Design System v3 changed hover token from `#3B82F6` (v2) to `#1D4ED8`, but `AGENTS.md` retained the old v2 value.
- **Remediation:** Update `AGENTS.md` line 27 to specify `#1D4ED8`.

---

## 3. Drift Resolution Action Items

1. **Synchronize `PROJECT.md`:** Commit a complete revision of `PROJECT.md` reflecting the Tri-Tier Architecture, active sync contracts, and real queue signatures.
2. **Synchronize `AGENTS.md`:** Align UI description and hover token to Design System v3.
3. **Archive Decommissioned Code:** Remove or archive `android/.../ui/RecordScreen.kt` and `dispatch/transport/discovery.py` to prevent ongoing agent confusion.
