"""Portable entry point: the dashboard behind a notification-area icon.

Tower Pilot is a local web app with no window of its own, so the launcher
used to be a console window the user had to leave open - close it by
accident and the dashboard went with it (2026-09-15: "can this have an icon
in the taskbar and not run in the console?").

The source launcher starts Python without a console, and the portable
build uses its bundled pythonw. This module puts an icon in the notification
area: click it to open the dashboard, right-click to quit. The pieces:

* `redirect_output` - pythonw gives a process no stdout/stderr at all, so
  the first print (or werkzeug's first log line) would raise. Both go to
  backend/logs/launcher.log instead, which is also where a start-up failure
  is readable after the fact.
* `serving` - a second double-click finds the port already answering and
  just opens the browser rather than starting a second dashboard on a port
  that is taken.
* startup waits for the dashboard to answer before opening the browser or
  showing the tray; a crash or timeout is logged and reported in a dialog.
* the dashboard runs in a daemon thread; the icon owns the main thread
  (pystray needs it on Windows). Quitting stops the icon, the daemon thread
  goes with the process, and RUNNERS ARE LEFT ALONE - they are detached
  processes by design, and "Stop now" in the dashboard is what ends a run.
* no tray available (or pystray missing from a source install) is not a
  failure: the dashboard keeps serving and the launcher blocks, exactly
  like the old console did.
* `--background` is the restart task's start (tools/register_dashboard_task.ps1):
  the same icon, but no browser, no dialog, and a dashboard already serving
  means exit quietly - the task repeats every few minutes and must never
  open a tab or a message box each time. Output always goes to the log
  (the task's console is a headless one nobody reads). When the dashboard
  stops, the icon goes with it, so the task sees a dead process and starts
  a new one rather than leaving an icon over nothing.

The icon is DRAWN HERE with Pillow, never loaded from a file: no image ships
with this app (CLAUDE.md, the image-free release boundary).
"""
import logging
import os
import runpy
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = os.environ.get('TOWER_PILOT_PORT', '8620')
URL = 'http://127.0.0.1:' + PORT
UI = URL + '/ui/app/'
SETUP = UI + '#emulators'
LOG = ROOT / 'backend' / 'logs' / 'launcher.log'
TITLE = 'Tower Pilot'
TOOLTIP = TITLE + ' - ' + URL


def redirect_output(force=False):
    """Log windowless launches, including any inherited parent streams."""
    if (not force and sys.stdout is not None and sys.stderr is not None
            and os.environ.get('TOWER_PILOT_WINDOWLESS') != '1'):
        return None
    LOG.parent.mkdir(parents=True, exist_ok=True)
    stream = open(LOG, 'a', encoding='utf-8', buffering=1)
    sys.stdout = sys.stderr = stream
    print('--- ' + time.strftime('%Y-%m-%d %H:%M:%S') + ' launcher start')
    return stream


def serving(timeout=1.0):
    """Is a dashboard already answering on this port?"""
    try:
        with urllib.request.urlopen(UI, timeout=timeout):
            return True
    except OSError:
        return False


def open_ui():
    webbrowser.open(SETUP)


def open_when_ready(attempts=60, delay=.5, stopped=None, show=True):
    for _ in range(attempts):
        if stopped is not None and stopped.is_set():
            return False
        if serving():
            if show:
                open_ui()
            return True
        if stopped is None:
            time.sleep(delay)
        else:
            stopped.wait(delay)
    return False


def report_startup_failure(message: str) -> None:
    """Make a hidden startup failure visible and point to its traceback."""
    message += ('\n\nSee the launcher logs in:\n' + str(LOG.parent)
                + '\n\nRetry Start Tower Pilot after correcting the error.')
    print(message, file=sys.stderr, flush=True)
    if sys.platform == 'win32':
        import ctypes
        try:
            ctypes.windll.user32.MessageBoxW(None, message, TITLE, 0x10)
        except OSError:
            traceback.print_exc()


def serve():
    os.chdir(ROOT)
    runpy.run_path(str(ROOT / 'backend/dashboard.py'), run_name='__main__')


def icon_image(size=64):
    """A drawn tower: three ascending bars on a dark rounded tile."""
    from PIL import Image, ImageDraw
    image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    pad = size // 16
    draw.rounded_rectangle((pad, pad, size - pad, size - pad),
                           radius=size // 6, fill=(20, 24, 48, 255))
    bar = size // 8
    base, top = int(size * .78), int(size * .22)
    for i in range(3):
        x = int(size * .26) + i * (bar + bar // 2)
        height = top + (2 - i) * (base - top) // 3 if i else top
        draw.rounded_rectangle((x, height, x + bar, base), radius=bar // 3,
                               fill=(64, 224, 208, 255))
    return image


def build_icon(on_quit):
    import pystray
    menu = pystray.Menu(
        pystray.MenuItem('Open ' + TITLE, lambda *_: open_ui(), default=True),
        pystray.MenuItem('Quit ' + TITLE, on_quit))
    return pystray.Icon('tower_pilot', icon_image(), TOOLTIP, menu)


def run_tray(stopped=None):
    """The icon, or a plain block when this machine has no usable tray.
    With `stopped` (the dashboard thread's end), either one ends with the dashboard."""
    try:
        icon = build_icon(lambda icon, item: icon.stop())
    except Exception:                       # noqa: BLE001 - never fatal
        traceback.print_exc()
        print('No notification-area icon: serving without one. '
              'Open ' + UI + ' and close this app from Task Manager.')
        (stopped or threading.Event()).wait()
        return 0
    if stopped is not None:
        def end_with_dashboard():
            stopped.wait()
            print('The dashboard stopped; removing the icon.')
            icon.stop()
        threading.Thread(target=end_with_dashboard, daemon=True).start()
    icon.run()
    return 0


def main(tray=None, background=False):
    redirect_output(force=background)
    if serving(.5):
        # Already running (a second double-click): show it, start nothing.
        # The restart task's repetitions land here every few minutes: nothing to show.
        if background:
            return 0
        print('A dashboard is already serving on ' + URL + '; opening it.')
        open_ui()
        return 0
    if background:
        # The log outlives the run: keep werkzeug's warnings and errors, not a line per request.
        logging.getLogger('werkzeug').setLevel(logging.WARNING)
    stopped = threading.Event()

    def run_dashboard():
        try:
            serve()
        except BaseException:  # Flask also uses SystemExit for a bind failure.
            traceback.print_exc()
        finally:
            stopped.set()

    threading.Thread(target=run_dashboard, daemon=True).start()
    if not open_when_ready(stopped=stopped, show=not background):
        message = ('The dashboard stopped before it was ready.' if stopped.is_set()
                   else 'The dashboard did not become ready before startup timed out.')
        if background:
            # A refused start (landing hold, port taken) repeats with the task: log it, no dialog.
            print(message, flush=True)
        else:
            report_startup_failure(message)
        return 1
    return tray() if tray is not None else run_tray(stopped)


if __name__ == '__main__':
    sys.exit(main(background='--background' in sys.argv[1:]))
