import cv2
import numpy as np
from player import screen_analysis as sa


def test_classifier_rejects_unknown_and_prefers_specific_child(monkeypatch):
    monkeypatch.setattr(sa, 'screen_matches', lambda f,l,s: s in ('events','bots'))
    assert sa.classify(None, [])['name'] == 'bots'
    monkeypatch.setattr(sa, 'screen_matches', lambda *a: False)
    assert sa.classify(None, []) == {'name':'unknown', 'candidates':[]}


def test_catalogue_aliases_deduplicate_and_unknown_roles_stay_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(sa, 'classify', lambda *a: {'name':'unknown','candidates':[]})
    rng = np.random.default_rng(91)
    source = rng.integers(0,256,(140,140,3),dtype=np.uint8)
    frame = np.zeros((400,400,3),np.uint8)
    frame[110:250,120:260] = source
    cv2.imwrite(str(tmp_path/'source.png'),source)
    records = [{'sha256':'same','file':'source.png','name':n} for n in ('first','alias')]
    result = sa.analyze(frame, [], tmp_path, {'images':records})
    assert result['tested'] == 1
    assert len(result['matches']) == 1
    hit = result['matches'][0]
    assert hit['names'] == ['alias','first']
    assert hit['roles'] == [] and not hit['ambiguous']
    assert abs(hit['rect'][0]-120) <= 1
    blank = sa.analyze(np.zeros_like(frame), [], tmp_path, {'images':records})
    assert blank['matches'] == []
