"""Versioned text catalog and pure, deterministic tap-attempt planning.

Catalog sequence is provisional: the repository establishes section membership for
its shipped policy selections, not authoritative full game row/column positions.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

_SECTIONS = {
    'attack': ('damage', 'attack_speed', 'critical_chance', 'critical_factor', 'range',
               'damage_per_meter', 'multishot_chance', 'multishot_targets',
               'rapid_fire_chance', 'rapid_fire_duration', 'bounce_shot_chance',
               'bounce_shot_targets', 'bounce_shot_range', 'super_crit_chance',
               'super_crit_mult', 'rend_armor_chance', 'rend_armor_mult'),
    'defense': ('health', 'health_regen', 'defense_percent', 'defense_absolute',
                'thorn_damage', 'lifesteal', 'knockback_chance', 'knockback_force',
                'orb_speed', 'orbs', 'shockwave_size', 'shockwave_frequency',
                'land_mine_chance', 'land_mine_damage', 'land_mine_radius',
                'death_defy', 'wall_health', 'wall_rebuild'),
    'utility': ('cash_bonus', 'cash_per_wave', 'coins_per_kill_bonus', 'coins_per_wave',
                'free_attack_upgrade', 'free_defense_upgrade', 'free_utility_upgrade',
                'interest_per_wave', 'recovery_package_chance', 'recovery_amount',
                'max_recovery', 'enemy_attack_level_skip', 'enemy_health_level_skip'),
}


def catalog() -> list[dict]:
    """Known selections; recognition support is distinct from account learning."""
    return [dict(id=stat, label=stat.replace('_', ' ').title(), section=section,
                 tab=section, order=index, game_order=None, order_verified=False,
                 order_provenance='provisional; requires account UI verification',
                 recognition='template', availability='account_observation_required',
                 excluded_by_default=stat == 'defense_absolute',
                 exclusion_reason='Legacy policy excludes an endless cash sink' if stat == 'defense_absolute' else None)
            for section, stats in _SECTIONS.items() for index, stat in enumerate(stats)]


def default_plan() -> dict:
    return normalize_plan({'version': 2, 'queue': []})


def _integer(value, path: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f'{path}: expected integer >= {minimum}')
    return value


def _boolean(value, path: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f'{path}: expected boolean')
    return value


def _range(value: Mapping, path: str, *, inherited: bool = False) -> tuple:
    start = value.get('from_wave', None if inherited else 1)
    end = value.get('until_wave')
    if start is None and not inherited:
        raise ValueError(f'{path}.from_wave: expected positive integer')
    if start is not None:
        _integer(start, f'{path}.from_wave', 1)
    if end is not None:
        _integer(end, f'{path}.until_wave', 2)
    if end is not None and start is not None and end <= start:
        raise ValueError(f'{path}: empty wave range')
    return start, end


def normalize_plan(value: dict) -> dict:
    """Validate strictly; quantities are attempts, never confirmed upgrades."""
    if not isinstance(value, dict) or value.get('version') != 2:
        raise ValueError('shopping plan requires version 2')
    return normalize_queue(value)


def _inside(value: dict, wave: int) -> bool:
    return wave >= (value['from_wave'] or 1) and (value['until_wave'] is None or wave < value['until_wave'])


def decide(plan: dict, wave: int | None, observations: dict, counters: dict, now: float, *, section: str | None = None, upgrade: str | None = None, max_age: float = 15, manual: bool = False) -> dict:
    """Choose one attempt or explain missing evidence without device I/O."""
    return decide_queue(plan, wave, observations, counters, now, section=section,
                        upgrade=upgrade, max_age=max_age, manual=manual)


def normalize_queue(value: dict) -> dict:
    allowed = {'version', 'enabled', 'interval_sec', 'cash_reserve', 'queue_mode', 'queue',
               'excluded_upgrades', 'cadence'}
    if set(value) - allowed:
        raise ValueError('queue plan: unsupported fields')
    interval, reserve = value.get('interval_sec', 5), value.get('cash_reserve')
    for number, name in ((interval, 'interval_sec'), (reserve, 'cash_reserve')):
        if name == 'cash_reserve' and number is None:
            continue
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number < 0:
            raise ValueError(f'{name}: expected nonnegative finite number')
    if not 1 <= interval <= 86400:
        raise ValueError('interval_sec: expected 1 to 86400')
    mode = value.get('queue_mode', 'highest_affordable')
    if mode not in ('highest_affordable', 'round_robin', 'weighted_split'):
        raise ValueError('queue_mode: unsupported')
    cadence = value.get('cadence', 'single_attempt')
    if cadence not in ('single_attempt', 'while_affordable'):
        raise ValueError('cadence: unsupported')
    known = {c['id'] for c in catalog()}
    excluded = value.get('excluded_upgrades', ['defense_absolute'])
    if not isinstance(excluded, list) or any(x not in known for x in excluded):
        raise ValueError('excluded_upgrades: unknown upgrade')
    rows = value.get('queue')
    if not isinstance(rows, list):
        raise ValueError('queue: expected list')
    queue, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or set(row) - {'id', 'enabled', 'tap_limit', 'from_wave', 'until_wave', 'weight'}:
            raise ValueError('queue entry: unsupported fields')
        uid = row.get('id')
        if uid not in known or uid in seen:
            raise ValueError('queue entry: unknown or duplicate upgrade')
        seen.add(uid)
        start, end = _range(row, uid)
        limit = row.get('tap_limit')
        if limit is not None:
            _integer(limit, 'tap_limit')
        weight = _integer(row.get('weight', 1), 'weight', 1)
        if weight > 100:
            raise ValueError('weight: expected integer from 1 to 100')
        queue.append(dict(id=uid, enabled=_boolean(row.get('enabled', True), uid), tap_limit=limit, from_wave=start, until_wave=end, weight=weight))
    return dict(version=2, enabled=_boolean(value.get('enabled', True), 'enabled'), interval_sec=interval,
                cash_reserve=reserve, queue_mode=mode, cadence=cadence,
                queue=queue, excluded_upgrades=list(excluded))


def queue_candidates(plan: dict, wave: int | None, counters: dict, *, section=None, upgrade=None) -> list[dict]:
    if type(wave) is not int or wave < 1:
        return []
    tabs = {c['id']: c['section'] for c in catalog()}
    queue = plan['queue']
    indexed = list(enumerate(queue))
    if plan['queue_mode'] in ('round_robin', 'weighted_split'):
        cursor_key = '__weighted_index' if plan['queue_mode'] == 'weighted_split' else '__queue_cursor'
        cursor = counters.get(cursor_key, 0) % max(1, len(queue))
        if queue and plan['queue_mode'] == 'weighted_split' and counters.get('__weighted_used', 0) >= queue[cursor]['weight']:
            cursor = (cursor + 1) % len(queue)
        indexed = indexed[cursor:] + indexed[:cursor]
    return [dict(row, section=tabs[row['id']], index=index) for index, row in indexed
            if row['enabled'] and _inside(row, wave) and row['id'] not in plan['excluded_upgrades']
            and (row['tap_limit'] is None or counters.get('queue/' + row['id'], 0) < row['tap_limit'])
            and (section is None or tabs[row['id']] == section) and (upgrade is None or row['id'] == upgrade)]


def decide_queue(plan: dict, wave, observations: dict, counters: dict, now: float, *, section=None, upgrade=None, max_age=15, manual=False) -> dict:
    if not plan['enabled'] and not manual:
        return dict(action='wait', reason='disabled')
    if type(wave) is not int or wave < 1:
        return dict(action='wait', reason='unknown_wave')
    if plan['cash_reserve'] is None:
        return dict(action='wait', reason='reserve_unconfigured')
    for row in queue_candidates(plan, wave, counters, section=section, upgrade=upgrade):
        uid = row['id']
        base = dict(section=row['section'], upgrade=uid)
        obs = observations.get(uid)
        if obs is None or not 0 <= now - obs['observed_at'] <= max_age:
            return dict(action='scan', reason='missing_or_stale_observation', **base)
        if obs['state'] == 'maxed':
            continue
        if obs['state'] != 'buy':
            continue
        if (not (type(obs.get('quantity')) is int and obs['quantity'] == 1)
                and (plan.get('cadence') == 'while_affordable' or obs.get('total_price_verified') is not True)):
            return dict(action='wait', reason='quantity_unverified', **base)
        price, cash = obs.get('price'), obs.get('cash')
        if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in (price, cash)) or price <= 0:
            return dict(action='wait', reason='unknown_price_or_cash', **base)
        if cash - price < plan['cash_reserve']:
            continue
        return dict(action='tap', reason=plan['queue_mode'], phase='queue', index=row['index'], attempts=1, **base)
    return dict(action='wait', reason='reserve_or_no_eligible_upgrades')


def record_confirmed_purchase(plan: dict, counters: dict, upgrade: str) -> None:
    """Advance weighted quota only after independently confirmed purchase evidence.

    Attempts live in queue/<id>; they never stand in for confirmed quota. Moving
    to another affordable item starts its own batch and drops a skipped batch's
    remainder, so temporary unaffordability cannot accumulate debt.
    """
    if plan['queue_mode'] != 'weighted_split':
        return
    for index, row in enumerate(plan['queue']):
        if row['id'] != upgrade:
            continue
        current = counters.get('__weighted_index', 0) % max(1, len(plan['queue']))
        used = counters.get('__weighted_used', 0) + 1 if current == index else 1
        counters['confirmed/' + upgrade] = counters.get('confirmed/' + upgrade, 0) + 1
        if used >= row['weight']:
            counters['__weighted_index'] = (index + 1) % len(plan['queue'])
            counters['__weighted_used'] = 0
        else:
            counters['__weighted_index'] = index
            counters['__weighted_used'] = used
        return


def convert_legacy(directives: list[dict], *, interval_sec: float = 90) -> dict:
    """A reviewable global-queue draft, never silently installed."""
    queue, warnings, seen = [], [], set()
    weighted = False
    for directive in directives:
        for uid in directive['stats']:
            if uid in seen:
                warnings.append(f'duplicate selection: {uid}')
                continue
            seen.add(uid)
            mode = directive['mode']
            limit = 1 if mode == 'once' else None
            weight = directive.get('clicks', 1) if mode == 'clicks' else 1
            weighted = weighted or mode == 'clicks'
            queue.append(dict(id=uid, enabled=directive.get('enabled', True), tap_limit=limit, weight=weight))
        if directive['mode'] == 'best_cost':
            warnings.append('best_cost becomes global queue ordering; review priority')
        if directive['mode'] == 'clicks':
            warnings.append('Legacy clicks N repeats attempted taps per sweep; draft uses N confirmed 1x purchases per weighted turn with no lifetime tap cap. Review this behavior change.')
    warnings += ['Review cash reserve before applying', 'Defense Absolute explicitly excluded by default',
                 'Legacy shopping has no cash reserve protection; conversion changes execution semantics',
                 'Queue order follows legacy selection order; legacy runtime did not guarantee it']
    return dict(plan=normalize_plan(dict(version=2, queue=queue, interval_sec=interval_sec,
                                         queue_mode='weighted_split' if weighted else 'highest_affordable')),
                warnings=warnings, requires_review=True)
