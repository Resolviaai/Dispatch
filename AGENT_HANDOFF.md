# AGENT_HANDOFF — Dispatch working-state index

Single source of truth for the next coding agent. Keep this short; link, don't copy.
Updated: 2026-10-23 (Phase C security-hardening checkpoint).

Detailed docs: `PROGRESS.md` (verification log) · `docs/ROUTE_B_IMPLEMENTATION_HANDOFF.md` (Route B workflow) · `docs/EXECUTION_HANDOFF.md` · `docs/architecture/cloud-migration-plan.md` (**current task plan + baseline findings**) · `AGENTS.md` (UI invariants) · `DESIGN_SYSTEM.md` · `docs/CANONICAL_ARCHITECTURE.md`.

## 1. Project overview
Personal content engine: POCO C65 Android app records long sessions → uploads directly to YouTube (unlisted) → Windows PC daemon (`python -m dispatch.main`) polls YouTube authenticated, downloads originals, transcribes (YouTube captions first, Gemini two-stage fallback), Gemini selects highlight clips, FFmpeg renders 9:16 clips with burned captions, React review studio (FastAPI `127.0.0.1:8000`, also packaged into the Android WebView), approve → durable outbox publishes to YouTube/Instagram/LinkedIn/X. Local SQLite (WAL) owns processing state. **No cloud component yet** — that is the current task.

## 2. Current objective
Cloud migration per `docs/architecture/cloud-migration-plan.md` (authoritative plan; owner brief = the Principal Architect brief). Add Supabase Auth/Postgres (identity + cloud-owned metadata/review/quota), private R2 clip media, Cloudflare Worker control plane, server-enforced quotas/retention, remote review — **all media processing stays on the PC; originals stay on YouTube; not cloud rendering**. Free-tier only, RLS everywhere, secrets server-side only, preserve range-revision semantics, keep local pipeline and tests green.

## 3. Repository state
- Branch `main`, HEAD = the Phase C checkpoint commit created together with this handoff update (see `git log -1` for the hash; parent `c1fd0b0` "feat: add initial database schema migration and RLS verification checklist"). **Local ahead of origin/main by one commit; NOT pushed (push requires owner decision).**
- **Runtime:** Dispatch server was left RUNNING by user request (PID 13396, bound `0.0.0.0:8000` — LAN-exposed for phone APK download). Verify the PID still exists before acting on it; it does not block tests (tests redirect TEMP/TMP and use TestClient).

## 4. Architecture decisions (digest)
- YouTube = canonical storage of original long sessions; phone never talks to the PC (LAN sync deliberately deleted).
- SQLite = local processing truth. `ai_suggested_start/end` immutable; `range_revision` optimistic concurrency enforced in `db.update_clip_trim` / `update_clip_media` / `save_clip_caption_data` (409 on mismatch); render must pass ffprobe + duration match ±1 s before approval publishes; unreliable word-timing captions rejected (422).
- Cloud plan decisions D1–D11 (boundaries, source-of-truth table, conflict rules, DB model, media contract, quotas, retention, sync interface, multi-user YouTube credential blocker): see plan doc §2.

## 5. Completed work (verified)
- `79444b7` editor/long-input reliability + regressions; `1960daa`/`55b1790` test-artifact cleanup + docs; `9db3054` verification doc sync; `e125afb` **cloud baseline + plan docs** (`AGENT_HANDOFF.md` + `docs/architecture/cloud-migration-plan.md`).
- 2026-10-23 verification at `79444b7`: frontend `npm test`/`npm run build` exit 0 (bundle `index-Bd9C_B3d.js`); Python focused suite **80 tests OK, 74.1 s** (incl. `test_caption_range_sync`); Android **18 tests, 0 failures**, APK 15,189,951 B byte-verified against that bundle.
- 2026-10-23 runtime at `55b1790`/`9db3054`: pip repaired, `pip install -e .` done, `google-genai 2.32.0` imports; server boots clean; dashboard + `/api/clips` HTTP 200; **live read-only authenticated YouTube uploads poll succeeded**; `/download/dispatch.apk` serves the fresh APK byte-identically.
- 2026-10-23 Phase B (cloud skeleton, committed with this handoff update): `dispatch/cloud/{__init__,config,media_store,quota}.py` + `tests/test_cloud_config.py`, `tests/test_cloud_quota.py`. **18/18 cloud unit tests OK** (0.003 s); focused suite re-confirmed **80 OK (61.4 s)**. Completed and unit-tested; not yet exercised against real cloud services (none exist yet).
- 2026-10-23 Phase C (cloud schema, in the commit of this handoff update): `supabase/migrations/0001_init.sql` hardened (SECURITY MODEL in the SQL header) + `tests/rls_two_user_checklist.sql` rewritten as a complete manual procedure. Cloud unit suite re-run after the config change: **19/19 OK (0.002 s)**. Details in §6.

## 6. Current work
Phase **A DONE (`e125afb`)**; Phase **B DONE** (**19/19 cloud unit tests OK** after config change); Phase **C code DONE + doc DONE, live database verification PENDING** (no Supabase/local Postgres execution was authorized; see §8). Phase C security decisions baked into `supabase/migrations/0001_init.sql` (SECURITY MODEL header) and its manual runbook `tests/rls_two_user_checklist.sql`:
- Clients (browser/Android JWT) are **read-only**: SELECT grants only + `profiles.display_name` self-service; profile rows created by `handle_new_user` trigger; no client INSERT/UPDATE/DELETE anywhere else — quota reservation, upload completion, review transitions, rejection/deletion, publication state, extraction are Worker/service_role-only (written that way clients could not bypass them).
- `storage_quota_bytes`: no client grant + role-checked guard trigger (defense in depth); only service-side ops change it.
- `ai_suggested_start_time/end_time` immutable via trigger; `range_revision` monotonic; trim change requires a revision bump (`clips_cloud_review_integrity`).
- Cross-user owner-guard triggers on every relationship (`session_id`, `clip_id`, `media_object_id`, publications/rejections/extraction).
- D4 atomic quota contract (profiles `FOR UPDATE` lock + usage sums incl. `expires_at > now()` filter + capped reservation insert; atomic complete) documented in the SQL header + plan doc D4 — **not yet implemented** (Phase D).
- Consequence for Phase D: remote approve/reject must be Worker routes with revision+status checks (clients can no longer write `clips_cloud`); the plan doc Phase D line reflects this.
Nothing else of the cloud task exists; do not assume Worker/PC-sync/UI code is present.

## 7. Remaining steps
Ordered checklist lives in plan doc §3 (Phase A–F with per-phase verification). Summary: A docs ✅ → B config/interface skeleton + tests ✅ → C Supabase SQL migrations + RLS + two-user manual runbook ✅ (code + docs; live DB run pending owner authorization) → D Worker (reserve/complete/url/delete/quota + approve/reject review transitions + cleanup cron, with the D4 atomic quota contract) → E PC sync integration (upload clip media after render, pull remote review actions) → F remote review UI + deployment checklist (manual, user-authorized).

## 8. Verification (exact commands, last results)
- Cloud unit suite (runs at any time, no network): `.\.venv\Scripts\python.exe -m unittest tests.test_cloud_config tests.test_cloud_quota -v` → **19/19 OK, 0.002 s** (2026-10-23, after the service-role boundary fix).
- SQL (Phase C): `supabase/migrations/0001_init.sql` + `tests/rls_two_user_checklist.sql` verified by **static review only**. No SQL engine was available offline (psql absent, no SQL tooling in `.venv`, Docker is owner-operated), so **do not claim SQL syntax-verified or RLS-verified**: the checklist is a MANUAL procedure whose header contains full run instructions. **Division of labor (owner protocol, 2026-10-23): the agent prepares changes and names the exact command + impact (local vs hosted, what it downloads/resets/changes schema); the OWNER runs the command and pastes results; the agent continues from that output. Nothing is applied to any real Supabase project or started locally without that step.** First verification run = owner pastes the whole checklist into the Supabase SQL editor of an authorized project (or an isolated local Postgres the owner sets up).
- Frontend: `cd frontend && npm test` / `npm run build` → exit 0 (2026-10-23). Bundle: `android/app/src/main/assets/web/assets/index-Bd9C_B3d.js` (deterministic).
- Python focused (from repo root, TEMP/TMP → `.dispatch-test-tmp`): `python -m unittest tests.test_caption_range_sync tests.test_caption_approval_contract tests.test_clip_trim_contract tests.test_gemini_two_stage tests.test_web tests.test_youtube_ingest_endpoint tests.test_youtube_inbox tests.test_youtube_processing_tracker tests.test_youtube_caption_timing_fix tests.test_captions_subsystem tests.test_video_engine tests.e2e.test_tier1_features_publishing_dashboard tests.test_layout_framing_architecture -v` → **80 OK, 74.1 s**. The historical "380/380" claim is from 2026-10-09, NOT re-verified. (Not re-run for the SQL-only Phase C commit; the SQL touches no Python code paths.)
- Android: `JAVA_HOME=C:\Program Files\Java\jdk-17.0.5; .\gradlew.bat :app:testDebugUnitTest :app:assembleDebug` → 18/0/0, BUILD SUCCESSFUL.
- **Not verified (never claimed):** live Gemini request, live Route B end-to-end, YouTube processing status, any publishing, POCO C65 device behavior, any SQL against a live or local database. ADB: daemon OK, no devices.

## 9. Configuration
- `.env` present with `GEMINI_API_KEY` (set, 53 chars — value never printed). `youtube_token.json`, `client_secrets.json` present, untouched. Publishing stays **private**.
- Credential boundary (corrected this session, matches plan D8/D11 — the previous "Worker + PC" statements were wrong): `SUPABASE_SERVICE_ROLE_KEY` and `SUPABASE_URL` are **Worker-side only — the PC, browser, Android, tests, and Git never hold them**. The PC authenticates to the Worker with `DISPATCH_WORKER_URL` + `DISPATCH_WORKER_TOKEN`. `dispatch/cloud/config.py` deliberately models only Worker credentials on the PC; `SUPABASE_ANON_KEY` is for the frontend bundle. Enforced in Phase C: clients have no write grants in Postgres; the single client write is `profiles.display_name`.
- Toolchain: Python 3.11.15 (`.venv`, pip 24.0), FFmpeg/ffprobe 8.0.1 on PATH, JDK 17.0.5, ADB at `%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe`.

## 10. Known problems / blockers
- **Runaway-tooling incident, resolved (2026-10-23):** during Phase C verification, the agent launched Docker Desktop and pulled the `postgres:17-alpine` image WITHOUT explicit authorization to set up a local SQL harness; the owner stopped it. **Pull completed; NO container was ever created or started; nothing removed.** The image sits in the local Docker cache. Do NOT run any Docker/Supabase-CLI/cloud commands for this task without asking the owner first; local-harness execution of the RLS checklist remains future work pending authorization.
- Pre-existing inbox item stuck at `STALLED_YOUTUBE_PROCESSING` (video from 2026-10-20) — untouched.
- Friends' private-YouTube processing is blocked by single-account credential design — plan doc D10 records the boundary; no insecure shortcut.
- Vite >500 kB chunk advisory (non-blocking). Supabase Free projects pause after inactivity; free-plan backups need manual export. Free tier is a ceiling, not a billing guarantee.
- Legacy `dispatch/sync/` + `dispatch/transport/` retained only because legacy tests import them.
- Server currently LAN-exposed (see §3) — revert to loopback when phone testing is done.

## 11. Recovery instructions
1. Read this file, then the plan doc, then relevant linked doc. 2. `git status --short; git branch --show-current; git rev-parse HEAD` and compare with §3. 3. If results in §8 may be stale (code changed), rerun the relevant suite. 4. Inspect uncommitted changes before touching them; never discard or overwrite them. 5. Continue from the first incomplete item in plan doc §3. Do not trust "intended" claims — verify.

## 12. Next action
Implement plan-doc **Phase D (Worker)**: create `cloud-worker/` (TypeScript) implementing `reserve/complete/url/delete/quota` + **review transitions (approve/reject with revision+status checks — clients have no write grants, so remote approve/reject MUST be Worker routes)** + scheduled cleanup, with the reserve/complete routes executing the **D4 atomic Postgres contract** (one transaction: `FOR UPDATE` on `profiles` → usage sums incl. `expires_at > now()` filter → caps → insert; atomic complete with real byte verification) and unit tests with a mocked R2 binding (`npm test`). Status/credentials per §9: Worker holds R2 binding + service-role key; PC (Phase E) only gets `DISPATCH_WORKER_URL`/`DISPATCH_WORKER_TOKEN`. Live verification of Phase C (two-user RLS checklist against a real project) happens only when the owner authorizes and runs it (§8 division of labor). Standing rule: the agent never starts/stops Docker, pulls images, runs Supabase CLI, links/initializes projects, applies or resets migrations, touches remote databases, deploys Workers, or creates billable resources — it finishes code first, states the exact needed command + impact, and waits for the owner's output. Never claim database-backed verification passed until its real output confirms it. Never put privileged Supabase credentials in the PC app, browser, Android, test fixtures, or Git.
