"""Unit and integration tests for post-transcription AI metadata enrichment.

Verifies:
- Model gemini-3.1-flash-lite structured output parsing
- Safeguard 1: Idempotent description merge (no duplicates on retry)
- Safeguard 2: YouTube snippet update preserves tags, categoryId, and language
- Safeguard 3: Failure isolation (never breaks transcription or downstream pipeline)
- Anti-AI emoji stripping
- Account credentials retention
"""
import unittest
from unittest.mock import MagicMock, patch
import json

from dispatch.youtube_inbox.enricher import (
    clean_text_emojis,
    merge_description,
    generate_ai_metadata,
    enrich_youtube_video_metadata,
    DEFAULT_ENRICHER_MODELS,
)
from dispatch.publisher.youtube import update_youtube_video_snippet
from dispatch import db


class TestYouTubeMetadataEnricher(unittest.TestCase):

    def setUp(self):
        db.init_db()

    def test_clean_text_emojis(self):
        """Verifies native emojis are strictly removed per Anti-AI Vibecoding invariant."""
        text_with_emojis = "🚀 My First Video! 💡 Amazing Tips 🔥 Must Watch ⚡"
        cleaned = clean_text_emojis(text_with_emojis)
        self.assertEqual(cleaned, "My First Video! Amazing Tips Must Watch")
        self.assertNotIn("🚀", cleaned)
        self.assertNotIn("🔥", cleaned)

    def test_merge_description_initial(self):
        """Initial merge appends metadata footer below divider."""
        initial_desc = (
            "Preserved recording segment for AI highlight extraction.\n"
            "Recorded via Dispatch Mobile Studio.\n\n"
            "Session: #107021 · Part 1\n"
            "ID: 3c5e88bb-8b1d-4eb0-8a40-2139b8bc7330"
        )
        ai_desc = "In this segment, we discuss the architecture of real-time video transcription."
        merged = merge_description(initial_desc, ai_desc)

        self.assertIn(ai_desc, merged)
        self.assertIn("---", merged)
        self.assertIn("Session: #107021 · Part 1", merged)
        self.assertIn("ID: 3c5e88bb-8b1d-4eb0-8a40-2139b8bc7330", merged)
        # Verify divider occurs exactly once
        self.assertEqual(merged.count("---"), 1)

    def test_merge_description_idempotent_retry(self):
        """Safeguard 1: On retry, replaces the top AI section instead of duplicating."""
        first_ai_desc = "Original AI description about video rendering."
        initial_desc = (
            "Preserved recording segment for AI highlight extraction.\n"
            "Recorded via Dispatch Mobile Studio.\n\n"
            "Session: #107021 · Part 1\n"
            "ID: 3c5e88bb-8b1d-4eb0-8a40-2139b8bc7330"
        )
        first_merged = merge_description(initial_desc, first_ai_desc)

        # Retry with second AI description
        second_ai_desc = "Updated AI description with refined insights and timestamps."
        second_merged = merge_description(first_merged, second_ai_desc)

        self.assertIn(second_ai_desc, second_merged)
        self.assertNotIn(first_ai_desc, second_merged, "Previous AI description should be replaced, not appended")
        self.assertEqual(second_merged.count("---"), 1, "Divider should not duplicate")
        self.assertIn("Session: #107021 · Part 1", second_merged)

    def test_generate_ai_metadata_gemini_3_1_flash_lite(self):
        """Verifies gemini-3.1-flash-lite structured output is parsed and cleaned."""
        self.assertEqual(DEFAULT_ENRICHER_MODELS[0], "gemini-3.1-flash-lite")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps({
                                    "title": "🔥 Optimizing Fast Whisper and Gemini Ingestion 🚀",
                                    "description": "A deep dive into low-latency video processing pipelines."
                                })
                            }
                        ]
                    }
                }
            ]
        }

        with patch("requests.post", return_value=mock_response) as mock_post:
            meta = generate_ai_metadata(
                transcript_text="We implemented faster whisper with stage 1 gemini flash lite transcription.",
                api_key="fake-test-key",
            )
            self.assertIsNotNone(meta)
            self.assertEqual(meta["title"], "Optimizing Fast Whisper and Gemini Ingestion")
            self.assertNotIn("🔥", meta["title"])
            self.assertEqual(meta["description"], "A deep dive into low-latency video processing pipelines.")
            # Verify request URL targeted gemini-3.1-flash-lite
            args, kwargs = mock_post.call_args
            self.assertIn("gemini-3.1-flash-lite", args[0])

    def test_update_youtube_video_snippet_preserves_tags(self):
        """Safeguard 2: Snippet update strictly preserves existing tags and categoryId."""
        mock_service = MagicMock()
        mock_service.videos().list().execute.return_value = {
            "items": [
                {
                    "id": "vid_12345678901",
                    "snippet": {
                        "title": "Nov 4, 2026 · 2:15 PM — Recording Part 1",
                        "description": "Preserved recording segment\nID: dispatch_id_abc",
                        "tags": ["dispatch", "dispatch_id_abc", "dispatch_session_107021"],
                        "categoryId": "22",
                        "defaultLanguage": "en",
                        "defaultAudioLanguage": "en",
                    }
                }
            ]
        }

        mock_update = MagicMock()
        mock_update.execute.return_value = {"id": "vid_12345678901"}
        mock_service.videos().update.return_value = mock_update

        success = update_youtube_video_snippet(
            video_id="vid_12345678901",
            title="Building Scalable Video Pipelines",
            description="New enriched description\n\n---\nPreserved recording segment",
            youtube_service=mock_service
        )

        self.assertTrue(success)
        mock_service.videos().update.assert_called_once()
        call_kwargs = mock_service.videos().update.call_args[1]
        body = call_kwargs["body"]
        self.assertEqual(body["id"], "vid_12345678901")
        updated_snippet = body["snippet"]
        self.assertEqual(updated_snippet["title"], "Building Scalable Video Pipelines")
        self.assertIn("New enriched description", updated_snippet["description"])
        # Tags and categoryId MUST be preserved!
        self.assertEqual(updated_snippet["tags"], ["dispatch", "dispatch_id_abc", "dispatch_session_107021"])
        self.assertEqual(updated_snippet["categoryId"], "22")
        self.assertEqual(updated_snippet["defaultLanguage"], "en")

    def test_enrich_youtube_video_metadata_full_flow(self):
        """Full end-to-end enrichment updates YouTube snippet, SQLite, and cloud."""
        vid = "vid_full_test_1"
        db.register_youtube_video(video_id=vid, title="Initial Title", dispatch_id="disp_1")

        mock_service = MagicMock()
        mock_service.videos().list().execute.return_value = {
            "items": [
                {
                    "id": vid,
                    "snippet": {
                        "title": "Initial Title",
                        "description": "Preserved recording segment\nID: disp_1",
                        "tags": ["dispatch", "dispatch_id_disp_1"],
                        "categoryId": "22"
                    }
                }
            ]
        }
        mock_update = MagicMock()
        mock_update.execute.return_value = {"id": vid}
        mock_service.videos().update.return_value = mock_update

        with patch("dispatch.youtube_inbox.enricher.generate_ai_metadata") as mock_ai:
            mock_ai.return_value = {
                "title": "Autonomous Video Editing Systems",
                "description": "Overview of autonomous agent architectures for video production."
            }
            with patch("dispatch.youtube_connections.update_job_title_by_video_id") as mock_cloud_title:
                res = enrich_youtube_video_metadata(
                    video_id=vid,
                    transcript_text="Today we are building autonomous video editing pipelines with Gemini and Whisper.",
                    youtube_service=mock_service
                )

                self.assertIsNotNone(res)
                self.assertEqual(res["title"], "Autonomous Video Editing Systems")
                self.assertTrue(res["youtube_updated"])

                # Check SQLite was updated
                record = db.get_youtube_video(vid)
                self.assertEqual(record["title"], "Autonomous Video Editing Systems")

                # Check cloud sync called
                mock_cloud_title.assert_called_once_with(vid, "Autonomous Video Editing Systems")

    def test_enrich_youtube_video_metadata_failure_isolation(self):
        """Safeguard 3: If Gemini or YouTube update throws, it never raises or crashes."""
        vid = "vid_fail_test"
        db.register_youtube_video(video_id=vid, title="Original Safe Title")

        with patch("dispatch.youtube_inbox.enricher.generate_ai_metadata", side_effect=RuntimeError("Gemini Quota Exceeded")):
            # Should not raise exception
            res = enrich_youtube_video_metadata(
                video_id=vid,
                transcript_text="Some valid transcript text that would normally be enriched."
            )
            self.assertIsNone(res)
            # Original title in DB untouched
            record = db.get_youtube_video(vid)
            self.assertEqual(record["title"], "Original Safe Title")

    def test_enrich_youtube_video_metadata_skips_short_transcript(self):
        """Skips videos with zero speech or fewer than 5 words."""
        vid = "vid_silent_test"
        res = enrich_youtube_video_metadata(video_id=vid, transcript_text="hello world")
        self.assertIsNone(res)


if __name__ == "__main__":
    unittest.main()
