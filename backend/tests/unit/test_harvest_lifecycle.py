"""Persistent gem harvesting: the person's choice, observed truth, reconciliation.

Real temporary ledgers and real operation admission (setup guard, device
ownership, idempotency, the durable launch fence). Only process creation, the
process table, adb state and run-settings resolution are fake boundaries. No
device, no network, no machine ledger.
"""
import copy
import hashlib
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from control import harvest, operations
from harvest_support import (
    CTX, FULL, NOW, OFF_ROUTINES, _battle, _collector, _run, active_ops, choose, current_revision, finish_last,
    harvest_body, harvest_overlay_key, identity, make_env, make_psutil, request, running, schema,
)
from towerpilot_workflows import harvest as ledger_h


@pytest.fixture
def h(tmp_path, monkeypatch):
    return make_env(tmp_path, monkeypatch)


@pytest.fixture
def psutil_fake(monkeypatch):
    return make_psutil(monkeypatch)


# ------------------------------------------------- read-only status, no schema
def test_nothing_chosen_reads_and_writes_nothing(h):
    ledger = operations.ledger_path(h.root)
    assert not ledger.exists()
    shown = h.status()
    assert (shown['state'], shown['code'], shown['desired']['choice'], shown['desired']['revision']) == (
        'off', 'never_enabled', 'unset', 0)
    result = h.tick()
    assert result['action'] == 'none' and h.spawned == []
    assert not ledger.exists(), 'status and an idle tick must not create the ledger'


def test_status_never_initializes_schema_in_an_existing_ledger(h):
    operations.ledger(h.root)   # a ledger that only knows operations (an older checkout)
    before = schema(operations.ledger_path(h.root))
    assert 'harvest_choices' not in before
    assert h.status()['state'] == 'off' and h.tick()['action'] == 'none'
    assert schema(operations.ledger_path(h.root)) == before


def test_status_is_byte_for_byte_read_only(h):
    choose(h)
    ledger = operations.ledger_path(h.root)
    before = hashlib.sha256(ledger.read_bytes()).hexdigest()
    result = h.status()
    assert (result['state'], result['code']) == ('starting', 'launch_due') and h.spawned == []
    assert hashlib.sha256(ledger.read_bytes()).hexdigest() == before


def test_an_unreadable_choice_is_unavailable_never_never_chosen(h):
    choose(h)
    with operations.ledger(h.root)._db() as db:
        db.execute('UPDATE harvest_choices SET body=?', ('not json',))
    shown = h.status()
    assert (shown['state'], shown['code']) == ('unavailable', 'harvest_state_unreadable')
    assert h.tick()['action'] == 'none' and h.spawned == []


# ------------------------------------------------------------ the choice
def test_enabling_validates_the_source_and_binds_context_and_policy(h):
    h.source['valid'] = False
    with pytest.raises(harvest.ChoiceRefused) as refusal:
        choose(h)
    assert refusal.value.code == 'policy_invalid' and refusal.value.extra['source']['code'] == 'gathering_disabled'
    assert current_revision(h) == 0
    h.source['valid'] = True
    result = choose(h)
    bound = result['choice']['bound']
    assert result['choice']['revision'] == 1 and result['choice']['label'] == 'Farm'
    assert {name: bound[name] for name in CTX} == {name: identity(h)[name] for name in CTX}
    assert bound['policy_revision'] == h.policy('bp_farm')


def test_context_is_checked_before_anything_is_recorded(h):
    with pytest.raises(harvest.ChoiceRefused) as refusal:
        choose(h, context={'serial': 'fake:9'})
    assert refusal.value.code == 'context_changed' and current_revision(h) == 0
    assert not operations.ledger_path(h.root).exists()


@pytest.mark.parametrize('mutate', [
    lambda b: b.pop('idempotency_key'), lambda b: b.update(idempotency_key='short'),
    lambda b: b.update(enabled='yes'), lambda b: b.pop('expected_context'),
    lambda b: b.pop('preset'), lambda b: b.pop('expected_revision'),
    lambda b: b.update(expected_revision=-1), lambda b: b['expected_context'].pop('account')],
    ids=['no-key', 'short-key', 'enabled-type', 'no-context', 'no-preset', 'no-revision', 'negative', 'no-account'])
def test_malformed_requests_are_refused_without_creating_anything(h, mutate):
    body = request(h)
    mutate(body)
    with pytest.raises(ValueError):
        harvest.apply_choice(h.root, h.cfg, body, resolve_source=h.resolve_source)
    assert not operations.ledger_path(h.root).exists()


def test_enabling_is_a_compare_and_set_and_off_always_applies(h):
    choose(h)
    with pytest.raises(harvest.ChoiceRefused) as refusal:
        choose(h, expected=0)
    assert refusal.value.code == 'harvest_revision_changed'
    assert refusal.value.extra['desired']['revision'] == 1 and refusal.value.extra['desired']['choice'] == 'on'
    off = choose(h, enabled=False, expected=0)   # a stale revision cannot stop an Off
    assert off['choice']['enabled'] is False and off['choice']['revision'] == 2
    assert off['choice']['preset'] == 'bp_farm' and off['choice']['label'] == 'Farm'   # kept for the UI


def test_a_lost_reply_is_answered_from_the_receipt_even_after_the_revision_advanced(h):
    body = request(h, key='lost-reply-1')
    first = harvest.apply_choice(h.root, h.cfg, body, resolve_source=h.resolve_source, now=NOW)
    choose(h, enabled=False)                        # the revision moves on
    again = harvest.apply_choice(h.root, h.cfg, body, resolve_source=h.resolve_source, now=NOW)
    assert (first['replayed'], again['replayed']) == (False, True)
    assert again['choice'] == first['choice'] and again['choice']['revision'] == 1
    assert current_revision(h) == 2


def test_a_request_that_was_never_recorded_cannot_take_effect_once_the_revision_moved_on(h):
    """What a client may conclude from a later read. A Turn on that lost its reply either recorded exactly one
    revision (its receipt answers any retry, whatever the revision is by then) or recorded nothing - and then ANY
    later delivery of it, a retry or a late duplicate, is refused once another writer advanced the revision. So a
    read that shows a revision above the request's expected_revision proves it can no longer take effect; that it
    WAS applied is shown by the choice carrying its request key at expected_revision + 1, or by its replay."""
    lost = request(h, key='never-arrived-1')                             # built at revision 0, never delivered
    choose(h, key='someone-else-1')
    choose(h, enabled=False, key='someone-else-2')
    assert current_revision(h) == 2
    with pytest.raises(harvest.ChoiceRefused) as refusal:
        harvest.apply_choice(h.root, h.cfg, lost, resolve_source=h.resolve_source, now=NOW)
    assert refusal.value.code == 'harvest_revision_changed' and current_revision(h) == 2
    with ledger_h.readonly(operations.ledger_path(h.root)) as db:
        assert ledger_h.read_receipt(db, 'never-arrived-1') is None      # nothing was recorded, so nothing can be replayed later
    arrived = request(h, key='arrived-1')                                # built at revision 2 and delivered, reply lost
    recorded = harvest.apply_choice(h.root, h.cfg, arrived, resolve_source=h.resolve_source, now=NOW)['choice']
    assert (recorded['revision'], recorded['request_key']) == (3, 'arrived-1')
    choose(h, enabled=False, key='someone-else-3')
    again = harvest.apply_choice(h.root, h.cfg, arrived, resolve_source=h.resolve_source, now=NOW)
    assert again['replayed'] is True and again['choice'] == recorded and current_revision(h) == 4


def test_a_replay_is_not_revalidated_against_a_source_that_changed_since(h):
    body = request(h, key='lost-reply-2')
    harvest.apply_choice(h.root, h.cfg, body, resolve_source=h.resolve_source)
    h.source['valid'] = False
    assert harvest.apply_choice(h.root, h.cfg, body, resolve_source=h.resolve_source)['replayed'] is True


def test_the_same_key_with_another_request_is_refused(h):
    body = request(h, key='same-key-1')
    harvest.apply_choice(h.root, h.cfg, body, resolve_source=h.resolve_source)
    with pytest.raises(harvest.ChoiceRefused) as refusal:
        harvest.apply_choice(h.root, h.cfg, dict(body, preset='bp_other'), resolve_source=h.resolve_source)
    assert refusal.value.code == 'idempotency_key_reused'


def test_off_replay_stops_only_while_that_off_is_current(h, monkeypatch):
    stops = []
    monkeypatch.setattr(harvest, 'stop_collectors', lambda root, ident, **kw: stops.append(1) or
                        dict(requested=True, stopped=[], stopping=[], errors=[]))
    choose(h)
    body = request(h, enabled=False, key='off-replay-1')
    harvest.apply_choice(h.root, h.cfg, body, resolve_source=h.resolve_source)
    harvest.apply_choice(h.root, h.cfg, body, resolve_source=h.resolve_source)   # lost reply: stop again
    assert len(stops) == 2
    choose(h)                                                                   # On again
    replay = harvest.apply_choice(h.root, h.cfg, body, resolve_source=h.resolve_source)
    assert replay['replayed'] is True and replay['stop']['requested'] is False and len(stops) == 2


def test_a_failure_after_the_commit_is_reported_in_the_stop_result_never_raised(h, monkeypatch):
    choose(h)
    monkeypatch.setattr(harvest, 'stop_collectors', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('psutil')))
    result = choose(h, enabled=False)
    assert result['choice']['enabled'] is False and 'RuntimeError: psutil' in result['stop']['errors'][0]['message']
    assert current_revision(h) == 2


# ------------------------------------------------- reconciliation and the fence
def test_enable_then_exactly_one_fenced_launch(h):
    choose(h)
    launched = h.tick()
    assert launched['action'] == 'launched' and len(h.spawned) == 1
    op = active_ops(h)[0]
    assert op['kind'] == 'gems'
    assert (op['request']['harvest'], op['request']['desire_revision'], op['request']['preset']) == (True, 1, 'bp_farm')
    assert op['request']['policy_revision'] == h.policy('bp_farm')
    assert op['idempotency_key'] == ledger_h.launch_key(identity(h)['device_key'], 1, 1)
    for later in (NOW + 1, NOW + 60):
        assert h.tick(later)['action'] == 'none'
    assert len(h.spawned) == 1 and h.status(NOW + 60)['operation']['id'] == op['id']


def test_explicit_off_is_never_undone_by_reconciliation(h):
    choose(h)
    choose(h, enabled=False)
    for moment in (NOW, NOW + 3600):
        assert h.tick(moment)['state'] == 'off'
    assert h.spawned == []


def test_off_stops_the_standalone_collector_and_leaves_the_battle(h, psutil_fake):
    choose(h)
    h.tick()
    op = running(h, active_ops(h)[0])
    store, battle = _battle(h, op)
    result = choose(h, enabled=False)
    assert result['stop']['stopped'] == [op['id']] and result['stop']['stopping'] == []
    assert psutil_fake.terminated == [4242]
    assert operations.ledger(h.root).get(op['id'])['status'] == 'cancelled'
    assert (store.get(battle['id'])['status'], store.get(battle['id'])['outcome']) == ('active', 'unknown')


def test_off_while_the_worker_is_still_exiting_reports_stopping_not_stopped(h, psutil_fake):
    psutil_fake.lingering = True
    choose(h)
    h.tick()
    op = running(h, active_ops(h)[0])
    result = choose(h, enabled=False)
    assert result['stop']['stopped'] == [] and result['stop']['stopping'] == [op['id']]
    shown = h.status()
    assert (shown['state'], shown['code']) == ('stopping', 'collector_stopping')
    psutil_fake.lingering = False                   # the next step retries a stuck stop, never assumes it
    assert harvest.stop_collectors(h.root, identity(h))['stopped'] == [op['id']]


def test_off_during_preparation_cannot_be_overtaken_by_the_launch(h):
    choose(h)

    def prepare_then_off(cfg_, preset):
        choose(h, enabled=False)          # another dashboard (or a second tab) turns it off mid-prepare
        return h.prepare(cfg_, preset)
    result = h.tick(prepare=prepare_then_off)
    assert result['action'] == 'refused'
    assert h.spawned == [] and active_ops(h) == []
    assert h.tick(NOW + 60)['action'] == 'none' and h.spawned == []
    assert any(kind == 'harvest_launch_refused' and fields['code'] == 'harvest_off' for kind, fields in h.events)


def test_a_new_revision_during_preparation_cannot_be_overtaken_by_the_stale_launch(h):
    choose(h)

    def prepare_then_change(cfg_, preset):
        choose(h, enabled=True, preset='bp_other')   # a different run, new revision
        return h.prepare(cfg_, preset)
    result = h.tick(prepare=prepare_then_change)
    assert result['action'] == 'refused' and h.spawned == [] and active_ops(h) == []
    assert any(fields['code'] == 'harvest_changed' for kind, fields in h.events if kind == 'harvest_launch_refused')
    assert h.tick(NOW + harvest.REFUSAL_RETRY_SEC)['action'] == 'launched'
    assert (active_ops(h)[0]['request']['preset'], active_ops(h)[0]['request']['desire_revision']) == ('bp_other', 2)


def test_off_after_acceptance_by_a_host_that_crashed_is_stopped_by_the_next_host(h, psutil_fake):
    choose(h)
    h.tick()
    op = running(h, active_ops(h)[0])
    # The Off was recorded, then that dashboard died before it could stop anything.
    ledger_h.Harvest(operations.ledger_path(h.root)).set_choice(
        identity(h), enabled=False, preset=None, label=None, expected_revision=None, bound=None,
        reason='paused_by_person', changed_by='person', request_key='crashed-off-1', fingerprint='x')
    shown = h.status(NOW + 1)
    assert (shown['state'], shown['code']) == ('stopping', 'harvest_off')
    assert h.tick(NOW + 1)['action'] == 'stopped_collector'
    assert operations.ledger(h.root).get(op['id'])['status'] == 'cancelled'
    assert h.tick(NOW + 60)['action'] == 'none' and len(h.spawned) == 1


def test_two_hosts_never_create_two_collectors(h):
    choose(h)

    def racing_launch(cfg_, body, cmd):
        # another host accepts a different attempt for the same revision first
        h.launch(cfg_, dict(body, idempotency_key='another-host-attempt'), cmd)
        return h.launch(cfg_, body, cmd)
    result = h.tick(launch=racing_launch)
    assert result['action'] == 'refused' and len(h.spawned) == 1 and len(active_ops(h)) == 1


def test_the_same_deterministic_attempt_from_two_hosts_is_one_operation(h):
    choose(h)
    key = ledger_h.launch_key(identity(h)['device_key'], 1, 1)
    body = harvest_body(h, key=key)
    first = operations.launch(h.root, h.cfg, 'gems', body, ['python'], h.spawn)
    again = operations.launch(h.root, h.cfg, 'gems', body, ['python'], h.spawn)
    assert first['id'] == again['id'] and len(h.spawned) == 1


def test_interrupted_bookkeeping_loses_nothing(h, monkeypatch):
    choose(h)

    def disk_full(self, *args, **kwargs):
        raise sqlite3.OperationalError('disk full')
    with monkeypatch.context() as patched:
        patched.setattr(ledger_h.Harvest, 'update_runtime', disk_full)
        assert h.tick()['action'] == 'launched'
    assert len(active_ops(h)) == 1 and h.tick(NOW + 5)['action'] == 'none' and len(h.spawned) == 1
    assert h.status(NOW + 5)['attempts'] == 1


def test_the_fence_refuses_a_forged_automatic_launch_without_a_choice(h):
    body = harvest_body(h, key='forged-launch-1')
    with pytest.raises(operations.Conflict) as refusal:
        operations.launch(h.root, h.cfg, 'gems', body, ['python'], h.spawn)
    assert refusal.value.code == 'harvest_off' and h.spawned == [] and active_ops(h) == []


@pytest.mark.parametrize('change,code', [
    (dict(desire_revision=9), 'harvest_changed'), (dict(preset='bp_other'), 'harvest_changed'),
    (dict(desire_revision=None), 'harvest_changed')])
def test_the_fence_binds_revision_and_preset(h, change, code):
    choose(h)
    body = harvest_body(h, key='fenced-launch-1')
    body.update(change)
    with pytest.raises(operations.Conflict) as refusal:
        operations.launch(h.root, h.cfg, 'gems', body, ['python'], h.spawn)
    assert refusal.value.code == code and h.spawned == []


def test_the_fence_binds_account_and_profile_and_a_different_emulator_has_no_choice(h):
    choose(h)
    body = harvest_body(h, key='fenced-launch-2')
    other_account = dict(h.cfg, accounts={'acct': {}}, instances=dict(
        h.cfg['instances'], main=dict(h.cfg['instances']['main'], account='acct')))
    for change, code in ((dict(h.cfg, active_profile='another'), 'context_changed'), (other_account, 'context_changed'),
                         (dict(h.cfg, instances=dict(h.cfg['instances'], main=dict(
                             h.cfg['instances']['main'], serial='fake:7'))), 'harvest_off')):
        with pytest.raises(operations.Conflict) as refusal:
            operations.launch(h.root, change, 'gems', dict(body), ['python'], h.spawn)
        assert refusal.value.code == code
    assert h.spawned == []


def test_a_manual_collector_start_is_not_fenced(h):
    operations.launch(h.root, h.cfg, 'gems', dict(preset='bp_farm', idempotency_key='manual-collect-1'), ['python'], h.spawn)
    assert len(h.spawned) == 1


@pytest.mark.parametrize('change,expected', [
    (lambda c: c['instances']['main'].update(allow_taps=False), 'taps_disabled'),
    (lambda c: c.update(active_profile='another'), 'context_changed'),
    (lambda c: (c['instances']['main'].update(account='acct'), c.update(accounts={'acct': {}})), 'context_changed')],
    ids=['taps', 'profile', 'account'])
def test_preconditions_block_visibly_and_launch_nothing(h, change, expected):
    choose(h)
    cfg = copy.deepcopy(h.cfg)
    change(cfg)
    result = h.tick(cfg_=cfg)
    assert (result['state'], result['code'], result['action'], h.spawned) == ('blocked', expected, 'none', [])


def test_policy_that_becomes_invalid_blocks_visibly_and_stops_the_collector(h, psutil_fake):
    choose(h)
    h.tick()
    op = running(h, active_ops(h)[0])
    h.source['valid'] = False                      # the run was edited so no gem routine is on
    shown = h.status(NOW + 1)
    assert (shown['state'], shown['code'], shown['source']['valid']) == ('blocked', 'policy_invalid', False)
    assert h.tick(NOW + 1)['action'] == 'stopped_collector'
    assert operations.ledger(h.root).get(op['id'])['status'] == 'cancelled'
    after = h.tick(NOW + 60)
    assert (after['state'], after['code'], after['action']) == ('blocked', 'policy_invalid', 'none') and len(h.spawned) == 1
    h.source['valid'] = True
    assert h.tick(NOW + 120)['action'] == 'launched'


def test_a_source_edit_that_stays_valid_is_flagged_and_used_at_the_next_launch(h):
    choose(h)
    bound = h.policy('bp_farm')
    h.source['edit'] = 1
    shown = h.status()
    assert shown['source']['changed_since_enable'] is True and shown['desired']['bound']['policy_revision'] == bound
    h.tick()
    assert active_ops(h)[0]['request']['policy_revision'] == h.policy('bp_farm') != bound


def test_offline_emulator_and_run_refusals_launch_nothing_and_recheck(h):
    choose(h)
    h.adb['state'] = 'offline'
    result = h.tick()
    assert (result['state'], result['code'], h.spawned) == ('blocked', 'emulator_offline', [])
    h.adb['state'] = 'device'
    h.refusal = ('digits_missing', 'Learn the wave digits before collecting gems.')
    assert h.tick(NOW + harvest.REFUSAL_RETRY_SEC)['code'] == 'digits_missing'
    h.refusal = None
    assert h.tick(NOW + harvest.REFUSAL_RETRY_SEC + 5)['action'] == 'none'          # rechecked at most every 10 s
    assert h.tick(NOW + 2 * harvest.REFUSAL_RETRY_SEC)['action'] == 'launched' and len(h.spawned) == 1


def test_a_transfer_hold_keeps_every_host_from_launching_and_preserves_the_choice(h):
    choose(h)
    harvest.begin_transfer(h.root, identity(h), 'run_start', now=NOW)
    shown = h.tick(NOW + 1)
    assert (shown['state'], shown['code'], shown['transfer']['reason']) == ('transferring', 'run_transfer', 'run_start')
    assert h.spawned == []
    body = harvest_body(h, key='during-transfer-1')
    with pytest.raises(operations.Conflict) as refusal:      # the durable fence holds even for a host that skipped the check
        operations.launch(h.root, h.cfg, 'gems', body, ['python'], h.spawn)
    assert refusal.value.code == 'transfer_pending'
    harvest.end_transfer(h.root, identity(h))
    assert h.tick(NOW + 2)['action'] == 'launched'
    assert h.status()['desired']['enabled'] is True and current_revision(h) == 1


def test_an_expired_transfer_hold_does_not_block_forever(h):
    choose(h)
    harvest.begin_transfer(h.root, identity(h), 'run_start', now=NOW)
    assert h.tick(NOW + harvest.TRANSFER_HOLD_SEC + 1)['action'] == 'launched'   # the fence reads the same clock


def test_wait_for_release_needs_no_operation_and_a_free_lease(h):
    choose(h)
    h.tick()
    op = active_ops(h)[0]
    clock = SimpleNamespace(t=0.0)
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock.t += seconds
        if len(sleeps) == 3:
            operations.ledger(h.root).transition(op['id'], op['revision'], 'failed')
    free = {'value': False}
    released, waited = harvest.wait_for_release(h.root, identity(h), lease_free=lambda: free['value'], timeout=10,
                                                sleep=sleep, clock=lambda: clock.t)
    assert released is False and waited >= 10                         # the lease is still held
    free['value'] = True
    released, _ = harvest.wait_for_release(h.root, identity(h), lease_free=lambda: True, timeout=10,
                                           sleep=sleep, clock=lambda: clock.t)
    assert released is True


# ------------------------------------------------- failures and the circuit
def test_fast_failures_back_off_and_stop_after_the_circuit(h):
    choose(h)
    now = NOW
    assert h.tick(now)['action'] == 'launched'
    for failures in range(1, harvest.CIRCUIT + 1):
        finish_last(h, 'failed', ran=5, now=now)
        result = h.tick(now + 1)
        assert result['failures'] == failures
        if failures < harvest.CIRCUIT:
            assert result['state'] == 'backing_off'
            assert result['next_attempt_at'] == now + harvest.backoff(failures)
            now = result['next_attempt_at']
            assert h.tick(now)['action'] == 'launched'
    assert (result['state'], result['code'], result['circuit_open']) == ('blocked', 'collector_failing', True)
    assert len(h.spawned) == harvest.CIRCUIT
    assert h.tick(now + 86400)['action'] == 'none'
    choose(h)                              # the person turns it on again: a new revision, a clean count
    assert h.tick(now + 86400)['action'] == 'launched'


def test_long_runs_cancellations_and_fence_refusals_are_not_failures(h):
    choose(h)
    assert h.tick()['action'] == 'launched'
    finish_last(h, 'failed', ran=harvest.FAIL_WINDOW_SEC + 1)                    # it ran for a while: not a failed attempt
    again = h.tick(NOW + 1)
    assert again['failures'] == 0 and again['action'] == 'launched'
    finish_last(h, 'failed', ran=1, now=NOW + 1,
                error={'code': 'CollectorRefused', 'message': 'Harvesting no longer asks for this collector'})
    refused = h.tick(NOW + harvest.MIN_SPACING_SEC)
    assert refused['failures'] == 0 and refused['action'] == 'launched'          # a fence refusal never opens the circuit
    finish_last(h, 'cancelled', ran=30, now=NOW + harvest.MIN_SPACING_SEC + 30)
    assert h.tick(NOW + harvest.MIN_SPACING_SEC + 31)['failures'] == 0


def test_a_quick_clean_exit_still_waits_the_minimum_spacing(h):
    choose(h)
    assert h.tick()['action'] == 'launched'
    finish_last(h, 'succeeded', ran=3, now=NOW + 3)
    waiting = h.tick(NOW + 4)
    assert (waiting['state'], waiting['failures']) == ('backing_off', 0)
    assert waiting['next_attempt_at'] == NOW + harvest.MIN_SPACING_SEC     # ten seconds after the launch
    assert h.tick(NOW + harvest.MIN_SPACING_SEC)['action'] == 'launched'


def test_a_crashed_worker_is_retired_then_counted(h):
    choose(h)
    assert h.tick()['action'] == 'launched'
    op = active_ops(h)[0]
    operations.ledger(h.root).transition(op['id'], op['revision'], 'running', worker={'pid': 1, 'creation_time': 1})
    h.alive['value'] = False                   # the worker died without an outcome
    result = h.tick(NOW + 2)
    assert operations.ledger(h.root).get(op['id'])['status'] == 'interrupted'
    assert result['failures'] == 1 and result['state'] == 'backing_off'
    assert result['next_attempt_at'] == NOW + 2 + harvest.backoff(1)


# ------------------------------------------------------- what is observed
@pytest.mark.parametrize('kwargs,state,code', [
    (dict(), 'collecting', 'collecting'),
    (dict(observing=False), 'waiting_for_battle', 'waiting_for_battle'),
    (dict(status='prepared', observing=False), 'waiting_for_battle', 'waiting_for_battle'),
    (dict(published=NOW - harvest.FRESH_SEC - 1), 'unavailable', 'stale_observation'),
    (dict(published=NOW + 60), 'unavailable', 'observation_invalid'),
    (dict(published=None), 'unavailable', 'observation_invalid'),
    (dict(routines={'ad_gems': {'enabled': False}, 'floating_gems': {'enabled': False}}),
     'live_paused', 'gem_routines_paused'),
    (dict(routines={}), 'unavailable', 'routines_not_reported'),
], ids=['fresh', 'not-observing', 'prepared', 'stale', 'future', 'no-time', 'both-off', 'no-routines'])
def test_the_collector_state_comes_from_finite_nonfuture_fresh_evidence(h, kwargs, state, code):
    choose(h)
    op = _collector(h)
    _battle(h, op, **kwargs)
    shown = h.status()
    assert (shown['state'], shown['code']) == (state, code)
    assert shown['operation']['id'] == op['id'] and shown['operation']['harvest_managed'] is True


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf'), True, '1800000000', 10 ** 30])
def test_non_numeric_non_finite_or_future_publication_never_reads_as_fresh(h, bad):
    choose(h)
    op = _collector(h)
    store, battle = _battle(h, op)
    with store._db() as db:       # the ledger refuses NaN on write, so tamper with the stored JSON directly
        body = json.loads(db.execute('SELECT body FROM battles WHERE id=?', (battle['id'],)).fetchone()[0])
        body['runtime']['server_time'] = bad
        db.execute('UPDATE battles SET body=? WHERE id=?', (json.dumps(body, sort_keys=True), battle['id']))
    shown = h.status()
    assert shown['state'] == 'unavailable' and shown['code'] == 'observation_invalid'


def test_a_running_collector_without_a_battle_is_waiting_never_collecting(h):
    choose(h)
    _collector(h)
    shown = h.status()
    assert (shown['state'], shown['code'], shown['operation']['battle']) == ('waiting_for_battle', 'between_battles', None)


def test_a_dead_collector_worker_is_unavailable_not_collecting(h):
    choose(h)
    op = _collector(h)
    _battle(h, op)
    h.alive['value'] = False
    shown = h.status()
    assert (shown['state'], shown['code']) == ('unavailable', 'worker_not_running')


def test_a_collector_that_is_still_starting_is_starting(h):
    choose(h)
    h.tick()
    shown = h.status()
    assert (shown['state'], shown['code'], shown['operation']['status']) == ('starting', 'collector_starting', 'accepted')


def test_natural_battle_ends_leave_the_collector_waiting_and_launch_nothing(h):
    choose(h)
    op = _collector(h)
    store, battle = _battle(h, op)
    assert h.status()['state'] == 'collecting'
    store.finish(battle['id'], 'g1', reason='observed_results')
    after = h.tick(NOW + 30)
    assert (after['state'], after['code'], after['action']) == ('waiting_for_battle', 'between_battles', 'none')
    _battle(h, op, generation='g1-next', published=NOW + 29)   # the next battle, whoever started it
    assert h.status(NOW + 30)['state'] == 'collecting' and h.spawned == []


def test_a_manual_collector_is_reported_even_when_never_chosen(h):
    op = _collector(h, harvest_owned=False)
    _battle(h, op)
    shown = h.status()
    assert shown['desired']['choice'] == 'unset' and shown['state'] == 'collecting'
    assert shown['operation']['harvest_managed'] is False
    assert h.tick()['action'] == 'none'


def test_a_collector_of_an_older_revision_is_replaced_not_adopted(h, psutil_fake):
    choose(h)
    old = _collector(h, revision=1)
    choose(h, preset='bp_other')                   # revision 2: another run's gem settings
    assert h.status()['code'] == 'harvest_changed'
    assert h.tick()['action'] == 'stopped_collector'
    assert operations.ledger(h.root).get(old['id'])['status'] == 'cancelled'
    assert h.tick(NOW + 20)['action'] == 'launched'
    assert active_ops(h)[0]['request']['desire_revision'] == 2


# --------------------------------------------------------------- full drivers
def test_a_running_run_is_the_owner_and_gathers_comes_only_from_its_published_routines(h):
    choose(h)
    run = _run(h)
    unknown = h.status()['owner']
    assert (unknown['gathers'], unknown['gems']['basis']) == (None, 'not_reported')   # a run alone proves nothing
    _battle(h, run, routines=OFF_ROUTINES, controls=FULL, gather={'ad_gems': False})
    owner = h.status()['owner']
    assert (owner['kind'], owner['operation_kind'], owner['gathers'], owner['gems']['basis']) == (
        'operation', 'run', False, 'published_routines')
    assert owner['recovery']['action'] == 'stop_operation' and h.status()['code'] == 'owner_gathers_off'
    assert h.spawned == []


def test_a_stale_run_publication_never_claims_gathers(h):
    run = _run(h)
    _battle(h, run, published=NOW - 600, controls=FULL)
    owner = h.status()['owner']
    assert (owner['gathers'], owner['gems']['basis']) == (None, 'stale')


def test_a_foreign_runner_process_blocks_launch_and_says_it_is_untracked(h):
    choose(h)
    h.rows.append({'pid': 4242, 'runner': 'orchestrator', 'cmdline': 'python orchestrator.py'})
    shown = h.tick()
    assert (shown['state'], shown['code'], shown['owner']['foreign'], shown['owner']['pid']) == (
        'owned_by_driver', 'process_owns_device', True, 4242)
    assert shown['owner']['gathers'] is None and h.spawned == [] and shown['overlay'] is None


def test_on_sets_each_routine_to_the_selected_runs_value_never_both(h):
    h.source.update(ad=True, floating=False)           # an ad-only source
    choose(h)
    run = _run(h)
    # the running run gathers only by orbit; ad gems are off
    store, battle = _battle(h, run, controls=FULL, gather={'ad_gems': False, 'gem_orbit': {'enabled': True}},
                            routines={'ad_gems': {'enabled': False}, 'floating_gems': {'enabled': True}})
    assert h.tick()['action'] == 'overlay'
    [command] = store.commands(battle['id'])
    assert command['payload'] == {'routines': {'ad_gems': {'enabled': True}, 'floating_gems': {'enabled': False}}}
    assert command['request_keys'][0] == harvest_overlay_key(battle, 1)
    assert h.tick(NOW + 1)['action'] == 'none' and len(store.commands(battle['id'])) == 1       # once per revision


def test_an_orbit_only_source_does_not_enable_ad_gems(h):
    h.source.update(ad=False, floating=True)
    choose(h)
    run = _run(h)
    store, battle = _battle(h, run, controls=FULL, gather={'ad_gems': False})
    h.tick()
    [command] = store.commands(battle['id'])
    assert command['payload'] == {'routines': {'floating_gems': {'enabled': True}}}      # ad stays as the run has it


def test_off_suppresses_both_gem_routines_of_a_running_driver_without_ending_it(h):
    choose(h)
    run = _run(h)
    store, battle = _battle(h, run, controls=FULL, gather={'ad_gems': True, 'gem_orbit': {'enabled': True}},
                            routines={'ad_gems': {'enabled': True}, 'floating_gems': {'enabled': True}})
    choose(h, enabled=False)
    assert h.tick()['action'] == 'overlay'
    [command] = store.commands(battle['id'])
    assert command['payload'] == {'routines': {'ad_gems': {'enabled': False}, 'floating_gems': {'enabled': False}}}
    assert command['request_keys'][0] == harvest_overlay_key(battle, 2)
    assert (store.get(battle['id'])['status'], operations.ledger(h.root).get(run['id'])['status']) == ('active', 'running')


def test_a_persons_live_choice_for_a_routine_always_wins(h):
    choose(h)
    run = _run(h)
    store, battle = _battle(h, run, controls=FULL, gather={'ad_gems': False})
    person = store.request_controls(battle['id'], run['id'], 'g1', 0, {'routines': {'ad_gems': {'enabled': False}}}, 'person-live-1')
    store.acknowledge(battle['id'], 'g1', person['id'], applied_paths=['routines.ad_gems.enabled'], state='applied')
    shown = h.status()
    assert shown['overlay']['skipped']['ad_gems'] == 'live_choice'
    assert h.tick()['action'] in ('none', 'overlay')
    commands = store.commands(battle['id'])
    assert [c['payload'] for c in commands if not c['request_keys'][0].startswith(ledger_h.OVERLAY_PREFIX)] == [
        {'routines': {'ad_gems': {'enabled': False}}}]
    assert all('ad_gems' not in c['payload'].get('routines', {}) for c in commands if c['request_keys'][0].startswith(ledger_h.OVERLAY_PREFIX))
    assert shown['overlay']['state'] in ('live_choice', 'not_needed')


def test_a_driver_without_the_controls_is_reported_unsupported_and_sent_nothing(h):
    choose(h)
    run = _run(h)
    store, battle = _battle(h, run, controls=[])
    assert h.tick()['action'] == 'none'
    assert store.commands(battle['id']) == []
    assert h.status()['overlay']['state'] == 'unsupported' and h.spawned == []


def test_a_prepared_battle_waits_for_its_capabilities(h):
    choose(h)
    run = _run(h)
    store, battle = _battle(h, run, status='prepared', observing=False, controls=FULL)
    assert h.tick()['action'] == 'none' and store.commands(battle['id']) == []
    assert h.status()['overlay']['state'] == 'waiting_for_battle'


def test_a_new_choice_revision_or_battle_generation_gets_its_own_command(h):
    h.source.update(ad=True, floating=False)
    choose(h)
    run = _run(h)
    store, battle = _battle(h, run, controls=FULL, gather={'ad_gems': False})
    h.tick()
    [first] = store.commands(battle['id'])
    store.acknowledge(battle['id'], 'g1', first['id'], applied_paths=['routines.ad_gems.enabled'], state='applied')
    choose(h, enabled=False)                       # revision 2 on the same battle and generation
    assert h.tick(NOW + 1)['action'] == 'overlay'
    assert len(store.commands(battle['id'])) == 2
    store.finish(battle['id'], 'g1', reason='observed_results')
    _, second = _battle(h, run, generation='g2', controls=FULL, gather={'ad_gems': True})
    assert h.tick(NOW + 2)['action'] == 'overlay'  # the next battle: the choice applies there too
    assert len(store.commands(second['id'])) == 1


def test_the_overlay_is_not_requested_when_the_source_is_invalid(h):
    choose(h)
    run = _run(h)
    h.source['valid'] = False
    store, battle = _battle(h, run, controls=FULL, gather={'ad_gems': False})
    assert h.tick()['action'] == 'none' and store.commands(battle['id']) == []
    assert h.status()['overlay']['state'] == 'blocked'


def test_a_pending_control_revision_defers_the_overlay_and_it_is_sent_once_the_person_is_acknowledged(h):
    choose(h)
    run = _run(h)
    store, battle = _battle(h, run, controls=FULL, gather={'ad_gems': False})
    person = store.request_controls(battle['id'], run['id'], 'g1', 0,
                                    {'routines': {'floating_gems': {'enabled': True}}}, 'person-pending-1')
    assert h.tick()['action'] == 'overlay'
    assert [c['id'] for c in store.commands(battle['id'])] == [person['id']]          # the person's command stands alone
    assert any(kind == 'harvest_overlay_deferred' for kind, fields in h.events)
    store.acknowledge(battle['id'], 'g1', person['id'], applied_paths=['routines.floating_gems.enabled'], state='applied')
    assert h.tick(NOW + 1)['action'] == 'overlay'
    sent = [c for c in store.commands(battle['id']) if c['request_keys'][0].startswith(ledger_h.OVERLAY_PREFIX)]
    assert [c['payload'] for c in sent] == [{'routines': {'ad_gems': {'enabled': True}}}]   # the person's routine is left alone


def test_only_standalone_harvest_collectors_may_be_taken_over_by_a_run(h):
    assert harvest.owners(h.root, h.cfg, []) == [] and harvest.only_collectors([]) is False
    choose(h)
    op = _collector(h)
    found = harvest.owners(h.root, h.cfg, [])
    assert harvest.only_collectors(found) is True
    assert found[0]['recovery'] == dict(action='pause_harvesting', operation_id=op['id'], expected_revision=1)
    operations.ledger(h.root).transition(op['id'], op['revision'], 'failed')
    _collector(h, harvest_owned=False)
    assert harvest.only_collectors(harvest.owners(h.root, h.cfg, [])) is False       # a person's own collector is not ours to take
    assert harvest.only_collectors(found + [{'kind': 'process', 'harvest_managed': False}]) is False


def test_an_owner_nobody_can_cancel_offers_no_recovery(h):
    store = operations.ledger(h.root)
    context = operations.context(h.root, h.cfg, {}, None)
    write, _ = store.accept('profile_write', context, {}, 'profile-write-key')
    store.transition(write['id'], write['revision'], 'running', worker={'pid': 1, 'creation_time': 1})
    owner = h.status()['owner']
    assert (owner['operation_kind'], owner['recovery']) == ('profile_write', None)      # synchronous persistence cannot be cancelled


# ======================================================== review round r1 fixes
def person_off(store, battle, run, key='independent-person-off'):
    """A person's live Ad Gems Off, recorded and acknowledged by the driver."""
    person = store.request_controls(battle['id'], run['id'], 'g1', battle['control_revision'],
                                    {'routines': {'ad_gems': {'enabled': False}}}, key)
    store.acknowledge(battle['id'], 'g1', person['id'], applied_paths=['routines.ad_gems.enabled'], state='applied')
    return person


# ---- H2B-02: Off stops a standalone collector whoever started it
@pytest.mark.parametrize('managed', [True, False], ids=['launched-by-the-host', 'started-by-a-person'])
def test_off_stops_every_standalone_collector_and_never_ends_the_battle(h, psutil_fake, managed):
    choose(h)
    op = _collector(h, harvest_owned=managed)
    store, battle = _battle(h, op)
    result = choose(h, enabled=False)
    assert result['stop']['stopped'] == [op['id']] and result['stop']['stopping'] == [] and result['stop']['errors'] == []
    assert psutil_fake.terminated == [1] and operations.ledger(h.root).get(op['id'])['status'] == 'cancelled'
    assert (store.get(battle['id'])['status'], store.get(battle['id'])['outcome']) == ('active', 'unknown')
    assert h.tick(NOW + 1)['action'] == 'none' and h.spawned == []


def test_a_manual_collector_that_is_still_exiting_is_reported_stopping(h, psutil_fake):
    psutil_fake.lingering = True
    choose(h)
    op = _collector(h, harvest_owned=False)
    result = choose(h, enabled=False)
    assert result['stop']['stopped'] == [] and result['stop']['stopping'] == [op['id']]


def test_an_off_recorded_by_a_host_that_died_is_finished_for_a_manual_collector_too(h, psutil_fake):
    choose(h)
    op = _collector(h, harvest_owned=False)                      # running before the Off
    ledger_h.Harvest(operations.ledger_path(h.root)).set_choice(
        identity(h), enabled=False, preset=None, label=None, expected_revision=None, bound=None,
        reason='paused_by_person', changed_by='person', request_key='off-by-a-dead-host', fingerprint='x', now=NOW)
    shown = h.status(NOW + 1)
    assert (shown['state'], shown['code']) == ('stopping', 'harvest_off')
    assert h.tick(NOW + 1)['action'] == 'stopped_collector'
    assert operations.ledger(h.root).get(op['id'])['status'] == 'cancelled'


def test_a_collector_a_person_starts_after_an_off_is_a_newer_request_and_is_left_alone(h, psutil_fake):
    body = request(h, enabled=False, key='off-then-collect')
    choose(h)
    harvest.apply_choice(h.root, h.cfg, body, resolve_source=h.resolve_source, now=NOW)
    h.clock.now = NOW + 30
    later = _collector(h, harvest_owned=False)                   # Collect diamonds, pressed after the Off
    assert h.tick(NOW + 31)['action'] == 'none' and operations.ledger(h.root).get(later['id'])['status'] == 'running'
    replay = harvest.apply_choice(h.root, h.cfg, body, resolve_source=h.resolve_source, now=NOW + 40)
    assert replay['replayed'] is True and replay['stop']['stopped'] == [] and psutil_fake.terminated == []


# ---- H2B-01: the overlay is a compare-and-set on the facts it was planned from
@pytest.fixture
def paused_plan(h, monkeypatch):
    """Run h.tick() in a real thread and hold it between planning the overlay and sending it
    (the plan it would send: the first decision of a generation is frozen before that)."""
    state = SimpleNamespace(planned=threading.Event(), resume=threading.Event(), result=[], errors=[], thread=None)
    real = harvest.plan_overlay
    once = {'paused': False}

    def pausing(*args, **kwargs):
        plan = real(*args, **kwargs)
        if threading.current_thread() is state.thread and not once['paused'] and plan['patch'] and not plan.get('freeze'):
            once['paused'] = True
            state.planned.set()
            assert state.resume.wait(10), 'test coordination timed out'
        return plan
    monkeypatch.setattr(harvest, 'plan_overlay', pausing)

    def start(now=NOW):
        def run():
            try:
                state.result.append(h.tick(now))
            except BaseException as exc:   # noqa: BLE001 - surfaced by the assertion below
                state.errors.append(repr(exc))
        state.thread = threading.Thread(target=run, name='planning-tick', daemon=True)
        state.thread.start()
        assert state.planned.wait(10), 'no overlay was planned'

    def release():
        state.resume.set()
        state.thread.join(10)
        assert not state.thread.is_alive() and not state.errors, state.errors
    state.start, state.release = start, release
    return state


def overlay_commands(store, battle):
    return [c for c in store.commands(battle['id']) if c['request_keys'][0].startswith(ledger_h.OVERLAY_PREFIX)]


def test_a_persons_live_choice_recorded_after_the_plan_is_never_overwritten(h, paused_plan):
    choose(h)
    run = _run(h)
    store, battle = _battle(h, run, controls=FULL, gather={'ad_gems': False})
    paused_plan.start()
    person_off(store, battle, run)                       # another host's person command lands after the plan was read
    paused_plan.release()
    assert store.get(battle['id'])['requested_controls']['routines']['ad_gems']['enabled'] is False
    assert overlay_commands(store, battle) == []
    assert any(kind == 'harvest_overlay_deferred' for kind, _ in h.events)
    assert h.tick(NOW + 1)['action'] in ('none', 'overlay') and overlay_commands(store, battle) == []   # recomputed: live choice wins
    assert h.status(NOW + 1)['overlay']['skipped']['ad_gems'] == 'live_choice'


def test_an_off_recorded_after_the_plan_drops_the_stale_enable(h, paused_plan):
    choose(h)
    run = _run(h)
    store, battle = _battle(h, run, controls=FULL, gather={'ad_gems': False})
    paused_plan.start()
    choose(h, enabled=False)                             # Off is exempt from the action lock: it can land at any moment
    paused_plan.release()
    assert store.get(battle['id'])['requested_controls']['routines']['ad_gems']['enabled'] is False
    assert overlay_commands(store, battle) == []
    assert any(kind == 'harvest_overlay_deferred' for kind, _ in h.events)


def test_the_overlay_is_recorded_only_with_the_choice_it_was_planned_from(h):
    choose(h)
    run = _run(h)
    store, battle = _battle(h, run, controls=FULL, gather={'ad_gems': False})
    patch = {'routines': {'ad_gems': {'enabled': True}}}
    stale = dict(revision=1, enabled=True)
    choose(h, enabled=False)
    with pytest.raises(ledger_h.HarvestFenceRefused) as refusal:
        store.request_controls(battle['id'], run['id'], 'g1', 0, patch, 'stale-overlay-1',
                               admit=lambda db, row: ledger_h.verify_overlay(db, stale, row))
    assert refusal.value.code == 'harvest_changed' and store.commands(battle['id']) == []
    assert store.get(battle['id'])['control_revision'] == 0


# ---- H2B-03: the bound context gates the overlay, from both sides
def test_a_choice_bound_to_one_profile_does_nothing_to_another_profiles_driver(h):
    choose(h)
    h.cfg['active_profile'] = 'someone-else'
    run = _run(h)
    store, battle = _battle(h, run, controls=FULL, gather={'ad_gems': False})
    result = h.tick()
    assert (result['state'], result['code'], result['action']) == ('blocked', 'context_changed', 'none')
    assert result['overlay']['state'] == 'blocked' and store.commands(battle['id']) == []
    assert h.spawned == []


def test_a_driver_started_under_another_profile_is_not_touched_even_when_the_dashboard_matches(h):
    choose(h)
    h.cfg['active_profile'] = 'someone-else'
    run = _run(h)                                        # the driver runs under 'someone-else'
    store, battle = _battle(h, run, controls=FULL, gather={'ad_gems': False})
    h.cfg['active_profile'] = 'mine'                     # the dashboard is back on the bound profile
    result = h.tick()
    assert (result['state'], result['code']) == ('blocked', 'context_changed')
    assert store.commands(battle['id']) == []
    with pytest.raises(ledger_h.HarvestFenceRefused) as refusal:   # and the ledger itself refuses it, whoever asks
        store.request_controls(battle['id'], run['id'], 'g1', 0, {'routines': {'ad_gems': {'enabled': True}}}, 'direct-1',
                               admit=lambda db, row: ledger_h.verify_overlay(db, dict(revision=1, enabled=True), row))
    assert refusal.value.code == 'context_changed'


def test_an_off_bound_to_another_profile_does_not_suppress_this_profiles_driver(h):
    choose(h)
    choose(h, enabled=False)
    h.cfg['active_profile'] = 'someone-else'
    run = _run(h)
    store, battle = _battle(h, run, controls=FULL, routines={'ad_gems': {'enabled': True}, 'floating_gems': {'enabled': False}}, gather={'ad_gems': True})
    result = h.tick()
    assert result['overlay']['state'] == 'blocked' and store.commands(battle['id']) == []
    assert result['state'] == 'owned_by_driver'


# ---- H2B-06: one command per choice revision and battle generation
def test_a_source_edit_never_adds_a_second_command_on_the_same_revision_and_generation(h):
    h.source.update(ad=True, floating=False)
    choose(h)
    run = _run(h)
    store, battle = _battle(h, run, controls=FULL, gather={'ad_gems': False})
    assert h.tick()['action'] == 'overlay'
    [first] = store.commands(battle['id'])
    store.acknowledge(battle['id'], 'g1', first['id'], applied_paths=['routines.ad_gems.enabled'], state='applied')
    h.source.update(ad=False, floating=True, edit=1)     # a different valid policy for the same choice
    for moment in (NOW + 1, NOW + 2):
        assert h.tick(moment)['action'] == 'none'
    assert [c['id'] for c in store.commands(battle['id'])] == [first['id']]
    shown = h.status(NOW + 2)
    assert shown['overlay']['state'] == 'applied' and shown['overlay']['targets']['ad_gems'] is True   # what was asked
    assert shown['source']['changed_since_enable'] is True
    choose(h)                                            # an explicit re-apply is a new revision and gets its own command
    assert h.tick(NOW + 3)['action'] == 'overlay'
    assert [c['payload'] for c in overlay_commands(store, battle)][-1] == {
        'routines': {'ad_gems': {'enabled': False}, 'floating_gems': {'enabled': True}}}


# ---- H2AD-01: bookkeeping belongs to its own revision
def test_bookkeeping_planned_under_an_older_revision_cannot_replace_the_current_revisions(h):
    choose(h)

    def slow_old_host(cfg_, preset):
        choose(h, preset='bp_other')                    # revision 2 while this host is still preparing revision 1
        h.refusal = ('digits_missing', 'the new host refused revision 2')
        newer = h.tick(NOW)                             # the new host records its refusal and retry deadline
        assert (newer['code'], newer['next_attempt_at']) == ('digits_missing', NOW + harvest.REFUSAL_RETRY_SEC)
        raise harvest.Refused('old_source_refused', 'the old host refused revision 1')
    result = h.tick(NOW, prepare=slow_old_host)
    assert result['action'] == 'refused'
    now_state = h.status(NOW)
    assert (now_state['code'], now_state['next_attempt_at']) == ('digits_missing', NOW + harvest.REFUSAL_RETRY_SEC)
    assert now_state['last_outcome']['message'] == 'the new host refused revision 2'
    with pytest.raises(ledger_h.HarvestStale):
        ledger_h.Harvest(operations.ledger_path(h.root)).update_runtime(identity(h)['device_key'], 1, next_attempt_at=NOW + 999)


def test_the_run_transfer_hold_belongs_to_the_emulator_and_survives_a_new_revision(h):
    choose(h)
    harvest.begin_transfer(h.root, identity(h), 'run_start', now=NOW)
    choose(h, preset='bp_other')
    assert h.status(NOW + 1)['transfer']['reason'] == 'run_start'
    harvest.end_transfer(h.root, identity(h))
    assert h.status(NOW + 1)['transfer'] is None and current_revision(h) == 2


# ---- H2AD-02: the policy is frozen once and bound end to end
def test_a_settings_edit_between_planning_and_preparing_replans_and_never_launches_a_hybrid(h):
    choose(h)

    def edit_then_prepare(cfg_, preset):
        h.source.update(ad=False, floating=True, edit=1)         # saved while this host was preparing
        return h.prepare(cfg_, preset)
    first = h.tick(prepare=edit_then_prepare)
    assert first['action'] == 'replanned' and h.spawned == [] and active_ops(h) == []
    assert any(kind == 'harvest_launch_replanned' for kind, _ in h.events)
    second = h.tick(NOW + 1)
    assert second['action'] == 'launched'
    op = active_ops(h)[0]
    assert op['request']['policy_revision'] == ledger_h.envelope_policy_revision(op['request']) == h.policy('bp_farm')


@pytest.mark.parametrize('mutate', [
    lambda body: body.update(policy_revision='0' * 64),
    lambda body: body.pop('policy_revision'),
    lambda body: body.pop('battle_envelope'),
    lambda body: body['battle_envelope']['preset']['gather'].update(ad_gems=False, gem_orbit={'enabled': True}),
], ids=['wrong-revision', 'no-revision', 'no-envelope', 'envelope-edited'])
def test_the_ledger_refuses_a_launch_whose_policy_and_envelope_disagree(h, mutate):
    choose(h)
    body = harvest_body(h, key='hybrid-1')
    mutate(body)
    with pytest.raises(ledger_h.HarvestFenceRefused) as refusal:
        operations.launch(h.root, h.cfg, 'gems', body, ['python'], h.spawn)
    assert refusal.value.code == 'policy_changed' and h.spawned == [] and active_ops(h) == []


def test_the_worker_refuses_an_operation_whose_frozen_policy_no_longer_matches_its_request(h):
    choose(h)
    h.tick()
    op = active_ops(h)[0]
    assert ledger_h.automatic_launch_valid(operations.ledger_path(h.root), op) == (True, None)
    forged = json.loads(json.dumps(op))
    forged['request']['battle_envelope']['preset']['gather']['ad_gems'] = False
    assert ledger_h.automatic_launch_valid(operations.ledger_path(h.root), forged) == (False, 'policy_changed')


# ---- H2R2-04: what a choice revision applies to a battle generation is fixed by the first decision
def test_the_first_decision_for_a_generation_is_fixed_even_when_it_needed_no_command(h):
    h.source.update(ad=True, floating=False)
    choose(h)
    run = _run(h)
    store, battle = _battle(h, run, controls=FULL, gather={'ad_gems': True})      # the driver already does what the source asks
    assert h.tick()['action'] == 'none' and h.status()['overlay']['state'] == 'not_needed'
    h.source.update(ad=False, floating=True, edit=1)                              # a different valid policy, same revision
    for moment in (NOW + 1, NOW + 2):
        assert h.tick(moment)['action'] == 'none'
    assert store.commands(battle['id']) == []                                     # no second decision, no late command
    shown = h.status(NOW + 2)
    assert shown['overlay']['state'] == 'not_needed' and shown['overlay']['targets'] == {'ad_gems': True, 'floating_gems': False}
    assert shown['source']['changed_since_enable'] is True
    store.finish(battle['id'], 'g1', reason='observed_results')
    _, following = _battle(h, run, generation='g2', controls=FULL, gather={'ad_gems': True})
    assert h.tick(NOW + 3)['action'] == 'overlay'                                 # the next battle follows the edit
    assert [c['payload'] for c in overlay_commands(store, following)] == [
        {'routines': {'ad_gems': {'enabled': False}, 'floating_gems': {'enabled': True}}}]


def test_a_new_choice_revision_decides_afresh_after_a_no_op_first_decision(h):
    h.source.update(ad=True, floating=False)
    choose(h)
    run = _run(h)
    store, battle = _battle(h, run, controls=FULL, gather={'ad_gems': True})
    assert h.tick()['action'] == 'none'
    h.source.update(ad=False, floating=True, edit=1)
    choose(h)                                                                     # an explicit re-apply is a new revision
    assert h.tick(NOW + 1)['action'] == 'overlay'
    assert [c['payload'] for c in overlay_commands(store, battle)] == [
        {'routines': {'ad_gems': {'enabled': False}, 'floating_gems': {'enabled': True}}}]


def test_a_decision_recorded_before_the_command_is_sent_is_the_one_that_is_sent(h):
    # A host that died between recording the decision and sending the command: the next host sends
    # what was decided, not what the (since edited) source says now.
    h.source.update(ad=True, floating=False)
    choose(h)
    run = _run(h)
    store, battle = _battle(h, run, controls=FULL, gather={'ad_gems': False})
    ledger_h.Harvest(operations.ledger_path(h.root)).freeze_overlay(
        identity(h)['device_key'], 1, battle['id'], 'g1', {'ad_gems': True, 'floating_gems': False})
    h.source.update(ad=False, floating=True, edit=1)
    assert h.tick(NOW + 1)['action'] == 'overlay'
    assert [c['payload'] for c in overlay_commands(store, battle)] == [{'routines': {'ad_gems': {'enabled': True}}}]


def test_the_first_writer_of_a_generations_decision_wins_and_a_stale_revision_writes_nothing(h):
    choose(h)
    run = _run(h)
    store, battle = _battle(h, run, controls=FULL, gather={'ad_gems': False})
    harvest_store = ledger_h.Harvest(operations.ledger_path(h.root))
    device = identity(h)['device_key']
    first = harvest_store.freeze_overlay(device, 1, battle['id'], 'g1', {'ad_gems': True, 'floating_gems': False})
    again = harvest_store.freeze_overlay(device, 1, battle['id'], 'g1', {'ad_gems': False, 'floating_gems': True})
    assert again == first and again['targets'] == {'ad_gems': True, 'floating_gems': False}
    choose(h, enabled=False)
    with pytest.raises(ledger_h.HarvestStale):                      # revision 1 is no longer the person's choice
        harvest_store.freeze_overlay(device, 1, battle['id'], 'g1', {'ad_gems': True, 'floating_gems': True})
    assert '"ad_gems": true, "floating_gems": true' not in stored_runtime(h)


def inject_freezes(h, records):
    """Plant raw records in the revision's decision map, as a damaged ledger or an older writer could have left them."""
    device = identity(h)['device_key']
    with sqlite3.connect(operations.ledger_path(h.root)) as db:
        row = db.execute('SELECT body FROM harvest_runtime WHERE device=?', (device,)).fetchone()
        body = json.loads(row[0]) if row else {}
        body.update(for_revision=1, overlay_freezes=records)
        db.execute('INSERT OR REPLACE INTO harvest_runtime VALUES (?,?)', (device, json.dumps(body)))


MALFORMED = [
    pytest.param(lambda battle: {'battle_id': 'another-battle', 'generation': 'g1', 'targets': {'ad_gems': False, 'floating_gems': True}}, id='another-battle'),
    pytest.param(lambda battle: {'battle_id': None, 'generation': 'g1', 'targets': {'ad_gems': False, 'floating_gems': True}}, id='no-battle'),
    pytest.param(lambda battle: {'battle_id': battle['id'], 'generation': 'other', 'targets': {'ad_gems': False, 'floating_gems': True}}, id='another-generation'),
    pytest.param(lambda battle: {'battle_id': battle['id'], 'generation': 'g1', 'targets': {'ad_gems': 'yes', 'floating_gems': True}}, id='targets-not-booleans'),
    pytest.param(lambda battle: {'battle_id': battle['id'], 'generation': 'g1', 'targets': {'ad_gems': True}}, id='targets-incomplete'),
    pytest.param(lambda battle: {'battle_id': battle['id'], 'generation': 'g1', 'targets': 'ad'}, id='targets-not-an-object'),
    pytest.param(lambda battle: 'garbage', id='not-an-object'),
]


@pytest.mark.parametrize('malformed', MALFORMED)
def test_a_recorded_decision_for_something_else_or_malformed_decides_nothing(h, malformed):
    h.source.update(ad=True, floating=False)
    choose(h)
    run = _run(h)
    store, battle = _battle(h, run, controls=FULL, gather={'ad_gems': False})
    inject_freezes(h, {ledger_h.freeze_key(battle['id'], 'g1'): malformed(battle)})
    assert h.status()['overlay']['targets'] == {'ad_gems': True, 'floating_gems': False}   # decided from the source
    assert h.tick(NOW + 1)['action'] == 'overlay'                                          # and replaced by a real decision
    assert [c['payload'] for c in overlay_commands(store, battle)] == [{'routines': {'ad_gems': {'enabled': True}}}]
    kept = json.loads(stored_runtime(h))['overlay_freezes'][ledger_h.freeze_key(battle['id'], 'g1')]
    assert ledger_h.valid_freeze(kept, battle['id'], 'g1') and kept['targets'] == {'ad_gems': True, 'floating_gems': False}


def stored_runtime(h):
    with sqlite3.connect(operations.ledger_path(h.root)) as db:
        row = db.execute('SELECT body FROM harvest_runtime').fetchone()
    return row[0] if row else ''


def test_a_status_read_never_records_the_decision(h):
    choose(h)
    run = _run(h)
    _battle(h, run, controls=FULL, gather={'ad_gems': False})
    before = hashlib.sha256(operations.ledger_path(h.root).read_bytes()).hexdigest()
    for moment in (NOW, NOW + 1):
        assert h.status(moment)['overlay']['state'] == 'not_requested'
    assert hashlib.sha256(operations.ledger_path(h.root).read_bytes()).hexdigest() == before
    assert 'overlay_freezes' not in stored_runtime(h)
    h.tick(NOW + 2)                                                              # only the lifecycle host records it
    assert 'overlay_freezes' in stored_runtime(h)


def test_an_off_recorded_between_the_plan_and_the_first_decision_drops_both(h, monkeypatch):
    choose(h)
    run = _run(h)
    store, battle = _battle(h, run, controls=FULL, gather={'ad_gems': False})
    real = harvest.plan_overlay
    reached, resume, outcome = threading.Event(), threading.Event(), {}

    def pausing(*args, **kwargs):
        plan = real(*args, **kwargs)
        if threading.current_thread().name == 'deciding-tick' and plan.get('freeze') and not reached.is_set():
            reached.set()
            assert resume.wait(10), 'test coordination timed out'
        return plan
    monkeypatch.setattr(harvest, 'plan_overlay', pausing)
    thread = threading.Thread(target=lambda: outcome.update(result=h.tick()), name='deciding-tick', daemon=True)
    thread.start()
    assert reached.wait(10)
    choose(h, enabled=False)                                                     # lands after the plan, before the decision is recorded
    resume.set()
    thread.join(10)
    assert not thread.is_alive() and outcome['result']['action'] == 'none'
    assert overlay_commands(store, battle) == [] and 'overlay_freezes' not in stored_runtime(h)


# ---- H2R2-01 at the status: a worker that cannot be checked is not a worker that stopped
def test_a_collector_whose_worker_cannot_be_checked_is_never_retired_or_replaced_and_off_reports_it_stopping(h, psutil_fake):
    choose(h)
    collector = _collector(h)
    h.alive['value'] = None                                                      # the worker's liveness cannot be established
    shown = h.tick()
    assert (shown['state'], shown['code']) == ('unavailable', 'worker_unverified')
    assert shown['operation']['worker_alive'] is None
    assert operations.ledger(h.root).get(collector['id'])['status'] == 'running' and h.spawned == []
    off = choose(h, enabled=False)
    assert off['stop']['stopped'] == [] and off['stop']['stopping'] == [collector['id']]
    assert operations.ledger(h.root).get(collector['id'])['status'] == 'cancelling'      # stop requested, input NOT proven stopped
    assert psutil_fake.terminated == []                                          # nothing was killed on a guess


# ---- R3: the overlay decision cannot be displaced by a host that planned for an older battle generation
NO_OP_GATHER = {'ad_gems': True, 'gem_orbit': {'enabled': False}}   # what the default source asks: the driver already does it


def delayed_host(h, monkeypatch, old, *, after_recording=False):
    """h.tick() in a real thread, held at Harvest.freeze_overlay for battle `old`: just before it records its
    decision (default) or just after it recorded it (after_recording). Deterministic: events, no sleeps."""
    state = SimpleNamespace(reached=threading.Event(), resume=threading.Event(), result=[], errors=[], thread=None)
    real = ledger_h.Harvest.freeze_overlay

    def hold(self, device, revision, battle_id, generation, targets):
        mine = threading.current_thread() is state.thread and battle_id == old['id']
        if mine and not after_recording:
            state.reached.set()
            assert state.resume.wait(10), 'test coordination timed out'
        outcome = real(self, device, revision, battle_id, generation, targets)
        if mine and after_recording:
            state.reached.set()
            assert state.resume.wait(10), 'test coordination timed out'
        return outcome
    monkeypatch.setattr(ledger_h.Harvest, 'freeze_overlay', hold)

    def start(now=NOW):
        def run():
            try:
                state.result.append(h.tick(now))
            except BaseException as exc:   # noqa: BLE001 - surfaced by the assertion below
                state.errors.append(repr(exc))
        state.thread = threading.Thread(target=run, name='old-generation-host', daemon=True)
        state.thread.start()
        assert state.reached.wait(10), 'the host never reached the decision'

    def release():
        state.resume.set()
        state.thread.join(10)
        assert not state.thread.is_alive() and not state.errors, state.errors
    state.start, state.release = start, release
    return state


def recorded_decisions(h):
    stored = stored_runtime(h)
    return json.loads(stored).get('overlay_freezes', {}) if stored else {}


@pytest.mark.parametrize('stale_host', [False, True], ids=['control-no-stale-host', 'stale-host-from-the-finished-generation'])
def test_an_old_generations_host_cannot_displace_the_new_generations_first_decision(h, monkeypatch, stale_host):
    h.source.update(ad=True, floating=False)
    choose(h)
    run = _run(h)
    store, g1 = _battle(h, run, controls=FULL, gather=NO_OP_GATHER)
    if stale_host:
        host = delayed_host(h, monkeypatch, g1)
        host.start()                                          # planned for g1, held just before it records the decision
    else:
        assert h.tick()['action'] == 'none'                   # g1's no-op decision, recorded in time
    store.finish(g1['id'], 'g1', reason='death', outcome='death', evidence={'natural': True})
    _, g2 = _battle(h, run, generation='g2', controls=FULL, gather=NO_OP_GATHER)
    assert h.tick(NOW + 1)['action'] == 'none'                # another host freezes a no-op decision for g2
    assert store.commands(g2['id']) == [] and h.status(NOW + 1)['overlay']['state'] == 'not_needed'
    h.source.update(ad=False, floating=True, edit=1)          # then the selected run is edited
    if stale_host:
        host.release()
        assert host.result[0]['action'] == 'none'             # its plan was dropped, not re-evaluated into g2
        dropped = [fields for kind, fields in h.events if kind == 'harvest_overlay_dropped']
        assert [d['battle_id'] for d in dropped] == [g1['id']] and 'ended' in dropped[0]['reason']
    later = h.tick(NOW + 2)
    assert later['action'] == 'none' and store.commands(g2['id']) == [], 'the edited flags reached the new generation'
    assert later['overlay']['targets'] == {'ad_gems': True, 'floating_gems': False}
    decisions = recorded_decisions(h)
    assert list(decisions) == [ledger_h.freeze_key(g2['id'], 'g2')]                       # one decision, g2's, intact
    assert decisions[ledger_h.freeze_key(g2['id'], 'g2')]['targets'] == {'ad_gems': True, 'floating_gems': False}


def test_a_plan_whose_battle_changes_between_the_record_and_the_re_read_is_dropped_not_carried_over(h, monkeypatch):
    h.source.update(ad=True, floating=False)
    choose(h)
    run = _run(h)
    store, g1 = _battle(h, run, controls=FULL, gather={'ad_gems': False})   # the driver differs: g1 would need a command
    host = delayed_host(h, monkeypatch, g1, after_recording=True)
    host.start()                                              # g1's decision is recorded; held before the plan is re-read
    store.finish(g1['id'], 'g1', reason='death', outcome='death', evidence={'natural': True})
    _, g2 = _battle(h, run, generation='g2', controls=FULL, gather={'ad_gems': False})
    h.source.update(ad=False, floating=True, edit=1)
    host.release()
    assert host.result[0]['action'] == 'none'
    assert store.commands(g1['id']) == [] and store.commands(g2['id']) == []   # the old plan sent nothing anywhere
    dropped = [fields for kind, fields in h.events if kind == 'harvest_overlay_dropped']
    assert [(d['battle_id'], d['reason']) for d in dropped] == [(g1['id'], 'battle_changed')]
    assert ledger_h.freeze_key(g2['id'], 'g2') not in recorded_decisions(h)    # g2 has not been decided by anyone yet
    assert h.tick(NOW + 2)['action'] == 'overlay'             # the next step decides g2 from its own facts (the edit came first)
    assert [c['payload'] for c in overlay_commands(store, g2)] == [{'routines': {'floating_gems': {'enabled': True}}}]
    assert recorded_decisions(h)[ledger_h.freeze_key(g2['id'], 'g2')]['targets'] == {'ad_gems': False, 'floating_gems': True}
    assert list(recorded_decisions(h)) == [ledger_h.freeze_key(g2['id'], 'g2')]


def test_a_decision_is_recorded_only_for_the_live_battle_at_its_generation(h):
    choose(h)
    run = _run(h)
    store, g1 = _battle(h, run, controls=FULL, gather=NO_OP_GATHER)
    harvest_store = ledger_h.Harvest(operations.ledger_path(h.root))
    device = identity(h)['device_key']
    targets = {'ad_gems': True, 'floating_gems': False}
    for battle_id, generation in (('no-such-battle', 'g1'), (g1['id'], 'another-generation')):
        with pytest.raises(ledger_h.HarvestStale):
            harvest_store.freeze_overlay(device, 1, battle_id, generation, targets)
    assert recorded_decisions(h) == {}
    store.finish(g1['id'], 'g1', reason='death', outcome='death', evidence={'natural': True})
    with pytest.raises(ledger_h.HarvestStale, match='ended'):
        harvest_store.freeze_overlay(device, 1, g1['id'], 'g1', targets)
    assert recorded_decisions(h) == {}
    with pytest.raises(ValueError):                           # a decision names a boolean for every gem routine
        _, g2 = _battle(h, run, generation='g2', controls=FULL, gather=NO_OP_GATHER)
        harvest_store.freeze_overlay(device, 1, g2['id'], 'g2', {'ad_gems': True})


def test_a_battle_of_another_emulator_cannot_be_decided_for_this_one(h):
    run = _run(h)
    store, g1 = _battle(h, run, controls=FULL, gather=NO_OP_GATHER)
    other = copy.deepcopy(h.cfg)
    other['active_instance'] = 'other'
    choose(h, cfg=other)                                      # a choice on the other emulator, revision 1
    harvest_store = ledger_h.Harvest(operations.ledger_path(h.root))
    with pytest.raises(ledger_h.HarvestStale, match='not a battle of this emulator'):
        harvest_store.freeze_overlay(identity(h, other)['device_key'], 1, g1['id'], 'g1', {'ad_gems': True, 'floating_gems': False})
    assert recorded_decisions(h) == {}


def test_a_decision_for_a_newer_battle_already_recorded_refuses_an_older_one(h):
    choose(h)
    run = _run(h)
    store, g1 = _battle(h, run, controls=FULL, gather=NO_OP_GATHER)
    inject_freezes(h, {'newer/g9': dict(battle_id='newer', generation='g9', order=10 ** 9,
                                        targets={'ad_gems': False, 'floating_gems': True}, at=NOW)})
    harvest_store = ledger_h.Harvest(operations.ledger_path(h.root))
    with pytest.raises(ledger_h.HarvestStale, match='newer battle'):
        harvest_store.freeze_overlay(identity(h)['device_key'], 1, g1['id'], 'g1', {'ad_gems': True, 'floating_gems': False})
    assert list(recorded_decisions(h)) == ['newer/g9']        # nothing was added, nothing was dropped


def test_decisions_of_older_battles_are_dropped_once_a_newer_one_is_recorded_and_each_keeps_its_first(h):
    choose(h)
    run = _run(h)
    store, g1 = _battle(h, run, controls=FULL, gather=NO_OP_GATHER)
    harvest_store = ledger_h.Harvest(operations.ledger_path(h.root))
    device = identity(h)['device_key']
    first = harvest_store.freeze_overlay(device, 1, g1['id'], 'g1', {'ad_gems': True, 'floating_gems': False})
    assert harvest_store.freeze_overlay(device, 1, g1['id'], 'g1', {'ad_gems': False, 'floating_gems': True}) == first
    store.finish(g1['id'], 'g1', reason='death', outcome='death', evidence={'natural': True})
    _, g2 = _battle(h, run, generation='g2', controls=FULL, gather=NO_OP_GATHER)
    second = harvest_store.freeze_overlay(device, 1, g2['id'], 'g2', {'ad_gems': False, 'floating_gems': True})
    assert harvest_store.freeze_overlay(device, 1, g2['id'], 'g2', {'ad_gems': True, 'floating_gems': False}) == second
    assert list(recorded_decisions(h)) == [ledger_h.freeze_key(g2['id'], 'g2')]


def test_the_generic_bookkeeping_writer_cannot_overwrite_the_overlay_decisions_or_the_hold(h):
    choose(h)
    harvest_store = ledger_h.Harvest(operations.ledger_path(h.root))
    device = identity(h)['device_key']
    for forbidden in ('overlay_freezes', 'transfer'):
        with pytest.raises(ValueError):
            harvest_store.update_runtime(device, 1, **{forbidden: {}})



# ---- a dashboard that starts over a collector from before it says what it found (change 2b)
def found(h, rows=()):
    return harvest.collector_at_start(h.root, h.cfg, processes=list(rows), resolve_source=h.resolve_source, now=NOW)


def test_a_live_collector_from_before_the_start_is_adopted_and_reported(h):
    choose(h)
    op = _collector(h)
    report = found(h)
    assert report == dict(code='adopted', adopted=True, reason='running', operation_id=op['id'], pid=1,
                          started_at=1, found_at=NOW)
    assert h.tick()['action'] == 'none' and h.spawned == []                   # the tick that follows keeps it: no second collector
    assert [o['id'] for o in active_ops(h)] == [op['id']]


def test_no_collector_from_before_the_start_reports_nothing(h):
    assert found(h) is None                                                   # no ledger at all
    assert not operations.ledger_path(h.root).exists(), 'the check must not create the ledger'
    choose(h)
    assert found(h) is None                                                   # a choice and nothing running
    assert found(h, [dict(pid=7, runner='coin', cmdline='', created=3.0)]) is None   # another runner is not a collector


def test_a_collector_record_whose_worker_is_gone_is_reported_dead_and_not_adopted(h):
    choose(h)
    op = _collector(h)
    h.alive['value'] = False
    report = found(h)
    assert (report['code'], report['adopted'], report['reason'], report['operation_id']) == (
        'dead', False, 'worker_not_running', op['id'])
    assert operations.ledger(h.root).get(op['id'])['status'] == 'running', 'reporting must not retire it: the tick does'


def test_a_collector_whose_worker_cannot_be_checked_is_reported_unverified(h):
    choose(h)
    _collector(h)
    h.alive['value'] = None
    report = found(h)
    assert (report['code'], report['adopted'], report['reason']) == ('unverified', False, 'worker_unverified')


def test_a_live_collector_that_the_choice_stops_is_reported_will_stop_not_adopted(h):
    choose(h)
    choose(h, enabled=False)
    op = _collector(h)                                                        # started while harvesting was on, Off recorded since
    report = found(h)
    assert (report['code'], report['adopted'], report['reason'], report['operation_id']) == (
        'will_stop', False, 'harvest_off', op['id'])


def test_a_collector_process_the_ledger_does_not_know_is_reported_untracked(h):
    choose(h)
    row = dict(pid=4711, runner='gem_collector', cmdline='gem_collector.py', created=1790000000.5)
    report = found(h, [row])
    assert report == dict(code='untracked', adopted=False, reason='not_tracked', operation_id=None, pid=4711,
                          started_at=1790000000.5, found_at=NOW)
    tracked = dict(row, operation_id='abc')
    assert found(h, [tracked]) is None                                        # a tracked row is the ledger's business

def test_the_report_is_one_assessment_so_a_changing_liveness_cannot_contradict_it(h):
    choose(h)
    _collector(h)
    answers = iter([True, False, None])
    asked = []
    report = harvest.collector_at_start(h.root, h.cfg, processes=[], resolve_source=h.resolve_source, now=NOW,
                                        alive=lambda worker: asked.append(worker) or next(answers))
    assert (report['code'], report['adopted']) == ('adopted', True) and len(asked) == 1


def test_a_start_that_could_not_read_the_ledger_is_unchecked_not_empty(h):
    choose(h)
    with operations.ledger(h.root)._db() as db:
        db.execute('UPDATE harvest_choices SET body=?', ('not json',))
    with pytest.raises(ValueError):
        found(h)


def test_the_other_reasons_a_live_or_pending_collector_is_not_adopted(h, psutil_fake):
    choose(h)
    op = _collector(h)
    h.source['valid'] = False                                                 # the chosen run can no longer collect
    assert (found(h)['code'], found(h)['reason']) == ('will_stop', 'policy_invalid')
    h.source['valid'] = True
    operations.ledger(h.root).transition(op['id'], op['revision'], 'cancelling', step='stop_requested')
    assert (found(h)['code'], found(h)['reason']) == ('will_stop', 'stopping')


def test_a_collector_accepted_but_not_started_is_reported_unverified_starting(h):
    choose(h)
    h.tick()                                                                  # launches: accepted, no worker yet
    report = found(h)
    assert (report['code'], report['adopted'], report['reason']) == ('unverified', False, 'starting')
