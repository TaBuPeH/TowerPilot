"""Every place that signals a process goes through a kernel handle (runtime.process_handle).

The dashboard's Stop and kill route are covered in test_process_identity.py and the kernel layer in
test_process_handle.py. This file covers the other callers - the tray that supervises orchestrators, the
scheduler that adopts a runner it did not start - and holds a structural guard so a new
`proc.terminate()` / `os.kill(pid)` cannot appear anywhere in the source tree without failing a test, however
it is spelled: called or merely referenced, imported by name (with or without an alias), reached through
`getattr(os, 'kill')`. A pid that passed to another process is never signalled, whichever caller asks."""

import paths as test_paths
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime import process_handle
from scheduling import combo

PID, CHILD, OTHER = 920001, 920002, 920003
T0 = 1_790_000_000.5


@pytest.fixture
def os_():
    return process_handle.kernel().os


@pytest.fixture
def tray(monkeypatch, tmp_path):
    from runtime import tray as module
    monkeypatch.setattr(module, 'log', lambda message: None)         # never write the real tray log
    return module


def instance(tray):
    return tray.Instance('main', {'label': 'main'})


def walked(pid, created):
    """What psutil.process_iter hands the tray for a process it matched by command line."""
    return SimpleNamespace(pid=pid, create_time=lambda: created)


# ---------------------------------------------------------------- tray: orphans
def test_the_tray_stops_an_orphan_and_its_children_through_kernel_handles(os_, tray, monkeypatch):
    os_.start(PID, T0, ppid=100)
    os_.start(CHILD, T0 + 1, ppid=PID)
    os_.start(OTHER, T0 - 50, ppid=100)
    inst = instance(tray)
    monkeypatch.setattr(inst, 'orphans', lambda: [walked(PID, T0)])
    assert inst.kill_orphans() == 1
    assert [pid for pid, _ in os_.kills] == [CHILD, PID] and os_.table == {OTHER: T0 - 50}


def test_the_tray_never_stops_a_process_that_took_the_pid_after_the_scan(os_, tray, monkeypatch):
    os_.start(PID, T0 + 100, ppid=100)                              # the orphan exited; another process has its pid now
    inst = instance(tray)
    monkeypatch.setattr(inst, 'orphans', lambda: [walked(PID, T0)])
    assert inst.kill_orphans() == 1 and os_.kills == [] and os_.table == {PID: T0 + 100}


def test_an_orphan_the_kernel_will_not_open_is_reported_not_signalled(os_, tray, monkeypatch):
    os_.start(PID, T0)
    os_.denied.add(PID)
    messages = []
    monkeypatch.setattr(tray, 'log', messages.append)
    inst = instance(tray)
    monkeypatch.setattr(inst, 'orphans', lambda: [walked(PID, T0)])
    inst.kill_orphans()
    assert os_.kills == [] and any('not stopped (unverified)' in message for message in messages)


# ---------------------------------------------------------------- tray: the orchestrator it started
def test_the_tray_stops_the_orchestrator_it_started_and_its_family(os_, tray, monkeypatch):
    os_.start(PID, T0, ppid=100)
    os_.start(CHILD, T0 + 1, ppid=PID)
    inst = instance(tray)
    inst.proc, inst.preset = SimpleNamespace(pid=PID, poll=lambda: None), 'bp_farm'
    monkeypatch.setattr(inst, '_collecting_runlog', lambda: False)
    inst.stop()
    assert [pid for pid, _ in os_.kills] == [CHILD, PID] and inst.proc is None


def test_the_tray_reports_an_orchestrator_that_would_not_exit(os_, tray, monkeypatch):
    os_.start(PID, T0)
    os_.lingering = True
    messages = []
    monkeypatch.setattr(tray, 'log', messages.append)
    inst = instance(tray)
    inst.proc, inst.preset = SimpleNamespace(pid=PID, poll=lambda: None), 'bp_farm'
    monkeypatch.setattr(inst, '_collecting_runlog', lambda: False)
    inst.stop()
    assert any('not confirmed stopped (stopping)' in message for message in messages)


# ---------------------------------------------------------------- scheduler: a runner it adopted
def adopted(created=T0):
    return combo._Adopted(SimpleNamespace(pid=PID, create_time=lambda: created, is_running=lambda: True))


def test_an_adopted_runner_is_stopped_through_a_handle_verified_against_the_creation_time_the_walk_saw(os_):
    os_.start(PID, T0)
    os_.start(CHILD, T0 + 1, ppid=PID)
    adopted().terminate()
    assert os_.kills == [(PID, T0)] and CHILD in os_.table          # only the adopted process, as before


def test_an_adopted_runner_whose_pid_went_to_another_process_is_never_stopped(os_):
    os_.start(PID, T0 + 100)
    adopted().terminate()
    adopted().kill()
    assert os_.kills == [] and os_.table == {PID: T0 + 100}


def test_an_adopted_runner_the_kernel_will_not_verify_refuses_instead_of_guessing(os_):
    os_.start(PID, T0)
    os_.denied.add(PID)
    with pytest.raises(PermissionError, match='could not be verified'):
        adopted().terminate()
    assert os_.kills == []


# ---------------------------------------------------------------- the structural guard
SOURCE_ROOT = test_paths.REPO
SIGNALS = {'terminate', 'kill', 'send_signal', 'killpg', 'wait_procs', 'TerminateProcess', 'TerminateJobObject',
           'NtTerminateProcess'}
STAR_SOURCES = {'os', 'signal', 'psutil', 'subprocess', 'multiprocessing', 'ctypes'}   # `from os import *` could bring `kill` in unnamed
# (file, receiver) pairs that signal a subprocess.Popen object the code itself spawned and still holds. Its
# handle pins the pid, so nothing else can be at that pid; each is a best-effort fallback after the handle
# path, never the way a recorded or scanned process is stopped.
OWNED_POPEN = {('backend/device/preview/mumu.py', 'process', 'kill')}


def signal_references(tree):
    """(receiver, name, line) of every place a module tree NAMES a signalling function, however it is spelled:

      * attribute access, called or not: `os.kill(pid, 9)`, `proc.terminate()`, `stop = os.kill`,
        `partial(os.kill, pid)`, `map(psutil.Process.terminate, procs)`, `kernel32.TerminateProcess(h, 1)`;
      * an import of one by name, with or without an alias: `from os import kill as stop`;
      * a star import from a module that has one: `from os import *`;
      * a string naming one handed to getattr/setattr/hasattr/delattr or used as a subscript:
        `getattr(os, 'kill')`, `os.__dict__['kill']`.

    This is a static guard, not a sandbox: a name assembled at run time (`getattr(os, name)`) is not seen."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in SIGNALS:
            found.append((ast.unparse(node.value), node.attr, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in SIGNALS or (alias.name == '*' and (node.module or '').split('.')[0] in STAR_SOURCES):
                    found.append((node.module or '.', alias.name, node.lineno))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('getattr', 'setattr', 'hasattr', 'delattr'):
            named = node.args[1] if len(node.args) > 1 else None
            if isinstance(named, ast.Constant) and named.value in SIGNALS:
                found.append((ast.unparse(node.args[0]), named.value, node.lineno))
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value in SIGNALS:
            found.append((ast.unparse(node.value), node.slice.value, node.lineno))
    return found


def signalling_calls(root=SOURCE_ROOT):
    found = []
    scanned = set()
    files = list((root / 'backend').rglob('*.py'))
    for path in files:
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if ('/tests/' in relative or '/logs/' in relative or '/accounts/' in relative or relative.startswith('backend/tools/')
                or relative == 'backend/runtime/process_handle.py'):   # the one module that IS the kernel boundary
            continue
        text = path.read_text(encoding='utf-8-sig')
        scanned.add(relative)
        for receiver, name, line in signal_references(ast.parse(text, filename=relative)):
            found.append((relative, receiver, name, line))
        for token in ('taskkill', 'Stop-Process', 'TerminateProcess('):
            if token in text:
                found.append((relative, token, 'text', 0))
    if root == SOURCE_ROOT:
        assert 'backend/orchestrator.py' in scanned
        assert 'backend/dashboard.py' in scanned      # the dashboard is scanned by the backend walk
    return found


def test_no_source_file_signals_a_process_except_through_process_handle():
    stray = [(file, receiver, call, line) for file, receiver, call, line in signalling_calls()
             if (file, receiver, call) not in OWNED_POPEN]
    assert stray == [], f'a process is signalled outside runtime.process_handle: {stray}'


FORBIDDEN_SPELLINGS = {
    'method call': 'proc.terminate()',
    'os.kill': 'import os\nos.kill(1, 9)',
    'kill in a loop': 'for p in kids:\n    p.kill()',
    'psutil.wait_procs': 'import psutil\npsutil.wait_procs(family)',
    'import by name': 'from os import kill\nkill(1, 9)',
    'import with an alias': 'from os import kill as stop\nstop(1, 9)',
    'import of killpg with an alias': 'from os import killpg as stop_group\nstop_group(1, 9)',
    'import from psutil': 'from psutil import wait_procs as waiter\nwaiter(family)',
    'module alias': 'import os as operating_system\noperating_system.kill(1, 9)',
    'assigned to a name': 'import os\nstop = os.kill\nstop(1, 9)',
    'passed uncalled': 'import functools, os\nstopper = functools.partial(os.kill, 1)',
    'unbound method mapped over processes': 'import psutil\nlist(map(psutil.Process.terminate, procs))',
    'getattr with a constant': "import os\ngetattr(os, 'kill')(1, 9)",
    'subscript of a namespace': "import os\nos.__dict__['kill'](1, 9)",
    'ctypes TerminateProcess': 'import ctypes\nctypes.windll.kernel32.TerminateProcess(handle, 1)',
    'star import': 'from os import *\nkill(1, 9)',
}
SANCTIONED_SPELLINGS = {
    'the handle-bound helper': 'from runtime import process_handle\nprocess_handle.terminate_tree(1, 2.0)',
    'a function that is merely called kill_orphans': 'def kill_orphans(self):\n    return 0\nself.kill_orphans()',
    'a method definition': 'class Adopted:\n    def kill(self):\n        return None',
    'the word in a string': "message = 'kill the process'",
    'getattr of something else': "getattr(os, 'getpid')()",
    'star import of an unrelated module': 'from math import *',
}


@pytest.mark.parametrize('source', FORBIDDEN_SPELLINGS.values(), ids=FORBIDDEN_SPELLINGS.keys())
def test_the_guard_sees_every_spelling_it_exists_to_forbid(source):
    assert signal_references(ast.parse(source)), source


@pytest.mark.parametrize('source', SANCTIONED_SPELLINGS.values(), ids=SANCTIONED_SPELLINGS.keys())
def test_the_guard_leaves_the_sanctioned_idioms_alone(source):
    assert signal_references(ast.parse(source)) == [], source


def test_an_aliased_import_planted_in_the_source_tree_fails_the_scan(tmp_path):
    plant = tmp_path / 'backend' / 'control' / 'sneaky.py'
    plant.parent.mkdir(parents=True)
    plant.write_text('from os import kill as stop\n\n\ndef go(pid):\n    stop(pid, 9)\n', encoding='utf-8')
    clean = tmp_path / 'backend' / 'control' / 'fine.py'
    clean.write_text('from runtime import process_handle\n\n\ndef go(pid, created):\n    return process_handle.terminate_tree(pid, created)\n', encoding='utf-8')
    assert signalling_calls(tmp_path) == [('backend/control/sneaky.py', 'os', 'kill', 1)]


def test_the_one_allowed_popen_kill_is_still_there_so_the_allowance_cannot_go_stale():
    assert {(f, r, c) for f, r, c, _ in signalling_calls()} == OWNED_POPEN


# ---------------------------------------------------------------- the same rule for the tests themselves (H2R7-06)
# A test that cleans up after itself by killing whatever a scan found at a pid is the same defect as product code
# doing it: the pid may belong to another process by then. Tests may signal only the objects below, each a
# subprocess.Popen the test itself started (it holds the process handle, which pins the pid), a Mock or fake
# of one, a method of a fake kernel, or a dictionary key spelled like a signal. A scanned process is stopped with
# process_handle.terminate_tree(pid, created), as test_process_handle.stop_identities does.
TEST_OWNED = {
    'backend/tests/recording/dashboard_contract_world.py': {('kernel', 'terminate')},
    'backend/tests/unit/test_daystate_serialization.py': {('child', 'kill')},      # a Popen the test started: a child that holds the daily-state lock, killed to show the OS frees it
    'backend/tests/unit/test_delegated_driver.py': {('child', 'terminate'), ('child', 'kill')},
    'backend/tests/unit/test_device_lease.py': {('first', 'kill')},
    'backend/tests/unit/test_file_lock.py': {('child', 'kill')},                    # the same, for the primitive
    'backend/tests/unit/test_harvest_contract.py': {('out', 'kill'), ('stop', 'kill')},
    'backend/tests/unit/test_preview_mumu.py': {('launcher', 'kill'), ('process', 'kill')},
    'backend/tests/unit/test_process_handle.py': {('FakeKernel', 'terminate'), ('process_handle.KERNEL', 'terminate'),
                                             ('process_handle.kernel()', 'terminate'), ('child', 'kill')},
    'backend/tests/unit/test_process_signal_sites.py': {('adopted()', 'terminate'), ('adopted()', 'kill')},
}


def signalling_references_in_tests(root=SOURCE_ROOT):
    found = []
    scanned = set()
    for path in sorted((root / 'backend' / 'tests').rglob('*.py')):
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=relative)
        scanned.add(relative)
        for receiver, name, line in signal_references(tree):
            found.append((relative, receiver, name, line))
    if root == SOURCE_ROOT:
        assert 'backend/tests/conftest.py' in scanned
    return found


def test_no_test_signals_a_process_it_found_by_scanning():
    stray = [(file, receiver, name, line) for file, receiver, name, line in signalling_references_in_tests()
             if (receiver, name) not in TEST_OWNED.get(file, set())]
    assert stray == [], f'a test signals something that is not a process it started itself: {stray}'


def test_the_test_guard_flags_a_scanned_pid_kill(tmp_path):
    plant = tmp_path / 'backend' / 'tests' / 'test_sneaky.py'
    plant.parent.mkdir(parents=True)
    plant.write_text('import psutil\n\n\ndef test_x(marker):\n    for pid in scan(marker):\n        psutil.Process(pid).kill()\n',
                     encoding='utf-8')
    assert [(file, receiver, name) for file, receiver, name, _ in signalling_references_in_tests(tmp_path)] == [
        ('backend/tests/test_sneaky.py', 'psutil.Process(pid)', 'kill')]
    assert ('psutil.Process(pid)', 'kill') not in TEST_OWNED.get('backend/tests/test_sneaky.py', set())
