"""Focused regression tests for the 2026-10-27 PC-side remediation (Cluster C).

Covers the contracts introduced by the audit:
- update_clip_media: rejected/approved clips can never be resurrected or demoted;
  queued_transcription clips are promoted; range_revision fencing holds.
- update_clip_thumbnail: safe from any status except rejected, never touches media.
- update_clip_metadata: ready_review-only revision-fenced metadata save.
- reject_clip: cancels queued publish jobs in the SAME transaction (no post-commit
  resurrection) and physically purges rendered media.
- save_clip_definitions: human-edited clips (trimmed range or typed captions)
  survive re-analysis idempotency deletes.
- get_outbox_queue: only approved clips earn queue slots (C12).
- cleanup_expired_published_clips: purges media ONLY for verifiably published
  clips backed by an outbox remote_id; approved/pending clips keep their media.
- update_youtube_video: expected_status CAS + FAILED auto-increments retry_count.
- Daily transcribe quota: day-roll advances the date (counter must NOT reset every
  call), limit enforcement, refund floor + date-guard.
- youtube-ack TOFU: a missing recorded hash is seeded from DISK BYTES, never the
  phone's claim; receipts are signed with the domain-separated ack key.
"""
import hashlib
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from dispatch.web.app import app
from dispatch import db
from dispatch.db import get_db_connection, init_db
from dispatch.sync.receiver import get_auth_token, _derive_receipt_hmac_key

client = TestClient(app)


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _mk_clip(
    clip_id: str,
    status: str = "ready_review",
    video_path: str = None,
    thumbnail_path: str = None,
    range_revision: int = 0,
    caption_data: str = None,
    chunk_id: str = None,
) -> None:
    with get_db_connection() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO clips (id, title, status, start_time, end_time, duration,
                                          video_path, thumbnail_path, range_revision, caption_data, chunk_id)
            VALUES (?, 'Remediation Clip', ?, 0.0, 10.0, 10.0, ?, ?, ?, ?, ?)
        """, (clip_id, status, video_path, thumbnail_path, range_revision, caption_data, chunk_id))
        conn.commit()


def _drop_clip(clip_id: str) -> None:
    with get_db_connection() as conn:
        conn.execute("DELETE FROM publishing_outbox WHERE clip_id = ?", (clip_id,))
        conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
        conn.commit()


def _seed_outbox_job(job_id: str, clip_id: str, status: str = "queued", remote_id: str = None) -> None:
    with get_db_connection() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO publishing_outbox
            (id, clip_id, platform, status, publish_mode, idempotency_key, remote_id, attempt_count)
            VALUES (?, ?, 'youtube', ?, 'private', ?, ?, 0)
        """, (job_id, clip_id, status, f"idem_{job_id}", remote_id))
        conn.commit()


def _drop_outbox_job(job_id: str) -> None:
    with get_db_connection() as conn:
        conn.execute("DELETE FROM publishing_outbox WHERE id = ?", (job_id,))
        conn.commit()


def _drop_youtube_video(video_id: str) -> None:
    with get_db_connection() as conn:
        conn.execute("DELETE FROM youtube_inbox WHERE video_id = ?", (video_id,))
        conn.commit()


def _get_setting(key: str):
    return db.get_setting(key)


def _set_setting(key: str, value: str):
    db.set_setting(key, value)


ANCHOR_QUOTA_DATE = db.get_setting("transcribe_quota_date")
ANCHOR_QUOTA_COUNT = db.get_setting("transcribe_quota_count")


def _cleanup_quota():
    # Best-effort restore of the global quota settings so other suites are unaffected.
    if ANCHOR_QUOTA_DATE is None:
        with get_db_connection() as conn:
            conn.execute("DELETE FROM settings WHERE key IN ('transcribe_quota_date', 'transcribe_quota_count')")
            conn.commit()
    else:
        _set_setting("transcribe_quota_date", ANCHOR_QUOTA_DATE)
        _set_setting("transcribe_quota_count", ANCHOR_QUOTA_COUNT or "0")


class TestUpdateClipMediaGuard(unittest.TestCase):
    """C1: a late async render can never resurrect or demote a decided clip."""

    def test_rejected_clip_is_never_resurrected(self):
        clip_id = _uid("clip_media_rej")
        _mk_clip(clip_id, status="rejected")
        try:
            self.assertFalse(db.update_clip_media(clip_id, "/tmp/new.mp4", "/tmp/new.jpg"))
            with get_db_connection() as conn:
                row = conn.execute("SELECT status, video_path FROM clips WHERE id = ?", (clip_id,)).fetchone()
            self.assertEqual(row["status"], "rejected")
            self.assertIsNone(row["video_path"], "Rejected media must stay purged")
        finally:
            _drop_clip(clip_id)

    def test_approved_clip_is_never_demoted(self):
        clip_id = _uid("clip_media_app")
        _mk_clip(clip_id, status="approved")
        try:
            self.assertFalse(db.update_clip_media(clip_id, "/tmp/new.mp4", "/tmp/new.jpg"))
            with get_db_connection() as conn:
                row = conn.execute("SELECT status FROM clips WHERE id = ?", (clip_id,)).fetchone()
            self.assertEqual(row["status"], "approved", "Late render must not demote approved clips into review")
        finally:
            _drop_clip(clip_id)

    def test_published_clip_is_never_demoted(self):
        clip_id = _uid("clip_media_pub")
        _mk_clip(clip_id, status="published")
        try:
            self.assertFalse(db.update_clip_media(clip_id, "/tmp/new.mp4", "/tmp/new.jpg"))
            with get_db_connection() as conn:
                row = conn.execute("SELECT status FROM clips WHERE id = ?", (clip_id,)).fetchone()
            self.assertEqual(row["status"], "published")
        finally:
            _drop_clip(clip_id)

    def test_ready_review_media_attachment_succeeds(self):
        clip_id = _uid("clip_media_rr")
        _mk_clip(clip_id, status="ready_review")
        try:
            self.assertTrue(db.update_clip_media(clip_id, "/tmp/ok.mp4", "/tmp/ok.jpg", "fit_blur"))
            with get_db_connection() as conn:
                row = conn.execute(
                    "SELECT status, video_path, thumbnail_path, rendered_layout_mode, render_stale FROM clips WHERE id = ?",
                    (clip_id,)
                ).fetchone()
            self.assertEqual(row["status"], "ready_review")
            self.assertEqual(row["video_path"], "/tmp/ok.mp4")
            self.assertEqual(row["rendered_layout_mode"], "fit_blur")
            self.assertEqual(row["render_stale"], 0)
        finally:
            _drop_clip(clip_id)

    def test_queued_transcription_is_promoted(self):
        clip_id = _uid("clip_media_qt")
        _mk_clip(clip_id, status="queued_transcription")
        try:
            self.assertTrue(db.update_clip_media(clip_id, "/tmp/q.mp4", "/tmp/q.jpg"))
            with get_db_connection() as conn:
                row = conn.execute("SELECT status FROM clips WHERE id = ?", (clip_id,)).fetchone()
            self.assertEqual(row["status"], "ready_review")
        finally:
            _drop_clip(clip_id)

    def test_range_revision_fencing(self):
        clip_id = _uid("clip_media_rev")
        _mk_clip(clip_id, status="ready_review", range_revision=4)
        try:
            # Stale editor revision -> no write
            self.assertFalse(db.update_clip_media(clip_id, "/tmp/s.mp4", "/tmp/s.jpg", expected_range_revision=2))
            with get_db_connection() as conn:
                row = conn.execute("SELECT video_path FROM clips WHERE id = ?", (clip_id,)).fetchone()
            self.assertIsNone(row["video_path"])
            # Matching revision -> write
            self.assertTrue(db.update_clip_media(clip_id, "/tmp/s.mp4", "/tmp/s.jpg", expected_range_revision=4))
            with get_db_connection() as conn:
                row = conn.execute("SELECT video_path FROM clips WHERE id = ?", (clip_id,)).fetchone()
            self.assertEqual(row["video_path"], "/tmp/s.mp4")
        finally:
            _drop_clip(clip_id)


class TestUpdateClipThumbnail(unittest.TestCase):
    def test_rejected_never_gets_thumbnail(self):
        clip_id = _uid("clip_thumb_rej")
        _mk_clip(clip_id, status="rejected")
        try:
            self.assertFalse(db.update_clip_thumbnail(clip_id, "/tmp/th.jpg"))
            with get_db_connection() as conn:
                row = conn.execute("SELECT status, thumbnail_path FROM clips WHERE id = ?", (clip_id,)).fetchone()
            self.assertEqual(row["status"], "rejected")
            self.assertIsNone(row["thumbnail_path"])
        finally:
            _drop_clip(clip_id)

    def test_approved_keeps_state_and_media(self):
        clip_id = _uid("clip_thumb_app")
        _mk_clip(clip_id, status="approved", video_path="/tmp/keep.mp4")
        try:
            self.assertTrue(db.update_clip_thumbnail(clip_id, "/tmp/th.png"))
            with get_db_connection() as conn:
                row = conn.execute(
                    "SELECT status, video_path, thumbnail_path FROM clips WHERE id = ?", (clip_id,)
                ).fetchone()
            self.assertEqual(row["status"], "approved", "Thumbnail refresh must not change clip state")
            self.assertEqual(row["video_path"], "/tmp/keep.mp4")
            self.assertEqual(row["thumbnail_path"], "/tmp/th.png")
        finally:
            _drop_clip(clip_id)


class TestUpdateClipMetadata(unittest.TestCase):
    """C3: revision-fenced metadata save for the InspectorPane."""

    def test_ready_review_save_and_revision_fencing(self):
        clip_id = _uid("clip_meta_rr")
        _mk_clip(clip_id, status="ready_review", range_revision=2)
        try:
            # Stale revision -> refused + unchanged
            self.assertFalse(db.update_clip_metadata(clip_id, title="Stale", expected_range_revision=1))
            with get_db_connection() as conn:
                row = conn.execute("SELECT title FROM clips WHERE id = ?", (clip_id,)).fetchone()
            self.assertNotEqual(row["title"], "Stale")

            # Current revision -> applied
            self.assertTrue(db.update_clip_metadata(
                clip_id,
                title="Fresh Title",
                hook="Hook",
                hashtags="#A #B",
                platform_targets="youtube,instagram",
                expected_range_revision=2,
            ))
            with get_db_connection() as conn:
                row = conn.execute(
                    "SELECT title, hook, hashtags, platform_targets FROM clips WHERE id = ?", (clip_id,)
                ).fetchone()
            self.assertEqual(row["title"], "Fresh Title")
            self.assertEqual(row["hook"], "Hook")
            self.assertEqual(row["hashtags"], "#A #B")
            self.assertEqual(row["platform_targets"], "youtube,instagram")
        finally:
            _drop_clip(clip_id)

    def test_non_reviewable_statuses_refused(self):
        for status in ("approved", "rejected", "published", "render_failed"):
            clip_id = _uid(f"clip_meta_{status}")
            _mk_clip(clip_id, status=status, chunk_id=None)
            try:
                self.assertFalse(
                    db.update_clip_metadata(clip_id, title="Should Not Apply", hook=None),
                    f"status {status} must refuse metadata edits",
                )
                with get_db_connection() as conn:
                    row = conn.execute("SELECT title FROM clips WHERE id = ?", (clip_id,)).fetchone()
                self.assertNotEqual(row["title"], "Should Not Apply")
            finally:
                _drop_clip(clip_id)

    def test_no_fields_returns_false(self):
        clip_id = _uid("clip_meta_nop")
        _mk_clip(clip_id, status="ready_review")
        try:
            self.assertFalse(db.update_clip_metadata(clip_id))
        finally:
            _drop_clip(clip_id)


class TestRejectClipCancelsOutbox(unittest.TestCase):
    """C12-adjacent: rejection cancels queued publish jobs in the same txn."""

    def test_reject_cancels_queued_job_and_purges_media(self):
        clip_id = _uid("clip_rej_out")
        tmp_dir = Path(tempfile.mkdtemp(prefix="dispatch_rej_test_"))
        video_path = tmp_dir / "rej.mp4"
        thumb_path = tmp_dir / "rej.jpg"
        video_path.write_bytes(b"fake rendered media")
        thumb_path.write_bytes(b"fake thumb")
        _mk_clip(clip_id, status="ready_review", video_path=str(video_path), thumbnail_path=str(thumb_path))
        job_id = _uid("job_rej_out")
        _seed_outbox_job(job_id, clip_id, status="queued")
        try:
            self.assertTrue(db.reject_clip(clip_id, reason="test reject"))
            with get_db_connection() as conn:
                job = conn.execute("SELECT status, last_error FROM publishing_outbox WHERE id = ?", (job_id,)).fetchone()
                clip_row = conn.execute("SELECT status, video_path FROM clips WHERE id = ?", (clip_id,)).fetchone()
                rej_count = conn.execute("SELECT COUNT(*) AS c FROM rejections WHERE clip_id = ?", (clip_id,)).fetchone()
            self.assertEqual(job["status"], "cancelled")
            self.assertIn("rejected", job["last_error"])
            self.assertEqual(clip_row["status"], "rejected")
            self.assertIsNone(clip_row["video_path"])
            self.assertEqual(rej_count["c"], 1)
            self.assertFalse(video_path.exists(), "Rejection must purge rendered media from disk")
            self.assertFalse(thumb_path.exists())
        finally:
            _drop_clip(clip_id)
            with get_db_connection() as conn:
                conn.execute("DELETE FROM rejections WHERE clip_id = ?", (clip_id,))
                conn.commit()

    def test_reject_leaves_published_jobs_alone(self):
        clip_id = _uid("clip_rej_pub")
        _mk_clip(clip_id, status="ready_review")
        pub_job = _uid("job_rej_pub")
        _seed_outbox_job(pub_job, clip_id, status="published", remote_id="yt_remote_1")
        try:
            self.assertTrue(db.reject_clip(clip_id))
            with get_db_connection() as conn:
                job = conn.execute("SELECT status, remote_id FROM publishing_outbox WHERE id = ?", (pub_job,)).fetchone()
            self.assertEqual(job["status"], "published", "Completed publishes are historical records, not queue rows")
            self.assertEqual(job["remote_id"], "yt_remote_1")
        finally:
            _drop_clip(clip_id)


class TestOutboxQueueApprovalGate(unittest.TestCase):
    """C12: only approved clips earn a publish slot."""

    def test_non_approved_clips_are_not_claimable(self):
        clip_id = _uid("clip_qgate")
        job_id = _uid("job_qgate")
        _mk_clip(clip_id, status="ready_review")
        _seed_outbox_job(job_id, clip_id, status="queued")
        try:
            queue_ids = {row["id"] for row in db.get_outbox_queue()}
            self.assertNotIn(job_id, queue_ids)

            with get_db_connection() as conn:
                conn.execute("UPDATE clips SET status = 'approved' WHERE id = ?", (clip_id,))
                conn.commit()
            queue_ids = {row["id"] for row in db.get_outbox_queue()}
            self.assertIn(job_id, queue_ids)
        finally:
            _drop_clip(clip_id)


class TestSaveClipDefinitionsPreservesEdits(unittest.TestCase):
    """C13: re-analysis must never delete human-edited clips."""

    def test_trimmed_and_captioned_clips_survive_reanalysis(self):
        chunk_id = _uid("chk_scd")
        video_id = _uid("vid_scd")
        tmp_dir = Path(tempfile.mkdtemp(prefix="dispatch_scd_test_"))
        tmp_file = tmp_dir / "src.mp4"
        tmp_file.write_bytes(b"synthetic")

        candidate_id = _uid("clip_cand")
        trimmed_id = _uid("clip_trim")
        captioned_id = _uid("clip_cap")

        # clips.chunk_id has a real FK to chunks(id) — register a true chunk row first
        real_chunk_id = db.register_chunk(
            session_id=None,
            filename=f"{chunk_id}.mp4",
            filepath=f"/tmp/{chunk_id}.mp4",
            file_hash=f"h_{chunk_id}",
        )

        _mk_clip(candidate_id, status="ready_review", video_path=None, chunk_id=real_chunk_id)
        _mk_clip(trimmed_id, status="ready_review", video_path=None, range_revision=3, chunk_id=real_chunk_id)
        _mk_clip(captioned_id, status="ready_review", video_path=None, caption_data='{"groups": []}', chunk_id=real_chunk_id)
        db.register_youtube_video(video_id=video_id, title="SCD", duration=300.0)

        try:
            saved = db.save_clip_definitions(
                chunk_id=real_chunk_id,
                session_id=None,
                video_id=video_id,
                clip_defs=[{
                    "start_time": 10.0,
                    "end_time": 40.0,
                    "title": "Fresh AI Clip",
                    "hook": "h",
                    "description": "d",
                    "hashtags": "#s",
                    "virality_score": 80,
                }],
            )
            self.assertEqual(len(saved), 1)

            with get_db_connection() as conn:
                remaining = {
                    row["id"]: row
                    for row in conn.execute(
                        "SELECT id, title FROM clips WHERE chunk_id = ?", (real_chunk_id,)
                    ).fetchall()
                }
            self.assertNotIn(candidate_id, remaining, "Unrendered virgin candidate must be replaced")
            self.assertIn(trimmed_id, remaining, "User-trimmed clip must survive re-analysis")
            self.assertIn(captioned_id, remaining, "User-captioned clip must survive re-analysis")
            self.assertIn(saved[0], remaining)
            self.assertEqual(remaining[trimmed_id]["title"], "Remediation Clip")

            row = db.get_youtube_video(video_id)
            self.assertEqual(row["status"], "CLIPS_DEFINED")
        finally:
            with get_db_connection() as conn:
                conn.execute("DELETE FROM clips WHERE chunk_id = ?", (real_chunk_id,))
                conn.execute("DELETE FROM chunks WHERE id = ?", (real_chunk_id,))
                conn.commit()
            _drop_youtube_video(video_id)


class TestCleanupExpiredPublishedClips(unittest.TestCase):
    """C2: purge media only for verifiably published clips."""

    def _mk_published(self, clip_id: str, video_file: Path, thumb_file: Path, published: bool) -> Path:
        _mk_clip(clip_id, status="published", video_path=str(video_file), thumbnail_path=str(thumb_file))
        with get_db_connection() as conn:
            conn.execute("UPDATE clips SET published_at = datetime('now', '-8 days') WHERE id = ?", (clip_id,))
            conn.commit()
        return video_file

    def test_verified_published_clip_is_purged_with_thumbnail_preserved(self):
        clip_id = _uid("clip_purge_ok")
        tmp_dir = Path(tempfile.mkdtemp(prefix="dispatch_purge_test_"))
        video_file = tmp_dir / "purge.mp4"
        thumb_file = tmp_dir / "purge.jpg"
        video_file.write_bytes(b"v")
        thumb_file.write_bytes(b"t")
        self._mk_published(clip_id, video_file, thumb_file, published=True)
        _seed_outbox_job(_uid("job_purge_ok"), clip_id, status="published", remote_id="yt_x")
        try:
            purged = db.cleanup_expired_published_clips(retention_days=7)
            self.assertGreaterEqual(purged, 1)
            self.assertFalse(video_file.exists(), "Expired verified publish must drop local media")
            self.assertTrue(thumb_file.exists(), "Thumbnail must be preserved")
            with get_db_connection() as conn:
                row = conn.execute("SELECT video_path, thumbnail_path, status FROM clips WHERE id = ?", (clip_id,)).fetchone()
            self.assertIsNone(row["video_path"])
            self.assertEqual(row["thumbnail_path"], str(thumb_file))
            self.assertEqual(row["status"], "published", "Metadata record survives the purge")
        finally:
            _drop_clip(clip_id)

    def test_pending_publish_clip_is_never_purged(self):
        clip_id = _uid("clip_purge_pending")
        tmp_dir = Path(tempfile.mkdtemp(prefix="dispatch_purge_pending_"))
        video_file = tmp_dir / "keep.mp4"
        thumb_file = tmp_dir / "keep.jpg"
        video_file.write_bytes(b"v")
        thumb_file.write_bytes(b"t")
        self._mk_published(clip_id, video_file, thumb_file, published=True)
        # Outbox row published but WITHOUT a real remote id -> unverified publish
        _seed_outbox_job(_uid("job_purge_pending"), clip_id, status="published", remote_id=None)
        try:
            before = video_file.stat().st_size
            db.cleanup_expired_published_clips(retention_days=7)
            self.assertTrue(video_file.exists())
            self.assertEqual(video_file.stat().st_size, before)
            with get_db_connection() as conn:
                row = conn.execute("SELECT video_path FROM clips WHERE id = ?", (clip_id,)).fetchone()
            self.assertIsNotNone(row["video_path"])
        finally:
            _drop_clip(clip_id)

    def test_recently_published_clip_stays(self):
        clip_id = _uid("clip_purge_fresh")
        tmp_dir = Path(tempfile.mkdtemp(prefix="dispatch_purge_fresh_"))
        video_file = tmp_dir / "fresh.mp4"
        thumb_file = tmp_dir / "fresh.jpg"
        video_file.write_bytes(b"v")
        thumb_file.write_bytes(b"t")
        _mk_clip(clip_id, status="published", video_path=str(video_file), thumbnail_path=str(thumb_file))
        _seed_outbox_job(_uid("job_purge_fresh"), clip_id, status="published", remote_id="yt_fresh")
        try:
            db.cleanup_expired_published_clips(retention_days=7)
            self.assertTrue(video_file.exists(), "Within retention window: media stays")
        finally:
            _drop_clip(clip_id)


class TestYouTubeVideoCASAndRetryCount(unittest.TestCase):
    """C10/C9: compare-and-swap writes + FAILED auto-increments retry_count."""

    def test_cas_writes(self):
        video_id = _uid("vid_cas")
        db.register_youtube_video(video_id=video_id, title="CAS", duration=10.0)
        try:
            with get_db_connection() as conn:
                conn.execute("UPDATE youtube_inbox SET status = 'CAS_BASE', retry_count = 2 WHERE video_id = ?", (video_id,))
                conn.commit()

            # Wrong expected_status -> no write, row untouched
            nada = db.update_youtube_video(video_id=video_id, status="CAS_NEXT", expected_status="CAS_OTHER")
            self.assertEqual(nada, 0)
            row = db.get_youtube_video(video_id)
            self.assertEqual(row["status"], "CAS_BASE")

            # Matching expected_status -> write + FAILED bumps retry_count
            applied = db.update_youtube_video(
                video_id=video_id, status="FAILED", last_error="probe dead",
                expected_status="CAS_BASE",
            )
            self.assertEqual(applied, 1)
            row = db.get_youtube_video(video_id)
            self.assertEqual(row["status"], "FAILED")
            self.assertEqual(row["retry_count"], 3, "FAILED with an error must increment retry_count")
        finally:
            _drop_youtube_video(video_id)

    def test_failed_without_error_leaves_retry_count(self):
        video_id = _uid("vid_cas_noe")
        db.register_youtube_video(video_id=video_id, title="CAS2", duration=10.0)
        try:
            with get_db_connection() as conn:
                conn.execute("UPDATE youtube_inbox SET status = 'CAS_BASE', retry_count = 0 WHERE video_id = ?", (video_id,))
                conn.commit()
            db.update_youtube_video(video_id=video_id, status="FAILED", expected_status="CAS_BASE")
            row = db.get_youtube_video(video_id)
            self.assertEqual(row["retry_count"], 0, "retry_count only advances on explicit error writes")
        finally:
            _drop_youtube_video(video_id)


class TestDailyTranscribeQuotaAtomicity(unittest.TestCase):
    """C14: single-txn quota; day-roll must ADVANCE the date."""

    def setUp(self):
        init_db()

    def tearDown(self):
        _cleanup_quota()

    def test_day_roll_advances_date_and_limits(self):
        _set_setting("transcribe_quota_date", "2000-01-01")
        _set_setting("transcribe_quota_count", "9")

        from dispatch.transcription.gemini_engine import check_and_increment_daily_transcribe_quota
        results = [check_and_increment_daily_transcribe_quota(max_daily_requests=3) for _ in range(4)]
        self.assertEqual(results, [True, True, True, False])

        self.assertEqual(_get_setting("transcribe_quota_date"), time.strftime("%Y-%m-%d"),
                         "Day-roll must write today's date, otherwise the counter resets every call")
        self.assertEqual(_get_setting("transcribe_quota_count"), "3")

    def test_refund_floors_at_zero(self):
        from dispatch.transcription.gemini_engine import check_and_increment_daily_transcribe_quota, refund_daily_transcribe_quota
        _set_setting("transcribe_quota_date", time.strftime("%Y-%m-%d"))
        _set_setting("transcribe_quota_count", "1")
        self.assertTrue(check_and_increment_daily_transcribe_quota(max_daily_requests=5))
        self.assertEqual(_get_setting("transcribe_quota_count"), "2")
        refund_daily_transcribe_quota()
        self.assertEqual(_get_setting("transcribe_quota_count"), "1")
        refund_daily_transcribe_quota()
        self.assertEqual(_get_setting("transcribe_quota_count"), "0")
        refund_daily_transcribe_quota()
        self.assertEqual(_get_setting("transcribe_quota_count"), "0", "Refund must floor at zero")

    def test_refund_is_date_guarded(self):
        from dispatch.transcription.gemini_engine import refund_daily_transcribe_quota
        _set_setting("transcribe_quota_date", "2000-01-01")
        _set_setting("transcribe_quota_count", "5")
        refund_daily_transcribe_quota()
        self.assertEqual(_get_setting("transcribe_quota_count"), "5",
                         "Refund must never gift a slot to a different day's counter")


class TestYouTubeAckTOFUAndDerivedReceipt(unittest.TestCase):
    """C18/C19: disk-bytes-are-truth seeding + domain-separated receipt key."""

    def setUp(self):
        os.environ["DISPATCH_AUTH_TOKEN"] = "dispatch_ack_tofu_test_token"
        init_db()

    def test_missing_recorded_hash_is_seeded_from_disk_and_mismatch_refused(self):
        token = get_auth_token()
        headers = {"x-auth-token": token}
        video_id = _uid("vid_tofu")
        dispatch_id = _uid("dsp_tofu")
        tmp_dir = Path(tempfile.mkdtemp(prefix="dispatch_tofu_"))
        media = tmp_dir / "seg.mp4"
        media.write_bytes(b"TRUE_DISK_BYTES_FOR_TOFU_TEST")
        disk_sha = hashlib.sha256(b"TRUE_DISK_BYTES_FOR_TOFU_TEST").hexdigest().lower()
        claimed_sha = "ab" * 32  # whatever the phone CLAIMS — not disk truth

        db.register_youtube_video(video_id=video_id, title="TOFU", duration=5.0, dispatch_id=dispatch_id)
        db.update_youtube_video(video_id=video_id, status="DOWNLOADED", local_video_path=str(media))
        try:
            # 1. Claimed hash not matching disk -> refused, NEVER adopted
            res = client.get(
                f"/api/sync/youtube-ack?video_id={video_id}&sha256={claimed_sha}",
                headers=headers,
            )
            self.assertEqual(res.status_code, 200)
            self.assertFalse(res.json()["acknowledged"])
            self.assertEqual(res.json().get("error"), "hash_mismatch")

            # 2. Seeded recorded hash is the disk truth, not the client's claim
            row = db.get_youtube_video(video_id)
            self.assertEqual((row.get("source_sha256") or ""), disk_sha,
                             "TOFU must record the BYTES ON DISK, never the client's claimed hash")

            # 3. Correct disk hash -> acknowledged with a domain-separated receipt
            res_ok = client.get(
                f"/api/sync/youtube-ack?video_id={video_id}&sha256={disk_sha}",
                headers=headers,
            )
            self.assertTrue(res_ok.json()["acknowledged"])
            data = res_ok.json()
            receipt = data["receipt"]
            canonical = data["canonical_id"]
            self.assertEqual(canonical, dispatch_id)
            # Derive independently and cross-check the helper
            import hmac as _hmac
            derived_key = _hmac.new(token.encode("utf-8"), b"dispatch-ack-receipt-v1", hashlib.sha256).digest()
            self.assertEqual(_derive_receipt_hmac_key(token), derived_key)
            import hmac as hmac_mod
            msg = f"{canonical}:{disk_sha}:DOWNLOADED:VERIFIED".encode("utf-8")
            self.assertEqual(
                receipt,
                hmac_mod.new(derived_key, msg, hashlib.sha256).hexdigest(),
            )
            # Legacy token-keyed receipt must NOT match (domain split is live)
            self.assertNotEqual(
                receipt,
                hmac_mod.new(token.encode("utf-8"), msg, hashlib.sha256).hexdigest(),
            )
        finally:
            _drop_youtube_video(video_id)

    def test_existing_recorded_hash_differences_are_refused(self):
        token = get_auth_token()
        headers = {"x-auth-token": token}
        video_id = _uid("vid_tofu2")
        tmp_dir = Path(tempfile.mkdtemp(prefix="dispatch_tofu2_"))
        media = tmp_dir / "seg2.mp4"
        media.write_bytes(b"OTHER_DISK_BYTES")
        disk_sha = hashlib.sha256(b"OTHER_DISK_BYTES").hexdigest().lower()

        db.register_youtube_video(video_id=video_id, title="TOFU2", duration=5.0)
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE youtube_inbox SET status='DOWNLOADED', local_video_path=?, source_sha256=? WHERE video_id=?",
                (str(media), "cd" * 32, video_id),
            )
            conn.commit()
        try:
            row = db.get_youtube_video(video_id)
            recorded = db.get_youtube_video(video_id).get("source_sha256")
            # Recorded hash says cd*32; phone claims disk hash -> mismatch (drift detected)
            res = client.get(f"/api/sync/youtube-ack?video_id={video_id}&sha256={disk_sha}", headers=headers)
            self.assertFalse(res.json()["acknowledged"])
            self.assertEqual(res.json().get("error"), "hash_mismatch")
            # And the recorded hash was NOT rewritten to the phone's claim
            self.assertEqual(db.get_youtube_video(video_id).get("source_sha256"), recorded)
        finally:
            _drop_youtube_video(video_id)


if __name__ == "__main__":
    unittest.main()
