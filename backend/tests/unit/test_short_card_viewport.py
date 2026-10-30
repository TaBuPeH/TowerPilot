import numpy as np
import pytest
from player import card_inventory as cards


def test_short_viewport_accepts_two_sibling_cards_as_overlap(monkeypatch):
    monkeypatch.setattr(cards,'manifest',lambda:{'card_inventory':{'viewport':[20,1030,1040,710]}})
    monkeypatch.setattr(cards,'tile_rects',lambda f:[(32,1400,244,301),(289,1400,244,301)])
    a=np.zeros((1920,1080,3),np.uint8); b=a.copy()
    rng=np.random.default_rng(42)
    for x in (32,289):
        tile=rng.integers(0,256,(301,244,3),dtype=np.uint8)
        a[1400:1701,x:x+244]=tile
        b[1220:1521,x:x+244]=tile
    assert cards.page_shift(a,b)==180
    b[1220:1521,289:533]=0
    with pytest.raises(RuntimeError,match='lost verified overlap'):
        cards.page_shift(a,b)


def test_swipe_shortens_when_only_complete_row_is_near_viewport_top(monkeypatch):
    monkeypatch.setattr(cards,'tile_rects',lambda f:[(32,1137,244,301),(289,1137,244,301)])
    spec={'swipe_down':[535,1550,535,1370],'viewport':[20,1030,1040,710],'tile_height':301}
    assert cards.scroll_points(None,'swipe_down',spec)==[535,1550,535,1503]
