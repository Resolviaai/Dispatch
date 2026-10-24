"""Auth identity boundary: the /api/auth/session endpoint verifies the app's Supabase
JWT server-side and fails closed. Supabase responses are mocked (no network)."""

import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from dispatch.web.app import app

client = TestClient(app)


class TestAuthSession(unittest.TestCase):
    def test_missing_token_is_401(self):
        res = client.get("/api/auth/session")
        self.assertEqual(res.status_code, 401)

    def test_invalid_token_is_401(self):
        with patch("httpx.get") as g:
            g.return_value = MagicMock(status_code=401)
            res = client.get("/api/auth/session", headers={"Authorization": "Bearer bad"})
        self.assertEqual(res.status_code, 401)

    def test_valid_token_returns_identity(self):
        with patch("httpx.get") as g:
            g.return_value = MagicMock(status_code=200,
                json=lambda: {"id": "u-123", "email": "a@b.c"})
            res = client.get("/api/auth/session", headers={"Authorization": "Bearer good"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"user_id": "u-123", "email": "a@b.c"})

    def test_auth_service_down_is_503_not_500(self):
        with patch("httpx.get", side_effect=ConnectionError("down")):
            res = client.get("/api/auth/session", headers={"Authorization": "Bearer x"})
        self.assertEqual(res.status_code, 503)


if __name__ == "__main__":
    unittest.main()
