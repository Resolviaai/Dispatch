# AGENT_HANDOFF — Dispatch working-state index

Single source of truth for the next coding agent. Keep this short; link, don't copy.
Updated: 2026-10-23 (checkpoint-protocol session).

Detailed docs: `PROGRESS.md` (verification log) · `docs/ROUTE_B_IMPLEMENTATION_HANDOFF.md` (Route B workflow) · `docs/EXECUTION_HANDOFF.md` · `docs/architecture/cloud-migration-plan.md` (**current task plan + baseline findings**) · `AGENTS.md` (UI invariants) · `DESIGN_SYSTEM.md` · `docs/CANONICAL_ARCHITECTURE.md`.

## 1. Project overview
Personal content engine: POCO C65 Android app records long sessions → uploads directly to YouTube (unlisted) → Windows PC daemon (`python -m dispatch.main`) polls YouTube authenticated, downloads originals, transcribes (YouTube captions first, Gemini two-stage fallback), Gemini selects highlight clips, FFmpeg renders 9:16 clips with burned captions, React review studio (FastAPI `127.0.0.1:8000`, also packaged into the Android WebView), approve → durable outbox publishes to YouTube/Instagram/LinkedIn/X. Local SQLite (WAL) owns processing state. **No cloud component yet** — that is the current task.

## 2. Current objective
Cloud migration per `docs/architecture/cloud-migration-plan.md` (authoritative plan; owner brief = the Principal Architect brief). Add Supabase Auth/Postgres (identity + cloud-owned metadata/review/quota), private R2 clip media, Cloudflare Worker control plane, server-enforced quotas/retention, remote review — **all media processing stays on the PC; originals stay on YouTube; not cloud rendering**. Free-tier only, RLS everywhere, secrets server-side only, preserve range-revision semantics, keep local pipeline and tests green.

## 3. Repository state
- Branch `main`, HEAD at last checkpoint `9db3054` ("docs: update progress and execution handoff for verification run"), == origin/main, clean tree.
- **Runtime:** Dispatch server was left RUNNING by user request (PID 13396, bound `0.0.0.0:8000` — LAN-exposed for phone APK download). Stop with `taskkill /PID 13396 /F`. Verify it still exists before acting on it; it does not block tests (tests redirect TEMP/TMP and use TestClient).

## 4. Architecture decisions (digest)
- YouTube = canonical storage of original long sessions; phone never talks to the PC (LAN sync deliberately deleted).
- SQLite = local processing truth. `ai_suggested_start/end` immutable; `range_revision` optimistic concurrency enforced in `db.update_clip_trim` / `update_clip_media` / `save_clip_caption_data` (409 on mismatch); render must pass ffprobe + duration match ±1 s before approval publishes; unreliable word-timing captions rejected (422).
- Cloud plan decisions D1–D11 (boundaries, source-of-truth table, conflict rules, DB model, media contract, quotas, retention, sync interface, multi-user YouTube credential blocker): see plan doc §2.

## 5. Completed work (verified)
- `79444b7` editor/long-input reliability + regressions; `1960daa`/`55b1790` test-artifact cleanup + docs; `9db3054` verification doc sync; `e125afb` **cloud baseline + plan docs** (`AGENT_HANDOFF.md` + `docs/architecture/cloud-migration-plan.md`).
- 2026-10-23 verification at `79444b7`: frontend `npm test`/`npm run build` exit 0 (bundle `index-Bd9C_B3d.js`); Python focused suite **80 tests OK, 74.1 s** (incl. `test_caption_range_sync`); Android **18 tests, 0 failures**, APK 15,189,951 B byte-verified against that bundle.
- 2026-10-23 runtime at `55b1790`/`9db3054`: pip repaired, `pip install -e .` done, `google-genai 2.32.0` imports; server boots clean; dashboard + `/api/clips` HTTP 200; **live read-only authenticated YouTube uploads poll succeeded**; `/download/dispatch.apk` serves the fresh APK byte-identically.
- 2026-10-23 Phase B (cloud skeleton, committed with this handoff update): `dispatch/cloud/{__init__,config,media_store,quota}.py` + `tests/test_cloud_config.py`, `tests/test_cloud_quota.py`. **18/18 cloud unit tests OK** (0.003 s); focused suite re-confirmed **80 OK (61.4 s)**. Completed and unit-tested; not yet exercised against real cloud services (none exist yet).

## 6. Current work
Cloud migration plan-doc phases: **A (docs) DONE (`e125afb`); B (config/interface/quota skeleton) DONE** pending the regression-suite confirmation recorded in §8. Phase C (Supabase SQL migrations + RLS) NOT STARTED. Nothing else of the cloud task exists; do not assume Worker/PC-sync/UI code is present.

## 7. Remaining steps
Ordered checklist lives in plan doc §3 (Phase A–F with per-phase verification). Summary: A docs ✅ → B config/interface skeleton + tests → C Supabase SQL migrations + RLS (+ two-user test scripts) → D Worker (reserve/complete/url/delete + cleanup cron) → E PC sync integration (upload clip media after render, pull remote review actions) → F remote review UI + deployment checklist (manual, user-authorized).

## 8. Verification (exact commands, last results)
- Frontend: `cd frontend && npm test` / `npm run build` → exit 0 (2026-10-23). Bundle: `android/app/src/main/assets/web/assets/index-Bd9C_B3d.js` (deterministic).
- Python focused (from repo root, TEMP/TMP → `.dispatch-test-tmp`): `python -m unittest tests.test_caption_range_sync tests.test_caption_approval_contract tests.test_clip_trim_contract tests.test_gemini_two_stage tests.test_web tests.test_youtube_ingest_endpoint tests.test_youtube_inbox tests.test_youtube_processing_tracker tests.test_youtube_caption_timing_fix tests.test_captions_subsystem tests.test_video_engine tests.e2e.test_tier1_features_publishing_dashboard tests.test_layout_framing_architecture -v` → **80 OK, 74.1 s**. The historical "380/380" claim is from 2026-10-09, NOT re-verified.
- Android: `JAVA_HOME=C:\Program Files\Java\jdk-17.0.5; .\gradlew.bat :app:testDebugUnitTest :app:assembleDebug` → 18/0/0, BUILD SUCCESSFUL.
- **Not verified (never claimed):** live Gemini request, live Route B end-to-end, YouTube processing status, any publishing, POCO C65 device behavior. ADB: daemon OK, no devices.

## 9. Configuration
- `.env` present with `GEMINI_API_KEY` (set, 53 chars — value never printed). `youtube_token.json`, `client_secrets.json` present, untouched. Publishing stays **private**.
- Planned new env vars (names only; see plan doc §4): `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (server-only: Worker + PC), `SUPABASE_ANON_KEY` (frontend), `DISPATCH_WORKER_URL`, `DISPATCH_WORKER_TOKEN` (PC↔Worker shared secret), `DISPATCH_CLOUD_SYNC_ENABLED`.
- Toolchain: Python 3.11.15 (`.venv`, pip 24.0), FFmpeg/ffprobe 8.0.1 on PATH, JDK 17.0.5, ADB at `%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe`.

## 10. Known problems / blockers
- Pre-existing inbox item stuck at `STALLED_YOUTUBE_PROCESSING` (video from 2026-10-20) — untouched.
- Friends' private-YouTube processing is blocked by single-account credential design — plan doc D10 records the boundary; no insecure shortcut.
- Vite >500 kB chunk advisory (non-blocking). Supabase Free projects pause after inactivity; free-plan backups need manual export. Free tier is a ceiling, not a billing guarantee.
- Legacy `dispatch/sync/` + `dispatch/transport/` retained only because legacy tests import them.
- Server currently LAN-exposed (see §3) — revert to loopback when phone testing is done.

## 11. Recovery instructions
1. Read this file, then the plan doc, then relevant linked doc. 2. `git status --short; git branch --show-current; git rev-parse HEAD` and compare with §3. 3. If results in §8 may be stale (code changed), rerun the relevant suite. 4. Inspect uncommitted changes before touching them; never discard or overwrite them. 5. Continue from the first incomplete item in plan doc §3. Do not trust "intended" claims — verify.

## 12. Next action
Implement plan-doc **Phase C**: create `supabase/migrations/0001_init.sql` (schema per plan §2-D4: profiles, source_sessions, clips_cloud, media_objects, upload_reservations, publications, rejections — RLS policies + grants on every user-owned table) and `tests/rls_two_user_checklist.sql` (two-user allow/deny script, labeled manual until a real project exists). Verify with a SQL syntax/lint pass (no live execution without user authorization), commit with the handoff update folded in.
