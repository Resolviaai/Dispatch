"""Abstract media-store contract (plan doc D5/D8).

The PC, the Worker, and tests all speak this interface; concrete implementations are
the Worker-backed client (Phase D/E) and mocked stores in unit tests. Implementations
must verify ownership server-side, treat client-supplied sizes as unverified claims,
and never return permanent public URLs for private media.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


class MediaStoreError(Exception):
    """Base class for cloud media-store failures."""


class CloudNotConfiguredError(MediaStoreError):
    """Cloud env is not configured; callers should stay on the local workflow."""


class QuotaExceededError(MediaStoreError):
    """User or global storage budget exhausted; the upload was refused."""


class ReservationError(MediaStoreError):
    """Reservation missing, expired, or in a state that forbids this operation."""


class OwnershipError(MediaStoreError):
    """The authenticated user does not own the requested resource."""


class ValidationError(MediaStoreError):
    """Upload payload violates the media contract (size, MIME, or state)."""


CLIP_VIDEO = "clip_video"
THUMBNAIL = "thumbnail"
OBJECT_KINDS = (CLIP_VIDEO, THUMBNAIL)


@dataclass(frozen=True)
class UploadReservation:
    reservation_id: str
    user_id: str
    object_kind: str
    local_clip_id: Optional[str]
    staging_key: str
    expected_bytes: int
    mime_type: str
    expires_at: datetime


@dataclass(frozen=True)
class StoredObject:
    object_id: str
    user_id: str
    object_kind: str
    final_key: str
    byte_count: int  # actual, server-verified size
    mime_type: str
    status: str
    expires_at: Optional[datetime]


class MediaStore(ABC):
    """Control-plane operations for private clip media (plan doc D5)."""

    @abstractmethod
    def reserve_upload(
        self,
        user_id: str,
        object_kind: str,
        mime_type: str,
        expected_bytes: int,
        local_clip_id: Optional[str] = None,
        client_token: Optional[str] = None,
    ) -> UploadReservation:
        """Atomically check ownership + quotas and issue a short-lived staging PUT URL.

        The client token makes repeated reserve calls idempotent. The expected byte
        count is a claim, never proof; the reservation only bounds the attempt.
        """

    @abstractmethod
    def complete_upload(self, user_id: str, reservation_id: str) -> StoredObject:
        """Verify the actual R2 object (real size/MIME), promote staging -> immutable
        final key, record the verified byte count, release the reservation.

        Must be idempotent: repeating a completion returns the same StoredObject.
        """

    @abstractmethod
    def get_download_url(self, user_id: str, object_id: str) -> str:
        """Short-lived signed GET URL after verifying ownership and active status."""

    @abstractmethod
    def delete_media(self, user_id: str, object_id: str) -> None:
        """Idempotent delete for a valid application state.

        On R2 failure the object enters a deletion-pending state for scheduled retry;
        quota is not released until deletion is confirmed. Never report success
        before deletion is confirmed.
        """
