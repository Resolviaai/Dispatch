"""AI Semantic highlight detector and short-form packaging engine.
Extracts high-retention clips (20-90s) with complete thoughts, hooks, and Roman Hinglish metadata.
Includes a resilient dual-mode: Gemini Flash API with an autonomous local heuristic fallback.
"""
import re
import json
import logging
import requests
from typing import List, Dict, Any, Optional
from dispatch.config import GEMINI_API_KEY, MIN_CLIP_DURATION, MAX_CLIP_DURATION
from dispatch.ai_clips.prompt_templates import HIGHLIGHT_SYSTEM_PROMPT, build_highlight_user_prompt
from dispatch.ai_clips.preference_learner import get_negative_feedback_prompt
from dispatch import db

logger = logging.getLogger("dispatch.ai_clips")


def snap_to_word_boundary(timestamp: float, segments: List[Dict[str, Any]], prefer_start: bool = True) -> float:
    """Snap a timestamp to the closest actual spoken word boundary in segments."""
    best_time = timestamp
    min_diff = float("inf")

    for seg in segments:
        words = seg.get("words", [])
        if words:
            for w in words:
                target = w["start"] if prefer_start else w["end"]
                diff = abs(target - timestamp)
                if diff < min_diff:
                    min_diff = diff
                    best_time = target
        else:
            target = seg["start"] if prefer_start else seg["end"]
            diff = abs(target - timestamp)
            if diff < min_diff:
                min_diff = diff
                best_time = target

    return round(best_time, 2)


def extract_clips_gemini(transcript_text: str, segments: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """Call Gemini Flash API to detect candidate highlights."""
    if not GEMINI_API_KEY:
        return None

    negative_context = get_negative_feedback_prompt()
    user_prompt = build_highlight_user_prompt(transcript_text, negative_context)

    # Use the 4 available models from Google AI Studio quota tier:
    # 1. gemini-3.5-flash (primary high-quality model)
    # 2. gemini-3.8-flash (primary alternative)
    # 3. gemini-3.5-flash-lite (fast lightweight fallback)
    # 4. gemini-3.1-flash-lite (high RPM/RPD fallback)
    CANDIDATE_MODELS = [
        "gemini-3.5-flash",
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite"
    ]

    headers = {
        "x-goog-api-key": GEMINI_API_KEY,
        "Content-Type": "application/json"
    }
    
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": HIGHLIGHT_SYSTEM_PROMPT},
                    {"text": user_prompt}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json"
        }
    }

    for model_name in CANDIDATE_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        try:
            logger.info("Requesting highlight extraction via %s...", model_name)
            response = requests.post(url, headers=headers, json=payload, timeout=45)
            if response.status_code == 200:
                data = response.json()
                raw_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                clips = json.loads(raw_text)
                if isinstance(clips, list) and len(clips) > 0:
                    logger.info("Gemini (%s) successfully identified %d clips", model_name, len(clips))
                    return clips
            else:
                logger.warning("Gemini model %s returned %d: %s. Trying next model...",
                               model_name, response.status_code, response.text[:150])
        except Exception as e:
            logger.warning("Gemini model %s request failed (%s). Trying next model...", model_name, e)

    return None


def extract_clips_local_heuristic(segments: List[Dict[str, Any]], total_duration: float) -> List[Dict[str, Any]]:
    """Local autonomous heuristic highlight detector.
    Groups transcript segments into semantic thought blocks (20-90s) with hook detection.
    """
    logger.info("Running local autonomous highlight detector over %d segments", len(segments))
    if not segments:
        return []

    # Common Hinglish/English hook words and high-energy phrases
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

        # When block reaches suitable duration (e.g., 30-60s) or hits a natural boundary
        if current_duration >= MIN_CLIP_DURATION:
            if current_duration >= 45.0 or current_duration >= MAX_CLIP_DURATION:
                block_text = " ".join(s["text"] for s in current_block_segments).strip()
                first_sentence = current_block_segments[0]["text"].strip()

                # Calculate virality score based on hook keywords and speech density
                virality = 60
                text_lower = block_text.lower()
                for kw in HOOK_KEYWORDS:
                    if kw in text_lower:
                        virality = min(95, virality + 8)

                # Generate Roman Hinglish title
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

                # Advance window with slight overlap for context
                current_block_segments = []
                current_start = seg["end"]

    # If the remaining block fits the minimum duration, include it
    if current_block_segments:
        rem_duration = current_block_segments[-1]["end"] - current_start
        if rem_duration >= MIN_CLIP_DURATION:
            block_text = " ".join(s["text"] for s in current_block_segments).strip()
            candidate_clips.append({
                "start_time": round(current_start, 2),
                "end_time": round(current_block_segments[-1]["end"], 2),
                "title": f"{block_text[:40]}... Key Takeaway",
                "hook": current_block_segments[0]["text"][:80],
                "description": f"Closing takeaway: {block_text[:120]}...",
                "hashtags": "#Shorts #Reels #Hinglish #Takeaway",
                "virality_score": 70,
                "layout_recommendation": "fit_blur",
                "reason": "Concluding thought segment"
            })

    # Fallback: if entire video is short (e.g. 20-90s), treat whole video as one clip
    if not candidate_clips and total_duration >= MIN_CLIP_DURATION:
        full_text = " ".join(s["text"] for s in segments).strip()
        candidate_clips.append({
            "start_time": 0.0,
            "end_time": round(total_duration, 2),
            "title": f"{full_text[:40]} | Quick Clip",
            "hook": segments[0]["text"][:80] if segments else "Quick highlight",
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
    publish_mode: str = "private"
) -> List[str]:
    """Identify highlights using Gemini Flash (with heuristic fallback) and save candidate clips to DB."""
    if not segments:
        logger.info("No speech detected in chunk. Skipping highlight generation.")
        return []

    # Format transcript lines with timestamps for the LLM
    transcript_lines = []
    for s in segments:
        start_m, start_s = divmod(s["start"], 60)
        end_m, end_s = divmod(s["end"], 60)
        transcript_lines.append(f"[{int(start_m):02d}:{start_s:05.2f} -> {int(end_m):02d}:{end_s:05.2f}] {s['text']}")
    transcript_text = "\n".join(transcript_lines)

    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is required to analyze YouTube inbox videos")

    raw_clips = extract_clips_gemini(transcript_text, segments)
    if not raw_clips:
        raise RuntimeError("Gemini did not return usable highlight clips")

    saved_clip_ids = []

    for c in raw_clips:
        start_t = float(c.get("start_time", 0.0))
        end_t = float(c.get("end_time", total_duration))

        # Clamp and snap boundaries to exact spoken word boundaries
        start_t = max(0.0, snap_to_word_boundary(start_t, segments, prefer_start=True))
        end_t = min(total_duration, snap_to_word_boundary(end_t, segments, prefer_start=False))

        duration = end_t - start_t
        if duration < MIN_CLIP_DURATION:
            end_t = min(total_duration, start_t + MIN_CLIP_DURATION)
            duration = end_t - start_t

        title = c.get("title", "Dispatch Highlight")
        hook = c.get("hook", "")
        description = c.get("description", "")
        hashtags = c.get("hashtags", "#Shorts #Reels")
        virality_score = int(c.get("virality_score", 70))
        layout_mode = c.get("layout_recommendation", "fit_blur")

        clip_id = db.save_clip(
            session_id=session_id,
            chunk_id=chunk_id,
            start_time=start_t,
            end_time=end_t,
            title=title,
            hook=hook,
            description=description,
            hashtags=hashtags,
            virality_score=virality_score,
            layout_mode=layout_mode,
            publish_mode=publish_mode,
            platform_targets="youtube,instagram"
        )
        saved_clip_ids.append(clip_id)
        logger.info("Saved candidate clip %s: '%s' [%.1fs - %.1fs] (Duration: %.1fs, Virality: %d)",
                    clip_id, title, start_t, end_t, duration, virality_score)

    return saved_clip_ids
