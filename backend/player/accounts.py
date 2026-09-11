"""Account storage and template resolution, shared by UI and runners.

No settings import: the dashboard can use this without binding a device.
Unbound installations retain their original paths. A bound account never
falls back to another account's crops. No game images are bundled.
"""
import copy
import json
import re
import hashlib
from pathlib import Path

_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")
FIELDS = ("loadouts", "active_profile", "tourney_card_tweaks")


def valid_id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("Use 1–64 letters, digits, underscores or hyphens, starting with a letter or digit")
    if value.casefold() in {"con", "prn", "aux", "nul", *[f"com{i}" for i in range(1, 10)], *[f"lpt{i}" for i in range(1, 10)]}:
        raise ValueError("This name is reserved by Windows; choose another name")
    return value


def identity(cfg):
    inst = cfg.get("active_instance", "main")
    account = ((cfg.get("instances") or {}).get(inst) or {}).get("account")
    if account:
        valid_id(account)
        if account not in (cfg.get("accounts") or {}):
            raise ValueError(f"Unknown account {account!r}; choose an account in Setup")
    return account


def effective(cfg):
    out = copy.deepcopy(cfg)
    account = identity(out)
    if account:
        body = out["accounts"][account]
        for key in FIELDS:
            out[key] = copy.deepcopy(body.get(key, "default" if key == "active_profile" else {}))
    return out


def persist(cfg, original):
    """Save edited account fields without overwriting legacy machine defaults."""
    out = copy.deepcopy(cfg)
    account = identity(out)
    if account:
        profiles = set(out["accounts"][account].get("profiles") or [])
        if out.get("active_profile") and out["active_profile"] != "default":
            profiles.add(out["active_profile"])
        out["accounts"][account]["profiles"] = sorted(profiles)
        for key in FIELDS:
            out["accounts"][account][key] = copy.deepcopy(out.get(key))
            if key in original:
                out[key] = copy.deepcopy(original[key])
            else:
                out.pop(key, None)
    return out


def calibration_dir(root, cfg):
    account = identity(cfg)
    if not account:
        return Path(root) / "logs" / cfg.get("active_instance", "main")
    inst = valid_id(cfg.get("active_instance", "main"))
    device = (cfg.get("instances") or {}).get(inst) or {}
    signature = str(device.get("serial", "")) + "|" + str((cfg.get("adb") or {}).get("exe", "")).casefold()
    rendering = device.get('rendering')
    if rendering and rendering != {'width':1080, 'height':2560, 'dpi':360}:
        signature += '|' + json.dumps(rendering, sort_keys=True)
    suffix = hashlib.sha256(signature.encode()).hexdigest()[:10]
    return Path(root) / "accounts" / account / "calibration" / f"{inst}-{suffix}"


def template_dir(root, cfg):
    if not identity(cfg):
        return Path(root) / "templates"
    return calibration_dir(root, cfg) / "templates"


def generic_names():
    return set(json.loads((Path(__file__).with_name("generic_templates.json")).read_text(encoding="utf-8-sig")))


def template_path(root, cfg, rel, *, write=False):
    rel = str(rel).replace("\\", "/")
    if (":" in rel or rel.startswith("/") or any(p in ("", ".", "..") for p in rel.split("/"))
            or "/" not in rel or not rel.endswith(".png")):
        raise ValueError("Expected a relative template path such as cards/preset_farm.png")
    return template_dir(root, cfg) / rel


def template_files(root, cfg, pattern="*/*.png"):
    rels = {p.relative_to(template_dir(root, cfg)).as_posix()
            for p in template_dir(root, cfg).glob(pattern)}
    return [template_path(root, cfg, r) for r in sorted(rels)]


def draft_name(cfg):
    return identity(cfg) or cfg.get("active_instance", "main")
