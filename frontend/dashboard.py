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
import shutil
import re
import struct
import subprocess
import sys
import threading
from functools import wraps
import time

import yaml
from flask import Flask, Response, jsonify, redirect, request, send_file, g

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

# Serialize dashboard mutations, including the short clicker worker. A config
# or account switch must not race a screenshot-verified click on the old device.
_ACTION_LOCK = threading.Lock()


@app.before_request
def serialize_actions():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if not _ACTION_LOCK.acquire(blocking=False):
            return jsonify({"ok": False, "error": "Another action is in progress; wait for its result"}), 409
        g.action_lock = True


@app.teardown_request
def release_action_lock(_error):
    if getattr(g, "action_lock", False):
        g.action_lock = False
        _ACTION_LOCK.release()


@app.get("/api/clicker/actions")
def api_clicker_actions():
    from player.clicker import ACTIONS
    return jsonify({"actions": [{"id": key, **value} for key, value in ACTIONS.items()]})


@app.post("/api/clicker/step")
def api_clicker_step():
    from player.clicker import ACTIONS
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    cfg = load_config()
    inst = cfg.get("active_instance", "main")
    if action not in ACTIONS or body.get("instance") != inst or body.get("account") != cfg.get("instances", {}).get(inst, {}).get("account"):
        return jsonify({"ok": False, "error": "Invalid action or the active emulator changed"}), 400
    if _procs():
        return jsonify({"ok": False, "error": "Stop automation or calibration before using the clicker"}), 409
    if body.get("execute") and not _taps_allowed(cfg):
        return jsonify({"ok": False, "error": "Allow automation taps in Control first"}), 409
    args = [sys.executable.replace("pythonw.exe", "python.exe"),
            os.path.join(ROOT, "player", "clicker.py"), "--instance", inst, "--action", action]
    if body.get("execute") is True:
        args.append("--execute")
    try:
        result = _run(args, cwd=ROOT, capture_output=True, text=True, timeout=45)
        lines = result.stdout.strip().splitlines()
        data = json.loads(lines[-1]) if lines else {"ok": False, "reason": "Clicker worker returned no result"}
        return jsonify({"ok": True, "result": data})
    except (subprocess.TimeoutExpired, ValueError) as exc:
        return jsonify({"ok": False, "error": "Check did not finish. Inspect the game before trying again: " + str(exc)[:150]})


@app.get("/api/clicker/frame")
def api_clicker_frame():
    return send_file(os.path.join(_calibration_dir(load_config()), "clicker_preview.png"), mimetype="image/png", max_age=0)


@app.get("/api/manifest/contribution")
def api_manifest_contribution():
    from player.bootstrap_layout import manifest
    from player.manifest_contribution import export, discovery_candidates
    try:
        with open(os.path.join(_calibration_dir(load_config()), "calibrate_report.json"), encoding="utf-8") as fh:
            report = json.load(fh)
    except (OSError, ValueError):
        report = {}
    payload = export(manifest(), report)
    try:
        directory = _calibration_dir(load_config())
        with open(os.path.join(directory, 'screen_analysis.json'), encoding='utf-8') as fh:
            analysis = json.load(fh)
        names = set()
        for path in glob.glob(os.path.join(directory, 'asset_library', '*', 'index.json')):
            with open(path, encoding='utf-8') as fh:
                names.update(item['name'] for item in json.load(fh).get('images', []) if item.get('name'))
        payload['discovery_candidates'] = discovery_candidates(manifest(), analysis, names)
    except (OSError, ValueError):
        payload['discovery_candidates'] = []
    return Response(json.dumps(payload, indent=2), mimetype="application/json",
                    headers={"Content-Disposition": 'attachment; filename="manifest-contribution.json"'})

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


_COMPILER_LOCK = threading.RLock()
_CONFIG_WRITE_LOCK = threading.RLock()


def _serialize_config(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with _CONFIG_WRITE_LOCK:
            return fn(*args, **kwargs)
    return wrapped


def _profile_check(profile, cfg=None, warnings=False):
    from player import playerprofile as pp
    with _COMPILER_LOCK:
        previous = pp.CONFIG
        pp.CONFIG = cfg if cfg is not None else load_config()
        try:
            return (pp.warnings if warnings else pp.validate)(profile)
        finally:
            pp.CONFIG = previous


def _profile_warnings(profile):
    return _profile_check(profile, warnings=True)


def _compiled_runs(cfg):
    """Compile the active account without binding settings to its device."""
    from player import playerprofile as pp
    import flows
    name = cfg.get("active_profile")
    if not name:
        return dict(cfg.get("presets") or {})
    path = _profile_path(name)
    if not path or not os.path.isfile(path):
        raise ValueError("Choose a valid profile in Setup")
    with open(path, encoding="utf-8") as fh:
        profile = yaml.safe_load(fh)
    profile["_name"] = name
    with _COMPILER_LOCK:
        previous = pp.CONFIG
        pp.CONFIG = cfg
        try:
            problems = pp.validate(profile)
            if problems:
                raise ValueError("; ".join(problems))
            out = dict(cfg.get("presets") or {})
            for key in profile.get("blueprints") or {}:
                compiled = pp.compile_preset(profile, key)
                compiled["runner"] = flows.script(compiled["kind"])
                out["bp_" + key] = compiled
            if profile.get("plan") and "combo" in (cfg.get("presets") or {}):
                out["combo"] = cfg["presets"]["combo"]
            else:
                out.pop("combo", None)
            return out
        finally:
            pp.CONFIG = previous


@app.get("/api/runs")
def api_runs():
    try:
        cfg = load_config()
        runs = _display_runs(cfg)
        if cfg.get("active_profile"):
            runs = {k: v for k, v in runs.items() if k.startswith("bp_") or k == "combo"}
        return jsonify({"runs": runs, "readiness": _runs_readiness(cfg, runs)})
    except Exception as e:
        return jsonify({"runs": {}, "error": str(e)}), 400


def _display_runs(cfg):
    """Keep saved runs editable before ownership checks can pass.

    Launch paths still use _compiled_runs and retain strict validation.
    """
    try:
        return _compiled_runs(cfg)
    except ValueError:
        path = _profile_path(cfg.get("active_profile"))
        if not path or not os.path.isfile(path):
            raise
        with open(path, encoding="utf-8") as fh:
            profile = yaml.safe_load(fh)
        from player import playerprofile as pp
        import flows
        import copy
        out = {}
        with _COMPILER_LOCK:
            previous = pp.CONFIG
            pp.CONFIG = cfg
            try:
                for key, bp in (profile.get("blueprints") or {}).items():
                    single = copy.deepcopy(profile)
                    single["blueprints"] = {key: bp}
                    single.pop("plan", None)
                    problems = pp.validate(single)
                    try:
                        item = pp.compile_preset(single, key)
                        item["runner"] = flows.script(item["kind"])
                    except Exception as exc:
                        item = dict(bp)
                        problems.append(str(exc))
                    item["_setup_errors"] = problems
                    out["bp_" + key] = item
            finally:
                pp.CONFIG = previous
        return out


def _runs_readiness(cfg, runs):
    """Per-run calibration state for the run picker: each run type needs its
    own set of images to click (its flow templates, gather features, presets,
    plus the shared recognition guards), so a run whose images exist starts
    and one whose images are missing is greyed out with the list to scan -
    independent of what any OTHER run type still lacks. `combo` schedules the
    others and is not checked here."""
    from player import readiness
    out = {}
    for name, body in runs.items():
        if name == "combo" or not isinstance(body, dict):
            continue
        if body.get("_setup_errors"):
            out[name] = {"ready": False, "setup_required": True, "missing": ["Confirm this run's equipment and abilities after scanning."],
                         "details": [], "advisory": [], **_setup_next_action(cfg, body)}
            continue
        try:
            r = readiness.check(ROOT, cfg, body)
            out[name] = {"ready": r["ready"],
                         "missing": [" or ".join(row["alternatives"]) or "; ".join(row["reasons"])
                                     for row in r["missing"]],
                         # one row per missing image for the Control card: the
                         # plain-language card (what / where / how), why this
                         # run needs it, and which scan step covers it
                         "details": [{"alternatives": row["alternatives"], "reasons": row["reasons"],
                                      "docs": row.get("docs") or []} for row in r["missing"]],
                         "advisory": [" or ".join(row["alternatives"]) for row in r.get("advisory", [])]}
        except Exception as e:                  # noqa: BLE001 - one bad run must not hide the rest
            out[name] = {"ready": False, "missing": [f"readiness check failed: {e}"], "advisory": []}
    return out


def _setup_next_action(cfg, body):
    """One actionable step; saved discoveries are separate from run validation."""
    try:
        with open(os.path.join(_calibration_dir(cfg), "calibrate_state.json"), encoding="utf-8") as fh:
            observed = json.load(fh).get("player") or {}
        with open(_profile_path(cfg.get("active_profile")), encoding="utf-8") as fh:
            saved = (yaml.safe_load(fh) or {}).get("player") or {}
        for key in ("card_presets", "global_presets", "category_presets", "uws", "abilities"):
            value = observed.get(key)
            if value and (any(saved.get(key, {}).get(k) != v for k, v in value.items())
                          if isinstance(value, dict) else saved.get(key) != value):
                return dict(next_action="apply_scan", message="Your menu scan is complete. Save its discoveries to this account.", action_label="Use completed scan")
    except (OSError, ValueError, TypeError):
        pass
    errors = " ".join(body.get("_setup_errors") or [])
    if any(word in errors for word in ("chain_lightning", "abilities", "wall", "uws")):
        return dict(next_action="battle_setup", message="Menu setup is complete. This run still needs battle controls and abilities verified.", action_label="Prepare battle controls")
    return dict(next_action="edit_run", message="Choose equipment available on this account for this run.", action_label="Choose run equipment")


@app.get("/api/readiness")
def api_readiness():
    from player import readiness
    cfg = load_config()
    try:
        runs = _display_runs(cfg)
        name = request.args.get("preset") or next((n for n in runs if n.startswith("bp_")), None) or next((n for n in runs if n != "combo"), None)
        if not name or name not in runs or name == "combo":
            raise ValueError("Choose an individual run to check calibration")
        if runs[name].get("_setup_errors"):
            return jsonify(ready=False, preset=name, missing=[],
                           **_setup_next_action(cfg, runs[name]),
                           diagnostics=runs[name]["_setup_errors"])
        return jsonify(dict(readiness.check(ROOT, cfg, runs[name]), preset=name))
    except Exception as e:
        return jsonify({"ready": False, "error": str(e), "missing": []}), 400


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
        from player import accounts
        return accounts.effective(yaml.safe_load(fh))


def save_config(data: dict) -> str:
    from player import accounts
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        original = yaml.safe_load(fh)
    data = accounts.persist(data, original)
    return _save_yaml_backup(CONFIG_PATH, data)


def _calibration_dir(cfg=None):
    from player import accounts
    return str(accounts.calibration_dir(ROOT, cfg if cfg is not None else load_config()))


@app.get("/api/accounts")
def api_accounts():
    from player import accounts
    cfg = load_config()
    max_tier = 1
    try:
        with open(_profile_path(cfg.get("active_profile")), encoding="utf-8") as fh:
            max_tier = (yaml.safe_load(fh).get("player") or {}).get("max_tier", 1)
    except (OSError, TypeError, ValueError):
        pass
    return jsonify({"active": accounts.identity(cfg),
                    "max_tier": max_tier,
                    "has_personal_settings": cfg.get("active_profile") not in (None, "default") or any(isinstance(v, dict) and any(k != "defined" for k in v) for v in cfg.get("loadouts", {}).values()),
                    "instance": cfg.get("active_instance", "main"),
                    "accounts": [{"id": k, "label": v.get("label", k)}
                                 for k, v in (cfg.get("accounts") or {}).items()]})


@app.post("/api/accounts")
@_serialize_config
def api_account_select():
    """Create/bind an account only while automation is idle. Never moves files."""
    import copy
    from pathlib import Path
    from player import accounts
    if _procs():
        return jsonify({"ok": False, "error": "Stop automation before changing accounts."}), 409
    body = request.get_json(force=True) or {}
    try:
        name = accounts.valid_id(body.get("id")).lower()
        cfg = load_config()
        library = cfg.setdefault("accounts", {})
        if body.get("create"):
            if name in library:
                raise ValueError("An account with that identifier already exists")
            if body.get("import_current"):
                record = {k: copy.deepcopy(cfg.get(k)) for k in accounts.FIELDS}
                profile_name = f"account_{name}"
                source_profile = _profile_path(cfg.get("active_profile") or "default")
                target_profile = Path(ROOT) / "profiles" / f"{profile_name}.yaml"
                if target_profile.exists():
                    raise ValueError("The account's profile already exists; choose another identifier")
                if not source_profile or not Path(source_profile).is_file():
                    raise ValueError("The current profile is missing; create a fresh account instead")
                shutil.copy2(source_profile, target_profile)
                record["active_profile"] = profile_name
            else:
                profile_name = f"account_{name}"
                path = Path(ROOT) / "profiles" / f"{profile_name}.yaml"
                if path.exists():
                    raise ValueError("The new account's profile already exists; choose another identifier")
                starter = yaml.safe_load((Path(ROOT) / "profiles" / "default.yaml").read_text(encoding="utf-8"))
                from player.run_templates import starter_runs
                starter = starter_runs(starter)
                starter.pop("plan", None)
                starter["policies"]["chores"] = []
                path.write_text(yaml.safe_dump(starter, sort_keys=False, allow_unicode=True), encoding="utf-8")
                record = {"active_profile": profile_name, "loadouts": {"my_equipment": {}}, "tourney_card_tweaks": {}}
            record["label"] = str(body.get("label") or name).strip()[:100]
            library[name] = record
            if body.get("import_current"):
                # Copy this installation's current assets; do not take them
                # away from a running process or the legacy configuration.
                source_cfg = copy.deepcopy(cfg)
                target_cfg = copy.deepcopy(cfg)
                target_cfg["instances"][cfg["active_instance"]]["account"] = name
                target = accounts.template_dir(ROOT, target_cfg)
                target.mkdir(parents=True, exist_ok=True)
                for source in accounts.template_files(ROOT, source_cfg, "**/*.png"):
                    base = accounts.template_dir(ROOT, source_cfg)
                    try:
                        rel = source.relative_to(base)
                    except ValueError:
                        rel = source.relative_to(Path(ROOT) / "templates")
                    dest = target / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, dest)
                history = accounts.calibration_dir(ROOT, source_cfg)
                destination = accounts.calibration_dir(ROOT, target_cfg)
                for filename in ("scan_state.json", "calibrate_state.json", "calibrate_report.json"):
                    if (history / filename).is_file():
                        shutil.copy2(history / filename, destination / filename)
                for dirname in ("scan_evidence", "calibrate_evidence"):
                    if (history / dirname).is_dir():
                        shutil.copytree(history / dirname, destination / dirname)
        elif name not in library:
            raise ValueError("Choose an existing account or create one first")
        cfg["instances"][cfg["active_instance"]]["account"] = name
        cfg = accounts.effective(cfg)
        save_config(cfg)
        with _FRAME_CACHE_LOCK:
            _FRAME_CACHE.clear()
        _CALIB_CACHE.update(t=0, missing=[])
        return jsonify({"ok": True, "active": name})
    except (ValueError, OSError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.post("/api/automation-access")
def api_automation_access():
    if _procs():
        return jsonify({"ok": False, "error": "Stop automation before changing its tap permission."}), 409
    body = request.get_json(force=True) or {}
    if type(body.get("enabled")) is not bool:
        return jsonify({"ok": False, "error": "Choose whether to allow automation to tap."}), 400
    with _CONFIG_WRITE_LOCK:
        cfg = load_config()
        cfg["instances"][cfg["active_instance"]]["allow_taps"] = body["enabled"]
        save_config(cfg)
    return jsonify({"ok": True, "enabled": body["enabled"]})


@app.post("/api/account-tier")
def api_account_tier():
    if _procs():
        return jsonify({"ok": False, "error": "Stop automation before changing account capabilities."}), 409
    cfg = load_config()
    tier = (request.get_json(force=True) or {}).get("max_tier")
    if type(tier) is not int or not 1 <= tier <= 20:
        return jsonify({"ok": False, "error": "Enter an unlocked tier from 1 to 20."}), 400
    name = cfg.get("active_profile")
    path = _profile_path(name)
    if _is_starter(name) or not path or not os.path.isfile(path):
        return jsonify({"ok": False, "error": "Create your account first."}), 409
    with _profile_lock(path):
        with open(path, encoding="utf-8") as fh:
            profile = yaml.safe_load(fh)
        profile.setdefault("player", {})["max_tier"] = tier
        profile["player"]["max_tier_verified_by"] = "operator"
        problems = _profile_check(profile, cfg)
        if problems:
            return jsonify({"ok": False, "error": problems[0]}), 409
        _save_yaml_backup(path, profile)
    return jsonify({"ok": True, "max_tier": tier})


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
            m = re.search(r"(gem_collector|orchestrator|shard|combo|tourney|quest_\w+|hp_probe|"
                          r"scan|calibrate|clicker|boot|dashboard)\.py", cl)
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
            m = re.search(r"(gem_collector|orchestrator|shard|combo|tourney|quest_\w+|hp_probe|"
                          r"scan|calibrate|clicker|boot|dashboard)\.py", cl)
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
    from player.scan_plan import control_gate
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
        "calibration": control_gate(ROOT, cfg),
        # nav gates: Control is locked until calibration has nothing missing,
        # and shows a connect overlay while the emulator port is not answering
        "connected": _connected(),
        "calibrate_missing": _calibrate_missing(),
    })


_DISPLAY_CACHE: dict = {}          # serial -> (game display id or None, derived at)
_DISPLAY_TTL = 300.0


def _game_display(serial, configured=None):
    """The display the dashboard's own captures must ask for.

    MuMu runs the game on a secondary display whose id CHANGES on every
    emulator restart, and adopt writes only serial + adb - so the preview,
    the live stream and the screen check all read the default display (the
    landscape launcher, prefixed by a text warning) until this derived it
    (2026-09-08: "no frame", a stream that never refreshed, a resolution of
    "[War" x "ning"). Same derivation as capture.refresh_display, cached
    briefly per serial; `_forget_display` drops it when a capture stops
    making sense so the next call re-derives. None = single display."""
    if configured:
        return configured
    hit = _DISPLAY_CACHE.get(serial)
    if hit and time.time() - hit[1] < _DISPLAY_TTL:
        return hit[0]
    from device import adbclient, displays
    try:
        disp, _logical = displays.game_display(
            lambda cmd: adbclient.shell(serial, cmd, timeout=10).decode(errors="replace"))
    except Exception:                           # noqa: BLE001 - offline: no display
        return None
    _DISPLAY_CACHE[serial] = (disp, time.time())
    return disp


def _forget_display(serial):
    _DISPLAY_CACHE.pop(serial, None)


def _screencap_cmd(serial, display, png=True):
    return ("screencap -p" if png else "screencap") + (f" -d {display}" if display else "")


@app.get("/api/frame.png")
def api_frame():
    cfg = load_config()
    inst = cfg["instances"][cfg.get("active_instance", "main")]
    serial = request.args.get("serial") or inst["serial"]
    # -d: MuMu runs the game on a secondary display; without the id the
    # screencap answers with a warning instead of pixels (same as capture.py)
    display = request.args.get("display") or _game_display(serial, inst.get("display"))
    cmd = _screencap_cmd(serial, display)
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
            _run([adb, "start-server"], capture_output=True, timeout=15)
            adbclient.reconnect(serial)
            raw = adbclient.exec_out(serial, cmd, timeout=15)
        except Exception as e:                  # noqa: BLE001
            return Response(f"capture failed: {e}", status=502)
    from device import displays
    raw = displays.strip_warning(raw)
    if not raw.startswith(displays.PNG_MAGIC):
        _forget_display(serial)                 # display churn: re-derive next time
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
    asked_display = request.args.get("display") or inst.get("display")
    fps = max(0.5, min(float(request.args.get("fps", 2)), 4.0))
    quality = max(30, min(int(request.args.get("q", 70)), 90))
    scale = max(0.2, min(float(request.args.get("scale", 0.5)), 1.0))

    def gen():
        from device import adbclient, displays
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
                # the game display is derived per frame (cached): MuMu's ids
                # change across restarts and a stale one shows the launcher
                cmd = _screencap_cmd(serial, _game_display(serial, asked_display))
                raw = displays.strip_warning(adbclient.exec_out(serial, cmd, timeout=2.0))
                fails = 0
                if not raw.startswith(displays.PNG_MAGIC):
                    _forget_display(serial)
                    raise OSError("no PNG from the device (display changed?)")
                if raw.startswith(displays.PNG_MAGIC):
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
    if action == "start_gems":
        if _procs():
            return jsonify(ok=False,error="Automation is already running. Stop it before starting the diamond collector."),409
        if not cfg.get("instances",{}).get(inst,{}).get("allow_taps"):
            return jsonify(ok=False,error="Enable automation taps first."),409
        name=body.get("preset")
        runs=_compiled_runs(cfg)
        if name not in runs:
            return jsonify(ok=False,error="Choose a run's diamond settings."),400
        gather=runs[name].get("gather") or {}
        if not (gather.get("ad_gems",True) or (gather.get("gem_orbit") or {}).get("enabled")):
            return jsonify(ok=False,error="Enable Ad Gems or timed orbit clicks in this run's gathering settings."),409
        from player import accounts
        if gather.get("ad_gems",True) and not accounts.template_path(ROOT,cfg,"buttons/ad_gems_claim.png").exists():
            return jsonify(ok=False,error="Capture the HUD Ad Gems claim button first."),409
        cmd=[sys.executable.replace("python.exe","pythonw.exe"),os.path.join(ROOT,"gem_collector.py"),"--instance",inst,"--preset",name]
        child=subprocess.Popen(cmd,cwd=ROOT,creationflags=subprocess.DETACHED_PROCESS | NO_WINDOW)
        import time as _t
        _t.sleep(1.5)
        if child.poll() is not None:
            return jsonify(ok=False,error="Diamond collector exited; check its log."),500
        _procs_refresh()
        return jsonify(ok=True,pid=child.pid)
    if action == "start":
        preset = body.get("preset")
        if _procs():
            return jsonify({"ok": False, "error": "Automation is already running. Finish or stop it before starting another run."}), 409
        try:
            from player import readiness
            cfg["presets"] = _compiled_runs(cfg)
            if preset in cfg["presets"] and preset != "combo":
                result = readiness.check(ROOT, cfg, cfg["presets"][preset])
                if not result["ready"]:
                    return jsonify(dict(result, ok=False, error="Calibration is needed for this run. Open Calibrate to see the missing steps.")), 409
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400
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


# ------------------------------------------------------------ calibrator
def _taps_allowed(cfg: dict) -> bool:
    inst = cfg.get("active_instance", "main")
    return bool(((cfg.get("instances") or {}).get(inst) or {}).get("allow_taps"))


_analysis_jobs = {}
_analysis_lock = threading.Lock()


@app.post("/api/screen-analysis/start")
def api_screen_analysis_start():
    cfg = load_config()
    directory = _calibration_dir(cfg)
    with _analysis_lock:
        previous = _analysis_jobs.get(directory)
        if previous and previous.poll() is None:
            return jsonify({"error": "Screen analysis is already running"}), 409
        os.makedirs(directory, exist_ok=True)
        from player.asset_library import atomic_json
        from pathlib import Path
        atomic_json(Path(directory)/"screen_analysis.json", {"status":"running", "message":"Starting screen analysis"})
        pyw = sys.executable.replace("python.exe", "pythonw.exe")
        process = subprocess.Popen([pyw, "-m", "player.screen_analysis", "--instance", cfg.get("active_instance", "main")],
                                   cwd=ROOT, creationflags=NO_WINDOW)
        _analysis_jobs[directory] = process
    return jsonify({"ok":True})


@app.get("/api/screen-analysis/status")
def api_screen_analysis_status():
    directory = _calibration_dir(load_config())
    try:
        with open(os.path.join(directory, "screen_analysis.json"), encoding="utf-8") as fh:
            result = json.load(fh)
    except (OSError, ValueError):
        result = {"status":"idle"}
    process = _analysis_jobs.get(directory)
    if result.get("status") == "running" and (process is None or process.poll() is not None):
        result.update(status="error", message="Analysis was interrupted. Start a new screen analysis.")
    return jsonify(result)


@app.get("/api/screen-analysis/frame.png")
def api_screen_analysis_frame():
    return send_file(os.path.join(_calibration_dir(load_config()), "screen_analysis.png"), mimetype="image/png", max_age=0)


@app.post("/api/calibrate/start")
def api_calibrate_start():
    """Run player/calibrate.py detached: it walks the menus and cuts this
    account's own templates (card tabs, preset rows, module icons) - the
    pictures the repo never ships. Same guards as the scan, plus taps: the
    calibrator navigates, so a read-only instance cannot run it."""
    body = request.get_json(force=True) or {}
    others = [pr for pr in _procs() if pr["runner"] != "calibrate"]
    if others:
        return jsonify({"ok": False,
                        "error": "runners alive: " +
                                 ", ".join(pr["runner"] for pr in others) +
                                 " - stop them first"}), 409
    if any(pr["runner"] == "calibrate" for pr in _procs()):
        return jsonify({"ok": False, "error": "a calibration is already running"}), 409
    cfg = load_config()
    if not _taps_allowed(cfg) and not body.get("allow_navigation"):
        return jsonify({"ok": False,
                        "error": "allow_taps is off for this instance - the "
                                 "calibrator walks the game's menus; switch it "
                                 "on in Configuration first"}), 409
    inst = cfg.get("active_instance", "main")
    phases = str(body.get("phases") or "c,g,m,u,b,w")
    bootstrap = body.get("bootstrap") is True
    chosen = [p.strip() for p in phases.split(",") if p.strip()]
    if not chosen or any(p not in ("c", "g", "m", "u", "b", "w", "e") for p in chosen):
        return jsonify({"ok": False, "error": "Choose valid calibration steps"}), 400
    from player.scan_plan import missing_navigation
    missing = [] if bootstrap else missing_navigation(ROOT, cfg, chosen, include_wave=False)
    if missing:
        return jsonify({"ok": False, "error": "Capture the navigation images in the Scan plan before automatic calibration.", "missing": missing}), 409
    if "e" in chosen and not bootstrap:
        try:
            with open(os.path.join(_calibration_dir(cfg), "calibrate_state.json"), encoding="utf-8") as fh:
                basic_done = json.load(fh).get("phases", {}).get("modules", {}).get("status") == "done"
        except (OSError, ValueError):
            basic_done = False
        if not basic_done:
            return jsonify({"ok": False, "error": "Finish phase 1 Modules first."}), 409
    args = [os.path.join(ROOT, "player", "calibrate.py"), "--instance", inst,
            "--phases", phases]
    if bootstrap:
        args.append("--bootstrap")
        # The consented action-capture stage (start+cancel a battle, walk
        # event/store/guild). Bootstrap-only and only when the frontend's
        # popup passed flows=true; never inferred.
        if body.get("flows"):
            args.append("--flows")
            if body.get("battle_only"):
                args.append("--battle-only")
    if body.get("overwrite"):
        args.append("--overwrite")
    if body.get("fresh"):
        args.append("--fresh")
    if body.get("allow_navigation"):
        args.append("--allow-navigation")
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    subprocess.Popen([pyw] + args, cwd=ROOT,
                     creationflags=subprocess.DETACHED_PROCESS | NO_WINDOW)
    return jsonify({"ok": True, "phases": phases})


@app.post("/api/calibrate/observe")
def api_calibrate_observe():
    """Capture from the screen as it is: two frames, searched with the
    installed artwork for every bound target still missing (the HUD ability
    buttons during a battle, the UW switches with the panel open). READ-ONLY:
    no taps, no navigation, so it needs no tap permission and may run over a
    battle the person is playing - the screen is what they chose to show."""
    others = [pr for pr in _procs() if pr["runner"] != "calibrate"]
    if others:
        return jsonify({"ok": False, "error": "runners alive: " + ", ".join(pr["runner"] for pr in others) + " - stop them first"}), 409
    if any(pr["runner"] == "calibrate" for pr in _procs()):
        return jsonify({"ok": False, "error": "a calibration is already running"}), 409
    cfg = load_config()
    inst = cfg.get("active_instance", "main")
    args = [os.path.join(ROOT, "player", "calibrate.py"), "--instance", inst, "--phases", "c", "--observe"]
    body = request.get_json(silent=True) or {}
    try:
        watch = min(600, max(0, int(body.get("watch") or 0)))
    except (TypeError, ValueError):
        watch = 0
    if watch:
        args += ["--observe-watch", str(watch)]
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    subprocess.Popen([pyw] + args, cwd=ROOT, creationflags=subprocess.DETACHED_PROCESS | NO_WINDOW)
    return jsonify({"ok": True})


@app.post("/api/calibrate/stop")
def api_calibrate_stop():
    cfg = load_config()
    inst = cfg.get("active_instance", "main")
    os.makedirs(_calibration_dir(cfg), exist_ok=True)
    with open(os.path.join(_calibration_dir(cfg), "calibrate_stop"), "w") as fh:
        fh.write("dashboard")
    return jsonify({"ok": True})


@app.get("/api/calibrate/status")
def api_calibrate_status():
    cfg = load_config()
    inst = cfg.get("active_instance", "main")
    out = {"state": {}, "report": {}, "manifest": {}}
    for fname, key in (("calibrate_state.json", "state"),
                       ("calibrate_report.json", "report"), ("module_manifest.json", "manifest"),
                       ("card_manifest.json", "card_manifest")):
        try:
            with open(os.path.join(_calibration_dir(cfg), fname), encoding="utf-8") as fh:
                out[key] = json.load(fh)
        except (OSError, ValueError):
            pass
    from player.calibration_report import describe_report
    out["report"] = describe_report(out["report"])
    # Start and status must use the same live process source. A cached negative
    # immediately after Popen used to make the UI declare completion.
    processes = [pr for pr in _procs() if pr["runner"] == "calibrate"]
    selection = None
    activity = None
    if processes:
        command = processes[0].get("cmdline", "")
        match = re.search(r"--phases\s+([a-z,]+)", command)
        if match:
            selection = {"mode": "observe" if "--observe" in command else "bootstrap" if "--bootstrap" in command else "basic", "phases": match.group(1).split(","),
                         "taps": "--allow-navigation" in command,
                         "overwrite": "--overwrite" in command}
        # Also supports workers launched before incremental reports existed.
        events = [row for row in _newest_events(inst, n=2000)
                  if row.get("kind", "").startswith("calibrate")]
        if events:
            activity = events[-1]
    return jsonify({"restore_pending": os.path.exists(os.path.join(_calibration_dir(cfg), "module_restore.json")), "running": bool(processes), "selection": selection,
                    "activity": activity,
                    "taps_allowed": _taps_allowed(cfg), **out})


@app.post("/api/calibrate/apply")
def api_calibrate_apply():
    """Apply only successfully completed observations to the current profile."""
    import copy
    if _procs():
        return jsonify({"ok": False, "error": "Wait for calibration to stop before applying results."}), 409
    cfg = load_config()
    name = cfg.get("active_profile")
    if _is_starter(name):
        return jsonify({"ok": False, "error": "Create your account or copy the starter before applying calibration."}), 409
    path = _profile_path(name)
    if not path or not os.path.isfile(path):
        return jsonify({"ok": False, "error": "Choose a profile first."}), 400
    try:
        with open(os.path.join(_calibration_dir(cfg), "calibrate_state.json"), encoding="utf-8") as fh:
            state = json.load(fh)
        if not state.get("player"):
            raise ValueError("No completed account observations yet")
        if os.path.exists(os.path.join(_calibration_dir(cfg), "module_restore.json")):
            raise ValueError("Restore the saved module equipment before applying discoveries.")
        with _profile_lock(path):
            with open(path, encoding="utf-8") as fh:
                profile = yaml.safe_load(fh)
            player = profile.setdefault("player", {})
            # The wall bar REGION setup detected is machine config (per
            # instance rois.wall_bar - what the wall watch reads), not profile
            # data: it goes to config.yaml, never into player.
            wall_bar = state["player"].get("wall_bar")
            for key, value in state["player"].items():
                if key == "wall_bar":
                    continue
                if key in ("category_presets", "uws"):
                    player.setdefault(key, {}).update(copy.deepcopy(value))
                else:
                    player[key] = copy.deepcopy(value)
            # Ownership the verified cuts prove (uw/<name>.png comes off the
            # in-run panel, which lists owned weapons only) - also for states
            # written before the scanner recorded player.uws itself.
            from player.calibration_report import owned_uws
            proven = owned_uws(state.get("entries"))
            if proven:
                player.setdefault("uws", {}).update(proven)
            from player import playerprofile as pp
            with _COMPILER_LOCK:
                previous = pp.CONFIG
                pp.CONFIG = cfg
                try:
                    problems = pp.validate(profile)
                finally:
                    pp.CONFIG = previous
            # Saving observed ownership must not depend on battle-only run
            # requirements. No run rules change; launch still validates them.
            _save_yaml_backup(path, profile)
        from player.readiness import wall_bar_roi
        if wall_bar and not wall_bar_roi(cfg):
            inst = cfg.setdefault("instances", {}).setdefault(cfg.get("active_instance", "main"), {})
            inst.setdefault("rois", {})["wall_bar"] = list(wall_bar)
            save_config(cfg)
        return jsonify({"ok": True, "profile": name, "wall_bar": wall_bar,
                        "remaining_run_checks": problems})
    except (OSError, ValueError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.post("/api/scan/stop")
def api_scan_stop():
    cfg = load_config()
    inst = cfg.get("active_instance", "main")
    os.makedirs(_calibration_dir(cfg), exist_ok=True)
    with open(os.path.join(_calibration_dir(cfg), "scan_stop"), "w") as fh:
        fh.write("dashboard")
    return jsonify({"ok": True})


@app.get("/api/scan/status")
def api_scan_status():
    cfg = load_config()
    inst = cfg.get("active_instance", "main")
    state = {}
    try:
        with open(os.path.join(_calibration_dir(cfg), "scan_state.json"),
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


@app.get("/api/run-templates")
def api_run_templates():
    from player.run_templates import catalogue
    return jsonify(catalogue())


@app.post("/api/run-templates/add")
def api_run_template_add():
    from player.run_templates import instantiate
    body=request.get_json(force=True) or {}
    if not isinstance(body,dict):
        return jsonify(ok=False,error='Expected run template choices.'),400
    name=body.get('profile')
    path=_profile_path(name)
    if not path:
        return jsonify(ok=False,error='Choose a valid local profile.'),400
    if _is_starter(name) or _is_draft(name):
        return jsonify(ok=False,error='Create a personal profile in Setup before adding run templates.'),409
    with _profile_lock(path):
        try:
            with open(path,encoding='utf-8') as fh:
                original=yaml.safe_load(fh)
            updated=instantiate(original,body.get('template'),body.get('run_id'),body.get('options'))
            problems=_profile_check(updated)
            if problems:
                return jsonify(ok=False,error=problems[0],problems=problems),400
            backup=_save_yaml_backup(path,updated)
        except (OSError,ValueError,yaml.YAMLError) as e:
            return jsonify(ok=False,error=str(e)),400
    return jsonify(ok=True,profile=updated,backup=backup)


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
# applied to an IN-MEMORY copy, handed to _profile_check(), and only
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



def _profile_lock(path: str) -> threading.Lock:
    key = os.path.normcase(os.path.abspath(path))
    with _PROFILE_LOCKS_GUARD:
        lock = _PROFILE_LOCKS.get(key)
        if lock is None:
            lock = _PROFILE_LOCKS[key] = threading.Lock()
        return lock


def _profiles_dir() -> str:
    return os.path.join(ROOT, "profiles")


STARTER_PROFILE = "default"
_STARTER_READ_ONLY = (
    "profiles/default.yaml is the shipped starter and stays generic - its comments "
    "are the guide for every install and a save would drop them. Make your own "
    "profile first: copy the starter (Home page) or Promote a scan draft, then "
    "activate it")


def _is_starter(name) -> bool:
    """The one profile that ships with the repo. Never written by the
    dashboard: an edit while it is active (a fresh install) would land in
    the tracked file, comments gone (2026-09-06)."""
    return isinstance(name, str) and name.casefold() == STARTER_PROFILE


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
    cfg = load_config()
    active = cfg.get("active_profile")
    out = []
    for f in sorted(glob.glob(os.path.join(_profiles_dir(), "*.yaml"))):
        stem = os.path.basename(f)[:-len(".yaml")]
        if _foreign_profile(stem, cfg):
            continue
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


def _foreign_profile(name, cfg):
    from player import accounts
    active = accounts.identity(cfg)
    return bool(active and any(key != active and (name == record.get("active_profile")
                   or name in (record.get("profiles") or []) or name == key + ".draft")
               for key, record in (cfg.get("accounts") or {}).items()))


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
        problems = _profile_check(data)
        warns = _profile_warnings(data)
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
    if _is_starter(name):
        return jsonify({"ok": False, "error": _STARTER_READ_ONLY, "starter": True}), 409
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
            problems = _profile_check(data)
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
        warns = _profile_warnings(data)
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
                candidate = dict(cfg, loadouts=patched)
                problems = _profile_check(prof if isinstance(prof, dict) else {}, candidate)
                warns = _profile_check(prof if isinstance(prof, dict) else {}, candidate, warnings=True)
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


@app.post("/api/profile-copy")
def api_profile_copy():
    """Copy a profile file VERBATIM to profiles/<name>.yaml: the way to get
    an editable profile of your own before any scan exists. The starter's
    comments come along (a yaml dump would drop them), the copy is
    git-ignored, every editor works on it. Nothing is activated here."""
    body = request.get_json(force=True) or {}
    src_name = str(body.get("from") or STARTER_PROFILE).strip()
    name = str(body.get("name") or "").strip()
    src = _profile_path(src_name)
    dest = _profile_path(name)
    if not src or _is_draft(src_name) or not os.path.exists(src):
        return jsonify({"ok": False, "error": f"no profile {src_name!r} to copy"}), 404
    if not dest or _is_draft(name):
        return jsonify({"ok": False, "error": "name: letters, digits, _ - . only"}), 400
    if _is_starter(name):
        return jsonify({"ok": False, "error": _STARTER_READ_ONLY, "starter": True}), 409
    if os.path.exists(dest) and not body.get("overwrite"):
        return jsonify({"ok": False, "error": f"profiles/{name}.yaml exists"}), 409
    shutil.copyfile(src, dest)
    return jsonify({"ok": True, "name": name, "copied_from": src_name})


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
    if _foreign_profile(name, load_config()):
        return jsonify({"ok": False, "error": "This profile belongs to another account. Select that account in Setup first."}), 409
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
        problems = _profile_check(data if isinstance(data, dict) else {})
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
    base = os.path.join(_calibration_dir(cfg), "scan_evidence")
    path = os.path.normpath(os.path.join(base, rel))
    if not path.startswith(base + os.sep):
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
            # which instances are UP: HD-Player.exe carries the key on its
            # command line (`HD-Player.exe --instance Pie64`, the same form
            # the Start Menu shortcut uses)
            live_keys = set()
            try:
                import psutil
                for pr in psutil.process_iter(["name", "cmdline"]):
                    if "hd-player" in (pr.info["name"] or "").lower():
                        args = pr.info["cmdline"] or []
                        for i, a in enumerate(args):
                            if a == "--instance" and i + 1 < len(args):
                                live_keys.add(args[i + 1])
            except Exception:                   # noqa: BLE001
                pass
            adb_on = 'bst.enable_adb_access="1"' in txt
            for m in re.finditer(r'bst\.instance\.(\w+)\.display_name="([^"]*)"', txt):
                key, name = m.group(1), m.group(2)
                pm = re.search(rf'bst\.instance\.{key}\.adb_port="(\d+)"', txt)
                def _val(field):
                    mm = re.search(rf'bst\.instance\.{key}\.{field}="([^"]*)"', txt)
                    return mm.group(1) if mm else None
                w, h, dpi = _val("fb_width"), _val("fb_height"), _val("dpi")
                out.append({"emulator": "BlueStacks", "index": key, "name": name,
                            "running": key in live_keys,
                            "adb_enabled": adb_on,
                            "display": (f"{w}x{h}@{dpi}" if w and h and dpi else None),
                            "display_ok": (w, h, dpi) == _BS_DISPLAY_OK,
                            "adb_port": int(pm.group(1)) if pm else None,
                            "port_source": "conf" if pm else None})
        except Exception as e:                  # noqa: BLE001
            out.append({"emulator": "BlueStacks", "error": f"conf: {e}"})
    return out


def _program_files_dirs() -> list[str]:
    out = []
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        d = os.environ.get(var)
        if d and d not in out:
            out.append(d)
    return out or [r"C:\Program Files"]


def _bluestacks_player() -> str | None:
    for pf in _program_files_dirs():
        exe = os.path.join(pf, "BlueStacks_nxt", "HD-Player.exe")
        if os.path.exists(exe):
            return exe
    return None


def _adopt_placeholder(serial: str, adb: str | None) -> str:
    """Start-button counterpart of /api/wizard/adopt for a FRESH install:
    when the active instance has no serial yet, write this emulator's serial
    (and its adb binary) so the boot pipeline has something to drive. An
    instance that already points somewhere is left alone - repointing it is
    the adopt endpoint's explicit act, never a side effect of Start."""
    with _CONFIG_WRITE_LOCK:
        cfg = load_config()
        name = cfg.get("active_instance", "main")
        inst = (cfg.get("instances") or {}).get(name)
        if not isinstance(inst, dict):
            return f" - no instance {name!r} in config.yaml; nothing adopted"
        if inst.get("serial"):
            if inst["serial"] == serial:
                return ""
            return (f" - instance {name!r} keeps {inst['serial']}; use 'Use this "
                    f"one' to repoint it at {serial}")
        inst["serial"] = serial
        if adb and os.path.exists(adb):
            cfg.setdefault("adb", {})["exe"] = adb
        try:
            save_config(cfg)
        except Exception as e:                  # noqa: BLE001
            return f" - could not write config.yaml: {e}"
    return f" - instance {name!r} now points at {serial}"


def _boot_pipeline_for(serial: str) -> str:
    """Start boot.py for the configured instance that uses `serial`, if any.
    Returns the sentence to append to the launch message."""
    cfg = load_config()
    inst = next((n for n, i in (cfg.get("instances") or {}).items()
                 if isinstance(i, dict) and i.get("serial") == serial), None)
    if not inst:
        return (f" - no configured instance uses {serial}; adopt it once it is "
                "up, the game auto-launch is skipped this time")
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    subprocess.Popen([pyw, os.path.join(ROOT, "device", "boot.py"),
                      "--instance", inst], cwd=ROOT,
                     creationflags=subprocess.DETACHED_PROCESS | NO_WINDOW)
    return (f" - boot pipeline started for '{inst}': waits for Android, clears "
            "ad overlays, launches the game")


def _launch_bluestacks(key: str):
    """`HD-Player.exe --instance <key>` - the exact command BlueStacks' own
    Start Menu shortcut runs - then the same boot pipeline MuMu gets. The adb
    daemon is started with BlueStacks' HD-Adb.exe only when no OTHER
    emulator's adb is configured (a foreign adb kills the shared daemon)."""
    if not re.fullmatch(r"[A-Za-z0-9_]+", key):
        return jsonify({"ok": False, "error": "BlueStacks instance key expected"}), 400
    player = _bluestacks_player()
    if not player:
        return jsonify({"ok": False, "error": "HD-Player.exe not found under "
                        "BlueStacks_nxt"}), 400
    entry = next((i for i in _emulator_instances()
                  if i.get("emulator") == "BlueStacks" and i.get("index") == key), None)
    if entry is None:
        return jsonify({"ok": False, "error": f"no BlueStacks instance {key!r} in "
                        "bluestacks.conf"}), 400
    if entry.get("running"):
        msg = f"BlueStacks '{entry.get('name') or key}' is already running"
    else:
        try:
            subprocess.Popen([player, "--instance", key],
                             creationflags=subprocess.DETACHED_PROCESS | NO_WINDOW)
        except Exception as e:                  # noqa: BLE001
            return jsonify({"ok": False, "error": f"HD-Player: {e}"}), 500
        msg = f"BlueStacks '{entry.get('name') or key}' starting"
    hd_adb = os.path.join(os.path.dirname(player), "HD-Adb.exe")
    cfg_adb = (load_config().get("adb") or {}).get("exe") or ""
    foreign = (os.path.exists(cfg_adb)
               and os.path.normcase(cfg_adb) != os.path.normcase(hd_adb))
    if foreign:
        msg += (f" - adb daemon NOT started: config points at {cfg_adb}; adopt "
                "this instance to switch to HD-Adb.exe")
    elif os.path.exists(hd_adb):
        try:
            _run([hd_adb, "start-server"], capture_output=True, timeout=30)
            msg += " - adb daemon started with HD-Adb.exe"
        except Exception as e:                  # noqa: BLE001
            msg += f" - adb start-server failed: {e}"
    if entry.get("adb_enabled") is False:
        msg += (" - WARNING: BlueStacks' Android Debug Bridge is OFF in "
                "bluestacks.conf; nothing answers on the adb port until you "
                "switch it on (Settings > Advanced)")
    port = entry.get("adb_port")
    if port:
        serial = f"127.0.0.1:{port}"
        msg += _adopt_placeholder(serial, hd_adb if not foreign else None)
        msg += _boot_pipeline_for(serial)
    return jsonify({"ok": True, "message": msg})


# BlueStacks keeps a LANDSCAPE framebuffer and rotates the display when a
# portrait-only app such as The Tower comes to the front, so 2560x1080 rotated
# is the 1080x2560 layout every template was cut at. A portrait framebuffer
# (1080x2560) reads right in `wm size` but Unity never produces a frame on it:
# blank window, SurfaceFlinger latency table all zero (seen 2026-09-05).
_BS_WANT_DISPLAY = {"fb_width": "2560", "fb_height": "1080", "dpi": "360",
                    "custom_resolution_selected": "1"}
_BS_DISPLAY_OK = ("2560", "1080", "360")


def _bluestacks_conf_path() -> str:
    return os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                        "BlueStacks_nxt", "bluestacks.conf")


def _prepare_bluestacks(key: str) -> dict:
    """Set what Tower Pilot needs in bluestacks.conf for one instance: the
    global Android Debug Bridge switch on, and the instance's framebuffer at
    2560x1080 landscape / 360 dpi - BlueStacks rotates it to the 1080x2560
    portrait layout every template was cut at when The Tower starts. BlueStacks
    reads the file when the instance starts and rewrites it while a player
    runs, so this refuses unless every HD-Player.exe is closed. A timestamped
    backup of the file is written first. Returns what changed."""
    if not re.fullmatch(r"[A-Za-z0-9_]+", key):
        raise ValueError("BlueStacks instance key expected")
    path = _bluestacks_conf_path()
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} not found")
    try:
        import psutil
        if any("hd-player" in (pr.info["name"] or "").lower()
               for pr in psutil.process_iter(["name"])):
            raise RuntimeError("BlueStacks is running - close every BlueStacks "
                               "window first (it rewrites bluestacks.conf on "
                               "exit and would undo the change)")
    except ImportError:
        pass
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if not re.search(rf'^bst\.instance\.{key}\.display_name=', text, re.M):
        raise ValueError(f"no instance {key!r} in bluestacks.conf")
    want = {"bst.enable_adb_access": "1"}
    want.update({f"bst.instance.{key}.{k}": v for k, v in _BS_WANT_DISPLAY.items()})
    changed, added = {}, []
    for full, val in want.items():
        pat = re.compile(rf'^{re.escape(full)}="([^"]*)"$', re.M)
        m = pat.search(text)
        if m is None:
            text = text.rstrip("\n") + f'\n{full}="{val}"\n'
            added.append(full)
        elif m.group(1) != val:
            changed[full] = {"from": m.group(1), "to": val}
            text = pat.sub(f'{full}="{val}"', text, count=1)
    if not changed and not added:
        return {"changed": {}, "added": [], "backup": None,
                "message": "already prepared (ADB on, 2560x1080 @ 360)"}
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{path}.bak-{stamp}"
    with open(path, encoding="utf-8", errors="replace") as src, \
            open(backup, "w", encoding="utf-8") as dst:
        dst.write(src.read())
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return {"changed": changed, "added": added, "backup": backup,
            "message": (f"bluestacks.conf updated for {key}: "
                        + ", ".join(f"{k.split('.')[-1]} {v['from']}->{v['to']}"
                                    for k, v in changed.items())
                        + (" (+" + ", ".join(a.split(".")[-1] for a in added) + ")"
                           if added else "")
                        + f"; backup {os.path.basename(backup)}")}


@app.post("/api/wizard/bluestacks/prepare")
def api_wizard_bluestacks_prepare():
    body = request.get_json(force=True) or {}
    try:
        return jsonify({"ok": True, **_prepare_bluestacks(str(body.get("index") or ""))})
    except (ValueError, FileNotFoundError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    except OSError as e:
        return jsonify({"ok": False, "error": f"cannot write bluestacks.conf: {e}"}), 500


_TOOLS_STATE = {"stage": "idle", "message": "Connection tools have not been installed", "percent": 0}
_TOOLS_LOCK = threading.Lock()


@app.get("/api/wizard/tools")
def api_wizard_tools():
    from device import tool_install
    with _TOOLS_LOCK:
        state = dict(_TOOLS_STATE)
    path = tool_install.installed(ROOT)
    return jsonify(dict(state, installed=bool(path), path=path))


@app.post("/api/wizard/tools/install")
def api_wizard_tools_install():
    if _procs():
        return jsonify(error="Stop automation before preparing connection tools"), 409
    body = request.get_json(force=True) or {}
    if body.get("accept_license") is not True:
        return jsonify(error="Accept the Android SDK terms before downloading"), 400
    with _TOOLS_LOCK:
        if _TOOLS_STATE["stage"] in ("downloading", "installing"):
            return jsonify(ok=True, **_TOOLS_STATE)
        _TOOLS_STATE.update(stage="downloading", message="Starting download", percent=0)

    def worker():
        from device import tool_install
        def progress(stage, message, percent):
            with _TOOLS_LOCK:
                _TOOLS_STATE.update(stage=stage, message=message, percent=percent)
        try:
            tool_install.install(ROOT, progress)
        except Exception as exc:
            progress("error", f"Could not install connection tools: {exc}. Check your internet connection and retry.", 0)
    threading.Thread(target=worker, daemon=True).start()
    return jsonify(ok=True, stage="downloading"), 202


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
    if body.get("emulator") == "BlueStacks":
        return _launch_bluestacks(str(body.get("index") or ""))
    mgr = _mumu_manager()
    if body.get("emulator") != "MuMu" or not mgr:
        return jsonify({"ok": False, "error": "launch is wired for MuMu and "
                        "BlueStacks only"}), 400
    idx = str(int(body["index"]))
    try:
        r = _run([mgr, "control", "-v", idx, "launch"], capture_output=True,
                 timeout=30, text=True)
        msg = (r.stdout or r.stderr or "").strip()
        if r.returncode != 0:
            return jsonify({"ok": False, "message": msg})
        serial = f"127.0.0.1:{16384 + 32 * int(idx)}"   # MuMu's port scheme
        mumu_adb = os.path.join(os.path.dirname(mgr), "adb.exe")
        if not os.path.isfile(mumu_adb):
            from device import tool_install
            mumu_adb = tool_install.installed(ROOT) or mumu_adb
        msg += _adopt_placeholder(serial, mumu_adb)
        msg += _boot_pipeline_for(serial)
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
        if not adb:
            from device import tool_install
            configured = (cfg.get("adb") or {}).get("exe") or ""
            adb = configured if os.path.isfile(configured) else tool_install.installed(ROOT) or ""
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
    existing callers and bookmarked URLs keep working.

    The size comes from the raw `screencap` header, not `wm size`: `wm size`
    reports the physical panel, and BlueStacks keeps a 2560x1080 landscape
    panel that it rotates when The Tower is in front - screencap then
    delivers a 1080x2560 frame while `wm size` still says 2560x1080 (seen
    2026-09-05). The screencap header is exactly what capture.grab's
    resolution lock enforces, so it is the truth this check has to match."""
    serial = request.args["serial"]
    try:
        from device import adbclient
        cfg = load_config()
        inst = cfg.get("instances", {}).get(cfg.get("active_instance", "main")) or {}
        configured = inst.get("display") if inst.get("serial") == serial else None
        display = request.args.get("display") or _game_display(serial, configured)
        raw = adbclient.exec_out(serial, _screencap_cmd(serial, display, png=False), timeout=20)
        if len(raw) < 8:
            raise RuntimeError("screencap answered nothing")
        # a multi-display emulator prefixes the raw payload with a text
        # warning line ("[Warning] Multiple displays ..."): the first bytes
        # then read "[War" x "ning" - drop leading lines until the header is
        # sane, exactly as capture.grab does
        w = h = 0
        for _ in range(4):
            if len(raw) < 8:
                break
            w, h = struct.unpack("<II", raw[:8])
            if w <= 10000 and h <= 10000:
                break
            nl = raw.find(b"\n", 0, 400)
            if nl < 0:
                break
            raw = raw[nl + 1:]
        if len(raw) < 8 or not w or not h or w > 10000 or h > 10000:
            _forget_display(serial)
            raise RuntimeError("screencap answered text, not a frame - which display is the game on?")
    except Exception as e:                      # noqa: BLE001
        return jsonify(_wiz_save("resolution", {
            "ok": False, "serial": serial,
            "error": f"{e} (no adb server, or the device is not attached - "
                     f"use adb connect above first)"}))
    if (w, h) == (1080, 2560):
        # the device answered at the calibrated resolution: wiring proven
        _mark_setup_complete("resolution")
    return jsonify(_wiz_save("resolution", {
        "ok": True, "serial": serial, "width": int(w), "height": int(h),
        "expected": (w, h) == (1080, 2560),
        "note": ("" if (w, h) == (1080, 2560) else
                 "Templates are calibrated for a 1080x2560 portrait frame."
                 " MuMu: set the display to 1080x2560 @ 360. BlueStacks:"
                 " 2560x1080 landscape @ 360 (Prepare writes it) and run"
                 " this check while the game is in front - it rotates the"
                 " panel.")}))


@app.get("/api/wizard/templates")
def api_wizard_templates():
    from player import accounts
    cfg = load_config()
    paths = accounts.template_files(ROOT, cfg, "**/*.png")
    have = []
    for path in paths:
        parts = path.parts
        at = len(parts) - 1 - list(reversed(parts)).index("templates")
        have.append("/".join(parts[at + 1:]))
    # templates the code ASKED for and did not find, straight from the logs -
    # the only honest source of "what is missing on this machine"
    missing = set()
    for rows in (_newest_events(n=2000),):
        for r in rows:
            if r.get("kind") == "template_missing":
                missing.add(r.get("template"))
    return jsonify({"have": sorted(have), "missing_seen": sorted(missing - set(have))})


@app.get("/api/wizard/scan-plan")
def api_wizard_scan_plan():
    from player import scan_plan, readiness
    cfg = load_config()
    runs = _compiled_runs(cfg)
    selected = request.args.get("preset")
    body = runs.get(selected, {}) if selected else {}
    return jsonify(scan_plan.plan(ROOT, cfg, readiness.requirements(cfg, body) if body else ()))


_ARTWORK_INDEX: dict = {}      # asset-library folder -> (index mtime, images by lower-case name)


def _asset_library(cfg):
    """The newest extracted asset library for the active account, or None."""
    base = os.path.join(_calibration_dir(cfg), "asset_library")
    best = None
    for folder in glob.glob(os.path.join(base, "*")):
        index = os.path.join(folder, "index.json")
        if os.path.isfile(index) and (best is None or os.path.getmtime(index) > best[1]):
            best = (folder, os.path.getmtime(index))
    return best[0] if best else None


def _artwork_path(cfg, rel):
    """The installed game's own artwork for a recognition target, from the
    asset library Full setup extracts (never shipped): the manifest mapping
    first, else the names template_docs lists for it. What the person is
    expected to find on screen, next to the request to find it (2026-09-08)."""
    from player import template_docs
    folder = _asset_library(cfg)
    if not folder:
        return None
    try:
        with open(os.path.join(folder, "mapping.json"), encoding="utf-8") as fh:
            target = (json.load(fh).get("targets") or {}).get(rel) or {}
        for cand in target.get("candidates") or []:
            path = os.path.join(folder, cand.get("file") or "")
            if cand.get("file") and os.path.isfile(path):
                return os.path.normpath(path)
    except (OSError, ValueError):
        pass
    names = [n.lower() for n in template_docs.artwork_names(rel)]
    if not names:
        return None
    index = os.path.join(folder, "index.json")
    stamp = os.path.getmtime(index)
    cached = _ARTWORK_INDEX.get(folder)
    if not cached or cached[0] != stamp:
        by_name = {}
        try:
            with open(index, encoding="utf-8") as fh:
                for img in json.load(fh).get("images") or []:
                    by_name.setdefault(str(img.get("name") or "").lower(), []).append(img)
        except (OSError, ValueError):
            by_name = {}
        cached = (stamp, by_name)
        _ARTWORK_INDEX[folder] = cached
    for name in names:
        hits = [i for i in cached[1].get(name, []) if i.get("file")]
        # a Sprite is the on-screen cut; prefer it, then the largest
        hits.sort(key=lambda i: (i.get("type") != "Sprite", -(i.get("width") or 0) * (i.get("height") or 0)))
        for img in hits:
            path = os.path.join(folder, img["file"])
            if os.path.isfile(path):
                return os.path.normpath(path)
    return None


def _text_badge_png(text):
    """A stand-in picture for a control the game renders as TEXT (RETRY,
    EQUIP, a header word): the expected words on a button-shaped badge, so
    every target row shows what to look for even when no sprite exists."""
    import cv2
    import numpy as np
    text = str(text)
    canvas = np.full((88, 260, 3), (44, 34, 30), np.uint8)
    cv2.rectangle(canvas, (3, 3), (256, 84), (230, 200, 60), 2)
    scale = 1.1 if len(text) <= 6 else 0.8 if len(text) <= 11 else 0.55
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale, 2)
    cv2.putText(canvas, text, ((260 - tw) // 2, (88 + th) // 2), cv2.FONT_HERSHEY_DUPLEX, scale, (255, 245, 235), 2, cv2.LINE_AA)
    ok, png = cv2.imencode(".png", canvas)
    return png.tobytes() if ok else None


@app.get("/api/artwork/<path:rel>")
def api_artwork(rel):
    path = _artwork_path(load_config(), rel)
    if path:
        return send_file(path, mimetype="image/png", max_age=0)
    from player import template_docs
    text = template_docs.text_for(rel)
    png = _text_badge_png(text) if text else None
    if not png:
        return Response("no artwork for this target", status=404)
    return Response(png, mimetype="image/png", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/template/<path:rel>")
def api_template(rel):
    path = _template_path(rel)
    if not path:
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


def _template_path(rel: str, *, write=False, cfg=None) -> str | None:
    """Absolute path under templates/ for a relative name, or None when the
    name is not a plain `<folder>/<name>.png` inside it."""
    if not isinstance(rel, str) or not rel.lower().endswith(".png"):
        return None
    rel = rel.replace("\\", "/")
    if rel.startswith("/") or ".." in rel.split("/") or ":" in rel or "/" not in rel:
        return None                     # every template lives in a subfolder
    from player import accounts
    try:
        return str(accounts.template_path(ROOT, load_config() if cfg is None else cfg, rel, write=write))
    except ValueError:
        return None



def _preset_slug(name: str) -> str:
    # interactions.presets._slug, restated (the dashboard imports no runner
    # module): preset names as the user typed them -> template file stems
    return "".join(c if c.isalnum() else "_" for c in str(name).strip().lower())


def _required_templates(cfg: dict, profile: dict | None) -> list[dict]:
    """Every ACCOUNT-SPECIFIC template the configured loadouts and the
    scanned presets need, with have/missing. All UI chrome is also captured
    locally; the separate Scan plan covers those images: its card
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
    equipped = set(player.get("modules_equipped") or [])
    out = []
    for rel in sorted(want):
        path = _template_path(rel, cfg=cfg)
        row = want[rel]
        row["have"] = bool(path and os.path.exists(path))
        stem = rel[len("modules/"):-4]
        if not row["have"] and rel.startswith("modules/") and "/" not in stem \
                and stem in equipped:
            # an equipped module is absent from the inventory grid, so its
            # grid tile cannot be cut until it is unequipped
            row["note"] = ("equipped right now - its inventory tile can only be "
                           "cut while it sits in the grid; re-run Calibrate then")
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


@app.get("/api/catalogue/modules")
def api_catalogue_modules():
    """Module slugs the loadouts and the module templates are keyed by - the
    Calibrate page's naming help for a fresh account (game knowledge only,
    player/catalogue.py; nothing about any account)."""
    from player import catalogue
    learned = catalogue.local_modules()
    return jsonify({"modules": [{"slug": s, "name": n, "abbrevs": list(a),
                                 "learned": s in learned}
                                for s, (n, a) in catalogue.all_modules().items()]})


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
    path = _template_path(rel, write=True)
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
    # where it came from: the screen (recognized by the manifest's anchors)
    # and the native rect go into the learned manifest, plus a report entry
    # and an event like every cut setup makes - so setup and observe can cut
    # this control themselves next time, on that screen, at this size
    screen = None
    try:
        from player import bootstrap, calibrate
        try:
            screen = bootstrap.recognize_screen(frame)
        except Exception as e:                  # noqa: BLE001 - OCR unavailable: still record the rect
            app.logger.warning("cropper screen recognition failed for %s: %s", rel, e)
        cdir = _calibration_dir()
        os.makedirs(cdir, exist_ok=True)
        calibrate.record_manual_cut({"state": os.path.join(cdir, "calibrate_state.json"),
                                     "report": os.path.join(cdir, "calibrate_report.json")},
                                    rel.replace("\\", "/"), crop, frame, [x, y, w, h], screen=screen)
    except Exception as e:                      # noqa: BLE001 - the file is written; provenance is best effort
        app.logger.warning("cropper provenance not recorded for %s: %s", rel, e)
    return jsonify({"ok": True, "rel": rel.replace("\\", "/"), "width": w,
                    "height": h, "second_best": round(second, 3), "screen": screen})


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
    if _is_starter(name):
        return jsonify({"ok": False, "error": _STARTER_READ_ONLY, "starter": True}), 409
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
    # Nothing is assumed: no wall until the player says so, and max_tier is
    # the tier the home screen showed at scan time (unlocked by definition),
    # else the starter's floor.
    player.setdefault("wall", False)
    tier_seen = player.get("tier_current")
    player.setdefault("max_tier", tier_seen if isinstance(tier_seen, int) and tier_seen > 0
                      else (doc.get("player") or {}).get("max_tier", 1))
    doc["player"] = player
    doc.pop("_name", None)
    doc.pop("_path", None)
    try:
        from player import playerprofile
        check = dict(doc)
        check["_name"] = name
        problems = _profile_check(check)
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
