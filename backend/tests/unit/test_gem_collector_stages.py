"""Per-pass stage timers (FOCUS #11 step 1): where each collector pass spends its seconds."""
import types

from gem_collector_run import run
from runtime.stage_clock import StageClock


def fake_time():
    now = [0.0]
    return now, lambda: now[0]


def test_the_clock_aggregates_stages_over_passes_and_resets_after_a_heartbeat():
    now, read = fake_time()
    clock = StageClock(now=read)
    assert clock.next_pass(heartbeat_sec=10) is None                  # the first call only starts pass 1
    beats = []
    for poll in (1.0, 1.0, 2.0, 1.0, 1.0, 1.0):                       # six passes of 1.5, 1.5, 2.5, 1.5, 1.5, 1.5 s
        with clock.stage('capture'):
            now[0] += 0.5
        with clock.stage('poll'):
            now[0] += poll - 0.5
            with clock.stage('ad.wave'):                               # nested: overlaps its parent
                now[0] += 0.5
        beats.append(clock.next_pass(heartbeat_sec=10))
    assert beats[:-1] == [None] * 5
    beat = beats[-1]
    assert (beat['window_sec'], beat['passes'], beat['period_mean_sec'], beat['period_max_sec']) == (10.0, 6, 1.667, 2.5)
    assert beat['stages']['capture'] == dict(mean_sec=0.5, max_sec=0.5, passes=6, calls=6)
    assert beat['stages']['ad.wave'] == dict(mean_sec=0.5, max_sec=0.5, passes=6, calls=6)
    assert (beat['stages']['poll']['mean_sec'], beat['stages']['poll']['max_sec']) == (1.167, 2.0)
    assert clock.next_pass(heartbeat_sec=10) is None                   # a new window starts empty


def test_a_stage_reports_its_own_seconds_into_a_dict_even_when_it_raises():
    now, read = fake_time()
    clock = StageClock(now=read)
    spent = {}
    try:
        with clock.stage('ad.claim', into=spent):
            now[0] += 2.5
            raise ValueError
    except ValueError:
        pass
    assert spent == {'ad.claim': 2.5}


def test_the_collector_logs_one_stage_heartbeat_a_minute_with_every_stage_it_ran(monkeypatch):
    import gem_collector as worker
    now, read = fake_time()
    clock = StageClock(now=read)
    monkeypatch.setattr(worker.time, 'monotonic', read)      # the pacing reads the same clock

    def slow(seconds, result):
        def read_with_cost(frame):
            now[0] += seconds
            return result(frame)
        return read_with_cost
    events, polled, returned, _ = run(
        monkeypatch, ['battle'] * 60, clock=clock,
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        screen=slow(0.25, lambda f: types.SimpleNamespace(name=f)),
        read_wave=slow(0.1, lambda f: 100))
    beats = [kw for name, kw in events if name == 'gem_collector_stages']
    assert not returned and len(beats) == 1
    beat = beats[0]
    assert beat['stages']['identify']['mean_sec'] == 0.25
    assert beat['stages']['wave']['mean_sec'] == 0.1                    # one read of the pass (it was two before step 2)
    assert beat['stages']['sleep']['mean_sec'] == 0.65         # what is left of the 1 s target period
    assert beat['period_mean_sec'] == 1.0
    assert {'capture', 'identify', 'wave', 'poll', 'sleep'} <= set(beat['stages'])