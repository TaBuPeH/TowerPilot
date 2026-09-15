"""Portable entry point: the dashboard behind a notification-area icon.

Tower Pilot is a local web app with no window of its own, so the launcher
used to be a console window the user had to leave open - close it by
accident and the dashboard went with it (2026-09-15: "can this have an icon
in the taskbar and not run in the console?").

Now `Start Tower Pilot.cmd` starts pythonw (no console at all) and this
module puts an icon in the notification area: click it to open the
dashboard, right-click to quit. The pieces:

* `redirect_output` - pythonw gives a process no stdout/stderr at all, so
  the first print (or werkzeug's first log line) would raise. Both go to
  backend/logs/launcher.log instead, which is also where a start-up failure
  is readable after the fact.
* `serving` - a second double-click finds the port already answering and
  just opens the browser rather than starting a second dashboard on a port
  that is taken.
* the dashboard runs in a daemon thread; the icon owns the main thread
  (pystray needs it on Windows). Quitting stops the icon, the daemon thread
  goes with the process, and RUNNERS ARE LEFT ALONE - they are detached
  processes by design, and "Stop now" in the dashboard is what ends a run.
* no tray available (or pystray missing from a source install) is not a
  failure: the dashboard keeps serving and the launcher blocks, exactly
  like the old console did.

The icon is DRAWN HERE with Pillow, never loaded from a file: no image ships
with this app (CLAUDE.md, the image-free release boundary).
"""
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
UI = URL + '/ui/index.html'
SETUP = UI + '#setup'
LOG = ROOT / 'backend' / 'logs' / 'launcher.log'
TITLE = 'Tower Pilot'
TOOLTIP = TITLE + ' - ' + URL


def redirect_output():
    """Send stdout/stderr to the launcher log when there is no console."""
    if sys.stdout is not None and sys.stderr is not None:
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


def open_when_ready(attempts=60, delay=.5):
    for _ in range(attempts):
        if serving():
            open_ui()
            return True
        time.sleep(delay)
    return False


def serve():
    os.chdir(ROOT)
    runpy.run_path(str(ROOT / 'frontend/dashboard.py'), run_name='__main__')


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


def run_tray():
    """The icon, or a plain block when this machine has no usable tray."""
    try:
        icon = build_icon(lambda icon, item: icon.stop())
    except Exception:                       # noqa: BLE001 - never fatal
        traceback.print_exc()
        print('No notification-area icon: serving without one. '
              'Open ' + UI + ' and close this app from Task Manager.')
        threading.Event().wait()
        return 0
    icon.run()
    return 0


def main(tray=None):
    redirect_output()
    if serving(.5):
        # Already running (a second double-click): show it, start nothing.
        print('A dashboard is already serving on ' + URL + '; opening it.')
        open_ui()
        return 0
    threading.Thread(target=serve, daemon=True).start()
    threading.Thread(target=open_when_ready, daemon=True).start()
    return (tray or run_tray)()


if __name__ == '__main__':
    sys.exit(main())
