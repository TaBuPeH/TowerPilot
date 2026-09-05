"""Shared config loading for the autopilot."""
import copy
import os
import subprocess
from pathlib import Path
import yaml

# A process launched from pythonw.exe has NO console, so every console child
# (adb.exe, powershell.exe) would pop its own window - roughly one flashing
# window per second under the tray supervisor. CREATE_NO_WINDOW suppresses it.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def run_hidden(args, **kw):
    """subprocess.run that never flashes a console window. ALL adb/powershell
    calls in the autopilot must go through this."""
    kw.setdefault("creationflags", NO_WINDOW)
    return subprocess.run(args, **kw)

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
CONFIG_EXAMPLE = ROOT / "config.example.yaml"


def seed_config(path: Path = CONFIG_PATH, example: Path = CONFIG_EXAMPLE) -> bool:
    """Create config.yaml from config.example.yaml when it does not exist.

    A fresh checkout ships no config.yaml (it is the machine file and is
    git-ignored). Before this every `import settings` died with
    FileNotFoundError at import - the dashboard's own scan endpoint included,
    so a new install could not even run the wizard that would have written
    the file. Returns True when the file was created. The dashboard has a
    copy of this rule (it deliberately never imports settings).
    """
    if path.exists():
        return False
    if not example.exists():
        raise FileNotFoundError(f"{path} is missing and so is {example}")
    path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    return True


seed_config()
CONFIG = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

# Blocks an instance may override, snapshotted PRISTINE at import. Overrides
# are applied in place (every module holds a reference to these dicts), so
# without a clean baseline a second select_instance() call would inherit the
# first instance's values - e.g. Main's wall_bar ROI leaking onto a low-tier
# account that has no wall.
_OVERRIDABLE = ("rois", "tabs", "loop", "missions", "side_menu", "fleet")
_DEFAULTS = {k: copy.deepcopy(CONFIG[k]) for k in _OVERRIDABLE if k in CONFIG}


def instance(name: str | None = None) -> dict:
    name = name or CONFIG["active_instance"]
    return CONFIG["instances"][name]


def select_instance(name: str, preset: str | None = None) -> None:
    """Bind this PROCESS to one instance, applying its overrides.

    Accounts differ in ways the vision layer cares about (Main has a wall bar
    where low tiers have none), so instances may carry their own `preset`,
    `rois` and `tabs` overrides on top of the global defaults. Overrides are
    merged IN PLACE because every module did `from settings import CONFIG` and
    holds a reference to these same dicts.

    One instance per process is deliberate: act/capture/logger keep module
    globals (tap rate cap, layout offset, log file) that assume a single
    screen. The tray supervisor runs one orchestrator process per instance.
    """
    if name not in CONFIG["instances"]:
        raise KeyError(f"unknown instance '{name}' "
                       f"(have: {', '.join(CONFIG['instances'])})")
    CONFIG["active_instance"] = name
    inst = CONFIG["instances"][name]
    for key, pristine in _DEFAULTS.items():
        CONFIG[key].clear()
        CONFIG[key].update(copy.deepcopy(pristine))
        override = inst.get(key)
        if isinstance(override, dict):
            CONFIG[key].update(override)
    # ---- PROFILE LAYER (P3). A profile compiles its blueprints into extra
    # CONFIG["presets"]["bp_<name>"] entries, so it has to run BEFORE the
    # preset is resolved below - `--preset bp_coin_default` must be findable
    # by the very check that would otherwise reject it as unknown.
    # Imported HERE, not at module scope: playerprofile.py imports settings.
    # (Named playerprofile, not profile: `profile` is a stdlib module and the
    # script directory precedes the stdlib on sys.path.)
    # No `active_profile` key (every legacy config) = not even an import.
    prof_name = CONFIG.get("active_profile")
    if prof_name:
        from player import playerprofile
        playerprofile.select_profile(prof_name)   # ...which materializes
                                                  # the bp_ presets itself
    chosen = preset or inst.get("preset") or CONFIG["preset"]
    body = CONFIG["presets"].get(chosen)
    if body is None:
        raise KeyError(f"unknown preset '{chosen}' "
                       f"(have: {', '.join(CONFIG['presets'])})")
    if not (body or {}).get("defined", True):
        raise ValueError(f"preset '{chosen}' is a placeholder with no settings "
                         f"yet - define it in config.yaml first")
    CONFIG["preset"] = chosen


def preset_menu() -> list[tuple[str, str, bool]]:
    """(key, label, selectable) for every preset, for the tray menu.
    A preset with `defined: false` is a placeholder: listed but not runnable
    until someone gives it real settings."""
    out = []
    for key, body in CONFIG["presets"].items():
        body = body or {}
        label = body.get("label") or key.replace("_", " ").title()
        out.append((key, label, body.get("defined", True)))
    return out


def adb_args(serial: str | None = None) -> list[str]:
    serial = serial or instance()["serial"]
    return [CONFIG["adb"]["exe"], "-s", serial]


def input_args() -> list[str]:
    """`adb shell input` prefix for the ACTIVE instance, display-aware:
    multi-display instances (Main) need `input -d <logical id>` or taps land
    on the launcher display instead of the game."""
    inst = instance()
    base = adb_args() + ["shell", "input"]
    if inst.get("input_display") is not None:
        base += ["-d", str(inst["input_display"])]
    return base
