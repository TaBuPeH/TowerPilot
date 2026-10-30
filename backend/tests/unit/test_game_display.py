"""The dashboard's own captures ask for the GAME display on MuMu.

MuMu runs the launcher on its default display and the game on a secondary
mumuscreen whose id changes on every restart; adopt records only serial +
adb. Until 2026-09-08 the preview, the live stream and the screen check read
the default display: a text warning ahead of the launcher's pixels, no PNG,
and a resolution of "[War" x "ning". They now derive the display like
capture.refresh_display does and strip the warning.
"""

import paths as test_paths
import importlib.util
import os
import struct

import pytest

from device import displays

BACKEND = str(test_paths.BACKEND)

SF = ('Display 4619827820427265280 (HWC display 0): port=0 pnpId=GGL displayName="mumuscreen000"\n'
      'Display 4619827767814508545 (HWC display 1): port=1 pnpId=GGL displayName="mumuscreen001"\n'
      'Display 4619826888814064386 (HWC display 2): port=2 pnpId=GGL displayName="mumuscreen002"\n')
DD = ("  mDisplayId=0\n    mPrimaryDisplayDevice=Built-in Screen(local:4619827820427265280)\n"
      "  mDisplayId=2\n    mPrimaryDisplayDevice=mumuscreen001(local:4619827767814508545)\n"
      "  mDisplayId=3\n    mPrimaryDisplayDevice=mumuscreen002(local:4619826888814064386)\n")
WARNING = (b"[Warning] Multiple displays were found, but no display id was specified! "
           b"Defaulting to the first display found, however this default is not guaranteed "
           b"to be consistent across runs.\n")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


def test_game_display_is_the_first_secondary_mumuscreen_with_its_input_index():
    calls = []

    def shell(cmd):
        calls.append(cmd)
        return SF if "SurfaceFlinger" in cmd else DD
    assert displays.game_display(shell) == ("4619827767814508545", 2)
    assert calls == ["dumpsys SurfaceFlinger --display-id", "dumpsys display"]
    # BlueStacks / a single-screen MuMu: nothing to derive
    assert displays.game_display(lambda cmd: "Display 0 (HWC display 0): port=0\n") == (None, None)
    assert displays.game_display(lambda cmd: SF.splitlines()[0] + "\n") == (None, None)


def test_strip_warning_only_drops_a_text_prefix_ahead_of_a_png():
    assert displays.strip_warning(WARNING + PNG) == PNG
    assert displays.strip_warning(PNG) == PNG
    assert displays.strip_warning(b"error: device offline\n") == b"error: device offline\n"
    assert displays.strip_warning(b"") == b""


@pytest.fixture(autouse=True)
def _no_real_adb_server(monkeypatch):
    """A failed frame runs the dashboard's _device_state, i.e. the real
    adbclient.get_state; point adbclient at a loopback port nothing listens on
    so no test here reaches the adb server on 5037."""
    import socket
    from device import adbclient
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    host = s.getsockname()
    s.close()
    monkeypatch.setattr(adbclient, "_HOST", host)


@pytest.fixture(scope="module")
def dash():
    path = str(test_paths.DASHBOARD)
    spec = importlib.util.spec_from_file_location("tp_dashboard_display", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mumu(dash, monkeypatch):
    """A MuMu instance with no display configured, answering like the real one:
    the warning + launcher without -d, the game frame with the derived id."""
    from device import adbclient
    cfg = {"active_instance": "main", "adb": {"exe": "adb"},
           "instances": {"main": {"serial": "127.0.0.1:16416"}}}
    cmds = []

    def exec_out(serial, cmd, timeout=None):
        cmds.append(cmd)
        if "-d 4619827767814508545" in cmd:
            return PNG if "-p" in cmd else struct.pack("<II", 1080, 2560) + b"\x00" * 8
        return WARNING + (PNG if "-p" in cmd else struct.pack("<II", 2560, 1080) + b"\x00" * 8)

    def shell(serial, cmd, timeout=None):
        return (SF if "SurfaceFlinger" in cmd else DD).encode()
    monkeypatch.setattr(dash, "load_config", lambda: dict(cfg))
    monkeypatch.setattr(adbclient, "exec_out", exec_out)
    monkeypatch.setattr(adbclient, "shell", shell)
    monkeypatch.setattr(dash, "_wiz_save", lambda key, value: value)
    monkeypatch.setattr(dash, "_mark_setup_complete", lambda key: None)
    dash._DISPLAY_CACHE.clear()
    return cmds


def test_preview_and_screen_check_ask_for_the_game_display(dash, mumu):
    with dash.app.test_client() as c:
        r = c.get("/api/frame.png")
        assert r.status_code == 200 and r.data == PNG
        assert mumu[-1] == "screencap -p -d 4619827767814508545"
        r = c.get("/api/wizard/resolution?serial=127.0.0.1:16416").get_json()
    assert (r["ok"], r["width"], r["height"], r["expected"]) == (True, 1080, 2560, True)
    assert mumu[-1] == "screencap -d 4619827767814508545"
    assert "127.0.0.1:16416" in dash._DISPLAY_CACHE          # derived once, cached


def test_a_failed_frame_queries_only_the_redirected_adb_server(dash, mumu, monkeypatch):
    # a failed frame runs _device_state (adbclient.get_state + the serial's port
    # dial); every dial is refused here and recorded, none is forwarded.
    import socket
    from device import adbclient
    dialed = []

    def dial(addr, *args, **kwargs):
        dialed.append(addr)
        raise ConnectionRefusedError()
    monkeypatch.setattr(socket, "create_connection", dial)
    monkeypatch.setattr(dash, "_reconnect_tick", lambda *a: None)   # no recovery thread, no adb spawn

    def down(serial, cmd, timeout=None):
        raise OSError("device down")
    monkeypatch.setattr(adbclient, "exec_out", down)
    dash._CONN_CACHE["t"] = 0.0
    with dash.app.test_client() as c:
        assert c.get("/api/frame.png").status_code == 502
    assert adbclient._HOST in dialed
    assert ("127.0.0.1", 5037) not in dialed


def test_screen_check_never_reads_a_warning_as_a_resolution(dash, mumu, monkeypatch):
    # a stale display id: the emulator restarted and every -d answers the
    # warning + default display. The check must strip the text, not parse it.
    from device import adbclient
    monkeypatch.setattr(adbclient, "exec_out",
                        lambda serial, cmd, timeout=None: WARNING + struct.pack("<II", 1080, 2560) + b"\x00" * 8)
    with dash.app.test_client() as c:
        r = c.get("/api/wizard/resolution?serial=127.0.0.1:16416").get_json()
    assert (r["width"], r["height"]) == (1080, 2560)
    monkeypatch.setattr(adbclient, "exec_out", lambda serial, cmd, timeout=None: b"[Warning] text only\n")
    with dash.app.test_client() as c:
        r = c.get("/api/wizard/resolution?serial=127.0.0.1:16416").get_json()
    assert r["ok"] is False and "which display" in r["error"]
