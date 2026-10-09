"""Dispatch Canonical Caption & Subtitle Engine.
Following CapForge & OpenCut architecture with preview/export parity.
"""
from dispatch.captions.model import (
    Word,
    CaptionGroup,
    CaptionTrack,
    StyleConfig,
    LayoutConfig,
    AnimationConfig,
    WordStyleOverride
)
from dispatch.captions.grouper import group_words_into_captions
from dispatch.captions.presets import CAPTION_PRESETS, get_preset
from dispatch.captions.exporter import (
    export_to_ass,
    export_to_srt,
    export_to_json,
    hex_to_ass_color
)

__all__ = [
    "Word",
    "CaptionGroup",
    "CaptionTrack",
    "StyleConfig",
    "LayoutConfig",
    "AnimationConfig",
    "WordStyleOverride",
    "group_words_into_captions",
    "CAPTION_PRESETS",
    "get_preset",
    "export_to_ass",
    "export_to_srt",
    "export_to_json",
    "hex_to_ass_color"
]
