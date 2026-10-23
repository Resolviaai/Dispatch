"""Regression: forged Origin headers must never authenticate (app.py origin-only
bypass removed 2026-10-24). The native Android WebView authenticates via the paired
device token (x-auth-token), not via its appassets origin."""

import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from dispatch.sync.receiver import get_auth_token
from dispatch.web.app import verify_dashboard_auth


class FakeRequest:
    def __init__(self, headers=None, client_host="127.0.0.1", path="/api/clips"):
        self.headers = headers or {}
        self.client = SimpleNamespace(host=client_host)
        self.url = SimpleNamespace(path=path)


FORGED = {"origin": "https://appassets.androidplatform.net"}


class TestDashboardAuth(unittest.TestCase):
    def test_forged_android_origin_from_lan_client_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            verify_dashboard_auth(FakeRequest(FORGED, client_host="192.168.0.55"))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_forged_android_origin_from_loopback_is_not_sufficient(self):
        # Origin alone must never authenticate; loopback + foreign origin -> 403
        with self.assertRaises(HTTPException) as ctx:
            verify_dashboard_auth(FakeRequest(FORGED, client_host="127.0.0.1"))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_valid_device_token_authenticates_from_anywhere(self):
        token = get_auth_token()
        req = FakeRequest({"x-auth-token": token}, client_host="192.168.0.55")
        self.assertIsNone(verify_dashboard_auth(req))  # no exception

    def test_wrong_token_from_lan_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            verify_dashboard_auth(FakeRequest({"x-auth-token": "bogus"}, client_host="192.168.0.55"))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_loopback_no_origin_still_works_for_local_browser(self):
        self.assertIsNone(verify_dashboard_auth(FakeRequest({}, client_host="127.0.0.1")))


if __name__ == "__main__":
    unittest.main()
