"""Sweep lifecycle evidence must include early exits without leaking across yields."""
import json

import pytest

from interactions import shopper
from player.shopping_plan import normalize_plan
from runtime import logger


def rows():
    return [json.loads(line) for path in logger._LOG_DIR.rglob('events_*.jsonl')
            for line in path.read_text().splitlines()]


@pytest.fixture
def sweep(monkeypatch):
    monkeypatch.setattr(shopper, '_TOP_RESETS', 0)
    monkeypatch.setattr(shopper.detect, 'panel_tab', lambda frame: 'defense')
    monkeypatch.setattr(shopper, '_swipe', lambda **kwargs: None)
    monkeypatch.setattr(shopper.act, 'tap', lambda *args, **kwargs: {'dry_run': False})
    monkeypatch.setattr(shopper, '_purchase_quote', lambda frame, stat:
                        dict(center=(10, 20), state='buy', price=10, cash=100, quantity=None,
                             total_price_verified=False, observed_at=shopper.time.monotonic()))
    return shopper.Shopper({'shopping_plan': normalize_plan(dict(version=2, cash_reserve=50, queue=[{'id': 'health'}]))})


def test_early_exit_has_terminal_reason_and_correlation_without_leaking(sweep):
    with logger.context(trace_id='scheduled', request_id='origin'):
        sweep.start()
    assert 'sweep_id' not in logger.current_context()
    with logger.context(trace_id='other-observer'):
        sweep.step('frame', wave=20, after_intro_sprint=True)
        logger.event('unrelated_monitor')
    start, end, unrelated = rows()
    assert start['kind'] == 'shop_sweep_start'
    assert end['kind'] == 'shop_sweep_end'
    assert start['sweep_id'] == end['sweep_id'] == sweep.status()['sweep_id']
    assert end['trace_id'] == 'scheduled' and end['request_id'] == 'origin'
    assert end['reason'] == 'quantity_unverified'
    assert end['outcome'] == 'skipped'
    assert end['purchase_taps'] == {'attempts': 0, 'dispatched': 0, 'dry_run': 0}
    assert end['section'] == 'defense' and end['upgrade'] == 'health'
    assert end['wave'] == 20 and end['duration_ms'] >= 0
    assert 'sweep_id' not in unrelated and unrelated['trace_id'] == 'other-observer'


def test_abort_before_first_frame_still_has_one_terminal_summary(sweep):
    sweep.start()
    sweep.abort()
    sweep.abort()
    ends = [row for row in rows() if row['kind'] == 'shop_sweep_end']
    assert len(ends) == 1
    assert ends[0]['outcome'] == 'aborted'
    assert ends[0]['purchase_taps']['attempts'] == 0
    assert 'sweep_id' not in logger.current_context()


def test_failure_after_previous_dispatched_sweep_never_reuses_counts(sweep, monkeypatch):
    monkeypatch.setattr(shopper, '_purchase_quote', lambda frame, stat:
                        dict(center=(10, 20), state='buy', price=10, cash=100, quantity=1,
                             total_price_verified=False, observed_at=shopper.time.monotonic()))
    sweep.start()
    sweep.step('frame', wave=20, after_intro_sprint=True)
    assert sweep.status()['last_sweep']['purchase_taps']['dispatched'] == 1
    def fail(*args):
        raise RuntimeError('quote observation failed')
    monkeypatch.setattr(shopper, '_purchase_quote', fail)
    sweep.start()
    with pytest.raises(RuntimeError, match='quote observation failed'):
        sweep.step('frame', wave=20, after_intro_sprint=True)
    sweep.abort()
    ends = [row for row in rows() if row['kind'] == 'shop_sweep_end']
    assert len(ends) == 2
    assert ends[0]['outcome'] == 'unconfirmed'
    assert ends[1]['outcome'] == 'error'
    assert ends[1]['sweep_id'] != ends[0]['sweep_id']
    assert ends[1]['purchase_taps']['attempts'] == 0
    assert ends[1]['exception_type'] == 'RuntimeError'
    assert ends[1]['exception_message'] == 'quote observation failed'
    assert 'raise RuntimeError' in ends[1]['traceback']


def test_manual_tap_refusal_has_terminal_exception_evidence(sweep, monkeypatch):
    monkeypatch.setattr(shopper, '_purchase_quote', lambda frame, stat:
                        dict(center=(10, 20), state='buy', price=10, cash=100, quantity=1,
                             total_price_verified=False, observed_at=shopper.time.monotonic()))
    def refuse(*args, **kwargs):
        raise shopper.act.TapRefused('lease not owned')
    monkeypatch.setattr(shopper.act, 'tap', refuse)
    assert sweep.request_once(section='defense', upgrade='health')
    sweep.step('frame', wave=20, after_intro_sprint=True)
    end = next(row for row in rows() if row['kind'] == 'shop_sweep_end')
    assert end['manual'] is True
    assert end['outcome'] == 'refused' and end['reason'] == 'tap_refused'
    assert end['purchase_taps']['attempts'] == 1 and end['purchase_taps']['dispatched'] == 0
    assert end['exception_type'] == 'TapRefused'
    assert 'lease not owned' in end['traceback']


@pytest.mark.parametrize('first_dispatch', [False, True])
@pytest.mark.parametrize('transport_failure', [False, True])
def test_legacy_refused_or_failed_burst_has_accurate_terminal_summary(monkeypatch, first_dispatch, transport_failure):
    import numpy as np
    monkeypatch.setattr(shopper, '_TOP_RESETS', 0)
    monkeypatch.setattr(shopper.detect, 'panel_tab', lambda frame: 'defense')
    monkeypatch.setattr(shopper, '_panel_gray', lambda frame: np.zeros((2, 2)))
    monkeypatch.setattr(shopper, 'read_cash', lambda frame: 100)
    monkeypatch.setattr(shopper, '_find_stat', lambda frame, stat: ((10, 20), 'buy', 10))
    monkeypatch.setattr(shopper.time, 'sleep', lambda seconds: None)
    calls = []
    def tap(*args, **kwargs):
        calls.append(args)
        if first_dispatch and len(calls) == 1:
            return {'dry_run': False}
        if transport_failure:
            raise shopper.act.TapRefused('transport failed') from OSError('connection closed')
        raise shopper.act.TapRefused('lease not owned')
    monkeypatch.setattr(shopper.act, 'tap', tap)
    instance = shopper.Shopper({'shopping': [{'tab': 'defense', 'stats': ['health'],
                                             'mode': 'clicks', 'clicks': 2}]})
    instance.start()
    for _ in range(5):
        instance.step('frame')
        if not instance.active:
            break
    assert not instance.active
    # The existing legacy generator resumes its partially dispatched chunk;
    # diagnostics must preserve those calls and report both refusals.
    assert len(calls) == (3 if first_dispatch else 1)
    end, = [row for row in rows() if row['kind'] == 'shop_sweep_end']
    failure = 'failed' if transport_failure else 'refused'
    assert end['outcome'] == ('unconfirmed' if first_dispatch else failure)
    assert end['reason'] == ('tap_failed' if transport_failure else 'tap_refused')
    assert end['purchase_taps']['attempts'] == len(calls)
    assert end['purchase_taps']['dispatched'] == int(first_dispatch)
    assert end['purchase_taps'][failure] == (2 if first_dispatch else 1)
    assert end['section'] == 'defense' and end['upgrade'] == 'health'
    assert end['exception_type'] == 'TapRefused'
    assert ('connection closed' if transport_failure else 'lease not owned') in end['traceback']
    assert instance.status()['confirmed_purchases'] is None
