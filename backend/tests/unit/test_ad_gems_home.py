"""Ad Gem claims on the Home screen (r15c).

The harvesting collector claims the offer at the top of the Home screen's left
column with one recognized CLAIM tap, only while the screen is exactly Home, and
counts a claim only on a stable, exact +6 on the Home gem counter. Frames here
are tokens that name what each real reader would report; the captured-frame
replay is test_ad_gems_home_replay.py.
"""
import random
from types import SimpleNamespace

import pytest

from device import overlays
from interactions import ad_gems
from runtime import logger

RECT = (5, 200, 234, 120)          # the offer find_claim_rect returns on captured Home frames
GAME = overlays.GAME_PKG + '/com.unity3d.player.UnityPlayerActivity'
AD = 'com.mumu.store/com.mumu.store.MainActivity'


def home(balance=966, offer=True, screen='home', wave=None):
    return SimpleNamespace(screen=screen, wave=wave, offer=offer, balance=balance)


class Recorder:
    def __init__(self):
        self.calls = []

    def opened(self, **fields):
        self.calls.append(('opened', fields))

    def closed(self, **fields):
        self.calls.append(('closed', fields))


@pytest.fixture
def world(monkeypatch):
    w = SimpleNamespace(taps=[], events=[], grabs=[], shots=[], queue=[], repeat=None, now=[100.0], windows=[GAME],
                        refuse=None, counter_regions=[])
    monkeypatch.setattr(ad_gems.time, 'monotonic', lambda: w.now[0])
    monkeypatch.setattr(ad_gems.time, 'sleep', lambda seconds: w.now.__setitem__(0, w.now[0] + seconds))
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda frame: frame.wave)
    monkeypatch.setattr(ad_gems.screen, 'identify', lambda frame: SimpleNamespace(name=frame.screen))
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda frame, **kwargs: RECT if frame.offer else None)

    def counter(frame, region=None):
        w.counter_regions.append(region)
        return frame.balance
    monkeypatch.setattr(ad_gems.ocr, 'read_gem_balance', counter)

    def grab():
        if w.queue:
            frame = w.queue.pop(0)
        elif w.repeat is not None:
            frame = w.repeat
        else:
            pytest.fail('unexpected capture')
        w.grabs.append(frame)
        return frame
    monkeypatch.setattr(ad_gems.capture, 'grab', grab)

    def tap(x, y, **kwargs):
        if w.refuse is not None:
            raise ad_gems.act.TapRefused(w.refuse)
        w.taps.append((x, y, kwargs))
        return {}
    monkeypatch.setattr(ad_gems.act, 'tap', tap)
    monkeypatch.setattr(ad_gems.overlays, 'windows', lambda serial, **kwargs: list(w.windows))
    monkeypatch.setattr(ad_gems.logger, 'event', lambda kind, **fields: w.events.append((kind, {**logger.current_context(), **fields})))
    monkeypatch.setattr(ad_gems.logger, 'shot', lambda image, name: w.shots.append(name) or f'{name}.png')
    monkeypatch.setattr(ad_gems.ocr, 'gem_balance_crop', lambda frame, region=None: ('counter', region))
    return w


def collector(**kwargs):
    kwargs.setdefault('delay', (0, 0))
    kwargs.setdefault('rng', random.Random(4))
    return ad_gems.AdGemCollector(**kwargs)


def named(w, kind):
    return [fields for name, fields in w.events if name == kind]


def test_a_verified_home_offer_gets_one_tap_inside_it_and_a_stable_plus_six_confirms(world):
    recorder = Recorder()
    c = collector(recorder=recorder)
    world.queue = [home(), home(972, offer=False), home(972, offer=False)]
    c.poll_home(home())
    assert len(world.taps) == 1
    x, y, kwargs = world.taps[0]
    assert RECT[0] < x < RECT[0] + RECT[2] and RECT[1] < y < RECT[1] + RECT[3]
    assert kwargs['deadline'] == pytest.approx(100 + 60)
    assert c.last_outcome['confirmed'] is True and c.last_outcome['reason'] == 'balance_increased'
    assert (c.last_outcome['before'], c.last_outcome['after']) == (966, 972)
    assert c.window_deadline is None and c.counters['confirmed'] == 1
    assert set(world.counter_regions) == {ad_gems.ocr.HOME_GEM_BALANCE_REGION}
    claim = named(world, 'ad_gems_claim')[0]
    assert claim['confirmed'] is True and claim['reads'] == [972, 972] and claim['surface'] == 'home'
    assert named(world, 'ad_gems_tap')[0]['surface'] == 'home'
    assert named(world, 'ad_gems_window_ended')[-1]['reason'] == 'confirmed'
    closed = [fields for kind, fields in recorder.calls if kind == 'closed']
    assert closed == [dict(result='succeeded', reason='balance_increased', effect='confirmed', attempts=1, confirmed=1,
                           evidence=['balance_increased'],
                           # the offer was read twice, both times visible: the decision frame, then the fresh one before the tap
                           refs=dict(window_id=c.window_id, surface='home', observation=dict(reads=2, incomplete=0, absent=0)))]


def test_after_a_confirmed_home_claim_the_next_offer_waits_for_the_next_window(world):
    c = collector()
    world.queue = [home(), home(972, offer=False), home(972, offer=False)]
    c.poll_home(home())
    world.now[0] += 30
    c.poll_home(home(972))                   # a new offer inside the same 720 s: observed, never claimed
    assert len(world.taps) == 1 and len(world.grabs) == 3
    world.now[0] = 100 + 720
    world.queue = [home(972), home(978, offer=False), home(978, offer=False)]
    c.poll_home(home(972))
    assert len(world.taps) == 2 and c.last_outcome['after'] == 978


@pytest.mark.parametrize('frame', [home(screen='unknown'), home(screen='modules'), home(screen='guild'),
                                   home(screen='game_stats'), home(screen='battle', wave=4100), home(wave=12)],
                         ids=['menu_or_store', 'modules', 'guild', 'game_stats', 'battle', 'home_with_a_wave'])
def test_anything_but_the_exact_home_screen_gets_no_capture_and_no_input(world, frame):
    c = collector()
    c.poll_home(frame)
    assert world.taps == [] and world.grabs == []
    assert c.visibility == 'unknown' and c.visibility_reason == 'not_home'


def test_a_disabled_routine_never_acts_on_home_even_with_a_run_now_request(world):
    c = collector(enabled=False)
    assert c.request_now()
    c.poll_home(home())
    assert world.taps == [] and world.grabs == [] and c.window_deadline is None


def test_a_person_navigating_away_before_the_tap_ends_the_window_with_no_input(world):
    c = collector()
    world.queue = [home(screen='unknown')]
    c.poll_home(home())
    assert world.taps == []
    assert named(world, 'ad_gems_skip')[0]['reason'] == 'fresh_frame_not_home'
    assert named(world, 'ad_gems_window_ended')[-1]['reason'] == 'left_home'
    assert c.window_deadline is None and c.surface == 'battle'
    world.now[0] += 10                       # back on Home inside the old window: nothing more until the next one
    c.poll_home(home())
    assert world.taps == [] and len(world.grabs) == 1


def test_the_response_delay_holds_on_home_and_the_offer_is_verified_again(world):
    c = collector(delay=(5, 5))
    c.poll_home(home())
    assert c.claim_at == 105 and world.grabs == [] and world.taps == []
    world.now[0] = 103
    c.poll_home(home())
    assert world.taps == []
    world.now[0] = 106
    world.queue = [home(), home(972, offer=False), home(972, offer=False)]
    c.poll_home(home())
    assert len(world.taps) == 1 and c.last_outcome['confirmed'] is True


@pytest.mark.parametrize('decision,fresh,reason', [(None, 966, 'balance_unreadable'), (966, None, 'balance_unreadable'),
                                                   (966, 972, 'balance_unstable')])
def test_no_tap_without_one_readable_agreed_baseline(world, decision, fresh, reason):
    c = collector()
    world.queue = [home(fresh)]
    c.poll_home(home(decision))
    assert world.taps == []
    assert named(world, 'ad_gems_skip')[0]['reason'] == reason
    assert world.shots == (['home_gem_balance_unreadable'] if reason == 'balance_unreadable' else [])


def test_an_unreadable_counter_is_kept_once_per_window(world):
    c = collector()
    for _ in range(3):
        world.queue = [home(None)]
        c.poll_home(home(None))
        world.now[0] += 5
    assert world.taps == [] and world.shots == ['home_gem_balance_unreadable']
    assert len(named(world, 'ad_gems_home_balance_unreadable')) == 1


def test_an_ad_drawn_over_the_game_blocks_the_home_tap(world):
    c = collector()
    world.windows = [AD, GAME]
    world.queue = [home()]
    c.poll_home(home())
    assert world.taps == []
    assert named(world, 'ad_gems_skip')[0]['reason'] == 'in_front:com.mumu.store'


def test_a_refused_tap_reads_nothing_more(world):
    c = collector()
    world.refuse = 'Automation input was revoked for this emulator'
    world.queue = [home()]
    c.poll_home(home())
    assert world.taps == [] and len(world.grabs) == 1
    assert c.last_outcome['reason'] == 'tap_refused'


def test_leaving_home_after_the_tap_stops_reading_and_sends_nothing_more(world):
    recorder = Recorder()
    c = collector(recorder=recorder)
    world.queue = [home(), home(screen='unknown')]
    c.poll_home(home())
    assert len(world.taps) == 1 and len(world.grabs) == 2
    assert named(world, 'ad_gems_claim')[0]['reason'] == 'post_claim_not_home'
    assert c.window_deadline is None and c.surface == 'battle'
    closed = [fields for kind, fields in recorder.calls if kind == 'closed'][0]
    assert (closed['result'], closed['reason'], closed['effect']) == ('failed', 'post_claim_not_home', 'unconfirmed')
    assert closed['refs']['surface'] == 'home'
    world.now[0] += 10
    c.poll_home(home())
    assert len(world.taps) == 1


@pytest.mark.parametrize('reads,reason,window_open', [
    ([978], 'balance_changed_without_confirmation', False),
    ([972, 960], 'balance_changed_without_confirmation', False),
    ([966] * 7, 'balance_unchanged', True),
    ([972] + [None] * 6, 'post_claim_balance_unreadable', True),
    ([None] * 6 + [972], 'balance_unstable', True),
    ([972, None, 972, None, 972, None, 972], 'balance_unstable', True),
], ids=['conflict', 'conflict_after_reward', 'unchanged', 'unreadable', 'single_read', 'never_two_in_a_row'])
def test_only_two_consecutive_exact_plus_six_reads_confirm(world, reads, reason, window_open):
    c = collector()
    world.queue = [home()] + [home(value, offer=False) for value in reads]
    c.poll_home(home())
    claim = named(world, 'ad_gems_claim')[0]
    assert (claim['confirmed'], claim['reason']) == (False, reason)
    assert c.counters['confirmed'] == 0 and len(world.taps) == 1
    assert world.queue == []                 # every scripted frame was read, and no more
    assert (c.window_deadline is not None) is window_open
    if window_open:
        assert world.now[0] < c.next_at <= c.window_deadline


def test_a_late_stable_pair_inside_the_reading_period_confirms(world):
    c = collector()
    world.queue = [home(), home(None, offer=False), home(966, offer=False), home(972, offer=False), home(972, offer=False)]
    c.poll_home(home())
    assert c.last_outcome['reason'] == 'balance_increased' and c.last_outcome['after'] == 972


def test_a_battle_poll_closes_a_window_home_was_using(world):
    c = collector()
    c.poll_home(home(offer=False))                # opens the window; nothing to claim yet
    assert c.window_deadline is not None
    c.poll(home(screen='battle', wave=10, offer=True))
    assert named(world, 'ad_gems_window_ended')[0]['reason'] == 'left_home'
    assert world.taps == [] and c.surface == 'battle'


def test_leave_home_is_a_no_op_for_a_battle_collector(world):
    c = collector()
    c.leave_home()
    assert world.events == [] and c.surface == 'battle'


def test_a_battle_offer_never_carries_over_to_home(world):
    c = collector(delay=(5, 5))
    c.poll(home(screen='battle', wave=10))
    assert c.claim_at == 105
    c.poll_home(home())
    assert c.claim_at is None                # the battle observation is forgotten on Home
    assert any(f['reason'] == 'surface_changed' for f in named(world, 'ad_gems_offer_unknown'))
    world.now[0] = 104
    c.poll_home(home())
    assert c.claim_at == 109 and world.taps == []     # observed again on Home, with its own response delay

# ------------------------------------------------------------ confirmation under load (r15d)
def slow_reads(world, seconds):
    """Each confirmation capture takes `seconds`, as one read did under load (2026-10-23 13:43: about 2 s)."""
    grab = ad_gems.capture.grab
    def slow():
        world.now[0] += seconds
        return grab()
    return slow


def test_slow_reads_still_get_three_reads_so_a_real_plus_six_confirms(world, monkeypatch):
    """13:43 live: reads [2566, 2572] ended at 3 s as balance_unstable; the next window's baseline was 2572."""
    c = collector()
    world.queue = [home(2566), home(2566, offer=False), home(2572, offer=False), home(2572, offer=False)]
    monkeypatch.setattr(ad_gems.capture, 'grab', slow_reads(world, 2.0))
    c.poll_home(home(2566))
    claim = named(world, 'ad_gems_claim')[0]
    assert (claim['reason'], claim['reads']) == ('balance_increased', [2566, 2572, 2572])
    assert len(world.taps) == 1 and world.queue == []


def test_slow_reads_stop_at_the_ceiling_and_prove_nothing(world, monkeypatch):
    c = collector()
    world.queue = [home(966)]
    world.repeat = home(None, offer=False)
    monkeypatch.setattr(ad_gems.capture, 'grab', slow_reads(world, 6.0))
    c.poll_home(home(966))
    claim = named(world, 'ad_gems_claim')[0]
    assert claim['reason'] == 'post_claim_balance_unreadable' and claim['confirmed'] is False
    assert claim['reads'] == [None, None]               # reads at +6 s and +12 s: the 10 s ceiling ends it after the second
    assert len(world.taps) == 1


def test_fast_reads_keep_the_three_second_period(world):
    c = collector()
    world.queue = [home(966)]
    world.repeat = home(966, offer=False)
    c.poll_home(home(966))
    claim = named(world, 'ad_gems_claim')[0]
    assert claim['reason'] == 'balance_unchanged' and len(claim['reads']) == 7
