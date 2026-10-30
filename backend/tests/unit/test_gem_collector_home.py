"""The collector's Home branch (r15c): which frames reach `AdGemCollector.poll_home`, and under which conditions."""
import types

import pytest


class Exhausted(Exception):
    pass


def run(monkeypatch, frames, *, fence=True, enabled=True, battle=None, refuse_at=None):
    import gem_collector as worker
    from runtime import runners, device_lease
    from runtime import battle_session, action_session
    calls, checkpoints, fences = [], [], []
    monkeypatch.setattr(device_lease, 'prepare_instance', lambda name: None)
    monkeypatch.setattr(worker.settings, 'select_instance', lambda *a: None)
    monkeypatch.setattr(runners, 'other_runners', lambda: iter([]))

    def check():
        fences.append(1)
        calls.append(('fence',))
        if refuse_at is not None and len(fences) >= refuse_at:
            raise RuntimeError('harvesting no longer asks for this collector')
    monkeypatch.setattr(worker, 'automatic_fence', lambda: check if fence else None)
    session = None
    if battle is not None:
        session = types.SimpleNamespace(
            battle=dict(status=battle, id='b1'), effective_preset=lambda: {},
            checkpoint=lambda state, **kw: checkpoints.append(kw), observe=lambda state, raw: True,
            finish=lambda *a, **kw: None, prepare_next=lambda **kw: None)
    monkeypatch.setattr(battle_session.BattleSession, 'from_environment', classmethod(lambda cls, *a, **kw: session))
    monkeypatch.setattr(action_session.ActionSession, 'for_battle', classmethod(lambda cls, *a, **kw: None))
    ad = types.SimpleNamespace(enabled=enabled, poll_home=lambda f, reads=None: calls.append(('poll_home', f)),
                               leave_home=lambda: calls.append(('leave_home',)))
    monkeypatch.setattr(worker, 'GemWatch', lambda gather=None, **kw: types.SimpleNamespace(
        ad_gems=ad, orbit=types.SimpleNamespace(enabled=True), poll=lambda f, reads=None: calls.append(('poll', f))))
    remaining = iter(frames)

    def grab():
        try:
            return next(remaining)
        except StopIteration:
            raise Exhausted from None
    monkeypatch.setattr(worker.capture, 'grab', grab)
    monkeypatch.setattr(worker.screen, 'identify', lambda f: types.SimpleNamespace(name=f))
    monkeypatch.setattr(worker.wave_reader, 'read_wave', lambda f: 100 if f == 'battle' else None)
    monkeypatch.setattr(worker.time, 'sleep', lambda _: None)
    monkeypatch.setattr(worker.runflag, 'requested', lambda: None)
    monkeypatch.setattr(worker.logger, 'event', lambda name, **kw: calls.append(('event', name, kw.get('state'))))
    with pytest.raises((Exhausted, RuntimeError)) as stopped:
        worker.main('main', 'bp_farm')
    return [c for c in calls if c[0] != 'event'], [c for c in calls if c[0] == 'event'], checkpoints, fences, stopped.value


def test_a_harvesting_collector_polls_only_exact_home_frames_and_leaves_home_on_anything_else(monkeypatch):
    calls, events, _, fences, _ = run(monkeypatch, ['home', 'home', 'unknown', 'modules', 'home', 'battle'])
    # The startup fence, then the fence immediately before every Home poll.
    assert calls == [('fence',), ('fence',), ('poll_home', 'home'), ('fence',), ('poll_home', 'home'),
                     ('leave_home',), ('leave_home',), ('fence',), ('poll_home', 'home'), ('poll', 'battle')]
    assert ('event', 'gem_collector_state', 'claiming_home_offer') in events


def test_a_collector_a_person_started_never_acts_on_home(monkeypatch):
    calls, events, _, fences, _ = run(monkeypatch, ['home', 'home', 'unknown'], fence=False)
    assert calls == [] and fences == []
    assert ('event', 'gem_collector_state', 'claiming_home_offer') not in events


def test_a_disabled_ad_gem_routine_never_acts_on_home(monkeypatch):
    calls, _, _, _, _ = run(monkeypatch, ['home', 'home'], enabled=False)
    assert calls == [('fence',), ('leave_home',), ('leave_home',)]


def test_no_home_claim_while_a_battle_is_still_active(monkeypatch):
    calls, _, checkpoints, _, _ = run(monkeypatch, ['home', 'home'], battle='active')
    assert ('poll_home', 'home') not in calls


def test_home_polls_keep_the_prepared_battle_published_with_the_offer_still_observed(monkeypatch):
    calls, _, checkpoints, _, _ = run(monkeypatch, ['home', 'home'], battle='prepared')
    assert calls == [('fence',), ('fence',), ('poll_home', 'home'), ('fence',), ('poll_home', 'home')]
    assert checkpoints == [dict(battle_visible=False, observed=('ad_gems',))] * 2


def test_a_fence_refusal_on_home_stops_the_collector_before_any_home_poll(monkeypatch):
    calls, _, _, _, stopped = run(monkeypatch, ['home', 'home'], refuse_at=2)
    assert isinstance(stopped, RuntimeError) and calls == [('fence',), ('fence',)]