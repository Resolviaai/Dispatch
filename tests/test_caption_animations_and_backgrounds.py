import unittest
from pathlib import Path
import tempfile

from dispatch.captions.model import CaptionTrack, CaptionGroup, Word, StyleConfig, LayoutConfig, AnimationConfig
from dispatch.captions.exporter import export_to_ass


class TestCaptionAnimationsAndBackgrounds(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_ass = Path(self.temp_dir.name) / "test.ass"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_background_mode_none_removes_box(self):
        track = CaptionTrack(
            groups=[
                CaptionGroup(
                    id="grp_1",
                    start=1.0,
                    end=3.0,
                    words=[
                        Word(id="w1", text="Hello", start=1.0, end=2.0),
                        Word(id="w2", text="World", start=2.0, end=3.0),
                    ],
                    style=StyleConfig(
                        background_enabled=False,
                        background_mode="none",
                    ),
                )
            ]
        )
        export_to_ass(track, self.output_ass)
        content = self.output_ass.read_text(encoding="utf-8")

        # The dialogue line must use Style "Default", NOT "Boxed"
        self.assertIn(",Default,", content)
        self.assertNotIn(",Boxed,", content)

    def test_background_mode_block_and_line_apply_box_and_offsets(self):
        track = CaptionTrack(
            groups=[
                CaptionGroup(
                    id="grp_1",
                    start=1.0,
                    end=3.0,
                    words=[
                        Word(id="w1", text="Pill", start=1.0, end=2.0),
                        Word(id="w2", text="Caption", start=2.0, end=3.0),
                    ],
                    style=StyleConfig(
                        background_enabled=True,
                        background_mode="line",
                        background_color="#FF0000",
                        background_opacity=0.8,
                        background_padding_x=16.0,
                        background_padding_y=10.0,
                        background_offset_x=12.0,
                        background_offset_y=-8.0,
                    ),
                    layout=LayoutConfig(
                        position_x=0.5,
                        position_y=0.8,
                    ),
                )
            ]
        )
        export_to_ass(track, self.output_ass)
        content = self.output_ass.read_text(encoding="utf-8")

        # Must use Boxed style
        self.assertIn(",Boxed,", content)
        # Check border padding tag
        self.assertIn("\\bord16.0", content)
        # Check pos coordinates shifted by offset: 540 + 12 = 552, 1536 - 8 = 1528
        self.assertIn("\\pos(552,1528)", content)

    def test_in_animations_fade_pop_slide(self):
        # 1. Fade
        track_fade = CaptionTrack(
            groups=[
                CaptionGroup(
                    id="g1", start=0.0, end=2.0,
                    words=[Word(id="w1", text="Fade Text", start=0.0, end=2.0)],
                    animation=AnimationConfig(
                        enter={"type": "fade", "duration_ms": 300}
                    ),
                )
            ]
        )
        export_to_ass(track_fade, self.output_ass)
        content_fade = self.output_ass.read_text(encoding="utf-8")
        self.assertIn(r"\fad(300,0)", content_fade)

        # 2. Pop
        track_pop = CaptionTrack(
            groups=[
                CaptionGroup(
                    id="g2", start=0.0, end=2.0,
                    words=[Word(id="w1", text="Pop Text", start=0.0, end=2.0)],
                    animation=AnimationConfig(
                        enter={"type": "pop", "duration_ms": 250}
                    ),
                )
            ]
        )
        export_to_ass(track_pop, self.output_ass)
        content_pop = self.output_ass.read_text(encoding="utf-8")
        self.assertIn(r"\fscx", content_pop)
        self.assertIn(r"\t(0,125,", content_pop)

        # 3. Slide Up
        track_slide = CaptionTrack(
            groups=[
                CaptionGroup(
                    id="g3", start=0.0, end=2.0,
                    words=[Word(id="w1", text="Slide Text", start=0.0, end=2.0)],
                    animation=AnimationConfig(
                        enter={"type": "slide_up", "duration_ms": 220}
                    ),
                )
            ]
        )
        export_to_ass(track_slide, self.output_ass)
        content_slide = self.output_ass.read_text(encoding="utf-8")
        self.assertIn(r"\move(", content_slide)
        self.assertIn(r"\fad(220,0)", content_slide)

    def test_out_animations_scale_down_slide_down(self):
        # Scale down
        track_scale = CaptionTrack(
            groups=[
                CaptionGroup(
                    id="g1", start=0.0, end=2.0,
                    words=[Word(id="w1", text="Exit Scale", start=0.0, end=2.0)],
                    animation=AnimationConfig(
                        exit={"type": "scale_down", "duration_ms": 200}
                    ),
                )
            ]
        )
        export_to_ass(track_scale, self.output_ass)
        content_scale = self.output_ass.read_text(encoding="utf-8")
        self.assertIn(r"\t(1800,2000,\fscx40\fscy40\alpha&HFF&)", content_scale)

        # Slide down
        track_slide = CaptionTrack(
            groups=[
                CaptionGroup(
                    id="g2", start=0.0, end=2.0,
                    words=[Word(id="w1", text="Exit Slide", start=0.0, end=2.0)],
                    animation=AnimationConfig(
                        exit={"type": "slide_down", "duration_ms": 150}
                    ),
                )
            ]
        )
        export_to_ass(track_slide, self.output_ass)
        content_slide = self.output_ass.read_text(encoding="utf-8")
        self.assertIn(r"\move(", content_slide)
        self.assertIn(r",1850,2000)", content_slide)

    def test_loop_animations_pulse_wiggle(self):
        # Pulse
        track_pulse = CaptionTrack(
            groups=[
                CaptionGroup(
                    id="g1", start=0.0, end=2.0,
                    words=[Word(id="w1", text="Pulse Loop", start=0.0, end=2.0)],
                    animation=AnimationConfig(
                        loop={"type": "pulse", "speed_ms": 800}
                    ),
                )
            ]
        )
        export_to_ass(track_pulse, self.output_ass)
        content_pulse = self.output_ass.read_text(encoding="utf-8")
        self.assertIn(r"\t(0,400,\fscx115\fscy115)", content_pulse)

        # Wiggle
        track_wiggle = CaptionTrack(
            groups=[
                CaptionGroup(
                    id="g2", start=0.0, end=2.0,
                    words=[Word(id="w1", text="Wiggle Loop", start=0.0, end=2.0)],
                    animation=AnimationConfig(
                        loop={"type": "wiggle", "speed_ms": 1200}
                    ),
                )
            ]
        )
        export_to_ass(track_wiggle, self.output_ass)
        content_wiggle = self.output_ass.read_text(encoding="utf-8")
        self.assertIn(r"\frz-6", content_wiggle)
        self.assertIn(r"\frz6", content_wiggle)


if __name__ == "__main__":
    unittest.main()
