"""Exclusive-device runner detection (runtime.runners) and the two setup
preflights that refuse to start beside another runner.

A venv launcher (uv's Scripts/python.exe on Windows) starts the real
interpreter as a CHILD with the same script arguments, so a runner's own
launcher used to match as "the other runner" and the calibrate worker
refused itself on every Learn screens attempt.
"""

import paths as test_paths

import json
import os
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest

import settings

BACKEND = test_paths.BACKEND
ME, LAUNCHER, SHELL = 5000, 4000, 3000


class _Proc:
    def __init__(self, pid, cmdline, name="python.exe", cwd=None):
        self.pid = pid
        self.info = {"pid": pid, "name": name, "cmdline": cmdline}
        self._cwd = cwd

    def cwd(self):
        if self._cwd is None:
            raise PermissionError("access denied")
        return self._cwd

    def parents(self):
        return [_Proc(LAUNCHER, []), _Proc(SHELL, [])]

    def create_time(self):
        return 1000.0 + self.pid


def _fake_psutil(monkeypatch, procs):
    """Install a psutil whose current process is ME (child of LAUNCHER, child
    of SHELL) and whose process table is `procs`."""
    table = {p.pid: p for p in procs}
    m = types.ModuleType("psutil")
    m.Error = OSError
    m.process_iter = lambda attrs=None: iter(procs)
    m.Process = lambda pid=None: _Proc(ME, []) if pid is None else table[pid]
    monkeypatch.setitem(sys.modules, "psutil", m)
    monkeypatch.setattr(os, "getpid", lambda: ME)


CAL = ["E:\\tp\\.venv\\Scripts\\python.exe", "player/calibrate.py", "--instance", "main"]


# ------------------------------------------------------------ other_runners
def test_own_launcher_with_identical_cmdline_is_not_another_runner(monkeypatch):
    from runtime import runners
    _fake_psutil(monkeypatch, [_Proc(LAUNCHER, CAL), _Proc(ME, CAL)])
    assert list(runners.other_runners()) == []


def test_a_separate_runner_is_still_found(monkeypatch):
    from runtime import runners
    shard = ["pythonw.exe", "flows/shard.py", "--instance", "main"]
    _fake_psutil(monkeypatch, [_Proc(LAUNCHER, CAL), _Proc(ME, CAL), _Proc(7777, shard, "pythonw.exe")])
    assert list(runners.other_runners()) == [(7777, "shard.py", shard)]


def test_a_second_copy_of_the_same_runner_is_still_found(monkeypatch):
    from runtime import runners
    _fake_psutil(monkeypatch, [_Proc(LAUNCHER, CAL), _Proc(ME, CAL), _Proc(8888, CAL)])
    assert [pid for pid, _, _ in runners.other_runners()] == [8888]


def test_non_python_and_non_runner_processes_are_ignored(monkeypatch):
    from runtime import runners
    _fake_psutil(monkeypatch, [_Proc(1, ["notepad.exe", "shard.py"], "notepad.exe"),
                               _Proc(2, ["python.exe", "backend/dashboard.py"]),
                               _Proc(3, None)])
    assert list(runners.other_runners()) == []


def test_another_scan_rebinding_info_mid_walk_cannot_break_the_runner_walk(monkeypatch):
    """psutil reuses one Process per pid and rebinds `.info` for every scan; the
    dashboard's emulator listing (attrs ["name"]) did it while the runner walk
    was inside the lease check, and the walk read a dict with no "pid"."""
    from runtime import device_lease, runners
    shard = ["pythonw.exe", "flows/shard.py", "--instance", "main"]
    procs = [_Proc(LAUNCHER, CAL), _Proc(ME, CAL), _Proc(7777, shard, "pythonw.exe")]
    _fake_psutil(monkeypatch, procs)

    def concurrent_listing(pid, target=None):
        for p in procs:
            p.info = {"name": p.info.get("name")}
        return False
    monkeypatch.setattr(device_lease, "owns_different_device", concurrent_listing)
    assert list(runners.other_runners()) == [(7777, "shard.py", shard)]


# --------------------------------------------------- real launcher regression
def test_real_process_does_not_see_its_own_launcher(tmp_path):
    """A script literally named calibrate.py run with sys.executable: on a
    machine whose venv python is a launcher stub, the stub is this script's
    parent with the same script arguments. Any runner reported must not be
    this script."""
    script = tmp_path / "calibrate.py"
    script.write_text(
        "import json\n"
        "from runtime.runners import other_runners\n"
        "print(json.dumps([[pid, args] for pid, _, args in other_runners()]))\n",
        encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(BACKEND), os.environ.get("PYTHONPATH", "")]))
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                         env=env, cwd=tmp_path, timeout=60, check=True).stdout
    mine = [pid for pid, args in json.loads(out) if any(str(script) in str(a) for a in args or [])]
    assert mine == []


# ------------------------------------------------------- bootstrap.preflight
def test_bootstrap_preflight_names_the_other_runner(monkeypatch):
    from player import bootstrap
    monkeypatch.setattr(settings, "instance", lambda: {"allow_taps": True, "serial": "test"})
    _fake_psutil(monkeypatch, [_Proc(LAUNCHER, CAL), _Proc(ME, CAL),
                               _Proc(4242, ["pythonw.exe", "flows/shard.py"], "pythonw.exe")])
    with pytest.raises(RuntimeError, match=r"^Stop the other runner first \(PID 4242: shard\.py\)$"):
        bootstrap.preflight()


def test_bootstrap_preflight_passes_its_own_launcher(monkeypatch):
    """Past the runner check the next step reads the device; stop there."""
    from device import adbclient
    from player import bootstrap
    monkeypatch.setattr(settings, "instance", lambda: {"allow_taps": True, "serial": "test"})
    _fake_psutil(monkeypatch, [_Proc(LAUNCHER, CAL), _Proc(ME, CAL)])

    def reached(*a, **k):
        raise LookupError("device read")
    monkeypatch.setattr(adbclient, "shell", reached)
    with pytest.raises(LookupError, match="device read"):
        bootstrap.preflight()


# ------------------------------------------------------------ scan.preflight
@pytest.fixture()
def scan_env(monkeypatch):
    from device import adbclient, capture, layout, overlays
    from player import scan, scan_plan
    monkeypatch.setattr(settings, "instance", lambda: {"serial": "test"})
    monkeypatch.setattr(capture, "grab", lambda: np.zeros((2560, 1080, 3), np.uint8))
    monkeypatch.setattr(adbclient, "shell", lambda serial, cmd, **k: b"Physical density: 360")
    monkeypatch.setattr(layout, "require_native", lambda *a: None)
    monkeypatch.setattr(scan_plan, "missing_navigation", lambda *a, **k: [])
    # The step after the runner check; refusing here proves the check passed.
    monkeypatch.setattr(overlays, "clean", lambda: False)
    return scan


SHARD = ["pythonw.exe", "flows/shard.py", "--instance", "main"]


def test_scan_refuses_a_runner_in_this_tree(monkeypatch, scan_env):
    _fake_psutil(monkeypatch, [_Proc(6000, SHARD, "pythonw.exe", cwd=str(settings.ROOT))])
    with pytest.raises(SystemExit, match="REFUSED: runner alive \\(pid 6000"):
        scan_env.preflight(False)


def test_scan_refuses_a_runner_whose_cwd_is_unreadable(monkeypatch, scan_env):
    _fake_psutil(monkeypatch, [_Proc(6000, SHARD, "pythonw.exe", cwd=None)])
    with pytest.raises(SystemExit, match="REFUSED: runner alive"):
        scan_env.preflight(False)


def test_scan_refuses_unproven_foreign_tree_runner_but_skips_own_launcher(monkeypatch, scan_env, tmp_path):
    scan_cmd = ["E:\\tp\\.venv\\Scripts\\python.exe", "player/scan.py"]
    _fake_psutil(monkeypatch, [_Proc(LAUNCHER, scan_cmd, cwd=str(settings.ROOT)),
                               _Proc(ME, scan_cmd, cwd=str(settings.ROOT)),
                               _Proc(6000, SHARD, "pythonw.exe", cwd=str(tmp_path))])
    with pytest.raises(SystemExit, match="REFUSED: runner alive"):
        scan_env.preflight(False)
