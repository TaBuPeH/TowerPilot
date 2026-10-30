"""The same action through the real Dashboard, recorded for the UI's tests (roadmap item 6, A2-7 backend request B1). TEST-ONLY.

`synthetic_probe` is registered, twice (two configured instances), only in a COPY of the action catalog that this generator swaps in with `use_catalog`.
No production file names it (test_the_synthetic_action_is_named_in_no_production_file): the app's non-test code must not either (spec 2.6), so these
recordings live in their own fixture, backend/tests/fixtures/synthetic_action_contract_v1.json, which the app loads from its tests only. They are NOT part of
dashboard_contract_pages_v1.json and never reach `src/contract/generated`.

Every answer is the real Flask route's (frontend/dashboard.py) over a synthetic world (the World of test_dashboard_contract.py: a temporary root, ledger and
leases, one fixed clock, an in-memory adb server and process table). The driver is a real `BattleSession` with a real `ActionSession`; the extension's handler,
binding and saved adapter are the ones test_action_synthetic_extension.py proves (`Probe`, `ProbeBinding`, `ProbeSaved`). Nothing touches a live dashboard,
device, ledger, lease or process.

  one family, `synthetic_action`:
    active / live controls   the two instances in GET /api/battles/active (item, key, descriptor, values, capabilities); a live-only field accepted and applied, refused outside its
                             bounds (400), refused for an unknown field or instance, a null that inherits the frozen value
    Run now                  accepted, started, completed (effect `unconfirmed`: the handler cannot confirm), the command and the execution it started linked in one read, a variant
                             token accepted, an unknown variant refused, the same key replayed after the driver ended (200, the same receipt), the same key with another payload (409),
                             a stale generation (409), an unknown reply (500 command_outcome_unknown) and the read that settles it
    history                  the executions of the battle in GET /api/battles/<id>
    newer schema             a projection written by a driver of a newer build (schema_version 2)
    next unknown             an action that cannot say when it is next due: `next.due_at` null with `next.reason`
    harvest                  turning Harvest on with the action present: the overlay is for the two gem routines only
    collector (AC7)          a standalone collector that Harvest launched, running, whose battle publishes the two instances beside the gem routines:
                             harvest.operation.routines holds all four, and collection evidence still rests on the gem routines alone
    pre-enable (AC7)         GET /api/status in the disabled world just before Harvest is turned on: harvesting off (choice unset), no overlay requested
    disabled (AC7)           the two instances switched off in the saved run before the battle starts; turning Harvest on targets the gem routines only,
                             and the reads after the overlay applies (the battle, and harvest.owner in the status) show both instances still disabled
"""
import contextlib
from types import SimpleNamespace

from control import battles as battle_api
from control import harvest
from player import playerprofile, saved_controls
from player.run_controls import resolve
from runtime import action_session as action_session_module
from runtime.action_session import ActionSession
from towerpilot_workflows import actions as actions_module
from towerpilot_workflows.action_contract import CATALOG, use_catalog

import test_dashboard_contract as dashboard
from test_action_synthetic_extension import DEFINITION, KEYS, Probe, ProbeBinding, ProbeSaved
from test_dashboard_contract import Driver, action_body, action_url, raising, with_orbit

SA = 'synthetic_action'
NOTE = (__doc__ or '').strip()
A, B = KEYS


@contextlib.contextmanager
def world(gen, name):
    """A contract world with the extension registered in a catalog copy (and its schema row and saved adapter), the way test_action_synthetic_extension.py does."""
    with gen.world(name) as w:
        catalog = CATALOG.copy()
        catalog.register(DEFINITION)
        w.mp.setattr(playerprofile, 'GATHER_KEYS', playerprofile.GATHER_KEYS + ('synthetic_probe',))
        w.mp.setitem(saved_controls.SAVED, 'synthetic_probe_saved', ProbeSaved())
        w.mp.setattr(actions_module, 'time', w.clock)
        w.mp.setattr(w.dash, '_HARVEST_HOST', {'last_tick_at': None, 'last_error': None})      # the dashboard remembers its last tick for the whole process: each world starts without one
        w.edit_profile(lambda profile: profile['policies']['gather']['shared'].update(synthetic_probe={'probe_a': {'interval_sec': 60}, 'probe_b': {'interval_sec': 90}}))
        with use_catalog(catalog):
            yield w


class Host:
    """The run driver of one world: the contract's driver (the real BattleSession launched through POST /api/control) with the extension's two instances bound to it."""

    def __init__(self, w, key, *, op=None):
        """op: a standalone collector that Harvest launched and that is already running; None launches a run through POST /api/control.

        A collector host is shaped as backend/gem_collector.py builds its own: it adopts the battle on screen (origin `adopted`) and holds the gem
        collectors and nothing else (`shop=None`, `mission=None`), so it publishes no shopping and none of a run's gates - only the gem routines and
        whatever it registered (here the extension's two instances)."""
        self.w = w
        self.d = Driver(w, key, origin='started' if op is None else 'adopted', op=op)
        if op is not None:
            self.d.rs = SimpleNamespace(gems=self.d.rs.gems, tracker=self.d.rs.tracker, mission=None, shop=None)
        self.session = ActionSession.for_battle(self.d.session, 'test')
        clock = self.session.clock = SimpleNamespace(mono=w.clock.monotonic, wall=w.clock.time)
        self.probes = {name: Probe(self.session, name.partition('@')[2], clock, interval=interval) for name, interval in zip(KEYS, (60, 90))}
        # each handler starts with the settings its battle froze (the saved run's, through the action's saved adapter), as the Driver's gates do
        frozen = resolve(self.d.session.base_preset, self.d.session.config)['routines']
        for name, probe in self.probes.items():
            probe.configure(frozen[name])
        self.d.rs.action_bindings = {name: ProbeBinding(probe) for name, probe in self.probes.items()}
        self.d.observe()

    @property
    def battle(self):
        return self.d.battle

    def tick(self, *, polls=True, visible=True):
        if polls:
            for probe in self.probes.values():
                probe.poll()
        self.d.checkpoint(visible)

    def live(self, changes, key, case=None, *, expect=200):
        body = dict(battle_id=self.battle['id'], generation=self.d.generation, expected_revision=self.battle['control_revision'], idempotency_key=key, changes=changes)
        url = action_url(self.d.op, 'live-controls')
        reply = self.w.post(SA, case, url, body) if case else self.w.plain_post(url, body)
        assert reply.status_code == expect, reply.get_json()
        return reply.get_json()

    def run_now(self, action, key, case=None, *, expect=200, **changes):
        body = action_body(self.d, action, key, **changes)
        url = action_url(self.d.op)
        reply = self.w.post(SA, case, url, body) if case else self.w.plain_post(url, body)
        assert reply.status_code == expect, reply.get_json()
        return reply

    def read(self, case):
        reply = self.w.get(SA, case, '/api/battles/active')
        assert reply.status_code == 200
        return reply.get_json()['battle']

    def commands(self):
        return {entry['request_keys'][0]: entry for entry in self.d.store.commands(self.battle['id'])}

    def items(self, battle):
        return {item['key']: item for item in battle['runtime']['actions']['items']}

    def pause(self, *keys):
        self.live({f'routines.{key}.enabled': False for key in keys}, 'pause-' + '-'.join(key.partition('@')[2] for key in keys))
        self.tick(polls=False)


def synthetic_action_cases(gen):
    with world(gen, 'synthetic_controls') as w:
        h = Host(w, 'controls')
        h.d.publish()
        active = h.read('synthetic_active_two_instances')
        shown = h.items(active)
        assert [item['key'] for item in active['runtime']['actions']['items']] == ['ad_gems', 'floating_gems', 'shopping', A, B]
        for key in KEYS:
            item = shown[key]
            assert (item['id'], item['instance'], item['label'], item['state'], item['coverage']) == ('synthetic_probe', key.partition('@')[2], 'Synthetic probe', 'due', 'full'), item
            assert [(field['path'], field['live'], field['saved']) for field in item['descriptor']['fields']] == [('enabled', True, True), ('interval_sec', True, True), ('burst', True, False)]
            assert [variant['token'] for variant in item['descriptor']['variants']] == ['fast', 'slow'] and item['descriptor']['capabilities']['run_now'] is True
            assert item['next']['basis'] == 'start_to_start' and item['last'] is None and item['active'] is None
        assert shown[A]['values'] == dict(enabled=True, interval_sec=60, burst=3) and shown[B]['values'] == dict(enabled=True, interval_sec=90, burst=3)
        assert set(active['capabilities']['controls']) >= {f'routines.{key}.{field}' for key in KEYS for field in ('enabled', 'interval_sec', 'burst')}
        assert set(active['capabilities']['actions']) >= {*KEYS, *(f'{key}:{variant}' for key in KEYS for variant in Probe.VARIANTS)}
        before = h.battle['control_revision']
        accepted = h.live({f'routines.{A}.burst': 7}, 'burst-1', 'synthetic_live_field_accepted')
        assert accepted['command']['state'] == 'requested' and accepted['control_revision'] == before + 1
        h.tick(polls=False)
        applied = h.read('synthetic_live_field_read_applied')
        assert h.probes[A].burst == 7 and h.probes[B].burst == 3 and h.commands()['burst-1']['state'] == 'applied'
        assert applied['effective']['routines'][A]['burst'] == 7 and h.items(applied)[A]['values']['burst'] == 7 and h.items(applied)[B]['values']['burst'] == 3
        revision = h.battle['control_revision']
        refused = h.live({f'routines.{A}.burst': 99}, 'burst-2', 'synthetic_live_field_out_of_bounds_refused', expect=400)
        assert f'Invalid value: routines.{A}.burst' in refused['error'] and h.battle['control_revision'] == revision
        assert 'Unknown control' in h.live({f'routines.{A}.nothing': 1}, 'unknown-field', 'synthetic_live_field_unknown_refused', expect=400)['error']
        assert 'Unsupported controls' in h.live({'routines.synthetic_probe@probe_z.burst': 4}, 'unknown-instance', 'synthetic_live_instance_unknown_refused', expect=400)['error']
        h.live({f'routines.{A}.burst': None}, 'burst-3', 'synthetic_live_field_null_inherits')
        h.tick(polls=False)
        inherited = h.read('synthetic_live_field_null_inherits_read')
        assert h.probes[A].burst == 3 and h.items(inherited)[A]['values']['burst'] == 3 and 'burst' not in h.battle['overrides'].get('routines', {}).get(A, {})
    with world(gen, 'synthetic_run_now') as w:
        h = Host(w, 'run-now')
        h.pause(*KEYS)
        assert not any(probe.opened for probe in h.probes.values())
        first = h.run_now(A, 'run-1', 'synthetic_run_now_accepted').get_json()
        assert first['command']['state'] == 'requested' and first['command']['payload'] == dict(action=A)
        h.tick(polls=False)                                                  # the checkpoint hands the request to the binding; the handler has not run yet
        h.tick()                                                             # the handler polls: due by request, its window opens
        started = h.read('synthetic_run_now_read_started')
        command_id = first['command']['id']
        assert h.commands()['run-1']['state'] == 'started' and h.items(started)[A]['state'] == 'running' and h.items(started)[A]['active']['window_ends_at'] is not None
        for _ in range(4):
            h.tick()
        done = h.read('synthetic_run_now_read_completed_unconfirmed')
        last = h.items(done)[A]['last']
        assert h.commands()['run-1']['state'] == 'completed' and h.commands()['run-1']['evidence']['effect'] == 'unconfirmed', h.commands()['run-1']
        assert (last['effect'], last['trigger'], last['result'], last['command_id']) == ('unconfirmed', 'manual', 'succeeded', command_id), last
        variant = h.run_now(f'{B}:fast', 'run-2', 'synthetic_run_now_variant_accepted').get_json()
        assert variant['command']['payload'] == dict(action=f'{B}:fast') and variant['command']['state'] == 'requested'
        refused = h.run_now(f'{A}:nope', 'run-3', 'synthetic_run_now_unknown_variant_refused', expect=400).get_json()
        assert refused['error'] == 'Unsupported manual action'
        h.run_now(f'{A}:fast', 'run-1', 'synthetic_run_now_changed_payload_refused', expect=409)
        h.run_now(A, 'run-4', 'synthetic_stale_generation_refused', expect=409, generation='an-older-driver')
        for _ in range(8):
            h.tick()
        assert h.commands()['run-2']['state'] == 'completed'
        detail = w.get(SA, 'synthetic_battle_detail_executions', f"/api/battles/{h.battle['id']}").get_json()
        mine = [row for row in detail['executions']['items'] if row['action'] == 'synthetic_probe']
        assert [(row['instance'], row['trigger'], row['state'], row['command_id']) for row in mine] == [('probe_b', 'manual', 'succeeded', variant['command']['id']),
                                                                                                    ('probe_a', 'manual', 'succeeded', command_id)], mine
        w.table.exit(h.d.op['worker']['pid'])
        replay = h.run_now(A, 'run-1', 'synthetic_run_now_replay_after_driver_ended').get_json()
        assert replay['command']['id'] == command_id and replay['command']['state'] == 'completed' and replay['command']['evidence']['effect'] == 'unconfirmed'
    with world(gen, 'synthetic_reply_unknown') as w:
        h = Host(w, 'unknown')
        h.pause(*KEYS)
        real = battle_api.public
        w.mp.setattr(battle_api, 'public', raising(KeyError('battle row vanished')))
        unknown = h.run_now(A, 'run-5', 'synthetic_run_now_reply_unknown', expect=500).get_json()
        assert unknown['code'] == 'command_outcome_unknown'
        w.mp.setattr(battle_api, 'public', real)
        read = h.read('synthetic_run_now_reply_unknown_read')
        assert [entry['request_keys'] for entry in read['commands'] if entry['kind'] == 'action'] == [['run-5']] and h.commands()['run-5']['state'] == 'requested'
    with world(gen, 'synthetic_newer_schema') as w:
        h = Host(w, 'newer')
        h.d.publish()
        w.mp.setattr(action_session_module, 'SCHEMA_VERSION', 2)             # the driver is a newer build: it writes the projection with its own version
        h.d.publish()
        newer = h.read('synthetic_projection_newer_schema')
        assert newer['runtime']['actions']['schema_version'] == 2 and {A, B} <= set(h.items(newer))
    with world(gen, 'synthetic_next_unknown') as w:
        h = Host(w, 'unknown-next')
        probe = h.probes[A]
        known = probe.status
        w.mp.setattr(probe, 'status', lambda: dict(known(), next_due_at=None))      # an action that cannot say when it is next due
        h.d.publish()
        unknown_next = h.read('synthetic_next_unknown_due_at_null')
        item = h.items(unknown_next)[A]
        assert item['next']['due_at'] is None and item['next']['reason'] == 'unknown' and item['state'] == 'waiting', item
    with world(gen, 'synthetic_harvest') as w:
        h = Host(w, 'harvest')
        w.edit_profile(with_orbit)         # after the battle froze its settings, the run's gathering settings gained timed orbit clicks: turning Harvest on has something to put on
        h.tick(polls=False)
        synthetic_before = {key: dict(h.d.battle['applied_controls']['routines'][key]) for key in KEYS}
        ident = harvest.identity_for(w.cfg)
        turn_on = dict(enabled=True, preset='bp_farm', expected_revision=0, idempotency_key='turn-on-synthetic',
                       expected_context={name: ident[name] for name in ('instance', 'account', 'serial', 'profile')})
        reply = w.post(SA, 'synthetic_harvest_on_accepted', '/api/harvest', turn_on)
        assert reply.status_code == 200, reply.get_json()
        w.dash._harvest_tick()
        status = dashboard.answer(w.client.get('/api/status'), 'GET', '/api/status')
        status['body'] = {key: value for key, value in status['body'].items() if key in ('harvest', 'battle', 'operations')}
        gen.cases.setdefault(SA, {})['synthetic_harvest_on_keeps_collection_gem_only'] = status
        overlay = status['body']['harvest']['overlay']
        assert overlay['state'] == 'requested' and overlay['paths'] == ['routines.floating_gems.enabled'] and set(overlay['targets']) == set(harvest.GEM_ROUTINES), overlay
        h.tick(polls=False)                                                       # the driver's checkpoint applies the overlay command
        active = h.read('synthetic_harvest_on_battle_read')
        sent = [entry for entry in active['commands'] if entry['kind'] == 'controls' and entry['request_keys'][0].startswith(harvest.OVERLAY_PREFIX)]
        assert len(sent) == 1 and sent[0]['state'] == 'applied' and list(sent[0]['payload']['routines']) == ['floating_gems'], sent
        assert {key: active['effective']['routines'][key] for key in KEYS} == synthetic_before and active['effective']['routines']['floating_gems']['enabled'] is True
    with world(gen, 'synthetic_collector') as w:
        # AC7 (a): Harvest launches the standalone collector; its battle publishes the gem routines AND the two instances
        turn_on_harvest(w, 'synthetic_collector_harvest_on_accepted', 'turn-on-collector')
        w.dash._harvest_tick()
        launched = w.client.get('/api/status').get_json()['harvest']['operation']
        assert launched is not None and launched['kind'] == 'gems' and launched['status'] == 'accepted' and launched['harvest_managed'] is True, launched
        op = w.store.get(launched['id'])
        op = w.store.transition(op['id'], op['revision'], 'running')            # the worker's own first transition
        h = Host(w, 'collector', op=op)
        h.d.publish()
        status = status_case(gen, w, 'synthetic_collector_running_routines_include_the_instances')
        harvest_view = status['body']['harvest']
        shown = harvest_view['operation']
        assert (harvest_view['state'], shown['status'], shown['worker_alive'], shown['kind']) == ('collecting', 'running', True, 'gems'), (harvest_view['state'], shown)
        assert list(shown['routines']) == [*harvest.GEM_ROUTINES, *KEYS], list(shown['routines'])        # a collector publishes its gem routines and its own registrations only
        assert {key: shown['routines'][key]['enabled'] for key in KEYS} == {A: True, B: True} and shown['routines']['ad_gems']['enabled'] is True, shown['routines']
        assert shown['battle']['fresh'] is True and shown['battle']['id'] == h.battle['id']
        battle = h.read('synthetic_collector_battle_read')
        assert {A, B, 'ad_gems', 'floating_gems'} <= set(h.items(battle)) and battle['operation_id'] == op['id']
        assert shown['routines'] == battle['runtime']['routines'], 'harvest.operation.routines is the battle\'s own publication'
    with world(gen, 'synthetic_harvest_disabled') as w:
        # AC7 (b): the two instances start disabled (the saved run switches them off), then Harvest is turned on over the run
        w.edit_profile(lambda profile: profile['blueprints']['farm'].setdefault('automation', {}).update({A: False, B: False}))
        h = Host(w, 'harvest-disabled')
        w.edit_profile(with_orbit)         # as in synthetic_harvest: after the battle froze its settings, the run's gathering settings gained timed orbit clicks
        h.tick(polls=False)
        before = h.read('synthetic_disabled_before_harvest_on')
        assert {key: (h.items(before)[key]['state'], h.items(before)[key]['enabled'], before['effective']['routines'][key]['enabled']) for key in KEYS} == {
            A: ('disabled', False, False), B: ('disabled', False, False)}, [h.items(before)[key] for key in KEYS]
        assert not any(probe.opened for probe in h.probes.values())
        synthetic_before = {key: dict(h.d.battle['applied_controls']['routines'][key]) for key in KEYS}
        pre = status_case(gen, w, 'synthetic_disabled_status_before_harvest_on')['body']['harvest']       # AC7 (c): the status the UI's Turn on starts from
        assert (pre['desired']['choice'], pre['desired']['enabled'], pre['overlay'], pre['operation']) == ('unset', None, None, None), (pre['desired'], pre['overlay'])
        assert {key: pre['owner']['routines'][key]['enabled'] for key in KEYS} == {A: False, B: False}
        turn_on_harvest(w, 'synthetic_disabled_harvest_on_accepted', 'turn-on-disabled')
        w.dash._harvest_tick()
        status = status_case(gen, w, 'synthetic_disabled_harvest_on_targets_the_gem_routines_only')
        overlay = status['body']['harvest']['overlay']
        assert overlay['state'] == 'requested' and overlay['paths'] == ['routines.floating_gems.enabled'] and set(overlay['targets']) == set(harvest.GEM_ROUTINES), overlay
        assert not [path for path in overlay['paths'] if 'synthetic_probe' in path]
        h.tick()                                                                  # the checkpoint applies the overlay; the handlers poll and stay idle
        after = h.read('synthetic_disabled_after_harvest_on')
        sent = [entry for entry in after['commands'] if entry['kind'] == 'controls' and entry['request_keys'][0].startswith(harvest.OVERLAY_PREFIX)]
        assert len(sent) == 1 and sent[0]['state'] == 'applied' and list(sent[0]['payload']['routines']) == ['floating_gems'], sent
        assert {key: after['effective']['routines'][key] for key in KEYS} == synthetic_before and after['effective']['routines']['floating_gems']['enabled'] is True
        assert {key: (h.items(after)[key]['state'], h.items(after)[key]['enabled'], h.items(after)[key]['values']['enabled']) for key in KEYS} == {
            A: ('disabled', False, False), B: ('disabled', False, False)}
        assert {key: after['runtime']['routines'][key]['enabled'] for key in KEYS} == {A: False, B: False} and not any(probe.opened for probe in h.probes.values())
        h.d.publish()
        later = status_case(gen, w, 'synthetic_disabled_status_after_harvest_on')['body']['harvest']
        assert later['operation'] is None and later['owner']['kind'] == 'operation' and later['overlay']['state'] == 'applied', (later['operation'], later['overlay'])
        assert {key: later['owner']['routines'][key]['enabled'] for key in KEYS} == {A: False, B: False} and later['owner']['routines']['floating_gems']['enabled'] is True


def turn_on_harvest(w, case, key):
    """POST /api/harvest enabled:true for the world's own context, recorded as `case`."""
    ident = harvest.identity_for(w.cfg)
    body = dict(enabled=True, preset='bp_farm', expected_revision=0, idempotency_key=key,
                expected_context={name: ident[name] for name in ('instance', 'account', 'serial', 'profile')})
    reply = w.post(SA, case, '/api/harvest', body)
    assert reply.status_code == 200, reply.get_json()
    return reply


def status_case(gen, w, case):
    """GET /api/status recorded as `case`, with the members the harvest views read (harvest, battle, operations), as synthetic_harvest_on_keeps_collection_gem_only."""
    status = dashboard.answer(w.client.get('/api/status'), 'GET', '/api/status')
    status['body'] = {key: value for key, value in status['body'].items() if key in ('harvest', 'battle', 'operations')}
    gen.cases.setdefault(SA, {})[case] = status
    return status


# B1 case id of the brief (backend/logs/reviews/ui-pages/a2-7-actions-brief.md section 11) -> the case of the fixture that satisfies it
B1 = {
    'synthetic_active_two_instances': 'synthetic_active_two_instances',
    'synthetic_live_field_accepted': 'synthetic_live_field_accepted',
    'synthetic_live_field_read_applied': 'synthetic_live_field_read_applied',
    'synthetic_live_field_out_of_bounds_refused': 'synthetic_live_field_out_of_bounds_refused',
    'synthetic_live_field_null_inherits': 'synthetic_live_field_null_inherits',
    'synthetic_live_field_null_inherits_read': 'synthetic_live_field_null_inherits_read',
    'synthetic_run_now_accepted': 'synthetic_run_now_accepted',
    'synthetic_run_now_read_started': 'synthetic_run_now_read_started',
    'synthetic_run_now_read_completed_unconfirmed': 'synthetic_run_now_read_completed_unconfirmed',
    'synthetic_run_now_variant_accepted': 'synthetic_run_now_variant_accepted',
    'synthetic_run_now_unknown_variant_refused': 'synthetic_run_now_unknown_variant_refused',
    'synthetic_run_now_replay_after_driver_ended': 'synthetic_run_now_replay_after_driver_ended',
    'synthetic_run_now_changed_payload_refused': 'synthetic_run_now_changed_payload_refused',
    'synthetic_run_now_reply_unknown': 'synthetic_run_now_reply_unknown',
    'synthetic_run_now_reply_unknown_read': 'synthetic_run_now_reply_unknown_read',
    'synthetic_stale_generation_refused': 'synthetic_stale_generation_refused',
    'synthetic_projection_newer_schema': 'synthetic_projection_newer_schema',
    'synthetic_battle_detail_executions': 'synthetic_battle_detail_executions',
    'synthetic_harvest_on_keeps_collection_gem_only': 'synthetic_harvest_on_keeps_collection_gem_only',
}
EXTRAS = ['synthetic_live_field_unknown_refused', 'synthetic_live_instance_unknown_refused', 'synthetic_next_unknown_due_at_null', 'synthetic_harvest_on_accepted',
          'synthetic_harvest_on_battle_read']
# AC7 review of A2-7 (r17c): the recordings that let the UI prove collection evidence stays gem-only -> the cases that satisfy them
AC7 = {
    'collector_running_with_the_synthetic_instances_in_its_routines': 'synthetic_collector_running_routines_include_the_instances',
    'synthetic_disabled_then_harvest_on_targets_the_gem_routines_only': 'synthetic_disabled_harvest_on_targets_the_gem_routines_only',
    'synthetic_still_disabled_after_the_overlay_applied': 'synthetic_disabled_after_harvest_on',
    'pre_enable_status_harvesting_off_no_overlay': 'synthetic_disabled_status_before_harvest_on',
}
AC7_EXTRAS = ['synthetic_collector_harvest_on_accepted', 'synthetic_collector_battle_read', 'synthetic_disabled_before_harvest_on', 'synthetic_disabled_harvest_on_accepted',
              'synthetic_disabled_status_after_harvest_on']
