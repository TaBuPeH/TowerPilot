"""Interruption parity (roadmap item 6): a driver that stops at any lifecycle point leaves history that says so.

The host is stopped the way the dashboard's reconciliation stops a dead worker (its operation becomes terminal) and
takes no later action. The refresher, never a later `begin` and never a GET, settles what it left open `interrupted`
with unknown finish and duration, never `succeeded`. A restarted driver replays nothing.
"""
from types import SimpleNamespace

import pytest

import action_golden as golden
from control import actions as actions_api
from control import operations
from flows.shard import GemWatch
from interactions.shopper import Shopper
from runtime.action_session import ActionSession
from runtime.battle_session import BattleSession
from towerpilot_workflows.actions import Actions
from towerpilot_workflows.battles import Battles
from towerpilot_workflows.operations import Operations

CONTEXT = dict(device_key='device', account='account', instance='main', serial='fake')


class Host:
    """One driver: an operation, its battle, an action session and a real GemWatch, over a fake device."""

    def __init__(self, root, world, key):
        self.path = operations.ledger_path(root)
        self.world = world
        self.ops = Operations(self.path)
        self.operation, _ = self.ops.accept('farm', CONTEXT, {}, key)
        body = dict(gather={'ad_gems': True, 'gem_orbit': {'enabled': False}}, shopping=[], shop_interval_sec=90)
        self.battle = BattleSession(Battles(self.path), self.operation['id'], body, {'active_profile': 'mine'})
        self.actions = ActionSession.for_battle(self.battle)
        self.watch = GemWatch(body['gather'], session=self.actions)
        self.watch.ad_gems.rng = world.rng

    def run(self, seconds, *, offer=False):
        for _ in range(seconds):
            self.world.now += 1.0
            self.world.offer = offer or self.world.offer
            self.watch.poll(self.world.frame())

    def stop(self):
        """What reconciliation does to a worker that died: the operation ends; this host does nothing more."""
        self.ops.transition(self.operation['id'], self.ops.get(self.operation['id'])['revision'], 'interrupted')

    def rows(self):
        return Actions.read(str(self.path), device='device')[0]


@pytest.fixture
def stage(tmp_path):
    world = golden.World(seed=5)
    world.claim_confirms = False                           # an offer that never vanishes: input is sent, nothing is confirmed
    with golden.gem_world(world):
        yield tmp_path, world


@pytest.mark.parametrize('point,seconds,offer', [('after_window_start', 1, False), ('after_input', 14, True), ('before_confirmation', 30, True)])
def test_a_driver_stopped_mid_window_is_settled_interrupted_by_the_refresher_not_succeeded(stage, point, seconds, offer):
    root, world = stage
    host = Host(root, world, 'op-1')
    host.run(seconds, offer=offer)
    (open_row,) = [row for row in host.rows() if row['state'] == 'running']
    if point != 'after_window_start':
        assert world.taps, 'the claim input had been sent'
    host.stop()
    taps = len(world.taps)
    assert [row['state'] for row in host.rows()] == ['running']          # nothing has settled it yet: no later action, no GET
    assert actions_api.settle(root) == [open_row['id']]
    (settled,) = host.rows()
    assert settled['state'] == 'interrupted' and settled['finished_at'] is None and settled['duration_ms'] is None
    assert settled['outcome']['result'] == 'interrupted' and settled['outcome']['effect'] == 'unknown'
    assert settled['settled_reason'] == 'operation_ended' and settled['state'] != 'succeeded'
    assert actions_api.settle(root) == [] and len(world.taps) == taps    # idempotent; the host sent nothing further


def test_a_driver_that_finished_its_window_keeps_the_outcome_it_wrote(stage):
    root, world = stage
    world.claim_confirms = True
    host = Host(root, world, 'op-1')
    host.run(2)
    world.offer = True
    host.run(40)
    done = [row for row in host.rows() if row['state'] != 'running']
    assert done and done[-1]['state'] == 'succeeded'
    host.stop()
    assert actions_api.settle(root) == [row['id'] for row in host.rows() if row['state'] == 'running']
    assert [row for row in host.rows() if row['id'] == done[-1]['id']] == done[-1:]       # settlement never rewrites a finished row


def test_a_restarted_driver_replays_nothing_and_records_its_own_window(stage):
    root, world = stage
    first = Host(root, world, 'op-1')
    first.run(14, offer=True)
    first.stop()
    old_window = [row for row in first.rows() if row['state'] == 'running'][0]
    taps = len(world.taps)
    actions_api.settle(root)
    assert len(world.taps) == taps                                          # settlement sent no input and re-sent no claim
    stale = first.battle.battle                                             # the restart closes the dead driver's battle, as from_environment does
    Battles(first.path).finish(stale['id'], stale['generation'], reason='driver_operation_ended', status='interrupted')
    second = Host(root, world, 'op-2')                                      # the restarted driver: a new operation, battle, session
    second.run(1)
    rows = {row['id']: row for row in second.rows()}
    assert rows[old_window['id']]['state'] == 'interrupted'                 # the old window is not resumed, completed or re-opened
    fresh = [row for row in rows.values() if row['state'] == 'running']
    assert len(fresh) == 1 and fresh[0]['id'] != old_window['id']        # (the fake device's ids repeat, so the rows are told apart by id)
    assert fresh[0]['host']['operation_id'] != old_window['host']['operation_id']


def test_a_shopping_sweep_stopped_after_its_first_purchase_is_settled_interrupted(tmp_path):
    from orchestrator import advance_shopping
    path = operations.ledger_path(tmp_path)
    ops = Operations(path)
    operation, _ = ops.accept('farm', CONTEXT, {}, 'op-shop')
    battle = BattleSession(Battles(path), operation['id'], dict(gather={}, shopping=[], shop_interval_sec=90), {})
    actions = ActionSession.for_battle(battle)
    clock = [100.0]
    with golden.shopping_world(clock) as market:
        instance = Shopper({'shopping_plan': golden.shopping_plan()})
        instance.recorder = actions.recorder('shopping')
        rs = SimpleNamespace(shop=instance, last_shop=0, shop_done_logged=False, mission=SimpleNamespace(active=False))
        for index in range(3):
            clock[0] += 0.1
            advance_shopping(rs, index + 1, 20, False, clock[0], 90)
        assert market.taps and instance.active                              # mid-sweep, input already sent
    ops.transition(operation['id'], ops.get(operation['id'])['revision'], 'interrupted')
    (row,) = [r for r in Actions.read(str(path), device='device')[0] if r['action'] == 'shopping']
    assert row['state'] == 'running'
    assert actions_api.settle(tmp_path) == [row['id']]
    settled = Actions.read(str(path), device='device')[0][0]
    assert (settled['state'], settled['finished_at'], settled['duration_ms']) == ('interrupted', None, None)


def test_a_harvest_refusal_before_any_operation_exists_is_a_row_and_off_after_a_crash_stays_off(tmp_path, monkeypatch):
    from control import harvest
    from harvest_support import NOW, choose, make_env
    h = make_env(tmp_path, monkeypatch)
    choose(h)
    h.refusal = ('digits_missing', 'The digit font is not captured.')
    session = harvest.action_session(h.root, h.cfg)
    assert h.tick(NOW, actions=session)['action'] == 'refused'
    (row,) = Actions.read(str(operations.ledger_path(h.root)), device=harvest.identity_for(h.cfg)['device_key'])[0]
    assert row['host']['operation_id'] is None and row['state'] == 'refused' and row['finished_at'] is not None
    choose(h, enabled=False)                                                # the person turns it off, then the dashboard "crashes"
    h.refusal = None
    again = harvest.action_session(h.root, h.cfg)                           # a restarted dashboard host: a fresh session
    assert h.tick(NOW + 60, actions=again)['action'] == 'none' and h.spawned == []