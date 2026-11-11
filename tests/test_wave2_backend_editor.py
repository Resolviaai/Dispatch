"""Wave 2A Slice 2 regression tests (@w2a-backend-editor).

Covers:
1. HTTP 409 Conflict + `{error: "revision_conflict", ...}` on stale timeline / CAS mutations & 0->1 transitions
2. `resolve_canonical_source_asset` never falling back to rendered `clip["video_path"]` + endpoint 404/409 errors
3. `edit_window_start` / `edit_window_end` clip serialization + BYOK `user_id` transliteration threading
4. Portrait `edit_windows` proxy aspect preservation (`720x1280`), `run_ffmpeg`, & raw audio extraction
5. `dispatch/cloud/sync.py` canonical `compute_render_fingerprint`, `stage_edit_window`, & edit window metadata
6. `dispatch/ai_clips/worker.py` deterministic `>720s` `chunk_offset` + `gemini_engine.py` `is_exact` flags
7. `dispatch/captions/range_sync.py` `word_id` rebase + `uncovered_ranges` gap hook
8. `dispatch/captions/exporter.py` `rgba(...)` ASS math + dynamic `PlayResX`/`PlayResY`
9. `dispatch/video_engine/autocut.py` tail phoneme/release padding & `source_end` mid-syllable clamp
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import shutil
import struct
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from dispatch import db, edit_windows
from dispatch.captions import CaptionGroup, CaptionTrack, LayoutConfig, StyleConfig, Word, export_to_ass
from dispatch.captions.exporter import hex_to_ass_color, resolve_ass_play_res
from dispatch.captions.range_sync import (
    compute_word_id,
    rebase_caption_track,
    sync_caption_range_with_gap_transcription,
)
from dispatch.cloud import sync as cloud_sync
from dispatch.segments import compute_media_fingerprint, compute_render_fingerprint, round_ms
from dispatch.transcription.gemini_engine import (
    _group_words_into_segments,
    _transcribe_chunk_fallback,
    _transcribe_chunk_primary,
)
from dispatch.ai_clips import worker as ai_worker
from dispatch.video_engine.autocut import analyze_waveform_and_transcript_autocuts
web_app = importlib.import_module("dispatch.web.app")


class TestWave2BackendEditor(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="w2a_be_"))
        self.orig_db_path = db.DB_PATH
        self.db_path = self.tmp_dir / "wave2a.db"
        db.DB_PATH = self.db_path
        db.init_db()

        self.orig_env = {
            k: os.environ.get(k)
            for k in ("DISPATCH_AUTH_TOKEN", "DISPATCH_USER_ID", "DISPATCH_TRUST_LAN")
        }
        os.environ["DISPATCH_AUTH_TOKEN"] = "w2a-test-secret"
        self.user_id = "11111111-1111-1111-1111-111111111111"
        os.environ["DISPATCH_USER_ID"] = self.user_id
        self.headers = {"X-Dispatch-Auth": "w2a-test-secret"}
        self.client = TestClient(web_app.app)

    def tearDown(self) -> None:
        self.client.close()
        db.close_db_connections()
        db.DB_PATH = self.orig_db_path
        for k, v in self.orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _seed_clip(
        self,
        *,
        with_raw_file: bool = True,
        with_rendered_file: bool = True,
        chunk_duration: float = 120.0,
        start_time: float = 10.0,
        end_time: float = 25.0,
    ) -> dict:
        chunk_id = f"chk_{uuid.uuid4().hex[:8]}"
        sess_id = f"sess_{uuid.uuid4().hex[:8]}"
        raw_path = self.tmp_dir / f"{chunk_id}_raw.mp4"
        rendered_path = self.tmp_dir / f"clip_{uuid.uuid4().hex[:6]}_rendered.mp4"

        if with_raw_file:
            raw_path.write_bytes(b"\x00\x00\x00\x18ftypmp42RAW_SOURCE_BYTES" * 16)
        if with_rendered_file:
            rendered_path.write_bytes(b"\x00\x00\x00\x18ftypmp42RENDERED_CLIP_BYTES" * 16)

        with db.get_db_connection() as conn:
            conn.execute(
                "INSERT INTO sessions (id, status, user_id) VALUES (?, 'completed', ?)",
                (sess_id, self.user_id),
            )
            conn.execute(
                """
                INSERT INTO chunks (id, session_id, filename, filepath, duration, width, height, aspect_ratio, status, user_id)
                VALUES (?, ?, ?, ?, ?, 1920, 1080, '16:9', 'processed', ?)
                """,
                (chunk_id, sess_id, raw_path.name, str(raw_path), chunk_duration, self.user_id),
            )
            conn.commit()

        clip_id = db.save_clip(
            session_id=sess_id,
            chunk_id=chunk_id,
            start_time=start_time,
            end_time=end_time,
            title="Wave2A Test Clip",
            hook="Hook",
            description="",
            hashtags="#test",
            virality_score=88,
            layout_mode="fit_blur",
            user_id=self.user_id,
        )
        if with_rendered_file:
            db.update_clip_media(clip_id, str(rendered_path), None)
        return {
            "clip_id": clip_id,
            "chunk_id": chunk_id,
            "session_id": sess_id,
            "raw_path": raw_path,
            "rendered_path": rendered_path,
        }

    # 1. HTTP 409 Conflict & Monotonic CAS
    def test_timeline_stale_returns_http_409_conflict_payload_never_200(self) -> None:
        seeded = self._seed_clip()
        clip_id = seeded["clip_id"]
        doc_v1 = {
            "version": 1,
            "source_asset_id": seeded["chunk_id"],
            "timeline": {"segments": [[10.0, 22.0]]},
            "tracks": [],
        }

        # Monotonic 0 -> 1 transition succeeds
        res1 = self.client.put(
            f"/api/clips/{clip_id}/timeline",
            headers=self.headers,
            json={
                "expected_revision": 0,
                "project_document": doc_v1,
                "operation_id": "op-1",
                "action_type": "trim_segment",
            },
        )
        self.assertEqual(res1.status_code, 200, res1.text)
        body1 = res1.json()
        self.assertEqual(body1["revision"], 1)
        self.assertEqual(body1["segments_revision"], 1)

        # Stale expected_revision=0 must return HTTP 409 with structured conflict payload
        res_stale = self.client.put(
            f"/api/clips/{clip_id}/timeline",
            headers=self.headers,
            json={
                "expected_revision": 0,
                "project_document": {
                    "version": 1,
                    "source_asset_id": seeded["chunk_id"],
                    "timeline": {"segments": [[10.0, 20.0]]},
                },
                "operation_id": "op-stale",
            },
        )
        self.assertEqual(res_stale.status_code, 409, res_stale.text)
        conflict = res_stale.json()
        self.assertEqual(conflict["error"], "revision_conflict")
        self.assertEqual(conflict["current_revision"], 1)
        self.assertEqual(conflict["active_state_revision"], 1)
        self.assertEqual(conflict["current_document"], doc_v1)
        self.assertEqual(conflict["segments"], [[10.0, 22.0]])
        self.assertEqual(conflict["start_time"], 10.0)
        self.assertEqual(conflict["end_time"], 25.0)

        # Even if import_cloud_timeline_data itself returns "stale" under concurrency, HTTP 409 (never 200)
        with patch.object(db, "import_cloud_timeline_data", return_value="stale"):
            res_race = self.client.post(
                f"/api/clips/{clip_id}/timeline",
                headers=self.headers,
                json={"expected_revision": 1, "project_document": doc_v1},
            )
        self.assertEqual(res_race.status_code, 409, res_race.text)
        self.assertEqual(res_race.json()["error"], "revision_conflict")

    def test_trim_captions_approve_cas_409_and_rev0_to_rev1(self) -> None:
        seeded = self._seed_clip()
        clip_id = seeded["clip_id"]

        # Trim 0 -> 1
        tr1 = self.client.post(
            f"/api/clips/{clip_id}/trim",
            headers=self.headers,
            json={"start_time": 12.0, "end_time": 24.0, "expected_range_revision": 0},
        )
        self.assertEqual(tr1.status_code, 200, tr1.text)
        self.assertEqual(tr1.json()["range_revision"], 1)
        self.assertIn("edit_window_start", tr1.json())
        self.assertIn("edit_window_end", tr1.json())

        # Stale trim rev 0 -> 409
        tr_stale = self.client.post(
            f"/api/clips/{clip_id}/trim",
            headers=self.headers,
            json={"start_time": 11.0, "end_time": 23.0, "expected_range_revision": 0},
        )
        self.assertEqual(tr_stale.status_code, 409)

        cap_payload = {
            "clip_id": clip_id,
            "range_revision": 1,
            "expected_range_revision": 1,
            "expected_captions_revision": 0,
            "groups": [
                {
                    "id": "g1",
                    "type": "caption",
                    "start": 0.5,
                    "end": 2.0,
                    "text": "hello world",
                    "has_word_timing": True,
                    "timing_mode": "word",
                    "words": [
                        {"word": "hello", "start": 0.5, "end": 1.1},
                        {"word": "world", "start": 1.2, "end": 2.0},
                    ],
                }
            ],
        }
        with patch.object(web_app, "export_to_ass") as mock_export:
            sc1 = self.client.post(
                f"/api/clips/{clip_id}/captions",
                headers=self.headers,
                json=cap_payload,
            )
        self.assertEqual(sc1.status_code, 200, sc1.text)
        self.assertEqual(sc1.json()["captions_revision"], 1)
        self.assertEqual(mock_export.call_args.kwargs["aspect_ratio"], "9:16")

        # Stale expected_captions_revision=0 and stale expected_segments_revision=99 -> 409
        sc_stale_cap = self.client.post(
            f"/api/clips/{clip_id}/captions",
            headers=self.headers,
            json={**cap_payload, "expected_captions_revision": 0},
        )
        self.assertEqual(sc_stale_cap.status_code, 409)

        sc_stale_seg = self.client.post(
            f"/api/clips/{clip_id}/captions",
            headers=self.headers,
            json={**cap_payload, "expected_captions_revision": 1, "expected_segments_revision": 99},
        )
        self.assertEqual(sc_stale_seg.status_code, 409)

        # Approve with stale expected_segments_revision -> 409
        ap_stale = self.client.post(
            f"/api/clips/{clip_id}/approve",
            headers=self.headers,
            json={"expected_range_revision": 1, "expected_segments_revision": 99},
        )
        self.assertEqual(ap_stale.status_code, 409)

    # 2. Canonical raw source resolver + caller endpoints + serialization + BYOK
    def test_resolve_canonical_source_asset_never_falls_back_to_rendered_video_path(self) -> None:
        seeded = self._seed_clip(with_raw_file=False, with_rendered_file=True, chunk_duration=0.0)
        clip_id = seeded["clip_id"]
        clip = db.get_clip_by_id(clip_id)

        # Must NEVER fall back to rendered video_path even when video_path exists on disk
        self.assertTrue(seeded["rendered_path"].exists())
        self.assertIsNone(web_app.resolve_canonical_source_asset(clip))
        self.assertIsNone(web_app.resolve_clip_source_path(clip))

        # Poison chunks.filepath so it points to rendered clip["video_path"] -> still rejected!
        with db.get_db_connection() as conn:
            conn.execute(
                "UPDATE chunks SET filepath = ? WHERE id = ?",
                (str(seeded["rendered_path"]), seeded["chunk_id"]),
            )
            conn.commit()
        self.assertIsNone(web_app.resolve_canonical_source_asset(clip))

        # Every endpoint using canonical raw source must fail cleanly (404/409)
        self.assertEqual(self.client.get(f"/api/clips/{clip_id}/source", headers=self.headers).status_code, 404)
        self.assertIn(self.client.post(f"/api/clips/{clip_id}/auto_reframe", headers=self.headers).status_code, (404, 409))
        self.assertIn(self.client.post(f"/api/clips/{clip_id}/render", headers=self.headers, json={"layout_mode": "fit_blur"}).status_code, (404, 409))
        self.assertIn(self.client.post(f"/api/clips/{clip_id}/trim", headers=self.headers, json={"start_time": 11.0, "end_time": 22.0}).status_code, (404, 409))
        self.assertIn(self.client.get(f"/api/clips/{clip_id}/autocut/proposals", headers=self.headers).status_code, (404, 409))
        self.assertEqual(self.client.get(f"/api/clips/{clip_id}/waveform", headers=self.headers).status_code, 404)

    def test_clip_serialization_includes_edit_window_and_byok_transliterate_user_id(self) -> None:
        seeded = self._seed_clip(with_raw_file=True, with_rendered_file=True, chunk_duration=200.0)
        clip_id = seeded["clip_id"]

        with patch.object(web_app, "resolve_caller_user_id", return_value=self.user_id):
            detail = self.client.get(f"/api/clips/{clip_id}", headers=self.headers)
            self.assertEqual(detail.status_code, 200, detail.text)
            d = detail.json()
            self.assertEqual(d["edit_window_start"], 0.0)
            self.assertEqual(d["edit_window_end"], 85.0)

            listing = self.client.get("/api/clips", headers=self.headers)
            self.assertEqual(listing.status_code, 200, listing.text)
            ready = next(c for c in listing.json()["ready"] if c["id"] == clip_id)
            self.assertEqual(ready["edit_window_start"], 0.0)
            self.assertEqual(ready["edit_window_end"], 85.0)

        # Uncached Devanagari transcript path passes user_id to transliterate_segments_to_hinglish
        with db.get_db_connection() as conn:
            conn.execute(
                "INSERT INTO transcripts (id, chunk_id, full_text, segments_json) VALUES (?, ?, ?, ?)",
                (
                    "tx_hi_1",
                    seeded["chunk_id"],
                    "नमस्ते",
                    json.dumps([{"start": 10.5, "end": 12.0, "text": "नमस्ते", "words": [{"word": "नमस्ते", "start": 10.5, "end": 12.0}]}]),
                ),
            )
            conn.commit()
        with patch("dispatch.web.app.transliterate_segments_to_hinglish", return_value=[
            {"start": 10.5, "end": 12.0, "text": "namaste", "words": [{"word": "namaste", "start": 10.5, "end": 12.0}]}
        ]) as mock_tseg:
            r_uncached = self.client.get(f"/api/clips/{clip_id}/captions", headers=self.headers)
        self.assertEqual(r_uncached.status_code, 200)
        self.assertEqual(mock_tseg.call_args.kwargs["user_id"], self.user_id)

        # Cached Devanagari track path passes user_id to transliterate_tokens_to_hinglish
        db.save_clip_caption_data(
            clip_id,
            json.dumps({
                "clip_id": clip_id,
                "groups": [{"id": "g1", "start": 0.5, "end": 1.5, "text": "नमस्ते", "words": [{"word": "नमस्ते", "start": 0.5, "end": 1.5}]}],
            }),
            expected_range_revision=0,
            source_range_start=10.0,
            source_range_end=25.0,
        )
        with patch("dispatch.ai_clips.highlight_finder.transliterate_tokens_to_hinglish", return_value=["namaste"]) as mock_ttok:
            r_cached = self.client.get(f"/api/clips/{clip_id}/captions", headers=self.headers)
        self.assertEqual(r_cached.status_code, 200)
        self.assertEqual(mock_ttok.call_args.kwargs["user_id"], self.user_id)

    # 3. Edit window portrait aspect preservation + run_ffmpeg + raw audio
    def test_edit_windows_portrait_aspect_and_run_ffmpeg(self) -> None:
        cmd_port = edit_windows.build_proxy_cmd("in.mp4", "out.mp4", 10.0, 40.0, source_width=1080, source_height=1920, orientation="portrait")
        vf_port = cmd_port[cmd_port.index("-vf") + 1]
        self.assertIn("720:1280", vf_port)
        self.assertNotIn("1280:720", vf_port)

        cmd_land = edit_windows.build_proxy_cmd("in.mp4", "out.mp4", 10.0, 40.0, source_width=1920, source_height=1080, orientation="landscape")
        vf_land = cmd_land[cmd_land.index("-vf") + 1]
        self.assertIn("1280:720", vf_land)

        cmd_45 = edit_windows.build_proxy_cmd("in.mp4", "out.mp4", 10.0, 40.0, source_width=1080, source_height=1350)
        vf_45 = cmd_45[cmd_45.index("-vf") + 1]
        self.assertIn("720:900", vf_45)

        self.assertIsNone(
            edit_windows.ffprobe_validate_proxy(
                {"duration": 30.0, "has_audio": True, "width": 720, "height": 1280},
                30.0,
                source_meta={"width": 1080, "height": 1920, "orientation": "portrait"},
            )
        )
        self.assertIsNotNone(
            edit_windows.ffprobe_validate_proxy(
                {"duration": 30.0, "has_audio": True, "width": 1280, "height": 720},
                30.0,
                source_meta={"width": 1080, "height": 1920, "orientation": "portrait"},
            )
        )

        raw_src = self.tmp_dir / "portrait_raw.mp4"
        raw_src.write_bytes(b"RAW_PORTRAIT_BYTES")
        recorded_cmds = []

        def fake_ffmpeg(cmd, **_kwargs):
            recorded_cmds.append(list(cmd))
            Path(cmd[-1]).write_bytes(b"PROXY_OR_AUDIO")
            return SimpleNamespace(returncode=0, stderr="")

        fake_store = MagicMock()
        fake_store.reserve_upload.return_value = SimpleNamespace(reservation_id="r1", media_object_id="mo_port")
        fake_store.upload_and_finalize.return_value = "mo_port"
        fake_db = MagicMock()
        fake_db.create_window.return_value = "ew_port_1"

        res = edit_windows.stage_edit_window(
            "clip_p",
            self.user_id,
            str(raw_src),
            5.0,
            25.0,
            transcribe_fn=lambda _p, max_input_seconds=360: {"words": [{"word": "hi", "start": 1.0, "end": 1.5}]},
            media_store=fake_store,
            db_ops=fake_db,
            ffprobe_meta=lambda p: {"duration": 20.0, "has_audio": True, "width": 720, "height": 1280, "orientation": "portrait"} if "proxy" in p else {"width": 1080, "height": 1920, "orientation": "portrait"},
            run_ffmpeg=fake_ffmpeg,
        )
        self.assertEqual(res["media_object_id"], "mo_port")
        self.assertEqual(len(recorded_cmds), 2)
        self.assertIn("720:1280", recorded_cmds[0][recorded_cmds[0].index("-vf") + 1])
        self.assertEqual(recorded_cmds[1][recorded_cmds[1].index("-i") + 1], str(raw_src))

    # 4. cloud/sync.py canonical render_fingerprint + stage_edit_window + edit_window metadata
    def test_cloud_sync_canonical_fingerprint_and_edit_window_staging(self) -> None:
        seeded = self._seed_clip(with_raw_file=True, with_rendered_file=True, chunk_duration=240.0, start_time=60.0, end_time=80.0)
        with db.get_db_connection() as conn:
            conn.execute(
                "UPDATE clips SET layout_mode = 'crop_916', crop_anchor_x = 0.25 WHERE id = ?",
                (seeded["clip_id"],),
            )
            conn.commit()
        clip = db.get_clip_by_id(seeded["clip_id"])

        expected_media_fp = compute_media_fingerprint(seeded["chunk_id"], [(60.0, 80.0)])
        expected_fp = compute_render_fingerprint(expected_media_fp, "crop_916", 0.25, layers=[])
        fp1 = cloud_sync._render_fingerprint(clip, seeded["rendered_path"])
        # File bytes/mtime changes on rendered output must NOT change canonical render fingerprint
        seeded["rendered_path"].write_bytes(b"COMPLETELY_DIFFERENT_SIZE_AND_MTIME" * 30)
        fp2 = cloud_sync._render_fingerprint(clip, seeded["rendered_path"])
        self.assertEqual(fp1, expected_fp)
        self.assertEqual(fp2, expected_fp)

        captured_clip_post = {}

        def fake_get(url, **_kwargs):
            return SimpleNamespace(ok=True, text="{}", json=lambda: {"already_staged": False})

        def fake_post(url, json=None, **_kwargs):
            if url.endswith("/sync/pc/clips"):
                captured_clip_post["payload"] = json
                return SimpleNamespace(ok=True, text='{"cloud_clip_id":"cc_1"}', json=lambda: {"cloud_clip_id": "cc_1"})
            return SimpleNamespace(ok=True, text="{}", json=lambda: {})

        with patch.object(cloud_sync, "requests", SimpleNamespace(get=fake_get, post=fake_post, put=MagicMock())), \
             patch.object(cloud_sync, "_worker_base", return_value="https://worker.example"), \
             patch.object(cloud_sync, "get_worker_token", return_value="tok"), \
             patch.object(cloud_sync.edit_windows, "stage_edit_window") as mock_stage:
            ok = cloud_sync.sync_clip_to_cloud(seeded["clip_id"])

        self.assertTrue(ok)
        self.assertEqual(captured_clip_post["payload"]["render_fingerprint"], expected_fp)
        self.assertEqual(captured_clip_post["payload"]["edit_window_start"], 0.0)
        self.assertEqual(captured_clip_post["payload"]["edit_window_end"], 140.0)
        mock_stage.assert_called_once()
        self.assertEqual(Path(mock_stage.call_args.kwargs["source_video"]), seeded["raw_path"])
        self.assertEqual(mock_stage.call_args.kwargs["window_start"], 0.0)
        self.assertEqual(mock_stage.call_args.kwargs["window_end"], 140.0)

        # When raw source is absent, sync_clip_to_cloud must NOT stage/upload trimmed video_path
        seeded["raw_path"].unlink()
        with patch.object(cloud_sync, "requests", SimpleNamespace(get=fake_get, post=fake_post, put=MagicMock())), \
             patch.object(cloud_sync, "_worker_base", return_value="https://worker.example"), \
             patch.object(cloud_sync, "get_worker_token", return_value="tok"), \
             patch.object(cloud_sync.edit_windows, "stage_edit_window") as mock_stage_missing:
            ok_missing = cloud_sync.sync_clip_to_cloud(seeded["clip_id"])
        self.assertTrue(ok_missing)
        mock_stage_missing.assert_not_called()

    # 5. worker.py deterministic >720s chunk_offset & gemini_engine is_exact flags
    def test_worker_deterministic_chunk_offset_and_gemini_is_exact(self) -> None:
        rebased = ai_worker.rebase_window_candidate_timestamps(
            [{"start_time": 18.0, "end_time": 42.0, "title": "Win2 Clip"}],
            chunk_offset=630.0,
            window_duration=720.0,
            total_duration=1500.0,
        )
        self.assertEqual(rebased[0]["start_time"], 648.0)
        self.assertEqual(rebased[0]["end_time"], 672.0)

        segments = [
            {"start": 15.0, "end": 45.0, "text": "intro block"},
            {"start": 740.0, "end": 780.0, "text": "late peak block"},
            {"start": 1280.0, "end": 1310.0, "text": "window three block"},
        ]

        def fake_gemini(prompt_text, api_key=None, candidate_models=None):
            if "late peak block" in prompt_text and "intro block" not in prompt_text:
                return ([{"start_time": 110.0, "end_time": 140.0, "title": "Peak", "virality_score": 90}], "mock-flash")
            if "window three block" in prompt_text and "late peak block" not in prompt_text:
                return ([{"start_time": 20.0, "end_time": 50.0, "title": "Third", "virality_score": 88}], "mock-flash")
            return ([], "mock-flash")

        with patch.object(ai_worker, "call_gemini_generate_content", side_effect=fake_gemini):
            cands, _model = ai_worker._extract_highlights_windowed_deterministic(segments, total_duration=1400.0)
        starts = sorted(round(c["start_time"], 1) for c in cands)
        self.assertEqual(starts, [740.0, 1280.0])

        # gemini_engine fallback synthetic/phrase words carry is_exact=False; primary carries is_exact=True
        mock_audio = self.tmp_dir / "tiny.mp3"
        mock_audio.write_bytes(b"ID3")
        fake_fallback_client = SimpleNamespace(
            files=SimpleNamespace(upload=lambda **_: SimpleNamespace(name="f1"), delete=lambda **_: None),
            models=SimpleNamespace(
                generate_content=lambda **_: SimpleNamespace(
                    text=json.dumps([{"start": "00:00.00", "end": "00:02.00", "text": "hello world", "speaker": "spk:0"}])
                )
            ),
        )
        fb_res = _transcribe_chunk_fallback(fake_fallback_client, mock_audio)
        self.assertTrue(len(fb_res["words"]) == 2)
        self.assertTrue(all(w["is_exact"] is False for w in fb_res["words"]))

        fake_primary_client = SimpleNamespace(
            files=SimpleNamespace(upload=lambda **_: SimpleNamespace(name="f2"), delete=lambda **_: None),
            models=SimpleNamespace(
                generate_content=lambda **_: SimpleNamespace(
                    text="exact word",
                    candidates=[
                        SimpleNamespace(
                            content=SimpleNamespace(
                                parts=[
                                    SimpleNamespace(
                                        audio_transcription=SimpleNamespace(
                                            speaker_label="spk:0",
                                            words=[SimpleNamespace(word="exact", start_offset="0.1s", end_offset="0.5s")],
                                        )
                                    )
                                ]
                            )
                        )
                    ],
                )
            ),
        )
        with patch("dispatch.transcription.gemini_engine.check_and_increment_daily_transcribe_quota", return_value=True), \
             patch("dispatch.transcription.gemini_engine._consume_daily_transcribe_quota"):
            pr_res = _transcribe_chunk_primary(fake_primary_client, mock_audio)
        self.assertTrue(all(w["is_exact"] is True for w in pr_res["words"]))
        grouped = _group_words_into_segments(fb_res["words"])
        self.assertTrue(all(w["is_exact"] is False for w in grouped[0]["words"]))

    # 6. range_sync word_id + uncovered_ranges hook
    def test_range_sync_word_id_rebase_and_uncovered_ranges_hook(self) -> None:
        orig_word = {"word": "oldword", "start": 2.0, "end": 2.5}
        wid = compute_word_id(orig_word, source_origin=10.0)
        del_wid = compute_word_id({"word": "removed", "start": 3.0, "end": 3.4}, source_origin=10.0)
        saved_track = {
            "clip_id": "c_rs",
            "deleted_word_ids": [del_wid],
            "groups": [
                {
                    "id": "g1",
                    "type": "caption",
                    "start": 2.0,
                    "end": 3.4,
                    "text": "edited",
                    "words": [
                        {"word": "EDITED_CUSTOM", "start": 2.0, "end": 2.5, "word_id": wid, "edited": True},
                    ],
                }
            ],
        }
        transcript_track = {
            "groups": [
                {
                    "id": "tg1",
                    "type": "caption",
                    "start": 0.0,
                    "end": 6.0,
                    "words": [
                        {"word": "intro", "start": 0.2, "end": 0.8},
                        {"word": "oldword", "start": 4.0, "end": 4.5, "word_id": wid},
                        {"word": "removed", "start": 5.0, "end": 5.4, "word_id": del_wid},
                    ],
                }
            ]
        }

        rebased, warning, uncovered = sync_caption_range_with_gap_transcription(
            saved_track,
            saved_source_start=10.0,
            saved_source_end=15.0,
            selected_start=8.0,
            selected_end=18.0,
            transcript_track=transcript_track,
            gap_transcribe_fn=lambda gs, ge: (
                [{"word": "tailgap", "start": gs + 0.5, "end": gs + 1.0, "is_exact": True}]
                if gs >= 14.9 else []
            ),
        )
        words_out = [w.get("text") or w.get("word") or "" for g in rebased["groups"] for w in g.get("words", [])]
        self.assertIn("EDITED_CUSTOM", words_out)
        self.assertNotIn("oldword", words_out)
        self.assertNotIn("removed", words_out)
        self.assertIn("tailgap", words_out)
        self.assertTrue(len(uncovered) == 0 or not warning)

    # 7. exporter rgba(...) ASS color + dynamic PlayRes
    def test_exporter_rgba_and_dynamic_play_res(self) -> None:
        # rgba(255, 16, 32, 0.5) -> alpha byte = round((1-0.5)*255) = 128 (0x80), B=0x20, G=0x10, R=0xFF
        self.assertEqual(hex_to_ass_color("rgba(255, 16, 32, 0.5)"), "&H802010FF&")
        # alpha="80" (~0.5 opacity) combined with css alpha 0.25 -> ~0.125 opacity -> 223 (0xDF)
        self.assertEqual(hex_to_ass_color("rgba(0, 0, 0, 0.25)", alpha="80"), "&HDF000000&")
        self.assertEqual(resolve_ass_play_res("9:16"), (1080, 1920))
        self.assertEqual(resolve_ass_play_res("16:9"), (1920, 1080))
        self.assertEqual(resolve_ass_play_res("1:1"), (1080, 1080))

        track = CaptionTrack(
            clip_id="c_ass",
            default_style=StyleConfig(font_size=48, primary_color="rgba(255, 16, 32, 0.5)"),
            default_layout=LayoutConfig(),
            groups=[
                CaptionGroup(
                    id="g1",
                    start=0.2,
                    end=1.2,
                    words=[Word(text="test", start=0.2, end=1.2)],
                )
            ],
        )
        out_ass = self.tmp_dir / "land.ass"
        export_to_ass(track, out_ass, aspect_ratio="16:9")
        ass_text = out_ass.read_text(encoding="utf-8")
        self.assertIn("PlayResX: 1920", ass_text)
        self.assertIn("PlayResY: 1080", ass_text)
        self.assertIn(",&H802010FF&,", ass_text)

    # 8. autocut tail release + source_end backward snap guard
    def test_autocut_preserves_tail_release_and_prevents_mid_syllable_source_end(self) -> None:
        # 5 seconds of 16kHz 16-bit PCM: quiet gap + non-zero speech tone so zero crossings exist
        sr = 16000
        samples = [int(2000 * math.sin(2 * math.pi * 220 * i / sr)) for i in range(sr * 5)]
        pcm = struct.pack(f"<{len(samples)}h", *samples)

        words = [
            {"word": "today", "start": 0.40, "end": 0.98},
            {"word": "we", "start": 1.00, "end": 1.36},
            {"word": "we", "start": 1.40, "end": 1.80},
            {"word": "ship", "start": 1.90, "end": 2.30},
        ]
        proposals = analyze_waveform_and_transcript_autocuts(
            media_path=None,
            transcript_segments=[{"start": 0.0, "end": 4.0, "words": words}],
            clip_start=0.0,
            clip_end=4.0,
            raw_pcm_bytes=pcm,
        )
        stutters = [p for p in proposals if p["type"] == "false_start"]
        self.assertTrue(stutters)
        st = stutters[0]
        # Preceding kept word ("today" ends 0.98) keeps ~150ms release padding (1.13s +- zero-crossing window, > raw_start 1.00)
        self.assertGreaterEqual(st["source_start"], 1.10)
        # Cut end must NEVER back up past stutter tail (1.36s) mid-syllable
        self.assertGreaterEqual(st["source_end"], 1.36)


if __name__ == "__main__":
    unittest.main()
