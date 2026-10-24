# ZCode Audit Report — 2026-10-24 (verified against HEAD `fffae6e`)

Auditor: ZCode. Method: every external finding (Muse audit of `4c1aa8a`, Claude re-review, ChatGPT synthesis) re-verified against the CURRENT tree before inclusion. **No code was edited** — this report only. Status labels: CONFIRMED (verified in this tree), PARTIALLY TRUE, NOT REPRODUCED, or FIXED since `4c1aa8a`.

## Snapshot reconciliation
- Muse audited `4c1aa8a` (Oct 20, "feat(editor): separate text and caption styling"). That commit IS in our `main` history — the audit is legitimate, just 59+ commits behind.
- HEAD is `fffae6e` (0008 grants fix, live-applied). Claude's re-review at `fffae6e` matches our tree.
- Per owner directive, the college-PC experiment (deleted, never pushed) is ignored; it was NOT `4c1aa8a`.

## P0 — CONFIRMED, with production impact on THIS PC
### 1. Real `.env` GEMINI_API_KEY overwritten by the test suite — CONFIRMED WITH LIVE IMPACT
- `POST /api/integrations/gemini` (`dispatch/web/app.py:2225`) writes `GEMINI_API_KEY=…` into the **real** `ROOT_DIR/.env`.
- `tests/test_phase1_security.py::test_gemini_key_injection_prevention` posts a valid-format key (`AIzaSyDas-UU7agAjyQeHDag1yOMU6qyXuxIWBE`) which the handler **accepts and writes**; the test never redirects the env path.
- **Verified impact:** hash comparison proves the current `.env` GEMINI_API_KEY **equals the committed test key** — the owner's real key has been overwritten by past suite runs (mine included).
- Owner actions: (1) create a fresh key at aistudio.google.com and paste into `.env` (the old one is unrecoverable from here); (2) revoke `AIzaSyDas-UU7agAjyQeHDag1yOMU6qyXuxIWBE` in Cloud Console if it is a real key — it sits in a PUBLIC repo's history; (3) then fix root cause (configurable env path or test-scoped ROOT_DIR).

### 2. Committed Google API key in a public repo — CONFIRMED
- Exactly one occurrence: `tests/test_phase1_security.py:45`, in tree and history (since Oct 20, `4c1aa8a`). Repo is public (anonymous clone works). Validity unprobed deliberately — treat as live until revoked. History purge = force-push, owner-authorized only.

## P1 — CONFIRMED
### 3. Gemini key rotation never reaches the worker gate — CONFIRMED
- `dispatch/ai_clips/worker.py:19` imports `GEMINI_API_KEY` at module level; the `:481` gate checks the stale binding, while `:537` re-reads `os.getenv` fresh — inconsistent. Dashboard key rotation fails until process restart.
### 4. Legacy LAN upload handoff still mounted; `/upload/direct` returns unconditional `verified: True` — CONFIRMED (one correction)
- Router mounted: `dispatch/web/app.py:140` (`app.include_router(sync_router)`). Handler `dispatch/sync/receiver.py:876` streams arbitrary multipart bytes with **no size cap and no expected hash** (computes its own hash = TOFU), registers a chunk, enqueues the pipeline. **Correction to Muse:** the response's `verified: True` is hardcoded in the return even though the internal DB probe may mark the chunk unverified — the phone-facing verdict is unconditional. Auth IS required (`verify_token`), so the actor is any paired device, not "anyone".
### 5. RemoteJobPoller drops user identity — CONFIRMED (own code)
- `dispatch/youtube_connections.py` `RemoteJobPoller._process` calls `ingest_video(job.youtube_url)` — `job.user_id`/`job_id` are dropped; the inbox keys by global video_id. Blocks safe multi-user cloud sync; needs a job→source→clip ownership design (per handoff §33).
### 6. Three WorkManager unique names — CONFIRMED
- `dispatch_youtube_uploads` (DispatchApplication:29), `dispatch_youtube_uploads_after_boot` (BootReceiver:28), `DispatchYouTubeUploadWorker` (SegmenterEngine:337) → three workers can drain the same segments concurrently.
### 7. Ack secret doubles as bearer token + HMAC key over cleartext — CONFIRMED
- `receiver.py`: `youtube-ack` uses `verify_token(auth_token)` where the token **is** `get_auth_token()`, and the same value is the HMAC key (line 619). Default HTTP binding → any LAN observer of one ack request can forge deletion-enabling receipts.

## PARTIALLY TRUE / NOT REPRODUCED
### 8. "No timeouts" — PARTIALLY TRUE
- yt-dlp: CONFIRMED — 3 `YoutubeDL` sites (`catcher.py:89,120,206`), no `socket_timeout` in opts.
- ffmpeg/ffprobe: **NOT REPRODUCED** — `validator.py` (30s/120s/60s) and `renderer.py`/`thumbnail.py` (300s/30s) all have explicit `timeout=`. Muse's count of 5 timeout-less ffmpeg calls does not match this tree.
### 9. Test `.env` pollution — CONFIRMED (upgraded to P0, see finding 1).
### 10. Editor dirty-tracking P0s — NOT RE-VERIFIED in this pass (requires the 3-pane workstation UI code from `4c1aa8a`+; Claude's re-review at `fffae6e` reports the failed-save-exit fixed and delete/duplicate dirty-marking still open). Needs a focused re-check before fixing.

## Supabase state (live, this session)
- Migration `0008` grants fix live; all client grants are SELECT-only; adversarial probes → 42501 on `youtube_connections`/`oauth_states`/`staging_watch`/`processing_jobs`; `on_auth_user_created` trigger present. Clean.

## Corrected priorities (audit-only; no work performed)
1. Owner: re-create Gemini key → `.env`; revoke the committed key in Cloud Console.
2. Fix the gemini-key handler to write an isolated env path (or test-scoped ROOT_DIR) + regression test.
3. Key-rotation fix (worker reads env at call time).
4. Retire or restrict `/upload/direct` (YouTube-only architecture violation) + split ack secret from bearer token.
5. One WorkManager name; per-item try/except + timeouts in yt-dlp; watchdog.
6. RemoteJobPoller ownership mapping before cloud review sync.
