"""Synthetic setup scans preserve coverage while avoiding unconditional waits."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from device import act
from player import card_inventory, collection_scan
from runtime import logger


def _card_scan(tmp_path, monkeypatch, *, moving=False, check_stop=lambda: None, trace=None):
    frame = np.random.default_rng(17).integers(0, 80, (2560, 1080, 3), dtype=np.uint8)
    x, y, w, h = 32, 1100, 244, 301
    waits, moves = [], []
    if trace is not None:
        trace.update(waits=waits, moves=moves)
    monkeypatch.setattr(card_inventory, 'tile_rects', lambda f: [(x, y, w, h)])
    monkeypatch.setattr(card_inventory, 'page_shift', lambda a, b: 0)
    grabs = 0
    def grab():
        nonlocal grabs
        grabs += 1
        if moving and moves:
            return np.random.default_rng(grabs).integers(0, 80, frame.shape, dtype=np.uint8)
        return frame.copy()
    cal = SimpleNamespace(p={'report': str(tmp_path/'report.json')}, player={},
        cut=lambda *a: {'verified': True}, save_report=lambda: None)
    result = card_inventory.scan(cal, grab=grab, swipe=lambda *a, **k: moves.append(a),
        read=lambda f: [(0, 0, 'Damage')], prove=lambda f: True,
        pause=waits.append, check_stop=check_stop)
    return result, waits, moves, grabs


def _assert_attempts(operation_id, action, outcome, count):
    events = [json.loads(line) for line in logger.run_file().read_text().splitlines()]
    rows = [row for row in events if row.get('operation_id') == operation_id and row.get('action') == action]
    starts = {row['attempt_id']: row for row in rows if row['kind'] == 'attempt_start'}
    ends = {row['attempt_id']: row for row in rows if row['kind'] == 'attempt_end'}
    assert len(starts) == len(ends) == count
    assert starts.keys() == ends.keys()
    assert all(row['outcome'] == outcome and row['duration_ms'] >= 0 for row in ends.values())
    return list(ends.values())


def test_card_readiness_keeps_independent_frames_and_persisted_completion(tmp_path, monkeypatch):
    with logger.context(operation_id='synthetic-card-readiness'):
        result, waits, moves, grabs = _card_scan(tmp_path, monkeypatch)
    stored = json.loads((tmp_path/'card_manifest.json').read_text())
    assert result['complete'] and stored == result
    assert [row['name'] for row in stored['cards']] == ['Damage']
    assert len(moves) == 4  # two unchanged scrolls at each independently proved boundary
    assert grabs >= len(moves)*4  # pre-input proof and three fresh settle frames
    measured = {'action': 'cards', 'moves': len(moves), 'pause_s': round(sum(waits), 3), 'grabs': grabs}
    print(json.dumps(measured))
    assert sum(waits) == pytest.approx(len(moves)*.6)
    phase = _assert_attempts('synthetic-card-readiness', 'setup.cards', 'verified', 1)[0]
    assert phase['cards'] == 1 and phase['complete']
    pages = _assert_attempts('synthetic-card-readiness', 'setup.cards.page', 'observed', 2)
    assert {row['page'] for row in pages} == {1, 2}
    assert all(row['parent_span_id'] == phase['span_id'] for row in pages)
    _assert_attempts('synthetic-card-readiness', 'setup.cards.scroll', 'settled', 4)


def test_collection_readiness_keeps_persisted_coverage_and_return_top(tmp_path, monkeypatch):
    state = {'position': 1}
    moves, waits = [], []
    monkeypatch.setattr(collection_scan, 'manifest', lambda: {'screens': {'list': {
        'scan_collection': {'viewport': [10, 20, 80, 100], 'max_pages': 8}}}})
    def swipe(x, y, x2, y2, duration, reason):
        moves.append((y, y2))
        state['position'] = max(0, min(1, state['position'] + (1 if y > y2 else -1)))
    monkeypatch.setattr(act, 'swipe', swipe)
    scanner = SimpleNamespace(cal=SimpleNamespace(p={'state': str(tmp_path/'state.json')}),
        state={}, skipped=[], check_stop=lambda: None, pause=waits.append,
        progress=lambda _: None, read=lambda f: [(5, 7, str(int(f[0, 0, 0])))],
        observed=lambda name: (np.full((140, 110, 3), state['position']*40, np.uint8), []))
    with logger.context(operation_id='synthetic-collection-readiness'):
        collection_scan.scan(scanner, 'list')
    stored = json.loads((tmp_path/'screen_collections/list/observations.json').read_text())
    assert stored['complete'] and state['position'] == 0
    assert {page['lines'][0]['text'] for page in stored['pages']} == {'0', '40'}
    print(json.dumps({'action': 'collection', 'moves': len(moves), 'pause_s': round(sum(waits), 3)}))
    assert sum(waits) == pytest.approx((len(moves)+1)*.3)
    _assert_attempts('synthetic-collection-readiness', 'setup.collection', 'verified', 1)
    _assert_attempts('synthetic-collection-readiness', 'setup.collection.page', 'observed', 3)


def test_unsettled_cards_are_bounded_without_more_input(tmp_path, monkeypatch):
    trace={}
    with logger.context(operation_id='synthetic-card-unsettled'):
        with pytest.raises(RuntimeError, match='still moving'):
            _card_scan(tmp_path, monkeypatch, moving=True, trace=trace)
    assert not json.loads((tmp_path/'card_manifest.json').read_text())['complete']
    assert len(trace['moves'])==1 and len(trace['waits'])==15
    _assert_attempts('synthetic-card-unsettled', 'setup.cards', 'error', 1)
    _assert_attempts('synthetic-card-unsettled', 'setup.cards.scroll', 'error', 1)


def test_stop_after_card_swipe_prevents_following_input_and_completion(tmp_path, monkeypatch):
    from player.calibrate import Stopped
    trace={}
    def stop():
        if trace.get('moves'):
            raise Stopped('requested')
    with pytest.raises(Stopped):
        _card_scan(tmp_path, monkeypatch, check_stop=stop, trace=trace)
    assert len(trace['moves'])==1 and not trace['waits']
    assert not json.loads((tmp_path/'card_manifest.json').read_text())['complete']


@pytest.mark.parametrize('close_outcome',['grid','dropped','unknown','changed'])
def test_module_opening_observes_animation_and_confirms_descriptor_twice(monkeypatch,close_outcome):
    from interactions import inventory
    from player import bootstrap, module_descriptor
    from vision import pills, textocr
    grid=np.random.default_rng(18).integers(0, 200, (2560,1080,3),dtype=np.uint8)
    grid[0,0]=0
    frames=[]
    for marker in (1,2,3,4):
        frame=grid.copy()
        frame[0,0]=marker
        frames.append(frame)
    if close_outcome=='dropped':
        frames.append(frames[-1].copy())
    if close_outcome in ('unknown','changed'):
        unknown=grid.copy()
        unknown[0,0]=5 if close_outcome=='unknown' else 6
        frames.append(unknown)
    else:
        frames.append(grid.copy())
    waits, taps, cuts, readings = [], [], [], []
    monkeypatch.setattr(pills,'grid_rows',lambda f: [1100])
    monkeypatch.setattr(inventory,'_find_close',lambda f: (927,878) if f[0,0,0] else None)
    def locate(icon,frame):
        marker=int(frame[0,0,0])
        if marker==1:
            raise RuntimeError('artwork still opening')
        if marker==5:
            raise RuntimeError('description no longer owned')
        readings.append(marker)
        return [200,900,150,150]
    monkeypatch.setattr(module_descriptor,'locate_icon',locate)
    monkeypatch.setattr(textocr,'read_lines',lambda *a: [(0,0,'Epic'),
        (20,0,'Black Hole Digestor' if readings[-1]==6 else 'Death Penalty')])
    scanner=bootstrap.Scanner.__new__(bootstrap.Scanner)
    scanner.cal=SimpleNamespace(cut=lambda *a: cuts.append(a))
    scanner.skipped=[]
    scanner.asset_folder=None
    scanner.check_stop=lambda: None
    scanner.progress=lambda s: None
    scanner.observed=lambda name: (grid.copy(),[])
    scanner.tap=lambda *a,**k: taps.append(a)
    scanner.grab=lambda: frames.pop(0)
    scanner.pause=waits.append
    scanner.read=lambda f: []
    scanner.cut=lambda *a,**k: None
    monkeypatch.setattr(bootstrap,'screen_matches',lambda f,lines,name: not f[0,0,0])
    with logger.context(operation_id='synthetic-module-readiness'):
        if close_outcome in ('unknown','changed'):
            message='description no longer owned' if close_outcome=='unknown' else 'description changed after closing'
            with pytest.raises(RuntimeError,match=message):
                scanner.module_detail(grid)
        else:
            scanner.module_detail(grid)
    assert len(cuts)==1 and cuts[0][1]=='modules/death_penalty.png'
    assert len(taps)==(3 if close_outcome=='dropped' else 2)
    assert readings==[2,3,3,4]+([4] if close_outcome=='dropped' else [6] if close_outcome=='changed' else [])
    assert waits==[.25,.25,.3,.6]+([.6] if close_outcome=='dropped' else [])
    assert not frames
    if close_outcome in ('unknown','changed'):
        _assert_attempts('synthetic-module-readiness','setup.module_detail','error',1)
    else:
        observed=_assert_attempts('synthetic-module-readiness','setup.module_detail','observed',1)[0]
        assert observed['descriptor_verified'] and observed['modules_grid_returned']
