"""Tests for Phase 3: AI Highlights, Subtitle Engine Unification & FFmpeg Renderer."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from dispatch.ai_clips.highlight_finder import (
    phonetic_devanagari_to_roman,
    transliterate_tokens_to_hinglish,
    has_devanagari
)
from dispatch.captions.model import CaptionTrack, CaptionGroup, Word, StyleConfig
from dispatch.captions.exporter import export_to_ass
from dispatch.config import PROCESSING_DIR


class TestPhase3HighlightsAndCaptions(unittest.TestCase):

    def test_phonetic_devanagari_to_roman(self):
        """Verify phonetic transliteration fallback for common Hindi words."""
        self.assertTrue(has_devanagari("नमस्ते"))
        res_namaste = phonetic_devanagari_to_roman("नमस्ते")
        self.assertIn("namaste", res_namaste.lower())

        self.assertTrue(has_devanagari("आप"))
        res_aap = phonetic_devanagari_to_roman("आप")
        self.assertEqual(res_aap.lower(), "aap")

        self.assertTrue(has_devanagari("कैसे"))
        res_kaise = phonetic_devanagari_to_roman("कैसे")
        self.assertEqual(res_kaise.lower(), "kaise")

        self.assertTrue(has_devanagari("हैं"))
        res_hain = phonetic_devanagari_to_roman("हैं")
        self.assertIn("hai", res_hain.lower())

    def test_phonetic_devanagari_exhaustive_unicode_range(self):
        """Verify that every single Devanagari codepoint in U+0900..U+097F converts to Roman with 0 Devanagari characters remaining."""
        for code in range(0x0900, 0x0980):
            ch = chr(code)
            res = phonetic_devanagari_to_roman(ch)
            self.assertFalse(has_devanagari(res), f"U+{code:04X} ({ch}) still contains Devanagari after transliteration: {res}")

    def test_phonetic_devanagari_special_characters_and_combinations(self):
        """Verify Chandra O, Danda, Nukta combinations, Digits, Avagraha, Visarga, Anusvara, Chandrabindu."""
        # Chandra O (independent and matra)
        res_auto = phonetic_devanagari_to_roman("ऑटो")
        self.assertFalse(has_devanagari(res_auto))
        self.assertIn("o", res_auto.lower())

        res_doctor = phonetic_devanagari_to_roman("डॉक्टर")
        self.assertFalse(has_devanagari(res_doctor))
        self.assertIn("dokt", res_doctor.lower())

        # Danda (single and double)
        res_danda = phonetic_devanagari_to_roman("नमस्ते दोस्त।")
        self.assertFalse(has_devanagari(res_danda))
        self.assertTrue(res_danda.endswith("."))

        res_double_danda = phonetic_devanagari_to_roman("सब ठीक॥")
        self.assertFalse(has_devanagari(res_double_danda))
        self.assertTrue("." in res_double_danda)

        # Nukta decomposed and precomposed
        res_phone = phonetic_devanagari_to_roman("फ़ोन")  # \u092b\u093c\u094b\u0928
        self.assertFalse(has_devanagari(res_phone))
        self.assertIn("fon", res_phone.lower())

        res_zindagi = phonetic_devanagari_to_roman("ज़िंदगी")  # \u091c\u093c...
        self.assertFalse(has_devanagari(res_zindagi))
        self.assertIn("zin", res_zindagi.lower())

        # Digits ०-९
        res_digits = phonetic_devanagari_to_roman("वर्ष २०२६")
        self.assertFalse(has_devanagari(res_digits))
        self.assertIn("2026", res_digits)

        # Avagraha
        res_avagraha = phonetic_devanagari_to_roman("सोऽहम्")
        self.assertFalse(has_devanagari(res_avagraha))

        # Visarga
        res_visarga = phonetic_devanagari_to_roman("अतः")
        self.assertFalse(has_devanagari(res_visarga))
        self.assertIn("atah", res_visarga.lower())

    def test_transliterate_tokens_fallback_1to1(self):
        """Verify transliterate_tokens_to_hinglish preserves exact 1:1 list length and strips Devanagari."""
        tokens = ["नमस्ते", "dosto", "आप", "सब", "कैसे", "हैं"]
        # Mock Gemini to raise exception (offline mode)
        with patch("dispatch.ai_clips.highlight_finder.call_gemini_generate_content", side_effect=RuntimeError("Offline")):
            res = transliterate_tokens_to_hinglish(tokens)
            self.assertEqual(len(res), len(tokens))
            self.assertEqual(res[1], "dosto")  # English untouched
            # All Devanagari should be converted to ASCII/Roman
            for token in res:
                self.assertFalse(has_devanagari(token), f"Token {token} still contains Devanagari!")

    def test_caption_track_export_to_ass(self):
        """Verify CaptionTrack correctly exports to ASS format with dialog events."""
        track = CaptionTrack(
            clip_id="test_clip_1",
            default_style=StyleConfig(font_family="Impact", font_size=54),
            groups=[
                CaptionGroup(
                    start=1.0,
                    end=3.0,
                    words=[
                        Word(text="Hello", start=1.0, end=1.5),
                        Word(text="World", start=1.5, end=2.5)
                    ]
                )
            ]
        )
        test_ass = PROCESSING_DIR / "test_track.ass"
        test_ass.parent.mkdir(parents=True, exist_ok=True)
        try:
            out_path = export_to_ass(track, test_ass)
            self.assertTrue(out_path.exists())
            content = out_path.read_text(encoding="utf-8")
            self.assertIn("[Script Info]", content)
            self.assertIn("Impact", content)
            self.assertIn("Dialogue:", content)
            self.assertIn("HELLO", content)
            self.assertIn("WORLD", content)
        finally:
            if test_ass.exists():
                test_ass.unlink()

    def test_phonetic_devanagari_additional_nuktas(self):
        """Verify additional nuktas (\u0928\u093c, \u0930\u093c, \u0933\u093c) are properly transliterated."""
        res_nnna = phonetic_devanagari_to_roman("ऩ")
        self.assertFalse(has_devanagari(res_nnna))
        self.assertEqual(res_nnna.lower(), "n")

        res_rra = phonetic_devanagari_to_roman("ऱ")
        self.assertFalse(has_devanagari(res_rra))
        self.assertEqual(res_rra.lower(), "r")

        res_llla = phonetic_devanagari_to_roman("ऴ")
        self.assertFalse(has_devanagari(res_llla))
        self.assertEqual(res_llla.lower(), "l")

    def test_highlight_prompt_density_guidance(self):
        """Verify highlight prompt incorporates density and sweet spot guidance for long recordings."""
        from dispatch.ai_clips.prompt_templates import build_highlight_user_prompt
        prompt_10min = build_highlight_user_prompt("00:01 Test segment", total_duration=602.0)
        self.assertIn("RECORDING COVERAGE & DENSITY GUIDANCE", prompt_10min)
        self.assertIn("602 seconds (~10 minutes)", prompt_10min)
        self.assertIn("SWEET-SPOT DURATION: Target 30 to 75 seconds", prompt_10min)
        self.assertIn("9 distinct", prompt_10min)


if __name__ == "__main__":
    unittest.main()
