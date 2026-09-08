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

Rules: it writes account-specific names and, in explicit starter-scan mode,
the text manifest allowlist; no images ship; it never overwrites an existing file unless --overwrite (hard rule 6:
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
PHASE_KEYS = "c,g,m,u,b,w,e"


class Stopped(Exception):
    """The dashboard's Stop flag was seen inside a phase: the phase is left
    'stopped', never 'done' - a half-walked grid must not read as the
    account's inventory."""


# ------------------------------------------------------------- plumbing
def _paths() -> dict:
    from settings import CONFIG
    inst = CONFIG.get("active_instance", "main")
    from player import accounts
    logs = str(accounts.calibration_dir(settings.ROOT, CONFIG))
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
    from player import accounts
    return accounts.template_path(settings.ROOT, settings.CONFIG, rel, write=True)


def is_account_rel(rel: str) -> bool:
    """Only the player's own templates may be written here - never a shipped
    button, screen or glyph."""
    if rel in ("cards/death_ray.png", "cards/extra_orb.png",
               "cards/cash.png", "cards/ultimate_crit.png"):
        # The root card detectors: the account's own card art, cut from its
        # inventory tile at the card's fixed grid position (card_inventory
        # routes these four to the root by is_account_rel). No image ships.
        return True
    if rel.startswith("cards/catalogue/") and rel.endswith(".png"):
        import re
        return bool(re.fullmatch(r"[a-z0-9_]+\.png", rel[len("cards/catalogue/"):]))
    if rel.startswith(ACCOUNT_PREFIXES) and rel.endswith(".png"):
        return "/" not in rel[len("presets/"):] if rel.startswith("presets/") else True
    if rel.startswith("modules/") and rel.endswith(".png") \
            and "/" not in rel[len("modules/"):]:
        from player import catalogue
        return rel[len("modules/"):-4] in catalogue.all_modules()
    return False


def write_template(rel: str, crop, overwrite: bool = False, *, bootstrap: bool = False) -> str:
    """'written' | 'exists' (kept, no overwrite) | 'refused' (not an
    account-specific name)."""
    from player.bootstrap_layout import writable_targets
    allowed = is_account_rel(rel) or (bootstrap and rel in writable_targets())
    if not allowed or crop is None or crop.size == 0:
        return "refused"
    path = template_path(rel)
    if path.exists() and not overwrite:
        return "exists"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".pending.png")
    if not cv2.imwrite(str(tmp), crop):
        raise OSError(f"Could not write template {path}")
    os.replace(tmp, path)
    return "written"


def record_manual_cut(p: dict, rel: str, crop, frame, rect, *, source="dashboard_cropper", screen=None) -> dict:
    """The dashboard cropper's provenance: WHERE a hand-cut template came
    from (native rect, frame size, self/next-best scores) as a report entry
    and a `calibrate_cut` event, like every cut setup makes - so a later
    look can tell what was cropped and from which screen (the person's own
    four crops of 2026-09-08 left no trace anywhere). Verification uses the
    same bar as setup; the file was already written by the cropper, this
    never writes or removes an image."""
    import hashlib
    from runtime import logger
    st = _state_load(p)
    entries = [e for e in (st.get("entries") or []) if e.get("rel") != rel]
    try:
        best, centre, second = pills.match(frame, crop)
    except Exception:                           # noqa: BLE001 - scores are a courtesy
        best, centre, second = 0.0, (0, 0), 0.0
    texture = float(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).std()) if crop is not None and crop.size else 0.0
    verified = texture >= 2 and best >= 0.99 and second < 0.85
    entry = {"phase": "cropper", "rel": rel, "name": rel.rsplit("/", 1)[-1][:-4].replace("_", " "),
             "status": "written", "verified": verified,
             "reason": None if verified else "Another spot on the source frame looks the same; crop tighter or accept the ambiguity",
             "self": best, "next": second, "at": list(centre), "rect": list(rect),
             "frame": [int(frame.shape[1]), int(frame.shape[0])], "source": source, "screen": screen, "t": time.time()}
    if screen:
        from player import learned
        learned.record(p, rel, rect, screen, source, frame_size=(int(frame.shape[1]), int(frame.shape[0])))
    try:
        entry["image_sha256"] = hashlib.sha256(template_path(rel).read_bytes()).hexdigest()
    except OSError:
        pass
    st["entries"] = entries + [entry]
    _state_save(p, st)
    if p.get("report"):
        cal = Calibration(p, False)
        cal.entries = st["entries"]
        cal.player = dict(st.get("player") or {})
        cal.save_report()
    logger.event("calibrate_cut", **{k: v for k, v in entry.items() if k != "t"})
    return entry


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
    # Windows OCR sometimes renders the small rarity '+' as '*'. Only
    # normalize an entire upgradeable rarity label, never ancestral stars.
    import re
    if re.fullmatch(r'(rare|epic|legendary|mythic)\s*\*', t):
        t = t.replace('*', '+')
    for token in t.replace("+", " + ").split():
        hit = difflib.get_close_matches(token, RARITIES, n=1, cutoff=0.7)
        if hit:
            return hit[0] + ("+" if "+" in t else "")
    return None


# ----------------------------------------------------------- the harvest
def match_module_crop(crop, old):
    """Compare the same tile at native scale, allowing small grid offsets.

    Registration uses the interior, then verification uses the entire shared
    area (including the rarity border). Never search other inventory tiles.
    """
    if crop.shape != old.shape:
        return pills.match(crop, old)
    h, w = old.shape[:2]
    margin = min(24, min(h, w) // 6)
    if margin < 1:
        return pills.match(crop, old)
    interior = old[margin:h-margin, margin:w-margin]
    _, _, _, loc = cv2.minMaxLoc(cv2.matchTemplate(crop, interior, cv2.TM_CCOEFF_NORMED))
    dx, dy = loc[0] - margin, loc[1] - margin
    x0, x1 = max(0, -dx), min(w, w-dx)
    y0, y1 = max(0, -dy), min(h, h-dy)
    if (x1-x0)*(y1-y0) < .85*w*h:
        return pills.match(crop, old)
    a = old[y0:y1, x0:x1]
    b = crop[y0+dy:y1+dy, x0+dx:x1+dx]
    score = float(cv2.matchTemplate(b, a, cv2.TM_CCOEFF_NORMED)[0, 0])
    return round(score, 3), (w//2+dx, h//2+dy), -1.0


class Calibration:
    def __init__(self, p, overwrite: bool, *, bootstrap: bool = False):
        self.p, self.overwrite = p, overwrite
        self.bootstrap = bootstrap
        self.entries: list[dict] = []
        self.player: dict = {}

    def cut(self, phase: str, rel: str, crop, frame, name: str, extra=None,
            *, unique: bool = True) -> dict:
        from runtime import logger
        best, centre, second = pills.match(frame, crop)
        # Self-match alone is inevitable for a crop of its source. Require
        # texture and uniqueness too; module duplicates are legitimate and
        # their identity is separately read from the detail panel. `unique=False`
        # is the same allowance for look-alike SIBLING controls captured by
        # position - the Yes/No confirm pair, the MORE STATS/PERKS pair - whose
        # identity is the button's fixed side of the row, not a globally unique
        # face (the runner locates them within the dialog, best-match wins).
        exempt = unique is False or rel.startswith("modules/")
        texture = float(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).std()) if crop is not None and crop.size else 0
        verified = texture >= 2 and best >= 0.99 and (second < 0.85 or exempt)
        status = write_template(rel, crop, self.overwrite, bootstrap=self.bootstrap) if verified else "rejected"
        fresh = None
        if status == "exists":
            # the file that is already there is what the runs will use: score
            # THAT against this machine's frame; the fresh cut's own score
            # rides along so the report can say "Overwrite would fix this"
            old = cv2.imread(str(template_path(rel)))
            if old is not None:
                fresh = best
                if rel.startswith("modules/"):
                    # Validate THIS copy/slot, not a different-rarity copy
                    # elsewhere on the same inventory page.
                    if old.shape[0] <= crop.shape[0] and old.shape[1] <= crop.shape[1]:
                        best, centre, second = match_module_crop(crop, old)
                    else:
                        best, centre, second = 0.0, (0, 0), 0.0
                else:
                    best, centre, second = pills.match(frame, old)
                if best < 0.95 or (second >= 0.85 and not exempt):
                    verified = False
                    status = "stale"
            else:
                verified = False
                status = "stale"
        entry = {"phase": phase, "rel": rel, "name": name, "status": status,
                 "verified": verified, "reason": None if verified else (
                     "The saved image does not match this captured appearance. Existing images were kept because replacement was off."
                     if status == "stale" else "Crop is blank, weak or ambiguous; retry this step"),
                 "self": best, "next": second, "at": list(centre),
                 "t": time.time(), **(extra or {})}
        if verified and status in ("written", "exists"):
            import hashlib
            entry["image_sha256"] = hashlib.sha256(template_path(rel).read_bytes()).hexdigest()
        if fresh is not None:
            entry["fresh"] = fresh
        self.entries = [e for e in self.entries if e["rel"] != rel] + [entry]
        logger.event("calibrate_cut", **{k: v for k, v in entry.items() if k != "t"})
        if verified and status in ("written", "exists") and (extra or {}).get("rect") and (extra or {}).get("screen"):
            # the learned manifest: this control lives HERE on THIS screen
            from player import learned
            learned.record(self.p, rel, extra["rect"], extra["screen"], extra.get("source") or phase)
        if self.p.get("report"):
            self.save_report()
        return entry

    def save_report(self) -> None:
        from player.calibration_report import describe_report
        tmp = self.p["report"] + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(describe_report({"entries": self.entries, "player": self.player,
                       "written_at": datetime.datetime.now().isoformat(timespec="seconds")}),
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
    seen_names = set()
    unreadable = 0
    for i, p in enumerate(chosen):
        crop, trect = pills.text_crop(frame, p["rect"])
        if crop is None:
            logger.event("calibrate_pill_unreadable", phase=phase, rect=list(p["rect"]))
            unreadable += 1
            continue
        text = textocr.read_text(crop)
        if not text or not text.strip():
            logger.event("calibrate_pill_unreadable", phase=phase, rect=list(trect),
                         shot=logger.shot(frame, f"calibrate_{phase}_unreadable"))
            unreadable += 1
            continue
        name = text.strip()
        s = slug(name)
        if s in seen_names:
            raise RuntimeError(f"Preset labels collide after normalization: {name!r}. Give them distinct names in the game and retry.")
        seen_names.add(s)
        entry = cal.cut(phase, f"{prefix}{s}.png", crop, frame, name,
                        {"state": p["state"], "ocr": text, "rect": list(trect)})
        if entry["verified"]:
            out.append((name, s, p["state"]))
        else:
            unreadable += 1
    if unreadable:
        raise RuntimeError(f"{unreadable} preset labels could not be verified. Previous account data is kept; retry this step, using Replace existing calibration for stale images.")
    return out


def _inspect(cx: int, cy: int, icon=None):
    """Tap a module (header slot or grid tile), read the detail panel's
    rarity and name, close it. (None, None) when no panel opened."""
    from device import act, capture
    from interactions import inventory
    from vision import textocr
    if icon is not None:
        current = capture.grab()
        h,w = icon.shape[:2]
        patch = current[cy-h//2:cy-h//2+h,cx-w//2:cx-w//2+w]
        if patch.shape != icon.shape or float(cv2.matchTemplate(patch,icon,cv2.TM_CCOEFF_NORMED)[0,0]) < .98:
            raise RuntimeError("Inventory tile moved after capture; stopped before opening it")
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
    if icon is not None:
        from player.module_descriptor import read_descriptor
        try:
            name,rarity,box = read_descriptor(icon,panel)
            from runtime import logger
            logger.event("calibrate_module_identity", centre=[cx,cy], panel_icon=box, name=name, rarity=rarity)
            return name,rarity
        finally:
            if not inventory._close_panel():
                raise RuntimeError("Module detail panel would not close")
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
    # Resume verified inventory identities from this account's report. Require
    # the exact saved file and its rarity; visually different copies still open.
    import hashlib
    for entry in cal.entries:
        rel = entry.get('rel','')
        if (entry.get('phase') != 'modules' or not entry.get('verified') or
            not rel.startswith('modules/') or '/' in rel[len('modules/'):] or
            not entry.get('image_sha256') or not entry.get('rarity')):
            continue
        path = template_path(rel)
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == entry['image_sha256']:
            image = cv2.imread(str(path))
            if image is not None:
                seen.append((image,rel[len('modules/'):-4],entry['rarity']))
    placed: list[tuple[int, int]] = []          # (absolute y, col) of tiles already counted
    slugs, copies = [], []
    offset = 0                                  # measured scroll so far, px
    unreadable = 0
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
                    if known[0] not in slugs:
                        slugs.append(known[0])
                    copies.append({"slug": known[0], "rarity": known[1], "page": page,
                                   "row": r, "col": c})
                continue
            name, rarity = _inspect(cx, cy, icon)
            s = module_slug(name)
            seen.append((icon, s, rarity))
            new += 1
            if s is None:
                logger.event("calibrate_module_unknown", where="grid", page=page,
                             row=r, col=c, ocr=name)
                unreadable += 1
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
    else:
        raise RuntimeError("Inventory walk reached its page limit before proving the end. Previous inventory is kept.")
    if unreadable:
        raise RuntimeError(f"{unreadable} module tiles could not be read. Previous inventory is kept; retry Modules.")
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
    from player.card_inventory import scan
    from runtime import logger
    def check_stop():
        if _stop_requested(cal.p):
            raise Stopped()
    inventory = scan(cal, check_stop=check_stop,
                     progress=lambda message: logger.event("calibrate_cards_progress", message=message))
    tourney.return_to_game("cards")
    return {"card_presets": cal.player["card_presets"], "names": [n for n, _s, _st in tabs], "inventory":inventory}


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
    if not rows:
        raise RuntimeError("No readable global presets found. Skip this step if Global Presets is locked, or retry with the picker visible.")
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
    if not rows:
        raise RuntimeError(f"No readable {category} presets found. Skip this step if unavailable, or retry after opening it in the game.")
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
    from player.module_roundtrip import ScreenDriver
    ScreenDriver(cal).inventory_tab()
    time.sleep(0.6)
    frame = capture.grab()
    _evidence(cal.p, frame, "modules")
    rows = harvest_row(cal, frame, TAB_BANDS["modules"], "presets/modules_", "modules")
    cal.player.setdefault("category_presets", {})["modules"] = [n for n, _s, _st in rows]
    equipped = []
    unreadable = 0
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
            unreadable += 1
            continue
        cal.cut("modules", f"modules/equipped/{s}.png", icon, frame, name,
                {"slot": slot["kind"], "rarity": rarity, "centre": [cx, cy]})
        equipped.append(s)
    if unreadable:
        raise RuntimeError(f"{unreadable} equipped modules could not be read. Previous inventory is kept; retry Modules.")
    cal.player["modules_equipped"] = equipped
    slugs, copies = _walk_grid(cal)
    cal.player["modules_in_grid"] = sorted(set(slugs))
    cal.player["modules_copies"] = copies
    tourney.return_to_game("modules")
    return {"modules_equipped": equipped, "modules_in_grid": cal.player["modules_in_grid"],
            "copies": len(copies), "preset_tabs": cal.player["category_presets"]["modules"]}


def phase_equipped_modules(cal: Calibration) -> dict:
    from interactions import tourney
    from player import module_roundtrip
    if _state_load(cal.p).get("phases", {}).get("modules", {}).get("status") != "done":
        raise RuntimeError("Finish phase 1 Modules successfully before phase 2")
    tourney.open_nav("modules", "modules/buy_module.png", "modules screen")
    driver = module_roundtrip.ManifestDriver(cal)
    count = module_roundtrip.run(cal.p, driver, lambda: _stop_requested(cal.p))
    tourney.return_to_game("modules")
    if _stop_requested(cal.p):
        raise Stopped("phase 2: original equipment restored")
    return {"modules_scanned_and_restored": count}


PHASES = {"e": ("equipped_modules", phase_equipped_modules), "c": ("cards", phase_cards),
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
    merged = st.setdefault("player", {})
    for key, value in player.items():
        if key == "category_presets":
            merged.setdefault(key, {}).update(value)
        else:
            merged[key] = value
    scan._state_save(sp, st)
    scan._write_draft(sp, st["player"])
    return sp["draft"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--instance", default="main")
    ap.add_argument("--preset", default=None, help="tray/dashboard parity")
    ap.add_argument("--phases", default="c,g,m,u,b,w",
                    help=f"comma list of {PHASE_KEYS}")
    ap.add_argument("--bootstrap", action="store_true", help="Capture Home and safe menus without existing templates")
    ap.add_argument("--observe", action="store_true", help="No taps: search the screen as it is for every artwork-bound target still missing")
    ap.add_argument("--observe-watch", type=float, default=0.0, metavar="SECONDS",
                    help="with --observe: keep looking for this long (no taps), for controls that are only up for moments")
    ap.add_argument("--flows", action="store_true",
                    help="After the menu walk, run the consented action-capture "
                         "flows (start+cancel a battle, walk event/store/guild). "
                         "Bootstrap only; the dashboard popup is the consent.")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace templates that already exist")
    ap.add_argument("--allow-navigation", action="store_true",
                    help="Allow taps for this human-started calibration process only")
    ap.add_argument("--fresh", action="store_true",
                    help="ignore previous state, redo all phases")
    a = ap.parse_args()
    settings.select_instance(a.instance, "normal_run")
    if a.allow_navigation:
        settings.instance()["allow_taps"] = True
    from runtime import logger
    from player import scan
    p = _paths()
    if a.observe:
        if os.path.exists(p["stop"]):
            os.remove(p["stop"])
        from player import bootstrap
        bootstrap.observe(p, watch=a.observe_watch)
        return
    if a.bootstrap:
        if os.path.exists(p["stop"]):
            os.remove(p["stop"])
        from player import bootstrap
        bootstrap.run(p, a.overwrite, flows=a.flows)
        return
    from player.scan_plan import missing_navigation
    missing = missing_navigation(settings.ROOT, settings.CONFIG, a.phases.split(","), include_wave=False)
    if missing:
        raise SystemExit("Capture the navigation images in the dashboard Scan plan first: " + ", ".join(missing))
    if os.path.exists(p["stop"]):
        os.remove(p["stop"])
    st = _state_load(p)
    if a.fresh:
        # redo the phases, keep the report: entries are keyed by template,
        # so a re-run refreshes them instead of forgetting the other phases
        selected_names = {PHASES[key.strip()][0] for key in a.phases.split(",") if key.strip() in PHASES}
        st["phases"] = {k:v for k,v in st.get("phases", {}).items() if k not in selected_names}
    st["preflight"] = {"status": "running"}
    _state_save(p, st)
    try:
        from player.bootstrap import preflight as menu_preflight, screen_matches
        from device import capture
        from vision import textocr
        menu_preflight()
        frame = capture.grab()
        if not screen_matches(frame,textocr.read_lines(frame,1),"home"):
            raise RuntimeError("Open Home without a dialog before calibration")
    except (Exception, SystemExit) as e:
        st["preflight"] = {"status": "error", "error": str(e)}
        _state_save(p, st)
        raise
    st["preflight"] = {"status": "done"}
    wanted = [s.strip() for s in a.phases.split(",") if s.strip()]
    if not wanted or any(key not in PHASES for key in wanted):
        raise SystemExit("Choose one or more calibration steps: " + PHASE_KEYS)
    from player import module_roundtrip
    if "e" in wanted and st.get("phases", {}).get("modules", {}).get("status") != "done" and "m" not in wanted and not module_roundtrip.pending(p):
        raise SystemExit("Complete phase 1 Modules before scanning equipped modules")
    wanted = [key for key in wanted if key != "e"] + (["e"] if "e" in wanted else [])
    cal = Calibration(p, a.overwrite)
    cal.entries = list(st.get("entries") or [])
    cal.player = dict(st.get("player") or {})
    if module_roundtrip.pending(p):
        from interactions import tourney
        tourney.open_nav("modules", "modules/buy_module.png", "modules screen")
        module_roundtrip.recover(p, module_roundtrip.ManifestDriver(cal))
        tourney.return_to_game("modules")
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
        phase_started = time.time()
        st["phases"][name] = {"status": "running", "started_at": phase_started,
                              "started": datetime.datetime.now().isoformat()}
        _state_save(p, st)
        stopped = False
        previous_player = json.loads(json.dumps(cal.player))
        try:
            result = fn(cal)
            st["phases"][name] = {"status": "done", "results": result}
        except SystemExit:
            raise
        except Stopped as e:
            from interactions import tourney
            stopped = True
            cal.player = previous_player
            st["phases"][name] = {"status": "stopped", "at": str(e)}
            logger.event("calibrate", stage="stopped", phase=name, at=str(e))
            try:
                tourney.ensure_home()
            except Exception:                   # noqa: BLE001
                pass
        except module_roundtrip.RestoreRequired as e:
            st["phases"][name] = {"status": "error", "error": str(e)}
            _state_save(p, st)
            cal.save_report()
            raise  # never continue with another phase while a module is removed
        except Exception as e:                  # noqa: BLE001 - phase isolation
            from interactions import tourney
            cal.player = previous_player
            st["phases"][name] = {"status": "error", "error": str(e)[:300]}
            logger.event("calibrate", stage="phase_error", phase=name,
                         error=str(e)[:300])
            try:
                tourney.ensure_home()
            except Exception:                   # noqa: BLE001
                pass
        st["phases"][name].update(started_at=phase_started, finished_at=time.time())
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
