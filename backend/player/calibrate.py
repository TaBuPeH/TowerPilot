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
    from runtime.files import atomic_json
    from runtime.operations import publish_progress
    publish_progress(st)
    atomic_json(p["state"], st)


def _record_activity(p, st, phase, message) -> None:
    """Save the latest basic-scan observation without changing phase outcome."""
    now = time.time()
    st['activity'] = {'kind': 'calibrate_progress', 'phase': phase, 'message': message,
                      't': now, 'operation_id': os.environ.get('TOWER_OPERATION_ID')}
    st['phases'][phase].update(message=message, updated_at=now)
    _state_save(p, st)


def record_final_screen(p, st, name: str, *, verified: bool, error=None) -> dict:
    """Persist and read back the final observation for this exact operation."""
    from runtime import logger
    from runtime.operations import environment_operation
    _, operation_id = environment_operation()
    result = dict(name=name, verified=bool(verified), observed_at=time.time(),
                  operation_id=operation_id)
    if error is not None:
        result['error'] = str(error)
    st['final_screen'] = result
    _state_save(p, st)
    with open(p['state'], encoding='utf-8') as stream:
        if json.load(stream).get('final_screen') != result:
            raise RuntimeError('Final screen observation could not be read back')
    logger.event('setup_final_screen', **result)
    return result


def _stop_requested(p) -> bool:
    from runtime.operations import environment_operation
    ledger, operation_id = environment_operation()
    if ledger:
        if ledger.get(operation_id)['status'] == 'cancelling':
            return True
        try:
            with open(p['stop'], encoding='utf-8') as marker:
                return marker.read().strip() == operation_id
        except FileNotFoundError:
            return False
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


def record_manual_cut(p: dict, rel: str, crop, frame, rect, *, source="dashboard_cropper", screen=None, template=None) -> dict:
    """The dashboard cropper's provenance: WHERE a hand-cut template came
    from (native rect, frame size, self/next-best scores) as a report entry
    and a `calibrate_cut` event, like every cut setup makes - so a later
    look can tell what was cropped and from which screen (the person's own
    four crops of 2026-09-08 left no trace anywhere). Verification uses the
    same bar as setup; the file was already written by the cropper, this
    never writes or removes an image. `template` is the file the caller wrote:
    a long-running dashboard must not look it up through the settings singleton,
    which still names the account it was imported under."""
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
        entry["image_sha256"] = hashlib.sha256((_Path(template) if template else template_path(rel)).read_bytes()).hexdigest()
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


def _below_epic(rarity: str | None) -> bool:
    """Does a rarity read ('rare', 'rare+', 'common') rank below Epic in RARITIES?
    False for None and for Epic and above."""
    return bool(rarity) and rarity.rstrip("+") in RARITIES[RARITIES.index("epic") + 1:]


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
    def __init__(self, p, overwrite: bool, *, bootstrap: bool = False, progress=None):
        self.p, self.overwrite = p, overwrite
        self.bootstrap = bootstrap
        self._progress = progress
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
        if status == 'refused':
            verified = False
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
            learned.record(self.p, rel, extra["rect"], extra["screen"], extra.get("source") or phase,
                           frame_size=(int(frame.shape[1]), int(frame.shape[0])))
        if self.p.get("report"):
            self.save_report()
        return entry

    def progress(self, phase, message) -> None:
        from runtime import logger
        logger.event('calibrate_' + phase + '_progress', message=message)
        if self._progress is not None:
            self._progress(phase, message)

    def save_report(self) -> None:
        from player.calibration_report import describe_report
        from runtime.files import atomic_json
        atomic_json(self.p["report"], describe_report({"entries": self.entries, "player": self.player,
                    "operation_id": os.environ.get('TOWER_OPERATION_ID'),
                    "written_at": datetime.datetime.now().isoformat(timespec="seconds")}))


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
        # Pills in one row are look-alike siblings (default "Preset 1".."Preset 5"
        # differ by one digit): identity is the row position plus the OCR name,
        # so they are cut with the sibling allowance, not global uniqueness.
        entry = cal.cut(phase, f"{prefix}{s}.png", crop, frame, name,
                        {"state": p["state"], "ocr": text, "rect": list(trect)}, unique=False)
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
        from player.module_descriptor import read_descriptor, settled_panel
        try:
            panel = settled_panel(icon, panel, capture.grab, time.sleep)
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


COPY_MATCH = 0.95       # match_module_crop score from which two tile crops are one module (cut() verifies at 0.95 too)
BORDER_HUE_TOL = 10     # circular hue gap (OpenCV 0-179) within which two tile frames are one rarity colour
PLUS_MARKER_SIDE = (8, 14)  # bounding-box side, px, of one pale marker on a '+' frame (measured 8-13)
PLUS_MARKER_MIN_PX = 40     # its area, px (measured 58-75; pale pink art on plain tiles: thin lines, specks, big blobs)
PLUS_MARKERS = 3            # markers from which a tile reads as the '+' variant (measured 3 or 4 on every '+' tile, 0 on plain)


def _ring_hue(frame, cx, cy):
    """Median hue of the lit pixels of the diamond ring around the tile centred
    at (cx, cy) - the ring shatter.tile_is_blue samples, where the frame glow
    carries the rarity colour - or None when the ring leaves the frame or fewer
    than shatter.BLUE_MIN_PX of its pixels are lit (no colour to trust)."""
    import numpy as np
    from interactions.shatter import BLUE_MIN_PX, RING_INNER, RING_OUTER
    if cy - RING_OUTER < 0 or cx - RING_OUTER < 0 or cy + RING_OUTER > frame.shape[0] or cx + RING_OUTER > frame.shape[1]:
        return None
    hsv = cv2.cvtColor(frame[cy - RING_OUTER:cy + RING_OUTER, cx - RING_OUTER:cx + RING_OUTER], cv2.COLOR_BGR2HSV)
    yy, xx = np.mgrid[-RING_OUTER:RING_OUTER, -RING_OUTER:RING_OUTER]
    d = np.abs(yy) + np.abs(xx)
    lit = (d > RING_INNER) & (d < RING_OUTER) & (hsv[..., 1] > 60) & (hsv[..., 2] > 90)
    return float(np.median(hsv[..., 0][lit])) if int(lit.sum()) >= BLUE_MIN_PX else None


def _same_border(a, b) -> bool:
    """Are two ring hues one rarity colour? Hue wraps at 180; an unreadable
    ring (None) is never the same as anything."""
    if a is None or b is None:
        return False
    gap = abs(a - b)
    return min(gap, 180 - gap) <= BORDER_HUE_TOL


def _tile_plus(frame, cx, cy):
    """Does the tile centred at (cx, cy) carry the '+' of its rarity? A '+'
    variant (Epic+, Legendary+, ...) shows 3 or 4 small pale-pink markers on
    its frame, in the same ring colour as the plain one: at the four tips of a
    diamond frame (partly outside the 150 px crop), inside the crop on the
    circular and triangular frames. Counts the compact pale-pink blobs (hue 150-179 or
    0-8, saturation 25-80, value >= 200, PLUS_MARKER_SIDE box, PLUS_MARKER_MIN_PX
    area) in the shatter.RING_OUTER box: True from PLUS_MARKERS of them, False
    for none, None for one or two (neither a plain nor a '+' frame: nothing to
    trust) or when the box leaves the frame."""
    import numpy as np
    from interactions.shatter import RING_OUTER
    if cy - RING_OUTER < 0 or cx - RING_OUTER < 0 or cy + RING_OUTER > frame.shape[0] or cx + RING_OUTER > frame.shape[1]:
        return None
    hsv = cv2.cvtColor(frame[cy - RING_OUTER:cy + RING_OUTER, cx - RING_OUTER:cx + RING_OUTER], cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    pale = (((h >= 150) | (h <= 8)) & (s >= 25) & (s <= 80) & (v >= 200)).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(pale, connectivity=8)
    lo, hi = PLUS_MARKER_SIDE
    markers = sum(1 for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= PLUS_MARKER_MIN_PX
                  and lo <= stats[i, cv2.CC_STAT_WIDTH] <= hi and lo <= stats[i, cv2.CC_STAT_HEIGHT] <= hi)
    return True if markers >= PLUS_MARKERS else False if markers == 0 else None


def _walk_grid(cal: Calibration, progress=lambda message: None):
    """Walk the whole inventory and return (slugs, copies), the modules at
    Epic and above. Cuts missing `modules/<slug>.png` from the first tile
    opened for each slug; an existing image is checked against identified
    copies until its appearance is found. Records a `copies` row for each tile opened and read at Epic
    or above (a name it cannot read raises at the end) or recognised as another
    copy of one.
    inventory.sweep's paging, with the tile rows read off each
    frame (the lattice shifts when "New" badges show).
    A tile framed in rare-blue (shatter.tile_is_blue, read off the grid frame)
    is only counted: it is never tapped, identified, recorded or cut. Any tile
    that is not positively rare-blue is opened, unless it is another copy of a
    module already read: the same plus state as that module's rarity (_tile_plus:
    Epic and Epic+ share the ring colour, so neither it nor, where the markers
    lie outside the crop, the crop score tells them apart) AND either a near-identical crop
    (inventory._same_icon, mean absdiff < 6) or match_module_crop (shift
    tolerant) at COPY_MATCH with the same ring colour as the rarity that module
    was read at. A near miss, an unreadable plus state or a ring with no
    readable colour opens the tile. An opened tile whose panel reads below Epic
    (Rare, Rare+ or Common: a frame tile_is_blue missed) is counted like the blue
    ones: the tap is spent and nothing is recorded or cut. It is not remembered,
    so each tile like it is opened again (a misread Epic tile then costs only
    itself). The count is kept in
    cal.player["modules_below_epic"]. The pages after the first use the settled
    frame next_page() ended on (inventory.take_page_frame); when the glide had
    not ended there is none, and the walk settles that page itself."""
    from device import capture
    from interactions import inventory
    from interactions.shatter import tile_is_blue
    from runtime import logger
    if not inventory._close_panel():
        raise RuntimeError("a modal is open and will not close - not walking the grid")
    inventory.park_top()
    seen: list[tuple] = []                      # (icon, slug or None, rarity); slug None: an unreadable name
    # Resume verified inventory identities from this account's report. Require
    # the exact saved file and its rarity; visually different copies still open.
    # A Rare or Common entry (from a report written before they were skipped) is not resumed.
    import hashlib
    for entry in cal.entries:
        rel = entry.get('rel','')
        if (entry.get('phase') != 'modules' or not entry.get('verified') or
            not rel.startswith('modules/') or '/' in rel[len('modules/'):] or
            not entry.get('image_sha256') or not entry.get('rarity') or _below_epic(entry['rarity'])):
            continue
        path = template_path(rel)
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == entry['image_sha256']:
            image = cv2.imread(str(path))
            if image is not None:
                seen.append((image,rel[len('modules/'):-4],entry['rarity']))
    placed: list[tuple[int, int]] = []          # (absolute y, col) of tiles already counted
    slugs, copies = [], []
    verified_slugs = set()
    offset = 0                                  # measured scroll so far, px
    unreadable = 0
    below_epic = opened = opened_below_epic = 0     # tiles counted below Epic / tiles tapped / of those tapped, read below Epic
    # Ring hue of the first tile opened at each rarity this run (Epic and above).
    # A saved crop (a resumed entry) holds no frame
    # colour, so its colour is that of the rarity it was saved at; until a tile of
    # that rarity with a readable ring has been opened, a shifted copy of it
    # matches nothing and is opened.
    hue_of: dict[str, float] = {}
    held = None                                 # the settled frame next_page() ended on (None: its glide had not ended, so settle)
    for page in range(inventory.MAX_PAGES):
        if _stop_requested(cal.p):
            raise Stopped(f"modules grid page {page}")
        progress(f"Modules: reading inventory page {page+1}")
        grid = held if held is not None else inventory.settle()
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
            if tile_is_blue(grid, cx, cy):
                below_epic += 1                 # rare-blue frame: counted, never tapped or cut
                continue
            hue = _ring_hue(grid, cx, cy)
            plus = _tile_plus(grid, cx, cy)
            known = next(((s, rar) for ic, s, rar in seen
                          if ic.shape == icon.shape and plus == bool(rar and rar.endswith("+"))
                          and (inventory._same_icon(icon, ic)
                               or (_same_border(hue, hue_of.get(rar)) and match_module_crop(icon, ic)[0] >= COPY_MATCH))), None)
            if known is not None:
                # another copy of a module already read, in the same plus state
                # (an unreadable one, None, equals neither state):
                # a near-identical crop (_same_icon tolerates mean absdiff < 6),
                # or the same art, shift tolerant, in the same rarity colour -
                # counted without a tap
                if known[0]:
                    if known[0] not in slugs:
                        slugs.append(known[0])
                    copies.append({"slug": known[0], "rarity": known[1], "page": page,
                                   "row": r, "col": c})
                    if known[0] not in verified_slugs:
                        entry = cal.cut("modules", f"modules/{known[0]}.png", icon, grid, known[0],
                                        {"rarity": known[1], "page": page, "row": r, "col": c})
                        page_cuts.append(entry)
                        if entry.get("verified"):
                            verified_slugs.add(known[0])
                continue
            progress(f"Modules: identifying tile {len(placed)} on page {page+1}")
            name, rarity = _inspect(cx, cy, icon)
            new += 1
            if _below_epic(rarity):
                # a tile_is_blue miss (it fails safe by tapping) whose panel reads below Epic is
                # counted with the blue ones, nothing recorded, nothing cut. It is not put on
                # `seen`: one misread of an Epic tile's rarity must cost that tile only, never
                # its copies, so each copy of it is opened and read for itself
                below_epic += 1
                opened_below_epic += 1
                continue
            s = module_slug(name)
            seen.append((icon, s, rarity))
            if rarity and hue is not None:
                hue_of.setdefault(rarity, hue)  # what this rarity's frame looks like on this screen
            if s is None:
                logger.event("calibrate_module_unknown", where="grid", page=page,
                             row=r, col=c, ocr=name)
                unreadable += 1
                continue
            copies.append({"slug": s, "rarity": rarity, "page": page, "row": r, "col": c})
            # One identity may have several rarity appearances. Keep checking
            # its identified copies until the saved image matches one; a
            # later different-rarity copy cannot invalidate that fresh proof.
            if s not in verified_slugs:
                entry = cal.cut("modules", f"modules/{s}.png", icon, grid, name,
                                {"rarity": rarity, "page": page, "row": r, "col": c})
                page_cuts.append(entry)
                if entry.get("verified"):
                    verified_slugs.add(s)
            if s not in slugs:
                slugs.append(s)
        for entry in page_cuts:
            # copies of the cut module on the same page: its next-best score
            # is legitimately ~1.0 then, and the report shows why
            same = sum(1 for cp in copies
                       if cp["page"] == page and cp["slug"] == entry["rel"][8:-4])
            if same > 1:
                entry["copies_on_page"] = same
        opened += new
        progress(f"Modules: counted {below_epic} below Epic ({below_epic - opened_below_epic} not opened)")
        moved = inventory.next_page()
        held = inventory.take_page_frame()
        logger.event("calibrate_grid_page", page=page, new=new, total=len(copies),
                     tiles=len(placed), moved=moved, offset=offset)
        if moved is None:
            raise RuntimeError("inventory scroll lost its overlap - rows may have been skipped")
        if moved == 0:
            break
        offset += moved
    else:
        raise RuntimeError("Inventory walk reached its page limit before proving the end. Previous inventory is kept.")
    logger.event("calibrate_modules_below_epic", count=below_epic, opened=opened, opened_below_epic=opened_below_epic)
    if unreadable:
        raise RuntimeError(f"{unreadable} module tiles could not be read. Previous inventory is kept; retry Modules.")
    cal.player["modules_below_epic"] = below_epic       # this walk's count (replaced, not added to)
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
    def check_stop():
        if _stop_requested(cal.p):
            raise Stopped()
    inventory = scan(cal, check_stop=check_stop,
                     progress=lambda message: cal.progress('cards', message))
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
    from interactions import tourney
    tourney.open_nav("modules", "modules/buy_module.png", "modules screen")
    result = read_module_contents(cal, progress=lambda message: cal.progress('modules', message))
    tourney.return_to_game("modules")
    return result


def read_module_contents(cal: Calibration, progress=lambda message: None) -> dict:
    """Read equipped identities and the entire inventory without changing slots."""
    from device import capture
    from interactions import tourney
    from runtime import logger
    from player.module_roundtrip import ScreenDriver
    ScreenDriver(cal).inventory_tab()
    time.sleep(0.6)
    frame = capture.grab()
    _evidence(cal.p, frame, "modules")
    rows = harvest_row(cal, frame, TAB_BANDS["modules"], "presets/modules_", "modules")
    cal.player.setdefault("category_presets", {})["modules"] = [n for n, _s, _st in rows]
    equipped = []
    unreadable = 0
    for index, slot in enumerate(pills.header_slots(frame), 1):
        if not slot["occupied"]:
            continue
        progress(f'Modules: identifying equipped slot {index}')
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
    slugs, copies = _walk_grid(cal, progress)
    from interactions import inventory
    inventory.park_top()
    cal.player["module_inventory_complete"] = True
    cal.player["modules_in_grid"] = sorted(set(slugs))
    cal.player["modules_copies"] = copies
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


def _prepare_display():
    from device import layout
    from runtime.operations import environment_operation
    ledger, operation_id = environment_operation()
    if ledger:
        # A tracked operation cannot silently change its rendering identity
        # or rewrite the machine configuration selected after acceptance.
        from dataclasses import asdict
        display = layout.discover()
        expected = ledger.get(operation_id)['context'].get('rendering')
        if expected is None:
            layout.require_native(display.width, display.height, display.dpi)
        elif expected != asdict(display):
            raise ValueError('Rendering changed after acceptance; configure the display in Setup before preparing again')
        layout.activate(display)
    else:
        layout.prepare_scan()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--instance", default="main")
    ap.add_argument("--preset", default=None, help="tray/dashboard parity")
    ap.add_argument("--phases", default="c,g,m,u,b,w",
                    help=f"comma list of {PHASE_KEYS}")
    ap.add_argument("--bootstrap", action="store_true", help="Capture Home and safe menus without existing templates")
    ap.add_argument("--routes", help="With --bootstrap: comma-separated manifest routes forming a Home-to-Home sequence")
    ap.add_argument("--observe", action="store_true", help="No taps: search the screen as it is for every artwork-bound target still missing")
    ap.add_argument("--observe-watch", type=float, default=0.0, metavar="SECONDS",
                    help="with --observe: keep looking for this long (no taps), for controls that are only up for moments")
    ap.add_argument("--flows", action="store_true",
                    help="After the menu walk, run the consented action-capture "
                         "flows (start+cancel a battle, walk event/store/guild). "
                         "Bootstrap only; the dashboard popup is the consent.")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace templates that already exist")
    ap.add_argument("--battle-only", action="store_true", help="Reuse menu discoveries and prepare only battle controls")
    ap.add_argument("--allow-navigation", action="store_true",
                    help="Allow taps for this human-started calibration process only")
    ap.add_argument("--fresh", action="store_true",
                    help="ignore previous state, redo all phases")
    a = ap.parse_args()
    routes = None
    if a.routes is not None:
        if not a.bootstrap or a.flows or a.battle_only or a.observe:
            ap.error('--routes requires --bootstrap and cannot be combined with --flows, --battle-only or --observe')
        from player.bootstrap import validate_route_selection
        routes = [key.strip() for key in a.routes.split(',')]
        try:
            validate_route_selection(routes)
        except ValueError as exc:
            ap.error(str(exc))
    from runtime.operations import restore_worker_context
    restore_worker_context(settings)
    settings.bind_device(a.instance)
    from runtime.device_lease import ensure_current
    ensure_current()
    settings.CONFIG["preset"] = "normal_run"
    if a.allow_navigation:
        settings.instance()["allow_taps"] = True
    from runtime import logger
    from player import scan
    from device import layout
    try:
        _prepare_display()
    except Exception as exc:
        p = _paths()
        st = _state_load(p)
        st.setdefault('phases', {})['bootstrap'] = {
            'status':'error', 'message':f'Display setup failed: {exc}', 'completed':0, 'total':0}
        _state_save(p, st)
        raise
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
        bootstrap.run(p, a.overwrite, flows=a.flows, battle_only=a.battle_only, routes=routes)
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
    st.pop('final_screen', None)
    st.pop('setup_battle', None)
    st.pop('activity', None)
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
    cal = Calibration(p, a.overwrite, progress=lambda phase, message: _record_activity(p, st, phase, message))
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
            record_final_screen(p, st, 'unknown', verified=False, error='Stopped before phase')
            break
        phase_started = time.time()
        st["phases"][name] = {"status": "running", "started_at": phase_started,
                              "started": datetime.datetime.now().isoformat()}
        cal.progress(name, 'Starting ' + name.replace('_', ' '))
        stopped = False
        previous_player = json.loads(json.dumps(cal.player))
        try:
            result = fn(cal)
            st["phases"][name] = {"status": "done", "results": result}
        except SystemExit:
            raise
        except Stopped as e:
            stopped = True
            cal.player = previous_player
            st["phases"][name] = {"status": "stopped", "at": str(e)}
            logger.event("calibrate", stage="stopped", phase=name, at=str(e))
        except module_roundtrip.RestoreRequired as e:
            st["phases"][name] = {"status": "error", "error": str(e)}
            cal.progress(name, str(e))
            cal.save_report()
            raise  # never continue with another phase while a module is removed
        except Exception as e:                  # noqa: BLE001 - phase isolation
            stopped = True
            cal.player = previous_player
            st["phases"][name] = {"status": "error", "error": str(e)[:300]}
            logger.event("calibrate", stage="phase_error", phase=name,
                         error=str(e)[:300])
        st["phases"][name].update(started_at=phase_started, finished_at=time.time())
        st["player"] = cal.player
        st["entries"] = cal.entries
        cal.progress(name, name.replace('_', ' ').capitalize() + ': ' + st['phases'][name]['status']
                     + (': ' + (st['phases'][name].get('error') or st['phases'][name].get('at'))
                        if st['phases'][name].get('error') or st['phases'][name].get('at') else ''))
        cal.save_report()
        try:
            _merge_draft(cal.player)
        except Exception as e:                  # noqa: BLE001 - the cuts are on disk
            logger.event("calibrate_draft_failed", error=str(e)[:200])
        if stopped:
            record_final_screen(p, st, 'unknown', verified=False,
                                error=st['phases'][name].get('error') or st['phases'][name].get('at'))
            break
    if not _stop_requested(p) and all(st['phases'].get(PHASES[key][0], {}).get('status') == 'done' for key in wanted):
        try:
            final = capture.grab()
            home = screen_matches(final, textocr.read_lines(final, 1, raise_on_error=True), 'home')
        except Exception as exc:
            record_final_screen(p, st, 'unknown', verified=False, error=exc)
            raise
        record_final_screen(p, st, 'home' if home else 'unknown', verified=home)
        if not home:
            raise RuntimeError('Calibration did not verify its final Home screen; no further input sent')
    summary = {k: v.get("status") for k, v in st["phases"].items()}
    logger.event("calibrate", stage="done", phases=summary,
                 written=sum(1 for e in cal.entries if e["status"] == "written"),
                 report=p["report"])
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
