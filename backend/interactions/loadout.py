"""A LOADOUT is what you equip; a RUN is what you then do with it.

Everything the game lets you configure before a battle - cards, guardian
chips, modules, ultimate weapons - is one named bundle here, defined in
config.yaml under `loadouts:` and applied by name.

Why this exists as its own module: the same four things were hardcoded as
module-level constants in tourney.py AND flows/shard.py, which meant a run type
could only ever have ONE loadout. Three tournament entries with three
different module sets was not expressible at all - the plan was a constant.
Naming loadouts in config makes that a data change instead of a code change.

Every section is OPTIONAL and every section is IDEMPOTENT:

  * a loadout that names no modules leaves the modules alone - it does not
    unequip anything. "Not mentioned" means "not this loadout's business",
    which is what lets the No Card quest loadout touch only the cards.
  * applying a loadout twice is the same as applying it once. Card presets are
    checked for the active border before tapping, and an equipped module is
    absent from the inventory, which _equip_module already reads as "already".

Restoring matters as much as applying. A quest run that switches to the No
Card preset and does not put the old one back hands the next run an empty
deck - so current_cards() reads what is selected BEFORE a swap, and callers
are expected to restore it afterwards.
"""
import glob
import os

from runtime import logger
from device import capture
from interactions import tourney
from settings import CONFIG, ROOT

CARD_TPL = "cards/preset_{}.png"


def card_tabs() -> tuple[str, ...]:
    """The card preset tabs this install can recognize: one template per tab
    under templates/cards/preset_<name>.png, cut on the Calibrate page.
    Nothing is assumed about how a player named them - a tab with no
    template is invisible here, and the scan says so."""
    paths = sorted(glob.glob(str(ROOT / "templates" / "cards" / "preset_*.png")))
    return tuple(os.path.basename(p)[len("preset_"):-4] for p in paths)


# Measured on the live tab row: the selected tab is outlined GREEN, the rest
# CYAN, and the label art is identical either way - so selection is decided by
# which hue is present, not by a threshold either has to clear.
MODULE_GRID = (1100, 2200)
# The equipped modules are drawn on the tower diagram above the tab row. Their
# art is a different size there than in the grid, so they need their own
# templates - templates/modules/equipped/<slug>.png - cut from the header.
HEADER_BAND = (250, 780)
EQUIPPED_THRESH = 0.88


def spec(name: str) -> dict:
    """The named loadout, or {} - an unknown name is never silently ignored."""
    lo = (CONFIG.get("loadouts") or {}).get(name)
    if lo is None:
        raise tourney.Abort(f"no loadout named '{name}' in config.yaml")
    lo = lo or {}
    if lo.get("defined") is False:
        # A placeholder, not a loadout. Refusing is the whole point: an empty
        # loadout equips nothing and returns cleanly, so the run that used it
        # would look successful and only reveal the mistake at the end.
        raise tourney.Abort(f"loadout '{name}' is declared but not defined yet")
    return lo


def _tab_active(frame, pt, half=(95, 40)) -> bool:
    """Is the tab/button under `pt` in its ACTIVE (green) styling?

    Comparative green-vs-cyan over a box around the template hit. `half` is
    the box half-size: the default suits card tabs and picker buttons, whose
    styling surrounds the text; the v29 category tab rows carry their state
    in an OUTLINE ~45px above/below the text, so their callers pass a taller,
    wider box (verified offline on live frames 2026-08-27: active green
    0.042-0.052 vs 0, inactive cyan 0.052-0.069 vs 0 at (240, 60))."""
    import cv2
    x0, x1 = max(0, pt[0] - half[0]), pt[0] + half[0]
    y0, y1 = max(0, pt[1] - half[1]), pt[1] + half[1]
    box = frame[y0:y1, x0:x1]
    if box.size == 0:
        return False
    hsv = cv2.cvtColor(box, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    lit = (s > 110) & (v > 140)
    green = float(((h > 40) & (h < 80) & lit).mean())
    cyan = float(((h > 85) & (h < 105) & lit).mean())
    return green > cyan


def current_cards(frame=None) -> str | None:
    """Which card preset is selected right now, by tab name.

    Read before a swap so it can be put back. Returns None if nothing reads as
    selected, and callers must treat that as "do not restore" rather than
    guessing - restoring the wrong deck is worse than leaving this one.
    """
    frame = frame if frame is not None else capture.grab()
    for name in card_tabs():
        hit = tourney.find(frame, CARD_TPL.format(name), 0.90)
        if hit and _tab_active(frame, hit[0]):
            return name
    return None


def apply_cards(preset: str) -> str:
    """Select a card preset tab. Returns 'already' or 'loaded'.

    THE TAP IS CONFIRMED AFTER THE FACT, not assumed from having been sent.
    This used to check `_tab_active` only BEFORE tapping and then report
    "loaded" unconditionally - which reports success for a tap the game
    swallowed. That is not hypothetical: the identical class was proved live on
    acct2 (2026-08-19) on the exit dialog, where end_round_yes matched at 0.99
    on the timeout shot - found, tapped, and swallowed during a fade-in.

    A deck is not something to be wrong about quietly: the caller that matters
    is the tournament entry, which pays a ticket for whatever is selected. So
    re-grab, re-read the tab, and tap once more if it did not take; a second
    failure is an Abort, never a silent "loaded".
    """
    tourney.open_nav("cards", CARD_TPL.format(preset), "cards screen")
    frame, pt = tourney.require(CARD_TPL.format(preset), f"{preset} preset tab")
    if _tab_active(frame, pt):
        logger.event("loadout_cards", preset=preset, result="already")
        tourney.return_to_game("cards")
        return "already"
    for attempt in (1, 2):
        tourney.tap_at(pt, f"load card preset {preset}")
        frame, pt = tourney.require(CARD_TPL.format(preset),
                                    f"{preset} preset tab")
        if _tab_active(frame, pt):
            logger.event("loadout_cards", preset=preset, result="loaded",
                         attempts=attempt)
            tourney.return_to_game("cards")
            return "loaded"
        logger.event("loadout_cards_swallowed", preset=preset,
                     attempt=attempt,
                     shot=logger.shot(frame, "loadout_cards_swallowed"))
    raise tourney.Abort(f"card preset {preset!r} would not select: the tab is "
                        f"still not active after two taps")


V29_EQUIP_BTN = "modules/v29_equip_btn.png"


def _apply_modules_v29(plan) -> dict:
    """Equip a module plan on the redesigned v29 modules screen.

    Live-verified 2026-08-27 (space_displacer <-> sharp_fortitude, both
    directions): tapping a module's inventory icon opens its detail
    dialog; the single Equip button places it in its category's slot; the
    dialog closes itself; the displaced module returns to the inventory
    grid and the equipped one leaves it. Slot names in the plan are
    ignored - v29 has no primary/assist buttons, the category decides.

    PRESETS AUTO-SAVE: every equip here permanently mutates the ACTIVE
    module preset. Callers declare that preset via `module_preset` (the
    validator's corruption advisory enforces the habit) and own restoring
    displaced modules afterwards (`modules_restore` in quest loadouts).
    """
    import time
    from vision import detect
    results = {}
    for name, _slot in plan:
        detect._tpl(f"modules/{name}.png")   # fail closed on missing template
        frame = capture.grab()
        hit = tourney.find(frame, f"modules/{name}.png", 0.95)
        if not hit:
            # Equipped modules leave the inventory grid (verified live), so
            # absence normally means "already equipped". It can also mean
            # "below the fold" - not distinguishable without scrolling, so
            # say it LOUDLY: nothing taps blind either way, and a module
            # misread as equipped shows up as a quest that farms zero.
            logger.event("module_assumed_equipped", module=name,
                         shot=logger.shot(frame, f"module_assumed_{name}"))
            results[name] = "already (not in inventory)"
            continue
        tourney.tap_at(hit[0], f"open module dialog {name}")
        frame, btn = tourney.wait_for(V29_EQUIP_BTN, 5.0)
        if not btn:
            logger.shot(frame, f"module_dialog_missing_{name}")
            raise tourney.Abort(f"module dialog for {name} did not open "
                                f"(no Equip button)")
        tourney.tap_at(btn, f"equip module {name} (v29)")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            frame = capture.grab()
            if not tourney.find(frame, V29_EQUIP_BTN, 0.95):
                break
            time.sleep(0.4)
        else:
            logger.shot(frame, f"module_dialog_stuck_{name}")
            raise tourney.Abort(f"equip dialog for {name} would not close")
        # the equip is confirmed by the icon LEAVING the inventory grid
        frame = capture.grab()
        if tourney.find(frame, f"modules/{name}.png", 0.95):
            logger.shot(frame, f"module_equip_swallowed_{name}")
            raise tourney.Abort(f"module {name} still in the inventory "
                                f"after Equip - the equip did not take")
        logger.event("module_equipped_v29", module=name)
        results[name] = "equipped"
    logger.event("loadout_modules", plan=results)
    tourney.return_to_game("modules")
    return results


def apply_modules(plan) -> dict:
    """Equip a module plan: [[name, slot], ...], in the order given.

    Order is load-bearing, not cosmetic - equipping one module DISPLACES
    whatever held its slot, and the Transfer Level prompt moves the levels
    across. The shard loadout depends on exactly this to free Primordial
    Collapse before claiming Primary.
    """
    # A MISSING TEMPLATE MUST NOT BE SILENT. find() returns None for one,
    # _scan_grid reads that as "not in the inventory", and _equip_module reads
    # that as "already equipped" - so a typo or an unharvested module would be
    # skipped while the log said everything was in place.
    #
    # But "already equipped" is often the TRUTH, and it is unharvestable: the
    # game removes an equipped module from the inventory grid, so a module that
    # never leaves the build can never have a grid template cut for it. That is
    # not a reason to refuse the loadout - it is a reason to stop INFERRING the
    # answer. An equipped module is drawn in the header, so look there and
    # confirm it positively; only a module that is in neither place is a fault.
    from vision import detect
    tourney.open_nav("modules", "modules/buy_module.png", "modules screen")
    header = capture.grab()
    # v29 (2026-08-27): the redesigned modules screen carries preset tabs
    # (as the player named them) - its equip choreography is a
    # different flow, live-verified the same day.
    if (tourney.find(header, "presets/modules_farm.png", 0.85)
            or tourney.find(header, "presets/modules_tourney.png", 0.85)):
        return _apply_modules_v29(plan)
    missing = []
    confirmed = {}
    for name, _ in plan:
        try:
            detect._tpl(f"modules/{name}.png")
            continue
        except detect.TemplateMissing:
            pass
        hit = tourney._find_in_band(header, f"modules/equipped/{name}.png",
                                    *HEADER_BAND, EQUIPPED_THRESH)
        if hit:
            confirmed[name] = "already (header)"
        else:
            missing.append(name)
    if missing:
        raise tourney.Abort("no template for module(s), and not equipped: "
                            + ", ".join(missing))
    todo = [(n, s) for n, s in plan if n not in confirmed]
    rels = [f"modules/{n}.png" for n, _ in todo]
    present = set(tourney._scan_grid(rels, *MODULE_GRID))
    results = dict(confirmed)
    # THE BATCH SCAN IS ONLY VALID UNTIL THE FIRST EQUIP. _scan_grid's contract
    # says "absent does not go stale, because equipping one module cannot make
    # another appear" - which is false in the one case that matters here.
    # Equipping DISPLACES the module holding the slot, and a displaced module
    # lands back in the grid. Live: equipping Multiverse Nexus as primary threw
    # Primordial Collapse out of the build, but PC had already been recorded
    # absent-therefore-equipped by the pre-swap scan, so the loadout finished
    # reporting success with PC not equipped at all.
    # After any equip, later lookups get a fresh frame instead (present=None).
    for name, slot in todo:
        results[name] = tourney._equip_module(name, slot, present)
        if results[name] == "equipped":
            present = None
    logger.event("loadout_modules", plan=results)
    tourney.return_to_game("modules")
    return results


def apply(name: str, restore_cards: bool = False) -> dict:
    """Apply a named loadout. Returns what it did, plus what it displaced.

    v29 ordering matters here:
    - `global_preset` bodies do ONE thing - select that Global Preset in the
      home-screen picker - because the game applies it wholesale at battle
      entry (the validator refuses mixed bodies).
    - Everything else (category presets, card presets, manual equipment)
      must end with the picker on NONE, or battle entry would re-apply a
      stale Global Preset over what was just equipped.

    `restore_cards` records the preset that was selected before the swap, so a
    short-lived run (the No Card quest) can hand it back afterwards. It does
    NOT restore anything itself - the run decides when it is finished.
    """
    from interactions import presets
    lo = spec(name)
    done = {"loadout": name}
    if lo.get("global_preset"):
        done["global_preset"] = presets.select_global(lo["global_preset"])
        logger.event("loadout_applied",
                     **{k: str(v) for k, v in done.items()})
        return done
    # Category-preset selections come FIRST: mutations from any later manual
    # equipment then land in the selected (scratch) preset, never in the
    # farming one - v29 presets auto-save, and nothing restores them.
    for cat_key, category in (("module_preset", "modules"),
                              ("guardian_preset", "guardians"),
                              ("workshop_preset", "workshop"),
                              ("bot_preset", "bots")):
        if not lo.get(cat_key):
            continue
        if cat_key == "bot_preset":
            # DEGRADE, don't abort: the bots screen lives in the EVENT hub,
            # whose availability/route can shift between events - and a
            # shard run on the wrong bots still farms shards, while an
            # aborted handoff closes the whole block for the day. Loud
            # sentinel, never a silent pass.
            try:
                done[cat_key] = presets.select_category(category, lo[cat_key])
            except tourney.Abort as e:
                logger.event("bot_preset_failed", preset=lo[cat_key],
                             error=str(e))
                done[cat_key] = "FAILED (see bot_preset_failed event)"
            continue
        done[cat_key] = presets.select_category(category, lo[cat_key])
    if restore_cards and lo.get("cards"):
        tourney.open_nav("cards", CARD_TPL.format(lo["cards"]), "cards screen")
        done["previous_cards"] = current_cards()
        tourney.return_to_game("cards")
    if lo.get("cards"):
        done["cards"] = apply_cards(lo["cards"])
    if lo.get("guardians"):
        # A loadout may NAME its chips. The coin farm needs fetch/bounty/summon
        # put back after a tournament borrows the slots, so "guardians: true"
        # (meaning "do the tournament swap") is not expressive enough.
        chips = lo["guardians"]
        chips = tuple(chips) if isinstance(chips, (list, tuple)) else tourney.CHIPS_IN
        tourney.guardian_swap(chips)
        done["guardians"] = ",".join(chips)
    if lo.get("modules"):
        done["modules"] = apply_modules([tuple(m) for m in lo["modules"]])
    # Anything hand-assembled must enter battle under "None", or the game
    # re-applies the still-selected Global Preset at entry and wipes it.
    if presets.available():
        done["picker"] = presets.select_global(None)
    logger.event("loadout_applied", **{k: str(v) for k, v in done.items()})
    return done
