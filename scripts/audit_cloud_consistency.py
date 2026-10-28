#!/usr/bin/env python3
"""Read-only cloud consistency audit (no writes, no deletions).

Calls the Worker's owner-scoped consistency report and prints counts plus sample
window ids. Exit code 1 means at least one category is non-zero.

Usage:
  python scripts/audit_cloud_consistency.py --user-id <uuid>
  python scripts/audit_cloud_consistency.py --user-id <uuid> --json
"""

from __future__ import annotations

import argparse
import json
import sys

import requests

from dispatch.cloud.sync import _cloud_headers, _worker_base, get_worker_token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True, help="Supabase user UUID to audit")
    parser.add_argument("--json", action="store_true", help="print raw JSON only")
    args = parser.parse_args()

    token = get_worker_token()
    if not token:
        print("DISPATCH_WORKER_TOKEN is not configured.", file=sys.stderr)
        return 2

    try:
        res = requests.get(
            f"{_worker_base()}/sync/pc/consistency",
            headers=_cloud_headers(token, args.user_id.strip()),
            timeout=30,
        )
    except Exception as e:
        print(f"consistency request failed: {e}", file=sys.stderr)
        return 2
    if not res.ok:
        print(f"consistency request failed: HTTP {res.status_code} {res.text[:200]}", file=sys.stderr)
        return 2

    body = res.json() if res.text else {}
    report = body.get("report") if isinstance(body, dict) else None
    if not isinstance(report, dict):
        print("Worker returned no consistency report.", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Consistency report for {report.get('user_id', args.user_id)}")
        issues = 0
        for key, value in report.items():
            if key.endswith("_windows") or key == "user_id":
                continue
            if isinstance(value, int) and value > 0:
                issues += value
                print(f"  {key}: {value}")
            elif isinstance(value, int):
                print(f"  {key}: 0")
        samples = report.get("sample_missing_transcript_windows") or []
        if samples:
            print(f"  sample windows missing transcripts: {', '.join(map(str, samples))}")
        print("No consistency issues found." if issues == 0 else f"{issues} issue(s) found (read-only report).")

    numeric = [v for k, v in report.items() if isinstance(v, int) and k != "user_id"]
    return 1 if any(v > 0 for v in numeric) else 0


if __name__ == "__main__":
    raise SystemExit(main())
