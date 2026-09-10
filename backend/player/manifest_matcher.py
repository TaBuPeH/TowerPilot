"""Account-local matching adapter for the manifest transition executor."""
import hashlib

from player.clicker import locate
from player.manifest_driver import catalogue


class LocalMatcher:
    """Mappings are indexed by transition id, never by a guessed button label.

    Each mapping must carry its source screen, verified image hash, native rect,
    template path and manifest version. Templates and mappings stay account-local.
    Anchor-relative mappings need a freshly located anchor; moving menu icons are
    searched in their container rather than at an old observed row.
    """
    def __init__(self, mappings, read_bytes, decode, *, version, locate_anchor=None):
        self.mappings, self.read_bytes, self.decode = mappings, read_bytes, decode
        self.version, self.locate_anchor = version, locate_anchor

    def __call__(self, frame, edge):
        row = self.mappings.get(edge['id'], {})
        if (not row.get('verified') or row.get('screen') != edge['source']
                or row.get('manifest_version') != self.version):
            return None
        try:
            raw = self.read_bytes(row['template'])
            digest = hashlib.sha256(raw).hexdigest()
            if digest != row.get('image_sha256'):
                return None
            template = self.decode(raw)
        except (KeyError, OSError, ValueError):
            return None
        rect, search = row.get('rect'), None
        position = row.get('position', 'fixed')
        if position in ('anchor_relative', 'container'):
            if not self.locate_anchor:
                return None
            anchor = self.locate_anchor(frame, edge['source'], row.get('anchor'))
            if not anchor or not anchor.get('verified'):
                return None
            ax, ay, aw, ah = anchor['rect']
            if position == 'container':
                search = [ax, ay, aw, ah]
            else:
                offset = row.get('rect_offset')
                if not offset or len(offset) != 4:
                    return None
                dx, dy, w, h = offset
                rect = [ax+dx, ay+dy, w, h]
        elif position != 'fixed':
            return None
        result = locate(frame, template, rect, search=search)
        if not result['ok']:
            return None
        return dict(verified=True, identity=digest, rect=result['rect'])


def read_regions(screen, frame, read, *, context=None):
    """Collect text from declared fields locally; no values enter shipped data.

    These are native reference fields. Call only after proving screen identity;
    dynamic collection rows require their separately detected container anchors.
    """
    spec = catalogue()[screen]
    regions = dict(spec.get('read_regions', {}))
    if context:
        regions.update(spec.get('contexts', {}).get(context, {}).get('read_regions', {}))
    result = {}
    for key, rect in regions.items():
        x, y, w, h = rect
        if 0 <= x < x+w <= frame.shape[1] and 0 <= y < y+h <= frame.shape[0]:
            result[key] = dict(rect=rect, text=read(frame[y:y+h, x:x+w]))
    return result
