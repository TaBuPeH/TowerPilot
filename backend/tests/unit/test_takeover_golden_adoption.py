"""Golden answers of the orchestrator's adoption path on the unchanged code (roadmap item 3, package T3-0).

orchestrator.main() runs for real over the real BattleSession and a temporary ledger; the device edge and the screen readers are fakes
(takeover_golden_loop.LoopWorld). Each scenario is the exact ordered journal of device calls, ledger writes and log events, the ledger as it was
left, and how the loop ended: a battle already on screen when the runner starts (origin adopted), the death branch with and without a pending
stop_after_run, the confirmed backward-wave branch (a replaced battle) and the probation that holds small or unconfirmed regressions."""
import json

import pytest

import takeover_golden as golden
from takeover_golden_scenarios import ADOPTION

NAME = 'orchestrator_adoption'


def test_the_recorded_document_holds_exactly_these_scenarios():
    golden.assert_registry(NAME, ADOPTION)


@pytest.mark.parametrize('scenario', sorted(ADOPTION))
def test_the_orchestrator_answers_what_was_recorded(scenario, tmp_path, monkeypatch):
    golden.assert_recorded(NAME, scenario, ADOPTION[scenario](tmp_path, monkeypatch))


def lines(scenario):
    return [json.loads(line) for line in golden.recorded(NAME)[scenario]['journal']]


def calls(scenario):
    return [entry[0] if entry[0] != 'event' else f'event:{entry[1]}' for entry in lines(scenario)]


def test_no_scenario_crashed_the_loop():
    assert all(entry['crashes'] == 0 for entry in golden.recorded(NAME).values())


def test_the_death_branch_order():
    """Death handler: finish the record, log, collect stats (taps), death rules, the run flag, the repeat decision, then RETRY."""
    order = calls('death_plain')
    sequence = ['session.finish', 'event:death', 'runlog.collect', 'run_death_rules', 'runflag.requested', 'reload_battle_source', 'session.prepare_next',
                'detect.death_screen', 'act.tap', 'event:retry']
    positions = [order.index(step, order.index('session.finish')) for step in sequence]
    assert positions == sorted(positions)
    assert lines('death_plain')[order.index('act.tap')][1:] == [540, 1500, 'auto_retry']


def test_a_pending_stop_after_run_leaves_before_any_restart_and_keeps_the_flag():
    scenario = golden.recorded(NAME)['death_with_pending_stop_after_run']
    order = calls('death_with_pending_stop_after_run')
    assert scenario['ended'] == 'returned' and scenario['stop_flag_left'] is True and scenario['stop_flag_text'] == 'scheduler transition'
    assert order[-1] == 'event:stop_after_run' and 'act.tap' not in order and 'session.prepare_next' not in order[order.index('session.finish'):]


def test_a_confirmed_backward_wave_finishes_the_old_record_and_names_it_the_predecessor():
    scenario = golden.recorded(NAME)['replaced_battle_confirmed']
    old, new = scenario['ledger']
    assert (old['status'], old['reason']) == ('ended', 'wave_went_backwards')
    assert (new['origin'], new['previous'], new['status']) == ('partial_adoption', old['battle'], 'active')


@pytest.mark.parametrize('scenario', ['one_big_dip_stays_on_probation', 'two_big_dips_stay_on_probation', 'small_dip_below_the_margin'])
def test_an_unconfirmed_regression_changes_nothing(scenario):
    recorded = golden.recorded(NAME)[scenario]
    assert len(recorded['ledger']) == 1 and recorded['ledger'][0]['status'] == 'active'
    assert not any('run_boundary' in line or 'session.finish' in line for line in recorded['journal'])
