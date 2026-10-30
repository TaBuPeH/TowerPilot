"""A fake world for `gem_collector.main`: the session, the gem routines, the screen and the clock are fakes.

Shared by the collector's loop tests (ledger lock, stage timers, pacing). `run` feeds the frames in order and stops with
`Exhausted` when they are used up; `returned` says whether main left by itself."""
import types


class Exhausted(Exception):
    pass


def run(monkeypatch, frames, *, session=None, requested=None, fence=None, gems=None, sleep=None, clock=None,
        grabbed=None, screen=None, read_wave=None):
    """Returns (events, polled, returned, gems). `gems` defaults to routines that record each polled frame."""
    import gem_collector as worker
    from runtime import action_session, battle_session, device_lease, runners
    events, polled = [], []
    monkeypatch.setattr(device_lease, 'prepare_instance', lambda name: None)
    monkeypatch.setattr(worker.settings, 'select_instance', lambda *a: None)
    monkeypatch.setattr(runners, 'other_runners', lambda: iter([]))
    monkeypatch.setattr(worker, 'automatic_fence', lambda: fence)
    monkeypatch.setattr(battle_session.BattleSession, 'from_environment', classmethod(lambda cls, *a, **kw: session))
    monkeypatch.setattr(action_session.ActionSession, 'for_battle', classmethod(lambda cls, *a, **kw: None))
    if gems is None:
        gems = types.SimpleNamespace(ad_gems=types.SimpleNamespace(enabled=True),
                                     orbit=types.SimpleNamespace(enabled=True), busy=lambda: [])
        gems.poll = lambda frame, reads=None: polled.append(frame)
    monkeypatch.setattr(worker, 'GemWatch', lambda gather=None, **kw: gems)
    remaining = iter(frames)

    def grab():
        try:
            frame = next(remaining)
        except StopIteration:
            raise Exhausted from None
        if grabbed is not None:
            grabbed.append(frame)
        return frame
    monkeypatch.setattr(worker.capture, 'grab', grab)
    monkeypatch.setattr(worker.screen, 'identify', screen or (lambda f: types.SimpleNamespace(name=f)))
    monkeypatch.setattr(worker.wave_reader, 'read_wave', read_wave or (lambda f: 100 if f == 'battle' else None))
    monkeypatch.setattr(worker.time, 'sleep', sleep or (lambda seconds: None))
    monkeypatch.setattr(worker.runflag, 'requested', lambda: requested)
    monkeypatch.setattr(worker.logger, 'event', lambda name, **kw: events.append((name, kw)))
    if clock is not None:
        monkeypatch.setattr(worker, 'clock', clock)
    try:
        worker.main('main', 'bp_farm')
        returned = True
    except Exhausted:
        returned = False
    return events, polled, returned, gems