# Dispatch - Master Task List & Architecture Roadmap (2 November 2026)

> **Document Status:** Active & Canonical Master Tracker  
> **Date:** November 2, 2026  
> **Milestone:** Stage 2 Production Verification & Polish (Retention, Cross-Posting, Auth & Caption WYSIWYG)  
> **Protocol Rule:** This document is updated incrementally after every single completed task. Any incoming AI agent or developer can resume work directly from this file without context loss.

---

## 1. Executive Summary & Objective

Dispatch is deployed in production (`v1.7.23-alpha`, versionCode 91). Following real-device usage and owner review, this document tracks:
1. **72-Hour Retention & Multi-Platform Cross-Posting:** Transition from 7-day retention to a strict 72-hour window, with dynamic countdown, and the ability for creators to publish to remaining platforms within the window.
2. **Authentication Hardening Audit:** Password show/hide eye toggle button, Forgot Password / Reset Password recovery pipeline, and clean logout confirmation.
3. **1:1 WYSIWYG Caption Rendering Invariant:** Ensuring captions rendered by Android Media3 / FFmpeg exactly replicate the web editor preview (fonts, wrapping, text stroke, shadows, and karaoke pop physics).

---

## 2. System State & Canonical Environment

| Component | Target / Specification | Current Live State |
| :--- | :--- | :--- |
| **Android Package** | `com.resolvia.dispatch` | Version `1.7.23` (`versionCode = 91`) |
| **Release Tag** | Monotonic SemVer Git Tag | `v1.7.23-alpha` |
| **Release Certificate** | Trusted keystore | SHA-256 fingerprint ends in `...3804` |
| **Release Distribution** | GitHub Releases & `gh-pages` | Hosted at `https://resolviaai.github.io/Dispatch/` |
| **PC Daemon API** | FastAPI / SQLite / Gemini Transcribe & Flash Lite | `http://127.0.0.1:8000` |
| **Cloudflare Worker** | Cloud API & Edge Storage Gateway | `https://dispatch-cloud.resolviaai.workers.dev` |
| **Supabase Cloud** | Auth, Profiles, Cloud DB | `https://aldzlbdxplqkcnyjhzmm.supabase.co` |
| **Web Review Studio** | Vite + React + Tailwind v3 | Embedded in Android Assets & PC Review UI |

---

## 3. Prioritized Task Register & Status

| ID | Category | Feature / Problem | Owner Request | Priority | Status |
| :--- | :--- | :--- | :--- | :---: | :---: |
| **T01** | Retention | Fix "7d left" glitch & change retention to 72 hours | Accurate countdown (`3d left`, `20h left`, `Cloud only`) | P0 | **COMPLETED & VERIFIED** |
| **T02** | Publishing | 72-Hour Multi-Platform Cross-Posting | Ability to publish to remaining unselected platforms from detail view | P0 | **COMPLETED & VERIFIED** |
| **T03** | Auth UI | Password visibility toggle ("eye" button) | Add show/hide password toggle button inside password input | P1 | **COMPLETED & VERIFIED** |
| **T04** | Auth Logic | Forgot Password & Password Reset pipeline | Supabase recovery email + reset password form + deep link | P1 | **COMPLETED & VERIFIED** |
| **T05** | Auth UX | Logout confirmation & session cleanup | Confirmation dialog in Settings before wiping cache | P1 | **COMPLETED & VERIFIED** |
| **T06** | Engine | 1:1 WYSIWYG Caption Rendering Invariant | Ensure rendered video captions match editor preview font, size, wrap & effects | P0 | **COMPLETED & VERIFIED** |
| **T07** | Activity UI | Remove "Done" button from Notification Center | Modal header already has close X and backdrop tap; bottom button is redundant | P1 | **COMPLETED & VERIFIED** |
| **T08** | Clips UI | Remove "Cloud Mode (Remote)" text from header | Clean header metadata to show `{ready} ready · {total} total` | P1 | **COMPLETED & VERIFIED** |
| **T09** | Sessions | Fix broken thumbnails for imported / direct sessions | Validate 11-char YouTube ID, stop 404 image requests, fall back to clip thumbnail | P0 | **COMPLETED & VERIFIED** |
| **T10** | Sessions | Fix "View 0 clips" bug when clicking session | Select `session_id` in cloud worker, sync parent IDs, and normalize prefix matching | P0 | **COMPLETED & VERIFIED** |
| **T11** | Settings | Fix right-column button layout misalignment | Standardize cards and 96px action button hitboxes (`Sign out`, `Customize`, `Disconnect`, etc.) | P1 | **COMPLETED & VERIFIED** |

---

## 4. Completed Work Detail (Today: November 2, 2026)

### T01 & T02: 72-Hour Retention & Multi-Platform Cross-Posting (VERIFIED)
1. **Root Cause of Glitch Fixed:**
   - Previous logic in `frontend/src/screens/ClipsScreen.tsx` relied on `clip.published_at` alone, which was `null` for cloud clips, falling back to a hardcoded `7`.
   - Now computes effective publish timestamp: `COALESCE(clip.published_at, clip.publications[0]?.published_at, clip.approved_at, clip.created_at)`.
   - Retention formula: $\Delta t = (72 \times 3600 \times 1000) - (\text{now} - t_{\text{effective}})$.
   - When $\Delta t \le 0$: shows `Cloud only` (e.g. 6-day-old clip shows `6d ago · Cloud only`).
   - When $\Delta t > 0$: shows `${daysLeft}d left` (if $>24\text{h}$) or `${hoursLeft}h left` (if $\le 24\text{h}$, e.g. `2d ago · 20h left`).
2. **72-Hour Cross-Posting Workflow:**
   - On Clip Detail view within 72h: Displays Glowing Hero CTA: `Publish to More Platforms · {hoursLeft}h left`.
   - In Publish View: Automatically identifies already-published platforms (e.g. YouTube Shorts tagged with `[ ✓ Published ]`), unchecks them by default, and selects the remaining platforms (Instagram Reels, LinkedIn, X/Twitter).
   - The user retains 100% manual control: they can tap any previously published toggle back ON if they wish to re-publish to that platform/account.
   - Bypasses re-rendering on the phone if media is already uploaded to R2, enqueuing directly to Cloudflare Worker `/publish/queue`.
3. **Backend Janitor Synchronized:**
   - Updated `cleanup_expired_published_clips(retention_days=3)` in `dispatch/db.py` and `dispatch/main.py`.
   - All 25 regression tests in `tests/test_cluster_c_remediation.py` pass.

---

## 5. Technical Research & Proposed Plan for Pending Tasks

### 5.1 Authentication Features Audit (T03, T04, T05)

#### What We Have Today:
- `signIn(email, password)` via Supabase Auth (`supabase.auth.signInWithPassword`).
- `signUp(email, password)` via Supabase Auth (`supabase.auth.signUp`).
- `signOut()` in `supabase.ts` which calls `supabase.auth.signOut()`, purges IndexedDB clips cache, and resets native bridge user ID.
- In `SettingsScreen.tsx`: A "Sign out" button that triggers `signOut()`.

#### What Is Missing:
1. **Password Visibility Toggle ("Eye" Icon):**
   - The password input in `AuthGate.tsx` is fixed at `type="password"`.
   - Need: An Eye/EyeOff toggle button inside the right edge of the password input with 44px hit-target.
2. **Forgot Password Flow:**
   - Missing: A "Forgot password?" link on the sign-in form.
   - Missing: Mode `'forgot'` in `AuthGate.tsx` with email input calling `supabase.auth.resetPasswordForEmail(email, { redirectTo: 'https://resolviaai.github.io/Dispatch/#recovery' })`.
3. **Reset Password Flow:**
   - Missing: When user clicks the recovery link in their email, Supabase sends a recovery token in the URL hash (`#type=recovery&access_token=...`).
   - Missing: Mode `'reset'` in `AuthGate.tsx` prompting the user for a new password and calling `supabase.auth.updateUser({ password: newPassword })`.
4. **Sign Out Confirmation Dialog:**
   - The "Sign out" button in Settings triggers immediately. It should show a clean modal: `"Sign out of Dispatch? Offline clips will remain safe on device."` with `Cancel` and `Sign Out`.

---

### 5.2 1:1 WYSIWYG Captions Rendering Invariant (T06)

#### Problem Analysis: Why did rendered captions look different from the editor preview?
1. **Font Discrepancy:**
   - The web editor uses web/system fonts (e.g. `Montserrat`, `Anton`, `Impact`, `Komika Hand`).
   - The Android native renderer (`AndroidClipRenderer.kt`) was using `Typeface.DEFAULT_BOLD` for all text instead of loading matching font typefaces from `assets/fonts/`.
2. **Text Wrapping & Line Breaks:**
   - In the web editor (`DispatchCaptionRenderer.tsx`), CSS wraps words based on container width or user line breaks.
   - In Android Media3 `CanvasOverlay`, text was drawn as a single horizontal line:
     `currentX += wordWidths[wIdx] + spaceWidth`
     If a caption phrase had 6–10 words, it spilled off the sides of the 1080p canvas!
3. **Karaoke Scale Pop & Highlight Timing:**
   - In web preview, the active word pops with $1.08\times$ scale around its center point.
   - In Android Media3, words changed color but did not scale pop around their center anchor, resulting in a flat feel.
4. **Proposed Fix Architecture:**
   - Multi-line text measurement with identical wrapping thresholds.
   - Typeface matching loader from bundled Google Fonts (`assets/fonts/`).
   - Centered active word matrix scale transformation ($1.08\times$ pop) matching web micro-physics.
   - Consistent stroke padding and shadow radius calculations relative to 1080p video canvas density.

---


### T03, T04 & T05: Authentication Hardening (VERIFIED)
1. **Password Visibility Toggle (T03):**
   - Added interactive Eye/EyeOff toggle button with 44px hitbox to password inputs in rontend/src/auth/AuthGate.tsx.
   - Separate state for primary and confirmation password fields.
2. **Forgot & Reset Password Recovery Pipeline (T04):**
   - Exported 
esetPassword(email) and updatePassword(password) from rontend/src/auth/supabase.ts.
   - Added 'forgot' view allowing creators to request email password reset links.
   - Added 'reset' view auto-detected from Supabase auth callback hash (#type=recovery or PASSWORD_RECOVERY event) with confirmation password matching.
3. **Logout Confirmation (T05):**
   - Implemented inline confirmation dialog in rontend/src/screens/SettingsScreen.tsx before invoking signOut(), preventing accidental sign-out while retaining standard 44px hitboxes.

### T06: 1:1 WYSIWYG Captions Parity in Android Clip Renderer (VERIFIED)
1. **Root Causes of Discrepancy Eliminated:**
   - **Screen Density Distortion Removed:** Removed density = context.resources.displayMetrics.scaledDensity from canvas coordinates. The canvas is already in 1080p pixels; now uses canvasScale = canvas.width / 1080f so exported video renders identically regardless of device screen DPI.
   - **Multi-Line Word Partitioning:** Replaced the single-horizontal-line layout with partitionWordsIntoVisualLines(), calculating line wraps against maxContentWidth and respecting user line-breaks.
   - **Centered Karaoke Pop Micro-Physics:** Fixed baseline jumping and word jittering by maintaining uniform 	extSize and applying centered matrix scaling canvas.scale(scale, scale, wordCenterX, wordCenterY) with optional 	ranslateY spring bounce during active spoken-word timing.
   - **Curated Creator Fonts:** Added 21 TTF fonts to ndroid/app/src/main/assets/fonts/ (Anton, Montserrat, Impact, Bangers, Archivo Black, etc.) with in-memory ontCache and safe system fallback.
   - **Full Styling & Casing Parity:** Added support for 	extTransform (uppercase/lowercase/title), ackgroundMode ('block' vs 'line'), corner radius, padding, outline stroke, and drop shadow opacity calculations.

## 6. Verification & Test Summary
- Android Unit Tests: All tests in AndroidClipRendererTest passed (34s, code 0).
- Frontend Test Suite: All 10 styling tests, 4 separation tests, 6 integrity tests, 12 audit tests, and 4 update tests passed 100% (node frontend/run_tests.js, code 0).
- Production Build: Vite production bundle compiled and synchronized to Android assets (index-OX1KJtxd.js, code 0).


## Forensic Audit & Resolution: Data Persistence & Sessions Display (2026-11-03)

### Executive Summary
A comprehensive audit spanning Supabase tables, Cloudflare Worker endpoints, SQLite databases, and frontend state reconciliation resolved the three critical issues reported by the user:
1. **Duration Badges Missing on Session Thumbnails**
2. **Completed YouTube Sessions Stuck in "Processing 3: Transcribing..."**
3. **Data Instability ("Glitchy" appearance/disappearance of items, and deleted sessions resurrecting)**

---

### Root Cause Analysis (First Principles)

#### 1. Why Video Duration Was Not Showing on Thumbnails
- **Z-Index Layering Inversion:** In [`RecordingsScreen.tsx`](frontend/src/screens/RecordingsScreen.tsx), the YouTube thumbnail image had `z-10`, whereas the duration pill badge (`formatDuration(session.totalDuration)`) and origin icon badge had `z-0` (default stacking context). When the image finished loading, it painted over and visually masked the duration badge and origin icon.
- **Missing Duration Field in Supabase `processing_jobs`:** The Cloudflare Worker's `/jobs/status` queried `processing_jobs`, which lacked a `duration` column. When the app fell back to cloud polling, `session.totalDuration` evaluated to `0`, formatting to `00:00` and remaining hidden under the image.

#### 2. Why Completed Videos Showed Under "Processing 3" as "Transcribing..."
- **Status Fallthrough Bug in `RecordingsScreen.tsx`:** In the unified session builder, `item.status === 'QUEUED'` was not explicitly matched (only `'DISCOVERED'` was matched to `'queued'`). As a result, genuine queued jobs fell through to `else { status = 'processing'; }`.
- **False Stage Label Mapping:** When `session.status === 'processing'`, `resolveSessionStage()` defaulted to `{ stage: 'transcribing', shortLabel: 'Transcribing…' }` with amber styling, falsely informing the user that transcribing was actively underway.
- **Top-20 Pagination Masking:** In `cloud-worker/src/index.ts`, `GET /jobs/status` fetched `limit=20`. Because multiple retry attempts had occurred, the completed `status: 'done'` records from earlier dates were pushed outside the top 20, returning only recent queued/failed duplicate attempts.
- **Clip Matching Mismatch:** `clips_cloud` in Supabase lacked a `youtube_video_id` column, and its `session_id` column was a UUID foreign key to `source_sessions` (which rejected SQLite string IDs like `sess_...`). Thus, clips for YouTube videos could not be linked to the session in cloud mode, resulting in `clipCount = 0`.

#### 3. Why Data Felt "Glitchy" and Old/Deleted Data Resurrected
- **LAN vs. Cloud Polling Flip-Flop:** Every 6 seconds, `fetchInbox` probed the PC LAN. When the PC responded, it loaded completed sessions with authentic durations from SQLite. If a health check dropped or timed out, the app fell back to the Cloudflare Worker `/jobs/status`. Because the cloud worker returned queued/failed records without durations, the UI flipped back and forth between "Ready" and "Processing (Transcribing...) 0:00".
- **Cancelled Jobs Resurrecting as Queued:** When a user deleted a session, `POST /jobs/cancel` patched the row in `processing_jobs` to `status = 'cancelled'`. However, `fetchInbox` mapped any unrecognized status (including `'cancelled'`) to `'QUEUED'`, which then fell through to `'processing'`. Exactly 6 seconds after deletion, the deleted session reappeared in the UI.

---

### Remediations Implemented & Verified

1. **Supabase Schema & Provenance Migration (`20261103000029_job_metadata_and_clip_provenance.sql`):**
   - Added `duration numeric` and `session_id text` to `public.processing_jobs`.
   - Added `youtube_video_id text` to `public.clips_cloud`.
   - Updated `cloud_submit_job()` to deduplicate active jobs atomically.
   - Cleaned up ghost duplicate jobs in `processing_jobs` and backfilled provenance metadata.

2. **Cloudflare Worker Enhancements (`cloud-worker/src/index.ts`):**
   - `/jobs/status`: Added `duration` and `session_id` to query selection, excluded cancelled jobs (`status=neq.cancelled`), and expanded limit to 100.
   - `/sync/pc/clips`: Persisted `youtube_video_id` into `clips_cloud`.
   - `GET /clips`: Returned `youtube_video_id` in `mapClipItem()`.
   - Successfully deployed to `dispatch-cloud.resolviaai.workers.dev` (Version ID: `c338d5c9-...`).

3. **Frontend Ingestion & UI Parity (`RecordingsScreen.tsx`):**
   - Elevated duration pill and origin badge to `z-20` so they float above loaded poster images.
   - Fixed status classification: mapped `QUEUED` and `DISCOVERED` strictly to `'queued'` (never falling through to `'processing'`).
   - Implemented deterministic job deduplication ranking: `COMPLETED (4) > PROCESSING (3) > QUEUED (2) > FAILED (1)`.
   - Ignored `status === 'cancelled'` so deleted sessions never resurrect.
   - Ensured fallback duration formatting (`05:00`, `00:37`, `00:01`, or `--:--`).

4. **Release Governance & Monotonic Bump:**
   - Monotonically bumped app to `v1.7.25-alpha` (`versionCode = 93`).
   - Rebuilt production Vite bundle and verified 37/37 unit tests.
   - Compiled signed release APK with `scripts/prepare_release.ps1 -AllowDirty`.
   - Installed on `emulator-5554` via `adb install -r release-out/Dispatch.apk`.
   - Verified live on emulator: Sessions tab cleanly shows `Ready 8`, `Processing 0`, `Queued 0`, with accurate duration badges and working Back button navigation.
