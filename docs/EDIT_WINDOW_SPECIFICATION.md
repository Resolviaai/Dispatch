# Dispatch Temporary Edit Window (Gemini Suggest → Staged Proxy → Full-Access Edit Raw) Engineering Specification

> **Status:** Implemented & Locked (v1.7.48-alpha / versionCode 116; 0032 migration lands with this spec)
> **Target Viewport:** Android Native Web-Studio (cloud mode) & Desktop Review Studio (PC mode)
> **Feature naming:** the owner brief calls the three pillars **Gemini Suggest**, **Temporary Edit Window**, and **Full-Access Edit Raw**. In code the user-facing entry points are the `Edit Window: {paddedStart}s - {paddedEnd}s` chip ([CaptionTimeline.tsx:2780](file:///c:/CODE/Dispatch/frontend/src/captions/CaptionTimeline.tsx#L2780)), the `Edit window` row in Cloud Files ([CloudFilesSheet.tsx:176](file:///c:/CODE/Dispatch/frontend/src/components/CloudFilesSheet.tsx#L176)), and the canonical raw-source serving route (`GET /api/clips/{id}/source`).
> **Source Modules:**
> - Pure window math & staging pipeline: [`dispatch/edit_windows.py`](file:///c:/CODE/Dispatch/dispatch/edit_windows.py)
> - Canonical raw-source serving (PC): [`dispatch/web/app.py`](file:///c:/CODE/Dispatch/dispatch/web/app.py) (`resolve_canonical_source_asset`, `GET /api/clips/{clip_id}/source`)
> - Cloud staging & worker transport: [`dispatch/cloud/sync.py`](file:///c:/CODE/Dispatch/dispatch/cloud/sync.py) (`sync_clip_to_cloud` Mode 2)
> - Control plane & hydration: [`cloud-worker/src/index.ts`](file:///c:/CODE/Dispatch/cloud-worker/src/index.ts) (`/sync/pc/windows`, `GET /clips`, `GET /clips/:id/source-url`)
> - Suggestion engine: [`dispatch/ai_clips/highlight_finder.py`](file:///c:/CODE/Dispatch/dispatch/ai_clips/highlight_finder.py) (`extract_highlights_windowed`) + [`dispatch/ai_clips/worker.py`](file:///c:/CODE/Dispatch/dispatch/ai_clips/worker.py) (`HighlightWorker`)
> - Phone consumption: [`frontend/src/screens/ClipsScreen.tsx`](file:///c:/CODE/Dispatch/frontend/src/screens/ClipsScreen.tsx)
> - Android render source: [`android/app/src/main/java/com/resolvia/dispatch/service/RenderForegroundService.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/service/RenderForegroundService.kt) + [`AndroidClipRenderer.kt`](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/renderer/AndroidClipRenderer.kt)
> - Database: [`supabase/migrations/20261025000009_edit_windows.sql`](file:///c:/CODE/Dispatch/supabase/migrations/20261025000009_edit_windows.sql), [`...0011_user_ownership_and_edit_windows.sql`](file:///c:/CODE/Dispatch/supabase/migrations/20261025000011_user_ownership_and_edit_windows.sql), [`...0021_edit_window_render_fingerprint.sql`](file:///c:/CODE/Dispatch/supabase/migrations/20261028000021_edit_window_render_fingerprint.sql), [`...0032_wave2b_straggler.sql`](file:///c:/CODE/Dispatch/supabase/migrations/20261113000032_wave2b_straggler.sql)

---

## 1. Executive Summary & Purpose

Dispatch edits **the same clip from three runtimes**: the PC daemon (FFmpeg), the phone web-studio (React), and Android Media3. The raw recording never leaves the PC. To let the creator keep editing when the PC is unreachable, Dispatch stages a **Temporary Edit Window**: a bounded 720p30 proxy of the source recording around the clip's selection, uploaded to Cloudflare R2 and registered in Supabase so the phone (and Android render) always have a playable, timebase-consistent source.

The three pillars, kept strictly separate ([edit_windows.py:1-19](file:///c:/CODE/Dispatch/dispatch/edit_windows.py#L1)):

1. **Source Recording** — the full original, stays on PC disk. Never uploaded to R2. Served to LAN/local clients via the canonical raw-source route.
2. **Temporary Edit Window** — a bounded proxy (≤ 300 s span) staged lazily into R2; distinct from final clips; counts against the same quota kernel as `kind='edit_window'`.
3. **Selected Clip** — the editor's source-time selection (5–180 s). The Gemini suggestion is stored separately (`ai_suggested_start_time` / `ai_suggested_end_time`) and **never rewritten by trims**.

**Gemini Suggest** is not a phone-triggered endpoint. It is the autonomous `HighlightWorker` pipeline on the PC daemon (`TRANSCRIBED → ANALYZING → CLIPS_DEFINED`, [main.py:172](file:///c:/CODE/Dispatch/dispatch/main.py#L172), [worker.py:242](file:///c:/CODE/Dispatch/dispatch/ai_clips/worker.py#L242)) that scans windowed slices of the transcript, proposes clip candidates, and persists them; the Worker hydrates them into cloud clip rows.

**Full-Access Edit Raw** is the guarantee that every runtime can reach the *canonical raw source* (PC route) or its bounded proxy (Worker route) with explicit window bounds — never a trimmed/rendered derivative — so trims and caption edits are always made against the original recording's timeline.

---

## 2. High-Altitude Architecture

```
+-------------------- PC DAEMON (owner's desk) ----------------------+
| HighlightWorker: TRANSCRIBED -> ANALYZING -> CLIPS_DEFINED         |
|   extract_highlights_windowed (720 s windows / 90 s overlap)       |
|   Gemini candidate extraction -> dedup (IoU > 0.45 | contain 0.45) |
|   clip rows: start_time/end_time + ai_suggested_start/end_time     |
|                                                                    |
| sync_clip_to_cloud Mode 2 (staging):                               |
|   resolve_canonical_source_asset (never the trimmed video_path)    |
|   compute_window(sel +- 60 s context, <= 300 s span)               |
|   FFmpeg proxy 1280x720 (or 720x1280) @30fps 2500k + faststart     |
|   ffprobe_validate_proxy (duration +-1.0 s, aspect delta <= 0.06)  |
|   window audio -> cached words rebased SOURCE-relative             |
+--------------------------+-----------------------------------------+
                           | /reserve -> R2 PUT -> /complete
                           | /sync/pc/windows (idempotent registration)
+--------------------------v-----------------------------------------+
| CLOUDFLARE WORKER (control plane only — no media processing)       |
|   edit_windows rows (staging -> ready | failed)                    |
|   window_transcripts (words SOURCE-relative)                       |
|   superseded-proxy reclamation (advisory-lock + R2 delete)         |
|   GET /clips hydration: edit_window_url, edit_window_start/end,    |
|     caption seeds window-relative (source:"window_seed")           |
|   GET /clips/:id/source-url: edit_window -> clip_video -> first    |
+----------+-----------------------------+---------------------------+
           |                             |
+----------v----------+       +----------v---------------------------+
| PHONE WEB-STUDIO    |       | ANDROID (Route C render)             |
| <video> src chain:  |       | RenderForegroundService.stageSource  |
|  source (PC route)  |       |  Video -> GET /clips/:id/source-url  |
|  -> edit_window_url |       |  -> cache/source_videos/{id}.mp4     |
|  -> rendered url    |       | AndroidClipRenderer (Media3) renders |
| captions: edited or |       | from the downloaded source asset     |
|  window_seed words  |       +--------------------------------------+
+---------------------+
```

---

## 3. Locked Constants & Contracts

| Constant | Value | Authority |
| --- | --- | --- |
| `MAX_WINDOW_SECONDS` | `300.0` | [edit_windows.py:33](file:///c:/CODE/Dispatch/dispatch/edit_windows.py#L33) |
| `CONTEXT_SECONDS` (initial window) | `60.0` before/after selection | [edit_windows.py:34](file:///c:/CODE/Dispatch/dispatch/edit_windows.py#L34) |
| `MIN_SELECTION_SECONDS` / `MAX_SELECTION_SECONDS` | `5.0` / `180.0` (final-clip product limit) | [edit_windows.py:35](file:///c:/CODE/Dispatch/dispatch/edit_windows.py#L35) |
| `MAX_WINDOW_TRANSCRIBE_SECONDS` | `300.0` (separate from `MAX_CLIP_DURATION`) | [edit_windows.py:40](file:///c:/CODE/Dispatch/dispatch/edit_windows.py#L40) |
| Proxy geometry | `1280x720` or `720x1280` (orientation-following), `30` fps, `2500k` video + `128k` AAC, `+faststart` | [edit_windows.py:42](file:///c:/CODE/Dispatch/dispatch/edit_windows.py#L42), [build_proxy_cmd](file:///c:/CODE/Dispatch/dispatch/edit_windows.py#L215) |
| Proxy validation | duration within ±1.0 s of requested span; aspect delta ≤ 0.06 vs source; missing audio rejected | `ffprobe_validate_proxy` (call site [edit_windows.py:303](file:///c:/CODE/Dispatch/dispatch/edit_windows.py#L303)) |
| Window reuse rule | reuse only when the window `covers()` the wanted range (1e-6 tolerance) **and** the combined span fits 300 s | [covers](file:///c:/CODE/Dispatch/dispatch/edit_windows.py#L83), [combined_span_fits](file:///c:/CODE/Dispatch/dispatch/edit_windows.py#L87) |
| Quota caps (`kind='edit_window'`) | reserve **512 MiB**; `/complete` per-kind caps: `clip_video` 512 MiB, `edit_window` 512 MiB, everything else 8 MiB | [0011](file:///c:/CODE/Dispatch/supabase/migrations/20261025000011_user_ownership_and_edit_windows.sql), [0032](file:///c:/CODE/Dispatch/supabase/migrations/20261113000032_wave2b_straggler.sql) |
| Registration idempotency | sibling window match tolerance ±0.005 s, oldest-first | [index.ts `/sync/pc/windows`](file:///c:/CODE/Dispatch/cloud-worker/src/index.ts) |
| Word coordinate contract | `window_transcripts.words` are **SOURCE-relative seconds**, always | [0009](file:///c:/CODE/Dispatch/supabase/migrations/20261025000009_edit_windows.sql#L31) |

Illustrative quota shape: a 60 s window proxy at 2500 kbps + 128 kbps audio lands near ~20 MB; a full 300 s window near ~100 MB — i.e. roughly **4–6 staged windows can consume a default 2 GB per-user quota**. Quota is enforced server-side at `/reserve` (per-user + global caps, advisory lock `918273645`) and re-checked at `/complete` including the still-present staging duplicate.

---

## 4. Gemini Suggest Pipeline (suggestion → clip rows)

1. **Trigger:** autonomous. `HighlightWorker` consumes `TRANSCRIBED → ANALYZING → CLIPS_DEFINED` ([main.py:172](file:///c:/CODE/Dispatch/dispatch/main.py#L172)); no HTTP suggest route exists on the PC (`/suggest` not found in `dispatch/web/app.py`) and no frontend component calls one.
2. **Windowed extraction:** `extract_highlights_windowed(segments, total_duration, negative_context, api_key, candidate_models, window_duration=720.0, window_overlap=90.0)` ([highlight_finder.py:1481](file:///c:/CODE/Dispatch/dispatch/ai_clips/highlight_finder.py#L1481)). Sources ≤ 720 s run single-pass; longer sources step by 630 s (720 − 90) ([highlight_finder.py:1517](file:///c:/CODE/Dispatch/dispatch/ai_clips/highlight_finder.py#L1517)).
3. **Dedup:** a candidate is suppressed when it overlaps an accepted one with **IoU > 0.45 or containment > 0.45** ([highlight_finder.py:1120](file:///c:/CODE/Dispatch/dispatch/ai_clips/highlight_finder.py#L1120)). ⚠ The function docstring still says 0.5 — the code (0.45) is authoritative.
4. **Storage:** suggestions persist as clip rows with `start_time`/`end_time` (the editable selection) plus `ai_suggested_start_time`/`ai_suggested_end_time` (immutable provenance). The Worker hydrates both into `GET /clips` payloads ([index.ts:5355](file:///c:/CODE/Dispatch/cloud-worker/src/index.ts#L5355)).
5. **Trim independence:** trims rewrite `start_time`/`end_time` and the EDL; they never touch `ai_suggested_*` (declared in [ClipsScreen.tsx:254](file:///c:/CODE/Dispatch/frontend/src/screens/ClipsScreen.tsx#L254) and [studio/types.ts:28](file:///c:/CODE/Dispatch/frontend/src/studio/types.ts#L28)).

**Locked rule:** the suggestion is *provenance*, not *state*. Deleting or re-cutting a clip never mutates the suggested range, so "restore to AI suggestion" remains possible and audits can always answer "what did Gemini pick, and what did the human change".

---

## 5. Window Derivation & Staging Pipeline

### 5.1 Window derivation (deterministic, no AI)

`_resolve_edit_window_bounds` ([sync.py:396](file:///c:/CODE/Dispatch/dispatch/cloud/sync.py#L396)) resolves bounds in tier order:

1. `compute_window(sel_start, sel_end, source_duration)` — up to ±60 s context around the selection, clamped to source bounds, then capped to 300 s keeping the selection centred as far as the clamp allows ([edit_windows.py:50](file:///c:/CODE/Dispatch/dispatch/edit_windows.py#L50)).
2. Clip-stored `edit_window_start`/`edit_window_end` (injected by `_populate_edit_window_bounds`, [app.py:950](file:///c:/CODE/Dispatch/dispatch/web/app.py#L950)) when the source probe is unavailable.
3. Legacy `padded_start`/`padded_end` fallback.

The same bounds are recomputed and re-injected into `GET /api/clips` ([app.py:1198](file:///c:/CODE/Dispatch/dispatch/web/app.py#L1198)), `GET /api/clips/{clip_id}` ([app.py:1302](file:///c:/CODE/Dispatch/dispatch/web/app.py#L1302)) and trim responses ([app.py:1801](file:///c:/CODE/Dispatch/dispatch/web/app.py#L1801)) so every client sees identical window bounds for identical clip state.

### 5.2 Staging (PC → R2, `stage_edit_window` [edit_windows.py:262](file:///c:/CODE/Dispatch/dispatch/edit_windows.py#L262))

1. **Validate span** ≤ 300 s (+1e-6), else `ValueError` before any I/O.
2. **Create window row** (`status='staging'`) via `db_ops.create_window`; on cloud path this is a deterministic client id `ew_{clip_id}_{ws_ms}_{we_ms}` ([sync.py:508](file:///c:/CODE/Dispatch/dispatch/cloud/sync.py#L508)) — registration is idempotent.
3. **Encode proxy** with input-seeking (`-ss` before `-i`) from the **canonical raw source** (`resolve_canonical_source_asset` [app.py:871](file:///c:/CODE/Dispatch/dispatch/web/app.py#L871) — never the trimmed `video_path`), orientation-aware 1280×720 / 720×1280.
4. **Validate proxy** (duration ±1.0 s, aspect delta ≤ 0.06, audio present). Failure → `mark_failed` → rethrow.
5. **Transcribe window audio** (MP3 extract from raw source when present, else from the proxy) and **rebase words to SOURCE-relative** (`window_start + local`), [edit_windows.py:308](file:///c:/CODE/Dispatch/dispatch/edit_windows.py#L308). On the cloud path transcription is *cached-words-only*: `_window_transcribe` filters the clip's caption words to the window span ([sync.py:981](file:///c:/CODE/Dispatch/dispatch/cloud/sync.py#L981)); when both fresh words and the cached fallback are empty the window still stages **ready with `transcript_status='missing'`** ([sync.py:512](file:///c:/CODE/Dispatch/dispatch/cloud/sync.py#L512), [edit_windows.py contract test](file:///c:/CODE/Dispatch/tests/test_edit_windows.py)).
6. **Quota-gated upload:** `/reserve` (`kind='edit_window'`, exact byte count) → R2 PUT → `/complete` ([_WorkerEditWindowMediaStore](file:///c:/CODE/Dispatch/dispatch/cloud/sync.py#L427)). Actual bytes are verified against the reservation.
7. **Register:** `/sync/pc/windows` marks `status='ready'` (`cloud_mark_window_ready`), stores words (`cloud_put_window_transcript`), and **reclaims superseded proxies**: when a re-stage replaces the window a sibling row pointed at, the orphaned R2 object is deleted via `cloud_mark_deletion` → R2 delete → `cloud_confirm_deletion` — only when no other live window still references it (the 1.2 GB / 37-orphan leak fix).

### 5.3 Failure semantics

Any exception after `create_window` → `db_ops.mark_failed(window_id, error)` → rethrow ([edit_windows.py:336](file:///c:/CODE/Dispatch/dispatch/edit_windows.py#L336)). On the cloud path `mark_failed` logs locally; the Worker-side row transitions to `failed` only via the registration call failing, so a PC crash mid-stage can leave a `staging` row — reclaimed when the next registration for the same bounds reuses it. (`/reserve`-time kind caps and `/complete` per-kind caps are aligned by [0032](file:///c:/CODE/Dispatch/supabase/migrations/20261113000032_wave2b_straggler.sql); before it, any `edit_window` proxy > 8 MiB uploaded successfully and then failed `/complete`.)

---

## 6. Coordinate Systems & Word Contract

Dispatch carries **four** time coordinates. The locked rule: *transcripts are stored SOURCE-relative; window-relative exists only as a transport encoding*.

| Coordinate | Origin | Used by |
| --- | --- | --- |
| `source_relative` | recording 0:00 | `window_transcripts.words`, `ai_suggested_*`, canonical serving headers |
| `window_relative` | `edit_windows.window_start` | Worker caption seeds (`word.start − window_start`, [index.ts:5280](file:///c:/CODE/Dispatch/cloud-worker/src/index.ts#L5280)), the window proxy's own timeline |
| `clip_relative` | clip `start_time` | PC caption tracks (`caption_source_origin`), EDL |
| `concat_relative` | edited EDL piece 0 | renderers, `sourceToConcat` mappers |

- `select_words_for_range` converts source→window-local deterministically, clipping to the window ([edit_windows.py:118](file:///c:/CODE/Dispatch/dispatch/edit_windows.py#L118)).
- Caption seeds arriving as `source:"window_seed"` ([index.ts:5299](file:///c:/CODE/Dispatch/cloud-worker/src/index.ts#L5299)) are display seeds; the first PC-side caption edit converts them into edited groups (`source:"edited"`, [index.ts:5274](file:///c:/CODE/Dispatch/cloud-worker/src/index.ts#L5274)) anchored via `caption_source_origin`.
- `remap_caption_track_to_timeline` gained an explicit `coordinate_system` parameter (`clip_relative` default; `source_relative` maps without `+clip_start`; `window_relative` anchors at the clip's `padded_start`) — the double-offset regression fix ([exporter.py](file:///c:/CODE/Dispatch/dispatch/captions/exporter.py), [renderer.py](file:///c:/CODE/Dispatch/dispatch/video_engine/renderer.py)).
- [`frontend/src/lib/timelineMapper.ts`](file:///c:/CODE/Dispatch/frontend/src/lib/timelineMapper.ts) ships the branded cross-runtime contract (`SourceTime`/`ProxyTime`/`ConcatTime`, RFC-8785 fingerprints, `TimelineMapper`). **Known gap:** no production module imports it yet — only [test_fingerprints.ts](file:///c:/CODE/Dispatch/frontend/test_fingerprints.ts) pins it; the cloud-worker keeps its own copy ([cloud-worker/src/timelineContract.ts](file:///c:/CODE/Dispatch/cloud-worker/src/timelineContract.ts)). See Open Question 7.

---

## 7. Serving Topology (Full-Access Edit Raw)

### 7.1 PC route — canonical raw source

`GET/HEAD /api/clips/{clip_id}/source` ([app.py:1393](file:///c:/CODE/Dispatch/dispatch/web/app.py#L1393)):
- Owner-checked; serves the **canonical raw source chunk on PC disk** (`video/mp4`), byte-range capable (`Accept-Ranges: bytes`).
- Declares the timebase explicitly: `X-Clip-Start`, `X-Clip-End`, `X-Clip-Duration` headers.
- Conditional requests: `ETag` / `Last-Modified` / `Cache-Control` with 304 revalidation.

This is what makes "edit raw" *full-access*: the phone can scrub and trim the **original recording**, not the 5–180 s rendered derivative.

### 7.2 Worker route — bounded proxy

`GET /clips/:id/source-url` ([index.ts:6518](file:///c:/CODE/Dispatch/cloud-worker/src/index.ts#L6518)) returns a presigned `source_url` with media preference **`edit_window` → `clip_video` → first valid** (excluding thumbnails and other users' objects), plus `edit_window_start` / `edit_window_end` / `expires_in`. When the PC is off, this keeps the editor alive on the bounded proxy.

### 7.3 Phone source chain

`ClipsScreen` resolves the `<video>` src in editor mode: PC source route → `edit_window_url` (presigned worker proxy) → rendered `_videoUrl` ([ClipsScreen.tsx:1229](file:///c:/CODE/Dispatch/frontend/src/screens/ClipsScreen.tsx#L1229)); review mode prefers the rendered URL. Presigned URLs pass through an ~8-minute stabilization cache keyed by clip + `range_revision` and are **cleared from offline snapshots** ([ClipsScreen.tsx:2120](file:///c:/CODE/Dispatch/frontend/src/screens/ClipsScreen.tsx#L2120), [2258](file:///c:/CODE/Dispatch/frontend/src/screens/ClipsScreen.tsx#L2258)).

### 7.4 Android render source

`RenderForegroundService.stageSourceVideo` ([RenderForegroundService.kt:919](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/service/RenderForegroundService.kt#L919)) calls `GET /clips/{clipId}/source-url`, reads **only `source_url`** (the worker-side preference decides proxy vs raw), downloads to `cache/source_videos/{clipId}.mp4`, and `AndroidClipRenderer` (Media3) renders from it. Android applies no window offset of its own — see Open Question 1.

---

## 8. Data Model & Lifecycle

### 8.1 Tables ([0009](file:///c:/CODE/Dispatch/supabase/migrations/20261025000009_edit_windows.sql))

- `edit_windows`: `id`, `user_id`, `local_clip_id`, **`window_start`/`window_end` (source-relative — "the ONLY time authority for the editor")**, `status in ('staging','ready','failed','expired')`, `media_object_id`, `staged_at`, `created_at`; index `(local_clip_id, created_at desc)`; RLS on, **no client policies** (service_role only).
- `window_transcripts`: `window_id` PK, `user_id`, `words jsonb` (SOURCE-relative), `transcribed_at`.
- [0021](file:///c:/CODE/Dispatch/supabase/migrations/20261028000021_edit_window_render_fingerprint.sql) adds `edit_windows.render_fingerprint` + index `(user_id, local_clip_id, render_fingerprint)`; legacy windows get NULL and are re-staged once.
- [0032](file:///c:/CODE/Dispatch/supabase/migrations/20261113000032_wave2b_straggler.sql) aligns `/complete` kind caps with the reserve contract and corrects `cloud_find_covering_window` (transcript-bearing windows first, then newest).

### 8.2 RPC surface (all SECURITY DEFINER, `search_path = pg_catalog, public`, service_role only)

`cloud_mark_window_ready`, `cloud_mark_window_failed`, `cloud_put_window_transcript` (upsert), `cloud_get_window_transcript`, `cloud_find_covering_window`, plus the quota kernel `cloud_set_user_quota` (per-user bytes within the 10 GB global budget).

### 8.3 Status machine

```
staging ── registration ok ──> ready ── re-stage supersedes ──> (row replaced; R2 reclaimed)
   │
   └── encode/validate/upload failure ──> failed
expired : reserved by schema, never written by current code (Open Question 4)
```

---

## 9. Editor UX Contract (locked behavior)

1. **Entry point:** the `Edit Window: {paddedStart}s - {paddedEnd}s` chip on the caption timeline ([CaptionTimeline.tsx:2780](file:///c:/CODE/Dispatch/frontend/src/captions/CaptionTimeline.tsx#L2780)) and per-clip `Edit window` rows in Cloud Files ([CloudFilesSheet.tsx:176](file:///c:/CODE/Dispatch/frontend/src/components/CloudFilesSheet.tsx#L176)). ⚠ The chip shows the **padded bounds** (`start_time − 4s` legacy seed), not `edit_windows.window_start` — see Open Question 6.
2. **Playback:** editor mode plays the canonical source when the PC serves it, else the window proxy; the proxy's timeline starts at `window_start` of the source.
3. **Captions:** cloud payload supplies edited groups when they exist, else window_seed words shifted window-relative; words past the asset duration are dropped ([ClipsScreen.tsx:96](file:///c:/CODE/Dispatch/frontend/src/screens/ClipsScreen.tsx#L96)).
4. **Trim timebase inference:** the phone infers source-vs-rendered timebase by duration heuristics (`video.duration >= clipEnd − 1.0`) at the trim/scrub sites ([ClipsScreen.tsx:2913](file:///c:/CODE/Dispatch/frontend/src/screens/ClipsScreen.tsx#L2913) and six other call sites). It does **not** read `edit_window_start/end` — the locked Open Question 1.
5. **Cloud sync of edits:** phone → `PUT /clips/{id}/timeline` → Worker → PC pulls via `GET /sync/pc/timeline`. PC-local edits publish back through `sync_clip_to_cloud` Mode 1 (upsert + transcript re-registration, [sync.py:930](file:///C:/CODE/Dispatch/dispatch/cloud/sync.py#L930)) and Mode 2 re-staging when the window no longer matches the clip's render fingerprint.

---

## 10. Known Codebase Preconditions & Gaps (documented, not open for debate)

| # | Gap | Evidence |
| --- | --- | --- |
| G1 | Phone never reads `edit_window_start/end`; window-proxy playback is treated as source timebase with no offset | grep: zero matches in `frontend/src`; `isFallbackTimebase` [ClipsScreen.tsx:1241](file:///c:/CODE/Dispatch/frontend/src/screens/ClipsScreen.tsx#L1241) |
| G2 | `timelineMapper.ts` (Wave 2A cross-runtime contract) has no production importer | only `test_fingerprints.ts:16` |
| G3 | Highlight dedup docstring says 0.5, code enforces 0.45 | [highlight_finder.py:1120](file:///c:/CODE/Dispatch/dispatch/ai_clips/highlight_finder.py#L1120) |
| G4 | Gap transcription (`sync_caption_range_with_gap_transcription`) exists on the PC `/captions` route only; cloud staging never transcribes fresh window audio | [app.py `_make_stage2_gap_transcriber`](file:///c:/CODE/Dispatch/dispatch/web/app.py#L2817), [sync.py:981](file:///c:/CODE/Dispatch/dispatch/cloud/sync.py#L981) |
| G5 | Cloud-path `mark_failed` logs locally only; a mid-stage PC crash can leave a `staging` row until the next registration | [sync.py:543](file:///c:/CODE/Dispatch/dispatch/cloud/sync.py#L543) |
| G6 | Android ignores `edit_window_start/end` in the source-url response (reads `source_url` only) | [RenderForegroundService.kt:938](file:///c:/CODE/Dispatch/android/app/src/main/java/com/resolvia/dispatch/service/RenderForegroundService.kt#L938) |
| G7 | Proxy t=0 can be off-keyframe by up to ±1 s (input-seeking + validation tolerance); frame-accurate seeks into the proxy start inherit this | `ffprobe_validate_proxy` contract |

---

## 11. Open Questions for External Review

> The owner deferred these decisions ("I don't know what to do") for verification with another model. Each records **current locked behavior**, the alternatives, and the trade-offs. Nothing below is settled; everything above is.

### OQ1 — Cloud-mode timebase parity (phone & Android ignore `edit_window_start/end`)
- **Current:** Worker hydrates `edit_window_start/end` into `GET /clips` and `/source-url` ([index.ts:5339](file:///c:/CODE/Dispatch/cloud-worker/src/index.ts#L5339), [6615](file:///c:/CODE/Dispatch/cloud-worker/src/index.ts#L6615)) but no client consumes them. The phone treats the window proxy as source timebase and infers timebase by duration heuristics; Android downloads `source_url` and applies no offset.
- **Consequence:** for clips whose window does **not** start near source 0:00 (common once `start_time > 60 s`), proxy-relative playback/ruler/trim coordinates differ from source coordinates by `window_start`. Caption seeds are shifted to window-relative by the Worker, so captions align with the proxy video, but clip-relative operations (trim windows, export ranges, `ai_suggested_*` comparisons) are computed in the other space.
- **Option A (recommended):** lock client-side parity — phone and Android honor `edit_window_start/end` (subtract on ingest, add on publish), making `TimelineMapper` the shared contract instead of duration heuristics.
- **Option B:** lock server-side equivalence — Worker serves window-relative coordinates consistently and translates everything on publish; clients stay naive.
- **Trade-off:** A fixes the drift class permanently but touches every trim/scrub site (seven known timebase heuristics); B keeps clients simple but adds a translation burden to every new Worker field.

### OQ2 — Cloud staging transcription policy (cached words only vs fresh Stage-2)
- **Current (locked, tested):** staging never calls Gemini on the cloud path; words come from the clip's cached caption track filtered to the window; empty → `transcript_status='missing'`, window still ready ([sync.py:981](file:///c:/CODE/Dispatch/dispatch/cloud/sync.py#L981), contract test in [test_edit_windows.py](file:///c:/CODE/Dispatch/tests/test_edit_windows.py)).
- **Option A (recommended):** keep cached-only. Zero surprise Gemini spend; `missing` windows are still fully editable video, and the PC fills words later via gap transcription when it comes online.
- **Option B:** fresh Stage-2 on missing coverage (the plumbing exists: audio extract + `MAX_WINDOW_TRANSCRIBE_SECONDS=300`). Cost: one transcription per window without cached words; benefit: captions immediately editable on phone for clips whose PC transcript didn't cover the window.
- **Trade-off:** B improves first-session mobile UX at the price of unbounded per-user Gemini spend triggered by staging, which today is a background side effect rather than an explicit user action.

### OQ3 — Android render source policy (proxy-preferred vs raw-when-reachable)
- **Current (locked):** `/source-url` prefers `edit_window` → `clip_video`; Android renders from the staged 720p proxy when present.
- **Option A (recommended):** keep proxy-preferred. Guaranteed availability with PC off; 720p30 is acceptable for Shorts output; predictable quota (~100 MB per window).
- **Option B:** prefer `clip_video` (raw) when reachable, proxy fallback. Higher output fidelity for 4K sources, but Android render quality then depends on the PC being on, and full raw sources consume 512 MiB-cap quota instead of ~100 MB windows.
- **Trade-off:** this is a quality-vs-availability decision; A is the only option that keeps "PC off" a first-class mode.

### OQ4 — Window lifecycle (manual reclamation only vs TTL expiry)
- **Current (locked):** no time-based expiry. `status='expired'` is dead schema; superseded proxies are reclaimed at re-registration; quota is enforced by caps, not age.
- **Option A (recommended):** keep manual reclamation; document `expired` as reserved. Simplest, matches current behavior, no sweeps.
- **Option B:** TTL (e.g. 7 days) with a Worker scheduled sweep marking windows expired and reclaiming R2 bytes. Frees quota from abandoned windows automatically but risks deleting a window the user still expects (presigned URL dies mid-edit), and adds a cron surface.
- **Trade-off:** quota pressure vs surprise eviction. The quota kernel (0032 caps + per-user 2 GB) currently caps the blast radius at ~4–6 windows.

### OQ5 — "Load More" beyond 300 s (backend-ready, product-absent)
- **Current:** `compute_extension_window` (zero-context window around an explicit request) and `uncovered_ranges` (deterministic extension transcription) exist and are unit-tested ([edit_windows.py:75](file:///c:/CODE/Dispatch/dispatch/edit_windows.py#L75), [95](file:///c:/CODE/Dispatch/dispatch/edit_windows.py#L95)), but no UI and no Worker route invoke them.
- **Option A (recommended):** document as a V1 non-goal; keep the pure functions as the contract for a future "extend window" action.
- **Option B:** specify the UX now (chip affordance → `compute_extension_window` → re-stage with partial word re-transcription via `uncovered_ranges`).
- **Trade-off:** B completes the owner brief's "Load More" story but adds a staging round-trip + potential Gemini spend to the mobile flow before the base flow's parity questions (OQ1) are settled.

### OQ6 — Two window-origin anchors (`edit_windows.window_start` vs `padded_start = start_time − 4`)
- **Current:** registration derives bounds via `compute_window` (±60 s context); the UI chip displays the legacy **padded** bounds; `_resolve_edit_window_bounds` falls back padded→computed; Worker seeds use `edit_windows.window_start`.
- **Option A (recommended):** unify on `edit_windows.window_start` as the single anchor; recompute `padded_start/end` from it (or retire the chip's padded display) so the UI shows the real staged span.
- **Option B:** keep dual anchors and document the mapping.
- **Trade-off:** A is one source of truth but requires migrating clips whose only stored bounds are padded; B preserves compatibility at the cost of permanent two-anchor drift risk.

### OQ7 — Adopt or archive `TimelineMapper`
- **Current:** the Wave 2A branded cross-runtime mapper (SOURCE/PROXY/CONCAT, RFC-8785 fingerprints) is fully specified, tested, and imported by nothing in production; the cloud-worker keeps a separate `timelineContract.ts` copy.
- **Option A (recommended):** adopt it at the OQ1 parity fix — replace the seven duration heuristics with `TimelineMapper` conversions, and make the Worker's copy import-shared (or version-pinned) to stop contract drift.
- **Option B:** archive it (delete) until a runtime actually needs branded coordinates.
- **Trade-off:** keeping two unmerged copies of the same contract is the "copy-paste drift" failure mode; deleting it discards working, tested code that OQ1 will likely need.

---

## 12. Verification Matrix

| Layer | Suite | Pins |
| --- | --- | --- |
| Window math & staging | `python -m unittest tests.test_edit_windows` | compute/extension/covering, word rebasing, proxy validation, staging happy-path, **empty-transcript-still-ready contract**, failure→`mark_failed` |
| Migration contract | `tests.test_wave2b_stragglers` | 0032 caps, signature stability, revokes/grants, Worker rpc signature unchanged |
| Grant hygiene | `tests.test_migration_grant_hygiene`, `tests.test_wave1_migrations` | SECURITY DEFINER search_path, service-role-only execute |
| Sync watermark | `tests.test_wave2b_media_perf` | `compute_clip_sync_hash` skip/clear semantics (staging re-runs only on real changes) |
| Cross-runtime contract | `npm --prefix frontend test` (test_fingerprints.ts) | `TimelineMapper`, fingerprints (G2/OQ7 baseline) |
| Phone behavior | `frontend test_timeline_coord_fixes.ts` + editor suites | timebase heuristics, ruler inflation, caption remap (G1 baseline) |
