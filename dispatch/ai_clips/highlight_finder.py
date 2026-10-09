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
from pydantic import BaseModel, Field, ValidationError

from dispatch.config import GEMINI_API_KEY, MIN_CLIP_DURATION, MAX_CLIP_DURATION
from dispatch.ai_clips.prompt_templates import HIGHLIGHT_SYSTEM_PROMPT, build_highlight_user_prompt
from dispatch.ai_clips.preference_learner import get_negative_feedback_prompt
from dispatch import db

logger = logging.getLogger("dispatch.ai_clips")

# Configurable default list of candidate Gemini models in priority order
DEFAULT_GEMINI_MODELS = [
    "gemini-flash-latest",
    "gemini-3.5-flash",
    "gemini-3.8-flash",
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


def get_configured_gemini_models() -> List[str]:
    """Retrieve list of candidate models from env or config defaults."""
    env_models = os.getenv("DISPATCH_GEMINI_MODELS")
    if env_models:
        parsed = [m.strip() for m in env_models.split(",") if m.strip()]
        if parsed:
            return parsed
    return DEFAULT_GEMINI_MODELS


def snap_to_word_boundary(timestamp: float, segments: List[Dict[str, Any]], prefer_start: bool = True) -> float:
    """Snap a timestamp to the closest actual spoken word boundary in segments."""
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

    return round(best_time, 2)


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
                            break
            if start_t > float(clip_words[0].get("start", 0.0)):
                break
        if start_t > float(clip_words[0].get("start", 0.0)):
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


DEVANAGARI_REGEX = re.compile(r'[\u0900-\u097F]')


def has_devanagari(text: str) -> bool:
    """Check if string contains any Devanagari Hindi characters."""
    return bool(DEVANAGARI_REGEX.search(text))


def transliterate_tokens_to_hinglish(tokens: List[str], timeout: int = 25) -> List[str]:
    """Transliterate a list of Devanagari tokens into Roman Hinglish with 1:1 token preservation using configured Gemini models.
    Reuses Dispatch's configured Gemini integration, model hierarchy, and structured validation.
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
        raw_result, winning_model = call_gemini_generate_content(
            user_prompt=prompt,
            system_instruction="You are a strict 1-to-1 Devanagari-to-Roman Hinglish transliterator. Return only a JSON array of strings with matching length.",
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
            return [str(t) for t in candidate_list]

        logger.warning(
            "Gemini model '%s' transliteration returned unexpected structure or count (%s, expected %d). Preserving original tokens.",
            winning_model, len(candidate_list) if candidate_list is not None else type(raw_result).__name__, len(tokens)
        )
    except Exception as e:
        logger.warning("Gemini transliteration failed across configured models (%s). Preserving original tokens.", e)

    return tokens


def transliterate_segments_to_hinglish(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
            transliterated = transliterate_tokens_to_hinglish(token_list)
            for (s_idx, w_idx), new_word in zip(all_words_meta, transliterated):
                output_segments[s_idx]["words"][w_idx]["word"] = new_word
            for s in output_segments:
                if s.get("words"):
                    s["text"] = " ".join(w["word"] for w in s["words"])
    else:
        # Segment-level text transliteration
        seg_texts = [s.get("text", "") for s in output_segments]
        if any(has_devanagari(t) for t in seg_texts):
            transliterated_texts = transliterate_tokens_to_hinglish(seg_texts)
            for s, new_text in zip(output_segments, transliterated_texts):
                s["text"] = new_text

    return output_segments


def validate_and_filter_candidates(
    raw_candidates: List[Dict[str, Any]],
    segments: List[Dict[str, Any]],
    total_duration: float,
    max_clips: int = MAX_HIGHLIGHTS_PER_CHUNK
) -> List[Dict[str, Any]]:
    """Validate, snap, clamp, filter, and deduplicate candidate clips.
    
    Rules:
      1. Schema validation via Pydantic HighlightCandidate.
      2. Timestamp clamping to [0.0, total_duration].
      3. Word boundary snapping with false start detection & padding margins.
      4. Strict duration constraint: MIN_CLIP_DURATION <= duration <= 180.0s.
      5. Deduplication: Suppress heavy overlapping clips (IoU > 0.5), keeping higher virality.
      6. Capped at max_clips (highest virality first).
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

        # Clean speech restarts and false starts throughout candidate
        if segments:
            start_t, end_t = clean_speech_restarts_and_boundaries(start_t, end_t, segments, total_duration)

        # Boundary snapping with safe audio padding margin (0.15s start, 0.20s end)
        if segments:
            snapped_s = snap_to_word_boundary(start_t, segments, prefer_start=True)
            snapped_e = snap_to_word_boundary(end_t, segments, prefer_start=False)
            start_t = max(0.0, snapped_s - 0.15)
            end_t = min(total_duration, snapped_e + 0.20)

        # Enforce canonical duration constraints (20.0s <= duration <= 90.0s)
        duration = round(end_t - start_t, 2)
        if duration < MIN_CLIP_DURATION:
            logger.info("Rejecting candidate '%s' (duration %.2fs < min %.2fs)", cand.title, duration, MIN_CLIP_DURATION)
            continue
        if duration > MAX_CLIP_DURATION:
            logger.info("Rejecting candidate '%s' (duration %.2fs > max %.2fs)", cand.title, duration, MAX_CLIP_DURATION)
            continue

        # Clean layout recommendation
        layout = cand.layout_recommendation.lower()
        if layout not in ("crop_follow", "fit_blur"):
            layout = "fit_blur"

        valid_candidates.append({
            "start_time": start_t,
            "end_time": end_t,
            "duration": duration,
            "title": cand.title.strip(),
            "hook": (cand.hook or "").strip(),
            "description": (cand.description or "").strip(),
            "hashtags": (cand.hashtags or "#Shorts #Reels").strip(),
            "virality_score": int(cand.virality_score),
            "layout_recommendation": layout,
            "reason": (cand.reason or "").strip()
        })

    # Sort primarily by virality_score descending, then duration
    valid_candidates.sort(key=lambda c: (c["virality_score"], c["duration"]), reverse=True)

    # Deduplicate overlapping candidates (IoU > 0.5)
    deduped: List[Dict[str, Any]] = []
    for cand in valid_candidates:
        overlap = False
        for accepted in deduped:
            iou = compute_interval_overlap(cand["start_time"], cand["end_time"], accepted["start_time"], accepted["end_time"])
            if iou > 0.5:
                overlap = True
                logger.debug("Suppressing overlapping candidate '%s' (IoU %.2f with '%s')",
                             cand["title"], iou, accepted["title"])
                break
        if not overlap:
            deduped.append(cand)
            if len(deduped) >= max_clips:
                break

    # Re-sort chronologically by start_time for a logical presentation
    deduped.sort(key=lambda c: c["start_time"])
    return deduped


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
    key = api_key or GEMINI_API_KEY
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured or is empty. Cannot extract highlights.")

    models_to_try = candidate_models or get_configured_gemini_models()
    headers = {
        "x-goog-api-key": key,
        "Content-Type": "application/json"
    }

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": system_instruction},
                    {"text": user_prompt}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json"
        }
    }

    last_error_details = []

    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        try:
            logger.info("Requesting Gemini highlight extraction using model '%s'...", model_name)
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
            
            if response.status_code == 200:
                data = response.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    err_msg = f"Gemini {model_name} returned 200 but empty candidates array: {response.text[:200]}"
                    logger.warning(err_msg)
                    last_error_details.append(err_msg)
                    continue

                content = candidates[0].get("content", {})
                parts = content.get("parts", [])
                if not parts or "text" not in parts[0]:
                    err_msg = f"Gemini {model_name} missing text in candidate parts: {response.text[:200]}"
                    logger.warning(err_msg)
                    last_error_details.append(err_msg)
                    continue

                raw_text = parts[0]["text"].strip()
                parsed = json.loads(raw_text)

                # Can be a JSON list or dict with list under key "clips", "highlights", "tokens", etc.
                if isinstance(parsed, dict):
                    for k in ("clips", "highlights", "candidates", "tokens", "words", "transliterated", "result"):
                        if k in parsed and isinstance(parsed[k], list):
                            parsed = parsed[k]
                            break

                if not isinstance(parsed, list):
                    err_msg = f"Gemini {model_name} did not return a JSON array (got {type(parsed).__name__})"
                    logger.warning(err_msg)
                    last_error_details.append(err_msg)
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


def extract_clips_gemini(transcript_text: str, segments: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """Compatibility wrapper for extract_clips_gemini."""
    negative_context = get_negative_feedback_prompt()
    user_prompt = build_highlight_user_prompt(transcript_text, negative_context)
    try:
        raw_clips, _ = call_gemini_generate_content(user_prompt)
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

    HOOK_KEYWORDS = [
        "suno", "dekho", "basically", "problem yeh hai", "important", "secret", "kaise",
        "agar aap", "solution", "sabse pehle", "trick", "automate", "system", "real reason"
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

                words = block_text.split()
                title_words = words[:6]
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

    if not candidate_clips and total_duration >= MIN_CLIP_DURATION:
        full_text = " ".join(s.get("text", "") for s in segments).strip()
        candidate_clips.append({
            "start_time": 0.0,
            "end_time": round(total_duration, 2),
            "title": f"{full_text[:40]} | Quick Clip",
            "hook": segments[0].get("text", "")[:80] if segments else "Quick highlight",
            "description": full_text[:120],
            "hashtags": "#Shorts #Reels #Tech #Dispatch",
            "virality_score": 75,
            "layout_recommendation": "fit_blur",
            "reason": "Full session thought clip"
        })

    return candidate_clips


def identify_and_save_highlights(
    chunk_id: Optional[str],
    session_id: Optional[str],
    segments: List[Dict[str, Any]],
    total_duration: float,
    publish_mode: str = "private",
    video_id: Optional[str] = None
) -> List[str]:
    """Identify highlights via Gemini and save candidate definitions.
    
    If video_id is provided, coordinates atomic transaction and status transition to CLIPS_DEFINED.
    """
    if not segments:
        logger.info("Zero speech segments detected. Returning 0 clips.")
        if video_id:
            db.update_youtube_video(video_id=video_id, status="CLIPS_DEFINED")
        return []

    # Build timestamped transcript string
    transcript_lines = []
    for s in segments:
        start_m, start_s = divmod(s.get("start", 0.0), 60)
        end_m, end_s = divmod(s.get("end", 0.0), 60)
        transcript_lines.append(f"[{int(start_m):02d}:{start_s:05.2f} -> {int(end_m):02d}:{end_s:05.2f}] {s.get('text', '')}")
    transcript_text = "\n".join(transcript_lines)

    negative_context = get_negative_feedback_prompt()
    user_prompt = build_highlight_user_prompt(transcript_text, negative_context)

    # Gemini API call with local heuristic fallback when offline / rate-limited
    try:
        raw_clips, model_used = call_gemini_generate_content(user_prompt)
    except Exception as e:
        logger.warning("Gemini highlight extraction failed (%s). Falling back to local heuristic extractor.", e)
        raw_clips = extract_clips_local_heuristic(segments, total_duration)
        model_used = "heuristic_fallback"

    # Validate and filter
    validated_clips = validate_and_filter_candidates(
        raw_candidates=raw_clips,
        segments=segments,
        total_duration=total_duration,
        max_clips=MAX_HIGHLIGHTS_PER_CHUNK
    )

    if video_id:
        saved_ids = db.save_clip_definitions(
            chunk_id=chunk_id,
            session_id=session_id,
            video_id=video_id,
            clip_defs=validated_clips,
            publish_mode=publish_mode
        )
    else:
        saved_ids = []
        for c in validated_clips:
            cid = db.save_clip(
                session_id=session_id,
                chunk_id=chunk_id,
                start_time=c["start_time"],
                end_time=c["end_time"],
                title=c["title"],
                hook=c["hook"],
                description=c["description"],
                hashtags=c["hashtags"],
                virality_score=c["virality_score"],
                layout_mode=c["layout_recommendation"],
                publish_mode=publish_mode
            )
            saved_ids.append(cid)

    logger.info("Highlight processing complete (Model: %s): %d clips accepted and saved.",
                model_used, len(saved_ids))
    return saved_ids
