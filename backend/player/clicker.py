"""One client-requested, manifest-positioned action. No autonomous background loop.

Pixels stay account-local. Each invocation binds one instance and exits; the
dashboard never imports the device settings singleton. Runtime uses no OCR.
"""
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from player.bootstrap_layout import manifest

# Action semantics only; all geometry comes from the existing manifest.
ACTIONS = {
    "guild": {"label": "Open Guild", "screen": "home", "target": "home/tile_guild.png", "destination": "guild"},
    "events": {"label": "Open Events", "screen": "home", "target": "home/tile_event.png", "destination": "events"},
    "presets": {"label": "Open preset picker", "screen": "home", "target": "presets/picker_icon.png", "destination": "picker"},
    "close_presets": {"label": "Close preset picker", "screen": "picker", "target": "presets/close_x.png", "destination": "home"},
    "cards_home": {"label": "Return from Cards to Home", "screen": "cards", "target": "navigation/cards_home.png", "destination": "home"},
    "guild_home": {"label": "Return from Guild to Home", "screen": "guild", "target": "buttons/return_to_game.png", "destination": "home"},
}


def locations(screen, learned):
    """Resolve screen-specific rectangles without a second coordinate table."""
    m = manifest()
    rows = {t["rel"]: t for t in m.get("screens", {}).get(screen, {}).get("targets", [])}
    for route in m.get("routes", []):
        icon = route.get("icon", {})
        if route.get("from") == screen and icon.get("rel"):
            rows[icon["rel"]] = icon
    for rel, row in learned.items():
        if row.get("screen") == screen:
            rows[rel] = row
    return rows


def locate(frame, template, rect, margin=24, threshold=.92, *, search=None):
    """Native-size match near a manifest position; reject weak/duplicate hits."""
    if template is None or frame is None or template.size == 0:
        return {"ok": False, "reason": "Capture this recognition image first"}
    if frame.shape[:2] != (2560, 1080):
        return {"ok": False, "reason": "Use Setup to configure the native display resolution"}
    try:
        if search is not None:
            rect, margin = search, 0
        x, y, w, h = map(int, rect)
        if min(w, h) <= 0 or min(x, y) < 0 or x+w > 1080 or y+h > 2560:
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        return {"ok": False, "reason": "Invalid manifest rectangle; scan this control again"}
    x0, y0 = max(0, x-margin), max(0, y-margin)
    roi = frame[y0:min(2560, y+h+margin), x0:min(1080, x+w+margin)]
    th, tw = template.shape[:2]
    if roi.shape[0] < th or roi.shape[1] < tw or float(template.std()) < 4:
        return {"ok": False, "reason": "Recognition image does not fit the manifest position"}
    scores = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
    scores = np.nan_to_num(scores, nan=-1, posinf=-1, neginf=-1)
    _, score, _, point = cv2.minMaxLoc(scores)
    px, py = point
    # Correlation alone also matches a dimmed parent behind a modal.
    patch = roi[py:py+th, px:px+tw]
    brightness = float(patch.mean()) / max(1., float(template.mean()))
    other = scores.copy()
    other[max(0, py-th//2):py+th//2+1, max(0, px-tw//2):px+tw//2+1] = -1
    second = float(other.max())
    ok = score >= threshold and score-second >= .06 and .75 <= brightness <= 1.3
    return {"ok": ok, "score": round(score, 3), "runner_up": round(second, 3),
            "rect": [x0+px, y0+py, tw, th], "search": [x0, y0, roi.shape[1], roi.shape[0]],
            "reason": "Target verified near its manifest position" if ok else "Target is missing, changed or ambiguous"}


def inspect(frame, action, learned, read_template, identify, asset_hit=None):
    name = identify(frame)
    if name != action["screen"]:
        return {"ok": False, "screen": name, "reason": f'Open {action["screen"]}; current screen is {name}'}
    if asset_hit:
        hit = asset_hit(action['target'], frame)
        if hit:
            return {"ok":True, "rect":hit['rect'], "screen":name, "target":action['target'],
                    "asset_sha256":hit['asset_sha256'], "position_source":"installed_artwork",
                    "reason":"Installed artwork verified at the manifest position"}
    row = locations(name, learned).get(action["target"])
    if not row:
        return {"ok": False, "screen": name, "reason": "Scan this control to learn its position first"}
    result = locate(frame, read_template(action["target"]), row.get("rect"))
    return {**result, "screen": name, "target": action["target"],
            "position_source": "learned" if learned.get(action["target"], {}).get("screen") == name else "shipped"}


def perform(action, learned, grab, read_template, identify, tap, execute=False, pause=time.sleep, asset_hit=None):
    first = inspect(grab(), action, learned, read_template, identify, asset_hit)
    if not first["ok"] or not execute:
        return first
    pause(.25)
    second = inspect(grab(), action, learned, read_template, identify, asset_hit)
    if not second["ok"] or first.get('asset_sha256') != second.get('asset_sha256') or max(abs(a-b) for a,b in zip(first["rect"], second.get("rect", first["rect"]))) > 3:
        return {**second, "ok": False, "reason": "Screen changed before the click; nothing clicked"}
    x, y, w, h = second["rect"]
    event = tap(x+w//2, y+h//2, reason="client_clicker:"+action["target"], instant=True)
    if event.get("dry_run"):
        return {**second, "clicked": False, "reason": "Dry run: target verified, no click sent"}
    pause(.6)
    destination = identify(grab())
    confirmed = destination == action["destination"]
    return {**second, "ok": confirmed, "clicked": True, "destination": destination,
            "reason": "Click confirmed: "+destination if confirmed else "Click sent; destination not confirmed. Stopped for review."}


def main():
    import argparse
    import settings
    from device import capture, act
    from player import learned, calibrate
    from vision import screen
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", required=True)
    ap.add_argument("--action", choices=ACTIONS, required=True)
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()
    settings.bind_device(args.instance)
    if args.execute:
        from player.bootstrap import preflight
        preflight()
    p = calibrate._paths()
    if Path(p["state"]).with_name("module_restore.json").exists():
        raise RuntimeError("Restore the saved module setup before using the clicker")
    def identify(frame):
        # Overlay-first existing detector is deliberately stricter than OCR
        # anchors on the dimmed parent behind a dialog.
        sc = screen.identify(frame).name
        if sc in ("unknown", "home"):
            for name, rels in (("picker", ["presets/select_header.png"]),
                               ("events", ["icons/event_missions_tab.png", "buttons/event_bots_tab.png"])):
                rows = locations(name, learned.known(p))
                if all(rel in rows and locate(frame, read_template(rel), rows[rel]["rect"])["ok"] for rel in rels):
                    return name
        if sc == "home":
            rows = locations("home", learned.known(p))
            if not locate(frame, read_template("home/battle_btn.png"), rows["home/battle_btn.png"]["rect"])["ok"]:
                return "unknown"
        return sc
    def read_template(rel):
        path = settings.template_path(rel)
        return cv2.imread(str(path)) if path.exists() else None
    from player import asset_library, asset_verify
    libraries = list(Path(p['state']).parent.glob('asset_library/*/index.json'))
    def asset_hit(rel, frame):
        definition = manifest().get('asset_bindings',{}).get(rel)
        if not definition or not libraries:
            return None
        index_path = max(libraries,key=lambda path:path.stat().st_mtime)
        index = json.loads(index_path.read_text(encoding='utf-8'))
        mapping = asset_library.map_targets(index,{rel:definition})[rel]
        return asset_verify.best_match(index_path.parent,mapping,frame,definition)
    frames = []
    def grab():
        frame = capture.grab()
        frames[:] = [frame]
        return frame
    result = perform(ACTIONS[args.action], learned.known(p), grab,
                     read_template, identify, act.tap, args.execute, asset_hit=asset_hit)
    if frames:
        preview = frames[0].copy()
        if result.get("rect") and not result.get("clicked"):
            x, y, w, h = result["rect"]
            cv2.rectangle(preview, (x,y), (x+w,y+h), (0,255,0), 4)
        cv2.imwrite(str(Path(p["state"]).with_name("clicker_preview.png")), preview)
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}))
