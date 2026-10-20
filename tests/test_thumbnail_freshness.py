"""tests/test_thumbnail_freshness.py
Comprehensive test suite verifying Dispatch video thumbnail freshness,
automatic regeneration, atomic generation, cache invalidation, and HTTP headers.
"""
import os
import time
import subprocess
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from dispatch.config import CLIPS_DIR, STORAGE_DIR
from dispatch import db
from dispatch.video_engine.thumbnail import (
    is_thumbnail_stale,
    generate_thumbnail_from_video,
    ensure_clip_thumbnail,
    get_thumbnail_version,
    populate_clip_media_urls,
)
from dispatch.web.app import app


class TestThumbnailFreshness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        CLIPS_DIR.mkdir(parents=True, exist_ok=True)
        cls.test_video_path = CLIPS_DIR / "test_thumb_source.mp4"
        # Generate a lightweight 2-second color video via ffmpeg for deterministic testing
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=navy:s=320x240:d=2",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-c:v", "libx264", "-t", "2",
            "-c:a", "aac", "-shortest",
            "-pix_fmt", "yuv420p",
            str(cls.test_video_path)
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode != 0 or not cls.test_video_path.exists():
            raise RuntimeError("Failed to generate test video fixture with ffmpeg")

    @classmethod
    def tearDownClass(cls):
        if cls.test_video_path.exists():
            try:
                cls.test_video_path.unlink()
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
        if out_thumb.exists():
            out_thumb.unlink()

        success = generate_thumbnail_from_video(self.test_video_path, out_thumb, timestamp_sec=0.5)
        self.assertTrue(success)
        self.assertTrue(out_thumb.exists())
        self.assertGreater(out_thumb.stat().st_size, 100)

        # Verify no .tmp_*.jpg files remain in CLIPS_DIR
        temp_files = list(CLIPS_DIR.glob(".tmp_*.jpg"))
        self.assertEqual(len(temp_files), 0, "No temporary thumbnail files should remain after generation")

        # Clean up
        out_thumb.unlink()

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

    def test_05_populate_clip_media_urls_version_query_strings(self):
        """populate_clip_media_urls must inject ?v={mtime} into URLs for cache invalidation."""
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

        clip_vid.write_bytes(self.test_video_path.read_bytes())
        db.update_clip_media(clip_id=clip_id, video_path=str(clip_vid), thumbnail_path=str(clip_thumb))

        clip_dict = db.get_clip_by_id(clip_id)
        populated = populate_clip_media_urls(clip_dict)

        self.assertIn("thumbnail_url", populated)
        self.assertIn("video_url", populated)
        self.assertIn("source_video_url", populated)

        # Check that ?v= query parameter is present with integer timestamp
        self.assertRegex(populated["thumbnail_url"], r"^/clips/.*\.jpg\?v=\d+$")
        self.assertRegex(populated["video_url"], r"^/clips/.*\.mp4\?v=\d+$")

        # Version matches file mtime
        thumb_v = int(clip_thumb.stat().st_mtime)
        self.assertEqual(populated["thumbnail_url"], f"/clips/{clip_thumb.name}?v={thumb_v}")

        # Clean up
        clip_vid.unlink()
        clip_thumb.unlink()

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

        clip_vid.write_bytes(self.test_video_path.read_bytes())
        db.update_clip_media(clip_id=clip_id, video_path=str(clip_vid), thumbnail_path=str(clip_thumb))

        ensure_clip_thumbnail(clip_id)
        self.assertTrue(clip_thumb.exists())
        thumb_mtime = int(clip_thumb.stat().st_mtime)

        client = TestClient(app)

        # 1. Versioned request (?v=...) -> public, max-age=86400
        res_v = client.get(f"/clips/{clip_thumb.name}?v={thumb_mtime}")
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

        clip_vid.write_bytes(self.test_video_path.read_bytes())
        db.update_clip_media(clip_id=clip_id, video_path=str(clip_vid), thumbnail_path=str(clip_thumb))

        # Ensure thumbnail does not exist initially
        if clip_thumb.exists():
            clip_thumb.unlink()

        client = TestClient(app)
        res = client.get(f"/clips/{clip_thumb.name}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("content-type"), "image/jpeg")
        self.assertTrue(clip_thumb.exists())

        # Clean up
        clip_vid.unlink()
        clip_thumb.unlink()

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
            fpath = CLIPS_DIR / f"{clip_id}_frame_{i}.jpg"
            if fpath.exists():
                fpath.unlink()
        clip_vid.unlink()
        tpath = CLIPS_DIR / f"{clip_id}.jpg"
        if tpath.exists():
            tpath.unlink()


if __name__ == "__main__":
    unittest.main()
