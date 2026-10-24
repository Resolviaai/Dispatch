# Architecture Audit — Admin Scheduling, Per-User Keys, Media/Render Path (2026-10-25)
Base: `main` @ `cdb289c`, tree clean. Read-only audit (3 parallel investigators + reconciliation). No code edited. Facts carry file:line; inferences labeled.

## 1. Verified current state
- Pipeline is real end-to-end (both routes → ingest → transcribe → Gemini highlights → FFmpeg render → review → outbox). Nothing from the pipeline ever reaches R2 (zero callers of `/reserve`/`/complete`; `dispatch/cloud/` = interfaces + quota kernel only).
- Per-user YouTube token vault: **write-only** — `get_user_refresh_token` has zero callers; `RemoteJobPoller._process` drops `job.user_id` (`dispatch/youtube_connections.py:126-139`).
- Admin: list/ban/unban only. **No priority ordering exists anywhere** (grep across migrations/Worker/PC/Android).
- Single GLOBAL Gemini key (owner `.env`); per-user Gemini keys do not exist; `highlight_finder.call_gemini_generate_content(api_key=…)` (highlight_finder.py:589) is a ready seam.
- PC background workers: 6+ threads, no global concurrency limiter — heavy Gemini/yt-dlp/FFmpeg work CAN overlap across workers; remote jobs are serial only by accident (synchronous poller).

## 2. Status table
| Requirement | Evidence | Gap | Risk |
|---|---|---|---|
| Admin priority ordering (persisted) | No priority anywhere; `profiles` table lacks a column (0001:24-29) | Whole feature | Friends' jobs processed in arrival order only |
| Fairness (3-consecutive rule) | `cloud_claim_job` = pure global FIFO, `skip locked` (0007:39-54) | Not implemented | One user can monopolize the PC |
| Job crash recovery | Nothing requeues `claimed` rows; PC death = stuck forever | Stale-lease requeue | Friend's job lost silently |
| Per-user queue caps | Only same-URL dedupe (0007:26-32) | Unlimited distinct URLs per user | One user floods the owner's PC/disk |
| PC processing limit | No limiter; per-worker locks only; renders/downloads overlap (worker.py:396 vs gemini_engine.py:467) | Global semaphore | PC overload, thermal, quality |
| Per-user Gemini key | Missing entirely; one global key in `.env`+env+3 globals | Full workstream | Friends cannot use the product; owner key is shared |
| Key vault consumption | Vault write-only; poller drops user_id | Just-in-time delivery | Designed security never enforced |
| Friend sees own clips | Clips render to PC disk only (`storage/clips/`); review via PC LAN API; zero R2 pipeline uploads | Cloud review sync (Phase E) | Friends can submit but not review — alpha incomplete |
| Phone rendering | No Media3 dependency; ADR-001 Decision 1 = PC renders | Conflicts with ADR | Weeks of device work; caption parity risk |
| Exposure/AF reliability | Native lock flow sound (CameraCaptureManager.kt:640-680: support probes, timeout, recovery, uncertain state) | Device-level AF confirmation timeouts on HyperOS | Cosmetic banner; recording unaffected |

## 3. Key corrections to the owner's mental model (facts)
- **Padding is ±4 seconds, not ±1 minute** (`highlight_finder.py:533-534`; identical in db.py:520-521, ai_clips/worker.py:266-267). A typical AI clip's "raw window" is 40–70 s; ~3.1 min only for a max-length 180 s clip.
- The editor already allows selection across the **full source** (bounded by source duration + 5–180 s), NOT limited to the AI window (`db.py:578-587`; padding is informational, used only for Stage-2 audio).
- The 10-min source is ~0.75–1.2 GB at device bitrates → **exceeds the 512 MiB R2 object cap** and eats 37–60% of a user's 2 GB quota per segment. Full-sources-in-R2 is contract-breaking and budget-breaking.

## 4. Smallest safe architecture (recommended)
**Alpha (now):** keep PC rendering (ADR-001 Decision 1). Build in this order:
1. **Job fairness + recovery (DB-first):** migration 0009 — `profiles.queue_priority` + stale-lease requeue (45 min) inside `cloud_claim_job` (priority asc, created_at asc, skip-locked; 3-consecutive-user fairness; store `claimed_by`). Pure-Python kernel `dispatch/cloud/job_queue.py` mirrored from the SQL for unit tests.
2. **Ingestion limiter:** `threading.BoundedSemaphore(1)` around download/validation in `ingest_video` (env-tunable) — makes remote jobs + poller + dashboard share one heavy slot.
3. **Per-user Gemini keys via the existing vault:** Settings card (enter/delete) → Worker `POST/DELETE /keys/gemini` (JWT + allowlist-free but user-scoped; AES-GCM with TOKEN_VAULT_KEY) → `gemini_connections` table (clone 0006) → PC `get_user_gemini_key(user_id)` → delivered as a function argument into `call_gemini_generate_content`/`get_gemini_client` (seams exist). Missing key → item FAILED `NO_USER_GEMINI_KEY` (no owner fallback); 401/403 → vault row deleted + "reconnect required".
4. **Phase E lite (unblocks friends):** PC uploads the **rendered reviewable clip** (+thumbnail) to R2 after render (D8-compliant), writes `clips_cloud` metadata; app lists own clips via RLS + signed `/media-url`. Per-user publish via vault token comes after.
**Later (needs ADR amendment):** phone rendering via Media3 (custom karaoke-bitmap overlays, loudnorm approximation, Android 14 `mediaProcessing` FGS, POCO thermal) — weeks of device work; only after cloud review proves the alpha.

## 5. Owner decisions (only these three)
1. **R2 boundary for remote editing:** (a) bounded proxy = AI window + ±4 s padding, selection limited to that window (recommended), or (b) full source in R2 (requires raising the 512 MiB cap + quota redesign + accepting originals in cloud).
2. **Rendering location for the alpha:** keep PC rendering (recommended) or amend ADR-001 for phone rendering now.
3. **Google consent screen:** publish the app (Production) or keep Testing and add each friend's email — this gates every friend's Connect YouTube.

## 6. Files + tests for implementation
- Migration `supabase/migrations/20261024000009_priority_and_leases.sql`; `cloud-worker/src/index.ts` (priority route); `dispatch/cloud/job_queue.py` (kernel); `dispatch/youtube_connections.py` (identity passthrough); `dispatch/youtube_inbox/catcher.py` (semaphore); `dispatch/ai_clips/highlight_finder.py` + `dispatch/transcription/gemini_engine.py` (api_key params); `dispatch/web/app.py` + `dispatch/web/templates/index.html` (mask fix: auth-protect `/api/integrations/status`); `frontend/src/screens/SettingsScreen.tsx` (Gemini card); Worker `/keys/gemini` routes; migrations for `gemini_connections`.
- Tests: `tests/test_remote_job_fairness.py` (priority/FIFO/3-rule/starvation/requeue), ingestion-cap concurrency test, vault Gemini round-trip + tamper, identity-passthrough test, NO_USER_GEMINI_KEY test, revocation test, real-.env-untouched guard (exists).
- Worker handler tests: admin priority route 403/allowlist; `/keys/gemini` auth + format rejection.

## 7. Unverified gates
- Live Google consent for a non-owner account (GCP Testing mode suspected blocker).
- Real POCO C65: full record→upload→process→review loop; camera exposure/AF reliability on HyperOS.
- Browser round-trip of the web admin (server-side flow verified; browser click pending).
- R2 upload/download at real clip sizes; YouTube publish → processing-status behavior for published clips (PC outbox marks `published` immediately — no processing-status check, outbox.py:271-284).
