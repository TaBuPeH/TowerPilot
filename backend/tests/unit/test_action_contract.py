"""The pure action contract: schedule policies, field validation, the catalog (roadmap item 6, A2-1)."""
import math

import pytest

from towerpilot_workflows import control_settings
from towerpilot_workflows.action_contract import (
    CATALOG, DEFAULT_SHOPPING_INTERVAL_SEC, AfterCompletion, ActionDefinition, Backoff, Capabilities, Catalog, Decision, Field,
    AppearanceDriven, StartToStart, effect_of, shopping_interval)


# ------------------------------------------------------------ StartToStart
def evaluate(now, nwa, *, open_=False, manual=False, interval=100.0, skip=60.0):
    return StartToStart().evaluate(now, dict(next_window_at=nwa, window_open=open_),
                                   dict(manual=manual, interval=interval, skip_duration=skip))


def test_the_first_window_is_due_at_once_with_an_unknown_due_time():
    decision, state = evaluate(5.0, 0.0)
    assert decision.kind == 'due' and decision.eligible_at is None and state['next_window_at'] == 0.0


@pytest.mark.parametrize('now,kind', [(99.999, 'wait'), (100.0, 'due'), (159.999, 'due'), (160.0, 'skip_missed')])
def test_a_window_is_due_at_its_time_and_skipped_once_wholly_in_the_past(now, kind):
    assert evaluate(now, 100.0)[0].kind == kind


def test_a_long_gap_is_skipped_by_whole_intervals_with_no_catch_up():
    decision, state = evaluate(1000.0, 100.0)
    assert (decision.kind, decision.missed) == ('skip_missed', 10)          # floor(900 / 100) + 1
    assert state['next_window_at'] == 100.0 + 10 * 100.0 and decision.next_due_at == 1100.0


def test_a_manual_start_is_never_skipped_and_an_open_window_waits():
    assert evaluate(5000.0, 100.0, manual=True)[0].kind == 'due'
    decision, _ = evaluate(5000.0, 100.0, open_=True, manual=True)
    assert (decision.kind, decision.reason) == ('wait', 'window_open')


def test_evaluate_never_changes_the_state_it_was_given():
    state = dict(next_window_at=100.0, window_open=False)
    StartToStart().evaluate(1000.0, state, dict(interval=100.0, skip_duration=60.0))
    assert state == dict(next_window_at=100.0, window_open=False)


def test_a_window_is_capped_by_the_interval_and_reanchoring_shortens_never_extends():
    window, state = StartToStart.open_window(50.0, dict(next_window_at=0.0, window_open=False), interval=30.0, window_sec=60.0)
    assert (window['duration'], window['deadline'], state['next_window_at'], state['window_open']) == (30.0, 80.0, 80.0, True)
    assert StartToStart.close_window(state)['window_open'] is False
    assert StartToStart.reanchor(50.0, 120.0) == 170.0
    assert StartToStart.window_end(50.0, 5.0, 80.0) == 55.0 and StartToStart.window_end(50.0, 600.0, 80.0) == 80.0


# ------------------------------------------------------- AppearanceDriven
def appears(now, nwa, *, open_=False, manual=False, seen=True):
    return AppearanceDriven().evaluate(now, dict(next_window_at=nwa, window_open=open_), dict(manual=manual, appearance=seen))


def test_an_attempt_is_due_only_when_the_thing_is_on_screen_and_the_spacing_has_passed():
    assert appears(5.0, 0.0)[0].kind == 'due' and appears(5.0, 0.0)[0].eligible_at is None        # nothing owed: due at once
    assert appears(99.999, 100.0)[0].kind == 'wait' and appears(99.999, 100.0)[0].reason == 'spacing'
    assert appears(100.0, 100.0)[0].kind == 'due' and appears(100.0, 100.0)[0].eligible_at == 100.0
    # unknown is not absence and authorizes nothing: no appearance, no attempt, however long ago the spacing passed
    decision, _ = appears(10_000.0, 100.0, seen=False)
    assert (decision.kind, decision.reason, decision.next_due_at) == ('wait', 'no_appearance', 100.0)
    assert appears(10_000.0, 0.0, seen=None)[0].kind == 'wait'


def test_a_late_poll_is_never_a_missed_window_and_nothing_is_owed():
    # the start-to-start policy would skip these whole intervals; an appearance-driven action owes nothing for a gap
    decision, state = appears(100_000.0, 100.0)
    assert decision.kind == 'due' and decision.missed == 0 and state['next_window_at'] == 100.0


def test_a_manual_request_waits_for_neither_an_appearance_nor_the_spacing_and_an_open_attempt_waits():
    assert appears(5.0, 10_000.0, manual=True, seen=False)[0].kind == 'due'
    decision, _ = appears(5000.0, 100.0, open_=True, manual=True)
    assert (decision.kind, decision.reason) == ('wait', 'window_open')


def test_appearance_driven_evaluate_never_changes_the_state_it_was_given():
    state = dict(next_window_at=100.0, window_open=False)
    AppearanceDriven().evaluate(1000.0, state, dict(manual=False, appearance=True))
    assert state == dict(next_window_at=100.0, window_open=False)


def test_the_spacing_arithmetic_of_an_appearance_driven_action():
    policy = AppearanceDriven
    window = policy.open_window(50.0, 60.0)
    assert window == dict(started_at=50.0, deadline=110.0, duration=60.0)                  # spacing never shortens the attempt
    assert policy.window_end(50.0, 5.0, 110.0) == 55.0 and policy.window_end(50.0, 600.0, 110.0) == 110.0
    # only an attempt that sent input anchors the spacing
    assert policy.retry_anchor(50.0, True) == 50.0 and policy.retry_anchor(50.0, False) is None
    # a timeout leaves a floor: the later of the safety backoff and the host's input pacing
    assert policy.safety_floor(110.0, 15.0, 100.0) == 125.0 and policy.safety_floor(110.0, 15.0, 400.0) == 400.0
    # the next attempt: `interval` after the anchor, never before the floor; no anchor owes nothing but the floor
    assert policy.spacing_floor(50.0, 960.0, 125.0) == 1010.0 and policy.spacing_floor(50.0, 5.0, 125.0) == 125.0
    assert policy.spacing_floor(None, 960.0, 125.0) == 125.0 and policy.spacing_floor(None, 960.0, 0.0) == 0.0
    # a confirmed claim resumes at once, after the host's input pacing
    assert policy.resume_at(200.0, 150.0) == 200.0 and policy.resume_at(200.0, 260.0) == 260.0


def test_the_floating_gem_controls_carry_the_gathering_settings_copy_and_bounds():
    from player.gather_settings import ORBIT_TIMING_FIELDS
    pairs = {'interval_sec': 'interval_sec', 'window_sec': 'attempt_timeout_sec', 'tap_interval_sec': 'retry_interval_sec'}
    fields = {field.path: field for field in CATALOG.get('floating_gems').fields}
    for path, setting in pairs.items():
        spec = ORBIT_TIMING_FIELDS[setting]
        assert (fields[path].label, fields[path].min, fields[path].max) == (spec['label'], spec['min'], spec['max']), path
    assert CATALOG.get('floating_gems').schedule.basis == 'appearance_driven' and CATALOG.get('ad_gems').schedule.basis == 'start_to_start'


# ---------------------------------------------------------- AfterCompletion
def test_after_completion_is_due_strictly_after_the_interval_or_at_once_for_a_continuation():
    policy, state = AfterCompletion(), dict(last_completed_at=1000.0)
    assert policy.evaluate(1090.0, state, dict(interval=90))[0].kind == 'wait'              # `>` never `>=`
    assert policy.evaluate(1090.001, state, dict(interval=90))[0].kind == 'due'
    decision, _ = policy.evaluate(1000.0, state, dict(interval=90, continuation_ready=True))
    assert (decision.kind, decision.reason) == ('due', 'continuation')
    assert AfterCompletion.complete(1234.0, state) == dict(last_completed_at=1234.0)


# ------------------------------------------------------------------ Backoff
def facts(created, updated, failures, refusal_at=None):
    return dict(newest_created_at=created, newest_updated_at=updated, failures=failures, refusal_at=refusal_at)


def test_the_backoff_series_and_its_cap():
    policy = Backoff()
    assert [policy.delay(n) for n in range(0, 9)] == [0, 30, 60, 120, 240, 480, 900, 900, 900]


def test_the_next_attempt_is_the_later_of_the_backoff_and_the_minimum_spacing():
    policy = Backoff()
    decision, _ = policy.evaluate(100.0, facts(created=95.0, updated=100.0, failures=1))
    assert (decision.kind, decision.next_attempt_at) == ('wait', 130.0)
    decision, _ = policy.evaluate(100.0, facts(created=99.0, updated=100.0, failures=0))
    assert decision.next_attempt_at == 109.0                                    # created + 10 beats updated + 0
    assert policy.evaluate(130.0, facts(created=95.0, updated=100.0, failures=1))[0].kind == 'due'


def test_a_refusal_recheck_and_a_running_attempt():
    policy = Backoff()
    assert policy.evaluate(100.0, facts(None, None, 0, refusal_at=110.0))[0].next_attempt_at == 110.0
    assert policy.evaluate(100.0, facts(created=90.0, updated=None, failures=0))[0].kind == 'due'   # no spacing while one runs


def test_five_fast_failures_open_the_circuit_and_fast_failure_has_one_definition():
    policy = Backoff()
    assert policy.evaluate(10_000.0, facts(created=1.0, updated=2.0, failures=5))[0].kind == 'blocked'
    assert policy.evaluate(10_000.0, facts(created=1.0, updated=2.0, failures=4))[0].kind == 'due'
    assert policy.fast_failure('failed', 119.9, False) and policy.fast_failure('interrupted', 1, False)
    assert not policy.fast_failure('failed', 120, False)                         # it ran long enough
    assert not policy.fast_failure('failed', 1, True)                            # a fence refusal is not a failure
    assert not policy.fast_failure('cancelled', 1, False) and not policy.fast_failure('succeeded', 1, False)


# ------------------------------------------------------------------- fields
LEGACY_FIELDS = {
    'ad_gems': {'enabled': bool, 'interval_sec': (1, 3600), 'window_sec': (5, 60)},
    'floating_gems': {'enabled': bool, 'interval_sec': (1, 3600), 'window_sec': (5, 60), 'tap_interval_sec': (0.5, 30)},
    'shopping': {'enabled': bool, 'interval_sec': (1, 86400)},
    'rewards': {'enabled': bool}, 'abilities': {'enabled': bool}, 'rescue': {'enabled': bool}, 'repeat': {'enabled': bool},
}


def test_the_control_table_is_the_one_the_catalog_generates_plus_the_gates_not_migrated_yet():
    assert control_settings.FIELDS == LEGACY_FIELDS
    assert {name: spec for name, spec in CATALOG.control_fields().items()} == {
        name: LEGACY_FIELDS[name] for name in ('ad_gems', 'floating_gems', 'shopping')}


GRID = [True, False, None, 0, 1, 1.0, 4.999, 5, 60, 60.001, 0.5, 30, 30.5, 3600, 3601, 86400, 86401,
        math.nan, math.inf, -math.inf, -1, '5', [5], {'a': 1}]


@pytest.mark.parametrize('action', ['ad_gems', 'floating_gems', 'shopping'])
def test_field_validation_equals_the_validator_every_control_change_goes_through(action):
    for field in CATALOG.get(action).fields:
        for value in GRID:
            if value is None:
                continue                               # null means "inherit" and is allowed for every field
            try:
                control_settings.validate_patch({'routines': {action: {field.path: value}}})
                accepted = True
            except ValueError:
                accepted = False
            assert field.valid(value) is accepted, (action, field.path, value)


def test_the_catalog_describes_the_pilot_and_a_copy_can_be_extended_without_touching_it():
    assert [item.id for item in CATALOG] == ['ad_gems', 'floating_gems', 'shopping', 'harvest_launch', 'daily_missions', 'guild_progress', 'event_missions', 'quest_visit',
                                             'guild_visit', 'guild_store', 'free_store_gems', 'quest_scan', 'shatter', 'housekeeping',
                                             'tournament_entry', 'tournament_ticket', 'in_run_cards', 'surrender', 'setup_capture', 'max_wave', 'repeat_after_death', 'sm_ride',
                                             'tournament_day_marker', 'perk_ban_cache', 'compiled_rule', 'demon_mode_always', 'uw_toggle']
    assert CATALOG.get('harvest_launch').capabilities.run_now == 'not_supported'
    copy = CATALOG.copy()
    probe = ActionDefinition(id='synthetic_probe', label='Probe', family='gems', hosts=frozenset({'test'}),
                             schedule=StartToStart(), fields=(Field('enabled', 'bool'),), capabilities=Capabilities(run_now=True))
    copy.register(probe)
    assert 'synthetic_probe' in copy and 'synthetic_probe' not in CATALOG
    with pytest.raises(ValueError):
        copy.register(probe)
    assert copy.get('synthetic_probe').descriptor()['fields'][0]['path'] == 'enabled'


def test_unknown_is_none_never_zero():
    assert Decision('wait').next_due_at is None and Decision('due').eligible_at is None

# ------------------------------------------------------------- the shopping interval
PRESETS = [{}, {'shop_interval_sec': 30}, {'shopping_plan': {'interval_sec': 45}}, {'shopping_plan': None},
           {'shop_interval_sec': 30, 'shopping_plan': {'interval_sec': 45}}, {'shop_interval_sec': 30, 'shopping_plan': None}]


@pytest.mark.parametrize('preset', PRESETS)
def test_the_one_shopping_interval_equals_the_four_expressions_it_replaced(preset):
    # battle_session.publish and run_controls.resolve: the plan's interval, else the preset's, else 90
    legacy = (preset.get('shopping_plan') or {}).get('interval_sec', preset.get('shop_interval_sec', 90))
    assert shopping_interval(preset) == shopping_interval(preset, tracked=True, sprint=True) == legacy
    if 'shop_interval_sec' in preset:               # the orchestrator indexed the key, so only these presets ran there
        assert shopping_interval(preset, tracked=True, sprint=False) == (preset.get('shopping_plan') or {}).get('interval_sec', preset['shop_interval_sec'])
        assert shopping_interval(preset, tracked=False, sprint=False) == preset['shop_interval_sec']   # an untracked run ignores the plan
    assert shopping_interval(preset, tracked=False, sprint=True) == 20


def test_the_default_interval_is_defined_once_by_the_shopping_field_and_run_workspace_uses_it():
    assert DEFAULT_SHOPPING_INTERVAL_SEC == 90 == CATALOG.get('shopping').field('interval_sec').default

def test_only_a_balance_rise_confirms_a_gem_claim_and_a_vanished_battle_offer_does_not():
    assert effect_of('ad_gems', 'balance_increased') == effect_of('floating_gems', 'balance_increased') == 'confirmed'
    assert effect_of('ad_gems', 'offer_disappeared') == 'unconfirmed'           # the offer was gone from one look after the tap; no balance was read
    assert effect_of('ad_gems', None) == 'unconfirmed'


def test_a_collector_launch_claims_no_evidence_because_starting_a_process_proves_nothing_ran():
    assert CATALOG.get('harvest_launch').evidence == ()
    assert 'offer_disappeared' not in CATALOG.get('ad_gems').evidence


# ------------------------------------------------------------- item 7, A3b kind B (r18e): main-loop upkeep
def test_the_upkeep_definitions_are_plain_orchestrator_actions_with_the_schedules_of_the_code_they_replace():
    from towerpilot_workflows.action_contract import Cooldown, upkeep_outcome
    rows = {'compiled_rule': (Cooldown(), ('rule_confirmed',)), 'demon_mode_always': (Cooldown(15.0), ('fire_confirmed',)), 'uw_toggle': (Backoff(10, 2, 120), ('toggle_verified',))}
    for ident, (schedule, evidence) in rows.items():
        definition = CATALOG.get(ident)
        assert (definition.schedule, definition.evidence, definition.family, definition.hosts) == (schedule, evidence, 'rules', frozenset({'orchestrator'}))
        assert (definition.fields, definition.capabilities, definition.restricted, definition.saved, definition.documents, definition.scope, definition.consumes) == \
               ((), Capabilities(), None, None, (), None, None)
    assert upkeep_outcome('demon_mode_always', True) == dict(result='succeeded', reason='fire_confirmed', effect='confirmed', attempts=1, confirmed=1, evidence=('fire_confirmed',))
    assert upkeep_outcome('uw_toggle', False) == dict(result='failed', reason='not_confirmed', effect='unknown', attempts=1, confirmed=0, evidence=())


def test_a_held_deadline_is_due_exactly_at_it_and_a_held_ladder_never_opens_a_circuit():
    ladder = Backoff(10, 2, 120)
    assert [ladder.retry_at(100.0, n) for n in range(1, 8)] == [110.0, 120.0, 140.0, 180.0, 220.0, 220.0, 220.0]
    for failures_held in (0, 1):
        decide = lambda now, deadline: ladder.evaluate(now, {'failures': 0, 'refusal_at': deadline})[0].kind
        assert (decide(99.9, 100.0), decide(100.0, 100.0), decide(5.0, 0.0)) == ('wait', 'due', 'due')
