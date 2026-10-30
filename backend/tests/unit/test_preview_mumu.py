"""Offline native-preview mapping, ABI conversion, and helper containment tests."""

import ctypes
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import psutil
import pytest

from device.preview import mumu, mumu_worker
from device.preview.common import PreviewCleanupError, PreviewError, PreviewUnavailable


@pytest.fixture
def installation(tmp_path):
    (tmp_path / "nx_main" / "sdk").mkdir(parents=True)
    (tmp_path / "nx_main" / "sdk" / "external_renderer_ipc.dll").touch()
    (tmp_path / "vms").mkdir()
    return tmp_path


def _vm(root: Path, index: int, port: int, extra: int | None = None) -> Path:
    vm = root / "vms" / f"MuMuPlayerGlobal-15.0-{index}"
    (vm / "configs").mkdir(parents=True)
    (vm / "configs" / "vm_config.json").write_text(json.dumps({
        "vm": {"nat": {"port_forward": {"adb": {"host_port": str(port)}}}},
    }))
    if extra is not None:
        (vm / f"{vm.name}.nemu").write_text(
            '<VirtualBox xmlns="urn:test"><Forwarding name="ADB_PORT_EX" '
            f'hostip="127.0.0.1" hostport="{extra}" guestport="5555" proto="1"/></VirtualBox>'
        )
    return vm


@pytest.mark.parametrize("options", [{}, {"index": None}, {"index": -1}, {"index": 3}])
def test_discovery_binds_actual_port_and_index(installation, options):
    _vm(installation, 0, 23000)
    _vm(installation, 3, 25000)
    assert mumu.discover_target("127.0.0.1:25000", str(installation / "nx_main" / "adb.exe"), options) == {
        "install_dir": str(installation.resolve()), "index": 3,
    }


def test_discovery_accepts_only_explicit_xml_alias(installation):
    _vm(installation, 7, 24000, extra=5555)
    options = {"install_dir": str(installation), "index": 7}
    assert mumu.discover_target("localhost:5555", "other-adb", options)["index"] == 7
    with pytest.raises(PreviewUnavailable, match="verified loopback"):
        mumu.discover_target("emulator-5554", "other-adb", options)


def test_discovery_rejects_explicit_wrong_index(installation):
    _vm(installation, 2, 25000)
    with pytest.raises(PreviewUnavailable, match="does not match"):
        mumu.discover_target("127.0.0.1:25000", "adb", {"install_dir": str(installation), "index": 0})


def test_discovery_never_guesses_formula_or_first_instance(installation):
    _vm(installation, 0, 23000)
    with pytest.raises(PreviewUnavailable, match="uniquely bind"):
        mumu.discover_target("127.0.0.1:16384", "adb", {"install_dir": str(installation)})


def test_discovery_rejects_ambiguous_ports(installation):
    _vm(installation, 0, 23000)
    _vm(installation, 3, 23000)
    with pytest.raises(PreviewUnavailable, match="uniquely bind"):
        mumu.discover_target("127.0.0.1:23000", "adb", {"install_dir": str(installation), "index": 3})


def test_discovery_rejects_broken_mapping(installation):
    _vm(installation, 0, 23000)
    broken = _vm(installation, 2, 24000)
    (broken / "configs" / "vm_config.json").write_text("invalid")
    with pytest.raises(PreviewUnavailable, match="unreadable mappings"):
        mumu.discover_target("127.0.0.1:23000", "adb", {"install_dir": str(installation)})


class _Function:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


@pytest.fixture
def dll(monkeypatch, installation):
    class DLL:
        def __init__(self):
            self.disconnects = []
            self.displays = []
            self.result = 0
            self.size = (2, 2)
            self.nemu_connect = _Function(lambda root, index: 11)
            self.nemu_disconnect = _Function(self.disconnects.append)
            self.nemu_get_display_id = _Function(lambda *args: self.displays.append(args) or 5)
            self.nemu_capture_display = _Function(self.capture)

        def capture(self, handle, display, count, width, height, buffer):
            ctypes.cast(width, ctypes.POINTER(ctypes.c_int))[0] = self.size[0]
            ctypes.cast(height, ctypes.POINTER(ctypes.c_int))[0] = self.size[1]
            if count and self.result == 0:
                pixels = np.array([[[255, 0, 0, 255], [0, 255, 0, 255]],
                                   [[0, 0, 255, 255], [10, 20, 30, 255]]], dtype=np.uint8)
                ctypes.memmove(buffer, pixels.ctypes.data, pixels.nbytes)
            return self.result

    fake = DLL()
    monkeypatch.setattr(mumu_worker.ctypes, "CDLL", lambda path: fake)
    return fake


def test_native_rgba_bottom_up_converts_to_upright_bgr(installation, dll):
    native = mumu_worker.NativeCapture(str(installation), 4, "test.package", None)
    buffer = native.buffer
    expected = np.array([[[255, 0, 0], [30, 20, 10]], [[0, 0, 255], [0, 255, 0]]], dtype=np.uint8)
    np.testing.assert_array_equal(native.read(), expected)
    np.testing.assert_array_equal(native.read(), expected)
    assert native.buffer is buffer
    assert dll.displays == [(11, b"test.package", 0)]
    native.close()
    native.close()
    assert dll.disconnects == [11]


def test_native_explicit_zero_display_and_capture_failure(installation, dll):
    native = mumu_worker.NativeCapture(str(installation), 4, "test.package", 0)
    assert native.display_id == 0
    assert dll.displays == []
    dll.result = 17
    with pytest.raises(RuntimeError, match="returned 17"):
        native.read()
    native.close()


def test_native_rejects_dimension_change(installation, dll):
    native = mumu_worker.NativeCapture(str(installation), 4, "test.package", None)
    dll.size = (1, 2)
    with pytest.raises(RuntimeError, match="resolution changed"):
        native.read()
    native.close()


def test_native_initialization_failure_releases_handle(installation, dll):
    dll.size = (10000, 2)
    with pytest.raises(PreviewError, match="invalid capture dimensions"):
        mumu_worker.NativeCapture(str(installation), 4, "test.package", None)
    assert dll.disconnects == [11]


_HELPER = r'''
import json, socket, struct, sys, time
port, token, mode = sys.argv[1:]
s = socket.create_connection(('127.0.0.1', int(port)))
def message(data):
    raw = json.dumps(data).encode()
    s.sendall(struct.pack('!I', len(raw)) + raw)
if mode == 'init_hang':
    time.sleep(30)
message({'token': token, 'width': 2, 'height': 2})
while s.recv(1):
    if mode == 'read_hang':
        time.sleep(30)
    if mode == 'crash':
        sys.exit(9)
    if mode == 'error':
        s.sendall(b'E')
        message({'error': 'native failure'})
        break
    s.sendall(b'F' + bytes(range(5 if mode == 'partial' else 12)))
    if mode == 'partial':
        time.sleep(30)
time.sleep(30)
'''


@pytest.fixture
def helper(monkeypatch, real_kernel):
    """Helper processes this test starts for itself, stopped through the REAL kernel (runtime.process_handle)."""
    original = subprocess.Popen
    processes = []

    def launch(mode):
        def popen(command, **kwargs):
            process = original([sys.executable, "-c", _HELPER, command[3], command[4], mode], **kwargs)
            processes.append(process)
            return process
        monkeypatch.setattr(mumu.subprocess, "Popen", popen)
        return processes

    yield launch
    for process in processes:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def test_helper_returns_complete_independently_owned_frames_and_bounded_close(installation, helper):
    processes = helper("success")
    preview = mumu.MuMuPreview(str(installation), 0, "test.package")
    first, second = preview.read(), preview.read()
    descendants = psutil.Process(processes[0].pid).children(recursive=True)
    np.testing.assert_array_equal(first.ravel(), np.arange(12, dtype=np.uint8))
    second[:] = 0
    assert first.sum() == 66
    started = time.monotonic()
    preview.close()
    preview.close()
    assert time.monotonic() - started < 1.5
    assert processes[0].poll() is not None
    assert all(not child.is_running() for child in descendants)


@pytest.mark.parametrize("mode", ["read_hang", "partial", "crash", "error"])
def test_helper_read_failure_is_bounded_and_reaps_child(installation, helper, mode):
    processes = helper(mode)
    preview = mumu.MuMuPreview(str(installation), 0, "test.package")
    started = time.monotonic()
    with pytest.raises(PreviewError):
        preview.read(timeout=0.1)
    assert time.monotonic() - started < 1.5
    assert processes[0].poll() is not None
    with pytest.raises(PreviewError, match="closed"):
        preview.read()


def test_helper_initialization_hang_is_bounded(installation, helper, monkeypatch):
    processes = helper("init_hang")
    monkeypatch.setattr(mumu, "_START_TIMEOUT", 0.5)
    monkeypatch.setattr(mumu, "_CLOSE_TIMEOUT", 2.0)   # the real 0.25 s kill bound is shorter than a loaded runner needs to reap a child
    started = time.monotonic()
    with pytest.raises(PreviewUnavailable):
        mumu.MuMuPreview(str(installation), 0, "test.package")
    assert time.monotonic() - started < 5
    assert processes[0].poll() is not None


def test_close_interrupts_an_outstanding_read(installation, helper, monkeypatch):
    processes = helper("read_hang")
    preview = mumu.MuMuPreview(str(installation), 0, "test.package")
    errors = []
    entered = threading.Event()
    receive = mumu._receive

    def receiving(*args):
        entered.set()
        return receive(*args)

    monkeypatch.setattr(mumu, "_receive", receiving)
    monkeypatch.setattr(mumu, "_CLOSE_TIMEOUT", 2.0)   # see test_helper_initialization_hang_is_bounded
    def read():
        try:
            preview.read(timeout=30)   # far longer than the close-and-join budget below: only close() can end it in time
        except PreviewError as exc:
            errors.append(exc)
    thread = threading.Thread(target=read)
    thread.start()
    assert entered.wait(timeout=5)
    with pytest.raises(PreviewError, match="outstanding frame request"):
        preview.read(timeout=0.05)
    closing = time.monotonic()
    preview.close()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert time.monotonic() - closing < 5
    assert len(errors) == 1
    assert processes[0].poll() is not None


def test_partial_ipc_frame_uses_a_single_absolute_deadline():
    left, right = socket.socketpair()
    try:
        right.sendall(b"abc")
        with pytest.raises(TimeoutError):
            mumu._receive(left, memoryview(bytearray(4)), time.monotonic() + 0.05)
    finally:
        left.close()
        right.close()


def _unstarted_preview():
    preview = mumu.MuMuPreview.__new__(mumu.MuMuPreview)
    preview._closed = threading.Event()
    preview._lock = threading.Lock()
    preview._socket = None
    preview._process = None
    return preview


def test_close_reports_surviving_owned_helpers_and_allows_cleanup_retry():
    from runtime import process_handle
    kernel_os = process_handle.kernel().os
    kernel_os.start(123, 1.0)
    preview = _unstarted_preview()
    launcher = SimpleNamespace(pid=123, wait=Mock(return_value=0), kill=Mock())
    preview._process = launcher
    kernel_os.lingering = True                       # the helper does not exit when terminated
    with pytest.raises(PreviewError, match=r"cleanup failed.*did not exit after kill: \[123\]"):
        preview.close()
    assert kernel_os.kills == [(123, 1.0)] and preview._process is launcher
    kernel_os.lingering = False
    preview.close()                                  # the retry signals it again, through a fresh handle
    assert preview._process is None and kernel_os.kills == [(123, 1.0)] * 2
    assert process_handle.kernel().open_handles() == 0


def test_close_reports_unverifiable_cleanup_after_best_effort_kill():
    from runtime import process_handle
    kernel_os = process_handle.kernel().os
    kernel_os.start(123, 1.0)
    kernel_os.denied.add(123)                        # the kernel will not say who owns the helper
    preview = _unstarted_preview()
    launcher = SimpleNamespace(pid=123, wait=Mock(return_value=0), kill=Mock())
    preview._process = launcher
    with pytest.raises(PreviewError, match="cannot verify helper process cleanup"):
        preview.close()
    launcher.kill.assert_called_once_with()
    launcher.wait.assert_called_once_with(timeout=mumu._CLOSE_TIMEOUT)


def test_read_preserves_original_error_when_cleanup_also_fails(monkeypatch):
    preview = _unstarted_preview()
    preview._socket = SimpleNamespace(settimeout=Mock(), sendall=Mock(side_effect=OSError("frame failed")))
    monkeypatch.setattr(preview, "close", Mock(side_effect=PreviewError("cleanup failed")))
    with pytest.raises(PreviewError, match="frame failed; cleanup failed") as failure:
        preview.read()
    assert isinstance(failure.value.__cause__, OSError)
    assert str(failure.value.__cause__) == "frame failed"


def test_constructor_preserves_original_error_when_cleanup_also_fails(installation, helper, monkeypatch):
    processes = helper("success")
    sources = []

    def failed_message(*args):
        raise PreviewError("initialization failed")

    original_close = mumu.MuMuPreview.close

    def failed_cleanup(self):
        sources.append(self)
        raise PreviewError("cleanup failed")

    monkeypatch.setattr(mumu, "_message", failed_message)
    monkeypatch.setattr(mumu.MuMuPreview, "close", failed_cleanup)
    with pytest.raises(PreviewCleanupError, match="initialization failed; cleanup failed") as failure:
        mumu.MuMuPreview(str(installation), 0, "test.package")
    assert isinstance(failure.value.__cause__, PreviewError)
    assert str(failure.value.__cause__) == "initialization failed"
    assert failure.value.source is sources[0]
    assert failure.value.source._process is processes[0]
    monkeypatch.setattr(mumu.MuMuPreview, "close", original_close)
    failure.value.source.close()
    assert processes[0].poll() is not None
