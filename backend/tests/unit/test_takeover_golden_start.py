"""Golden answers of POST /api/control action "start" on the unchanged dashboard (roadmap item 3, package T3-0).

The real Flask route over a temporary root and ledger; only process creation, psutil and the process table are faked (the harvest route
tests' harness). Each scenario is compared with what was recorded before the explicit takeover existed: the order of the steps (the owner
check, readiness, the guard and context checks BEFORE the collector's input stops, the hold, the stop and release wait, the launch,
end_transfer), the accepted operation's stored request, the shape of the battle_envelope _launch_operation stored, the retry and receipt
behaviour, and the surviving harvest choice."""
import pytest

import takeover_golden as golden
from takeover_golden_start import START

NAME = 'dashboard_start'


def test_the_recorded_document_holds_exactly_these_scenarios():
    golden.assert_registry(NAME, START)


@pytest.mark.parametrize('scenario', sorted(START))
def test_start_answers_what_was_recorded(scenario, dash, launches, monkeypatch):
    golden.assert_recorded(NAME, scenario, START[scenario](dash, launches, monkeypatch))


def recorded(scenario):
    return golden.recorded(NAME)[scenario]


def test_the_guard_and_the_context_check_come_before_the_collectors_input_stops():
    """Read from the recording: in every refusal that is not an owner refusal the hold (begin_transfer) is never reached, and in the
    accepted transfer it comes after both checks and before the stop."""
    accepted = recorded('start_with_a_collector')['journal']
    order = [accepted.index(step) for step in ('call refuse_if_blocked', 'call verify_expected', 'call begin_transfer', 'call stop_collectors',
                                              'call wait_for_release', 'call Popen -m runtime.operation_worker <ROOT>/orchestrator.py --instance main --preset bp_farm',
                                              'call end_transfer')]
    assert order == sorted(order) and len(set(order)) == 7
    for scenario in ('stale_context_leaves_the_collector', 'run_scope_guard_blocks_before_stop', 'readiness_failure_leaves_the_collector'):
        assert 'call begin_transfer' not in recorded(scenario)['journal']
        assert recorded(scenario)['collector_status'] == 'running' and recorded(scenario)['terminated'] == 0


def test_end_transfer_is_in_the_finally_of_the_launch_only():
    assert recorded('launch_failure_ends_the_transfer')['journal'][-1] == 'call end_transfer'
    assert 'call end_transfer' not in recorded('collector_still_stopping')['journal']     # the hold expires by itself


def test_the_envelope_selector_is_the_key_and_the_body_is_the_effective_preset():
    for scenario in ('start_with_a_collector', 'start_without_a_collector'):
        envelope = recorded(scenario)['envelope']
        assert envelope['preset_key'] == envelope['selected'] == envelope['config_preset'] == 'bp_farm'
        assert envelope['preset_is_effective_preset'] is True and envelope['controls_are_resolved'] is True
        assert envelope['stored_request'] == {'action': 'start', 'preset': 'bp_farm'}
