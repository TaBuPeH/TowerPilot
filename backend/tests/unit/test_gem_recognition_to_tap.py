"""Generated frames go through the REAL recognizers into a tap decision.

Every other default test of a tap stubs the recognizer (`find_orbit_gem`, `find_claim_rect`,
`read_gem_balance`), so a recognizer that stopped finding gems, or found them in the wrong
place, passed them all. Here a frame drawn in code (cv2 shapes and digits from a drawn font;
no game image) is read by the real recognizer, and the stubs sit only at the device edge: the
tap recorder, the clock, capture, and the screen / wave / overlay gates.

Scope: the recognizer-to-decision seam. How taps are paced, and in which order the tapper
waits and captures, is deliberately not asserted; a case only requires that a recognized gem
is tapped where the recognizer measured it, and that a frame without the shape is never
tapped however long it is polled.
"""
import math
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from flows import shard
from interactions import ad_gems
from tap_recorder import tap_recorder
from test_gem_balance import font, hud          # noqa: F401 - digit-font fixture and counter frame
from test_orbit_gems import scene
from vision import orbit_gems, textocr

ARC_ANGLE = 135.                                 # the tapper's default arc direction, degrees above +x
POLLS = 12                                       # frames offered to the tapper per case
ON_ARC_PX = 2.5                                  # rounding plus the recognizer's centre estimate


def gem_center(radius):
    """Where a gem sits when it orbits the reference centre at `radius`, on the tapper's arc."""
    (cx, cy), _ = orbit_gems.reference_geometry()
    return round(cx+radius*math.cos(math.radians(ARC_ANGLE))), round(cy-radius*math.sin(math.radians(ARC_ANGLE)))


@pytest.fixture
def orbit_world(monkeypatch, font):
    """A tapper over the real recognizers. The screen never changes, so no reward is ever confirmed."""
    world = SimpleNamespace(now=100., frame=None, events=[], font=font)
    monkeypatch.setattr(shard.time, 'monotonic', lambda: world.now)
    monkeypatch.setattr(shard.time, 'time', lambda: 1000+world.now)
    monkeypatch.setattr(shard.capture, 'grab', lambda: world.frame)
    monkeypatch.setattr(shard.capture, 'layout_offset', 0)
    monkeypatch.setattr(shard.wave_reader, 'read_wave', lambda frame: 123)
    monkeypatch.setattr(shard.screen, 'identify', lambda frame: SimpleNamespace(name='battle'))
    monkeypatch.setattr(shard, 'battlefield_observation', lambda *args, **kw: (True, 'live', args[1]))
    monkeypatch.setattr(shard, '_in_ability_row', lambda point: False)
    monkeypatch.setattr(shard.logger, 'event', lambda kind, **data: world.events.append((kind, data)))
    monkeypatch.setattr(shard.act, 'wait_for_tap_slot', lambda *args, **kw: None, raising=False)
    world.taps = tap_recorder(monkeypatch, shard.act)
    world.tapper = shard.GemOrbitTapper(enabled=True, jitter=0)

    def frame(kind='gem', radius=200., rotation=0., scale=1.):
        """Battle HUD (diamond icon, gem counter) plus one orbiting square; kind None leaves only the HUD."""
        center = gem_center(radius)
        battle = scene(rotation, center=center, kind='empty' if kind is None else kind, scale=scale)
        if kind is None:
            battle[center[1]-75:center[1]+75, center[0]-75:center[0]+75] = 0
        return np.maximum(battle, hud(font, '200'))

    def offer(frame_, polls=POLLS):
        world.frame = frame_
        for _ in range(polls):
            world.now += 7.
            world.tapper.poll(frame_)
    world.frame_of, world.offer = frame, offer
    return world


def arc_through(radius):
    (cx, cy), _ = orbit_gems.reference_geometry()
    return [(cx+radius*math.cos(math.radians(ARC_ANGLE+shard.GEM_ORBIT_SPREAD*k)),
             cy-radius*math.sin(math.radians(ARC_ANGLE+shard.GEM_ORBIT_SPREAD*k))) for k in (-1, 0, 1)]


@pytest.mark.parametrize('radius', [170., 230.])
@pytest.mark.parametrize('rotation,scale', [(0, 1.), (45, 1.), (225, .8)])
def test_a_recognized_orbit_gem_is_tapped_on_the_arc_through_where_it_was_found(orbit_world, radius, rotation, scale):
    orbit_world.offer(orbit_world.frame_of(radius=radius, rotation=rotation, scale=scale))
    assert orbit_world.taps.points, 'a gem the recognizer finds produced no tap'
    assert 'gem_orbit_window' in [kind for kind, _ in orbit_world.events]
    aimed = arc_through(radius)
    for tapped in orbit_world.taps.points:
        assert min(math.dist(tapped, point) for point in aimed) <= ON_ARC_PX, (
            f'tap {tapped} is off the arc {aimed} through the gem at radius {radius}')


@pytest.mark.parametrize('kind', [None, 'empty', 'filled', 'boss', 'red'])
def test_a_frame_without_the_gem_shape_is_never_tapped(orbit_world, kind):
    orbit_world.offer(orbit_world.frame_of(kind), polls=3*POLLS)
    orbit_world.taps.assert_no_taps(f'{kind!r} square')
    assert 'gem_orbit_window' not in [kind for kind, _ in orbit_world.events], 'an attempt was opened for a non-gem'

# ---- Ad gems: the real offer finder (cyan block in the left rail), OCR text stubbed ----

OFFER_LEFT, OFFER_RIGHT, OFFER_HEIGHT = 20, 268, 148     # the drawn offer border, native pixels


def offer_frame(top=1270, *, color=(255, 255, 0), right=OFFER_RIGHT):
    """A battle frame with an offer-shaped cyan block in the rail; `top=None` draws no block."""
    frame = np.zeros((2560, 1080, 3), np.uint8)
    if top is not None:
        cv2.rectangle(frame, (OFFER_LEFT, top), (right, top+OFFER_HEIGHT), color, 4)
    return frame


@pytest.fixture
def ad_world(monkeypatch, tmp_path):
    """An enabled collector over the real finder. The OCR engine is the only recognizer stubbed:
    it reads `world.text` from every crop the finder hands it, so which crops exist is the finder's call."""
    world = SimpleNamespace(text='CLAIM 6', crops=[], grabs=[], grab_calls=0, events=[])

    def read_texts(crops, **kwargs):
        world.crops.extend(crops)
        return [world.text]*len(crops)
    monkeypatch.setattr(ad_gems.settings, 'template_path', lambda _: tmp_path/'missing.png')   # no claim template: text branch
    monkeypatch.setattr(textocr, 'read_texts', read_texts)
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda frame: 100)
    monkeypatch.setattr(ad_gems.screen, 'identify', lambda frame: SimpleNamespace(name='battle'))
    monkeypatch.setattr(ad_gems.overlays, 'windows', lambda serial, **kw: [ad_gems.overlays.GAME_PKG+'/Activity'])
    def grab():
        world.grab_calls += 1
        return world.grabs.pop(0)
    monkeypatch.setattr(ad_gems.capture, 'grab', grab)
    monkeypatch.setattr(ad_gems.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(ad_gems.logger, 'event', lambda name, **data: world.events.append((name, data)))
    world.taps = tap_recorder(monkeypatch, ad_gems.act)
    world.collector = ad_gems.AdGemCollector(delay=(0, 0))
    return world


@pytest.mark.parametrize('top', [1120, 1270, 2069])
def test_a_found_ad_offer_is_tapped_inside_its_drawn_border_and_confirmed_when_it_is_gone(ad_world, top):
    ad_world.grabs[:] = [offer_frame(top), offer_frame(None)]       # the fresh look, then the frame after the tap
    ad_world.collector.poll(offer_frame(top))
    (x, y), = ad_world.taps.points
    inset = ad_gems.EDGE_INSET_PX
    assert OFFER_LEFT+inset <= x <= OFFER_RIGHT-inset and top+inset <= y <= top+OFFER_HEIGHT-inset, (x, y)
    assert ad_world.collector.last_outcome['confirmed'] is True


@pytest.mark.parametrize('case', ['no_block', 'block_too_narrow', 'wrong_text', 'text_without_six'])
def test_a_frame_without_a_real_ad_offer_is_never_tapped(ad_world, case):
    frame = offer_frame(None if case == 'no_block' else 1270, right=OFFER_LEFT+60 if case == 'block_too_narrow' else OFFER_RIGHT)
    ad_world.text = {'wrong_text': 'BUY 6', 'text_without_six': 'CLAIM 20'}.get(case, 'CLAIM 6')
    ad_world.grabs[:] = []                    # a fresh capture would mean the collector went on to verify the offer
    for _ in range(6):
        ad_world.collector.poll(frame)
    ad_world.taps.assert_no_taps(case)
    assert ad_world.grab_calls == 0, 'an offer was verified (fresh capture) that the finder should have rejected'
    assert 'ad_gems_attempt' not in [name for name, _ in ad_world.events], 'a claim attempt was opened'
    assert not [name for name, _ in ad_world.events if 'error' in name], 'the collector failed instead of declining'
    assert bool(ad_world.crops) == (case in ('wrong_text', 'text_without_six')), 'the finder offered the wrong crops to OCR'