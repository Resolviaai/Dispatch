"""Admin authorization: backend-enforced allowlist; ordinary users and anonymous
callers cannot reach admin APIs; no secret material in responses."""

import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from dispatch.web.app import app

client = TestClient(app)


def auth_as(email):
    return patch("dispatch.web.app.get_authenticated_user",
                 return_value={"user_id": "u-1", "email": email})


class TestAdminAuthorization(unittest.TestCase):
    def test_anonymous_is_401(self):
        self.assertEqual(client.get("/api/admin/users").status_code, 401)

    def test_non_admin_email_is_403(self):
        with auth_as("friend@example.com"):
            self.assertEqual(client.get("/api/admin/users").status_code, 403)

    def test_admin_email_lists_users_without_secrets(self):
        fake_users = {"users": [{"id": "u1", "email": "friend@example.com",
                                 "created_at": "2026-10-24", "last_sign_in_at": None,
                                 "banned_until": None}]}
        with auth_as("resolviaai@gmail.com"), \
             patch("httpx.get", return_value=MagicMock(status_code=200, json=lambda: fake_users)):
            res = client.get("/api/admin/users")
        self.assertEqual(res.status_code, 200)
        body = res.text
        self.assertIn("friend@example.com", body)
        for secret_marker in ("refresh", "client_secret", "access_token"):
            self.assertNotIn(secret_marker, body.lower())

    def test_non_admin_cannot_ban(self):
        with auth_as("friend@example.com"):
            self.assertEqual(client.post("/api/admin/users/u1/ban").status_code, 403)

    def test_admin_can_ban_and_unban(self):
        with auth_as("resolviaai@gmail.com"), \
             patch("httpx.put", return_value=MagicMock(status_code=200)):
            self.assertEqual(client.post("/api/admin/users/u1/ban").status_code, 200)
            self.assertEqual(client.post("/api/admin/users/u1/unban").status_code, 200)

    def test_whoami_reports_admin_status(self):
        with auth_as("resolviaai@gmail.com"):
            self.assertTrue(client.get("/api/admin/whoami").json()["is_admin"])
        with auth_as("friend@example.com"):
            self.assertFalse(client.get("/api/admin/whoami").json()["is_admin"])


if __name__ == "__main__":
    unittest.main()
