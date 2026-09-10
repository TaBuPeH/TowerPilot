"""Executable view of the shipped catalogue, without duplicating its geometry.

Adapters supply screen recognition, locally verified control matching and capture.
Reference coordinates alone never authorize input. No device imports here: the
same executor can be exercised offline without touching a running game.
"""
from copy import deepcopy

from player.bootstrap_layout import manifest


def canonical(name):
    return manifest().get('execution', {}).get('screen_aliases', {}).get(name, name)


def catalogue(source=None):
    m = source if source is not None else manifest()
    screens = deepcopy(m['interface']['screens'])
    for alias, target in m.get('execution', {}).get('screen_aliases', {}).items():
        if alias in screens:
            variant = screens.pop(alias)
            screens[target].setdefault('contexts', {})['tournament'] = variant
    return screens


def transitions(source=None):
    """Compile navigation references; mutation actions are explicitly excluded."""
    m = source if source is not None else manifest()
    rows = []
    for i, route in enumerate(m['routes']):
        rows.append(dict(id=f'route_{i}', source=route['from'],
                         destination=route['to'], action=route['name'],
                         kind='navigate', availability='always', route=route))
    for name, screen in catalogue(m).items():
        for key, control in screen.get('controls', {}).items():
            if control.get('destination'):
                rows.append(dict(id=f'{name}.{key}', source=name,
                                 destination=canonical(control['destination']), action=key,
                                 kind=control.get('action', 'observe'), availability='always'))
    for i, row in enumerate(m['interface'].get('tournament_observed_transitions', [])):
        rows.append(dict(id=f'tournament_{i}', source=canonical(row['source']),
                         destination=canonical(row['destination']), action=row['action'],
                         kind='manual_only' if row['action'] in ('battle', 'claim') else 'navigate',
                         availability='tournament_open', context='tournament'))
    execution = m.get('execution', {})
    for row in rows:
        row['preconditions'] = execution.get('preconditions',
            ['source_screen_verified', 'control_verified_twice'])
        row['postcondition'] = 'destination_screen_verified'
    return rows


def plan(*, tournament_open=False, source=None):
    """Availability is an observation, never inferred from the weekday."""
    screens = catalogue(source)
    rows = []
    for edge in transitions(source):
        status = 'needs_local_verification'
        if edge['kind'] != 'navigate':
            status = 'manual_only'
        elif edge['availability'] == 'tournament_open' and not tournament_open:
            status = 'deferred'
        rows.append(dict(edge, status=status))
    return dict(start='home', screens=screens, steps=rows,
                shared_battle_screens=['battle', 'battle_menu'])


def validate(source=None):
    """Reject broken graph references before any connection or input."""
    p = plan(source=source)
    ids = set()
    for edge in p['steps']:
        if edge['id'] in ids:
            raise ValueError(f"Duplicate transition {edge['id']}")
        ids.add(edge['id'])
        for side in ('source', 'destination'):
            if edge[side] not in p['screens']:
                raise ValueError(f"Unknown {side} for {edge['id']}: {edge[side]}")
    return p


class MappingStopped(RuntimeError):
    pass


class Driver:
    """One observed transition at a time; uncertain outcomes are never retried.

    observe returns a frame token and canonical screen id. locate returns a
    locally proven rectangle and identity, or None. collect persists discoveries;
    publish persists progress before input as well as after verification.
    """
    def __init__(self, *, observe, locate, tap, collect, publish, check_stop,
                 tournament_open=False, geometry=None):
        self.observe, self.locate, self.tap = observe, locate, tap
        self.collect, self.publish, self.check_stop = collect, publish, check_stop
        self.tournament_open = tournament_open
        self.geometry = geometry

    def _observe(self):
        frame, screen = self.observe()
        if self.geometry is not None:
            self.geometry.begin_frame(id(frame))
        return frame, screen

    def walk(self, edges, *, max_steps=150):
        """Visit reachable menus and return Home using verified reverse routes.

        Unmatched controls stay pending. A failed postcondition raises immediately;
        there is no recovery tapping. Each directed edge is attempted once, except
        already proved edges needed to return to another unfinished branch.
        """
        from collections import deque
        _, screen = self._observe()
        screen = canonical(screen)
        if screen != 'home':
            raise MappingStopped('Open Home before mapping; a live run is left untouched')
        pending = {e['id']: e for e in edges if e['kind'] == 'navigate'
                   and (e['availability'] == 'always' or self.tournament_open)}
        proved, visited = [], {'home'}
        for _ in range(max_steps):
            self.check_stop()
            candidates = [e for e in pending.values() if e['source'] == screen]
            if candidates:
                edge = candidates[0]
                del pending[edge['id']]
            else:
                # Only replay edges whose destination was already confirmed.
                goals = {e['source'] for e in pending.values()} | {'home'}
                queue, seen, path = deque([(screen, [])]), {screen}, None
                while queue:
                    node, trail = queue.popleft()
                    if trail and node in goals:
                        path = trail
                        break
                    for e in proved:
                        if e['source'] == node and e['destination'] not in seen:
                            seen.add(e['destination'])
                            queue.append((e['destination'], trail + [e]))
                if not path:
                    break
                edge = path[0]
            if self.step(edge):
                screen = edge['destination']
                visited.add(screen)
                if not any(e['id'] == edge['id'] for e in proved):
                    proved.append(edge)
            elif not candidates:
                break  # previously verified return is no longer recognizable
        result = dict(status='mapped' if not pending and screen == 'home' else 'needs_attention',
                      screen=screen, visited=sorted(visited), pending=list(pending))
        # A missing control was attempted, but must never be counted as mapped.
        failed = [e['id'] for e in edges if e['kind'] == 'navigate'
                  and (e['availability'] == 'always' or self.tournament_open)
                  and not any(v['id'] == e['id'] for v in proved)]
        result['unverified'] = failed
        if failed:
            result['status'] = 'needs_attention'
        self.publish(result)
        return result

    def step(self, edge):
        self.check_stop()
        if edge['kind'] != 'navigate':
            self.publish(dict(id=edge['id'], status='manual_only'))
            return False
        if edge['availability'] == 'tournament_open' and not self.tournament_open:
            self.publish(dict(id=edge['id'], status='deferred'))
            return False
        first, screen = self._observe()
        if canonical(screen) != edge['source']:
            raise MappingStopped('Source screen changed; no input sent')
        hit = self.locate(first, edge)
        self.check_stop()
        second, screen = self._observe()
        other = self.locate(second, edge) if canonical(screen) == edge['source'] else None
        if not self._stable(hit, other, self.geometry.display if self.geometry else None):
            self.publish(dict(id=edge['id'], status='needs_local_verification',
                              first_match=hit, second_match=other, observed_screen=screen))
            return False
        self.check_stop()
        self.publish(dict(id=edge['id'], status='input_pending'))
        if self.geometry is not None:
            self.geometry.begin_frame(id(second))
            self.geometry.record(edge['id'], edge['source'], other['rect'],
                source=other.get('source_kind', 'two_frame_identity'), confidence=1.0,
                verified_frames=2, anchor={'identity': other['identity']})
            point = self.geometry.tap(edge['id'], edge['source'],
                lambda row: row['rect'] == other['rect'] and row['anchor']['identity'] == other['identity'])
        else:
            x, y, w, h = other['rect']
            point = (x + w // 2, y + h // 2)
        self.tap(*point)
        frame, screen = self._observe()
        if canonical(screen) != edge['destination']:
            self.publish(dict(id=edge['id'], status='uncertain', observed_screen=screen))
            raise MappingStopped('Destination unconfirmed; inspect before any further input')
        self.collect(edge['destination'], frame)
        self.publish(dict(id=edge['id'], status='done'))
        return True

    @staticmethod
    def _stable(a, b, display=None):
        if not a or not b or not a.get('verified') or not b.get('verified'):
            return False
        if not a.get('identity') or a['identity'] != b.get('identity'):
            return False
        layout = manifest()['layout'] if display is None else vars(display)
        for hit in (a, b):
            rect = hit.get('rect', [])
            if len(rect) != 4 or not all(isinstance(v, int) for v in rect):
                return False
            x, y, w, h = rect
            if x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > layout['width'] or y+h > layout['height']:
                return False
        return max(abs(x-y) for x, y in zip(a['rect'], b['rect'])) <= 3
