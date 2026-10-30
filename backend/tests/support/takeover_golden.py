"""Shared plumbing of the explicit-takeover golden answers (roadmap item 3, package T3-0).

The goldens are recorded on UNCHANGED code (backend/tests/fixtures/takeover_golden/*.json) so the packages that add the takeover (T3a to
T3c) can prove that ordinary, non-takeover behaviour did not change. A test observes a scenario and compares it with the recorded
document; it never writes. The recorder is the explicit developer command in unit/test_takeover_golden_capture.py.

Nothing here touches a device, a network, a process or a game image: every scenario runs over fakes and synthetic frames."""
import hashlib
import json
import os

import paths as test_paths
from fixture_json import write_fixture_json

BACKEND = test_paths.BACKEND
DIRECTORY = test_paths.FIXTURES / 'takeover_golden'
REGENERATE = 'TP_REGENERATE_TAKEOVER_GOLDEN'

# What each document characterizes: the files the answers come from. The recorder stores their digests in the document, so a reader can tell
# which code a golden was recorded on (the tests never compare them: a later package changes these files on purpose).
SOURCES = {
    'dashboard_start': ['backend/dashboard.py', 'backend/control/operations.py', 'backend/control/harvest.py', 'backend/control/battles.py'],
    'orchestrator_adoption': ['backend/orchestrator.py', 'backend/runtime/battle_session.py', 'backend/towerpilot_workflows/battles.py'],
    'fleet_marks': ['backend/orchestrator.py', 'backend/scheduling/fleets.py'],
    'battle_session': ['backend/runtime/battle_session.py', 'backend/towerpilot_workflows/battles.py', 'backend/control/battles.py'],
    'operation_worker': ['backend/runtime/operation_worker.py'],
    'readiness_requirements': ['backend/player/readiness.py'],
    'screen_readers': ['backend/vision/screen.py', 'backend/vision/detect.py'],
}


def roundtrip(value):
    """What the fixture file will hold: the JSON a document serializes to, read back (tuples become lists, keys become strings)."""
    return json.loads(json.dumps(value, sort_keys=True))


def load(name):
    return json.loads((DIRECTORY / f'{name}.json').read_text(encoding='utf-8'))


def recorded(name):
    return load(name)['scenarios']


def source_digests(name):
    root = BACKEND.parent
    return {rel: hashlib.sha256((root / rel).read_bytes()).hexdigest().upper() for rel in SOURCES[name]}


def write(name, about, scenarios, **provenance):
    """Write one document with the shared fixture writer (support/fixture_json.py). `recorded_on` is the digest of the files the answers come
    from at this moment; `provenance` adds named keys (the rebase of the recorded answers onto a later HEAD names itself there)."""
    document = dict(version=1, about=about, recorded_on=source_digests(name), scenarios=roundtrip(scenarios), **provenance)
    write_fixture_json(DIRECTORY / f'{name}.json', document)


def regenerating():
    return bool(os.environ.get(REGENERATE))


def assert_recorded(name, scenario, observed):
    """The scenario answers exactly what was recorded on the unchanged code."""
    expected = recorded(name)[scenario]
    assert roundtrip(observed) == expected


def assert_registry(name, scenarios):
    """The recorded document holds exactly the scenarios the tests run: a scenario deleted from the code cannot pass by never running."""
    assert sorted(recorded(name)) == sorted(scenarios)
