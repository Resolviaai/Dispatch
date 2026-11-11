"""AI Semantic highlight detector and short-form packaging engine.
Extracts high-retention clips (20-90s) with complete thoughts, hooks, and Roman Hinglish metadata.
Pillar 3 authority: strictly relies on Gemini API with structured candidate validation.
Never substitutes a silent local heuristic.
"""
import re
import os
import json
import logging
from typing import List, Dict, Any, Optional, Tuple
import requests
from pydantic import BaseModel, Field, ValidationError, model_validator

from dispatch.config import GEMINI_API_KEY, MIN_CLIP_DURATION, MAX_CLIP_DURATION
import math
from dispatch.ai_clips.prompt_templates import (
    HIGHLIGHT_SYSTEM_PROMPT,
    build_highlight_user_prompt,
    compute_optimal_clip_quota
)
from dispatch.ai_clips.preference_learner import get_negative_feedback_prompt
from dispatch.video_engine.silence import analyze_silence_gaps, find_natural_subsection_within_limit
from dispatch import db

logger = logging.getLogger("dispatch.ai_clips")

# Configurable default list of candidate Gemini models in priority order
DEFAULT_GEMINI_MODELS = [
    "gemini-flash-latest",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite"
]

MAX_HIGHLIGHTS_PER_CHUNK = int(os.getenv("DISPATCH_MAX_HIGHLIGHTS_PER_CHUNK", "8"))


class HighlightCandidate(BaseModel):
    """Pydantic model validating individual raw candidate highlights from Gemini."""
    start_time: float
    end_time: float
    title: str = Field(min_length=1)
    hook: Optional[str] = ""
    description: Optional[str] = ""
    hashtags: Optional[str] = "#Shorts #Reels"
    virality_score: int = Field(default=70, ge=1, le=100)
    layout_recommendation: str = Field(default="fit_blur")
    reason: Optional[str] = ""
    start_words: Optional[str] = None
    end_words: Optional[str] = None
    tier: Optional[str] = "A"
    hook_type: Optional[str] = "claim"
    archetype: Optional[str] = None
    arousal_state: Optional[str] = None
    ignition_quote: Optional[str] = None
    score_breakdown: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def validate_timestamps(self) -> "HighlightCandidate":
        if self.end_time <= self.start_time:
            raise ValueError(f"end_time ({self.end_time}) must be strictly greater than start_time ({self.start_time})")
        return self


def get_configured_gemini_models() -> List[str]:
    """Retrieve list of candidate models from env or config defaults."""
    env_models = os.getenv("DISPATCH_GEMINI_MODELS")
    if env_models:
        parsed = [m.strip() for m in env_models.split(",") if m.strip()]
        if parsed:
            return parsed
    return DEFAULT_GEMINI_MODELS


def snap_to_word_boundary(timestamp: float, segments: List[Dict[str, Any]], prefer_start: bool = True, max_tolerance: float = 2.0) -> float:
    """Snap a timestamp to the closest actual spoken word boundary in segments within tolerance."""
    if not segments:
        return round(timestamp, 2)

    best_time = timestamp
    min_diff = float("inf")

    for seg in segments:
        words = seg.get("words", [])
        if words:
            for w in words:
                target = w.get("start") if prefer_start else w.get("end")
                if target is not None:
                    diff = abs(target - timestamp)
                    if diff < min_diff:
                        min_diff = diff
                        best_time = target
        else:
            target = seg.get("start") if prefer_start else seg.get("end")
            if target is not None:
                diff = abs(target - timestamp)
                if diff < min_diff:
                    min_diff = diff
                    best_time = target

    if min_diff <= max_tolerance:
        return round(best_time, 2)
    return round(timestamp, 2)


def compute_interval_overlap(start1: float, end1: float, start2: float, end2: float) -> float:
    """Compute Intersection-over-Union (IoU) of two time intervals."""
    inter_start = max(start1, start2)
    inter_end = min(end1, end2)
    intersection = max(0.0, inter_end - inter_start)
    if intersection == 0.0:
        return 0.0

    union = (end1 - start1) + (end2 - start2) - intersection
    if union <= 0.0:
        return 0.0
    return intersection / union


def compute_containment_ratio(start1: float, end1: float, start2: float, end2: float) -> float:
    """Compute the overlap ratio relative to the shorter interval (containment)."""
    inter_start = max(start1, start2)
    inter_end = min(end1, end2)
    intersection = max(0.0, inter_end - inter_start)
    if intersection == 0.0:
        return 0.0
    min_dur = min(end1 - start1, end2 - start2)
    if min_dur <= 0.0:
        return 0.0
    return intersection / min_dur


def clean_speech_restarts_and_boundaries(
    start_t: float,
    end_t: float,
    segments: List[Dict[str, Any]],
    total_duration: float
) -> Tuple[float, float]:
    """Detect accidental repeated sentences, repeated phrases, and abandoned restarts throughout candidate clip.
    
    Invariants:
      1. Preserves intentional rhetorical repetition (e.g. immediate repeated words < 0.25s or rhythmic parallelism).
      2. If uncertain, retains the audio rather than making a destructive cut.
      3. For opening/early false starts (within candidate opening take), snaps start_t forward to the clean take (+0.15s margin).
      4. For trailing abandoned fragments/restarts near clip end, trims end_t back to the end of the clean thought (+0.20s margin).
      5. Keeps timestamps and subtitles strictly synchronized to speech.
    """
    if not segments or end_t <= start_t:
        return start_t, end_t

    # Flatten words within the candidate range plus a small margin
    clip_words = []
    for s in segments:
        for w in s.get("words", []):
            w_start = float(w.get("start", 0.0))
            if start_t - 0.2 <= w_start <= end_t + 0.5:
                clip_words.append(w)

    if len(clip_words) < 4:
        return start_t, end_t

    norm_words = [re.sub(r'[^\w\s]', '', w.get("word", "")).strip().lower() for w in clip_words]

    # 1. Detect opening or early false starts / repeated phrases
    # Look for matching phrases (2 to 8 words) starting within the first 30 seconds of the candidate
    restart_detected = False
    for n in (2, 3, 4, 5, 6, 7, 8):
        for i in range(min(12, len(clip_words) - n * 2)):
            phrase1 = norm_words[i:i + n]
            if not any(phrase1):
                continue
            for j in range(i + n, min(len(clip_words) - n + 1, i + n + 16)):
                phrase2 = norm_words[j:j + n]
                if phrase1 == phrase2:
                    t1_end = float(clip_words[i + n - 1].get("end", 0.0))
                    t2_start = float(clip_words[j].get("start", 0.0))
                    gap = t2_start - t1_end

                    # Intentional repetition: immediate repetition (< 0.25s gap) without hesitation is rhetorical
                    if gap < 0.25:
                        continue

                    # Accidental restart typically has 0.3s to 6.0s pause / hesitation
                    if 0.3 <= gap <= 6.0:
                        rem_duration = end_t - t2_start
                        if rem_duration >= MIN_CLIP_DURATION:
                            logger.info(
                                "Detected speech restart: '%s' (gap %.2fs) -> clean take at %.2fs. Snapping start.",
                                " ".join(phrase1), gap, t2_start
                            )
                            start_t = max(start_t, t2_start - 0.15)
                            restart_detected = True
                            break
            if restart_detected:
                break
        if restart_detected:
            break

    # 2. Detect trailing abandoned fragments near candidate end
    # If the speaker finished a thought and then started an abandoned fragment in the last 8 seconds
    trailing_words = [w for w in clip_words if float(w.get("start", 0.0)) >= max(start_t, end_t - 8.0)]
    if len(trailing_words) >= 2:
        for k in range(len(trailing_words) - 1):
            w_prev = trailing_words[k]
            w_next = trailing_words[k + 1]
            p_end = float(w_prev.get("end", 0.0))
            n_start = float(w_next.get("start", 0.0))
            pause_before_fragment = n_start - p_end

            # Noticeable pause (>= 0.8s) followed by a short abandoned fragment (1-3 words) left hanging
            frag_words_remaining = len(trailing_words) - (k + 1)
            if pause_before_fragment >= 0.8 and 1 <= frag_words_remaining <= 3:
                last_w_end = float(trailing_words[-1].get("end", 0.0))
                if end_t - last_w_end <= 1.5 and (p_end - start_t) >= MIN_CLIP_DURATION:
                    logger.info("Detected trailing abandoned fragment near end (pause %.2fs). Trimming end to %.2fs.",
                                pause_before_fragment, p_end + 0.20)
                    end_t = min(end_t, p_end + 0.20)
                    break

    return start_t, end_t


def detect_and_trim_opening_false_start(
    start_t: float,
    end_t: float,
    segments: List[Dict[str, Any]]
) -> float:
    """Backwards-compatible wrapper delegating to clean_speech_restarts_and_boundaries."""
    cleaned_start, _ = clean_speech_restarts_and_boundaries(start_t, end_t, segments, end_t)
    return cleaned_start

def match_quote_anchor(
    quote: Optional[str],
    segments: List[Dict[str, Any]],
    approx_time: float,
    prefer_start: bool = True,
    search_window: float = 25.0
) -> Optional[float]:
    """Find the exact word timestamp for a 3-8 word quote anchor within search_window of approx_time."""
    if not quote or not segments:
        return None

    tokens = [t for t in re.findall(r'\b\w+\b', quote.lower()) if t]
    if not tokens:
        return None

    window_words: List[Tuple[str, float, float]] = []
    for s in segments:
        for w in s.get("words", []):
            w_time = float(w.get("start" if prefer_start else "end", 0.0))
            if abs(w_time - approx_time) <= search_window:
                norm_w = re.sub(r'[^\w]', '', w.get("word", "")).lower()
                if norm_w:
                    window_words.append((norm_w, float(w.get("start", 0.0)), float(w.get("end", 0.0))))

    if len(window_words) < len(tokens):
        return None

    best_match_idx = -1
    best_match_score = 0
    k = len(tokens)

    for i in range(len(window_words) - k + 1):
        slice_words = [window_words[i + j][0] for j in range(k)]
        matches = sum(1 for a, b in zip(slice_words, tokens) if a == b)
        if matches > best_match_score and matches >= max(2, int(k * 0.6)):
            best_match_score = matches
            best_match_idx = i

    if best_match_idx >= 0:
        if prefer_start:
            return window_words[best_match_idx][1]
        else:
            return window_words[best_match_idx + k - 1][2]

    return None


FILLER_TOKENS = {
    "so", "um", "uh", "okay", "ok", "basically", "like", "actually",
    "right", "now", "well", "toh", "matlab", "dekho", "suno", "haan",
    "arre", "yaar", "aur", "and", "but", "ki"
}


def strip_leading_filler_words(
    start_t: float,
    end_t: float,
    segments: List[Dict[str, Any]]
) -> float:
    """Advance start_t past opening filler tokens to the core statement."""
    clip_words = []
    for s in segments:
        for w in s.get("words", []):
            w_start = float(w.get("start", 0.0))
            if start_t - 0.1 <= w_start <= start_t + 4.0:
                clip_words.append(w)

    if len(clip_words) < 4:
        return start_t

    new_start = start_t
    stripped_count = 0
    for w in clip_words:
        token = re.sub(r'[^\w]', '', w.get("word", "")).lower()
        if token in FILLER_TOKENS and stripped_count < 3:
            w_end = float(w.get("end", 0.0))
            if end_t - w_end >= MIN_CLIP_DURATION:
                new_start = w_end
                stripped_count += 1
            else:
                break
        else:
            if stripped_count > 0:
                w_start = float(w.get("start", 0.0))
                new_start = max(start_t, w_start - 0.10)
            break

    return new_start


CONJUNCTION_TOKENS = {"and", "but", "because", "so", "which", "or", "ki", "aur", "ya", "lekin", "magar", "kyunki"}


def trim_trailing_conjunctions(
    start_t: float,
    end_t: float,
    segments: List[Dict[str, Any]]
) -> float:
    """Trim end_t if the candidate ends mid-thought on a dangling conjunction."""
    trailing_words = []
    for s in segments:
        for w in s.get("words", []):
            w_end = float(w.get("end", 0.0))
            if end_t - 3.0 <= w_end <= end_t + 0.5:
                trailing_words.append(w)

    if len(trailing_words) < 2:
        return end_t

    last_w = trailing_words[-1]
    last_token = re.sub(r'[^\w]', '', last_w.get("word", "")).lower()
    if last_token in CONJUNCTION_TOKENS:
        prev_w = trailing_words[-2]
        prev_end = float(prev_w.get("end", 0.0))
        if prev_end - start_t >= MIN_CLIP_DURATION:
            logger.info("Trimmed dangling trailing conjunction '%s' at end of clip (%.2fs -> %.2fs)",
                        last_token, end_t, prev_end + 0.15)
            return prev_end + 0.15

    return end_t



SARCASM_RETRACTION_REGEX = re.compile(
    r"(?i)\b("
    r"just kidding|just joking|kidding|joking|mazak tha|mazaak tha|"
    r"mazak kar raha tha|obviously not|obviously don't|i'm joking|i am joking|"
    r"not actually|sarcasm tha|don't actually do this|i was joking"
    r")\b"
)


def apply_sarcasm_and_irony_lookahead(
    start_t: float,
    end_t: float,
    segments: List[Dict[str, Any]],
    total_duration: float,
    lookahead_seconds: float = 10.0
) -> Tuple[float, float, bool]:
    """Scan window immediately following candidate end_t for sarcasm/irony retraction markers.

    If a bold/outrageous statement is retracted ('just kidding', 'mazak tha'), we must either:
    1. Extend end_t to incorporate the retraction and its resolution clause (if <= MAX_CLIP_DURATION).
    2. Reject the candidate (return is_safe=False) if extending would exceed maximum allowable clip duration,
       preventing viral misinformation and misleading out-of-context clips.
    """
    if not segments:
        return start_t, end_t, True

    lookahead_end = min(total_duration, end_t + lookahead_seconds)
    subsequent_words = []
    for s in segments:
        for w in s.get("words", []):
            w_s = float(w.get("start", 0.0))
            if end_t <= w_s <= lookahead_end:
                subsequent_words.append(w)

    if not subsequent_words:
        lookahead_text = " ".join(
            s.get("text", "") for s in segments
            if float(s.get("start", 0.0)) >= end_t and float(s.get("start", 0.0)) <= lookahead_end
        )
        match = SARCASM_RETRACTION_REGEX.search(lookahead_text)
        if match:
            matching_segs = [
                s for s in segments
                if float(s.get("start", 0.0)) >= end_t and float(s.get("start", 0.0)) <= lookahead_end
            ]
            if matching_segs:
                new_end = min(total_duration, float(matching_segs[0].get("end", end_t)) + 0.25)
                if (new_end - start_t) <= MAX_CLIP_DURATION:
                    return start_t, new_end, True
                return start_t, end_t, False
        return start_t, end_t, True

    lookahead_text = " ".join(w.get("word", "") for w in subsequent_words)
    match = SARCASM_RETRACTION_REGEX.search(lookahead_text)
    if not match:
        return start_t, end_t, True

    cur_char = 0
    matched_word_idx = -1
    for idx, w in enumerate(subsequent_words):
        w_text = w.get("word", "")
        w_end_idx = cur_char + len(w_text)
        cur_char = w_end_idx + 1
        if w_end_idx >= match.end():
            matched_word_idx = idx
            break

    if matched_word_idx < 0:
        matched_word_idx = len(subsequent_words) - 1

    clause_end_idx = matched_word_idx
    for k in range(matched_word_idx, min(len(subsequent_words), matched_word_idx + 4)):
        clause_end_idx = k
        if subsequent_words[k].get("word", "").endswith((".", "!", "?")):
            break

    retraction_end_t = float(subsequent_words[clause_end_idx].get("end", end_t))
    extended_end = min(total_duration, round(retraction_end_t + 0.25, 2))

    if (extended_end - start_t) <= MAX_CLIP_DURATION:
        logger.info("Detected sarcasm/irony marker '%s' after %.2fs. Safely extended end to %.2fs.",
                    match.group(0), end_t, extended_end)
        return start_t, extended_end, True
    else:
        logger.info("Detected sarcasm/irony marker '%s', but extended duration (%.2fs) exceeds max %.1fs. Rejecting candidate.",
                    match.group(0), extended_end - start_t, MAX_CLIP_DURATION)
        return start_t, end_t, False


SPONSOR_PROMO_REGEX = re.compile(
    r"(?i)\b("
    r"sponsored by|promo code|coupon code|use code|discount code|"
    r"link in (the )?(description|bio)|affiliate link|patreon\.com|"
    r"support (us|me) on|check out the link below|click the link below|"
    r"smash that like button|subscribe to (the |my )?channel|"
    r"don't forget to like and subscribe|leave a comment down below|"
    r"merch link|buy my course|our sponsor today"
    r")\b"
)


def detect_sponsor_promo_contamination(
    start_t: float,
    end_t: float,
    segments: List[Dict[str, Any]]
) -> Tuple[float, float, bool]:
    """Detect sponsor pitches, promo codes, and subscribe asks in candidate clip.

    If located at edge, surgically trims. If contaminating core body or dominant, returns is_clean=False.
    """
    if not segments or end_t <= start_t:
        return start_t, end_t, True

    clip_words = []
    for s in segments:
        for w in s.get("words", []):
            w_s = float(w.get("start", 0.0))
            if start_t - 0.2 <= w_s <= end_t + 0.2:
                clip_words.append(w)

    full_clip_text = " ".join(w.get("word", "") for w in clip_words) if clip_words else " ".join(
        s.get("text", "") for s in segments if float(s.get("end", 0.0)) > start_t and float(s.get("start", 0.0)) < end_t
    )

    match = SPONSOR_PROMO_REGEX.search(full_clip_text)
    if not match:
        return start_t, end_t, True

    if clip_words:
        cur_char = 0
        p_start_time = None
        p_end_time = None
        for w in clip_words:
            w_text = w.get("word", "")
            w_end_idx = cur_char + len(w_text)
            if p_start_time is None and w_end_idx > match.start():
                p_start_time = float(w.get("start", start_t))
            if w_end_idx >= match.end():
                p_end_time = float(w.get("end", end_t))
                break
            cur_char = w_end_idx + 1

        p_start_time = p_start_time or start_t
        p_end_time = p_end_time or end_t

        if (p_start_time - start_t) <= 8.0:
            rem_dur = end_t - p_end_time
            if rem_dur >= MIN_CLIP_DURATION:
                logger.info("Trimming opening sponsor read '%s' (start %.2fs -> %.2fs)",
                            match.group(0), start_t, p_end_time + 0.15)
                return p_end_time + 0.15, end_t, True

        if (end_t - p_end_time) <= 8.0:
            rem_dur = p_start_time - start_t
            if rem_dur >= MIN_CLIP_DURATION:
                logger.info("Trimming trailing sponsor/subscribe ask '%s' (end %.2fs -> %.2fs)",
                            match.group(0), end_t, p_start_time - 0.15)
                return start_t, p_start_time - 0.15, True

    logger.info("Rejecting candidate contaminated with sponsor/promo read: '%s'", match.group(0))
    return start_t, end_t, False


LAUGHTER_REGEX = re.compile(r"(?i)(\[(laughter|laugh|applause)\]|\b(haha+|hehe+|chuckle|giggle|lol)\b)")


def apply_laughter_release_window(
    end_t: float,
    segments: List[Dict[str, Any]],
    total_duration: float,
    max_release_seconds: float = 2.0
) -> float:
    """Extend end_t by up to 2.0s when laughter or applause follows a comedic punchline.

    Ensures the comedic timing breathes naturally instead of cutting off mid-chuckle.
    """
    if not segments or end_t >= total_duration:
        return end_t

    search_end = min(total_duration, end_t + max_release_seconds + 0.5)
    last_reaction_end = end_t

    for s in segments:
        s_start = float(s.get("start", 0.0))
        s_end = float(s.get("end", 0.0))
        if end_t <= s_start <= search_end:
            s_text = s.get("text", "")
            if LAUGHTER_REGEX.search(s_text):
                last_reaction_end = max(last_reaction_end, min(s_end, end_t + max_release_seconds))
        for w in s.get("words", []):
            w_s = float(w.get("start", 0.0))
            w_e = float(w.get("end", 0.0))
            if end_t <= w_s <= search_end:
                if LAUGHTER_REGEX.search(w.get("word", "")):
                    last_reaction_end = max(last_reaction_end, min(w_e, end_t + max_release_seconds))

    if last_reaction_end > end_t:
        extended = min(total_duration, round(last_reaction_end + 0.20, 2))
        logger.info("Extended comedic punchline release window (%.2fs -> %.2fs) for laughter decay", end_t, extended)
        return extended

    return end_t

def snap_clip_to_stage2_words(
    target_start: float,
    target_end: float,
    stage2_words: List[Dict[str, Any]],
    padded_start: float,
    padded_end: float,
    max_snap_tolerance: float = 2.0
) -> Tuple[float, float]:
    """Snap candidate start and end to the closest stage2 precision words around target_start and target_end.

    Crucial fix for Bug #2: Does NOT use words[0] or words[-1] of the padded slice (which would pull in
    4 seconds of unrelated lead-in and lead-out speech). Instead, snaps strictly to the spoken words
    closest to target_start and target_end.
    """
    if not stage2_words:
        return target_start, target_end

    abs_words = []
    for w in stage2_words:
        w_s = padded_start + float(w.get("start", 0.0))
        w_e = padded_start + float(w.get("end", 0.0))
        abs_words.append((w_s, w_e, w.get("word", "")))

    if not abs_words:
        return target_start, target_end

    best_start = target_start
    min_start_diff = float("inf")
    for w_s, w_e, _ in abs_words:
        diff = abs(w_s - target_start)
        if diff < min_start_diff and diff <= max_snap_tolerance:
            min_start_diff = diff
            best_start = max(padded_start, round(w_s - 0.15, 2))

    best_end = target_end
    min_end_diff = float("inf")
    for w_s, w_e, _ in abs_words:
        diff = abs(w_e - target_end)
        if diff < min_end_diff and diff <= max_snap_tolerance:
            min_end_diff = diff
            best_end = min(padded_end, round(w_e + 0.25, 2))

    if best_end - best_start < MIN_CLIP_DURATION:
        return target_start, target_end

    return best_start, best_end


DEVANAGARI_REGEX = re.compile(r'[\u0900-\u097F]')


def has_devanagari(text: str) -> bool:
    """Check if string contains any Devanagari Hindi characters."""
    return bool(DEVANAGARI_REGEX.search(text))


_DEVANAGARI_MAP = {
    # Signs / Modifiers
    '\u0900': 'n',   # Inverted Chandrabindu
    '\u0901': 'n',   # Chandrabindu
    '\u0902': 'n',   # Anusvara
    '\u0903': 'h',   # Visarga

    # Independent vowels
    '\u0904': 'a',   # Short A
    '\u0905': 'a',
    '\u0906': 'aa',
    '\u0907': 'i',
    '\u0908': 'ee',
    '\u0909': 'u',
    '\u090a': 'oo',
    '\u090b': 'ri',
    '\u090c': 'li',
    '\u090d': 'e',   # Chandra E
    '\u090e': 'e',   # Short E
    '\u090f': 'e',
    '\u0910': 'ai',
    '\u0911': 'o',   # Chandra O (ऑ)
    '\u0912': 'o',   # Short O
    '\u0913': 'o',
    '\u0914': 'au',

    # Consonants
    '\u0915': 'k', '\u0916': 'kh', '\u0917': 'g', '\u0918': 'gh', '\u0919': 'ng',
    '\u091a': 'ch', '\u091b': 'chh', '\u091c': 'j', '\u091d': 'jh', '\u091e': 'ny',
    '\u091f': 't', '\u0920': 'th', '\u0921': 'd', '\u0922': 'dh', '\u0923': 'n',
    '\u0924': 't', '\u0925': 'th', '\u0926': 'd', '\u0927': 'dh', '\u0928': 'n',
    '\u0929': 'n',   # NNNA
    '\u092a': 'p', '\u092b': 'ph', '\u092c': 'b', '\u092d': 'bh', '\u092e': 'm',
    '\u092f': 'y', '\u0930': 'r', '\u0931': 'r',   # RRA
    '\u0932': 'l', '\u0933': 'l', '\u0934': 'l',   # LLA, LLLA
    '\u0935': 'v', '\u0936': 'sh', '\u0937': 'sh', '\u0938': 's', '\u0939': 'h',

    # Additional signs & vowels
    '\u093a': 'oe',
    '\u093b': 'ooe',
    '\u093c': '',    # Nukta (standalone fallback)
    '\u093d': "'",   # Avagraha (ऽ)

    # Dependent vowel signs (matras)
    '\u093e': 'aa', '\u093f': 'i', '\u0940': 'ee', '\u0941': 'u', '\u0942': 'oo',
    '\u0943': 'ri', '\u0944': 'ri', '\u0945': 'e', '\u0946': 'e', '\u0947': 'e',
    '\u0948': 'ai', '\u0949': 'o', '\u094a': 'o', '\u094b': 'o', '\u094c': 'au',
    '\u094e': 'e', '\u094f': 'aw',

    # Om & Accents
    '\u0950': 'Om',
    '\u0951': '', '\u0952': '', '\u0953': '', '\u0954': '',
    '\u0955': 'e', '\u0956': 'u', '\u0957': 'oo',

    # Nukta consonants
    '\u0958': 'q', '\u0959': 'kh', '\u095a': 'g', '\u095b': 'z', '\u095c': 'r',
    '\u095d': 'rh', '\u095e': 'f', '\u095f': 'y',

    # Additional Sanskrit vowels
    '\u0960': 'ri', '\u0961': 'li', '\u0962': 'li', '\u0963': 'li',

    # Danda & Punctuation
    '\u0964': '.', '\u0965': '.',

    # Digits (०-९)
    '\u0966': '0', '\u0967': '1', '\u0968': '2', '\u0969': '3', '\u096a': '4',
    '\u096b': '5', '\u096c': '6', '\u096d': '7', '\u096e': '8', '\u096f': '9',

    # Devanagari extensions
    '\u0970': '.', '\u0971': '', '\u0972': 'a', '\u0973': 'oe', '\u0974': 'ooe',
    '\u0975': 'aw', '\u0976': 'ue', '\u0977': 'uue', '\u0978': 'd', '\u0979': 'z',
    '\u097a': 'y', '\u097b': 'g', '\u097c': 'j', '\u097d': '', '\u097e': 'd',
    '\u097f': 'b',
}

_NUKTA_REPLACE = {
    '\u0915\u093c': '\u0958',  # क़ -> q
    '\u0916\u093c': '\u0959',  # ख़ -> kh
    '\u0917\u093c': '\u095a',  # ग़ -> g
    '\u091c\u093c': '\u095b',  # ज़ -> z
    '\u0921\u093c': '\u095c',  # ड़ -> r
    '\u0922\u093c': '\u095d',  # ढ़ -> rh
    '\u0928\u093c': '\u0929',  # ऩ -> n
    '\u092b\u093c': '\u095e',  # फ़ -> f
    '\u092f\u093c': '\u095f',  # य़ -> y
    '\u0930\u093c': '\u0931',  # ऱ -> r
    '\u0933\u093c': '\u0934',  # ऴ -> l
}

_HALANT = '\u094d'
_CONSONANTS = set(
    '\u0915\u0916\u0917\u0918\u0919'
    '\u091a\u091b\u091c\u091d\u091e'
    '\u091f\u0920\u0921\u0922\u0923'
    '\u0924\u0925\u0926\u0927\u0928\u0929'
    '\u092a\u092b\u092c\u092d\u092e'
    '\u092f\u0930\u0931\u0932\u0933\u0934\u0935\u0936\u0937\u0938\u0939'
    '\u0958\u0959\u095a\u095b\u095c\u095d\u095e\u095f'
    '\u0978\u0979\u097a\u097b\u097c\u097e\u097f'
)
_MATRAS = set(
    '\u093e\u093f\u0940\u0941\u0942\u0943\u0944\u0945\u0946\u0947\u0948\u0949\u094a\u094b\u094c'
    '\u094e\u094f\u0955\u0956\u0957\u0962\u0963'
)
_WORD_DELIMITERS = set('\u0964\u0965.,!?;:\'\"()[]{}-_/\r\n\t')


def phonetic_devanagari_to_roman(text: str) -> str:
    """Deterministic phonetic transliteration of Devanagari Hindi into readable Roman Hinglish.
    Exhaustively handles all Devanagari codepoints (U+0900..U+097F) with zero unmapped characters."""
    if not text:
        return ""

    # Normalize decomposed nukta sequences
    for comp, precomp in _NUKTA_REPLACE.items():
        text = text.replace(comp, precomp)
    # Strip any remaining standalone nuktas
    text = text.replace('\u093c', '')

    result = []
    chars = list(text)
    n = len(chars)
    for i, ch in enumerate(chars):
        if ch in _CONSONANTS:
            base = _DEVANAGARI_MAP.get(ch, '')
            next_ch = chars[i + 1] if i + 1 < n else None
            if next_ch == _HALANT:
                result.append(base)
            elif next_ch in _MATRAS:
                result.append(base)
            elif next_ch and next_ch in _CONSONANTS:
                result.append(base + 'a')
            elif next_ch in ('\u0901', '\u0902'):
                result.append(base + 'a')
            elif next_ch is None or next_ch.isspace() or next_ch in _WORD_DELIMITERS:
                result.append(base)
            else:
                result.append(base + 'a')
        elif ch in _DEVANAGARI_MAP:
            result.append(_DEVANAGARI_MAP[ch])
        elif ch == _HALANT:
            pass
        elif 0x0900 <= ord(ch) <= 0x097F:
            # Fallback guarantee: never leak any unmapped Devanagari characters
            pass
        else:
            result.append(ch)
    return "".join(result)


def transliterate_tokens_to_hinglish(
    tokens: List[str],
    timeout: int = 25,
    api_key: Optional[str] = None,
    user_id: Optional[str] = None,
) -> List[str]:
    """Transliterate a list of Devanagari tokens into Roman Hinglish with 1:1 token preservation.
    Uses Gemini when available, with deterministic phonetic fallback if offline or failed.
    Preserves exact list length to ensure word timestamp alignment."""
    if not tokens or not any(has_devanagari(t) for t in tokens):
        return tokens

    prompt = (
        "You are an expert Hindi-to-Roman Hinglish transliterator.\n"
        "Transliterate the following words written in Devanagari Hindi into natural, modern Roman Hinglish "
        "(e.g., 'नमस्ते' -> 'Namaste', 'आप' -> 'Aap', 'कैसे' -> 'kaise', 'हैं' -> 'hain').\n"
        "Leave English words or numbers untouched.\n"
        f"CRITICAL REQUIREMENT: You MUST return a JSON array of strings containing EXACTLY {len(tokens)} items, "
        "matching each input token 1-to-1 in order. Do not merge, split, omit, or translate to English.\n\n"
        f"Input tokens:\n{json.dumps(tokens, ensure_ascii=False)}"
    )

    try:
        eff_key = api_key
        if not eff_key and user_id and str(user_id).strip():
            from dispatch.transcription.gemini_engine import resolve_gemini_api_key
            eff_key = resolve_gemini_api_key(str(user_id), raise_if_missing_host_key=True)
        raw_result, winning_model = call_gemini_generate_content(
            user_prompt=prompt,
            system_instruction="You are a strict 1-to-1 Devanagari-to-Roman Hinglish transliterator. Return only a JSON array of strings with matching length.",
            api_key=eff_key,
            timeout=timeout
        )

        # Structured-output validation
        candidate_list: Optional[List[Any]] = None
        if isinstance(raw_result, list):
            candidate_list = raw_result
        elif isinstance(raw_result, dict):
            for k in ("tokens", "words", "transliterated", "result", "clips", "highlights"):
                if k in raw_result and isinstance(raw_result[k], list):
                    candidate_list = raw_result[k]
                    break

        if candidate_list is not None and len(candidate_list) == len(tokens):
            return [phonetic_devanagari_to_roman(str(t)) if has_devanagari(str(t)) else str(t) for t in candidate_list]

        logger.warning(
            "Gemini model '%s' transliteration returned unexpected structure or count (%s, expected %d). Using phonetic fallback.",
            winning_model, len(candidate_list) if candidate_list is not None else type(raw_result).__name__, len(tokens)
        )
    except Exception as e:
        logger.warning("Gemini transliteration failed across configured models (%s). Using phonetic fallback.", e)

    # Deterministic phonetic fallback preserves font rendering safety without missing glyph tofu boxes
    return [phonetic_devanagari_to_roman(t) if has_devanagari(t) else t for t in tokens]


def transliterate_segments_to_hinglish(
    segments: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Transliterate segments and words containing Devanagari into Roman Hinglish with 1:1 timestamp preservation."""
    if not segments:
        return []

    # Check if there is any Devanagari anywhere
    contains_hindi = False
    for s in segments:
        if has_devanagari(s.get("text", "")):
            contains_hindi = True
            break
        for w in s.get("words", []):
            if has_devanagari(w.get("word", "")):
                contains_hindi = True
                break
        if contains_hindi:
            break

    if not contains_hindi:
        return segments

    import copy
    output_segments = copy.deepcopy(segments)

    # 1. Check if word-level timestamps are present
    has_words = any(bool(s.get("words")) for s in output_segments)
    if has_words:
        all_words_meta = []
        token_list = []
        for s_idx, s in enumerate(output_segments):
            for w_idx, w in enumerate(s.get("words", [])):
                all_words_meta.append((s_idx, w_idx))
                token_list.append(w.get("word", ""))

        if token_list and any(has_devanagari(t) for t in token_list):
            transliterated = transliterate_tokens_to_hinglish(token_list, api_key=api_key, user_id=user_id)
            for (s_idx, w_idx), new_word in zip(all_words_meta, transliterated):
                output_segments[s_idx]["words"][w_idx]["word"] = new_word
            for s in output_segments:
                if s.get("words"):
                    s["text"] = " ".join(w["word"] for w in s["words"])
    else:
        # Segment-level text transliteration
        seg_texts = [s.get("text", "") for s in output_segments]
        if any(has_devanagari(t) for t in seg_texts):
            transliterated_texts = transliterate_tokens_to_hinglish(seg_texts, api_key=api_key, user_id=user_id)
            for s, new_text in zip(output_segments, transliterated_texts):
                s["text"] = new_text

    return output_segments


def expand_short_candidate_with_context(
    start_t: float,
    end_t: float,
    segments: List[Dict[str, Any]],
    total_duration: float,
    min_duration: float = MIN_CLIP_DURATION,
    max_duration: float = MAX_CLIP_DURATION
) -> Optional[Tuple[float, float]]:
    """Recover a valuable moment that is shorter than min_duration (e.g. 3-4s punchline).

    Invariants:
      1. Preserves meaning, setup, and resolution by expanding to preceding/succeeding sentences.
      2. Respects natural sentence boundaries.
      3. Never pads with dead silence.
      4. After expansion and silence evaluation, result must be in [min_duration, max_duration].
      5. Returns (expanded_start, expanded_end) or None if no worthwhile clip can be formed.
    """
    if not segments or end_t <= start_t:
        return None

    # Find the indices of segments intersecting or nearest to [start_t, end_t]
    matching_indices = [
        i for i, s in enumerate(segments)
        if float(s.get("end", 0.0)) > start_t and float(s.get("start", 0.0)) < end_t
    ]

    if not matching_indices:
        closest_idx = min(
            range(len(segments)),
            key=lambda i: min(abs(float(segments[i].get("start", 0.0)) - start_t),
                              abs(float(segments[i].get("end", 0.0)) - end_t))
        )
        matching_indices = [closest_idx]

    first_idx = matching_indices[0]
    last_idx = matching_indices[-1]

    cand_s = start_t
    cand_e = end_t

    # 1. Expand backwards to include preceding setup / context
    cur_first = first_idx
    while cur_first > 0 and (cand_e - cand_s) < min_duration:
        cur_first -= 1
        prev_seg = segments[cur_first]
        prev_s = float(prev_seg.get("start", 0.0))
        gap = cand_s - float(prev_seg.get("end", 0.0))
        if gap > 4.0:
            break
        cand_s = prev_s

    # 2. Expand forward to include reaction / conclusion if still < min_duration
    cur_last = last_idx
    while cur_last < len(segments) - 1 and (cand_e - cand_s) < min_duration:
        cur_last += 1
        nxt_seg = segments[cur_last]
        nxt_e = float(nxt_seg.get("end", 0.0))
        gap = float(nxt_seg.get("start", 0.0)) - cand_e
        if gap > 4.0:
            break
        cand_e = nxt_e

    # Snap to clean word boundaries
    snapped_s = snap_to_word_boundary(cand_s, segments, prefer_start=True)
    snapped_e = snap_to_word_boundary(cand_e, segments, prefer_start=False)
    final_s = max(0.0, snapped_s - 0.15)
    final_e = min(total_duration, snapped_e + 0.20)

    eff_dur, _ = analyze_silence_gaps(final_s, final_e, segments)
    if min_duration <= eff_dur <= max_duration:
        return final_s, final_e

    return None


def validate_and_filter_candidates(
    raw_candidates: List[Dict[str, Any]],
    segments: List[Dict[str, Any]],
    total_duration: float,
    max_clips: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Validate, snap, clamp, filter, and deduplicate candidate clips.
    
    Rules:
      1. Schema validation via Pydantic HighlightCandidate.
      2. Timestamp clamping to [0.0, total_duration].
      3. Word boundary snapping with false start detection & padding margins.
      4. Short moment recovery (< 5.0s): expands context backwards/forwards to reach >= 5.0s.
      5. Silence removal for long candidates (> 180.0s): evaluates dead-air removal. If speech
         fits <= 180.0s, qualifies. If still > 180.0s, finds natural complete subsection.
      6. Strict duration constraint: MIN_CLIP_DURATION (5.0s) <= duration <= MAX_CLIP_DURATION (180.0s).
      7. Deduplication: Suppresses heavy overlapping clips (IoU > 0.5), keeping higher virality,
         while preserving distinct nearby candidates.
      8. Capped at max_clips (highest virality first).
    """
    valid_candidates: List[Dict[str, Any]] = []

    for idx, raw in enumerate(raw_candidates):
        try:
            cand = HighlightCandidate.model_validate(raw)
        except ValidationError as ve:
            logger.warning("Rejecting malformed Gemini highlight candidate #%d: %s", idx, ve)
            continue

        start_t = float(cand.start_time)
        end_t = float(cand.end_time)

        # Clamping
        start_t = max(0.0, min(total_duration, start_t))
        end_t = max(0.0, min(total_duration, end_t))

        # Quote anchor snapping (millisecond accuracy based on exact spoken words)
        anchor_s_text = cand.start_words or cand.ignition_quote
        if segments and anchor_s_text:
            matched_s = match_quote_anchor(anchor_s_text, segments, start_t, prefer_start=True)
            if matched_s is not None:
                start_t = matched_s

        if segments and cand.end_words:
            matched_e = match_quote_anchor(cand.end_words, segments, end_t, prefer_start=False)
            if matched_e is not None:
                end_t = matched_e

        # Sponsor & housekeeping contamination filter
        if segments:
            start_t, end_t, is_clean = detect_sponsor_promo_contamination(start_t, end_t, segments)
            if not is_clean:
                continue

        # Sarcasm and irony lookahead safeguard
        if segments:
            start_t, end_t, is_safe = apply_sarcasm_and_irony_lookahead(start_t, end_t, segments, total_duration)
            if not is_safe:
                continue

        # Clean speech restarts and false starts throughout candidate
        if segments:
            start_t, end_t = clean_speech_restarts_and_boundaries(start_t, end_t, segments, total_duration)

        # Strip opening filler tokens (so, basically, um, toh, matlab)
        if segments:
            start_t = strip_leading_filler_words(start_t, end_t, segments)

        # Trim dangling trailing conjunctions (and, but, because, so, kyunki)
        if segments:
            end_t = trim_trailing_conjunctions(start_t, end_t, segments)

        # Laughter release window for comedic punchlines
        if segments:
            end_t = apply_laughter_release_window(end_t, segments, total_duration)

        # Boundary snapping with safe audio padding margin (0.15s start, 0.20s end)
        if segments:
            snapped_s = snap_to_word_boundary(start_t, segments, prefer_start=True)
            snapped_e = snap_to_word_boundary(end_t, segments, prefer_start=False)
            start_t = max(0.0, snapped_s - 0.15)
            end_t = min(total_duration, snapped_e + 0.20)
            if (end_t - start_t) > MAX_CLIP_DURATION and (float(cand.end_time) - float(cand.start_time)) <= MAX_CLIP_DURATION:
                end_t = min(total_duration, start_t + MAX_CLIP_DURATION)

        raw_duration = round(end_t - start_t, 2)

        # 1. Handle short candidate recovery (< 5.0s)
        if raw_duration < MIN_CLIP_DURATION:
            expanded = expand_short_candidate_with_context(start_t, end_t, segments, total_duration)
            if expanded:
                start_t, end_t = expanded
                logger.info("Expanded short candidate '%s' (%.2fs -> %.2fs) with surrounding context",
                            cand.title, raw_duration, end_t - start_t)
            else:
                logger.info("Rejecting candidate '%s' (duration %.2fs < min %.2fs and cannot be naturally expanded)",
                            cand.title, raw_duration, MIN_CLIP_DURATION)
                continue

        # 2. Handle long candidate & dead-air silence removal (> 180.0s)
        eff_dur, cuts = analyze_silence_gaps(start_t, end_t, segments)
        if eff_dur > MAX_CLIP_DURATION:
            # Check for a natural complete subsection within limits
            sub = find_natural_subsection_within_limit(start_t, end_t, segments, MAX_CLIP_DURATION, MIN_CLIP_DURATION)
            if sub:
                start_t, end_t = sub
                eff_dur, cuts = analyze_silence_gaps(start_t, end_t, segments)
                logger.info("Trimmed overlong candidate '%s' to complete subsection (%.2fs - %.2fs, eff: %.2fs)",
                            cand.title, start_t, end_t, eff_dur)
            else:
                logger.info("Rejecting candidate '%s' (effective duration %.2fs > max %.2fs with no complete subsection)",
                            cand.title, eff_dur, MAX_CLIP_DURATION)
                continue

        # 3. Final verification of effective duration
        if eff_dur < MIN_CLIP_DURATION or eff_dur > MAX_CLIP_DURATION:
            logger.info("Rejecting candidate '%s' (final effective duration %.2fs outside [%.1fs, %.1fs])",
                        cand.title, eff_dur, MIN_CLIP_DURATION, MAX_CLIP_DURATION)
            continue

        duration = eff_dur

        # Clean layout recommendation
        layout = cand.layout_recommendation.lower()
        if layout not in ("crop_follow", "fit_blur"):
            layout = "fit_blur"

        p_start = max(0.0, round(start_t - 4.0, 2))
        p_end = min(total_duration, round(end_t + 4.0, 2))

        cand_title = cand.title.strip()
        cand_hook = (cand.hook or "").strip()
        cand_desc = (cand.description or "").strip()
        if has_devanagari(cand_title):
            cand_title = phonetic_devanagari_to_roman(cand_title)
        if has_devanagari(cand_hook):
            cand_hook = phonetic_devanagari_to_roman(cand_hook)
        if has_devanagari(cand_desc):
            cand_desc = phonetic_devanagari_to_roman(cand_desc)

        valid_candidates.append({
            "start_time": start_t,
            "end_time": end_t,
            "padded_start": p_start,
            "padded_end": p_end,
            "duration": duration,
            "title": cand_title,
            "hook": cand_hook,
            "description": cand_desc,
            "hashtags": (cand.hashtags or "#Shorts #Reels").strip(),
            "virality_score": int(cand.virality_score),
            "layout_recommendation": layout,
            "reason": (cand.reason or "").strip(),
            "silence_cuts": cuts,
            "start_words": cand.start_words,
            "end_words": cand.end_words,
            "tier": (cand.tier or "A").upper(),
            "hook_type": cand.hook_type or "claim"
        })

    eff_max_clips = max_clips if max_clips is not None else compute_optimal_clip_quota(total_duration)

    # Sort primarily by tier ("A" first), then virality_score descending, then duration
    valid_candidates.sort(
        key=lambda c: (1 if c.get("tier") == "A" else 0, c["virality_score"], c["duration"]),
        reverse=True
    )

    # Deduplicate overlapping candidates (IoU > 0.45 or Containment > 0.45)
    deduped: List[Dict[str, Any]] = []
    for cand in valid_candidates:
        overlap = False
        for accepted in deduped:
            iou = compute_interval_overlap(cand["start_time"], cand["end_time"], accepted["start_time"], accepted["end_time"])
            containment = compute_containment_ratio(cand["start_time"], cand["end_time"], accepted["start_time"], accepted["end_time"])
            if iou > 0.45 or containment > 0.45:
                overlap = True
                logger.debug(
                    "Suppressing overlapping candidate '%s' (IoU %.2f, Containment %.2f with '%s')",
                    cand["title"], iou, containment, accepted["title"]
                )
                break
        if not overlap:
            deduped.append(cand)
            if len(deduped) >= eff_max_clips:
                break

    # Re-sort chronologically by start_time for a logical presentation
    deduped.sort(key=lambda c: c["start_time"])
    return deduped


def _strip_json_code_fences(raw_text: str) -> str:
    """Remove optional markdown code fences around a JSON payload."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```"):
            if lines[-1].strip().startswith("```"):
                cleaned = "\n".join(lines[1:-1]).strip()
            else:
                cleaned = "\n".join(lines[1:]).strip()
    return cleaned


def _parse_gemini_json_list_response(
    data: Any,
    model_name: str,
    raw_preview: str = "",
) -> Tuple[Optional[List[Any]], Optional[str]]:
    """Extract and validate a JSON list payload from Gemini generateContent JSON."""
    if not isinstance(data, dict):
        return None, f"Gemini {model_name} returned non-object response ({type(data).__name__})"

    preview = raw_preview or json.dumps(data, ensure_ascii=False)[:200]
    raw_text = ""

    if "candidates" in data:
        candidates = data.get("candidates") or []
        if not candidates:
            return None, f"Gemini {model_name} returned 200 but empty candidates array: {preview}"
        content = candidates[0].get("content", {}) if isinstance(candidates[0], dict) else {}
        parts = content.get("parts", []) if isinstance(content, dict) else []
        text_parts = [
            p["text"]
            for p in parts
            if isinstance(p, dict) and isinstance(p.get("text"), str) and not p.get("thought")
        ]
        if not text_parts:
            text_parts = [p["text"] for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str)]
        if not text_parts:
            return None, f"Gemini {model_name} missing text in candidate parts: {preview}"
        raw_text = _strip_json_code_fences("".join(text_parts))
    elif isinstance(data.get("text"), str):
        raw_text = _strip_json_code_fences(data["text"])
    else:
        return None, f"Gemini {model_name} returned 200 but empty candidates array: {preview}"

    parsed = json.loads(raw_text)
    if isinstance(parsed, dict):
        for k in ("clips", "highlights", "candidates", "tokens", "words", "transliterated", "result"):
            if k in parsed and isinstance(parsed[k], list):
                parsed = parsed[k]
                break

    if not isinstance(parsed, list):
        return None, f"Gemini {model_name} did not return a JSON array (got {type(parsed).__name__})"
    return parsed, None


def _is_proxy_auth_error(exc: Exception) -> bool:
    """Return True when a Worker proxy exception represents a permanent auth/vault failure."""
    if isinstance(exc, PermissionError):
        return True
    msg = str(exc).lower()
    return any(term in msg for term in (
        "(401)", "(403)", "api_key_invalid", "permission_denied",
        "unauthenticated", "gemini_key_missing", "not configured in dispatch cloud vault",
    ))


def call_gemini_generate_content(
    user_prompt: str,
    system_instruction: str = HIGHLIGHT_SYSTEM_PROMPT,
    api_key: Optional[str] = None,
    candidate_models: Optional[List[str]] = None,
    timeout: int = 45
) -> Tuple[List[Dict[str, Any]], str]:
    """Call Gemini API across candidate models sequentially with strict error propagation.
    
    Returns:
      (raw_clips_list, winning_model_name)
      
    Raises:
      RuntimeError: If authentication fails, quota exhausted, or all models fail.
    """
    from dispatch.transcription.gemini_engine import parse_worker_gemini_proxy_user

    try:
        proxy_user_id = parse_worker_gemini_proxy_user(api_key)
    except Exception as e:
        raise RuntimeError(f"Gemini API authentication failed: {e}") from e

    key = ""
    if not proxy_user_id:
        key = api_key or GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not configured or is empty. Cannot extract highlights.")

    models_to_try = candidate_models or get_configured_gemini_models()
    headers = {
        "x-goog-api-key": key,
        "Content-Type": "application/json"
    }

    payload = {
        "systemInstruction": {
            "parts": [{"text": system_instruction}]
        },
        "contents": [
            {
                "parts": [{"text": user_prompt}]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json"
        }
    }

    last_error_details = []

    for model_name in models_to_try:
        if proxy_user_id:
            try:
                import dispatch.youtube_connections as yc

                logger.info(
                    "Requesting Gemini highlight extraction via Cloud Worker proxy using model '%s'...",
                    model_name,
                )
                data = yc.call_user_gemini_proxy(
                    user_id=proxy_user_id,
                    contents=payload["contents"],
                    model=model_name,
                    generation_config=payload["generationConfig"],
                    system_instruction=payload["systemInstruction"] if system_instruction else None,
                    timeout_seconds=float(timeout),
                )
                parsed, parse_err = _parse_gemini_json_list_response(data, model_name)
                if parse_err:
                    logger.warning(parse_err)
                    last_error_details.append(parse_err)
                    continue
                logger.info("Gemini proxy model '%s' successfully returned %d raw candidate clips.", model_name, len(parsed))
                return parsed, model_name
            except Exception as exc:
                if _is_proxy_auth_error(exc):
                    err_msg = f"Gemini API authentication failed via Cloud Worker proxy: {exc}"
                    logger.error(err_msg)
                    raise RuntimeError(err_msg) from exc
                if isinstance(exc, json.JSONDecodeError):
                    err_msg = f"Gemini model '{model_name}' response was not valid JSON: {exc}"
                else:
                    err_msg = f"Gemini model '{model_name}' proxy error: {exc}"
                logger.warning(err_msg)
                last_error_details.append(err_msg)
                continue

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        try:
            logger.info("Requesting Gemini highlight extraction using model '%s'...", model_name)
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
            
            if response.status_code == 200:
                data = response.json()
                parsed, parse_err = _parse_gemini_json_list_response(
                    data, model_name, raw_preview=response.text[:200]
                )
                if parse_err:
                    logger.warning(parse_err)
                    last_error_details.append(parse_err)
                    continue

                logger.info("Gemini model '%s' successfully returned %d raw candidate clips.", model_name, len(parsed))
                return parsed, model_name

            elif response.status_code in (401, 403):
                # Permanent auth failure
                err_msg = f"Gemini API authentication failed (HTTP {response.status_code}): {response.text[:200]}"
                logger.error(err_msg)
                raise RuntimeError(err_msg)

            elif response.status_code == 429:
                err_msg = f"Gemini model '{model_name}' rate limited / quota exhausted (HTTP 429): {response.text[:200]}"
                logger.warning(err_msg)
                last_error_details.append(err_msg)

            elif response.status_code in (500, 503):
                err_msg = f"Gemini model '{model_name}' temporary server error (HTTP {response.status_code}): {response.text[:200]}"
                logger.warning(err_msg)
                last_error_details.append(err_msg)

            else:
                err_msg = f"Gemini model '{model_name}' returned HTTP {response.status_code}: {response.text[:200]}"
                logger.warning(err_msg)
                last_error_details.append(err_msg)

        except requests.exceptions.Timeout as te:
            err_msg = f"Gemini model '{model_name}' timed out after {timeout}s: {te}"
            logger.warning(err_msg)
            last_error_details.append(err_msg)
        except requests.exceptions.RequestException as re:
            err_msg = f"Gemini model '{model_name}' network error: {re}"
            logger.warning(err_msg)
            last_error_details.append(err_msg)
        except json.JSONDecodeError as jde:
            err_msg = f"Gemini model '{model_name}' response was not valid JSON: {jde}"
            logger.warning(err_msg)
            last_error_details.append(err_msg)

    # If all models failed
    full_err = f"All Gemini candidate models failed: {'; '.join(last_error_details)}"
    logger.error(full_err)
    raise RuntimeError(full_err)


def extract_clips_gemini(transcript_text: str, segments: List[Dict[str, Any]], user_id: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
    """Compatibility wrapper for extract_clips_gemini."""
    negative_context = get_negative_feedback_prompt(user_id)
    tot_dur = segments[-1]["end"] if segments else None
    user_prompt = build_highlight_user_prompt(transcript_text, negative_context, total_duration=tot_dur)
    try:
        active_key: Optional[str] = None
        if user_id:
            from dispatch.transcription.gemini_engine import resolve_gemini_api_key
            active_key = resolve_gemini_api_key(user_id, raise_if_missing_host_key=True)
        raw_clips, _ = call_gemini_generate_content(user_prompt, api_key=active_key)
        return raw_clips
    except Exception as e:
        logger.warning("extract_clips_gemini wrapper encountered error: %s", e)
        return None


def extract_clips_local_heuristic(segments: List[Dict[str, Any]], total_duration: float) -> List[Dict[str, Any]]:
    """Local autonomous heuristic highlight detector.
    Retained for offline boundary and stress unit tests.
    """
    if not segments:
        return []

    full_text = " ".join(s.get("text", "") for s in segments).strip()
    words = full_text.split()
    if len(words) < 4:
        return []

    HOOK_KEYWORDS = [
        "suno", "dekho", "basically", "problem yeh hai", "important", "secret", "kaise",
        "agar aap", "solution", "sabse pehle", "trick", "automate", "system", "real reason",
        "so", "actually", "the thing is", "remember", "never", "always", "funny thing",
        "i realized", "story", "honestly", "point is", "truth is", "listen", "wild", "key"
    ]

    candidate_clips = []
    current_block_segments = []
    current_start = segments[0]["start"]

    for seg in segments:
        current_block_segments.append(seg)
        current_duration = seg["end"] - current_start

        if current_duration >= MIN_CLIP_DURATION:
            if current_duration >= 45.0 or current_duration >= MAX_CLIP_DURATION:
                block_text = " ".join(s.get("text", "") for s in current_block_segments).strip()
                first_sentence = current_block_segments[0].get("text", "").strip()

                virality = 60
                text_lower = block_text.lower()
                for kw in HOOK_KEYWORDS:
                    if kw in text_lower:
                        virality = min(95, virality + 8)

                block_words = block_text.split()
                title_words = block_words[:6]
                title = " ".join(title_words)
                if len(title) > 50:
                    title = title[:47] + "..."
                if not title.endswith(("?", "!")):
                    title = f"{title} | Complete Guide"

                candidate_clips.append({
                    "start_time": round(current_start, 2),
                    "end_time": round(seg["end"], 2),
                    "title": title,
                    "hook": first_sentence[:80],
                    "description": f"Insight from session: {block_text[:120]}...",
                    "hashtags": "#Shorts #Reels #Hinglish #Productivity #Dispatch",
                    "virality_score": virality,
                    "layout_recommendation": "fit_blur",
                    "reason": "High semantic density complete thought block"
                })

                current_block_segments = []
                current_start = seg["end"]

    if current_block_segments:
        rem_duration = current_block_segments[-1]["end"] - current_start
        if rem_duration >= MIN_CLIP_DURATION:
            block_text = " ".join(s.get("text", "") for s in current_block_segments).strip()
            candidate_clips.append({
                "start_time": round(current_start, 2),
                "end_time": round(current_block_segments[-1]["end"], 2),
                "title": f"{block_text[:40]}... Key Takeaway",
                "hook": current_block_segments[0].get("text", "")[:80],
                "description": f"Closing takeaway: {block_text[:120]}...",
                "hashtags": "#Shorts #Reels #Hinglish #Takeaway",
                "virality_score": 70,
                "layout_recommendation": "fit_blur",
                "reason": "Concluding thought segment"
            })

    if not candidate_clips and MIN_CLIP_DURATION <= total_duration:
        clip_end = min(total_duration, float(segments[-1].get("end", total_duration)))
        clip_duration = clip_end - 0.0
        if clip_duration > MAX_CLIP_DURATION:
            matching_segs = [s for s in segments if float(s.get("end", 0.0)) <= MAX_CLIP_DURATION]
            if matching_segs and (matching_segs[-1]["end"] - 0.0) >= MIN_CLIP_DURATION:
                clip_end = matching_segs[-1]["end"]
            else:
                clip_end = MAX_CLIP_DURATION

        snippet_text = " ".join(s.get("text", "") for s in segments if float(s.get("end", 0.0)) <= clip_end).strip()
        candidate_clips.append({
            "start_time": 0.0,
            "end_time": round(clip_end, 2),
            "title": f"{snippet_text[:40]} | Quick Clip",
            "hook": segments[0].get("text", "")[:80] if segments else "Quick highlight",
            "description": snippet_text[:120],
            "hashtags": "#Shorts #Reels #Tech #Dispatch",
            "virality_score": 75,
            "layout_recommendation": "fit_blur",
            "reason": "Complete thought clip"
        })

    return candidate_clips




def extract_highlights_windowed(
    segments: List[Dict[str, Any]],
    total_duration: float,
    negative_context: str = "",
    api_key: Optional[str] = None,
    candidate_models: Optional[List[str]] = None,
    window_duration: float = 720.0,
    window_overlap: float = 90.0
) -> Tuple[List[Dict[str, Any]], str]:
    """Extract highlights from long recordings by partitioning into overlapping 12-minute windows.

    Prevents single-pass attention loss on long videos (>12m) while deduplicating globally.
    """
    if total_duration <= window_duration:
        transcript_lines = []
        prev_end = 0.0
        for s in segments:
            s_start = float(s.get("start", 0.0))
            s_end = float(s.get("end", 0.0))
            if prev_end > 0.0 and (s_start - prev_end) >= 0.5:
                transcript_lines.append(f"[PAUSE: {s_start - prev_end:.1f}s]")
            start_m, start_s = divmod(s_start, 60)
            end_m, end_s = divmod(s_end, 60)
            transcript_lines.append(
                f"[{int(start_m):02d}:{start_s:05.2f} ({s_start:.1f}s) -> {int(end_m):02d}:{end_s:05.2f} ({s_end:.1f}s)] {s.get('text', '')}"
            )
            prev_end = max(prev_end, s_end)

        transcript_text = "\n".join(transcript_lines)
        user_prompt = build_highlight_user_prompt(transcript_text, negative_context, total_duration=total_duration)
        return call_gemini_generate_content(
            user_prompt,
            api_key=api_key,
            candidate_models=candidate_models
        )

    step = window_duration - window_overlap
    all_raw_candidates: List[Dict[str, Any]] = []
    winning_model = "windowed_gemini"

    num_windows = int(math.ceil((total_duration - window_duration) / step)) + 1
    logger.info("Partitioning %.1fs recording into %d overlapping extraction windows of %.0fs (step %.0fs)",
                total_duration, num_windows, window_duration, step)

    current_start = 0.0
    win_idx = 0
    while current_start < total_duration:
        current_end = min(total_duration, current_start + window_duration)
        win_idx += 1

        win_segments = [
            s for s in segments
            if float(s.get("end", 0.0)) > current_start and float(s.get("start", 0.0)) < current_end
        ]

        if not win_segments or not any(s.get("text", "").strip() for s in win_segments):
            logger.debug("Window #%d [%.1fs - %.1fs] contains no speech; skipping.", win_idx, current_start, current_end)
            current_start += step
            continue

        transcript_lines = []
        prev_end = 0.0
        for s in win_segments:
            s_start = float(s.get("start", 0.0))
            s_end = float(s.get("end", 0.0))
            if prev_end > 0.0 and (s_start - prev_end) >= 0.5:
                transcript_lines.append(f"[PAUSE: {s_start - prev_end:.1f}s]")
            start_m, start_s = divmod(s_start, 60)
            end_m, end_s = divmod(s_end, 60)
            transcript_lines.append(
                f"[{int(start_m):02d}:{start_s:05.2f} ({s_start:.1f}s) -> {int(end_m):02d}:{end_s:05.2f} ({s_end:.1f}s)] {s.get('text', '')}"
            )
            prev_end = max(prev_end, s_end)

        transcript_text = "\n".join(transcript_lines)
        win_duration = current_end - current_start
        user_prompt = build_highlight_user_prompt(transcript_text, negative_context, total_duration=win_duration)

        try:
            raw_clips, model_used = call_gemini_generate_content(
                user_prompt,
                api_key=api_key,
                candidate_models=candidate_models
            )
            winning_model = model_used

            for c in raw_clips:
                c_start = float(c.get("start_time", 0.0))
                c_end = float(c.get("end_time", 0.0))
                if c_start < (current_start - 30.0) and current_start > 0.0:
                    c["start_time"] = round(c_start + current_start, 2)
                    c["end_time"] = round(c_end + current_start, 2)
                all_raw_candidates.append(c)

            logger.info("Window #%d [%.1fs - %.1fs] extracted %d candidates via %s",
                        win_idx, current_start, current_end, len(raw_clips), model_used)

        except Exception as win_err:
            msg_l = str(win_err).lower()
            if (
                isinstance(win_err, PermissionError)
                or win_err.__class__.__name__ == "PermanentAuthError"
                or any(term in msg_l for term in (
                    "authentication failed",
                    "api_key_invalid",
                    "gemini_api_key is not configured",
                    "refusing fallback to pc host gemini_api_key",
                    "permission_denied",
                    "(401)",
                    "(403)",
                ))
            ):
                raise
            logger.warning("Extraction window #%d [%.1fs - %.1fs] failed (%s); continuing other windows",
                           win_idx, current_start, current_end, win_err)

        current_start += step

    if not all_raw_candidates:
        raise RuntimeError("All windowed extraction passes failed or produced 0 candidate clips.")

    return all_raw_candidates, winning_model


def identify_and_save_highlights(
    chunk_id: Optional[str],
    session_id: Optional[str],
    segments: List[Dict[str, Any]],
    total_duration: float,
    publish_mode: str = "private",
    video_id: Optional[str] = None,
    user_id: Optional[str] = None,
    api_key: Optional[str] = None
) -> List[str]:
    """Identify highlights via Gemini and save candidate definitions.
    
    If video_id is provided, coordinates atomic transaction and status transition to CLIPS_DEFINED.
    """
    if not segments:
        logger.info("Zero speech segments detected. Returning 0 clips.")
        if video_id:
            db.update_youtube_video(video_id=video_id, status="CLIPS_DEFINED")
        return []

    eff_user_id = user_id
    if not eff_user_id and video_id:
        v = db.get_youtube_video(video_id)
        if v and v.get("user_id"):
            eff_user_id = v["user_id"]
    if not eff_user_id and chunk_id:
        chk = db.get_chunk_by_id(chunk_id)
        if chk and chk.get("user_id"):
            eff_user_id = chk["user_id"]
    if not eff_user_id and session_id:
        with db.get_db_connection() as conn:
            r = conn.execute("SELECT user_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if r and r["user_id"]:
                eff_user_id = r["user_id"]

    # Multi-tenant API key resolution: strictly prevent falling back to PC host key when user identity exists
    active_gemini_key = api_key
    if not active_gemini_key:
        if eff_user_id:
            try:
                from dispatch.transcription.gemini_engine import resolve_gemini_api_key
                active_gemini_key = resolve_gemini_api_key(eff_user_id, raise_if_missing_host_key=True)
            except RuntimeError as ke:
                err_msg = str(ke)
                if video_id:
                    db.update_youtube_video(video_id=video_id, status="FAILED", last_ai_error=err_msg)
                raise
        else:
            active_gemini_key = os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY

    from dispatch.ai_clips.preference_learner import get_editorial_guidance_prompt
    editorial_context = get_editorial_guidance_prompt(eff_user_id)

    # Gemini extraction (windowed for recordings > 12m) with local heuristic fallback when offline
    try:
        raw_clips, model_used = extract_highlights_windowed(
            segments=segments,
            total_duration=total_duration,
            negative_context=editorial_context,
            api_key=active_gemini_key
        )
    except Exception as e:
        logger.warning("Gemini highlight extraction failed (%s). Falling back to local heuristic extractor.", e)
        raw_clips = extract_clips_local_heuristic(segments, total_duration)
        model_used = "heuristic_fallback"

    extraction_source = "heuristic_fallback" if model_used == "heuristic_fallback" else "gemini"
    extraction_model = model_used

    optimal_quota = compute_optimal_clip_quota(total_duration)

    # Validate and filter
    validated_clips = validate_and_filter_candidates(
        raw_candidates=raw_clips,
        segments=segments,
        total_duration=total_duration,
        max_clips=optimal_quota
    )

    eff_user_id = user_id
    if not eff_user_id and video_id:
        v = db.get_youtube_video(video_id)
        if v and v.get("user_id"):
            eff_user_id = v["user_id"]
    if not eff_user_id and chunk_id:
        chk = db.get_chunk_by_id(chunk_id)
        if chk and chk.get("user_id"):
            eff_user_id = chk["user_id"]
    if not eff_user_id and session_id:
        with db.get_db_connection() as conn:
            r = conn.execute("SELECT user_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if r and r["user_id"]:
                eff_user_id = r["user_id"]

    tpl = db.get_default_template(user_id=eff_user_id)
    tpl_framing = tpl.get("framing", {})

    if video_id:
        saved_ids = db.save_clip_definitions(
            chunk_id=chunk_id,
            session_id=session_id,
            video_id=video_id,
            clip_defs=validated_clips,
            publish_mode=publish_mode,
            user_id=eff_user_id,
            extraction_source=extraction_source,
            extraction_model=extraction_model
        )
    else:
        saved_ids = []
        for c in validated_clips:
            c_layout = c.get("layout_recommendation")
            if not c_layout:
                c_layout = tpl_framing.get("horizontal_mode") or "crop_916"
            cid = db.save_clip(
                session_id=session_id,
                chunk_id=chunk_id,
                start_time=c["start_time"],
                end_time=c["end_time"],
                duration=c.get("duration"),
                title=c["title"],
                hook=c["hook"],
                description=c["description"],
                hashtags=c["hashtags"],
                virality_score=c["virality_score"],
                layout_mode=c_layout,
                platform_targets="youtube",
                publish_mode=publish_mode,
                padded_start=c.get("padded_start"),
                padded_end=c.get("padded_end"),
                user_id=eff_user_id,
                extraction_source=extraction_source,
                extraction_model=extraction_model
            )
            saved_ids.append(cid)

    logger.info("Highlight processing complete (Model: %s): %d clips accepted and saved.",
                model_used, len(saved_ids))
    return saved_ids

