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
    checked for the active border, and modules require positive equipped-slot
    evidence. Absence from an inventory viewport never proves equipment.

Restoring matters as much as applying. A quest run that switches to the No
Card preset and does not put the old one back hands the next run an empty
deck - so current_cards() reads what is selected BEFORE a swap, and callers
are expected to restore it afterwards.
"""
import copy
import os

from runtime import logger
from device import act, capture
from interactions import tourney
from vision import detect
from settings import CONFIG, ROOT

CARD_TPL = "cards/preset_{}.png"


def card_tabs() -> tuple[str, ...]:
    """The card preset tabs this install can recognize: one template per tab
    under templates/cards/preset_<name>.png, cut on the Calibrate page.
    Nothing is assumed about how a player named them - a tab with no
    template is invisible here, and the scan says so."""
    from player import accounts
    paths = accounts.template_files(ROOT, CONFIG, "cards/preset_*.png")
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


def unknown_setup(name=None, *, detail='adopted', reason=None):
    return dict(state='unknown', loadout=name, detail=detail, reason=reason,
                continued_with_partial=False, modules=[])


def setup_failure(name, exc):
    report = copy.deepcopy(getattr(exc, 'setup', None))
    if not isinstance(report, dict):
        report = unknown_setup(name, detail='setup_refused')
    report.update(state='degraded', loadout=name, continued_with_partial=False,
                  reason=str(exc), detail='setup_refused')
    return report


class SetupAbort(tourney.Abort):
    def __init__(self, message, setup):
        super().__init__(message)
        self.setup = copy.deepcopy(setup)


class ModuleResults(dict):
    def __init__(self, results, evidence):
        super().__init__(results)
        self.evidence = evidence


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


def _plan_evidence(plan, version, frame):
    from interactions import module_evidence
    evidence = []
    for name, role in plan:
        ok = (module_evidence.verify_v29(name, frame) if version == 'v29'
              else tourney.verify_slot(name, role, frame))
        evidence.append(dict(module=name, role='category' if version == 'v29' else role,
                             state='verified' if ok is True else 'degraded' if ok is False else 'unknown',
                             detail='equipped_header' if ok is True else 'not_in_expected_slot' if ok is False else 'not_verified',
                             reason=None if ok is True else 'module_not_positively_equipped'))
    return evidence


def _verify_plan(plan, version, frame):
    evidence = _plan_evidence(plan, version, frame)
    if any(item['state'] != 'verified' for item in evidence):
        report = unknown_setup(detail='manual_equipment', reason='module_plan_not_verified')
        report.update(state='degraded', modules=evidence)
        raise SetupAbort('module plan is not positively equipped after all changes', report)
    return evidence


def _apply_modules_v29(plan) -> dict:
    """Equip by category, retaining the historical v29 role-label semantics.

    Every already/equipped result is a positive header read. Changes auto-save
    into the selected category preset; callers own declarative restoration.
    """
    import time
    from interactions import module_evidence
    results = {}
    for name, _role in plan:
        frame = module_evidence.settled('v29')
        proof = module_evidence.verify_v29(name, frame)
        if proof is True:
            results[name] = 'already (header)'
            continue
        if proof is None:
            raise tourney.Abort(f'module {name} category or equipped identity is not verifiable')
        frame, point = module_evidence.find_inventory(name, 'v29')
        if point is None:
            raise tourney.Abort(f'module {name} is neither positively equipped nor found in inventory')
        module_evidence.require_screen(frame, 'v29')
        tourney.tap_at(point, f'open module dialog {name}')
        frame, button = tourney.wait_for(V29_EQUIP_BTN, 5.0, thresh=tourney.STRICT)
        from interactions import inventory
        if button is None or not inventory._panel_open(frame):
            raise tourney.Abort(f'owned v29 module dialog for {name} did not open')
        tourney.tap_at(button, f'equip module {name} (v29)')
        # Closing a dialog or losing an inventory tile is only a transition.
        # A role-choice prompt is unsupported here; never guess a button.
        for _ in range(13):
            frame = capture.grab()
            if (tourney.find(frame, 'modules/primary_btn.png', tourney.STRICT)
                    or tourney.find(frame, 'modules/assist_btn.png', tourney.STRICT)):
                raise tourney.Abort('v29 module role chooser is unsupported; no role was selected')
            if not inventory._panel_open(frame):
                module_evidence.require_screen(frame, 'v29')
                break
            time.sleep(.4)
        else:
            raise tourney.Abort(f'equip dialog for {name} did not close')
        frame = module_evidence.settled('v29')
        if module_evidence.verify_v29(name, frame) is not True:
            raise tourney.Abort(f'module {name} equip was not proven in its category slot')
        logger.event('module_equipped_v29', module=name, proof='equipped_header')
        results[name] = 'equipped'
    evidence = _verify_plan(plan, 'v29', module_evidence.settled('v29'))
    logger.event('loadout_modules', plan=results, verified=True)
    tourney.return_to_game('modules')
    return ModuleResults(results, evidence)


def apply_modules(plan) -> dict:
    """Apply and verify the entire ordered plan, including restoration plans.

    A structural v29 row selects its choreography independently of user labels.
    Otherwise only positive legacy header/control evidence permits success or
    mutation; absent v29 tabs do not themselves prove a legacy UI.
    """
    from interactions import module_evidence
    plan = [tuple(entry) for entry in plan]
    version = None
    try:
        tourney.open_nav('modules', 'modules/buy_module.png', 'modules screen')
        header = capture.grab()
        module_evidence.require_screen(header)
        version = module_evidence.ui_version(header)
        if version == 'v29':
            return _apply_modules_v29(plan)
        results = {}
        for name, role in plan:
            results[name] = tourney._equip_module(name, role)
        frame = capture.grab()
        module_evidence.require_screen(frame)
        evidence = _verify_plan(plan, 'legacy', frame)
        logger.event('loadout_modules', plan=results, verified=True)
        tourney.return_to_game('modules')
        return ModuleResults(results, evidence)
    except SetupAbort:
        raise
    except (tourney.Abort, act.TapRefused, detect.TemplateMissing) as exc:
        report = unknown_setup(detail='manual_equipment', reason=str(exc))
        report.update(state='degraded', modules=[
            dict(module=name, role='category' if version == 'v29' else role,
                 state='unknown', detail='not_verified', reason=str(exc))
            for name, role in plan])
        raise SetupAbort(str(exc), report) from exc


def apply(name: str, restore_cards: bool = False) -> dict:
    """Apply required setup; report verified/degraded/unknown to the caller."""
    try:
        return _apply(name, restore_cards)
    except (tourney.Abort, act.TapRefused, detect.TemplateMissing) as exc:
        raise SetupAbort(str(exc), setup_failure(name, exc)) from exc


def _apply(name: str, restore_cards: bool = False) -> dict:
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
        done['setup'] = dict(state='verified', loadout=name,
                             detail='global_preset_selection', reason=None,
                             continued_with_partial=False, modules=[])
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
    done['setup'] = unknown_setup(name, detail='as_is')
    if any(lo.get(key) for key in ('cards', 'guardians', 'modules', 'module_preset',
                                   'guardian_preset', 'workshop_preset', 'bot_preset')):
        evidence = getattr(done.get('modules'), 'evidence', [])
        if lo.get('modules') and not evidence:
            raise tourney.Abort('module application returned no final equipment evidence')
        done['setup'].update(state='verified', detail='manual_equipment', modules=evidence)
        if str(done.get('bot_preset', '')).startswith('FAILED'):
            done['setup'].update(state='degraded', reason='optional_bot_preset_failed')
    logger.event("loadout_applied", **{k: str(v) for k, v in done.items()})
    return done
