"""Pillar 3: Autonomous Highlight Selection Worker for YouTube Inbox.
Consumes records in youtube_inbox with status = 'TRANSCRIBED',
calls Gemini semantic highlight detection with live verified models and prompt conditioning,
validates and deduplicates candidates, and transitions state:
  TRANSCRIBED -> ANALYZING -> CLIPS_DEFINED.
"""
import os
import json
import time
import logging
import threading
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple

from dispatch import db
import dispatch.config
from dispatch.config import DEFAULT_PUBLISH_MODE, PROCESSING_DIR, GEMINI_API_KEY
from dispatch.video_engine.renderer import render_clip
from dispatch.captions import group_words_into_captions, export_to_ass
from dispatch.captions.presets import get_default_preset_id
from dispatch.ai_clips.highlight_finder import (
    call_gemini_generate_content,
    validate_and_filter_candidates,
    transliterate_segments_to_hinglish,
    MAX_HIGHLIGHTS_PER_CHUNK,
)
from dispatch.ai_clips.prompt_templates import HIGHLIGHT_SYSTEM_PROMPT, build_highlight_user_prompt
from dispatch.ai_clips.preference_learner import get_negative_feedback_prompt

logger = logging.getLogger("dispatch.ai_clips.worker")


def _parse_iso_or_sqlite_timestamp(ts_str: Optional[str]) -> Optional[datetime]:
    """Parse timestamp string from SQLite (YYYY-MM-DD HH:MM:SS or ISO-8601)."""
    if not ts_str:
        return None
    clean_ts = ts_str.strip().replace(" ", "T")
    try:
        dt = datetime.fromisoformat(clean_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            dt = datetime.strptime(ts_str.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


class HighlightWorker:
    """Standalone worker for Pillar 3 highlight selection contract.
    
    States:
      TRANSCRIBED -> ANALYZING -> CLIPS_DEFINED
    """

    def __init__(
        self,
        poll_interval_seconds: Optional[int] = None,
        max_attempts: int = 5,
        base_backoff_seconds: int = 30
    ):
        env_interval = os.getenv("DISPATCH_HIGHLIGHT_POLL_INTERVAL")
        self.poll_interval = poll_interval_seconds or (int(env_interval) if env_interval and env_interval.isdigit() else 15)
        self.max_attempts = max_attempts
        self.base_backoff_seconds = base_backoff_seconds

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self):
        """Start the background highlight worker thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("Highlight worker is already running.")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="HighlightWorkerThread", daemon=True)
        self._thread.start()
        logger.info(
            "Highlight worker started (Poll interval: %ds, Max attempts: %d)",
            self.poll_interval, self.max_attempts
        )

    def stop(self):
        """Signal worker to stop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            logger.info("Highlight worker stopped.")

    def _run_loop(self):
        """Periodic loop scanning for TRANSCRIBED records."""
        try:
            self.process_pending()
        except Exception as e:
            logger.error("Initial highlight scan error: %s", e)

        while not self._stop_event.is_set():
            if self._stop_event.wait(timeout=self.poll_interval):
                break
            try:
                self.process_pending()
            except Exception as e:
                logger.error("Error in highlight worker cycle: %s", e)

    def process_pending(self) -> List[Dict[str, Any]]:
        """Scan DB for items requiring highlight extraction or unrendered clips."""
        with self._lock:
            processed = []
            pending_items = db.list_youtube_inbox(status="TRANSCRIBED", limit=20)
            now_dt = datetime.now(timezone.utc)

            for item in pending_items:
                video_id = item["video_id"]
                next_attempt_str = item.get("next_ai_attempt_at")
                if next_attempt_str:
                    next_attempt_dt = _parse_iso_or_sqlite_timestamp(next_attempt_str)
                    if next_attempt_dt and now_dt < next_attempt_dt:
                        wait_sec = (next_attempt_dt - now_dt).total_seconds()
                        logger.debug("Skipping %s for highlight analysis: backoff active (%.1fs remaining)",
                                     video_id, wait_sec)
                        continue

                try:
                    result = self.process_video_highlights(video_id)
                    if result:
                        processed.append(result)
                except Exception as e:
                    logger.error("Error processing highlights for %s: %s", video_id, e)

            # Also verify and auto-render any existing CLIPS_DEFINED items
            defined_items = db.list_youtube_inbox(status="CLIPS_DEFINED", limit=10)
            for item in defined_items:
                video_id = item["video_id"]
                try:
                    chunk_id, session_id, segments, duration = self._resolve_chunk_and_transcript(video_id, item)

                    # Cross-boundary highlight support: check for multi-segment session transcript stitching
                    if session_id:
                        stitched = self._resolve_session_stitched_transcript(session_id)
                        if stitched:
                            segments, duration = stitched
                            logger.info("Using session-stitched transcript for %s across session %s (total duration: %.1fs)", video_id, session_id, duration)
                    with db.get_db_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT id FROM clips WHERE chunk_id = ?", (chunk_id,))
                        rows = cursor.fetchall()
                        clip_ids = [r["id"] for r in rows] if rows else []

                    local_path_str = item.get("local_video_path")
                    src_path = Path(local_path_str) if local_path_str else None
                    if not src_path or not src_path.exists():
                        if chunk_id:
                            chk = db.get_chunk_by_id(chunk_id)
                            if chk and chk.get("filepath"):
                                cp = Path(chk["filepath"])
                                if cp.exists():
                                    src_path = cp

                    user_id = item.get("user_id")
                    active_render_key = ""
                    if user_id:
                        try:
                            from dispatch.youtube_connections import get_user_gemini_key
                            active_render_key = get_user_gemini_key(user_id) or ""
                        except Exception:
                            pass
                    else:
                        from dispatch.config import GEMINI_API_KEY
                        active_render_key = os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY

                    self._render_clips_for_video(video_id, clip_ids, segments, src_path, session_id=session_id, api_key=active_render_key)
                    # Verify rendered outputs exist on disk before marking COMPLETED
                    if clip_ids:
                        all_rendered = True
                        for cid in clip_ids:
                            c_row = db.get_clip_by_id(cid)
                            if c_row and c_row.get("status") == "queued_transcription":
                                continue
                            vp = Path(c_row["video_path"]) if (c_row and c_row.get("video_path")) else None
                            if not vp or not vp.exists() or vp.stat().st_size == 0:
                                all_rendered = False
                                break
                        if all_rendered:
                            db.update_youtube_video(video_id=video_id, status="COMPLETED")
                            logger.info("All clips rendered or queued for video %s -> COMPLETED", video_id)
                        else:
                            logger.warning("Not all clips rendered yet for video %s; maintaining CLIPS_DEFINED", video_id)
                    else:
                        db.update_youtube_video(video_id=video_id, status="COMPLETED")
                except Exception as e:
                    logger.error("Error rendering CLIPS_DEFINED item %s: %s", video_id, e)

            return processed

    def _resolve_chunk_and_transcript(self, video_id: str, item: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], List[Dict[str, Any]], float]:
        """Resolve chunk_id, session_id, transcript segments, and duration."""
        file_hash = f"yt_{video_id}"
        chunk = db.get_chunk_by_file_hash(file_hash)
        chunk_id = chunk["id"] if chunk else None
        session_id = (chunk.get("session_id") if chunk else None) or item.get("session_id")
        duration = float(item.get("duration", 0.0) or 0.0)

        if chunk and chunk.get("duration"):
            duration = float(chunk["duration"])

        # Try loading segments from item first
        segments: List[Dict[str, Any]] = []
        raw_seg = item.get("segments_json")
        if raw_seg:
            try:
                segments = json.loads(raw_seg)
            except Exception:
                segments = []

        # If not found or empty, try from transcripts table
        if not segments and chunk_id:
            with db.get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT segments_json FROM transcripts WHERE chunk_id = ?", (chunk_id,))
                t_row = cursor.fetchone()
                if t_row and t_row["segments_json"]:
                    try:
                        segments = json.loads(t_row["segments_json"])
                    except Exception:
                        segments = []

        return chunk_id, session_id, segments, duration

    def _resolve_session_stitched_transcript(
        self,
        session_id: str
    ) -> Optional[Tuple[List[Dict[str, Any]], float]]:
        """If multiple sequence segments exist for a session, stitch their transcripts with cumulative offsets
        so highlight extraction can seamlessly span segment boundaries."""
        if not session_id:
            return None
        try:
            session_items = db.get_session_inbox_items(session_id)
            if len(session_items) <= 1:
                return None
            stitched_segments = []
            total_duration = 0.0
            for part in session_items:
                part_dur = float(part.get("duration", 0.0) or 0.0)
                raw_s = part.get("segments_json")
                part_segs = []
                if raw_s:
                    try:
                        part_segs = json.loads(raw_s) if isinstance(raw_s, str) else raw_s
                    except Exception:
                        pass
                if not part_segs:
                    return None
                for s in part_segs:
                    offset_s = dict(s)
                    offset_s["start"] = float(s.get("start", 0.0)) + total_duration
                    offset_s["end"] = float(s.get("end", 0.0)) + total_duration
                    if "words" in s and isinstance(s["words"], list):
                        offset_s["words"] = [
                            {
                                **w,
                                "start": float(w.get("start", 0.0)) + total_duration,
                                "end": float(w.get("end", 0.0)) + total_duration
                            }
                            if isinstance(w, dict) else w
                            for w in s["words"]
                        ]
                    stitched_segments.append(offset_s)
                total_duration += part_dur
            return stitched_segments, total_duration
        except Exception as e:
            logger.warning("Session transcript stitch error for %s: %s", session_id, e)
            return None

    def _resolve_clip_media_source(
        self,
        clip: Dict[str, Any],
        default_src_path: Optional[Path],
        session_items: List[Dict[str, Any]]
    ) -> Tuple[Optional[Path], float, float, float, float, Optional[Path]]:
        """Resolve the effective media file and local clip timestamps for multi-part sessions."""
        c_start = float(clip["start_time"])
        c_end = float(clip["end_time"])
        p_start = float(clip["padded_start"]) if clip.get("padded_start") is not None else max(0.0, c_start - 4.0)
        p_end = float(clip["padded_end"]) if clip.get("padded_end") is not None else (c_end + 4.0)

        if not session_items or len(session_items) <= 1:
            return default_src_path, c_start, c_end, p_start, p_end, None

        # Build sequence windows with cumulative offsets
        windows = []
        curr_offset = 0.0
        for it in session_items:
            dur = float(it.get("duration", 0.0) or 0.0)
            p_str = it.get("local_video_path")
            p = Path(p_str) if p_str and Path(p_str).exists() else None
            windows.append({
                "item": it,
                "start": curr_offset,
                "end": curr_offset + dur,
                "path": p
            })
            curr_offset += dur

        # 1. Single segment containment check
        for w in windows:
            if c_start >= w["start"] and c_end <= w["end"] and w["path"]:
                off = w["start"]
                return w["path"], c_start - off, c_end - off, max(0.0, p_start - off), min(w["end"] - off, p_end - off), None

        # 2. Boundary crossing check across segment transition
        overlapping = [w for w in windows if w["end"] > p_start and w["start"] < p_end and w["path"]]
        if len(overlapping) == 1:
            w = overlapping[0]
            off = w["start"]
            return w["path"], max(0.0, c_start - off), c_end - off, max(0.0, p_start - off), p_end - off, None

        if len(overlapping) > 1:
            try:
                import tempfile
                import subprocess
                from dispatch.config import PROCESSING_DIR
                PROCESSING_DIR.mkdir(parents=True, exist_ok=True)
                temp_stitched = tempfile.NamedTemporaryFile(
                    prefix=f"boundary_stitch_{clip['id']}_", suffix=".mp4", dir=str(PROCESSING_DIR), delete=False
                )
                temp_stitched_path = Path(temp_stitched.name)
                temp_stitched.close()

                concat_txt = temp_stitched_path.with_suffix(".txt")
                with open(concat_txt, "w", encoding="utf-8") as f:
                    for w in overlapping:
                        f.write(f"file '{w['path'].resolve().as_posix()}'\n")

                cmd = [
                    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(concat_txt),
                    "-c", "copy",
                    str(temp_stitched_path)
                ]
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60)
                if concat_txt.exists():
                    concat_txt.unlink()

                if res.returncode == 0 and temp_stitched_path.exists() and temp_stitched_path.stat().st_size > 0:
                    first_off = overlapping[0]["start"]
                    return temp_stitched_path, c_start - first_off, c_end - first_off, p_start - first_off, p_end - first_off, temp_stitched_path
                else:
                    if temp_stitched_path.exists():
                        temp_stitched_path.unlink()
                    logger.error("FFmpeg boundary stitch failed for multi-segment clip %s (code %s); aborting invalid single-segment fallback", clip.get("id"), res.returncode)
                    return None, 0.0, 0.0, 0.0, 0.0, None
            except Exception as stitch_err:
                logger.error("Failed to stitch boundary segments for clip %s: %s; aborting invalid single-segment fallback", clip.get("id"), stitch_err)
                return None, 0.0, 0.0, 0.0, 0.0, None

        return default_src_path, c_start, c_end, p_start, p_end, None

    def _render_clips_for_video(
        self,
        video_id: str,
        saved_ids: List[str],
        segments: List[Dict[str, Any]],
        source_video_path: Optional[Path],
        session_id: Optional[str] = None,
        api_key: Optional[str] = None
    ):
        """Render candidate clips with Stage 2 precision transcription and FFmpeg."""
        if not saved_ids:
            return

        from dispatch.transcription.gemini_engine import (
            check_and_increment_daily_transcribe_quota,
            extract_stage2_clip_audio,
            transcribe_stage2_precision_words,
        )

        session_items = db.get_session_inbox_items(session_id) if session_id else []

        for clip_id in saved_ids:
            clip = db.get_clip_by_id(clip_id)
            if not clip:
                continue

            # Check if clip is already rendered and non-empty
            if clip.get("video_path"):
                vp = Path(clip["video_path"])
                if vp.exists() and vp.stat().st_size > 0:
                    logger.debug("Clip %s already rendered (%s). Skipping duplicate render.", clip_id, vp.name)
                    continue

            # Check if clip is already waiting in backlog
            if clip.get("status") == "queued_transcription":
                logger.debug("Clip %s is queued in backlog. Skipping render until quota drain.", clip_id)
                continue

            # Daily Quota Budget Guard (gemini-3.5-transcribe 25 RPD free tier ceiling)
            has_quota = check_and_increment_daily_transcribe_quota(max_daily_requests=25)
            if not has_quota:
                with db.get_db_connection() as conn:
                    conn.cursor().execute("UPDATE clips SET status = 'queued_transcription' WHERE id = ?", (clip_id,))
                    conn.commit()
                logger.info("Daily transcribe quota (25/25) reached. Clip %s routed to backlog queue ('queued_transcription').", clip_id)
                continue

            (clip_src_path, eff_c_start, eff_c_end, eff_p_start, eff_p_end, temp_cleanup) = (
                self._resolve_clip_media_source(clip, source_video_path, session_items)
            )
            range_revision = int(clip.get("range_revision") or 0)
            tpl = db.get_default_template(user_id=clip.get("user_id"))
            tpl_layout = tpl.get("framing", {}).get("horizontal_mode") or "crop_916"
            c_layout = clip.get("layout_mode") or tpl_layout

            active_segs: List[Dict[str, Any]] = []

            # Stage 2: Precision Word Alignment via gemini-3.5-transcribe
            try:
                if not clip_src_path or not clip_src_path.exists():
                    raise RuntimeError("Cannot extract bounded Stage 2 audio: source video is unavailable")
                PROCESSING_DIR.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(
                    prefix=f"dispatch_stage2_{clip_id}_", dir=str(PROCESSING_DIR)
                ) as temp_dir:
                    slice_mp3 = Path(temp_dir) / "clip_audio.mp3"
                    extract_stage2_clip_audio(clip_src_path, eff_p_start, eff_p_end, slice_mp3)
                    stage2_res = transcribe_stage2_precision_words(slice_mp3, api_key=api_key)

                words = stage2_res.get("words", [])
                if words:
                    # 2.2 Word-Snap: tight cut to millisecond start and end of spoken syllables
                    speech_start = eff_p_start + stage2_res.get("speech_start", 0.0)
                    speech_end = eff_p_start + stage2_res.get("speech_end", eff_c_end - eff_p_start)
                    snapped_start = max(eff_p_start, round(speech_start - 0.2, 2))
                    snapped_end = min(eff_p_end, round(speech_end + 0.3, 2))
                    range_trimmed = db.update_clip_trim(
                        clip_id, snapped_start, snapped_end,
                        expected_range_revision=range_revision,
                    )
                    if range_trimmed:
                        eff_c_start = snapped_start
                        eff_c_end = snapped_end
                    latest = db.get_clip_by_id(clip_id)
                    if not latest or abs(float(latest["start_time"]) - eff_c_start) > 0.01 or abs(float(latest["end_time"]) - eff_c_end) > 0.01:
                        logger.info("Skipping stale Stage 2 result for %s after its range changed", clip_id)
                        if temp_cleanup and temp_cleanup.exists():
                            temp_cleanup.unlink(missing_ok=True)
                        continue
                    range_revision = int(latest.get("range_revision") or 0)
                    if not range_trimmed:
                        logger.info(
                            "Keeping original clip range for %s because the speech-tightened range "
                            "is outside source or product limits.", clip_id
                        )

                    # 5.1 Word-by-Word Caption Data Mapping
                    clean_words = []
                    for w in words:
                        w_s = round(eff_p_start + w.get("start", 0.0), 3)
                        w_e = round(eff_p_start + w.get("end", 0.0), 3)
                        if w_e > eff_c_start and w_s < eff_c_end:
                            clean_words.append({
                                "word": w.get("word", ""),
                                "start": w_s,
                                "end": w_e
                            })

                    track = group_words_into_captions(
                        raw_segments=[{"id": 0, "start": eff_c_start, "end": eff_c_end, "words": clean_words}],
                        clip_start=eff_c_start,
                        clip_end=eff_c_end,
                        clip_id=clip_id,
                        preset_id=(db.get_default_template(user_id=clip.get("user_id")).get("captions", {}).get("preset_id") or get_default_preset_id())
                    )
                    track_dict = track.to_dict()
                    track_dict["words"] = clean_words
                    if not db.save_clip_caption_data(
                        clip_id, json.dumps(track_dict), expected_range_revision=range_revision,
                        source_range_start=eff_c_start, source_range_end=eff_c_end,
                    ):
                        logger.info("Skipping stale caption save for %s after its range changed", clip_id)
                        if temp_cleanup and temp_cleanup.exists():
                            temp_cleanup.unlink(missing_ok=True)
                        continue
                    ass_path = PROCESSING_DIR / f"{clip_id}.ass"
                    export_to_ass(track, ass_path)
                    active_segs = clean_words
                else:
                    # Fallback to coarse Stage 1 segments if Stage 2 found no words
                    clip_segs = [s for s in segments if s.get("end", 0.0) > float(clip["start_time"]) and s.get("start", 0.0) < float(clip["end_time"])]
                    offset_diff = float(clip["start_time"]) - eff_c_start
                    if offset_diff != 0.0:
                        shifted_segs = []
                        for cs in clip_segs:
                            scs = dict(cs)
                            scs["start"] = max(0.0, float(cs.get("start", 0.0)) - offset_diff)
                            scs["end"] = max(0.0, float(cs.get("end", 0.0)) - offset_diff)
                            shifted_segs.append(scs)
                        clip_segs = shifted_segs

                    clean_segs = transliterate_segments_to_hinglish(clip_segs)
                    track = group_words_into_captions(
                        raw_segments=clean_segs,
                        clip_start=eff_c_start,
                        clip_end=eff_c_end,
                        clip_id=clip_id,
                        preset_id=(db.get_default_template(user_id=clip.get("user_id")).get("captions", {}).get("preset_id") or get_default_preset_id())
                    )
                    if not db.save_clip_caption_data(
                        clip_id, json.dumps(track.to_dict()), expected_range_revision=range_revision,
                        source_range_start=eff_c_start, source_range_end=eff_c_end,
                    ):
                        logger.info("Skipping stale caption save for %s after its range changed", clip_id)
                        if temp_cleanup and temp_cleanup.exists():
                            temp_cleanup.unlink(missing_ok=True)
                        continue
                    ass_path = PROCESSING_DIR / f"{clip_id}.ass"
                    export_to_ass(track, ass_path)
                    active_segs = clean_segs

            except Exception as e:
                logger.warning("Stage 2 precision transcribe encountered error for clip %s: %s", clip_id, e)
                # If quota/rate limit error, route to backlog queue
                if "quota" in str(e).lower() or "429" in str(e) or "resourceexhausted" in str(e).lower():
                    with db.get_db_connection() as conn:
                        conn.cursor().execute("UPDATE clips SET status = 'queued_transcription' WHERE id = ?", (clip_id,))
                        conn.commit()
                    logger.info("Clip %s routed to backlog queue due to quota exhaustion.", clip_id)
                    if temp_cleanup and temp_cleanup.exists():
                        temp_cleanup.unlink(missing_ok=True)
                    continue

                # Otherwise fallback to Stage 1 segments
                clip_segs = [s for s in segments if s.get("end", 0.0) > float(clip["start_time"]) and s.get("start", 0.0) < float(clip["end_time"])]
                offset_diff = float(clip["start_time"]) - eff_c_start
                if offset_diff != 0.0:
                    shifted_segs = []
                    for cs in clip_segs:
                        scs = dict(cs)
                        scs["start"] = max(0.0, float(cs.get("start", 0.0)) - offset_diff)
                        scs["end"] = max(0.0, float(cs.get("end", 0.0)) - offset_diff)
                        shifted_segs.append(scs)
                    clip_segs = shifted_segs

                clean_segs = transliterate_segments_to_hinglish(clip_segs)
                try:
                    track = group_words_into_captions(
                        raw_segments=clean_segs,
                        clip_start=eff_c_start,
                        clip_end=eff_c_end,
                        clip_id=clip_id,
                        preset_id=(db.get_default_template(user_id=clip.get("user_id")).get("captions", {}).get("preset_id") or get_default_preset_id())
                    )
                    if not db.save_clip_caption_data(
                        clip_id, json.dumps(track.to_dict()), expected_range_revision=range_revision,
                        source_range_start=eff_c_start, source_range_end=eff_c_end,
                    ):
                        logger.info("Skipping stale caption save for %s after its range changed", clip_id)
                        if temp_cleanup and temp_cleanup.exists():
                            temp_cleanup.unlink(missing_ok=True)
                        continue
                    ass_path = PROCESSING_DIR / f"{clip_id}.ass"
                    export_to_ass(track, ass_path)
                    active_segs = clean_segs
                except Exception:
                    pass

            # 5.2 Animated Pop-Up Subtitle Burner via FFmpeg
            if clip_src_path and clip_src_path.exists():
                try:
                    logger.info("Auto-rendering clip %s (%.1fs - %.1fs) with FFmpeg...", clip_id, eff_c_start, eff_c_end)
                    render_clip(
                        source_video=clip_src_path,
                        clip_id=clip_id,
                        start_time=eff_c_start,
                        end_time=eff_c_end,
                        aspect_ratio=None,
                        layout_mode=c_layout,
                        segments=active_segs,
                        subtitle_options={"enabled": False, "preserve_timing": True},
                        expected_range_revision=range_revision,
                    )
                    with db.get_db_connection() as conn:
                        conn.cursor().execute("UPDATE clips SET status = 'ready_review' WHERE id = ?", (clip_id,))
                        conn.commit()
                except Exception as e:
                    logger.error("Auto-render failed for clip %s: %s", clip_id, e)
            else:
                logger.warning("Source video file not found on disk for auto-rendering clip %s", clip_id)

            if temp_cleanup and temp_cleanup.exists():
                try:
                    temp_cleanup.unlink(missing_ok=True)
                except Exception:
                    pass

    def process_video_highlights(self, video_id: str) -> Optional[Dict[str, Any]]:
        """Run Pillar 3 highlight analysis pipeline for a single video.
        
        Transitions:
          1. TRANSCRIBED -> ANALYZING
          2. Zero-speech: marks CLIPS_DEFINED with 0 clips directly.
          3. Call Gemini (strictly fails on error, never silent heuristic).
          4. Candidate validation and deduplication.
          5. Save clip definitions, transliterate to Roman Hinglish, and auto-render with FFmpeg -> COMPLETED.
        """
        item = db.get_youtube_video(video_id)
        if not item:
            logger.warning("Video %s not found in inbox. Skipping.", video_id)
            return None

        if item.get("status") not in ("TRANSCRIBED", "CLIPS_DEFINED"):
            logger.info("Video %s is in status '%s', expected 'TRANSCRIBED' or 'CLIPS_DEFINED'. Skipping.",
                        video_id, item.get("status"))
            return None

        chunk_id, session_id, segments, duration = self._resolve_chunk_and_transcript(video_id, item)

        # Cross-boundary highlight support: check for multi-segment session transcript stitching
        if session_id:
            stitched = self._resolve_session_stitched_transcript(session_id)
            if stitched:
                segments, duration = stitched
                logger.info("Using session-stitched transcript for %s across session %s (total duration: %.1fs)", video_id, session_id, duration)

        # Transition to ANALYZING
        db.update_youtube_video(video_id=video_id, status="ANALYZING")
        logger.info("Transitioned %s to ANALYZING", video_id)

        # Handle zero-speech / empty transcript immediately
        if not segments or not any(s.get("text", "").strip() for s in segments):
            logger.info("No speech detected for %s. Marking CLIPS_DEFINED with 0 clips.", video_id)
            db.save_clip_definitions(
                chunk_id=chunk_id,
                session_id=session_id,
                video_id=video_id,
                clip_defs=[]
            )
            db.update_youtube_video(video_id=video_id, status="CLIPS_DEFINED")
            return {
                "video_id": video_id,
                "status": "CLIPS_DEFINED",
                "clips_count": 0,
                "model_used": None
            }

        # Format transcript lines with timestamps and pause markers for LLM
        transcript_lines = []
        prev_end = 0.0
        for s in segments:
            s_start = float(s.get("start", 0.0))
            s_end = float(s.get("end", 0.0))
            if prev_end > 0.0:
                pause_gap = s_start - prev_end
                if pause_gap >= 0.5:
                    transcript_lines.append(f"[PAUSE: {pause_gap:.1f}s]")

            start_m, start_s = divmod(s_start, 60)
            end_m, end_s = divmod(s_end, 60)
            transcript_lines.append(f"[{int(start_m):02d}:{start_s:05.2f} -> {int(end_m):02d}:{end_s:05.2f}] {s.get('text', '')}")
            prev_end = max(prev_end, s_end)

        transcript_text = "\n".join(transcript_lines)

        negative_context = get_negative_feedback_prompt()
        user_prompt = build_highlight_user_prompt(transcript_text, negative_context)

        attempt_count = int(item.get("ai_attempt_count") or 0) + 1

        # Resolve user's vaulted Gemini key if available; fail closed if user has no key configured
        user_id = item.get("user_id")
        if user_id:
            user_gemini_key = ""
            try:
                from dispatch.youtube_connections import get_user_gemini_key
                user_gemini_key = get_user_gemini_key(user_id)
            except Exception as ke:
                logger.debug("User Gemini key lookup note for %s: %s", user_id, ke)

            if not user_gemini_key:
                err_msg = f"User {user_id} has not configured a Gemini API key in Settings."
                db.update_youtube_video(video_id=video_id, status="FAILED", last_ai_error=err_msg)
                raise RuntimeError(err_msg)
            active_gemini_key = user_gemini_key
        else:
            active_gemini_key = GEMINI_API_KEY
            if not active_gemini_key:
                err_msg = "GEMINI_API_KEY is not configured or is empty. Cannot extract highlights."
                db.update_youtube_video(video_id=video_id, status="FAILED", last_ai_error=err_msg)
                raise RuntimeError(err_msg)

        try:
            # Strictly call Gemini API with active key (user-vaulted or server fallback)
            raw_clips, model_used = call_gemini_generate_content(user_prompt, api_key=active_gemini_key)

            # Validate, filter, snap, clamp, and deduplicate
            validated_clips = validate_and_filter_candidates(
                raw_candidates=raw_clips,
                segments=segments,
                total_duration=duration,
                max_clips=MAX_HIGHLIGHTS_PER_CHUNK
            )

            publish_mode = db.get_setting("publish_mode", DEFAULT_PUBLISH_MODE)

            # Save clip definitions atomically and transition to CLIPS_DEFINED
            saved_ids = db.save_clip_definitions(
                chunk_id=chunk_id,
                session_id=session_id,
                video_id=video_id,
                clip_defs=validated_clips,
                publish_mode=publish_mode,
                user_id=user_id
            )

            # Reset error fields on success and transition to CLIPS_DEFINED
            db.update_youtube_video(
                video_id=video_id,
                status="CLIPS_DEFINED",
                ai_attempt_count=0,
                next_ai_attempt_at="",
                last_ai_error=""
            )

            logger.info(
                "Pillar 3 Highlight SUCCESS for %s -> CLIPS_DEFINED (Model: %s, Raw: %d, Accepted: %d, ClipIDs: %s)",
                video_id, model_used, len(raw_clips), len(saved_ids), saved_ids
            )

            return {
                "video_id": video_id,
                "status": "CLIPS_DEFINED",
                "clips_count": len(saved_ids),
                "clip_ids": saved_ids,
                "model_used": model_used
            }

        except Exception as e:
            err_msg = str(e)
            logger.error("Gemini highlight extraction failed for %s (attempt %d/%d): %s",
                         video_id, attempt_count, self.max_attempts, err_msg)

            # Determine whether error is permanent or retryable
            current_gemini_key = active_gemini_key
            is_permanent = (
                not current_gemini_key
                or "API_KEY" in err_msg.upper()
                or "AUTHENTICATION FAILED" in err_msg.upper()
                or attempt_count >= self.max_attempts
            )

            if is_permanent:
                logger.critical("Permanent failure for %s in Pillar 3: %s", video_id, err_msg)
                db.update_youtube_video(
                    video_id=video_id,
                    status="FAILED",
                    ai_attempt_count=attempt_count,
                    last_ai_error=err_msg
                )
            else:
                backoff_sec = self.base_backoff_seconds * (2 ** (attempt_count - 1))
                next_dt = datetime.now(timezone.utc) + timedelta(seconds=backoff_sec)
                logger.warning("Scheduling retry for %s in %ds (at %s)",
                               video_id, backoff_sec, next_dt.strftime("%Y-%m-%d %H:%M:%S"))
                db.update_youtube_video(
                    video_id=video_id,
                    status="TRANSCRIBED",  # reset to TRANSCRIBED for future cycle
                    ai_attempt_count=attempt_count,
                    next_ai_attempt_at=next_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    last_ai_error=err_msg
                )

            raise
