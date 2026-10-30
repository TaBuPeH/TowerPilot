"""The fleet timetable golden answers (T3-0): scheduling/fleets.py for a tier from the blueprint and the legacy fallback when the run
names no tier, and every consumer of orchestrator.marks().

The five consumers (found by search, orchestrator.py): cl_window (Chain Lightning around the marks), _trigger_fires (the fleet_mark rule),
_fast_wall_watch (the nuke inside the post-Second-Wind watch), watch_frame (the proactive nuke) and the danger-window shot in main(). Each
runs for real here, with marks() NOT replaced: only the run's tier and the configured legacy schedule change, so the answer is what the
orchestrator does with the timetable of a tier, of no tier plus a legacy schedule, and of no tier at all."""
import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import paths as test_paths

import orchestrator
from scheduling import fleets
from vision import detect, wave_reader

from takeover_golden_loop import LoopWorld, frame

LEGACY = {'first_wave': 2495, 'interval': 1000}
TIERS = (None, 1, 5, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 25)
FULL = (None, 1, 14, 18)             # tiers whose whole list is recorded; the others keep their size, ends and digest


def digest(marks):
    return hashlib.sha256(json.dumps(marks, separators=(',', ':')).encode('ascii')).hexdigest()


def table_row(tier, cfg):
    marks = fleets.marks(tier, cfg)
    row = dict(schedule=fleets.schedule(tier, cfg), count=len(marks), head=marks[:8], tail=marks[-3:], sha256=digest(marks))
    if tier in FULL:
        row['marks'] = marks
    return row


def fleet_tables():
    """scheduling.fleets for every tier family, with no config, with the legacy single schedule, and with a partial by_tier override."""
    override = {'fleet': {**LEGACY, 'by_tier': {18: {'first_wave': 90, 'interval': 100}, '14': {'first_wave': 2000, 'interval': 500}}}}
    return {'tier': {str(t): table_row(t, None) for t in TIERS},
            'legacy_schedule': {str(t): table_row(t, {'fleet': LEGACY}) for t in (None, 14, 18)},
            'config_override': {str(t): table_row(t, override) for t in (None, 14, 18, 19)},
            'no_tier_and_no_legacy_schedule': table_row(None, {'fleet': {'hold_window': 100}}),
            'legends_row': dict(fleets.LEGENDS), 'max_wave': fleets.MAX_WAVE}


def table_readers():
    """Who calls scheduling.fleets (found by parsing every non-test module of backend/): module -> the fleets functions it calls."""
    backend = test_paths.BACKEND
    found = {}
    for path in sorted(backend.rglob('*.py')):
        relative = path.relative_to(backend).as_posix()
        if relative.startswith(('tests/', 'logs/', 'accounts/', 'runs/')) or path.name == 'fleets.py':
            continue
        calls = sorted({node.func.attr for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == 'fleets'})
        if calls:
            found[relative] = calls
    return found


# ---------------------------------------------------------------- orchestrator.marks() and run_tier()
def orchestrator_marks(monkeypatch):
    """run_tier() and marks() as the orchestrator reads them: from the preset when no battle session is bound, from the session's frozen
    configuration and effective preset when one is."""
    out = {}
    for label, preset, fleet in (('tier_14', {'tier': 14}, LEGACY), ('tier_18', {'tier': 18}, LEGACY), ('tier_0', {'tier': 0}, LEGACY),
                                 ('no_tier_with_legacy_schedule', {}, LEGACY), ('no_tier_without_legacy_schedule', {}, {'hold_window': 100}),
                                 ('tier_as_string', {'tier': '14'}, LEGACY)):
        monkeypatch.setattr(orchestrator, '_battle_session', None)
        monkeypatch.setattr(orchestrator, 'CONFIG', {'fleet': fleet})
        monkeypatch.setattr(orchestrator, 'preset', lambda preset=preset: preset)
        marks = orchestrator.marks()
        out[label] = dict(run_tier=orchestrator.run_tier(), count=len(marks), head=marks[:4], sha256=digest(marks))
    session = SimpleNamespace(config={'fleet': {'by_tier': {'18': {'first_wave': 95, 'interval': 100}}}}, effective_preset=lambda: {'tier': 18})
    monkeypatch.setattr(orchestrator, '_battle_session', session)
    monkeypatch.setattr(orchestrator, 'CONFIG', {'fleet': {'by_tier': {'18': {'first_wave': 999, 'interval': 1000}}}})
    monkeypatch.setattr(orchestrator, 'preset', lambda: session.effective_preset())
    out['session_config_wins'] = dict(run_tier=orchestrator.run_tier(), head=orchestrator.marks()[:3])
    return out


# ---------------------------------------------------------------- the consumers
WORLDS = (('tier_14', 14, LEGACY), ('tier_18', 18, LEGACY), ('no_tier_with_legacy_schedule', None, {'first_wave': 3000, 'interval': 500}),
          ('no_tier_without_legacy_schedule', None, {}))
WAVES = {'tier_14': (2490, 2492, 2495, 2498, 2500, 2560, 3495, 3499, 3560, 12000),
         'tier_18': (90, 93, 95, 98, 100, 155, 195, 199),
         'no_tier_with_legacy_schedule': (2995, 2997, 3000, 3003, 3005, 3500, 3504, 4000),
         'no_tier_without_legacy_schedule': (95, 2495, 3000)}


def chain_lightning_windows(monkeypatch):
    """cl_window(rs, wave): Chain Lightning is wanted from `pre_mark_waves` before a mark to `off_after_waves` after it."""
    out = {}
    for label, tier, fleet in WORLDS:
        monkeypatch.setattr(orchestrator, '_battle_session', None)
        monkeypatch.setattr(orchestrator, 'CONFIG', {'fleet': dict(fleet)})
        body = {'chain_lightning': {'enabled': True, 'pre_mark_waves': [3, 3], 'off_after_waves': [5, 5]}}
        if tier is not None:
            body['tier'] = tier
        monkeypatch.setattr(orchestrator, 'preset', lambda body=body: body)
        rs = SimpleNamespace(cl_offsets={}, cl_always_above=None)
        out[label] = {str(wave): orchestrator.cl_window(rs, wave) for wave in WAVES[label]}
        out[label]['offsets'] = dict(count=len(rs.cl_offsets), distinct=sorted({tuple(v) for v in rs.cl_offsets.values()}),
                                     marks_sha256=digest(sorted(rs.cl_offsets)))
    return out


def fleet_mark_rule(monkeypatch):
    """_trigger_fires for the fleet_mark trigger: whether it fires per wave, its snapshot (the nearest pending mark), and the marks it
    retired (a wave past a mark's window retires it, never fires late)."""
    out = {}
    for label, tier, fleet in WORLDS:
        monkeypatch.setattr(orchestrator, '_battle_session', None)
        monkeypatch.setattr(orchestrator, 'CONFIG', {'fleet': dict(fleet)})
        body = {} if tier is None else {'tier': tier}
        monkeypatch.setattr(orchestrator, 'preset', lambda body=body: body)
        rows = []
        for wave in WAVES[label]:
            rs = SimpleNamespace(rule_marks={})
            fired, snapshot = orchestrator._trigger_fires(rs, 'FRAME', wave, 0, 'fleet_mark', {'after_waves': 3, 'window_waves': 60}, {})
            rows.append(dict(wave=wave, fired=fired, snapshot=snapshot, retired=sorted(rs.rule_marks.get(0, ()))))
        out[label] = rows
    return out


def _quiet_battle(monkeypatch, cfg, tier):
    """The fakes test_fleet_gate uses to run watch_frame / _fast_wall_watch over a synthetic frame, with the fleet sighting off so the gate
    answers 'schedule' and the mark alone decides."""
    import copy
    monkeypatch.setattr(orchestrator, '_battle_session', None)
    monkeypatch.setattr(orchestrator, 'CONFIG', cfg)
    monkeypatch.setattr(orchestrator.logger, 'event', lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.logger, 'shot', lambda *a, **k: '')
    abilities = {'rescue_bar': None, 'dm_below': 0.2, 'nuke_on_fleet': {'after_waves': 1, 'window_waves': 60}}
    base = {'abilities': abilities}
    if tier is not None:
        base['tier'] = tier
    monkeypatch.setattr(orchestrator, 'preset', lambda: copy.deepcopy(base))
    monkeypatch.setattr(orchestrator.detect, 'death_screen', lambda f: (False, None))
    monkeypatch.setattr(orchestrator.detect, 'second_wind_badge', lambda f: (False, 0.0))
    monkeypatch.setattr(orchestrator, 'demon_mode_always', lambda *a: False)
    return abilities


def _fresh_run_state(wave):
    rs = orchestrator.RunState()
    rs.tracker.last = wave
    rs.tracker.update = lambda w: None
    rs.gems = SimpleNamespace(poll=lambda f: f)
    return rs


def proactive_nuke(monkeypatch):
    """watch_frame's proactive fleet nuke: which waves fire which mark (reason fleet_<mark>), on one RunState walked through the waves."""
    out = {}
    for label, tier, fleet in WORLDS:
        cfg = {'fleet': {**fleet, 'sighting': {'enabled': False}}, 'rois': {'wall_bar': None}, 'loop': {'fps': 2}, 'active_instance': 'main',
               'preset': 'farm', 'presets': {'farm': {'kind': 'coin', 'gather': {}, 'shopping': [], **({} if tier is None else {'tier': tier})}}}
        _quiet_battle(monkeypatch, cfg, tier)
        rs = _fresh_run_state(0)
        fired, rows = [], []
        monkeypatch.setattr(orchestrator, 'fire_button', lambda image, name, reason, **kw: fired.append([name, reason]) or True)
        for wave in WAVES[label]:
            rs.tracker.last, rs.fleet_try_at = wave, 0.0
            before = len(fired)
            status = orchestrator.watch_frame(rs, 'FRAME', observation={'raw_wave': wave})
            rows.append(dict(wave=wave, status=status, fired=fired[before:], nuked_marks=sorted(rs.nuked_marks)))
        out[label] = rows
    return out


def fast_wall_nuke(monkeypatch):
    """_fast_wall_watch's nuke inside the post-Second-Wind watch: on the 8th sample the wave is read and the nearest pending mark fires."""
    out = {}
    for label, tier, fleet in WORLDS:
        cfg = {'fleet': {**fleet, 'sighting': {'enabled': False}}, 'rois': {'wall_bar': None}, 'loop': {'fps': 2}, 'active_instance': 'main',
               'preset': 'farm', 'presets': {'farm': {'kind': 'coin', 'gather': {}, 'shopping': [], **({} if tier is None else {'tier': tier})}}}
        abilities = _quiet_battle(monkeypatch, cfg, tier)
        rows = []
        for wave in WAVES[label]:
            rs = _fresh_run_state(wave)
            rs.post_sw_until = float('inf')
            samples, fired = [], []
            monkeypatch.setattr(orchestrator, 'fire_button', lambda image, name, reason, fired=fired, **kw: fired.append([name, reason]) or True)
            monkeypatch.setattr(orchestrator.capture, 'grab', lambda samples=samples: samples.append(1) or 'FRAME')
            monkeypatch.setattr(wave_reader, 'read_wave', lambda image, wave=wave: wave)
            monkeypatch.setattr(detect, 'wall_overheal', lambda image, samples=samples: (1.0, 'immune' if len(samples) >= 8 else 'normal'))
            monkeypatch.setattr(orchestrator.time, 'sleep', lambda *a: None)
            status = orchestrator._fast_wall_watch(rs, abilities)
            rows.append(dict(wave=wave, status=status, fired=fired, nuked_marks=sorted(rs.nuked_marks), samples=len(samples)))
        out[label] = rows
    return out


DANGER_WAVES = {'tier_14': (2490, 2495, 2500, 2645, 2646, 3495, 3645, 3646), 'tier_18': (94, 95, 245, 246, 295),
                'no_tier_with_legacy_schedule': (2999, 3000, 3150, 3151), 'no_tier_without_legacy_schedule': (95, 2495, 3000)}


def danger_window_shots(label):
    """main()'s science shot `danger_w<wave>`: taken while a wave is inside [mark, mark + fleet.post_window]. One live pass per wave."""
    _, tier, fleet = next(world for world in WORLDS if world[0] == label)

    def scenario(tmp_path, monkeypatch):
        waves = DANGER_WAVES[label]
        table = {'initial': frame(waves[0]), 'coin_check': frame(waves[0])}
        table.update({f'w{n}': frame(wave) for n, wave in enumerate(waves)})
        extra = {'fleet': dict(hold_window=100, post_window=150, by_tier={}, **fleet)}
        run = LoopWorld(tmp_path, monkeypatch, frames=list(table), table=table, tier=tier, session=False, cfg_extra=extra, tail=True, follow=True,
                         preset_extra=dict(shopping=[{'enabled': True, 'stats': ['damage']}])).run()
        return dict(ended=run['ended'], crashes=run['crashes'], waves=list(waves),
                    shots=[json.loads(line)[1] for line in run['journal'] if line.startswith('["shot"')])
    scenario.__name__ = f'danger_window_shots_{label}'
    return scenario


def table_scenario(function):
    return lambda tmp_path, monkeypatch: function()


def monkeypatched(function):
    return lambda tmp_path, monkeypatch: function(monkeypatch)


MARKS = {'fleet_tables': table_scenario(fleet_tables), 'table_readers': table_scenario(table_readers), 'orchestrator_marks': monkeypatched(orchestrator_marks),
         'chain_lightning_windows': monkeypatched(chain_lightning_windows), 'fleet_mark_rule': monkeypatched(fleet_mark_rule),
         'proactive_nuke': monkeypatched(proactive_nuke), 'fast_wall_nuke': monkeypatched(fast_wall_nuke)}
MARKS.update({f'danger_window_shots_{label}': danger_window_shots(label) for label, _, _ in WORLDS})
