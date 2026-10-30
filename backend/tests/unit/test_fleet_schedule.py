"""The fleet timetable is per tier and picked by the run's tier (user,
2026-09-12: "does it auto-adjust based on the active tier?" - it did not:
one 2495 + 1000k list served every tier). Numbers: Tower Hub / Fandom wiki
table, confirmed live on Tier 14."""
import pytest

from scheduling import fleets


@pytest.mark.parametrize("tier, first, interval, per", [
    (1, 15000, 100, 1), (2, 14750, 100, 1), (13, 12000, 100, 1),
    (14, 2495, 1000, 1), (15, 1495, 750, 1), (16, 995, 500, 1), (17, 495, 250, 1),
    (18, 95, 100, 1), (19, 45, 50, 1), (20, 5, 10, 1), (21, 5, 10, 2),
    (22, 5, 10, 3), (24, 5, 10, 3),
])
def test_table_matches_the_published_spawn_rows(tier, first, interval, per):
    row = fleets.schedule(tier)
    assert (row["first_wave"], row["interval"], row["fleets_per_spawn"]) == (first, interval, per)
    assert row["source"] == "table"


@pytest.mark.parametrize("tier, bonus", [(14, 11750), (15, 11500), (18, 10750), (20, 10250), (24, 9250)])
def test_battle_condition_tiers_carry_the_bonus_series(tier, bonus):
    row = fleets.schedule(tier)
    assert (row["bonus_first_wave"], row["bonus_interval"]) == (bonus, 100)
    assert fleets.schedule(13)["bonus_first_wave"] is None


def test_marks_merge_regular_and_bonus_series_in_order():
    m = fleets.marks(14)
    assert m[:4] == [2495, 3495, 4495, 5495]
    assert 11750 in m and 11850 in m and 12495 in m
    assert m == sorted(set(m)) and m[-1] <= fleets.MAX_WAVE
    assert fleets.marks(18)[:3] == [95, 195, 295]
    assert fleets.marks(20)[:3] == [5, 15, 25]
    assert fleets.marks(1)[:2] == [15000, 15100]


def test_config_overrides_one_tier_and_legacy_keys_serve_only_untiered_runs():
    cfg = {"fleet": {"first_wave": 2495, "interval": 1000, "by_tier": {"18": {"first_wave": 90, "interval": 100}}}}
    assert fleets.schedule(18, cfg)["source"] == "config"
    assert fleets.marks(18, cfg)[:2] == [90, 190]
    assert fleets.marks(18, cfg).count(10750) == 1          # the table's bonus row survives a partial override
    assert fleets.schedule(14, cfg)["first_wave"] == 2495 and fleets.schedule(14, cfg)["source"] == "table"
    legacy = fleets.schedule(None, cfg)
    assert legacy["source"] == "legacy" and fleets.marks(None, cfg)[:2] == [2495, 3495]
    assert fleets.marks(None, {"fleet": {"hold_window": 100}}) == []
    assert fleets.schedule(0) is None


def test_legends_row_is_data_never_auto_selected():
    assert fleets.LEGENDS["first_wave"] == 895 and fleets.LEGENDS["interval"] == 30
    assert all(fleets.schedule(t)["first_wave"] != 895 for t in range(1, 25))


def test_orchestrator_marks_follow_the_preset_tier(monkeypatch):
    import orchestrator
    monkeypatch.setattr(orchestrator, "CONFIG", {"fleet": {"first_wave": 2495, "interval": 1000}})
    monkeypatch.setattr(orchestrator, "preset", lambda: {"tier": 18})
    assert orchestrator.run_tier() == 18 and orchestrator.marks()[:2] == [95, 195]
    monkeypatch.setattr(orchestrator, "preset", lambda: {"tier": 14})
    assert orchestrator.marks()[:2] == [2495, 3495]
    monkeypatch.setattr(orchestrator, "preset", lambda: {})          # legacy preset: old single schedule
    assert orchestrator.run_tier() is None and orchestrator.marks()[:2] == [2495, 3495]


def test_shard_loop_waves_derive_from_the_tier_first_fleet():
    from flows import shard
    assert shard.fleet_waves(18) == (100, 101)      # the documented T18 loop
    assert shard.fleet_waves(19) == (50, 51)
    assert shard.fleet_waves(17) == (500, 501)
