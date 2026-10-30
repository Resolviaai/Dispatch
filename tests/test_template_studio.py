"""Unit tests for Template Studio persistence, endpoints, and word-level alignment."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from dispatch import db
from dispatch.config import PROCESSING_DIR
from dispatch.captions.model import CaptionTrack, StyleConfig, LayoutConfig, AnimationConfig
from dispatch.captions.grouper import group_words_into_captions, apply_template_to_track
from dispatch.captions.presets import get_default_preset_id
from dispatch.transcription.gemini_engine import (
    transcribe_stage2_precision_words,
    drain_queued_transcriptions,
)
from dispatch.web.app import app


def fake_genai_module():
    module = ModuleType("google.genai")
    module.types = SimpleNamespace(
        GenerateContentConfig=lambda **kwargs: kwargs,
        AudioTranscriptionConfig=lambda **kwargs: kwargs,
    )
    return module


class TestTemplateStudioEndpointsAndPersistence(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)

    def test_appassets_origin_accepted_by_local_network_trust(self):
        """Android WebView's appassets.androidplatform.net origin must be allowed without 403."""
        # Querying an endpoint with Android WebView origin
        response = self.client.get(
            "/api/template/default",
            headers={
                "Host": "127.0.0.1:8000",
                "Origin": "https://appassets.androidplatform.net",
            }
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("captions", data)
        self.assertIn("framing", data)

    def test_save_and_get_default_template_endpoints(self):
        """Verify GET and POST on /api/template/default and /api/captions/templates."""
        payload = {
            "name": "Creator Pro Custom",
            "framing": {
                "vertical_mode": "native_916",
                "horizontal_mode": "fit_blur",
            },
            "captions": {
                "preset_id": "custom",
                "font_family": "Bebas Neue",
                "font_size": 52,
                "font_weight": "800",
                "primary_color": "#00FFCC",
                "highlight_color": "#FF007F",
                "outline_enabled": True,
                "outline_color": "#000000",
                "outline_width": 5.0,
                "shadow_enabled": True,
                "shadow_color": "#111111",
                "shadow_blur": 6.0,
                "glow_enabled": True,
                "glow_color": "#00FFFF",
                "glow_blur": 14.0,
                "position_y": 0.82,
                "all_caps": True,
            },
            "hook": {"enabled": True, "style": "pill"},
            "publish_mode": "private",
            "platform_targets": ["youtube", "instagram"],
        }

        # 1. POST to /api/template/default
        res = self.client.post(
            "/api/template/default",
            json=payload,
            headers={
                "Host": "localhost:8000",
                "Origin": "http://localhost:5173",
            }
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["template"]["name"], "Creator Pro Custom")
        self.assertEqual(body["template"]["captions"]["font_family"], "Bebas Neue")

        # 2. GET from /api/template/default
        get_res = self.client.get("/api/template/default", headers={"Host": "localhost:8000"})
        self.assertEqual(get_res.status_code, 200)
        saved_data = get_res.json()
        self.assertEqual(saved_data["captions"]["font_family"], "Bebas Neue")
        self.assertEqual(saved_data["captions"]["primary_color"], "#00FFCC")

        # 3. GET alias from /api/captions/templates
        alias_get = self.client.get("/api/captions/templates", headers={"Host": "localhost:8000"})
        self.assertEqual(alias_get.status_code, 200)
        self.assertEqual(alias_get.json()["captions"]["font_family"], "Bebas Neue")

        # 4. POST alias to /api/captions/templates with nested {"template": {...}}
        nested_payload = {"template": {**payload, "name": "Nested Wrap Test"}}
        alias_post = self.client.post(
            "/api/captions/templates",
            json=nested_payload,
            headers={"Host": "localhost:8000", "Origin": "http://localhost:5173"}
        )
        self.assertEqual(alias_post.status_code, 200)
        self.assertEqual(alias_post.json()["template"]["name"], "Nested Wrap Test")

    def test_apply_template_to_track_fidelity(self):
        """Verify apply_template_to_track sets all styles, all_caps, glow, and shadow."""
        track = CaptionTrack(
            id="track_test",
            clip_id="clip_test",
            language="en",
            groups=[],
            default_style=StyleConfig(font_family="Impact", font_size=40),
            default_layout=LayoutConfig(position_y=0.75),
            default_animation=AnimationConfig(type="pop")
        )
        words = [
            {"id": "w1", "text": "Hello", "start": 0.0, "end": 0.5},
            {"id": "w2", "text": "World", "start": 0.5, "end": 1.0},
        ]
        track = group_words_into_captions(
            raw_segments=[{"id": 0, "start": 0.0, "end": 1.0, "words": words}],
            clip_start=0.0,
            clip_end=1.0,
            clip_id="clip_test",
            preset_id=get_default_preset_id()
        )

        tpl_captions = {
            "font_family": "Montserrat",
            "font_size": 48,
            "primary_color": "#EEEEEE",
            "highlight_color": "#FFDD00",
            "all_caps": True,
            "glow_enabled": True,
            "glow_color": "#00FF88",
            "glow_blur": 10.0,
            "shadow_enabled": True,
            "shadow_offset_x": 3.0,
            "shadow_offset_y": 4.0,
            "position_y": 0.85,
            "animation": "slide"
        }

        updated_track = apply_template_to_track(track, tpl_captions)
        self.assertEqual(updated_track.default_style.font_family, "Montserrat")
        self.assertEqual(updated_track.default_style.font_size, 48)
        self.assertEqual(updated_track.default_style.text_transform, "uppercase")
        self.assertTrue(updated_track.default_style.glow_enabled)
        self.assertEqual(updated_track.default_style.glow_color, "#00FF88")
        self.assertEqual(updated_track.default_style.shadow_offset_x, 3.0)
        self.assertEqual(updated_track.default_style.shadow_offset_y, 4.0)
        self.assertEqual(updated_track.default_layout.position_y, 0.85)
        self.assertEqual(updated_track.default_animation.type, "slide")
        # Ensure all groups inherit the styles
        for grp in updated_track.groups:
            self.assertEqual(grp.style.font_family, "Montserrat")
            self.assertEqual(grp.layout.position_y, 0.85)
            self.assertEqual(grp.animation.type, "slide")

    @patch("dispatch.transcription.gemini_engine.get_gemini_client")
    def test_stage2_fallback_to_flash_lite_on_transcribe_failure(self, mock_get_client):
        """When gemini-3.5-transcribe fails, it should seamlessly fall back to gemini-3.5-flash-lite."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_file = MagicMock()
        mock_file.name = "files/test_fallback"
        mock_client.files.upload.return_value = mock_file

        # First call to gemini-3.5-transcribe raises Exception
        # Second call to gemini-3.5-flash-lite returns valid JSON
        flash_lite_resp = MagicMock()
        flash_lite_resp.text = json.dumps([
            {"word": "Dispatch", "start": 0.10, "end": 0.60, "speaker": "spk:0"},
            {"word": "Launch", "start": 0.65, "end": 1.20, "speaker": "spk:0"}
        ])

        def side_effect_generate(model, contents, config=None):
            if model == "gemini-3.5-transcribe":
                raise RuntimeError("gemini-3.5-transcribe service unavailable")
            elif model == "gemini-3.5-flash-lite":
                return flash_lite_resp
            raise ValueError(f"Unexpected model {model}")

        mock_client.models.generate_content.side_effect = side_effect_generate

        test_file = PROCESSING_DIR / "test_fallback_audio.mp3"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_bytes(b"dummy audio")

        with patch.dict(sys.modules, {"google.genai": fake_genai_module()}), \
             patch("dispatch.transcription.gemini_engine._probe_audio_duration", return_value=5.0):
            result = transcribe_stage2_precision_words(test_file)

        try:
            self.assertEqual(len(result["words"]), 2)
            self.assertEqual(result["words"][0]["word"], "Dispatch")
            self.assertEqual(result["words"][0]["start"], 0.10)
            self.assertEqual(result["words"][1]["word"], "Launch")
            self.assertEqual(result["words"][1]["end"], 1.20)
            self.assertEqual(result["speech_start"], 0.10)
            self.assertEqual(result["speech_end"], 1.20)
        finally:
            if test_file.exists():
                test_file.unlink()


    def test_drain_queued_transcriptions_inherits_user_default_template(self):
        """drain_queued_transcriptions must apply creator's saved template to caption track and framing."""
        # Save a custom default template for user_123
        user_id = "test_creator_abc"
        custom_tpl = {
            "name": "Creator Custom V3",
            "framing": {"horizontal_mode": "crop_916", "vertical_mode": "native_916"},
            "captions": {
                "preset_id": "custom",
                "font_family": "Outfit",
                "font_size": 56,
                "primary_color": "#FFCC00",
                "highlight_color": "#FF0055",
                "position_y": 0.80,
                "all_caps": True
            }
        }
        db.save_default_template(custom_tpl, user_id=user_id)

        with tempfile.TemporaryDirectory() as tmp:
            dummy_src = Path(tmp) / "src.mp4"
            dummy_src.write_bytes(b"dummy")

            clip_id = "test_clip_drain_tpl"
            with db.get_db_connection() as conn:
                conn.cursor().execute("INSERT OR IGNORE INTO sessions (id, status) VALUES ('sess_drain', 'ready')")
                conn.cursor().execute("INSERT OR REPLACE INTO chunks (id, session_id, filename, filepath, status) VALUES ('test_chunk_drain', 'sess_drain', 'src.mp4', ?, 'ready')", (str(dummy_src),))
                conn.cursor().execute("DELETE FROM clips WHERE id = ?", (clip_id,))
                conn.cursor().execute("""
                    INSERT INTO clips (id, chunk_id, user_id, start_time, end_time, padded_start, padded_end, duration, title, hook, virality_score, status, range_revision, layout_mode)
                    VALUES (?, 'test_chunk_drain', ?, 10.0, 30.0, 8.0, 32.0, 20.0, 'Drain Test', 'Hook', 80, 'queued_transcription', 0, NULL)
                """, (clip_id, user_id))
                conn.commit()

            fake_words = [
                {"word": "Awesome", "start": 0.5, "end": 1.2, "speaker": "spk:0"},
                {"word": "highlight", "start": 1.3, "end": 2.0, "speaker": "spk:0"}
            ]

            with patch("dispatch.transcription.gemini_engine.extract_stage2_clip_audio", return_value=dummy_src),                  patch("dispatch.transcription.gemini_engine.check_and_increment_daily_transcribe_quota", return_value=True),                  patch("dispatch.transcription.gemini_engine.transcribe_stage2_precision_words", return_value={"words": fake_words, "speech_start": 0.5, "speech_end": 2.0}),                  patch("dispatch.video_engine.renderer.render_clip") as mock_render:

                drained = drain_queued_transcriptions(max_batch=1)

            self.assertEqual(drained, 1)
            mock_render.assert_called_once()
            # Verify layout_mode was inherited from user_tpl framing
            self.assertEqual(mock_render.call_args.kwargs.get("layout_mode"), "crop_916")

            # Check that saved caption data has the user template styles
            cap_json = db.get_clip_caption_data(clip_id)
            self.assertIsNotNone(cap_json)
            cap_data = json.loads(cap_json)
            self.assertEqual(cap_data["default_style"]["font_family"], "Outfit")
            self.assertEqual(cap_data["default_style"]["primary_color"], "#FFCC00")
            self.assertEqual(cap_data["default_style"]["highlight_color"], "#FF0055")
            self.assertEqual(cap_data["default_style"]["text_transform"], "uppercase")

        # Cleanup
        with db.get_db_connection() as conn:
            conn.cursor().execute("DELETE FROM clips WHERE id = ?", (clip_id,))
            conn.commit()

if __name__ == "__main__":
    unittest.main()
