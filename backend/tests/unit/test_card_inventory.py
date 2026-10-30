from types import SimpleNamespace
import json
import cv2
import numpy as np
import pytest
from player import card_inventory as ci
from player.calibrate import is_account_rel


def test_named_pages_scroll_to_both_boundaries_without_taps(tmp_path, monkeypatch):
    spec=ci.manifest()['card_inventory']
    x,y,w,h=32,1100,244,301
    frames=[]
    for i in range(3):
        frame=np.random.default_rng(i).integers(0,100,(2560,1080,3),dtype=np.uint8)
        frame[y+5:y+50,x+5:x+w-5]=i+1
        frames.append(frame)
    monkeypatch.setattr(ci,'screen_matches',lambda *a:True)
    monkeypatch.setattr(ci,'tile_rects',lambda f:[(x,y,w,h)])
    # Dedup is by absolute position now: a moving page advances the list by a
    # row, the bottom stops. A fixed tile at a growing offset is a new card
    # each page while scrolling, and the same card once the frame settles.
    monkeypatch.setattr(ci,'page_shift',lambda previous,current:0 if np.array_equal(previous,current) else 351)
    index=1
    moves=[]
    def swipe(*args,**kw):
        nonlocal index
        moves.append(args)
        index=max(0,index-1) if list(args)==spec['swipe_up'] else min(2,index+1)
    def read(frame):
        return [(0,0,{1:'Damage',2:'Health',3:'Coins'}[int(frame[0,0,0])])] if frame.shape[0]==45 else []
    cuts=[]
    def cut(*args):
        cuts.append(args[4])
        return {'verified':True}
    cal=SimpleNamespace(p={'report':str(tmp_path/'report.json')},player={},cut=cut,save_report=lambda:None)
    result=ci.scan(cal,grab=lambda:frames[index],swipe=swipe,read=read,pause=lambda _:None)
    assert result['complete'] and cuts==['Damage','Health','Coins']
    assert index==2
    assert len(moves)>=7
    assert json.loads((tmp_path/'card_manifest.json').read_text())['complete']


def test_unknown_screen_sends_no_scroll(tmp_path,monkeypatch):
    monkeypatch.setattr(ci,'screen_matches',lambda *a:False)
    cal=SimpleNamespace(p={'report':str(tmp_path/'report.json')})
    with pytest.raises(RuntimeError,match='obscured'):
        ci.scan(cal,grab=lambda:np.zeros((2560,1080,3),np.uint8),read=lambda f:[],
                swipe=lambda *a,**kw:pytest.fail('input on unknown screen'))
    assert not json.loads((tmp_path/'card_manifest.json').read_text())['complete']


def test_card_catalogue_writer_cannot_escape_directory():
    assert is_account_rel('cards/catalogue/attack_speed.png')
    assert not is_account_rel('cards/catalogue/../other.png')
    assert not is_account_rel('cards/catalogue/invalid/name.png')


def test_complete_green_bordered_tiles_and_partial_rows():
    frame=np.zeros((2560,1080,3),np.uint8)
    for x in ci.manifest()['card_inventory']['columns']:
        frame[1108:1393,x+3]=(0,255,0)
        frame[2210:2380,x+3]=(0,255,0)
    rects=ci.tile_rects(frame)
    assert len(rects)==4
    assert all(r[1]==1100 for r in rects)


def test_scroll_requires_two_complete_overlapping_cards(monkeypatch):
    previous=np.zeros((2560,1080,3),np.uint8)
    rng=np.random.default_rng(9)
    rectangles=[(32,1400,244,301),(289,1400,244,301)]
    for x,y,w,h in rectangles:
        previous[y:y+h,x:x+w]=rng.integers(0,255,(h,w,3),dtype=np.uint8)
    current=np.zeros_like(previous)
    current[1030:2030]=previous[1330:2330]
    monkeypatch.setattr(ci,'tile_rects',lambda f:rectangles)
    assert ci.page_shift(previous,current)==300
    current[:]=0
    with pytest.raises(RuntimeError,match='overlap'):
        ci.page_shift(previous,current)


def test_same_card_across_overlapping_pages_counts_once_with_repaired_name(tmp_path, monkeypatch):
    """The bug this fixes: one physical card, read 'Free LJpgrades' on the page
    it enters and 'Free Upgrades' on the next, was counted twice. Absolute
    position fuses it; the vocabulary keeps the game's spelling."""
    spec=ci.manifest()['card_inventory']
    x=spec['columns'][0]; w,h=spec['tile_width'],spec['tile_height']
    S=300
    codes={10:'Free LJpgrades',20:'Nuke',30:'Free Upgrades'}
    def band(frame,y,code):
        frame[y+5:y+50,x+5:x+w-5]=code
    page1=np.random.default_rng(1).integers(0,80,(2560,1080,3),dtype=np.uint8)
    page1[0,1,0]=1                              # page marker for tile_rects
    band(page1,1400,10)                         # the card enters here as 'Free LJpgrades'
    page2=np.random.default_rng(2).integers(0,80,(2560,1080,3),dtype=np.uint8)
    page2[0,1,0]=2
    band(page2,1100,30)                         # same card, scrolled up, now 'Free Upgrades' (never re-read)
    band(page2,1400,20)                         # a genuinely new card below it
    frames=[page1,page2]
    index=0
    def swipe(*args,**kw):
        nonlocal index
        index=max(0,index-1) if list(args)==spec['swipe_up'] else min(len(frames)-1,index+1)
    def tile_rects(frame):
        return [(x,1400,w,h)] if frame[0,1,0]==1 else [(x,1100,w,h),(x,1400,w,h)]
    def read(frame):
        return [(0,0,codes[int(frame[0,0,0])])] if frame.shape[0]==45 else []
    monkeypatch.setattr(ci,'screen_matches',lambda *a:True)
    monkeypatch.setattr(ci,'tile_rects',tile_rects)
    monkeypatch.setattr(ci,'page_shift',lambda p,c:0 if np.array_equal(p,c) else S)
    seen=[]
    def cut(phase,rel,crop,frame,name,extra):
        seen.append(name)
        return {'verified':True}
    cal=SimpleNamespace(p={'report':str(tmp_path/'report.json')},player={},cut=cut,save_report=lambda:None)
    result=ci.scan(cal,grab=lambda:frames[index],swipe=swipe,read=read,pause=lambda _:None)
    names=[c['name'] for c in result['cards']]
    assert result['complete']
    assert names==['Free Upgrades','Nuke']      # one physical card, repaired spelling; not three
    assert seen==['Free Upgrades','Nuke']       # the second-page 'Free Upgrades' tile was never re-read/cut
    entry=next(c for c in result['cards'] if c['name']=='Free Upgrades')
    assert entry['ocr']=='Free LJpgrades'       # raw reading kept alongside the repaired name


def test_active_check_and_mastery_border_are_independent_signals():
    """Active = green check overhanging the bottom-right gap; Mastery = green
    left border. The two are read from different regions and must not bleed."""
    spec = ci.manifest()['card_inventory']
    x, y, w, h = 32, 1100, spec['tile_width'], spec['tile_height']
    f = np.zeros((2560, 1080, 3), np.uint8)
    assert not ci.is_active(f, x, y, w, h) and not ci.has_mastery(f, x, y, w, h)
    f[y+h+2:y+h+20, x+w-36:x+w-2] = (0, 255, 0)      # green check in the gap below bottom-right
    assert ci.is_active(f, x, y, w, h)
    assert not ci.has_mastery(f, x, y, w, h)          # the border is still blank
    f[y+20:y+h-20, x+2:x+7] = (0, 255, 0)             # green Mastery border on the left edge
    assert ci.has_mastery(f, x, y, w, h) and ci.is_active(f, x, y, w, h)


def test_card_names_repairs_confident_slips_but_keeps_uncertainty():
    from player import card_names
    assert card_names.resolve('Stow Aura')=='Slow Aura'
    assert card_names.resolve('Free LJpgrades')=='Free Upgrades'
    assert card_names.resolve('Damage')=='Damage'
    assert card_names.resolve('Ceus')=='Cells'           # degraded but unmistakable -> repaired
    assert card_names.resolve('Cish') is None            # contested (Cash vs Coins): keep uncertain
    assert card_names.resolve('Quantum Widget') is None  # a card the table has never seen passes through
    assert all(card_names.resolve(n)==n for n in card_names.CARD_NAMES)
