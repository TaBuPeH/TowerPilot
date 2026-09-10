import json
from pathlib import Path
import cv2
import numpy as np
import pytest
from player import bootstrap, calibrate, accounts
from player.bootstrap_layout import manifest, writable_targets


def test_manifest_is_native_bounded_and_only_safe_routes():
    m=manifest()
    assert m['layout']=={'width':1080,'height':2560,'dpi':360}
    assert writable_targets() <= accounts.generic_names()
    for screen in m['screens'].values():
        for item in screen['anchors']+screen['targets']:
            x,y,w,h=item['rect']
            assert 0<=x<x+w<=1080 and 0<=y<y+h<=2560
    assert all(r['to'] not in ('battle','tournament','purchase') for r in m['routes'])
    from interactions import tourney
    from vision import pills
    assert tourney.NAV['cards']==tuple(m['navigation']['cards'])
    assert pills.TAB_BANDS['modules']==tuple(m['tab_bands']['modules'])


def test_bootstrap_write_allowlist_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(calibrate,'template_path',lambda r:tmp_path/r)
    tile=np.random.default_rng(2).integers(0,255,(30,80,3),dtype=np.uint8)
    assert calibrate.write_template('home/battle_btn.png',tile)=='refused'
    assert calibrate.write_template('home/battle_btn.png',tile,bootstrap=True)=='written'
    assert calibrate.write_template('home/battle_btn.png',tile,bootstrap=True)=='exists'
    assert calibrate.write_template('../outside.png',tile,bootstrap=True)=='refused'
    assert calibrate.write_template('tourney/buy_ticket_title.png',tile,bootstrap=True)=='refused'


def test_screen_proof_rejects_dimmed_anchors_wrong_position_and_wrong_resolution():
    frame=np.full((2560,1080,3),240,np.uint8)
    lines=[(a['rect'][1],a['rect'][0],a['text']) for a in manifest()['screens']['home']['anchors']]
    assert bootstrap.screen_matches(frame,lines,'home')
    assert not bootstrap.screen_matches(frame//2,lines,'home')
    assert not bootstrap.screen_matches(frame,[(10,10,t) for _,_,t in lines],'home')
    assert not bootstrap.screen_matches(frame[:2000],lines,'home')


def test_native_anchor_crop_is_tried_before_enlargement(monkeypatch):
    from vision import textocr
    scales=[]
    def read(frame, scale):
        scales.append(scale)
        return [(0,0,'MISSIONS' if scale==1 else 'Misstorqs')]
    monkeypatch.setattr(textocr,'read_lines',read)
    assert bootstrap.anchor_present(np.full((100,100,3),240,np.uint8),[],
                                    {'text':'MISSIONS','rect':[0,0,80,30]})
    assert scales == [1]


def test_perk_header_does_not_prove_the_wrong_selected_tab():
    frame=np.zeros((2560,1080,3),np.uint8)
    frame[430:520,360:690]=240
    cv2.rectangle(frame,(654,534),(860,616),(0,255,0),5)
    lines=[(450,380,'PERKS')]
    assert bootstrap.screen_matches(frame,lines,'perks_hub')
    assert bootstrap.screen_matches(frame,lines,'perks_priority')
    assert not bootstrap.screen_matches(frame,lines,'perks_first')
    assert not bootstrap.screen_matches(frame,lines,'perks_ban')


def test_unknown_start_sends_no_input_and_writes_no_templates(tmp_path):
    cal=calibrate.Calibration({'stop':str(tmp_path/'stop'),'state':str(tmp_path/'state.json')},False,bootstrap=True)
    taps=[]
    scanner=bootstrap.Scanner(cal,{},grab=lambda:np.zeros((2560,1080,3),np.uint8),read=lambda f:[],tap=lambda *a,**k:taps.append(a),pause=lambda _:None)
    with pytest.raises(RuntimeError,match='Cannot verify home'):scanner.run()
    assert taps==[] and cal.entries==[]


def test_stop_is_checked_before_observation_or_tap(tmp_path):
    stop=tmp_path/'stop';stop.touch()
    cal=calibrate.Calibration({'stop':str(stop)},False,bootstrap=True)
    scanner=bootstrap.Scanner(cal,{},grab=lambda:pytest.fail('grab after stop'),tap=lambda *a,**k:pytest.fail('tap after stop'))
    with pytest.raises(calibrate.Stopped):scanner.run()


def test_changed_second_frame_never_saves_a_cut(tmp_path, monkeypatch):
    cal=calibrate.Calibration({},False,bootstrap=True)
    monkeypatch.setattr(cal,'cut',lambda *a,**k:pytest.fail('unstable cut saved'))
    scanner=bootstrap.Scanner(cal,{})
    rng=np.random.default_rng(99)
    a=rng.integers(0,255,(100,100,3),dtype=np.uint8)
    b=rng.integers(0,255,(100,100,3),dtype=np.uint8)
    scanner.cut({'rect':[10,10,40,40],'rel':'home/tile_guild.png'},a,b,[],icon=True)
    assert scanner.skipped


def test_module_descriptor_matches_clicked_art_and_rejects_other_art():
    from player.module_descriptor import locate_icon
    rng=np.random.default_rng(44)
    tile=np.zeros((150,150,3),np.uint8)
    for _ in range(100):
        cv2.circle(tile,tuple(map(int,rng.integers(10,140,2))),int(rng.integers(2,7)),tuple(map(int,rng.integers(70,255,3))),-1)
    panel=np.zeros((2560,1080,3),np.uint8)
    # Synthetic enlarged panel fixture, never a captured/shipped game image.
    panel[600:825,130:355]=cv2.resize(tile,(225,225))
    x,y,w,h=locate_icon(tile,panel)
    assert abs(x-130)<3 and abs(y-600)<3 and abs(w-225)<5 and abs(h-225)<5
    with pytest.raises(RuntimeError):locate_icon(rng.integers(0,255,tile.shape,dtype=np.uint8),panel)


def test_grid_must_still_show_captured_tile_before_opening(monkeypatch):
    from device import capture,act
    tile=np.random.default_rng(5).integers(0,255,(150,150,3),dtype=np.uint8)
    monkeypatch.setattr(capture,'grab',lambda:np.zeros((2560,1080,3),np.uint8))
    monkeypatch.setattr(act,'tap',lambda *a,**k:pytest.fail('moved tile tapped'))
    with pytest.raises(RuntimeError,match='moved after capture'):calibrate._inspect(126,1200,tile)


def test_short_module_description_tracks_observed_close_button(monkeypatch):
    from player.module_descriptor import locate_icon
    from interactions import inventory
    rng=np.random.default_rng(44)
    tile=np.zeros((150,150,3),np.uint8)
    for _ in range(100):
        cv2.circle(tile,tuple(map(int,rng.integers(10,140,2))),int(rng.integers(2,7)),tuple(map(int,rng.integers(70,255,3))),-1)
    panel=np.zeros((2560,1080,3),np.uint8)
    panel[950:1175,130:355]=cv2.resize(tile,(225,225))
    monkeypatch.setattr(inventory,'_find_close',lambda f:(930,925))
    x,y,w,h=locate_icon(tile,panel)
    assert abs(x-130)<3 and abs(y-950)<3 and abs(w-225)<5
    with pytest.raises(RuntimeError):
        locate_icon(rng.integers(0,255,tile.shape,dtype=np.uint8),panel)


def test_progress_history_persists_and_does_not_finish_work_early(tmp_path,monkeypatch):
    from runtime import logger
    monkeypatch.setattr(logger,'event',lambda *a,**k:None)
    p={'state':str(tmp_path/'state.json')}
    cal=calibrate.Calibration(p,False)
    scanner=bootstrap.Scanner(cal,{})
    scanner.progress('Checking emulator')
    scanner.finish_step(message='Emulator verified')
    scanner.begin_step(1)
    scanner.progress('Capturing Home')
    state=json.loads(Path(p['state']).read_text())['phases']['bootstrap']
    assert state['completed']==1
    assert state['steps'][0]['status']=='done'
    assert state['steps'][1]['status']=='running'
    assert state['steps'][1]['started_at'] >= state['steps'][0]['started_at']
    scanner.progress('Unexpected dialog','error')
    saved=json.loads(Path(p['state']).read_text())['phases']['bootstrap']
    assert saved['completed']==1 and saved['status']=='error'
    assert saved['steps'][1]['status']=='error'
    assert saved['steps'][2]['status']=='pending'
    assert saved['history'][0]['message']=='Checking emulator'
    assert saved['history'][-1]['message']=='Unexpected dialog'


def test_skipped_branches_are_history_steps_not_successful_visits(tmp_path,monkeypatch):
    from runtime import logger
    monkeypatch.setattr(logger,'event',lambda *a,**k:None)
    monkeypatch.setattr(calibrate,'_merge_draft',lambda *a:None)
    monkeypatch.setattr(bootstrap,'icon_present',lambda *a:False)
    p={'state':str(tmp_path/'state.json'),'stop':str(tmp_path/'stop')}
    cal=calibrate.Calibration(p,False)
    monkeypatch.setattr(cal,'save_report',lambda:None)
    scanner=bootstrap.Scanner(cal,{},tap=lambda *a,**k:None,pause=lambda _:None)
    monkeypatch.setattr(scanner,'observed',lambda name:(None,[]))
    monkeypatch.setattr(scanner,'harvest',lambda *a:None)
    scanner.state['screen_map']={'home':{'status':'verified','verified':7,'total':7}}
    monkeypatch.setattr(scanner,'module_detail',lambda *a:None)
    monkeypatch.setattr(scanner,'card_inventory',lambda *a:None)
    scanner.finish_step(message='Preflight passed')
    for stage in ('extract','map'):
        scanner.begin_step(scanner.step_index(stage))
        scanner.finish_step(message=stage+' completed')
    scanner.run()
    saved=json.loads(Path(p['state']).read_text())['phases']['bootstrap']
    assert saved['status']=='needs_attention'
    assert saved['completed']==saved['total']
    steps={s['label']:s for s in saved['steps']}
    assert steps['Guild']['status']=='skipped'
    assert steps['Guardian']['status']=='skipped'
    assert steps['Cards']['status']=='skipped'  # no artwork proof means no coordinate-only visit
    assert all(s['status']!='running' for s in saved['steps'])


def test_incomplete_home_never_taps(tmp_path,monkeypatch):
    from runtime import logger
    monkeypatch.setattr(logger,'event',lambda *a,**k:None)
    cal=calibrate.Calibration({'state':str(tmp_path/'state.json'),'stop':str(tmp_path/'stop')},False)
    taps=[]
    scanner=bootstrap.Scanner(cal,{},tap=lambda *a,**k:taps.append(a),pause=lambda _:None)
    monkeypatch.setattr(scanner,'observed',lambda name:(None,[]))
    monkeypatch.setattr(scanner,'harvest',lambda *a:None)
    scanner.state['screen_map']={'home':{'status':'needs_mapping','verified':6,'total':7,
        'blocks':[{'target':'presets/picker_icon.png','status':'needs_mapping'}]}}
    import pytest
    with pytest.raises(RuntimeError,match='Home mapping is incomplete'):
        scanner.run()
    assert taps==[]


def test_battle_preparation_reuses_menu_scan(tmp_path, monkeypatch):
    from player.mapping_session import Session
    cal=calibrate.Calibration({'state':str(tmp_path/'state.json'),'stop':str(tmp_path/'stop')},False)
    state={'screen_map':{'home':{'status':'verified'},'cards':{'status':'verified'}},
           'collections':{'cards':{'count':31}}}
    scanner=bootstrap.Scanner(cal,state,flows=True,battle_only=True)
    assert [s['id'] for s in scanner.steps] == ['preflight','extract','map','home','flow_battle','finish']
    monkeypatch.setattr(scanner,'observed',lambda name:(None,[]))
    monkeypatch.setattr(scanner,'harvest',lambda *a:None)
    monkeypatch.setattr(scanner,'_run_flows',lambda:None)
    monkeypatch.setattr(scanner,'progress',lambda *a,**k:None)
    monkeypatch.setattr(scanner,'finish_step',lambda *a,**k:None)
    monkeypatch.setattr(scanner,'begin_step',lambda *a,**k:None)
    monkeypatch.setattr(cal,'save_report',lambda:None)
    monkeypatch.setattr(calibrate,'_merge_draft',lambda *a:None)
    monkeypatch.setattr(Session,'run',lambda *a:pytest.fail('must not repeat menu/inventory scan'))
    scanner.run()
    assert state['collections']['cards']['count']==31
    assert state['screen_map']['cards']['status']=='verified'
