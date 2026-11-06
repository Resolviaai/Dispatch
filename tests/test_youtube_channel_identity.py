"""Unit and integration tests for Route A YouTube Channel Identity Alignment & Enforcement.

Verifies:
1. Two YouTube accounts for the same user (ambiguity vs explicit channel selection).
2. Multiple configured PC owners in DISPATCH_PC_OWNER_USER_IDS without DISPATCH_USER_ID.
3. Supabase HTTP 500 error handling (fail-closed, not fail-open).
4. Supabase request timeout handling (fail-closed, not fail-open).
5. Missing cloud configuration without standalone mode (fail-closed).
6. Explicitly enabled standalone mode (DISPATCH_STANDALONE=1).
7. Legacy migrated accounts with email as account_id (reconnect status vs metadata reconciliation).
8. Legacy migrated accounts with UUID as account_id.
9. Matching and mismatching channel IDs.
10. Poller actively halting without querying YouTube for every unsafe identity state.
"""

import os
import unittest
from unittest.mock import MagicMock, patch
import httpx

from dispatch.youtube_inbox.oauth import verify_channel_alignment, get_channel_identity
from dispatch.youtube_inbox.poller import YouTubeInboxPoller
from dispatch.youtube_connections import (
    get_cloud_connected_youtube_channel,
    is_valid_youtube_channel_id,
    VaultUnavailable,
)


class TestYouTubeChannelIdentity(unittest.TestCase):
    def setUp(self):
        # Clean environment overrides before each test
        for key in [
            "SUPABASE_URL",
            "SUPABASE_SERVICE_ROLE_KEY",
            "DISPATCH_USER_ID",
            "DISPATCH_PC_OWNER_USER_IDS",
            "DISPATCH_YOUTUBE_CHANNEL_ID",
            "DISPATCH_YOUTUBE_ACCOUNT_ID",
            "DISPATCH_STANDALONE",
            "DISPATCH_OFFLINE_MODE",
        ]:
            if key in os.environ:
                del os.environ[key]

    def _mock_youtube_service(self, channel_id="UC_PC_CHANNEL", channel_title="PC Studio Channel"):
        mock_svc = MagicMock()
        mock_svc.channels.return_value.list.return_value.execute.return_value = {
            "items": [{"id": channel_id, "snippet": {"title": channel_title}}]
        }
        return mock_svc

    # -------------------------------------------------------------------------
    # 1. Unauthenticated & Cloud Unavailable (Fail-Closed)
    # -------------------------------------------------------------------------

    def test_unauthenticated_pc(self):
        with patch("dispatch.youtube_inbox.oauth.get_youtube_service", return_value=None):
            result = verify_channel_alignment(None)
            self.assertFalse(result["matched"])
            self.assertEqual(result["status"], "unauthenticated")

    def test_missing_cloud_configuration_without_standalone_fails_closed(self):
        # When cloud is unconfigured and DISPATCH_STANDALONE is not set, must NOT be no_cloud_account
        svc = self._mock_youtube_service("UC_PC_123")
        result = verify_channel_alignment(svc)
        self.assertFalse(result["matched"])
        self.assertEqual(result["status"], "cloud_unavailable")
        self.assertIn("Cloud vault not configured", result["error"])

    def test_explicit_standalone_mode_allows_route_a(self):
        os.environ["DISPATCH_STANDALONE"] = "1"
        svc = self._mock_youtube_service("UC_PC_123")
        result = verify_channel_alignment(svc)
        self.assertTrue(result["matched"])
        self.assertEqual(result["status"], "standalone_mode")
        self.assertTrue(result.get("standalone"))

    @patch("httpx.get")
    def test_supabase_http_500_fails_closed(self, mock_http):
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"
        os.environ["DISPATCH_USER_ID"] = "user-123"

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_http.return_value = mock_resp

        svc = self._mock_youtube_service("UC_PC_123")
        result = verify_channel_alignment(svc)
        self.assertFalse(result["matched"])
        self.assertEqual(result["status"], "cloud_unavailable")
        self.assertIn("HTTP 500", result["error"])

    @patch("httpx.get", side_effect=httpx.TimeoutException("Connection timed out"))
    def test_supabase_timeout_fails_closed(self, mock_http):
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"
        os.environ["DISPATCH_USER_ID"] = "user-123"

        svc = self._mock_youtube_service("UC_PC_123")
        result = verify_channel_alignment(svc)
        self.assertFalse(result["matched"])
        self.assertEqual(result["status"], "cloud_unavailable")
        self.assertIn("timed out", result["error"])

    # -------------------------------------------------------------------------
    # 2. Genuine No Cloud Account vs Matched vs Mismatched
    # -------------------------------------------------------------------------

    @patch("httpx.get")
    def test_genuine_no_cloud_account_connected(self, mock_http):
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"
        os.environ["DISPATCH_USER_ID"] = "user-123"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []  # Verified empty list
        mock_http.return_value = mock_resp

        svc = self._mock_youtube_service("UC_TEST_123")
        result = verify_channel_alignment(svc)
        self.assertTrue(result["matched"])
        self.assertEqual(result["status"], "no_cloud_account")
        self.assertEqual(result["pc_channel_id"], "UC_TEST_123")

    @patch("httpx.get")
    def test_matching_channel_identity(self, mock_http):
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"
        os.environ["DISPATCH_USER_ID"] = "usr-1"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"account_id": "UC_MATCH_456", "account_name": "Creator Channel", "user_id": "usr-1"}
        ]
        mock_http.return_value = mock_resp

        svc = self._mock_youtube_service("UC_MATCH_456", "Creator Channel")
        result = verify_channel_alignment(svc)
        self.assertTrue(result["matched"])
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["pc_channel_id"], "UC_MATCH_456")
        self.assertEqual(result["cloud_channel_id"], "UC_MATCH_456")

    @patch("httpx.get")
    def test_mismatched_channel_identity(self, mock_http):
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"
        os.environ["DISPATCH_USER_ID"] = "usr-1"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"account_id": "UC_PHONE_CHANNEL", "account_name": "Creator Brand", "user_id": "usr-1"}
        ]
        mock_http.return_value = mock_resp

        svc = self._mock_youtube_service("UC_PC_CHANNEL", "PC Account")
        result = verify_channel_alignment(svc)
        self.assertFalse(result["matched"])
        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(result["pc_channel_id"], "UC_PC_CHANNEL")
        self.assertEqual(result["cloud_channel_id"], "UC_PHONE_CHANNEL")
        self.assertIn("Channel mismatch", result["error"])

    # -------------------------------------------------------------------------
    # 3. Multiple YouTube Accounts for Same User
    # -------------------------------------------------------------------------

    @patch("httpx.get")
    def test_multiple_accounts_for_same_user_ambiguous(self, mock_http):
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"
        os.environ["DISPATCH_USER_ID"] = "usr-1"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"account_id": "UC_CHANNEL_A", "account_name": "Channel A", "user_id": "usr-1"},
            {"account_id": "UC_CHANNEL_B", "account_name": "Channel B", "user_id": "usr-1"},
        ]
        mock_http.return_value = mock_resp

        svc = self._mock_youtube_service("UC_CHANNEL_A")
        result = verify_channel_alignment(svc)
        self.assertFalse(result["matched"])
        self.assertEqual(result["status"], "ambiguous_owner")
        self.assertIn("User usr-1 has 2 connected YouTube accounts", result["error"])

    @patch("httpx.get")
    def test_multiple_accounts_for_same_user_resolved_via_explicit_channel(self, mock_http):
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"
        os.environ["DISPATCH_USER_ID"] = "usr-1"
        os.environ["DISPATCH_YOUTUBE_CHANNEL_ID"] = "UC_CHANNEL_B"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"account_id": "UC_CHANNEL_A", "account_name": "Channel A", "user_id": "usr-1"},
            {"account_id": "UC_CHANNEL_B", "account_name": "Channel B", "user_id": "usr-1"},
        ]
        mock_http.return_value = mock_resp

        svc = self._mock_youtube_service("UC_CHANNEL_B", "Channel B")
        result = verify_channel_alignment(svc)
        self.assertTrue(result["matched"])
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["cloud_channel_id"], "UC_CHANNEL_B")

    # -------------------------------------------------------------------------
    # 4. PC Owner Selection (Multiple Owners vs Single Owner)
    # -------------------------------------------------------------------------

    @patch("httpx.get")
    def test_multiple_pc_owners_without_explicit_owner_returns_ambiguous(self, mock_http):
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"
        os.environ["DISPATCH_PC_OWNER_USER_IDS"] = (
            "11111111-1111-1111-1111-111111111111, 22222222-2222-2222-2222-222222222222"
        )

        svc = self._mock_youtube_service("UC_PC_123")
        result = verify_channel_alignment(svc)
        self.assertFalse(result["matched"])
        self.assertEqual(result["status"], "ambiguous_owner")
        self.assertIn("Multiple PC owners configured", result["error"])
        # HTTP query should not even be called when owners are ambiguous
        mock_http.assert_not_called()

    @patch("httpx.get")
    def test_single_pc_owner_in_authorized_pc_owners_resolves_cleanly(self, mock_http):
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"
        os.environ["DISPATCH_PC_OWNER_USER_IDS"] = "11111111-1111-1111-1111-111111111111"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"account_id": "UC_SINGLE_OWNER", "account_name": "Owner Channel", "user_id": "11111111-1111-1111-1111-111111111111"}
        ]
        mock_http.return_value = mock_resp

        svc = self._mock_youtube_service("UC_SINGLE_OWNER", "Owner Channel")
        result = verify_channel_alignment(svc)
        self.assertTrue(result["matched"])
        self.assertEqual(result["status"], "matched")

        # Verify query string scoped to the single owner
        args, _ = mock_http.call_args
        self.assertIn("user_id=eq.11111111-1111-1111-1111-111111111111", args[0])

    # -------------------------------------------------------------------------
    # 5. Legacy Migrated YouTube Accounts
    # -------------------------------------------------------------------------

    @patch("httpx.get")
    def test_legacy_migrated_account_email_id_requires_reconnect(self, mock_http):
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"
        os.environ["DISPATCH_USER_ID"] = "usr-legacy"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {
                "account_id": "creator@gmail.com",
                "account_name": "Old Channel",
                "user_id": "usr-legacy",
                "metadata": {},
            }
        ]
        mock_http.return_value = mock_resp

        svc = self._mock_youtube_service("UC_PC_CHANNEL", "PC Channel")
        result = verify_channel_alignment(svc)
        self.assertFalse(result["matched"])
        self.assertEqual(result["status"], "legacy_account_needs_reconnect")
        self.assertIn("reconnect your YouTube channel", result["error"])
        # Crucial: it must NOT be reported as a channel mismatch
        self.assertNotEqual(result["status"], "mismatch")

    @patch("httpx.get")
    def test_legacy_migrated_account_reconciled_via_metadata(self, mock_http):
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"
        os.environ["DISPATCH_USER_ID"] = "usr-legacy"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {
                "account_id": "creator@gmail.com",
                "account_name": "Old Channel",
                "user_id": "usr-legacy",
                "metadata": {"channel_id": "UC_RECONCILED_456"},
            }
        ]
        mock_http.return_value = mock_resp

        svc = self._mock_youtube_service("UC_RECONCILED_456", "Old Channel")
        result = verify_channel_alignment(svc)
        self.assertTrue(result["matched"])
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["cloud_channel_id"], "UC_RECONCILED_456")

    @patch("httpx.get")
    def test_legacy_migrated_account_uuid_id_requires_reconnect(self, mock_http):
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"
        os.environ["DISPATCH_USER_ID"] = "usr-legacy"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {
                "account_id": "11111111-1111-1111-1111-111111111111",
                "account_name": "UUID Channel",
                "user_id": "usr-legacy",
                "metadata": {},
            }
        ]
        mock_http.return_value = mock_resp

        svc = self._mock_youtube_service("UC_PC_CHANNEL", "PC Channel")
        result = verify_channel_alignment(svc)
        self.assertFalse(result["matched"])
        self.assertEqual(result["status"], "legacy_account_needs_reconnect")
        self.assertIn("UUID identifier", result["error"])

    # -------------------------------------------------------------------------
    # 6. Poller Halts on Every Unsafe Identity State
    # -------------------------------------------------------------------------

    @patch("dispatch.youtube_inbox.poller.list_authenticated_user_uploads")
    def test_poller_aborts_on_all_unsafe_states(self, mock_list_uploads):
        unsafe_statuses = [
            "unauthenticated",
            "cloud_unavailable",
            "ambiguous_owner",
            "legacy_account_needs_reconnect",
            "mismatch",
        ]

        for unsafe_status in unsafe_statuses:
            with patch("dispatch.youtube_inbox.poller.verify_channel_alignment") as mock_verify:
                mock_verify.return_value = {
                    "matched": False,
                    "status": unsafe_status,
                    "error": f"Simulated {unsafe_status} failure",
                }
                poller = YouTubeInboxPoller()
                results = poller.poll_once()
                self.assertEqual(results, [], f"Poller did not return [] for status '{unsafe_status}'")
                mock_list_uploads.assert_not_called()

    @patch("dispatch.youtube_inbox.poller.list_authenticated_user_uploads")
    @patch("dispatch.youtube_inbox.poller.verify_channel_alignment")
    def test_poller_proceeds_when_matched_or_standalone(self, mock_verify, mock_list_uploads):
        mock_list_uploads.return_value = []

        # Test matched: verification succeeds, uploads listing is called
        mock_verify.return_value = {"matched": True, "status": "matched"}
        poller = YouTubeInboxPoller()
        results = poller.poll_once()
        self.assertEqual(results, [])
        mock_list_uploads.assert_called_once()

        mock_list_uploads.reset_mock()

        # Test standalone_mode: local standalone allows uploads listing
        mock_verify.return_value = {"matched": True, "status": "standalone_mode", "standalone": True}
        results = poller.poll_once()
        self.assertEqual(results, [])
        mock_list_uploads.assert_called_once()

    # -------------------------------------------------------------------------
    # 7. Helper: is_valid_youtube_channel_id
    # -------------------------------------------------------------------------

    def test_channel_id_validation_helper(self):
        self.assertTrue(is_valid_youtube_channel_id("UC_TEST_123"))
        self.assertTrue(is_valid_youtube_channel_id("UC1234567890123456789012"))
        self.assertTrue(is_valid_youtube_channel_id("ch-channel-id"))
        self.assertFalse(is_valid_youtube_channel_id("creator@gmail.com"))
        self.assertFalse(is_valid_youtube_channel_id("11111111-1111-1111-1111-111111111111"))
        self.assertFalse(is_valid_youtube_channel_id(None))
        self.assertFalse(is_valid_youtube_channel_id(""))
        self.assertFalse(is_valid_youtube_channel_id("invalid with spaces"))


if __name__ == "__main__":
    unittest.main()
