"""What one incomplete offer read does to an offer's response schedule (r15d).

Live r14i windows lost their claim this way: the offer was visible, one batched OCR
read timed out under CPU load, the collector forgot the offer's response schedule,
the next visible read drew a fresh 15-45 s delay, and the 60 s window closed first.
An observation lapse already kept the schedule; an incomplete read now does the same.
Unknown still never taps: input needs a visible read now, then a fresh capture and a
complete fresh recognition of CLAIM + 6.
"""
import json
import os
import random
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from interactions import ad_gems

RECT = (20, 1200, 248, 148)
INCOMPLETE = ('ocr_timeout', 'ocr_error', 'recognition_deadline')


class ScriptedDelays(random.Random):
    """Draw scripted response delays; every other draw takes the lower bound."""

    def __init__(self, delays, delay_range):
        super().__init__(15)
        self.delays, self.delay_range = list(delays), tuple(delay_range)

    def uniform(self, a, b):
        if (a, b) == self.delay_range:
            return self.delays.pop(0)
        return a


@pytest.fixture
def world(monkeypatch):
    w = SimpleNamespace(now=[100.0], events=[], taps=[], grabs=[], reads={}, fresh=[], after=[])
    monkeypatch.setattr(ad_gems.time, 'monotonic', lambda: w.now[0])
    monkeypatch.setattr(ad_gems.time, 'time', lambda: 1000 + w.now[0])
    monkeypatch.setattr(ad_gems.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda frame: 100)
    monkeypatch.setattr(ad_gems.screen, 'identify', lambda frame: SimpleNamespace(name='battle'))
    monkeypatch.setattr(ad_gems.overlays, 'windows', lambda serial, **kw: [ad_gems.overlays.GAME_PKG + '/Activity'])
    monkeypatch.setattr(ad_gems.logger, 'event', lambda kind, **fields: w.events.append((kind, fields)))

    def recognize(frame, **kwargs):
        result = frame.read if hasattr(frame, 'read') else w.reads.get(round(w.now[0], 2), RECT)
        if isinstance(result, str):
            raise ad_gems.RecognitionIncomplete(result)
        return result

    def grab():
        if not w.fresh:
            pytest.fail('capture without an authorized attempt')
        frame = w.fresh.pop(0)
        w.grabs.append((w.now[0], frame.read))
        return frame
    monkeypatch.setattr(ad_gems, 'find_claim_rect', recognize)
    monkeypatch.setattr(ad_gems.capture, 'grab', grab)
    monkeypatch.setattr(ad_gems.act, 'tap', lambda x, y, **kwargs: w.taps.append((w.now[0], x, y)) or {})
    w.frame = lambda read: SimpleNamespace(read=read)
    return w


def collector(w, delays, delay_range=(15, 45)):
    c = ad_gems.AdGemCollector(delay=delay_range, rng=ScriptedDelays(delays, delay_range))
    c.next_at = c.next_window_at = w.now[0]
    return c


def drive(w, c, times):
    for t in times:
        w.now[0] = t
        c.poll(object())


def kinds(w, kind):
    return [fields for name, fields in w.events if name == kind]


def test_an_observation_lapse_keeps_the_response_schedule(world):
    """Pinned before the change: no reads for longer than two poll intervals is not an absence."""
    c = collector(world, [15])
    drive(world, c, [100])
    due = c.status()['response_due_at']
    world.now[0] = 109                      # no poll since 100: stale after 2 x 4 s
    assert c.status()['visibility_reason'] == 'observations_lapsed'
    assert c.status()['response_due_at'] == due == 1115


@pytest.mark.parametrize('reason', ['not_battle', 'waiting_for_game_state'])
def test_a_screen_change_still_forgets_the_offer_and_draws_a_new_delay(world, monkeypatch, reason):
    c = collector(world, [15, 20])
    drive(world, c, [100])
    world.now[0] = 102
    if reason == 'not_battle':
        monkeypatch.setattr(ad_gems.screen, 'identify', lambda frame: SimpleNamespace(name='exit_battle'))
        c.poll(object())
        monkeypatch.setattr(ad_gems.screen, 'identify', lambda frame: SimpleNamespace(name='battle'))
    else:
        c.mark_unknown(reason)
    assert c.status()['response_due_at'] is None
    drive(world, c, [104])
    assert [round(s['response_delay_sec']) for s in kinds(world, 'ad_gems_response_scheduled')] == [15, 20]


def test_an_unexpected_observation_error_still_forgets_the_schedule(world, monkeypatch):
    """Only a read that could not finish keeps the schedule; a crash in recognition does not."""
    c = collector(world, [15, 20])
    drive(world, c, [100])
    def broken(frame, **kwargs):
        raise RuntimeError('recognizer crashed')
    monkeypatch.setattr(ad_gems, 'find_claim_rect', broken)
    drive(world, c, [102])
    assert (c.visibility, c.visibility_reason, c.status()['response_due_at']) == ('unknown', 'recognition_error', None)


def test_an_absent_read_still_forgets_the_schedule(world):
    c = collector(world, [15, 20])
    world.reads = {102: 'ocr_timeout', 104: None}
    drive(world, c, [100, 102, 104, 106])
    assert [round(s['response_delay_sec']) for s in kinds(world, 'ad_gems_response_scheduled')] == [15, 20]
    assert c.status()['response_due_at'] == 1126


# r14i window 4 (operation f9948ebf, 2026-10-23 05:15): the offer was visible from the window's first read.
# Reads at +8.4 s and +27.0 s timed out; the delays the collector drew were 29.6, 17.7 and 29.5 s.
R14I_WINDOW_4 = dict(delays=[29.598, 17.662, 29.464], incomplete={108.4: 'ocr_timeout', 127.0: 'ocr_timeout'},
                     polls=[100, 104, 108.4, 112.3, 116, 120, 124, 127.0, 131.8, 135, 139, 143, 147, 151, 155, 159, 160.5])


def test_r14i_window_4_keeps_its_first_schedule_and_claims_in_the_window(world):
    """The live sequence that lost its claim now claims at the first visible read after the first due time."""
    case = R14I_WINDOW_4
    c = collector(world, case['delays'])
    world.reads = dict(case['incomplete'])
    world.fresh = [world.frame(RECT), world.frame(None)]       # verify before input, then the offer is gone
    drive(world, c, case['polls'])
    assert [round(s['response_delay_sec'], 1) for s in kinds(world, 'ad_gems_response_scheduled')] == [29.6]
    assert [t for t, *_ in world.taps] == [131.8]
    assert [read for _, read in world.grabs] == [RECT, None]
    assert [e['reason'] for e in kinds(world, 'ad_gems_window_ended')] == ['confirmed']
    assert [e['reason'] for e in kinds(world, 'ad_gems_recognition_incomplete')] == ['ocr_timeout', 'ocr_timeout']


@pytest.mark.parametrize('reason', INCOMPLETE)
def test_an_incomplete_read_keeps_the_schedule_and_never_taps(world, reason):
    c = collector(world, [15, 99])
    world.reads = {102: reason, 116: reason}
    drive(world, c, [100, 102])
    status = c.status()
    assert (status['visibility'], status['visibility_reason']) == ('unknown', reason)
    assert status['response_due_at'] == 1115
    drive(world, c, [106, 110, 116])                    # 116: due, but this read is incomplete
    assert world.taps == [] and world.grabs == []
    world.fresh = [world.frame(RECT), world.frame(None)]
    drive(world, c, [120])                              # visible again: fresh capture, verified, then input
    assert [t for t, *_ in world.taps] == [120]
    assert len(kinds(world, 'ad_gems_response_scheduled')) == 1


@pytest.mark.parametrize('fresh', ['ocr_timeout', None])
def test_a_kept_schedule_still_needs_a_complete_fresh_recognition_before_input(world, fresh):
    c = collector(world, [15, 15])
    world.reads = {102: 'ocr_timeout'}
    world.fresh = [world.frame(fresh)]
    drive(world, c, [100, 102, 116])
    assert world.taps == []
    assert [t for t, _ in world.grabs] == [116]
    assert c.last_outcome['reason'] == ('fresh_recognition_incomplete' if fresh else 'fresh_offer_missing')


def test_the_lapse_rule_is_explicit_not_a_reason_name(world):
    c = collector(world, [15])
    drive(world, c, [100])
    c.mark_unknown('observations_lapsed')               # an outside caller naming the reason does not keep it
    assert c.status()['response_due_at'] is None


@pytest.fixture
def captured(monkeypatch):
    """r15's private battle frames (TOWERPILOT_R15_FIXTURES); captured pixels never ship."""
    configured = os.environ.get('TOWERPILOT_R15_FIXTURES')
    if not configured:
        pytest.skip('Set TOWERPILOT_R15_FIXTURES to the private local frame archive')
    import cv2
    root = Path(configured)
    monkeypatch.setattr(ad_gems.settings, 'template_path', lambda rel: root / 'templates' / rel)
    cases = {case['frame']: case['rect'] for case in json.loads((root / 'cases.json').read_text(encoding='utf-8'))}
    return lambda name: (cv2.imread(str(root / name)), cases[name])


def test_captured_frames_real_ocr_one_timeout_keeps_the_schedule_and_taps_inside_the_offer(monkeypatch, captured):
    """Real contours and batched Windows OCR on captured battle frames; the timeout, clock, capture and tap are scripted."""
    from vision import textocr
    offer, rect = captured('frame-1792708202.662.png')
    gone, no_rect = captured('frame-1792709061.732.png')
    assert rect is not None and no_rect is None
    now, events, taps, grabs, urgent = [100.0], [], [], [], []
    monkeypatch.setattr(ad_gems.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(ad_gems.time, 'time', lambda: 1000 + now[0])
    monkeypatch.setattr(ad_gems.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda frame: 100)
    monkeypatch.setattr(ad_gems.screen, 'identify', lambda frame: SimpleNamespace(name='battle'))
    monkeypatch.setattr(ad_gems.overlays, 'windows', lambda serial, **kw: [ad_gems.overlays.GAME_PKG + '/Activity'])
    monkeypatch.setattr(ad_gems.logger, 'event', lambda kind, **fields: events.append((kind, fields)))
    real_read = textocr.read_texts

    def read_texts(crops, **kwargs):
        urgent.append(kwargs.get('urgent'))
        if now[0] == 102:
            raise subprocess.TimeoutExpired('scripted load', kwargs['timeout'])
        return real_read(crops, **{**kwargs, 'timeout': 60})   # the clock is scripted; real OCR gets real time
    monkeypatch.setattr(textocr, 'read_texts', read_texts)
    frames = [offer, gone]
    monkeypatch.setattr(ad_gems.capture, 'grab', lambda: grabs.append(now[0]) or frames.pop(0))
    monkeypatch.setattr(ad_gems.act, 'tap', lambda x, y, **kwargs: taps.append((now[0], x, y)) or {})
    c = ad_gems.AdGemCollector(delay=(15, 45), rng=ScriptedDelays([15, 99], (15, 45)))
    c.next_at = c.next_window_at = now[0]
    for t in (100, 102, 106, 110, 116):
        now[0] = t
        c.poll(offer)
    assert [f['reason'] for k, f in events if k == 'ad_gems_recognition_incomplete'] == ['ocr_timeout']
    assert [round(f['response_delay_sec']) for k, f in events if k == 'ad_gems_response_scheduled'] == [15]
    [(when, x, y)] = taps
    x0, y0, w, h = rect
    assert when == 116 and grabs == [116, 116]
    assert x0 < x < x0 + w and y0 < y < y0 + h
    assert c.last_outcome['reason'] == 'offer_disappeared'
    assert urgent and all(flag is True for flag in urgent)
