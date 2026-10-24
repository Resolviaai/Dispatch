# Bounded Hybrid Clip Editing + Dynamic Quotas — Implementation Notes (2026-10-25)
Status: implemented, tests green, NOT deployed to production Supabase/Worker, NOT committed (owner review).

## What was built (this session, uncommitted)
1. **Migration `20261025000009_edit_windows.sql`** (not yet applied — owner runs or authorizes): `edit_windows` (per-user, per-clip source-relative window bounds, status staging/ready/failed/expired), `window_transcripts` (source-relative word timings, JSONB), `cloud_mark_window_ready/_failed/_put_transcript/_get_transcript/_find_covering_window` RPCs (service-role only), `cloud_set_user_quota` (dynamic per-user quota in integer bytes; refuses allocations that would push the SUM of all quotas past the 10,000,000,000-byte global budget), `media_objects.kind` extended with `'edit_window'`.
2. **`dispatch/edit_windows.py`** — pure window math (60 s context, clamped, 300 s cap, centred-on-selection when capping), reuse rules (cover check, combined-span ≤ 300 s — nearby suggestions share a window only when the combined span fits), uncovered-range computation for extensions, word re-anchoring (source→window-local), proxy ffmpeg cmd (720p30, 2500k target, duration/orientation/audio preserved), `stage_edit_window` pipeline (proxy → ffprobe verify → audio extract → transcribe with `max_input_seconds=300` → cache SOURCE-relative words → quota-gated upload via MediaStore `upload_and_finalize` → mark ready; failure → mark failed + re-raise, nothing left ready).
3. **Stage 2 separation:** `transcribe_stage2_precision_words(media_path, max_input_seconds=None)` — final clips keep `MAX_CLIP_DURATION + 16`; edit windows pass `300`. Full-source transcription still impossible.
4. **Worker:** `edit_window` added to kinds + caps; admin quota route unified into `/admin/users/:id/{ban,unban,quota}` (decimal-GB input → integer bytes, 0 < gb ≤ 10).
5. **Web admin:** per-row quota input (decimal GB) + Set button; status feedback.
6. **Tests:** `tests/test_edit_windows.py` **19/19** (window math incl. cap-centering, reuse/combined-span, uncovered ranges, word re-anchoring, Stage 2 separation, proxy verification incl. duration/audio rejection, staging happy/failure paths with faked ffmpeg/store/db).

## NOT built (honest)
- Production Supabase/Worker deployment of 0009 + worker (owner authorization step).
- Phone-side editor consumption of staged windows (signed-URL fetch in ClipsScreen) — the staging API is ready for it.
- "Load More" app UI action (backend `compute_extension_window` + RPC ready).
- Admin quota UI reaches the Worker; PC-side `/api/admin/*/quota` mirror not added (web admin is canonical).

## Test evidence (exact)
- `python -m unittest tests.test_edit_windows` → **19/19 OK**
- Combined scoped battery (edit_windows, vault/remote-jobs, cloud config/quota, admin, auth session, phase1 security, migration hygiene) → **71/71 OK**
- Worker: tsc clean, **24/24** · frontend `npm test`/`npm run build` exit 0 · `git diff --check` clean
