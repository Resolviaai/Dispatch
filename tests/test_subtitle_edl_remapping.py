import unittest
import tempfile
from pathlib import Path

from dispatch.captions.model import CaptionTrack, CaptionGroup, Word, StyleConfig, LayoutConfig, AnimationConfig
from dispatch.captions.exporter import export_to_ass, remap_caption_track_to_timeline


class TestSubtitleEDLRemapping(unittest.TestCase):

    def setUp(self):
        # Base track with 3 caption groups:
        # Group 1: inside piece 0 (clip-relative 1.0s - 3.0s -> source 11.0s - 13.0s)
        # Group 2: inside deleted gap (clip-relative 7.0s - 9.0s -> source 17.0s - 19.0s)
        # Group 3: inside piece 1 (clip-relative 16.0s - 18.0s -> source 26.0s - 28.0s)
        w1 = Word(id="w1", text="PieceOne", start=1.0, end=2.5, is_exact=True)
        g1 = CaptionGroup(id="g1", start=1.0, end=2.5, words=[w1])

        w2 = Word(id="w2", text="DeletedGap", start=7.0, end=8.5, is_exact=True)
        g2 = CaptionGroup(id="g2", start=7.0, end=8.5, words=[w2])

        w3 = Word(id="w3", text="PieceTwo", start=16.0, end=17.5, is_exact=True)
        g3 = CaptionGroup(id="g3", start=16.0, end=17.5, words=[w3])

        self.track = CaptionTrack(
            id="track_test",
            clip_id="c_test",
            groups=[g1, g2, g3],
        )

    def test_multi_segment_cut_drops_deleted_words_and_shifts_surviving_words(self):
        # Segments: [10.0, 15.0] (dur 5.0) and [25.0, 30.0] (dur 5.0)
        # Total kept duration = 10.0s
        spans = [(10.0, 15.0), (25.0, 30.0)]
        clip_start = 10.0

        remapped = remap_caption_track_to_timeline(self.track, spans, clip_start=clip_start)

        self.assertEqual(len(remapped.groups), 2, "Deleted gap group must be completely omitted")

        # Group 1 (PieceOne)
        rg1 = remapped.groups[0]
        self.assertEqual(rg1.id, "g1")
        self.assertEqual(rg1.words[0].text, "PieceOne")
        self.assertAlmostEqual(rg1.start, 1.0, places=2)
        self.assertAlmostEqual(rg1.end, 2.5, places=2)

        # Group 3 (PieceTwo) shifted:
        # Source time 26.0s is 1.0s into piece 1.
        # Piece 0 duration is 5.0s, so output time is 5.0 + 1.0 = 6.0s!
        rg2 = remapped.groups[1]
        self.assertEqual(rg2.id, "g3")
        self.assertEqual(rg2.words[0].text, "PieceTwo")
        self.assertAlmostEqual(rg2.start, 6.0, places=2)
        self.assertAlmostEqual(rg2.end, 7.5, places=2)

    def test_export_to_ass_dialogue_times_never_exceed_eff_duration(self):
        spans = [(10.0, 15.0), (25.0, 30.0)]
        clip_start = 10.0

        remapped = remap_caption_track_to_timeline(self.track, spans, clip_start=clip_start)

        with tempfile.TemporaryDirectory() as tmpdir:
            ass_path = Path(tmpdir) / "test_remap.ass"
            export_to_ass(remapped, ass_path)
            content = ass_path.read_text(encoding="utf-8")

            dialogues = [line for line in content.splitlines() if line.startswith("Dialogue:")]
            self.assertTrue(len(dialogues) > 0)
            for d in dialogues:
                parts = d.split(",")
                end_str = parts[2]
                h, m, s = end_str.split(":")
                end_sec = int(h) * 3600 + int(m) * 60 + float(s)
                self.assertLessEqual(end_sec, 10.0, f"Dialogue end time {end_sec}s exceeds effective duration 10.0s")

    def test_single_segment_identity_remapping(self):
        spans = [(10.0, 30.0)]
        clip_start = 10.0

        remapped = remap_caption_track_to_timeline(self.track, spans, clip_start=clip_start)
        self.assertEqual(len(remapped.groups), 3)
        self.assertAlmostEqual(remapped.groups[0].start, 1.0, places=2)
        self.assertAlmostEqual(remapped.groups[1].start, 7.0, places=2)
        self.assertAlmostEqual(remapped.groups[2].start, 16.0, places=2)


    def test_single_range_leading_trim_uses_immutable_source_origin(self):
        # Caption timestamps remain relative to the original source origin (10s)
        # even when the current EDL contains only one range beginning at 12s.
        remapped = remap_caption_track_to_timeline(
            self.track,
            [(12.0, 30.0)],
            clip_start=10.0,
            source_duration=40.0,
        )
        self.assertEqual([group.words[0].text for group in remapped.groups], ["PieceOne", "DeletedGap", "PieceTwo"])
        self.assertAlmostEqual(remapped.groups[0].start, 0.0, places=3)
        self.assertAlmostEqual(remapped.groups[0].end, 0.5, places=3)
        self.assertAlmostEqual(remapped.groups[1].start, 5.0, places=3)
        self.assertAlmostEqual(remapped.groups[2].start, 14.0, places=3)

    def test_three_pieces_two_gaps_mapping(self):
        # Spans: [0.0, 5.0], [10.0, 15.0], [20.0, 25.0] -> Total = 15.0s
        spans = [(0.0, 5.0), (10.0, 15.0), (20.0, 25.0)]
        w_p0 = Word(id="wp0", text="P0", start=2.0, end=3.0)
        g_p0 = CaptionGroup(id="gp0", start=2.0, end=3.0, words=[w_p0])

        w_gap1 = Word(id="wg1", text="G1", start=7.0, end=8.0)
        g_gap1 = CaptionGroup(id="gg1", start=7.0, end=8.0, words=[w_gap1])

        w_p1 = Word(id="wp1", text="P1", start=12.0, end=13.0)
        g_p1 = CaptionGroup(id="gp1", start=12.0, end=13.0, words=[w_p1])

        w_gap2 = Word(id="wg2", text="G2", start=17.0, end=18.0)
        g_gap2 = CaptionGroup(id="gg2", start=17.0, end=18.0, words=[w_gap2])

        w_p2 = Word(id="wp2", text="P2", start=22.0, end=23.0)
        g_p2 = CaptionGroup(id="gp2", start=22.0, end=23.0, words=[w_p2])

        track = CaptionTrack(id="tr3", groups=[g_p0, g_gap1, g_p1, g_gap2, g_p2])
        remapped = remap_caption_track_to_timeline(track, spans, clip_start=0.0)

        self.assertEqual(len(remapped.groups), 3)
        self.assertEqual([g.words[0].text for g in remapped.groups], ["P0", "P1", "P2"])
        # P0: [2.0, 3.0]
        self.assertAlmostEqual(remapped.groups[0].start, 2.0, places=2)
        # P1: 5.0 (p0 duration) + (12.0 - 10.0) = 7.0s
        self.assertAlmostEqual(remapped.groups[1].start, 7.0, places=2)
        self.assertAlmostEqual(remapped.groups[1].end, 8.0, places=2)
        # P2: 10.0 (p0 + p1 duration) + (22.0 - 20.0) = 12.0s
        self.assertAlmostEqual(remapped.groups[2].start, 12.0, places=2)
        self.assertAlmostEqual(remapped.groups[2].end, 13.0, places=2)


if __name__ == "__main__":
    unittest.main()
