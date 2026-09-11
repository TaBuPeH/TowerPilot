"""Native display contract shared by setup and calibration."""
import re

WIDTH, HEIGHT, DPI = 1080, 2560, 360


def discover():
    """Measure the game display through the existing socket connection."""
    from device import capture, adbclient
    from settings import instance
    from player.geometry import Display
    inst = instance()
    capture.refresh_display()
    frame = capture.grab(discover=True)
    command = 'wm density'
    if inst.get('input_display') is not None:
        command += f" -d {int(inst['input_display'])}"
    dpi = density(adbclient.shell(inst['serial'], command).decode(errors='replace'))
    display = Display(int(frame.shape[1]), int(frame.shape[0]), dpi)
    if display.width >= display.height:
        raise ValueError('Open The Tower in portrait before scanning')
    return display


def activate(display):
    from settings import CONFIG, instance
    from dataclasses import asdict
    from player.bootstrap_layout import bind_display, manifest
    bind_display(display)
    CONFIG['screen'] = {'width': display.width, 'height': display.height}
    instance()['rendering'] = asdict(display)
    from vision import pills
    m = manifest()
    # Rendering-scoped geometry supplies run readers as well as calibration.
    # The optional wall is enabled only by a verified account observation.
    from copy import deepcopy
    native = m['runtime_layout']
    from device import capture
    capture.PANEL_SHIFT = native['panel_shift']
    capture.layout_offset = 0
    CONFIG['rois'].update(deepcopy({k:v for k,v in native['rois'].items() if k != 'wall_bar'}))
    CONFIG['tabs'].update(deepcopy(native['tabs']))
    reference = m['_runtime_geometry']['reference']
    if asdict(display) == reference:
        # Preserve legacy user measurements only on their original display.
        for section in ('rois', 'tabs'):
            CONFIG[section].update(deepcopy(instance().get(section, {})))
    else:
        CONFIG['rois']['wall_bar'] = None
    measured = instance().get('runtime_geometry', {})
    if (measured.get('display') == asdict(display)
            and measured.get('manifest_revision') == m['_runtime_geometry']['revision']
            and measured.get('profile_revision') == m['_runtime_geometry']['profile_revision']):
        for section in ('rois', 'tabs'):
            CONFIG[section].update(deepcopy(measured.get(section, {})))
    tx,ty,tw,th = m['side_menu']['toggle']['rect']
    CONFIG['side_menu'].update(toggle=[tx+tw//2,ty+th//2], slot_roi=[tx,ty,tw,th])
    pills.TAB_BANDS.clear()
    pills.TAB_BANDS.update({k:tuple(v) for k,v in m['tab_bands'].items()})
    pills.HEADER_LARGE = tuple(map(tuple,m['module_slots']['large']))
    pills.HEADER_SMALL = tuple(map(tuple,m['module_slots']['small']))
    pills.HEADER_RADIUS = dict(m['module_slots']['radius'])
    pills.GRID_CLEAR = tuple(m['module_inventory']['grid_clear'])
    pills.TILE_HALF = m['module_inventory']['tile_half']
    from interactions import inventory
    inventory.configure_geometry()
    import sys
    flow = sys.modules.get('player.flow_capture')
    if flow:
        for key, rect in m.get('flow_regions', {}).items():
            setattr(flow, 'R_'+key.upper(), tuple(rect))
        flow.WALL_BAR_ROI = tuple(m['flow_regions']['wall_bar'])
        flow.HP_BAR_ROI = tuple(m['flow_regions']['hp_bar'])


def prepare_scan():
    """Bind and persist rendering before resolving any calibration paths."""
    import os
    import tempfile
    import time
    import yaml
    import settings
    from dataclasses import asdict
    display = discover()
    activate(display)
    cfg = yaml.safe_load(settings.CONFIG_PATH.read_text(encoding='utf-8'))
    if cfg['instances'][settings.CONFIG['active_instance']].get('rendering') == asdict(display):
        return display
    cfg['instances'][settings.CONFIG['active_instance']]['rendering'] = asdict(display)
    fd, name = tempfile.mkstemp(dir=settings.CONFIG_PATH.parent, suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            yaml.safe_dump(cfg,stream,allow_unicode=True,sort_keys=False)
        for attempt in range(6):
            try:
                os.replace(name,settings.CONFIG_PATH)
                break
            except PermissionError:
                if attempt == 5: raise
                time.sleep(.05 * 2**attempt)
    finally:
        if os.path.exists(name): os.unlink(name)
    return display


def density(text):
    matches = re.findall(r"(?:Physical|Override) density:\s*(\d+)", text)
    return int(matches[-1]) if matches else None


def require_native(width, height, dpi):
    if (width, height, dpi) != (WIDTH, HEIGHT, DPI):
        raise ValueError(f"Display is {width}×{height} at {dpi or 'unknown'} dpi. "
                         "Use Setup to configure 1080×2560 portrait at 360 dpi "
                         "(BlueStacks panel: 2560×1080 landscape). No calibration was started.")
