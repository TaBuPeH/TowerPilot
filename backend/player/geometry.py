"""Reference geometry and verified, display-specific runtime measurements.

Coordinates are native pixels. Reference geometry only proposes search areas;
it never grants permission to click an unverified control.
"""
from copy import deepcopy
from dataclasses import dataclass, asdict
import hashlib
import json
import math


@dataclass(frozen=True)
class Display:
    width: int
    height: int
    dpi: int

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in asdict(self).values()):
            raise ValueError('Display width, height and DPI must be positive integers')


def revision(reference):
    return hashlib.sha256(json.dumps(reference, sort_keys=True).encode()).hexdigest()


def _slot(document, pointer):
    if not isinstance(pointer, str) or not pointer.startswith('/') or pointer == '/':
        raise ValueError('Geometry requires a nonempty JSON pointer')
    parts = [p.replace('~1', '/').replace('~0', '~') for p in pointer.split('/')[1:]]
    node = document
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    key = int(parts[-1]) if isinstance(node, list) else parts[-1]
    return node, key


def scale_manifest(reference, display):
    """Scale explicitly declared JSON pointers, always from reference values.

    Source-art pixels, time, counts and thresholds are deliberately undeclared.
    Never accept an already-transformed document as another reference.
    """
    if '_runtime_geometry' in reference:
        raise ValueError('Scale the shipped reference, not a runtime copy')
    base = Display(**reference['layout'])
    sx, sy = display.width/base.width, display.height/base.height
    result = deepcopy(reference)
    axes = {'x': sx, 'y': sy, 'min': min(sx, sy)}
    for pointer, units in reference.get('geometry_fields', {}).items():
        node, key = _slot(result, pointer)
        value = node[key]
        if isinstance(value, list):
            if len(value) != len(units) or any(u not in axes for u in units):
                raise ValueError(f'Invalid geometry declaration: {pointer}')
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in value):
                raise ValueError(f'Invalid geometry value: {pointer}')
            node[key] = [round(v*axes[u]) for v,u in zip(value, units)]
        else:
            if units not in axes or type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f'Invalid geometry declaration: {pointer}')
            node[key] = round(value*axes[units])
    result['layout'] = asdict(display)
    result['_runtime_geometry'] = {'reference': asdict(base), 'display': asdict(display),
                                   'revision': revision(reference), 'scale_x': sx, 'scale_y': sy}
    return result


class Resolver:
    """One resolver per account/connection/display and observation session."""
    def __init__(self, reference, display, store, context):
        self.reference = deepcopy(reference)
        self.display = display
        self.manifest = scale_manifest(reference, display)
        self.store = store
        self.context = str(context)
        self.frame = None
        self.transient = {}

    def begin_frame(self, token):
        if token != self.frame:
            self.transient.clear()
            self.frame = token

    def record(self, target, screen, rect, *, source, confidence, verified_frames,
               stable=False, anchor=None):
        if verified_frames < 2 or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError('A measurement requires two verified frames and a valid confidence')
        if len(rect) != 4 or any(type(v) is not int for v in rect):
            raise ValueError('A rectangle requires four native integer coordinates')
        x,y,w,h = rect
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > self.display.width or y+h > self.display.height:
            raise ValueError('Measured rectangle is outside the display')
        if not source or not screen:
            raise ValueError('Screen and source are required')
        row = {'screen': screen, 'rect': list(rect), 'source': source,
               'confidence': confidence, 'verified_frames': verified_frames,
               'display': asdict(self.display), 'context': self.context,
               'manifest_revision': revision(self.reference), 'anchor': deepcopy(anchor),
               'stable': stable}
        if stable:
            from player import learned
            data = learned.load(self.store)
            data.setdefault('runtime_targets', {})[target] = row
            learned._save(self.store, data)
        else:
            if self.frame is None:
                raise ValueError('Transient measurements require a frame token')
            self.transient[target] = row
        return deepcopy(row)

    def measured(self, target, screen):
        from player import learned
        row = self.transient.get(target) or learned.load(self.store).get('runtime_targets', {}).get(target)
        if not isinstance(row, dict) or row.get('screen') != screen or row.get('context') != self.context:
            return None
        if row.get('display') != asdict(self.display) or row.get('manifest_revision') != revision(self.reference):
            return None
        if type(row.get('verified_frames')) is not int or row['verified_frames'] < 2:
            return None
        rect = row.get('rect', [])
        if len(rect) != 4 or any(type(v) is not int for v in rect):
            return None
        x,y,w,h = rect
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > self.display.width or y+h > self.display.height:
            return None
        if target not in self.transient and not row.get('stable'):
            return None
        return deepcopy(row)

    def search(self, target, screen, reference_pointer):
        row = self.measured(target, screen)
        if row:
            return row['rect']
        node,key = _slot(self.manifest, reference_pointer)
        return deepcopy(node[key])

    def tap(self, target, screen, verify):
        """Even a persisted location must be proved on the current screen."""
        row = self.measured(target, screen)
        if row is None or not verify(deepcopy(row)):
            raise ValueError('Control is not verified on the current screen')
        x,y,w,h = row['rect']
        return x+w//2, y+h//2
