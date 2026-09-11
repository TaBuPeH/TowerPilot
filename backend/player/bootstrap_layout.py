"""Shipped, pixel-free native layout shared by calibration and navigation."""
import json
from functools import lru_cache
from pathlib import Path
from contextvars import ContextVar

_display = ContextVar('manifest_display', default=None)

def bind_display(display):
    """Bind the native display for this worker, never mutate reference data."""
    from player.geometry import scale_manifest
    return _display.set((display, scale_manifest(reference_manifest(), display)))

@lru_cache(maxsize=1)
def reference_manifest():
    return json.loads(Path(__file__).with_name("bootstrap_manifest.json").read_text(encoding="utf-8"))


def manifest(display=None):
    from copy import deepcopy
    from player.geometry import scale_manifest
    reference = reference_manifest()
    if display is not None:
        return scale_manifest(reference, display)
    bound = _display.get()
    return deepcopy(bound[1] if bound is not None else reference)


# Preserve callers that explicitly refresh the reference after editing it.
manifest.cache_clear = reference_manifest.cache_clear

def writable_targets():
    m = manifest()
    names = {t["rel"] for s in m["screens"].values() for t in s["targets"]}
    names.update(r["icon"]["rel"] for r in m["routes"] if r.get("icon"))
    names.update(t["rel"] for t in m.get("dynamic_targets", []))
    names.update(m.get("asset_bindings", {}))
    names.update(t["rel"] for s in m.get("hud", {}).values() if isinstance(s, dict) for t in s.get("targets", []))
    names.update(f"mapping/v{m['version']}/route_{i}.png" for i in range(len(m['routes'])))
    return names


def screen_targets(screen):
    """The shipped manifest's fixed-rect targets for a screen: a menu screen's
    `screens` entry or an in-run `hud` state (battle, battle_menu)."""
    m = manifest()
    if screen in m["screens"]:
        return list(m["screens"][screen]["targets"])
    hud = m.get("hud", {}).get(screen)
    return list(hud["targets"]) if isinstance(hud, dict) else []


def scan_steps():
    """Stable step IDs for saved progress and the frontend's planned route."""
    return ([{"id":"preflight", "label":"Check emulator"}, {"id":"extract", "label":"Extract artwork"}, {"id":"map", "label":"Map artwork"}, {"id":"home", "label":"Verify Home"}]
            + [{"id":f"route_{i}", "label":r["name"]} for i,r in enumerate(manifest()["routes"])]
            + [{"id":"finish", "label":"Verify and save"}])
