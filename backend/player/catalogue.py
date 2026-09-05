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
"""

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


def resolve(text: str) -> str:
    """'MVN' / 'Multiverse Nexus' / 'multiverse_nexus' -> 'multiverse_nexus'."""
    key = text.strip().lower()
    if key in _LOOKUP:
        return _LOOKUP[key]
    raise KeyError(f"unknown module {text!r}")


def display(slug: str) -> str:
    return MODULES[slug][0]
