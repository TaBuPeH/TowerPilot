"""Read named inventory tiles while scrolling; never tap/equip a card."""
import re
import time
from pathlib import Path
import cv2
import numpy as np
from player.bootstrap_layout import manifest
from player.bootstrap import screen_matches
from player.asset_library import atomic_json
from player import card_names

# A physical card is identified by its column and its absolute position in the
# list (measured scroll so far + its y in the viewport), never by its OCR'd
# name - the same card reads differently across the pages it overlaps. The
# rows sit ~350 px apart and the measured scroll drifts a pixel or two per
# page, so this window (matching the modules grid's dedup in calibrate.py) is
# wide enough to fuse a card seen twice yet far below the row pitch.
POSITION_TOLERANCE = 80

# A mastered card wears a bright green border (the game's Mastery upgrade tier);
# an un-mastered card's border is white. On the left border strip, green beats
# the stronger of red/blue by ~50+ when mastered and sits at or below ~8
# otherwise - a wide, unambiguous gap. This is NOT the active/equipped signal.
MASTERY_BORDER_GREEN = 30


def has_mastery(frame, x, y, w, h):
    """True when this tile's border is the game's green Mastery border. Read-only
    metadata - the scan never changes a card's mastery or which cards are equipped."""
    strip = frame[y+20:y+h-20, x+2:x+7]
    if strip.size == 0:
        return False
    b, g, r = (float(strip[..., i].mean()) for i in range(3))
    return g - max(r, b) > MASTERY_BORDER_GREEN and g > 150


# An equipped ('active') card shows a green check mark that overhangs the dark
# gap just below its bottom-right corner - clear of the Mastery border and the
# magenta level stars, which never reach there. That gap patch fills with green
# for an active card and stays dark otherwise (~0.75 vs 0.0 across this account
# - a wide, unambiguous gap that also matches the screen's own "ACTIVE n/n").
ACTIVE_CHECK_GREEN_FRACTION = 0.3


def is_active(frame, x, y, w, h):
    """True when this tile carries the game's green 'active/equipped' check mark.
    Read-only: the scan never equips, unequips or reorders a card."""
    fh, fw = frame.shape[:2]
    patch = frame[y+h+2:min(fh, y+h+20), x+w-36:min(fw, x+w-2)].astype(int)
    if patch.size == 0:
        return False
    g, r, b = patch[..., 1], patch[..., 2], patch[..., 0]
    return float(((g > 120) & (g - r > 40) & (g - b > 40)).mean()) > ACTIVE_CHECK_GREEN_FRACTION


def tile_rects(frame):
    spec = manifest()['card_inventory']
    x0,y0,w0,h0 = spec['viewport']
    rectangles = []
    for x in spec['columns']:
        # Follow the bright side of each rounded card, including green borders.
        lit = (np.max(frame[y0:y0+h0,x+3],axis=1)>150).astype(int)
        changes = np.diff(np.r_[0,lit,0])
        for start,end in zip(np.where(changes==1)[0],np.where(changes==-1)[0]):
            if not 280 <= end-start <= 290:
                continue
            y = int(start+y0-8)
            if y <= y0 or y+spec['tile_height'] >= y0+h0:
                continue
            rectangles.append((x,y,spec['tile_width'],spec['tile_height']))
    return sorted(rectangles,key=lambda r:(r[1]//12,r[0]))


def similarity(a,b):
    return float(cv2.matchTemplate(a,b,cv2.TM_CCOEFF_NORMED)[0,0])


def page_shift(previous,current):
    """Prove overlap using at least two complete cards, without a card count."""
    x0,y0,w0,h0=manifest()['card_inventory']['viewport']
    # Suppress subpixel edge rasterization changes after a drag, while keeping
    # native coordinates and the same agreement threshold. Saved art is raw.
    filtered_previous=cv2.GaussianBlur(previous,(3,3),.8)
    filtered_current=cv2.GaussianBlur(current,(3,3),.8)
    search=filtered_current[y0:y0+h0,x0:x0+w0]
    shifts=[]
    for x,y,w,h in tile_rects(previous):
        tile=filtered_previous[y:y+h,x:x+w]
        _,score,_,point=cv2.minMaxLoc(cv2.matchTemplate(search,tile,cv2.TM_CCOEFF_NORMED))
        dx=x-(point[0]+x0)
        dy=y-(point[1]+y0)
        # Two agreeing cards can be siblings on one row. Requiring two full
        # vertical tile heights rejects valid overlap on a shorter viewport.
        if score>.98 and abs(dx)<=3 and 0<=dy<=h0-h:
            shifts.append(dy)
    if len(shifts)<2 or max(shifts)-min(shifts)>3:
        raise RuntimeError('Card scroll lost verified overlap; a row may have been skipped. Inventory remains incomplete.')
    return int(round(float(np.median(shifts))))


def scroll_points(frame, direction, spec):
    points=list(spec[direction])
    if direction=='swipe_down':
        rows=tile_rects(frame)
        if not rows:
            raise RuntimeError('No complete card row available to prove the next scroll')
        # Keep the lowest complete row visible after movement. A fixed swipe
        # loses it when the viewport currently starts midway through a row.
        margin=round(spec['tile_height']*.04)
        available=max(y for x,y,w,h in rows)-spec['viewport'][1]-margin
        distance=min(points[1]-points[3],max(1,available//2))
        points[3]=points[1]-distance
    return points


def scan(cal, *, grab=None, swipe=None, read=None, read_label=None, pause=time.sleep, check_stop=lambda:None, progress=lambda message:None, prove=None):
    from device import capture, act
    from vision import textocr
    read_label=read_label or read or (lambda f:textocr.read_lines(f,2))
    grab,swipe,read = grab or capture.grab, swipe or act.swipe, read or (lambda f:textocr.read_lines(f,1))
    spec = manifest()['card_inventory']
    x0,y0,w0,h0 = spec['viewport']
    directory = Path(cal.p['report']).parent
    evidence = directory/'card_scan'
    evidence.mkdir(parents=True,exist_ok=True)
    cards = {}          # (abs_y, column_x) -> one entry per physical card
    placed = []         # (abs_y, column_x, entry) of cards already read this scan
    unread = []         # (abs_y, column_x) of complete tiles seen but not read
    offset = 0          # measured scroll below the top so far, native px
    def already(coll, ay, x):
        return any(abs(ay-py) < POSITION_TOLERANCE and x==px for py,px in coll)
    if hasattr(cal,'entries'):
        cal.entries=[e for e in cal.entries if not (e.get('phase')=='cards' and e.get('source')=='inventory_label')]
    atomic_json(directory/'card_manifest.json',{'complete':False,'cards':[],'status':'running'})
    def observe():
        check_stop()
        frame=grab()
        if not (prove(frame) if prove else screen_matches(frame,read(frame),'cards')):
            raise RuntimeError('Cards screen is obscured or unsupported; stopped before scrolling')
        return frame
    def grid(frame):
        return frame[y0:y0+h0,x0:x0+w0]
    def move(frame, direction):
        check_stop()
        follow=observe()
        if similarity(grid(frame),grid(follow))<.98:
            raise RuntimeError('Card inventory changed before scrolling; retry when stable')
        swipe(*scroll_points(frame,direction,spec),ms=900,reason='card calibration: preserve a complete inventory row')
        pause(.8)
        previous=observe()
        stable_frames=0
        for _ in range(15):
            pause(.3)
            current=observe()
            stable_frames=stable_frames+1 if similarity(grid(previous),grid(current))>.995 else 0
            if stable_frames>=2:
                return current
            previous=current
        raise RuntimeError('Card inventory is still moving after scrolling; scan stopped')
    progress('Cards: finding the top of the inventory')
    # The caller confirmed the cards screen just before this, but a single
    # scale-1 read of the medium INVENTORY anchor flakes on a frame grabbed
    # mid-settle (seen live: the harvest passed, then this raised 'obscured' one
    # frame later). Retry the FIRST check the way the harvest does; the
    # scroll-time observe() calls stay strict - the screen must not drift then.
    frame=None
    for _attempt in range(6):
        try:
            frame=observe(); break
        except RuntimeError:
            if _attempt==5: raise
            pause(.5)
    stable=0
    for _ in range(spec['max_pages']):
        after=move(frame,'swipe_up')
        stable=stable+1 if similarity(grid(frame),grid(after))>.995 else 0
        frame=after
        if stable>=2: break
    else: raise RuntimeError('Could not verify the top of the card inventory')
    stable=0
    for page in range(spec['max_pages']):
        progress(f'Cards: reading page {page+1}; {len(cards)} cards identified')
        cv2.imwrite(str(evidence/f'page_{page+1}.png'),frame)
        rects=tile_rects(frame)
        if not rects:
            raise RuntimeError('No complete card rows could be verified; inventory remains incomplete')
        follow=observe()
        for x,y,w,h in rects:
            check_stop()
            ay=offset+y                         # absolute position in the list
            seen=next((e for ay0,x0,e in placed if abs(ay-ay0)<POSITION_TOLERANCE and x==x0),None)
            if seen is not None:
                # Same physical card, seen again higher up the page: OR in its
                # state, where the check/border can sit clear of the viewport
                # clip that hid it on the row it first entered on.
                seen['active']=seen['active'] or is_active(frame,x,y,w,h)
                seen['mastery']=seen['mastery'] or has_mastery(frame,x,y,w,h)
                continue
            tile=frame[y:y+h,x:x+w]
            if similarity(tile,follow[y:y+h,x:x+w])<.98:
                raise RuntimeError('Card tile moved while reading its label')
            label=tile[5:50,5:w-5]
            lines=read_label(label)
            name=' '.join(str(t).strip() for _,_,t in lines).strip()
            # Read the same label independently; do not assign an OCR guess.
            confirmation=' '.join(str(t).strip() for _,_,t in read_label(follow[y+5:y+50,x+5:x+w-5])).strip()
            if name!=confirmation or not re.fullmatch(r'[A-Za-z][A-Za-z -]{2,39}',name):
                if not already(unread,ay,x): unread.append((ay,x))
                continue                        # keep this position open; a later page may read it cleanly
            unread=[u for u in unread if not (abs(ay-u[0])<POSITION_TOLERANCE and x==u[1])]
            # Repair a confident OCR slip to the game's own spelling; keep the
            # raw reading when the match is not close and unambiguous.
            display=card_names.resolve(name) or name
            key=re.sub(r'[^a-z0-9]+','_',display.lower()).strip('_')
            icon=tile[58:225,12:w-12].copy()
            # A card is a ROOT detector (cards/<key>.png) when it is one the
            # runtime looks up directly - the four allowlisted in
            # calibrate.is_account_rel (death_ray, extra_orb, cash,
            # ultimate_crit) or any card carrying an asset binding. Every other
            # card is the account's inventory catalogue.
            from player.calibrate import is_account_rel
            known=f'cards/{key}.png'
            rel=known if (is_account_rel(known) or known in manifest()['asset_bindings']) else f'cards/catalogue/{key}.png'
            cut_entry=cal.cut('cards',rel,icon,grid(frame),display,{'source':'inventory_label','page':page+1,'rect':[x+12,y+58,w-24,167]})
            entry={'name':display,'ocr':name,'rel':rel,'verified':cut_entry['verified'],
                   'active':is_active(frame,x,y,w,h),'mastery':has_mastery(frame,x,y,w,h),
                   'page':page+1,'column':spec['columns'].index(x),'abs_y':round(ay)}
            cards[(round(ay),x)]=entry
            placed.append((ay,x,entry))
            cal.player['cards_observed']=list(cards.values())
            cal.save_report()
        cal.player['cards_active']=[c['name'] for c in cards.values() if c['active']]
        cal.player['cards_mastered']=[c['name'] for c in cards.values() if c['mastery']]
        progress(f'Cards: page {page+1} read; {len(cards)} cards identified; scrolling down')
        after=move(frame,'swipe_down')
        offset+=page_shift(frame,after)          # accumulate the verified scroll so positions stay absolute
        stable=stable+1 if similarity(grid(frame),grid(after))>.995 else 0
        frame=after
        atomic_json(directory/'card_manifest.json',{'complete':False,'cards':list(cards.values()),'pages':page+1,
                    'unreadable_tiles':len(unread),'active':sum(1 for c in cards.values() if c['active'])})
        if stable>=2: break
    else: raise RuntimeError('Card scan reached its page limit before verifying the bottom')
    cal.player['cards_active']=[c['name'] for c in cards.values() if c['active']]
    cal.player['cards_mastered']=[c['name'] for c in cards.values() if c['mastery']]
    result={'complete':not unread and all(c['verified'] for c in cards.values()),'cards':list(cards.values()),
            'pages':page+1,'unreadable_tiles':len(unread),
            'active':sum(1 for c in cards.values() if c['active']),
            'mastered':sum(1 for c in cards.values() if c['mastery'])}
    atomic_json(directory/'card_manifest.json',result)
    if not result['complete']:
        raise RuntimeError(f'Card list scrolled to the bottom; {len(unread)} tile readings need attention. Observations saved, inventory not marked complete.')
    progress(f"Cards: reached the bottom; {len(cards)} cards identified; {result['active']} active, {result['mastered']} mastered")
    return result
