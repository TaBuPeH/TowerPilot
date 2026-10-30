"""The one harness of the three r18e parity files (roadmap item 7, A3b kind B: compiled rules, global Demon Mode, Ultimate Weapon retry).

Each parity file owns what is unique to its behaviour (the scenarios, the fuzz, the run function and the history assertions). What the three used to repeat lives
here once: the pinned unmigrated orchestrator and how it is loaded, the live module, the golden-fixture checks (captured on the pinned bytes, re-derived from them,
replayed by the live code), the capture command, the ledger session and its reader, and the broken-ledger comparison.
"""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import sys

import pytest

import paths as test_paths
from fixture_json import write_fixture_json
from runtime.action_session import ActionSession
from towerpilot_workflows.action_contract import FakeClock
from towerpilot_workflows.actions import Actions

SNAPSHOT = test_paths.FIXTURES / 'latency_gate' / 'orchestrator_unmigrated.py.txt'
REGENERATE = 'TP_REGENERATE_ACTION_R18E_GOLDEN'
CONTEXT = dict(device_key='device', account='account', instance='main', serial='fake', profile='mine')

# the capture command: an explicit developer step, only meaningful on the UNMIGRATED code
capture_only_on_unmigrated_code = pytest.mark.skipif(not os.environ.get(REGENERATE), reason='explicit developer command, run on the UNMIGRATED code only')


def golden_path(name):
    return test_paths.FIXTURES / 'action_r18e' / f'{name}_golden_v1.json'


def snapshot_sha():
    return hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest().upper()


def snapshot_module():
    """The pinned unmigrated orchestrator, loaded under a private name (the same bytes the capture ran on)."""
    loader = importlib.machinery.SourceFileLoader('_r18e_unmigrated_orchestrator', str(SNAPSHOT))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    sys.modules[loader.name] = module
    try:
        loader.exec_module(module)
    except BaseException:
        sys.modules.pop(loader.name, None)
        raise
    return module


def live():
    import orchestrator
    return orchestrator


def roundtrip(value):
    return json.loads(json.dumps(value, sort_keys=True))


def load_golden(name):
    return json.loads(golden_path(name).read_text(encoding='utf-8'))


def capture_golden(name, traces):
    """Write the golden traces of the CURRENT code (the explicit capture command; run on the unmigrated code only)."""
    write_fixture_json(golden_path(name), dict(version=1, captured_on={'backend/orchestrator.py': snapshot_sha()}, traces=traces(live())))


def assert_captured_on_the_pinned_snapshot(captured):
    assert captured['captured_on'] == {'backend/orchestrator.py': snapshot_sha()}


def assert_snapshot_reproduces(captured, traces):
    assert traces(snapshot_module()) == captured['traces']


def assert_live_replays(captured, traces):
    assert traces(live()) == captured['traces']


def without_record_failures(trace):
    """The trace with the session's own `action_record_failed` lines removed (a broken ledger says so, once per lost row; nothing else may differ)."""
    return [dict(row, events=[e for e in row['events'] if e[0] != 'action_record_failed']) for row in trace]


def make_session(tmp_path):
    return ActionSession(tmp_path / 'operations.db', context=CONTEXT, driver='orchestrator', clock=FakeClock(mono=1.0, wall=5000.0))


def broken_session(tmp_path):
    """A directory is not a ledger: every write fails inside the session, which counts it and raises nothing."""
    return ActionSession(tmp_path, context=CONTEXT, driver='orchestrator')


def history_rows(session, action):
    return Actions.read(session.path, device='device', action=action)[0]


def assert_rows_claim_no_measured_duration(rows):
    """A row written after the fact (`ActionSession.record`) carries no timing of the attempt: duration unmeasured (null, basis none), never zero."""
    assert rows
    for row in rows:
        assert row['duration_ms'] is None and row['duration_basis'] == 'none', (row['id'], row['duration_ms'], row['duration_basis'])
