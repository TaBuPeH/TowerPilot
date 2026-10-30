"""The control dispatch, the Run now progress and the saved settings are generated from the action catalog (roadmap item 6, batch 2).

What the migration must not change is pinned by the parity tests (test_action_dispatch_parity.py: traces captured before it). What
it must change is pinned here: nothing that dispatches a control, accepts or follows a Run now, publishes a routine or reads and
writes a saved setting names an action any more - each asks the catalog's definition and the binding or saved adapter registered for
it - and a definition added to a copy of the catalog is dispatched, validated and published by the same code.
"""

import paths as test_paths
import ast
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from player import saved_controls
from runtime import action_bindings
from runtime.action_bindings import BindingBase, host_bindings, outcome_of, reports_progress, run_now_tokens
from runtime.battle_session import BattleSession
from towerpilot_workflows import control_settings
from towerpilot_workflows.action_contract import (CATALOG, ActionDefinition, BindingResult, Capabilities, Field, StartToStart, catalog,
                                                  routine_key_valid, split_key, use_catalog)
from towerpilot_workflows.battles import Battles
from towerpilot_workflows.operations import Operations

BACKEND = test_paths.BACKEND
ACTIONS = {'ad_gems', 'floating_gems', 'shopping', 'shopping_plan'}
GATES = {'rewards', 'abilities', 'rescue', 'repeat'}


def constants(node):
    return {item.value for item in ast.walk(node) if isinstance(item, ast.Constant) and isinstance(item.value, str)}


def functions(path, *names, cls=None):
    tree = ast.parse((BACKEND / path).read_text(encoding='utf-8'))
    scope = tree.body if cls is None else next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == cls).body
    found = {node.name: node for node in scope if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return [found[name] for name in names]


def code_constants(function):
    """The string constants of a function's code (its docstring is prose, not a branch)."""
    body = function.body[1:] if function.body and isinstance(function.body[0], ast.Expr) and isinstance(getattr(function.body[0], 'value', None), ast.Constant) else function.body
    return set().union(*(constants(statement) for statement in body)) if body else set()


# ----------------------------------------------------------- nothing that dispatches names an action
def test_the_session_methods_that_dispatch_publish_and_follow_run_now_name_no_action_and_no_gate():
    for method in functions('runtime/battle_session.py', 'bindings', '_control_paths', 'capabilities', '_apply_control', 'checkpoint', '_manual_progress',
                            'publish', 'manual_for', cls='BattleSession'):
        named = code_constants(method) & (ACTIONS | GATES)
        assert not named, (method.name, sorted(named))


def test_the_saved_setting_entry_points_name_no_action():
    for path, names in (('player/run_controls.py', ('resolve', 'apply_to_preset', 'effective_preset')),
                        ('control/run_workspace.py', ('normalize_changes', '_capabilities', 'read', 'update')),
                        ('player/saved_controls.py', ('resolve', 'apply_to_preset', 'capabilities', 'write', 'routines_by_definition', 'frozen_documents')),
                        ('towerpilot_workflows/control_settings.py', ('flatten', 'validate_patch', 'merge_patch', 'documents'))):
        for function in functions(path, *names):
            named = code_constants(function) & ACTIONS
            assert not named, (path, function.name, sorted(named))


def test_the_ledgers_override_merge_names_no_document():
    (function,) = functions('towerpilot_workflows/battles.py', '_set_overrides', cls='Battles')
    assert not code_constants(function) & ACTIONS


def test_the_actions_themselves_are_named_only_where_their_binding_or_adapter_is_registered():
    """The per-action code is the binding and the saved adapter, each registered once; this is the whole list of those places."""
    registered = {'runtime/action_bindings.py': {'ad_gems', 'floating_gems', 'shopping', 'shopping_plan'},
                  'player/saved_controls.py': {'ad_gems', 'floating_gems', 'shopping', 'shopping_plan'}}
    assert 'runtime/action_bindings.py' in registered
    for path in registered:
        assert constants(ast.parse((BACKEND / path).read_text(encoding='utf-8'))) & ACTIONS, path
    for path, allowed in (('runtime/battle_session.py', {'_collection_evidence', 'manual_shopping'}), ('control/run_workspace.py', set()), ('player/run_controls.py', set())):
        tree = ast.parse((BACKEND / path).read_text(encoding='utf-8'))
        assert {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and code_constants(node) & ACTIONS} == allowed, path


# ----------------------------------------------------------------------------- the FIELDS view
def test_fields_is_the_catalogs_table_plus_the_gates_in_catalog_order_and_follows_a_catalog_copy():
    assert list(control_settings.FIELDS) == ['ad_gems', 'floating_gems', 'shopping', 'rewards', 'abilities', 'rescue', 'repeat']
    assert control_settings.FIELDS['shopping'] == {'enabled': bool, 'interval_sec': (1, 86400)} and len(control_settings.FIELDS) == 7
    extended = CATALOG.copy()
    extended.register(ActionDefinition(id='probe', label='Probe', family='gems', hosts=frozenset({'test'}), schedule=StartToStart(),
                                       fields=(Field('enabled', 'bool'), Field('burst', 'number', 1, 5))))
    with use_catalog(extended):
        assert list(control_settings.FIELDS)[:4] == ['ad_gems', 'floating_gems', 'shopping', 'probe']
        assert control_settings.FIELDS['probe'] == {'enabled': bool, 'burst': (1, 5)} and 'probe' in control_settings.FIELDS
    assert 'probe' not in control_settings.FIELDS and catalog() is CATALOG


@pytest.mark.parametrize('key,known', [('ad_gems@main', True), ('ad_gems@a-b_c1', True), ('ad_gems@', False), ('ad_gems@A', False), ('ad_gems@a.b', False),
                                        ('ad_gems@a@b', False), ('ad_gems@a:b', False), ('rewards@x', False), ('nothing@x', False), ('nothing', False), ('', False)])
def test_an_instance_key_resolves_to_its_actions_fields_only_with_a_valid_instance_name(key, known):
    assert (key in control_settings.FIELDS) is known
    assert routine_key_valid(key) is (known or key in ('rewards@x', 'nothing@x', 'nothing', '') and split_key(key)[1] in (None, 'x'))


def test_a_key_that_is_no_text_is_no_routine():
    for key in (None, 3, ('a',), b'ad_gems'):
        assert key not in control_settings.FIELDS
        assert routine_key_valid(key) is False


# ------------------------------------------------------------------- documents are a catalog fact
@pytest.fixture
def documented(monkeypatch):
    extended = CATALOG.copy()
    extended.register(ActionDefinition(id='probe', label='Probe', family='gems', hosts=frozenset({'test'}), schedule=StartToStart(),
                                       fields=(Field('enabled', 'bool'),), documents=('probe_doc',)))
    monkeypatch.setitem(control_settings._DOCUMENT_VALIDATORS, 'probe_doc', lambda value: None if isinstance(value, dict) and value.get('ok') else (_ for _ in ()).throw(ValueError('bad doc')))
    with use_catalog(extended):
        yield extended


def test_a_document_of_a_catalog_action_is_validated_by_its_owner_replaced_wholesale_and_never_flattened(documented):
    assert control_settings.documents() == {'shopping_plan', 'probe_doc'}
    assert control_settings.flatten({'probe_doc': {'ok': True, 'nested': {'deep': 1}}, 'routines': {'probe': {'enabled': True}}}) == {
        'probe_doc': {'ok': True, 'nested': {'deep': 1}}, 'routines.probe.enabled': True}
    assert control_settings.validate_patch({'probe_doc': {'ok': True}}) == {'probe_doc': {'ok': True}}
    with pytest.raises(ValueError, match='bad doc'):
        control_settings.validate_patch({'probe_doc': {'ok': False}})
    assert control_settings.validate_patch({'probe_doc': None, 'routines': {'probe': {'enabled': False}}})
    assert control_settings.merge_patch({'probe_doc': {'ok': True, 'old': 1}}, {'probe_doc': {'ok': True, 'new': 2}}) == {'probe_doc': {'ok': True, 'new': 2}}
    patch = {'probe_doc': {'ok': True, 'old': 1}, 'routines': {'probe': {'enabled': True, 'burst': 3}}}
    Battles._set_overrides(patch, {'probe_doc': {'ok': True, 'new': 2}, 'routines': {'probe': {'enabled': False}}})
    assert patch == {'probe_doc': {'ok': True, 'new': 2}, 'routines': {'probe': {'enabled': False, 'burst': 3}}}, 'a document replaces the old one; a mapping merges'


def test_a_document_nobody_registered_a_validator_for_is_refused_not_accepted(documented, monkeypatch):
    monkeypatch.delitem(control_settings._DOCUMENT_VALIDATORS, 'probe_doc')
    with pytest.raises(ValueError, match='No validator for control: probe_doc'):
        control_settings.validate_patch({'probe_doc': {'ok': True}})


def test_a_document_is_unknown_without_the_action_that_owns_it():
    with pytest.raises(ValueError, match='Unknown control patch fields'):
        control_settings.validate_patch({'probe_doc': {'ok': True}})


# --------------------------------------------------------------------------- Run now progress
def test_a_result_acknowledges_its_own_record_else_a_record_built_from_what_it_says_else_nothing():
    assert BindingResult('pending').acknowledgement() is None and BindingResult('started').acknowledgement() is None
    assert BindingResult('started', ack={'outcome': 'unknown'}).acknowledgement() == {'outcome': 'unknown'}
    own = {'confirmed': True, 'reason': 'x'}
    assert BindingResult('terminal', outcome='succeeded', ack=own).acknowledgement() == own and BindingResult('terminal', ack=own).acknowledgement() == own
    assert BindingResult('terminal').acknowledgement() is None, 'a terminal result that says nothing is acknowledged with nothing, never invented'
    built = BindingResult('terminal', outcome='succeeded', effect='unconfirmed', reason='ran', attempts=2, evidence=('probe',), refs={'id': '1'}).acknowledgement()
    assert built == dict(outcome='succeeded', effect='unconfirmed', reason='ran', attempts=2, evidence=['probe'], refs={'id': '1'})
    result = BindingResult('terminal', outcome='failed', ack=own)
    result.acknowledgement()['changed'] = True
    assert 'changed' not in result.ack and 'changed' not in own, 'the acknowledgement is a copy'


@pytest.mark.parametrize('kwargs', [dict(state='nearly'), dict(state='terminal', outcome='winning'), dict(state='terminal', outcome='succeeded', effect='confirmed-ish')])
def test_a_result_that_names_a_state_outcome_or_effect_the_contract_does_not_know_is_refused(kwargs):
    with pytest.raises(ValueError):
        BindingResult(**kwargs)


@pytest.mark.parametrize('record,expected', [({'confirmed': True, 'reason': 'balance_increased'}, ('succeeded', 'confirmed', 'balance_increased')),
                                              ({'confirmed': False, 'outcome': 'not_confirmed', 'reason': 'offer_still_visible'}, ('failed', 'unconfirmed', 'offer_still_visible')),
                                              ({'confirmed': False, 'outcome': 'skipped', 'reason': 'offer_moved'}, ('no_op', 'none', 'offer_moved')),
                                              ({'reason': 'x'}, (None, 'unknown', 'x')), (None, (None, 'unknown', None)), ('text', (None, 'unknown', None))])
def test_a_collectors_own_outcome_is_read_as_confirmed_unconfirmed_or_unknown_never_confirmed_by_guessing(record, expected):
    assert outcome_of(record) == expected


class Plain(BindingBase):
    """A binding that has not said how far a request has come."""
    def runnable(self):
        return True

    def variants(self):
        return ('a',)


class Reporting(Plain):
    def progress(self):
        return BindingResult('pending')


def test_a_binding_with_no_progress_of_its_own_has_no_run_now_so_nothing_can_be_acknowledged_from_another_handlers_state():
    assert reports_progress(Plain()) is False and reports_progress(Reporting()) is True
    assert run_now_tokens('probe@one', Plain()) == []
    assert run_now_tokens('probe@one', Reporting()) == ['probe@one', 'probe@one:a']
    assert run_now_tokens('probe@one', BindingBase()) == []


def test_a_binding_that_cannot_run_now_publishes_no_token_even_when_it_reports_progress():
    class NotNow(Reporting):
        def runnable(self):
            return False
    assert run_now_tokens('probe', NotNow()) == []


# ---------------------------------------------------------------- the dispatch asks the binding, whatever the action
@pytest.fixture
def driver(tmp_path, monkeypatch):
    from runtime import logger
    monkeypatch.setattr(logger, 'event', lambda *args, **kwargs: None)
    path = tmp_path / 'operations.db'
    operation, _ = Operations(path).accept('farm', dict(device_key='device', account='account', instance='main', serial='fake'), {})
    extended = CATALOG.copy()
    extended.register(ActionDefinition(
        id='probe', label='Probe', family='gems', hosts=frozenset({'test'}), schedule=StartToStart(),
        fields=(Field('enabled', 'bool'), Field('burst', 'number', 1, 20), Field('saved_only', 'number', 1, 9, live=False)),
        capabilities=Capabilities(live_controls=True, run_now=True), saved=None))
    with use_catalog(extended):
        session = BattleSession(Battles(path), operation['id'], dict(gather={}, shopping=[], shop_interval_sec=90), {})
        yield session, operation


class Recording(Reporting):
    def __init__(self, answers=()):
        self.calls, self.answers = [], list(answers)

    def configure(self, values):
        self.calls.append(dict(values))
        return self.answers.pop(0) if self.answers else True

    def request_now(self, variant=None, *, command_id=None):
        self.calls.append(('run', variant, command_id))
        return True

    def routine(self, *, battle_visible, now, epoch):
        return dict(enabled=True, state='scheduled', next_due_at=epoch, window_ends_at=None)


def host(binding, key='probe@one'):
    return SimpleNamespace(gems=SimpleNamespace(ad_gems=None, orbit=None), tracker=SimpleNamespace(last=1), mission=None, shop=None,
                           action_bindings={key: binding})


def test_a_control_of_a_registered_instance_reaches_its_binding_with_one_field_and_the_frozen_value_for_a_null(driver):
    session, operation = driver
    binding = Recording()
    rs = host(binding)
    rs.gems = SimpleNamespace(ad_gems=Recording(), orbit=Recording())
    session.observe(rs, 5)
    assert 'routines.probe@one.burst' in session.store.get(session.battle['id'])['capabilities']['controls']
    assert 'routines.probe@one.saved_only' not in session.store.get(session.battle['id'])['capabilities']['controls'], 'a saved-only field is not a live control'
    battle = session.store.get(session.battle['id'])
    session.store.request_controls(battle['id'], operation['id'], session.generation, 0, {'routines': {'probe@one': {'burst': 7, 'enabled': False}}}, 'one')
    session.checkpoint(rs, battle_visible=True)
    assert binding.calls == [{'burst': 7}, {'enabled': False}] and session.store.commands(battle['id'])[0]['state'] == 'applied'


def test_a_binding_that_cannot_install_a_value_yet_keeps_the_command_pending_and_is_asked_again(driver):
    session, operation = driver
    binding = Recording(answers=[False, True])
    rs = host(binding)
    rs.gems = SimpleNamespace(ad_gems=Recording(), orbit=Recording())
    session.observe(rs, 5)
    battle = session.store.get(session.battle['id'])
    session.store.request_controls(battle['id'], operation['id'], session.generation, 0, {'routines': {'probe@one': {'burst': 7}}}, 'one')
    session.checkpoint(rs, battle_visible=True)
    pending = session.store.commands(battle['id'])[0]
    assert pending['state'] == 'pending' and pending['applied_paths'] == [] and binding.calls == [{'burst': 7}]
    session.checkpoint(rs, battle_visible=True)
    assert session.store.commands(battle['id'])[0]['state'] == 'applied' and binding.calls == [{'burst': 7}, {'burst': 7}]


def test_a_control_for_a_routine_no_binding_holds_is_refused_and_a_host_cannot_bind_what_the_catalog_lacks(driver):
    session, operation = driver
    rs = host(Recording(), key='nothing@one')
    rs.gems = SimpleNamespace(ad_gems=Recording(), orbit=Recording())
    assert 'nothing@one' not in host_bindings(rs, session) and list(host_bindings(rs, session)) == ['ad_gems', 'floating_gems']
    with pytest.raises(ValueError, match='Runtime gate is unsupported'):
        session._apply_control(rs, 'routines.probe@two.burst', 3)
    with pytest.raises(ValueError, match='Runtime gate is unsupported'):
        session._apply_control(rs, 'shopping_plan', {})


def test_a_binding_registered_under_an_invalid_instance_key_is_ignored(driver):
    session, _ = driver
    for key in ('probe@', 'probe@A', 'probe@a.b', 'probe', 5):
        rs = host(Recording(), key=key)
        rs.gems = SimpleNamespace(ad_gems=None, orbit=None)
        assert key not in host_bindings(rs, session) or key == 'probe'


def test_run_now_is_asked_of_the_binding_with_its_variant_and_command_and_waits_for_a_busy_one(driver):
    session, operation = driver

    class Busy(Recording):
        busy_now = True

        def busy(self):
            return self.busy_now

        def variants(self):
            return ('v1',)
    busy = Busy()
    rs = host(busy)
    rs.gems = SimpleNamespace(ad_gems=Recording(), orbit=Recording())
    session.observe(rs, 5)
    battle = session.store.get(session.battle['id'])
    assert 'probe@one:v1' in battle['capabilities']['actions'] and 'probe@one' in battle['capabilities']['actions']
    command = session.store.request_action(battle['id'], operation['id'], session.generation, 'probe@one:v1', 'go')
    session.checkpoint(rs, battle_visible=True)
    assert busy.calls == [] and session.store.commands(battle['id'])[0]['state'] == 'requested'
    busy.busy_now = False
    session.checkpoint(rs, battle_visible=False)
    assert busy.calls == [], 'a battle that is not visible starts nothing'
    session.checkpoint(rs, battle_visible=True)
    assert busy.calls == [('run', 'v1', command['id'])] and session.manual_for('probe@one') and not session.manual_for('probe')


def test_a_terminal_result_seen_before_a_started_one_is_acknowledged_started_then_completed(driver):
    session, operation = driver

    class Instant(Recording):
        def progress(self):
            return BindingResult('terminal', outcome='succeeded', effect='unconfirmed', reason='ran', attempts=1, evidence=('probe',), refs={'id': 'x'})
    binding = Instant()
    rs = host(binding)
    rs.gems = SimpleNamespace(ad_gems=Recording(), orbit=Recording())
    session.observe(rs, 5)
    battle = session.store.get(session.battle['id'])
    session.store.request_action(battle['id'], operation['id'], session.generation, 'probe@one', 'go')
    session.checkpoint(rs, battle_visible=True)
    command = session.store.commands(battle['id'])[0]
    assert command['state'] == 'completed' and command['evidence'] == dict(outcome='succeeded', effect='unconfirmed', reason='ran', attempts=1,
                                                                            evidence=['probe'], refs={'id': 'x'})
    started = [event['command'] for event in session.store.events(battle['id']) if event['kind'] == 'command_acknowledged' and event['command']['state'] == 'started']
    assert started and started[0]['evidence'] is None and session.manual is None


def test_a_binding_that_has_not_decided_leaves_the_request_in_flight_and_nothing_acknowledged(driver):
    session, operation = driver

    class Undecided(Recording):
        def progress(self):
            return None
    rs = host(Undecided())
    rs.gems = SimpleNamespace(ad_gems=Recording(), orbit=Recording())
    session.observe(rs, 5)
    battle = session.store.get(session.battle['id'])
    session.store.request_action(battle['id'], operation['id'], session.generation, 'probe@one', 'go')
    for _ in range(3):
        session.checkpoint(rs, battle_visible=True)
    assert session.store.commands(battle['id'])[0]['state'] == 'requested' and session.manual is not None


def test_the_documents_a_preset_carries_are_frozen_with_the_battle_even_when_the_accepted_envelope_lacks_them(tmp_path):
    from player.shopping_plan import default_plan
    operation, _ = Operations(tmp_path / 'operations.db').accept('farm', dict(device_key='device', account='account', instance='main', serial='fake'), {})
    plan = default_plan()
    for held in (plan, None):
        preset = dict(gather={}, shopping=[], shop_interval_sec=90, **({} if held is None else dict(shopping_plan=held)))
        envelope = {'preset': preset, 'config': {}, 'controls': {'routines': {}}, 'source': None, 'source_revision': None}
        session = BattleSession(Battles(tmp_path / 'operations.db'), operation['id'], preset, {}, envelope=envelope)
        controls = session.battle['envelope']['controls']
        assert (controls.get('shopping_plan') == held) and ('shopping_plan' in controls) is (held is not None)
        session.finish('test_boundary')


def test_a_collector_whose_request_is_pending_and_whose_window_is_running_is_reported_started():
    class Both:
        def status(self):
            return dict(manual_active=True, manual_pending=True, last_outcome=None)
    rs = SimpleNamespace(gems=SimpleNamespace(ad_gems=Both(), orbit=Both()))
    assert action_bindings.CollectorBinding(rs, 'gems.ad_gems').progress().state == 'started'
    done = SimpleNamespace(status=lambda: dict(manual_active=False, manual_pending=False, last_outcome={'confirmed': True, 'reason': 'x'}))
    rs.gems.ad_gems = done
    result = action_bindings.CollectorBinding(rs, 'gems.ad_gems').progress()
    assert (result.state, result.outcome, result.effect, result.ack) == ('terminal', 'succeeded', 'confirmed', {'confirmed': True, 'reason': 'x'})
    # bound to its action, a confirmed outcome is worded by that action's evidence: a vanished battle offer is not a confirmed effect
    rs.gems.ad_gems = SimpleNamespace(status=lambda: dict(manual_active=False, manual_pending=False, last_outcome={'confirmed': True, 'reason': 'offer_disappeared'}))
    gone = action_bindings.CollectorBinding(rs, 'gems.ad_gems', 'ad_gems').progress()
    assert (gone.outcome, gone.effect, gone.reason) == ('succeeded', 'unconfirmed', 'offer_disappeared')
    rs.gems.ad_gems = SimpleNamespace(status=lambda: dict(manual_active=False, manual_pending=False, last_outcome={'confirmed': True, 'reason': 'balance_increased'}))
    assert action_bindings.CollectorBinding(rs, 'gems.ad_gems', 'ad_gems').progress().effect == 'confirmed'

def test_a_run_now_acknowledgement_does_not_claim_a_confirmed_effect_the_contract_calls_unconfirmed():
    record = {'outcome': 'confirmed', 'confirmed': True, 'reason': 'offer_disappeared', 'attempts': 1}
    rs = SimpleNamespace(gems=SimpleNamespace(ad_gems=SimpleNamespace(status=lambda: dict(manual_active=False, manual_pending=False, last_outcome=record))))
    gone = action_bindings.CollectorBinding(rs, 'gems.ad_gems', 'ad_gems').progress()
    ack = gone.acknowledgement()
    assert (ack['confirmed'], ack['outcome'], ack['reason']) == (False, 'unconfirmed', 'offer_disappeared')
    assert record == {'outcome': 'confirmed', 'confirmed': True, 'reason': 'offer_disappeared', 'attempts': 1}      # the collector's own record is untouched
    paid = dict(record, reason='balance_increased')
    rs.gems.ad_gems = SimpleNamespace(status=lambda: dict(manual_active=False, manual_pending=False, last_outcome=paid))
    assert action_bindings.CollectorBinding(rs, 'gems.ad_gems', 'ad_gems').progress().acknowledgement() == paid
# ---------------------------------------------------------------------------- the saved adapters
def test_every_definition_that_names_a_saved_adapter_has_one_registered_and_a_definition_that_names_none_has_none():
    for definition in CATALOG:
        if definition.saved is None:
            assert saved_controls.adapter_for(definition) is None
        else:
            assert saved_controls.adapter_for(definition) is saved_controls.SAVED[definition.saved]
    unregistered = ActionDefinition(id='lonely', label='Lonely', family='gems', hosts=frozenset(), schedule=StartToStart(), saved='no_such_adapter')
    with pytest.raises(ValueError, match="No saved-control adapter named 'no_such_adapter' for lonely"):
        saved_controls.adapter_for(unregistered)


def test_a_definition_is_saved_for_a_kind_only_when_it_names_an_adapter_and_lists_the_kind():
    free = ActionDefinition(id='a', label='A', family='gems', hosts=frozenset(), schedule=StartToStart(), saved='x')
    narrow = ActionDefinition(id='b', label='B', family='gems', hosts=frozenset(), schedule=StartToStart(), saved='x', saved_kinds=frozenset({'coin'}))
    live_only = ActionDefinition(id='c', label='C', family='gems', hosts=frozenset(), schedule=StartToStart(), saved_kinds=frozenset({'coin'}))
    assert [free.saved_for(kind) for kind in ('coin', 'shard', None)] == [True, True, True]
    assert [narrow.saved_for(kind) for kind in ('coin', 'shard', None)] == [True, False, False]
    assert [live_only.saved_for(kind) for kind in ('coin', 'shard')] == [False, False]


def test_the_saved_capabilities_come_from_each_definitions_saved_fields_kinds_and_documents():
    extended = CATALOG.copy()
    extended.register(ActionDefinition(id='probe', label='Probe', family='gems', hosts=frozenset(), schedule=StartToStart(), saved='gather_policy',
                                       fields=(Field('enabled', 'bool'), Field('live_only', 'number', 1, 2, saved=False), Field('both', 'number', 1, 2)),
                                       saved_kinds=frozenset({'shard'})))
    with use_catalog(extended):
        coin = saved_controls.capabilities({'kind': 'coin'})['controls']
        shard = saved_controls.capabilities({'kind': 'shard'})['controls']
    assert 'routines.probe.both' not in coin and 'routines.rewards.enabled' in coin and coin[-1] == 'shopping_plan'
    assert 'routines.probe.both' in shard and 'routines.probe.enabled' in shard and 'routines.probe.live_only' not in shard
    assert not any(path.startswith('routines.shopping') or path.startswith('routines.rewards') for path in shard) and shard[-1] != 'shopping_plan'
    assert [path for path in coin if path.startswith('routines.ad_gems')] == ['routines.ad_gems.enabled', 'routines.ad_gems.interval_sec', 'routines.ad_gems.window_sec']


def test_resolve_apply_and_write_visit_every_adapter_in_catalog_order_and_only_those(monkeypatch):
    calls = []

    class Spy(saved_controls.SavedAdapter):
        def read(self, definition, preset, config, automation):
            calls.append(('read', definition.id))
            return {definition.id: {'enabled': True}}

        def apply(self, definition, preset, by_key):
            calls.append(('apply', definition.id, sorted(by_key)))

        def prepare(self, ctx, definition, by_key, docs):
            calls.append(('prepare', definition.id))

        def write(self, ctx, definition, by_key, docs):
            calls.append(('write', definition.id, sorted(by_key), sorted(docs)))

        def finish(self, ctx):
            calls.append(('finish',))
    monkeypatch.setitem(saved_controls.SAVED, 'gather_policy', Spy())
    monkeypatch.setitem(saved_controls.SAVED, 'shopping_plan', Spy())
    saved_controls.resolve({}, {})
    assert [call for call in calls if call[0] == 'read'] == [('read', 'ad_gems'), ('read', 'floating_gems'), ('read', 'shopping')]
    calls.clear()
    saved_controls.apply_to_preset({}, {'routines': {'floating_gems': {'enabled': False}}})
    assert calls == [('apply', 'ad_gems', []), ('apply', 'floating_gems', ['floating_gems']), ('apply', 'shopping', [])]
    calls.clear()
    context = saved_controls.WriteContext({}, 'farm', {})
    saved_controls.write(context, {'routines': {'shopping': {'enabled': True}}, 'shopping_plan': {'x': 1}})
    assert calls == [('prepare', 'shopping'), ('write', 'shopping', ['shopping'], ['shopping_plan']), ('finish',)]
    assert context.bp == {'automation': {'shopping': True}}
    calls.clear()
    saved_controls.write(context, {'routines': {'ad_gems': {'enabled': False}, 'floating_gems': {'enabled': False}}})
    assert [call[0] for call in calls] == ['prepare', 'prepare', 'write', 'write', 'finish'] and calls[-1] == ('finish',), 'a shared adapter finishes once'


def test_a_gate_is_stored_on_the_blueprint_without_an_adapter_and_a_saved_only_catalog_action_reads_its_values_from_its_adapter():
    context = saved_controls.WriteContext({}, 'farm', {})
    saved_controls.write(context, {'routines': {'rewards': {'enabled': False}, 'repeat': {'enabled': None}}})
    assert context.bp == {'automation': {'rewards': False}} and context.gather is None


def test_the_registry_resolves_an_unregistered_name_loudly_and_never_by_skipping():
    extended = CATALOG.copy()
    extended.register(ActionDefinition(id='probe', label='Probe', family='gems', hosts=frozenset(), schedule=StartToStart(), saved='unregistered',
                                       fields=(Field('enabled', 'bool'),)))
    with use_catalog(extended):
        with pytest.raises(ValueError, match='No saved-control adapter'):
            saved_controls.resolve({}, {})
        with pytest.raises(ValueError, match='No saved-control adapter'):
            saved_controls.capabilities({'kind': 'coin'})


def test_the_active_catalog_is_restored_after_a_block_that_raises():
    extended = CATALOG.copy()
    with pytest.raises(RuntimeError):
        with use_catalog(extended):
            assert catalog() is extended
            raise RuntimeError('boom')
    assert catalog() is CATALOG
    with use_catalog(extended):
        with use_catalog(CATALOG):
            assert catalog() is CATALOG
        assert catalog() is extended
