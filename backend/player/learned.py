"""The learned manifest: where THIS account's controls were cut, per screen.

The shipped bootstrap manifest names the screens, their anchors and the
targets setup can cut. The person's own crops extend it (user, 2026-09-08:
"we build the MANIFEST ALWAYS and the screen we are on is part of the
manifest - navigate to screen, crop, that is all"). Every verified cut with a
native rectangle - a setup cut, an artwork hit, a dashboard crop - lands here
with the screen it was taken on, and from then on Full setup and the observe
pass cut any target still missing from its learned position whenever they
stand on that screen, at the same size.

Account-local and git-ignored (it lives beside calibrate_state.json). Only
native 1080x2560 frames are recorded: a rect measured on anything else means
nothing here.
"""
import json
import os
import time
from pathlib import Path

NATIVE = (1080, 2560)
FILE = "learned_manifest.json"


def path(p) -> Path:
    return Path(p["state"]).with_name(FILE)


def load(p) -> dict:
    try:
        data = json.loads(path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError):
        data = {}
    if not isinstance(data, dict) or not isinstance(data.get("targets"), dict):
        data = {"version": 1, "targets": {}}
    return data


def _save(p, data) -> None:
    file = path(p)
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, file)


def record(p, rel, rect, screen, source, frame_size=NATIVE, extra=None) -> dict | None:
    """Remember that `rel` was cut at `rect` on `screen`. Returns the row, or
    None when there is nothing to learn (no screen, no rect, not native)."""
    if not p or not p.get("state") or not rel or not screen or not rect:
        return None
    try:
        x, y, w, h = (int(v) for v in rect)
    except (TypeError, ValueError):
        return None
    fw, fh = (int(v) for v in (frame_size or NATIVE))
    if (fw, fh) != NATIVE or w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > fw or y + h > fh:
        return None
    row = {"screen": str(screen), "rect": [x, y, w, h],
           "relative": [round(x / fw, 4), round(y / fh, 4), round(w / fw, 4), round(h / fh, 4)],
           "source": str(source or "cut"), "t": time.time()}
    if extra:
        row.update({k: v for k, v in extra.items() if k not in row})
    data = load(p)
    data["targets"][str(rel).replace("\\", "/")] = row
    _save(p, data)
    return row


def targets_on(p, screen) -> dict:
    """{rel: row} learned on `screen`."""
    return {rel: row for rel, row in load(p)["targets"].items() if row.get("screen") == screen}


def known(p) -> dict:
    return dict(load(p)["targets"])
