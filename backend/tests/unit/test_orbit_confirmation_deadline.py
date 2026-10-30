"""A dispatched tap keeps bounded proof time without extending input time."""
from test_gem_orbit_cycle import cycle  # noqa: F401 - shared pytest fixture

import pytest
from flows import shard
from runtime.action_session import ActionSession
from towerpilot_workflows.actions import Actions


def late_tap(state, tapper):
    state.balance = None
    tapper.poll(None)
    state.now, state.balance = 159.75, 200
    tapper.poll(200)


def test_last_moment_tap_can_confirm_after_input_window_closes(cycle):
    state, tapper = cycle
    late_tap(state, tapper)
    assert state.taps and tapper.balance_before == 200
    sent = len(state.taps)
    state.now, state.balance = 162, 202
    tapper.poll(202)
    assert len(state.taps) == sent
    assert tapper.last_outcome['confirmed'] is True
    assert any(kind == 'gem_orbit_claim' and data.get('before') == 200
               and data.get('after') == 202 and data.get('confirmed')
               for kind, data in state.events)


def test_confirmation_period_never_dispatches_more_input(cycle, monkeypatch):
    state, tapper = cycle
    late_tap(state, tapper)
    sent = len(state.taps)
    state.now = 161
    monkeypatch.setattr(shard.capture, 'grab', lambda: pytest.fail('unchanged supplied proof needs no capture'))
    tapper.poll(200)
    assert len(state.taps) == sent
    assert tapper.last_outcome is None and tapper.balance_before == 200


def test_confirmation_cutoff_is_frozen_when_an_unstable_pair_waits(cycle):
    state, tapper = cycle
    late_tap(state, tapper)
    state.now, state.balance = 161, 202
    tapper.poll(200)  # Supplied/fresh disagree; one +2 reading is insufficient.
    assert tapper.last_outcome is None
    state.now = 165
    tapper.poll(202)
    assert tapper.last_outcome['confirmed'] is False


@pytest.mark.parametrize('balance', [None, 200, 206])
def test_unknown_unchanged_and_conflicting_counters_never_fabricate_confirmation(cycle, balance):
    state, tapper = cycle
    late_tap(state, tapper)
    sent = len(state.taps)
    state.now, state.balance = 162, balance
    tapper.poll(balance)
    assert len(state.taps) == sent
    assert not any(kind == 'gem_orbit_claim' and data.get('confirmed') for kind, data in state.events)
    state.now = 166
    tapper.poll(balance)
    assert tapper.last_outcome['confirmed'] is False


def test_input_crossing_cutoff_stops_set_and_keeps_accepted_tap_proof(cycle, monkeypatch):
    state, tapper = cycle
    state.balance = None
    tapper.poll(None)
    state.now, state.balance = 159.75, 200
    def dispatch(*args, **kwargs):
        assert state.now < kwargs['deadline']
        state.taps.append(args)
        state.now = 160.1
        return {'dry_run': False}
    monkeypatch.setattr(shard.act, 'tap', dispatch)
    tapper.poll(200)
    assert len(state.taps) == 1 and tapper.sent_tap
    state.now, state.balance = 162, 202
    tapper.poll(202)
    assert len(state.taps) == 1 and tapper.last_outcome['confirmed'] is True


def test_dry_run_does_not_extend_confirmation_deadline(cycle):
    state, tapper = cycle
    state.dry = True
    late_tap(state, tapper)
    state.now, state.balance = 162, 202
    tapper.poll(202)
    assert tapper.last_outcome['confirmed'] is False


def test_timing_edit_does_not_extend_proof_from_an_old_tap(cycle):
    state, tapper = cycle
    late_tap(state, tapper)
    tapper.configure({'tap_interval_sec': 30})
    state.now, state.balance = 165, 202
    tapper.poll(202)
    assert tapper.last_outcome['confirmed'] is False


def test_closed_input_window_uses_supplied_unknown_pixels_after_timing_edit(cycle, monkeypatch):
    state, tapper = cycle
    late_tap(state, tapper)
    tapper.configure({'window_sec': 5})
    state.now, state.balance = 161, None
    monkeypatch.setattr(shard.capture, 'grab', lambda: pytest.fail('unknown supplied confirmation needs no capture'))
    tapper.poll(None)
    assert tapper.last_outcome is None and tapper.balance_before == 200


def test_shorter_input_window_cannot_keep_an_old_tap_until_the_original_cutoff(cycle):
    state, tapper = cycle
    tapper.poll(200)  # Last dispatched tap at100; existing pacing ends at105.
    state.now = 102
    tapper.configure({'window_sec': 5})
    state.now, state.balance = 106, 202
    tapper.poll(202)
    assert tapper.last_outcome['confirmed'] is False


def test_confirmation_keeps_battle_guard_and_does_not_dispatch_input(cycle, monkeypatch):
    state, tapper = cycle
    late_tap(state, tapper)
    sent = len(state.taps)
    state.now, state.balance = 162, 202
    def ownership_guard(frame, expected):
        raise RuntimeError('ownership revoked')
    tapper.guard = ownership_guard
    with pytest.raises(RuntimeError, match='ownership revoked'):
        tapper.poll(202)
    assert len(state.taps) == sent and tapper.last_outcome is None


@pytest.fixture
def recorded_cycle(cycle, tmp_path):
    state, tapper = cycle
    path = tmp_path / 'operations.db'
    session = ActionSession(path, context=dict(device_key='fake-device', account='fake-account',
                            instance='main', serial='fake', profile='fake'), driver='collector')
    tapper.recorder = session.recorder('floating_gems')
    return state, tapper, path


def test_conflict_after_crossing_cutoff_invalidates_proof_and_records_failed_execution(recorded_cycle, monkeypatch):
    state, tapper, path = recorded_cycle
    state.balance = None
    tapper.poll(None)
    state.now, state.balance = 159.75, 200

    def dispatch(*args, **kwargs):
        assert state.now < kwargs['deadline']
        state.taps.append(args)
        state.now, state.balance = 160.1, 206
        return {'dry_run': False}

    monkeypatch.setattr(shard.act, 'tap', dispatch)
    tapper.poll(200)
    assert len(state.taps) == 1
    assert tapper.balance_before is None and not tapper.sent_tap
    state.now, state.balance = 162, 202
    tapper.poll(202)
    assert len(state.taps) == 1
    assert not any(kind == 'gem_orbit_claim' and event.get('confirmed') for kind, event in state.events)
    rows = Actions.read(str(path), device='fake-device')[0]
    assert len(rows) == 1 and rows[0]['state'] == 'failed'
    assert rows[0]['outcome']['effect'] == 'unconfirmed'
    assert rows[0]['outcome']['attempts'] == 1 and rows[0]['outcome']['confirmed'] == 0


def test_late_confirmation_closes_r11d_execution_once_as_succeeded(recorded_cycle):
    state, tapper, path = recorded_cycle
    late_tap(state, tapper)
    sent = len(state.taps)
    state.now, state.balance = 162, 202
    tapper.poll(202)
    assert len(state.taps) == sent
    rows = Actions.read(str(path), device='fake-device')[0]
    assert len(rows) == 1 and rows[0]['state'] == 'succeeded'
    assert rows[0]['outcome']['effect'] == 'confirmed'
    assert rows[0]['outcome']['attempts'] == sent and rows[0]['outcome']['confirmed'] == 1
    assert tapper.recorder.execution_id is None
