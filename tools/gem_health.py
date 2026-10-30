"""Read-only live gem-collection health check for the running dashboard.

Run after every change that reaches the live collector (docs/FOCUS.md, definition
of done) and compare with the previous run:

    python tools/gem_health.py            # current collector
    python tools/gem_health.py --hours 3  # only the last 3 hours

It only sends GET requests to the dashboard (default http://127.0.0.1:8620) and
never touches the emulator, adb or any process.
"""
import argparse
import collections
import datetime
import json
import sys
import time
import urllib.request


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=15) as response:
        return json.load(response)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://127.0.0.1:8620")
    parser.add_argument("--hours", type=float, default=None,
                        help="only count executions started in the last N hours (default: the current collector)")
    args = parser.parse_args(argv)

    harvest = get(args.base, "/api/harvest")["harvest"]
    op = harvest.get("operation") or {}
    print(f"harvest: {harvest['code']} ({harvest['state']})")
    if not op:
        print("no collector operation is active")
    else:
        print(f"collector: {op.get('id', '')[:8]} status={op.get('status')} worker_alive={op.get('worker_alive')}")

    rows = get(args.base, "/api/actions/executions?emulator=main&limit=500")["executions"]
    if args.hours is not None:
        since = time.time() - args.hours * 3600
        rows = [r for r in rows if r["started_at"] >= since]
        scope = f"last {args.hours:g} h"
    else:
        rows = [r for r in rows if op and r["host"].get("operation_id") == op.get("id")]
        scope = "current collector"
    if not rows:
        print(f"no executions in scope ({scope})")
        return 1

    def clock(t):
        return datetime.datetime.fromtimestamp(t).strftime("%m-%d %H:%M")

    first, last = min(r["started_at"] for r in rows), max(r["started_at"] for r in rows)
    print(f"executions: {len(rows)} ({scope}, {clock(first)} to {clock(last)})")
    counts = collections.Counter((r["action"], r["outcome"]["result"], r["outcome"].get("reason")) for r in rows)
    for (action, result, reason), n in sorted(counts.items(), key=str):
        print(f"  {action:<15} {result:<12} {reason or '':<40} {n}")

    for action in ("ad_gems", "floating_gems"):
        own = sorted((r for r in rows if r["action"] == action), key=lambda r: r["started_at"])
        ok = [r for r in own if r["outcome"]["result"] == "succeeded"]
        last_ok = clock(ok[-1]["started_at"]) if ok else "never"
        gaps = [round(b["started_at"] - a["started_at"]) for a, b in zip(own, own[1:])][-6:]
        print(f"{action}: {len(ok)}/{len(own)} succeeded, last success {last_ok}, recent spacing (s) {gaps}")
    return 0


if __name__ == "__main__":
    sys.exit(main())