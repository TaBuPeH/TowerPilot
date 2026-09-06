"""Auto-calibration: cut this account's own templates from its own screens.

Why: card preset tabs, preset picker rows, category preset tabs and module
icons are the player's own - names they typed, icons at their rarity - and
they are the game's art besides, so none of it ships with the repo (CLAUDE.md
rule 8). The dashboard's Calibrate button drives this: it walks the menus
the way the runs do, finds every pill structurally (vision/pills.py), reads
its label with Windows OCR (vision/textocr.py), cuts the template, verifies
it against the frame it came from, and records the names in the draft
profile so the scan and the loadouts can refer to them.

Phases (selectable, resumable, stop flag - the scan.py contract):
  c  cards      cards screen tab row        -> cards/preset_<slug>.png
  g  global     home preset picker rows     -> presets/gp_<slug>.png
  m  modules    modules screen: tab row     -> presets/modules_<slug>.png
                header slots (tap -> panel) -> modules/equipped/<slug>.png
                inventory tiles (tap)       -> modules/<slug>.png
  u  guardians  guild > Guardian tab row    -> presets/guardians_<slug>.png
  b  bots       event hub > Bots tab row    -> presets/bots_<slug>.png
  w  workshop   workshop tab row            -> presets/workshop_<slug>.png

Rules: it writes ONLY account-specific template names (never a shipped
one); it never overwrites an existing file unless --overwrite (hard rule 6:
a detector never rewrites its own ground truth - this is the second
human-triggered writer, beside the cropper); every cut is verified on its
source frame (self-match, next-best); it taps only inside menus it opened
itself and returns home after each; and it refuses to start unless the home
screen is up and no runner is alive (scan.preflight).
"""
import argparse
import datetime
import difflib
import json
import os
import time

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import cv2

import settings
from vision import pills
from vision.pills import TAB_BANDS

ACCOUNT_PREFIXES = ("cards/preset_", "presets/gp_", "presets/modules_",
                    "presets/guardians_", "presets/workshop_", "presets/bots_",
                    "modules/equipped/")
RARITIES = ("ancestral", "mythic", "legendary", "epic", "rare", "common")
PHASE_KEYS = "c,g,m,u,b,w"


class Stopped(Exception):
    """The dashboard's Stop flag was seen inside a phase: the phase is left
    'stopped', never 'done' - a half-walked grid must not read as the
    account's inventory."""


# ------------------------------------------------------------- plumbing
def _paths() -> dict:
    from settings import CONFIG
    inst = CONFIG.get("active_instance", "main")
    logs = os.path.join("logs", inst)
    os.makedirs(logs, exist_ok=True)
    return {"state": os.path.join(logs, "calibrate_state.json"),
            "stop": os.path.join(logs, "calibrate_stop"),
            "report": os.path.join(logs, "calibrate_report.json"),
            "evidence": os.path.join(logs, "calibrate_evidence")}


def _state_load(p) -> dict:
    try:
        with open(p["state"], encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"phases": {}}


def _state_save(p, st) -> None:
    tmp = p["state"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=1)
    os.replace(tmp, p["state"])


def _stop_requested(p) -> bool:
    return os.path.exists(p["stop"])


def _evidence(p, frame, name: str) -> str:
    os.makedirs(p["evidence"], exist_ok=True)
    path = os.path.join(p["evidence"], f"{name}.png")
    cv2.imwrite(path, frame)
    return path


def slug(text: str) -> str:
    """Preset name as typed -> template stem (one definition: presets._slug)."""
    from interactions.presets import _slug
    return _slug(text)


def template_path(rel: str):
    return settings.ROOT / "templates" / rel


def is_account_rel(rel: str) -> bool:
    """Only the player's own templates may be written here - never a shipped
    button, screen or glyph."""
    if rel.startswith(ACCOUNT_PREFIXES) and rel.endswith(".png"):
        return "/" not in rel[len("presets/"):] if rel.startswith("presets/") else True
    if rel.startswith("modules/") and rel.endswith(".png") \
            and "/" not in rel[len("modules/"):]:
        from player import catalogue
        return rel[len("modules/"):-4] in catalogue.all_modules()
    return False


def write_template(rel: str, crop, overwrite: bool = False) -> str:
    """'written' | 'exists' (kept, no overwrite) | 'refused' (not an
    account-specific name)."""
    if not is_account_rel(rel) or crop is None or crop.size == 0:
        return "refused"
    path = template_path(rel)
    if path.exists() and not overwrite:
        return "exists"
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), crop)
    return "written"


def resolve_module(text: str | None) -> str | None:
    """OCR'd module name -> catalogue slug, tolerating OCR slips - against
    the shipped table AND what this install learned, or 'Orbitat Sail' (one
    glyph off a learned 'Orbital Sail', 2026-09-06) becomes a second module."""
    from player import catalogue
    t = (text or "").strip()
    if not t:
        return None
    try:
        return catalogue.resolve(t)
    except KeyError:
        pass
    names = {n.lower(): s for s, (n, _a) in catalogue.all_modules().items()}
    hit = difflib.get_close_matches(t.lower(), list(names), n=1, cutoff=0.72)
    return names[hit[0]] if hit else None


def looks_like_module_name(text: str | None) -> bool:
    """A detail-panel line that can only be a module's name: letters, spaces
    and hyphens, 3-32 chars, not a rarity word, no digits (effect lines
    carry numbers: 'x14.48 Tower Damage')."""
    t = (text or "").strip()
    if not 3 <= len(t) <= 32 or parse_rarity(t) is not None:
        return False
    return all(c.isalpha() or c in " -'" for c in t) and any(c.isalpha() for c in t)


def module_slug(text: str | None) -> str | None:
    """OCR'd name -> slug: shipped table, fuzzy match, else LEARN it - the game
    keeps adding modules and the panel is the ground truth for their names."""
    s = resolve_module(text)
    if s is not None or not looks_like_module_name(text):
        return s
    from player import catalogue
    from runtime import logger
    s = catalogue.learn(text.strip())
    logger.event("calibrate_module_learned", name=text.strip(), slug=s)
    return s


def parse_rarity(text: str | None) -> str | None:
    """'ANCESTRAL' / 'Mythic+' / OCR-mangled variants -> 'ancestral', 'mythic+'."""
    t = (text or "").strip().lower()
    if not t:
        return None
    for token in t.replace("+", " + ").split():
        hit = difflib.get_close_matches(token, RARITIES, n=1, cutoff=0.7)
        if hit:
            return hit[0] + ("+" if "+" in t else "")
    return None


# ----------------------------------------------------------- the harvest
class Calibration:
    def __init__(self, p, overwrite: bool):
        self.p, self.overwrite = p, overwrite
        self.entries: list[dict] = []
        self.player: dict = {}

    def cut(self, phase: str, rel: str, crop, frame, name: str, extra=None) -> dict:
        from runtime import logger
        best, centre, second = pills.match(frame, crop)
        status = write_template(rel, crop, self.overwrite)
        fresh = None
        if status == "exists":
            # the file that is already there is what the runs will use: score
            # THAT against this machine's frame; the fresh cut's own score
            # rides along so the report can say "Overwrite would fix this"
            old = cv2.imread(str(template_path(rel)))
            if old is not None:
                fresh = best
                best, centre, second = pills.match(frame, old)
        entry = {"phase": phase, "rel": rel, "name": name, "status": status,
                 "self": best, "next": second, "at": list(centre),
                 "t": time.time(), **(extra or {})}
        if fresh is not None:
            entry["fresh"] = fresh
        self.entries = [e for e in self.entries if e["rel"] != rel] + [entry]
        logger.event("calibrate_cut", **{k: v for k, v in entry.items() if k != "t"})
        return entry

    def save_report(self) -> None:
        tmp = self.p["report"] + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"entries": self.entries, "player": self.player,
                       "written_at": datetime.datetime.now().isoformat(timespec="seconds")},
                      fh, indent=1)
        os.replace(tmp, self.p["report"])


def harvest_row(cal: Calibration, frame, band, prefix: str, phase: str,
                all_rows: bool = False) -> list[tuple[str, str, str]]:
    """Cut every pill label in `band` as `<prefix><slug>.png`. Returns
    (name as read, slug, state) per pill - the TOP row only unless all_rows
    (the picker lays its presets out over several rows)."""
    from runtime import logger
    from vision import textocr
    ps = pills.pills(frame, *band)
    rows = pills.rows_of(ps)
    if not rows:
        logger.event("calibrate_no_pills", phase=phase, band=list(band),
                     shot=logger.shot(frame, f"calibrate_{phase}_no_pills"))
        return []
    chosen = [p for row in rows for p in row] if all_rows else rows[0]
    out = []
    for i, p in enumerate(chosen):
        crop, trect = pills.text_crop(frame, p["rect"])
        if crop is None:
            logger.event("calibrate_pill_unreadable", phase=phase, rect=list(p["rect"]))
            continue
        text = textocr.read_text(crop)
        name = text or f"tab{i + 1}"
        s = slug(name)
        cal.cut(phase, f"{prefix}{s}.png", crop, frame, name,
                {"state": p["state"], "ocr": text, "rect": list(trect)})
        out.append((name, s, p["state"]))
    return out


def _inspect(cx: int, cy: int):
    """Tap a module (header slot or grid tile), read the detail panel's
    rarity and name, close it. (None, None) when no panel opened."""
    from device import act, capture
    from interactions import inventory
    from vision import textocr
    act.tap(cx, cy, "calibrate: inspect module")
    panel = None
    for _ in range(8):
        time.sleep(inventory.PANEL_WAIT / 2)
        f = capture.grab()
        if inventory._panel_open(f):
            panel = f
            break
    if panel is None:
        return None, None
    name = rarity = None
    xpt = inventory._find_close(panel)
    if xpt is not None:
        head = panel[xpt[1] + inventory.HEAD_DY[0]:xpt[1] + inventory.HEAD_DY[1],
                     inventory.HEAD_X[0]:inventory.HEAD_X[1]]
        texts = [t for _, _, t in textocr.read_lines(head, 2.0)]
        for t in texts:
            if rarity is None and parse_rarity(t) and resolve_module(t) is None:
                rarity = parse_rarity(t)
            elif name is None and resolve_module(t):
                name = t
        if name is None:
            name = next((t for t in texts if parse_rarity(t) is None), None)
    if not inventory._close_panel():
        raise RuntimeError("module detail panel would not close")
    return name, rarity


def _walk_grid(cal: Calibration):
    """Every inventory tile: tap, read, cut `modules/<slug>.png` for the
    first copy of each module. inventory.sweep's paging, with the tile rows
    read off each frame (the lattice shifts when "New" badges show)."""
    from device import capture
    from interactions import inventory
    from runtime import logger
    if not inventory._close_panel():
        raise RuntimeError("a modal is open and will not close - not walking the grid")
    inventory.park_top()
    seen: list[tuple] = []                      # (icon, slug or None, rarity)
    placed: list[tuple[int, int]] = []          # (absolute y, col) of tiles already counted
    slugs, copies = [], []
    offset = 0                                  # measured scroll so far, px
    for page in range(inventory.MAX_PAGES):
        if _stop_requested(cal.p):
            raise Stopped(f"modules grid page {page}")
        grid = inventory.settle()
        _evidence(cal.p, grid, f"modules_grid_{page}")
        rows = pills.grid_rows(grid)          # whole rows only; none = nothing to read
        tiles = [(r, cy, c, cx, inventory._tile_icon(grid, cx, cy))
                 for r, cy in enumerate(rows) for c, cx in enumerate(inventory.COL_X)]
        tiles = [t for t in tiles if not inventory._blank_tile(t[4])]
        new = 0
        page_cuts: list[dict] = []
        for r, cy, c, cx, icon in tiles:
            if _stop_requested(cal.p):
                raise Stopped(f"modules grid page {page}")
            ay = offset + cy
            if any(abs(ay - py) < 80 and c == pc for py, pc in placed):
                continue                        # the overlap rows: counted on the page before
            placed.append((ay, c))
            known = next(((s, rar) for ic, s, rar in seen if inventory._same_icon(icon, ic)), None)
            if known is not None:
                # the same tile again = another copy of a module already read
                # (same icon, same rarity frame) - counted without a tap
                if known[0]:
                    copies.append({"slug": known[0], "rarity": known[1], "page": page,
                                   "row": r, "col": c})
                continue
            name, rarity = _inspect(cx, cy)
            s = module_slug(name)
            seen.append((icon, s, rarity))
            new += 1
            if s is None:
                logger.event("calibrate_module_unknown", where="grid", page=page,
                             row=r, col=c, ocr=name)
                continue
            copies.append({"slug": s, "rarity": rarity, "page": page, "row": r, "col": c})
            if s not in slugs:
                page_cuts.append(cal.cut("modules", f"modules/{s}.png", icon, grid, name,
                                         {"rarity": rarity, "page": page, "row": r, "col": c}))
                slugs.append(s)
        for entry in page_cuts:
            # copies of the cut module on the same page: its next-best score
            # is legitimately ~1.0 then, and the report shows why
            same = sum(1 for cp in copies
                       if cp["page"] == page and cp["slug"] == entry["rel"][8:-4])
            if same > 1:
                entry["copies_on_page"] = same
        moved = inventory.next_page()
        logger.event("calibrate_grid_page", page=page, new=new, total=len(copies),
                     tiles=len(placed), moved=moved, offset=offset)
        if moved is None:
            raise RuntimeError("inventory scroll lost its overlap - rows may have been skipped")
        if moved == 0:
            break
        offset += moved
    return slugs, copies


# ---------------------------------------------------------------- phases
def phase_cards(cal: Calibration) -> dict:
    from device import capture
    from interactions import tourney
    tourney.open_nav("cards", "cards/active_label.png", "cards screen")
    time.sleep(0.6)
    frame = capture.grab()
    _evidence(cal.p, frame, "cards")
    tabs = harvest_row(cal, frame, TAB_BANDS["cards"], "cards/preset_", "cards")
    cal.player["card_presets"] = [s for _n, s, _st in tabs]
    current = [s for _n, s, st in tabs if st == "green"]
    if current:
        cal.player["cards_current"] = current[0]
    tourney.return_to_game("cards")
    return {"card_presets": cal.player["card_presets"], "names": [n for n, _s, _st in tabs]}


def phase_global(cal: Calibration) -> dict:
    from device import capture
    from interactions import presets
    presets._open_picker()
    time.sleep(0.4)
    frame = capture.grab()
    _evidence(cal.p, frame, "picker")
    rows = harvest_row(cal, frame, TAB_BANDS["picker"], "presets/gp_", "global",
                       all_rows=True)
    names = [n for n, s, _st in rows if s != "none"]
    cal.player["global_presets"] = names
    presets._close_picker()
    return {"global_presets": names}


def _category_row(cal: Calibration, category: str, opener, closer) -> dict:
    from device import capture
    opener()
    time.sleep(0.6)
    frame = capture.grab()
    _evidence(cal.p, frame, category)
    rows = harvest_row(cal, frame, TAB_BANDS[category], f"presets/{category}_", category)
    names = [n for n, _s, _st in rows]
    cal.player.setdefault("category_presets", {})[category] = names
    closer()
    return {category: names}


def phase_guardians(cal: Calibration) -> dict:
    from interactions import presets
    return _category_row(cal, "guardians", presets._open_guardians, presets._close_guardians)


def phase_bots(cal: Calibration) -> dict:
    from interactions import presets
    return _category_row(cal, "bots", presets._open_bots, presets._close_bots)


def phase_workshop(cal: Calibration) -> dict:
    from interactions import presets
    return _category_row(cal, "workshop", presets._open_workshop, presets._close_workshop)


def phase_modules(cal: Calibration) -> dict:
    from device import capture
    from interactions import tourney
    from runtime import logger
    tourney.open_nav("modules", "modules/buy_module.png", "modules screen")
    time.sleep(0.6)
    frame = capture.grab()
    _evidence(cal.p, frame, "modules")
    rows = harvest_row(cal, frame, TAB_BANDS["modules"], "presets/modules_", "modules")
    cal.player.setdefault("category_presets", {})["modules"] = [n for n, _s, _st in rows]
    equipped = []
    for slot in pills.header_slots(frame):
        if not slot["occupied"]:
            continue
        cx, cy = slot["centre"]
        half = slot["half"]
        icon = frame[cy - half:cy + half, cx - half:cx + half].copy()
        name, rarity = _inspect(cx, cy)
        s = module_slug(name)
        if s is None:
            logger.event("calibrate_module_unknown", where="header", centre=[cx, cy], ocr=name)
            continue
        cal.cut("modules", f"modules/equipped/{s}.png", icon, frame, name,
                {"slot": slot["kind"], "rarity": rarity, "centre": [cx, cy]})
        equipped.append(s)
    cal.player["modules_equipped"] = equipped
    slugs, copies = _walk_grid(cal)
    cal.player["modules_in_grid"] = sorted(set(slugs))
    cal.player["modules_copies"] = copies
    tourney.return_to_game("modules")
    return {"modules_equipped": equipped, "modules_in_grid": cal.player["modules_in_grid"],
            "copies": len(copies), "preset_tabs": cal.player["category_presets"]["modules"]}


PHASES = {"c": ("cards", phase_cards),
          "g": ("global", phase_global),
          "m": ("modules", phase_modules),
          "u": ("guardians", phase_guardians),
          "b": ("bots", phase_bots),
          "w": ("workshop", phase_workshop)}


def _merge_draft(player: dict) -> str:
    """The names go where the scan's do: into scan_state.json and the draft
    profile, so Promote and the loadout editor see them."""
    from player import scan
    sp = scan._paths()
    st = scan._state_load(sp)
    st.setdefault("player", {}).update(player)
    scan._state_save(sp, st)
    scan._write_draft(sp, st["player"])
    return sp["draft"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--instance", default="main")
    ap.add_argument("--preset", default=None, help="tray/dashboard parity")
    ap.add_argument("--phases", default=PHASE_KEYS,
                    help=f"comma list of {PHASE_KEYS}")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace templates that already exist")
    ap.add_argument("--fresh", action="store_true",
                    help="ignore previous state, redo all phases")
    a = ap.parse_args()
    settings.select_instance(a.instance, "normal_run")
    from runtime import logger
    from player import scan
    p = _paths()
    if os.path.exists(p["stop"]):
        os.remove(p["stop"])
    st = _state_load(p)
    if a.fresh:
        # redo the phases, keep the report: entries are keyed by template,
        # so a re-run refreshes them instead of forgetting the other phases
        st["phases"] = {}
    scan.preflight(False)
    wanted = [s.strip() for s in a.phases.split(",") if s.strip()]
    cal = Calibration(p, a.overwrite)
    cal.entries = list(st.get("entries") or [])
    cal.player = dict(st.get("player") or {})
    logger.event("calibrate", stage="begin", phases=wanted, overwrite=a.overwrite,
                 resume=not a.fresh)
    for key in wanted:
        name, fn = PHASES[key]
        rec = st["phases"].get(name, {})
        if rec.get("status") == "done":
            logger.event("calibrate", stage="skip_done", phase=name)
            continue
        if _stop_requested(p):
            logger.event("calibrate", stage="stopped", before=name)
            break
        st["phases"][name] = {"status": "running",
                              "started": datetime.datetime.now().isoformat()}
        _state_save(p, st)
        stopped = False
        try:
            result = fn(cal)
            st["phases"][name] = {"status": "done", "results": result}
        except SystemExit:
            raise
        except Stopped as e:
            from interactions import tourney
            stopped = True
            st["phases"][name] = {"status": "stopped", "at": str(e)}
            logger.event("calibrate", stage="stopped", phase=name, at=str(e))
            try:
                tourney.ensure_home()
            except Exception:                   # noqa: BLE001
                pass
        except Exception as e:                  # noqa: BLE001 - phase isolation
            from interactions import tourney
            st["phases"][name] = {"status": "error", "error": str(e)[:300]}
            logger.event("calibrate", stage="phase_error", phase=name,
                         error=str(e)[:300])
            try:
                tourney.ensure_home()
            except Exception:                   # noqa: BLE001
                pass
        st["player"] = cal.player
        st["entries"] = cal.entries
        _state_save(p, st)
        cal.save_report()
        try:
            _merge_draft(cal.player)
        except Exception as e:                  # noqa: BLE001 - the cuts are on disk
            logger.event("calibrate_draft_failed", error=str(e)[:200])
        if stopped:
            break
    summary = {k: v.get("status") for k, v in st["phases"].items()}
    logger.event("calibrate", stage="done", phases=summary,
                 written=sum(1 for e in cal.entries if e["status"] == "written"),
                 report=p["report"])
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
