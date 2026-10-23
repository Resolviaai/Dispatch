import unittest
from unittest.mock import patch

from dispatch.cloud.config import (
    GIB,
    MIB,
    CloudConfig,
    is_secret_env_name,
    load_cloud_config,
)


class TestCloudConfigDefaults(unittest.TestCase):
    def test_defaults_match_documented_limits(self):
        cfg = load_cloud_config(env={})
        self.assertEqual(cfg.default_user_quota_bytes, 2_000_000_000)
        self.assertEqual(cfg.max_user_quota_bytes, 10_000_000_000)
        self.assertEqual(cfg.global_object_budget_bytes, 10_000_000_000)
        self.assertEqual(cfg.max_clip_object_bytes, 512 * MIB)
        self.assertEqual(cfg.max_thumbnail_bytes, 8 * MIB)
        self.assertEqual(cfg.max_outstanding_uploads_per_user, 3)
        self.assertEqual(cfg.presign_expiry_seconds, 600)
        self.assertEqual(cfg.reservation_expiry_seconds, 1800)

    def test_unconfigured_by_default(self):
        cfg = load_cloud_config(env={})
        self.assertFalse(cfg.worker_ready)


class TestCloudConfigEnv(unittest.TestCase):
    def test_env_overrides_apply(self):
        cfg = load_cloud_config(
            env={
                "DISPATCH_WORKER_URL": "https://worker.example.com",
                "DISPATCH_WORKER_TOKEN": "secret-token",
                "DISPATCH_CLOUD_DEFAULT_USER_QUOTA_BYTES": "1500000000",
                "DISPATCH_CLOUD_MAX_OUTSTANDING_UPLOADS": "5",
            }
        )
        self.assertTrue(cfg.worker_ready)
        self.assertEqual(cfg.default_user_quota_bytes, 1500000000)
        self.assertEqual(cfg.max_outstanding_uploads_per_user, 5)

    def test_invalid_integer_limit_raises(self):
        with self.assertRaises(ValueError):
            load_cloud_config(env={"DISPATCH_CLOUD_GLOBAL_BUDGET_BYTES": "not-a-number"})

    def test_repr_masks_secrets(self):
        cfg = load_cloud_config(
            env={
                "DISPATCH_WORKER_TOKEN": "super-secret-token",
                "SUPABASE_SERVICE_ROLE_KEY": "super-secret-key",
            }
        )
        rendered = repr(cfg)
        self.assertNotIn("super-secret-token", rendered)
        self.assertNotIn("super-secret-key", rendered)
        self.assertNotIn("worker_token=secret-token", rendered)

    def test_supabase_credentials_are_worker_side_and_never_loaded_on_the_pc(self):
        cfg = load_cloud_config(
            env={
                "SUPABASE_URL": "https://proj.supabase.co",
                "SUPABASE_SERVICE_ROLE_KEY": "service-secret",
                "SUPABASE_ANON_KEY": "anon-secret",
            }
        )
        self.assertEqual(cfg.worker_url, "")
        self.assertEqual(cfg.worker_token, "")
        self.assertFalse(cfg.worker_ready)
        self.assertNotIn("service", repr(cfg))

    def test_real_environment_secrets_never_loaded_into_placeholder_names(self):
        with patch.dict("os.environ", {"DISPATCH_WORKER_TOKEN": "from-real-env"}):
            cfg = load_cloud_config(env={})
        self.assertEqual(cfg.worker_token, "")


class TestSecretEnvNames(unittest.TestCase):
    def test_known_secret_names_flagged(self):
        self.assertTrue(is_secret_env_name("DISPATCH_WORKER_TOKEN"))
        self.assertTrue(is_secret_env_name("SUPABASE_SERVICE_ROLE_KEY"))
        self.assertFalse(is_secret_env_name("SUPABASE_URL"))
        self.assertFalse(is_secret_env_name("PATH"))


if __name__ == "__main__":
    unittest.main()
