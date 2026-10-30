"""Offline setup-run ownership and final-screen evidence regressions."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

import settings
from flows import shard
from interactions.tourney import Abort
from player import bootstrap, calibrate, flow_capture as fc
from runtime import logger, operations


SCREENS = ('home', 'battle', 'menu', 'surrender', 'confirm', 'stats', 'unknown', 'tournament')
TEXT = {'menu': ['EXIT BATTLE'], 'surrender': ['EXIT BATTLE', 'Surrender', 'Go Home'],
        'confirm': ['END ROUND', 'Are you sure you want to end the round?'],
        'stats': ['GAME STATS'], 'home': ['BATTLE', 'Tier 1']}


@pytest.fixture
def setup_flow(tmp_path, monkeypatch):
    # All device, OCR, template and state boundaries stay in this fixture.
    frames = {name: np.broadcast_to(np.array([index, 0, 0], np.uint8), (2560, 1080, 3))
              for index, name in enumerate(SCREENS)}
    name_of = lambda frame: SCREENS[int(frame[0, 0, 0])]
    p = {'state': str(tmp_path / 'state.json')}
    cal = SimpleNamespace(p=p, player={}, entries=[])
    sc = SimpleNamespace(cal=cal, state={}, skipped=[], screen='home', inputs=[], clock=0.,
                         stop_error=None, fail_action=None, fail_start=False, events=[])

    def check_stop():
        if sc.stop_error is not None:
            raise sc.stop_error

    def tap(x, y, *, reason, **kwargs):
        sc.inputs.append(reason)
        if sc.fail_action and sc.fail_action in reason:
            raise Abort('unexpected screen during input')
        if 'start ' in reason:
            sc.screen = 'unknown' if sc.fail_start else 'battle'
        elif 'open side menu' in reason:
            sc.screen = 'menu'
        elif 'tap EXIT BATTLE' in reason:
            sc.screen = 'surrender'
        elif 'confirm exit' in reason:
            sc.screen = 'confirm'
        elif 'confirm END ROUND' in reason:
            sc.screen = 'stats'
        elif 'game stats: HOME' in reason:
            sc.screen = 'home'

    sc.check_stop = check_stop
    sc.grab = lambda **kwargs: frames[sc.screen]
    sc.tap = tap
    sc.pause = lambda seconds: setattr(sc, 'clock', sc.clock + seconds)
    sc.progress = lambda *a, **k: None
    monkeypatch.setattr(operations, 'environment_operation', lambda: (None, 'setup-op'))
    monkeypatch.setattr(fc, 'time', SimpleNamespace(time=lambda: sc.clock,
                                                   monotonic=lambda: sc.clock))
    monkeypatch.setattr(fc, '_read', lambda f, scale=2., **kwargs: [(100, 100, t) for t in TEXT.get(name_of(f), [])])
    monkeypatch.setattr(fc, '_sprint_glyph', lambda f: (None, None))
    monkeypatch.setattr(fc, '_row_buttons', lambda f, *a, **k: ((200, 1410, 300, 70), (580, 1410, 300, 70)))
    monkeypatch.setattr(fc, '_button_crop', lambda f, box: np.ones((70, 300, 3), np.uint8))
    monkeypatch.setattr(fc, '_have_templates', lambda rels: set(rels))
    monkeypatch.setattr(logger, 'shot', lambda *a: None)
    flow = fc._Flow(sc)
    flow.on_home = lambda f: name_of(f) == 'home'
    flow.wave = lambda f: 1 if name_of(f) in ('battle', 'menu') else None
    flow.side_menu = lambda f: name_of(f) == 'menu'
    flow.game_stats = lambda f: name_of(f) == 'stats'
    flow.in_tournament = lambda f: name_of(f) == 'tournament'
    flow.locate = lambda *a, **k: (100, 100)
    flow.capture = lambda *a, **k: {'verified': True}
    flow.cut_crop = lambda *a, **k: {'verified': True}
    return flow


def stored(flow):
    return json.loads(open(flow.cal.p['state'], encoding='utf-8').read())


def test_owned_run_surrenders_through_both_confirms_and_persists_fresh_home(setup_flow):
    flow = setup_flow
    flow.s.state['final_screen'] = {'name': 'home', 'verified': True, 'operation_id': 'old-op'}
    fc._start_setup_run(flow, 'start lifecycle test')
    battle_id = stored(flow)['setup_battle']['id']
    assert 'final_screen' not in stored(flow)
    assert stored(flow)['setup_battle']['status'] == 'active'

    assert fc.end_run_and_capture(flow) is True

    final = stored(flow)
    assert final['setup_battle']['id'] == battle_id
    assert final['setup_battle']['status'] == 'finished'
    assert final['setup_battle']['operation_id'] == 'setup-op'
    assert final['setup_battle']['finished_at'] >= final['setup_battle']['started_at']
    assert final['final_screen']['name'] == 'home'
    assert final['final_screen']['verified'] is True
    assert final['final_screen']['operation_id'] == 'setup-op'
    assert flow.s.inputs == ['flow: start lifecycle test', 'flow: open side menu',
                             'flow: tap EXIT BATTLE', 'flow: confirm exit (surrender)',
                             'flow: confirm END ROUND (yes)', 'flow: game stats: HOME']
    events = [json.loads(line) for line in logger.run_file().read_text().splitlines()]
    attempts = [row for row in events if row.get('action') in ('setup.battle.start', 'setup.battle.end')]
    starts = {row['attempt_id']: row for row in attempts if row['kind'] == 'attempt_start'}
    ends = {row['attempt_id']: row for row in attempts if row['kind'] == 'attempt_end'}
    assert len(starts) == len(ends) == 2 and starts.keys() == ends.keys()
    assert all(row['outcome'] == 'verified' and row['setup_battle_id'] == battle_id for row in ends.values())


@pytest.mark.parametrize('screen', ['battle', 'menu', 'stats', 'unknown', 'tournament'])
def test_preexisting_screen_cannot_be_started_over_or_surrendered(setup_flow, screen):
    flow = setup_flow
    flow.s.screen = screen
    with pytest.raises(RuntimeError, match='No setup battle started'):
        fc._start_setup_run(flow, 'start refused test')
    assert fc.end_run_and_capture(flow) is False
    assert flow.s.inputs == []
    assert stored(flow)['final_screen']['verified'] is False


@pytest.mark.parametrize('screen', ['unknown', 'tournament'])
def test_owned_battle_loses_input_authority_on_unknown_or_tournament(setup_flow, screen):
    flow = setup_flow
    fc._start_setup_run(flow, 'start owned test')
    flow.s.inputs.clear()
    flow.s.screen = screen
    assert fc.end_run_and_capture(flow) is False
    assert flow.s.inputs == []
    assert stored(flow)['setup_battle']['status'] == 'unknown'
    assert stored(flow)['final_screen']['verified'] is False


def test_persisted_record_cannot_grant_ownership_to_a_new_adapter(setup_flow):
    flow = setup_flow
    fc._start_setup_run(flow, 'start original test')
    flow.s.inputs.clear()
    replacement = fc._Flow(flow.s)
    assert fc.end_run_and_capture(replacement) is False
    assert flow.s.inputs == []


def test_another_operation_cannot_use_the_owned_record(setup_flow):
    flow = setup_flow
    fc._start_setup_run(flow, 'start original test')
    flow.operation_id = 'different-op'
    flow.s.inputs.clear()
    assert fc.end_run_and_capture(flow) is False
    assert flow.s.inputs == []


def test_unverified_start_cannot_be_cleaned_up_or_retried(setup_flow):
    flow = setup_flow
    flow.s.fail_start = True
    with pytest.raises(RuntimeError, match='start was not verified'):
        fc._start_setup_run(flow, 'start failed test')
    assert len(flow.s.inputs) == 1
    assert stored(flow)['setup_battle']['status'] == 'unknown'
    assert fc.end_run_and_capture(flow) is False
    flow.s.screen = 'home'
    with pytest.raises(RuntimeError, match='no verified finish'):
        fc._start_setup_run(flow, 'start forbidden retry')
    assert len(flow.s.inputs) == 1


@pytest.mark.parametrize('error', [calibrate.Stopped('Stop requested'), Abort('Unexpected dialog')])
def test_interrupting_owned_run_records_unknown_and_sends_no_cleanup(setup_flow, error):
    flow = setup_flow
    fc._start_setup_run(flow, 'start stopped test')
    flow.s.inputs.clear()
    flow.s.stop_error = error
    with pytest.raises(type(error), match=str(error)):
        fc.end_run_and_capture(flow)
    assert flow.s.inputs == []
    assert stored(flow)['setup_battle']['status'] == 'unknown'
    assert stored(flow)['final_screen']['verified'] is False


def test_abort_during_surrender_does_not_send_the_next_confirm(setup_flow):
    flow = setup_flow
    fc._start_setup_run(flow, 'start abort test')
    flow.s.fail_action = 'confirm exit'
    with pytest.raises(Abort):
        fc.end_run_and_capture(flow)
    assert flow.s.inputs[-1] == 'flow: confirm exit (surrender)'
    assert not any('confirm END ROUND' in reason for reason in flow.s.inputs)
    assert stored(flow)['setup_battle']['status'] == 'unknown'
    ends = [json.loads(line) for line in logger.run_file().read_text().splitlines()]
    assert [row['outcome'] for row in ends if row.get('action') == 'setup.battle.end'
            and row['kind'] == 'attempt_end'] == ['error']


def test_unknown_screen_after_stats_home_is_never_tapped_again(setup_flow):
    flow = setup_flow
    fc._start_setup_run(flow, 'start HOME timeout test')
    flow.s.screen = 'stats'
    tap = flow.s.tap
    def unknown_after_home(*args, **kwargs):
        tap(*args, **kwargs)
        if 'game stats: HOME' in kwargs['reason']:
            flow.s.screen = 'unknown'
    flow.s.tap = unknown_after_home
    flow.s.inputs.clear()
    assert fc.capture_game_stats(flow) is False
    assert flow.s.inputs == ['flow: game stats: HOME']
    assert stored(flow)['setup_battle']['status'] == 'unknown'
    assert stored(flow)['final_screen']['verified'] is False


@pytest.mark.parametrize('pass_name', ['battle', 'digits'])
@pytest.mark.parametrize('error', [calibrate.Stopped('Stop during sprint'), Abort('Unexpected sprint')])
def test_interrupted_capture_never_surrenders_as_cleanup(setup_flow, tmp_path, monkeypatch, pass_name, error):
    flow = setup_flow
    monkeypatch.setattr(settings, 'template_path', lambda rel: tmp_path / rel)
    monkeypatch.setitem(settings.CONFIG, 'presets', {})
    monkeypatch.setitem(settings.CONFIG, 'active_profile', None)
    monkeypatch.setattr(fc, 'capture_intro_sprint', lambda f: (_ for _ in ()).throw(error))
    with pytest.raises(type(error)):
        if pass_name == 'battle':
            fc._battle_pass(flow, None, 'interrupted capture')
        else:
            fc._hud_digit_pass(flow)
    assert len(flow.s.inputs) == 1 and 'start ' in flow.s.inputs[0]
    assert stored(flow)['setup_battle']['status'] == 'unknown'
    assert stored(flow)['final_screen']['verified'] is False


def test_home_proof_must_still_be_fresh_at_completion(setup_flow):
    flow = setup_flow
    fc._start_setup_run(flow, 'start stale proof test')
    flow.s.state['final_screen'] = {'name': 'home', 'verified': True}
    with pytest.raises(RuntimeError, match='final Home is unverified'):
        fc._complete_setup_battle(flow)
    assert stored(flow)['setup_battle']['status'] == 'unknown'


def test_failed_final_screen_readback_cannot_finish_owned_battle(setup_flow, monkeypatch):
    flow = setup_flow
    fc._start_setup_run(flow, 'start readback test')
    flow.s.screen = 'home'
    original_save = calibrate._state_save
    def corrupt(p, state):
        original_save(p, {**state, 'final_screen': {'name': 'unknown', 'verified': False}})
    monkeypatch.setattr(calibrate, '_state_save', corrupt)
    with pytest.raises(RuntimeError, match='could not be read back'):
        fc._complete_setup_battle(flow)
    assert stored(flow)['setup_battle']['status'] == 'unknown'
    assert 'finished_at' not in stored(flow)['setup_battle']


def test_safe_home_never_navigates_off_stats(setup_flow):
    flow = setup_flow
    flow.s.screen = 'stats'
    with pytest.raises(RuntimeError, match='no automatic waiting or further taps'):
        fc._safe_home(flow)
    assert flow.s.inputs == []


def test_tier_restoration_never_runs_after_abort(setup_flow, monkeypatch):
    flow = setup_flow
    monkeypatch.setattr(fc, 'read_tier', lambda f: 1)
    monkeypatch.setattr(fc, '_battle_pass', lambda *a: (_ for _ in ()).throw(Abort('unexpected screen')))
    monkeypatch.setattr(fc, 'set_tier', lambda *a: pytest.fail('tier restoration after Abort'))
    with pytest.raises(Abort):
        fc.capture_battle_flow(flow)
    assert flow.s.inputs == []
    assert stored(flow)['final_screen']['verified'] is False


def test_unobserved_intro_sprint_is_an_advisory_gap_with_no_input(setup_flow, monkeypatch):
    flow = setup_flow
    fc._start_setup_run(flow, 'start sprint absence test')
    flow.s.inputs.clear()
    monkeypatch.setattr(fc, '_have_templates', lambda rels: set())

    fc.capture_intro_sprint(flow)

    assert flow.s.inputs == []
    assert 10 <= flow.s.clock <= 10.5
    assert len(flow.s.skipped) == 1
    assert flow.s.skipped[0]['target'] == 'home/intro_sprint_dialog.png'
    assert flow.s.skipped[0]['optional'] is True
    assert 'not observed' in flow.s.skipped[0]['reason']


@pytest.mark.parametrize('reached, observed', [(None, 14), (1, 14), (14, 1), (14, None)])
def test_tier_restoration_requires_selection_and_fresh_readback(setup_flow, monkeypatch, reached, observed):
    flow = setup_flow
    flow.cal.player['max_tier_verified_by'] = 'setup'
    monkeypatch.setattr(fc, '_hud_digit_pass', lambda f: None)
    tiers = iter([14, 1, observed])
    monkeypatch.setattr(fc, 'read_tier', lambda f: next(tiers))
    selections = []
    monkeypatch.setattr(fc, 'set_tier', lambda f, target: selections.append(target) or reached)

    with pytest.raises(RuntimeError, match='restore.*difficulty'):
        fc.capture_battle_flow(flow)

    assert selections == [14]
    assert flow.s.inputs == []
    assert stored(flow)['final_screen']['verified'] is False
    rows = [json.loads(line) for line in logger.run_file().read_text().splitlines()]
    assert not any(row['kind'] == 'flow_tier_restored' for row in rows)


def test_tier_restoration_records_success_only_after_fresh_home_readback(setup_flow, monkeypatch):
    flow = setup_flow
    flow.cal.player['max_tier_verified_by'] = 'setup'
    monkeypatch.setattr(fc, '_hud_digit_pass', lambda f: None)
    tiers = iter([14, 1, 14])
    monkeypatch.setattr(fc, 'read_tier', lambda f: next(tiers))
    monkeypatch.setattr(fc, 'set_tier', lambda f, target: target)

    fc.capture_battle_flow(flow)

    assert stored(flow)['final_screen']['verified'] is True
    rows = [json.loads(line) for line in logger.run_file().read_text().splitlines()]
    assert [row['tier'] for row in rows if row['kind'] == 'flow_tier_restored'] == [14]


def test_lottery_exit_failure_never_logs_done(setup_flow, monkeypatch):
    flow = setup_flow
    remaining = [('uw/example.png', 'Example', (10, 10))]
    monkeypatch.setattr(fc, '_uw_remaining', lambda: list(remaining))
    monkeypatch.setattr(fc, 'set_tier', lambda *a: 1)
    monkeypatch.setattr(fc, '_start_lottery_run', lambda f: (fc._start_setup_run(f, 'start lottery test') is not None))
    monkeypatch.setattr(fc, '_sweep_uw_for_targets', lambda f: remaining.clear())
    monkeypatch.setattr(fc, 'end_run_and_capture', lambda f: False)
    with pytest.raises(RuntimeError, match='did not return Home'):
        fc._uw_lottery_pass(flow)
    assert stored(flow)['setup_battle']['status'] == 'unknown'
    rows = [json.loads(line) for line in logger.run_file().read_text().splitlines()]
    assert not [row for row in rows if row['kind'] == 'flow_uw_lottery' and row.get('result') == 'done']


@pytest.mark.parametrize('pass_name', ['battle', 'digits', 'lottery'])
def test_each_setup_battle_path_owns_then_finishes_its_run(setup_flow, tmp_path, monkeypatch, pass_name):
    flow = setup_flow
    monkeypatch.setattr(shard, 'wait_for_wave', lambda *a: None)
    monkeypatch.setattr(shard, 'ensure_max_speed', lambda: None)
    monkeypatch.setattr(settings, 'template_path', lambda rel: tmp_path / rel)
    monkeypatch.setitem(settings.CONFIG, 'presets', {})
    monkeypatch.setitem(settings.CONFIG, 'active_profile', None)
    monkeypatch.setattr(fc, 'capture_hud_digits', lambda f: [])
    monkeypatch.setattr(fc, 'capture_upgrade_labels', lambda f: None)
    monkeypatch.setattr(fc, 'capture_uw_weapons', lambda f: None)
    if pass_name == 'battle':
        fc._battle_pass(flow, None, 'test capture')
    elif pass_name == 'digits':
        fc._hud_digit_pass(flow)
    else:
        assert fc._start_lottery_run(flow)
        assert stored(flow)['setup_battle']['status'] == 'active'
        fc._finish_setup_run(flow)
    final = stored(flow)
    assert final['setup_battle']['status'] == 'finished'
    assert final['setup_battle']['operation_id'] == 'setup-op'
    assert final['final_screen']['name'] == 'home' and final['final_screen']['verified'] is True


def test_lottery_reroll_finishes_the_previous_id_before_starting_the_next(setup_flow, monkeypatch):
    flow = setup_flow
    monkeypatch.setattr(shard, 'wait_for_wave', lambda *a: None)
    monkeypatch.setattr(shard, 'ensure_max_speed', lambda: None)
    fc._start_lottery_run(flow)
    previous = stored(flow)['setup_battle']['id']
    assert fc._reroll_lottery_run(flow)
    current = stored(flow)['setup_battle']
    assert current['id'] != previous and current['status'] == 'active'
    assert 'final_screen' not in stored(flow)
    rows = [json.loads(line) for line in logger.run_file().read_text().splitlines()]
    assert any(row['kind'] == 'setup_battle' and row['id'] == previous
               and row['status'] == 'finished' for row in rows)
    assert fc._finish_setup_run(flow)


@pytest.mark.parametrize('error', [calibrate.Stopped('Stop requested'), Abort('Unexpected menu')])
def test_scanner_never_begins_battle_or_recovers_after_menu_interruption(setup_flow, monkeypatch, error):
    flow = setup_flow
    cal = calibrate.Calibration(flow.cal.p, False, bootstrap=True)
    scanner = bootstrap.Scanner(cal, {}, flows=True, grab=flow.s.grab, tap=flow.s.tap,
                                 read=lambda f: [], pause=lambda _: None)
    scanner.check_stop = lambda: None
    monkeypatch.setattr(fc, 'capture_menu_extras', lambda f: (_ for _ in ()).throw(error))
    monkeypatch.setattr(fc, '_safe_home', lambda f: pytest.fail('unexpected recovery'))
    monkeypatch.setattr(fc, 'capture_battle_flow', lambda f, **k: pytest.fail('battle started after interruption'))
    with pytest.raises(type(error)):
        scanner._run_flows()
    assert flow.s.inputs == []
    assert json.loads(open(flow.cal.p['state'], encoding='utf-8').read())['final_screen']['verified'] is False


def test_operationless_flow_never_starts_a_setup_battle(setup_flow, monkeypatch):
    """A setup battle outside an accepted operation would never be sealed in the
    ledger, so the setup guard could not see it if it were interrupted."""
    monkeypatch.setattr(operations, 'environment_operation', lambda: (None, None))
    flow = fc._Flow(setup_flow.s)
    reads = []
    monkeypatch.setattr(flow.s, 'grab', lambda **kwargs: reads.append('frame'))
    with pytest.raises(RuntimeError, match='dashboard-accepted operation'):
        fc._start_setup_run(flow, 'start lifecycle test')
    assert reads == [] and flow.s.inputs == []
    assert 'setup_battle' not in flow.s.state and flow._owned_battle_id is None
