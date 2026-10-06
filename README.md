# Dispatch: Autonomous Personal Content Engine

> **"You create the raw signal. Dispatch carries it the rest of the way."**

Dispatch is an autonomous, local-first content pipeline engineered to turn raw, multi-hour natural work and speaking sessions recorded on an Android phone into polished, high-retention vertical short-form videos (YouTube Shorts, Instagram Reels, LinkedIn, X).

Inspired by the "lead-miner" philosophy, Dispatch requires **zero manual intervention** once configured. There are no manual USB cable transfers, no timeline non-linear editing (NLE) drag-and-drops, and no multi-step render exports. 

---

## 1. System Philosophy & User Experience

### The 5 Human Steps
The system is built so the user only touches 5 high-value, tactile actions:
1. **Start Record**: Press record on your phone (POCO C65) when starting work or a speaking session.
2. **Stop Record**: Press stop when you finish your session.
3. **Review Clips**: Open the Dispatch local dashboard (on PC or phone browser over LAN) to preview ready clips.
4. **Review/Update Tags**: Check or adjust the auto-generated title, description, and hashtags if desired.
5. **Approve**: Tap **Approve** (or **Reject**). 
   - If set to **Auto-Publish**, the clip is automatically scheduled and posted publicly.
   - If set to **Private/Draft**, Dispatch uploads the clip as a draft or private post so you have total peace of mind and can inspect it on-platform before making it public.

**Everything else happens 100% autonomously in the background.**

---

## 2. Hardware & Operating Environment

- **Host Machine**: Windows 11 Home (Ryzen 5 5600H 6-core/12-thread, 23.3 GB RAM, AMD Radeon RX 5500M 4GB VRAM).
- **Compute Strategy**: 
  - Zero reliance on NVIDIA CUDA (which would fail on AMD hardware).
  - High-performance CPU multithreading (`faster-whisper` CPU-int8 / `whisper.cpp`).
  - FFmpeg hardware acceleration where available, with fast CPU software fallback.
  - No heavyweight Docker/WSL2 containers to safeguard the 43.6 GB free SSD space.
- **Mobile Hardware**: POCO C65 (Android 14+), recording 1080p @ 30 fps.
- **Transfer**: Resumable local Wi-Fi sync (via LAN folder sync/Syncthing or TUS background upload). Zero manual cables.
- **Budget & Cost**: 100% free local infrastructure; optional pennies spent strictly on Google Gemini Flash API for high-level semantic highlight extraction.

---

## 3. Core Architectural Pillars

Dispatch is structured into seven decoupled, fault-tolerant pillars:

```
[POCO C65 Mobile Phone]
  │ (Records rolling 15-30 min chunks to prevent thermal/file corruption)
  │ Automatic Background Wi-Fi LAN Sync
  ▼
[Pillar 1: Ingestion & Session Watcher] (dispatch/ingestion)
  │ Watches incoming/ directory, validates file completion, verifies ffprobe integrity
  │ Detects aspect ratio (16:9 Landscape vs 9:16 Portrait) & creates SQLite session record
  ▼
[Pillar 2: Audio Extraction & Speech Engine] (dispatch/transcription)
  │ Fast 16kHz mono audio extraction
  │ Faster-Whisper VAD speech detection -> Word-level timestamps & Roman Hinglish text
  ▼
[Pillar 3: AI Semantic Highlight & Packaging Engine] (dispatch/ai_clips)
  │ Passes structured transcript to Gemini Flash
  │ Identifies complete thoughts (20-90s) with clear Hook -> Body -> Conclusion
  │ Generates catchy Roman Hinglish titles, descriptions, hashtags & virality scores
  │ Negative Preference Learning: Filters topics user previously rejected
  ▼
[Pillar 4: Video Assembly & Subtitle Rendering] (dispatch/video_engine)
  │ Adaptive Framing:
  │   - 9:16 Portrait: Full-res native passthrough
  │   - 16:9 Landscape: Smart face-crop or Fit with blurred background
  │ Generates styled .ass (Advanced SubStation Alpha) Roman Hinglish captions
  │ Single-pass FFmpeg burn-in + audio normalization (loudnorm) + preview thumbnail
  ▼
[Pillar 5: Review & Control Web Dashboard] (dispatch/web)
  │ Modern dark-canvas dashboard (FastAPI + Tailwind UI) accessible via PC & phone
  │ Live clip preview, platform checkboxes, Public vs Private toggle, 1-click Approve/Reject
  ▼
[Pillar 6: Publishing & Scheduling Engine] (dispatch/publisher)
  │ Idempotent publishing outbox for YouTube Shorts & Instagram Reels
  │ Handles Private/Draft uploads vs Public scheduled drops
  │ Auto-cleans exported video files upon verified upload
  ▼
[Pillar 7: Master Dispatch Daemon & CLI] (dispatch/main.py)
  │ Single command boots entire pipeline: Watcher + Worker Queue + Web Dashboard
```

---

## 4. Directory Structure

```
Dispatch/
├── 1.txt, 2.txt, 3.txt, 4.txt    # Project research & transcript context
├── README.md                     # Master project documentation
├── PROGRESS.md                   # Real-time state and change log
├── dispatch/
│   ├── __init__.py
│   ├── config.py                 # Central configurations & directory paths
│   ├── db.py                     # SQLite database schema, migrations & CRUD operations
│   ├── ingestion/                # Pillar 1: File watcher, validation & session manager
│   │   ├── __init__.py
│   │   ├── watcher.py
│   │   └── validator.py
│   ├── transcription/            # Pillar 2: Speech extraction & Hinglish word alignment
│   │   ├── __init__.py
│   │   ├── audio.py
│   │   └── transcriber.py
│   ├── ai_clips/                 # Pillar 3: Semantic highlight finder & packaging
│   │   ├── __init__.py
│   │   ├── highlight_finder.py
│   │   ├── prompt_templates.py
│   │   └── preference_learner.py
│   ├── video_engine/             # Pillar 4: FFmpeg reframing, .ass subtitles & rendering
│   │   ├── __init__.py
│   │   ├── reframer.py
│   │   ├── subtitle_generator.py
│   │   └── renderer.py
│   ├── web/                      # Pillar 5: FastAPI review dashboard
│   │   ├── __init__.py
│   │   ├── app.py
│   │   ├── static/
│   │   └── templates/
│   ├── publisher/                # Pillar 6: YouTube & Instagram publishing adapters
│   │   ├── __init__.py
│   │   ├── youtube.py
│   │   ├── instagram.py
│   │   └── outbox.py
│   └── main.py                   # Pillar 7: Unified daemon entry point
├── storage/
│   ├── incoming/                 # Phone drops raw video chunks here via Wi-Fi sync
│   ├── processing/               # Active workspace during transcription/cutting
│   ├── clips/                    # Finished candidate clips ready for review
│   └── database/
│       └── dispatch.db           # SQLite state, idempotency keys, & preference logs
└── tests/                        # Comprehensive unit & end-to-end integration tests
```

---

## 5. Storage Lifecycle (Rolling & Ephemeral)

To fit comfortably within the 43.6 GB disk space constraint:
1. **Raw Sessions**: Once all candidate clips are extracted, rendered, and verified on disk, the raw multi-hour source chunks are automatically purged from `incoming/` and `processing/`.
2. **Finished Clips**: Stored in `storage/clips/` (~1.5 GB for 50 clips). Once an approved clip is uploaded to YouTube or Instagram and verified, the local clip file is deleted.
3. **Database Records**: Transcripts, timestamps, review decisions, and publication audit logs are permanently retained in SQLite (a few megabytes total).

---

## 6. How to Run

1. **Install Prerequisites**: Python 3.11+ and FFmpeg (with `libass` support).
2. **Set API Key**: Set your `GEMINI_API_KEY` in environment variables or `.env`.
3. **Start Dispatch Daemon**:
   ```powershell
   python -m dispatch.main
   ```
4. **Access Control Dashboard**: Open `http://localhost:8765` on your PC (or `http://<your-pc-ip>:8765` from your POCO C65 phone browser).
5. **Install Windows Silent Background Service (Optional)**:
   ```cmd
   scripts\install_windows_startup.bat
   ```
   Registers Dispatch in Windows Task Scheduler to start silently on login with below-normal CPU priority and zero terminal popups.

---

## 7. Phone Recording & Resumable Sync (Zero Cloud Storage, Zero Cables)

Dispatch does **not** rely on third-party cloud storage (S3, R2, Supabase) or fragile sync folders. It features a custom **TUS-style chunked, byte-resumable synchronization engine**:

### Architecture:
- **Phone = Durable Source of Truth & Outbox**: Uses SQLite in Write-Ahead Logging (WAL) mode or IndexedDB.
- **Laptop = Opportunistic Worker**: Safe for laptop to be OFF, sleeping, or disconnected; phone keeps records safely queued.
- **Rolling Segments (15 mins)**: Records into `.tmp` files and atomically commits to `.mp4` with SHA-256 checksums. Battery death or crashes only lose the active segment, never the session.
- **Confirmed Byte Resuming**: If network drops at 5%, 50%, or 99%, the upload resumes from the exact remote byte offset without restarting from 0%.
- **Conservative Retention**: Phone **never** deletes a local recording until the laptop explicitly confirms SHA-256 verification via `/api/sync/reconcile`.

### How to Record on Your POCO C65:

#### Option A: Zero-Install Mobile Web PWA Recorder (Instant)
1. Open your POCO C65 mobile browser and navigate to:
   ```
   http://<your-laptop-ip>:8765/mobile
   ```
2. Tap the large circular Record button. The camera streams to viewfinder while slicing rolling segments into browser IndexedDB.
3. Chunks automatically upload in the background to your laptop whenever reachable.

#### Option B: Native Android Studio App (`android/`)
1. Open the `android/` directory in Android Studio.
2. Build and install the APK (`./gradlew assembleDebug`) on your POCO C65.
3. Features CameraX video recording, Room SQLite WAL storage, WorkManager background upload with Wi-Fi constraints, and `BootReceiver` for automatic recovery on phone reboot.

#### Option C: Python Mobile Engine (`dispatch_mobile/`)
- Run inside Termux on Android:
  ```bash
  python -m dispatch_mobile.main
  ```

---

## 8. Resource Governor & Laptop Performance Manager

Dispatch includes a hardware-aware **Resource Governor** (`dispatch/governor/`) preventing laptop lag, overheating, or battery drain:
- **Windows Process Priority**: Runs worker threads with `BELOW_NORMAL_PRIORITY_CLASS` so foreground games, IDEs, and browsers remain smooth.
- **Power Awareness**: Prefers AC power and defers heavy FFmpeg rendering when running on low battery (< 30%).
- **Idle Detection**: Detects user typing/mouse activity via Windows `GetLastInputInfo` and yields resources.
- **Disk Safety Floor**: Halts heavy transcode operations if SSD free space drops below 5.0 GB to avoid disk full crashes.

---

## 9. Platform Publishing Credentials

### YouTube Shorts
- Save your OAuth 2.0 client secrets in Google Cloud Console.
- When `publish_mode` is set to `private`, uploads will be created as private drafts in YouTube Studio for inspection.

### Instagram Reels
- Set `INSTAGRAM_ACCESS_TOKEN` and `INSTAGRAM_USER_ID` in your `.env` file.
- When credentials are not yet configured, Dispatch operates in simulation mode, tracking states in SQLite without crashing.

---

## 10. Running Automated Chaos & Failure Tests

Run the comprehensive test suite validating all 25 mission-critical failure scenarios (network drops, phone battery death, laptop power cuts, API 429 timeouts, disk full, lease recoveries):

```powershell
# Run all tests across the suite
python -m unittest discover tests

# Run specific chaos and resilience test
python -m unittest tests/test_chaos.py
```


