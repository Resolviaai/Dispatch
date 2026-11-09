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
      3. Ignores generic uninformative default reasons ('Rejected from review studio', 'No reason provided')
         to prevent token bloat and unhelpful negative conditioning (T07).
      4. Preserves compatibility with existing database schema and tests.
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

    meaningful_count = 0
    for r in rejections:
        title = (r.get("title") or "Untitled").strip()
        raw_reason = (r.get("reason") or "").strip()
        snippet = (r.get("transcript_snippet") or "").strip()

        # T07: Skip generic default rejection reasons that contain no editorial feedback
        if not raw_reason or raw_reason.lower() in (
            "rejected from review studio",
            "no reason provided",
            "rejected",
            "none",
            "n/a",
        ):
            continue

        meaningful_count += 1
        lines.append(f"- Rejected Example {meaningful_count}: '{title}' | Reason: {raw_reason} | Excerpt: \"{snippet[:100]}...\"")

        r_lower = raw_reason.lower()
        if any(w in r_lower for w in ("slow", "boring", "fast", "pace", "energy", "talking")):
            pacing_critiques.append(raw_reason)
        elif any(w in r_lower for w in ("cut", "mid-sentence", "start", "end", "boundary", "incomplete", "unfinished")):
            boundary_critiques.append(raw_reason)
        elif any(w in r_lower for w in ("context", "confusing", "understand", "standalone", "setup")):
            context_critiques.append(raw_reason)
        elif any(w in r_lower for w in ("repeat", "duplicate", "again", "similar", "redundant")):
            duplicate_critiques.append(raw_reason)
        else:
            other_critiques.append(raw_reason)

    if meaningful_count == 0:
        return ""

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

def get_positive_style_anchors(user_id: str | None = None, limit: int = 3) -> str:
    """Retrieve recent user-approved clips to act as gold-standard few-shot style anchors.

    Invariants:
      1. Grounds LLM in actual real-world approved boundaries, hooks, and titles.
      2. Owner-scoped: never leaks one user's private approved clips to another user.
      3. Returns empty string if no approved clips exist yet.
    """
    approved = db.get_recent_approved_clips(limit=limit, user_id=user_id)
    if not approved:
        return ""

    lines = [
        "\nUSER-APPROVED HIGH-PERFORMANCE STYLE ANCHORS (POSITIVE USER PREFERENCES):",
        "The creator previously approved and published these clips. Emulate their high-energy opening hooks, direct-response titles, and punchy complete payoffs:"
    ]
    for idx, c in enumerate(approved, 1):
        title = (c.get("title") or "").strip()
        hook = (c.get("hook") or "").strip()
        dur = float(c.get("duration") or 0.0)
        lines.append(f"- Approved Anchor #{idx}: Title: \"{title}\" | Hook: \"{hook}\" | Duration: {dur:.1f}s")

    return "\n".join(lines)


def get_editorial_guidance_prompt(user_id: str | None = None) -> str:
    """Combines negative rejection feedback with positive approved style anchors."""
    neg = get_negative_feedback_prompt(user_id)
    pos = get_positive_style_anchors(user_id)
    parts = [p for p in (neg, pos) if p.strip()]
    return "\n\n".join(parts) if parts else ""

