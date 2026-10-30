"""Orbit collection has bounded windows; only verified changes count as hits."""

import paths as test_paths
from types import SimpleNamespace

import pytest

from flows import shard
from player.gather_settings import ORBIT_TIMING_FIELDS, validate_orbit_timing


@pytest.fixture
def cycle(monkeypatch):
    state = SimpleNamespace(now=100., balance=200, taps=[], events=[], dry=False)
    monkeypatch.setattr(shard.time, 'monotonic', lambda: state.now)
    monkeypatch.setattr(shard.capture, 'grab', lambda: state.balance)
    monkeypatch.setattr(shard.capture, 'layout_offset', 0)
    monkeypatch.setattr(shard.wave_reader, 'read_wave', lambda frame: 123)
    monkeypatch.setattr(shard.screen, 'identify', lambda frame: SimpleNamespace(name='battle'))
    monkeypatch.setattr(shard, 'read_gem_balance', lambda frame: frame)
    monkeypatch.setattr(shard, 'find_orbit_gem', lambda frame, **kw:
                        SimpleNamespace(center=(680., 805.), rect=(640, 765, 80, 80), score=.99))
    monkeypatch.setattr(shard, 'battlefield_observation', lambda *a: (True, 'live', a[1]))
    monkeypatch.setattr(shard, '_in_ability_row', lambda p: False)
    monkeypatch.setattr(shard.logger, 'event', lambda kind, **kw: state.events.append((kind, kw)))
    monkeypatch.setattr(shard.act, 'tap', lambda *a, **kw: state.taps.append(a) or {'dry_run': state.dry})
    return state, shard.GemOrbitTapper(enabled=True, jitter=0)


def test_missing_gem_waits_until_next_scheduled_window(cycle):
    state, tapper = cycle
    tapper.poll(200)
    assert len(state.taps) == 3
    assert tapper.next_at == 105
    state.now = 105
    tapper.poll(200)
    assert len(state.taps) == 6
    state.now = 160
    tapper.poll(200)
    assert len(state.taps) == 6
    assert tapper.next_at == 1060
    assert ('gem_orbit_claim', dict(confirmed=False, reason='attempt_timeout', before=200, after=None)) in state.events
    state.now = 1059
    tapper.poll(200)
    assert len(state.taps) == 6
    state.now = 1060
    tapper.poll(200)
    assert len(state.taps) == 9


def test_latest_refusal_keeps_actual_time_and_correlation_without_claiming_reward(cycle, monkeypatch):
    state, tapper = cycle
    monkeypatch.setattr(shard.time, 'time', lambda: 1000+state.now)
    tapper.request_now()  # Explicit attempts still report a missing-wave refusal.
    monkeypatch.setattr(shard.wave_reader, 'read_wave', lambda _: None)
    tapper.poll(200)
    refused = tapper.status()['last_refusal']
    assert refused == dict(reason='no_wave', observed_at=1100,
                           window_id=tapper.window_id, attempt_id=tapper.attempt_id)
    assert refused['window_id'] and refused['attempt_id']
    assert state.taps == [] and tapper.status()['last_outcome'] is None
    state.now = 102
    assert tapper.status()['last_refusal'] == refused
    state.now = 160
    tapper.poll(200)
    assert tapper.status()['last_outcome']['confirmed'] is False
    assert tapper.status()['last_refusal'] == refused


@pytest.mark.parametrize('reason', ['no_wave', 'balance_unreadable', 'static'])
def test_refusals_do_not_restart_or_extend_collection_window(cycle, monkeypatch, reason):
    state, tapper = cycle
    if reason == 'no_wave':
        tapper.request_now()
        monkeypatch.setattr(shard.wave_reader, 'read_wave', lambda _: None)
    elif reason == 'balance_unreadable':
        state.balance = None
    else:
        monkeypatch.setattr(shard, 'battlefield_observation', lambda *a: (False, 'static', None))
    for now in [100, 115, 130, 145, 160]:
        state.now = now
        tapper.poll(state.balance)
    assert state.taps == []
    assert tapper.next_at == 175  # No dispatched input consumes long retry spacing.
    assert len([e for k, e in state.events if k == 'gem_orbit_window']) == 1


def test_short_failed_attempt_spacing_does_not_shorten_active_deadline(cycle):
    state, tapper = cycle
    tapper.interval = 10
    tapper.poll(200)
    state.now = 110
    tapper.poll(200)
    assert len(state.taps) == 6
    assert tapper.window_ends_at == 160


def test_stable_two_gem_increase_ends_window_early(cycle, monkeypatch):
    state, tapper = cycle
    tapper.poll(200)
    state.balance = 202
    state.now = 105
    tapper.poll(200)  # old input/new capture: not yet stable
    assert tapper.next_at == 110
    assert not any(k == 'gem_orbit_claim' for k, _ in state.events)
    state.now = 110
    tapper.poll(202)
    assert len(state.taps) == 3
    pause = 0
    assert tapper.next_at == 110
    assert state.events[-1] == ('gem_orbit_claim', dict(confirmed=True, before=200, after=202, pause_sec=round(pause, 3)))
    state.now = 110
    monkeypatch.setattr(shard, 'find_orbit_gem', lambda *a, **kw: None)
    tapper.poll(202)
    assert len(state.taps) == 3


def test_confirmed_collection_does_not_suppress_an_earlier_new_appearance(cycle):
    state, tapper = cycle
    tapper.poll(200)
    state.balance = 202
    state.now = 105
    tapper.poll(202)
    assert tapper.next_at == 105
    state.now = 240
    tapper.poll(202)
    assert len(state.taps) == 6


@pytest.mark.parametrize('now', [160, 161, 200])
def test_expired_attempt_cannot_confirm_late_balance_increase(cycle, now):
    state, tapper = cycle
    tapper.poll(200)
    state.balance = 202
    state.now = now
    tapper.poll(202)
    assert len(state.taps) == 3
    assert tapper.next_at == 1060
    assert ('gem_orbit_claim', dict(confirmed=False, reason='attempt_timeout', before=200, after=None)) in state.events
    assert not any(k == 'gem_orbit_claim' and e['confirmed'] for k, e in state.events)


def test_capture_crossing_deadline_stops_remaining_taps(cycle, monkeypatch):
    state, tapper = cycle
    def grab():
        if state.taps:
            state.now = 160
        return state.balance
    monkeypatch.setattr(shard.capture, 'grab', grab)
    tapper.poll(200)
    assert len(state.taps) == 1
    assert tapper.next_at == 1060
    assert ('gem_orbit_claim', dict(confirmed=False, reason='attempt_timeout', before=200, after=200)) in state.events


def test_stop_remaining_taps_as_soon_as_balance_increases(cycle, monkeypatch):
    state, tapper = cycle
    def tap(*a, **kw):
        state.taps.append(a)
        state.balance = 202
        return {}
    monkeypatch.setattr(shard.act, 'tap', tap)
    assert tapper.poll(200) == 202
    assert len(state.taps) == 1
    assert not any(k == 'gem_orbit_claim' for k, _ in state.events)


@pytest.mark.parametrize('balance', [None, 206, 208, 199, 1200])
def test_unreadable_or_unrelated_rewards_never_confirm(cycle, balance):
    state, tapper = cycle
    tapper.poll(200)
    state.balance = balance
    state.now = 105
    tapper.poll(balance)
    assert len(state.taps) == 3
    assert not any(k == 'gem_orbit_claim' and e['confirmed'] for k, e in state.events)
    assert tapper.next_at == (105 if balance is None else 120)


def test_unreadable_baseline_does_not_tap(cycle):
    state, tapper = cycle
    state.balance = None
    tapper.poll(None)
    assert state.taps == []
    assert state.events[-1] == ('gem_orbit_skip', {'reason': 'balance_unreadable'})


@pytest.mark.parametrize('observed', [202, 206])
def test_unknown_motion_gem_cannot_hide_a_readable_changed_balance(cycle, monkeypatch, observed):
    state, tapper = cycle
    tapper.poll(200)
    detector = shard.find_orbit_gem
    monkeypatch.setattr(shard, 'battlefield_observation', lambda *a: (True, 'live', observed))
    monkeypatch.setattr(shard, 'find_orbit_gem', lambda frame, **kw: None if frame == observed else detector(frame, **kw))
    state.now = 105
    tapper.poll(200)
    assert len(state.taps) == 3
    assert not any(kind == 'gem_orbit_claim' and data['confirmed'] for kind, data in state.events)
    assert tapper.balance_before == (200 if observed == 202 else None)
    assert tapper.sent_tap == (observed == 202)


@pytest.mark.parametrize('unknown', ['balance', 'gem'])
def test_transient_unknown_retries_the_next_supplied_frame(cycle, monkeypatch, unknown):
    state, tapper = cycle
    detector = shard.find_orbit_gem
    if unknown == 'balance':
        state.balance = None
    else:
        monkeypatch.setattr(shard, 'find_orbit_gem', lambda *a, **kw: None)
    tapper.poll(state.balance)
    assert state.taps == []
    state.now, state.balance = 101, 200
    monkeypatch.setattr(shard, 'find_orbit_gem', detector)
    tapper.poll(200)
    assert len(state.taps) == 3
    assert tapper.window_ends_at == (161 if unknown == 'gem' else 160)


def test_partial_set_unknown_preserves_actual_input_pacing(cycle, monkeypatch):
    state, tapper = cycle
    frames = iter([200, None])
    monkeypatch.setattr(shard.capture, 'grab', lambda: next(frames))
    tapper.poll(200)
    assert len(state.taps) == 1
    monkeypatch.setattr(shard.capture, 'grab', lambda: state.balance)
    state.now = 101
    tapper.poll(200)
    assert len(state.taps) == 1
    state.now = 105
    tapper.poll(200)
    assert len(state.taps) == 4


@pytest.mark.parametrize('reason', ['static', 'screen:home', 'windows_unreadable:OSError'])
def test_hard_safety_backoff_still_blocks_early_input(cycle, monkeypatch, reason):
    state, tapper = cycle
    monkeypatch.setattr(shard, 'battlefield_observation', lambda *a: (False, reason, None))
    tapper.poll(200)
    assert tapper.next_at == 115 and state.taps == []
    monkeypatch.setattr(shard, 'battlefield_observation', lambda *a: (True, 'live', a[1]))
    state.now = 101
    tapper.poll(200)
    assert state.taps == []
    state.now = 115
    tapper.poll(200)
    assert len(state.taps) == 3


def test_consecutive_windows_after_unknowns_still_confirm_only_stable_plus_two(cycle):
    state, tapper = cycle
    for start, before in [(100, 200), (1060, 202)]:
        state.now, state.balance = start, None
        tapper.poll(None)
        state.now, state.balance = start+1, before
        tapper.poll(before)
        assert tapper.balance_before == before and tapper.sent_tap
        state.now, state.balance = start+2, before+2
        tapper.poll(before+2)
        assert tapper.last_outcome['confirmed']
    claims = [data for kind, data in state.events if kind == 'gem_orbit_claim']
    assert [(data['before'], data['after']) for data in claims] == [(200, 202), (202, 204)]
    assert len(state.taps) == 6


def test_taps_follow_measured_radius_instead_of_reference(cycle, monkeypatch):
    import math
    state, tapper = cycle
    center, _ = tapper._orbit_geometry()
    monkeypatch.setattr(shard, 'find_orbit_gem', lambda frame, **kw:
                        SimpleNamespace(center=(center[0]+343, center[1]), rect=(0, 0, 80, 80), score=.99))
    tapper.poll(200)
    assert len(state.taps) == 3
    assert all(abs(math.dist(point, center)-343) < 1 for point in state.taps)


@pytest.mark.parametrize('stage', ['fresh', 'pre_tap'])
def test_unknown_gem_stops_input_without_erasing_pending_confirmation(cycle, monkeypatch, stage):
    state, tapper = cycle
    tapper.poll(200)
    dispatched = len(state.taps)
    good = shard.find_orbit_gem(None)
    observations = iter([None] if stage == 'fresh' else [good, None])
    monkeypatch.setattr(shard, 'find_orbit_gem', lambda frame, **kw: next(observations, None))
    state.now = 105
    tapper.poll(200)
    assert len(state.taps) == dispatched
    assert tapper.balance_before == 200 and tapper.sent_tap
    assert state.events[-1] == ('gem_orbit_skip', dict(reason='gem_not_detected'))
    state.now = tapper.next_at
    state.balance = 202
    tapper.poll(202)
    assert state.events[-1][0] == 'gem_orbit_claim' and state.events[-1][1]['confirmed']


def test_camera_radius_change_before_dispatch_refuses(cycle, monkeypatch):
    state, tapper = cycle
    center, _ = tapper._orbit_geometry()
    sightings = iter([170, 170, 343])
    monkeypatch.setattr(shard, 'find_orbit_gem', lambda frame, **kw:
                        SimpleNamespace(center=(center[0]+next(sightings, 343), center[1]),
                                        rect=(0, 0, 80, 80), score=.99))
    tapper.poll(200)
    assert state.taps == []
    assert state.events[-1] == ('gem_orbit_skip', dict(reason='gem_geometry_changed'))


def test_no_verified_gem_cannot_start_a_tap_set(cycle, monkeypatch):
    state, tapper = cycle
    monkeypatch.setattr(shard, 'find_orbit_gem', lambda frame, **kw: None)
    tapper.poll(200)
    assert state.taps == [] and tapper.balance_before is None
    assert state.events == [] and tapper.attempt_started is None


def test_unknown_gem_cannot_hide_a_conflicting_balance(cycle, monkeypatch):
    state, tapper = cycle
    tapper.poll(200)
    dispatched = len(state.taps)
    monkeypatch.setattr(shard, 'find_orbit_gem', lambda frame, **kw: None)
    state.now, state.balance = 105, 206
    tapper.poll(None)
    assert len(state.taps) == dispatched
    assert tapper.balance_before is None and not tapper.sent_tap
    state.now, state.balance = tapper.next_at, 202
    tapper.poll(202)
    assert not any(kind == 'gem_orbit_claim' and data['confirmed'] for kind, data in state.events)


def test_measured_arc_outside_battlefield_cannot_dispatch(cycle):
    state, tapper = cycle
    tapper.center, tapper.angle = (220, 1492), 270
    tapper.poll(200)
    assert state.taps == []
    assert state.events[-1] == ('gem_orbit_skip', dict(reason='invalid_orbit_arc'))


@pytest.mark.parametrize('stage', ['prior', 'fresh', 'between_taps'])
def test_unreadable_observation_retains_dispatched_attempt_for_later_stable_confirmation(cycle, monkeypatch, stage):
    state, tapper = cycle
    tapper.poll(200)
    dispatched = len(state.taps)
    state.now = 105
    if stage == 'prior':
        tapper.poll(None)
    elif stage == 'fresh':
        state.balance = None
        tapper.poll(200)
    else:
        frames = iter([200, None])
        monkeypatch.setattr(shard.capture, 'grab', lambda: next(frames))
        tapper.poll(200)
        assert len(state.taps) == dispatched + 1
        dispatched = len(state.taps)
        monkeypatch.setattr(shard.capture, 'grab', lambda: state.balance)
    assert len(state.taps) == dispatched
    assert tapper.balance_before == 200 and tapper.sent_tap
    assert tapper.window_ends_at == 160
    assert not any(k == 'gem_orbit_claim' for k, _ in state.events)
    state.balance = 202
    state.now = tapper.next_at
    tapper.poll(202)
    assert len(state.taps) == dispatched
    assert next(data for kind, data in state.events if kind == 'gem_orbit_claim')['confirmed'] is True


@pytest.mark.parametrize('final_balance', [None, 206, 199])
def test_paused_attempt_never_guesses_or_confirms_other_rewards(cycle, final_balance):
    state, tapper = cycle
    tapper.poll(200)
    state.balance = None
    state.now = 105
    tapper.poll(None)
    state.balance = final_balance
    state.now = 120
    tapper.poll(final_balance)
    assert len(state.taps) == 3
    assert not any(k == 'gem_orbit_claim' and data['confirmed'] for k, data in state.events)


@pytest.mark.parametrize('unknown', ['balance', 'gem'])
def test_pending_reward_is_confirmed_during_input_backoff(cycle, monkeypatch, unknown):
    state, tapper = cycle
    tapper.poll(200)
    dispatched = len(state.taps)
    state.now = 105
    if unknown == 'balance':
        state.balance = None
        tapper.poll(None)
    else:
        monkeypatch.setattr(shard, 'find_orbit_gem', lambda *a, **kw: None)
        tapper.poll(200)
    assert tapper.next_at == 105
    state.now, state.balance = 106, 202
    tapper.poll(202)
    assert len(state.taps) == dispatched
    assert next(data for kind, data in state.events if kind == 'gem_orbit_claim')['confirmed']
    assert tapper.next_at == 106


@pytest.mark.parametrize('balance', [None, 200])
def test_input_pacing_without_a_changed_balance_never_captures_or_taps(cycle, monkeypatch, balance):
    state, tapper = cycle
    tapper.poll(200)
    state.now, state.balance = 101, None
    tapper.poll(None)
    dispatched = len(state.taps)
    monkeypatch.setattr(shard.capture, 'grab', lambda: pytest.fail('unchanged backoff must not capture'))
    state.now, state.balance = 102, balance
    tapper.poll(balance)
    assert len(state.taps) == dispatched
    assert tapper.balance_before == 200 and tapper.sent_tap
    assert tapper.next_at == 105
    assert not any(kind == 'gem_orbit_claim' for kind, _ in state.events)


@pytest.mark.parametrize('prior,fresh', [(206, None), (199, None), (202, 206), (202, 199)])
def test_confirmation_only_backoff_cannot_hide_conflicting_readings(cycle, prior, fresh):
    state, tapper = cycle
    tapper.poll(200)
    state.now, state.balance = 105, None
    tapper.poll(None)
    dispatched = len(state.taps)
    state.now, state.balance = 106, fresh
    tapper.poll(prior)
    assert len(state.taps) == dispatched
    assert tapper.balance_before is None and not tapper.sent_tap
    assert not any(kind == 'gem_orbit_claim' and data['confirmed'] for kind, data in state.events)


@pytest.mark.parametrize('fresh', [None, 200])
def test_confirmation_only_backoff_needs_a_matching_fresh_reward(cycle, fresh):
    state, tapper = cycle
    tapper.poll(200)
    state.now, state.balance = 105, None
    tapper.poll(None)
    state.now, state.balance = 106, fresh
    tapper.poll(202)
    assert len(state.taps) == 3
    assert not any(kind == 'gem_orbit_claim' and data['confirmed'] for kind, data in state.events)


def test_backoff_without_dispatched_input_cannot_confirm_or_capture(cycle, monkeypatch):
    state, tapper = cycle
    state.dry = True
    tapper.poll(200)
    state.now, state.balance = 101, None
    tapper.poll(None)
    assert not tapper.sent_tap
    monkeypatch.setattr(shard.capture, 'grab', lambda: pytest.fail('no dispatched attempt'))
    state.now, state.balance = 102, 202
    tapper.poll(202)
    assert not any(kind == 'gem_orbit_claim' for kind, _ in state.events)


def test_local_backoff_frame_replay(cycle, monkeypatch):
    import os
    from pathlib import Path

    import cv2

    from vision import ocr

    configured = os.environ.get('TOWERPILOT_R14C_FIXTURES')
    if not configured:
        pytest.skip('Captured game frames stay local; set TOWERPILOT_R14C_FIXTURES for incident replay')
    root = Path(configured)
    font = test_paths.FIXTURES / 'r14-gem-balance/templates'
    monkeypatch.setattr(ocr.settings, 'template_files', lambda pattern: sorted(font.glob(pattern)))
    monkeypatch.setattr(ocr, '_glyph_cache', {})
    monkeypatch.setattr(shard, 'read_gem_balance', ocr.read_gem_balance)
    frames = {}
    for name, expected in [('001581.png', 1989), ('001584.png', None),
                           ('001586.png', 1991), ('001587.png', 1991)]:
        frame = cv2.imread(str(root / 'frames' / name))
        assert frame is not None, name
        assert ocr.read_gem_balance(frame) == expected, name
        frames[name] = frame
    state, tapper = cycle
    state.balance = frames['001581.png']
    tapper.poll(state.balance)
    dispatched = len(state.taps)
    assert tapper.balance_before == 1989 and tapper.sent_tap
    state.now, state.balance = 105, frames['001584.png']
    tapper.poll(state.balance)
    assert tapper.next_at == 105
    state.now, state.balance = 106, frames['001587.png']
    tapper.poll(frames['001586.png'])
    claim = next(data for kind, data in state.events if kind == 'gem_orbit_claim')
    assert claim['confirmed'] and claim['before'] == 1989 and claim['after'] == 1991
    assert len(state.taps) == dispatched


@pytest.mark.parametrize('prior,fresh', [(None, 206), (206, None), (None, 199),
                                       (199, None), (206, 202), (199, 202)])
def test_partial_or_unstable_pair_cannot_hide_a_conflicting_read(cycle, prior, fresh):
    state, tapper = cycle
    tapper.poll(200)
    state.now, state.balance = 105, fresh
    tapper.poll(prior)
    assert tapper.balance_before is None and not tapper.sent_tap
    state.now, state.balance = tapper.next_at, 202
    tapper.poll(202)
    assert not any(kind == 'gem_orbit_claim' and data['confirmed'] for kind, data in state.events)


@pytest.mark.parametrize('prior,fresh', [(None, 202), (202, None)])
def test_partial_expected_reward_still_needs_later_stable_pair(cycle, prior, fresh):
    state, tapper = cycle
    tapper.poll(200)
    state.now, state.balance = 105, fresh
    tapper.poll(prior)
    assert tapper.balance_before == 200 and tapper.sent_tap
    assert not any(kind == 'gem_orbit_claim' for kind, _ in state.events)
    state.now, state.balance = tapper.next_at, 202
    tapper.poll(202)
    assert next(data for kind, data in state.events if kind == 'gem_orbit_claim')['confirmed'] is True
    assert len(state.taps) == 3


def test_paused_attempt_expires_without_reusing_its_baseline_next_window(cycle):
    state, tapper = cycle
    tapper.poll(200)
    state.balance = None
    state.now = 105
    tapper.poll(None)
    state.now = 160
    tapper.poll(None)
    assert ('gem_orbit_claim', dict(confirmed=False, reason='attempt_timeout', before=200, after=None)) in state.events
    assert tapper.balance_before is None and not tapper.sent_tap
    state.balance = 202
    state.now = 1060
    tapper.poll(202)
    assert len(state.taps) == 6
    assert not any(k == 'gem_orbit_claim' and data['confirmed'] for k, data in state.events)


def test_unknown_counter_diagnostic_saves_only_supplied_crop_once_per_window(cycle, monkeypatch):
    import cv2
    import numpy as np

    from vision.ocr import GEM_BALANCE_REGION

    state, tapper = cycle
    frame = np.zeros((2560, 1080, 3), np.uint8)
    x, y, w, h = GEM_BALANCE_REGION
    frame[y:y+h, x:x+w] = (30, 40, 50)
    monkeypatch.setattr(shard, 'read_gem_balance', lambda _: None)
    monkeypatch.setitem(shard.CONFIG['logging'], 'screenshot_on_trigger', True)
    tapper.poll(frame)
    [diagnostic] = [data for kind, data in state.events if kind == 'gem_orbit_balance_unreadable']
    assert diagnostic['stage'] == 'prior'
    assert diagnostic['before'] is None and diagnostic['tap_sent'] is False
    path = shard.logger.run_file().parent / diagnostic['counter_frame']
    saved = cv2.imread(str(path))
    assert saved.shape == (h, w, 3)
    assert np.array_equal(saved, frame[y:y+h, x:x+w])
    state.now = 115
    tapper.poll(frame)
    assert len(list(path.parent.glob('*gem_balance_unreadable*.png'))) == 1
    state.now = 160
    tapper.poll(frame)
    state.now = 1060
    tapper.poll(frame)
    assert len(list(path.parent.glob('*gem_balance_unreadable*.png'))) == 2
    assert state.taps == []


def test_dry_run_cannot_confirm_a_collection(cycle):
    state, tapper = cycle
    state.dry = True
    tapper.poll(200)
    state.balance = 202
    state.now = 105
    tapper.poll(202)
    assert not any(k == 'gem_orbit_claim' for k, _ in state.events)


def test_unknown_gem_diagnostic_keeps_supplied_field_once_per_stage(cycle, monkeypatch):
    import cv2
    import numpy as np

    state, tapper = cycle
    tapper.request_now()  # Idle UNKNOWN deliberately opens no automatic attempt.
    frame = np.full((2560, 1080, 3), (20, 30, 40), np.uint8)
    state.balance = frame
    monkeypatch.setattr(shard, 'read_gem_balance', lambda _: 200)
    monkeypatch.setattr(shard, 'find_orbit_gem', lambda *a, **kw: None)
    monkeypatch.setitem(shard.CONFIG['logging'], 'screenshot_on_trigger', True)
    tapper.poll(frame)
    tapper._gem_geometry(frame, 'fresh')
    tapper._gem_geometry(frame, 'pre_tap')
    diagnostics = [data for kind, data in state.events if kind == 'gem_orbit_recognition_unknown']
    assert [data['stage'] for data in diagnostics] == ['fresh', 'pre_tap']
    fx, fy, fw, fh = shard.field_region(0)
    for data in diagnostics:
        path = shard.logger.run_file().parent / data['field_frame']
        saved = cv2.imread(str(path))
        assert np.array_equal(saved, frame[fy:fy+fh, fx:fx+fw])
    assert state.taps == []


def test_unknown_motion_gem_is_saved_before_readable_fallback_once_per_window(cycle, monkeypatch):
    import cv2
    import numpy as np

    state, tapper = cycle
    fresh = np.full((2560, 1080, 3), (20, 30, 40), np.uint8)
    motion = np.full_like(fresh, (50, 60, 70))
    state.balance = fresh
    tapper.taps = 1
    detected = shard.find_orbit_gem(fresh)
    monkeypatch.setattr(shard, 'read_gem_balance', lambda _: 200)
    monkeypatch.setattr(shard, 'find_orbit_gem', lambda frame, **kw:
                        None if frame is motion else detected)
    monkeypatch.setattr(shard, 'battlefield_observation', lambda *a: (True, 'live', motion))
    monkeypatch.setitem(shard.CONFIG['logging'], 'screenshot_on_trigger', True)

    tapper.poll(fresh)
    state.now = 105
    tapper.poll(fresh)
    diagnostics = [data for kind, data in state.events if kind == 'gem_orbit_recognition_unknown']
    assert [data['stage'] for data in diagnostics] == ['motion']
    path = shard.logger.run_file().parent / diagnostics[0]['field_frame']
    fx, fy, fw, fh = shard.field_region(0)
    assert np.array_equal(cv2.imread(str(path)), motion[fy:fy+fh, fx:fx+fw])
    assert len(state.taps) == 2
    assert not any(kind == 'gem_orbit_claim' for kind, _ in state.events)

    state.now = 160
    tapper.poll(fresh)
    state.now = 1060
    tapper.poll(fresh)
    diagnostics = [data for kind, data in state.events if kind == 'gem_orbit_recognition_unknown']
    assert [data['stage'] for data in diagnostics] == ['motion', 'motion']
    assert len(list(path.parent.glob('*gem_not_detected*.png'))) == 2


def test_returns_post_tap_frame_even_after_last_tap(cycle, monkeypatch):
    state, tapper = cycle
    tapper.taps = 1
    def tap(*a, **kw):
        state.balance = 202
        return {}
    monkeypatch.setattr(shard.act, 'tap', tap)
    assert tapper.poll(200) == 202


@pytest.mark.parametrize('key', ORBIT_TIMING_FIELDS)
@pytest.mark.parametrize('value', [False, float('nan'), float('inf'), -1, '3'])
def test_invalid_timing_is_rejected(key, value):
    assert validate_orbit_timing({key: value})


def test_timing_reaches_runner_and_settings_catalog():
    from control.settings_catalog import catalog
    values = dict(interval_sec=120, retry_interval_sec=3, attempt_timeout_sec=45, retry_backoff_sec=20)
    options = shard.gem_orbit_opts({'gem_orbit': values})
    assert options == dict(enabled=False, interval=120, retry_interval_sec=3, attempt_timeout_sec=45, retry_backoff_sec=20)
    modules = catalog({}, {'policies': {'gather': {'shared': {'gem_orbit': values}}}}, True)
    fields = {f['key']: f for m in modules if m['id'] == 'orbit:shared' for f in m['fields']}
    for key, value in values.items():
        assert fields['gem_orbit.' + key]['value'] == value
