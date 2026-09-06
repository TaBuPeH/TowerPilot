"""v29 preset selection - the Global Preset picker and per-category tabs.

The game's v29 update made presets the fast path for build switching: a
Global Preset bundles one preset from each category (workshop / cards /
bots / modules / guardians) and is chosen per-battle from a popup on the
home screen; each category screen also grew its own Preset tab row.

Two hard-won rules shape this module:

1. Preset names are USER-RENAMEABLE, so every button template here is
   account-specific and harvested from that account's own screens (same
   doctrine as cards/preset_*.png). A missing template is a calibration
   gap and fails closed via detect.TemplateMissing - never a blind tap.

2. Category presets AUTO-SAVE mutations (proven live 2026-08-25: a stray
   tap unequipped a card and reloading the preset did NOT restore it).
   Global presets only SELECT category presets; they never restore their
   contents. That is why callers must route manual equipment through a
   scratch preset - see loadout.apply - and why every selection here is
   verified after the fact rather than assumed from having been sent.
   VERIFIED 2026-08-28 (orb test): battle entry under a global preset
   applies the card preset's CURRENT auto-saved contents - a deck edit
   made at a true idle boundary persists through entry. The trap is
   MID-RUN edits: a card swap made while a run is live reverted twice
   with zero bot writes in the log - deck changes stick only when made
   between runs.
"""
import time

from device import capture, act
from interactions import tourney
from runtime import logger
from vision import pills

# The picker popup on the home screen ("SELECT A PRESET"). `None` is a
# first-class choice: the game reads it as "enter with the currently
# active presets", which is exactly what a hand-equipped loadout needs -
# entering with a named Global Preset selected would re-apply it at
# battle entry, over whatever was just equipped.
PICKER_ICON = "presets/picker_icon.png"
# picker_icon_large.png (2026-09-04): a second machine drew the sliders icon
# beside BATTLE at ~91x93 px where the first cut is 70x70 - it scored 0.34
# on the icon and every handoff loadout aborted with "picker icon not
# found". The larger cut self-matches 1.000 on five home shots, 0.667 on the
# white right-rail sliders icon, and nothing else on 700 non-battle
# screenshots reaches 0.6. Same one-control-several-looks pattern as
# tourney.BATTLE_BUTTONS: add YOUR machine's cut here if neither matches.
PICKER_ICONS = (PICKER_ICON, "presets/picker_icon_large.png")
PICKER_HEADER = "presets/select_header.png"
PICKER_CLOSE = "presets/close_x.png"
GP_TPL = "presets/gp_{}.png"

# Category screens' own Preset tab rows. Cards are deliberately absent:
# card presets predate v29 and loadout.apply_cards already selects them
# with the same verify-after-tap discipline. Each entry is an
# (open, close) pair because the categories do not share a navigation
# shape: modules are a bottom-nav tab, guardians live inside the guild
# overlay (guild tile -> Guardian tab).
def _open_modules() -> None:
    tourney.open_nav("modules", "modules/buy_module.png", "modules screen")


def _close_modules() -> None:
    tourney.return_to_game("modules")


def _open_guardians() -> None:
    frame = capture.grab()
    # Already inside the guild overlay (its own tab strip is visible)? Then
    # only the tab tap, which is idempotent - this is also the recovery path
    # after an Abort left the overlay open (the closer never runs on an
    # Abort). The strip is generic: no account template is assumed.
    if tourney.find(frame, "guardian/tab_guardian.png", 0.90):
        tourney.tap_at(tourney.GUARDIAN_TAB, "guardian tab")
        return
    hit = tourney.find(frame, "home/tile_guild.png", 0.90)
    if not hit:
        raise tourney.Abort("guild tile not on the left rail")
    tourney.tap_at(hit[0], "open guild")
    tourney.require("guardian/tab_guardian.png", "guild screen")
    tourney.tap_at(tourney.GUARDIAN_TAB, "guardian tab")


def _close_guardians() -> None:
    tourney.return_to_game("guild")


def _open_bots() -> None:
    """Bots live in the EVENT hub (user, 2026-08-28): home left rail's
    event tile -> Bots tab. HOME ONLY - the loadout handoffs that select
    presets all run from home, and no in-run caller exists yet (the hub is
    also reachable mid-battle via the side menu's star icon; add that
    route when something needs it). The event tile template was harvested
    from a dialog-dimmed frame - CCOEFF matching is normalized, but the
    threshold stays at 0.85 for it until a clean-frame harvest replaces it."""
    frame = capture.grab()
    if not tourney.on_home(frame) and pills.pills(frame, *pills.TAB_BANDS["bots"]):
        return                          # already on the bots screen (tab row up)
    if not tourney.on_home(frame):
        raise tourney.Abort("bots preset screen needs the home screen "
                            "(event tile route)")
    frame, pt = tourney.require("home/tile_event.png", "event tile",
                                thresh=0.85)
    tourney.tap_at(pt, "open event hub")
    frame, pt = tourney.require("buttons/event_bots_tab.png",
                                "event Bots tab")
    tourney.tap_at(pt, "event: Bots tab")


def _close_bots() -> None:
    tourney.return_to_game("event")


def _open_workshop() -> None:
    """Workshop is a bottom-nav tab like modules. The landmark is its own
    preset tab row, found STRUCTURALLY (vision.pills) rather than by a
    template: the tab labels are the player's own names, so no template of
    them can be assumed to exist before calibration."""
    frame = capture.grab()
    band = pills.TAB_BANDS["workshop"]
    if not tourney.on_home(frame) and pills.pills(frame, *band):
        return                          # already on the workshop screen
    tourney.tap_at(tourney.NAV["workshop"], "nav workshop")
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        frame = capture.grab()
        if pills.pills(frame, *band):
            return
        time.sleep(0.4)
    logger.shot(frame, "tourney_missing_workshop preset tab row")
    raise tourney.Abort("workshop screen did not show its preset tab row")


def _close_workshop() -> None:
    tourney.return_to_game("workshop")


CATEGORY_NAV = {
    "modules": (_open_modules, _close_modules),
    "guardians": (_open_guardians, _close_guardians),
    "bots": (_open_bots, _close_bots),
    "workshop": (_open_workshop, _close_workshop),
}
CAT_TPL = "presets/{}_{}.png"


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name.strip().lower())


def available() -> bool:
    """Does this account have the Global Presets lab (= harvested picker)?

    Capability is decided by the presence of the harvested template, not by
    guessing from screen state: an un-calibrated account must never open a
    popup it has no templates to read.
    """
    from vision import detect
    for rel in PICKER_ICONS:
        try:
            detect._tpl(rel)
            return True
        except detect.TemplateMissing:
            continue
    return False


def _open_picker() -> "tuple":
    """From home, open the SELECT A PRESET popup. Returns the popup frame."""
    frame = capture.grab()
    if not tourney.on_home(frame):
        raise tourney.Abort("global preset picker needs the home screen")
    # require() for a set of variants: whichever cut this client draws.
    deadline = time.monotonic() + 6.0
    pt = None
    while time.monotonic() < deadline:
        frame = capture.grab()
        pt, _rel = tourney.find_any(frame, PICKER_ICONS)
        if pt:
            break
        time.sleep(0.4)
    if pt is None:
        logger.shot(frame, "tourney_missing_global preset picker icon")
        raise tourney.Abort("expected global preset picker icon "
                            f"({' | '.join(PICKER_ICONS)}) on screen, not found")
    tourney.tap_at(pt, "open global preset picker")
    frame, hit = tourney.wait_for(PICKER_HEADER, 5.0)
    if not hit:
        raise tourney.Abort("preset picker did not open (header not found)")
    return frame


def _close_picker() -> None:
    frame, pt = tourney.require(PICKER_CLOSE, "preset picker close X")
    tourney.tap_at(pt, "close global preset picker")
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if tourney.on_home(capture.grab()):
            return
        time.sleep(0.4)
    raise tourney.Abort("preset picker would not close")


def select_global(name: "str | None") -> str:
    """Select a Global Preset (or None) in the per-battle picker.

    Returns 'already' or 'selected'. The tap is CONFIRMED after the fact -
    a swallowed tap is an Abort, never a silent success, because whatever
    is selected here is what the next BATTLE press applies to the run.
    """
    from interactions.loadout import _tab_active
    label = name if name is not None else "none"
    tpl = GP_TPL.format(_slug(label))
    frame = _open_picker()
    frame, pt = tourney.require(tpl, f"global preset button {label!r}")
    if _tab_active(frame, pt):
        logger.event("preset_global", preset=label, result="already")
        _close_picker()
        return "already"
    tourney.tap_at(pt, f"select global preset {label}")
    frame, pt = tourney.require(tpl, f"global preset button {label!r}")
    if not _tab_active(frame, pt):
        logger.event("preset_global_swallowed", preset=label,
                     shot=logger.shot(frame, "preset_global_swallowed"))
        raise tourney.Abort(f"global preset {label!r} would not select")
    logger.event("preset_global", preset=label, result="selected")
    _close_picker()
    return "selected"


def verify_global(name: "str | None") -> bool:
    """READ-ONLY: does the picker currently select `name`? Opens the popup,
    reads the active state, closes it - selects nothing. The last gate
    before a tournament ticket is spent on a global-preset loadout."""
    from interactions.loadout import _tab_active
    label = name if name is not None else "none"
    frame = _open_picker()
    hit = tourney.find(frame, GP_TPL.format(_slug(label)), 0.90)
    ok = bool(hit) and _tab_active(frame, hit[0])
    _close_picker()
    logger.event("preset_global_verify", preset=label, ok=ok)
    return ok


def select_category(category: str, preset_name: str) -> str:
    """Select a named preset on a category screen's Preset tab row.

    The primary v29 mechanism for specialized builds, and the protection
    against manual-swap corruption: selecting a preset FIRST means any
    later mutation lands in that preset, not in the farming one.
    """
    from interactions.loadout import _tab_active
    if category not in CATEGORY_NAV:
        raise tourney.Abort(
            f"category {category!r} has no preset navigation yet "
            f"(known: {', '.join(sorted(CATEGORY_NAV))}) - harvest its "
            f"templates and add it to CATEGORY_NAV")
    opener, closer = CATEGORY_NAV[category]
    tpl = CAT_TPL.format(category, _slug(preset_name))
    # The v29 tab rows carry active/inactive in an outline around the whole
    # tab, not behind the text - sample a box tall and wide enough to reach
    # it (see _tab_active).
    TAB_HALF = (240, 60)
    opener()
    frame, pt = tourney.require(tpl, f"{category} preset tab {preset_name!r}")
    if _tab_active(frame, pt, TAB_HALF):
        logger.event("preset_category", category=category,
                     preset=preset_name, result="already")
        closer()
        return "already"
    tourney.tap_at(pt, f"select {category} preset {preset_name}")
    frame, pt = tourney.require(tpl, f"{category} preset tab {preset_name!r}")
    if not _tab_active(frame, pt, TAB_HALF):
        logger.event("preset_category_swallowed", category=category,
                     preset=preset_name,
                     shot=logger.shot(frame, "preset_category_swallowed"))
        raise tourney.Abort(
            f"{category} preset {preset_name!r} would not select")
    logger.event("preset_category", category=category,
                 preset=preset_name, result="selected")
    closer()
    return "selected"
