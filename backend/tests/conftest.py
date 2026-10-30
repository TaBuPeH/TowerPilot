"""Suite-wide fixtures."""

import paths as test_paths

import contextlib
import copy
from pathlib import Path

import pytest
import yaml

import settings

# settings.CONFIG is loaded at import from the machine's own git-ignored
# backend/config.yaml, so results used to depend on each install's config (a
# bound account, a non-native `rendering`, a `fleet.by_tier` override). pytest
# imports this file before any test module, and settings imports nothing that
# reads CONFIG, so it is replaced here with the shipped config.example.yaml
# before any other module sees it: in place, because modules hold
# `from settings import CONFIG`, with bind_device()'s pristine snapshots retaken
# from the same body.
settings.CONFIG.clear()
settings.CONFIG.update(yaml.safe_load(settings.CONFIG_EXAMPLE.read_text(encoding="utf-8")))
settings._DEFAULTS = {k: copy.deepcopy(settings.CONFIG[k]) for k in settings._OVERRIDABLE if k in settings.CONFIG}
settings._ACCOUNT_DEFAULTS = {k: copy.deepcopy(settings.CONFIG.get(k)) for k in settings._ACCOUNT_DEFAULTS}


@contextlib.contextmanager
def settings_restored():
    """Whatever runs inside, the settings singletons come out as they went in.

    settings.CONFIG and its two baselines (_DEFAULTS, _ACCOUNT_DEFAULTS, which select_instance and an operation's worker bootstrap
    rebuild) are process-wide, and production code rewrites them on purpose: a successful POST /api/loadout-patch assigns
    CONFIG['loadouts'] so the dashboard matches the file it just wrote, and a worker bootstrap replaces CONFIG with the accepted
    frozen one. A test that reaches such a path would leave the next test the wrong table (a later test reading the shipped
    `tourney_1` loadout then fails, but only after the first one has run). Restored in place - modules hold
    `from settings import CONFIG`, so the dict keeps its identity - and only when something changed."""
    before = copy.deepcopy((settings.CONFIG, settings._DEFAULTS, settings._ACCOUNT_DEFAULTS))
    try:
        yield
    finally:
        if (settings.CONFIG, settings._DEFAULTS, settings._ACCOUNT_DEFAULTS) != before:
            config, defaults, account_defaults = copy.deepcopy(before)
            settings.CONFIG.clear()
            settings.CONFIG.update(config)
            settings._DEFAULTS, settings._ACCOUNT_DEFAULTS = defaults, account_defaults


@pytest.fixture(autouse=True)
def _settings_singletons_restored():
    """No test hands the next one a changed settings table (the world of a contract generator restores its own as well)."""
    with settings_restored():
        yield


@pytest.fixture(autouse=True)
def _tap_pacing_starts_idle(monkeypatch):
    """No test inherits the previous test's last-tap time. Pacing compares it with
    `time.monotonic()`, which many tests replace with a small fake clock; a real
    leftover value would make the next paced wait sleep for hours."""
    from device import act
    monkeypatch.setattr(act, '_last_tap', 0.0)


@pytest.fixture(autouse=True)
def _operation_ledger_in_tmp_path(tmp_path, monkeypatch):
    """No test opens the machine's real ledger. Every reader and writer,
    including the read-only setup-battle guard, derives the path from
    control.operations.ledger_path, so one redirection isolates them all."""
    from control import operations
    original = operations.ledger_path
    checkout_backend = test_paths.BACKEND

    def isolated(root):
        if Path(root).resolve() == checkout_backend:
            root = tmp_path / 'operation-root'
        return original(root)

    monkeypatch.setattr(operations, 'ledger_path', isolated)


@pytest.fixture(autouse=True)
def _device_lease_in_tmp_path(tmp_path, monkeypatch):
    """Offline tests can never reserve the user's actual emulator."""
    from runtime import device_lease
    device_lease.release_current()
    monkeypatch.setattr(device_lease, "_ROOT", tmp_path / "device-leases")
    yield
    device_lease.release_current()


@pytest.fixture(autouse=True)
def _no_test_reaches_the_real_process_kernel(monkeypatch):
    """Every destructive process action goes through runtime.process_handle's kernel. An empty fake one
    is the default, so no test can open, query or terminate a real process by accident: a test that
    wants processes builds them (harvest_support.fake_platform / make_psutil) and the few that exercise
    the real kernel on children they spawned themselves ask for the real_kernel fixture."""
    import psutil
    from harvest_support import install_kernel
    install_kernel(monkeypatch)
    yield
    clear = getattr(psutil.process_iter, 'cache_clear', None)   # a test may have replaced process_iter by a plain function
    if clear is not None:
        clear()   # process_iter keeps Process objects between calls: none built over a fake platform may leak


@pytest.fixture
def real_kernel(monkeypatch):
    """The real Win32 kernel, for tests that spawn their own child processes and stop only those."""
    from runtime import process_handle
    monkeypatch.setattr(process_handle, 'KERNEL', None)
    return process_handle.kernel()


@pytest.fixture(autouse=True)
def _event_log_in_tmp_path(tmp_path, monkeypatch):
    """Point runtime.logger's JSONL events and trigger screenshots at this
    test's tmp_path instead of backend/logs/<instance>/.

    The logger fixes its file on the first event under the real log dir, and
    the dashboard / tray read the newest events_*.jsonl there as live activity,
    so every test whose code path reached an unpatched logger.event() wrote
    fixture records into the user's log. Resetting `_run_file` makes the next
    event name a fresh file under the patched dir; tests that assert on events
    still patch logger.event themselves."""
    from runtime import logger

    monkeypatch.setattr(logger, "_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(logger, "_run_file", None)


@pytest.fixture(autouse=True)
def _dashboard_diagnostics_in_tmp_path(tmp_path, monkeypatch):
    """Request diagnostics must never contaminate a live dashboard event log."""
    from control import diagnostics
    original = diagnostics.log_root
    checkout = test_paths.BACKEND

    def isolated(root):
        return tmp_path / 'dashboard-logs' if Path(root).resolve() == checkout else original(root)

    monkeypatch.setattr(diagnostics, 'log_root', isolated)


# ------------------------------------------------ the dashboard under test
class Spawn:
    """Process creation stand-in: records every spawn, starts nothing."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kwargs):
        import os
        from types import SimpleNamespace
        self.calls.append(cmd)
        return SimpleNamespace(pid=os.getpid())


def seed_root(root):
    """A temporary backend root for the dashboard: its own config, profile and digit font."""
    import cv2
    import numpy as np
    root = Path(root)
    (root / 'profiles').mkdir(parents=True, exist_ok=True)
    cfg = dict(active_instance='main', active_profile='mine', preset='bp_farm',
               instances={'main': {'serial': 'fake:1', 'allow_taps': True}, 'other': {'serial': 'fake:2'}},
               loadouts={}, presets={})
    profile = dict(player={}, blueprints={
        'farm': {'kind': 'coin', 'tier': 1, 'loadout': 'as_is', 'policies': {'gather': 'shared'}}},
        policies={'gather': {'shared': {'ad_gems': True, 'gem_delay_sec': [3, 10]}}})
    (root / 'config.yaml').write_text(yaml.safe_dump(cfg))
    (root / 'profiles' / 'mine.yaml').write_text(yaml.safe_dump(profile))
    for digit in range(10):   # synthetic digit font for the collector precheck
        path = root / 'templates' / 'digits' / f'{digit}.png'
        path.parent.mkdir(parents=True, exist_ok=True)
        assert cv2.imwrite(str(path), np.full((8, 6), 40 + digit, np.uint8))


@pytest.fixture
def dash(tmp_path, monkeypatch):
    """backend/dashboard.py loaded against a temporary backend root: its own
    config, profile, digit font and ledger; adb pointed at a closed local port."""
    import importlib.util
    import socket

    from control import operations
    from device import adbclient
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    monkeypatch.setattr(adbclient, '_HOST', s.getsockname())  # never the real adb server
    s.close()
    source = test_paths.DASHBOARD
    spec = importlib.util.spec_from_file_location('admission_dashboard_test', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    seed_root(tmp_path)
    monkeypatch.setattr(module, 'ROOT', str(tmp_path))
    monkeypatch.setattr(module, 'CONFIG_PATH', str(tmp_path / 'config.yaml'))
    from harvest_support import fake_inspect
    monkeypatch.setattr(operations, 'inspect_worker', fake_inspect(lambda worker: True))
    return module


@pytest.fixture
def launches(dash, monkeypatch):
    """Real admission through control.operations; only process creation is faked."""
    from player import readiness
    spawn = Spawn()
    monkeypatch.setattr(dash.subprocess, 'Popen', spawn)
    monkeypatch.setattr(readiness, 'check', lambda root, cfg, preset: {'ready': True, 'missing': []})
    monkeypatch.setattr(dash, '_procs', lambda: [])
    monkeypatch.setattr(dash, '_procs_refresh', lambda: None)
    return spawn.calls


@pytest.fixture
def runner_rows(monkeypatch):
    """The machine-wide process table boundary: no real process is ever read."""
    from runtime import runners
    rows = []
    monkeypatch.setattr(runners, 'other_runners', lambda target=None: iter(list(rows)))
    return rows


@pytest.fixture
def worker_env(dash, monkeypatch):
    """The process state a worker bootstrap rewrites, so a test that runs the real operation bootstrap
    in-process leaves nothing behind. (The settings table it replaces is put back by the suite-wide
    _settings_singletons_restored.)"""
    import sys

    import settings
    from player import playerprofile
    monkeypatch.setattr(settings, 'ROOT', Path(dash.ROOT))
    monkeypatch.setattr(playerprofile, 'PROFILE', getattr(playerprofile, 'PROFILE', None), raising=False)
    monkeypatch.setattr(sys, 'argv', list(sys.argv))


def pytest_addoption(parser):
    parser.addoption('--regenerate-harvest-fixture', action='store_true', default=False,
                     help='Rewrite backend/tests/fixtures/harvest_status_v1.json from the current code. '
                          'Normal runs only compare against it.')

# ------------------------------------------------ the live ports no test may touch
# 16384 is the MuMu emulator's adb port: a test that dials it passes only on a machine where the emulator runs.
LIVE_PORTS = {5037: 'adb server', 8620: 'live dashboard', 8630: 'live worker', 16384: 'MuMu emulator adb port'}
_LIVE_PORT_HITS = []


def _live_port_of(address):
    """The protected port a socket address names, else None (loopback ephemeral ports the fixtures open themselves pass)."""
    if isinstance(address, tuple) and len(address) >= 2 and isinstance(address[1], int):
        return address[1] if address[1] in LIVE_PORTS else None
    return None


def pytest_configure(config):
    """Session-wide: connect()/connect_ex() to 5037, 8620, 8630 or 16384 never leaves the process. The attempt is
    recorded with its thread and stack, and refused with ConnectionRefusedError so nothing live is reached even
    from a background thread whose error nobody sees; the _no_live_port_connect fixture then fails the test."""
    import socket
    import threading
    import traceback
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def record(address):
        _LIVE_PORT_HITS.append(f"{address!r} ({LIVE_PORTS[_live_port_of(address)]}) from thread "
                               f"{threading.current_thread().name!r}\n{''.join(traceback.format_stack(limit=25))}")

    def connect(self, address):
        if _live_port_of(address) is not None:
            record(address)
            raise ConnectionRefusedError(f"test socket guard refused {address!r}")
        return real_connect(self, address)

    def connect_ex(self, address):
        if _live_port_of(address) is not None:
            record(address)
            return 10061
        return real_connect_ex(self, address)

    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
    config._live_port_originals = (real_connect, real_connect_ex)


def pytest_unconfigure(config):
    import socket
    originals = getattr(config, '_live_port_originals', None)
    if originals:
        socket.socket.connect, socket.socket.connect_ex = originals


@pytest.fixture(autouse=True)
def _own_priority_unchanged(monkeypatch):
    """No test changes the test process's real Windows priority class: the gem collector raises its own class at startup
    (runtime.priority), and every test that runs gem_collector.main would otherwise raise pytest's. Tests of that call fake
    _set_own_class themselves, which overrides this."""
    from runtime import priority
    monkeypatch.setattr(priority, '_set_own_class', lambda flag: None)


@pytest.fixture(autouse=True)
def _no_live_port_connect():
    """Fails the test during which anything (a background thread included) tried to reach the live adb server,
    gem collector or dashboard. Hits are never discarded: one from a thread that outlives its test is reported by
    the next test to end, and by pytest_sessionfinish (which makes the run fail) when no test follows."""
    yield
    assert not _LIVE_PORT_HITS, "a test reached a live port:\n" + "\n".join(_drain_live_port_hits())


def _drain_live_port_hits():
    hits = list(_LIVE_PORT_HITS)
    _LIVE_PORT_HITS.clear()
    return hits


def pytest_sessionfinish(session, exitstatus):
    hits = _drain_live_port_hits()
    if hits:
        print("\nLIVE PORT CONNECT ATTEMPTS AFTER THE LAST TEST:\n" + "\n".join(hits))
        session.exitstatus = 1


@pytest.fixture(autouse=True)
def _reconnect_threads_end_with_their_test(monkeypatch):
    """The dashboard starts an 'adb-reconnect' thread from any status poll or harvest tick that sees the device down.
    It dials adbclient._HOST when it runs, and a test's monkeypatches (the closed local port, FakeAdb) are undone
    when the test ends: a thread still running then dials the real adb server on 127.0.0.1:5037. This fixture is
    requested after `monkeypatch`, so it tears down first and the thread ends while the test's patches still hold."""
    yield
    join_reconnect_threads()


def join_reconnect_threads(timeout=20.0):
    """Wait for every dashboard 'adb-reconnect' thread (a contract world calls this before its own patches unwind)."""
    import threading
    for thread in threading.enumerate():
        if thread.name == 'adb-reconnect' and thread is not threading.current_thread():
            thread.join(timeout)
            assert not thread.is_alive(), 'an adb-reconnect thread outlived its test'
