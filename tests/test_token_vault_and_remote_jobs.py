"""Vault round-trip (Worker-compatible AES-GCM format) and remote-job claim/finish
semantics with mocked Supabase RPCs."""

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
        # Deterministic corruption: flip one ciphertext byte in the middle - the GCM
        # tag MUST reject it. (Base64-tail edits can decode to identical bytes: flaky.)
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
    def test_claim_returns_job_and_finish_records_status(self):
        calls = []

        def fake_rpc(action, params):
            calls.append((action, params))
            if action == "cloud_claim_job":
                return [{"job_id": "j1", "user_id": "u1", "youtube_url": "https://youtu.be/x"}]
            return None

        with patch.object(yc, "_supabase_rpc", side_effect=fake_rpc):
            job = yc.claim_remote_job("owner-pc-1")
            self.assertEqual(job.job_id, "j1")
            yc.finish_remote_job("j1", "done", "vid", "")

        self.assertEqual(calls[1][0], "cloud_finish_job")

    def test_empty_queue_returns_none(self):
        with patch.object(yc, "_supabase_rpc", return_value=[]):
            self.assertIsNone(yc.claim_remote_job("w"))

    def test_poller_processes_claimed_job_through_ingest(self):
        processed = []
        with patch.object(yc, "_supabase_rpc", return_value=[]), \
             patch.object(yc, "claim_remote_job", return_value=yc.RemoteJob("j9", "u9", "https://youtu.be/ok")):
            poller = yc.RemoteJobPoller(ingest_video=lambda url: processed.append(url) or {"status": "DOWNLOADED", "video_id": "v9"})
            poller._process(poller and yc.claim_remote_job("w"))
        self.assertEqual(processed, ["https://youtu.be/ok"])

    def test_ingest_failure_marks_job_failed(self):
        with patch.object(yc, "_supabase_rpc", return_value=[]) as rpc, \
             patch.object(yc, "claim_remote_job", return_value=yc.RemoteJob("j8", "u8", "https://youtu.be/bad")):
            poller = yc.RemoteJobPoller(ingest_video=lambda url: {"status": "FAILED", "error": "private"})
            poller._process(yc.claim_remote_job("w"))
        finish_calls = [c for c in rpc.call_args_list if c.args[0] == "cloud_finish_job"]
        self.assertEqual(finish_calls[0].args[1]["p_status"], "failed")


if __name__ == "__main__":
    unittest.main()
