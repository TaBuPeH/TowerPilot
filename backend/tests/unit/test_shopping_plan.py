from copy import deepcopy

import pytest

from player.shopping_plan import normalize_plan, decide
from interactions import shopper


def queue_plan(mode='highest_affordable', reserve=50):
    return normalize_plan(dict(version=2, cash_reserve=reserve, queue_mode=mode,
                               queue=[{'id': 'health'}, {'id': 'damage'}, {'id': 'enemy_attack_level_skip'}]))


def quote(price=10, cash=100, quantity=1):
    return dict(price=price, cash=cash, quantity=quantity, total_price_verified=False,
                state='buy', observed_at=100)


def test_global_queue_uses_priority_independent_of_tabs_or_old_cursor():
    value = queue_plan()
    obs = {uid: quote() for uid in ('health', 'damage', 'enemy_attack_level_skip')}
    first = decide(value, 5, obs, {}, 100)
    assert first['upgrade'] == 'health'
    assert first['section'] == 'defense'
    second = decide(value, 5, obs, {'__queue_cursor': 1}, 100)
    assert second['upgrade'] == 'health'
    assert second['section'] == 'defense'
    value['queue_mode'] = 'highest_affordable'
    assert decide(value, 5, obs, {'__queue_cursor': 1}, 100)['upgrade'] == 'health'


def test_reserve_uses_verified_total_and_fail_closed_unknowns():
    value = queue_plan()
    obs = {uid: quote() for uid in ('health', 'damage', 'enemy_attack_level_skip')}
    obs['health'] = quote(price=60)
    assert decide(value, 5, obs, {}, 100)['upgrade'] == 'damage'
    obs['health'] = quote(quantity=None)
    assert decide(value, 5, obs, {}, 100)['reason'] == 'quantity_unverified'
    obs['health']['total_price_verified'] = True
    assert decide(value, 5, obs, {}, 100)['upgrade'] == 'health'
    obs['health']['cash'] = None
    assert decide(value, 5, obs, {}, 100)['reason'] == 'unknown_price_or_cash'
    obs['health'] = quote(price=50)
    assert decide(value, 5, obs, {}, 100)['action'] == 'tap'  # exact reserve equality


def test_queue_executor_requires_quote_evidence_and_retains_counter(monkeypatch):
    monkeypatch.setattr(shopper.detect, 'panel_tab', lambda frame: 'defense')
    monkeypatch.setattr(shopper, '_swipe', lambda **kw: None)
    taps = []
    monkeypatch.setattr(shopper.act, 'tap', lambda *a, **kw: taps.append(a) or {})
    monkeypatch.setattr(shopper, '_purchase_quote', lambda frame, stat:
                        dict(quote(quantity=None), center=(frame, 5), observed_at=shopper.time.monotonic()))
    instance = shopper.Shopper({'shopping_plan': queue_plan()})
    instance.start()
    for frame in range(1, 10):
        instance.step(frame, wave=5, after_intro_sprint=True)
        if not instance.active:
            break
    assert taps == []
    assert instance.status()['decision']['reason'] == 'quantity_unverified'
    monkeypatch.setattr(shopper, '_purchase_quote', lambda frame, stat:
                        dict(quote(), center=(frame, 5), observed_at=shopper.time.monotonic()))
    instance.start()
    for frame in range(20, 30):
        instance.step(frame, wave=5, after_intro_sprint=True)
        if not instance.active:
            break
    assert taps == [(23, 5)]
    assert instance.attempts['queue/health'] == 1
    edited = queue_plan()
    edited['queue'][0]['tap_limit'] = 1
    instance.update_plan(edited)
    assert instance.attempts['queue/health'] == 1


@pytest.mark.parametrize('reserve', [True, -1, float('inf'), float('nan')])
def test_queue_rejects_invalid_reserve(reserve):
    with pytest.raises(ValueError, match='cash_reserve'):
        queue_plan(reserve=reserve)


def test_queue_default_reserve_requires_configuration():
    from player.shopping_plan import default_plan
    value = default_plan()
    assert value['cash_reserve'] is None
    assert value['queue_mode'] == 'highest_affordable'
    assert decide(value, 5, {}, {}, 100)['reason'] == 'reserve_unconfigured'


def test_round_robin_rotates_global_queue_but_single_focus_does_not():
    value = queue_plan(mode='round_robin')
    obs = {uid: quote() for uid in ('health', 'damage', 'enemy_attack_level_skip')}
    assert decide(value, 5, obs, {'__queue_cursor': 1}, 100)['upgrade'] == 'damage'
    value['queue_mode'] = 'highest_affordable'
    assert decide(value, 5, obs, {'__queue_cursor': 1}, 100)['upgrade'] == 'health'


def test_rejects_removed_phases_schema():
    with pytest.raises(ValueError, match='unsupported fields'):
        normalize_plan({'version': 2, 'phases': []})


def test_queue_wave_and_duplicate_validation():
    value = queue_plan()
    value['queue'][0]['until_wave'] = 1
    with pytest.raises(ValueError, match='until_wave'):
        normalize_plan(value)
    value = queue_plan()
    value['queue'].append(deepcopy(value['queue'][0]))
    with pytest.raises(ValueError, match='duplicate'):
        normalize_plan(value)


def test_pending_manual_abort_preserves_attempts():
    instance = shopper.Shopper({'shopping_plan': queue_plan()})
    instance.attempts['queue/damage'] = 2
    assert instance.request_once(upgrade='damage')
    instance.abort()
    assert instance.status()['manual']['state'] == 'aborted'
    assert instance.attempts['queue/damage'] == 2


def test_no_actions_without_sprint_evidence(monkeypatch):
    monkeypatch.setattr(shopper.act, 'tap', lambda *a, **k: pytest.fail('unsafe tap'))
    instance = shopper.Shopper({'shopping_plan': queue_plan()})
    instance.start()
    instance.step(1, wave=5)
    assert instance.status()['decision']['reason'] == 'intro_sprint'
    assert not instance.active


def test_manual_bypasses_enabled_but_not_attempt_limit_or_exclusion():
    value = queue_plan()
    value['enabled'] = False
    obs = {uid: quote() for uid in ('health', 'damage', 'enemy_attack_level_skip')}
    assert decide(value, 5, obs, {}, 100)['reason'] == 'disabled'
    assert decide(value, 5, obs, {}, 100, manual=True)['action'] == 'tap'
    value['queue'][0]['tap_limit'] = 1
    assert decide(value, 5, obs, {'queue/health': 1}, 100, manual=True)['upgrade'] == 'damage'
    value['excluded_upgrades'] = ['health', 'damage', 'enemy_attack_level_skip']
    assert decide(value, 5, obs, {}, 100, manual=True)['action'] == 'wait'


@pytest.mark.parametrize('token,expected', [('x1', 1), ('1x', 1), ('X1', 1), ('\u00d71', 1),
                                           ('x10', 10), ('x100', 100), ('XIO', None),
                                           ('XI', None), ('max', None), ('', None), ('x1 extra', None)])
def test_quantity_parser_never_repairs_or_prefix_matches(token, expected):
    assert shopper._quantity_token(token) == expected


def test_quantity_observer_requires_two_full_agreeing_readings(monkeypatch):
    import numpy as np
    from vision import textocr
    monkeypatch.setattr(shopper.detect, 'panel_tab', lambda frame: 'utility')
    monkeypatch.setattr(shopper.capture, 'roi', lambda frame, name: frame)
    frame = np.zeros((660, 1080, 3), np.uint8)
    calls = []
    def read(header, *, scale, timeout):
        calls.append((header.shape, timeout))
        return [(0, 0, 'x1')]
    monkeypatch.setattr(textocr, 'read_lines', read)
    assert shopper._read_purchase_quantity(frame) == 1
    assert calls == [((55, 270, 3), .75), ((55, 270, 3), .75)]
    monkeypatch.setattr(textocr, 'read_lines', lambda *a, scale, **kw: [(0, 0, 'x1' if scale == 2 else 'x10')])
    assert shopper._read_purchase_quantity(frame) is None
    monkeypatch.setattr(textocr, 'read_lines', lambda *a, **kw: [(0, 0, 'XIO')])
    assert shopper._read_purchase_quantity(frame) is None


def test_actual_observation_boundary_reads_same_frame_cash_and_quantity(monkeypatch):
    monkeypatch.setattr(shopper, '_find_stat', lambda frame, stat: ((5, 7), 'buy', 20))
    monkeypatch.setattr(shopper, 'read_cash', lambda frame: 60 if frame == 'current' else 100)
    monkeypatch.setattr(shopper, '_read_purchase_quantity', lambda frame: 1 if frame == 'current' else 10)
    value = queue_plan(reserve=50)
    obs = {'health': shopper._purchase_quote('current', 'health')}
    decision = decide(value, 5, obs, {}, shopper.time.monotonic())
    assert decision['action'] == 'scan'  # health fails reserve; next queued target needs observation
    assert decision['upgrade'] == 'damage'
    obs['health'] = shopper._purchase_quote('old', 'health')
    assert decide(value, 5, obs, {}, shopper.time.monotonic())['reason'] == 'quantity_unverified'


def test_textocr_timeout_forwarded_to_hidden_process(monkeypatch, tmp_path):
    import numpy as np
    import subprocess
    from vision import textocr
    timeouts = []
    def run(*args, **kwargs):
        timeouts.append(kwargs['timeout'])
        raise subprocess.TimeoutExpired('ocr', kwargs['timeout'])
    monkeypatch.setattr(textocr.settings, 'run_hidden', run)
    assert textocr.read_lines(np.zeros((10, 10, 3), np.uint8), timeout=.75) == []
    assert timeouts == [.75]


def test_weighted_split_batches_and_affordability_skip_without_debt():
    from player.shopping_plan import record_confirmed_purchase
    value = normalize_plan(dict(version=2, cash_reserve=50, queue_mode='weighted_split',
                                queue=[{'id': 'damage', 'weight': 3}, {'id': 'health', 'weight': 2}]))
    obs = {'damage': quote(), 'health': quote()}
    counts = {}
    sequence = []
    for _ in range(7):
        choice = decide(value, 5, obs, counts, 100)
        sequence.append(choice['upgrade'])
        record_confirmed_purchase(value, counts, choice['upgrade'])
    assert sequence == ['damage', 'damage', 'damage', 'health', 'health', 'damage', 'damage']
    obs['damage'] = quote(price=60)
    assert decide(value, 5, obs, counts, 100)['upgrade'] == 'health'
    record_confirmed_purchase(value, counts, 'health')
    record_confirmed_purchase(value, counts, 'health')
    obs['damage'] = quote()
    assert decide(value, 5, obs, counts, 100)['upgrade'] == 'damage'
    record_confirmed_purchase(value, counts, 'damage')
    assert counts['__weighted_used'] == 1  # prior skipped damage remainder was dropped


@pytest.mark.parametrize('weight', [0, 101, True, 1.5])
def test_weight_validation(weight):
    with pytest.raises(ValueError, match='weight'):
        normalize_plan(dict(version=2, queue=[{'id': 'damage', 'weight': weight}]))


def test_weighted_executor_confirms_two_indicators_before_advancing(monkeypatch):
    value = normalize_plan(dict(version=2, cash_reserve=50, queue_mode='weighted_split',
                                queue=[{'id': 'health', 'weight': 2}]))
    monkeypatch.setattr(shopper.detect, 'panel_tab', lambda frame: 'defense')
    monkeypatch.setattr(shopper, '_swipe', lambda **kw: None)
    taps = []
    monkeypatch.setattr(shopper.act, 'tap', lambda *a, **kw: taps.append(a) or {})
    base_time = shopper.time.monotonic() - 1
    def observed(frame, stat):
        return dict(quote(price=10 if frame <= 4 else 11, cash=100 if frame <= 4 else 92),
                    center=(frame, 5), observed_at=base_time + frame * .01)
    monkeypatch.setattr(shopper, '_purchase_quote', observed)
    instance = shopper.Shopper({'shopping_plan': value})
    instance.start()
    for frame in range(1, 10):
        instance.step(frame, wave=5, after_intro_sprint=True)
        if not instance.active:
            break
    assert len(taps) == 1
    assert instance.attempts['queue/health'] == 1
    assert instance.attempts.get('confirmed/health') == 1, instance.status()
    assert instance.attempts['__weighted_used'] == 1
    assert instance.status()['pending_confirmation'] is None


def test_weighted_unknown_confirmation_holds_without_repeating_tap(monkeypatch):
    value = normalize_plan(dict(version=2, cash_reserve=50, queue_mode='weighted_split',
                                queue=[{'id': 'health', 'weight': 2}]))
    monkeypatch.setattr(shopper.detect, 'panel_tab', lambda frame: 'defense')
    monkeypatch.setattr(shopper, '_swipe', lambda **kw: None)
    taps = []
    monkeypatch.setattr(shopper.act, 'tap', lambda *a, **kw: taps.append(a) or {})
    monkeypatch.setattr(shopper, '_purchase_quote', lambda frame, stat:
                        dict(quote(), center=(frame, 5), observed_at=shopper.time.monotonic()))
    instance = shopper.Shopper({'shopping_plan': value})
    for start in (1, 20):
        instance.start()
        for frame in range(start, start+10):
            instance.step(frame, wave=5, after_intro_sprint=True)
            if not instance.active:
                break
    assert len(taps) == 1
    assert instance.attempts['queue/health'] == 1
    assert instance.attempts.get('confirmed/health', 0) == 0
    assert instance.status()['decision']['reason'] == 'purchase_confirmation_unknown'
    instance.update_plan(value)
    assert instance.status()['pending_confirmation'] is not None


@pytest.mark.parametrize('price,cash,quantity', [(10, 90, 1), (11, 100, 1), (11, 80, 1), (11, 92, 10)])
def test_confirmation_rejects_missing_or_inconsistent_indicators(price, cash, quantity):
    before = dict(quote(), observed_at=1)
    after = dict(quote(price=price, cash=cash, quantity=quantity), observed_at=2)
    assert not shopper._confirmed_purchase(before, after)


def test_confirmation_rejects_evidence_after_bounded_window():
    before = dict(quote(), observed_at=1)
    after = dict(quote(price=11, cash=92), observed_at=17)
    assert not shopper._confirmed_purchase(before, after)


def test_weight_edit_retains_confirmed_progress_and_reduced_quota_moves_on():
    from player.shopping_plan import record_confirmed_purchase
    value = normalize_plan(dict(version=2, cash_reserve=50, queue_mode='weighted_split',
                                queue=[{'id': 'damage', 'weight': 3}, {'id': 'health', 'weight': 2}]))
    counts = {}
    record_confirmed_purchase(value, counts, 'damage')
    record_confirmed_purchase(value, counts, 'damage')
    value['queue'][0]['weight'] = 1
    obs = {'damage': quote(), 'health': quote()}
    assert decide(value, 5, obs, counts, 100)['upgrade'] == 'health'
    assert counts['confirmed/damage'] == 2


def test_unknown_confirmation_expires_without_quota_and_next_pass_can_attempt(monkeypatch):
    value = normalize_plan(dict(version=2, cash_reserve=50, queue_mode='weighted_split',
                                queue=[{'id': 'health', 'weight': 2}]))
    clock = [100.0]
    monkeypatch.setattr(shopper.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(shopper.detect, 'panel_tab', lambda frame: 'defense')
    monkeypatch.setattr(shopper, '_swipe', lambda **kw: None)
    taps = []
    monkeypatch.setattr(shopper.act, 'tap', lambda *a, **kw: taps.append(a) or {})
    monkeypatch.setattr(shopper, '_purchase_quote', lambda frame, stat:
                        dict(quote(), center=(frame, 5), observed_at=clock[0]))
    instance = shopper.Shopper({'shopping_plan': value})
    def run_pass():
        instance.start()
        for frame in range(1, 12):
            instance.step(frame, wave=5, after_intro_sprint=True)
            if not instance.active:
                break
    run_pass()
    assert instance.status()['decision']['reason'] == 'purchase_confirmation_unknown'
    assert len(taps) == 1
    clock[0] = 116
    run_pass()
    assert len(taps) == 1  # Expiration itself never grants an immediate retry.
    assert instance.status()['pending_confirmation'] is None
    assert instance.status()['last_confirmation']['outcome'] == 'terminal_unconfirmed'
    assert instance.status()['decision']['reason'] == 'purchase_confirmation_expired'
    assert instance.attempts['queue/health'] == 1
    assert instance.attempts.get('confirmed/health', 0) == 0
    clock[0] = 117
    run_pass()
    assert len(taps) == 2
    assert instance.attempts['queue/health'] == 2
    assert instance.attempts.get('confirmed/health', 0) == 0


def test_conversion_preserves_legacy_recurring_clicks_and_cadence():
    from player.shopping_plan import convert_legacy
    draft = convert_legacy([
        {'tab': 'attack', 'stats': ['damage'], 'mode': 'clicks', 'clicks': 3},
        {'tab': 'defense', 'stats': ['health'], 'mode': 'once'},
    ], interval_sec=120)
    value = draft['plan']
    assert value['interval_sec'] == 120
    assert value['queue_mode'] == 'weighted_split'
    assert value['queue'][0]['weight'] == 3
    assert value['queue'][0]['tap_limit'] is None
    assert value['queue'][1]['tap_limit'] == 1
    assert value['cash_reserve'] is None
    assert draft['requires_review'] is True
    assert any('per sweep' in warning for warning in draft['warnings'])
    assert convert_legacy([])['plan']['interval_sec'] == 90
