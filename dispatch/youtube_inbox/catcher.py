"""YouTube Inbox Catcher & Video Processing Pipeline.
Polls or accepts YouTube video URLs/IDs, downloads media via yt-dlp, extracts timestamped transcripts
(fetching YouTube auto-captions with an automatic fallback to local faster-whisper), runs semantic highlight
detection, renders vertical 9:16 clips, and populates the Review Dashboard idempotently.
"""
import re
import os
import json
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import yt_dlp

from dispatch.config import YOUTUBE_INBOX_DIR, CLIPS_DIR, DEFAULT_PUBLISH_MODE
from dispatch import db
from dispatch.youtube_inbox.vtt_parser import parse_vtt_file, parse_vtt_content
from dispatch.ai_clips.highlight_finder import identify_and_save_highlights
from dispatch.video_engine.renderer import render_clip
from dispatch.transcription.transcriber import transcribe_video

logger = logging.getLogger("dispatch.youtube_inbox.catcher")

# Regex to extract 11-character YouTube video ID
YOUTUBE_ID_REGEX = re.compile(
    r"(?:v=|\/|youtu\.be\/|embed\/|shorts\/)([a-zA-Z0-9_-]{11})(?:\?|&|$)"
)


def extract_youtube_video_id(url_or_id: str) -> Optional[str]:
    """Extract standard 11-character YouTube video ID from URL or return raw ID if valid."""
    cleaned = url_or_id.strip()
    if len(cleaned) == 11 and re.match(r"^[a-zA-Z0-9_-]{11}$", cleaned):
        return cleaned

    match = YOUTUBE_ID_REGEX.search(cleaned)
    if match:
        return match.group(1)
    return None


def _apply_auth_opts(ydl_opts: Dict[str, Any]) -> None:
    """Apply cookie authentication options if configured via environment variables."""
    cookies_file = os.getenv("DISPATCH_YTDLP_COOKIES_FILE")
    cookies_browser = os.getenv("DISPATCH_YTDLP_COOKIES_BROWSER")
    if cookies_file and Path(cookies_file).exists():
        ydl_opts["cookiefile"] = str(Path(cookies_file).resolve())
    elif cookies_browser:
        ydl_opts["cookiesfrombrowser"] = (cookies_browser,)


class YouTubeInboxCatcher:
    """Manages downloading, transcript extraction, and end-to-end processing of YouTube videos."""

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = storage_dir or YOUTUBE_INBOX_DIR
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def fetch_video_info(self, video_id: str) -> Dict[str, Any]:
        """Fetch video metadata without downloading video stream."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
        }
        _apply_auth_opts(ydl_opts)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "id": info.get("id", video_id),
                "title": info.get("title", f"YouTube Video {video_id}"),
                "channel_id": info.get("channel_id", ""),
                "channel": info.get("uploader", ""),
                "duration": float(info.get("duration", 0.0) or 0.0),
                "upload_date": info.get("upload_date", ""),
                "description": info.get("description", "")
            }

    def fetch_youtube_captions(self, video_id: str) -> Optional[List[Dict[str, Any]]]:
        """Attempt to fetch existing YouTube captions or auto-generated VTT subtitles."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        temp_sub_prefix = self.storage_dir / f"sub_{video_id}"
        
        ydl_opts = {
            "skip_download": True,
            "writeautomaticsub": True,
            "writesubtitles": True,
            "subtitleslangs": ["en.*", "hi.*", "en", "hi"],
            "subtitlesformat": "vtt",
            "outtmpl": f"{temp_sub_prefix}.%(ext)s",
            "quiet": True,
            "no_warnings": True,
        }
        _apply_auth_opts(ydl_opts)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            # Look for written .vtt files
            vtt_files = list(self.storage_dir.glob(f"sub_{video_id}*.vtt"))
            if vtt_files:
                # Choose preferred language: 1. English (en.*), 2. Hindi (hi.*), 3. First available
                target_file = None
                for f in vtt_files:
                    fname = f.name.lower()
                    if ".en." in fname or fname.endswith(".en.vtt") or ".en-" in fname:
                        target_file = f
                        break
                if not target_file:
                    for f in vtt_files:
                        fname = f.name.lower()
                        if ".hi." in fname or fname.endswith(".hi.vtt") or ".hi-" in fname:
                            target_file = f
                            break
                if not target_file:
                    target_file = vtt_files[0]

                logger.info("Found YouTube caption file %s for video %s", target_file.name, video_id)
                segments = parse_vtt_file(target_file)

                # Cleanup sub files
                for f in vtt_files:
                    try:
                        f.unlink()
                    except Exception:
                        pass

                if segments:
                    return segments

        except Exception as e:
            logger.warning("Error fetching YouTube captions via yt-dlp for %s: %s", video_id, e)

        # YouTube Data API v3 fallback for authorized channels
        try:
            from dispatch.youtube_inbox.oauth import get_youtube_service
            service = get_youtube_service()
            if service:
                captions_resp = service.captions().list(part="snippet", videoId=video_id).execute()
                items = captions_resp.get("items", [])
                if items:
                    target_cid = None
                    for it in items:
                        snip = it.get("snippet", {})
                        lang = snip.get("language", "").lower()
                        if lang.startswith("en") or lang.startswith("hi"):
                            target_cid = it.get("id")
                            break
                    if not target_cid and items:
                        target_cid = items[0].get("id")
                    if target_cid:
                        caption_content = service.captions().download(id=target_cid, tfmt="vtt").execute()
                        cap_str = caption_content.decode("utf-8", errors="replace") if isinstance(caption_content, bytes) else str(caption_content)
                        parsed = parse_vtt_content(cap_str)
                        if parsed:
                            logger.info("Retrieved %d segments via YouTube Data API caption track for %s", len(parsed), video_id)
                            return parsed
        except Exception as api_err:
            logger.debug("YouTube Data API caption retrieval fallback failed for %s: %s", video_id, api_err)

        return None

    def download_video(self, video_id: str) -> Path:
        """Download video file locally via yt-dlp, limiting resolution to 1080p MP4."""
        target_path = self.storage_dir / f"{video_id}.mp4"
        if target_path.exists() and target_path.stat().st_size > 1024:
            logger.info("Video %s already downloaded at %s", video_id, target_path)
            return target_path

        url = f"https://www.youtube.com/watch?v={video_id}"
        outtmpl = str(self.storage_dir / f"{video_id}.%(ext)s")

        ydl_opts = {
            "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
            "outtmpl": outtmpl,
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
        }
        _apply_auth_opts(ydl_opts)

        logger.info("Starting yt-dlp download for video %s", video_id)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if not target_path.exists():
            # Check if downloaded with different container and convert/rename
            # Exclude incomplete downloads, transcripts, and audio-only/video-only DASH splits
            ignored_patterns = re.compile(r"(\.part$|\.ytdl$|\.vtt$|\.aria2$|\.tmp$|\.temp$|\.f\d+\.)", re.IGNORECASE)
            candidates = [
                f for f in self.storage_dir.glob(f"{video_id}.*")
                if f != target_path and not ignored_patterns.search(f.name) and f.stat().st_size > 1024
            ]
            if candidates:
                # Pick the largest candidate file
                candidates.sort(key=lambda f: f.stat().st_size, reverse=True)
                cand = candidates[0]
                if cand.suffix.lower() != ".mp4":
                    # Convert/remux to MP4
                    proc = subprocess.run(
                        ["ffmpeg", "-y", "-i", str(cand), "-c", "copy", str(target_path)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False
                    )
                    if proc.returncode == 0 and target_path.exists() and target_path.stat().st_size > 1024:
                        cand.unlink(missing_ok=True)
                    else:
                        target_path.unlink(missing_ok=True)
                        raise RuntimeError(f"FFmpeg remux of candidate {cand.name} to MP4 failed with exit code {proc.returncode}")
                else:
                    cand.rename(target_path)

        if not target_path.exists():
            raise FileNotFoundError(f"Failed to download video {video_id}")

        logger.info("Downloaded %s successfully (%.2f MB)", video_id, target_path.stat().st_size / (1024 * 1024))
        return target_path

    def ingest_video(
        self,
        url_or_id: str,
        dispatch_id: Optional[str] = None,
        session_id: Optional[str] = None,
        sequence_number: Optional[int] = None
    ) -> Dict[str, Any]:
        """Pillar 1: Dedicated YouTube Ingestion & Media Validation.
        
        Contract:
          1. Discover & register video record in youtube_inbox idempotently (status: DISCOVERED).
          2. Transition to DOWNLOADING and download video via yt-dlp.
          3. Transition to VALIDATING and run real ffprobe validation (stream, decodable, duration, dimensions).
          4. Persist verified metadata and local path, then transition to DOWNLOADED.
          5. STOP. (Downstream stages consume DOWNLOADED records).
        """
        from dispatch.ingestion.validator import probe_video
        from dispatch.youtube_inbox.oauth import extract_dispatch_id, extract_session_metadata

        video_id = extract_youtube_video_id(url_or_id)
        if not video_id:
            raise ValueError(f"Invalid YouTube URL or ID: {url_or_id}")

        existing = db.get_youtube_video(video_id)
        # Idempotency check: If already DOWNLOADED or completed downstream, return existing record
        if existing and existing["status"] in (
            "DOWNLOADED", "WAITING_FOR_TRANSCRIPT", "TRANSCRIBED",
            "TRANSCRIPT_FETCHED", "ANALYZING", "CLIPS_DEFINED",
            "CLIPS_CREATED", "COMPLETED"
        ):
            local_path = Path(existing["local_video_path"]) if existing.get("local_video_path") else None
            if local_path and local_path.exists() and local_path.stat().st_size > 0:
                is_valid, _, _ = probe_video(local_path)
                if is_valid:
                    logger.info("Video %s is already downloaded and verified. Skipping duplicate ingest.", video_id)
                    return {
                        "video_id": video_id,
                        "status": existing["status"],
                        "local_video_path": str(local_path),
                        "verified": True
                    }

        logger.info("Starting Pillar 1 Ingestion for YouTube video: %s", video_id)

        # 1. Fetch metadata & register DISCOVERED
        info = {}
        try:
            info = self.fetch_video_info(video_id)
            desc = info.get("description", "")
            if not dispatch_id:
                dispatch_id = extract_dispatch_id(desc)
            s_sess, s_seg, s_seq = extract_session_metadata(desc)
            if not session_id:
                session_id = s_sess
            if sequence_number is None:
                sequence_number = s_seq
            if not dispatch_id and s_seg:
                dispatch_id = s_seg

            db.register_youtube_video(
                video_id=video_id,
                title=info.get("title", f"YouTube Video {video_id}"),
                channel_id=info.get("channel_id", ""),
                upload_time=info.get("upload_date"),
                duration=info.get("duration", 0.0),
                dispatch_id=dispatch_id,
                session_id=session_id,
                sequence_number=sequence_number
            )
        except Exception as e:
            logger.warning("Could not fetch remote info for video %s (%s). Registering placeholder.", video_id, e)
            db.register_youtube_video(
                video_id=video_id,
                title=f"YouTube Video {video_id}",
                dispatch_id=dispatch_id,
                session_id=session_id,
                sequence_number=sequence_number
            )

        db.update_youtube_video(
            video_id,
            status="DISCOVERED",
            dispatch_id=dispatch_id,
            session_id=session_id,
            sequence_number=sequence_number
        )

        # 1.5 Authoritative YouTube Data API status check before download attempt
        from dispatch.youtube_inbox.oauth import get_youtube_processing_status
        status_info = None
        try:
            status_map = get_youtube_processing_status([video_id])
            status_info = status_map.get(video_id)
        except Exception as e:
            logger.debug("Authoritative status check error for %s: %s", video_id, e)

        if status_info:
            p_status = status_info.get("processing_status")
            u_status = status_info.get("upload_status")
            fail_reason = status_info.get("failure_reason")

            if p_status == "processing" or (u_status == "uploaded" and p_status != "succeeded"):
                logger.info(
                    "Video %s is still processing on YouTube (processingStatus=%s, uploadStatus=%s). Deferring download.",
                    video_id, p_status, u_status
                )
                db.update_youtube_video(
                    video_id,
                    status="WAITING_FOR_YOUTUBE_PROCESSING",
                    last_error=f"Processing on YouTube (processingStatus={p_status}, uploadStatus={u_status})..."
                )
                return {
                    "video_id": video_id,
                    "status": "WAITING_FOR_YOUTUBE_PROCESSING",
                    "verified": False,
                    "processing": True
                }

            if p_status in ("failed", "terminated") or u_status in ("failed", "rejected"):
                err_msg = f"YouTube processing failed: {fail_reason or 'rejected'}"
                logger.error("Video %s failed processing on YouTube: %s", video_id, err_msg)
                db.update_youtube_video(video_id, status="FAILED", last_error=err_msg)
                return {
                    "video_id": video_id,
                    "status": "FAILED",
                    "verified": False,
                    "error": err_msg
                }

        # 2. Transition to DOWNLOADING & download media
        db.update_youtube_video(video_id, status="DOWNLOADING")
        try:
            local_video_path = self.download_video(video_id)
        except Exception as e:
            err = f"Download failed: {e}"
            err_str = str(e).lower()
            if "processing" in err_str or "check back later" in err_str:
                logger.info("Video %s is still processing on YouTube. Transitioning to WAITING_FOR_YOUTUBE_PROCESSING.", video_id)
                db.update_youtube_video(video_id, status="WAITING_FOR_YOUTUBE_PROCESSING", last_error=err)
                return {
                    "video_id": video_id,
                    "status": "WAITING_FOR_YOUTUBE_PROCESSING",
                    "verified": False,
                    "processing": True
                }
            logger.error("Download failed for video %s: %s", video_id, err)
            db.update_youtube_video(video_id, status="FAILED", last_error=err)
            raise RuntimeError(err) from e

        # 3. Transition to VALIDATING & run real ffprobe verification
        db.update_youtube_video(video_id, status="VALIDATING", local_video_path=str(local_video_path))
        is_valid, meta, err = probe_video(local_video_path)
        if not is_valid:
            error_msg = f"Media validation failed for {local_video_path.name}: {err}"
            logger.error(error_msg)
            # Remove corrupt or zero-byte file
            try:
                local_video_path.unlink(missing_ok=True)
            except Exception:
                pass
            db.update_youtube_video(video_id, status="FAILED", last_error=error_msg)
            raise ValueError(error_msg)

        # 4. Verified: Persist metadata & set status = DOWNLOADED
        total_duration = meta.get("duration", 0.0)
        db.update_youtube_video(
            video_id=video_id,
            status="DOWNLOADED",
            local_video_path=str(local_video_path)
        )

        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE youtube_inbox SET duration = ? WHERE video_id = ?",
                (total_duration, video_id)
            )
            conn.commit()

        logger.info("Pillar 1 Ingestion SUCCESS for %s -> DOWNLOADED (Duration: %.1fs, Dimensions: %dx%d)",
                    video_id, total_duration, meta.get("width", 0), meta.get("height", 0))

        return {
            "video_id": video_id,
            "status": "DOWNLOADED",
            "local_video_path": str(local_video_path),
            "metadata": meta,
            "verified": True
        }

    def process_video(
        self,
        url_or_id: str,
        session_id: Optional[str] = None,
        dispatch_id: Optional[str] = None,
        force_whisper: bool = False
    ) -> Dict[str, Any]:
        """[LEGACY DOWNSTREAM DEMO] Full end-to-end processing pipeline for a single YouTube video.
        WARNING: Do NOT use this in the active ingestion path! Active ingestion is handled
        strictly by `ingest_video()`, which stops at `DOWNLOADED` per the Pillar 1 contract.
        This function is preserved only for downstream testing until Pillars 2-4 take over.

        1. Extract and validate video ID.
        2. Idempotent check in SQLite DB.
        3. Fetch metadata and register in youtube_inbox with dispatch_id.
        4. Download video media (DOWNLOADING).
        5. Fetch transcript (TRANSCRIBING: YouTube captions / VTT with Whisper fallback).
        6. Detect semantic highlights (ANALYZING: Gemini Flash / heuristic).
        7. Render vertical 9:16 candidate clips (RENDERING: FFmpeg + ASS).
        8. Mark completed (CLIPS_CREATED -> ready_review) and return result summary.
        """
        video_id = extract_youtube_video_id(url_or_id)
        if not video_id:
            raise ValueError(f"Invalid YouTube URL or ID: {url_or_id}")

        # Check idempotency: If already completed and clips created, return cached record
        existing = db.get_youtube_video(video_id)
        if existing and existing["status"] in ("CLIPS_CREATED", "COMPLETED"):
            logger.info("Video %s has already been completely processed. Skipping duplicate run.", video_id)
            return {
                "video_id": video_id,
                "status": "ALREADY_PROCESSED",
                "clips_created": []
            }

        logger.info("Processing YouTube Inbox item: %s", video_id)

        # 1. Fetch metadata
        try:
            info = self.fetch_video_info(video_id)
            desc = info.get("description", "")
            if not dispatch_id:
                from dispatch.youtube_inbox.oauth import extract_dispatch_id
                dispatch_id = extract_dispatch_id(desc)
            if not session_id:
                from dispatch.youtube_inbox.oauth import extract_session_metadata
                s_sess, s_seg, s_seq = extract_session_metadata(desc)
                if s_sess:
                    session_id = s_sess
                if not dispatch_id and s_seg:
                    dispatch_id = s_seg

            db.register_youtube_video(
                video_id=video_id,
                title=info["title"],
                channel_id=info["channel_id"],
                upload_time=info["upload_date"],
                duration=info["duration"],
                dispatch_id=dispatch_id,
                session_id=session_id
            )
        except Exception as e:
            logger.error("Failed to fetch info for video %s: %s", video_id, e)
            db.register_youtube_video(
                video_id=video_id,
                title=f"YouTube Video {video_id}",
                dispatch_id=dispatch_id,
                session_id=session_id
            )
            info = {"title": f"YouTube Video {video_id}", "duration": 0.0}

        db.update_youtube_video(video_id, status="DOWNLOADING", dispatch_id=dispatch_id, session_id=session_id)

        # 2. Download media
        try:
            local_video_path = self.download_video(video_id)
            db.update_youtube_video(video_id, status="TRANSCRIBING", local_video_path=str(local_video_path))
        except Exception as e:
            error_msg = f"Failed to download video: {e}"
            logger.error(error_msg)
            db.update_youtube_video(video_id, status="FAILED", last_error=error_msg)
            raise

        # 3. Fetch Transcript (YouTube captions first -> Whisper fallback)
        segments: Optional[List[Dict[str, Any]]] = None
        transcript_source = "none"

        if not force_whisper:
            logger.info("Attempting to retrieve YouTube auto/manual captions for %s", video_id)
            segments = self.fetch_youtube_captions(video_id)
            if segments:
                transcript_source = "youtube"
                logger.info("Successfully loaded %d segments from YouTube captions", len(segments))

        if not segments:
            logger.info("YouTube captions unavailable. Falling back to local faster-whisper on %s", local_video_path.name)
            whisper_result = transcribe_video(local_video_path)
            segments = whisper_result.get("segments", [])
            transcript_source = "whisper"
            logger.info("Local faster-whisper generated %d segments", len(segments))

        db.update_youtube_video(
            video_id,
            status="TRANSCRIPT_FETCHED",
            transcript_source=transcript_source,
            segments_json=json.dumps(segments, ensure_ascii=False)
        )

        # 4. Probe video duration & aspect ratio if info["duration"] was 0
        total_duration = info["duration"]
        if total_duration <= 0.0:
            from dispatch.ingestion.validator import probe_video
            valid, meta, error = probe_video(local_video_path)
            if not valid:
                raise RuntimeError(f"Downloaded YouTube video is not readable: {error}")
            total_duration = meta.get("duration", 60.0)

        # Also register a chunk in the DB so clips have a valid chunk_id reference
        file_hash = f"yt_{video_id}"
        chunk_id = db.register_chunk(
            session_id=session_id,
            filename=local_video_path.name,
            filepath=str(local_video_path),
            file_hash=file_hash
        )
        db.update_chunk_metadata(
            chunk_id=chunk_id,
            duration=total_duration,
            width=1920,
            height=1080,
            aspect_ratio="16:9",
            status="transcribed"
        )

        full_text = " ".join(s["text"] for s in segments)
        db.save_transcript(
            chunk_id=chunk_id,
            session_id=session_id,
            full_text=full_text,
            segments=segments
        )

        # 5. Extract highlights (ANALYZING)
        db.update_youtube_video(video_id, status="ANALYZING")
        logger.info("Detecting candidate highlights for %s (Duration: %.1fs)", video_id, total_duration)
        publish_mode = db.get_setting("publish_mode", DEFAULT_PUBLISH_MODE) or "private"
        clip_ids = identify_and_save_highlights(
            chunk_id=chunk_id,
            session_id=session_id,
            segments=segments,
            total_duration=total_duration,
            publish_mode=publish_mode
        )

        logger.info("Identified %d candidate clips for YouTube video %s", len(clip_ids), video_id)
        if not clip_ids:
            raise RuntimeError(f"Gemini returned no renderable highlights for YouTube video {video_id}")

        # 6. Render candidate clips (RENDERING)
        db.update_youtube_video(video_id, status="RENDERING")
        rendered_clips = []
        for cid in clip_ids:
            with db.get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM clips WHERE id = ?", (cid,))
                c_row = cursor.fetchone()

            start_t = c_row["start_time"] if c_row else 0.0
            end_t = c_row["end_time"] if c_row else total_duration
            l_mode = (c_row["layout_mode"] if c_row else None) or "fit_blur"

            try:
                render_result = render_clip(
                    source_video=local_video_path,
                    clip_id=cid,
                    start_time=start_t,
                    end_time=end_t,
                    aspect_ratio="16:9",
                    layout_mode=l_mode,
                    segments=segments
                )
                db.update_clip_media(
                    clip_id=cid,
                    video_path=str(render_result.get("video_path", "")),
                    thumbnail_path=str(render_result.get("thumbnail_path", ""))
                )
                rendered_clips.append(cid)
            except Exception as e:
                logger.exception("Failed to render clip %s", cid)
                db.update_youtube_video(video_id, status="FAILED", last_error=f"FFmpeg render failed: {e}")
                raise

        if not rendered_clips:
            raise RuntimeError(f"FFmpeg did not produce an output clip for YouTube video {video_id}")

        db.update_youtube_video(video_id, status="CLIPS_CREATED")
        logger.info("YouTube Inbox processing complete for %s. %d clips ready for review.", video_id, len(rendered_clips))

        return {
            "video_id": video_id,
            "title": info["title"],
            "status": "CLIPS_CREATED",
            "transcript_source": transcript_source,
            "clips_count": len(rendered_clips),
            "clip_ids": rendered_clips
        }


def scan_channel_for_dispatch_uploads(
    channel_id: Optional[str] = None,
    marker: str = "[DISPATCH]",
    limit: int = 10
) -> List[Dict[str, Any]]:
    """Scan channel uploads using yt-dlp for videos matching marker prefix (e.g. [DISPATCH]).
    Returns list of discovered videos that have not yet been processed.
    """
    if not channel_id:
        return []

    # Use the YouTube uploads playlist ID: replacing 'UC' prefix with 'UU'
    if channel_id.startswith("UC"):
        target_url = f"https://www.youtube.com/playlist?list=UU{channel_id[2:]}"
    else:
        target_url = f"https://www.youtube.com/channel/{channel_id}/videos"

    logger.debug("Scanning YouTube uploads playlist %s for marker '%s'", target_url, marker)
    ydl_opts = {
        "extract_flat": "in_playlist",
        "playlistend": limit,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
    }

    discovered = []
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            res = ydl.extract_info(target_url, download=False)
            if not res:
                return []
            entries = res.get("entries", [])
            for item in entries:
                if not item:
                    continue
                title = item.get("title", "")
                v_id = item.get("id")
                if not v_id:
                    continue

                # Check if marker matches (if marker specified)
                if marker and marker.lower() not in title.lower():
                    continue

                if not db.is_youtube_video_processed(v_id):
                    discovered.append({
                        "video_id": v_id,
                        "title": title,
                        "duration": float(item.get("duration", 0.0) or 0.0),
                    })
    except Exception as e:
        logger.debug("Uploads playlist scan encounter note: %s", e)

    return discovered
