"""Equipment actions require final whole-plan proof; no real I/O is permitted."""
import copy
from types import SimpleNamespace

import numpy as np
import pytest

from interactions import inventory, loadout, module_evidence, presets, tourney


@pytest.fixture
def world(monkeypatch):
    import socket
    import subprocess
    import settings
    from device import adbclient, capture

    def blocked(*args, **kwargs):
        raise AssertionError('module application test attempted real I/O')

    for module, name in ((socket, 'socket'), (subprocess, 'Popen'), (settings, 'run_hidden'),
                         (adbclient, 'shell'), (adbclient, 'exec_out')):
        monkeypatch.setattr(module, name, blocked)
    state = SimpleNamespace(version='v29', kind='grid', equipped={}, available={'space_displacer'},
                            taps=[], events=[], outcome='success', open_name=None, scans=[],
                            entered=False, callbacks=[])
    categories = {'space_displacer': 'armor', 'sharp_fortitude': 'armor', 'dimension_core': 'core'}

    def frame():
        return SimpleNamespace(kind=state.kind, equipped=dict(state.equipped))

    def require_screen(image, version=None):
        if image.kind != 'grid' or version is not None and version != state.version:
            raise tourney.Abort('unrecognized module screen')

    def settled(version=None):
        image = frame()
        require_screen(image, version)
        return image

    def find_inventory(name, version=None):
        state.scans.append(name)
        state.open_name = name
        return settled(version), (100, 1200) if name in state.available else None

    def find(image, rel, threshold=.9, **kwargs):
        if rel == 'modules/buy_module.png' and image.kind == 'grid': return ((100, 300), 1.0)
        if image.kind == 'detail':
            if rel == loadout.V29_EQUIP_BTN and state.version == 'v29': return ((300, 1500), 1.0)
            if rel == 'modules/equip_btn.png' and state.version == 'legacy': return ((300, 1500), 1.0)
        if image.kind == 'chooser' and rel in {'modules/primary_btn.png', 'modules/assist_btn.png'}:
            return ((300, 1600), 1.0)
        return None

    def wait_for(rel, *args, **kwargs):
        image = frame()
        hit = find(image, rel)
        return image, hit[0] if hit else None

    def require(rel, what, **kwargs):
        image, point = wait_for(rel)
        if point is None:
            raise tourney.Abort('required control not visible')
        return image, point

    def equip():
        name = state.open_name
        old = state.equipped.get(categories[name])
        if state.outcome != 'vanished_only':
            state.equipped[categories[name]] = name
        state.available.discard(name)
        if old:
            state.available.add(old)
        state.kind = 'grid'

    def tap(point, reason):
        state.taps.append(reason)
        if reason.startswith(('open module dialog', 'open module ')):
            state.kind = 'detail'
        elif reason.startswith('equip module'):
            if state.outcome == 'chooser': state.kind = 'chooser'
            elif state.outcome == 'foreign': state.kind = 'cards'
            else: equip()
        elif reason.startswith('equip '): state.kind = 'chooser'
        elif '->' in reason: equip()

    monkeypatch.setattr(capture, 'grab', frame)
    monkeypatch.setattr(module_evidence, 'require_screen', require_screen)
    monkeypatch.setattr(module_evidence, 'ui_version', lambda image: state.version if state.version == 'v29' else None)
    monkeypatch.setattr(module_evidence, 'settled', settled)
    monkeypatch.setattr(module_evidence, 'find_inventory', find_inventory)
    monkeypatch.setattr(module_evidence, 'verify_v29', lambda name, image: name in image.equipped.values())
    monkeypatch.setattr(tourney, 'verify_slot', lambda name, role, image=None:
                        name in (image or frame()).equipped.values())
    monkeypatch.setattr(tourney, 'open_nav', lambda *a: None)
    monkeypatch.setattr(tourney, 'return_to_game', lambda *a: frame())
    monkeypatch.setattr(tourney, 'find', find)
    monkeypatch.setattr(tourney, 'wait_for', wait_for)
    monkeypatch.setattr(tourney, 'require', require)
    monkeypatch.setattr(tourney, 'tap_at', tap)
    monkeypatch.setattr(inventory, '_panel_open', lambda image: image.kind in {'detail', 'chooser'})
    monkeypatch.setattr(tourney.time, 'sleep', lambda *_: None)
    monkeypatch.setattr(loadout.logger, 'event', lambda kind, **kw: state.events.append((kind, kw)))
    monkeypatch.setattr(loadout.logger, 'shot', lambda *a: 'synthetic-shot')
    monkeypatch.setattr(presets, 'available', lambda: False)
    state.frame = frame
    return state


def test_v29_absence_from_inventory_cannot_mean_already_equipped(world):
    world.available.clear()
    with pytest.raises(loadout.SetupAbort) as error:
        loadout.apply_modules([('space_displacer', 'primary')])
    assert world.scans == ['space_displacer']
    assert world.taps == []
    assert error.value.setup['state'] == 'degraded'
    assert error.value.setup['modules'][0]['state'] == 'unknown'


def test_positive_equipped_header_skips_inventory_and_reverifies_plan(world):
    world.equipped['armor'] = 'space_displacer'
    result = loadout.apply_modules([('space_displacer', 'assist')])
    assert result['space_displacer'] == 'already (header)'
    assert world.scans == world.taps == []
    assert result.evidence == [dict(module='space_displacer', role='category', state='verified',
                                    detail='equipped_header', reason=None)]


def test_equipped_result_requires_header_after_mutation(world):
    result = loadout.apply_modules([('space_displacer', 'assist')])
    assert result['space_displacer'] == 'equipped'
    assert world.equipped == {'armor': 'space_displacer'}
    assert result.evidence[0]['role'] == 'category'


@pytest.mark.parametrize('outcome', ['vanished_only', 'chooser', 'foreign'])
def test_disappearance_role_chooser_or_foreign_screen_is_not_success(world, outcome):
    world.outcome = outcome
    with pytest.raises(loadout.SetupAbort):
        loadout.apply_modules([('space_displacer', 'primary')])
    assert len([reason for reason in world.taps if reason.startswith('equip module')]) == 1
    assert not any('->' in reason for reason in world.taps)


def test_final_plan_detects_displacement_of_an_earlier_already_module(world):
    world.equipped['armor'] = 'space_displacer'
    world.available = {'sharp_fortitude'}
    with pytest.raises(loadout.SetupAbort) as error:
        loadout.apply_modules([('space_displacer', 'primary'), ('sharp_fortitude', 'assist')])
    evidence = error.value.setup['modules']
    assert evidence[0]['state'] == 'degraded'
    assert evidence[1]['state'] == 'verified'
    assert world.equipped == {'armor': 'sharp_fortitude'}


def test_restoration_uses_the_same_positive_whole_plan_contract(world):
    world.equipped['armor'] = 'space_displacer'
    world.available = {'sharp_fortitude'}
    restored = loadout.apply_modules([('sharp_fortitude', 'primary')])
    assert restored.evidence[0]['state'] == 'verified'
    assert world.equipped == {'armor': 'sharp_fortitude'}


def test_unknown_ui_cannot_fall_through_to_legacy_equipment_mutation(world):
    world.version = 'unknown'
    with pytest.raises(loadout.SetupAbort):
        loadout.apply_modules([('space_displacer', 'primary')])
    assert world.taps == ['open module space_displacer']


def test_legacy_unverifiable_slot_is_not_already(world, monkeypatch):
    world.version = 'legacy'
    world.available.clear()
    monkeypatch.setattr(tourney, 'verify_slot', lambda *a: None)
    with pytest.raises(tourney.Abort, match='positively equipped'):
        tourney._equip_module('space_displacer', 'primary', present=set())
    assert world.taps == []


def test_legacy_requires_positive_dialog_and_final_slot(world):
    world.version = 'legacy'
    result = loadout.apply_modules([('space_displacer', 'primary')])
    assert result.evidence[0]['role'] == 'primary'
    assert result.evidence[0]['state'] == 'verified'
    assert 'space_displacer -> primary' in world.taps


def test_apply_exposes_module_report_and_normalizes_failure(world, monkeypatch):
    monkeypatch.setattr(loadout, 'spec', lambda name: {'modules': [['space_displacer', 'assist']]})
    result = loadout.apply('required')
    assert result['setup']['state'] == 'verified'
    assert result['setup']['loadout'] == 'required'
    assert result['setup']['modules'][0]['role'] == 'category'
    world.equipped.clear()
    world.available.clear()
    with pytest.raises(loadout.SetupAbort) as error:
        loadout.apply('required')
    assert error.value.setup['loadout'] == 'required'
    assert error.value.setup['continued_with_partial'] is False


def test_metadata_only_loadout_is_unknown_as_is(world, monkeypatch):
    monkeypatch.setattr(loadout, 'spec', lambda name: {'tier': 1})
    result = loadout.apply('unchanged')
    assert result['setup']['state'] == 'unknown'
    assert result['setup']['detail'] == 'as_is'
    assert result['setup']['modules'] == []
    assert world.taps == []


def test_global_preset_report_is_selection_only(world, monkeypatch):
    monkeypatch.setattr(loadout, 'spec', lambda name: {'global_preset': 'Any user label'})
    monkeypatch.setattr(presets, 'select_global', lambda name: 'selected')
    result = loadout.apply('global')
    assert result['setup']['state'] == 'verified'
    assert result['setup']['detail'] == 'global_preset_selection'
    assert result['setup']['modules'] == []
    assert world.taps == world.scans == []


def test_setup_failure_copies_partial_evidence(world):
    report = loadout.unknown_setup('source', reason='missing slot')
    report['modules'] = [dict(module='space_displacer', state='unknown')]
    exc = loadout.SetupAbort('module missing', report)
    normalized = loadout.setup_failure('destination', exc)
    normalized['modules'][0]['state'] = 'verified'
    assert exc.setup['modules'][0]['state'] == 'unknown'
    assert normalized['loadout'] == 'destination'
    assert normalized['continued_with_partial'] is False


def test_legacy_constant_template_cannot_prove_equipment(monkeypatch):
    monkeypatch.setattr(module_evidence, 'require_screen', lambda *a: None)
    monkeypatch.setattr(module_evidence, 'ui_version', lambda frame: None)
    monkeypatch.setattr(tourney.detect, '_tpl', lambda rel: np.full((100, 100, 3), 128, np.uint8))
    frame = np.zeros((2560, 1080, 3), np.uint8)
    assert tourney.verify_slot('amplifying_strike', 'primary', frame) is None


def test_legacy_dedicated_equipped_crop_does_not_need_grid_template(monkeypatch):
    monkeypatch.setattr(module_evidence, 'require_screen', lambda *a: None)
    monkeypatch.setattr(module_evidence, 'ui_version', lambda frame: None)
    template = np.random.default_rng(73).integers(0, 240, (80, 80, 3), dtype=np.uint8)
    def tpl(rel):
        if rel == 'modules/equipped/amplifying_strike.png': return template
        raise tourney.detect.TemplateMissing(rel)
    monkeypatch.setattr(tourney.detect, '_tpl', tpl)
    frame = np.zeros((2560, 1080, 3), np.uint8)
    cx, cy, _ = tourney.HEADER_SLOTS[('cannon', 'primary')]
    frame[cy-40:cy+40, cx-40:cx+40] = template
    assert tourney.verify_slot('amplifying_strike', 'primary', frame) is True
    assert tourney.verify_slot('amplifying_strike', 'assist', frame) is False


@pytest.mark.parametrize('final_ok', [False, True])
def test_tournament_setup_publishes_final_gate_before_ticket(world, monkeypatch, final_ok):
    order = []
    monkeypatch.setattr(tourney, 'in_tournament', lambda frame: False)
    monkeypatch.setattr(tourney, 'open_tournament', world.frame)
    monkeypatch.setattr(tourney, 'read_conditions', lambda frame: None)
    monkeypatch.setattr(tourney, 'card_tweaks', lambda: None)
    monkeypatch.setattr(tourney, 'verify_loadout', lambda name: [] if final_ok else ['module NOT VERIFIABLE'])
    monkeypatch.setattr(tourney, 'start_battle', lambda: order.append('ticket'))
    monkeypatch.setattr(loadout, 'spec', lambda name: {})
    def callback(report):
        order.append(report['state'])
    if final_ok:
        assert tourney.setup(on_setup=callback) is True
        assert order == ['unknown', 'ticket']  # empty loadout has no setup proof
    else:
        with pytest.raises(tourney.Abort):
            tourney.setup(on_setup=callback)
        assert order == ['degraded']


@pytest.mark.parametrize('entry_refused', [False, True])
def test_optional_tournament_setup_continuation_is_persisted_only_after_entry(world, monkeypatch, tmp_path, entry_refused):
    from control.battles import public
    from runtime.battle_session import BattleSession
    from towerpilot_workflows.battles import Battles
    from towerpilot_workflows.operations import Operations
    path = tmp_path / 'operations.db'
    operations = Operations(path)
    context = dict(device_key='synthetic-tournament', instance='main', account=None, serial='fake')
    operation, _ = operations.accept('run', context, {})
    session = BattleSession(Battles(path), operation['id'], {'kind': 'tournament', 'tier': 1}, {})
    frozen = copy.deepcopy(session.battle['envelope'])
    monkeypatch.setattr(tourney, 'in_tournament', lambda frame: False)
    monkeypatch.setattr(tourney, 'open_tournament', world.frame)
    monkeypatch.setattr(tourney, 'read_conditions', lambda frame: None)
    monkeypatch.setattr(tourney, 'card_tweaks', lambda: None)
    monkeypatch.setattr(tourney, 'verify_loadout', lambda name: [])
    monkeypatch.setattr(loadout, 'spec', lambda name: {'bot_preset': 'optional'})
    def select(category, preset):
        assert (category, preset) == ('bots', 'optional')
        raise tourney.Abort('Optional bot preset unavailable')
    monkeypatch.setattr(presets, 'select_category', select)
    reports, entries = [], []
    def callback(report):
        session.set_setup(report)
        reports.append(copy.deepcopy(report))
    def enter():
        report = session.store.get(session.battle['id'])['runtime']['setup']
        assert report['state'] == 'degraded'
        assert report['reason'] == 'optional_bot_preset_failed'
        assert report['continued_with_partial'] is False
        entries.append('ticket')
        if entry_refused:
            raise tourney.Abort('Tournament entry refused')
    monkeypatch.setattr(tourney, 'start_battle', enter)
    if entry_refused:
        with pytest.raises(tourney.Abort, match='Tournament entry refused'):
            tourney.setup(on_setup=callback)
    else:
        assert tourney.setup(on_setup=callback) is True
    stored = session.store.get(session.battle['id'])
    report = public(session.store, stored)['runtime']['setup']
    assert entries == ['ticket']
    assert len(reports) == 2 and reports[-1] == report
    assert report['continued_with_partial'] is not entry_refused
    assert all(item['continued_with_partial'] is False for item in reports[:-1])
    assert report['reason'] == ('Tournament entry refused' if entry_refused else 'optional_bot_preset_failed')
    assert stored['envelope'] == frozen
    setup_events = [event['status']['setup'] for event in session.store.history(stored['id'])
                    if event['kind'] == 'status' and 'setup' in event['status']]
    assert setup_events[-1] == report


@pytest.mark.parametrize('route,outcome,proof', [
    ('normal', 'swallowed', None), ('normal', 'dry', None),
    ('post_ticket', 'swallowed', None), ('post_ticket', 'dry', None),
    ('normal', 'delayed', 'screen'), ('normal', 'delayed', 'wave'),
    ('post_ticket', 'delayed', 'badge'), ('auto_ticket', 'observed', 'badge'),
    ('auto_ticket', 'observed', 'screen'),
])
def test_real_tournament_entry_requires_observation_before_partial_continuation(
        world, monkeypatch, tmp_path, route, outcome, proof):
    from control.battles import public
    from runtime.battle_session import BattleSession
    from towerpilot_workflows.battles import Battles
    from towerpilot_workflows.operations import Operations
    path = tmp_path / 'entry.db'
    context = dict(device_key='synthetic-observed-entry', instance='main', account=None, serial='fake')
    operation, _ = Operations(path).accept('run', context, {})
    session = BattleSession(Battles(path), operation['id'], {'kind': 'tournament', 'tier': 1}, {})
    def image(kind, *, wave=None, badge=False):
        return SimpleNamespace(kind=kind, wave=wave, badge=badge)
    tournament = image('tournament')
    positive = image('battle' if proof == 'screen' else 'unknown',
                     wave=12 if proof == 'wave' else None, badge=proof == 'badge')
    frames = [image('home')]
    if route in ('post_ticket', 'auto_ticket'):
        frames.append(image('buy_ticket'))
        frames.extend([image('battle'), positive] if route == 'auto_ticket' else [tournament, tournament])
    if route != 'auto_ticket':
        frames.append(tournament)
        if outcome == 'delayed':
            frames.extend([image('unknown'), positive])
    remaining = iter(frames)
    last = frames[-1]
    observed = []
    def grab():
        nonlocal last
        last = next(remaining, last)
        observed.append(last)
        return last
    monkeypatch.setattr(tourney.capture, 'grab', grab)
    monkeypatch.setattr(tourney, 'CONFIG', dict(loop={'dry_run': outcome == 'dry'}, preset='synthetic',
                        presets={'synthetic': {'gem_entry_max': 10}}))
    monkeypatch.setattr(tourney, 'open_tournament', lambda: tournament)
    monkeypatch.setattr(tourney, 'read_conditions', lambda frame: None)
    monkeypatch.setattr(tourney, 'card_tweaks', lambda: None)
    monkeypatch.setattr(tourney, 'verify_loadout', lambda name: [])
    monkeypatch.setattr(tourney, 'in_tournament', lambda frame: frame.badge)
    monkeypatch.setattr(tourney.wave_reader, 'read_wave', lambda frame: frame.wave)
    monkeypatch.setattr(tourney.screen, 'identify', lambda frame: SimpleNamespace(name=frame.kind))
    monkeypatch.setattr(tourney, '_in_battle', lambda frame: pytest.fail('Stats/dialog-inclusive evidence used'))
    monkeypatch.setattr(loadout, 'spec', lambda name: {'bot_preset': 'optional'})
    def select(category, preset):
        raise tourney.Abort('Optional bot preset unavailable')
    monkeypatch.setattr(presets, 'select_category', select)
    def find(frame, rel, *args, **kwargs):
        if frame.kind == 'tournament' and rel in tourney.BATTLE_BUTTONS:
            return ((500, 1800), 1.0)
        if frame.kind == 'buy_ticket' and rel == tourney.BUY_TICKET_VIDEO and route == 'post_ticket':
            return ((300, 1500), 1.0)
        if frame.kind == 'buy_ticket' and rel == tourney.BUY_TICKET_CANCEL:
            return ((300, 1500), 1.0)
        return None
    monkeypatch.setattr(tourney, 'find', find)
    monkeypatch.setattr(tourney, 'read_gem_price', lambda frame: 10)
    inputs, shots, reports = [], [], []
    def tap(point, reason):
        inputs.append(reason)
        assert session.store.get(session.battle['id'])['runtime']['setup']['continued_with_partial'] is False
    monkeypatch.setattr(tourney, 'tap_at', tap)
    monkeypatch.setattr(tourney.logger, 'shot', lambda frame, tag: shots.append(tag))
    def callback(report):
        session.set_setup(report)
        reports.append(copy.deepcopy(report))
    success = proof is not None
    if success:
        assert tourney.setup(loadout_name='synthetic_optional', on_setup=callback) is True
    else:
        with pytest.raises(tourney.Abort, match='battle entry was not observed'):
            tourney.setup(loadout_name='synthetic_optional', on_setup=callback)
    battle = session.store.get(session.battle['id'])
    report = public(session.store, battle)['runtime']['setup']
    assert report['state'] == 'degraded'
    assert report['continued_with_partial'] is success
    assert all(not item['continued_with_partial'] for item in reports[:-1])
    battle_inputs = [reason for reason in inputs if reason.startswith('tournament BATTLE')]
    assert battle_inputs == (['tournament BATTLE', 'tournament BATTLE (after ticket)']
                             if route == 'post_ticket' else ['tournament BATTLE'])
    assert not any('cancel' in reason or 'end' in reason or 'recover' in reason for reason in inputs)
    assert shots.count('tourney_started') == int(success)
    assert shots.count('tourney_entry_unconfirmed') == int(not success)
    if route in ('post_ticket', 'auto_ticket'):
        assert len([reason for reason in inputs if reason.startswith('buy ticket:')]) == 1
    if not success:
        assert len(observed) < 30  # observation-only timeout is bounded
        assert report['detail'] == 'setup_refused'
        assert any(kind == 'tourney_battle_unconfirmed' for kind, fields in world.events)
