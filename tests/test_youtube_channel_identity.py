"""Unit tests for Route A YouTube Channel Identity Alignment & Enforcement."""

import os
import unittest
from unittest.mock import MagicMock, patch
from dispatch.youtube_inbox.oauth import verify_channel_alignment, get_channel_identity
from dispatch.youtube_inbox.poller import YouTubeInboxPoller
from dispatch.youtube_connections import get_cloud_connected_youtube_channel


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
            "user_id": "usr-1",
            "status": "ok",
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
            "user_id": "usr-1",
            "status": "ok",
        }
        with patch("dispatch.youtube_connections.get_cloud_connected_youtube_channel", return_value=cloud_info):
            result = verify_channel_alignment(mock_svc)
            self.assertFalse(result["matched"])
            self.assertEqual(result["status"], "mismatch")
            self.assertEqual(result["pc_channel_id"], "UC_PC_CHANNEL")
            self.assertEqual(result["cloud_channel_id"], "UC_PHONE_CHANNEL")
            self.assertIn("Channel mismatch", result["error"])

    def test_ambiguous_owner_channel_alignment(self):
        mock_svc = MagicMock()
        mock_svc.channels.return_value.list.return_value.execute.return_value = {
            "items": [{"id": "UC_PC_CHANNEL", "snippet": {"title": "PC Channel"}}]
        }
        cloud_info = {
            "status": "ambiguous_owner",
            "account_count": 2,
            "accounts": ["UC_ACC_1", "UC_ACC_2"],
        }
        with patch("dispatch.youtube_connections.get_cloud_connected_youtube_channel", return_value=cloud_info):
            result = verify_channel_alignment(mock_svc)
            self.assertFalse(result["matched"])
            self.assertEqual(result["status"], "ambiguous_owner")
            self.assertIn("Multiple YouTube accounts", result["error"])

    @patch("dispatch.youtube_inbox.poller.verify_channel_alignment")
    @patch("dispatch.youtube_inbox.poller.list_authenticated_user_uploads")
    def test_poller_aborts_on_mismatch(self, mock_list_uploads, mock_verify):
        mock_verify.return_value = {
            "matched": False,
            "status": "mismatch",
            "error": "Channel mismatch: PC authenticated as UC_PC, Cloud connected to UC_CLOUD",
        }
        poller = YouTubeInboxPoller(user_id="test-user")
        results = poller.poll_once()
        self.assertEqual(results, [])
        mock_list_uploads.assert_not_called()

    @patch("dispatch.youtube_inbox.poller.verify_channel_alignment")
    @patch("dispatch.youtube_inbox.poller.list_authenticated_user_uploads")
    def test_poller_aborts_on_unauthenticated(self, mock_list_uploads, mock_verify):
        mock_verify.return_value = {
            "matched": False,
            "status": "unauthenticated",
            "error": "PC is not authenticated to YouTube",
        }
        poller = YouTubeInboxPoller()
        results = poller.poll_once()
        self.assertEqual(results, [])
        mock_list_uploads.assert_not_called()

    @patch("dispatch.youtube_inbox.poller.verify_channel_alignment")
    @patch("dispatch.youtube_inbox.poller.list_authenticated_user_uploads")
    def test_poller_aborts_on_ambiguous_owner(self, mock_list_uploads, mock_verify):
        mock_verify.return_value = {
            "matched": False,
            "status": "ambiguous_owner",
            "error": "Multiple YouTube accounts exist in cloud vault",
        }
        poller = YouTubeInboxPoller()
        results = poller.poll_once()
        self.assertEqual(results, [])
        mock_list_uploads.assert_not_called()

    @patch.dict(os.environ, {"SUPABASE_URL": "https://test.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "secret"})
    @patch("httpx.get")
    def test_get_cloud_connected_youtube_channel_user_scoped(self, mock_http_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"account_id": "UC_USER_1", "account_name": "Channel 1", "user_id": "usr-1"}
        ]
        mock_http_get.return_value = mock_resp

        channel = get_cloud_connected_youtube_channel(user_id="usr-1")
        self.assertIsNotNone(channel)
        self.assertEqual(channel["status"], "ok")
        self.assertEqual(channel["account_id"], "UC_USER_1")
        self.assertEqual(channel["user_id"], "usr-1")

        # Verify query string contained user_id=eq.usr-1
        args, kwargs = mock_http_get.call_args
        self.assertIn("user_id=eq.usr-1", args[0])

    @patch.dict(os.environ, {"SUPABASE_URL": "https://test.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "secret"}, clear=True)
    @patch("httpx.get")
    def test_get_cloud_connected_youtube_channel_ambiguous_multiple_accounts(self, mock_http_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"account_id": "UC_USER_1", "account_name": "Channel 1", "user_id": "usr-1"},
            {"account_id": "UC_USER_2", "account_name": "Channel 2", "user_id": "usr-2"},
        ]
        mock_http_get.return_value = mock_resp

        channel = get_cloud_connected_youtube_channel(user_id=None)
        self.assertIsNotNone(channel)
        self.assertEqual(channel["status"], "ambiguous_owner")
        self.assertEqual(channel["account_count"], 2)


if __name__ == "__main__":
    unittest.main()
