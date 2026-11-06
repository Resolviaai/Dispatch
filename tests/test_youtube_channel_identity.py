"""Unit tests for Route A YouTube Channel Identity Alignment & Enforcement."""

import unittest
from unittest.mock import MagicMock, patch
from dispatch.youtube_inbox.oauth import verify_channel_alignment, get_channel_identity


class TestYouTubeChannelIdentity(unittest.TestCase):
    def test_unauthenticated_pc(self):
        with patch("dispatch.youtube_inbox.oauth.get_youtube_service", return_value=None):
            result = verify_channel_alignment(None)
            self.assertFalse(result["matched"])
            self.assertEqual(result["status"], "unauthenticated")

    def test_no_cloud_account_configured(self):
        mock_svc = MagicMock()
        mock_svc.channels.return_value.list.return_value.execute.return_value = {
            "items": [{"id": "UC_TEST_123", "snippet": {"title": "Test Channel"}}]
        }
        with patch("dispatch.youtube_connections.get_cloud_connected_youtube_channel", return_value=None):
            result = verify_channel_alignment(mock_svc)
            self.assertTrue(result["matched"])
            self.assertEqual(result["status"], "no_cloud_account")
            self.assertEqual(result["pc_channel_id"], "UC_TEST_123")

    def test_matching_channel_identity(self):
        mock_svc = MagicMock()
        mock_svc.channels.return_value.list.return_value.execute.return_value = {
            "items": [{"id": "UC_MATCH_456", "snippet": {"title": "Creator Channel"}}]
        }
        cloud_info = {
            "account_id": "UC_MATCH_456",
            "account_name": "Creator Channel",
            "user_id": "usr-1"
        }
        with patch("dispatch.youtube_connections.get_cloud_connected_youtube_channel", return_value=cloud_info):
            result = verify_channel_alignment(mock_svc)
            self.assertTrue(result["matched"])
            self.assertEqual(result["status"], "matched")
            self.assertEqual(result["pc_channel_id"], "UC_MATCH_456")
            self.assertEqual(result["cloud_channel_id"], "UC_MATCH_456")

    def test_mismatched_channel_identity(self):
        mock_svc = MagicMock()
        mock_svc.channels.return_value.list.return_value.execute.return_value = {
            "items": [{"id": "UC_PC_CHANNEL", "snippet": {"title": "PC Personal Account"}}]
        }
        cloud_info = {
            "account_id": "UC_PHONE_CHANNEL",
            "account_name": "Creator Brand Channel",
            "user_id": "usr-1"
        }
        with patch("dispatch.youtube_connections.get_cloud_connected_youtube_channel", return_value=cloud_info):
            result = verify_channel_alignment(mock_svc)
            self.assertFalse(result["matched"])
            self.assertEqual(result["status"], "mismatch")
            self.assertEqual(result["pc_channel_id"], "UC_PC_CHANNEL")
            self.assertEqual(result["cloud_channel_id"], "UC_PHONE_CHANNEL")
            self.assertIn("Channel mismatch", result["error"])


if __name__ == "__main__":
    unittest.main()
