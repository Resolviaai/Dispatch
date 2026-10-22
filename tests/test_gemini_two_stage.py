"""Unit tests for Gemini Two-Stage Transcription Engine."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
from types import ModuleType, SimpleNamespace

from dispatch.transcription.gemini_engine import (
    _parse_offset_to_seconds,
    check_and_increment_daily_transcribe_quota,
    transcribe_stage1_flash_lite,
    transcribe_stage2_precision_words,
    _parse_stage1_time,
    _deduplicate_overlapping_segments,
    drain_queued_transcriptions,
)
from dispatch import db
from dispatch.transcription.audio import _probe_audio_duration, split_audio_for_transcription


def fake_genai_module():
    module = ModuleType("google.genai")
    module.types = SimpleNamespace(
        GenerateContentConfig=lambda **kwargs: kwargs,
        AudioTranscriptionConfig=lambda **kwargs: kwargs,
    )
    return module


class TestGeminiTwoStage(unittest.TestCase):

    def test_parse_offset_to_seconds(self):
        self.assertEqual(_parse_offset_to_seconds("0.100s"), 0.1)
        self.assertEqual(_parse_offset_to_seconds("2.450s"), 2.45)
        self.assertEqual(_parse_offset_to_seconds("15s"), 15.0)
        self.assertEqual(_parse_offset_to_seconds(3.5), 3.5)
        self.assertEqual(_parse_offset_to_seconds(None), 0.0)

    def test_daily_transcribe_quota_tracking(self):
        # Reset test state
        db.set_setting("transcribe_quota_date", "2026-01-01")
        db.set_setting("transcribe_quota_count", "0")

        # First 5 calls pass with limit=5
        for i in range(1, 6):
            self.assertTrue(check_and_increment_daily_transcribe_quota(max_daily_requests=5))

        # 6th call exceeds limit
        self.assertFalse(check_and_increment_daily_transcribe_quota(max_daily_requests=5))

    @patch("dispatch.transcription.gemini_engine.get_gemini_client")
    def test_transcribe_stage1_flash_lite(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_file = MagicMock()
        mock_file.name = "files/test1234"
        mock_client.files.upload.return_value = mock_file

        mock_resp = MagicMock()
        mock_resp.text = '[{"start": "00:01.00", "end": "00:04.50", "text": "Testing hook", "speaker": "Speaker 1"}]'
        mock_client.models.generate_content.return_value = mock_resp

        # Create dummy audio file
        test_file = Path("storage/test_dummy.mp3")
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_bytes(b"dummy")

        with patch.dict(sys.modules, {"google.genai": fake_genai_module()}), \
             patch("dispatch.transcription.gemini_engine.split_audio_for_transcription", return_value=[(test_file, 0.0)]):
            segments = transcribe_stage1_flash_lite(test_file)
        try:
            self.assertEqual(len(segments), 1)
            self.assertEqual(segments[0]["start"], 1.0)
            self.assertEqual(segments[0]["end"], 4.5)
            self.assertEqual(segments[0]["text"], "Testing hook")
        finally:
            if test_file.exists():
                test_file.unlink()

    @patch("dispatch.transcription.gemini_engine.get_gemini_client")
    def test_transcribe_stage2_precision_words(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_file = MagicMock()
        mock_file.name = "files/test5678"
        mock_client.files.upload.return_value = mock_file

        # Mock WordInfo
        w1 = MagicMock()
        w1.word = "Hello"
        w1.start_offset = "0.200s"
        w1.end_offset = "0.500s"

        w2 = MagicMock()
        w2.word = "world"
        w2.start_offset = "0.600s"
        w2.end_offset = "1.100s"

        part = MagicMock()
        trans = MagicMock()
        trans.speaker_label = "spk:0"
        trans.words = [w1, w2]
        part.audio_transcription = trans

        cand = MagicMock()
        cand.content.parts = [part]

        mock_resp = MagicMock()
        mock_resp.text = "Hello world"
        mock_resp.candidates = [cand]
        mock_client.models.generate_content.return_value = mock_resp

        test_file = Path("storage/test_dummy_clip.mp3")
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_bytes(b"dummy")

        with patch.dict(sys.modules, {"google.genai": fake_genai_module()}), \
             patch("dispatch.transcription.gemini_engine._probe_audio_duration", return_value=3.0):
            result = transcribe_stage2_precision_words(test_file)
        try:
            self.assertEqual(result["full_text"], "Hello world")
            self.assertEqual(len(result["words"]), 2)
            self.assertEqual(result["words"][0]["word"], "Hello")
            self.assertEqual(result["words"][0]["start"], 0.2)
            self.assertEqual(result["speech_start"], 0.2)
            self.assertEqual(result["speech_end"], 1.1)
        finally:
            if test_file.exists():
                test_file.unlink()

    def test_stage1_combines_part_transcripts_on_original_timeline(self):
        client = MagicMock()
        client.files.upload.side_effect = [SimpleNamespace(name="part-one"), SimpleNamespace(name="part-two")]
        client.models.generate_content.side_effect = [
            SimpleNamespace(text='[{"start":"00:09.00","end":"00:10.00","text":"first"}]'),
            SimpleNamespace(text='[{"start":"00:01.00","end":"00:02.00","text":"second"}]'),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "long.mp3"
            first = Path(tmp) / "first.mp3"
            second = Path(tmp) / "second.mp3"
            source.write_bytes(b"source")
            first.write_bytes(b"part 1")
            second.write_bytes(b"part 2")
            with patch.dict(sys.modules, {"google.genai": fake_genai_module()}), \
                 patch("dispatch.transcription.gemini_engine.get_gemini_client", return_value=client), \
                 patch("dispatch.transcription.gemini_engine.split_audio_for_transcription",
                       return_value=[(first, 0.0), (second, 595.0)]) as split_audio:
                segments = transcribe_stage1_flash_lite(source)

        split_audio.assert_called_once()
        self.assertEqual(split_audio.call_args.kwargs["overlap_seconds"], 5.0)
        self.assertEqual([item["start"] for item in segments], [9.0, 596.0])
        self.assertEqual([item["text"] for item in segments], ["first", "second"])
        self.assertEqual(client.files.delete.call_count, 2)
        self.assertEqual(_parse_stage1_time("01:02:03.5"), 3723.5)

    def test_stage1_deduplicates_only_adjacent_window_boundary_phrases(self):
        segments = [
            {"start": 597.5, "end": 599.5, "text": "same phrase", "speaker": "one", "_source_part_index": 0},
            {"start": 598.1, "end": 600.2, "text": "Same phrase.", "speaker": "one", "_source_part_index": 1},
            {"start": 601.0, "end": 602.0, "text": "same phrase", "speaker": "one", "_source_part_index": 1},
        ]
        merged = _deduplicate_overlapping_segments(segments, overlap_seconds=5.0)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["start"], 597.5)
        self.assertEqual(merged[0]["end"], 600.2)
        self.assertNotIn("_source_part_index", merged[0])

    def test_stage2_rejects_full_source_audio_before_upload(self):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "multi_hour.mp3"
            source.write_bytes(b"fixture")
            with patch.dict(sys.modules, {"google.genai": fake_genai_module()}), \
                 patch("dispatch.transcription.gemini_engine.get_gemini_client", return_value=client), \
                 patch("dispatch.transcription.gemini_engine._probe_audio_duration", return_value=7200.0):
                with self.assertRaisesRegex(ValueError, "Full-source transcription is disabled"):
                    transcribe_stage2_precision_words(source)
        client.files.upload.assert_not_called()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools are required")
    def test_long_audio_split_keeps_order_offsets_and_short_final_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.mp3"
            parts_dir = Path(tmp) / "parts"
            subprocess.run(
                ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=2.2", "-c:a", "libmp3lame",
                 "-b:a", "32k", str(source)],
                check=True,
            )
            parts = split_audio_for_transcription(source, parts_dir, chunk_seconds=1)
            offsets = [offset for _, offset in parts]
            self.assertEqual(offsets[0], 0.0)
            self.assertAlmostEqual(offsets[1], 0.9, delta=0.01)
            self.assertAlmostEqual(offsets[2], 1.8, delta=0.01)
            self.assertTrue(all(path.is_file() and path.stat().st_size > 0 for path, _ in parts))
            self.assertLess(_probe_audio_duration(parts[-1][0]), 1.0, "the final segment is a valid shorter remainder")

    def test_stage2_slice_failure_retries_and_never_transcribes_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "long_source.mp4"
            source.write_bytes(b"source fixture")
            process_dir = Path(tmp) / "processing"
            clip = {
                "id": "clip-stage2-slice-failure", "chunk_id": "chunk-stage2-slice-failure",
                "start_time": 100.0, "end_time": 120.0,
                "padded_start": 96.0, "padded_end": 124.0,
            }
            failed_ffmpeg = SimpleNamespace(returncode=1, stderr="synthetic extraction error")
            with patch("dispatch.transcription.gemini_engine.db.get_queued_transcription_clips", return_value=[clip]), \
                 patch("dispatch.transcription.gemini_engine.db.get_chunk_by_id", return_value={"filepath": str(source)}), \
                 patch("dispatch.transcription.gemini_engine.check_and_increment_daily_transcribe_quota", return_value=True), \
                 patch("subprocess.run", return_value=failed_ffmpeg) as run_ffmpeg, \
                 patch("dispatch.transcription.gemini_engine.transcribe_stage2_precision_words") as transcribe, \
                 patch("dispatch.transcription.gemini_engine.PROCESSING_DIR", process_dir):
                drained = drain_queued_transcriptions(max_batch=1)
            self.assertEqual(drained, 0)
            self.assertEqual(run_ffmpeg.call_count, 2, "bounded extraction gets one safe retry")
            transcribe.assert_not_called()

    def test_transcription_queue_stats_and_backlog(self):
        # Insert a queued_transcription clip directly into SQLite
        clip_id = "test_queued_clip_999"
        with db.get_db_connection() as conn:
            conn.cursor().execute("DELETE FROM clips WHERE id = ?", (clip_id,))
            conn.cursor().execute("""
                INSERT INTO clips (id, start_time, end_time, padded_start, padded_end, duration, title, hook, virality_score, status)
                VALUES (?, 10.0, 40.0, 6.0, 44.0, 30.0, 'Test Clip', 'Test Hook', 85, 'queued_transcription')
            """, (clip_id,))
            conn.commit()

        try:
            queued = db.get_queued_transcription_clips(limit=10)
            self.assertTrue(any(c["id"] == clip_id for c in queued))

            stats = db.get_transcription_queue_stats()
            self.assertGreaterEqual(stats["queued_count"], 1)
            self.assertEqual(stats["daily_quota_max"], 25)
        finally:
            with db.get_db_connection() as conn:
                conn.cursor().execute("DELETE FROM clips WHERE id = ?", (clip_id,))
                conn.commit()

    @patch("dispatch.transcription.gemini_engine.check_and_increment_daily_transcribe_quota")
    def test_drain_queued_transcriptions_respects_quota(self, mock_quota):
        # When quota is exhausted, drain stops immediately
        mock_quota.return_value = False
        from dispatch.transcription.gemini_engine import drain_queued_transcriptions
        drained = drain_queued_transcriptions(max_batch=5)
        self.assertEqual(drained, 0)


if __name__ == "__main__":
    unittest.main()
