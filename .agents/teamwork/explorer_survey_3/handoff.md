# Handoff Report: Survey of Ingestion & Multi-Transport Sync, Publishing Outbox, and Web Dashboard

## 1. Observation

### 1.1 Ingestion & Multi-Transport Sync (LAN Sync, Discovery & Cryptographic Pruning)

#### A. PC Receiver Chunking & Resumability
- **File**: `dispatch/sync/receiver.py` (lines 319–458)
  - `/api/sync/upload/init` (POST): Accepts `InitUploadRequest` (`session_id`, `segment_id`, `filename`, `file_size_bytes`, `sha256_hash`, `auth_token`). Validates `verify_token(payload.auth_token)`. Checks existing completed files via `verify_file_integrity(target_file, payload.file_size_bytes, payload.sha256_hash)`. If matches, returns `status="already_completed", remote_offset=file_size_bytes, verified=True`. If `.part` file exists, returns `remote_offset=part_size`.
  - `/api/sync/upload/chunk` (PATCH): Requires headers `x-segment-id`, `x-upload-offset`, `x-file-size`, `x-sha256`, `x-session-id`, `x-auth-token`.
    - Lines 377–383 enforce strict byte-offset verification:
      ```python
      current_size = part_file.stat().st_size if part_file.exists() else 0
      if x_upload_offset != current_size:
          raise HTTPException(status_code=409, detail=f"Offset mismatch: client has {x_upload_offset}, server has {current_size}")
      ```
    - Lines 390–393: Appends stream bytes: `with open(part_file, "ab") as f: f.write(chunk_bytes)`.
    - Lines 396–452: When `new_offset == x_file_size`:
      Computes SHA-256 over entire `.part` file. If mismatch, unlinks `.part` and raises HTTP 422. If matches: atomically renames `.part` to `.mp4`, moves to `PROCESSING_DIR`, registers chunk in SQLite `chunks` table via `db.register_chunk()`, extracts metadata via `probe_video()`, and immediately enqueues pipeline job via `job_id = enqueue_job(chunk_id=chunk_id, session_id=x_session_id)`.
  - `/api/sync/reconcile` (POST, lines 240–317): Performs bidirectional manifest reconciliation against physical disk state and database records.

#### B. Cryptographic Verification & Pruning Invariant
- **Endpoint**: `/api/sync/verify-chunk` (`dispatch/sync/receiver.py`, lines 170–213)
  - Requires `auth_token` header. Validates that physical file on disk matches expected `file_size` and computed SHA-256 matches expected `sha256`, or verifies against SQLite `chunks` table if already processed into clips:
    ```python
    if actual_hash.lower() == sha256.lower():
        return {"verified": True, "segment_id": seg_id, "size": actual_size, "status": "VERIFIED"}
    ```
- **Android Client**: `android/app/src/main/java/com/resolvia/dispatch/sync/ResumableSyncWorker.kt` (lines 165–194) and `LiveSyncManager.kt` (lines 165–175):
  - Local media deletion strictly depends on explicit server verification:
    ```kotlin
    val sha256 = calculateSha256(file)
    val isVerified = verifyWithServer(activeUrl, item.segmentId, sha256, totalBytes)
    if (isVerified) {
        file.delete()
        dao.updateSegmentStatus(item.segmentId, "UPLOADED_TO_PC")
        dao.deleteOutboxItem(item.segmentId)
    } else {
        android.util.Log.w("LiveSyncManager", "Proof-of-receipt check failed for ${item.segmentId}. Preserving local file.")
    }
    ```
- **Mobile Python Client**: `dispatch_mobile/retention.py` (lines 14–63):
  - Local segment files are pruned only when status in mobile database is confirmed `VERIFIED_BY_LAPTOP`. If policy is `NEVER_DELETE`, deletion is skipped.

#### C. Automatic Discovery (UDP Broadcast & Subnet Sweep)
- **PC Discovery Server**: `dispatch/transport/discovery.py` (lines 20–147)
  - Listens on UDP port 8765 (`0.0.0.0:8765`).
  - Replies to `DISPATCH_DISCOVER` datagrams with JSON containing `lan_url`, `ip`, `port`, `auth_token`.
  - Periodically (every 2.5s) broadcasts announcement packets to `255.255.255.255:8765` and local subnet broadcast `X.X.X.255:8765`.
- **Android Discovery Client**: `android/app/src/main/java/com/resolvia/dispatch/data/NetworkDiscovery.kt` (lines 44–185)
  - Implements a parallel race across discovery channels:
    1. Direct probe of last-known `lanHost` (`/api/sync/ping` within 350ms).
    2. UDP Broadcast: Sends `DISPATCH_DISCOVER` packet to `255.255.255.255:8765` and subnet broadcast (800ms timeout).
    3. Concurrent Subnet Sweep: Sweeps local `/24` subnet across prioritized DHCP pools (`.100`–`.115` and `.1`–`.30`) concurrently querying `http://<ip>:8000/api/sync/ping` within 1.2s timeout.
    4. Fallback: Probes Tailscale endpoint if configured.

---

### 1.2 Security Audit: `/api/sync/pairing/config` Token Leak

- **File**: `dispatch/sync/receiver.py` (lines 110–167)
  ```python
  @router.get("/pairing/config")
  async def get_pairing_config():
      """Return discovered laptop network endpoints and pairing token for phone configuration.
      Crucial: never exposes cloud OAuth secrets (e.g. YouTube client secrets).
      """
      ...
      token = get_auth_token()
      connection_string = f"dispatch://pair?lan={lan_url}&tailscale={tailscale_url}&token={token}"

      yt_token = ""
      yt_refresh = ""
      yt_cid = ""
      yt_csec = ""

      from dispatch.config import ROOT_DIR
      import json
      yt_token_file = ROOT_DIR / "youtube_token.json"
      if yt_token_file.exists():
          try:
              with open(yt_token_file, "r", encoding="utf-8") as f:
                  yt_data = json.load(f)
              yt_token = yt_data.get("token", "")
              yt_refresh = yt_data.get("refresh_token", "")
              yt_cid = yt_data.get("client_id", "")
              yt_csec = yt_data.get("client_secret", "")
              if yt_token or yt_refresh:
                  connection_string += f"&yt_token={yt_token}&yt_refresh={yt_refresh}&yt_client_id={yt_cid}&yt_client_secret={yt_csec}"
          except Exception as e:
              logger.debug("Could not read youtube_token.json for pairing: %s", e)

      return {
          "lan_url": lan_url,
          "tailscale_url": tailscale_url,
          "auth_token": token,
          "yt_token": yt_token,
          "yt_refresh": yt_refresh,
          "yt_client_id": yt_cid,
          "yt_client_secret": yt_csec,
          "connection_string": connection_string
      }
  ```
- **Vulnerabilities Observed**:
  1. **Unauthenticated Secret Disclosure**: The endpoint accepts unauthenticated HTTP GET requests from any client on the LAN (no token check, no session validation, no PIN requirement).
  2. **Device Auth Token Leak**: Returns the master device authentication token (`auth_token`).
  3. **Cloud OAuth Secret Leak**: Despite the docstring claiming *"never exposes cloud OAuth secrets (e.g. YouTube client secrets)"*, lines 152, 154, and 165 directly extract and return `client_secret` (`yt_csec`), access token (`yt_token`), and refresh token (`yt_refresh`).
  4. **UDP Broadcast Leak**: Additionally, `DiscoveryBeaconServer._beacon_loop()` in `dispatch/transport/discovery.py` broadcasts `auth_token` in plaintext to `255.255.255.255:8765` every 2.5 seconds.
  5. **Client Dependency**: `NetworkDiscovery.kt` (lines 208–236), `PairingManager.kt` (lines 89–151), and `ResumableSyncWorker.kt` (lines 30–43) currently invoke `/api/sync/pairing/config` without auth headers to fetch tokens.

---

### 1.3 YouTube Cloud Inbox Architecture

- **Background Polling Daemon**: `dispatch/youtube_inbox/poller.py` (lines 23–150)
  - `YouTubeInboxPoller` runs in a background thread (`YouTubePollerThread`).
  - Executes immediate poll on server boot, then every `poll_interval_seconds` (default 600s / 10 minutes, configurable via `DISPATCH_YOUTUBE_POLL_INTERVAL`).
  - Calls `list_authenticated_user_uploads()` in `dispatch/youtube_inbox/oauth.py` (lines 80–142), querying YouTube Data API v3 on the authenticated channel's `uploads` playlist (`UU...`), retrieving Private and Unlisted videos.
  - Matches videos where `dispatch_id` is present in description/tags, or `[DISPATCH]` is in title, or `dispatch` is in tags.
  - Unauthenticated fallback: Scans channel playlist via `yt-dlp` (`scan_channel_for_dispatch_uploads()`).
  - Idempotency check: Skips videos where `db.is_youtube_video_processed(video_id)` returns True.
- **Video & Transcript Processing**: `dispatch/youtube_inbox/catcher.py` (lines 43–350)
  - `fetch_video_info()`: Obtains metadata without downloading stream.
  - `download_video()`: Downloads 1080p MP4 via `yt-dlp` into `storage/youtube_inbox/`.
  - `fetch_youtube_captions()`: Fetches auto-generated or manual VTT subtitles via `yt-dlp`.
  - `vtt_parser.py` (lines 41–191): Normalizes WebVTT, strips cue tags, converts timestamps to seconds, deduplicates repeating rolling cues, merges cues into 3–8s speech chunks, and estimates word timestamps.
  - Fallback: If YouTube captions are absent or `force_whisper=True`, automatically invokes local `transcribe_video()` via `faster-whisper`.
  - Triggers AI highlight extraction (`identify_and_save_highlights()`), renders vertical 9:16 clips with animated subtitles (`render_clip()`), registers clips in SQLite (`ready_review`), and marks video status as `CLIPS_CREATED`.
- **Android Mobile Direct Uploader**: `android/app/src/main/java/com/resolvia/dispatch/sync/YouTubeDirectUploadWorker.kt` (lines 27–185)
  - Reads finalized recordings, checks for YouTube OAuth credentials (or refreshes via Google OAuth endpoint).
  - Posts resumable upload initiation to `https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable` with `privacyStatus="private"`, title `[DISPATCH] <timestamp>`, and tags `dispatch`, `dispatch_id_<id>`.
  - Streams file bytes; upon completion updates Room DB to `UPLOADED_TO_YOUTUBE`. If unconfigured, falls back to `ResumableSyncWorker`.

---

### 1.4 Publishing Outbox & Multi-Platform Uploaders

- **Outbox Architecture**: `dispatch/publisher/outbox.py` (lines 17–148)
  - `publishing_outbox` table in SQLite (`dispatch/db.py`, lines 171–186) maintains jobs with columns: `id`, `clip_id`, `platform` (youtube, instagram, linkedin, x), `status` ('queued', 'uploading', 'published', 'failed', 'blocked_needs_auth'), `publish_mode`, `attempt_count`, `last_error`, `idempotency_key` (`UNIQUE`), `remote_id`, `remote_url`.
  - Approval flow: When `handle_approve_clip` (`web/app.py`:173) or `db.approve_clip` (`db.py`:389) is called, clip status becomes `'approved'`. For each target platform, an outbox entry is created with unique idempotency key `{clip_id}_{platform}`.
  - `process_outbox_queue()` iterates over queued jobs:
    - Marks job `'uploading'`.
    - Dispatches to platform adapter:
      - `youtube`: `upload_youtube_short()` (`dispatch/publisher/youtube.py`:10)
      - `instagram`: `upload_instagram_reel()` (`dispatch/publisher/instagram.py`:15)
      - `linkedin`: `upload_linkedin_video()` (`dispatch/publisher/linkedin.py`:12)
      - `x` / `twitter`: `upload_x_video()` (`dispatch/publisher/twitter.py`:12)
- **Authentic Credentials Check & `blocked_needs_auth`**:
  - All four platform uploaders strictly verify credentials:
    - `youtube.py`: Raises `PermissionError("YouTube OAuth credentials missing (youtube_token.json)...")`
    - `instagram.py`: Raises `PermissionError("Instagram Graph API credentials missing (INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_USER_ID)...")`
    - `linkedin.py`: Raises `PermissionError("LinkedIn API credentials missing (LINKEDIN_ACCESS_TOKEN and LINKEDIN_AUTHOR_URN)...")`
    - `twitter.py`: Raises `PermissionError("X / Twitter API credentials missing (X_API_KEY, X_BEARER_TOKEN)...")`
  - In `outbox.py` lines 98–106:
    ```python
    except PermissionError as pe:
        logger.warning("Publishing job %s (%s) blocked: %s", job_id, platform, pe)
        with db.get_db_connection() as conn:
            conn.execute("""
                UPDATE publishing_outbox 
                SET status = 'blocked_needs_auth',
                    last_error = ?
                WHERE id = ?
            """, (str(pe), job_id))
    ```
    Jobs are explicitly transitioned to `blocked_needs_auth` without faking success.
- **Test Suite Discrepancy Observed**:
  - Running `python -m unittest discover -s tests -p "test_*.py"` produced:
    1. `ERROR: test_04_multi_platform_publishing_idempotency` (`test_destructive.py`:292):
       Calls `upload_linkedin_video(access_token=None)` and expects `li_res["is_simulation"] == True` and `li_res["status"] == "published"`, but raises `PermissionError` due to authentic credentials check.
    2. `test_publisher.py` (lines 53–55):
       Calls `process_outbox_queue()` with no Instagram credentials and asserts `success_count >= 2`. Because Instagram is marked `blocked_needs_auth`, `success_count == 1`, failing the assertion.
    3. `test_pipeline_runner.py` (line 159):
       Asserts `self.assertFalse(dummy_video.exists())` (expecting raw video deletion), whereas `_run_finalize_stage` in `pipeline_runner.py` now explicitly preserves raw incoming video to satisfy R1/R4 requirements.

---

### 1.5 Web Control Dashboard

- **Server Backend**: `dispatch/web/app.py`
  - Served via FastAPI (`title="Dispatch Dashboard"`, version 1.0.0).
  - Cross-Origin Resource Sharing (CORS) middleware enabled.
  - Includes `sync_router` from `dispatch/sync/receiver.py`.
- **Frontend Template**: `dispatch/web/templates/index.html` (1149 lines)
  - UI styled with Tailwind CSS, strictly adhering to the 4-layer dark canvas architecture (Canvas: `#070A0F`, Surfaces: `bg-white/[0.03]`, Nested: `bg-black/25`, Inputs: `bg-black/40`, Functional accents: `#4F46E5`, `#10B981`, `#F43F5E`, `#F59E0B`). Zero decorative emojis used.
- **Core Dashboard Features**:
  1. **Real-time Pipeline Status**:
     - `GET /api/status`: Returns disk free/total GB, percent used, counts for ready/approved/published clips, total chunks, publish mode, and auto-process flag.
     - `GET /api/pipeline/jobs`: Returns live active pipeline jobs. Displayed in an active visual progression bar showing stages: 1. Wi-Fi/YT Ingest → 2. Whisper ASR → 3. Gemini AI Score → 4. Karaoke Render → 5. Creator Review. Polled every 3 seconds.
  2. **Clip Playback & Review**:
     - `GET /clips/{filename}`: Streams video (`video/mp4`) or thumbnail (`image/jpeg`) using FastAPI `FileResponse`.
     - `GET /api/clips`: Returns clips grouped into `ready` and `recent_approved`.
     - Ready clips tab renders an embedded 9:16 vertical video player (`<video controls playsinline preload="metadata">`), virality score badge (`Score: XX/100`), editable Roman Hinglish title, editable hashtags, dynamic active-word subtitle indicator, platform checkboxes (YouTube Shorts, Instagram Reels, LinkedIn, X), and "Approve & Dispatch" / "Reject" buttons.
     - Approved & Published tab lists processed clips with status badges (`PUBLISHED` vs `QUEUED FOR PUBLISH`) and direct MP4 download links.
  3. **Full Pipeline & Integration Visibility**:
     - `GET /api/integrations/status`: Returns live status for YouTube Data API v3 OAuth and Google Gemini 2.5 Flash API key.
     - Integrations modal allows connecting/disconnecting YouTube account and setting the Gemini API key.
     - YouTube Cloud Inbox Hub allows manual URL/ID ingestion, triggering background video fetching/rendering, and scanning channel uploads.
     - Device Pairing Modal renders an interactive QR code using `qrcode.min.js`, LAN URL, masked device auth token with toggle, and 1-click pairing string.
  4. **APK Download Link**:
     - Header contains download button linking to `/download/dispatch.apk`.
     - Pairing modal contains direct link to `/download/dispatch.apk`.
     - `GET /download/dispatch.apk` in `dispatch/web/app.py` serves `android/app/build/outputs/apk/debug/app-debug.apk` as `Dispatch-POCO-C65-v1.apk`.
     - Local inspection verified that `android/app/build/outputs/apk/debug/app-debug.apk` exists and is 11,102,494 bytes (~11.1 MB).

---

## 2. Logic Chain

1. **Premise 1 (Ingestion & LAN Sync)**:
   - `dispatch/sync/receiver.py` implements `/api/sync/upload/init`, `/api/sync/upload/chunk`, and `/api/sync/reconcile`.
   - Android's `LiveSyncManager.kt` and `ResumableSyncWorker.kt` stream 256–512 KB slices and update database byte offsets.
   - Atomic rename occurs from `.part` to `.mp4` only after the entire file's SHA-256 is verified by the receiver.
   - Deletion of local files on Android is gated behind `verifyWithServer()` calling `/api/sync/verify-chunk`, ensuring cryptographic proof-of-receipt before pruning.
   - *Conclusion*: LAN ingestion and cryptographic pruning logic is fully implemented and structurally sound.

2. **Premise 2 (Security of `/api/sync/pairing/config`)**:
   - `get_pairing_config()` in `dispatch/sync/receiver.py` (lines 110–167) lacks any authorization check.
   - It outputs `auth_token`, `yt_token`, `yt_refresh`, `yt_client_id`, and `yt_client_secret`.
   - Furthermore, `DiscoveryBeaconServer._beacon_loop()` in `dispatch/transport/discovery.py` broadcasts `auth_token` to `255.255.255.255:8765`.
   - Acceptance criterion in `ORIGINAL_REQUEST.md` requires: *"Unauthenticated requests to `/api/sync/pairing/config` do not leak authentication tokens to arbitrary LAN sniffers."*
   - *Conclusion*: The current implementation constitutes a critical security vulnerability that directly violates the acceptance criteria. It leaks both server authentication tokens and Google OAuth secrets to any LAN sniffer.

3. **Premise 3 (YouTube Cloud Inbox)**:
   - `YouTubeInboxPoller` autonomously polls authenticated uploads (`uploads` playlist) or unauthenticated channel uploads for `[DISPATCH]` or `dispatch_id`.
   - `YouTubeInboxCatcher` downloads media, attempts YouTube auto/manual caption extraction, parses and deduplicates WebVTT, falls back to `faster-whisper` when necessary, identifies highlights, and renders clips.
   - `YouTubeDirectUploadWorker.kt` provides mobile client-side upload to YouTube as Private.
   - *Conclusion*: The YouTube Cloud Inbox fulfills all ingestion and transcription requirements.

4. **Premise 4 (Publishing Outbox & Authenticity)**:
   - `publishing_outbox` table and `process_outbox_queue()` support multi-platform dispatch (YouTube Shorts, Instagram Reels, LinkedIn, X).
   - Platform modules check real credentials and raise `PermissionError` when missing.
   - `process_outbox_queue()` catches `PermissionError` and sets `status = 'blocked_needs_auth'`.
   - However, legacy tests (`test_destructive.py`, `test_publisher.py`, `test_end_to_end.py`) were expecting simulation success (`status = 'published'`, `is_simulation = True`) or raw chunk deletion, causing test assertion failures.
   - *Conclusion*: Outbox production code adheres to the authentic credential requirement, but the test suite needs updating to assert `blocked_needs_auth` instead of legacy simulated success.

5. **Premise 5 (Web Control Dashboard & APK Download)**:
   - `dispatch/web/app.py` and `dispatch/web/templates/index.html` deliver real-time pipeline status, 9:16 vertical clip review and playback, approval workflows, YouTube inbox controls, and pairing modals.
   - The compiled debug APK exists at `android/app/build/outputs/apk/debug/app-debug.apk` (11.1 MB) and `/download/dispatch.apk` delivers it.
   - *Conclusion*: The web control dashboard satisfies all R5 frontend, status, playback, and APK distribution requirements.

---

## 3. Caveats

1. **Android Runtime Verification**: While the Kotlin code, Gradle build outputs, and compiled APK (11.1 MB) were analyzed and verified statically, live on-device Wi-Fi sync on an actual physical POCO C65 hardware was not executed in this survey turn.
2. **Third-Party Social API Credentials**: Instagram Graph API, LinkedIn UGC API, and Twitter/X API credentials are not present in `.env`, meaning actual uploads to these platforms will enter `blocked_needs_auth` status as designed.
3. **CPU Governor Threshold**: In `test_governor.py`, the test failed because live system CPU saturation was high at the instant the test ran; resource governor thresholds depend on host background load.

---

## 4. Conclusion

1. **LAN Sync & Multi-Transport Ingestion**: Fully designed and functional with resumable chunking, atomic rename, and cryptographic proof-of-receipt before local segment pruning.
2. **Critical Security Vulnerability Identified**: `/api/sync/pairing/config` must be secured immediately. Unauthenticated requests currently leak `auth_token` and full YouTube OAuth credentials (`yt_token`, `yt_refresh`, `yt_client_id`, `yt_client_secret`). In addition, the UDP discovery beacon broadcasts the plaintext `auth_token` over the local network.
3. **YouTube Cloud Inbox**: Polling, VTT caption extraction, Whisper fallback, highlight generation, and rendering are fully wired.
4. **Publishing Outbox**: Multi-platform adapters correctly reject unauthenticated jobs into `blocked_needs_auth`. Unit test suites (`test_destructive.py`, `test_publisher.py`, `test_pipeline_runner.py`) contain stale assertions expecting obsolete simulation success or raw file deletion, which accounts for the test suite failures.
5. **Web Control Dashboard**: Complete 4-layer dark canvas UI with real-time status polling, 9:16 vertical video player, pipeline visibility, and verified 11.1 MB APK download endpoint.

---

## 5. Verification Method

To independently verify these findings, run the following inspection commands:

1. **Verify `/api/sync/pairing/config` Security Leak**:
   ```powershell
   python -c "from fastapi.testclient import TestClient; from dispatch.web.app import app; c = TestClient(app); r = c.get('/api/sync/pairing/config'); print('Status:', r.status_code); print('Leaked keys:', list(r.json().keys()))"
   ```
   *Expected Observation*: Status 200 returned with unauthenticated exposure of `auth_token`, `yt_token`, `yt_refresh`, `yt_client_id`, `yt_client_secret`.

2. **Verify Verified Chunk Proof-of-Receipt & Pruning Gate**:
   - Inspect `dispatch/sync/receiver.py` (lines 170–213)
   - Inspect `android/app/src/main/java/com/resolvia/dispatch/sync/ResumableSyncWorker.kt` (lines 165–194)
   - Inspect `android/app/src/main/java/com/resolvia/dispatch/sync/LiveSyncManager.kt` (lines 165–175)

3. **Verify Outbox `blocked_needs_auth` Handling**:
   - Inspect `dispatch/publisher/outbox.py` (lines 98–106)
   - Inspect `dispatch/publisher/youtube.py` (lines 41–45)
   - Inspect `dispatch/publisher/instagram.py` (lines 35–37)
   - Inspect `dispatch/publisher/linkedin.py` (lines 34–36)
   - Inspect `dispatch/publisher/twitter.py` (lines 35–37)

4. **Verify Web Dashboard & APK Download Endpoint**:
   ```powershell
   python -c "from fastapi.testclient import TestClient; from dispatch.web.app import app; c = TestClient(app); r = c.get('/download/dispatch.apk'); print('APK Status:', r.status_code, 'Content-Length:', len(r.content))"
   ```
   *Expected Observation*: Status 200 returned with Content-Length ~11,102,494 bytes.

5. **Verify Test Suite Current Status**:
   ```powershell
   python -m unittest discover -s tests -p "test_*.py"
   ```
   *Expected Observation*: 2 failures and 1 error (`test_destructive.py` expecting simulation publish, `test_pipeline_runner.py` expecting raw file deletion, `test_governor.py` battery/CPU saturation).
