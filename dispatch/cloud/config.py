"""Environment-driven cloud configuration.

Secrets are read from the environment only and are masked in repr/str so they can
never leak into logs (plan doc §4). No cloud SDK is imported here; an unconfigured
environment simply reports not-ready and callers keep using the local workflow.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

GIB = 1024 ** 3
MIB = 1024 ** 2


@dataclass(frozen=True)
class CloudConfig:
    # PC-side settings (plan doc D8/D11): the PC talks to the Worker only. The Worker
    # holds the R2 binding and the Supabase service-role key; those credentials never
    # leave Cloudflare and are deliberately not modeled anywhere on the PC.
    worker_url: str = ""
    worker_token: str = ""

    # Server-enforced limits (plan doc D6; alpha brief 2026-10-23: decimal-byte quotas —
    # user default 2 GB, per-user ceiling and global cap 10 GB; clip/thumbnail stay MiB).
    default_user_quota_bytes: int = 2_000_000_000
    max_user_quota_bytes: int = 10_000_000_000
    global_object_budget_bytes: int = 10_000_000_000
    max_clip_object_bytes: int = 512 * MIB
    max_thumbnail_bytes: int = 8 * MIB
    max_outstanding_uploads_per_user: int = 3
    presign_expiry_seconds: int = 600
    reservation_expiry_seconds: int = 1800

    @property
    def worker_ready(self) -> bool:
        """True when the PC can reach the cloud control plane."""
        return bool(self.worker_url and self.worker_token)

    def __repr__(self) -> str:  # pragma: no cover - trivial masking
        return (
            "CloudConfig(worker_url=%r, worker_token=<masked>, ...)"
            % (self.worker_url,)
        )

    __str__ = __repr__


_SECRET_ENV = {"DISPATCH_WORKER_TOKEN", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY"}
_LIMIT_ENV = {
    "DISPATCH_CLOUD_DEFAULT_USER_QUOTA_BYTES": "default_user_quota_bytes",
    "DISPATCH_CLOUD_MAX_USER_QUOTA_BYTES": "max_user_quota_bytes",
    "DISPATCH_CLOUD_GLOBAL_BUDGET_BYTES": "global_object_budget_bytes",
    "DISPATCH_CLOUD_MAX_CLIP_OBJECT_BYTES": "max_clip_object_bytes",
    "DISPATCH_CLOUD_MAX_THUMBNAIL_BYTES": "max_thumbnail_bytes",
    "DISPATCH_CLOUD_MAX_OUTSTANDING_UPLOADS": "max_outstanding_uploads_per_user",
}
# Worker-side variables (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_ANON_KEY) are
# deliberately NOT mapped here: they configure the Cloudflare Worker, never the PC.
_TEXT_ENV = {
    "DISPATCH_WORKER_URL": "worker_url",
    "DISPATCH_WORKER_TOKEN": "worker_token",
}


def load_cloud_config(env: dict | None = None) -> CloudConfig:
    """Build a CloudConfig from environment variables (or an explicit dict for tests)."""
    source = os.environ if env is None else env
    values: dict = {target: source.get(name, "") for name, target in _TEXT_ENV.items()}
    for name, target in _LIMIT_ENV.items():
        raw = source.get(name)
        if raw:
            try:
                values[target] = int(raw)
            except ValueError:
                raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}")
    return CloudConfig(**values)


def is_secret_env_name(name: str) -> bool:
    """True for env var names that must never be logged or committed."""
    return name in _SECRET_ENV
