"""Tier 1: Feature Coverage — Ingestion, Transport & Pairing Security.
Covers:
- Feature 1: Pairing Security Hardening (>= 5 tests)
- Feature 2: Cryptographic Verification & Pruning (>= 5 tests)
- Feature 3: UDP & Subnet Auto-Discovery (>= 5 tests)
"""
import os
import time
import socket
import hashlib
from pathlib import Path
from unittest.mock import patch, MagicMock

from tests.e2e.e2e_base import DispatchE2EBaseTestCase
from dispatch.sync.receiver import get_pairing_pin, get_auth_token
from dispatch.config import INCOMING_DIR, PROCESSING_DIR
from dispatch.db import get_db_connection, register_chunk
from dispatch.transport.discovery import DiscoveryBeaconServer, BEACON_MAGIC
from dispatch.transport.transport_manager import TransportManager, NetworkPolicy, EndpointCandidate
from dispatch_mobile.segmenter import RecordingSessionManager
from dispatch_mobile.retention import cleanup_verified_segments
from dispatch_mobile.db import get_mobile_db


class TestTier1IngestionSecurity(DispatchE2EBaseTestCase):
    """Tier 1 Feature Coverage: Features 1, 2, and 3."""

    # =========================================================================
    # FEATURE 1: Pairing Security Hardening
    # =========================================================================

    def test_f01_pairing_unauthenticated_lan_forbidden(self):
        """F1.1: Unauthenticated request from non-localhost LAN client must receive HTTP 403."""
        # Simulate request with external client IP
        response = self.client.get(
            "/api/sync/pairing/config",
            headers={"client-ip": "192.168.1.105"}
        )
        # TestClient uses 127.0.0.1 by default; when passing an explicit non-auth request without PIN/token
        # and mocking client host to external IP, it must be forbidden:
        with patch("fastapi.Request.client", new_callable=MagicMock) as mock_client:
            mock_client.host = "192.168.1.105"
            res = self.client.get("/api/sync/pairing/config")
            self.assertIn(res.status_code, (401, 403))
            self.assertIn("detail", res.json())
            self.assertNotIn("auth_token", res.json())

    def test_f01_pairing_authenticated_with_pin(self):
        """F1.2: Supplying valid 6-digit PIN via query parameter permits access to pairing config."""
        pin = get_pairing_pin()
        self.assertEqual(len(pin), 6)
        self.assertTrue(pin.isdigit())

        with patch("fastapi.Request.client", new_callable=MagicMock) as mock_client:
            mock_client.host = "192.168.1.105"
            res = self.client.get(f"/api/sync/pairing/config?pin={pin}")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertIn("auth_token", data)
            self.assertIn("connection_string", data)
            self.assertEqual(data["pairing_pin"], pin)

    def test_f01_pairing_authenticated_with_header_token(self):
        """F1.3: Supplying valid x-auth-token header permits access even from external IP."""
        token = get_auth_token()
        with patch("fastapi.Request.client", new_callable=MagicMock) as mock_client:
            mock_client.host = "192.168.1.105"
            res = self.client.get("/api/sync/pairing/config", headers={"x-auth-token": token})
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["auth_token"], token)
            self.assertIn("dispatch://pair", data["connection_string"])

    def test_f01_pairing_invalid_credentials_rejected(self):
        """F1.4: Invalid PIN or wrong token receives 403 Forbidden."""
        with patch("fastapi.Request.client", new_callable=MagicMock) as mock_client:
            mock_client.host = "192.168.1.105"
            res_bad_pin = self.client.get("/api/sync/pairing/config?pin=000000")
            self.assertIn(res_bad_pin.status_code, (401, 403))

            res_bad_token = self.client.get(
                "/api/sync/pairing/config",
                headers={"x-auth-token": "completely_bogus_token"}
            )
            self.assertIn(res_bad_token.status_code, (401, 403))

    def test_f01_pairing_localhost_access_allowed(self):
        """F1.5: Localhost connections (creator viewing PC web dashboard) are permitted without PIN."""
        res = self.client.get("/api/sync/pairing/config")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("lan_url", data)
        self.assertIn("pairing_pin", data)
        self.assertIn("auth_token", data)

    def test_f01_pairing_pin_generation_and_persistence(self):
        """F1.6: Pairing PIN generation is persistent across repeated invocations."""
        pin1 = get_pairing_pin()
        pin2 = get_pairing_pin()
        self.assertEqual(pin1, pin2)
        self.assertEqual(len(pin1), 6)

    # =========================================================================
    # FEATURE 2: Cryptographic Verification & Pruning
    # =========================================================================

    def test_f02_verify_chunk_matching_sha256_and_size(self):
        """F2.1: Matching SHA-256 and size returns verified=True and status=VERIFIED."""
        seg_id = f"seg_f02_match_{int(time.time() * 1000)}"
        payload = self.create_mock_payload_bytes(10240, b"V")
        sha256 = self.calculate_bytes_sha256(payload)

        # Write file into incoming
        file_path = INCOMING_DIR / f"{seg_id}.mp4"
        file_path.write_bytes(payload)

        try:
            res = self.client.get(
                f"/api/sync/verify-chunk?segment_id={seg_id}&sha256={sha256}&file_size=10240",
                headers={"x-auth-token": self.auth_token}
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertTrue(data["verified"])
            self.assertEqual(data["status"], "VERIFIED")
            self.assertEqual(data["size"], 10240)
        finally:
            file_path.unlink(missing_ok=True)

    def test_f02_verify_chunk_hash_mismatch_refused(self):
        """F2.2: Tampered payload returns verified=False and refusal status."""
        seg_id = f"seg_f02_tamper_{int(time.time() * 1000)}"
        payload = self.create_mock_payload_bytes(10240, b"G")
        genuine_sha256 = self.calculate_bytes_sha256(payload)
        corrupted_payload = self.create_mock_payload_bytes(10240, b"X")

        file_path = INCOMING_DIR / f"{seg_id}.mp4"
        file_path.write_bytes(corrupted_payload)

        try:
            res = self.client.get(
                f"/api/sync/verify-chunk?segment_id={seg_id}&sha256={genuine_sha256}&file_size=10240",
                headers={"x-auth-token": self.auth_token}
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertFalse(data["verified"])
            self.assertIn("mismatch", data.get("reason", "").lower() + data.get("status", "").lower())
        finally:
            file_path.unlink(missing_ok=True)

    def test_f02_verify_chunk_size_mismatch_refused(self):
        """F2.3: Incomplete or oversized payload returns verified=False."""
        seg_id = f"seg_f02_size_{int(time.time() * 1000)}"
        payload = self.create_mock_payload_bytes(5000, b"S")
        sha256 = self.calculate_bytes_sha256(payload)

        file_path = INCOMING_DIR / f"{seg_id}.mp4"
        file_path.write_bytes(payload)

        try:
            res = self.client.get(
                f"/api/sync/verify-chunk?segment_id={seg_id}&sha256={sha256}&file_size=10000",
                headers={"x-auth-token": self.auth_token}
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertFalse(data["verified"])
            self.assertEqual(data["reason"], "size_mismatch")
        finally:
            file_path.unlink(missing_ok=True)

    def test_f02_verify_chunk_nonexistent_returns_missing(self):
        """F2.4: Querying proof for non-existent segment returns verified=False, status=MISSING."""
        res = self.client.get(
            "/api/sync/verify-chunk?segment_id=seg_does_not_exist&sha256=abcdef123456&file_size=1000",
            headers={"x-auth-token": self.auth_token}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data["verified"])
        self.assertEqual(data["status"], "MISSING")

    def test_f02_mobile_retention_prunes_only_verified_segments(self):
        """F2.5: Phone retention deletes VERIFIED_BY_LAPTOP segments and strictly preserves unverified."""
        manager = RecordingSessionManager(storage_dir=self.temp_dir)
        sess_id = manager.start_session(notes="Retention Test")

        # Segment 1 is started automatically by start_session
        seg1_id = f"{sess_id}_seg_0001"
        seg1_path = self.temp_dir / f"{seg1_id}.mp4"
        seg1_tmp = manager.current_tmp_path
        seg1_tmp.write_bytes(b"SEGMENT_1_TEST_BYTES")

        # Roll to Segment 2 (finalizes segment 1)
        manager.start_next_segment()
        seg2_id = f"{sess_id}_seg_0002"
        seg2_path = self.temp_dir / f"{seg2_id}.mp4"
        seg2_tmp = manager.current_tmp_path
        seg2_tmp.write_bytes(b"SEGMENT_2_TEST_BYTES")
        manager.finalize_current_segment()

        self.assertTrue(seg1_path.exists())
        self.assertTrue(seg2_path.exists())

        # Without verification, cleanup deletes 0 files
        deleted_count = cleanup_verified_segments()
        self.assertEqual(deleted_count, 0)
        self.assertTrue(seg1_path.exists())
        self.assertTrue(seg2_path.exists())

        # Mark only Segment 1 as verified
        with get_mobile_db() as conn:
            conn.execute(
                "UPDATE mobile_segments SET status = 'VERIFIED_BY_LAPTOP' WHERE segment_id = ?",
                (seg1_id,)
            )

        deleted_count = cleanup_verified_segments()
        self.assertEqual(deleted_count, 1)
        self.assertFalse(seg1_path.exists(), "Verified segment 1 should have been deleted")
        self.assertTrue(seg2_path.exists(), "Unverified segment 2 must be preserved")

    def test_f02_verify_chunk_requires_authentication(self):
        """F2.6: verify-chunk endpoint enforces x-auth-token presence."""
        res = self.client.get(
            "/api/sync/verify-chunk?segment_id=seg_test&sha256=1234&file_size=100"
        )
        self.assertEqual(res.status_code, 401)

    # =========================================================================
    # FEATURE 3: UDP & Subnet Auto-Discovery
    # =========================================================================

    def test_f03_discovery_beacon_payload_structure(self):
        """F3.1: UDP Discovery server constructs announce payload matching specification."""
        server = DiscoveryBeaconServer(port=8765)
        payload = server._get_payload()
        self.assertEqual(payload["magic"], BEACON_MAGIC)
        self.assertEqual(payload["service"], "dispatch")
        self.assertIn("lan_url", payload)
        self.assertIn("ip", payload)
        self.assertIn("port", payload)
        self.assertNotIn("auth_token", payload, "UDP discovery beacon must not leak auth_token")
        self.assertTrue(payload.get("pairing_required"))

    def test_f03_transport_manager_selects_lowest_latency_healthy_route(self):
        """F3.2: Selects LAN route when LAN latency is faster than Tailscale."""
        manager = TransportManager(
            auth_token=self.auth_token,
            lan_url="http://192.168.1.50:8765",
            tailscale_url="http://laptop.tailscale.net:8765"
        )

        def mock_get(url, **kwargs):
            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.json.return_value = {"status": "online"}
            return mock_res

        with patch("requests.get", side_effect=mock_get):
            best = manager.select_best_endpoint()
            self.assertIsNotNone(best)
            self.assertEqual(best.name, "lan")
            self.assertEqual(best.url, "http://192.168.1.50:8765")

    def test_f03_transport_manager_failover_when_primary_offline(self):
        """F3.3: Automatically fails over to Tailscale when LAN route is unreachable."""
        manager = TransportManager(
            auth_token=self.auth_token,
            lan_url="http://192.168.1.50:8765",
            tailscale_url="http://laptop.tailscale.net:8765"
        )

        def mock_failover(url, **kwargs):
            if "192.168" in url:
                raise ConnectionError("No route to LAN host")
            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.json.return_value = {"status": "online"}
            return mock_res

        with patch("requests.get", side_effect=mock_failover):
            best = manager.select_best_endpoint()
            self.assertIsNotNone(best)
            self.assertEqual(best.name, "tailscale")
            self.assertEqual(best.url, "http://laptop.tailscale.net:8765")

    def test_f03_transport_manager_offline_all_routes_returns_none(self):
        """F3.4: When all endpoints are unreachable, manager returns None for safe local queuing."""
        manager = TransportManager(
            auth_token=self.auth_token,
            lan_url="http://192.168.1.50:8765",
            tailscale_url="http://laptop.tailscale.net:8765"
        )

        with patch("requests.get", side_effect=ConnectionError("Network down")):
            best = manager.select_best_endpoint()
            self.assertIsNone(best)

    def test_f03_transport_network_policy_evaluation(self):
        """F3.5: NetworkPolicy enforces Wi-Fi only, cellular gating, and blocks roaming."""
        manager = TransportManager(auth_token=self.auth_token)

        # Disconnected
        allowed, reason = manager.evaluate_network_policy("none")
        self.assertFalse(allowed)

        # Wi-Fi allowed
        allowed, reason = manager.evaluate_network_policy("wifi")
        self.assertTrue(allowed)

        # Roaming blocked
        allowed, reason = manager.evaluate_network_policy("wifi", is_roaming=True)
        self.assertFalse(allowed)
        self.assertIn("roaming", reason.lower())

        # Cellular blocked by default
        allowed, reason = manager.evaluate_network_policy("cellular")
        self.assertFalse(allowed)

    def test_f03_transport_auth_headers_contain_device_token(self):
        """F3.6: get_auth_headers() includes paired token and client identification."""
        manager = TransportManager(auth_token=self.auth_token)
        headers = manager.get_auth_headers()
        self.assertEqual(headers["X-Dispatch-Device-Token"], self.auth_token)
        self.assertEqual(headers["X-Dispatch-Client"], "DispatchMobile/1.0")
