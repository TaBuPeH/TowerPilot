"""Council #9(b) (quality-council-20261024, BC-03): what the rescue and the wall watch do when the wave counter cannot be read.

`_fast_wall_watch` and `watch_frame` are the code item 7 moves (C-3, C-4), and the tower-on-screen proof they share is a readable wave: a dark wall bar is also what the death dialog
and every menu look like from the wall's ROI. Until now no test reached the three `off`/`break` guards (the 8th-sample escape, the check before the burst, the check inside the retap
loop), the stand-down of an unreadable first frame, or the "a proc needs a battle" and "rescue needs a wave" gates, and the latency gate's worlds always read wave 1498. These are
BEHAVIOURAL tests against `_fast_wall_watch(rs, ab)` and `watch_frame(rs, frame, observation)`: a scripted world answers the captures, the wave reader, the wall detector and the
buttons, and the assertion is what was TAPPED, what was returned and what the run state says afterwards. Every unreadable-wave test has a readable positive control that does tap, so
a test that passes only because the world never reaches the rescue cannot pass.

What this file is not: it moves no baseline (`baseline_structural_v1.json` is untouched; unreadable-wave gate worlds are a later additive extension of the gate), it measures no rescue
latency (the world's clock is the real monotonic clock and every sleep is a no-op), and it provokes no rescue: no device, no capture, no socket, no real tap.
A scripted world that is never exhausted cannot hang a mutant: the capture raises `WorldExhausted` after `MAX_GRABS` frames.
"""
from types import SimpleNamespace

import pytest

import orchestrator

DM_AT = (242, 1493)
MAX_GRABS = 200
ABILITIES = {'rescue_bar': 'wall', 'dm_below': 0.2, 'hold_until_second_wind': True}
FALLING = [(0.6, 'normal'), (0.15, 'normal'), (0.10, 'normal')]            # two falling samples under the threshold: the third sample is the danger


class WorldExhausted(AssertionError):
    pass


class Frame:
    def __init__(self, number):
        self.number = number


class World:
    """One scripted battle. `wave(number)` and `wall(number)` answer for the frame the loop captured as that (1-based) number; `border` answers the Demon Mode button's border value."""

    def __init__(self, monkeypatch, wave, wall=lambda number: (0.9, 'normal'), border=lambda number: 200):
        self.taps, self.events, self.grabs, self.wave_reads = [], [], 0, []
        self.wave, self.wall, self.border = wave, wall, border
        self.fired = []
        for target, name, value in (
                (orchestrator.capture, 'grab', self._grab), (orchestrator.wave_reader, 'read_wave', self._read_wave), (orchestrator.detect, 'wall_overheal', lambda f: self.wall(f.number)),
                (orchestrator.detect, 'find_intro_sprint', lambda f: None), (orchestrator.detect, 'button_border_val', lambda f, name: self.border(f.number)),
                (orchestrator.detect, 'button_state', lambda f, name: SimpleNamespace(present=True, ready=True, score=0.99, center=DM_AT)),
                (orchestrator.screen, 'identify', lambda f: SimpleNamespace(name='battle')), (orchestrator.act, 'tap', self._tap), (orchestrator.time, 'sleep', lambda *a: None), (orchestrator.logger, 'event', lambda name, **k: self.events.append(name)),
                (orchestrator.logger, 'shot', lambda *a, **k: ''), (orchestrator, '_battle_session', None), (orchestrator, 'fire_button', self._fire),
                (orchestrator, 'marks', lambda: [1495]), (orchestrator, 'demon_mode_always', lambda *a: False)):
            monkeypatch.setattr(target, name, value)
        monkeypatch.setitem(orchestrator.CONFIG['rois'], 'wall_bar', None)

    def _grab(self):
        self.grabs += 1
        if self.grabs > MAX_GRABS:
            raise WorldExhausted(f'the watch asked for frame {self.grabs}: it never ended')
        return Frame(self.grabs)

    def _read_wave(self, frame):
        self.wave_reads.append(frame.number)
        return self.wave(frame.number)

    def _tap(self, x, y, reason='', instant=False):
        self.taps.append((x, y, reason))

    def _fire(self, frame, name, reason, **kwargs):
        self.fired.append((name, reason))
        return True


@pytest.fixture
def rs(monkeypatch):
    monkeypatch.setattr(orchestrator, '_battle_session', None)
    monkeypatch.setattr(orchestrator.logger, 'event', lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.logger, 'shot', lambda *a, **k: '')
    monkeypatch.setitem(orchestrator.CONFIG, 'fleet', {'sighting': {'enabled': False}})      # the plain schedule: a due mark fires, no pentagon image is needed
    state = orchestrator.RunState()
    state.post_sw_until = float('inf')
    state.last_fire['demon_mode'] = -1e9
    return state


def dm_taps(world):
    return [tap for tap in world.taps if tap[2].startswith('rescue_dm')]


# ------------------------------------------------------------------ _fast_wall_watch: the off-battle escape (every 8th sample)
def test_two_unreadable_checks_in_a_row_end_the_watch_with_no_tap_and_no_rescue(monkeypatch, rs):
    world = World(monkeypatch, wave=lambda number: None)
    assert orchestrator._fast_wall_watch(rs, dict(ABILITIES)) == 'off'
    assert world.grabs == 16 and world.wave_reads == [8, 16]                    # the wave is read on every 8th sample only, and the second unreadable check ends the watch
    assert world.taps == [] and world.fired == [] and 'rescue' not in world.events
    assert rs.last_fire['demon_mode'] == -1e9 and rs.dm_fired is False


def test_one_readable_check_between_two_unreadable_ones_keeps_the_watch_going(monkeypatch, rs):
    script = {8: None, 16: 1498, 24: None, 32: None}
    world = World(monkeypatch, wave=lambda number: script.get(number))
    assert orchestrator._fast_wall_watch(rs, dict(ABILITIES)) == 'off'
    assert world.grabs == 32 and world.wave_reads == [8, 16, 24, 32]            # the count restarts at the readable check: not off at 24
    assert world.taps == []


def test_a_readable_wave_never_ends_the_watch_by_itself_so_the_escape_is_the_unreadable_wave(monkeypatch, rs):
    world = World(monkeypatch, wave=lambda number: 1498, wall=lambda number: (1.0, 'immune') if number == 30 else (0.9, 'normal'))
    assert orchestrator._fast_wall_watch(rs, dict(ABILITIES)) == 'immune'       # control: with a readable wave the loop runs on past the 16th sample
    assert world.grabs == 30 and world.taps == []


@pytest.mark.parametrize('readable', [False, True], ids=['unreadable', 'readable-control'])
def test_the_fleet_nuke_inside_the_watch_needs_a_wave_it_has_just_read(monkeypatch, rs, readable):
    abilities = dict(ABILITIES, nuke_on_fleet={'after_waves': 1, 'window_waves': 60})
    rs.tracker.last = 1498                                                       # a stale tracker value from earlier in the run: the unreadable check must not act on it
    world = World(monkeypatch, wave=lambda number: 1498 if readable else None, wall=lambda number: (1.0, 'immune') if number == 40 else (0.9, 'normal'))
    outcome = orchestrator._fast_wall_watch(rs, abilities)
    if readable:
        assert outcome == 'immune' and world.fired == [('nuke', 'fleet_1495')] and rs.nuked_marks == {1495}
    else:
        assert outcome == 'off' and world.grabs == 16 and world.fired == [] and rs.nuked_marks == set()


# ------------------------------------------------------------------ _fast_wall_watch: the check before the burst
@pytest.mark.parametrize('readable', [False, True], ids=['unreadable', 'readable-control'])
def test_the_burst_is_tapped_only_over_a_readable_wave(monkeypatch, rs, readable):
    """A wall falling under the threshold is also what the death dialog's dark bar looks like: no wave, no tap."""
    wall = lambda number: FALLING[number - 1] if number <= 3 else (0.1, 'normal')
    world = World(monkeypatch, wave=lambda number: 1498 if readable else None, wall=wall, border=lambda number: 100)
    outcome = orchestrator._fast_wall_watch(rs, dict(ABILITIES))
    if readable:
        assert outcome == 'fired' and [tap[:2] for tap in dm_taps(world)] == [DM_AT] and 'rescue' in world.events
        assert rs.dm_fired is True and rs.last_fire['demon_mode'] > 0
    else:
        assert outcome == 'off' and world.grabs == 3 and world.taps == [] and 'rescue' not in world.events
        assert rs.dm_fired is False and rs.last_fire['demon_mode'] == -1e9       # no refire guard was started by a burst that never happened


# ------------------------------------------------------------------ _fast_wall_watch: the check inside the retap loop and the last look
def burst_world(monkeypatch, *, after_burst_wave, border):
    wall = lambda number: FALLING[number - 1] if number <= 3 else (0.1, 'normal')
    return World(monkeypatch, wave=lambda number: 1498 if number <= 3 else after_burst_wave(number), wall=wall, border=border)


def test_an_unreadable_wave_after_the_burst_stops_the_retaps_and_does_not_claim_the_rescue_fired(monkeypatch, rs):
    world = burst_world(monkeypatch, after_burst_wave=lambda number: None, border=lambda number: 200)         # the button still reads READY, which alone would mean "tap again"
    assert orchestrator._fast_wall_watch(rs, dict(ABILITIES)) == 'fired'
    assert len(dm_taps(world)) == 1                                              # the burst's own tap: the death dialog gets no retap
    assert rs.dm_fired is False and 'rescue_confirm' in world.events


def test_a_readable_wave_after_the_burst_retaps_while_the_button_still_reads_ready(monkeypatch, rs):
    world = burst_world(monkeypatch, after_burst_wave=lambda number: 1498, border=lambda number: 200)
    assert orchestrator._fast_wall_watch(rs, dict(ABILITIES)) == 'fired'
    assert len(dm_taps(world)) == 1 + 3                                          # the burst and its three retaps (control for the test above)
    assert rs.dm_fired is False                                                  # the button never dimmed: not confirmed


@pytest.mark.parametrize('wave_after,expected', [(None, False), (1498, True)], ids=['dialog-hides-the-button', 'in-battle-border-unreadable'])
def test_an_unreadable_border_is_a_fire_only_while_the_tower_is_still_on_screen(monkeypatch, rs, wave_after, expected):
    world = burst_world(monkeypatch, after_burst_wave=lambda number: wave_after, border=lambda number: None if number > 3 else 100)
    assert orchestrator._fast_wall_watch(rs, dict(ABILITIES)) == 'fired'
    assert rs.dm_fired is expected                                               # the death dialog also hides the button: counting that as confirmed is how a lost run read "fired"


# ------------------------------------------------------------------ watch_frame
@pytest.fixture
def frames(monkeypatch, rs):
    abilities = dict(ABILITIES)
    spy = SimpleNamespace(fast=[], fired=[])
    monkeypatch.setattr(orchestrator, 'preset', lambda: {'abilities': abilities})
    monkeypatch.setattr(orchestrator.detect, 'death_screen', lambda f: (False, None))
    monkeypatch.setattr(orchestrator.detect, 'second_wind_badge', lambda f: (False, 0.0))
    monkeypatch.setattr(orchestrator.detect, 'wall_state', lambda f: 'normal')
    monkeypatch.setattr(orchestrator.detect, 'wall_fill', lambda f: 0.9)
    monkeypatch.setattr(orchestrator.detect, 'hp_fill', lambda f: 0.01)
    monkeypatch.setitem(orchestrator.CONFIG['rois'], 'wall_bar', None)
    monkeypatch.setattr(orchestrator, 'demon_mode_always', lambda *a: False)
    monkeypatch.setattr(orchestrator, 'marks', lambda: [])
    monkeypatch.setattr(orchestrator, '_fast_wall_watch', lambda state, ab: spy.fast.append(1) or 'fired')
    monkeypatch.setattr(orchestrator, 'fire_button', lambda f, name, reason, **kw: spy.fired.append((name, reason)) or True)
    monkeypatch.setattr(orchestrator.wave_reader, 'read_wave', lambda f: None)
    rs.gems = SimpleNamespace(poll=lambda f: f)
    rs.abilities, rs.spy = abilities, spy
    return rs


def watch(state, wave, **kwargs):
    return orchestrator.watch_frame(state, object(), observation={'raw_wave': wave}, **kwargs)


def test_one_unreadable_frame_is_not_a_menu_and_two_in_a_row_are_and_a_readable_one_restarts_the_count(frames):
    assert watch(frames, None) is False and frames.no_wave == 1
    assert watch(frames, 1498) is False and frames.no_wave == 0
    assert watch(frames, None) is False and watch(frames, None) == 'menu' and frames.no_wave == 2


def test_a_death_screen_needs_an_unreadable_wave_and_two_frames_before_it_is_dead(monkeypatch, frames):
    monkeypatch.setattr(orchestrator.detect, 'death_screen', lambda f: (True, 0.99))
    monkeypatch.setattr(orchestrator.wave_reader, 'read_wave', lambda f: None)
    assert orchestrator.watch_frame(frames, object()) == 'menu' and frames.dead_frames == 1       # first frame: hands off, not yet dead
    assert orchestrator.watch_frame(frames, object()) == 'dead' and frames.dead_frames == 2
    monkeypatch.setattr(orchestrator.wave_reader, 'read_wave', lambda f: 1498)                    # the dialog covers the wave box: a readable wave means the tower is alive
    assert orchestrator.watch_frame(frames, object()) is False and frames.dead_frames == 0


@pytest.mark.parametrize('wave,opened', [(None, False), (1498, True)], ids=['unreadable-no-battle', 'readable-control'])
def test_a_second_wind_badge_opens_a_window_only_over_a_readable_wave(monkeypatch, frames, wave, opened):
    monkeypatch.setattr(orchestrator.detect, 'second_wind_badge', lambda f: (True, 0.996))
    watch(frames, wave)
    assert (frames.sw_proc_count, frames.sw_floater_seen) == ((1, True) if opened else (0, False))     # menu art out-scores the live badge: a false proc would arm the wall watch on a screen with no wall


@pytest.mark.parametrize('bar', ['wall', 'hp'])
def test_the_rescue_needs_a_readable_wave_even_when_one_unreadable_frame_is_not_yet_a_menu(frames, bar):
    frames.abilities['rescue_bar'] = bar
    frames.sw_proc_count = 1                                                    # a Second Wind has passed and its immunity is over: the watch is open
    assert watch(frames, None) is False and frames.no_wave == 1
    assert frames.spy.fast == [] and frames.spy.fired == []                      # no wall watch is entered and no Demon Mode is tapped over a frame that may be a menu
    assert watch(frames, 1498) is False
    assert (frames.spy.fast if bar == 'wall' else frames.spy.fired) != []        # control: the same frame with a readable wave does start the rescue
