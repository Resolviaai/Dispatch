"""Wave 2 Slice 3 regression tests for PC Outbox (§33) and Social Publishers (§34)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dispatch import db
from dispatch.publisher import outbox
from dispatch.publisher.facebook import upload_facebook_reel
from dispatch.publisher.instagram import upload_instagram_reel
from dispatch.publisher.linkedin import upload_linkedin_video
from dispatch.publisher.twitter import upload_x_video
from dispatch.publisher.youtube import upload_youtube_short


class TestWave2OutboxHardening(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="wave2_pub_")
        self._tmp_path = Path(self._tmp.name)
        self._db_path = self._tmp_path / "wave2.db"
        self._db_patch = patch.object(db, "DB_PATH", self._db_path)
        self._db_patch.start()
        db.init_db()

    def tearDown(self):
        db.close_db_connections()
        self._db_patch.stop()
        self._tmp.cleanup()

    def _create_video_file(self, name: str = "clip.mp4") -> Path:
        p = self._tmp_path / name
        p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 256)
        return p

    def test_idempotency_key_uses_immutable_ids_only_not_title_or_description(self):
        k1 = outbox.build_publish_idempotency_key(
            clip_id="clip_42",
            platform="youtube_shorts",
            account_id="UC_123",
            render_fingerprint="fp_abc",
            revision=3,
        )
        k2 = outbox.build_publish_idempotency_key(
            clip_id="clip_42",
            platform="youtube",
            account_id="UC_123",
            render_fingerprint="fp_abc",
            revision=3,
        )
        self.assertEqual(k1, k2)
        self.assertTrue(k1.startswith("idem_"))

        # Different fingerprint or account produces a distinct key
        k_diff_fp = outbox.build_publish_idempotency_key("clip_42", "youtube", "UC_123", "fp_xyz", 3)
        k_diff_acct = outbox.build_publish_idempotency_key("clip_42", "youtube", "UC_999", "fp_abc", 3)
        self.assertNotEqual(k1, k_diff_fp)
        self.assertNotEqual(k1, k_diff_acct)

    def test_lock_released_and_uploading_row_cleaned_up_on_crash(self):
        # 1. _processing_lock releases even when _process_outbox_queue_internal raises BaseException
        with patch.object(outbox, "_process_outbox_queue_internal", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                outbox.process_outbox_queue()
        self.assertFalse(outbox._processing_lock.locked(), "_processing_lock must be released after crash")

        # 2. Dangling 'uploading' row transitions to failed/reconciliation_required on BaseException
        vid = self._create_video_file("crash.mp4")
        clip_id = db.save_clip(
            session_id=None,
            chunk_id=None,
            start_time=0.0,
            end_time=10.0,
            title="Crash Clip",
            hook="h",
            description="d",
            hashtags="#test",
            virality_score=80,
            layout_mode="fit_blur",
        )
        with db.get_db_connection() as conn:
            conn.execute(
                "UPDATE clips SET status = 'approved', rendered_layout_mode = 'fit_blur', video_path = ? WHERE id = ?",
                (str(vid), clip_id),
            )
            conn.execute(
                """INSERT INTO publishing_outbox (id, clip_id, platform, status, idempotency_key)
                   VALUES ('job_crash_1', ?, 'youtube', 'queued', 'idem_crash_1')""",
                (clip_id,),
            )
            conn.commit()

        with patch.object(outbox, "probe_video", return_value=(True, {"duration": 10.0}, None)), \
             patch.object(outbox, "upload_youtube_short", side_effect=KeyboardInterrupt("worker timeout")):
            with self.assertRaises(KeyboardInterrupt):
                outbox.process_outbox_queue()

        self.assertFalse(outbox._processing_lock.locked())
        with db.get_db_connection() as conn:
            row = conn.execute("SELECT status FROM publishing_outbox WHERE id = 'job_crash_1'").fetchone()
            clip_row = conn.execute("SELECT status FROM clips WHERE id = ?", (clip_id,)).fetchone()
        self.assertEqual(row["status"], "reconciliation_required")
        self.assertEqual(clip_row["status"], "approved")

    def test_terminal_failure_and_blocked_needs_auth_close_spinner_by_returning_clip_to_ready_review(self):
        vid = self._create_video_file("spinner.mp4")
        clip_id = db.save_clip(
            session_id=None,
            chunk_id=None,
            start_time=0.0,
            end_time=10.0,
            title="Spinner Clip",
            hook="h",
            description="d",
            hashtags="#test",
            virality_score=85,
            layout_mode="fit_blur",
            user_id="11111111-1111-1111-1111-111111111111",
        )
        with db.get_db_connection() as conn:
            conn.execute(
                "UPDATE clips SET status = 'approved', rendered_layout_mode = 'fit_blur', video_path = ? WHERE id = ?",
                (str(vid), clip_id),
            )
            conn.execute(
                """INSERT INTO publishing_outbox
                   (id, clip_id, user_id, platform, account_id, status, idempotency_key)
                   VALUES ('job_spin_auth', ?, '11111111-1111-1111-1111-111111111111', 'youtube', 'UC_REVOKED', 'queued', 'idem_spin_auth')""",
                (clip_id,),
            )
            conn.commit()

        with patch.object(outbox, "probe_video", return_value=(True, {"duration": 10.0}, None)), \
             patch("dispatch.youtube_connections.get_account_credentials", return_value={"needs_reauth": True, "error": "Revoked"}):
            outbox.process_outbox_queue()

        with db.get_db_connection() as conn:
            job_row = conn.execute("SELECT status FROM publishing_outbox WHERE id = 'job_spin_auth'").fetchone()
            clip_row = conn.execute("SELECT status FROM clips WHERE id = ?", (clip_id,)).fetchone()
        self.assertEqual(job_row["status"], "blocked_needs_auth")
        self.assertEqual(clip_row["status"], "ready_review")

    def test_stale_rerender_passes_full_render_params(self):
        src_vid = self._create_video_file("source.mp4")
        old_vid = self._create_video_file("old_clip.mp4")
        new_vid = self._create_video_file("new_clip.mp4")
        p_doc = {
            "timeline": {"segments": [{"id": "seg-1", "source_start": 1.0, "source_end": 12.0}]},
            "layout": {"mode": "crop_916", "crop_anchor_x": 0.25},
        }
        clip_id = db.save_clip(
            session_id=None,
            chunk_id=None,
            start_time=1.0,
            end_time=12.0,
            title="Stale Clip",
            hook="h",
            description="d",
            hashtags="#test",
            virality_score=91,
            layout_mode="crop_916",
        )
        with db.get_db_connection() as conn:
            conn.execute(
                """UPDATE clips
                   SET status = 'approved',
                       video_path = ?,
                       rendered_layout_mode = 'crop_916',
                       crop_anchor_x = 0.25,
                       project_document = ?,
                       render_fingerprint = 'fp_new_123',
                       rendered_fingerprint = 'fp_old_000',
                       render_stale = 1,
                       range_revision = 4
                   WHERE id = ?""",
                (str(old_vid), json.dumps(p_doc), clip_id),
            )
            conn.execute(
                """INSERT INTO publishing_outbox (id, clip_id, platform, status, idempotency_key)
                   VALUES ('job_stale_1', ?, 'youtube', 'queued', 'idem_stale_1')""",
                (clip_id,),
            )
            conn.commit()

        render_calls = []

        def fake_render(**kwargs):
            render_calls.append(kwargs)
            return {"video_path": new_vid}

        with patch.object(outbox, "probe_video", return_value=(True, {"duration": 11.0}, None)), \
             patch("dispatch.web.app.resolve_clip_source_path", return_value=src_vid), \
             patch("dispatch.video_engine.renderer.render_clip", side_effect=fake_render), \
             patch.object(outbox, "upload_youtube_short", return_value={"status": "published", "remote_id": "yt-99", "remote_url": "https://youtu.be/yt-99"}) as mock_up:
            outbox.process_outbox_queue()

        self.assertEqual(len(render_calls), 1)
        rc = render_calls[0]
        self.assertEqual(rc["layout_mode"], "crop_916")
        self.assertEqual(rc["expected_range_revision"], 4)
        self.assertEqual(rc["segments"], p_doc["timeline"]["segments"])
        self.assertEqual(rc["subtitle_options"]["crop_anchor_x"], 0.25)
        self.assertEqual(rc["subtitle_options"]["render_fingerprint"], "fp_new_123")
        self.assertEqual(rc["subtitle_options"]["project_document"], p_doc)
        self.assertEqual(mock_up.call_args.kwargs["video_path"], new_vid)

    def test_multi_platform_account_selection_and_twitter_bearer_forwarding(self):
        vid = self._create_video_file("multi.mp4")
        clip_id = db.save_clip(
            session_id=None,
            chunk_id=None,
            start_time=0.0,
            end_time=10.0,
            title="Multi Platform",
            hook="h",
            description="Clean description",
            hashtags="#dispatch",
            virality_score=95,
            layout_mode="fit_blur",
            user_id="11111111-1111-1111-1111-111111111111",
        )
        with db.get_db_connection() as conn:
            conn.execute(
                "UPDATE clips SET status = 'approved', rendered_layout_mode = 'fit_blur', video_path = ? WHERE id = ?",
                (str(vid), clip_id),
            )
            for jid, plat, acct in [
                ("j_ig", "instagram", "row-ig"),
                ("j_li", "linkedin", "row-li"),
                ("j_x", "x", "row-tw"),
                ("j_fb", "facebook", ""),
            ]:
                conn.execute(
                    """INSERT INTO publishing_outbox (id, clip_id, user_id, platform, account_id, status, idempotency_key)
                       VALUES (?, ?, '11111111-1111-1111-1111-111111111111', ?, ?, 'queued', ?)""",
                    (jid, clip_id, plat, acct, f"idem_{jid}"),
                )
            conn.commit()

        def fake_creds(account_id, user_id=None, provider=None):
            if provider == "instagram":
                return {"access_token": "tok-ig", "ig_user_id": "IG_REAL_1", "account_id": "IG_REAL_1", "api_base": "https://graph.instagram.com/v21.0"}
            if provider == "linkedin":
                return {"access_token": "tok-li", "author_urn": "urn:li:person:LI_REAL", "account_id": "urn:li:person:LI_REAL"}
            if provider == "twitter":
                return {"access_token": "tok-tw-oauth2", "account_id": "TW_REAL"}
            if provider == "facebook":
                return {"access_token": "tok-fb-page", "page_id": "FB_PAGE_REAL", "account_id": "FB_PAGE_REAL"}
            return {}

        with patch.object(outbox, "probe_video", return_value=(True, {"duration": 10.0}, None)), \
             patch("dispatch.youtube_connections.get_account_credentials", side_effect=fake_creds), \
             patch.object(outbox, "upload_instagram_reel", return_value={"status": "published", "remote_id": "ig1", "remote_url": "https://ig/1"}) as m_ig, \
             patch.object(outbox, "upload_linkedin_video", return_value={"status": "published", "remote_id": "li1", "remote_url": "https://li/1"}) as m_li, \
             patch.object(outbox, "upload_x_video", return_value={"status": "published", "remote_id": "tw1", "remote_url": "https://x/1"}) as m_tw, \
             patch.object(outbox, "upload_facebook_reel", return_value={"status": "published", "remote_id": "fb1", "remote_url": "https://fb/1"}) as m_fb:
            count = outbox.process_outbox_queue()

        self.assertEqual(count, 4)
        self.assertEqual(m_ig.call_args.kwargs["ig_user_id"], "IG_REAL_1")
        self.assertNotIn("dispatch_idempotency_key", m_ig.call_args.kwargs["caption"])
        self.assertEqual(m_li.call_args.kwargs["author_urn"], "urn:li:person:LI_REAL")
        self.assertEqual(m_tw.call_args.kwargs["bearer_token"], "tok-tw-oauth2")
        self.assertEqual(m_fb.call_args.kwargs["page_id"], "FB_PAGE_REAL")
        self.assertNotIn("dispatch_idempotency_key", m_fb.call_args.kwargs["description"])


class TestWave2SocialPublishers(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="wave2_adapters_")
        self._video = Path(self._tmp.name) / "video.mp4"
        self._video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"1" * 1024)

    def tearDown(self):
        self._tmp.cleanup()

    def test_youtube_resumable_next_chunk_and_401_permission_error(self):
        fake_req = MagicMock()
        fake_req.next_chunk.side_effect = [
            (MagicMock(progress=lambda: 0.5), None),
            (None, {"id": "yt_123", "status": {"uploadStatus": "uploaded"}}),
        ]
        fake_svc = MagicMock()
        fake_svc.videos.return_value.insert.return_value = fake_req
        fake_svc.videos.return_value.list.return_value.execute.return_value = {
            "items": [{"status": {"uploadStatus": "processed"}}]
        }

        with patch("googleapiclient.discovery.build", return_value=fake_svc), \
             patch("googleapiclient.http.MediaFileUpload") as mock_media:
            res = upload_youtube_short(
                video_path=self._video,
                title="Test YT",
                description="Desc",
                tags="#Shorts",
                credentials_data={"access_token": "ya29.test"},
            )
        self.assertEqual(res["status"], "published")
        self.assertEqual(res["remote_id"], "yt_123")
        self.assertEqual(mock_media.call_args.kwargs["chunksize"], 8 * 1024 * 1024)

        # 401 HttpError translates to PermissionError (blocked_needs_auth)
        from googleapiclient.errors import HttpError
        err_401 = HttpError(MagicMock(status=401, reason="Unauthorized"), b'{"error":{"message":"invalidCredentials"}}')
        fake_req_401 = MagicMock()
        fake_req_401.next_chunk.side_effect = err_401
        fake_svc.videos.return_value.insert.return_value = fake_req_401
        with patch("googleapiclient.discovery.build", return_value=fake_svc):
            with self.assertRaises(PermissionError):
                upload_youtube_short(
                    video_path=self._video,
                    title="Test YT 401",
                    description="Desc",
                    tags="#Shorts",
                    credentials_data={"access_token": "ya29.revoked"},
                )

    def test_instagram_container_poll_and_oauth190_permission_error(self):
        post_calls = []
        get_calls = []

        def fake_post(url, **kwargs):
            post_calls.append((url, kwargs))
            resp = MagicMock(status_code=200)
            if url.endswith("/media"):
                resp.json.return_value = {"id": "cont_1", "uri": "https://rupload.facebook.com/ig-video/cont_1"}
            elif "rupload" in url:
                resp.json.return_value = {"success": True}
            elif url.endswith("/media_publish"):
                resp.json.return_value = {"id": "ig_media_9"}
            return resp

        def fake_get(url, **kwargs):
            get_calls.append((url, kwargs))
            resp = MagicMock(status_code=200)
            if "cont_1" in url:
                code = "FINISHED" if len([c for c in get_calls if "cont_1" in c[0]]) >= 2 else "IN_PROGRESS"
                resp.json.return_value = {"status_code": code}
            else:
                resp.json.return_value = {"permalink": "https://www.instagram.com/reel/ig_media_9/"}
            return resp

        with patch("dispatch.publisher.instagram.requests.post", side_effect=fake_post), \
             patch("dispatch.publisher.instagram.requests.get", side_effect=fake_get):
            res = upload_instagram_reel(
                video_path=self._video,
                caption="Reel caption",
                access_token="ig-tok",
                ig_user_id="17841400",
                api_base="https://graph.instagram.com/v21.0",
                poll_interval_seconds=0.0,
            )
        self.assertEqual(res["remote_id"], "ig_media_9")
        self.assertTrue(post_calls[0][0].startswith("https://graph.instagram.com/v21.0/17841400/media"))

        # Meta OAuth 190 / 401 raises PermissionError immediately
        err_resp = MagicMock(status_code=401, text='{"error":{"code":190}}')
        err_resp.json.return_value = {"error": {"code": 190, "type": "OAuthException", "message": "Invalid OAuth"}}
        with patch("dispatch.publisher.instagram.requests.post", return_value=err_resp):
            with self.assertRaises(PermissionError):
                upload_instagram_reel(self._video, "cap", access_token="bad", ig_user_id="1784")

    def test_linkedin_multipart_put_no_cdn_bearer_and_empty_201_restli_urn(self):
        put_headers_seen = []
        finalize_payloads = []

        def fake_post(url, **kwargs):
            resp = MagicMock()
            if "initializeUpload" in url:
                resp.status_code = 200
                resp.content = b"{}"
                resp.json.return_value = {
                    "value": {
                        "video": "urn:li:video:555",
                        "uploadToken": "li-up-tok",
                        "uploadInstructions": [
                            {"uploadUrl": "https://li-cdn.test/p1", "firstByte": 0, "lastByte": 511},
                            {"uploadUrl": "https://li-cdn.test/p2", "firstByte": 512, "lastByte": self._video.stat().st_size - 1},
                        ],
                    }
                }
            elif "finalizeUpload" in url:
                finalize_payloads.append(kwargs.get("json"))
                resp.status_code = 200
                resp.content = b"{}"
                resp.json.return_value = {}
            elif url.endswith("/rest/posts"):
                resp.status_code = 201
                resp.content = b""
                resp.text = ""
                resp.headers = {"x-restli-id": "urn:li:share:777"}
                resp.json.side_effect = ValueError("empty body")
            return resp

        def fake_put(url, **kwargs):
            put_headers_seen.append(kwargs.get("headers") or {})
            data = kwargs.get("data")
            if hasattr(data, "read"):
                data.read()
            resp = MagicMock(status_code=200, headers={"ETag": f'"etag-{len(put_headers_seen)}"'})
            return resp

        def fake_get(url, **kwargs):
            resp = MagicMock(status_code=200, content=b"{}")
            resp.json.return_value = {"status": "AVAILABLE"}
            return resp

        with patch("dispatch.publisher.linkedin.requests.post", side_effect=fake_post), \
             patch("dispatch.publisher.linkedin.requests.put", side_effect=fake_put), \
             patch("dispatch.publisher.linkedin.requests.get", side_effect=fake_get):
            res = upload_linkedin_video(
                video_path=self._video,
                commentary="LinkedIn post",
                title="Title",
                access_token="li-tok",
                author_urn="12345",
                poll_interval_seconds=0.0,
            )

        self.assertEqual(res["remote_id"], "urn:li:share:777")
        self.assertEqual(len(put_headers_seen), 2)
        self.assertTrue(all("Authorization" not in h for h in put_headers_seen))
        self.assertEqual(finalize_payloads[0]["finalizeUploadRequest"]["uploadedPartIds"], ["etag-1", "etag-2"])

    def test_twitter_oauth2_v2_media_and_status_poll_and_401(self):
        post_urls = []

        def fake_post(url, **kwargs):
            post_urls.append((url, kwargs))
            resp = MagicMock(status_code=200, content=b"{}")
            if url.endswith("/initialize"):
                resp.json.return_value = {"data": {"id": "media_v2_1"}}
            elif url.endswith("/finalize"):
                resp.json.return_value = {"data": {"processing_info": {"state": "pending", "check_after_secs": 0}}}
            elif url.endswith("/2/tweets"):
                self.assertEqual(kwargs["json"]["media"]["media_ids"], ["media_v2_1"])
                resp.json.return_value = {"data": {"id": "tweet_999"}}
            else:
                resp.json.return_value = {}
            return resp

        def fake_get(url, **kwargs):
            resp = MagicMock(status_code=200, content=b"{}")
            resp.json.return_value = {"processing_info": {"state": "succeeded"}}
            return resp

        with patch("dispatch.publisher.twitter.requests.post", side_effect=fake_post), \
             patch("dispatch.publisher.twitter.requests.get", side_effect=fake_get):
            res = upload_x_video(
                video_path=self._video,
                text="Hello X",
                bearer_token="tw-oauth2-bearer",
                poll_interval_seconds=0.0,
            )
        self.assertEqual(res["remote_id"], "tweet_999")
        self.assertIn("https://api.x.com/2/media/upload/initialize", [u for u, _ in post_urls])

        err_401 = MagicMock(status_code=401, content=b"{}", text="unauthorized")
        err_401.json.return_value = {"detail": "Unauthorized"}
        with patch("dispatch.publisher.twitter.requests.post", return_value=err_401):
            with self.assertRaises(PermissionError):
                upload_x_video(self._video, "Hello X", bearer_token="expired")

    def test_facebook_page_token_lookup_rupload_and_status_poll(self):
        def fake_get(url, **kwargs):
            resp = MagicMock(status_code=200, content=b"{}")
            if url.endswith("/me/accounts"):
                resp.json.return_value = {"data": [{"id": "PAGE_42", "access_token": "PAGE_TOK_42"}]}
            else:
                resp.json.return_value = {"status": {"video_status": "ready"}}
            return resp

        def fake_post(url, **kwargs):
            resp = MagicMock(status_code=200, content=b"{}")
            if url.endswith("/video_reels") and kwargs.get("data", {}).get("upload_phase") == "start":
                self.assertEqual(kwargs["headers"]["Authorization"], "Bearer PAGE_TOK_42")
                resp.json.return_value = {"video_id": "fb_vid_42", "upload_url": "https://rupload.facebook.com/video-upload/v21.0/fb_vid_42"}
            elif "rupload" in url:
                self.assertEqual(kwargs["headers"]["Authorization"], "OAuth PAGE_TOK_42")
                resp.json.return_value = {"success": True}
            elif url.endswith("/video_reels") and kwargs.get("data", {}).get("upload_phase") == "finish":
                resp.json.return_value = {"success": True}
            return resp

        with patch("dispatch.publisher.facebook.requests.get", side_effect=fake_get), \
             patch("dispatch.publisher.facebook.requests.post", side_effect=fake_post):
            res = upload_facebook_reel(
                video_path=self._video,
                description="FB Reel",
                access_token="USER_TOK",
                page_id=None,
                poll_interval_seconds=0.0,
            )
        self.assertEqual(res["remote_id"], "fb_vid_42")
        self.assertEqual(res["status"], "published")


if __name__ == "__main__":
    unittest.main()
