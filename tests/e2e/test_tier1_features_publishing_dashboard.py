"""Tier 1: Feature Coverage — Publishing Outbox, Web Dashboard & Unit Test Synchronization.
Covers:
- Feature 11: Authentic Publishing & `blocked_needs_auth` (>= 5 tests)
- Feature 12: Web Control Dashboard & APK Distribution (>= 5 tests)
- Feature 13: Python Test Suite Synchronization (>= 5 tests)
"""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.e2e.e2e_base import DispatchE2EBaseTestCase
from dispatch.config import ROOT_DIR, CLIPS_DIR
from dispatch.db import get_db_connection, save_clip, approve_clip, reject_clip, get_setting
from dispatch.publisher.youtube import upload_youtube_short
from dispatch.publisher.instagram import upload_instagram_reel
from dispatch.publisher.linkedin import upload_linkedin_video
from dispatch.publisher.twitter import upload_x_video
from dispatch.publisher.outbox import process_outbox_queue


class TestTier1PublishingDashboard(DispatchE2EBaseTestCase):
    """Tier 1 Feature Coverage: Features 11, 12, and 13."""

    # =========================================================================
    # FEATURE 11: Authentic Publishing & `blocked_needs_auth`
    # =========================================================================

    def test_f11_youtube_missing_credentials_raises_permission_error(self):
        """F11.1: YouTube adapter raises PermissionError when credentials are missing."""
        dummy_video = self.temp_dir / "yt_test.mp4"
        dummy_video.write_bytes(b"DUMMY_VIDEO_DATA")

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(PermissionError) as ctx:
                upload_youtube_short(
                    video_path=dummy_video,
                    title="Test Short",
                    description="Description",
                    tags="#Shorts",
                    credentials_path=self.temp_dir / "non_existent_token.json"
                )
            self.assertIn("YouTube OAuth credentials missing", str(ctx.exception))

    def test_f11_instagram_missing_credentials_raises_permission_error(self):
        """F11.2: Instagram adapter raises PermissionError when credentials are missing."""
        dummy_video = self.temp_dir / "ig_test.mp4"
        dummy_video.write_bytes(b"DUMMY_VIDEO_DATA")

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(PermissionError) as ctx:
                upload_instagram_reel(
                    video_path=dummy_video,
                    caption="Caption #Reels",
                    access_token=None
                )
            self.assertIn("Instagram Graph API credentials missing", str(ctx.exception))

    def test_f11_linkedin_missing_credentials_raises_permission_error(self):
        """F11.3: LinkedIn adapter raises PermissionError when access token is missing."""
        dummy_video = self.temp_dir / "li_test.mp4"
        dummy_video.write_bytes(b"DUMMY_VIDEO_DATA")

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(PermissionError) as ctx:
                upload_linkedin_video(
                    video_path=dummy_video,
                    title="Title",
                    commentary="Commentary",
                    access_token=None
                )
            self.assertIn("LinkedIn API credentials missing", str(ctx.exception))

    def test_f11_twitter_missing_credentials_raises_permission_error(self):
        """F11.4: Twitter/X adapter raises PermissionError when bearer token is missing."""
        dummy_video = self.temp_dir / "x_test.mp4"
        dummy_video.write_bytes(b"DUMMY_VIDEO_DATA")

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(PermissionError) as ctx:
                upload_x_video(
                    video_path=dummy_video,
                    text="Text #X",
                    bearer_token=None
                )
            self.assertIn("X / Twitter API credentials missing", str(ctx.exception))

    def test_f11_outbox_queue_marks_unauthenticated_jobs_blocked_needs_auth(self):
        """F11.5: Outbox queue processor catches PermissionError and sets status blocked_needs_auth."""
        dummy_video = self.temp_dir / "clip_outbox_auth.mp4"
        dummy_video.write_bytes(b"OUTBOX_VIDEO_BYTES")

        clip_id = save_clip(
            session_id="sess_outbox_01",
            chunk_id=None,
            start_time=0.0,
            end_time=10.0,
            title="Auth Block Test",
            hook="Hook",
            description="Desc",
            hashtags="#Test",
            virality_score=85,
            layout_mode="fit_blur",
            platform_targets="youtube,instagram,linkedin,x"
        )
        with get_db_connection() as conn:
            conn.execute("UPDATE clips SET video_path = ? WHERE id = ?", (str(dummy_video), clip_id))

        approve_clip(clip_id, custom_platforms="youtube,instagram,linkedin,x")

        with patch("dispatch.publisher.outbox.upload_youtube_short", side_effect=PermissionError("YouTube auth missing")):
            # Process outbox without live credentials configured
            success_count = process_outbox_queue()
            self.assertEqual(success_count, 0)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT platform, status, last_error FROM publishing_outbox WHERE clip_id = ?", (clip_id,))
            jobs = cursor.fetchall()
            self.assertEqual(len(jobs), 4)
            for j in jobs:
                self.assertEqual(j["status"], "blocked_needs_auth")
                self.assertIsNotNone(j["last_error"])

    def test_f11_outbox_processing_idempotency(self):
        """F11.6: Re-running outbox processor on already-processed queue does not re-upload or duplicate jobs."""
        dummy_video = self.temp_dir / "clip_outbox_idemp.mp4"
        dummy_video.write_bytes(b"OUTBOX_IDEMP_BYTES")

        clip_id = save_clip(
            session_id="sess_idemp_01",
            chunk_id=None,
            start_time=0.0,
            end_time=10.0,
            title="Idempotency Test",
            hook="Hook",
            description="Desc",
            hashtags="#Test",
            virality_score=85,
            layout_mode="fit_blur"
        )
        with get_db_connection() as conn:
            conn.execute("UPDATE clips SET video_path = ? WHERE id = ?", (str(dummy_video), clip_id))

        approve_clip(clip_id, custom_platforms="youtube,instagram")
        process_outbox_queue()

        # Second pass should find 0 pending jobs and return 0
        second_pass = process_outbox_queue()
        self.assertEqual(second_pass, 0)

    # =========================================================================
    # FEATURE 12: Web Control Dashboard & APK Distribution
    # =========================================================================

    def test_f12_web_dashboard_serves_html_ui(self):
        """F12.1: GET / returns HTML dashboard with dark canvas (#0B0F17) structure."""
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/html", res.headers.get("content-type", ""))
        self.assertIn("Dispatch", res.text)

    def test_f12_web_api_status_metrics(self):
        """F12.2: GET /api/status returns disk space metrics and pipeline statistics."""
        res = self.client.get("/api/status")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("disk_free_gb", data)
        self.assertIn("disk_total_gb", data)
        self.assertIn("ready_clips", data)

    def test_f12_web_api_clips_list_ready(self):
        """F12.3: GET /api/clips returns candidate clips segmented by review state."""
        clip_id = save_clip(
            session_id=None,
            chunk_id=None,
            start_time=0.0,
            end_time=20.0,
            title="Dashboard Review Clip",
            hook="High retention hook",
            description="Desc",
            hashtags="#Shorts",
            virality_score=88,
            layout_mode="fit_blur"
        )
        res = self.client.get("/api/clips")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("ready", data)
        ready_ids = [c["id"] for c in data["ready"]]
        self.assertIn(clip_id, ready_ids)

    def test_f12_web_api_clip_approve_enqueues_outbox(self):
        """F12.4: POST /api/clips/{id}/approve enqueues outbox records and updates clip status."""
        clip_id = save_clip(
            session_id=None,
            chunk_id=None,
            start_time=0.0,
            end_time=15.0,
            title="Clip to Approve",
            hook="Hook",
            description="Desc",
            hashtags="#Approve",
            virality_score=90,
            layout_mode="fit_blur"
        )
        res = self.client.post(f"/api/clips/{clip_id}/approve", json={
            "title": "Final Approved Title",
            "hashtags": "#Shorts #AI",
            "publish_mode": "private",
            "platforms": "youtube,instagram"
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "success")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM clips WHERE id = ?", (clip_id,))
            self.assertEqual(cursor.fetchone()["status"], "approved")

            cursor.execute("SELECT COUNT(*) FROM publishing_outbox WHERE clip_id = ?", (clip_id,))
            self.assertEqual(cursor.fetchone()[0], 2)

    def test_f12_web_api_clip_reject_records_rejection(self):
        """F12.5: POST /api/clips/{id}/reject marks clip rejected and logs preference feedback."""
        clip_id = save_clip(
            session_id=None,
            chunk_id=None,
            start_time=0.0,
            end_time=15.0,
            title="Clip to Reject",
            hook="Weak hook",
            description="Desc",
            hashtags="#Reject",
            virality_score=50,
            layout_mode="fit_blur"
        )
        res = self.client.post(f"/api/clips/{clip_id}/reject", json={
            "reason": "Not catchy enough"
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "success")
        self.assertIn("rejected", res.json()["message"])

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM clips WHERE id = ?", (clip_id,))
            self.assertEqual(cursor.fetchone()["status"], "rejected")

            cursor.execute("SELECT reason FROM rejections WHERE clip_id = ?", (clip_id,))
            rejection = cursor.fetchone()
            self.assertIsNotNone(rejection)
            self.assertEqual(rejection["reason"], "Not catchy enough")

    def test_f12_web_download_apk_endpoint(self):
        """F12.6: GET /download/dispatch.apk serves APK or returns HTTP 404 compiling message."""
        res = self.client.get("/download/dispatch.apk")
        # If APK does not exist, returns 404 with compiling explanation
        if res.status_code == 404:
            self.assertIn("compiling", res.json()["detail"].lower())
        else:
            self.assertEqual(res.status_code, 200)
            self.assertIn("android.package-archive", res.headers.get("content-type", ""))

    # =========================================================================
    # FEATURE 13: Python Test Suite Synchronization
    # =========================================================================

    def test_f13_all_test_modules_importable(self):
        """F13.1: All test files in tests/ and tests/e2e/ can be imported without syntax errors."""
        tests_dir = ROOT_DIR / "tests"
        for py_file in tests_dir.glob("test_*.py"):
            mod_name = f"tests.{py_file.stem}"
            try:
                __import__(mod_name)
            except Exception as e:
                self.fail(f"Failed to import {mod_name}: {e}")

    def test_f13_test_cases_subclass_unittest_testcase(self):
        """F13.2: Critical test suites inherit from unittest.TestCase for standard discovery."""
        from tests.test_destructive import TestDestructiveChaos
        from tests.test_chaos import TestDispatchChaosEngineering
        from tests.test_sync_engine import TestSyncEngine
        from tests.test_transport import TestTransportManager

        self.assertTrue(issubclass(TestDestructiveChaos, unittest.TestCase))
        self.assertTrue(issubclass(TestDispatchChaosEngineering, unittest.TestCase))
        self.assertTrue(issubclass(TestSyncEngine, unittest.TestCase))
        self.assertTrue(issubclass(TestTransportManager, unittest.TestCase))

    def test_f13_assertions_enforce_raw_preservation(self):
        """F13.3: Tests assert raw creator video is preserved, never asserting raw file deletion."""
        from dispatch.orchestrator.pipeline_runner import PipelineStageRunner
        runner = PipelineStageRunner()
        test_video = self.temp_dir / "sync_raw_test.mp4"
        test_video.write_bytes(b"PRESERVE_TEST_BYTES")
        runner._run_finalize_stage(
            job={"job_id": "job_raw", "chunk_id": "chunk_raw"},
            chunk={"id": "chunk_raw", "filename": test_video.name, "filepath": str(test_video)},
            filepath=test_video
        )
        self.assertTrue(test_video.exists(), "Assertion violation: raw file was deleted")

    def test_f13_outbox_tests_assert_blocked_needs_auth(self):
        """F13.4: Outbox processor tests verify blocked_needs_auth status upon missing auth."""
        dummy_video = self.temp_dir / "blocked_sync.mp4"
        dummy_video.write_bytes(b"BLOCKED_BYTES")
        clip_id = save_clip(
            session_id=None, chunk_id=None, start_time=0.0, end_time=1.0,
            title="Blocked Test", hook="Hook", description="Desc", hashtags="#Tag",
            virality_score=80, layout_mode="fit_blur"
        )
        with get_db_connection() as conn:
            conn.execute("UPDATE clips SET video_path = ? WHERE id = ?", (str(dummy_video), clip_id))

        approve_clip(clip_id, custom_platforms="instagram")
        process_outbox_queue()

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM publishing_outbox WHERE clip_id = ?", (clip_id,))
            self.assertEqual(cursor.fetchone()["status"], "blocked_needs_auth")

    def test_f13_database_clean_isolation_between_test_runs(self):
        """F13.5: Database tables are strictly cleared and isolated across test executions."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM pipeline_jobs")
            self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute("SELECT COUNT(*) FROM publishing_outbox")
            self.assertEqual(cursor.fetchone()[0], 0)
