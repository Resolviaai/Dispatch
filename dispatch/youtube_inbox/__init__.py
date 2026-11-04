"""YouTube Cloud Inbox & Transport module for Dispatch.
Enables Phone -> YouTube (Private) -> Laptop Catcher workflow.
"""
from dispatch.youtube_inbox.vtt_parser import (
    parse_vtt_content,
    parse_vtt_file,
    clean_vtt_text,
    parse_timestamp_to_seconds
)
from dispatch.youtube_inbox.catcher import (
    YouTubeInboxCatcher,
    extract_youtube_video_id,
    scan_channel_for_dispatch_uploads
)
from dispatch.youtube_inbox.enricher import (
    enrich_youtube_video_metadata,
    merge_description,
    generate_ai_metadata,
)

# Convenience aliases
parse_vtt_to_segments = parse_vtt_file
YouTubeCatcher = YouTubeInboxCatcher

__all__ = [
    "parse_vtt_content",
    "parse_vtt_file",
    "parse_vtt_to_segments",
    "clean_vtt_text",
    "parse_timestamp_to_seconds",
    "YouTubeInboxCatcher",
    "YouTubeCatcher",
    "extract_youtube_video_id",
    "scan_channel_for_dispatch_uploads",
    "enrich_youtube_video_metadata",
    "merge_description",
    "generate_ai_metadata",
]
