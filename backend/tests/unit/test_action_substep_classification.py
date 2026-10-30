"""Rows C-6 (row 15), C-13 and C-14 of item 7 (r18d): every substep throttle of the orchestrator is classified, and the two non-calendar daily-state consumers are pinned to the
serialized owner (`scheduling/daystate.py`).

Row 15 of the specification: a throttle that makes input reach the screen is a parent-owned substep (its parent row owns its timing); one that never does is infrastructure; none is
left unnamed. The discovery reads the orchestrator's source (every `now - rs.<name> <op> <number>` comparison), so a throttle added later fails here until someone classifies it.
Nothing is wrapped and no timer moves.
"""

import paths as test_paths
import ast
import datetime
import json

import pytest

from scheduling import daystate
from towerpilot_workflows.action_contract import CATALOG, EventOnly

BACKEND = test_paths.BACKEND

# name -> (seconds, comparison, function that holds it, kind, what owns its timing, where to look for the input, proof)
#   scope 'function': the whole function; 'if': the innermost `if` whose test holds the comparison.
#   proof 'taps': `act.tap` is called in the scope; 'owner': the scope calls the owner of the taps; 'none': no `act.` call in the scope.
SUBSTEPS = {
    'sprint_end_try': dict(seconds=30, op='Lt', function='end_intro_sprint', kind='substep', parent='the rule and rescue flows that end the intro sprint (C-1, C-3)', scope='function', proof='taps'),
    'off_battle_since': dict(seconds=45, op='LtE', function='recover_reward_menu', kind='substep', parent='the reward flows (R-1, R-2): the precondition of recover_try', scope='function', proof='owner'),
    'recover_try': dict(seconds=10, op='LtE', function='recover_reward_menu', kind='substep', parent='the reward flows (R-1, R-2)', scope='function', proof='owner'),
    'menu_open_try': dict(seconds=8, op='Gt', function='main', kind='substep', parent='the reward visits (R-1, R-2): the side menu they need open', scope='if', proof='taps'),
    'overlay_sweep_at': dict(seconds=60, op='Gt', function='main', kind='infrastructure', parent=None, scope='if', proof='none'),
    'end_round_learn_at': dict(seconds=10, op='Gt', function='main', kind='infrastructure', parent=None, scope='if', proof='none'),
    'wave_seen_at': dict(seconds=300, op='Gt', function='watch_frame', kind='infrastructure', parent=None, scope='if', proof='none'),
}


def tree_of(rel):
    return ast.parse((BACKEND / rel).read_text(encoding='utf-8'))


def discover():
    tree = tree_of('orchestrator.py')
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    found = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.left, ast.BinOp) and isinstance(node.left.op, ast.Sub)):
            continue
        slot, limit = node.left.right, node.comparators[0]
        if not (isinstance(slot, ast.Attribute) and isinstance(slot.value, ast.Name) and slot.value.id == 'rs'
                and isinstance(limit, ast.Constant) and type(limit.value) in (int, float)):
            continue
        scopes = {}
        up = node
        while up in parents:
            up = parents[up]
            if isinstance(up, ast.If) and 'if' not in scopes and any(sub is node for sub in ast.walk(up.test)):
                scopes['if'] = up
            if isinstance(up, ast.FunctionDef):
                scopes['function'] = up
                break
        found[slot.attr] = dict(seconds=limit.value, op=type(node.ops[0]).__name__, function=scopes['function'].name, scopes=scopes)
    return found


def calls(node):
    out = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            parts, target = [], sub.func
            while isinstance(target, ast.Attribute):
                parts.append(target.attr)
                target = target.value
            if isinstance(target, ast.Name):
                out.add('.'.join([target.id, *reversed(parts)]))
    return out


# ------------------------------------------------------------------------------------- row 15
def test_every_time_throttle_of_the_orchestrator_is_classified_and_none_is_left_unnamed():
    found = discover()
    assert set(found) == set(SUBSTEPS), ('unclassified', sorted(set(found) - set(SUBSTEPS)), 'gone', sorted(set(SUBSTEPS) - set(found)))
    for name, expected in SUBSTEPS.items():
        assert (found[name]['seconds'], found[name]['op'], found[name]['function']) == (expected['seconds'], expected['op'], expected['function']), name


def test_the_classified_throttles_are_not_actions_of_the_catalog_and_the_product_timers_are_not_among_them():
    assert not set(SUBSTEPS) & {definition.id for definition in CATALOG}
    product_timers = {'last_fire', 'fleet_try_at', 'rule_next', 'dm_always_try_at', 'uw_next_try', 'free_gems_try_at', 'last_shop', 'quest_due', 'last_visit'}
    assert not product_timers & set(SUBSTEPS), 'those belong to rows C-1 to C-5 and R-1 to R-4, not to row 15'


@pytest.mark.parametrize('name', sorted(SUBSTEPS))
def test_a_throttle_is_a_substep_when_input_reaches_the_screen_and_infrastructure_when_it_never_does(name):
    expected, found = SUBSTEPS[name], discover()[name]
    scope = found['scopes'][expected['scope']]
    used = calls(scope)
    acts = {call for call in used if call.startswith('act.')}
    if expected['proof'] == 'taps':
        assert 'act.tap' in acts, (name, acts)
    elif expected['proof'] == 'owner':
        assert 'rs.mission.recover' in used, (name, used)
        missions = tree_of('interactions/missions.py')
        klass = next(node for node in missions.body if isinstance(node, ast.ClassDef) and node.name == 'Mission')
        assert 'recover' in {node.name for node in klass.body if isinstance(node, ast.FunctionDef)} and 'act.tap' in calls(missions), 'the recovery taps are the mission flow\'s own'
    else:
        assert not acts, (name, acts)
    assert (expected['kind'] == 'substep') == (expected['proof'] in ('taps', 'owner')), 'kind follows the proof'
    assert (expected['parent'] is None) == (expected['kind'] == 'infrastructure')


def test_the_learned_end_round_template_and_the_overlay_sweep_send_no_input_to_the_game():
    learn = next(node for node in ast.walk(tree_of('player/flow_capture.py')) if isinstance(node, ast.FunctionDef) and node.name == 'capture_missing_end_round')
    assert not {call for call in calls(learn) if call.startswith(('act.', 'adb', 'capture.tap'))}, 'it reads a frame and writes one template'
    sweep = discover()['overlay_sweep_at']['scopes']['if']
    assert 'overlays.clean' in calls(sweep) and 'overlays.offending' in calls(sweep)


# ---------------------------------------------------------------------- rows 21 and 22 (C-13, C-14)
def daystate_calls(rel):
    return {node.func.attr for node in ast.walk(tree_of(rel)) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == 'daystate'}


def daystate_attributes(rel):
    return {node.attr for node in ast.walk(tree_of(rel)) if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == 'daystate'}


def test_the_two_state_consumers_reach_the_daily_state_only_through_its_serialized_public_functions():
    assert daystate_attributes('tools/tier_ladder.py') == {'mark_today'}
    assert daystate_attributes('interactions/perks.py') == {'get_raw', 'set_raw'}
    for rel in ('tools/tier_ladder.py', 'interactions/perks.py'):
        text = (BACKEND / rel).read_text(encoding='utf-8')
        assert 'daily_state' not in text and 'daystate.STATE' not in text, rel
    functions = {node.name: node for node in tree_of('scheduling/daystate.py').body if isinstance(node, ast.FunctionDef)}
    for writer in ('mark_today', 'set_raw'):
        assert 'update' in calls(functions[writer]), f'{writer} is one locked update step (the serialized owner)'


def test_the_consumers_are_declared_as_state_with_the_scope_their_key_has():
    marker, cache = CATALOG.get('tournament_day_marker'), CATALOG.get('perk_ban_cache')
    assert (marker.scope, cache.scope) == ('installation', 'account')
    assert isinstance(marker.schedule, EventOnly) and isinstance(cache.schedule, EventOnly)
    assert marker.hosts == cache.hosts == frozenset() and marker.consumes is None and cache.consumes is None, 'not calendar actions: no consume hook, no recorder'
    assert marker.capabilities.run_now is False and cache.capabilities.run_now is False
    perks = (BACKEND / 'interactions' / 'perks.py').read_text(encoding='utf-8')
    assert '"perk_bans:" + str(inst.get("account") or settings.CONFIG["active_instance"])' in perks, 'the cache key is per account'


@pytest.fixture
def state(tmp_path, monkeypatch):
    """The daily-state file in a temporary folder, with every serialized write step counted by key."""
    monkeypatch.setattr(daystate, 'STATE', str(tmp_path / 'daily_state.json'))
    writes = []
    original = daystate.update
    monkeypatch.setattr(daystate, 'update', lambda key, change: (writes.append(key), original(key, change))[1])
    return writes


def test_the_by_hand_tournament_marker_still_suppresses_the_tournament_block_through_one_serialized_write(state):
    from scheduling import combo
    now = datetime.datetime.now()
    assert combo._key('tournament') == 'combo_tournament'
    assert combo._tournament_taken(now, blocks=[]) is None
    daystate.mark_today('combo_tournament')                    # what tools/tier_ladder.py writes after a tournament played by hand
    assert combo._tournament_taken(now, blocks=[]) == 'combo_tournament'
    assert state == ['combo_tournament']
    assert json.loads(open(daystate.STATE).read()) == {'combo_tournament': datetime.date.today().isoformat()}, 'the file format and the key are unchanged'
    ladder = (BACKEND / 'tools' / 'tier_ladder.py').read_text(encoding='utf-8')
    assert 'daystate.mark_today("combo_tournament")' in ladder


def test_the_perk_ban_cache_applies_a_set_once_per_account_and_writes_through_the_serialized_owner(state, monkeypatch):
    from interactions import perks
    account = {'account': 'acct-1'}
    monkeypatch.setattr(perks.settings, 'instance', lambda: account)
    monkeypatch.setattr(perks.logger, 'event', lambda *args, **kwargs: None)
    applied = []
    monkeypatch.setattr(perks, 'apply_bans', lambda wanted: applied.append(list(wanted)) or {'banned': list(wanted), 'toggled': []})
    assert perks.ensure_bans(None) is False and applied == [], 'null never opens the dialog'
    assert perks.ensure_bans(['alpha']) is True and len(applied) == 1
    token = json.dumps(perks.wanted_set(['alpha']))
    assert daystate.get_raw('perk_bans:acct-1') == token
    assert perks.ensure_bans(['alpha']) is False and len(applied) == 1, 'the same set for the same account pays nothing'
    assert perks.ensure_bans(['beta']) is True and len(applied) == 2
    account['account'] = 'acct-2'
    assert perks.ensure_bans(['beta']) is True and len(applied) == 3, 'another account has its own cache'
    assert state == ['perk_bans:acct-1', 'perk_bans:acct-1', 'perk_bans:acct-2']
    assert set(json.loads(open(daystate.STATE).read())) == {'perk_bans:acct-1', 'perk_bans:acct-2'}
