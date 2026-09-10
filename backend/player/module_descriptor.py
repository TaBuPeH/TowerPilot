"""Bind a pre-click inventory crop to its opened description, without resizing it."""
import cv2
import numpy as np
from player.bootstrap_layout import manifest


def settled_panel(icon, first, grab, pause, attempts=8):
    """Wait through opening animation; require the same mapping twice.

    The close control can appear before the enlarged icon finishes moving.
    A transient failure must not abort the inventory or weaken its identity check.
    """
    previous = None
    frame = first
    for _ in range(attempts):
        try:
            box = locate_icon(icon, frame)
        except RuntimeError:
            box = None
        if box is not None and previous is not None and max(abs(a-b) for a,b in zip(box, previous)) <= 3:
            return frame
        previous = box
        pause(.25)
        frame = grab()
    raise RuntimeError('Module artwork did not settle into a consistent description position; retry this scan')


def locate_icon(icon, panel):
    """Feature registration locates enlarged artwork; no pixels are rescaled.

    Only search the detail panel's artwork area, never the dim inventory behind
    it. Require spatially distributed inliers and a plausible, upright mapping.
    """
    spec = manifest()["module_descriptor"]
    x,y,w,h = spec["icon_search"]
    # Short descriptions sit lower than long effect lists. Anchor to the
    # observed close control so the entire icon stays inside the search area.
    from interactions import inventory
    close = inventory._find_close(panel)
    if close is not None:
        relative = spec['icon_search_from_close']
        x,y,w,h = relative['x'],close[1]+relative['dy'],relative['width'],relative['height']
        if y < 0 or y+h > panel.shape[0]:
            raise RuntimeError('Module description is outside the supported native frame')
    from player.asset_verify import locate
    hit = locate(icon, panel, [x,y,w,h])
    if hit is None and min(icon.shape[:2]) > 20:
        # Inventory tile edges include neighbouring glow/background. Match
        # the unchanged inner artwork, then recover the original crop extent
        # using the measured registration (not an assumed display scale).
        inset = 5
        inner = locate(icon[inset:-inset, inset:-inset], panel, [x,y,w,h])
        if inner is not None:
            padding = round(inset * inner['scale'])
            ix,iy,iw,ih = inner['rect']
            rect = [ix-padding, iy-padding, iw+2*padding, ih+2*padding]
            if rect[0] >= x and rect[1] >= y and rect[0]+rect[2] <= x+w and rect[1]+rect[3] <= y+h:
                hit = dict(inner, rect=rect)
    if hit is None or not .8 <= hit['scale'] <= 3:
        raise RuntimeError('Description icon does not match the clicked tile with consistent scale and position')
    return hit['rect']


def read_descriptor(icon,panel):
    from vision import textocr
    from player import calibrate
    box = locate_icon(icon,panel)
    x,y,w,h = box
    spec=manifest()["module_descriptor"]
    left=x+w+spec["text_gap"]
    top=max(0,y-spec["text_above"])
    bottom=min(panel.shape[0],y+spec["text_below"])
    if left >= spec["text_right"]:
        raise RuntimeError("No room for the module descriptor beside the matched icon")
    texts=[t.strip() for _,_,t in textocr.read_lines(panel[top:bottom,left:spec["text_right"]],1)]
    rarity=next((calibrate.parse_rarity(t) for t in texts if calibrate.parse_rarity(t)),None)
    names=[t for t in texts if calibrate.resolve_module(t)]
    if not names:
        # An unknown name must be the first readable line following rarity,
        # never an arbitrary effect or action label further down the panel.
        for i,t in enumerate(texts[:-1]):
            if calibrate.parse_rarity(t) and calibrate.looks_like_module_name(texts[i+1]):
                names=[texts[i+1]]
                break
    if len(names)!=1 or rarity is None:
        raise RuntimeError("Cannot uniquely read name and rarity beside the matched module icon")
    return names[0],rarity,box
