"""Preference learning engine for Dispatch.
Analyzes user approvals and rejections to dynamically improve clip extraction
without overfitting or creating rigid topic blacklists.
"""
from typing import List, Dict, Any
from dispatch import db


def get_negative_feedback_prompt(user_id: str | None = None) -> str:
    """Retrieve recent rejections from SQLite and construct nuanced negative conditioning prompt.

    Invariants:
      1. Explains why clips were rejected (pacing, speech boundary cutoffs, lack of context, duplication)
         to guide editorial selection and boundary setting.
      2. Strictly instructs the model NOT to blacklist entire subject matters or topics.
      3. Preserves compatibility with existing database schema and tests.
    """
    rejections = db.get_all_rejections(limit=15, user_id=user_id)
    if not rejections:
        return ""

    lines = [
        "\nUSER EDITORIAL PREFERENCES & PREVIOUS REJECTIONS (NEGATIVE USER PREFERENCES):"
    ]

    pacing_critiques = []
    boundary_critiques = []
    context_critiques = []
    duplicate_critiques = []
    other_critiques = []

    for idx, r in enumerate(rejections, 1):
        title = r.get("title", "Untitled")
        reason = r.get("reason", "No reason provided")
        snippet = r.get("transcript_snippet", "")
        lines.append(f"- Rejected Example {idx}: '{title}' | Reason: {reason} | Excerpt: \"{snippet[:100]}...\"")

        r_lower = reason.lower()
        if any(w in r_lower for w in ("slow", "boring", "fast", "pace", "energy", "talking")):
            pacing_critiques.append(reason)
        elif any(w in r_lower for w in ("cut", "mid-sentence", "start", "end", "boundary", "incomplete", "unfinished")):
            boundary_critiques.append(reason)
        elif any(w in r_lower for w in ("context", "confusing", "understand", "standalone", "setup")):
            context_critiques.append(reason)
        elif any(w in r_lower for w in ("repeat", "duplicate", "again", "similar", "redundant")):
            duplicate_critiques.append(reason)
        else:
            other_critiques.append(reason)

    lines.append("\nHOW TO APPLY THIS FEEDBACK (DO NOT OVERFIT OR BLACKLIST TOPICS):")
    lines.append(
        "- Treat rejections as feedback on EDITORIAL EXECUTION (pacing, boundary precision, standalone context), NOT as a ban on entire subjects."
    )
    lines.append(
        "- DO NOT blacklist or avoid a topic simply because an earlier clip on that subject had poor pacing or incomplete boundaries."
    )

    if pacing_critiques:
        lines.append("- Pacing note: The user has rejected clips for sluggish pacing or rambling. Prioritize tighter, punchier delivery without dead air.")
    if boundary_critiques:
        lines.append("- Boundary note: The user has rejected clips that started mid-sentence or cut off prematurely. Ensure complete thoughts with clean setups and payoffs.")
    if context_critiques:
        lines.append("- Context note: The user has rejected clips that were confusing without broader context. Ensure standalone completeness.")
    if duplicate_critiques:
        lines.append("- Diversity note: The user has rejected redundant clips. Ensure candidate clips are distinct and cover different moments.")

    return "\n".join(lines)
