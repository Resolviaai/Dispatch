# Route B Implementation Handoff

**Objective:** Make this workflow work end to end: normal phone camera → manual upload to YouTube → paste video link into Dispatch → download → transcription → AI highlight detection → FFmpeg captioned clips → existing review dashboard. Route B must work independently of the unfinished Android camera module (Route A).

**Date started:** 2026-10-22
**Branch:** `main`
**Starting commit SHA:** `be0629bf56caed61b8c8030e4c6fd72f3d395dd9`
**Working tree at start:** clean
**Status:** Code complete; targeted tests 22/22 green; full-suite triage in progress (6 failures, believed pre-existing — verification pending). Not committed (per instructions).

---

## Architecture decision (traced, not redesigned)

The full pipeline already exists and consumes DB state regardless of how a video was discovered:

1. `POST /api/youtube/ingest` (`dispatch/web/app.py`) — auth via `verify_dashboard_auth`; extracts video ID; checks `is_ingestion_in_flight(video_id)` BEFORE spawning a thread (truthful duplicate handling); spawns background thread running `YouTubeInboxCatcher.ingest_video()`; returns `{"status": "processing_queued"}` or `{"status": "already_processing", "duplicate": true}`; 400 on invalid URL.
2. `YouTubeInboxCatcher.ingest_video()` (`dispatch/youtube_inbox/catcher.py`) — single-flight per-video guard (module-level `_INGEST_INFLIGHT` set + lock, acquired/released in `finally`), run body moved to `_ingest_video_locked()`. DISCOVERED → DOWNLOADING (yt-dlp) → VALIDATING (ffprobe) → **DOWNLOADED**. Stops there per Pillar 1 contract. DB idempotency skip for already-DOWNLOADED/COMPLETED rows with a valid local file.
3. `TranscriptionWorker` (`dispatch/transcription/worker.py`, polls 15 s) — DOWNLOADED → Gemini Stage 1 / YouTube captions / Whisper → TRANSCRIBED.
4. `HighlightWorker` (`dispatch/ai_clips/worker.py`, polls 15 s) — TRANSCRIBED → Gemini highlights → CLIPS_DEFINED → FFmpeg render → clips `ready_review` → COMPLETED.
5. UI entry point: **Sessions tab** (`frontend/src/screens/RecordingsScreen.tsx`) — user decision made mid-task: the link-entry UI lives in the Sessions screen, NOT the old PC dashboard template (`dispatch/web/templates/index.html` left untouched). "Import from YouTube" card posts to `/api/youtube/ingest` with `getAuthHeaders()`; polls `/api/youtube/inbox` every 5 s; imported sessions listed with statuses Queued → Processing… → Clips Ready → Failed/Stalled (with `last_error` + Retry). PC Studio/Clips screens already poll clips continuously, so rendered clips appear without restart.
6. Dispatch daemon (`dispatch/main.py`) runs all workers automatically; no worker changes were needed.

**Concurrency contract (explicit):**
- One shared per-video guard in `catcher.py` used by BOTH manual imports and the poller (poller calls the same `ingest_video()`).
- API pre-checks the guard before launching a thread → truthful `already_processing` vs `processing_queued`.
- Guard slot is released in `finally` (including exceptions). Residual check-then-spawn race is bounded: worst case an extra no-op thread returns ALREADY_INGESTING; a duplicate download can never start.
- **Known limit (documented, not hidden):** the guard is process-local (in-memory). Correct for the single-process dispatch daemon; NOT a database-level guarantee across multiple processes/servers. Cross-restart duplication is bounded by the DB idempotency skip (valid local file + terminal status → skip).

## Files changed

| File | Change |
|---|---|
| `dispatch/youtube_inbox/catcher.py` | Split `ingest_video` into public wrapper (validation, DB idempotency skip, single-flight guard w/ `finally` release) + `_ingest_video_locked` (unchanged run body); added `is_ingestion_in_flight()` helper; added `threading` import. |
| `dispatch/web/app.py` | Import `is_ingestion_in_flight`; endpoint pre-check returning distinct `already_processing`/`duplicate: true` status. No other endpoint/pipeline changes. |
| `frontend/src/screens/RecordingsScreen.tsx` | Route B entry point: "Import from YouTube" card (URL input + Enter/submit, 44px hitboxes, in-flight disable, truthful queued/already_processing/error feedback mirroring server validation), imported-sessions list polling `/api/youtube/inbox` (5 s) with Queued/Processing…/Clips Ready/Failed-Stalled badges, `last_error` display (safe text rendering), Retry Import (re-POSTs same video). Distinguishes imported items from native recordings by excluding video IDs already present on native recording segments. |
| `tests/test_youtube_ingest_endpoint.py` | NEW: 6 regression tests through the real endpoint/real guard (network boundary mocked only). |
| `docs/ROUTE_B_IMPLEMENTATION_HANDOFF.md` | This file. |
| `docs/DISPATCH_FUTURE_TASKS.md` | NEW: deferred out-of-scope tasks. |

## Live checklist

- [x] Read AGENTS.md, PROGRESS.md, docs/EXECUTION_HANDOFF.md, current code; verified branch/SHA/clean tree.
- [x] Baseline targeted tests: 16/16 OK.
- [x] Single-flight guard in catcher (root cause; shared by endpoint threads + poller; `finally` release).
- [x] Endpoint pre-check → distinct truthful statuses (`processing_queued` vs `already_processing`).
- [x] Sessions-tab UI (user decision): import form + live imported-session statuses + failure visibility + retry.
- [x] Focused regression tests (real endpoint, real guard): invalid URL 400; valid → ingestion runs with submitted URL; concurrent duplicates refused at catcher level AND via endpoint (`already_processing`); download failure → FAILED + `last_error` visible via `/api/youtube/inbox`; re-import of downstream-COMPLETED video → no re-download.
- [x] Targeted suite: `python -m unittest tests.test_youtube_ingest_endpoint tests.test_youtube_inbox tests.test_youtube_processing_tracker tests.test_web` → **22/22 OK**.
- [x] Frontend type-check: `npx tsc --noEmit` → RecordingsScreen clean; 1 PRE-EXISTING error in `src/camera/cameraInteractionState.ts` (untouched file, committed before this task).
- [x] Full suite `python -m unittest discover -s tests -p "test_*.py"` → **371 tests: 3 failures + 3 errors — ALL triaged as pre-existing/flaky, none caused by Route B** (proof below).
- [x] Real-server smoke test: uvicorn boot of the actual app → GET / 200; GET /api/youtube/inbox 200; POST invalid URL → 400.
- [x] Runtime verification of what is possible; exact remaining manual check documented below.
- [x] `docs/DISPATCH_FUTURE_TASKS.md` created (deferred: Route A, SaaS, R2, multi-platform publishing).
- [x] Final diff inspection: 3 modified files (minimal, intentional) + 3 new files; no unintended changes; no deletions.

## Commands executed & results

| Command | Result |
|---|---|
| `git branch --show-current && git rev-parse HEAD && git status --porcelain` | `main`, `be0629b…`, clean |
| `.venv/Scripts/python.exe -m unittest tests.test_youtube_inbox tests.test_youtube_processing_tracker tests.test_web -v` | 16/16 OK (baseline) |
| `.venv/Scripts/python.exe -m unittest tests.test_youtube_ingest_endpoint -v` | 1 failure (test-thread leaked across patch contexts) → fixed by waiting on guard release in test 4 |
| `.venv/Scripts/python.exe -m unittest tests.test_youtube_ingest_endpoint tests.test_youtube_inbox tests.test_youtube_processing_tracker tests.test_web -v` | **22/22 OK** (6.1 s) |
| `cd frontend && npx tsc --noEmit` | 1 pre-existing error in `src/camera/cameraInteractionState.ts(66,14)`; no errors in changed files |
| `.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"` | **371 tests: 3 failures + 3 errors — all pre-existing/flaky (see triage table)** |
| Clean-tree stash rerun of failing modules | Same failures reproduce without Route B changes; stash popped, changes intact |
| Real-server smoke test (uvicorn 127.0.0.1:8901) | GET / 200; GET /api/youtube/inbox 200; POST invalid URL → 400 `Invalid YouTube URL or Video ID` |

## Full-suite failures (final: 371 tests, 3 failures + 3 errors — all pre-existing/flaky)

Verified by `git stash push` of the three modified files → rerun on clean tree → `git stash pop` (changes restored intact):

| Test | Cause | Pre-existing? |
|---|---|---|
| `test_end_to_end` (ERROR) | `ModuleNotFoundError: No module named 'edge_tts'` — missing dep in venv | Yes (environment) |
| `e2e test_f13_all_test_modules_importable` (FAIL) | Same `edge_tts` import failure of `tests/test_end_to_end.py` | Yes (cascade) |
| `test_gemini_two_stage` ×2 (ERROR) | `ModuleNotFoundError: No module named 'google.genai'` — missing dep in venv | Yes (reproduced on clean tree) |
| `test_youtube_caption_timing_fix.test_2` (FAIL) | ASS dialogue line-wrapping assertion (`\\NCAPTION.` split) in untouched caption renderer | Yes (reproduced on clean tree) |
| `test_job_queue.test_job_queue_and_recovery` (FAIL) | Same-second job-ID FIFO ordering race; code untouched; passed on clean-tree rerun | Flaky, unrelated |

None of the failures touch `catcher.py`, `web/app.py`, or `RecordingsScreen.tsx`. No tests were weakened or skipped.

## Runtime environment observed

- Python 3.11.15 (venv), yt-dlp 2026.08.19, FFmpeg 8.0.1 in PATH.
- `.env`: `GEMINI_API_KEY` set; `youtube_token.json` + `client_secrets.json` exist.
- yt-dlp cookies (`DISPATCH_YTDLP_COOKIES_FILE` / `DISPATCH_YTDLP_COOKIES_BROWSER`) NOT configured → unauthenticated download works only for public/unlisted videos. Private-video access, if needed, requires cookies config (documented; not rebuilt). An **unlisted** test video is acceptable for the first real E2E run.

## Verification classification (honest)

- **Mocked integration (verified):** endpoint contract, guard concurrency, DB state transitions, failure visibility, idempotent re-import — all via `tests/test_youtube_ingest_endpoint.py` + existing suites.
- **NOT yet verified (needs a human with a real video):** a real download → transcription → Gemini highlights → FFmpeg render → review-dashboard run. Exact manual check: start the daemon (`python -m dispatch.main`), paste an accessible (unlisted) YouTube URL into Sessions → Import, wait for processing, confirm a captioned vertical clip appears in the review dashboard and plays. Never claim this passed without executing it.

## Known blockers / decisions

- UI entry moved to Sessions tab per user decision (mid-task); PC dashboard template untouched.
- In-memory guard is single-process only (documented above).
- No new dependencies, no schema migrations, no downstream processing changes.

## Next recommended task

The single remaining step is the real E2E manual check: start the daemon (`python -m dispatch.main`), open the Sessions tab, paste an accessible (unlisted) YouTube URL into "Import from YouTube", wait through download → transcription → highlights → render, and confirm a captioned vertical clip appears in the review dashboard and plays. If the video is private, first configure yt-dlp cookies (`DISPATCH_YTDLP_COOKIES_FILE` or `DISPATCH_YTDLP_COOKIES_BROWSER`) — do not assume the PC's YouTube OAuth token authorizes yt-dlp.
