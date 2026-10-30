"""Golden answers of the fleet timetable and its five consumers on the unchanged code (roadmap item 3, package T3-0).

scheduling/fleets.py for a tier from the blueprint and for the legacy fallback when the run names no tier, then each consumer of
orchestrator.marks() run for real with marks() NOT replaced. A later package that makes an unknown tier mean 'no timetable' must keep every
tiered answer here and change only the no-tier ones, deliberately."""
import ast

import pytest

import takeover_golden as golden
from takeover_golden_marks import MARKS

NAME = 'fleet_marks'
CONSUMERS = ['_fast_wall_watch', '_trigger_fires', 'cl_window', 'main', 'watch_frame']


def test_the_recorded_document_holds_exactly_these_scenarios():
    golden.assert_registry(NAME, MARKS)


@pytest.mark.parametrize('scenario', sorted(MARKS))
def test_the_timetable_answers_what_was_recorded(scenario, tmp_path, monkeypatch):
    golden.assert_recorded(NAME, scenario, MARKS[scenario](tmp_path, monkeypatch))


def test_marks_has_exactly_the_five_consumers_the_scenarios_cover():
    """Found by reading the source: every call of marks() in orchestrator.py sits in one of these functions. A sixth consumer must be added
    to the scenarios (and here) before it can land."""
    tree = ast.parse((golden.BACKEND / 'orchestrator.py').read_text(encoding='utf-8'))
    found = set()
    for function in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)):
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'marks':
                found.add(function.name)
    assert sorted(found - {'marks'}) == CONSUMERS


def test_an_unknown_tier_today_falls_back_to_the_legacy_schedule_or_nothing():
    recorded = golden.recorded(NAME)['orchestrator_marks']
    assert recorded['no_tier_with_legacy_schedule']['head'] == [2495, 3495, 4495, 5495] and recorded['no_tier_with_legacy_schedule']['run_tier'] is None
    assert recorded['no_tier_without_legacy_schedule']['count'] == 0
