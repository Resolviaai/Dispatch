# Route B Implementation Handoff

Updated: 2026-10-23
Branch/base: main / 1ab62b6
State: Route B and caption approval safeguards have focused automated coverage. Working tree is uncommitted. Live YouTube/Gemini E2E remains outstanding.

## Route B flow

1. POST /api/youtube/ingest validates the YouTube URL and starts one guarded ingestion for that video.
2. YouTubeInboxCatcher downloads and probes the source, then records durable inbox state as DOWNLOADED.
3. TranscriptionWorker advances it through transcription; HighlightWorker detects clips, renders review candidates and advances the inbox.
4. Sessions shows queued/processing/ready/failed status and retry. Studio/Clips shows ready candidates.
5. Approval renders the selected caption snapshot and layout from source media, validates the final file, then updates clip approval and creates publishing outbox rows.
6. The outbox validates media before upload and exposes auth, retry, failure, and reconciliation states. An ambiguous remote result is not blindly re-uploaded.

The original downloaded MP4 stays one canonical file and inbox row. For Gemini Stage 1, extracted audio longer than 600 seconds is split into verified audio parts; transcript offsets are restored to the original timeline. This is not video-file segmentation.

## Caption/editor and publishing safeguards

- Caption GET is read-only: it returns stored captions or a transcript-derived preview without writing the DB or exporting ASS.
- Both editor approval paths submit or persist the edited caption track. The final render uses that snapshot and preserves its timing.
- Render output must exist and pass ffprobe before approval can enqueue publishing. Failed renders keep the clip reviewable and preserve submitted caption edits. Repeated approval is idempotent.
- If exporting a supplied final caption track fails, rendering stops before FFmpeg; it cannot quietly produce and enqueue an uncaptioned video.
- Frontend tests cover clip selection, caption loading/error/retry, preview/timeline mounting and recovery, camera pointer/exposure result state, and AE/AF lock state. These are automated production-component/state checks, not physical-device proof.

## Verification completed (automated)

- frontend: npm test passed.
- frontend: npm run build passed; produced android/app/src/main/assets/web/assets/index-EuxHzvMB.js.
- Focused Python run: 65 tests passed, including ingestion, inbox processing, caption timing and approval, render failure handling, cancellation-safe clip mutation, publishing outbox, and editor/publishing API paths.
- Android: :app:testDebugUnitTest :app:assembleDebug passed. APK ZIP inspection confirmed index-EuxHzvMB.js exists and packaged index.html references it. MainActivity loads the packaged android_asset/web/index.html.
- ADB devices -l returned no attached device. No POCO camera/recording check was performed.
- No real YouTube download, Gemini request, or remote publish was performed. The Python tests mock external service boundaries.
- Current venv does not import google.genai. google-genai>=1.0.0 is declared in requirements.txt and pyproject.toml; Gemini tests use mocked SDK objects.

## Exact remaining steps

1. In PowerShell, install declared project dependencies:
   cd C:\CODE\Dispatch
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe -c "from google import genai; print('Gemini SDK import OK')"
2. With the intended credentials and network available, start Dispatch and submit one accessible unlisted test video. Record the inbox status transitions, transcript result, generated review clip, caption edit, final render probe, outbox status, and remote upload result. Inspect logs at the first state that fails. Do not infer YouTube-side processing success from the local upload response.
3. Connect the POCO C65, install android/app/build/outputs/apk/debug/app-debug.apk, and test both camera lenses: exposure drag up/down/reset; AE/AF lock; wait while locked; unlock; lens switch; screen dim and touch wake while recording; reopen Studio on a selected clip. Save device/Android version and logs.
4. Only call camera recording verified after step 3 succeeds on the physical phone. If it fails, fix the observed root cause and rerun the focused tests/build plus the affected device steps.

## Key code locations

- Ingest and workflow: dispatch/web/app.py, dispatch/youtube_inbox/catcher.py, dispatch/transcription/worker.py, dispatch/transcription/audio.py, dispatch/transcription/gemini_engine.py, dispatch/ai_clips/worker.py
- Caption render/approval/publishing: dispatch/captions/, dispatch/video_engine/renderer.py, dispatch/web/app.py, dispatch/db.py, dispatch/publisher/outbox.py, dispatch/publisher/youtube.py
- UI: frontend/src/screens/RecordingsScreen.tsx, frontend/src/screens/ClipsScreen.tsx, frontend/src/studio/StudioWorkstation.tsx
- Automated regressions: tests/test_youtube_ingest_endpoint.py, tests/test_gemini_two_stage.py, tests/test_caption_approval_contract.py, tests/test_video_engine.py, tests/e2e/test_tier1_features_publishing_dashboard.py
