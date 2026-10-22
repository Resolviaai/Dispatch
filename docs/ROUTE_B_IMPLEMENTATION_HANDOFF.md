# Route B Implementation Handoff

Updated: 2026-10-23 (verification run against committed HEAD)

Branch/base: `main` / HEAD `79444b7db6afc3c0cf74612b4e44f96808fb15cd`
State: the editor and long-input reliability changes plus the Route B regressions are **committed** at `79444b7` (commit message reads "add database layer…" but the content is this work, including new clip-range columns/migrations). All verification below ran against exactly this HEAD from a clean tree. Live YouTube/Gemini end-to-end execution is still required. Keep Route A continuity and optional automatic cuts behind that gate.

## Current priority

1. Finish the editor and long-input reliability work; automated regressions/builds pass below.
2. Prove the real Route B flow with credentials/network and one accessible unlisted source.
3. Then handle Route A session grouping and clips crossing recording-segment boundaries.
4. Keep optional automatic cuts deferred.

## Route B flow

1. `POST /api/youtube/ingest` validates a YouTube URL and starts one guarded ingestion for that video.
2. `YouTubeInboxCatcher` downloads and probes the source, then persists inbox state as `DOWNLOADED`.
3. Transcription and highlight workers advance durable state and produce review candidates.
4. Sessions exposes queued/processing/ready/failed status and retry; Clips/Studio exposes review candidates.
5. Approval renders the submitted caption snapshot and layout, probes the final file, then creates publishing outbox rows.
6. The outbox validates media before upload and exposes auth/retry/failure/reconciliation states; ambiguous remote outcomes are not blindly re-uploaded.

The original downloaded MP4 remains one canonical inbox file/row. Stage 1 audio over 600 seconds is extracted into verified overlapping audio windows, with source offsets restored and adjacent-boundary duplicate phrases removed. If bounded Stage 2 extraction fails, it retries and does not transcribe the full source.

## Editor and caption safeguards in this checkpoint

- Trim uses source-time endpoints, can extend past Gemini's suggested range, and enforces source bounds plus the product's 5–180 second range.
- Timeline scrubbing maps source-time ruler positions to clip-relative video seeks and caption timing. Trim pauses playback, seeks to the new source start, and returns the clip clock to zero.
- A successful trim marks an existing render stale; successful re-render clears the stale marker. API rejection restores the prior trim in the editor.
- Production timeline handlers use the tested pointer/range/clock helpers; pointer capture and cancel/lost-capture paths are included. Tests exercise production helpers wired into the component, but do not replace browser or device input proof.
- Caption GET remains side-effect-free; edits are persisted and rendered from the submitted snapshot. Render output must exist and pass ffprobe before approval can enqueue publishing. Caption export failure stops before FFmpeg/publishing, and repeated approval is idempotent.
- Frontend regressions cover selected-clip/loading/error/retry and preview/timeline mounting/recovery. These source tests are not physical-device proof.

## Automated verification (2026-10-23, committed HEAD `79444b7`; supersedes the pre-commit run)

- `git diff --check` — passed (clean tree at HEAD).
- `cd frontend; npm test` — passed (exit 0), including editor, timeline interaction, camera interaction-state, and Studio render-path regressions.
- `cd frontend; npm run build` — passed (exit 0); emitted `android/app/src/main/assets/web/assets/index-Bd9C_B3d.js` (590.93 kB). Vite reported only its non-blocking >500 kB chunk advisory.
- From `C:\CODE\Dispatch`, with TEMP/TMP set to `.dispatch-test-tmp`: `python -m unittest tests.test_caption_range_sync tests.test_caption_approval_contract tests.test_clip_trim_contract tests.test_gemini_two_stage tests.test_web tests.test_youtube_ingest_endpoint tests.test_youtube_inbox tests.test_youtube_processing_tracker tests.test_youtube_caption_timing_fix tests.test_captions_subsystem tests.test_video_engine tests.e2e.test_tier1_features_publishing_dashboard tests.test_layout_framing_architecture -v` — **80 passed in 74.1 s** (prior 76 plus 4 tests added with HEAD, including the new `tests.test_caption_range_sync` expansion/shrink regressions).
- From `android`, with `JAVA_HOME=C:\Program Files\Java\jdk-17.0.5`: `gradlew.bat :app:testDebugUnitTest --rerun-tasks` — **18 tests, 0 failures, 0 errors** (forced execution, BUILD SUCCESSFUL in 31 s); `gradlew.bat :app:testDebugUnitTest :app:assembleDebug` — **BUILD SUCCESSFUL**.
- APK `android/app/build/outputs/apk/debug/app-debug.apk`: **15,189,951 bytes**. ZIP inspection verified packaged `index.html` points to `index-Bd9C_B3d.js` (591,049 bytes in APK), identical to the committed web entry and the fresh rebuild. The earlier `index-BchKp7b8.js` reference was the pre-commit bundle and is resolved: the committed entry and the current APK both use `index-Bd9C_B3d.js`.
- ADB (`platform-tools/adb.exe`) — daemon works, no attached devices. No POCO C65 check was done; camera behavior is not verified.
- All Gemini/YouTube API boundaries in this test run are mocked. `from google import genai` still fails in `.\.venv` (`google-genai` declared in `requirements.txt` but not installed; the venv also lacks `pip`). No real download, Gemini request, remote publish, or YouTube processing status was observed.

## Exact remaining steps

0. Review the prepared (uncommitted) test-artifact cleanup: `git rm -r --cached .dispatch-test-tmp` plus `.gitignore` rules for `.dispatch-test-tmp/` and `android/.kotlin/` — the 8 accidentally committed sandbox files (test DBs/font manifests, no user data) are staged for untracking, files kept on disk. Commit when satisfied.
1. In `C:\CODE\Dispatch`, install declared requirements if needed: `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`; verify `.\.venv\Scripts\python.exe -c "from google import genai; print('Gemini SDK import OK')"`. The current venv was previously observed missing `google.genai`.
2. With intended credentials and a network connection, import one accessible unlisted source. Record the real statuses and logs at ingest, download/probe, transcription, clip generation, caption edit/approval, render/probe, outbox, and remote YouTube upload/processing. Do not infer YouTube processing from local upload completion.
3. After Route B E2E succeeds, implement/test Route A session grouping and clips that cross recording-segment boundaries. Do not add automatic cuts in this pass.
4. For camera proof, connect the POCO C65, install the built debug APK, and test exposure in both directions/reset on front and rear cameras; AE/AF lock, wait, unlock; lens switching; and reopening Studio on a selected clip. Keep camera recording unverified until that device run passes.

## Key code locations

- Ingest/workflow: `dispatch/web/app.py`, `dispatch/youtube_inbox/catcher.py`, `dispatch/transcription/worker.py`, `dispatch/transcription/audio.py`, `dispatch/transcription/gemini_engine.py`, `dispatch/ai_clips/worker.py`
- Caption render/approval/publishing: `dispatch/captions/`, `dispatch/video_engine/renderer.py`, `dispatch/db.py`, `dispatch/publisher/outbox.py`, `dispatch/publisher/youtube.py`
- UI: `frontend/src/screens/RecordingsScreen.tsx`, `frontend/src/screens/ClipsScreen.tsx`, `frontend/src/captions/CaptionTimeline.tsx`, `frontend/src/studio/StudioWorkstation.tsx`
- Regressions: `frontend/test_clip_timeline_interactions.js`, `tests/test_clip_trim_contract.py`, `tests/test_gemini_two_stage.py`, `tests/test_caption_approval_contract.py`, `tests/test_youtube_ingest_endpoint.py`, `tests/test_video_engine.py`
