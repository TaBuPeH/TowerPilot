import numpy as np
from player.bootstrap_layout import reference_manifest
from player.geometry import Display, scale_manifest
from interactions import inventory


def test_short_module_profile_keeps_header_and_clips_inventory():
    m = scale_manifest(reference_manifest(), Display(1080, 1920, 280))
    assert m['module_slots']['large'][0] == [307, 518]
    assert m['module_inventory']['grid_clear'] == [1080, 1628]
    assert m['module_inventory']['page_drag'] == [1510, 1360]
    assert m['module_inventory']['columns'] == [146, 346, 546, 746, 946]
    assert all(1080 < y < 1628 for y in m['module_inventory']['page_drag'])


def test_missing_module_close_never_falls_back_to_a_blind_tap(monkeypatch):
    monkeypatch.setattr(inventory.capture, 'grab', lambda: np.zeros((200,200,3), np.uint8))
    monkeypatch.setattr(inventory, '_panel_open', lambda *a: True)
    monkeypatch.setattr(inventory, '_find_close', lambda f: None)
    monkeypatch.setattr(inventory.act, 'tap', lambda *a,**k: (_ for _ in ()).throw(AssertionError('blind tap')))
    assert inventory._close_panel() is False
