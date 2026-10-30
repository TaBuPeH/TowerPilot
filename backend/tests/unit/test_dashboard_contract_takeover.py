"""The recorded takeover contract (tests/fixtures/dashboard_contract_takeover_v1.json): each scenario is driven through the real routes and must record exactly
what the fixture holds. `TP_REGENERATE_TAKEOVER_FIXTURE=1` rewrites a scenario's slice (through support/fixture_json.py). Nothing here touches a device."""
import json
import os

import pytest

from dashboard_contract_cases_takeover import FIXTURE, SCENARIOS, VERSION, Recorder, read_fixture
from fixture_json import render_fixture_json, write_fixture_json
from takeover_http import install_readiness, make_app


@pytest.fixture
def app(dash, launches, monkeypatch):
    app = make_app(dash, launches, monkeypatch)
    app.readiness = install_readiness(monkeypatch)
    return app


def record(app, scenario, monkeypatch, tmp_path_factory):
    rec = Recorder(app)
    fn = SCENARIOS[scenario]
    fn(app, rec, monkeypatch, tmp_path_factory.mktemp('worker')) if fn.__code__.co_argcount == 4 else fn(app, rec)
    return json.loads(json.dumps(rec.cases))


@pytest.mark.parametrize('scenario', sorted(SCENARIOS))
def test_the_scenario_records_what_the_fixture_holds(app, scenario, monkeypatch, tmp_path_factory):
    cases = record(app, scenario, monkeypatch, tmp_path_factory)
    if os.environ.get('TP_REGENERATE_TAKEOVER_FIXTURE') == '1':
        document = read_fixture()
        document['scenarios'][scenario] = cases
        write_fixture_json(FIXTURE, document)
    held = read_fixture()['scenarios'].get(scenario)
    assert held is not None, f'{scenario} is not in {FIXTURE.name}; regenerate with TP_REGENERATE_TAKEOVER_FIXTURE=1'
    assert cases == held


def test_the_fixture_is_the_writers_own_text_and_names_exactly_the_scenarios():
    raw = FIXTURE.read_bytes()
    document = json.loads(raw.decode('utf-8'))
    assert raw == render_fixture_json(document).encode('utf-8')
    assert document['version'] == VERSION and sorted(document['scenarios']) == sorted(SCENARIOS)


def test_the_scrubber_names_ids_by_appearance_and_keeps_the_policy_revision():
    from dashboard_contract_cases_takeover import Scrubber
    scrub = Scrubber('/tmp/root')
    one, two = '11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222'
    digest = 'a' * 64
    assert scrub({'a': one, 'b': [two, one], 'policy_revision': digest, 'other': digest, 'server_time': 1.5, 'x_at': 2.0, 'n': 3,
                  'path': '/tmp/root/profiles/mine.yaml'}) == {
        'a': '<id-1>', 'b': ['<id-2>', '<id-1>'], 'policy_revision': digest, 'other': '<sha256>', 'server_time': 1800000000.0, 'x_at': 1800000000.0, 'n': 3,
        'path': '<root>/profiles/mine.yaml'}
