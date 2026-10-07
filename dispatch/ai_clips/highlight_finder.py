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
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.8-flash",
    "gemini-3.1-flash-lite",
    "gemini-flash-latest"
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
      3. Word boundary snapping.
      4. Strict duration constraint: 20.0s <= duration <= 90.0s. If invalid, REJECT. Never fabricate.
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

        # Boundary snapping
        if segments:
            start_t = max(0.0, snap_to_word_boundary(start_t, segments, prefer_start=True))
            end_t = min(total_duration, snap_to_word_boundary(end_t, segments, prefer_start=False))

        # Strict duration check: never artificially manufacture duration
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

                # Can be a JSON list or dict with list under key "clips" / "highlights"
                if isinstance(parsed, dict):
                    for k in ("clips", "highlights", "candidates"):
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
    """[LEGACY / UNUSED IN PILLAR 3]
    Local autonomous heuristic highlight detector.
    Retained solely for backward compatibility with offline standalone unit tests if needed.
    Never invoked by the active Pillar 3 worker.
    """
    logger.warning("[LEGACY / UNUSED] extract_clips_local_heuristic invoked.")
    return []


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

    # Gemini API call: strictly fails without silent heuristic fallback
    raw_clips, model_used = call_gemini_generate_content(user_prompt)

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
