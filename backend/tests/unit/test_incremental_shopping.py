"""Sustained shopping yields monitoring and requires evidence for every buy."""
import json
from types import SimpleNamespace

import pytest

from interactions import shopper
from player.shopping_plan import normalize_plan
from runtime import logger


@pytest.fixture
def market(monkeypatch):
    clock = [100.0]
    state = SimpleNamespace(cash=1000, prices={'health': 10, 'damage': 10},
                            tab='defense', quantity=1, outcome='confirmed', quotes=[], taps=[], clock=clock)
    monkeypatch.setattr(shopper.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(shopper, '_TOP_RESETS', 0)
    monkeypatch.setattr(shopper, '_swipe', lambda **kwargs: None)
    monkeypatch.setattr(shopper.detect, 'panel_tab', lambda frame: state.tab)
    monkeypatch.setitem(shopper.CONFIG, 'tabs', {'defense': (1, 1), 'attack': (2, 2)})

    def observe(frame, stat):
        quote = dict(center=(stat, frame), state='buy', price=state.prices[stat],
                     cash=state.cash, quantity=state.quantity, total_price_verified=False,
                     observed_at=clock[0])
        state.quotes.append((frame, stat, quote.copy()))
        return quote

    def tap(x, y, *, reason):
        if reason.startswith('tab_'):
            state.tab = reason.removeprefix('tab_')
            return {}
        state.taps.append((y, x, state.cash, state.prices[x]))
        outcome = state.outcomes.pop(0) if getattr(state, 'outcomes', None) else state.outcome
        if outcome == 'refused':
            raise shopper.act.TapRefused('synthetic lease refused')
        if outcome == 'dry_run':
            return {'dry_run': True}
        if outcome == 'confirmed':
            state.cash -= state.prices[x]
            state.prices[x] += 1
        return {}

    monkeypatch.setattr(shopper, '_purchase_quote', observe)
    monkeypatch.setattr(shopper.act, 'tap', tap)
    return state


def plan(mode='highest_affordable', **changes):
    return normalize_plan(dict(version=2, cadence='while_affordable', interval_sec=90,
                               cash_reserve=50, queue_mode=mode,
                               queue=[{'id': 'health'}], **changes))


def run_window(instance, market, *, start=1):
    instance.start()
    monitored = []
    for frame in range(start, start + 100):
        market.clock[0] += .1
        monitored.append(frame)  # caller's monitor gets a fresh pass before each action
        before = len(market.taps)
        instance.step(frame, wave=20, after_intro_sprint=True)
        assert len(market.taps) - before <= 1
        if not instance.active:
            return monitored
    pytest.fail('Shopping window did not terminate within its bounded action budget')


def test_sustained_window_has_fresh_quotes_confirmations_and_monitor_yields(market):
    instance = shopper.Shopper({'shopping_plan': plan()})
    monitored = run_window(instance, market)
    assert len(market.taps) == 4
    assert len(monitored) == 8  # separate observed purchase and confirmation frames
    assert instance.status()['continuation_ready'] is True
    assert instance.status()['last_sweep']['outcome'] == 'confirmed'
    assert instance.status()['last_sweep']['confirmed_purchases'] == 4
    assert instance.attempts['queue/health'] == 4
    for frame, stat, cash, price in market.taps:
        fresh = [quote for observed_frame, uid, quote in market.quotes if observed_frame == frame and uid == stat]
        assert fresh[-1]['cash'] == cash and fresh[-1]['price'] == price
        assert cash - price >= 50
    assert [frame for frame, *_ in market.taps] == [1, 3, 5, 7]
    records = [json.loads(line) for line in logger.run_file().read_text().splitlines()]
    started, = [record for record in records if record['kind'] == 'shop_sweep_start']
    ended, = [record for record in records if record['kind'] == 'shop_sweep_end']
    assert started['sweep_id'] == ended['sweep_id'] == instance.status()['sweep_id']
    assert ended['outcome'] == 'confirmed' and ended['confirmed_purchases'] == 4


def test_affordable_continuation_stops_at_reserve_and_uses_configured_idle_backoff(market):
    market.cash = 80
    instance = shopper.Shopper({'shopping_plan': plan()})
    run_window(instance, market)
    assert len(market.taps) == 2 and market.cash == 59
    assert instance.status()['decision']['reason'] == 'reserve_or_no_eligible_upgrades'
    assert instance.status()['last_sweep']['outcome'] == 'confirmed'
    assert instance.status()['last_sweep']['confirmed_purchases'] == 2
    assert not instance.status()['continuation_ready']
    assert not instance.due(150, 100, 90)
    assert instance.due(191, 100, 90)
    assert instance.plan['interval_sec'] == 90 and instance.plan['cash_reserve'] == 50


@pytest.mark.parametrize('outcome,reason,pending', [
    ('unknown', 'purchase_confirmation_unknown', True),
    ('dry_run', 'highest_affordable', False),
    ('refused', 'tap_refused', False),
])
def test_unverified_attempt_never_continues_or_shortens_idle_backoff(market, outcome, reason, pending):
    market.outcome = outcome
    instance = shopper.Shopper({'shopping_plan': plan()})
    run_window(instance, market)
    assert len(market.taps) == 1
    assert not instance.due(101, 100, 90)
    status = instance.status()
    assert status['decision']['reason'] == reason
    assert status['last_sweep']['confirmed_purchases'] == 0
    assert status['last_sweep']['outcome'] != 'confirmed'
    assert (status['pending_confirmation'] is not None) is pending


def test_quantity_unknown_stops_before_any_purchase(market):
    market.quantity = None
    instance = shopper.Shopper({'shopping_plan': plan()})
    run_window(instance, market)
    assert not market.taps
    assert instance.status()['decision']['reason'] == 'quantity_unverified'
    assert not instance.due(101, 100, 90)


def test_unknown_after_confirmed_purchase_stops_continuation_and_weight_advancement(market):
    market.outcomes = ['confirmed', 'unknown']
    instance = shopper.Shopper({'shopping_plan': plan('weighted_split')})
    run_window(instance, market)
    assert len(market.taps) == 2
    assert instance.attempts['confirmed/health'] == 1
    assert instance.status()['last_sweep']['confirmed_purchases'] == 1
    assert instance.status()['last_sweep']['outcome'] == 'unconfirmed'
    assert instance.status()['pending_confirmation'] is not None
    assert not instance.due(101, 100, 90)


def test_manual_try_once_stays_one_attempt_in_sustained_mode(market):
    instance = shopper.Shopper({'shopping_plan': plan()})
    assert instance.request_once(upgrade='health')
    for frame in range(1, 20):
        market.clock[0] += .1
        instance.step(frame, wave=20, after_intro_sprint=True)
        if not instance.active:
            break
    assert not instance.active and len(market.taps) == 1
    assert instance.status()['manual']['state'] == 'completed'
    assert instance.status()['manual']['attempts'] == 1
    assert not instance.status()['continuation_ready']


@pytest.mark.parametrize('mode,expected', [
    ('round_robin', ['health', 'damage', 'health', 'damage']),
    ('weighted_split', ['health', 'health', 'damage', 'health']),
])
def test_sustained_modes_preserve_queue_advancement(market, mode, expected):
    value = plan(mode)
    value['queue'] = [{'id': 'health', 'weight': 2}, {'id': 'damage', 'weight': 1}]
    instance = shopper.Shopper({'shopping_plan': value})
    run_window(instance, market)
    assert [stat for frame, stat, cash, price in market.taps] == expected
    if mode == 'weighted_split':
        assert instance.attempts['confirmed/health'] == 3
        assert instance.attempts['confirmed/damage'] == 1
        assert instance.attempts['__weighted_used'] == 1
    assert instance.status()['last_sweep']['confirmed_purchases'] == 4


def test_time_budget_stops_navigation_without_shortening_backoff(market):
    instance = shopper.Shopper({'shopping_plan': plan()})
    instance.start()
    market.clock[0] += 21
    instance.step(1, wave=20, after_intro_sprint=True)
    assert not market.taps and not instance.active
    assert instance.status()['decision']['reason'] == 'continuation_time_budget'
    assert not instance.due(101, 100, 90)


def test_old_plan_defaults_to_exactly_one_attempt(market):
    value = plan()
    del value['cadence']
    instance = shopper.Shopper({'shopping_plan': value})
    run_window(instance, market)
    assert len(market.taps) == 1
    assert instance.plan['cadence'] == 'single_attempt'
    assert not instance.due(101, 100, 90)


@pytest.mark.parametrize('cadence', [None, False, 'burst', 3])
def test_invalid_cadence_rejected(cadence):
    value = plan()
    value['cadence'] = cadence
    with pytest.raises(ValueError, match='cadence'):
        normalize_plan(value)


def test_orchestrator_terminal_noop_applies_backoff_immediately(market):
    from orchestrator import advance_shopping
    market.cash = 50
    instance = shopper.Shopper({'shopping_plan': plan()})
    rs = SimpleNamespace(shop=instance, last_shop=0, shop_done_logged=False,
                         mission=SimpleNamespace(active=False))
    advance_shopping(rs, 1, 20, False, 100, 90)
    assert not instance.active and rs.last_shop == 100
    assert instance.status()['last_sweep']['outcome'] == 'skipped'
    sweep_id = instance.status()['sweep_id']
    advance_shopping(rs, 2, 20, False, 101, 90)
    assert instance.status()['sweep_id'] == sweep_id


def test_orchestrator_continues_after_confirmed_window_with_an_idle_monitor_checkpoint(market):
    from orchestrator import advance_shopping
    instance = shopper.Shopper({'shopping_plan': plan()})
    rs = SimpleNamespace(shop=instance, last_shop=0, shop_done_logged=False,
                         mission=SimpleNamespace(active=False))
    inactive_checkpoints = []
    for frame in range(1, 10):
        market.clock[0] += .1
        if not instance.active:
            inactive_checkpoints.append(frame)  # other routines run here first
        advance_shopping(rs, frame, 20, False, market.clock[0], 90)
    assert inactive_checkpoints == [1, 9]
    assert len(market.taps) == 5
    assert instance.status()['pending_confirmation'] is not None
    assert instance.status()['last_sweep']['confirmed_purchases'] == 4
    assert rs.last_shop == pytest.approx(100.8)
    instance.abort()


def test_time_budget_after_confirmed_progress_allows_next_monitored_window(market):
    instance = shopper.Shopper({'shopping_plan': plan()})
    instance.start()
    for frame in (1, 2):
        market.clock[0] += .1
        instance.step(frame, wave=20, after_intro_sprint=True)
    assert len(market.taps) == 1 and instance.active
    market.clock[0] += 21
    instance.step(3, wave=20, after_intro_sprint=True)
    assert not instance.active and instance.due(market.clock[0], market.clock[0], 90)
    assert instance.status()['last_sweep']['outcome'] == 'confirmed'
    assert instance.status()['last_sweep']['confirmed_purchases'] == 1


@pytest.mark.parametrize('shopping_active', [False, True])
def test_active_mission_blocks_real_shopper_even_when_continuation_is_due(market, shopping_active):
    from orchestrator import advance_shopping
    instance = shopper.Shopper({'shopping_plan': plan()})
    run_window(instance, market)
    assert instance.status()['continuation_ready'] is True
    if shopping_active:
        instance.start()
    market.tab = 'attack'
    market.taps.clear()
    quotes = len(market.quotes)
    rs = SimpleNamespace(shop=instance, last_shop=market.clock[0], shop_done_logged=False,
                         mission=SimpleNamespace(active=True))
    sweep_id = instance.status()['sweep_id']
    market.clock[0] += .1
    advance_shopping(rs, 'mission frame', 20, False, market.clock[0], 90)
    assert market.tab == 'attack' and not market.taps
    assert len(market.quotes) == quotes and instance.status()['sweep_id'] == sweep_id
    assert instance.active is shopping_active
    instance.abort()


@pytest.mark.parametrize('flow_name', ['quests', 'guild', 'store', 'free_gems', 'global_quests', 'active_mission'])
@pytest.mark.parametrize('finishes_immediately', [False, True])
def test_main_reobserves_after_reward_dispatch_before_uw_or_shopping(
        market, monkeypatch, flow_name, finishes_immediately):
    """Run the real main dispatch with real Mission and sustained Shopper state."""
    import orchestrator as orch
    from interactions import missions, store
    from player import readiness
    from runtime import battle_session, device_lease
    from scheduling import global_rewards

    instance = shopper.Shopper({'shopping_plan': plan()})
    run_window(instance, market)
    assert instance.status()['continuation_ready'] is True
    market.tab = 'attack'  # a fall-through would immediately dispatch tab_defense
    actions = []

    def reward_flow():
        frame = yield
        orch.act.tap(10, 20, reason=f'reward_open:{flow_name}')
        if finishes_immediately:
            return
        yield

    mission = missions.Mission()
    if flow_name == 'active_mission':
        mission.start(reward_flow)
    rs = SimpleNamespace(tracker=SimpleNamespace(last=20, update=lambda raw: raw),
        wave_hwm=None, mission=mission, shop=instance, last_shop=market.clock[0],
        shop_done_logged=False, await_guild_result=False, pending_store=flow_name == 'store',
        rewards_enabled=True, menu_open_frames=1, menu_closed_frames=0,
        end_round_learn_at=market.clock[0], quest_due=(flow_name, 0) if flow_name in ('quests', 'guild') else None,
        last_visit={}, free_gems_try_at=0., uw_next_try=0., cl_normalized=False,
        cl_on=False, uw_fails=0, uw_normalized=False, uw_done=set(), cl_blocked_logged=False,
        sprint_prev=None, shopping_enabled=True)
    gathering = dict(quests_8h=flow_name == 'quests', quest_rewards=False,
                     guild=flow_name == 'guild', free_store_gems=flow_name == 'free_gems')
    body = dict(shopping_plan=plan(), shop_interval_sec=90, gather=gathering,
                chain_lightning={'enabled': True, 'always_on': False}, abilities={},
                global_behaviors={'daily_missions': True} if flow_name == 'global_quests' else {})
    monkeypatch.setattr(orch, 'preset', lambda: body)
    monkeypatch.setattr(orch, 'RunState', lambda: rs)
    monkeypatch.setattr(orch, 'run_tier', lambda: 1)
    monkeypatch.setattr(orch, 'is_compiled_preset', lambda: False)
    monkeypatch.setattr(orch, '_prepare_coin_start', lambda: None)
    monkeypatch.setattr(device_lease, 'ensure_current', lambda: None)
    monkeypatch.setattr(battle_session.BattleSession, 'from_environment', lambda *a, **k: None)
    monkeypatch.setattr(readiness, 'require', lambda *a: None)
    monkeypatch.setattr(orch.psutil, 'Process', lambda: SimpleNamespace(cpu_percent=lambda: 0))
    monkeypatch.setattr(orch.wave_reader, 'read_wave', lambda f: 20)
    monkeypatch.setattr(orch, 'watch_frame', lambda *a, **k: actions.append('monitor') or None)
    monkeypatch.setattr(orch, 'eval_rules', lambda *a: None)
    monkeypatch.setattr(orch, 'apply_cancel_sprint', lambda *a: None)
    monkeypatch.setattr(orch, 'max_wave_reached', lambda *a: False)
    monkeypatch.setattr(orch, 'run_in_run_actions', lambda *a: False)
    monkeypatch.setattr(orch, 'cl_window', lambda *a: False)
    monkeypatch.setattr(orch, 'automation_enabled', lambda *a: True)
    monkeypatch.setattr(orch.detect, 'side_menu_open', lambda f: True)
    monkeypatch.setattr(orch.detect, 'intro_sprint_active', lambda f: False)
    monkeypatch.setattr(missions, 'quests_badge', lambda f: flow_name in ('quests', 'global_quests'))
    monkeypatch.setattr(missions, 'guild_badge', lambda f: flow_name == 'guild')
    monkeypatch.setattr(missions, 'quest_flow', reward_flow)
    monkeypatch.setattr(missions, 'guild_flow', reward_flow)
    monkeypatch.setattr(store, 'store_flow', reward_flow)
    monkeypatch.setattr(missions, 'free_gems_flow', lambda **k: reward_flow())
    from towerpilot_workflows.action_contract import Decision
    monkeypatch.setattr(global_rewards, 'store_decide', lambda *a, **k: Decision('due', occurrence_key='2026-09-10'))
    monkeypatch.setattr(global_rewards, 'decide', lambda key, now=None: Decision('due'))
    monkeypatch.setattr(global_rewards, 'settle', lambda key, now=None: None)
    monkeypatch.setattr(orch.act, 'tap', lambda *a, reason, **k: actions.append(reason) or {})
    monkeypatch.setattr(shopper, '_swipe', lambda **k: actions.append('shopping_swipe'))
    monkeypatch.setattr(shopper, 'uw_toggle', lambda *a: actions.append('uw_toggle') or True)
    # The next capture is the assertion boundary: no real loop/device or sleep.
    class Reobserved(BaseException):
        pass
    captures = []
    def grab():
        captures.append(len(captures))
        if len(captures) > 2:  # startup observation, first loop observation, next loop
            raise Reobserved
        return 'battle frame'
    monkeypatch.setattr(orch.capture, 'grab', grab)
    monkeypatch.setattr(orch.time, 'sleep', lambda seconds: None)
    market.clock[0] += .1
    with pytest.raises(Reobserved):
        orch.main()
    assert actions == ['monitor', f'reward_open:{flow_name}']
    assert instance.status()['continuation_ready'] is True and not instance.active
    assert mission.active is not finishes_immediately
    if flow_name in ('quests', 'guild'):
        assert rs.quest_due is None and rs.last_visit[flow_name] == market.clock[0]
    if flow_name == 'guild':
        assert rs.await_guild_result is True
    if flow_name == 'store':
        assert rs.pending_store is False
    if flow_name == 'free_gems':
        assert rs.free_gems_try_at == market.clock[0] + 300
    mission.abort()
