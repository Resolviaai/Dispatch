#!/usr/bin/env python3
"""Manually drain queued phone-to-PC clip commands for one account.

Use this when a cloud clip_commands row is stuck `pending` with zero attempts
(for example the PC was offline, or DISPATCH_PC_OWNER_USER_IDS was unset).
The operation is idempotent: reject/restore commands are applied through the
same durable state machine as the background sync.

Usage:
  python scripts/drain_clip_commands.py --user-id <uuid>
  python scripts/drain_clip_commands.py --user-id <uuid> --limit 5
"""

from __future__ import annotations

import argparse
import json
import sys

from dispatch.cloud.sync import authorized_pc_owner_ids, drain_clip_commands, get_worker_token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True, help="Supabase user UUID that owns the clips")
    parser.add_argument("--limit", type=int, default=25, help="maximum commands to claim (default 25)")
    args = parser.parse_args()

    if not get_worker_token():
        print("DISPATCH_WORKER_TOKEN is not configured; cannot claim commands.", file=sys.stderr)
        return 2
    user_id = args.user_id.strip()
    if not user_id:
        print("--user-id must not be empty", file=sys.stderr)
        return 2
    owners = authorized_pc_owner_ids()
    if owners and user_id not in owners:
        print("--user-id is not in DISPATCH_PC_OWNER_USER_IDS; refusing to drain another account.", file=sys.stderr)
        return 2
    if not owners:
        print("Warning: DISPATCH_PC_OWNER_USER_IDS is empty; the Worker must authorize this account.", file=sys.stderr)

    applied = drain_clip_commands(limit=max(1, args.limit), user_ids=[user_id])
    print(json.dumps({"status": "complete", "applied": applied}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
