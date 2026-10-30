"""scheduling/daystate.py: every write is one locked step (roadmap item 7, row R-0).

The races are forced, not hoped for. Child processes (daystate_child.py) run real daystate operations against a temporary state file; the first child pauses
INSIDE its write step - after it has loaded the file, before it saves - and the second child runs into exactly that gap:

  * without a lock the second child completes in the gap and the first then saves its stale copy over it: a key is lost (distinct keys), or an increment is lost
    (the same key);
  * with the lock the second child cannot enter the gap, so it waits, and both writes survive.

The same tests were run against the unchanged module (the lost update is real today): see characterization in the package.
"""

import paths as test_paths
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from runtime import file_lock
from scheduling import daystate

BACKEND = test_paths.BACKEND
CHILD = test_paths.TESTS / "support" / "daystate_child.py"


def start(tmp_path, **spec):
    spec = dict(state=str(tmp_path / "daily_state.json"), sync=str(tmp_path / "sync"), **spec)
    env = dict(os.environ, PYTHONPATH=str(BACKEND), TP_DAYSTATE_CHILD=json.dumps(spec), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.Popen([sys.executable, str(CHILD)], cwd=str(BACKEND.parent), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def finish(process):
    out, err = process.communicate(timeout=180)
    assert process.returncode == 0, err
    return json.loads(out.strip().splitlines()[-1])


def stored(tmp_path):
    return json.loads((tmp_path / "daily_state.json").read_text())


def race(tmp_path, first_ops, second_ops, **second):
    """The first child pauses inside its first write; the second starts when the first is in the gap. Returns both children's error lists."""
    a = start(tmp_path, role="A", other="B", ops=first_ops, pause=True, pause_sec=2.0)
    b = start(tmp_path, role="B", other="A", ops=second_ops, after="loaded", **second)
    return finish(a)["errors"], finish(b)["errors"]


# ------------------------------------------------------------------ the two lost updates (forced)
def test_two_processes_writing_different_keys_lose_neither(tmp_path):
    errors = race(tmp_path, [["set_today", "alpha", 1]], [["set_raw", "beta", {"odd": "shape"}]])
    assert errors == ([], []), errors
    state = stored(tmp_path)
    assert state["alpha"]["value"] == 1 and state["beta"] == {"odd": "shape"}, state       # the unprotected module saves A's copy over B's key


def test_two_processes_incrementing_one_key_end_at_exactly_two(tmp_path):
    errors = race(tmp_path, [["increment_today", "runs"]], [["increment_today", "runs"]])
    assert errors == ([], []), errors
    assert stored(tmp_path)["runs"]["value"] == 2


def test_a_flag_written_in_the_gap_is_not_lost_and_clear_is_serialized(tmp_path):
    (tmp_path / "daily_state.json").write_text(json.dumps({"keep": "2001-01-01", "drop": 1}))
    errors = race(tmp_path, [["clear", "drop"]], [["mark_today", "chore_scan"]])
    assert errors == ([], []), errors
    state = stored(tmp_path)
    assert "drop" not in state and state["keep"] == "2001-01-01" and state["chore_scan"] == daystate._today(), state


def test_a_get_followed_by_a_set_is_not_protected_which_is_why_counters_use_increment_today(tmp_path):
    """The pattern the callers used before (flows/shard.py, scheduling/combo.py): two steps, so two writers read n and both write n + 1. Documented, not fixed:
    the lock cannot join two calls. If this test ever fails, a caller can drop increment_today - and the rule in the daystate docstring changes."""
    a = start(tmp_path, role="A", other="B", ops=[["old_increment", "runs"]])
    b = start(tmp_path, role="B", other="A", ops=[["old_increment", "runs"]])
    assert finish(a)["errors"] == [] and finish(b)["errors"] == []
    assert stored(tmp_path)["runs"]["value"] == 1


# ------------------------------------------------------------------ volume: processes and threads
def test_many_increments_from_two_processes_and_many_threads_lose_none(tmp_path):
    ops = [["increment_today", "runs"], ["increment_today", "other_runs", 2]]
    a = start(tmp_path, role="A", other="B", ops=ops, threads=4, repeat=15)
    b = start(tmp_path, role="B", other="A", ops=ops, threads=4, repeat=15)
    assert finish(a)["errors"] == [] and finish(b)["errors"] == []
    state = stored(tmp_path)
    assert state["runs"]["value"] == 2 * 4 * 15 and state["other_runs"]["value"] == 2 * 4 * 15 * 2, state


def test_threads_of_one_process_lose_no_increment(tmp_path, monkeypatch):
    monkeypatch.setattr(daystate, "STATE", str(tmp_path / "daily_state.json"))
    errors = []

    def work():
        try:
            for _ in range(25):
                daystate.increment_today("runs")
        except Exception as exc:          # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [] and daystate.get_today("runs") == 200


# ------------------------------------------------------------------ what the file means does not change
def test_day_rollover_markers_and_odd_shapes_survive_an_increment(tmp_path, monkeypatch):
    monkeypatch.setattr(daystate, "STATE", str(tmp_path / "daily_state.json"))
    (tmp_path / "daily_state.json").write_text(json.dumps({
        "runs": {"date": "2001-01-01", "value": 99},                     # yesterday's count must read as 0, never bleed into today
        "chore_a": "2001-01-01", "alpha:store": "2026-10-23", "alpha:daily:next": 1799999999.5, "nested": {"x": [1, 2]}}))
    assert daystate.get_today("runs") == 0
    assert daystate.increment_today("runs") == 1 and daystate.increment_today("runs", 4) == 5
    state = stored(tmp_path)
    assert state["runs"] == {"date": daystate._today(), "value": 5}
    assert (state["chore_a"], state["alpha:store"], state["alpha:daily:next"], state["nested"]) == ("2001-01-01", "2026-10-23", 1799999999.5, {"x": [1, 2]})
    daystate.mark_today("flag")
    assert stored(tmp_path)["flag"] == daystate._today() and daystate.flag_today("flag")      # a flag is still a bare ISO date


def test_the_day_changes_inside_the_lock_not_before_it(tmp_path, monkeypatch):
    monkeypatch.setattr(daystate, "STATE", str(tmp_path / "daily_state.json"))
    days = iter(["2026-10-22", "2026-10-23"])                 # exactly one read of the day per increment
    monkeypatch.setattr(daystate, "_today", lambda: next(days))
    assert daystate.increment_today("runs") == 1             # counted on the 22nd
    assert daystate.increment_today("runs") == 1             # the 23rd starts again; the 22nd's count does not carry over


def test_update_replaces_the_raw_value_and_returns_it(tmp_path, monkeypatch):
    monkeypatch.setattr(daystate, "STATE", str(tmp_path / "daily_state.json"))
    assert daystate.update("token", lambda current: (current or 0) + 5) == 5
    assert daystate.update("token", lambda current: current + 1) == 6 and daystate.get_raw("token") == 6


def test_a_write_started_inside_a_write_step_is_refused_not_silently_lost(tmp_path, monkeypatch):
    """A nested write would be saved over by the outer step's older copy of the file. It must fail loudly, leave the lock free, and lose nothing already written."""
    monkeypatch.setattr(daystate, "STATE", str(tmp_path / "daily_state.json"))
    monkeypatch.setattr(daystate, "LOCK_WAIT_SEC", 2.0)
    daystate.set_raw("before", 1)
    with pytest.raises(RuntimeError, match="inside a daystate write step"):
        daystate.update("outer", lambda current: (daystate.increment_today("inner"), "done")[1])
    assert daystate.get_raw("before") == 1 and daystate.get_raw("outer") is None and daystate.get_today("inner") == 0
    assert daystate.increment_today("later") == 1                                         # the failed step released the lock


# ------------------------------------------------------------------ the lock
def test_a_lock_that_stays_held_times_out_logs_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(daystate, "STATE", str(tmp_path / "daily_state.json"))
    monkeypatch.setattr(daystate, "LOCK_WAIT_SEC", 0.3)
    (tmp_path / "daily_state.json").write_text(json.dumps({"before": "kept"}))
    from runtime import logger
    events = []
    monkeypatch.setattr(logger, "event", lambda kind, **fields: events.append((kind, fields)))
    holder = file_lock.try_lock(str(tmp_path / "daily_state.json") + ".lock")
    try:
        started = time.monotonic()
        with pytest.raises(daystate.DaystateLockTimeout):
            daystate.increment_today("runs")
        assert 0.25 < time.monotonic() - started < 3
        assert [kind for kind, _ in events] == ["daystate_lock_timeout"]
        assert stored(tmp_path) == {"before": "kept"}, "a timed-out write must not change the file"
        assert daystate.get_raw("before") == "kept" and daystate.flag_today("nothing") is False, "reads take no lock"
    finally:
        file_lock.unlock(holder)
    assert daystate.increment_today("runs") == 1             # and the module works again once the holder lets go


def test_a_holder_that_is_killed_releases_the_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(daystate, "STATE", str(tmp_path / "daily_state.json"))
    code = ("import sys, time; from scheduling import daystate; daystate.STATE = sys.argv[1]\n"
            "with daystate._locked():\n    print('locked', flush=True)\n    time.sleep(60)\n")
    child = subprocess.Popen([sys.executable, "-c", code, str(tmp_path / "daily_state.json")], cwd=str(BACKEND.parent), stdout=subprocess.PIPE, text=True,
                             env=dict(os.environ, PYTHONPATH=str(BACKEND), PYTHONDONTWRITEBYTECODE="1"))
    try:
        assert child.stdout.readline().strip() == "locked"
        monkeypatch.setattr(daystate, "LOCK_WAIT_SEC", 0.3)
        with pytest.raises(daystate.DaystateLockTimeout):
            daystate.set_raw("x", 1)
    finally:
        child.kill()                                          # the child this test started
        child.wait(timeout=30)
    monkeypatch.setattr(daystate, "LOCK_WAIT_SEC", 10.0)
    daystate.set_raw("x", 2)                                  # the OS released the lock with the process: no stale lock to clear
    assert daystate.get_raw("x") == 2


def test_the_state_file_stays_the_one_file_with_a_sidecar_lock_that_is_never_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(daystate, "STATE", str(tmp_path / "daily_state.json"))
    daystate.set_today("a", 1)
    daystate.clear("a")
    daystate.mark_today("b")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["daily_state.json", "daily_state.json.lock"]


# ------------------------------------------------------------------ the callers that incremented with two steps
def test_a_block_runner_exit_counts_one_unit_through_the_locked_increment(tmp_path, monkeypatch):
    from scheduling import combo
    monkeypatch.setattr(daystate, "STATE", str(tmp_path / "daily_state.json"))
    block = {"id": "tourney", "block": "tournament", "kind": "tournament", "count": 3}
    calls = []
    real = daystate.increment_today
    monkeypatch.setattr(daystate, "increment_today", lambda key, by=1: calls.append(key) or real(key, by))
    monkeypatch.setattr(daystate, "set_today", lambda *a, **k: pytest.fail("a counter must not be written with a plain set"))
    combo._close_block(block, 0)
    combo._close_block(block, 0)
    assert calls == [combo._progress_key(block)] * 2 and daystate.get_today(combo._progress_key(block)) == 2
    assert not daystate.flag_today(combo._closed_key(block))            # 2 of 3: the block is still open


def test_the_shard_loop_counts_its_run_through_the_locked_increment():
    source = (BACKEND / "flows" / "shard.py").read_text(encoding="utf-8")
    assert 'daystate.increment_today("shard_runs")' in source
    assert 'get_today("shard_runs") + 1' not in source and "daystate.set_today(\"shard_runs\"" not in source
