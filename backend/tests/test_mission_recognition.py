import cv2
import numpy as np
from interactions import missions
from vision import textocr


def test_reward_without_image_uses_verified_manifest_text(monkeypatch):
    from interactions import event_rewards
    monkeypatch.setattr(missions.detect, '_match', lambda *a: (False,0,None))
    monkeypatch.setattr(event_rewards, 'screen', lambda f: False)
    monkeypatch.setattr(event_rewards.detect, 'side_menu_open', lambda f: False)
    monkeypatch.setattr(textocr, 'read_lines', lambda *a: [(5,5,'SKIP')])
    frame=np.zeros((2560,1080,3),np.uint8)
    assert missions.find_skip(frame)==(900,375)
    monkeypatch.setattr(textocr, 'read_lines', lambda *a: [(5,5,'Farm')])
    assert missions.find_skip(frame) is None


def test_weekly_chest_waits_for_delayed_reward_before_return(monkeypatch):
    from vision import wave_reader
    taps=[]
    monkeypatch.setattr(missions,'find_tile',lambda *a:(1,1))
    monkeypatch.setattr(missions,'_tap',lambda x,y,reason,**kw:taps.append(reason))
    monkeypatch.setattr(missions,'missions_screen',lambda f:f[0,0,0]==1)
    monkeypatch.setattr(missions,'find_claim',lambda f:None)
    monkeypatch.setattr(missions,'claimable_chests',lambda f:[(2,2)])
    monkeypatch.setattr(missions,'find_skip',lambda f:(3,3) if f[0,0,0]==2 else None)
    monkeypatch.setattr(missions.act,'swipe',lambda *a,**kw:None)
    monkeypatch.setattr(missions.logger,'event',lambda *a,**kw:None)
    monkeypatch.setattr(missions.logger,'shot',lambda *a:None)
    monkeypatch.setattr(wave_reader,'read_wave',lambda f:110) # misleading number on reward
    menu=np.ones((2560,1080,3),np.uint8)
    reward=menu*2
    flow=missions.quest_flow(); next(flow)
    for _ in range(20):
        flow.send(menu)
        if 'weekly_chest' in taps: break
    assert 'weekly_chest' in taps
    flow.send(menu)
    flow.send(menu)  # old parent still visible during chest animation
    flow.send(reward)
    assert taps[-1]=='reward_skip'
    assert 'return_to_game' not in taps


def test_notification_badge_does_not_hide_mission_glyph(monkeypatch):
    template=np.zeros((100,100,3),np.uint8)
    cv2.rectangle(template,(3,3),(96,96),(255,255,255),5)
    cv2.line(template,(25,50),(45,72),(255,255,255),10)
    cv2.line(template,(45,72),(77,28),(255,255,255),10)
    frame=np.zeros((2560,1080,3),np.uint8)
    frame[120:220,860:960]=template
    cv2.circle(frame,(863,123),18,(0,0,255),-1)
    monkeypatch.setattr(missions.detect,'side_menu_open',lambda f:True)
    monkeypatch.setattr(missions.detect,'_tpl',lambda r:template)
    assert missions.find_tile(frame,'icons/tile_quests.png') == (910,170)
    assert missions.quests_badge(frame)


def test_claim_reads_only_a_verified_mission_button(monkeypatch,tmp_path):
    import settings
    monkeypatch.setattr(settings,'template_path',lambda r:tmp_path/'missing.png')
    monkeypatch.setattr(missions,'missions_screen',lambda f:True)
    frame=np.zeros((2560,1080,3),np.uint8)
    cv2.rectangle(frame,(20,1100),(1050,1370),(255,255,255),5)
    cv2.rectangle(frame,(150,1200),(900,1300),(255,0,255),5)
    def read(patch,scale):
        assert patch.shape[0]<150 and patch.shape[1]>350
        return [(10,200,'CLAIM')]
    monkeypatch.setattr(textocr,'read_lines',read)
    assert missions.find_claim(frame) == (525,1250)
    monkeypatch.setattr(textocr,'read_lines',lambda *a:[(0,0,'BUY')])
    assert missions.find_claim(frame) is None
    monkeypatch.setattr(missions,'missions_screen',lambda f:False)
    assert missions.find_claim(frame) is None
