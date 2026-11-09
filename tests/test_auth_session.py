"""Auth identity boundary: the /api/auth/session endpoint verifies the app's Supabase
JWT server-side and fails closed. Supabase responses are mocked (no network)."""

import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from dispatch.web.app import app, _clear_auth_cache

client = TestClient(app)


class TestAuthSession(unittest.TestCase):
    def setUp(self):
        _clear_auth_cache()

    def tearDown(self):
        _clear_auth_cache()

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

    def test_auth_session_caches_within_ttl(self):
        with patch("httpx.get") as g:
            g.return_value = MagicMock(status_code=200,
                json=lambda: {"id": "u-cached", "email": "cached@test.com"})
            # Call 1: Miss
            res1 = client.get("/api/auth/session", headers={"Authorization": "Bearer token-1"})
            self.assertEqual(res1.status_code, 200)
            self.assertEqual(g.call_count, 1)

            # Call 2: Hit within TTL
            res2 = client.get("/api/auth/session", headers={"Authorization": "Bearer token-1"})
            self.assertEqual(res2.status_code, 200)
            self.assertEqual(g.call_count, 1, "Subsequent call within 60s TTL must hit cache without network request")


if __name__ == "__main__":
    unittest.main()
