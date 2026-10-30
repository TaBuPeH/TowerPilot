import base64
import json
import os
import re
import subprocess
import sys
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from vision import textocr


def _batch_boundary(monkeypatch, tmp_path, output):
    temporary = textocr.tempfile.TemporaryDirectory
    monkeypatch.setattr(textocr.tempfile, 'TemporaryDirectory',
                        lambda **kw: temporary(dir=tmp_path, **kw))
    calls = []

    def run(args, **kwargs):
        script = base64.b64decode(args[-1]).decode('utf-16le')
        manifest = re.search(r"-LiteralPath '([^']+)'", script)[1]
        entries = json.loads(open(manifest, encoding='utf-8').read())
        images = [cv2.imread(entry['path']) for entry in entries]
        calls.append((script, kwargs, images))
        return output(args, entries)

    monkeypatch.setattr(textocr.settings, 'run_hidden', run)
    return calls


def test_batch_recognizes_each_crop_and_scale_separately_with_identical_vote(monkeypatch, tmp_path):
    readings = ['Alpha', 'Alpha', 'Alpba', 'Tourney Pl', 'Tourney P1', 'Tourney P1']

    def output(args, entries):
        lines = [f'TP_OCR_IMAGE\t{entry["index"]}\n4\t6\t{readings[entry["index"]]}'
                 for entry in entries]
        return SimpleNamespace(args=args, returncode=0, stderr=b'', stdout='\n'.join(lines).encode())

    calls = _batch_boundary(monkeypatch, tmp_path, output)
    crops = [np.full((10, 20, 3), 30, np.uint8), np.full((15, 25, 3), 60, np.uint8)]
    assert textocr.read_texts(crops, raise_on_error=True) == ['Alpha', 'Tourney P1']
    assert len(calls) == 1
    assert [image.shape[:2] for image in calls[0][2]] == [(20, 40), (30, 60), (40, 80),
                                                                     (30, 50), (45, 75), (60, 100)]
    assert [int(image[0, 0, 0]) for image in calls[0][2]] == [30, 30, 30, 60, 60, 60]
    assert "$ProgressPreference='SilentlyContinue'" in calls[0][0]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('failure', ['timeout', 'exit', 'stderr', 'truncated', 'bad_index'])
def test_batch_failures_stay_distinct_from_empty_text_and_clean_all_artifacts(monkeypatch, tmp_path, failure):
    def output(args, entries):
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(args, .5)
        return SimpleNamespace(args=args, returncode=1 if failure == 'exit' else 0,
                               stderr=b'engine failed' if failure == 'stderr' else b'',
                               stdout=b'TP_OCR_IMAGE\t9' if failure == 'bad_index' else b'')

    _batch_boundary(monkeypatch, tmp_path, output)
    crop = np.zeros((10, 20, 3), np.uint8)
    with pytest.raises((subprocess.SubprocessError, ValueError)):
        textocr.read_texts([crop], scales=(2, 3), raise_on_error=True)
    assert not list(tmp_path.iterdir())
    assert textocr.read_texts([crop], scales=(2, 3)) == ['']
    assert not list(tmp_path.iterdir())


def test_batch_preserves_empty_reading_as_success_and_wrapped_line_order(monkeypatch, tmp_path):
    def output(args, entries):
        stdout = 'TP_OCR_IMAGE\t0\n40\t2\tLevel Skip\n2\t2\tEnemy Attack\nTP_OCR_IMAGE\t1\n'
        return SimpleNamespace(args=args, returncode=0, stderr=b'', stdout=stdout.encode())

    _batch_boundary(monkeypatch, tmp_path, output)
    crop = np.zeros((50, 80, 3), np.uint8)
    assert textocr.read_texts([crop, crop], scales=(1,), raise_on_error=True) == ['Enemy Attack Level Skip', '']
    assert not list(tmp_path.iterdir())


def test_batch_deadline_includes_preparation_and_cleans_all_artifacts(monkeypatch, tmp_path):
    calls = _batch_boundary(monkeypatch, tmp_path, lambda *args: None)
    times = iter((0, 2))
    monkeypatch.setattr(textocr.time, 'monotonic', lambda: next(times, 2))
    with pytest.raises(subprocess.TimeoutExpired):
        textocr.read_texts([np.zeros((10, 20, 3), np.uint8)], timeout=1, raise_on_error=True)
    assert not calls
    assert not list(tmp_path.iterdir())


# ------------------------------------------------------------ the warm reader (urgent reads, r15d)
class FakeServe:
    """A `winocr.ps1 -Serve` stand-in over a real pipe: answers each request line the way `reply` says."""

    def __init__(self, reply):
        self.reply, self.requests, self.closed = reply, [], False
        read, self._write = os.pipe()
        self.stdout = os.fdopen(read, 'rb')
        self.stdin = self
        self.returncode = None

    def write(self, data):
        request = json.loads(data.decode('utf-8'))
        self.requests.append(request)
        out = self.reply(request)
        if out is not None:
            os.write(self._write, out.encode('utf-8'))

    def flush(self):
        pass

    hangs = False                            # True: a WinRT call that never returns, so stdin EOF is never seen
    pid = 4242

    def close(self):                         # stdin EOF: the real script leaves its loop and exits
        if not self.closed:
            self.closed = True
            if not self.hangs:
                self.exited = True
                os.close(self._write)

    exited = False

    def poll(self):
        return 0 if self.exited else None


def answer(texts):
    def reply(request):
        lines = [f"TP_OCR_BEGIN\t{request['id']}"]
        for entry in request['images']:
            lines += [f"TP_OCR_IMAGE\t{entry['index']}", f"4\t6\t{texts[entry['index']]}"]
        return '\r\n'.join(lines + [f"TP_OCR_DONE\t{request['id']}"]) + '\r\n'
    return reply


@pytest.fixture
def warm(monkeypatch, tmp_path):
    temporary = textocr.tempfile.TemporaryDirectory
    monkeypatch.setattr(textocr.tempfile, 'TemporaryDirectory', lambda **kw: temporary(dir=tmp_path, **kw))
    monkeypatch.setattr(textocr, '_WARM', textocr._WarmReader())
    w = type('W', (), {})()
    w.spawned, w.replies = [], []

    def spawn(args, **kwargs):
        proc = FakeServe(w.replies.pop(0))
        w.spawned.append((args, kwargs, proc))
        return proc
    monkeypatch.setattr(textocr.settings, 'spawn_hidden', spawn)
    monkeypatch.setattr(textocr.settings, 'run_hidden', lambda *a, **k: pytest.fail('an urgent read started a cold PowerShell'))
    yield w
    textocr._WARM.abandon()


def test_an_urgent_read_uses_one_warm_reader_above_normal_and_votes_like_a_cold_batch(warm, tmp_path):
    readings = ['Alpha', 'Alpha', 'Alpba', 'Tourney Pl', 'Tourney P1', 'Tourney P1']
    warm.replies = [answer(readings)]
    crops = [np.full((10, 20, 3), 30, np.uint8), np.full((15, 25, 3), 60, np.uint8)]
    for _ in range(3):
        assert textocr.read_texts(crops, timeout=2, raise_on_error=True, urgent=True) == ['Alpha', 'Tourney P1']
    [(args, kwargs, proc)] = warm.spawned
    assert args[-3:] == ['-File', str(textocr.WINOCR), '-Serve']
    assert kwargs['creationflags'] == textocr.settings.NO_WINDOW | subprocess.ABOVE_NORMAL_PRIORITY_CLASS
    assert [r['id'] for r in proc.requests] == ['1', '2', '3']
    assert [len(r['images']) for r in proc.requests] == [6, 6, 6]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('failure', ['silent', 'wrong_id', 'engine_error', 'exited', 'garbage'])
def test_a_failed_warm_read_raises_abandons_the_reader_and_the_next_read_starts_fresh(warm, failure):
    def bad(request):
        rid = request['id']
        return {'silent': None,
                'wrong_id': f'TP_OCR_BEGIN\t{int(rid) + 7}\r\n',
                'engine_error': f'TP_OCR_ERROR\t{rid}\tThe OCR engine failed\r\n',
                'exited': '',
                'garbage': 'Exception calling "RecognizeAsync"\r\n'}[failure]
    warm.replies = [bad, answer(['CLAIM 6'])]
    if failure == 'exited':
        original = FakeServe.write
        def write_then_exit(self, data):
            original(self, data)
            self.close()
        FakeServe.write = write_then_exit
    crop = np.zeros((10, 20, 3), np.uint8)
    started = time.monotonic()
    try:
        with pytest.raises(subprocess.SubprocessError) as raised:
            textocr.read_texts([crop], scales=(2,), timeout=1.5, raise_on_error=True, urgent=True)
    finally:
        if failure == 'exited':
            FakeServe.write = original
    # a malformed or failed reply is refused at once; only silence waits for the deadline
    assert isinstance(raised.value, subprocess.TimeoutExpired) is (failure == 'silent')
    assert time.monotonic() - started < (2 if failure == 'silent' else 1)
    first = warm.spawned[0][2]
    assert first.closed                                          # abandoned: stdin closed, nothing signalled
    assert textocr.read_texts([crop], scales=(2,), timeout=2, raise_on_error=True, urgent=True) == ['CLAIM 6']
    assert len(warm.spawned) == 2


def test_a_silent_warm_reader_times_out_at_the_deadline(warm):
    warm.replies = [lambda request: None]
    with pytest.raises(subprocess.TimeoutExpired):
        textocr.read_texts([np.zeros((10, 20, 3), np.uint8)], scales=(2,), timeout=.3, raise_on_error=True, urgent=True)


def test_a_read_that_is_not_urgent_never_starts_the_warm_reader(monkeypatch, tmp_path):
    monkeypatch.setattr(textocr, '_WARM', textocr._WarmReader())
    monkeypatch.setattr(textocr.settings, 'spawn_hidden', lambda *a, **k: pytest.fail('warm reader started'))
    calls = _batch_boundary(monkeypatch, tmp_path, lambda args, entries: SimpleNamespace(
        args=args, returncode=0, stderr=b'', stdout=b'TP_OCR_IMAGE\t0\n4\t6\tCLAIM'))
    assert textocr.read_texts([np.zeros((10, 20, 3), np.uint8)], scales=(1,), raise_on_error=True) == ['CLAIM']
    assert 'creationflags' not in calls[0][1]


def _text_crop(text):
    crop = np.zeros((60, 260, 3), np.uint8)
    cv2.putText(crop, text, (8, 44), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (255, 255, 255), 3)
    return crop


@pytest.mark.skipif(os.name != 'nt' or not textocr.available(), reason='Windows OCR only')
def test_the_real_warm_reader_reads_exactly_what_a_cold_batch_reads(monkeypatch):
    monkeypatch.setattr(textocr, '_WARM', textocr._WarmReader())
    try:
        crops = [_text_crop('CLAIM 6'), _text_crop('Ends in 6d'), np.zeros((40, 80, 3), np.uint8)]
        cold = textocr.read_texts(crops, scales=(2,), timeout=60, raise_on_error=True)
        warm = [textocr.read_texts(crops, scales=(2,), timeout=60, raise_on_error=True, urgent=True) for _ in range(2)]
        assert cold[0] == 'CLAIM 6' and cold[2] == ''
        assert warm == [cold, cold]
        assert textocr._WARM._proc.poll() is None
    finally:
        textocr._WARM.abandon()


CHILD = """
import os, sys
sys.path.insert(0, {backend!r})
import numpy as np
from vision import textocr
textocr.read_texts([np.zeros((20, 40, 3), np.uint8)], scales=(1,), timeout=60, raise_on_error=True, urgent=True)
print(textocr._WARM._proc.pid, flush=True)
os._exit(0)                                    # no cleanup: the parent simply goes away
"""


def _alive(pid):
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000 | 0x00100000, False, pid)   # QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == 0x102              # WAIT_TIMEOUT: still running
    finally:
        kernel32.CloseHandle(handle)


@pytest.mark.skipif(os.name != 'nt' or not textocr.available(), reason='Windows OCR only')
def test_the_warm_reader_exits_by_itself_when_its_parent_process_ends():
    backend = str(textocr.settings.ROOT)
    child = subprocess.run([sys.executable, '-c', CHILD.format(backend=backend)], capture_output=True, text=True,
                           timeout=120, cwd=backend, creationflags=textocr.settings.NO_WINDOW)
    pid = int(child.stdout.strip().splitlines()[-1])
    ends = time.monotonic() + 20
    while _alive(pid) and time.monotonic() < ends:
        time.sleep(.2)
    assert not _alive(pid)


# A reader that ignores stdin and never replies: a WinRT call that never returns.
HANG = "param([string]$img, [switch]$Serve)\r\nwhile ($true) { Start-Sleep -Milliseconds 200 }\r\n"


@pytest.fixture
def hung(monkeypatch, tmp_path, real_kernel):
    """Real hung `-Serve` PowerShell processes, stopped through the real kernel; every one is recorded and stopped at teardown."""
    from runtime import process_handle
    script = tmp_path / 'hang.ps1'
    script.write_text(HANG, encoding='utf-8')
    monkeypatch.setattr(textocr, 'WINOCR', script)
    monkeypatch.setattr(textocr, '_WARM', textocr._WarmReader())
    monkeypatch.setattr(textocr, 'ABANDON_GRACE_SEC', .3, raising=False)   # raising=False: also runs against a build without it
    w = type('H', (), {})()
    w.spawned, w.stopped = [], []
    real_spawn, real_stop = textocr.settings.spawn_hidden, process_handle.terminate_owned
    monkeypatch.setattr(textocr.settings, 'spawn_hidden', lambda *a, **k: w.spawned.append(real_spawn(*a, **k)) or w.spawned[-1])
    monkeypatch.setattr(process_handle, 'terminate_owned', lambda pid, **k: w.stopped.append(pid) or real_stop(pid, **k))
    w.alive = lambda: [p for p in w.spawned if p.poll() is None]
    try:
        yield w
    finally:
        textocr._WARM.abandon()
        for proc in w.spawned:
            if proc.poll() is None:
                real_stop(proc.pid, timeout=5)
        assert w.alive() == []


def _timeout_read():
    with pytest.raises(subprocess.TimeoutExpired):
        textocr.read_texts([np.zeros((10, 20, 3), np.uint8)], scales=(2,), timeout=.6, raise_on_error=True, urgent=True)


@pytest.mark.skipif(os.name != 'nt', reason='Windows processes')
def test_a_hung_reader_that_ignores_eof_is_reclaimed_before_a_new_one_starts(hung):
    _timeout_read()
    [first] = hung.spawned
    time.sleep(.4)                                         # past the grace; it never saw EOF
    assert first.poll() is None
    _timeout_read()                                        # reclaims it, then starts a fresh reader
    assert hung.stopped == [first.pid] and first.poll() is not None
    assert len(hung.spawned) == 2


@pytest.mark.skipif(os.name != 'nt', reason='Windows processes')
def test_repeated_timeouts_never_leave_more_than_one_extra_reader(hung):
    for _ in range(6):
        _timeout_read()
        assert len(hung.alive()) <= 1                      # after a read nothing is current; at most one abandoned
        time.sleep(.15)
    assert len(textocr._WARM._abandoned) <= 1


@pytest.mark.skipif(os.name != 'nt', reason='Windows processes')
def test_an_unreclaimable_reader_sends_the_next_read_cold_and_starts_no_other(hung, monkeypatch):
    from runtime import process_handle
    _timeout_read()
    monkeypatch.setattr(process_handle, 'terminate_owned', lambda pid, **k: process_handle.Stop('unverified'))
    cold = []

    def run(args, **kwargs):
        cold.append(kwargs['timeout'])
        return SimpleNamespace(args=args, returncode=0, stderr=b'', stdout=b'TP_OCR_IMAGE\t0\n4\t6\tCLAIM 6')
    monkeypatch.setattr(textocr.settings, 'run_hidden', run)
    time.sleep(.4)
    for _ in range(3):
        assert textocr.read_texts([np.zeros((10, 20, 3), np.uint8)], scales=(2,), timeout=2,
                                  raise_on_error=True, urgent=True) == ['CLAIM 6']
    assert len(hung.spawned) == 1 and len(cold) == 3 and all(0 < t <= 2 for t in cold)


def test_a_busy_warm_reader_lock_times_out_at_the_callers_deadline(warm):
    import threading
    outcome = []

    def read():
        try:
            textocr.read_texts([np.zeros((10, 20, 3), np.uint8)], scales=(2,), timeout=.2, raise_on_error=True, urgent=True)
            outcome.append('read')
        except subprocess.TimeoutExpired:
            outcome.append('timeout')
    textocr._WARM._lock.acquire()
    try:
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(2)
        assert outcome == ['timeout'] and warm.spawned == []
    finally:
        textocr._WARM._lock.release()
