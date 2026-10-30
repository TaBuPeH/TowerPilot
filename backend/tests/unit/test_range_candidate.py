"""A repair CANDIDATE for a saved stat image that reads as another stat (roadmap item 9, package p9f; spec 3.7, review R9-03 and the second early return of N1).

The flow never cuts a stats file that exists, so Range (stats/bounce_shot_range.png, saved from the row 'Bounce Shot Chance') had no producer. The upgrade-label capture now
reads the panel for the row of a rejected saved image and keeps the cut as EVIDENCE under calibrate_evidence/repair_candidates, never as a template. These tests drive the
real capture_upgrade_labels and _hud_digit_pass over a fake panel; the OCR, the device and the clock are the only fakes.
"""
import hashlib
import json
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

import settings
from device import act
from player import accounts, battle_capture as bc, flow_capture as fc, playerprofile
from vision import textocr

from test_upgrade_capture import _events, _panel, _setup

RANGE, CHANCE = 'bounce_shot_range', 'bounce_shot_chance'
REL = 'stats/bounce_shot_range.png'


def crop_of(label):
    """The tight label crop capture_stats cuts from a panel whose only row reads `label`."""
    return bc.capture_stats(_panel((label,)), lambda _: label, {bc.stat_slug(label)})[bc.stat_slug(label)]


def save(rel, image):
    path = settings.template_path(rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    assert cv2.imwrite(str(path), image)
    return path


class Clock:
    """A strictly increasing stand-in for flow_capture's `time` module: time() ticks by one at every call, everything else is the real module."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        self.now += 1.0
        return self.now

    def __getattr__(self, name):
        return getattr(time, name)


def templates_state(tmp_path):
    return {str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(settings.template_path('stats/x.png').parent.parent.rglob('*')) if p.is_file()}


@pytest.fixture
def scene(tmp_path, monkeypatch):
    """Saved Damage (correct) and Range (cut from 'Bounce Shot Chance'); a panel of two pages, Damage on the first, the Range row only on the second.
    OCR answers come from a queue, one list per read_texts call, in the order the code makes them: the saved-label check, then the page reads."""
    flow, _, grabs, taps, paths = _setup(tmp_path, monkeypatch, labels=('Damage', 'Bounce Shot Range'))
    paths['evidence'] = str(tmp_path / 'calibrate_evidence')
    save('stats/damage.png', crop_of('Damage'))
    save(REL, crop_of('Bounce Shot Chance'))
    pages, page, clock, grab_times = [_panel(('Damage',)), _panel(('Bounce Shot Range',))], [0], Clock(), []
    monkeypatch.setattr(fc, 'time', clock)

    def grab():
        grabs.append(True)
        grab_times.append(clock.time())            # when the device was read: the candidate's grab times must fall between these
        return pages[min(page[0], len(pages) - 1)].copy()

    flow.s.grab = grab
    monkeypatch.setattr(act, 'swipe', lambda *args, **kwargs: page.__setitem__(0, page[0] + 1))
    answers, calls = [], []

    def read(crops, **kwargs):
        calls.append(len(crops))
        assert answers, f'an unexpected OCR call over {len(crops)} crops'
        return answers.pop(0)

    monkeypatch.setattr(textocr, 'read_texts', read)
    monkeypatch.setattr(fc, 'set_tier', lambda *a, **k: pytest.fail('capture_upgrade_labels never starts a run'))
    return dict(flow=flow, grabs=grabs, taps=taps, paths=paths, answers=answers, calls=calls, page=page, tmp=tmp_path, grab_times=grab_times)


def candidate_files(scene):
    return sorted(Path(scene['paths']['evidence']).rglob('*.png'))


def entry(scene, rel=REL):
    return next(e for e in json.loads(Path(scene['paths']['report']).read_text(encoding='utf-8'))['entries'] if e['rel'] == rel)


def label_ends():
    return [e for e in _events() if e['kind'] == 'attempt_end' and e['action'] == 'setup_upgrade_labels']


def add_max_plaque(monkeypatch):
    """The account's generic stat names now include the Max plaque (stats/max_label.png), as in generic_templates.json."""
    monkeypatch.setattr(accounts, 'generic_names', lambda: {'stats/damage.png', REL, 'stats/max_label.png'})


def test_a_mislabeled_saved_stat_file_gets_a_candidate_never_a_replacement(scene):
    flow, answers = scene['flow'], scene['answers']
    before = templates_state(scene['tmp'])
    answers += [['Bounce Shot Chance', 'Damage'],        # the saved-label check: Range reads as Chance
                ['Bounce Shot Range'],                    # page 2: the row's label
                ['Bounce Shot Range']]                    # the cropped label read again
    fc.capture_upgrade_labels(flow)
    assert templates_state(scene['tmp']) == before, 'a template was written, replaced or removed'
    assert not answers and scene['page'][0] >= 1, 'the Range row is on the SECOND page: the loop must not stop after the first (review N1)'
    png, source = candidate_files(scene)[0], candidate_files(scene)[1]
    assert png.parent == Path(scene['paths']['evidence']) / 'repair_candidates' / 'stats' and not any('templates' in part for part in png.parts)
    record = entry(scene)['repair_candidate']
    assert entry(scene)['status'] == 'stale' and entry(scene)['verified'] is False
    assert record['rel'] == REL and record['sha256'] == hashlib.sha256(Path(record['path']).read_bytes()).hexdigest() and record['grabs'] == 2
    assert record['semantic_texts'] == ['Bounce Shot Range'] and record['screen'] == 'battle' and len(record['rect']) == 4 and Path(record['source_frame']).exists()
    first, second = scene['grab_times'][-2:]          # the two grabs of the page that holds the Range row; the loop returns right after the candidate
    assert first < record['grabbed_at'][0] < second < record['grabbed_at'][1] < record['at'], 'the record names the time of EACH of the two grabs it was confirmed on (spec 3.7 step 3)'
    ends = label_ends()
    assert ends[-1]['outcome'] == 'incomplete' and ends[-1]['candidates'] == [REL] and ends[-1]['invalid'][REL]['observed_slug'] == CHANCE
    assert any(e['kind'] == 'setup_repair_candidate' for e in _events())


def test_the_candidate_is_the_crop_of_the_right_label_and_matches_its_own_frame(scene):
    scene['answers'] += [['Bounce Shot Chance', 'Damage'], ['Bounce Shot Range'], ['Bounce Shot Range']]
    fc.capture_upgrade_labels(scene['flow'])
    record = entry(scene)['repair_candidate']
    crop = cv2.imread(record['path'])
    frame = cv2.imread(record['source_frame'])
    x, y, w, h = record['rect']
    assert crop.shape[:2] == (h, w) and float(cv2.matchTemplate(frame, crop, cv2.TM_CCOEFF_NORMED).max()) >= .98
    assert crop.shape == crop_of('Bounce Shot Range').shape


def test_candidate_needs_two_grabs_and_the_semantic_text(scene):
    flow, answers = scene['flow'], scene['answers']
    # the cropped label reads as another stat: no candidate, nothing kept
    answers += [['Bounce Shot Chance', 'Damage']] + [['Bounce Shot Range'], ['Bounce Shot Chance']] + [['Bounce Shot Range'], ['Bounce Shot Chance']] * 8
    fc.capture_upgrade_labels(flow)
    assert candidate_files(scene) == [] and 'repair_candidate' not in entry(scene)


def test_a_second_grab_that_does_not_show_the_label_is_no_candidate(scene, monkeypatch):
    flow, answers = scene['flow'], scene['answers']
    shown = flow.s.grab
    calls = []

    def grab():
        calls.append(1)
        frame = shown()
        # the follow-up grab of the page that holds the Range row (the second grab of that page) shows nothing
        return np.zeros_like(frame) if scene['page'][0] >= 1 and sum(1 for _ in calls) % 2 == 0 else frame

    flow.s.grab = grab
    answers += [['Bounce Shot Chance', 'Damage']] + [['Bounce Shot Range']] * 30
    fc.capture_upgrade_labels(flow)
    assert candidate_files(scene) == [] and 'repair_candidate' not in entry(scene) and entry(scene)['status'] == 'stale'


def test_no_candidate_when_the_row_is_not_visible_sends_no_extra_tap(scene):
    flow, answers = scene['flow'], scene['answers']
    scene['flow'].s.grab = lambda: scene['grabs'].append(True) or _panel(('Damage',)).copy()
    answers += [['Bounce Shot Chance', 'Damage']]
    fc.capture_upgrade_labels(flow)
    assert candidate_files(scene) == [] and 'repair_candidate' not in entry(scene)
    assert all(len(args) == 2 for args in scene['taps']) and len(scene['taps']) <= 2           # only the defense and utility tab openings; no tap for a candidate
    assert flow.s.skipped and flow.s.skipped[0]['target'] == REL
    assert settings.template_path(REL).exists() and templates_state(scene['tmp'])


def test_repair_candidate_never_writes_a_template(scene, monkeypatch):
    monkeypatch.setattr(fc._Flow, 'cut_crop', lambda *a, **k: pytest.fail('a template was cut'))
    monkeypatch.setattr(fc, 'write_template', lambda *a, **k: pytest.fail('a template was written'), raising=False)
    real_imwrite = cv2.imwrite
    monkeypatch.setattr(cv2, 'imwrite', lambda path, image, *a: pytest.fail(f'an image was written outside the evidence folder: {path}') if 'repair_candidates' not in str(path) and 'templates' in str(path) else real_imwrite(path, image, *a))
    before = templates_state(scene['tmp'])
    scene['answers'] += [['Bounce Shot Chance', 'Damage'], ['Bounce Shot Range'], ['Bounce Shot Range']]
    fc.capture_upgrade_labels(scene['flow'])
    assert templates_state(scene['tmp']) == before and 'repair_candidate' in entry(scene)


def test_without_an_evidence_folder_no_candidate_is_kept_and_nothing_else_changes(scene):
    scene['paths'].pop('evidence')
    scene['answers'] += [['Bounce Shot Chance', 'Damage']] + [['Bounce Shot Range'], ['Bounce Shot Range']] * 12
    before = templates_state(scene['tmp'])
    fc.capture_upgrade_labels(scene['flow'])
    assert templates_state(scene['tmp']) == before and 'repair_candidate' not in entry(scene)


def test_only_a_wrong_or_rejected_label_is_repairable():
    assert fc._repairable({'stats/a.png': {'status': 'mismatch'}, 'stats/b.png': {'status': 'unreadable'}, 'stats/c.png': {'status': 'recognition_unverified'}}) == ['stats/a.png', 'stats/c.png']
    assert fc._repairable({}) == []
    # the Max plaque has no candidate producer: wrong or rejected, it is not repairable (review F1)
    assert fc._repairable({'stats/max_label.png': {'status': 'mismatch'}, 'stats/d.png': {'status': 'recognition_unverified'}}) == ['stats/d.png']


def test_a_correct_saved_set_reads_no_panel(scene):
    save(REL, crop_of('Bounce Shot Range'))
    scene['answers'] += [['Bounce Shot Range', 'Damage']]
    fc.capture_upgrade_labels(scene['flow'])
    assert not scene['grabs'] and not scene['taps'] and candidate_files(scene) == []
    ends = label_ends()
    assert ends[-1]['outcome'] == 'verified_existing'


def populated_hud(scene, monkeypatch):
    """An account whose digits, UW and HUD templates are all present and that configures nothing: only the saved stat labels decide whether _hud_digit_pass starts
    its Tier-1 run. Returns the list of run starts (set_tier records the call and stops the pass)."""
    for digit in '0123456789':
        save(f'digits/{digit}.png', crop_of('Damage'))
    monkeypatch.setitem(settings.CONFIG, 'presets', {})
    monkeypatch.setitem(settings.CONFIG, 'active_profile', None)
    monkeypatch.setattr(playerprofile, 'load', lambda _: pytest.fail('no profile'))
    monkeypatch.setattr(fc, '_uw_remaining', lambda: [])
    monkeypatch.setattr(fc, '_have_templates', lambda rels: set(rels))
    started = []
    monkeypatch.setattr(fc, 'set_tier', lambda *a: started.append(a) or (_ for _ in ()).throw(RuntimeError('reached its run')))
    scene['flow'].s.state = {'phases': {}}
    return started


def test_a_hud_pass_over_an_otherwise_complete_account_starts_the_run_only_for_a_repairable_label(scene, monkeypatch):
    flow = scene['flow']
    started = populated_hud(scene, monkeypatch)
    scene['answers'] += [['Bounce Shot Chance', 'Damage']]
    with pytest.raises(RuntimeError, match='reached its run'):
        fc._hud_digit_pass(flow)
    assert len(started) == 1
    started.clear()
    save(REL, crop_of('Bounce Shot Range'))                      # the person replaced the saved image: the saved labels now read correctly
    flow.cal.entries = []
    scene['answers'] += [['Bounce Shot Range', 'Damage']]
    fc._hud_digit_pass(flow)
    assert started == []
    # an unreadable saved image is not repairable: the pass returns as it always did
    settings.template_path(REL).write_bytes(b'not an image')
    flow.cal.entries = []
    scene['answers'] += [['Damage']]
    fc._hud_digit_pass(flow)
    assert started == []


def test_a_wrong_saved_max_plaque_starts_no_run_and_reads_no_panel(scene, monkeypatch):
    """Review F1: stats/max_label.png has no candidate producer (the plaque is read only while its file is missing), so a wrong saved plaque must neither walk the
    panel in capture_upgrade_labels nor start the Tier-1 run in the digit pass."""
    add_max_plaque(monkeypatch)
    save(REL, crop_of('Bounce Shot Range'))
    save('stats/max_label.png', crop_of('Health'))                    # a saved plaque that reads as another word
    before = templates_state(scene['tmp'])
    scene['answers'] += [['Bounce Shot Range', 'Damage', 'Health']]   # the saved-label check, in sorted order: range, damage, max_label
    fc.capture_upgrade_labels(scene['flow'])
    assert not scene['answers'] and not scene['grabs'] and not scene['taps'] and scene['page'][0] == 0, 'a wrong plaque walked the panel'
    assert label_ends()[-1]['outcome'] == 'incomplete' and list(label_ends()[-1]['invalid']) == ['stats/max_label.png']
    assert entry(scene, 'stats/max_label.png')['status'] == 'stale' and 'repair_candidate' not in entry(scene, 'stats/max_label.png')
    assert candidate_files(scene) == [] and templates_state(scene['tmp']) == before
    started = populated_hud(scene, monkeypatch)                       # and the digit pass over an otherwise complete account starts no run for it
    scene['flow'].cal.entries = []
    scene['answers'] += [['Bounce Shot Range', 'Damage', 'Health']]
    fc._hud_digit_pass(scene['flow'])
    assert started == [] and not scene['answers'] and not scene['grabs'] and not scene['taps']


def test_a_wrong_max_plaque_does_not_stop_or_extend_the_walk_for_a_range_candidate(scene, monkeypatch):
    add_max_plaque(monkeypatch)
    save('stats/max_label.png', crop_of('Health'))
    scene['answers'] += [['Bounce Shot Chance', 'Damage', 'Health'], ['Bounce Shot Range'], ['Bounce Shot Range']]
    fc.capture_upgrade_labels(scene['flow'])
    assert not scene['answers'] and scene['page'][0] == 1, 'the walk ends on the page that gave the Range candidate; it does not wait for the plaque'
    assert 'repair_candidate' in entry(scene) and 'repair_candidate' not in entry(scene, 'stats/max_label.png')
    assert label_ends()[-1]['candidates'] == [REL] and set(label_ends()[-1]['invalid']) == {REL, 'stats/max_label.png'}


def test_a_rejected_image_that_now_reads_correctly_still_gets_a_candidate_and_stays_stale(scene):
    """Review F5: the recognition_unverified status end to end. The saved Range pixels read as Range, but the person's earlier rejection is on record."""
    flow = scene['flow']
    save(REL, crop_of('Bounce Shot Range'))
    flow.cal.entries = [dict(rel=REL, phase='bootstrap', status='stale', verified=False, reason='rejected before')]
    before = templates_state(scene['tmp'])
    scene['answers'] += [['Bounce Shot Range', 'Damage'], ['Bounce Shot Range'], ['Bounce Shot Range']]
    fc.capture_upgrade_labels(flow)
    assert not scene['answers'] and scene['page'][0] >= 1
    assert label_ends()[-1]['invalid'][REL]['status'] == 'recognition_unverified' and label_ends()[-1]['candidates'] == [REL]
    record = entry(scene)['repair_candidate']
    assert entry(scene)['status'] == 'stale' and entry(scene)['verified'] is False and entry(scene)['semantic_verification']['status'] == 'verified'
    assert record['semantic_texts'] == ['Bounce Shot Range'] and Path(record['path']).is_file() and Path(record['source_frame']).is_file()
    assert templates_state(scene['tmp']) == before and any(e['kind'] == 'setup_repair_candidate' for e in _events())


def candidate_failure(scene, reason_part):
    """The run finished without a candidate and said why (setup_repair_candidate_failed); the entry is as the saved-label check left it."""
    assert 'repair_candidate' not in entry(scene) and entry(scene)['status'] == 'stale' and entry(scene)['verified'] is False
    failures = [e for e in _events() if e['kind'] == 'setup_repair_candidate_failed']
    assert failures and failures[0]['rel'] == REL and reason_part in failures[0]['reason']
    assert not any(e['kind'] == 'setup_repair_candidate' for e in _events()) and label_ends()[-1]['candidates'] == []


@pytest.mark.parametrize('how', ['returns_false_after_writing', 'returns_true_but_writes_nothing'])
def test_a_source_frame_that_is_not_written_records_no_candidate(scene, monkeypatch, how):
    """Review F4: cv2.imwrite reports failure by returning False; the file must also be there when the record says so."""
    real = cv2.imwrite

    def imwrite(path, image, *args):
        if 'repair_candidates' not in str(path):
            return real(path, image, *args)
        if how == 'returns_false_after_writing':
            real(path, image, *args)
            return False
        return True

    monkeypatch.setattr(cv2, 'imwrite', imwrite)
    before = templates_state(scene['tmp'])
    scene['answers'] += [['Bounce Shot Chance', 'Damage']] + [['Bounce Shot Range']] * 40
    fc.capture_upgrade_labels(scene['flow'])
    candidate_failure(scene, 'source frame')
    assert templates_state(scene['tmp']) == before


@pytest.mark.parametrize('how', ['raises', 'truncated'])
def test_a_candidate_crop_that_cannot_be_written_in_full_records_no_candidate(scene, monkeypatch, how):
    real = Path.write_bytes

    def write_bytes(self, data):
        if 'repair_candidates' not in str(self):
            return real(self, data)
        if how == 'raises':
            raise OSError('disk full')
        return real(self, data[:10])

    monkeypatch.setattr(Path, 'write_bytes', write_bytes)
    before = templates_state(scene['tmp'])
    scene['answers'] += [['Bounce Shot Chance', 'Damage']] + [['Bounce Shot Range']] * 40
    fc.capture_upgrade_labels(scene['flow'])          # the setup run is not aborted by a candidate that cannot be kept
    candidate_failure(scene, 'disk full' if how == 'raises' else 'on disk as written')
    assert templates_state(scene['tmp']) == before
