"""Cross-runtime Wave 2A contract tests for dispatch.lib.timeline_mapper & RFC-8785 canonicalization."""
from __future__ import annotations

import json
import math
from pathlib import Path
import unittest

from dispatch.lib.timeline_mapper import (
    CANONICALIZER_VERSION,
    ConcatToSourceOk,
    ConcatToSourceOutOfRange,
    EditWindowDescriptor,
    EdlSegment,
    PlaybackMapOk,
    PlaybackOutOfRange,
    RenderedClipDescriptor,
    SourceToConcatGap,
    SourceToConcatOk,
    SourceVideoDescriptor,
    TIMELINE_CONTRACT_VERSION,
    TimelineMapper,
    TimelineMapperError,
    WindowMapOk,
    WindowMapOutOfWindow,
    as_concat_time,
    as_proxy_time,
    as_source_time,
    c2_to_c3_time,
    c3_to_c2_time,
    canonical_json_dumps,
    compute_media_fingerprint,
    compute_render_fingerprint,
    concat_to_source,
    descriptor_from_dict,
    kept_duration,
    normalize_segments,
    playback_time_to_source,
    proxy_to_source,
    round_ms,
    source_to_concat,
    source_to_playback_time,
    source_to_proxy,
)
from dispatch.segments import SegmentError


FIXTURES_PATH = Path(__file__).parent / "fixtures" / "fingerprints_v1.json"


class TestWave2TimelineMapper(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with FIXTURES_PATH.open("r", encoding="utf-8") as f:
            cls.fixtures = json.load(f)

    def test_contract_versions_and_round_ms_vectors(self) -> None:
        self.assertEqual(TIMELINE_CONTRACT_VERSION, 1)
        self.assertEqual(CANONICALIZER_VERSION, "rfc8785-v1")
        for item in self.fixtures["round_ms_vectors"]:
            self.assertEqual(round_ms(item["input"]), item["expected"])
        self.assertEqual(math.copysign(1.0, round_ms(-0.0)), 1.0)
        self.assertEqual(math.copysign(1.0, round_ms(-0.0001)), 1.0)

    def test_source_to_concat_golden_vectors_no_silent_snap(self) -> None:
        vec = self.fixtures["timeline_mapper_vectors"]
        segments = vec["segments"]
        self.assertEqual(kept_duration(segments), vec["expected_kept_duration"])

        for case in vec["source_to_concat_cases"]:
            with self.subTest(case=case["name"]):
                res = source_to_concat(as_source_time(case["source_time"]), segments)
                exp = case["expected"]
                if exp["kind"] == "ok":
                    self.assertIsInstance(res, SourceToConcatOk)
                    assert isinstance(res, SourceToConcatOk)
                    self.assertEqual(res.time, exp["time"])
                    self.assertEqual(res.segment_index, exp["segment_index"])
                    self.assertFalse(res.snapped)
                else:
                    self.assertIsInstance(res, SourceToConcatGap)
                    assert isinstance(res, SourceToConcatGap)
                    self.assertEqual(res.reason, exp["reason"])
                    self.assertEqual(res.nearest_segment_start, exp["nearest_segment_start"])
                    self.assertEqual(res.nearest_segment_end, exp["nearest_segment_end"])
                    self.assertEqual(res.nearest_concat_time, exp["nearest_concat_time"])

    def test_source_to_concat_explicit_snap_opt_in(self) -> None:
        vec = self.fixtures["timeline_mapper_vectors"]
        segments = vec["segments"]
        for case in vec["snap_gap_opt_in_cases"]:
            with self.subTest(case=case["name"]):
                res = source_to_concat(
                    as_source_time(case["source_time"]),
                    segments,
                    snap_gap_to=case["snap_gap_to"],
                )
                self.assertIsInstance(res, SourceToConcatOk)
                assert isinstance(res, SourceToConcatOk)
                self.assertTrue(res.snapped)
                self.assertEqual(res.time, case["expected_time"])
                self.assertEqual(res.segment_index, case["expected_segment_index"])

        before_prev = source_to_concat(as_source_time(5.0), segments, snap_gap_to="previous")
        self.assertIsInstance(before_prev, SourceToConcatGap)
        after_next = source_to_concat(as_source_time(45.0), segments, snap_gap_to="next")
        self.assertIsInstance(after_next, SourceToConcatGap)

    def test_concat_to_source_golden_vectors_and_clamp(self) -> None:
        vec = self.fixtures["timeline_mapper_vectors"]
        segments = vec["segments"]

        for case in vec["concat_to_source_cases"]:
            with self.subTest(case=case["name"]):
                res = concat_to_source(as_concat_time(case["concat_time"]), segments)
                exp = case["expected"]
                if exp["kind"] == "ok":
                    self.assertIsInstance(res, ConcatToSourceOk)
                    assert isinstance(res, ConcatToSourceOk)
                    self.assertEqual(res.time, exp["time"])
                    self.assertEqual(res.segment_index, exp["segment_index"])
                    self.assertFalse(res.clamped)
                else:
                    self.assertIsInstance(res, ConcatToSourceOutOfRange)
                    assert isinstance(res, ConcatToSourceOutOfRange)
                    self.assertEqual(res.reason, exp["reason"])
                    self.assertEqual(res.clamped_time, exp["clamped_time"])

                    clamped = concat_to_source(as_concat_time(case["concat_time"]), segments, clamp=True)
                    self.assertIsInstance(clamped, ConcatToSourceOk)
                    assert isinstance(clamped, ConcatToSourceOk)
                    self.assertTrue(clamped.clamped)
                    self.assertEqual(clamped.time, exp["clamped_time"])

    def test_roundtrip_invertibility_invariants(self) -> None:
        vec = self.fixtures["timeline_mapper_vectors"]
        segments = vec["segments"]
        for c_pt in vec["roundtrip_concat_points"]:
            src_res = concat_to_source(as_concat_time(c_pt), segments)
            self.assertIsInstance(src_res, ConcatToSourceOk)
            assert isinstance(src_res, ConcatToSourceOk)
            back_res = source_to_concat(src_res.time, segments)
            self.assertIsInstance(back_res, SourceToConcatOk)
            assert isinstance(back_res, SourceToConcatOk)
            self.assertEqual(back_res.time, c_pt)

    def test_adjacent_segments_are_not_merged(self) -> None:
        segs = normalize_segments([[10.0, 20.0], [20.0, 30.0]])
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0], EdlSegment(id="seg-1", source_start=as_source_time(10.0), source_end=as_source_time(20.0)))
        self.assertEqual(segs[1], EdlSegment(id="seg-2", source_start=as_source_time(20.0), source_end=as_source_time(30.0)))
        seam = concat_to_source(as_concat_time(10.0), segs)
        self.assertIsInstance(seam, ConcatToSourceOk)
        assert isinstance(seam, ConcatToSourceOk)
        self.assertEqual(seam.segment_index, 1)
        self.assertEqual(seam.time, 20.0)

    def test_edit_window_and_c2_c3_bounds(self) -> None:
        ew = self.fixtures["timeline_mapper_vectors"]["edit_window_cases"]
        ws = as_source_time(ew["window_start"])
        we = as_source_time(ew["window_end"])

        for case in ew["source_to_proxy"]:
            res = source_to_proxy(as_source_time(case["source_time"]), ws, we)
            exp = case["expected"]
            if exp["kind"] == "ok":
                self.assertIsInstance(res, WindowMapOk)
                assert isinstance(res, WindowMapOk)
                self.assertEqual(res.time, exp["time"])
            else:
                self.assertIsInstance(res, WindowMapOutOfWindow)
                assert isinstance(res, WindowMapOutOfWindow)
                self.assertEqual(res.reason, exp["reason"])
                self.assertEqual(res.clamped_time, exp["clamped_time"])
                clamped = source_to_proxy(as_source_time(case["source_time"]), ws, we, clamp=True)
                self.assertIsInstance(clamped, WindowMapOk)
                assert isinstance(clamped, WindowMapOk)
                self.assertTrue(clamped.clamped)
                self.assertEqual(clamped.time, exp["clamped_time"])

        for case in ew["proxy_to_source"]:
            res = proxy_to_source(as_proxy_time(case["proxy_time"]), ws, we)
            exp = case["expected"]
            if exp["kind"] == "ok":
                self.assertIsInstance(res, WindowMapOk)
                assert isinstance(res, WindowMapOk)
                self.assertEqual(res.time, exp["time"])
            else:
                self.assertIsInstance(res, WindowMapOutOfWindow)
                assert isinstance(res, WindowMapOutOfWindow)
                self.assertEqual(res.reason, exp["reason"])
                self.assertEqual(res.clamped_time, exp["clamped_time"])

        self.assertEqual(c2_to_c3_time(14.2, ws, we), 714.7)
        self.assertEqual(c3_to_c2_time(714.7, ws, we), 14.2)
        with self.assertRaises(SegmentError):
            c2_to_c3_time(-0.01, ws, we)
        with self.assertRaises(SegmentError):
            c2_to_c3_time(240.1, ws, we)
        with self.assertRaises(SegmentError):
            c3_to_c2_time(700.0, ws, we)
        with self.assertRaises(SegmentError):
            c3_to_c2_time(941.0, ws, we)

    def test_playback_descriptor_mapping(self) -> None:
        cases = self.fixtures["timeline_mapper_vectors"]["playback_descriptor_cases"]
        for case in cases:
            with self.subTest(case=case["name"]):
                desc = descriptor_from_dict(case["descriptor"])
                res = source_to_playback_time(as_source_time(case["source_time"]), desc)
                if case["expected_kind"] == "ok":
                    self.assertIsInstance(res, PlaybackMapOk)
                    assert isinstance(res, PlaybackMapOk)
                    self.assertEqual(res.time, case["expected_playback_time"])
                    rev = playback_time_to_source(res.time, desc)
                    self.assertIsInstance(rev, PlaybackMapOk)
                    assert isinstance(rev, PlaybackMapOk)
                    self.assertEqual(rev.time, case["source_time"])
                else:
                    self.assertIsInstance(res, SourceToConcatGap)

        sv = SourceVideoDescriptor(source_duration=100.0)
        self.assertIsInstance(playback_time_to_source(-1.0, sv), PlaybackOutOfRange)
        self.assertIsInstance(source_to_playback_time(as_source_time(105.0), sv), PlaybackOutOfRange)
        ew_desc = EditWindowDescriptor(window_start=as_source_time(10.0), window_end=as_source_time(30.0))
        self.assertIsInstance(playback_time_to_source(25.0, ew_desc), WindowMapOutOfWindow)
        rc_desc = RenderedClipDescriptor(segments=[[10.0, 20.0]])
        self.assertIsInstance(playback_time_to_source(15.0, rc_desc), PlaybackOutOfRange)

    def test_rfc8785_canonicalization_and_render_fingerprints(self) -> None:
        for vec in self.fixtures["rfc8785_fingerprint_vectors"]:
            with self.subTest(name=vec["name"]):
                layers_json = canonical_json_dumps(vec["layers"])
                settings_json = canonical_json_dumps(vec["settings"])
                self.assertEqual(layers_json, vec["expected_canonical_layers_json"])
                self.assertEqual(settings_json, vec["expected_canonical_settings_json"])

                mfp = compute_media_fingerprint(vec["source_asset_id"], vec["ranges"])
                self.assertEqual(mfp, vec["expected_media_fingerprint"])

                rfp = compute_render_fingerprint(
                    mfp,
                    vec["layout_mode"],
                    vec["crop_anchor_x"],
                    vec["layers"],
                    vec["settings"],
                )
                self.assertEqual(rfp, vec["expected_render_fingerprint"])

        # Namespace class parity
        self.assertEqual(TimelineMapper.round_ms(10.0005), 10.001)
        self.assertEqual(canonical_json_dumps({"b": -0.0, "a": 1.0}), '{"a":1,"b":0}')

    def test_rejects_non_finite_and_invalid_inputs(self) -> None:
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(TimelineMapperError):
                round_ms(bad)
            with self.assertRaises(TimelineMapperError):
                as_source_time(bad)
            with self.assertRaises(TimelineMapperError):
                canonical_json_dumps({"x": bad})
        with self.assertRaises(TimelineMapperError):
            normalize_segments([])
        with self.assertRaises(TimelineMapperError):
            normalize_segments([[10.0, 20.0], [19.0, 25.0]])


if __name__ == "__main__":
    unittest.main()
