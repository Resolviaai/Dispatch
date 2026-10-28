"""Regression tests for canonical transcript words and the backfill tool."""

import contextlib
import io
import json
import unittest
import uuid

from dispatch import db
from dispatch.cloud.sync import extract_source_words
from scripts import backfill_window_transcripts as backfill


def _clip(chunk_id=None, with_words=None):
    clip_id = db.save_clip(
        session_id=None, chunk_id=chunk_id, start_time=0.0, end_time=10.0,
        title="Transcript Test", hook="hook", description="", hashtags="",
        virality_score=50, layout_mode="fit_blur",
    )
    if with_words is not None:
        with db.get_db_connection() as conn:
            conn.execute(
                "UPDATE clips SET caption_data = ? WHERE id = ?",
                (json.dumps({"words": with_words}), clip_id),
            )
            conn.commit()
    return clip_id


class TestCanonicalWords(unittest.TestCase):
    def test_caption_words_accept_w_text_and_word(self):
        clip_id = _clip(with_words=[
            {"word": "alpha", "start": 0.0, "end": 0.4},
            {"w": "beta", "start": 0.4, "end": 0.8},
            {"text": "gamma", "start": 0.8, "end": 1.2},
            {"word": "", "start": 1.2, "end": 1.3},
        ])
        words = extract_source_words(db.get_clip_by_id(clip_id))
        self.assertEqual([w["word"] for w in words], ["alpha", "beta", "gamma"])
        self.assertFalse(any(w["word"] == "" for w in words))

    def test_transcript_segments_are_the_fallback(self):
        chunk_id = f"chunk_{uuid.uuid4().hex[:8]}"
        with db.get_db_connection() as conn:
            conn.execute(
                "INSERT INTO chunks (id, filename, filepath) VALUES (?, ?, ?)",
                (chunk_id, f"{chunk_id}.mp4", f"storage/incoming/{chunk_id}.mp4"),
            )
            conn.commit()
        clip_id = _clip(chunk_id=chunk_id)
        with db.get_db_connection() as conn:
            conn.execute(
                "INSERT INTO transcripts (id, chunk_id, full_text, segments_json) VALUES (?, ?, ?, ?)",
                (
                    f"tx_{uuid.uuid4().hex[:8]}", chunk_id, "Hi there",
                    json.dumps([{"start": 1.0, "end": 2.0, "text": "Hi", "words": [
                        {"w": "Hi", "start": 1.0, "end": 1.4},
                        {"text": "there", "start": 1.4, "end": 2.0},
                    ]}]),
                ),
            )
            conn.commit()
        words = extract_source_words(db.get_clip_by_id(clip_id))
        self.assertEqual([w["word"] for w in words], ["Hi", "there"])
        self.assertEqual(words[0]["start"], 1.0)

    def test_clip_relative_group_words_are_converted_to_source_time(self):
        clip_id = _clip()
        with db.get_db_connection() as conn:
            conn.execute(
                "UPDATE clips SET caption_data = ?, caption_range_start = ?, caption_coordinate_system = 'clip_relative' WHERE id = ?",
                (json.dumps({"groups": [
                    {"start": 0.5, "end": 0.9, "words": [{"text": "Hi", "start": 0.5, "end": 0.9}]},
                    {"start": 1.0, "end": 1.4, "words": [{"text": "there", "start": 1.0, "end": 1.4}]},
                ]}), 10.0, clip_id),
            )
            conn.commit()
        words = extract_source_words(db.get_clip_by_id(clip_id))
        self.assertEqual([w["word"] for w in words], ["Hi", "there"])
        self.assertAlmostEqual(words[0]["start"], 10.5, places=3)
        self.assertAlmostEqual(words[1]["end"], 11.4, places=3)

    def test_no_words_returns_empty_without_inventing(self):
        clip_id = _clip()
        self.assertEqual(extract_source_words(db.get_clip_by_id(clip_id)), [])


class TestCaptionRevisionFencing(unittest.TestCase):
    def test_stale_caption_save_is_refused(self):
        clip_id = _clip()
        self.assertTrue(db.save_clip_caption_data(
            clip_id, json.dumps({"groups": []}),
            expected_range_revision=0, source_range_start=0.0, source_range_end=10.0,
            expected_captions_revision=0,
        ))
        self.assertFalse(db.save_clip_caption_data(
            clip_id, json.dumps({"groups": []}),
            expected_range_revision=0, source_range_start=0.0, source_range_end=10.0,
            expected_captions_revision=0,
        ), "second stale save must conflict")
        self.assertTrue(db.save_clip_caption_data(
            clip_id, json.dumps({"groups": []}),
            expected_range_revision=0, source_range_start=0.0, source_range_end=10.0,
            expected_captions_revision=1,
        ))
        self.assertEqual(int(db.get_clip_by_id(clip_id)["captions_revision"]), 2)


class TestProcessingBackoff(unittest.TestCase):
    def test_processing_deadline_moves_job_to_reconciliation(self):
        from dispatch.publisher import outbox as outbox_module

        clip_id = db.save_clip(
            session_id=None, chunk_id=None, start_time=0.0, end_time=10.0,
            title="Processing", hook="hook", description="", hashtags="",
            virality_score=50, layout_mode="fit_blur",
        )
        job_id = "job_processing_deadline"
        with db.get_db_connection() as conn:
            conn.execute(
                """INSERT INTO publishing_outbox
                   (id, clip_id, platform, status, idempotency_key, processing_started_at)
                   VALUES (?, ?, 'youtube', 'processing', ?, datetime('now', '-3 hours'))""",
                (job_id, clip_id, job_id),
            )
            conn.commit()
        self.assertTrue(outbox_module._processing_deadline_exceeded({"id": job_id}))
        with db.get_db_connection() as conn:
            row = conn.execute("SELECT status FROM publishing_outbox WHERE id = ?", (job_id,)).fetchone()
        self.assertEqual(row["status"], "reconciliation_required")

    def test_schedule_processing_check_sets_future_retry(self):
        from dispatch.publisher import outbox as outbox_module

        clip_id = db.save_clip(
            session_id=None, chunk_id=None, start_time=0.0, end_time=10.0,
            title="Processing 2", hook="hook", description="", hashtags="",
            virality_score=50, layout_mode="fit_blur",
        )
        job_id = "job_processing_backoff"
        with db.get_db_connection() as conn:
            conn.execute(
                """INSERT INTO publishing_outbox
                   (id, clip_id, platform, status, idempotency_key, processing_started_at)
                   VALUES (?, ?, 'youtube', 'processing', ?, CURRENT_TIMESTAMP)""",
                (job_id, clip_id, job_id),
            )
            conn.commit()
        outbox_module._schedule_processing_check(job_id, 0)
        with db.get_db_connection() as conn:
            row = conn.execute(
                "SELECT processing_attempts, (julianday(next_retry_at)-julianday('now'))*86400 AS seconds FROM publishing_outbox WHERE id = ?",
                (job_id,),
            ).fetchone()
        self.assertEqual(row["processing_attempts"], 1)
        self.assertGreater(row["seconds"], 0.0)


class TestTranscribeQuotaNotBurned(unittest.TestCase):
    def test_missing_source_does_not_consume_a_daily_slot(self):
        from dispatch.transcription import gemini_engine

        chunk_id = f"chunk_missing_{uuid.uuid4().hex[:8]}"
        with db.get_db_connection() as conn:
            conn.execute(
                "INSERT INTO chunks (id, filename, filepath) VALUES (?, ?, ?)",
                (chunk_id, f"{chunk_id}.mp4", f"storage/incoming/{chunk_id}_does_not_exist.mp4"),
            )
            conn.commit()
        clip_id = db.save_clip(
            session_id=None, chunk_id=chunk_id, start_time=0.0, end_time=10.0,
            title="Queued Missing Source", hook="hook", description="", hashtags="",
            virality_score=50, layout_mode="fit_blur",
        )
        with db.get_db_connection() as conn:
            conn.execute("UPDATE clips SET status = 'queued_transcription' WHERE id = ?", (clip_id,))
            conn.commit()

        def quota_count():
            with db.get_db_connection() as conn:
                row = conn.execute("SELECT value FROM settings WHERE key = 'transcribe_quota_count'").fetchone()
                return int(row["value"]) if row else 0

        before = quota_count()
        gemini_engine.drain_queued_transcriptions(max_batch=1)
        self.assertEqual(quota_count(), before, "a missing source must not spend a Gemini slot")


class TestBackfillDryRun(unittest.TestCase):
    def test_dry_run_reports_would_push_and_unrecoverable(self):
        with_words = _clip(with_words=[{"word": "hello", "start": 0.0, "end": 0.5}])
        no_words = _clip()

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = backfill.main(["--dry-run", "--clip-id", with_words])
        self.assertEqual(code, 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report["results"][0]["action"], "would_push_transcript")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = backfill.main(["--dry-run", "--clip-id", no_words])
        self.assertEqual(code, 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report["results"][0]["action"], "unrecoverable_no_local_transcript")


if __name__ == "__main__":
    unittest.main()
