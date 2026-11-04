"""Post-transcription AI metadata enrichment for YouTube video recordings.

Generates an authentic, creator-friendly YouTube title and structured description
using a single Gemini API call with structured JSON output, then updates the
video snippet on YouTube and in the local and cloud databases.

Safeguards:
1. No duplicate descriptions: On retries or re-analysis, replaces existing AI section
   above the '---' divider instead of repeatedly appending.
2. Preserve internal tags: Preserves dispatch_id tags, session tags, and categoryId.
3. Failure isolation: If Gemini or YouTube update fails, keep original metadata and
   never block transcription or highlight clip generation.
4. Account retention: Uses the exact YouTube account credentials bound to the upload.
5. Database independence: The app/db never parses title/description text to reference records.
"""
import os
import re
import json
import logging
from typing import Dict, Any, Optional, List, Tuple
import requests

from dispatch.config import GEMINI_API_KEY
from dispatch.publisher.youtube import (
    get_authenticated_youtube_service,
    update_youtube_video_snippet,
)

logger = logging.getLogger("dispatch.youtube_inbox.enricher")

# Primary model: gemini-3.1-flash-lite, with resilient fallbacks
DEFAULT_ENRICHER_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-flash-latest"
]

ENRICHER_SYSTEM_PROMPT = """You are an expert YouTube title and description copywriter for video recordings.
You analyze video transcripts and generate:
1. An engaging, descriptive, creator-focused YouTube title (max 80-90 characters).
   - Crisp, authentic, human-sounding.
   - Strictly ZERO emojis (Never include emojis).
   - Do NOT use generic prefixes like "Title:", "Video:", "Episode:".
   - Reflect the actual core topic, story, or project discussed.
2. A 2-3 paragraph informative video description summarizing:
   - What the video discusses.
   - Key insights, arguments, or topics covered.
   - A concluding takeaway.
   - Clean, professional, creator-friendly tone without emojis.

Output valid JSON matching this schema:
{
  "title": "string",
  "description": "string"
}"""

ENRICHER_USER_PROMPT = """Here is the complete transcript of the recorded video segment:

---
{transcript_text}
---

Generate an authentic YouTube title and informative video description based on this transcript.
Return JSON with keys "title" and "description"."""

# Comprehensive regex stripping emojis to guarantee anti-ai invariant
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA70-\U0001FAFF"
    "]+",
    flags=re.UNICODE,
)


def clean_text_emojis(text: str) -> str:
    """Remove any native emoji characters from text."""
    if not text:
        return ""
    cleaned = EMOJI_PATTERN.sub("", text)
    # Collapse any resulting multi-spaces
    return re.sub(r" {2,}", " ", cleaned).strip()


def merge_description(existing_description: str, new_ai_description: str) -> str:
    """Merge new AI description with original Dispatch metadata footer idempotently.
    
    Safeguard 1: If an existing AI description is already present above '---',
    it is cleanly replaced so retries never create duplicate descriptions.
    """
    ai_text = clean_text_emojis(new_ai_description or "").strip()
    existing = (existing_description or "").strip()

    if not existing:
        return ai_text[:5000]

    # 1. Check if '---' divider already exists
    if "---" in existing:
        parts = existing.split("---", 1)
        footer = parts[1].strip()
        merged = f"{ai_text}\n\n---\n{footer}" if ai_text else footer
        return merged[:5000]

    # 2. Check for standard Dispatch footer anchors
    footer_markers = [
        "Preserved recording segment",
        "Recorded via Dispatch",
        "Session: #",
        "ID: "
    ]
    for marker in footer_markers:
        idx = existing.find(marker)
        if idx != -1:
            footer = existing[idx:].strip()
            merged = f"{ai_text}\n\n---\n{footer}" if ai_text else footer
            return merged[:5000]

    # 3. If no standard footer, append existing below '---'
    merged = f"{ai_text}\n\n---\n{existing}" if ai_text else existing
    return merged[:5000]


def generate_ai_metadata(
    transcript_text: str,
    user_id: Optional[str] = None,
    api_key: Optional[str] = None,
    candidate_models: Optional[List[str]] = None,
    timeout: int = 45,
) -> Optional[Dict[str, str]]:
    """Single Gemini API call generating title and description from transcript in structured JSON."""
    if not transcript_text or not transcript_text.strip():
        return None

    # Resolve active Gemini API key
    key = api_key
    if not key and user_id:
        try:
            from dispatch.youtube_connections import get_user_gemini_key
            key = get_user_gemini_key(user_id)
        except Exception:
            key = None

    if not key:
        key = os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY
    if not key:
        logger.warning("No GEMINI_API_KEY available for post-transcription metadata enrichment.")
        return None

    models_to_try = candidate_models or DEFAULT_ENRICHER_MODELS
    headers = {
        "x-goog-api-key": key,
        "Content-Type": "application/json"
    }

    # Bound transcript text to 40k chars to stay safely within limits
    bounded_transcript = transcript_text.strip()[:40000]

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": ENRICHER_SYSTEM_PROMPT},
                    {"text": ENRICHER_USER_PROMPT.format(transcript_text=bounded_transcript)}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.3,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "title": {
                        "type": "STRING",
                        "description": "Engaging creator title (under 90 chars, no emojis)"
                    },
                    "description": {
                        "type": "STRING",
                        "description": "2-3 paragraph overview of topics discussed and key insights"
                    }
                },
                "required": ["title", "description"]
            }
        }
    }

    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        try:
            logger.info("Requesting Gemini metadata enrichment using model '%s'...", model_name)
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)

            # Fallback if responseSchema is rejected by model
            if response.status_code == 400 and "responseSchema" in response.text:
                fallback_payload = {
                    "contents": payload["contents"],
                    "generationConfig": {
                        "temperature": 0.3,
                        "responseMimeType": "application/json"
                    }
                }
                response = requests.post(url, headers=headers, json=fallback_payload, timeout=timeout)

            if response.status_code == 200:
                data = response.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    continue

                content = candidates[0].get("content", {})
                parts = content.get("parts", [])
                if not parts or "text" not in parts[0]:
                    continue

                raw_text = parts[0]["text"].strip()
                parsed = json.loads(raw_text)

                if isinstance(parsed, dict):
                    raw_title = str(parsed.get("title", "")).strip()
                    raw_desc = str(parsed.get("description", "")).strip()

                    # Strip unwanted quotes or markdown formatting
                    clean_title = clean_text_emojis(raw_title).strip(' "\'')
                    clean_title = re.sub(r"^title\s*:\s*", "", clean_title, flags=re.IGNORECASE)
                    clean_desc = clean_text_emojis(raw_desc).strip()

                    if clean_title:
                        # Limit title to 100 characters per YouTube API spec
                        final_title = clean_title[:100]
                        logger.info("Gemini model '%s' generated title: '%s'", model_name, final_title)
                        return {
                            "title": final_title,
                            "description": clean_desc
                        }
            else:
                logger.warning("Gemini model %s returned status %d: %s", model_name, response.status_code, response.text[:200])

        except Exception as e:
            logger.warning("Error calling Gemini model %s for metadata enrichment: %s", model_name, e)

    return None


def enrich_youtube_video_metadata(
    video_id: str,
    transcript_text: str,
    user_id: Optional[str] = None,
    account_id: Optional[str] = None,
    youtube_service=None,
) -> Optional[Dict[str, Any]]:
    """Enrich YouTube video metadata from transcript using Gemini 3.1 Flash-Lite.
    
    Safeguards applied:
    - Failure isolation: Never raises; returns None on failure without disrupting callers.
    - Preserves tags and category on YouTube.
    - Idempotently merges description without duplicating text.
    - Updates local DB and cloud processing_jobs.
    """
    if not video_id or not transcript_text:
        return None

    words = transcript_text.strip().split()
    if len(words) < 5:
        logger.info("Transcript for %s has only %d words; skipping AI metadata generation", video_id, len(words))
        return None

    try:
        # 1. Resolve account_id if not passed
        if not account_id and user_id:
            try:
                from dispatch.youtube_connections import get_account_id_for_video
                account_id = get_account_id_for_video(video_id, user_id=user_id)
            except Exception:
                pass

        # 2. Call Gemini 3.1 Flash-Lite (single structured call)
        ai_meta = generate_ai_metadata(transcript_text, user_id=user_id)
        if not ai_meta or not ai_meta.get("title"):
            logger.warning("Gemini did not return valid metadata for %s", video_id)
            return None

        ai_title = ai_meta["title"]
        ai_desc = ai_meta.get("description", "")

        # 3. Fetch existing description from YouTube or local DB to merge
        existing_desc = ""
        service = youtube_service or get_authenticated_youtube_service(account_id=account_id, user_id=user_id)
        if service:
            try:
                resp = service.videos().list(part="snippet", id=video_id).execute()
                items = resp.get("items", [])
                if items:
                    existing_desc = items[0].get("snippet", {}).get("description", "")
            except Exception as e:
                logger.debug("Could not fetch existing snippet from YouTube for %s: %s", video_id, e)

        merged_desc = merge_description(existing_desc, ai_desc)

        # 4. Update YouTube video snippet
        yt_updated = False
        if service:
            yt_updated = update_youtube_video_snippet(
                video_id=video_id,
                title=ai_title,
                description=merged_desc,
                account_id=account_id,
                user_id=user_id,
                youtube_service=service
            )

        # 5. Update local database
        try:
            from dispatch import db
            db.update_youtube_video_title(video_id, ai_title)
        except Exception as e:
            logger.warning("Failed to update local db title for %s: %s", video_id, e)

        # 6. Update cloud processing_jobs source_title
        try:
            from dispatch.youtube_connections import update_job_title_by_video_id
            update_job_title_by_video_id(video_id, ai_title)
        except Exception as e:
            logger.debug("Failed to update cloud processing_jobs title for %s: %s", video_id, e)

        logger.info(
            "Enrichment completed for %s: Title='%s', YouTube updated=%s",
            video_id, ai_title, yt_updated
        )

        return {
            "title": ai_title,
            "description": merged_desc,
            "youtube_updated": yt_updated
        }
    except Exception as e:
        logger.warning("Post-transcription metadata enrichment failed for %s: %s", video_id, e)
        return None
