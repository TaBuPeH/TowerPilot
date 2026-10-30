"""The venv launcher stub against the scheduler's and the tray's process scans.

A venv launcher (uv's Scripts/python.exe on Windows) starts the real
interpreter as a CHILD with the same script arguments, so every stub-launched
python shows up twice in the process table. That made bootstrap/scan refuse
their own launcher as "the other runner" (runtime.runners, fixed). These
tests pin the two other scans reported with the same defect:

- combo._find_running / _find_running_block look only for flow runner
  scripts (orchestrator.py, flows/*.py), so combo's own launcher - whose argv
  names scheduling/combo.py - never matches; a stub-launched runner matches
  twice and either half is adopted.
- tray.orphans() only matches config runner basenames at argv[1], so the
  tray's own launcher (runtime/tray.py) never matches; a leftover stub pair
  is returned whole, which is what kill_orphans needs to clear it.
"""

import os
import sys
import types

import pytest

import scheduling.combo as combo
from runtime import tray
from settings import CONFIG, ROOT

ME, LAUNCHER = 5000, 4000
STUB = "E:\\tp\\.venv\\Scripts\\python.exe"
REAL = "C:\\uv\\python\\cpython-3.12\\python.exe"


def _proc(pid, cmdline, name="python.exe"):
    """What the scans read of a psutil.Process: pid, the cmdline from process_iter, and the creation time combo's _Adopted caches
    from the walked object (it stops the adopted runner through a handle verified against that time)."""
    return types.SimpleNamespace(pid=pid, info={"pid": pid, "name": name, "cmdline": cmdline}, create_time=lambda: 1_000_000.0 + pid)


def _fake_psutil(procs):
    m = types.ModuleType("psutil")
    m.process_iter = lambda attrs=None: iter(procs)
    m.Process = lambda pid: types.SimpleNamespace(pid=pid, is_running=lambda: True)
    return m


def _pair(pid, args):
    """A stub-launched python: the launcher and its real interpreter child."""
    return [_proc(pid, [STUB, *args]), _proc(pid + 1, [REAL, *args])]


# ------------------------------------------------------------------- combo
COMBO_ARGS = ["scheduling/combo.py", "--instance", "main", "--preset", "combo"]
COIN_ARGS = ["orchestrator.py", "--instance", "main", "--preset", "normal_run"]
COIN_BLOCK = {"id": "monday#1", "block": "coin", "kind": "coin", "preset": "bp_coin_default"}


@pytest.fixture()
def legacy(monkeypatch):
    """No profile: the coin phase matches the literal legacy preset."""
    monkeypatch.delitem(CONFIG, "active_profile", raising=False)
    monkeypatch.setattr(os, "getpid", lambda: ME)


def test_combo_does_not_adopt_its_own_launcher(monkeypatch, legacy):
    monkeypatch.setitem(sys.modules, "psutil", _fake_psutil([_proc(LAUNCHER, [STUB, *COMBO_ARGS]),
                                                             _proc(ME, [REAL, *COMBO_ARGS])]))
    assert combo._find_running("coin", "main") is None
    assert combo._find_running_block(COIN_BLOCK, "main") is None


@pytest.mark.parametrize("find", [lambda: combo._find_running("coin", "main"),
                                  lambda: combo._find_running_block(
                                      dict(COIN_BLOCK, preset="normal_run"), "main")])
def test_combo_adopts_one_half_of_a_stub_launched_runner(monkeypatch, legacy, find):
    monkeypatch.setitem(sys.modules, "psutil", _fake_psutil([_proc(LAUNCHER, [STUB, *COMBO_ARGS]),
                                                             _proc(ME, [REAL, *COMBO_ARGS]),
                                                             *_pair(7000, COIN_ARGS)]))
    found = find()
    assert found is not None and found._p.pid in (7000, 7001)
    assert found._created == 1_000_000.0 + found.pid, "the adopted runner keeps the creation time of the process the walk matched"


# -------------------------------------------------------------------- tray
def _orch(instance):
    return [str(ROOT / "orchestrator.py"), "--instance", instance, "--preset", "normal_run"]


def test_tray_orphans_skip_its_own_launcher_and_return_a_whole_stub_pair(monkeypatch):
    table = [_proc(LAUNCHER, [STUB, "runtime/tray.py", "--autostart", "main"]),
             _proc(ME, [REAL, "runtime/tray.py", "--autostart", "main"]),
             *_pair(7000, _orch("main")),
             *_pair(8000, _orch("other"))]
    fake = _fake_psutil(table)
    monkeypatch.setattr(tray, "psutil", fake)
    monkeypatch.setitem(sys.modules, "psutil", fake)   # the scan itself goes through runtime.runners.process_rows
    assert [p.pid for p in tray.Instance("main", {}).orphans()] == [7000, 7001]
