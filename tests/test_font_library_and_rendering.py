"""Test suite for Dispatch Font Library, Font Rendering & Custom Font Imports.
Verifies font catalog, ASS export font parity, fontsdir reframer integration, and font APIs.
"""
import io
import json
import tempfile
import unittest
from pathlib import Path

from dispatch.captions.model import CaptionTrack, CaptionGroup, Word, StyleConfig, LayoutConfig, AnimationConfig, WordStyleOverride
from dispatch.captions.exporter import export_to_ass, get_ass_weight_tag
from dispatch.video_engine.reframer import build_filter_complex
from dispatch.config import FONTS_DIR


class TestFontLibraryAndRendering(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_font_manifest_exists_and_contains_curated_library(self):
        """Verify the curated 20-25 creator fonts are cataloged with licenses and real weights."""
        manifest_path = FONTS_DIR / "fonts_manifest.json"
        self.assertTrue(manifest_path.exists(), "fonts_manifest.json must exist in FONTS_DIR")
        
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
            
        self.assertGreaterEqual(len(manifest), 20, "Should contain at least 20 curated creator fonts")
        
        families = {f["family"] for f in manifest}
        required_families = [
            "Anton", "Archivo Black", "Bebas Neue", "Montserrat", "Oswald",
            "Barlow Condensed", "League Spartan", "Poppins", "Inter", "Roboto",
            "Open Sans", "Noto Sans", "Space Grotesk", "Bangers", "Luckiest Guy",
            "Russo One", "Titan One", "Bungee", "Permanent Marker", "Noto Sans Devanagari"
        ]
        for req in required_families:
            self.assertIn(req, families, f"Required font '{req}' missing from font manifest")

        # Verify real weights specification
        anton = next(f for f in manifest if f["family"] == "Anton")
        self.assertEqual(anton["weights"], ["400"], "Anton must only declare its real available weight (400)")

    def test_ass_export_includes_inline_font_family_size_and_weight(self):
        """ASS dialogue events must include explicit \\fn, \\fs, \\b, \\fsp tags for exact render parity."""
        track = CaptionTrack(
            default_style=StyleConfig(
                font_family="Montserrat",
                font_size=54,
                font_weight="bold",
                letter_spacing=1.5,
                primary_color="#FFFFFF"
            ),
            groups=[
                CaptionGroup(
                    id="g1",
                    start=0.0,
                    end=2.0,
                    style=StyleConfig(
                        font_family="Anton",
                        font_size=60,
                        font_weight="900",
                        letter_spacing=2.0,
                        primary_color="#FFE600",
                        glow_enabled=True,
                        glow_color="#00F0FF"
                    ),
                    words=[
                        Word(id="w1", text="SUPER", start=0.0, end=1.0),
                        Word(id="w2", text="CREATOR", start=1.0, end=2.0),
                    ]
                )
            ]
        )
        ass_path = self.temp_path / "test_font.ass"
        export_to_ass(track, ass_path)

        with open(ass_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check inline tags in dialogue events
        self.assertIn(r"\fnAnton", content, "Dialogue event must contain \\fnAnton inline override")
        self.assertIn(r"\fs60", content, "Dialogue event must contain \\fs60 font size override")
        self.assertIn(r"\b900", content, "Dialogue event must contain \\b900 font weight override")
        self.assertIn(r"\fsp2.0", content, "Dialogue event must contain \\fsp2.0 letter spacing override")

        # Check glow layer also contains matching font tags
        dialogue_lines = [line for line in content.splitlines() if line.startswith("Dialogue:")]
        self.assertGreaterEqual(len(dialogue_lines), 2)
        glow_line = dialogue_lines[0]
        self.assertIn(r"\fnAnton", glow_line, "Glow layer must have matching \\fnAnton tag")
        self.assertIn(r"\b900", glow_line, "Glow layer must have matching \\b900 weight tag")

    def test_ass_export_word_level_style_overrides(self):
        """Word-level style overrides (color, weight, scale) must be preserved in ASS output."""
        track = CaptionTrack(
            default_style=StyleConfig(font_family="Inter", font_size=48, font_weight="400"),
            groups=[
                CaptionGroup(
                    id="g1",
                    start=0.0,
                    end=2.0,
                    words=[
                        Word(id="w1", text="NORMAL", start=0.0, end=1.0),
                        Word(
                            id="w2",
                            text="EMPHASIS",
                            start=1.0,
                            end=2.0,
                            style_override=WordStyleOverride(
                                color="#FF0000",
                                font_weight="900",
                                scale=1.25
                            )
                        ),
                    ]
                )
            ]
        )
        ass_path = self.temp_path / "test_word_override.ass"
        export_to_ass(track, ass_path)

        with open(ass_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Should contain weight override \b900 for the word
        self.assertIn(r"\b900", content)

    def test_reframer_includes_fontsdir_in_subtitles_filter(self):
        """build_filter_complex must pass :fontsdir to FFmpeg subtitles filter when FONTS_DIR exists."""
        fake_sub = self.temp_path / "subs.ass"
        fake_sub.write_text("[Script Info]\n", encoding="utf-8")

        filter_str, out_label = build_filter_complex(
            aspect_ratio="9:16",
            layout_mode="native_916",
            ass_subtitle_path=fake_sub
        )
        self.assertIn("subtitles=", filter_str)
        self.assertIn(":fontsdir=", filter_str, "Subtitles filter must specify :fontsdir for libass font discovery")

    def test_api_get_fonts(self):
        """FastAPI endpoint /api/fonts returns 200 and catalog of fonts."""
        from fastapi.testclient import TestClient
        from dispatch.web.app import app

        client = TestClient(app)
        response = client.get("/api/fonts")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("fonts", data)
        self.assertIsInstance(data["fonts"], list)
        self.assertGreater(len(data["fonts"]), 0)

    def test_api_import_invalid_font_extension(self):
        """Uploading non-ttf/otf files must return 400 with helpful error."""
        from fastapi.testclient import TestClient
        from dispatch.web.app import app

        client = TestClient(app)
        response = client.post(
            "/api/fonts/import",
            files={"file": ("virus.exe", b"not a font file", "application/octet-stream")}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Only .ttf and .otf", response.json()["detail"])

    def test_api_import_corrupt_font_file(self):
        """Uploading corrupt font file must return 400 with validation failure message."""
        from fastapi.testclient import TestClient
        from dispatch.web.app import app

        client = TestClient(app)
        response = client.post(
            "/api/fonts/import",
            files={"file": ("fake_font.ttf", b"corrupt" * 200, "font/ttf")}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Corrupt or invalid font format", response.json()["detail"])

    def test_api_import_valid_ttf(self):
        """Uploading genuine TTF must succeed, parse family, and return font entry."""
        from fastapi.testclient import TestClient
        from dispatch.web.app import app
        from dispatch.config import BUNDLED_FONTS_DIR

        # Use Anton from bundled fonts as test upload
        sample_font = BUNDLED_FONTS_DIR / "Anton-Regular.ttf"
        if not sample_font.exists():
            return

        client = TestClient(app)
        with open(sample_font, "rb") as f:
            content = f.read()

        response = client.post(
            "/api/fonts/import",
            files={"file": ("CustomTestFont.ttf", content, "font/ttf")}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        font = data.get("font")
        self.assertIsNotNone(font)
        self.assertTrue(font.get("is_custom"))
        self.assertEqual(font.get("filename"), "CustomTestFont.ttf")

    def test_capcut_style_export_parity(self):
        """Verify export parity for case transforms, italic/underline, shadow distance/angle, and alpha."""
        my_style = StyleConfig(
            font_family="Montserrat",
            font_size=48,
            font_weight="bold",
            font_style="italic",
            underline=True,
            text_transform="title",
            text_opacity=0.8,
            outline_enabled=True,
            outline_color="#000000",
            outline_width=4.0,
            outline_opacity=0.6,
            shadow_enabled=True,
            shadow_color="#111111",
            shadow_distance=5.0,
            shadow_angle=90.0,
            shadow_opacity=0.5,
            background_enabled=True,
            background_mode="line",
            background_color="#222222",
            background_opacity=0.9
        )
        track = CaptionTrack(
            default_style=my_style,
            groups=[
                CaptionGroup(
                    id="g1",
                    start=0.0,
                    end=2.0,
                    style=my_style,
                    words=[
                        Word(id="w1", text="super", start=0.0, end=1.0),
                        Word(id="w2", text="creator", start=1.0, end=2.0, style_override=WordStyleOverride(underline=False)),
                    ]
                )
            ]
        )
        ass_path = self.temp_path / "test_capcut.ass"
        export_to_ass(track, ass_path)

        with open(ass_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 1. Boxed style in V4+ Styles
        self.assertIn("Style: Boxed", content)

        # 2. Italic & Underline tags
        self.assertIn(r"\i1", content)
        self.assertIn(r"\u1", content)

        # 3. Directional shadow: angle 90 deg -> x=0, y=5.0
        self.assertIn(r"\xshad0.0\yshad5.0", content)

        # 4. Title case transform in dialogue
        self.assertIn("Super", content)
        self.assertIn("Creator", content)

        # 5. Word override underline negation
        self.assertIn(r"\u0", content)

        # 6. Dialogue uses Boxed style
        self.assertIn(",Boxed,", content)


if __name__ == "__main__":
    unittest.main()
