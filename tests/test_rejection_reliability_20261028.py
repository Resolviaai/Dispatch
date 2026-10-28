"""Regression tests for deterministic clip-command draining."""

import os
import unittest
from unittest.mock import patch

from dispatch.cloud import sync


OWNER = "11111111-1111-1111-1111-111111111111"


class TestCommandDrainTargets(unittest.TestCase):
    def test_explicit_owner_is_used_even_with_no_local_clips(self):
        with patch.dict(os.environ, {"DISPATCH_PC_OWNER_USER_IDS": OWNER}, clear=False):
            self.assertIn(OWNER, sync._resolve_drain_user_ids())

    def test_explicit_user_list_bypasses_resolution(self):
        calls = []

        def fake_drain(worker_url, worker_token, user_id, limit):
            calls.append(user_id)
            return 1

        with patch.object(sync, "get_worker_token", return_value="token"), \
             patch.object(sync, "_worker_base", return_value="https://w.test"), \
             patch.object(sync, "_drain_for_user", side_effect=fake_drain), \
             patch.object(sync, "import_cloud_captions_for_clips", return_value=0), \
             patch.object(sync, "_flush_cloud_purges", return_value=0), \
             patch.object(sync.db, "purge_expired_rejections", return_value=0):
            applied = sync.drain_clip_commands(limit=1, user_ids=[OWNER])
        self.assertEqual(applied, 1)
        self.assertEqual(calls, [OWNER])


if __name__ == "__main__":
    unittest.main()
