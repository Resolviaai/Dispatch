"""Wave 2b straggler regression tests (P0 fixes, verified 2026-11-13).

Covers:
- Migration 20261113000032: cloud_complete_upload redefined with the reserve
  contract's per-kind caps (edit_window -> 512 MiB) — static SQL contract plus a
  static check that the Worker call signature is unchanged.
- LinkedIn adapter: env-overridable API version header, transient-retry status
  poll (5xx/429), and TimeoutError instead of creating the post while the video
  is still processing.
- Facebook adapter: TimeoutError when the Reel never reaches a ready status, and
  stored-credential page_id disambiguation for multi-Page accounts.
- Retention: 'ready_review' clips protect their source video in all three tiers.
- Quota kernel: kind 'edit_window' (video/mp4) accepted up to the 512 MiB cap.
- Direct ingest: bounded Range-resume of interrupted .part downloads.

All external HTTP is mocked; Windows-safe (no POSIX-only calls).
"""

from __future__ import annotations

import importlib
import os
import re
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from dispatch import db, direct_ingest
from dispatch.storage.retention import enforce_retention_policy
from tests.test_migration_grant_hygiene import _strip_sql_comments

MIB = 1024 * 1024
MIGRATIONS_DIR = Path("supabase/migrations")
STRAGGLER_MIGRATION = MIGRATIONS_DIR / "20261113000032_wave2b_straggler.sql"


# ---------------------------------------------------------------------------
# 1. Supabase migration contract (static)
# ---------------------------------------------------------------------------

class TestWave2bMigrationContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw_sql = STRAGGLER_MIGRATION.read_text(encoding="utf-8")
        cls.sql = _strip_sql_comments(cls.raw_sql)
        cls.fn_match = re.search(
            r"create\s+or\s+replace\s+function\s+public\.cloud_complete_upload\s*\(([^)]*)\)"
            r"\s*returns\s+media_objects\s+language\s+plpgsql\s+security\s+definer\s+"
            r"set\s+search_path\s*=\s*pg_catalog\s*,\s*public\s+as\s+\$\$(.*?)\$\$;",
            cls.sql,
            re.I | re.S,
        )

    def test_migration_file_exists_and_is_latest(self):
        self.assertTrue(STRAGGLER_MIGRATION.is_file())
        names = [p.name for p in sorted(MIGRATIONS_DIR.glob("*.sql"))]
        self.assertIn("20261113000032_wave2b_straggler.sql", names)
        self.assertEqual(names[-1], "20261113000032_wave2b_straggler.sql")

    def test_complete_upload_redefined_with_edit_window_cap(self):
        self.assertIsNotNone(self.fn_match, "cloud_complete_upload definition missing in 0032")
        body = self.fn_match.group(2)
        self.assertIn("when 'clip_video' then 536870912", body)
        self.assertIn("when 'edit_window' then 536870912", body)
        self.assertIn("else 8388608", body)
        # The stale two-branch cap from 0002/0003 must be gone.
        self.assertNotIn(
            "case v_obj.kind when 'clip_video' then 536870912 else 8388608 end",
            " ".join(body.split()),
        )

    def test_signature_return_and_security_attributes_unchanged(self):
        self.assertIsNotNone(self.fn_match)
        params = " ".join(self.fn_match.group(1).lower().split())
        self.assertEqual(
            params, "p_reservation_id uuid, p_actual_bytes bigint, p_final_key text"
        )
        preamble = re.search(
            r"^(.*?)as\s+\$\$",
            self.fn_match.group(0),
            re.I | re.S,
        )
        header = " ".join((preamble.group(1) if preamble else self.fn_match.group(0)).lower().split())
        self.assertIn("returns media_objects", header)
        self.assertIn("language plpgsql security definer", header)
        self.assertIn("set search_path = pg_catalog, public", header)

    def test_0003_accounting_body_preserved(self):
        self.assertIsNotNone(self.fn_match)
        body = self.fn_match.group(2)
        self.assertIn("COMPLETE: unknown reservation", body)
        self.assertIn("COMPLETE: idempotent replay with different final key", body)
        self.assertIn("COMPLETE: reservation not pending", body)
        self.assertIn("COMPLETE: actual size exceeds reserved expectation", body)
        self.assertIn("COMPLETE: actual size exceeds kind cap", body)
        self.assertIn("pg_advisory_xact_lock(918273645)", body)
        self.assertIn("cloud_quota_usage(v_obj.user_id)", body)
        self.assertIn("10000000000", body)
        # Promotion-duplicate accounting and staging_key retention are intact.
        self.assertIn("2 * p_actual_bytes", body)
        self.assertIn("update upload_reservations set status = 'completed' where id = v_res.id", body)

    def test_only_wave2b_functions_are_redefined(self):
        fns = re.findall(
            r"create\s+or\s+replace\s+function\s+public\.(\w+)", self.sql, re.I
        )
        self.assertEqual(
            fns, ["cloud_complete_upload", "cloud_find_covering_window"]
        )

    def test_redefined_functions_revoke_public_and_grant_service_role_only(self):
        for fn in ("cloud_complete_upload", "cloud_find_covering_window"):
            self.assertRegex(
                self.sql,
                rf"revoke\s+all\s+on\s+function\s+public\.{fn}[^;]*"
                r"from\s+public\s*,\s*anon\s*,\s*authenticated\s*;",
                re.I | re.S,
            )
            self.assertRegex(
                self.sql,
                rf"grant\s+execute\s+on\s+function\s+public\.{fn}[^;]*to\s+service_role\s*;",
                re.I | re.S,
            )

    def test_worker_call_signature_unchanged(self):
        worker_src = Path("cloud-worker/src/index.ts").read_text(encoding="utf-8")
        m = re.search(
            r'rpc<[^>]*>\(env,\s*"cloud_complete_upload"\s*,\s*\{([^}]*)\}', worker_src, re.S
        )
        self.assertIsNotNone(m, "Worker cloud_complete_upload rpc call not found")
        params = " ".join(m.group(1).split())
        self.assertIn("p_reservation_id: body.reservation_id", params)
        self.assertIn("p_actual_bytes: head.size", params)
        self.assertIn("p_final_key: finalKey", params)


# ---------------------------------------------------------------------------
# 2. LinkedIn adapter
# ---------------------------------------------------------------------------

class TestLinkedInAdapter(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="wave2b_li_")
        self._video = Path(self._tmp.name) / "clip.mp4"
        self._video.write_bytes(b"V" * 1024)

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def _resp(status_code=200, payload=None, headers=None, text=""):
        resp = MagicMock(status_code=status_code, content=b"{}", text=text)
        resp.json.return_value = payload if payload is not None else {}
        resp.headers = headers or {}
        return resp

    def _init_payload(self):
        return {
            "value": {
                "video": "urn:li:video:555",
                "uploadToken": "li-up-tok",
                "uploadInstructions": [
                    {"uploadUrl": "https://li-cdn.test/p1", "firstByte": 0, "lastByte": self._video.stat().st_size - 1},
                ],
            }
        }

    def test_api_version_is_env_overridable_with_current_default(self):
        from dispatch.publisher import linkedin as li

        try:
            with patch.dict(os.environ, {"DISPATCH_LINKEDIN_API_VERSION": "202601"}):
                reloaded = importlib.reload(li)
                self.assertEqual(reloaded.LINKEDIN_API_VERSION, "202601")
        finally:
            os.environ.pop("DISPATCH_LINKEDIN_API_VERSION", None)
            importlib.reload(li)
        self.assertEqual(li.LINKEDIN_API_VERSION, "202610")

    def test_version_header_uses_module_constant(self):
        from dispatch.publisher import linkedin as li

        post_headers_seen = []

        def fake_post(url, **kwargs):
            post_headers_seen.append(kwargs.get("headers") or {})
            if "initializeUpload" in url:
                return self._resp(200, self._init_payload())
            if "finalizeUpload" in url:
                return self._resp(200, {})
            if url.endswith("/rest/posts"):
                return self._resp(201, {}, headers={"x-restli-id": "urn:li:share:777"}, text="")
            return self._resp(200, {})

        with patch("dispatch.publisher.linkedin.requests.post", side_effect=fake_post), \
             patch("dispatch.publisher.linkedin.requests.put", return_value=self._resp(200, {}, headers={"ETag": '"e1"'})), \
             patch("dispatch.publisher.linkedin.requests.get", return_value=self._resp(200, {"status": "AVAILABLE"})):
            res = li.upload_linkedin_video(
                video_path=self._video,
                commentary="LinkedIn post",
                access_token="li-tok",
                author_urn="urn:li:person:abc",
                poll_interval_seconds=0.0,
            )
        self.assertEqual(res["status"], "published")
        init_headers = post_headers_seen[0]
        self.assertEqual(init_headers["Linkedin-Version"], li.LINKEDIN_API_VERSION)
        self.assertEqual(init_headers["X-Restli-Protocol-Version"], "2.0.0")

    def test_status_poll_retries_transient_5xx_and_429(self):
        from dispatch.publisher import linkedin as li

        post_urls = []
        get_resps = [
            self._resp(503),
            self._resp(429),
            self._resp(200, {"status": "AVAILABLE"}),
        ]

        def fake_post(url, **kwargs):
            post_urls.append(url)
            if "initializeUpload" in url:
                return self._resp(200, self._init_payload())
            if "finalizeUpload" in url:
                return self._resp(200, {})
            return self._resp(201, {}, headers={"x-restli-id": "urn:li:share:777"})

        with patch("dispatch.publisher.linkedin.requests.post", side_effect=fake_post), \
             patch("dispatch.publisher.linkedin.requests.put", return_value=self._resp(200, {}, headers={"ETag": '"e1"'})), \
             patch("dispatch.publisher.linkedin.requests.get", side_effect=get_resps) as get_mock:
            res = li.upload_linkedin_video(
                video_path=self._video,
                commentary="cap",
                access_token="li-tok",
                author_urn="urn:li:person:abc",
                poll_interval_seconds=0.0,
            )
        self.assertEqual(res["status"], "published")
        self.assertEqual(get_mock.call_count, 3)  # 503 + 429 retried, then AVAILABLE
        self.assertTrue(any(u.endswith("/rest/posts") for u in post_urls))

    def test_status_poll_times_out_while_processing_and_never_creates_post(self):
        from dispatch.publisher import linkedin as li

        post_urls = []

        def fake_post(url, **kwargs):
            post_urls.append(url)
            if "initializeUpload" in url:
                return self._resp(200, self._init_payload())
            if "finalizeUpload" in url:
                return self._resp(200, {})
            return self._resp(201, {}, headers={"x-restli-id": "urn:li:share:777"})

        with patch("dispatch.publisher.linkedin.requests.post", side_effect=fake_post), \
             patch("dispatch.publisher.linkedin.requests.put", return_value=self._resp(200, {}, headers={"ETag": '"e1"'})), \
             patch("dispatch.publisher.linkedin.requests.get", return_value=self._resp(200, {"status": "PROCESSING"})) as get_mock:
            with self.assertRaises(TimeoutError):
                li.upload_linkedin_video(
                    video_path=self._video,
                    commentary="cap",
                    access_token="li-tok",
                    author_urn="urn:li:person:abc",
                    poll_interval_seconds=0.0,
                )
        self.assertEqual(get_mock.call_count, 10)
        self.assertFalse(
            any(u.endswith("/rest/posts") for u in post_urls),
            "post creation must not run while the video is still PROCESSING",
        )

    def test_status_poll_definitive_4xx_stops_polling_and_times_out(self):
        from dispatch.publisher import linkedin as li

        def fake_post(url, **kwargs):
            if "initializeUpload" in url:
                return self._resp(200, self._init_payload())
            if "finalizeUpload" in url:
                return self._resp(200, {})
            return self._resp(201, {}, headers={"x-restli-id": "urn:li:share:777"})

        with patch("dispatch.publisher.linkedin.requests.post", side_effect=fake_post), \
             patch("dispatch.publisher.linkedin.requests.put", return_value=self._resp(200, {}, headers={"ETag": '"e1"'})), \
             patch("dispatch.publisher.linkedin.requests.get", return_value=self._resp(404)) as get_mock:
            with self.assertRaises(TimeoutError):
                li.upload_linkedin_video(
                    video_path=self._video,
                    commentary="cap",
                    access_token="li-tok",
                    author_urn="urn:li:person:abc",
                    poll_interval_seconds=0.0,
                )
        self.assertEqual(get_mock.call_count, 1, "definitive 4xx must break the poll immediately")


# ---------------------------------------------------------------------------
# 3. Facebook adapter
# ---------------------------------------------------------------------------

class TestFacebookAdapter(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="wave2b_fb_")
        self._video = Path(self._tmp.name) / "clip.mp4"
        self._video.write_bytes(b"F" * 1024)

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def _resp(status_code=200, payload=None):
        resp = MagicMock(status_code=status_code, content=b"{}", text="")
        resp.json.return_value = payload if payload is not None else {}
        return resp

    def test_reel_never_ready_raises_timeout_error(self):
        from dispatch.publisher.facebook import upload_facebook_reel

        def fake_post(url, **kwargs):
            data = kwargs.get("data") or {}
            if url.endswith("/video_reels") and data.get("upload_phase") == "start":
                return self._resp(200, {"video_id": "fb_vid_t", "upload_url": "https://rupload.facebook.com/fb_vid_t"})
            if "rupload" in url:
                return self._resp(200, {"success": True})
            if url.endswith("/video_reels") and data.get("upload_phase") == "finish":
                return self._resp(200, {"success": True})
            return self._resp(200, {})

        with patch("dispatch.publisher.facebook.requests.post", side_effect=fake_post), \
             patch("dispatch.publisher.facebook.requests.get",
                   return_value=self._resp(200, {"status": {"video_status": "processing"}})) as get_mock:
            with self.assertRaises(TimeoutError):
                upload_facebook_reel(
                    video_path=self._video,
                    description="FB Reel",
                    access_token="PAGE_TOK",
                    page_id="PAGE_1",
                    poll_interval_seconds=0.0,
                )
        self.assertEqual(get_mock.call_count, 12)

    def test_preferred_credential_page_disambiguates_multi_page_account(self):
        from dispatch.publisher.facebook import upload_facebook_reel

        start_auth_seen = []

        def fake_post(url, **kwargs):
            data = kwargs.get("data") or {}
            if url.endswith("/video_reels") and data.get("upload_phase") == "start":
                start_auth_seen.append(kwargs["headers"]["Authorization"])
                return self._resp(200, {"video_id": "fb_vid_42", "upload_url": "https://rupload.facebook.com/fb_vid_42"})
            if "rupload" in url:
                return self._resp(200, {"success": True})
            if url.endswith("/video_reels") and data.get("upload_phase") == "finish":
                return self._resp(200, {"success": True})
            return self._resp(200, {})

        def fake_get(url, **kwargs):
            if url.endswith("/me/accounts"):
                return self._resp(200, {"data": [
                    {"id": "PAGE_42", "name": "Preferred", "access_token": "PAGE_TOK_42"},
                    {"id": "PAGE_99", "name": "Other", "access_token": "PAGE_TOK_99"},
                ]})
            return self._resp(200, {"status": {"video_status": "ready"}})

        env = {"FACEBOOK_ACCESS_TOKEN": "", "FACEBOOK_PAGE_ID": ""}
        with patch.dict(os.environ, env):
            with patch("dispatch.publisher.facebook.requests.post", side_effect=fake_post), \
                 patch("dispatch.publisher.facebook.requests.get", side_effect=fake_get):
                res = upload_facebook_reel(
                    video_path=self._video,
                    description="FB Reel",
                    access_token="USER_TOK",
                    page_id=None,
                    poll_interval_seconds=0.0,
                    credentials={"page_id": "PAGE_42"},
                )
        self.assertEqual(res["remote_id"], "fb_vid_42")
        self.assertEqual(res["status"], "published")
        self.assertEqual(start_auth_seen, ["Bearer PAGE_TOK_42"])

    def test_resolve_page_token_prefers_stored_page_and_keeps_ambiguity_error(self):
        from dispatch.publisher.facebook import _resolve_page_token

        def fake_get(url, **kwargs):
            return self._resp(200, {"data": [
                {"id": "PAGE_42", "name": "Preferred", "access_token": "PAGE_TOK_42"},
                {"id": "PAGE_99", "name": "Other", "access_token": "PAGE_TOK_99"},
            ]})

        with patch("dispatch.publisher.facebook.requests.get", side_effect=fake_get):
            tok, pid = _resolve_page_token("USER_TOK", None, preferred_page_id="PAGE_99")
            self.assertEqual((tok, pid), ("PAGE_TOK_99", "PAGE_99"))

            tok, pid = _resolve_page_token("USER_TOK", None, preferred_page_id="PAGE_42")
            self.assertEqual((tok, pid), ("PAGE_TOK_42", "PAGE_42"))

            with self.assertRaises(PermissionError):
                _resolve_page_token("USER_TOK", None, preferred_page_id="PAGE_MISSING")
            with self.assertRaises(PermissionError):
                _resolve_page_token("USER_TOK", None)


# ---------------------------------------------------------------------------
# 4. Retention: ready_review protection across all three tiers
# ---------------------------------------------------------------------------

class TestRetentionReadyReviewProtection(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="wave2b_ret_")
        self._tmp_path = Path(self._tmp.name)
        self._db_path = self._tmp_path / "wave2b.db"
        self._db_patch = patch.object(db, "DB_PATH", self._db_path)
        self._db_patch.start()
        db.init_db()
        self.storage_path = self._tmp_path / "storage"
        self.inbox_path = self.storage_path / "youtube_inbox"
        self.inbox_path.mkdir(parents=True, exist_ok=True)
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        db.close_db_connections()
        self._db_patch.stop()
        self._tmp.cleanup()

    def _ts(self, days_ago: float) -> str:
        return (self.now - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")

    def _add_source(self, name: str, age_days: float, clips: list) -> Path:
        """Create a source video with clips: list of (clip_id, status, published_age_days|None)."""
        f = self.inbox_path / name
        f.write_bytes(b"0" * MIB)
        with db.get_db_connection() as conn:
            conn.execute(
                "INSERT INTO sessions (id, status) VALUES (?, 'completed')",
                (f"sess_{name}",),
            )
            conn.execute(
                "INSERT INTO chunks (id, session_id, filename, filepath, created_at, status)"
                " VALUES (?, ?, ?, ?, ?, 'transcribed')",
                (f"chk_{name}", f"sess_{name}", name, str(f), self._ts(age_days)),
            )
            for clip_id, status, published_age in clips:
                conn.execute(
                    "INSERT INTO clips (id, session_id, chunk_id, title, duration, start_time,"
                    " end_time, status, created_at, published_at)"
                    " VALUES (?, ?, ?, ?, 10.0, 0, 10, ?, ?, ?)",
                    (
                        clip_id, f"sess_{name}", f"chk_{name}", clip_id,
                        status, self._ts(age_days),
                        self._ts(published_age) if published_age is not None else None,
                    ),
                )
            conn.commit()
        return f

    def test_tier1_7day_ready_review_source_is_preserved(self):
        protected = self._add_source("t1_ready.mp4", 8, [("t1_clip", "ready_review", None)])
        prunable = self._add_source("t1_rejected.mp4", 8, [("t1_clip_r", "rejected", None)])

        stats = enforce_retention_policy(
            storage_dir=self.storage_path,
            storage_ceiling_bytes=1024 * 1024 * 1024,
            now=self.now,
        )

        self.assertEqual(stats["pruned_7_day"], 1)
        self.assertTrue(protected.exists(), "ready_review clip must protect its source in Tier 1")
        self.assertFalse(prunable.exists())

    def test_tier2_72h_ready_review_source_is_preserved(self):
        protected = self._add_source("t2_ready.mp4", 4, [
            ("t2_pub", "published", 4),
            ("t2_draft", "ready_review", None),
        ])
        prunable = self._add_source("t2_allpub.mp4", 4, [("t2_pub2", "published", 4)])

        stats = enforce_retention_policy(
            storage_dir=self.storage_path,
            storage_ceiling_bytes=1024 * 1024 * 1024,
            now=self.now,
        )

        self.assertEqual(stats["pruned_72_hour_published"], 1)
        self.assertTrue(protected.exists(), "ready_review clip must protect its source in Tier 2")
        self.assertFalse(prunable.exists())

    def test_tier3_fifo_skips_ready_review_source(self):
        # Protected source is the OLDEST file: FIFO must still skip it.
        protected = self._add_source("t3_ready.mp4", 8, [("t3_clip", "ready_review", None)])
        evictable = self._add_source("t3_evict.mp4", 4, [])

        stats = enforce_retention_policy(
            storage_dir=self.storage_path,
            storage_ceiling_bytes=int(1.5 * MIB),  # 2 MiB stored -> ceiling exceeded
            now=self.now,
        )

        self.assertTrue(stats["ceiling_exceeded"])
        self.assertEqual(stats["pruned_ceiling_fifo"], 1)
        self.assertTrue(protected.exists(), "ready_review clip must protect its source in Tier 3 FIFO")
        self.assertFalse(evictable.exists())


# ---------------------------------------------------------------------------
# 5. Quota kernel: edit_window kind
# ---------------------------------------------------------------------------

def evaluate(usage=None, kind="clip_video", mime="video/mp4", expected=100 * MIB, **overrides):
    from dispatch.cloud.quota import UsageSummary, evaluate_upload_reservation

    params = dict(
        user_quota_bytes=2_000_000_000,
        global_budget_bytes=10_000_000_000,
        max_clip_object_bytes=512 * MIB,
        max_thumbnail_bytes=8 * MIB,
        max_outstanding_uploads_per_user=3,
    )
    params.update(overrides)
    return evaluate_upload_reservation(
        usage or UsageSummary(0, 0, 0, 0, 0), kind, mime, expected, **params
    )


class TestQuotaKernelEditWindow(unittest.TestCase):
    def test_edit_window_video_mp4_accepted_up_to_clip_cap(self):
        self.assertTrue(evaluate(kind="edit_window", mime="video/mp4", expected=400 * MIB).allowed)
        self.assertTrue(evaluate(kind="edit_window", mime="video/mp4", expected=512 * MIB).allowed)

    def test_edit_window_over_clip_cap_rejected(self):
        decision = evaluate(kind="edit_window", mime="video/mp4", expected=512 * MIB + 1)
        self.assertFalse(decision.allowed)
        self.assertIn("exceeds the edit_window limit", decision.reason)

    def test_edit_window_rejects_non_video_mimes(self):
        decision = evaluate(kind="edit_window", mime="image/jpeg", expected=1 * MIB)
        self.assertFalse(decision.allowed)
        self.assertIn("Unsupported MIME type for edit_window", decision.reason)

    def test_object_kind_registered_in_kernel_and_contract(self):
        from dispatch.cloud.media_store import EDIT_WINDOW, OBJECT_KINDS
        from dispatch.cloud.quota import ALLOWED_MIME_TYPES

        self.assertEqual(EDIT_WINDOW, "edit_window")
        self.assertIn(EDIT_WINDOW, OBJECT_KINDS)
        self.assertIn(EDIT_WINDOW, ALLOWED_MIME_TYPES)
        self.assertEqual(ALLOWED_MIME_TYPES[EDIT_WINDOW], frozenset({"video/mp4"}))


# ---------------------------------------------------------------------------
# 6. Direct ingest: Range resume of interrupted .part downloads
# ---------------------------------------------------------------------------

class _FakeStream:
    def __init__(self, status_code, chunk_factory, content_type="video/mp4"):
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self._chunk_factory = chunk_factory

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=1 << 20):
        yield from self._chunk_factory(chunk_size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _chunks(*blobs):
    return lambda chunk_size: iter(blobs)


def _drop_after(*blobs):
    def factory(chunk_size):
        yield from blobs
        raise requests.exceptions.ChunkedEncodingError("connection dropped mid-stream")
    return factory


class TestDirectIngestResume(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="wave2b_ingest_")
        self._inbox = Path(self._tmp.name)
        self._dir_patch = patch.object(direct_ingest, "YOUTUBE_INBOX_DIR", self._inbox)
        self._dir_patch.start()

    def tearDown(self):
        self._dir_patch.stop()
        self._tmp.cleanup()

    def _target(self, video_id):
        return self._inbox / f"{video_id}.mp4"

    def test_resumes_from_part_after_transient_drop(self):
        url = "https://r2.test/obj"
        responses = [
            _FakeStream(200, _drop_after(b"A" * MIB, b"A" * MIB, b"A" * MIB)),
            _FakeStream(206, _chunks(b"B" * MIB, b"B" * MIB, b"B" * (MIB // 2))),
        ]
        with patch.object(direct_ingest.requests, "get", side_effect=responses) as get_mock:
            target = direct_ingest._download_direct_video(url, "dir_resume")

        self.assertEqual(get_mock.call_count, 2)
        first_headers = get_mock.call_args_list[0].kwargs["headers"]
        second_headers = get_mock.call_args_list[1].kwargs["headers"]
        self.assertNotIn("Range", first_headers)
        self.assertEqual(second_headers["Range"], f"bytes={3 * MIB}-")
        data = target.read_bytes()
        self.assertEqual(len(data), 3 * MIB + 2 * MIB + MIB // 2)
        self.assertEqual(data[: 3 * MIB], b"A" * (3 * MIB))
        self.assertEqual(data[3 * MIB:], b"B" * (2 * MIB + MIB // 2))
        self.assertFalse((self._inbox / "dir_resume.mp4.part").exists())

    def test_restarts_from_zero_when_server_ignores_range(self):
        url = "https://r2.test/obj2"
        responses = [
            _FakeStream(200, _drop_after(b"A" * MIB, b"A" * MIB)),
            _FakeStream(200, _chunks(b"C" * MIB, b"C" * MIB, b"C" * MIB, b"C" * MIB)),
        ]
        with patch.object(direct_ingest.requests, "get", side_effect=responses) as get_mock:
            target = direct_ingest._download_direct_video(url, "dir_restart")

        self.assertEqual(get_mock.call_count, 2)
        self.assertEqual(
            get_mock.call_args_list[1].kwargs["headers"]["Range"], f"bytes={2 * MIB}-"
        )
        data = target.read_bytes()
        self.assertEqual(data, b"C" * (4 * MIB), "stale .part bytes must be truncated on a 200 restart")
        self.assertFalse((self._inbox / "dir_restart.mp4.part").exists())

    def test_cap_exceeded_aborts_without_retry_and_cleans_up(self):
        url = "https://r2.test/obj3"
        with patch.object(direct_ingest, "DIRECT_DOWNLOAD_CAP_BYTES", 3 * MIB), \
             patch.object(direct_ingest.requests, "get",
                          side_effect=[_FakeStream(200, _chunks(b"X" * MIB, b"X" * MIB, b"X" * MIB, b"X" * MIB))]) as get_mock:
            with self.assertRaises(RuntimeError):
                direct_ingest._download_direct_video(url, "dir_cap")
        self.assertEqual(get_mock.call_count, 1, "the object cap is definitive; no resume retry")
        self.assertFalse((self._inbox / "dir_cap.mp4.part").exists())
        self.assertFalse(self._target("dir_cap").exists())

    def test_exhausted_retries_raise_and_clean_up(self):
        url = "https://r2.test/obj4"
        with patch.object(direct_ingest.requests, "get",
                          side_effect=requests.exceptions.ConnectionError("boom")) as get_mock:
            with self.assertRaises(requests.exceptions.ConnectionError):
                direct_ingest._download_direct_video(url, "dir_fail")
        self.assertEqual(get_mock.call_count, 3, "bounded retry loop: exactly 3 attempts")
        self.assertFalse((self._inbox / "dir_fail.mp4.part").exists())
        self.assertFalse(self._target("dir_fail").exists())


if __name__ == "__main__":
    unittest.main()
