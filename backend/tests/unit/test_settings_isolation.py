"""The settings singleton is handed back as it was given (the leak behind `KeyError: 'tourney_1'`).

settings.CONFIG and its two baselines (_DEFAULTS, _ACCOUNT_DEFAULTS) are process-wide, and production code rewrites them on
purpose: a successful POST /api/loadout-patch assigns CONFIG['loadouts'], a worker bootstrap replaces CONFIG with the accepted
frozen one. The contract generators drive that route for real (the Equipment cases), so before this fix the pages contract test
left the shipped `tourney_1` loadout out of the table and a LATER test (test_p6_consumers) failed, but only in an order that ran
the pages tests first. Three pieces undo it and each is pinned here separately, so reverting any one of them fails a test:

  * conftest.settings_restored - the helper (in place, exceptions included, baselines too);
  * Generator.world of test_dashboard_contract.py - each contract scenario hands the table back (asserted INSIDE the test, where
    the suite-wide fixture below cannot hide a world that does not);
  * conftest._settings_singletons_restored - every test, the net under all the others (the pair of tests at the end).
"""
import copy

import pytest

import settings
from conftest import settings_restored
from dashboard_contract_cases_editors import equipped, loadout
from test_dashboard_contract import Generator


def state():
    return copy.deepcopy((settings.CONFIG, settings._DEFAULTS, settings._ACCOUNT_DEFAULTS))


def test_the_helper_puts_back_the_table_and_both_baselines_in_place_even_when_the_block_raises():
    before, table = state(), settings.CONFIG
    with pytest.raises(RuntimeError):
        with settings_restored():
            settings.CONFIG['loadouts']['zz_isolation_probe'] = {'cards': 'x'}     # a nested change
            settings.CONFIG['active_instance'] = 'elsewhere'                        # a top-level change
            settings.CONFIG['zz_isolation_probe_key'] = 1                           # a key that was not there
            settings.CONFIG.pop('tap')                                              # a removal
            settings._DEFAULTS = {'rois': {}}                                       # the baselines are rebound, not mutated
            settings._ACCOUNT_DEFAULTS = {}
            raise RuntimeError('the block failed')
    assert state() == before
    assert settings.CONFIG is table, 'modules hold `from settings import CONFIG`: the dict must keep its identity'


def test_the_helper_leaves_an_unchanged_table_alone():
    table, inner = settings.CONFIG, settings.CONFIG['loadouts']
    with settings_restored():
        pass
    assert settings.CONFIG is table and settings.CONFIG['loadouts'] is inner, 'nothing changed, so nothing was replaced'


def test_a_contract_world_that_saves_a_loadout_hands_the_settings_table_back(dash, tmp_path, monkeypatch):
    """The Equipment family saves through the real handler, which assigns the live table. The world must undo it by itself."""
    before = copy.deepcopy(settings.CONFIG)
    generator = Generator(dash, tmp_path / 'worlds', monkeypatch)
    with generator.world('saved') as w:
        equipped(w)
        revision = w.get('probe', 'read', '/api/config').get_json()['config_revision']
        loadout(w, 'save', 'plain', {'cards': 'main_farm'}, revision, expect=200)
        assert settings.CONFIG['loadouts']['plain'] == {'cards': 'main_farm'}, 'the handler assigned the live table: this test would prove nothing otherwise'
        assert settings.CONFIG != before
    assert settings.CONFIG == before


# ------------------------------------------------ the suite-wide fixture: a pair, in definition order
PROBED = []


def test_a_test_that_rewrites_the_settings_table_is_the_first_of_a_pair():
    PROBED.append(True)
    settings.CONFIG['loadouts']['zz_isolation_probe'] = {'cards': 'x'}
    settings._DEFAULTS = {'rois': {}}
    settings._ACCOUNT_DEFAULTS = {}
    assert 'zz_isolation_probe' in settings.CONFIG['loadouts']


def test_the_next_test_gets_the_table_it_would_have_had():
    if not PROBED:
        pytest.skip('the first test of the pair did not run in this selection')
    assert 'zz_isolation_probe' not in settings.CONFIG['loadouts']
    assert settings._DEFAULTS and settings._ACCOUNT_DEFAULTS, 'the baselines were left rebound'
