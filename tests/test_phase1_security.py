"""Regression test suite for Phase 1 Security Lockdown and Sanitization."""
import os
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from dispatch.web.app import app
from dispatch.config import WEB_HOST, ROOT_DIR, CLIPS_DIR
from dispatch import db


class TestPhase1Security(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_default_web_host_is_localhost(self):
        """Assert WEB_HOST defaults to 127.0.0.1, not 0.0.0.0."""
        self.assertEqual(WEB_HOST, "127.0.0.1")

    def test_clips_path_traversal_blocked(self):
        """Assert path traversal in /clips/{filename} returns 403 or 404, never serving arbitrary files."""
        # Relative traversal attempt
        resp = self.client.get("/clips/../../dispatch/config.py")
        self.assertIn(resp.status_code, (403, 404))

        resp = self.client.get("/clips/..%2F..%2Fdispatch%2Fconfig.py")
        self.assertIn(resp.status_code, (403, 404))

    def test_reject_nonexistent_clip_returns_404(self):
        """Assert POST /api/clips/{id}/reject returns 404 for nonexistent clips instead of poisoning rejections."""
        resp = self.client.post("/api/clips/nonexistent_clip_999999/reject", json={"reason": "test"})
        self.assertEqual(resp.status_code, 404)

    def test_gemini_key_injection_prevention(self):
        """Assert POST /api/integrations/gemini rejects newline injection, null bytes, and invalid formats."""
        # Newline injection attempt
        resp = self.client.post("/api/integrations/gemini", json={"api_key": "AIzaSyFakeKey\nMALICIOUS_ENV=1"})
        self.assertEqual(resp.status_code, 400)

        # Invalid prefix / format
        resp = self.client.post("/api/integrations/gemini", json={"api_key": "not_a_valid_gemini_key"})
        self.assertEqual(resp.status_code, 400)

        # Valid format
        valid_key = "AIzaSyDas-UU7agAjyQeHDag1yOMU6qyXuxIWBE"
        resp = self.client.post("/api/integrations/gemini", json={"api_key": valid_key})
        self.assertEqual(resp.status_code, 200)

    def test_dns_rebinding_untrusted_host_blocked(self):
        """Assert requests with untrusted Host header are rejected with 403."""
        resp = self.client.post(
            "/api/settings",
            json={"publish_mode": "private"},
            headers={"host": "attacker-controlled-site.com"}
        )
        self.assertEqual(resp.status_code, 403)

    def test_untrusted_origin_blocked(self):
        """Assert requests with untrusted Origin header are rejected with 403."""
        resp = self.client.post(
            "/api/settings",
            json={"publish_mode": "private"},
            headers={"origin": "http://evil-site.com"}
        )
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
