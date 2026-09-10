"""Locate extracted artwork in a native frame before creating a runtime crop."""
import math
import cv2
import numpy as np


def locate(image, frame, search, target_features=None):
    """Find `image` (installed artwork) inside `frame[search]`. Small on-screen
    controls (the ~66px HUD ability buttons, the UW switches) yield too few
    SIFT keypoints at native scale - Nuke matched with 9 inliers on the edge
    of the 8 minimum (2026-09-08) - so a miss is retried on the search region
    upscaled 2x and the rect mapped back. The upscale sees the same pixels,
    only bigger: nothing is invented."""
    hit=_locate(image,frame,search,target_features)
    if hit or target_features is not None:
        return hit
    x,y,w,h=search
    if min(x,y)<0 or x+w>frame.shape[1] or y+h>frame.shape[0] or image is None:
        return None
    big=cv2.resize(frame[y:y+h,x:x+w],None,fx=2,fy=2,interpolation=cv2.INTER_CUBIC)
    hit=_locate(image,big,[0,0,big.shape[1],big.shape[0]],None)
    if not hit:
        return None
    rx,ry,rw,rh=hit['rect']
    hit['rect']=[x+rx//2,y+ry//2,rw//2,rh//2]
    hit['scale']=round(hit['scale']/2,4)
    hit['upscaled']=2
    return hit


def _locate(image, frame, search, target_features=None):
    x,y,w,h=search
    if min(x,y)<0 or x+w>frame.shape[1] or y+h>frame.shape[0]:
        return None
    if image is None or image.size==0:
        return None
    if image.ndim==2:
        source=image
    elif image.shape[2]==4:
        source=cv2.cvtColor((image[:,:,:3].astype(np.float32)*(image[:,:,3:4]/255)).astype(np.uint8),cv2.COLOR_BGR2GRAY)
    else:
        source=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    target=cv2.cvtColor(frame[y:y+h,x:x+w],cv2.COLOR_BGR2GRAY)
    sift=cv2.SIFT_create(nfeatures=1600)
    ka,da=sift.detectAndCompute(source,None)
    kb,db=target_features if target_features is not None else sift.detectAndCompute(target,None)
    if da is None or db is None or len(db)<2:
        return None
    good=[m for pair in cv2.BFMatcher().knnMatch(da,db,k=2) if len(pair)==2 for m,n in [pair] if m.distance<.7*n.distance]
    if len(good)<8:
        return None
    src=np.float32([ka[m.queryIdx].pt for m in good]);dst=np.float32([kb[m.trainIdx].pt for m in good])
    affine,mask=cv2.estimateAffinePartial2D(src,dst,method=cv2.RANSAC,ransacReprojThreshold=2,maxIters=3000,confidence=.995)
    if affine is None or mask is None:
        return None
    keep=mask.ravel().astype(bool)
    if keep.sum()<8 or keep.mean()<.4:
        return None
    ih,iw=source.shape
    if np.ptp(src[keep,0])<iw*.3 or np.ptp(src[keep,1])<ih*.3:
        return None
    scale=math.hypot(affine[0,0],affine[1,0]);angle=math.degrees(math.atan2(affine[1,0],affine[0,0]))
    if not .08<=scale<=6 or abs(angle)>5:
        return None
    corners=cv2.transform(np.float32([[[0,0],[iw,0],[iw,ih],[0,ih]]]),affine)[0]
    left,top=np.floor(corners.min(axis=0)).astype(int)
    right,bottom=np.ceil(corners.max(axis=0)).astype(int)
    if left<0 or top<0 or right>w or bottom>h or min(right-left,bottom-top)<15:
        return None
    return {'rect':[int(left+x),int(top+y),int(right-left),int(bottom-top)],'inliers':int(keep.sum()),
            'inlier_fraction':round(float(keep.mean()),3),'scale':round(scale,4)}


def best_match(folder, mapping, frame, definition):
    hits=[]
    search = definition['search']
    # Developer mapping provides a small first search. Retain the declared
    # region as a fallback for layouts/account progression not mapped yet.
    reference = definition.get('reference_rect')
    nearby = None
    if reference:
        x,y,w,h = reference
        x0,y0=max(0,x-48),max(0,y-48)
        nearby=[x0,y0,min(frame.shape[1],x+w+48)-x0,min(frame.shape[0],y+h+48)-y0]
    for candidate in mapping.get('candidates',[]):
        image=cv2.imread(str(folder/candidate['file']),cv2.IMREAD_UNCHANGED)
        if image is not None and definition.get('source_crop'):
            sx,sy,sw,sh=definition['source_crop']
            image=image[sy:sy+sh,sx:sx+sw].copy()
        hit=locate(image,frame,nearby) if nearby else None
        if not hit:
            hit=locate(image,frame,search)
        if not hit and definition.get('silhouette'):
            hit=locate_silhouette(image,frame,search,white=definition.get('silhouette') == 'white', allow_multiple=definition.get('repeated', False))
        if not hit and definition.get('outline_shape'):
            hit=locate_outline(image,frame,search)
        if hit:
            hits.append(dict(hit,asset_id=candidate['id'],asset_name=candidate['name'],asset_sha256=candidate['sha256']))
    if not hits:
        return None
    return max(hits,key=lambda h:h.get('inliers',0))


def locate_outline(image, frame, search):
    """Match a hollow icon's outer shape despite differing outline thickness."""
    x,y,w,h=search
    if image is None or image.ndim!=3 or image.shape[2]!=4 or min(x,y)<0 or x+w>frame.shape[1] or y+h>frame.shape[0]:
        return None
    source=((image[:,:,:3].min(2)>205)&(image[:,:,3]>127)).astype(np.uint8)*255
    contours,_=cv2.findContours(source,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    if not contours: return None
    cv2.drawContours(source,contours,-1,255,-1)
    sx,sy,sw,sh=cv2.boundingRect(cv2.findNonZero(source));source=source[sy:sy+sh,sx:sx+sw]
    binary=(frame[y:y+h,x:x+w].min(2)>205).astype(np.uint8)*255
    _,labels,stats,_=cv2.connectedComponentsWithStats(binary)
    hits=[]
    for i,(px,py,pw,ph,area) in enumerate(stats[1:],1):
        if not (24<=min(pw,ph) and max(pw,ph)<=160 and abs((pw/ph)/(sw/sh)-1)<.15): continue
        patch=(labels[py:py+ph,px:px+pw]==i).astype(np.uint8)*255
        cs,_=cv2.findContours(patch,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(patch,cs,-1,255,-1)
        mask=cv2.resize(source,(int(pw),int(ph)))>127; target=patch>0
        score=np.count_nonzero(mask&target)/max(1,np.count_nonzero(mask|target))
        hits.append((score,[int(x+px),int(y+py),int(pw),int(ph)]))
    hits.sort(reverse=True)
    if hits and hits[0][0]>=.90 and (len(hits)==1 or hits[0][0]-hits[1][0]>=.10):
        return {'rect':hits[0][1], 'outline_score':float(hits[0][0])}
    return None


def locate_silhouette(image, frame, search, *, white=False, allow_multiple=False):
    """Simple flat sprites have too few SIFT features. Compare their complete
    alpha silhouettes in a bounded manifest cell, including interior holes.
    This is opt-in for flat navigation art, never a global shape search.
    """
    if image is None or image.ndim != 3 or image.shape[2] != 4:
        return None
    x,y,w,h=search
    if min(x,y)<0 or x+w>frame.shape[1] or y+h>frame.shape[0]:
        return None
    color = image[:,:,:3].min(axis=2)>205 if white else image[:,:,:3].max(axis=2)>100
    source=((image[:,:,3]>127)&color).astype(np.uint8)*255
    points=cv2.findNonZero(source)
    if points is None:
        return None
    sx,sy,sw,sh=cv2.boundingRect(points)
    source=source[sy:sy+sh,sx:sx+sw]
    gray=cv2.cvtColor(frame[y:y+h,x:x+w],cv2.COLOR_BGR2GRAY)
    if gray.std()<4:
        return None
    if white:
        binary=(frame[y:y+h,x:x+w].min(axis=2)>205).astype(np.uint8)*255
    else:
        _,binary=cv2.threshold(gray,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    count,_,stats,_=cv2.connectedComponentsWithStats(binary)
    boxes=list(stats[1:])
    # Some sprites (the flask cap, for example) contain separated components.
    parts=[s for s in boxes if s[4]>=100]
    if len(parts)>1:
        lx=min(s[0] for s in parts); ty=min(s[1] for s in parts)
        rx=max(s[0]+s[2] for s in parts); by=max(s[1]+s[3] for s in parts)
        boxes.append([lx,ty,rx-lx,by-ty,sum(s[4] for s in parts)])
    matches=[]
    for px,py,pw,ph,area in boxes:
        if min(pw,ph)<24 or area<150 or max(pw,ph)>160:
            continue
        if abs((pw/ph)/(sw/sh)-1)>.15:
            continue
        mask=cv2.resize(source,(int(pw),int(ph)),interpolation=cv2.INTER_AREA)>127
        patch=binary[py:py+ph,px:px+pw]>0
        score=float(np.count_nonzero(mask&patch)/max(1,np.count_nonzero(mask|patch)))
        if score>=.93:
            matches.append(dict(rect=[int(x+px),int(y+py),int(pw),int(ph)],
                                inliers=0, silhouette_score=round(score,4), scale=round(float(pw/sw),4)))
    return matches[0] if len(matches)==1 or (allow_multiple and matches) else None
