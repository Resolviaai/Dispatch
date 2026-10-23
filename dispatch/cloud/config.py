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
    # Connection settings (plan doc §4). The PC talks to the Worker only; the
    # Worker holds the R2 binding and the Supabase service-role key server-side.
    worker_url: str = ""
    worker_token: str = ""
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_anon_key: str = ""

    # Server-enforced limits (plan doc D6). Byte values are overridable by env.
    default_user_quota_bytes: int = 1 * GIB
    max_user_quota_bytes: int = 2 * GIB
    global_object_budget_bytes: int = 8 * GIB
    max_clip_object_bytes: int = 512 * MIB
    max_thumbnail_bytes: int = 8 * MIB
    max_outstanding_uploads_per_user: int = 3
    presign_expiry_seconds: int = 600
    reservation_expiry_seconds: int = 1800

    @property
    def worker_ready(self) -> bool:
        """True when the PC can reach the cloud control plane."""
        return bool(self.worker_url and self.worker_token)

    @property
    def supabase_ready(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    def __repr__(self) -> str:  # pragma: no cover - trivial masking
        return (
            "CloudConfig(worker_url=%r, worker_token=%r, supabase_url=%r, "
            "supabase_service_role_key=<masked>, supabase_anon_key=<masked>, ...)"
            % (self.worker_url, "<masked>" if self.worker_token else "", self.supabase_url)
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
_TEXT_ENV = {
    "DISPATCH_WORKER_URL": "worker_url",
    "DISPATCH_WORKER_TOKEN": "worker_token",
    "SUPABASE_URL": "supabase_url",
    "SUPABASE_SERVICE_ROLE_KEY": "supabase_service_role_key",
    "SUPABASE_ANON_KEY": "supabase_anon_key",
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
