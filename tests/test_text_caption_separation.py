import unittest
from pathlib import Path
import tempfile

from dispatch.captions.model import CaptionTrack, CaptionGroup, Word, StyleConfig, LayoutConfig, AnimationConfig
from dispatch.captions.exporter import export_to_ass


class TestTextCaptionSeparation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_ass = Path(self.temp_dir.name) / "test.ass"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_normal_text_layer_exports_single_dialogue_without_word_cuts(self):
        track = CaptionTrack(
            groups=[
                CaptionGroup(
                    id="txt_1",
                    type="text",
                    start=1.0,
                    end=4.0,
                    words=[
                        Word(id="w1", text="Normal", start=1.0, end=2.5),
                        Word(id="w2", text="Text", start=2.5, end=4.0),
                    ],
                    style=StyleConfig(
                        primary_color="#38BDF8",
                        highlight_color="#FFE600",
                        highlight_enabled=False,
                    ),
                )
            ]
        )
        export_to_ass(track, self.output_ass)
        content = self.output_ass.read_text(encoding="utf-8")

        # Normal text should export ONE single dialogue event across the whole 1.00-4.00 range
        dialogue_lines = [l for l in content.splitlines() if l.startswith("Dialogue:")]
        self.assertEqual(len(dialogue_lines), 1)
        self.assertIn("NORMAL TEXT", dialogue_lines[0])
        # Color &H00F8BD38& is #38BDF8 in ASS BGR
        self.assertIn("&H00F8BD38&", dialogue_lines[0])
        # Caption highlight &H0000E6FF& (#FFE600) must NOT be present
        self.assertNotIn("&H0000E6FF&", dialogue_lines[0])

    def test_caption_with_highlight_enabled_exports_highlight_color(self):
        track = CaptionTrack(
            groups=[
                CaptionGroup(
                    id="cap_1",
                    type="caption",
                    start=1.0,
                    end=4.0,
                    words=[
                        Word(id="w1", text="Spoken", start=1.0, end=2.5),
                        Word(id="w2", text="Words", start=2.5, end=4.0),
                    ],
                    style=StyleConfig(
                        primary_color="#FFFFFF",
                        highlight_color="#FFE600",
                        highlight_enabled=True,
                    ),
                )
            ]
        )
        export_to_ass(track, self.output_ass)
        content = self.output_ass.read_text(encoding="utf-8")

        # Caption with highlighting enabled SHOULD contain highlight color &H0000E6FF& (#FFE600)
        self.assertIn("&H0000E6FF&", content)
        self.assertIn("SPOKEN", content)
        self.assertIn("WORDS", content)

    def test_caption_with_highlight_disabled_exports_uniform_static_colors(self):
        track = CaptionTrack(
            groups=[
                CaptionGroup(
                    id="cap_off",
                    type="caption",
                    start=1.0,
                    end=4.0,
                    words=[
                        Word(id="w1", text="Uniform", start=1.0, end=2.5),
                        Word(id="w2", text="Caption", start=2.5, end=4.0),
                    ],
                    style=StyleConfig(
                        primary_color="#FFFFFF",
                        highlight_color="#FFE600",
                        highlight_enabled=False,
                    ),
                )
            ]
        )
        export_to_ass(track, self.output_ass)
        content = self.output_ass.read_text(encoding="utf-8")

        # Caption with highlight_enabled=False should NOT contain highlight color &H0000E6FF&
        self.assertNotIn("&H0000E6FF&", content)
        self.assertIn("UNIFORM", content)
        self.assertIn("CAPTION", content)

    def test_simultaneous_caption_and_text_both_exported(self):
        track = CaptionTrack(
            groups=[
                CaptionGroup(
                    id="cap_sub",
                    type="caption",
                    start=0.0,
                    end=5.0,
                    words=[Word(id="w1", text="Subtitle", start=0.0, end=5.0)],
                    layout=LayoutConfig(position_y=0.8),
                ),
                CaptionGroup(
                    id="txt_brand",
                    type="text",
                    start=0.0,
                    end=10.0,
                    words=[Word(id="w2", text="Brand", start=0.0, end=10.0)],
                    layout=LayoutConfig(position_y=0.1),
                ),
            ]
        )
        export_to_ass(track, self.output_ass)
        content = self.output_ass.read_text(encoding="utf-8")

        # Both dialogue lines must be present in the ASS output
        dialogue_lines = [l for l in content.splitlines() if l.startswith("Dialogue:")]
        self.assertGreaterEqual(len(dialogue_lines), 2)
        self.assertTrue(any("SUBTITLE" in l for l in dialogue_lines))
        self.assertTrue(any("BRAND" in l for l in dialogue_lines))


if __name__ == "__main__":
    unittest.main()
