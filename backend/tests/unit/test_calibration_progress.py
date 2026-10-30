"""Basic setup progress survives polling/reload without reading process logs."""
import json
from pathlib import Path

import pytest

from player import calibrate
from runtime import operations


@pytest.fixture
def recording(tmp_path, monkeypatch):
    monkeypatch.setenv('TOWER_OPERATION_ID', 'current-operation')
    monkeypatch.setattr(operations, 'publish_progress', lambda state: None)
    paths = {'state': str(tmp_path / 'calibrate_state.json')}
    state = {'operation_id': 'current-operation', 'phases': {}}
    cal = calibrate.Calibration(paths, False,
        progress=lambda phase, message: calibrate._record_activity(paths, state, phase, message))
    return cal, paths, state


@pytest.mark.parametrize('status', ['running', 'done', 'stopped', 'error'])
def test_activity_is_durable_operation_bound_and_cannot_change_phase_outcome(recording, status):
    cal, paths, state = recording
    state['phases']['cards'] = {'status': status, 'started_at': 10}
    cal.progress('cards', 'Cards: reading page 3; 12 cards identified')
    saved = json.loads(Path(paths['state']).read_text(encoding='utf-8'))
    assert saved['activity'] == state['activity']
    assert saved['activity']['operation_id'] == 'current-operation'
    assert saved['activity']['phase'] == 'cards'
    assert saved['activity']['message'] == 'Cards: reading page 3; 12 cards identified'
    assert saved['phases']['cards']['updated_at'] == saved['activity']['t']
    assert saved['phases']['cards']['status'] == status
    assert saved['phases']['cards']['started_at'] == 10


def test_cards_callback_persists_the_actual_inventory_message(recording, monkeypatch):
    from device import capture
    from interactions import tourney
    from player import card_inventory
    cal, paths, state = recording
    state['phases']['cards'] = {'status': 'running'}
    monkeypatch.setattr(tourney, 'open_nav', lambda *args: None)
    monkeypatch.setattr(tourney, 'return_to_game', lambda *args: None)
    monkeypatch.setattr(capture, 'grab', lambda: object())
    monkeypatch.setattr(calibrate.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(calibrate, '_evidence', lambda *args: None)
    monkeypatch.setattr(calibrate, 'harvest_row', lambda *args: [])
    def inventory(calibration, *, check_stop, progress):
        progress('Cards: page 2 read; 8 cards identified; scrolling down')
        return {'complete': True}
    monkeypatch.setattr(card_inventory, 'scan', inventory)
    result = calibrate.phase_cards(cal)
    saved = json.loads(Path(paths['state']).read_text(encoding='utf-8'))
    assert result['inventory']['complete'] is True
    assert saved['activity']['message'] == 'Cards: page 2 read; 8 cards identified; scrolling down'
    assert saved['phases']['cards']['status'] == 'running'


def test_modules_callback_persists_inventory_progress_before_failure(recording, monkeypatch):
    from interactions import tourney
    cal, paths, state = recording
    state['phases']['modules'] = {'status': 'running'}
    monkeypatch.setattr(tourney, 'open_nav', lambda *args: None)
    monkeypatch.setattr(tourney, 'return_to_game', lambda *args: pytest.fail('Failed scan cannot report a return'))
    def contents(calibration, progress):
        progress('Modules: identifying tile 5 on page 2')
        raise calibrate.Stopped('Operator requested stop')
    monkeypatch.setattr(calibrate, 'read_module_contents', contents)
    with pytest.raises(calibrate.Stopped, match='Operator requested stop'):
        calibrate.phase_modules(cal)
    saved = json.loads(Path(paths['state']).read_text(encoding='utf-8'))
    assert saved['activity']['message'] == 'Modules: identifying tile 5 on page 2'
    assert saved['phases']['modules']['status'] != 'done'
