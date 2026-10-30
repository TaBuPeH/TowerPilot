"""Bounded route capture validates before I/O and never certifies full setup."""
import copy
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from player import bootstrap, calibrate, mapping_session
from runtime import operations


@pytest.mark.parametrize('routes', [
    ['route_17', 'route_18'],
    ['route_23', 'route_24', 'route_25', 'route_26', 'route_27'],
])
def test_supported_home_to_home_route_selections(routes):
    edges = bootstrap.validate_route_selection(routes)
    assert [edge['id'] for edge in edges] == routes
    assert edges[0]['source'] == edges[-1]['destination'] == 'home'
    assert all(a['destination'] == b['source'] for a, b in zip(edges, edges[1:]))


@pytest.mark.parametrize('routes', [
    [], 'route_17,route_18', ['route_17', ''], ['route_999'],
    ['home.battle'], [{'id': 'route_17', 'rect': [1, 2, 3, 4]}],
    ['route_18'], ['route_17'], ['route_17', 'route_16'],
    ['route_17', 'route_18', 'route_17', 'route_18'],
])
def test_invalid_route_selection_is_rejected_before_state_or_device_io(monkeypatch, routes):
    monkeypatch.setattr(calibrate, '_state_load', lambda *a: pytest.fail('state read before validation'))
    monkeypatch.setattr(bootstrap, 'preflight', lambda: pytest.fail('device I/O before validation'))
    with pytest.raises(ValueError):
        bootstrap.run({}, routes=routes)


@pytest.mark.parametrize('extra', [{'flows': True}, {'battle_only': True}])
def test_scoped_runner_rejects_battle_modes_before_state_access(monkeypatch, extra):
    monkeypatch.setattr(calibrate, '_state_load', lambda *a: pytest.fail('state accessed'))
    with pytest.raises(ValueError, match='cannot include'):
        bootstrap.run({}, routes=['route_17', 'route_18'], **extra)


@pytest.mark.parametrize('args', [
    ['--routes', 'route_17,route_18'],
    ['--bootstrap', '--routes', 'route_17,route_18', '--flows'],
    ['--bootstrap', '--routes', 'route_17,route_18', '--battle-only'],
    ['--bootstrap', '--routes', 'route_17,route_18', '--observe'],
    ['--bootstrap', '--routes', 'route_17,route_18', '--observe', '--observe-watch', '3'],
    ['--bootstrap', '--routes', ''],
    ['--bootstrap', '--routes', 'route_17'],
    ['--bootstrap', '--routes', 'route_17,,route_18'],
])
def test_cli_rejects_invalid_routes_before_binding_or_display(monkeypatch, args):
    monkeypatch.setattr(sys, 'argv', ['calibrate.py', *args])
    monkeypatch.setattr(operations, 'restore_worker_context', lambda *a: pytest.fail('worker bound'))
    monkeypatch.setattr(calibrate, '_prepare_display', lambda: pytest.fail('device I/O'))
    with pytest.raises(SystemExit) as exc:
        calibrate.main()
    assert exc.value.code == 2


def test_cli_forwards_selected_routes_to_bootstrap(monkeypatch, tmp_path):
    from runtime import device_lease
    monkeypatch.setattr(sys, 'argv', ['calibrate.py', '--bootstrap', '--routes', 'route_17, route_18'])
    monkeypatch.setattr(operations, 'restore_worker_context', lambda *a: None)
    monkeypatch.setattr(calibrate.settings, 'bind_device', lambda *a: None)
    monkeypatch.setattr(calibrate.settings, 'CONFIG', {})
    monkeypatch.setattr(device_lease, 'ensure_current', lambda: None)
    monkeypatch.setattr(calibrate, '_prepare_display', lambda: None)
    paths = {'state': str(tmp_path / 'state.json'), 'stop': str(tmp_path / 'stop')}
    monkeypatch.setattr(calibrate, '_paths', lambda: paths)
    calls = []
    monkeypatch.setattr(bootstrap, 'run', lambda *a, **kw: calls.append((a, kw)))
    calibrate.main()
    assert calls == [((paths, False), {'flows': False, 'battle_only': False,
                                     'routes': ['route_17', 'route_18']})]


@pytest.fixture
def scoped_scanner(monkeypatch, tmp_path):
    monkeypatch.setattr(operations, 'environment_operation', lambda: (None, 'route-operation'))
    paths = {'state': str(tmp_path / 'state.json'), 'stop': str(tmp_path / 'stop')}
    cal = calibrate.Calibration(paths, False, bootstrap=True)
    cal.entries = [{'rel': 'modules/owned.png', 'verified': False, 't': 1},
                   {'rel': 'mapping/v11/route_17.png', 'verified': False, 't': 1}]
    cal.player = {'cards': {'owned': True}}
    state = {'phases': {'bootstrap': {'status': 'needs_attention', 'kind': 'full'},
                        'modules': {'status': 'done', 'results': {'count': 4}}},
             'collections': {'cards': {'count': 31}},
             'screen_map': {'home': {'status': 'needs_mapping'}},
             'final_screen': {'name': 'home', 'verified': True, 'operation_id': 'old'}}
    saved = copy.deepcopy((cal.entries, cal.player, state))
    scanner = bootstrap.Scanner(cal, state, routes=['route_17', 'route_18'],
        grab=lambda: pytest.fail('unexpected device capture'),
        tap=lambda *a, **kw: pytest.fail('unexpected device tap'), sweep=lambda: True)
    monkeypatch.setattr(scanner, 'harvest', lambda *a: pytest.fail('full content harvest'))
    monkeypatch.setattr(scanner, '_run_flows', lambda: pytest.fail('battle flows'))
    monkeypatch.setattr(calibrate, '_merge_draft', lambda *a: pytest.fail('unrelated draft write'))
    monkeypatch.setattr(cal, 'save_report', lambda: None)
    return scanner, saved


@pytest.mark.parametrize('route_status,expected', [('done', 'done'),
                                                ('needs_attention', 'needs_attention'),
                                                ('skipped', 'needs_attention')])
def test_scoped_outcome_and_final_home_preserve_prior_discoveries(scoped_scanner, monkeypatch,
                                                                 route_status, expected):
    scanner, (entries, player, previous) = scoped_scanner
    observations = []
    monkeypatch.setattr(scanner, 'observed', lambda name: (observations.append(name) or np.zeros((1, 1, 3)), []))
    class Routes:
        def __init__(self, actual, *, scan_contents):
            assert actual is scanner and scan_contents is False
        def run(self, edges):
            assert [edge['id'] for edge in edges] == ['route_17', 'route_18']
            for edge in edges:
                scanner.begin_step(scanner.step_index(edge['id']))
                scanner.finish_step(route_status)
    monkeypatch.setattr(mapping_session, 'Session', Routes)
    scanner.run()
    stored = json.loads(Path(scanner.cal.p['state']).read_text())
    phase = stored['phases']['bootstrap']
    assert phase['kind'] == 'routes' and phase['routes'] == ['route_17', 'route_18']
    assert phase['status'] == expected
    assert [s['id'] for s in phase['steps']] == ['preflight', 'extract', 'map', 'home',
                                                'route_17', 'route_18', 'finish']
    assert observations == ['home', 'home']
    assert stored['final_screen']['operation_id'] == 'route-operation'
    assert stored['final_screen']['name'] == 'home' and stored['final_screen']['verified']
    assert stored['entries'] == entries and stored['player'] == player
    assert stored['collections'] == previous['collections']
    assert stored['screen_map'] == previous['screen_map']
    assert stored['phases']['modules'] == previous['phases']['modules']
    assert stored['scan_history'][-1]['kind'] == 'full'


def test_failed_final_home_does_not_reuse_previous_proof(scoped_scanner, monkeypatch):
    scanner, _ = scoped_scanner
    observations = []
    def observe(name):
        observations.append(name)
        if len(observations) == 2:
            raise RuntimeError('cannot verify final Home')
        return np.zeros((1, 1, 3)), []
    monkeypatch.setattr(scanner, 'observed', observe)
    class Routes:
        def __init__(self, *a, **kw): pass
        def run(self, edges): pass
    monkeypatch.setattr(mapping_session, 'Session', Routes)
    with pytest.raises(RuntimeError, match='final Home'):
        scanner.run()
    assert 'final_screen' not in scanner.state
    assert scanner.state['phases']['bootstrap']['status'] != 'done'
