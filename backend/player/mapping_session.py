"""Calibration-worker adapter for the shared manifest navigation driver."""
import hashlib
import json
import time
import uuid
from pathlib import Path

import cv2

from player.bootstrap_layout import manifest
from player.manifest_driver import Driver, MappingStopped
from runtime.files import atomic_json


class Session:
    """Acquire route controls; disable content scans for bounded route retries."""

    def __init__(self, scanner, *, scan_contents=True):
        self.scanner = scanner
        self.expected = scanner.current
        self.edge = None
        self.last_hit = None
        self.last_source = None
        self.lines = []
        self.scan_contents = scan_contents
        self.evidence = None
        self.evidence_path = None
        self.folder = Path(scanner.cal.p['state']).parent
        self.path = self.folder / 'navigation_manifest.json'
        try:
            previous = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            previous = {}
        self.saved = {'version': manifest()['version'],
                      'controls': previous.get('controls', {}) if previous.get('version') == manifest()['version'] else {},
                      'history': []}
        from player.bootstrap_layout import route_capture_targets
        active = route_capture_targets()
        self.saved['controls'] = {key: row for key, row in self.saved['controls'].items()
                                  if key not in active or row.get('template') == active[key]}
        self.geometry = None
        if getattr(scanner, 'display', None) is not None:
            from player.geometry import Resolver
            # Calibration directory identifies the account and connection.
            from player.bootstrap_layout import reference_manifest
            self.geometry = Resolver(reference_manifest(), scanner.display, scanner.cal.p,
                                     str(self.folder.resolve()))
        self.driver = Driver(observe=self.observe, locate=self.locate,
            tap=self.tap, collect=self.collect, publish=self.publish,
            check_stop=scanner.check_stop, geometry=self.geometry,
            before_input=self.before_input)
        self.navigation = {}
        self.route_art = {}
        self.inventories_scanned = set()
        from player.asset_library import map_targets
        for name, definition in manifest().get('navigation_art', {}).items():
            d = dict(definition, screen='home')
            self.navigation[name] = (d, map_targets(scanner.asset_index or {'images': []}, {name: d})[name])

    def observe(self):
        try:
            frame, self.lines = self.scanner.observed(self.expected)
            return frame, self.expected
        except RuntimeError:
            return None, None

    def locate(self, frame, edge):
        from player import asset_verify
        from player.bootstrap import anchor_present, region
        self.edge = edge
        if frame is None:
            return None
        route = edge.get('route', {})
        hit = None
        if route.get('art') and self.scanner.asset_folder:
            from player.asset_library import map_targets
            definition = dict(route['art'], screen=edge['source'])
            mapping = self.route_art.setdefault(edge['id'], map_targets(self.scanner.asset_index, {edge['id']:definition})[edge['id']])
            found = asset_verify.best_match(self.scanner.asset_folder, mapping, frame, definition)
            if found:
                hit = dict(verified=True, rect=found['rect'], identity=found['asset_sha256'],
                           source_kind='installed_artwork', asset_name=found['asset_name'])
        elif 'nav' in route and self.scanner.asset_folder:
            definition, mapping = self.navigation[route['nav']]
            geometry = getattr(self, 'geometry', None)
            measured = geometry.measured(edge['id'], edge['source']) if geometry else None
            found = None
            if measured:
                found = asset_verify.best_match(self.scanner.asset_folder, mapping, frame,
                                               dict(definition, search=measured['rect']))
            if not found:
                found = asset_verify.best_match(self.scanner.asset_folder, mapping, frame, definition)
            if found:
                hit = dict(verified=True, rect=found['rect'], identity=found['asset_sha256'],
                           source_kind='installed_artwork', asset_name=found['asset_name'])
        elif route.get('icon'):
            found = self.scanner.asset_hit(route['icon']['rel'], frame) if self.scanner.asset_folder else None
            if found:
                hit = dict(verified=True, rect=found['rect'], identity=found['asset_sha256'],
                           source_kind='installed_artwork', asset_name=found['asset_name'])
            else:
                # Existing verified local crops cover composed picker controls
                # which are not the same rendering as the original sprite.
                import settings
                from player.clicker import locate
                rel = route['icon']['rel']
                path = settings.template_path(rel)
                if not path.is_file():
                    return None
                raw = path.read_bytes()
                import numpy as np
                template = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
                result = locate(frame, template, route['icon']['rect'])
                if result['ok']:
                    hit = dict(verified=True, rect=result['rect'], identity=hashlib.sha256(raw).hexdigest(),
                               source_kind='local_template')
        elif route.get('verify'):
            spec = route['verify']
            key = 'control:' + edge['id']
            cached = self.scanner.visual_anchors.matches(key, frame, [spec])
            present = cached or anchor_present(frame, self.lines, spec)
            if not present and not self.lines:
                # A screen may already be proved visually while this control
                # has never been read. Some isolated short words (Bots) are
                # omitted by OCR unless the surrounding tabs are present.
                self.lines = self.scanner.read(frame)
                present = anchor_present(frame, self.lines, spec)
            if present:
                self.scanner.visual_anchors.remember(key, frame, [spec])
                hit = dict(verified=True, rect=spec['rect'], identity=spec['text'],
                           source_kind='visual_anchor' if cached else 'text_anchor')
        if hit:
            self.last_hit, self.last_source = hit, frame
        return hit

    @staticmethod
    def _save_image(folder, name, frame):
        path = folder / (name + '.png')
        encoded, data = cv2.imencode('.png', frame)
        if not encoded:
            raise RuntimeError(f'Cannot encode navigation evidence: {name}')
        raw = data.tobytes()
        path.write_bytes(raw)
        return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest())

    def before_input(self, edge, first, hit, second, other):
        """Retain reviewable source proof before acting, never as a template.

        Each attempt has its own directory so a retry cannot erase the rejected
        candidate or an uncertain transition. Failure to save sends no input.
        """
        from player.bootstrap import region
        folder = self.folder / 'navigation_evidence' / uuid.uuid4().hex
        folder.mkdir(parents=True)
        self.evidence_path = folder / 'proof.json'
        self.evidence = dict(id=edge['id'], source=edge['source'],
            destination=edge['destination'], status='prepared',
            confirmation_method='two_frame_control_identity',
            first_match=hit, second_match=other, prepared_at=time.time(),
            source_frame=self._save_image(folder, 'source', first),
            confirmation_frame=self._save_image(folder, 'confirmation', second),
            candidate=self._save_image(folder, 'candidate', region(second, other['rect'])))
        atomic_json(self.evidence_path, self.evidence)

    def tap(self, x, y):
        self.scanner.check_stop()
        self.scanner.tap(x, y, reason=f"manifest scan: {self.edge['action']}")
        self.expected = self.edge['destination']

    def collect(self, screen, frame):
        from player.bootstrap import region
        self.scanner.current = screen
        # Candidate/source evidence exists before input. Only a proved
        # destination may promote its crop through the calibration writer.
        hit = self.last_hit
        rect = hit['rect']
        image = region(self.last_source, rect)
        from player.bootstrap_layout import route_capture_targets
        rel = route_capture_targets()[self.edge['id']]
        if self.evidence is not None:
            self.evidence['destination_frame'] = self._save_image(self.evidence_path.parent, 'destination', frame)
            self.evidence.update(destination_verified=True, destination_verified_at=time.time())
            atomic_json(self.evidence_path, self.evidence)
        entry = self.scanner.cal.cut('bootstrap', rel, image, self.last_source,
            self.edge['action'], dict(rect=rect, screen=self.edge['source'],
                source_kind=hit['source_kind'], confirmation_method='two_frame_control_identity',
                navigation_evidence=str(self.evidence_path),
                manifest_version=manifest()['version']), unique=False)
        self.saved['controls'][self.edge['id']] = dict(
            screen=self.edge['source'], destination=screen, template=rel, rect=rect,
            verified=entry['verified'], image_sha256=entry.get('image_sha256'),
            manifest_version=manifest()['version'], identity=hit['identity'],
            source_kind=hit['source_kind'], navigation_evidence=str(self.evidence_path), verified_at=time.time())
        if self.evidence is not None:
            self.evidence.update(template=rel, calibration_status=entry['status'],
                                 template_verified=entry['verified'], image_sha256=entry.get('image_sha256'))
            atomic_json(self.evidence_path, self.evidence)
        if self.geometry is not None and entry['verified'] and 'nav' in self.edge.get('route', {}):
            # Only the fixed bottom navigation persists. Menu rails, overlays
            # and list rows must be located again after a layout/scroll change.
            self.geometry.record(self.edge['id'], self.edge['source'], rect,
                source=hit['source_kind'], confidence=1.0, verified_frames=2,
                stable=True, anchor={'identity': hit['identity'], 'region': 'bottom_navigation'})
        atomic_json(self.path, self.saved)
        if not self.scan_contents:
            return
        if manifest()['screens'][screen].get('scan_collection'):
            from player.collection_scan import scan
            frame = scan(self.scanner, screen)
            self.lines = self.scanner.read(frame)
        self.scanner.harvest(screen, frame, self.lines)
        self.scan_inventory_once(screen, frame)
        self.scanner.update_screen_map(screen)

    def scan_inventory_once(self, screen, frame):
        # Revisiting a screen to verify another route must not repeat its
        # inventory walk. Only a successfully completed scan earns reuse.
        if screen not in ('cards', 'modules') or screen in self.inventories_scanned:
            return
        if screen == 'cards':
            self.scanner.card_inventory()
        elif screen == 'modules':
            self.scanner.module_detail(frame)
            from player.calibrate import read_module_contents
            self.scanner.progress('Modules: reading equipped slots and the full inventory')
            result = read_module_contents(self.scanner.cal, self.scanner.progress)
            self.scanner.state.setdefault('phases', {})['modules'] = dict(status='done', results=result)
        self.inventories_scanned.add(screen)

    def publish(self, event):
        if (self.evidence is not None and self.evidence['id'] == event.get('id')
                and event.get('status') in ('input_pending', 'uncertain', 'done')):
            self.evidence.update(event, updated_at=time.time())
            atomic_json(self.evidence_path, self.evidence)
            event = dict(event, navigation_evidence=str(self.evidence_path))
        self.saved['history'].append(dict(event, at=time.time()))
        atomic_json(self.path, self.saved)
        self.scanner.state['navigation'] = self.saved
        if event.get('status') == 'input_pending':
            self.scanner.progress(f"Opening {self.edge['action']}: control verified twice")

    def run(self, edges):
        for edge in edges:
            self.scanner.begin_step(self.scanner.step_index(edge['id']))
            if self.scanner.current != edge['source']:
                self.scanner.finish_step('skipped', 'Parent screen unavailable')
                continue
            before = len(self.scanner.skipped)
            self.scanner.progress(f"Verifying {edge['action']}")
            matched = self.driver.step(edge)
            # Tab content and labels can settle after the screen header. A
            # failed control proof sent no input, so one fresh observation is
            # safe. Never retry an input whose destination was uncertain.
            if not matched:
                self.scanner.pause(.6)
                matched = self.driver.step(edge)
            if not matched:
                skipped = dict(screen=edge['destination'],
                    reason='Control not verified against installed artwork or text')
                if edge.get('route', {}).get('optional') is True:
                    skipped['optional'] = True
                self.scanner.skipped.append(skipped)
                self.scanner.finish_step('skipped', 'Control unavailable; no input sent')
                continue
            if not self.scan_contents:
                verified = self.saved['controls'][edge['id']]['verified']
                self.scanner.finish_step('done' if verified else 'needs_attention',
                                         f"{edge['action']}: destination verified; route image "
                                         + ('verified' if verified else 'needs attention'))
                continue
            mapping = self.scanner.state['screen_map'][edge['destination']]
            # an optional skip (a guardian chip drawn as a padlock) is listed,
            # never attention - the same rule as the scan's final status
            missed = [s for s in self.scanner.skipped[before:] if not s.get('optional')]
            attention = bool(missed) or mapping['status'] != 'verified'
            self.scanner.finish_step('needs_attention' if attention else 'done',
                                    f"{edge['action']} mapped")
