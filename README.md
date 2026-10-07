# Dispatch: Autonomous Personal Content Engine

> **"You create the raw signal. Dispatch carries it the rest of the way."**

Dispatch is an autonomous, local-first content pipeline engineered to turn raw, multi-hour natural work and speaking sessions recorded on an Android phone into polished, high-retention vertical short-form videos (YouTube Shorts, Instagram Reels, LinkedIn, X).

Inspired by the "lead-miner" philosophy, Dispatch requires **zero manual intervention** once configured. There are no manual USB cable transfers, no timeline non-linear editing (NLE) drag-and-drops, and no multi-step render exports. 

---

## 1. System Philosophy: The 5-Step Human Loop

The system is built so the user only touches 5 high-value, tactile actions:
1. **Open Dispatch**: Open the app on your phone (POCO C65 or browser).
2. **Record**: Tap record when starting work or a speaking session.
3. **Stop**: Tap stop when you finish your session.
4. **Review Clips**: Open the Dispatch local dashboard on your PC at `http://localhost:8000`.
5. **Approve / Reject**: Tap **Approve** (or **Reject**). 
   - If set to **Auto-Publish**, the clip is automatically scheduled and posted publicly.
   - If set to **Private/Draft**, Dispatch uploads the clip as a draft or private post so you have total peace of mind and can inspect it on-platform before making it public.

**Everything between Stop and Review happens 100% autonomously in the background.**

---

## 2. Core Architecture: YouTube as Cloud Inbox

```text
📱 Phone (You)
   └── Record video → Stop → Auto-uploads to YouTube as Private
       (Tagged with durable dispatch_id in description/tags)
          │
          ▼
☁️ YouTube (Cloud Inbox)
   └── Stores raw video & generates automatic speech captions
          │
          ▼
💻 Laptop (Dispatch Catcher Daemon)
   └── Starts immediately on boot + polls every 10 min
       └── Discovers [DISPATCH] uploads matching dispatch_id
       └── Downloads the original video via yt-dlp
       └── Retrieves YouTube captions when available (local Whisper fallback)
       └── Sends the timestamped transcript to Gemini for clip selection
       └── FFmpeg cuts clips, formats 9:16 vertical video & burns ASS captions
          │
          ▼
🖥️ Review Dashboard (http://localhost:8000)
   └── Live clip preview → 1-click Approve → Dispatches across platforms
```

### Current architecture boundary
- The phone records and uploads directly to YouTube. It does not discover, pair with, or transfer video to the PC.
- The PC polls the authenticated YouTube account, downloads each Dispatch upload, processes it, and serves the review dashboard locally.
- If phone credentials are missing, the upload stays queued; there is no PC/LAN fallback.
- Private uploads are still the intended phone setting. Confirm that the PC can download a private upload before declaring the end-to-end milestone complete.

---

## 3. Hardware & Operating Environment

- **Host Machine**: Windows 11 (AMD Ryzen / Intel CPU, 16GB+ RAM).
- **Compute Strategy**: 
  - Zero reliance on NVIDIA CUDA (optimized for AMD CPU multithreading via `faster-whisper` CPU-int8).
  - FFmpeg hardware acceleration where available, with fast CPU software fallback.
  - No heavyweight Docker/WSL2 containers to safeguard SSD space.
- **Mobile Hardware**: POCO C65 (Android 14+), recording 1080p @ 30 fps.
- **Budget & Cost**: 100% free local infrastructure; optional pennies spent strictly on Google Gemini Flash API for semantic highlight extraction.

---

## 4. Directory Structure

```
Dispatch/
├── README.md                     # Master project documentation
├── requirements.txt              # Standard Python dependencies
├── pyproject.toml                # Modern package configuration
├── dispatch/
│   ├── config.py                 # Central configurations & directory paths
│   ├── db.py                     # SQLite database schema, migrations & CRUD operations
│   ├── youtube_inbox/            # YouTube Cloud Inbox & Catcher
│   │   ├── catcher.py            # Catcher core: download -> captions -> clips
│   │   ├── vtt_parser.py         # WebVTT subtitle parser & word boundary estimator
│   │   ├── oauth.py              # YouTube Data API OAuth client
│   │   └── poller.py             # Background daemon polling for new uploads
│   ├── ingestion/                # File watcher & validator for incoming drops
│   ├── transcription/            # Speech extraction & Hinglish word alignment (faster-whisper)
│   ├── ai_clips/                 # Semantic highlight finder & preference learner (Gemini Flash)
│   ├── video_engine/             # FFmpeg 9:16 reframing, .ass subtitles & rendering
│   ├── web/                      # FastAPI review dashboard (PC & mobile)
│   │   ├── app.py
│   │   └── templates/
│   ├── publisher/                # Multi-platform publishing (YouTube, Instagram, LinkedIn, X)
│   ├── governor/                 # Hardware resource governor (CPU priority, battery, disk floor)
│   ├── orchestrator/             # Checkpointed pipeline runner & crash recovery
│   └── main.py                   # Master daemon entry point
├── android/                      # Native Android Studio Project (POCO C65)
│   ├── app/                      # CameraX recorder, Room DB, YouTubeDirectUploadWorker
│   ├── gradlew, gradlew.bat      # Self-contained Gradle wrapper scripts
│   └── build.gradle.kts
├── storage/
│   ├── incoming/                 # Raw video chunks
│   ├── youtube_inbox/            # Downloaded YouTube Inbox media
│   ├── processing/               # Active workspace during transcription/cutting
│   ├── clips/                    # Finished candidate clips ready for review
│   └── database/
│       └── dispatch.db           # SQLite state, checkpoints, & preference logs
└── tests/                        # Comprehensive unit, chaos, destructive & inbox tests (29 tests)
```

---

## 5. Quickstart & How to Run

### Step 1: Install Dependencies
```powershell
# Clone repo
git clone https://github.com/Resolviaai/Dispatch.git
cd Dispatch

# Install Python requirements
pip install -r requirements.txt
```

### Step 2: Configure Environment (.env)
Create a `.env` file in the project root:
```ini
GEMINI_API_KEY=your_gemini_api_key_here
DISPATCH_WEB_PORT=8000
DISPATCH_PUBLISH_MODE=private
DISPATCH_YOUTUBE_POLL_INTERVAL=600
```

### Step 3: Start Dispatch Daemon
```powershell
python -m dispatch.main
```
You will see:
```text
BOOTING DISPATCH: AUTONOMOUS PERSONAL CONTENT ENGINE
Database initialized
Autonomous YouTube Cloud Inbox poller active
Launching Web Dashboard on http://127.0.0.1:8000
```

### Step 4: Access Dashboard
Open **http://localhost:8000** in your browser.

---

## 6. Phone Recording Setup (POCO C65)

### Option A: Native Android App (Recommended)
1. Download `Dispatch-POCO-C65-v1.apk` from the dashboard at `http://<your-pc-ip>:8000/download/dispatch.apk`.
2. Install on your phone.
3. Features:
   - CameraX FHD 1080p video recording.
   - Pro Camera controls: Front/Back lens toggle, LED torch, AE/AF locking, tap-to-focus.
   - Background `RecordingForegroundService` keeps recording even when screen is locked.
   - `YouTubeDirectUploadWorker` automatically uploads recordings to YouTube as Private upon tapping Stop.
4. In **Settings**, enter the authorized account's YouTube OAuth refresh token, client ID, and client secret. The app stores them on the phone and uploads directly to YouTube; it does not pair with the PC.

The phone and PC operate independently after YouTube is configured. Review generated clips at `http://localhost:8000` on the PC.

---

## 7. Windows Silent Auto-Start on Boot

To run Dispatch silently in the background whenever Windows boots:
```cmd
scripts\install_windows_startup.bat
```
Registers Dispatch in Windows Task Scheduler to run hidden with below-normal CPU priority.
To uninstall at any time, run:
```cmd
scripts\uninstall_windows_startup.bat
```

---

## 8. Verification & Test Suite

Run the full automated test suite covering all 29 unit, integration, chaos, destructive, and YouTube inbox scenarios:

```powershell
# Run all 29 tests
python -m unittest discover tests

# Run YouTube Inbox tests
python -m unittest tests/test_youtube_inbox.py

# Run crash recovery & destructive tests
python -m unittest tests/test_destructive.py

# Run chaos engineering tests
python -m unittest tests/test_chaos.py
```
All 29 tests pass with 100% green exit code.
