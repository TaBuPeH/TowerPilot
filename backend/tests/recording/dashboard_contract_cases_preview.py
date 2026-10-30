"""Contract cases of the live preview player (F6-13; backend/logs/reviews/ui-pages/f6-13-preview-brief.md section 7).

Families: preview_status (GET /api/stream/status), preview_backends (GET /api/stream/backends), preview_stream (GET /api/stream.mjpg) and
preview_frame_png (GET /api/frame.png with its headers). Every answer is the real route over the isolated world; nothing reads a
device, an emulator or the live dashboard.

What is controlled, and how:
  * the preview SOURCES. PreviewCapture._open (the real manager's one construction point) hands out synthetic sources - 12x24 frames,
    or 1080x2560 for the quality presets - or raises what a missing MuMu / scrcpy raises, so the manager's own fallback, failure log and
    cleanup handling run for real. A source's capture takes 10 ms and the JPEG encode 2 ms of the world's fixed clock; the age of a scrcpy source
    is 50 ms at the moment a frame completes and grows with the clock.
  * the emulator's TCP port (the stream probes it before every capture) is the world's FakeAdb.port.
  * a stream is read the way a browser reads it: the WSGI app is called directly, so the response is NOT iterated until the case asks for a part
    (the Flask test client would pull the first part before it returns). A recorded stream is the response headers and its first parts, decoded;
    "termination: recorder_closed" says the recorder - not the server - ended it. The JPEG's own bytes are not recorded as bytes: encoded_bytes is
    the length cv2 produced for the synthetic pixels and pixels_sha256 the decoded pixels, so they bind this backend's encoder, not a browser's.
  * headers: the Content-Type and Cache-Control the route sent (absent is absent), and for a part all of its own headers, including the
    four provenance headers added with this contract: X-Preview-Frame-Kind (frame | error), X-Preview-Sequence (successful frames when the part was made;
    an error marker does not advance it), X-Preview-Backend (mumu | scrcpy | adb | unknown) and X-Preview-Source-Age-Ms (a number | unknown).
"""
import hashlib
import io
from types import SimpleNamespace

import cv2
import numpy as np
from werkzeug.test import EnvironBuilder

from dashboard_contract_cases_environment import environment_world, platform_tools, put
from dashboard_contract_cases_runtime import pin_environment
from dashboard_contract_cases_screens import FRAME, binary, png_of, prepare, serve_frames, synthetic_frame
from device.preview import manager, scrcpy_package
from device.preview.common import PreviewError, PreviewUnavailable
from runtime import environment

PS, PB, PM, PF = 'preview_status', 'preview_backends', 'preview_stream', 'preview_frame_png'
SID = 'pv1'
SMALL = (24, 12)            # height, width of a synthetic frame: portrait, like the game
MARKER = (270, 96)          # width, height of the error marker the route draws
NEUTRAL = (2560, 1080)
CAPTURE, ENCODE = 0.010, 0.002
STATUS = '/api/stream/status?stream_id=' + SID
BACKENDS = ('mumu', 'scrcpy', 'adb')
M = dict(backend='mumu', reason='fixture mumu unavailable', stage='capture')
C = dict(backend='scrcpy', reason='fixture scrcpy unavailable', stage='capture')


def url(backend='auto', *, instance='main', stream_id=SID, fps='15', scale='1', q='90', extra=''):
    return f'/api/stream.mjpg?instance={instance}&stream_id={stream_id}&fps={fps}&scale={scale}&q={q}&backend={backend}{extra}'


# ----------------------------------------------------------------------------------------------- sources
class Source:
    """A preview source of the world. `fail_reads` are the 1-based reads that raise; `close_error` makes close() raise."""

    def __init__(self, w, backend, shape, *, fail_reads=(), close_error=None):
        self.w, self.backend, self.shape, self.fail_reads, self.close_error = w, backend, shape, set(fail_reads), close_error
        self.reads, self.closes = 0, 0

    def read(self, timeout=2.0):
        self.w.clock.now += CAPTURE
        self.reads += 1
        if self.reads in self.fail_reads:
            raise RuntimeError('fixture adb failed')
        height, width = self.shape
        return ((np.arange(height)[:, None, None] * 3 + np.arange(width)[None, :, None] * 5 + np.arange(3)[None, None, :] * 40 + self.reads * 7) % 256).astype(np.uint8)

    def close(self):
        self.closes += 1
        if self.close_error:
            raise RuntimeError(self.close_error)

    @property
    def frame_age_ms(self):
        """Only a scrcpy source estimates its age: 50 ms when a frame completes, growing with the clock."""
        if self.backend != 'scrcpy' or self.w.completed is None:
            return None
        return round(50 + (self.w.clock.now - self.w.completed) * 1000, 1)


def sources(w, plan=None, *, shape=SMALL):
    """Install the world's sources. plan[backend] is None/absent (a working source), an exception (what constructing it raises) or a
    dict(fail_reads=..., close_error=...). The JPEG encoder takes ENCODE ms of the clock and stamps the moment a frame completes."""
    plan = dict(plan or {})
    w.opened, w.made, w.completed = [], [], None
    real_imencode = cv2.imencode

    def imencode(ext, image, *args):
        done = real_imencode(ext, image, *args)
        if image.shape[:2] != (96, 270):                     # the error marker is not a frame: it takes no time and completes nothing
            w.clock.now += ENCODE
            w.completed = w.clock.now
        return done

    def open_source(self, backend):
        w.opened.append(backend)
        spec = plan.get(backend)
        if isinstance(spec, list):                           # one entry per construction; the last one repeats
            spec = spec.pop(0) if len(spec) > 1 else spec[0]
        if isinstance(spec, Exception):
            raise spec
        source = Source(w, backend, shape, **(spec or {}))
        w.made.append(source)
        return source

    w.mp.setattr(cv2, 'imencode', imencode)
    w.mp.setattr(manager.PreviewCapture, '_open', open_source)
    w.mp.setattr(w.dash, '_STREAMS', {})
    return plan


# ----------------------------------------------------------------------------------------------- recording
def kept(headers):
    return {name: value for name, value in headers if name in ('Content-Type', 'Cache-Control')}


def part_of(chunk):
    """One multipart part as the route yields it: its headers and the decoded picture."""
    head, _, rest = chunk.partition(b'\r\n\r\n')
    lines = head.decode('ascii').split('\r\n')
    assert lines[0] == '--frame', lines
    headers = dict(line.split(': ', 1) for line in lines[1:])
    jpeg = rest[:int(headers['Content-Length'])]
    assert rest[len(jpeg):] == b'\r\n', 'a part must end with CRLF'
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert image is not None, 'a part must be a JPEG'
    info = dict(content_type=headers['Content-Type'], width=image.shape[1], height=image.shape[0])
    if (info['width'], info['height']) == MARKER:
        # the error marker is drawn text: its pixels and its encoded length depend on the drawing library's build, so only its kind and size are
        # recorded (the header that carries the length is dropped with them)
        return dict(headers={name: value for name, value in headers.items() if name != 'Content-Length'}, binary=info)
    return dict(headers=headers, binary=dict(info, encoded_bytes=len(jpeg), pixels_sha256=hashlib.sha256(image.tobytes()).hexdigest().upper()))


class Stream:
    """A stream response read part by part, and closed when the scenario says so (or when its world ends)."""

    def __init__(self, w, path, method='GET'):
        self.w, self.path, self.method, self.parts, self.closed = w, path, method, [], False
        environ = EnvironBuilder(path=path, method=method).get_environ()
        out = {}

        def start_response(status, headers, exc_info=None):
            out['status'], out['headers'] = int(status.split()[0]), headers
        self.iterable = w.dash.app.wsgi_app(environ, start_response)     # nothing is iterated yet
        self.status, self.headers = out['status'], out['headers']
        self.chunks = iter(self.iterable)
        w.streams = getattr(w, 'streams', []) + [self]

    def take(self, count=1):
        for _ in range(count):
            self.parts.append(part_of(next(self.chunks)))
        return self.parts[-count:]

    def close(self):
        if not self.closed:
            self.closed = True
            close = getattr(self.iterable, 'close', None)
            if close:
                close()

    def body(self):
        if self.status != 200 or self.method == 'HEAD':
            return b''.join(self.chunks).decode('utf-8')
        return dict(multipart=dict(boundary='frame', parts=list(self.parts), termination='recorder_closed'))

    def record(self, family, case):
        body = self.body()
        if isinstance(body, str):
            try:
                import json
                body = json.loads(body) if body else dict(text='')
            except ValueError:
                body = dict(text=body)
        return put_case(self.w, family, case, dict(request=dict(method=self.method, path=self.path), status=self.status, headers=kept(self.headers), body=body))


def put_case(w, family, case, entry):
    w.gen.cases.setdefault(family, {})[case] = entry
    return entry


def get(w, family, case, path, *, expect=200):
    """A JSON GET recorded with the headers the route sent."""
    response = w.client.get(path)
    assert response.status_code == expect, (case, response.status_code, response.get_data(as_text=True)[:200])
    put_case(w, family, case, dict(request=dict(method='GET', path=path), status=response.status_code, headers=kept(response.headers.items()), body=response.get_json()))
    return response.get_json()


def close_all(w):
    for stream in getattr(w, 'streams', []):
        stream.close()


def ready(gen, name, plan=None, **kwargs):
    """A preview world: sources installed, the emulator port open, no stream known."""
    return gen.world(name), plan, kwargs


# ------------------------------------------------------------------------------------ the status shapes
def status_of(backend, *, frames, parts, requested=None, failures=(), age_ms=None, shape=SMALL, fps=15.0, scale=1.0, quality=90):
    """S0 / S(b) of the brief, computed from what the parts say - the expectations the generator asserts."""
    last = parts[-1]['binary'] if parts else None
    return dict(stream_id=SID, instance='main', target_fps=fps, scale=scale, quality=quality, width=last['width'] if last else None,
                height=last['height'] if last else None, jpeg_bytes=last['encoded_bytes'] if last else None, frames=frames, errors=0,
                capture_ms=10.0 if last else None, encode_ms=2.0 if last else None, backend=backend, requested_backend=requested or backend or 'auto',
                effective_fps=4.0 if backend == 'adb' else fps, backend_failures=list(failures), frame_age_ms=age_ms, active=True, last_error=None,
                last_frame_age_s=0.0 if last else None, actual_fps=None)


def source_age(backend):
    return 50.0 if backend == 'scrcpy' else None


def period_wait(backend, fps=15.0):
    """The generator's sleep after a successful frame: one period minus the 12 ms the frame took."""
    return 1.0 / (4.0 if backend == 'adb' else fps) - (CAPTURE + ENCODE)

# --------------------------------------------------------------------------------------------- scenarios
def begin(w, backend='auto', *, parts=0, **kwargs):
    stream = Stream(w, url(backend, **kwargs))
    assert stream.status == 200, (stream.status, stream.body())
    if parts:
        stream.take(parts)
    return stream


def part_headers(part):
    return {key: value for key, value in part['headers'].items() if key.startswith('X-Preview-')}


def expect_parts(parts, backend, *, kinds=('frame',), first=1, age=None):
    """Every part says what it is: a frame with its own sequence from `first`, the backend that made it, its source age."""
    for number, part in enumerate(parts):
        kind = kinds[min(number, len(kinds) - 1)]
        expected = {'X-Preview-Frame-Kind': kind, 'X-Preview-Sequence': str(first + number if kind == 'frame' else first + number - 1),
                    'X-Preview-Backend': backend, 'X-Preview-Source-Age-Ms': 'unknown' if age is None else f'{age:g}'}
        assert part_headers(part) == expected, (number, part_headers(part), expected)


def status_cases(gen):
    with gen.world('pv_status_missing') as w:
        sources(w)
        body = get(w, PS, 'missing_session', '/api/stream/status?stream_id=missing', expect=404)
        assert body == dict(ok=False, error='Stream session not found')
    with gen.world('pv_status_evicted') as w:
        sources(w)
        first = begin(w)
        first.close()
        assert w.client.get(STATUS).get_json()['active'] is False                    # retired, still readable
        for number in range(32):
            begin(w, stream_id=f'e{number:02d}')
        body = get(w, PS, 'evicted_session', STATUS, expect=404)
        assert body == dict(ok=False, error='Stream session not found')
        close_all(w)
    with gen.world('pv_status_idle') as w:
        sources(w)
        stream = begin(w)                                                              # reserved, not iterated
        got = get(w, PS, 'idle_reserved', STATUS)
        assert got == status_of(None, frames=0, parts=[], requested='auto') and 'ok' not in got and w.opened == []
        stream.close()
        assert w.client.get(STATUS).get_json()['active'] is False and w.opened == []
    for b in BACKENDS:
        with gen.world(f'pv_status_first_{b}') as w:
            sources(w)
            stream = begin(w, b, parts=1)
            got = get(w, PS, f'first_frame_{b}', STATUS)
            assert got == status_of(b, frames=1, parts=stream.parts, age_ms=source_age(b)), got
            close_all(w)
        with gen.world(f'pv_status_streaming_{b}') as w:
            sources(w)
            stream = begin(w, b, parts=2)
            fps = 4.0 if b == 'adb' else 15.0
            got = get(w, PS, f'streaming_{b}', STATUS)
            assert got == dict(status_of(b, frames=2, parts=stream.parts, age_ms=source_age(b)), actual_fps=fps), got
            expect_parts(stream.parts, b, kinds=('frame', 'frame'), age=source_age(b))
            close_all(w)
        with gen.world(f'pv_status_failed_{b}') as w:
            sources(w)
            stream = begin(w, b, parts=2)
            w.adb.port = False                                                         # the emulator's port stops answering
            w.advance(4)                                                               # 4 s past the last completed frame; the loop's wait for the next period is already over
            marker = stream.take(1)[0]
            assert (marker['binary']['width'], marker['binary']['height']) == (270, 96)
            assert part_headers(marker) == {'X-Preview-Frame-Kind': 'error', 'X-Preview-Sequence': '2', 'X-Preview-Backend': b, 'X-Preview-Source-Age-Ms': 'unknown'}
            got = get(w, PS, f'failed_{b}', STATUS)
            streaming = status_of(b, frames=2, parts=stream.parts[:2], age_ms=source_age(b))
            assert got == dict(streaming, errors=1, last_error='emulator port closed', last_frame_age_s=4.0, actual_fps=0.0,
                               frame_age_ms=4050.0 if b == 'scrcpy' else None), got
            close_all(w)
        with gen.world(f'pv_status_stale_{b}') as w:
            sources(w)
            stream = begin(w, b, parts=2)
            w.advance(4)
            got = get(w, PS, f'stale_{b}', STATUS)
            fps = 4.0 if b == 'adb' else 15.0
            assert got == dict(status_of(b, frames=2, parts=stream.parts, age_ms=source_age(b)), actual_fps=0.0, last_frame_age_s=4.0) and got['last_error'] is None, got
            close_all(w)
        with gen.world(f'pv_status_closed_{b}') as w:
            sources(w)
            stream = begin(w, b, parts=2)
            stream.close()
            assert [s.closes for s in w.made] == [1]
            got = get(w, PS, f'closed_{b}', STATUS)
            fps = 4.0 if b == 'adb' else 15.0
            assert got == dict(status_of(b, frames=2, parts=stream.parts, age_ms=source_age(b)), active=False, actual_fps=fps), got
    with gen.world('pv_status_cleanup') as w:
        sources(w, {'mumu': dict(close_error='fixture cleanup failed')})
        stream = begin(w, 'mumu', parts=2)
        stream.close()
        got = get(w, PS, 'cleanup_failed', STATUS)
        assert got == dict(status_of('mumu', frames=2, parts=stream.parts), active=False, actual_fps=15.0, errors=1, last_error='Preview cleanup failed: fixture cleanup failed',
                           backend_failures=[dict(backend='mumu', reason='fixture cleanup failed', stage='cleanup')]), got


FALLBACKS = [  # (case, requested backend, what fails, backend that serves, failures)
    ('fallback_mumu_to_scrcpy', 'auto', {'mumu': 'M'}, 'scrcpy', [M]),
    ('fallback_mumu_scrcpy_to_adb', 'auto', {'mumu': 'M', 'scrcpy': 'C'}, 'adb', [M, C]),
    ('fallback_scrcpy_to_adb', 'scrcpy', {'scrcpy': 'C'}, 'adb', [C]),
    ('requested_mumu_falls_back_to_scrcpy', 'mumu', {'mumu': 'M'}, 'scrcpy', [M]),
]


def fallback_cases(gen):
    for case, requested, failing, serving, failures in FALLBACKS:
        with gen.world('pv_' + case) as w:
            sources(w, {name: PreviewUnavailable(f'fixture {name} unavailable') for name in failing})
            stream = begin(w, requested, parts=2)
            stream.record(PM, case)
            expect_parts(stream.parts, serving, kinds=('frame', 'frame'), age=source_age(serving))
            fps = 4.0 if serving == 'adb' else 15.0
            got = get(w, PS, case, STATUS)
            assert got == dict(status_of(serving, frames=2, parts=stream.parts, requested=requested, failures=failures, age_ms=source_age(serving)), actual_fps=fps), got
            assert w.opened[:len(failures) + 1] == list(failing) + [serving] or serving in w.opened
            close_all(w)


def stream_cases(gen):
    for b in BACKENDS + ('auto',):
        with gen.world(f'pv_stream_start_{b}') as w:
            sources(w)
            stream = begin(w, b, parts=2)
            stream.record(PM, f'start_{b}')
            served = 'mumu' if b == 'auto' else b
            expect_parts(stream.parts, served, kinds=('frame', 'frame'), age=source_age(served))
            assert stream.parts[0]['binary']['width'] == 12 and stream.parts[0]['binary']['height'] == 24
            assert stream.headers and kept(stream.headers) == {'Content-Type': 'multipart/x-mixed-replace; boundary=frame', 'Cache-Control': 'no-cache'}
            close_all(w)
    fallback_cases(gen)
    with gen.world('pv_stream_error_marker') as w:
        sources(w, {'adb': [dict(fail_reads={1}), None]})
        stream = begin(w, 'adb')
        marker = stream.take(1)[0]
        assert part_headers(marker) == {'X-Preview-Frame-Kind': 'error', 'X-Preview-Sequence': '0', 'X-Preview-Backend': 'adb', 'X-Preview-Source-Age-Ms': 'unknown'}
        assert (marker['binary']['width'], marker['binary']['height']) == (270, 96)
        failing = get(w, PS, 'error_marker_status_before_recovery', STATUS)
        assert failing['errors'] == 1 and failing['frames'] == 0 and failing['last_error'] == 'Screenshot fallback failed: fixture adb failed', failing
        assert failing['backend_failures'] == [dict(backend='adb', reason='fixture adb failed', stage='capture')]
        frame = stream.take(1)[0]
        expect_parts([frame], 'adb', first=1)
        assert (frame['binary']['width'], frame['binary']['height']) == (12, 24)
        stream.record(PM, 'error_marker_then_recovery')
        recovered = get(w, PS, 'error_marker_status_after_recovery', STATUS)
        assert recovered['last_error'] is None and recovered['errors'] == 1 and recovered['frames'] == 1, recovered
        close_all(w)
    for case, scale, q, fps, shape in (('balanced', '0.65', '85', '15', (1664, 702)), ('low', '0.4', '70', '15', (1024, 432)), ('fractional_fps', '1', '90', '0.5', (2560, 1080))):
        with gen.world('pv_stream_' + case) as w:
            sources(w, shape=NEUTRAL)
            stream = begin(w, 'auto', parts=2, fps=fps, scale=scale, q=q)
            stream.record(PM, case)
            assert all((p['binary']['height'], p['binary']['width']) == shape for p in stream.parts), (case, [(p['binary']['height'], p['binary']['width']) for p in stream.parts])
            got = get(w, PS, 'status_after_' + case, STATUS)
            assert (got['scale'], got['quality'], got['target_fps'], got['frames']) == (float(scale), int(q), float(fps), 2), got
            assert (got['width'], got['height'], got['jpeg_bytes']) == (shape[1], shape[0], stream.parts[-1]['binary']['encoded_bytes'])
            close_all(w)
    with gen.world('pv_stream_head') as w:
        sources(w)
        head = Stream(w, url('auto'), method='HEAD')
        head.record(PM, 'head_no_capture')
        assert head.status == 200 and w.dash._STREAMS == {} and w.opened == [] and kept(head.headers)['Cache-Control'] == 'no-cache'
    with gen.world('pv_stream_refused') as w:
        sources(w)
        other = Stream(w, url('auto', instance='other'))
        got = other.record(PM, 'wrong_instance')
        assert got['status'] == 409 and got['body'] == dict(ok=False, error='Active emulator changed; refresh the stream')
        first = begin(w)
        duplicate = Stream(w, url('auto'))
        got = duplicate.record(PM, 'duplicate_active')
        assert got['status'] == 409 and got['body'] == dict(ok=False, error='Stream session already active')
        close_all(w)
    with gen.world('pv_stream_capacity') as w:
        sources(w)
        for number in range(32):
            begin(w, stream_id=f'c{number:02d}')
        full = Stream(w, url('auto', stream_id='one_too_many'))
        got = full.record(PM, 'capacity_full')
        assert got['status'] == 429 and got['body'] == dict(ok=False, error='Too many active streams')
        close_all(w)
    with gen.world('pv_stream_invalid') as w:
        sources(w)
        for case, kwargs, message in (('invalid_backend', dict(backend='other'), 'backend must be auto, mumu, scrcpy, or adb'), ('invalid_fps', dict(fps='0'), 'fps must be between 0.5 and 30'),
                                      ('invalid_quality', dict(q='29'), 'q must be between 30 and 90'), ('invalid_scale', dict(scale='0.1'), 'scale must be between 0.2 and 1'),
                                      ('invalid_stream_id', dict(stream_id='bad!'), 'Invalid stream_id'), ('invalid_display', dict(extra='&display=bad'), 'display: a whole number expected')):
            backend = kwargs.pop('backend', 'auto')
            got = Stream(w, url(backend, **kwargs)).record(PM, case)
            assert got['status'] == 400 and got['body'] == dict(ok=False, error=message), (case, got['body'])
        assert w.opened == [] and w.dash._STREAMS == {}
    with gen.world('pv_stream_saved_invalid') as w:
        sources(w)
        w.edit_config(lambda cfg: cfg['instances']['main'].__setitem__('preview', {'fps': 0}))
        got = Stream(w, url('auto')).record(PM, 'invalid_saved_settings')
        assert got['status'] == 400 and got['body'] == dict(ok=False, error='preview.fps must be between 0.5 and 30')
    with gen.world('pv_stream_owned') as w:
        sources(w)
        operation = w.running('run', {'preset': 'bp_farm'}, args=['--preset', 'bp_farm'])
        before = w.store.get(operation['id'])
        stream = begin(w, 'auto', parts=2)
        stream.record(PM, 'start_while_automation_owns_screen')
        assert w.store.get(operation['id']) == before and not any('input' in request for request in w.adb.requests), w.adb.requests
        expect_parts(stream.parts, 'mumu', kinds=('frame', 'frame'))
        close_all(w)


def backends_cases(gen):
    import contextlib

    @contextlib.contextmanager
    def world(name, *, mumu=True, server=True, decoder=True):
        with gen.world(name) as w:
            install = w.root / 'install'
            (install / 'backend').mkdir(parents=True)
            w.mp.setattr(w.dash, 'ROOT', str(install / 'backend'))
            pin_environment(w)
            platform_tools(install)
            path = w.root / 'tools' / 'scrcpy' / scrcpy_package.VERSION / f'scrcpy-server-v{scrcpy_package.VERSION}'
            w.mp.setattr(scrcpy_package, 'default_server_path', lambda: path)
            if server:
                put(path, b'synthetic scrcpy server')
                w.mp.setattr(scrcpy_package, 'SERVER_SHA256', hashlib.sha256(b'synthetic scrcpy server').hexdigest())
            if not decoder:
                w.mp.setattr(scrcpy_package, 'importlib', SimpleNamespace(util=SimpleNamespace(find_spec=lambda name: None)))
            if mumu:
                w.mp.setattr(environment, 'discover_target', lambda serial, exe, options: dict(install_dir=str(w.root / 'MuMu'), index=0))
            else:
                def refuse(serial, exe, options):
                    raise PreviewUnavailable('fixture mumu unavailable')
                w.mp.setattr(environment, 'discover_target', refuse)
            yield w

    with world('pv_backends_all') as w:
        everything = get(w, PB, 'all_available', '/api/stream/backends')
        assert everything['ok'] is True and everything['mumu']['available'] and everything['scrcpy']['available'] and everything['adb']['available'] is True
        w.adb.state, w.adb.port = 'offline', False
        offline = get(w, PB, 'adb_available_transport_offline', '/api/stream/backends')
        assert offline == everything                                                    # availability is local prerequisites, never a connection
        assert not w.adb.requests, 'listing the backends must not talk to adb'
    with world('pv_backends_mumu', mumu=False) as w:
        got = get(w, PB, 'mumu_unavailable', '/api/stream/backends')
        assert got['mumu'] == dict(available=False, reason='fixture mumu unavailable') and got['scrcpy']['available']
    with world('pv_backends_package', server=False) as w:
        got = get(w, PB, 'scrcpy_package_missing', '/api/stream/backends')
        assert (got['scrcpy']['installed'], got['scrcpy']['decoder_available'], got['scrcpy']['available']) == (False, True, False)
    with world('pv_backends_decoder', decoder=False) as w:
        got = get(w, PB, 'scrcpy_decoder_missing', '/api/stream/backends')
        assert (got['scrcpy']['installed'], got['scrcpy']['decoder_available'], got['scrcpy']['available']) == (True, False, False) and 'PyAV missing' in got['scrcpy']['reason']
    with world('pv_backends_neither', server=False, decoder=False) as w:
        got = get(w, PB, 'scrcpy_package_and_decoder_missing', '/api/stream/backends')
        assert (got['scrcpy']['installed'], got['scrcpy']['decoder_available']) == (False, False) and '; PyAV missing' in got['scrcpy']['reason']
    with world('pv_backends_invalid') as w:
        w.edit_config(lambda cfg: cfg['instances']['main'].__setitem__('preview', {'fps': 0}))
        body = get(w, PB, 'invalid_saved_fps', '/api/stream/backends', expect=400)
        assert body == dict(ok=False, error='preview.fps must be between 0.5 and 30')
    with world('pv_backends_saved') as w:
        w.edit_config(lambda cfg: cfg['instances']['main'].__setitem__('preview', {'backend': 'scrcpy', 'fps': 12.5}))
        got = get(w, PB, 'saved_scrcpy_fractional_fps', '/api/stream/backends')
        assert (got['settings']['backend'], got['settings']['fps']) == ('scrcpy', 12.5)
    with world('pv_backends_other') as w:
        w.edit_config(lambda cfg: cfg.update(active_instance='other'))
        got = get(w, PB, 'other_instance', '/api/stream/backends')
        assert got['instance'] == 'other' and got['scrcpy']['available']


def frame_png_cases(gen):
    """GET /api/frame.png with the headers it sends (the answers of recognition_images, which records none)."""
    def record(w, case, path, expect):
        response = w.client.get(path)
        assert response.status_code == expect, (case, response.status_code)
        if response.mimetype == 'image/png':
            body = binary(response)
        elif response.is_json:
            body = response.get_json()
        else:
            body = dict(text=response.get_data(as_text=True))
        put_case(w, PF, case, dict(request=dict(method='GET', path=path), status=response.status_code, headers=kept(response.headers.items()), body=body))
        return response
    with gen.world('pv_png_ok') as w:
        prepare(w)
        got = record(w, 'frame_png_without_a_token_is_not_remembered', FRAME, 200)
        assert not w.dash._FRAME_CACHE and 'Cache-Control' not in got.headers
        refused = record(w, 'frame_png_display_that_is_not_a_number_400', FRAME + '?display=2;reboot', 400)
        assert not any('reboot' in request for request in w.adb.requests)
    with gen.world('pv_png_garbage') as w:
        prepare(w)
        serve_frames(w, raw=b'the screen is off')
        record(w, 'frame_png_that_is_not_a_png_502', FRAME + '?ts=t1', 502)
    with gen.world('pv_png_down') as w:
        prepare(w)
        w.adb.server = ConnectionRefusedError
        record(w, 'frame_png_capture_failed_502', FRAME + '?ts=t1', 502)


def run_preview_cases(gen):
    status_cases(gen)
    backends_cases(gen)
    stream_cases(gen)
    frame_png_cases(gen)
