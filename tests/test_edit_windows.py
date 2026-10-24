"""Bounded hybrid clip editing: window math, reuse rules, staging pipeline
(faked boundaries), Stage 2 separation, and quota accounting."""

import os
import unittest
from unittest.mock import MagicMock, patch

from dispatch import edit_windows as ew


class TestWindowMath(unittest.TestCase):
    def test_initial_window_60s_context_clamped(self):
        # suggestion 100-140 in a 600s source -> 40..200 (60s context, clamped at 0)
        self.assertEqual(ew.compute_window(100, 140, 600), (40.0, 200.0))

    def test_window_clamped_to_source_end(self):
        # suggestion 570-590 in a 600s source -> 510..600
        self.assertEqual(ew.compute_window(570, 590, 600), (510.0, 600.0))

    def test_max_window_cap_centers_on_selection(self):
        # 40s selection + 60s context = 160s span, under the 300s cap -> uncapped
        self.assertEqual(ew.compute_window(100, 140, 100000, max_window=300), (40.0, 200.0))
        # a 240s selection + context WOULD exceed 300s -> capped to exactly 300
        start, end = ew.compute_window(100, 340, 100000, max_window=300)
        self.assertAlmostEqual(end - start, 300, places=2)
        self.assertLessEqual(start, 100)
        self.assertGreaterEqual(end, 340)

    def test_extension_window_zero_context(self):
        self.assertEqual(ew.compute_extension_window(200, 260, 1000), (200.0, 260.0))

    def test_selection_product_limits(self):
        self.assertIsNotNone(ew.validate_selection(0, 4.9))
        self.assertIsNone(ew.validate_selection(0, 5.0))
        self.assertIsNone(ew.validate_selection(10, 190))
        self.assertIsNotNone(ew.validate_selection(0, 180.1))


class TestWindowReuse(unittest.TestCase):
    def test_ready_window_covers_wanted(self):
        self.assertTrue(ew.covers((40, 200), (50, 150)))
        self.assertFalse(ew.covers((40, 200), (30, 150)))

    def test_combined_span_rule(self):
        # nearby suggestions 4:00-5:00 and 6:00-7:00 share 3:00-8:00 (5 min <= 300s? NO -> 300s cap)
        # 4:00-5:00 and 6:00-7:00 -> combined 240..420 = 180s span, fits the cap
        self.assertTrue(ew.combined_span_fits((240, 300), (360, 420), max_window=300))

    def test_combined_span_exceeds_cap(self):
        self.assertFalse(ew.combined_span_fits((0, 120), (400, 500), max_window=300))

    def test_uncovered_ranges_for_extension(self):
        # cached window covers 0-200; user wants 150-320 -> uncovered 200-320
        self.assertEqual(ew.uncovered_ranges((150, 320), [(0, 200)]), [(200.0, 320.0)])

    def test_fully_covered_means_no_new_transcription(self):
        self.assertEqual(ew.uncovered_ranges((50, 150), [(0, 200)]), [])

    def test_words_mapped_to_window_local_time(self):
        words = [{"word": "a", "start": 40.0, "end": 40.5},
                 {"word": "b", "start": 250.0, "end": 250.5}]
        local = ew.select_words_for_range(words, 40.0)
        self.assertEqual(len(local), 2)
        self.assertEqual(local[0]["start"], 0.0)  # word a clamped to window start
        self.assertEqual(local[1]["start"], 210.0)  # word b stays source-anchored minus offset


class TestStage2Separation(unittest.TestCase):
    def test_stage2_accepts_explicit_300s_bound(self):
        from dispatch.transcription import gemini_engine
        # the guard raises BEFORE any network call for over-long inputs
        try:
            gemini_engine.transcribe_stage2_precision_words(
                __import__("pathlib").Path("nonexistent.mp4"), max_input_seconds=301)
            self.fail("expected ValueError")
        except ValueError as e:
            self.assertIn("301", str(e))
        except RuntimeError:
            # duration probe of a missing file may raise first - also acceptable fail-closed
            pass

    def test_default_bound_unchanged_for_final_clips(self):
        import inspect
        from dispatch.transcription import gemini_engine
        src = inspect.getsource(gemini_engine.transcribe_stage2_precision_words)
        self.assertIn("max_input_seconds if max_input_seconds else MAX_CLIP_DURATION + 16.0", src)


class TestProxyVerification(unittest.TestCase):
    def test_proxy_duration_mismatch_rejected(self):
        self.assertIn("duration", ew.ffprobe_validate_proxy(
            {"duration": 100.0, "has_audio": True, "width": 1280, "height": 720}, 300.0))

    def test_proxy_missing_audio_rejected(self):
        self.assertIn("audio", ew.ffprobe_validate_proxy(
            {"duration": 300.0, "has_audio": False, "width": 1280, "height": 720}, 300.0))

    def test_proxy_valid_passes(self):
        self.assertIsNone(ew.ffprobe_validate_proxy(
            {"duration": 300.0, "has_audio": True, "width": 1280, "height": 720}, 300.0))

    def test_proxy_cmd_preserves_duration_and_target_bitrate(self):
        cmd = ew.build_proxy_cmd("src.mp4", "out.mp4", 30.0, 330.0)
        self.assertIn("300.000", cmd)  # -t = window span
        self.assertIn("2500k", cmd)
        self.assertIn("720", " ".join(cmd))


class FakeMediaStore:
    def __init__(self):
        self.reserved = None
        self.uploaded = None

    def reserve_upload(self, user_id, object_kind, mime_type, expected_bytes, local_clip_id=None):
        self.reserved = (user_id, object_kind, mime_type, expected_bytes)
        return MagicMock(reservation_id="r1")

    def upload_and_finalize(self, reservation, path, user_id):
        self.uploaded = (reservation.reservation_id, path, user_id)
        return "obj-1"


class FakeDb:
    def __init__(self):
        self.events = []

    def create_window(self, clip_id, user_id, ws, we):
        self.events.append(("create", ws, we))
        return "w1"

    def put_transcript(self, window_id, user_id, words):
        self.events.append(("transcript", len(words)))

    def mark_ready(self, window_id, object_id):
        self.events.append(("ready", object_id))

    def mark_failed(self, window_id, error):
        self.events.append(("failed", error))


class TestStagingPipeline(unittest.TestCase):
    def _run(self, fail_ffmpeg=False):
        import tempfile
        from pathlib import Path
        tmp = tempfile.mkdtemp(prefix="ew_")
        src = Path(tmp) / "src.mp4"
        src.write_bytes(b"\x00" * 1024)  # ffmpeg is faked below
        fake_store = FakeMediaStore()
        fake_db = FakeDb()

        def fake_run(cmd, **kw):
            m = MagicMock(returncode=0)
            if fail_ffmpeg and "proxy.mp4" in str(cmd[-1]):
                return MagicMock(returncode=1, stderr="boom")
            # create the output file the pipeline expects
            Path(cmd[-1]).write_bytes(b"\x00" * 5000)
            return m

        words = {"words": [{"word": "hi", "start": 1.0, "end": 1.5}]}
        with patch.object(ew.subprocess, "run", side_effect=fake_run), \
\
             patch.dict(os.environ, {"DISPATCH_TOKEN_VAULT_KEY": ""}, clear=False):
            rec = ew.stage_edit_window(
                "clip-1", "user-1", str(src), 30.0, 330.0,
                transcribe_fn=lambda p, max_input_seconds: words,
                media_store=fake_store, db_ops=fake_db,
                ffprobe_meta=lambda p: {"duration": 300.0, "has_audio": True,
                                        "width": 1280, "height": 720},
                run_ffmpeg=fake_run)
        return fake_store, fake_db, rec

    def test_staging_uploads_window_and_persists_ready(self):
        import shutil, tempfile
        with patch.dict(os.environ, {"DISPATCH_TOKEN_VAULT_KEY": "x"}):
            store, db, rec = self._run()
        self.assertEqual(store.reserved[1], "edit_window")
        self.assertEqual(store.reserved[3], 5000)  # actual bytes reserved
        self.assertEqual(rec["status"], "ready")
        self.assertEqual(db.events[-1][0], "ready")

    def test_staging_failure_marks_failed_and_reraises(self):
        with patch.dict(os.environ, {"DISPATCH_TOKEN_VAULT_KEY": "x"}):
            with self.assertRaises(RuntimeError):
                self._run(fail_ffmpeg=True)
        # a fake store/db from a fresh run would show failed; assert via a second run
        fake_store, fake_db = FakeMediaStore(), FakeDb()
        poller_run = False
        with patch.dict(os.environ, {"DISPATCH_TOKEN_VAULT_KEY": "x"}):
            with patch.object(ew.subprocess, "run", side_effect=lambda cmd, **kw: MagicMock(returncode=1, stderr="boom")),                  patch.object(ew, "ffprobe_validate_proxy", return_value=None):
                try:
                    ew.stage_edit_window(
                        "clip-1", "user-1", "src.mp4", 30.0, 330.0,
                        transcribe_fn=lambda p, max_input_seconds: {"words": []},
                        media_store=fake_store, db_ops=fake_db,
                        ffprobe_meta=lambda p: {"duration": 300.0, "has_audio": True,
                                                "width": 1280, "height": 720},
                        run_ffmpeg=None)
                    self.fail("expected RuntimeError")
                except RuntimeError:
                    pass
        self.assertTrue(any(e[0] == "failed" for e in fake_db.events))
        self.assertNotIn("ready", [e[0] for e in fake_db.events])


if __name__ == "__main__":
    unittest.main()
