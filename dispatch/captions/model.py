"""Canonical Caption Domain Model for Dispatch.
Standardized JSON-serializable domain representation following CapForge and OpenCut architecture.
Serves as the single source of truth across preview, editing, persistence, and export.
"""
from dataclasses import dataclass, field, asdict, fields
from typing import List, Dict, Any, Optional
import uuid


def _filter_known_fields(cls, data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract only declared dataclass fields to guarantee backwards/forwards compatibility without crashing."""
    if not isinstance(data, dict):
        return {}
    known = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in known and v is not None}


@dataclass
class WordStyleOverride:
    color: Optional[str] = None          # Hex e.g. '#FFD700'
    highlight_color: Optional[str] = None
    scale: Optional[float] = None        # Scale multiplier e.g. 1.15
    font_weight: Optional[str] = None   # 'bold', '900', etc.
    underline: Optional[bool] = None
    background_color: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WordStyleOverride":
        if not isinstance(data, dict):
            return cls()
        return cls(**_filter_known_fields(cls, data))


@dataclass
class Word:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    text: str = ""
    start: float = 0.0                   # Millisecond precision in seconds (e.g. 1.240)
    end: float = 0.0
    confidence: float = 1.0
    is_exact: bool = True
    style_override: Optional[WordStyleOverride] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if not self.style_override:
            d.pop("style_override", None)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Word":
        if not isinstance(data, dict):
            return cls()
        override = None
        if "style_override" in data and data["style_override"]:
            override = WordStyleOverride.from_dict(data["style_override"])
        try:
            start_val = float(data.get("start", 0.0))
        except (ValueError, TypeError):
            start_val = 0.0
        try:
            end_val = float(data.get("end", 0.0))
        except (ValueError, TypeError):
            end_val = 0.0
        try:
            conf_val = float(data.get("confidence", 1.0))
        except (ValueError, TypeError):
            conf_val = 1.0

        return cls(
            id=str(data.get("id", str(uuid.uuid4())[:8])),
            text=str(data.get("text", "")),
            start=start_val,
            end=end_val,
            confidence=conf_val,
            is_exact=bool(data.get("is_exact", True)),
            style_override=override
        )


@dataclass
class StyleConfig:
    font_family: str = "Impact"
    font_size: int = 54                  # Canonical base points for 1080p
    font_weight: str = "bold"
    font_style: str = "normal"           # 'normal', 'italic'
    underline: bool = False
    text_transform: str = "uppercase"    # 'uppercase', 'none', 'lowercase', 'title'
    letter_spacing: float = 0.5          # px
    line_height: float = 1.2
    
    # Colors (Standard Web Hex)
    primary_color: str = "#FFFFFF"       # Base inactive word color
    highlight_color: str = "#FFD700"     # Spoken active word color (Neon Yellow default)
    highlight_enabled: bool = True       # Spoken-word highlight toggle
    text_opacity: float = 1.0            # Independent text fill opacity [0.0 - 1.0]
    
    # Outline Stroke
    outline_enabled: bool = True
    outline_color: str = "#000000"
    outline_width: float = 4.5           # px for 1080p
    outline_opacity: float = 1.0         # Independent stroke opacity [0.0 - 1.0]
    
    # Drop Shadow
    shadow_enabled: bool = True
    shadow_color: str = "rgba(0,0,0,0.85)"
    shadow_offset_x: float = 2.0
    shadow_offset_y: float = 2.0
    shadow_blur: float = 4.0
    shadow_opacity: float = 1.0          # Independent shadow opacity [0.0 - 1.0]
    shadow_distance: float = 2.8         # Distance in px
    shadow_angle: float = 45.0           # Angle in degrees [0 - 360]
    
    # Opacity & Glow
    opacity: float = 1.0
    glow_enabled: bool = False
    glow_color: str = "#00F0FF"
    glow_blur: float = 16.0
    glow_opacity: float = 1.0            # Independent glow opacity [0.0 - 1.0]
    
    # Background Box Pill
    background_enabled: bool = False
    background_mode: str = "none"        # 'none', 'line', 'block'
    background_color: str = "rgba(0,0,0,0.8)"
    background_opacity: float = 1.0      # Independent background opacity [0.0 - 1.0]
    background_padding_x: float = 18.0
    background_padding_y: float = 8.0
    background_corner_radius: float = 12.0
    background_offset_x: float = 0.0
    background_offset_y: float = 0.0
    background_width: Optional[float] = None
    background_height: Optional[float] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StyleConfig":
        if not isinstance(data, dict):
            return cls()
        filtered = _filter_known_fields(cls, data)
        if "font_size" in filtered:
            try:
                filtered["font_size"] = int(filtered["font_size"])
            except (ValueError, TypeError):
                filtered.pop("font_size", None)
        for flt_field in (
            "letter_spacing", "line_height", "outline_width",
            "shadow_offset_x", "shadow_offset_y", "shadow_blur",
            "shadow_distance", "shadow_angle", "shadow_opacity",
            "opacity", "text_opacity", "outline_opacity",
            "glow_blur", "glow_opacity",
            "background_opacity", "background_padding_x",
            "background_padding_y", "background_corner_radius",
            "background_offset_x", "background_offset_y",
            "background_width", "background_height"
        ):
            if flt_field in filtered:
                try:
                    filtered[flt_field] = float(filtered[flt_field])
                except (ValueError, TypeError):
                    filtered.pop(flt_field, None)
        for bool_field in ("outline_enabled", "shadow_enabled", "glow_enabled", "background_enabled", "underline", "highlight_enabled"):
            if bool_field in filtered:
                filtered[bool_field] = bool(filtered[bool_field])
        return cls(**filtered)


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

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LayoutConfig":
        if not isinstance(data, dict):
            return cls()
        filtered = _filter_known_fields(cls, data)
        for flt_field in ("position_x", "position_y", "scale", "rotation", "max_width"):
            if flt_field in filtered:
                try:
                    filtered[flt_field] = float(filtered[flt_field])
                except (ValueError, TypeError):
                    filtered.pop(flt_field, None)
        if "max_lines" in filtered:
            try:
                filtered["max_lines"] = int(filtered["max_lines"])
            except (ValueError, TypeError):
                filtered.pop("max_lines", None)
        return cls(**filtered)


@dataclass
class AnimationConfig:
    type: str = "pop"                    # 'pop', 'karaoke', 'bounce', 'fade', 'none'
    duration_ms: int = 100               # Pop settle duration
    active_scale: float = 1.16           # Punch scale for active spoken word
    easing: str = "ease_out"
    
    # 3-layer animation definitions (flat representation)
    enter_type: str = "none"             # 'fade', 'pop', 'slide_up', 'none'
    enter_duration_ms: int = 150
    active_type: str = "pop"             # 'pop', 'karaoke', 'bounce', 'none'
    exit_type: str = "none"              # 'fade', 'scale_down', 'slide_down', 'none'
    exit_duration_ms: int = 150
    loop_type: str = "none"              # 'pulse', 'wiggle', 'none'
    loop_speed_ms: int = 1000

    # 3-layer animation definitions (nested objects from frontend)
    enter: Optional[Dict[str, Any]] = None
    active_word: Optional[Dict[str, Any]] = None
    exit: Optional[Dict[str, Any]] = None
    loop: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AnimationConfig":
        if not isinstance(data, dict):
            return cls()
        
        filtered = _filter_known_fields(cls, data)
        
        # Bi-directional parsing between nested and flat animation payloads
        if "enter" in data and isinstance(data["enter"], dict):
            filtered["enter"] = data["enter"]
            if "type" in data["enter"]:
                filtered["enter_type"] = str(data["enter"]["type"])
            if "duration_ms" in data["enter"]:
                try:
                    filtered["enter_duration_ms"] = int(data["enter"]["duration_ms"])
                except (ValueError, TypeError):
                    pass
        elif "enter_type" in data and not data.get("enter"):
            filtered["enter"] = {
                "type": str(data["enter_type"]),
                "duration_ms": int(data.get("enter_duration_ms", 150))
            }
        
        if "active_word" in data and isinstance(data["active_word"], dict):
            filtered["active_word"] = data["active_word"]
            if "type" in data["active_word"]:
                filtered["active_type"] = str(data["active_word"]["type"])
            if "active_scale" in data["active_word"]:
                try:
                    filtered["active_scale"] = float(data["active_word"]["active_scale"])
                except (ValueError, TypeError):
                    pass
        elif "active_type" in data and not data.get("active_word"):
            filtered["active_word"] = {
                "type": str(data["active_type"]),
                "active_scale": float(data.get("active_scale", 1.16)),
                "duration_ms": int(data.get("duration_ms", 100))
            }
            
        if "exit" in data and isinstance(data["exit"], dict):
            filtered["exit"] = data["exit"]
            if "type" in data["exit"]:
                filtered["exit_type"] = str(data["exit"]["type"])
            if "duration_ms" in data["exit"]:
                try:
                    filtered["exit_duration_ms"] = int(data["exit"]["duration_ms"])
                except (ValueError, TypeError):
                    pass
        elif "exit_type" in data and not data.get("exit"):
            filtered["exit"] = {
                "type": str(data["exit_type"]),
                "duration_ms": int(data.get("exit_duration_ms", 150))
            }

        if "loop" in data and isinstance(data["loop"], dict):
            filtered["loop"] = data["loop"]
            if "type" in data["loop"]:
                filtered["loop_type"] = str(data["loop"]["type"])
            if "speed_ms" in data["loop"]:
                try:
                    filtered["loop_speed_ms"] = int(data["loop"]["speed_ms"])
                except (ValueError, TypeError):
                    pass
        elif "loop_type" in data and not data.get("loop"):
            filtered["loop"] = {
                "type": str(data["loop_type"]),
                "speed_ms": int(data.get("loop_speed_ms", 1000))
            }
            
        return cls(**filtered)


@dataclass
class CaptionGroup:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    start: float = 0.0
    end: float = 0.0
    words: List[Word] = field(default_factory=list)
    layout: LayoutConfig = field(default_factory=LayoutConfig)
    style: StyleConfig = field(default_factory=StyleConfig)
    animation: AnimationConfig = field(default_factory=AnimationConfig)
    
    # Layer and media metadata
    trackIndex: int = 0
    type: str = "caption"
    volume: float = 1.0
    is_muted: bool = False
    audio_url: Optional[str] = None
    overlay_url: Optional[str] = None
    overlay_type: Optional[str] = None
    opacity: float = 1.0
    label: Optional[str] = None
    flip_h: bool = False
    flip_v: bool = False
    overlay_animation: Optional[Dict[str, Any]] = None
    has_word_timing: bool = True
    timing_mode: str = "word"            # 'word' | 'phrase'

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "words": [w.to_dict() for w in self.words],
            "layout": asdict(self.layout),
            "style": asdict(self.style),
            "animation": asdict(self.animation),
            "trackIndex": self.trackIndex,
            "type": self.type,
            "volume": self.volume,
            "is_muted": self.is_muted,
            "audio_url": self.audio_url,
            "overlay_url": self.overlay_url,
            "overlay_type": self.overlay_type,
            "opacity": self.opacity,
            "label": self.label,
            "flip_h": self.flip_h,
            "flip_v": self.flip_v,
            "overlay_animation": self.overlay_animation,
            "has_word_timing": self.has_word_timing,
            "timing_mode": self.timing_mode,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CaptionGroup":
        if not isinstance(data, dict):
            return cls()
        words = [Word.from_dict(w) for w in data.get("words", [])]
        layout = LayoutConfig.from_dict(data.get("layout", {}))
        style = StyleConfig.from_dict(data.get("style", {}))
        anim = AnimationConfig.from_dict(data.get("animation", {}))
        
        filtered = _filter_known_fields(cls, data)
        filtered["words"] = words
        filtered["layout"] = layout
        filtered["style"] = style
        filtered["animation"] = anim
        filtered["has_word_timing"] = bool(data.get("has_word_timing", True))
        filtered["timing_mode"] = str(data.get("timing_mode", "word"))
        try:
            filtered["start"] = float(data.get("start", 0.0))
        except (ValueError, TypeError):
            filtered["start"] = 0.0
        try:
            filtered["end"] = float(data.get("end", 0.0))
        except (ValueError, TypeError):
            filtered["end"] = 0.0

        return cls(**filtered)


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
        if not isinstance(data, dict):
            return cls()
        groups = [CaptionGroup.from_dict(g) for g in data.get("groups", [])]
        style = StyleConfig.from_dict(data.get("default_style", {}))
        layout = LayoutConfig.from_dict(data.get("default_layout", {}))
        anim = AnimationConfig.from_dict(data.get("default_animation", {}))
        return cls(
            id=str(data.get("id", str(uuid.uuid4())[:8])),
            clip_id=str(data.get("clip_id", "")),
            language=str(data.get("language", "en")),
            default_style=style,
            default_layout=layout,
            default_animation=anim,
            groups=groups
        )
