"""The launcher runs windowless with a notification-area icon (2026-09-15).

pythonw gives the process no stdout/stderr and no console, so three things
have to hold: output goes to a file instead of raising, a second launch does
not start a second dashboard on a taken port, and a machine with no usable
tray still serves. The icon is drawn, never loaded from a file (the
image-free release boundary)."""

import paths as test_paths
import importlib.util
import hashlib
import json
import shutil
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

ROOT = test_paths.REPO


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


def test_windowless_launch_redirects_inherited_streams(launcher, monkeypatch):
    monkeypatch.setenv("TOWER_PILOT_WINDOWLESS", "1")
    real_out, real_err = sys.stdout, sys.stderr
    stream = launcher.redirect_output()
    try:
        print("windowless startup")
    finally:
        sys.stdout, sys.stderr = real_out, real_err
        stream.close()
    assert "windowless startup" in launcher.LOG.read_text(encoding="utf-8")


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
    assert launcher.UI == launcher.URL + "/ui/app/"

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


def test_open_ui_opens_the_new_emulators_page(launcher, monkeypatch):
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    launcher.open_ui()
    assert opened == [launcher.URL + "/ui/app/#emulators"]


def test_first_launch_serves_opens_the_browser_and_hands_the_main_thread_to_the_tray(
        launcher, monkeypatch):
    calls = []
    finish = threading.Event()
    monkeypatch.setattr(launcher, "serving", lambda timeout=1.0: False)

    def serve():
        calls.append("serve")
        finish.wait(5)

    monkeypatch.setattr(launcher, "serve", serve)
    monkeypatch.setattr(launcher, "open_when_ready",
                        lambda **kw: calls.append("browser") or True)
    threads = []
    real_thread = launcher.threading.Thread

    def record(target, daemon=None):
        t = real_thread(target=target, daemon=daemon)
        threads.append(t)
        return t
    monkeypatch.setattr(launcher.threading, "Thread", record)
    try:
        assert launcher.main(tray=lambda: calls.append("tray") or 0) == 0
    finally:
        finish.set()
    for t in threads:
        t.join(timeout=5)
    assert all(t.daemon for t in threads)          # never outlive the icon
    assert sorted(calls) == ["browser", "serve", "tray"]


@pytest.mark.parametrize("error", [RuntimeError("missing dependency"), SystemExit(1)])
def test_dashboard_startup_crash_reports_failure_without_leaving_a_tray(
        launcher, monkeypatch, error, capsys):
    monkeypatch.setattr(launcher, "serving", lambda timeout=1.0: False)

    def crash():
        raise error

    monkeypatch.setattr(launcher, "serve", crash)
    reported = []
    monkeypatch.setattr(launcher, "report_startup_failure", reported.append)
    assert launcher.main(tray=lambda: pytest.fail("tray after startup failure")) == 1
    assert reported == ["The dashboard stopped before it was ready."]
    assert type(error).__name__ in capsys.readouterr().err


def test_dashboard_startup_timeout_reports_failure(launcher, monkeypatch):
    finish = threading.Event()
    monkeypatch.setattr(launcher, "serving", lambda timeout=1.0: False)
    monkeypatch.setattr(launcher, "serve", lambda: finish.wait(5))
    monkeypatch.setattr(launcher, "open_when_ready", lambda **kw: False)
    reported = []
    monkeypatch.setattr(launcher, "report_startup_failure", reported.append)
    try:
        assert launcher.main(tray=lambda: pytest.fail("tray after timeout")) == 1
    finally:
        finish.set()
    assert reported == ["The dashboard did not become ready before startup timed out."]


def test_startup_failure_points_to_logs_and_shows_a_windows_dialog(
        launcher, monkeypatch, capsys):
    import ctypes
    shown = []
    user32 = types.SimpleNamespace(MessageBoxW=lambda *args: shown.append(args))
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(user32=user32), raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    launcher.report_startup_failure("Dashboard crashed")
    assert "Dashboard crashed" in capsys.readouterr().err
    assert str(launcher.LOG.parent) in shown[0][1]
    assert shown[0][2] == launcher.TITLE


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


def test_readiness_stops_when_the_dashboard_exits(launcher, monkeypatch):
    stopped = threading.Event()
    stopped.set()
    monkeypatch.setattr(launcher, "serving", lambda: pytest.fail("probe after exit"))
    monkeypatch.setattr(launcher, "open_ui", lambda: pytest.fail("browser after exit"))
    assert launcher.open_when_ready(stopped=stopped) is False


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


def test_the_icon_goes_when_the_dashboard_stops(launcher, monkeypatch):
    stopped = threading.Event()

    class FakeIcon:
        def __init__(self):
            self.ended = threading.Event()

        def stop(self):
            self.ended.set()

        def run(self):
            stopped.set()                         # the dashboard thread ends while the icon is up
            assert self.ended.wait(5)
    monkeypatch.setattr(launcher, "build_icon", lambda on_quit: FakeIcon())
    assert launcher.run_tray(stopped) == 0


def test_no_tray_available_ends_with_the_dashboard(launcher, monkeypatch):
    def no_tray(on_quit):
        raise ImportError("no pystray on this machine")
    monkeypatch.setattr(launcher, "build_icon", no_tray)
    stopped = threading.Event()
    stopped.set()
    assert launcher.run_tray(stopped) == 0


# --------------------------------------------------------- background
@pytest.fixture
def background(launcher, monkeypatch):
    """main(background=True) logs to the file even with a console; put the streams back afterwards."""
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    yield launcher
    if sys.stdout is not None and getattr(sys.stdout, "name", "") == str(launcher.LOG):
        sys.stdout.close()


def test_a_background_repetition_over_a_running_dashboard_does_nothing(background, monkeypatch):
    monkeypatch.setattr(background, "serving", lambda timeout=1.0: True)
    monkeypatch.setattr(background, "open_ui", lambda: pytest.fail("no browser from the restart task"))
    monkeypatch.setattr(background, "serve", lambda: pytest.fail("no second dashboard"))
    assert background.main(tray=lambda: pytest.fail("no second icon"), background=True) == 0
    assert "launcher start" in background.LOG.read_text(encoding="utf-8")


def test_a_background_start_serves_and_shows_the_icon_without_a_browser(background, monkeypatch):
    finish = threading.Event()
    monkeypatch.setattr(background, "serving", lambda timeout=1.0: False)
    monkeypatch.setattr(background, "serve", lambda: finish.wait(5))
    asked = []
    monkeypatch.setattr(background, "open_when_ready", lambda **kw: asked.append(kw["show"]) or True)
    import logging
    monkeypatch.setattr(logging.getLogger("werkzeug"), "level", logging.NOTSET)
    try:
        assert background.main(tray=lambda: 0, background=True) == 0
    finally:
        finish.set()
    assert asked == [False]
    assert logging.getLogger("werkzeug").level == logging.WARNING     # no log line per request


def test_a_refused_background_start_is_logged_without_a_dialog(background, monkeypatch):
    monkeypatch.setattr(background, "serving", lambda timeout=1.0: False)

    def refused():
        raise SystemExit(3)                       # dashboard.serve's landing-hold refusal
    monkeypatch.setattr(background, "serve", refused)
    monkeypatch.setattr(background, "report_startup_failure",
                        lambda message: pytest.fail("a dialog every repetition"))
    assert background.main(tray=lambda: pytest.fail("no icon"), background=True) == 1
    assert "The dashboard stopped before it was ready." in background.LOG.read_text(encoding="utf-8")


def test_open_when_ready_can_wait_without_opening(launcher, monkeypatch):
    monkeypatch.setattr(launcher, "serving", lambda *a, **k: True)
    monkeypatch.setattr(launcher, "open_ui", lambda: pytest.fail("browser while hidden"))
    assert launcher.open_when_ready(show=False) is True


def test_the_restart_task_runs_the_tray_launcher_in_the_background():
    script = (ROOT / "tools/register_dashboard_task.ps1").read_text(encoding="utf-8")
    assert "'tools\\portable_launcher.py'" in script
    assert "'--headless \"{0}\" \"{1}\" --background' -f $python, $launcher" in script


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
    assert ".CreateNoWindow = $true" in ps1 and ".UseShellExecute = $false" in ps1
    assert "Keep this window open" not in ps1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console inheritance")
def test_source_launcher_handles_spaces_logs_and_windowless_redirector_children(tmp_path):
    """Exercise the real PS1 and venv redirector without starting the dashboard."""
    venv = ROOT / ".venv"
    if not (venv / "Scripts/python.exe").is_file():
        pytest.skip("source checkout has no Windows venv")
    import ctypes

    def terminal_windows():
        windows = set()
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        @callback_type
        def collect(hwnd, unused):
            name = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetClassNameW(ctypes.c_void_p(hwnd), name, len(name))
            if name.value in {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}:
                windows.add(hwnd)
            return True

        ctypes.windll.user32.EnumWindows(collect, None)
        return windows

    stage = tmp_path / "Tower Pilot with spaces"
    (stage / "tools").mkdir(parents=True)
    (stage / ".venv/Scripts").mkdir(parents=True)
    shutil.copyfile(ROOT / "Start-TowerPilot.ps1", stage / "Start-TowerPilot.ps1")
    shutil.copyfile(venv / "Scripts/python.exe", stage / ".venv/Scripts/python.exe")
    shutil.copyfile(venv / "pyvenv.cfg", stage / ".venv/pyvenv.cfg")
    (stage / "requirements.txt").write_bytes(b"")
    (stage / ".venv/requirements.sha256").write_text(hashlib.sha256(b"").hexdigest())
    # The process exits itself after inspection; no existing Tower Pilot process
    # is started, stopped, or inspected by this isolated launcher smoke test.
    (stage / "tools/portable_launcher.py").write_text(
        "import ctypes, json, os, pathlib, sys, time\n"
        "kernel = ctypes.windll.kernel32\n"
        "kernel.GetConsoleWindow.restype = ctypes.c_void_p\n"
        "window = kernel.GetConsoleWindow()\n"
        "sys.stdout = sys.stderr = open('backend-log.txt', 'a', buffering=1)\n"
        "print('launcher stdout', flush=True)\n"
        "print('launcher stderr', file=sys.stderr, flush=True)\n"
        "pathlib.Path('result.json').write_text(json.dumps({"
        "'pid': os.getpid(), 'cwd': os.getcwd(), 'argv': sys.argv, "
        "'console': window, 'windowless': os.environ.get('TOWER_PILOT_WINDOWLESS')}))\n"
        "time.sleep(2)\n", encoding="utf-8")
    before = terminal_windows()
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(stage / "Start-TowerPilot.ps1")], capture_output=True, text=True,
        timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads((stage / "result.json").read_text())
    assert Path(result["cwd"]) == stage
    assert result["argv"] == [str(stage / "tools/portable_launcher.py")]
    assert result["console"] is None
    assert result["windowless"] == "1"
    assert terminal_windows() - before == set()
    log = (stage / "backend-log.txt").read_text()
    assert "launcher stdout" in log and "launcher stderr" in log
