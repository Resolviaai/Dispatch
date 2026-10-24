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
        db.init_db()
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
        """Assert POST /api/integrations/gemini rejects injection and writes ONLY
        to an isolated env file - restoring process env and Gemini module globals
        afterwards (advisor requirement: full test isolation)."""
        import tempfile
        from pathlib import Path as _P
        from unittest.mock import patch as _patch
        import os as _os
        import dispatch.config as _cfg
        import dispatch.ai_clips.highlight_finder as _hf
        saved_env = _os.environ.get("GEMINI_API_KEY")
        saved_cfg = _cfg.GEMINI_API_KEY
        saved_hf = _hf.GEMINI_API_KEY
        isolated = None
        try:
            with tempfile.TemporaryDirectory(prefix="gemini_env_") as tmp:
                isolated = _P(tmp) / ".env"
                isolated.write_text("GEMINI_API_KEY=AIzaSyOriginalKey00000000000000000000", encoding="utf-8")
                with _patch.dict(_os.environ, {"DISPATCH_ENV_PATH": str(isolated)}):
                    resp = self.client.post("/api/integrations/gemini",
                                            json={"api_key": "AIzaSyFakeKey\nMALICIOUS_ENV=1"})
                    self.assertEqual(resp.status_code, 400)

                    resp = self.client.post("/api/integrations/gemini",
                                            json={"api_key": "not_a_valid_gemini_key"})
                    self.assertEqual(resp.status_code, 400)

                    valid_key = "AIzaSyTestKeyWithValidFormat0000000000000"
                    resp = self.client.post("/api/integrations/gemini", json={"api_key": valid_key})
                    self.assertEqual(resp.status_code, 200)

                content = isolated.read_text(encoding="utf-8")
                self.assertIn(valid_key, content)
        finally:
            if saved_env is None:
                _os.environ.pop("GEMINI_API_KEY", None)
            else:
                _os.environ["GEMINI_API_KEY"] = saved_env
            _cfg.GEMINI_API_KEY = saved_cfg
            _hf.GEMINI_API_KEY = saved_hf
        real_env = _P(".env")
        if real_env.exists():
            self.assertNotIn("AIzaSyTestKeyWithValidFormat0000000000000",
                             real_env.read_text(encoding="utf-8"),
                             "REGRESSION: test wrote the real .env")


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

    def test_hostile_prefix_origin_rejected(self):
        """Assert requests with origins like http://localhost.evil.com or http://localhost:9999 are rejected."""
        resp = self.client.post(
            "/api/settings",
            json={"publish_mode": "private"},
            headers={"origin": "http://localhost.evil.com"}
        )
        self.assertEqual(resp.status_code, 403)

        resp = self.client.post(
            "/api/settings",
            json={"publish_mode": "private"},
            headers={"origin": "http://localhost:9999"}
        )
        self.assertEqual(resp.status_code, 403)

    def test_pairing_code_requires_trusted_host_and_origin(self):
        """Assert pairing-code endpoint rejects untrusted Host header even from loopback client."""
        resp = self.client.post(
            "/api/integrations/youtube/pairing-code",
            json={"understands_http_risk": True},
            headers={"host": "attacker-rebinding.com"}
        )
        # endpoint removed (410 Gone) - fails closed for ALL inputs
        self.assertEqual(resp.status_code, 410)

    def test_credentials_post_rejects_untrusted_host_and_origin(self):
        """Assert POST /api/youtube/credentials rejects untrusted Host and Origin headers."""
        resp = self.client.post(
            "/api/youtube/credentials",
            json={"pairing_code": "123456"},
            headers={"host": "attacker-rebinding.com"}
        )
        # endpoint removed (410 Gone) - fails closed for ALL inputs
        self.assertEqual(resp.status_code, 410)

        resp = self.client.post(
            "/api/youtube/credentials",
            json={"pairing_code": "123456"},
            headers={"origin": "http://evil-attacker.com"}
        )
        # endpoint removed (410 Gone) - fails closed for ALL inputs
        self.assertEqual(resp.status_code, 410)

    def test_studio_endpoint_serves_html_and_assets(self):
        """Assert GET /studio redirects to /studio/ and GET /studio/ serves 200 HTML with valid assets."""
        resp = self.client.get("/studio", follow_redirects=False)
        self.assertEqual(resp.status_code, 307)
        self.assertEqual(resp.headers.get("location"), "/studio/")

        resp = self.client.get("/studio/", follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))

        # Check that assets referenced by index.html actually return HTTP 200
        import re
        css_matches = re.findall(r'href="(?:\./)?assets/([^"]+\.css)"', resp.text)
        js_matches = re.findall(r'src="(?:\./)?assets/([^"]+\.js)"', resp.text)
        if css_matches:
            css_resp = self.client.get(f"/studio/assets/{css_matches[0]}")
            self.assertEqual(css_resp.status_code, 200)
        if js_matches:
            js_resp = self.client.get(f"/studio/assets/{js_matches[0]}")
            self.assertEqual(js_resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
