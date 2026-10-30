"""Optional replay of captured Home frames with the real readers (r15c).

Captured game pixels and account templates never ship in the source package: set TOWERPILOT_R15C_FIXTURES to the
private, local-only archive (frames/ and templates/) to run these. Screen identification, the batched Windows OCR of
the offer, and the Home counter read all run for real; only capture, input and the window list are scripted.
"""
import json
import os
import random
from pathlib import Path
from types import SimpleNamespace

import cv2
import pytest

from device import overlays
from interactions import ad_gems
from vision import detect, ocr, screen, wave_reader

GAME = overlays.GAME_PKG + '/com.unity3d.player.UnityPlayerActivity'
AD = 'com.mumu.store/com.mumu.store.MainActivity'
OFFER = (5, 200, 234, 120)
# frame: (screen, offer rectangle on Home, Home counter). Measured once on these captures; never a template.
CASES = {
    'home-offer-2522.png': ('home', OFFER, 2522),
    'home-offer-2522-r14.png': ('home', OFFER, 2522),
    'home-offer-966.png': ('home', OFFER, 966),
    'home-offer-993.png': ('home', OFFER, 993),
    'home-no-offer-966.png': ('home', None, 966),
    'home-no-offer-972.png': ('home', None, 972),          # the offer gone and the left column moved up: after a claim
    'store-641.png': ('unknown', None, None),                # the Store: same top bar; this account's '4' never reads
    'store-603.png': ('unknown', None, 603),
    'missions-menu.png': ('unknown', None, 2055),
    'event-menu.png': ('unknown', None, 780),
    'modules.png': ('modules', None, 719),
    'guild.png': ('guild', None, 972),
    'battle-offer.png': ('battle', None, None),
}


@pytest.fixture
def fixtures(monkeypatch):
    configured = os.environ.get('TOWERPILOT_R15C_FIXTURES')
    if not configured:
        pytest.skip('Set TOWERPILOT_R15C_FIXTURES to the private local Home frame archive')
    root = Path(configured)
    assert (root / 'frames').is_dir() and (root / 'templates').is_dir(), root
    from player import accounts
    templates = root / 'templates'
    monkeypatch.setattr(accounts, 'template_path', lambda base, cfg, rel, write=False: templates / rel)
    monkeypatch.setattr(accounts, 'template_files', lambda base, cfg, pattern='*/*.png': sorted(templates.glob(pattern)))
    monkeypatch.setattr(detect, '_TPL_CACHE', {})
    monkeypatch.setattr(ocr, '_glyph_cache', {})
    monkeypatch.setattr(wave_reader, '_templates', None)
    return lambda name: cv2.imread(str(root / 'frames' / name))


def test_each_captured_frame_reads_as_measured(fixtures):
    seen = {}
    for name, (expected_screen, rect, balance) in CASES.items():
        frame = fixtures(name)
        assert frame is not None and frame.shape == (2560, 1080, 3), name
        name_read = screen.identify(frame).name
        offer = ad_gems.find_claim_rect(frame) if name_read == 'home' else None
        seen[name] = (name_read, offer, ocr.read_gem_balance(frame, ocr.HOME_GEM_BALANCE_REGION))
    assert seen == CASES, json.dumps(seen)


@pytest.fixture
def scripted(fixtures, monkeypatch):
    w = SimpleNamespace(queue=[], taps=[], grabs=[], events=[], windows=[GAME])
    monkeypatch.setattr(ad_gems.time, 'sleep', lambda seconds: None)

    def grab():
        if not w.queue:
            pytest.fail('unexpected capture')
        name = w.queue.pop(0)
        w.grabs.append(name)
        return fixtures(name)
    monkeypatch.setattr(ad_gems.capture, 'grab', grab)
    monkeypatch.setattr(ad_gems.act, 'tap', lambda x, y, **kwargs: w.taps.append((x, y)) or {})
    monkeypatch.setattr(ad_gems.overlays, 'windows', lambda serial, **kwargs: list(w.windows))
    monkeypatch.setattr(ad_gems.logger, 'event', lambda kind, **fields: w.events.append((kind, fields)))
    monkeypatch.setattr(ad_gems.logger, 'shot', lambda image, name: None)
    w.collector = ad_gems.AdGemCollector(delay=(0, 0), rng=random.Random(15))
    return w


def claim(w):
    return next(fields for kind, fields in w.events if kind == 'ad_gems_claim')


def test_a_captured_home_offer_is_tapped_inside_it_and_the_captured_plus_six_confirms(scripted, fixtures):
    w = scripted
    w.queue = ['home-offer-966.png', 'home-no-offer-972.png', 'home-no-offer-972.png']
    w.collector.poll_home(fixtures('home-offer-966.png'))
    [(x, y)] = w.taps
    assert OFFER[0] < x < OFFER[0] + OFFER[2] and OFFER[1] < y < OFFER[1] + OFFER[3]
    assert (claim(w)['confirmed'], claim(w)['before'], claim(w)['after']) == (True, 966, 972)
    assert w.collector.window_deadline is None


@pytest.mark.parametrize('name', [name for name, case in CASES.items() if case[0] != 'home'])
def test_a_menu_store_or_battle_frame_gets_no_capture_and_no_input(scripted, fixtures, name):
    scripted.collector.poll_home(fixtures(name))
    assert scripted.taps == [] and scripted.grabs == []


def test_a_home_frame_without_the_offer_gets_no_capture_and_no_input(scripted, fixtures):
    scripted.collector.poll_home(fixtures('home-no-offer-966.png'))
    assert scripted.taps == [] and scripted.grabs == [] and scripted.collector.visibility == 'absent'


def test_a_person_opening_the_store_before_the_tap_ends_the_window(scripted, fixtures):
    w = scripted
    w.queue = ['store-603.png']
    w.collector.poll_home(fixtures('home-offer-2522.png'))
    assert w.taps == [] and w.collector.window_deadline is None
    assert [f['reason'] for k, f in w.events if k == 'ad_gems_skip'] == ['fresh_frame_not_home']


def test_an_ad_drawn_over_home_blocks_the_tap(scripted, fixtures):
    w = scripted
    w.windows = [AD, GAME]
    w.queue = ['home-offer-2522.png']
    w.collector.poll_home(fixtures('home-offer-2522.png'))
    assert w.taps == [] and [f['reason'] for k, f in w.events if k == 'ad_gems_skip'] == ['in_front:com.mumu.store']


def test_a_menu_opened_after_the_tap_stops_the_confirmation(scripted, fixtures):
    w = scripted
    w.queue = ['home-offer-2522.png', 'missions-menu.png']
    w.collector.poll_home(fixtures('home-offer-2522.png'))
    assert len(w.taps) == 1 and w.queue == []
    assert (claim(w)['confirmed'], claim(w)['reason']) == (False, 'post_claim_not_home')


@pytest.mark.parametrize('brightness', [0.8, 0.5])
def test_a_dimmed_home_behind_a_dialog_backdrop_gets_no_input(scripted, fixtures, brightness):
    """Derived from a captured frame (no dialog over Home was captured): normalized template matching still names a
    uniformly dimmed Home 'home', so the bright-pixel offer recognition is what refuses it."""
    dimmed = (fixtures('home-offer-2522.png') * brightness).astype('uint8')
    assert screen.identify(dimmed).name == 'home' and ad_gems.find_claim_rect(dimmed) is None
    scripted.collector.poll_home(dimmed)
    assert scripted.taps == [] and scripted.grabs == []


def test_a_counter_this_account_cannot_read_refuses_the_tap(scripted, fixtures, monkeypatch):
    """No Home frame with an unreadable counter was captured, so the Store's identical top bar (641, whose '4' scores
    0.747 against this account's digit template) stands in for the counter pixels of a Home frame."""
    w = scripted
    home, store = fixtures('home-offer-2522.png'), fixtures('store-641.png')
    region = ocr.HOME_GEM_BALANCE_REGION
    x, y, width, height = region
    home[y:y + height, x:x + width] = store[y:y + height, x:x + width]
    assert screen.identify(home).name == 'home' and ocr.read_gem_balance(home, region) is None
    monkeypatch.setattr(ad_gems.capture, 'grab', lambda: home.copy())
    w.collector.poll_home(home.copy())
    assert w.taps == [] and [f['reason'] for k, f in w.events if k == 'ad_gems_skip'] == ['balance_unreadable']