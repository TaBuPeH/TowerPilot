"""What every module is called, and every name it gets called by.

Read off the game itself by inventory.py: tap each tile, read the detail panel.
The abbreviations are the community ones (what Reddit / the wiki use) so that a
loadout can be written as "MVN" or "Multiverse Nexus" or "multiverse_nexus" and
mean the same module.

Most abbreviations are just the initials, but not all of them - MVN keeps the
V from multiVerse, and Galaxy Compressor is written both GC and GCOMP - so
they are listed rather than derived. resolve() accepts any of the three forms.

This is GAME knowledge only. What an account holds - the six equipped slots,
the inventory grid with its rarities - is account data: the scan writes it to
the player profile (player.modules_equipped / player.modules_in_grid) and
nothing in code assumes it.

The shipped table is not closed: the game keeps adding modules, and the
calibrator reads names off the detail panel. A name the table lacks is
LEARNED into backend/catalogue_local.yaml (machine-local, git-ignored) by
learn(); resolve()/display()/all_modules() see both tables.
"""
import os
from pathlib import Path

import yaml

LOCAL = Path(__file__).resolve().parents[1] / "catalogue_local.yaml"
_local_cache: dict = {"mtime": None, "data": {}}

# slug -> (display name, abbreviations)
MODULES = {
    "amplifying_strike":       ("Amplifying Strike",       ["AS"]),
    "anti_cube_portal":        ("Anti-Cube Portal",        ["ACP"]),
    "astral_deliverance":      ("Astral Deliverance",      ["AD"]),
    "being_annihilator":       ("Being Annihilator",       ["BA"]),
    "black_hole_digestor":     ("Black Hole Digestor",     ["BHD"]),
    "death_penalty":           ("Death Penalty",           ["DP"]),
    "dimension_core":          ("Dimension Core",          ["DC"]),
    "galaxy_compressor":       ("Galaxy Compressor",       ["GC", "GCOMP"]),
    "harmony_conductor":       ("Harmony Conductor",       ["HC"]),
    "havoc_bringer":           ("Havoc Bringer",           ["HB"]),
    "magnetic_hook":           ("Magnetic Hook",           ["MH"]),
    "multiverse_nexus":        ("Multiverse Nexus",        ["MVN"]),
    "negative_mass_projector": ("Negative Mass Projector", ["NMP"]),
    "om_chip":                 ("Om Chip",                 ["OC"]),
    "orbital_augment":         ("Orbital Augment",         ["OA"]),
    "primordial_collapse":     ("Primordial Collapse",     ["PC"]),
    "project_funding":         ("Project Funding",         ["PF"]),
    "pulsar_harvester":        ("Pulsar Harvester",        ["PH"]),
    "restorative_bonus":       ("Restorative Bonus",       ["RB"]),
    "sharp_fortitude":         ("Sharp Fortitude",         ["SF"]),
    "shrink_ray":              ("Shrink Ray",              ["SR"]),
    "singularity_harness":     ("Singularity Harness",     ["SH"]),
    "solar_dyson_sphere":      ("Solar Dyson Sphere",      ["SDS"]),
    "space_displacer":         ("Space Displacer",         ["SD"]),
    "wormhole_redirector":     ("Wormhole Redirector",     ["WR"]),
}


def _index():
    idx = {}
    for slug, (name, abbrevs) in MODULES.items():
        idx[slug] = slug
        idx[name.lower()] = slug
        idx[name.lower().replace("-", " ")] = slug
        for a in abbrevs:
            idx[a.lower()] = slug
    return idx


_LOOKUP = _index()


def slug_of(name: str) -> str:
    """A display name -> the slug it would get ('Sentry Protocol' ->
    'sentry_protocol'); the shipped slugs follow the same rule."""
    return "".join(c if c.isalnum() else "_" for c in name.strip().lower()).strip("_")


def local_modules() -> dict:
    """Modules learned on THIS install: slug -> (name, abbrevs). Re-read when
    the file changes; empty when there is none."""
    try:
        mtime = os.path.getmtime(LOCAL)
    except OSError:
        _local_cache.update(mtime=None, data={})
        return {}
    if _local_cache["mtime"] != mtime:
        try:
            raw = yaml.safe_load(Path(LOCAL).read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            raw = {}
        data = {}
        for slug, body in (raw.get("modules") or {}).items():
            if isinstance(body, dict) and body.get("name"):
                data[str(slug)] = (str(body["name"]),
                                   [str(a) for a in (body.get("abbrevs") or [])])
        _local_cache.update(mtime=mtime, data=data)
    return dict(_local_cache["data"])


def all_modules() -> dict:
    """Shipped table plus what this install learned."""
    return {**MODULES, **local_modules()}


def learn(name: str) -> str:
    """Record a module name the shipped table lacks - read off the game's own
    detail panel by the calibrator - and return its slug. Idempotent; a
    name the table already knows is just resolved."""
    try:
        return resolve(name)
    except KeyError:
        pass
    slug = slug_of(name)
    if not slug:
        raise KeyError(f"no slug for {name!r}")
    raw = {}
    if Path(LOCAL).exists():
        try:
            raw = yaml.safe_load(Path(LOCAL).read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            raw = {}
    mods = raw.setdefault("modules", {})
    if slug not in mods:
        mods[slug] = {"name": name.strip(), "abbrevs": []}
        Path(LOCAL).write_text(
            "# Modules learned from the game by the calibrator: names the shipped\n"
            "# catalogue (player/catalogue.py) does not know yet. Machine-local,\n"
            "# git-ignored, safe to delete - the next calibration rewrites it.\n"
            + yaml.safe_dump(raw, sort_keys=True, allow_unicode=True),
            encoding="utf-8")
        _local_cache["mtime"] = None
    return slug


def resolve(text: str) -> str:
    """'MVN' / 'Multiverse Nexus' / 'multiverse_nexus' -> 'multiverse_nexus',
    for shipped and learned modules alike."""
    key = text.strip().lower()
    if key in _LOOKUP:
        return _LOOKUP[key]
    for slug, (name, abbrevs) in local_modules().items():
        if key in (slug, name.lower(), name.lower().replace("-", " "),
                   *[a.lower() for a in abbrevs]):
            return slug
    raise KeyError(f"unknown module {text!r}")


def display(slug: str) -> str:
    return all_modules()[slug][0]
