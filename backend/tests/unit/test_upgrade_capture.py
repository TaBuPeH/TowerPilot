import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

import settings
from device import act
from interactions import shopper
from player import accounts, battle_capture as bc, calibrate, flow_capture as fc, playerprofile
from runtime import logger
from vision import detect, textocr


def _panel(labels):
    panel = np.zeros((600, 1080, 3), np.uint8)
    for index, label in enumerate(labels):
        x, y = 20 + (index % 2) * 520, 80 + (index // 2) * 180
        cv2.rectangle(panel, (x, y), (x + 490, y + 160), (255, 255, 255), 2)
        cv2.putText(panel, label, (x + 20, y + 80), cv2.FONT_HERSHEY_SIMPLEX,
                    .7, (255, 255, 255), 2, cv2.LINE_AA)
    return panel


def _setup(tmp_path, monkeypatch, labels=('Damage', 'Health')):
    monkeypatch.setattr(settings, 'ROOT', tmp_path)
    monkeypatch.setitem(settings.CONFIG, 'accounts', {})
    monkeypatch.setitem(settings.CONFIG, 'instances', {'main': {}})
    monkeypatch.setitem(settings.CONFIG, 'active_instance', 'main')
    monkeypatch.setitem(settings.CONFIG, 'rois', {'upgrade_panel': [0, 0, 1080, 600]})
    monkeypatch.setattr(accounts, 'generic_names', lambda: {f'stats/{bc.stat_slug(label)}.png' for label in labels})
    panel = _panel(labels)
    paths = {'state': str(tmp_path / 'calibrate_state.json'), 'report': str(tmp_path / 'calibrate_report.json')}
    cal = calibrate.Calibration(paths, False, bootstrap=True)
    grabs, taps, current_tab = [], [], ['attack']
    def tap(*args, **kwargs):
        taps.append(args)
        current_tab[0] = kwargs['reason'].split('open ', 1)[1].split()[0]
    scanner = SimpleNamespace(cal=cal, skipped=[], check_stop=lambda: None,
                              grab=lambda: grabs.append(True) or panel.copy(), pause=lambda _: None,
                              progress=lambda *args, **kwargs: None, tap=tap)
    flow = fc._Flow(scanner)
    flow.wave = lambda _: 1
    monkeypatch.setattr(detect, 'panel_tab', lambda _: current_tab[0])
    monkeypatch.setattr(shopper, '_scroll_to_top', lambda: None)
    monkeypatch.setattr(act, 'swipe', lambda *args, **kwargs: None)
    return flow, panel, grabs, taps, paths


def _events():
    return [json.loads(line) for line in logger.run_file().read_text().splitlines()]


def test_cold_capture_persists_verified_templates_and_repeat_semantically_verifies_without_device_io(tmp_path, monkeypatch):
    flow, panel, grabs, taps, paths = _setup(tmp_path, monkeypatch)
    calls = []

    def read(crops, **kwargs):
        calls.append((len(crops), kwargs))
        return ['Damage', 'Health']

    monkeypatch.setattr(textocr, 'read_texts', read)
    fc.capture_upgrade_labels(flow)
    report = json.loads(open(paths['report'], encoding='utf-8').read())
    assert {entry['rel'] for entry in report['entries'] if entry['verified']} == {'stats/damage.png', 'stats/health.png'}
    learned = json.loads((tmp_path / 'learned_manifest.json').read_text())
    assert set(learned['targets']) == {'stats/damage.png', 'stats/health.png'}
    before = {name: settings.template_path(name).read_bytes() for name in learned['targets']}
    assert all(cv2.imread(str(settings.template_path(name))) is not None for name in before)
    assert grabs and not taps
    grabs.clear()
    fc.capture_upgrade_labels(flow)
    assert not grabs and not taps
    assert before == {name: settings.template_path(name).read_bytes() for name in before}
    assert calls == [(2, {'scales': (2.0, 3.0), 'raise_on_error': True})] * 2
    ends = [event for event in _events() if event['kind'] == 'attempt_end' and event['action'] == 'setup_upgrade_labels']
    assert [event['outcome'] for event in ends] == ['complete', 'verified_existing']
    starts = [event for event in _events() if event['kind'] == 'attempt_start' and event['action'] == 'setup_upgrade_labels']
    assert [event['attempt_id'] for event in starts] == [event['attempt_id'] for event in ends]


def test_saved_wrong_label_stays_stale_until_fresh_visual_verification(tmp_path, monkeypatch):
    flow, panel, grabs, taps, _ = _setup(tmp_path, monkeypatch, labels=('Bounce Shot Range',))
    reasons, swipes, send_tap = [], [], flow.s.tap
    flow.s.tap = lambda *args, **kwargs: reasons.append(kwargs['reason']) or send_tap(*args, **kwargs)
    monkeypatch.setattr(act, 'swipe', lambda *args, **kwargs: swipes.append(kwargs['reason']))

    def tab_opens(*names):
        """The only taps the page loop makes: the tab openings (the tab already open needs none), with their reasons."""
        return ([tuple(int(v) for v in settings.CONFIG['tabs'][name]) for name in names], [f'flow: open {name} upgrades for capture' for name in names])

    path = settings.template_path('stats/bounce_shot_range.png')
    path.parent.mkdir(parents=True)
    assert cv2.imwrite(str(path), _panel(('Bounce Shot Chance',)))
    before = path.read_bytes()
    monkeypatch.setattr(textocr, 'read_texts', lambda crops, **kwargs: ['Bounce Shot Chance'])
    fc.capture_upgrade_labels(flow)
    assert grabs, 'a saved label that reads as another stat has the panel read for a repair candidate (item 9 p9f)'
    # The attack tab is open already: the reading of the panel sends the defense and utility tab taps and the loop's own scroll swipes, and NOTHING else.
    assert (taps, reasons) == tab_opens('defense', 'utility')
    assert swipes and set(swipes) == {'setup overlapping upgrade scroll'}
    assert path.read_bytes() == before
    assert flow.s.skipped[0]['target'] == 'stats/bounce_shot_range.png'
    end = [event for event in _events() if event['kind'] == 'attempt_end'][-1]
    assert end['outcome'] == 'incomplete'
    assert end['invalid']['stats/bounce_shot_range.png']['observed_slug'] == 'bounce_shot_chance'
    monkeypatch.setattr(textocr, 'read_texts', lambda crops, **kwargs: ['Bounce Shot Range'])
    taps.clear(), reasons.clear(), swipes.clear()
    fc.capture_upgrade_labels(flow)
    assert (taps, reasons) == tab_opens('attack', 'defense', 'utility')        # the utility tab was left open by the first pass
    assert swipes and set(swipes) == {'setup overlapping upgrade scroll'}
    # A changed OCR answer alone is no proof that the same rejected pixels
    # now recognize the right control. Keep the rejection until recaptured.
    assert flow.s.skipped[0]['target'] == 'stats/bounce_shot_range.png'
    entry = json.loads(open(flow.cal.p['report'], encoding='utf-8').read())['entries'][0]
    assert entry['status'] == 'stale' and not entry['verified']
    assert entry['semantic_verification']['status'] == 'verified'
    assert path.read_bytes() == before


def test_rejected_saved_label_cannot_bypass_fresh_row_ocr(tmp_path, monkeypatch):
    flow, _, grabs, taps, _ = _setup(tmp_path, monkeypatch, labels=('Bounce Shot Range',))
    chance_panel = _panel(('Bounce Shot Chance',))
    chance_crop = bc.capture_stats(chance_panel, lambda _: 'Bounce Shot Chance',
                                   {'bounce_shot_chance'})['bounce_shot_chance']
    path = settings.template_path('stats/bounce_shot_range.png')
    path.parent.mkdir(parents=True)
    assert cv2.imwrite(str(path), chance_crop)
    before = path.read_bytes()
    monkeypatch.setattr(textocr, 'read_texts', lambda crops, **kw: ['Bounce Shot Chance'])
    fc.verify_saved_upgrade_labels(flow)
    monkeypatch.setattr(textocr, 'read_texts', lambda crops, **kw: ['Bounce Shot Range'])

    known, invalid = fc.verify_saved_upgrade_labels(flow)
    calls = []
    def read_fresh(crops):
        calls.append(len(crops))
        return ['Bounce Shot Chance']
    cuts = bc.capture_stats(chance_panel, None, {'bounce_shot_range', 'bounce_shot_chance'},
                            read_many=read_fresh, known=known)

    assert 'stats/bounce_shot_range.png' in invalid
    assert 'bounce_shot_range' not in known
    assert calls == [1] and set(cuts) == {'bounce_shot_chance'}
    assert path.read_bytes() == before and not grabs and not taps


def test_semantic_rejection_invalidates_saved_recognition_without_replacing_pixels(tmp_path, monkeypatch):
    import hashlib
    from player import readiness, scan_plan

    rel = 'stats/bounce_shot_range.png'
    flow, panel, grabs, taps, _ = _setup(tmp_path, monkeypatch, labels=('Bounce Shot Range',))
    path = settings.template_path(rel)
    path.parent.mkdir(parents=True)
    assert cv2.imwrite(str(path), panel)
    before = path.read_bytes()
    digest = hashlib.sha256(before).hexdigest()
    flow.cal.entries = [{'rel': rel, 'phase': 'bootstrap', 'status': 'exists',
                         'verified': True, 'image_sha256': digest, 'rect': [1, 2, 3, 4]}]
    monkeypatch.setattr(textocr, 'read_texts', lambda crops, **kw: ['Bounce Shot Chance'])
    # Use the ordinary account report location consumed by the dashboard/readiness.
    report_path = accounts.calibration_dir(tmp_path, settings.CONFIG) / 'calibrate_report.json'
    flow.cal.p['report'] = str(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    fc.verify_saved_upgrade_labels(flow)

    entry = json.loads(report_path.read_text())['entries'][0]
    assert entry['status'] == 'stale' and entry['verified'] is False
    assert entry['rect'] == [1, 2, 3, 4]
    assert entry['image_sha256'] == digest
    assert entry['semantic_verification']['observed_slug'] == 'bounce_shot_chance'
    assert 'Bounce Shot Chance' in entry['reason']
    plan = scan_plan.plan(tmp_path, settings.CONFIG)
    row = next(r for step in plan['steps'] for r in step['targets'] if r['rel'] == rel)
    assert 'Needs attention' in row['status'] and 'Bounce Shot Chance' in row['status']
    missing = readiness.check(tmp_path, settings.CONFIG,
                              {'shopping': [{'stats': ['bounce_shot_range']}]})['missing']
    assert any(r['alternatives'] == [rel] for r in missing)
    assert path.read_bytes() == before and not grabs and not taps


def test_cold_capture_requires_independent_follow_frame(tmp_path, monkeypatch):
    flow, panel, grabs, taps, _ = _setup(tmp_path, monkeypatch)
    follow = False

    def read(crops, **kwargs):
        nonlocal follow
        follow = True
        return ['Damage', 'Health']

    monkeypatch.setattr(textocr, 'read_texts', read)

    def grab():
        nonlocal follow
        if follow:
            follow = False
            return np.zeros_like(panel)
        return panel.copy()

    flow.s.grab = grab
    fc.capture_upgrade_labels(flow)
    assert not settings.template_path('stats/damage.png').exists()
    assert not settings.template_path('stats/health.png').exists()
    ends = [event for event in _events() if event['kind'] == 'attempt_end' and event['action'] == 'setup_upgrade_labels']
    assert ends[-1]['outcome'] == 'incomplete'


def test_known_whole_label_matching_never_consumes_longer_label_or_false_fuzzy_neighbor():
    panel = _panel(('Damage', 'Damage / Meter', 'Bounce Shot Chance'))
    first = bc.capture_stats(_panel(('Damage',)), lambda _: 'Damage', {'damage'})['damage']
    calls = []

    def read(crops):
        calls.append(len(crops))
        return ['Damage / Meter', 'Bounce Shot Chance']

    cuts = bc.capture_stats(panel, None, {'damage', 'damage_per_meter', 'bounce_shot_range'},
                            known={'damage': first}, read_many=read)
    assert calls == [2]
    assert set(cuts) == {'damage', 'damage_per_meter'}


def test_semantic_checker_distinguishes_blank_missing_and_wrong_name():
    image = _panel(('Example',))
    got = bc.verify_stat_labels({'damage': image, 'max_label': image, 'health': None,
                                  'death_defy': np.zeros((20, 30, 3), np.uint8)},
                                 lambda crops: ['Damage', 'Max'])
    assert {key: value['status'] for key, value in got.items()} == {
        'damage': 'verified', 'max_label': 'verified', 'health': 'unreadable', 'death_defy': 'unreadable'}


def test_unreadable_saved_template_is_explicitly_incomplete_without_overwrite_or_ocr(tmp_path, monkeypatch):
    flow, _, grabs, taps, _ = _setup(tmp_path, monkeypatch, labels=('Damage',))
    path = settings.template_path('stats/damage.png')
    path.parent.mkdir(parents=True)
    path.write_bytes(b'not an image')
    monkeypatch.setattr(textocr, 'read_texts', lambda *args, **kwargs: pytest.fail('Unreadable image sent to OCR'))
    fc.capture_upgrade_labels(flow)
    assert path.read_bytes() == b'not an image'
    assert not grabs and not taps
    assert flow.s.skipped[0]['target'] == 'stats/damage.png'
    end = [event for event in _events() if event['kind'] == 'attempt_end'][-1]
    assert end['outcome'] == 'incomplete'
    assert end['invalid']['stats/damage.png']['status'] == 'unreadable'


def test_ocr_error_retains_error_terminal_outcome_and_never_persists_templates(tmp_path, monkeypatch):
    flow, _, _, _, _ = _setup(tmp_path, monkeypatch)
    def read(*args, **kwargs):
        raise OSError('OCR unavailable')
    monkeypatch.setattr(textocr, 'read_texts', read)
    with pytest.raises(OSError, match='OCR unavailable'):
        fc.capture_upgrade_labels(flow)
    assert not settings.template_path('stats/damage.png').exists()
    ends = [event for event in _events() if event['kind'] == 'attempt_end'
            and event['action'] != 'setup_upgrade_verification']
    assert [(event['action'], event['outcome']) for event in ends] == [
        ('setup_upgrade_page', 'error'), ('setup_upgrade_labels', 'error')]


@pytest.mark.parametrize('reading, outcome', [('Bounce Shot Range', 'verified_existing'),
                                               ('Bounce Shot Chance', 'incomplete')])
def test_populated_hud_pass_verifies_saved_labels_before_return_without_device_io(tmp_path, monkeypatch, reading, outcome):
    flow, panel, grabs, taps, _ = _setup(tmp_path, monkeypatch, labels=('Bounce Shot Range',))
    path = settings.template_path('stats/bounce_shot_range.png')
    path.parent.mkdir(parents=True)
    assert cv2.imwrite(str(path), panel)
    for digit in '0123456789':
        digit_path = settings.template_path(f'digits/{digit}.png')
        digit_path.parent.mkdir(parents=True, exist_ok=True)
        digit_path.write_bytes(b'existing digit fixture')
    monkeypatch.setitem(settings.CONFIG, 'presets', {})
    monkeypatch.setitem(settings.CONFIG, 'active_profile', None)
    monkeypatch.setattr(playerprofile, 'load', lambda _: pytest.fail('No configured profile should be read'))
    monkeypatch.setattr(fc, '_uw_remaining', lambda: [])
    monkeypatch.setattr(fc, '_have_templates', lambda rels: set(rels))
    reached = []

    def set_tier(*args):
        reached.append(args)
        if outcome == 'verified_existing':
            pytest.fail('Populated setup started a run')
        raise RuntimeError('the pass reached its Tier-1 run')       # a saved label that reads as another stat starts the run for a repair candidate (item 9 p9f)

    monkeypatch.setattr(fc, 'set_tier', set_tier)
    calls = []
    def read(crops, **kwargs):
        calls.append((len(crops), kwargs))
        return [reading]
    monkeypatch.setattr(textocr, 'read_texts', read)
    flow.s.skipped.append({'target': 'stats/bounce_shot_range.png', 'reason': 'previous label mismatch'})
    before = path.read_bytes()

    if outcome == 'incomplete':
        flow.s.state = {'phases': {}}
        with pytest.raises(RuntimeError, match='reached its Tier-1 run'):
            fc._hud_digit_pass(flow)
        assert reached == [(flow, 1)]
    else:
        fc._hud_digit_pass(flow)
        assert not reached

    assert not grabs and not taps
    assert path.read_bytes() == before
    assert calls == [(1, {'scales': (2.0, 3.0), 'raise_on_error': True})]
    assert bool(flow.s.skipped) == (outcome == 'incomplete')
    events = _events()
    start = next(event for event in events if event['kind'] == 'attempt_start')
    end = next(event for event in events if event['kind'] == 'attempt_end')
    assert start['action'] == end['action'] == 'setup_upgrade_verification'
    assert start['attempt_id'] == end['attempt_id']
    assert end['outcome'] == outcome
