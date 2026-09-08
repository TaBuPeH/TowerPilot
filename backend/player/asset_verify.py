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
    for candidate in mapping.get('candidates',[]):
        image=cv2.imread(str(folder/candidate['file']),cv2.IMREAD_UNCHANGED)
        hit=locate(image,frame,definition['search'])
        if hit:
            hits.append(dict(hit,asset_id=candidate['id'],asset_name=candidate['name'],asset_sha256=candidate['sha256']))
    if not hits:
        return None
    return max(hits,key=lambda h:h['inliers'])
