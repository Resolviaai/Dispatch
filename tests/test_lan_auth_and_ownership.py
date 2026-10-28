import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from dispatch.web.app import app
import dispatch.db as db


class TestLanAuthAndOwnership(unittest.TestCase):
    def setUp(self):
        db.init_db()
        self.client = TestClient(app)

    def test_loopback_clips_allowed_without_auth(self):
        """Loopback caller (127.0.0.1 or testclient) can read clips without auth header."""
        res = self.client.get("/api/clips")
        self.assertEqual(res.status_code, 200)
        self.assertIn("ready", res.json())

    def test_lan_clips_requires_auth(self):
        """Non-loopback caller (LAN IP) without auth receives 401 Unauthorized."""
        res = self.client.get("/api/clips", headers={"X-Forwarded-For": "192.168.1.100"})
        # In TestClient, client.host is testclient by default unless client is constructed with host
        # or we mock request.client.host
        with patch("dispatch.web.app.is_trusted_localhost", return_value=False):
            res = self.client.get("/api/clips")
            self.assertEqual(res.status_code, 401)
            self.assertIn("Authentication required", res.json()["detail"])

    def test_lan_clips_scoped_by_user(self):
        """Authenticated LAN caller sees only their own clips."""
        with patch("dispatch.web.app.is_trusted_localhost", return_value=False), \
             patch("dispatch.web.app.get_authenticated_user", return_value={"user_id": "usr_alpha", "email": "alpha@example.com"}):
            res = self.client.get("/api/clips", headers={"Authorization": "Bearer fake_token"})
            self.assertEqual(res.status_code, 200)
            data = res.json()
            for c in data.get("ready", []):
                if c.get("user_id"):
                    self.assertEqual(c["user_id"], "usr_alpha")

    def test_outbox_blocks_user_job_without_account_credentials(self):
        """User-owned outbox job without bound account credentials fails closed as blocked_needs_auth."""
        from dispatch.publisher.outbox import _process_outbox_queue_internal
        from pathlib import Path
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"x" * 1024)
            temp_path = f.name

        with db.get_db_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO clips (id, user_id, title, status, video_path, start_time, end_time, duration)
                VALUES ('clip_auth_test', 'usr_alpha', 'Auth Test', 'approved', ?, 0.0, 15.0, 15.0)
            """, (temp_path,))
            conn.execute("""
                INSERT OR REPLACE INTO publishing_outbox (id, clip_id, user_id, platform, account_id, status, publish_mode, idempotency_key)
                VALUES ('job_auth_test', 'clip_auth_test', 'usr_alpha', 'youtube', 'missing_acct', 'queued', 'private', 'idem_auth_test')
            """)

        with patch("dispatch.publisher.outbox.probe_video", return_value=(True, {"duration": 15.0}, None)), \
             patch("dispatch.youtube_connections.get_account_credentials", return_value={}):
            _process_outbox_queue_internal()

        with db.get_db_connection() as conn:
            row = conn.execute("SELECT status, last_error FROM publishing_outbox WHERE id = 'job_auth_test'").fetchone()
            self.assertEqual(row["status"], "blocked_needs_auth")
            self.assertIn("requires a connected and verified youtube account", row["last_error"])

        Path(temp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
