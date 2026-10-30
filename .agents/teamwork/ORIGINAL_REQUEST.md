# Original User Request

## 2026-10-07T08:57:21Z

Dispatch: A production-grade, bug-free, fully autonomous personal content engine for a solo creator recording high-definition video on a POCO C65 (HyperOS/Android 13/14) and processing on a Windows 11 PC (Ryzen 5 5600H, RX 5500M GPU, 23 GB RAM). The system captures segmented video, syncs over LAN or YouTube Cloud Inbox, transcribes Roman Hinglish audio, identifies high-impact hooks using AI, renders 9:16 vertical clips with animated karaoke subtitles, and manages social outbox publishing.

Working directory: c:\CODE\Dispatch
Integrity mode: development

## Requirements

### R1. End-to-End Pipeline Execution & Verifiable Flow
The entire media pipeline (Verify → Transcribe → Analyze → Render → Finalize) must execute sequentially and without failure on both sample test footage and incoming device streams. Zero manual intervention required from upload to final 9:16 short-form video clips with animated subtitles.

### R2. Reliable Ingestion & Multi-Transport Sync (LAN & YouTube Cloud Inbox)
The system must support two ingestion paths without data loss:
1. **Local Network (LAN)**: High-speed resumable chunked upload from the Android device to the PC receiver, with automatic discovery (UDP/subnet sweep) and cryptographic verification before local segment pruning.
2. **YouTube Cloud Inbox**: Ingestion and polling of private videos tagged `[DISPATCH]` uploaded directly by the mobile app when away from home, with automatic transcript and video fetch.

### R3. Flawless Android Capture & Native UI Spec
The Android mobile application on POCO C65 must record uninterrupted FHD 1080p video with zero dropped footage between segment boundaries, maintain camera capture during screen-off/dim via foreground service and wake locks, and match the 6-screen reference design (Record, Active Viewfinder, Sessions/Upload Gauge, Processing Timeline, Clip Review, Settings/Connection).

### R4. Hinglish Transcription & Hallucination-Free Highlight Picker
Whisper transcription must accurately recognize mixed Hindi and English (Hinglish) spoken content. The AI highlight finder must extract genuine, high-retention complete thoughts (20-90s) with valid hooks and titles using Gemini Flash (authenticated via header), with zero fallback to canned fake titles and zero deletion of raw creator video.

### R5. Multi-Platform Publishing Outbox & Web Control Dashboard
The publishing outbox must reliably queue, approve, and upload clips to target platforms (YouTube Shorts, Instagram Reels, LinkedIn, X) with authentic credentials, marking unauthenticated jobs as `blocked_needs_auth` rather than faking success. The web dashboard must provide real-time status, clip playback, and full pipeline visibility.

## Acceptance Criteria

### Automated Pipeline Verification
- [ ] End-to-end media test with real sample video executes all 5 stages (VERIFY -> TRANSCRIBE -> ANALYZE -> RENDER -> FINALIZE) to completion with exit status 0 and generates verified 9:16 MP4 clips with subtitles in `storage/clips/`.
- [ ] Raw source video files in `storage/incoming/` are never deleted during finalize stage.
- [ ] The retry cap terminates failing jobs into `FAILED_PERMANENT` after max attempts (5) instead of looping indefinitely.
- [ ] All test suites (`python -m unittest discover -s tests -p "test_*.py"`) pass with 0 failures and 0 errors.

### Security & Pairing
- [ ] Unauthenticated requests to `/api/sync/pairing/config` do not leak authentication tokens to arbitrary LAN sniffers.
- [ ] Android discovery automatically identifies the laptop server IP and successfully handshakes.

### Android Application
- [ ] Gradle build (`gradlew.bat assembleDebug`) succeeds without errors or warnings.
- [ ] APK is generated at `android/app/build/outputs/apk/debug/app-debug.apk` and downloadable from the web dashboard.
- [ ] Camera segment boundary transitions execute concurrently without blocking camera recording.
