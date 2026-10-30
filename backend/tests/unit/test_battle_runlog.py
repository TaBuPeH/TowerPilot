"""Clipboard evidence must be fresh, structured and bound to known entry."""
import pytest

from runtime.runlog import clipboard_provenance


@pytest.fixture
def battle():
    return dict(id='battle', envelope_hash='frozen', start_certainty='observed_entry',
                envelope={'preset': {'tier': 1}}, runtime={'observed_wave': 100})


def test_unchanged_clipboard_never_verifies_a_result(battle):
    text = 'Tier\t1\nWave\t101\nCoins earned\t42\n'
    evidence = clipboard_provenance(text, text, 8, 8, battle)
    assert evidence['fresh'] is False
    assert evidence['verified'] is False
    assert evidence['status'] == 'captured_unverified'


def test_clipboard_sequence_can_establish_recopy_without_erasing_clipboard(battle):
    text = 'Tier\t1\nWave\t101\nCoins earned\t42\n'
    evidence = clipboard_provenance(text, text, 8, 9, battle)
    assert evidence['fresh'] is True
    assert evidence['verified'] is True
    assert evidence['battle_id'] == 'battle'
    assert evidence['envelope_hash'] == 'frozen'


@pytest.mark.parametrize('text', ['hello', 'Tier\t1\nWave\t99\nCoins earned\t42',
                                  'Tier\t2\nWave\t101\nCoins earned\t42',
                                  'Wave\t101\nCoins earned\t42'])
def test_wrong_or_unidentified_content_remains_unverified(battle, text):
    evidence = clipboard_provenance('baseline', text, 1, 2, battle)
    assert evidence['verified'] is False


def test_adoption_cannot_verify_tier_from_configured_tier(battle):
    battle['start_certainty'] = 'partial_observation'
    evidence = clipboard_provenance('', 'Tier\t1\nWave\t101\nCoins earned\t42', 1, 2, battle)
    assert evidence['verified'] is False


def test_logger_attaches_battle_context_without_mutating_caller(tmp_path, monkeypatch):
    import json
    from runtime import logger
    monkeypatch.setattr(logger, '_battle_context', {})
    monkeypatch.setattr(logger, 'run_file', lambda: tmp_path/'events.jsonl')
    context = {'battle_id': 'battle', 'driver_segment': 'driver', 'envelope_hash': 'frozen'}
    logger.set_battle_context(context)
    context['battle_id'] = 'changed'
    logger.event('purchase', confirmed=False)
    event = json.loads((tmp_path/'events.jsonl').read_text())
    assert event['battle_id'] == 'battle'
    assert event['confirmed'] is False
