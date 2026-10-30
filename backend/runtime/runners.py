"""Exclusive-device runner detection shared by the setup preflights."""

import threading
from pathlib import Path

# Script basenames that drive the device. A python process whose argv names
# one of them holds the device and excludes every other runner.
RUNNER_SCRIPTS = frozenset({"orchestrator.py", "shard.py", "combo.py", "tourney.py", "quest_sm.py",
                            "quest_ilm.py", "scan.py", "calibrate.py", "clicker.py", "boot.py", "harness.py",
                            "gem_collector.py", "coin.py", "tournament.py", "tier_ladder.py",
                            "operation_worker.py", "hp_probe.py"})

# psutil.process_iter keeps ONE Process object per pid across calls and
# rebinds its `.info` to the attrs of whichever call iterated last. Two
# threads of one process scanning at once (the dashboard's runner refresher
# and its emulator listing, attrs ["name"]) left the refresher reading an
# info dict with no "pid": KeyError in the launcher log, 2026-10-19/29.
_PROCESS_ITER_LOCK = threading.Lock()


def process_rows(attrs):
    """[(process, info)] for every process, info copied under one lock.

    Every process_iter(attrs) caller in a threaded process goes through
    here, so no other scan can rebind `.info` between the read and its use."""
    import psutil
    with _PROCESS_ITER_LOCK:
        return [(p, dict(p.info)) for p in psutil.process_iter(attrs)]


def other_runners(target=None):
    """Yield (pid, script basename, argv) for every python process, other than
    this one and its ancestors, whose argv names a RUNNER_SCRIPTS basename.

    Ancestors are skipped because a venv launcher (uv's Scripts/python.exe on
    Windows) starts the real interpreter as a CHILD with the same script
    arguments, so this process's own launcher would otherwise read as a
    second runner. A runner that is not this process's ancestor is still yielded."""
    import psutil
    me = psutil.Process()
    skip = {me.pid} | {a.pid for a in me.parents()}
    for _p, info in process_rows(["pid", "name", "cmdline"]):
        if info["pid"] in skip or "python" not in (info["name"] or "").lower():
            continue
        args = info["cmdline"] or []
        script = next((Path(a).name for a in args if Path(a).name in RUNNER_SCRIPTS), None)
        if script:
            from runtime.device_lease import owns_different_device
            if owns_different_device(info["pid"], target=target):
                continue
            yield info["pid"], script, args
