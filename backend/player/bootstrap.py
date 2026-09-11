"""Explicitly started, template-free Home/menu acquisition. Never a detector writer.

OCR is confined to this calibration process. Every input has a proved source
and destination. Unsupported/obscured screens stop input; optional absent Home
icons are skipped. No purchases, equipment changes, rewards or battles.
"""
import os
import time
from pathlib import Path
import cv2
import numpy as np
from player.bootstrap_layout import manifest


def region(frame, rect):
    x, y, w, h = rect
    if min(x, y) < 0 or min(w, h) <= 0 or x+w > frame.shape[1] or y+h > frame.shape[0]:
        raise ValueError("Manifest rectangle is outside the native frame")
    return frame[y:y+h, x:x+w]


def normalized(s):
    return " ".join(str(s).upper().split())


def anchor_present(frame, lines, anchor):
    x, y, w, h = anchor["rect"]
    # Dimmed text behind a modal can still OCR correctly. It is not a source
    # screen proof. Require bright text in every independent anchor region.
    crop = region(frame, anchor["rect"])
    bright = np.min(crop, axis=2) > 205
    if np.count_nonzero(bright) < 40:
        return False
    if any(x <= px < x+w and y <= py < y+h and normalized(anchor["text"]) in normalized(text)
           for py, px, text in lines):
        return True
    # Full-frame scale-1 OCR intermittently DROPS a medium anchor word even
    # when it is plainly on screen (seen live: the cards 'INVENTORY' anchor was
    # bright with 6166 px and read cleanly in isolation, yet the whole-frame
    # pass missed it, and the harvest's 4 retries all lost the same coin toss ->
    # 'Cannot verify cards'). When the region is bright but the fast pass missed,
    # confirm deterministically from the region ITSELF at a higher scale. Gated
    # on brightness, so a dimmed/blank anchor never reaches this.
    from vision import textocr
    want = normalized(anchor["text"])
    # Small UI labels can get worse when enlarged (MISSIONS became
    # "Misstorqs" on a live native frame). Try the native crop first.
    return any(want in normalized(t) for scale in (1, 2)
               for _, _, t in textocr.read_lines(crop, scale))


def screen_matches(frame, lines, screen):
    m = manifest()
    if frame.shape[:2] != (m["layout"]["height"], m["layout"]["width"]):
        return False
    spec=m['screens'][screen]
    if not all(anchor_present(frame, lines, a) for a in spec['anchors']):
        return False
    tabs=spec.get('selected_tab')
    if tabs:
        values=[float(np.median(cv2.cvtColor(region(frame,r),cv2.COLOR_BGR2HSV)[:,:,2]))
                for r in tabs['rects']]
        selected=values[tabs['index']]
        if any(selected < value+20 for i,value in enumerate(values) if i!=tabs['index']):
                return False
    pills=spec.get('selected_pill')
    if pills:
        counts=[]
        for rect in pills['rects']:
            hsv=cv2.cvtColor(region(frame,rect),cv2.COLOR_BGR2HSV)
            counts.append(int(np.count_nonzero((hsv[:,:,0]>=40)&(hsv[:,:,0]<=95)&(hsv[:,:,1]>100)&(hsv[:,:,2]>180))))
        selected=counts[pills['index']]
        if selected<80 or any(value>selected*.5 for i,value in enumerate(counts) if i!=pills['index']):
            return False
    return True


def proof_anchors(name):
    spec=manifest()['screens'][name]
    return spec['anchors'] + [{'rect':r} for kind in ('selected_tab','selected_pill') for r in spec.get(kind,{}).get('rects',[])]


def recognize_screen(frame, lines=None, *, read=None):
    """Which manifest screen is this frame - by the same anchors the scan
    proves a destination with - or 'home_menu' (Home with the side menu's X
    lit), or 'battle' (a readable wave counter), or None. Read-only: this is
    how a dashboard crop learns the screen it came from and how the observe
    pass knows which learned positions apply."""
    m = manifest()
    if frame is None or frame.shape[:2] != (m["layout"]["height"], m["layout"]["width"]):
        return None
    if lines is None:
        if read is None:
            from vision import textocr
            read = lambda f: textocr.read_lines(f, 1)
        lines = read(frame)
    for name in m["screens"]:
        if screen_matches(frame, lines, name):
            return name
    try:
        from vision import wave_reader
        if wave_reader.read_wave(frame) is not None:
            sm = m.get("side_menu")
            if sm and icon_present(frame, {"rect": sm["toggle"]["rect"], "hue": sm["toggle"]["open_hue"]}):
                return "battle_menu"             # the in-run side menu is open (the green X)
            return "battle"
    except Exception:                            # noqa: BLE001 - no digit font yet, or no HUD
        pass
    return None


def _three_bars(crop):
    """The hamburger: three white horizontal bars inside a white outline box.
    Rows that are mostly-but-not-entirely bright (the bars, not the box's
    edges) must form exactly three bands of similar height. A chevron, a
    checkmark or the green X never do (a plain 'white' check accepted Home's
    chevron, 2026-09-08)."""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    lit = (hsv[..., 1] < 70) & (hsv[..., 2] > 190)
    frac = lit.mean(axis=1)
    rows = (frac > 0.35) & (frac < 0.9)
    bands, start = [], None
    for i, r in enumerate(list(rows) + [False]):
        if r and start is None:
            start = i
        elif not r and start is not None:
            bands.append((start, i))
            start = None
    # interior bands only: the outline box's own top/bottom edge reads as a
    # mostly-bright band too (0.8 on the live HUD), so anything within 8 rows
    # of the crop's edges is the box, not a bar
    bands = [(a, b) for a, b in bands if b - a >= 5 and a >= 8 and b <= len(rows) - 8]
    if len(bands) != 3:
        return False
    heights = [b - a for a, b in bands]
    return max(heights) <= 2.5 * min(heights)


def icon_present(frame, spec):
    """Conservative native position/colour guard; destination proves identity.

    This supports the measured rail positions only. A shifted or locked tile
    is skipped, never hunted by clicking a sequence of possible coordinates.
    """
    crop = region(frame, spec["rect"])
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    masks = {"red": (h < 12) | (h > 168), "cyan": (h > 80) & (h < 110),
             "purple": (h > 125) & (h < 165), "gold": (h > 8) & (h < 40),
             "green": (h > 40) & (h < 80)}
    if spec["hue"] == "bars":
        return _three_bars(crop)
    if spec['hue'] == 'cross':
        # White close-X with a cyan/green glow; the glow hue varies by theme.
        hh,ww=crop.shape[:2]
        inner=crop[int(hh*.2):int(hh*.8),int(ww*.2):int(ww*.8)]
        white=inner.min(axis=2)>210
        yy,xx=np.indices(white.shape)
        diagonal=(abs(xx/white.shape[1]-yy/white.shape[0])<.18)|(abs(xx/white.shape[1]+yy/white.shape[0]-1)<.18)
        return bool(white[diagonal].mean()>.55 and white[~diagonal].mean()<.35)
    lit = masks[spec["hue"]] & (s > 80) & (v > 170)
    ys, xs = np.nonzero(lit)
    return bool(len(xs) >= 100 and np.ptp(xs) >= crop.shape[1]*.35 and np.ptp(ys) >= crop.shape[0]*.35)


def preflight():
    import psutil
    import settings
    from device import adbclient, capture, layout, overlays
    if not settings.instance().get("allow_taps"):
        raise RuntimeError("Allow menu navigation before starting the starter scan")
    # CLI and dashboard use the same exclusive-device principle. Refuse every
    # other runner, including foreign checkouts (they may share the device).
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        if p.info["pid"] == os.getpid() or "python" not in (p.info["name"] or "").lower():
            continue
        args = p.info["cmdline"] or []
        runners = {"orchestrator.py", "shard.py", "combo.py", "tourney.py", "quest_sm.py",
                   "quest_ilm.py", "scan.py", "calibrate.py", "clicker.py", "boot.py", "harness.py"}
        if any(Path(arg).name in runners for arg in args):
            raise RuntimeError(f"Stop the other runner first (PID {p.pid})")
    inst = settings.instance()
    command = "wm density"
    if inst.get("input_display") is not None:
        command += f" -d {int(inst['input_display'])}"
    dpi = layout.density(adbclient.shell(inst["serial"], command).decode(errors="replace"))
    frame = capture.grab()
    expected = manifest()['layout']
    if (frame.shape[1], frame.shape[0], dpi) != (expected['width'], expected['height'], expected['dpi']):
        raise RuntimeError('Display changed during setup; restart the scan for the new layout')
    wins = overlays.windows(inst["serial"])
    if not any(w.startswith(overlays.GAME_PKG) for w in wins) or any(overlays.offending(wins)):
        raise RuntimeError("The game must be visible with no other app or overlay covering it")
    from player import accounts
    if (accounts.calibration_dir(settings.ROOT, settings.CONFIG) / "module_restore.json").exists():
        raise RuntimeError("Restore the saved module setup before a starter scan")
    from player.geometry import Display
    return Display(frame.shape[1], frame.shape[0], dpi)


class Scanner:
    def __init__(self, cal, state, grab=None, tap=None, read=None, pause=None,
                 flows=False, battle_only=False):
        from device import capture, act
        from vision import textocr
        self.cal, self.state = cal, state
        previous = state.get('phases', {}).get('bootstrap')
        if previous:
            state['scan_history'] = (state.get('scan_history', []) + [previous])[-8:]
        self.grab, self.tap = grab or capture.grab, tap or act.tap
        self.read, self.pause = read or (lambda f: textocr.read_lines(f, 1)), pause or time.sleep
        self.current = "home"
        self.visited = set()
        self.completed = 0
        self.skipped = []
        self.flows = flows
        self.battle_only = battle_only
        from player.bootstrap_layout import scan_steps
        steps = [dict(step, status="pending") for step in scan_steps()]
        if flows:
            # the consented action-capture stage runs after the menu walk and
            # before the final Home verification (flow_capture.run_flows)
            fi = next(i for i, s in enumerate(steps) if s["id"] == "finish")
            steps[fi:fi] = [
                dict(id="flow_menus", label="Capture menu controls", status="pending"),
                dict(id="flow_battle", label="Capture a battle and its results", status="pending")]
        self.steps = steps
        if battle_only:
            self.steps = [s for s in steps if s['id'] in ('preflight', 'extract', 'map', 'home', 'flow_battle', 'finish')]
        self.active_step = 0
        self.started_at = time.time()
        self.steps[0].update(status="running", started_at=self.started_at)
        self.history = []
        self.asset_folder = None
        self.asset_index = {}
        self.asset_mappings = {}
        self.asset_summary = {}
        from player.screen_proof import VisualAnchors
        self.visual_anchors = VisualAnchors()

    def check_stop(self):
        from player.calibrate import _stop_requested, Stopped
        if _stop_requested(self.cal.p):
            raise Stopped("Starter scan stopped; no further game input sent")

    def begin_step(self, index):
        self.active_step = index
        self.steps[index].update(status="running", started_at=time.time())

    def finish_step(self, status="done", message=None):
        step = self.steps[self.active_step]
        step.update(status=status, finished_at=time.time())
        if message:
            step["message"] = message
        self.completed = sum(s["status"] in ("done", "skipped", "needs_attention") for s in self.steps)
        self.progress(step.get("message", step["label"]))

    def progress(self, message, status="running", **extra):
        from player.calibrate import _state_save
        from runtime import logger
        now = time.time()
        step = self.steps[self.active_step]
        if step["status"] == "running":
            if extra.get("units"):
                step["units"] = extra["units"]
            step["message"] = message
            if status in ("error", "stopped"):
                step.update(status=status, finished_at=now)
        event = {"step_id":step["id"], "message":message, "status":status, "at":now}
        if not self.history or any(self.history[-1][key] != event[key] for key in ("step_id", "message", "status")):
            self.history.append(event)
        self.state.setdefault("phases", {})["bootstrap"] = {
            "status":status, "message":message, "completed":self.completed, "kind":"battle" if self.battle_only else "full",
            "total":len(self.steps), "steps":self.steps, "history":self.history,
            "active_step":step["id"], "started_at":self.started_at, "updated_at":now,
            "assets":self.asset_summary,
            "skipped":self.skipped, **extra}
        self.state["entries"] = self.cal.entries
        # A verified UW name label came off the in-run panel, which lists only
        # owned weapons: record the ownership the cut proves, so Apply carries
        # it into player.uws and a Chain Lightning plan binds on this account.
        from player.calibration_report import owned_uws
        proven = owned_uws(self.cal.entries)
        if proven:
            self.cal.player.setdefault("uws", {}).update(proven)
        self.state["player"] = self.cal.player
        _state_save(self.cal.p, self.state)
        logger.event("calibrate_bootstrap", message=message, status=status, step=self.completed)

    def step_index(self, name):
        return next(i for i,s in enumerate(self.steps) if s["id"] == name)

    def prepare_assets(self):
        import settings
        from player import asset_library
        self.begin_step(self.step_index("extract"))
        self.progress("Extracting artwork from the installed game")
        self.asset_folder,self.asset_index = asset_library.acquire(
            Path(self.cal.p["state"]).parent,settings.instance()["serial"],self.progress,self.check_stop)
        index=self.asset_index
        self.asset_summary={"version":index["installation"]["version"],"images":len(index["images"]),
            "unique_images":index["unique_images"],"decode_errors":sum(e.get("kind")!="empty_placeholder" for e in index["errors"]),
            "empty_placeholders":sum(e.get("kind")=="empty_placeholder" for e in index["errors"]),
            "package_hashes":index["package_hashes"],"mapped":0,"verified":0,"targets":[]}
        self.finish_step(message=f"{len(index['images'])} image objects extracted locally")
        self.begin_step(self.step_index("map"))
        self.progress("Mapping extracted sprite names to the shipped manifest")
        self.asset_mappings=asset_library.map_targets(index,manifest().get("asset_bindings",{}))
        self.publish_assets()
        self.finish_step(message=f"{self.asset_summary['mapped']} recognition targets mapped to installed artwork")

    def publish_assets(self):
        from player import asset_library
        self.asset_summary.update(
            mapped=sum(m["status"]=="mapped" for m in self.asset_mappings.values()),
            verified=sum(m.get("verification")=="verified" for m in self.asset_mappings.values()),
            targets=[{"rel":r,"screen":m["screen"],"source":m["status"],"verification":m["verification"],
                      "names":sorted({c["name"] for c in m["candidates"]})} for r,m in self.asset_mappings.items()])
        if self.asset_folder:
            asset_library.atomic_json(self.asset_folder/'mapping.json',{
                'manifest_version':manifest()['version'],'summary':self.asset_summary,'targets':self.asset_mappings})

    def asset_hit(self, rel, frame):
        from player import asset_verify
        definition=manifest().get("asset_bindings",{}).get(rel)
        if not self.asset_folder or not definition:
            return None
        return asset_verify.best_match(self.asset_folder,self.asset_mappings.get(rel,{}),frame,definition)

    def verify_assets(self, name, frame, follow):
        rels=[rel for rel,definition in manifest().get("asset_bindings",{}).items() if definition["screen"]==name]
        self.verify_asset_rels(rels, frame, follow)

    def verify_asset_rels(self, rels, frame, follow, *, unique=True):
        """Find the installed artwork for each target on a live frame ("with
        the artwork you can screenshot and search the screenshot for what you
        need" - user, 2026-09-08), confirm it stayed put on a second frame,
        and cut the on-screen pixels as the runtime template. Menu screens go
        through verify_assets; the battle passes call this directly for the
        HUD ability buttons and the UW panel switches. `unique=False` for
        controls that repeat on screen (every owned weapon has a switch).
        Returns {rel: verification} for the rels that had a binding."""
        out={}
        for rel in rels:
            definition=manifest().get("asset_bindings",{}).get(rel)
            if not definition or not self.asset_folder or rel not in self.asset_mappings:
                continue
            self.check_stop()
            self.progress(f"Verifying extracted artwork: {', '.join(definition['names'])}")
            hit=self.asset_hit(rel,frame)
            confirmed=self.asset_hit(rel,follow) if hit else None
            mapping=self.asset_mappings[rel]
            if not hit or not confirmed:
                mapping['verification']='not_seen'
                out[rel]='not_seen'
                continue
            if hit['asset_sha256']!=confirmed['asset_sha256'] or max(abs(a-b) for a,b in zip(hit['rect'],confirmed['rect']))>3:
                mapping['verification']='unstable'
                out[rel]='unstable'
                continue
            crop=region(frame,hit['rect']).copy()
            patch=region(follow,hit['rect'])
            score=float(cv2.matchTemplate(patch,crop,cv2.TM_CCOEFF_NORMED)[0,0])
            if not np.isfinite(score) or score<.95:
                mapping['verification']='unstable'
                out[rel]='unstable'
                continue
            entry=self.cal.cut('bootstrap',rel,crop,frame,hit['asset_name'],
                {'source_kind':'installed_asset','asset_id':hit['asset_id'],'asset_sha256':hit['asset_sha256'],
                 'game_version':self.asset_summary['version'],'rect':hit['rect'],'confirmation':round(score,3),
                 'source_inliers':hit['inliers'],'screen':definition.get('screen')},
                unique=unique and not definition.get('repeated', False))
            mapping['verification']='verified' if entry['verified'] else 'needs_attention'
            mapping['observed_rect']=hit['rect']
            out[rel]=mapping['verification']
        self.publish_assets()
        return out

    def observed(self, name):
        self.check_stop()
        for _ in range(4):
            frame = self.grab()
            anchors = proof_anchors(name)
            if self.visual_anchors.matches(name, frame, anchors):
                return frame, []
            lines = self.read(frame)
            if screen_matches(frame, lines, name):
                self.visual_anchors.remember(name, frame, anchors)
                return frame, lines
            self.pause(.5)
            self.check_stop()
        raise RuntimeError(f"Cannot verify {name.replace('_',' ')}. No further taps sent. Open Home and retry the starter scan.")

    def cut(self, spec, frame, follow, lines, icon=False, screen=None):
        from vision import pills
        crop = region(frame, spec["rect"]).copy()
        if spec.get("icon"):
            # a fixed-position icon target (the top bar's cart tile and
            # hamburger): its colour at the native position stands in for
            # a text anchor, and the SAME check must hold on the second frame
            icon = True
            if not (icon_present(frame, spec) and icon_present(follow, spec)):
                self.skipped.append({"target":spec["rel"], "reason":"Icon not lit at its native position"})
                return
        if not icon and not anchor_present(frame, lines, spec):
            self.skipped.append({"target":spec["rel"], "reason":"Label not visible at this layout position"})
            return
        # Independently captured frame must match at the SAME position, not
        # another lookalike elsewhere. Self-match alone proves nothing.
        patch = region(follow, spec["rect"])
        score = float(cv2.matchTemplate(patch, crop, cv2.TM_CCOEFF_NORMED)[0,0])
        if crop.std() < 2 or not np.isfinite(score) or score < .95:
            self.skipped.append({"target":spec["rel"], "reason":"Capture changed between frames"})
            return
        self.cal.cut("bootstrap", spec["rel"], crop, frame, spec.get("text", spec["rel"]),
                     {"rect":spec["rect"], "confirmation":round(score,3), "manifest_version":manifest()["version"],
                      "screen": screen or self.current})

    def harvest(self, name, frame, lines):
        if name in self.visited:
            return
        if name == 'home' and not self.visited and not self.battle_only:
            self.state['screen_map'] = {}
            self.state['collections'] = {}
        self.visited.add(name)
        self.progress(f"Capturing {name.replace('_',' ')}")
        self.pause(.3)
        follow, _ = self.observed(name)
        for spec in manifest()["screens"][name]["targets"]:
            self.check_stop()
            self.progress(f"Capturing {spec.get("text", spec["rel"])} on {name.replace("_"," " )}")
            self.cut(spec, frame, follow, lines)
        if self.asset_folder:
            self.verify_assets(name,frame,follow)
        # Some composed controls (e.g. the preset picker) do not render as
        # their original sprite. Their shipped route has its own verifier.
        # Map them BEFORE leaving the parent, not only after navigating away.
        for route in manifest().get('routes', []):
            spec = route.get('icon')
            if route.get('from') == name and spec and icon_present(frame,spec) and icon_present(follow,spec):
                if not any(e.get('rel')==spec['rel'] and e.get('verified') and e.get('t',0)>=self.started_at
                           for e in self.cal.entries):
                    self.cut(spec, frame, follow, lines, icon=True, screen=name)
        self.update_screen_map(name)
        self.learned_cuts(name, frame, follow)
        # Read preset names without selecting them or changing the loadout.
        categories = {"cards":("cards/preset_", "cards"), "modules":("presets/modules_", "modules"),
                      "guardians":("presets/guardians_", "guardians"), "bots":("presets/bots_", "bots"),
                      "picker":("presets/gp_", "global")}
        if name in categories:
            from player.calibrate import harvest_row
            prefix, phase = categories[name]
            self.progress(f"Reading preset names on {name}")
            rows = harvest_row(self.cal, frame, tuple(manifest()["tab_bands"][name]), prefix, phase, all_rows=name=="picker")
            names = [n for n, _, _ in rows]
            if names:
                if phase == "cards": self.cal.player["card_presets"] = [slug for _,slug,_ in rows]
                elif phase == "global": self.cal.player["global_presets"] = [n for n,slug,_ in rows if slug != "none"]
                else: self.cal.player.setdefault("category_presets", {})[phase] = names

    def update_screen_map(self, name):
        expected = {t['rel'] for t in manifest()['screens'][name]['targets']}
        expected.update(rel for rel, definition in manifest().get('asset_bindings', {}).items()
                        if definition['screen'] == name)
        evidence = {e['rel']:e for e in self.cal.entries
                    if e.get('t', 0) >= self.started_at and e.get('rel') in expected}
        optional = {r['icon']['rel'] for r in manifest()['routes']
                    if r.get('optional') and r.get('icon') and r['from']==name}
        blocks = [{"target":rel, "status":"verified" if evidence.get(rel, {}).get('verified') else "unavailable" if rel in optional else "needs_mapping"}
                  for rel in sorted(expected)]
        if not blocks:
            blocks = [{'target': 'screen_identity', 'status': 'verified' if name in self.visited else 'needs_mapping'}]
        verified = sum(b['status']=='verified' for b in blocks)
        available = sum(b['status']!='unavailable' for b in blocks)
        self.state.setdefault('screen_map', {})[name] = {
            "status":"verified" if blocks and verified==available else "needs_mapping",
            "verified":verified, "total":available, "blocks":blocks}
        self.progress(f"{name.replace('_',' ')}: {verified}/{available} available blocks verified")

    def module_detail(self, grid):
        from interactions import inventory
        from vision import pills
        from player import module_descriptor
        from player.calibrate import module_slug
        tiles = [(cx,cy,inventory._tile_icon(grid,cx,cy))
                 for cy in pills.grid_rows(grid) for cx in inventory.COL_X]
        tiles = [(x,y,icon) for x,y,icon in tiles if not inventory._blank_tile(icon)]
        if not tiles:
            self.skipped.append({"screen":"module_detail", "reason":"No inventory tile visible; nothing opened"})
            return
        # The whole visible list has been cropped before opening anything.
        cx,cy,icon = tiles[0]
        self.progress("Modules: matching the first inventory tile to its description")
        current,_ = self.observed("modules")
        if float(cv2.matchTemplate(inventory._tile_icon(current,cx,cy),icon,cv2.TM_CCOEFF_NORMED)[0,0]) < .98:
            raise RuntimeError("Inventory changed before opening its captured tile")
        self.check_stop()
        self.tap(cx,cy,reason="starter scan: inspect captured inventory tile")
        self.pause(.8)
        panel=self.grab()
        name,rarity,box=module_descriptor.read_descriptor(icon,panel)
        close=inventory._find_close(panel)
        if close is None:
            raise RuntimeError("Matched module has no verified close control")
        self.pause(.3)
        follow=self.grab()
        next_name,next_rarity,_=module_descriptor.read_descriptor(icon,follow)
        if (name,rarity)!=(next_name,next_rarity) or inventory._find_close(follow)!=close:
            raise RuntimeError("Module description changed; no further input sent")
        slug=module_slug(name)
        if not slug:
            raise RuntimeError("Module name could not be resolved")
        self.cal.cut("bootstrap",f"modules/{slug}.png",icon,grid,name,
                     {"rarity":rarity,"source":"inventory_before_open", "panel_icon":box})
        x,y=close
        self.cut({"rel":"modules/v29_dialog_close.png","rect":[x-34,y-34,68,68]},panel,follow,[],icon=True,screen="module_detail")
        if self.asset_folder:
            self.verify_assets("module_detail",panel,follow)
        lines=self.read(panel)
        equip=[(y,x,t) for y,x,t in lines if normalized(t)=="EQUIP"]
        if len(equip)==1:
            y,x,_=equip[0]
            self.cut({"rel":"modules/v29_equip_btn.png","text":"Equip","rect":[x-10,y-8,210,65]},panel,follow,lines,screen="module_detail")
        else:
            self.skipped.append({"target":"modules/v29_equip_btn.png","reason":"Equip label unavailable; no action taken"})
        self.check_stop()
        self.progress(f"Verified {name} ({rarity}); closing description")
        # BlueStacks occasionally DROPS a single tap (seen live: this close tap
        # never registered and the description stayed up through observed()'s
        # whole retry window, aborting the entire 20-step scan). One missed tap
        # must not sink setup: re-detect the close control and tap again until
        # the modules grid is back. Re-aiming each time survives a panel shift;
        # a vanished close means the dialog is already gone.
        for _ in range(4):
            self.tap(*close,reason="starter scan: close verified module description")
            self.pause(.6)
            frame = self.grab()
            if screen_matches(frame, self.read(frame), "modules"):
                return
            spot = inventory._find_close(frame)
            if spot is None:
                break
            close = spot
            self.check_stop()
        self.observed("modules")

    def card_inventory(self):
        from player.card_inventory import scan
        from vision import textocr
        scan(self.cal, grab=self.grab, read=self.read, read_label=lambda f:textocr.read_lines(f,2), pause=self.pause,
             check_stop=self.check_stop, progress=self.progress,
             prove=lambda frame: self.prove_screen('cards', frame))

    def prove_screen(self, name, frame):
        anchors = proof_anchors(name)
        if self.visual_anchors.matches(name, frame, anchors):
            return True
        if screen_matches(frame, self.read(frame), name):
            self.visual_anchors.remember(name, frame, anchors)
            return True
        return False

    def run(self):
        self.check_stop()
        from player.manifest_driver import validate
        validate()  # Broken screen references must fail before the first input.
        self.begin_step(self.step_index("home"))
        self.progress("Verifying Home")
        frame, lines = self.observed("home")
        self.harvest("home", frame, lines)
        home = self.state.get('screen_map', {}).get('home', {})
        self.finish_step("done" if home.get('status') == 'verified' else "needs_attention",
                         f"Home: {home.get('verified',0)}/{home.get('total',0)} declared blocks verified")
        if home.get('status') != 'verified':
            missing = [b['target'] for b in home.get('blocks',[]) if b['status']!='verified']
            raise RuntimeError("Home mapping is incomplete; no navigation started. Needs mapping: " + ', '.join(missing))
        from player.manifest_driver import transitions
        from player.mapping_session import Session
        if not self.battle_only:
            Session(self).run([edge for edge in transitions() if 'route' in edge])
        if self.flows:
            self._run_flows()
        self.begin_step(self.step_index("finish"))
        self.progress("Verifying return to Home and saving discoveries")
        self.observed("home")
        self.cal.save_report()
        from player.calibrate import _merge_draft
        _merge_draft(self.cal.player)
        self.finish_step(message="Returned to Home; discoveries saved")
        attention = sum(not e.get("verified") for e in self.cal.entries)
        incomplete = [name for name,row in self.state.get('screen_map',{}).items() if row['status']!='verified']
        tail = ("" if self.flows
                else " Battle and rare-dialog captures remain separate.")
        if incomplete:
            tail += " Incomplete screen maps: " + ', '.join(incomplete) + "."
        self.progress(f"Starter scan finished on Home. {len(self.visited)} screens checked.{tail}",
                      "needs_attention" if attention or self.skipped or incomplete else "done", screens=sorted(self.visited), needs_attention=attention)

    def learned_cuts(self, screen, frame, follow):
        """Cut every target the learned manifest places on `screen` that is
        still missing on disk: the same rect, the same size, from a frame the
        scan has proven to be that screen, stable on a second frame. A
        target the shipped manifest knows a colour or text check for must
        pass it too; otherwise the proven screen and the person's own earlier
        crop at this spot are the evidence ("navigate to screen, crop - that
        is all"). Returns {rel: 'verified'|'needs_attention'|'not_seen'}."""
        import settings
        from player import learned, template_docs
        out = {}
        rows = learned.targets_on(self.cal.p, screen, frame_size=(frame.shape[1], frame.shape[0]))
        if not rows:
            return out
        from player.bootstrap_layout import screen_targets
        icons = {t["rel"]: t for name in list(manifest()["screens"]) + list(manifest().get("hud", {}))
                 for t in screen_targets(name) if t.get("icon")}
        for rel, row in rows.items():
            if settings.template_path(rel).exists():
                continue
            self.check_stop()
            rect = row["rect"]
            try:
                crop = region(frame, rect).copy()
                patch = region(follow, rect)
            except ValueError:
                continue
            score = float(cv2.matchTemplate(patch, crop, cv2.TM_CCOEFF_NORMED)[0, 0])
            if crop.std() < 2 or not np.isfinite(score) or score < .95:
                out[rel] = "not_seen"
                continue
            spec = icons.get(rel)
            text = template_docs.text_for(rel)
            if spec and not (icon_present(frame, dict(spec, rect=rect)) and icon_present(follow, dict(spec, rect=rect))):
                out[rel] = "not_seen"
                continue
            if text and not spec:
                from vision import textocr
                seen = " ".join(t for _, _, t in textocr.read_lines(crop, 2))
                if normalized(text) not in normalized(seen):
                    out[rel] = "not_seen"
                    continue
            self.progress(f"Cutting {rel} where it was last cut on {screen.replace('_', ' ')}")
            entry = self.cal.cut("learned", rel, crop, frame, text or rel.rsplit("/", 1)[-1][:-4],
                                 {"rect": list(rect), "screen": screen, "source": "learned_position",
                                  "learned_from": row.get("source"), "confirmation": round(score, 3)})
            out[rel] = "verified" if entry["verified"] else "needs_attention"
        return out

    def _run_flows(self):
        """The consented action-capture stage: menu-surfaced controls, then a
        started-and-cancelled battle with its dialogs. Each is isolated so one
        failing leaves the other and the rest of the scan intact, and each
        returns to Home. Only reached when the starter scan was launched with
        flows enabled (the dashboard's popup is the consent)."""
        from player import flow_capture
        from runtime import logger
        flow = flow_capture._Flow(self)
        if self.battle_only:
            import settings
            if not settings.template_path('icons/chest_lock.png').exists() or not settings.template_path('buttons/quest_claim.png').exists():
                from player.mapping_session import Session
                from player.manifest_driver import transitions
                session = Session(self)
                for edge in transitions():
                    if 'route' not in edge or 'daily_missions' not in (edge['source'],edge['destination']):
                        continue
                    self.progress('Checking missing mission controls')
                    if not session.driver.step(edge):
                        raise RuntimeError('Could not verify the mission screen route; no further taps')
                    if edge['destination'] == 'daily_missions':
                        flow.capture('buttons/quest_claim.png','CLAIM',flow_capture.R_FULL,(160,50))
        if not self.battle_only:
            self.begin_step(self.step_index("flow_menus"))
            self.progress("Capturing event, store and guild controls")
            try:
                flow_capture.capture_menu_extras(flow)
                self.finish_step(message="Menu controls captured")
            except Exception as e:                   # noqa: BLE001 - isolate
                logger.event("flow_menu_error", error=str(e)[:200])
                flow_capture._safe_home(flow)
                self.finish_step("needs_attention", "Menu capture interrupted; returned Home")
        self.begin_step(self.step_index("flow_battle"))
        self.progress("Starting and cancelling a battle to capture its dialogs")
        try:
            flow_capture.capture_battle_flow(flow)
            self.finish_step(message="Battle and results captured")
        except Exception as e:                   # noqa: BLE001 - isolate
            logger.event("flow_battle_error", error=str(e)[:200])
            try:
                flow_capture._safe_home(flow)
            except Exception as recovery:
                raise RuntimeError(f"Battle capture stopped: {e}. {recovery}") from e
            self.finish_step("needs_attention", "Battle capture interrupted; returned Home")


def run(p, overwrite=False, flows=False, battle_only=False):
    from player.calibrate import Calibration, _state_load, _state_save, Stopped, _merge_draft
    st = _state_load(p)
    cal = Calibration(p, overwrite, bootstrap=True)
    cal.entries = list(st.get("entries") or [])
    cal.player = dict(st.get("player") or {})
    scanner = Scanner(cal, st, flows=flows, battle_only=battle_only)
    scanner.progress("Checking game, emulator and Home")
    try:
        scanner.display = preflight()
        scanner.finish_step(message="Emulator and game checks passed")
        scanner.prepare_assets()
        scanner.run()
    except Exception as e:
        scanner.progress(str(e), "stopped" if isinstance(e, Stopped) else "error")
        cal.save_report()
        raise


def observe(p, watch=0.0):
    """Look at the screen as it is - no taps - and cut every artwork-bound
    target that is still missing from what is on it ("with the artwork you
    can screenshot and search the screenshot for what you need" - user,
    2026-09-08). The person opens the screen (a battle for the HUD ability
    buttons, the UW tab for the switches); this takes two frames, finds each
    target's installed sprite in both, and writes the on-screen pixels
    through the same verified cut as Full setup. Never navigates, never ends
    a run, never touches the setup's step record.

    `watch` > 0 keeps looking for that many seconds (about one pass a second,
    still no taps) and stops early once nothing bound is missing - for the
    controls that are only up for moments, like the Second Wind badge during
    the person's own run."""
    from device import capture
    from player import asset_library, flow_capture
    from player.calibrate import Calibration, _state_load, _state_save, _stop_requested
    from runtime import logger
    st = _state_load(p)
    cal = Calibration(p, False, bootstrap=True)
    cal.entries = list(st.get("entries") or [])
    cal.player = dict(st.get("player") or {})
    scanner = Scanner(cal, st)
    def progress(message, status="running", **extra):
        st["observation"] = {"message": message, "status": status, "t": time.time(), **extra}
        _state_save(p, st)
        logger.event("calibrate_observe", message=message, status=status, **extra)
    scanner.progress = progress
    scanner.progress("Reading the installed artwork")
    import settings
    scanner.asset_folder, scanner.asset_index = asset_library.acquire(
        Path(p["state"]).parent, settings.instance()["serial"], scanner.progress, scanner.check_stop)
    scanner.asset_summary = {"version": scanner.asset_index["installation"]["version"]}
    scanner.asset_mappings = asset_library.map_targets(scanner.asset_index, manifest().get("asset_bindings", {}))
    bound = [rel for rel in manifest().get("asset_bindings", {}) if not settings.template_path(rel).exists()]
    from player import learned
    positioned = [rel for rel in learned.known(p) if not settings.template_path(rel).exists()]
    from player.bootstrap_layout import screen_targets
    positioned += [t["rel"] for name in list(manifest()["screens"]) + list(manifest().get("hud", {}))
                   for t in screen_targets(name) if not settings.template_path(t["rel"]).exists()]
    if not bound and not positioned:
        scanner.progress("Every artwork-bound, manifest and learned target is already captured", "done")
        return {}
    deadline = time.time() + max(0.0, float(watch or 0))
    result = {}
    passes = 0
    while True:
        passes += 1
        if watch:
            scanner.progress(f"Watching the screen for {len(bound)} target(s), {max(0, int(deadline - time.time()))}s left")
        else:
            scanner.progress(f"Searching the screen for {len(bound)} target(s)")
        result.update(_observe_pass(scanner, cal, bound))
        bound = [r for r in bound if result.get(r) != "verified"]
        positioned = [r for r in positioned if result.get(r) != "verified"]
        if not watch or not (bound or positioned) or time.time() >= deadline or _stop_requested(p):
            break
        time.sleep(0.8)
    for rel, state in result.items():
        if state == "verified" and rel in flow_capture.HUD_ABILITY_TARGETS:
            cal.player.setdefault("abilities", {})[rel.split("/")[-1][:-4]] = True
            cal.player["abilities_verified_by"] = "setup"
    st["entries"] = cal.entries
    st["player"] = cal.player
    _state_save(p, st)
    cal.save_report()
    found = sorted(r for r, s in result.items() if s == "verified")
    scanner.progress(f"Captured {len(found)} from the screen: {', '.join(found) or 'nothing on this screen'}"
                     + (f" ({passes} looks)" if passes > 1 else ""), "done", found=found, result=result)
    logger.event("observe_result", found=found, result=result, passes=passes)
    return result


def _observe_pass(scanner, cal, bound):
    """One read-only look: two frames, every bound target still wanted."""
    from device import capture
    from player import flow_capture
    from runtime import logger
    first = capture.grab()
    time.sleep(0.4)
    second = capture.grab()
    repeated = set(flow_capture.UW_SWITCH_TARGETS)
    result = scanner.verify_asset_rels([r for r in bound if r not in repeated], first, second)
    result.update(scanner.verify_asset_rels([r for r in bound if r in repeated], first, second, unique=False))
    # whatever this screen is: cut the shipped manifest's own targets for it
    # that are still missing (icons by colour, labels by their text anchor),
    # then what the learned manifest says was cut here before
    lines = scanner.read(first)
    screen = recognize_screen(first, lines)
    if screen:
        import settings
        from player.bootstrap_layout import screen_targets
        for spec in screen_targets(screen):
            if settings.template_path(spec["rel"]).exists():
                continue
            before = len(scanner.skipped)
            scanner.cut(spec, first, second, lines, screen=screen)
            result[spec["rel"]] = "not_seen" if len(scanner.skipped) > before else "verified"
        result.update(scanner.learned_cuts(screen, first, second))
    logger.event("observe_screen", screen=screen)
    # the UW switches: by position under the owned weapons' verified labels
    result.update(flow_capture.capture_uw_switches(cal, first, second))
    # any Ultimate Weapon name label still missing, off the open UW tab by its
    # text (the sanctioned MISSING-only writer the quest runner also uses)
    try:
        for rel in flow_capture.capture_missing_uw_labels(first):
            result[rel] = "verified"
    except Exception as e:                       # noqa: BLE001 - isolate
        logger.event("observe_uw_label_error", error=str(e)[:200])
    return result
