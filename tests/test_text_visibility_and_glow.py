"""Regression Test Suite for Dispatch Text Visibility, Glow Rendering, and Animation Model.
Verifies:
1. Glow ON: emits background Gaussian glow on Layer 0 and crisp text on Layer 1.
2. Glow OFF: emits single crisp Layer 0 line; no glow emitted.
3. Glow preserves independent outline stroke and drop shadow.
4. AnimationConfig round-trip serialization (In, Out, Loop, Active-Word).
5. Phrase-level vs word-level ASS glow rendering parity.
"""
import unittest
import tempfile
from pathlib import Path

from dispatch.captions.model import (
    CaptionTrack, CaptionGroup, Word, StyleConfig, LayoutConfig, AnimationConfig
)
from dispatch.captions.exporter import export_to_ass


class TestTextVisibilityAndGlow(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ass_path = Path(self.temp_dir.name) / "test.ass"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_glow_on_emits_layer0_blur_and_layer1_text(self):
        """When glow_enabled is True, exporter must emit Layer 0 Gaussian blur and Layer 1 crisp foreground."""
        style = StyleConfig(
            font_family="Montserrat",
            font_size=44,
            primary_color="#FFFFFF",
            highlight_color="#FFE600",
            glow_enabled=True,
            glow_color="#00F0FF",
            glow_blur=16.0,
            outline_enabled=True,
            outline_color="#000000",
            outline_width=3.0,
            shadow_enabled=True,
            shadow_color="#111111",
            shadow_blur=6.0,
        )

        words = [
            Word(id="w1", text="NEON", start=1.0, end=1.5, is_exact=True),
            Word(id="w2", text="GLOW", start=1.5, end=2.0, is_exact=True),
        ]
        group = CaptionGroup(id="g1", start=1.0, end=2.0, words=words, style=style)
        track = CaptionTrack(
            id="trk1",
            clip_id="clip_glow",
            default_style=style,
            default_layout=LayoutConfig(),
            default_animation=AnimationConfig(type="pop"),
            groups=[group],
        )

        export_to_ass(track, self.ass_path)
        content = self.ass_path.read_text(encoding="utf-8")

        # Layer 0 must contain blur and glow color
        self.assertIn("Dialogue: 0,", content)
        self.assertIn(r"\blur16", content)
        self.assertIn(r"\alpha&H35&", content)

        # Layer 1 must contain crisp foreground text
        self.assertIn("Dialogue: 1,", content)
        # Outline and shadow must be preserved
        self.assertIn(r"\bord3.0", content)
        self.assertIn(r"\shad", content)

    def test_glow_off_emits_no_glow_layer(self):
        """When glow_enabled is False, only standard Dialogue line is emitted without glow tags."""
        style = StyleConfig(
            font_family="Montserrat",
            font_size=44,
            primary_color="#FFFFFF",
            glow_enabled=False,
            outline_enabled=True,
            outline_color="#000000",
            outline_width=2.0,
            shadow_enabled=False,
        )

        words = [
            Word(id="w1", text="CLEAN", start=0.5, end=1.5, is_exact=True),
        ]
        group = CaptionGroup(id="g1", start=0.5, end=1.5, words=words, style=style)
        track = CaptionTrack(
            id="trk2",
            clip_id="clip_noglow",
            default_style=style,
            default_layout=LayoutConfig(),
            default_animation=AnimationConfig(),
            groups=[group],
        )

        export_to_ass(track, self.ass_path)
        content = self.ass_path.read_text(encoding="utf-8")

        # Must not contain Layer 1 or alpha&H35& glow tags
        self.assertNotIn("Dialogue: 1,", content)
        self.assertNotIn(r"\alpha&H35&", content)
        # Dialogue: 0 must exist
        self.assertIn("Dialogue: 0,", content)
        self.assertIn("CLEAN", content)

    def test_phrase_level_glow_parity(self):
        """Phrase-only captions (timing_mode='phrase') render glow on Layer 0 and text on Layer 1."""
        style = StyleConfig(
            font_family="Impact",
            font_size=50,
            primary_color="#FFFF00",
            glow_enabled=True,
            glow_color="#FF0055",
            glow_blur=14.0,
        )

        words = [
            Word(id="w1", text="PHRASE", start=2.0, end=4.0, is_exact=False),
            Word(id="w2", text="ONLY", start=2.0, end=4.0, is_exact=False),
        ]
        group = CaptionGroup(
            id="g_phrase",
            start=2.0,
            end=4.0,
            words=words,
            style=style,
            timing_mode="phrase",
            has_word_timing=False,
        )
        track = CaptionTrack(
            id="trk3",
            clip_id="clip_phrase",
            default_style=style,
            default_layout=LayoutConfig(),
            default_animation=AnimationConfig(),
            groups=[group],
        )

        export_to_ass(track, self.ass_path)
        content = self.ass_path.read_text(encoding="utf-8")

        self.assertIn("Dialogue: 0,0:00:02.00,0:00:04.00,Default,,0,0,0,", content)
        self.assertIn("Dialogue: 1,0:00:02.00,0:00:04.00,Default,,0,0,0,", content)
        self.assertIn(r"\blur14", content)
        self.assertIn("PHRASE ONLY", content)

    def test_animation_config_serialization(self):
        """AnimationConfig round-trip preserves in/out/loop/active_word configuration."""
        anim_data = {
            "type": "pop",
            "duration_ms": 120,
            "active_scale": 1.22,
            "enter": {"type": "slide_up", "duration_ms": 250},
            "exit": {"type": "fade", "duration_ms": 180},
            "loop": {"type": "pulse", "speed_ms": 900},
            "active_word": {"type": "pop", "active_scale": 1.25, "duration_ms": 110},
        }

        cfg = AnimationConfig.from_dict(anim_data)
        self.assertEqual(cfg.enter_type, "slide_up")
        self.assertEqual(cfg.enter_duration_ms, 250)
        self.assertEqual(cfg.exit_type, "fade")
        self.assertEqual(cfg.exit_duration_ms, 180)
        self.assertEqual(cfg.loop_type, "pulse")
        self.assertEqual(cfg.loop_speed_ms, 900)
        self.assertEqual(cfg.active_word.get("active_scale"), 1.25)


if __name__ == "__main__":
    unittest.main()
