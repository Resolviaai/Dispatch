"""Shared-fixture tests for the pure Dispatch EDL v1 timeline engine."""
import json
import unittest
from pathlib import Path

from dispatch.segments import (
    SegmentError,
    concat_to_source,
    delete_piece,
    intersect_source_interval,
    kept_duration,
    normalize_edl_document,
    normalize_ranges,
    source_to_concat,
    split_at,
    timeline_interval_to_source,
    trim_edge,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "segments_v1.json"


def assert_ranges(test: unittest.TestCase, actual, expected):
    test.assertEqual(len(actual), len(expected))
    for actual_pair, expected_pair in zip(actual, expected):
        test.assertAlmostEqual(actual_pair[0], expected_pair[0], places=8)
        test.assertAlmostEqual(actual_pair[1], expected_pair[1], places=8)


class SegmentTimelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with FIXTURE_PATH.open("r", encoding="utf-8") as handle:
            cls.fixtures = json.load(handle)

    def test_shared_split_trim_and_delete_fixtures(self):
        source_duration = self.fixtures["source_duration"]
        cases = self.fixtures["cases"]

        for name in ("split", "split_multi_range"):
            case = cases[name]
            with self.subTest(name=name):
                result = split_at(case["ranges"], case["source_time"], source_duration)
                assert_ranges(self, result, case["expected"])
                self.assertAlmostEqual(
                    kept_duration(result), kept_duration(case["ranges"]), places=8
                )

        for name in (
            "trim_start",
            "trim_expand_into_gap",
            "trim_end",
            "trim_end_expand_into_gap",
        ):
            case = cases[name]
            with self.subTest(name=name):
                result = trim_edge(
                    case["ranges"], case["index"], case["edge"],
                    case["new_source_time"], source_duration,
                )
                assert_ranges(self, result, case["expected"])

        case = cases["delete_middle"]
        result = delete_piece(case["ranges"], case["index"], source_duration)
        assert_ranges(self, result, case["expected"])
        self.assertEqual(len(result), 2, "delete must preserve separate surviving blocks")

    def test_adjacent_ranges_are_not_merged(self):
        case = self.fixtures["cases"]["adjacency_preserved"]
        result = normalize_ranges(case["ranges"], self.fixtures["source_duration"])
        self.assertEqual(len(result), case["expected_count"])
        self.assertAlmostEqual(kept_duration(result), case["expected_duration"], places=8)

    def test_shared_point_mapping_fixtures(self):
        case = self.fixtures["cases"]["point_mapping"]
        for item in case["source_to_concat"]:
            with self.subTest(source_to_concat=item):
                actual = source_to_concat(
                    case["ranges"], item["source"], self.fixtures["source_duration"]
                )
                self.assertEqual(actual is None, item["expected"] is None)
                if actual is not None:
                    self.assertAlmostEqual(actual, item["expected"], places=8)

        for item in case["concat_to_source"]:
            with self.subTest(concat_to_source=item):
                actual = concat_to_source(
                    case["ranges"], item["timeline"], self.fixtures["source_duration"]
                )
                self.assertAlmostEqual(actual, item["expected"], places=8)

    def test_layer_intervals_fragment_across_removed_gaps(self):
        case = self.fixtures["cases"]["source_interval_fragments"]
        actual = intersect_source_interval(
            case["ranges"], case["source_start"], case["source_end"],
            self.fixtures["source_duration"],
        )
        self.assertEqual(len(actual), len(case["expected"]))
        for actual_fragment, expected_fragment in zip(actual, case["expected"]):
            for field, expected in expected_fragment.items():
                self.assertAlmostEqual(actual_fragment[field], expected, places=8)

    def test_timeline_interval_maps_back_to_multiple_source_fragments(self):
        case = self.fixtures["cases"]["timeline_interval_fragments"]
        actual = timeline_interval_to_source(
            case["ranges"], case["timeline_start"], case["timeline_end"],
            self.fixtures["source_duration"],
        )
        self.assertEqual(len(actual), len(case["expected"]))
        for actual_fragment, expected_fragment in zip(actual, case["expected"]):
            for field, expected in expected_fragment.items():
                self.assertAlmostEqual(actual_fragment[field], expected, places=8)

    def test_edl_document_validation_preserves_metadata(self):
        case = self.fixtures["cases"]["edl_document"]
        result = normalize_edl_document(case["document"])
        self.assertEqual(len(result["ranges"]), case["expected_range_count"])
        self.assertAlmostEqual(kept_duration(result["ranges"]), case["expected_duration"])
        self.assertEqual(result["settings"], case["document"]["settings"])
        self.assertEqual(result["provenance"], case["document"]["provenance"])

    def test_invalid_shared_range_fixtures_are_rejected(self):
        for case in self.fixtures["invalid_ranges"]:
            with self.subTest(name=case["name"]):
                with self.assertRaises(SegmentError):
                    normalize_ranges(case["ranges"], case["source_duration"])

    def test_split_rejects_gap_and_too_short_fragments(self):
        ranges = [[10.0, 20.0]]
        for point in (9.0, 10.0, 10.05, 19.95, 20.0, 22.0):
            with self.subTest(point=point):
                with self.assertRaises(SegmentError):
                    split_at(ranges, point, 100.0)

    def test_trim_cannot_overlap_neighbor_or_create_short_piece(self):
        ranges = [[10.0, 20.0], [25.0, 35.0]]
        with self.assertRaises(SegmentError):
            trim_edge(ranges, 0, "end", 25.0, 100.0)
        with self.assertRaises(SegmentError):
            trim_edge(ranges, 1, "start", 19.0, 100.0)
        with self.assertRaises(SegmentError):
            trim_edge(ranges, 0, "start", 19.95, 100.0)

    def test_final_piece_cannot_be_deleted_as_a_segment(self):
        with self.assertRaises(SegmentError):
            delete_piece([[10.0, 20.0]], 0, 100.0)

    def test_source_bound_epsilon_is_clamped_not_retained(self):
        result = normalize_ranges([[-0.005, 5.005]], 5.0)
        assert_ranges(self, result, [[0.0, 5.0]])

    def test_edl_rejects_unsupported_version_and_missing_source(self):
        with self.assertRaises(SegmentError):
            normalize_edl_document({
                "v": 2, "source_asset_id": "asset", "source_duration": 10,
                "ranges": [[0, 10]],
            })
        with self.assertRaises(SegmentError):
            normalize_edl_document({
                "v": 1, "source_asset_id": "", "source_duration": 10,
                "ranges": [[0, 10]],
            })


if __name__ == "__main__":
    unittest.main()
