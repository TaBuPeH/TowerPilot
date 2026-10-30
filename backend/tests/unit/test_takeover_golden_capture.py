"""Explicit developer command: record the takeover golden answers from the CURRENT code.

    TP_REGENERATE_TAKEOVER_GOLDEN=1 python -m pytest backend/tests/unit/test_takeover_golden_capture.py -p no:cacheprovider

Skipped in every normal run. The committed documents (backend/tests/fixtures/takeover_golden/) were recorded on the tree BEFORE the explicit
takeover existed (`recorded_on` holds the digests of the files they pin); regenerating them after the takeover landed would erase the proof, so
the other test_takeover_golden_* modules only ever compare."""
import json

import pytest

import takeover_golden as golden
from fixture_json import render_fixture_json
from takeover_golden_marks import MARKS
from takeover_golden_misc import READINESS, SCREEN, SESSION, WORKER
from takeover_golden_scenarios import ADOPTION
from takeover_golden_start import START

DOCUMENTS = {
    'dashboard_start': ('POST /api/control action start on the dashboard, with and without a collector (T3-0)', START, 'dashboard'),
    'orchestrator_adoption': ('orchestrator.main() over a battle already on screen: start, death branch, replaced battle (T3-0)', ADOPTION, 'directory'),
    'fleet_marks': ('scheduling.fleets and the five consumers of orchestrator.marks() (T3-0)', MARKS, 'directory'),
    'battle_session': ('BattleSession.from_environment predecessor link (T3-0)', SESSION, 'directory'),
    'operation_worker': ('the operation worker deletes stop_after_run for run operations (T3-0)', WORKER, 'directory'),
    'readiness_requirements': ('player.readiness.requirements over a synthetic matrix of compiled runs (T3-0)', READINESS, 'directory'),
    'screen_readers': ('vision.screen.in_tournament and identify over synthetic frames (T3-0)', SCREEN, 'directory'),
}
OBSERVED = {name: {} for name in DOCUMENTS}
CASES = [(name, scenario) for name, (_, registry, _) in DOCUMENTS.items() for scenario in sorted(registry)]
explicit = pytest.mark.skipif(not golden.regenerating(), reason='explicit developer command, never a test side effect')


@explicit
@pytest.mark.parametrize('document,scenario', CASES)
def test_observe_a_scenario(document, scenario, request, tmp_path, monkeypatch):
    _, registry, how = DOCUMENTS[document]
    if how == 'dashboard':
        observed = registry[scenario](request.getfixturevalue('dash'), request.getfixturevalue('launches'), monkeypatch)
    else:
        observed = registry[scenario](tmp_path, monkeypatch)
    OBSERVED[document][scenario] = golden.roundtrip(observed)


@explicit
def test_write_the_documents():
    for name, (about, registry, _) in DOCUMENTS.items():
        assert sorted(OBSERVED[name]) == sorted(registry), f'{name}: run the whole file, not a subset'
        golden.write(name, about, OBSERVED[name])
        assert golden.recorded(name) == OBSERVED[name]


@pytest.mark.parametrize('name', sorted(DOCUMENTS))
def test_a_recorded_document_is_the_shared_writers_own_text(name):
    raw = (golden.DIRECTORY / f'{name}.json').read_bytes()
    assert raw == render_fixture_json(json.loads(raw.decode('utf-8'))).encode('utf-8'), f'{name}.json was not written by support/fixture_json.py'


@pytest.mark.parametrize('name', sorted(DOCUMENTS))
def test_a_document_pins_the_files_it_characterizes(name):
    document = golden.load(name)
    assert {'about', 'recorded_on', 'scenarios', 'version'} <= set(document) <= {'about', 'rebased_on', 'recorded_on', 'scenarios', 'version'}
    assert sorted(document['recorded_on']) == sorted(golden.SOURCES[name]) or 'rebased_on' in document   # a rebased document keeps the paths it was recorded on
    if 'rebased_on' in document:
        assert sorted(document['rebased_on']['sources']) == sorted(golden.SOURCES[name])
