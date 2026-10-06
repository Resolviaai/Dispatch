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


if __name__ == "__main__":
    unittest.main()
