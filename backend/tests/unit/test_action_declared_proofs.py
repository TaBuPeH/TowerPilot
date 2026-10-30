"""The proofs the declaration of item 6 still needed after batch 1 (roadmap item 6, batch 2): interruption and ownership of the manual path and of
the floating-gem windows, Run now that is never offered for what must not be started, and the map from the declaration to the tests that prove it.

Everything runs the real Flask routes over a temporary root and ledger with the real `BattleSession`, `ActionSession` and gem collectors as the driver, on a fake clock
and a fake device (golden.gem_world): no frame, no input, no device, no live ledger.
"""

import paths as test_paths
import ast
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import action_golden as golden
from control import actions as actions_api
from control import battles as battle_api
from control import operations
from flows.shard import GemWatch
from runtime.action_bindings import BindingBase, host_bindings, run_now_tokens
from runtime.action_session import ActionSession
from runtime.battle_session import BattleSession
from towerpilot_workflows.action_contract import CATALOG, ActionDefinition, BindingResult, Capabilities, Catalog, Field, StartToStart, use_catalog
from towerpilot_workflows.actions import Actions
from towerpilot_workflows.operations import Conflict

TESTS = test_paths.TESTS


def make_driver(dash, world, key='run-1', *, orbit=True, instance='main', host='collector'):
    """A driver as the ledger sees one: an accepted and running operation, its tracked battle, an action session and the real gem collectors."""
    cfg = dash.load_config()
    cfg['active_instance'] = instance
    selected = operations.context(dash.ROOT, cfg, {'preset': 'bp_farm'}, None)
    ledger = operations.ledger(dash.ROOT)
    operation, _ = ledger.accept('run', selected, {}, key)
    operation = ledger.transition(operation['id'], operation['revision'], 'running', worker={'pid': os.getpid(), 'creation_time': 1.0})
    preset = {'tier': 1, 'label': 'Farm', 'shopping': [], 'shop_interval_sec': 90, 'gather': {'ad_gems': True, 'gem_orbit': {'enabled': orbit}}}
    session = BattleSession(battle_api.ledger(dash.ROOT), operation['id'], preset, cfg)
    actions = ActionSession.for_battle(session, host)
    gems = GemWatch(preset['gather'], session=actions)
    gems.ad_gems.rng = world.rng
    rs = SimpleNamespace(gems=gems, tracker=SimpleNamespace(last=12), mission=None, shop=None)
    session.observe(rs, 12)
    return SimpleNamespace(dash=dash, world=world, ledger=ledger, operation=operation, session=session, rs=rs, selected=selected, client=dash.app.test_client(),
                           device=selected['device_key'])


def rows(h):
    return Actions.read(operations.ledger_path(h.dash.ROOT), device=h.device)[0]


def body(h, **fields):
    battle = h.session.store.get(h.session.battle['id'])
    return dict(battle_id=battle['id'], generation=h.session.generation, expected_revision=battle['control_revision'], **fields)


def post(h, kind, **fields):
    return h.client.post(f"/api/operations/{h.operation['id']}/{kind}", json=body(h, **fields))


def step(h, seconds=1.0, *, offer=False, visible=True):
    h.world.now += seconds
    h.world.offer = offer or h.world.offer
    h.rs.gems.poll(h.world.frame())
    h.session.checkpoint(h.rs, battle_visible=visible)


def commands(h):
    return {c['request_keys'][0]: c for c in h.session.store.commands(h.session.battle['id'])}


def stop(h):
    """What reconciliation does to a worker that died: the operation ends. The refresher then finishes the battle and settles the rows."""
    h.ledger.transition(h.operation['id'], h.ledger.get(h.operation['id'])['revision'], 'interrupted')


def refresh(h):
    battle_api.reconcile_existing(h.dash.ROOT)
    return actions_api.settle(h.dash.ROOT)


@pytest.fixture
def world():
    sim = golden.World(seed=5)
    sim.claim_confirms = False
    with golden.gem_world(sim):
        yield sim


# ------------------------------------------------- interruption: a floating-gem attempt stopped at each point of its life
@pytest.mark.parametrize('point,polls,hit', [('after_the_attempt_opened', 1, False), ('before_confirmation', 6, False)])
def test_a_driver_stopped_in_a_floating_gem_attempt_is_settled_interrupted_by_the_refresher_not_succeeded(dash, world, point, polls, hit):
    h = make_driver(dash, world)
    h.rs.gems.ad_gems.enabled = False
    for _ in range(polls):
        step(h)
    (open_row,) = [row for row in rows(h) if row['state'] == 'running' and row['action'] == 'floating_gems']
    assert world.taps, 'the attempt had sent input'
    taps = len(world.taps)
    stop(h)
    assert {row['id']: row['state'] for row in rows(h)}[open_row['id']] == 'running'          # nothing settled it yet: no later action, no GET
    assert open_row['id'] in refresh(h)
    settled = {row['id']: row for row in rows(h)}[open_row['id']]
    assert settled['state'] == 'interrupted' and settled['finished_at'] is None and settled['duration_ms'] is None
    assert (settled['outcome']['result'], settled['outcome']['effect'], settled['settled_reason']) == ('interrupted', 'unknown', 'operation_ended')
    assert refresh(h) == [] and len(world.taps) == taps, 'idempotent, and settlement sent no input'


def test_a_floating_gem_attempt_that_finished_keeps_the_outcome_it_wrote_when_the_driver_dies_later(dash, world):
    h = make_driver(dash, world)
    h.rs.gems.ad_gems.enabled = False
    world.hit_next_tap = True
    for _ in range(4):
        step(h)
    done = [row for row in rows(h) if row['action'] == 'floating_gems' and row['state'] != 'running']
    assert done and done[0]['state'] == 'succeeded' and done[0]['outcome']['effect'] == 'confirmed'
    stop(h)
    refresh(h)
    assert [row for row in rows(h) if row['id'] == done[0]['id']] == done[:1], 'settlement never rewrites a finished row'


# ---------------------------------------------------------- interruption: a Run now whose driver dies
def test_a_run_now_accepted_but_not_yet_acted_on_when_the_driver_dies_is_never_replayed_by_a_restarted_driver(dash, world):
    first = make_driver(dash, world, 'run-1')
    first.rs.gems.ad_gems.enabled = False
    asked = post(first, 'actions', action='ad_gems', idempotency_key='ask-1')
    assert asked.status_code == 200 and asked.get_json()['command']['state'] == 'requested'
    stop(first)                                                              # the worker died before its next checkpoint: the request was never acted on
    refresh(first)
    dead = first.session.store.get(first.session.battle['id'])
    assert dead['status'] == 'interrupted' and first.rs.gems.ad_gems.manual_requested is False
    assert commands(first)['ask-1']['state'] == 'expired', 'finishing the battle expires a request nobody acted on'
    again = first.client.post(f"/api/operations/{first.operation['id']}/actions", json=body(first, action='ad_gems', idempotency_key='ask-1'))
    assert again.status_code == 200 and again.get_json()['command']['id'] == asked.get_json()['command']['id'] and again.get_json()['command']['state'] == 'expired'
    fresh_key = post(first, 'actions', action='ad_gems', idempotency_key='ask-2')
    assert fresh_key.status_code == 409 and 'no longer active' in fresh_key.get_json()['error']
    second = make_driver(dash, world, 'run-2', orbit=False)                   # a restarted driver: a new operation, battle and generation
    second.rs.gems.ad_gems.enabled = False
    taps = len(world.taps)
    for _ in range(3):
        step(second)
    assert second.rs.gems.ad_gems.manual_requested is False and second.rs.gems.ad_gems.manual_command_id is None
    assert second.session.store.pending_commands(second.session.battle['id'], second.session.generation) == []
    assert [row for row in rows(second) if row['trigger'] == 'manual'] == [], 'the dead request was replayed as a manual window'
    assert len(world.taps) == taps


def test_a_run_now_started_when_the_driver_dies_expires_in_its_receipt_and_its_window_is_settled_interrupted(dash, world):
    h = make_driver(dash, world)
    h.rs.gems.ad_gems.enabled = False
    asked = post(h, 'actions', action='ad_gems', idempotency_key='ask-1')
    assert asked.status_code == 200
    for _ in range(4):
        step(h, offer=True)
    assert commands(h)['ask-1']['state'] == 'started'
    h.rs.gems.orbit.enabled = False
    (window,) = [row for row in rows(h) if row['trigger'] == 'manual']
    assert window['state'] == 'running' and window['command_id'] == asked.get_json()['command']['id']
    stop(h)
    assert window['id'] in refresh(h)
    settled = {row['id']: row for row in rows(h)}[window['id']]
    assert settled['state'] == 'interrupted' and settled['outcome']['effect'] == 'unknown' and settled['command_id'] == window['command_id']
    assert commands(h)['ask-1']['state'] == 'expired', 'a command is never completed on behalf of a driver that did not finish it'
    receipt = h.client.post(f"/api/operations/{h.operation['id']}/actions", json=body(h, action='ad_gems', idempotency_key='ask-1'))
    assert receipt.status_code == 200 and receipt.get_json()['command']['state'] == 'expired' and receipt.get_json()['command']['id'] == window['command_id']


@pytest.mark.parametrize('host', ['orchestrator', 'collector'])
def test_run_now_through_the_route_is_recorded_as_a_manual_execution_linked_to_its_command_for_both_battle_hosts(dash, world, host):
    world.claim_confirms = True
    h = make_driver(dash, world, host=host, orbit=False)
    h.rs.gems.ad_gems.enabled = False
    asked = post(h, 'actions', action='ad_gems', idempotency_key='ask-1').get_json()['command']
    for _ in range(14):
        step(h, offer=True)
    assert commands(h)['ask-1']['state'] == 'completed'
    (row,) = [row for row in rows(h) if row['trigger'] == 'manual']
    assert (row['command_id'], row['host']['driver'], row['host']['battle_id'], row['host']['generation']) == (asked['id'], host, h.session.battle['id'], h.session.generation)
    assert row['state'] in ('failed', 'succeeded') and row['outcome']['effect'] in ('confirmed', 'unconfirmed')


# ------------------------------------------------------------------------ ownership of the manual path and the controls
def test_a_stale_generation_a_foreign_battle_and_a_foreign_operation_are_refused_and_record_nothing(dash, world):
    mine = make_driver(dash, world, 'run-1')
    battle = mine.session.store.get(mine.session.battle['id'])
    stale = mine.client.post(f"/api/operations/{mine.operation['id']}/live-controls",
                             json=dict(body(mine, changes={'routines.ad_gems.enabled': False}, idempotency_key='stale'), generation='an-older-generation'))
    assert stale.status_code == 409 and commands(mine) == {}
    stale_run = mine.client.post(f"/api/operations/{mine.operation['id']}/actions", json=dict(body(mine, action='ad_gems', idempotency_key='stale-run'), generation='older'))
    assert stale_run.status_code == 409 and commands(mine) == {}
    stranger = make_driver(dash, world, 'run-2', instance='other')            # another operation's battle, on another emulator
    foreign = mine.client.post(f"/api/operations/{mine.operation['id']}/actions",
                               json=dict(body(stranger, action='ad_gems', idempotency_key='foreign-battle')))
    assert foreign.status_code == 409 and commands(stranger) == {} and commands(mine) == {}
    wrong_operation = mine.client.post(f"/api/operations/{stranger.operation['id']}/live-controls",
                                       json=body(mine, changes={'routines.ad_gems.enabled': False}, idempotency_key='foreign-operation'))
    assert wrong_operation.status_code == 409 and commands(mine) == {}
    unknown = mine.client.post('/api/operations/not-an-operation/actions', json=body(mine, action='ad_gems', idempotency_key='nobody'))
    assert unknown.status_code in (404, 409) and commands(mine) == {}
    assert mine.session.store.get(battle['id'])['control_revision'] == battle['control_revision']


def test_a_replaced_driver_generation_can_no_longer_acknowledge_or_finish_anything_of_the_new_one(dash, world):
    h = make_driver(dash, world)
    asked = post(h, 'actions', action='ad_gems', idempotency_key='ask-1').get_json()['command']
    with pytest.raises(Conflict):
        h.session.store.acknowledge(h.session.battle['id'], 'a-replaced-generation', asked['id'], state='started')
    with pytest.raises(Conflict):
        h.session.store.finish(h.session.battle['id'], 'a-replaced-generation', reason='observed_results')
    assert commands(h)['ask-1']['state'] == 'requested' and h.session.store.get(h.session.battle['id'])['status'] == 'active'


# --------------------------------------------------- what must not be started is never offered a Run now
class Claims(BindingBase):
    """A binding that claims it can run now and reports progress: the definition it is registered for still decides."""
    def runnable(self):
        return True

    def variants(self):
        return ('v',)

    def progress(self):
        return BindingResult('pending')

    def routine(self, *, battle_visible, now, epoch):
        return dict(enabled=True, state='scheduled', next_due_at=epoch, window_ends_at=None)


def restricted_catalog():
    # item 7, r18d: the real catalog now declares `tournament_entry` and `surrender` (restricted, no fields, no live control). This test registers its own, stronger stand-ins
    # for the same keys (restricted WITH live controls), so the two real entries are left out of the copy; test_action_restricted_declarations.py covers the real ones.
    extended = Catalog(item for item in CATALOG if item.id not in ('tournament_entry', 'surrender'))
    extended.register(ActionDefinition(id='view_only', label='View only', family='gems', hosts=frozenset({'test'}), schedule=StartToStart(),
                                       fields=(Field('enabled', 'bool'),), capabilities=Capabilities(live_controls=False, run_now=True)))
    for ident, run_now, restricted in (('tournament_entry', False, 'a tournament run cannot be cancelled once a ticket is bought'),
                                       ('ticket_purchase', False, 'the gem cost escalates'), ('surrender', False, 'a person ends a run'),
                                       ('not_supported_yet', 'not_supported', None), ('allowed_probe', True, None)):
        extended.register(ActionDefinition(id=ident, label=ident, family='gems', hosts=frozenset({'test'}), schedule=StartToStart(),
                                           fields=(Field('enabled', 'bool'),), capabilities=Capabilities(live_controls=True, run_now=run_now),
                                           restricted=restricted))
    return extended


def test_a_restricted_or_unsupported_definition_publishes_no_run_now_even_when_its_binding_claims_it_can_run(dash, world):
    with use_catalog(restricted_catalog()):
        h = make_driver(dash, world)
        h.rs.action_bindings = {key: Claims() for key in ('tournament_entry@main', 'ticket_purchase@main', 'surrender@main', 'not_supported_yet@main', 'allowed_probe@main', 'view_only@main')}
        h.session.observe(h.rs, 12)
        controls = h.session.capabilities(h.rs)['controls']
        assert 'routines.allowed_probe@main.enabled' in controls and not [path for path in controls if '@main' in path and 'view_only' in path], 'a definition without live controls publishes none'
        refused_control = h.client.post(f"/api/operations/{h.operation['id']}/live-controls", json=body(h, changes={'routines.view_only@main.enabled': False}, idempotency_key='view-only'))
        assert refused_control.status_code == 400 and 'Unsupported controls' in refused_control.get_json()['error'] and commands(h) == {}
        published = h.session.capabilities(h.rs)['actions']
        assert 'allowed_probe@main' in published and 'allowed_probe@main:v' in published
        assert not [token for token in published if token.split('@')[0] in ('tournament_entry', 'ticket_purchase', 'surrender', 'not_supported_yet')]
        for action in ('tournament_entry@main', 'ticket_purchase@main', 'surrender@main', 'not_supported_yet@main:v'):
            refused = post(h, 'actions', action=action, idempotency_key=f'try-{action}')
            assert refused.status_code == 400 and refused.get_json()['error'] == 'Unsupported manual action', action
        assert commands(h) == {}
        for key in ('tournament_entry@main', 'surrender@main'):
            assert run_now_tokens(key, Claims()) == []
        assert run_now_tokens('allowed_probe@main', Claims()) == ['allowed_probe@main', 'allowed_probe@main:v']


def test_a_battle_driver_never_offers_a_tournament_a_ticket_a_surrender_or_a_battle_end(dash, world):
    h = make_driver(dash, world)
    published = h.session.capabilities(h.rs)
    assert published['actions'] == ['ad_gems', 'floating_gems']
    for action in ('tournament', 'start_tournament', 'tournament_entry', 'buy_ticket', 'ticket_purchase', 'surrender', 'end_round', 'end_run', 'cancel_run'):
        refused = post(h, 'actions', action=action, idempotency_key=f'try-{action}')
        assert refused.status_code == 400 and refused.get_json()['error'] == 'Unsupported manual action', action
    assert commands(h) == {}
    assert not [path for path in published['controls'] if any(word in path for word in ('tournament', 'ticket', 'surrender', 'end_round'))]


def test_the_bindings_send_no_input_they_import_nothing_that_could():
    forbidden = {'device', 'cv2', 'settings', 'vision', 'interactions', 'flows', 'adbclient', 'capture', 'act', 'subprocess', 'socket', 'orchestrator'}
    tree = ast.parse((test_paths.BACKEND / 'runtime' / 'action_bindings.py').read_text(encoding='utf-8'))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split('.')[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split('.')[0])
    assert not imported & forbidden, sorted(imported & forbidden)


# ------------------------------------------------------------- the declaration of done, mapped to the tests that prove it
# declaration (roadmap item 6) -> {test file: [test names]}. The map is checked against the files, so a renamed or deleted proof fails here.
DECLARATION = {
    'one definition, handler and policy contract gives lifecycle, timing, history and generic controls inside existing drivers': {
        'test_action_contract.py': ['test_a_window_is_due_at_its_time_and_skipped_once_wholly_in_the_past', 'test_the_catalog_describes_the_pilot_and_a_copy_can_be_extended_without_touching_it'],
        'test_action_session.py': ['test_an_execution_records_its_host_context_trigger_and_monotonic_duration', 'test_the_projection_is_published_beside_the_unchanged_routines_and_mirrors_them'],
        'test_action_dispatch_generated.py': ['test_the_session_methods_that_dispatch_publish_and_follow_run_now_name_no_action_and_no_gate',
                                              'test_the_saved_setting_entry_points_name_no_action',
                                              'test_a_control_of_a_registered_instance_reaches_its_binding_with_one_field_and_the_frozen_value_for_a_null',
                                              'test_a_binding_with_no_progress_of_its_own_has_no_run_now_so_nothing_can_be_acknowledged_from_another_handlers_state'],
        'test_action_dispatch_parity.py': ['test_every_control_of_every_host_shape_changes_the_same_state_and_is_acknowledged_the_same_way',
                                           'test_the_saved_effective_controls_of_every_preset_are_unchanged',
                                           'test_reading_and_editing_a_saved_run_is_unchanged_including_the_copy_on_write_of_a_shared_policy'],
    },
    'the pilot works end to end across the declared callers (six gem hosts, shopping, harvest launch attempts)': {
        'test_action_pilot_callers.py': ['test_every_production_gem_watch_is_constructed_with_its_hosts_session', 'test_each_gem_host_leaves_a_persisted_window_for_both_collectors',
                                         'test_a_shopping_sweep_that_buys_is_its_own_row_with_the_confirmed_purchases',
                                         'test_an_accepted_launch_is_one_row_keyed_by_its_durable_launch_key_and_linked_to_its_operation',
                                         'test_refusals_before_any_operation_exists_are_recorded_and_identical_ones_are_counted'],
        'test_action_declared_proofs.py': ['test_run_now_through_the_route_is_recorded_as_a_manual_execution_linked_to_its_command_for_both_battle_hosts'],
        'test_action_session.py': ['test_a_manual_run_now_is_recorded_as_a_manual_execution_linked_to_its_command',
                                   'test_a_manual_floating_gem_run_now_is_recorded_as_a_manual_execution_linked_to_its_command',
                                   'test_a_manual_shopping_sweep_is_linked_to_its_command_too'],
        'test_action_executions_route.py': ['test_the_first_instrumented_battle_exposes_the_executions_it_recorded'],
    },
    'a synthetic action gets live controls, Run now, a saved edit, history and projection through the existing routes, with no new endpoint and no Dashboard branch': {
        'test_action_synthetic_extension.py': ['test_the_descriptor_and_an_item_of_each_instance_reach_the_dashboard',
                                               'test_a_live_only_field_changes_through_live_controls_and_the_binding_installs_it',
                                               'test_run_now_starts_the_binding_and_its_own_progress_drives_both_acknowledgements',
                                               'test_the_same_run_now_key_replays_its_receipt_after_the_driver_ends_and_a_changed_payload_is_a_409',
                                               'test_a_saved_edit_reaches_the_registered_adapter_and_leaves_a_sibling_runs_shared_policy_unchanged',
                                               'test_its_executions_are_in_the_battle_detail_and_the_generic_history_route',
                                               'test_no_route_was_added_and_the_route_table_is_the_same_with_and_without_the_extension',
                                               'test_the_synthetic_action_is_named_in_no_production_file'],
        'test_action_baseline_snapshots.py': ['test_the_route_table_differs_from_the_baseline_only_by_the_declared_additions'],
    },
    'cadence parity': {
        'test_action_parity_gems.py': ['test_a_random_sequence_in_which_the_gem_comes_and_goes_replays_the_captured_digests',
                                       'test_the_first_ad_gem_window_opens_at_once_and_each_window_draws_its_interval_from_the_seeded_range',
                                       'test_a_long_gap_skips_whole_windows_with_no_catch_up_burst',
                                       'test_a_floating_gem_gap_owes_no_catch_up_and_the_next_attempt_waits_for_the_safety_backoff'],
        'test_action_parity_shopping.py': ['test_a_sweep_is_due_only_strictly_after_the_interval_unless_a_confirmed_continuation_waits'],
        'test_action_parity_harvest.py': ['test_the_backoff_series_the_spacing_and_the_circuit_are_the_documented_ones'],
    },
    'interruption parity': {
        'test_action_interruption.py': ['test_a_driver_stopped_mid_window_is_settled_interrupted_by_the_refresher_not_succeeded',
                                        'test_a_restarted_driver_replays_nothing_and_records_its_own_window',
                                        'test_a_shopping_sweep_stopped_after_its_first_purchase_is_settled_interrupted',
                                        'test_a_harvest_refusal_before_any_operation_exists_is_a_row_and_off_after_a_crash_stays_off'],
        'test_action_declared_proofs.py': ['test_a_driver_stopped_in_a_floating_gem_attempt_is_settled_interrupted_by_the_refresher_not_succeeded',
                                           'test_a_run_now_accepted_but_not_yet_acted_on_when_the_driver_dies_is_never_replayed_by_a_restarted_driver',
                                           'test_a_run_now_started_when_the_driver_dies_expires_in_its_receipt_and_its_window_is_settled_interrupted'],
    },
    'ownership parity': {
        'test_action_ownership.py': ['test_a_held_device_makes_the_attempt_a_recorded_device_owned_refusal_and_starts_no_worker',
                                     'test_a_row_of_a_live_generation_is_not_settled_by_a_replaced_one'],
        'test_action_declared_proofs.py': ['test_a_stale_generation_a_foreign_battle_and_a_foreign_operation_are_refused_and_record_nothing',
                                           'test_a_replaced_driver_generation_can_no_longer_acknowledge_or_finish_anything_of_the_new_one',
                                           'test_a_restricted_or_unsupported_definition_publishes_no_run_now_even_when_its_binding_claims_it_can_run',
                                           'test_a_battle_driver_never_offers_a_tournament_a_ticket_a_surrender_or_a_battle_end'],
        'test_harvest_lifecycle.py': [],
    },
    'the generic executions history is a recorded contract': {
        'test_dashboard_contract_pages.py': ['test_the_r17a_family_is_made_by_the_real_routes_and_is_the_same_twice'],
        'test_action_executions_route.py': ['test_the_selected_accounts_older_execution_is_reachable_behind_two_newer_rows_of_another_account',
                                            'test_a_battle_on_a_ledger_a_newer_build_wrote_is_not_interpreted'],
    },
}


def test_the_declaration_of_item_6_names_only_tests_that_exist():
    missing = []
    scanned = set()
    for declaration, files in DECLARATION.items():
        for name, tests in files.items():
            path = TESTS / 'unit' / name
            assert path.exists(), (declaration, name)
            defined = {node.name for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
            scanned.add(name)
            missing += [(declaration, name, test) for test in tests if test not in defined]
    assert 'test_action_ownership.py' in scanned
    assert not missing, missing


def test_every_declared_test_file_is_a_test_the_focused_subset_runs():
    for files in DECLARATION.values():
        for name in files:
            assert name.startswith('test_') and name.endswith('.py')
