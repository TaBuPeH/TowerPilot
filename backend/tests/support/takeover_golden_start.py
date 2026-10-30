"""The dashboard `start` golden answers (T3-0): POST /api/control action "start" through the real Flask route of backend/dashboard.py.

Same scaffolding as test_harvest_routes.py (harvest_http.make_app: the real app over a temporary root and ledger, one fake clock, a
controllable process table; process creation, psutil and adb are the only fakes). On top of that every step the route takes is journaled
in order: the owner check, readiness, the guard and context checks of _transfer_from_collector, the hold (begin_transfer), the collector's
stop and release wait, the launch and its Popen, and end_transfer. The recorded documents are
backend/tests/fixtures/takeover_golden/dashboard_start.json."""
import json

from control import operations
from harvest_http import _battle_for, collector_row, enable, identity, make_app, ops, running_collector
from player import readiness
from player.run_controls import effective_preset, resolve


class Journal:
    def __init__(self, app):
        self.app, self.lines, self.names = app, [], {}

    def note(self, line):
        self.lines.append(line)

    def label(self, operation_id):
        if operation_id not in self.names:
            self.names[operation_id] = f'op-{len(self.names) + 1}'
        return self.names[operation_id]

    def spy(self, owner, name, *, label=None, show=None):
        original, journal = getattr(owner, name), self
        label = label or name

        def wrapper(*args, **kwargs):
            journal.note(f'call {label}')
            try:
                result = original(*args, **kwargs)
            except BaseException as exc:                 # noqa: BLE001 - journaled, then re-raised unchanged
                journal.note(f'raise {label} {type(exc).__name__}')
                raise
            summary = show(result) if show else None
            if summary is not None:
                journal.note(f'return {label} {summary}')
            return result
        self.app.monkeypatch.setattr(owner, name, wrapper)


def instrument(app, journal):
    """Spy on every step the start route takes. The readiness check keeps answering ready (the `launches` fixture's answer)."""
    dash, spy = app.dash, journal.spy
    answer = readiness.check
    app.monkeypatch.setattr(readiness, 'check', lambda root, cfg, preset: journal.note('call readiness.check') or answer(root, cfg, preset))
    spy(dash, '_collector_takeover', show=lambda found: 'none' if found is None else f'{len(found)} standalone collector(s)')
    spy(dash, '_transfer_from_collector', show=lambda done: f'stopped={len(done["stopped"])} stopping={len(done["stopping"])}')
    spy(dash, '_launch_operation', show=lambda op: f'{op["kind"]} {op["status"]}')
    spy(dash.operation_api, 'retry', show=lambda found: 'hit' if found else 'none')
    spy(dash.operation_api, 'verify_expected')
    spy(dash.battle_api, 'refuse_if_blocked')
    spy(dash.harvest_api, 'begin_transfer')
    spy(dash.harvest_api, 'stop_collectors', show=lambda stop: f'stopped={len(stop["stopped"])} stopping={len(stop["stopping"])}')
    spy(dash.harvest_api, 'wait_for_release', show=lambda done: f'released={done[0]}')
    spy(dash.harvest_api, 'end_transfer')
    real_popen = dash.subprocess.Popen

    def popen(cmd, **kwargs):
        journal.note('call Popen ' + ' '.join(str(part).replace(dash.ROOT, '<ROOT>').replace('\\', '/') for part in cmd[1:]))
        return real_popen(cmd, **kwargs)
    app.monkeypatch.setattr(dash.subprocess, 'Popen', popen)


def settle(value, journal):
    """Operation ids become op-1, op-2 ... in order of first appearance, so a golden does not hold a random id."""
    if isinstance(value, dict):
        return {key: settle(item, journal) for key, item in value.items()}
    if isinstance(value, list):
        return [settle(item, journal) for item in value]
    return journal.label(value) if isinstance(value, str) and value in journal.names else value


def reply(response, journal, *, text=False):
    payload = response.get_json()
    view = dict(status=response.status_code, keys=sorted(payload))
    for name in ('ok', 'code', 'retry'):
        if name in payload:
            view[name] = payload[name]
    if text and 'error' in payload:
        view['error'] = payload['error']
    if 'operation_id' in payload:
        view['operation_id'] = journal.label(payload['operation_id'])
    if 'transfer' in payload:
        view['transfer'] = dict(stopped=len(payload['transfer']['stopped']), stopping=len(payload['transfer']['stopping']))
    if isinstance(payload.get('operation'), dict):
        view['operation'] = dict(kind=payload['operation']['kind'], status=payload['operation']['status'],
                                 request=sorted(payload['operation']['request']))
    if isinstance(payload.get('owner'), dict):
        view['owner'] = dict(kind=payload['owner'].get('kind'), runner=payload['owner'].get('runner'),
                             foreign=payload['owner'].get('foreign'))
    return view


def envelope_view(app, op):
    """The shape of the battle_envelope _launch_operation stored with the accepted run, and what it must agree with."""
    stored = operations.ledger(app.dash.ROOT).get(op['id'])
    envelope = stored['request']['battle_envelope']
    selected = stored['request']['preset']
    effective = effective_preset(envelope['config'], selected)
    return dict(
        keys=sorted(envelope), preset_key=envelope['preset_key'], selected=selected, config_preset=envelope['config']['preset'],
        preset_is_effective_preset=envelope['preset'] == effective, controls_are_resolved=envelope['controls'] == resolve(envelope['preset'], envelope['config']),
        preset_keys=sorted(envelope['preset']), control_routines={name: dict(item) for name, item in sorted(envelope['controls']['routines'].items())},
        control_keys=sorted(envelope['controls']), source=envelope['source'], source_revision_present=envelope['source_revision'] is not None,
        profile_kind=type(envelope['profile']).__name__, frozen_config_has_snapshot_keys=sorted(k for k in envelope['config'] if k.startswith('_')),
        stored_request_keys=sorted(stored['request']), stored_request={k: v for k, v in stored['request'].items() if k != 'battle_envelope'},
        stored_kind=stored['kind'], context_identity={k: stored['context'][k] for k in ('instance', 'account', 'serial', 'rendering', 'profile', 'selected_run')})


def post_start(app, key, **extra):
    """POST /api/control action start for bp_farm with a fixed idempotency key (the harness's own counter would make the key depend on test order)."""
    return app.client.post('/api/control', json=dict(action='start', preset='bp_farm', idempotency_key=key, **extra))


def new_app(dash, launches, monkeypatch):
    app = make_app(dash, launches, monkeypatch)
    journal = Journal(app)
    instrument(app, journal)
    return app, journal


def with_collector(app, journal):
    """A person's harvest choice is on and its collector is running over a live battle. Returns the collector's operation id (journal label
    op-1), the battle store and the battle; the setup's own calls are dropped from the journal."""
    enable(app)
    collector = running_collector(app)
    store, battle = _battle_for(app, collector)
    app.rows.append(collector_row(collector))
    journal.label(collector['id'])
    journal.lines.clear()
    return collector['id'], store, battle


def harvest_state(app):
    shown = app.dash._harvest_status(app.dash.load_config(), [])
    return dict(choice=shown['desired']['choice'], revision=shown['desired']['revision'], transfer=shown['transfer'], state=shown['state'])


def every_operation(app, journal):
    """All operations of the ledger, oldest first: kind and status (ids as op-N)."""
    with operations.ledger(app.dash.ROOT)._db() as db:
        rows = [json.loads(row[0]) for row in db.execute('SELECT body FROM operations ORDER BY rowid').fetchall()]
    return [dict(op=journal.label(row['id']), kind=row['kind'], status=row['status']) for row in rows]


# ---------------------------------------------------------------- scenarios
def start_with_a_collector(dash, launches, monkeypatch):
    """Home Start while the diamond collector owns the emulator: the guard and context checks run first, then the hold, the collector is
    stopped and released, the run is launched and the hold ends; the harvest choice and the game battle survive."""
    app, journal = new_app(dash, launches, monkeypatch)
    collector_id, store, battle = with_collector(app, journal)
    response = post_start(app, 'golden-start-with-collector-1')
    payload = response.get_json()
    [run] = [op for op in ops(app) if op['kind'] == 'run']
    return dict(reply=reply(response, journal), journal=journal.lines, envelope=envelope_view(app, run),
                collector_status=operations.ledger(app.dash.ROOT).get(collector_id)['status'], battle_status=store.get(battle['id'])['status'],
                terminated=len(app.psutil.terminated), harvest=harvest_state(app), spawned=len(app.spawned),
                active_kinds=sorted(op['kind'] for op in ops(app)), transfer=settle({key: value for key, value in payload['transfer'].items() if key != 'waited_sec'}, journal),
                transfer_keys=sorted(payload['transfer']))


def start_without_a_collector(dash, launches, monkeypatch):
    """Nothing owns the emulator: no hold, no stop, one launch."""
    app, journal = new_app(dash, launches, monkeypatch)
    response = post_start(app, 'golden-start-alone-1')
    [run] = ops(app)
    return dict(reply=reply(response, journal), journal=journal.lines, envelope=envelope_view(app, run), spawned=len(app.spawned),
                harvest=harvest_state(app))


def identical_retry(dash, launches, monkeypatch):
    """The same key and body again: the original operation comes back, nothing is launched; after the run ended it still does."""
    app, journal = new_app(dash, launches, monkeypatch)
    body = dict(action='start', preset='bp_farm', idempotency_key='golden-retry-1')
    first = app.client.post('/api/control', json=body)
    spawned = len(app.spawned)
    journal.lines.append('--- identical retry')
    second = app.client.post('/api/control', json=body)
    store = operations.ledger(app.dash.ROOT)
    [run] = store.active()
    store.transition(run['id'], run['revision'], 'interrupted')
    journal.lines.append('--- identical retry after the run ended')
    third = app.client.post('/api/control', json=body)
    return dict(first=reply(first, journal), second=reply(second, journal), third=reply(third, journal), journal=journal.lines,
                spawned_by_retries=len(app.spawned) - spawned, same_operation=len({first.get_json()['operation_id'], second.get_json()['operation_id'],
                                                                                   third.get_json()['operation_id']}) == 1)


def changed_payload_same_key(dash, launches, monkeypatch):
    """The key of an accepted start with a different body (an expected context added) is refused 409; nothing is launched."""
    app, journal = new_app(dash, launches, monkeypatch)
    key = 'golden-changed-1'
    first = app.client.post('/api/control', json=dict(action='start', preset='bp_farm', idempotency_key=key))
    spawned = len(app.spawned)
    ident = identity(app)
    journal.lines.append('--- same key, changed body')
    changed = app.client.post('/api/control', json=dict(action='start', preset='bp_farm', idempotency_key=key,
                                                        expected_context={name: ident[name] for name in ('instance', 'account', 'serial', 'profile')}))
    journal.lines.append('--- same key, as another action')
    other = app.client.post('/api/control', json=dict(action='start_gems', preset='bp_farm', idempotency_key=key))
    return dict(first=reply(first, journal), changed=reply(changed, journal, text=True), as_gems=reply(other, journal, text=True),
                journal=journal.lines, spawned_after=len(app.spawned) - spawned)


def automatic_harvest_fields_refused(dash, launches, monkeypatch):
    """`harvest` and `desire_revision` are reserved for the lifecycle host: a start carrying either is a 400 before anything else."""
    app, journal = new_app(dash, launches, monkeypatch)
    results = {}
    for field, value in (('harvest', True), ('desire_revision', 3)):
        results[field] = reply(app.client.post('/api/control', json={'action': 'start', 'preset': 'bp_farm', 'idempotency_key': f'golden-{field}-1',
                                                                      field: value}), journal, text=True)
    return dict(results=results, journal=journal.lines, spawned=len(app.spawned))


def unknown_preset_and_action(dash, launches, monkeypatch):
    app, journal = new_app(dash, launches, monkeypatch)
    unknown_preset = app.client.post('/api/control', json=dict(action='start', preset='bp_missing', idempotency_key='golden-unknown-1'))
    unknown_action = app.client.post('/api/control', json=dict(action='start_everything', preset='bp_farm', idempotency_key='golden-unknown-2'))
    return dict(unknown_preset=reply(unknown_preset, journal, text=True), unknown_action=reply(unknown_action, journal, text=True),
                journal=journal.lines, spawned=len(app.spawned))


def readiness_failure_leaves_the_collector(dash, launches, monkeypatch):
    """A run whose images are missing is refused after the owner check and before the hold: no begin_transfer, the collector untouched."""
    app, journal = new_app(dash, launches, monkeypatch)
    collector_id, _, _ = with_collector(app, journal)
    monkeypatch.setattr(readiness, 'check', lambda root, cfg, preset: journal.note('call readiness.check') or {
        'ready': False, 'missing': [{'runs': ['farm'], 'alternatives': ['x.png'], 'reasons': ['needed']}]})
    response = post_start(app, 'golden-not-ready-1')
    return dict(reply=reply(response, journal, text=True), journal=journal.lines,
                collector_status=operations.ledger(app.dash.ROOT).get(collector_id)['status'], terminated=len(app.psutil.terminated), harvest=harvest_state(app))


def stale_context_leaves_the_collector(dash, launches, monkeypatch):
    """The page's expected context no longer holds: the guard and the context check run inside _transfer_from_collector, BEFORE the hold, so
    a refusal leaves the collector's input running."""
    app, journal = new_app(dash, launches, monkeypatch)
    collector_id, _, _ = with_collector(app, journal)
    stale = dict(instance='main', account='someone', serial='fake:1', rendering=None, profile='mine')
    response = post_start(app, 'golden-stale-1', expected_context=stale)
    return dict(reply=reply(response, journal, text=True), journal=journal.lines,
                collector_status=operations.ledger(app.dash.ROOT).get(collector_id)['status'], terminated=len(app.psutil.terminated), harvest=harvest_state(app))


def run_scope_guard_blocks_before_stop(dash, launches, monkeypatch):
    """Unreadable setup evidence blocks a run at the run-scope guard: refused 409 setup_evidence_unreadable before the hold."""
    from pathlib import Path
    from test_dashboard_admission import calibrate, home
    app, journal = new_app(dash, launches, monkeypatch)
    sealed = calibrate(app.dash, home)
    collector_id, _, _ = with_collector(app, journal)
    path = Path(sealed['result_refs']['calibrate_state.json']['path'])
    path.write_bytes(path.read_bytes() + b' ')
    response = post_start(app, 'golden-guard-1')      # its message names a calibration operation id: the code is the answer
    return dict(reply=reply(response, journal), journal=journal.lines,
                collector_status=operations.ledger(app.dash.ROOT).get(collector_id)['status'], terminated=len(app.psutil.terminated), harvest=harvest_state(app))


def collector_still_stopping(dash, launches, monkeypatch):
    """The collector does not release in time: 409 collector_stopping with the shared owner body. The hold stays (it expires by itself), nothing
    is launched, and end_transfer is NOT called (it is only in the launch's finally)."""
    app, journal = new_app(dash, launches, monkeypatch)
    collector_id, _, _ = with_collector(app, journal)
    app.psutil.lingering = True
    response = post_start(app, 'golden-lingering-1')
    return dict(reply=reply(response, journal, text=True), journal=journal.lines, spawned=len(app.spawned), harvest=harvest_state(app),
                active_kinds=sorted(op['kind'] for op in ops(app)))


def foreign_process_blocks(dash, launches, monkeypatch):
    app, journal = new_app(dash, launches, monkeypatch)
    enable(app)
    app.rows.append({'pid': 4242, 'runner': 'orchestrator', 'cmdline': 'python orchestrator.py'})
    journal.lines.clear()
    response = post_start(app, 'golden-foreign-1')
    return dict(reply=reply(response, journal, text=True), journal=journal.lines, terminated=len(app.psutil.terminated), spawned=len(app.spawned))


def launch_failure_ends_the_transfer(dash, launches, monkeypatch):
    """The process cannot be created after the collector was stopped: the hold is released in the finally and the error propagates."""
    app, journal = new_app(dash, launches, monkeypatch)
    collector_id, _, _ = with_collector(app, journal)

    def broken(cmd, **kwargs):
        journal.note('call Popen (fails)')
        raise OSError('synthetic spawn failure')
    monkeypatch.setattr(dash.subprocess, 'Popen', broken)
    app.dash.app.config['PROPAGATE_EXCEPTIONS'] = False
    response = post_start(app, 'golden-spawnfail-1')
    return dict(status=response.status_code, journal=journal.lines, harvest=harvest_state(app),
                collector_status=operations.ledger(app.dash.ROOT).get(collector_id)['status'],
                operations=every_operation(app, journal))


START = {fn.__name__: fn for fn in (
    start_with_a_collector, start_without_a_collector, identical_retry, changed_payload_same_key, automatic_harvest_fields_refused,
    unknown_preset_and_action, readiness_failure_leaves_the_collector, stale_context_leaves_the_collector,
    run_scope_guard_blocks_before_stop, collector_still_stopping, foreign_process_blocks, launch_failure_ends_the_transfer)}
