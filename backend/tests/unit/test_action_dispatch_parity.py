"""The control dispatch, Run now acknowledgements, capability lists, publication and saved-setting transforms replay the traces
captured on the tree before batch 2 (roadmap item 6): deriving them from the action catalog must change nothing a person, the
ledger or the Dashboard can observe. Scenario drivers: action_dispatch_golden.py (explicit capture: test_action_dispatch_capture.py)."""
import pytest

import action_dispatch_golden as golden


@pytest.fixture(scope='module')
def captured():
    return golden.load()


@pytest.fixture(scope='module')
def run_now_now(tmp_path_factory):
    return golden.run_now_cases(tmp_path_factory.mktemp('run-now-gems'))


@pytest.fixture(scope='module')
def run_now_shopping_now(tmp_path_factory):
    return golden.run_now_shopping_cases(tmp_path_factory.mktemp('run-now-shopping'))


def check(captured, family, current):
    assert golden.roundtrip(current) == captured[family], family


def test_the_fixture_was_captured_before_the_dispatch_was_derived_from_the_catalog(captured):
    assert captured['version'] == 1 and set(captured['captured_on']) >= {
        'backend/runtime/battle_session.py', 'backend/player/run_controls.py', 'backend/control/run_workspace.py'}
    assert set(captured) == {'version', 'captured_on', 'capabilities', 'validator', 'controls', 'multi_control', 'plan_controls', 'run_now_gems',
                             'run_now_shopping', 'published', 'resolve', 'apply', 'merge', 'workspace'}


def test_the_capability_lists_of_every_host_shape_are_unchanged(captured, tmp_path):
    check(captured, 'capabilities', golden.capability_lists(tmp_path))


def test_the_control_validator_accepts_and_refuses_exactly_what_it_did(captured):
    check(captured, 'validator', golden.validator_cases())


def test_every_control_of_every_host_shape_changes_the_same_state_and_is_acknowledged_the_same_way(captured, tmp_path):
    check(captured, 'controls', golden.control_matrix(tmp_path))


def test_several_controls_in_one_command_drain_as_they_did_while_the_rewards_flow_owns_the_screen(captured, tmp_path):
    check(captured, 'multi_control', golden.multi_control(tmp_path))


def test_a_shopping_plan_edit_keeps_the_interval_override_and_the_frozen_gate(captured, tmp_path):
    check(captured, 'plan_controls', golden.plan_controls(tmp_path))


@pytest.mark.parametrize('case', ['ad_offer_confirmed', 'ad_no_offer', 'ad_no_offer_short_window', 'ad_disabled_offer', 'ad_window_already_open',
                                  'orbit_gem_visible', 'orbit_no_gem', 'orbit_no_gem_short_window', 'orbit_gem_visible_never_confirmed_short_window',
                                  'orbit_disabled', 'orbit_window_already_open'])
def test_a_run_now_on_a_collector_is_held_started_and_completed_exactly_as_before(captured, run_now_now, case):
    assert golden.roundtrip(run_now_now[case]) == captured['run_now_gems'][case], case


@pytest.mark.parametrize('case', ['whole', 'section', 'upgrade', 'utility_upgrade', 'defense_upgrade', 'not_in_the_catalog', 'sweep_active'])
def test_a_run_now_on_the_shopper_is_started_at_once_and_completed_when_the_sweep_ends(captured, run_now_shopping_now, case):
    assert golden.roundtrip(run_now_shopping_now[case]) == captured['run_now_shopping'][case], case


def test_the_published_routines_and_actions_are_unchanged_in_every_host_state(captured, tmp_path):
    check(captured, 'published', golden.published(tmp_path))


def test_the_saved_effective_controls_of_every_preset_are_unchanged(captured):
    check(captured, 'resolve', golden.resolve_cases())


def test_applying_saved_controls_to_a_preset_is_unchanged(captured):
    check(captured, 'apply', golden.apply_cases())
    check(captured, 'merge', golden.merge_cases())


def test_reading_and_editing_a_saved_run_is_unchanged_including_the_copy_on_write_of_a_shared_policy(captured):
    check(captured, 'workspace', golden.workspace_cases())
