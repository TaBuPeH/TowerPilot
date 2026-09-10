"""Read-only matching of installed artwork inside shipped manifest regions."""
import json
from functools import lru_cache
from pathlib import Path
import cv2
import settings
from player import accounts, asset_library, asset_verify
from player.bootstrap_layout import manifest


@lru_cache(maxsize=32)
def _images(folder, rel):
    definition = manifest().get('asset_bindings', {}).get(rel)
    if not definition:
        return ()
    indices = list(Path(folder).glob('asset_library/*/index.json'))
    if not indices:
        return ()
    index_path = max(indices, key=lambda p:p.stat().st_mtime)
    index = json.loads(index_path.read_text(encoding='utf-8'))
    mapping = asset_library.map_targets(index,{rel:definition})[rel]
    result=[]
    for candidate in mapping.get('candidates',[]):
        image=cv2.imread(str(index_path.parent/candidate['file']),cv2.IMREAD_UNCHANGED)
        if image is not None:
            if definition.get('source_crop'):
                x,y,w,h=definition['source_crop']
                image=image[y:y+h,x:x+w].copy()
            result.append(image)
    return tuple(result)


def match(frame, rel):
    definition = manifest().get('asset_bindings', {}).get(rel)
    if not definition or frame is None or frame.shape[:2] != (2560,1080):
        return False, 0.0
    folder = str(accounts.calibration_dir(settings.ROOT,settings.CONFIG))
    try:
        images = _images(folder,rel)
    except (OSError, ValueError, KeyError):
        return False, 0.0
    for image in images:
        hit=asset_verify.locate(image,frame,definition['search'])
        if hit:
            return True, float(hit['inlier_fraction'])
    return False, 0.0
