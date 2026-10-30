"""The dashboard's runner-process scan (`_scan_procs`) over fake process tables.

A venv launcher stub (.venv/Scripts/python(w).exe) starts the real
interpreter as a CHILD with the same script arguments, so one runner used to
show as two rows in the dashboard (two kill buttons, two names in every
"runners alive" refusal). The scan reuses runtime.runners.other_runners (the
walk bootstrap/scan preflights use) and folds a launcher's child into the
launcher's row.
"""

import paths as test_paths

import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

BACKEND = test_paths.BACKEND
ME, ME_LAUNCHER = 5000, 4000            # the dashboard process and its own launcher


class _Proc:
    def __init__(self, pid, cmdline, ppid=1, cwd=None, name="pythonw.exe", created=None):
        self.pid = pid
        self._created = 1_790_000_000.0 + pid if created is None else created
        self.info = {"pid": pid, "name": name, "cmdline": cmdline}
        self._ppid, self._cwd = ppid, cwd

    def cwd(self):
        if self._cwd is None:
            raise PermissionError("access denied")
        return self._cwd

    def ppid(self):
        return self._ppid

    def cmdline(self):
        return self.info["cmdline"]

    def create_time(self):
        return self._created

    def parents(self):
        return [_Proc(ME_LAUNCHER, [])]


@pytest.fixture(scope="module")
def dash():
    spec = importlib.util.spec_from_file_location("tp_dashboard_procscan", test_paths.DASHBOARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def table(dash, monkeypatch):
    """Install a psutil whose current process is ME and whose table is the
    list the test fills in; record any process the scan spawns."""
    procs, spawned = [], []

    def process(pid=None):
        if pid is None:
            return _Proc(ME, [])
        return next(p for p in procs if p.pid == pid)
    m = types.ModuleType("psutil")
    m.process_iter = lambda attrs=None: iter(list(procs))
    m.Process = process
    monkeypatch.setitem(sys.modules, "psutil", m)

    def run(args, **kw):
        spawned.append(args)
        raise OSError("no process may be spawned by the scan")
    monkeypatch.setattr(dash, "_run", run)
    return procs, spawned


VENV = "E:\\tp\\.venv\\Scripts\\pythonw.exe"
BASE = "C:\\uv\\cpython-3.12\\pythonw.exe"
CAL = ["player/calibrate.py", "--instance", "main", "--phases", "home,cards"]
SHARD = ["flows/shard.py", "--instance", "main", "--preset", "bp_farm"]


def test_a_launcher_and_its_child_are_one_runner_row(dash, table):
    procs, spawned = table
    procs += [_Proc(700, [VENV] + CAL, ppid=600, cwd=dash.ROOT),
              _Proc(701, [BASE] + CAL, ppid=700, cwd=dash.ROOT)]
    rows = dash._scan_procs()
    assert rows == [{"pid": 700, "runner": "calibrate", "cmdline": " ".join([VENV] + CAL), "created": 1_790_000_700.0}]
    assert spawned == []                                   # no powershell/WMI child


def test_separate_runners_and_a_second_copy_each_keep_their_row(dash, table):
    procs, _ = table
    procs += [_Proc(700, [VENV] + CAL, cwd=dash.ROOT), _Proc(701, [BASE] + CAL, ppid=700, cwd=dash.ROOT),
              _Proc(800, [VENV] + SHARD, cwd=dash.ROOT), _Proc(801, [BASE] + SHARD, ppid=800, cwd=dash.ROOT),
              _Proc(900, [BASE] + CAL, ppid=42, cwd=dash.ROOT)]          # a second calibrate, own parent
    assert [(r["pid"], r["runner"]) for r in dash._scan_procs()] == [(700, "calibrate"), (800, "shard"), (900, "calibrate")]


def test_a_runner_child_with_other_arguments_is_its_own_row(dash, table):
    procs, _ = table
    combo = ["scheduling/combo.py", "--instance", "main"]
    procs += [_Proc(700, [BASE] + combo, cwd=dash.ROOT), _Proc(701, [BASE] + SHARD, ppid=700, cwd=dash.ROOT)]
    assert [(r["pid"], r["runner"]) for r in dash._scan_procs()] == [(700, "combo"), (701, "shard")]


def test_rows_stay_scoped_to_this_tree(dash, table, tmp_path):
    procs, _ = table
    procs += [_Proc(700, [BASE] + SHARD, cwd=str(tmp_path)),             # the other tree's farm
              _Proc(800, [BASE] + CAL, cwd=None),                         # unreadable cwd: ours
              _Proc(ME_LAUNCHER, [VENV, "backend/dashboard.py"], cwd=dash.ROOT),
              _Proc(ME, [BASE, "backend/dashboard.py"], ppid=ME_LAUNCHER, cwd=dash.ROOT),
              _Proc(900, ["notepad.exe", "shard.py"], name="notepad.exe", cwd=dash.ROOT)]
    assert [r["pid"] for r in dash._scan_procs()] == [800]


def test_the_diamond_collector_and_hp_probe_are_runners(dash, table):
    # the dashboard starts gem_collector.py itself (start_gems) and the page
    # names it "Diamond collector"; both scripts were rows before the scan
    # reused runtime.runners, so RUNNER_SCRIPTS must name them
    procs, _ = table
    procs += [_Proc(700, [BASE, "gem_collector.py", "--instance", "main"], cwd=dash.ROOT),
              _Proc(800, [BASE, "runtime/hp_probe.py"], cwd=dash.ROOT)]
    assert [r["runner"] for r in dash._scan_procs()] == ["gem_collector", "hp_probe"]


def test_discovery_uses_selected_config_without_rebinding_runtime(dash, table, monkeypatch):
    from runtime import device_lease
    from device import adbclient
    procs, _ = table
    procs.append(_Proc(700, [BASE] + SHARD, cwd=dash.ROOT))
    monkeypatch.setattr(dash, 'load_config', lambda: {
        'active_instance': 'second', 'instances': {'second': {'serial': 'test:second'}}})
    monkeypatch.setattr(adbclient, '_HOST', ('localhost', 5050))
    observed = []
    monkeypatch.setattr(device_lease, 'owns_different_device',
                        lambda pid, target=None: observed.append((pid, target)) or True)
    assert dash._scan_procs() == []
    assert observed == [(700, ('localhost', 5050, 'test:second'))]


def test_process_cache_is_invalidated_when_selected_transport_changes(dash, monkeypatch):
    import time
    target = ['localhost', 5037, 'test:first']
    monkeypatch.setattr(dash, '_process_target', lambda: tuple(target))
    monkeypatch.setattr(dash, '_PROCS_CACHE', {'at': time.time(), 'target': tuple(target), 'procs': [{'pid': 1}]})
    monkeypatch.setattr(dash, '_scan_procs', lambda selected: [{'pid': 2, 'serial': selected[2]}])
    assert dash._procs_cached() == [{'pid': 1}]
    target[2] = 'test:second'
    assert dash._procs_cached() == [{'pid': 2, 'serial': 'test:second'}]


def test_failed_discovery_retains_same_target_rows_with_visible_error(dash, monkeypatch, caplog):
    import time
    target = ('localhost', 5037, 'test:first')
    monkeypatch.setattr(dash, '_process_target', lambda: target)
    monkeypatch.setattr(dash, '_PROCS_CACHE', {'at': time.time(), 'target': target, 'procs': [{'pid': 1}]})

    def fail(_):
        raise PermissionError('process table unavailable')
    monkeypatch.setattr(dash, '_scan_procs', fail)
    assert dash._procs_refresh() == [{'pid': 1}]
    state = dash._procs_status()
    assert state['state'] == 'stale'
    assert state['error']['type'] == 'PermissionError'
    assert state['last_success_at'] is not None
    assert 'process table unavailable' in caplog.text


def test_background_refresh_never_blocks_status_or_leaks_other_device(dash, monkeypatch):
    import threading
    target = ('localhost', 5037, 'test:second')
    monkeypatch.setattr(dash, '_process_target', lambda: target)
    monkeypatch.setattr(dash, '_PROCS_CACHE', {'at': 1.0, 'target': ('localhost', 5037, 'test:first'), 'procs': [{'pid': 1}]})
    monkeypatch.setattr(dash, '_PROCS_THREAD', types.SimpleNamespace(is_alive=lambda: True))
    wake = threading.Event()
    monkeypatch.setattr(dash, '_PROCS_WAKE', wake)
    monkeypatch.setattr(dash, '_procs_refresh', lambda: pytest.fail('status performed blocking scan'))
    assert dash._procs_cached() == []
    assert wake.is_set()
    assert dash._procs_status()['state'] == 'unknown'


def test_process_discovery_recovers_from_failure(dash, monkeypatch):
    target = ('localhost', 5037, 'test:second')
    monkeypatch.setattr(dash, '_process_target', lambda: target)
    monkeypatch.setattr(dash, '_PROCS_CACHE', {'at': 0, 'target': target, 'procs': [], 'error': {'message': 'prior failure'}})
    monkeypatch.setattr(dash, '_scan_procs', lambda selected: [{'pid': 2}])
    assert dash._procs_refresh() == [{'pid': 2}]
    state = dash._procs_status()
    assert state['state'] == 'fresh'
    assert state['error'] is None


def test_runtime_endpoint_reports_source_at_start_and_capabilities(dash, monkeypatch):
    monkeypatch.setattr(dash.build_info, 'compare', lambda root, started: dict(started, source_changed=True))
    monkeypatch.setattr(dash.operation_api, 'ledger', lambda root: types.SimpleNamespace(active=lambda: []))
    monkeypatch.setattr(dash, '_procs_status', lambda: {'state': 'unknown'})
    response = dash.app.test_client().get('/api/runtime')
    assert response.status_code == 200
    body = response.get_json()
    assert body['server']['source_changed'] is True
    assert 'battle_controls' in body['capabilities']


def test_each_row_carries_the_creation_time_of_the_process_the_walk_saw(dash, table):
    procs, _ = table
    procs += [_Proc(700, [VENV] + CAL, cwd=dash.ROOT, created=1_790_000_123.5)]
    [row] = dash._scan_procs()
    assert (row["pid"], row["created"]) == (700, 1_790_000_123.5)   # what Stop-by-pid is judged against, never the pid alone


def test_a_row_has_no_identity_when_the_pid_no_longer_belongs_to_the_walked_process(dash, table, monkeypatch):
    procs, _ = table
    walked = _Proc(700, [VENV] + CAL, cwd=dash.ROOT)
    procs.append(walked)
    reused = _Proc(700, [BASE, "something_else.py"], cwd=dash.ROOT)            # the pid changed hands after the walk
    psutil = sys.modules["psutil"]
    original = psutil.Process
    psutil.Process = lambda pid=None: reused if pid == 700 else original(pid)
    [row] = dash._scan_procs()
    assert row["pid"] == 700 and row["created"] is None


def test_a_row_without_a_readable_creation_time_has_no_identity(dash, table):
    procs, _ = table
    unreadable = _Proc(700, [VENV] + CAL, cwd=dash.ROOT)
    unreadable.create_time = lambda: (_ for _ in ()).throw(PermissionError("access denied"))
    procs.append(unreadable)
    [row] = dash._scan_procs()
    assert row["created"] is None
