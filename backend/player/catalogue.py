"""What every module is called, and every name it gets called by.

Read off the game itself by inventory.py: tap each tile, read the detail panel.
The abbreviations are the community ones (the user's own shorthand, and what
Reddit / the wiki use) so that a loadout can be written as "MVN" or
"Multiverse Nexus" or "multiverse_nexus" and mean the same module.

Most abbreviations are just the initials, but not all of them - MVN keeps the
V from multiVerse, and Galaxy Compressor is written both GC and GCOMP - so
they are listed rather than derived. resolve() accepts any of the three forms.
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

# What the main account holds, as read on 2026-08-13 (37/300).
#
# READ FROM THE SIX EQUIPPED SLOTS, not from the intended build. An earlier
# version of this table was written from the loadout described in conversation
# and got the core slot wrong in both halves: the tower runs Primordial
# Collapse as PRIMARY with Dimension Core assisting, and Multiverse Nexus is
# not equipped at all - it is a Lv.1 spare sitting in the inventory. Equipped
# modules are absent from the inventory grid, so the only way to read them is
# to open each slot.
EQUIPPED = {
    "amplifying_strike":   {"level": 179, "slot": "primary", "stars": 3},
    "black_hole_digestor": {"level": 179, "slot": "primary", "stars": 0},
    "galaxy_compressor":   {"level": 89,  "slot": "assist",  "stars": 2},
    "sharp_fortitude":     {"level": 179, "slot": "primary", "stars": 1},
    "primordial_collapse": {"level": 179, "slot": "primary", "stars": 3},
    "dimension_core":      {"level": 78,  "slot": "assist",  "stars": 3},
}

# The 31 inventory tiles, in grid order: (slug, rarity, stars).
# Every one of these is Lv.1 - they are spares and shatter fodder, NOT the
# build. That distinction is the whole reason this list records rarity.
INVENTORY = [
    ("shrink_ray",              "ancestral", 2),
    ("multiverse_nexus",        "ancestral", 2),
    ("death_penalty",           "ancestral", 2),
    ("pulsar_harvester",        "mythic+",   0),
    ("restorative_bonus",       "ancestral", 5),
    ("om_chip",                 "ancestral", 4),
    ("space_displacer",         "ancestral", 4),
    ("havoc_bringer",           "ancestral", 4),
    ("harmony_conductor",       "ancestral", 3),
    ("singularity_harness",     "ancestral", 3),
    ("negative_mass_projector", "ancestral", 3),
    ("orbital_augment",         "ancestral", 2),
    ("wormhole_redirector",     "ancestral", 2),
    ("astral_deliverance",      "ancestral", 2),
    ("magnetic_hook",           "ancestral", 1),
    ("project_funding",         "ancestral", 1),
    ("anti_cube_portal",        "ancestral", 1),
    ("being_annihilator",       "ancestral", 1),
    ("pulsar_harvester",        "epic+",     0),
    ("primordial_collapse",     "epic",      0),
    ("orbital_augment",         "epic",      0),
    ("amplifying_strike",       "epic",      0),
    ("magnetic_hook",           "epic",      0),
    ("sharp_fortitude",         "epic",      0),
    ("shrink_ray",              "epic",      0),
    ("galaxy_compressor",       "epic",      0),
    ("pulsar_harvester",        "epic",      0),
    ("anti_cube_portal",        "epic",      0),
    ("astral_deliverance",      "epic",      0),
    ("being_annihilator",       "epic",      0),
    ("solar_dyson_sphere",      "rare",      0),
]


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


def duplicates_of(slug: str) -> list:
    """Inventory copies of a module, which is what makes it unsafe to equip by
    icon alone - see the note in loadout.apply_modules."""
    return [(s, r, st) for s, r, st in INVENTORY if s == slug]
