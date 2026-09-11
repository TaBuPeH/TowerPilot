"""Portable entry point. The bundled interpreter is also used by all workers."""
import os
import runpy
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def open_when_ready():
    url = 'http://127.0.0.1:' + os.environ.get('TOWER_PILOT_PORT', '8620')
    for _ in range(60):
        try:
            with urllib.request.urlopen(url + '/ui/index.html', timeout=1):
                pass
            webbrowser.open(url + '/ui/index.html#setup')
            return
        except OSError:
            time.sleep(.5)


if __name__ == '__main__':
    os.chdir(ROOT)
    print('Tower Pilot is running. Keep this window open; use Stop now in the dashboard before closing it.')
    threading.Thread(target=open_when_ready, daemon=True).start()
    runpy.run_path(str(ROOT / 'frontend/dashboard.py'), run_name='__main__')
