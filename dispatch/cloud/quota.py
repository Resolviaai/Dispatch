"""Pure reservation decision kernel (plan doc D6).

This module holds the quota/MIME/size rules as a side-effect-free function so they
are unit-testable without any cloud dependency. The authoritative enforcement is the
Postgres transaction + Worker (plan doc D4/D5); this kernel is the shared definition
of the policy both must implement. Keep the two in sync deliberately.
"""

from __future__ import annotations

from dataclasses import dataclass

from dispatch.cloud.media_store import CLIP_VIDEO, THUMBNAIL

ALLOWED_MIME_TYPES = {
    CLIP_VIDEO: frozenset({"video/mp4"}),
    THUMBNAIL: frozenset({"image/jpeg"}),
}


@dataclass(frozen=True)
class UsageSummary:
    """Server-computed storage usage for quota decisions.

    user_active_bytes/user_pending_bytes are summed from media_objects with status
    active/pending; outstanding reservations count pending uploads not yet completed.
    """

    user_active_bytes: int
    user_pending_bytes: int
    user_outstanding_reservations: int
    global_active_bytes: int
    global_pending_bytes: int


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    reason: str


def evaluate_upload_reservation(
    usage: UsageSummary,
    object_kind: str,
    mime_type: str,
    expected_bytes: int,
    *,
    user_quota_bytes: int,
    global_budget_bytes: int,
    max_clip_object_bytes: int,
    max_thumbnail_bytes: int,
    max_outstanding_uploads_per_user: int,
) -> QuotaDecision:
    """Decide whether one upload reservation may be issued.

    Expected bytes bound the attempt only; the actual object size is re-verified at
    completion (plan doc D5). Concurrent requests are serialized by the database
    transaction that computes `usage` and inserts the reservation row together.
    """
    if object_kind not in ALLOWED_MIME_TYPES:
        return QuotaDecision(False, f"Unsupported object kind: {object_kind!r}")
    if mime_type not in ALLOWED_MIME_TYPES[object_kind]:
        return QuotaDecision(False, f"Unsupported MIME type for {object_kind}: {mime_type!r}")
    if expected_bytes <= 0:
        return QuotaDecision(False, "Expected byte count must be positive")

    kind_cap = max_clip_object_bytes if object_kind == CLIP_VIDEO else max_thumbnail_bytes
    if expected_bytes > kind_cap:
        return QuotaDecision(
            False,
            f"Expected {expected_bytes} bytes exceeds the {object_kind} limit of {kind_cap} bytes",
        )

    user_used = usage.user_active_bytes + usage.user_pending_bytes
    if user_used + expected_bytes > user_quota_bytes:
        return QuotaDecision(
            False,
            f"User storage quota exceeded: {user_used} used, {expected_bytes} requested, "
            f"quota {user_quota_bytes}",
        )
    if usage.user_outstanding_reservations >= max_outstanding_uploads_per_user:
        return QuotaDecision(
            False,
            f"Too many outstanding uploads: {usage.user_outstanding_reservations} "
            f"(limit {max_outstanding_uploads_per_user})",
        )

    global_used = usage.global_active_bytes + usage.global_pending_bytes
    if global_used + expected_bytes > global_budget_bytes:
        return QuotaDecision(
            False,
            f"Global storage budget exceeded: {global_used} used, {expected_bytes} requested, "
            f"budget {global_budget_bytes}",
        )
    return QuotaDecision(True, "ok")
