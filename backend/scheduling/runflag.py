"""A "stop when the current run ends" flag, shared between scheduler and runner.

Combo mode switches what the tower is doing at 8AM and 7PM, and the user's rule
for both is the same: "when the next run is over". Killing the runner process
outright would not honour that - it would drop a live run at whatever wave it
had reached, losing the coins and the run log with it.

So a phase change does not kill anything. It writes this flag, and the runner
notices at the ONE moment where stopping is free: the death handler, after the
run log is collected and before it would have restarted. The scheduler then
waits for the process to exit on its own.

A file rather than a signal because the runner is a separate process on Windows
(no SIGUSR1), and because a stale flag is visible and removable by hand.
"""
import os

from runtime import logger
from settings import CONFIG


def _path() -> str:
    d = os.path.join("logs", CONFIG.get("active_instance", "main"))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "stop_after_run")


def request(reason: str = "") -> None:
    with open(_path(), "w", encoding="utf-8") as fh:
        fh.write(reason)
    logger.event("stop_requested", reason=reason)


def requested() -> str | None:
    """The reason string if a stop is pending, else None."""
    try:
        with open(_path(), encoding="utf-8") as fh:
            return fh.read() or "(no reason)"
    except FileNotFoundError:
        return None


def clear() -> None:
    try:
        os.remove(_path())
    except FileNotFoundError:
        pass
