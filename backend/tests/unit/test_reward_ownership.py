"""Reward navigation must lose authority when a person changes the screen."""
from types import SimpleNamespace

import numpy as np
import pytest

from interactions import event_rewards, missions, store


@pytest.fixture
def world(monkeypatch, tmp_path):
    from device import adbclient, capture
    from vision import textocr, wave_reader
    import settings

    def forbidden(*args, **kwargs):
        raise AssertionError('offline reward test attempted device I/O')

    monkeypatch.setattr(capture, 'grab', forbidden)
    monkeypatch.setattr(adbclient, 'shell', forbidden)
    monkeypatch.setattr(adbclient, 'exec_out', forbidden)
    monkeypatch.setattr(settings, 'template_path', lambda rel: tmp_path / rel)
    frames = {kind: np.zeros((2560, 1080, 3), np.uint8)
              for kind in ('battle_menu', 'battle', 'guild', 'guild_store', 'guild_guardian', 'quests', 'premium',
                           'events', 'event_shop', 'event_bots', 'reward', 'cards', 'modules', 'unknown')}
    names = {id(frame): kind for kind, frame in frames.items()}
    state = SimpleNamespace(kind='battle_menu', inputs=[], quest=False,
                            guild=False, free=False, event=False, milestone=False,
                            chests=[(300, 350), (650, 350)], parent=None)
    name = lambda frame: names[id(frame)]
    monkeypatch.setattr(missions, 'reward_screen', name)
    monkeypatch.setattr(store, 'reward_screen', name)
    monkeypatch.setattr(missions, 'return_target', lambda frame: (540, 2455))
    monkeypatch.setattr(missions, 'find_tile', lambda frame, *a: (100, 100)
                        if name(frame) == 'battle_menu' else None)
    monkeypatch.setattr(store, 'find_tile', missions.find_tile)
    monkeypatch.setattr(missions.logger, 'event', lambda *a, **k: None)
    monkeypatch.setattr(missions.logger, 'shot', lambda *a, **k: None)
    monkeypatch.setattr(missions, 'learn_lock_template', lambda frame: False)
    monkeypatch.setattr(missions, 'learn_claim_template', lambda *a: False)
    monkeypatch.setattr(missions, 'guild_claimables', lambda frame:
                        [(200, 708)] if name(frame) == 'guild' and not state.guild else [])
    monkeypatch.setattr(missions, 'missions_screen', lambda frame: name(frame) == 'quests')
    monkeypatch.setattr(missions, 'find_claim', lambda frame:
                        (400, 900) if name(frame) == 'quests' and not state.quest else None)
    monkeypatch.setattr(missions, 'claimable_chests', lambda frame:
                        list(state.chests) if name(frame) == 'quests' else [])
    monkeypatch.setattr(missions, 'find_skip', lambda frame:
                        (900, 375) if name(frame) == 'reward' else None)
    monkeypatch.setattr(event_rewards, 'reward_dismiss', lambda frame:
                        (900, 375) if name(frame) == 'reward' else None)
    monkeypatch.setattr(event_rewards, 'screen', lambda frame: name(frame) in {'events', 'event_shop', 'event_bots'})
    monkeypatch.setattr(event_rewards, 'claim_buttons', lambda frame, milestones=False:
                        ([(787, 470)] if not state.milestone else []) if milestones else
                        ([(400, 900)] if not state.event else []))
    markers = {'icons/guild_header.png': 'guild', 'icons/premium_store.png': 'premium',
               'icons/event_missions_tab.png': 'events'}

    def match(frame, rel, threshold):
        hit = (name(frame) == markers.get(rel)
               or rel == 'icons/guild_header.png' and name(frame) in {'guild_store', 'guild_guardian'}
               or rel == 'icons/event_missions_tab.png' and name(frame) in {'event_shop', 'event_bots'}
               or rel == 'icons/free_gems.png' and name(frame) == 'premium')
        return hit, 1.0 if hit else 0.0, (200, 500)

    monkeypatch.setattr(missions.detect, '_match', match)
    monkeypatch.setattr(missions.detect, '_tpl', lambda rel: np.ones((50, 100, 3), np.uint8))
    monkeypatch.setattr(textocr, 'read_lines', lambda *a: [(0, 0, '10H' if state.free else 'CLAIM REWARDS')])
    monkeypatch.setattr(wave_reader, 'read_wave', lambda frame: 100 if name(frame) == 'battle' else None)
    monkeypatch.setattr(store, 'read_balance', lambda frame: 100)
    monkeypatch.setattr(store, 'scan_frame', lambda frame, *a, **kw: store.StoreScan([], []))

    def tap(x, y, reason='', **kwargs):
        state.inputs.append((state.kind, 'tap', reason))
        if reason in {'guild_open', 'quests_open', 'gem_store_open', 'events_open'}:
            state.kind = {'guild_open': 'guild', 'quests_open': 'quests',
                          'gem_store_open': 'premium', 'events_open': 'events'}[reason]
        elif reason in {'guild_reward', 'quest_claim', 'weekly_chest', 'free_gems_claim',
                        'event_quest_claim', 'event_milestone_claim'}:
            state.parent = state.kind
            state.kind = 'reward'
            if reason == 'guild_reward': state.guild = True
            if reason == 'quest_claim': state.quest = True
            if reason == 'free_gems_claim': state.free = True
            if reason == 'event_quest_claim': state.event = True
            if reason == 'event_milestone_claim': state.milestone = True
            if reason == 'weekly_chest': state.chests.remove((x, y))
        elif reason in {'reward_skip', 'event_reward_skip', 'free_gems_reward_collect'}:
            state.kind = state.parent
        elif reason == 'return_to_game':
            state.kind = 'battle'
        elif reason == 'store_tab':
            state.kind = 'guild_store'
        elif reason == 'guild_members_tab':
            state.kind = 'guild'
        elif reason == 'event_missions_tab':
            state.kind = 'events'
        return {'dry_run': False, 'reason': reason}

    def swipe(*args, reason='', **kwargs):
        state.inputs.append((state.kind, 'swipe', reason))
        return {'dry_run': False}

    monkeypatch.setattr(missions.act, 'tap', tap)
    monkeypatch.setattr(missions.act, 'swipe', swipe)
    state.frames = frames
    state.step = lambda mission: mission.step(frames[state.kind])
    def reset():
        state.kind = 'battle_menu'
        state.inputs.clear()
        state.quest = state.guild = state.free = state.event = state.milestone = False
        state.chests = [(300, 350), (650, 350)]
        state.parent = None
    state.reset = reset
    return state


FLOWS = [missions.guild_flow, missions.quest_flow, missions.free_gems_flow,
         event_rewards.event_flow, store.store_flow]


@pytest.mark.parametrize('flow', FLOWS, ids=lambda flow: flow.__name__)
def test_owned_reward_paths_complete(world, flow):
    runner = missions.Mission()
    runner.start(flow)
    for _ in range(150):
        world.step(runner)
        if not runner.active:
            break
    assert not runner.active
    assert not runner.recovery_owned
    assert world.kind == 'battle'
    assert world.inputs[0][2] in {'guild_open', 'quests_open', 'gem_store_open', 'events_open'}
    assert world.inputs[-1][2] == 'return_to_game'
    if flow is missions.quest_flow:
        assert sum(reason == 'weekly_chest' for _, _, reason in world.inputs) == 2


@pytest.mark.parametrize('flow', FLOWS, ids=lambda flow: flow.__name__)
@pytest.mark.parametrize('foreign', ['cards', 'modules', 'unknown'])
def test_foreign_frame_revokes_continuation_and_later_recovery(world, flow, foreign):
    runner = missions.Mission()
    runner.start(flow)
    world.step(runner)  # the bot dispatched its own menu entry
    world.step(runner)  # the bot observed the destination
    before = len(world.inputs)
    world.kind = foreign
    for _ in range(missions.FlowGuard.TRANSITION_FRAMES + 2):
        world.step(runner)
    assert len(world.inputs) == before
    assert not runner.active
    assert not runner.recovery_owned
    # Returning to the old menu cannot resurrect a revoked generator/token.
    for kind in ('reward', 'guild', 'quests', 'premium', 'events'):
        world.kind = kind
        world.step(runner)
        runner.observe(world.frames[kind])
        assert not runner.recover(world.frames[kind])
    assert len(world.inputs) == before


@pytest.mark.parametrize('flow', FLOWS, ids=lambda flow: flow.__name__)
def test_a_visible_foreign_menu_cannot_establish_entry_ownership(world, flow):
    world.kind = 'guild'
    runner = missions.Mission()
    runner.start(flow)
    world.step(runner)
    assert not runner.active
    assert not runner.recovery_owned
    assert world.inputs == []


def test_crash_on_foreign_frame_does_not_call_blind_bail(world):
    def broken():
        yield
        raise RuntimeError('synthetic crash')

    runner = missions.Mission()
    runner.start(broken)
    world.kind = 'cards'
    world.step(runner)
    assert not runner.active
    assert not runner.recovery_owned
    assert world.inputs == []
    assert missions.bail(world.frames['unknown'], 'opening_timeout') is False


def test_owned_crash_exits_with_fresh_target_and_no_sticky_recovery(world, monkeypatch):
    @missions.reward_flow
    def broken(guard):
        frame = yield from guard.wait()
        guard.tap(frame, 100, 100, 'guild_open', screen='battle_menu', destination='guild')
        yield from guard.wait()
        raise RuntimeError('synthetic owned crash')

    runner = missions.Mission()
    runner.start(broken)
    world.step(runner)
    world.step(runner)
    assert world.inputs[-1] == ('guild', 'tap', 'return_to_game')
    world.step(runner)
    runner.observe(world.frames['battle'])
    assert not runner.recovery_owned


def test_missing_exit_evidence_never_uses_return_coordinate(world, monkeypatch):
    monkeypatch.setattr(missions, 'return_target', lambda frame: None)
    runner = missions.Mission()
    runner.start(store.store_flow)
    for _ in range(40):
        world.step(runner)
        if not runner.active: break
    assert not runner.active
    assert not runner.recovery_owned
    assert all(reason != 'return_to_game' for _, _, reason in world.inputs)


@pytest.mark.parametrize('dry', [False, True])
def test_refused_or_dry_entry_does_not_grant_ownership(world, monkeypatch, dry):
    def refused(*args, **kwargs):
        if dry: return {'dry_run': True}
        raise missions.act.TapRefused('offline refusal')
    monkeypatch.setattr(missions.act, 'tap', refused)
    runner = missions.Mission()
    runner.start(missions.guild_flow)
    world.step(runner)
    assert not runner.active
    assert not runner.recovery_owned
    assert world.inputs == []


def test_transition_wait_is_bounded_and_has_no_input(world):
    runner = missions.Mission()
    runner.start(missions.guild_flow)
    world.step(runner)
    world.kind = 'unknown'
    for _ in range(3): world.step(runner)
    assert runner.active
    assert len(world.inputs) == 1
    world.kind = 'guild'
    world.step(runner)
    assert world.inputs[-1][2] == 'guild_members_tab'
    world.kind = 'unknown'
    for _ in range(missions.FlowGuard.TRANSITION_FRAMES + 1): world.step(runner)
    assert not runner.active
    assert not runner.recovery_owned


def test_stale_owned_source_expires_without_destination(world):
    runner = missions.Mission()
    runner.start(missions.guild_flow)
    world.step(runner)
    world.kind = 'battle_menu'  # entry tap was swallowed
    for _ in range(20): world.step(runner)
    assert not runner.active
    assert not runner.recovery_owned
    assert len(world.inputs) == 1


@pytest.mark.parametrize('flow', FLOWS, ids=lambda flow: flow.__name__)
@pytest.mark.parametrize('foreign', ['cards', 'unknown'])
def test_interruption_at_every_yield_prevents_input_and_resumption(world, flow, foreign):
    runner = missions.Mission()
    runner.start(flow)
    steps = 0
    while runner.active and steps < 150:
        world.step(runner)
        steps += 1
    assert not runner.active and world.kind == 'battle'
    # Each yielded frame in the complete positive scenario is a separate
    # interruption point, including reward reveals, scrolls and final exits.
    for interruption in range(steps):
        world.reset()
        runner = missions.Mission()
        runner.start(flow)
        for _ in range(interruption): world.step(runner)
        before = list(world.inputs)
        world.kind = foreign
        for _ in range(missions.FlowGuard.TRANSITION_FRAMES + 2): world.step(runner)
        assert not runner.active, (flow.__name__, interruption)
        assert not runner.recovery_owned
        assert world.inputs == before, (flow.__name__, interruption)
        # A later recognized popup/menu must not revive the interrupted work.
        for kind in ('reward', 'guild', 'guild_store', 'quests', 'premium', 'events'):
            world.kind = kind
            runner.observe(world.frames[kind])
            world.step(runner)
            assert not runner.recover(world.frames[kind])
        assert world.inputs == before


@pytest.mark.parametrize('flow,foreign', [(missions.guild_flow, 'guild_store'),
                                       (store.store_flow, 'guild'),
                                       (event_rewards.event_flow, 'event_shop'),
                                       (event_rewards.event_flow, 'event_bots')])
def test_human_tab_change_revokes_ownership(world, flow, foreign):
    runner = missions.Mission()
    runner.start(flow)
    world.step(runner)  # open guild
    world.step(runner)  # dispatch the desired tab
    world.step(runner)  # observe its selected state
    before = list(world.inputs)
    world.kind = foreign
    world.step(runner)
    assert not runner.active
    assert not runner.recovery_owned
    assert world.inputs == before


def test_guild_entry_wildcard_collapses_to_observed_tab(world):
    guard = missions.FlowGuard()
    guard.observe(world.frames['battle_menu'])
    guard.tap(world.frames['battle_menu'], 100, 100, 'guild_open',
              screen='battle_menu', destination='guild_entry')
    guard.observe(world.frames['guild'])
    with pytest.raises(missions.FlowInterrupted):
        guard.observe(world.frames['guild_store'])
    assert not guard.owned


@pytest.mark.parametrize('flow,destination', [(missions.guild_flow,'guild'),
                                            (store.store_flow,'guild'),
                                            (event_rewards.event_flow,'events')])
@pytest.mark.parametrize('source_frames', [1,3])
def test_delayed_entry_keeps_destination_until_it_is_observed(world, flow, destination, source_frames):
    runner=missions.Mission()
    runner.start(flow)
    world.step(runner)  # own entry dispatched; old battle menu remains visible
    world.kind='battle_menu'
    for _ in range(source_frames):
        world.step(runner)
        assert runner.active
        assert len(world.inputs)==1
    world.kind=destination
    for _ in range(150):
        world.step(runner)
        if not runner.active: break
    assert not runner.active
    assert not runner.recovery_owned
    assert world.kind=='battle'
    assert world.inputs[-1][2]=='return_to_game'


def test_reward_permission_is_retired_when_parent_returns(world):
    runner = missions.Mission()
    runner.start(missions.guild_flow)
    for _ in range(8):
        world.step(runner)
        if world.inputs[-1][2] == 'reward_skip': break
    assert world.inputs[-1][2] == 'reward_skip'
    guard = runner._gen.guard
    guard.observe(world.frames['guild'])
    with pytest.raises(missions.FlowInterrupted):
        guard.observe(world.frames['reward'])
    assert not guard.owned


def test_recovery_is_revoked_by_foreign_screen_even_with_sticky_external_flag(world):
    @missions.reward_flow
    def unfinished(guard):
        frame = yield from guard.wait()
        guard.tap(frame,100,100,'guild_open',screen='battle_menu',destination='guild_entry')
        yield from guard.wait()
        return
    runner = missions.Mission()
    runner.start(unfinished)
    world.step(runner)
    world.step(runner)
    assert not runner.active and runner.recovery_owned
    before = list(world.inputs)
    external_state = SimpleNamespace(bot_left_battle=True)
    runner.observe(world.frames['cards'])
    assert external_state.bot_left_battle  # historical provenance is insufficient
    assert not runner.recover(world.frames['cards'])
    runner.observe(world.frames['guild'])
    assert not runner.recover(world.frames['guild'])
    assert world.inputs == before


def test_failed_guild_visit_cannot_borrow_previous_claim_count(world):
    missions.last_guild_claims = 4
    runner = missions.Mission()
    runner.start(missions.guild_flow)
    world.kind = 'cards'
    world.step(runner)
    assert missions.last_guild_claims == 0


def test_guild_tab_recognition_requires_a_single_selected_tab():
    from player.bootstrap_layout import manifest
    rects = manifest()['screens']['guild']['selected_tab']['rects']
    frame = np.zeros((2560,1080,3),np.uint8)
    assert missions.guild_surface(frame) == 'unknown'
    for index, expected in enumerate(('guild','guild_guardian','guild_store')):
        frame.fill(0)
        x,y,w,h = rects[index]
        frame[y:y+h,x:x+w] = 120
        assert missions.guild_surface(frame) == expected
    x,y,w,h = rects[0]
    frame[y:y+h,x:x+w] = 120
    assert missions.guild_surface(frame) == 'unknown'


def test_event_tab_recognition_requires_missions_for_continuation():
    from player.bootstrap_layout import manifest
    rects = manifest()['screens']['events']['selected_tab']['rects']
    frame = np.zeros((2560,1080,3),np.uint8)
    assert missions.event_surface(frame) == 'unknown'
    for index, expected in enumerate(('events','event_shop','event_bots')):
        frame.fill(0)
        x,y,w,h = rects[index]
        frame[y:y+h,x:x+w] = 120
        assert missions.event_surface(frame) == expected


def test_classifier_preserves_flow_markers_and_text_only_reveals(monkeypatch):
    from vision import screen
    monkeypatch.setattr(screen, 'identify', lambda frame: SimpleNamespace(name='unknown'))
    monkeypatch.setattr(missions, 'find_skip', lambda frame: None)
    monkeypatch.setattr(event_rewards, 'screen', lambda frame: False)
    monkeypatch.setattr(event_rewards, 'reward_dismiss', lambda frame: (500,1900) if frame=='reveal' else None)
    marker_for = {'quests':'icons/daily_missions.png', 'premium':'icons/premium_store.png'}
    def match(frame, rel, threshold):
        assert threshold == .75  # preserve the established flow detector threshold
        return rel == marker_for.get(frame),1,(0,0)
    monkeypatch.setattr(missions.detect,'_match',match)
    assert missions.reward_screen('quests') == 'quests'
    assert missions.reward_screen('premium') == 'premium'
    assert missions.reward_screen('reveal') == 'reward'
    assert missions.reward_screen('unknown') == 'unknown'


def test_global_guild_header_still_requires_selected_tab_proof(monkeypatch):
    from vision import screen
    from player.bootstrap_layout import manifest
    monkeypatch.setattr(screen,'identify',lambda frame:SimpleNamespace(name='guild'))
    monkeypatch.setattr(missions.detect,'_match',lambda *a:(False,0,(0,0)))
    monkeypatch.setattr(missions,'find_skip',lambda frame:None)
    monkeypatch.setattr(event_rewards,'screen',lambda frame:False)
    monkeypatch.setattr(event_rewards,'reward_dismiss',lambda frame:None)
    frame=np.zeros((2560,1080,3),np.uint8)
    assert missions.reward_screen(frame)=='unknown'
    x,y,w,h=manifest()['screens']['guild']['selected_tab']['rects'][2]
    frame[y:y+h,x:x+w]=120
    assert missions.reward_screen(frame)=='guild_store'


@pytest.mark.parametrize('enabled', [False, True])
def test_outer_recovery_uses_current_permission_and_rewards_gate(world, enabled):
    from orchestrator import recover_reward_menu
    @missions.reward_flow
    def unfinished(guard):
        frame=yield from guard.wait()
        guard.tap(frame,100,100,'guild_open',screen='battle_menu',destination='guild_entry')
        yield from guard.wait()
    runner=missions.Mission()
    runner.start(unfinished)
    world.step(runner)
    world.step(runner)
    assert runner.recovery_owned
    rs=SimpleNamespace(mission=runner,rewards_enabled=enabled,bot_left_battle=True,
                       off_battle_since=0,recover_try=0)
    before=list(world.inputs)
    assert recover_reward_menu(rs,world.frames['guild'],100) is enabled
    if enabled:
        assert world.inputs[-1]==('guild','tap','return_to_game')
    else:
        assert world.inputs==before
    # A foreign surface must revoke even while Rewards OFF prevents input.
    recover_reward_menu(rs,world.frames['cards'],101)
    assert not runner.recovery_owned
    rs.rewards_enabled=True
    before=list(world.inputs)
    assert not recover_reward_menu(rs,world.frames['guild'],200)
    assert world.inputs==before


def test_exit_target_rejects_missing_dimmed_and_ambiguous_images(monkeypatch):
    rng = np.random.default_rng(777)
    template = rng.integers(20,240,(45,100,3),dtype=np.uint8)
    monkeypatch.setattr(missions.detect,'_tpl',lambda rel:template)
    frame=np.zeros((2560,1080,3),np.uint8)
    frame[2435:2480,240:340]=template
    assert missions.return_target(frame)==(290,2457)
    frame[2435:2480,240:340]=(template*.3).astype(np.uint8)
    assert missions.return_target(frame) is None
    frame[2435:2480,240:340]=template
    frame[2435:2480,440:540]=template
    assert missions.return_target(frame) is None
    def missing(rel): raise missions.detect.TemplateMissing(rel)
    monkeypatch.setattr(missions.detect,'_tpl',missing)
    assert missions.return_target(frame) is None
