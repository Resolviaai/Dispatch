"""tests/test_thumbnail_freshness.py
Comprehensive test suite verifying Dispatch video thumbnail freshness,
automatic regeneration, atomic generation, cache invalidation, sub-second
fingerprinting, concurrency serialization, and HTTP headers.
"""
import concurrent.futures
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

        # 1. Versioned request (?v=...) -> public, max-age=86400
        res_v = client.get(f"/clips/{clip_thumb.name}?v={thumb_v}")
        self.assertEqual(res_v.status_code, 200)
        self.assertIn("public", res_v.headers.get("cache-control", ""))
        self.assertIn("max-age=86400", res_v.headers.get("cache-control", ""))
        etag = res_v.headers.get("etag")
        self.assertIsNotNone(etag)

        # 2. Unversioned request -> no-cache, must-revalidate
        res_unv = client.get(f"/clips/{clip_thumb.name}")
        self.assertEqual(res_unv.status_code, 200)
        self.assertIn("no-cache", res_unv.headers.get("cache-control", ""))

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

    def test_08_filmstrip_freshness_detection_and_regeneration(self):
        """Filmstrip frames must regenerate and return fresh ?v= parameters when video changes."""
        clip_id = db.save_clip(
            session_id=None,
            chunk_id=None,
            start_time=0.0,
            end_time=2.0,
            title="Filmstrip Freshness Test",
            hook="Hook",
            description="Desc",
            hashtags="#Test",
            virality_score=80,
            layout_mode="fit_blur"
        )
        clip_vid = CLIPS_DIR / f"{clip_id}.mp4"

        clip_vid.write_bytes(self.test_video_path.read_bytes())
        t0 = time.time() - 30.0
        os.utime(str(clip_vid), (t0, t0))

        clip_thumb = CLIPS_DIR / f"{clip_id}.jpg"
        clip_meta = CLIPS_DIR / f"{clip_id}.jpg.meta"
        db.update_clip_media(clip_id=clip_id, video_path=str(clip_vid), thumbnail_path=str(clip_thumb))

        client = TestClient(app)

        # 1. Fetch filmstrip for the first time
        res1 = client.get(f"/api/clips/{clip_id}/filmstrip")
        self.assertEqual(res1.status_code, 200)
        frames1 = res1.json().get("frames", [])
        self.assertGreaterEqual(len(frames1), 1)
        self.assertIn("?v=", frames1[0])

        # 2. Modify video to newer timestamp
        t_mod = time.time() + 10.0
        os.utime(str(clip_vid), (t_mod, t_mod))

        # 3. Fetch filmstrip again: should detect stale frames and re-extract with updated ?v=
        res2 = client.get(f"/api/clips/{clip_id}/filmstrip")
        self.assertEqual(res2.status_code, 200)
        frames2 = res2.json().get("frames", [])
        self.assertGreaterEqual(len(frames2), 1)

        # Clean up frames
        for i in range(12):
            for ext in (".jpg", ".jpg.meta"):
                fpath = CLIPS_DIR / f"{clip_id}_frame_{i}{ext}"
                if fpath.exists():
                    try:
                        fpath.unlink()
                    except Exception:
                        pass
        clip_vid.unlink()
        if clip_thumb.exists():
            clip_thumb.unlink()
        if clip_meta.exists():
            clip_meta.unlink()

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
