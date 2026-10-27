"""Renderer caption-rendering contract (v1.5 §7).

Verifies the four caption states distinguishably:
1. Captions intentionally disabled -> valid caption-less render.
2. Captions required but unavailable -> hard render failure.
3. Captions required, generation fails -> hard render failure.
4. Captions exported but the ASS file is missing -> hard render failure.

Failure paths are asserted BEFORE any FFmpeg invocation so suites stay fast;
the disabled path performs a real (tiny) render to prove output validity.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dispatch.video_engine.renderer import render_clip
from dispatch.db import init_db


class TestRendererCaptionContract(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()

    def _stub_orientation(self):
        return patch("dispatch.ingestion.validator.inspect_video_orientation", return_value={
            "valid": True,
            "orientation": "landscape",
            "has_audio": True,
        })

    def test_intentionally_disabled_renders_without_captions(self):
        """Case 1: disabling subtitles is a VALID caption-less render."""
        from dispatch.db import get_clip_by_id, get_db_connection
        try:
            with tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "disabled_captions_src.mp4"
                # A 1s synthetic source rendered with ffmpeg (lavfi) keeps this
                # path a genuine end-to-end render.
                import subprocess
                subprocess.run([
                    "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x568:rate=10",
                    "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", str(source),
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

                clip_id = "render_disabled_captions"
                with get_db_connection() as conn:
                    conn.execute("""
                        INSERT OR REPLACE INTO clips (id, start_time, end_time, duration, title, hook, status)
                        VALUES (?, 0.0, 5.5, 5.5, 'Disabled Captions', 'h', 'ready_review')
                    """, (clip_id,))
                    conn.commit()

                result = render_clip(
                    source_video=source,
                    clip_id=clip_id,
                    start_time=0.0,
                    end_time=5.5,
                    aspect_ratio="16:9",
                    layout_mode="fit_blur",
                    segments=None,
                    subtitle_options={"enabled": False, "preserve_timing": True},
                )
                out = Path(result["video_path"])
                self.assertTrue(out.exists() and out.stat().st_size > 0)
        finally:
            with get_db_connection() as conn:
                conn.execute("DELETE FROM clips WHERE id = 'render_disabled_captions'")
                conn.commit()
            for f in Path("clips/rendered").glob("render_disabled_captions*"):
                f.unlink(missing_ok=True)

    def test_required_but_unavailable_captions_fail_loudly(self):
        """Case 3 (unavailable): enabled=True with no track saved and no
        transcript segments must abort the render, never emit silently
        caption-less output."""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "no_transcript_src.mp4"
            source.write_bytes(b"dummy")  # never reaches ffmpeg

            with self._stub_orientation():
                with self.assertRaisesRegex(RuntimeError, "[Cc]aptions are required"):
                    render_clip(
                        source_video=source,
                        clip_id="render_no_captions_anywhere",
                        start_time=0.0,
                        end_time=2.0,
                        aspect_ratio="16:9",
                        layout_mode="fit_blur",
                        segments=None,
                        subtitle_options={"enabled": True, "preserve_timing": True},
                    )

    def test_caption_generation_failure_fails_loudly(self):
        """Case 3 (generation): transcript present but grouping must not be
        swallowed into a caption-less render."""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "gen_failure_src.mp4"
            source.write_bytes(b"dummy")
            segments = [{"id": 0, "start": 0.0, "end": 2.0, "text": "hello world", "words": []}]

            with self._stub_orientation():
                with patch("dispatch.captions.group_words_into_captions", side_effect=ValueError("bad timing")):
                    with self.assertRaisesRegex(RuntimeError, "Caption generation failed"):
                        render_clip(
                            source_video=source,
                            clip_id="render_generation_failure",
                            start_time=0.0,
                            end_time=2.0,
                            aspect_ratio="16:9",
                            layout_mode="fit_blur",
                            segments=segments,
                            subtitle_options={"enabled": True, "preserve_timing": True},
                        )

    def test_missing_ass_file_after_successful_export_fails_loudly(self):
        """Case 4: serveability — an exported track whose ASS file cannot be
        found aborts the render instead of dropping subtitles."""
        import subprocess  # noqa: F401 (parity with sibling tests)
        from dispatch.captions import CaptionTrack

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "vanish_src.mp4"
            source.write_bytes(b"dummy")  # never reaches ffmpeg
            track = CaptionTrack(clip_id="render_ass_vanish")

            with self._stub_orientation():
                with patch("dispatch.captions.exporter.export_to_ass", return_value=None):
                    with self.assertRaisesRegex(RuntimeError, "[Cc]aption file is missing"):
                        render_clip(
                            source_video=source,
                            clip_id="render_ass_vanish",
                            start_time=0.0,
                            end_time=2.0,
                            aspect_ratio="16:9",
                            layout_mode="fit_blur",
                            subtitle_options={
                                "enabled": True,
                                "caption_track": track.to_dict(),
                                "preserve_timing": True,
                            },
                        )


if __name__ == "__main__":
    unittest.main()
