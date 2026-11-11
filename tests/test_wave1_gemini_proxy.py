"""Wave 1 Slice 3 tests: Cloud Worker Gemini proxy routing and fail-closed tenant boundaries."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

from dispatch import db
from dispatch.ai_clips.highlight_finder import (
    call_gemini_generate_content,
    extract_highlights_windowed,
    identify_and_save_highlights,
)
from dispatch.ai_clips.worker import HighlightWorker
from dispatch.transcription import gemini_engine
from dispatch.transcription.gemini_engine import (
    PermanentAuthError,
    drain_queued_transcriptions,
    make_worker_gemini_proxy_key,
    transcribe_media,
    transcribe_stage1_flash_lite,
)
from dispatch.transcription.worker import TranscriptionWorker
from dispatch.youtube_connections import VaultUnavailable


def _fake_genai_module() -> ModuleType:
    module = ModuleType("google.genai")
    module.types = SimpleNamespace(
        GenerateContentConfig=lambda **kwargs: kwargs,
        AudioTranscriptionConfig=lambda **kwargs: kwargs,
    )
    return module


class TestWave1GeminiProxy(unittest.TestCase):
    """Verify Worker Gemini proxy routing, fail-closed guarantees, and key-mock compatibility."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="w1_gemini_proxy_")
        self.tmp_path = Path(self.temp_dir.name)
        self.proc_dir = self.tmp_path / "processing"
        self.proc_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmp_path / "w1_proxy_test.db"

        self._orig_db_path = db.DB_PATH
        self._orig_env_gemini = os.environ.get("GEMINI_API_KEY")
        os.environ["GEMINI_API_KEY"] = "host_pc_secret_must_never_leak"

        db.DB_PATH = self.db_path
        db.init_db()
        db.set_setting("transcribe_quota_date", "2099-01-01")
        db.set_setting("transcribe_quota_count", "0")

    def tearDown(self):
        if hasattr(db, "close_db_connections"):
            db.close_db_connections(self.db_path)
        db.DB_PATH = self._orig_db_path
        if self._orig_env_gemini is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = self._orig_env_gemini
        self.temp_dir.cleanup()

    def _seed_downloaded_video(self, video_id: str, user_id: str | None) -> Path:
        vid_path = self.tmp_path / f"{video_id}.mp4"
        vid_path.write_bytes(b"synthetic-mp4-data")
        db.register_youtube_video(
            video_id=video_id,
            title=f"Title {video_id}",
            duration=40.0,
            user_id=user_id,
        )
        db.update_youtube_video(
            video_id=video_id,
            status="DOWNLOADED",
            local_video_path=str(vid_path),
        )
        return vid_path

    def _seed_transcribed_video(
        self,
        video_id: str,
        user_id: str | None,
        duration: float = 120.0,
    ) -> tuple[str, Path, list[dict]]:
        src_file = self.tmp_path / f"{video_id}.mp4"
        src_file.write_bytes(b"synthetic-mp4-data")
        segments = [
            {
                "id": 0,
                "start": 10.0,
                "end": 45.0,
                "text": "First highlight segment about shipping safe proxies.",
                "words": [
                    {"word": "First", "start": 10.0, "end": 10.4},
                    {"word": "proxies.", "start": 44.3, "end": 45.0},
                ],
            },
            {
                "id": 1,
                "start": 50.0,
                "end": 95.0,
                "text": "Second segment covering fail closed boundaries.",
                "words": [
                    {"word": "Second", "start": 50.0, "end": 50.5},
                    {"word": "boundaries.", "start": 94.2, "end": 95.0},
                ],
            },
        ]
        if duration > 720.0:
            segments.append(
                {
                    "id": 2,
                    "start": 740.0,
                    "end": 800.0,
                    "text": "Third segment in second window after twelve minutes.",
                    "words": [
                        {"word": "Third", "start": 740.0, "end": 740.5},
                        {"word": "minutes.", "start": 799.2, "end": 800.0},
                    ],
                }
            )
        chunk_id = db.register_chunk(
            session_id=None,
            filename=src_file.name,
            filepath=str(src_file),
            file_hash=f"yt_{video_id}",
            user_id=user_id,
        )
        db.update_chunk_metadata(
            chunk_id, duration=duration, width=1920, height=1080, aspect_ratio="16:9", status="transcribed"
        )
        db.save_transcript(chunk_id=chunk_id, session_id=None, full_text="Transcript", segments=segments)
        db.register_youtube_video(video_id=video_id, title=f"Vid {video_id}", duration=duration, user_id=user_id)
        db.update_youtube_video(
            video_id=video_id,
            status="TRANSCRIBED",
            local_video_path=str(src_file),
            transcript_source="gemini_transcribe",
            segments_json=json.dumps(segments),
            duration=duration,
            user_id=user_id,
        )
        return chunk_id, src_file, segments

    # -------------------------------------------------------------------------
    # (a) User-owned transcription via Cloud Worker Gemini proxy
    # -------------------------------------------------------------------------

    def test_transcription_worker_routes_via_worker_proxy(self):
        video_id = "w1_tx_proxy_1"
        vid_path = self._seed_downloaded_video(video_id, user_id="user_proxy_tx")
        part_mp3 = self.tmp_path / "part0.mp3"
        part_mp3.write_bytes(b"mp3-bytes")

        worker_generate_resp = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "audioTranscription": {
                                    "speakerLabel": "spk:0",
                                    "words": [
                                        {"word": "Proxy", "startOffset": "0.100s", "endOffset": "0.400s"},
                                        {"word": "works", "startOffset": "0.500s", "endOffset": "0.900s"},
                                    ],
                                }
                            }
                        ]
                    }
                }
            ]
        }

        with patch.dict(sys.modules, {"google.genai": _fake_genai_module()}), \
             patch("dispatch.transcription.gemini_engine.PROCESSING_DIR", self.proc_dir), \
             patch("dispatch.youtube_connections.get_user_gemini_key", return_value="") as mock_get_key, \
             patch("dispatch.youtube_connections.has_user_gemini_proxy", return_value=True) as mock_has_proxy, \
             patch(
                 "dispatch.youtube_connections.upload_user_gemini_media",
                 return_value={
                     "file": {
                         "name": "files/proxy_audio_1",
                         "uri": "https://generativelanguage.googleapis.com/v1beta/files/proxy_audio_1",
                         "mimeType": "audio/mp3",
                     }
                 },
             ) as mock_upload, \
             patch("dispatch.youtube_connections.call_user_gemini_proxy", return_value=worker_generate_resp) as mock_gen, \
             patch("dispatch.transcription.gemini_engine.extract_compressed_audio", return_value=part_mp3), \
             patch("dispatch.transcription.gemini_engine.split_audio_for_transcription", return_value=[(part_mp3, 0.0)]), \
             patch("dispatch.ingestion.validator.probe_video", return_value=(True, {"duration": 40.0, "width": 1080, "height": 1920, "aspect_ratio": "9:16"}, None)):
            res = TranscriptionWorker().process_pending()

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["status"], "TRANSCRIBED")
        self.assertEqual(res[0]["source"], "gemini_transcribe")
        mock_get_key.assert_called_once_with("user_proxy_tx")
        mock_has_proxy.assert_called_once_with("user_proxy_tx")
        mock_upload.assert_called_once()
        self.assertEqual(mock_upload.call_args.kwargs["user_id"], "user_proxy_tx")
        self.assertEqual(mock_upload.call_args.kwargs["mime_type"], "audio/mp3")

        mock_gen.assert_called_once()
        gen_kwargs = mock_gen.call_args.kwargs
        self.assertEqual(gen_kwargs["user_id"], "user_proxy_tx")
        self.assertEqual(gen_kwargs["model"], "gemini-3.5-transcribe")
        self.assertEqual(
            gen_kwargs["contents"][0]["parts"][0]["fileData"]["fileUri"],
            "https://generativelanguage.googleapis.com/v1beta/files/proxy_audio_1",
        )
        self.assertEqual(
            gen_kwargs["generation_config"]["audioTranscriptionConfig"],
            {"wordTimestamp": True, "diarization": True},
        )

        row = db.get_youtube_video(video_id)
        self.assertEqual(row["status"], "TRANSCRIBED")
        segs = json.loads(row["segments_json"])
        self.assertEqual(segs[0]["text"], "Proxy works")
        self.assertTrue(vid_path.exists())

    def test_transcription_proxy_primary_recoverable_falls_back_to_flash_lite_via_proxy(self):
        audio = self.tmp_path / "sample.mp3"
        audio.write_bytes(b"mp3-bytes")
        fallback_resp = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": '[{"start":"00:01.00","end":"00:03.00","text":"Fallback proxy ok","speaker":"spk:0"}]'}
                        ]
                    }
                }
            ]
        }

        with patch.dict(sys.modules, {"google.genai": _fake_genai_module()}), \
             patch("dispatch.transcription.gemini_engine.PROCESSING_DIR", self.proc_dir), \
             patch("dispatch.transcription.gemini_engine.split_audio_for_transcription", return_value=[(audio, 0.0)]), \
             patch(
                 "dispatch.youtube_connections.upload_user_gemini_media",
                 return_value={"name": "files/fb_proxy_1", "uri": "https://generativelanguage.googleapis.com/v1beta/files/fb_proxy_1"},
             ) as mock_upload, \
             patch(
                 "dispatch.youtube_connections.call_user_gemini_proxy",
                 side_effect=[VaultUnavailable("Worker Gemini generate proxy failed (503): overloaded"), fallback_resp],
             ) as mock_gen:
            res = transcribe_media(audio, api_key=make_worker_gemini_proxy_key("user_fb_proxy"))

        self.assertEqual(res["source"], "gemini_flash_lite")
        self.assertEqual(res["segments"][0]["text"], "Fallback proxy ok")
        self.assertEqual(mock_upload.call_count, 2)
        self.assertEqual(mock_gen.call_count, 2)
        self.assertEqual(mock_gen.call_args_list[0].kwargs["model"], "gemini-3.5-transcribe")
        self.assertEqual(mock_gen.call_args_list[1].kwargs["model"], "gemini-3.5-flash-lite")
        self.assertEqual(
            mock_gen.call_args_list[1].kwargs["generation_config"],
            {"temperature": 0.0, "responseMimeType": "application/json"},
        )
        # Quota slot reserved for primary must be refunded when primary fails and fallback succeeds
        self.assertEqual(db.get_setting("transcribe_quota_count"), "0")

    def test_transcription_proxy_permission_error_is_permanent_and_skips_fallback(self):
        audio = self.tmp_path / "perm.mp3"
        audio.write_bytes(b"mp3-bytes")
        with patch.dict(sys.modules, {"google.genai": _fake_genai_module()}), \
             patch("dispatch.transcription.gemini_engine.PROCESSING_DIR", self.proc_dir), \
             patch("dispatch.transcription.gemini_engine.split_audio_for_transcription", return_value=[(audio, 0.0)]), \
             patch(
                 "dispatch.youtube_connections.upload_user_gemini_media",
                 return_value={"file": {"name": "files/p_err", "uri": "https://generativelanguage.googleapis.com/v1beta/files/p_err"}},
             ), \
             patch(
                 "dispatch.youtube_connections.call_user_gemini_proxy",
                 side_effect=PermissionError("User Gemini API key is not configured in Dispatch Cloud Vault"),
             ) as mock_gen:
            with self.assertRaises(PermanentAuthError):
                transcribe_media(audio, api_key=make_worker_gemini_proxy_key("user_missing_vault"))

        self.assertEqual(mock_gen.call_count, 1)
        self.assertEqual(db.get_setting("transcribe_quota_count"), "0")

    def test_transcribe_stage1_flash_lite_via_worker_proxy(self):
        audio = self.tmp_path / "stage1.mp3"
        audio.write_bytes(b"mp3-bytes")
        resp = {
            "candidates": [
                {"content": {"parts": [{"text": '[{"start":"00:02.00","end":"00:05.00","text":"Stage1 proxy"}]'}]}}
            ]
        }
        with patch.dict(sys.modules, {"google.genai": _fake_genai_module()}), \
             patch("dispatch.transcription.gemini_engine.PROCESSING_DIR", self.proc_dir), \
             patch("dispatch.transcription.gemini_engine.split_audio_for_transcription", return_value=[(audio, 0.0)]), \
             patch(
                 "dispatch.youtube_connections.upload_user_gemini_media",
                 return_value={"file": {"name": "files/s1", "uri": "https://generativelanguage.googleapis.com/v1beta/files/s1"}},
             ) as mock_upload, \
             patch("dispatch.youtube_connections.call_user_gemini_proxy", return_value=resp) as mock_gen:
            segs = transcribe_stage1_flash_lite(audio, api_key=make_worker_gemini_proxy_key("user_s1"))

        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["text"], "Stage1 proxy")
        self.assertEqual(mock_upload.call_args.kwargs["user_id"], "user_s1")
        self.assertEqual(mock_gen.call_args.kwargs["user_id"], "user_s1")

    def test_drain_queued_transcriptions_routes_user_clip_via_worker_proxy(self):
        src = self.tmp_path / "drain_src.mp4"
        src.write_bytes(b"video-bytes")
        chunk_id = db.register_chunk(
            session_id=None, filename=src.name, filepath=str(src), file_hash="h_drain_proxy", user_id="user_drain_1"
        )
        clip_id = db.save_clip(
            session_id=None,
            chunk_id=chunk_id,
            start_time=10.0,
            end_time=40.0,
            title="Queued Clip",
            hook="Hook",
            description="",
            hashtags="#Shorts",
            virality_score=85,
            layout_mode="fit_blur",
            padded_start=6.0,
            padded_end=44.0,
            user_id="user_drain_1",
        )
        with db.get_db_connection() as conn:
            conn.execute("UPDATE clips SET status = 'queued_transcription' WHERE id = ?", (clip_id,))
            conn.commit()

        def fake_extract(source_path, start_seconds, end_seconds, output_path, attempts=2):
            output_path.write_bytes(b"slice-mp3")
            return output_path

        worker_stage2_resp = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "audioTranscription": {
                                    "speakerLabel": "spk:0",
                                    "words": [
                                        {"word": "Drain", "startOffset": "4.200s", "endOffset": "4.600s"},
                                        {"word": "proxy", "startOffset": "4.700s", "endOffset": "5.200s"},
                                        {"word": "done", "startOffset": "32.000s", "endOffset": "33.000s"},
                                    ],
                                }
                            }
                        ]
                    }
                }
            ]
        }

        with patch.dict(sys.modules, {"google.genai": _fake_genai_module()}), \
             patch("dispatch.transcription.gemini_engine.PROCESSING_DIR", self.proc_dir), \
             patch("dispatch.youtube_connections.get_user_gemini_key", return_value="") as mock_get_key, \
             patch("dispatch.youtube_connections.has_user_gemini_proxy", return_value=True) as mock_has_proxy, \
             patch("dispatch.transcription.gemini_engine._probe_audio_duration", return_value=38.0), \
             patch("dispatch.transcription.gemini_engine.extract_stage2_clip_audio", side_effect=fake_extract), \
             patch(
                 "dispatch.youtube_connections.upload_user_gemini_media",
                 return_value={"file": {"name": "files/drain1", "uri": "https://generativelanguage.googleapis.com/v1beta/files/drain1"}},
             ) as mock_upload, \
             patch("dispatch.youtube_connections.call_user_gemini_proxy", return_value=worker_stage2_resp) as mock_gen, \
             patch("dispatch.video_engine.renderer.render_clip") as mock_render:
            drained = drain_queued_transcriptions(max_batch=5)

        self.assertEqual(drained, 1)
        mock_get_key.assert_called_once_with("user_drain_1")
        mock_has_proxy.assert_called_once_with("user_drain_1")
        mock_upload.assert_called_once()
        self.assertEqual(mock_upload.call_args.kwargs["user_id"], "user_drain_1")
        mock_gen.assert_called_once()
        self.assertEqual(mock_gen.call_args.kwargs["user_id"], "user_drain_1")
        mock_render.assert_called_once()
        self.assertEqual(db.get_clip_by_id(clip_id)["status"], "ready_review")

    # -------------------------------------------------------------------------
    # (b) User-owned HighlightWorker + highlight_finder via Cloud Worker proxy
    # -------------------------------------------------------------------------

    def test_highlight_worker_short_video_routes_via_worker_proxy(self):
        video_id = "w1_hl_short"
        self._seed_transcribed_video(video_id, user_id="user_hl_1", duration=300.0)
        worker_resp = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"thought": True, "text": "internal chain of thought"},
                            {
                                "text": '```json\n[{"start_time": 10.0, "end_time": 45.0, "title": "Proxy Short Clip", "virality_score": 92}]\n```'
                            },
                        ]
                    }
                }
            ]
        }

        with patch("dispatch.youtube_connections.get_user_gemini_key", return_value="") as mock_get_key, \
             patch("dispatch.youtube_connections.has_user_gemini_proxy", return_value=True) as mock_has_proxy, \
             patch("dispatch.youtube_connections.call_user_gemini_proxy", return_value=worker_resp) as mock_gen, \
             patch("dispatch.ai_clips.highlight_finder.requests.post", side_effect=AssertionError("Must not call direct HTTP")):
            res = HighlightWorker().process_video_highlights(video_id)

        self.assertEqual(res["status"], "CLIPS_DEFINED")
        self.assertEqual(res["clips_count"], 1)
        mock_get_key.assert_called_once_with("user_hl_1")
        mock_has_proxy.assert_called_once_with("user_hl_1")
        mock_gen.assert_called_once()
        self.assertEqual(mock_gen.call_args.kwargs["user_id"], "user_hl_1")
        self.assertEqual(db.get_youtube_video(video_id)["status"], "CLIPS_DEFINED")

    def test_highlight_worker_windowed_long_video_routes_via_worker_proxy(self):
        video_id = "w1_hl_long"
        self._seed_transcribed_video(video_id, user_id="user_hl_long", duration=950.0)
        win1_resp = {
            "candidates": [
                {"content": {"parts": [{"text": '[{"start_time": 10.0, "end_time": 45.0, "title": "Window 1 Clip", "virality_score": 88}]'}]}}
            ]
        }
        win2_resp = {
            "candidates": [
                {"content": {"parts": [{"text": '[{"start_time": 740.0, "end_time": 790.0, "title": "Window 2 Clip", "virality_score": 90}]'}]}}
            ]
        }

        with patch("dispatch.youtube_connections.get_user_gemini_key", return_value=""), \
             patch("dispatch.youtube_connections.has_user_gemini_proxy", return_value=True), \
             patch("dispatch.youtube_connections.call_user_gemini_proxy", side_effect=[win1_resp, win2_resp]) as mock_gen, \
             patch("dispatch.ai_clips.highlight_finder.requests.post", side_effect=AssertionError("Must not call direct HTTP")):
            res = HighlightWorker().process_video_highlights(video_id)

        self.assertEqual(res["status"], "CLIPS_DEFINED")
        self.assertEqual(res["clips_count"], 2)
        self.assertEqual(mock_gen.call_count, 2)
        for c in mock_gen.call_args_list:
            self.assertEqual(c.kwargs["user_id"], "user_hl_long")

    def test_highlight_worker_clips_defined_render_routes_stage2_via_worker_proxy(self):
        video_id = "w1_render_proxy"
        chunk_id, src_file, _ = self._seed_transcribed_video(
            video_id, user_id="user_render_proxy", duration=120.0
        )
        db.save_clip_definitions(
            chunk_id=chunk_id,
            session_id=None,
            video_id=video_id,
            clip_defs=[{"start_time": 10.0, "end_time": 45.0, "title": "Render Proxy Clip", "virality_score": 87}],
            user_id="user_render_proxy",
        )
        db.update_youtube_video(video_id=video_id, status="CLIPS_DEFINED")

        def fake_extract(source_path, start_seconds, end_seconds, output_path, attempts=2):
            output_path.write_bytes(b"mp3-bytes")
            return output_path

        def fake_slice(source_video, clip_id, start_time, end_time, expected_range_revision=None):
            out_mp4 = self.tmp_path / f"{clip_id}.mp4"
            out_mp4.write_bytes(b"rendered-mp4")
            db.update_clip_media(clip_id, str(out_mp4), None, expected_range_revision=expected_range_revision)
            return {"clip_id": clip_id, "video_path": out_mp4}

        stage2_resp = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "audioTranscription": {
                                    "speakerLabel": "spk:0",
                                    "words": [
                                        {"word": "First", "startOffset": "4.000s", "endOffset": "4.400s"},
                                        {"word": "proxies.", "startOffset": "38.200s", "endOffset": "39.000s"},
                                    ],
                                }
                            }
                        ]
                    }
                }
            ]
        }

        with patch.dict(sys.modules, {"google.genai": _fake_genai_module()}), \
             patch("dispatch.ai_clips.worker.PROCESSING_DIR", self.proc_dir), \
             patch("dispatch.transcription.gemini_engine.PROCESSING_DIR", self.proc_dir), \
             patch("dispatch.youtube_connections.get_user_gemini_key", return_value=""), \
             patch("dispatch.youtube_connections.has_user_gemini_proxy", return_value=True), \
             patch("dispatch.transcription.gemini_engine._probe_audio_duration", return_value=43.0), \
             patch("dispatch.transcription.gemini_engine.extract_stage2_clip_audio", side_effect=fake_extract), \
             patch(
                 "dispatch.youtube_connections.upload_user_gemini_media",
                 return_value={"file": {"name": "files/r_p1", "uri": "https://generativelanguage.googleapis.com/v1beta/files/r_p1"}},
             ) as mock_upload, \
             patch("dispatch.youtube_connections.call_user_gemini_proxy", return_value=stage2_resp) as mock_gen, \
             patch("dispatch.video_engine.renderer.slice_clean_clip", side_effect=fake_slice):
            HighlightWorker().process_pending()

        mock_upload.assert_called_once()
        self.assertEqual(mock_upload.call_args.kwargs["user_id"], "user_render_proxy")
        mock_gen.assert_called_once()
        self.assertEqual(mock_gen.call_args.kwargs["user_id"], "user_render_proxy")
        self.assertEqual(db.get_youtube_video(video_id)["status"], "COMPLETED")

    def test_identify_and_save_highlights_routes_via_worker_proxy(self):
        src = self.tmp_path / "hf_src.mp4"
        src.write_bytes(b"video-bytes")
        chunk_id = db.register_chunk(
            session_id=None, filename=src.name, filepath=str(src), file_hash="h_hf_proxy", user_id="user_hf_1"
        )
        segments = [
            {"id": 0, "start": 5.0, "end": 40.0, "text": "Shipping Worker proxy highlight extraction safely."},
        ]
        proxy_resp = {
            "candidates": [
                {"content": {"parts": [{"text": '[{"start_time": 5.0, "end_time": 40.0, "title": "Safe Proxy Clip", "virality_score": 89}]'}]}}
            ]
        }

        with patch("dispatch.youtube_connections.get_user_gemini_key", return_value=""), \
             patch("dispatch.youtube_connections.has_user_gemini_proxy", return_value=True), \
             patch("dispatch.youtube_connections.call_user_gemini_proxy", return_value=proxy_resp) as mock_gen, \
             patch("dispatch.ai_clips.highlight_finder.requests.post", side_effect=AssertionError("Must not call direct HTTP")):
            saved_ids = identify_and_save_highlights(
                chunk_id=chunk_id,
                session_id=None,
                segments=segments,
                total_duration=60.0,
                user_id="user_hf_1",
            )

        self.assertEqual(len(saved_ids), 1)
        mock_gen.assert_called_once()
        self.assertEqual(mock_gen.call_args.kwargs["user_id"], "user_hf_1")
        clip_row = db.get_clip_by_id(saved_ids[0])
        self.assertEqual(clip_row["extraction_source"], "gemini")

    def test_highlight_proxy_auth_error_is_permanent_and_rate_limit_retries(self):
        vid_auth = "w1_hl_perm_auth"
        self._seed_transcribed_video(vid_auth, user_id="user_auth_err", duration=120.0)
        with patch("dispatch.youtube_connections.get_user_gemini_key", return_value=""), \
             patch("dispatch.youtube_connections.has_user_gemini_proxy", return_value=True), \
             patch(
                 "dispatch.youtube_connections.call_user_gemini_proxy",
                 side_effect=VaultUnavailable("Worker Gemini generate proxy failed (401): API_KEY_INVALID"),
             ) as mock_gen:
            with self.assertRaises(RuntimeError):
                HighlightWorker(max_attempts=5).process_video_highlights(vid_auth)
        # Permanent auth error aborts immediately on first model instead of retrying candidate models.
        self.assertEqual(mock_gen.call_count, 1)
        self.assertEqual(db.get_youtube_video(vid_auth)["status"], "FAILED")

        vid_429 = "w1_hl_429"
        self._seed_transcribed_video(vid_429, user_id="user_429", duration=120.0)
        with patch("dispatch.youtube_connections.get_user_gemini_key", return_value=""), \
             patch("dispatch.youtube_connections.has_user_gemini_proxy", return_value=True), \
             patch(
                 "dispatch.youtube_connections.call_user_gemini_proxy",
                 side_effect=VaultUnavailable("Worker Gemini generate proxy failed (429): quota exceeded"),
             ):
            with self.assertRaises(RuntimeError):
                HighlightWorker(max_attempts=5).process_video_highlights(vid_429)
        row_429 = db.get_youtube_video(vid_429)
        self.assertEqual(row_429["status"], "TRANSCRIBED")
        self.assertEqual(row_429["ai_attempt_count"], 1)
        self.assertTrue(row_429["next_ai_attempt_at"])

    # -------------------------------------------------------------------------
    # (c) Fail-closed boundaries when has_user_gemini_proxy == False
    # -------------------------------------------------------------------------

    def test_user_owned_jobs_fail_closed_with_zero_host_key_fallback(self):
        # 1. TranscriptionWorker
        vid_tx = "w1_no_proxy_tx"
        self._seed_downloaded_video(vid_tx, user_id="user_no_proxy")
        with patch("dispatch.youtube_connections.get_user_gemini_key", return_value=""), \
             patch("dispatch.youtube_connections.has_user_gemini_proxy", return_value=False), \
             patch("dispatch.transcription.gemini_engine.transcribe_media", side_effect=AssertionError("Must not call transcribe_media")):
            res = TranscriptionWorker().process_downloaded_item(db.get_youtube_video(vid_tx))
        self.assertIsNone(res)
        self.assertEqual(db.get_youtube_video(vid_tx)["status"], "FAILED")
        self.assertIn("Refusing fallback to PC host GEMINI_API_KEY", db.get_youtube_video(vid_tx)["last_error"])

        # 2. drain_queued_transcriptions (was previously falling back to host GEMINI_API_KEY!)
        src = self.tmp_path / "drain_fail_closed.mp4"
        src.write_bytes(b"video-bytes")
        chk_id = db.register_chunk(
            session_id=None, filename=src.name, filepath=str(src), file_hash="h_drain_fc", user_id="user_no_proxy"
        )
        clip_id = db.save_clip(
            session_id=None, chunk_id=chk_id, start_time=5.0, end_time=25.0,
            title="Fail Closed Drain", hook="h", description="", hashtags="", virality_score=80,
            layout_mode="fit_blur", padded_start=1.0, padded_end=29.0, user_id="user_no_proxy",
        )
        with db.get_db_connection() as conn:
            conn.execute("UPDATE clips SET status = 'queued_transcription' WHERE id = ?", (clip_id,))
            conn.commit()
        with patch("dispatch.youtube_connections.get_user_gemini_key", return_value=""), \
             patch("dispatch.youtube_connections.has_user_gemini_proxy", return_value=False), \
             patch("dispatch.transcription.gemini_engine.transcribe_stage2_precision_words", side_effect=AssertionError("Must not call Stage 2")):
            drained = drain_queued_transcriptions(max_batch=5)
        self.assertEqual(drained, 0)
        self.assertEqual(db.get_clip_by_id(clip_id)["status"], "queued_transcription")
        self.assertEqual(db.get_setting("transcribe_quota_count"), "0")

        # 3. HighlightWorker
        vid_hl = "w1_no_proxy_hl"
        self._seed_transcribed_video(vid_hl, user_id="user_no_proxy", duration=120.0)
        with patch("dispatch.youtube_connections.get_user_gemini_key", return_value=""), \
             patch("dispatch.youtube_connections.has_user_gemini_proxy", return_value=False), \
             patch("dispatch.ai_clips.highlight_finder.requests.post", side_effect=AssertionError("Must not call direct HTTP")), \
             patch("dispatch.youtube_connections.call_user_gemini_proxy", side_effect=AssertionError("Must not call proxy")):
            with self.assertRaises(RuntimeError) as ctx:
                HighlightWorker().process_video_highlights(vid_hl)
        self.assertIn("Refusing fallback to PC host GEMINI_API_KEY", str(ctx.exception))
        self.assertEqual(db.get_youtube_video(vid_hl)["status"], "FAILED")

        # 4. identify_and_save_highlights
        with patch("dispatch.youtube_connections.get_user_gemini_key", return_value=""), \
             patch("dispatch.youtube_connections.has_user_gemini_proxy", return_value=False), \
             patch("dispatch.ai_clips.highlight_finder.requests.post", side_effect=AssertionError("Must not call direct HTTP")):
            with self.assertRaises(RuntimeError) as ctx2:
                identify_and_save_highlights(
                    chunk_id=chk_id,
                    session_id=None,
                    segments=[{"id": 0, "start": 0.0, "end": 20.0, "text": "No fallback"}],
                    total_duration=30.0,
                    user_id="user_no_proxy",
                )
        self.assertIn("Refusing fallback to PC host GEMINI_API_KEY", str(ctx2.exception))

        # 5. Malformed sentinel without user_id must fail closed
        with self.assertRaises(PermanentAuthError):
            gemini_engine.get_gemini_client(api_key="__worker_gemini_proxy__:")
        with self.assertRaises(RuntimeError):
            call_gemini_generate_content("prompt", api_key="__worker_gemini_proxy__:")

    # -------------------------------------------------------------------------
    # (d) Mocked get_user_gemini_key compatibility
    # -------------------------------------------------------------------------

    def test_mocked_get_user_gemini_key_continues_to_work_unchanged(self):
        with patch("dispatch.youtube_connections.get_user_gemini_key", return_value="user-key") as mock_get_key, \
             patch("dispatch.youtube_connections.has_user_gemini_proxy", side_effect=AssertionError("Should not check proxy when raw key is returned")):
            # TranscriptionWorker
            key = TranscriptionWorker()._resolve_api_key_for_item({"video_id": "v1", "user_id": "user_mock_1"})
            self.assertEqual(key, "user-key")

            # HighlightWorker
            vid_hl = "w1_mock_key_hl"
            self._seed_transcribed_video(vid_hl, user_id="user_mock_1", duration=120.0)
            with patch("dispatch.ai_clips.worker.call_gemini_generate_content", return_value=([], "gemini-flash-latest")) as mock_hl:
                HighlightWorker().process_video_highlights(vid_hl)
            self.assertEqual(mock_hl.call_args.kwargs["api_key"], "user-key")

            # identify_and_save_highlights
            with patch("dispatch.ai_clips.highlight_finder.extract_highlights_windowed", return_value=([], "gemini-flash-latest")) as mock_win:
                identify_and_save_highlights(
                    chunk_id=None,
                    session_id=None,
                    segments=[{"id": 0, "start": 0.0, "end": 30.0, "text": "Mock key test"}],
                    total_duration=40.0,
                    user_id="user_mock_1",
                )
            self.assertEqual(mock_win.call_args.kwargs["api_key"], "user-key")

            # drain_queued_transcriptions
            src = self.tmp_path / "drain_mock_key.mp4"
            src.write_bytes(b"video-bytes")
            chk_id = db.register_chunk(
                session_id=None, filename=src.name, filepath=str(src), file_hash="h_drain_mock", user_id="user_mock_1"
            )
            clip_id = db.save_clip(
                session_id=None, chunk_id=chk_id, start_time=5.0, end_time=25.0,
                title="Mock Key Drain", hook="h", description="", hashtags="", virality_score=80,
                layout_mode="fit_blur", padded_start=1.0, padded_end=29.0, user_id="user_mock_1",
            )
            with db.get_db_connection() as conn:
                conn.execute("UPDATE clips SET status = 'queued_transcription' WHERE id = ?", (clip_id,))
                conn.commit()
            with patch("dispatch.transcription.gemini_engine.extract_stage2_clip_audio", side_effect=lambda s, st, en, out: out.write_bytes(b"mp3") or out), \
                 patch("dispatch.transcription.gemini_engine.transcribe_stage2_precision_words", return_value={"words": []}) as mock_s2, \
                 patch("dispatch.video_engine.renderer.render_clip"):
                drained = drain_queued_transcriptions(max_batch=1)
            self.assertEqual(drained, 1)
            self.assertEqual(mock_s2.call_args.kwargs["api_key"], "user-key")

        self.assertGreaterEqual(mock_get_key.call_count, 4)

    def test_real_google_genai_types_and_proxy_devanagari_transliteration(self):
        """Verify proxy works with real google.genai.types (Pydantic) and routes transliteration via Worker."""
        audio = self.tmp_path / "real_types.mp3"
        audio.write_bytes(b"mp3-bytes")
        primary_resp = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "audioTranscription": {
                                    "speakerLabel": "spk:0",
                                    "words": [
                                        {"word": "नमस्ते", "startOffset": "0.100s", "endOffset": "0.500s"},
                                        {"word": "Dispatch", "startOffset": "0.600s", "endOffset": "1.000s"},
                                    ],
                                }
                            }
                        ]
                    }
                }
            ]
        }
        translit_resp = {
            "candidates": [
                {"content": {"parts": [{"text": '["Namaste", "Dispatch"]'}]}}
            ]
        }

        with patch("dispatch.transcription.gemini_engine.PROCESSING_DIR", self.proc_dir), \
             patch("dispatch.transcription.gemini_engine.split_audio_for_transcription", return_value=[(audio, 0.0)]), \
             patch(
                 "dispatch.youtube_connections.upload_user_gemini_media",
                 return_value={"file": {"name": "files/rt1", "uri": "https://generativelanguage.googleapis.com/v1beta/files/rt1"}},
             ) as mock_upload, \
             patch(
                 "dispatch.youtube_connections.call_user_gemini_proxy",
                 side_effect=[primary_resp, translit_resp],
             ) as mock_gen, \
             patch("dispatch.ai_clips.highlight_finder.requests.post", side_effect=AssertionError("Must not call direct HTTP")):
            res = transcribe_media(audio, api_key=make_worker_gemini_proxy_key("user_real_types"))

        self.assertEqual(res["source"], "gemini_transcribe")
        self.assertEqual(res["segments"][0]["text"], "Namaste Dispatch")
        self.assertEqual(mock_upload.call_count, 1)
        self.assertEqual(mock_gen.call_count, 2)
        self.assertEqual(
            mock_gen.call_args_list[0].kwargs["generation_config"],
            {"audioTranscriptionConfig": {"wordTimestamp": True, "diarization": True}},
        )
        self.assertEqual(mock_gen.call_args_list[1].kwargs["user_id"], "user_real_types")

    def test_render_clips_transliterates_devanagari_via_proxy_across_stage2_and_stage1_fallbacks(self):
        """Verify _render_clips_for_video passes user proxy key to all transliterate_segments_to_hinglish paths."""
        from dispatch.ai_clips.highlight_finder import transliterate_segments_to_hinglish

        def fake_extract(source_path, start_seconds, end_seconds, output_path, attempts=2):
            output_path.write_bytes(b"mp3-bytes")
            return output_path

        def fake_slice(source_video, clip_id, start_time, end_time, expected_range_revision=None):
            out_mp4 = self.tmp_path / f"{clip_id}.mp4"
            out_mp4.write_bytes(b"rendered-mp4")
            db.update_clip_media(clip_id, str(out_mp4), None, expected_range_revision=expected_range_revision)
            return {"clip_id": clip_id, "video_path": out_mp4}

        # Case A: Stage 2 words returned (empty words triggers line 662; mocked words triggers line 622)
        vid_a = "w1_render_dev_s2"
        chunk_a, src_a, _ = self._seed_transcribed_video(vid_a, user_id="user_dev_a", duration=120.0)
        clip_a = db.save_clip_definitions(
            chunk_id=chunk_a,
            session_id=None,
            video_id=vid_a,
            clip_defs=[{"start_time": 10.0, "end_time": 45.0, "title": "Dev A", "virality_score": 88}],
            user_id="user_dev_a",
        )[0]
        segs_hindi = [
            {
                "id": 0,
                "start": 10.0,
                "end": 45.0,
                "text": "नमस्ते दोस्तों",
                "words": [
                    {"word": "नमस्ते", "start": 10.0, "end": 11.0},
                    {"word": "दोस्तों", "start": 11.5, "end": 44.5},
                ],
            }
        ]
        translit_resp = {"candidates": [{"content": {"parts": [{"text": '["Namaste", "dosto"]'}]}}]}

        # Line 622: Stage 2 returns Devanagari words (mocked stage2 helper bypasses inner translit to exercise worker:622)
        with patch("dispatch.ai_clips.worker.PROCESSING_DIR", self.proc_dir), \
             patch("dispatch.transcription.gemini_engine.extract_stage2_clip_audio", side_effect=fake_extract), \
             patch(
                 "dispatch.transcription.gemini_engine.transcribe_stage2_precision_words",
                 return_value={"words": [{"word": "नमस्ते", "start": 4.1, "end": 4.8}, {"word": "दोस्तों", "start": 38.0, "end": 39.0}]},
             ), \
             patch("dispatch.youtube_connections.get_user_gemini_key", return_value=""), \
             patch("dispatch.youtube_connections.has_user_gemini_proxy", return_value=True), \
             patch("dispatch.youtube_connections.call_user_gemini_proxy", return_value=translit_resp) as mock_gen_a, \
             patch("dispatch.ai_clips.highlight_finder.requests.post", side_effect=AssertionError("Must not call direct HTTP")), \
             patch("dispatch.video_engine.renderer.slice_clean_clip", side_effect=fake_slice):
            HighlightWorker()._render_clips_for_video(vid_a, [clip_a], segs_hindi, src_a, api_key=None)
        mock_gen_a.assert_called_once()
        self.assertEqual(mock_gen_a.call_args.kwargs["user_id"], "user_dev_a")
        self.assertIn("Namaste", db.get_clip_caption_data(clip_a))

        # Line 662: Stage 2 returns empty words -> falls back to Stage 1 Devanagari segments
        vid_b = "w1_render_dev_empty"
        chunk_b, src_b, _ = self._seed_transcribed_video(vid_b, user_id="user_dev_b", duration=120.0)
        clip_b = db.save_clip_definitions(
            chunk_id=chunk_b,
            session_id=None,
            video_id=vid_b,
            clip_defs=[{"start_time": 10.0, "end_time": 45.0, "title": "Dev B", "virality_score": 88}],
            user_id="user_dev_b",
        )[0]
        with patch("dispatch.ai_clips.worker.PROCESSING_DIR", self.proc_dir), \
             patch("dispatch.transcription.gemini_engine.extract_stage2_clip_audio", side_effect=fake_extract), \
             patch("dispatch.transcription.gemini_engine.transcribe_stage2_precision_words", return_value={"words": []}), \
             patch("dispatch.youtube_connections.get_user_gemini_key", return_value=""), \
             patch("dispatch.youtube_connections.has_user_gemini_proxy", return_value=True), \
             patch("dispatch.youtube_connections.call_user_gemini_proxy", return_value=translit_resp) as mock_gen_b, \
             patch("dispatch.ai_clips.highlight_finder.requests.post", side_effect=AssertionError("Must not call direct HTTP")), \
             patch("dispatch.video_engine.renderer.slice_clean_clip", side_effect=fake_slice):
            HighlightWorker()._render_clips_for_video(vid_b, [clip_b], segs_hindi, src_b, api_key=None)
        mock_gen_b.assert_called_once()
        self.assertEqual(mock_gen_b.call_args.kwargs["user_id"], "user_dev_b")

        # Line 711: Stage 2 raises non-429 error -> except branch falls back to Stage 1 Devanagari segments
        vid_c = "w1_render_dev_err"
        chunk_c, src_c, _ = self._seed_transcribed_video(vid_c, user_id="user_dev_c", duration=120.0)
        clip_c = db.save_clip_definitions(
            chunk_id=chunk_c,
            session_id=None,
            video_id=vid_c,
            clip_defs=[{"start_time": 10.0, "end_time": 45.0, "title": "Dev C", "virality_score": 88}],
            user_id="user_dev_c",
        )[0]
        with patch("dispatch.ai_clips.worker.PROCESSING_DIR", self.proc_dir), \
             patch("dispatch.transcription.gemini_engine.extract_stage2_clip_audio", side_effect=fake_extract), \
             patch("dispatch.transcription.gemini_engine.transcribe_stage2_precision_words", side_effect=RuntimeError("HTTP 500 error")), \
             patch("dispatch.youtube_connections.get_user_gemini_key", return_value=""), \
             patch("dispatch.youtube_connections.has_user_gemini_proxy", return_value=True), \
             patch("dispatch.youtube_connections.call_user_gemini_proxy", return_value=translit_resp) as mock_gen_c, \
             patch("dispatch.ai_clips.highlight_finder.requests.post", side_effect=AssertionError("Must not call direct HTTP")), \
             patch("dispatch.video_engine.renderer.slice_clean_clip", side_effect=fake_slice):
            HighlightWorker()._render_clips_for_video(vid_c, [clip_c], segs_hindi, src_c, api_key=None)
        mock_gen_c.assert_called_once()
        self.assertEqual(mock_gen_c.call_args.kwargs["user_id"], "user_dev_c")

        # Unconfigured user_id passed to transliterate_segments_to_hinglish fails closed to phonetic fallback without host key
        with patch("dispatch.youtube_connections.get_user_gemini_key", return_value=""), \
             patch("dispatch.youtube_connections.has_user_gemini_proxy", return_value=False), \
             patch("dispatch.ai_clips.highlight_finder.requests.post", side_effect=AssertionError("Must not call direct HTTP")):
            out_segs = transliterate_segments_to_hinglish(segs_hindi, user_id="user_no_proxy")
        self.assertIn("namaste", out_segs[0]["text"].lower())

    def test_render_clips_fails_closed_for_user_clip_without_proxy_and_windowed_propagates_auth(self):
        """Verify direct _render_clips_for_video fails closed for unconfigured user and windowed >720s propagates 401."""
        vid = "w1_render_fc"
        chunk_id, src, segs = self._seed_transcribed_video(vid, user_id="user_fc", duration=120.0)
        clip_id = db.save_clip_definitions(
            chunk_id=chunk_id,
            session_id=None,
            video_id=vid,
            clip_defs=[{"start_time": 10.0, "end_time": 45.0, "title": "FC Clip", "virality_score": 85}],
            user_id="user_fc",
        )[0]
        with patch("dispatch.youtube_connections.get_user_gemini_key", return_value=""), \
             patch("dispatch.youtube_connections.has_user_gemini_proxy", return_value=False), \
             patch("dispatch.ai_clips.highlight_finder.requests.post", side_effect=AssertionError("Must not call direct HTTP")):
            with self.assertRaises(RuntimeError) as ctx:
                HighlightWorker()._render_clips_for_video(vid, [clip_id], segs, src, api_key=None)
        self.assertIn("Refusing fallback to PC host GEMINI_API_KEY", str(ctx.exception))
        self.assertEqual(db.get_setting("transcribe_quota_count"), "0")

        # Windowed >720s video receiving 401 in window #1 must propagate auth error immediately (no retry / no window #2)
        vid_long = "w1_win_401"
        self._seed_transcribed_video(vid_long, user_id="user_win_401", duration=950.0)
        with patch("dispatch.youtube_connections.get_user_gemini_key", return_value=""), \
             patch("dispatch.youtube_connections.has_user_gemini_proxy", return_value=True), \
             patch(
                 "dispatch.youtube_connections.call_user_gemini_proxy",
                 side_effect=VaultUnavailable("Worker Gemini generate proxy failed (401): API_KEY_INVALID"),
             ) as mock_gen_long:
            with self.assertRaises(RuntimeError) as ctx_long:
                HighlightWorker(max_attempts=5).process_video_highlights(vid_long)
        self.assertIn("authentication failed", str(ctx_long.exception).lower())
        self.assertEqual(mock_gen_long.call_count, 1)
        self.assertEqual(db.get_youtube_video(vid_long)["status"], "FAILED")


if __name__ == "__main__":
    unittest.main()
