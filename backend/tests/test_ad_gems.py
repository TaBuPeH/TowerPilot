"""Ad Gems must not route to the Store or tap when the HUD shifts."""
from interactions import ad_gems


def test_missing_offer_image_requires_both_claim_and_six(monkeypatch,tmp_path):
    import cv2
    import numpy as np
    from vision import textocr
    monkeypatch.setattr(ad_gems.settings,'template_path',lambda _:tmp_path/'missing.png')
    frame=np.zeros((2560,1080,3),np.uint8)
    cv2.rectangle(frame,(15,400),(195,500),(255,255,0),4)
    monkeypatch.setattr(textocr,'read_lines',lambda *a:[(20,20,'BUY 6')])
    assert ad_gems.find_claim(frame) is None
    monkeypatch.setattr(textocr,'read_lines',lambda *a:[(20,20,'CLAIM 20')])
    assert ad_gems.find_claim(frame) is None
    monkeypatch.setattr(textocr,'read_lines',lambda *a:[(20,20,'6 CLAIM')])
    assert ad_gems.find_claim(frame) is not None

def test_disabled_and_nonbattle_never_tap(monkeypatch):
    monkeypatch.setattr(ad_gems.wave_reader,'read_wave',lambda f:None)
    monkeypatch.setattr(ad_gems.act,'tap',lambda *a,**k: (_ for _ in ()).throw(AssertionError('tap')))
    ad_gems.AdGemCollector(False).poll(None)
    ad_gems.AdGemCollector(True).poll(None)

def test_claim_rechecks_position_and_confirms_disappearance(monkeypatch):
    events=[];taps=[]
    monkeypatch.setattr(ad_gems.wave_reader,'read_wave',lambda f:100)
    points=iter([(100,1300),(100,1300),None])
    monkeypatch.setattr(ad_gems,'find_claim',lambda f:next(points))
    monkeypatch.setattr(ad_gems.capture,'grab',lambda:None)
    monkeypatch.setattr(ad_gems.time,'sleep',lambda t:None)
    monkeypatch.setattr(ad_gems.act,'tap',lambda *a,**k:taps.append(a) or {})
    monkeypatch.setattr(ad_gems.logger,'event',lambda name,**k:events.append((name,k)))
    c=ad_gems.AdGemCollector();c.poll(None);c.poll(None)
    assert taps==[(100,1300)]
    assert events[-1]==('ad_gems_claim',{'confirmed':True})

def test_moving_claim_is_not_clicked(monkeypatch):
    monkeypatch.setattr(ad_gems.wave_reader,'read_wave',lambda f:100)
    points=iter([(100,1300),(100,1500)])
    monkeypatch.setattr(ad_gems,'find_claim',lambda f:next(points))
    monkeypatch.setattr(ad_gems.capture,'grab',lambda:None)
    monkeypatch.setattr(ad_gems.act,'tap',lambda *a,**k: (_ for _ in ()).throw(AssertionError('tap')))
    ad_gems.AdGemCollector().poll(None)

def test_separate_readiness_requirements():
    from player import readiness
    cfg={'active_instance':'main','instances':{'main':{}},'loadouts':{}}
    body={'kind':'coin','gather':{'flying_gem':False,'ad_gems':True,'free_store_gems':False,'guild':False,'quests_8h':False,'quest_rewards':False}}
    paths={p for r in readiness.requirements(cfg,body) for p in r['alternatives']}
    assert 'buttons/ad_gems_claim.png' in paths
    assert 'icons/free_gems.png' not in paths
    assert not any(p.startswith('floaters/') for p in paths)
    body['gather'].update(ad_gems=False,free_store_gems=True)
    paths={p for r in readiness.requirements(cfg,body) for p in r['alternatives']}
    assert 'icons/free_gems.png' in paths
    assert 'buttons/ad_gems_claim.png' not in paths


def test_guild_rewards_do_not_require_store_purchases():
    from player import readiness
    cfg = {'active_instance': 'main', 'instances': {'main': {}}, 'loadouts': {}}
    body = {'kind': 'coin', 'gather': {'guild': True}}
    paths = {p for r in readiness.requirements(cfg, body) for p in r['alternatives']}
    assert 'icons/guild_header.png' in paths
    assert 'icons/guild_coin.png' not in paths
    body['gather']['guild_store'] = True
    paths = {p for r in readiness.requirements(cfg, body) for p in r['alternatives']}
    assert 'icons/guild_coin.png' in paths


def test_native_wide_offer_uses_amount_glyph_when_ocr_omits_six(monkeypatch,tmp_path):
    import cv2
    import numpy as np
    from vision import textocr
    monkeypatch.setattr(ad_gems.settings,'template_path',lambda _:tmp_path/'missing.png')
    frame=np.zeros((2560,1080,3),np.uint8)
    cv2.rectangle(frame,(20,1270),(268,1418),(255,255,0),4)
    monkeypatch.setattr(textocr,'read_lines',lambda *a:[(30,80,'CLAIM')])
    observed=[]
    monkeypatch.setattr(ad_gems,'_six_amount',lambda p:observed.append(p.shape) or True)
    assert ad_gems.find_claim(frame)==(144,1344)
    assert observed[0][1]>240
    monkeypatch.setattr(ad_gems,'_six_amount',lambda p:False)
    assert ad_gems.find_claim(frame) is None


def test_offer_moves_with_effect_rows_and_closed_upgrade_panel(monkeypatch,tmp_path):
    import cv2
    import numpy as np
    from vision import textocr
    monkeypatch.setattr(ad_gems.settings,'template_path',lambda _:tmp_path/'missing.png')
    monkeypatch.setattr(textocr,'read_lines',lambda *a:[(20,30,'CLAIM 6')])
    for top in (1120,1270,1390,2069):
        frame=np.zeros((2560,1080,3),np.uint8)
        cv2.rectangle(frame,(20,top),(268,top+148),(255,255,0),4)
        assert ad_gems.find_claim(frame)==(144,top+74)
