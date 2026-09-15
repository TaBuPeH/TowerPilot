"""The launcher runs windowless with a notification-area icon (2026-09-15).

pythonw gives the process no stdout/stderr and no console, so three things
have to hold: output goes to a file instead of raising, a second launch does
not start a second dashboard on a taken port, and a machine with no usable
tray still serves. The icon is drawn, never loaded from a file (the
image-free release boundary)."""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def launcher(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "portable_launcher", ROOT / "tools/portable_launcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "LOG", tmp_path / "logs" / "launcher.log")
    return module


# ------------------------------------------------------------- output
def test_output_goes_to_a_file_only_when_there_is_no_console(launcher, monkeypatch):
    real_out, real_err = sys.stdout, sys.stderr
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    stream = launcher.redirect_output()
    try:
        assert sys.stdout is stream and sys.stderr is stream
        print("hello from pythonw")
    finally:
        # restore by hand: monkeypatch.undo() here would also undo the
        # fixture's temp LOG path, and the assertions below read that file
        sys.stdout, sys.stderr = real_out, real_err
        stream.close()
    text = launcher.LOG.read_text(encoding="utf-8")
    assert "launcher start" in text and "hello from pythonw" in text


def test_a_console_session_is_left_alone(launcher):
    assert launcher.redirect_output() is None
    assert not launcher.LOG.exists()


# ------------------------------------------------------------ serving
def test_serving_reports_whether_the_port_answers(launcher, monkeypatch):
    class _Response:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    asked = []
    monkeypatch.setattr(launcher.urllib.request, "urlopen",
                        lambda url, timeout=None: asked.append(url) or _Response())
    assert launcher.serving() is True
    assert asked == [launcher.UI]

    def refused(url, timeout=None):
        raise OSError("connection refused")
    monkeypatch.setattr(launcher.urllib.request, "urlopen", refused)
    assert launcher.serving() is False


def test_a_second_launch_opens_the_running_dashboard_and_starts_nothing(launcher, monkeypatch):
    opened, served = [], []
    monkeypatch.setattr(launcher, "serving", lambda timeout=1.0: True)
    monkeypatch.setattr(launcher, "open_ui", lambda: opened.append(launcher.SETUP))
    monkeypatch.setattr(launcher, "serve", lambda: served.append("started"))
    assert launcher.main(tray=lambda: pytest.fail("no tray on a second launch")) == 0
    assert opened == [launcher.SETUP] and served == []


def test_first_launch_serves_opens_the_browser_and_hands_the_main_thread_to_the_tray(
        launcher, monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, "serving", lambda timeout=1.0: False)
    monkeypatch.setattr(launcher, "serve", lambda: calls.append("serve"))
    monkeypatch.setattr(launcher, "open_when_ready", lambda: calls.append("browser"))
    threads = []
    real_thread = launcher.threading.Thread

    def record(target, daemon=None):
        t = real_thread(target=target, daemon=daemon)
        threads.append(t)
        return t
    monkeypatch.setattr(launcher.threading, "Thread", record)
    assert launcher.main(tray=lambda: calls.append("tray") or 0) == 0
    for t in threads:
        t.join(timeout=5)
    assert all(t.daemon for t in threads)          # never outlive the icon
    assert sorted(calls) == ["browser", "serve", "tray"]


def test_open_when_ready_waits_for_the_port(launcher, monkeypatch):
    answers = [False, False, True]
    monkeypatch.setattr(launcher, "serving", lambda *a, **k: answers.pop(0))
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)
    opened = []
    monkeypatch.setattr(launcher, "open_ui", lambda: opened.append(1))
    assert launcher.open_when_ready() is True and opened == [1]
    monkeypatch.setattr(launcher, "serving", lambda *a, **k: False)
    assert launcher.open_when_ready(attempts=3) is False
    assert opened == [1]


# --------------------------------------------------------------- tray
def test_the_icon_is_drawn_not_loaded(launcher):
    PIL = pytest.importorskip("PIL")
    image = launcher.icon_image()
    assert image.size == (64, 64) and image.mode == "RGBA"
    assert len(image.getcolors(maxcolors=2 ** 16)) > 1     # not a blank square


def test_the_menu_opens_the_dashboard_by_default_and_can_quit(launcher, monkeypatch):
    pytest.importorskip("PIL")
    made = {}

    class FakeItem:
        def __init__(self, text, action, default=False):
            made[text] = action
            self.default = default

    class FakeIcon:
        def __init__(self, name, image, tooltip, menu):
            made["tooltip"] = tooltip
            made["image"] = image
            self.stopped = False

        def stop(self):
            self.stopped = True
    fake = types.ModuleType("pystray")
    fake.Menu = lambda *items: items
    fake.MenuItem = FakeItem
    fake.Icon = FakeIcon
    monkeypatch.setitem(sys.modules, "pystray", fake)
    opened = []
    monkeypatch.setattr(launcher, "open_ui", lambda: opened.append(1))
    quit_calls = []
    icon = launcher.build_icon(lambda icon, item: quit_calls.append(icon))
    assert isinstance(icon, FakeIcon)
    assert launcher.URL in made["tooltip"]
    made["Open Tower Pilot"]()
    assert opened == [1]
    made["Quit Tower Pilot"](icon, None)
    assert quit_calls == [icon]


def test_no_tray_available_keeps_serving_instead_of_dying(launcher, monkeypatch):
    def no_tray(on_quit):
        raise ImportError("no pystray on this machine")
    monkeypatch.setattr(launcher, "build_icon", no_tray)
    waited = []

    class FakeEvent:
        def wait(self, timeout=None):
            waited.append(True)
    monkeypatch.setattr(launcher.threading, "Event", FakeEvent)
    assert launcher.run_tray() == 0
    assert waited == [True]                       # blocked, did not return


# ------------------------------------------------------------ packaging
def test_the_portable_launcher_starts_pythonw_with_no_window_to_keep_open():
    build = (ROOT / "tools/build_portable.py").read_text(encoding="utf-8")
    start = build[build.index("Start Tower Pilot.cmd"):build.index("START HERE.txt")]
    assert "pythonw.exe" in start and "python.exe\\\\\"" not in start
    assert "start \"\"" in start
    text = (ROOT / "tools/build_portable.py").read_text(encoding="utf-8")
    assert "Keep the launcher window open" not in text
    assert "notification area" in text
    ps1 = (ROOT / "Start-TowerPilot.ps1").read_text(encoding="utf-8")
    assert "pythonw.exe" in ps1 and "Start-Process" in ps1
    assert "Keep this window open" not in ps1
