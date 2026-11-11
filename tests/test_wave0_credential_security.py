"""Wave 0 (§6 & §7) credential isolation, atomic token storage, and Worker broker tests."""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dispatch.cloud.config import load_cloud_config
from dispatch import youtube_connections as yc
from dispatch.publisher import youtube as pub_yt
from dispatch.publisher import outbox as pub_outbox
from dispatch.youtube_inbox import oauth as inbox_oauth


class TestWave0CredentialIsolation(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_cloud_config_ignores_worker_only_vault_and_service_role_secrets(self):
        with patch.dict(
            os.environ,
            {
                "DISPATCH_WORKER_URL": "https://worker.test",
                "DISPATCH_WORKER_TOKEN": "pc-tok",
                "SUPABASE_SERVICE_ROLE_KEY": "svc-secret",
                "DISPATCH_TOKEN_VAULT_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                "TOKEN_VAULT_KEY": "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
            },
            clear=False,
        ):
            cfg = load_cloud_config()
            self.assertEqual(cfg.worker_url, "https://worker.test")
            self.assertEqual(cfg.worker_token, "pc-tok")
            self.assertFalse(hasattr(cfg, "service_role_key"))
            self.assertFalse(hasattr(cfg, "supabase_service_role_key"))
            self.assertFalse(hasattr(cfg, "token_vault_key"))
            self.assertNotIn("svc-secret", repr(cfg))
            self.assertNotIn("AAAA", repr(cfg))

    def test_pc_credential_helpers_work_without_service_role_or_vault_key(self):
        os.environ["DISPATCH_WORKER_URL"] = "https://worker.test"
        os.environ["DISPATCH_WORKER_TOKEN"] = "pc-tok"
        os.environ["DISPATCH_USER_ID"] = "11111111-1111-1111-1111-111111111111"
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
        os.environ.pop("DISPATCH_TOKEN_VAULT_KEY", None)

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.content = b"{}"
        fake_resp.json.return_value = {
            "ok": True,
            "provider": "youtube",
            "account_id": "UC_CHAN_1",
            "access_token": "ya29.short-lived-access",
            "expires_at": "2099-01-01T00:00:00Z",
            "ttl_seconds": 3000,
        }

        with patch("httpx.post", return_value=fake_resp) as mock_post:
            creds = yc.get_account_credentials("UC_CHAN_1", provider="youtube")

        self.assertEqual(creds["access_token"], "ya29.short-lived-access")
        self.assertNotIn("refresh_token", creds)
        self.assertNotIn("client_secret", creds)
        self.assertIn("/sync/pc/oauth/access-token", mock_post.call_args.args[0])

    def test_get_user_refresh_token_and_gemini_key_fail_closed_on_pc(self):
        with patch("httpx.post") as mock_post, patch("httpx.get") as mock_get:
            self.assertEqual(yc.get_user_refresh_token("11111111-1111-1111-1111-111111111111"), "")
            self.assertEqual(yc.get_user_gemini_key("11111111-1111-1111-1111-111111111111"), "")
            mock_post.assert_not_called()
            mock_get.assert_not_called()

    def test_gemini_proxy_executes_via_worker_without_exposing_key(self):
        os.environ["DISPATCH_WORKER_URL"] = "https://worker.test"
        os.environ["DISPATCH_WORKER_TOKEN"] = "pc-tok"
        uid = "11111111-1111-1111-1111-111111111111"

        status_resp = MagicMock(status_code=200, content=b"{}")
        status_resp.json.return_value = {"configured": True, "key_prefix": "AIzaSyXX...1234"}
        with patch("httpx.get", return_value=status_resp):
            self.assertTrue(yc.has_user_gemini_proxy(uid))

        gen_resp = MagicMock(status_code=200, content=b"{}")
        gen_resp.json.return_value = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
        with patch("httpx.post", return_value=gen_resp) as mock_post:
            res = yc.call_user_gemini_proxy(uid, contents=[{"parts": [{"text": "hi"}]}])
        self.assertEqual(res["candidates"][0]["content"]["parts"][0]["text"], "ok")
        self.assertEqual(mock_post.call_args.kwargs["headers"]["X-Dispatch-User-Id"], uid)

    def test_needs_reauth_propagates_to_permission_error_and_outbox_status(self):
        os.environ["DISPATCH_WORKER_URL"] = "https://worker.test"
        os.environ["DISPATCH_WORKER_TOKEN"] = "pc-tok"
        uid = "11111111-1111-1111-1111-111111111111"

        reauth_resp = MagicMock(status_code=401, content=b"{}")
        reauth_resp.json.return_value = {
            "error": "YouTube OAuth revoked",
            "code": "needs_reauth",
            "needs_reauth": True,
        }
        with patch("httpx.post", return_value=reauth_resp):
            creds = yc.get_account_credentials("UC_REVOKED", user_id=uid, provider="youtube")
        self.assertTrue(creds["needs_reauth"])
        with self.assertRaises(PermissionError):
            pub_yt.build_youtube_service_from_credentials(creds)

    def test_outbox_transitions_job_to_blocked_needs_auth_on_needs_reauth(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            vid_path = Path(td) / "clip.mp4"
            vid_path.write_bytes(b"video-bytes")

            from dispatch import db as storage_db
            with patch.object(storage_db, "DB_PATH", db_path):
                storage_db.init_db()
                clip_id = storage_db.save_clip(
                    session_id=None,
                    chunk_id=None,
                    start_time=0.0,
                    end_time=5.0,
                    title="Clip Title",
                    hook="hook",
                    description="",
                    hashtags="",
                    virality_score=90,
                    layout_mode="fit_blur",
                    user_id="11111111-1111-1111-1111-111111111111",
                )
                with storage_db.get_db_connection() as conn:
                    conn.execute(
                        "UPDATE clips SET status = 'approved', rendered_layout_mode = 'fit_blur', video_path = ? WHERE id = ?",
                        (str(vid_path), clip_id),
                    )
                    conn.execute(
                        """INSERT INTO publishing_outbox
                           (id, clip_id, platform, status, idempotency_key, user_id, account_id)
                           VALUES (?, ?, 'youtube', 'queued', ?, ?, ?)""",
                        ("job_reauth_1", clip_id, "idem_reauth_1", "11111111-1111-1111-1111-111111111111", "UC_REVOKED"),
                    )

                with patch("dispatch.publisher.outbox.probe_video", return_value=(True, {"duration": 5.0}, None)), \
                     patch("dispatch.youtube_connections.get_account_credentials", return_value={"needs_reauth": True, "error": "OAuth consent revoked"}):
                    pub_outbox.process_outbox_queue()

                with storage_db.get_db_connection() as conn:
                    row = conn.execute("SELECT status, last_error FROM publishing_outbox WHERE id = ?", ("job_reauth_1",)).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row["status"], "blocked_needs_auth")
                self.assertIn("revoked", row["last_error"].lower())

    def test_youtube_inbox_status_uses_worker_proxy_without_refresh_token(self):
        os.environ["DISPATCH_WORKER_URL"] = "https://worker.test"
        os.environ["DISPATCH_WORKER_TOKEN"] = "pc-tok"
        uid = "11111111-1111-1111-1111-111111111111"

        status_resp = MagicMock(status_code=200, content=b"{}")
        status_resp.json.return_value = {
            "ok": True,
            "videos": {
                "vid123456": {
                    "upload_status": "processed",
                    "processing_status": "succeeded",
                    "privacy_status": "unlisted",
                    "title": "Clip",
                    "duration": "PT30S",
                }
            },
        }
        with patch("dispatch.youtube_inbox.oauth.get_youtube_service", return_value=None), \
             patch("httpx.post", return_value=status_resp):
            res = inbox_oauth.get_youtube_processing_status(["vid123456"], user_id=uid)
        self.assertEqual(res["vid123456"]["upload_status"], "processed")
        self.assertEqual(res["vid123456"]["processing_status"], "succeeded")

    def test_atomic_youtube_token_file_writes_are_thread_safe(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "youtube_token.json"
            errors = []

            def writer(idx: int):
                try:
                    for j in range(10):
                        payload = f'{{"token": "tok-{idx}-{j}"}}'
                        if idx % 2 == 0:
                            pub_yt._atomic_write_token_file(target, payload)
                        else:
                            inbox_oauth._atomic_write_token_file(target, payload)
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=writer, args=(i,)) for i in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(errors, [])
            self.assertTrue(target.exists())
            leftovers = [p for p in Path(td).iterdir() if p.name != "youtube_token.json"]
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
