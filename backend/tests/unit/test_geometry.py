"""Resolution transforms and runtime observations must never become blind taps."""
from copy import deepcopy
import pytest

from player.bootstrap_layout import manifest
from player.geometry import Display, Resolver, scale_manifest


def test_full_manifest_scales_axes_without_scaling_art_or_behavior():
    source = manifest()
    original = deepcopy(source)
    scaled = manifest(Display(540, 1920, 240))
    assert scaled['navigation']['battle'] == [42, 1852]
    assert scaled['tab_bands']['cards'] == [248, 420]
    assert scaled['card_inventory']['columns'] == [16, 144, 273, 402]
    assert scaled['module_descriptor']['icon_search_from_close']['dy'] == -30
    assert scaled['asset_bindings']['icons/tile_events.png']['source_crop'] == source['asset_bindings']['icons/tile_events.png']['source_crop']
    assert scaled['module_descriptor']['minimum_inlier_fraction'] == .3
    assert scaled['card_inventory']['max_pages'] == source['card_inventory']['max_pages']
    assert source == original == manifest()
    assert scaled == manifest(Display(540, 1920, 240))
    with pytest.raises(ValueError, match='runtime copy'):
        scale_manifest(scaled, Display(270, 960, 120))


def resolver(tmp_path, display=Display(540, 1280, 180), context='account/emulator', source=None):
    return Resolver(source or manifest(), display, {'state': str(tmp_path/'state.json')}, context)


def test_persisted_bindings_require_same_display_account_and_revision(tmp_path):
    r = resolver(tmp_path)
    r.record('home.cards', 'home', [20, 1200, 70, 50], source='artwork',
             confidence=.98, verified_frames=2, stable=True)
    assert resolver(tmp_path).measured('home.cards', 'home')['rect'] == [20, 1200, 70, 50]
    assert r.measured('home.cards', 'battle') is None
    assert resolver(tmp_path, context='different account').measured('home.cards', 'home') is None
    for display in [Display(540,1280,200), Display(1080,2560,180)]:
        assert resolver(tmp_path, display).measured('home.cards', 'home') is None
    source = manifest(); source['version'] += 1
    assert resolver(tmp_path, source=source).measured('home.cards', 'home') is None
    with pytest.raises(ValueError, match='not verified'):
        r.tap('home.cards', 'home', lambda row: False)
    assert r.tap('home.cards', 'home', lambda row: True) == (55,1225)


def test_scrolled_and_temporary_controls_are_frame_local(tmp_path):
    r = resolver(tmp_path)
    r.begin_frame('before scroll')
    r.record('quest.claim', 'missions', [25,500,200,45], source='quest rectangle',
             confidence=.98, verified_frames=2, anchor={'quest':'login'})
    assert r.measured('quest.claim','missions') is not None
    assert resolver(tmp_path).measured('quest.claim','missions') is None
    r.begin_frame('after scroll')
    assert r.measured('quest.claim','missions') is None
    assert r.search('quest.claim','missions','/reward_collection/event_missions/viewport') == [5,185,530,1005]
    with pytest.raises(ValueError): r.tap('quest.claim','missions',lambda row: True)


def test_invalid_measurements_are_rejected(tmp_path):
    r = resolver(tmp_path)
    for rect in [[-1,0,10,10],[0,0,541,10],[0,0,10,1281],[1.2,0,10,10]]:
        with pytest.raises(ValueError):
            r.record('x','home',rect,source='art',confidence=.99,verified_frames=2,stable=True)
    with pytest.raises(ValueError):
        r.record('x','home',[0,0,10,10],source='art',confidence=.99,verified_frames=1,stable=True)


def test_driver_resolves_confirmed_control_before_tapping(tmp_path):
    from player.manifest_driver import Driver
    import numpy as np
    r=resolver(tmp_path)
    observations=iter([(np.zeros((1280,540,3),'uint8'),'home'),
                       (np.zeros((1280,540,3),'uint8'),'home'),
                       (np.zeros((1280,540,3),'uint8'),'cards')])
    taps=[]
    d=Driver(observe=lambda:next(observations),
        locate=lambda frame,edge:dict(verified=True,rect=[200,1200,60,50],identity='cards'),
        tap=lambda *p:taps.append(p),collect=lambda *a:None,
        publish=lambda e:None,check_stop=lambda:None,geometry=r)
    assert d.step(dict(id='cards',source='home',destination='cards',kind='navigate',availability='always'))
    assert taps==[(230,1225)]
    assert r.measured('cards','home') is None  # destination observation expired source geometry


def test_every_numeric_vector_has_explicit_units_or_source_art_exclusion():
    source=manifest()
    def walk(value,path=''):
        if isinstance(value,dict):
            for key,child in value.items():
                if key!='geometry_fields':
                    yield from walk(child,path+'/'+key.replace('~','~0').replace('/','~1'))
        elif isinstance(value,list):
            if value and all(type(v) in (float,int) for v in value):
                yield path
            else:
                for i,child in enumerate(value): yield from walk(child,path+'/'+str(i))
    assert set(walk(source))-set(source['geometry_fields']) == {'/asset_bindings/icons~1tile_events.png/source_crop'}


def test_profile_default_and_explicit_outlier():
    source=manifest()
    profile={'default':{'scale_x':.5,'scale_y':.75},'overrides':{
        '/navigation/cards':{'multiply':[1,1],'offset':[0,-640]}}}
    mapped=scale_manifest(source,Display(540,1920,280),profile)
    assert mapped['navigation']['battle']==[42,1852]
    assert mapped['navigation']['cards']==[448,1830]
    assert source['navigation']['cards']==[448,2470]
    with pytest.raises(ValueError):
        scale_manifest(source,Display(540,1920,280),{'overrides':{'/card_inventory/max_pages':{'value':99}}})


def test_bound_worker_uses_transformed_manifest_and_restores_context():
    from player.bootstrap_layout import bind_display, _display
    token=bind_display(Display(540,1280,180))
    try:
        assert manifest()['layout']=={'width':540,'height':1280,'dpi':180}
        assert manifest()['navigation']['cards']==[224,1235]
    finally:
        _display.reset(token)
    assert manifest()['layout']['height']==2560


def test_rendering_changes_calibration_storage(tmp_path):
    from player.accounts import calibration_dir
    cfg={'active_instance':'main','instances':{'main':{'account':'a','serial':'device'}},'accounts':{'a':{}},'adb':{'exe':'adb'}}
    before=calibration_dir(tmp_path,cfg)
    cfg['instances']['main']['rendering']={'width':1080,'height':2560,'dpi':360}
    assert calibration_dir(tmp_path,cfg)==before
    cfg['instances']['main']['rendering']={'width':1080,'height':1920,'dpi':280}
    assert calibration_dir(tmp_path,cfg)!=before


def test_bound_profile_is_frozen_until_next_display_bind(monkeypatch):
    from player import bootstrap_layout as b, geometry
    token=b.bind_display(Display(540,1280,180))
    try:
        monkeypatch.setattr(geometry, 'scale_manifest', lambda *a,**k: (_ for _ in ()).throw(AssertionError('rescaled during scan')))
        first=b.manifest();first['navigation']['cards'][0]=999
        assert b.manifest()['navigation']['cards']==[224,1235]
    finally:
        b._display.reset(token)
