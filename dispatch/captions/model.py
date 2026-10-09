"""Canonical Caption Domain Model for Dispatch.
Standardized JSON-serializable domain representation following CapForge and OpenCut architecture.
Serves as the single source of truth across preview, editing, persistence, and export.
"""
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
import uuid


@dataclass
class WordStyleOverride:
    color: Optional[str] = None          # Hex e.g. '#FFD700'
    highlight_color: Optional[str] = None
    scale: Optional[float] = None        # Scale multiplier e.g. 1.15
    font_weight: Optional[str] = None   # 'bold', '900', etc.
    underline: Optional[bool] = None
    background_color: Optional[str] = None


@dataclass
class Word:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    text: str = ""
    start: float = 0.0                   # Millisecond precision in seconds (e.g. 1.240)
    end: float = 0.0
    confidence: float = 1.0
    style_override: Optional[WordStyleOverride] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if not self.style_override:
            d.pop("style_override", None)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Word":
        override = None
        if "style_override" in data and data["style_override"]:
            override = WordStyleOverride(**data["style_override"])
        return cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            text=data.get("text", ""),
            start=float(data.get("start", 0.0)),
            end=float(data.get("end", 0.0)),
            confidence=float(data.get("confidence", 1.0)),
            style_override=override
        )


@dataclass
class StyleConfig:
    font_family: str = "Impact"
    font_size: int = 54                  # Canonical base points for 1080p
    font_weight: str = "bold"
    text_transform: str = "uppercase"    # 'uppercase', 'none', 'lowercase'
    letter_spacing: float = 0.5          # px
    line_height: float = 1.2
    
    # Colors (Standard Web Hex)
    primary_color: str = "#FFFFFF"       # Base inactive word color
    highlight_color: str = "#FFD700"     # Spoken active word color (Neon Yellow default)
    
    # Outline Stroke
    outline_enabled: bool = True
    outline_color: str = "#000000"
    outline_width: float = 4.5           # px for 1080p
    
    # Drop Shadow
    shadow_enabled: bool = True
    shadow_color: str = "rgba(0,0,0,0.85)"
    shadow_offset_x: float = 2.0
    shadow_offset_y: float = 2.0
    shadow_blur: float = 4.0
    
    # Opacity & Glow
    opacity: float = 1.0
    glow_enabled: bool = False
    glow_color: str = "#00F0FF"
    glow_blur: float = 16.0
    
    # Background Box Pill
    background_enabled: bool = False
    background_color: str = "rgba(0,0,0,0.8)"
    background_padding_x: float = 18.0
    background_padding_y: float = 8.0
    background_corner_radius: float = 12.0


@dataclass
class LayoutConfig:
    # Resolution-independent normalized coordinates [0.0 - 1.0]
    position_x: float = 0.5              # Center horizontally
    position_y: float = 0.78             # Lower-third Shorts safe zone (above platform buttons)
    scale: float = 1.0                   # Geometric transform scale
    rotation: float = 0.0                # Rotation in degrees
    alignment: str = "center"            # 'left', 'center', 'right'
    max_width: float = 0.88              # 88% of screen width max
    max_lines: int = 2


@dataclass
class AnimationConfig:
    type: str = "pop"                    # 'pop', 'karaoke', 'bounce', 'fade', 'none'
    duration_ms: int = 100               # Pop settle duration
    active_scale: float = 1.16           # Punch scale for active spoken word
    easing: str = "ease_out"
    
    # 3-layer animation definitions
    enter_type: str = "none"             # 'fade', 'pop', 'slide_up', 'none'
    enter_duration_ms: int = 150
    active_type: str = "pop"             # 'pop', 'karaoke', 'bounce', 'none'
    exit_type: str = "none"              # 'fade', 'scale_down', 'none'
    exit_duration_ms: int = 150


@dataclass
class CaptionGroup:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    start: float = 0.0
    end: float = 0.0
    words: List[Word] = field(default_factory=list)
    layout: LayoutConfig = field(default_factory=LayoutConfig)
    style: StyleConfig = field(default_factory=StyleConfig)
    animation: AnimationConfig = field(default_factory=AnimationConfig)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "words": [w.to_dict() for w in self.words],
            "layout": asdict(self.layout),
            "style": asdict(self.style),
            "animation": asdict(self.animation)
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CaptionGroup":
        words = [Word.from_dict(w) for w in data.get("words", [])]
        layout = LayoutConfig(**data.get("layout", {})) if "layout" in data else LayoutConfig()
        style = StyleConfig(**data.get("style", {})) if "style" in data else StyleConfig()
        anim = AnimationConfig(**data.get("animation", {})) if "animation" in data else AnimationConfig()
        return cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            start=float(data.get("start", 0.0)),
            end=float(data.get("end", 0.0)),
            words=words,
            layout=layout,
            style=style,
            animation=anim
        )


@dataclass
class CaptionTrack:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    clip_id: str = ""
    language: str = "en"
    groups: List[CaptionGroup] = field(default_factory=list)
    default_style: StyleConfig = field(default_factory=StyleConfig)
    default_layout: LayoutConfig = field(default_factory=LayoutConfig)
    default_animation: AnimationConfig = field(default_factory=AnimationConfig)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "clip_id": self.clip_id,
            "language": self.language,
            "default_style": asdict(self.default_style),
            "default_layout": asdict(self.default_layout),
            "default_animation": asdict(self.default_animation),
            "groups": [g.to_dict() for g in self.groups]
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CaptionTrack":
        groups = [CaptionGroup.from_dict(g) for g in data.get("groups", [])]
        style = StyleConfig(**data.get("default_style", {})) if "default_style" in data else StyleConfig()
        layout = LayoutConfig(**data.get("default_layout", {})) if "default_layout" in data else LayoutConfig()
        anim = AnimationConfig(**data.get("default_animation", {})) if "default_animation" in data else AnimationConfig()
        return cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            clip_id=data.get("clip_id", ""),
            language=data.get("language", "en"),
            default_style=style,
            default_layout=layout,
            default_animation=anim,
            groups=groups
        )
