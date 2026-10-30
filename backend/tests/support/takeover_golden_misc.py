"""Session, worker and pure-function golden answers (T3-0): BattleSession.from_environment's predecessor link, the operation worker's run
flag, readiness.requirements over a synthetic matrix of compiled runs, and the tournament / screen readers over synthetic frames.

Recorded documents: backend/tests/fixtures/takeover_golden/battle_session.json, operation_worker.json, readiness_requirements.json and
screen_readers.json. Nothing here reads a game image: frames are random arrays and the templates are written to a temporary directory."""
import copy
import json
from pathlib import Path

import cv2
import numpy as np

from control import operations
from player import accounts, readiness
from runtime.battle_session import BattleSession
from towerpilot_workflows.battles import Battles
from towerpilot_workflows.operations import Operations
from vision import detect, screen, wave_reader


# ---------------------------------------------------------------- BattleSession.from_environment
def session_config():
    return dict(active_instance='main', active_profile=None, preset='bp_farm', instances={'main': {'serial': 'synthetic-session'}}, loadouts={},
                presets={'bp_farm': dict(kind='coin', tier=14, loadout=None, shopping=[], gather={}, abilities={}, rules=[],
                                         _source=dict(profile='golden', blueprint='farm'))})


class SessionWorld:
    """A ledger in a temporary directory with the operation of the driver that starts, as the dashboard would have accepted it."""

    def __init__(self, tmp_path, monkeypatch):
        self.mp, self.cfg = monkeypatch, session_config()
        self.context = dict(device_key='synthetic-session-device', instance='main', account=None, serial='synthetic-session',
                            config=copy.deepcopy(self.cfg))
        self.operations = Operations(tmp_path / 'operations.db')
        self.store = Battles(self.operations.path)
        self.names = {}

    def label(self, battle_id):
        if battle_id is None:
            return None
        return self.names.setdefault(battle_id, f'battle-{len(self.names) + 1}')

    def accept(self, kind='run', request=None):
        op, _ = self.operations.accept(kind, copy.deepcopy(self.context), request or {})
        return self.operations.transition(op['id'], op['revision'], 'running')

    def end(self, op, status='succeeded'):
        return self.operations.transition(op['id'], self.operations.get(op['id'])['revision'], status)

    def battle(self, op, generation='old-driver'):
        envelope = dict(preset=copy.deepcopy(self.cfg['presets']['bp_farm']), config=copy.deepcopy(self.cfg),
                        controls=dict(routines={'rewards': {'enabled': True}}))
        return self.store.create(op['id'], self.context, envelope, generation=generation)

    def environment(self, op):
        self.mp.setenv('TOWER_OPERATION_ID', op['id'])
        self.mp.setenv('TOWER_OPERATION_DB', self.operations.path)
        for key in ('TOWER_BATTLE_ID', 'TOWER_BATTLE_GENERATION', 'TOWER_DEVICE_DELEGATION'):
            self.mp.delenv(key, raising=False)

    def view(self):
        rows = []
        for battle in sorted(self.store.list(limit=200), key=lambda b: b['created_at']):
            rows.append(dict(battle=self.label(battle['id']), origin=battle['origin'], previous=self.label(battle['previous_battle_id']),
                             status=battle['status'], reason=battle['reason'], outcome=battle['outcome'],
                             start_certainty=battle['start_certainty'], setup=(battle.get('runtime') or {}).get('setup', {}).get('detail'),
                             envelope_keys=sorted(battle['envelope']), preset_key=battle['envelope'].get('preset_key')))
        return rows

    def start(self, op, *, origin='started'):
        """BattleSession.from_environment exactly as the orchestrator calls it."""
        self.environment(op)
        from player.run_controls import effective_preset
        try:
            session = BattleSession.from_environment(effective_preset(self.cfg), self.cfg, origin=origin)
        except Exception as exc:                       # noqa: BLE001 - the refusal is the answer
            return None, dict(raised=type(exc).__name__, message=str(exc))
        return session, None


def subdirectory(tmp_path, name):
    path = tmp_path / name
    path.mkdir()
    return path


def no_operation_in_the_environment(tmp_path, monkeypatch):
    for key in ('TOWER_OPERATION_ID', 'TOWER_OPERATION_DB', 'TOWER_BATTLE_ID', 'TOWER_BATTLE_GENERATION'):
        monkeypatch.delenv(key, raising=False)
    return dict(session=BattleSession.from_environment({}, {}, origin='started'))


def first_battle(origin):
    def scenario(tmp_path, monkeypatch):
        world = SessionWorld(tmp_path, monkeypatch)
        session, refused = world.start(world.accept(), origin=origin)
        return dict(refused=refused, ledger=world.view(), session_origin=session.battle['origin'], previous=session.battle['previous_battle_id'])
    scenario.__name__ = f'first_battle_origin_{origin}'
    return scenario


def predecessor_of_an_ended_driver(tmp_path, monkeypatch):
    """The previous driver's operation ended while its record stayed live (an orphan): the successor finishes it `interrupted`
    (driver_operation_ended) and is created partial_adoption with previous_battle_id, whatever origin the caller passed."""
    out = {}
    for origin in ('started', 'adopted'):
        world = SessionWorld(subdirectory(tmp_path, origin), monkeypatch)
        old_op = world.accept(kind='gems')
        old = world.battle(old_op)
        world.end(old_op)
        session, refused = world.start(world.accept(), origin=origin)
        out[origin] = dict(refused=refused, ledger=world.view(), successor_previous=world.label(session.battle['previous_battle_id']),
                           old_is=world.label(old['id']))
    return out


def predecessor_already_reconciled(tmp_path, monkeypatch):
    """The dashboard's refresher finished the orphan first (driver_observation_lost): store.active() no longer returns it, so the successor is
    created unlinked - the race R3-02 names. The link is only ever found through active()."""
    world = SessionWorld(tmp_path, monkeypatch)
    old_op = world.accept(kind='gems')
    old = world.battle(old_op)
    world.end(old_op)
    world.store.finish(old['id'], old['generation'], reason='driver_observation_lost', status='interrupted',
                       evidence={'operation_status': 'succeeded'})                       # what control.battles.reconcile writes
    live = [world.label(row['id']) for row in world.store.active(world.context['device_key'])]
    session, refused = world.start(world.accept(), origin='started')
    return dict(refused=refused, ledger=world.view(), live_records_the_successor_could_find=live)


def predecessor_with_a_running_owner(tmp_path, monkeypatch):
    """A live record whose owner operation still runs is not taken over (here the same operation enters again, the only way a second driver can
    meet it: the ledger admits one operation per emulator): creating the successor is refused (Conflict) and the record is untouched."""
    world = SessionWorld(tmp_path, monkeypatch)
    op = world.accept()
    old = world.battle(op)
    session, refused = world.start(op, origin='started')
    return dict(refused=refused, session_is_none=session is None, ledger=world.view(), old_is=world.label(old['id']))


def accepted_envelope_is_used(tmp_path, monkeypatch):
    """A run accepted with a battle_envelope whose preset_key matches the run: the session installs the envelope's preset and config (the
    ledger record keeps preset_key); a different preset_key is ignored and the process's own preset is frozen instead."""
    out = {}
    for label, key in (('matching_preset_key', 'bp_farm'), ('other_preset_key', 'bp_other')):
        world = SessionWorld(subdirectory(tmp_path, label), monkeypatch)
        envelope_cfg = copy.deepcopy(world.cfg)
        envelope_cfg['presets']['bp_farm']['label'] = 'from the envelope'
        envelope = dict(preset_key=key, preset=copy.deepcopy(envelope_cfg['presets']['bp_farm']), config=envelope_cfg,
                        controls=dict(routines={'rewards': {'enabled': False}}), source=None, source_revision=None, profile=None)
        op = world.accept(request={'battle_envelope': envelope})
        session, refused = world.start(op)
        battle = session.battle
        out[label] = dict(refused=refused, preset_label=battle['envelope']['preset'].get('label'), preset_key=battle['envelope'].get('preset_key'),
                          gates=session.gates, envelope_keys=sorted(battle['envelope']))
    return out


SESSION = {fn.__name__: fn for fn in (
    no_operation_in_the_environment, first_battle('started'), first_battle('adopted'), predecessor_of_an_ended_driver,
    predecessor_already_reconciled, predecessor_with_a_running_owner, accepted_envelope_is_used)}


# ---------------------------------------------------------------- runtime/operation_worker.py
def worker_run_flag(tmp_path, monkeypatch):
    """The operation worker's bootstrap deletes the instance's stop_after_run flag for every operation of kind `run` (and only that kind, only
    that instance's flag), after the lease and before the target executes - whatever else the request carries."""
    import sys
    from runtime import device_lease, operation_worker
    rows = []
    cases = (('run', {}), ('run', {'preset': 'bp_farm'}), ('run', {'attach': {'mode': 'current_battle'}}), ('gems', {}), ('gems', {'harvest': True}))
    for number, (kind, request) in enumerate(cases):
        root = tmp_path / f'case-{number}'
        root.mkdir()
        profile = root / 'profiles' / 'mine.yaml'
        profile.parent.mkdir()
        profile.write_text('player: {}\n')
        cfg = dict(active_instance='main', active_profile='mine', instances={'main': {'serial': 'synthetic-worker'}})
        context = operations.context(root, cfg, {}, str(profile))
        store = Operations(root / 'ops.db')
        op, _ = store.accept(kind, context, request)
        flag = Path(store.path).parent / 'main' / 'stop_after_run'
        flag.parent.mkdir()
        flag.write_text('scheduler transition', encoding='utf-8')
        other = flag.parent.parent / 'another' / 'stop_after_run'
        other.parent.mkdir()
        other.write_text('other instance', encoding='utf-8')
        monkeypatch.setenv('TOWER_OPERATION_ID', op['id'])
        monkeypatch.setenv('TOWER_OPERATION_DB', store.path)
        seen = {}
        monkeypatch.setattr(device_lease, 'acquire', lambda serial, host, port, seen=seen: seen.setdefault('lease_before_flag_removal', flag.exists()))
        monkeypatch.setattr(operation_worker, 'restore_worker_context', lambda settings: None)
        monkeypatch.setattr(operation_worker.runpy, 'run_path', lambda path, run_name, seen=seen, store=store, op=op, flag=flag:
                            seen.update(flag_present_at_target=flag.exists(), status_at_target=store.get(op['id'])['status']))
        monkeypatch.setattr(sys, 'argv', ['operation_worker', 'fake_target.py'])
        operation_worker.main()
        rows.append(dict(kind=kind, request_keys=sorted(request), **seen, flag_after=flag.exists(), other_instance_flag_after=other.exists(),
                         final_status=store.get(op['id'])['status']))
    return dict(cases=rows)


WORKER = {'worker_run_flag': worker_run_flag}


# ---------------------------------------------------------------- player/readiness.requirements
LOADOUT = dict(global_preset='My GP 1', module_preset='Mods A', guardian_preset='Guard B', workshop_preset='Shop C', bot_preset='Bots D',
               cards='farm', cards_restore='idle', modules=['mod_a', ['mod_b', 'x']], modules_restore=['mod_c'])
READINESS_CONFIG = dict(active_instance='main', loadouts={'full': LOADOUT, 'cards_only': {'cards': 'farm'}},
                        instances={'main': {'rois': {'wall_bar': [1, 2, 3, 4]}}}, rois={})
REGISTRY = {'coin': {'label': 'Coin farm', 'templates': ['flow/coin_a.png', 'flow/coin_b.png'], 'runner': 'orchestrator.py'},
            'tournament': {'label': 'Tournament', 'templates': ['flow/tourney_a.png'], 'runner': 'orchestrator.py'},
            'shard': {'label': 'Shard farm', 'templates': ['flow/shard_a.png'], 'runner': 'flows/shard.py'},
            'cycle_quest': {'label': 'Quest cycle', 'templates': [], 'runner': 'flows/quest_cycle.py'},
            'uw_grant_quest': {'label': 'UW grant quest', 'templates': [], 'runner': 'flows/quest_uw.py'}}
GATHER_ON = dict(ad_gems=True, quests_8h=True, quest_rewards=True, guild=True, free_store_gems=True, guild_store=True)
GATHER_OFF = dict(ad_gems=False, quests_8h=False, quest_rewards=False, guild=False, free_store_gems=False, guild_store=False)
RULES = [{'when': {'kind': 'second_wind'}, 'do': {'kind': 'surrender_retry'}},
         {'when': {'fleet_mark': {'after_waves': 3}}, 'do': {'fire': {'button': 'nuke'}}},
         {'when': {'on': 'second_wind'}, 'do': {'kind': 'fire', 'button': 'demon_mode'}}, {'when': {'kind': 'wave_at_least', 'wave': 5}, 'do': {'kind': 'noop'}}]
ABILITIES = dict(dm_below=0.2, nuke_below=0.1, nuke_on_fleet={'after_waves': 1}, rescue_bar='wall', hold_until_second_wind=True)
BODIES = {
    'coin_minimal': dict(kind='coin', loadout=None, tier=14, gather=GATHER_OFF),
    'coin_default_gather': dict(kind='coin', loadout=None, tier=14),
    'coin_gather_everything': dict(kind='coin', loadout=None, tier=14, gather=GATHER_ON),
    'coin_full_loadout': dict(kind='coin', loadout='full', tier=14, gather=GATHER_OFF),
    'coin_cards_only_loadout': dict(kind='coin', loadout='cards_only', tier=14, gather=GATHER_OFF),
    'coin_shopping': dict(kind='coin', loadout=None, gather=GATHER_OFF,
                          shopping=[{'enabled': True, 'stats': ['damage', 'health']}, {'enabled': False, 'stats': ['range']}, {'stats': ['regen']}]),
    'coin_uw_wanted': dict(kind='coin', loadout=None, gather=GATHER_OFF, uw_wanted={'chain_lightning': True, 'death_wave': False}),
    'coin_abilities_and_rules': dict(kind='coin', loadout=None, gather=GATHER_OFF, abilities=ABILITIES, rules=RULES, cancel_sprint=True),
    'coin_rescue_bar_wall_without_roi': dict(kind='coin', loadout=None, gather=GATHER_OFF, abilities=dict(rescue_bar='wall')),
    'coin_max_wave': dict(kind='coin', loadout=None, gather=GATHER_OFF, max_wave=2000),
    'coin_surrender_retry_rule': dict(kind='coin', loadout=None, gather=GATHER_OFF, rules=[{'when': {'kind': 'second_wind'}, 'do': {'kind': 'surrender_retry'}}]),
    'coin_dissonant_restarting_via_home': dict(kind='coin', loadout=None, gather=GATHER_OFF, dissonant_tab='gold', restart_via_home=True),
    'coin_dissonant_not_restarting': dict(kind='coin', loadout=None, gather=GATHER_OFF, dissonant_tab='gold', restart_via_home=False),
    'coin_perk_bans': dict(kind='coin', loadout=None, gather=GATHER_OFF, perk_bans=['p1', 'p2']),
    'coin_perk_bans_empty_list': dict(kind='coin', loadout=None, gather=GATHER_OFF, perk_bans=[]),
    'coin_global_behaviours': dict(kind='coin', loadout=None, gather=GATHER_OFF,
                                   global_behaviors=dict(event_missions=True, demon_mode_always=True, free_store_gems=True, daily_missions=True, guild_progress=True)),
    'coin_everything': dict(kind='coin', loadout='full', tier=14, gather=GATHER_ON, shopping=[{'enabled': True, 'stats': ['damage']}], uw_wanted={'chain_lightning': True},
                            abilities=ABILITIES, rules=RULES, cancel_sprint=True, max_wave=2000, dissonant_tab='gold', restart_via_home=True, perk_bans=['p1'],
                            global_behaviors=dict(event_missions=True, demon_mode_always=True, daily_missions=True, guild_progress=True)),
    'tournament_setup': dict(kind='tournament', tournament_setup=True, loadout=None, gather=GATHER_OFF),
    'tournament_by_setup_flag_only': dict(tournament_setup=True, loadout=None, gather=GATHER_OFF),
    'tournament_with_rules': dict(kind='tournament', tournament_setup=True, loadout='full', gather=GATHER_OFF, abilities=ABILITIES, rules=RULES),
    'shard': dict(kind='shard', loadout=None, gather=GATHER_OFF),
    'quest_cycle': dict(kind='cycle_quest', loadout=None, gather=GATHER_OFF),
    'quest_uw_grant': dict(kind='uw_grant_quest', loadout=None, gather=GATHER_OFF),
    'kind_from_runner': dict(runner='flows/shard.py', loadout=None, gather=GATHER_OFF),
    'nothing_declared': {},
}


def requirements_matrix(monkeypatch):
    """readiness.requirements(cfg, body) over the synthetic matrix: the full output of every body, each row as one JSON line [alternatives, reasons, blocking].
    The flow registry and the perk templates are synthetic here (what they hold is not what is being recorded)."""
    import flows
    from interactions import perks
    monkeypatch.setattr(flows, 'flows', lambda: {kind: dict(spec, kind=kind) for kind, spec in REGISTRY.items()})
    monkeypatch.setattr(flows, 'flow', lambda kind: dict(REGISTRY.get(kind, {'label': kind, 'templates': []})))
    monkeypatch.setattr(perks, 'templates', lambda: ['perks/ban_tab.png', 'perks/toggle.png'])
    out = {}
    for name, body in BODIES.items():
        rows = readiness.requirements(copy.deepcopy(READINESS_CONFIG), copy.deepcopy(body))
        out[name] = dict(blocking=sum(1 for row in rows if row['blocking']), advisory=sum(1 for row in rows if not row['blocking']),
                         rows=[json.dumps([row['alternatives'], row['reasons'], row['blocking']], separators=(',', ':')) for row in rows], row_keys=sorted(rows[0]))
    return out


def requirements_do_not_mutate_their_input(monkeypatch):
    import flows
    monkeypatch.setattr(flows, 'flows', lambda: {kind: dict(spec, kind=kind) for kind, spec in REGISTRY.items()})
    monkeypatch.setattr(flows, 'flow', lambda kind: dict(REGISTRY.get(kind, {'label': kind, 'templates': []})))
    body, cfg = copy.deepcopy(BODIES['coin_everything']), copy.deepcopy(READINESS_CONFIG)
    before = json.dumps([body, cfg], sort_keys=True)
    readiness.requirements(cfg, body)
    return dict(unchanged=json.dumps([body, cfg], sort_keys=True) == before)


READINESS = {'requirements_matrix': lambda tmp_path, monkeypatch: requirements_matrix(monkeypatch),
             'requirements_do_not_mutate_their_input': lambda tmp_path, monkeypatch: requirements_do_not_mutate_their_input(monkeypatch)}


# ---------------------------------------------------------------- vision/screen: in_tournament and identify
IN_TOURNAMENT = 'tourney/in_tournament.png'
REGION = (1590, 1740, 450, 750)            # y0, y1, x0, x1 of the badge band screen.in_tournament searches


def noise(seed, shape):
    return np.random.default_rng(seed).integers(0, 255, shape, dtype=np.uint8)


def frame_with(template, at=(1620, 500)):
    image = noise(1, (2560, 1080, 3))
    if template is not None:
        y, x = at
        image[y:y + template.shape[0], x:x + template.shape[1]] = template
    return image


def calibration_dir(monkeypatch, tmp_path):
    """detect._tpl reads accounts.template_path: point it at a temporary directory and start with an empty template cache."""
    monkeypatch.setattr(accounts, 'template_path', lambda root, cfg, rel, write=False: tmp_path / 'templates' / rel)
    monkeypatch.setattr(detect, '_TPL_CACHE', {})
    return tmp_path / 'templates'


def readers(image):
    """What the two readers answer for a frame: in_tournament and the raw template score inside the badge band."""
    score, center = screen._match(image, IN_TOURNAMENT, ((REGION[0], REGION[1]), (REGION[2], REGION[3])))
    matching = score >= screen.OVERLAY_THRESH
    return dict(in_tournament=screen.in_tournament(image), score_is_one=round(score, 3) == 1.0, score_zero=score == 0.0, score_below_threshold=score < screen.OVERLAY_THRESH,
                center=list(center) if matching or score == 0.0 else 'not recorded: the best position of a poor match')


def in_tournament_readers(tmp_path, monkeypatch):
    """The trophy-badge reader with the template present and matching, present and not matching, missing, and unreadable (not an image): a
    missing or unreadable template scores 0, so the reader answers False - it cannot tell 'no tournament' from 'cannot tell'."""
    directory = calibration_dir(monkeypatch, tmp_path)
    template = noise(7, (60, 90, 3))
    path = directory / IN_TOURNAMENT
    path.parent.mkdir(parents=True)
    out = {'missing': readers(frame_with(template))}
    detect._TPL_CACHE.clear()
    path.write_bytes(b'not an image at all')
    out['unreadable'] = readers(frame_with(template))
    detect._TPL_CACHE.clear()
    assert cv2.imwrite(str(path), template)
    out['present_and_matching'] = readers(frame_with(template))
    out['present_matching_elsewhere_in_the_frame'] = readers(frame_with(template, at=(300, 100)))
    out['present_and_not_matching'] = readers(frame_with(None))
    out['threshold'] = screen.OVERLAY_THRESH
    out['band'] = list(REGION)
    return out


def identify_a_battle(tmp_path, monkeypatch):
    """screen.identify on a frame with a readable wave counter and no header: 'battle' with score 1.0 and the evidence of every template tried
    (all zero with no template cut); with the trophy badge matching too it is still 'battle' - identify does not consult the badge."""
    directory = calibration_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(wave_reader, 'read_wave', lambda image: 4321)
    plain = screen.identify(frame_with(None))
    template = noise(7, (60, 90, 3))
    path = directory / IN_TOURNAMENT
    path.parent.mkdir(parents=True)
    assert cv2.imwrite(str(path), template)
    badge = screen.identify(frame_with(template))
    monkeypatch.setattr(wave_reader, 'read_wave', lambda image: None)
    unreadable_wave = screen.identify(frame_with(None))

    def view(found):
        return dict(name=found.name, score=found.score, wave=found.wave, evidence=found.evidence, in_battle=found.in_battle)
    return dict(readable_wave=view(plain), readable_wave_with_the_badge=view(badge), unreadable_wave=view(unreadable_wave))


SCREEN = {'in_tournament_readers': in_tournament_readers, 'identify_a_battle': identify_a_battle}
