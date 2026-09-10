from types import SimpleNamespace
import numpy as np

from player import bootstrap
from player.mapping_session import Session
from player.screen_proof import VisualAnchors
import pytest


def test_new_control_can_use_full_frame_context_after_visual_screen_proof(monkeypatch):
    frame=np.random.default_rng(6).integers(0,255,(60,120,3),dtype=np.uint8)
    calls=[]
    def read(image):
        calls.append(image)
        return [(10,10,'Bots')]
    monkeypatch.setattr(bootstrap,'anchor_present',lambda image,lines,spec: bool(lines))
    session=Session.__new__(Session)
    session.scanner=SimpleNamespace(visual_anchors=VisualAnchors(),read=read)
    session.lines=[]
    edge={'id':'bots','source':'events','route':{'verify':{'text':'Bots','rect':[5,5,70,40]}}}
    first=session.locate(frame,edge)
    session.lines=[]  # subsequent screen observation was also visual-only
    second=session.locate(frame.copy(),edge)
    assert first['identity']==second['identity']=='Bots'
    assert second['source_kind']=='visual_anchor'
    assert len(calls)==1


@pytest.mark.parametrize('screen', ['cards', 'modules'])
def test_inventory_walk_is_not_repeated_on_route_revisit(monkeypatch, screen):
    from player import calibrate
    calls=[]
    monkeypatch.setattr(calibrate, 'read_module_contents', lambda *a: calls.append('inventory') or {})
    session=Session.__new__(Session)
    session.inventories_scanned=set()
    session.scanner=SimpleNamespace(card_inventory=lambda: calls.append('inventory'),
        module_detail=lambda f: None, cal=object(), progress=lambda s: None, state={})
    session.scan_inventory_once(screen, None)
    session.scan_inventory_once(screen, None)
    assert calls == ['inventory']


def test_failed_inventory_walk_can_be_retried():
    session=Session.__new__(Session)
    session.inventories_scanned=set()
    def fail():
        raise RuntimeError('incomplete')
    session.scanner=SimpleNamespace(card_inventory=fail)
    with pytest.raises(RuntimeError):
        session.scan_inventory_once('cards', None)
    assert 'cards' not in session.inventories_scanned
