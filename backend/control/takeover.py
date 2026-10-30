"""Explicit tracked takeover, server side: the request, the admission order, the preview and the restricted envelope. No web framework, no device, no screen.

`dashboard.py` keeps the HTTP shell (route, error mapping, the transfer from a standalone collector, the launch) and hands this module the few things only it knows
(compiled runs, owners, the setup-battle guard, readiness) as `Ports`. Everything a takeover decides is here; the policy itself (what a preset may do when attached,
the request and envelope shapes) is `player/attach_policy.py`. Rules: takeover.rules.md.
"""
from dataclasses import dataclass
from typing import Callable

from control import battles as battle_api
from control import harvest as harvest_api
from control import operations
from player import attach_policy, readiness
from runtime.attach_worker import WAVE_MARGIN   # the one margin of admission and of the worker's entry gate
from player.run_controls import effective_preset

ACTION = 'takeover'
SCHEMA_VERSION = 1
CONTEXT_KEYS = ('instance', 'account', 'serial', 'rendering', 'profile')
BODY_KEYS = ('action', 'preset', 'idempotency_key', 'expected_context', 'attach')
# Fields only the server may write: the lifecycle host's harvest fields (as for `start`) and the envelope, which is derived here and never taken from a client.
RESERVED_KEYS = ('harvest', 'desire_revision', 'battle_envelope')


class InvalidTakeover(ValueError):
    """A request that is not the takeover shape (400 `invalid_request`). `reason` is the stable word (the AttachRequestInvalid codes, or reserved_field,
    missing_field, unknown_field, wrong_type, unknown_preset) and `field` the offending value."""
    code = 'invalid_request'

    def __init__(self, reason, field=None, detail=None):
        self.reason, self.field = reason, field
        super().__init__(' '.join(part for part in (reason, field and f'[{field}]', detail) if part))


class TakeoverRefused(operations.Conflict):
    """A takeover refused before the collector's input stopped and before anything was recorded (409). `code` is the word a client branches on and `extra`
    the fields that ride with it in the body."""

    def __init__(self, code, message, **extra):
        super().__init__(message)
        self.code, self.extra = code, extra


@dataclass(frozen=True)
class Restriction:
    """The server-derived facts that go into `battle_envelope.attach`: never part of the request, so an identical retry still matches its receipt."""
    policy_revision: str
    tier: dict
    predecessor: dict | None


@dataclass(frozen=True)
class Parsed:
    preset: str
    attach: dict
    expected_context: dict


@dataclass(frozen=True)
class Admitted:
    preset: str
    owners: list | None
    restrict: Restriction


@dataclass(frozen=True)
class Ports:
    """What only the dashboard can supply. Each callable takes the configuration (`cfg`) the request is judged in."""
    root: str
    compiled_runs: Callable        # cfg -> {run key: compiled preset}; ValueError when the profile cannot be compiled
    runnable: Callable             # cfg -> [run key] the Start button may start
    setup_guard: Callable          # cfg -> the run-scope setup-battle guard
    readiness: Callable            # (cfg, preset) -> readiness.check(..., attached=True)
    owners: Callable               # cfg -> None | [standalone collectors]; raises the shared owner refusal for any other owner
    owners_view: Callable          # cfg -> [owner views], read-only (a GET never retires an operation)


# ------------------------------------------------------------------ the request
def _object(value, field):
    if not isinstance(value, dict):
        raise InvalidTakeover('wrong_type', field, 'expected an object')
    return value


def parse(body) -> Parsed:
    """The takeover request, or InvalidTakeover. The exact keys of spec 3.1; the body itself is stored unchanged by the launch, which binds the key to it."""
    body = _object(body, 'body')
    for key in RESERVED_KEYS:
        if key in body:
            raise InvalidTakeover('reserved_field', key, 'written by the server only')
    for key in body:
        if key not in BODY_KEYS:
            raise InvalidTakeover('unknown_field', key)
    for key in ('preset', 'idempotency_key', 'expected_context', 'attach'):
        if key not in body:
            raise InvalidTakeover('missing_field', key)
    for key in ('preset', 'idempotency_key'):
        if not isinstance(body[key], str) or not body[key]:
            raise InvalidTakeover('wrong_type', key, 'expected a non-empty string')
    expected = _object(body['expected_context'], 'expected_context')
    if set(expected) != set(CONTEXT_KEYS) or any(value is not None and not isinstance(value, str) for value in expected.values()):
        raise InvalidTakeover('bad_value', 'expected_context', 'name exactly ' + ', '.join(CONTEXT_KEYS) + ', each text or null')
    try:
        attach = attach_policy.validate_request(body['attach'])
    except attach_policy.AttachRequestInvalid as exc:
        raise InvalidTakeover(exc.code, exc.field, exc.detail) from exc
    return Parsed(body['preset'], attach, dict(expected))


# ------------------------------------------------------------------ the runs
def _compiled(ports, cfg):
    try:
        cfg['presets'] = ports.compiled_runs(cfg)
    except ValueError as exc:
        raise TakeoverRefused('runs_unavailable', str(exc)) from exc
    return cfg['presets']


def run_verdict(cfg, key):
    """`(verdict, reasons, effective preset)` of one compiled run: `compatible`, `incompatible` (with the reasons of attach_policy.compatibility) or `unreadable`
    (the preset cannot be resolved, so nothing is claimed about it)."""
    try:
        preset = effective_preset(cfg, key)
        reasons = attach_policy.compatibility(preset, key)
    except (KeyError, TypeError, attach_policy.AttachPolicyError) as exc:
        return 'unreadable', [{'code': 'preset_unreadable', 'field': key, 'detail': f'{type(exc).__name__}: {exc}'}], None
    return ('incompatible' if reasons else 'compatible'), reasons, preset


def restricted_preset(key, preset):
    """The preset a takeover of `key` runs with (attach_policy.restrict_envelope over the preset alone), for readiness and the preview."""
    return attach_policy.restrict_envelope(preset, {}, {'presets': {key: preset}}, key, {'value': None, 'provenance': 'unknown'})[0]


# ------------------------------------------------------------------ the battle on the collector's record
def collector_record(root, cfg, owners):
    """The active ledger record of the standalone collector that holds the emulator, or None (no collector, no ledger, or a collector not in a battle yet).
    Read from the ledger the collector writes; the server reads no screen, so this is the only source of a wave before the worker's own entry gate."""
    if not owners:
        return None
    held = {owner['operation_id'] for owner in owners if owner.get('operation_id')}
    for battle in battle_api.active_read_only(root, operations.device_identity(cfg)['device_key']):
        if battle['operation_id'] in held:
            return battle
    return None


def current_battle(record):
    """The battle the server can name, as the preview shows it and `attach_battle_changed` returns it. `wave_source` says where the wave comes from."""
    if record is None:
        return {'source': 'none', 'battle_id': None, 'generation': None, 'observed_wave': None, 'operation_id': None, 'status': None,
                'wave_source': 'none'}
    wave = (record.get('runtime') or {}).get('observed_wave')
    return {'source': 'collector_record', 'battle_id': record['id'], 'generation': record['generation'],
            'observed_wave': wave, 'operation_id': record['operation_id'], 'status': record['status'],
            'wave_source': 'collector_record' if wave is not None else 'none'}


def predecessor_of(record):
    return None if record is None else {'battle_id': record['id'], 'generation': record['generation'], 'operation_id': record['operation_id']}


def fenced(record, expected):
    """True when the battle the person confirmed is still the one the server names (spec 3.3 step 8). The ids must match exactly; with no collector record all
    three are null. The record's wave must stay within WAVE_MARGIN of the confirmed one: more below is another battle, and more above is a page too old to
    vouch for the battle (the worker's entry gate measures against the confirmed wave only, so a wider drift would leave it a window for a replacement)."""
    shown = current_battle(record)
    if (expected['battle_id'], expected['generation']) != (shown['battle_id'], shown['generation']):
        return False
    seen, now = expected['observed_wave'], shown['observed_wave']
    if seen is None or now is None:
        return seen is None and now is None
    return abs(now - seen) <= WAVE_MARGIN


# ------------------------------------------------------------------ admission (spec 3.3, steps 1-8; the launch is steps 9-10)
def admit(ports: Ports, cfg, request: Parsed) -> Admitted:
    """Everything that must hold before the collector's input stops, in the order of spec 3.3. Sets `cfg['presets']` to the compiled runs, as `start` does.
    Raises InvalidTakeover, TakeoverRefused, the setup-battle refusal or the shared owner refusal; nothing is recorded and no input has stopped."""
    target = operations.context(ports.root, cfg, {'preset': request.preset}, None)
    if any(request.expected_context[key] != target.get(key) for key in CONTEXT_KEYS):          # 2: the page's context
        raise TakeoverRefused('context_changed', 'The emulator, account or profile changed since this page was read; review the new context before taking over',
                              current_context={key: target.get(key) for key in CONTEXT_KEYS})
    runs = _compiled(ports, cfg)
    if request.preset not in runs or request.preset not in ports.runnable(cfg):
        raise InvalidTakeover('unknown_preset', 'preset')
    verdict, reasons, preset = run_verdict(cfg, request.preset)                                  # 3: a run that can be attached
    if verdict != 'compatible':
        raise TakeoverRefused('takeover_not_compatible', 'This run cannot be taken over: ' + '; '.join(
            f"{reason['code']} ({reason['field']})" for reason in reasons), reasons=reasons)
    revision = attach_policy.policy_revision(request.preset, preset)
    if revision != request.attach['policy_revision']:                                           # 4
        raise TakeoverRefused('policy_changed', 'The run changed since the preview; read it again before taking over', current_policy_revision=revision)
    battle_api.refuse_if_blocked(ports.setup_guard(cfg))                                         # 5: run scope, no handoff reuse
    ready = ports.readiness(cfg, restricted_preset(request.preset, preset))                      # 6
    if not ready['ready']:
        raise TakeoverRefused(*readiness_refusal(ready), missing=ready['missing'], advisory=ready.get('advisory', []))
    owners = ports.owners(cfg)                                                                   # 7
    record = collector_record(ports.root, cfg, owners)
    if not fenced(record, request.attach['expected_battle']):                                    # 8
        raise TakeoverRefused('attach_battle_changed', 'The battle on the collector changed since you confirmed it; read it again before taking over',
                              current_battle=current_battle(record))
    return Admitted(request.preset, owners, Restriction(revision, request.attach['tier'], predecessor_of(record)))


def readiness_refusal(ready):
    """`(code, message)` for a readiness result that is not ready. The trophy badge gets its own code: the person captures it through Recognition."""
    badge = [row for row in ready['missing'] if readiness.IN_TOURNAMENT in row['alternatives']]
    if badge:
        return 'tournament_recognition_missing', 'Takeover refuses to guess whether this is a tournament: capture the trophy badge in Recognition first'
    return 'calibration_needed', 'Calibration is needed for this run. Open Calibrate to see the missing steps.'


# ------------------------------------------------------------------ the envelope (spec 3.3 step 9)
def restrict_parts(preset, controls, frozen, selected, restrict: Restriction):
    """`(restricted preset, controls, frozen, envelope['attach'])` for `_launch_operation`: attach_policy's mutually consistent triple plus the server-derived
    block. `preset` is the effective preset BEFORE restriction (the one the revision was computed from)."""
    restricted, restricted_controls, restricted_frozen = attach_policy.restrict_envelope(preset, controls, frozen, selected, restrict.tier)
    block = attach_policy.build_envelope_attach(restrict.policy_revision, restrict.tier, attach_policy.plan(preset)['stripped'], restrict.predecessor)
    return restricted, restricted_controls, restricted_frozen, block


# ------------------------------------------------------------------ the preview (read-only)
def owner_state(root, owners):
    """`(state, owner views)`: free, collector, or the first blocking owner as owner_run, owner_scheduler (the day plan's `combo` run) or owner_other."""
    if not owners:
        return 'free', []
    if harvest_api.only_collectors(owners) and all(owner['kind'] == 'operation' for owner in owners):
        return 'collector', owners
    for owner in owners:
        if owner.get('operation_kind') == 'run':
            try:
                scheduler = operations.stored(root, owner['operation_id'])['context'].get('selected_run') == 'combo'
            except KeyError:
                scheduler = False
            return ('owner_scheduler' if scheduler else 'owner_run'), owners
    return 'owner_other', owners


def _owner_row(owner):
    return {key: owner.get(key) for key in ('kind', 'operation_id', 'operation_kind', 'status', 'harvest_managed', 'foreign')}


def preview(ports: Ports, cfg, key=None) -> dict:
    """The takeover preview. Reads only: no operation is retired, nothing is recorded, no input is sent.

    With a run key: its verdict, the plan (what is applied, stripped, needs a tier), the policy revision to send back, attached readiness, the setup-battle
    guard, the emulator's owners, the collector's battle (with where its wave comes from) and `blockers`, the reasons a takeover would be refused now, in the
    order the Dashboard shows them. Without one: every runnable run with its verdict. Raises TakeoverRefused('runs_unavailable') and InvalidTakeover('unknown_preset')."""
    runs = _compiled(ports, cfg)
    context = operations.context(ports.root, cfg, {'preset': key}, None)
    shown = {name: context.get(name) for name in CONTEXT_KEYS}
    if key is None:
        listed = []
        for name in ports.runnable(cfg):
            if name in runs:
                verdict, reasons, preset = run_verdict(cfg, name)
                listed.append({'preset': name, 'label': (preset or {}).get('label'), 'verdict': verdict, 'reasons': reasons})
        return {'ok': True, 'schema_version': SCHEMA_VERSION, 'context': shown, 'preset': None, 'runs': listed}
    if key not in runs or key not in ports.runnable(cfg):
        raise InvalidTakeover('unknown_preset', 'preset')
    verdict, reasons, preset = run_verdict(cfg, key)
    owners = ports.owners_view(cfg)
    state, held = owner_state(ports.root, owners)
    guard = ports.setup_guard(cfg)
    record = collector_record(ports.root, cfg, owners if state == 'collector' else None)
    result = {'ok': True, 'schema_version': SCHEMA_VERSION, 'context': shown, 'preset': key,
              'run': {'preset': key, 'label': (preset or {}).get('label'), 'kind': (preset or {}).get('kind'), 'verdict': verdict, 'reasons': reasons,
                      'policy_revision': attach_policy.policy_revision(key, preset) if preset is not None else None},
              'plan': attach_policy.plan(preset) if verdict == 'compatible' else None, 'readiness': None,
              'setup_guard': {name: guard.get(name) for name in ('blocks_launch', 'code', 'message')},
              'device': {'state': state, 'owners': [_owner_row(owner) for owner in held]},
              'current_battle': current_battle(record)}
    blockers = [] if verdict == 'compatible' else ['not_compatible']
    if guard['blocks_launch']:
        blockers.append('guard_blocks')
    if verdict == 'compatible':
        ready = ports.readiness(cfg, restricted_preset(key, preset))
        result['readiness'] = {'ready': ready['ready'], 'missing': ready['missing'], 'advisory': ready.get('advisory', [])}
        if not ready['ready']:
            blockers.append(readiness_refusal(ready)[0])
    if state not in ('free', 'collector'):
        blockers.append(state)
    result['blockers'] = blockers
    return result
