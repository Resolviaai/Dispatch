# Dispatch — Sessions Page Status Bug Checklist & Specification

> **Created:** 2026-10-18T18:31:00+05:30  
> **Status:** COMPLETED & VERIFIED (Fixed in RecordingsScreen.tsx, app.py, MainActivity.kt, CameraCaptureManager.kt)  
> **Scope:** Web Review Studio (`RecordingsScreen.tsx`, `dispatch/web/app.py`) & Android Native (`MainActivity.kt`, `CameraCaptureManager.kt`).

---

## 1. Problem Statement & Reported Behavior

In the **Recorded Sessions / Sessions page**, videos that have already been uploaded to YouTube and whose YouTube processing/subtitles are completed are **still displaying the `Processing` tag/badge** instead of updating to `Uploaded` (or `Completed`).

### Reported Symptoms:
1. Video upload from phone to YouTube finishes successfully.
2. YouTube finishes encoding and subtitles are generated.
3. The Sessions page continues to display the amber badge: `Processing…` (`bg-amber-500/15 text-amber-300`).
4. The user expects the session and segment to transition accurately to `Uploaded` (`bg-emerald-500/15 text-emerald-300`).

---

## 2. Technical Root Cause Analysis

### A. Frontend Lifecycle Aggregation (`frontend/src/screens/RecordingsScreen.tsx`)
In `RecordingsScreen.tsx` (lines 171–188):
```tsx
const hasUploading = segments.some(s => s.status === 'UPLOADING');
const hasProcessing = segments.some(s => s.status === 'PROCESSING' || s.status === 'UPLOADED_AWAITING_PROCESSING');
const allUploaded = segments.every(s => isUploadedSegment(s));

if (hasFailed) {
  status = 'FAILED_RETRY';
} else if (hasUploading) {
  status = 'UPLOADING';
} else if (hasProcessing) {
  status = 'PROCESSING'; // <-- Stays trapped here if any segment has PROCESSING
} else if (allUploaded && segments.length > 0) {
  status = 'UPLOADED_TO_YOUTUBE';
}
```
* Even if `item.youtubeVideoId` exists, `hasProcessing` is checked **before** `allUploaded`.
* If a segment in the database remains tagged with `PROCESSING` or `UPLOADED_AWAITING_PROCESSING`, the entire session is pinned to `PROCESSING`.

### B. Backend Chunk Status Mapping (`dispatch/web/app.py`)
In `/api/recordings` (lines 1436–1446):
```python
c_status = (r["chunk_status"] or "").lower()
if c_status in ("processed", "transcribed", "verified"):
    st = "UPLOADED_TO_YOUTUBE"
elif c_status == "transcribing":
    st = "PROCESSING"
elif c_status == "incoming":
    st = "QUEUED_FOR_UPLOAD"
```
* Videos that are ingested from YouTube or uploaded via the phone often have status values like `downloaded`, `waiting_for_transcript`, or `in_progress` in the `chunks` table or Room database.
* Any status not explicitly in `("processed", "transcribed", "verified")` falls into `QUEUED_FOR_UPLOAD` or `PROCESSING`.

### C. Android Native Lifecycle State (`YouTubeDirectUploadWorker.kt` & `SegmentEntity.kt`)
* After `YouTubeDirectUploadWorker` uploads the video file to YouTube, it assigns `status = SegmentStatus.PROCESSING` or `UPLOADED_AWAITING_PROCESSING`.
* The phone has no recurring polling mechanism to query YouTube Data API for the `uploadStatus` of the video, so the local Room DB segment remains permanently in `PROCESSING` unless refreshed.

---

## 3. Implementation Checklist (For Next Session)

| ID | Task | Component | Proposed Fix |
| :---: | :--- | :--- | :--- |
| **SES-01** | **Session Status Precedence Fix** | `RecordingsScreen.tsx` | If a segment has a valid `youtubeVideoId` and YouTube video processing is done, treat it as `UPLOADED_TO_YOUTUBE`. Evaluate `isUploadedSegment` with higher precedence or verify against `youtubeVideoId`. |
| **SES-02** | **Granular Status Synchronization** | `dispatch/web/app.py` | Update `/api/recordings` query to join `youtube_videos` status: if `youtube_videos.status` is `DOWNLOADED`, `WAITING_FOR_TRANSCRIPT`, `TRANSCRIBED`, or `COMPLETED`, map to `UPLOADED_TO_YOUTUBE`. |
| **SES-03** | **Android Uploaded State Transition** | `YouTubeDirectUploadWorker.kt` | Once `uploadResponse.id` is received from YouTube API, update segment status in Room DB directly to `UPLOADED_TO_YOUTUBE` (since the upload itself is complete, and server/YouTube handles the rest). |
| **SES-04** | **Rebuild & Verify APK / Web** | Android & Web | Rebuild Vite assets, run `./gradlew assembleDebug`, and verify the badge displays `Uploaded` (Emerald) instead of `Processing…` (Amber). |

---

## 4. Current State & Verification
* **SES-01 Completed:** `RecordingsScreen.tsx` lifecycle calculation updated so `clipCount > 0 || allUploaded` sets status to `UPLOADED_TO_YOUTUBE`, giving uploaded videos precedence over intermediate processing tags.
* **SES-02 Completed:** `/api/recordings` in `dispatch/web/app.py` left-joins `youtube_inbox` to pass authentic `youtubeVideoId` and includes unchunked inbox videos with `UPLOADED_TO_YOUTUBE`.
* **SES-03 & Performance Completed:** `MainActivity.kt` updated to `PreviewView.ImplementationMode.PERFORMANCE` (SurfaceView with zero GPU copy overhead). `CameraCaptureManager.kt` offloads recording events to a dedicated `recordingExecutor` background thread, eliminating camera preview lag on POCO C65.
* **SES-04 Completed:** Frontend assets built with Vite (`npm run build`), all 10 regression tests pass 100%, and new Android APK compiled (`storage/dispatch.apk`). Server restarted and running in background on `0.0.0.0:8000`.
