"""Record both Schedule/Runs save orders through real routes in the isolated contract world.

No running dashboard, device, live ledger, lease or process is used. Generate from
the repository root with python -m pytest backend/tests/recording/schedule_fixture_cases.py.
"""
import copy
import json

import paths
from fixture_json import write_fixture_json
from conftest import dash  # noqa: F401 - fixture with an isolated backend root
from dashboard_contract_pages_world import PagesGenerator, populate
from test_dashboard_contract import scrub


def test_generate_schedule_revision_races(tmp_path, monkeypatch, dash):
    gen = PagesGenerator(dash, tmp_path / 'schedule', monkeypatch)
    for first in ('schedule', 'runs'):
        family = f'{first}_first'
        with gen.world(family) as w:
            populate(w)
            w.get(family, 'config', '/api/config')
            source = w.get(family, 'profile_initial', '/api/profile-src/mine').get_json()
            run = w.get(family, 'run_initial', '/api/run-controls?blueprint=bp_farm').get_json()
            assert source['revision'] == run['profile_revision']
            plan = copy.deepcopy(source['profile']['plan'])
            plan['days']['farm_day'][0]['count'] = 60
            writes = {
                'schedule': ('/api/profile-patch', dict(name='mine', path=['plan'], value=plan,
                                                     expected_revision=source['revision'])),
                'runs': ('/api/run-controls', dict(blueprint=run['blueprint'], context=run['context'],
                                                 expected_revision=run['profile_revision'],
                                                 changes={'routines.ad_gems.enabled': False})),
            }
            second = 'runs' if first == 'schedule' else 'schedule'
            response = w.post(family, 'first_saved', *writes[first])
            assert response.status_code == 200, response.get_json()
            refused = w.post(family, 'second_stale', *writes[second])
            assert refused.status_code == 409, refused.get_json()
            latest = w.get(family, 'profile_after_first', '/api/profile-src/mine').get_json()
            run_latest = w.get(family, 'run_after_first', '/api/run-controls?blueprint=bp_farm').get_json()
            assert latest['revision'] == run_latest['profile_revision'] != source['revision']
            path, body = writes[second]
            retry = dict(body, expected_revision=latest['revision'])
            saved = w.post(family, 'second_saved_after_read', path, retry)
            assert saved.status_code == 200, saved.get_json()
            final = w.get(family, 'profile_final', '/api/profile-src/mine').get_json()
            final_run = w.get(family, 'run_final', '/api/run-controls?blueprint=bp_farm').get_json()
            assert final['revision'] == final_run['profile_revision'] != latest['revision']
            assert final['profile']['plan']['days']['farm_day'][0]['count'] == 60
            assert final_run['settings']['routines']['ad_gems']['enabled'] is False
    document = scrub(dict(version=1, generated_by='backend/tests/recording/schedule_fixture_cases.py',
                          cases=gen.cases), gen.base)
    write_fixture_json(paths.FRONTEND_FIXTURES / 'schedule-race-samples.json', document)
    assert all(len(cases) == 10 for cases in document['cases'].values())
