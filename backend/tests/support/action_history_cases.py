"""Builders shared by the r18h history, clock, restart, concurrency, coalescing and compatibility tests (a test helper, not a test).

Everything is offline and temporary: a ledger under a test's tmp_path, a fake clock, no device, no adb, no live port. `RealHost` is the real thing at the seams that
matter (a real operation, a real `Battles` row, a real `BattleSession`, a real `ActionSession`); `battle` and `execution` are plain records for the rules of the pure
validator, which reads nothing else.
"""
import itertools

from control import operations
from towerpilot_workflows import actions
from towerpilot_workflows.action_contract import FakeClock
from towerpilot_workflows.battles import Battles
from towerpilot_workflows.operations import Operations

CONTEXT = dict(device_key='device', account='account', instance='main', serial='fake')
BODY = dict(gather={'ad_gems': True, 'gem_orbit': {'enabled': False}}, shopping=[], shop_interval_sec=90)
_ids = itertools.count(1)


class SteppedClock(FakeClock):
    """A fake clock whose wall time can be stepped on its own (an NTP correction, a manual change) while the monotonic time keeps going forward only."""

    def step_wall(self, seconds):
        self._wall += seconds

    def set_wall(self, value):
        self._wall = float(value)

    def advance_mono(self, seconds):
        self._mono += seconds


def battle(*, battle_id='b1', origin='started', status='active', generations=('g1',), created_at=1000.0, device='device', account='account', ended_at=None):
    """A battle record with the fields the validator reads. One attachment per generation, in order, each starting 100 s after the one before."""
    segments = [dict(generation=generation, operation_id=f'op-{index}', started_at=created_at + 100.0 * index, ended_at=None) for index, generation in enumerate(generations)]
    if ended_at is not None:
        segments[-1]['ended_at'] = ended_at
    return dict(id=battle_id, origin=origin, status=status, created_at=created_at, segments=segments,
                context=dict(device_key=device, account=account, instance='main', serial='fake'))


def execution(*, action='ad_gems', instance=None, battle_id='b1', generation='g1', started_at=1100.0, duration_ms=5000.0, state='succeeded',
              effect='confirmed', reason='offer_disappeared', occurrence_key=None, coverage='full', account='account', device='device', operation_id='op-0',
              identity=None, trigger='automatic', finished_at=None, aggregate=None):
    """A whole stored body (the shape every writer makes): terminal by default, `state='running'` for an open one."""
    body = actions.new_record(device=device, action=action, instance=instance or action, trigger=trigger, occurrence_key=occurrence_key, started_at=started_at,
                              host=dict(driver='orchestrator', operation_id=operation_id, battle_id=battle_id, generation=generation), context=dict(account=account),
                              coverage=coverage)
    body['id'] = identity or f'e{next(_ids)}'
    if state != 'running':
        body.update(state=state, finished_at=started_at + (duration_ms or 0) / 1000 if finished_at is None else finished_at,
                    duration_ms=duration_ms, duration_basis='monotonic' if duration_ms is not None else 'none')
        body['outcome'] = dict(result=state, reason=reason, effect=effect, attempts=1, confirmed=int(effect == 'confirmed'), evidence=[reason] if reason else [])
    body['aggregate'] = aggregate
    assert actions.is_whole(body), body
    return body


class RealHost:
    """One driver over a temporary ledger: an operation, its battle, a battle session and the ActionSession attached to it."""

    def __init__(self, root, *, origin='started', key='op-1', clock=None, tracked_only=False, previous_battle_id=None, driver='orchestrator'):
        from runtime.action_session import ActionSession
        from runtime.battle_session import BattleSession
        self.path = operations.ledger_path(root)
        self.ops = Operations(self.path)
        self.operation, _ = self.ops.accept('farm', CONTEXT, {}, key)
        self.store = Battles(self.path)
        self.battle_session = BattleSession(self.store, self.operation['id'], dict(BODY), {'active_profile': 'mine'}, origin=origin, previous_battle_id=previous_battle_id)
        self.session = ActionSession.for_battle(self.battle_session, driver, tracked_only=tracked_only)
        self.session.clock = clock or FakeClock()
        actions.Actions(str(self.path))        # the ledger names itself now, after the battle it will hold (a battle made before it is 'recorded before action history')

    @property
    def battle_id(self):
        return self.battle_session.battle['id']

    def battle_record(self):
        return self.store.get(self.battle_id)

    def rows(self, **filters):
        return actions.Actions.read(str(self.path), device='device', **filters)[0]

    def end_battle(self, status='ended'):
        """The battle ends in the ledger and in the driver's own view of it, WITHOUT the driver's own settlement (`BattleSession.finish` would close the open rows)."""
        self.battle_session.battle = self.store.finish(self.battle_id, self.battle_session.generation, reason='test', status=status)

    def end_operation(self):
        """What the dashboard's reconciliation does to a worker that died: its operation becomes terminal."""
        self.ops.transition(self.operation['id'], self.ops.get(self.operation['id'])['revision'], 'interrupted')
