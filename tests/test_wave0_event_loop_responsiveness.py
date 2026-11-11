"""Wave 0 regression tests: FastAPI event-loop unblocking, 60s single-flight
integrations cache, child-only BELOW_NORMAL process priority, and source media
security / path traversal isolation.
"""

import asyncio
import hashlib
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import psutil
from fastapi.testclient import TestClient

import dispatch.web.app  # noqa: F401
from dispatch import db
from dispatch.config import CLIPS_DIR, PROCESSING_DIR
from dispatch.sync.receiver import get_auth_token
from dispatch.transcription.subprocess_utils import low_priority_subprocess_kwargs, run_checked
from dispatch.video_engine import renderer
from dispatch.web.app import (
    _FFMPEG_CONCURRENCY_LIMIT,
    _clear_auth_cache,
    _clear_integrations_status_cache,
    app,
)

app_module = sys.modules["dispatch.web.app"]


class TestWave0EventLoopResponsiveness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db.init_db()
        _clear_integrations_status_cache()
        _clear_auth_cache()

    async def asyncTearDown(self):
        _clear_integrations_status_cache()
        _clear_auth_cache()

    async def test_status_responds_while_integrations_status_hangs_and_coalesces_single_flight(self):
        """Slow external YouTube/Supabase probes must not block /api/status, and
        concurrent cold callers must coalesce onto one single-flight probe + 60s TTL cache.
        """
        probe_started = threading.Event()
        release_probe = threading.Event()
        call_count = 0
        count_lock = threading.Lock()

        fake_service = MagicMock()
        fake_service.channels.return_value.list.return_value.execute.return_value = {
            "items": [{"id": "UC_TEST_123", "snippet": {"title": "Test Channel"}}]
        }

        def slow_get_youtube_service():
            nonlocal call_count
            with count_lock:
                call_count += 1
            probe_started.set()
            release_probe.wait(timeout=3.0)
            return fake_service

        with patch("dispatch.youtube_inbox.oauth.get_youtube_service", side_effect=slow_get_youtube_service), \
             patch("dispatch.youtube_inbox.oauth.verify_channel_alignment", return_value={"matched": True, "status": "matched"}):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://127.0.0.1:8000",
            ) as client:
                t1 = asyncio.create_task(client.get("/api/integrations/status"))
                t2 = asyncio.create_task(client.get("/api/integrations/status"))
                t3 = asyncio.create_task(client.get("/api/integrations/status"))

                deadline = time.monotonic() + 2.0
                while not probe_started.is_set() and time.monotonic() < deadline:
                    await asyncio.sleep(0.01)
                self.assertTrue(probe_started.is_set(), "Integrations probe did not start")

                # /api/status MUST return immediately while integrations probe is blocked
                t0 = time.monotonic()
                status_res = await asyncio.wait_for(client.get("/api/status"), timeout=1.0)
                status_elapsed = time.monotonic() - t0
                self.assertEqual(status_res.status_code, 200)
                self.assertIn("ready_clips", status_res.json())
                self.assertLess(status_elapsed, 0.5)

                release_probe.set()
                r1, r2, r3 = await asyncio.gather(t1, t2, t3)
                for r in (r1, r2, r3):
                    self.assertEqual(r.status_code, 200)
                    self.assertTrue(r.json()["youtube"]["connected"])
                    self.assertEqual(r.json()["youtube"]["channel_id"], "UC_TEST_123")

                self.assertEqual(call_count, 1, "Concurrent callers must share a single-flight probe")

                # Subsequent call within 60s TTL hits the cache without probing again
                cached_res = await client.get("/api/integrations/status")
                self.assertEqual(cached_res.status_code, 200)
                self.assertEqual(call_count, 1, "Subsequent request within 60s TTL must hit cache")

                # Cache invalidation triggers a fresh probe
                _clear_integrations_status_cache()
                refreshed_res = await client.get("/api/integrations/status")
                self.assertEqual(refreshed_res.status_code, 200)
                self.assertEqual(call_count, 2, "Cleared cache must re-probe integrations")

    async def test_integrations_status_timeout_returns_safe_fallback_without_hanging(self):
        """If external integration verification hangs past timeout, endpoint returns degraded fallback."""
        release_probe = threading.Event()

        def hung_get_youtube_service():
            release_probe.wait(timeout=2.0)
            return None

        try:
            with patch.object(app_module, "_INTEGRATIONS_STATUS_FETCH_TIMEOUT_SECONDS", 0.08), \
                 patch("dispatch.youtube_inbox.oauth.get_youtube_service", side_effect=hung_get_youtube_service), \
                 patch("dispatch.youtube_inbox.oauth.verify_channel_alignment", return_value={"matched": False, "status": "unauthenticated"}):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://127.0.0.1:8000",
                ) as client:
                    t0 = time.monotonic()
                    res = await client.get("/api/integrations/status")
                    elapsed = time.monotonic() - t0
                    self.assertEqual(res.status_code, 200)
                    body = res.json()
                    self.assertFalse(body["youtube"]["connected"])
                    self.assertEqual(body["youtube"]["channel_alignment"]["status"], "timeout")
                    self.assertLess(elapsed, 0.5)
        finally:
            release_probe.set()

    async def test_status_responds_during_receiver_sha256_hashing(self):
        """SHA-256 verification in receiver.py must not block /api/status on the event loop."""
        seg_id = "seg_wave0_hash_test"
        test_file = PROCESSING_DIR / f"{seg_id}.mp4"
        payload = b"WAVE0_NONBLOCKING_HASH_PAYLOAD_" * 64
        expected_sha = hashlib.sha256(payload).hexdigest()
        test_file.write_bytes(payload)
        token = get_auth_token()

        hash_started = threading.Event()
        release_hash = threading.Event()
        real_sha256 = hashlib.sha256

        class SlowSha256:
            def __init__(self):
                self._inner = real_sha256()

            def update(self, data):
                hash_started.set()
                release_hash.wait(timeout=3.0)
                self._inner.update(data)

            def hexdigest(self):
                return self._inner.hexdigest()

        try:
            with patch("dispatch.sync.receiver.hashlib.sha256", side_effect=SlowSha256):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://127.0.0.1:8000",
                ) as client:
                    verify_task = asyncio.create_task(
                        client.get(
                            f"/api/sync/verify-chunk?segment_id={seg_id}&sha256={expected_sha}&file_size={len(payload)}",
                            headers={"x-auth-token": token},
                        )
                    )

                    deadline = time.monotonic() + 2.0
                    while not hash_started.is_set() and time.monotonic() < deadline:
                        await asyncio.sleep(0.01)
                    self.assertTrue(hash_started.is_set(), "Receiver SHA-256 hashing did not start")

                    status_res = await asyncio.wait_for(client.get("/api/status"), timeout=1.0)
                    self.assertEqual(status_res.status_code, 200)

                    release_hash.set()
                    verify_res = await verify_task
                    self.assertEqual(verify_res.status_code, 200)
                    self.assertTrue(verify_res.json()["verified"])
                    self.assertEqual(verify_res.json()["status"], "VERIFIED")
        finally:
            release_hash.set()
            test_file.unlink(missing_ok=True)

    async def test_status_responds_during_autocut_and_ffmpeg_slot_bounds_concurrency(self):
        """Autocut DSP runs off the event loop and respects the bounded FFmpeg semaphore."""
        clip_id = db.save_clip(
            session_id=None,
            chunk_id=None,
            start_time=0.0,
            end_time=20.0,
            title="Wave 0 Autocut Concurrency Clip",
            hook="Hook",
            description="Desc",
            hashtags="#wave0",
            virality_score=80,
            layout_mode="fit_blur",
        )
        active = 0
        max_active = 0
        active_lock = threading.Lock()
        first_entered = threading.Event()
        release_autocut = threading.Event()

        def slow_autocut(**_kwargs):
            nonlocal active, max_active
            with active_lock:
                active += 1
                max_active = max(max_active, active)
                first_entered.set()
            try:
                release_autocut.wait(timeout=3.0)
                return []
            finally:
                with active_lock:
                    active -= 1

        try:
            with patch.object(app_module, "resolve_clip_source_path", return_value=None), \
                 patch.object(app_module, "analyze_waveform_and_transcript_autocuts", side_effect=slow_autocut):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://127.0.0.1:8000",
                ) as client:
                    tasks = [
                        asyncio.create_task(client.get(f"/api/clips/{clip_id}/autocut/proposals"))
                        for _ in range(_FFMPEG_CONCURRENCY_LIMIT + 2)
                    ]

                    deadline = time.monotonic() + 2.0
                    while not first_entered.is_set() and time.monotonic() < deadline:
                        await asyncio.sleep(0.01)
                    self.assertTrue(first_entered.is_set())

                    # Let all tasks attempt to enter _acquire_ffmpeg_slot before releasing
                    await asyncio.sleep(0.08)
                    status_res = await asyncio.wait_for(client.get("/api/status"), timeout=1.0)
                    self.assertEqual(status_res.status_code, 200)
                    self.assertLessEqual(
                        max_active,
                        _FFMPEG_CONCURRENCY_LIMIT,
                        "Concurrent FFmpeg/DSP work must stay bounded by semaphore",
                    )

                    release_autocut.set()
                    responses = await asyncio.gather(*tasks)
                    for res in responses:
                        self.assertEqual(res.status_code, 200)
                        self.assertEqual(res.json()["clip_id"], clip_id)
        finally:
            release_autocut.set()
            with db.get_db_connection() as conn:
                conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
                conn.commit()


class TestWave0ProcessPriorityIsolation(unittest.TestCase):
    def test_start_dispatch_does_not_lower_server_process_priority(self):
        """dispatch/main.py must NEVER lower the main Dispatch/Uvicorn server priority."""
        import dispatch.main as main_mod

        with patch.object(main_mod, "acquire_single_instance_lock", return_value=True), \
             patch.object(main_mod, "release_single_instance_lock"), \
             patch.object(main_mod.db, "init_db"), \
             patch.object(main_mod, "recover_laptop_orchestrator", return_value={}), \
             patch.object(main_mod.governor, "set_low_process_priority") as mock_low_prio, \
             patch.object(main_mod.shutil, "which", return_value="ffmpeg"), \
             patch("dispatch.transcription.worker.TranscriptionWorker"), \
             patch("dispatch.ai_clips.worker.HighlightWorker"), \
             patch("dispatch.publisher.outbox.PublishingOutboxWorker"), \
             patch("dispatch.youtube_inbox.poller.YouTubeInboxPoller"), \
             patch("dispatch.youtube_connections.RemoteJobPoller"), \
             patch("dispatch.cloud.sync.start_cloud_sync_worker"), \
             patch("dispatch.main.uvicorn.run"):
            main_mod.start_dispatch(run_web=True)

        mock_low_prio.assert_not_called()

    def test_run_checked_spawns_child_at_below_normal_without_lowering_parent(self):
        """Child processes spawned via run_checked get BELOW_NORMAL while parent stays normal."""
        parent_proc = psutil.Process(os.getpid())
        parent_before = parent_proc.nice()

        probe_code = "import os, psutil; print(psutil.Process(os.getpid()).nice())"
        result = run_checked([sys.executable, "-c", probe_code], timeout_seconds=10.0, label="priority-probe")
        self.assertEqual(result.returncode, 0, result.stderr)
        child_nice = int(result.stdout.strip())
        parent_after = parent_proc.nice()

        self.assertEqual(parent_before, parent_after, "Parent process priority must remain unchanged")
        if os.name == "nt":
            expected = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000)
            self.assertEqual(child_nice, expected)
            self.assertNotEqual(parent_after, expected)
        else:
            self.assertGreaterEqual(child_nice, parent_before + 10)

    def test_low_priority_subprocess_kwargs_posix_branch(self):
        """On POSIX platforms, low_priority_subprocess_kwargs provides a preexec_fn using os.nice."""
        with patch("dispatch.transcription.subprocess_utils.os.name", "posix"), \
             patch("dispatch.transcription.subprocess_utils.os.nice", create=True) as mock_nice:
            kwargs = low_priority_subprocess_kwargs()
            self.assertIn("preexec_fn", kwargs)
            self.assertNotIn("creationflags", kwargs)
            kwargs["preexec_fn"]()
            mock_nice.assert_called_once_with(10)

    def test_renderer_passes_below_normal_priority_to_ffmpeg_subprocesses(self):
        """Both render_clip and slice_clean_clip pass child priority kwargs to subprocess.run."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            source.write_bytes(b"source media")
            rendered_out = CLIPS_DIR / "wave0_prio_render.mp4"
            rendered_thumb = CLIPS_DIR / "wave0_prio_render.jpg"
            sliced_out = CLIPS_DIR / "wave0_prio_slice.mp4"
            sliced_thumb = CLIPS_DIR / "wave0_prio_slice.jpg"

            def fake_run(cmd, **kwargs):
                out_arg = Path(cmd[-1])
                out_arg.parent.mkdir(parents=True, exist_ok=True)
                out_arg.write_bytes(b"fake media output")
                return SimpleNamespace(returncode=0, stderr="")

            try:
                with patch("dispatch.ingestion.validator.inspect_video_orientation", return_value={
                    "valid": True, "orientation": "landscape", "has_audio": True,
                }), patch("dispatch.ingestion.validator.probe_video", return_value=(
                    True, {"duration": 15.0}, None,
                )), patch("dispatch.video_engine.renderer.subprocess.run", side_effect=fake_run) as mock_run, \
                     patch("dispatch.video_engine.renderer.db.get_clip_by_id", return_value=None), \
                     patch("dispatch.video_engine.renderer.db.record_render_completion"), \
                     patch("dispatch.video_engine.renderer.db.update_clip_media", return_value=True), \
                     patch("dispatch.cloud.sync.report_render_complete_to_cloud"):
                    renderer.render_clip(
                        source_video=source,
                        clip_id="wave0_prio_render",
                        start_time=0.0,
                        end_time=15.0,
                        layout_mode="fit_blur",
                        subtitle_options={"enabled": False, "preserve_timing": True},
                    )
                    renderer.slice_clean_clip(
                        source_video=source,
                        clip_id="wave0_prio_slice",
                        start_time=0.0,
                        end_time=15.0,
                    )

                self.assertEqual(mock_run.call_count, 4)
                expected_kw = low_priority_subprocess_kwargs()
                for call in mock_run.call_args_list:
                    if "creationflags" in expected_kw:
                        self.assertEqual(call.kwargs.get("creationflags"), expected_kw["creationflags"])
                    else:
                        self.assertIn("preexec_fn", call.kwargs)
            finally:
                for p in (rendered_out, rendered_thumb, sliced_out, sliced_thumb):
                    p.unlink(missing_ok=True)


class TestWave0SourceMediaSecurity(unittest.TestCase):
    def setUp(self):
        db.init_db()
        self.client = TestClient(app)
        self.tmp_dir = tempfile.TemporaryDirectory(prefix="wave0_media_sec_")
        self.tmp_root = Path(self.tmp_dir.name)
        self.created_chunks = []
        self.created_yt = []
        self.created_files = []

    def tearDown(self):
        with db.get_db_connection() as conn:
            for cid in self.created_chunks:
                conn.execute("DELETE FROM chunks WHERE id = ?", (cid,))
            for vid in self.created_yt:
                conn.execute("DELETE FROM youtube_inbox WHERE video_id = ?", (vid,))
            conn.commit()
        for f in self.created_files:
            try:
                Path(f).unlink(missing_ok=True)
            except Exception:
                pass
        self.tmp_dir.cleanup()

    def test_source_chunk_branch_enforces_owner_isolation(self):
        chunk_path = self.tmp_root / "owner_a_chunk.mp4"
        chunk_path.write_bytes(b"PRIVATE_USER_A_CHUNK_BYTES")
        chunk_id = "chk_wave0_owner_a"
        self.created_chunks.append(chunk_id)

        with db.get_db_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO chunks (id, session_id, filename, filepath, duration, status, user_id)
                   VALUES (?, NULL, ?, ?, 12.0, 'processed', 'user_a')""",
                (chunk_id, chunk_path.name, str(chunk_path)),
            )
            conn.commit()

        with patch.object(app_module, "resolve_caller_user_id", return_value="user_b"):
            res_b = self.client.get(f"/api/clips/{chunk_id}/source")
            self.assertEqual(res_b.status_code, 403)
            res_rec_b = self.client.get(f"/api/recordings/{chunk_id}/source")
            self.assertEqual(res_rec_b.status_code, 403)

        with patch.object(app_module, "resolve_caller_user_id", return_value="user_a"):
            res_a = self.client.get(f"/api/clips/{chunk_id}/source")
            self.assertEqual(res_a.status_code, 200)
            self.assertEqual(res_a.content, b"PRIVATE_USER_A_CHUNK_BYTES")

    def test_source_yt_and_dir_branches_enforce_owner_isolation(self):
        yt_path = self.tmp_root / "owner_a_yt.mp4"
        yt_path.write_bytes(b"PRIVATE_USER_A_YT_BYTES")
        yt_vid = "wave0yt12345"
        dir_hex = "a1b2c3d4e5f647899012abcdef123456"
        dir_dashed = "dir_a1b2c3d4-e5f6-4789-9012-abcdef123456"
        dir_path = self.tmp_root / f"{dir_dashed}.mp4"
        dir_path.write_bytes(b"PRIVATE_USER_A_DIR_BYTES")
        self.created_yt.extend([yt_vid, dir_dashed])

        with db.get_db_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO youtube_inbox (video_id, title, status, local_video_path, duration, user_id)
                   VALUES (?, 'YT A', 'DOWNLOADED', ?, 20.0, 'user_a')""",
                (yt_vid, str(yt_path)),
            )
            conn.execute(
                """INSERT OR REPLACE INTO youtube_inbox (video_id, title, status, local_video_path, duration, user_id)
                   VALUES (?, 'DIR A', 'DOWNLOADED', ?, 25.0, 'user_a')""",
                (dir_dashed, str(dir_path)),
            )
            conn.commit()

        with patch.object(app_module, "resolve_caller_user_id", return_value="user_b"):
            self.assertEqual(self.client.get(f"/api/clips/{yt_vid}/source").status_code, 403)
            self.assertEqual(self.client.get(f"/api/clips/yt_{yt_vid}/source").status_code, 403)
            self.assertEqual(self.client.get(f"/api/clips/dir_{dir_hex}/source").status_code, 403)
            self.assertEqual(self.client.get(f"/api/clips/{dir_dashed}/source").status_code, 403)

        with patch.object(app_module, "resolve_caller_user_id", return_value="user_a"):
            res_yt = self.client.get(f"/api/clips/yt_{yt_vid}/source")
            self.assertEqual(res_yt.status_code, 200)
            self.assertEqual(res_yt.content, b"PRIVATE_USER_A_YT_BYTES")

            res_dir = self.client.get(f"/api/clips/dir_{dir_hex}/source")
            self.assertEqual(res_dir.status_code, 200)
            self.assertEqual(res_dir.content, b"PRIVATE_USER_A_DIR_BYTES")

    def test_source_unrecorded_dir_file_and_youtube_inbox_traversal_blocked(self):
        inbox_dir = self.tmp_root / "youtube_inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        outside_secret = self.tmp_root / "secret_outside.mp4"
        outside_secret.write_bytes(b"SECRET_OUTSIDE_INBOX")
        unowned_dir_file = inbox_dir / "dir_unrecorded123.mp4"
        unowned_dir_file.write_bytes(b"UNRECORDED_DIR_MEDIA")

        with patch.object(app_module, "YOUTUBE_INBOX_DIR", inbox_dir):
            # Traversal attempt via encoded backslash / relative escape must return 403
            res_trav = self.client.get("/api/clips/dir_..%5Csecret_outside/source")
            self.assertEqual(res_trav.status_code, 403)

            # Unowned disk fallback must be blocked for authenticated non-owner user
            with patch.object(app_module, "resolve_caller_user_id", return_value="user_b"):
                res_unowned = self.client.get("/api/clips/dir_unrecorded123/source")
                self.assertEqual(res_unowned.status_code, 403)

            # Localhost / LAN <video> fixture without user scope still works safely inside inbox
            lan_client = TestClient(app, client=("192.168.0.105", 54321))
            res_lan = lan_client.get("/api/clips/dir_unrecorded123/source")
            self.assertEqual(res_lan.status_code, 200)
            self.assertEqual(res_lan.content, b"UNRECORDED_DIR_MEDIA")

    def test_serve_studio_is_relative_to_blocks_sibling_prefix_and_traversal(self):
        if not app_module.STUDIO_DIR.exists():
            self.skipTest("STUDIO_DIR not present in workspace")

        studio_root = self.tmp_root / "web"
        studio_root.mkdir(parents=True, exist_ok=True)
        (studio_root / "index.html").write_text("<html><body>Studio OK</body></html>", encoding="utf-8")
        (studio_root / "manifest.json").write_text('{"name":"Studio"}', encoding="utf-8")

        sibling_secret_dir = self.tmp_root / "web_secret"
        sibling_secret_dir.mkdir(parents=True, exist_ok=True)
        (sibling_secret_dir / "leak.txt").write_text("TOP_SECRET_SIBLING", encoding="utf-8")

        with patch.object(app_module, "STUDIO_DIR", studio_root):
            # Sibling prefix bypass that defeated .startswith() must be rejected with 403
            res_sibling = self.client.get("/studio/..%2Fweb_secret%2Fleak.txt")
            self.assertEqual(res_sibling.status_code, 403)
            self.assertNotIn("TOP_SECRET_SIBLING", res_sibling.text)

            res_backslash = self.client.get("/studio/..%5Cweb_secret%5Cleak.txt")
            self.assertEqual(res_backslash.status_code, 403)

            # Legitimate static asset and SPA route still succeed
            res_manifest = self.client.get("/studio/manifest.json")
            self.assertEqual(res_manifest.status_code, 200)
            self.assertIn("Studio", res_manifest.text)

            res_spa = self.client.get("/studio/clips/123")
            self.assertEqual(res_spa.status_code, 200)
            self.assertIn("Studio OK", res_spa.text)


if __name__ == "__main__":
    unittest.main()
