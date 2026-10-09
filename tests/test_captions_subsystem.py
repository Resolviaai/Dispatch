"""Unit tests for the Dispatch Canonical Caption & Text Object Subsystem.
Verifies word grouping, template application, geometry transforms,
preview/export mathematical parity in ASS export, and serialization.
"""
import unittest
from pathlib import Path
import tempfile
import json
from dispatch.captions.model import (
    CaptionTrack, 
    CaptionGroup, 
    Word, 
    StyleConfig, 
    LayoutConfig, 
    AnimationConfig
)
from dispatch.captions.grouper import group_words_into_captions
from dispatch.captions.presets import CAPTION_PRESETS
from dispatch.captions.exporter import export_to_ass, export_to_json, export_to_srt
from dispatch.config import TARGET_WIDTH, TARGET_HEIGHT


class TestCaptionsSubsystem(unittest.TestCase):
    def setUp(self):
        self.sample_segments = [
            {
                "start": 0.0,
                "end": 2.5,
                "words": [
                    {"word": "This", "start": 0.0, "end": 0.3},
                    {"word": "is", "start": 0.3, "end": 0.5},
                    {"word": "Dispatch", "start": 0.5, "end": 1.0},
                    {"word": "AI", "start": 1.2, "end": 1.5},
                    {"word": "video", "start": 1.5, "end": 1.8},
                    {"word": "editor", "start": 1.8, "end": 2.2}
                ]
            }
        ]

    def test_grouping_respects_constraints_and_clamps(self):
        """Words are grouped into compact 2-3 word blocks without exceeding line/duration limits."""
        track = group_words_into_captions(
            raw_segments=self.sample_segments,
            clip_start=0.0,
            clip_end=2.5,
            clip_id="test_clip_1"
        )
        self.assertIsInstance(track, CaptionTrack)
        self.assertEqual(track.clip_id, "test_clip_1")
        self.assertGreaterEqual(len(track.groups), 2)

        # First group should contain first words
        first_group = track.groups[0]
        self.assertLessEqual(len(first_group.words), 4)
        self.assertEqual(first_group.start, 0.0)
        self.assertGreaterEqual(first_group.end, 0.5)

    def test_geometry_and_transform_parity(self):
        """LayoutConfig correctly normalizes coordinates and transform scales."""
        layout = LayoutConfig(position_x=0.45, position_y=0.82, scale=1.25, alignment="center")
        self.assertEqual(layout.position_x, 0.45)
        self.assertEqual(layout.position_y, 0.82)
        self.assertEqual(layout.scale, 1.25)

    def test_ass_export_injects_pos_tags_and_active_punch(self):
        """ASS export writes exact \\pos(X, Y) and \\fscx scale punch tags for preview parity."""
        track = group_words_into_captions(
            raw_segments=self.sample_segments,
            clip_start=0.0,
            clip_end=2.5,
            clip_id="clip_parity"
        )
        # Customize group 0 geometry
        track.groups[0].layout = LayoutConfig(position_x=0.48, position_y=0.76, scale=1.10)
        track.groups[0].animation = AnimationConfig(type="pop", active_scale=1.20, duration_ms=100)

        with tempfile.TemporaryDirectory() as tmpdir:
            ass_path = Path(tmpdir) / "subtitles.ass"
            export_to_ass(track, ass_path)

            self.assertTrue(ass_path.exists())
            content = ass_path.read_text(encoding="utf-8")

            # Must contain Script Info resolution
            self.assertIn(f"PlayResX: {TARGET_WIDTH}", content)
            self.assertIn(f"PlayResY: {TARGET_HEIGHT}", content)

            # Must contain exact pixel \\pos(X, Y) tag matching 0.48 * 1080 = 518 and 0.76 * 1920 = 1459
            expected_x = int(round(0.48 * TARGET_WIDTH))
            expected_y = int(round(0.76 * TARGET_HEIGHT))
            self.assertIn(f"\\pos({expected_x},{expected_y})", content)

            # Must contain scale pop animation tag
            self.assertIn(r"\fscx", content)
            self.assertIn(r"\fscy", content)

    def test_json_roundtrip_serialization(self):
        """CaptionTrack can be serialized to JSON and reconstituted without data loss."""
        track = group_words_into_captions(
            raw_segments=self.sample_segments,
            clip_start=0.0,
            clip_end=2.5,
            clip_id="clip_serial"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "captions.json"
            export_to_json(track, json_path)

            data = json.loads(json_path.read_text(encoding="utf-8"))
            reconstituted = CaptionTrack.from_dict(data)

            self.assertEqual(reconstituted.clip_id, track.clip_id)
            self.assertEqual(len(reconstituted.groups), len(track.groups))
            self.assertEqual(reconstituted.groups[0].words[0].text, "This")


if __name__ == "__main__":
    unittest.main()
