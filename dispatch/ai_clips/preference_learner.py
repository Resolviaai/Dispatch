"""Preference learning engine for Dispatch.
Analyzes user approvals and rejections to dynamically improve clip extraction.
"""
from typing import List, Dict, Any
from dispatch import db


def get_negative_feedback_prompt() -> str:
    """Retrieve recent rejections from SQLite and construct negative conditioning prompt."""
    rejections = db.get_all_rejections(limit=15)
    if not rejections:
        return ""

    lines = [
        "\nNEGATIVE USER PREFERENCES (The user has previously REJECTED these types of clips, so DO NOT select clips like these):"
    ]
    for idx, r in enumerate(rejections, 1):
        title = r.get("title", "Untitled")
        reason = r.get("reason", "No reason provided")
        snippet = r.get("transcript_snippet", "")
        lines.append(f"- Rejected Example {idx}: '{title}' | Reason: {reason} | Excerpt: \"{snippet[:100]}...\"")

    lines.append(
        "Strictly avoid low-energy sections, repetitive musings, unfinished thoughts, or topics similar to the rejected examples above."
    )
    return "\n".join(lines)
