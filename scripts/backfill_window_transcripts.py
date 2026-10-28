#!/usr/bin/env python3
"""Safely backfill cloud edit-window transcripts from local persisted transcripts.

The PC never invents transcripts. For each local clip this tool:

1. reads the best available SOURCE-relative word timings (edited caption track
   first, then persisted transcript segments);
2. if words exist, re-registers the already-staged window through the Worker,
   which persists window_transcripts idempotently and marks transcript_status;
3. if no words exist, reports the window as unrecoverable by this tool and
   explains the manual next step (run transcription for the source first).

Usage:
  python scripts/backfill_window_transcripts.py --dry-run
  python scripts/backfill_window_transcripts.py --limit 20
  python scripts/backfill_window_transcripts.py --clip-id clip_20261028_...
  python scripts/backfill_window_transcripts.py --owner-user-id <uuid>   # legacy unowned clips
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List

from dispatch import db
from dispatch.cloud.sync import (
    authorized_pc_owner_ids,
    extract_source_words,
    get_worker_token,
    push_window_transcript,
)


def _candidates(limit: int, clip_id: str | None) -> List[Dict[str, Any]]:
    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        if clip_id:
            cursor.execute("SELECT * FROM clips WHERE id = ?", (clip_id,))
        else:
            cursor.execute(
                """SELECT * FROM clips
                   WHERE status IN ('ready_review', 'approved', 'published')
                     AND video_path IS NOT NULL
                   ORDER BY created_at ASC
                   LIMIT ?""",
                (max(1, limit),),
            )
        return [dict(row) for row in cursor.fetchall()]


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report actions without contacting the Worker")
    parser.add_argument("--limit", type=int, default=20, help="maximum clips to inspect (default 20)")
    parser.add_argument("--clip-id", default=None, help="backfill one specific local clip id")
    parser.add_argument("--owner-user-id", default=None,
                        help="account to attribute LOCAL clips that have no user_id (legacy rows). "
                             "Must already be in DISPATCH_PC_OWNER_USER_IDS; the Worker still verifies ownership.")
    args = parser.parse_args(argv)

    if not get_worker_token() and not args.dry_run:
        print("DISPATCH_WORKER_TOKEN is not configured; cannot backfill cloud transcripts.", file=sys.stderr)
        return 2
    if args.owner_user_id and args.owner_user_id.strip() not in authorized_pc_owner_ids():
        print("--owner-user-id is not in DISPATCH_PC_OWNER_USER_IDS; refusing to touch another account.", file=sys.stderr)
        return 2

    clips = _candidates(args.limit, args.clip_id)
    if not clips:
        print(json.dumps({"status": "nothing_to_do", "clips": 0}))
        return 0

    results = []
    for clip in clips:
        clip_id = clip["id"]
        words = extract_source_words(clip)
        if not words:
            results.append({
                "clip_id": clip_id,
                "action": "unrecoverable_no_local_transcript",
                "next_step": "Run the transcription pipeline for this source (or edit captions on the PC), then re-run this tool.",
            })
            continue
        if args.dry_run:
            results.append({
                "clip_id": clip_id,
                "action": "would_push_transcript",
                "words": len(words),
                "first_word": words[0]["word"],
                "last_word": words[-1]["word"],
            })
            continue
        outcome = push_window_transcript(clip_id, clip, only_if_missing=True,
                                         owner_user_id=args.owner_user_id)
        action = {
            "pushed": "pushed_transcript",
            "skipped_existing": "skipped_existing_transcript",
            "no_words": "unrecoverable_no_local_transcript",
            "not_staged": "not_staged_window",
            "unauthorized_owner": "needs_owner_attribution",
            "failed": "push_failed",
            "no_credentials": "push_failed",
        }.get(outcome, "push_failed")
        entry = {
            "clip_id": clip_id,
            "action": action,
            "words": len(words),
        }
        if action == "needs_owner_attribution":
            entry["next_step"] = (
                "This local clip has no user_id and no single-profile owner could be "
                "resolved. Re-run with --owner-user-id <uuid> for an account listed in "
                "DISPATCH_PC_OWNER_USER_IDS."
            )
        results.append(entry)

    summary = {
        "status": "dry_run" if args.dry_run else "complete",
        "clips": len(results),
        "pushed": sum(1 for r in results if r["action"] == "pushed_transcript"),
        "would_push": sum(1 for r in results if r["action"] == "would_push_transcript"),
        "skipped_existing": sum(1 for r in results if r["action"] == "skipped_existing_transcript"),
        "not_staged": sum(1 for r in results if r["action"] == "not_staged_window"),
        "failed": sum(1 for r in results if r["action"] == "push_failed"),
        "needs_owner": sum(1 for r in results if r["action"] == "needs_owner_attribution"),
        "unrecoverable": sum(1 for r in results if r["action"] == "unrecoverable_no_local_transcript"),
        "results": results,
    }
    print(json.dumps(summary, indent=2))
    return 1 if (summary["failed"] or summary["needs_owner"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
