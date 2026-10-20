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

    def test_generated_code_is_strictly_four_numeric_digits(self):
        """Verify that every generated code contains exactly four numeric digits."""
        for _ in range(50):
            code, expiry = issue_pairing_code()
            self.assertEqual(len(code), 4, f"Generated code {code} is not 4 digits")
            self.assertTrue(code.isdigit(), f"Generated code {code} is not all digits")
            self.assertEqual(expiry, 300)

    def test_code_with_leading_zeros_accepted(self):
        """Verify codes with leading zeros, such as '0047', are properly accepted."""
        with patch("secrets.randbelow", return_value=47):
            code, _ = issue_pairing_code()
            self.assertEqual(code, "0047")
            # Should consume without error
            consume_pairing_code("0047", "192.168.1.50")

    def test_invalid_length_and_non_numeric_codes_rejected(self):
        """Reject 3-digit, 5-digit, and non-numeric codes."""
        code, _ = issue_pairing_code()
        
        # 3 digits
        with self.assertRaises(PairingCodeError):
            consume_pairing_code("123", "192.168.1.50")
        
        # 5 digits
        with self.assertRaises(PairingCodeError):
            consume_pairing_code("12345", "192.168.1.50")
            
        # Non-numeric
        with self.assertRaises(PairingCodeError):
            consume_pairing_code("abcd", "192.168.1.50")
        with self.assertRaises(PairingCodeError):
            consume_pairing_code("12a4", "192.168.1.50")

    def test_code_is_single_use(self):
        """Verify single-use behavior."""
        code, expiry = issue_pairing_code()
        self.assertEqual(expiry, 300)
        consume_pairing_code(code, "192.168.1.20")
        with self.assertRaises(PairingCodeError):
            consume_pairing_code(code, "192.168.1.20")

    def test_code_expiry(self):
        """Verify code expires after TTL."""
        import dispatch.sync.pairing_codes as pc
        code, _ = issue_pairing_code()
        # Simulate time passing past TTL
        with patch("time.monotonic", return_value=pc._active_code_expires_at + 1):
            with self.assertRaises(PairingCodeError) as ctx:
                consume_pairing_code(code, "192.168.1.20")
            self.assertEqual(ctx.exception.status_code, 401)

    def test_lockout_protection_after_five_failed_attempts(self):
        """Verify IP lockout after 5 consecutive invalid attempts."""
        code, _ = issue_pairing_code()
        client_ip = "192.168.1.99"
        
        # 4 failed attempts
        for _ in range(4):
            with self.assertRaises(PairingCodeError) as ctx:
                consume_pairing_code("0000" if code != "0000" else "9999", client_ip)
            self.assertEqual(ctx.exception.status_code, 401)
            
        # 5th failed attempt triggers lockout
        with self.assertRaises(PairingCodeError) as ctx:
            consume_pairing_code("0000" if code != "0000" else "9999", client_ip)
        self.assertEqual(ctx.exception.status_code, 401)
        
        # Subsequent attempt (even with valid code!) is locked out with 429
        with self.assertRaises(PairingCodeError) as ctx:
            consume_pairing_code(code, client_ip)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("Too many incorrect pairing attempts", str(ctx.exception))

    def test_complete_pc_generation_to_phone_pairing_flow(self):
        """Test the complete PC generation -> phone entry -> successful pairing flow via API."""
        from fastapi.testclient import TestClient
        from dispatch.web.app import app
        
        client = TestClient(app)
        
        # 1. PC generates 4-digit pairing code from localhost
        pc_resp = client.post(
            "/api/integrations/youtube/pairing-code",
            json={"understands_http_risk": True},
            headers={"host": "localhost:8000"}
        )
        self.assertEqual(pc_resp.status_code, 200)
        data = pc_resp.json()
        code = data["code"]
        self.assertEqual(len(code), 4)
        self.assertTrue(code.isdigit())
        self.assertEqual(data["expires_in"], 300)
        
        # 2. Phone enters the 4-digit code from local LAN IP
        mock_creds = {
            "refresh_token": "mock-refresh-token",
            "client_id": "mock-client-id",
            "client_secret": "mock-client-secret"
        }
        with patch("dispatch.web.app._load_youtube_pairing_credentials", return_value=mock_creds):
            phone_resp = client.post(
                "/api/youtube/credentials",
                json={"pairing_code": code},
                headers={"host": "localhost:8000"}
            )
            self.assertEqual(phone_resp.status_code, 200)
            phone_data = phone_resp.json()
            self.assertTrue(phone_data["configured"])
            self.assertEqual(phone_data["refresh_token"], "mock-refresh-token")
            
        # 3. Second attempt with consumed code is rejected (single-use)
        second_resp = client.post(
            "/api/youtube/credentials",
            json={"pairing_code": code},
            headers={"host": "localhost:8000"}
        )
        self.assertEqual(second_resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
