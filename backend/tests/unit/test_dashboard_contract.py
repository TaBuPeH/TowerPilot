"""The Dashboard contract: what the baseline routes of backend/dashboard.py answer.

backend/tests/fixtures/dashboard_contract_v1.json is what the new Dashboard's strict
schemas, its fake backend and its tests bind to. It is GENERATED here by driving the real
Flask routes over synthetic state (a temporary root, ledger and lease directory, one fixed
clock, a process table, adb socket and process creation that are all in memory, see
dashboard_contract_world.py), never edited by hand and never read from the live backend,
device, lease, process table or a running dashboard. A normal run only compares: any change
in a field, a code or a status fails until a developer regenerates it on purpose and the
UI side re-reads it:

    TP_REGENERATE_DASHBOARD_CONTRACT=1 python -m pytest backend/tests/unit/test_dashboard_contract.py

TP_DASHBOARD_CONTRACT_OUT=<path> writes the generated contract to that path instead of
comparing (how a second tree, such as the r6 candidate, is generated; the committed fixture
is untouched).

Cases are keyed `<family>.<case>`. SPEC_10_3 maps each case the Dashboard spec (section
10.3, backend/logs/reviews/ui-dashboard-rebuild/dashboard-spec.md) requires to its table
row; a case the backend produced but 10.3 does not list is an `extra`. The harvest
families (status.harvest, /api/harvest, /api/automation-access, owner refusals, the Stop
contract, the runner kill, the start transfer) stay in harvest_status_v1.json; this contract
repeats only what 10.3 lists for POST /api/control (one transfer sample, the two kill answers
that mean Unknown) and the unknown-outcome writes the harvest fixture lacks.

Each case is {request: {method, path, body?}, status, body}. A JSON body is the route's
exact answer (keys sorted by Flask); a body that is not JSON (Flask's default 500 page,
which an unhandled exception produces in production) is {text}. Ids are aliased in order
of appearance and the scenario root is `<root>`."""

import paths as test_paths
from fixture_json import render_fixture_json
import contextlib
import copy
import json
import os
import re
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from conftest import join_reconnect_threads, seed_root, settings_restored
from control import battles as battle_api
from control import harvest, operations
from control.operations import inspect_worker as real_inspect_worker
from dashboard_contract_world import ADB_SERVER, ContractClock, FakeAdb, ProcessTable, Spawner, completed
from harvest_support import NOW
from player import readiness as readiness_api
from runtime import device_lease, runners
from towerpilot_workflows import battles as ledger_battles
from towerpilot_workflows import harvest as ledger_h
from towerpilot_workflows import operations as ledger_ops

REAL_READINESS_CHECK = readiness_api.check   # the world replaces readiness.check; the readiness cases put it back
FIXTURE = test_paths.FIXTURES / 'dashboard_contract_v1.json'
REGENERATE = 'TP_REGENERATE_DASHBOARD_CONTRACT'
OUTPUT = 'TP_DASHBOARD_CONTRACT_OUT'
PYW = 'C:/Python/pythonw.exe'
CREATED = 1_799_990_000.25      # the creation time of the first stand-in process; the next one is a second later


def answer(response, method, path, body=None):
    request = dict(method=method, path=path)
    if body is not None:
        request['body'] = body
    if response.is_json:
        return dict(request=request, status=response.status_code, body=response.get_json())
    return dict(request=request, status=response.status_code, body=dict(text=response.get_data(as_text=True)))


class World:
    """One scenario: a fresh root, ledger, leases, clock, process table, adb server and Flask client."""

    def __init__(self, gen, name, root, mp):
        self.gen, self.dash, self.name, self.root, self.mp = gen, gen.dash, name, root, mp
        self.clock = ContractClock(NOW)
        self.table = ProcessTable(mp, root)
        self.adb = FakeAdb(mp)
        self.spawn = Spawner(self.table, self.clock)
        self.client = self.dash.app.test_client()
        self.runs = []

    # -- recording
    def case(self, family, case, response, method, path, body=None):
        self.gen.cases.setdefault(family, {})[case] = answer(response, method, path, body)
        return response

    def get(self, family, case, path):
        return self.case(family, case, self.client.get(path), 'GET', path)

    def post(self, family, case, path, body=None, **kwargs):
        return self.case(family, case, self.client.post(path, json=body, **kwargs), 'POST', path, body)

    def plain_get(self, path):
        return self.client.get(path)

    def plain_post(self, path, body=None):
        return self.client.post(path, json=body)

    # -- the world's state
    @property
    def cfg(self):
        return self.dash.load_config()

    def edit_config(self, change):
        path = Path(self.root) / 'config.yaml'
        cfg = yaml.safe_load(path.read_text(encoding='utf-8'))
        change(cfg)
        path.write_text(yaml.safe_dump(cfg), encoding='utf-8')

    def edit_profile(self, change, name='mine'):
        path = Path(self.root) / 'profiles' / f'{name}.yaml'
        profile = yaml.safe_load(path.read_text(encoding='utf-8'))
        change(profile)
        path.write_text(yaml.safe_dump(profile), encoding='utf-8')

    def context(self, command=None):
        return operations.context(self.root, self.cfg, command or {}, self.dash._profile_path(self.cfg.get('active_profile')))

    @property
    def store(self):
        return operations.ledger(self.root)

    def advance(self, seconds):
        self.clock.now += seconds

    def worker(self, pid, runner_script, *, created=None, args=(), ppid=0):
        """A running worker process in the table, as Popen would have left it."""
        created = CREATED + len(self.table.rows) if created is None else created
        self.table.start(pid, created, [PYW, '-m', 'runtime.operation_worker', runner_script, '--instance', 'main', *args],
                         ppid=ppid, runner=True)
        return created

    def running(self, kind, request=None, *, pid=None, key=None, script=None, args=()):
        """An accepted operation moved to running with its worker recorded and present in the table."""
        request = request or {}
        op, _ = self.store.accept(kind, self.context(request), request, key or f'{self.name}-{kind}-{len(self.runs)}')
        pid = pid or 4400 + len(self.runs)
        self.runs.append(op['id'])
        script = script or {'run': 'orchestrator.py', 'gems': 'gem_collector.py'}.get(kind, f'{kind}.py')
        created = self.worker(pid, script, args=args)
        return self.store.transition(op['id'], op['revision'], 'running', worker={'pid': pid, 'creation_time': created})


class Generator:
    """Builds the contract: every case of every family, in one deterministic order."""

    def __init__(self, dash, base, monkeypatch):
        self.dash, self.base, self.root_monkeypatch = dash, Path(base), monkeypatch
        self.cases = {}
        self.counter = 0

    def uuid4(self):
        """Ids are minted from one counter, so every id of the contract is the same on every run."""
        import uuid
        self.counter += 1
        return uuid.UUID(int=((0x7A000000 | self.counter) << 96) | 0x5EED, version=4)

    @contextlib.contextmanager
    def world(self, name, *, taps=True, adb=False):
        root = self.base / name
        seed_root(root)
        (root / 'logs').mkdir(parents=True, exist_ok=True)
        (root / 'logs' / 'setup_done').write_text('seeded\n', encoding='utf-8')
        if not taps:
            cfg = yaml.safe_load((root / 'config.yaml').read_text(encoding='utf-8'))
            cfg['instances']['main']['allow_taps'] = False
            (root / 'config.yaml').write_text(yaml.safe_dump(cfg), encoding='utf-8')
        if adb:
            cfg = yaml.safe_load((root / 'config.yaml').read_text(encoding='utf-8'))
            cfg['adb'] = {'exe': 'adb'}
            (root / 'config.yaml').write_text(yaml.safe_dump(cfg), encoding='utf-8')
        dash = self.dash
        # a route the contract drives may rewrite the process-wide settings singleton on purpose (a saved loadout assigns
        # CONFIG['loadouts']): each world hands it back, so no scenario sees another's and the test leaves the next one the
        # shipped table (conftest.settings_restored)
        with settings_restored(), pytest.MonkeyPatch.context() as mp:
            import random
            import uuid
            from device import adbclient
            from flows import shard
            from interactions import ad_gems, shopper
            from player import readiness
            from control import schedule_status
            from runtime import battle_session, logger
            world = World(self, name, root, mp)
            for attribute, value in (('ROOT', str(root)), ('CONFIG_PATH', str(root / 'config.yaml')),
                                     ('CONFIG_EXAMPLE', str(root / 'config.example.yaml')),
                                     ('_WIZ_STATE', str(root / 'logs' / 'wizard_state.json')),
                                     ('_SETUP_FLAG', str(root / 'logs' / 'setup_done')),
                                     ('_CONN_CACHE', {'t': 0.0, 'serial': None, 'device_state': 'unknown', 'port_open': False}),
                                     ('_RECONNECT', {'state': 'idle', 'attempts': 0, 'next_at': 0.0, 'last_error': '',
                                                     'serial': None, 'down': False, 'flipped': False, 'thread': None}),
                                     ('_PROCS_CACHE', {'at': 0.0, 'procs': [], 'attempted_at': None, 'error': None}),
                                     ('_PROCS_THREAD', None),
                                     ('_CALIB_CACHE', {'t': 0.0, 'missing': []}),
                                     ('_SETUP_CACHE', {'done': False, 'probe_failed_at': 0.0}),
                                     ('_DISPLAY_CACHE', {}),
                                     ('_HARVEST_SOURCES', {}),
                                     ('_ACTION_LOCK', threading.Lock()),
                                     ('_TRANSFER_WAIT_SEC', 0.3)):
                mp.setattr(dash, attribute, value)
            mp.setattr(adbclient, '_HOST', ADB_SERVER)
            mp.setattr(adbclient, '_offline_since', {})
            mp.setattr(device_lease, '_ROOT', root / 'leases')
            for module in (ledger_ops, ledger_battles, ledger_h, battle_api, dash, schedule_status, device_lease,
                           battle_session, shard, ad_gems, shopper):
                mp.setattr(module, 'time', world.clock)
            mp.setattr(uuid, 'uuid4', self.uuid4)
            mp.setattr(logger, 'event', lambda *args, **kwargs: None)   # driver events are not part of the contract
            random_state = random.getstate()
            random.seed(name)
            mp.setattr(harvest, 'time', world.clock)
            real_wait = harvest.wait_for_release   # the transfer's wait for the lease: on the fixed clock, never real time
            mp.setattr(harvest, 'wait_for_release',
                       lambda *args, **kwargs: real_wait(*args, sleep=world.clock.sleep, clock=world.clock.monotonic, **kwargs))
            mp.setattr(operations, 'inspect_worker', real_inspect_worker)
            mp.setattr(runners, 'other_runners', lambda target=None: world.table.runners())
            mp.setattr(dash.subprocess, 'Popen', world.spawn)
            mp.setattr(dash, '_run', lambda args, **kw: completed(stdout='connected to ' + str(args[-1])))
            mp.setattr(readiness, 'check', lambda root_, cfg, preset: {'ready': True, 'missing': []})
            try:
                yield world
            finally:
                join_reconnect_threads()   # still inside this world's patches: _HOST and FakeAdb hold
                random.setstate(random_state)


# ----------------------------------------------------------- sealed setup evidence
# The same builders test_dashboard_admission.py uses: one calibrate operation on the device,
# its state sealed like runtime.operations._retain_artifact (operations/<id>/, sha256).
def record(w, state=None, *, status='failed', request=None):
    import hashlib
    context = w.context()
    op, _ = w.store.accept('calibrate', context, request or {'bootstrap': True, 'flows': True, 'phases': 'bootstrap'})
    op = w.store.transition(op['id'], op['revision'], 'running')
    if status == 'running':
        return op
    refs = {}
    if state is not None:
        raw = state if isinstance(state, bytes) else json.dumps(state(op['id'])).encode()
        path = Path(context['artifact_dir']) / 'operations' / op['id'] / 'calibrate_state.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        refs['calibrate_state.json'] = {'path': str(path), 'revision': hashlib.sha256(raw).hexdigest()}
    return w.store.transition(op['id'], op['revision'], status, result_refs=refs)


def unresolved(op_id):
    return {'operation_id': op_id,
            'setup_battle': {'id': 'battle-' + op_id[:6], 'status': 'unknown', 'operation_id': op_id,
                             'started_at': 1.0, 'reason': "No fresh matching control for 'open intro sprint dialog'"},
            'final_screen': {'name': 'unknown', 'verified': False, 'operation_id': op_id}}


ATTEST = 'new_manual_battle'
HANDOFF = '/api/setup-battle/collection-handoff'


def handoff_body(blocker, **changes):
    body = dict(operation_id=blocker['operation_id'], setup_battle_id=blocker['setup_battle_id'],
                evidence_revision=blocker['evidence_revision'], attestation=ATTEST,
                expected_context=dict(instance='main', account=None, serial='fake:1'))
    body.update(changes)
    return body


# ------------------------------------------------------------------- GET /api/status
OMITTED = ('harvest', 'runtime')   # harvest is its own fixture; runtime is the build identity of the machine


def status_case(w, case, note=None):
    """GET /api/status as the Dashboard reads it: every root member except harvest (its own fixture)
    and runtime (build and port identity of the machine, not a contract of the page)."""
    response = w.client.get('/api/status')
    record_ = answer(response, 'GET', '/api/status')
    assert response.status_code == 200, response.get_data(as_text=True)
    record_['body'] = {key: value for key, value in record_['body'].items() if key not in OMITTED}
    w.gen.cases.setdefault('status', {})[case] = record_
    return record_['body']


def freeze_reconnect(w, **fields):
    """Pin the auto-reconnect bookkeeping a status poll would otherwise advance, with the recovery
    thread 'in flight' so the tick returns at once and nothing is started."""
    alive = SimpleNamespace(is_alive=lambda: True)
    state = {'state': 'idle', 'attempts': 0, 'next_at': 0.0, 'last_error': '', 'serial': 'fake:1', 'down': False,
             'flipped': False, 'thread': alive}
    state.update(fields)
    w.mp.setattr(w.dash, '_RECONNECT', state)


def status_cases(gen):
    with gen.world('status_processes_empty') as w:
        status_case(w, 'processes_empty')
    with gen.world('status_processes_linked') as w:
        collector = w.running('gems', {'preset': 'bp_farm'}, args=['--preset', 'bp_farm'])
        status_case(w, 'processes_linked_collector')
    with gen.world('status_processes_untracked') as w:
        w.worker(4501, 'orchestrator.py', args=['--preset', 'bp_farm'])
        w.table.cmdline_denied.add(4501)
        status_case(w, 'processes_untracked_created_null')
    with gen.world('status_processes_two') as w:
        w.worker(4511, 'orchestrator.py', args=['--preset', 'bp_farm'])
        w.worker(4512, 'orchestrator.py', args=['--preset', 'bp_other'])
        status_case(w, 'processes_two_rows_same_runner')
    with gen.world('status_refresh_stale') as w:
        w.worker(4521, 'orchestrator.py', args=['--preset', 'bp_farm'])
        w.dash._procs_refresh()
        w.advance(7.5)
        w.mp.setattr(w.dash, '_PROCS_THREAD', SimpleNamespace(is_alive=lambda: True))   # the refresher is stalled, not absent
        status_case(w, 'process_refresh_stale')
    with gen.world('status_refresh_error') as w:
        w.worker(4531, 'orchestrator.py', args=['--preset', 'bp_farm'])
        w.dash._procs_refresh()
        w.advance(3)

        def unreadable(target=None):
            raise OSError('the process table could not be read')
        w.mp.setattr(runners, 'other_runners', unreadable)
        w.dash._procs_refresh()
        w.mp.setattr(w.dash, '_PROCS_THREAD', SimpleNamespace(is_alive=lambda: True))
        status_case(w, 'process_refresh_error')


# ---------------------------------------------------------- config, accounts, wizard
def config_cases(gen):
    with gen.world('config_taps_true') as w:
        w.get('config', 'taps_true', '/api/config')
    with gen.world('config_taps_false', taps=False) as w:
        w.get('config', 'taps_false', '/api/config')
    with gen.world('config_taps_missing') as w:
        w.edit_config(lambda cfg: cfg.update(active_instance='other'))
        w.get('config', 'taps_missing', '/api/config')


def account_cases(gen):
    library = {'alpha': {'label': 'Alpha', 'active_profile': 'mine', 'loadouts': {}},
               'beta': {'label': 'Beta', 'active_profile': 'mine', 'loadouts': {}}}

    def accounts_of(account):
        def change(cfg):
            cfg['accounts'] = copy.deepcopy(library)
            cfg['instances']['main']['account'] = account
        return change
    with gen.world('accounts_active') as w:
        w.edit_config(accounts_of('alpha'))
        w.get('accounts', 'active_among_several', '/api/accounts')
    with gen.world('accounts_none_active') as w:
        w.edit_config(accounts_of(None))
        w.get('accounts', 'none_active', '/api/accounts')
    with gen.world('accounts_read_error') as w:
        w.edit_config(accounts_of('ghost'))   # the selected account is not in the library: the read fails
        w.get('accounts', 'read_error', '/api/accounts')


@contextlib.contextmanager
def emulator_world(gen, name, *, manager=True, run=None):
    """A wizard emulator scan over a machine whose install folders are temporary directories."""
    import psutil
    from types import SimpleNamespace as NS
    with gen.world(name, adb=True) as w:
        program_files, program_data = w.root / 'program-files', w.root / 'program-data'
        for variable, folder in (('ProgramFiles', program_files), ('ProgramFiles(x86)', w.root / 'program-files-x86'),
                                 ('ProgramData', program_data)):
            w.mp.setenv(variable, str(folder))
        if manager:
            nx = program_files / 'Netease' / 'MuMuPlayer' / 'nx_main'
            nx.mkdir(parents=True)
            (nx / 'MuMuManager.exe').write_bytes(b'MZ')
            (nx / 'adb.exe').write_bytes(b'MZ')
        w.mp.setattr(w.dash, '_run', run or (lambda args, **kw: completed(stdout='{}')))
        w.mp.setattr(psutil, 'process_iter', lambda attrs=None, ad_value=None: iter(
            [NS(info={'name': 'MuMuPlayer.exe', 'cmdline': []})] if manager else []))
        yield w


MUMU = {'0': {'index': 0, 'name': 'Main Tower', 'is_android_started': True, 'is_process_started': True, 'adb_port': 16384},
        '3': {'index': 3, 'name': 'Second Tower', 'is_android_started': False, 'is_process_started': False}}


def wizard_cases(gen):
    def info(args, **kw):
        return completed(stdout=json.dumps(MUMU))
    with emulator_world(gen, 'wizard_emulators_instances', run=info) as w:
        w.adb.state, w.adb.listing = 'missing', ['127.0.0.1:16384']
        w.get('wizard_emulators', 'instances_with_names_and_ports', '/api/wizard/emulators')
        w.get('wizard_state', 'after_scan', '/api/wizard/state')
    with emulator_world(gen, 'wizard_emulators_none', manager=False) as w:
        w.adb.server = ConnectionRefusedError
        w.get('wizard_emulators', 'none', '/api/wizard/emulators')
    with emulator_world(gen, 'wizard_emulators_error', run=lambda args, **kw: completed(stdout='this is not json')) as w:
        w.adb.state = 'missing'
        w.get('wizard_emulators', 'scan_error', '/api/wizard/emulators')
    with gen.world('wizard_tools_missing') as w:
        w.get('wizard_tools', 'not_installed', '/api/wizard/tools')
        w.get('wizard_state', 'empty', '/api/wizard/state')
    with gen.world('wizard_tools_installed') as w:
        from device import tool_install
        folder = Path(w.root) / 'tools' / 'platform-tools'
        folder.mkdir(parents=True)
        for filename in tool_install.FILES:
            (folder / filename).write_bytes(b'MZ')
        w.get('wizard_tools', 'installed', '/api/wizard/tools')
    with gen.world('wizard_state_sections') as w:
        w.dash._wiz_save('emulators', {'scanned': [], 'running': [], 'configured': [{'instance': 'main', 'serial': 'fake:1', 'port_open': True}],
                                       'instances': [], 'suggestion': None, 'config_serial_placeholder': False,
                                       'active_instance': 'main'})
        w.dash._wiz_save('adopt', {'serial': 'fake:1', 'adb': 'adb', 'instance': 'main', 'backup': 'config.yaml.bak',
                                   'connected': True, 'message': 'connected to fake:1'})
        w.dash._wiz_save('resolution', {'ok': True, 'serial': 'fake:1', 'width': 1080, 'height': 2560, 'expected': True,
                                        'reference': True, 'note': 'Native display detected. Scanning applies its layout '
                                                                   'profile and verifies controls.'})
        w.get('wizard_state', 'all_sections', '/api/wizard/state')

# -------------------------------------------------- runs, readiness, run controls
IMAGE_ROW = {'alternatives': ['buttons/exit_battle.png'], 'reasons': ['Screen recognition and safe recovery'], 'blocking': True}
CONFIG_ROW = {'alternatives': ['config: rois.wall_bar'],
              'reasons': ['Wall bar region (Full setup detects it from the battle HUD)'], 'blocking': True}
END_ROUND_ROW = {'alternatives': ['home/end_round_yes.png'], 'reasons': ['Screen recognition and safe recovery'],
                 'blocking': True}
SECOND_WIND_ROW = {'alternatives': ['floaters/second_wind.png'], 'reasons': ['Screen recognition and safe recovery'],
                   'blocking': True}


def requirements(w, groups):
    """Replace only the list of images a run needs (so a case names its rows and the response stays small).
    The check itself, the files it looks for, the rows' cards and the projection stay real. A value that is
    an exception is raised for that run (the caught branch of /api/runs)."""
    from player import readiness

    def stub(cfg, body):
        outcome = groups.get(body.get('label'), [])
        if isinstance(outcome, Exception):
            raise outcome
        return copy.deepcopy(outcome)
    w.mp.setattr(readiness, 'requirements', stub)


def put_image(w, rel):
    import cv2
    import numpy as np
    from player import accounts
    path = accounts.template_path(w.root, w.cfg, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    assert cv2.imwrite(str(path), np.full((8, 6), 90, np.uint8))


def real_readiness(w):
    """Undo the world's compact readiness.check stand-in: the real check runs over the scenario's files."""
    w.mp.setattr(readiness_api, 'check', REAL_READINESS_CHECK)


PRESETS = {'farm': {'label': 'Farm', 'runner': 'orchestrator.py', 'tier': 1},
           'quest_chronofield': {'label': 'Quest: Chrono Field', 'defined': False},
           'combo': {'label': 'Combo (day plan)', 'runner': 'scheduling/combo.py', 'tier': 1}}


def run_cases(gen):
    with gen.world('runs_defined_undefined') as w:
        real_readiness(w)
        w.edit_config(lambda cfg: cfg.update(active_profile=None, presets=copy.deepcopy(PRESETS)))
        requirements(w, {'Farm': [], 'Quest: Chrono Field': [IMAGE_ROW]})
        w.get('runs', 'defined_and_undefined_with_labels', '/api/runs')
    with gen.world('runs_ready') as w:
        real_readiness(w)
        requirements(w, {})
        w.get('runs', 'ready', '/api/runs')
    with gen.world('runs_image_row') as w:
        real_readiness(w)
        w.edit_profile(lambda profile: profile['blueprints'].update(other=copy.deepcopy(profile['blueprints']['farm'])))
        requirements(w, {'Farm': [IMAGE_ROW]})
        w.get('runs', 'not_ready_image_row', '/api/runs')
    with gen.world('runs_config_row') as w:
        real_readiness(w)
        requirements(w, {'Farm': [CONFIG_ROW]})
        w.get('runs', 'not_ready_config_row', '/api/runs')
    with gen.world('runs_modules_pending') as w:
        real_readiness(w)
        requirements(w, {})
        from player import accounts
        folder = accounts.calibration_dir(w.root, w.cfg)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'module_restore.json').write_text('{}', encoding='utf-8')
        w.get('runs', 'not_ready_module_restoration', '/api/runs')
    with gen.world('runs_check_raised') as w:
        real_readiness(w)
        requirements(w, {'Farm': ValueError('the run could not be checked')})
        w.get('runs', 'readiness_check_raised', '/api/runs')
    with gen.world('runs_setup_required_edit') as w:
        real_readiness(w)
        w.edit_profile(lambda profile: profile['blueprints']['farm'].update(loadout='nope'))
        w.get('runs', 'setup_required_edit_run', '/api/runs')
    with gen.world('runs_setup_required_apply') as w:
        real_readiness(w)
        w.edit_profile(lambda profile: profile['blueprints']['farm'].update(loadout='nope'))
        from player import accounts
        folder = accounts.calibration_dir(w.root, w.cfg)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'calibrate_state.json').write_text(json.dumps({'player': {'card_presets': {'1': 'Damage'}}}), encoding='utf-8')
        w.get('runs', 'setup_required_apply_scan', '/api/runs')
    with gen.world('runs_setup_required_battle_setup') as w:
        real_readiness(w)
        w.edit_profile(lambda profile: profile['blueprints']['farm'].update(abilities={'bogus': 1}))
        w.get('runs', 'setup_required_battle_setup', '/api/runs')
    with gen.world('runs_combo') as w:
        real_readiness(w)

        def with_plan(profile):
            profile['plan'] = {'week': {'default': 'normal'}, 'days': {'normal': [{'blueprint': 'farm', 'block': 'coin'}]}}
        w.edit_profile(with_plan)
        w.edit_config(lambda cfg: cfg.update(presets={'combo': copy.deepcopy(PRESETS['combo'])}))
        requirements(w, {'Farm': [IMAGE_ROW]})
        w.get('runs', 'combo_entry', '/api/runs')


def readiness_cases(gen):
    with gen.world('readiness_ready') as w:
        real_readiness(w)
        requirements(w, {'Farm': [IMAGE_ROW]})
        put_image(w, 'buttons/exit_battle.png')
        w.get('readiness', 'ready_with_context', '/api/readiness?preset=bp_farm')
    with gen.world('readiness_end_round') as w:
        real_readiness(w)
        requirements(w, {'Farm': [END_ROUND_ROW]})
        w.get('readiness', 'not_ready_with_message_and_next_action', '/api/readiness?preset=bp_farm')
    with gen.world('readiness_second_wind') as w:
        real_readiness(w)
        requirements(w, {'Farm': [SECOND_WIND_ROW]})
        w.get('readiness', 'not_ready_second_wind', '/api/readiness?preset=bp_farm')
    with gen.world('readiness_plain') as w:
        real_readiness(w)
        requirements(w, {'Farm': [IMAGE_ROW, CONFIG_ROW]})
        w.get('readiness', 'not_ready_without_message', '/api/readiness?preset=bp_farm')
    with gen.world('readiness_setup_required') as w:
        real_readiness(w)
        w.edit_profile(lambda profile: profile['blueprints']['farm'].update(loadout='nope'))
        w.get('readiness', 'setup_required', '/api/readiness?preset=bp_farm')
    with gen.world('readiness_other_context') as w:
        real_readiness(w)
        requirements(w, {'Farm': []})
        w.edit_config(lambda cfg: cfg.update(active_instance='other'))   # the page was composed for emulator main
        w.get('readiness', 'context_other_than_the_page', '/api/readiness?preset=bp_farm')
    with gen.world('readiness_unknown') as w:
        real_readiness(w)
        w.get('readiness', 'unknown_preset', '/api/readiness?preset=bp_missing')


def run_controls_cases(gen):
    with gen.world('controls_ad_on') as w:
        w.get('run_controls', 'ad_gems_on_floating_off', '/api/run-controls?blueprint=farm')
    with gen.world('controls_both_off') as w:
        w.edit_profile(lambda profile: profile['policies']['gather']['shared'].update(ad_gems=False))
        w.get('run_controls', 'both_off', '/api/run-controls?blueprint=farm')
        w.get('run_controls', 'unknown_blueprint', '/api/run-controls?blueprint=nope')
        w.get('run_controls', 'combo_is_no_blueprint', '/api/run-controls?blueprint=combo')

# ------------------------------------------------------ calibration and the scheduler
def calibrate_cases(gen):
    with gen.world('calibrate_idle') as w:
        w.get('calibrate_status', 'idle', '/api/calibrate/status')
    with gen.world('calibrate_running') as w:
        op = record(w, status='running')
        folder = Path(op['context']['artifact_dir'])
        folder.mkdir(parents=True, exist_ok=True)
        now = w.clock.now
        state = {'operation_id': op['id'],
                 'phases': {'bootstrap': {'status': 'done', 'started_at': now - 120, 'updated_at': now - 90},
                            'cards': {'status': 'running', 'started_at': now - 90, 'updated_at': now - 5,
                                      'message': 'Cards: page 2 read; 8 cards identified; scrolling down'},
                            'modules': {'status': 'pending'}},
                 'activity': {'kind': 'calibrate_progress', 'phase': 'cards', 't': now - 5, 'operation_id': op['id'],
                              'message': 'Cards: page 2 read; 8 cards identified; scrolling down'}}
        (folder / 'calibrate_state.json').write_text(json.dumps(state), encoding='utf-8')
        (folder / 'calibrate_report.json').write_text(json.dumps({'operation_id': op['id'], 'entries': []}), encoding='utf-8')
        w.get('calibrate_status', 'running_with_steps', '/api/calibrate/status')
    with gen.world('calibrate_failed_setup') as w:
        record(w, unresolved)
        w.get('calibrate_status', 'failed_setup_unverified_final_screen', '/api/calibrate/status')
    with gen.world('calibrate_restore_pending') as w:
        from player import accounts
        folder = accounts.calibration_dir(w.root, w.cfg)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'module_restore.json').write_text('{}', encoding='utf-8')
        w.get('calibrate_status', 'restore_pending', '/api/calibrate/status')


def plan_block(uid, kind, start, end, count=None):
    return dict(id=uid, block=uid, blueprint=uid, preset='bp_' + uid, kind=kind, after_min=start, until_min=end, count=count)


@contextlib.contextmanager
def scheduler_world(gen, name, *, week, observe=True):
    """The real scheduler (scheduling.combo.next_block) publishing its decision into a running combo
    operation's progress, as the combo driver does every tick."""
    import datetime
    from scheduling import combo, daystate
    with gen.world(name) as w:
        blocks = [plan_block('farm', 'coin', 0, 600), plan_block('shards', 'shard', 600, 1440, 10)]
        cfg = dict(active_profile='mine', plan={'week': {day: copy.deepcopy(blocks) for day in week}} if week is not None else None,
                   presets={'bp_farm': {'label': 'Farm'}, 'bp_shards': {'label': 'Shards'}})
        w.mp.setattr(combo, 'CONFIG', cfg)
        w.mp.setattr(combo, '_plan_unavailable_reason', None)
        w.mp.setattr(combo, '_plan_absent_logged', False)
        w.mp.setattr(combo, '_block_refusals', set())
        w.mp.setattr(daystate, 'STATE', str(w.root / 'daily.json'))
        op = w.running('run', {'preset': 'combo'}, args=[])
        w.mp.setenv('TOWER_OPERATION_ID', op['id'])
        w.mp.setenv('TOWER_OPERATION_DB', str(w.store.path))
        w.op = op
        if observe:
            combo.next_block(datetime.datetime.fromtimestamp(NOW, tz=datetime.timezone.utc))   # Friday 08:00 UTC
        yield w


def schedule_cases(gen):
    path = '/api/schedule/status?instance=main'
    with scheduler_world(gen, 'schedule_available', week=['friday']) as w:
        w.get('schedule_status', 'available_with_winner_and_boundary', path)
    with scheduler_world(gen, 'schedule_no_winner', week=['saturday']) as w:
        w.get('schedule_status', 'available_no_winner', path)
    with scheduler_world(gen, 'schedule_not_observed', week=['friday'], observe=False) as w:
        w.get('schedule_status', 'scheduler_not_observed', path)
    with scheduler_world(gen, 'schedule_not_running', week=['friday']) as w:
        w.store.transition(w.op['id'], w.store.get(w.op['id'])['revision'], 'cancelling', step='stop_requested')
        w.get('schedule_status', 'scheduler_not_running', path)
    with scheduler_world(gen, 'schedule_worker_unavailable', week=['friday']) as w:
        w.table.exit(w.store.get(w.op['id'])['worker']['pid'])
        w.get('schedule_status', 'scheduler_worker_unavailable', path)
    with scheduler_world(gen, 'schedule_stale', week=['friday']) as w:
        w.advance(120)
        w.get('schedule_status', 'scheduler_observation_stale', path)
    with scheduler_world(gen, 'schedule_plan_unavailable', week=None) as w:
        w.get('schedule_status', 'plan_unavailable', path)
    with scheduler_world(gen, 'schedule_unknown_emulator', week=['friday']) as w:
        w.get('schedule_status', 'unknown_emulator', '/api/schedule/status?instance=nope')

# ------------------------------------------------------------------------ battles
PLAN_V2 = {'version': 2, 'enabled': True, 'interval_sec': 30, 'cash_reserve': 500,
           'queue': [{'id': 'damage', 'enabled': True, 'tap_limit': 3},
                     {'id': 'health', 'enabled': True, 'tap_limit': 2, 'from_wave': 5}]}
PLAN_LEGACY = [{'enabled': True, 'tab': 'attack', 'stats': ['damage'], 'mode': 'once'}]


def with_shopping(plan):
    def change(profile):
        profile['policies'].setdefault('shopping_lists', {})['main_list'] = copy.deepcopy(plan)
        profile['blueprints']['farm']['shopping'] = 'main_list'
    return change


def with_orbit(profile):
    profile['policies']['gather']['shared']['gem_orbit'] = {'enabled': True}


class Driver:
    """A run driver as the ledger sees it: the real BattleSession over the real GemWatch and Shopper
    collectors (no frames, no input), launched through the real POST /api/control start, so the
    envelope, capabilities, runtime and command states are the ones the code writes. The collectors'
    outcomes are the dictionaries their own methods build."""

    def __init__(self, w, key='driver', *, wave=123, origin='adopted', previous=None, op=None, adopt=False):
        import random
        from flows.shard import GemWatch
        from interactions.shopper import Shopper
        from player.run_controls import resolve
        from runtime.battle_session import BattleSession
        self.w = w
        if op is None:
            reply = w.plain_post('/api/control', dict(action='start', preset='bp_farm', idempotency_key=key))
            assert reply.status_code == 200, reply.get_json()
            op = w.store.get(reply.get_json()['operation_id'])
            op = w.store.transition(op['id'], op['revision'], 'running')   # the worker's own first transition
        self.op = op
        envelope = copy.deepcopy(op['request']['battle_envelope'])
        preset = envelope['preset']
        random.seed(f'{w.name}-{key}')
        if adopt:   # the real adoption: BattleSession.from_environment finishes the battle of a driver that is gone
            w.mp.setenv('TOWER_OPERATION_ID', op['id'])
            w.mp.setenv('TOWER_OPERATION_DB', str(w.store.path))
            self.session = BattleSession.from_environment(preset, envelope['config'], origin=origin)
        else:
            self.session = BattleSession(battle_api.ledger(w.root), op['id'], preset, envelope['config'],
                                         envelope=envelope, origin=origin, previous_battle_id=previous)
        # Resolve the same initial gates as orchestrator.RunState; a run without
        # a shopping plan must not publish an invented enabled shopper.
        live = resolve(preset, envelope['config'])['routines']
        self.rs = SimpleNamespace(gems=GemWatch(preset['gather']), shop=Shopper(preset),
                                  mission=SimpleNamespace(active=False), shopping_enabled=live['shopping']['enabled'],
                                  rewards_enabled=live['rewards']['enabled'], repeat_enabled=live['repeat']['enabled'], tracker=SimpleNamespace(last=wave),
                                  last_shop=w.clock.monotonic() - 10)
        self.wave = wave

    @property
    def store(self):
        return self.session.store

    @property
    def battle(self):
        return self.store.get(self.session.battle['id'])

    @property
    def generation(self):
        return self.session.generation

    def observe(self, wave=None):
        self.rs.tracker.last = self.wave = self.wave if wave is None else wave
        self.session.observe(self.rs, self.wave)

    def publish(self, visible=True):
        self.w.advance(2)                    # the driver publishes at most once a second
        self.session.publish(self.rs, battle_visible=visible)

    def checkpoint(self, visible=True):
        self.w.advance(2)
        self.session.checkpoint(self.rs, battle_visible=visible)

    def end(self, reason='observed_results', outcome='death', **evidence):
        return self.session.finish(reason, outcome=outcome, evidence=evidence or {'last_observed_wave': self.wave})

    def controls(self, patch, key, revision=None):
        battle = self.battle
        return self.store.request_controls(battle['id'], self.op['id'], self.generation,
                                           battle['control_revision'] if revision is None else revision, patch, key)

    def action(self, action, key):
        return self.store.request_action(self.battle['id'], self.op['id'], self.generation, action, key,
                                         expected_revision=self.battle['control_revision'])

    def ack(self, command, **fields):
        return self.store.acknowledge(self.battle['id'], self.generation, command['id'], **fields)

    # collector outcomes, as their own methods record them
    def ad_outcome(self, confirmed):
        ad = self.rs.gems.ad_gems
        ad.window_id, ad.offer_id, ad.claim_attempt_id = (f'{self.w.name}-window', f'{self.w.name}-offer', f'{self.w.name}-claim')
        ad._attempt_started_at = self.w.clock.monotonic() - 2
        ad._finish_attempt('confirmed' if confirmed else 'not_confirmed',
                           'offer_disappeared' if confirmed else 'offer_still_visible')

    def orbit_outcome(self, confirmed):
        orbit = self.rs.gems.orbit
        orbit.last_outcome = dict(confirmed=confirmed, reason='balance_increased' if confirmed else 'attempt_timeout',
                                  observed_at=self.w.clock.time(), window_id=f'{self.w.name}-orbit-window',
                                  attempt_id=f'{self.w.name}-orbit-attempt')

    def go_live(self, visible=True):
        self.observe()
        self.publish(visible)
        return self


def active(w, case):
    return w.get('battles_active', case, '/api/battles/active')


def history(w, case, query='?limit=3&instance=main'):
    return w.get('battles_history', case, '/api/battles' + query)


def detail(w, case, battle_id):
    return w.get('battle_detail', case, f'/api/battles/{battle_id}')

def battle_active_cases(gen):
    with gen.world('battles_none') as w:
        active(w, 'null_no_driver')
        history(w, 'empty')
    with gen.world('battles_prepared') as w:
        Driver(w)
        active(w, 'prepared')
    with gen.world('battles_runtime') as w:
        w.edit_profile(with_orbit)
        w.edit_profile(with_shopping(PLAN_V2))
        d = Driver(w)
        d.observe()
        ad = d.rs.gems.ad_gems
        ad.window_started_at, ad.window_deadline = w.clock.monotonic() - 5, w.clock.monotonic() + 20   # a window is open
        d.ad_outcome(True)
        d.orbit_outcome(False)
        d.publish()
        active(w, 'runtime_ad_orbit_shopping')
        w.advance(200)
        active(w, 'stale_report')
        d.publish(visible=False)
        active(w, 'waiting_for_game_state_blocker')
    with gen.world('battles_legacy_plan') as w:
        w.edit_profile(with_shopping(PLAN_LEGACY))
        Driver(w).go_live()
        active(w, 'legacy_shopping_plan')
    with gen.world('battles_commands') as w:
        # One battle, both kinds of command, every state the ledger writes. The controls move through the
        # driver's real checkpoint (its own applies and acknowledgements); an action's progress is the
        # driver's own acknowledgement call (started, completed, failed...), made directly because
        # collecting a gem needs frames.
        d = Driver(w).go_live()
        d.controls({'routines': {'ad_gems': {'enabled': False}}}, 'ctl-1')
        first = d.action('ad_gems', 'act-1')
        active(w, 'commands_controls_requested_action_requested')
        d.rs.mission.active = True                              # the rewards flow owns the screen
        d.checkpoint()                                          # ctl-1 applies; act-1 waits (the screen is owned)
        d.controls({'routines': {'rewards': {'enabled': False}}}, 'ctl-2')
        d.checkpoint()                                          # ctl-2 cannot apply its only path yet
        d.ack(first, state='started')
        active(w, 'commands_controls_pending_no_path_applied_action_started')
        d.rs.mission.active = False
        d.checkpoint()                                          # ctl-2 applies
        d.rs.mission.active = True
        d.controls({'routines': {'rewards': {'enabled': True}, 'floating_gems': {'enabled': True}}}, 'ctl-3')
        d.checkpoint()                                          # floating gems apply, rewards cannot yet
        d.ad_outcome(True)
        d.ack(first, state='completed', evidence=d.rs.gems.ad_gems.status()['last_outcome'])
        active(w, 'commands_controls_pending_some_paths_applied_action_completed')
        d.rs.mission.active = False
        d.checkpoint()
        second = d.action('floating_gems', 'act-2')
        d.ack(second, state='failed', reason='tap_refused', evidence={'confirmed': False, 'reason': 'tap_refused'})
        active(w, 'commands_controls_applied_with_overrides_action_failed')
        rejected = d.controls({'routines': {'ad_gems': {'interval_sec': 120}}}, 'ctl-4')
        d.ack(rejected, state='rejected', reason='driver_refused')
        third = d.action('ad_gems', 'act-3')
        d.ack(third, state='rejected', reason='battle_not_visible')
        active(w, 'commands_controls_rejected_action_rejected')
        expired = d.controls({'routines': {'ad_gems': {'window_sec': 20}}}, 'ctl-5')
        d.ack(expired, state='expired', reason='superseded')
        d.action('floating_gems', 'act-4')
        d.action('floating_gems', 'act-4-again')                # the same open action: the second key joins it
        active(w, 'commands_controls_expired_action_second_key_joined')
    with gen.world('battles_replaced_generation') as w:
        first = Driver(w, 'driver-one').go_live()
        w.table.exit(first.op['worker']['pid'])                    # its worker is gone; the next launch retires it
        second = Driver(w, 'driver-two', adopt=True)
        active(w, 'replaced_generation')

def battle_history_cases(gen):
    with gen.world('battles_closed_by_refresher') as w:
        # The driver's worker is gone. The refresher (never a GET) retires the operation and closes the
        # LEDGER record; the game is untouched, so nothing here ever ends a battle on screen.
        d = Driver(w).go_live()
        d.action('ad_gems', 'closure-action')
        d.controls({'routines': {'ad_gems': {'enabled': False}}}, 'closure-controls')
        record_id = d.battle['id']
        w.table.exit(d.op['worker']['pid'])
        w.dash._harvest_tick()
        active(w, 'null_after_reconcile_closed_record')
        detail(w, 'interrupted_commands_expired_battle_ended', record_id)
        history(w, 'interrupted_driver_observation_lost')
        detail(w, 'unknown_battle', 'no-such-battle')
        w.get('battle_detail', 'other_emulator', f'/api/battles/{record_id}?instance=other')
    with gen.world('battles_closed_by_driver') as w:
        d = Driver(w).go_live()
        d.action('floating_gems', 'ended-action')
        record_id = d.battle['id']
        d.end('observed_results', 'death', last_observed_wave=187, previous_wave=186)
        active(w, 'null_after_the_driver_ended_the_record')
        detail(w, 'ended_commands_expired_battle_ended', record_id)
        history(w, 'ended_with_final_wave')
    with gen.world('battles_history_mixed') as w:
        one = Driver(w, 'driver-one', wave=40).go_live()
        one.end('observed_results', 'death', last_observed_wave=187)
        history(w, 'ended_final_wave_only_row')
        one.session.prepare_next()                       # the same driver's next battle
        one.observe(41)
        one.publish()
        w.table.exit(one.op['worker']['pid'])
        two = Driver(w, 'driver-two', adopt=True, wave=77)   # adoption finishes the gone driver's battle: driver_operation_ended
        two.go_live()
        w.table.exit(two.op['worker']['pid'])
        w.dash._harvest_tick()                           # the refresher closes the next one: driver_observation_lost
        three = Driver(w, 'driver-three', adopt=True, wave=5)
        three.go_live()
        history(w, 'active_row_first_then_interrupted_rows')

# ------------------------------------------- the rest of GET /api/status
def device_status(gen, case, *, state='device', server=None, port=True, serial=None):
    with gen.world(f'status_device_{case}') as w:
        w.adb.state, w.adb.server, w.adb.port = state, server, port
        if serial is not None:
            w.edit_config(lambda cfg: cfg['instances']['main'].update(serial=serial))
        freeze_reconnect(w)
        status_case(w, f'device_state_{case}')


def more_status_cases(gen):
    with gen.world('status_guards_clear') as w:
        status_case(w, 'guards_clear')
    with gen.world('status_guards_unresolved') as w:
        record(w, unresolved)
        status_case(w, 'guards_setup_and_collection_blocking_the_same_operation')
    with gen.world('status_guards_handoff') as w:
        record(w, unresolved)
        blocker = w.dash._setup_battle_guard(w.cfg)
        assert w.plain_post(HANDOFF, handoff_body(blocker)).status_code == 200
        status_case(w, 'guards_setup_blocking_collection_released_by_handoff')
    with gen.world('status_guards_unreadable') as w:
        record(w, b'{this is not json')
        status_case(w, 'guards_records_unreadable')
    device_status(gen, 'device')
    device_status(gen, 'offline', state='offline')
    device_status(gen, 'unauthorized', state='unauthorized')
    device_status(gen, 'missing', state='missing', port=False)
    device_status(gen, 'no_server_connect_refused', server=ConnectionRefusedError, port=False)
    device_status(gen, 'no_server_connect_timed_out', server=TimeoutError, port=False)
    device_status(gen, 'unconfigured', serial='', port=False)
    device_status(gen, 'unknown', state='unknown')
    with gen.world('status_reconnect_idle') as w:
        status_case(w, 'reconnect_idle')
    with gen.world('status_reconnect_trying') as w:
        w.adb.server = ConnectionRefusedError
        w.mp.setattr(w.dash, '_RECONNECT', {'state': 'trying', 'attempts': 2, 'next_at': NOW + 30, 'last_error': 'device no_server after recovery',
                                            'serial': 'fake:1', 'down': True, 'flipped': False, 'thread': None})
        status_case(w, 'reconnect_trying_waiting_out_its_backoff')
    with gen.world('status_reconnect_running') as w:
        w.adb.server = ConnectionRefusedError
        freeze_reconnect(w, state='trying', attempts=1, down=True)
        status_case(w, 'reconnect_trying_attempt_in_flight')
    with gen.world('status_reconnect_gave_up') as w:
        w.adb.server = ConnectionRefusedError
        w.mp.setattr(w.dash, '_RECONNECT', {'state': 'gave_up', 'attempts': 10, 'next_at': NOW, 'last_error': 'device no_server after recovery',
                                            'serial': 'fake:1', 'down': True, 'flipped': False, 'thread': None})
        status_case(w, 'reconnect_gave_up')
    with gen.world('status_stop_flag') as w:
        w.running('run', {'preset': 'bp_farm'}, args=['--preset', 'bp_farm'])
        flag = Path(w.root) / 'logs' / 'main' / 'stop_after_run'
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text('stop after this run\n', encoding='utf-8')
        status_case(w, 'stop_flag_true_with_a_listed_run')
    with gen.world('status_other_instance') as w:
        w.edit_config(lambda cfg: cfg.update(active_instance='other'))
        status_case(w, 'instance_other')

# --------------------------------------------------------------- POST /api/control
def start_body(key, action='start', **extra):
    return dict(action=action, preset='bp_farm', idempotency_key=key, **extra)


def raising(error):
    def fail(*args, **kwargs):
        raise error
    return fail


def control_cases(gen):
    with gen.world('control_start') as w:
        w.post('control', 'start_accepted', '/api/control', start_body('start-1'))
        w.post('control', 'start_retry_answered_from_the_receipt', '/api/control', start_body('start-1'))
    with gen.world('control_start_transfer') as w:
        ident = harvest.identity_for(w.cfg)
        turn_on = dict(enabled=True, preset='bp_farm', expected_revision=0, idempotency_key='turn-on-1',
                       expected_context={name: ident[name] for name in ('instance', 'account', 'serial', 'profile')})
        assert w.plain_post('/api/harvest', turn_on).status_code == 200
        w.dash._harvest_tick()                                  # the lifecycle host launches the standalone collector
        [collector] = w.store.active(ident['device_key'])
        w.store.transition(collector['id'], collector['revision'], 'running')
        w.post('control', 'start_accepted_with_transfer', '/api/control', start_body('start-2'))
    with gen.world('control_start_refused_by_setup_guard') as w:
        record(w, unresolved)
        w.post('control', 'start_refused_by_setup_guard', '/api/control', start_body('start-3'))
    with gen.world('control_start_gems') as w:
        w.post('control', 'start_gems_accepted', '/api/control', start_body('gems-1', 'start_gems'))
        w.post('control', 'start_gems_retry_answered_from_the_receipt', '/api/control', start_body('gems-1', 'start_gems'))
    with gen.world('control_start_gems_refused') as w:
        record(w, unresolved)
        w.post('control', 'start_gems_refused_by_collection_guard', '/api/control', start_body('gems-2', 'start_gems'))
    with gen.world('control_context_changed') as w:
        changed = dict(expected_context=dict(instance='main', account=None, serial='fake:changed', rendering=None, profile='mine'))
        w.post('control', 'start_context_changed', '/api/control', start_body('start-4', **changed))
        w.post('control', 'start_gems_context_changed', '/api/control', start_body('gems-3', 'start_gems', **changed))
    with gen.world('control_clear_flag') as w:
        flag = Path(w.root) / 'logs' / 'main' / 'stop_after_run'
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text('stop after this run\n', encoding='utf-8')
        w.post('control', 'clear_flag_accepted', '/api/control', dict(action='clear_flag'))
        assert not flag.exists()
        op = w.running('run', {'preset': 'bp_farm'}, args=['--preset', 'bp_farm'])
        w.post('control', 'clear_flag_refused_for_a_changed_revision', '/api/control',
               dict(action='clear_flag', operation_id=op['id'], expected_revision=op['revision'] - 1))
    with gen.world('control_action_lock') as w:
        assert w.dash._ACTION_LOCK.acquire(blocking=False)
        try:
            w.post('control', 'action_lock', '/api/control', start_body('start-5'))
        finally:
            w.dash._ACTION_LOCK.release()
    with gen.world('control_unknown_action') as w:
        w.post('control', 'unknown_action', '/api/control', dict(action='nothing'))


def unknown_outcome_control_cases(gen):
    for kind, action, prefix in (('run', 'start', 'start'), ('gems', 'start_gems', 'start_gems')):
        with gen.world(f'unknown_{prefix}_spawn_failed') as w:
            w.spawn.fail = OSError('the worker could not be started')
            w.post('control_unknown_outcomes', f'{prefix}_accepted_then_the_worker_spawn_failed', '/api/control',
                   start_body(f'{prefix}-spawn', action))
            w.spawn.fail = None
            w.post('control_unknown_outcomes', f'{prefix}_spawn_failed_identical_retry', '/api/control',
                   start_body(f'{prefix}-spawn', action))
        with gen.world(f'unknown_{prefix}_refresh_failed') as w:
            w.mp.setattr(w.dash, '_procs_refresh', raising(RuntimeError('the process table could not be read')))
            w.post('control_unknown_outcomes', f'{prefix}_accepted_then_the_process_refresh_raised', '/api/control',
                   start_body(f'{prefix}-refresh', action))
            w.post('control_unknown_outcomes', f'{prefix}_refresh_failed_identical_retry', '/api/control',
                   start_body(f'{prefix}-refresh', action))
    with gen.world('unknown_clear_flag') as w:
        flag = Path(w.root) / 'logs' / 'main' / 'stop_after_run'
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text('stop after this run\n', encoding='utf-8')
        real = w.dash.jsonify
        w.mp.setattr(w.dash, 'jsonify', raising(RuntimeError('the answer could not be built')))
        w.post('control_unknown_outcomes', 'clear_flag_removed_then_the_answer_failed', '/api/control', dict(action='clear_flag'))
        w.mp.setattr(w.dash, 'jsonify', real)
        assert not flag.exists()
        status_case(w, 'stop_flag_false_after_the_lost_clear_flag_answer')
    pid, created = 910001, 1_790_000_000.123456
    with gen.world('unknown_kill_would_not_die') as w:
        w.table.start(pid, created, [PYW, 'orchestrator.py', '--instance', 'main'], runner=True)
        w.table.make_stubborn(pid)
        w.post('control_unknown_outcomes', 'kill_the_process_did_not_exit', '/api/control', dict(action='kill', pid=pid, created=created))
    with gen.world('unknown_kill_access_denied') as w:
        w.table.start(pid, created, [PYW, 'orchestrator.py', '--instance', 'main'], runner=True)
        w.table.refuse_termination(pid)
        w.post('control_unknown_outcomes', 'kill_access_denied', '/api/control', dict(action='kill', pid=pid, created=created))

# ---------------------------------------------- reconnect, handoff, diagnostics
def reconnect_cases(gen):
    with gen.world('reconnect_ok', adb=True) as w:
        w.post('reconnect', 'accepted_device_state_device', '/api/wizard/reconnect', {})
    with gen.world('reconnect_state', adb=True) as w:
        w.adb.state = 'unauthorized'
        w.post('reconnect', 'answered_with_another_device_state', '/api/wizard/reconnect', {})
    with gen.world('reconnect_offline', adb=True) as w:
        w.adb.state = 'offline'
        w.post('reconnect', 'answered_offline_after_the_retries', '/api/wizard/reconnect', {})
    with gen.world('reconnect_refused', adb=True) as w:
        w.worker(4601, 'orchestrator.py', args=['--preset', 'bp_farm'])
        w.post('reconnect', 'refused_while_a_runner_is_listed', '/api/wizard/reconnect', {})
    with gen.world('reconnect_raised', adb=True) as w:
        w.mp.setattr(w.dash, '_run', raising(OSError('adb.exe could not be started')))
        w.post('reconnect', 'adb_connect_raised', '/api/wizard/reconnect', {})


def handoff_cases(gen):
    with gen.world('handoff_recorded') as w:
        record(w, unresolved)
        blocker = w.dash._setup_battle_guard(w.cfg)
        w.post('handoff', 'recorded', HANDOFF, handoff_body(blocker))
        w.post('handoff', 'identical_retry_answered_from_its_record', HANDOFF, handoff_body(blocker))
    with gen.world('handoff_refused') as w:
        record(w, unresolved)
        blocker = w.dash._setup_battle_guard(w.cfg)
        w.post('handoff', 'refused_attestation_missing', HANDOFF, handoff_body(blocker, attestation=None))
        w.post('handoff', 'refused_blocker_changed', HANDOFF, handoff_body(blocker, setup_battle_id='another-battle'))
        w.post('handoff', 'refused_context_changed', HANDOFF,
               handoff_body(blocker, expected_context=dict(instance='main', account=None, serial='fake:changed')))
        w.worker(4611, 'orchestrator.py', args=['--preset', 'bp_farm'])
        w.post('handoff', 'refused_device_owned', HANDOFF, handoff_body(blocker))
        w.table.exit(4611)
        w.mp.setattr(device_lease, 'lease_state', raising(PermissionError(13, 'denied')))
        w.post('handoff', 'refused_device_owner_unreadable', HANDOFF, handoff_body(blocker))
    with gen.world('handoff_nothing_recorded') as w:
        blocker = dict(operation_id='none', setup_battle_id='none', evidence_revision='0' * 64)
        w.post('handoff', 'refused_nothing_to_hand_off', HANDOFF, handoff_body(blocker))


def diagnostic_cases(gen):
    with gen.world('diagnostics') as w:
        w.post('diagnostics_client', 'accepted', '/api/diagnostics/client',
               dict(kind='api_error', message='GET /api/status answered 500', path='/api/status', page='dashboard',
                    method='GET', request_id='req-0123456789'))
        w.post('diagnostics_client', 'refused_unsupported_kind', '/api/diagnostics/client', dict(kind='unknown'))


def unknown_outcome_write_cases(gen):
    with gen.world('unknown_handoff_answer') as w:
        record(w, unresolved)
        blocker = w.dash._setup_battle_guard(w.cfg)
        real = w.dash._collection_guard
        calls = []

        def guard_once(cfg):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError('the guard could not be read back')
            return real(cfg)
        w.mp.setattr(w.dash, '_collection_guard', guard_once)
        w.post('write_unknown_outcomes', 'handoff_recorded_then_the_answer_failed', HANDOFF, handoff_body(blocker))
        w.post('write_unknown_outcomes', 'handoff_identical_retry_created_false', HANDOFF, handoff_body(blocker))
    with gen.world('unknown_stop_raised') as w:
        op = w.running('run', {'preset': 'bp_farm'}, args=['--preset', 'bp_farm'])
        w.mp.setattr(operations, 'cancel', raising(RuntimeError('the stop could not be delivered')))
        w.post('write_unknown_outcomes', 'stop_paused_harvesting_then_the_cancel_raised',
               f"/api/operations/{op['id']}/cancel", dict(expected_revision=op['revision']))
        w.get('write_unknown_outcomes', 'operation_read_after_the_failed_stop', f"/api/operations/{op['id']}")
    with gen.world('unknown_allow_input', taps=False) as w:
        w.mp.setattr(w.dash, 'save_config', raising(OSError('config.yaml could not be written')))
        w.post('write_unknown_outcomes', 'allow_input_cleared_the_revocation_then_the_save_raised', '/api/automation-access',
               dict(enabled=True))
        w.get('write_unknown_outcomes', 'config_read_after_the_failed_allow_input', '/api/config')

# ------------------------------------------- POST /api/operations/{id}/actions
def action_body(d, action, key, **changes):
    battle = d.battle
    body = dict(battle_id=battle['id'], generation=d.generation, expected_revision=battle['control_revision'],
                idempotency_key=key, action=action)
    body.update(changes)
    return body


def action_url(op, route='actions'):
    return f"/api/operations/{op['id']}/{route}"


def recorded_commands(d):
    return [command['id'] for command in d.store.commands(d.battle['id'])]


def action_cases(gen):
    family = 'battle_actions'
    with gen.world('actions_requests') as w:
        d = Driver(w).go_live()
        url = action_url(d.op)
        w.post(family, 'accepted_command_requested', url, action_body(d, 'ad_gems', 'act-1'))
        w.post(family, 'second_key_for_the_open_action_joins_it', url, action_body(d, 'ad_gems', 'act-1-again'))
        w.post(family, 'different_action_while_one_is_open_refused', url, action_body(d, 'floating_gems', 'act-2'))
        w.post(family, 'changed_expected_revision_refused', url, action_body(d, 'floating_gems', 'act-3', expected_revision=7))
        w.post(family, 'unsupported_action_refused', url, action_body(d, 'cut_the_grass', 'act-4'))
        w.post(family, 'stale_generation_refused', url, action_body(d, 'floating_gems', 'act-5', generation='an-older-driver'))
        w.post(family, 'same_key_different_payload_refused', url, action_body(d, 'floating_gems', 'act-1'))
        w.post(family, 'unknown_operation_refused', '/api/operations/no-such-operation/actions', action_body(d, 'ad_gems', 'act-6'))
        w.post(family, 'live_controls_accepted_command_requested', action_url(d.op, 'live-controls'),
               dict(battle_id=d.battle['id'], generation=d.generation, expected_revision=d.battle['control_revision'],
                    idempotency_key='ctl-1', changes={'routines.floating_gems.enabled': True}))
        assert len(recorded_commands(d)) == 2
    with gen.world('actions_driver_gone') as w:
        d = Driver(w).go_live()
        url = action_url(d.op)
        w.post(family, 'recorded_before_the_driver_ended', url, action_body(d, 'ad_gems', 'act-gone'))
        w.table.exit(d.op['worker']['pid'])
        w.post(family, 'new_key_refused_the_driver_is_no_longer_active', url, action_body(d, 'floating_gems', 'act-gone-new'))
        w.post(family, 'identical_retry_answered_from_the_record_after_the_driver_ended', url, action_body(d, 'ad_gems', 'act-gone'))
        assert len(recorded_commands(d)) == 1
    with gen.world('actions_replaced') as w:
        first = Driver(w, 'driver-one').go_live()
        w.table.exit(first.op['worker']['pid'])
        second = Driver(w, 'driver-two', adopt=True).go_live()
        stale = dict(battle_id=first.battle['id'], generation=first.generation, expected_revision=0, idempotency_key='act-old',
                     action='ad_gems')
        w.post(family, 'replaced_battle_refused', action_url(second.op), stale)
        assert recorded_commands(second) == []
    with gen.world('actions_closure') as w:
        d = Driver(w).go_live()
        url = action_url(d.op)
        body = action_body(d, 'ad_gems', 'act-closure')
        w.post(family, 'recorded_before_the_battle_closed', url, body)
        w.table.exit(d.op['worker']['pid'])
        w.dash._harvest_tick()
        w.post(family, 'identical_retry_after_the_battle_closed_expired_battle_ended', url, body)
    with gen.world('actions_readback_failed') as w:
        d = Driver(w).go_live()
        url = action_url(d.op)
        body = action_body(d, 'ad_gems', 'act-readback')
        real = battle_api.public
        w.mp.setattr(battle_api, 'public', raising(KeyError('battle row vanished')))
        w.post(family, 'admitted_then_the_readback_failed_unknown', url, body)
        replayed = w.post(family, 'replay_while_the_readback_still_fails_unknown', url, body)
        assert replayed.get_json()['code'] == 'command_outcome_unknown'
        w.mp.setattr(battle_api, 'public', real)
        replay = w.post(family, 'identical_replay_answered_from_the_record', url, body)
        assert replay.status_code == 200 and len(recorded_commands(d)) == 1
    with gen.world('actions_diagnostic_failed') as w:
        d = Driver(w).go_live()
        original = w.dash._diagnostic_event

        def diagnostic(kind, **fields):
            if kind == 'battle_command_accepted':
                raise ValueError('the diagnostic sink rejected the record')
            return original(kind, **fields)
        w.mp.setattr(w.dash, '_diagnostic_event', diagnostic)
        body = action_body(d, 'ad_gems', 'act-diagnostic')
        w.post(family, 'admitted_then_the_success_diagnostic_raised_default_500', action_url(d.op), body)
        w.mp.setattr(w.dash, '_diagnostic_event', original)
        w.post(family, 'diagnostic_failed_identical_replay_answered_from_the_record', action_url(d.op), body)
        assert len(recorded_commands(d)) == 1

# ------------------------------------------------------------------ the document
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
HEX32 = re.compile(r'(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])')
DIGEST = re.compile(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])')


def alias_ids(value):
    """Replace every uuid (and its 8-character display prefix), 32-hex id and sha256 digest by an alias
    numbered in order of first appearance, so the contract does not depend on how many ids a change to the
    code happens to mint. (The same algorithm as test_harvest_contract.alias_ids.)"""
    text = json.dumps(value, sort_keys=False)
    aliases = {}
    for found in UUID.findall(text):
        aliases.setdefault(found, f'{len(aliases) + 1:08x}-0000-4000-8000-000000000000')
    for found, alias in aliases.items():
        text = text.replace(found, alias)
    for found, alias in aliases.items():
        text = re.sub(r'(?<![0-9a-f])' + found[:8] + r'(?![0-9a-f])', alias[:8], text)
    for pattern, width in ((HEX32, 32), (DIGEST, 64)):
        seen = {}
        for found in pattern.findall(text):
            seen.setdefault(found, f'{len(seen) + 1:0{width}x}')
        text = pattern.sub(lambda match: seen[match.group(0)], text)
    return json.loads(text)


def scrub(value, base):
    """The scenario root and every path below it become <root>/..., whatever the platform separator."""
    base = str(base)
    forms = '|'.join(re.escape(form) for form in dict.fromkeys(
        (base, base.replace('\\', '\\\\'), base.replace('\\', '/'))))
    pattern = re.compile(r'(?:' + forms + r')(?:\\\\|\\|/)+[A-Za-z0-9_.-]+'
                         r'(?P<tail>(?:(?:\\\\|\\|/)+[^\\/\s\'"<>|,;]+)*)')

    def clean(text):
        return pattern.sub(lambda match: '<root>' + re.sub(r'(?:\\\\|\\|/)+', '/', match.group('tail')), text)
    if isinstance(value, dict):
        return {key: scrub(item, base) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item, base) for item in value]
    return clean(value) if isinstance(value, str) else value


def pin_processes(value):
    if isinstance(value, dict):
        return {k: (4242 if k == 'pid' and v == os.getpid() else pin_processes(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [pin_processes(v) for v in value]
    return value


NOTES = dict(
    format=('Each case is {request:{method, path, body?}, status, body}. A JSON body is the route\'s exact answer '
            '(keys sorted by Flask); a body that is not JSON (the default 500 page an unhandled exception produces in '
            'production, exception propagation off) is {text}. Ids are aliased in order of appearance; the scenario '
            'root is <root>; time is the fixed clock 1800000000 (2027-01-15 08:00 UTC).'),
    status=('GET /api/status, one sample per condition: every root member except harvest (its own fixture, '
            'harvest_status_v1.json) and runtime (the build and port identity of the machine; /api/runtime is not '
            'a Dashboard contract). The process table, adb server and emulator port are in-memory stand-ins under the '
            'real scan, projection and adbclient parser; the two no_server samples differ only in the injected '
            'exception (a refused connect and a timed-out connect), their bodies are identical.'),
    wizard_state=('GET /api/wizard/state returns the file the wizard writes. after_scan is the section the real '
                  'emulator scan saved; all_sections adds adopt and resolution through the same writer (_wiz_save) with '
                  'the payload shapes their routes write (adopting needs a config rewrite and the resolution probe a '
                  'screencap, neither of which belongs in this generator).'),
    runs=('The list of images a run needs is replaced per case (so a case names its rows); the readiness check, the '
          'files it looks for, the rows\' cards and the /api/runs projection are real. Module restoration, the '
          'setup_required variants (validation errors from the real profile validator) and the combo entry (a '
          'profile plan) are real. readiness_check_raised injects the exception.'),
    battles=('Battle bodies come from the real BattleSession over the real GemWatch and Shopper collectors, launched '
             'through the real POST /api/control start (no frames, no input). Controls move through the driver\'s own '
             'checkpoint; an action\'s started/completed/failed/rejected progress is the driver\'s acknowledgement call, '
             'made directly because collecting a gem needs frames. The orbit collector\'s last_outcome is the dictionary '
             'its own loop builds. GET /api/battles/active never changes the ledger: closing the record of a driver '
             'that is gone is the refresher (_harvest_tick) and nothing here touches a game on screen.'),
    unknown_outcomes=('control_unknown_outcomes and write_unknown_outcomes: requests that had an effect before the '
                      'answer failed, each with the follow-up read or identical-key retry the route offers; the harvest '
                      'writes (harvest_outcome_unknown, input_revocation_failed) are in harvest_status_v1.json. '
                      'start_accepted_with_transfer repeats the harvest fixture\'s transfer sample because 10.3 lists it '
                      'for POST /api/control.'),
    battle_actions=('POST /api/operations/{id}/actions (live-controls shares the route function: one accepted '
                    'live-controls sample is included). The admitted-then-failed samples are injected exactly as '
                    'test_dashboard_admission.py injects them.'),
)


def build(dash, base, monkeypatch):
    base = Path(base) / 'contract'
    document = dict(version=1, generated_by='backend/tests/test_dashboard_contract.py', notes=NOTES,
                    cases=generate(dash, base, monkeypatch))
    return alias_ids(pin_processes(scrub(document, base)))


def render(document):
    return render_fixture_json(document)

# -------------------------------------------------- 10.3 of the Dashboard spec
# Each row of the table in section 10.3 (and of its "Unknown outcomes of the baseline writes" table) with the
# cases of this fixture that satisfy it. test_the_fixture_has_every_case_10_3_lists fails when one is missing and
# when the fixture holds a case that is neither listed here nor in EXTRAS.
STATUS_DEVICE = ['status.device_state_' + name for name in (
    'device', 'offline', 'unauthorized', 'missing', 'no_server_connect_refused', 'no_server_connect_timed_out',
    'unconfigured', 'unknown')]
SPEC_10_3 = {
    'GET /api/status': [
        'status.processes_empty', 'status.processes_linked_collector', 'status.processes_untracked_created_null',
        'status.processes_two_rows_same_runner', 'status.process_refresh_stale', 'status.process_refresh_error',
        'status.guards_clear', 'status.guards_setup_and_collection_blocking_the_same_operation',
        'status.guards_setup_blocking_collection_released_by_handoff', 'status.guards_records_unreadable',
        *STATUS_DEVICE, 'status.reconnect_idle', 'status.reconnect_trying_waiting_out_its_backoff',
        'status.reconnect_trying_attempt_in_flight', 'status.reconnect_gave_up',
        'status.stop_flag_true_with_a_listed_run', 'status.instance_other'],
    'GET /api/config': ['config.taps_true', 'config.taps_false', 'config.taps_missing'],
    'GET /api/accounts': ['accounts.active_among_several', 'accounts.none_active', 'accounts.read_error'],
    'GET /api/wizard/emulators': ['wizard_emulators.instances_with_names_and_ports', 'wizard_emulators.none',
                                  'wizard_emulators.scan_error'],
    'GET /api/wizard/tools, GET /api/wizard/state': [
        'wizard_tools.installed', 'wizard_tools.not_installed', 'wizard_state.empty', 'wizard_state.after_scan',
        'wizard_state.all_sections'],
    'GET /api/runs': [
        'runs.defined_and_undefined_with_labels', 'runs.ready', 'runs.not_ready_image_row', 'runs.not_ready_config_row',
        'runs.not_ready_module_restoration', 'runs.readiness_check_raised', 'runs.setup_required_edit_run',
        'runs.setup_required_apply_scan', 'runs.setup_required_battle_setup', 'runs.combo_entry'],
    'GET /api/readiness?preset=': ['readiness.ready_with_context', 'readiness.not_ready_with_message_and_next_action',
                                   'readiness.context_other_than_the_page'],
    'GET /api/run-controls?blueprint=': ['run_controls.ad_gems_on_floating_off', 'run_controls.both_off',
                                         'run_controls.combo_is_no_blueprint', 'run_controls.unknown_blueprint'],
    'GET /api/battles/active': [
        'battles_active.null_no_driver', 'battles_active.null_after_reconcile_closed_record', 'battles_active.prepared',
        'battles_active.runtime_ad_orbit_shopping', 'battles_active.stale_report',
        'battles_active.waiting_for_game_state_blocker', 'battles_active.legacy_shopping_plan',
        'battles_active.commands_controls_requested_action_requested',
        'battles_active.commands_controls_pending_no_path_applied_action_started',
        'battles_active.commands_controls_pending_some_paths_applied_action_completed',
        'battles_active.commands_controls_applied_with_overrides_action_failed',
        'battles_active.commands_controls_rejected_action_rejected',
        'battles_active.commands_controls_expired_action_second_key_joined', 'battles_active.replaced_generation',
        'battles_active.null_after_the_driver_ended_the_record',
        'battle_detail.interrupted_commands_expired_battle_ended', 'battle_detail.ended_commands_expired_battle_ended',
        'battle_actions.identical_retry_after_the_battle_closed_expired_battle_ended'],
    'GET /api/calibrate/status': ['calibrate_status.idle', 'calibrate_status.running_with_steps',
                                  'calibrate_status.failed_setup_unverified_final_screen', 'calibrate_status.restore_pending'],
    'GET /api/schedule/status?instance=': [
        'schedule_status.available_with_winner_and_boundary', 'schedule_status.available_no_winner',
        'schedule_status.scheduler_not_observed', 'schedule_status.scheduler_not_running',
        'schedule_status.scheduler_worker_unavailable', 'schedule_status.scheduler_observation_stale',
        'schedule_status.plan_unavailable'],
    'GET /api/battles?limit=3&instance=': [
        'battles_history.ended_with_final_wave', 'battles_history.interrupted_driver_observation_lost',
        'battles_history.active_row_first_then_interrupted_rows', 'battles_history.empty'],
    'POST /api/control': [
        'control.start_accepted', 'control.start_accepted_with_transfer', 'control.start_refused_by_setup_guard',
        'control.start_gems_accepted', 'control.start_gems_refused_by_collection_guard',
        'control.start_context_changed', 'control.start_gems_context_changed',
        'control.start_retry_answered_from_the_receipt', 'control.start_gems_retry_answered_from_the_receipt',
        'control.clear_flag_accepted', 'control.clear_flag_refused_for_a_changed_revision', 'control.action_lock',
        'control_unknown_outcomes.clear_flag_removed_then_the_answer_failed',
        'control_unknown_outcomes.kill_the_process_did_not_exit', 'control_unknown_outcomes.kill_access_denied'],
    'POST /api/wizard/reconnect': ['reconnect.accepted_device_state_device', 'reconnect.answered_with_another_device_state',
                                   'reconnect.refused_while_a_runner_is_listed', 'reconnect.adb_connect_raised'],
    'POST /api/setup-battle/collection-handoff': [
        'handoff.recorded', 'handoff.identical_retry_answered_from_its_record', 'handoff.refused_blocker_changed',
        'handoff.refused_context_changed', 'handoff.refused_device_owned', 'handoff.refused_device_owner_unreadable'],
    'POST /api/operations/{id}/actions': [
        'battle_actions.accepted_command_requested', 'battle_actions.second_key_for_the_open_action_joins_it',
        'battle_actions.different_action_while_one_is_open_refused', 'battle_actions.changed_expected_revision_refused',
        'battle_actions.replaced_battle_refused', 'battle_actions.stale_generation_refused',
        'battle_actions.unsupported_action_refused', 'battle_actions.unknown_operation_refused',
        'battle_actions.new_key_refused_the_driver_is_no_longer_active',
        'battle_actions.identical_retry_answered_from_the_record_after_the_driver_ended',
        'battle_actions.same_key_different_payload_refused', 'battle_actions.admitted_then_the_readback_failed_unknown',
        'battle_actions.identical_replay_answered_from_the_record',
        'battle_actions.replay_while_the_readback_still_fails_unknown',
        'battle_actions.admitted_then_the_success_diagnostic_raised_default_500'],
    'POST /api/diagnostics/client': ['diagnostics_client.accepted'],
    'unknown outcomes: POST /api/control start, start_gems': [
        f'control_unknown_outcomes.{kind}_{branch}' for kind in ('start', 'start_gems') for branch in (
            'accepted_then_the_worker_spawn_failed', 'spawn_failed_identical_retry',
            'accepted_then_the_process_refresh_raised', 'refresh_failed_identical_retry')],
    'unknown outcomes: POST /api/setup-battle/collection-handoff': [
        'write_unknown_outcomes.handoff_recorded_then_the_answer_failed',
        'write_unknown_outcomes.handoff_identical_retry_created_false'],
    'unknown outcomes: POST /api/operations/{id}/cancel': [
        'write_unknown_outcomes.stop_paused_harvesting_then_the_cancel_raised',
        'write_unknown_outcomes.operation_read_after_the_failed_stop'],
    'unknown outcomes: POST /api/automation-access enabled:true': [
        'write_unknown_outcomes.allow_input_cleared_the_revocation_then_the_save_raised',
        'write_unknown_outcomes.config_read_after_the_failed_allow_input'],
    'unknown outcomes: POST /api/control clear_flag (the dropped answer is OD-8, a frontend fake-transport scenario)': [
        'status.stop_flag_false_after_the_lost_clear_flag_answer'],
}
# Cases the backend produced that 10.3 does not list (documented, not required).
EXTRAS = [
    'readiness.not_ready_second_wind', 'readiness.not_ready_without_message', 'readiness.setup_required',
    'readiness.unknown_preset', 'schedule_status.unknown_emulator', 'battle_detail.unknown_battle',
    'battle_detail.other_emulator', 'battles_history.ended_final_wave_only_row', 'control.unknown_action',
    'reconnect.answered_offline_after_the_retries', 'handoff.refused_attestation_missing',
    'handoff.refused_nothing_to_hand_off', 'diagnostics_client.refused_unsupported_kind',
    'battle_actions.live_controls_accepted_command_requested',
    'battle_actions.recorded_before_the_driver_ended', 'battle_actions.recorded_before_the_battle_closed',
    'battle_actions.diagnostic_failed_identical_replay_answered_from_the_record']

FAMILIES = [status_cases, more_status_cases, config_cases, account_cases, wizard_cases, run_cases, readiness_cases,
            run_controls_cases, calibrate_cases, schedule_cases, battle_active_cases, battle_history_cases,
            control_cases, unknown_outcome_control_cases, reconnect_cases, handoff_cases, diagnostic_cases,
            unknown_outcome_write_cases, action_cases]


def generate(dash, base, monkeypatch):
    gen = Generator(dash, base, monkeypatch)
    for family in FAMILIES:
        family(gen)
    return gen.cases


def case_ids(document):
    return {f'{family}.{case}' for family, cases in document['cases'].items() for case in cases}


# ------------------------------------------------------------------------- tests
def test_the_published_contract_matches_what_the_routes_answer(dash, tmp_path, monkeypatch):
    generated = build(dash, tmp_path / 'first', monkeypatch)
    text = render(generated)
    for leak in (str(tmp_path), str(tmp_path).replace('\\', '\\\\'), tmp_path.as_posix(),
                 str(Path(dash.__file__).parents[1]), str(Path(dash.__file__).parents[1]).replace('\\', '\\\\')):
        assert leak not in text, 'a machine path leaked into the contract'
    assert 'NaN' not in text and 'Infinity' not in text
    assert render(build(dash, tmp_path / 'second', monkeypatch)) == text, 'the contract differs between two runs'
    target = os.environ.get(OUTPUT)
    if target:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(text.encode('utf-8'))
        pytest.skip(f'contract written to {target}; the committed fixture was not compared')
    if os.environ.get(REGENERATE):
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_bytes(text.encode('utf-8'))
        pytest.skip('fixture regenerated: review the diff and tell the UI author')
    assert FIXTURE.exists(), f'run with {REGENERATE}=1 to publish it'
    assert json.loads(FIXTURE.read_text(encoding='utf-8')) == generated


def fixture_document():
    assert FIXTURE.exists(), f'run with {REGENERATE}=1 to publish it'
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


def test_the_fixture_has_every_case_10_3_lists():
    document = fixture_document()
    present = case_ids(document)
    required = {case for cases in SPEC_10_3.values() for case in cases}
    assert not required - present, f'10.3 cases missing from the fixture: {sorted(required - present)}'
    unlisted = present - required - set(EXTRAS)
    assert not unlisted, f'cases that are neither a 10.3 row nor a documented extra: {sorted(unlisted)}'
    assert not set(EXTRAS) - present, f'documented extras that are not in the fixture: {sorted(set(EXTRAS) - present)}'


def body(document, ident):
    family, case = ident.split('.', 1)
    return document['cases'][family][case]['body']


def answer_of(document, ident):
    family, case = ident.split('.', 1)
    return document['cases'][family][case]


def commands_of(document):
    """Every command the fixture records, wherever a battle is embedded."""
    found = []
    for family in ('battles_active', 'battle_detail', 'battle_actions'):
        for case in document['cases'][family].values():
            battle = (case['body'] or {}).get('battle')
            if isinstance(battle, dict):
                found += battle['commands']
    for case in document['cases']['battles_history'].values():
        for battle in case['body']['battles']:
            found += battle['commands']
    return found


def test_the_fixture_covers_the_states_and_codes_10_3_names():
    document = fixture_document()
    cases = document['cases']
    status = {name: case['body'] for name, case in cases['status'].items()}
    assert all(set(item) >= {'processes', 'process_refresh', 'setup_battle_guard', 'collection_guard', 'device_state',
                             'connected', 'port_open', 'reconnect', 'stop_flag', 'instance'} and 'harvest' not in item
               for item in status.values())
    # connection: every adb word, each with its own connected/port_open, no_server twice
    states = [status[name]['device_state'] for name in status if name.startswith('device_state_')]
    assert sorted(states) == sorted(['device', 'offline', 'unauthorized', 'missing', 'no_server', 'no_server',
                                     'unconfigured', 'unknown'])
    assert all(item['connected'] == (item['device_state'] == 'device') for item in status.values())
    assert status['device_state_no_server_connect_refused'] == status['device_state_no_server_connect_timed_out']   # one body, two causes
    assert {item['port_open'] for item in status.values()} == {True, False}
    assert {item['reconnect']['state'] for item in status.values()} == {'idle', 'trying', 'gave_up'}
    assert status['reconnect_trying_waiting_out_its_backoff']['reconnect']['next_try_in'] == 30.0
    # processes
    linked = status['processes_linked_collector']['processes']
    assert [(row['runner'], type(row['created']), bool(row['operation_id']), type(row['operation_revision']),
             row['operation']['kind']) for row in linked] == [('gem_collector', float, True, int, 'gems')]
    assert [row['created'] for row in status['processes_untracked_created_null']['processes']] == [None]
    two = status['processes_two_rows_same_runner']['processes']
    assert len(two) == 2 and len({row['runner'] for row in two}) == 1 and len({row['pid'] for row in two}) == 2
    assert status['processes_empty']['processes'] == [] and status['processes_empty']['process_refresh']['state'] == 'fresh'
    assert status['process_refresh_stale']['process_refresh']['state'] == 'stale'
    assert status['process_refresh_stale']['process_refresh']['age_sec'] > 6
    assert status['process_refresh_error']['process_refresh']['error']['type'] == 'OSError'
    # guards
    clear = status['guards_clear']
    assert (clear['setup_battle_guard']['blocks_launch'], clear['collection_guard']['blocks_launch']) == (False, False)
    both = status['guards_setup_and_collection_blocking_the_same_operation']
    assert both['setup_battle_guard']['blocks_launch'] and both['collection_guard']['blocks_launch']
    assert both['setup_battle_guard']['operation_id'] == both['collection_guard']['operation_id']
    released = status['guards_setup_blocking_collection_released_by_handoff']
    assert released['setup_battle_guard']['blocks_launch'] and not released['collection_guard']['blocks_launch']
    assert released['collection_guard']['evidence'] == 'collection_handoff' and released['collection_guard']['handoff_id']
    unreadable = status['guards_records_unreadable']
    assert unreadable['setup_battle_guard']['blocks_launch'] and unreadable['setup_battle_guard']['code'] == 'setup_evidence_unreadable'
    assert status['stop_flag_true_with_a_listed_run']['stop_flag'] is True
    assert status['stop_flag_true_with_a_listed_run']['processes'][0]['stop_flag'] is True
    assert status['instance_other']['instance'] == 'other'
    # config
    taps = {name: cases['config'][name]['body']['instances'][cases['config'][name]['body']['active_instance']].get('allow_taps', 'missing')
            for name in cases['config']}
    assert taps == {'taps_true': True, 'taps_false': False, 'taps_missing': 'missing'}
    # accounts
    assert body(document, 'accounts.active_among_several')['active'] and len(body(document, 'accounts.active_among_several')['accounts']) > 1
    assert body(document, 'accounts.none_active')['active'] is None
    assert answer_of(document, 'accounts.read_error')['status'] == 500 and 'text' in body(document, 'accounts.read_error')
    # wizard
    instances = body(document, 'wizard_emulators.instances_with_names_and_ports')['instances']
    assert all(item.get('name') and item.get('adb_port') for item in instances)
    assert body(document, 'wizard_emulators.none')['instances'] == []
    assert any('error' in item for item in body(document, 'wizard_emulators.scan_error')['instances'])
    assert (body(document, 'wizard_tools.installed')['installed'], body(document, 'wizard_tools.not_installed')['installed']) == (True, False)
    assert body(document, 'wizard_state.empty') == {} and set(body(document, 'wizard_state.all_sections')) == {'emulators', 'adopt', 'resolution'}
    # runs
    runs = body(document, 'runs.defined_and_undefined_with_labels')['runs']
    assert any(run.get('defined') is False and run.get('label') for run in runs.values()) and any('defined' not in run for run in runs.values())
    assert body(document, 'runs.ready')['readiness']['bp_farm']['ready'] is True
    image = body(document, 'runs.not_ready_image_row')['readiness']['bp_farm']
    assert image['ready'] is False and image['details'][0]['alternatives'][0].endswith('.png')
    config_row = body(document, 'runs.not_ready_config_row')['readiness']['bp_farm']
    assert config_row['details'][0]['alternatives'] == ['config: rois.wall_bar']
    restore = body(document, 'runs.not_ready_module_restoration')['readiness']['bp_farm']
    assert restore['details'] == [dict(alternatives=[], docs=[], reasons=[
        'Restore the original modules: return to Home and resume calibration'])]
    raised = body(document, 'runs.readiness_check_raised')['readiness']['bp_farm']
    assert raised['missing'][0].startswith('readiness check failed: ') and 'details' not in raised
    required = {body(document, f'runs.setup_required_{kind}')['readiness']['bp_farm']['next_action'] for kind in
                ('edit_run', 'apply_scan', 'battle_setup')}
    assert required == {'edit_run', 'apply_scan', 'battle_setup'}
    assert all(body(document, f'runs.setup_required_{kind}')['readiness']['bp_farm']['setup_required'] is True
               for kind in ('edit_run', 'apply_scan', 'battle_setup'))
    assert 'combo' in body(document, 'runs.combo_entry')['readiness'] and 'combo' in body(document, 'runs.combo_entry')['runs']
    # readiness
    assert body(document, 'readiness.ready_with_context')['ready'] is True and 'context' in body(document, 'readiness.ready_with_context')
    guided = body(document, 'readiness.not_ready_with_message_and_next_action')
    assert guided['ready'] is False and guided['message'] and guided['next_action']
    assert body(document, 'readiness.context_other_than_the_page')['context']['instance'] == 'other'
    # run controls
    on, off = body(document, 'run_controls.ad_gems_on_floating_off'), body(document, 'run_controls.both_off')
    assert (on['settings']['routines']['ad_gems']['enabled'], on['settings']['routines']['floating_gems']['enabled']) == (True, False)
    assert (off['settings']['routines']['ad_gems']['enabled'], off['settings']['routines']['floating_gems']['enabled']) == (False, False)
    assert on['context'] and answer_of(document, 'run_controls.unknown_blueprint')['status'] == 400
    assert answer_of(document, 'run_controls.combo_is_no_blueprint')['status'] == 400
    # calibration
    assert body(document, 'calibrate_status.restore_pending')['restore_pending'] is True
    failed = body(document, 'calibrate_status.failed_setup_unverified_final_screen')
    assert failed['state']['setup_battle'] and failed['state']['final_screen']['verified'] is False
    assert body(document, 'calibrate_status.running_with_steps')['running'] is True
    assert body(document, 'calibrate_status.running_with_steps')['state']['phases']
    # schedule
    schedule = {name: case['body'] for name, case in cases['schedule_status'].items()}
    assert {item['reason'] for name, item in schedule.items() if name != 'unknown_emulator'} == {
        'first_eligible_block', 'no_blocks_today', 'scheduler_not_observed', 'scheduler_not_running',
        'scheduler_worker_unavailable', 'scheduler_observation_stale', 'plan_unavailable'}
    assert schedule['available_with_winner_and_boundary']['winner'] and schedule['available_with_winner_and_boundary']['next_clock_boundary']
    assert schedule['available_no_winner']['available'] is True and schedule['available_no_winner']['winner'] is None
    # battles: records
    active = {name: case['body'] for name, case in cases['battles_active'].items()}
    assert active['null_no_driver']['battle'] is None and active['null_after_reconcile_closed_record']['battle'] is None
    assert active['null_after_the_driver_ended_the_record']['battle'] is None
    assert active['prepared']['battle']['status'] == 'prepared'
    runtime = active['runtime_ad_orbit_shopping']['battle']
    routines = runtime['runtime']['routines']
    assert routines['ad_gems']['last_outcome']['confirmed'] is True and routines['floating_gems']['last_outcome']['confirmed'] is False
    assert routines['ad_gems']['window_ends_at'] and routines['shopping']['next_due_at'] and 'blocker' in routines['ad_gems']
    assert runtime['capabilities']['controls'] and runtime['capabilities']['actions']
    assert runtime['effective']['shopping_plan']['version'] == 2 and runtime['effective']['shopping_plan']['queue']
    assert routines['shopping']['last_outcome']['reserve_supported'] is True
    legacy = active['legacy_shopping_plan']['battle']
    assert legacy['runtime']['routines']['shopping']['last_outcome']['reserve_supported'] is False
    assert active['stale_report']['server_time'] - active['stale_report']['battle']['runtime']['server_time'] > 100
    assert active['waiting_for_game_state_blocker']['battle']['runtime']['routines']['ad_gems']['blocker'] == 'waiting_for_game_state'
    replaced = active['replaced_generation']['battle']
    assert replaced['previous_battle_id'] and replaced['origin'] == 'partial_adoption'
    assert active['commands_controls_applied_with_overrides_action_failed']['battle']['overrides']
    # battles: commands, per kind, wire values
    commands = commands_of(document)
    assert all(isinstance(command.get('request_keys'), list) and command['request_keys'] for command in commands)
    states = {kind: {command['state'] for command in commands if command['kind'] == kind} for kind in ('controls', 'action')}
    assert states['controls'] == {'requested', 'pending', 'applied', 'rejected', 'expired'}
    assert states['action'] == {'requested', 'started', 'completed', 'failed', 'rejected', 'expired'}
    pending = [command for command in commands if command['kind'] == 'controls' and command['state'] == 'pending']
    assert any(command['applied_paths'] == [] and command['reason'] == 'finishing_owned_flow' for command in pending)
    assert any(command['applied_paths'] and command['reason'] == 'finishing_owned_flow' for command in pending)
    assert any(command['state'] == 'completed' and command['evidence'] for command in commands)
    assert any(len(command['request_keys']) == 2 for command in commands)
    expired = [command for command in commands if command['state'] == 'expired' and command['reason'] == 'battle_ended']
    assert {command['kind'] for command in expired} == {'controls', 'action'}
    # battles: history
    mixed = body(document, 'battles_history.active_row_first_then_interrupted_rows')['battles']
    assert [(row['status'], row['end_reason']) for row in mixed] == [
        ('active', None), ('interrupted', 'driver_observation_lost'), ('interrupted', 'driver_operation_ended')]
    assert all(row['final_wave'] is None and row['wave'] for row in mixed[1:])
    ended = body(document, 'battles_history.ended_with_final_wave')['battles'][0]
    assert (ended['status'], ended['end_reason'], ended['final_wave']) == ('ended', 'observed_results', 187)
    assert body(document, 'battles_history.empty')['battles'] == []
    # writes
    wire = lambda ident: (answer_of(document, ident)['status'], body(document, ident).get('code'))   # noqa: E731
    assert wire('control.start_refused_by_setup_guard') == (409, 'setup_battle_unresolved')
    assert wire('control.start_gems_refused_by_collection_guard') == (409, 'setup_battle_unresolved')
    assert wire('control.start_context_changed')[0] == 409 and wire('control.start_gems_context_changed')[0] == 409
    assert wire('control.action_lock') == (409, 'action_lock')
    assert body(document, 'control.start_accepted_with_transfer')['transfer']['stopped']
    assert body(document, 'control.start_retry_answered_from_the_receipt')['operation_id'] == body(document, 'control.start_accepted')['operation_id']
    assert wire('control_unknown_outcomes.kill_access_denied') == (500, 'process_access_denied')
    assert wire('control_unknown_outcomes.kill_the_process_did_not_exit')[0] == 500
    for kind in ('start', 'start_gems'):
        assert answer_of(document, f'control_unknown_outcomes.{kind}_accepted_then_the_worker_spawn_failed')['status'] == 500
        assert 'text' in body(document, f'control_unknown_outcomes.{kind}_accepted_then_the_worker_spawn_failed')
        retry = body(document, f'control_unknown_outcomes.{kind}_spawn_failed_identical_retry')
        assert retry['operation']['status'] == 'failed' and retry['operation']['error']['code'] == 'launch_failed'
        assert answer_of(document, f'control_unknown_outcomes.{kind}_refresh_failed_identical_retry')['status'] == 200
    assert body(document, 'status.stop_flag_false_after_the_lost_clear_flag_answer')['stop_flag'] is False
    assert [body(document, f'reconnect.{name}')['device_state'] for name in (
        'accepted_device_state_device', 'answered_with_another_device_state')] == ['device', 'unauthorized']
    assert wire('reconnect.refused_while_a_runner_is_listed') == (409, 'device_owned')
    assert wire('reconnect.adb_connect_raised')[0] == 500
    assert {wire(f'handoff.refused_{name}')[1] for name in ('blocker_changed', 'context_changed', 'device_owned', 'device_owner_unreadable')} == {
        'handoff_blocker_changed', 'context_changed', 'device_owned', 'device_owner_unreadable'}
    assert (body(document, 'handoff.recorded')['created'], body(document, 'handoff.identical_retry_answered_from_its_record')['created']) == (True, False)
    assert 'text' in body(document, 'write_unknown_outcomes.handoff_recorded_then_the_answer_failed')
    assert body(document, 'write_unknown_outcomes.handoff_identical_retry_created_false')['created'] is False
    assert 'text' in body(document, 'write_unknown_outcomes.stop_paused_harvesting_then_the_cancel_raised')
    assert body(document, 'write_unknown_outcomes.operation_read_after_the_failed_stop')['operation']['status'] == 'running'
    assert 'text' in body(document, 'write_unknown_outcomes.allow_input_cleared_the_revocation_then_the_save_raised')
    assert body(document, 'write_unknown_outcomes.config_read_after_the_failed_allow_input')['instances']['main']['allow_taps'] is False
    # actions
    assert wire('battle_actions.accepted_command_requested') == (200, None)
    assert body(document, 'battle_actions.accepted_command_requested')['command']['state'] == 'requested'
    assert wire('battle_actions.different_action_while_one_is_open_refused')[0] == 409
    assert 'Another manual action is pending or active' in body(document, 'battle_actions.different_action_while_one_is_open_refused')['error']
    assert wire('battle_actions.unsupported_action_refused')[0] == 400 and wire('battle_actions.unknown_operation_refused')[0] == 404
    assert 'Idempotency key reused for another command' in body(document, 'battle_actions.same_key_different_payload_refused')['error']
    unknown = body(document, 'battle_actions.admitted_then_the_readback_failed_unknown')
    assert (unknown['code'], unknown['outcome'], unknown['ok']) == ('command_outcome_unknown', 'unknown', False)
    assert body(document, 'battle_actions.replay_while_the_readback_still_fails_unknown')['command_id'] == unknown['command_id']
    replay = body(document, 'battle_actions.identical_replay_answered_from_the_record')
    assert replay['command']['id'] == unknown['command_id'] and len(replay['battle']['commands']) == 1
    assert answer_of(document, 'battle_actions.admitted_then_the_success_diagnostic_raised_default_500')['status'] == 500
    assert 'text' in body(document, 'battle_actions.admitted_then_the_success_diagnostic_raised_default_500')
    closed = body(document, 'battle_actions.identical_retry_after_the_battle_closed_expired_battle_ended')
    assert (closed['command']['state'], closed['command']['reason']) == ('expired', 'battle_ended')
    assert wire('diagnostics_client.accepted') == (202, None)
