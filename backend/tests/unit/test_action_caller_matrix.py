"""Who may do what, for the rows of item 7 migrated so far (A3a: R-1 to R-7). Supporting evidence for the behavioural tests of each row, not a substitute for them:
it fails when a legacy producer comes back or a second copy of a due calculation appears. Later batches extend it with their own rows."""

import paths as test_paths
import ast
from pathlib import Path

BACKEND = test_paths.BACKEND


def production_files():
    files = [path for path in BACKEND.rglob('*.py') if not {'tests', 'logs'} & set(path.relative_to(BACKEND).parts)]
    assert BACKEND / 'orchestrator.py' in files
    assert test_paths.DASHBOARD in files      # the dashboard is a production file of the backend walk
    return files


def parse(path):
    return ast.parse(path.read_text(encoding='utf-8'))


def function(path, name):
    return next(node for node in ast.walk(parse(path)) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)


def numbers(node):
    return {sub.value for sub in ast.walk(node) if isinstance(sub, ast.Constant) and type(sub.value) in (int, float) and not isinstance(sub.value, bool)}


# --------------------------------------------------------------- R-1, R-4, R-6: the producers that are gone
GONE_NAMES = {'store_due', 'store_claimed', 'store_gems_due', 'free_gems_due', 'free_gems_mark_claimed', 'AD_GEMS_DAILY_CAP', 'get_utc_today', 'bump_utc_today', '_utc_today'}
GONE_ATTRIBUTES = {'check_due', 'checked'}


def test_the_legacy_reward_producers_and_the_obsolete_cap_helpers_exist_nowhere_in_production_code():
    found = []
    for path in production_files():
        for node in ast.walk(parse(path)):
            names = set()
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.alias):
                names.add(node.name)
            for name in names & GONE_NAMES:
                found.append((str(path.relative_to(BACKEND)), name))
            if isinstance(node, ast.Attribute) and node.attr in GONE_ATTRIBUTES and isinstance(node.value, ast.Name) and node.value.id == 'global_rewards':
                found.append((str(path.relative_to(BACKEND)), node.attr))
            if isinstance(node, ast.FunctionDef) and node.name in GONE_ATTRIBUTES and path.name == 'global_rewards.py':
                found.append((str(path.relative_to(BACKEND)), node.name))
    assert not found, found


def test_the_reward_upkeep_has_no_due_arithmetic_of_its_own():
    """The 300 s check, the 1200 s recent window, the 1500-2400 s repeat delay, the 300 s retry and the 01:00 UTC opening are the policies' numbers (towerpilot_workflows/
    action_contract.py). The functions that drive them name none of them."""
    orchestrator = BACKEND / 'orchestrator.py'
    own = {300, 1200, 1500, 2400, 3600, 600}
    for name in ('advance_rewards', 'step_reward_mission', 'store_gems_mark_claimed', 'visit_schedule', 'store_follow_up_schedule'):
        assert not numbers(function(orchestrator, name)) & own, (name, numbers(function(orchestrator, name)) & own)
    rewards = BACKEND / 'scheduling' / 'global_rewards.py'
    assert not numbers(parse(rewards)) & own, numbers(parse(rewards)) & own


def test_the_chore_dispatcher_reads_no_calendar_of_its_own():
    chores = BACKEND / 'scheduling' / 'chores.py'
    tree = parse(chores)
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr == 'hour'], 'the floor hour is the policy\'s'
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr == 'flag_today'], 'the day flag is read through the policy'
    assert 'START_HOUR = action_contract.CHORE_SCHEDULE.floor_hour' in chores.read_text(encoding='utf-8')


# ------------------------------------------------------------------- the writers
def test_the_daily_state_is_written_only_by_the_modules_that_own_a_calendar_or_a_named_marker():
    mutators = {'set_raw', 'mark_today', 'set_today', 'increment_today', 'update', 'clear'}
    writers = set()
    for path in production_files():
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.Attribute) and node.attr in mutators and isinstance(node.value, ast.Name) and node.value.id == 'daystate':
                writers.add(path.relative_to(BACKEND).as_posix())
    assert writers == {'scheduling/chores.py', 'scheduling/global_rewards.py', 'scheduling/combo.py', 'flows/shard.py', 'tools/tier_ladder.py', 'interactions/perks.py'}, writers


def test_execution_rows_are_inserted_only_by_the_ledger_module():
    inserting = [path.relative_to(BACKEND).as_posix() for path in production_files() if 'INSERT INTO action_executions' in path.read_text(encoding='utf-8')]
    assert inserting == ['towerpilot_workflows/actions.py']
