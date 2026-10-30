"""Local web dashboard: preset/loadout editor, live status, setup wizard.

Runs a Flask server on 127.0.0.1 only - this is a control panel for THIS
machine, not a service. Point a browser at http://127.0.0.1:8620/

Design rules:
  * The dashboard never imports settings/capture/orchestrator - the runners own the
    game. Everything here goes through the filesystem (config.yaml, logs,
    daily_state) or a direct adb call, so a dashboard bug can never wedge a
    live run.
  * Every config save writes a timestamped backup next to config.yaml first.
    People WILL save a broken value; the previous file must be one copy away.
  * Emulator-agnostic: MuMu and BlueStacks (and anything else adb-based)
    differ only in where adb.exe lives and which ports the instances listen
    on. The wizard scans the known install paths and probes each daemon for
    devices; whatever answers `adb devices` can be driven.

Start:  python backend/dashboard.py   (or pythonw for headless)
"""
import copy
import datetime
import glob
import hashlib
import json
import os
import shutil
import re
import struct
import subprocess
import sys
import threading
from pathlib import Path
from functools import wraps
import time
import urllib.parse
import uuid
import traceback
from contextlib import ExitStack

import yaml
from flask import Flask, Response, jsonify, redirect, request, send_file, g, has_request_context

# This module lives in the BACKEND directory, next to everything it controls -
# config.yaml, logs, profiles, templates, the runner scripts. ROOT is that
# directory: the working directory every runner is spawned with and the tree
# that _proc_in_tree scopes process ownership to.
ROOT = os.path.dirname(os.path.abspath(__file__))
# What it serves is the FRONTEND's static folder, a sibling of the backend. The
# path is absolute: a relative one would resolve against this module's folder.
WEBUI = os.path.join(os.path.dirname(ROOT), "frontend", "webui")
# The compiler-side imports (playerprofile.vocab, the flow registry) resolve
# from the backend too.
sys.path.insert(0, ROOT)
CONFIG_PATH = os.path.join(ROOT, "config.yaml")
PORT = int(os.environ.get("TOWER_PILOT_PORT", "8620"))   # override with the
# TOWER_PILOT_PORT env var (a second dashboard on one machine, tests)
HOST = "127.0.0.1"          # the one interface app.run binds (bottom of file)
# Host names a request to THIS dashboard may carry (any port): loopback and
# the bind host. Anything else is DNS rebinding - a site whose name now
# resolves to 127.0.0.1 - and is refused by local_requests_only.
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", HOST}

# The dashboard runs under pythonw (no console), so every adb.exe child pops
# its own console window - the frame endpoint alone spawned ~720 windows/hour
# (the "MuMu PowerShell window", 2026-08-18). Same rule as settings.run_hidden,
# duplicated here because this module deliberately never imports settings.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _run(args, **kw):
    kw.setdefault("creationflags", NO_WINDOW)
    return subprocess.run(args, **kw)

app = Flask(__name__, static_folder=WEBUI, static_url_path="/ui")

from control import operations as operation_api
from control import battles as battle_api
from control import harvest as harvest_api
from control import takeover as takeover_api
from runtime import build_info, config_seed, environment
from towerpilot_workflows.harvest import gem_policy_revision

_BUILD = build_info.snapshot(Path(ROOT).parent, 'dashboard')
_CAPABILITIES = ['battle_controls', 'run_history', 'stream_backends', 'runtime_identity',
                 'process_refresh_status', 'schedule_status', 'revisioned_editor_saves',
                 'setup_battle_guard', 'collection_handoff', 'gem_harvesting', 'action_executions']


@app.get('/api/runtime')
def api_runtime():
    active = operation_api.Operations.active_readonly(operation_api.ledger_path(ROOT))
    workers = [dict(operation_id=op['id'], kind=op['kind'], status=op['status'],
                    instance=op['context'].get('instance'), worker=op.get('worker'),
                    alive=operation_api.worker_alive(op.get('worker')))
               for op in active if not operation_api.synchronous_persistence(op)]
    persistence = [operation_api.public(op) for op in active if operation_api.synchronous_persistence(op)]
    return jsonify(server=build_info.compare(Path(ROOT).parent, _BUILD),
                   release=build_info.release_identity(Path(ROOT).parent),
                   environment=environment.report(Path(ROOT).parent, emulators={
                       'mumu': _mumu_manager(), 'bluestacks': _bluestacks_player()}),
                   capabilities=_CAPABILITIES, api_contract=1, port=PORT,
                   workers=workers, persistence_operations=persistence, process_refresh=_procs_status())


# The routes of the Recognition page answer every refusal with a stable `code` to branch on (and, when the refusal names one, the
# operation as it is now), the way a person's Stop does. Every other route keeps {ok, error} until its consumer moves to codes:
# dashboard_contract_v1.json is bound to that shape.
CODED_REFUSAL_ENDPOINTS = frozenset({
    'api_calibrate_start', 'api_calibrate_observe', 'api_calibrate_apply', 'api_calibrate_status',
    'api_screen_analysis_start', 'api_screen_analysis_status', 'api_screen_analysis_frame'})


@app.errorhandler(operation_api.Conflict)
def operation_conflict(error):
    if request.endpoint not in CODED_REFUSAL_ENDPOINTS:
        return jsonify(ok=False, error=str(error)), 409
    body = dict(ok=False, code=error.code or 'conflict', error=str(error))
    if getattr(error, 'operation', None) is not None:
        body['operation'] = operation_api.public(error.operation)
    return jsonify(body), 409


@app.errorhandler(operation_api.ContextChanged)
def page_context_changed(error):
    """A write fenced by `expected_context` (see _fenced_by_page_context): nothing was written. `current_context` is what the
    configuration names now, so the page can show it; the page never resends by itself."""
    return jsonify(ok=False, code='context_changed', error=str(error), current_context=error.current), 409


class InvalidRequest(Exception):
    """A request the route cannot act on (400). Nothing was done. `code` is the stable word a client branches on."""

    def __init__(self, message, code='invalid_request'):
        super().__init__(message)
        self.code = code


@app.errorhandler(InvalidRequest)
def invalid_request(error):
    return jsonify(ok=False, code=error.code, error=str(error)), 400


def _request_object(*, required=False):
    """The JSON object a write route was sent.

    required: the route reads nothing else, so an empty, mangled or non-object body is refused. Otherwise a body that is empty or
    does not claim to be JSON is an empty command, as it always was, but a body that claims to be JSON must be an object: a
    mangled or array body would otherwise start a default operation the client did not ask for."""
    raw = request.get_data()
    if not required and (not raw.strip() or not request.is_json):
        return {}
    try:
        body = json.loads(raw)
    except ValueError:                                  # includes UnicodeDecodeError
        raise InvalidRequest('The request body is not valid JSON') from None
    if not isinstance(body, dict):
        raise InvalidRequest('The request body must be a JSON object')
    return body


MAX_OBSERVE_WATCH_SEC = 600


def _watch_seconds(value):
    """How long an observation may wait for the screen it is to read: a whole number of seconds from 0 to 600 (absent or 0: the
    two frames only). Anything else is refused, never clamped."""
    if value is None:
        return 0
    if type(value) is int and 0 <= value <= MAX_OBSERVE_WATCH_SEC:
        return value
    raise InvalidRequest(f'watch must be a whole number of seconds from 0 to {MAX_OBSERVE_WATCH_SEC}', 'invalid_watch')


def _refuse_while_calibrating():
    if any(pr["runner"] == "calibrate" for pr in _procs()):
        raise operation_api.OperationRefused('calibration_running', 'a calibration is already running')


@app.errorhandler(battle_api.SetupBattleUnresolved)
def setup_battle_unresolved(error):
    """A launch refused before acceptance, with the guard /api/status publishes."""
    return jsonify(ok=False, code=error.guard['code'], error=str(error),
                   setup_battle_guard=error.guard), 409


def _setup_battle_guard(cfg):
    """The setup-battle guard for cfg's emulator: the rule control.operations
    admission applies to run launches (read-only)."""
    return battle_api.guard_for(ROOT, cfg, scope='run')


def _collection_guard(cfg):
    """The same rule for the gem collector's admission and entry (read-only):
    additionally honours a recorded collection-only handoff."""
    return battle_api.guard_for(ROOT, cfg, scope='collection')


@app.errorhandler(takeover_api.InvalidTakeover)
def takeover_invalid(error):
    return jsonify(ok=False, code=error.code, reason=error.reason, field=error.field, error=str(error)), 400


@app.errorhandler(takeover_api.TakeoverRefused)
def takeover_refused(error):
    """A takeover refused before the collector's input stopped: no operation or battle was recorded and nothing was sent to the game."""
    return jsonify(ok=False, code=error.code, error=str(error), **error.extra), 409


@app.errorhandler(battle_api.HandoffRefused)
def collection_handoff_refused(error):
    return jsonify(ok=False, code=error.code, error=str(error),
                   **({'setup_battle_guard': error.guard} if error.guard is not None else {})), 409


def _device_owners(identity):
    """Everything that holds this emulator now: active ledger operations
    (dead workers reconciled first) and runner processes from any checkout."""
    from runtime import runners
    owners = []
    for op in operation_api.ledger(ROOT).active(identity['device_key']):
        op = operation_api.resolve(ROOT, op['id'])
        if op['status'] in ('accepted', 'running', 'cancelling', 'applying'):
            owners.append(f"operation {op['id'][:8]} ({op['kind']})")
    target = (identity['adb_host'], identity['adb_port'], identity['serial'])
    owners += [f'process {pid} ({script})' for pid, script, _ in runners.other_runners(target=target)]
    if not owners and harvest_api.lease_holder(identity) is not None:
        owners.append('a process that holds the emulator lock')   # the device lease is the final owner
    return owners


@app.post('/api/setup-battle/collection-handoff')
def api_collection_handoff():
    """Record a person's explicit statement that the battle on screen is a new
    manually started run, for the one exact unresolved setup record. Grants gem
    collection only; general runs stay refused and the failed setup's history
    is unchanged. Refusals record nothing."""
    cfg = load_config()
    try:
        record, created, _ = battle_api.record_collection_handoff(
            ROOT, cfg, request.get_json(silent=True), owners=_device_owners)
    except (battle_api.HandoffRefused, harvest_api.OwnersUnreadable):
        raise
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    _diagnostic_event('collection_handoff_recorded', handoff_id=record['id'], created=created,
                      setup_operation_id=record['blocker']['operation_id'],
                      setup_battle_id=record['blocker']['setup_battle_id'], outcome='recorded')
    return jsonify(ok=True, created=created, handoff=record,
                   collection_guard=_collection_guard(cfg), setup_battle_guard=_setup_battle_guard(cfg))


# ------------------------------------------------------- persistent harvesting
class DeviceOwned(operation_api.Conflict):
    """An action refused because automation holds the emulator. One body for
    every route that needs an idle emulator: who holds it, and how to release it."""

    def __init__(self, body):
        super().__init__(body['error'])
        self.body = body


@app.errorhandler(DeviceOwned)
def device_owned(error):
    return jsonify(error.body), 409


@app.errorhandler(harvest_api.OwnersUnreadable)
def owners_unreadable(error):
    return jsonify(ok=False, code='device_owner_unreadable', error=str(error)), 409


@app.errorhandler(harvest_api.ChoiceRefused)
def harvest_choice_refused(error):
    return jsonify(ok=False, code=error.code, error=str(error), **error.extra), 409


def _owners_now(cfg, rows):
    """Every owner of the emulator: the runner processes in rows AND every
    accepted ledger operation, whether or not its worker has spawned yet. This
    is only called on a POST path, so workers that died without an outcome are
    retired first (project_processes reconciles)."""
    return harvest_api.device_owners(ROOT, cfg, rows)


def _device_owned_body(message, found, rows, *, code, collector_code=None):
    if not found:
        found = [harvest_api.owner_view(None, None, rows[0], time.time(), 0)]
    owner = found[0]
    if collector_code and owner.get('operation_kind') == 'gems':
        code = collector_code
    return dict(ok=False, code=code, error=message, owner=owner, recovery=owner['recovery'],
                retry='after_confirmed_stop')


def _require_idle(message, rows=None, *, code='device_owned', collector_code=None, allow_kinds=()):
    """Refuse (409, shared body) while anything holds the emulator: a runner
    process (rows defaults to a fresh scan of this tree's runners) or an accepted
    ledger operation that has no process yet. allow_kinds names operation kinds
    the calling route owns and judges itself (a second scan, a second calibration)."""
    rows = _procs() if rows is None else rows
    cfg = load_config()
    everyone = _owners_now(cfg, rows)
    found = [owner for owner in everyone if owner.get('operation_kind') not in allow_kinds]
    if rows or found:
        raise DeviceOwned(_device_owned_body(message, found, rows, code=code, collector_code=collector_code))
    if not everyone:
        _require_lease_free(cfg, message, code=code)


def _require_lease_free(cfg, message, *, code='device_owned'):
    """The device lease is the final owner: refuse while any process holds it even
    when the ledger and the process table read empty (another checkout's worker, an
    operation retired in error). Only asked when nothing else explains the lock, so
    a route that judges its own scan or calibration is never refused by its own worker."""
    holder = harvest_api.lease_owner(cfg)
    if holder is not None:
        raise DeviceOwned(_device_owned_body(message, [holder], [], code=code))


_TRANSFER_WAIT_SEC = harvest_api.TRANSFER_WAIT_SEC
_HARVEST_SOURCES = {}
_HARVEST_SOURCES_LOCK = threading.Lock()
_HARVEST_SOURCE_TTL = 30.0
_HARVEST_HOST = {'last_tick_at': None, 'last_error': None, 'collector_at_start': None}


def _harvest_source(cfg, preset):
    """The gem settings harvesting would use from one saved run, or why it cannot.
    Never raises. Recompiles only when the profile, the config file or the run
    changed (or after 30 s), because /api/status asks on every poll."""
    profile = cfg.get('active_profile')
    try:
        revision = operation_api.file_revision(_profile_path(profile)) if profile else None
        stat = os.stat(CONFIG_PATH)
        key = (profile, revision, stat.st_mtime_ns, stat.st_size, preset)
    except OSError:
        key = None
    now = time.monotonic()
    with _HARVEST_SOURCES_LOCK:
        hit = _HARVEST_SOURCES.get(key) if key else None
    if hit is not None and now - hit[0] < _HARVEST_SOURCE_TTL:
        import copy
        return copy.deepcopy(hit[1])
    value = _compute_harvest_source(cfg, preset, profile)
    if key:
        with _HARVEST_SOURCES_LOCK:
            if len(_HARVEST_SOURCES) > 32:
                _HARVEST_SOURCES.clear()
            _HARVEST_SOURCES[key] = (now, value)
    import copy
    return copy.deepcopy(value)


def _compute_harvest_source(cfg, preset, profile):
    from player.run_controls import effective_preset, resolve
    source = dict(preset=preset, label=None, profile=profile, valid=False, code='source_unavailable',
                  message='The selected run could not be read.', policy_revision=None, policy=None,
                  edit=dict(page='runs', blueprint=preset[3:] if preset.startswith('bp_') else None))
    try:
        runs = _compiled_runs(dict(cfg))
    except ValueError as exc:
        return dict(source, code='profile_invalid', message=f'The saved profile is not valid: {exc}')
    except Exception as exc:                            # noqa: BLE001 - reported, never raised from a status read
        return dict(source, message=f'The selected run could not be read: {type(exc).__name__}: {exc}')
    body = runs.get(preset)
    if not preset.startswith('bp_') or not isinstance(body, dict):
        return dict(source, code='preset_missing', message="Choose a saved run's diamond settings. That run no longer exists.")
    source.update(label=body.get('label'),
                  edit=dict(page='runs', blueprint=(body.get('_source') or {}).get('blueprint') or preset[3:]))
    effective = effective_preset(dict(cfg, presets=runs), preset)   # what a launch would freeze
    gather = effective.get('gather') or {}
    if not (gather.get('ad_gems', True) or (gather.get('gem_orbit') or {}).get('enabled')):
        return dict(source, code='gathering_disabled',
                    message="Enable Ad Gems or timed orbit clicks in this run's gathering settings.")
    try:
        routines = resolve(effective, dict(cfg))['routines']
    except Exception as exc:                            # noqa: BLE001
        return dict(source, message=f'The run\'s gem settings could not be resolved: {exc}')
    policy = dict(ad_gems={key: routines['ad_gems'][key] for key in ('enabled', 'interval_sec', 'window_sec')},
                  floating_gems={key: routines['floating_gems'][key]
                                 for key in ('enabled', 'interval_sec', 'window_sec', 'tap_interval_sec')})
    return dict(source, valid=True, code=None, message=None, policy=policy,
                policy_revision=gem_policy_revision(preset, gather))


_REFUSAL_STATUS = {'preset_missing': 400}


def _gems_launch_plan(cfg, name):
    """Everything a diamond collector needs before it may start (a person's Collect
    and the lifecycle host share it). Returns harvest_api.Prepared(command,
    policy_revision): the worker command and the revision of the gem settings of
    the ONE compiled run this launch freezes into its envelope. Raises
    harvest_api.Refused(code, message) with nothing started."""
    from player.run_controls import effective_preset
    inst = cfg.get('active_instance', 'main')
    if not cfg.get('instances', {}).get(inst, {}).get('allow_taps'):
        raise harvest_api.Refused('taps_disabled', 'Enable automation taps first.')
    try:
        runs = _compiled_runs(cfg)
    except ValueError as exc:
        raise harvest_api.Refused('profile_invalid', str(exc)) from exc
    cfg['presets'] = runs
    if name not in runs:
        raise harvest_api.Refused('preset_missing', "Choose a run's diamond settings.")
    effective = effective_preset(cfg, name)   # exactly what _launch_operation freezes
    gather = effective.get('gather') or {}
    if not (gather.get('ad_gems', True) or (gather.get('gem_orbit') or {}).get('enabled')):
        raise harvest_api.Refused('gathering_disabled',
                                  "Enable Ad Gems or timed orbit clicks in this run's gathering settings.")
    # The HUD collector can verify CLAIM + 6 without a saved offer image.
    # Its battle proof still requires the account's complete digit font.
    from player import accounts
    import cv2
    for digit in range(10):
        path = accounts.template_path(ROOT, cfg, f'digits/{digit}.png')
        if not path.exists() or cv2.imread(str(path), cv2.IMREAD_GRAYSCALE) is None:
            raise harvest_api.Refused('digits_missing', 'Learn the wave digits before collecting gems.')
    command = [sys.executable.replace('python.exe', 'pythonw.exe'), os.path.join(ROOT, 'gem_collector.py'),
               '--instance', inst, '--preset', name]
    return harvest_api.Prepared(command, gem_policy_revision(name, gather))


def _harvest_host():
    """This server's own reconciler, not the ledger's: another dashboard has its own."""
    thread, last = _PROCS_THREAD, _HARVEST_HOST['last_tick_at']
    fresh = last is None or time.time() - last <= 30
    host = dict(reconciler='active' if thread is not None and thread.is_alive() and fresh else 'inactive',
                last_tick_at=last, last_error=_HARVEST_HOST['last_error'])
    if _HARVEST_HOST.get('collector_at_start') is not None:       # present only when this start found one
        host['collector_at_start'] = _HARVEST_HOST['collector_at_start']
    return host


def _harvest_status(cfg=None, rows=None):
    """status.harvest: one read-only snapshot. A failure is reported as an
    unavailable harvest, never as a failed /api/status."""
    cfg = load_config() if cfg is None else cfg
    try:
        rows = operation_api.project_processes(ROOT, _procs_cached(), reconcile=False) if rows is None else rows
        return harvest_api.status(ROOT, cfg, processes=rows, resolve_source=_harvest_source, host=_harvest_host())
    except Exception as exc:                            # noqa: BLE001
        app.logger.exception('Harvest status failed')
        return dict(version=harvest_api.VERSION, server_time=time.time(), state='unavailable', code='status_failed',
                    message=f'Harvesting status could not be read ({type(exc).__name__}).', host=_harvest_host(),
                    desired=None, source=None, operation=None, owner=None, overlay=None, collection_guard=None)


_ACTION_HISTORY = {'unusable': 0, 'failure': None}   # what the last settlement reported: a standing condition is reported once


def _settle_action_history():
    """Close the action executions whose host is provably gone. The history is bookkeeping beside the lifecycle: whatever it
    raises (a locked or unreadable ledger, a row nobody can read) is reported once and never stops the reconciliation that
    follows in the same tick, and a damaged row is skipped and reported, never retried into a failure."""
    from control import actions as actions_api
    try:
        seen = []
        actions_api.settle(ROOT, event=lambda kind, **fields: seen.append(fields))
        unusable = sum(fields['rows'] for fields in seen)
        if unusable != _ACTION_HISTORY['unusable']:
            _ACTION_HISTORY['unusable'] = unusable
            if unusable:
                _diagnostic_event('action_history_unusable', rows=unusable, where='settlement')
        _ACTION_HISTORY['failure'] = None
    except Exception as exc:                            # noqa: BLE001 - nothing here may reach the lifecycle
        failure = (type(exc).__name__, str(exc))
        if _ACTION_HISTORY['failure'] != failure:
            _ACTION_HISTORY['failure'] = failure
            app.logger.exception('Action history settlement failed')


def _harvest_tick():
    """One reconciliation step on the process-refresher thread. Never from a GET
    or a browser tab. Skipped while a person's action holds the action lock.

    This is the one place that moves recorded lifecycle state without a person's
    request: project_processes retires operations whose worker died, then the
    battles of operations that ended are finished. Every GET only reads."""
    if not _ACTION_LOCK.acquire(blocking=False):
        return
    try:
        cfg = load_config()
        rows = operation_api.project_processes(ROOT, _procs_cached())
        battle_api.reconcile_existing(ROOT)
        _settle_action_history()        # close the executions whose host is provably gone: never from a GET, never able to skip the tick below
        harvest_api.tick(
            ROOT, cfg, processes=rows, actions=harvest_api.action_session(ROOT, cfg),
            resolve_source=_harvest_source, prepare=_gems_launch_plan,
            launch=lambda cfg_, body, cmd: _launch_operation(cfg_, 'gems', body, cmd),
            device_state=lambda identity: _device_state(cfg)['device_state'],
            event=lambda kind, **fields: _diagnostic_event(kind, **fields))
        _HARVEST_HOST.update(last_tick_at=time.time(), last_error=None)
    except Exception as exc:                            # noqa: BLE001 - the refresher must survive
        app.logger.exception('Harvest reconciliation failed')
        _HARVEST_HOST.update(last_tick_at=time.time(),
                             last_error=dict(type=type(exc).__name__, message=str(exc), at=time.time()))
    finally:
        _ACTION_LOCK.release()


def _collector_takeover(message, cfg):
    """None when the emulator is idle. The standalone harvest collector(s) when
    they are its only owners (a requested run may take over). Anything else is
    refused with the shared owner body."""
    rows = _procs()
    found = _owners_now(cfg, rows)
    if not rows and not found:
        _require_lease_free(cfg, message)
        return None
    if harvest_api.only_collectors(found) and all(owner['kind'] == 'operation' for owner in found):
        return found
    raise DeviceOwned(_device_owned_body(message, found, rows, code='device_owned'))


def _transfer_from_collector(cfg, body, owners):
    """Hand the emulator from the standalone collector to the requested run.

    Called only after the run's readiness and settings are proven. The run's own
    pre-acceptance checks (selected context, setup-battle guard) are repeated
    here, BEFORE the collector's input stops, so a refusal leaves it untouched.
    The choice is preserved; the battle is never ended. If the collector does
    not release the emulator in time the run is refused (409 collector_stopping)
    and nothing was launched."""
    from runtime import device_lease
    battle_api.refuse_if_blocked(_setup_battle_guard(cfg))
    command = {key: value for key, value in body.items() if key != 'idempotency_key'}
    operation_api.verify_expected(operation_api.context(ROOT, cfg, command, _profile_path(cfg.get('active_profile'))), command)
    identity = harvest_api.identity_for(cfg)
    harvest_api.begin_transfer(ROOT, identity, 'run_start')
    stop = harvest_api.stop_collectors(ROOT, identity, select=lambda op: op['request'].get('harvest') is True)
    released, waited = harvest_api.wait_for_release(
        ROOT, identity, timeout=_TRANSFER_WAIT_SEC, lease_free=lambda: device_lease.lease_free(
            identity['serial'], identity['adb_host'], identity['adb_port']))
    if not released:
        owner = owners[0]   # the hold expires on its own, so a lagging collector is not relaunched under the run
        raise DeviceOwned(dict(ok=False, code='collector_stopping',
                               error='The diamond collector is still stopping. Wait for it to release the emulator, then start the run again.',
                               owner=owner, recovery=owner['recovery'], retry='after_confirmed_stop',
                               stop=stop, waited_sec=waited))
    return dict(stopped=stop['stopped'], stopping=stop['stopping'], waited_sec=waited)


def _launch_operation(cfg, kind, body, cmd, *, restrict=None, **kwargs):
    """Accept an operation and start its worker. `restrict` (a takeover.Restriction, runs only) makes the envelope the restricted one of a takeover: not
    forwarded in kwargs, which go to Popen."""
    if restrict is not None and kind != 'run':
        raise ValueError('Only a run can be launched as a takeover')
    if kind in ('run', 'gems'):
        import copy
        from player.run_controls import effective_preset, resolve
        body = copy.deepcopy(body)
        selected = body.get('preset')
        preset = cfg.get('presets', {}).get(selected)
        if preset is None:
            raise ValueError('Run was not compiled before acceptance')
        preset = effective_preset(cfg, selected)
        frozen = copy.deepcopy(cfg)
        frozen['preset'] = selected
        profile = frozen.pop('_profile_snapshot', None)
        revision = frozen.pop('_profile_revision', None)
        controls = resolve(preset, frozen)
        attach = None
        if restrict is not None:
            preset, controls, frozen, attach = takeover_api.restrict_parts(preset, controls, frozen, selected, restrict)
        body['battle_envelope'] = dict(preset_key=selected, preset=copy.deepcopy(preset),
                                      config=frozen, profile=profile,
                                      controls=controls,
                                      source=copy.deepcopy(preset.get('_source')),
                                      source_revision=revision)
        if attach is not None:
            body['battle_envelope']['attach'] = attach
    # NO_WINDOW alone, never DETACHED_PROCESS: a uv venv's pythonw.exe is the same
    # console trampoline as its python.exe and starts the base python.exe. Detached,
    # the trampoline has no console to hand down, so that python.exe opened a new,
    # visible one (a Windows Terminal titled pythonw.exe on every collector start,
    # 2026-10-28). NO_WINDOW gives the worker its own windowless console, which the
    # interpreter inherits; it is not the dashboard's, so the worker still outlives it.
    return operation_api.launch(ROOT, cfg, kind, body, cmd, subprocess.Popen,
                                profile_path=_profile_path(cfg.get('active_profile')),
                                cwd=ROOT, creationflags=NO_WINDOW,
                                **kwargs)


class _FlowRegistryUnreadable(Exception):
    """The flow registry could not be read while building a run's command; the message is the registry's own."""


def _run_command(cfg, inst, preset):
    """The worker command line of a run: the compiled preset's runner and arguments. `cfg['presets']` holds the compiled runs."""
    runner = cfg["presets"].get(preset, {}).get("runner") or "orchestrator.py"
    args = cfg["presets"].get(preset, {}).get("runner_args") or []
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    # combo.py schedules everything itself and takes no --preset. Flow
    # scripts take `--preset bp_<name>` (compiled blueprints) ONLY -
    # started under their own legacy config preset they bind it
    # themselves (FLOW["legacy_preset"]), and the quest runners refuse
    # the legacy name at argparse, which read as "runner failed" in the
    # UI (user, 2026-08-27).
    preset_argv = ["--preset", preset]
    if os.path.basename(runner) == "combo.py":
        preset_argv = []
    elif runner.replace("\\", "/").startswith("flows/"):
        try:
            import flows
            if any(spec.get("runner") == runner
                   and spec.get("legacy_preset") == preset
                   for spec in flows.flows().values()):
                preset_argv = []
        except Exception as e:          # noqa: BLE001 - shown verbatim
            raise _FlowRegistryUnreadable(str(e)) from e
    return ([pyw, os.path.join(ROOT, runner), "--instance", inst]
            + preset_argv + [str(a) for a in args])


# ------------------------------------------------------------------ takeover
def _takeover_owners_view(cfg):
    """Everything that holds the emulator, read-only (a GET retires no operation): ledger owners and untracked runners, then the device lease."""
    rows = operation_api.project_processes(ROOT, _procs_cached(), reconcile=False)
    found = harvest_api.owners(ROOT, cfg, rows)
    if not rows and not found:
        holder = harvest_api.lease_owner(cfg)
        found = [holder] if holder is not None else []
    return found


def _attached_readiness(cfg, preset):
    from player import readiness
    return readiness.check(ROOT, cfg, preset, attached=True)


def _takeover_ports():
    return takeover_api.Ports(
        root=ROOT, compiled_runs=_compiled_runs, runnable=runnable_presets, setup_guard=_setup_battle_guard, readiness=_attached_readiness,
        owners=lambda cfg: _collector_takeover("Automation is already running. Finish or stop it before taking over the battle.", cfg),
        owners_view=_takeover_owners_view)


@app.get('/api/takeover/preview')
def api_takeover_preview():
    """The one read of the takeover dialog: verdicts, plan, policy revision, readiness, owners and the collector's battle. Never polled; a GET changes nothing."""
    return jsonify(takeover_api.preview(_takeover_ports(), load_config(), request.args.get('preset') or None))


def _takeover_action(body, cfg, inst):
    """POST /api/control action=takeover: attach a run's restricted policy to the battle on screen. Admission is takeover.admit (every check before the collector's
    input stops); the transfer and the launch are the ones `start` uses, with the restricted envelope. An identical retry replays the first receipt."""
    request_ = takeover_api.parse(body)
    # no ledger file means no earlier receipt: looking would create it, and a refusal records nothing
    retry = (operation_api.retry(ROOT, cfg, 'run', body, _profile_path(cfg.get('active_profile')))
             if operation_api.ledger_path(ROOT).is_file() else None)
    if retry:
        return jsonify(ok=True, operation_id=retry['id'], operation=operation_api.public(retry))
    admitted = takeover_api.admit(_takeover_ports(), cfg, request_)
    try:
        cmd = _run_command(cfg, inst, admitted.preset)
    except _FlowRegistryUnreadable as e:
        return jsonify({"ok": False, "error": f"flow registry unreadable: {e}"}), 500
    transfer, keep_hold = None, False
    try:
        transfer = _transfer_from_collector(cfg, body, admitted.owners) if admitted.owners else None
        operation = _launch_operation(cfg, 'run', body, cmd, restrict=admitted.restrict)
    except DeviceOwned as refused:
        keep_hold = refused.body.get('code') == 'collector_stopping'      # as `start`: the hold expires on its own, so a lagging collector is not relaunched
        raise
    finally:
        if admitted.owners and not keep_hold:
            harvest_api.end_transfer(ROOT, harvest_api.identity_for(cfg))
    _procs_refresh()
    return jsonify(ok=True, operation_id=operation['id'], operation=operation_api.public(operation),
                   **({'transfer': transfer} if transfer else {}))


@app.get('/api/operations/<operation_id>')
def api_operation_status(operation_id):
    try:
        return jsonify(operation=operation_api.public(operation_api.stored(ROOT, operation_id)))
    except KeyError:
        return jsonify(ok=False, error='Unknown operation'), 404


def _kill_action(body):
    """POST /api/control action=kill: stop the runner THIS person was shown, and only that one.

    The request names the runner the way the list drew it: its pid AND the creation time (`created`)
    the row carried. A pid alone names nothing, the operating system reuses it. A fresh scan must
    still list that pid with that same creation time (a pid that passed to another runner after the
    page drew the row is refused), and the stop acts on the one psutil.Process that was verified
    against it (_kill_runner). No process with the pid at all is `gone`; a missing or different
    creation time is refused 409 process_identity_unverified; a pid that is no listed runner is
    refused 400 not_a_runner. Nothing is signalled on any refusal."""
    from runtime import process_identity
    pid = body.get("pid")
    if not process_identity.usable_pid(pid):
        return jsonify(ok=False, code='invalid_request', error='pid must be the integer process id of a listed runner'), 400
    row = next((p for p in operation_api.project_processes(ROOT, _procs()) if p["pid"] == pid), None)
    if row is None:
        if pid not in operation_api.inline_owner_pids(ROOT) and process_identity.pid_absent(pid):
            _procs_refresh()
            return jsonify({"ok": True, "gone": True})          # no process has this pid: the runner that was listed is gone
        return jsonify(ok=False, code='not_a_runner', error="not a runner pid"), 400
    if row.get('operation'):
        raise operation_api.Conflict('Cancel the tracked operation by its ID and revision')
    return _kill_runner(row, body.get("created"))


def _kill_runner(row, seen):
    """Stop the listed runner `row` if it is the one whose creation time the person saw (`seen`).

    The scan row and the request must agree on the creation time. The pid is then opened ONCE and
    judged against it, and the process is terminated through that same kernel handle
    (runtime.process_handle): a pid that passes to another process at any moment is never
    signalled. A runner that exited since the scan answers gone and signals nothing; one whose
    identity cannot be proven is refused (409) and signals nothing. Only this process is stopped
    (its descendants are not touched), as before."""
    from runtime import process_handle, process_identity
    pid = row["pid"]
    if not process_identity.same_created(row.get("created"), seen):
        return _kill_unverified(pid)
    # terminate, WAIT for it: the reply must not go out while the process is still in the table - the
    # UI refreshes on reply and would show the row it just "killed" (user, 2026-08-18).
    stop = process_handle.terminate_tree(pid, seen, descendants=False, timeout=5.0)
    if stop.state == 'unverified':
        return _kill_unverified(pid)
    if not stop.confirmed:
        if any(error.get('code') == process_handle.ERROR_ACCESS_DENIED for error in stop.errors):
            return jsonify(ok=False, code='process_access_denied', error=f"pid {pid} could not be stopped (access denied)"), 500
        return jsonify({"ok": False, "error": f"pid {pid} would not die"}), 500
    _procs_refresh()
    return jsonify({"ok": True, "gone": True})     # stopped now, or already gone (its pid may belong to another process)


def _kill_unverified(pid):
    return jsonify(ok=False, code='process_identity_unverified',
                   error=f"pid {pid} could not be proven to be the runner you were shown, so nothing was stopped; "
                         "refresh the list and try again"), 409


@app.post('/api/operations/<operation_id>/cancel')
def api_operation_cancel(operation_id):
    body = request.get_json(silent=True)
    body = dict(body) if isinstance(body, dict) else {}
    body['operation_id'] = operation_id
    return _person_stop(body)


HOLDER_REASONS = ('worker_not_verifiable_lease_held', 'lease_held_after_stop')


def _person_stop(body):
    """A person's Stop - the one implementation behind POST /api/operations/<id>/cancel, /api/scan/stop and
    /api/calibrate/stop. It runs WITHOUT the global action lock (see _is_revocation), so every step is a
    compare-and-set in the ledger or a handle-bound kernel call, and what it did is tracked in `effects`:

      * a refusal that happened before anything was done (a stale revision, an unknown or uncancellable
        operation) is 409 with a stable `code` and the current operation;
      * once anything was done (harvesting paused, the stop recorded, a process signalled, a worker
        released) a conflict is NEVER a plain refusal: the stop is finished from the operation's current
        state when it can be (operation_api.stop_driver does), otherwise 409 `stop_outcome_partial` lists
        what this request did;
      * every answer says what THIS request did (`stop.by_this_request`), so an operation that another
        writer settled a moment after this request stopped its worker is not reported as one nobody stopped."""
    effects = operation_api.stop_effects()
    try:
        operation = operation_api.require_revision(ROOT, body, ('accepted', 'running', 'cancelling'))
        if operation['kind'] in ('run', 'gems'):
            # A person's Stop is final for the automatic collector too: pause the
            # choice before cancelling, or the lifecycle host would undo the Stop.
            context = operation['context']
            harvest_api.pause(ROOT, {key: context.get(key) for key in ('device_key', 'instance', 'account', 'serial', 'profile')},
                              'stopped_by_person', f"stop-{operation['id']}", effects=effects)
        stopped = operation_api.cancel(ROOT, body, release_unverified=True, effects=effects)
    except operation_api.Conflict as exc:
        return _stop_conflict(exc, effects, body.get('operation_id'))
    return jsonify(ok=True, **_stop_view(stopped, effects))


def _stop_view(operation, effects):
    """{operation, stop} for a Stop's answer: the operation and how the stop stands (operation_api.stop_state)
    plus what this request did. A worker that can never be verified is released here, but only while
    nothing holds the device lease; while some process does, `stop.holder` names it with the same owner body
    the shared device_owned refusal uses (owner pid/script stay null: the lease is not a process row;
    lease.holders lists the live processes its lock files name), otherwise it is null. The same body is given
    when the worker is gone or was stopped but the lease is still held (reason lease_held_after_stop): the
    stop is then not confirmed."""
    stop = operation_api.stop_state(operation)
    holder = None
    if stop['reason'] in HOLDER_REASONS:
        context = operation.get('context') or {}
        try:
            holder = harvest_api.lease_holder(dict(serial=context.get('serial'), adb_host=context.get('adb_host', '127.0.0.1'),
                                                   adb_port=context.get('adb_port', 5037)))
        except harvest_api.OwnersUnreadable:
            holder = None   # the lease cannot be read now; the reason will say unreadable on the next ask
    return dict(operation=operation_api.public(operation),
                stop=dict(stop, holder=holder, by_this_request=dict(effects, signalled=list(effects['signalled']))))


def _stop_conflict(exc, effects, operation_id):
    """The 409 for a Stop that hit a Conflict. Nothing done yet: a coded refusal (OperationRefused.code, else
    stop_refused) with the operation as it stands. Something done: stop_outcome_partial with the current
    operation, how the stop stands now and what this request did - it never reads as 'nothing happened'."""
    current = getattr(exc, 'operation', None)
    try:
        current = operation_api.stored(ROOT, operation_id) if operation_id else current
    except Exception:   # noqa: BLE001 - best effort: the refusal is reported with whatever the conflict itself carried
        pass
    if not operation_api.effects_taken(effects):
        return jsonify(ok=False, code=getattr(exc, 'code', 'stop_refused'), error=str(exc),
                       operation=None if current is None else operation_api.public(current)), 409
    done = [text for taken, text in ((effects['paused'], 'harvesting was paused'),
                                     (effects['requested'], 'the stop was recorded'),
                                     (effects['signalled'], f"{len(effects['signalled'])} process(es) were signalled"),
                                     (effects['released'], 'the worker was released')) if taken]
    body = dict(ok=False, code='stop_outcome_partial', conflict=str(exc),
                error=f"The stop was only partly carried out ({', '.join(done)}) and then the operation changed: {exc}. "
                      'Read its current state before stopping again.')
    body.update(_stop_view(current, effects) if current is not None else dict(operation=None, stop=None))
    return jsonify(body), 409


def _battle_device(cfg):
    return operation_api.context(ROOT, cfg, {}, None)['device_key']


def _battle_history_context():
    cfg = load_config()
    instance = request.args.get('instance')
    if instance:
        if instance not in cfg.get('instances', {}):
            raise ValueError('Unknown emulator')
        cfg['active_instance'] = instance
    context = operation_api.context(ROOT, cfg, {}, None)
    return {key: context.get(key) for key in ('device_key', 'instance', 'account', 'serial')}


@app.get('/api/schedule/status')
def api_schedule_status():
    from control import schedule_status
    from player import accounts
    try:
        cfg = load_config()
        instance = request.args.get('instance') or cfg.get('active_instance', 'main')
        if instance not in cfg.get('instances', {}):
            raise ValueError('Unknown emulator')
        cfg['active_instance'] = instance
        cfg = accounts.effective(cfg)
        context = operation_api.context(ROOT, cfg, {}, None)
        return jsonify(schedule_status.read(ROOT, **{key: context.get(key) for key in
                       ('instance', 'account', 'serial', 'profile', 'device_key')}))
    except ValueError as exc:
        return jsonify(ok=False, availability='unavailable', error=str(exc)), 400


@app.get('/api/actions/executions')
def api_action_executions():
    """The recorded executions of every timed action of the selected emulator and account, newest first. Read-only:
    nothing is created or settled here, and a checkout with no history answers an empty list."""
    from control import actions as actions_api
    try:
        context = _battle_history_context()
        return jsonify(actions_api.executions(
            ROOT, context, action=request.args.get('action'), instance=request.args.get('action_instance'),
            battle_id=request.args.get('battle_id'), before=request.args.get('before'), limit=request.args.get('limit')))
    except ValueError as exc:
        return jsonify(ok=False, code='invalid_request', error=str(exc)), 400


@app.get('/api/battles/active')
def api_battle_active():
    from control import battles
    return jsonify(battles.active(ROOT, _battle_device(load_config())))


@app.get('/api/battles')
def api_battle_history():
    from control import battles
    store = battles.ledger(ROOT)
    try:
        context = _battle_history_context()
        device = context['device_key']
        limit, offset = int(request.args.get('limit', 50)), int(request.args.get('offset', 0))
        rows = store.list(limit=limit, offset=offset, device=device,
                          status=request.args.get('status') or None, context=context)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    return jsonify(battles=[battles.public(store, row) for row in rows], context=context)


@app.get('/api/battles/<battle_id>')
def api_battle_detail(battle_id):
    from control import battles
    store = battles.ledger(ROOT)
    try:
        context = _battle_history_context()
        row = store.get(battle_id)
        if any(row['context'].get(key) != value for key, value in context.items()):
            return jsonify(ok=False, error='Battle does not belong to the selected emulator and account'), 404
        events = [dict(event, t=event['timestamp']) for event in store.events(battle_id)]
        from control import actions as actions_api
        return jsonify(battle=battles.public(store, row), events=events, context=context,
                       executions=actions_api.for_battle(ROOT, row))
    except KeyError:
        return jsonify(ok=False, error='Unknown battle'), 404
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post('/api/operations/<operation_id>/live-controls')
@app.post('/api/operations/<operation_id>/actions')
def api_battle_command(operation_id):
    # Contract of this route:
    #  * 400/404/409 mean "refused, nothing recorded". Only pre-admission
    #    failures may answer them, including an unknown key reused for another
    #    request and a driver that is no longer active for a NEW key.
    #  * an identical request that was already recorded is answered from its
    #    receipt (200) even after the driver or battle ended, so a client that
    #    lost the first reply learns the outcome; the same key with any other
    #    request stays a 409 conflict.
    #  * once a command is recorded, any failure to read it back is a 5xx
    #    command_outcome_unknown, never a refusal. The client's retry rule is
    #    unchanged: resend the SAME request to read the outcome back.
    from control import battles
    try:
        result = battles.command(ROOT, operation_id, request.get_json(silent=True),
                                 action=request.path.endswith('/actions'))
    except battles.CommandOutcomeUnknown as exc:
        cause = exc.__cause__
        _diagnostic_event('battle_command_outcome_unknown', operation_id=operation_id,
                          command_id=exc.command_id, outcome='unknown',
                          exception_type=type(cause).__name__, exception_message=str(cause))
        return jsonify(ok=False, code='command_outcome_unknown', outcome='unknown',
                       command_id=exc.command_id, error=str(exc)), 500
    except KeyError:
        return jsonify(ok=False, error='Unknown battle or operation'), 404
    except operation_api.Conflict:
        raise
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    command = result['command']
    _diagnostic_event('battle_command_accepted', operation_id=operation_id,
                      battle_id=result['battle']['id'], command_id=command['id'],
                      command_kind=command['kind'], action=command['payload'].get('action'),
                      control_revision=result['control_revision'], outcome='accepted')
    return jsonify(result)


@app.route('/api/run-controls', methods=['GET', 'POST'])
def api_run_controls():
    from control import run_workspace
    from player import playerprofile as pp
    cfg = load_config()
    name = cfg.get('active_profile')
    from player import accounts
    context = dict(instance=cfg.get('active_instance', 'main'), account=accounts.identity(cfg), profile=name)
    path = _profile_path(name)
    if not path or not os.path.isfile(path):
        return jsonify(ok=False, error='Choose a saved profile first'), 400
    body = request.get_json(silent=True) if request.method == 'POST' else request.args
    if body is None or not hasattr(body, 'get'):
        return jsonify(ok=False, error='Settings must be an object'), 400
    run = body.get('blueprint', body.get('run'))
    if isinstance(run, str) and run.startswith('bp_'):
        run = run[3:]
    if request.method == 'POST' and (_is_starter(name) or _is_draft(name) or _foreign_profile(name, cfg)):
        return jsonify(ok=False, error='Make or select your own editable profile first'), 409
    if request.method == 'POST' and body.get('context') != context:
        raise operation_api.Conflict('Account, emulator or profile changed; reopen the run before editing')
    with _profile_lock(path), _COMPILER_LOCK:
        revision = operation_api.file_revision(path)
        if request.method == 'POST' and body.get('expected_revision') != revision:
            raise operation_api.Conflict('Saved run changed; refresh before editing again')
        profile = yaml.safe_load(Path(path).read_bytes())
        profile['_name'] = name
        previous = pp.CONFIG
        pp.CONFIG = cfg
        try:
            if request.method == 'POST':
                result = run_workspace.update(profile, cfg, run, body.get('changes', body.get('patch')))
                updated = result.pop('profile')
                problems = pp.validate(updated)
                if problems:
                    raise ValueError('; '.join(problems))
                updated.pop('_name', None)
                _save_yaml_backup(path, updated)
                revision = operation_api.file_revision(path)
            else:
                result = run_workspace.read(profile, cfg, run)
            return jsonify(ok=True, context=context, profile_revision=revision, **result)
        except (ValueError, pp.ProfileError) as exc:
            return jsonify(ok=False, error=str(exc)), 400
        finally:
            pp.CONFIG = previous


def _requested_operation(cfg, kind, *, reconcile=True):
    """The operation a request names, or the latest of this kind for the selected
    emulator and account. reconcile=False (every GET) returns it as recorded and
    changes nothing; a POST retires a worker that died, as the refresher does."""
    read = operation_api.resolve if reconcile else operation_api.stored
    operation_id = request.args.get('operation_id')
    if operation_id:
        try:
            op = read(ROOT, operation_id)
        except KeyError:
            raise operation_api.OperationRefused('operation_unknown', 'Unknown operation') from None
        if op['kind'] not in kind:
            raise operation_api.OperationRefused('operation_kind_mismatch', 'Operation belongs to another workflow')
        return op
    ctx = operation_api.context(ROOT, cfg, {}, _profile_path(cfg.get('active_profile')))
    if reconcile:
        op = operation_api.ledger(ROOT).latest(ctx, kind)
    else:
        op = operation_api.Operations.latest_read_only(operation_api.ledger_path(ROOT), ctx, kind)
    if op and op['kind'] in kind:
        return read(ROOT, op['id'])
    return None

# Serialize dashboard mutations, including the short clicker worker. A config
# or account switch must not race a screenshot-verified click on the old device.
_ACTION_LOCK = threading.Lock()


def _diagnostic_event(kind, **fields):
    from control.diagnostics import log_root
    from runtime import diagnostics
    return diagnostics.event_to(log_root(ROOT) / '_dashboard', kind, source='dashboard', **fields)


def _correlation_id(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{8,100}', value) else uuid.uuid4().hex


@app.before_request
def begin_request_diagnostics():
    if not request.path.startswith('/api/'):
        return
    from runtime import diagnostics
    request_id = _correlation_id(request.headers.get('X-Request-ID'))
    trace_id = _correlation_id(request.headers.get('X-Trace-ID', request_id))
    g.diagnostic_started = time.monotonic()
    g.diagnostic_ids = {'request_id': request_id, 'trace_id': trace_id}
    g.diagnostic_scope = ExitStack()
    g.diagnostic_scope.enter_context(diagnostics.context(**g.diagnostic_ids,
        method=request.method, route=request.path,
        **{key: value for key, value in (request.view_args or {}).items()
           if key in ('operation_id', 'battle_id')}))
    # Polls and frames are measured at their own boundaries. Log their failures,
    # but don't let a healthy preview bury the actual attempts in request noise.
    g.diagnostic_record_request = request.path not in {
        '/api/status', '/api/calibrate/status', '/api/battles/active',
        '/api/stream.mjpg', '/api/stream/status', '/api/frame.png',
        '/api/diagnostics/events', '/api/diagnostics/client'}
    if g.diagnostic_record_request:
        _diagnostic_event('request_started')


@app.after_request
def finish_request_diagnostics(response):
    if not hasattr(g, 'diagnostic_ids'):
        return response
    response.headers['X-Request-ID'] = g.diagnostic_ids['request_id']
    response.headers['X-Trace-ID'] = g.diagnostic_ids['trace_id']
    payload = response.get_json(silent=True) if response.is_json else None
    failure = response.status_code >= 400 or (isinstance(payload, dict) and payload.get('ok') is False)
    if g.diagnostic_record_request or failure:
        fields = dict(status_code=response.status_code, outcome='failed' if failure else 'completed',
                      duration_ms=round((time.monotonic()-g.diagnostic_started)*1000, 3))
        if failure and isinstance(payload, dict):
            fields['error'] = str(payload.get('error') or payload.get('message') or payload.get('problems') or 'Request refused')[:2000]
        _diagnostic_event('request_finished', **fields)
    return response


@app.teardown_request
def close_request_diagnostics(error):
    if error is not None and hasattr(g, 'diagnostic_ids'):
        _diagnostic_event('request_exception', outcome='error', exception_type=type(error).__name__,
                          exception_message=str(error),
                          traceback=''.join(traceback.format_exception(type(error), error, error.__traceback__)))
    scope = getattr(g, 'diagnostic_scope', None)
    if scope is not None:
        g.diagnostic_scope = None
        scope.close()


@app.before_request
def local_requests_only():
    """Refuse, before any other hook or route runs: a request whose Host
    header names anything but a loopback / bind host (421), and a POST/PUT/
    PATCH/DELETE whose Origin header is present and is not this dashboard's
    own `http://<Host>` (403) - a web page the user visits sends its own
    Origin. A request with no Origin header (curl, scripts, a typed URL)
    is not refused by the Origin check."""
    if urllib.parse.urlsplit("//" + request.host).hostname not in _LOCAL_HOSTS:
        return jsonify({"ok": False, "error": "This dashboard only answers on 127.0.0.1 / localhost"}), 421
    origin = request.headers.get("Origin")
    if (request.method not in ("GET", "HEAD", "OPTIONS") and origin is not None
            and origin.casefold() != ("http://" + request.host).casefold()):
        return jsonify({"ok": False, "error": "Cross-site request refused"}), 403


@app.after_request
def deny_framing(response):
    """`X-Frame-Options: DENY` on every response, refusals included: no page
    may frame the dashboard and click Start / allow taps through an invisible
    overlay. No first-party page uses an <iframe>."""
    response.headers["X-Frame-Options"] = "DENY"
    return response


PERSON_STOP_RULES = frozenset({'/api/operations/<operation_id>/cancel', '/api/scan/stop', '/api/calibrate/stop'})


def _is_revocation():
    """Turning harvesting off, tap permission off, or a person's Stop. They stay reachable while
    another action runs; each is a durable, fenced change of its own, so it needs
    no help from the global lock. Everything else stays serialized.

    A Stop is safe without the lock because every step of it is its own atomic act: the stop is recorded
    by a compare-and-set on the operation's revision (a stale revision is a coded 409 and nothing is
    done), the processes are signalled through kernel handles verified against the recorded identity
    (runtime.process_handle), the final settlement is a compare-and-set that a concurrent writer - the
    refresher retiring a worker it saw die, a second Stop - can win without the Stop being lost, and an
    unverifiable worker is released only while the device lease is free at the moment of the write.
    The refresher tick no longer waits for a Stop, and neither does the Stop for it."""
    rule = request.url_rule
    if rule is not None and request.method == 'POST' and rule.rule in PERSON_STOP_RULES:
        return True
    if request.path not in ('/api/harvest', '/api/automation-access'):
        return False
    body = request.get_json(silent=True)
    return isinstance(body, dict) and body.get('enabled') is False


@app.before_request
def serialize_actions():
    if request.path == '/api/diagnostics/client':
        return
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if _is_revocation():
            return
        if not _ACTION_LOCK.acquire(blocking=False):
            # Transient and pre-admission: this request evaluated and recorded
            # nothing. The code tells a retry apart from a definite refusal;
            # status and message stay (learning-controller matches the prefix).
            return jsonify({"ok": False, "code": "action_lock",
                            "error": "Another action is in progress; wait for its result"}), 409
        g.action_lock = True


@app.teardown_request
def release_action_lock(_error):
    if getattr(g, "action_lock", False):
        g.action_lock = False
        _ACTION_LOCK.release()


@app.get('/api/diagnostics/events')
def api_diagnostic_events():
    from control import diagnostics
    from runtime.diagnostics import health
    try:
        return jsonify(**diagnostics.query(ROOT, request.args), writer_health=health())
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post('/api/diagnostics/client')
def api_diagnostic_client():
    raw = request.stream.read(16 * 1024 + 1)
    if len(raw) > 16 * 1024:
        return jsonify(ok=False, error='Diagnostic event is too large'), 413
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        return jsonify(ok=False, error='Diagnostic event must be JSON'), 400
    if not isinstance(body, dict) or body.get('kind') not in ('api_error', 'javascript_error', 'unhandled_rejection'):
        return jsonify(ok=False, error='Unsupported browser diagnostic event'), 400
    fields = {key: str(body[key])[:limit] for key, limit in
              (('message', 2000), ('stack', 8000), ('path', 500), ('page', 100), ('method', 20)) if key in body}
    original = body.get('request_id')
    if isinstance(original, str) and re.fullmatch(r'[A-Za-z0-9_-]{8,100}', original):
        fields.update(request_id=original, trace_id=original, reported_by_request_id=g.diagnostic_ids['request_id'])
    _diagnostic_event('browser_' + body['kind'], outcome='error', **fields)
    return jsonify(ok=True), 202


@app.get("/api/clicker/actions")
def api_clicker_actions():
    from player.clicker import ACTIONS
    return jsonify({"actions": [{"id": key, **value} for key, value in ACTIONS.items()]})


@app.post("/api/clicker/step")
def api_clicker_step():
    from player.clicker import ACTIONS
    body = _request_object()
    action = body.get("action")
    cfg = load_config()
    inst = cfg.get("active_instance", "main")
    if (not isinstance(action, str) or action not in ACTIONS or body.get("instance") != inst
            or body.get("account") != cfg.get("instances", {}).get(inst, {}).get("account")):
        raise InvalidRequest("Invalid action or the active emulator changed")
    execute = body.get("execute", False)
    if type(execute) is not bool:
        raise InvalidRequest("execute must be true or false", 'invalid_execute')
    _require_idle("Stop automation or calibration before using the clicker")
    if execute and not _taps_allowed(cfg):
        return jsonify({"ok": False, "code": "taps_disabled", "error": "Allow automation taps in Control first"}), 409
    args = [sys.executable.replace("pythonw.exe", "python.exe"),
            os.path.join(ROOT, "player", "clicker.py"), "--instance", inst, "--action", action]
    if execute:
        args.append("--execute")
    try:
        result = _run(args, cwd=ROOT, capture_output=True, text=True, timeout=45)
        lines = result.stdout.strip().splitlines()
        data = json.loads(lines[-1]) if lines else {"ok": False, "reason": "Clicker worker returned no result"}
        return jsonify({"ok": True, "result": data})
    except (subprocess.TimeoutExpired, ValueError) as exc:
        return jsonify({"ok": False, "error": "Check did not finish. Inspect the game before trying again: " + str(exc)[:150]})


@app.get("/api/clicker/frame")
def api_clicker_frame():
    path = os.path.join(_calibration_dir(load_config()), "clicker_preview.png")
    if not os.path.isfile(path):
        return jsonify(ok=False, code='no_preview', error='The clicker has not written a preview yet'), 404
    return send_file(path, mimetype="image/png", max_age=0)


@app.get("/api/manifest/contribution")
def api_manifest_contribution():
    from player.bootstrap_layout import manifest
    from player.manifest_contribution import export, discovery_candidates
    try:
        with open(os.path.join(_calibration_dir(load_config()), "calibrate_report.json"), encoding="utf-8") as fh:
            report = json.load(fh)
    except (OSError, ValueError):
        report = {}
    payload = export(manifest(), report if isinstance(report, dict) else {})
    try:
        directory = _calibration_dir(load_config())
        with open(os.path.join(directory, 'screen_analysis.json'), encoding='utf-8') as fh:
            analysis = json.load(fh)
        names = set()
        for path in glob.glob(os.path.join(directory, 'asset_library', '*', 'index.json')):
            with open(path, encoding='utf-8') as fh:
                names.update(item['name'] for item in json.load(fh).get('images', []) if item.get('name'))
        payload['discovery_candidates'] = discovery_candidates(manifest(), analysis, names)
    except (OSError, ValueError):
        payload['discovery_candidates'] = []
    return Response(json.dumps(payload, indent=2), mimetype="application/json",
                    headers={"Content-Disposition": 'attachment; filename="manifest-contribution.json"'})

# Known emulator adb locations, expanded per-drive. BlueStacks 5 ships its
# own daemon as HD-Adb.exe; MuMu under nx_main; a PATH adb catches the rest.
EMULATOR_ADB_CANDIDATES = [
    ("MuMu Player", r"{pf}\Netease\MuMuPlayer\nx_main\adb.exe"),
    ("MuMu Player 12", r"{pf}\Netease\MuMu Player 12\shell\adb.exe"),
    ("BlueStacks 5", r"{pf}\BlueStacks_nxt\HD-Adb.exe"),
    ("BlueStacks (msi2)", r"{pf}\BlueStacks_msi2\HD-Adb.exe"),
    ("LDPlayer", r"{pf}\LDPlayer\LDPlayer9\adb.exe"),
    ("PATH adb", "adb"),
]

# P0 (2026-08-18): runnable presets are DERIVED from config.yaml instead of
# a hardcoded allowlist that drifted every time a preset was added. A preset
# is startable unless it is an explicit placeholder (defined: false).
def runnable_presets(cfg: dict) -> list[str]:
    from control.settings_catalog import runnable_presets as derived     # one definition: the catalogue offers the same list as choices
    return derived(cfg)


_COMPILER_LOCK = threading.RLock()
_CONFIG_WRITE_LOCK = threading.RLock()


def _serialize_config(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with _CONFIG_WRITE_LOCK:
            return fn(*args, **kwargs)
    return wrapped


def _fenced_by_page_context(fn):
    """The optional context fence of a write whose effect depends on which emulator, account and profile is in force
    (accounts, tier, profile files, a profile or loadout patch, the Recognition page's apply of saved observations). A page that
    composed its request for one context sends it as
    `expected_context` {instance, account, profile, serial}; the request is refused 409 context_changed, writing nothing,
    when the configuration now names another. The check is the first thing after the body is read - before the owner refusal,
    the validators and the revision checks - and is made under _CONFIG_WRITE_LOCK, the lock every account or profile switch
    holds, against the saved configuration read under that lock. The fence only READS it: a missing file is not seeded from the
    example (a refusal writes nothing), and a configuration that cannot be resolved - missing, unreadable, or a stored shape the
    accounts layer cannot interpret - names no context, so it is refused the same way, `current_context` null. The lock is held until the handler has written or refused, so a
    switch cannot land between the check and the write (within this process every other non-revocation write also holds
    _ACTION_LOCK; another process editing config.yaml is excluded by no lock, as before). No `expected_context`: the request
    runs exactly as it always did."""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            expected = operation_api.expected_page_context(request.get_json(force=True, silent=True))
        except ValueError as exc:
            raise InvalidRequest(str(exc)) from None
        if expected is None:
            return fn(*args, **kwargs)
        with _CONFIG_WRITE_LOCK:
            try:
                current = operation_api.page_context(_read_saved_config())
            except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError) as exc:   # missing file, unknown account, a stored shape that is not a configuration
                raise operation_api.ContextChanged(None) from exc
            operation_api.require_page_context(expected, current)
            return fn(*args, **kwargs)
    return wrapped


def _profile_check(profile, cfg=None, warnings=False):
    from player import playerprofile as pp
    with _COMPILER_LOCK:
        previous = pp.CONFIG
        pp.CONFIG = cfg if cfg is not None else load_config()
        try:
            return (pp.warnings if warnings else pp.validate)(profile)
        finally:
            pp.CONFIG = previous


def _profile_warnings(profile):
    return _profile_check(profile, warnings=True)


def _compiled_runs(cfg):
    """Compile the active account without binding settings to its device."""
    from player import playerprofile as pp
    import flows
    name = cfg.get("active_profile")
    if not name:
        return dict(cfg.get("presets") or {})
    path = _profile_path(name)
    if not path or not os.path.isfile(path):
        raise ValueError("Choose a valid profile in Setup")
    profile_bytes = Path(path).read_bytes()
    profile = yaml.safe_load(profile_bytes)
    profile["_name"] = name
    with _COMPILER_LOCK:
        previous = pp.CONFIG
        pp.CONFIG = cfg
        try:
            problems = pp.validate(profile)
            if problems:
                raise ValueError("; ".join(problems))
            out = dict(cfg.get("presets") or {})
            for key in profile.get("blueprints") or {}:
                compiled = pp.compile_preset(profile, key)
                compiled["runner"] = flows.script(compiled["kind"])
                out["bp_" + key] = compiled
            import copy
            import hashlib
            cfg['_profile_snapshot'] = copy.deepcopy(profile)
            cfg['_profile_revision'] = hashlib.sha256(profile_bytes).hexdigest()
            plan = pp.compile_plan(profile)
            if plan is not None:
                cfg['plan'] = plan
            if profile.get("plan") and "combo" in (cfg.get("presets") or {}):
                out["combo"] = cfg["presets"]["combo"]
            else:
                out.pop("combo", None)
            return out
        finally:
            pp.CONFIG = previous


@app.get("/api/runs")
def api_runs():
    try:
        cfg = load_config()
        runs = _display_runs(cfg)
        if cfg.get("active_profile"):
            runs = {k: v for k, v in runs.items() if k.startswith("bp_") or k == "combo"}
        return jsonify({"runs": runs, "readiness": _runs_readiness(cfg, runs)})
    except Exception as e:
        return jsonify({"runs": {}, "error": str(e)}), 400


def _display_runs(cfg):
    """Keep saved runs editable before ownership checks can pass.

    Launch paths still use _compiled_runs and retain strict validation.
    """
    try:
        return _compiled_runs(cfg)
    except ValueError:
        path = _profile_path(cfg.get("active_profile"))
        if not path or not os.path.isfile(path):
            raise
        with open(path, encoding="utf-8") as fh:
            profile = yaml.safe_load(fh)
        from player import playerprofile as pp
        import flows
        import copy
        out = {}
        with _COMPILER_LOCK:
            previous = pp.CONFIG
            pp.CONFIG = cfg
            try:
                for key, bp in (profile.get("blueprints") or {}).items():
                    single = copy.deepcopy(profile)
                    single["blueprints"] = {key: bp}
                    single.pop("plan", None)
                    problems = pp.validate(single)
                    try:
                        item = pp.compile_preset(single, key)
                        item["runner"] = flows.script(item["kind"])
                    except Exception as exc:
                        item = dict(bp)
                        problems.append(str(exc))
                    item["_setup_errors"] = problems
                    out["bp_" + key] = item
            finally:
                pp.CONFIG = previous
        return out


def _combo_readiness(cfg, runs):
    """readiness.check_plan over the active profile's compiled day plan
    (playerprofile.compile_plan, the schedule combo.py walks): Combo is ready
    only when every run its plan names on any weekday is. With no active
    profile combo.py runs its own phase constants, so there is no plan run
    to check (empty week: ready)."""
    from player import playerprofile as pp, readiness
    week = {}
    if cfg.get("active_profile"):
        with open(_profile_path(cfg["active_profile"]), encoding="utf-8") as fh:
            week = pp.compile_plan(yaml.safe_load(fh))["week"]
    return readiness.check_plan(ROOT, cfg, runs, week)


def _runs_readiness(cfg, runs):
    """Per-run calibration state for the run picker: each run type needs its
    own set of images to click (its flow templates, gather features, presets,
    plus the shared recognition guards), so a run whose images exist starts
    and one whose images are missing is greyed out with the list to scan -
    independent of what any OTHER run type still lacks. `combo` is the union
    of the runs its day plan names (_combo_readiness); each of its missing
    images is prefixed with the runs and blocks that need it."""
    from player import readiness
    out = {}
    for name, body in runs.items():
        if not isinstance(body, dict):
            continue
        if body.get("_setup_errors"):
            out[name] = {"ready": False, "setup_required": True, "missing": ["Confirm this run's equipment and abilities after scanning."],
                         "details": [], "advisory": [], **_setup_next_action(cfg, body)}
            continue
        try:
            r = _combo_readiness(cfg, runs) if name == "combo" else readiness.check(ROOT, cfg, body)
            out[name] = {"ready": r["ready"],
                         "missing": [("; ".join(row["runs"]) + ": " if row.get("runs") else "")
                                     + (" or ".join(row["alternatives"]) or "; ".join(row["reasons"]))
                                     for row in r["missing"]],
                         # one row per missing image for the Control card: the
                         # plain-language card (what / where / how), why this
                         # run needs it, and which scan step covers it
                         "details": [{"alternatives": row["alternatives"], "reasons": row["reasons"],
                                      "docs": row.get("docs") or []} for row in r["missing"]],
                         "advisory": [" or ".join(row["alternatives"]) for row in r.get("advisory", [])]}
        except Exception as e:                  # noqa: BLE001 - one bad run must not hide the rest
            out[name] = {"ready": False, "missing": [f"readiness check failed: {e}"], "advisory": []}
    return out


def _setup_next_action(cfg, body):
    """One actionable step; saved discoveries are separate from run validation."""
    try:
        with open(os.path.join(_calibration_dir(cfg), "calibrate_state.json"), encoding="utf-8") as fh:
            observed = json.load(fh).get("player") or {}
        with open(_profile_path(cfg.get("active_profile")), encoding="utf-8") as fh:
            saved = (yaml.safe_load(fh) or {}).get("player") or {}
        for key in ("card_presets", "global_presets", "category_presets", "uws", "abilities"):
            value = observed.get(key)
            if value and (any(saved.get(key, {}).get(k) != v for k, v in value.items())
                          if isinstance(value, dict) else saved.get(key) != value):
                return dict(next_action="apply_scan", message="Your menu scan is complete. Save its discoveries to this account.", action_label="Use completed scan")
    except (OSError, ValueError, TypeError):
        pass
    errors = " ".join(body.get("_setup_errors") or [])
    if any(word in errors for word in ("chain_lightning", "abilities", "wall", "uws")):
        return dict(next_action="battle_setup", message="Menu setup is complete. This run still needs battle controls and abilities verified.", action_label="Prepare battle controls")
    return dict(next_action="edit_run", message="Choose equipment available on this account for this run.", action_label="Choose run equipment")


@app.get("/api/readiness")
def api_readiness():
    from player import readiness
    cfg = load_config()
    try:
        runs = _display_runs(cfg)
        name = request.args.get("preset") or next((n for n in runs if n.startswith("bp_")), None) or next((n for n in runs if n != "combo"), None)
        if not name or name not in runs or name == "combo":
            raise ValueError("Choose an individual run to check calibration")
        if runs[name].get("_setup_errors"):
            return jsonify(ready=False, preset=name, missing=[],
                           **_setup_next_action(cfg, runs[name]),
                           diagnostics=runs[name]["_setup_errors"])
        projection = readiness.check(ROOT, cfg, runs[name])
        context = operation_api.context(ROOT, cfg, {'selected_run': name}, _profile_path(cfg.get('active_profile')))
        context.pop('config', None)
        return jsonify(dict(projection, preset=name, context=context))
    except Exception as e:
        return jsonify({"ready": False, "error": str(e), "missing": []}), 400


# ------------------------------------------------------------------ config io
CONFIG_EXAMPLE = os.path.join(ROOT, "config.example.yaml")


def seed_config() -> bool:
    """Create config.yaml from config.example.yaml when it is missing - the
    same rule as settings.seed_config (runtime/config_seed.py; this module
    never imports settings). Without it a fresh install 500s on /api/status
    and on the wizard's own scan endpoint, so the wizard that writes the file
    cannot run."""
    return config_seed.seed(CONFIG_PATH, CONFIG_EXAMPLE)


def _read_saved_config() -> dict:
    """The saved configuration as the accounts layer resolves it. Reads only: a missing file is an OSError here, never seeded."""
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        from player import accounts
        return accounts.effective(yaml.safe_load(fh))


def load_config() -> dict:
    seed_config()
    return _read_saved_config()


def save_config(data: dict) -> str:
    from player import accounts
    data = {key: value for key, value in data.items() if key != 'config_revision'}
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        original = yaml.safe_load(fh)
    data = accounts.persist(data, original)
    return _save_yaml_backup(CONFIG_PATH, data)


def save_active_instance(instance: str) -> str:
    """Select another emulator: the stored configuration with `active_instance` changed and NOTHING else. Selecting is not an edit of any
    account, so accounts.persist - which files the effective fields of the selected emulator under its account and fills in the defaults
    of a sparse record ({} loadouts, {} tweaks, a sorted profile list) - is not run: every account record stays as it was stored."""
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        stored = yaml.safe_load(fh)
    stored['active_instance'] = instance
    return _save_yaml_backup(CONFIG_PATH, stored)


def _settings_snapshot(cfg):
    import hashlib
    from player import accounts
    from control.settings_catalog import catalog
    name = cfg.get('active_profile')
    path = _profile_path(name)
    profile = {}
    profile_bytes = b''
    if path and os.path.isfile(path):
        profile_bytes = Path(path).read_bytes()
        profile = yaml.safe_load(profile_bytes) or {}
        if not isinstance(profile, dict):
            raise ValueError('Active profile must be an object')
    context = dict(instance=cfg.get('active_instance', 'main'),
                   account=accounts.identity(cfg), profile=name)
    revision = hashlib.sha256(Path(CONFIG_PATH).read_bytes() + b'\0' + profile_bytes).hexdigest()
    editable = bool(path and profile_bytes and not _is_starter(name)
                    and not _is_draft(name) and not _foreign_profile(name, cfg))
    modules = catalog(cfg, profile, editable)
    policies = [dict(id=key, title=body.get('label') or key.replace('_', ' '),
                     editor=editor, section=section, editable=editable)
                for section, editor in [('shopping_lists', 'shop'), ('uw_policies', 'uw'),
                                        ('rescue_policies', 'rescue')]
                for key, body in sorted((profile.get('policies', {}).get(section) or {}).items())]
    return dict(ok=True, context=context, revision=revision, modules=modules,
                policies=policies), profile, path


def _settings_public(snapshot):
    for module in snapshot['modules']:
        module.pop('storage_path', None)
    return snapshot


@app.get('/api/settings')
def api_settings_get():
    with _CONFIG_WRITE_LOCK:
        try:
            cfg = load_config()
            path = _profile_path(cfg.get('active_profile'))
            # Same lock order as POST: config, then profile.
            if path:
                with _profile_lock(path):
                    snapshot, _, _ = _settings_snapshot(cfg)
            else:
                snapshot, _, _ = _settings_snapshot(cfg)
            return jsonify(_settings_public(snapshot))
        except (ValueError, OSError, yaml.YAMLError) as exc:
            return jsonify(ok=False, error=str(exc)), 400


@app.post('/api/settings')
@_serialize_config
def api_settings_post():
    from contextlib import nullcontext
    from control.settings_catalog import validate, write, read
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(ok=False, error='Expected a settings object'), 400
    try:
        cfg = load_config()
        path = _profile_path(cfg.get('active_profile'))
        with _profile_lock(path) if path else nullcontext():
            snapshot, profile, path = _settings_snapshot(cfg)
            if body.get('context') != snapshot['context'] or body.get('revision') != snapshot['revision']:
                return jsonify(ok=False, error='Settings or active account changed. Reload before saving.'), 409
            module = next((m for m in snapshot['modules'] if m['id'] == body.get('module')), None)
            if module is None:
                raise ValueError('Unknown settings module')
            if not module['editable']:
                return jsonify(ok=False, error='Create and activate a personal profile before editing these settings.'), 409
            values = body.get('values')
            if not isinstance(values, dict) or not values:
                raise ValueError('Provide one or more field values')
            allowed = {spec['key']: spec for spec in module['fields']}
            target = profile if module['scope'] == 'profile' else cfg
            before_problems = set(_profile_check(profile, cfg)) if module['scope'] == 'profile' else set()
            prefix = '.'.join(module['storage_path'])
            # Validate every input before applying any part of the patch.
            for key, value in values.items():
                if key not in allowed:
                    raise ValueError(f'Field is not editable in this module: {key}')
                validate(allowed[key], value)
                if key == 'gem_orbit.center' and (value[0] >= 1080 or value[1] >= 2560):
                    raise ValueError('Orbit center must be inside the native 1080 × 2560 screen')
            # Policy names are literal mapping keys, including periods.
            node = target
            for part in module['storage_path']:
                if part not in node:
                    node[part] = {}
                if not isinstance(node[part], dict):
                    raise ValueError(f'{prefix}: existing value is not an object')
                node = node[part]
            switch = values.get('active_instance') if module['id'] == 'active-instance' else None
            if switch is not None:
                from player import accounts
                accounts.identity(dict(cfg, active_instance=switch))      # an unknown account is refused here, before anything is written
            else:
                for key, value in values.items():
                    write(node, key, value)
            if module['id'] == 'device-preview':
                from device.preview.common import options as preview_options
                preview_options(node)
            if module['scope'] == 'profile':
                # Preserve unrelated legacy/unknown fields; validate this policy's
                # known fields rather than rejecting an entire existing profile.
                for spec in module['fields']:
                    validate(spec, read(node, spec['key'], spec['default']))
                introduced = set(_profile_check(profile, cfg)) - before_problems
                if introduced:
                    raise ValueError('; '.join(sorted(introduced)))
                backup = _save_yaml_backup(path, profile)
            elif switch is not None:
                backup = save_active_instance(switch)
            else:
                backup = save_config(cfg)
            fresh, _, _ = _settings_snapshot(load_config())
            fresh['backup'] = backup
            return jsonify(_settings_public(fresh))
    except (ValueError, OSError, yaml.YAMLError) as exc:
        return jsonify(ok=False, error=str(exc)), 400


def _calibration_dir(cfg=None):
    from player import accounts
    return str(accounts.calibration_dir(ROOT, cfg if cfg is not None else load_config()))


@app.get("/api/accounts")
def api_accounts():
    from player import accounts
    cfg = load_config()
    max_tier = 1
    try:
        with open(_profile_path(cfg.get("active_profile")), encoding="utf-8") as fh:
            max_tier = (yaml.safe_load(fh).get("player") or {}).get("max_tier", 1)
    except (OSError, TypeError, ValueError):
        pass
    return jsonify({"active": accounts.identity(cfg),
                    "max_tier": max_tier,
                    "has_personal_settings": cfg.get("active_profile") not in (None, "default") or any(isinstance(v, dict) and any(k != "defined" for k in v) for v in cfg.get("loadouts", {}).values()),
                    "instance": cfg.get("active_instance", "main"),
                    "accounts": [{"id": k, "label": v.get("label", k)}
                                 for k, v in (cfg.get("accounts") or {}).items()]})


@app.post("/api/accounts")
@_fenced_by_page_context
@_serialize_config
def api_account_select():
    """Create/bind an account only while automation is idle. Never moves files."""
    import copy
    from pathlib import Path
    from player import accounts
    _require_idle("Stop automation before changing accounts.")
    body = request.get_json(force=True) or {}
    try:
        name = accounts.valid_id(body.get("id")).lower()
        cfg = load_config()
        library = cfg.setdefault("accounts", {})
        if body.get("create"):
            if name in library:
                raise ValueError("An account with that identifier already exists")
            if body.get("import_current"):
                record = {k: copy.deepcopy(cfg.get(k)) for k in accounts.FIELDS}
                profile_name = f"account_{name}"
                source_profile = _profile_path(cfg.get("active_profile") or "default")
                target_profile = Path(ROOT) / "profiles" / f"{profile_name}.yaml"
                if target_profile.exists():
                    raise ValueError("The account's profile already exists; choose another identifier")
                if not source_profile or not Path(source_profile).is_file():
                    raise ValueError("The current profile is missing; create a fresh account instead")
                shutil.copy2(source_profile, target_profile)
                record["active_profile"] = profile_name
            else:
                profile_name = f"account_{name}"
                path = Path(ROOT) / "profiles" / f"{profile_name}.yaml"
                if path.exists():
                    raise ValueError("The new account's profile already exists; choose another identifier")
                starter = yaml.safe_load((Path(ROOT) / "profiles" / "default.yaml").read_text(encoding="utf-8"))
                from player.run_templates import starter_runs
                starter = starter_runs(starter)
                starter.pop("plan", None)
                starter["policies"]["chores"] = []
                path.write_text(yaml.safe_dump(starter, sort_keys=False, allow_unicode=True), encoding="utf-8")
                record = {"active_profile": profile_name, "loadouts": {"my_equipment": {}}, "tourney_card_tweaks": {}}
            record["label"] = str(body.get("label") or name).strip()[:100]
            library[name] = record
            if body.get("import_current"):
                # Copy this installation's current assets; do not take them
                # away from a running process or the legacy configuration.
                source_cfg = copy.deepcopy(cfg)
                target_cfg = copy.deepcopy(cfg)
                target_cfg["instances"][cfg["active_instance"]]["account"] = name
                target = accounts.template_dir(ROOT, target_cfg)
                target.mkdir(parents=True, exist_ok=True)
                for source in accounts.template_files(ROOT, source_cfg, "**/*.png"):
                    base = accounts.template_dir(ROOT, source_cfg)
                    try:
                        rel = source.relative_to(base)
                    except ValueError:
                        rel = source.relative_to(Path(ROOT) / "templates")
                    dest = target / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, dest)
                history = accounts.calibration_dir(ROOT, source_cfg)
                destination = accounts.calibration_dir(ROOT, target_cfg)
                for filename in ("scan_state.json", "calibrate_state.json", "calibrate_report.json"):
                    if (history / filename).is_file():
                        shutil.copy2(history / filename, destination / filename)
                for dirname in ("scan_evidence", "calibrate_evidence"):
                    if (history / dirname).is_dir():
                        shutil.copytree(history / dirname, destination / dirname)
        elif name not in library:
            raise ValueError("Choose an existing account or create one first")
        cfg["instances"][cfg["active_instance"]]["account"] = name
        cfg = accounts.effective(cfg)
        save_config(cfg)
        with _FRAME_CACHE_LOCK:
            _FRAME_CACHE.clear()
        _CALIB_CACHE.update(t=0, missing=[])
        return jsonify({"ok": True, "active": name})
    except (ValueError, OSError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.post("/api/automation-access")
def api_automation_access():
    """Allow or revoke automation input. Enabling keeps the no-runner rule.
    Revoking is always accepted and is a fenced sequence: refuse all later input
    at the shared input boundary (reaching accepted workers and delegated
    children whatever config they froze), pause harvesting, stop collector
    input, latch allow_taps false, then prove no input call is still in flight.
    A running full driver keeps observing; only its input is refused."""
    body = request.get_json(force=True) or {}
    if type(body.get("enabled")) is not bool:
        return jsonify({"ok": False, "error": "Choose whether to allow automation to tap."}), 400
    if not body["enabled"]:
        revocation = _revoke_taps()
        if revocation['state'] == 'failed':
            # The durable refusal could not be published: input is NOT revoked. Say so;
            # a retry of this request runs the whole sequence again.
            return jsonify(ok=False, code='input_revocation_failed', revocation=revocation,
                           error='Game input could not be revoked, so a running automation may still send input. '
                                 'The revocation field says what was stopped; send the request again to retry.'), 500
        return jsonify(ok=True, enabled=False, revocation=revocation)
    _require_idle("Stop automation before changing its tap permission.")
    with _CONFIG_WRITE_LOCK:
        cfg = load_config()
        device = operation_api.device_identity(cfg)
        if device['serial']:
            from runtime import device_lease
            device_lease.clear_revocation(device['serial'], device['adb_host'], device['adb_port'])
        cfg["instances"][cfg["active_instance"]]["allow_taps"] = True
        save_config(cfg)
    return jsonify({"ok": True, "enabled": True})


def _revoke_taps():
    """Revoke game input. Every step runs and reports; one failing never skips the rest.

    Enforcement and configuration are different facts. The durable refusal (the
    marker every accepted worker and delegated child reads at the input boundary)
    is what stops a running automation; allow_taps=false in the config only
    reaches processes started afterwards. Nothing is reported as revoked, fenced,
    latched or refused unless the marker was published and read back, and fenced /
    latched are claims about EVERY holder of the emulator: while the scan of who holds
    it (the ledger and process owners, the device lease) failed, who is covered is
    unknown, so state is `stopping` and both are false.

    state: nothing_running | input_stopped | stopping | not_enforced | failed
    (failed: the marker could not be published, so input is NOT revoked)."""
    from runtime import device_lease
    cfg = load_config()
    identity = harvest_api.identity_for(cfg)
    errors = []
    serial, host, port = identity['serial'], identity['adb_host'], identity['adb_port']

    def attempt(name, fn):
        try:
            return fn()
        except Exception as exc:                        # noqa: BLE001 - reported per step
            app.logger.exception('Tap revocation step %s failed', name)
            errors.append(dict(step=name, message=f'{type(exc).__name__}: {exc}'))
            return None

    def publish_marker():
        last = None
        for _ in range(3):   # a transient replace failure (antivirus, an open reader) is retried
            try:
                return device_lease.revoke_input(serial, host, port)
            except OSError as exc:
                last = exc
                time.sleep(0.05)
        raise last
    record = attempt('revoke_input', publish_marker) if serial else None
    enforced = record is not None or not serial   # without an ADB serial no worker can hold the device
    paused = attempt('pause_harvesting', lambda: harvest_api.pause(
        ROOT, identity, 'taps_revoked', f"taps-{(record or {}).get('token') or uuid.uuid4().hex}"))
    stop = attempt('stop_collectors', lambda: harvest_api.stop_collectors(ROOT, identity)) or dict(stopped=[], stopping=[], errors=[])

    def persist():
        with _CONFIG_WRITE_LOCK:
            fresh = load_config()
            fresh["instances"][fresh["active_instance"]]["allow_taps"] = False
            save_config(fresh)
        return True
    persisted = attempt('persist_allow_taps', persist) is True
    fenced = bool(record) and attempt('fence_input', lambda: device_lease.fence_input(serial, host, port)) is True
    lease = attempt('lease_state', lambda: device_lease.lease_state(serial, host, port)) if serial else None
    scanned = attempt('owners', lambda: _owners_now(cfg, _procs_cached()))
    owners = scanned or []
    holders_unknown = scanned is None or (bool(serial) and lease is None)   # a failed scan is never an empty one
    if holders_unknown:
        fenced = False
    for owner in owners:
        owner['input'] = ('refused' if fenced and lease and lease.get('input_gate') == 'enforced' else 'unfenced')
    held = bool(lease and lease.get('owned'))
    if not enforced:
        state = 'failed'                                # no durable refusal exists: nothing below is true
    elif holders_unknown:
        state = 'stopping'                              # who still holds the emulator is unknown: nothing is confirmed
    elif not (stop['stopped'] or stop['stopping'] or owners or held):
        state = 'nothing_running'
    elif stop['stopping'] or not fenced:
        state = 'stopping'                              # a worker is still exiting or an input call is in flight
    elif any(owner['input'] == 'unfenced' for owner in owners) or (held and lease.get('input_gate') != 'enforced'):
        state = 'not_enforced'                          # a holder that does not advertise the gate cannot be fenced
    else:
        state = 'input_stopped'
    return dict(state=state, fenced=fenced, latched=enforced and persisted and not holders_unknown,
                config_persisted=persisted,
                revoked_at=(record or {}).get('revoked_at'),
                harvest=dict(paused=bool(paused) and not paused.get('enabled'),
                             revision=(paused or {}).get('revision')),
                stopped=stop['stopped'], stopping=stop['stopping'], errors=errors + stop['errors'],
                still_owned=[owner for owner in owners])


@app.get('/api/harvest')
def api_harvest():
    return jsonify(ok=True, harvest=_harvest_status())


@app.post('/api/harvest')
def api_harvest_set():
    """Turn persistent gem harvesting on or off (see control/harvest.py).

    Pre-commit refusals (400 invalid_request, 409 context_changed, policy_invalid,
    harvest_revision_changed, idempotency_key_reused, action_lock) recorded
    nothing. Anything unexpected after a possible commit is 500
    harvest_outcome_unknown carrying the request identity: send the SAME request
    again to read the outcome back. The server never resends by itself."""
    body = request.get_json(silent=True)
    key = body.get('idempotency_key') if isinstance(body, dict) else None
    cfg = load_config()
    try:
        result = harvest_api.apply_choice(ROOT, cfg, body, resolve_source=_harvest_source, event=_diagnostic_event)
    except (harvest_api.ChoiceRefused, operation_api.Conflict):
        raise
    except ValueError as exc:
        return jsonify(ok=False, code='invalid_request', error=str(exc)), 400
    except Exception as exc:                            # noqa: BLE001 - may have happened after the commit
        app.logger.exception('Harvest choice outcome unknown')
        return jsonify(ok=False, code='harvest_outcome_unknown', outcome='unknown',
                       request_key=key if isinstance(key, str) else None,
                       error='The request may have been recorded. Send the same request again to read the outcome back.'), 500
    harvest = _harvest_status(cfg)
    return jsonify(ok=True, replayed=result['replayed'], desired=harvest_api.desired_view(result['choice']),
                   stop=result['stop'], overlay=harvest.get('overlay'), harvest=harvest)


@app.post("/api/account-tier")
@_fenced_by_page_context
def api_account_tier():
    _require_idle("Stop automation before changing account capabilities.")
    cfg = load_config()
    tier = (request.get_json(force=True) or {}).get("max_tier")
    if type(tier) is not int or not 1 <= tier <= 20:
        return jsonify({"ok": False, "error": "Enter an unlocked tier from 1 to 20."}), 400
    name = cfg.get("active_profile")
    path = _profile_path(name)
    if _is_starter(name) or not path or not os.path.isfile(path):
        return jsonify({"ok": False, "error": "Create your account first."}), 409
    with _profile_lock(path):
        with open(path, encoding="utf-8") as fh:
            profile = yaml.safe_load(fh)
        profile.setdefault("player", {})["max_tier"] = tier
        profile["player"]["max_tier_verified_by"] = "operator"
        problems = _profile_check(profile, cfg)
        if problems:
            return jsonify({"ok": False, "error": problems[0]}), 409
        _save_yaml_backup(path, profile)
    return jsonify({"ok": True, "max_tier": tier})


@app.get("/")
def index():
    # The dashboard (frontend/app) is the only page; a checkout that has not
    # built it gets the page saying how. /ui/index.html is a static redirect that
    # maps old bookmarks onto /ui/app/ (plan R17, option A).
    if os.path.isfile(os.path.join(app.static_folder, "app", "index.html")):
        return redirect("/ui/app/")
    return Response(_APP_NOT_BUILT, status=404, mimetype="text/html")


# The dashboard app (frontend/app, ADR-001) is a build product in
# webui/app/, git-ignored, served by the static folder at /ui/app/. A source
# checkout that has not built it gets a page saying how, not a bare 404.
_APP_NOT_BUILT = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Tower Pilot - UI not built</title></head>
<body style="background:#17191e;color:#e5e7eb;font-family:system-ui,sans-serif;margin:2rem">
<h1>The dashboard is not built yet</h1>
<p>This checkout has no build of the dashboard app (<code>frontend/webui/app/</code>).
Build it once with Node.js 22.13+ (22.x), 24.x or 26+:</p>
<pre>cd frontend/app; npm ci; npm run build</pre>
<p>Then reload this page.</p>
</body></html>
"""


def _inside(folder: str, path: str) -> bool:
    """True when real path `path` is `folder` or lies in it; another drive or a UNC share is not."""
    try:
        return os.path.commonpath([os.path.normcase(folder), os.path.normcase(path)]) == os.path.normcase(folder)
    except ValueError:
        return False


@app.get("/ui/app/")
@app.get("/ui/app/<path:asset>")
def ui_app(asset: str = "index.html") -> Response:
    from flask import abort, send_file
    from werkzeug.security import safe_join
    build = os.path.join(app.static_folder, "app")
    if not os.path.isfile(os.path.join(build, "index.html")):
        return Response(_APP_NOT_BUILT, status=404, mimetype="text/html")
    # Serve only regular files whose REAL path lies inside the real build folder:
    # a junction or symlink (or a linked build folder) could point anywhere.
    if os.path.islink(build) or os.path.isjunction(build):
        abort(404)
    root = os.path.realpath(build)
    joined = safe_join(root, asset)
    target = os.path.realpath(joined) if joined is not None else root
    if not _inside(root, target) or os.path.normcase(target) == os.path.normcase(root) or not os.path.isfile(target):
        abort(404)
    return send_file(target)


@app.before_request
def ui_app_static_guard() -> Response | None:
    """Flask's static route (/ui/<path>) reaches the same files under other
    spellings: /ui/App/, /ui/./app/, /ui/app./, /ui/app%20/, or a junction to
    webui/app or to a folder inside it. A static path whose real prefix lies in
    the build folder is answered by ui_app() with the build-relative rest, so its
    containment applies; any other static file is served only when its real
    path lies inside the real webui/ folder."""
    if request.endpoint != "static":
        return None
    from flask import abort
    filename = (request.view_args or {}).get("filename", "")
    parts = [part for part in filename.split("/") if part not in ("", ".")]
    if not parts or "\\" in filename or any(part == ".." or ":" in part for part in parts):
        abort(404)                          # no backslash, parent, drive or stream syntax
    static = app.static_folder
    build = os.path.realpath(os.path.join(static, "app"))
    for depth in range(1, len(parts) + 1):
        prefix = os.path.realpath(os.path.join(static, *parts[:depth]))
        if _inside(build, prefix):
            asset = os.path.relpath(os.path.join(prefix, *parts[depth:]), build).replace("\\", "/")
            return ui_app("index.html" if asset == "." else asset)
    if not _inside(os.path.realpath(static), os.path.realpath(os.path.join(static, *parts))):
        abort(404)
    return None


@app.get("/api/config")
def api_config_get():
    from player import accounts
    with _CONFIG_WRITE_LOCK:
        seed_config()
        raw = Path(CONFIG_PATH).read_bytes()
        cfg = accounts.effective(yaml.safe_load(raw))
    return jsonify(dict(cfg, config_revision=hashlib.sha256(raw).hexdigest()))


@app.post("/api/config")
def api_config_post():
    data = request.get_json(force=True)
    try:
        backup = save_config(data)
    except Exception as e:                      # noqa: BLE001 - shown to user
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "backup": backup})


# ------------------------------------------------------------------ status
def _procs():
    """Live runner processes, scanned now (_scan_procs, uncached). /api/status
    reads _procs_cached instead: the background refresher's snapshot, which
    it rescans inline on the request only when that snapshot is over 6 s old
    (refresher not started, stalled or failing)."""
    return _scan_procs()


def _proc_in_tree(pid: int) -> bool:
    """Scope process controls to this checkout; device leases cross checkouts.

    Cwd determines which dashboard may control a process, not which device
    it owns. Unreadable cwd stays conservative in the control inventory.
    """
    try:
        import psutil
        cwd = os.path.normcase(psutil.Process(pid).cwd())
    except Exception:                           # noqa: BLE001
        return True
    return cwd == os.path.normcase(ROOT)


def _process_target(cfg=None):
    from device import adbclient
    cfg = load_config() if cfg is None else cfg
    inst = (cfg.get('instances') or {}).get(cfg.get('active_instance', 'main')) or {}
    return (*adbclient._HOST, inst.get('serial'))


def _scan_procs(target=None):
    """One row {pid, runner (script stem), cmdline} per runner of THIS tree.

    The process walk and the runner-script set are runtime.runners.
    other_runners, the same source the bootstrap and scan preflights use.
    A venv launcher stub (.venv/Scripts/python(w).exe) starts the real
    interpreter as a CHILD with the same arguments after argv[0], so every
    runner it starts appears as two processes; a process whose parent is
    another runner with the same arguments is that launcher's child and is
    left out, so the row carries the launcher's pid (the pid Popen returned).
    Terminating either process of the pair ends both (2026-10-12, uv venv).
    Rows are scoped to this checkout and omit workers proven to own another
    transport. Foreign checkouts remain protected by the machine-wide lease;
    they do not belong in this dashboard's PID kill list."""
    import psutil
    from runtime import runners
    target = target if target is not None else _process_target()
    found = {pid: (script, args) for pid, script, args in runners.other_runners(target=target)
             if _proc_in_tree(pid)}
    out = []
    for pid, (script, args) in found.items():
        try:
            process = psutil.Process(pid)
            parent = process.ppid()
        except Exception:                       # noqa: BLE001 - exited since the walk
            process, parent = None, None
        if parent in found and found[parent][1][1:] == args[1:]:
            continue
        # Pin the row to the process the walk saw: only a process that still has that command line
        # gives its creation time. Stopping a listed runner is judged by (pid, creation time), never
        # by the pid alone, so a pid reused after this scan can never be stopped as this runner.
        created = None
        if process is not None:
            try:
                if process.cmdline() == args:
                    created = process.create_time()
            except Exception:                   # noqa: BLE001 - unreadable: the row simply has no identity
                created = None
        out.append({"pid": pid, "runner": Path(script).stem, "cmdline": " ".join(args), "created": created})
    return out


# /api/status is polled every 2.5 s by every open tab; the process scan
# must never be on that request's critical path. A background thread keeps
# a snapshot fresh (every 2 s) and the endpoint returns it instantly.
# Control actions (kill/start) call _procs_refresh() to update it at once.
_PROCS_CACHE = {"at": 0.0, "procs": [], "attempted_at": None, "error": None}
_PROCS_LOCK = threading.Lock()
_PROCS_WAKE = threading.Event()
_PROCS_THREAD = None


def _procs_refresh():
    global _PROCS_CACHE
    with _PROCS_LOCK:
        target = None
        attempted = time.time()
        try:
            target = _process_target()
            rows = _scan_procs(target)
        except Exception as exc:  # a failed discovery is visible; control scans still raise
            previous = _PROCS_CACHE if _PROCS_CACHE.get('target') == target else {}
            _PROCS_CACHE = dict(previous, target=target, attempted_at=attempted,
                                at=previous.get('at', 0.0), procs=previous.get('procs', []),
                                error={'type': type(exc).__name__, 'message': str(exc)})
            app.logger.exception('Runner process discovery failed for %s', target)
        else:
            _PROCS_CACHE = dict(target=target, attempted_at=attempted, at=time.time(),
                                procs=rows, error=None)
        return _PROCS_CACHE['procs']


def _procs_snapshot(target=None, *, refresh=True):
    """Keep rows and discovery evidence in one atomically published snapshot."""
    target = _process_target() if target is None else target
    cache = _PROCS_CACHE
    if refresh and (time.time() - cache['at'] > 6 or cache.get('target') != target):
        if _PROCS_THREAD is not None and _PROCS_THREAD.is_alive():
            _PROCS_WAKE.set()  # status never waits for a running process-table scan
        else:
            _procs_refresh()  # imports/test clients without the normal server startup
            cache = _PROCS_CACHE
    return cache, target


def _procs_read(refresh=True):
    """(cache, target, now) for one read of the process cache.

    A reply that must pair process ROWS with the refresh EVIDENCE about them (api_status: `processes` and
    `process_refresh`) pins its snapshot first (`g.procs_pin = {}`): the first read of that reply decides - the
    same call, with the same refresh flag, it would have made on its own - and every later read of the same
    reply gets the same published cache dict and the same instant. The refresher replaces the cache as a
    whole, so without the pin the rows could come from an older scan than the age and timestamps next to
    them, and "the emulator is free" would be judged on rows the metadata does not describe. Anything else
    (an action handler that refreshes and reads again) reads the live cache every time."""
    pin = g.get('procs_pin') if has_request_context() else None
    if pin is None:
        cache, target = _procs_snapshot(refresh=refresh)
        return cache, target, time.time()
    if 'read' not in pin:
        cache, target = _procs_snapshot(refresh=refresh)
        pin['read'] = (cache, target, time.time())
    return pin['read']


def _procs_cached():
    cache, target, _ = _procs_read()
    return cache['procs'] if cache.get('target') == target else []


def _procs_status():
    cache, target, now = _procs_read(refresh=False)
    matches = cache.get('target') == target
    age = max(0.0, now - cache['at']) if matches and cache['at'] else None
    return dict(state='unknown' if age is None else 'stale' if age > 6 or cache.get('error') else 'fresh',
                age_sec=age, last_success_at=cache['at'] if matches and cache['at'] else None,
                last_attempt_at=cache.get('attempted_at') if matches else None,
                error=cache.get('error') if matches else None)


def _process_snapshot_idle(cache, target):
    """Only fresh, successful, unchanged discovery proves transport recovery safe."""
    return (not _PROCS_LOCK.locked() and cache is _PROCS_CACHE and cache.get('target') == target
            and target == _process_target() and cache.get('error') is None
            and cache.get('at', 0) > 0 and 0 <= time.time() - cache['at'] <= 6
            and cache.get('procs') == [])


def _start_procs_refresher():
    global _PROCS_THREAD
    if _PROCS_THREAD is not None and _PROCS_THREAD.is_alive():
        return

    def loop():
        while True:
            _PROCS_WAKE.clear()
            _procs_refresh()
            _harvest_tick()
            _PROCS_WAKE.wait(2.0)
    _PROCS_THREAD = threading.Thread(target=loop, daemon=True, name="procs-refresher")
    _PROCS_THREAD.start()


_CONN_CACHE = {"t": 0.0, "serial": None, "device_state": "unknown", "port_open": False}


def _port_open(serial: str) -> bool:
    """Does a `host:port` serial's port accept a TCP dial (0.3 s)? False for
    any other serial shape. Open port != adb device online: MuMu keeps the
    port open while the adb server lists the device `offline`."""
    if ":" not in serial:
        return False
    import socket
    host, _, port = serial.rpartition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=0.3):
            return True
    except (OSError, ValueError):       # unreachable / non-numeric port
        return False


def _device_state(cfg: dict) -> dict:
    """The active instance's adb device state in `cfg`, from ONE adb-server
    host query (`adbclient.get_state`, socket only - CLAUDE.md rule 1), plus
    the TCP `port_open` dial. /api/status is polled every 2.5 s by every open
    tab, so both answers are reused for 10 s - only for the serial they were
    asked for. A state the server answered that is not 'device' (offline,
    missing, unauthorized...) is re-queried (get_state alone, the port dial
    keeps its 10 s) on every call, so a stale online answer never clears the
    Start blocker. 'no_server' and 'unknown' (no server, or one that never
    answers) are held for the 10 s like 'device': asking again waits out
    get_state's whole 1 s timeout on every poll; `_CONN_CACHE["t"] = 0` also
    forces a full re-query."""
    from device import adbclient
    now = time.time()
    inst = (cfg.get("instances") or {}).get(
        cfg.get("active_instance", "main")) or {}
    serial = inst.get("serial") or ""
    if now - _CONN_CACHE["t"] >= 10 or serial != _CONN_CACHE["serial"]:
        _CONN_CACHE.update(t=now, serial=serial,
                           device_state=adbclient.get_state(serial),
                           port_open=_port_open(serial))
    elif _CONN_CACHE["device_state"] not in ("device", "no_server", "unknown"):
        _CONN_CACHE["device_state"] = adbclient.get_state(serial)
    _reconnect_tick(cfg, serial, _CONN_CACHE["device_state"], _CONN_CACHE["port_open"])
    return {"device_state": _CONN_CACHE["device_state"],
            "port_open": _CONN_CACHE["port_open"], "serial": serial}


# AUTO-RECONNECT (user, 2026-10-12: "add some resiliency and re-connectivity
# built in"). Live that day the adb server restarted and the TCP device stayed
# detached - Live read "missing" until a manual host:connect. Every
# _device_state call (each /api/status poll, a failed frame, which first drops
# the cached answer) runs _reconnect_tick: when the server does not list the
# device as `device`, one adbclient.recover runs on a background thread -
# never on the request - if it has something to do: connect the device when its adb port answers (the
# VM is up) and no runner of this tree is live, or start a server when none
# listens (any serial, runners or not: no server means no transport to
# bounce). While a runner is live the device is the runner's (capture.grab
# recovers it; the manual reconnect refuses with 409 for the same reason),
# so then only a down server is started (recover attempts=0: no connect, no
# disconnect). One in flight; the next no sooner than 5 s after the last
# started, nor before the failure backoff (5, 10, 20, 40, then 60 s);
# 'gave_up' after _RECONNECT_GIVE_UP consecutive failed attempts, and it
# holds until the device reads `device`, the serial changes, the server's
# situation flips (a give-up reached with no server listening holds only while
# none listens, one reached with a server listening only while one does: a
# server that dies later gets its start, with a fresh set of attempts that
# waits out the backoff the last one set), or a manual reconnect / adopt calls
# _reconnect_reset (the Reconnect button stays on the page). Only ONE flip is
# honoured per outage ("flipped": a give-up reached after a flip is final until
# the device reads `device` or a reset), so a server that keeps dying cannot
# keep the attempts, and its start-server spawns, going (a server-only attempt
# counts like any other: only the device reading `device` clears the count,
# also with a runner live or the port closed). Nothing else
# counts as "something changed": the state an attempt leaves behind is its own
# doing (`offline` right after its connect, `missing` once the server drops
# that transport, a server it started), and a closed port or a live runner is
# a tick with nothing to do, which leaves the failure count alone. So is
# `unauthorized`: only the person at the emulator can allow the host, recover
# returns that state untouched, so no attempt starts or counts for it. recover
# sends no game input. "down" is whether the server was down when the last
# attempt ended (when it started, until it ends).
_RECONNECT = {"state": "idle", "attempts": 0, "next_at": 0.0, "last_error": "",
              "serial": None, "down": False, "flipped": False, "thread": None}
_RECONNECT_LOCK = threading.Lock()
_RECONNECT_GIVE_UP = 10


def _reconnect_reset():
    """Manual reconnect / adopt: idle, no failures counted, next attempt
    allowed at once."""
    with _RECONNECT_LOCK:
        _RECONNECT.update(state="idle", attempts=0, next_at=0.0, last_error="", serial=None,
                          flipped=False)


def _reconnect_tick(cfg, serial, device_state, port_open):
    """Start one background recovery for `serial` if one is due (see
    AUTO-RECONNECT above); otherwise only update the bookkeeping: 'device'
    clears the failure count to idle; a new serial starts the count over
    with no wait; 'gave_up' returns at once while the server is still in the
    state (down / listening) the last attempt left it in, or when this outage
    already had its one flip; otherwise it starts the count over (the backoff
    the last attempt set still applies); nothing to do (port closed or a
    runner live, and a server listening; or `unauthorized`) reads idle and
    keeps the failure count; an attempt waiting out its backoff reads
    'trying'. Returns at once either way."""
    now = time.time()
    target = _process_target(cfg)
    snapshot = None
    if port_open and device_state not in ('device', 'unauthorized') and target[-1] == serial:
        snapshot, _ = _procs_snapshot(target)
    connect = snapshot is not None and _process_snapshot_idle(snapshot, target)
    with _RECONNECT_LOCK:
        r = _RECONNECT
        if r["thread"] is not None and r["thread"].is_alive():
            return
        if device_state == "device":
            r.update(state="idle", attempts=0, last_error="", serial=serial, flipped=False)
            return
        if r["serial"] != serial:
            r.update(state="idle", attempts=0, next_at=0.0, last_error="", serial=serial,
                     flipped=False)
        down = device_state == "no_server"
        if r["state"] == "gave_up":
            if down == r["down"] or r["flipped"]:
                return
            r.update(state="idle", attempts=0, last_error="", flipped=True)
        if device_state == "unauthorized" or (not connect and not down):
            r["state"] = "idle"             # a person allows the host / VM down / runner's device
            return
        if now < r["next_at"]:
            r["state"] = "trying"
            return
        adb = (cfg.get("adb") or {}).get("exe") or ""
        r.update(state="trying", next_at=now + 5.0, down=down)
        r["thread"] = threading.Thread(target=_reconnect_attempt,
                                       args=(serial, adb, connect, target, snapshot),
                                       daemon=True, name="adb-reconnect")
        r["thread"].start()


def _reconnect_attempt(serial, adb, connect, target, snapshot):
    """One adbclient.recover for `serial` (its start_server: `<adb>
    start-server` through _run, the sanctioned lifecycle spawn; with
    `connect` False, attempts=0: start a down server, touch no transport),
    then the outcome into _RECONNECT: 'device' -> idle; any other state
    (a server-only attempt that started a server and left the device
    unattached included), or an exception, counts one failed attempt, sets
    the backoff and records whether the server was down when the attempt
    ended. Dropped when a reset or another serial took over meanwhile."""
    from device import adbclient

    # A refresh, selection change or delayed thread can invalidate the tick's
    # evidence. Refuse transport changes before recover can connect/disconnect.
    if connect and not _process_snapshot_idle(snapshot, target):
        with _RECONNECT_LOCK:
            if _RECONNECT['serial'] == serial:
                _RECONNECT['state'] = 'idle'
        return

    def start_server():
        _run([adb, "start-server"], capture_output=True, timeout=15)
    try:
        state = (adbclient.recover(serial, start_server) if connect
                 else adbclient.recover(serial, start_server, attempts=0))
        error = "" if state == "device" else f"device {state} after recovery"
    except Exception as e:                      # noqa: BLE001 - reported in /api/status
        state, error = None, str(e)[:120]
    with _RECONNECT_LOCK:
        r = _RECONNECT
        if r["serial"] != serial:
            return
        if state == "device":
            r.update(state="idle", attempts=0, last_error="")
        else:
            n = r["attempts"] + 1
            r.update(attempts=n, last_error=error,
                     down=r["down"] if state is None else state == "no_server",
                     state="gave_up" if n >= _RECONNECT_GIVE_UP else "trying",
                     next_at=max(r["next_at"], time.time() + min(5.0 * 2 ** (n - 1), 60.0)))
    _CONN_CACHE["t"] = 0.0          # the next poll re-asks the adb server


def _reconnect_status(serial) -> dict:
    """The /api/status `reconnect` value for `serial`, the device that status
    reports. next_try_in: seconds until the next attempt may start, None while
    idle, given up, or an attempt is running. A record that belongs to another
    serial (the instance switched while the old serial's attempt still runs;
    the tick starts over only once it ends) reads as a fresh idle one."""
    with _RECONNECT_LOCK:
        r = _RECONNECT
        if r["serial"] != serial:
            return {"state": "idle", "attempts": 0, "next_try_in": None, "last_error": ""}
        busy = r["thread"] is not None and r["thread"].is_alive()
        wait = (round(max(0.0, r["next_at"] - time.time()), 1)
                if r["state"] == "trying" and not busy else None)
        return {"state": r["state"], "attempts": r["attempts"],
                "next_try_in": wait, "last_error": r["last_error"]}


_CALIB_CACHE = {"t": 0.0, "missing": []}


def _calibrate_missing() -> list:
    """Templates the code ASKED for and did not find, from the newest run's
    events - the only honest 'calibration is missing something' signal (a
    template never asked for cannot be known missing). Cached 10 s."""
    now = time.time()
    if now - _CALIB_CACHE["t"] < 10:
        return _CALIB_CACHE["missing"]
    cfg = load_config()
    inst = cfg.get("active_instance", "main")
    missing = sorted({r.get("template") for r in _newest_events(inst, n=2000)
                      if r.get("kind") == "template_missing"} - {None})
    _CALIB_CACHE.update(t=now, missing=missing)
    return missing


def _newest_events(instance: str = "main", n: int = 60):
    files = sorted(glob.glob(os.path.join(ROOT, "logs", instance,
                                          "events_*.jsonl")),
                   key=os.path.getmtime)
    if not files:
        return []
    rows = []
    with open(files[-1], encoding="utf-8") as fh:
        for line in fh.readlines()[-n:]:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


@app.get("/api/status")
def api_status():
    cfg = load_config()
    from player.scan_plan import control_gate
    daily = {}
    try:
        with open(os.path.join(ROOT, "logs", "daily_state.json")) as fh:
            daily = json.load(fh)
    except (OSError, ValueError):
        pass
    inst = cfg.get("active_instance", "main")
    flag = _stop_flag_path(inst)
    conn = _device_state(cfg)
    g.procs_pin = {}   # `processes` and `process_refresh` below are built from ONE snapshot of the process cache
    # A GET never creates the ledger or retires a worker: the refresher does that.
    processes = operation_api.project_processes(ROOT, _procs_cached(), reconcile=False)
    return jsonify({
        "processes": processes,
        "harvest": _harvest_status(cfg, processes),
        # after processes: that projection reconciles dead workers first. The
        # same evaluations POST /api/control start (run scope) and start_gems
        # (collection scope) admission apply.
        "setup_battle_guard": _setup_battle_guard(cfg),
        "collection_guard": _collection_guard(cfg),
        "process_refresh": _procs_status(),
        "runtime": dict(server=_BUILD, capabilities=_CAPABILITIES, api_contract=1, port=PORT),
        "daily_state": daily,
        "stop_flag": os.path.exists(flag),
        "events": _newest_events(inst),
        "instance": inst,
        # setup gate: false hides every tab but the wizard (see _setup_complete;
        # sticky once true, and the grandfather probe runs only while false)
        "setup_complete": _setup_complete(),
        "calibration": control_gate(ROOT, cfg),
        # nav gates: Control is locked until calibration has nothing missing,
        # and shows a connect overlay while the adb server does not list the
        # device as online (`device`) - an open port alone is not connected
        "connected": conn["device_state"] == "device",
        "device_state": conn["device_state"],
        "port_open": conn["port_open"],
        # the background device recovery (_reconnect_tick): state idle /
        # trying / gave_up, consecutive failed attempts, seconds to the next
        # one (None when none is scheduled), the last failure's message
        "reconnect": _reconnect_status(conn["serial"]),
        "calibrate_missing": _calibrate_missing(),
    })


_DISPLAY_CACHE: dict = {}          # serial -> (game display id or None, derived at)
_DISPLAY_TTL = 300.0


def _game_display(serial, configured=None):
    """The display the dashboard's own captures must ask for.

    MuMu runs the game on a secondary display whose id CHANGES on every
    emulator restart, and adopt writes only serial + adb - so the preview,
    the live stream and the screen check all read the default display (the
    landscape launcher, prefixed by a text warning) until this derived it
    (2026-09-08: "no frame", a stream that never refreshed, a resolution of
    "[War" x "ning"). Same derivation as capture.refresh_display, cached
    briefly per serial; `_forget_display` drops it when a capture stops
    making sense so the next call re-derives. None = single display."""
    if configured:
        return configured
    hit = _DISPLAY_CACHE.get(serial)
    if hit and time.time() - hit[1] < _DISPLAY_TTL:
        return hit[0]
    from device import adbclient, displays
    try:
        disp, _logical = displays.game_display(
            lambda cmd: adbclient.shell(serial, cmd, timeout=10).decode(errors="replace"))
    except Exception:                           # noqa: BLE001 - offline: no display
        return None
    _DISPLAY_CACHE[serial] = (disp, time.time())
    return disp


def _forget_display(serial):
    _DISPLAY_CACHE.pop(serial, None)


def _screencap_cmd(serial, display, png=True):
    return ("screencap -p" if png else "screencap") + (f" -d {display}" if display else "")


def _bad_display_query():
    """The refusal (JSON, 400) for a capture route's `display` query value,
    or None when it may be used: it goes into the device's screencap shell
    command, so it must be ASCII digits only."""
    display = request.args.get("display")
    if display and not re.fullmatch(r"[0-9]+", display):
        return jsonify({"ok": False, "error": "display: a whole number expected"}), 400
    return None


def _bad_device_query(configured_serial):
    """_bad_display_query, plus the refusal for a `serial` query value (the
    screen check's): it goes to the adb server, so it must be the configured
    one or have a form _SERIAL_RE accepts (host:port, emulator-NNNN)."""
    serial = request.args.get("serial")
    if serial and serial != configured_serial and not _SERIAL_RE.fullmatch(serial):
        return jsonify({"ok": False, "error": "serial: host:port or emulator-NNNN expected"}), 400
    return _bad_display_query()


@app.get("/api/frame.png")
def api_frame():
    cfg = load_config()
    inst = cfg["instances"][cfg.get("active_instance", "main")]
    refused = _bad_display_query()
    if refused:
        return refused
    # the configured device only: no page names another (a `serial` query
    # parameter is ignored)
    serial = inst["serial"]
    # -d: MuMu runs the game on a secondary display; without the id the
    # screencap answers with a warning instead of pixels (same as capture.py)
    display = request.args.get("display") or _game_display(serial, inst.get("display"))
    cmd = _screencap_cmd(serial, display)
    # SOCKET FIRST, adb.exe NEVER on the hot path (user, 2026-08-18: "we
    # also have a Stream why running new ADB?"): this endpoint fires every
    # 5 s per open tab, and spawning adb.exe for each was both the console-
    # window storm and pointless - the runners already talk straight to the
    # adb server socket. A failed capture spawns nothing here: it hands the
    # device to the bounded background recovery (_device_state ->
    # _reconnect_tick), which starts a down server with the configured adb
    # at most once per backoff window - it used to run start-server on EVERY
    # failed frame and never re-attached a dropped TCP device. Only the
    # configured adb binary is ever started (an `adb` query parameter is
    # ignored: it let any web page name an executable for this server to run).
    try:
        from device import adbclient
        raw = adbclient.exec_out(serial, cmd, timeout=15)
    except Exception as e:                      # noqa: BLE001 - device or server down
        _CONN_CACHE["t"] = 0.0                  # a held 'device' is stale now
        _device_state(cfg)
        return Response(f"capture failed: {e}", status=502)
    from device import displays
    raw = displays.strip_warning(raw)
    if not raw.startswith(displays.PNG_MAGIC):
        _forget_display(serial)                 # display churn: re-derive next time
        return Response("no PNG from adb (device offline?)", status=502)
    ts = request.args.get("ts")
    if ts:
        # THE CROPPER'S CONTRACT: a template is cut from the exact frame the
        # user drew on, never from a fresh grab that may show another screen
        # - and only for the page (emulator, account, profile) it was fetched for.
        _remember_frame(ts, raw, _frame_context(cfg))
    return Response(raw, mimetype="image/png")


_STREAMS = {}                     # bounded recent sessions; never store pixels
_STREAM_LOCK = threading.Lock()
_STREAM_LIMIT = 32


def _stream_decode(raw):
    from device.preview.common import decode_raw
    return decode_raw(raw)


@app.get("/api/stream/backends")
def api_stream_backends():
    """Read local package availability without starting captures or downloads."""
    try:
        return jsonify(environment.stream_backends(Path(ROOT).parent, load_config()))
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post('/api/environment/probe')
def api_environment_probe():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or body != {'probe': 'windows_ocr'}:
        return jsonify(ok=False, error='Expected {"probe": "windows_ocr"}'), 400
    return jsonify(ok=True, windows_ocr=environment.probe_windows_ocr(Path(ROOT).parent, _run))


@app.get("/api/stream/status")
def api_stream_status():
    with _STREAM_LOCK:
        entry = _STREAMS.get(request.args.get("stream_id", ""))
        if entry is None:
            return jsonify(ok=False, error="Stream session not found"), 404
        result = {key: value for key, value in entry.items() if not key.startswith("_")}
        ticks = entry["_frames"]
        age = time.monotonic() - ticks[-1] if ticks else None
        result["last_frame_age_s"] = round(age, 3) if age is not None else None
        result["actual_fps"] = (round((len(ticks) - 1) / (ticks[-1] - ticks[0]), 2)
                                if len(ticks) > 1 and ticks[-1] > ticks[0] else None)
        if age is not None and age > max(3, 2 / entry["target_fps"]):
            result["actual_fps"] = 0.0
    return jsonify(result)


@app.get("/api/stream.mjpg")
def api_stream():
    """Native MuMu preview with optional scrcpy and socket screenshot fallbacks."""
    import math
    import secrets
    from device.preview.common import BACKENDS, options as preview_options
    cfg = load_config()
    instance = cfg.get("active_instance", "main")
    inst = cfg["instances"][instance]
    refused = _bad_display_query()
    if refused:
        return refused
    if request.args.get("instance", instance) != instance:
        return jsonify(ok=False, error="Active emulator changed; refresh the stream"), 409
    serial = inst["serial"]             # the configured device only, as api_frame
    asked_display = request.args.get("display") or inst.get("display")
    explicit_display = bool(request.args.get("display"))
    try:
        settings = preview_options(inst.get("preview"))
        backend = request.args.get("backend", settings["backend"])
        if backend not in BACKENDS:
            raise ValueError("backend must be auto, mumu, scrcpy, or adb")
        fps = float(request.args.get("fps", settings["fps"]))
        quality = int(request.args.get("q", 90))
        scale = float(request.args.get("scale", 1))
        if not math.isfinite(fps) or not 0.5 <= fps <= 30:
            raise ValueError("fps must be between 0.5 and 30")
        if not 30 <= quality <= 90:
            raise ValueError("q must be between 30 and 90")
        if not math.isfinite(scale) or not 0.2 <= scale <= 1:
            raise ValueError("scale must be between 0.2 and 1")
    except (ValueError, OverflowError) as exc:
        return jsonify(ok=False, error=str(exc)), 400
    stream_id = request.args.get("stream_id", secrets.token_hex(12))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", stream_id):
        return jsonify(ok=False, error="Invalid stream_id"), 400
    if request.method == "HEAD":
        # Flask handles HEAD through this GET route, but never iterates its
        # body. A probe must not reserve a capture session it cannot finish.
        return Response(mimetype="multipart/x-mixed-replace; boundary=frame",
                        headers={"Cache-Control": "no-cache"})
    stats = dict(stream_id=stream_id, instance=instance, target_fps=fps,
                 scale=scale, quality=quality, width=None, height=None, jpeg_bytes=None,
                 frames=0, errors=0, capture_ms=None, encode_ms=None,
                 backend=None, requested_backend=backend, effective_fps=fps,
                 backend_failures=[], frame_age_ms=None,
                 active=True, last_error=None, _frames=[], _started=False)
    correlation = dict(getattr(g, "diagnostic_ids", {}))
    with _STREAM_LOCK:
        if stream_id in _STREAMS and _STREAMS[stream_id]["active"]:
            return jsonify(ok=False, error="Stream session already active"), 409
        while len(_STREAMS) >= _STREAM_LIMIT:
            retired = next((key for key, value in _STREAMS.items() if not value["active"]), None)
            if retired is None:
                return jsonify(ok=False, error="Too many active streams"), 429
            del _STREAMS[retired]
        _STREAMS[stream_id] = stats

    def event(kind, **fields):
        _diagnostic_event(kind, stream_id=stream_id, instance=instance, **correlation, **fields)

    def finish_stream():
        with _STREAM_LOCK:
            if not stats["active"]:
                return
            stats["active"] = False
            started, frames, errors = stats["_started"], stats["frames"], stats["errors"]
        if started:
            event("stream_stopped", frames=frames, errors=errors)

    def gen():
        from device import adbclient
        from device.preview.manager import PreviewCapture
        import cv2
        import numpy as np
        # THE STREAM MUST NEVER STARVE THE SERVER (2026-08-18, live): with
        # the emulator offline every frame blocked 15 s inside the adb
        # timeout, pinning a Flask thread; /api/status calls queued behind
        # it for 8-20 s, so kill/scan/wizard all looked broken - one bug
        # wearing three faces. Short capture timeout, and after a failure
        # back off to one probe every 3 s so an offline device costs one
        # thread a trivial amount of time.

        def multipart(jpg, kind, sequence, backend_name, source_age_ms):
            """One part with the provenance of ITS OWN pixels: whether it is a frame or the error marker, how many successful frames the
            stream had produced, which source made them and that source's own age estimate (unknown when it gave none). The caller
            snapshots the four values under the stream lock, in the same step that counted the frame, so a fallback that happens
            right after cannot relabel a part already made."""
            usable = isinstance(source_age_ms, (int, float)) and not isinstance(source_age_ms, bool) and math.isfinite(source_age_ms)
            age = f"{round(max(0.0, source_age_ms), 1):.1f}".rstrip("0").rstrip(".") if usable else "unknown"
            return (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpg)).encode() +
                    f"\r\nX-Preview-Frame-Kind: {kind}\r\nX-Preview-Sequence: {sequence}\r\n"
                    f"X-Preview-Backend: {backend_name or 'unknown'}\r\nX-Preview-Source-Age-Ms: {age}\r\n\r\n".encode() +
                    jpg.tobytes() + b"\r\n")

        def _port_up() -> bool:
            # a ~1 ms TCP probe of the emulator's adb port (_port_open); when the VM is
            # down this is instant, so the stream never even ASKS adb and
            # never holds a thread for a capture that cannot succeed. A serial
            # without a port (emulator-5554) has nothing to probe: ask adb.
            return ":" not in serial or _port_open(serial)

        with _STREAM_LOCK:
            stats["_started"] = True
        event("stream_started", target_fps=fps, scale=scale, quality=quality, requested_backend=backend)

        def raw_capture():
            cmd = _screencap_cmd(serial, _game_display(serial, asked_display), png=False)
            raw = adbclient.exec_out(serial, cmd, timeout=2.0)
            try:
                return _stream_decode(raw)
            except ValueError:
                _forget_display(serial)
                raise

        source = PreviewCapture(
            serial=serial, adb_exe=cfg.get("adb", {}).get("exe", ""), settings=settings,
            raw_capture=raw_capture, physical_display=lambda: _game_display(serial, asked_display),
            requested_backend=backend, fps=fps, explicit_display=explicit_display,
            event=event,
        )
        try:
            while True:
                t0 = time.monotonic()
                failed = False
                try:
                    if not _port_up():
                        raise OSError("emulator port closed")
                    img = source.read(timeout=2.0)
                    captured = time.monotonic()
                    if scale != 1.0:
                        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                    ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
                    if not ok:
                        raise ValueError("JPEG encoding failed")
                    completed = time.monotonic()
                    with _STREAM_LOCK:
                        recovered = stats["last_error"] is not None
                        stats.update(frames=stats["frames"] + 1, last_error=None,
                                     width=img.shape[1], height=img.shape[0], jpeg_bytes=len(jpg),
                                     capture_ms=round((captured - t0) * 1000, 2),
                                     encode_ms=round((completed - captured) * 1000, 2))
                        stats.update(source.status())
                        stats["_frames"] = (stats["_frames"] + [completed])[-16:]
                        part = ("frame", stats["frames"], stats["backend"], stats["frame_age_ms"])
                    if recovered:
                        event("stream_recovered")
                    yield multipart(jpg, *part)
                except GeneratorExit:
                    return
                except Exception as exc:      # noqa: BLE001 - device/codec boundary
                    failed = True
                    with _STREAM_LOCK:
                        first_failure = stats["last_error"] is None
                        stats.update(errors=stats["errors"] + 1, last_error=str(exc))
                        stats.update(source.status())
                        part = ("error", stats["frames"], stats["backend"], None)     # the marker does not advance the sequence and has no source age
                    if first_failure:
                        event("stream_error", error=str(exc), error_type=type(exc).__name__)
                    canvas = np.zeros((96, 270, 3), np.uint8)
                    cv2.putText(canvas, "stream unavailable", (8, 58),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (80, 80, 240), 2)
                    ok, jpg = cv2.imencode(".jpg", canvas)
                    if not ok:
                        raise RuntimeError("Could not encode stream error marker") from exc
                    yield multipart(jpg, *part)
                # Capture/encoding/browser backpressure consume the period. A
                # slow capture never queues catch-up work or adds another period.
                wait = (3.0 if failed else 1.0 / source.effective_fps) - (time.monotonic() - t0)
                if wait > 0:
                    time.sleep(wait)
        finally:
            try:
                source.close()
            except Exception as exc:  # cleanup remains visible in session diagnostics
                with _STREAM_LOCK:
                    stats.update(last_error=str(exc), errors=stats["errors"] + 1)
                    stats.update(source.status())
                event("stream_cleanup_failed", error=str(exc), error_type=type(exc).__name__)
            finally:
                finish_stream()

    response = Response(gen(),
                        mimetype="multipart/x-mixed-replace; boundary=frame",
                        headers={"Cache-Control": "no-cache"})
    # An unstarted Python generator never enters its finally block on close.
    # WSGI response cleanup must release that reservation too.
    response.call_on_close(finish_stream)
    return response


def _stop_flag_path(inst: str) -> str:
    """The runflag file (scheduling/runflag.py) for this instance."""
    return os.path.join(ROOT, "logs", inst, "stop_after_run")


# ------------------------------------------------------------------ control
@app.post("/api/control")
def api_control():
    body = request.get_json(force=True)
    action = body.get("action")
    cfg = load_config()
    inst = cfg.get("active_instance", "main")
    if action == takeover_api.ACTION:
        return _takeover_action(body, cfg, inst)
    if action in ('start', 'start_gems'):
        if 'harvest' in body or 'desire_revision' in body:
            return jsonify(ok=False, code='invalid_request',
                           error='Automatic harvesting fields are reserved for the lifecycle host'), 400
        retry = operation_api.retry(ROOT, cfg, 'run' if action == 'start' else 'gems', body, _profile_path(cfg.get('active_profile')))
        if retry:
            return jsonify(ok=True, operation_id=retry['id'], operation=operation_api.public(retry))
    # Legacy stop requests are retired. Live repeat controls belong to the
    # current battle; the day plan retains its boundary flag and cancellation.
    if action == "clear_flag":
        if body.get('operation_id'):
            operation = operation_api.require_revision(ROOT, body, ('accepted', 'running'))
            inst = operation['context']['instance']
        try:
            os.remove(_stop_flag_path(inst))
        except FileNotFoundError:
            pass
        return jsonify({"ok": True})
    if action == "kill":
        return _kill_action(body)
    if action == "start_gems":
        _require_idle("Automation is already running. Stop it before starting the diamond collector.",
                      collector_code='collector_running')
        try:
            cmd = _gems_launch_plan(cfg, body.get("preset")).command
        except harvest_api.Refused as exc:
            return jsonify(ok=False, code=exc.code, error=str(exc)), _REFUSAL_STATUS.get(exc.code, 409)
        operation = _launch_operation(cfg, "gems", body, cmd)
        _procs_refresh()
        return jsonify(ok=True, operation_id=operation['id'], operation=operation_api.public(operation))
    if action == "start":
        preset = body.get("preset")
        takeover = _collector_takeover("Automation is already running. Finish or stop it before starting another run.", cfg)
        try:
            from player import readiness
            cfg["presets"] = _compiled_runs(cfg)
            if preset in cfg["presets"]:
                # combo: every run its day plan names, so an unready block is
                # refused here, before any tap, not at its handoff
                result = (_combo_readiness(cfg, cfg["presets"]) if preset == "combo"
                          else readiness.check(ROOT, cfg, cfg["presets"][preset]))
                if not result["ready"]:
                    runs = sorted({run for row in result["missing"] for run in row.get("runs", [])})
                    return jsonify(dict(result, ok=False, error="Calibration is needed for this run"
                                        + (" (not ready: " + ", ".join(runs) + ")" if runs else "")
                                        + ". Open Calibrate to see the missing steps.")), 409
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        if preset not in runnable_presets(cfg):
            return jsonify({"ok": False, "error": "unknown preset"}), 400
        try:
            cmd = _run_command(cfg, inst, preset)
        except _FlowRegistryUnreadable as e:
            return jsonify({"ok": False, "error": f"flow registry unreadable: {e}"}), 500
        transfer = _transfer_from_collector(cfg, body, takeover) if takeover else None
        try:
            operation = _launch_operation(cfg, "run", body, cmd)
        finally:
            if takeover:
                harvest_api.end_transfer(ROOT, harvest_api.identity_for(cfg))
        _procs_refresh()
        return jsonify(ok=True, operation_id=operation['id'], operation=operation_api.public(operation),
                       **({'transfer': transfer} if transfer else {}))
    return jsonify({"ok": False, "error": "unknown action"}), 400


# ------------------------------------------------------------------ scanner
@app.post("/api/scan/start")
def api_scan_start():
    body = request.get_json(force=True) or {}
    retry = operation_api.retry(ROOT, load_config(), "scan", body, _profile_path(load_config().get('active_profile')))
    if retry:
        return jsonify(ok=True, operation_id=retry['id'], operation=operation_api.public(retry))
    others = [pr for pr in _procs() if pr["runner"] != "scan"]
    _require_idle("runners alive: " + ", ".join(pr["runner"] for pr in others) +
                  " - stop them first (scan.py would refuse anyway; this check saves the spawn)", rows=others,
                  allow_kinds=('scan',))
    if any(pr["runner"] == "scan" for pr in _procs()):
        return jsonify({"ok": False, "error": "a scan is already running"}), 409
    cfg = load_config()
    inst = cfg.get("active_instance", "main")
    phases = body.get("phases", "g,c,m")
    args = [os.path.join(ROOT, "player", "scan.py"), "--instance", inst,
            "--phases", phases]
    if body.get("battle"):
        args.append("--battle")
    if body.get("deep"):
        args.append("--deep")
    if body.get("fresh"):
        args.append("--fresh")
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    operation = _launch_operation(cfg, "scan", body, [pyw] + args)
    return jsonify(ok=True, phases=phases, operation_id=operation['id'], operation=operation_api.public(operation))


# ------------------------------------------------------------ calibrator
def _operation_artifact_bytes(operation, filename, *, allow_shared=False):
    """Read the bytes named by the operation, then verify those same bytes."""
    ref = operation['result_refs'].get(filename)
    if ref is None:
        if not allow_shared:
            raise operation_api.OperationRefused('artifact_unavailable', f'No saved {filename} for this operation')
        path = Path(operation['context']['artifact_dir']) / filename
    elif not isinstance(ref, dict) or not ref.get('path') or not ref.get('revision'):
        raise operation_api.OperationRefused('artifact_invalid', f'Invalid saved reference for {filename}')
    else:
        path = Path(ref['path'])
    try:
        contents = path.read_bytes()
    except OSError as exc:
        raise operation_api.OperationRefused('artifact_unavailable', f'Saved {filename} is unavailable: {exc}') from exc
    revision = hashlib.sha256(contents).hexdigest()
    if ref is not None and revision != ref['revision']:
        raise operation_api.OperationRefused('artifact_changed', f'Saved {filename} changed after this operation')
    return contents, {'path': str(path), 'revision': revision}


def _operation_artifact_json(operation, filename, *, allow_shared=False):
    contents, ref = _operation_artifact_bytes(operation, filename, allow_shared=allow_shared)
    try:
        result = json.loads(contents)
    except (ValueError, UnicodeError) as exc:
        raise operation_api.OperationRefused('artifact_invalid', f'Saved {filename} is not valid JSON') from exc
    if not isinstance(result, dict) or result.get('operation_id') != operation['id']:
        raise operation_api.OperationRefused('artifact_invalid', f'Saved {filename} belongs to another operation')
    return result, ref


def _taps_allowed(cfg: dict) -> bool:
    inst = cfg.get("active_instance", "main")
    return bool(((cfg.get("instances") or {}).get(inst) or {}).get("allow_taps"))


@app.post("/api/screen-analysis/start")
def api_screen_analysis_start():
    cfg = load_config()
    body = _request_object()
    retry = operation_api.retry(ROOT, cfg, 'screen_analysis', body, _profile_path(cfg.get('active_profile')))
    if retry:
        return jsonify(ok=True, operation_id=retry['id'], operation=operation_api.public(retry))
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    op = _launch_operation(cfg, 'screen_analysis', body, [pyw, '-m', 'player.screen_analysis', '--instance', cfg.get('active_instance', 'main')])
    return jsonify(ok=True, operation_id=op['id'], operation=operation_api.public(op))


@app.get("/api/screen-analysis/status")
def api_screen_analysis_status():
    cfg = load_config()
    operation = _requested_operation(cfg, ('screen_analysis',), reconcile=False)
    if operation:
        cfg = operation['context']['config']
    try:
        if operation:
            result, _ = _operation_artifact_json(operation, 'screen_analysis.json', allow_shared=True)
        else:
            with open(os.path.join(_calibration_dir(cfg), "screen_analysis.json"), encoding="utf-8") as fh:
                result = json.load(fh)
    except operation_api.Conflict as exc:
        result = {'status': 'unavailable', 'message': str(exc)}
    except (OSError, ValueError):
        result = {"status": "idle"}
    if operation:
        result['operation'] = operation_api.public(operation)
        if operation['status'] in ('accepted', 'running', 'cancelling'):
            result.update(status='running')
        elif operation['status'] != 'succeeded':
            result.update(status='error', message=(operation.get('error') or {}).get('message', 'Analysis did not complete'))
    return jsonify(result)


@app.get("/api/screen-analysis/frame.png")
def api_screen_analysis_frame():
    cfg = load_config()
    operation = _requested_operation(cfg, ('screen_analysis',), reconcile=False)
    if operation:
        if operation['status'] != 'succeeded':
            raise operation_api.OperationRefused('analysis_not_finished', 'Wait for this screen analysis to complete', operation)
        from io import BytesIO
        contents, _ = _operation_artifact_bytes(operation, 'screen_analysis.png')
        return send_file(BytesIO(contents), mimetype='image/png', max_age=0)
    path = os.path.join(_calibration_dir(cfg), "screen_analysis.png")
    if not os.path.isfile(path):
        return jsonify(ok=False, code='no_screenshot', error='No screen analysis screenshot has been saved yet'), 404
    return send_file(path, mimetype="image/png", max_age=0)


@app.post("/api/calibrate/start")
def api_calibrate_start():
    """Run player/calibrate.py detached: it walks the menus and cuts this
    account's own templates (card tabs, preset rows, module icons) - the
    pictures the repo never ships. Same guards as the scan, plus taps: the
    calibrator navigates, so a read-only instance cannot run it."""
    body = _request_object(required=True)
    routes = body.get('routes')
    if 'routes' in body:
        if (not isinstance(routes, list) or body.get('bootstrap') is not True
                or any(body.get(key) for key in ('flows', 'battle_only', 'observe'))):
            raise InvalidRequest('Routes require bootstrap only, without flows, battle_only or observe', 'invalid_routes')
        from player.bootstrap import validate_route_selection
        try:
            validate_route_selection(routes)
        except ValueError as exc:
            raise InvalidRequest(str(exc), 'invalid_routes') from exc
    retry = operation_api.retry(ROOT, load_config(), "calibrate", body, _profile_path(load_config().get('active_profile')))
    if retry:
        return jsonify(ok=True, operation_id=retry['id'], operation=operation_api.public(retry))
    others = [pr for pr in _procs() if pr["runner"] != "calibrate"]
    _require_idle("runners alive: " + ", ".join(pr["runner"] for pr in others) + " - stop them first", rows=others,
                  allow_kinds=('calibrate', 'observe'))
    _refuse_while_calibrating()
    cfg = load_config()
    if not _taps_allowed(cfg) and not body.get("allow_navigation"):
        return jsonify({"ok": False, "code": "taps_disabled",
                        "error": "allow_taps is off for this instance - the "
                                 "calibrator walks the game's menus; switch it "
                                 "on in Configuration first"}), 409
    inst = cfg.get("active_instance", "main")
    phases = str(body.get("phases") or "c,g,m,u,b,w")
    bootstrap = body.get("bootstrap") is True
    chosen = [p.strip() for p in phases.split(",") if p.strip()]
    if not chosen or any(p not in ("c", "g", "m", "u", "b", "w", "e") for p in chosen):
        raise InvalidRequest("Choose valid calibration steps", 'invalid_phases')
    from player.scan_plan import missing_navigation
    missing = [] if bootstrap else missing_navigation(ROOT, cfg, chosen, include_wave=False)
    if missing:
        return jsonify({"ok": False, "code": "navigation_images_missing",
                        "error": "Capture the navigation images in the Scan plan before automatic calibration.", "missing": missing}), 409
    if "e" in chosen and not bootstrap:
        try:
            with open(os.path.join(_calibration_dir(cfg), "calibrate_state.json"), encoding="utf-8") as fh:
                basic_done = json.load(fh).get("phases", {}).get("modules", {}).get("status") == "done"
        except (OSError, ValueError):
            basic_done = False
        if not basic_done:
            return jsonify({"ok": False, "code": "modules_not_done", "error": "Finish phase 1 Modules first."}), 409
    args = [os.path.join(ROOT, "player", "calibrate.py"), "--instance", inst,
            "--phases", phases]
    if bootstrap:
        args.append("--bootstrap")
        if routes is not None:
            args.extend(['--routes', ','.join(routes)])
        # The consented action-capture stage (start+cancel a battle, walk
        # event/store/guild). Bootstrap-only and only when the frontend's
        # popup passed flows=true; never inferred.
        if body.get("flows"):
            args.append("--flows")
            if body.get("battle_only"):
                args.append("--battle-only")
    if body.get("overwrite"):
        args.append("--overwrite")
    if body.get("fresh"):
        args.append("--fresh")
    if body.get("allow_navigation"):
        args.append("--allow-navigation")
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    log_dir = Path(ROOT) / 'logs' / inst
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / 'calibration-worker.log').open('ab') as output:
        operation = _launch_operation(cfg, "calibrate", body, [pyw] + args, stdout=output, stderr=subprocess.STDOUT)
    return jsonify(ok=True, phases=phases, operation_id=operation['id'], operation=operation_api.public(operation))


@app.post("/api/calibrate/observe")
def api_calibrate_observe():
    """Capture from the screen as it is: two frames, searched with the
    installed artwork for every bound target still missing (the HUD ability
    buttons during a battle, the UW switches with the panel open). READ-ONLY:
    no taps, no navigation, so it needs no tap permission and may run over a
    battle the person is playing - the screen is what they chose to show."""
    body = _request_object()
    retry = operation_api.retry(ROOT, load_config(), "observe", body, _profile_path(load_config().get('active_profile')))
    if retry:
        return jsonify(ok=True, operation_id=retry['id'], operation=operation_api.public(retry))
    watch = _watch_seconds(body.get('watch'))
    others = [pr for pr in _procs() if pr["runner"] != "calibrate"]
    _require_idle("runners alive: " + ", ".join(pr["runner"] for pr in others) + " - stop them first", rows=others,
                  allow_kinds=('calibrate', 'observe'))
    _refuse_while_calibrating()
    cfg = load_config()
    inst = cfg.get("active_instance", "main")
    args = [os.path.join(ROOT, "player", "calibrate.py"), "--instance", inst, "--phases", "c", "--observe"]
    if watch:
        args += ["--observe-watch", str(watch)]
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    operation = _launch_operation(cfg, "observe", body, [pyw] + args)
    return jsonify(ok=True, operation_id=operation['id'], operation=operation_api.public(operation))


@app.post("/api/calibrate/stop")
def api_calibrate_stop():
    body = request.get_json(silent=True)
    return _person_stop(dict(body) if isinstance(body, dict) else {})


@app.get("/api/calibrate/status")
def api_calibrate_status():
    cfg = load_config()
    operation = _requested_operation(cfg, ('calibrate', 'observe'), reconcile=False)
    if operation:
        cfg = operation['context']['config']
    inst = cfg.get("active_instance", "main")
    out = {"state": {}, "report": {}, "manifest": {}, "card_manifest": {}}
    for fname, key in (("calibrate_state.json", "state"),
                       ("calibrate_report.json", "report"), ("module_manifest.json", "manifest"),
                       ("card_manifest.json", "card_manifest")):
        try:
            if operation:
                active = operation['status'] in ('accepted', 'running', 'cancelling')
                # Manifests predate operation identity; only a sealed reference
                # can associate them with a historical capture.
                if key in ('manifest', 'card_manifest'):
                    if fname not in operation['result_refs']:
                        continue
                    contents, _ = _operation_artifact_bytes(operation, fname)
                    out[key] = json.loads(contents)
                elif key == 'report' and not active and fname not in operation['result_refs']:
                    continue
                else:
                    out[key], _ = _operation_artifact_json(operation, fname, allow_shared=True)
            else:
                with open(os.path.join(_calibration_dir(cfg), fname), encoding="utf-8") as fh:
                    value = json.load(fh)
                out[key] = value if isinstance(value, dict) else {}
        except operation_api.Conflict as exc:
            out.setdefault('artifact_errors', {})[key] = str(exc)
        except (OSError, ValueError):
            pass
    from player.calibration_report import describe_report
    out["report"] = describe_report(out["report"])
    if operation and out['state'].get('operation_id') != operation['id']:
        out.update(state={}, report={}, manifest={}, card_manifest={})
    if not out['card_manifest']:
        cards = out['state'].get('phases', {}).get('cards', {})
        inventory = (cards.get('results') or {}).get('inventory')
        if cards.get('status') == 'done' and isinstance(inventory, dict):
            out['card_manifest'] = inventory
    # Start and status must use the same live process source. A cached negative
    # immediately after Popen used to make the UI declare completion.
    processes = [] if operation else [pr for pr in _procs() if pr["runner"] == "calibrate"]
    selection = None
    activity = out['state'].get('activity')
    if not isinstance(activity, dict) or (operation and activity.get('operation_id') != operation['id']):
        activity = None
    if processes:
        command = processes[0].get("cmdline", "")
        match = re.search(r"--phases\s+([a-z,]+)", command)
        if match:
            selection = {"mode": "observe" if "--observe" in command else "bootstrap" if "--bootstrap" in command else "basic", "phases": match.group(1).split(","),
                         "taps": "--allow-navigation" in command,
                         "overwrite": "--overwrite" in command}
        # Also supports workers launched before incremental reports existed.
        if activity is None:
            events = [row for row in _newest_events(inst, n=2000)
                      if row.get("kind", "").startswith("calibrate")]
            if events:
                activity = events[-1]
    if operation:
        processes = operation['status'] in ('accepted', 'running', 'cancelling')
        selection = dict(operation['request'], mode='observe' if operation['kind'] == 'observe' else 'bootstrap' if operation['request'].get('bootstrap') else 'basic')
        if isinstance(selection.get('phases'), str):
            selection['phases'] = selection['phases'].split(',')
        selection['taps'] = bool(selection.get('allow_navigation'))
        out['operation'] = operation_api.public(operation)
    return jsonify({"restore_pending": os.path.exists(os.path.join(_calibration_dir(cfg), "module_restore.json")), "running": bool(processes), "selection": selection,
                    "activity": activity,
                    "taps_allowed": _taps_allowed(cfg), **out})


class ApplyRefused(ValueError):
    """An apply refused before it changed anything: a 400 with a stable `code`."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@app.post("/api/calibrate/apply")
@_fenced_by_page_context
@_serialize_config
def api_calibrate_apply():
    """Apply only successfully completed observations to the current profile.

    It writes the PROFILE IN FORCE (and, for a wall region, the configuration), so a switch of account or profile between the
    page's read and this write would put the observations on the wrong profile. A request that carries `expected_context`
    {instance, account, profile, serial} is therefore fenced like every context-dependent write (see _fenced_by_page_context):
    refused 409 context_changed, writing nothing and recording no operation, before the owner refusal, the replay of an
    applied operation and every other check. An apply by `operation_id` is also judged against the context of that operation
    (below); the fence adds the page's own context to that, it does not replace it. No `expected_context`: exactly as before."""
    import copy
    body = _request_object()
    operation = None
    legacy_write = None
    if body.get('operation_id'):
        try:
            prior = operation_api.resolve(ROOT, body['operation_id'])
        except KeyError:
            raise operation_api.OperationRefused('operation_unknown', 'Unknown operation') from None
        if prior['kind'] not in ('calibrate', 'observe'):
            raise operation_api.OperationRefused('not_a_calibration', 'Operation is not a calibration', prior)
        applied = prior['status'] == 'succeeded' or prior['result_refs'].get('apply_status') == 'succeeded'
        if applied and body.get('expected_revision') in (prior['revision'], prior['result_refs'].get('apply_request_revision')):
            return jsonify(ok=True, applied=True, profile=prior['context']['profile'], operation=operation_api.public(prior), readiness=prior.get('readiness'))
    _require_idle("Wait for calibration to stop before applying results.")
    cfg = load_config()
    if body.get('operation_id'):
        operation = operation_api.require_revision(ROOT, body, ('awaiting_apply', 'failed', 'interrupted'))
        if operation['kind'] not in ('calibrate', 'observe'):
            raise operation_api.OperationRefused('not_a_calibration', 'Operation is not a calibration', operation)
        original = operation['context']
        current = operation_api.context(ROOT, cfg, {}, _profile_path(cfg.get('active_profile')))
        allowed_config_revisions = (original['config_revision'], operation['result_refs'].get('apply_config_revision'))
        if (any(current.get(k) != original.get(k) for k in ('account', 'instance', 'serial', 'rendering', 'profile', 'device_key'))
                or current['config_revision'] not in allowed_config_revisions):
            raise operation_api.OperationRefused('context_changed', 'Return to the original unchanged account, device and profile before applying', operation)
        cfg = original['config']
    elif not body.get('saved_observations'):
        raise operation_api.OperationRefused('operation_required', 'Name the operation, or explicitly review saved_observations')
    else:
        tracked = _requested_operation(cfg, ('calibrate', 'observe'))
        if tracked:
            raise operation_api.OperationRefused('observations_tracked', 'These observations belong to an operation; apply its ID and revision', tracked)
    name = cfg.get("active_profile")
    if _is_starter(name):
        return jsonify({"ok": False, "code": "starter_profile",
                        "error": "Create your account or copy the starter before applying calibration."}), 409
    path = _profile_path(name)
    if not path or not os.path.isfile(path):
        return jsonify({"ok": False, "code": "profile_missing", "error": "Choose a profile first."}), 400
    try:
        if operation:
            state, state_ref = _operation_artifact_json(
                operation, 'calibrate_state.json', allow_shared=operation['status'] == 'interrupted')
        else:
            with open(os.path.join(_calibration_dir(cfg), "calibrate_state.json"), encoding="utf-8") as fh:
                state = json.load(fh)
            if state.get('operation_id'):
                raise operation_api.OperationRefused('observations_tracked', 'These observations belong to an operation; apply its ID and revision')
        if not state.get("player"):
            raise ApplyRefused('no_observations', "No completed account observations yet")
        if os.path.exists(os.path.join(_calibration_dir(cfg), "module_restore.json")):
            raise ApplyRefused('restoration_pending', "Restore the saved module equipment before applying discoveries.")
        if operation is None:
            store = operation_api.ledger(ROOT)
            legacy_write, _ = store.accept('profile_write',
                operation_api.context(ROOT, cfg, {}, path), {'action': 'apply_saved_observations'})
            legacy_write = store.transition(legacy_write['id'], legacy_write['revision'], 'running',
                                            worker=_profile_write_worker(), step='profile_write')
        with _profile_lock(path):
            if operation:
                if operation_api.file_revision(path) != operation['context']['profile_revision']:
                    with open(path, encoding='utf-8') as current_profile:
                        persisted = yaml.safe_load(current_profile)
                    if operation_api.digest(persisted) != operation['result_refs'].get('apply_profile_digest'):
                        raise operation_api.OperationRefused('profile_changed', 'Profile changed after preparation; discoveries remain saved for review', operation)
                refs = dict(operation['result_refs'])
                refs['calibrate_state.json'] = state_ref
                refs.setdefault('apply_capture_outcome', {
                    key: operation.get(key) for key in ('status', 'error', 'step', 'recovery_action')})
                refs.update(apply_request_revision=operation['revision'], apply_status='applying', apply_error=None)
                import psutil
                operation = operation_api.ledger(ROOT).transition(operation['id'], operation['revision'], 'applying', step='apply', worker={'pid': os.getpid(), 'creation_time': psutil.Process().create_time()}, result_refs=refs)
            with open(path, encoding="utf-8") as fh:
                profile = yaml.safe_load(fh)
            player = profile.setdefault("player", {})
            # The wall bar REGION setup detected is machine config (per
            # instance rois.wall_bar - what the wall watch reads), not profile
            # data: it goes to config.yaml, never into player.
            wall_bar = state["player"].get("wall_bar")
            for key, value in state["player"].items():
                if key == "wall_bar":
                    continue
                if key in ("category_presets", "uws", "abilities"):
                    player.setdefault(key, {}).update(copy.deepcopy(value))
                else:
                    player[key] = copy.deepcopy(value)
            # Ownership the verified cuts prove (uw/<name>.png comes off the
            # in-run panel, which lists owned weapons only) - also for states
            # written before the scanner recorded player.uws itself.
            from player.calibration_report import owned_uws
            proven = owned_uws(state.get("entries"))
            if proven:
                player.setdefault("uws", {}).update(proven)
            from player import playerprofile as pp
            with _COMPILER_LOCK:
                previous = pp.CONFIG
                pp.CONFIG = cfg
                try:
                    problems = pp.validate(profile)
                finally:
                    pp.CONFIG = previous
            # Saving observed ownership must not depend on battle-only run
            # requirements. No run rules change; launch still validates them.
            if operation:
                operation = operation_api.ledger(ROOT).transition(operation['id'], operation['revision'], 'applying', step='profile_write_intent', result_refs=dict(operation['result_refs'], apply_profile_digest=operation_api.digest(profile)))
            _save_yaml_backup(path, profile)
            saved_profile = Path(path).read_bytes()
            if operation_api.digest(yaml.safe_load(saved_profile)) != operation_api.digest(profile):
                raise ValueError('Applied profile could not be verified by reading it back')
            profile_revision = hashlib.sha256(saved_profile).hexdigest()
        from player.readiness import wall_bar_roi
        if wall_bar and not wall_bar_roi(cfg):
            inst = cfg.setdefault("instances", {}).setdefault(cfg.get("active_instance", "main"), {})
            inst.setdefault("rois", {})["wall_bar"] = list(wall_bar)
            if operation:
                operation = operation_api.ledger(ROOT).transition(operation['id'], operation['revision'], 'applying', step='config_write_intent', result_refs=dict(operation['result_refs'], apply_config_revision=operation_api.digest(cfg)))
            save_config(cfg)
            if operation:
                persisted_cfg = load_config()
                persisted_context = operation_api.context(ROOT, persisted_cfg, {}, path)
                if (wall_bar_roi(persisted_cfg) != list(wall_bar)
                        or any(persisted_context.get(key) != operation['context'].get(key)
                               for key in ('account', 'instance', 'serial', 'rendering', 'profile', 'device_key'))):
                    raise ValueError('Applied wall region could not be verified by reading configuration back')
                operation = operation_api.ledger(ROOT).transition(
                    operation['id'], operation['revision'], 'applying', step='config_verified',
                    result_refs=dict(operation['result_refs'], apply_config_revision=operation_api.digest(persisted_cfg)))
        extra = {}
        if operation:
            readiness_result = None
            selected = operation['context'].get('selected_run')
            if selected:
                try:
                    from player import readiness
                    runs = _compiled_runs(cfg)
                    if selected in runs:
                        readiness_result = readiness.check(ROOT, cfg, runs[selected])
                except Exception as exc:  # applied data remains confirmed; readiness must fail closed
                    readiness_result = {'ready': False, 'error': str(exc), 'missing': []}
            captured = operation['result_refs']['apply_capture_outcome']
            capture_incomplete = captured['status'] in ('failed', 'interrupted')
            operation = operation_api.ledger(ROOT).transition(
                operation['id'], operation['revision'], captured['status'] if capture_incomplete else 'succeeded',
                step=captured['step'] if capture_incomplete else 'applied', readiness=readiness_result,
                error=captured['error'] if capture_incomplete else None,
                recovery_action=captured['recovery_action'] if capture_incomplete else None,
                result_refs=dict(operation['result_refs'], apply_status='succeeded', apply_error=None,
                                 apply_profile_revision=profile_revision))
            extra['operation'] = operation_api.public(operation)
            extra['readiness'] = readiness_result
        if legacy_write:
            legacy_write = operation_api.ledger(ROOT).transition(
                legacy_write['id'], legacy_write['revision'], 'succeeded', step='profile_written',
                result_refs={'apply_profile_revision': profile_revision})
        return jsonify({"ok": True, "applied": True, "profile": name, "wall_bar": wall_bar,
                        "remaining_run_checks": problems, **extra})
    except operation_api.Conflict as exc:
        if legacy_write:
            operation_api.ledger(ROOT).transition(legacy_write['id'], legacy_write['revision'], 'failed',
                error={'code': 'apply_failed', 'message': str(exc)})
        raise
    except Exception as e:  # request boundary: preserve an explicit recoverable apply outcome
        if not isinstance(e, (OSError, ValueError)):
            app.logger.exception('Calibration apply failed')
        if operation and operation['status'] == 'applying':
            captured = operation['result_refs']['apply_capture_outcome']
            capture_incomplete = captured['status'] in ('failed', 'interrupted')
            error = {'code': 'apply_failed', 'message': str(e)}
            operation_api.ledger(ROOT).transition(
                operation['id'], operation['revision'], captured['status'] if capture_incomplete else 'awaiting_apply',
                error=captured['error'] if capture_incomplete else error,
                recovery_action=captured['recovery_action'] if capture_incomplete else 'Review the profile and retry applying saved discoveries',
                result_refs=dict(operation['result_refs'], apply_status='failed', apply_error=error))
        if legacy_write:
            operation_api.ledger(ROOT).transition(legacy_write['id'], legacy_write['revision'], 'failed',
                error={'code': 'apply_failed', 'message': str(e)})
        return jsonify({"ok": False, "code": e.code if isinstance(e, ApplyRefused) else 'apply_failed', "error": str(e)}), 400 if isinstance(e, (OSError, ValueError)) else 500


@app.post("/api/scan/stop")
def api_scan_stop():
    body = request.get_json(silent=True)
    return _person_stop(dict(body) if isinstance(body, dict) else {})


@app.get("/api/scan/status")
def api_scan_status():
    cfg = load_config()
    operation = _requested_operation(cfg, ('scan',), reconcile=False)
    if operation:
        cfg = operation['context']['config']
    inst = cfg.get("active_instance", "main")
    state = {}
    artifact_error = None
    try:
        if operation:
            state, _ = _operation_artifact_json(operation, 'scan_state.json', allow_shared=True)
        else:
            with open(os.path.join(_calibration_dir(cfg), "scan_state.json"),
                      encoding="utf-8") as fh:
                state = json.load(fh)
    except operation_api.Conflict as exc:
        artifact_error = str(exc)
    except (OSError, ValueError):
        pass
    if operation and state.get('operation_id') != operation['id']:
        state = {}
    return jsonify({"running": operation['status'] in ('accepted', 'running', 'cancelling') if operation else any(pr["runner"] == "scan" for pr in _procs_cached()),
                    "state": state, "operation": operation_api.public(operation) if operation else None,
                    "artifact_error": artifact_error})


@app.get("/api/profiles")
def api_profiles():
    out = []
    pdir = os.path.join(ROOT, "profiles")
    for f in sorted(glob.glob(os.path.join(pdir, "*.yaml"))):
        out.append({"name": os.path.basename(f),
                    "draft": f.casefold().endswith(".draft.yaml"),
                    "mtime": os.path.getmtime(f)})
    return jsonify(out)


@app.get("/api/run-templates")
def api_run_templates():
    from player.run_templates import catalogue
    return jsonify(catalogue())


@app.post("/api/run-templates/add")
@_fenced_by_page_context
def api_run_template_add():
    """Add a run from a shipped recipe: {profile, template, run_id, options}.

    Two optional guards, both ABSENT = the request as it always was (the legacy page): `expected_revision` (the profile revision the page
    read, as /api/profile-src and /api/profile-patch publish it) is judged under the profile lock BEFORE the recipe and its choices are
    looked at, and a stale one is 409 revision_conflict with the current revision, writing nothing; `expected_context` {instance, account,
    profile, serial} is the page fence of the other context-dependent writes (see _fenced_by_page_context: 409 context_changed,
    judged first, writing nothing). A request that names `expected_revision` and succeeds also gets the new `revision` back, so a page can
    chain the next add without a read; the answer to a request without it is unchanged (no `revision`). The resend of a committed add that
    carried `expected_revision` meets a changed profile and is refused 409 revision_conflict (a page settles it by reading the profile); one
    without it is refused by the run identifier that now exists (400), as before.

    A request that names a guard (either of the two) also gets a stable `code` on each refusal the route can PROVE happened before anything was
    written, so a page can show it as Refused instead of Unknown: id_taken (the run identifier, or a policy it would create, exists),
    recipe_refused (bad identifier, unknown template, an unsupported or malformed choice), validation_failed (the profile validator refused the
    result; the answer keeps `problems`), profile_unavailable (no such profile, unreadable or not a mapping, the starter or a scan draft) and
    write_failed_nothing_saved (the write raised AND the profile file is byte-identical to what it was before the attempt, judged under the
    profile lock). revision_conflict, context_changed and invalid_request are as before. A failure after which the file differs, or cannot be
    read back, has NO code: its outcome is unknown and is settled by reading the profile. A request without a guard is answered exactly as it
    always was, without a code.
    """
    from player.run_templates import RunIdTaken, instantiate
    body=request.get_json(force=True) or {}
    if not isinstance(body,dict):
        return jsonify(ok=False,error='Expected run template choices.'),400
    guarded='expected_revision' in body or operation_api.expected_page_context(body) is not None

    def refuse(status,code,error,**extra):
        return jsonify(ok=False,error=error,**({'code':code} if guarded else {}),**extra),status
    name=body.get('profile')
    path=_profile_path(name)
    if not path:
        return refuse(400,'profile_unavailable','Choose a valid local profile.')
    if _is_starter(name) or _is_draft(name):
        return refuse(409,'profile_unavailable','Create a personal profile in Setup before adding run templates.')
    with _profile_lock(path):
        try:
            current=hashlib.sha256(Path(path).read_bytes()).hexdigest() if guarded else None
            if 'expected_revision' in body and body['expected_revision']!=current:
                return jsonify(ok=False,code='revision_conflict',revision=current,
                               error='Saved profile changed. Reload and reconcile the pending edit before saving again.'),409
            with open(path,encoding='utf-8') as fh:
                original=yaml.safe_load(fh)
        except (OSError,ValueError,yaml.YAMLError) as e:
            return refuse(400,'profile_unavailable',str(e))
        if not isinstance(original,dict):
            return refuse(400,'profile_unavailable','Choose a valid local profile.')
        try:
            updated=instantiate(original,body.get('template'),body.get('run_id'),body.get('options'))
        except RunIdTaken as e:
            return refuse(400,'id_taken',str(e))
        except ValueError as e:
            return refuse(400,'recipe_refused',str(e))
        except (OSError,yaml.YAMLError) as e:
            return jsonify(ok=False,error=str(e)),400
        try:
            problems=_profile_check(updated)
        except (OSError,ValueError,yaml.YAMLError) as e:
            return jsonify(ok=False,error=str(e)),400
        if problems:
            return refuse(400,'validation_failed',problems[0],problems=problems)
        try:
            backup=_save_yaml_backup(path,updated)
            stored=hashlib.sha256(Path(path).read_bytes()).hexdigest() if 'expected_revision' in body else None
        except (OSError,ValueError,yaml.YAMLError) as e:
            if guarded and _file_has_digest(path,current):
                return refuse(400,'write_failed_nothing_saved',str(e))
            return jsonify(ok=False,error=str(e)),400
    if stored is not None:
        return jsonify(ok=True,profile=updated,backup=backup,revision=stored)
    return jsonify(ok=True,profile=updated,backup=backup)


def _file_has_digest(path:str,digest:str|None)->bool:
    """True only when `path` can be read and is byte-identical to the file whose SHA-256 is `digest`: the proof that a failed write changed nothing."""
    try:
        return digest is not None and hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest
    except OSError:
        return False


@app.get("/api/profile/<name>")
def api_profile(name):
    # `<stem>.yaml` (the file name /api/profiles lists), resolved by
    # _profile_path: a drive-relative name such as `C:x.yaml` used to pass a
    # separator-only check and os.path.join then left profiles/ for C:'s cwd
    path = _profile_path(name[:-len(".yaml")]) if name.casefold().endswith(".yaml") else None
    if not path:
        return Response("no", status=403)
    try:
        with open(path, encoding="utf-8") as fh:
            return jsonify(yaml.safe_load(fh))
    except OSError:
        return jsonify({"error": "not found"}), 404


# ------------------------------------------------------- profile editor (P6)
#
# THE UX LAW (user, 2026-08-19): every setting is a dropdown, a checkbox or a
# number box. Nobody edits YAML to configure a farm. The Configuration tree
# tab stays as the advanced escape hatch, but nothing in the normal flow
# needs it.
#
# THE VALIDATOR IS THE SINGLE SOURCE OF TRUTH. This module never re-implements
# a profile rule - not one range, not one "shard-only" clause. Every patch is
# applied to an IN-MEMORY copy, handed to _profile_check(), and only
# written if that returns an empty list. The refusal text goes to the browser
# verbatim, because a paraphrase of a rule is a second copy of that rule.
#
# playerprofile is imported INSIDE each handler, never at module scope: this
# dashboard deliberately imports no runner module (a dashboard bug must not be
# able to wedge a live run), and a profile layer that fails to import has to
# degrade to a red box in the UI rather than a dead server.
# `\Z`, not `$`: Python's `$` also matches before a trailing newline, so
# "default\n" passed this and became a filename with a newline in it (audit,
# 2026-08-19). It never escaped profiles/, but a name that reads as valid and
# writes as something else is the start of every path bug.
_PROFILE_NAME_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.\-]*\Z")

# ONE LOCK PER PROFILE, held across the WHOLE read -> patch -> validate ->
# backup -> write transaction (audit, 2026-08-19: two overlapping valid
# patches both returned 200 with the same backup name and one edit vanished).
# Flask serves these on threads, and every patch is a read-modify-write of the
# same file - unlocked, the second read happens before the first write and the
# later writer wins with a stale document. Keyed by resolved path so two names
# for one file cannot take two locks.
_PROFILE_LOCKS: dict[str, threading.Lock] = {}
_PROFILE_LOCKS_GUARD = threading.Lock()
# activate is a read-modify-write of config.yaml through save_config, which has
# the same shape and the same race with any other config writer.



def _profile_lock(path: str) -> threading.Lock:
    key = os.path.normcase(os.path.abspath(path))
    with _PROFILE_LOCKS_GUARD:
        lock = _PROFILE_LOCKS.get(key)
        if lock is None:
            lock = _PROFILE_LOCKS[key] = threading.Lock()
        return lock


def _profiles_dir() -> str:
    return os.path.join(ROOT, "profiles")


STARTER_PROFILE = "default"
_STARTER_READ_ONLY = (
    "profiles/default.yaml is the shipped starter and stays generic - its comments "
    "are the guide for every install and a save would drop them. Make your own "
    "profile first: copy the starter (Home page) or Promote a scan draft, then "
    "activate it")


def _is_starter(name) -> bool:
    """The one profile that ships with the repo. Never written by the
    dashboard: an edit while it is active (a fresh install) would land in
    the tracked file, comments gone (2026-09-06)."""
    return isinstance(name, str) and name.casefold() == STARTER_PROFILE


def _is_draft(name: str) -> bool:
    # casefold BOTH the check and every listing that keys off it (final P6
    # gate finding): NTFS resolves names case-insensitively, so "X.DRAFT"
    # addresses the same file as "x.draft.yaml" and a case-sensitive check
    # let it bypass the draft guards (patch 409 / activate 400 / picker).
    return isinstance(name, str) and name.casefold().endswith(".draft")


def _profile_path(name: str) -> str | None:
    """Absolute path of `profiles/<name>.yaml`, or None if the name is not a
    plain profile name. Rejects separators and `..` before touching disk."""
    if not isinstance(name, str) or not _PROFILE_NAME_RE.fullmatch(name):
        return None
    if ".." in name or "/" in name or "\\" in name:
        return None
    path = os.path.normpath(os.path.join(_profiles_dir(), name + ".yaml"))
    if os.path.dirname(path) != os.path.normpath(_profiles_dir()):
        return None
    return path


def _save_yaml_backup(path: str, data: dict) -> str:
    """Write `data` over `path`, timestamped backup first. Deliberately the
    same shape as save_config: round-trip through yaml BEFORE touching the
    real file (a value the writer cannot represent must fail with the old
    file intact), then keep the last 20 backups."""
    # ONE BACKUP PER EDIT, and this editor edits one field at a time - two
    # changes inside the same second are normal here, so the second-resolution
    # stamp gets a counter. The counter is claimed with O_CREAT|O_EXCL rather
    # than a look-then-write (audit, 2026-08-19): `os.path.exists` followed by
    # `open("w")` is itself a race, and two writers agreeing on a name means
    # the second silently destroys the first's copy - the one holding the
    # value the user wants back. Callers hold the per-profile lock as well;
    # this is the second belt, and the one that also covers any other process.
    with open(path, encoding="utf-8") as fh:
        old = fh.read()
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True,
                          default_flow_style=None)
    yaml.safe_load(text)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup, n = path + f".bak-{stamp}", 0
    while True:
        try:
            fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            n += 1
            backup = path + f".bak-{stamp}_{n}"
            if n > 999:                         # a stuck clock, not a race
                raise
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(old)
        break
    # replaced, never rewritten in place: settings.py parses config.yaml at
    # runner import and GET ticks read it unlocked, so a reader must see the
    # whole old file or the whole new one
    from runtime.files import atomic_text
    atomic_text(path, text)
    for b in sorted(glob.glob(path + ".bak-*"))[:-20]:
        os.remove(b)
    return os.path.basename(backup)


@app.get("/api/flows")
def api_flows():
    """The flow registry, for the UI's run-type cards. Importing `flows` is
    safe here: discovery ast-parses the FLOW literals and executes no runner
    code (see backend/flows/__init__.py)."""
    try:
        import flows
        return jsonify({"ok": True, "flows": flows.flows()})
    except Exception as e:                      # noqa: BLE001 - shown verbatim
        return jsonify({"ok": False, "error": str(e), "flows": {}}), 500


@app.get("/api/vocab")
def api_vocab():
    """Every editable value space, as pure data, straight from the profile
    layer. The UI renders it GENERICALLY - enum to a select, bool to a
    checkbox, int/float to a number box with the stated range, list to an
    ordered editor - so a section this dashboard has never heard of still
    gets an editor instead of being dropped on the floor."""
    try:
        from player import playerprofile
        v = playerprofile.vocab()
    except Exception as e:                      # noqa: BLE001 - shown to user
        return jsonify({"ok": False, "vocab": {},
                        "error": f"{type(e).__name__}: {e}"})
    if not isinstance(v, dict):
        return jsonify({"ok": False, "vocab": {},
                        "error": f"vocab() returned {type(v).__name__}, "
                                 f"expected a dict"})
    return jsonify({"ok": True, "vocab": v})


@app.get("/api/profile-files")
def api_profile_files():
    """Profile NAMES (the stem playerprofile.load takes), with the active
    flag read from config.yaml's `active_profile`."""
    cfg = load_config()
    active = cfg.get("active_profile")
    out = []
    for f in sorted(glob.glob(os.path.join(_profiles_dir(), "*.yaml"))):
        stem = os.path.basename(f)[:-len(".yaml")]
        if _foreign_profile(stem, cfg):
            continue
        draft = _is_draft(stem)
        out.append({"name": stem, "file": os.path.basename(f),
                    "draft": draft,
                    # LISTED BUT NOT PATCHABLE (audit, 2026-08-19). A draft is
                    # scan output - `player:` and nothing else - so it is
                    # missing required sections and every single-field repair
                    # leaves another error behind. Hiding it would be a lie
                    # about what is in profiles/; offering an editor that can
                    # only refuse is worse. It gets a pointer instead.
                    "patchable": not draft,
                    "note": ("scan draft - complete it on the Profile tab "
                             "before editing it here") if draft else None,
                    "mtime": os.path.getmtime(f),
                    "active": stem == active})
    return jsonify({"profiles": out, "active": active})


def _foreign_profile(name, cfg):
    from player import accounts
    active = accounts.identity(cfg)
    return bool(active and any(key != active and (name == record.get("active_profile")
                   or name in (record.get("profiles") or []) or name == key + ".draft")
               for key, record in (cfg.get("accounts") or {}).items()))


@app.get("/api/profile-src/<name>")
def api_profile_src(name):
    """The parsed profile, plus the validator's current verdict on it.

    The verdict rides along because a profile is edited field by field: the
    editor has to show the problems the file ALREADY has, or a refusal on the
    first patch reads as "my edit broke it" when it was broken on arrival.
    """
    path = _profile_path(name)
    if not path:
        return jsonify({"ok": False, "error": f"bad profile name: {name!r}"}), 400
    if not os.path.exists(path):
        return jsonify({"ok": False, "error": f"no such profile: {name}"}), 404
    try:
        with _profile_lock(path):
            raw = Path(path).read_bytes()
            data = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if not isinstance(data, dict):
        return jsonify({"ok": False,
                        "error": f"{name}.yaml is not a mapping"}), 400
    problems, warns, verr = [], [], None
    try:
        from player import playerprofile
        problems = _profile_check(data)
        warns = _profile_warnings(data)
    except Exception as e:                      # noqa: BLE001
        verr = f"{type(e).__name__}: {e}"
    return jsonify({"ok": True, "name": name, "profile": data,
                    "revision": hashlib.sha256(raw).hexdigest(),
                    "problems": problems, "warnings": warns,
                    "validator_error": verr})


def _apply_patch(root: dict, path: list, value, delete: bool) -> str | None:
    """Walk `path` and set (or delete) the leaf. Returns an error string.

    Containers are addressed the way the profile reads: a string step is a
    mapping key, an int step is a list index (and index == len appends, which
    is how the ordered editors add a rule / directive / plan block). A missing
    MAPPING step is created - adding `plan` to a profile that has none is a
    legitimate edit - but a missing LIST index is never invented, because a
    hole in a list is not a thing the schema can express.
    """
    if not isinstance(path, list) or not path:
        return "path must be a non-empty list"
    node = root
    for i, step in enumerate(path[:-1]):
        nxt = path[i + 1]
        if isinstance(step, bool) or not isinstance(step, (str, int)):
            return f"path[{i}]: steps are strings or ints, got {step!r}"
        if isinstance(step, int):
            if not isinstance(node, list):
                return f"path[{i}]: index {step} into a {type(node).__name__}"
            if not 0 <= step < len(node):
                return f"path[{i}]: index {step} out of range"
            node = node[step]
            continue
        if not isinstance(node, dict):
            return f"path[{i}]: key {step!r} into a {type(node).__name__}"
        if step not in node or node[step] is None:
            node[step] = [] if isinstance(nxt, int) and not isinstance(nxt, bool) else {}
        node = node[step]
    leaf = path[-1]
    if isinstance(leaf, bool) or not isinstance(leaf, (str, int)):
        return f"path[-1]: steps are strings or ints, got {leaf!r}"
    if isinstance(leaf, int):
        if not isinstance(node, list):
            return f"path[-1]: index {leaf} into a {type(node).__name__}"
        if delete:
            if not 0 <= leaf < len(node):
                return f"path[-1]: index {leaf} out of range"
            node.pop(leaf)
        elif leaf == len(node):
            node.append(value)                  # append sentinel
        elif 0 <= leaf < len(node):
            node[leaf] = value
        else:
            return f"path[-1]: index {leaf} out of range (append with {len(node)})"
        return None
    if not isinstance(node, dict):
        return f"path[-1]: key {leaf!r} into a {type(node).__name__}"
    if delete:
        node.pop(leaf, None)
    else:
        node[leaf] = value
    return None


@app.post("/api/profile-patch")
@_fenced_by_page_context
def api_profile_patch():
    """One typed field, one request: {name, path: [...], value}.

    VALIDATE BEFORE WRITE, always, on an in-memory copy. The file on disk is
    never the place a bad value is discovered - a profile is loaded by a
    runner at spawn, hours later, and a field that only fails there fails
    where nobody is watching. `op: "delete"` removes the addressed key or
    list element (the ordered editors need it to drop a rule or a block).

    SERIALIZED PER PROFILE. The read, the patch, the validation, the backup
    and the write are ONE transaction under `_profile_lock` (audit,
    2026-08-19: two overlapping patches both answered 200 and one edit was
    gone). Semantics: LAST WRITER WINS, but every writer sees every earlier
    write - the losing shape was a writer that never saw the other edit at
    all. Requests queue rather than 409, because the editor sends one field
    per control and a queued 30ms write is invisible where a refusal is not.
    """
    body = request.get_json(force=True) or {}
    name = body.get("name")
    path = _profile_path(name)
    if not path:
        return jsonify({"ok": False, "error": f"bad profile name: {name!r}"}), 400
    if _is_starter(name):
        return jsonify({"ok": False, "error": _STARTER_READ_ONLY, "starter": True}), 409
    if not os.path.exists(path):
        return jsonify({"ok": False, "error": f"no such profile: {name}"}), 404
    # A DRAFT IS NOT A PROFILE YET. scan.py writes `player:` alone, so it is
    # missing whole required sections and every single-field repair still
    # leaves another - the typed editor can only refuse, forever (audit,
    # 2026-08-19). It is completed on the Profile tab, where the scan that
    # produced it lives; the dashboard will not invent blueprints and
    # policies nobody authored just to make a file validate.
    if _is_draft(name):
        return jsonify({"ok": False, "error": (
            f"{name} is a scan DRAFT, not a profile: it has no blueprints or "
            f"policies yet, so no single field edit can make it valid. "
            f"Complete it on the Profile tab, then edit it here.")}), 409
    with _profile_lock(path):
        try:
            raw = Path(path).read_bytes()
            revision = hashlib.sha256(raw).hexdigest()
            if 'expected_revision' in body and body['expected_revision'] != revision:
                return jsonify(ok=False, code='revision_conflict', revision=revision,
                               error='Saved profile changed. Reload and reconcile the pending edit before saving again.'), 409
            data = yaml.safe_load(raw)
        except (OSError, yaml.YAMLError) as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        if not isinstance(data, dict):
            return jsonify({"ok": False,
                            "error": f"{name}.yaml is not a mapping"}), 400
        delete = body.get("op") == "delete"
        before = copy.deepcopy(data)
        err = _apply_patch(data, body.get("path"), body.get("value"), delete)
        if err:
            return jsonify({"ok": False, "error": err}), 400
        # THE VALIDATOR DECIDES. Its message is the user's message, verbatim.
        try:
            from player import playerprofile
            problems = _profile_check(data)
        except Exception as e:                  # noqa: BLE001
            return jsonify({"ok": False,
                            "error": f"validator unavailable: {type(e).__name__}: {e}"}), 500
        if problems:
            return jsonify({"ok": False, "problems": problems,
                            "error": problems[0]}), 400
        # A RUN'S KIND IS FIXED once the run is added: it picks the runner and decides which fields the run may carry. The validator refuses most
        # changes of it by accident (a field the new kind may not carry, an unknown kind); one that happens to leave a valid profile used to be
        # saved. Checked after the validator, whose specific message wins for a change it refuses, and after the revision and context checks.
        if changes := playerprofile.blueprint_kind_changes(before, data):
            first = changes[0]
            became = f"a {first['to_kind']} run" if first["to_kind"] else "a run without a type"
            return jsonify({"ok": False, "code": "kind_change_refused", "changes": changes,
                            "error": f"Run {first['blueprint']!r} is a {first['from_kind']} run and cannot become {became}: a run's type is fixed when it "
                                     f"is added. Add a new run of the type you want and remove this one."}), 400
        try:
            backup = _save_yaml_backup(path, data)
            stored = Path(path).read_bytes()
            if yaml.safe_load(stored) != data:
                raise RuntimeError('Saved profile readback does not match the requested change')
            revision = hashlib.sha256(stored).hexdigest()
        except Exception as e:                  # noqa: BLE001
            return jsonify({"ok": False, "error": f"write failed: {e}"}), 500
    warns = []
    try:
        from player import playerprofile
        warns = _profile_warnings(data)
    except Exception:                           # noqa: BLE001 - advisory only
        pass
    return jsonify({"ok": True, "backup": backup, "profile": data,
                    "warnings": warns, "revision": revision})


@app.post("/api/loadout-patch")
@_fenced_by_page_context
def api_loadout_patch():
    """One whole loadout body, one request: {name, body}.

    Loadouts live in config.yaml (the machine file), but their CONTENTS are
    what the profile validator's loadout-ownership checks veto - so the
    transaction here mirrors /api/profile-patch: apply to an in-memory copy,
    re-validate the ACTIVE profile against the patched loadouts, and only
    then write (timestamped config backup, config write lock). The verdict
    is the user's message, verbatim.

    playerprofile reads loadouts from the settings.CONFIG singleton, so the
    validation swaps the patched table in under the lock and restores it on
    every path; a successful save keeps it (the dashboard process then
    matches the file it just wrote).
    """
    body = request.get_json(force=True) or {}
    name = body.get("name")
    lo = body.get("body")
    if not isinstance(name, str) or not name.strip():
        return jsonify({"ok": False, "error": f"bad loadout name: {name!r}"}), 400
    if not isinstance(lo, dict) or not lo:
        return jsonify({"ok": False,
                        "error": "body must be a non-empty loadout mapping"}), 400
    with _CONFIG_WRITE_LOCK:
        revision = operation_api.file_revision(CONFIG_PATH)
        if 'expected_revision' in body and body['expected_revision'] != revision:
            return jsonify(ok=False, code='revision_conflict', config_revision=revision,
                           error='Saved configuration changed. Reload and reconcile the pending edit before saving again.'), 409
        cfg = load_config()
        if name not in (cfg.get("loadouts") or {}):
            return jsonify({"ok": False,
                            "error": f"no loadout named {name!r} in "
                                     f"config.yaml"}), 404
        patched = dict(cfg.get("loadouts") or {})
        patched[name] = lo
        # THE RESTORE VALUES (cards_restore, modules_restore) are checked against the account only for a loadout a blueprint of the ACTIVE
        # profile names: the validator reads no other. A body that sets, changes or clears one of them on any other loadout - or when there is no
        # active profile - would be saved looking verified and is not: refused, nothing written. A body whose restore keys are as stored is
        # not a restore edit (the page sends whole bodies) and is judged as it always was.
        from player import playerprofile
        stored_body = (cfg.get("loadouts") or {}).get(name)
        stored_body = stored_body if isinstance(stored_body, dict) else {}
        absent = object()
        restore_changed = [key for key in playerprofile.LOADOUT_RESTORE_KEYS if stored_body.get(key, absent) != lo.get(key, absent)]
        active = cfg.get("active_profile")
        used, problems, warns = [], [], []
        if active:
            ppath = _profile_path(active)
            try:
                with _profile_lock(ppath):
                    with open(ppath, encoding="utf-8") as fh:
                        prof = yaml.safe_load(fh)
                prof = prof if isinstance(prof, dict) else {}
                used = playerprofile.loadouts_in_use(prof)
                if name in used or not restore_changed:
                    candidate = dict(cfg, loadouts=patched)
                    problems = _profile_check(prof, candidate)
                    warns = _profile_check(prof, candidate, warnings=True)
            except Exception as e:              # noqa: BLE001
                return jsonify({"ok": False,
                                "error": f"validator unavailable: "
                                         f"{type(e).__name__}: {e}"}), 500
        if restore_changed and name not in used:
            keys = ", ".join(restore_changed)
            why = (f"is not used by any run of the active profile {active!r}, so its restore values ({keys}) cannot be checked against the "
                   f"account and are not saved. Use the loadout in a run of that profile first." if active else
                   f"cannot be checked: there is no active profile, so its restore values ({keys}) are not saved. Activate a profile that "
                   f"uses the loadout first.")
            return jsonify({"ok": False, "code": "restore_loadout_unused", "loadout": name, "restore_keys": restore_changed,
                            "active_profile": active or None, "error": f"Loadout {name!r} {why}"}), 409
        if problems:
            return jsonify({"ok": False, "problems": problems, "error": problems[0]}), 400
        cfg["loadouts"] = patched
        try:
            backup = save_config(cfg)
            stored = Path(CONFIG_PATH).read_bytes()
            from player import accounts
            if accounts.effective(yaml.safe_load(stored)).get('loadouts') != patched:
                raise RuntimeError('Saved loadout readback does not match the requested change')
            revision = hashlib.sha256(stored).hexdigest()
        except Exception as e:                  # noqa: BLE001
            return jsonify({"ok": False, "error": f"write failed: {e}"}), 500
        from settings import CONFIG as LIVE_CONFIG
        LIVE_CONFIG["loadouts"] = patched
    return jsonify({"ok": True, "backup": backup, "loadouts": patched,
                    "warnings": warns, "config_revision": revision})


@app.post("/api/profile-copy")
@_fenced_by_page_context
def api_profile_copy():
    """Copy a profile file VERBATIM to profiles/<name>.yaml: the way to get
    an editable profile of your own before any scan exists. The starter's
    comments come along (a yaml dump would drop them), the copy is
    git-ignored, every editor works on it. Nothing is activated here."""
    body = request.get_json(force=True) or {}
    src_name = str(body.get("from") or STARTER_PROFILE).strip()
    name = str(body.get("name") or "").strip()
    src = _profile_path(src_name)
    dest = _profile_path(name)
    if not src or _is_draft(src_name) or not os.path.exists(src):
        return jsonify({"ok": False, "error": f"no profile {src_name!r} to copy"}), 404
    if not dest or _is_draft(name):
        return jsonify({"ok": False, "error": "name: letters, digits, _ - . only"}), 400
    if _is_starter(name):
        return jsonify({"ok": False, "error": _STARTER_READ_ONLY, "starter": True}), 409
    if os.path.exists(dest) and not body.get("overwrite"):
        return jsonify({"ok": False, "error": f"profiles/{name}.yaml exists"}), 409
    shutil.copyfile(src, dest)
    return jsonify({"ok": True, "name": name, "copied_from": src_name})


@app.post("/api/profile-activate")
@_fenced_by_page_context
def api_profile_activate():
    """Bind config.yaml to a profile - {name} to set, {name: null} to remove
    the key and go back to the legacy constants.

    Refused while a runner is live, same rule as the wizard's adopt: every
    runner reads `active_profile` at spawn and compiles its presets from it,
    so moving it under a live run means the next restart runs a different
    thing than the one the operator is watching.
    """
    _require_idle("runners alive - stop them first")
    body = request.get_json(force=True) or {}
    name = body.get("name")
    if _foreign_profile(name, load_config()):
        return jsonify({"ok": False, "error": "This profile belongs to another account. Select that account in Setup first."}), 409
    if name in (None, ""):
        # read-modify-write of config.yaml: same transaction discipline as a
        # profile patch, or two activates race and one loses its whole edit
        with _CONFIG_WRITE_LOCK:
            cfg = load_config()
            had = cfg.pop("active_profile", None)
            try:
                backup = save_config(cfg)
            except Exception as e:              # noqa: BLE001
                return jsonify({"ok": False, "error": str(e)}), 500
        return jsonify({"ok": True, "active": None, "backup": backup,
                        "message": (f"unbound {had!r} - the scheduler runs its "
                                    f"legacy constants") if had else
                                   "no profile was bound"})
    path = _profile_path(name)
    if not path or not os.path.exists(path):
        return jsonify({"ok": False, "error": f"no such profile: {name!r}"}), 400
    if _is_draft(name):
        return jsonify({"ok": False, "error": (
            f"{name} is a scan draft, not a runnable profile - complete it on "
            f"the Profile tab first")}), 400
    # A profile that EXISTS but is broken must not be bound: the runner would
    # raise at spawn instead of farming (playerprofile.select_profile). Read it
    # under ITS lock so a patch landing right now is seen whole, not half.
    try:
        with _profile_lock(path):
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        from player import playerprofile
        problems = _profile_check(data if isinstance(data, dict) else {})
    except Exception as e:                      # noqa: BLE001
        return jsonify({"ok": False,
                        "error": f"cannot validate {name}: {type(e).__name__}: {e}"}), 400
    if problems:
        return jsonify({"ok": False, "problems": problems,
                        "error": f"{name} has {len(problems)} problem(s) - "
                                 f"fix them before activating"}), 400
    with _CONFIG_WRITE_LOCK:
        cfg = load_config()
        cfg["active_profile"] = name
        try:
            backup = save_config(cfg)
        except Exception as e:                  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "active": name, "backup": backup,
                    "message": f"config.yaml now runs profile {name!r}"})


@app.get("/api/evidence/<path:rel>")
def api_evidence(rel):
    cfg = load_config()
    inst = cfg.get("active_instance", "main")
    base = os.path.join(_calibration_dir(cfg), "scan_evidence")
    path = os.path.normpath(os.path.join(base, rel))
    if not path.startswith(base + os.sep):
        return Response("no", status=403)
    if not os.path.exists(path):
        return Response("not found", status=404)
    return send_file(path, mimetype="image/png")


# ------------------------------------------------------------------ wizard
# The wizard's findings live SERVER-SIDE (user, 2026-08-18: "this does not
# persist the findings / state so when I refresh nothing is there"). Every
# probe result is stamped and written to logs/wizard_state.json; the page
# renders that file on load and only re-probes when a button is pressed.
_WIZ_STATE = os.path.join(ROOT, "logs", "wizard_state.json")
_WIZ_LOCK = threading.Lock()


def _wiz_load() -> dict:
    try:
        with open(_WIZ_STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:                           # noqa: BLE001
        return {}


def _wiz_save(section: str, payload: dict) -> dict:
    """Persist one wizard section. NEVER raises (audit, 2026-08-19): an
    unwritable logs/ used to turn a completed adopt - config already saved,
    device already connected - into a 500 the caller reads as "nothing
    happened". Failure is reported as a `warning` field on the payload."""
    payload = dict(payload, t=time.time())
    try:
        with _WIZ_LOCK:
            st = _wiz_load()
            st[section] = payload
            os.makedirs(os.path.dirname(_WIZ_STATE), exist_ok=True)
            tmp = _WIZ_STATE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(st, fh, indent=1)
            os.replace(tmp, _WIZ_STATE)
    except Exception as e:                      # noqa: BLE001 - display state
        payload = dict(payload, warning=f"wizard state not saved: {e}")
    return payload


@app.get("/api/wizard/state")
def api_wizard_state():
    return jsonify(_wiz_load())


# host:port (adb tcp) or the usb/emulator token form. Guards the one endpoint
# that WRITES a serial into config.yaml - a typo there points the farm at
# nothing, and the runner's abort would be the first sign.
_SERIAL_RE = re.compile(r"^(?:[A-Za-z0-9_.\-]+:\d{1,5}|emulator-\d+)$")


# SETUP GATE (user, 2026-08-19: "no other tabs should even be possible until
# the first config is scanned and we are ready to operate").
#
# The flag lives in its OWN sentinel file, NOT in wizard_state.json (audit,
# 2026-08-19): wizard_state.json is rewritten wholesale on every scan and
# `_wiz_load` turns a corrupt file into {} - which would silently erase the
# flag and relock a machine that has been farming for weeks. The sentinel is
# write-once, tiny, and read independently: corrupt or unwritable wizard
# state cannot touch it.
#
# Once set it is never auto-unset - an operating machine must keep Status
# reachable when its emulator is merely powered off.
_SETUP_FLAG = os.path.join(ROOT, "logs", "setup_done")
_SETUP_CACHE = {"done": False, "probe_failed_at": 0.0}
_PROBE_BACKOFF = 30.0       # seconds between failed grandfather probes
# Adopt's read-check-write must be atomic (audit round 2: two concurrent
# adopts both passed their preconditions and the last write won).
_ADOPT_LOCK = threading.Lock()


def _mark_setup_complete(how: str) -> None:
    if _SETUP_CACHE["done"] or os.path.exists(_SETUP_FLAG):
        _SETUP_CACHE["done"] = True
        return
    try:
        os.makedirs(os.path.dirname(_SETUP_FLAG), exist_ok=True)
        tmp = _SETUP_FLAG + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(f"{how}\n{datetime.datetime.now().isoformat(timespec='seconds')}\n")
        os.replace(tmp, _SETUP_FLAG)
    except Exception:                           # noqa: BLE001 - gate only
        pass                                    # in-memory flag still holds
    _SETUP_CACHE["done"] = True


def _setup_complete() -> bool:
    if _SETUP_CACHE["done"]:
        return True
    if os.path.exists(_SETUP_FLAG):
        _SETUP_CACHE["done"] = True
        return True
    # GRANDFATHER an already-working machine: this feature ships onto boxes
    # that have been farming for weeks and must not lock their own dashboard.
    # A configured serial whose port answers right now IS a completed setup.
    # BACKOFF (audit, 2026-08-19): /api/status is polled every 2.5 s by every
    # open tab; an unconfigured machine would pay the 0.3 s connect timeout on
    # every single poll forever. One failed probe suppresses the next 30 s.
    if time.time() - _SETUP_CACHE["probe_failed_at"] < _PROBE_BACKOFF:
        return False
    try:
        cfg = load_config()
        inst = (cfg.get("instances") or {}).get(
            cfg.get("active_instance", "main")) or {}
        serial = inst.get("serial") or ""
        if ":" not in serial:
            raise ValueError("no tcp serial configured")
        import socket
        host, _, port = serial.rpartition(":")
        with socket.create_connection((host, int(port)), timeout=0.3):
            pass
    except Exception:                           # noqa: BLE001 - not set up yet
        _SETUP_CACHE["probe_failed_at"] = time.time()
        return False
    _mark_setup_complete("grandfathered")
    return True


# INSTANCE INVENTORY via the emulator's OWN manager (user, 2026-08-18: "why
# doesn't it get the other 2 VMs"). `adb devices` and a port probe only see
# instances whose VM is up; a stopped instance is invisible to both. The
# managers list every configured instance regardless: MuMuManager
# `info -v all` (JSON, index/name/is_android_started, adb_port when up),
# LDPlayer `ldconsole list2`, BlueStacks `bluestacks.conf` instance keys.
# MuMu adb port when stopped: 16384 + 32*index (verified: config serial
# 16480 == index 3 "Main Tower-1"); flagged expected until the VM answers.
def _mumu_manager() -> str | None:
    pfs = [os.environ.get("ProgramFiles", r"C:\Program Files"),
           os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    for pf in pfs:
        for rel in (r"Netease\MuMuPlayer\nx_main\MuMuManager.exe",
                    r"Netease\MuMu Player 12\shell\MuMuManager.exe"):
            p = os.path.join(pf, rel)
            if os.path.exists(p):
                return p
    return None


def _adb_for_family(family: str) -> str | None:
    """First installed adb.exe belonging to an emulator FAMILY (MuMu /
    BlueStacks / LDPlayer). Used to suggest an adb path next to a suggested
    serial: a serial from MuMu's manager is worthless with BlueStacks' adb
    (different server protocol - they kill each other's daemon)."""
    key = (family or "").strip().lower().split(" ")[0]
    if not key:
        return None
    pfs = [os.environ.get("ProgramFiles", r"C:\Program Files"),
           os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    for label, pattern in EMULATOR_ADB_CANDIDATES:
        if pattern == "adb" or not label.lower().startswith(key):
            continue
        for pf in pfs:
            path = pattern.format(pf=pf)
            if os.path.exists(path):
                return path
    return None


def _emulator_instances() -> list[dict]:
    out: list[dict] = []
    mgr = _mumu_manager()
    if mgr:
        try:
            r = _run([mgr, "info", "-v", "all"], capture_output=True,
                     timeout=15, text=True)
            data = json.loads(r.stdout or "{}")
            # a single instance comes back as one object, several as a map
            items = ([data] if "index" in data else list(data.values()))
            for it in items:
                idx = int(it.get("index", -1))
                port = it.get("adb_port")
                out.append({
                    "emulator": "MuMu", "index": idx, "name": it.get("name"),
                    "running": bool(it.get("is_android_started")),
                    "process": bool(it.get("is_process_started")),
                    "adb_port": port or (16384 + 32 * idx if idx >= 0 else None),
                    "port_source": "manager" if port else "expected",
                    "manager": mgr})
        except Exception as e:                  # noqa: BLE001
            out.append({"emulator": "MuMu", "error": f"manager: {e}"})
    for pf in (os.environ.get("ProgramFiles", r"C:\Program Files"),):
        ld = os.path.join(pf, r"LDPlayer\LDPlayer9\ldconsole.exe")
        if os.path.exists(ld):
            try:
                r = _run([ld, "list2"], capture_output=True, timeout=15, text=True)
                for line in r.stdout.splitlines():
                    f = line.split(",")
                    if len(f) >= 5:
                        out.append({"emulator": "LDPlayer", "index": int(f[0]),
                                    "name": f[1], "running": f[4] == "1",
                                    "adb_port": 5555 + 2 * int(f[0]),
                                    "port_source": "expected", "manager": ld})
            except Exception as e:              # noqa: BLE001
                out.append({"emulator": "LDPlayer", "error": f"ldconsole: {e}"})
    bs = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                      r"BlueStacks_nxt\bluestacks.conf")
    if os.path.exists(bs):
        try:
            with open(bs, encoding="utf-8", errors="replace") as fh:
                txt = fh.read()
            # which instances are UP: HD-Player.exe carries the key on its
            # command line (`HD-Player.exe --instance Pie64`, the same form
            # the Start Menu shortcut uses)
            live_keys = set()
            try:
                from runtime.runners import process_rows
                for _pr, info in process_rows(["name", "cmdline"]):
                    if "hd-player" in (info["name"] or "").lower():
                        args = info["cmdline"] or []
                        for i, a in enumerate(args):
                            if a == "--instance" and i + 1 < len(args):
                                live_keys.add(args[i + 1])
            except Exception:                   # noqa: BLE001
                pass
            adb_on = 'bst.enable_adb_access="1"' in txt
            for m in re.finditer(r'bst\.instance\.(\w+)\.display_name="([^"]*)"', txt):
                key, name = m.group(1), m.group(2)
                pm = re.search(rf'bst\.instance\.{key}\.adb_port="(\d+)"', txt)
                def _val(field):
                    mm = re.search(rf'bst\.instance\.{key}\.{field}="([^"]*)"', txt)
                    return mm.group(1) if mm else None
                w, h, dpi = _val("fb_width"), _val("fb_height"), _val("dpi")
                out.append({"emulator": "BlueStacks", "index": key, "name": name,
                            "running": key in live_keys,
                            "adb_enabled": adb_on,
                            "display": (f"{w}x{h}@{dpi}" if w and h and dpi else None),
                            "display_ok": (w, h, dpi) == _BS_DISPLAY_OK,
                            "adb_port": int(pm.group(1)) if pm else None,
                            "port_source": "conf" if pm else None})
        except Exception as e:                  # noqa: BLE001
            out.append({"emulator": "BlueStacks", "error": f"conf: {e}"})
    return out


def _program_files_dirs() -> list[str]:
    out = []
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        d = os.environ.get(var)
        if d and d not in out:
            out.append(d)
    return out or [r"C:\Program Files"]


def _bluestacks_player() -> str | None:
    for pf in _program_files_dirs():
        exe = os.path.join(pf, "BlueStacks_nxt", "HD-Player.exe")
        if os.path.exists(exe):
            return exe
    return None


def _adopt_placeholder(serial: str, adb: str | None) -> str:
    """Start-button counterpart of /api/wizard/adopt for a FRESH install:
    when the active instance has no serial yet, write this emulator's serial
    (and its adb binary) so the boot pipeline has something to drive. An
    instance that already points somewhere is left alone - repointing it is
    the adopt endpoint's explicit act, never a side effect of Start."""
    with _CONFIG_WRITE_LOCK:
        cfg = load_config()
        name = cfg.get("active_instance", "main")
        inst = (cfg.get("instances") or {}).get(name)
        if not isinstance(inst, dict):
            return f" - no instance {name!r} in config.yaml; nothing adopted"
        if inst.get("serial"):
            if inst["serial"] == serial:
                return ""
            return (f" - instance {name!r} keeps {inst['serial']}; use 'Use this "
                    f"one' to repoint it at {serial}")
        inst["serial"] = serial
        if adb and os.path.exists(adb):
            cfg.setdefault("adb", {})["exe"] = adb
        try:
            save_config(cfg)
        except Exception as e:                  # noqa: BLE001
            return f" - could not write config.yaml: {e}"
    return f" - instance {name!r} now points at {serial}"


def _boot_pipeline_for(serial: str) -> str:
    """Start boot.py for the configured instance that uses `serial`, if any.
    Returns the sentence to append to the launch message."""
    cfg = load_config()
    inst = next((n for n, i in (cfg.get("instances") or {}).items()
                 if isinstance(i, dict) and i.get("serial") == serial), None)
    if not inst:
        return (f" - no configured instance uses {serial}; adopt it once it is "
                "up, the game auto-launch is skipped this time")
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    cfg['active_instance'] = inst
    _launch_operation(cfg, "boot", {}, [pyw, os.path.join(ROOT, "device", "boot.py"), "--instance", inst])
    return (f" - boot pipeline started for '{inst}': waits for Android, clears "
            "ad overlays, launches the game")


def _launch_bluestacks(key: str):
    """`HD-Player.exe --instance <key>` - the exact command BlueStacks' own
    Start Menu shortcut runs - then the same boot pipeline MuMu gets. The adb
    daemon is started with BlueStacks' HD-Adb.exe only when no OTHER
    emulator's adb is configured (a foreign adb kills the shared daemon)."""
    if not re.fullmatch(r"[A-Za-z0-9_]+", key):
        return jsonify({"ok": False, "error": "BlueStacks instance key expected"}), 400
    player = _bluestacks_player()
    if not player:
        return jsonify({"ok": False, "error": "HD-Player.exe not found under "
                        "BlueStacks_nxt"}), 400
    entry = next((i for i in _emulator_instances()
                  if i.get("emulator") == "BlueStacks" and i.get("index") == key), None)
    if entry is None:
        return jsonify({"ok": False, "error": f"no BlueStacks instance {key!r} in "
                        "bluestacks.conf"}), 400
    if entry.get("running"):
        msg = f"BlueStacks '{entry.get('name') or key}' is already running"
    else:
        try:
            subprocess.Popen([player, "--instance", key],
                             creationflags=subprocess.DETACHED_PROCESS | NO_WINDOW)
        except Exception as e:                  # noqa: BLE001
            return jsonify({"ok": False, "error": f"HD-Player: {e}"}), 500
        msg = f"BlueStacks '{entry.get('name') or key}' starting"
    hd_adb = os.path.join(os.path.dirname(player), "HD-Adb.exe")
    cfg_adb = (load_config().get("adb") or {}).get("exe") or ""
    foreign = (os.path.exists(cfg_adb)
               and os.path.normcase(cfg_adb) != os.path.normcase(hd_adb))
    if foreign:
        msg += (f" - adb daemon NOT started: config points at {cfg_adb}; adopt "
                "this instance to switch to HD-Adb.exe")
    elif os.path.exists(hd_adb):
        try:
            _run([hd_adb, "start-server"], capture_output=True, timeout=30)
            msg += " - adb daemon started with HD-Adb.exe"
        except Exception as e:                  # noqa: BLE001
            msg += f" - adb start-server failed: {e}"
    if entry.get("adb_enabled") is False:
        msg += (" - WARNING: BlueStacks' Android Debug Bridge is OFF in "
                "bluestacks.conf; nothing answers on the adb port until you "
                "switch it on (Settings > Advanced)")
    port = entry.get("adb_port")
    if port:
        serial = f"127.0.0.1:{port}"
        msg += _adopt_placeholder(serial, hd_adb if not foreign else None)
        msg += _boot_pipeline_for(serial)
    return jsonify({"ok": True, "message": msg})


# BlueStacks keeps a LANDSCAPE framebuffer and rotates the display when a
# portrait-only app such as The Tower comes to the front, so 2560x1080 rotated
# is the 1080x2560 layout every template was cut at. A portrait framebuffer
# (1080x2560) reads right in `wm size` but Unity never produces a frame on it:
# blank window, SurfaceFlinger latency table all zero (seen 2026-09-05).
_BS_WANT_DISPLAY = {"fb_width": "2560", "fb_height": "1080", "dpi": "360",
                    "custom_resolution_selected": "1"}
_BS_DISPLAY_OK = ("2560", "1080", "360")


def _bluestacks_conf_path() -> str:
    return os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                        "BlueStacks_nxt", "bluestacks.conf")


def _prepare_bluestacks(key: str) -> dict:
    """Set what Tower Pilot needs in bluestacks.conf for one instance: the
    global Android Debug Bridge switch on, and the instance's framebuffer at
    2560x1080 landscape / 360 dpi - BlueStacks rotates it to the 1080x2560
    portrait layout every template was cut at when The Tower starts. BlueStacks
    reads the file when the instance starts and rewrites it while a player
    runs, so this refuses unless every HD-Player.exe is closed. A timestamped
    backup of the file is written first. Returns what changed."""
    if not re.fullmatch(r"[A-Za-z0-9_]+", key):
        raise ValueError("BlueStacks instance key expected")
    path = _bluestacks_conf_path()
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} not found")
    try:
        from runtime.runners import process_rows
        if any("hd-player" in (info["name"] or "").lower()
               for _pr, info in process_rows(["name"])):
            raise RuntimeError("BlueStacks is running - close every BlueStacks "
                               "window first (it rewrites bluestacks.conf on "
                               "exit and would undo the change)")
    except ImportError:
        pass
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if not re.search(rf'^bst\.instance\.{key}\.display_name=', text, re.M):
        raise ValueError(f"no instance {key!r} in bluestacks.conf")
    want = {"bst.enable_adb_access": "1"}
    want.update({f"bst.instance.{key}.{k}": v for k, v in _BS_WANT_DISPLAY.items()})
    changed, added = {}, []
    for full, val in want.items():
        pat = re.compile(rf'^{re.escape(full)}="([^"]*)"$', re.M)
        m = pat.search(text)
        if m is None:
            text = text.rstrip("\n") + f'\n{full}="{val}"\n'
            added.append(full)
        elif m.group(1) != val:
            changed[full] = {"from": m.group(1), "to": val}
            text = pat.sub(f'{full}="{val}"', text, count=1)
    if not changed and not added:
        return {"changed": {}, "added": [], "backup": None,
                "message": "already prepared (ADB on, 2560x1080 @ 360)"}
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{path}.bak-{stamp}"
    with open(path, encoding="utf-8", errors="replace") as src, \
            open(backup, "w", encoding="utf-8") as dst:
        dst.write(src.read())
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return {"changed": changed, "added": added, "backup": backup,
            "message": (f"bluestacks.conf updated for {key}: "
                        + ", ".join(f"{k.split('.')[-1]} {v['from']}->{v['to']}"
                                    for k, v in changed.items())
                        + (" (+" + ", ".join(a.split(".")[-1] for a in added) + ")"
                           if added else "")
                        + f"; backup {os.path.basename(backup)}")}


@app.post("/api/wizard/bluestacks/prepare")
def api_wizard_bluestacks_prepare():
    body = request.get_json(force=True) or {}
    try:
        return jsonify({"ok": True, **_prepare_bluestacks(str(body.get("index") or ""))})
    except (ValueError, FileNotFoundError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    except OSError as e:
        return jsonify({"ok": False, "error": f"cannot write bluestacks.conf: {e}"}), 500


_TOOLS_STATE = {"stage": "idle", "message": "Connection tools have not been installed", "percent": 0}
_TOOLS_LOCK = threading.Lock()


@app.get("/api/wizard/tools")
def api_wizard_tools():
    from device import tool_install
    with _TOOLS_LOCK:
        state = dict(_TOOLS_STATE)
    path = tool_install.installed(ROOT)
    return jsonify(dict(state, installed=bool(path), path=path))


@app.post("/api/wizard/tools/install")
def api_wizard_tools_install():
    _require_idle("Stop automation before preparing connection tools")
    body = request.get_json(force=True) or {}
    if body.get("accept_license") is not True:
        return jsonify(error="Accept the Android SDK terms before downloading"), 400
    with _TOOLS_LOCK:
        if _TOOLS_STATE["stage"] in ("downloading", "installing"):
            return jsonify(ok=True, **_TOOLS_STATE)
        _TOOLS_STATE.update(stage="downloading", message="Starting download", percent=0)

    def worker():
        from device import tool_install
        def progress(stage, message, percent):
            with _TOOLS_LOCK:
                _TOOLS_STATE.update(stage=stage, message=message, percent=percent)
        try:
            tool_install.install(ROOT, progress)
        except Exception as exc:
            progress("error", f"Could not install connection tools: {exc}. Check your internet connection and retry.", 0)
    threading.Thread(target=worker, daemon=True).start()
    return jsonify(ok=True, stage="downloading"), 202


@app.post("/api/wizard/launch")
def api_wizard_launch():
    """Start a stopped MuMu instance through MuMuManager (`control -v <i>
    launch`), then hand off to boot.py (user, 2026-08-21: "when we click
    launch it needs to do the pre-launching check for advertising and to
    launch the game as well"). The pipeline runs detached, same as a
    scan: wait for adb + Android, dismiss ad overlays (overlays.clean),
    start The Tower, verify a known screen. It only runs when a
    configured instance's serial matches the VM's adb port - a VM no
    instance drives gets the bare emulator launch, nothing more.
    Explicit user click only; refused while a runner (or an earlier
    boot pipeline) is live."""
    _require_idle("runners alive - stop them first")
    body = request.get_json(force=True) or {}
    if body.get("emulator") == "BlueStacks":
        return _launch_bluestacks(str(body.get("index") or ""))
    mgr = _mumu_manager()
    if body.get("emulator") != "MuMu" or not mgr:
        return jsonify({"ok": False, "error": "launch is wired for MuMu and "
                        "BlueStacks only"}), 400
    idx = str(int(body["index"]))
    try:
        r = _run([mgr, "control", "-v", idx, "launch"], capture_output=True,
                 timeout=30, text=True)
        msg = (r.stdout or r.stderr or "").strip()
        if r.returncode != 0:
            return jsonify({"ok": False, "message": msg})
        serial = f"127.0.0.1:{16384 + 32 * int(idx)}"   # MuMu's port scheme
        mumu_adb = os.path.join(os.path.dirname(mgr), "adb.exe")
        if not os.path.isfile(mumu_adb):
            from device import tool_install
            mumu_adb = tool_install.installed(ROOT) or mumu_adb
        msg += _adopt_placeholder(serial, mumu_adb)
        msg += _boot_pipeline_for(serial)
        return jsonify({"ok": True, "message": msg})
    except Exception as e:                      # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/wizard/emulators")
def api_wizard_emulators():
    # PROBE GUARD (2026-08-18, learned live): running a FOREIGN adb.exe
    # restarts the shared adb server, and the configured emulator's transport
    # drops for a few seconds - a live runner then eats a CaptureError
    # mid-run (shard loop 18 did, recovered only thanks to _kick_adb). While
    # any runner is active, only the configured adb may be queried; the
    # others are still listed as installed, probe deferred.
    # FOREIGN adb binaries are NEVER run any more (2026-08-18, seen live):
    # BlueStacks' HD-Adb.exe speaks server protocol 36, MuMu's adb 41 - each
    # `devices` call kills the other's daemon ("server version doesn't
    # match; killing..."), the transport flaps to `offline` and a connect
    # attempted in that window fails. Only the configured adb touches the
    # daemon; other installs are listed as installed, ports come from the
    # manager inventory below.
    runners_live = True
    cfg = load_config()
    cfg_adb = os.path.normcase(cfg["adb"]["exe"])
    active_inst = (cfg.get("instances") or {}).get(cfg.get("active_instance", "main")) or {}
    found = []
    pfs = [os.environ.get("ProgramFiles", r"C:\Program Files"),
           os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    for label, pattern in EMULATOR_ADB_CANDIDATES:
        paths = ([pattern] if pattern == "adb"
                 else [pattern.format(pf=pf) for pf in pfs])
        for path in paths:
            if pattern != "adb" and not os.path.exists(path):
                continue
            if runners_live and os.path.normcase(path) != cfg_adb:
                found.append({"emulator": label, "adb": path, "devices": [],
                              "note": "installed - not probed (a foreign adb "
                                      "restarts the shared daemon); set adb.exe "
                                      "in Configuration to use it"})
                break
            # the server's own `device` rows over its socket (host:devices,
            # CLAUDE.md rule 1 - architecture review R16); no server
            # listening (the connect REFUSED - not a timeout, a slow server
            # is listening and start-server from another adb version kills
            # it) -> ONE start-server with this configured adb, the
            # sanctioned lifecycle spawn, and ask again. NOT when the active
            # instance has a serial: a cold server never re-attaches its TCP
            # device (seen live 2026-10-12 16:39:33), and the status poll's
            # _reconnect_tick starts the server AND connects the device
            # (adbclient.recover), so this adb is skipped like any failure
            from device import adbclient
            try:
                try:
                    devices = adbclient.devices()
                except ConnectionRefusedError:
                    if active_inst.get("serial"):
                        continue
                    _run([path, "start-server"], capture_output=True, timeout=15)
                    devices = adbclient.devices()
            except Exception:                   # noqa: BLE001
                continue
            found.append({"emulator": label, "adb": path,
                          "devices": devices})
            break
    # WHAT IS ACTUALLY ON THIS MACHINE (user, 2026-08-18: "this has no idea
    # what devices I have"). `adb devices` answers only for instances the
    # daemon currently holds a transport to - an emulator whose VM is down or
    # restarting reports nothing, which reads as "no emulator" when the
    # emulator window is plainly open. So also report: running emulator
    # PROCESSES, and the CONFIGURED serial with a live port probe, so the
    # page can say "MuMu is running but its adb port 16480 refuses" instead
    # of "none".
    import socket
    running = []
    try:
        marks = {"MuMu": ("mumu",), "BlueStacks": ("hd-player", "bluestacks"),
                 "LDPlayer": ("dnplayer", "ldplayer")}
        seen = set()
        from runtime.runners import process_rows
        for _pr, info in process_rows(["name"]):
            n = (info["name"] or "").lower()
            for label, keys in marks.items():
                if label not in seen and any(k in n for k in keys):
                    running.append({"emulator": label, "process": info["name"]})
                    seen.add(label)
    except Exception:                       # noqa: BLE001
        pass
    cfg = load_config()
    from device import adbclient
    configured = []
    # each configured serial's adb-server state (one socket host query each,
    # CLAUDE.md rule 1): the step-3 "configured device is down" test. A port
    # probe could not judge an `emulator-NNNN` serial, so it always read down.
    configured_states = []
    for name, inst in (cfg.get("instances") or {}).items():
        serial = (inst or {}).get("serial") or ""
        configured_states.append(adbclient.get_state(serial))
        port_ok = None
        if ":" in serial:
            host, _, port = serial.rpartition(":")
            try:
                with socket.create_connection((host, int(port)), timeout=1.5):
                    port_ok = True
            except OSError:
                port_ok = False
        configured.append({"instance": name, "serial": serial,
                           "port_open": port_ok})
    instances = _emulator_instances()
    # STEP-3 SUGGESTION (user, 2026-08-19: step 3 told the user to hand-copy
    # the detected adb path and serial into the Configuration tab, which the
    # wizard already knows). Offered ONLY when the configured device is
    # unreachable AND exactly one live candidate exists: two candidates means
    # two accounts (main is 16480 here while a second VM may be up on 16384),
    # and silently picking one would send the farm to the wrong account.
    candidates: list[dict] = []
    seen_serials: set[str] = set()
    for i in instances:
        if i.get("running") is not True or not i.get("adb_port"):
            continue
        ser = f"127.0.0.1:{i['adb_port']}"
        if ser in seen_serials:
            continue
        seen_serials.add(ser)
        candidates.append({"serial": ser, "emulator": i.get("emulator"),
                           "name": i.get("name"),
                           "adb": _adb_for_family(i.get("emulator") or "")})
    # ONE VM, TWO DAEMON SERIALS (seen live 2026-10-12): a MuMu VM is listed
    # as its manager port `127.0.0.1:16384` AND the console token
    # `emulator-5554`. The `emulator-NNNN` tokens go to `also_seen` instead of
    # becoming candidates when the daemon ALSO lists at least as many
    # manager-backed host:port serials (the inventory candidates above) as
    # tokens - each such VM can explain one token, and its host:port serial
    # is the one kept. The daemon is shared, so the label of the adb binary
    # that ran `devices` says nothing about which emulator owns a device and
    # is not compared. A token with no listed host:port serial beside it
    # cannot be attributed to a VM, so every token stays a candidate (no
    # auto-pick). A configured serial is never folded: it is the device the
    # instance already drives. `scanned` still lists every daemon serial.
    configured_serials = {c["serial"] for c in configured}
    also_seen: list[str] = []
    for f in found:
        devices = f.get("devices") or []
        tokens = [dv for dv in devices
                  if dv.startswith("emulator-") and dv not in configured_serials]
        listed_vms = [c for c in candidates if c["serial"] in devices]
        fold = len(tokens) <= len(listed_vms)
        for dv in f.get("devices") or []:
            # `emulator-NNNN` counts too (audit, 2026-08-19): adopt accepts
            # the token, so excluding it here left a machine whose only
            # device is an AVD with no suggestion at all.
            if not _SERIAL_RE.match(dv) or dv in seen_serials:
                continue
            seen_serials.add(dv)
            if fold and dv in tokens:
                also_seen.append(dv)
                continue
            candidates.append({"serial": dv, "emulator": f.get("emulator"),
                               "name": "daemon device", "adb": f.get("adb")})
    configured_down = all(s != "device" for s in configured_states)
    suggestion = None
    if configured_down and len(candidates) == 1:
        # adb path is omitted when that family's binary is not on disk
        suggestion = {k: v for k, v in candidates[0].items() if v}
        suggestion["serial"] = candidates[0]["serial"]
        if also_seen:
            suggestion["also_seen"] = also_seen
    active = cfg.get("active_instance", "main")
    placeholder = not ((cfg.get("instances") or {}).get(active) or {}).get("serial")
    return jsonify(_wiz_save("emulators", {
        "scanned": found, "running": running, "configured": configured,
        "instances": instances, "suggestion": suggestion,
        "config_serial_placeholder": placeholder, "active_instance": active}))


@app.post("/api/wizard/reconnect")
def api_wizard_reconnect():
    """`adb connect <serial>` for a configured instance - the daemon-
    lifecycle spawn is sanctioned (CLAUDE.md), window-suppressed. Refused
    while a runner is live (a reconnect can bounce the transport). `ok` is
    whether the adb server then lists the device as `device`; `device_state`
    is what it listed."""
    _require_idle("runners alive - stop them first")
    body = request.get_json(force=True) or {}
    cfg = load_config()
    configured = cfg["instances"][cfg.get("active_instance", "main")]["serial"]
    serial = body.get("serial") or configured
    # it goes to adb.exe's argv: the configured one, or a form adopt accepts
    if serial != configured and not (isinstance(serial, str) and _SERIAL_RE.fullmatch(serial)):
        return jsonify({"ok": False, "error": "serial: host:port or emulator-NNNN expected"}), 400
    adb = cfg["adb"]["exe"]
    from device import adbclient
    # one read before the connect: a 'missing' answer ends the offline clock of
    # a transport the server dropped while no poll was looking, so the fresh
    # transport the connect makes is not judged by the dead one's age
    adbclient.get_state(serial)
    try:
        out = _run([adb, "connect", serial], capture_output=True, timeout=10,
                   text=True)
        msg = (out.stdout or out.stderr or "").strip()
    except Exception as e:                  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500
    # ok = the adb server lists the device online, not what `adb connect`
    # said: 'already connected' also answers for a transport the server holds
    # offline, and a fresh one reads offline until its handshake completes, so
    # an offline read is re-asked up to 10 more times, 0.2 s apart
    state = adbclient.get_state(serial)
    for _ in range(10):
        if state != "offline":
            break
        time.sleep(0.2)
        state = adbclient.get_state(serial)
    if state != "device":
        msg += f" - the adb server lists the device as {state}"
    # the status poll's device state is cached 10 s - drop it so the next poll
    # re-queries the adb server instead of trusting the connect message
    _CONN_CACHE["t"] = 0.0
    _reconnect_reset()              # a person asked: auto-reconnect starts over
    return jsonify({"ok": state == "device", "message": msg, "device_state": state})


@app.post("/api/wizard/adopt")
def api_wizard_adopt():
    """Step 3 of the wizard, done FOR the user (2026-08-19): write the
    discovered adb.exe + serial into config.yaml (timestamped backup, as
    every save here does) and `adb connect` the device. It used to be a
    paragraph asking the user to hand-copy two values the wizard already
    knew. Refused while a runner is live - repointing the config and
    bouncing the transport under a live run is how you lose a run.

    PRECONDITIONS (audit, 2026-08-19 - the auto-adopt TOCTOU): the client
    sends what it BELIEVED when it decided to adopt - `expect_instance` (the
    active instance the suggestion was computed for) and, on the automatic
    fresh-install path only, `expect_placeholder: true` (that instance had
    no serial at all). Config is re-read HERE, immediately before the write,
    and both are re-checked; anything moved in between - the user edited
    config.yaml, another tab adopted first, a second VM came up - is a 409,
    not an overwrite. Without this a stale scan could repoint a configured
    farm at the wrong account."""
    _require_idle("runners alive - stop them first")
    body = request.get_json(force=True) or {}
    raw = body.get("serial")
    serial = raw.strip() if isinstance(raw, str) else ""
    if not serial or not _SERIAL_RE.match(serial):
        return jsonify({"ok": False,
                        "error": f"not a device serial: {raw!r}"}), 400
    # The whole read-check-write is one critical section (audit round 2: two
    # concurrent adopts both passed their preconditions and last-write-won).
    # One process serves this app, so a module lock IS the CAS.
    with _ADOPT_LOCK:
        # re-read INSIDE the lock, right before the write - never trust the
        # config snapshot the scan that produced this suggestion was built on
        cfg = load_config()
        inst = cfg.get("active_instance", "main")
        expect_inst = body.get("expect_instance")
        if expect_inst is not None and expect_inst != inst:
            return jsonify({"ok": False, "error": (
                f"active instance changed since the scan: expected "
                f"{expect_inst!r}, config now says {inst!r} - rescan")}), 409
        instances = cfg.get("instances")
        if not isinstance(instances, dict) or not isinstance(instances.get(inst), dict):
            # NEVER create an instance (audit): a serial-only stub has no
            # display, no preset, no allow_taps - a runner started on it
            # misbehaves in ways that look like a game bug. Instances are
            # authored in Configuration.
            return jsonify({"ok": False, "error": (
                f"instance {inst!r} does not exist in config.yaml - create it in "
                f"Configuration first (this endpoint never invents instances)")}), 400
        current = instances[inst].get("serial") or ""
        if body.get("expect_placeholder") and current:
            return jsonify({"ok": False, "error": (
                f"instance {inst!r} already has serial {current!r} - refusing the "
                f"automatic adopt (it only ever fills an EMPTY serial)")}), 409
        adb = body.get("adb")
        adb = adb.strip() if isinstance(adb, str) else ""
        if not adb:
            from device import tool_install
            configured = (cfg.get("adb") or {}).get("exe") or ""
            adb = configured if os.path.isfile(configured) else tool_install.installed(ROOT) or ""
        if adb and os.path.exists(adb):
            cfg.setdefault("adb", {})["exe"] = adb
        instances[inst]["serial"] = serial
        try:
            backup = save_config(cfg)
        except Exception as e:                  # noqa: BLE001 - shown to user
            return jsonify({"ok": False, "error": str(e)}), 500
    _CONN_CACHE["t"] = 0.0      # serial changed: next /api/status re-queries adb
    _reconnect_reset()          # ...and auto-reconnect starts over
    # sanctioned daemon-lifecycle spawn (CLAUDE.md), window-suppressed by _run.
    # SKIPPED for `emulator-NNNN` serials: those are already-attached USB/
    # emulator transports, and `adb connect` only speaks host:port - calling
    # it on a token just returns a parse error that reads like a failure.
    adb_exe = (cfg.get("adb") or {}).get("exe") or ""
    connected = None
    if ":" not in serial:
        # `adb connect` does not apply to emulator-NNNN tokens - but a stale
        # device row must not fake a connection either (audit round 2: an
        # unverified token adopt could permanently unlock setup on a dead
        # transport). Prove the transport over the adb-server socket.
        try:
            from device import adbclient
            adbclient.exec_out(serial, "echo ok", timeout=3)
            msg = "attached transport verified over the adb-server socket"
            connected = True
        except Exception as e:                  # noqa: BLE001
            msg = f"transport {serial} did not answer: {e}"
            connected = False
    elif adb_exe:
        try:
            out = _run([adb_exe, "connect", serial], capture_output=True,
                       timeout=10, text=True)
            msg = (out.stdout or out.stderr or "").strip()
        except Exception as e:                  # noqa: BLE001
            msg = f"connect failed: {e}"
    else:
        msg = "no adb.exe configured - set adb.exe in Configuration"
    if connected is None:
        connected = "connected" in msg.lower() and "cannot" not in msg.lower()
    saved = _wiz_save("adopt", {"serial": serial, "adb": adb_exe,
                                "instance": inst, "backup": backup,
                                "connected": connected, "message": msg})
    # A successful connect proves the wiring - that is the setup gate's
    # second set-point (the first is a confirmed 1080x2560 in step 2).
    if connected:
        _mark_setup_complete("adopt")
    # ok = the config WAS saved; a failed connect is reported, never undone
    return jsonify(dict(saved, ok=True))


@app.get("/api/wizard/resolution")
def api_wizard_resolution():
    """`wm size` over the ADB SERVER SOCKET, never adb.exe (audit,
    2026-08-19). This runs after every successful adopt, so spawning a
    process here was a non-lifecycle adb.exe spawn on a path the user
    triggers repeatedly - exactly what CLAUDE.md forbids. Same pattern as
    /api/frame.png: adbclient is a pure socket client, and importing it
    breaks none of the module's rules (settings/capture/orchestrator stay out).

    The `adb` query parameter is ACCEPTED AND IGNORED - the socket talks to
    the one adb server on this machine, whichever binary started it. Kept so
    existing callers and bookmarked URLs keep working.

    The size comes from the raw `screencap` header, not `wm size`: `wm size`
    reports the physical panel, and BlueStacks keeps a 2560x1080 landscape
    panel that it rotates when The Tower is in front - screencap then
    delivers a 1080x2560 frame while `wm size` still says 2560x1080 (seen
    2026-09-05). The screencap header is exactly what capture.grab's
    resolution lock enforces, so it is the truth this check has to match."""
    serial = request.args["serial"]
    try:
        from device import adbclient
        cfg = load_config()
        inst = cfg.get("instances", {}).get(cfg.get("active_instance", "main")) or {}
        refused = _bad_device_query(inst.get("serial"))
        if refused:
            return refused
        configured = inst.get("display") if inst.get("serial") == serial else None
        display = request.args.get("display") or _game_display(serial, configured)
        raw = adbclient.exec_out(serial, _screencap_cmd(serial, display, png=False), timeout=20)
        if len(raw) < 8:
            raise RuntimeError("screencap answered nothing")
        # a multi-display emulator prefixes the raw payload with a text
        # warning line ("[Warning] Multiple displays ..."): the first bytes
        # then read "[War" x "ning" - drop leading lines until the header is
        # sane, exactly as capture.grab does
        w = h = 0
        for _ in range(4):
            if len(raw) < 8:
                break
            w, h = struct.unpack("<II", raw[:8])
            if w <= 10000 and h <= 10000:
                break
            nl = raw.find(b"\n", 0, 400)
            if nl < 0:
                break
            raw = raw[nl + 1:]
        if len(raw) < 8 or not w or not h or w > 10000 or h > 10000:
            _forget_display(serial)
            raise RuntimeError("screencap answered text, not a frame - which display is the game on?")
    except Exception as e:                      # noqa: BLE001
        return jsonify(_wiz_save("resolution", {
            "ok": False, "serial": serial,
            "error": f"{e} (no adb server, or the device is not attached - "
                     f"use adb connect above first)"}))
    if 0 < w < h:
        # the device answered at the calibrated resolution: wiring proven
        _mark_setup_complete("resolution")
    return jsonify(_wiz_save("resolution", {
        "ok": True, "serial": serial, "width": int(w), "height": int(h),
        "expected": 0 < w < h,
        "reference": (w, h) == (1080, 2560),
        "note": ("Native display detected. Scanning applies its layout profile and verifies controls."
                 if 0 < w < h else "Open The Tower in portrait, then check again.")}))


@app.get("/api/wizard/templates")
def api_wizard_templates():
    from player import accounts
    cfg = load_config()
    paths = accounts.template_files(ROOT, cfg, "**/*.png")
    have = []
    for path in paths:
        parts = path.parts
        at = len(parts) - 1 - list(reversed(parts)).index("templates")
        have.append("/".join(parts[at + 1:]))
    # templates the code ASKED for and did not find, straight from the logs -
    # the only honest source of "what is missing on this machine"
    missing = set()
    for rows in (_newest_events(n=2000),):
        for r in rows:
            if r.get("kind") == "template_missing":
                missing.add(r.get("template"))
    return jsonify({"have": sorted(have), "missing_seen": sorted(missing - set(have))})


@app.get("/api/wizard/scan-plan")
def api_wizard_scan_plan():
    from player import scan_plan, readiness
    cfg = load_config()
    runs = _compiled_runs(cfg)
    selected = request.args.get("preset")
    body = runs.get(selected, {}) if selected else {}
    return jsonify(scan_plan.plan(ROOT, cfg, readiness.requirements(cfg, body) if body else ()))


_ARTWORK_INDEX: dict = {}      # asset-library folder -> (index mtime, images by lower-case name)


def _asset_library(cfg):
    """The newest extracted asset library for the active account, or None."""
    base = os.path.join(_calibration_dir(cfg), "asset_library")
    best = None
    for folder in glob.glob(os.path.join(base, "*")):
        index = os.path.join(folder, "index.json")
        if os.path.isfile(index) and (best is None or os.path.getmtime(index) > best[1]):
            best = (folder, os.path.getmtime(index))
    return best[0] if best else None


def _artwork_path(cfg, rel):
    """The installed game's own artwork for a recognition target, from the
    asset library Full setup extracts (never shipped): the manifest mapping
    first, else the names template_docs lists for it. What the person is
    expected to find on screen, next to the request to find it (2026-09-08)."""
    from player import template_docs
    folder = _asset_library(cfg)
    if not folder:
        return None
    try:
        with open(os.path.join(folder, "mapping.json"), encoding="utf-8") as fh:
            target = (json.load(fh).get("targets") or {}).get(rel) or {}
        for cand in target.get("candidates") or []:
            path = os.path.join(folder, cand.get("file") or "")
            if cand.get("file") and os.path.isfile(path):
                return os.path.normpath(path)
    except (OSError, ValueError):
        pass
    names = [n.lower() for n in template_docs.artwork_names(rel)]
    if not names:
        return None
    index = os.path.join(folder, "index.json")
    stamp = os.path.getmtime(index)
    cached = _ARTWORK_INDEX.get(folder)
    if not cached or cached[0] != stamp:
        by_name = {}
        try:
            with open(index, encoding="utf-8") as fh:
                for img in json.load(fh).get("images") or []:
                    by_name.setdefault(str(img.get("name") or "").lower(), []).append(img)
        except (OSError, ValueError):
            by_name = {}
        cached = (stamp, by_name)
        _ARTWORK_INDEX[folder] = cached
    for name in names:
        hits = [i for i in cached[1].get(name, []) if i.get("file")]
        # a Sprite is the on-screen cut; prefer it, then the largest
        hits.sort(key=lambda i: (i.get("type") != "Sprite", -(i.get("width") or 0) * (i.get("height") or 0)))
        for img in hits:
            path = os.path.join(folder, img["file"])
            if os.path.isfile(path):
                return os.path.normpath(path)
    return None


def _text_badge_png(text):
    """A stand-in picture for a control the game renders as TEXT (RETRY,
    EQUIP, a header word): the expected words on a button-shaped badge, so
    every target row shows what to look for even when no sprite exists."""
    import cv2
    import numpy as np
    text = str(text)
    canvas = np.full((88, 260, 3), (44, 34, 30), np.uint8)
    cv2.rectangle(canvas, (3, 3), (256, 84), (230, 200, 60), 2)
    scale = 1.1 if len(text) <= 6 else 0.8 if len(text) <= 11 else 0.55
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale, 2)
    cv2.putText(canvas, text, ((260 - tw) // 2, (88 + th) // 2), cv2.FONT_HERSHEY_DUPLEX, scale, (255, 245, 235), 2, cv2.LINE_AA)
    ok, png = cv2.imencode(".png", canvas)
    return png.tobytes() if ok else None


@app.get("/api/artwork/<path:rel>")
def api_artwork(rel):
    path = _artwork_path(load_config(), rel)
    if path:
        return send_file(path, mimetype="image/png", max_age=0)
    from player import template_docs
    text = template_docs.text_for(rel)
    png = _text_badge_png(text) if text else None
    if not png:
        # 204, not 404: the page asks for artwork for every target row and
        # removes the <img> on its error event, which an empty 204 body
        # fires too - without a console error per artless target
        return Response(status=204)
    return Response(png, mimetype="image/png", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/template/<path:rel>")
def api_template(rel):
    path = _template_path(rel)
    if not path:
        return Response("no", status=403)
    if not os.path.isfile(path):        # normal on a fresh account
        return Response("template not captured yet", status=404)
    return send_file(path, mimetype="image/png")


# --------------------------------------------------- calibration: cropper
#
# Templates used to be cut by hand in an image editor. The cropper below is
# the same act with the mouse on the Calibrate page: drag a box on the live
# frame, name it, save. It is a HUMAN-driven write - no detector ever calls
# it (CLAUDE.md #5: detectors never overwrite their own templates), and an
# existing file is never replaced unless the request says `overwrite`.
_FRAME_CACHE: "dict[str, tuple[dict, bytes]]" = {}      # token -> (the page it was fetched for, the PNG)
_FRAME_CACHE_KEEP = 8
_FRAME_CACHE_LOCK = threading.Lock()


def _frame_context(cfg=None) -> dict:
    """The page a frame belongs to: the emulator, account, profile and serial a crop cut from it would be saved under."""
    identity = harvest_api.identity_for(load_config() if cfg is None else cfg)
    return {key: identity.get(key) for key in ('instance', 'account', 'profile', 'serial')}


def _remember_frame(ts: str, raw: bytes, context: dict | None = None) -> None:
    context = _frame_context() if context is None else context
    with _FRAME_CACHE_LOCK:
        _FRAME_CACHE[str(ts)] = (context, raw)
        while len(_FRAME_CACHE) > _FRAME_CACHE_KEEP:
            _FRAME_CACHE.pop(next(iter(_FRAME_CACHE)))


def _template_path(rel: str, *, write=False, cfg=None) -> str | None:
    """Absolute path under templates/ for a relative name, or None when the
    name is not a plain `<folder>/<name>.png` inside it."""
    if not isinstance(rel, str) or not rel.lower().endswith(".png"):
        return None
    rel = rel.replace("\\", "/")
    if rel.startswith("/") or ".." in rel.split("/") or ":" in rel or "/" not in rel:
        return None                     # every template lives in a subfolder
    from player import accounts
    try:
        return str(accounts.template_path(ROOT, load_config() if cfg is None else cfg, rel, write=write))
    except ValueError:
        return None



def _preset_slug(name: str) -> str:
    # interactions.presets._slug, restated (the dashboard imports no runner
    # module): preset names as the user typed them -> template file stems
    return "".join(c if c.isalnum() else "_" for c in str(name).strip().lower())


def _required_templates(cfg: dict, profile: dict | None) -> list[dict]:
    """Every ACCOUNT-SPECIFIC template the configured loadouts and the
    scanned presets need, with have/missing. All UI chrome is also captured
    locally; the separate Scan plan covers those images: its card
    presets, its (renamed) global and category presets, its modules at its
    own rarity."""
    want: dict[str, dict] = {}

    def need(rel: str, feature: str, used_by: str) -> None:
        row = want.setdefault(rel, {"rel": rel, "feature": feature, "used_by": []})
        if used_by not in row["used_by"]:
            row["used_by"].append(used_by)

    cats = (("module_preset", "modules"), ("guardian_preset", "guardians"),
            ("workshop_preset", "workshop"), ("bot_preset", "bots"))
    for lname, lo in (cfg.get("loadouts") or {}).items():
        if not isinstance(lo, dict) or lo.get("defined") is False:
            continue
        src = f"loadout {lname}"
        for key in ("cards", "cards_restore"):
            if lo.get(key):
                need(f"cards/preset_{lo[key]}.png", "card preset tab", src)
        if lo.get("global_preset"):
            need(f"presets/gp_{_preset_slug(lo['global_preset'])}.png",
                 "global preset (picker row)", src)
        for key, cat in cats:
            if lo.get(key):
                need(f"presets/{cat}_{_preset_slug(lo[key])}.png",
                     f"{cat} preset (picker row)", src)
        for key in ("modules", "modules_restore"):
            for entry in lo.get(key) or []:
                slug = entry[0] if isinstance(entry, (list, tuple)) else entry
                if not slug:
                    continue
                need(f"modules/{slug}.png", "module icon (inventory grid)", src)
                need(f"modules/equipped/{slug}.png",
                     "module icon (equipped header)", src)
    player = (profile or {}).get("player") or {}
    for name in player.get("card_presets") or []:
        need(f"cards/preset_{name}.png", "card preset tab", "scanned account")
    for name in player.get("global_presets") or []:
        need(f"presets/gp_{_preset_slug(name)}.png",
             "global preset (picker row)", "scanned account")
    for cat, names in (player.get("category_presets") or {}).items():
        for name in names or []:
            need(f"presets/{cat}_{_preset_slug(name)}.png",
                 f"{cat} preset (picker row)", "scanned account")
    for slug in list(player.get("modules_equipped") or []):
        need(f"modules/{slug}.png", "module icon (inventory grid)", "scanned account")
        need(f"modules/equipped/{slug}.png", "module icon (equipped header)",
             "scanned account")
    if player.get("global_presets"):
        need("presets/picker_icon.png", "global preset picker button", "scanned account")
    equipped = set(player.get("modules_equipped") or [])
    out = []
    for rel in sorted(want):
        path = _template_path(rel, cfg=cfg)
        row = want[rel]
        row["have"] = bool(path and os.path.exists(path))
        stem = rel[len("modules/"):-4]
        if not row["have"] and rel.startswith("modules/") and "/" not in stem \
                and stem in equipped:
            # an equipped module is absent from the inventory grid, so its
            # grid tile cannot be cut until it is unequipped
            row["note"] = ("equipped right now - its inventory tile can only be "
                           "cut while it sits in the grid; re-run Calibrate then")
        out.append(row)
    return out


@app.get("/api/wizard/required")
def api_wizard_required():
    cfg = load_config()
    profile = None
    try:
        name = cfg.get("active_profile")
        path = _profile_path(name) if name else None
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                profile = yaml.safe_load(fh)
    except Exception:                           # noqa: BLE001 - listing only
        profile = None
    rows = _required_templates(cfg, profile)
    return jsonify({"required": rows,
                    "missing": [r["rel"] for r in rows if not r["have"]]})


@app.get("/api/catalogue/modules")
def api_catalogue_modules():
    """Module slugs the loadouts and the module templates are keyed by - the
    Calibrate page's naming help for a fresh account (game knowledge only,
    player/catalogue.py; nothing about any account)."""
    from player import catalogue
    learned = catalogue.local_modules()
    return jsonify({"modules": [{"slug": s, "name": n, "abbrevs": list(a),
                                 "learned": s in learned}
                                for s, (n, a) in catalogue.all_modules().items()]})


@app.post("/api/template/<path:rel>")
def api_template_save(rel):
    """Cut a template out of a frame the browser displayed.

    Body: {ts, x, y, w, h, overwrite?} - native 1080x2560 pixels; `ts` is the
    query value the frame was fetched with (/api/frame.png?ts=...), so the
    crop comes from that exact frame. Returns the crop size and how close the
    NEXT-best match on the same frame comes (a good template matches itself
    at 1.0 and nothing else near it)."""
    import numpy as np
    import cv2
    path = _template_path(rel, write=True)
    if not path:
        return jsonify({"ok": False, "error": "template name must be "
                        "<folder>/<name>.png inside templates/"}), 400
    body = request.get_json(force=True) or {}
    try:
        x, y = int(body["x"]), int(body["y"])
        w, h = int(body["w"]), int(body["h"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"ok": False, "error": "x, y, w, h are required"}), 400
    with _FRAME_CACHE_LOCK:
        cached = _FRAME_CACHE.get(str(body.get("ts")))
    if cached is None:
        return jsonify({"ok": False, "code": "frame_expired", "error": "that frame is no longer cached - "
                        "refresh the live screen and draw the box again"}), 409
    frame_context, raw = cached
    if frame_context != _frame_context():
        # the token outlives an account, emulator or profile switch: a crop of ANOTHER page's screen must never become this account's template
        return jsonify({"ok": False, "code": "frame_context_changed", "error": "that frame was captured for another account, "
                        "emulator or profile - refresh the live screen and draw the box again"}), 409
    frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return jsonify({"ok": False, "error": "cached frame did not decode"}), 500
    fh_, fw_ = frame.shape[:2]
    if w < 6 or h < 6 or x < 0 or y < 0 or x + w > fw_ or y + h > fh_:
        return jsonify({"ok": False, "error": f"box {x},{y} {w}x{h} is outside "
                        f"the {fw_}x{fh_} frame or smaller than 6px"}), 400
    if os.path.exists(path) and not body.get("overwrite"):
        return jsonify({"ok": False, "exists": True,
                        "error": f"{rel} exists - tick overwrite to replace it"}), 409
    crop = frame[y:y + h, x:x + w].copy()
    if float(crop.std()) < 2.0:
        # a flat-colour box has no detail to match - TM_CCOEFF_NORMED on it
        # is undefined and scores 1.0 everywhere
        return jsonify({"ok": False, "error": "the box is a flat colour - "
                        "include an edge or some text"}), 400
    # uniqueness on the source frame: best score with the crop's own area
    # masked out (the self-match is 1.0 by construction)
    res = cv2.matchTemplate(frame, crop, cv2.TM_CCOEFF_NORMED)
    y0, y1 = max(0, y - h + 1), min(res.shape[0], y + h)
    x0, x1 = max(0, x - w + 1), min(res.shape[1], x + w)
    res[y0:y1, x0:x1] = -1.0
    second = float(res.max()) if res.size else 0.0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not cv2.imwrite(path, crop):
        return jsonify({"ok": False, "error": "could not write the PNG"}), 500
    # where it came from: the screen (recognized by the manifest's anchors)
    # and the native rect go into the learned manifest, plus a report entry
    # and an event like every cut setup makes - so setup and observe can cut
    # this control themselves next time, on that screen, at this size
    screen = None
    try:
        from player import bootstrap, calibrate
        try:
            screen = bootstrap.recognize_screen(frame)
        except Exception as e:                  # noqa: BLE001 - OCR unavailable: still record the rect
            app.logger.warning("cropper screen recognition failed for %s: %s", rel, e)
        cdir = _calibration_dir()
        os.makedirs(cdir, exist_ok=True)
        calibrate.record_manual_cut({"state": os.path.join(cdir, "calibrate_state.json"),
                                     "report": os.path.join(cdir, "calibrate_report.json")},
                                    rel.replace("\\", "/"), crop, frame, [x, y, w, h], screen=screen, template=path)
    except Exception as e:                      # noqa: BLE001 - the file is written; provenance is best effort
        app.logger.warning("cropper provenance not recorded for %s: %s", rel, e)
    return jsonify({"ok": True, "rel": rel.replace("\\", "/"), "width": w,
                    "height": h, "second_best": round(second, 3), "screen": screen})


# ------------------------------------------- account scan -> profile
def _profile_write_worker():
    import psutil
    return {'pid': os.getpid(), 'creation_time': psutil.Process().create_time()}


def _promotion_response(source, promotion):
    return jsonify(dict(promotion['result_refs']['profile_promote'],
                        operation=operation_api.public(source) if source else None,
                        promotion=operation_api.public(promotion)))


def _persist_promotion(source, cfg, command, key, dest, text, result, promotion=None):
    """Reserve device ownership and retain an exact, retryable write receipt."""
    store = operation_api.ledger(ROOT)
    if promotion is None:
        context = source['context'] if source else operation_api.context(
            ROOT, cfg, {}, _profile_path(cfg.get('active_profile')))
        promotion, created = store.accept('profile_promote', context, command, key)
        if not created:
            raise operation_api.Conflict('Promotion changed; refresh before retrying')
    running = 'running' if promotion['status'] == 'accepted' else 'applying'
    promotion = store.transition(promotion['id'], promotion['revision'], running,
                                 worker=_profile_write_worker(), step='profile_write')
    try:
        from runtime.files import atomic_text
        # atomic_text uses text-mode newlines; the intent must name the bytes
        # it will persist on this OS, including CRLF on Windows.
        text_revision = hashlib.sha256(text.replace('\n', os.linesep).encode('utf-8')).hexdigest() if text is not None else None
        with _profile_lock(dest):
            previous = operation_api.file_revision(dest)
            intent = promotion['result_refs'].get('profile_promote_intent')
            if intent is None:
                if previous is not None and not command['overwrite']:
                    raise operation_api.Conflict('Destination profile already exists; review before replacing it')
                intent = {'previous_revision': previous, 'revision': text_revision,
                          'header': result.pop('_header'),
                          'response': dict(result)}
                intent['response']['profile_revision'] = intent['revision']
                promotion = store.transition(promotion['id'], promotion['revision'], running,
                    step='profile_write_intent', result_refs={'profile_promote_intent': intent})
            if previous != intent['revision']:
                if previous != intent['previous_revision']:
                    raise operation_api.Conflict('Destination changed after promotion; saved receipt cannot overwrite it')
                if text_revision != intent['revision']:
                    raise operation_api.Conflict('Promotion inputs changed; the original write remains unconfirmed')
                atomic_text(dest, text)
            if operation_api.file_revision(dest) != intent['revision']:
                raise ValueError('Promoted profile could not be verified by reading it back')
        promotion = store.transition(promotion['id'], promotion['revision'], 'succeeded',
            step='profile_written', error=None,
            result_refs=dict(promotion['result_refs'], profile_promote=intent['response']))
        return _promotion_response(source, promotion)
    except Exception as exc:
        store.transition(promotion['id'], promotion['revision'], 'failed',
                         error={'code': 'promotion_failed', 'message': str(exc)})
        if isinstance(exc, operation_api.Conflict):
            raise
        if not isinstance(exc, (OSError, ValueError)):
            app.logger.exception('Profile promotion failed')
        return jsonify(ok=False, error=str(exc)), 400 if isinstance(exc, (OSError, ValueError)) else 500


@app.post("/api/profile-promote")
@_fenced_by_page_context
@_serialize_config
def api_profile_promote():
    """Turn a scan draft (profiles/<inst>.draft.yaml, `player:` only) into a
    runnable profile: the draft's `player:` block on top of the starter's
    blueprints, policies and plan, written to profiles/<name>.yaml.

    The result is validated and the problems are RETURNED, not enforced: an
    account that owns fewer weapons than the starter assumes will fail a
    blueprint or two, and the fix is an edit to that profile, which needs
    the file to exist first. Nothing is activated here."""
    body = request.get_json(force=True) or {}
    operation = None
    if body.get('operation_id'):
        operation = operation_api.require_revision(ROOT, body, ('succeeded', 'failed', 'interrupted'))
        if operation['kind'] != 'scan':
            raise operation_api.Conflict('Operation is not an account scan')
    draft = str(body.get("draft") or "").strip()
    for suffix in (".draft.yaml", ".draft", ".yaml"):
        if draft.casefold().endswith(suffix):
            draft = draft[:-len(suffix)]
    name = str(body.get("name") or "").strip()
    base = str(body.get("base") or "default").strip()
    if not draft or not _PROFILE_NAME_RE.fullmatch(draft):
        return jsonify({"ok": False, "error": "draft: instance name expected"}), 400
    if not _PROFILE_NAME_RE.fullmatch(name) or name.casefold().endswith(".draft"):
        return jsonify({"ok": False, "error": "name: letters, digits, _ - . only"}), 400
    if _is_starter(name):
        return jsonify({"ok": False, "error": _STARTER_READ_ONLY, "starter": True}), 409
    command = dict(source_operation_id=operation['id'] if operation else None,
                   source_revision=operation['revision'] if operation else None,
                   draft=draft, name=name, base=base, overwrite=bool(body.get('overwrite')))
    key = 'profile-promote:' + operation['id'] if operation else None
    promotion = operation_api.ledger(ROOT).find_by_key(key) if key else None
    if promotion:
        if (promotion['kind'] != 'profile_promote' or promotion['request'] != command
                or promotion['context'] != operation['context']):
            raise operation_api.Conflict('This scan already belongs to a different promotion request')
        promotion = operation_api.resolve(ROOT, promotion['id'])
        if promotion['status'] == 'succeeded':
            return _promotion_response(operation, promotion)
        if promotion['status'] in ('accepted', 'running', 'cancelling', 'applying'):
            raise operation_api.Conflict('Promotion is still running; refresh its receipt before retrying')
    if operation:
        _require_idle('Wait for scanning to stop before applying results')
    cfg = load_config()
    if operation:
        current = operation_api.context(ROOT, cfg, {}, _profile_path(cfg.get('active_profile')))
        if any(current.get(field) != operation['context'].get(field)
               for field in ('account', 'instance', 'serial', 'rendering', 'profile', 'config_revision', 'device_key')):
            raise operation_api.Conflict('Return to the original unchanged account, device and profile before applying')
        intent = promotion['result_refs'].get('profile_promote_intent') if promotion else None
        own_write = intent and current['profile_path'] == _profile_path(name) and current['profile_revision'] == intent['revision']
        if current['profile_revision'] != operation['context']['profile_revision'] and not own_write:
            raise operation_api.Conflict('Profile changed after scanning; saved discoveries remain available')
    draft_path = os.path.join(_profiles_dir(), f"{draft}.draft.yaml")
    base_path = _profile_path(base)
    dest = _profile_path(name)
    if not operation and not os.path.exists(draft_path):
        return jsonify({"ok": False, "error": f"no draft for {draft!r} - run the "
                        "account scan first"}), 400
    if not base_path or not os.path.exists(base_path) or _is_draft(base):
        return jsonify({"ok": False, "error": f"base profile {base!r} not found"}), 400
    if not dest:
        return jsonify({"ok": False, "error": "bad profile name"}), 400
    intent = promotion['result_refs'].get('profile_promote_intent') if promotion else None
    if intent and operation_api.file_revision(dest) == intent['revision']:
        return _persist_promotion(operation, cfg, command, key, dest, None, None, promotion)
    if not operation:
        from player import accounts
        if draft == accounts.draft_name(cfg) and _requested_operation(cfg, ('scan',)):
            raise operation_api.Conflict('These observations belong to an operation; apply its ID and revision')
    if os.path.exists(dest) and not command['overwrite'] and not intent:
        return jsonify({"ok": False, "exists": True,
                        "error": f"profiles/{name}.yaml exists - tick overwrite"}), 409
    if operation:
        from player import accounts
        if draft != accounts.draft_name(operation['context']['config']):
            raise operation_api.Conflict('The draft belongs to another account scan')
        draft_doc, _ = _operation_artifact_json(operation, 'scan_state.json')
    else:
        with open(draft_path, encoding="utf-8") as fh:
            draft_doc = yaml.safe_load(fh) or {}
    with open(base_path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    player = draft_doc.get("player")
    if not isinstance(player, dict) or not player:
        return jsonify({"ok": False, "error": "the draft has no player: block"}), 400
    player = dict(player)
    # the scanner only asserts abilities it SAW (scan.py --battle); anything
    # else stays unverified, exactly as the schema demands
    if player.get("abilities_verified") is not True:
        player["abilities_verified"] = False
    # Nothing is assumed: no wall until the player says so, and max_tier is
    # the tier the home screen showed at scan time (unlocked by definition),
    # else the starter's floor.
    player.setdefault("wall", False)
    tier_seen = player.get("tier_current")
    player.setdefault("max_tier", tier_seen if isinstance(tier_seen, int) and tier_seen > 0
                      else (doc.get("player") or {}).get("max_tier", 1))
    doc["player"] = player
    doc.pop("_name", None)
    doc.pop("_path", None)
    try:
        from player import playerprofile
        check = dict(doc)
        check["_name"] = name
        problems = _profile_check(check)
    except Exception as e:                      # noqa: BLE001
        problems = [f"validator unavailable: {type(e).__name__}: {e}"]
    header = intent['header'] if intent else (f"# Promoted from profiles/{draft}.draft.yaml over profiles/{base}.yaml"
              f" on {datetime.datetime.now():%Y-%m-%d %H:%M}.\n"
              "# Machine-specific and git-ignored.\n")
    text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True,
                          default_flow_style=None)
    yaml.safe_load(text)
    result = dict(ok=True, applied=True, name=name, path=dest, problems=problems,
                  abilities_verified=player['abilities_verified'], _header=header)
    return _persist_promotion(operation, cfg, command, key, dest, header + text, result, promotion)


def _start_note(kind, **fields):
    """One start/stop line in the dashboard's own event log; a log that cannot be written never blocks the decision."""
    try:
        _diagnostic_event(kind, **fields)
    except Exception as exc:    # noqa: BLE001 - the refusal/exit code below is the decision, the log is the record
        print(f"{kind}: {fields} (event log unavailable: {exc})", file=sys.stderr)


def _report_collector_at_start():
    """Once per start: say whether a collector from before this dashboard is alive and whether this one keeps it
    (control/harvest.collector_at_start owns the judgement). One dashboard_collector_found event, and the same record in
    the harvest status host. Nothing found writes nothing; a check that fails is logged and never blocks the start."""
    try:
        report = harvest_api.collector_at_start(
            ROOT, load_config(), processes=operation_api.project_processes(ROOT, _procs(), reconcile=False),
            resolve_source=_harvest_source)
    except Exception as exc:    # noqa: BLE001 - the start is not conditional on this report
        _start_note('dashboard_collector_unchecked', pid=os.getpid(), error=f'{type(exc).__name__}: {exc}')
        return
    if report is not None:
        _HARVEST_HOST['collector_at_start'] = report
        # the event log stamps its own pid on every line, so the collector's is named apart
        _start_note('dashboard_collector_found', collector_pid=report['pid'], **{k: v for k, v in report.items() if k != 'pid'})


def serve(host=None, port=None) -> int:
    """Run the dashboard until it is stopped; returns the process exit code.

    Order matters: the landing hold, then the exclusive port, and only THEN the background threads - a dashboard
    that loses either must not reconcile or relaunch anything before it exits (runtime/dashboard_start.py)."""
    from control.diagnostics import log_root
    from runtime import dashboard_start
    host, port = host or HOST, port or PORT
    notice = dashboard_start.hold_notice(log_root(ROOT))
    if notice is not None:
        _start_note('dashboard_start_refused', reason='landing_hold', pid=os.getpid(), note=notice,
                    hold=str(dashboard_start.hold_path(log_root(ROOT))))
        return 3
    try:
        server = dashboard_start.bind(host, port, app)
    except dashboard_start.PortTaken as exc:
        _start_note('dashboard_start_refused', reason='port_taken', pid=os.getpid(), port=port, error=str(exc))
        return 1
    # threaded: the MJPEG stream holds a connection open for as long as the
    # tab is; single-threaded Flask would block every other request behind it
    app.debug = False       # what app.run(debug=False) set, whatever FLASK_DEBUG says
    _report_collector_at_start()    # before the refresher: its first tick retires a dead record this would report
    _start_procs_refresher()
    _start_note('dashboard_started', pid=os.getpid(), port=port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    _start_note('dashboard_stopped', pid=os.getpid(), port=port)
    return 0


if __name__ == "__main__":
    sys.exit(serve())
