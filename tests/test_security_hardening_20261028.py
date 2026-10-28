"""Regression tests for the 2026-10-28 security hardening batch.

Covers: owner-scoped rejection learning, publisher outbox authorization,
and removal of the publicly-known vault fallback key.
"""

import asyncio
import base64
import hashlib
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from dispatch import db
from dispatch import youtube_connections
import dispatch.web.app  # noqa: F401  (ensure the module, not the FastAPI object, is imported)

app_module = sys.modules["dispatch.web.app"]


USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"


class FakeRequest:
    def __init__(self, headers=None, client_host="127.0.0.1", path="/api/publisher/outbox"):
        self.headers = headers or {}
        self.client = SimpleNamespace(host=client_host)
        self.url = SimpleNamespace(path=path)
        self.method = "GET"


def _save_clip(user_id):
    return db.save_clip(
        session_id=None, chunk_id=None, start_time=0.0, end_time=10.0,
        title=f"Clip {user_id[:4]}", hook="hook", description="", hashtags="",
        virality_score=50, layout_mode="fit_blur", user_id=user_id,
    )


def _outbox_job(clip_id, user_id, status="reconciliation_required"):
    job_id = f"job_{user_id[:4]}_{status}"
    with db.get_db_connection() as conn:
        conn.execute(
            """INSERT INTO publishing_outbox
               (id, clip_id, platform, status, idempotency_key, user_id)
               VALUES (?, ?, 'youtube', ?, ?, ?)""",
            (job_id, clip_id, status, f"idem_{job_id}", user_id),
        )
        conn.commit()
    return job_id


class TestOwnerScopedRejectionLearning(unittest.TestCase):
    def test_rejections_are_isolated_by_owner(self):
        clip_a = _save_clip(USER_A)
        clip_b = _save_clip(USER_B)
        db.reject_clip(clip_a, reason="A private pacing note")
        db.reject_clip(clip_b, reason="B private pacing note")

        rows_a = db.get_all_rejections(limit=50, user_id=USER_A)
        rows_b = db.get_all_rejections(limit=50, user_id=USER_B)
        self.assertTrue(any("A private" in (r.get("reason") or "") for r in rows_a))
        self.assertFalse(any("B private" in (r.get("reason") or "") for r in rows_a))
        self.assertTrue(any("B private" in (r.get("reason") or "") for r in rows_b))
        self.assertFalse(any("A private" in (r.get("reason") or "") for r in rows_b))

    def test_anonymous_prompt_never_returns_owned_rows(self):
        clip_a = _save_clip(USER_A)
        db.reject_clip(clip_a, reason="owned secret note")
        anonymous_rows = db.get_all_rejections(limit=50, user_id=None)
        self.assertFalse(any("owned secret note" in (r.get("reason") or "") for r in anonymous_rows))


class TestPublisherOutboxAuthorization(unittest.TestCase):
    def test_outbox_list_is_scoped_to_caller(self):
        clip_a = _save_clip(USER_A)
        clip_b = _save_clip(USER_B)
        _outbox_job(clip_a, USER_A, status="queued")
        _outbox_job(clip_b, USER_B, status="queued")

        with patch.object(app_module, "resolve_caller_user_id", return_value=USER_A):
            result = asyncio.run(app_module.get_publisher_outbox(FakeRequest(), status=None))
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["jobs"][0]["user_id"], USER_A)

    def test_foreign_job_resolution_is_forbidden(self):
        clip_b = _save_clip(USER_B)
        job_b = _outbox_job(clip_b, USER_B)
        payload = SimpleNamespace(action="mark_failed", note="x", remote_id=None, remote_url=None)

        with patch.object(app_module, "resolve_caller_user_id", return_value=USER_A):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(app_module.resolve_reconciliation_job(job_b, payload, FakeRequest()))
        self.assertEqual(ctx.exception.status_code, 403)


class TestVaultFallbackRemoved(unittest.TestCase):
    def test_no_fallback_key_without_configuration(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DISPATCH_TOKEN_VAULT_KEY", None)
            self.assertEqual(youtube_connections.get_candidate_vault_keys(), [])

    def test_public_fallback_ciphertext_is_not_decryptable(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        fallback = hashlib.sha256(b"dispatch-fallback-vault-key").digest()
        iv = b"123456789012"
        ciphertext = AESGCM(fallback).encrypt(iv, b"refresh-token-value", None)
        payload = base64.urlsafe_b64encode(iv + ciphertext).decode().rstrip("=")

        configured = base64.b64encode(b"k" * 32).decode()
        with patch.dict(os.environ, {"DISPATCH_TOKEN_VAULT_KEY": configured}, clear=False):
            with self.assertRaises(Exception):
                youtube_connections.decrypt_refresh_token(payload)


if __name__ == "__main__":
    unittest.main()
