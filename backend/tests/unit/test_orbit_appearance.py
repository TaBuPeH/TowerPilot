"""Only recognized appearances open automatic attempts on supplied frames."""
from types import SimpleNamespace

import pytest

from flows import shard
from test_gem_orbit_cycle import cycle  # noqa: F401 - pytest fixture injection


def test_idle_unknown_reuses_supplied_frames_without_capture_or_attempt(cycle, monkeypatch):
    state, tapper = cycle
    monkeypatch.setattr(shard, 'find_orbit_gem', lambda *a, **kw: None)
    monkeypatch.setattr(shard.capture, 'grab', lambda: pytest.fail('idle sensing must not capture'))
    monkeypatch.setattr(shard.wave_reader, 'read_wave', lambda _: pytest.fail('unknown presence needs no wave read'))
    monkeypatch.setattr(shard.screen, 'identify', lambda _: pytest.fail('unknown presence needs no classification'))
    for state.now in [100, 159, 300, 1060, 2000]:
        assert tapper.poll(200) == 200
    # The rule is unchanged: no capture, no wave read, no classification and no tap (above). What changed (FOCUS #4,
    # GC-01): this path used to leave no trace at all, so a finder that stopped matching was invisible. It now counts
    # the frames it was handed, and logs that count once a battle minute, with no new read.
    assert [kind for kind, _ in state.events] == ['gem_orbit_sensing'] * 3 and state.taps == []
    assert tapper.attempt_started is None
    assert (tapper.sensing()['frames'], tapper.sensing()['recognized']) == (5, 0)


def test_visible_gem_opens_attempt_between_old_scheduled_windows(cycle, monkeypatch):
    state, tapper = cycle
    detector = shard.find_orbit_gem
    monkeypatch.setattr(shard, 'find_orbit_gem', lambda *a, **kw: None)
    tapper.poll(200)
    state.now = 300
    monkeypatch.setattr(shard, 'find_orbit_gem', detector)
    tapper.poll(200)
    assert len(state.taps) == 3
    assert tapper.attempt_started == 300 and tapper.window_ends_at == 360


def test_idle_presence_needs_battle_classification_and_wave(cycle, monkeypatch):
    state, tapper = cycle
    monkeypatch.setattr(shard.screen, 'identify', lambda _: SimpleNamespace(name='lab'))
    monkeypatch.setattr(shard.capture, 'grab', lambda: pytest.fail('no battle'))
    tapper.poll(200)
    assert tapper.attempt_started is None and state.taps == []


def test_success_rearms_appearance_sensing_after_existing_input_pacing(cycle):
    state, tapper = cycle
    tapper.poll(200)
    state.now, state.balance = 101, 202
    tapper.poll(202)
    assert tapper.last_outcome['confirmed']
    state.now = 104
    tapper.poll(202)
    assert len(state.taps) == 3
    state.now = 105
    tapper.poll(202)
    assert len(state.taps) == 6 and tapper.balance_before == 202


def test_interval_edit_does_not_reintroduce_schedule_after_success(cycle):
    state, tapper = cycle
    tapper.poll(200)
    state.now, state.balance = 101, 202
    tapper.poll(202)
    tapper.configure({'interval_sec': 3600})
    state.now = 105
    tapper.poll(202)
    assert len(state.taps) == 6


def test_timeout_duration_is_independent_of_failed_attempt_spacing(cycle):
    state, tapper = cycle
    tapper.interval = 10
    tapper.poll(200)
    assert tapper.window_ends_at == 160
    state.now = 160
    tapper.poll(200)
    assert not tapper.last_outcome['confirmed'] and len(state.taps) == 3
    assert tapper.next_at >= 175  # safety backoff still separates failed attempts


def test_reapplying_controls_cannot_erase_timeout_safety_backoff(cycle):
    state, tapper = cycle
    tapper.interval = 10
    tapper.poll(200)
    state.now = 160
    tapper.poll(200)
    tapper.configure({'interval_sec': 10})
    assert tapper.next_at == 175
    state.now = 161
    tapper.poll(200)
    assert len(state.taps) == 3


def test_appearance_does_not_bypass_refusal_backoff(cycle, monkeypatch):
    state, tapper = cycle
    monkeypatch.setattr(shard, 'battlefield_observation', lambda *a: (False, 'static', None))
    tapper.poll(200)
    assert tapper.next_at == 115
    monkeypatch.setattr(shard, 'battlefield_observation', lambda *a: (True, 'live', a[1]))
    state.now = 101
    tapper.poll(200)
    assert state.taps == []
    state.now = 115
    tapper.poll(200)
    assert len(state.taps) == 3


def test_disabled_appearance_check_has_no_detector_or_capture_work(cycle, monkeypatch):
    state, tapper = cycle
    tapper.enabled = False
    monkeypatch.setattr(shard, 'find_orbit_gem', lambda *a, **kw: pytest.fail('disabled detector'))
    monkeypatch.setattr(shard.capture, 'grab', lambda: pytest.fail('disabled capture'))
    tapper.poll(200)
    assert state.events == [] and state.taps == []


@pytest.mark.parametrize('dry', [False, True])
def test_timeout_without_dispatched_input_retries_after_safety_backoff(cycle, dry):
    state, tapper = cycle
    state.dry=dry
    state.balance=200 if dry else None
    tapper.poll(state.balance)
    dispatched=len(state.taps)
    assert not tapper.sent_tap
    state.now=160
    tapper.poll(state.balance)
    assert tapper.next_at==175
    state.now,state.balance,state.dry=175,200,False
    tapper.poll(200)
    assert len(state.taps)==dispatched+3


def test_unknown_baseline_uses_supplied_pixels_without_an_extra_capture(cycle, monkeypatch):
    state,tapper=cycle
    state.balance=None
    monkeypatch.setattr(shard.capture,'grab',lambda:pytest.fail('no baseline to validate'))
    tapper.poll(None)
    assert state.taps==[] and tapper.attempt_started==100


def test_conflicting_counter_clears_proof_but_keeps_dispatched_attempt_spacing(cycle):
    state,tapper=cycle
    tapper.poll(200)
    state.now,state.balance=105,206
    tapper.poll(206)
    assert tapper.balance_before is None and not tapper.sent_tap
    state.now=160
    tapper.poll(206)
    assert tapper.next_at==1060
    tapper.configure({'interval_sec':1200})
    assert tapper.next_at==1300


def test_interval_edit_cannot_impose_input_spacing_on_an_attempt_with_no_taps(cycle):
    state,tapper=cycle
    state.balance=None
    tapper.poll(None)
    state.now=160
    tapper.poll(None)
    tapper.configure({'interval_sec':3600})
    assert tapper.next_at==175


def test_timeout_preserves_tap_set_pacing_across_attempts(cycle):
    state,tapper=cycle
    tapper.interval,tapper.retry_interval,tapper.attempt_timeout,tapper.retry_backoff=1,30,5,1
    tapper.poll(200)
    state.now=105
    tapper.poll(200)
    assert tapper.next_at==130
    state.now=106
    tapper.poll(200)
    assert len(state.taps)==3
