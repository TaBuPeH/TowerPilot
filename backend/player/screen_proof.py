"""Learn text anchors once during setup; prove subsequent frames visually."""
import cv2
import numpy as np


class VisualAnchors:
    def __init__(self):
        self.images = {}

    def matches(self, key, frame, anchors):
        rows = self.images.get(key)
        if rows is None or len(rows) != len(anchors):
            return False
        for saved, spec in zip(rows, anchors):
            x,y,w,h = spec['rect']
            patch = frame[y:y+h,x:x+w]
            if patch.shape != saved.shape or saved.std()<2:
                return False
            score=float(cv2.matchTemplate(patch,saved,cv2.TM_CCOEFF_NORMED)[0,0])
            ratio=float(patch.mean())/max(1.,float(saved.mean()))
            if not np.isfinite(score) or score<.97 or not .9<=ratio<=1.1:
                return False
        return True

    def remember(self, key, frame, anchors):
        self.images[key] = [frame[y:y+h,x:x+w].copy()
                           for x,y,w,h in (a['rect'] for a in anchors)]
