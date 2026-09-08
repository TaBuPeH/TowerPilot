"""Snapshot every slot, unequip, index inventory once, restore the manifest.

Only the human-started calibrator uses this; normal runners never learn images.
"""
import json
import os
from pathlib import Path


class RestoreRequired(RuntimeError):
    pass


def journal_path(paths):
    return Path(paths['state']).with_name('module_restore.json')


def pending(paths):
    path = journal_path(paths)
    if not path.exists():
        return None
    # A damaged journal must block further equipment changes, not look empty.
    return json.loads(path.read_text(encoding='utf-8'))


def _save(paths, record):
    path = journal_path(paths)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(record, indent=2), encoding='utf-8')
    os.replace(tmp, path)
    if record.get('version') == 2:
        manifest = path.with_name('module_manifest.json')
        temp = manifest.with_suffix('.tmp')
        temp.write_text(json.dumps(record, indent=2), encoding='utf-8')
        os.replace(temp, manifest)


def recover(paths, driver):
    record = pending(paths)
    if record is None:
        return
    driver.progress('Restoring the original module and slot')
    try:
        if record.get('version') == 2:
            driver.restore_manifest(record)
        else:
            driver.restore(record)
        if not driver.restored(record):
            raise RuntimeError('The original equipped slots did not match after restoration')
    except Exception as exc:
        raise RestoreRequired('Equipment restoration needs attention. Keep this emulator on Home, then resume calibration. ' + str(exc)) from exc
    if record.get('version') == 2:
        if hasattr(driver, 'finalize_manifest'):
            driver.finalize_manifest(record)
        record['stage'] = 'restored'
        _save(paths, record)
    journal_path(paths).unlink()


def run(paths, driver, stopped):
    recover(paths, driver)
    manifest = driver.snapshot_all(stopped)
    if stopped():
        return 0
    if len(manifest['slots']) != 8 or any(r['state'] not in ('equipped','empty','locked') for r in manifest['slots']):
        raise RuntimeError('Not every slot could be identified. No modules were unequipped; review the manifest.')
    _save(paths, manifest)  # all eight slots are durable BEFORE ANY Unequip
    targets = [r for r in manifest['slots'] if r['state'] == 'equipped']
    try:
        manifest['stage'] = 'unequipping'
        for record in targets:
            if stopped():
                return 0  # finally restores every slot already changed
            manifest['current_slot'] = record['slot']['id']
            _save(paths, manifest)
            driver.unequip_slot(record)
        manifest['all_unequipped'] = True
        manifest['stage'] = 'scanning'
        _save(paths, manifest)
        driver.scan_inventory(manifest, stopped)
    finally:
        recover(paths, driver)
    return len(targets)


class ScreenDriver:
    """Calibration-only OCR for identity; native screenshots verify restoration."""
    def __init__(self, calibration):
        self.cal = calibration
        self.paths = calibration.p

    def progress(self, message):
        from runtime import logger
        logger.event('calibrate_roundtrip', message=message)

    def inventory_tab(self):
        from device import capture, act
        from vision import textocr
        from interactions import inventory
        frame = capture.grab()
        choices = [(x+20,y+1010) for y,x,t in textocr.read_lines(frame[1000:1100],1.0)
                   if t.strip().casefold() == 'inventory']
        if len(choices) != 1:
            raise RuntimeError('Inventory tab is not recognizable')
        act.tap(*choices[0], 'calibration: inventory tab')
        inventory.settle()

    def slots(self):
        self.inventory_tab()
        from interactions import inventory
        from vision import pills
        return [s for s in pills.header_slots(inventory.settle()) if s['occupied']]

    def _panel(self, point):
        import time
        from device import act, capture
        from interactions import inventory
        act.tap(*point, 'calibration: inspect exact module copy')
        for _ in range(10):
            time.sleep(.25)
            frame = capture.grab()
            if inventory._panel_open(frame):
                time.sleep(.7)
                return capture.grab()
        raise RuntimeError('Module detail panel did not open')

    def _identity(self, frame):
        from interactions import inventory
        from vision import textocr
        from player import calibrate
        import re
        close = inventory._find_close(frame)
        if close is None:
            raise RuntimeError('Module panel close button is not recognizable')
        top = max(0, close[1]-50)
        lines = textocr.read_lines(frame[top:2300, 100:980], 1.0)
        texts = [' '.join(t.lower().split()) for _,_,t in lines]
        slug = next((calibrate.resolve_module(t) for t in texts if calibrate.resolve_module(t)), None)
        rarity = next((calibrate.parse_rarity(t) for t in texts if calibrate.parse_rarity(t)), None)
        level = next((re.search(r'^lv[. ]+([0-9]+)', t).group(1) for t in texts if re.search(r'^lv[. ]+([0-9]+)', t)), None)
        if not slug or not rarity:
            raise RuntimeError('Could not record the module name, rarity and level')
        starts = [y for y,x,t in lines if t.strip().casefold() == 'effects']
        if len(starts) != 1:
            raise RuntimeError('Could not identify the module effect list')
        effects = []
        for y,x,t in lines:
            match = re.match(r'^\+[0-9.]+(?:%|x|s)?\s+(?:[iIlLsS]{1,2}\s+)?(.+)', t.strip(), re.I)
            if y <= starts[0] or x < 220 or not match:
                continue
            badges = [calibrate.parse_rarity(label) for yy,xx,label in lines if xx < 220 and abs(yy-y) < 15]
            badges = [badge for badge in badges if badge]
            if len(badges) != 1:
                raise RuntimeError('Could not identify a module effect rarity')
            effects.append([badges[0], ' '.join(match.group(1).casefold().split())])
        if not effects:
            raise RuntimeError('Could not identify the module effects')
        return {'slug':slug, 'rarity':rarity, 'level':level, 'effects':effects}

    def _fingerprints(self, frame, folder):
        import cv2
        from interactions import inventory
        from vision import textocr
        close = inventory._find_close(frame)
        lines = textocr.read_lines(frame, 1.0)
        starts = [y for y,x,t in lines if t.strip().casefold() == 'effects']
        ends = [y for y,x,t in lines if t.strip() == '+1']
        if len(starts) != 1 or not ends or min(ends)-starts[0] < 300:
            raise RuntimeError('Could not record all module effects before Unequip')
        header = frame[close[1]-30:close[1]+110, 410:905]
        effects = frame[starts[0]:min(ends)-25, 150:925]
        paths = []
        for name, crop in [('identity',header),('effects',effects)]:
            path = folder / (name + '.png')
            if not cv2.imwrite(str(path),crop):
                raise RuntimeError('Could not save restoration evidence')
            paths.append(str(path))
        return paths

    def _same_copy(self, frame, record):
        import cv2
        for path in record['fingerprints']:
            if record['slot']['kind'] == 'small' and Path(path).stem == 'effects':
                continue  # assist efficiency changes displayed numbers, not effect types/rarities
            patch = cv2.imread(path)
            if patch is None:
                raise RuntimeError('Restoration identity evidence is missing')
            if cv2.minMaxLoc(cv2.matchTemplate(frame, patch, cv2.TM_CCOEFF_NORMED))[1] < .99:
                return False
        current = self._identity(frame)
        return all(current[k] == record['identity'][k] for k in ('slug','rarity','effects'))

    def _close(self):
        from interactions import inventory
        if not inventory._close_panel():
            raise RuntimeError('Module panel will not close')

    def snapshot(self, slot):
        import cv2
        from interactions import inventory
        frame = inventory.settle()
        panel = self._panel(slot['centre'])
        identity = self._identity(panel)
        self._close()
        folder = Path(self.paths['evidence']) / 'module_restore'
        folder.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(folder / 'before.png'), frame)
        cv2.imwrite(str(folder / 'panel.png'), panel)
        fingerprints = self._fingerprints(panel, folder)
        return {'slot': slot, 'identity': identity, 'fingerprints': fingerprints, 'before': str(folder / 'before.png')}

    def _action(self, word):
        from device import capture, act
        from interactions import inventory
        from vision import textocr
        frame = capture.grab()
        if not inventory._panel_open(frame):
            raise RuntimeError('Expected a module detail panel')
        # The left action column contains Equip/Unequip, never the level or
        # currency-spending controls. Refuse ambiguous OCR results.
        choices = [(x+15, y+1010) for y, x, text in textocr.read_lines(frame[1000:2300, :450], 1.0)
                   if text.strip().casefold().replace('ljnequip', 'unequip') == word.casefold()]
        if len(choices) != 1:
            raise RuntimeError(f'Could not uniquely identify {word}; no action taken')
        act.tap(*choices[0], f'calibration: {word}')

    def _module_frame(self):
        from interactions import inventory, tourney
        frame = inventory.settle()
        if not tourney.find(frame, 'modules/buy_module.png', .95):
            raise RuntimeError('The Modules screen is no longer visible; restoration remains pending')
        return frame

    def restored(self, record):
        import cv2
        import numpy as np
        from interactions import inventory
        from vision import pills
        self._close()
        before = cv2.imread(record['before'])
        if before is None:
            raise RuntimeError('Restoration screenshot is missing')
        current = self._module_frame()
        # Verify all eight slots, including originally empty ones, and the
        # selected preset row. Matching one name is not proof of restoration.
        patches = [(x-90,y-90,x+90,y+125) for x,y in (*pills.HEADER_LARGE,*pills.HEADER_SMALL)]
        patches.append((60,185,1020,275))
        for x0,y0,x1,y1 in patches:
            old, new = before[y0:y1,x0:x1], current[y0:y1,x0:x1]
            if np.abs(old.astype(float)-new.astype(float)).mean() <= 3:
                continue
            if float(cv2.matchTemplate(new, old, cv2.TM_CCOEFF_NORMED)[0,0]) < .98:
                return False
        return True

    def unequip(self, record):
        panel = self._panel(record['slot']['centre'])
        if not self._same_copy(panel, record):
            raise RuntimeError('The selected module changed before Unequip')
        self._action('Unequip')
        self._close()
        if self.restored(record):
            raise RuntimeError('Unequip did not change the slot')

    def _find_copy(self, record):
        from interactions import inventory
        from vision import pills
        self._close()
        self._module_frame()
        inventory.park_top()
        for page in range(inventory.MAX_PAGES):
            self.progress(f"Locating {record['identity']['slug'].replace('_',' ')}: inventory page {page+1}")
            frame = inventory.settle()
            for cy in pills.grid_rows(frame):
                for cx in inventory.COL_X:
                    icon = inventory._tile_icon(frame,cx,cy)
                    if inventory._blank_tile(icon):
                        continue
                    panel = self._panel((cx,cy))
                    same = self._same_copy(panel, record)
                    if same:
                        return frame, icon, panel
                    self._close()
            moved = inventory.next_page()
            if moved == 0:
                break
            if moved is None:
                raise RuntimeError('Inventory scroll could not be measured')
        raise RuntimeError('The exact original module copy was not found; restoration remains pending')

    def scan(self, record):
        from player import calibrate
        frame, icon, panel = self._find_copy(record)
        slug = record['identity']['slug']
        name = slug.replace('_',' ').title()
        rarity = record['identity']['rarity']
        self.cal.cut('modules', f'modules/{slug}.png', icon, frame, name,
                     {'rarity': rarity, 'source': 'temporarily_unequipped'})
        self._close()

    def restore(self, record):
        import time
        from interactions import tourney
        if self.restored(record):
            return
        self._find_copy(record)
        self._action('Equip')
        slot = 'primary' if record['slot']['kind'] == 'large' else 'assist'
        # Some versions auto-equip; others offer Primary/Assist explicitly.
        _, button = tourney.wait_for(f'modules/{slot}_btn.png', 3.0)
        if button:
            tourney.tap_at(button, f'calibration: restore {slot} slot')
        time.sleep(.6)
        self._close()
        if not self.restored(record):
            raise RuntimeError('Equip did not restore the original configuration')

class ManifestDriver(ScreenDriver):
    """Learn all slots first; index native inventory icons once for restoration."""
    def _choose_role(self, role):
        """Human-started restoration reads the two actual slot choice labels."""
        import time
        from device import capture, act
        from vision import textocr
        from interactions import inventory
        time.sleep(.6)
        def choices(frame):
            labels = {}
            lines = textocr.read_lines(frame,1)
            for y,x,text in lines:
                word=text.strip().casefold()
                if word not in ('primary','assist') or not 500 < y < 2300:
                    continue
                patch=frame[y:y+35,x:x+100]
                import numpy as np
                if np.count_nonzero(np.min(patch,axis=2)>205) < 40:
                    continue
                if word in labels:
                    raise RuntimeError('Slot choice labels are ambiguous')
                labels[word]=(x+15,y+15)
            # Whole-screen OCR can omit text inside glowing buttons. Read
            # their structurally detected interiors, after proving the prompt.
            prompt = ' '.join(t.casefold() for _,_,t in lines)
            if set(labels) != {'primary','assist'} and 'primary slot' in prompt and 'assist slot' in prompt:
                from vision import pills
                for pill in pills.pills(frame,1000,1800):
                    x,y,w,h=pill['rect']
                    word=' '.join(t.strip().casefold() for _,_,t in textocr.read_lines(frame[y:y+h,x:x+w],2))
                    if word in ('primary','assist'):
                        labels[word]=(x+w//2,y+h//2)
            return labels
        frame=capture.grab()
        labels=choices(frame)
        if set(labels) != {'primary','assist'}:
            # Accounts without an assist slot may equip directly. The caller
            # still must verify the original slot after closing the detail.
            if inventory._panel_open(frame) and inventory._find_close(frame) is None:
                raise RuntimeError('Slot choice dialog is not readable; restoration remains pending')
            return
        if abs(labels['primary'][1]-labels['assist'][1]) > 100:
            raise RuntimeError('Slot choices are not on the same dialog row')
        time.sleep(.3)
        follow = capture.grab()
        if choices(follow) != labels:
            raise RuntimeError('Slot choices changed before restoration')
        self._cut_slot_buttons(frame, follow, labels)
        act.tap(*labels[role], 'calibration: restore '+role+' slot')

    def _cut_slot_buttons(self, frame, follow, labels):
        """The prompt is up and proven on two frames: cut PRIMARY and ASSIST
        as `modules/<slot>_btn.png` from the pill each label sits in. This is
        the only place those buttons ever render (v29 equips by category
        otherwise), so the calibrator cuts them while it is here - through the
        sanctioned writer, MISSING targets only unless overwrite was asked."""
        from vision import pills
        cut = getattr(self.cal, 'cut', None)
        if cut is None:                          # a driver without a calibration record
            return
        try:
            found = pills.pills(frame, 1000, 1800)
        except Exception:                        # noqa: BLE001 - a cut is a courtesy here
            return
        for word, (px, py) in labels.items():
            for pill in found:
                x, y, w, h = pill['rect']
                if x <= px < x + w and y <= py < y + h:
                    cut('modules', f'modules/{word}_btn.png', frame[y:y + h, x:x + w].copy(),
                        follow, word.upper(), {'rect': [x, y, w, h], 'source': 'slot_prompt'})
                    break

    def _close(self):
        from device import capture, act
        from interactions import inventory
        from vision import textocr
        frame = capture.grab()
        if not inventory._panel_open(frame):
            return
        if inventory._find_close(frame) is None:
            lines = textocr.read_lines(frame,1.0)
            if any(t.strip().casefold() == 'slot locked' for y,x,t in lines):
                buttons = [(x+15,y+15) for y,x,t in lines if t.strip().casefold() in ('ok','0k')]
                if len(buttons) != 1:
                    raise RuntimeError('Locked-slot notice has no readable OK button')
                act.tap(*buttons[0], 'calibration: dismiss locked-slot notice')
                import time
                time.sleep(.5)
                if inventory._panel_open(capture.grab()):
                    raise RuntimeError('Locked-slot notice did not close')
                return
        super()._close()

    def snapshot_all(self, stopped):
        import cv2
        import time
        from vision import pills, textocr
        self.inventory_tab()
        before = self._module_frame()
        folder = Path(self.paths['evidence']) / ('manifest_' + str(time.time_ns()))
        folder.mkdir(parents=True)
        before_path = folder / 'before.png'
        if not cv2.imwrite(str(before_path), before):
            raise RuntimeError('Could not save original equipment screenshot')
        manifest = {'version':2, 'stage':'snapshot', 'before':str(before_path),
                    'folder':str(folder), 'slots':[], 'inventory':[], 'inventory_complete':False}
        categories = ('cannon','armor','generator','core')
        for index, slot in enumerate(pills.header_slots(before)):
            if stopped():
                break
            slot['category'] = categories[index % 4]
            slot['role'] = 'primary' if slot['kind'] == 'large' else 'assist'
            slot['id'] = slot['category'] + '_' + slot['role']
            record = {'slot':slot, 'state':'unknown', 'before':str(before_path)}
            slot_dir = folder / slot['id']
            slot_dir.mkdir()
            self.progress(f"Snapshot {index+1}/8: {slot['category']} {slot['role']}")
            try:
                panel = self._panel(slot['centre'])
                panel_path = slot_dir / 'panel.png'
                if not cv2.imwrite(str(panel_path), panel):
                    raise RuntimeError('Could not save slot screenshot')
                record['panel'] = str(panel_path)
                texts = ' '.join(t.casefold() for y,x,t in textocr.read_lines(panel,1.0))
                if 'empty' in texts:
                    record['state'] = 'empty'
                elif 'slot locked' in texts or ('unlock' in texts and 'assist module slot' in texts):
                    record['state'] = 'locked'
                else:
                    record['identity'] = self._identity(panel)
                    record['title_image'] = self._save_title(panel, slot_dir)
                    record['state'] = 'equipped'
            except RuntimeError as exc:
                record['error'] = str(exc)
            finally:
                self._close()
            if record['state'] == 'unknown' and slot['role'] == 'assist':
                if self._locked_assist(slot['category'], slot_dir):
                    record['state'] = 'locked'
                    record.pop('error',None)
            manifest['slots'].append(record)
            # A read-only snapshot is useful even when a slot is unreadable.
            target = journal_path(self.paths).with_name('module_manifest.json')
            temp = target.with_suffix('.tmp')
            temp.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
            os.replace(temp,target)
        return manifest

    def _save_title(self, panel, folder):
        import cv2
        from interactions import inventory
        close = inventory._find_close(panel)
        if close is None:
            raise RuntimeError('Module title cannot be located')
        from vision import textocr
        from player import calibrate
        names = [y for y,x,t in textocr.read_lines(panel,1.0)
                 if x >= 395 and close[1]-30 <= y <= close[1]+190 and calibrate.resolve_module(t)]
        if len(names) != 1:
            raise RuntimeError('Module name cannot be uniquely located for its title image')
        # No-star modules put the name higher than starred modules. A fixed
        # bottom included their equipped-only multiplier and rejected the
        # same module in inventory. End at the observed name instead.
        path = folder / 'title.png'
        temp = folder / 'title.tmp.png'
        if not cv2.imwrite(str(temp),panel[close[1]-30:names[0]+38,410:905]):
            raise RuntimeError('Could not save module title evidence')
        os.replace(temp,path)
        return str(path)

    def _locked_assist(self, category, folder):
        import cv2
        from device import capture, act
        from vision import textocr
        frame = self._module_frame()
        choices = [(x+20,y+1010) for y,x,t in textocr.read_lines(frame[1000:1100],1.0)
                   if t.strip().casefold() == 'assist']
        if len(choices) != 1:
            return False
        act.tap(*choices[0], 'calibration: inspect assist availability (no purchase)')
        try:
            frame = self._module_frame()
            cv2.imwrite(str(folder / 'availability.png'),frame)
            text = ' '.join(t.casefold() for y,x,t in textocr.read_lines(frame,1.0))
            return f'unlock {category} assist module slot' in text
        finally:
            self.inventory_tab()

    def _slot_matches(self, record, frame=None):
        import cv2
        import numpy as np
        before = cv2.imread(record['before'])
        if before is None:
            raise RuntimeError('Original equipment screenshot is missing')
        current = self._module_frame() if frame is None else frame
        x,y = record['slot']['centre']
        old,new = before[y-90:y+125,x-90:x+90],current[y-90:y+125,x-90:x+90]
        return (np.abs(old.astype(float)-new.astype(float)).mean() <= 3 or
                float(cv2.matchTemplate(new,old,cv2.TM_CCOEFF_NORMED)[0,0]) >= .98)

    def unequip_slot(self, record):
        from vision import pills
        if not self._slot_matches(record):
            raise RuntimeError('Equipment changed after the manifest was saved')
        panel = self._panel(record['slot']['centre'])
        current = self._identity(panel)
        if current != record['identity']:
            self._close()
            raise RuntimeError('Slot identity changed before Unequip')
        self._action('Unequip')
        self._close()
        slots = pills.header_slots(self._module_frame())
        actual = next(s for s in slots if tuple(s['centre']) == tuple(record['slot']['centre']))
        if actual['occupied']:
            raise RuntimeError('The module slot did not become empty after Unequip')

    def _inventory_identity(self, panel):
        try:
            return self._identity(panel)
        except RuntimeError:
            # Common/rare tiles may have no rolled effects. Record their
            # names too, but never use incomplete identity to restore a build.
            from interactions import inventory
            from vision import textocr
            from player import calibrate
            close = inventory._find_close(panel)
            if close is None:
                raise RuntimeError('Inventory detail panel cannot be identified')
            texts = [t for y,x,t in textocr.read_lines(panel[close[1]-30:close[1]+190,395:985],2.0)]
            name = next((calibrate.resolve_module(t) for t in texts if calibrate.resolve_module(t)),None)
            rarity = next((calibrate.parse_rarity(t) for t in texts if calibrate.parse_rarity(t)),None)
            return {'slug':name,'rarity':rarity,'effects':None}

    def scan_inventory(self, manifest, stopped=lambda:False):
        import cv2
        from interactions import inventory
        from vision import pills
        self._close()
        self.inventory_tab()
        inventory.park_top()
        records = []
        placed = []
        offset = 0
        folder = Path(manifest['folder']) / 'inventory'
        folder.mkdir(exist_ok=True)
        manifest['inventory'] = records
        manifest['inventory_complete'] = False
        for page in range(inventory.MAX_PAGES):
            frame = inventory.settle()
            for row,cy in enumerate(pills.grid_rows(frame)):
                for col,cx in enumerate(inventory.COL_X):
                    if stopped():
                        _save(self.paths,manifest)
                        return
                    if any(c == col and abs(y-offset-cy) < 80 for y,c in placed):
                        continue
                    icon = inventory._tile_icon(frame,cx,cy)
                    if inventory._blank_tile(icon):
                        continue
                    placed.append((offset+cy,col))
                    self.progress(f'Inventory page {page+1}, module {len(records)+1}: reading name and rarity')
                    panel = self._panel((cx,cy))
                    try:
                        from player.module_descriptor import read_descriptor
                        from player.calibrate import module_slug
                        descriptor_name, descriptor_rarity, icon_box = read_descriptor(icon,panel)
                        identity = self._inventory_identity(panel)
                        if identity.get("slug") != module_slug(descriptor_name) or identity.get("rarity") != descriptor_rarity:
                            raise RuntimeError("Module descriptor disagrees with the clicked inventory icon")
                        item_dir = folder / str(len(records))
                        item_dir.mkdir(exist_ok=True)
                        icon_path,panel_path = item_dir/'icon.png',item_dir/'panel.png'
                        if not cv2.imwrite(str(icon_path),icon) or not cv2.imwrite(str(panel_path),panel):
                            raise RuntimeError('Could not save inventory evidence')
                        item = {'identity':identity,'icon':str(icon_path),'panel':str(panel_path),
                                'page':page,'row':row,'col':col}
                        records.append(item)
                        _save(self.paths,manifest)
                    finally:
                        self._close()
            moved = inventory.next_page()
            if moved == 0:
                manifest['inventory_complete'] = True
                _save(self.paths,manifest)
                if any(not item['identity'].get('slug') for item in records):
                    raise RuntimeError('Some inventory names were unreadable. Evidence was saved; restoring the original setup.')
                self._publish_inventory(manifest)
                return
            if moved is None:
                raise RuntimeError('Inventory paging lost overlap; original equipment will be restored')
            offset += moved
        raise RuntimeError('Inventory page limit reached before confirming the end')

    @staticmethod
    def _matches(item, record):
        wanted,got = record['identity'],item['identity']
        if not all(got.get(k) == wanted.get(k) for k in ('slug','rarity','effects')):
            return False
        if record.get('title_image') and item.get('panel'):
            import cv2
            title,panel = cv2.imread(record['title_image']),cv2.imread(item['panel'])
            if title is None or panel is None:
                raise RuntimeError('Module identity evidence is missing')
            return cv2.minMaxLoc(cv2.matchTemplate(panel,title,cv2.TM_CCOEFF_NORMED))[1] >= .99
        return True

    def _publish_inventory(self, manifest):
        import cv2
        targets = [r for r in manifest['slots'] if r['state']=='equipped']
        chosen = {}
        # Prefer the actual equipped copy over a lower-rarity inventory copy.
        for item in manifest['inventory']:
            slug = item['identity']['slug']
            if slug and (slug not in chosen or any(self._matches(item,r) for r in targets)):
                chosen[slug] = item
        for slug,item in chosen.items():
            icon = cv2.imread(item['icon'])
            self.cal.cut('modules',f'modules/{slug}.png',icon,icon,slug.replace('_',' ').title(),
                         {'rarity':item['identity']['rarity'],'source':'complete_inventory'})
        self.cal.player['modules_in_grid'] = sorted(chosen)
        self.cal.player['modules_copies'] = [{'slug':i['identity']['slug'],'rarity':i['identity']['rarity'],
                                            'page':i['page'],'row':i['row'],'col':i['col']} for i in manifest['inventory'] if i['identity']['slug']]
        self.cal.save_report()

    def finalize_manifest(self, manifest):
        if not manifest.get('all_unequipped') or not manifest.get('inventory_complete'):
            return
        remaining = list(manifest['inventory'])
        equipped = [r for r in manifest['slots'] if r['state'] == 'equipped']
        for record in equipped:
            found = next((i for i,item in enumerate(remaining) if self._matches(item,record)),None)
            if found is None:
                raise RuntimeError('Restored module is absent from the saved inventory index')
            remaining.pop(found)
        self.cal.player['modules_equipped'] = [r['identity']['slug'] for r in equipped]
        self.cal.player['modules_in_grid'] = sorted({i['identity']['slug'] for i in remaining if i['identity']['slug']})
        self.cal.player['modules_copies'] = [{'slug':i['identity']['slug'],'rarity':i['identity']['rarity'],
                                            'page':i['page'],'row':i['row'],'col':i['col']} for i in remaining if i['identity']['slug']]
        self.cal.save_report()

    def restore_manifest(self, manifest):
        from interactions import inventory, tourney
        if self.restored(manifest):
            return
        targets = [r for r in manifest['slots'] if r['state']=='equipped' and not self._slot_matches(r)]
        # An interrupted scan reindexes remaining inventory once; it never
        # falls back to a repeated name/OCR sweep for each module.
        if any(not any(self._matches(i,r) for i in manifest['inventory']) for r in targets):
            self.progress('Recovery: indexing remaining modules once for restoration')
            self.scan_inventory(manifest)
        manifest['stage'] = 'restoring'
        _save(self.paths,manifest)
        for record in targets:
            candidates = [i for i in manifest['inventory'] if self._matches(i,record)]
            if not candidates:
                raise RuntimeError('No matching inventory copy for ' + record['slot']['id'])
            self.progress('Restoring ' + record['slot']['id'] + ': ' + record['identity']['slug'].replace('_',' '))
            self._equip_indexed(record,candidates)
            record['restored'] = True
            _save(self.paths,manifest)

    @staticmethod
    def _icon_candidate(frame, cx, cy, icons):
        import cv2
        from interactions import inventory,tourney
        # Grid reflow changes the icon's offset inside its tile. Search a
        # native neighborhood with the normal inventory threshold; exact
        # name/rarity/effects/title checks still gate the actual Equip action.
        region = frame[max(inventory.GRID_BAND[0],cy-100):min(inventory.GRID_BAND[1],cy+100),
                       max(0,cx-100):min(frame.shape[1],cx+100)]
        return any(icon.shape[0] <= region.shape[0] and icon.shape[1] <= region.shape[1]
                   and cv2.minMaxLoc(cv2.matchTemplate(region,icon,cv2.TM_CCOEFF_NORMED))[1] >= tourney.STRICT
                   for icon in icons)

    def _equip_indexed(self, record, candidates):
        import cv2
        import time
        from interactions import inventory,tourney
        from vision import pills
        icons = [cv2.imread(item['icon']) for item in candidates]
        if any(icon is None for icon in icons):
            raise RuntimeError('Restoration inventory images are missing')
        self._close()
        self._module_frame()
        inventory.park_top()
        for page in range(inventory.MAX_PAGES):
            frame = inventory.settle()
            for cy in pills.grid_rows(frame):
                for cx in inventory.COL_X:
                    if not self._icon_candidate(frame,cx,cy,icons):
                        continue
                    panel = self._panel((cx,cy))
                    live_path = Path(record['panel']).with_name('restore_candidate.png')
                    if not cv2.imwrite(str(live_path),panel):
                        raise RuntimeError('Could not save restore candidate')
                    if not self._matches({'identity':self._inventory_identity(panel),'panel':str(live_path)},record):
                        self._close()
                        continue
                    self._action('Equip')
                    self._choose_role(record['slot']['role'])
                    time.sleep(.6)
                    self._close()
                    if not self._slot_matches(record):
                        raise RuntimeError('The original slot was not restored: '+record['slot']['id'])
                    return
            moved = inventory.next_page()
            if moved == 0:
                break
            if moved is None:
                raise RuntimeError('Restoration paging lost overlap')
        raise RuntimeError('The indexed module is no longer visible for '+record['slot']['id'])
