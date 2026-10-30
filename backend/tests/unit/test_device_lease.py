"""Real OS locking tests using only synthetic serials and temporary files."""

import paths as test_paths
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from runtime import device_lease


BACKEND = test_paths.BACKEND
SCRIPT = '''
import json, sys
from pathlib import Path
from runtime import device_lease as lease
lease._ROOT = Path(sys.argv[1])
try:
    held = lease.acquire(sys.argv[2], port=int(sys.argv[3]))
except lease.DeviceBusy:
    print('busy', flush=True)
    raise SystemExit(3)
print('owned', flush=True)
sys.stdin.readline()
'''


def child(tmp_path, serial="synthetic-device", port=5037, env=None, cwd=None, script=SCRIPT):
    environment = dict(os.environ, PYTHONPATH=str(BACKEND))
    if env is not None:
        environment.update(env)
    return subprocess.Popen([sys.executable, "-c", script, str(device_lease._ROOT), serial, str(port)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=environment, cwd=cwd or tmp_path)


def stop(proc):
    if proc.poll() is None:
        proc.communicate("exit\n", timeout=15)


def test_same_device_is_atomic_across_checkouts_and_released_on_exit(tmp_path):
    first_cwd, second_cwd = tmp_path / "checkout1", tmp_path / "checkout2"
    first_cwd.mkdir()
    second_cwd.mkdir()
    first = child(tmp_path, cwd=first_cwd)
    try:
        assert first.stdout.readline().strip() == "owned"
        second = child(tmp_path, cwd=second_cwd)
        assert second.communicate(timeout=15)[0].strip() == "busy"
        assert second.returncode == 3
        first.kill()
        first.communicate(timeout=15)
        third = child(tmp_path, cwd=second_cwd)
        try:
            assert third.stdout.readline().strip() == "owned"
        finally:
            stop(third)
    finally:
        stop(first)


def test_simultaneous_processes_have_exactly_one_owner(tmp_path):
    gated = SCRIPT.replace("try:\n", "print('ready', flush=True)\nsys.stdin.readline()\ntry:\n", 1)
    contenders = [child(tmp_path, script=gated), child(tmp_path, script=gated)]
    try:
        assert [p.stdout.readline().strip() for p in contenders] == ["ready", "ready"]
        for proc in contenders:
            proc.stdin.write("go\n")
            proc.stdin.flush()
        assert sorted(p.stdout.readline().strip() for p in contenders) == ["busy", "owned"]
    finally:
        for proc in contenders:
            stop(proc)


def test_different_serials_and_servers_are_independent(tmp_path):
    device_lease.acquire("synthetic-device")
    for serial, port in [("different-device", 5037), ("synthetic-device", 15037)]:
        other = child(tmp_path, serial, port)
        try:
            assert other.stdout.readline().strip() == "owned"
        finally:
            stop(other)


def test_process_cannot_rebind_while_owning_device():
    first = device_lease.acquire("synthetic-device")
    assert device_lease.acquire("synthetic-device", host="localhost") is first
    with pytest.raises(device_lease.DeviceBusy, match="One device per process"):
        device_lease.acquire("different-device")


def test_scheduler_delegates_input_without_releasing_reservation(tmp_path):
    held = device_lease.acquire("synthetic-device")
    # child_environment resolves the configured serial; use the exact explicit
    # grant here so this OS-lock test never loads machine configuration.
    grant = held.grant()
    runner = child(tmp_path, env={device_lease._ENV: json.dumps(grant)})
    try:
        assert runner.stdout.readline().strip() == "owned"
        with pytest.raises(device_lease.DeviceBusy):
            held.check()
        intruder = child(tmp_path, env={device_lease._ENV: ""})
        assert intruder.communicate(timeout=15)[0].strip() == "busy"
    finally:
        stop(runner)
    assert held.check() is held


def test_activity_remains_exclusive_after_supervisor_releases(tmp_path):
    held = device_lease.acquire("synthetic-device")
    grant = held.grant()
    runner = child(tmp_path, env={device_lease._ENV: json.dumps(grant)})
    try:
        assert runner.stdout.readline().strip() == "owned"
        held.release()
        intruder = child(tmp_path, env={device_lease._ENV: ""})
        assert intruder.communicate(timeout=15)[0].strip() == "busy"
    finally:
        stop(runner)
    device_lease.acquire("synthetic-device")


def test_act_fence_refuses_parent_input_while_child_drives(tmp_path, monkeypatch):
    import settings
    from device import act, adbclient
    held = device_lease.acquire("synthetic-device")
    monkeypatch.setattr(settings, "instance", lambda: {"serial": "synthetic-device"})
    monkeypatch.setattr(adbclient, "shell", lambda *a, **k: pytest.fail("input escaped ownership fence"))
    grant = held.grant()
    runner = child(tmp_path, env={device_lease._ENV: json.dumps(grant)})
    try:
        assert runner.stdout.readline().strip() == "owned"
        with pytest.raises(device_lease.DeviceBusy):
            act._input("tap", 10, 20)
    finally:
        stop(runner)


def test_discovery_skips_only_proven_different_device_owner(tmp_path, monkeypatch):
    import settings
    monkeypatch.setattr(settings, "instance", lambda: {"serial": "synthetic-device"})
    runner = child(tmp_path, serial="different-device")
    try:
        assert runner.stdout.readline().strip() == "owned"
        assert device_lease.owns_different_device(runner.pid)
        monkeypatch.setattr(settings, "instance", lambda: {"serial": "different-device"})
        assert not device_lease.owns_different_device(runner.pid)
        monkeypatch.setattr(settings, "instance", lambda: {"serial": "synthetic-device"})
        path = next(device_lease._ROOT.glob("*.json"))
        owner = json.loads(path.read_text())
        owner["creation_time"] += 1
        owner["launchers"] = {pid: stamp + 1 for pid, stamp in owner.get("launchers", {}).items()}
        path.write_text(json.dumps(owner))
        assert not device_lease.owns_different_device(runner.pid)
    finally:
        stop(runner)


def test_child_can_promote_after_one_shot_supervisor_exits(tmp_path):
    held = device_lease.acquire("synthetic-device")
    promote = SCRIPT + '''
lease.ensure_current = lambda: held
environment = lease.child_environment()
assert not held.delegated
print('promoted', flush=True)
sys.stdin.readline()
'''
    runner = child(tmp_path, env={device_lease._ENV: json.dumps(held.grant())}, script=promote)
    try:
        assert runner.stdout.readline().strip() == "owned"
        held.release()
        runner.stdin.write("promote\n")
        runner.stdin.flush()
        assert runner.stdout.readline().strip() == "promoted"
        intruder = child(tmp_path, env={device_lease._ENV: ""})
        assert intruder.communicate(timeout=15)[0].strip() == "busy"
    finally:
        stop(runner)


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "::1", "127.0.0.1"])
def test_discovery_keeps_same_device_owner_for_loopback_alias(tmp_path, monkeypatch, host):
    import settings
    from device import adbclient
    monkeypatch.setattr(settings, "instance", lambda: {"serial": "synthetic-device"})
    monkeypatch.setattr(adbclient, "_HOST", (host, 5037))
    runner = child(tmp_path)
    try:
        assert runner.stdout.readline().strip() == "owned"
        assert not device_lease.owns_different_device(runner.pid)
    finally:
        stop(runner)


# ----------------------------------------------------------------- H2R7-01: absence is ONE observation of both locks
def test_a_device_kept_owned_by_a_promotion_is_never_observed_free(monkeypatch):
    """child_environment() promotes a delegated child by taking the primary lock BEFORE letting go of the
    activity lock, so the device is owned at every instant. A reader that probed the primary lock, let it go,
    and only then probed the activity lock saw the primary free (before the promotion) and the activity free
    (after it): an owned device read as free, and a Stop was confirmed over a live owner. The promotion is
    run by the real child_environment(), on real lock files, at exactly that moment."""
    from harvest_support import delegated_lease, promote_after_the_primary_probe
    lease = delegated_lease(monkeypatch, "synthetic-device")
    try:
        fired = promote_after_the_primary_probe(monkeypatch, lease)
        state = device_lease.lease_state("synthetic-device")
        assert state["owned"] is True and state["delegated"] is True and fired == [True]
        after = device_lease.lease_state("synthetic-device")  # the hook fired once and is a plain pass-through now
        assert after["owned"] is True and after["delegated"] is False and not lease.delegated
        with pytest.raises(device_lease.DeviceBusy):         # and the primary lock really is held, by the promoted owner
            device_lease._lock(lease.path)
    finally:
        lease.release()


def test_control_without_a_promotion_the_same_reader_sees_exactly_what_is_held(monkeypatch):
    from harvest_support import delegated_lease
    lease = delegated_lease(monkeypatch, "synthetic-device")
    try:
        state = device_lease.lease_state("synthetic-device")
        assert (state["owned"], state["delegated"], state["input_gate"]) == (True, True, "enforced")
        assert [holder["role"] for holder in state["holders"]] == ["delegated"]
    finally:
        lease.release()
    assert device_lease.lease_state("synthetic-device") == {"owned": False, "delegated": False, "input_gate": None, "holders": []}


def test_a_promotion_that_arrives_while_the_device_is_being_read_waits_for_the_reader_and_is_not_lost(monkeypatch):
    """The other order: the promotion is already asking for the primary lock while the reader holds its free
    probe. It is made to wait (child_environment retries), the reader answers that the device is owned, and
    the promotion then completes."""
    import threading
    from harvest_support import delegated_lease
    lease = delegated_lease(monkeypatch, "synthetic-device")
    try:
        real_lock, blocked, outcome = device_lease._lock, threading.Event(), []

        def lock(path):
            try:
                return real_lock(path)
            except device_lease.DeviceBusy:
                if threading.current_thread().name == "promoter":
                    blocked.set()
                raise

        def promote():
            try:
                device_lease.child_environment()
                outcome.append("promoted")
            except BaseException as exc:   # noqa: BLE001
                outcome.append(repr(exc))
        promoter = threading.Thread(target=promote, name="promoter")
        state = []

        def lock_then_start_the_promoter(path):
            handle = lock(path)
            if device_lease.Path(path) == lease.path and not promoter.is_alive() and not outcome and threading.current_thread() is threading.main_thread():
                promoter.start()
                assert blocked.wait(10), "the promotion never asked for the primary lock"
            return handle
        monkeypatch.setattr(device_lease, "_lock", lock_then_start_the_promoter)
        state.append(device_lease.lease_state("synthetic-device"))
        promoter.join(15)
        assert outcome == ["promoted"] and state[0]["owned"] is True and state[0]["delegated"] is True
        monkeypatch.setattr(device_lease, "_lock", real_lock)
        assert device_lease.lease_state("synthetic-device")["delegated"] is False
    finally:
        lease.release()
