import unittest

from dispatch.captions.range_sync import rebase_caption_track


def _gap_words_for_big_gap():
    """Nine evenly-spaced gap-local words (no pauses) spanning the (12s, 21s) gap."""
    words = []
    t = 0.0
    for i in range(9):
        words.append({"word": f"g{i}", "start": round(t, 3), "end": round(t + 0.3, 3), "is_exact": True})
        t += 0.35
    return words


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

    def _saved_track_with_default_style(self):
        return {
            "default_style": {"primary_color": "#112233"},
            "default_layout": {"position_y": 0.6},
            "groups": [{
                "id": "saved-group", "type": "caption", "start": 0.2, "end": 1.2,
                "words": [
                    {"id": "w1", "text": "edited", "start": 0.2, "end": 0.5},
                    {"id": "w2", "text": "word", "start": 1.0, "end": 1.2},
                ],
            }],
        }

    def _gap_transcribe_fn(self, gap_start, gap_end):
        if gap_end - gap_start < 2.0:
            return None
        return {"words": _gap_words_for_big_gap()}

    def test_gap_transcription_produces_standard_multi_word_cards(self):
        """Audit 2026-11: a long gap must never collapse into one wall card."""
        saved = self._saved_track_with_default_style()

        def gap_fn(gap_start, gap_end):
            if gap_end - gap_start < 2.0:
                return None
            return {"words": [
                {"word": f"g{i}", "start": round(i * 0.35, 3), "end": round(i * 0.35 + 0.3, 3), "is_exact": True}
                for i in range(9)
            ]}

        result, warning = rebase_caption_track(
            saved, 10.0, 9.0, 21.0, saved_source_end=12.0, gap_transcribe_fn=gap_fn,
        )
        self.assertFalse(warning)
        gap_groups = [g for g in result["groups"] if str(g.get("id", "")).startswith("gap_")]
        self.assertEqual(len(gap_groups), 3, "nine consecutive gap words must group into 3-word cards")
        for grp in gap_groups:
            self.assertEqual(len(grp["words"]), 3)
            self.assertTrue(grp.get("has_word_timing"))
            self.assertEqual(grp.get("timing_mode"), "word")
        # Cards stay sequential and gap-ordered.
        starts = [g["start"] for g in gap_groups]
        self.assertEqual(starts, sorted(starts))
        # Ids are deterministic per gap (stable across saves and syncs).
        self.assertEqual([g["id"] for g in gap_groups], [
            "gap_12000_21000_1", "gap_12000_21000_2", "gap_12000_21000_3",
        ])

    def test_gap_cards_inherit_track_default_style_and_stable_word_ids(self):
        saved = self._saved_track_with_default_style()
        result, _warning = rebase_caption_track(
            saved, 10.0, 9.0, 21.0, saved_source_end=12.0, gap_transcribe_fn=self._gap_transcribe_fn,
        )
        gap_groups = [g for g in result["groups"] if str(g.get("id", "")).startswith("gap_")]
        self.assertTrue(gap_groups)
        for grp in gap_groups:
            self.assertEqual(grp["style"]["primary_color"], "#112233", "gap cards inherit the track default style")
            self.assertEqual(grp["layout"]["position_y"], 0.6)

        # Word ids are deterministic and anchored to SOURCE time, so the same
        # source gap always reconciles to the same ids (phone <-> PC dedup).
        from dispatch.captions.range_sync import _normalize_gap_transcription_result, compute_word_id
        norm_a = _normalize_gap_transcription_result({"words": _gap_words_for_big_gap()}, 12.0, 21.0, 9.0)
        norm_b = _normalize_gap_transcription_result({"words": _gap_words_for_big_gap()}, 12.0, 21.0, 9.0)
        ids_a = [w["word_id"] for g in norm_a["groups"] for w in g["words"]]
        ids_b = [w["word_id"] for g in norm_b["groups"] for w in g["words"]]
        self.assertEqual(ids_a, ids_b)
        self.assertTrue(ids_a)
        self.assertEqual(len(ids_a), len(set(ids_a)))
        first_word = norm_a["groups"][0]["words"][0]
        self.assertEqual(
            first_word["word_id"],
            compute_word_id(
                {"start": 9.0 + first_word["start"], "end": 9.0 + first_word["end"], "text": first_word["text"]},
                0.0,
            ),
        )
        # The rebased track carries exactly those stable ids.
        rebased_ids = [w["word_id"] for g in gap_groups for w in g["words"]]
        self.assertEqual(rebased_ids, ids_a)

    def test_gap_transcription_phrase_timing_keeps_legacy_warning_behavior(self):
        """Preserved legacy semantics: phrase-timed (non-exact) gap words raise
        the timing warning and are never fabricated into word-timed cards."""
        saved = self._saved_track_with_default_style()

        def gap_fn(gap_start, gap_end):
            if gap_end - gap_start < 2.0:
                return None
            return {"words": [
                {"word": "approx", "start": 0.0, "end": 1.0, "is_exact": False},
                {"word": "words", "start": 1.0, "end": 2.0, "is_exact": False},
            ]}

        result, warning = rebase_caption_track(
            saved, 10.0, 9.0, 21.0, saved_source_end=12.0, gap_transcribe_fn=gap_fn,
        )
        gap_groups = [g for g in result["groups"] if str(g.get("id", "")).startswith("gap_")]
        self.assertEqual(gap_groups, [], "phrase timing cannot be subdivided into word cards")
        self.assertTrue(warning, "phrase-timed gap fill flags the timing warning like transcript phrases")


if __name__ == "__main__":
    unittest.main()
