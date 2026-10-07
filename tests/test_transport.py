"""Unit test for Phase 6: Remote Connectivity & Transport Abstraction."""
import unittest
from unittest.mock import patch, MagicMock

from dispatch.transport.transport_manager import (
    TransportManager,
    EndpointCandidate,
    NetworkPolicy
)


class TestTransportManager(unittest.TestCase):
    def setUp(self):
        self.auth_token = "secret_paired_token_123"
        self.manager = TransportManager(
            auth_token=self.auth_token,
            lan_url="http://192.168.1.50:8765",
            tailscale_url="http://laptop.tailscale.net:8765"
        )

    def test_auth_headers(self):
        headers = self.manager.get_auth_headers()
        self.assertEqual(headers["X-Dispatch-Device-Token"], self.auth_token)
        self.assertIn("X-Dispatch-Client", headers)

    def test_network_policy_evaluation(self):
        # 1. Disconnected
        allowed, reason = self.manager.evaluate_network_policy("none")
        self.assertFalse(allowed)

        # 2. Wi-Fi allowed
        allowed, reason = self.manager.evaluate_network_policy("wifi")
        self.assertTrue(allowed)

        # 3. Roaming blocked
        allowed, reason = self.manager.evaluate_network_policy("wifi", is_roaming=True)
        self.assertFalse(allowed)
        self.assertIn("roaming", reason.lower())

        # 4. Cellular disallowed by default
        allowed, reason = self.manager.evaluate_network_policy("cellular")
        self.assertFalse(allowed)
        self.assertIn("cellular sync is disabled", reason.lower())

        # 5. Cellular allowed if policy enables it
        manager_cell = TransportManager(
            auth_token=self.auth_token,
            policy=NetworkPolicy(allow_cellular=True, wifi_only=False)
        )
        allowed, reason = manager_cell.evaluate_network_policy("cellular")
        self.assertTrue(allowed)

    def test_route_probing_and_failover(self):
        # Case 1: Both LAN and Tailscale online -> LAN preferred (priority 1)
        def mock_requests_get(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "online"}
            return mock_resp

        with patch("requests.get", side_effect=mock_requests_get):
            best = self.manager.select_best_endpoint()
            self.assertIsNotNone(best)
            self.assertEqual(best.name, "lan")
            self.assertEqual(best.url, "http://192.168.1.50:8765")

        # Case 2: LAN offline, Tailscale online -> Auto failover to Tailscale
        def mock_lan_down_get(url, **kwargs):
            if "192.168" in url:
                raise ConnectionError("Host unreachable on local LAN")
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "online"}
            return mock_resp

        with patch("requests.get", side_effect=mock_lan_down_get):
            best = self.manager.select_best_endpoint()
            self.assertIsNotNone(best)
            self.assertEqual(best.name, "tailscale")
            self.assertEqual(best.url, "http://laptop.tailscale.net:8765")

        # Case 3: Both offline -> None returned (safe queue in phone outbox)
        with patch("requests.get", side_effect=ConnectionError("All networks down")):
            best = self.manager.select_best_endpoint()
            self.assertIsNone(best)


from fastapi.testclient import TestClient

from dispatch.web.app import app
from dispatch.sync.receiver import (
    get_auth_token,
    get_pairing_pin,
    _FAILED_PIN_ATTEMPTS,
    _PIN_LOCKOUTS,
)
from dispatch.transport.discovery import (
    DiscoveryBeaconServer,
    BEACON_MAGIC,
    SERVICE_VERSION,
)


class TestDiscoveryAndPairingSecurity(unittest.TestCase):
    def setUp(self):
        # Client simulating a remote phone / LAN device
        self.lan_client = TestClient(app, client=("192.168.1.100", 50000))
        # Client simulating laptop localhost
        self.local_client = TestClient(app, client=("127.0.0.1", 50000))
        self.expected_token = get_auth_token()
        self.expected_pin = get_pairing_pin()
        _FAILED_PIN_ATTEMPTS.clear()
        _PIN_LOCKOUTS.clear()

    def test_udp_discovery_beacon_does_not_leak_auth_token(self):
        """Milestone 1 Acceptance: UDP discovery beacon must NOT broadcast master auth token."""
        server = DiscoveryBeaconServer()
        payload = server._get_payload()
        self.assertEqual(payload["magic"], BEACON_MAGIC)
        self.assertEqual(payload["service"], "dispatch")
        self.assertEqual(payload["version"], SERVICE_VERSION)
        self.assertTrue(payload.get("pairing_required"))
        self.assertIn("lan_url", payload)
        self.assertIn("ip", payload)
        self.assertIn("port", payload)
        self.assertIn("hostname", payload)
        # CRITICAL: auth_token must NOT be present in LAN UDP broadcast
        self.assertNotIn("auth_token", payload, "UDP discovery beacon must not leak auth_token to LAN sniffers")
        self.assertNotIn("token", payload)
        self.assertNotIn(self.expected_token, str(payload))

    def test_pairing_config_unauthenticated_lan_request_rejected(self):
        """Milestone 1 Acceptance: Unauthenticated LAN requests return 401 without tokens."""
        resp = self.lan_client.get("/api/sync/pairing/config")
        self.assertEqual(resp.status_code, 401)
        self.assertIn("WWW-Authenticate", resp.headers)
        resp_data = resp.json()

        # Absolute guarantee: no secrets in payload
        self.assertNotIn("auth_token", resp_data)
        self.assertNotIn("connection_string", resp_data)
        self.assertNotIn("yt_token", resp_data)
        self.assertNotIn("yt_refresh", resp_data)
        self.assertNotIn("yt_client_id", resp_data)
        self.assertNotIn("yt_client_secret", resp_data)
        self.assertNotIn(self.expected_token, resp.text)

    def test_pairing_config_masked_discovery_returns_200_without_secrets(self):
        """Masked discovery mode returns 200 OK with null secrets for safe network probing."""
        resp = self.lan_client.get("/api/sync/pairing/config?mask=true")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNone(data["auth_token"])
        self.assertIsNone(data["pairing_pin"])
        self.assertIsNone(data["connection_string"])
        self.assertIsNone(data["yt_token"])
        self.assertFalse(data["authenticated"])
        self.assertTrue(data["requires_pairing"])
        self.assertIn("lan_url", data)

    def test_pairing_config_localhost_exemption_for_web_dashboard(self):
        """Localhost web dashboard is exempt and receives pairing credentials."""
        resp = self.local_client.get("/api/sync/pairing/config")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["auth_token"], self.expected_token)
        self.assertEqual(data["pairing_pin"], self.expected_pin)
        self.assertTrue(data["authenticated"])
        self.assertIn("lan_url", data)
        self.assertIn("connection_string", data)

    def test_pairing_config_invalid_pin_or_token_rejected_with_401(self):
        """Invalid PIN or invalid token from LAN must be rejected with 401."""
        # Wrong PIN via header
        resp_bad_pin = self.lan_client.get("/api/sync/pairing/config", headers={"x-pairing-pin": "000000"})
        self.assertEqual(resp_bad_pin.status_code, 401)
        self.assertIn("Invalid pairing PIN", resp_bad_pin.text)
        self.assertIn("WWW-Authenticate", resp_bad_pin.headers)

        # Wrong PIN via query param
        resp_bad_pin_q = self.lan_client.get("/api/sync/pairing/config?pin=000000")
        self.assertEqual(resp_bad_pin_q.status_code, 401)

        # Wrong Auth Token
        resp_bad_tok = self.lan_client.get("/api/sync/pairing/config", headers={"x-auth-token": "bad_token_123"})
        self.assertEqual(resp_bad_tok.status_code, 401)
        self.assertIn("Invalid authentication token", resp_bad_tok.text)

    def test_pairing_config_authenticated_with_pin(self):
        """Client providing valid 6-digit pairing PIN successfully authenticates and receives tokens."""
        # Query parameter
        resp_query = self.lan_client.get(f"/api/sync/pairing/config?pin={self.expected_pin}")
        self.assertEqual(resp_query.status_code, 200)
        self.assertEqual(resp_query.json()["auth_token"], self.expected_token)
        self.assertIn("lan_url", resp_query.json())
        self.assertTrue(resp_query.json()["authenticated"])

        # Header parameter
        resp_hdr = self.lan_client.get("/api/sync/pairing/config", headers={"x-pairing-pin": self.expected_pin})
        self.assertEqual(resp_hdr.status_code, 200)
        self.assertEqual(resp_hdr.json()["auth_token"], self.expected_token)

    def test_pairing_config_authenticated_with_device_token(self):
        """Previously paired client providing x-auth-token successfully retrieves current config."""
        resp = self.lan_client.get("/api/sync/pairing/config", headers={"x-auth-token": self.expected_token})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["auth_token"], self.expected_token)

    def test_pin_brute_force_lockout_returns_429(self):
        """Brute-force protection: 5 failed PIN attempts results in 300s lockout returning 429."""
        ip = "192.168.1.222"
        attacker_client = TestClient(app, client=(ip, 50000))

        # First 5 attempts return 401
        for i in range(5):
            res = attacker_client.get("/api/sync/pairing/config?pin=000000")
            self.assertEqual(res.status_code, 401)

        # 6th attempt must be locked out with 429 Too Many Requests
        res_lockout = attacker_client.get("/api/sync/pairing/config?pin=000000")
        self.assertEqual(res_lockout.status_code, 429)
        self.assertIn("Too many failed pairing attempts", res_lockout.text)

        # Even if attacker now supplies correct PIN, they remain locked out
        res_locked_valid = attacker_client.get(f"/api/sync/pairing/config?pin={self.expected_pin}")
        self.assertEqual(res_locked_valid.status_code, 429)

    def test_pairing_handshake_endpoint(self):
        """Explicit POST /api/sync/pairing/handshake endpoint."""
        # Failure with wrong PIN
        res_fail = self.lan_client.post("/api/sync/pairing/handshake", json={"pin": "wrong_pin", "client_name": "POCO C65"})
        self.assertEqual(res_fail.status_code, 401)
        self.assertIn("Invalid pairing PIN", res_fail.text)

        # Success with valid PIN
        res_ok = self.lan_client.post("/api/sync/pairing/handshake", json={"pin": self.expected_pin, "client_name": "POCO C65"})
        self.assertEqual(res_ok.status_code, 200)
        data = res_ok.json()
        self.assertEqual(data["status"], "paired")
        self.assertEqual(data["auth_token"], self.expected_token)
        self.assertIn("lan_url", data)
        self.assertIn("connection_string", data)

    def test_simulated_android_discovery_and_handshake_flow(self):
        """Simulate complete Android discovery & pairing flow."""
        server = DiscoveryBeaconServer()
        payload = server._get_payload()
        discovered_ip = payload["ip"]
        discovered_port = payload["port"]
        discovered_url = payload["lan_url"]
        self.assertTrue(discovered_ip and discovered_port)
        self.assertTrue(payload.get("pairing_required"))

        # Step 1: Android tests reachability on ping endpoint
        ping_resp = self.lan_client.get("/api/sync/ping")
        self.assertEqual(ping_resp.status_code, 200)
        self.assertEqual(ping_resp.json()["status"], "online")

        # Step 2: Unauthenticated probe fails with 401 (no leak)
        unauth_resp = self.lan_client.get("/api/sync/pairing/config")
        self.assertEqual(unauth_resp.status_code, 401)
        self.assertNotIn("auth_token", unauth_resp.text)

        # Step 3: Phone submits pairing PIN entered by user
        pair_resp = self.lan_client.post("/api/sync/pairing/handshake", json={
            "pin": self.expected_pin,
            "client_name": "POCO C65"
        })
        self.assertEqual(pair_resp.status_code, 200)
        acquired_token = pair_resp.json()["auth_token"]
        self.assertEqual(acquired_token, self.expected_token)

        # Step 4: Phone can now perform authenticated operations
        auth_check = self.lan_client.get(
            "/api/sync/verify-chunk?segment_id=dummy&sha256=none",
            headers={"x-auth-token": acquired_token}
        )
        self.assertEqual(auth_check.status_code, 200)
        self.assertFalse(auth_check.json()["verified"])


if __name__ == "__main__":
    unittest.main()
