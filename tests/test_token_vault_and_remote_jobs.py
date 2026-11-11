"""Vault round-trip (standalone AES-GCM helper) and Worker-broker remote-job claim/finish
semantics (Wave 0: no SUPABASE_SERVICE_ROLE_KEY on PC)."""

import base64
import os
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from dispatch import youtube_connections as yc


def b64url(bytes_: bytes) -> str:
    import base64 as b
    return b.urlsafe_b64encode(bytes_).decode().rstrip("=")


class TestVaultRoundTrip(unittest.TestCase):
    def setUp(self):
        self.key = base64.b64encode(os.urandom(32)).decode()
        self._env = patch.dict(os.environ, {"DISPATCH_TOKEN_VAULT_KEY": self.key})
        self._env.start()
        self.addCleanup(self._env.stop)

    def _encrypt_like_worker(self, plaintext: str) -> str:
        iv = os.urandom(12)
        ct = AESGCM(base64.b64decode(self.key)).encrypt(iv, plaintext.encode(), None)
        return b64url(iv + ct)

    def test_worker_encrypted_token_decrypts_on_pc(self):
        token = "1//0gRefreshTokenFromGoogle"
        enc = self._encrypt_like_worker(token)
        self.assertEqual(yc.decrypt_refresh_token(enc), token)

    def test_tampered_ciphertext_fails_closed(self):
        import base64 as b64
        enc = self._encrypt_like_worker("secret")
        raw = bytearray(b64.urlsafe_b64decode(enc + "=" * (-len(enc) % 4)))
        raw[len(raw) // 2] ^= 0x01
        tampered = b64url(bytes(raw))
        with self.assertRaises(Exception):
            yc.decrypt_refresh_token(tampered)

    def test_missing_vault_key_raises_unavailable(self):
        os.environ.pop("DISPATCH_TOKEN_VAULT_KEY", None)
        with self.assertRaises(yc.VaultUnavailable):
            yc.vault_key()


class TestRemoteJobs(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(
            os.environ,
            {
                "DISPATCH_WORKER_URL": "https://worker.test",
                "DISPATCH_WORKER_TOKEN": "pc-worker-token",
            },
        )
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_claim_returns_job_and_finish_records_status(self):
        calls = []

        def fake_worker(path, payload=None, user_id=None, timeout_seconds=20.0):
            calls.append((path, payload))
            if path == "/sync/pc/remote-jobs/claim":
                return 200, {"job": {"job_id": "j1", "user_id": "u1", "youtube_url": "https://youtu.be/x"}}
            return 200, {"ok": True}

        with patch.object(yc, "_worker_post", side_effect=fake_worker):
            job = yc.claim_remote_job("owner-pc-1")
            self.assertEqual(job.job_id, "j1")
            yc.finish_remote_job("j1", "done", "vid", "")

        self.assertEqual(calls[1][0], "/sync/pc/remote-jobs/finish")
        self.assertEqual(calls[1][1]["status"], "done")

    def test_empty_queue_returns_none(self):
        with patch.object(yc, "_worker_post", return_value=(200, {"job": None})):
            self.assertIsNone(yc.claim_remote_job("w"))

    def test_poller_processes_claimed_job_through_ingest(self):
        processed = []
        with patch.object(yc, "_worker_post", return_value=(200, {"ok": True})), \
             patch.object(yc, "claim_remote_job", return_value=yc.RemoteJob("j9", "u9", "https://youtu.be/ok")):
            poller = yc.RemoteJobPoller(
                ingest_video=lambda url, **kwargs: processed.append(url) or {"status": "DOWNLOADED", "video_id": "v9"}
            )
            poller._process(yc.claim_remote_job("w"))
        self.assertEqual(processed, ["https://youtu.be/ok"])

    def test_ingest_failure_marks_job_failed(self):
        with patch.object(yc, "_worker_post", return_value=(200, {"ok": True})) as wp, \
             patch.object(yc, "claim_remote_job", return_value=yc.RemoteJob("j8", "u8", "https://youtu.be/bad")):
            poller = yc.RemoteJobPoller(ingest_video=lambda url, **kwargs: {"status": "FAILED", "error": "private"})
            poller._process(yc.claim_remote_job("w"))
        finish_calls = [c for c in wp.call_args_list if c.args[0] == "/sync/pc/remote-jobs/finish"]
        self.assertEqual(finish_calls[0].args[1]["status"], "failed")

    def test_claim_parses_direct_job_fields(self):
        def fake_worker(path, payload=None, user_id=None, timeout_seconds=20.0):
            if path == "/sync/pc/remote-jobs/claim":
                return 200, {
                    "job": {
                        "job_id": "jd1",
                        "user_id": "u1",
                        "youtube_url": "",
                        "source": "direct",
                        "upload_object_id": "obj-1",
                        "source_title": "My Vlog",
                    }
                }
            return 200, {"ok": True}

        with patch.object(yc, "_worker_post", side_effect=fake_worker):
            job = yc.claim_remote_job("owner-pc-1")
            self.assertEqual(job.source, "direct")
            self.assertEqual(job.upload_object_id, "obj-1")
            self.assertEqual(job.source_title, "My Vlog")

    def test_direct_job_routes_to_direct_handler(self):
        downloads = []
        youtube_calls = []

        def direct_handler(job):
            downloads.append(job.upload_object_id)
            return {"status": "DOWNLOADED", "video_id": f"dir_{job.upload_object_id}"}

        with patch.object(yc, "_worker_post", return_value=(200, {"ok": True})) as wp, \
             patch.object(
                 yc,
                 "claim_remote_job",
                 return_value=yc.RemoteJob("jd9", "u9", "", source="direct", upload_object_id="obj-9"),
             ):
            poller = yc.RemoteJobPoller(
                ingest_video=lambda url, **kw: youtube_calls.append(url) or {"status": "FAILED"},
                direct_handler=direct_handler,
            )
            poller._process(yc.claim_remote_job("w"))
        self.assertEqual(downloads, ["obj-9"])
        self.assertEqual(youtube_calls, [])
        finish_calls = [c for c in wp.call_args_list if c.args[0] == "/sync/pc/remote-jobs/finish"]
        self.assertEqual(finish_calls[0].args[1]["status"], "done")
        self.assertEqual(finish_calls[0].args[1]["video_id"], "dir_obj-9")

    def test_without_direct_handler_the_direct_job_fails_loudly(self):
        with patch.object(yc, "_worker_post", return_value=(200, {"ok": True})) as wp, \
             patch.object(
                 yc,
                 "claim_remote_job",
                 return_value=yc.RemoteJob("jd7", "u7", "", source="direct", upload_object_id="obj-7"),
             ):
            poller = yc.RemoteJobPoller(ingest_video=lambda url, **kwargs: {"status": "FAILED"})
            poller._process(yc.claim_remote_job("w"))
        finish_calls = [c for c in wp.call_args_list if c.args[0] == "/sync/pc/remote-jobs/finish"]
        self.assertEqual(finish_calls[0].args[1]["status"], "failed")

    def test_stage_title_and_disconnect_route_through_worker_endpoints(self):
        calls = []

        def fake_worker(path, payload=None, user_id=None, timeout_seconds=20.0):
            calls.append((path, payload, user_id))
            return 200, {"ok": True}

        with patch.object(yc, "_worker_post", side_effect=fake_worker):
            yc.update_remote_job_stage("11111111-1111-1111-1111-111111111111", "transcribing", video_id="v123", duration=12.5)
            yc.update_job_stage_by_video_id("v123", "rendering")
            yc.update_job_title_by_video_id("v123", "Updated Title")
            yc.disconnect_user_token("u1")

        self.assertEqual([c[0] for c in calls], [
            "/sync/pc/remote-jobs/stage",
            "/sync/pc/remote-jobs/stage-by-video",
            "/sync/pc/remote-jobs/title-by-video",
            "/sync/pc/disconnect",
        ])
        self.assertEqual(calls[0][1]["stage"], "transcribing")
        self.assertEqual(calls[1][1]["video_id"], "v123")
        self.assertEqual(calls[2][1]["title"], "Updated Title")
        self.assertEqual(calls[3][2], "u1")


if __name__ == "__main__":
    unittest.main()
