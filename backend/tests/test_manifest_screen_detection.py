import numpy as np
from vision import screen, detect
from player.bootstrap_layout import manifest


def test_runtime_accepts_the_native_header_size_written_by_setup(monkeypatch):
    spec=next(t for t in manifest()['screens']['cards']['targets'] if t['rel']=='screens/hdr_cards.png')
    x,y,w,h=spec['rect']
    assert h > screen.HEADER_BAND[1]-screen.HEADER_BAND[0]
    template=np.random.default_rng(9).integers(0,255,(h,w,3),dtype=np.uint8)
    frame=np.zeros((2560,1080,3),np.uint8)
    frame[y:y+h,x:x+w]=template
    def lookup(rel):
        if rel==spec['rel']:
            return template
        raise detect.TemplateMissing(rel)
    monkeypatch.setattr(detect,'_tpl',lookup)
    assert screen.identify(frame).name=='cards'
