# Survey & Codebase Assessment Report: Python Media Engine, AI Processing & Test Suite

**Surveyor**: Explorer 1  
**Target Codebase**: `c:\CODE\Dispatch`  
**Reference Document**: `c:\CODE\Dispatch\.agents\teamwork\ORIGINAL_REQUEST.md`  
**Timestamp**: 2026-10-07T14:44:00Z  

---

## 1. Observation

### 1.1 Python Media Engine & Pipeline Architecture
- **Stage Definitions**: `dispatch/orchestrator/state_machine.py` defines `PipelineStage(Enum)`:
  - Lines 12–18: `VERIFY = 'VERIFY'`, `TRANSCRIBE = 'TRANSCRIBE'`, `ANALYZE = 'ANALYZE'`, `RENDER = 'RENDER'`, `FINALIZE = 'FINALIZE'`, `COMPLETED = 'COMPLETED'`.
- **Pipeline Execution & Checkpoints**: `dispatch/orchestrator/pipeline_runner.py`:
  - Lines 41–114 (`PipelineStageRunner.process_job_step`):
    - Resource governor pre-flight gate on heavy tasks (`TRANSCRIBE`, `RENDER`) via `self.governor.can_process_heavy_task()`.
    - Stage dispatch calls discrete private methods: `_run_verify_stage` (Line 116), `_run_transcribe_stage` (Line 131), `_run_analyze_stage` (Line 156), `_run_render_stage` (Line 194), `_run_finalize_stage` (Line 234).
    - Checks stage completion atomically via `complete_stage_checkpoint(job_id, current_stage, worker_id=worker_id)`.
    - Uses `HeartbeatThread` during `TRANSCRIBE` (Line 146) and `RENDER` (Line 214) to renew leases every 15s.
  - `_run_finalize_stage` (Lines 234–243):
    ```python
    def _run_finalize_stage(self, job: Dict[str, Any], chunk: Dict[str, Any], filepath: Path):
        """Stage 5: Finalize chunk and mark processed. Preserves raw source video to prevent data loss."""
        chunk_id = chunk["id"]
        with db.get_db_connection() as conn:
            conn.execute("UPDATE chunks SET status = 'processed' WHERE id = ?", (chunk_id,))
        logger.info("Finalized job %s and chunk %s successfully! (Source video preserved at %s)",
                    job["job_id"], chunk_id, filepath.name)
    ```
    Raw source video is **preserved** without unlinking.
- **Orchestration Loop**: `dispatch/main.py`:
  - Lines 83–118 (`pipeline_worker_loop`): Runs background thread on a 3-second cycle.
    1. Watchdog: Every 45s runs `recover_laptop_orchestrator()` to reclaim expired worker leases from crashed workers.
    2. Ingestion: Scans `storage/incoming/` via `scan_incoming()` and enqueues newly dropped chunks.
    3. Claim & Execute: Calls `claim_job(worker_id="laptop_worker_01", lease_duration_seconds=60)` and dispatches to `stage_runner.process_job_step()`.
    4. Outbox: Calls `process_outbox_queue()` to publish approved clips.
- **Storage Layout**: `dispatch/config.py`:
  - Lines 15–25: `INCOMING_DIR = storage/incoming`, `PROCESSING_DIR = storage/processing`, `CLIPS_DIR = storage/clips`, `DATABASE_DIR = storage/database`, `QUARANTINE_DIR = storage/quarantine`, `YOUTUBE_INBOX_DIR = storage/youtube_inbox`.
  - Temporary files unlinked:
    - `dispatch/transcription/transcriber.py:140`: temporary extracted WAV `audio_path.unlink()`.
    - `dispatch/video_engine/renderer.py:101-103, 128`: `<clip_id>.ass` and failed `.tmp.mp4` unlinked.
    - `dispatch/orchestrator/recovery.py:57-60`: `PROCESSING_DIR` `.tmp`, `.part`, `.ass` unlinked on recovery boot.
- **Retry Engine & Retry Cap**:
  - `dispatch/orchestrator/retry_engine.py`:
    - Lines 25–32: Jittered exponential backoff: `base_delay * 2^(attempt-1)` (capped at 900s, jitter factor ±0.25).
    - Lines 34–78 (`classify_error`):
      - 429/quota/rate limit -> `(WAITING_FOR_AI, False)`
      - 502/503/timeout -> `(WAITING_FOR_AI, False)`
      - Out of disk -> `(WAITING_FOR_RESOURCES, False)`
      - Windows file lock -> `(RETRY_PENDING, False)`
      - Corrupt container / missing codecs / 0 bytes / empty file -> `(FAILED_PERMANENT, True)`
  - `dispatch/orchestrator/job_queue.py`:
    - Lines 191–227 (`fail_stage_job`):
      - Lines 210–213: If `attempts >= max_attempts` (default 5) and not waiting for AI/resources -> sets `status = 'FAILED_PERMANENT'`. If `attempts >= max_attempts * 2` -> sets `status = 'FAILED_PERMANENT'`.
    - Lines 83–120 (`claim_job`):
      - Line 106: `AND (attempt_count < max_attempts OR status IN ('WAITING_FOR_AI', 'WAITING_FOR_RESOURCES'))`.
      - Line 100: `attempt_count = attempt_count + 1`.
      - `complete_stage_checkpoint` (Lines 141–188) does **not** reset `attempt_count = 0` between stages.

---

### 1.2 Transcription & AI Highlight Picking
- **Whisper Roman Hinglish Configuration**: `dispatch/transcription/transcriber.py`:
  - Lines 16–33: `faster_whisper.WhisperModel(model_size='base', device='cpu', compute_type='int8', cpu_threads=4)`.
  - Lines 56–72:
    - Audio extracted via `extract_audio(video_path)` -> 16kHz mono PCM WAV.
    - `initial_prompt`: `"Main aaj discuss karunga video creation, coding, automation and workflow design."` (primes Whisper decoder to output Roman script Hinglish words rather than Devanagari script).
    - `beam_size=1`, `word_timestamps=True`, `vad_filter=True`, `vad_parameters=dict(min_silence_duration_ms=500)`.
    - Lines 102–133: Fallback retry with `vad_filter=False` if VAD filters out all segments.
    - Word timestamps stored in segments (`w.word`, `w.start`, `w.end`, `w.probability`).
- **Gemini Flash Highlight Detection**: `dispatch/ai_clips/highlight_finder.py`:
  - Lines 42–88 (`extract_clips_gemini`):
    - URL: `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent`.
    - Headers: `{"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}` (authenticated via header).
    - Generation config: `temperature: 0.2`, `responseMimeType: "application/json"`.
    - Wrapped in `try...except` logging warning and returning `None` on network/API failure.
  - `dispatch/ai_clips/prompt_templates.py`:
    - `HIGHLIGHT_SYSTEM_PROMPT` enforces:
      1. Complete thought (Hook in first 3–5s -> Body -> Conclusion/Punchline).
      2. Duration: 20.0s to 90.0s.
      3. Roman Hinglish script for titles, hooks, and descriptions.
      4. Virality score (1–100), layout recommendation (`fit_blur` or `crop_follow`).
      5. Strict JSON array schema.
  - Lines 190–192: `if not segments: return []` — **Zero canned fake titles** (removed legacy behavior that generated fake "Work Session Highlight" clips when no speech was detected).
  - Lines 218–223: `snap_to_word_boundary(timestamp, segments)` snaps suggested start/end times to exact spoken word boundaries.
  - Lines 86–178 (`extract_clips_local_heuristic`): Autonomous fallback that clusters genuine transcript segments into 20–90s blocks, scoring Hinglish hook keywords ("suno", "dekho", "basically", "problem yeh hai", "important"), and extracting real titles from the spoken text.
- **Subtitle & Video Rendering (9:16 vertical MP4)**:
  - `dispatch/video_engine/subtitle_generator.py`:
    - Generates ASS subtitles (`[Script Info] PlayResX: 1080, PlayResY: 1920`).
    - Styles: `Default` font size 52, `SUBTITLE_PRIMARY_COLOR` (&H00FFFFFF), `MarginV: 480` (safe zone above platform overlays).
    - Active word illumination (Lines 84–112): Groups into 3–4 word cards; illuminates active spoken word in yellow `&H0000FFFF` (`{\\c&H0000FFFF&}WORD{\\c&H00FFFFFF&}`). Bridges inter-word intervals to prevent flickering.
  - `dispatch/video_engine/reframer.py`:
    - Builds FFmpeg `filter_complex`:
      - 9:16 native: scale and pad to 1080x1920.
      - `crop_follow`: center-crop `ih*9/16` scaled to 1080x1920.
      - `fit_blur`: split 2 streams -> background scaled + `boxblur=25:5`, foreground scaled to 1080 width, overlay centered -> burns subtitles using escaped Windows file path syntax (`C\:/...`).
  - `dispatch/video_engine/renderer.py`:
    - FFmpeg command: `-threads 2 -preset fast -crf 20 -pix_fmt yuv420p -af loudnorm=I=-14:LRA=11:TP=-1.5 -c:a aac -b:a 192k -movflags +faststart`.
    - Renders to `<clip_id>.tmp.mp4`, then atomic `.replace()` to `<clip_id>.mp4`.
    - Generates 1-frame JPEG thumbnail at `<clip_id>.jpg`.

---

### 1.3 Existing Test Suite Inventory & Results
When running the official acceptance test command:
`python -m unittest discover -s tests -p "test_*.py"`

**Test Execution Results**:
- Discovered 29 test methods across 6 test modules containing `unittest.TestCase` classes.
- Result: **Ran 29 tests in 1.490s: FAILED (failures=1, errors=1)**.

#### Verbatim Test Failures in `unittest discover`:
1. **Failure 1**: `test_pipeline_runner.py`:
   ```
   FAIL: test_checkpointed_stage_execution_and_resumption (test_pipeline_runner.TestPipelineCheckpointingAndRetry.test_checkpointed_stage_execution_and_resumption)
   Traceback (most recent call last):
     File "C:\CODE\Dispatch\tests\test_pipeline_runner.py", line 159, in test_checkpointed_stage_execution_and_resumption
       self.assertFalse(dummy_video.exists())
   AssertionError: True is not false
   ```
2. **Error 1**: `test_destructive.py`:
   ```
   ERROR: test_04_multi_platform_publishing_idempotency (test_destructive.TestDestructiveChaos.test_04_multi_platform_publishing_idempotency)
   Traceback (most recent call last):
     File "C:\CODE\Dispatch\tests\test_destructive.py", line 292, in test_04_multi_platform_publishing_idempotency
       li_res = upload_linkedin_video(
     File "C:\CODE\Dispatch\dispatch\publisher\linkedin.py", line 35, in upload_linkedin_video
       raise PermissionError("LinkedIn API credentials missing (LINKEDIN_ACCESS_TOKEN and LINKEDIN_AUTHOR_URN).")
   PermissionError: LinkedIn API credentials missing (LINKEDIN_ACCESS_TOKEN and LINKEDIN_AUTHOR_URN).
   ```

#### Full Inventory of All 16 Test Files in `tests/`:

| File | Type | Discovered by `unittest`? | Standalone Run Result | Status / Root Cause |
|---|---|---|---|---|
| `test_governor.py` | `TestCase` (6 tests) | Yes | PASS | Passes all resource governor tests. |
| `test_transport.py` | `TestCase` (3 tests) | Yes | PASS | Passes LAN/Tailscale transport tests. |
| `test_youtube_inbox.py` | `TestCase` (6 tests) | Yes | PASS | Passes VTT parser, DB, and catcher tests. |
| `test_chaos.py` | `TestCase` (6 tests) | Yes | PASS | Passes all 23 chaos scenarios (Gemini fallback, power cuts, corrupt files). |
| `test_pipeline_runner.py` | `TestCase` (3 tests) | Yes | FAIL (1 fail) | Line 159 asserts `self.assertFalse(dummy_video.exists())`. Raw source video is now intentionally preserved per R4 / acceptance criteria. |
| `test_destructive.py` | `TestCase` (5 tests) | Yes | ERROR (1 error) | Line 292 asserts simulated upload on missing credentials. Under R5, missing credentials raise `PermissionError` and mark jobs as `blocked_needs_auth`. |
| `test_ai_clips.py` | Standalone script | No | PASS | Snapping, local heuristic, preference learner pass. |
| `test_transcription.py` | Standalone script | No | PASS | 16kHz mono audio extraction passes. |
| `test_video_engine.py` | Standalone script | No | PASS | 9:16 rendering and ASS subtitle generation pass. |
| `test_ingestion.py` | Standalone script | No | PASS | File stabilization and ffprobe verification pass. |
| `test_mobile_engine.py` | Standalone script | No | PASS | Mobile chunk database and retention manager pass. |
| `test_sync_engine.py` | Standalone script | No | FAIL | Line 127 asserts `INCOMING_DIR / f"{seg_id}.mp4"` exists, but `receiver.py` moves verified chunks to `PROCESSING_DIR`. |
| `test_job_queue.py` | Standalone script | No | FAIL | Line 85 `claimed6` is `None`. `attempt_count` reaches 5 across 5 sequential stage claims, hitting `attempt_count < max_attempts` query lock. |
| `test_web.py` | Standalone script | No | FAIL | Line 61 asserts `"Dispatch Mobile Recorder" in res.text`. Template title was renamed to `"Dispatch Mobile \| Pro Autonomous Capture"`. |
| `test_publisher.py` | Standalone script | No | FAIL | Line 54 expects `>= 2` published jobs, but Instagram has no credentials and is marked `blocked_needs_auth`. |
| `test_end_to_end.py` | Standalone script | No | FAIL | Line 66 asserts raw chunk was deleted (`assert not staged_info["filepath"].exists()`), violating zero-deletion requirement. |

---

## 2. Logic Chain

1. **Pipeline Execution & Checkpoints**:
   - `PipelineStageRunner.process_job_step()` processes one stage at a time and calls `complete_stage_checkpoint()` to record progress in SQLite before moving to the next stage.
   - If a crash occurs, `recover_laptop_orchestrator()` reclaims stale leases and sets the job to `RETRY_PENDING` at the exact current stage. Completed stages are never re-executed.
2. **Raw Video Preservation**:
   - Requirement R4 states "zero deletion of raw creator video" and Acceptance Criteria state "Raw source video files in `storage/incoming/` are never deleted during finalize stage".
   - In `dispatch/orchestrator/pipeline_runner.py:234-243`, `_run_finalize_stage()` marks chunks as `processed` in SQLite and logs source video preservation without unlinking the file.
   - However, `tests/test_pipeline_runner.py:159` still asserts `self.assertFalse(dummy_video.exists())` from an older version of the codebase when cleanup was active. This directly causes the failure in `test_pipeline_runner.py`.
3. **Retry Cap & `FAILED_PERMANENT`**:
   - In `fail_stage_job()`, when `attempts >= max_attempts` (default 5), the status is set to `FAILED_PERMANENT`. Permanent corruptions (unsupported formats, missing moov atoms) are classified as permanent immediately by `RetryEngine.classify_error()`.
   - In `claim_job()`, line 106 has `AND (attempt_count < max_attempts ...)`. Because `claim_job()` increments `attempt_count` on *every* stage claim and `complete_stage_checkpoint()` does not reset `attempt_count` across stages, a job advancing through all 5 stages (VERIFY, TRANSCRIBE, ANALYZE, RENDER, FINALIZE) hits `attempt_count == 5` at stage 5 and cannot be claimed.
   - `attempt_count` should track retry failures for the current stage, or `complete_stage_checkpoint()` should reset `attempt_count = 0` upon successful stage transition.
4. **Publishing Credentials & `blocked_needs_auth`**:
   - Requirement R5 mandates: "marking unauthenticated jobs as `blocked_needs_auth` rather than faking success."
   - `linkedin.py` and other publisher adapters were updated to raise `PermissionError` when credentials are missing, and `outbox.py` catches `PermissionError` to set `status = 'blocked_needs_auth'`.
   - `tests/test_destructive.py` (`test_04_multi_platform_publishing_idempotency`) was written prior to this change, expecting missing credentials to return `{"status": "published", "is_simulation": True}`. Calling `upload_linkedin_video(access_token=None)` raises `PermissionError`, causing the test error.
5. **Test Suite Discovery**:
   - 10 of the 16 test files use bare functions (`def test_*():`) instead of inheriting from `unittest.TestCase`. `unittest discover` ignores these files completely, meaning only 6 files (29 test methods) are currently evaluated by `python -m unittest discover`.

---

## 3. Caveats

1. **Android Gradle Toolchain**: The Android build (`gradlew.bat assembleDebug`) was not executed during this survey, as this task focused strictly on the Python Media Engine, Pipeline, Transcription, AI Highlight Picking, and Python test suite.
2. **Third-Party API Credentials in Development Mode**: Local runs without real LinkedIn, Instagram, or Twitter credentials in `.env` are expected to mark jobs as `blocked_needs_auth`. Tests asserting publishing success for all 4 platforms must use mocked credentials or verify the `blocked_needs_auth` state.

---

## 4. Conclusion & Required Actions

To achieve 100% test pass status (`Ran 29 tests: 0 failures, 0 errors`) and full alignment with `ORIGINAL_REQUEST.md`:

### Immediate Fixes for `python -m unittest discover -s tests -p "test_*.py"`:
1. **Fix `tests/test_pipeline_runner.py` (Line 159)**:
   - Change `self.assertFalse(dummy_video.exists())` to `self.assertTrue(dummy_video.exists())` to align with the raw video preservation policy ("zero deletion of raw creator video").
2. **Fix `tests/test_destructive.py` (Lines 291–309, 328–342)**:
   - Update `test_04_multi_platform_publishing_idempotency` to expect `PermissionError` when calling `upload_linkedin_video` and `upload_x_video` without credentials, and verify that `process_outbox_queue()` marks unauthenticated platform jobs as `blocked_needs_auth` (or mock credentials to test idempotent published transitions).

### Additional Pipeline & Test Hardening:
3. **Fix `dispatch/orchestrator/job_queue.py` (Stage Checkpoint Reset)**:
   - In `complete_stage_checkpoint()` (Line 161), add `attempt_count = 0` to the SQL `UPDATE` statement so that successful stage transitions reset retry attempts for the next stage. This resolves the claim lockout in `test_job_queue.py`.
4. **Fix `tests/test_sync_engine.py` (Line 126–127)**:
   - Check `PROCESSING_DIR / f"{seg_id}.mp4"` as well as `INCOMING_DIR / f"{seg_id}.mp4"`, matching `receiver.py`'s staging flow.
5. **Fix `tests/test_web.py` (Line 61)**:
   - Change assertion from `"Dispatch Mobile Recorder"` to `"Dispatch Mobile"` to match the updated PWA template title.
6. **Fix `tests/test_end_to_end.py` (Line 66)**:
   - Change `assert not staged_info["filepath"].exists()` to `assert staged_info["filepath"].exists()` to respect the raw video retention requirement.
7. **Make All Tests Discoverable**:
   - Wrap the 10 standalone test scripts into `unittest.TestCase` classes so that `python -m unittest discover` exercises the entire test suite comprehensively.

---

## 5. Verification Method

To independently verify these findings:

1. **Run Current Test Suite**:
   ```powershell
   python -m unittest discover -s tests -p "test_*.py"
   ```
   *Expected Current Output*: 29 tests run, 1 failure (`test_pipeline_runner.py:159`), 1 error (`test_destructive.py:292`).
2. **Inspect Raw Video Preservation**:
   Check `dispatch/orchestrator/pipeline_runner.py` lines 234–243: verify `_run_finalize_stage` does not call `filepath.unlink()`.
3. **Inspect Retry Cap**:
   Check `dispatch/orchestrator/job_queue.py` lines 205–215: verify `fail_stage_job` sets `status = 'FAILED_PERMANENT'` when `attempts >= max_attempts`.
4. **Inspect Whisper Hinglish Prompt**:
   Check `dispatch/transcription/transcriber.py` lines 58–61: verify `initial_prompt` contains Roman Hinglish text hint.
5. **Inspect Gemini Flash Header Authentication**:
   Check `dispatch/ai_clips/highlight_finder.py` lines 50–55: verify header `"x-goog-api-key": GEMINI_API_KEY`.
6. **Inspect Subtitle ASS Karaoke Formatting**:
   Check `dispatch/video_engine/subtitle_generator.py` lines 53–55 and 106–108: verify `PlayResX: 1080, PlayResY: 1920` and `{\\c&H0000FFFF&}` active word styling.
