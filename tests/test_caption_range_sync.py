import unittest

from dispatch.captions.range_sync import rebase_caption_track


class TestCaptionRangeSync(unittest.TestCase):
    def test_expansion_rebases_saved_words_once_and_adds_only_missing_transcript_words(self):
        saved = {
            "clip_id": "clip-a",
            "default_style": {"primary_color": "#ff0000"},
            "groups": [{
                "id": "saved-group", "type": "caption", "start": 0.2, "end": 1.2,
                "style": {"primary_color": "#00ff00"},
                "words": [
                    {"id": "w1", "text": "edited", "start": 0.2, "end": 0.5},
                    {"id": "w2", "text": "word", "start": 1.0, "end": 1.2},
                ],
            }],
        }
        transcript = {"groups": [{
            "id": "source-group", "type": "caption", "has_word_timing": True, "timing_mode": "word",
            "start": 0.2, "end": 2.8,
            "words": [
                {"id": "before", "text": "before", "start": 0.2, "end": 0.4},
                {"id": "original", "text": "original", "start": 1.2, "end": 1.5},
                {"id": "original2", "text": "word", "start": 2.0, "end": 2.2},
                {"id": "after", "text": "after", "start": 2.5, "end": 2.8},
            ],
        }]}

        result, warning = rebase_caption_track(saved, 10.0, 9.0, 12.0, transcript)
        self.assertFalse(warning)
        self.assertEqual(result["groups"][0]["id"], "source-group-range-1")
        self.assertEqual(result["groups"][1]["id"], "saved-group")
        self.assertEqual(result["groups"][1]["words"][0]["id"], "w1")
        self.assertEqual(result["groups"][1]["words"][0]["start"], 1.2)
        self.assertEqual(result["groups"][1]["style"]["primary_color"], "#00ff00")
        self.assertEqual([word["text"] for group in result["groups"] for word in group["words"]].count("original"), 0)
        self.assertEqual([word["text"] for group in result["groups"] for word in group["words"]].count("before"), 1)
        self.assertEqual([word["text"] for group in result["groups"] for word in group["words"]].count("after"), 1)

    def test_shrink_filters_outside_words_and_phrase_timing_is_flagged(self):
        saved = {
            "groups": [{
                "id": "g", "type": "caption", "start": 0, "end": 2,
                "has_word_timing": False, "timing_mode": "phrase",
                "words": [
                    {"id": "a", "text": "before", "start": 0.1, "end": 0.4},
                    {"id": "b", "text": "inside", "start": 1.2, "end": 1.4},
                ],
            }],
        }
        result, warning = rebase_caption_track(saved, 10.0, 11.0, 11.5)
        self.assertTrue(warning)
        self.assertEqual([w["text"] for g in result["groups"] for w in g["words"]], ["inside"])
        self.assertEqual(result["groups"][0]["words"][0]["start"], 0.2)
        outside, outside_warning = rebase_caption_track(saved, 10.0, 12.0, 13.0)
        self.assertFalse(outside_warning)
        self.assertEqual(outside["groups"], [])


if __name__ == "__main__":
    unittest.main()
