"""Wave 0 Safety Regression Tests (§2 & §3).

Covers (with all external network/FFmpeg calls mocked):
1. HighlightWorker:
   - <720s, ==720s, and >720s highlight extraction (negative_context=editorial_context fix)
   - Partial render failure & exception resetting youtube_inbox.status out of RENDERING
   - Background & per-clip updated_at heartbeats during long runs
2. Single Authoritative Daily Quota Reservation:
   - HighlightWorker + gemini_engine deduct exactly 1 slot on primary success (no double-deduct)
   - Primary fail -> fallback ok refunds slot & burns 0 transcribe quota
   - Primary fail -> fallback fail refunds exactly once (no double-refund)
   - Single-refund max and no refund after consumed
   - 429 quota error queueing & retry behavior
   - Concurrent quota reservation with zero overspend & request_id idempotency
3. Safe Per-Job Workspace, Recovery & 72h Retention:
   - Per-job workspace isolation under PROCESSING_DIR/jobs/<job_id>/
   - Non-boot recovery preserves active leased jobs, fresh heartbeats, and active renderer .ass files
   - Boot-time recovery cleans orphaned legacy root temp files while preserving .mp4 sources
   - Idempotent recovery cleanup & safe logging
   - Retention Tier 2 requires real terminal 'published' (NEVER 'approved')
   - Retention Tier 3 72h FIFO never evicts active/pending/retrying/rendering/outbox items
   - Retention matches youtube_inbox clips by both session_id and chunk_id
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

from dispatch import db
from dispatch.ai_clips.worker import HighlightWorker
from dispatch.orchestrator.job_queue import claim_job, enqueue_job, init_job_queue_schema
from dispatch.orchestrator.recovery import (
    get_job_workspace_dir,
    recover_laptop_orchestrator,
    sanitize_job_workspace_id,
)
from dispatch.storage.retention import enforce_retention_policy, get_source_videos_status
from dispatch.transcription import gemini_engine


def _fake_genai_module() -> ModuleType:
    module = ModuleType("google.genai")
    module.types = SimpleNamespace(
        GenerateContentConfig=lambda **kwargs: kwargs,
        AudioTranscriptionConfig=lambda **kwargs: kwargs,
    )
    return module


def _make_primary_response(words_list):
    word_objs = [
        SimpleNamespace(word=w, start_offset=f"{st:.3f}s", end_offset=f"{en:.3f}s")
        for w, st, en in words_list
    ]
    trans = SimpleNamespace(speaker_label="spk:0", words=word_objs)
    part = SimpleNamespace(audio_transcription=trans)
    cand = SimpleNamespace(content=SimpleNamespace(parts=[part]))
    return SimpleNamespace(
        text=" ".join(w for w, _, _ in words_list),
        candidates=[cand],
    )


class TestWave0HighlightWorkerSafety(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="w0_hw_")
        self.tmp_path = Path(self.temp_dir.name)
        self.proc_dir = self.tmp_path / "processing"
        self.proc_dir.mkdir(parents=True, exist_ok=True)
        self.clips_dir = self.tmp_path / "clips"
        self.clips_dir.mkdir(parents=True, exist_ok=True)

        self.old_db_path = db.DB_PATH
        db.DB_PATH = self.tmp_path / "w0_test.db"
        db.init_db()
        os.environ["GEMINI_API_KEY"] = "test_dummy_gemini_key"

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        self.temp_dir.cleanup()

    def _seed_video(self, video_id: str, duration: float, status: str = "TRANSCRIBED", user_id: str | None = None):
        src_file = self.tmp_path / f"{video_id}.mp4"
        src_file.write_bytes(b"fake-video-bytes")
        segments = [
            {
                "id": 0,
                "start": 10.0,
                "end": 45.0,
                "text": "First highlight segment about reliable systems.",
                "words": [
                    {"word": "First", "start": 10.0, "end": 10.5},
                    {"word": "systems.", "start": 44.2, "end": 45.0},
                ],
            },
            {
                "id": 1,
                "start": 50.0,
                "end": 90.0,
                "text": "Second highlight segment with clear payoff.",
                "words": [
                    {"word": "Second", "start": 50.0, "end": 50.5},
                    {"word": "payoff.", "start": 89.2, "end": 90.0},
                ],
            },
        ]
        chunk_id = db.register_chunk(
            session_id=None,
            filename=src_file.name,
            filepath=str(src_file),
            file_hash=f"yt_{video_id}",
            user_id=user_id,
        )
        db.update_chunk_metadata(chunk_id, duration=duration, width=1920, height=1080, aspect_ratio="16:9", status="transcribed")
        db.save_transcript(chunk_id=chunk_id, session_id=None, full_text="First systems. Second payoff.", segments=segments)
        db.register_youtube_video(video_id=video_id, title=f"Vid {video_id}", duration=duration, user_id=user_id)
        db.update_youtube_video(
            video_id=video_id,
            status=status,
            local_video_path=str(src_file),
            transcript_source="gemini_transcribe",
            segments_json=json.dumps(segments),
            duration=duration,
            user_id=user_id,
        )
        return chunk_id, src_file, segments

    def test_highlight_extraction_under_720s_uses_single_call(self):
        video_id = "w0_under_720"
        self._seed_video(video_id, duration=300.0)
        raw = [{"start_time": 10.0, "end_time": 45.0, "title": "Short Vid Clip", "virality_score": 85}]
        with patch("dispatch.ai_clips.worker.call_gemini_generate_content", return_value=(raw, "gemini-flash-latest")) as mock_single, \
             patch("dispatch.ai_clips.worker.extract_highlights_windowed") as mock_win:
            res = HighlightWorker().process_video_highlights(video_id)
        self.assertEqual(res["status"], "CLIPS_DEFINED")
        self.assertEqual(res["clips_count"], 1)
        mock_single.assert_called_once()
        mock_win.assert_not_called()

    def test_highlight_extraction_exactly_720s_uses_single_call(self):
        video_id = "w0_eq_720"
        self._seed_video(video_id, duration=720.0)
        raw = [{"start_time": 10.0, "end_time": 45.0, "title": "Exact 720s Clip", "virality_score": 88}]
        with patch("dispatch.ai_clips.worker.call_gemini_generate_content", return_value=(raw, "gemini-flash-latest")) as mock_single, \
             patch("dispatch.ai_clips.worker.extract_highlights_windowed") as mock_win:
            res = HighlightWorker().process_video_highlights(video_id)
        self.assertEqual(res["status"], "CLIPS_DEFINED")
        self.assertEqual(res["clips_count"], 1)
        mock_single.assert_called_once()
        mock_win.assert_not_called()

    def test_highlight_extraction_over_720s_passes_editorial_context_without_nameerror(self):
        video_id = "w0_over_720"
        self._seed_video(video_id, duration=950.0)
        raw = [{"start_time": 10.0, "end_time": 45.0, "title": "Long Recording Clip", "virality_score": 91}]
        with patch("dispatch.ai_clips.preference_learner.get_editorial_guidance_prompt", return_value="PREFER_TIGHT_HOOKS"), \
             patch("dispatch.ai_clips.worker.extract_highlights_windowed", return_value=(raw, "gemini-flash-latest")) as mock_win, \
             patch("dispatch.ai_clips.worker.call_gemini_generate_content") as mock_single:
            res = HighlightWorker().process_video_highlights(video_id)
        self.assertEqual(res["status"], "CLIPS_DEFINED")
        self.assertEqual(res["clips_count"], 1)
        mock_single.assert_not_called()
        mock_win.assert_called_once()
        self.assertEqual(mock_win.call_args.kwargs["negative_context"], "PREFER_TIGHT_HOOKS")

    def test_partial_render_failure_resets_youtube_inbox_out_of_rendering(self):
        video_id = "w0_partial_render"
        chunk_id, src_file, _ = self._seed_video(video_id, duration=300.0, status="ANALYZING")
        clip_ids = db.save_clip_definitions(
            chunk_id=chunk_id,
            session_id=None,
            video_id=video_id,
            clip_defs=[
                {"start_time": 10.0, "end_time": 45.0, "title": "Clip 1", "virality_score": 90},
                {"start_time": 50.0, "end_time": 90.0, "title": "Clip 2", "virality_score": 85},
            ],
        )
        self.assertEqual(len(clip_ids), 2)

        def fake_extract(source_path, start_seconds, end_seconds, output_path, attempts=2):
            output_path.write_bytes(b"mp3-bytes")
            return output_path

        def fake_slice(source_video, clip_id, start_time, end_time, expected_range_revision=None):
            if clip_id == clip_ids[0]:
                out_mp4 = self.clips_dir / f"{clip_id}.mp4"
                out_mp4.write_bytes(b"rendered-mp4")
                db.update_clip_media(clip_id, str(out_mp4), None, expected_range_revision=expected_range_revision)
                return {"clip_id": clip_id, "video_path": out_mp4}
            raise RuntimeError("synthetic ffmpeg failure on second clip")

        with patch("dispatch.ai_clips.worker.PROCESSING_DIR", self.proc_dir), \
             patch("dispatch.transcription.gemini_engine.extract_stage2_clip_audio", side_effect=fake_extract), \
             patch("dispatch.transcription.gemini_engine.transcribe_stage2_precision_words", return_value={"words": []}), \
             patch("dispatch.video_engine.renderer.slice_clean_clip", side_effect=fake_slice):
            HighlightWorker().process_pending()

        row = db.get_youtube_video(video_id)
        self.assertEqual(row["status"], "CLIPS_DEFINED", "Partial render failure must reset RENDERING back to CLIPS_DEFINED")
        c0 = db.get_clip_by_id(clip_ids[0])
        c1 = db.get_clip_by_id(clip_ids[1])
        self.assertTrue(c0["video_path"])
        self.assertIsNone(c1["video_path"])
        self.assertEqual(c1["render_attempt_count"], 1)

    def test_unhandled_render_exception_resets_youtube_inbox_out_of_rendering(self):
        video_id = "w0_render_exc"
        chunk_id, _, _ = self._seed_video(video_id, duration=200.0, status="ANALYZING")
        db.save_clip_definitions(
            chunk_id=chunk_id,
            session_id=None,
            video_id=video_id,
            clip_defs=[{"start_time": 10.0, "end_time": 45.0, "title": "Clip 1", "virality_score": 90}],
        )
        worker = HighlightWorker()
        with patch.object(worker, "_render_clips_for_video", side_effect=RuntimeError("unexpected crash")):
            worker.process_pending()

        row = db.get_youtube_video(video_id)
        self.assertEqual(row["status"], "CLIPS_DEFINED")
        self.assertIn("Render interrupted", row["last_error"])

    def test_long_run_heartbeat_refreshes_updated_at(self):
        video_id = "w0_heartbeat_long"
        self._seed_video(video_id, duration=900.0)
        stale_ts = (datetime.now(timezone.utc) - timedelta(minutes=14)).strftime("%Y-%m-%d %H:%M:%S")
        with db.get_db_connection() as conn:
            conn.execute("UPDATE youtube_inbox SET updated_at = ? WHERE video_id = ?", (stale_ts, video_id))
            conn.commit()

        observed_timestamps = []

        def slow_windowed(**kwargs):
            # Backdate updated_at right after transition to ANALYZING, then wait for heartbeat tick
            with db.get_db_connection() as conn:
                conn.execute("UPDATE youtube_inbox SET updated_at = ? WHERE video_id = ?", (stale_ts, video_id))
                conn.commit()
            time.sleep(0.16)
            row_mid = db.get_youtube_video(video_id)
            observed_timestamps.append(row_mid["updated_at"])
            return [{"start_time": 10.0, "end_time": 45.0, "title": "HB Clip", "virality_score": 85}], "gemini-flash-latest"

        worker = HighlightWorker(heartbeat_interval_seconds=0.04)
        with patch("dispatch.ai_clips.worker.extract_highlights_windowed", side_effect=slow_windowed):
            res = worker.process_video_highlights(video_id)

        self.assertEqual(res["status"], "CLIPS_DEFINED")
        self.assertTrue(observed_timestamps)
        self.assertNotEqual(observed_timestamps[0], stale_ts, "Heartbeat thread must refresh updated_at during long extraction")


class TestWave0DailyQuotaSafety(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="w0_quota_")
        self.tmp_path = Path(self.temp_dir.name)
        self.proc_dir = self.tmp_path / "processing"
        self.proc_dir.mkdir(parents=True, exist_ok=True)
        self.clips_dir = self.tmp_path / "clips"
        self.clips_dir.mkdir(parents=True, exist_ok=True)

        self.old_db_path = db.DB_PATH
        db.DB_PATH = self.tmp_path / "w0_quota.db"
        db.init_db()
        os.environ["GEMINI_API_KEY"] = "test_dummy_gemini_key"

        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        db.set_setting("transcribe_quota_date", today_utc)
        db.set_setting("transcribe_quota_count", "0")

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        self.temp_dir.cleanup()

    def _seed_renderable_clip(self, clip_suffix: str):
        vid = f"vid_{clip_suffix}"
        src = self.tmp_path / f"{vid}.mp4"
        src.write_bytes(b"video-bytes")
        segs = [{"id": 0, "start": 10.0, "end": 40.0, "text": "Hello world clip test"}]
        chunk_id = db.register_chunk(session_id=None, filename=src.name, filepath=str(src), file_hash=f"yt_{vid}")
        db.register_youtube_video(video_id=vid, title=vid, duration=120.0)
        db.update_youtube_video(video_id=vid, status="ANALYZING", local_video_path=str(src), segments_json=json.dumps(segs))
        clip_ids = db.save_clip_definitions(
            chunk_id=chunk_id,
            session_id=None,
            video_id=vid,
            clip_defs=[{"start_time": 10.0, "end_time": 40.0, "title": "Quota Clip", "virality_score": 85}],
        )
        return vid, clip_ids[0], src, segs

    def _run_render_with_mock_client(self, mock_client, clip_suffix: str, extract_fails: bool = False):
        vid, clip_id, src, segs = self._seed_renderable_clip(clip_suffix)

        def fake_extract(source_path, start_seconds, end_seconds, output_path, attempts=2):
            if extract_fails:
                raise RuntimeError("synthetic audio extraction failure")
            output_path.write_bytes(b"mp3-bytes")
            return output_path

        def fake_slice(source_video, clip_id, start_time, end_time, expected_range_revision=None):
            out_mp4 = self.clips_dir / f"{clip_id}.mp4"
            out_mp4.write_bytes(b"rendered-mp4")
            db.update_clip_media(clip_id, str(out_mp4), None, expected_range_revision=expected_range_revision)
            return {"clip_id": clip_id, "video_path": out_mp4}

        with patch.dict(sys.modules, {"google.genai": _fake_genai_module()}), \
             patch("dispatch.ai_clips.worker.PROCESSING_DIR", self.proc_dir), \
             patch("dispatch.transcription.gemini_engine.PROCESSING_DIR", self.proc_dir), \
             patch("dispatch.transcription.gemini_engine.get_gemini_client", return_value=mock_client), \
             patch("dispatch.transcription.gemini_engine._probe_audio_duration", return_value=30.0), \
             patch("dispatch.transcription.gemini_engine.extract_stage2_clip_audio", side_effect=fake_extract), \
             patch("dispatch.video_engine.renderer.slice_clean_clip", side_effect=fake_slice):
            HighlightWorker()._render_clips_for_video(vid, [clip_id], segs, src)
        return clip_id

    def test_single_quota_deduction_when_primary_succeeds(self):
        mock_client = MagicMock()
        mock_client.files.upload.return_value = SimpleNamespace(name="files/p1")
        mock_client.models.generate_content.return_value = _make_primary_response(
            [("Hello", 4.1, 4.5), ("world", 4.6, 5.2), ("today", 32.0, 33.5)]
        )
        self._run_render_with_mock_client(mock_client, "primary_ok")
        self.assertEqual(db.get_setting("transcribe_quota_count"), "1", "Must deduct exactly 1 quota slot, never 2")

    def test_primary_fail_fallback_ok_refunds_slot_and_burns_zero_transcribe_quota(self):
        mock_client = MagicMock()
        mock_client.files.upload.return_value = SimpleNamespace(name="files/fb1")
        mock_client.models.generate_content.side_effect = [
            RuntimeError("HTTP 503: primary overloaded"),
            SimpleNamespace(text='[{"start":"00:04.00","end":"00:08.00","text":"Fallback words ok","speaker":"spk:0"}]'),
        ]
        clip_id = self._run_render_with_mock_client(mock_client, "fallback_ok")
        self.assertEqual(db.get_setting("transcribe_quota_count"), "0", "Failed primary + flash-lite fallback must not burn transcribe quota")
        clip_row = db.get_clip_by_id(clip_id)
        self.assertEqual(clip_row["status"], "ready_review")
        self.assertTrue(clip_row["video_path"])

    def test_total_failure_refunds_exactly_once_preserving_existing_count(self):
        db.set_setting("transcribe_quota_count", "1")
        mock_client = MagicMock()
        mock_client.files.upload.return_value = SimpleNamespace(name="files/fail_both")
        mock_client.models.generate_content.side_effect = [
            RuntimeError("HTTP 503: primary failed"),
            RuntimeError("HTTP 500: fallback failed"),
        ]
        self._run_render_with_mock_client(mock_client, "total_fail")
        self.assertEqual(db.get_setting("transcribe_quota_count"), "1", "Total failure must refund exactly once, never double-refund")

    def test_429_quota_error_refunds_and_routes_to_backlog_queue(self):
        db.set_setting("transcribe_quota_count", "2")
        mock_client = MagicMock()
        mock_client.files.upload.return_value = SimpleNamespace(name="files/rate429")
        mock_client.models.generate_content.side_effect = [
            RuntimeError("HTTP 429 ResourceExhausted"),
            RuntimeError("HTTP 429 ResourceExhausted"),
        ]
        clip_id = self._run_render_with_mock_client(mock_client, "rate_429")
        self.assertEqual(db.get_setting("transcribe_quota_count"), "2")
        self.assertEqual(db.get_clip_by_id(clip_id)["status"], "queued_transcription")

    def test_extract_audio_failure_refunds_slot(self):
        mock_client = MagicMock()
        self._run_render_with_mock_client(mock_client, "extract_fail", extract_fails=True)
        self.assertEqual(db.get_setting("transcribe_quota_count"), "0")

    def test_request_id_idempotency_single_refund_max_and_no_refund_after_consume(self):
        # 1. Same request_id reserved twice -> increments once
        self.assertTrue(gemini_engine.check_and_increment_daily_transcribe_quota(25, request_id="job_alpha"))
        self.assertTrue(gemini_engine.check_and_increment_daily_transcribe_quota(25, request_id="job_alpha"))
        self.assertEqual(db.get_setting("transcribe_quota_count"), "1")

        # 2. Multiple refunds for same request_id -> single refund max
        self.assertTrue(gemini_engine.refund_daily_transcribe_quota(request_id="job_alpha"))
        self.assertFalse(gemini_engine.refund_daily_transcribe_quota(request_id="job_alpha"))
        self.assertEqual(db.get_setting("transcribe_quota_count"), "0")

        # 3. Retry of previously refunded request_id re-reserves 1 slot, and once consumed cannot be refunded
        self.assertTrue(gemini_engine.check_and_increment_daily_transcribe_quota(25, request_id="job_alpha"))
        self.assertEqual(db.get_setting("transcribe_quota_count"), "1")
        gemini_engine._consume_daily_transcribe_quota("job_alpha")
        self.assertFalse(gemini_engine.refund_daily_transcribe_quota(request_id="job_alpha"))
        self.assertEqual(db.get_setting("transcribe_quota_count"), "1")

    def test_concurrent_quota_reservations_no_overspend_and_idempotent_under_contention(self):
        barrier = threading.Barrier(12)
        results = []
        lock = threading.Lock()

        def worker_distinct(idx: int):
            barrier.wait()
            ok = gemini_engine.check_and_increment_daily_transcribe_quota(
                max_daily_requests=5, request_id=f"conc_distinct_{idx}"
            )
            with lock:
                results.append(ok)

        threads = [threading.Thread(target=worker_distinct, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sum(1 for r in results if r), 5, "Concurrency must never overspend cap")
        self.assertEqual(db.get_setting("transcribe_quota_count"), "5")

        # Reset and verify concurrent duplicate calls with the same request_id charge only once
        db.set_setting("transcribe_quota_count", "0")
        dup_barrier = threading.Barrier(10)
        dup_results = []

        def worker_dup():
            dup_barrier.wait()
            ok = gemini_engine.check_and_increment_daily_transcribe_quota(
                max_daily_requests=5, request_id="conc_shared_req"
            )
            with lock:
                dup_results.append(ok)

        dup_threads = [threading.Thread(target=worker_dup) for _ in range(10)]
        for t in dup_threads:
            t.start()
        for t in dup_threads:
            t.join()

        self.assertTrue(all(dup_results))
        self.assertEqual(db.get_setting("transcribe_quota_count"), "1")


class TestWave0WorkspaceRecoveryAndRetention(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="w0_rec_ret_")
        self.tmp_path = Path(self.temp_dir.name)
        self.storage_dir = self.tmp_path / "storage"
        self.proc_dir = self.storage_dir / "processing"
        self.inbox_dir = self.storage_dir / "youtube_inbox"
        self.proc_dir.mkdir(parents=True, exist_ok=True)
        self.inbox_dir.mkdir(parents=True, exist_ok=True)

        self.old_db_path = db.DB_PATH
        db.DB_PATH = self.tmp_path / "w0_rec_ret.db"
        db.init_db()
        init_job_queue_schema()

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        self.temp_dir.cleanup()

    def test_per_job_workspace_isolation_and_traversal_sanitization(self):
        safe_id = sanitize_job_workspace_id("../evil/job:1")
        self.assertNotIn("/", safe_id)
        self.assertNotIn("\\", safe_id)
        self.assertNotIn("..", safe_id)

        ws = get_job_workspace_dir("../evil/job:1", base_dir=self.proc_dir)
        self.assertEqual(ws.parent, self.proc_dir / "jobs")
        self.assertTrue(ws.exists())
        shutil.rmtree(ws)

        # Verify transcribe_media isolates temp files under PROCESSING_DIR/jobs/ and cleans up
        audio = self.tmp_path / "sample.mp3"
        audio.write_bytes(b"mp3")
        seen_part_dirs = []
        mock_client = MagicMock()
        mock_client.files.upload.return_value = SimpleNamespace(name="files/t1")
        mock_client.models.generate_content.return_value = _make_primary_response([("Ok", 0.1, 0.4)])

        def fake_split(audio_path, parts_dir, chunk_seconds=600, overlap_seconds=5.0):
            seen_part_dirs.append(Path(parts_dir))
            Path(parts_dir).mkdir(parents=True, exist_ok=True)
            p0 = Path(parts_dir) / "part0.mp3"
            p0.write_bytes(b"p0")
            return [(p0, 0.0)]

        with patch.dict(sys.modules, {"google.genai": _fake_genai_module()}), \
             patch("dispatch.transcription.gemini_engine.PROCESSING_DIR", self.proc_dir), \
             patch("dispatch.transcription.gemini_engine.get_gemini_client", return_value=mock_client), \
             patch("dispatch.transcription.gemini_engine.split_audio_for_transcription", side_effect=fake_split):
            res = gemini_engine.transcribe_media(audio, api_key="k")

        self.assertEqual(res["full_text"], "Ok")
        self.assertTrue(seen_part_dirs)
        self.assertTrue(seen_part_dirs[0].is_relative_to(self.proc_dir / "jobs"))
        self.assertEqual(list((self.proc_dir / "jobs").iterdir()), [], "Completed job workspace must clean up")

    def test_non_boot_recovery_protects_active_leases_heartbeats_and_renderer_files(self):
        # 1. Active leased pipeline job
        c_active = db.register_chunk(None, "active.mp4", str(self.proc_dir / "active.mp4"), "h_active")
        j_active = enqueue_job(c_active)
        claim_job("worker_live", lease_duration_seconds=600)
        active_ws = get_job_workspace_dir(j_active, base_dir=self.proc_dir)
        active_tmp = active_ws / "work.tmp"
        active_tmp.write_bytes(b"live-work")

        # 2. Expired pipeline job
        c_exp = db.register_chunk(None, "exp.mp4", str(self.proc_dir / "exp.mp4"), "h_exp")
        j_exp = enqueue_job(c_exp)
        claim_job("worker_dead", lease_duration_seconds=30)
        past = (datetime.now() - timedelta(minutes=30)).isoformat()
        with db.get_db_connection() as conn:
            conn.execute("UPDATE pipeline_jobs SET lease_expires_at = ?, heartbeat_at = ? WHERE job_id = ?", (past, past, j_exp))
            conn.commit()
        exp_ws = get_job_workspace_dir(j_exp, base_dir=self.proc_dir)
        exp_tmp = exp_ws / "orphan.tmp"
        exp_tmp.write_bytes(b"stale-work")

        # 3. Active youtube_inbox rendering video with fresh updated_at
        db.register_youtube_video("vid_live_render", "Live Render", duration=60.0)
        db.update_youtube_video("vid_live_render", status="RENDERING")
        yt_live_ws = get_job_workspace_dir("vid_live_render", base_dir=self.proc_dir)
        yt_live_ass = yt_live_ws / "clip_live.ass"
        yt_live_ass.write_text("[Script Info]", encoding="utf-8")

        # 4. Active renderer .ass file in PROCESSING_DIR root + finalized .mp4 chunk
        renderer_ass = self.proc_dir / "clip_999_deadbeef.ass"
        renderer_ass.write_text("[Script Info]", encoding="utf-8")
        source_mp4 = self.proc_dir / "active.mp4"
        source_mp4.write_bytes(b"mp4-data")

        with patch("dispatch.orchestrator.recovery.PROCESSING_DIR", self.proc_dir):
            stats1 = recover_laptop_orchestrator(is_boot=False, stale_timeout_seconds=900)
            stats2 = recover_laptop_orchestrator(is_boot=False, stale_timeout_seconds=900)

        self.assertIn(j_exp, stats1["reclaimed_jobs"])
        self.assertNotIn(j_active, stats1["reclaimed_jobs"])
        self.assertFalse(exp_ws.exists(), "Expired job workspace must be cleaned")
        self.assertTrue(active_tmp.exists(), "Active leased job workspace must never be deleted")
        self.assertTrue(yt_live_ass.exists(), "Fresh heartbeat RENDERING workspace must never be deleted")
        self.assertTrue(renderer_ass.exists(), "Active renderer .ass file must never be deleted on non-boot sweep")
        self.assertTrue(source_mp4.exists())
        self.assertEqual(stats2["cleaned_temp_files"], 0, "Recovery cleanup must be idempotent")

    def test_boot_recovery_cleans_legacy_root_temp_files_preserving_mp4(self):
        legacy_ass = self.proc_dir / "old_root.ass"
        legacy_tmp = self.proc_dir / "old_root.tmp"
        legacy_part = self.proc_dir / "old_root.part"
        source_mp4 = self.proc_dir / "keep_source.mp4"
        for p in (legacy_ass, legacy_tmp, legacy_part, source_mp4):
            p.write_bytes(b"data")

        with patch("dispatch.orchestrator.recovery.PROCESSING_DIR", self.proc_dir):
            stats = recover_laptop_orchestrator(is_boot=True)

        self.assertFalse(legacy_ass.exists())
        self.assertFalse(legacy_tmp.exists())
        self.assertFalse(legacy_part.exists())
        self.assertTrue(source_mp4.exists())
        self.assertEqual(stats["cleaned_temp_files"], 3)

    def test_retention_tier2_rejects_approved_and_requires_published(self):
        now = datetime.now(timezone.utc)
        five_days_ago = (now - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
        four_days_ago = (now - timedelta(days=4)).strftime("%Y-%m-%d %H:%M:%S")

        vid_file = self.inbox_dir / "approved_only.mp4"
        vid_file.write_bytes(b"a" * 4096)

        with db.get_db_connection() as conn:
            conn.execute("INSERT INTO sessions (id, status) VALUES ('sess_app', 'completed')")
            conn.execute(
                "INSERT INTO chunks (id, session_id, filename, filepath, created_at, status) "
                "VALUES ('chk_app', 'sess_app', 'approved_only.mp4', ?, ?, 'transcribed')",
                (str(vid_file), five_days_ago),
            )
            conn.execute(
                "INSERT INTO clips (id, session_id, chunk_id, title, duration, start_time, end_time, status, created_at, approved_at) "
                "VALUES ('clip_app', 'sess_app', 'chk_app', 'Approved Not Published', 15.0, 0, 15, 'approved', ?, ?)",
                (five_days_ago, four_days_ago),
            )
            conn.commit()

        stats = enforce_retention_policy(
            storage_dir=self.storage_dir,
            storage_ceiling_bytes=100 * 1024 * 1024,
            now=now,
        )
        self.assertEqual(stats["pruned_72_hour_published"], 0)
        self.assertEqual(stats["pruned_7_day"], 0)
        self.assertTrue(vid_file.exists(), "Approved (unpublished) clip source must NEVER be pruned by Tier 2")

    def test_retention_tier3_72h_fifo_protects_active_pending_retrying_rendering_and_outbox(self):
        now = datetime.now(timezone.utc)
        eight_days_ago = (now - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S")
        four_days_ago = (now - timedelta(days=4)).strftime("%Y-%m-%d %H:%M:%S")

        files = {}
        for name in (
            "yt_rendering",
            "yt_clips_defined",
            "job_retrying",
            "clip_queued_tx",
            "clip_approved",
            "outbox_queued",
            "idle_evictable",
        ):
            p = self.inbox_dir / f"{name}.mp4"
            p.write_bytes(b"x" * 4096)
            files[name] = p

        with db.get_db_connection() as conn:
            # 1. youtube_inbox RENDERING (4 days old)
            conn.execute(
                "INSERT INTO youtube_inbox (video_id, title, status, local_video_path, discovered_at) "
                "VALUES ('yt_rend', 'Rendering', 'RENDERING', ?, ?)",
                (str(files["yt_rendering"]), four_days_ago),
            )
            # 2. youtube_inbox CLIPS_DEFINED (4 days old)
            conn.execute(
                "INSERT INTO youtube_inbox (video_id, title, status, local_video_path, discovered_at) "
                "VALUES ('yt_def', 'Defined', 'CLIPS_DEFINED', ?, ?)",
                (str(files["yt_clips_defined"]), four_days_ago),
            )
            # 3. pipeline_jobs RETRY_PENDING (8 days old!)
            conn.execute(
                "INSERT INTO chunks (id, filename, filepath, created_at, status) "
                "VALUES ('chk_retry', 'job_retrying.mp4', ?, ?, 'transcribed')",
                (str(files["job_retrying"]), eight_days_ago),
            )
            conn.execute(
                "INSERT INTO pipeline_jobs (job_id, chunk_id, current_stage, status) "
                "VALUES ('job_ret_1', 'chk_retry', 'ANALYZE', 'RETRY_PENDING')"
            )
            # 4. clip queued_transcription (8 days old!)
            conn.execute(
                "INSERT INTO chunks (id, filename, filepath, created_at, status) "
                "VALUES ('chk_qtx', 'clip_queued_tx.mp4', ?, ?, 'transcribed')",
                (str(files["clip_queued_tx"]), eight_days_ago),
            )
            conn.execute(
                "INSERT INTO clips (id, chunk_id, title, duration, start_time, end_time, status, created_at) "
                "VALUES ('clip_qtx', 'chk_qtx', 'Backlog', 10.0, 0, 10, 'queued_transcription', ?)",
                (eight_days_ago,),
            )
            # 5. clip approved (8 days old!)
            conn.execute(
                "INSERT INTO chunks (id, filename, filepath, created_at, status) "
                "VALUES ('chk_cap', 'clip_approved.mp4', ?, ?, 'transcribed')",
                (str(files["clip_approved"]), eight_days_ago),
            )
            conn.execute(
                "INSERT INTO clips (id, chunk_id, title, duration, start_time, end_time, status, created_at) "
                "VALUES ('clip_cap', 'chk_cap', 'Approved', 10.0, 0, 10, 'approved', ?)",
                (eight_days_ago,),
            )
            # 6. publishing_outbox uploading/queued even if clip row marked published (8 days old!)
            conn.execute(
                "INSERT INTO chunks (id, filename, filepath, created_at, status) "
                "VALUES ('chk_obx', 'outbox_queued.mp4', ?, ?, 'transcribed')",
                (str(files["outbox_queued"]), eight_days_ago),
            )
            conn.execute(
                "INSERT INTO clips (id, chunk_id, title, duration, start_time, end_time, status, created_at, published_at) "
                "VALUES ('clip_obx', 'chk_obx', 'Outbox', 10.0, 0, 10, 'ready_review', ?, ?)",
                (eight_days_ago, four_days_ago),
            )
            conn.execute(
                "INSERT INTO publishing_outbox (id, clip_id, platform, status, idempotency_key) "
                "VALUES ('pub_obx_1', 'clip_obx', 'instagram', 'uploading', 'idem_obx_1')"
            )
            # 7. Unprotected idle source (4 days old -> eligible ONLY for Tier 3 FIFO under ceiling)
            conn.execute(
                "INSERT INTO chunks (id, filename, filepath, created_at, status) "
                "VALUES ('chk_idle', 'idle_evictable.mp4', ?, ?, 'transcribed')",
                (str(files["idle_evictable"]), four_days_ago),
            )
            conn.commit()

        # Tiny ceiling (512 bytes) forces Tier 3 FIFO pressure across all >72h files
        stats = enforce_retention_policy(
            storage_dir=self.storage_dir,
            storage_ceiling_bytes=512,
            now=now,
        )

        self.assertTrue(stats["ceiling_exceeded"])
        self.assertEqual(stats["pruned_ceiling_fifo"], 1)
        self.assertFalse(files["idle_evictable"].exists())
        for protected_name in (
            "yt_rendering",
            "yt_clips_defined",
            "job_retrying",
            "clip_queued_tx",
            "clip_approved",
            "outbox_queued",
        ):
            self.assertTrue(
                files[protected_name].exists(),
                f"Protected source '{protected_name}' was evicted under 72h+ Tier 3 FIFO!",
            )

    def test_retention_matches_youtube_inbox_clips_by_both_session_id_and_chunk_id(self):
        now = datetime.now(timezone.utc)
        four_days_ago = (now - timedelta(days=4)).strftime("%Y-%m-%d %H:%M:%S")

        # Case A: session_id is NULL, chunk row exists with file_hash='yt_<vid>' (empty filepath), clip linked via chunk_id
        vid_a = "yt_match_chk"
        file_a = self.inbox_dir / f"{vid_a}.mp4"
        file_a.write_bytes(b"a" * 2048)

        # Case B: session_id is NULL, no chunks row at all, clip linked via legacy chunk_id='chk_yt_<vid>'
        vid_b = "yt_match_legacy"
        file_b = self.inbox_dir / f"{vid_b}.mp4"
        file_b.write_bytes(b"b" * 2048)

        with db.get_db_connection() as conn:
            conn.execute(
                "INSERT INTO youtube_inbox (video_id, session_id, title, status, local_video_path, discovered_at) "
                "VALUES (?, NULL, 'Match By Chunk', 'COMPLETED', ?, ?)",
                (vid_a, str(file_a), four_days_ago),
            )
            conn.execute(
                "INSERT INTO chunks (id, session_id, filename, filepath, file_hash, created_at, status) "
                "VALUES ('real_chk_a', NULL, 'yt_match_chk.mp4', '', ?, ?, 'verified')",
                (f"yt_{vid_a}", four_days_ago),
            )
            conn.execute(
                "INSERT INTO clips (id, session_id, chunk_id, title, duration, start_time, end_time, status, created_at, published_at) "
                "VALUES ('clip_match_a', NULL, 'real_chk_a', 'Published A', 15.0, 0, 15, 'published', ?, ?)",
                (four_days_ago, four_days_ago),
            )

            conn.execute(
                "INSERT INTO youtube_inbox (video_id, session_id, title, status, local_video_path, discovered_at) "
                "VALUES (?, NULL, 'Match Legacy ChkYt', 'COMPLETED', ?, ?)",
                (vid_b, str(file_b), four_days_ago),
            )
            conn.commit()
            # PRAGMA foreign_keys only takes effect outside an active transaction
            conn.execute("PRAGMA foreign_keys = OFF;")
            conn.execute(
                "INSERT INTO clips (id, session_id, chunk_id, title, duration, start_time, end_time, status, created_at) "
                "VALUES ('clip_match_b', NULL, ?, 'Approved B', 15.0, 0, 15, 'approved', ?)",
                (f"chk_yt_{vid_b}", four_days_ago),
            )
            conn.commit()
            conn.execute("PRAGMA foreign_keys = ON;")

        statuses = {item["id"]: item for item in get_source_videos_status(now=now)}
        self.assertEqual(statuses[vid_a]["total_clips"], 1)
        self.assertTrue(statuses[vid_a]["eligible_72_hour_post_publish"])
        self.assertEqual(statuses[vid_b]["total_clips"], 1)
        self.assertTrue(statuses[vid_b]["is_protected"])
        self.assertFalse(statuses[vid_b]["eligible_72_hour_post_publish"])

        stats = enforce_retention_policy(storage_dir=self.storage_dir, storage_ceiling_bytes=1024, now=now)
        self.assertEqual(stats["pruned_72_hour_published"], 1)
        self.assertFalse(file_a.exists(), "Published youtube_inbox source matched via chunk_id should prune after 72h")
        self.assertTrue(file_b.exists(), "Approved clip matched via chk_yt_<video_id> must block Tier 2 & Tier 3 eviction")


if __name__ == "__main__":
    unittest.main()
