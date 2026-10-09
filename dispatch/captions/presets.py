"""Curated High-Impact Caption Style Presets for Dispatch.
Provides instant, professional styling mimicking CapCut, Hormozi, and MrBeast.
"""
from typing import Dict, Any
from dispatch.captions.model import StyleConfig, LayoutConfig, AnimationConfig


CAPTION_PRESETS: Dict[str, Dict[str, Any]] = {
    "yellow_pop": {
        "id": "yellow_pop",
        "name": "CapCut Yellow Pop",
        "description": "Bold Impact font, bright yellow active word with tactile punch bounce, heavy black stroke.",
        "style": StyleConfig(
            font_family="Impact",
            font_size=54,
            font_weight="bold",
            text_transform="uppercase",
            primary_color="#FFFFFF",
            highlight_color="#FFD700",
            outline_enabled=True,
            outline_color="#000000",
            outline_width=4.5,
            shadow_enabled=True,
            shadow_color="rgba(0,0,0,0.85)",
            shadow_offset_x=2.0,
            shadow_offset_y=2.0,
            shadow_blur=4.0,
            background_enabled=False
        ),
        "animation": AnimationConfig(
            type="pop",
            duration_ms=100,
            active_scale=1.18,
            easing="ease_out"
        ),
        "layout": LayoutConfig(
            position_x=0.5,
            position_y=0.78,
            alignment="center",
            max_width=0.88,
            max_lines=2
        )
    },
    
    "hormozi_green": {
        "id": "hormozi_green",
        "name": "Hormozi Emerald",
        "description": "High-energy uppercase text with neon emerald green highlights and heavy drop shadow.",
        "style": StyleConfig(
            font_family="Montserrat",
            font_size=52,
            font_weight="900",
            text_transform="uppercase",
            primary_color="#FFFFFF",
            highlight_color="#00FF66",
            outline_enabled=True,
            outline_color="#000000",
            outline_width=5.0,
            shadow_enabled=True,
            shadow_color="rgba(0,0,0,0.9)",
            shadow_offset_x=3.0,
            shadow_offset_y=3.0,
            shadow_blur=6.0,
            background_enabled=False
        ),
        "animation": AnimationConfig(
            type="pop",
            duration_ms=90,
            active_scale=1.16,
            easing="ease_out"
        ),
        "layout": LayoutConfig(
            position_x=0.5,
            position_y=0.78,
            alignment="center",
            max_width=0.88,
            max_lines=2
        )
    },

    "electric_cyan": {
        "id": "electric_cyan",
        "name": "Electric Cyan",
        "description": "Modern electric cyan active word pop with crisp contrast.",
        "style": StyleConfig(
            font_family="Arial Black",
            font_size=52,
            font_weight="900",
            text_transform="uppercase",
            primary_color="#FFFFFF",
            highlight_color="#00F0FF",
            outline_enabled=True,
            outline_color="#000000",
            outline_width=4.5,
            shadow_enabled=True,
            shadow_color="rgba(0,0,0,0.85)",
            shadow_offset_x=2.0,
            shadow_offset_y=2.0,
            shadow_blur=4.0,
            background_enabled=False
        ),
        "animation": AnimationConfig(
            type="pop",
            duration_ms=100,
            active_scale=1.16,
            easing="ease_out"
        ),
        "layout": LayoutConfig(
            position_x=0.5,
            position_y=0.78,
            alignment="center",
            max_width=0.88,
            max_lines=2
        )
    },

    "boxed_minimal": {
        "id": "boxed_minimal",
        "name": "Boxed Minimal",
        "description": "Rounded dark translucent pill background with warm gold spoken highlight.",
        "style": StyleConfig(
            font_family="Arial",
            font_size=46,
            font_weight="bold",
            text_transform="none",
            primary_color="#FFFFFF",
            highlight_color="#FFC107",
            outline_enabled=False,
            outline_width=0.0,
            shadow_enabled=False,
            background_enabled=True,
            background_color="rgba(0,0,0,0.82)",
            background_padding_x=16.0,
            background_padding_y=8.0,
            background_corner_radius=10.0
        ),
        "animation": AnimationConfig(
            type="karaoke",
            duration_ms=80,
            active_scale=1.05,
            easing="linear"
        ),
        "layout": LayoutConfig(
            position_x=0.5,
            position_y=0.80,
            alignment="center",
            max_width=0.85,
            max_lines=2
        )
    },

    "classic": {
        "id": "classic",
        "name": "Classic Studio",
        "description": "Timeless clean white typography with subtle drop shadow.",
        "style": StyleConfig(
            font_family="Arial",
            font_size=48,
            font_weight="bold",
            text_transform="none",
            primary_color="#FFFFFF",
            highlight_color="#60A5FA",
            outline_enabled=True,
            outline_color="#000000",
            outline_width=3.0,
            shadow_enabled=True,
            shadow_color="rgba(0,0,0,0.7)",
            shadow_offset_x=1.5,
            shadow_offset_y=1.5,
            shadow_blur=3.0,
            background_enabled=False
        ),
        "animation": AnimationConfig(
            type="fade",
            duration_ms=100,
            active_scale=1.0,
            easing="linear"
        ),
        "layout": LayoutConfig(
            position_x=0.5,
            position_y=0.78,
            alignment="center",
            max_width=0.88,
            max_lines=2
        )
    }
}


def get_preset(preset_id: str) -> Dict[str, Any]:
    """Retrieve preset by ID with fallback to 'yellow_pop'."""
    return CAPTION_PRESETS.get(preset_id, CAPTION_PRESETS["yellow_pop"])
