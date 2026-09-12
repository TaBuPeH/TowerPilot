"""Fleet spawn timetable per tier - what the fleet-mark Nuke, the Chain
Lightning fleet choreography, the danger-window shots and the shard loop
key off. ONE table, picked by the run's tier; nothing else in the tree may
hardcode a fleet wave.

Source: Tower Hub "Fleet Enemies" (numbers cross-checked by them against
decoded save data and the community Fandom wiki, last edited 2026-07-05),
confirmed live on Tier 14 - fleets at 2495, 3495, 4495, 5495 (fleet_nuke
events, 2026-08-19).

  Tiers 1-13   first fleet at 15000 - 250 * (tier - 1), then every 100 waves
  Tier 14      2495 / 1000        Tier 15   1495 / 750     Tier 16   995 / 500
  Tier 17       495 / 250         Tier 18     95 / 100     Tier 19    45 / 50
  Tier 20+     wave 5, then every 10 (1 fleet on T20, 2 on T21, 3 on T22+)
  Bonus spawns on Tier 14+: the low-tier high-wave pattern carried over -
  first at 11750 - 250 * (tier - 14), then every 100. They drop nothing
  while a regular fleet is up, but they hit the wall like any fleet, so they
  are marks too.
  Legends-league tournaments (V27.3+): 895 then every 30. Not a tier, so it
  is exposed as LEGENDS and never auto-selected - tournament presets carry
  nuke_on_fleet: null and their Chain Lightning mode is off_until_wave.

Config may override a tier when the game changes (`fleet.by_tier`), and the
legacy single schedule (`fleet.first_wave` / `fleet.interval`) still applies
to a run that names no tier at all.
"""
from functools import lru_cache

# Marks are enumerated up to this wave. The deepest farm runs seen end well
# under 10000; the bonus series on T14+ starts around 10000-11750, so this
# keeps them in range without an unbounded list.
MAX_WAVE = 30000

LEGENDS = {"first_wave": 895, "interval": 30, "fleets_per_spawn": 1,
           "bonus_first_wave": None, "bonus_interval": None}

_BATTLE_CONDITION = {          # tier: (first_wave, interval, fleets per spawn)
    14: (2495, 1000, 1),
    15: (1495, 750, 1),
    16: (995, 500, 1),
    17: (495, 250, 1),
    18: (95, 100, 1),
    19: (45, 50, 1),
    20: (5, 10, 1),
    21: (5, 10, 2),
}
_BONUS_INTERVAL = 100


def _table_row(tier: int) -> dict | None:
    if tier < 1:
        return None
    if tier <= 13:
        return {"first_wave": 15000 - 250 * (tier - 1), "interval": 100,
                "fleets_per_spawn": 1, "bonus_first_wave": None, "bonus_interval": None}
    first, interval, per = _BATTLE_CONDITION.get(tier, (5, 10, 3))   # T22+ = 3 fleets
    return {"first_wave": first, "interval": interval, "fleets_per_spawn": per,
            "bonus_first_wave": 11750 - 250 * (tier - 14), "bonus_interval": _BONUS_INTERVAL}


def _override(tier: int, cfg) -> dict | None:
    by_tier = ((cfg or {}).get("fleet") or {}).get("by_tier") or {}
    row = by_tier.get(tier, by_tier.get(str(tier)))
    return dict(row) if isinstance(row, dict) else None


def schedule(tier, cfg=None) -> dict | None:
    """The spawn row a run on `tier` lives under, or None when nothing
    applies: keys first_wave, interval, fleets_per_spawn, bonus_first_wave,
    bonus_interval, tier, source ('config', 'table' or 'legacy')."""
    fleet_cfg = (cfg or {}).get("fleet") or {}
    if tier is not None:
        tier = int(tier)
        row = _override(tier, cfg)
        if row is not None:
            base = _table_row(tier) or {}
            merged = {**LEGENDS, **base, **row}
            return dict(merged, tier=tier, source="config")
        row = _table_row(tier)
        if row is not None:
            return dict(row, tier=tier, source="table")
    if fleet_cfg.get("first_wave") is not None and fleet_cfg.get("interval"):
        return {"first_wave": int(fleet_cfg["first_wave"]), "interval": int(fleet_cfg["interval"]),
                "fleets_per_spawn": 1, "bonus_first_wave": None, "bonus_interval": None,
                "tier": tier, "source": "legacy"}
    return None


def first_wave(tier, cfg=None) -> int | None:
    row = schedule(tier, cfg)
    return None if row is None else int(row["first_wave"])


@lru_cache(maxsize=64)
def _marks(first, interval, bonus_first, bonus_interval, max_wave) -> tuple[int, ...]:
    out = set()
    if first is not None and interval:
        out.update(range(int(first), max_wave + 1, int(interval)))
    if bonus_first is not None and bonus_interval:
        out.update(range(int(bonus_first), max_wave + 1, int(bonus_interval)))
    return tuple(sorted(out))


def marks(tier, cfg=None, max_wave: int = MAX_WAVE) -> list[int]:
    """Every fleet spawn wave for `tier` up to `max_wave`, regular and bonus
    series merged and sorted. Empty when no schedule applies."""
    row = schedule(tier, cfg)
    if row is None:
        return []
    return list(_marks(row["first_wave"], row["interval"], row.get("bonus_first_wave"),
                       row.get("bonus_interval"), int(max_wave)))
