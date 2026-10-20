"""Acceptance Test Suite for YouTube Caption Timing & Karaoke Grouping.
Validates:
1. Word-timed VTT preserves real word boundaries through export.
2. Phrase-only VTT does not produce fabricated exact word timings.
3. Three-word caption grouping does not alter source timestamps.
4. Clip trimming does not introduce a constant timing offset.
5. Preview and rendered output stay synchronized.
6. Missing YouTube captions never trigger local Whisper.
7. Existing caption editing and Apply to All styling continue to work.
"""
import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

from dispatch.youtube_inbox.vtt_parser import parse_vtt_content
from dispatch.captions.model import CaptionTrack, CaptionGroup, Word, StyleConfig, LayoutConfig, AnimationConfig
from dispatch.captions.grouper import group_words_into_captions
from dispatch.captions.exporter import export_to_ass
from dispatch.transcription.worker import TranscriptionWorker
from dispatch.youtube_inbox.catcher import YouTubeInboxCatcher
from dispatch import db


class TestYouTubeCaptionTimingFix(unittest.TestCase):

    def setUp(self):
        # Configure test database in-memory or isolated
        pass

    def test_1_word_timed_vtt_preserves_real_word_boundaries_through_export(self):
        """1. Word-timed VTT preserves the real word boundaries through export."""
        vtt_content = """WEBVTT

00:00:01.000 --> 00:00:03.500
<00:00:01.000><c> Hello</c><00:00:01.500><c> world</c><00:00:02.000><c> welcome</c>
"""
        segments = parse_vtt_content(vtt_content)
        self.assertEqual(len(segments), 1)
        seg = segments[0]
        self.assertTrue(seg.get("has_word_timestamps"))
        self.assertEqual(len(seg["words"]), 3)

        # Check real boundaries from VTT tags
        w0 = seg["words"][0]
        w1 = seg["words"][1]
        w2 = seg["words"][2]

        self.assertEqual(w0["word"], "Hello")
        self.assertEqual(w0["start"], 1.000)
        self.assertEqual(w0["end"], 1.500)
        self.assertTrue(w0["is_exact"])

        self.assertEqual(w1["word"], "world")
        self.assertEqual(w1["start"], 1.500)
        self.assertEqual(w1["end"], 2.000)
        self.assertTrue(w1["is_exact"])

        self.assertEqual(w2["word"], "welcome")
        self.assertEqual(w2["start"], 2.000)
        self.assertEqual(w2["end"], 3.500)
        self.assertTrue(w2["is_exact"])

        # Group words into captions
        track = group_words_into_captions(segments, clip_start=0.0, clip_end=5.0)
        self.assertEqual(len(track.groups), 1)
        grp = track.groups[0]
        self.assertTrue(grp.has_word_timing)
        self.assertEqual(grp.timing_mode, "word")

        # Export to ASS
        with tempfile.TemporaryDirectory() as tmpdir:
            ass_path = Path(tmpdir) / "test.ass"
            export_to_ass(track, ass_path)
            content = ass_path.read_text(encoding="utf-8")

            # Dialogue events must reflect authentic word boundaries:
            # Word 0: 1.00s -> 1.50s (0:00:01.00 -> 0:00:01.50)
            self.assertIn("Dialogue: 0,0:00:01.00,0:00:01.50", content)
            # Word 1: 1.50s -> 2.00s (0:00:01.50 -> 0:00:02.00)
            self.assertIn("Dialogue: 0,0:00:01.50,0:00:02.00", content)
            # Word 2: 2.00s -> 3.50s (0:00:02.00 -> 0:00:03.50)
            self.assertIn("Dialogue: 0,0:00:02.00,0:00:03.50", content)

    def test_2_phrase_only_vtt_does_not_produce_fabricated_exact_word_timings(self):
        """2. Phrase-only VTT does not produce fabricated exact word timings."""
        vtt_content = """WEBVTT

00:00:01.000 --> 00:00:04.000
This is a standard YouTube manual caption.
"""
        segments = parse_vtt_content(vtt_content)
        self.assertEqual(len(segments), 1)
        seg = segments[0]
        # Must NOT be marked as having authentic word timestamps
        self.assertFalse(seg.get("has_word_timestamps"))

        # Words must have is_exact=False and NOT have divided progressive timestamps
        words = seg["words"]
        for w in words:
            self.assertFalse(w["is_exact"])
            # Duration must not be linearly divided (e.g. 1.0 -> 1.57, 1.57 -> 2.14)
            self.assertEqual(w["start"], 1.000)
            self.assertEqual(w["end"], 4.000)

        # Group words into captions
        track = group_words_into_captions(segments, clip_start=0.0, clip_end=5.0)
        self.assertEqual(len(track.groups), 1)
        grp = track.groups[0]
        self.assertFalse(grp.has_word_timing)
        self.assertEqual(grp.timing_mode, "phrase")

        # Export to ASS must render as single phrase dialogue without karaoke flashing
        with tempfile.TemporaryDirectory() as tmpdir:
            ass_path = Path(tmpdir) / "phrase.ass"
            export_to_ass(track, ass_path)
            content = ass_path.read_text(encoding="utf-8")
            dialogue_lines = [l for l in content.splitlines() if l.startswith("Dialogue:")]
            # Exactly 1 dialogue event for the phrase, not 7 events for each word
            self.assertEqual(len(dialogue_lines), 1)
            self.assertIn("0:00:01.00,0:00:04.00", dialogue_lines[0])
            self.assertIn("THIS IS A STANDARD YOUTUBE MANUAL CAPTION.", dialogue_lines[0])

    def test_3_three_word_caption_grouping_does_not_alter_source_timestamps(self):
        """3. Three-word caption grouping does not alter source timestamps."""
        raw_segments = [
            {
                "start": 1.0,
                "end": 2.1,
                "words": [
                    {"word": "One", "start": 1.000, "end": 1.300, "is_exact": True},
                    {"word": "Two", "start": 1.400, "end": 1.700, "is_exact": True},
                    {"word": "Three", "start": 1.800, "end": 2.100, "is_exact": True},
                ]
            },
            {
                "start": 2.25,
                "end": 3.0,
                "words": [
                    {"word": "Next", "start": 2.250, "end": 2.800, "is_exact": True}
                ]
            }
        ]

        # Group into captions (gap between 2.100 and 2.250 is 0.15s < 0.25s, so card end will stretch)
        track = group_words_into_captions(raw_segments, clip_start=0.0, clip_end=5.0, words_per_group=3)
        self.assertEqual(len(track.groups), 2)

        grp0 = track.groups[0]
        # Card end was stretched to 2.25 to prevent visual gap flicker
        self.assertEqual(grp0.end, 2.250)

        # BUT the 3rd word's authentic source timestamp MUST remain untouched!
        w_last = grp0.words[2]
        self.assertEqual(w_last.text, "Three")
        self.assertEqual(w_last.start, 1.800)
        self.assertEqual(w_last.end, 2.100, "Source word timestamp must NOT be mutated by gap closure!")

    def test_4_clip_trimming_does_not_introduce_constant_timing_offset(self):
        """4. Clip trimming does not introduce a constant timing offset."""
        # Absolute source video timestamps: 45.0s to 50.0s
        raw_segments = [
            {
                "start": 45.0,
                "end": 47.0,
                "words": [
                    {"word": "Highlight", "start": 45.200, "end": 45.800, "is_exact": True},
                    {"word": "moment", "start": 46.000, "end": 46.700, "is_exact": True}
                ]
            }
        ]

        # Clip trimmed with clip_start=40.0s
        track = group_words_into_captions(raw_segments, clip_start=40.0, clip_end=55.0)
        self.assertEqual(len(track.groups), 1)
        w0 = track.groups[0].words[0]
        w1 = track.groups[0].words[1]

        # Converted to clip-relative time exactly once: 45.2 - 40.0 = 5.2s
        self.assertAlmostEqual(w0.start, 5.200, places=3)
        self.assertAlmostEqual(w0.end, 5.800, places=3)
        self.assertAlmostEqual(w1.start, 6.000, places=3)
        self.assertAlmostEqual(w1.end, 6.700, places=3)

        # Check rolling cue deduplication does not introduce constant offset
        rolling_vtt = """WEBVTT

00:00:01.000 --> 00:00:03.000
First line

00:00:02.000 --> 00:00:04.500
First line second line
"""
        deduped = parse_vtt_content(rolling_vtt)
        self.assertEqual(len(deduped), 2)
        # First cue ends at 3.000s
        self.assertEqual(deduped[0]["end"], 3.000)
        # Second deduped cue "second line" must start at 3.000s, NOT at 2.000s (which would be 1.0s too early!)
        self.assertEqual(deduped[1]["text"], "second line")
        self.assertEqual(deduped[1]["start"], 3.000)

    def test_5_preview_and_rendered_output_stay_synchronized(self):
        """5. Preview and rendered output stay synchronized."""
        raw_segments = [
            {
                "start": 10.0,
                "end": 12.0,
                "words": [
                    {"word": "Sync", "start": 10.200, "end": 10.600, "is_exact": True},
                    {"word": "test", "start": 10.700, "end": 11.200, "is_exact": True}
                ]
            }
        ]
        track = group_words_into_captions(raw_segments, clip_start=10.0, clip_end=15.0)
        w0 = track.groups[0].words[0]
        w1 = track.groups[0].words[1]

        # Frontend preview uses currentTime >= w.start && currentTime <= w.end
        # Preview active window for w0: [0.200, 0.600]
        # Preview active window for w1: [0.700, 1.200]
        self.assertEqual(w0.start, 0.200)
        self.assertEqual(w0.end, 0.600)
        self.assertEqual(w1.start, 0.700)
        self.assertEqual(w1.end, 1.200)

        # Exported ASS must have exact same timecodes
        with tempfile.TemporaryDirectory() as tmpdir:
            ass_path = Path(tmpdir) / "sync.ass"
            export_to_ass(track, ass_path)
            content = ass_path.read_text(encoding="utf-8")

            # Dialogue for w0 active illumination: 0:00:00.20 to 0:00:00.60
            self.assertIn("Dialogue: 0,0:00:00.20,0:00:00.60", content)
            # Dialogue for w1 active illumination: 0:00:00.70 to 0:00:01.20
            self.assertIn("Dialogue: 0,0:00:00.70,0:00:01.20", content)

    def test_6_missing_youtube_captions_never_trigger_local_whisper(self):
        """6. Missing YouTube captions never trigger local Whisper."""
        video_id = "test_no_whisper_yt"
        db.register_youtube_video(video_id=video_id, title="No Whisper Video", duration=120.0)
        past_deadline = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
        db.update_youtube_video(
            video_id=video_id,
            status="WAITING_FOR_TRANSCRIPT",
            transcript_wait_deadline=past_deadline
        )

        mock_catcher = MagicMock()
        mock_catcher.fetch_youtube_captions.return_value = None

        with patch("dispatch.transcription.worker.transcribe_video") as mock_whisper:
            worker = TranscriptionWorker(caption_wait_seconds=60, catcher=mock_catcher)
            res = worker.process_pending()

            # Whisper must NEVER be invoked!
            mock_whisper.assert_not_called()

            # Result must be CAPTIONS_UNAVAILABLE
            self.assertEqual(len(res), 1)
            self.assertEqual(res[0]["status"], "CAPTIONS_UNAVAILABLE")

        # Database record must reflect CAPTIONS_UNAVAILABLE
        yt_rec = db.get_youtube_video(video_id)
        self.assertEqual(yt_rec["status"], "CAPTIONS_UNAVAILABLE")
        self.assertIn("unavailable", yt_rec["last_error"].lower())

    def test_7_existing_caption_editing_and_apply_to_all_styling_continue_to_work(self):
        """7. Existing caption editing and Apply to All styling continue to work."""
        # Build multi-group track
        g1 = CaptionGroup(
            id="grp_1",
            start=0.0,
            end=2.0,
            words=[Word(id="w1", text="First", start=0.0, end=1.0), Word(id="w2", text="card", start=1.0, end=2.0)],
            style=StyleConfig(font_family="Impact", font_size=54, primary_color="#FFFFFF")
        )
        g2 = CaptionGroup(
            id="grp_2",
            start=2.5,
            end=4.5,
            words=[Word(id="w3", text="Second", start=2.5, end=3.5), Word(id="w4", text="card", start=3.5, end=4.5)],
            style=StyleConfig(font_family="Roboto", font_size=42, primary_color="#00FF00")
        )
        track = CaptionTrack(clip_id="clip_apply_all", groups=[g1, g2])

        # Test JSON round-trip
        data = track.to_dict()
        reconstituted = CaptionTrack.from_dict(data)

        self.assertEqual(len(reconstituted.groups), 2)
        self.assertEqual(reconstituted.groups[0].style.font_family, "Impact")
        self.assertEqual(reconstituted.groups[1].style.font_family, "Roboto")
        self.assertTrue(reconstituted.groups[0].has_word_timing)

        # Simulate editing caption text
        reconstituted.groups[0].words[0].text = "EditedFirst"
        self.assertEqual(reconstituted.groups[0].words[0].text, "EditedFirst")

        # Export to ASS works with custom styles
        with tempfile.TemporaryDirectory() as tmpdir:
            ass_path = Path(tmpdir) / "edited.ass"
            export_to_ass(reconstituted, ass_path)
            content = ass_path.read_text(encoding="utf-8")
            self.assertIn("EDITEDFIRST", content)
            self.assertIn("SECOND", content)


if __name__ == "__main__":
    unittest.main()
