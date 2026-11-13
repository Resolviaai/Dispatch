"""Wave 2B media + performance defect regressions.

Covers:
- probe_video memoization (no subprocess work on cache hits; mtime/size change re-probes;
  bounded cache; defensive copies).
- Concurrent filmstrip slot extraction (bounded thread pool, order preserved, failed
  slots tolerated exactly like the sequential implementation).
- Waveform array-backed PCM decoding (odd-length tail safety), ranged PCM extraction,
  cough frame coalescing, and speech-gated transient click detection.
- Cloud sync rescan watermark (unchanged clips skipped, revision bumps re-sync,
  mutators clear the stored hash, failed pushes never store a hash).
"""
import json
import os
import struct
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dispatch import db


# ---------------------------------------------------------------------------
# probe_video cache
# ---------------------------------------------------------------------------

def _ffprobe_payload():
    return json.dumps({
        "format": {"duration": "12.5"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920, "tags": {}}
        ],
    })


class TestProbeVideoCache(unittest.TestCase):
    def setUp(self):
        from dispatch.ingestion import validator

        self.validator = validator
        validator._PROBE_CACHE.clear()
        self._tmp = tempfile.TemporaryDirectory()
        self.video = Path(self._tmp.name) / "probe.mp4"
        self.video.write_bytes(b"fake-video-bytes")

    def tearDown(self):
        self.validator._PROBE_CACHE.clear()
        self._tmp.cleanup()

    def _fake_run(self, calls):
        def _run(cmd, **kwargs):
            calls.append(list(cmd))
            if cmd[0] == "ffprobe":
                return SimpleNamespace(returncode=0, stdout=_ffprobe_payload(), stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return _run

    def test_second_probe_skips_all_subprocess_work(self):
        calls = []
        with patch.object(self.validator.subprocess, "run", side_effect=self._fake_run(calls)):
            ok1, meta1, err1 = self.validator.probe_video(self.video, chunk_id="chk_cache")
            count_after_first = len(calls)
            ok2, meta2, err2 = self.validator.probe_video(self.video, chunk_id="chk_cache")
        self.assertTrue(ok1 and ok2)
        self.assertEqual(count_after_first, 3, "one probe = orientation inspect + re-inspect + decode check")
        self.assertEqual(len(calls), count_after_first, "cache hit must not spawn any subprocess")
        self.assertEqual(meta1, meta2)
        self.assertEqual(meta1["duration"], 12.5)

    def test_changed_mtime_and_size_reprobes(self):
        calls = []
        with patch.object(self.validator.subprocess, "run", side_effect=self._fake_run(calls)):
            self.validator.probe_video(self.video, chunk_id="chk_cache")
            base_calls = len(calls)
            # A file being rewritten (temp render outputs) changes mtime/size.
            self.video.write_bytes(b"fake-video-bytes-v2")
            stamp = time.time() + 5
            os.utime(self.video, (stamp, stamp))
            self.validator.probe_video(self.video, chunk_id="chk_cache")
        self.assertEqual(len(calls), base_calls * 2, "stale key (new mtime) must re-probe")

    def test_cache_hit_returns_defensive_copy(self):
        calls = []
        with patch.object(self.validator.subprocess, "run", side_effect=self._fake_run(calls)):
            _, meta, _ = self.validator.probe_video(self.video, chunk_id="chk_cache")
            meta["duration"] = 999.0
            _, meta2, _ = self.validator.probe_video(self.video, chunk_id="chk_cache")
        self.assertEqual(meta2["duration"], 12.5, "caller mutation must not poison the cache")

    def test_cache_is_bounded_and_evicts_oldest(self):
        for i in range(self.validator._PROBE_CACHE_MAX_ENTRIES + 10):
            self.validator._probe_cache_put((f"k{i}", i, i), (True, {"i": i}, None))
        self.assertLessEqual(len(self.validator._PROBE_CACHE), self.validator._PROBE_CACHE_MAX_ENTRIES)
        self.assertIsNone(self.validator._PROBE_CACHE.get(("k0", 0, 0)), "oldest entries evicted first")
        self.assertIsNotNone(self.validator._PROBE_CACHE.get(("k265", 265, 265)))


# ---------------------------------------------------------------------------
# Filmstrip concurrent extraction
# ---------------------------------------------------------------------------

class TestFilmstripConcurrency(unittest.TestCase):
    def _generate(self, tmp_path, clip_id, extract_fn):
        from dispatch.video_engine import filmstrip

        fake_clips_dir = tmp_path / "clips"
        fake_clips_dir.mkdir(exist_ok=True)
        video = tmp_path / "source.mp4"
        video.write_bytes(b"0")
        clip = {"duration": 20.0, "start_time": 0.0}
        with patch.object(filmstrip, "CLIPS_DIR", fake_clips_dir), \
             patch.object(filmstrip, "_extract_slot", side_effect=extract_fn):
            return filmstrip.generate_filmstrip(
                clip_id=clip_id,
                clip=clip,
                active_video=video,
                video_fingerprint="fp",
                video_mtime_ns=123,
            )

    def test_slots_extract_concurrently_with_bounded_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = threading.Lock()
            state = {"active": 0, "max_active": 0}

            def fake_extract(active_video, axis_time, out_path):
                with lock:
                    state["active"] += 1
                    state["max_active"] = max(state["max_active"], state["active"])
                time.sleep(0.25)
                with lock:
                    state["active"] -= 1
                out_path.write_bytes(b"jpg")
                return True

            start = time.monotonic()
            manifest = self._generate(Path(tmp), "strip-conc", fake_extract)
            elapsed = time.monotonic() - start

        self.assertIsNotNone(manifest)
        # duration 20s -> ceil(20 / 2.5) = 8 slots; pool of 4 workers must overlap.
        self.assertEqual(state["max_active"], 4)
        # Serial execution would need >= 2.0s of sleep time alone.
        self.assertLess(elapsed, 1.5)
        self.assertEqual([f["index"] for f in manifest["frames"]], list(range(8)))
        self.assertEqual([f["name"] for f in manifest["frames"]], [f"f{i:02d}.jpg" for i in range(8)])

    def test_failed_slot_is_tolerated_and_order_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fake_extract(active_video, axis_time, out_path):
                # Slot 3 mid-point at (3 + 0.5) * 2.5 = 8.75s in source scope.
                if abs(axis_time - 8.75) < 0.01:
                    return False
                out_path.write_bytes(b"jpg")
                return True

            manifest = self._generate(Path(tmp), "strip-fail", fake_extract)

        self.assertIsNotNone(manifest)
        self.assertEqual([f["index"] for f in manifest["frames"]], [0, 1, 2, 4, 5, 6, 7])


# ---------------------------------------------------------------------------
# Waveform: array-backed PCM, ranged extraction, cough coalescing, click gating
# ---------------------------------------------------------------------------

def _pcm(samples):
    return struct.pack(f"<{len(samples)}h", *samples)


class TestWaveformArrayBackend(unittest.TestCase):
    def test_pcm_samples_matches_struct_and_handles_odd_tail(self):
        from dispatch.video_engine.waveform import _pcm_samples

        samples = [0, -3, 7, 32767, -32768, 1234]
        raw = _pcm(samples) + b"\x51"  # truncated odd-length tail byte
        decoded = list(_pcm_samples(raw))
        self.assertEqual(decoded, samples, "array('h') decode must match little-endian struct unpack")
        self.assertEqual(len(_pcm_samples(b"\x01")), 0, "a lone tail byte is ignored (alignment)")

    def test_full_file_detectors_tolerate_odd_length_tail(self):
        from dispatch.video_engine.waveform import (
            detect_audio_clipping,
            detect_transient_clicks,
            detect_breaths_and_coughs,
        )

        raw = _pcm([100] * 800) + b"\x07"
        self.assertEqual(detect_audio_clipping(raw), [])
        self.assertEqual(detect_transient_clicks(raw), [])
        self.assertEqual(detect_breaths_and_coughs(raw, [(0.0, 0.5)]), [])

    def test_extract_pcm_samples_full_file_command_unchanged(self):
        from dispatch.video_engine import waveform

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return SimpleNamespace(returncode=0, stdout=b"\x00\x00", stderr=b"")

        with patch("dispatch.video_engine.waveform.subprocess.run", side_effect=fake_run):
            waveform.extract_pcm_samples(Path("fake.mp4"), sample_rate=16000)
        self.assertNotIn("-ss", captured["cmd"])
        self.assertNotIn("-t", captured["cmd"])

    def test_extract_pcm_samples_range_seeks_and_trims_to_window(self):
        from dispatch.video_engine import waveform

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            # 24000 samples = 1.5s of audio (deliberately longer than the 1s window)
            return SimpleNamespace(returncode=0, stdout=b"\x01\x00" * 24000, stderr=b"")

        with patch("dispatch.video_engine.waveform.subprocess.run", side_effect=fake_run):
            raw, rate, count = waveform.extract_pcm_samples(
                Path("fake.mp4"), sample_rate=16000, clip_start=10.0, clip_end=11.0,
            )
        cmd = captured["cmd"]
        self.assertEqual(cmd[cmd.index("-ss") + 1], "10.000000")
        self.assertEqual(cmd[cmd.index("-t") + 1], "1.000000")
        self.assertEqual(count, 16000, "returned PCM is capped at the requested window")
        self.assertEqual(len(raw), 32000)


class TestCoughCoalescing(unittest.TestCase):
    def _pcm_with_cough_bursts(self, bursts, sample_rate=16000, total=3.0):
        samples = [0] * int(total * sample_rate)
        for start, end in bursts:
            for i in range(int(start * sample_rate), int(end * sample_rate)):
                samples[i] = 9000 if (i // 40) % 2 == 0 else -9000  # rms ~9000 -> > -18 dBFS
        return _pcm(samples)

    def test_continuous_burst_is_one_proposal(self):
        from dispatch.video_engine.waveform import detect_breaths_and_coughs

        pcm = self._pcm_with_cough_bursts([(1.0, 1.4)])
        events = detect_breaths_and_coughs(pcm, [(0.0, 0.5), (2.5, 3.0)], sample_rate=16000)
        coughs = [e for e in events if e["type"] == "cough"]
        self.assertEqual(len(coughs), 1, "an 8-frame burst used to emit 8 separate proposals")
        self.assertAlmostEqual(coughs[0]["source_start"], 1.0, delta=0.06)
        self.assertGreaterEqual(coughs[0]["source_end"], coughs[0]["source_start"] + 0.35)
        self.assertEqual(coughs[0]["decision"], "REVIEW")

    def test_distant_bursts_stay_separate(self):
        from dispatch.video_engine.waveform import detect_breaths_and_coughs

        pcm = self._pcm_with_cough_bursts([(1.0, 1.3), (2.0, 2.3)])
        events = detect_breaths_and_coughs(pcm, [(0.0, 0.5), (2.5, 3.0)], sample_rate=16000)
        coughs = [e for e in events if e["type"] == "cough"]
        self.assertEqual(len(coughs), 2)

    def test_sub_tolerance_lull_is_joined(self):
        from dispatch.video_engine.waveform import detect_breaths_and_coughs

        # Loud 1.00-1.10, one silent 50ms frame at 1.10, loud again 1.15-1.30:
        # the 100ms gap between cough frames is within the join tolerance.
        pcm = self._pcm_with_cough_bursts([(1.0, 1.1), (1.15, 1.3)])
        events = detect_breaths_and_coughs(pcm, [(0.0, 0.5), (2.5, 3.0)], sample_rate=16000)
        coughs = [e for e in events if e["type"] == "cough"]
        self.assertEqual(len(coughs), 1, "a <=100ms lull must not split the burst")

    def test_breath_detection_still_works_alongside_coalescing(self):
        import random
        from dispatch.video_engine.waveform import detect_breaths_and_coughs

        random.seed(123)
        samples = [0] * 32000
        for i in range(9600, 14400):  # 0.6s - 0.9s breath noise
            samples[i] = int(random.uniform(-1400, 1400))
        events = detect_breaths_and_coughs(_pcm(samples), [(0.0, 0.5), (1.1, 2.0)], sample_rate=16000)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "breath")


class TestClickSpeechGating(unittest.TestCase):
    def _click_pcm(self, second_click_sample=12000):
        samples = [100] * 16000
        for i in range(4000, 4005):    # click at 0.25s
            samples[i] = 22000
        for i in range(second_click_sample, second_click_sample + 5):
            samples[i] = 22000
        return _pcm(samples)

    def test_no_intervals_keeps_legacy_ungated_behavior(self):
        from dispatch.video_engine.waveform import detect_transient_clicks

        clicks = detect_transient_clicks(self._click_pcm(), sample_rate=16000)
        self.assertEqual(len(clicks), 2)
        self.assertAlmostEqual(clicks[0]["source_start"], 0.25, delta=0.02)
        self.assertAlmostEqual(clicks[1]["source_start"], 0.75, delta=0.02)

    def test_clicks_inside_speech_are_suppressed(self):
        from dispatch.video_engine.waveform import detect_transient_clicks

        clicks = detect_transient_clicks(
            self._click_pcm(), sample_rate=16000, speech_intervals=[(0.2, 0.5)],
        )
        self.assertEqual(len(clicks), 1, "in-speech transient is a plosive, not a mic defect")
        self.assertAlmostEqual(clicks[0]["source_start"], 0.75, delta=0.02)

    def test_autocut_passes_speech_intervals_to_click_detector(self):
        from dispatch.video_engine import autocut
        from dispatch.video_engine import waveform as wf

        # Clicks at 0.25s (inside the word 0.0-0.3) and 0.45s (inter-word gap 0.3-0.6).
        pcm = self._click_pcm(second_click_sample=7200)
        segments = [{
            "start": 0.0, "end": 1.0, "text": "words here",
            "words": [
                {"text": "words", "start": 0.0, "end": 0.3},
                {"text": "here", "start": 0.6, "end": 1.0},
            ],
        }]
        seen = {}

        def fake_clicks(raw_bytes, sample_rate=16000, speech_intervals=None):
            seen["intervals"] = speech_intervals
            return wf.detect_transient_clicks(raw_bytes, sample_rate=sample_rate, speech_intervals=speech_intervals)

        with patch.object(autocut, "detect_transient_clicks", side_effect=fake_clicks):
            proposals = autocut.analyze_waveform_and_transcript_autocuts(
                media_path=None,
                transcript_segments=segments,
                clip_start=0.0,
                clip_end=1.0,
                raw_pcm_bytes=pcm,
            )
        self.assertEqual(
            seen["intervals"],
            [(0.0, 0.3), (0.6, 1.0)],
            "autocut must derive and forward active speech spans",
        )
        starts = [p["source_start"] for p in proposals if p["type"] == "audio_defect"]
        self.assertNotIn(0.25, starts, "in-speech click must be gated")
        self.assertIn(0.45, starts, "inter-word gap click must still be proposed")


# ---------------------------------------------------------------------------
# Cloud sync rescan watermark
# ---------------------------------------------------------------------------

class TestCloudSyncWatermark(unittest.TestCase):
    def setUp(self):
        db.init_db()
        suffix = uuid.uuid4().hex
        self.chunk_id = f"wm-chunk-{suffix}"
        self.clip_id = f"wm-clip-{suffix}"
        with db.get_db_connection() as conn:
            conn.execute(
                "INSERT INTO chunks (id, filename, filepath, duration) VALUES (?, ?, ?, ?)",
                (self.chunk_id, "wm-source.mp4", "wm-source.mp4", 600.0),
            )
            conn.execute(
                """INSERT INTO clips (id, chunk_id, start_time, end_time, duration, title, status)
                   VALUES (?, ?, 100.0, 115.0, 15.0, 'watermark clip', 'ready_review')""",
                (self.clip_id, self.chunk_id),
            )
            conn.commit()

    def tearDown(self):
        with db.get_db_connection() as conn:
            conn.execute("DELETE FROM clips WHERE id = ?", (self.clip_id,))
            conn.execute("DELETE FROM chunks WHERE id = ?", (self.chunk_id,))
            conn.commit()

    def _sweep(self):
        """Run a sweep that only 'succeeds' for the clip under test so unrelated
        dev-database rows are never stamped with a watermark."""
        from dispatch.cloud import sync as cloud_sync

        calls = []

        def fake_sync(clip_id, clip=None, status_only=False):
            calls.append(clip_id)
            return clip_id == self.clip_id

        with patch.object(cloud_sync, "sync_clip_to_cloud", side_effect=fake_sync):
            cloud_sync.sync_all_clips_to_cloud()
        return [c for c in calls if c == self.clip_id]

    def _stored_hash(self):
        row = db.get_clip_by_id(self.clip_id)
        return row.get("cloud_sync_hash") if row else None

    def test_unchanged_clip_is_skipped_on_resweep(self):
        first = self._sweep()
        self.assertEqual(len(first), 1)
        self.assertTrue(self._stored_hash(), "a successful push stores the watermark")

        second = self._sweep()
        self.assertEqual(len(second), 0, "unchanged clip must not be re-synced")

    def test_range_revision_bump_resyncs_and_clears_hash(self):
        self._sweep()
        self.assertTrue(db.update_clip_trim(self.clip_id, 100.0, 118.0))
        self.assertIsNone(self._stored_hash(), "trim mutator must clear the watermark")
        again = self._sweep()
        self.assertEqual(len(again), 1, "changed clip re-syncs on the next sweep")

    def test_save_caption_data_clears_hash(self):
        self._sweep()
        self.assertTrue(self._stored_hash())
        self.assertTrue(db.save_clip_caption_data(self.clip_id, '{"groups": []}'))
        self.assertIsNone(self._stored_hash(), "caption save must clear the watermark")

    def test_failed_sync_never_stores_watermark(self):
        from dispatch.cloud import sync as cloud_sync

        with patch.object(cloud_sync, "sync_clip_to_cloud", return_value=False):
            cloud_sync.sync_all_clips_to_cloud()
        self.assertIsNone(self._stored_hash(), "a failed push must not watermark the clip")

    def test_compute_hash_is_stable_and_reacts_to_synced_fields(self):
        from dispatch.cloud.sync import compute_clip_sync_hash

        clip = db.get_clip_by_id(self.clip_id)
        h1 = compute_clip_sync_hash(clip)
        h2 = compute_clip_sync_hash(dict(clip))
        self.assertTrue(h1)
        self.assertEqual(h1, h2, "hash must be deterministic for an unchanged row")
        self.assertIsNone(compute_clip_sync_hash({"id": None, "start_time": "not-a-number"}),
                          "unhashable rows report None so the sweep re-syncs")

        changed = dict(clip)
        changed["title"] = "renamed"
        self.assertNotEqual(h1, compute_clip_sync_hash(changed))
        changed2 = dict(clip)
        changed2["range_revision"] = int(clip["range_revision"] or 0) + 1
        self.assertNotEqual(h1, compute_clip_sync_hash(changed2))
        changed3 = dict(clip)
        changed3["caption_data"] = json.dumps({"groups": [{"id": "g1", "words": []}]})
        self.assertNotEqual(h1, compute_clip_sync_hash(changed3))


if __name__ == "__main__":
    unittest.main()
