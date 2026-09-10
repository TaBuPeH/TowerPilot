import cv2
import numpy as np
from interactions import missions
from vision import textocr


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
