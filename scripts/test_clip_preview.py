"""Interactive test script to preview clip quality and active-word subtitle rendering.
Takes an existing video (or default speech sample), transcribes with Whisper,
detects highlights, and renders a vertical 9:16 video with karaoke subtitles.
"""
import sys
import os
from pathlib import Path

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from dispatch.transcription.transcriber import transcribe_video
from dispatch.ai_clips.highlight_finder import extract_clips_gemini, extract_clips_local_heuristic
from dispatch.video_engine.renderer import render_clip
from dispatch.config import CLIPS_DIR, PROCESSING_DIR, GEMINI_API_KEY


def run_preview(video_path: Path):
    print("=" * 65)
    print("DISPATCH CLIP & SUBTITLE QUALITY PREVIEW")
    print("=" * 65)
    print(f"Input Video : {video_path}")
    print(f"Video Size  : {video_path.stat().st_size / (1024*1024):.2f} MB")
    
    # 1. Transcription
    print("\n[Step 1/3] Running Whisper Speech-to-Text (local int8 CPU multithreaded)...")
    trans_res = transcribe_video(video_path)
    segments = trans_res.get("segments", [])
    full_text = trans_res.get("full_text", "")
    print(f"   Detected Language : {trans_res.get('language')}")
    print(f"   Total Segments    : {len(segments)}")
    print(f"   Transcript Preview: \"{full_text[:120]}...\"")

    if not segments:
        print("[ERROR] No speech segments detected.")
        return

    # Total duration from last segment
    total_duration = segments[-1]["end"]

    # 2. Highlight Detection
    print("\n[Step 2/3] Evaluating Quality & Highlight Detection...")
    gemini_clips = None
    if GEMINI_API_KEY:
        print("   Attempting Gemini Cloud Analysis...")
        gemini_clips = extract_clips_gemini(full_text, segments)

    if gemini_clips:
        print("   [INFO] Highlight selected by Gemini Cloud AI")
        clips = gemini_clips
    else:
        print("   [INFO] Gemini API Key not active/set. Using Local Heuristic Engine...")
        clips = extract_clips_local_heuristic(segments, total_duration)

    if not clips:
        # Fallback to entire clip
        clips = [{
            "start_time": 0.0,
            "end_time": total_duration,
            "title": "Dispatch Preview Highlight",
            "virality_score": 85,
            "layout_recommendation": "fit_blur"
        }]

    clip = clips[0]
    start_t = clip["start_time"]
    end_t = clip["end_time"]
    title = clip.get("title", "Dispatch Preview Clip")
    virality = clip.get("virality_score", 80)
    layout = clip.get("layout_recommendation", "fit_blur")

    print(f"   Selected Clip : \"{title}\"")
    print(f"   Time Window   : {start_t:.1f}s -> {end_t:.1f}s (Duration: {end_t - start_t:.1f}s)")
    print(f"   Virality Score: {virality}/100")
    print(f"   Framing Mode  : {layout}")

    # 3. Video Rendering with Subtitles
    print("\n[Step 3/3] Rendering 9:16 Vertical Video with Karaoke Subtitles via FFmpeg...")
    clip_id = "test_render_preview"
    output = render_clip(
        source_video=video_path,
        clip_id=clip_id,
        start_time=start_t,
        end_time=end_t,
        aspect_ratio="16:9",
        layout_mode=layout,
        segments=segments
    )

    rendered_path = output["video_path"]
    thumb_path = output["thumbnail_path"]

    print("\n" + "=" * 65)
    print("PREVIEW RENDER COMPLETE!")
    print("=" * 65)
    print(f"Rendered Video : {rendered_path.resolve()}")
    if thumb_path:
        print(f"Thumbnail      : {thumb_path.resolve()}")
    print("\nTo view it immediately, open PowerShell and run:")
    print(f"   start \"{rendered_path.resolve()}\"")
    print("=" * 65)


if __name__ == "__main__":
    target = None
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
    else:
        default_video = ROOT_DIR / "storage" / "processing" / "phone_sync_chunk_speech.mp4"
        if default_video.exists():
            target = default_video

    if not target or not target.exists():
        print("Please provide a video file path: python scripts/test_clip_preview.py <path_to_video.mp4>")
        sys.exit(1)

    run_preview(target)
