"""WebVTT subtitle parser and normalizer for YouTube transcripts.
Converts raw .vtt caption files downloaded from YouTube into structured segment
dictionaries with start/end timestamps and clean text, stripping VTT markup and deduplicating
rolling cues.
"""
import re
import html
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger("dispatch.youtube_inbox.vtt")

# Regex patterns for VTT parsing
TIMESTAMP_PATTERN = re.compile(
    r"(?:(\d{1,2}):)?(\d{2}):(\d{2})[.,](\d{3})\s+-->\s+(?:(\d{1,2}):)?(\d{2}):(\d{2})[.,](\d{3})"
)
WORD_TIME_PATTERN = re.compile(r"<(\d{2}):(\d{2}):(\d{2})[.,](\d{3})>([^<]*)")
TAG_STRIP_PATTERN = re.compile(r"<[^>]+>")


def parse_timestamp_to_seconds(hours: Optional[str], minutes: str, seconds: str, millis: str) -> float:
    """Convert timestamp components into total seconds as a float."""
    h = int(hours) if hours else 0
    m = int(minutes)
    s = int(seconds)
    ms = int(millis)
    return round(h * 3600 + m * 60 + s + ms / 1000.0, 3)


def clean_vtt_text(text: str) -> str:
    """Clean cue text by stripping tags, unescaping HTML entities, and normalizing spaces."""
    cleaned = TAG_STRIP_PATTERN.sub("", text)
    cleaned = html.unescape(cleaned)
    # Remove cue positioning artifacts
    cleaned = re.sub(r"align:\S+|position:\S+|line:\S+|size:\S+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def parse_inline_words(raw_text: str, cue_start: float, cue_end: float) -> List[Dict[str, Any]]:
    """Parse exact inline word timestamps like `<00:00:01.200><c> word</c>` if present."""
    pattern = re.compile(r"<(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})>")
    parts = pattern.split(raw_text)
    if len(parts) <= 1:
        return []

    time_points = [cue_start]
    texts = [parts[0]]
    i = 1
    while i < len(parts):
        h, m, s, ms = parts[i:i+4]
        t = int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0
        time_points.append(round(t, 3))
        texts.append(parts[i+4])
        i += 5
    time_points.append(cue_end)

    words = []
    for idx, txt in enumerate(texts):
        cleaned = clean_vtt_text(txt)
        if not cleaned:
            continue
        w_start = time_points[idx]
        w_end = max(w_start + 0.05, time_points[idx+1])
        tokens = cleaned.split()
        if not tokens:
            continue
        dt = max(0.01, w_end - w_start) / len(tokens)
        for sub_i, tok in enumerate(tokens):
            words.append({
                "word": tok,
                "start": round(w_start + sub_i * dt, 3),
                "end": round(w_start + (sub_i + 1) * dt, 3),
                "probability": 1.0,
                "is_exact": True
            })
    return words


def parse_vtt_content(vtt_content: str) -> List[Dict[str, Any]]:
    """Parse WebVTT string content into structured segment dictionaries.
    
    Returns:
        List of segments: [{"start": float, "end": float, "text": str, "words": [...]}]
    """
    lines = vtt_content.splitlines()
    raw_cues = []
    current_start = None
    current_end = None
    current_text_lines = []

    for line in lines:
        line_str = line.strip()
        if not line_str or line_str.startswith("WEBVTT") or line_str.startswith("NOTE") or line_str.startswith("Kind:") or line_str.startswith("Language:"):
            continue

        # Check for timestamp line
        match = TIMESTAMP_PATTERN.search(line_str)
        if match:
            # If we already had a cue being collected, save it
            if current_start is not None and current_text_lines:
                raw_text = " ".join(current_text_lines)
                cleaned = clean_vtt_text(raw_text)
                if cleaned:
                    inline_words = parse_inline_words(raw_text, current_start, current_end)
                    raw_cues.append({
                        "start": current_start,
                        "end": current_end,
                        "text": cleaned,
                        "raw": raw_text,
                        "words": inline_words
                    })
                current_text_lines = []

            h1, m1, s1, ms1, h2, m2, s2, ms2 = match.groups()
            current_start = parse_timestamp_to_seconds(h1, m1, s1, ms1)
            current_end = parse_timestamp_to_seconds(h2, m2, s2, ms2)
        elif current_start is not None:
            current_text_lines.append(line_str)

    # Flush the last cue
    if current_start is not None and current_text_lines:
        raw_text = " ".join(current_text_lines)
        cleaned = clean_vtt_text(raw_text)
        if cleaned:
            inline_words = parse_inline_words(raw_text, current_start, current_end)
            raw_cues.append({
                "start": current_start,
                "end": current_end,
                "text": cleaned,
                "raw": raw_text,
                "words": inline_words
            })

    if not raw_cues:
        return []

    # Deduplicate rolling captions (YouTube auto-captions frequently repeat the previous line)
    deduped_cues: List[Dict[str, Any]] = []
    last_text = ""
    for cue in raw_cues:
        text = cue["text"]
        # Skip if exact duplicate
        if text == last_text:
            continue

        # If cue starts with previous cue text, keep only new words
        if last_text and text.startswith(last_text):
            remainder = text[len(last_text):].strip()
            if remainder:
                prefix_word_count = len(last_text.split())
                cue_words = cue.get("words", [])
                if len(cue_words) > prefix_word_count:
                    rem_words = cue_words[prefix_word_count:]
                else:
                    rem_words = [w for w in cue_words if w.get("word") in remainder.split()]
                if rem_words:
                    start_time = rem_words[0]["start"]
                else:
                    # In rolling cues without inline word timestamps, the remainder was spoken
                    # after the previous cue's end. Avoid setting start_time to cue["start"]
                    # which introduces a multi-second constant timing offset.
                    prev_end = deduped_cues[-1]["end"] if deduped_cues else cue["start"]
                    start_time = min(cue["end"] - 0.1, max(cue["start"], prev_end))
                deduped_cues.append({
                    "start": start_time,
                    "end": cue["end"],
                    "text": remainder,
                    "words": rem_words
                })
                last_text = text
            continue

        deduped_cues.append({
            "start": cue["start"],
            "end": cue["end"],
            "text": text,
            "words": cue["words"]
        })
        last_text = text

    # Merge cues: only merge if cues contain word timestamps.
    # For phrase-only cues without inline word timestamps, keep authentic cue boundaries intact.
    has_any_inline_words = any(bool(c.get("words")) for c in deduped_cues)
    merged_segments: List[Dict[str, Any]] = []

    if has_any_inline_words:
        current_seg: Optional[Dict[str, Any]] = None
        for cue in deduped_cues:
            if current_seg is None:
                current_seg = {
                    "start": cue["start"],
                    "end": cue["end"],
                    "text": cue["text"],
                    "words": list(cue.get("words", []))
                }
                continue

            gap = cue["start"] - current_seg["end"]
            duration = current_seg["end"] - current_seg["start"]

            # Merge if gap is small and cumulative duration is under 5 seconds
            if gap < 1.5 and duration < 5.0 and not current_seg["text"].endswith((".", "?", "!")):
                current_seg["end"] = cue["end"]
                current_seg["text"] = f"{current_seg['text']} {cue['text']}".strip()
                current_seg["words"].extend(cue.get("words", []))
            else:
                merged_segments.append(current_seg)
                current_seg = {
                    "start": cue["start"],
                    "end": cue["end"],
                    "text": cue["text"],
                    "words": list(cue.get("words", []))
                }

        if current_seg is not None:
            merged_segments.append(current_seg)
    else:
        # Preserve authentic phrase boundaries directly from deduped cues
        for cue in deduped_cues:
            merged_segments.append({
                "start": cue["start"],
                "end": cue["end"],
                "text": cue["text"],
                "words": []
            })

    # Build word boundaries for segments: keep exact words if authentic; do NOT fabricate linear words!
    for seg in merged_segments:
        text_words = seg["text"].split()
        if not text_words:
            seg["words"] = []
            seg["has_word_timestamps"] = False
            continue

        existing_words = seg.get("words", [])
        # If text words have corresponding exact words from inline VTT tags, preserve them
        if existing_words and all(w.get("is_exact") for w in existing_words):
            seg["has_word_timestamps"] = True
            seg["words"] = existing_words
        else:
            # Phrase-level only: do NOT divide segment duration evenly across words.
            # Preserve honest phrase boundaries with is_exact=False.
            seg["has_word_timestamps"] = False
            word_objs = []
            for w in text_words:
                word_objs.append({
                    "word": w,
                    "start": seg["start"],
                    "end": seg["end"],
                    "probability": 1.0,
                    "is_exact": False
                })
            seg["words"] = word_objs

    logger.info("Parsed %d raw VTT cues into %d clean speech segments", len(raw_cues), len(merged_segments))
    return merged_segments


def parse_vtt_file(vtt_path: Path) -> List[Dict[str, Any]]:
    """Read and parse a .vtt subtitle file from disk."""
    if not vtt_path.exists():
        raise FileNotFoundError(f"VTT file not found: {vtt_path}")

    with open(vtt_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    return parse_vtt_content(content)
