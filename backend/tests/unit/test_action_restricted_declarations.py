"""Row 20 / C-12 of item 7 (r18d): the restricted and retired actions are NAMED in the catalog, and nothing can offer them a Run now.

Direct service tests: a real `BattleSession` over a temporary operations ledger, the real bindings module and the real battle store's admission. No Flask client, no HTTP, no
POST, no port, no device, no input. A binding that claims it can run is the attack in every test: the definition decides what a person may start, the binding only how.
"""

import dataclasses
from types import SimpleNamespace

import pytest

from flows.shard import GemWatch
from interactions.shopper import Shopper
from runtime.action_bindings import BindingBase, host_bindings, run_now_tokens
from runtime.action_session import ActionSession
from runtime.battle_session import BattleSession
from towerpilot_workflows import control_settings
from towerpilot_workflows.action_contract import (CATALOG, ActionDefinition, BindingResult, Capabilities, Catalog, EventOnly, FakeClock, Field, StartToStart, use_catalog)
from towerpilot_workflows.battles import Battles
from towerpilot_workflows.operations import Operations

RESTRICTED = ('tournament_entry', 'tournament_ticket', 'in_run_cards', 'surrender', 'setup_capture', 'max_wave')
EVENT_ONLY = ('repeat_after_death', 'sm_ride')
CONSUMERS = ('tournament_day_marker', 'perk_ban_cache')
DECLARED = RESTRICTED + EVENT_ONLY + CONSUMERS


class Claims(BindingBase):
    """A malicious binding: it says it can run, has variants, and reports progress."""

    def runnable(self):
        return True

    def variants(self):
        return ('v', 'w')

    def request_now(self, variant=None, *, command_id=None):
        return True

    def progress(self):
        return BindingResult('terminal', outcome='succeeded', effect='confirmed')

    def configure(self, values):
        return True

    def routine(self, *, battle_visible, now, epoch):
        return dict(enabled=True, state='scheduled', next_due_at=None, window_ends_at=None)


def make_driver(tmp_path, monkeypatch, bindings=None):
    from runtime import logger
    monkeypatch.setattr(logger, 'event', lambda *args, **kwargs: None)
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / 'operations.db'
    context = dict(device_key='device', account='account', instance='main', serial='fake')
    operation, _ = Operations(path).accept('farm', context, {})
    body = dict(gather={'ad_gems': True, 'gem_orbit': {'enabled': False}}, shopping=[], shop_interval_sec=90)
    session = BattleSession(Battles(path), operation['id'], body, {})
    rs = SimpleNamespace(gems=GemWatch(body['gather']), shop=Shopper(body), mission=SimpleNamespace(active=False), shopping_enabled=False, rewards_enabled=True,
                         repeat_enabled=True, tracker=SimpleNamespace(last=123), last_shop=0)
    if bindings is not None:
        rs.action_bindings = bindings
    session.observe(rs, 123)
    return session, rs, operation


# ------------------------------------------------------------------------------ the entries
@pytest.mark.parametrize('ident', RESTRICTED)
def test_every_restricted_or_retired_action_is_named_with_a_reason_and_no_way_to_run_it(ident):
    definition = CATALOG.get(ident)
    assert isinstance(definition.restricted, str) and definition.restricted.strip(), 'a restricted action says why'
    assert definition.capabilities == Capabilities() and definition.capabilities.run_now is False
    assert not definition.fields and not definition.documents and definition.saved is None and not definition.capabilities.live_controls
    assert isinstance(definition.schedule, EventOnly), 'it has no time: it happens when its event does'
    assert definition.hosts <= {'orchestrator'}, 'no driver records it yet; max_wave is the one the orchestrator will'


def test_the_row_20_actions_the_spec_names_are_all_present():
    """Tournament entry and ticket, in-run card actions (retired), surrender, setup capture (spec 2.1 row 20); the max-wave surrender is the same kind."""
    assert set(RESTRICTED) <= {item.id for item in CATALOG if item.restricted}
    assert {item.id for item in CATALOG if item.restricted} == set(RESTRICTED), 'no other action is restricted by this package'
    assert 'retired' in CATALOG.get('in_run_cards').restricted


def test_the_retired_tournament_cards_entry_matches_the_runtime_it_describes():
    """The runtime refuses in_run_actions at every call; the entry says so (source-code-first: the docstring and the log line)."""
    import ast
    import paths as test_paths
    source = (test_paths.BACKEND / 'orchestrator.py').read_text(encoding='utf-8')
    function = next(node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.FunctionDef) and node.name == 'run_in_run_actions')
    assert 'DISABLED AT RUNTIME' in ast.get_docstring(function) and 'in_run_actions is disabled at runtime' in ast.get_source_segment(source, function)
    assert [node.value for node in ast.walk(function) if isinstance(node, ast.Return)] and all(
        isinstance(node.value, ast.Constant) and node.value.value is False for node in ast.walk(function) if isinstance(node, ast.Return))


@pytest.mark.parametrize('ident', DECLARED)
def test_a_declared_entry_publishes_no_control_no_field_and_no_run_now(ident):
    definition = CATALOG.get(ident)
    assert definition.id not in control_settings.FIELDS and definition.descriptor()['fields'] == []
    assert definition.capabilities.run_now is False and definition.capabilities.live_controls is False
    for key in (ident, f'{ident}@main'):
        assert run_now_tokens(key, Claims()) == [], 'a binding that claims it can run is not enough'


# ------------------------------------------------------------------ a definition can never grant Run now
@pytest.mark.parametrize('capabilities', [Capabilities(run_now=True), Capabilities(live_controls=True, pause=True, run_now=True, variants=True)])
def test_a_restricted_definition_cannot_be_built_with_run_now(capabilities):
    with pytest.raises(ValueError, match='restricted action cannot be offered Run now'):
        ActionDefinition(id='x', label='X', family='gems', hosts=frozenset(), schedule=StartToStart(), capabilities=capabilities, restricted='a reason')


@pytest.mark.parametrize('reason', ['', '   ', '\n', 0, False])
def test_a_restricted_flag_without_a_reason_is_refused_not_read_as_unrestricted(reason):
    with pytest.raises(ValueError, match='restricted action needs a reason'):
        ActionDefinition(id='x', label='X', family='gems', hosts=frozenset(), schedule=StartToStart(), restricted=reason)


def test_the_refusal_leaves_ordinary_definitions_alone():
    refused = ActionDefinition(id='r', label='R', family='gems', hosts=frozenset(), schedule=StartToStart(), capabilities=Capabilities(run_now='not_supported'), restricted='why')
    plain = ActionDefinition(id='p', label='P', family='gems', hosts=frozenset(), schedule=StartToStart(), capabilities=Capabilities(run_now=True))
    unrestricted_with_reasoncode = ActionDefinition(id='q', label='Q', family='gems', hosts=frozenset(), schedule=StartToStart(), capabilities=Capabilities(run_now='not_supported'))
    assert refused.restricted == 'why' and plain.restricted is None and unrestricted_with_reasoncode.capabilities.run_now == 'not_supported'


def test_the_token_check_refuses_a_restricted_definition_even_when_the_constructor_was_bypassed():
    """The second line of defence: a definition altered after construction (or built by a path that skipped `__post_init__`) still publishes nothing."""
    tampered = dataclasses.replace(CATALOG.get('surrender'))
    object.__setattr__(tampered, 'capabilities', Capabilities(run_now=True))
    extended = Catalog(tampered if item.id == 'surrender' else item for item in CATALOG)
    with use_catalog(extended):
        assert extended.get('surrender').capabilities.run_now is True and extended.get('surrender').restricted
        assert run_now_tokens('surrender', Claims()) == [] and run_now_tokens('surrender@main:v', Claims()) == []


def test_the_restricted_flag_is_what_blocks_it_not_an_accident_of_the_other_fields():
    """The control for the test above: the same definition WITHOUT the flag and WITH Run now is offered, so the refusal is the flag's."""
    permitted = dataclasses.replace(CATALOG.get('surrender'), restricted=None, capabilities=Capabilities(run_now=True))
    extended = Catalog(permitted if item.id == 'surrender' else item for item in CATALOG)
    with use_catalog(extended):
        assert run_now_tokens('surrender', Claims()) == ['surrender', 'surrender:v', 'surrender:w']


# -------------------------------------------------------- the real driver and the real admission, malicious bindings registered
def test_restricted_actions_offer_no_run_now_even_with_a_runnable_binding_registered_for_every_declared_action(tmp_path, monkeypatch):
    session, rs, operation = make_driver(tmp_path / 'attacked', monkeypatch, {ident: Claims() for ident in DECLARED})
    plain_session, plain_rs, _ = make_driver(tmp_path / 'plain', monkeypatch)
    bindings = session.bindings(rs)
    assert set(DECLARED) <= set(bindings), 'the host accepted the bindings: the catalog knows the ids now, so the attack really reaches Run now'
    assert set(DECLARED).isdisjoint(plain_session.bindings(plain_rs))
    published, plain = session.capabilities(rs), plain_session.capabilities(plain_rs)
    assert published == plain, 'registering them changes neither a control nor a Run now token'
    assert [token for token in published['actions'] if token.partition(':')[0] in DECLARED] == []
    assert session.store.get(session.battle['id'])['capabilities'] == published
    battle_id = session.battle['id']
    for ident in DECLARED:
        for action in (ident, f'{ident}:v'):
            with pytest.raises(ValueError, match='Unsupported manual action'):
                session.store.request_action(battle_id, operation['id'], session.generation, action, f'try-{action}')
        with pytest.raises(ValueError):
            session.store.request_controls(battle_id, operation['id'], session.generation, 0, {'routines': {ident: {'enabled': True}}}, f'control-{ident}')
    assert session.store.commands(battle_id) == []


def test_the_same_driver_does_offer_an_unrestricted_probe_so_the_refusals_above_are_not_vacuous(tmp_path, monkeypatch):
    extended = CATALOG.copy()
    extended.register(ActionDefinition(id='allowed_probe', label='Allowed', family='gems', hosts=frozenset({'orchestrator'}), schedule=StartToStart(),
                                       fields=(Field('enabled', 'bool'),), capabilities=Capabilities(live_controls=True, run_now=True)))
    with use_catalog(extended):
        session, rs, operation = make_driver(tmp_path, monkeypatch, {'allowed_probe': Claims(), 'surrender': Claims()})
        actions = session.capabilities(rs)['actions']
        assert 'allowed_probe' in actions and 'allowed_probe:v' in actions and not [a for a in actions if a.startswith('surrender')]
        command = session.store.request_action(session.battle['id'], operation['id'], session.generation, 'allowed_probe', 'ok')
        assert command['payload'] == {'action': 'allowed_probe'}


# ------------------------------------------------------------ ordinary control validation and the gates keep their meaning
def test_ordinary_control_validation_is_unchanged_by_the_declarations():
    assert list(control_settings.FIELDS) == ['ad_gems', 'floating_gems', 'shopping', 'rewards', 'abilities', 'rescue', 'repeat']
    for ident in DECLARED + ('repeat_after_death',):
        for value in (True, False, 1):
            with pytest.raises(ValueError, match='Unknown routine or invalid mapping'):
                control_settings.validate_patch({'routines': {ident: {'enabled': value}}})
    assert control_settings.validate_patch({'routines': {'repeat': {'enabled': False}}}) == {'routines': {'repeat': {'enabled': False}}}
    with pytest.raises(ValueError, match='Invalid value'):
        control_settings.validate_patch({'routines': {'repeat': {'enabled': 1}}})


def test_the_repeat_gate_is_not_a_catalog_action_so_nothing_is_projected_or_re_ordered_for_it(tmp_path):
    """`repeat` (the routine, its enable flag) stays a literal gate: an entry of that name would be projected into runtime.actions (a new Dashboard row)."""
    assert 'repeat' not in CATALOG and 'repeat_after_death' in CATALOG
    rs = SimpleNamespace(repeat_enabled=True)
    from runtime.action_bindings import RepeatGate
    routine = RepeatGate(rs).routine(battle_visible=True, now=1.0, epoch=2.0)
    assert routine == dict(enabled=True, state='boundary_wait', next_due_at=None, window_ends_at=None)
    session = ActionSession(tmp_path / 'ledger.sqlite3', context=dict(device_key='d', account='a', instance='main', profile=None), driver='orchestrator', clock=FakeClock())
    gates = {'rewards': dict(routine, state='watching'), 'abilities': dict(routine, state='watching'), 'rescue': dict(routine, state='watching'), 'repeat': routine}
    assert session.project(gates, observed_at=1.0, control_revision=0)['items'] == []
    assert run_now_tokens('repeat', RepeatGate(rs)) == []
