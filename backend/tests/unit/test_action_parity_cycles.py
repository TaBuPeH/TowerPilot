"""Rows C-7 and the declaration half of C-9 (item 7, r18d): the boundaries the code already has are DECLARED as event-only actions, and nothing about them moves.

The death boundary (the runner leaves or restarts where the run ends) and the wave targets (the max-wave surrender, the Smart Missiles ride) have no time at all. This file pins what
the declarations say against the source they describe, and that no caller records them yet (no history, no owner change, no timer): a package that later gives them an
execution lifecycle changes these tests on purpose. Full C-9 (the 15 s scan and the 300 s decision timeout as substeps of the owned cycle) stays with r18g.
"""

import paths as test_paths
import ast

import pytest

from towerpilot_workflows.action_contract import CATALOG, Decision, EventOnly

BACKEND = test_paths.BACKEND
BOUNDARIES = {'repeat_after_death': ('death_boundary', 'orchestrator'), 'max_wave': ('wave_target', 'orchestrator'), 'sm_ride': ('wave_target', 'quest_sm')}
NEW_IDS = ('tournament_entry', 'tournament_ticket', 'in_run_cards', 'surrender', 'setup_capture', 'max_wave', 'repeat_after_death', 'sm_ride', 'tournament_day_marker', 'perk_ban_cache')


def parse(rel):
    path = BACKEND / rel
    return path.read_text(encoding='utf-8'), ast.parse(path.read_text(encoding='utf-8'))


def function(tree, name):
    return next(node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)


def names(node):
    return {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)} | {sub.attr for sub in ast.walk(node) if isinstance(sub, ast.Attribute)}


# ------------------------------------------------------------------------------ the declarations
@pytest.mark.parametrize('ident', sorted(BOUNDARIES))
def test_each_boundary_is_an_event_only_action_with_no_time_and_no_run_now(ident):
    event, host = BOUNDARIES[ident]
    definition = CATALOG.get(ident)
    assert definition.schedule == EventOnly(event) and definition.schedule.basis == 'event' and definition.hosts == frozenset({host})
    assert definition.capabilities.run_now is False and not definition.fields and definition.consumes is None
    waiting, state = definition.schedule.evaluate(0.0, {}, {})
    assert waiting == Decision('wait', reason=event, basis='event') and waiting.next_due_at is None and waiting.eligible_at is None, 'no time: unknown is None, never zero'
    assert state == {}
    due, _ = definition.schedule.evaluate(0.0, EventOnly.raised({}), {})
    assert (due.kind, due.reason, due.next_due_at) == ('due', event, None)


def test_the_max_wave_surrender_is_also_restricted_and_the_other_two_boundaries_are_not():
    assert CATALOG.get('max_wave').restricted and CATALOG.get('repeat_after_death').restricted is None and CATALOG.get('sm_ride').restricted is None


# ---------------------------------------------------------------- the source the declarations describe
def test_the_death_boundary_is_where_the_runner_leaves_or_restarts_a_run():
    flag_source, flag_tree = parse('scheduling/runflag.py')
    assert 'death handler' in ast.get_docstring(flag_tree) and {'request', 'requested', 'clear'} <= {n.name for n in flag_tree.body if isinstance(n, ast.FunctionDef)}
    source, tree = parse('orchestrator.py')
    after = function(tree, '_after_surrender')
    assert 'repeat_enabled' in names(after) and 'prepare_next' in names(after) and 'finish' in names(after), 'repeat off leaves; on prepares the next run'
    restart = function(tree, 'restart_from_home')
    assert ast.get_docstring(restart).startswith('Death -> HOME -> set the tier -> BATTLE')


def test_the_max_wave_surrender_is_one_attempt_a_comparison_with_a_limit_and_no_timer():
    source, tree = parse('orchestrator.py')
    function_ = function(tree, 'max_wave_reached')
    compares = [ast.get_source_segment(source, node) for node in ast.walk(function_) if isinstance(node, ast.Compare)]
    assert 'wave < limit' in compares, compares
    assigns = [node.lineno for node in ast.walk(function_) if isinstance(node, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == 'max_wave_done' for t in node.targets)]
    calls = [node.lineno for node in ast.walk(function_) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'abandon_run']
    assert assigns and calls and min(assigns) < min(calls), 'the attempt is marked spent BEFORE the surrender is tried'
    assert not {'time', 'monotonic', 'sleep', 'pause'} & names(function_), 'it is a wave comparison, not a timer'
    assert 'request' in names(function_) and 'runflag' in names(function_), 'a failed surrender falls back to the stop flag, never a second try'


def test_the_sm_ride_is_a_wave_target_owned_by_the_quest_run_and_declares_no_timer_of_its_own():
    source, tree = parse('flows/quest_sm.py')
    ride = function(tree, 'ride_to')
    compares = [ast.get_source_segment(source, node) for node in ast.walk(ride) if isinstance(node, ast.Compare)]
    assert any('>= target' in text for text in compares), compares
    calls = {ast.get_source_segment(source, node.func) for node in ast.walk(ride) if isinstance(node, ast.Call)}
    assert {'run.check', 'run.observe', 'run.pause', 'shard.cancel_sprint'} <= calls, 'the QuestRun stays the owner of every observation and wait'
    assert not {'time', 'monotonic', 'sleep'} & names(ride)
    assert 'SCAN_EVERY_SEC' not in names(ride) and 'DECISION_TIMEOUT_SEC' not in names(ride), 'the scan cadence and the decision timeout are not part of the ride'


# ---------------------------------------------------------- nobody records the new entries yet: no history, no owner change
def test_no_production_code_records_an_execution_of_any_new_entry():
    recording = {'recorder', 'begin', 'check', 'record', 'opened', 'closed', 'finish'}
    found = []
    for path in BACKEND.rglob('*.py'):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith(('tests/', 'logs/')) or rel == 'towerpilot_workflows/action_contract.py':
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in recording and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and first.value in NEW_IDS:
                    found.append((rel, node.lineno, first.value))
    assert not found, found


def test_the_new_entries_are_named_nowhere_else_in_production_code_as_an_action_id():
    """Only `sm_ride` and `max_wave` are strings the code already used (an event name, a preset key); the rest must appear in the contract alone."""
    unique = ('tournament_entry', 'tournament_ticket', 'in_run_cards', 'setup_capture', 'repeat_after_death', 'tournament_day_marker', 'perk_ban_cache')
    found = []
    for path in BACKEND.rglob('*.py'):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith(('tests/', 'logs/')) or rel == 'towerpilot_workflows/action_contract.py':
            continue
        text = path.read_text(encoding='utf-8')
        found += [(rel, name) for name in unique if f"'{name}'" in text or f'"{name}"' in text]
    assert not found, found
