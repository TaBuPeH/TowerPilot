"""Read a manifest viewport to its end, keeping all data and frames local."""
from pathlib import Path

import cv2
import numpy as np

from player.bootstrap_layout import manifest
from runtime.files import atomic_json


def scan(scanner, name):
    from device import act
    spec=manifest()['screens'][name]['scan_collection']
    x,y,w,h=spec['viewport']
    folder=Path(scanner.cal.p['state']).parent/'screen_collections'/name
    folder.mkdir(parents=True,exist_ok=True)
    report={'screen':name,'complete':False,'pages':[],'viewport':spec['viewport']}
    def observe():
        return scanner.observed(name)[0]
    def same(a,b):
        a=a[y:y+h,x:x+w];b=b[y:y+h,x:x+w]
        return float(np.mean(cv2.absdiff(a,b)))<.8
    def settled():
        frame=observe()
        for _ in range(16):
            scanner.check_stop()
            scanner.pause(.3)
            following=observe()
            if same(frame,following):
                return following
            frame=following
        raise RuntimeError(f'{name}: scrolling did not settle; no further input sent')
    def move(frame, direction):
        scanner.check_stop()
        other=observe()
        if not same(frame,other):
            raise RuntimeError(f'{name}: screen changed before scrolling')
        top,bottom=y+int(h*.15),y+int(h*.85)
        start,end=(top,bottom) if direction=='top' else (bottom,top)
        act.swipe(x+w//2,start,x+w//2,end,650,reason=f'manifest scan: {name} {direction}')
        scanner.pause(.7)
        return settled()
    def park_top(frame):
        unchanged=0
        for _ in range(spec['max_pages']):
            following=move(frame,'top')
            unchanged=unchanged+1 if same(frame,following) else 0
            frame=following
            if unchanged>=2:
                return frame
        raise RuntimeError(f'{name}: could not verify the top of the list')
    scanner.progress(f'{name.replace("_"," ")}: finding the top of the list')
    frame=park_top(settled())
    unchanged=0
    for page in range(spec['max_pages']):
        scanner.check_stop()
        scanner.progress(f'{name.replace("_"," ")}: reading page {page+1}')
        cv2.imwrite(str(folder/f'page_{page+1}.png'),frame)
        lines=scanner.read(frame[y:y+h,x:x+w])
        report['pages'].append(dict(page=page+1, lines=[{'x':px+x,'y':py+y,'text':t} for py,px,t in lines]))
        atomic_json(folder/'observations.json',report)
        following=move(frame,'bottom')
        unchanged=unchanged+1 if same(frame,following) else 0
        frame=following
        if unchanged>=2:
            report['complete']=True
            break
    atomic_json(folder/'observations.json',report)
    scanner.state.setdefault('collections',{})[name]={'complete':report['complete'],'pages':len(report['pages'])}
    if not report['complete']:
        scanner.skipped.append({'screen':name,'reason':'Collection end was not confirmed; saved partial observations'})
    scanner.progress(f'{name.replace("_"," ")}: {len(report["pages"])} pages saved; returning to top')
    return park_top(frame)
