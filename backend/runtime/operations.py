"""Runtime adapters for the pure workflow ledger."""
import json
import hashlib
import os
from pathlib import Path
from contextlib import contextmanager

from towerpilot_workflows.operations import (
    Operations, Conflict, digest, file_revision, ACTIVE, STATUSES,
)
from runtime import build_info

_BUILD = build_info.snapshot(Path(__file__).resolve().parents[2], 'worker')


def environment_operation():
    operation_id = os.environ.get('TOWER_OPERATION_ID')
    path = os.environ.get('TOWER_OPERATION_DB')
    battle_id, generation = os.environ.get('TOWER_BATTLE_ID'), os.environ.get('TOWER_BATTLE_GENERATION')
    if (bool(operation_id) != bool(path) or bool(battle_id) != bool(generation)
            or ((battle_id or generation) and not operation_id)):
        raise Conflict('Incomplete accepted driver identity')
    return (Operations(path), operation_id) if operation_id and path else (None, None)


def validate_worker_context(operation, name=None, config=None):
    """Refuse a retired operation or redirected source before installing context."""
    if operation['status'] not in ('accepted', 'running'):
        raise Conflict('Driver operation is not active')
    context = operation['context']
    if name is not None and name != context['instance']:
        raise Conflict('Accepted run belongs to another emulator')
    if config is not None:
        device = config.get('instances', {}).get(context['instance'], {})
        if (config.get('active_instance') != context['instance']
                or any(device.get(key) != context.get(key) for key in ('serial', 'account'))):
            raise Conflict('Accepted source belongs to another device or account')


def restore_worker_context(settings, name=None):
    """Pin configuration before device binding, including original profile."""
    ledger, operation_id = environment_operation()
    if ledger:
        op = ledger.get(operation_id)
        validate_worker_context(op, name)
        from runtime.battle_source import accepted_source
        accepted = accepted_source(op['context']['instance'])
        config = accepted[0] if accepted is not None else op['context']['config']
        from device import adbclient
        adbclient._HOST = (op['context'].get('adb_host', '127.0.0.1'),
                           op['context'].get('adb_port', 5037))
        settings.CONFIG.clear()
        settings.CONFIG.update(config)
        import copy
        settings._DEFAULTS = {k: copy.deepcopy(settings.CONFIG[k]) for k in settings._OVERRIDABLE if k in settings.CONFIG}
        settings._ACCOUNT_DEFAULTS = {k: copy.deepcopy(settings.CONFIG.get(k)) for k in ('loadouts', 'active_profile', 'tourney_card_tweaks')}


def publish_progress(state):
    ledger, operation_id = environment_operation()
    if not ledger:
        return
    state['operation_id'] = operation_id
    op = ledger.get(operation_id)
    if op['status'] == 'running':
        phases = state.get('phases', {})
        step = next((key for key, value in phases.items() if value.get('status') == 'running'), 'capture')
        try:
            op = ledger.transition(operation_id, op['revision'], 'running', step=step)
        except Conflict:
            op = ledger.get(operation_id)
    state['operation_revision'] = op['revision']


def _retain_artifact(directory, operation_id, filename):
    """Freeze completed evidence before publishing its reference.

    The working file belongs to the next scan as soon as it starts. A result
    reference must instead keep the exact bytes this operation inspected.
    Exclusive creation also prevents a repeated finalizer replacing evidence.
    """
    source = directory / filename
    try:
        raw = source.read_bytes()
    except FileNotFoundError:
        return None, None
    destination = directory / 'operations' / operation_id / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb') as stream:
        stream.write(raw)
    revision = hashlib.sha256(raw).hexdigest()
    if file_revision(destination) != revision:
        raise OSError(f'Operation artifact readback failed: {filename}')
    return {'path': str(destination), 'revision': revision}, raw


@contextmanager
def worker_operation(kind=None):
    """Retain one durable worker outcome, including failures without exceptions."""
    from runtime import logger
    ledger, operation_id = environment_operation()
    op = ledger.get(operation_id) if ledger else None
    identity = op['context'] if op else {}
    fields = {}
    if op:
        fields.update(operation_id=operation_id, instance=identity.get('instance'),
                      operation_kind=op['kind'])
    with logger.context(**fields), logger.attempt('operation.worker') as attempt:
        try:
            with _worker_operation(kind):
                yield
        except SystemExit as exc:
            if exc.code in (None, 0):
                attempt.finish('exited', exit_code=exc.code)
            raise
        finally:
            if ledger:
                final = ledger.get(operation_id)
                logger.event('operation_worker_finished', outcome=final['status'],
                             error=final.get('error'), revision=final['revision'])
                if attempt.outcome is None:
                    attempt.finish(final['status'])


@contextmanager
def _worker_operation(kind=None):
    """Entrypoint wrapper. Lease ownership remains the worker's responsibility."""
    ledger, operation_id = environment_operation()
    if ledger is None:
        yield
        return
    import psutil
    op = ledger.get(operation_id)
    worker = {'pid': os.getpid(), 'creation_time': psutil.Process().create_time(), 'build': _BUILD}
    if op['status'] == 'cancelling':
        ledger.transition(operation_id, op['revision'], 'cancelled', worker=worker)
        raise SystemExit('Operation cancelled before worker start')
    if op['status'] != 'accepted':
        raise Conflict('Worker launch is no longer accepted')
    running = ledger.transition(operation_id, op['revision'], 'running', worker=worker, step='capture')
    from runtime import logger
    logger.event('operation_worker_started', operation_kind=op['kind'],
                 revision=running['revision'], worker=worker)
    failure = None
    try:
        yield
    except SystemExit as exc:
        if exc.code not in (None, 0):
            failure = {'code': 'SystemExit', 'message': str(exc)[:1000]}
        raise
    except BaseException as exc:
        failure = {'code': type(exc).__name__, 'message': str(exc)[:1000]}
        raise
    finally:
        op = ledger.get(operation_id)
        directory = Path(op['context']['artifact_dir'])
        restore = (directory / 'module_restore.json').exists()
        refs = {}
        state = {}
        name = 'scan' if op['kind'] == 'scan' else 'calibrate'
        filenames = (f'{name}_state.json', f'{name}_report.json') if op['kind'] in ('calibrate', 'observe', 'scan') else ('screen_analysis.json', 'screen_analysis.png') if op['kind'] == 'screen_analysis' else ()
        restore = restore and op['kind'] in ('calibrate', 'observe', 'scan')
        for filename in filenames:
            try:
                reference, raw = _retain_artifact(directory, operation_id, filename)
                if reference is None:
                    continue
                refs[filename] = reference
                if filename.endswith('_state.json') or filename == 'screen_analysis.json':
                    state = json.loads(raw.decode('utf-8'))
                    if not isinstance(state, dict):
                        state = {}
                        raise ValueError('Operation state must be a JSON object')
            except (OSError, ValueError) as exc:
                failure = failure or {'code': 'invalid_artifact', 'message': str(exc)}
        phase_names = {'c': 'cards', 'g': 'global', 'm': 'modules', 'u': 'guardians', 'b': 'bots', 'w': 'workshop', 'e': 'equipped_modules'}
        if op['kind'] == 'scan':
            phase_names = {'c': 'cards', 'g': 'guardians', 'm': 'modules', 'b': 'battle'}
        requested = op['request'].get('phases', 'g,c,m' if op['kind'] == 'scan' else 'c,g,m,u,b,w')
        if isinstance(requested, str):
            requested = [key.strip() for key in requested.split(',') if key.strip()]
        selected_names = {phase_names.get(key, key) for key in requested}
        if op['kind'] == 'scan' and op['request'].get('battle'):
            selected_names.add('battle')
        if op['request'].get('bootstrap'):
            selected_names = {'bootstrap'}
        phases = state.get('phases', {})
        if op['kind'] in ('calibrate', 'scan') and any(
                phases.get(key, {}).get('status') != 'done' for key in selected_names):
            failure = failure or {'code': 'capture_incomplete', 'message': 'Not every requested phase has a confirmed completed result'}
        phase_errors = any(x.get('status') in ('error', 'stopped', 'running', 'needs_attention')
                           or any(step.get('status') in ('error', 'stopped', 'needs_attention', 'running') for step in x.get('steps', []))
                           for key, x in phases.items() if key in selected_names)
        if filenames and state.get('operation_id') != operation_id:
            failure = failure or {'code': 'missing_capture_outcome', 'message': 'Worker did not publish observations for this operation'}
        if op['kind'] == 'calibrate':
            final_screen = state.get('final_screen') or {}
            if (final_screen.get('operation_id') != operation_id
                    or final_screen.get('name') != 'home'
                    or final_screen.get('verified') is not True):
                failure = failure or {'code': 'final_screen_unverified',
                                      'message': 'Setup did not verify and persist its final Home screen'}
        if op['kind'] == 'screen_analysis' and state.get('status') != 'done':
            failure = failure or {'code': 'analysis_incomplete', 'message': state.get('message', 'Screen analysis did not complete')}
        if op['kind'] == 'screen_analysis':
            screenshot = state.get('screenshot')
            retained = refs.get('screen_analysis.png')
            if (not isinstance(screenshot, dict) or retained is None
                    or screenshot.get('operation_id') != operation_id
                    or screenshot.get('path') != str(directory / 'screen_analysis.png')
                    or screenshot.get('revision') != retained['revision']):
                failure = failure or {
                    'code': 'analysis_image_unverified',
                    'message': 'Screen analysis requires the PNG saved and verified by this operation',
                }
        for attempt in range(3):
            # Cancellation can arrive while artifacts are read, or between this
            # read and the CAS. Recompute precedence from the current operation.
            op = ledger.get(operation_id)
            if op['status'] not in ('running', 'cancelling'):
                break  # Another owner already recorded a terminal outcome.
            if restore:
                status = 'restoration_required'
            elif op['status'] == 'cancelling':
                status = 'cancelled'
            elif failure or phase_errors:
                status = 'failed'
            elif op['kind'] in ('calibrate', 'observe'):
                status = 'awaiting_apply'
            else:
                status = 'succeeded'
            try:
                ledger.transition(operation_id, op['revision'], status, result_refs=refs,
                                  error=failure or ({'code': 'capture_incomplete', 'message': 'Some capture phases did not complete'} if phase_errors else None),
                                  step='restore' if restore else 'capture_complete',
                                  recovery_action='Restore original module equipment before continuing' if restore else None)
                break
            except Conflict:
                if attempt == 2 or ledger.get(operation_id)['revision'] == op['revision']:
                    raise
