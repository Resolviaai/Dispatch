"""Unit and integration tests for Route A YouTube Channel Identity Alignment & Enforcement.

Verifies the Worker-broker contract (Wave 0): PC checks identity exclusively via
`/sync/pc/youtube/channel` using DISPATCH_WORKER_URL + DISPATCH_WORKER_TOKEN (never
SUPABASE_SERVICE_ROLE_KEY or DISPATCH_TOKEN_VAULT_KEY).
"""

import os
import unittest
from unittest.mock import MagicMock, patch
import httpx

from dispatch.youtube_inbox.oauth import verify_channel_alignment
from dispatch.youtube_inbox.poller import YouTubeInboxPoller
from dispatch.youtube_connections import is_valid_youtube_channel_id


class TestYouTubeChannelIdentity(unittest.TestCase):
    def setUp(self):
        for key in [
            "DISPATCH_WORKER_URL",
            "DISPATCH_CLOUD_WORKER_URL",
            "DISPATCH_WORKER_TOKEN",
            "SUPABASE_URL",
            "SUPABASE_SERVICE_ROLE_KEY",
            "DISPATCH_TOKEN_VAULT_KEY",
            "DISPATCH_USER_ID",
            "DISPATCH_PC_OWNER_USER_IDS",
            "DISPATCH_YOUTUBE_CHANNEL_ID",
            "DISPATCH_YOUTUBE_ACCOUNT_ID",
            "DISPATCH_STANDALONE",
            "DISPATCH_OFFLINE_MODE",
        ]:
            os.environ.pop(key, None)

    def _mock_youtube_service(self, channel_id="UC_PC_CHANNEL", channel_title="PC Studio Channel"):
        mock_svc = MagicMock()
        mock_svc.channels.return_value.list.return_value.execute.return_value = {
            "items": [{"id": channel_id, "snippet": {"title": channel_title}}]
        }
        return mock_svc

    def _configure_worker(self, user_id="usr-1"):
        os.environ["DISPATCH_WORKER_URL"] = "https://worker.test"
        os.environ["DISPATCH_WORKER_TOKEN"] = "pc-worker-token"
        if user_id:
            os.environ["DISPATCH_USER_ID"] = user_id

    @staticmethod
    def _mock_worker_response(body, status_code=200):
        resp = MagicMock()
        resp.status_code = status_code
        resp.content = b"{}"
        resp.json.return_value = body
        resp.text = str(body)
        return resp

    # -------------------------------------------------------------------------
    # 1. Unauthenticated & Cloud Unavailable (Fail-Closed)
    # -------------------------------------------------------------------------

    def test_unauthenticated_pc(self):
        with patch("dispatch.youtube_inbox.oauth.get_youtube_service", return_value=None):
            result = verify_channel_alignment(None)
            self.assertFalse(result["matched"])
            self.assertEqual(result["status"], "unauthenticated")

    def test_missing_cloud_configuration_without_standalone_fails_closed(self):
        svc = self._mock_youtube_service("UC_PC_123")
        result = verify_channel_alignment(svc)
        self.assertFalse(result["matched"])
        self.assertEqual(result["status"], "cloud_unavailable")
        self.assertIn("Cloud worker broker not configured", result["error"])

    def test_explicit_standalone_mode_allows_route_a(self):
        os.environ["DISPATCH_STANDALONE"] = "1"
        svc = self._mock_youtube_service("UC_PC_123")
        result = verify_channel_alignment(svc)
        self.assertTrue(result["matched"])
        self.assertEqual(result["status"], "standalone_mode")
        self.assertTrue(result.get("standalone"))

    @patch("httpx.post")
    def test_worker_http_500_fails_closed(self, mock_post):
        self._configure_worker("user-123")
        mock_post.return_value = self._mock_worker_response({"error": "Internal Server Error"}, status_code=500)

        svc = self._mock_youtube_service("UC_PC_123")
        result = verify_channel_alignment(svc)
        self.assertFalse(result["matched"])
        self.assertEqual(result["status"], "cloud_unavailable")
        self.assertIn("HTTP 500", result["error"])

    @patch("httpx.post", side_effect=httpx.TimeoutException("Connection timed out"))
    def test_worker_timeout_fails_closed(self, _mock_post):
        self._configure_worker("user-123")
        svc = self._mock_youtube_service("UC_PC_123")
        result = verify_channel_alignment(svc)
        self.assertFalse(result["matched"])
        self.assertEqual(result["status"], "cloud_unavailable")
        self.assertIn("timed out", result["error"])

    # -------------------------------------------------------------------------
    # 2. Genuine No Cloud Account vs Matched vs Mismatched
    # -------------------------------------------------------------------------

    @patch("httpx.post")
    def test_no_cloud_account_fails_closed_without_standalone(self, mock_post):
        self._configure_worker("user-123")
        mock_post.return_value = self._mock_worker_response(
            {"status": "not_found", "user_id": "user-123", "channel_id": None, "reason": "no_connected_youtube_account"}
        )

        svc = self._mock_youtube_service("UC_TEST_123")
        result = verify_channel_alignment(svc)
        self.assertFalse(result["matched"])
        self.assertEqual(result["status"], "no_cloud_account")
        self.assertEqual(result["pc_channel_id"], "UC_TEST_123")
        self.assertIn("No YouTube account connected", result["error"])

    @patch("httpx.post")
    def test_no_cloud_account_with_standalone_mode_allows_route_a(self, mock_post):
        self._configure_worker("user-123")
        os.environ["DISPATCH_STANDALONE"] = "1"
        mock_post.return_value = self._mock_worker_response(
            {"status": "not_found", "user_id": "user-123", "channel_id": None}
        )

        svc = self._mock_youtube_service("UC_TEST_123")
        result = verify_channel_alignment(svc)
        self.assertTrue(result["matched"])
        self.assertEqual(result["status"], "standalone_mode")

    @patch("httpx.post")
    def test_matching_channel_identity(self, mock_post):
        self._configure_worker("usr-1")
        mock_post.return_value = self._mock_worker_response({
            "status": "connected",
            "user_id": "usr-1",
            "channel_id": "UC_MATCH_456",
            "channel_title": "Creator Channel",
            "row_id": "row-1",
        })

        svc = self._mock_youtube_service("UC_MATCH_456", "Creator Channel")
        result = verify_channel_alignment(svc)
        self.assertTrue(result["matched"])
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["pc_channel_id"], "UC_MATCH_456")
        self.assertEqual(result["cloud_channel_id"], "UC_MATCH_456")

    @patch("httpx.post")
    def test_mismatched_channel_identity(self, mock_post):
        self._configure_worker("usr-1")
        mock_post.return_value = self._mock_worker_response({
            "status": "connected",
            "user_id": "usr-1",
            "channel_id": "UC_PHONE_CHANNEL",
            "channel_title": "Creator Brand",
            "row_id": "row-2",
        })

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

    @patch("httpx.post")
    def test_multiple_accounts_for_same_user_ambiguous(self, mock_post):
        self._configure_worker("usr-1")
        mock_post.return_value = self._mock_worker_response({
            "status": "ambiguous_owner",
            "user_id": "usr-1",
            "channel_id": None,
            "channels": [
                {"channel_id": "UC_CHANNEL_A"},
                {"channel_id": "UC_CHANNEL_B"},
            ],
        })

        svc = self._mock_youtube_service("UC_CHANNEL_A")
        result = verify_channel_alignment(svc)
        self.assertFalse(result["matched"])
        self.assertEqual(result["status"], "ambiguous_owner")
        self.assertIn("User usr-1 has 2 connected YouTube accounts", result["error"])

    @patch("httpx.post")
    def test_multiple_accounts_for_same_user_resolved_via_explicit_channel(self, mock_post):
        self._configure_worker("usr-1")
        os.environ["DISPATCH_YOUTUBE_CHANNEL_ID"] = "UC_CHANNEL_B"
        mock_post.return_value = self._mock_worker_response({
            "status": "connected",
            "user_id": "usr-1",
            "channel_id": "UC_CHANNEL_B",
            "channel_title": "Channel B",
            "row_id": "row-b",
        })

        svc = self._mock_youtube_service("UC_CHANNEL_B", "Channel B")
        result = verify_channel_alignment(svc)
        self.assertTrue(result["matched"])
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["cloud_channel_id"], "UC_CHANNEL_B")
        self.assertEqual(mock_post.call_args.kwargs["json"]["channel_id"], "UC_CHANNEL_B")

    # -------------------------------------------------------------------------
    # 4. PC Owner Selection (Multiple Owners vs Single Owner)
    # -------------------------------------------------------------------------

    @patch("httpx.post")
    def test_multiple_pc_owners_without_explicit_owner_returns_ambiguous(self, mock_post):
        self._configure_worker(user_id=None)
        os.environ["DISPATCH_PC_OWNER_USER_IDS"] = (
            "11111111-1111-1111-1111-111111111111, 22222222-2222-2222-2222-222222222222"
        )

        svc = self._mock_youtube_service("UC_PC_123")
        result = verify_channel_alignment(svc)
        self.assertFalse(result["matched"])
        self.assertEqual(result["status"], "ambiguous_owner")
        self.assertIn("Multiple PC owners configured", result["error"])
        mock_post.assert_not_called()

    @patch("httpx.post")
    def test_single_pc_owner_in_authorized_pc_owners_resolves_cleanly(self, mock_post):
        self._configure_worker(user_id=None)
        os.environ["DISPATCH_PC_OWNER_USER_IDS"] = "11111111-1111-1111-1111-111111111111"
        mock_post.return_value = self._mock_worker_response({
            "status": "connected",
            "user_id": "11111111-1111-1111-1111-111111111111",
            "channel_id": "UC_SINGLE_OWNER",
            "channel_title": "Owner Channel",
            "row_id": "row-single",
        })

        svc = self._mock_youtube_service("UC_SINGLE_OWNER", "Owner Channel")
        result = verify_channel_alignment(svc)
        self.assertTrue(result["matched"])
        self.assertEqual(result["status"], "matched")
        self.assertEqual(
            mock_post.call_args.kwargs["headers"]["X-Dispatch-User-Id"],
            "11111111-1111-1111-1111-111111111111",
        )

    # -------------------------------------------------------------------------
    # 5. Legacy Migrated YouTube Accounts
    # -------------------------------------------------------------------------

    @patch("httpx.post")
    def test_legacy_migrated_account_email_id_requires_reconnect(self, mock_post):
        self._configure_worker("usr-legacy")
        mock_post.return_value = self._mock_worker_response({
            "status": "legacy_reauth_required",
            "user_id": "usr-legacy",
            "channel_id": "creator@gmail.com",
            "account_name": "Old Channel",
            "legacy_id_type": "email",
            "reason": "legacy_account_needs_reconnect",
        })

        svc = self._mock_youtube_service("UC_PC_CHANNEL", "PC Channel")
        result = verify_channel_alignment(svc)
        self.assertFalse(result["matched"])
        self.assertEqual(result["status"], "legacy_account_needs_reconnect")
        self.assertIn("reconnect your YouTube channel", result["error"])
        self.assertNotEqual(result["status"], "mismatch")

    @patch("httpx.post")
    def test_legacy_migrated_account_reconciled_via_metadata(self, mock_post):
        self._configure_worker("usr-legacy")
        mock_post.return_value = self._mock_worker_response({
            "status": "connected",
            "user_id": "usr-legacy",
            "channel_id": "UC_RECONCILED_456",
            "channel_title": "Old Channel",
            "row_id": "row-reconciled",
        })

        svc = self._mock_youtube_service("UC_RECONCILED_456", "Old Channel")
        result = verify_channel_alignment(svc)
        self.assertTrue(result["matched"])
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["cloud_channel_id"], "UC_RECONCILED_456")

    @patch("httpx.post")
    def test_legacy_migrated_account_uuid_id_requires_reconnect(self, mock_post):
        self._configure_worker("usr-legacy")
        mock_post.return_value = self._mock_worker_response({
            "status": "legacy_reauth_required",
            "user_id": "usr-legacy",
            "channel_id": "11111111-1111-1111-1111-111111111111",
            "account_name": "UUID Channel",
            "legacy_id_type": "UUID",
            "reason": "legacy_account_needs_reconnect",
        })

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
            "no_cloud_account",
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

        mock_verify.return_value = {"matched": True, "status": "matched"}
        poller = YouTubeInboxPoller()
        results = poller.poll_once()
        self.assertEqual(results, [])
        mock_list_uploads.assert_called_once()

        mock_list_uploads.reset_mock()

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

    # -------------------------------------------------------------------------
    # 8. Dispatch Main Daemon Poller Startup & Shutdown Lifecycle
    # -------------------------------------------------------------------------

    @patch("dispatch.main.acquire_single_instance_lock", return_value=True)
    @patch("dispatch.main.release_single_instance_lock")
    @patch("dispatch.main.db.init_db")
    @patch("dispatch.main.recover_laptop_orchestrator", return_value={})
    @patch("dispatch.main.governor.set_low_process_priority")
    @patch("dispatch.main.shutil.which", return_value="ffmpeg")
    @patch("dispatch.transcription.worker.TranscriptionWorker")
    @patch("dispatch.ai_clips.worker.HighlightWorker")
    @patch("dispatch.publisher.outbox.PublishingOutboxWorker")
    @patch("dispatch.youtube_inbox.poller.YouTubeInboxPoller")
    @patch("dispatch.cloud.sync.start_cloud_sync_worker")
    @patch("dispatch.main.uvicorn.run")
    def test_start_dispatch_starts_and_stops_youtube_inbox_poller(
        self,
        mock_uvicorn_run,
        mock_sync_worker,
        mock_poller_cls,
        mock_outbox_cls,
        mock_highlight_cls,
        mock_transcription_cls,
        mock_which,
        mock_priority,
        mock_recover,
        mock_init_db,
        mock_release_lock,
        mock_acquire_lock,
    ):
        from dispatch.main import start_dispatch

        mock_poller_instance = MagicMock()
        mock_poller_cls.return_value = mock_poller_instance
        mock_uvicorn_run.return_value = None

        start_dispatch(run_web=True)

        mock_poller_cls.assert_called_once()
        mock_poller_instance.start.assert_called_once()
        mock_poller_instance.stop.assert_called_once()
        mock_release_lock.assert_called_once()

    @patch("dispatch.main.acquire_single_instance_lock", return_value=True)
    @patch("dispatch.main.release_single_instance_lock")
    @patch("dispatch.main.db.init_db")
    @patch("dispatch.main.recover_laptop_orchestrator", return_value={})
    @patch("dispatch.main.governor.set_low_process_priority")
    @patch("dispatch.main.shutil.which", return_value="ffmpeg")
    @patch("dispatch.transcription.worker.TranscriptionWorker")
    @patch("dispatch.ai_clips.worker.HighlightWorker")
    @patch("dispatch.publisher.outbox.PublishingOutboxWorker")
    @patch("dispatch.youtube_inbox.poller.YouTubeInboxPoller")
    @patch("dispatch.cloud.sync.start_cloud_sync_worker")
    @patch("dispatch.main.uvicorn.run")
    def test_start_dispatch_respects_poller_disabled_toggle(
        self,
        mock_uvicorn_run,
        mock_sync_worker,
        mock_poller_cls,
        mock_outbox_cls,
        mock_highlight_cls,
        mock_transcription_cls,
        mock_which,
        mock_priority,
        mock_recover,
        mock_init_db,
        mock_release_lock,
        mock_acquire_lock,
    ):
        from dispatch.main import start_dispatch

        os.environ["DISPATCH_ROUTE_A_POLLER"] = "0"
        try:
            mock_uvicorn_run.return_value = None
            start_dispatch(run_web=True)
            mock_poller_cls.assert_not_called()
        finally:
            del os.environ["DISPATCH_ROUTE_A_POLLER"]


if __name__ == "__main__":
    unittest.main()
