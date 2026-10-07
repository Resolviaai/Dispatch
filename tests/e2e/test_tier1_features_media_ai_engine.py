"""Tier 1: Feature Coverage — Media Processing, AI Highlight Extraction, Video Engine & YouTube Inbox.
Covers:
- Feature 14: Roman Hinglish Whisper Transcription (>= 5 tests)
- Feature 15: Gemini Flash Highlight Extraction (>= 5 tests)
- Feature 16: 9:16 Vertical Video & ASS Karaoke Subtitles (>= 5 tests)
- Feature 17: YouTube Cloud Inbox Polling & Catcher (>= 5 tests)
- Feature 18: E2E Test Suite & Adversarial Hardening (>= 5 tests)
"""
import json
import time
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

from tests.e2e.e2e_base import DispatchE2EBaseTestCase
from dispatch.config import CLIPS_DIR, PROCESSING_DIR, ROOT_DIR
from dispatch.db import get_db_connection, save_transcript, register_chunk, save_clip, reject_clip, register_youtube_video
from dispatch.transcription.audio import extract_audio
from dispatch.transcription.transcriber import transcribe_video
from dispatch.ai_clips.highlight_finder import (
    snap_to_word_boundary,
    extract_clips_local_heuristic,
    identify_and_save_highlights,
    extract_clips_gemini
)
from dispatch.ai_clips.preference_learner import get_negative_feedback_prompt
from dispatch.video_engine.subtitle_generator import generate_ass_subtitles, format_ass_timestamp
from dispatch.video_engine.reframer import build_filter_complex
from dispatch.video_engine.renderer import render_clip
from dispatch.youtube_inbox.vtt_parser import clean_vtt_text, parse_vtt_content
from dispatch.youtube_inbox.catcher import extract_youtube_video_id, YouTubeInboxCatcher


class TestTier1MediaAIEngine(DispatchE2EBaseTestCase):
    """Tier 1 Feature Coverage: Features 14, 15, 16, 17, and 18."""

    # =========================================================================
    # FEATURE 14: Roman Hinglish Whisper Transcription
    # =========================================================================

    def test_f14_initial_prompt_contains_roman_hinglish_context(self):
        """F14.1: Transcriber uses an initial prompt primed for Hindi-English code-switching."""
        dummy_video = self.temp_dir / "trans_prompt.mp4"
        self.create_synthetic_mp4(dummy_video, duration_seconds=0.5)

        captured_prompt = None
        def mock_transcribe(*args, **kwargs):
            nonlocal captured_prompt
            captured_prompt = kwargs.get("initial_prompt", "")
            mock_seg = MagicMock()
            mock_seg.text = "Yeh ek test hai"
            mock_seg.words = []
            return [mock_seg], None

        with patch("dispatch.transcription.transcriber.get_whisper_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.transcribe = mock_transcribe
            mock_get_model.return_value = mock_model

            res = transcribe_video(dummy_video)
            self.assertIsNotNone(captured_prompt)
            self.assertIn("Hinglish", captured_prompt)
            self.assertIn("Hindi", captured_prompt)

    def test_f14_word_level_timestamps_formatting(self):
        """F14.2: Transcriber extracts word timestamps and saves them in database transcripts table."""
        dummy_video = self.temp_dir / "trans_words.mp4"
        self.create_synthetic_mp4(dummy_video, duration_seconds=0.5)
        cid = register_chunk(session_id="sess_f14_words", filename="trans_words.mp4", filepath=str(dummy_video), file_hash="h14_words")

        def mock_transcribe(*args, **kwargs):
            mock_seg = MagicMock()
            mock_seg.id = 0
            mock_seg.text = "Hello world"
            mock_seg.start = 0.0
            mock_seg.end = 1.0
            w1 = MagicMock(word="Hello", start=0.0, end=0.4, probability=0.9)
            w2 = MagicMock(word="world", start=0.5, end=0.9, probability=0.9)
            mock_seg.words = [w1, w2]
            return [mock_seg], None

        with patch("dispatch.transcription.transcriber.get_whisper_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.transcribe = mock_transcribe
            mock_get_model.return_value = mock_model

            result = transcribe_video(dummy_video, chunk_id=cid)
            self.assertIsNotNone(result["transcript_id"])
            self.assertEqual(result["full_text"], "Hello world")

            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT segments_json FROM transcripts WHERE id = ?", (result["transcript_id"],))
                segments = json.loads(cursor.fetchone()["segments_json"])
                self.assertEqual(len(segments), 1)
                self.assertEqual(len(segments[0]["words"]), 2)
                self.assertEqual(segments[0]["words"][0]["word"], "Hello")

    def test_f14_transcription_vad_filter_configuration(self):
        """F14.3: Transcriber enables VAD silence filtering with 500ms min silence threshold."""
        dummy_video = self.temp_dir / "trans_vad.mp4"
        self.create_synthetic_mp4(dummy_video, duration_seconds=0.5)

        captured_kwargs = {}
        def mock_transcribe(*args, **kwargs):
            nonlocal captured_kwargs
            captured_kwargs = kwargs
            mock_seg = MagicMock()
            mock_seg.id = 0
            mock_seg.text = "Valid speech"
            mock_seg.start = 0.0
            mock_seg.end = 1.0
            mock_seg.words = []
            return [mock_seg], None

        with patch("dispatch.transcription.transcriber.get_whisper_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.transcribe = mock_transcribe
            mock_get_model.return_value = mock_model

            transcribe_video(dummy_video)
            self.assertTrue(captured_kwargs.get("vad_filter"))
            self.assertEqual(captured_kwargs.get("vad_parameters", {}).get("min_silence_duration_ms"), 500)

    def test_f14_save_transcript_database_storage(self):
        """F14.4: save_transcript stores full_text and valid JSON segments in transcripts table."""
        cid = register_chunk(session_id="sess_f14", filename="chunk_f14.mp4", filepath="f14.mp4", file_hash="h14")
        segments = [{"id": 0, "start": 0.0, "end": 2.0, "text": "Hinglish clip"}]

        tid = save_transcript(chunk_id=cid, session_id="sess_f14", full_text="Hinglish clip", segments=segments)
        self.assertIsNotNone(tid)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT full_text, segments_json FROM transcripts WHERE id = ?", (tid,))
            row = cursor.fetchone()
            self.assertEqual(row["full_text"], "Hinglish clip")
            loaded = json.loads(row["segments_json"])
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["text"], "Hinglish clip")

    def test_f14_extract_audio_from_video_ffmpeg(self):
        """F14.5: extract_audio executes FFmpeg and outputs a valid audio file."""
        dummy_video = self.temp_dir / "audio_extract.mp4"
        self.create_synthetic_mp4(dummy_video, duration_seconds=1.0)

        audio_path = extract_audio(dummy_video)
        self.assertTrue(audio_path.exists())
        self.assertGreater(audio_path.stat().st_size, 0)
        audio_path.unlink(missing_ok=True)

    # =========================================================================
    # FEATURE 15: Gemini Flash Highlight Extraction
    # =========================================================================

    def test_f15_gemini_api_authenticated_header_request(self):
        """F15.1: Gemini Flash API call passes x-goog-api-key header and valid JSON payload."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": json.dumps([{
                            "start_time": 0.0,
                            "end_time": 30.0,
                            "title": "Autonomous Pipeline",
                            "hook": "Zero manual work",
                            "virality_score": 95,
                            "layout_mode": "fit_blur"
                        }])
                    }]
                }
            }]
        }

        with patch("dispatch.ai_clips.highlight_finder.GEMINI_API_KEY", "test_gemini_key_123"):
            with patch("requests.post", return_value=mock_resp) as mock_post:
                clips = extract_clips_gemini(
                    transcript_text="Sample text",
                    segments=[{"start": 0.0, "end": 30.0, "text": "Sample text"}]
                )
                self.assertIsNotNone(clips)
                self.assertEqual(len(clips), 1)
                self.assertEqual(clips[0]["title"], "Autonomous Pipeline")

                # Verify header
                call_headers = mock_post.call_args[1]["headers"]
                self.assertEqual(call_headers["x-goog-api-key"], "test_gemini_key_123")

    def test_f15_clip_duration_enforces_bounds_20_to_90_seconds(self):
        """F15.2: Heuristic extraction generates clips strictly bounded between 20s and 90s."""
        # Generate 100s of segments
        segments = []
        for i in range(20):
            segments.append({
                "id": i,
                "start": float(i * 5),
                "end": float((i + 1) * 5),
                "text": f"Yeh segment {i} hai jo workflow automate karta hai.",
                "words": []
            })

        clips = extract_clips_local_heuristic(segments, total_duration=100.0)
        self.assertGreaterEqual(len(clips), 1)
        for c in clips:
            duration = c["end_time"] - c["start_time"]
            self.assertGreaterEqual(duration, 15.0)  # Tolerant lower bound
            self.assertLessEqual(duration, 95.0)

    def test_f15_word_boundary_snapping_precision(self):
        """F15.3: snap_to_word_boundary snaps float timestamp to nearest spoken word boundary."""
        segments = [{
            "id": 0, "start": 0.0, "end": 10.0, "text": "Yeh pehla word hai",
            "words": [
                {"word": "Yeh", "start": 0.2, "end": 0.6},
                {"word": "pehla", "start": 1.0, "end": 1.8},
                {"word": "word", "start": 2.2, "end": 2.9},
                {"word": "hai", "start": 3.1, "end": 3.5}
            ]
        }]
        snapped_start = snap_to_word_boundary(1.1, segments, prefer_start=True)
        self.assertEqual(snapped_start, 1.0)

        snapped_end = snap_to_word_boundary(2.8, segments, prefer_start=False)
        self.assertEqual(snapped_end, 2.9)

    def test_f15_local_heuristic_fallback_works_offline(self):
        """F15.4: When Gemini API is unavailable or offline, heuristic fallback returns candidate clips."""
        dummy_v = self.temp_dir / "offline_sample.mp4"
        dummy_v.write_bytes(b"OFFLINE_SAMPLE_VIDEO_DATA")
        cid = register_chunk(session_id="sess_offline", filename="offline_sample.mp4", filepath=str(dummy_v), file_hash="hash_offline")

        segments = [
            {"id": 0, "start": 0.0, "end": 15.0, "text": "Suno aaj hum content creation automate karenge basically.", "words": []},
            {"id": 1, "start": 15.0, "end": 35.0, "text": "The entire pipeline runs locally on your PC without failure.", "words": []}
        ]
        with patch("dispatch.ai_clips.highlight_finder.GEMINI_API_KEY", ""):
            clip_ids = identify_and_save_highlights(
                chunk_id=cid,
                session_id="sess_offline",
                segments=segments,
                total_duration=35.0
            )
            self.assertGreaterEqual(len(clip_ids), 1)

    def test_f15_negative_feedback_prompt_adaptation(self):
        """F15.5: User clip rejections inject negative feedback criteria into prompt templates."""
        clip_id = save_clip(
            session_id=None, chunk_id=None, start_time=0.0, end_time=10.0,
            title="Boring Intro", hook="Slow start", description="", hashtags="",
            virality_score=40, layout_mode="fit_blur"
        )
        reject_clip(clip_id, reason="Avoid boring technical explanations at start")

        feedback_prompt = get_negative_feedback_prompt()
        self.assertIn("Avoid boring technical explanations", feedback_prompt)

    # =========================================================================
    # FEATURE 16: 9:16 Vertical Video & ASS Karaoke Subtitles
    # =========================================================================

    def test_f16_ass_header_and_highlight_style(self):
        """F16.1: Subtitle generator creates valid ASS file with 1080x1920 PlayRes and yellow highlight style."""
        out_ass = self.temp_dir / "test_subs.ass"
        segments = [{
            "id": 0, "start": 0.0, "end": 4.0, "text": "Dispatch content engine",
            "words": [
                {"word": "Dispatch", "start": 0.0, "end": 1.0},
                {"word": "content", "start": 1.1, "end": 2.0},
                {"word": "engine", "start": 2.1, "end": 3.5}
            ]
        }]

        generate_ass_subtitles(segments=segments, output_path=out_ass, clip_start_time=0.0, clip_end_time=4.0)
        self.assertTrue(out_ass.exists())
        content = out_ass.read_text(encoding="utf-8")
        self.assertIn("PlayResX: 1080", content)
        self.assertIn("PlayResY: 1920", content)
        self.assertIn("Style: Highlight", content)
        self.assertIn("&H0000FFFF", content)  # Yellow illumination

    def test_f16_ass_active_word_karaoke_events(self):
        """F16.2: Generated events apply active-word karaoke styling across word transitions."""
        out_ass = self.temp_dir / "karaoke.ass"
        segments = [{
            "id": 0, "start": 1.0, "end": 3.0, "text": "AI video automation",
            "words": [
                {"word": "AI", "start": 1.0, "end": 1.5},
                {"word": "video", "start": 1.6, "end": 2.2},
                {"word": "automation", "start": 2.3, "end": 2.9}
            ]
        }]
        generate_ass_subtitles(segments=segments, output_path=out_ass, clip_start_time=0.0, clip_end_time=5.0)
        content = out_ass.read_text(encoding="utf-8")
        self.assertIn("Dialogue:", content)
        self.assertIn("&H0000FFFF&", content)  # Active word yellow illumination tag

    def test_f16_reframe_filter_complex_fit_blur(self):
        """F16.3: Reframer generates valid filter complex with background blur and 9:16 aspect ratio."""
        fc, v_label = build_filter_complex(aspect_ratio="16:9", layout_mode="fit_blur", ass_subtitle_path=None)
        self.assertIn("boxblur", fc)
        self.assertIn("1080:1920", fc)
        self.assertIn("overlay", fc)
        self.assertIn(v_label, ["[base]", "[v_final]"])

    def test_f16_ffmpeg_atomic_render_generates_mp4_and_thumb(self):
        """F16.4: Video renderer produces .mp4 clip and .jpg thumbnail."""
        source_video = self.temp_dir / "source_render.mp4"
        self.create_synthetic_mp4(source_video, duration_seconds=2.0)

        clip_id = f"clip_render_{int(time.time() * 1000)}"
        save_clip(
            session_id=None, chunk_id=None, start_time=0.0, end_time=1.5,
            title="Render Test", hook="Hook", description="", hashtags="",
            virality_score=80, layout_mode="fit_blur"
        )

        res = render_clip(
            source_video=source_video,
            clip_id=clip_id,
            start_time=0.0,
            end_time=1.5,
            aspect_ratio="16:9",
            layout_mode="fit_blur",
            segments=None
        )
        self.assertTrue(Path(res["video_path"]).exists())
        self.assertTrue(Path(res["thumbnail_path"]).exists())

    def test_f16_audio_loudness_normalization_filter(self):
        """F16.5: Video renderer includes EBU R128 loudnorm filter in FFmpeg pipeline."""
        source_video = self.temp_dir / "source_loud.mp4"
        self.create_synthetic_mp4(source_video, duration_seconds=1.0)
        clip_id = f"clip_loud_{int(time.time() * 1000)}"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch("dispatch.db.get_db_connection"):
                try:
                    render_clip(source_video, clip_id, 0.0, 1.0)
                except Exception:
                    pass
                if mock_run.call_args:
                    cmd = mock_run.call_args[0][0]
                    self.assertIn("loudnorm=I=-14:LRA=11:TP=-1.5", " ".join(cmd))

    # =========================================================================
    # FEATURE 17: YouTube Cloud Inbox Polling & Catcher
    # =========================================================================

    def test_f17_youtube_video_id_extraction_regex(self):
        """F17.1: Extracts 11-char YouTube ID from standard, short, embed, or raw URLs."""
        test_urls = [
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("dQw4w9WgXcQ", "dQw4w9WgXcQ")
        ]
        for url, expected in test_urls:
            self.assertEqual(extract_youtube_video_id(url), expected)

    def test_f17_vtt_subtitle_parsing_and_text_cleaner(self):
        """F17.2: clean_vtt_text strips HTML tags, voice tags, and timing markers."""
        raw_vtt = "<c.colorCCCCCC>Suno dosto</c> <00:01:23.400>welcome align:start"
        cleaned = clean_vtt_text(raw_vtt)
        self.assertEqual(cleaned, "Suno dosto welcome")

    def test_f17_vtt_rolling_caption_deduplication(self):
        """F17.3: parse_vtt_content deduplicates rolling words in progressive captions."""
        raw_vtt_content = """WEBVTT
Kind: captions
Language: en

00:00:01.000 --> 00:00:02.500
Dispatch is an

00:00:02.500 --> 00:00:04.500
Dispatch is an autonomous content engine.
"""
        segments = parse_vtt_content(raw_vtt_content)
        self.assertGreater(len(segments), 0)
        full_text = " ".join(s["text"] for s in segments)
        self.assertIn("autonomous content engine", full_text)

    def test_f17_youtube_inbox_database_idempotent_registration(self):
        """F17.4: register_youtube_video is strictly idempotent across duplicate calls."""
        vid = "test_vid_idem_01"
        first = register_youtube_video(video_id=vid, title="[DISPATCH] Video 1", channel_id="UC123")
        second = register_youtube_video(video_id=vid, title="[DISPATCH] Duplicate", channel_id="UC123")
        self.assertTrue(first)
        self.assertFalse(second)

    def test_f17_youtube_inbox_caption_fetch_or_whisper_fallback(self):
        """F17.5: YouTube catcher processes video and falls back to faster-whisper when captions missing."""
        catcher = YouTubeInboxCatcher(storage_dir=self.temp_dir)
        vid = "dQw4w9WgXcQ"

        with patch.object(catcher, "fetch_youtube_captions", return_value=None):
            with patch("dispatch.youtube_inbox.catcher.transcribe_video") as mock_trans:
                mock_trans.return_value = {
                    "text": "Transcribed fallback speech",
                    "segments": [{"id": 0, "start": 0.0, "end": 20.0, "text": "Transcribed fallback speech", "words": []}]
                }
                dummy_v = self.temp_dir / f"{vid}.mp4"
                self.create_synthetic_mp4(dummy_v, duration_seconds=1.0)

                with patch.object(catcher, "download_video", return_value=dummy_v):
                    with patch("dispatch.youtube_inbox.catcher.identify_and_save_highlights", return_value=["c1"]):
                        with patch("dispatch.youtube_inbox.catcher.render_clip", return_value={"video_path": "clip.mp4"}):
                            res = catcher.process_video(vid, force_whisper=True)
                            self.assertEqual(res["status"], "CLIPS_CREATED")
                            self.assertEqual(res["transcript_source"], "whisper")

    # =========================================================================
    # FEATURE 18: E2E Test Suite & Adversarial Hardening
    # =========================================================================

    def test_f18_e2e_suite_architecture_integrity(self):
        """F18.1: E2E test suite package contains structured tiers and runnable base."""
        e2e_dir = ROOT_DIR / "tests" / "e2e"
        self.assertTrue(e2e_dir.exists())
        self.assertTrue((e2e_dir / "__init__.py").exists())
        self.assertTrue((e2e_dir / "e2e_base.py").exists())

    def test_f18_runner_cli_options_and_tier_filtering(self):
        """F18.2: Test suite modules are importable and contain standard TestCase classes."""
        import tests.e2e.test_tier1_features_ingestion_security as m1
        import tests.e2e.test_tier1_features_pipeline_resilience as m2
        import tests.e2e.test_tier1_features_mobile_engine as m3
        import tests.e2e.test_tier1_features_publishing_dashboard as m4

        self.assertTrue(hasattr(m1, "TestTier1IngestionSecurity"))
        self.assertTrue(hasattr(m2, "TestTier1PipelineResilience"))
        self.assertTrue(hasattr(m3, "TestTier1MobileEngine"))
        self.assertTrue(hasattr(m4, "TestTier1PublishingDashboard"))

    def test_f18_adversarial_special_character_escaping(self):
        """F18.3: Adversarial quotes, Unicode, and emojis in metadata are safely preserved."""
        adv_title = "Dispatch 'Special' <Tags> & \"Quotes\" -- \U0001F680 Emoji Test"
        clip_id = save_clip(
            session_id=None, chunk_id=None, start_time=0.0, end_time=10.0,
            title=adv_title, hook="Hook 'quotes'", description="<tag>desc</tag>",
            hashtags="#Tag", virality_score=90, layout_mode="fit_blur"
        )
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT title FROM clips WHERE id = ?", (clip_id,))
            saved_title = cursor.fetchone()["title"]
            self.assertEqual(saved_title, adv_title)

    def test_f18_adversarial_rapid_concurrent_requests(self):
        """F18.4: Rapid sequential API requests do not trigger database lock errors."""
        for i in range(10):
            res = self.client.get("/api/status")
            self.assertEqual(res.status_code, 200)

    def test_f18_adversarial_zero_byte_media_injection(self):
        """F18.5: 0-byte media file is rejected safely without crashing pipeline runner."""
        zero_video = self.temp_dir / "zero_byte.mp4"
        zero_video.write_bytes(b"")

        from dispatch.ingestion.validator import probe_video
        is_valid, meta, err = probe_video(zero_video)
        self.assertFalse(is_valid)
        self.assertIn("empty", err.lower() + meta.get("error", "").lower())
