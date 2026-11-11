"""Golden fixture contract tests for Dispatch canonical millisecond rounding and fingerprinting."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from dispatch.segments import (
    round_ms,
    canonical_json_dumps,
    canonical_media_spans,
    canonical_spans_str,
    compute_media_fingerprint,
    compute_render_fingerprint,
    fragment_layer_interval,
    c2_to_c3_time,
    c3_to_c2_time,
)


class TestFingerprints(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixtures_path = Path(__file__).parent / "fixtures" / "fingerprints_v1.json"
        with fixtures_path.open("r", encoding="utf-8") as f:
            cls.fixtures = json.load(f)

    def test_round_ms_golden_vectors(self) -> None:
        for item in self.fixtures["round_ms_vectors"]:
            val = item["input"]
            expected = item["expected"]
            actual = round_ms(val)
            self.assertAlmostEqual(actual, expected, places=3, msg=f"round_ms({val}) != {expected}")

    def test_canonical_spans_golden_vectors(self) -> None:
        for item in self.fixtures["canonical_spans_vectors"]:
            ranges = [tuple(pair) for pair in item["ranges"]]
            expected_spans = [tuple(pair) for pair in item["expected_spans"]]
            expected_str = item["expected_str"]

            actual_spans = canonical_media_spans(ranges)
            self.assertEqual(actual_spans, expected_spans, f"{item['name']} spans mismatch")

            actual_str = canonical_spans_str(ranges)
            self.assertEqual(actual_str, expected_str, f"{item['name']} str mismatch")

    def test_fingerprint_golden_vectors_and_split_invariance(self) -> None:
        for item in self.fixtures["fingerprint_vectors"]:
            source_id = item["source_asset_id"]
            ranges = [tuple(pair) for pair in item["ranges"]]
            layout = item["layout_mode"]
            crop = item["crop_anchor_x"]
            layers = item["layers"]

            actual_media_fp = compute_media_fingerprint(source_id, ranges)
            self.assertEqual(actual_media_fp, item["expected_media_fingerprint"], f"{item['name']} media_fp mismatch")

            actual_render_fp = compute_render_fingerprint(actual_media_fp, layout, crop, layers)
            self.assertEqual(actual_render_fp, item["expected_render_fingerprint"], f"{item['name']} render_fp mismatch")

    def test_layer_fragmentation_golden_vectors(self) -> None:
        for item in self.fixtures["layer_fragmentation_vectors"]:
            layer_int = tuple(item["layer_interval"])
            gap = tuple(item["deleted_gap"])
            expected = [tuple(pair) for pair in item["expected"]]

            actual = fragment_layer_interval(layer_int, gap)
            self.assertEqual(actual, expected, f"{item['name']} fragmentation mismatch")

    def test_c2_c3_time_mapping(self) -> None:
        window_start = 700.5
        proxy_time = 14.2
        c3_time = c2_to_c3_time(proxy_time, window_start)
        self.assertEqual(c3_time, 714.7)

        mapped_proxy = c3_to_c2_time(c3_time, window_start)
        self.assertEqual(mapped_proxy, proxy_time)

    def test_rfc8785_fingerprint_golden_vectors(self) -> None:
        for item in self.fixtures["rfc8785_fingerprint_vectors"]:
            with self.subTest(name=item["name"]):
                self.assertEqual(
                    canonical_json_dumps(item["layers"]),
                    item["expected_canonical_layers_json"],
                )
                self.assertEqual(
                    canonical_json_dumps(item["settings"]),
                    item["expected_canonical_settings_json"],
                )
                actual_media_fp = compute_media_fingerprint(item["source_asset_id"], item["ranges"])
                self.assertEqual(actual_media_fp, item["expected_media_fingerprint"])
                actual_render_fp = compute_render_fingerprint(
                    actual_media_fp,
                    item["layout_mode"],
                    item["crop_anchor_x"],
                    item["layers"],
                    item["settings"],
                )
                self.assertEqual(actual_render_fp, item["expected_render_fingerprint"])


if __name__ == "__main__":
    unittest.main()
