"""The dashboard is a control panel for THIS machine: requests from other
sites (DNS rebinding, cross-site POSTs), executables or device-shell text
smuggled through query strings, and path parameters that leave their folder
are refused. Flask test client only - no server, no device.
"""

import paths as test_paths

import importlib.util
import os
import socket
import tempfile
from pathlib import Path

import pytest

BACKEND = test_paths.BACKEND
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
CFG = {"active_instance": "main", "adb": {"exe": "C:\\emu\\adb.exe"},
       "instances": {"main": {"serial": "127.0.0.1:16384", "display": None}}}


@pytest.fixture()
def dash(tmp_path, monkeypatch):
    # any adbclient call a test misses meets a dead loopback port, never the
    # real adb server on 5037
    from device import adbclient

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    monkeypatch.setattr(adbclient, "_HOST", s.getsockname())
    s.close()
    spec = importlib.util.spec_from_file_location("tp_dashboard_security", test_paths.DASHBOARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    root = tmp_path / "backend"
    (root / "profiles").mkdir(parents=True)
    (root / "logs" / "main").mkdir(parents=True)
    monkeypatch.setattr(mod, "ROOT", str(root))
    # module-level paths computed from the real ROOT at import: keep them off the live files
    monkeypatch.setattr(mod, "_WIZ_STATE", str(root / "logs" / "wizard_state.json"))
    monkeypatch.setattr(mod, "_SETUP_FLAG", str(root / "logs" / "setup_done"))
    monkeypatch.setattr(mod, "CONFIG_PATH", str(tmp_path / "config.yaml"))
    monkeypatch.setattr(mod, "load_config", lambda: {k: (dict(v) if isinstance(v, dict) else v) for k, v in CFG.items()})
    monkeypatch.setattr(mod, "_calibration_dir", lambda cfg=None: str(root / "logs" / "main"))
    return mod


def test_every_path_the_dashboard_writes_to_is_inside_the_test_dir(dash, tmp_path):
    # the module computes these from the REAL ROOT when it is imported, so
    # patching ROOT alone left them on the live files: the resolution route
    # saved a probe of a malformed serial into backend/logs/wizard_state.json
    for name in ("_WIZ_STATE", "_SETUP_FLAG", "CONFIG_PATH"):
        assert Path(getattr(dash, name)).resolve().is_relative_to(tmp_path.resolve()), name


# ------------------------------------------------------------ Host / Origin
@pytest.mark.parametrize("host", ["127.0.0.1:8620", "localhost:8620", "[::1]:8620", "127.0.0.1",
                                  "LocalHost:8621", "localhost"])
def test_loopback_hosts_are_served(dash, host):
    with dash.app.test_client() as c:
        assert c.get("/api/profiles", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["attacker.example", "attacker.example:8620", "127.0.0.1.attacker.example:8620",
                                  "localhost.attacker.example", "192.168.1.20:8620", "0.0.0.0:8620", "[::2]:8620"])
def test_other_hosts_are_refused_before_any_route_runs(dash, host):
    with dash.app.test_client() as c:
        r = c.get("/api/profiles", headers={"Host": host})
        assert r.status_code == 421
        assert r.get_json()["ok"] is False and "host" in r.get_json()["error"].lower()
        assert c.post("/api/control", json={"action": "clear_flag"}, headers={"Host": host}).status_code == 421


@pytest.mark.parametrize("host,origin", [("127.0.0.1:8620", None),
                                         ("127.0.0.1:8620", "http://127.0.0.1:8620"),
                                         ("localhost:8620", "http://localhost:8620"),
                                         ("[::1]:8620", "http://[::1]:8620")])
def test_same_origin_and_originless_posts_are_served(dash, host, origin):
    headers = {"Host": host, **({"Origin": origin} if origin else {})}
    with dash.app.test_client() as c:
        r = c.post("/api/control", json={"action": "clear_flag"}, headers=headers)
    assert (r.status_code, r.get_json()) == (200, {"ok": True})


@pytest.mark.parametrize("origin", ["https://attacker.example", "http://attacker.example:8620", "null",
                                    "http://127.0.0.1:3000", "http://localhost:8620", "https://127.0.0.1:8620"])
def test_cross_origin_posts_are_refused_and_release_the_action_lock(dash, origin):
    with dash.app.test_client() as c:
        r = c.post("/api/control", json={"action": "stop_after_run"},
                   headers={"Host": "127.0.0.1:8620", "Origin": origin})
        assert r.status_code == 403 and r.get_json()["ok"] is False
        assert not os.path.exists(os.path.join(dash.ROOT, "logs", "main", "stop_after_run"))
        # the refused request never held the action lock: the next one runs
        assert c.post("/api/control", json={"action": "clear_flag"},
                      headers={"Host": "127.0.0.1:8620"}).status_code == 200


def test_a_cross_origin_get_is_not_an_origin_refusal(dash):
    with dash.app.test_client() as c:
        r = c.get("/api/profiles", headers={"Host": "127.0.0.1:8620", "Origin": "https://attacker.example"})
    assert r.status_code == 200


@pytest.mark.parametrize("method", ["HEAD", "OPTIONS"])
def test_head_and_options_are_not_origin_checked(dash, method):
    with dash.app.test_client() as c:
        r = c.open("/api/profiles", method=method,
                   headers={"Host": "127.0.0.1:8620", "Origin": "https://attacker.example"})
    assert r.status_code == 200


def test_every_response_forbids_framing(dash):
    # no first-party page frames the dashboard; any other page framing it
    # could click Start / allow taps through an invisible overlay
    with dash.app.test_client() as c:
        served = c.get("/api/profiles", headers={"Host": "127.0.0.1:8620"})
        posted = c.post("/api/control", json={"action": "clear_flag"}, headers={"Host": "127.0.0.1:8620"})
        refused = c.get("/api/profiles", headers={"Host": "attacker.example"})
        page = c.get("/ui/app/", headers={"Host": "127.0.0.1:8620"})
        static = c.get("/ui/index.html", headers={"Host": "127.0.0.1:8620"})
    for r in (served, posted, refused, page, static):
        assert r.headers.get("X-Frame-Options") == "DENY", r.request.path


# ---------------------------------------------------------- /api/frame.png
@pytest.fixture()
def device(dash, monkeypatch):
    """adb as seen by the frame routes: every exec_out and every spawn is
    recorded; `fail` makes the socket path raise (server down)."""
    from device import adbclient
    seen = {"exec": [], "run": [], "fail": False}

    def exec_out(serial, cmd, timeout=None):
        seen["exec"].append((serial, cmd))
        if seen["fail"]:
            raise ConnectionRefusedError("no adb server")
        return PNG
    def shell(serial, cmd, timeout=None):
        # display discovery for an uncached serial: never a real adb server
        seen["exec"].append((serial, cmd))
        raise ConnectionRefusedError("no adb server")

    def recover(serial, start_server=None, **kw):
        # what adbclient.recover does with no server listening
        start_server()
        return "no_server"

    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    monkeypatch.setattr(adbclient, "_HOST", s.getsockname())   # dead port, never 5037
    s.close()
    monkeypatch.setattr(adbclient, "exec_out", exec_out)
    monkeypatch.setattr(adbclient, "shell", shell)
    monkeypatch.setattr(adbclient, "get_state", lambda serial, timeout=1.0: "no_server")
    monkeypatch.setattr(adbclient, "recover", recover)
    monkeypatch.setattr(dash, "_port_open", lambda serial: True)
    monkeypatch.setattr(dash, "_run", lambda args, **kw: seen["run"].append(args))
    dash._DISPLAY_CACHE["127.0.0.1:16384"] = (None, 1e18)
    yield seen
    t = dash._RECONNECT["thread"]
    if t is not None:
        t.join(5)


def test_the_pages_own_frame_request_is_served(dash, device):
    with dash.app.test_client() as c:
        r = c.get("/api/frame.png?ts=1760000000000")
    assert (r.status_code, r.data) == (200, PNG)
    assert device["exec"] == [("127.0.0.1:16384", "screencap -p")]


def test_the_frame_fallback_starts_only_the_configured_adb(dash, device):
    # the start-server comes from the background recovery, not the request
    device["fail"] = True
    with dash.app.test_client() as c:
        assert c.get("/api/frame.png?adb=C:\\Windows\\System32\\calc.exe").status_code == 502
        dash._RECONNECT["thread"].join(5)
    assert device["run"] == [["C:\\emu\\adb.exe", "start-server"]]


@pytest.mark.parametrize("query", ["display=0;reboot", "display=0%20%26%26%20input%20tap%201%201", "display=-1",
                                   "display=0x1"])
@pytest.mark.parametrize("route", ["/api/frame.png", "/api/stream.mjpg", "/api/wizard/resolution"])
def test_malformed_display_never_reaches_adb(dash, device, route, query):
    if route.endswith("resolution"):
        query += "&serial=127.0.0.1:16384"
    with dash.app.test_client() as c:
        r = c.get(f"{route}?{query}")
    assert r.status_code == 400 and r.get_json()["ok"] is False
    assert device["exec"] == [] and device["run"] == []


@pytest.mark.parametrize("serial", ["127.0.0.1:5555;reboot", "x%0Ahost:kill", "127.0.0.1:5555%0A"])
def test_malformed_resolution_serial_never_reaches_adb(dash, device, serial):
    with dash.app.test_client() as c:
        r = c.get(f"/api/wizard/resolution?serial={serial}")
    assert r.status_code == 400 and r.get_json()["ok"] is False
    assert device["exec"] == [] and device["run"] == []


def _png():
    import cv2
    import numpy as np
    return cv2.imencode(".png", np.zeros((4, 4, 3), np.uint8))[1].tobytes()


@pytest.mark.parametrize("serial", ["127.0.0.1:16416", "127.0.0.1:5555;reboot", "emulator-5554"])
def test_capture_routes_capture_only_the_configured_serial(dash, device, monkeypatch, serial):
    # no page sends `serial`: the preview and the live stream show the
    # configured device, whatever the query names
    from device import adbclient

    def capture(s, cmd, timeout=None):
        import struct
        device["exec"].append((s, cmd))
        return _png() if " -p" in cmd else struct.pack("<IIII", 4, 4, 1, 0) + bytes(4 * 4 * 4)

    monkeypatch.setattr(adbclient, "exec_out", capture)
    with dash.app.test_client() as c:
        assert c.get(f"/api/frame.png?serial={serial}").status_code == 200
        r = c.get(f"/api/stream.mjpg?backend=adb&serial={serial}")
        assert next(iter(r.response)).startswith(b"--frame")
        r.close()
    assert device["exec"] == [("127.0.0.1:16384", "screencap -p"),
                              ("127.0.0.1:16384", "screencap")]


@pytest.mark.parametrize("backend", ["auto", "mumu", "scrcpy"])
@pytest.mark.parametrize("serial", ["127.0.0.1:16416", "127.0.0.1:5555;reboot", "emulator-5554"])
def test_accelerated_preview_ignores_query_device_and_executable(dash, device, monkeypatch, backend, serial):
    import numpy as np
    from device.preview.manager import PreviewCapture

    opened, closed = [], []

    class VideoSource:
        def read(self, timeout=2.0):
            return np.zeros((4, 4, 3), dtype=np.uint8)

        def close(self):
            closed.append(True)

    def open_source(preview, source_name):
        opened.append((preview.serial, preview.adb_exe, source_name))
        return VideoSource()

    monkeypatch.setattr(PreviewCapture, "_open", open_source)
    with dash.app.test_client() as client:
        response = client.get("/api/stream.mjpg", query_string={
            "backend": backend, "serial": serial, "adb": "C:/untrusted/program.exe", "stream_id": "routing"})
        try:
            assert response.status_code == 200
            assert next(iter(response.response)).startswith(b"--frame")
            status = client.get("/api/stream/status?stream_id=routing").get_json()
            assert status["frames"] == 1 and status["errors"] == 0
            assert status["requested_backend"] == backend
        finally:
            response.close()
    assert len(opened) == 1
    assert opened[0][:2] == ("127.0.0.1:16384", "C:\\emu\\adb.exe")
    assert opened[0][2] == ("scrcpy" if backend == "scrcpy" else "mumu")
    assert closed == [True]
    assert device["exec"] == [] and device["run"] == []


def test_an_integer_display_is_used(dash, device):
    with dash.app.test_client() as c:
        assert c.get("/api/frame.png?display=4619827767814508545").status_code == 200
    assert device["exec"] == [("127.0.0.1:16384", "screencap -p -d 4619827767814508545")]


def test_resolution_accepts_the_configured_serial_whatever_its_form(dash, device, monkeypatch):
    # a hand-written usb serial in config.yaml: not a form adopt writes, but
    # it is this machine's own device, so the screen check keeps working
    cfg = dict(CFG, instances={"main": {"serial": "R58M12ABCDE"}})
    monkeypatch.setattr(dash, "load_config", lambda: cfg)
    monkeypatch.setattr(dash, "_wiz_save", lambda key, value: value)
    dash._DISPLAY_CACHE["R58M12ABCDE"] = (None, 1e18)
    with dash.app.test_client() as c:
        r = c.get("/api/wizard/resolution?serial=R58M12ABCDE")
    assert r.status_code == 200 and device["exec"] == [("R58M12ABCDE", "screencap")]


# ------------------------------------------------- path-like route parameters
def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_profile_reads_only_profiles_yaml(dash, tmp_path, monkeypatch):
    _write(Path(dash.ROOT) / "profiles" / "mine.yaml", "player: {max_tier: 1}\n")
    _write(tmp_path / "secret.yaml", "secret: 1\n")
    monkeypatch.chdir(tmp_path)
    with dash.app.test_client() as c:
        assert c.get("/api/profile/mine.yaml").get_json() == {"player": {"max_tier": 1}}
        assert c.get("/api/profile/missing.yaml").status_code == 404
        for name in ["..%5Csecret.yaml", "..%2Fsecret.yaml", "..", "mine", "mine.yaml%0A",
                     "mine.yaml:stream", f"{tmp_path.drive}secret.yaml"]:
            r = c.get(f"/api/profile/{name}")
            assert r.status_code in (403, 404) and b"secret" not in r.data, name


@pytest.mark.skipif(os.name != "nt" or BACKEND.drive.casefold() == Path(tempfile.gettempdir()).drive.casefold(),
                    reason="needs Windows with the repo and the temp dir on different drives")
def test_a_drive_relative_name_cannot_leave_profiles(dash, tmp_path, monkeypatch):
    """`C:secret.yaml` joined onto a backend on ANOTHER drive is the C: drive's
    current directory, not profiles/ (ntpath.join keeps only the new drive)."""
    _write(tmp_path / "secret.yaml", "secret: 1\n")
    elsewhere = BACKEND.parent / "never-created" / "backend"                                 # repo drive
    monkeypatch.setattr(dash, "ROOT", str(elsewhere))
    from control import diagnostics
    monkeypatch.setattr(diagnostics, 'log_root', lambda root: tmp_path / 'diagnostics')
    monkeypatch.setattr(dash, "_calibration_dir", lambda cfg=None: str(elsewhere / "logs" / "main"))
    monkeypatch.chdir(tmp_path)                                                             # temp drive
    with dash.app.test_client() as c:
        r = c.get(f"/api/profile/{tmp_path.drive}secret.yaml")
        assert r.status_code == 403 and b"secret" not in r.data
        r = c.get(f"/api/evidence/{tmp_path.drive}secret.yaml")
        assert r.status_code == 403 and b"secret" not in r.data


def test_profile_src_rejects_names_that_leave_profiles(dash, tmp_path, monkeypatch):
    _write(tmp_path / "secret.yaml", "secret: 1\n")
    monkeypatch.chdir(tmp_path)
    with dash.app.test_client() as c:
        for name in ["..%5Csecret", "..%2Fsecret", f"{tmp_path.drive}secret", "secret%0A"]:
            r = c.get(f"/api/profile-src/{name}")
            assert r.status_code in (400, 404) and b"secret: 1" not in r.data, name


def test_evidence_and_template_stay_inside_their_folders(dash, tmp_path, monkeypatch):
    _write(tmp_path / "secret.png", "SECRET")
    monkeypatch.chdir(tmp_path)
    with dash.app.test_client() as c:
        for route in ["/api/evidence/", "/api/template/"]:
            for rel in ["..%5C..%5C..%5Csecret.png", "../../../secret.png", "a/..%2F..%2F..%2F..%2Fsecret.png",
                        f"{tmp_path.drive}%5Csecret.png", f"{tmp_path.drive}secret.png",
                        str(tmp_path / "secret.png").replace("\\", "/")]:
                r = c.get(route + rel)
                assert r.status_code in (403, 404) and b"SECRET" not in r.data, route + rel
        r = c.post("/api/template/..%2F..%2Fsecret.png", json={"ts": "1", "x": 0, "y": 0, "w": 9, "h": 9})
        assert r.status_code == 400


def test_artwork_never_serves_a_file_named_by_the_url(dash, tmp_path, monkeypatch):
    _write(tmp_path / "secret.png", "SECRET")
    monkeypatch.chdir(tmp_path)
    with dash.app.test_client() as c:
        for rel in ["..%5C..%5C..%5Csecret.png", "../../../secret.png", f"{tmp_path.drive}secret.png"]:
            assert b"SECRET" not in c.get("/api/artwork/" + rel).data


# --------------------------------------------------- artwork without a picture
def test_a_target_without_artwork_answers_204_with_no_body(dash):
    with dash.app.test_client() as c:
        r = c.get("/api/artwork/modules/some_module.png")
    assert (r.status_code, r.data) == (204, b"")
