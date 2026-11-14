"""Phase 3 contracts: GET /api/clips/{id}/full-proxy (202 while generating, cached
Range/206 serving with X-Source-Duration) and POST /api/clips/{id}/transcribe-range
(PC transcript seeding, end<=0 = source-end semantics, 2-3 word caption cards)."""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from dispatch import db
import importlib
from fastapi.testclient import TestClient

from dispatch import db
from dispatch.web.app import app

web_app = importlib.import_module("dispatch.web.app")

client = TestClient(app)


def _seed_words(start: float, count: int, step: float = 0.5, length: float = 0.4):
    words = []
    for i in range(count):
        ws = round(start + i * step, 3)
        words.append({"word": f"w{i}", "start": ws, "end": round(ws + length, 3), "is_exact": True})
    return words


class FullProxyTestCase(unittest.TestCase):
    def setUp(self):
        db.init_db()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 4096)
        self.chunk_id = db.register_chunk(None, "source.mp4", str(self.source), f"hash-{id(self)}", None)
        self.clip_id = db.save_clip(
            session_id=None,
            chunk_id=self.chunk_id,
            start_time=10,
            end_time=34,
            title="Full proxy test",
            hook="test",
            description="",
            hashtags="#test",
            virality_score=50,
            layout_mode="fit_blur",
        )
        self._clear_cache()

    def tearDown(self):
        self._clear_cache()
        with db.get_db_connection() as conn:
            conn.execute("DELETE FROM clips WHERE id = ?", (self.clip_id,))
            conn.execute("DELETE FROM chunks WHERE id = ?", (self.chunk_id,))
        self.temp_dir.cleanup()

    def _clear_cache(self):
        web_app._FULL_PROXY_GENERATING.discard(self.clip_id)
        for suffix in (".mp4", ".meta.json", ".words.json"):
            Path(str(web_app._FULL_PROXY_DIR / f"{self.clip_id}{suffix}")).unlink(missing_ok=True)

    def _write_cache(self, source_size: int = None, duration: float = 24.0):
        web_app._FULL_PROXY_DIR.mkdir(parents=True, exist_ok=True)
        mp4 = web_app._FULL_PROXY_DIR / f"{self.clip_id}.mp4"
        mp4.write_bytes(b"proxypixel" * 100)
        meta = {
            "clip_id": self.clip_id,
            "duration": duration,
            "width": 1280,
            "height": 720,
            "source_mtime": int(self.source.stat().st_mtime),
            "source_size": int(self.source.stat().st_size if source_size is None else source_size),
        }
        (web_app._FULL_PROXY_DIR / f"{self.clip_id}.meta.json").write_text(json.dumps(meta), encoding="utf-8")

    def _source_patch(self):
        return patch("dispatch.web.app.resolve_clip_source_path", return_value=self.source)


class TestFullProxyEndpoint(FullProxyTestCase):
    def test_missing_clip_is_404(self):
        with self._source_patch():
            response = client.get("/api/clips/no-such-clip/full-proxy")
        self.assertEqual(response.status_code, 404)

    def test_unresolvable_source_is_404(self):
        with patch("dispatch.web.app.resolve_clip_source_path", return_value=None):
            response = client.get(f"/api/clips/{self.clip_id}/full-proxy")
        self.assertEqual(response.status_code, 404)

    def test_cache_miss_returns_202_and_kicks_off_generation(self):
        started = threading.Event()
        calls = []

        def fake_generate(clip_id, source_path):
            calls.append((clip_id, source_path))
            web_app._FULL_PROXY_GENERATING.discard(clip_id)
            started.set()

        with self._source_patch(), patch("dispatch.web.app._generate_full_proxy", side_effect=fake_generate):
            response = client.get(f"/api/clips/{self.clip_id}/full-proxy")
            self.assertTrue(started.wait(timeout=5.0), "generation thread never ran")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"status": "generating"})
        self.assertEqual(calls, [(self.clip_id, self.source.resolve())])

    def test_duplicate_requests_do_not_spawn_second_generation(self):
        release = threading.Event()

        def fake_generate(clip_id, source_path):
            release.wait(timeout=5.0)
            web_app._FULL_PROXY_GENERATING.discard(clip_id)

        try:
            with self._source_patch(), patch("dispatch.web.app._generate_full_proxy", side_effect=fake_generate):
                first = client.get(f"/api/clips/{self.clip_id}/full-proxy")
                second = client.get(f"/api/clips/{self.clip_id}/full-proxy")
        finally:
            release.set()
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)

    def test_cached_proxy_is_served_with_headers(self):
        self._write_cache()
        with self._source_patch():
            response = client.get(f"/api/clips/{self.clip_id}/full-proxy")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"].split(";")[0], "video/mp4")
        self.assertEqual(response.headers["accept-ranges"], "bytes")
        self.assertEqual(response.headers["x-source-duration"], "24.000")
        self.assertIn("etag", response.headers)
        self.assertEqual(response.content, b"proxypixel" * 100)

    def test_head_request_serves_headers_without_body(self):
        self._write_cache()
        with self._source_patch():
            response = client.head(f"/api/clips/{self.clip_id}/full-proxy")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-source-duration"], "24.000")
        self.assertEqual(response.content, b"")

    def test_range_request_returns_206_slice(self):
        self._write_cache()
        with self._source_patch():
            response = client.get(f"/api/clips/{self.clip_id}/full-proxy", headers={"range": "bytes=0-9"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.headers["content-range"].startswith("bytes 0-9/"), True)
        self.assertEqual(response.content, b"proxypixel")

    def test_conditional_get_returns_304(self):
        self._write_cache()
        with self._source_patch():
            first = client.get(f"/api/clips/{self.clip_id}/full-proxy")
            second = client.get(
                f"/api/clips/{self.clip_id}/full-proxy",
                headers={"if-none-match": first.headers["etag"]},
            )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 304)

    def test_stale_cache_triggers_regeneration(self):
        self._write_cache(source_size=int(self.source.stat().st_size) + 1)
        started = threading.Event()

        def fake_generate(clip_id, source_path):
            web_app._FULL_PROXY_GENERATING.discard(clip_id)
            started.set()

        with self._source_patch(), patch("dispatch.web.app._generate_full_proxy", side_effect=fake_generate):
            response = client.get(f"/api/clips/{self.clip_id}/full-proxy")
            self.assertTrue(started.wait(timeout=5.0))
        self.assertEqual(response.status_code, 202)

    def test_other_users_source_media_is_forbidden(self):
        self._write_cache()
        with db.get_db_connection() as conn:
            conn.execute("UPDATE clips SET user_id = 'owner-x' WHERE id = ?", (self.clip_id,))

        def caller_y(request, allow_lan_peer=False):
            return "caller-y"

        with self._source_patch(), patch("dispatch.web.app.resolve_caller_user_id", side_effect=caller_y):
            response = client.get(f"/api/clips/{self.clip_id}/full-proxy")
        self.assertEqual(response.status_code, 403)

        def caller_owner(request, allow_lan_peer=False):
            return "owner-x"

        with self._source_patch(), patch("dispatch.web.app.resolve_caller_user_id", side_effect=caller_owner):
            response = client.get(f"/api/clips/{self.clip_id}/full-proxy")
        self.assertEqual(response.status_code, 200)

    def test_traversal_clip_id_is_rejected(self):
        for evil in ("..%2Fescape", "..\\escape", "%2e%2e%2fescape"):
            response = client.get(f"/api/clips/{evil}/full-proxy")
            self.assertIn(response.status_code, (403, 404), evil)


class TestTranscribeRangeEndpoint(FullProxyTestCase):
    def _gap_transcriber_patch(self, gap_words=None, record=None, available=True):
        def factory(clip, effective_user_id):
            if not available:
                return None

            def gap_fn(gap_start, gap_end):
                if record is not None:
                    record.append((gap_start, gap_end))
                if gap_words is None:
                    raise RuntimeError("gap transcription failed")
                return gap_words

            return gap_fn

        return patch("dispatch.web.app._make_stage2_gap_transcriber", side_effect=factory)

    def _seed_transcript(self, words):
        db.save_transcript(
            self.chunk_id, None,
            " ".join(w["word"] for w in words),
            [{"start": words[0]["start"], "end": words[-1]["end"], "words": words}],
        )

    def _post(self, ranges):
        with self._source_patch(), patch("dispatch.web.app._probe_media_duration", return_value={"duration": 24.0}):
            return client.post(f"/api/clips/{self.clip_id}/transcribe-range", json={"ranges": ranges})

    def test_end_le_zero_means_source_end_and_merges_gap_words(self):
        self._seed_transcript(_seed_words(0.0, 10, length=0.5))  # covers 0..5.0
        gap_words = _seed_words(7.0, 10)  # covers 7.0..11.9
        requested = []
        with self._gap_transcriber_patch(gap_words=gap_words, record=requested):
            response = self._post([[0.0, -1.0]])

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["transcribed"], [[5.0, 24.0]])
        self.assertEqual(requested, [(5.0, 24.0)])
        self.assertEqual(body["word_count"], 20)
        self.assertEqual([w["start"] for w in body["words"]], sorted(w["start"] for w in body["words"]))
        self.assertTrue(all(len(g["words"]) <= 3 for g in body["groups"]))
        self.assertGreaterEqual(len(body["groups"]), 7)
        cached = json.loads((web_app._FULL_PROXY_DIR / f"{self.clip_id}.words.json").read_text(encoding="utf-8"))
        self.assertEqual(cached["word_count"] if "word_count" in cached else len(cached["words"]), 20)

    def test_fully_covered_range_never_pays_for_transcription(self):
        self._seed_transcript(_seed_words(0.0, 10, length=0.5))  # covers 0..5.0
        # Pre-seeded cache duplicates one DB word and adds one unique word
        cache_word = {"word": "cached", "start": 0.25, "end": 0.45, "is_exact": True}
        (web_app._FULL_PROXY_DIR / f"{self.clip_id}.words.json").write_text(
            json.dumps({"clip_id": self.clip_id, "words": [cache_word]}), encoding="utf-8")

        def must_not_be_called(gap_start, gap_end):
            raise AssertionError("paid transcription triggered for covered range")

        def factory(clip, effective_user_id):
            return must_not_be_called

        with patch("dispatch.web.app._make_stage2_gap_transcriber", side_effect=factory):
            response = self._post([[0.0, 4.8]])

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["transcribed"], [])
        self.assertEqual(body["word_count"], 11)  # 10 DB words + 1 unique cached word
        self.assertTrue(all(len(g["words"]) <= 3 for g in body["groups"]))

    def test_missing_transcriber_is_503(self):
        with self._gap_transcriber_patch(available=False):
            response = self._post([[0.0, 24.0]])
        self.assertEqual(response.status_code, 503)

    def test_gap_transcription_failure_does_not_500(self):
        with self._gap_transcriber_patch(gap_words=None):  # gap_fn raises
            response = self._post([[0.0, 24.0]])
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["transcribed"], [[0.0, 24.0]])
        self.assertEqual(body["word_count"], 0)
        self.assertEqual(body["groups"], [])

    def test_invalid_range_shape_is_400(self):
        response = self._post([[0.0]])
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
