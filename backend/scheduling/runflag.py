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

The gem collector has a second request in this module, `collector_exit`: "leave at the
next safe point" so new code can go live without killing it. It is a separate file
because it means something else: a run's death handler must never read it, and the
collector acts on it mid-battle, not only at a battle boundary. Only the collector
reads it, and only when it was written after that collector started (see
`collector_exit_requested`), so a request that found no collector cannot make the
next one leave the moment it launches.
"""
import json
import os
import time

from runtime import logger
from settings import CONFIG

STOP_FILE = "stop_after_run"
COLLECTOR_EXIT_FILE = "collector_exit"


def _dir(log_dir=None, create=False) -> str:
    d = str(log_dir) if log_dir is not None else os.path.join("logs", CONFIG.get("active_instance", "main"))
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def _path(name=STOP_FILE, *, log_dir=None, create=True) -> str:
    return os.path.join(_dir(log_dir, create), name)


def _retry(action, attempts=6, pause=0.05):
    """Run a file operation, retrying while Windows refuses it because the other side (the collector's read, the
    CLI's delete) has the file open for a moment. The last refusal is raised."""
    for left in range(attempts - 1, -1, -1):
        try:
            return action()
        except PermissionError:
            if not left:
                raise
            time.sleep(pause)


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


def request_collector_exit(reason: str, *, log_dir=None, now: float | None = None) -> str:
    """Ask the running gem collector to leave at its next safe point. Writes the request file and returns its path.

    Nothing is killed or signalled and no event is logged (a person's CLI must not start an event log file). The
    collector deletes the file when it leaves. `log_dir` is the instance log folder; by default the one the
    process runs in (backend/logs/<active instance>)."""
    path = _path(COLLECTOR_EXIT_FILE, log_dir=log_dir)
    body = json.dumps(dict(reason=reason or "(no reason)", requested_at=time.time() if now is None else now))
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as fh:
        fh.write(body)
    _retry(lambda: os.replace(temp, path))
    return path


def collector_exit_requested(not_before: float = 0.0, *, log_dir=None) -> dict | None:
    """The pending exit request as {reason, requested_at}, else None. A request written before `not_before` (the
    reading collector's own start) was meant for an earlier collector and is ignored. A file that is not this
    module's JSON (written by hand) counts with its text as the reason and its modification time as its age."""
    path = _path(COLLECTOR_EXIT_FILE, log_dir=log_dir, create=False)
    def read():
        with open(path, encoding="utf-8") as fh:
            return fh.read(), os.path.getmtime(path)
    try:
        text, stamp = _retry(read)
    except (FileNotFoundError, PermissionError):     # gone, or held open by a writer: the caller reads again next pass
        return None
    try:
        body = json.loads(text)
        found = dict(reason=str(body["reason"]), requested_at=float(body["requested_at"]))
    except (ValueError, KeyError, TypeError):
        found = dict(reason=text.strip() or "(no reason)", requested_at=stamp)
    return found if found["requested_at"] >= not_before else None


def collector_exit_pending(*, log_dir=None) -> bool:
    """Whether the request file exists, readable or not (a person waiting for the collector asks this)."""
    return os.path.exists(_path(COLLECTOR_EXIT_FILE, log_dir=log_dir, create=False))


def clear_collector_exit(*, log_dir=None) -> bool:
    """Delete the exit request. True if one was deleted. Never raises for a file another process holds open: the
    planned exit must end normally (a raise there would be recorded as a failed collector); the file is then left
    for a person to cancel, and a collector that starts later ignores it as older than itself."""
    try:
        _retry(lambda: os.remove(_path(COLLECTOR_EXIT_FILE, log_dir=log_dir, create=False)))
    except (FileNotFoundError, PermissionError):
        return False
    return True
