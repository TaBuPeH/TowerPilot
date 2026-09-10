"""HUD Ad Gems are separate from free Store gems and orbiting diamonds."""
import time
import re
import cv2
from device import act, capture
from vision import wave_reader
from runtime import logger
import settings

TEMPLATE = 'buttons/ad_gems_claim.png'

def _six_amount(patch):
    """Read the amount beside the gem, excluding the icon and button border."""
    h,w=patch.shape[:2]
    amount=patch[int(h*.10):int(h*.52),int(w*.5):int(w*.8)]
    mask=cv2.inRange(amount,(180,180,180),(255,255,255))
    contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    glyphs=[]
    for contour in contours:
        x,y,gw,gh=cv2.boundingRect(contour)
        if gh>=20 and gw>=6 and x>0 and y>0 and x+gw<mask.shape[1] and y+gh<mask.shape[0]:
            glyphs.append(mask[y:y+gh,x:x+gw])
    if len(glyphs)!=1:return False
    scores={}
    for d in range(10):
        path=settings.template_path(f'digits/{d}.png')
        if not path.exists():return False
        template=cv2.imread(str(path),cv2.IMREAD_GRAYSCALE)
        if template is None:return False
        resized=cv2.resize(glyphs[0],(template.shape[1],template.shape[0]))
        scores[d]=float(cv2.matchTemplate(resized,template,cv2.TM_CCOEFF_NORMED)[0,0])
    return scores[6]>=.8 and scores[6]-max(v for k,v in scores.items() if k!=6)>=.1


def find_claim(frame):
    from player.bootstrap_layout import manifest
    rx,ry,rw,rh = manifest()['runtime_regions']['ad_gems']['search']
    rail=frame[ry:ry+rh,rx:rx+rw]
    template = cv2.imread(str(settings.template_path(TEMPLATE))) if settings.template_path(TEMPLATE).exists() else None
    if template is None:
        # Offers can be absent throughout setup (including at the daily cap).
        # Locate the cyan offer block in the manifest's left HUD rail, then
        # verify its label. Both frames in poll must independently pass.
        from vision import textocr
        hsv=cv2.cvtColor(rail,cv2.COLOR_BGR2HSV)
        mask=cv2.inRange(hsv,(75,60,150),(110,255,255))
        contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x,y,w,h=cv2.boundingRect(contour)
            if not (100<=w<=300 and 55<=h<=180): continue
            text=' '.join(t for _,_,t in textocr.read_lines(rail[y:y+h,x:x+w],2)).upper()
            if re.search(r'\bCLAIM\b',text) and (re.search(r'\b6\b',text) or _six_amount(rail[y:y+h,x:x+w])):
                return (rx+x+w//2,ry+y+h//2)
        return None
    # Left HUD column; search vertically because wall/cards change its height.
    hay=rail
    h,w=template.shape[:2]
    if h>=hay.shape[0] or w>=hay.shape[1]:return None
    _,score,_,loc=cv2.minMaxLoc(cv2.matchTemplate(hay,template,cv2.TM_CCOEFF_NORMED))
    return (rx+loc[0]+w//2,ry+loc[1]+h//2) if score>=.92 else None

class AdGemCollector:
    def __init__(self, enabled=True):
        self.enabled=enabled
        self.next_at=0.0

    def poll(self, frame):
        if not self.enabled or time.monotonic()<self.next_at:return
        self.next_at=time.monotonic()+3
        if wave_reader.read_wave(frame) is None:return
        point=find_claim(frame)
        if point is None:return
        # Recheck before touching a shifting HUD, not a remembered position.
        fresh=capture.grab()
        if wave_reader.read_wave(fresh) is None:return
        current=find_claim(fresh)
        if current is None or abs(current[0]-point[0])+abs(current[1]-point[1])>12:return
        logger.event('ad_gems_tap',**act.tap(*current,reason='HUD Ad Gems claim'))
        time.sleep(.35)
        after=capture.grab()
        vanished=find_claim(after) is None
        logger.event('ad_gems_claim',confirmed=vanished)
        self.next_at=time.monotonic()+(30 if vanished else 5)
