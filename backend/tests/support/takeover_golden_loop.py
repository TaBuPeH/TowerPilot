"""The orchestrator's main loop over fakes: one scripted world for the takeover golden answers (T3-0).

`orchestrator.main()` runs for real, over the real BattleSession and a real ledger in a temporary directory. What is faked is the
device edge (capture, the two input functions, the sleeps) and the screen readers (the wave, the screen name, the death dialog,
GAME STATS), and `watch_frame` (its verdict per frame is scripted: the death and menu logic is not what a takeover changes). Every
call that matters is journaled in order, one compact JSON line each, so a golden answer is the exact sequence of device calls,
ledger writes and log events a scenario produced. Frames are plain names; nothing here reads a pixel.

The loop ends when the script of frames runs out (the next grab raises `Reobserved`, a BaseException so the loop's own
`except Exception` cannot swallow it) or when main() returns by itself; the observation says which."""
import copy
import json
from types import SimpleNamespace

import orchestrator
from control import battles as control_battles
from device import act, capture
from flows import shard
from interactions import loadout, tourney
from player import readiness
from player.run_controls import effective_preset
import time
from runtime import battle_session as battle_session_module
from runtime import battle_source, device_lease, logger, runlog
from runtime.battle_session import BattleSession
from scheduling import runflag
from towerpilot_workflows.battles import Battles
from towerpilot_workflows.operations import Operations
from vision import detect, screen, wave_reader


class SteadyClock:
    """The clock BattleSession reads: its monotonic time creeps by a millisecond per read, so the once-a-second status publish happens at
    the first checkpoint of a scenario and never again, however slow the machine running the test is."""
    time = staticmethod(time.time)

    def __init__(self):
        self.now = 10_000.0

    def monotonic(self):
        self.now += 0.001
        return self.now


class FollowingTracker:
    """A wave tracker that believes every read (the real one holds jumps of more than 20 waves on probation): for scenarios that walk
    through far-apart waves."""

    def __init__(self):
        self.last = None

    def update(self, raw):
        if raw is not None:
            self.last = raw
        return self.last


class Reobserved(BaseException):
    """The scripted frames ran out: the loop was about to look again."""


def frame(wave=None, *, screen_name='battle', status=None, stats=False, retry=None):
    """What the fakes answer for one named frame: its wave read, its screen name, watch_frame's verdict, whether GAME STATS
    matches, and the RETRY button center (None: no death dialog)."""
    return dict(wave=wave, screen=screen_name, status=status, stats=stats, retry=retry)


def make_config(tier=14, *, preset_extra=None, compiled=True):
    """A literal configuration with one coin run on `tier` (not the shipped example, so an edit of its presets cannot move a golden).
    `compiled` names the run bp_farm and stamps a `_source`, the shape the dashboard launches; otherwise it is the plain legacy `farm`."""
    key = 'bp_farm' if compiled else 'farm'
    body = dict(kind='coin', label='Golden farm', loadout=None, shopping=[], gather={}, abilities={}, rules=[])
    if tier is not None:
        body['tier'] = tier
    if compiled:
        body['_source'] = dict(profile='golden', blueprint='farm')
    body.update(preset_extra or {})
    return dict(active_instance='main', active_profile=None, instances={'main': {'serial': 'synthetic-golden'}}, loadouts={},
                loop=dict(fps=2, dry_run=False), logging=dict(bench_interval_sec=30), fleet=dict(hold_window=100, post_window=150, by_tier={}),
                rois={}, presets={key: body}, preset=key)


class LoopWorld:
    """Everything one scenario needs: the config, the ledger, the frame table, the journal, and the patches."""

    def __init__(self, tmp_path, monkeypatch, *, frames, table, tier=14, compiled=True, preset_extra=None, session=True,
                 repeat=True, stop_flag=None, restart_result=True, death_rules=False, stats_artifact=False,
                 gate_result=True, tap_refused=False, cfg_extra=None, tail=False, follow=False):
        self.mp, self.journal = monkeypatch, []
        self.frames, self.table = list(frames), table
        self.cfg = make_config(tier, preset_extra=preset_extra, compiled=compiled)
        if not repeat:
            self.cfg['repeat'] = False
        self.cfg.update(copy.deepcopy(cfg_extra or {}))
        self.restart_result, self.death_rules_result = restart_result, death_rules
        self.stats_artifact, self.tap_refused = stats_artifact, tap_refused
        self.flag = tmp_path / 'stop_after_run'
        if stop_flag is not None:
            self.flag.write_text(stop_flag, encoding='utf-8')
        self.store = self.op = None
        if session:
            self._ledger(tmp_path)
        self.names = {}
        self._patch(tmp_path, gate_result=gate_result)
        if tail:
            self._patch_tail()
        if follow:
            monkeypatch.setattr(wave_reader, 'WaveTracker', FollowingTracker)

    # ------------------------------------------------------------------ ledger
    def _ledger(self, tmp_path):
        context = dict(device_key='synthetic-golden-device', instance='main', account=None, serial='synthetic-golden',
                       config=copy.deepcopy(self.cfg))
        operations = Operations(tmp_path / 'operations.db')
        op, _ = operations.accept('run', context, {})
        self.op = operations.transition(op['id'], op['revision'], 'running')
        self.operations, self.store = operations, Battles(operations.path)
        self.mp.setenv('TOWER_OPERATION_ID', self.op['id'])
        self.mp.setenv('TOWER_OPERATION_DB', operations.path)
        for key in ('TOWER_BATTLE_ID', 'TOWER_BATTLE_GENERATION', 'TOWER_DEVICE_DELEGATION'):
            self.mp.delenv(key, raising=False)

    def label(self, battle_id):
        """battle-1, battle-2 ... in the order they are first named (ids are random)."""
        if battle_id is None:
            return None
        if battle_id not in self.names:
            self.names[battle_id] = f'battle-{len(self.names) + 1}'
        return self.names[battle_id]

    def ledger_view(self):
        if self.store is None:
            return None
        view = []
        for battle in sorted(self.store.list(limit=200), key=lambda b: b['created_at']):
            runtime = battle.get('runtime') or {}
            capture_status = runtime.get('stats_capture')
            view.append(dict(
                battle=self.label(battle['id']), origin=battle['origin'], previous=self.label(battle['previous_battle_id']),
                status=battle['status'], reason=battle['reason'], outcome=battle['outcome'],
                start_certainty=battle['start_certainty'], boundary_evidence=battle.get('boundary_evidence'),
                setup=(runtime.get('setup') or {}).get('detail'),
                stats_capture=None if capture_status is None else capture_status.get('status'),
                observed_wave=runtime.get('observed_wave')))
        return view

    # ------------------------------------------------------------------ the fakes
    def note(self, *entry):
        """One compact JSON line per call, in call order."""
        self.journal.append(json.dumps(list(entry), sort_keys=True, separators=(',', ':'), default=str))

    def _patch(self, tmp_path, *, gate_result):
        mp, note, world = self.mp, self.note, self
        mp.setattr(orchestrator, 'CONFIG', self.cfg)
        mp.setattr(orchestrator, '_battle_session', None)
        mp.setattr(battle_session_module, 'time', SteadyClock())
        mp.setattr(device_lease, 'ensure_current', lambda: note('lease.ensure_current'))
        real_guard = control_battles.require_runner_entry

        def guard(root, cfg, *args, **kwargs):
            note('require_runner_entry')
            return real_guard(root, cfg, *args, **kwargs)
        mp.setattr(control_battles, 'require_runner_entry', guard)
        mp.setattr(readiness, 'require', lambda root, cfg, body: note('readiness.require', dict(kind=body.get('kind'), tier=body.get('tier'))))
        mp.setattr(orchestrator, '_gate_preset', lambda: note('gate_preset') or gate_result)

        def event(name, **kw):
            kw = {key: value for key, value in kw.items() if key != 'trace'}
            if name == 'compiled_config':
                kw['hash'] = '<hash>'          # a hash of the preset body: the golden is about when it is logged, not its digest
            note('event', name, kw)
        mp.setattr(logger, 'event', event)
        mp.setattr(logger, 'shot', lambda image, tag: note('shot', tag) or f'shot/{tag}.png')
        mp.setattr(logger, 'set_battle_context', lambda ctx: None, raising=False)

        queue = list(self.frames)

        def grab():
            if not queue:
                note('grab', None)
                raise Reobserved()
            name = queue.pop(0)
            note('grab', name)
            return name
        mp.setattr(capture, 'grab', grab)
        mp.setattr(wave_reader, 'read_wave', lambda image: world.table[image]['wave'])
        mp.setattr(screen, 'identify', lambda image: SimpleNamespace(name=world.table[image]['screen'], score=1.0, wave=None))
        mp.setattr(orchestrator, 'watch_frame', lambda rs, image, observation=None: note('watch_frame', image) or world.table[image]['status'])
        mp.setattr(detect, '_match', lambda image, rel, threshold=0.8: (note('detect._match', image, rel, threshold)
                                                                          or (world.table[image]['stats'], 1.0, (0, 0))))
        mp.setattr(detect, 'death_screen', lambda image: (note('detect.death_screen', image)
                                                         or (world.table[image]['retry'] is not None, world.table[image]['retry'])))

        def tap(x, y, reason='', instant=False, **kw):
            note('act.tap', x, y, reason)
            if world.tap_refused and reason == 'auto_retry':
                raise act.TapRefused('synthetic refusal')
            return {'x': x, 'y': y}
        mp.setattr(act, 'tap', tap)
        mp.setattr(act, 'swipe', lambda *a, **kw: note('act.swipe', list(a), sorted(kw)) or {})
        mp.setattr(orchestrator.time, 'sleep', lambda seconds: note('sleep', seconds))

        def collect(instance, battle=None):
            note('runlog.collect', instance, None if battle is None else world.label(battle['id']))
            if not world.stats_artifact:
                return None
            artifact = tmp_path / 'stats-artifact'
            artifact.mkdir(exist_ok=True)
            (artifact / 'capture.json').write_text('{"status": "verified", "verified": true}', encoding='utf-8')
            return artifact
        mp.setattr(runlog, 'collect', collect)
        mp.setattr(runflag, '_path', lambda: str(world.flag))
        real_requested = runflag.requested

        def requested():
            answer = real_requested()
            note('runflag.requested', answer)
            return answer
        mp.setattr(runflag, 'requested', requested)
        mp.setattr(orchestrator, 'restart_from_home', lambda image, tier: note('restart_from_home', image, tier) or world.restart_result)
        mp.setattr(orchestrator, 'run_death_rules', lambda rs, image: note('run_death_rules', image, rs.tracker.last) or world.death_rules_result)

        def reload_source():
            note('reload_battle_source')
            return effective_preset(world.cfg), copy.deepcopy(world.cfg)
        mp.setattr(battle_source, 'reload_battle_source', reload_source)

        # entry: the equipment, tier and tournament set-up a person's Start or a death restart would run
        mp.setattr(loadout, 'apply', lambda name, **kw: note('loadout.apply', name) or {'setup': dict(
            state='verified', loadout=name, detail='manual_equipment', reason=None, continued_with_partial=False, modules=[])})
        mp.setattr(shard, 'set_tier', lambda tier: note('shard.set_tier', tier))
        mp.setattr(shard, 'enter_run', lambda body: note('shard.enter_run', body.get('kind')))
        mp.setattr(tourney, 'setup', lambda **kw: note('tourney.setup', sorted(kw)))
        mp.setattr(tourney, '_in_battle', lambda image: note('tourney._in_battle', image) or False)

        def wrap(name, observe):
            original = getattr(BattleSession, name)

            def spy(session, *args, **kwargs):
                note(f'session.{name}', *observe(session, args, kwargs))
                return original(session, *args, **kwargs)
            mp.setattr(BattleSession, name, spy)
        wrap('finish', lambda s, a, k: [a[0], k.get('outcome', 'unknown'), k.get('status', 'ended'), k.get('evidence')])
        wrap('prepare_next', lambda s, a, k: [k.get('origin', 'started'), world.label(k.get('previous_battle_id')),
                                              'preset' if (a or k.get('preset')) else 'same'])
        wrap('observe', lambda s, a, k: [a[1]])
        wrap('checkpoint', lambda s, a, k: [k.get('battle_visible')])

    def _patch_tail(self):
        """The rest of one main-loop pass for a live battle frame (rules, side menu, Chain Lightning, shopping), faked down to its
        end: the danger-window shot is the one thing the pass still does."""
        mp, note = self.mp, self.note
        for name in ('eval_rules', 'apply_cancel_sprint'):
            mp.setattr(orchestrator, name, lambda *a, _name=name: note(_name) or None)
        for name in ('max_wave_reached', 'run_in_run_actions', 'step_reward_mission', 'advance_rewards'):
            mp.setattr(orchestrator, name, lambda *a, _name=name: note(_name) or False)
        mp.setattr(orchestrator, 'advance_shopping', lambda *a: note('advance_shopping'))
        mp.setattr(detect, 'side_menu_open', lambda image: True)
        mp.setattr(detect, 'intro_sprint_active', lambda image: False)
        self.mp.setitem(self.cfg['presets'][self.cfg['preset']], 'chain_lightning', {'enabled': False})

    # ------------------------------------------------------------------ run
    def run(self):
        ended = 'returned'
        try:
            orchestrator.main()
        except Reobserved:
            ended = 'script_exhausted'
        crashes = [entry for entry in self.journal if entry.startswith('["event","crash"')]
        return dict(ended=ended, journal=self.journal, ledger=self.ledger_view(), stop_flag_left=self.flag.exists(),
                    stop_flag_text=self.flag.read_text(encoding='utf-8') if self.flag.exists() else None, crashes=len(crashes))
