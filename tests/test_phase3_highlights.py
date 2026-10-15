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
        test_ass = Path("storage/processing/test_track.ass")
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


if __name__ == "__main__":
    unittest.main()
