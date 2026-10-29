import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from dispatch.cloud.sync import _publication_sync_payload
from dispatch.publisher.stats import (
    _youtube_service,
    parse_youtube_statistics_item,
    public_publication,
    refresh_publication_stats,
    stats_check_is_fresh,
)
from dispatch.web.app import app


class FakeVideos:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return self

    def execute(self):
        return self.response


class FakeYouTube:
    def __init__(self, response):
        self._videos = FakeVideos(response)

    def videos(self):
        return self._videos


def publication(**overrides):
    value = {
        "id": "pub_1",
        "clip_id": "clip_1",
        "platform": "youtube",
        "account_id": "account_1",
        "user_id": "user_1",
        "remote_id": "video_1",
        "remote_url": "https://youtube.com/shorts/video_1",
        "publish_mode": "public",
        "published_at": "2026-10-29T08:00:00+00:00",
        "view_count": None,
        "like_count": None,
        "comment_count": None,
        "stats_updated_at": None,
        "stats_checked_at": None,
    }
    value.update(overrides)
    return value


class TestPublicationStats(unittest.TestCase):
    def test_parse_youtube_statistics(self):
        self.assertEqual(
            parse_youtube_statistics_item({
                "statistics": {"viewCount": "1240", "likeCount": "86", "commentCount": "12"}
            }),
            {"views": 1240, "likes": 86, "comments": 12},
        )
        self.assertIsNone(parse_youtube_statistics_item(None))
        self.assertEqual(
            parse_youtube_statistics_item({"statistics": {}}),
            {"views": None, "likes": None, "comments": None},
        )

    def test_ttl_uses_attempt_time_not_only_success_time(self):
        now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
        recent_attempt = publication(stats_checked_at=(now - timedelta(minutes=5)).isoformat())
        old_attempt = publication(stats_checked_at=(now - timedelta(minutes=20)).isoformat())
        self.assertTrue(stats_check_is_fresh(recent_attempt, now))
        self.assertFalse(stats_check_is_fresh(old_attempt, now))

    def test_refresh_success_persists_nullable_counts(self):
        service = FakeYouTube({
            "items": [{"statistics": {"viewCount": "9", "commentCount": "2"}}]
        })
        with patch("dispatch.publisher.stats._youtube_service", return_value=service), \
             patch("dispatch.publisher.stats.db.record_publication_stats") as record:
            refreshed = refresh_publication_stats(publication())

        self.assertEqual(refreshed["view_count"], 9)
        self.assertEqual(refreshed["like_count"], None)
        self.assertEqual(refreshed["comment_count"], 2)
        record.assert_called_once()
        self.assertTrue(record.call_args.kwargs["succeeded"])
        self.assertIsNone(record.call_args.kwargs["like_count"])

    def test_refresh_with_no_usable_counters_does_not_erase_previous_values(self):
        old = publication(
            view_count=100,
            like_count=5,
            comment_count=3,
            stats_updated_at="2026-10-28T08:00:00+00:00",
            stats_checked_at="2026-10-28T08:00:00+00:00",
        )
        service = FakeYouTube({"items": [{"statistics": {}}]})
        with patch("dispatch.publisher.stats._youtube_service", return_value=service), \
             patch("dispatch.publisher.stats.db.record_publication_stats") as record:
            refreshed = refresh_publication_stats(old)
        self.assertEqual(refreshed["view_count"], 100)
        self.assertEqual(refreshed["like_count"], 5)
        self.assertEqual(refreshed["comment_count"], 3)
        record.assert_called_once()
        self.assertFalse(record.call_args.kwargs["succeeded"])

    def test_account_credentials_require_owner(self):
        with patch("dispatch.youtube_connections.get_account_credentials") as lookup:
            service = _youtube_service(publication(account_id="creator@example.com", user_id=""))
        self.assertIsNone(service)
        lookup.assert_not_called()

    def test_refresh_failure_keeps_old_stats_and_advances_retry_clock(self):
        old = publication(
            view_count=100,
            like_count=5,
            comment_count=3,
            stats_updated_at="2026-10-28T08:00:00+00:00",
            stats_checked_at="2026-10-28T08:00:00+00:00",
        )
        with patch("dispatch.publisher.stats._youtube_service", return_value=None), \
             patch("dispatch.publisher.stats.db.record_publication_stats") as record:
            refreshed = refresh_publication_stats(old)

        self.assertEqual(refreshed["view_count"], 100)
        self.assertIsNotNone(refreshed["stats_checked_at"])
        record.assert_called_once()
        self.assertFalse(record.call_args.kwargs["succeeded"])

    def test_public_shape_does_not_expose_credentials_or_ownership(self):
        value = public_publication(publication(view_count=0, like_count=0, comment_count=0,
                                               stats_updated_at="2026-10-29T08:00:00+00:00",
                                               stats_checked_at="2026-10-29T08:00:00+00:00"))
        self.assertEqual(value["stats"], {"views": 0, "likes": 0, "comments": 0})
        self.assertFalse(value["stats_stale"])
        self.assertNotIn("account_id", value)
        self.assertNotIn("user_id", value)

    def test_pc_sync_payload_normalizes_time_and_never_sends_metrics(self):
        row = publication(published_at="2026-10-28 08:00:00")
        with patch("dispatch.cloud.sync.db.get_publications_for_clips", return_value={"clip_1": [row]}):
            payload = _publication_sync_payload("clip_1")
        self.assertEqual(len(payload), 1)
        self.assertTrue(payload[0]["published_at"].endswith("+00:00"))
        self.assertNotIn("view_count", payload[0])
        self.assertEqual(payload[0]["account_id"], "account_1")

    def test_publications_endpoint_returns_contract(self):
        row = publication(view_count=1240, like_count=86, comment_count=12,
                          stats_updated_at="2026-10-29T08:00:00+00:00",
                          stats_checked_at="2026-10-29T08:00:00+00:00")
        with patch("dispatch.web.app.db.get_clip_by_id", return_value={"id": "clip_1", "user_id": None}), \
             patch("dispatch.web.app.db.get_publications_for_clips", return_value={"clip_1": [row]}), \
             patch("dispatch.web.app.refresh_publication_stats", return_value=row):
            response = TestClient(app).get("/api/clips/clip_1/publications")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["publications"][0]
        self.assertEqual(payload["platform"], "youtube")
        self.assertEqual(payload["stats"]["views"], 1240)
        self.assertNotIn("user_id", payload)
        self.assertNotIn("account_id", payload)


class FakeMetaResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class TestMetaPublicationStats(unittest.TestCase):
    def _ready_config(self):
        return types.SimpleNamespace(worker_ready=lambda: True)

    def test_refresh_instagram_success_via_worker(self):
        old = publication(
            platform="instagram",
            account_id="ig_acct",
            remote_id="1790001",
            remote_url=None,
        )
        sent = {}

        def fake_post(url, **kwargs):
            sent["url"] = url
            sent["headers"] = kwargs.get("headers") or {}
            sent["json"] = kwargs.get("json")
            return FakeMetaResponse(200, {"found": True, "stats": {"views": 555, "likes": 44, "comments": 6}})

        with patch("dispatch.publisher.stats.requests.post", side_effect=fake_post), \
             patch("dispatch.cloud.config.load_cloud_config", return_value=self._ready_config()), \
             patch("dispatch.publisher.stats.db.record_publication_stats") as record:
            refreshed = refresh_publication_stats(old)

        self.assertTrue(sent["url"].endswith("/sync/pc/publication-stats"))
        self.assertEqual(sent["headers"].get("X-Dispatch-User-Id"), "user_1")
        self.assertEqual(sent["json"], {"provider": "instagram", "account_id": "ig_acct", "remote_id": "1790001"})
        self.assertEqual(
            [refreshed["view_count"], refreshed["like_count"], refreshed["comment_count"]],
            [555, 44, 6],
        )
        record.assert_called_once()
        self.assertTrue(record.call_args.kwargs["succeeded"])

    def test_refresh_instagram_failure_keeps_old_stats_and_advances_retry_clock(self):
        old = publication(
            platform="instagram",
            account_id="ig_acct",
            remote_id="1790001",
            remote_url=None,
            view_count=100,
            like_count=5,
            comment_count=3,
            stats_updated_at="2026-10-28T08:00:00+00:00",
            stats_checked_at="2026-10-28T08:00:00+00:00",
        )
        with patch(
            "dispatch.publisher.stats.requests.post",
            side_effect=RuntimeError("worker stats endpoint returned 502: meta stats failed: graph down"),
        ), \
             patch("dispatch.cloud.config.load_cloud_config", return_value=self._ready_config()), \
             patch("dispatch.publisher.stats.db.record_publication_stats") as record:
            refreshed = refresh_publication_stats(old)

        self.assertEqual(refreshed["view_count"], 100)
        self.assertEqual(refreshed["like_count"], 5)
        self.assertEqual(refreshed["comment_count"], 3)
        self.assertIsNotNone(refreshed["stats_checked_at"])
        record.assert_called_once()
        self.assertFalse(record.call_args.kwargs["succeeded"])

    def test_refresh_facebook_success_without_views(self):
        old = publication(platform="facebook", account_id="page_1", remote_id="fb_video_1", remote_url=None)
        with patch(
            "dispatch.publisher.stats.requests.post",
            return_value=FakeMetaResponse(200, {"found": True, "stats": {"views": None, "likes": 12, "comments": 3}}),
        ), \
             patch("dispatch.cloud.config.load_cloud_config", return_value=self._ready_config()), \
             patch("dispatch.publisher.stats.db.record_publication_stats") as record:
            refreshed = refresh_publication_stats(old)

        self.assertEqual(refreshed["like_count"], 12)
        self.assertEqual(refreshed["comment_count"], 3)
        self.assertIsNone(refreshed["view_count"])
        record.assert_called_once()
        self.assertTrue(record.call_args.kwargs["succeeded"])

    def test_refresh_meta_worker_not_ready_marks_failure(self):
        old = publication(platform="instagram", account_id="ig_acct", remote_id="1790001", remote_url=None)
        with patch("dispatch.cloud.config.load_cloud_config",
                   return_value=types.SimpleNamespace(worker_ready=lambda: False)), \
             patch("dispatch.publisher.stats.requests.post") as post, \
             patch("dispatch.publisher.stats.db.record_publication_stats") as record:
            refreshed = refresh_publication_stats(old)

        post.assert_not_called()
        record.assert_called_once()
        self.assertFalse(record.call_args.kwargs["succeeded"])

    def test_refresh_skips_unsupported_platform_without_worker_call(self):
        old = publication(platform="linkedin")
        with patch("dispatch.publisher.stats.requests.post") as post, \
             patch("dispatch.publisher.stats.db.record_publication_stats") as record:
            refreshed = refresh_publication_stats(old)

        post.assert_not_called()
        record.assert_not_called()
        self.assertIsNone(refreshed["view_count"])


if __name__ == "__main__":
    unittest.main()
