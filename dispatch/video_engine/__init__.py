"""Video engine package for Dispatch."""
from dispatch.video_engine.subtitle_generator import generate_ass_subtitles, format_ass_timestamp
from dispatch.video_engine.reframer import build_filter_complex, escape_ffmpeg_filter_path
from dispatch.video_engine.renderer import render_clip
from dispatch.video_engine.thumbnail import (
    ensure_clip_thumbnail,
    resolve_clip_source_path,
    resolve_active_clip_video,
    generate_thumbnail_from_video,
    is_thumbnail_stale,
    get_file_fingerprint,
    get_thumbnail_version,
    get_thumbnail_version_token,
    populate_clip_media_urls
)

__all__ = [
    "generate_ass_subtitles",
    "format_ass_timestamp",
    "build_filter_complex",
    "escape_ffmpeg_filter_path",
    "render_clip",
    "ensure_clip_thumbnail",
    "resolve_clip_source_path",
    "resolve_active_clip_video",
    "generate_thumbnail_from_video",
    "is_thumbnail_stale",
    "get_file_fingerprint",
    "get_thumbnail_version",
    "get_thumbnail_version_token",
    "populate_clip_media_urls"
]
