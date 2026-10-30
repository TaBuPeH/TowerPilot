"""The history of the reward flows (roadmap item 7, A3a): Mission's end hook, FlowHistory, and the orchestrator's reward upkeep writing rows through them."""
import contextlib
from types import SimpleNamespace

import pytest

import action_a3a_golden as golden
from interactions import missions
from runtime.action_session import ActionSession
from runtime.flow_history import FlowHistory, flow_outcome
from towerpilot_workflows.action_contract import FakeClock
from towerpilot_workflows.actions import Actions

CONTEXT = dict(device_key='device', account='account', instance='main', serial='fake', profile='mine')


# ------------------------------------------------------------------ the outcome table
@pytest.mark.parametrize('gated,how,proof,expected', [
    (False, 'completed', None, ('succeeded', 'flow_completed', 'unknown', (), 0)),        # handler completion is not a game effect
    (True, 'completed', None, ('no_op', 'no_effect_confirmed', 'none', (), 0)),           # an evidence-gated action is not "succeeded" without proof
    (True, 'completed', ('milestones_claimed', 3), ('succeeded', 'milestones_claimed', 'confirmed', ('milestones_claimed',), 3)),
    (False, 'crashed', None, ('failed', 'flow_crashed', 'unknown', (), 0)),
    (False, 'interrupted', None, ('cancelled', 'flow_interrupted', 'unknown', (), 0)),
    (False, 'aborted', None, ('cancelled', 'flow_aborted', 'unknown', (), 0)),
    (True, 'crashed', ('cooldown_shown', 1), ('failed', 'flow_crashed', 'confirmed', ('cooldown_shown',), 1)),        # a proved effect stays proved when the flow dies later
])
def test_how_a_flow_ended_decides_what_is_recorded(gated, how, proof, expected):
    assert flow_outcome(gated, how, proof) == expected


# ------------------------------------------------------------------ Mission's end hook
def flow_ending(kind):
    def flow():
        yield
        yield
        if kind == 'interrupted':
            raise missions.FlowInterrupted('person took over')
        if kind == 'crashed':
            raise RuntimeError('boom')
    return flow


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(missions.logger, 'event', lambda *a, **k: None)
    monkeypatch.setattr(missions.logger, 'shot', lambda *a, **k: None)


def test_a_mission_reports_how_each_flow_ended_exactly_once(quiet):
    ends = []
    mission = missions.Mission()
    mission.on_end = ends.append
    mission.start(flow_ending('completed'))
    mission.step(None)
    assert ends == [] and mission.active
    mission.step(None)
    assert ends == ['completed'] and mission.ended == 'completed' and not mission.active
    mission.step(None)                                                     # an idle mission reports nothing
    assert ends == ['completed']
    mission.start(flow_ending('interrupted')); mission.step(None); mission.step(None)
    mission.start(flow_ending('crashed')); mission.step(None); mission.step(None)
    mission.start(flow_ending('completed')); mission.abort()
    mission.abort()                                                        # aborting an idle mission is not an ending
    assert ends == ['completed', 'interrupted', 'crashed', 'aborted'] and mission.ended == 'aborted'


def test_a_hook_that_raises_never_ends_or_breaks_the_flow(monkeypatch):
    logged = []

    def hook(how):
        raise RuntimeError('bookkeeping broke')
    mission = missions.Mission()
    mission.on_end = hook
    monkeypatch.setattr(missions.logger, 'event', lambda kind, **fields: logged.append(kind))
    mission.start(flow_ending('completed'))
    mission.step(None); mission.step(None)
    assert not mission.active and mission.ended == 'completed' and 'mission_end_hook_failed' in logged


def test_a_mission_without_a_hook_behaves_as_before(quiet):
    mission = missions.Mission()
    mission.start(flow_ending('completed'))
    mission.step(None); mission.step(None)
    assert not mission.active


# ------------------------------------------------------------------ FlowHistory over a real ledger
@pytest.fixture
def session(tmp_path):
    return ActionSession(tmp_path / 'operations.db', context=CONTEXT, driver='orchestrator', clock=FakeClock(mono=100.0, wall=5000.0))


def rows(session, **kw):
    return Actions.read(session.path, device='device', **kw)[0]


def test_a_flow_is_an_open_row_that_the_end_hook_closes(session):
    history = FlowHistory(session)
    history.opened('quest_visit')
    assert [row['state'] for row in rows(session)] == ['running'] and history.action == 'quest_visit'
    history.ended('completed')
    row, = rows(session)
    assert (row['action'], row['state'], row['outcome']['effect'], row['outcome']['reason']) == ('quest_visit', 'succeeded', 'unknown', 'flow_completed') and history.action is None


def test_a_gated_action_needs_proof_to_succeed_and_carries_it(session):
    history = FlowHistory(session)
    history.opened('guild_visit'); history.ended('completed')
    history.opened('guild_visit'); history.proved('milestones_claimed', 3); history.ended('completed')
    outcomes = sorted((row['outcome']['result'], row['outcome']['effect'], row['outcome']['confirmed']) for row in rows(session))
    assert outcomes == [('no_op', 'none', 0), ('succeeded', 'confirmed', 3)]


def test_a_dependent_action_names_the_execution_it_follows(session):
    history = FlowHistory(session)
    history.opened('guild_visit'); history.proved('milestones_claimed', 2); history.ended('completed')
    visit = next(row for row in rows(session) if row['action'] == 'guild_visit')
    history.opened('guild_store', trigger='dependency', parent='guild_visit'); history.ended('completed')
    follow_up = next(row for row in rows(session) if row['action'] == 'guild_store')
    assert follow_up['trigger'] == 'dependency' and follow_up['host']['parent_execution'] == visit['id']
    history.opened('guild_store', trigger='dependency', parent='never_ran'); history.ended('completed')
    orphan = [row for row in rows(session) if row['action'] == 'guild_store' and row['id'] != follow_up['id']][0]
    assert orphan['host']['parent_execution'] is None                          # a parent that never ran is not invented


def test_checks_that_found_nothing_are_one_row_counted(session):
    history = FlowHistory(session)
    for _ in range(50):
        history.checked('daily_missions')
    row, = rows(session)
    assert (row['action'], row['state'], row['aggregate']['count'], row['outcome']['reason']) == ('daily_missions', 'no_op', 50, 'badge_absent')


def test_a_flow_that_never_reported_its_end_is_closed_failed_when_the_next_one_opens(session):
    history = FlowHistory(session)
    history.opened('quest_visit')
    history.opened('quest_visit')
    done = sorted(rows(session), key=lambda row: row['state'])
    assert [row['state'] for row in done] == ['failed', 'running'] and done[0]['outcome']['reason'] == 'superseded'


def test_an_end_with_nothing_open_records_nothing(session):
    FlowHistory(session).ended('completed')
    assert rows(session) == []


def test_a_ledger_that_fails_never_raises_into_the_flow(session, monkeypatch):
    def broken(self, *a, **k):
        raise RuntimeError('database is locked')
    monkeypatch.setattr(Actions, 'begin', broken)
    monkeypatch.setattr(Actions, 'finish', broken)
    monkeypatch.setattr(Actions, 'record_check', broken)
    history = FlowHistory(session)
    history.opened('quest_visit'); history.proved('x'); history.ended('completed'); history.checked('daily_missions')
    assert session.unrecorded and rows(session) == []


# ------------------------------------------------------------------ the orchestrator's upkeep writing rows
class World:
    """The reward upkeep of one run with a REAL Mission and a REAL FlowHistory over a temporary ledger, under the golden harness's clock and daily state."""

    def __init__(self, tmp_path, *, gather=None, global_=None):
        import orchestrator
        self.orchestrator = orchestrator
        self.clock, self.store, self.events, self.draws, self.started = golden.Clock(), golden.Store(), [], [], []
        rs, self.badges, self.prefs, patches = golden.reward_world(self.clock, 1, self.events, self.draws, self.started, self.store, gather=gather, global_=global_)
        self.session = ActionSession(tmp_path / 'operations.db', context=CONTEXT, driver='orchestrator', clock=FakeClock(mono=100.0, wall=5000.0))
        self.history = FlowHistory(self.session)
        rs.mission, rs.rewards_history = missions.Mission(), self.history
        rs.mission.on_end = lambda how: orchestrator.reward_flow_ended(rs, how)
        self.rs, self.end, self.claims, self.on_success = rs, False, 0, None
        self.stack = contextlib.ExitStack()
        for patch in patches:
            self.stack.enter_context(patch)
        self.stack.enter_context(golden.frozen(self.clock))
        self.stack.enter_context(golden.daily_state(self.store))
        from interactions import event_rewards, store as store_module
        from unittest import mock
        for module, name in ((missions, 'quest_flow'), (missions, 'guild_flow'), (event_rewards, 'event_flow'), (store_module, 'store_flow'), (missions, 'free_gems_flow')):
            self.stack.enter_context(mock.patch.object(module, name, self.flow(name)))

    def flow(self, label):
        world = self

        def make(*args, **kwargs):
            def generator():
                world.started.append(label)
                world.on_success = kwargs.get('on_success')
                yield
                while not world.end:
                    yield
                missions.last_guild_claims = world.claims
            return generator()
        return make

    def tick(self, dt=1.0, *, menu_status=False):
        self.clock.t += dt
        self.started.clear()
        if menu_status:                                  # main()'s menu branch steps the flow without step_reward_mission
            self.rs.mission.step(None)
            return 'menu_step'
        if self.orchestrator.step_reward_mission(self.rs, None):
            return 'mission_step'
        return 'continue' if self.orchestrator.advance_rewards(self.rs, None, self.clock.t, 123, self.prefs.global_) else 'fall_through'

    def finish(self, **kw):
        self.end = True
        self.tick(**kw)
        self.end = False

    def rows(self, **kw):
        return rows(self.session, **kw)

    def close(self):
        self.stack.close()


@pytest.fixture
def world(tmp_path):
    made = []

    def build(**kw):
        made.append(World(tmp_path, **kw))
        return made[-1]
    yield build
    for item in made:
        item.close()


def test_a_global_check_with_a_badge_is_a_flow_row_and_without_one_an_aggregated_no_op(world):
    w = world(global_={'daily_missions': True}, gather={})
    w.tick(); w.tick()                                                          # no badge: the check is made, a no-op row is written
    assert [(row['action'], row['state']) for row in w.rows()] == [('daily_missions', 'no_op')]
    w.badges.quests = True
    w.tick(400)                                                                 # 300 s later: the badge is there, a flow starts
    assert w.started == ['quest_flow']
    running = [row for row in w.rows() if row['state'] == 'running']
    assert [row['action'] for row in running] == ['daily_missions']
    w.finish()
    done = next(row for row in w.rows() if row['action'] == 'daily_missions' and row['state'] != 'no_op')
    assert (done['state'], done['outcome']['effect'], done['outcome']['reason']) == ('succeeded', 'unknown', 'flow_completed')


def test_a_legacy_visit_is_a_quest_visit_row_and_a_guild_visit_that_claimed_is_confirmed(world):
    w = world()
    w.badges.guild = True
    for _ in range(60):
        w.tick(30)
        if w.started == ['guild_flow']:
            break
    assert w.started == ['guild_flow'] and [row['action'] for row in w.rows()] == ['guild_visit']
    w.claims = 2
    w.finish()
    visit = w.rows()[0]
    assert (visit['state'], visit['outcome']['effect'], visit['outcome']['confirmed'], visit['outcome']['evidence']) == ('succeeded', 'confirmed', 2, ['milestones_claimed'])
    assert w.rs.pending_store is True                                           # the follow-up is raised by the event
    w.tick(1)
    assert w.started == ['store_flow']
    w.finish()
    follow_up = next(row for row in w.rows() if row['action'] == 'guild_store')
    assert follow_up['trigger'] == 'dependency' and follow_up['host']['parent_execution'] == visit['id'] and follow_up['state'] == 'succeeded'
    assert w.rs.pending_store is False


def test_a_guild_visit_that_claimed_nothing_is_a_no_op_not_a_success(world):
    w = world(gather={'quests_8h': False, 'quest_rewards': False, 'guild': True})
    w.badges.guild = True
    for _ in range(60):
        w.tick(30)
        if w.started:
            break
    w.claims = 0
    w.finish()
    row, = w.rows()
    assert (row['action'], row['state'], row['outcome']['reason'], row['outcome']['effect']) == ('guild_visit', 'no_op', 'no_effect_confirmed', 'none')
    assert w.rs.pending_store is False


def test_the_daily_store_row_carries_its_utc_day_and_a_confirmed_claim_proves_the_effect(world):
    w = world(gather={'free_store_gems': True, 'quests_8h': False, 'guild': False})
    for _ in range(40):
        w.tick(300)
        if w.started == ['free_gems_flow']:
            break
    assert w.started == ['free_gems_flow']
    w.on_success()                                                              # the flow saw the cooldown on the card
    w.finish()
    row, = w.rows()
    assert (row['action'], row['occurrence_key'], row['state'], row['outcome']['effect'], row['outcome']['evidence']) == ('free_store_gems', '2026-09-10', 'succeeded', 'confirmed', ['cooldown_shown'])
    assert w.store.data['global_rewards:alice:store'] == '2026-09-10'          # the day is closed


def test_a_store_attempt_without_a_confirmed_claim_is_not_a_success_and_the_day_stays_open(world):
    w = world(gather={'free_store_gems': True, 'quests_8h': False, 'guild': False})
    for _ in range(40):
        w.tick(300)
        if w.started:
            break
    w.finish()
    row, = w.rows()
    assert (row['state'], row['outcome']['reason'], row['outcome']['effect']) == ('no_op', 'no_effect_confirmed', 'none')
    assert 'global_rewards:alice:store' not in w.store.data


def test_a_flow_that_ends_in_the_menu_branch_is_closed_by_the_hook_too(world):
    w = world(global_={'event_missions': True}, gather={})
    w.badges.events = True
    w.tick(); w.tick()
    assert w.started == ['event_flow'] and [row['state'] for row in w.rows()] == ['running']
    w.end = True
    assert w.tick(menu_status=True) == 'menu_step'
    assert [row['state'] for row in w.rows()] == ['succeeded']


def test_a_flow_the_run_abandons_is_closed_cancelled_by_the_abort(world):
    w = world(global_={'event_missions': True}, gather={})
    w.badges.events = True
    w.tick(); w.tick()
    w.rs.mission.abort()
    row, = w.rows()
    assert (row['state'], row['outcome']['reason']) == ('cancelled', 'flow_aborted')


def test_a_run_with_no_history_runs_the_upkeep_exactly_as_before(world):
    w = world(global_={'event_missions': True}, gather={})
    w.rs.rewards_history = None
    w.badges.events = True
    w.tick(); w.tick(); w.finish()
    assert w.rows() == [] and w.started == []                                   # nothing recorded; the flow started and ended without a ledger

# ------------------------------------------------------------------ the wiring in the orchestrator
def test_a_run_state_gets_the_history_and_the_end_hook_only_when_the_process_records(monkeypatch, session):
    import orchestrator
    monkeypatch.setattr(orchestrator, '_battle_session', None)
    monkeypatch.setattr(orchestrator.logger, 'event', lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, '_rewards_history', None)
    plain = orchestrator.RunState()
    assert plain.rewards_history is None and plain.mission.on_end is None
    history = FlowHistory(session)
    monkeypatch.setattr(orchestrator, '_rewards_history', history)
    recording = orchestrator.RunState()
    assert recording.rewards_history is history and callable(recording.mission.on_end)


def test_the_chore_history_is_looked_up_once_per_process_and_is_none_outside_a_ledger_operation(monkeypatch):
    import orchestrator
    asked = []
    monkeypatch.setattr(ActionSession, 'for_operation', classmethod(lambda cls, driver: asked.append(driver)))
    monkeypatch.setattr(orchestrator, '_chore_session', False)
    assert orchestrator.chore_history() is None and orchestrator.chore_history() is None
    assert asked == ['orchestrator']                                   # not under an operation: asked once, never again, nothing recorded
