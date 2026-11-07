"""Unit tests for API Tenant Isolation & Ownership Enforcement.

Verifies:
1. GET /api/clips/{clip_id}:
   - Unauthenticated LAN peer gets 401.
   - User A accessing User A's clip gets 200.
   - User B accessing User A's clip gets 403.
   - Unauthenticated localhost caller accessing User A's clip gets 403.
2. DELETE /api/youtube/inbox/{video_id}:
   - User B attempting to delete User A's inbox entry gets 403.
   - User A deleting their own entry gets 200 and entry is deleted.
3. POST /api/youtube/ingest:
   - Identity is correctly propagated to ingest_video (checks user_id).
4. Paired device authentication:
   - Fails closed (returns None) on multi-owner ambiguity.
   - Resolves to explicit DISPATCH_USER_ID when provided.
"""

import os
import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from dispatch import db
from dispatch.web.app import app, resolve_caller_user_id

client = TestClient(app)


class TestTenantIsolation(unittest.TestCase):
    def setUp(self):
        db.init_db()
        for key in ["DISPATCH_USER_ID", "DISPATCH_PC_OWNER_USER_IDS", "DISPATCH_TRUST_LAN"]:
            if key in os.environ:
                del os.environ[key]

        with db.get_db_connection() as conn:
            conn.execute("DELETE FROM clips WHERE title LIKE 'Test Iso %'")
            conn.execute("DELETE FROM youtube_inbox WHERE video_id LIKE 'test-inbox-%'")
            conn.commit()

    def tearDown(self):
        for key in ["DISPATCH_USER_ID", "DISPATCH_PC_OWNER_USER_IDS", "DISPATCH_TRUST_LAN"]:
            if key in os.environ:
                del os.environ[key]
        with db.get_db_connection() as conn:
            conn.execute("DELETE FROM clips WHERE title LIKE 'Test Iso %'")
            conn.execute("DELETE FROM youtube_inbox WHERE video_id LIKE 'test-inbox-%'")
            conn.commit()

    # -------------------------------------------------------------------------
    # 1. GET /api/clips/{clip_id} Isolation
    # -------------------------------------------------------------------------

    def test_get_clip_detail_rejects_unauthenticated_lan_caller(self):
        clip_id = db.save_clip(
            session_id=None,
            chunk_id=None,
            start_time=0.0,
            end_time=15.0,
            title="Test Iso User A Secret Clip",
            hook="secret",
            description="",
            hashtags="#secret",
            virality_score=80,
            layout_mode="fit_blur",
            user_id="user_a",
        )

        headers = {"host": "192.168.1.100:8000"}
        with patch("dispatch.web.app.Request.client", new_callable=MagicMock) as mock_client:
            mock_client.host = "192.168.1.50"
            resp = client.get(f"/api/clips/{clip_id}", headers=headers)
            self.assertEqual(resp.status_code, 401)

    @patch("dispatch.web.app.resolve_caller_user_id")
    def test_get_clip_detail_enforces_user_isolation(self, mock_resolve_user):
        clip_id = db.save_clip(
            session_id=None,
            chunk_id=None,
            start_time=0.0,
            end_time=15.0,
            title="Test Iso User A Private Clip",
            hook="secret",
            description="",
            hashtags="#secret",
            virality_score=80,
            layout_mode="fit_blur",
            user_id="user_a",
        )

        # 1. User B accesses User A's clip -> 403 Forbidden
        mock_resolve_user.return_value = "user_b"
        resp = client.get(f"/api/clips/{clip_id}")
        self.assertEqual(resp.status_code, 403)
        self.assertIn("Clip belongs to another user", resp.text)

        # 2. Unauthenticated caller (None) accesses User A's clip -> 403 Forbidden
        mock_resolve_user.return_value = None
        resp = client.get(f"/api/clips/{clip_id}")
        self.assertEqual(resp.status_code, 403)

        # 3. User A accesses User A's clip -> 200 OK
        mock_resolve_user.return_value = "user_a"
        resp = client.get(f"/api/clips/{clip_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["id"], clip_id)

    # -------------------------------------------------------------------------
    # 2. DELETE /api/youtube/inbox/{video_id} Isolation
    # -------------------------------------------------------------------------

    @patch("dispatch.web.app.resolve_caller_user_id")
    def test_delete_youtube_inbox_item_enforces_ownership(self, mock_resolve_user):
        video_id = "test-inbox-v1"
        with db.get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO youtube_inbox (video_id, title, status, user_id)
                VALUES (?, ?, 'DOWNLOADED', ?)
                """,
                (video_id, "User A Upload", "user_a"),
            )
            conn.commit()

        # User B tries to delete User A's entry -> 403 Forbidden
        mock_resolve_user.return_value = "user_b"
        resp = client.delete(f"/api/youtube/inbox/{video_id}")
        self.assertEqual(resp.status_code, 403)
        self.assertIn("Inbox item belongs to another user", resp.text)

        # Verify entry still exists in DB
        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT video_id FROM youtube_inbox WHERE video_id = ?", (video_id,))
            self.assertIsNotNone(cursor.fetchone())

        # User A deletes their own entry -> 200 OK
        mock_resolve_user.return_value = "user_a"
        resp = client.delete(f"/api/youtube/inbox/{video_id}")
        self.assertEqual(resp.status_code, 200)

        # Verify entry was deleted
        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT video_id FROM youtube_inbox WHERE video_id = ?", (video_id,))
            self.assertIsNone(cursor.fetchone())

    # -------------------------------------------------------------------------
    # 3. POST /api/youtube/ingest Propagates Caller Identity
    # -------------------------------------------------------------------------

    @patch("dispatch.web.app.YouTubeInboxCatcher")
    @patch("dispatch.web.app.resolve_caller_user_id", return_value="creator_user_99")
    def test_youtube_ingest_propagates_authenticated_user_id(self, mock_resolve, mock_catcher_cls):
        mock_catcher_inst = MagicMock()
        mock_catcher_cls.return_value = mock_catcher_inst

        resp = client.post(
            "/api/youtube/ingest",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
        )
        self.assertEqual(resp.status_code, 200)

        import time
        time.sleep(0.1)
        mock_catcher_inst.ingest_video.assert_called_once_with(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            user_id="creator_user_99"
        )

    # -------------------------------------------------------------------------
    # 4. Paired Device Authentication Multi-Owner Ambiguity
    # -------------------------------------------------------------------------

    @patch("dispatch.sync.receiver.verify_token")
    @patch("dispatch.web.app.authorized_pc_owner_ids")
    def test_paired_device_fails_closed_on_multi_owner_ambiguity(self, mock_owners, mock_verify):
        from fastapi import HTTPException
        mock_verify.return_value = True
        mock_owners.return_value = ["owner_1", "owner_2"]

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.45"
        mock_request.headers = {"x-auth-token": "valid_device_token"}

        # Without DISPATCH_USER_ID: must raise 403 (fail-closed, not pick owner_1 or fall through)
        with self.assertRaises(HTTPException) as ctx:
            resolve_caller_user_id(mock_request)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("ambiguous", ctx.exception.detail)

        # With explicit DISPATCH_USER_ID set to owner_2: resolves to owner_2
        os.environ["DISPATCH_USER_ID"] = "owner_2"
        resolved = resolve_caller_user_id(mock_request)
        self.assertEqual(resolved, "owner_2")

        # With explicit DISPATCH_USER_ID set to unauthorized user: raises 403
        os.environ["DISPATCH_USER_ID"] = "stranger"
        with self.assertRaises(HTTPException) as ctx:
            resolve_caller_user_id(mock_request)
        self.assertEqual(ctx.exception.status_code, 403)

        # With single owner: resolves cleanly
        del os.environ["DISPATCH_USER_ID"]
        mock_owners.return_value = ["sole_owner"]
        resolved = resolve_caller_user_id(mock_request)
        self.assertEqual(resolved, "sole_owner")

    @patch("dispatch.sync.receiver.verify_token")
    @patch("dispatch.web.app.authorized_pc_owner_ids")
    def test_youtube_ingest_fails_closed_on_paired_device_ambiguity(self, mock_owners, mock_verify):
        mock_verify.return_value = True
        mock_owners.return_value = ["owner_1", "owner_2"]

        headers = {
            "x-auth-token": "valid_device_token",
            "host": "192.168.1.100:8000"
        }
        with patch("dispatch.web.app.Request.client", new_callable=MagicMock) as mock_client:
            mock_client.host = "192.168.1.45"
            resp = client.post(
                "/api/youtube/ingest",
                json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                headers=headers
            )
            self.assertEqual(resp.status_code, 403)
            self.assertIn("ambiguous", resp.text)


if __name__ == "__main__":
    unittest.main()
