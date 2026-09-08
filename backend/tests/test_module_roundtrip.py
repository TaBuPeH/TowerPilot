"""All slots must be recorded before any equipment change."""
import pytest
from player import module_roundtrip as rt


class Driver:
    def __init__(self, fail_scan=False, fail_restore=False, unknown=False):
        self.calls=[]
        self.removed=set()
        self.fail_scan=fail_scan
        self.fail_restore=fail_restore
        self.unknown=unknown
    def snapshot_all(self, stopped):
        self.calls.extend(('snapshot',i) for i in range(8))
        return {'version':2,'stage':'snapshot','inventory':[], 'slots':[
            {'slot':{'id':str(i)},'state':('unknown' if self.unknown and i==7 else 'equipped' if i<6 else 'locked')}
            for i in range(8)]}
    def progress(self, message): pass
    def unequip_slot(self, record):
        self.removed.add(record['slot']['id'])
        self.calls.append(('unequip',record['slot']['id']))
    def scan_inventory(self, manifest, stopped):
        self.calls.append(('scan',len(self.removed)))
        assert len(self.removed)==6
        if self.fail_scan: raise ValueError('scan failed')
    def restore_manifest(self, manifest):
        self.calls.append(('restore',len(self.removed)))
        if self.fail_restore: raise ValueError('device disconnected')
        self.removed.clear()
    def restored(self, manifest): return not self.removed


def test_complete_manifest_then_unequip_all_scan_once_restore(tmp_path):
    paths={'state':str(tmp_path/'state.json')}
    driver=Driver()
    assert rt.run(paths,driver,lambda:False)==6
    assert [c[0] for c in driver.calls]==['snapshot']*8+['unequip']*6+['scan','restore']
    assert rt.pending(paths) is None


def test_unknown_slot_blocks_every_equipment_change(tmp_path):
    paths={'state':str(tmp_path/'state.json')}
    driver=Driver(unknown=True)
    with pytest.raises(RuntimeError,match='Not every slot'):
        rt.run(paths,driver,lambda:False)
    assert all(c[0]=='snapshot' for c in driver.calls)
    assert rt.pending(paths) is None


def test_scan_failure_restores_whole_manifest(tmp_path):
    paths={'state':str(tmp_path/'state.json')}
    driver=Driver(fail_scan=True)
    with pytest.raises(ValueError,match='scan failed'): rt.run(paths,driver,lambda:False)
    assert not driver.removed and rt.pending(paths) is None


def test_stop_during_unequip_restores_already_removed_modules(tmp_path):
    paths={'state':str(tmp_path/'state.json')}
    driver=Driver()
    assert rt.run(paths,driver,lambda:bool(driver.removed))==0
    assert not driver.removed and rt.pending(paths) is None
    assert sum(c[0]=='unequip' for c in driver.calls)==1


def test_disconnection_keeps_complete_journal(tmp_path):
    paths={'state':str(tmp_path/'state.json')}
    driver=Driver(fail_restore=True)
    with pytest.raises(rt.RestoreRequired): rt.run(paths,driver,lambda:False)
    assert len(rt.pending(paths)['slots'])==8
    driver.fail_restore=False
    rt.recover(paths,driver)
    assert not driver.removed and rt.pending(paths) is None


def test_complete_journal_exists_before_first_unequip(tmp_path):
    paths={'state':str(tmp_path/'state.json')}
    driver=Driver()
    original=driver.unequip_slot
    def checked(record):
        assert len(rt.pending(paths)['slots'])==8
        original(record)
    driver.unequip_slot=checked
    rt.run(paths,driver,lambda:False)


def test_recovery_blocks_run_readiness(tmp_path):
    from player import readiness
    path=tmp_path/'logs'/'main'
    path.mkdir(parents=True)
    (path/'module_restore.json').write_text('{}')
    result=readiness.check(tmp_path,{'active_instance':'main'}, {})
    assert result['ready'] is False
    assert 'Restore' in result['missing'][0]['reasons'][0]


def test_assist_effects_keep_identity_when_efficiency_changes(monkeypatch):
    import numpy as np
    from types import SimpleNamespace
    from vision import textocr
    from interactions import inventory
    from player import calibrate
    monkeypatch.setattr(inventory,'_find_close',lambda f:(928,600))
    monkeypatch.setattr(calibrate,'resolve_module',lambda t:'example' if t=='example module' else None)
    rows=[(0,300,'ANCESTRAL'),(50,300,'Example Module'),(100,20,'Lv. 113 /240'),
          (200,300,'Effects'),(250,250,'+0.08% Enemy Health Level Skip'),(253,87,'Ancestral')]
    monkeypatch.setattr(textocr,'read_lines',lambda *a:rows)
    driver=rt.ScreenDriver(SimpleNamespace(p={}))
    equipped=driver._identity(np.zeros((2560,1080,3),np.uint8))
    rows[4]=(250,250,'+8% Enemy Health Level Skip')
    unequipped=driver._identity(np.zeros((2560,1080,3),np.uint8))
    assert equipped['effects']==unequipped['effects']
    rows[5]=(253,87,'Epic')
    assert driver._identity(np.zeros((2560,1080,3),np.uint8))['effects']!=equipped['effects']

def test_locked_notice_is_dismissed_with_ok_without_purchase(monkeypatch):
    from types import SimpleNamespace
    from device import capture,act
    from interactions import inventory
    from vision import textocr
    frames=iter(['locked','closed'])
    monkeypatch.setattr(capture,'grab',lambda:next(frames))
    monkeypatch.setattr(inventory,'_panel_open',lambda f:f=='locked')
    monkeypatch.setattr(inventory,'_find_close',lambda f:None)
    monkeypatch.setattr(textocr,'read_lines',lambda *a:[(1029,355,'SLOT LOCKED'),(1432,510,'0K')])
    monkeypatch.setattr('time.sleep',lambda s:None)
    taps=[]
    monkeypatch.setattr(act,'tap',lambda x,y,reason:taps.append((x,y,reason)))
    rt.ManifestDriver(SimpleNamespace(p={}))._close()
    assert [(x,y) for x,y,_ in taps]==[(525,1447)]
    assert 'dismiss' in taps[0][2]

def test_title_crop_excludes_equipped_only_multiplier(tmp_path, monkeypatch):
    import cv2
    import numpy as np
    from types import SimpleNamespace
    from vision import textocr
    from interactions import inventory
    from player import calibrate
    rng=np.random.default_rng(80)
    before=rng.integers(0,256,(1000,1080,3),dtype=np.uint8)
    after=before.copy()
    after[600:650,410:905]=0  # equipped-only multiplier is absent in inventory
    monkeypatch.setattr(inventory,'_find_close',lambda frame:(926,503))
    monkeypatch.setattr(textocr,'read_lines',lambda *args:[(559,429,'Example Module')])
    monkeypatch.setattr(calibrate,'resolve_module',lambda text:'example')
    driver=rt.ManifestDriver(SimpleNamespace(p={}))
    title=cv2.imread(driver._save_title(before,tmp_path))
    assert cv2.minMaxLoc(cv2.matchTemplate(after,title,cv2.TM_CCOEFF_NORMED))[1] >= .99

def test_restored_copies_are_removed_from_available_inventory():
    from types import SimpleNamespace
    ident={'slug':'example','rarity':'epic','effects':[['epic','test']]}
    item={'identity':ident,'page':0,'row':0,'col':0}
    other={'identity':{'slug':'other','rarity':'rare','effects':None},'page':0,'row':0,'col':1}
    manifest={'all_unequipped':True,'inventory_complete':True,'inventory':[item,other],
              'slots':[{'state':'equipped','identity':ident}]}
    cal=SimpleNamespace(p={},player={},save_report=lambda:None)
    rt.ManifestDriver(cal).finalize_manifest(manifest)
    assert cal.player['modules_equipped']==['example']
    assert cal.player['modules_in_grid']==['other']
    assert len(cal.player['modules_copies'])==1
    assert len(manifest['inventory'])==2

def test_indexed_icon_lookup_handles_native_offset_after_grid_reflow():
    import numpy as np
    rng=np.random.default_rng(16)
    icon=rng.integers(0,256,(150,150,3),dtype=np.uint8)
    frame=np.zeros((2560,1080,3),dtype=np.uint8)
    frame[1125:1275,260:410]=icon  # centre is shifted 6px from the column centre
    assert rt.ManifestDriver._icon_candidate(frame,329,1200,[icon])
    other=rng.integers(0,256,(150,150,3),dtype=np.uint8)
    assert not rt.ManifestDriver._icon_candidate(frame,329,1200,[other])
def test_restore_slot_choices_are_verified_without_templates(monkeypatch):
    import numpy as np
    from types import SimpleNamespace
    from player.module_roundtrip import ManifestDriver
    from device import capture, act
    from vision import textocr
    import time
    frame=np.full((2560,1080,3),255,np.uint8)
    monkeypatch.setattr(capture,'grab',lambda:frame)
    monkeypatch.setattr(textocr,'read_lines',lambda *a:[(1500,300,'Primary'),(1500,650,'Assist')])
    monkeypatch.setattr(time,'sleep',lambda _:None)
    taps=[]
    monkeypatch.setattr(act,'tap',lambda *args,**kwargs:taps.append(args))
    ManifestDriver(SimpleNamespace(p={}))._choose_role('assist')
    assert taps[0][:2] == (665,1515)


def test_slot_choice_uses_button_crops_when_full_screen_ocr_omits_labels(monkeypatch):
    import numpy as np
    from types import SimpleNamespace
    from player.module_roundtrip import ManifestDriver
    from device import capture, act
    from vision import textocr, pills
    import time
    frame=np.zeros((2560,1080,3),np.uint8)
    frame[1400:1460,280:520]=11
    frame[1400:1460,560:800]=22
    monkeypatch.setattr(capture,'grab',lambda:frame)
    def read(image,*args):
        if image.shape[0] == 2560:
            return [(1200,300,'to the primary slot or assist slot?')]
        return [(10,10,'Primary' if image[0,0,0]==11 else 'Assist')]
    monkeypatch.setattr(textocr,'read_lines',read)
    monkeypatch.setattr(pills,'pills',lambda *a:[{'rect':(280,1400,240,60)},{'rect':(560,1400,240,60)}])
    monkeypatch.setattr(time,'sleep',lambda _:None)
    taps=[]
    monkeypatch.setattr(act,'tap',lambda *args,**kwargs:taps.append(args))
    ManifestDriver(SimpleNamespace(p={}))._choose_role('primary')
    assert taps[0][:2] == (400,1430)
