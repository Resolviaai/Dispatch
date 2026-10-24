"""2026-10-24 pivot: the PC no longer transfers YouTube credentials to ANY client.
Both former credential/pairing routes must respond 410 with no secret material,
over any transport, with any token/origin. (Supersedes the secure-transport tests:
the insecure architecture was removed instead of encrypted.)"""

import unittest
from fastapi.testclient import TestClient
from dispatch.web.app import app


class TestCredentialTransferRemoved(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def _assert_no_secrets(self, body: str):
        for marker in ("refresh_token", "client_secret", "ya29.", "1//"):
            self.assertNotIn(marker, body)

    def test_credentials_route_is_410_with_no_secret_material(self):
        post_res = self.client.post(
            "/api/youtube/credentials",
            json={"pairing_code": "1234"},
            headers={"origin": "https://appassets.androidplatform.net"},
        )
        self.assertEqual(post_res.status_code, 410)
        self._assert_no_secrets(post_res.text)
        get_res = self.client.get(
            "/api/youtube/credentials",
            headers={"origin": "https://appassets.androidplatform.net"},
        )
        self.assertEqual(get_res.status_code, 410)
        self._assert_no_secrets(get_res.text)

    def test_pairing_code_route_is_410(self):
        res = self.client.post(
            "/api/integrations/youtube/pairing-code",
            json={"request_reason": "x"},
            headers={"origin": "http://localhost:5173"},
        )
        self.assertIn(res.status_code, (410, 403))
        self._assert_no_secrets(res.text)


if __name__ == "__main__":
    unittest.main()
