"""A new action needs a catalog entry, a binding and a saved adapter, and nothing else (roadmap item 6, batch 2: the synthetic proof).

`synthetic_probe` is registered, twice (two configured instances), only in a COPY of the catalog that a test swaps in with
`use_catalog`. It is named in no other file of the checkout: no route, no endpoint and no dashboard branch changes. Everything
below goes through the real Flask routes of backend/dashboard.py over a temporary root and ledger, with the real `BattleSession`
as the driver (the test is the driver: no frame, no input, no device):

  * the handler actually runs - Run now makes the binding start, and its own `progress()` (not a collector's or the shopper's
    state) drives the started and the terminal acknowledgement; a completion nobody can confirm is published `effect: unconfirmed`;
  * the descriptor and an item for each instance reach GET /api/battles/active;
  * a live-only field changes through live-controls, a value outside the published bounds is refused 400 by the generated
    validator, a null inherits, a variant token is accepted and an unknown one refused;
  * the same Run now key replays its receipt after the driver ends and a changed payload is a 409;
  * a saved edit through /api/run-controls reaches the registered adapter and leaves a sibling run's shared policy unchanged;
  * its executions are in GET /api/battles/<id> and GET /api/actions/executions;
  * the route table is the same before and after, and the name appears in no production file.

What an extension still adds beyond the contract: a row in the profile schema when it saves NUMBERS (`playerprofile.GATHER_KEYS`
here: the profile schema is closed on purpose). Its `enabled` flag needs none: blueprint `automation` is validated against the
catalog.
"""

import paths as test_paths
import ast
import copy
import hashlib
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from control import operations
from harvest_support import fake_inspect
from runtime import battle_session as battle_session_module
from runtime.action_bindings import BindingBase
from runtime.action_session import ActionSession
from runtime.battle_session import BattleSession
from player import playerprofile, saved_controls
from towerpilot_workflows.action_contract import (CATALOG, ActionDefinition, BindingResult, Capabilities, FakeClock, Field,
                                                  StartToStart, use_catalog)
from towerpilot_workflows.actions import Actions

REPO = test_paths.REPO
CONTEXT = dict(device_key='device', account='account', instance='main', serial='fake', profile='mine')
KEYS = ('synthetic_probe@probe_a', 'synthetic_probe@probe_b')
DEFINITION = ActionDefinition(
    id='synthetic_probe', label='Synthetic probe', family='gems', hosts=frozenset({'test'}), schedule=StartToStart(),
    fields=(Field('enabled', 'bool', label='Enabled'),
            Field('interval_sec', 'number', 10, 600, 's', 'Interval'),
            Field('burst', 'number', 1, 20, 'steps', 'Burst', saved=False)),          # live only: no saved setting
    capabilities=Capabilities(live_controls=True, pause=True, run_now=True, variants=True), saved='synthetic_probe_saved',
    evidence=('probe_ran',))


@pytest.fixture
def catalog():
    copy_ = CATALOG.copy()
    copy_.register(DEFINITION)
    return copy_


# ------------------------------------------------------------------ the test's handler, binding and saved adapter
class Probe:
    """The extension's handler: when it runs it does `burst` steps of work in a five-second window, records the window through the
    contract's recorder and decides when it is due with the contract's own start-to-start policy."""
    VARIANTS = ('fast', 'slow')

    def __init__(self, session, instance, clock, *, interval):
        self.instance, self.clock, self.policy = instance, clock, StartToStart()
        self.recorder = session.recorder('synthetic_probe', instance)
        self.state = dict(next_window_at=0.0, window_open=False)
        self.enabled, self.interval, self.burst = True, interval, 3
        self.requested, self.command_id, self.variant = False, None, None
        self.window, self.opened, self.last = None, 0, None
        self.configured = []

    def configure(self, values):
        self.configured.append(dict(values))
        for name, value in values.items():
            setattr(self, 'interval' if name == 'interval_sec' else name, value if value is not None else self._default(name))

    @staticmethod
    def _default(name):
        return dict(enabled=True, interval_sec=60, burst=3)[name]

    def request(self, variant, command_id):
        if self.requested or self.window is not None:
            return False
        self.requested, self.command_id, self.variant = True, command_id, variant
        return True

    def poll(self):
        now = self.clock.mono()
        if self.window is None:
            decision, state = self.policy.evaluate(now, self.state, dict(manual=self.requested, interval=self.interval, skip_duration=5))
            self.state = state
            if decision.kind == 'due' and (self.enabled or self.requested):
                manual = self.requested
                self.opened += 1
                window, self.state = StartToStart.open_window(now, self.state, self.interval, 5)
                self.window = dict(window, manual=manual, number=self.opened)
                self.recorder.opened(trigger='manual' if manual else 'automatic', command_id=self.command_id if manual else None,
                                     occurrence_key=f'window:{self.instance}:{self.opened}', refs=dict(probe_id=f'{self.instance}-{self.opened}'))
                self.requested, self.command_id = False, None
        elif now >= self.window['deadline']:
            self.state = StartToStart.close_window(self.state)
            self.last = dict(window=self.window['number'], steps=self.burst, confirmed=False, variant=self.variant)
            self.recorder.closed(result='succeeded', reason='ran_unconfirmed', effect='unconfirmed', attempts=self.burst, evidence=['probe_ran'],
                                 refs=dict(probe_id=f'{self.instance}-{self.window["number"]}'))
            self.window = None

    def status(self):
        epoch = self.clock.wall() + self.state['next_window_at'] - self.clock.mono() if self.state['next_window_at'] else self.clock.wall()
        return dict(enabled=self.enabled, state='active' if self.window is not None else 'scheduled' if self.enabled else 'off',
                    next_due_at=epoch, window_ends_at=None if self.window is None else self.clock.wall() + self.window['deadline'] - self.clock.mono(),
                    manual_pending=self.requested, manual_active=self.window is not None and self.window['manual'],
                    last_outcome=self.last, counters=dict(windows=self.opened, burst=self.burst))


class ProbeBinding(BindingBase):
    """What the extension registers for each configured instance. Run now acknowledges only what THIS binding reports."""

    def __init__(self, probe):
        self.probe = probe

    def configure(self, values):
        self.probe.configure(values)
        return True

    def runnable(self):
        return True

    def variants(self):
        return Probe.VARIANTS

    def descriptor_variants(self):
        return Probe.VARIANTS

    def request_now(self, variant=None, *, command_id=None):
        return self.probe.request(variant, command_id)

    def progress(self):
        probe = self.probe
        if probe.requested:
            return BindingResult('pending')
        if probe.window is not None:
            return BindingResult('started', refs=dict(probe_id=f'{probe.instance}-{probe.window["number"]}'))
        last = probe.last
        return BindingResult('terminal', outcome='succeeded', effect='unconfirmed', reason='ran_unconfirmed', attempts=last['steps'],
                             evidence=('probe_ran',), refs=dict(probe_id=f'{probe.instance}-{last["window"]}'))

    def status(self):
        return self.probe.status()

    def routine(self, *, battle_visible, now, epoch):
        return self.probe.status()


class ProbeSaved(saved_controls.SavedAdapter):
    """Where the extension keeps its saved settings: its own entry in the run's gather policy, copied before it changes."""

    def keys(self, definition, bp):
        return KEYS

    def read(self, definition, preset, config, automation):
        stored = (preset.get('gather') or {}).get('synthetic_probe', {})
        out = {}
        for key in KEYS:
            values = dict(enabled=True, interval_sec=stored.get(key.partition('@')[2], {}).get('interval_sec', 60), burst=3)
            if key in automation:
                values['enabled'] = automation[key]
            out[key] = values
        return out

    def prepare(self, ctx, definition, by_key, docs):
        if ctx.gather is None:
            ctx.gather = saved_controls.owned_policy(ctx.profile, ctx.run, 'gather', 'gather')

    def write(self, ctx, definition, by_key, docs):
        for key, values in by_key.items():
            if 'interval_sec' in values:
                saved_controls.assign(ctx.gather.setdefault('synthetic_probe', {}).setdefault(key.partition('@')[2], {}), 'interval_sec',
                                      values['interval_sec'])


# --------------------------------------------------------------------------------------- the real routes over a real driver
@pytest.fixture
def host(dash, catalog, monkeypatch):
    """The dashboard over a temporary root, the extension registered in a catalog copy, and a driver (a real BattleSession with a
    real ActionSession) that holds the gem collectors AND the extension's two bindings."""
    from flows.shard import GemWatch
    from control import battles
    clock = FakeClock(mono=100.0, wall=5000.0)
    monkeypatch.setattr(battle_session_module, 'time', SimpleNamespace(monotonic=clock.mono, time=clock.wall))
    monkeypatch.setattr(playerprofile, 'GATHER_KEYS', playerprofile.GATHER_KEYS + ('synthetic_probe',))   # the extension's schema row
    monkeypatch.setitem(saved_controls.SAVED, 'synthetic_probe_saved', ProbeSaved())
    root = Path(dash.ROOT)
    profile = yaml.safe_load((root / 'profiles' / 'mine.yaml').read_text(encoding='utf-8'))
    profile['blueprints']['sibling'] = copy.deepcopy(profile['blueprints']['farm'])        # shares the gather policy with `farm`
    (root / 'profiles' / 'mine.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    cfg = dash.load_config()
    selected = operations.context(dash.ROOT, cfg, {'preset': 'bp_farm'}, None)
    ledger = operations.ledger(dash.ROOT)
    operation, _ = ledger.accept('run', selected, {}, 'probe-run')
    operation = ledger.transition(operation['id'], operation['revision'], 'running', worker={'pid': os.getpid(), 'creation_time': 1.0})
    preset = {'tier': 1, 'label': 'Farm', 'shopping': [], 'shop_interval_sec': 90,
              'gather': {'ad_gems': True, 'synthetic_probe': {'probe_a': {'interval_sec': 60}, 'probe_b': {'interval_sec': 90}}}}
    with use_catalog(catalog):
        driver = BattleSession(battles.ledger(dash.ROOT), operation['id'], preset, cfg)
        actions = ActionSession.for_battle(driver, 'test')
        actions.clock = clock
        probes = {key: Probe(actions, key.partition('@')[2], clock, interval=interval) for key, interval in zip(KEYS, (60, 90))}
        rs = SimpleNamespace(gems=GemWatch(preset['gather']), tracker=SimpleNamespace(last=12), mission=None, shop=None,
                             action_bindings={key: ProbeBinding(probe) for key, probe in probes.items()})
        driver.observe(rs, 12)
        yield SimpleNamespace(dash=dash, driver=driver, rs=rs, probes=probes, clock=clock, operation=operation, selected=selected,
                              client=dash.app.test_client(), ledger=ledger, root=root)


def battle(h):
    return h.driver.store.get(h.driver.battle['id'])


def command_body(h, **fields):
    current = battle(h)
    return dict(battle_id=current['id'], generation=h.driver.generation, expected_revision=current['control_revision'], **fields)


def live(h, changes, key, *, expect=200):
    reply = h.client.post(f"/api/operations/{h.operation['id']}/live-controls", json=command_body(h, changes=changes, idempotency_key=key))
    assert reply.status_code == expect, reply.get_json()
    return reply.get_json()


def run_now(h, action, key, *, expect=200):
    reply = h.client.post(f"/api/operations/{h.operation['id']}/actions", json=command_body(h, action=action, idempotency_key=key))
    assert reply.status_code == expect, reply.get_json()
    return reply.get_json()


def tick(h, seconds=1.0, *, visible=True, polls=True):
    h.clock.advance(seconds)
    if polls:
        for probe in h.probes.values():
            probe.poll()
    h.driver.checkpoint(h.rs, battle_visible=visible)


def commands(h):
    return {c['request_keys'][0]: c for c in h.driver.store.commands(h.driver.battle['id'])}


def active(h):
    return h.client.get('/api/battles/active').get_json()['battle']


def history(h):
    return Actions.read(h.driver.actions.path, device=h.selected['device_key'])[0]


def pause(h, *keys, key='pause'):
    live(h, {f'routines.{item}.enabled': False for item in keys}, key)
    tick(h, polls=False)

# ------------------------------------------------------------------------------- the extension, through the real routes
def test_the_descriptor_and_an_item_of_each_instance_reach_the_dashboard(host):
    tick(host)
    shown = active(host)
    items = {item['instance']: item for item in shown['runtime']['actions']['items'] if item['id'] == 'synthetic_probe'}
    assert set(items) == {'probe_a', 'probe_b'}
    for item in items.values():
        assert [field['path'] for field in item['descriptor']['fields']] == ['enabled', 'interval_sec', 'burst']
        assert [(field['live'], field['saved']) for field in item['descriptor']['fields']] == [(True, True), (True, True), (True, False)]
        assert item['descriptor']['capabilities']['run_now'] is True
        assert [variant['token'] for variant in item['descriptor']['variants']] == ['fast', 'slow']
        assert item['next']['basis'] == 'start_to_start' and item['label'] == 'Synthetic probe'
    assert set(shown['runtime']['routines']) >= {'ad_gems', 'floating_gems', *KEYS}
    assert set(shown['capabilities']['controls']) >= {f'routines.{key}.{field}' for key in KEYS for field in ('enabled', 'interval_sec', 'burst')}
    assert set(shown['capabilities']['actions']) >= {*KEYS, *(f'{key}:{variant}' for key in KEYS for variant in Probe.VARIANTS)}


def test_the_contract_gives_the_action_a_lifecycle_a_start_to_start_timing_and_a_recorded_history(host):
    tick(host)                                            # start-to-start: the first window of each instance is due at once
    a, b = host.probes[KEYS[0]], host.probes[KEYS[1]]
    assert a.window is not None and b.window is not None
    running = {row['instance']: row for row in history(host) if row['action'] == 'synthetic_probe'}
    assert {name: row['state'] for name, row in running.items()} == {'probe_a': 'running', 'probe_b': 'running'}
    for _ in range(6):
        tick(host)
    finished = {row['instance']: row for row in history(host) if row['action'] == 'synthetic_probe'}
    assert {name: (row['state'], row['outcome']['effect'], row['outcome']['attempts']) for name, row in finished.items()} == {
        'probe_a': ('succeeded', 'unconfirmed', 3), 'probe_b': ('succeeded', 'unconfirmed', 3)}
    assert finished['probe_a']['trigger'] == 'automatic' and finished['probe_a']['host']['battle_id'] == host.driver.battle['id']
    assert finished['probe_a']['duration_ms'] is not None and finished['probe_a']['refs'] == dict(probe_id='probe_a-1')
    due = {item['instance']: item['next']['due_at'] for item in active(host)['runtime']['actions']['items'] if item['id'] == 'synthetic_probe'}
    assert a.interval == 60 and b.interval == 90
    assert a.state['next_window_at'] == 101.0 + 60 and b.state['next_window_at'] == 101.0 + 90   # one interval after the window STARTED, however long it ran
    for probe, instance in ((a, 'probe_a'), (b, 'probe_b')):
        assert due[instance] == pytest.approx(host.clock.wall() + probe.state['next_window_at'] - host.clock.mono())   # the Dashboard shows the policy's number
    assert due['probe_b'] > due['probe_a']
    for _ in range(60):
        tick(host)
    assert a.opened == 2 and b.opened == 1               # probe_a's interval has passed, probe_b's has not
    host.driver.finish('observed_results')               # the driver ends with a window open: its own finalization closes it
    rows = [row for row in history(host) if row['action'] == 'synthetic_probe']
    assert sorted(row['state'] for row in rows).count('succeeded') >= 3 and all(row['state'] != 'running' for row in rows)


def test_a_live_only_field_changes_through_live_controls_and_the_binding_installs_it(host):
    tick(host)
    before = battle(host)['control_revision']
    body = live(host, {'routines.synthetic_probe@probe_a.burst': 7}, 'burst-1')
    assert body['command']['state'] == 'requested' and body['control_revision'] == before + 1
    tick(host)
    assert host.probes[KEYS[0]].burst == 7 and host.probes[KEYS[1]].burst == 3 and host.probes[KEYS[0]].configured == [dict(burst=7)]
    assert commands(host)['burst-1']['state'] == 'applied' and commands(host)['burst-1']['applied_paths'] == ['routines.synthetic_probe@probe_a.burst']
    shown = active(host)
    assert shown['applied_revision'] == before + 1 and shown['effective']['routines'][KEYS[0]]['burst'] == 7
    assert shown['effective']['routines'][KEYS[1]]['burst'] == 3


@pytest.mark.parametrize('path,value', [('burst', 99), ('burst', 0), ('interval_sec', 9), ('interval_sec', 601), ('enabled', 1), ('burst', True), ('burst', '5')])
def test_a_value_outside_the_published_bounds_is_refused_by_the_generated_validator_and_changes_nothing(host, path, value):
    tick(host)
    before = battle(host)['control_revision']
    body = live(host, {f'routines.{KEYS[0]}.{path}': value}, f'bad-{path}', expect=400)
    assert f'Invalid value: routines.{KEYS[0]}.{path}' in body['error']
    assert battle(host)['control_revision'] == before and not host.probes[KEYS[0]].configured


def test_an_unknown_field_an_unknown_instance_and_a_malformed_instance_are_refused(host):
    tick(host)
    assert 'Unknown control' in live(host, {f'routines.{KEYS[0]}.nothing': 1}, 'unknown-field', expect=400)['error']
    assert 'Unsupported controls' in live(host, {'routines.synthetic_probe@probe_z.burst': 4}, 'unknown-instance', expect=400)['error']
    assert live(host, {'routines.synthetic_probe@Bad.burst': 4}, 'bad-instance', expect=400)['error']
    assert live(host, {'routines.synthetic_probe.burst': 4}, 'bare-action', expect=400)['error']      # the action id alone names no configured instance
    assert battle(host)['control_revision'] == 0


def test_a_null_inherits_the_frozen_value(host):
    tick(host)
    live(host, {f'routines.{KEYS[0]}.burst': 9}, 'set')
    tick(host)
    assert host.probes[KEYS[0]].burst == 9
    live(host, {f'routines.{KEYS[0]}.burst': None}, 'inherit')
    tick(host)
    assert host.probes[KEYS[0]].burst == 3
    assert 'burst' not in battle(host)['overrides'].get('routines', {}).get(KEYS[0], {})
    assert commands(host)['inherit']['state'] == 'applied'


def test_pausing_an_instance_through_the_generic_enabled_control_stops_its_automatic_windows(host):
    live(host, {f'routines.{KEYS[0]}.enabled': False}, 'pause-a')
    tick(host, polls=False)
    assert host.probes[KEYS[0]].enabled is False and host.probes[KEYS[1]].enabled is True
    for _ in range(80):
        tick(host)
    assert host.probes[KEYS[0]].opened == 0 and host.probes[KEYS[1]].opened >= 1


def test_a_variant_token_is_accepted_and_an_unknown_one_refused_with_its_reason(host):
    tick(host)
    accepted = run_now(host, f'{KEYS[0]}:fast', 'variant-ok')
    assert accepted['command']['payload'] == dict(action=f'{KEYS[0]}:fast') and accepted['command']['state'] == 'requested'
    refused = run_now(host, f'{KEYS[0]}:nope', 'variant-bad', expect=400)
    assert refused['error'] == 'Unsupported manual action'
    assert run_now(host, 'synthetic_probe', 'bare', expect=400)['error'] == 'Unsupported manual action'
    assert run_now(host, 'synthetic_probe@probe_z', 'nobody', expect=400)['error'] == 'Unsupported manual action'


def test_run_now_starts_the_binding_and_its_own_progress_drives_both_acknowledgements(host):
    pause(host, *KEYS)
    assert not any(probe.opened for probe in host.probes.values())
    ad, orbit = host.rs.gems.ad_gems, host.rs.gems.orbit
    run_now(host, f'{KEYS[0]}:fast', 'go-1')
    assert commands(host)['go-1']['state'] == 'requested'
    tick(host, polls=False)                                                         # the checkpoint asks the binding; the handler has not run yet
    assert host.probes[KEYS[0]].requested is True and commands(host)['go-1']['state'] == 'requested'
    tick(host)                                                                      # the handler polls: due by request, its window opens
    started = commands(host)['go-1']
    assert started['state'] == 'started' and started['evidence'] is None and host.probes[KEYS[0]].window['manual'] is True
    for _ in range(6):
        tick(host)
    done = commands(host)['go-1']
    assert done['state'] == 'completed'
    assert done['evidence'] == dict(outcome='succeeded', effect='unconfirmed', reason='ran_unconfirmed', attempts=3, evidence=['probe_ran'],
                                    refs=dict(probe_id='probe_a-1'))
    assert (ad.manual_requested, orbit.manual_requested, ad.manual_command_id, orbit.manual_command_id) == (False, False, None, None)
    row = next(r for r in history(host) if r['action'] == 'synthetic_probe')
    assert (row['trigger'], row['command_id'], row['state'], row['outcome']['effect']) == ('manual', done['id'], 'succeeded', 'unconfirmed')
    assert host.driver.manual is None


def test_a_second_run_now_waits_for_the_first_and_a_request_the_handler_refuses_stays_requested(host):
    pause(host, KEYS[0])
    run_now(host, KEYS[0], 'go-1')
    tick(host, polls=False)
    other = run_now(host, f'{KEYS[0]}:slow', 'go-2', expect=409)
    assert 'Another manual action' in other['error']


def test_the_same_run_now_key_replays_its_receipt_after_the_driver_ends_and_a_changed_payload_is_a_409(host):
    pause(host, KEYS[0])
    body = command_body(host, action=KEYS[0], idempotency_key='replay-1')
    url = f"/api/operations/{host.operation['id']}/actions"
    first = host.client.post(url, json=body).get_json()
    tick(host, polls=False)
    tick(host)
    for _ in range(6):
        tick(host)
    host.driver.finish('observed_results')
    host.ledger.transition(host.operation['id'], host.ledger.get(host.operation['id'])['revision'], 'interrupted')
    again = host.client.post(url, json=body)
    assert again.status_code == 200 and again.get_json()['command']['id'] == first['command']['id']
    assert again.get_json()['command']['state'] == 'completed' and again.get_json()['command']['evidence']['effect'] == 'unconfirmed'
    changed = host.client.post(url, json=dict(body, action=f'{KEYS[0]}:fast'))
    assert changed.status_code == 409
    fresh = host.client.post(url, json=dict(body, idempotency_key='replay-2'))
    assert fresh.status_code == 409 and 'no longer active' in fresh.get_json()['error']


def test_its_executions_are_in_the_battle_detail_and_the_generic_history_route(host):
    pause(host, *KEYS)
    run_now(host, f'{KEYS[0]}:fast', 'go-1')
    for _ in range(9):
        tick(host)
    detail = host.client.get(f"/api/battles/{host.driver.battle['id']}").get_json()
    mine = [row for row in detail['executions']['items'] if row['action'] == 'synthetic_probe']
    assert [(row['instance'], row['trigger'], row['state']) for row in mine] == [('probe_a', 'manual', 'succeeded')]
    listed = host.client.get('/api/actions/executions?action=synthetic_probe').get_json()
    assert [(row['instance'], row['state'], row['command_id']) for row in listed['executions']] == [('probe_a', 'succeeded', commands(host)['go-1']['id'])]
    assert listed['context']['device_key'] == host.selected['device_key']


def test_a_saved_edit_reaches_the_registered_adapter_and_leaves_a_sibling_runs_shared_policy_unchanged(host):
    path = host.root / 'profiles' / 'mine.yaml'
    first = host.client.get('/api/run-controls?blueprint=farm').get_json()
    assert first['ok'] is True
    controls = first['capabilities']['controls']
    assert {f'routines.{key}.{field}' for key in KEYS for field in ('enabled', 'interval_sec')} <= set(controls)
    assert not any(control.endswith('.burst') for control in controls)                  # live only: it has no saved setting
    assert first['constraints'][f'routines.{KEYS[0]}.interval_sec'] == dict(min=10, max=600)
    assert first['settings']['routines'][KEYS[0]] == dict(enabled=True, interval_sec=60, burst=3)
    before = yaml.safe_load(path.read_text(encoding='utf-8'))
    changes = {f'routines.{KEYS[0]}.interval_sec': 120, f'routines.{KEYS[1]}.enabled': False}
    saved = host.client.post('/api/run-controls', json=dict(blueprint='farm', context=first['context'], expected_revision=first['profile_revision'],
                                                             changes=changes))
    assert saved.status_code == 200, saved.get_json()
    assert saved.get_json()['settings']['routines'][KEYS[0]]['interval_sec'] == 120
    assert saved.get_json()['settings']['routines'][KEYS[1]]['enabled'] is False
    after = yaml.safe_load(path.read_text(encoding='utf-8'))
    owned = after['blueprints']['farm']['policies']['gather']
    assert owned != 'shared' and after['policies']['gather'][owned]['synthetic_probe']['probe_a'] == dict(interval_sec=120)
    assert after['policies']['gather']['shared'] == before['policies']['gather']['shared']              # the sibling's policy is untouched
    assert after['blueprints']['sibling'] == before['blueprints']['sibling'] and after['blueprints']['sibling']['policies']['gather'] == 'shared'
    assert after['blueprints']['farm']['automation'][KEYS[1]] is False
    sibling = host.client.get('/api/run-controls?blueprint=sibling').get_json()
    assert sibling['settings']['routines'][KEYS[0]]['interval_sec'] == 60 and sibling['settings']['routines'][KEYS[1]]['enabled'] is True
    refused = host.client.post('/api/run-controls', json=dict(blueprint='farm', context=first['context'],
                                                              expected_revision=saved.get_json()['profile_revision'],
                                                              changes={f'routines.{KEYS[0]}.burst': 5}))
    assert refused.status_code == 400 and 'unavailable' in refused.get_json()['error']
    out_of_range = host.client.post('/api/run-controls', json=dict(blueprint='farm', context=first['context'],
                                                                   expected_revision=saved.get_json()['profile_revision'],
                                                                   changes={f'routines.{KEYS[0]}.interval_sec': 5}))
    assert out_of_range.status_code == 400


def test_no_route_was_added_and_the_route_table_is_the_same_with_and_without_the_extension(dash, catalog):
    before = sorted((rule.rule, rule.endpoint, tuple(sorted(rule.methods))) for rule in dash.app.url_map.iter_rules())
    with use_catalog(catalog):
        assert catalog.find('synthetic_probe') is not None
        during = sorted((rule.rule, rule.endpoint, tuple(sorted(rule.methods))) for rule in dash.app.url_map.iter_rules())
    assert during == before and CATALOG.find('synthetic_probe') is None


def test_the_pilot_catalog_is_not_changed_by_registering_the_extension_in_a_copy(catalog):
    assert 'synthetic_probe' not in CATALOG and 'synthetic_probe' in catalog
    assert [item.id for item in catalog][:len(list(CATALOG))] == [item.id for item in CATALOG]

def test_the_synthetic_action_is_named_in_no_production_file():
    files = list((REPO / 'backend').rglob('*.py'))
    assert test_paths.DASHBOARD in files      # the dashboard is scanned by the backend walk
    for path in files:
        if 'tests' in path.parts or '.venv' in path.parts or 'logs' in path.parts:
            continue
        assert 'synthetic_probe' not in path.read_text(encoding='utf-8', errors='ignore'), path