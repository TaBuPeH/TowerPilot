"""Ad Gems must not route to the Store or tap when the HUD shifts."""
from interactions import ad_gems
from tap_recorder import tap_recorder
from types import SimpleNamespace
import pytest


@pytest.fixture(autouse=True)
def battle_screen(monkeypatch):
    monkeypatch.setattr(ad_gems.screen, 'identify', lambda frame: SimpleNamespace(name='battle'))
    monkeypatch.setattr(ad_gems.overlays, 'windows', lambda serial, **kw: [ad_gems.overlays.GAME_PKG+'/Activity'])


def test_missing_offer_image_requires_both_claim_and_six(monkeypatch,tmp_path):
    import cv2
    import numpy as np
    from vision import textocr
    monkeypatch.setattr(ad_gems.settings,'template_path',lambda _:tmp_path/'missing.png')
    frame=np.zeros((2560,1080,3),np.uint8)
    cv2.rectangle(frame,(15,400),(195,500),(255,255,0),4)
    monkeypatch.setattr(textocr,'read_texts',lambda crops, **kwargs:['BUY 6']*len(crops))
    assert ad_gems.find_claim(frame) is None
    monkeypatch.setattr(textocr,'read_texts',lambda crops, **kwargs:['CLAIM 20']*len(crops))
    assert ad_gems.find_claim(frame) is None
    monkeypatch.setattr(textocr,'read_texts',lambda crops, **kwargs:['6 CLAIM']*len(crops))
    assert ad_gems.find_claim(frame) is not None

def test_disabled_and_nonbattle_never_tap(monkeypatch):
    # The collector catches every Exception around its tap, so a stub that raises proves nothing:
    # record the taps, and record whether the offer finder was even consulted.
    taps=tap_recorder(monkeypatch,ad_gems.act)
    looks=[]
    monkeypatch.setattr(ad_gems,'find_claim_rect',lambda f, **kwargs:looks.append(f) or (0,1250,200,100))
    monkeypatch.setattr(ad_gems.capture,'grab',lambda:object())
    frame=object()
    monkeypatch.setattr(ad_gems.wave_reader,'read_wave',lambda f:100)
    disabled=ad_gems.AdGemCollector(False,delay=(0,0))
    disabled.poll(frame)
    assert looks==[] and disabled.visibility=='unknown'   # a disabled collector observes nothing
    monkeypatch.setattr(ad_gems.wave_reader,'read_wave',lambda f:None)
    nonbattle=ad_gems.AdGemCollector(True,delay=(0,0))
    nonbattle.poll(frame)
    assert looks==[] and nonbattle.visibility=='unknown'  # no wave: not a battle, no search for an offer
    taps.assert_no_taps('disabled collector or a frame that is not a battle')

def test_claim_rechecks_position_and_confirms_disappearance(monkeypatch):
    events=[];taps=[]
    monkeypatch.setattr(ad_gems.wave_reader,'read_wave',lambda f:100)
    points=iter([(0,1250,200,100),(0,1250,200,100),None])
    monkeypatch.setattr(ad_gems,'find_claim_rect',lambda f, **kwargs:next(points))
    monkeypatch.setattr(ad_gems.capture,'grab',lambda:None)
    monkeypatch.setattr(ad_gems.time,'sleep',lambda t:None)
    monkeypatch.setattr(ad_gems.act,'tap',lambda *a,**k:taps.append(a) or {})
    monkeypatch.setattr(ad_gems.logger,'event',lambda name,**k:events.append((name,k)))
    c=ad_gems.AdGemCollector(delay=(0, 0));c.poll(None);c.poll(None)
    assert len(taps) == 1
    assert 11 <= taps[0][0] <= 188 and 1261 <= taps[0][1] <= 1338
    assert next(data for name, data in events if name == 'ad_gems_claim')['confirmed'] is True
    assert events[-1][0] == 'ad_gems_window_ended'
    assert events[-1][1]['reason'] == 'confirmed'

def test_moving_claim_is_not_clicked(monkeypatch):
    events=[]
    taps=tap_recorder(monkeypatch,ad_gems.act)
    monkeypatch.setattr(ad_gems.wave_reader,'read_wave',lambda f:100)
    points=iter([(0,1250,200,100),(0,1450,200,100)])
    monkeypatch.setattr(ad_gems,'find_claim_rect',lambda f, **kwargs:next(points))
    monkeypatch.setattr(ad_gems.capture,'grab',lambda:None)
    monkeypatch.setattr(ad_gems.logger,'event',lambda name,**k:events.append((name,k)))
    ad_gems.AdGemCollector(delay=(0, 0)).poll(None)
    taps.assert_no_taps('the offer moved 200 px between the decision frame and the fresh look')
    # It reached the movement guard (a swallowed error would also leave no tap):
    assert [k['reason'] for name,k in events if name=='ad_gems_skip']==['offer_moved']
    assert not [k for name,k in events if name=='ad_gems_attempt_finished' and k.get('reason')=='claim_verification_error']


def test_contour_edge_noise_does_not_restart_verified_claim(monkeypatch):
    # Measured on successive live frames: edge/size deltas sum to 15px,
    # while the verified button's center moves only 4px.
    initial, fresh, after = object(), object(), object()
    rectangles = {initial: (10, 1384, 261, 157), fresh: (17, 1383, 255, 158), after: None}
    frames = iter([fresh, after])
    taps, events = [], []
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda frame: 100)
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda frame, **kwargs: rectangles.get(frame))
    monkeypatch.setattr(ad_gems.capture, 'grab', lambda: next(frames))
    monkeypatch.setattr(ad_gems.time, 'sleep', lambda _: None)
    monkeypatch.setattr(ad_gems.act, 'tap', lambda *args, **kwargs: taps.append(args) or {})
    monkeypatch.setattr(ad_gems.logger, 'event', lambda name, **data: events.append((name, data)))

    collector = ad_gems.AdGemCollector(delay=(0, 0))
    assert collector.poll(initial) is after
    assert len(taps) == 1
    assert collector.last_outcome['confirmed'] is True
    assert next(data for name, data in events if name == 'ad_gems_tap')['claim_rect'] == rectangles[fresh]


def test_offer_wait_is_nonblocking_and_revalidates_after_delay(monkeypatch):
    import random
    now = [100.0]
    frame, fresh, after = object(), object(), object()
    taps, events = [], []
    monkeypatch.setattr(ad_gems.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(ad_gems.time, 'sleep', lambda _: None)
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda f: 100)
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda f, **kwargs: None if f is after else (0, 1250, 200, 100))
    monkeypatch.setattr(ad_gems.capture, 'grab', lambda: pytest.fail('captured during response wait'))
    monkeypatch.setattr(ad_gems.act, 'tap', lambda *a, **kw: taps.append(a) or {})
    monkeypatch.setattr(ad_gems.logger, 'event', lambda kind, **kw: events.append((kind, kw)))
    collector = ad_gems.AdGemCollector(rng=random.Random(8))
    assert collector.poll(frame) is frame
    assert 3 <= collector.claim_at - now[0] <= 10
    assert taps == []
    now[0] = collector.claim_at
    frames = iter([fresh, after])
    monkeypatch.setattr(ad_gems.capture, 'grab', lambda: next(frames))
    assert collector.poll(frame) is after
    assert len(taps) == 1
    assert 11 <= taps[0][0] <= 188 and 1261 <= taps[0][1] <= 1338
    tap_event = next(data for name, data in events if name == 'ad_gems_tap')
    assert 3 <= tap_event['offer_wait_sec'] <= 10
    assert collector.next_at == 820
    assert collector.window_deadline is None


def test_offer_disappearing_during_wait_cancels_pending_claim(monkeypatch):
    now = [100.0]
    visible = [True]
    monkeypatch.setattr(ad_gems.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda f: 100)
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda f, **kwargs: (0, 1250, 200, 100) if visible[0] else None)
    monkeypatch.setattr(ad_gems.act, 'tap', lambda *a, **kw: pytest.fail('stale offer tapped'))
    collector = ad_gems.AdGemCollector(delay=(5, 5))
    collector.poll(None)
    visible[0] = False
    now[0] = 106
    collector.poll(None)
    assert collector.claim_at is None
    visible[0] = True
    now[0] = 111
    collector.poll(None)
    assert collector.claim_at == 116


def test_default_poll_and_response_deadlines_vary_with_seed(monkeypatch):
    import random
    monkeypatch.setattr(ad_gems.time, 'monotonic', lambda: 100)
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda f: 100)
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda f, **kwargs: (0, 1250, 200, 100))
    collectors = [ad_gems.AdGemCollector(rng=random.Random(seed)) for seed in range(8)]
    for collector in collectors:
        collector.poll(None)
        assert 102 <= collector.next_at <= 104
        assert 103 <= collector.claim_at <= 110
    assert len({c.next_at for c in collectors}) == 8
    assert len({c.claim_at for c in collectors}) == 8


@pytest.mark.parametrize('after_wave,after_screen,dry', [
    (None, 'game_stats', False),
    (100, 'exit_battle', False),
    (100, 'battle', True),
])
def test_claim_never_confirms_a_departed_battle_or_dry_run(monkeypatch, after_wave, after_screen, dry):
    initial, fresh, after = object(), object(), object()
    events = []
    frames = iter([fresh, after])
    monkeypatch.setattr(ad_gems.capture, 'grab', lambda: next(frames))
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda f: after_wave if f is after else 100)
    monkeypatch.setattr(ad_gems.screen, 'identify', lambda f: SimpleNamespace(name=after_screen if f is after else 'battle'))
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda f, **kwargs: None if f is after else (0, 1250, 200, 100))
    monkeypatch.setattr(ad_gems.act, 'tap', lambda *a, **kw: {'dry_run': dry})
    monkeypatch.setattr(ad_gems.time, 'sleep', lambda _: None)
    monkeypatch.setattr(ad_gems.logger, 'event', lambda name, **kw: events.append((name, kw)))
    result = ad_gems.AdGemCollector(delay=(0, 0)).poll(initial)
    assert result is (fresh if dry else after)
    assert not any(data['confirmed'] for name, data in events if name == 'ad_gems_claim')


def test_modal_over_readable_wave_prevents_claim(monkeypatch):
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda f: 100)
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda f, **kwargs: (0, 1250, 200, 100))
    monkeypatch.setattr(ad_gems.capture, 'grab', lambda: None)
    monkeypatch.setattr(ad_gems.screen, 'identify', lambda f: SimpleNamespace(name='exit_battle'))
    monkeypatch.setattr(ad_gems.act, 'tap', lambda *a, **kw: pytest.fail('tap on modal'))
    ad_gems.AdGemCollector(delay=(0, 0)).poll(None)


@pytest.mark.parametrize('cover', ['com.android.vending/Activity', 'app.lawnchair/Launcher',
                                   'com.bluestacks.appmart/Activity', 'NotificationShade'])
def test_an_app_covering_the_game_prevents_claim(monkeypatch, cover):
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda f: 100)
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda f, **kwargs: (0, 1250, 200, 100))
    monkeypatch.setattr(ad_gems.capture, 'grab', lambda: None)
    monkeypatch.setattr(ad_gems.overlays, 'windows', lambda serial, **kw: [cover, ad_gems.overlays.GAME_PKG+'/Activity'])
    monkeypatch.setattr(ad_gems.act, 'tap', lambda *a, **kw: pytest.fail('tap on another app'))
    ad_gems.AdGemCollector(delay=(0, 0)).poll(None)

def test_separate_readiness_requirements():
    from player import readiness
    cfg={'active_instance':'main','instances':{'main':{}},'loadouts':{}}
    body={'kind':'coin','gather':{'flying_gem':False,'ad_gems':True,'free_store_gems':False,'guild':False,'quests_8h':False,'quest_rewards':False}}
    paths={p for r in readiness.requirements(cfg,body) for p in r['alternatives']}
    assert 'buttons/ad_gems_claim.png' in paths
    assert 'icons/free_gems.png' not in paths
    assert not any(p.startswith('floaters/') for p in paths)
    body['gather'].update(ad_gems=False,free_store_gems=True)
    paths={p for r in readiness.requirements(cfg,body) for p in r['alternatives']}
    assert 'icons/free_gems.png' in paths
    assert 'buttons/ad_gems_claim.png' not in paths


def test_guild_rewards_do_not_require_store_purchases():
    from player import readiness
    cfg = {'active_instance': 'main', 'instances': {'main': {}}, 'loadouts': {}}
    body = {'kind': 'coin', 'gather': {'guild': True}}
    paths = {p for r in readiness.requirements(cfg, body) for p in r['alternatives']}
    assert 'icons/guild_header.png' in paths
    assert 'icons/guild_coin.png' not in paths
    body['gather']['guild_store'] = True
    paths = {p for r in readiness.requirements(cfg, body) for p in r['alternatives']}
    assert 'icons/guild_coin.png' in paths


def test_native_wide_offer_uses_amount_glyph_when_ocr_omits_six(monkeypatch,tmp_path):
    import cv2
    import numpy as np
    from vision import textocr
    monkeypatch.setattr(ad_gems.settings,'template_path',lambda _:tmp_path/'missing.png')
    frame=np.zeros((2560,1080,3),np.uint8)
    cv2.rectangle(frame,(20,1270),(268,1418),(255,255,0),4)
    monkeypatch.setattr(textocr,'read_texts',lambda crops, **kwargs:['CLAIM']*len(crops))
    observed=[]
    monkeypatch.setattr(ad_gems,'_six_amount',lambda p:observed.append(p.shape) or True)
    assert ad_gems.find_claim(frame)==(144,1344)
    assert observed[0][1]>240
    monkeypatch.setattr(ad_gems,'_six_amount',lambda p:False)
    assert ad_gems.find_claim(frame) is None


def test_offer_moves_with_effect_rows_and_closed_upgrade_panel(monkeypatch,tmp_path):
    import cv2
    import numpy as np
    from vision import textocr
    monkeypatch.setattr(ad_gems.settings,'template_path',lambda _:tmp_path/'missing.png')
    monkeypatch.setattr(textocr,'read_texts',lambda crops, **kwargs:['CLAIM 6']*len(crops))
    for top in (1120,1270,1390,2069):
        frame=np.zeros((2560,1080,3),np.uint8)
        cv2.rectangle(frame,(20,top),(268,top+148),(255,255,0),4)
        assert ad_gems.find_claim(frame)==(144,top+74)


@pytest.mark.parametrize('upper', [False, True])
def test_entire_press_stays_five_pixels_inside_offer_at_jitter_extremes(monkeypatch, upper):
    cfg = {'tap': {'jitter_px': 8, 'jitter_ms': [15, 50], 'max_rate_per_sec': 12},
           'screen': {'width': 1080, 'height': 2560}, 'loop': {'dry_run': False}}
    monkeypatch.setattr(ad_gems.settings, 'CONFIG', cfg)
    monkeypatch.setattr(ad_gems.act, 'CONFIG', cfg)
    monkeypatch.setattr(ad_gems.act, 'instance', lambda: {'allow_taps': True})
    rng = SimpleNamespace(randint=lambda low, high: high if upper else low,
                          uniform=lambda low, high: low, gauss=lambda *args: 85)
    monkeypatch.setattr(ad_gems.act, 'random', rng)
    monkeypatch.setattr(ad_gems.time, 'sleep', lambda _: None)
    monkeypatch.setattr(ad_gems.act, '_last_tap', 0)
    commands = []
    monkeypatch.setattr(ad_gems.act, '_input', lambda *args, deadline=None: commands.append(args))
    rect = (20, 1200, 248, 148)
    point = ad_gems._claim_point(rect, rng)
    ad_gems.act.tap(*point)
    _, x, y, end_x, end_y, _ = commands[0]
    assert 25 <= x <= 262 and 1205 <= y <= 1342
    assert (end_x, end_y) == ((262, 1342) if upper else (25, 1205))


def test_claim_sampling_spans_button_interior_and_refuses_tiny_targets(monkeypatch):
    import random
    monkeypatch.setattr(ad_gems.settings, 'CONFIG', {
        'tap': {'jitter_px': 4}, 'screen': {'width': 1080, 'height': 2560}})
    rng = random.Random(21)
    points = [ad_gems._claim_point((20, 1200, 248, 148), rng) for _ in range(64)]
    assert max(x for x, _ in points)-min(x for x, _ in points) > 150
    assert max(y for _, y in points)-min(y for _, y in points) > 80
    assert all(31 <= x <= 256 and 1211 <= y <= 1336 for x, y in points)
    assert ad_gems._claim_point((20, 1200, 20, 20), rng) is None


def test_requested_long_offer_delay_does_not_tap_early(monkeypatch):
    import random
    now = [100.0]
    monkeypatch.setattr(ad_gems.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda _: 100)
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda _, **kwargs: (20, 1200, 248, 148))
    monkeypatch.setattr(ad_gems.act, 'tap', lambda *a, **kw: pytest.fail('early tap'))
    collector = ad_gems.AdGemCollector(delay=(15, 45), rng=random.Random(17))
    collector.poll(None)
    assert 115 <= collector.claim_at <= 145
    now[0] = 114.9
    collector.poll(None)


@pytest.mark.parametrize('confirmed, cooldown', [(True, 26), (False, 9)])
def test_custom_settings_control_poll_inset_and_claim_cooldown(monkeypatch, confirmed, cooldown):
    now = [100.0]
    initial, fresh, after = object(), object(), object()
    frames = iter([fresh, after])
    rect = (20, 1200, 248, 148)
    taps, events = [], []
    monkeypatch.setattr(ad_gems.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(ad_gems.time, 'sleep', lambda _: None)
    monkeypatch.setattr(ad_gems.capture, 'grab', lambda: next(frames))
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda _: 100)
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda f, **kwargs: None if f is after and confirmed else rect)
    monkeypatch.setattr(ad_gems.act, 'tap', lambda *a, **kw: taps.append(a) or {})
    monkeypatch.setattr(ad_gems.logger, 'event', lambda name, **kw: events.append((name, kw)))
    monkeypatch.setattr(ad_gems.settings, 'CONFIG', {
        'tap': {'jitter_px': 4}, 'screen': {'width': 1080, 'height': 2560}})
    rng = SimpleNamespace(randint=lambda low, high: low, uniform=lambda low, high: low)
    monkeypatch.setattr(ad_gems.settings, 'instance', lambda: {'serial': 'test-device'})
    collector = ad_gems.AdGemCollector(delay=(15, 15), rng=rng, options={
        'poll_sec': [6, 7], 'edge_inset_px': 12,
        'confirmed_cooldown_sec': [41, 42], 'retry_cooldown_sec': [9, 10]})
    collector.poll(initial)
    assert collector.next_at == 106
    now[0] = 115
    collector.poll(initial)
    assert taps == [(38, 1218)]  # inset12 + position jitter4 + press drift2
    assert next(data for name, data in events if name == 'ad_gems_tap')['edge_inset_px'] == 12
    assert collector.next_at == 115 + cooldown


@pytest.mark.parametrize('mode', ['absent', 'nonbattle', 'unconfirmed', 'dry', 'blocked', 'refused'])
def test_attempt_window_expires_without_extending_and_waits_for_next_cycle(monkeypatch, mode):
    now = [100.0]
    taps = []
    rect = (20, 1200, 248, 148)
    monkeypatch.setattr(ad_gems.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(ad_gems.time, 'sleep', lambda _: None)
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda _: None if mode == 'nonbattle' else 100)
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda _, **kwargs: None if mode == 'absent' else rect)
    monkeypatch.setattr(ad_gems.capture, 'grab', lambda: None)
    monkeypatch.setattr(ad_gems.overlays, 'foreground_blocker', lambda _: 'human' if mode == 'blocked' else None)
    def tap(*args, **kwargs):
        taps.append(now[0])
        if mode == 'refused':
            raise ad_gems.act.TapRefused('unsafe')
        return {'dry_run': mode == 'dry'}
    monkeypatch.setattr(ad_gems.act, 'tap', tap)
    collector = ad_gems.AdGemCollector(delay=(0, 0), options={
        'poll_sec': [2, 2], 'retry_cooldown_sec': [4, 4]})
    for second in range(100, 820):
        now[0] = second
        collector.poll(None)
    assert collector.next_at == 820
    assert collector.window_deadline is None
    assert collector.claim_at is None
    assert all(100 <= timestamp < 160 for timestamp in taps)
    if mode in ('unconfirmed', 'dry', 'refused'):
        assert taps
    else:
        assert taps == []
    now[0] = 820
    collector.poll(None)
    assert collector.window_deadline == 880
    assert collector.next_window_at == 1540


@pytest.mark.parametrize('io_stage', ['capture', 'windows'])
def test_window_expiring_during_fresh_io_never_taps(monkeypatch, io_stage):
    now = [100.0]
    monkeypatch.setattr(ad_gems.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda _: 100)
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda _, **kwargs: (20, 1200, 248, 148))
    def capture():
        if io_stage == 'capture':
            now[0] = 160
        return None
    def windows(*args, **kwargs):
        if io_stage == 'windows':
            now[0] = 160
        return [ad_gems.overlays.GAME_PKG+'/Activity']
    monkeypatch.setattr(ad_gems.capture, 'grab', capture)
    monkeypatch.setattr(ad_gems.overlays, 'windows', windows)
    monkeypatch.setattr(ad_gems.act, 'tap', lambda *a, **kw: pytest.fail('tap after window deadline'))
    collector = ad_gems.AdGemCollector(delay=(0, 0))
    collector.poll(None)
    assert collector.next_at == 820
    assert collector.window_deadline is None


def test_short_interval_limits_window_and_delayed_offer_cannot_escape(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(ad_gems.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda _: 100)
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda _, **kwargs: (20, 1200, 248, 148))
    monkeypatch.setattr(ad_gems.act, 'tap', lambda *a, **kw: pytest.fail('delayed offer escaped window'))
    collector = ad_gems.AdGemCollector(delay=(15, 15), options={'confirmed_cooldown_sec': [10, 10]})
    collector.poll(None)
    assert collector.window_deadline == 110
    assert collector.next_window_at == 110
    now[0] = 110
    collector.poll(None)
    assert collector.window_deadline == 120
    # the 15 s draw is clamped to leave a poll interval and a recognition budget before the deadline (item 2)
    assert collector.claim_at == 120-collector.options['poll_sec'][1]-ad_gems.OBSERVATION_TIMEOUT_SEC


def test_confirmed_claim_closes_window_and_observes_without_input_until_next_start(monkeypatch):
    now = [100.0]
    initial, fresh, after = object(), object(), object()
    frames = iter([fresh, after])
    taps = []
    monkeypatch.setattr(ad_gems.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(ad_gems.time, 'sleep', lambda _: None)
    monkeypatch.setattr(ad_gems.capture, 'grab', lambda: next(frames))
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda _: 100)
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda f, **kwargs: None if f is after else (20, 1200, 248, 148))
    monkeypatch.setattr(ad_gems.act, 'tap', lambda *a, **kw: taps.append(now[0]) or {})
    collector = ad_gems.AdGemCollector(delay=(15, 15))
    collector.poll(initial)
    now[0] = 115
    collector.poll(initial)
    assert taps == [115]
    assert collector.window_deadline is None
    assert collector.next_at == 820
    checks = []
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda f: checks.append(f) or 100)
    monkeypatch.setattr(ad_gems.capture, 'grab', lambda: pytest.fail('captured during long pause'))
    for second in (116, 159, 160, 500, 819):
        now[0] = second
        assert collector.poll(initial) is initial
    assert taps == [115]
    assert checks
    assert collector.status()['visibility'] == 'visible'


@pytest.mark.parametrize('confirmed, end_at, pause', [(True, 115, 705), (False, 160, 660)])
def test_window_events_report_schedule_and_end_reason(monkeypatch, confirmed, end_at, pause):
    now = [100.0]
    events = []
    initial, fresh, after = object(), object(), object()
    frames = iter([fresh, after])
    monkeypatch.setattr(ad_gems.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(ad_gems.time, 'sleep', lambda _: None)
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda _: 100)
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda f, **kwargs: (20, 1200, 248, 148) if confirmed and f is not after else None)
    monkeypatch.setattr(ad_gems.capture, 'grab', lambda: next(frames))
    monkeypatch.setattr(ad_gems.act, 'tap', lambda *a, **kw: {})
    monkeypatch.setattr(ad_gems.logger, 'event', lambda name, **data: events.append((name, data)))
    collector = ad_gems.AdGemCollector(delay=(15, 15))
    collector.poll(initial)
    opened = next(data for name, data in events if name == 'ad_gems_window')
    assert opened['duration_sec'] == 60
    assert opened['interval_sec'] == 720
    now[0] = end_at
    collector.poll(initial)
    ended = next(data for name, data in events if name == 'ad_gems_window_ended')
    assert ended['reason'] == ('confirmed' if confirmed else 'timeout')
    assert ended['pause_sec'] == pause
    assert ended['window_id'] == opened['window_id']
    now[0] += 1
    collector.poll(initial)
    assert sum(name == 'ad_gems_window_ended' for name, _ in events) == 1


@pytest.fixture
def observation_clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(ad_gems.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(ad_gems.time, 'time', lambda: 1000+now[0])
    monkeypatch.setattr(ad_gems.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda frame: 100)
    monkeypatch.setattr(ad_gems.capture, 'grab', lambda: pytest.fail('unexpected capture'))
    monkeypatch.setattr(ad_gems.act, 'tap', lambda *args, **kwargs: pytest.fail('unexpected tap'))
    return now


def test_passive_offer_observation_is_throttled_and_bounds_appearance(monkeypatch, observation_clock):
    import json
    now = observation_clock
    events, checked = [], []
    visible = [False]
    rect = (20, 1200, 248, 148)
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda frame, **kwargs: checked.append(now[0]) or (rect if visible[0] else None))
    monkeypatch.setattr(ad_gems.logger, 'event', lambda name, **data: events.append((name, data)))
    collector = ad_gems.AdGemCollector(options={'poll_sec': [2, 2]})
    collector.next_at = collector.next_window_at = 820
    collector.poll(None)
    for tenth in range(1001, 1020):
        now[0] = tenth/10
        collector.poll(None)
    assert checked == [100]
    visible[0] = True
    now[0] = 102
    collector.poll(None)
    status = collector.status()
    assert status['visibility'] == 'visible'
    assert status['first_seen_at'] == 1102
    assert status['last_absent_at'] == status['appearance_after_at'] == 1100
    assert status['last_observed_at'] == 1102
    assert status['claim_rect'] == rect
    assert status['response_due_at'] is None
    assert status['blocker'] == 'scheduled_pause'
    assert status['counters'] == dict(checks=2, seen=1, skips=0, attempts=0, confirmed=0, windows_missed=0)
    json.dumps(status)
    seen = next(data for name, data in events if name == 'ad_gems_offer_seen')
    assert seen['appearance_bound_sec'] == 2
    assert seen['collection_active'] is False
    now[0] = 104
    visible[0] = False
    collector.poll(None)
    lost = next(data for name, data in events if name == 'ad_gems_offer_lost')
    assert lost['offer_id'] == seen['offer_id']
    assert lost['last_seen_at'] == 1102
    assert collector.status()['visibility'] == 'absent'


def test_visibility_unknown_after_lapse_or_departure_never_means_absent(monkeypatch, observation_clock):
    now = observation_clock
    events = []
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda frame, **kwargs: (20, 1200, 248, 148))
    monkeypatch.setattr(ad_gems.logger, 'event', lambda name, **data: events.append((name, data)))
    collector = ad_gems.AdGemCollector(options={'poll_sec': [2, 2]})
    collector.next_at = collector.next_window_at = 820
    collector.poll(None)
    now[0] = 105
    assert collector.status()['visibility'] == 'unknown'
    assert collector.status()['visibility_reason'] == 'observations_lapsed'
    assert collector.status()['last_absent_at'] is None
    assert sum(name == 'ad_gems_offer_unknown' for name, _ in events) == 1
    collector.poll(None)
    assert collector.status()['first_seen_at'] == 1105
    assert collector.status()['appearance_after_at'] is None
    now[0] = 107
    monkeypatch.setattr(ad_gems.screen, 'identify', lambda frame: SimpleNamespace(name='exit_battle'))
    collector.poll(None)
    assert collector.status()['visibility'] == 'unknown'
    assert collector.status()['visibility_reason'] == 'not_battle'
    assert collector.status()['last_absent_at'] is None


def test_disabled_collector_does_not_observe_but_manual_window_can(monkeypatch, observation_clock):
    checked = []
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda frame, **kwargs: checked.append(frame) or None)
    collector = ad_gems.AdGemCollector(enabled=False)
    collector.poll(None)
    assert checked == []
    assert collector.status()['visibility_reason'] == 'disabled'
    assert collector.request_now()
    collector.poll(None)
    assert checked == [None]
    assert collector.status()['manual_active'] is True


def test_attempt_trace_separates_scheduling_response_and_verification(monkeypatch, observation_clock):
    now = observation_clock
    initial, fresh, after = object(), object(), object()
    frames = iter([fresh, after])
    events, taps = [], []
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda frame, **kwargs: None if frame is after else (20, 1200, 248, 148))
    def capture():
        now[0] += 2
        return next(frames)
    def windows(*args, **kwargs):
        now[0] += 1
        return [ad_gems.overlays.GAME_PKG+'/Activity']
    monkeypatch.setattr(ad_gems.capture, 'grab', capture)
    monkeypatch.setattr(ad_gems.overlays, 'windows', windows)
    monkeypatch.setattr(ad_gems.act, 'tap', lambda *args, **kwargs: taps.append(now[0]) or {})
    monkeypatch.setattr(ad_gems.logger, 'event', lambda name, **data: events.append((name, data)))
    collector = ad_gems.AdGemCollector(delay=(15, 15), options={'poll_sec': [2, 2]})
    collector.next_at = collector.next_window_at = 110
    for second in range(100, 126):
        now[0] = second
        collector.poll(initial)
    assert taps == [128]
    status = collector.status()
    timing = status['durations']
    assert timing['scheduling_wait_sec'] == 10
    assert timing['response_delay_sec'] == timing['response_wait_sec'] == 15
    assert timing['response_overdue_sec'] == 0
    assert timing['verification_sec'] == 5
    assert timing['verification_before_tap_sec'] == 3
    assert timing['observed_wait_sec'] == 30
    assert status['counters']['attempts'] == status['counters']['confirmed'] == 1
    traced = [data for name, data in events if name in {
        'ad_gems_attempt', 'ad_gems_tap', 'ad_gems_claim',
        'ad_gems_attempt_finished', 'ad_gems_window_ended'}]
    assert len(traced) == 5
    for key in ('window_id', 'offer_id', 'claim_attempt_id'):
        assert len({data[key] for data in traced}) == 1
        assert traced[0][key]


@pytest.mark.parametrize('mode, outcome, reason', [
    ('missing', 'skipped', 'fresh_offer_missing'),
    ('moved', 'skipped', 'offer_moved'),
    ('blocked', 'skipped', 'human_owner'),
    ('windows_error', 'skipped', 'windows_unreadable'),
    ('capture_error', 'error', 'claim_verification_error'),
    ('recognition_error', 'error', 'claim_verification_error'),
    ('refused', 'tap_refused', 'tap_refused'),
    ('dry', 'dry_run', 'dry_run'),
    ('unconfirmed', 'not_confirmed', 'offer_still_visible'),
    ('departed', 'not_confirmed', 'post_claim_not_battle'),
])
def test_each_failed_attempt_has_precise_correlated_outcome(monkeypatch, observation_clock, mode, outcome, reason):
    import json
    initial, fresh, after = object(), object(), object()
    frames = iter([fresh, after])
    taps, events = [], []
    rect = (20, 1200, 248, 148)
    def capture():
        if mode == 'capture_error':
            raise RuntimeError('capture diagnostic')
        return next(frames)
    def recognize(frame, **kwargs):
        if frame is fresh:
            if mode == 'missing':
                return None
            if mode == 'recognition_error':
                raise RuntimeError('recognition diagnostic')
            if mode == 'moved':
                return (20, 1400, 248, 148)
        return rect
    def windows(*args, **kwargs):
        if mode == 'windows_error':
            raise RuntimeError('foreground diagnostic')
        return []
    def tap(*args, **kwargs):
        taps.append(args)
        if mode == 'refused':
            raise ad_gems.act.TapRefused('tap diagnostic')
        return {'dry_run': mode == 'dry'}
    monkeypatch.setattr(ad_gems.capture, 'grab', capture)
    monkeypatch.setattr(ad_gems, 'find_claim_rect', recognize)
    monkeypatch.setattr(ad_gems.overlays, 'windows', windows)
    monkeypatch.setattr(ad_gems.overlays, 'foreground_blocker', lambda owners: 'human_owner' if mode == 'blocked' else None)
    monkeypatch.setattr(ad_gems.act, 'tap', tap)
    monkeypatch.setattr(ad_gems.screen, 'identify', lambda frame: SimpleNamespace(name='exit_battle' if frame is after and mode == 'departed' else 'battle'))
    monkeypatch.setattr(ad_gems.logger, 'event', lambda name, **data: events.append((name, data)))
    collector = ad_gems.AdGemCollector(delay=(0, 0))
    collector.poll(initial)
    status = collector.status()
    failed = status['last_failed_attempt']
    assert failed['outcome'] == outcome
    assert failed['reason'] == reason
    assert failed['confirmed'] is False
    assert status['claim_attempt_id'] is None
    assert failed['claim_attempt_id'] == status['last_outcome']['claim_attempt_id']
    starts = [data for name, data in events if name == 'ad_gems_attempt']
    ends = [data for name, data in events if name == 'ad_gems_attempt_finished']
    assert len(starts) == len(ends) == 1
    assert starts[0]['claim_attempt_id'] == ends[0]['claim_attempt_id']
    assert status['counters']['attempts'] == 1
    assert status['counters']['confirmed'] == 0
    if mode in ('capture_error', 'recognition_error', 'windows_error', 'refused'):
        assert 'diagnostic' in failed['error']
        assert 'Traceback' in failed['traceback']
    if outcome in ('error', 'skipped'):
        assert taps == []
    json.dumps(status)


def test_passive_recognition_error_has_traceback_without_input(monkeypatch, observation_clock):
    events = []
    def recognize(frame, **kwargs):
        raise RuntimeError('offer OCR unavailable')
    monkeypatch.setattr(ad_gems, 'find_claim_rect', recognize)
    monkeypatch.setattr(ad_gems.logger, 'event', lambda name, **data: events.append((name, data)))
    collector = ad_gems.AdGemCollector()
    collector.next_at = collector.next_window_at = 820
    collector.poll(None)
    error = next(data for name, data in events if name == 'ad_gems_observation_error')
    assert error['error'] == 'offer OCR unavailable'
    assert error['error_type'] == 'RuntimeError'
    assert 'Traceback' in error['traceback']
    assert collector.status()['visibility'] == 'unknown'
    assert collector.status()['counters']['attempts'] == 0


@pytest.mark.parametrize('failure', [None, 'capture', 'tap'])
def test_claim_context_reaches_nested_io_attempts_without_leaking(monkeypatch, observation_clock, failure):
    initial, fresh, after = object(), object(), object()
    frames = iter([fresh, after])
    observed = []
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda frame, **kwargs: None if frame is after else (20, 1200, 248, 148))
    def capture():
        with ad_gems.logger.attempt('test_capture') as nested:
            observed.append(('capture', nested.attempt_id, ad_gems.logger.current_context()))
            if failure == 'capture':
                raise RuntimeError('capture unavailable')
            nested.finish('completed')
            return next(frames)
    def tap(*args, **kwargs):
        with ad_gems.logger.attempt('test_tap') as nested:
            observed.append(('tap', nested.attempt_id, ad_gems.logger.current_context()))
            if failure == 'tap':
                raise RuntimeError('tap unavailable')
            nested.finish('dispatched')
            return {}
    monkeypatch.setattr(ad_gems.capture, 'grab', capture)
    monkeypatch.setattr(ad_gems.act, 'tap', tap)
    with ad_gems.logger.context(trace_id='outer-trace', attempt_id='outer-attempt'):
        baseline = ad_gems.logger.current_context()
        collector = ad_gems.AdGemCollector(delay=(0, 0))
        collector.poll(initial)
        status = collector.status()
        assert observed
        assert len(observed) == (1 if failure == 'capture' else 2 if failure == 'tap' else 3)
        for _, nested_id, context in observed:
            assert context['trace_id'] == 'outer-trace'
            for key in ('window_id', 'offer_id', 'claim_attempt_id'):
                assert context[key] == status['last_outcome'][key]
                assert context[key]
            assert context['attempt_id'] == nested_id
            assert nested_id not in ('outer-attempt', status['last_outcome']['claim_attempt_id'])
        assert ad_gems.logger.current_context() == baseline
        assert status['claim_attempt_id'] is None


@pytest.mark.parametrize('confirmed', [True, False])
def test_finished_claim_id_does_not_leak_into_passive_or_next_response_events(monkeypatch, observation_clock, confirmed):
    import json
    from control.diagnostics import query
    now = observation_clock
    initial, fresh, after = object(), object(), object()
    frames = iter([fresh, after])
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda frame, **kwargs: None if confirmed and frame is after else (20, 1200, 248, 148))
    monkeypatch.setattr(ad_gems.capture, 'grab', lambda: next(frames))
    monkeypatch.setattr(ad_gems.act, 'tap', lambda *args, **kwargs: {})
    collector = ad_gems.AdGemCollector(delay=(0, 0), options={'poll_sec': [2, 2], 'retry_cooldown_sec': [4, 4]})
    collector.poll(initial)
    completed = dict(collector.status()['last_outcome'])
    claim_id = completed['claim_attempt_id']
    assert claim_id
    assert completed['confirmed'] is confirmed
    assert collector.claim_attempt_id is None
    log_path = ad_gems.logger.run_file()
    original_rows = [json.loads(line) for line in log_path.read_text(encoding='utf-8').splitlines()]
    collector.delay = (5, 5)
    now[0] = 102
    collector.mark_unknown('waiting_for_game_state')
    collector.poll(initial)
    now[0] = 104
    collector.poll(initial)
    later_rows = [json.loads(line) for line in log_path.read_text(encoding='utf-8').splitlines()][len(original_rows):]
    assert any(row['kind'] == 'ad_gems_offer_unknown' for row in later_rows)
    assert any(row['kind'] == 'ad_gems_offer_seen' for row in later_rows)
    if not confirmed:
        assert any(row['kind'] == 'ad_gems_response_scheduled' for row in later_rows)
    assert all(row.get('claim_attempt_id') is None for row in later_rows)
    assert collector.status()['last_outcome'] == completed
    joined = query(log_path.parents[2], {'attempt_id': claim_id})['events']
    finished = next(row for row in joined if row['kind'] == 'ad_gems_attempt_finished')
    assert finished['claim_attempt_id'] == claim_id
    assert finished['offer_id'] == completed['offer_id']
    if confirmed:
        ended = next(row for row in joined if row['kind'] == 'ad_gems_window_ended')
        assert ended['claim_attempt_id'] == claim_id
        assert ended['window_id'] == finished['window_id']
    assert not any(row['kind'] in ('ad_gems_offer_unknown', 'ad_gems_offer_seen', 'ad_gems_response_scheduled') for row in joined)


@pytest.fixture
def fallback_frame(monkeypatch, tmp_path):
    import cv2
    import numpy as np
    monkeypatch.setattr(ad_gems.settings, 'template_path', lambda _: tmp_path/'missing.png')
    frame = np.zeros((2560, 1080, 3), np.uint8)
    for top in (400, 700, 1000):
        cv2.rectangle(frame, (20, top), (200, top+100), (255, 255, 0), 4)
    return frame


def test_fallback_batches_all_candidates_with_one_shared_budget(monkeypatch, observation_clock, fallback_frame):
    from vision import textocr
    now = observation_clock
    timeouts = []
    def read_texts(crops, *, scales, timeout, raise_on_error, urgent):
        assert raise_on_error is True
        assert urgent is True
        assert scales == (2,)
        assert len(crops) == 3
        timeouts.append(timeout)
        now[0] += .5
        return ['', '', 'CLAIM 6']
    monkeypatch.setattr(textocr, 'read_texts', read_texts)
    assert ad_gems.find_claim_rect(fallback_frame) == (18, 998, 185, 105)
    assert timeouts == [2]


def test_batch_never_combines_claim_and_amount_from_different_regions(monkeypatch, fallback_frame):
    from vision import textocr
    monkeypatch.setattr(textocr, 'read_texts', lambda crops, **kwargs: ['CLAIM', '6', 'BUY 6'])
    monkeypatch.setattr(ad_gems, '_six_amount', lambda patch: False)
    assert ad_gems.find_claim_rect(fallback_frame) is None


def test_complete_batch_with_only_distractors_proves_absence(monkeypatch, fallback_frame):
    from vision import textocr
    monkeypatch.setattr(textocr, 'read_texts', lambda crops, **kwargs: ['', 'Health', '187.83T /'])
    assert ad_gems.find_claim_rect(fallback_frame) is None


def test_neutral_claim_time_border_still_requires_claim_and_six(monkeypatch, tmp_path):
    import cv2
    import numpy as np
    from vision import textocr
    monkeypatch.setattr(ad_gems.settings, 'template_path', lambda _: tmp_path/'missing.png')
    frame = np.zeros((2560, 1080, 3), np.uint8)
    cv2.rectangle(frame, (20, 1200), (268, 1348), (208,208,208), 4)
    monkeypatch.setattr(textocr, 'read_texts', lambda crops, **kwargs: ['CLAIM 6']*len(crops))
    assert ad_gems.find_claim_rect(frame) == (18, 1198, 253, 153)
    monkeypatch.setattr(textocr, 'read_texts', lambda crops, **kwargs: ['Health 6']*len(crops))
    assert ad_gems.find_claim_rect(frame) is None


def test_neutral_visible_offer_after_input_does_not_confirm_reward(monkeypatch, tmp_path):
    import cv2
    import numpy as np
    from vision import textocr
    initial = np.zeros((2560, 1080, 3), np.uint8)
    after = initial.copy()
    cv2.rectangle(initial, (20, 1200), (268, 1348), (255,255,0), 4)
    cv2.rectangle(after, (20, 1200), (268, 1348), (208,208,208), 4)
    monkeypatch.setattr(ad_gems.settings, 'template_path', lambda _: tmp_path/'missing.png')
    monkeypatch.setattr(textocr, 'read_texts', lambda crops, **kwargs: ['CLAIM 6']*len(crops))
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', lambda frame: 100)
    frames = iter([initial, after])
    monkeypatch.setattr(ad_gems.capture, 'grab', lambda: next(frames))
    monkeypatch.setattr(ad_gems.time, 'sleep', lambda seconds: None)
    taps = []
    monkeypatch.setattr(ad_gems.act, 'tap', lambda *args, **kwargs: taps.append(args) or {})
    collector = ad_gems.AdGemCollector(delay=(0, 0))
    assert collector.poll(initial) is after
    assert len(taps) == 1
    assert collector.last_outcome['reason'] == 'offer_still_visible'
    assert collector.counters['confirmed'] == 0


@pytest.mark.parametrize('expired', [False, True])
def test_fallback_rejects_results_at_deadline_and_starts_no_work_after_it(monkeypatch, observation_clock, fallback_frame, expired):
    from vision import textocr
    now = observation_clock
    calls = []
    def read_texts(crops, *, timeout, **kwargs):
        calls.append(timeout)
        now[0] += timeout
        return ['CLAIM 6']*len(crops)
    monkeypatch.setattr(textocr, 'read_texts', read_texts)
    with pytest.raises(ad_gems.RecognitionIncomplete, match='recognition_deadline'):
        ad_gems.find_claim_rect(fallback_frame, deadline=100 if expired else 100.25)
    assert calls == ([] if expired else [.25])


@pytest.mark.parametrize('stage', ['initial', 'fresh', 'confirmation'])
@pytest.mark.parametrize('failure', ['timeout', 'truncated', 'engine_error', 'invalid_index'])
def test_incomplete_recognition_never_authorizes_input_or_confirms_reward(monkeypatch, observation_clock, fallback_frame, stage, failure):
    import subprocess
    from vision import textocr
    observations = {'initial': 1, 'fresh': 2, 'confirmation': 3}
    target = observations[stage]
    reads, captures, taps = [], [], []
    def read_texts(crops, **kwargs):
        reads.append(kwargs)
        if len(reads) >= target:
            if failure == 'engine_error':
                raise OSError('Windows OCR unavailable')
            if failure == 'timeout':
                raise subprocess.TimeoutExpired('test OCR', kwargs['timeout'])
            if failure == 'invalid_index':
                raise ValueError('Windows OCR returned an invalid image index')
            return []
        return ['CLAIM 6']*len(crops)
    monkeypatch.setattr(textocr, 'read_texts', read_texts)
    monkeypatch.setattr(ad_gems.capture, 'grab', lambda: captures.append(True) or fallback_frame)
    monkeypatch.setattr(ad_gems.act, 'tap', lambda *args, **kwargs: taps.append(kwargs) or {})
    collector = ad_gems.AdGemCollector(delay=(0, 0))
    collector.poll(fallback_frame)
    status = collector.status()
    assert status['visibility'] == 'unknown'
    assert status['visibility_reason'] == ('ocr_timeout' if failure == 'timeout' else 'ocr_error')
    assert status['last_absent_at'] is None
    assert status['counters']['confirmed'] == 0
    assert len(taps) == (1 if stage == 'confirmation' else 0)
    assert len(captures) == target-1
    if stage != 'initial':
        assert status['last_outcome']['reason'] == ('post_claim_recognition_incomplete'
                                                  if stage == 'confirmation' else 'fresh_recognition_incomplete')


def test_initial_recognition_uses_remaining_window_and_closes_without_capture(monkeypatch, observation_clock, fallback_frame):
    from vision import textocr
    now = observation_clock
    timeouts = []
    def read_texts(crops, *, timeout, **kwargs):
        timeouts.append(timeout)
        if now[0] > 100:
            now[0] += timeout
        return ['CLAIM 6']*len(crops)
    monkeypatch.setattr(textocr, 'read_texts', read_texts)
    collector = ad_gems.AdGemCollector(delay=(1, 1))
    collector.poll(fallback_frame)
    now[0] = 159.5
    collector.poll(fallback_frame)
    status = collector.status()
    assert timeouts == [2, .5]
    assert status['visibility'] == 'unknown'
    assert status['counters']['attempts'] == 0
    assert status['counters']['windows_missed'] == 1
    assert collector.window_deadline is None
    assert collector.next_window_at == 820


@pytest.mark.parametrize('raise_on_error', [False, True])
def test_textocr_timeout_is_opt_in_and_cleans_temporary_crop(monkeypatch, tmp_path, raise_on_error):
    import subprocess
    import numpy as np
    from vision import textocr
    mkstemp = textocr.tempfile.mkstemp
    monkeypatch.setattr(textocr.tempfile, 'mkstemp', lambda **kwargs: mkstemp(dir=tmp_path, **kwargs))
    def run_hidden(*args, **kwargs):
        raise subprocess.TimeoutExpired('test OCR', kwargs['timeout'])
    monkeypatch.setattr(textocr.settings, 'run_hidden', run_hidden)
    crop = np.zeros((10, 10, 3), np.uint8)
    if raise_on_error:
        with pytest.raises(subprocess.TimeoutExpired):
            textocr.read_lines(crop, 1, timeout=.5, raise_on_error=True)
    else:
        assert textocr.read_lines(crop, 1, timeout=.5) == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('preparation_sec', [.25, 1])
def test_strict_textocr_budget_includes_crop_preparation(monkeypatch, tmp_path, observation_clock, preparation_sec):
    import subprocess
    import numpy as np
    from vision import textocr
    now = observation_clock
    mkstemp = textocr.tempfile.mkstemp
    monkeypatch.setattr(textocr.tempfile, 'mkstemp', lambda **kwargs: mkstemp(dir=tmp_path, **kwargs))
    def write(*args):
        now[0] += preparation_sec
        return True
    calls = []
    monkeypatch.setattr(textocr.cv2, 'imwrite', write)
    monkeypatch.setattr(textocr.settings, 'run_hidden', lambda *args, **kwargs: calls.append(kwargs['timeout']) or SimpleNamespace(stdout=b'', returncode=0, stderr=b''))
    crop = np.zeros((10, 10, 3), np.uint8)
    if preparation_sec == 1:
        with pytest.raises(subprocess.TimeoutExpired):
            textocr.read_lines(crop, 1, timeout=1, raise_on_error=True)
        assert calls == []
    else:
        assert textocr.read_lines(crop, 1, timeout=1, raise_on_error=True) == []
        assert calls == [.75]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('failure', ['unavailable', 'process_error', 'nonzero', 'stderr', 'crop_write'])
@pytest.mark.parametrize('strict', [False, True])
def test_textocr_engine_failures_are_distinct_from_empty_reading_in_strict_mode(monkeypatch, tmp_path, failure, strict):
    import subprocess
    import numpy as np
    from vision import textocr
    mkstemp = textocr.tempfile.mkstemp
    monkeypatch.setattr(textocr.tempfile, 'mkstemp', lambda **kwargs: mkstemp(dir=tmp_path, **kwargs))
    if failure == 'crop_write':
        monkeypatch.setattr(textocr.cv2, 'imwrite', lambda *args: False)
    def run_hidden(*args, **kwargs):
        if failure == 'unavailable':
            raise OSError('OCR engine unavailable')
        if failure == 'process_error':
            raise subprocess.SubprocessError('OCR engine failed')
        return SimpleNamespace(returncode=1 if failure == 'nonzero' else 0,
                               stdout=b'', stderr=b'engine error' if failure == 'stderr' else b'',
                               args=['test OCR'])
    monkeypatch.setattr(textocr.settings, 'run_hidden', run_hidden)
    crop = np.zeros((10, 10, 3), np.uint8)
    if strict:
        with pytest.raises((OSError, subprocess.SubprocessError)):
            textocr.read_lines(crop, 1, timeout=1, raise_on_error=True)
    else:
        assert textocr.read_lines(crop, 1, timeout=1) == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('stage', ['wave', 'screen'])
def test_observation_prework_consumes_shared_budget_before_offer_ocr(monkeypatch, observation_clock, stage):
    now = observation_clock
    def slow_wave(frame):
        now[0] += ad_gems.OBSERVATION_TIMEOUT_SEC
        return 100
    def slow_screen(frame):
        now[0] += ad_gems.OBSERVATION_TIMEOUT_SEC
        return SimpleNamespace(name='battle')
    if stage == 'wave':
        monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', slow_wave)
        monkeypatch.setattr(ad_gems.screen, 'identify', lambda frame: pytest.fail('work after deadline'))
    else:
        monkeypatch.setattr(ad_gems.screen, 'identify', slow_screen)
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda *args, **kwargs: pytest.fail('OCR after deadline'))
    collector = ad_gems.AdGemCollector(delay=(0, 0))
    collector.poll(None)
    assert collector.status()['visibility_reason'] == 'recognition_deadline'
    assert collector.status()['counters']['attempts'] == 0
