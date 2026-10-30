"""Human-requested, read-only catalogue matching on one native screenshot.

Results are observations, never templates, ownership, or navigation authority.
"""
import hashlib
import json
import time
from pathlib import Path
import cv2
from player.asset_verify import locate
from player.bootstrap import screen_matches
from player.bootstrap_layout import manifest
from player.asset_library import atomic_json


def _write_state(path, state):
    from runtime.operations import publish_progress
    publish_progress(state)
    atomic_json(path, state)


def _save_screenshot(path, frame):
    """Bind the producer's encoded bytes, never a pre-existing working PNG."""
    from runtime.operations import environment_operation
    _, operation_id = environment_operation()
    encoded, image = cv2.imencode('.png', frame)
    if not encoded:
        raise OSError('Screen analysis screenshot could not be encoded')
    raw = image.tobytes()
    revision = hashlib.sha256(raw).hexdigest()
    path.write_bytes(raw)
    if path.read_bytes() != raw:
        raise OSError('Screen analysis screenshot readback failed')
    return {'operation_id': operation_id, 'path': str(path), 'revision': revision}


def classify(frame, lines):
    matches = [name for name in manifest()['screens'] if screen_matches(frame, lines, name)]
    # A child page retains its parent's header. Do not label it as the parent.
    for parent, children in {'guild': {'guardians', 'guild_store'}, 'events': {'bots'}}.items():
        if children.intersection(matches) and parent in matches:
            matches.remove(parent)
    return {'name': matches[0] if len(matches) == 1 else 'unknown', 'candidates': matches}


def analyze(frame, lines, folder, index, progress=lambda *a: None):
    screen = classify(frame, lines)
    definitions = manifest()['asset_bindings']
    grouped = {}
    for item in index['images']:
        grouped.setdefault(item['sha256'], []).append(item)
    features = cv2.SIFT_create(nfeatures=10000).detectAndCompute(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), None)
    hits = []
    for n, aliases in enumerate(grouped.values(), 1):
        item = aliases[0]
        image = cv2.imread(str(folder/item['file']), cv2.IMREAD_UNCHANGED)
        hit = locate(image, frame, [0, 0, frame.shape[1], frame.shape[0]], features)
        if hit:
            names = sorted({a['name'] for a in aliases})
            roles = []
            for rel, spec in definitions.items():
                if spec['screen'] != screen['name'] or not {s.casefold() for s in spec['names']}.intersection(s.casefold() for s in names):
                    continue
                x,y,w,h = hit['rect']; sx,sy,sw,sh = spec['search']
                if sx <= x and sy <= y and x+w <= sx+sw and y+h <= sy+sh:
                    roles.append(rel)
            hits.append(dict(hit, names=names, roles=roles, source=item['file'], sha256=item['sha256']))
        if n % 20 == 0 or n == len(grouped):
            progress(n, len(grouped))
    # Sprite/texture variants with the same names and location are one identity.
    unique = []
    for hit in sorted(hits, key=lambda h:h['inliers'], reverse=True):
        if any(hit['names'] == old['names'] and
               max(abs(a-b) for a,b in zip(hit['rect'], old['rect'])) <= 8 for old in unique):
            continue
        unique.append(hit)
    hits = unique
    # Distinct source images can describe the same visible object. Retain them
    # as ambiguous evidence rather than choosing an identity by vote count.
    for a in hits:
        ax,ay,aw,ah = a['rect']
        a['ambiguous'] = False
        for b in hits:
            if a is b:
                continue
            bx,by,bw,bh = b['rect']
            overlap = max(0,min(ax+aw,bx+bw)-max(ax,bx))*max(0,min(ay+ah,by+bh)-max(ay,by))
            if overlap / max(1, aw*ah+bw*bh-overlap) > .7:
                a['ambiguous'] = True
    return {'screen': screen, 'matches': sorted(hits, key=lambda h:h['inliers'], reverse=True),
            'width': frame.shape[1], 'height': frame.shape[0], 'ocr': lines,
            'tested': len(grouped), 'captured_at': time.time()}


def main():
    import argparse
    import settings
    from player import accounts
    from device import capture
    from vision import textocr
    from player.asset_library import installed, valid_index
    ap = argparse.ArgumentParser()
    ap.add_argument('--instance', required=True)
    args = ap.parse_args()
    settings.bind_device(args.instance)
    settings.CONFIG['preset'] = 'normal_run'
    directory = accounts.calibration_dir(settings.ROOT, settings.CONFIG)
    state = directory/'screen_analysis.json'
    screenshot = None
    def write(value):
        if screenshot is not None:
            value['screenshot'] = screenshot
        _write_state(state, value)
    def progress(n, total):
        write({'status':'running','completed':n,'total':total,'message':'Matching installed artwork against the captured screen'})
    try:
        write({'status':'running','message':'Checking the installed artwork library'})
        identity = installed(settings.instance()['serial'])
        folder = directory/'asset_library'/identity['key']
        index = json.loads((folder/'index.json').read_text(encoding='utf-8'))
        if not valid_index(folder, index):
            raise RuntimeError('Extract artwork first; the local library is missing or damaged')
        frame = capture.grab()
        captured_at = time.time()
        screenshot = _save_screenshot(directory/'screen_analysis.png', frame)
        write({'status':'running','message':'Reading screen labels'})
        result = analyze(frame, textocr.read_lines(frame, 1), folder, index, progress)
        result.update(status='done', captured_at=captured_at)
        write(result)
    except Exception as exc:
        write({'status':'error','message':str(exc)})


if __name__ == '__main__':
    main()
