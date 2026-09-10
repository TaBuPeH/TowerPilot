"""Event quests first, then free/previously unlocked paid reward tracks."""
import re
import cv2
import numpy as np
from device import act
from interactions import missions
from player.bootstrap_layout import manifest
from runtime import logger
from vision import detect, textocr


def screen(frame):
    return detect._match(frame, 'icons/event_missions_tab.png', .85)[0]


def claim_buttons(frame, milestones=False):
    spec = manifest()['reward_collection']['event_missions']
    if milestones:
        found=[]
        for x,y,w,h in spec['milestone_slots']:
            patch=frame[y:y+h,x:x+w]
            if any(t.strip().upper()=='CLAIM' for _,_,t in textocr.read_lines(patch,2)):
                found.append((x+w//2,y+h//2))
        return found
    x0,y0,w0,h0 = spec['milestones' if milestones else 'viewport']
    roi = frame[y0:y0+h0,x0:x0+w0]
    # A quest owns its action. Reject buttons outside a complete cyan/white
    # quest-card border (including the boost purchase panel).
    bright=(roi.min(axis=2)>175).astype(np.uint8)*255
    borders,_=cv2.findContours(bright,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
    cards=[cv2.boundingRect(c) for c in borders]
    cards=[(x,y,w,h) for x,y,w,h in cards if w>=900 and 150<=h<=450]
    hsv=cv2.cvtColor(roi,cv2.COLOR_BGR2HSV)
    mask=cv2.inRange(hsv,(125,45,150),(175,255,255))
    contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    lo,hi=spec['milestone_button_width' if milestones else 'quest_button_width']
    hlo,hhi=spec['button_height']
    found=[]
    for contour in contours:
        x,y,w,h=cv2.boundingRect(contour)
        if not (lo<=w<=hi and hlo<=h<=hhi):
            continue
        if not any(cx<x and cy<y and x+w<cx+cw and y+h<cy+ch for cx,cy,cw,ch in cards):
            continue
        # OCR only verifies the action inside a located button, never a menu route.
        texts=[s.strip().upper() for _,_,s in textocr.read_lines(roi[y:y+h,x:x+w],2)]
        if any(re.fullmatch(spec['claim_text'],s) for s in texts):
            found.append((x0+x+w//2,y0+y+h//2))
    return sorted(found,key=lambda p:p[1])


def reward_dismiss(frame):
    if screen(frame) or detect.side_menu_open(frame): return None
    # Known reward reveal controls. Exact text at the manifest spot, no blind skip.
    spec=manifest()['reward_collection']['reveal']
    for item in spec:
        x,y,w,h=item['rect']
        if any(t.strip().upper() in item['labels'] for _,_,t in textocr.read_lines(frame[y:y+h,x:x+w],2)):
            return x+w//2,y+h//2
    return None


def event_flow():
    frame=yield
    point=missions.find_tile(frame,'icons/tile_events.png')
    if point is None:
        logger.event('event_rewards_unavailable',reason='Event menu icon not recognized')
        return
    missions._tap(*point,'events_open')
    for _ in range(12):
        frame=yield
        if screen(frame): break
    else:
        logger.event('event_rewards_unavailable',reason='Event screen not recognized')
        return
    # The entry remembers its last tab. Select Missions by its verified label.
    hit,_,loc=detect._match(frame,'icons/event_missions_tab.png',.85)
    tpl=detect._tpl('icons/event_missions_tab.png')
    missions._tap(loc[0]+tpl.shape[1]//2,loc[1]+tpl.shape[0]//2,'event_missions_tab')
    frame=yield
    frame=yield
    spec=manifest()['reward_collection']['event_missions']
    pages=0; claims=0; previous=None
    for _ in range(100):
        if not screen(frame):
            point=missions.find_skip(frame) or reward_dismiss(frame)
            if point: missions._tap(*point,'event_reward_skip')
            else: break
            frame=yield
            continue
        points=claim_buttons(frame)
        if points:
            point=points[0]
            frame=yield
            if point not in claim_buttons(frame): continue
            missions._tap(*point,'event_quest_claim')
            claims+=1
            previous=None
            frame=yield
            frame=yield
            continue
        x,y,w,h=spec['viewport']
        small=cv2.resize(frame[y:y+h,x:x+w],(100,190))
        if pages>=14 or (previous is not None and np.abs(small.astype(float)-previous.astype(float)).mean()<1): break
        previous=small
        act.swipe(*spec['scroll'],400,reason='event quest next page')
        pages+=1
        frame=yield
        frame=yield
    # The reward track scrolls too: restore the top before checking its slots.
    for _ in range(pages+1):
        if not screen(frame): break
        x0,y0,x1,y1=spec['scroll']
        act.swipe(x1,y1,x0,y0,400,reason='event rewards return to top')
        frame=yield
        frame=yield
    milestones=0
    for _ in range(8):
        if not screen(frame):
            point=missions.find_skip(frame) or reward_dismiss(frame)
            if not point: break
            missions._tap(*point,'event_reward_skip')
            frame=yield
            continue
        points=claim_buttons(frame,True)
        if not points: break
        point=points[0]
        frame=yield
        if point not in claim_buttons(frame,True): continue
        missions._tap(*point,'event_milestone_claim')
        milestones+=1
        frame=yield
        frame=yield
    logger.event('event_rewards_done',quests=claims,milestones=milestones,pages=pages)
    if screen(frame):
        missions._tap(*spec['return'],'event_return_to_game')
        frame=yield
