"""Disk retention for the data the autopilot writes forever.

The screenshot flood is real: the danger window shoots ~1 frame/s at deep
waves (~22 GB of PNGs a day) and nothing ever deleted them - logs/ hit
220 GB on 2026-08-21 before the first manual sweep. Retention, enforced
once a day by the housekeeping chore (chores.py registry):

  * danger_* shots: 1 day. Their only value is post-morteming the current
    runs; nobody has ever read a week-old danger frame.
  * every other logs/<instance> PNG: 14 days. These are rare and
    diagnostic (death, sw, fleet_nuke, off_battle, rescue, template
    misses) - a few hundred MB standing, worth the history.
  * events_*.jsonl: 60 days. Small files, but one per process start.
  * runs/, templates/, profiles/, captures/, recordings/: never touched
    here. Run stats are the account's history; templates are ground
    truth.

Disk-only: reads and unlinks files, never looks at the screen, so the
"only from Home" chore rule is satisfied by construction. Ages are
measured in seconds against mtime - no find(1)-style whole-day rounding.
"""
import glob
import os
import time

DANGER_KEEP_SEC = 1 * 86400
SHOT_KEEP_SEC = 14 * 86400
EVENTS_KEEP_SEC = 60 * 86400

# BACKEND ROOT, not this file's directory. The 2026-08-24 package
# restructure moved this file into scheduling/ and the old script-dir
# anchor silently swept the nonexistent scheduling/logs/ for a week -
# "removed 0, freed 0.0 MB, ok: true" while logs/ grew to 118 GB
# (caught by the user 2026-09-01: "your cleanup is not working").
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sweep() -> dict:
    """Apply retention under logs/*/; returns {removed counts, freed MB}."""
    logs_dir = os.path.join(ROOT, "logs")
    if not os.path.isdir(logs_dir):
        # fail LOUD: an empty glob over a wrong root is indistinguishable
        # from a clean disk, which is exactly how the anchor bug hid.
        raise RuntimeError(f"logs dir not found at {logs_dir} - "
                           "housekeeping root is wrong, nothing swept")
    now = time.time()
    removed = {"danger": 0, "shot": 0, "events": 0}
    freed = 0
    for path in glob.glob(os.path.join(logs_dir, "*", "*")):
        name = os.path.basename(path)
        if name.endswith(".png"):
            kind = "danger" if "_danger_" in name else "shot"
            keep = DANGER_KEEP_SEC if kind == "danger" else SHOT_KEEP_SEC
        elif name.startswith("events_") and name.endswith(".jsonl"):
            kind, keep = "events", EVENTS_KEEP_SEC
        else:
            continue
        try:
            st = os.stat(path)
            if now - st.st_mtime <= keep:
                continue
            os.remove(path)
            removed[kind] += 1
            freed += st.st_size
        except OSError:
            continue    # a locked/live file or a race: tomorrow's sweep
    return {"removed": removed, "freed_mb": round(freed / 1e6, 1)}
