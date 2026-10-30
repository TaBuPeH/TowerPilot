"""The latency gate under the three whole-process import histories (roadmap item 7, spec 3.7, package r18f prerequisite F-1). The harness: `latency_gate.py`; the gate and its in-process
controls: `test_action_latency_gate.py`.

Which `scheduling.global_rewards` the orchestrator holds depends on which module imported it first in the process, so the same inserted statement can pass or fail the structural gate depending on the
order the suite ran. Each history below is a fresh interpreter (nothing an earlier test did can leak into it):
  alone        nothing imported earlier (the gate file run by itself)
  real_first   `scheduling.global_rewards` imported for real, bound to the real daily state (what test_action_parity_rewards.py does)
  p4_first     `test_p4_interpreter._import_live('orchestrator')` ran first and left `scheduling.global_rewards` bound to ITS fake daily state
In each, the unmutated pass is clean and leaves the binding it found, and a daily-state read, write, `global_rewards.decide` and `global_rewards.settle` inserted before the fire tap, and three
late imports of the daily state, are all refused with exactly the counts the real module registers. The child sets `daystate.STATE` to a canary path of its own before anything runs: the gate must
redirect its writes elsewhere, so the canary must never appear.

What this does not fix: `test_p4_interpreter._import_live` itself still leaves `scheduling.global_rewards` bound to a fake for the rest of ITS process (a later parity test fails there); that leak
is not this harness's and is not repaired here. This file only proves the gate neither depends on it nor adds to it.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import latency_gate as gate
import paths as test_paths

HISTORIES = ('alone', 'real_first', 'p4_first')
# variant name: (statement inserted before the fire tap of the pinned snapshot, file_open and lock_acquire it must add before the first tap, optional line added at MODULE level of the snapshot)
# The alias variants take the function by name at import time, which is when a consumer binds whatever the fake daily state held then: the meter must already be on that function.
VARIANTS = {
    'read': ('global_rewards.daystate.get_raw("gate_probe")', (1, 0)),
    'write': ('global_rewards.daystate.set_raw("gate_probe", 1)', (3, 3)),
    'decide': ('global_rewards.decide("free_store_gems")', (1, 0)),
    'settle': ('global_rewards.settle("free_store_gems")', (3, 3)),
    'late_daystate_read': ('from scheduling import daystate as _ds; _ds.get_raw("gate_probe")', (1, 0)),
    'late_daystate_write': ('from scheduling import daystate as _ds; _ds.set_raw("gate_probe", 1)', (3, 3)),
    'late_global_rewards_read': ('from scheduling import global_rewards as _gr; _gr.daystate.get_raw("gate_probe")', (1, 0)),
    'alias_get_raw': ('_alias_get_raw("gate_probe")', (1, 0), 'from scheduling.daystate import get_raw as _alias_get_raw'),
    'alias_set_raw': ('_alias_set_raw("gate_probe", 1)', (3, 3), 'from scheduling.daystate import set_raw as _alias_set_raw'),
    'alias_get_today': ('_alias_get_today("gate_probe")', (1, 0), 'from scheduling.daystate import get_today as _alias_get_today'),
    'alias_mark_today': ('_alias_mark_today("gate_probe")', (3, 3), 'from scheduling.daystate import mark_today as _alias_mark_today'),
    'alias_decide': ('_alias_decide("free_store_gems")', (1, 0), 'from scheduling.global_rewards import decide as _alias_decide'),
    'alias_settle': ('_alias_settle("free_store_gems")', (3, 3), 'from scheduling.global_rewards import settle as _alias_settle'),
}

SOCKET_GUARD = """
# conftest's socket guard is installed by pytest_configure and is not active in a `python -c` child: an equivalent one, in place before anything else runs.
import socket
LIVE = {5037, 8620, 8630}
refused = []


def refuse(address):
    loopback = isinstance(address, tuple) and len(address) > 1 and address[0] in ('127.0.0.1', '::1', 'localhost')
    if not loopback or address[1] in LIVE:
        refused.append(repr(address))
        return True
    return False


def guarded_connect(self, address):
    refuse(address)
    raise ConnectionRefusedError('child socket guard: no connect from the gate ' + repr(address))


def guarded_connect_ex(self, address):
    refuse(address)
    return 10061


socket.socket.connect, socket.socket.connect_ex = guarded_connect, guarded_connect_ex
probes = []
for probe in (('127.0.0.1', 5037), ('127.0.0.1', 8620), ('127.0.0.1', 8630), ('192.0.2.1', 80)):
    try:
        socket.socket().connect(probe)
        probes.append(False)
    except ConnectionRefusedError:
        probes.append(True)
guard_active = all(probes) and len(refused) == 4
refused.clear()
"""

HISTORY_CHILD = """
import hashlib, importlib, json, os, shutil, sys, tempfile
from pathlib import Path
import conftest                                                    # the shipped config, as the suite loads it
import latency_gate as gate
from scheduling import daystate as real_state
work = Path(tempfile.mkdtemp(prefix='latency-order-'))
canary = work / 'machine_daily_state.json'
machine = real_state.STATE


def machine_file():
    # the machine's own daily-state file and its lock file right now: existence, size, mtime and content hash
    seen = []
    for name in (machine, machine + '.lock'):
        target = Path(name)
        seen.append(None if not target.exists() else [target.stat().st_size, target.stat().st_mtime_ns, hashlib.sha256(target.read_bytes()).hexdigest()])
    return seen


machine_before = machine_file()
real_state.STATE = str(canary)                                     # a stand-in for the machine's own file: the gate must write nowhere near it
history, variants = sys.argv[1], json.loads(sys.argv[2])
import scheduling
sys.modules.pop('scheduling.global_rewards', None)
if hasattr(scheduling, 'global_rewards'):
    delattr(scheduling, 'global_rewards')
if history == 'real_first':
    importlib.import_module('scheduling.global_rewards')
elif history == 'p4_first':
    from test_p4_interpreter import _import_live
    _import_live('orchestrator')


def binding():
    module = sys.modules.get('scheduling.global_rewards')
    if module is None:
        return None
    return ['real' if module.daystate is real_state else ('fake' if module.daystate.__spec__ is None else 'other'), id(module), id(module.daystate)]


baseline = json.loads(gate.BASELINE_PATH.read_text(encoding='utf-8'))
text = gate.SNAPSHOT_PATH.read_text(encoding='utf-8')
tap = '        ev = act.tap(*st.center, reason=reason)\\n'
assert text.count(tap) == 1
found = binding()
out = {}
for name, statement, preamble in [('control', None, None)] + [(k, v[0], v[2] if len(v) > 2 else None) for k, v in variants.items()]:
    path = work / (name + '.py.txt')
    probed = text if statement is None else text.replace(tap, '        ' + statement + '\\n' + tap)
    if preamble:
        assert probed.count('\\nimport json\\n') == 1
        probed = probed.replace('\\nimport json\\n', '\\nimport json\\n' + preamble + '\\n')
    path.write_text(probed, encoding='utf-8', newline='\\n')
    counts = gate.structural_counts(path)
    before = counts['fire_button_with_confirmation']['before_first_tap']
    out[name] = dict(failures=gate.structural_failures(counts, baseline), file_open=before['file_open'], lock_acquire=before['lock_acquire'])
    if name == 'control':
        out['control']['binding_unchanged'] = binding() == found
result = dict(history=history, variants=out, found=found and found[0], canary_written=canary.exists() or Path(str(canary) + '.lock').exists(), machine_state_unchanged=machine_file() == machine_before and machine != str(canary),
              socket_guard_active=guard_active, socket_hits_during_run=refused)
shutil.rmtree(work, ignore_errors=True)
print(json.dumps(result))
"""

NEXT_IMPORTER_CHILD = """
import json
import conftest
import latency_gate as gate
loaded = gate.load_orchestrator(gate.SNAPSHOT_PATH, '_probe')
gate.structural_counts(gate.SNAPSHOT_PATH)
fakes = [fake for fake in loaded._fakes.values() if getattr(fake, '__spec__', None) is None]
left_bound = sorted(gate.modules_bound_to(fakes))
from scheduling import daystate, global_rewards
from device import adbclient, overlays
print(json.dumps(dict(socket_guard_active=guard_active, socket_hits=refused, left_bound=left_bound, global_rewards_has_the_real_daily_state=global_rewards.daystate is daystate and daystate.__spec__ is not None,
                      overlays_has_the_real_adb_client=overlays.adbclient is adbclient and adbclient.__spec__ is not None)))
"""


def run_child(code, *args):
    paths = [str(test_paths.BACKEND), str(test_paths.TESTS), str(test_paths.TESTS / 'support'), str(test_paths.TESTS / 'unit')]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(paths + ([os.environ['PYTHONPATH']] if os.environ.get('PYTHONPATH') else [])), PYTHONDONTWRITEBYTECODE='1')
    run = subprocess.run([sys.executable, '-c', SOCKET_GUARD + code, *args], cwd=str(gate.BACKEND), env=env, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stderr[-3000:]
    return json.loads(run.stdout.strip().splitlines()[-1])


@pytest.fixture(scope='module', params=HISTORIES)
def history_result(request):
    return run_child(HISTORY_CHILD, request.param, json.dumps(VARIANTS))


def test_the_unmutated_pass_is_clean_and_leaves_the_binding_it_found(history_result):
    control = history_result['variants']['control']
    assert control['failures'] == [] and (control['file_open'], control['lock_acquire']) == (0, 0)
    assert control['binding_unchanged'], 'the gate changed which scheduling.global_rewards the process holds'
    assert history_result['found'] == {'alone': None, 'real_first': 'real', 'p4_first': 'fake'}[history_result['history']], 'the history did not produce the binding it names'


@pytest.mark.parametrize('variant', sorted(VARIANTS))
def test_a_daily_state_statement_before_the_tap_is_refused_with_the_real_modules_counts_in_every_history(history_result, variant):
    got = history_result['variants'][variant]
    assert got['failures'], f"{history_result['history']}: {variant} was not seen by the structural gate"
    assert (got['file_open'], got['lock_acquire']) == VARIANTS[variant][1], got
    assert any('before_first_tap file_open' in line for line in got['failures']), got['failures']


def test_no_history_writes_the_daily_state_it_was_given(history_result):
    assert history_result['canary_written'] is False and history_result['machine_state_unchanged'] is True


def test_the_child_ran_behind_a_socket_guard_and_never_dialled_out(history_result):
    """The child is `python -c`, so conftest's guard is not there: the child installs its own (5037, 8620, 8630 and any non-loopback address refused) and proves it refuses before anything runs."""
    assert history_result['socket_guard_active'] is True and history_result['socket_hits_during_run'] == []


def test_after_the_gate_ran_the_next_importer_gets_the_real_daily_state_and_adb_client():
    """In a fresh interpreter, where no earlier test can have left anything: after the gate loaded an orchestrator and ran a structural pass, the first import of global_rewards and of overlays (both
    import modules the orchestrator fakes) binds the REAL ones. Before the fix they stayed bound to the fakes for the rest of the process (r18c review F-3, gate half): a parity test run after the
    gate file failed."""
    assert run_child(NEXT_IMPORTER_CHILD) == dict(socket_guard_active=True, socket_hits=[], left_bound=[], global_rewards_has_the_real_daily_state=True, overlays_has_the_real_adb_client=True)
