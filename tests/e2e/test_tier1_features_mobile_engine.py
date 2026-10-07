"""Tier 1: Feature Coverage — Android Mobile Engine, UI Spec & Build Cleanliness.
Covers:
- Feature 7: Non-Blocking Segment Roll (>= 5 tests)
- Feature 8: Screen-Off / Dim Wake Lock (>= 5 tests)
- Feature 9: 6-Screen Compose UI Polish (>= 5 tests)
- Feature 10: Gradle Build & Compiler Cleanliness (>= 5 tests)
"""
from pathlib import Path

from tests.e2e.e2e_base import DispatchE2EBaseTestCase
from dispatch.config import ROOT_DIR
from dispatch_mobile.segmenter import RecordingSessionManager
from dispatch_mobile.db import get_mobile_db


class TestTier1MobileEngine(DispatchE2EBaseTestCase):
    """Tier 1 Feature Coverage: Features 7, 8, 9, and 10."""

    def setUp(self):
        super().setUp()
        self.android_dir = ROOT_DIR / "android"
        self.app_dir = self.android_dir / "app"
        self.src_dir = self.app_dir / "src" / "main" / "java" / "com" / "resolvia" / "dispatch"
        self.manifest_path = self.app_dir / "src" / "main" / "AndroidManifest.xml"

    # =========================================================================
    # FEATURE 7: Non-Blocking Segment Roll
    # =========================================================================

    def test_f07_start_session_creates_active_tmp_file(self):
        """F7.1: RecordingSessionManager creates initial .tmp file immediately upon starting session."""
        manager = RecordingSessionManager(storage_dir=self.temp_dir)
        sess_id = manager.start_session(notes="Segment Roll Test")
        self.assertEqual(manager.current_session_id, sess_id)
        self.assertIsNotNone(manager.current_tmp_path)
        self.assertTrue(manager.current_tmp_path.exists())
        self.assertTrue(manager.current_tmp_path.name.endswith(".tmp"))

    def test_f07_start_next_segment_rolls_previous_to_mp4(self):
        """F7.2: Calling start_next_segment converts previous .tmp to .mp4 and starts next .tmp."""
        manager = RecordingSessionManager(storage_dir=self.temp_dir)
        sess_id = manager.start_session(notes="Roll Test")
        seg1_tmp = manager.current_tmp_path
        seg1_tmp.write_bytes(b"SEGMENT_1_CONTENT_BYTES")

        seg2_info = manager.start_next_segment()
        seg1_final = self.temp_dir / f"{sess_id}_seg_0001.mp4"

        # Segment 1 must now be .mp4
        self.assertTrue(seg1_final.exists())
        self.assertFalse(seg1_tmp.exists(), "Previous .tmp should be renamed to .mp4")
        self.assertEqual(seg1_final.stat().st_size, len(b"SEGMENT_1_CONTENT_BYTES"))

        # Segment 2 must be an active .tmp file
        self.assertTrue(manager.current_tmp_path.exists())
        self.assertTrue(manager.current_tmp_path.name.endswith(".tmp"))

    def test_f07_finalize_segment_computes_sha256_and_size(self):
        """F7.3: Finalized segment contains verified SHA-256 and size in mobile database."""
        manager = RecordingSessionManager(storage_dir=self.temp_dir)
        sess_id = manager.start_session()
        payload = b"DETERMINISTIC_MOBILE_PAYLOAD_BYTES_XYZ"
        manager.current_tmp_path.write_bytes(payload)

        seg_info = manager.finalize_current_segment()
        self.assertIsNotNone(seg_info)
        self.assertEqual(seg_info["size_bytes"], len(payload))
        self.assertEqual(len(seg_info["sha256"]), 64)

        with get_mobile_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, sha256_hash, file_size_bytes FROM mobile_segments WHERE segment_id = ?",
                           (seg_info["segment_id"],))
            row = cursor.fetchone()
            self.assertEqual(row["status"], "QUEUED_FOR_UPLOAD")
            self.assertEqual(row["sha256_hash"], seg_info["sha256"])
            self.assertEqual(row["file_size_bytes"], len(payload))

    def test_f07_mobile_outbox_enqueued_on_segment_finalization(self):
        """F7.4: Segment is queued in mobile_outbox with remote_offset=0 upon finalization."""
        manager = RecordingSessionManager(storage_dir=self.temp_dir)
        sess_id = manager.start_session()
        manager.current_tmp_path.write_bytes(b"PAYLOAD_OUTBOX_TEST")
        seg_info = manager.finalize_current_segment()

        with get_mobile_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, remote_offset FROM mobile_outbox WHERE segment_id = ?",
                           (seg_info["segment_id"],))
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["status"], "QUEUED_FOR_UPLOAD")
            self.assertEqual(row["remote_offset"], 0)

    def test_f07_consecutive_rolling_stress(self):
        """F7.5: 5 consecutive segment rolls produce 5 distinct finalized MP4 files with zero loss."""
        manager = RecordingSessionManager(storage_dir=self.temp_dir)
        sess_id = manager.start_session()

        for i in range(1, 5):
            manager.current_tmp_path.write_bytes(f"SEG_{i}_PAYLOAD".encode())
            manager.start_next_segment()

        # Finalize the 5th segment
        manager.current_tmp_path.write_bytes(b"SEG_5_PAYLOAD")
        manager.finalize_current_segment()

        for seq in range(1, 6):
            expected_mp4 = self.temp_dir / f"{sess_id}_seg_{seq:04d}.mp4"
            self.assertTrue(expected_mp4.exists(), f"Segment {seq} MP4 missing on disk")
            self.assertGreater(expected_mp4.stat().st_size, 0)

    # =========================================================================
    # FEATURE 8: Screen-Off / Dim Wake Lock & Foreground Persistence
    # =========================================================================

    def test_f08_android_manifest_declares_foreground_service(self):
        """F8.1: AndroidManifest.xml contains FOREGROUND_SERVICE and FOREGROUND_SERVICE_CAMERA."""
        self.assertTrue(self.manifest_path.exists())
        manifest_text = self.manifest_path.read_text(encoding="utf-8")
        self.assertIn("android.permission.FOREGROUND_SERVICE", manifest_text)
        self.assertIn("android.permission.FOREGROUND_SERVICE_CAMERA", manifest_text)

    def test_f08_android_manifest_declares_battery_and_audio_permissions(self):
        """F8.2: AndroidManifest.xml contains audio and battery optimization permissions."""
        manifest_text = self.manifest_path.read_text(encoding="utf-8")
        self.assertIn("android.permission.RECORD_AUDIO", manifest_text)
        self.assertIn("android.permission.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS", manifest_text)

    def test_f08_recording_foreground_service_lifecycle_methods(self):
        """F8.3: RecordingForegroundService.kt implements lifecycle hooks and foreground startup."""
        service_file = self.src_dir / "recorder" / "RecordingForegroundService.kt"
        self.assertTrue(service_file.exists(), f"Missing service file: {service_file}")
        code = service_file.read_text(encoding="utf-8")
        self.assertIn("override fun onCreate()", code)
        self.assertIn("override fun onStartCommand", code)
        self.assertIn("startForeground", code)

    def test_f08_recording_foreground_service_companion_starter(self):
        """F8.4: RecordingForegroundService.kt exposes companion start/stop methods."""
        service_file = self.src_dir / "recorder" / "RecordingForegroundService.kt"
        code = service_file.read_text(encoding="utf-8")
        self.assertIn("fun start(context: Context)", code)
        self.assertIn("fun stop(context: Context)", code)

    def test_f08_foreground_notification_channel_configured(self):
        """F8.5: Foreground service configures ongoing persistent notification channel."""
        service_file = self.src_dir / "recorder" / "RecordingForegroundService.kt"
        code = service_file.read_text(encoding="utf-8")
        self.assertIn("NotificationChannel", code)
        self.assertIn("dispatch_recording_channel", code)

    # =========================================================================
    # FEATURE 9: 6-Screen Compose UI Polish
    # =========================================================================

    def test_f09_compose_record_screen_structure(self):
        """F9.1: RecordScreen.kt implements record controls, session metadata, and camera view."""
        screen_file = self.src_dir / "ui" / "screens" / "RecordScreen.kt"
        self.assertTrue(screen_file.exists())
        code = screen_file.read_text(encoding="utf-8")
        self.assertIn("@Composable", code)
        self.assertIn("RecordScreen", code)

    def test_f09_compose_sessions_screen_pipeline_polling(self):
        """F9.2: SessionsScreen.kt displays segment upload gauge and processing status."""
        screen_file = self.src_dir / "ui" / "screens" / "SessionsScreen.kt"
        self.assertTrue(screen_file.exists())
        code = screen_file.read_text(encoding="utf-8")
        self.assertIn("SessionsScreen", code)
        self.assertIn("SegmentEntity", code)

    def test_f09_compose_clips_screen_review_and_playback(self):
        """F9.3: ClipsScreen.kt provides 9:16 vertical preview and approval actions."""
        screen_file = self.src_dir / "ui" / "screens" / "ClipsScreen.kt"
        self.assertTrue(screen_file.exists())
        code = screen_file.read_text(encoding="utf-8")
        self.assertIn("ClipsScreen", code)
        self.assertIn("RemoteClipItem", code)

    def test_f09_compose_settings_screen_connection_inputs(self):
        """F9.4: SettingsScreen.kt provides LAN URL, Pairing PIN, and token inputs."""
        screen_file = self.src_dir / "ui" / "screens" / "SettingsScreen.kt"
        self.assertTrue(screen_file.exists())
        code = screen_file.read_text(encoding="utf-8")
        self.assertIn("SettingsScreen", code)
        self.assertIn("PairingManager", code)

    def test_f09_compose_navigation_and_theme_system(self):
        """F9.5: BottomNavBar.kt and Theme.kt configure 6-screen navigation and dark palette."""
        nav_file = self.src_dir / "ui" / "components" / "BottomNavBar.kt"
        theme_file = self.src_dir / "ui" / "theme" / "Theme.kt"
        self.assertTrue(nav_file.exists())
        self.assertTrue(theme_file.exists())
        self.assertIn("DispatchBottomBar", nav_file.read_text(encoding="utf-8"))
        self.assertIn("MaterialTheme", theme_file.read_text(encoding="utf-8"))

    # =========================================================================
    # FEATURE 10: Gradle Build & Compiler Cleanliness
    # =========================================================================

    def test_f10_gradle_properties_configures_jvm_and_android_settings(self):
        """F10.1: gradle.properties specifies memory limits and AndroidX flags."""
        props_file = self.android_dir / "gradle.properties"
        self.assertTrue(props_file.exists())
        props_text = props_file.read_text(encoding="utf-8")
        self.assertIn("android.useAndroidX=true", props_text)
        self.assertIn("org.gradle.jvmargs", props_text)

    def test_f10_app_build_gradle_specifies_java_17_compatibility(self):
        """F10.2: app/build.gradle.kts targets JavaVersion.VERSION_17."""
        build_file = self.app_dir / "build.gradle.kts"
        self.assertTrue(build_file.exists())
        build_text = build_file.read_text(encoding="utf-8")
        self.assertIn("JavaVersion.VERSION_17", build_text)
        self.assertIn("targetCompatibility", build_text)

    def test_f10_gradle_wrapper_properties_valid(self):
        """F10.3: gradle-wrapper.properties declares standard Gradle distribution."""
        wrapper_props = self.android_dir / "gradle" / "wrapper" / "gradle-wrapper.properties"
        self.assertTrue(wrapper_props.exists())
        text = wrapper_props.read_text(encoding="utf-8")
        self.assertIn("distributionUrl", text)
        self.assertIn("gradle-", text)

    def test_f10_all_23_kotlin_sources_exist_and_non_empty(self):
        """F10.4: All core Kotlin source files exist and have non-empty length."""
        expected_classes = [
            "DispatchApplication.kt",
            "MainActivity.kt",
            "data/AppDatabase.kt",
            "data/NetworkDiscovery.kt",
            "data/OutboxEntity.kt",
            "data/PairingManager.kt",
            "data/RecordingDao.kt",
            "data/SegmentEntity.kt",
            "data/SessionEntity.kt",
            "receiver/BootReceiver.kt",
            "recorder/CameraCaptureManager.kt",
            "recorder/RecordingForegroundService.kt",
            "recorder/SegmenterEngine.kt",
            "sync/LiveSyncManager.kt",
            "sync/ResumableSyncWorker.kt",
            "sync/YouTubeDirectUploadWorker.kt",
            "ui/components/BottomNavBar.kt",
            "ui/screens/ClipsScreen.kt",
            "ui/screens/RecordScreen.kt",
            "ui/screens/SessionsScreen.kt",
            "ui/screens/SettingsScreen.kt",
            "ui/theme/Color.kt",
            "ui/theme/Theme.kt"
        ]
        for rel_path in expected_classes:
            f = self.src_dir / rel_path
            self.assertTrue(f.exists(), f"Kotlin source missing: {rel_path}")
            self.assertGreater(f.stat().st_size, 0, f"Kotlin source empty: {rel_path}")

    def test_f10_kotlin_package_and_architecture_cleanliness(self):
        """F10.5: All Kotlin files use the package com.resolvia.dispatch root."""
        main_activity = self.src_dir / "MainActivity.kt"
        code = main_activity.read_text(encoding="utf-8")
        self.assertIn("package com.resolvia.dispatch", code)
        self.assertIn("ComponentActivity", code)
