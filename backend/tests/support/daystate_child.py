"""Child process of test_daystate_serialization (not a test): runs a few daystate operations against a temporary state file, optionally paused inside its first
write step - between the load and the save - so the test can run another process into exactly that gap. Spec: JSON in the environment variable TP_DAYSTATE_CHILD.

  state, sync   the temporary state file and the folder the two children signal each other through
  role, other   this child's name and the other's ('A' / 'B')
  after         wait until the other child has made this signal ('loaded' = it is inside its write step)
  pause         pause inside the first save until the other child signals 'done' (at most pause_sec)
  ops           [[name, key, ...], ...] run in order by each of `threads` threads, `repeat` times: set_today, increment_today, mark_today, set_raw, clear,
                old_increment (get_today then set_today: the pattern that is NOT protected)
"""
import json
import os
import sys
import threading
import time
from pathlib import Path

spec = json.loads(os.environ["TP_DAYSTATE_CHILD"])
from scheduling import daystate          # noqa: E402

daystate.STATE = spec["state"]
sync, role, other = Path(spec["sync"]), spec["role"], spec["other"]
sync.mkdir(parents=True, exist_ok=True)


def mark(name):
    (sync / f"{role}_{name}").write_text("1")


def seen(name):
    return (sync / f"{other}_{name}").exists()


def wait(condition, limit):
    end = time.monotonic() + limit
    while time.monotonic() < end and not condition():
        time.sleep(0.01)
    return condition()


if spec.get("after"):
    wait(lambda: seen(spec["after"]), 30)
if spec.get("pause"):
    real_save, state = daystate._save, {"paused": False}

    def save(st):
        if not state["paused"]:
            state["paused"] = True
            mark("loaded")
            wait(lambda: seen("done"), spec.get("pause_sec", 2.0))
        real_save(st)
    daystate._save = save

errors = []


def run_ops():
    for _ in range(spec.get("repeat", 1)):
        for name, key, *args in spec["ops"]:
            try:
                if name == "old_increment":
                    value = daystate.get_today(key)
                    mark("read")
                    wait(lambda: seen("read"), 5)
                    daystate.set_today(key, value + 1)
                else:
                    getattr(daystate, name)(key, *args)
            except Exception as exc:          # noqa: BLE001 - reported to the test, which asserts there are none
                errors.append(f"{type(exc).__name__}: {exc}")


threads = [threading.Thread(target=run_ops) for _ in range(spec.get("threads", 1))]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
mark("done")
print(json.dumps({"errors": errors}))
sys.exit(0)
