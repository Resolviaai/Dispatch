"""tests/test_thumbnail_freshness.py
Comprehensive test suite verifying Dispatch video thumbnail freshness,
automatic regeneration, atomic generation, cache invalidation, sub-second
fingerprinting, concurrency serialization, and HTTP headers.
"""
import concurrent.futures
import math
import os
import subprocess
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from dispatch.config import CLIPS_DIR
from dispatch import db
from dispatch.video_engine.thumbnail import (
    is_thumbnail_stale,
    generate_thumbnail_from_video,
    ensure_clip_thumbnail,
    get_file_fingerprint,
    get_thumbnail_version,
    get_thumbnail_version_token,
    populate_clip_media_urls,
)
from dispatch.web.app import app


class TestThumbnailFreshness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        CLIPS_DIR.mkdir(parents=True, exist_ok=True)
        cls.test_video_path = CLIPS_DIR / "test_thumb_source.mp4"
        cls.test_video_alt_path = CLIPS_DIR / "test_thumb_source_alt.mp4"

        # Generate lightweight 2-second color videos via ffmpeg for deterministic testing
        cmd1 = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=navy:s=320x240:d=2",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-c:v", "libx264", "-t", "2",
            "-c:a", "aac", "-shortest",
            "-pix_fmt", "yuv420p",
            str(cls.test_video_path)
        ]
        res1 = subprocess.run(cmd1, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res1.returncode != 0 or not cls.test_video_path.exists():
            raise RuntimeError("Failed to generate test video fixture with ffmpeg")

        cmd2 = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=crimson:s=320x240:d=2",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-c:v", "libx264", "-t", "2",
            "-c:a", "aac", "-shortest",
            "-pix_fmt", "yuv420p",
            str(cls.test_video_alt_path)
        ]
        subprocess.run(cmd2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        for p in (cls.test_video_path, cls.test_video_alt_path):
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass

    def test_01_is_thumbnail_stale_logic(self):
        """Test timestamp-based staleness logic across file existence and mtimes."""
        vpath = self.test_video_path
        tpath = CLIPS_DIR / "temp_logic_test.jpg"

        # Missing thumbnail is stale
        if tpath.exists():
            tpath.unlink()
        self.assertTrue(is_thumbnail_stale(vpath, tpath))

        # 0-byte thumbnail is stale
        tpath.touch()
        self.assertTrue(is_thumbnail_stale(vpath, tpath))

        # Non-empty thumbnail older than video is stale
        tpath.write_bytes(b"dummy image content for testing")
        vid_mtime = vpath.stat().st_mtime
        os.utime(str(tpath), (vid_mtime - 10.0, vid_mtime - 10.0))
        self.assertTrue(is_thumbnail_stale(vpath, tpath))

        # Non-empty thumbnail newer than or equal to video is fresh
        os.utime(str(tpath), (vid_mtime + 5.0, vid_mtime + 5.0))
        self.assertFalse(is_thumbnail_stale(vpath, tpath))

        # Clean up
        if tpath.exists():
            tpath.unlink()

    def test_02_atomic_generation_and_no_leftover_temps(self):
        """Test that FFmpeg extracts frame atomically and leaves no temporary files."""
        out_thumb = CLIPS_DIR / "test_atomic_thumb.jpg"
        meta_thumb = CLIPS_DIR / "test_atomic_thumb.jpg.meta"
        for p in (out_thumb, meta_thumb):
            if p.exists():
                p.unlink()

        success = generate_thumbnail_from_video(self.test_video_path, out_thumb, timestamp_sec=0.5)
        self.assertTrue(success)
        self.assertTrue(out_thumb.exists())
        self.assertGreater(out_thumb.stat().st_size, 100)
        self.assertTrue(meta_thumb.exists(), "Sidecar metadata must be created atomically alongside thumbnail")

        # Verify no .tmp_* files remain in CLIPS_DIR
        temp_files = list(CLIPS_DIR.glob(".tmp_*test_atomic_thumb*"))
        self.assertEqual(len(temp_files), 0, "No temporary thumbnail files should remain after generation")

        # Clean up
        out_thumb.unlink()
        if meta_thumb.exists():
            meta_thumb.unlink()

    def test_03_video_replacement_triggers_thumbnail_regeneration(self):
        """When a video is updated/replaced with same clip ID, ensure_clip_thumbnail regenerates it."""
        clip_id = db.save_clip(
            session_id=None,
            chunk_id=None,
            start_time=0.0,
            end_time=2.0,
            title="Replacement Test Clip",
            hook="Hook",
            description="Desc",
            hashtags="#Test",
            virality_score=80,
            layout_mode="fit_blur"
        )
        clip_vid = CLIPS_DIR / f"{clip_id}.mp4"
        clip_thumb = CLIPS_DIR / f"{clip_id}.jpg"
        clip_meta = CLIPS_DIR / f"{clip_id}.jpg.meta"

        # Copy test video to clip_vid
        clip_vid.write_bytes(self.test_video_path.read_bytes())
        t0 = time.time() - 20.0
        os.utime(str(clip_vid), (t0, t0))

        db.update_clip_media(clip_id=clip_id, video_path=str(clip_vid), thumbnail_path=str(clip_thumb))

        # 1. Initial generation
        first_path = ensure_clip_thumbnail(clip_id)
        self.assertIsNotNone(first_path)
        self.assertTrue(clip_thumb.exists())
        initial_thumb_mtime = clip_thumb.stat().st_mtime

        # 2. Simulate video file replacement on PC (newer mtime)
        t_new = initial_thumb_mtime + 10.0
        os.utime(str(clip_vid), (t_new, t_new))
        self.assertTrue(is_thumbnail_stale(clip_vid, clip_thumb))

        # 3. Call ensure_clip_thumbnail: must detect staleness and regenerate!
        second_path = ensure_clip_thumbnail(clip_id)
        self.assertIsNotNone(second_path)
        new_thumb_mtime = clip_thumb.stat().st_mtime

        self.assertGreater(new_thumb_mtime, initial_thumb_mtime,
                           "Thumbnail modification time must increase after video replacement")
        self.assertFalse(is_thumbnail_stale(clip_vid, clip_thumb),
                         "Regenerated thumbnail must be recognized as fresh")

        # Clean up
        clip_vid.unlink()
        clip_thumb.unlink()
        if clip_meta.exists():
            clip_meta.unlink()

    def test_04_unchanged_video_skips_unnecessary_regeneration(self):
        """Unchanged videos must preserve their cached thumbnail without re-invoking FFmpeg."""
        clip_id = db.save_clip(
            session_id=None,
            chunk_id=None,
            start_time=0.0,
            end_time=2.0,
            title="Cache Hit Test Clip",
            hook="Hook",
            description="Desc",
            hashtags="#Test",
            virality_score=80,
            layout_mode="fit_blur"
        )
        clip_vid = CLIPS_DIR / f"{clip_id}.mp4"
        clip_thumb = CLIPS_DIR / f"{clip_id}.jpg"
        clip_meta = CLIPS_DIR / f"{clip_id}.jpg.meta"

        clip_vid.write_bytes(self.test_video_path.read_bytes())
        db.update_clip_media(clip_id=clip_id, video_path=str(clip_vid), thumbnail_path=str(clip_thumb))

        # First run generates thumbnail
        ensure_clip_thumbnail(clip_id)
        self.assertTrue(clip_thumb.exists())
        first_mtime = clip_thumb.stat().st_mtime

        # Second run on unchanged video must be a cache hit (same mtime)
        ensure_clip_thumbnail(clip_id)
        second_mtime = clip_thumb.stat().st_mtime
        self.assertEqual(first_mtime, second_mtime, "Thumbnail must not be modified when video is unchanged")

        # Clean up
        clip_vid.unlink()
        clip_thumb.unlink()
        if clip_meta.exists():
            clip_meta.unlink()

    def test_05_populate_clip_media_urls_version_query_strings(self):
        """populate_clip_media_urls must inject robust ?v={token} into URLs for cache invalidation."""
        clip_id = db.save_clip(
            session_id=None,
            chunk_id=None,
            start_time=0.0,
            end_time=2.0,
            title="Populate URLs Test",
            hook="Hook",
            description="Desc",
            hashtags="#Test",
            virality_score=80,
            layout_mode="fit_blur"
        )
        clip_vid = CLIPS_DIR / f"{clip_id}.mp4"
        clip_thumb = CLIPS_DIR / f"{clip_id}.jpg"
        clip_meta = CLIPS_DIR / f"{clip_id}.jpg.meta"

        clip_vid.write_bytes(self.test_video_path.read_bytes())
        db.update_clip_media(clip_id=clip_id, video_path=str(clip_vid), thumbnail_path=str(clip_thumb))

        clip_dict = db.get_clip_by_id(clip_id)
        populated = populate_clip_media_urls(clip_dict)

        self.assertIn("thumbnail_url", populated)
        self.assertIn("video_url", populated)
        self.assertIn("source_video_url", populated)

        # Check that robust version token is present
        self.assertRegex(populated["thumbnail_url"], r"^/clips/.*\.jpg\?v=[\w\d_-]+$")
        self.assertRegex(populated["video_url"], r"^/clips/.*\.mp4\?v=\d+$")

        # Version matches deterministic version token
        expected_token = get_thumbnail_version_token(clip_thumb)
        self.assertEqual(populated["thumbnail_url"], f"/clips/{clip_thumb.name}?v={expected_token}")
        self.assertEqual(populated["thumbnail_version"], expected_token)

        # Clean up
        clip_vid.unlink()
        clip_thumb.unlink()
        if clip_meta.exists():
            clip_meta.unlink()

    def test_06_http_cache_headers_and_conditional_revalidation(self):
        """Verify RFC 9111 HTTP cache headers: max-age on versioned, no-cache on unversioned, and 304."""
        clip_id = db.save_clip(
            session_id=None,
            chunk_id=None,
            start_time=0.0,
            end_time=2.0,
            title="HTTP Headers Test",
            hook="Hook",
            description="Desc",
            hashtags="#Test",
            virality_score=80,
            layout_mode="fit_blur"
        )
        clip_vid = CLIPS_DIR / f"{clip_id}.mp4"
        clip_thumb = CLIPS_DIR / f"{clip_id}.jpg"
        clip_meta = CLIPS_DIR / f"{clip_id}.jpg.meta"

        clip_vid.write_bytes(self.test_video_path.read_bytes())
        db.update_clip_media(clip_id=clip_id, video_path=str(clip_vid), thumbnail_path=str(clip_thumb))

        ensure_clip_thumbnail(clip_id)
        self.assertTrue(clip_thumb.exists())
        thumb_v = get_thumbnail_version_token(clip_thumb)

        client = TestClient(app)

        # 1. Versioned request (?v=...) -> public, max-age=604800, immutable
        res_v = client.get(f"/clips/{clip_thumb.name}?v={thumb_v}")
        self.assertEqual(res_v.status_code, 200)
        self.assertIn("public", res_v.headers.get("cache-control", ""))
        self.assertIn("max-age=604800", res_v.headers.get("cache-control", ""))
        self.assertIn("immutable", res_v.headers.get("cache-control", ""))
        etag = res_v.headers.get("etag")
        self.assertIsNotNone(etag)

        # 2. Unversioned request -> bounded cache, then ETag revalidation
        res_unv = client.get(f"/clips/{clip_thumb.name}")
        self.assertEqual(res_unv.status_code, 200)
        self.assertIn("max-age=3600", res_unv.headers.get("cache-control", ""))
        self.assertIn("stale-while-revalidate", res_unv.headers.get("cache-control", ""))

        # 3. Conditional GET with If-None-Match -> 304 Not Modified
        res_cond = client.get(f"/clips/{clip_thumb.name}", headers={"If-None-Match": etag})
        self.assertEqual(res_cond.status_code, 304)

        # Clean up
        clip_vid.unlink()
        clip_thumb.unlink()
        if clip_meta.exists():
            clip_meta.unlink()

    def test_07_direct_image_request_generates_thumbnail_dynamically(self):
        """When an image thumbnail is requested directly, it should be generated if missing."""
        clip_id = db.save_clip(
            session_id=None,
            chunk_id=None,
            start_time=0.0,
            end_time=2.0,
            title="Dynamic Fetch Test",
            hook="Hook",
            description="Desc",
            hashtags="#Test",
            virality_score=80,
            layout_mode="fit_blur"
        )
        clip_vid = CLIPS_DIR / f"{clip_id}.mp4"
        clip_thumb = CLIPS_DIR / f"{clip_id}.jpg"
        clip_meta = CLIPS_DIR / f"{clip_id}.jpg.meta"

        clip_vid.write_bytes(self.test_video_path.read_bytes())
        db.update_clip_media(clip_id=clip_id, video_path=str(clip_vid), thumbnail_path=str(clip_thumb))

        # Ensure thumbnail does not exist initially
        if clip_thumb.exists():
            clip_thumb.unlink()
        if clip_meta.exists():
            clip_meta.unlink()

        client = TestClient(app)
        res = client.get(f"/clips/{clip_thumb.name}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("content-type"), "image/jpeg")
        self.assertTrue(clip_thumb.exists())

        # Clean up
        clip_vid.unlink()
        clip_thumb.unlink()
        if clip_meta.exists():
            clip_meta.unlink()

    def test_08_filmstrip_time_anchored_contract(self):
        """Filmstrip payload is time-anchored and plan-consistent, and any
        artifact drift (stray files, changed video, changed duration) forces a
        full atomic regeneration instead of serving mixed frames."""
        clip_id = db.save_clip(
            session_id=None,
            chunk_id=None,
            start_time=0.0,
            end_time=2.0,
            title="Filmstrip Contract Test",
            hook="Hook",
            description="Desc",
            hashtags="#Test",
            virality_score=80,
            layout_mode="fit_blur",
            duration=2.0,
        )
        clip_vid = CLIPS_DIR / f"{clip_id}.mp4"
        clip_thumb = CLIPS_DIR / f"{clip_id}.jpg"
        clip_meta = CLIPS_DIR / f"{clip_id}.jpg.meta"
        cache_dir = CLIPS_DIR / "filmstrip_cache" / clip_id

        clip_vid.write_bytes(self.test_video_path.read_bytes())
        t0 = time.time() - 30.0
        os.utime(str(clip_vid), (t0, t0))
        db.update_clip_media(clip_id=clip_id, video_path=str(clip_vid), thumbnail_path=str(clip_thumb))

        client = TestClient(app)

        try:
            # 1. Fresh generation -> plan-consistent, time-anchored payload
            res1 = client.get(f"/api/clips/{clip_id}/filmstrip")
            self.assertEqual(res1.status_code, 200)
            body1 = res1.json()
            self.assertEqual(body1["duration"], 2.0)
            self.assertAlmostEqual(body1["interval"], 0.25, places=6)
            self.assertEqual(body1["count"], 8)
            self.assertTrue(body1["scope"] == "render" or body1["scope"] == "source")
            self.assertGreaterEqual(len(body1["frames"]), 1)
            for idx, frame in enumerate(body1["frames"]):
                self.assertAlmostEqual(frame["t"], idx * 0.25, places=6)
                self.assertIn(f"/clips/filmstrip_cache/{clip_id}/", frame["url"])
                self.assertIn("?v=", frame["url"])
                self.assertTrue(frame["url"].split("?")[0].rsplit("/", 1)[-1].endswith(".jpg"))

            # Every declared frame file exists inside the single artifact dir
            self.assertTrue((cache_dir / "manifest.json").exists())
            manifest = (cache_dir / "manifest.json").read_text(encoding="utf-8")
            import json as _json
            m1 = _json.loads(manifest)
            self.assertEqual(m1["count"], 8)

            # 2. Unchanged video -> cache hit (identical payload, no regen)
            res2 = client.get(f"/api/clips/{clip_id}/filmstrip")
            body2 = res2.json()
            self.assertEqual(body1["frames"], body2["frames"])

            # 3. Simulate a leftover artifact from an older generation: a stray
            #    frame file that the manifest never declared. The invariant
            #    check must force regeneration and the stray file must vanish.
            stray = cache_dir / "f09.jpg"
            stray.write_bytes(clip_vid.read_bytes())
            res3 = client.get(f"/api/clips/{clip_id}/filmstrip")
            self.assertEqual(res3.status_code, 200)
            body3 = res3.json()
            urls3 = {f["url"].split("?")[0].rsplit("/", 1)[-1] for f in body3["frames"]}
            self.assertNotIn("f09.jpg", urls3)
            self.assertFalse(stray.exists(), "Stray frame from an older artifact must not survive regeneration")

            # 4. Video content replaced -> fingerprint -> full regeneration
            clip_vid.write_bytes(self.test_video_alt_path.read_bytes())
            t_mod = time.time() + 10.0
            os.utime(str(clip_vid), (t_mod, t_mod))
            res4 = client.get(f"/api/clips/{clip_id}/filmstrip")
            self.assertEqual(res4.status_code, 200)
            body4 = res4.json()
            self.assertEqual(body4["count"], 8)
            self.assertEqual(body4["duration"], 2.0)
            with open(cache_dir / "manifest.json", encoding="utf-8") as fh:
                m4 = _json.loads(fh.read())
            self.assertNotEqual(m1["video_fingerprint"], m4["video_fingerprint"],
                                "Video replacement must invalidate the manifest fingerprint")

            # 5. Legacy flat-frame naming from the old implementation must be
            #    swept after regeneration, never returned again.
            legacy_leftover = CLIPS_DIR / f"{clip_id}_frame_3.jpg"
            legacy_leftover.write_bytes(b"stale-era frame")
            res5 = client.get(f"/api/clips/{clip_id}/filmstrip")
            self.assertEqual(res5.status_code, 200)
            self.assertFalse(legacy_leftover.exists(),
                             "Legacy flat frames must be swept; only the artifact directory is authoritative")
            urls5 = {f["url"].split("?")[0].rsplit("/", 1)[-1] for f in res5.json()["frames"]}
            self.assertFalse(any(name.endswith(f"{clip_id}_frame_3.jpg") for name in urls5))
        finally:
            # Artifact + legacy cleanup for the following tests
            for legacy in CLIPS_DIR.glob(f"{clip_id}_frame_*.jpg*"):
                try:
                    legacy.unlink()
                except Exception:
                    pass
            import shutil as _shutil
            _shutil.rmtree(str(cache_dir), ignore_errors=True)
            clip_vid.unlink()
            if clip_thumb.exists():
                clip_thumb.unlink()
            if clip_meta.exists():
                clip_meta.unlink()

    def test_09_filmstrip_plan_math(self):
        """The single shared sampling plan: proportional count, exact interval
        coverage, clamped budget, and rejection of invalid durations."""
        from dispatch.video_engine.filmstrip import FILMSTRIP_MAX_COUNT, FILMSTRIP_MIN_COUNT, plan_filmstrip

        # Short clip hits the minimum slot budget with exact coverage
        interval, count = plan_filmstrip(2.0)
        self.assertEqual(count, FILMSTRIP_MIN_COUNT)
        self.assertAlmostEqual(interval * count, 2.0, places=9)

        # Long clip is clamped to the max budget while still covering fully
        interval, count = plan_filmstrip(80.0)
        self.assertEqual(count, FILMSTRIP_MAX_COUNT)
        self.assertAlmostEqual(interval, 5.0, places=9)
        self.assertAlmostEqual(interval * count, 80.0, places=9)

        # Mid-duration clip projects one slot per ~2.5 seconds
        interval, count = plan_filmstrip(10.0)
        self.assertEqual(count, max(FILMSTRIP_MIN_COUNT, min(FILMSTRIP_MAX_COUNT, int(math.ceil(10.0 / 2.5)))))
        self.assertAlmostEqual(interval, 10.0 / count, places=9)

        # Invalid durations are rejected by the plan, never guessed
        for bad in (0.0, -1.0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                plan_filmstrip(bad)

    def test_10_filmstrip_sample_axis_coordinates(self):
        """Sampling must address the ACTIVE video's own coordinate system:
        rendered videos are sampled locally; raw sources are sampled at
        clip.start_time + slot, identical to thumbnail behavior."""
        from dispatch.video_engine.filmstrip import sample_axis_times

        # Rendered scope: slot centers in local time, offset ignored
        times = sample_axis_times("render", 25.0, 1.0, 4)
        self.assertEqual(times, [0.5, 1.5, 2.5, 3.5])

        # Source scope: slot centers offset by the clip window start
        times = sample_axis_times("source", 120.0, 1.0, 4)
        self.assertEqual(times, [120.5, 121.5, 122.5, 123.5])

    def test_09_same_second_replacement_and_preserved_timestamp_detection(self):
        """Verify that fingerprinting and nanoseconds detect replacements even with identical integer timestamps."""
        clip_id = db.save_clip(
            session_id=None,
            chunk_id=None,
            start_time=0.0,
            end_time=2.0,
            title="Fingerprint Staleness Test",
            hook="Hook",
            description="Desc",
            hashtags="#Test",
            virality_score=85,
            layout_mode="fit_blur"
        )
        clip_vid = CLIPS_DIR / f"{clip_id}.mp4"
        clip_thumb = CLIPS_DIR / f"{clip_id}.jpg"
        clip_meta = CLIPS_DIR / f"{clip_id}.jpg.meta"

        # 1. Initial write of navy video
        clip_vid.write_bytes(self.test_video_path.read_bytes())
        fixed_t = 1700000000.0  # Stable integer timestamp
        os.utime(str(clip_vid), (fixed_t, fixed_t))
        db.update_clip_media(clip_id=clip_id, video_path=str(clip_vid), thumbnail_path=str(clip_thumb))

        # Generate thumbnail
        ensure_clip_thumbnail(clip_id)
        self.assertTrue(clip_thumb.exists())
        self.assertFalse(is_thumbnail_stale(clip_vid, clip_thumb))
        initial_token = get_thumbnail_version_token(clip_thumb)

        # 2. Replace with crimson video BUT preserve the exact same integer timestamp fixed_t
        if self.test_video_alt_path.exists():
            clip_vid.write_bytes(self.test_video_alt_path.read_bytes())
            os.utime(str(clip_vid), (fixed_t, fixed_t))

            # Fingerprint must detect that content changed even though mtime didn't increase!
            self.assertTrue(
                is_thumbnail_stale(clip_vid, clip_thumb),
                "Content fingerprint must detect replacement even when timestamp is preserved"
            )

            # Regenerate thumbnail
            ensure_clip_thumbnail(clip_id)
            new_token = get_thumbnail_version_token(clip_thumb)
            self.assertNotEqual(initial_token, new_token, "Version token must change when content changes")
            self.assertFalse(is_thumbnail_stale(clip_vid, clip_thumb))

        # Clean up
        clip_vid.unlink()
        clip_thumb.unlink()
        if clip_meta.exists():
            clip_meta.unlink()

    def test_10_concurrent_thumbnail_requests_serialize_safely(self):
        """Concurrent calls to ensure_clip_thumbnail for the same clip must not crash or race."""
        clip_id = db.save_clip(
            session_id=None,
            chunk_id=None,
            start_time=0.0,
            end_time=2.0,
            title="Concurrency Test",
            hook="Hook",
            description="Desc",
            hashtags="#Test",
            virality_score=80,
            layout_mode="fit_blur"
        )
        clip_vid = CLIPS_DIR / f"{clip_id}.mp4"
        clip_thumb = CLIPS_DIR / f"{clip_id}.jpg"
        clip_meta = CLIPS_DIR / f"{clip_id}.jpg.meta"

        clip_vid.write_bytes(self.test_video_path.read_bytes())
        db.update_clip_media(clip_id=clip_id, video_path=str(clip_vid), thumbnail_path=str(clip_thumb))

        def _worker():
            return ensure_clip_thumbnail(clip_id)

        # Execute 6 parallel requests across threads
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(_worker) for _ in range(6)]
            results = [f.result() for f in futures]

        for res in results:
            self.assertIsNotNone(res)
            self.assertEqual(res, clip_thumb)

        self.assertTrue(clip_thumb.exists())
        self.assertGreater(clip_thumb.stat().st_size, 100)

        # Ensure no dangling temporary files
        temp_files = list(CLIPS_DIR.glob(f".tmp_*{clip_id}*"))
        self.assertEqual(len(temp_files), 0, "No orphaned temporary files should remain after concurrent runs")

        # Clean up
        clip_vid.unlink()
        clip_thumb.unlink()
        if clip_meta.exists():
            clip_meta.unlink()


if __name__ == "__main__":
    unittest.main()
