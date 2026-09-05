"""Local web dashboard: preset/loadout editor, live status, setup wizard.

Runs a Flask server on 127.0.0.1 only - this is a control panel for THIS
machine, not a service. Point a browser at http://127.0.0.1:8620/

Design rules:
  * The dashboard never imports settings/capture/orchestrator - the runners own the
    game. Everything here goes through the filesystem (config.yaml, logs,
    daily_state) or a direct adb call, so a dashboard bug can never wedge a
    live run.
  * Every config save writes a timestamped backup next to config.yaml first.
    People WILL save a broken value; the previous file must be one copy away.
  * Emulator-agnostic: MuMu and BlueStacks (and anything else adb-based)
    differ only in where adb.exe lives and which ports the instances listen
    on. The wizard scans the known install paths and probes each daemon for
    devices; whatever answers `adb devices` can be driven.

Start:  python dashboard.py           (or pythonw for headless)
"""
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time

import yaml
from flask import Flask, Response, jsonify, redirect, request, send_file

# The FRONTEND lives here (dashboard.py + webui/); everything it serves and
# controls - config.yaml, logs, profiles, templates, the runner scripts -
# lives in the BACKEND directory next door. ROOT is the backend: it is the
# working directory every runner is spawned with and the tree that
# _proc_in_tree scopes process ownership to.
ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "backend")
# The compiler-side imports (playerprofile.vocab, the flow registry) resolve
# from the backend too.
sys.path.insert(0, ROOT)
CONFIG_PATH = os.path.join(ROOT, "config.yaml")
PORT = int(os.environ.get("TOWER_PILOT_PORT", "8620"))   # override with the
# TOWER_PILOT_PORT env var (a second dashboard on one machine, tests)

# The dashboard runs under pythonw (no console), so every adb.exe child pops
# its own console window - the frame endpoint alone spawned ~720 windows/hour
# (the "MuMu PowerShell window", 2026-08-18). Same rule as settings.run_hidden,
# duplicated here because this module deliberately never imports settings.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _run(args, **kw):
    kw.setdefault("creationflags", NO_WINDOW)
    return subprocess.run(args, **kw)

app = Flask(__name__, static_folder="webui", static_url_path="/ui")

# Known emulator adb locations, expanded per-drive. BlueStacks 5 ships its
# own daemon as HD-Adb.exe; MuMu under nx_main; a PATH adb catches the rest.
EMULATOR_ADB_CANDIDATES = [
    ("MuMu Player", r"{pf}\Netease\MuMuPlayer\nx_main\adb.exe"),
    ("MuMu Player 12", r"{pf}\Netease\MuMu Player 12\shell\adb.exe"),
    ("BlueStacks 5", r"{pf}\BlueStacks_nxt\HD-Adb.exe"),
    ("BlueStacks (msi2)", r"{pf}\BlueStacks_msi2\HD-Adb.exe"),
    ("LDPlayer", r"{pf}\LDPlayer\LDPlayer9\adb.exe"),
    ("PATH adb", "adb"),
]

# P0 (2026-08-18): runnable presets are DERIVED from config.yaml instead of
# a hardcoded allowlist that drifted every time a preset was added. A preset
# is startable unless it is an explicit placeholder (defined: false).
def runnable_presets(cfg: dict) -> list[str]:
    return [name for name, body in (cfg.get("presets") or {}).items()
            if isinstance(body, dict) and body.get("defined", True)]


# ------------------------------------------------------------------ config io
CONFIG_EXAMPLE = os.path.join(ROOT, "config.example.yaml")


def seed_config() -> bool:
    """Create config.yaml from config.example.yaml when it is missing - the
    same rule as settings.seed_config (this module never imports settings).
    Without it a fresh install 500s on /api/status and on the wizard's own
    scan endpoint, so the wizard that writes the file cannot run."""
    if os.path.exists(CONFIG_PATH):
        return False
    if not os.path.exists(CONFIG_EXAMPLE):
        raise FileNotFoundError(f"{CONFIG_PATH} is missing and so is "
                                f"{CONFIG_EXAMPLE}")
    with open(CONFIG_EXAMPLE, encoding="utf-8") as src,             open(CONFIG_PATH, "w", encoding="utf-8") as dst:
        dst.write(src.read())
    return True


def load_config() -> dict:
    seed_config()
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def save_config(data: dict) -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = CONFIG_PATH + f".bak-{stamp}"
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        old = fh.read()
    with open(backup, "w", encoding="utf-8") as fh:
        fh.write(old)
    # Round-trip through yaml BEFORE touching config.yaml: a value the yaml
    # writer cannot represent must fail here, with the old file untouched.
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True,
                          default_flow_style=None)
    yaml.safe_load(text)
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        fh.write(text)
    # keep the last 20 backups, drop older ones
    baks = sorted(glob.glob(CONFIG_PATH + ".bak-*"))
    for b in baks[:-20]:
        os.remove(b)
    return os.path.basename(backup)


@app.get("/")
def index():
    return redirect("/ui/index.html")


@app.get("/api/config")
def api_config_get():
    return jsonify(load_config())


@app.post("/api/config")
def api_config_post():
    data = request.get_json(force=True)
    try:
        backup = save_config(data)
    except Exception as e:                      # noqa: BLE001 - shown to user
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "backup": backup})


# ------------------------------------------------------------------ status
def _procs():
    """Live runner processes. THE ORDER OF THE TWO LOOKUPS IS THE WHOLE
    FUNCTION (2026-08-18, live): asking psutil for `cmdline` on every
    process on the machine cost ~4 s per call on Windows (a per-process
    handle query), and /api/status calls it every 2.5 s - refreshes queued
    behind each other for good and the page never redrew after kill/start/
    scan. Name is nearly free; cmdline is fetched only for the few python
    processes."""
    return _scan_procs()


def _proc_in_tree(pid: int) -> bool:
    """Two trees can farm side by side (the main farm and the P3 clone,
    each driving its own VM). A runner belongs to THIS dashboard only if
    its process cwd is this tree - every runner is spawned with its tree
    as working directory, and the cmdline alone cannot tell the trees
    apart (`pythonw orchestrator.py --instance main` is relative). Same rule as
    scan.py's preflight. Unreadable cwd counts as ours: every consumer of
    these rows (wizard guards, kill buttons, activate) must fail closed."""
    try:
        import psutil
        cwd = os.path.normcase(psutil.Process(pid).cwd())
    except Exception:                           # noqa: BLE001
        return True
    return cwd == os.path.normcase(ROOT)


def _scan_procs():
    """One WMI query for all python processes with their command lines
    (~1.5 s on this machine - process enumeration is just slow on Windows;
    psutil's per-process handles were 2-4 s). The number does not matter
    any more: this runs on the background refresher thread, never on a
    request. Falls back to psutil if pwsh/WMI is unavailable. Rows are
    scoped to THIS tree (_proc_in_tree) - the other tree's runners are a
    different farm on a different VM and must not trip this dashboard's
    guards or appear in its kill list."""
    out = []
    try:
        res = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name like "
                    "'python%'\" | Select-Object ProcessId,CommandLine | "
                    "ConvertTo-Json -Compress"],
                   capture_output=True, text=True, timeout=10)
        data = json.loads(res.stdout or "[]")
        if isinstance(data, dict):
            data = [data]
        for row in data:
            cl = row.get("CommandLine") or ""
            m = re.search(r"(orchestrator|shard|combo|tourney|quest_\w+|hp_probe|"
                          r"scan|boot|dashboard)\.py", cl)
            if m and m.group(1) != "dashboard":
                pid = int(row["ProcessId"])
                if not _proc_in_tree(pid):
                    continue
                out.append({"pid": pid,
                            "runner": m.group(1), "cmdline": cl})
        return out
    except Exception:                           # noqa: BLE001 - no WMI/pwsh
        import psutil
        for p in psutil.process_iter(["pid", "name"]):
            if "python" not in (p.info["name"] or "").lower():
                continue
            try:
                cl = " ".join(p.cmdline() or [])
            except Exception:                   # noqa: BLE001
                continue
            m = re.search(r"(orchestrator|shard|combo|tourney|quest_\w+|hp_probe|"
                          r"scan|boot|dashboard)\.py", cl)
            if m and m.group(1) != "dashboard":
                if not _proc_in_tree(p.info["pid"]):
                    continue
                out.append({"pid": p.info["pid"], "runner": m.group(1),
                            "cmdline": cl})
        return out


# /api/status is polled every 2.5 s by every open tab; the process scan
# must never be on that request's critical path. A background thread keeps
# a snapshot fresh (every 2 s) and the endpoint returns it instantly.
# Control actions (kill/start) call _procs_refresh() to update it at once.
_PROCS_CACHE = {"at": 0.0, "procs": []}
_PROCS_LOCK = None


def _procs_refresh():
    import threading
    global _PROCS_LOCK
    if _PROCS_LOCK is None:
        _PROCS_LOCK = threading.Lock()
    with _PROCS_LOCK:
        _PROCS_CACHE["procs"] = _scan_procs()
        _PROCS_CACHE["at"] = __import__("time").time()
    return _PROCS_CACHE["procs"]


def _procs_cached():
    import time as _t
    if _t.time() - _PROCS_CACHE["at"] > 6:     # stale beyond the refresher:
        return _procs_refresh()                # refresh inline once
    return _PROCS_CACHE["procs"]


def _start_procs_refresher():
    import threading
    import time as _t

    def loop():
        while True:
            try:
                _procs_refresh()
            except Exception:                   # noqa: BLE001
                pass
            _t.sleep(2.0)
    threading.Thread(target=loop, daemon=True, name="procs-refresher").start()


_CONN_CACHE = {"t": 0.0, "ok": False}


def _connected() -> bool:
    """Is the active instance's adb port answering right now? A pure TCP
    dial - never an adb.exe spawn (CLAUDE.md rule 1) - cached 10 s because
    /api/status is polled every 2.5 s by every open tab."""
    now = time.time()
    if now - _CONN_CACHE["t"] < 10:
        return _CONN_CACHE["ok"]
    ok = False
    try:
        cfg = load_config()
        inst = (cfg.get("instances") or {}).get(
            cfg.get("active_instance", "main")) or {}
        serial = inst.get("serial") or ""
        if ":" in serial:
            import socket
            host, _, port = serial.rpartition(":")
            with socket.create_connection((host, int(port)), timeout=0.3):
                ok = True
    except Exception:                   # noqa: BLE001 - unreachable = not connected
        ok = False
    _CONN_CACHE.update(t=now, ok=ok)
    return ok


_CALIB_CACHE = {"t": 0.0, "missing": []}


def _calibrate_missing() -> list:
    """Templates the code ASKED for and did not find, from the newest run's
    events - the only honest 'calibration is missing something' signal (a
    template never asked for cannot be known missing). Cached 10 s."""
    now = time.time()
    if now - _CALIB_CACHE["t"] < 10:
        return _CALIB_CACHE["missing"]
    cfg = load_config()
    inst = cfg.get("active_instance", "main")
    missing = sorted({r.get("template") for r in _newest_events(inst, n=2000)
                      if r.get("kind") == "template_missing"} - {None})
    _CALIB_CACHE.update(t=now, missing=missing)
    return missing


def _newest_events(instance: str = "main", n: int = 60):
    files = sorted(glob.glob(os.path.join(ROOT, "logs", instance,
                                          "events_*.jsonl")),
                   key=os.path.getmtime)
    if not files:
        return []
    rows = []
    with open(files[-1], encoding="utf-8") as fh:
        for line in fh.readlines()[-n:]:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


@app.get("/api/status")
def api_status():
    cfg = load_config()
    daily = {}
    try:
        with open(os.path.join(ROOT, "logs", "daily_state.json")) as fh:
            daily = json.load(fh)
    except (OSError, ValueError):
        pass
    inst = cfg.get("active_instance", "main")
    flag = os.path.join(ROOT, "logs", inst, "stop_after_run")
    return jsonify({
        "processes": _procs_cached(),
        "daily_state": daily,
        "stop_flag": os.path.exists(flag),
        "events": _newest_events(inst),
        "instance": inst,
        # setup gate: false hides every tab but the wizard (see _setup_complete;
        # sticky once true, and the grandfather probe runs only while false)
        "setup_complete": _setup_complete(),
        # nav gates: Control is locked until calibration has nothing missing,
        # and shows a connect overlay while the emulator port is not answering
        "connected": _connected(),
        "calibrate_missing": _calibrate_missing(),
    })


@app.get("/api/frame.png")
def api_frame():
    cfg = load_config()
    inst = cfg["instances"][cfg.get("active_instance", "main")]
    serial = request.args.get("serial") or inst["serial"]
    # -d: MuMu runs the game on a secondary display; without the id the
    # screencap answers with a warning instead of pixels (same as capture.py)
    display = request.args.get("display") or inst.get("display")
    cmd = "screencap -p" + (f" -d {display}" if display else "")
    # SOCKET FIRST, adb.exe NEVER on the hot path (user, 2026-08-18: "we
    # also have a Stream why running new ADB?"): this endpoint fires every
    # 5 s per open tab, and spawning adb.exe for each was both the console-
    # window storm and pointless - the runners already talk straight to the
    # adb server socket. The one process fallback (hidden) exists only for
    # a fresh machine where no adb server is running yet; running adb.exe
    # once STARTS the daemon, and every later frame rides the socket.
    try:
        from device import adbclient
        raw = adbclient.exec_out(serial, cmd, timeout=15)
    except Exception:                           # noqa: BLE001 - server down?
        adb = request.args.get("adb") or cfg["adb"]["exe"]
        try:
            raw = _run([adb, "-s", serial, "exec-out"] + cmd.split(),
                       capture_output=True, timeout=15).stdout
        except Exception as e:                  # noqa: BLE001
            return Response(f"capture failed: {e}", status=502)
    if not raw.startswith(b"\x89PNG"):
        return Response("no PNG from adb (device offline?)", status=502)
    ts = request.args.get("ts")
    if ts:
        # THE CROPPER'S CONTRACT: a template is cut from the exact frame the
        # user drew on, never from a fresh grab that may show another screen.
        _remember_frame(ts, raw)
    return Response(raw, mimetype="image/png")


@app.get("/api/stream.mjpg")
def api_stream():
    """Live feed: one persistent connection, frames pushed as captured
    (multipart/x-mixed-replace - every browser renders it in a plain <img>).

    User, 2026-08-18: "if we have an open socket feed why do we serve
    screens every 5 seconds and not a live feed?" - no reason; the poll was
    a first-cut default. Frames ride the same adb-server socket the runners
    use. RATE IS THE ONE DELIBERATE LIMIT: a device-side screencap costs
    ~350 ms of emulator CPU and the orchestrator already pulls ~3 fps of its own,
    so this streams at `fps` (default 2, cap 4) and never faster - the
    dashboard must not starve the rescue watch. Encodes to JPEG (~150 KB vs
    ~2.4 MB PNG) so a browser tab on the LAN is not 5 MB/s. Stops the
    moment the client disconnects (generator finalizer)."""
    import time as _time
    cfg = load_config()
    inst = cfg["instances"][cfg.get("active_instance", "main")]
    serial = request.args.get("serial") or inst["serial"]
    display = request.args.get("display") or inst.get("display")
    fps = max(0.5, min(float(request.args.get("fps", 2)), 4.0))
    quality = max(30, min(int(request.args.get("q", 70)), 90))
    scale = max(0.2, min(float(request.args.get("scale", 0.5)), 1.0))
    cmd = "screencap -p" + (f" -d {display}" if display else "")

    def gen():
        from device import adbclient
        import cv2
        import numpy as np
        period = 1.0 / fps
        # THE STREAM MUST NEVER STARVE THE SERVER (2026-08-18, live): with
        # the emulator offline every frame blocked 15 s inside the adb
        # timeout, pinning a Flask thread; /api/status calls queued behind
        # it for 8-20 s, so kill/scan/wizard all looked broken - one bug
        # wearing three faces. Short capture timeout, and after a failure
        # back off to one probe every 3 s so an offline device costs one
        # thread a trivial amount of time.
        fails = 0
        import socket

        def _port_up() -> bool:
            # a ~1 ms TCP probe of the emulator's adb port; when the VM is
            # down this is instant, so the stream never even ASKS adb and
            # never holds a thread for a capture that cannot succeed
            if ":" not in serial:
                return True
            host, _, port = serial.rpartition(":")
            try:
                with socket.create_connection((host, int(port)), timeout=0.3):
                    return True
            except OSError:
                return False

        while True:
            t0 = _time.monotonic()
            try:
                if not _port_up():
                    raise OSError("emulator port closed")
                raw = adbclient.exec_out(serial, cmd, timeout=2.0)
                fails = 0
                if raw.startswith(b"\x89PNG"):
                    img = cv2.imdecode(np.frombuffer(raw, np.uint8),
                                       cv2.IMREAD_COLOR)
                    if img is not None:
                        if scale != 1.0:
                            img = cv2.resize(img, None, fx=scale, fy=scale,
                                             interpolation=cv2.INTER_AREA)
                        ok, jpg = cv2.imencode(".jpg", img,
                                               [cv2.IMWRITE_JPEG_QUALITY,
                                                quality])
                        if ok:
                            yield (b"--frame\r\nContent-Type: image/jpeg\r\n"
                                   b"Content-Length: " +
                                   str(len(jpg)).encode() + b"\r\n\r\n" +
                                   jpg.tobytes() + b"\r\n")
            except GeneratorExit:
                return                      # client went away: stop capturing
            except Exception:               # noqa: BLE001 - device hiccup:
                fails += 1                  # skip the frame, keep the stream
                # emit a tiny "offline" marker frame so the <img> does not
                # sit on a stale picture forever and the user can see the
                # device is gone rather than the dashboard being broken
                try:
                    canvas = np.zeros((96, 270, 3), np.uint8)
                    cv2.putText(canvas, "device offline", (18, 58),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (80, 80, 240), 2)
                    ok, jpg = cv2.imencode(".jpg", canvas)
                    if ok:
                        yield (b"--frame\r\nContent-Type: image/jpeg\r\n"
                               b"Content-Length: " + str(len(jpg)).encode()
                               + b"\r\n\r\n" + jpg.tobytes() + b"\r\n")
                except GeneratorExit:
                    return
                except Exception:           # noqa: BLE001
                    pass
            dt = _time.monotonic() - t0
            wait = (3.0 if fails else period)
            if dt < wait:
                _time.sleep(wait - dt)

    return Response(gen(),
                    mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"Cache-Control": "no-cache"})


# ------------------------------------------------------------------ control
@app.post("/api/control")
def api_control():
    body = request.get_json(force=True)
    action = body.get("action")
    cfg = load_config()
    inst = cfg.get("active_instance", "main")
    if action == "stop_after_run":
        path = os.path.join(ROOT, "logs", inst, "stop_after_run")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("dashboard")
        return jsonify({"ok": True})
    if action == "clear_flag":
        try:
            os.remove(os.path.join(ROOT, "logs", inst, "stop_after_run"))
        except FileNotFoundError:
            pass
        return jsonify({"ok": True})
    if action == "kill":
        import psutil
        pid = int(body["pid"])
        for p in _procs():
            if p["pid"] == pid:
                proc = psutil.Process(pid)
                # terminate, WAIT for it, escalate to kill: on Windows
                # terminate() is TerminateProcess and usually enough, but
                # the reply must not go out while the process is still in
                # the table - the UI refreshes on reply and would show the
                # row it just "killed" (user, 2026-08-18).
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except psutil.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=2)
                    except psutil.TimeoutExpired:
                        return jsonify({"ok": False,
                                        "error": f"pid {pid} would not die"}), 500
                _procs_refresh()
                return jsonify({"ok": True, "gone": True})
        return jsonify({"ok": False, "error": "not a runner pid"}), 400
    if action == "start":
        preset = body.get("preset")
        if preset not in runnable_presets(cfg):
            return jsonify({"ok": False, "error": "unknown preset"}), 400
        runner = cfg["presets"].get(preset, {}).get("runner") or "orchestrator.py"
        args = cfg["presets"].get(preset, {}).get("runner_args") or []
        pyw = sys.executable.replace("python.exe", "pythonw.exe")
        # combo.py schedules everything itself and takes no --preset. Flow
        # scripts take `--preset bp_<name>` (compiled blueprints) ONLY -
        # started under their own legacy config preset they bind it
        # themselves (FLOW["legacy_preset"]), and the quest runners refuse
        # the legacy name at argparse, which read as "runner failed" in the
        # UI (user, 2026-08-27).
        preset_argv = ["--preset", preset]
        if os.path.basename(runner) == "combo.py":
            preset_argv = []
        elif runner.replace("\\", "/").startswith("flows/"):
            try:
                import flows
                if any(spec.get("runner") == runner
                       and spec.get("legacy_preset") == preset
                       for spec in flows.flows().values()):
                    preset_argv = []
            except Exception as e:          # noqa: BLE001 - shown verbatim
                return jsonify({"ok": False,
                                "error": f"flow registry unreadable: {e}"}), 500
        cmd = ([pyw, os.path.join(ROOT, runner), "--instance", inst]
               + preset_argv + [str(a) for a in args])
        child = subprocess.Popen(cmd, cwd=ROOT,
                                 creationflags=subprocess.DETACHED_PROCESS
                                 | NO_WINDOW)
        # give the child a moment to show up (or die on import) so the reply
        # reflects reality; a runner that exits inside 1.5 s is a failed
        # start and the UI should say so, not list a ghost
        import time as _t
        _t.sleep(1.5)
        if child.poll() is not None:
            return jsonify({"ok": False,
                            "error": f"runner exited immediately (code "
                                     f"{child.returncode}) - check logs"}), 500
        _procs_refresh()
        return jsonify({"ok": True, "cmd": cmd, "pid": child.pid})
    return jsonify({"ok": False, "error": "unknown action"}), 400


# ------------------------------------------------------------------ scanner
@app.post("/api/scan/start")
def api_scan_start():
    body = request.get_json(force=True) or {}
    others = [pr for pr in _procs() if pr["runner"] != "scan"]
    if others:
        return jsonify({"ok": False,
                        "error": "runners alive: " +
                                 ", ".join(pr["runner"] for pr in others) +
                                 " - stop them first (scan.py would refuse "
                                 "anyway; this check saves the spawn)"}), 409
    if any(pr["runner"] == "scan" for pr in _procs()):
        return jsonify({"ok": False, "error": "a scan is already running"}), 409
    cfg = load_config()
    inst = cfg.get("active_instance", "main")
    phases = body.get("phases", "g,c,m")
    args = [os.path.join(ROOT, "player", "scan.py"), "--instance", inst,
            "--phases", phases]
    if body.get("battle"):
        args.append("--battle")
    if body.get("deep"):
        args.append("--deep")
    if body.get("fresh"):
        args.append("--fresh")
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    subprocess.Popen([pyw] + args, cwd=ROOT,
                     creationflags=subprocess.DETACHED_PROCESS | NO_WINDOW)
    return jsonify({"ok": True, "phases": phases})


@app.post("/api/scan/stop")
def api_scan_stop():
    cfg = load_config()
    inst = cfg.get("active_instance", "main")
    with open(os.path.join(ROOT, "logs", inst, "scan_stop"), "w") as fh:
        fh.write("dashboard")
    return jsonify({"ok": True})


@app.get("/api/scan/status")
def api_scan_status():
    cfg = load_config()
    inst = cfg.get("active_instance", "main")
    state = {}
    try:
        with open(os.path.join(ROOT, "logs", inst, "scan_state.json"),
                  encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        pass
    return jsonify({"running": any(pr["runner"] == "scan" for pr in _procs_cached()),
                    "state": state})


@app.get("/api/profiles")
def api_profiles():
    out = []
    pdir = os.path.join(ROOT, "profiles")
    for f in sorted(glob.glob(os.path.join(pdir, "*.yaml"))):
        out.append({"name": os.path.basename(f),
                    "draft": f.casefold().endswith(".draft.yaml"),
                    "mtime": os.path.getmtime(f)})
    return jsonify(out)


@app.get("/api/profile/<name>")
def api_profile(name):
    if "/" in name or "\\" in name or ".." in name:
        return Response("no", status=403)
    path = os.path.join(ROOT, "profiles", name)
    try:
        with open(path, encoding="utf-8") as fh:
            return jsonify(yaml.safe_load(fh))
    except OSError:
        return jsonify({"error": "not found"}), 404


# ------------------------------------------------------- profile editor (P6)
#
# THE UX LAW (user, 2026-08-19): every setting is a dropdown, a checkbox or a
# number box. Nobody edits YAML to configure a farm. The Configuration tree
# tab stays as the advanced escape hatch, but nothing in the normal flow
# needs it.
#
# THE VALIDATOR IS THE SINGLE SOURCE OF TRUTH. This module never re-implements
# a profile rule - not one range, not one "shard-only" clause. Every patch is
# applied to an IN-MEMORY copy, handed to playerprofile.validate(), and only
# written if that returns an empty list. The refusal text goes to the browser
# verbatim, because a paraphrase of a rule is a second copy of that rule.
#
# playerprofile is imported INSIDE each handler, never at module scope: this
# dashboard deliberately imports no runner module (a dashboard bug must not be
# able to wedge a live run), and a profile layer that fails to import has to
# degrade to a red box in the UI rather than a dead server.
# `\Z`, not `$`: Python's `$` also matches before a trailing newline, so
# "default\n" passed this and became a filename with a newline in it (audit,
# 2026-08-19). It never escaped profiles/, but a name that reads as valid and
# writes as something else is the start of every path bug.
_PROFILE_NAME_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.\-]*\Z")

# ONE LOCK PER PROFILE, held across the WHOLE read -> patch -> validate ->
# backup -> write transaction (audit, 2026-08-19: two overlapping valid
# patches both returned 200 with the same backup name and one edit vanished).
# Flask serves these on threads, and every patch is a read-modify-write of the
# same file - unlocked, the second read happens before the first write and the
# later writer wins with a stale document. Keyed by resolved path so two names
# for one file cannot take two locks.
_PROFILE_LOCKS: dict[str, threading.Lock] = {}
_PROFILE_LOCKS_GUARD = threading.Lock()
# activate is a read-modify-write of config.yaml through save_config, which has
# the same shape and the same race with any other config writer.
_CONFIG_WRITE_LOCK = threading.Lock()


def _profile_lock(path: str) -> threading.Lock:
    key = os.path.normcase(os.path.abspath(path))
    with _PROFILE_LOCKS_GUARD:
        lock = _PROFILE_LOCKS.get(key)
        if lock is None:
            lock = _PROFILE_LOCKS[key] = threading.Lock()
        return lock


def _profiles_dir() -> str:
    return os.path.join(ROOT, "profiles")


def _is_draft(name: str) -> bool:
    # casefold BOTH the check and every listing that keys off it (final P6
    # gate finding): NTFS resolves names case-insensitively, so "X.DRAFT"
    # addresses the same file as "x.draft.yaml" and a case-sensitive check
    # let it bypass the draft guards (patch 409 / activate 400 / picker).
    return isinstance(name, str) and name.casefold().endswith(".draft")


def _profile_path(name: str) -> str | None:
    """Absolute path of `profiles/<name>.yaml`, or None if the name is not a
    plain profile name. Rejects separators and `..` before touching disk."""
    if not isinstance(name, str) or not _PROFILE_NAME_RE.fullmatch(name):
        return None
    if ".." in name or "/" in name or "\\" in name:
        return None
    path = os.path.normpath(os.path.join(_profiles_dir(), name + ".yaml"))
    if os.path.dirname(path) != os.path.normpath(_profiles_dir()):
        return None
    return path


def _save_yaml_backup(path: str, data: dict) -> str:
    """Write `data` over `path`, timestamped backup first. Deliberately the
    same shape as save_config: round-trip through yaml BEFORE touching the
    real file (a value the writer cannot represent must fail with the old
    file intact), then keep the last 20 backups."""
    # ONE BACKUP PER EDIT, and this editor edits one field at a time - two
    # changes inside the same second are normal here, so the second-resolution
    # stamp gets a counter. The counter is claimed with O_CREAT|O_EXCL rather
    # than a look-then-write (audit, 2026-08-19): `os.path.exists` followed by
    # `open("w")` is itself a race, and two writers agreeing on a name means
    # the second silently destroys the first's copy - the one holding the
    # value the user wants back. Callers hold the per-profile lock as well;
    # this is the second belt, and the one that also covers any other process.
    with open(path, encoding="utf-8") as fh:
        old = fh.read()
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True,
                          default_flow_style=None)
    yaml.safe_load(text)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup, n = path + f".bak-{stamp}", 0
    while True:
        try:
            fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            n += 1
            backup = path + f".bak-{stamp}_{n}"
            if n > 999:                         # a stuck clock, not a race
                raise
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(old)
        break
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    for b in sorted(glob.glob(path + ".bak-*"))[:-20]:
        os.remove(b)
    return os.path.basename(backup)


@app.get("/api/flows")
def api_flows():
    """The flow registry, for the UI's run-type cards. Importing `flows` is
    safe here: discovery ast-parses the FLOW literals and executes no runner
    code (see backend/flows/__init__.py)."""
    try:
        import flows
        return jsonify({"ok": True, "flows": flows.flows()})
    except Exception as e:                      # noqa: BLE001 - shown verbatim
        return jsonify({"ok": False, "error": str(e), "flows": {}}), 500


@app.get("/api/vocab")
def api_vocab():
    """Every editable value space, as pure data, straight from the profile
    layer. The UI renders it GENERICALLY - enum to a select, bool to a
    checkbox, int/float to a number box with the stated range, list to an
    ordered editor - so a section this dashboard has never heard of still
    gets an editor instead of being dropped on the floor."""
    try:
        from player import playerprofile
        v = playerprofile.vocab()
    except Exception as e:                      # noqa: BLE001 - shown to user
        return jsonify({"ok": False, "vocab": {},
                        "error": f"{type(e).__name__}: {e}"})
    if not isinstance(v, dict):
        return jsonify({"ok": False, "vocab": {},
                        "error": f"vocab() returned {type(v).__name__}, "
                                 f"expected a dict"})
    return jsonify({"ok": True, "vocab": v})


@app.get("/api/profile-files")
def api_profile_files():
    """Profile NAMES (the stem playerprofile.load takes), with the active
    flag read from config.yaml's `active_profile`."""
    active = load_config().get("active_profile")
    out = []
    for f in sorted(glob.glob(os.path.join(_profiles_dir(), "*.yaml"))):
        stem = os.path.basename(f)[:-len(".yaml")]
        draft = _is_draft(stem)
        out.append({"name": stem, "file": os.path.basename(f),
                    "draft": draft,
                    # LISTED BUT NOT PATCHABLE (audit, 2026-08-19). A draft is
                    # scan output - `player:` and nothing else - so it is
                    # missing required sections and every single-field repair
                    # leaves another error behind. Hiding it would be a lie
                    # about what is in profiles/; offering an editor that can
                    # only refuse is worse. It gets a pointer instead.
                    "patchable": not draft,
                    "note": ("scan draft - complete it on the Profile tab "
                             "before editing it here") if draft else None,
                    "mtime": os.path.getmtime(f),
                    "active": stem == active})
    return jsonify({"profiles": out, "active": active})


@app.get("/api/profile-src/<name>")
def api_profile_src(name):
    """The parsed profile, plus the validator's current verdict on it.

    The verdict rides along because a profile is edited field by field: the
    editor has to show the problems the file ALREADY has, or a refusal on the
    first patch reads as "my edit broke it" when it was broken on arrival.
    """
    path = _profile_path(name)
    if not path:
        return jsonify({"ok": False, "error": f"bad profile name: {name!r}"}), 400
    if not os.path.exists(path):
        return jsonify({"ok": False, "error": f"no such profile: {name}"}), 404
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if not isinstance(data, dict):
        return jsonify({"ok": False,
                        "error": f"{name}.yaml is not a mapping"}), 400
    problems, warns, verr = [], [], None
    try:
        from player import playerprofile
        problems = playerprofile.validate(data)
        warns = playerprofile.warnings(data)
    except Exception as e:                      # noqa: BLE001
        verr = f"{type(e).__name__}: {e}"
    return jsonify({"ok": True, "name": name, "profile": data,
                    "problems": problems, "warnings": warns,
                    "validator_error": verr})


def _apply_patch(root: dict, path: list, value, delete: bool) -> str | None:
    """Walk `path` and set (or delete) the leaf. Returns an error string.

    Containers are addressed the way the profile reads: a string step is a
    mapping key, an int step is a list index (and index == len appends, which
    is how the ordered editors add a rule / directive / plan block). A missing
    MAPPING step is created - adding `plan` to a profile that has none is a
    legitimate edit - but a missing LIST index is never invented, because a
    hole in a list is not a thing the schema can express.
    """
    if not isinstance(path, list) or not path:
        return "path must be a non-empty list"
    node = root
    for i, step in enumerate(path[:-1]):
        nxt = path[i + 1]
        if isinstance(step, bool) or not isinstance(step, (str, int)):
            return f"path[{i}]: steps are strings or ints, got {step!r}"
        if isinstance(step, int):
            if not isinstance(node, list):
                return f"path[{i}]: index {step} into a {type(node).__name__}"
            if not 0 <= step < len(node):
                return f"path[{i}]: index {step} out of range"
            node = node[step]
            continue
        if not isinstance(node, dict):
            return f"path[{i}]: key {step!r} into a {type(node).__name__}"
        if step not in node or node[step] is None:
            node[step] = [] if isinstance(nxt, int) and not isinstance(nxt, bool) else {}
        node = node[step]
    leaf = path[-1]
    if isinstance(leaf, bool) or not isinstance(leaf, (str, int)):
        return f"path[-1]: steps are strings or ints, got {leaf!r}"
    if isinstance(leaf, int):
        if not isinstance(node, list):
            return f"path[-1]: index {leaf} into a {type(node).__name__}"
        if delete:
            if not 0 <= leaf < len(node):
                return f"path[-1]: index {leaf} out of range"
            node.pop(leaf)
        elif leaf == len(node):
            node.append(value)                  # append sentinel
        elif 0 <= leaf < len(node):
            node[leaf] = value
        else:
            return f"path[-1]: index {leaf} out of range (append with {len(node)})"
        return None
    if not isinstance(node, dict):
        return f"path[-1]: key {leaf!r} into a {type(node).__name__}"
    if delete:
        node.pop(leaf, None)
    else:
        node[leaf] = value
    return None


@app.post("/api/profile-patch")
def api_profile_patch():
    """One typed field, one request: {name, path: [...], value}.

    VALIDATE BEFORE WRITE, always, on an in-memory copy. The file on disk is
    never the place a bad value is discovered - a profile is loaded by a
    runner at spawn, hours later, and a field that only fails there fails
    where nobody is watching. `op: "delete"` removes the addressed key or
    list element (the ordered editors need it to drop a rule or a block).

    SERIALIZED PER PROFILE. The read, the patch, the validation, the backup
    and the write are ONE transaction under `_profile_lock` (audit,
    2026-08-19: two overlapping patches both answered 200 and one edit was
    gone). Semantics: LAST WRITER WINS, but every writer sees every earlier
    write - the losing shape was a writer that never saw the other edit at
    all. Requests queue rather than 409, because the editor sends one field
    per control and a queued 30ms write is invisible where a refusal is not.
    """
    body = request.get_json(force=True) or {}
    name = body.get("name")
    path = _profile_path(name)
    if not path:
        return jsonify({"ok": False, "error": f"bad profile name: {name!r}"}), 400
    if not os.path.exists(path):
        return jsonify({"ok": False, "error": f"no such profile: {name}"}), 404
    # A DRAFT IS NOT A PROFILE YET. scan.py writes `player:` alone, so it is
    # missing whole required sections and every single-field repair still
    # leaves another - the typed editor can only refuse, forever (audit,
    # 2026-08-19). It is completed on the Profile tab, where the scan that
    # produced it lives; the dashboard will not invent blueprints and
    # policies nobody authored just to make a file validate.
    if _is_draft(name):
        return jsonify({"ok": False, "error": (
            f"{name} is a scan DRAFT, not a profile: it has no blueprints or "
            f"policies yet, so no single field edit can make it valid. "
            f"Complete it on the Profile tab, then edit it here.")}), 409
    with _profile_lock(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except (OSError, yaml.YAMLError) as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        if not isinstance(data, dict):
            return jsonify({"ok": False,
                            "error": f"{name}.yaml is not a mapping"}), 400
        delete = body.get("op") == "delete"
        err = _apply_patch(data, body.get("path"), body.get("value"), delete)
        if err:
            return jsonify({"ok": False, "error": err}), 400
        # THE VALIDATOR DECIDES. Its message is the user's message, verbatim.
        try:
            from player import playerprofile
            problems = playerprofile.validate(data)
        except Exception as e:                  # noqa: BLE001
            return jsonify({"ok": False,
                            "error": f"validator unavailable: {type(e).__name__}: {e}"}), 500
        if problems:
            return jsonify({"ok": False, "problems": problems,
                            "error": problems[0]}), 400
        try:
            backup = _save_yaml_backup(path, data)
        except Exception as e:                  # noqa: BLE001
            return jsonify({"ok": False, "error": f"write failed: {e}"}), 500
    warns = []
    try:
        from player import playerprofile
        warns = playerprofile.warnings(data)
    except Exception:                           # noqa: BLE001 - advisory only
        pass
    return jsonify({"ok": True, "backup": backup, "profile": data,
                    "warnings": warns})


@app.post("/api/loadout-patch")
def api_loadout_patch():
    """One whole loadout body, one request: {name, body}.

    Loadouts live in config.yaml (the machine file), but their CONTENTS are
    what the profile validator's loadout-ownership checks veto - so the
    transaction here mirrors /api/profile-patch: apply to an in-memory copy,
    re-validate the ACTIVE profile against the patched loadouts, and only
    then write (timestamped config backup, config write lock). The verdict
    is the user's message, verbatim.

    playerprofile reads loadouts from the settings.CONFIG singleton, so the
    validation swaps the patched table in under the lock and restores it on
    every path; a successful save keeps it (the dashboard process then
    matches the file it just wrote).
    """
    body = request.get_json(force=True) or {}
    name = body.get("name")
    lo = body.get("body")
    if not isinstance(name, str) or not name.strip():
        return jsonify({"ok": False, "error": f"bad loadout name: {name!r}"}), 400
    if not isinstance(lo, dict) or not lo:
        return jsonify({"ok": False,
                        "error": "body must be a non-empty loadout mapping"}), 400
    with _CONFIG_WRITE_LOCK:
        cfg = load_config()
        if name not in (cfg.get("loadouts") or {}):
            return jsonify({"ok": False,
                            "error": f"no loadout named {name!r} in "
                                     f"config.yaml"}), 404
        patched = dict(cfg.get("loadouts") or {})
        patched[name] = lo
        active = cfg.get("active_profile")
        problems, warns = [], []
        if active:
            ppath = _profile_path(active)
            try:
                with _profile_lock(ppath):
                    with open(ppath, encoding="utf-8") as fh:
                        prof = yaml.safe_load(fh)
                from player import playerprofile
                from settings import CONFIG as LIVE_CONFIG
                old = LIVE_CONFIG.get("loadouts")
                LIVE_CONFIG["loadouts"] = patched
                try:
                    problems = playerprofile.validate(
                        prof if isinstance(prof, dict) else {})
                    warns = playerprofile.warnings(
                        prof if isinstance(prof, dict) else {})
                finally:
                    LIVE_CONFIG["loadouts"] = old
            except Exception as e:              # noqa: BLE001
                return jsonify({"ok": False,
                                "error": f"validator unavailable: "
                                         f"{type(e).__name__}: {e}"}), 500
            if problems:
                return jsonify({"ok": False, "problems": problems,
                                "error": problems[0]}), 400
        cfg["loadouts"] = patched
        try:
            backup = save_config(cfg)
        except Exception as e:                  # noqa: BLE001
            return jsonify({"ok": False, "error": f"write failed: {e}"}), 500
        from settings import CONFIG as LIVE_CONFIG
        LIVE_CONFIG["loadouts"] = patched
    return jsonify({"ok": True, "backup": backup, "loadouts": patched,
                    "warnings": warns})


@app.post("/api/profile-activate")
def api_profile_activate():
    """Bind config.yaml to a profile - {name} to set, {name: null} to remove
    the key and go back to the legacy constants.

    Refused while a runner is live, same rule as the wizard's adopt: every
    runner reads `active_profile` at spawn and compiles its presets from it,
    so moving it under a live run means the next restart runs a different
    thing than the one the operator is watching.
    """
    if _procs():
        return jsonify({"ok": False, "error": "runners alive - stop them first"}), 409
    body = request.get_json(force=True) or {}
    name = body.get("name")
    if name in (None, ""):
        # read-modify-write of config.yaml: same transaction discipline as a
        # profile patch, or two activates race and one loses its whole edit
        with _CONFIG_WRITE_LOCK:
            cfg = load_config()
            had = cfg.pop("active_profile", None)
            try:
                backup = save_config(cfg)
            except Exception as e:              # noqa: BLE001
                return jsonify({"ok": False, "error": str(e)}), 500
        return jsonify({"ok": True, "active": None, "backup": backup,
                        "message": (f"unbound {had!r} - the scheduler runs its "
                                    f"legacy constants") if had else
                                   "no profile was bound"})
    path = _profile_path(name)
    if not path or not os.path.exists(path):
        return jsonify({"ok": False, "error": f"no such profile: {name!r}"}), 400
    if _is_draft(name):
        return jsonify({"ok": False, "error": (
            f"{name} is a scan draft, not a runnable profile - complete it on "
            f"the Profile tab first")}), 400
    # A profile that EXISTS but is broken must not be bound: the runner would
    # raise at spawn instead of farming (playerprofile.select_profile). Read it
    # under ITS lock so a patch landing right now is seen whole, not half.
    try:
        with _profile_lock(path):
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        from player import playerprofile
        problems = playerprofile.validate(data if isinstance(data, dict) else {})
    except Exception as e:                      # noqa: BLE001
        return jsonify({"ok": False,
                        "error": f"cannot validate {name}: {type(e).__name__}: {e}"}), 400
    if problems:
        return jsonify({"ok": False, "problems": problems,
                        "error": f"{name} has {len(problems)} problem(s) - "
                                 f"fix them before activating"}), 400
    with _CONFIG_WRITE_LOCK:
        cfg = load_config()
        cfg["active_profile"] = name
        try:
            backup = save_config(cfg)
        except Exception as e:                  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "active": name, "backup": backup,
                    "message": f"config.yaml now runs profile {name!r}"})


@app.get("/api/evidence/<path:rel>")
def api_evidence(rel):
    cfg = load_config()
    inst = cfg.get("active_instance", "main")
    base = os.path.join(ROOT, "logs", inst, "scan_evidence")
    path = os.path.normpath(os.path.join(base, rel))
    if not path.startswith(base):
        return Response("no", status=403)
    if not os.path.exists(path):
        return Response("not found", status=404)
    return send_file(path, mimetype="image/png")


# ------------------------------------------------------------------ wizard
# The wizard's findings live SERVER-SIDE (user, 2026-08-18: "this does not
# persist the findings / state so when I refresh nothing is there"). Every
# probe result is stamped and written to logs/wizard_state.json; the page
# renders that file on load and only re-probes when a button is pressed.
_WIZ_STATE = os.path.join(ROOT, "logs", "wizard_state.json")
_WIZ_LOCK = threading.Lock()


def _wiz_load() -> dict:
    try:
        with open(_WIZ_STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:                           # noqa: BLE001
        return {}


def _wiz_save(section: str, payload: dict) -> dict:
    """Persist one wizard section. NEVER raises (audit, 2026-08-19): an
    unwritable logs/ used to turn a completed adopt - config already saved,
    device already connected - into a 500 the caller reads as "nothing
    happened". Failure is reported as a `warning` field on the payload."""
    payload = dict(payload, t=time.time())
    try:
        with _WIZ_LOCK:
            st = _wiz_load()
            st[section] = payload
            os.makedirs(os.path.dirname(_WIZ_STATE), exist_ok=True)
            tmp = _WIZ_STATE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(st, fh, indent=1)
            os.replace(tmp, _WIZ_STATE)
    except Exception as e:                      # noqa: BLE001 - display state
        payload = dict(payload, warning=f"wizard state not saved: {e}")
    return payload


@app.get("/api/wizard/state")
def api_wizard_state():
    return jsonify(_wiz_load())


# host:port (adb tcp) or the usb/emulator token form. Guards the one endpoint
# that WRITES a serial into config.yaml - a typo there points the farm at
# nothing, and the runner's abort would be the first sign.
_SERIAL_RE = re.compile(r"^(?:[A-Za-z0-9_.\-]+:\d{1,5}|emulator-\d+)$")


# SETUP GATE (user, 2026-08-19: "no other tabs should even be possible until
# the first config is scanned and we are ready to operate").
#
# The flag lives in its OWN sentinel file, NOT in wizard_state.json (audit,
# 2026-08-19): wizard_state.json is rewritten wholesale on every scan and
# `_wiz_load` turns a corrupt file into {} - which would silently erase the
# flag and relock a machine that has been farming for weeks. The sentinel is
# write-once, tiny, and read independently: corrupt or unwritable wizard
# state cannot touch it.
#
# Once set it is never auto-unset - an operating machine must keep Status
# reachable when its emulator is merely powered off.
_SETUP_FLAG = os.path.join(ROOT, "logs", "setup_done")
_SETUP_CACHE = {"done": False, "probe_failed_at": 0.0}
_PROBE_BACKOFF = 30.0       # seconds between failed grandfather probes
# Adopt's read-check-write must be atomic (audit round 2: two concurrent
# adopts both passed their preconditions and the last write won).
_ADOPT_LOCK = threading.Lock()


def _mark_setup_complete(how: str) -> None:
    if _SETUP_CACHE["done"] or os.path.exists(_SETUP_FLAG):
        _SETUP_CACHE["done"] = True
        return
    try:
        os.makedirs(os.path.dirname(_SETUP_FLAG), exist_ok=True)
        tmp = _SETUP_FLAG + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(f"{how}\n{datetime.datetime.now().isoformat(timespec='seconds')}\n")
        os.replace(tmp, _SETUP_FLAG)
    except Exception:                           # noqa: BLE001 - gate only
        pass                                    # in-memory flag still holds
    _SETUP_CACHE["done"] = True


def _setup_complete() -> bool:
    if _SETUP_CACHE["done"]:
        return True
    if os.path.exists(_SETUP_FLAG):
        _SETUP_CACHE["done"] = True
        return True
    # GRANDFATHER an already-working machine: this feature ships onto boxes
    # that have been farming for weeks and must not lock their own dashboard.
    # A configured serial whose port answers right now IS a completed setup.
    # BACKOFF (audit, 2026-08-19): /api/status is polled every 2.5 s by every
    # open tab; an unconfigured machine would pay the 0.3 s connect timeout on
    # every single poll forever. One failed probe suppresses the next 30 s.
    if time.time() - _SETUP_CACHE["probe_failed_at"] < _PROBE_BACKOFF:
        return False
    try:
        cfg = load_config()
        inst = (cfg.get("instances") or {}).get(
            cfg.get("active_instance", "main")) or {}
        serial = inst.get("serial") or ""
        if ":" not in serial:
            raise ValueError("no tcp serial configured")
        import socket
        host, _, port = serial.rpartition(":")
        with socket.create_connection((host, int(port)), timeout=0.3):
            pass
    except Exception:                           # noqa: BLE001 - not set up yet
        _SETUP_CACHE["probe_failed_at"] = time.time()
        return False
    _mark_setup_complete("grandfathered")
    return True


# INSTANCE INVENTORY via the emulator's OWN manager (user, 2026-08-18: "why
# doesn't it get the other 2 VMs"). `adb devices` and a port probe only see
# instances whose VM is up; a stopped instance is invisible to both. The
# managers list every configured instance regardless: MuMuManager
# `info -v all` (JSON, index/name/is_android_started, adb_port when up),
# LDPlayer `ldconsole list2`, BlueStacks `bluestacks.conf` instance keys.
# MuMu adb port when stopped: 16384 + 32*index (verified: config serial
# 16480 == index 3 "Main Tower-1"); flagged expected until the VM answers.
def _mumu_manager() -> str | None:
    pfs = [os.environ.get("ProgramFiles", r"C:\Program Files"),
           os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    for pf in pfs:
        for rel in (r"Netease\MuMuPlayer\nx_main\MuMuManager.exe",
                    r"Netease\MuMu Player 12\shell\MuMuManager.exe"):
            p = os.path.join(pf, rel)
            if os.path.exists(p):
                return p
    return None


def _adb_for_family(family: str) -> str | None:
    """First installed adb.exe belonging to an emulator FAMILY (MuMu /
    BlueStacks / LDPlayer). Used to suggest an adb path next to a suggested
    serial: a serial from MuMu's manager is worthless with BlueStacks' adb
    (different server protocol - they kill each other's daemon)."""
    key = (family or "").strip().lower().split(" ")[0]
    if not key:
        return None
    pfs = [os.environ.get("ProgramFiles", r"C:\Program Files"),
           os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    for label, pattern in EMULATOR_ADB_CANDIDATES:
        if pattern == "adb" or not label.lower().startswith(key):
            continue
        for pf in pfs:
            path = pattern.format(pf=pf)
            if os.path.exists(path):
                return path
    return None


def _emulator_instances() -> list[dict]:
    out: list[dict] = []
    mgr = _mumu_manager()
    if mgr:
        try:
            r = _run([mgr, "info", "-v", "all"], capture_output=True,
                     timeout=15, text=True)
            data = json.loads(r.stdout or "{}")
            # a single instance comes back as one object, several as a map
            items = ([data] if "index" in data else list(data.values()))
            for it in items:
                idx = int(it.get("index", -1))
                port = it.get("adb_port")
                out.append({
                    "emulator": "MuMu", "index": idx, "name": it.get("name"),
                    "running": bool(it.get("is_android_started")),
                    "process": bool(it.get("is_process_started")),
                    "adb_port": port or (16384 + 32 * idx if idx >= 0 else None),
                    "port_source": "manager" if port else "expected",
                    "manager": mgr})
        except Exception as e:                  # noqa: BLE001
            out.append({"emulator": "MuMu", "error": f"manager: {e}"})
    for pf in (os.environ.get("ProgramFiles", r"C:\Program Files"),):
        ld = os.path.join(pf, r"LDPlayer\LDPlayer9\ldconsole.exe")
        if os.path.exists(ld):
            try:
                r = _run([ld, "list2"], capture_output=True, timeout=15, text=True)
                for line in r.stdout.splitlines():
                    f = line.split(",")
                    if len(f) >= 5:
                        out.append({"emulator": "LDPlayer", "index": int(f[0]),
                                    "name": f[1], "running": f[4] == "1",
                                    "adb_port": 5555 + 2 * int(f[0]),
                                    "port_source": "expected", "manager": ld})
            except Exception as e:              # noqa: BLE001
                out.append({"emulator": "LDPlayer", "error": f"ldconsole: {e}"})
    bs = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                      r"BlueStacks_nxt\bluestacks.conf")
    if os.path.exists(bs):
        try:
            with open(bs, encoding="utf-8", errors="replace") as fh:
                txt = fh.read()
            for m in re.finditer(r'bst\.instance\.(\w+)\.display_name="([^"]*)"', txt):
                key, name = m.group(1), m.group(2)
                pm = re.search(rf'bst\.instance\.{key}\.adb_port="(\d+)"', txt)
                out.append({"emulator": "BlueStacks", "index": key, "name": name,
                            "running": None,
                            "adb_port": int(pm.group(1)) if pm else None,
                            "port_source": "conf" if pm else None})
        except Exception as e:                  # noqa: BLE001
            out.append({"emulator": "BlueStacks", "error": f"conf: {e}"})
    return out


@app.post("/api/wizard/launch")
def api_wizard_launch():
    """Start a stopped MuMu instance through MuMuManager (`control -v <i>
    launch`), then hand off to boot.py (user, 2026-08-21: "when we click
    launch it needs to do the pre-launching check for advertising and to
    launch the game as well"). The pipeline runs detached, same as a
    scan: wait for adb + Android, dismiss ad overlays (overlays.clean),
    start The Tower, verify a known screen. It only runs when a
    configured instance's serial matches the VM's adb port - a VM no
    instance drives gets the bare emulator launch, nothing more.
    Explicit user click only; refused while a runner (or an earlier
    boot pipeline) is live."""
    if _procs():
        return jsonify({"ok": False, "error": "runners alive - stop them first"}), 409
    body = request.get_json(force=True) or {}
    mgr = _mumu_manager()
    if body.get("emulator") != "MuMu" or not mgr:
        return jsonify({"ok": False, "error": "launch is wired for MuMu only"}), 400
    idx = str(int(body["index"]))
    try:
        r = _run([mgr, "control", "-v", idx, "launch"], capture_output=True,
                 timeout=30, text=True)
        msg = (r.stdout or r.stderr or "").strip()
        if r.returncode != 0:
            return jsonify({"ok": False, "message": msg})
        serial = f"127.0.0.1:{16384 + 32 * int(idx)}"   # MuMu's port scheme
        cfg = load_config()
        inst = next((n for n, i in (cfg.get("instances") or {}).items()
                     if isinstance(i, dict) and i.get("serial") == serial),
                    None)
        if inst:
            pyw = sys.executable.replace("python.exe", "pythonw.exe")
            subprocess.Popen(
                [pyw, os.path.join(ROOT, "device", "boot.py"),
                 "--instance", inst],
                cwd=ROOT,
                creationflags=subprocess.DETACHED_PROCESS | NO_WINDOW)
            msg += (f" - boot pipeline started for '{inst}': waits for "
                    "Android, clears ad overlays, launches the game")
        else:
            msg += (f" - no configured instance uses {serial}; "
                    "game auto-launch skipped")
        return jsonify({"ok": True, "message": msg})
    except Exception as e:                      # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/wizard/emulators")
def api_wizard_emulators():
    # PROBE GUARD (2026-08-18, learned live): running a FOREIGN adb.exe
    # restarts the shared adb server, and the configured emulator's transport
    # drops for a few seconds - a live runner then eats a CaptureError
    # mid-run (shard loop 18 did, recovered only thanks to _kick_adb). While
    # any runner is active, only the configured adb may be queried; the
    # others are still listed as installed, probe deferred.
    # FOREIGN adb binaries are NEVER run any more (2026-08-18, seen live):
    # BlueStacks' HD-Adb.exe speaks server protocol 36, MuMu's adb 41 - each
    # `devices` call kills the other's daemon ("server version doesn't
    # match; killing..."), the transport flaps to `offline` and a connect
    # attempted in that window fails. Only the configured adb touches the
    # daemon; other installs are listed as installed, ports come from the
    # manager inventory below.
    runners_live = True
    cfg_adb = os.path.normcase(load_config()["adb"]["exe"])
    found = []
    pfs = [os.environ.get("ProgramFiles", r"C:\Program Files"),
           os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    for label, pattern in EMULATOR_ADB_CANDIDATES:
        paths = ([pattern] if pattern == "adb"
                 else [pattern.format(pf=pf) for pf in pfs])
        for path in paths:
            if pattern != "adb" and not os.path.exists(path):
                continue
            if runners_live and os.path.normcase(path) != cfg_adb:
                found.append({"emulator": label, "adb": path, "devices": [],
                              "note": "installed - not probed (a foreign adb "
                                      "restarts the shared daemon); set adb.exe "
                                      "in Configuration to use it"})
                break
            try:
                out = _run([path, "devices"], capture_output=True,
                           timeout=10, text=True).stdout
            except Exception:                   # noqa: BLE001
                continue
            devices = [l.split("\t")[0] for l in out.splitlines()[1:]
                       if "\tdevice" in l]
            found.append({"emulator": label, "adb": path,
                          "devices": devices})
            break
    # WHAT IS ACTUALLY ON THIS MACHINE (user, 2026-08-18: "this has no idea
    # what devices I have"). `adb devices` answers only for instances the
    # daemon currently holds a transport to - an emulator whose VM is down or
    # restarting reports nothing, which reads as "no emulator" when the
    # emulator window is plainly open. So also report: running emulator
    # PROCESSES, and the CONFIGURED serial with a live port probe, so the
    # page can say "MuMu is running but its adb port 16480 refuses" instead
    # of "none".
    import socket
    running = []
    try:
        import psutil
        marks = {"MuMu": ("mumu",), "BlueStacks": ("hd-player", "bluestacks"),
                 "LDPlayer": ("dnplayer", "ldplayer")}
        seen = set()
        for pr in psutil.process_iter(["name"]):
            n = (pr.info["name"] or "").lower()
            for label, keys in marks.items():
                if label not in seen and any(k in n for k in keys):
                    running.append({"emulator": label, "process": pr.info["name"]})
                    seen.add(label)
    except Exception:                       # noqa: BLE001
        pass
    cfg = load_config()
    configured = []
    for name, inst in (cfg.get("instances") or {}).items():
        serial = (inst or {}).get("serial") or ""
        port_ok = None
        if ":" in serial:
            host, _, port = serial.rpartition(":")
            try:
                with socket.create_connection((host, int(port)), timeout=1.5):
                    port_ok = True
            except OSError:
                port_ok = False
        configured.append({"instance": name, "serial": serial,
                           "port_open": port_ok})
    instances = _emulator_instances()
    # STEP-3 SUGGESTION (user, 2026-08-19: step 3 told the user to hand-copy
    # the detected adb path and serial into the Configuration tab, which the
    # wizard already knows). Offered ONLY when the configured device is
    # unreachable AND exactly one live candidate exists: two candidates means
    # two accounts (main is 16480 here while a second VM may be up on 16384),
    # and silently picking one would send the farm to the wrong account.
    candidates: list[dict] = []
    seen_serials: set[str] = set()
    for i in instances:
        if i.get("running") is not True or not i.get("adb_port"):
            continue
        ser = f"127.0.0.1:{i['adb_port']}"
        if ser in seen_serials:
            continue
        seen_serials.add(ser)
        candidates.append({"serial": ser, "emulator": i.get("emulator"),
                           "name": i.get("name"),
                           "adb": _adb_for_family(i.get("emulator") or "")})
    for f in found:
        for dv in f.get("devices") or []:
            # `emulator-NNNN` counts too (audit, 2026-08-19): adopt accepts
            # the token, so excluding it here left a machine whose only
            # device is an AVD with no suggestion at all.
            if not _SERIAL_RE.match(dv) or dv in seen_serials:
                continue
            seen_serials.add(dv)
            candidates.append({"serial": dv, "emulator": f.get("emulator"),
                               "name": "daemon device", "adb": f.get("adb")})
    configured_down = all(c["port_open"] is not True for c in configured)
    suggestion = None
    if configured_down and len(candidates) == 1:
        # adb path is omitted when that family's binary is not on disk
        suggestion = {k: v for k, v in candidates[0].items() if v}
        suggestion["serial"] = candidates[0]["serial"]
    active = cfg.get("active_instance", "main")
    placeholder = not ((cfg.get("instances") or {}).get(active) or {}).get("serial")
    return jsonify(_wiz_save("emulators", {
        "scanned": found, "running": running, "configured": configured,
        "instances": instances, "suggestion": suggestion,
        "config_serial_placeholder": placeholder, "active_instance": active}))


@app.post("/api/wizard/reconnect")
def api_wizard_reconnect():
    """`adb connect <serial>` for a configured instance - the daemon-
    lifecycle spawn is sanctioned (CLAUDE.md), window-suppressed. Refused
    while a runner is live (a reconnect can bounce the transport)."""
    if _procs():
        return jsonify({"ok": False, "error": "runners alive - stop them first"}), 409
    body = request.get_json(force=True) or {}
    cfg = load_config()
    serial = body.get("serial") or cfg["instances"][
        cfg.get("active_instance", "main")]["serial"]
    adb = cfg["adb"]["exe"]
    try:
        out = _run([adb, "connect", serial], capture_output=True, timeout=10,
                   text=True)
        msg = (out.stdout or out.stderr or "").strip()
    except Exception as e:                  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500
    ok = "connected" in msg.lower() and "cannot" not in msg.lower()
    if ok:
        # the status poll's connection probe is cached 10 s - a successful
        # connect must be visible on the very next poll, not a cache TTL later
        _CONN_CACHE.update(t=time.time(), ok=True)
    return jsonify({"ok": ok, "message": msg})


@app.post("/api/wizard/adopt")
def api_wizard_adopt():
    """Step 3 of the wizard, done FOR the user (2026-08-19): write the
    discovered adb.exe + serial into config.yaml (timestamped backup, as
    every save here does) and `adb connect` the device. It used to be a
    paragraph asking the user to hand-copy two values the wizard already
    knew. Refused while a runner is live - repointing the config and
    bouncing the transport under a live run is how you lose a run.

    PRECONDITIONS (audit, 2026-08-19 - the auto-adopt TOCTOU): the client
    sends what it BELIEVED when it decided to adopt - `expect_instance` (the
    active instance the suggestion was computed for) and, on the automatic
    fresh-install path only, `expect_placeholder: true` (that instance had
    no serial at all). Config is re-read HERE, immediately before the write,
    and both are re-checked; anything moved in between - the user edited
    config.yaml, another tab adopted first, a second VM came up - is a 409,
    not an overwrite. Without this a stale scan could repoint a configured
    farm at the wrong account."""
    if _procs():
        return jsonify({"ok": False, "error": "runners alive - stop them first"}), 409
    body = request.get_json(force=True) or {}
    raw = body.get("serial")
    serial = raw.strip() if isinstance(raw, str) else ""
    if not serial or not _SERIAL_RE.match(serial):
        return jsonify({"ok": False,
                        "error": f"not a device serial: {raw!r}"}), 400
    # The whole read-check-write is one critical section (audit round 2: two
    # concurrent adopts both passed their preconditions and last-write-won).
    # One process serves this app, so a module lock IS the CAS.
    with _ADOPT_LOCK:
        # re-read INSIDE the lock, right before the write - never trust the
        # config snapshot the scan that produced this suggestion was built on
        cfg = load_config()
        inst = cfg.get("active_instance", "main")
        expect_inst = body.get("expect_instance")
        if expect_inst is not None and expect_inst != inst:
            return jsonify({"ok": False, "error": (
                f"active instance changed since the scan: expected "
                f"{expect_inst!r}, config now says {inst!r} - rescan")}), 409
        instances = cfg.get("instances")
        if not isinstance(instances, dict) or not isinstance(instances.get(inst), dict):
            # NEVER create an instance (audit): a serial-only stub has no
            # display, no preset, no allow_taps - a runner started on it
            # misbehaves in ways that look like a game bug. Instances are
            # authored in Configuration.
            return jsonify({"ok": False, "error": (
                f"instance {inst!r} does not exist in config.yaml - create it in "
                f"Configuration first (this endpoint never invents instances)")}), 400
        current = instances[inst].get("serial") or ""
        if body.get("expect_placeholder") and current:
            return jsonify({"ok": False, "error": (
                f"instance {inst!r} already has serial {current!r} - refusing the "
                f"automatic adopt (it only ever fills an EMPTY serial)")}), 409
        adb = body.get("adb")
        adb = adb.strip() if isinstance(adb, str) else ""
        if adb and os.path.exists(adb):
            cfg.setdefault("adb", {})["exe"] = adb
        instances[inst]["serial"] = serial
        try:
            backup = save_config(cfg)
        except Exception as e:                  # noqa: BLE001 - shown to user
            return jsonify({"ok": False, "error": str(e)}), 500
    # sanctioned daemon-lifecycle spawn (CLAUDE.md), window-suppressed by _run.
    # SKIPPED for `emulator-NNNN` serials: those are already-attached USB/
    # emulator transports, and `adb connect` only speaks host:port - calling
    # it on a token just returns a parse error that reads like a failure.
    adb_exe = (cfg.get("adb") or {}).get("exe") or ""
    connected = None
    if ":" not in serial:
        # `adb connect` does not apply to emulator-NNNN tokens - but a stale
        # device row must not fake a connection either (audit round 2: an
        # unverified token adopt could permanently unlock setup on a dead
        # transport). Prove the transport over the adb-server socket.
        try:
            from device import adbclient
            adbclient.exec_out(serial, "echo ok", timeout=3)
            msg = "attached transport verified over the adb-server socket"
            connected = True
        except Exception as e:                  # noqa: BLE001
            msg = f"transport {serial} did not answer: {e}"
            connected = False
    elif adb_exe:
        try:
            out = _run([adb_exe, "connect", serial], capture_output=True,
                       timeout=10, text=True)
            msg = (out.stdout or out.stderr or "").strip()
        except Exception as e:                  # noqa: BLE001
            msg = f"connect failed: {e}"
    else:
        msg = "no adb.exe configured - set adb.exe in Configuration"
    if connected is None:
        connected = "connected" in msg.lower() and "cannot" not in msg.lower()
    saved = _wiz_save("adopt", {"serial": serial, "adb": adb_exe,
                                "instance": inst, "backup": backup,
                                "connected": connected, "message": msg})
    # A successful connect proves the wiring - that is the setup gate's
    # second set-point (the first is a confirmed 1080x2560 in step 2).
    if connected:
        _mark_setup_complete("adopt")
    # ok = the config WAS saved; a failed connect is reported, never undone
    return jsonify(dict(saved, ok=True))


@app.get("/api/wizard/resolution")
def api_wizard_resolution():
    """`wm size` over the ADB SERVER SOCKET, never adb.exe (audit,
    2026-08-19). This runs after every successful adopt, so spawning a
    process here was a non-lifecycle adb.exe spawn on a path the user
    triggers repeatedly - exactly what CLAUDE.md forbids. Same pattern as
    /api/frame.png: adbclient is a pure socket client, and importing it
    breaks none of the module's rules (settings/capture/orchestrator stay out).

    The `adb` query parameter is ACCEPTED AND IGNORED - the socket talks to
    the one adb server on this machine, whichever binary started it. Kept so
    existing callers and bookmarked URLs keep working."""
    serial = request.args["serial"]
    try:
        from device import adbclient
        out = adbclient.exec_out(serial, "wm size", timeout=10).decode(
            "utf-8", "replace")
    except Exception as e:                      # noqa: BLE001
        return jsonify(_wiz_save("resolution", {
            "ok": False, "serial": serial,
            "error": f"{e} (no adb server, or the device is not attached - "
                     f"use adb connect above first)"}))
    m = re.search(r"(\d+)x(\d+)", out)
    if not m:
        return jsonify(_wiz_save("resolution", {
            "ok": False, "serial": serial, "error": out.strip() or "no answer"}))
    w, h = int(m.group(1)), int(m.group(2))
    if (w, h) == (1080, 2560):
        # the device answered at the calibrated resolution: wiring proven
        _mark_setup_complete("resolution")
    return jsonify(_wiz_save("resolution", {
        "ok": True, "serial": serial, "width": w, "height": h,
        "expected": w == 1080 and h == 2560,
        "note": ("" if (w, h) == (1080, 2560) else
                 "Templates are calibrated for 1080x2560. Set the"
                 " emulator display to exactly this resolution.")}))


@app.get("/api/wizard/templates")
def api_wizard_templates():
    tpl_root = os.path.join(ROOT, "templates")
    have = []
    for dirpath, _, files in os.walk(tpl_root):
        for f in files:
            if f.endswith(".png"):
                rel = os.path.relpath(os.path.join(dirpath, f), tpl_root)
                have.append(rel.replace("\\", "/"))
    # templates the code ASKED for and did not find, straight from the logs -
    # the only honest source of "what is missing on this machine"
    missing = set()
    for rows in (_newest_events(n=2000),):
        for r in rows:
            if r.get("kind") == "template_missing":
                missing.add(r.get("template"))
    return jsonify({"have": sorted(have), "missing_seen": sorted(missing)})


@app.get("/api/template/<path:rel>")
def api_template(rel):
    path = os.path.normpath(os.path.join(ROOT, "templates", rel))
    if not path.startswith(os.path.join(ROOT, "templates")):
        return Response("no", status=403)
    return send_file(path, mimetype="image/png")


# --------------------------------------------------- calibration: cropper
#
# Templates used to be cut by hand in an image editor. The cropper below is
# the same act with the mouse on the Calibrate page: drag a box on the live
# frame, name it, save. It is a HUMAN-driven write - no detector ever calls
# it (CLAUDE.md #5: detectors never overwrite their own templates), and an
# existing file is never replaced unless the request says `overwrite`.
_FRAME_CACHE: "dict[str, bytes]" = {}
_FRAME_CACHE_KEEP = 8
_FRAME_CACHE_LOCK = threading.Lock()


def _remember_frame(ts: str, raw: bytes) -> None:
    with _FRAME_CACHE_LOCK:
        _FRAME_CACHE[str(ts)] = raw
        while len(_FRAME_CACHE) > _FRAME_CACHE_KEEP:
            _FRAME_CACHE.pop(next(iter(_FRAME_CACHE)))


def _template_path(rel: str) -> str | None:
    """Absolute path under templates/ for a relative name, or None when the
    name is not a plain `<folder>/<name>.png` inside it."""
    if not isinstance(rel, str) or not rel.lower().endswith(".png"):
        return None
    rel = rel.replace("\\", "/")
    if rel.startswith("/") or ".." in rel.split("/") or ":" in rel or "/" not in rel:
        return None                     # every template lives in a subfolder
    root = os.path.normpath(os.path.join(ROOT, "templates"))
    path = os.path.normpath(os.path.join(root, rel))
    if not path.startswith(root + os.sep):
        return None
    return path


def _preset_slug(name: str) -> str:
    # interactions.presets._slug, restated (the dashboard imports no runner
    # module): preset names as the user typed them -> template file stems
    return "".join(c if c.isalnum() else "_" for c in str(name).strip().lower())


def _required_templates(cfg: dict, profile: dict | None) -> list[dict]:
    """Every ACCOUNT-SPECIFIC template the configured loadouts and the
    scanned presets need, with have/missing. The generic UI chrome ships with
    the repo; these are the ones only this account can provide: its card
    presets, its (renamed) global and category presets, its modules at its
    own rarity."""
    want: dict[str, dict] = {}

    def need(rel: str, feature: str, used_by: str) -> None:
        row = want.setdefault(rel, {"rel": rel, "feature": feature, "used_by": []})
        if used_by not in row["used_by"]:
            row["used_by"].append(used_by)

    cats = (("module_preset", "modules"), ("guardian_preset", "guardians"),
            ("workshop_preset", "workshop"), ("bot_preset", "bots"))
    for lname, lo in (cfg.get("loadouts") or {}).items():
        if not isinstance(lo, dict) or lo.get("defined") is False:
            continue
        src = f"loadout {lname}"
        for key in ("cards", "cards_restore"):
            if lo.get(key):
                need(f"cards/preset_{lo[key]}.png", "card preset tab", src)
        if lo.get("global_preset"):
            need(f"presets/gp_{_preset_slug(lo['global_preset'])}.png",
                 "global preset (picker row)", src)
        for key, cat in cats:
            if lo.get(key):
                need(f"presets/{cat}_{_preset_slug(lo[key])}.png",
                     f"{cat} preset (picker row)", src)
        for key in ("modules", "modules_restore"):
            for entry in lo.get(key) or []:
                slug = entry[0] if isinstance(entry, (list, tuple)) else entry
                if not slug:
                    continue
                need(f"modules/{slug}.png", "module icon (inventory grid)", src)
                need(f"modules/equipped/{slug}.png",
                     "module icon (equipped header)", src)
    player = (profile or {}).get("player") or {}
    for name in player.get("card_presets") or []:
        need(f"cards/preset_{name}.png", "card preset tab", "scanned account")
    for name in player.get("global_presets") or []:
        need(f"presets/gp_{_preset_slug(name)}.png",
             "global preset (picker row)", "scanned account")
    for cat, names in (player.get("category_presets") or {}).items():
        for name in names or []:
            need(f"presets/{cat}_{_preset_slug(name)}.png",
                 f"{cat} preset (picker row)", "scanned account")
    for slug in list(player.get("modules_equipped") or []):
        need(f"modules/{slug}.png", "module icon (inventory grid)", "scanned account")
        need(f"modules/equipped/{slug}.png", "module icon (equipped header)",
             "scanned account")
    if player.get("global_presets"):
        need("presets/picker_icon.png", "global preset picker button", "scanned account")
    out = []
    for rel in sorted(want):
        path = _template_path(rel)
        row = want[rel]
        row["have"] = bool(path and os.path.exists(path))
        out.append(row)
    return out


@app.get("/api/wizard/required")
def api_wizard_required():
    cfg = load_config()
    profile = None
    try:
        name = cfg.get("active_profile")
        path = _profile_path(name) if name else None
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                profile = yaml.safe_load(fh)
    except Exception:                           # noqa: BLE001 - listing only
        profile = None
    rows = _required_templates(cfg, profile)
    return jsonify({"required": rows,
                    "missing": [r["rel"] for r in rows if not r["have"]]})


@app.post("/api/template/<path:rel>")
def api_template_save(rel):
    """Cut a template out of a frame the browser displayed.

    Body: {ts, x, y, w, h, overwrite?} - native 1080x2560 pixels; `ts` is the
    query value the frame was fetched with (/api/frame.png?ts=...), so the
    crop comes from that exact frame. Returns the crop size and how close the
    NEXT-best match on the same frame comes (a good template matches itself
    at 1.0 and nothing else near it)."""
    import numpy as np
    import cv2
    path = _template_path(rel)
    if not path:
        return jsonify({"ok": False, "error": "template name must be "
                        "<folder>/<name>.png inside templates/"}), 400
    body = request.get_json(force=True) or {}
    try:
        x, y = int(body["x"]), int(body["y"])
        w, h = int(body["w"]), int(body["h"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"ok": False, "error": "x, y, w, h are required"}), 400
    with _FRAME_CACHE_LOCK:
        raw = _FRAME_CACHE.get(str(body.get("ts")))
    if raw is None:
        return jsonify({"ok": False, "error": "that frame is no longer cached - "
                        "refresh the live screen and draw the box again"}), 409
    frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return jsonify({"ok": False, "error": "cached frame did not decode"}), 500
    fh_, fw_ = frame.shape[:2]
    if w < 6 or h < 6 or x < 0 or y < 0 or x + w > fw_ or y + h > fh_:
        return jsonify({"ok": False, "error": f"box {x},{y} {w}x{h} is outside "
                        f"the {fw_}x{fh_} frame or smaller than 6px"}), 400
    if os.path.exists(path) and not body.get("overwrite"):
        return jsonify({"ok": False, "exists": True,
                        "error": f"{rel} exists - tick overwrite to replace it"}), 409
    crop = frame[y:y + h, x:x + w].copy()
    if float(crop.std()) < 2.0:
        # a flat-colour box has no detail to match - TM_CCOEFF_NORMED on it
        # is undefined and scores 1.0 everywhere
        return jsonify({"ok": False, "error": "the box is a flat colour - "
                        "include an edge or some text"}), 400
    # uniqueness on the source frame: best score with the crop's own area
    # masked out (the self-match is 1.0 by construction)
    res = cv2.matchTemplate(frame, crop, cv2.TM_CCOEFF_NORMED)
    y0, y1 = max(0, y - h + 1), min(res.shape[0], y + h)
    x0, x1 = max(0, x - w + 1), min(res.shape[1], x + w)
    res[y0:y1, x0:x1] = -1.0
    second = float(res.max()) if res.size else 0.0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not cv2.imwrite(path, crop):
        return jsonify({"ok": False, "error": "could not write the PNG"}), 500
    return jsonify({"ok": True, "rel": rel.replace("\\", "/"), "width": w,
                    "height": h, "second_best": round(second, 3)})


# ------------------------------------------- account scan -> profile
@app.post("/api/profile-promote")
def api_profile_promote():
    """Turn a scan draft (profiles/<inst>.draft.yaml, `player:` only) into a
    runnable profile: the draft's `player:` block on top of the starter's
    blueprints, policies and plan, written to profiles/<name>.yaml.

    The result is validated and the problems are RETURNED, not enforced: an
    account that owns fewer weapons than the starter assumes will fail a
    blueprint or two, and the fix is an edit to that profile, which needs
    the file to exist first. Nothing is activated here."""
    body = request.get_json(force=True) or {}
    draft = str(body.get("draft") or "").strip()
    for suffix in (".draft.yaml", ".draft", ".yaml"):
        if draft.casefold().endswith(suffix):
            draft = draft[:-len(suffix)]
    name = str(body.get("name") or "").strip()
    base = str(body.get("base") or "default").strip()
    if not draft or not _PROFILE_NAME_RE.fullmatch(draft):
        return jsonify({"ok": False, "error": "draft: instance name expected"}), 400
    if not _PROFILE_NAME_RE.fullmatch(name) or name.casefold().endswith(".draft"):
        return jsonify({"ok": False, "error": "name: letters, digits, _ - . only"}), 400
    draft_path = os.path.join(_profiles_dir(), f"{draft}.draft.yaml")
    base_path = _profile_path(base)
    dest = _profile_path(name)
    if not os.path.exists(draft_path):
        return jsonify({"ok": False, "error": f"no draft for {draft!r} - run the "
                        "account scan first"}), 400
    if not base_path or not os.path.exists(base_path) or _is_draft(base):
        return jsonify({"ok": False, "error": f"base profile {base!r} not found"}), 400
    if not dest:
        return jsonify({"ok": False, "error": "bad profile name"}), 400
    if os.path.exists(dest) and not body.get("overwrite"):
        return jsonify({"ok": False, "exists": True,
                        "error": f"profiles/{name}.yaml exists - tick overwrite"}), 409
    with open(draft_path, encoding="utf-8") as fh:
        draft_doc = yaml.safe_load(fh) or {}
    with open(base_path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    player = draft_doc.get("player")
    if not isinstance(player, dict) or not player:
        return jsonify({"ok": False, "error": "the draft has no player: block"}), 400
    player = dict(player)
    # the scanner only asserts abilities it SAW (scan.py --battle); anything
    # else stays unverified, exactly as the schema demands
    if player.get("abilities_verified") is not True:
        player["abilities_verified"] = False
    player.setdefault("wall", True)
    player.setdefault("max_tier", (doc.get("player") or {}).get("max_tier", 14))
    doc["player"] = player
    doc.pop("_name", None)
    doc.pop("_path", None)
    try:
        from player import playerprofile
        check = dict(doc)
        check["_name"] = name
        problems = playerprofile.validate(check)
    except Exception as e:                      # noqa: BLE001
        problems = [f"validator unavailable: {type(e).__name__}: {e}"]
    header = (f"# Promoted from profiles/{draft}.draft.yaml over profiles/{base}.yaml"
              f" on {datetime.datetime.now():%Y-%m-%d %H:%M}.\n"
              "# Machine-specific and git-ignored.\n")
    text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True,
                          default_flow_style=None)
    yaml.safe_load(text)
    with _profile_lock(dest):
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(header + text)
    return jsonify({"ok": True, "name": name, "path": dest, "problems": problems,
                    "abilities_verified": player["abilities_verified"]})


if __name__ == "__main__":
    # threaded: the MJPEG stream holds a connection open for as long as the
    # tab is; single-threaded Flask would block every other request behind it
    _start_procs_refresher()
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
