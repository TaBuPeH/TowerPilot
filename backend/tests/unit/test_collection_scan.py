from types import SimpleNamespace
import json
import numpy as np

from player import collection_scan
from device import act


def test_scroll_scan_covers_pages_and_returns_top(tmp_path, monkeypatch):
    state = {'position': 2}
    moves = []
    monkeypatch.setattr(collection_scan, 'manifest', lambda: {'screens': {'list': {
        'scan_collection': {'viewport': [10, 20, 80, 100], 'max_pages': 8}}}})
    def swipe(x, y, x2, y2, duration, reason):
        assert x == x2 == 50 and 20 < y < 120 and 20 < y2 < 120
        moves.append((y, y2))
        state['position'] = max(0, min(2, state['position'] + (1 if y > y2 else -1)))
    monkeypatch.setattr(act, 'swipe', swipe)
    scanner = SimpleNamespace(
        cal=SimpleNamespace(p={'state': str(tmp_path/'state.json')}),
        state={}, skipped=[], check_stop=lambda: None, pause=lambda _: None,
        progress=lambda _: None, read=lambda f: [(5, 7, str(int(f[0, 0, 0])))],
        observed=lambda name: (np.full((140, 110, 3), state['position']*40, np.uint8), []))
    collection_scan.scan(scanner, 'list')
    report = json.loads((tmp_path/'screen_collections/list/observations.json').read_text())
    assert report['complete'] and state['position'] == 0
    assert {p['lines'][0]['text'] for p in report['pages']} == {'0', '40', '80'}
    assert report['pages'][0]['lines'][0]['x'] == 17
    assert report['pages'][0]['lines'][0]['y'] == 25
    assert len(moves) >= 10  # two unchanged observations at each endpoint
