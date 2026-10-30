"""The provenance of a preview part (F6-13): every part of /api/stream.mjpg says what its own pixels are.

X-Preview-Frame-Kind (frame | error), X-Preview-Sequence (successful frames when the part was made; an error marker repeats the last number and never
advances it), X-Preview-Backend (the source that made the part: mumu | scrcpy | adb, or unknown) and X-Preview-Source-Age-Ms (the source's own estimate,
a non-negative number, or unknown). They are snapshotted with the part under the stream lock, so a fallback or a failure that happens right after a part
cannot relabel it, and the status record (which can race a fallback) is never needed to identify the picture on screen.
"""
import time
from types import SimpleNamespace

import numpy as np
import pytest
from werkzeug.test import EnvironBuilder

from device.preview import manager


class NoSleep:
    """The stream sleeps between frames (and 3 s after a failure): the test does not wait."""

    def sleep(self, seconds):
        pass

    def __getattr__(self, name):
        return getattr(time, name)


class Source:
    def __init__(self, age=None, fail_at=()):
        self.age, self.fail_at, self.reads = age, set(fail_at), 0

    @property
    def frame_age_ms(self):
        return self.age

    def read(self, timeout=2.0):
        self.reads += 1
        if self.reads in self.fail_at:
            raise RuntimeError('fixture read failed')
        return np.full((24, 12, 3), self.reads * 9 % 256, np.uint8)

    def close(self):
        pass


@pytest.fixture
def stream(dash, monkeypatch):
    """open(query, plan) -> (status, headers, parts iterator); plan[backend] is a Source, or an exception to raise when it is opened."""
    monkeypatch.setattr(dash, 'time', NoSleep())
    monkeypatch.setattr(dash, '_STREAMS', {})
    state = SimpleNamespace(port=True, opened=[])

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def create_connection(address, timeout=None):
        if not state.port:
            raise ConnectionRefusedError('closed')
        return Connection()
    monkeypatch.setattr('socket.create_connection', create_connection)
    opened = []

    def run(backend, plan, method='GET'):
        def open_source(self, name):
            opened.append(name)
            spec = plan(name) if callable(plan) else plan[name]
            if isinstance(spec, Exception):
                raise spec
            return spec
        monkeypatch.setattr(manager.PreviewCapture, '_open', open_source)
        environ = EnvironBuilder(path=f'/api/stream.mjpg?instance=main&stream_id=t1&fps=15&scale=1&q=90&backend={backend}', method=method).get_environ()
        out = {}

        def start_response(status, headers, exc_info=None):
            out['status'] = int(status.split()[0])
        iterable = dash.app.wsgi_app(environ, start_response)
        state.close = getattr(iterable, 'close', lambda: None)
        return out['status'], iterable
    state.run, state.opened_backends = run, opened
    yield state
    if hasattr(state, 'close'):
        state.close()


def headers_of(chunk):
    head = chunk.split(b'\r\n\r\n', 1)[0].decode().split('\r\n')
    assert head[0] == '--frame'
    return {k: v for k, v in (line.split(': ', 1) for line in head[1:]) if k.startswith('X-Preview-')}


def take(iterable, count):
    chunks = iter(iterable)
    return [headers_of(next(chunks)) for _ in range(count)]


def part(kind, sequence, backend, age='unknown'):
    return {'X-Preview-Frame-Kind': kind, 'X-Preview-Sequence': str(sequence), 'X-Preview-Backend': backend, 'X-Preview-Source-Age-Ms': age}


def test_every_part_names_its_kind_sequence_backend_and_source_age(stream):
    status, body = stream.run('auto', {'mumu': Source(age=50.0)})
    assert status == 200
    assert take(body, 3) == [part('frame', 1, 'mumu', '50'), part('frame', 2, 'mumu', '50'), part('frame', 3, 'mumu', '50')]


@pytest.mark.parametrize('age,written', [(None, 'unknown'), (12.5, '12.5'), (50, '50'), (0, '0'), (-5, '0'), (1234567.0, '1234567'), (4050.04, '4050'), (0.04, '0'),
                                          (float('nan'), 'unknown'), (float('inf'), 'unknown'), (True, 'unknown'), ('50', 'unknown')])
def test_the_source_age_is_a_clamped_number_or_unknown(stream, age, written):
    status, body = stream.run('mumu', {'mumu': Source(age=age)})
    assert take(body, 1)[0]['X-Preview-Source-Age-Ms'] == written


def test_a_fallback_relabels_only_the_parts_made_after_it(stream):
    mumu = Source(age=None, fail_at={3})
    status, body = stream.run('auto', {'mumu': mumu, 'scrcpy': Source(age=7.0), 'adb': Source()})
    assert take(body, 4) == [part('frame', 1, 'mumu'), part('frame', 2, 'mumu'), part('frame', 3, 'scrcpy', '7'), part('frame', 4, 'scrcpy', '7')]
    assert stream.opened_backends == ['mumu', 'scrcpy']


def test_an_error_marker_never_advances_the_sequence_and_has_no_source_age(stream):
    made = []

    def construct(name):
        # the screenshot source is constructed again after a failure: the first one fails at its 2nd read, the next works
        made.append(Source(age=3.0, fail_at={2} if not made else ()))
        return made[-1]
    status, body = stream.run('adb', construct)
    assert take(body, 3) == [part('frame', 1, 'adb', '3'), part('error', 1, 'adb'), part('frame', 2, 'adb', '3')]
    assert len(made) == 2


def test_a_marker_made_because_the_port_closed_has_no_source_age_although_the_source_is_alive(stream):
    status, body = stream.run('scrcpy', {'scrcpy': Source(age=50.0)})
    chunks = iter(body)
    assert [headers_of(next(chunks)) for _ in range(2)] == [part('frame', 1, 'scrcpy', '50'), part('frame', 2, 'scrcpy', '50')]
    stream.port = False
    assert headers_of(next(chunks)) == part('error', 2, 'scrcpy')


def test_a_stream_whose_port_is_closed_from_the_start_says_unknown_backend_and_sequence_zero(stream):
    stream.port = False
    status, body = stream.run('auto', {'mumu': Source(), 'scrcpy': Source(), 'adb': Source()})
    assert take(body, 1) == [part('error', 0, 'unknown')]
    assert stream.opened_backends == []


def test_head_validates_reserves_nothing_and_sends_no_part(stream, dash):
    status, body = stream.run('auto', {'mumu': Source()}, method='HEAD')
    assert status == 200 and b''.join(body) == b'' and dash._STREAMS == {} and stream.opened_backends == []
