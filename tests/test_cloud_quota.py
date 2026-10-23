import unittest

from dispatch.cloud.media_store import CLIP_VIDEO, THUMBNAIL
from dispatch.cloud.quota import UsageSummary, evaluate_upload_reservation

GIB = 1024 ** 3
MIB = 1024 ** 2


def evaluate(usage=None, kind=CLIP_VIDEO, mime="video/mp4", expected=100 * MIB, **overrides):
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


class TestQuotaKernel(unittest.TestCase):
    def test_valid_reservation_allowed(self):
        decision = evaluate()
        self.assertTrue(decision.allowed, decision.reason)
        self.assertEqual(decision.reason, "ok")

    def test_user_quota_exceeded(self):
        usage = UsageSummary(user_active_bytes=1_900_000_000, user_pending_bytes=200_000_000,
                             user_outstanding_reservations=0, global_active_bytes=0, global_pending_bytes=0)
        decision = evaluate(usage, expected=100 * MIB)  # 2.1 GB > 2 GB quota
        self.assertFalse(decision.allowed)
        self.assertIn("User storage quota exceeded", decision.reason)

    def test_user_quota_boundary_is_inclusive(self):
        usage = UsageSummary(user_active_bytes=2_000_000_000 - 100 * MIB, user_pending_bytes=0,
                             user_outstanding_reservations=0, global_active_bytes=0, global_pending_bytes=0)
        self.assertTrue(evaluate(usage, expected=100 * MIB).allowed)
        self.assertFalse(evaluate(usage, expected=100 * MIB + 1).allowed)

    def test_global_budget_exceeded(self):
        usage = UsageSummary(0, 0, 0, global_active_bytes=10_000_000_000, global_pending_bytes=0)
        decision = evaluate(usage, expected=1 * MIB)
        self.assertFalse(decision.allowed)
        self.assertIn("Global storage budget exceeded", decision.reason)

    def test_outstanding_reservation_limit(self):
        usage = UsageSummary(0, 0, user_outstanding_reservations=3, global_active_bytes=0, global_pending_bytes=0)
        decision = evaluate(usage, expected=1 * MIB)
        self.assertFalse(decision.allowed)
        self.assertIn("Too many outstanding uploads", decision.reason)

    def test_oversize_clip_object_rejected(self):
        decision = evaluate(expected=512 * MIB + 1)
        self.assertFalse(decision.allowed)
        self.assertIn("exceeds the clip_video limit", decision.reason)

    def test_thumbnail_cap_uses_thumbnail_limit(self):
        self.assertTrue(evaluate(kind=THUMBNAIL, mime="image/jpeg", expected=8 * MIB).allowed)
        decision = evaluate(kind=THUMBNAIL, mime="image/jpeg", expected=8 * MIB + 1)
        self.assertFalse(decision.allowed)
        self.assertIn("exceeds the thumbnail limit", decision.reason)

    def test_unsupported_mime_rejected(self):
        decision = evaluate(mime="video/quicktime")
        self.assertFalse(decision.allowed)
        self.assertIn("Unsupported MIME type", decision.reason)

    def test_video_mime_not_valid_for_thumbnail_kind(self):
        decision = evaluate(kind=THUMBNAIL, mime="video/mp4", expected=1 * MIB)
        self.assertFalse(decision.allowed)

    def test_unknown_kind_rejected(self):
        decision = evaluate(kind="document", mime="application/pdf", expected=1 * MIB)
        self.assertFalse(decision.allowed)
        self.assertIn("Unsupported object kind", decision.reason)

    def test_zero_and_negative_bytes_rejected(self):
        self.assertFalse(evaluate(expected=0).allowed)
        self.assertFalse(evaluate(expected=-5).allowed)


    def test_global_budget_boundary_exact_allowed(self):
        usage = UsageSummary(0, 0, 0, global_active_bytes=10_000_000_000 - 100 * MIB, global_pending_bytes=0)
        self.assertTrue(evaluate(usage, expected=100 * MIB).allowed)

    def test_global_budget_boundary_one_byte_over_rejected(self):
        usage = UsageSummary(0, 0, 0, global_active_bytes=10_000_000_000 - 100 * MIB, global_pending_bytes=0)
        self.assertFalse(evaluate(usage, expected=100 * MIB + 1).allowed)

    def test_user_ceiling_cannot_exceed_global_cap(self):
        from dispatch.cloud.config import CloudConfig
        cfg = CloudConfig()
        self.assertLessEqual(cfg.max_user_quota_bytes, cfg.global_object_budget_bytes)
        self.assertEqual(cfg.default_user_quota_bytes, 2_000_000_000)
        self.assertEqual(cfg.global_object_budget_bytes, 10_000_000_000)


if __name__ == "__main__":
    unittest.main()
