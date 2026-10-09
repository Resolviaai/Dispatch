import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dispatch.sync.pairing_codes import (
    PairingCodeError,
    consume_pairing_code,
    issue_pairing_code,
    reset_pairing_codes_for_tests,
)
from scripts import auth_youtube


class YouTubeAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.root = Path(self.temp_dir.name)
        (self.root / "client_secrets.json").write_text("{}", encoding="utf-8")
        self.token_file = self.root / "youtube_token.json"
        self.token_file.write_text("old-token", encoding="utf-8")
        self.credentials = MagicMock(refresh_token="refresh", to_json=lambda: '{"token":"new"}')
        self.flow = MagicMock()
        self.flow.run_local_server.return_value = self.credentials

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run_auth(self, channels_result):
        service = MagicMock()
        service.channels.return_value.list.return_value.execute.return_value = channels_result
        with patch.object(auth_youtube, "ROOT_DIR", self.root), \
             patch.object(auth_youtube, "CLIENT_SECRETS_FILE", self.root / "client_secrets.json"), \
             patch.object(auth_youtube, "TOKEN_FILE", self.token_file), \
             patch("google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file", return_value=self.flow), \
             patch("googleapiclient.discovery.build", return_value=service):
            return auth_youtube.run_authentication()

    def test_token_is_saved_only_after_channel_verification(self):
        with self.assertRaises(RuntimeError):
            self._run_auth({"items": []})
        self.assertEqual(self.token_file.read_text(encoding="utf-8"), "old-token")

        channel = self._run_auth({"items": [{"id": "channel-id", "snippet": {"title": "Channel"}}]})
        self.assertEqual(channel["channel_id"], "channel-id")
        self.assertEqual(json.loads(self.token_file.read_text(encoding="utf-8")), {"token": "new"})


class PairingCodeTests(unittest.TestCase):
    def setUp(self):
        reset_pairing_codes_for_tests()

    def tearDown(self):
        reset_pairing_codes_for_tests()

    def test_code_is_single_use(self):
        code, expiry = issue_pairing_code()
        self.assertEqual(expiry, 300)
        consume_pairing_code(code, "192.168.1.20")
        with self.assertRaises(PairingCodeError):
            consume_pairing_code(code, "192.168.1.20")


if __name__ == "__main__":
    unittest.main()
