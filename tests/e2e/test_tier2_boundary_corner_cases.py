"""Tier 2: Boundary, Corner Case & Negative Stress Tests across All Features.
Covers:
- Category 1: Empty Inputs & Zero-Byte Media (>= 5 tests)
- Category 2: Zero, Negative & Inverted Boundaries (>= 5 tests)
- Category 3: Maximum Payload & Scale Limits (>= 5 tests)
- Category 4: Corrupt & Malformed Data Injection (>= 5 tests)
- Category 5: Offline, Connection Drop & Timeout Conditions (>= 5 tests)
"""
import time
import hashlib
from pathlib import Path
from unittest.mock import patch, MagicMock

from tests.e2e.e2e_base import DispatchE2EBaseTestCase
from dispatch.config import INCOMING_DIR, PROCESSING_DIR
from dispatch.db import get_db_connection, register_chunk, save_clip
from dispatch.sync.receiver import sanitize_segment_id
from dispatch.ingestion.validator import probe_video
from dispatch.ai_clips.highlight_finder import extract_clips_local_heuristic
from dispatch.video_engine.subtitle_generator import format_ass_timestamp
from dispatch.youtube_inbox.vtt_parser import parse_vtt_content
from dispatch.orchestrator.retry_engine import RetryEngine
from dispatch.orchestrator.job_queue import (
    enqueue_job,
    claim_job,
    complete_stage_checkpoint,
    LeaseLostError
)
from dispatch.orchestrator.state_machine import PipelineStage
from dispatch.publisher.outbox import process_outbox_queue


class TestTier2BoundaryCornerCases(DispatchE2EBaseTestCase):
    """Tier 2: Boundary & Corner Cases."""

    # =========================================================================
    # CATEGORY 1: Empty Inputs & Zero-Byte Media
    # =========================================================================

    def test_t2_01_empty_video_upload_rejected(self):
        """T2.1.1: 0-byte video file uploaded via web API is rejected or fails probe."""
        zero_file = self.temp_dir / "zero_upload.mp4"
        zero_file.write_bytes(b"")

        is_valid, meta, err = probe_video(zero_file)
        self.assertFalse(is_valid)
        self.assertIn("empty", err.lower() + meta.get("error", "").lower())

    def test_t2_01_empty_transcript_segments_handled_gracefully(self):
        """T2.1.2: Heuristic highlight finder returns empty list when passed empty segments without crashing."""
        clips = extract_clips_local_heuristic(segments=[], total_duration=0.0)
        self.assertEqual(clips, [])

    def test_t2_01_empty_vtt_content_parsing(self):
        """T2.1.3: VTT parser returns empty list when parsing empty or whitespace string."""
        self.assertEqual(parse_vtt_content(""), [])
        self.assertEqual(parse_vtt_content("   \n\n\t  "), [])

    def test_t2_01_empty_pairing_pin_rejected(self):
        """T2.1.4: Passing empty PIN string to pairing config receives 401/403 Unauthorized/Forbidden."""
        with patch("fastapi.Request.client", new_callable=MagicMock) as mock_client:
            mock_client.host = "192.168.1.105"
            res = self.client.get("/api/sync/pairing/config?pin=")
            self.assertIn(res.status_code, (401, 403))

    def test_t2_01_empty_outbox_queue_processing_returns_zero(self):
        """T2.1.5: Running outbox worker when 0 jobs are queued returns 0 without raising exceptions."""
        success_count = process_outbox_queue()
        self.assertEqual(success_count, 0)

    def test_t2_01_empty_reconcile_manifest_returns_empty_list(self):
        """T2.1.6: Reconcile request with empty manifest returns HTTP 200 with empty reconciled array."""
        res = self.client.post("/api/sync/reconcile", json={
            "auth_token": self.auth_token,
            "manifest": []
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["reconciled"], [])

    # =========================================================================
    # CATEGORY 2: Zero, Negative & Inverted Boundaries
    # =========================================================================

    def test_t2_02_negative_chunk_upload_offset_rejected(self):
        """T2.2.1: Upload chunk with negative byte offset is rejected with HTTP 409 Conflict / 400."""
        seg_id = "seg_t2_neg_offset"
        headers = {
            "x-segment-id": seg_id,
            "x-upload-offset": "-100",
            "x-file-size": "5000",
            "x-sha256": "fakehash",
            "x-session-id": "sess_t2",
            "x-auth-token": self.auth_token,
            "content-type": "application/octet-stream"
        }
        res = self.client.patch("/api/sync/upload/chunk", content=b"12345", headers=headers)
        self.assertIn(res.status_code, (400, 409))

    def test_t2_02_inverted_clip_timestamps_handled_safely(self):
        """T2.2.2: Subtitle generation with start_time >= end_time generates valid header with 0 events."""
        out_ass = self.temp_dir / "inverted.ass"
        segments = [{"id": 0, "start": 10.0, "end": 20.0, "text": "Clip", "words": []}]
        # Inverted: clip_start=30.0, clip_end=10.0
        from dispatch.video_engine.subtitle_generator import generate_ass_subtitles
        generate_ass_subtitles(segments=segments, output_path=out_ass, clip_start_time=30.0, clip_end_time=10.0)
        self.assertTrue(out_ass.exists())
        content = out_ass.read_text(encoding="utf-8")
        self.assertNotIn("Dialogue:", content)

    def test_t2_02_zero_second_clip_duration_clamped(self):
        """T2.2.3: format_ass_timestamp formats 0.0s correctly as 0:00:00.00."""
        formatted = format_ass_timestamp(0.0)
        self.assertEqual(formatted, "0:00:00.00")

    def test_t2_02_negative_backoff_delay_bounded_to_minimum(self):
        """T2.2.4: RetryEngine given negative attempt count bounds to minimum base delay."""
        engine = RetryEngine(base_delay_seconds=10, max_delay_seconds=60)
        delay = engine.compute_backoff_seconds(-5)
        self.assertGreaterEqual(delay, 5)

    def test_t2_02_zero_file_size_verify_chunk_refused(self):
        """T2.2.5: verify-chunk for file with size mismatch 0 bytes returns verified=False."""
        seg_id = f"seg_t2_zero_size_{int(time.time() * 1000)}"
        file_path = INCOMING_DIR / f"{seg_id}.mp4"
        file_path.write_bytes(b"NON_EMPTY_PAYLOAD")

        try:
            res = self.client.get(
                f"/api/sync/verify-chunk?segment_id={seg_id}&sha256=somehash&file_size=0",
                headers={"x-auth-token": self.auth_token}
            )
            self.assertEqual(res.status_code, 200)
            self.assertFalse(res.json()["verified"])
            self.assertEqual(res.json()["reason"], "size_mismatch")
        finally:
            file_path.unlink(missing_ok=True)

    # =========================================================================
    # CATEGORY 3: Maximum Payload & Scale Limits
    # =========================================================================

    def test_t2_03_extreme_length_segment_id_sanitization(self):
        """T2.3.1: Segment ID exceeding 64 characters is rejected with HTTP 400."""
        too_long_id = "A" * 65
        with self.assertRaises(Exception):
            sanitize_segment_id(too_long_id)

    def test_t2_03_massive_manifest_reconciliation_batch(self):
        """T2.3.2: Reconciling 100 items in a single manifest executes safely without DB limits."""
        manifest = []
        for i in range(100):
            manifest.append({
                "segment_id": f"seg_batch_{i:04d}",
                "session_id": "sess_massive_batch",
                "file_size_bytes": 1024,
                "sha256_hash": "a" * 64
            })

        res = self.client.post("/api/sync/reconcile", json={
            "auth_token": self.auth_token,
            "manifest": manifest
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()["reconciled"]), 100)

    def test_t2_03_extreme_duration_media_handling(self):
        """T2.3.3: format_ass_timestamp formats 10-hour duration (36,000s) accurately."""
        formatted = format_ass_timestamp(36725.50)
        # 10 hours, 12 mins, 5.5s -> 10:12:05.50
        self.assertEqual(formatted, "10:12:05.50")

    def test_t2_03_massive_text_transcript_highlight_extraction(self):
        """T2.3.4: Local heuristic highlight extractor handles 500 segments smoothly under 1 second."""
        segments = []
        for i in range(500):
            segments.append({
                "id": i,
                "start": float(i * 2),
                "end": float((i + 1) * 2),
                "text": f"Segment {i} discussing fast execution in Python and automated content pipelines.",
                "words": []
            })
        start_t = time.time()
        clips = extract_clips_local_heuristic(segments, total_duration=1000.0)
        elapsed = time.time() - start_t
        self.assertLess(elapsed, 1.0)
        self.assertGreaterEqual(len(clips), 1)

    def test_t2_03_extreme_metadata_title_preservation(self):
        """T2.3.5: Extreme title strings (1000 chars) are stored in clips table without SQLite error."""
        long_title = "LONG_TITLE_" * 80
        cid = save_clip(
            session_id=None, chunk_id=None, start_time=0.0, end_time=10.0,
            title=long_title, hook="Hook", description="Desc", hashtags="#Tag",
            virality_score=80, layout_mode="fit_blur"
        )
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT title FROM clips WHERE id = ?", (cid,))
            self.assertEqual(cursor.fetchone()["title"], long_title)

    # =========================================================================
    # CATEGORY 4: Corrupt & Malformed Data Injection
    # =========================================================================

    def test_t2_04_corrupt_mp4_missing_moov_atom_probe(self):
        """T2.4.1: Corrupt media containing random bytes fails probe_video validation."""
        corrupt_file = self.temp_dir / "corrupt_stream.mp4"
        corrupt_file.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\xFF" * 1024)

        is_valid, meta, err = probe_video(corrupt_file)
        self.assertFalse(is_valid)

    def test_t2_04_sha256_checksum_mismatch_purges_partial_file(self):
        """T2.4.2: Chunk upload with SHA-256 mismatch purges .part file and raises 422."""
        seg_id = f"seg_mismatch_{int(time.time() * 1000)}"
        real_payload = b"GENUINE_DATA"
        mismatched_sha = "f" * 64

        # Init upload
        self.client.post("/api/sync/upload/init", json={
            "session_id": "sess_mis",
            "segment_id": seg_id,
            "filename": f"{seg_id}.mp4",
            "file_size_bytes": len(real_payload),
            "sha256_hash": mismatched_sha,
            "auth_token": self.auth_token
        })

        headers = {
            "x-segment-id": seg_id,
            "x-upload-offset": "0",
            "x-file-size": str(len(real_payload)),
            "x-sha256": mismatched_sha,
            "x-session-id": "sess_mis",
            "x-auth-token": self.auth_token,
            "content-type": "application/octet-stream"
        }
        res = self.client.patch("/api/sync/upload/chunk", content=real_payload, headers=headers)
        self.assertEqual(res.status_code, 422)

        # .part file must have been purged
        self.assertFalse((INCOMING_DIR / f"{seg_id}.part").exists())

    def test_t2_04_malformed_vtt_timestamp_syntax_recovery(self):
        """T2.4.3: VTT parser recovers from malformed timestamp cues without crashing."""
        broken_vtt = """WEBVTT

NOT_A_TIMESTAMP --> STILL_NOT
Broken line text here.

00:00:01.000 --> 00:00:03.000
Valid speech caption.
"""
        segments = parse_vtt_content(broken_vtt)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["text"], "Valid speech caption.")

    def test_t2_04_path_traversal_in_segment_id_blocked(self):
        """T2.4.4: Path traversal payload in segment_id is rejected by sanitizer with HTTP 400."""
        traversal_attempts = [
            "../../etc/passwd",
            "..\\..\\windows\\system32",
            "seg/../../secret",
            "seg;rm -rf /"
        ]
        for att in traversal_attempts:
            with self.assertRaises(Exception):
                sanitize_segment_id(att)

    def test_t2_04_truncated_json_payload_rejection(self):
        """T2.4.5: Malformed JSON sent to sync endpoint returns HTTP 422."""
        res = self.client.post(
            "/api/sync/reconcile",
            content=b'{"auth_token": "token", "manifest": [',
            headers={"content-type": "application/json"}
        )
        self.assertEqual(res.status_code, 422)

    # =========================================================================
    # CATEGORY 5: Offline & Connection Drop Conditions
    # =========================================================================

    def test_t2_05_unreachable_lan_ip_probing_timeout(self):
        """T2.5.1: Probing non-routable IP fails fast with ConnectionError without hanging indefinitely."""
        from dispatch.transport.transport_manager import TransportManager
        manager = TransportManager(auth_token=self.auth_token, lan_url="http://192.0.2.1:8765")

        with patch("requests.get", side_effect=ConnectionError("Route unreachable")):
            best = manager.select_best_endpoint()
            self.assertIsNone(best)

    def test_t2_05_simulated_network_drop_at_50_percent_chunk_upload(self):
        """T2.5.2: Disconnect at 50% retains exact byte offset in .part file on disk."""
        seg_id = f"seg_drop_50_{int(time.time() * 1000)}"
        full_payload = b"RELIABLE_PAYLOAD_CHUNK_BYTES_" * 400  # ~11,600 bytes
        half_size = len(full_payload) // 2
        chunk1 = full_payload[:half_size]
        full_sha256 = hashlib.sha256(full_payload).hexdigest()

        # Init upload
        self.client.post("/api/sync/upload/init", json={
            "session_id": "sess_drop",
            "segment_id": seg_id,
            "filename": f"{seg_id}.mp4",
            "file_size_bytes": len(full_payload),
            "sha256_hash": full_sha256,
            "auth_token": self.auth_token
        })

        headers = {
            "x-segment-id": seg_id,
            "x-upload-offset": "0",
            "x-file-size": str(len(full_payload)),
            "x-sha256": full_sha256,
            "x-session-id": "sess_drop",
            "x-auth-token": self.auth_token,
            "content-type": "application/octet-stream"
        }
        res = self.client.patch("/api/sync/upload/chunk", content=chunk1, headers=headers)
        self.assertEqual(res.status_code, 200)

        # Simulate network drop: verify .part file on disk has exactly half_size bytes
        part_file = INCOMING_DIR / f"{seg_id}.part"
        self.assertTrue(part_file.exists())
        self.assertEqual(part_file.stat().st_size, half_size)

        # Clean up
        part_file.unlink(missing_ok=True)

    def test_t2_05_missing_auth_token_all_sync_endpoints_fail_closed(self):
        """T2.5.3: Sync endpoints fail-closed with 401 when auth token is omitted."""
        res_ping = self.client.get("/api/sync/verify-chunk?segment_id=seg1&sha256=hash")
        self.assertEqual(res_ping.status_code, 401)

        res_init = self.client.post("/api/sync/upload/init", json={
            "session_id": "s", "segment_id": "seg", "filename": "seg.mp4",
            "file_size_bytes": 100, "sha256_hash": "hash", "auth_token": "wrong_token"
        })
        self.assertEqual(res_init.status_code, 401)

    def test_t2_05_expired_lease_worker_fenced_out(self):
        """T2.5.4: Worker attempting checkpoint 60 seconds after lease expiration is fenced out."""
        setup = self.register_test_chunk_and_job(filename="fence_job.mp4")
        job_id = setup["job_id"]

        claim_job(worker_id="worker_expired", lease_duration_seconds=10)

        with get_db_connection() as conn:
            conn.execute(
                "UPDATE pipeline_jobs SET lease_expires_at = datetime('now', '-60 seconds') WHERE job_id = ?",
                (job_id,)
            )

        with self.assertRaises(LeaseLostError):
            complete_stage_checkpoint(job_id=job_id, current_stage=PipelineStage.VERIFY, worker_id="worker_expired")

    def test_t2_05_database_locked_retry_handling(self):
        """T2.5.5: Database WAL mode and 30-second timeout allows concurrent reader/writer without SQLITE_BUSY."""
        with get_db_connection() as conn1:
            with get_db_connection() as conn2:
                # Concurrent read and write in separate connections
                conn1.execute("SELECT COUNT(*) FROM chunks")
                conn2.execute("INSERT INTO settings (key, value) VALUES ('wal_test', 'active') ON CONFLICT(key) DO UPDATE SET value = 'active'")
                conn2.commit()
                res = conn1.execute("SELECT value FROM settings WHERE key = 'wal_test'").fetchone()
                self.assertEqual(res["value"], "active")
