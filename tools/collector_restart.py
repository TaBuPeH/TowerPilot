"""Ask the running gem collector to leave at its next safe point, so new code can go live without killing it.

    python tools/collector_restart.py                      # ask, return at once
    python tools/collector_restart.py --wait 180           # ask, then wait until the collector has taken the request
    python tools/collector_restart.py --status             # is a request pending, and how old is it
    python tools/collector_restart.py --cancel             # withdraw a request nobody took

It only writes a file (scheduling/runflag.py, the owner of exit requests): no process is killed or signalled,
and nothing talks to the dashboard, the emulator or adb. The collector reads the file between polls, waits
until no ad claim and no orbit attempt is in progress, closes its battle record as interrupted
(reason `collector_restart`), deletes the file and exits normally. The dashboard's harvest tick relaunches a
harvest-managed collector as it does for any ended one; a normal exit does not count toward the failure
circuit or the launch backoff. A collector a person started by hand is not relaunched.

A request is for the collector running now: one written before a collector started is ignored by it.
Exit codes: 0 asked (and, with --wait, taken), 2 --wait ran out with the request still pending, 1 usage error.
"""
import argparse
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
DEFAULT_REASON = "restart for new code"


def main(argv=None, *, sleep=time.sleep, clock=time.monotonic, out=print):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true", help="show a pending request and change nothing")
    mode.add_argument("--cancel", action="store_true", help="delete a pending request")
    parser.add_argument("--reason", default=DEFAULT_REASON, help="recorded in the collector's log and battle record")
    parser.add_argument("--wait", type=float, default=0.0, metavar="SEC",
                        help="after asking, wait up to SEC seconds for the collector to take the request")
    parser.add_argument("--instance", default=None, help="emulator instance (default: active_instance in config.yaml)")
    parser.add_argument("--log-dir", default=None, help="instance log folder (default: backend/logs/<instance>)")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(BACKEND))
    import settings
    from scheduling import runflag
    log_dir = Path(args.log_dir) if args.log_dir else BACKEND / "logs" / (args.instance or settings.CONFIG["active_instance"])

    if args.status or args.cancel:
        found = runflag.collector_exit_requested(log_dir=log_dir)
        if found is None:
            out("no collector exit request is pending")
            return 0
        if args.cancel:
            runflag.clear_collector_exit(log_dir=log_dir)
            out(f"cancelled the pending request ({found['reason']})")
            return 0
        out(f"pending since {time.time() - found['requested_at']:.0f} s ago: {found['reason']}")
        return 0

    path = runflag.request_collector_exit(args.reason, log_dir=log_dir)
    out(f"asked the collector to leave at its next safe point: {path}")
    if args.wait <= 0:
        return 0
    deadline = clock() + args.wait
    while runflag.collector_exit_pending(log_dir=log_dir):
        if clock() >= deadline:
            out(f"still pending after {args.wait:g} s: the collector is busy, not running, or not a harvest-managed one;"
                " withdraw it with --cancel")
            return 2
        sleep(1.0)
    out("the collector took the request and is leaving; the dashboard relaunches it on its next tick")
    return 0


if __name__ == "__main__":
    sys.exit(main())
