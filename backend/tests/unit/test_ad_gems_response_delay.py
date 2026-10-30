"""Ad Gem response delay across offer movement and near the window deadline (quality council item 2).

Live windows were lost two ways: an `offer_moved` attempt forgot the chosen response due time, so the next
visible read drew a fresh 15-45 s delay that ended after the window (8.7 s and 20.6 s were left); and a
first draw in the last seconds of a window could never be attempted. The due time now survives movement
(no new draw, no new randomness) and every draw, the first included, is clamped to the last moment that
still leaves one poll interval plus one recognition budget before the deadline.

Settings here are explicit synthetic values; nothing reads machine config or any tap cap. The clock, capture,
recognition, windows and input are scripted, so nothing touches a device.
"""
import random
from types import SimpleNamespace

import pytest

from interactions import ad_gems

RECT = (20, 1200, 248, 148)
MOVED = (20, 1600, 248, 148)
WINDOW = 60
POLL = [2, 2]
LATEST_MARGIN = POLL[1] + ad_gems.OBSERVATION_TIMEOUT_SEC      # last due time = deadline - 4


class ScriptedDelays(random.Random):
    """Draw scripted response delays; every other draw takes the lower bound. `draws` counts delay draws."""

    def __init__(self, delays, delay_range):
        super().__init__(15)
        self.delays, self.delay_range, self.draws = list(delays), tuple(delay_range), 0

    def uniform(self, a, b):
        if (a, b) == self.delay_range:
            self.draws += 1
            return self.delays.pop(0)
        return a


@pytest.fixture
def world(monkeypatch):
    w = SimpleNamespace(now=[100.0], events=[], taps=[], grabs=[], fresh=[], offer=lambda t: RECT)
    monkeypatch.setattr(ad_gems.time, 'monotonic', lambda: w.now[0])
    monkeypatch.setattr(ad_gems.time, 'time', lambda: 1000 + w.now[0])
    monkeypatch.setattr(ad_gems.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda frame: 100)
    monkeypatch.setattr(ad_gems.screen, 'identify', lambda frame: SimpleNamespace(name='battle'))
    monkeypatch.setattr(ad_gems.overlays, 'windows', lambda serial, **kw: [ad_gems.overlays.GAME_PKG + '/Activity'])
    monkeypatch.setattr(ad_gems.logger, 'event', lambda kind, **fields: w.events.append((kind, fields)))
    monkeypatch.setattr(ad_gems, 'find_claim_rect',
                        lambda frame, **kw: frame.read if hasattr(frame, 'read') else w.offer(w.now[0]))

    def grab():
        if not w.fresh:
            pytest.fail('capture without an authorized attempt')
        frame = w.fresh.pop(0)
        w.now[0] += getattr(frame, 'cost', 0)
        w.grabs.append(w.now[0])
        return frame
    monkeypatch.setattr(ad_gems.capture, 'grab', grab)
    monkeypatch.setattr(ad_gems.act, 'tap', lambda x, y, **kw: w.taps.append((w.now[0], x, y)) or {})
    w.frame = lambda read, cost=0: SimpleNamespace(read=read, cost=cost)
    return w


def collector(w, delays, delay=(15, 45)):
    c = ad_gems.AdGemCollector(delay=delay, rng=ScriptedDelays(delays, delay), options={'poll_sec': POLL})
    c.next_at = c.next_window_at = w.now[0]
    return c


def drive(w, c, times):
    for t in times:
        w.now[0] = t
        c.poll(object())


def scheduled(w):
    return [f for k, f in w.events if k == 'ad_gems_response_scheduled']


def skips(w):
    return [f['reason'] for k, f in w.events if k == 'ad_gems_skip']


def test_the_served_delay_survives_an_offer_move_at_t40_of_60(world):
    """The pre-fix bug: the move at t=40 forgot the 40 s due time and the 25 s redraw ended at t=65 > 60."""
    c = collector(world, [40, 25])
    world.offer = lambda t: MOVED if t > 140 else RECT
    world.fresh = [world.frame(MOVED), world.frame(MOVED), world.frame(None)]
    drive(world, c, range(100, 160))
    assert c.window_deadline is None and skips(world) == ['offer_moved']
    assert c.rng.draws == 1 and len(scheduled(world)) == 1                  # the 25 s redraw never happens
    [(when, x, y)] = world.taps
    assert 140 < when < 160                                                    # one attempt, before the deadline
    assert (MOVED[0] < x < MOVED[0] + MOVED[2]) and (MOVED[1] < y < MOVED[1] + MOVED[3])   # the fresh position, not the stale one
    assert c.last_outcome['reason'] == 'offer_disappeared'


def test_the_kept_due_time_is_the_original_and_applies_at_the_next_visible_read(world):
    c = collector(world, [40, 25])
    world.offer = lambda t: MOVED if t > 140 else RECT
    world.fresh = [world.frame(MOVED), world.frame(MOVED), world.frame(None)]
    drive(world, c, range(100, 150))
    [first] = scheduled(world)
    kept = [f for k, f in world.events if k == 'ad_gems_response_kept']
    assert [f['response_due_at'] for f in kept] == [first['response_due_at']] == [1140]
    assert world.taps and world.taps[0][0] < 150


def test_every_move_keeps_the_same_due_time_and_never_taps_a_stale_position(world):
    c = collector(world, [40, 25, 25])
    world.offer = lambda t: RECT if t <= 140 else MOVED
    world.fresh = [world.frame(MOVED)] + [world.frame(RECT) for _ in range(40)]      # the fresh frame always disagrees with the read
    drive(world, c, range(100, 161))
    assert world.taps == [] and c.rng.draws == 1
    assert len(skips(world)) >= 3 and set(skips(world)) == {'offer_moved'}
    assert c.window_deadline is None                                            # the window ended; nothing was resent


def test_a_move_then_the_window_ending_taps_nothing(world):
    c = collector(world, [40, 25])
    world.offer = lambda t: MOVED if t > 140 else RECT
    world.fresh = [world.frame(MOVED)]                                        # no second chance is captured after the deadline
    drive(world, c, [100, 102, 140, 141])
    world.now[0] = 161
    c.poll(object())
    assert world.taps == [] and c.window_deadline is None and c.rng.draws == 1


def test_recognition_time_spent_on_the_fresh_frame_can_still_expire_the_window(world):
    """The fresh capture itself consumes the remaining window: no tap, no resend."""
    c = collector(world, [40])
    world.fresh = [world.frame(RECT, cost=30)]
    drive(world, c, range(100, 142, 2))
    assert world.taps == [] and c.window_deadline is None
    assert skips(world) == ['fresh_recognition_incomplete']


def test_a_move_then_an_absent_offer_ends_the_kept_schedule(world):
    c = collector(world, [40, 7])
    seen = {'gone': False}
    world.offer = lambda t: None if seen['gone'] else (MOVED if t > 140 else RECT)
    world.fresh = [world.frame(MOVED)]
    drive(world, c, [100, 102, 140])
    seen['gone'] = True
    drive(world, c, [142, 144])
    seen['gone'] = False
    drive(world, c, [146])
    assert [round(f['response_delay_sec']) for f in scheduled(world)] == [40, 7]


def test_the_first_draw_is_clamped_to_leave_an_attempt_before_the_deadline(world):
    c = collector(world, [30])
    world.offer = lambda t: RECT if t >= 150 else None                         # first seen 10 s before the deadline
    world.fresh = [world.frame(RECT), world.frame(None)]
    drive(world, c, range(100, 160))
    [event] = scheduled(world)
    assert event['response_delay_drawn_sec'] == 30 and event['response_clamped'] is True
    assert event['response_delay_sec'] == pytest.approx(160 - LATEST_MARGIN - 150)
    [(when, _, _)] = world.taps
    assert when < 160 and c.last_outcome['reason'] == 'offer_disappeared'


def test_an_offer_first_seen_after_the_last_useful_moment_is_attempted_at_once(world):
    c = collector(world, [30])
    world.offer = lambda t: RECT if t >= 158 else None
    world.fresh = [world.frame(RECT), world.frame(None)]
    drive(world, c, range(100, 160))
    [event] = scheduled(world)
    assert event['response_delay_sec'] == 0
    [(when, _, _)] = world.taps
    assert when == 158


def test_an_unclamped_draw_inside_the_window_is_unchanged(world):
    c = collector(world, [15])
    drive(world, c, [100])
    [event] = scheduled(world)
    assert event['response_delay_sec'] == 15 and 'response_clamped' not in event


def test_shortening_the_window_pulls_a_waiting_due_time_inside_it(world):
    c = collector(world, [40])
    drive(world, c, [100, 102])
    world.now[0] = 103
    c.configure({'window_sec': 20})
    assert c.window_deadline == 120
    assert c.claim_at == 120 - LATEST_MARGIN


def test_a_redraw_after_an_unconfirmed_tap_is_clamped_and_every_tap_has_its_own_fresh_verification(world):
    """r17b pages case (attempts 2 -> 3): an unconfirmed tap already allowed more taps in one window (existing retry rule); the
    redraw that follows is now clamped too, so the last retry can still happen before the deadline. Each tap is one attempt with its
    own fresh capture; nothing is sent twice inside an attempt and nothing after the deadline."""
    c = ad_gems.AdGemCollector(delay=(15, 15), rng=ScriptedDelays([15, 15], (15, 15)),
                               options={'poll_sec': POLL, 'attempt_timeout_sec': 30, 'retry_cooldown_sec': [4, 4]})
    c.next_at = c.next_window_at = world.now[0]
    world.fresh = [world.frame(RECT) for _ in range(8)]                       # the offer stays visible after every tap
    drive(world, c, range(100, 135))
    assert c.window_deadline is None
    assert len(world.taps) == c.counters['attempts'] == 2                     # one tap per attempt, two attempts
    assert len(world.grabs) == 2 * c.counters['attempts']                     # a fresh capture and a confirmation capture each
    first, second = scheduled(world)
    assert 'response_clamped' not in first and first['response_delay_sec'] == 15
    assert second['response_clamped'] is True and second['response_delay_drawn_sec'] == 15
    assert second['response_delay_sec'] < 15
    assert all(when < 130 for when, _, _ in world.taps)                       # every tap before the 30 s deadline