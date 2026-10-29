"""Latest per-publication metrics, fetched lazily with an existing account credential."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from dispatch import db

logger = logging.getLogger("dispatch.publisher.stats")

STATS_TTL = timedelta(minutes=15)


def _as_utc(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def stats_check_is_fresh(publication: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    checked = _as_utc(publication.get("stats_checked_at"))
    if checked is None:
        return False
    return (now or datetime.now(timezone.utc)) - checked < STATS_TTL


def _iso_utc(value: Optional[str]) -> Optional[str]:
    parsed = _as_utc(value)
    if parsed is None:
        return value
    return parsed.isoformat().replace("+00:00", "Z")


def _count(value: Any) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def parse_youtube_statistics_item(item: Optional[Dict[str, Any]]) -> Optional[Dict[str, Optional[int]]]:
    if not item:
        return None
    statistics = item.get("statistics")
    if not isinstance(statistics, dict):
        return None
    return {
        "views": _count(statistics.get("viewCount")),
        "likes": _count(statistics.get("likeCount")),
        "comments": _count(statistics.get("commentCount")),
    }


def _youtube_service(publication: Dict[str, Any]):
    platform = str(publication.get("platform") or "").lower()
    if platform not in ("youtube", "youtube_shorts"):
        return None
    account_id = str(publication.get("account_id") or "").strip()
    user_id = str(publication.get("user_id") or "").strip()
    credentials: Dict[str, Any] = {}
    if account_id:
        if not user_id:
            # Never resolve an account reference without its owner: the RPC can
            # otherwise return another profile's credential for the same account.
            return None
        try:
            from dispatch.youtube_connections import get_account_credentials
            credentials = get_account_credentials(account_id, user_id=user_id, timeout_seconds=5.0)
        except Exception as exc:
            logger.debug("Stats credential lookup failed for publication %s: %s", publication.get("id"), exc)
        if not credentials:
            return None
    elif user_id:
        return None
    if credentials:
        from dispatch.publisher.youtube import build_youtube_service_from_credentials
        return build_youtube_service_from_credentials(credentials, timeout_seconds=8)
    from dispatch.youtube_inbox.oauth import get_youtube_service
    return get_youtube_service()


def refresh_publication_stats(
    publication: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Return this publication with refreshed cached stats when the TTL allows it."""
    current = dict(publication)
    platform = str(current.get("platform") or "").lower()
    if platform not in ("youtube", "youtube_shorts"):
        return current
    if stats_check_is_fresh(current, now):
        return current

    checked_at = (now or datetime.now(timezone.utc)).isoformat()
    remote_id = str(current.get("remote_id") or "").strip()
    if not remote_id:
        db.record_publication_stats(str(current.get("id") or ""), checked_at, succeeded=False)
        current["stats_checked_at"] = checked_at
        return current

    try:
        service = _youtube_service(current)
        if service is None:
            raise RuntimeError("no usable YouTube credential for this publication")
        response = service.videos().list(part="statistics", id=remote_id).execute()
        items = response.get("items") if isinstance(response, dict) else None
        stats = parse_youtube_statistics_item(items[0] if isinstance(items, list) and items else None)
        if stats is None or not any(value is not None for value in stats.values()):
            raise RuntimeError("YouTube returned no usable statistics for this publication")
    except Exception as exc:
        logger.info("Publication stats unavailable for %s: %s", current.get("id"), exc)
        db.record_publication_stats(str(current.get("id") or ""), checked_at, succeeded=False)
        current["stats_checked_at"] = checked_at
        return current

    db.record_publication_stats(
        str(current.get("id") or ""),
        checked_at,
        succeeded=True,
        view_count=stats["views"],
        like_count=stats["likes"],
        comment_count=stats["comments"],
    )
    current["view_count"] = stats["views"]
    current["like_count"] = stats["likes"]
    current["comment_count"] = stats["comments"]
    current["stats_updated_at"] = checked_at
    current["stats_checked_at"] = checked_at
    return current


def public_publication(publication: Dict[str, Any]) -> Dict[str, Any]:
    platform = str(publication.get("platform") or "").lower()
    remote_id = publication.get("remote_id")
    remote_url = publication.get("remote_url")
    if not remote_url and remote_id and platform in ("youtube", "youtube_shorts"):
        remote_url = f"https://youtu.be/{remote_id}"

    updated = _as_utc(publication.get("stats_updated_at"))
    checked = _as_utc(publication.get("stats_checked_at"))
    stats_stale = updated is None or (checked is not None and checked > updated)

    return {
        "id": str(publication.get("id") or ""),
        "platform": platform,
        "remote_id": remote_id,
        "remote_url": remote_url,
        "published_at": _iso_utc(publication.get("published_at")),
        "publish_mode": publication.get("publish_mode"),
        "stats": {
            "views": publication.get("view_count"),
            "likes": publication.get("like_count"),
            "comments": publication.get("comment_count"),
        },
        "stats_updated_at": _iso_utc(publication.get("stats_updated_at")),
        "stats_stale": stats_stale,
    }
