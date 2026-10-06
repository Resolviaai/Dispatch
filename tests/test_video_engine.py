"""Unit test for video reframing, subtitle generation, and rendering."""
import subprocess
from pathlib import Path
from dispatch.config import PROCESSING_DIR, CLIPS_DIR
from dispatch.video_engine.subtitle_generator import generate_ass_subtitles
from dispatch.video_engine.reframer import build_filter_complex, escape_ffmpeg_filter_path
from dispatch.video_engine.renderer import render_clip
from dispatch.ingestion.validator import probe_video
from dispatch.db import init_db, save_clip

def test_video_engine():
    init_db()

    # 1. Generate 3-second 16:9 test video (1280x720) with audio
    test_src = PROCESSING_DIR / "unit_test_render_src.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=440",
        "-t", "3",
        "-c:v", "libx264",
        "-c:a", "aac",
        str(test_src)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    # 2. Mock transcript segments
    mock_segments = [
        {
            "id": 0, "start": 0.5, "end": 2.5,
            "text": "Yeh Dispatch automation test hai",
            "words": [
                {"word": "Yeh", "start": 0.5, "end": 0.8},
                {"word": "Dispatch", "start": 0.9, "end": 1.4},
                {"word": "automation", "start": 1.5, "end": 2.0},
                {"word": "test", "start": 2.1, "end": 2.4},
            ]
        }
    ]

    # Save mock clip in DB
    clip_id = save_clip(
        session_id=None,
        chunk_id=None,
        start_time=0.5,
        end_time=2.8,
        title="Yeh Dispatch automation test hai",
        hook="Opening test",
        description="Description",
        hashtags="#Test",
        virality_score=88,
        layout_mode="fit_blur"
    )

    # 3. Render clip
    render_result = render_clip(
        source_video=test_src,
        clip_id=clip_id,
        start_time=0.5,
        end_time=2.8,
        aspect_ratio="16:9",
        layout_mode="fit_blur",
        segments=mock_segments
    )

    out_video = render_result["video_path"]
    out_thumb = render_result["thumbnail_path"]

    assert out_video.exists(), f"Rendered video does not exist: {out_video}"
    assert out_thumb.exists(), f"Thumbnail does not exist: {out_thumb}"

    # 4. Probe rendered vertical video
    valid, meta, err = probe_video(out_video)
    assert valid, f"Rendered video is invalid: {err}"
    assert meta["width"] == 1080, f"Expected width 1080, got {meta['width']}"
    assert meta["height"] == 1920, f"Expected height 1920, got {meta['height']}"
    assert meta["aspect_ratio"] == "9:16", f"Expected 9:16, got {meta['aspect_ratio']}"

    # Clean up test artifacts
    test_src.unlink()
    out_video.unlink()
    out_thumb.unlink()

    print("ALL VIDEO ENGINE TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    test_video_engine()
