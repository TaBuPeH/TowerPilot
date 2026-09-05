"""A live run is never ended by automation, whoever started it
(user, 2026-09-05: "even if someone else started a run you do not interrupt").

Guarantees: tourney.ensure_home HOLDS on a live run instead of walking it out
through END ROUND, taps nothing while it holds, and the scheduler spawns the
shard runner in adopt mode over the battle its own handoff started, so the
runner's setup never walks Home over it (the "Tier 18 / Wave 1 / Coins 0"
history entries).
"""
import types

import pytest

import interactions.tourney as tourney
import flows
import scheduling.combo as combo


class _Log:
    def __init__(self):
        self.events = []

    def event(self, kind, **kw):
        self.events.append((kind, kw))

    def shot(self, frame, name):
        return f"{name}.png"


def _wire(monkeypatch, frames, tournament=False):
    """Drive ensure_home with a scripted sequence of screen tokens."""
    seq = list(frames)
    clock = [1000.0]
    last = {"frame": None}

    def grab():
        last["frame"] = seq.pop(0) if len(seq) > 1 else seq[0]
        return last["frame"]

    monkeypatch.setattr(tourney.capture, "grab", grab)
    monkeypatch.setattr(tourney, "on_home", lambda f: f == "home")
    monkeypatch.setattr(tourney, "_in_battle", lambda f: f in ("battle", "stats"))
    monkeypatch.setattr(tourney, "in_tournament", lambda f: tournament)
    monkeypatch.setattr(tourney.detect, "death_screen",
                        lambda f: (f == "stats", None))
    monkeypatch.setattr(tourney, "find",
                        lambda f, rel, *a, **k: ((1, 1), 1.0)
                        if (f == "stats" and rel == "home/game_stats_home.png")
                        else None)
    monkeypatch.setattr(tourney.wave_reader, "read_wave",
                        lambda f: 2224 if f == "battle" else None)
    ended = []
    monkeypatch.setattr(tourney, "end_round", lambda: ended.append(last["frame"]))
    taps = []
    monkeypatch.setattr(tourney, "tap_at", lambda pt, why: taps.append((last["frame"], why)))
    monkeypatch.setattr(tourney, "time", types.SimpleNamespace(
        sleep=lambda s: clock.__setitem__(0, clock[0] + s),
        monotonic=lambda: clock[0]))
    log = _Log()
    monkeypatch.setattr(tourney, "logger", log)
    return ended, log, clock, taps


def test_ensure_home_holds_on_a_live_run_and_never_ends_it(monkeypatch):
    ended, log, _, taps = _wire(monkeypatch, ["battle", "battle", "battle", "stats", "home"])
    assert tourney.ensure_home() == "home"
    # END ROUND ran exactly once, and only on the GAME STATS screen
    assert ended == ["stats"]
    assert taps == []
    kinds = [k for k, _ in log.events]
    assert kinds.count("tourney_home_hold") == 1
    assert "tourney_home_hold_release" in kinds
    hold = next(kw for k, kw in log.events if k == "tourney_home_hold")
    assert hold["wave"] == 2224 and hold["reason"] == "run in progress"


def test_ensure_home_taps_nothing_while_a_person_is_in_the_menus(monkeypatch):
    """A menu excursion during the hold (neither battle nor Home) is a person
    at the screen: keep waiting, never 'back out to home' / 'nav battle'."""
    ended, log, _, taps = _wire(monkeypatch, ["battle", "menu", "menu", "battle",
                                            "stats", "home"])
    assert tourney.ensure_home() == "home"
    assert taps == []
    assert ended == ["stats"]


def test_ensure_home_still_walks_menus_home_when_no_run_was_seen(monkeypatch):
    """The bot-owned case: after a death, chores leave a menu up and nothing is
    live - the old tap-out path still applies."""
    ended, log, _, taps = _wire(monkeypatch, ["menu", "menu", "home"])
    assert tourney.ensure_home() == "home"
    assert [why for _, why in taps][:1] == ["back out to home"]
    assert ended == []


def test_ensure_home_gives_up_after_the_runaway_ceiling(monkeypatch):
    ended, log, clock, taps = _wire(monkeypatch, ["battle"])
    monkeypatch.setattr(tourney, "HOLD_LIVE_RUN_SEC", 20)
    with pytest.raises(tourney.Abort):
        tourney.ensure_home()
    assert ended == [] and taps == []      # gave up, still touched nothing


def test_ensure_home_still_refuses_a_tournament_run(monkeypatch):
    ended, log, _, taps = _wire(monkeypatch, ["battle"], tournament=True)
    with pytest.raises(tourney.Abort):
        tourney.ensure_home()
    assert ended == []


def test_live_run_is_not_the_stats_screen(monkeypatch):
    monkeypatch.setattr(tourney, "_in_battle", lambda f: f in ("battle", "stats"))
    monkeypatch.setattr(tourney.detect, "death_screen", lambda f: (f == "stats", None))
    monkeypatch.setattr(tourney, "find", lambda f, rel, *a, **k: None)
    assert tourney.live_run("battle") is True
    assert tourney.live_run("stats") is False
    assert tourney.live_run("home") is False


def test_shard_runner_is_spawned_in_adopt_mode_after_a_loadout_handoff(monkeypatch):
    assert flows.flow("shard")["adopt_arg"] == "--no-setup"
    assert flows.flow("shard")["handoff"] == "loadout"
    b = {"kind": "shard", "preset": "bp_shard_daily", "count": 100,
         "id": "test#0", "block": "shards"}
    monkeypatch.setattr(combo, "_block_progress", lambda b: 0)
    spawned = []
    monkeypatch.setattr(combo, "_spawn", lambda argv: spawned.append(argv) or "proc")
    combo._block_runner(b, "acct2")
    assert spawned[0][-1] == "--no-setup"
    assert spawned[0][:-1] == combo._block_argv(b, "acct2")
    # the adoption-matching argv itself is unchanged
    assert "--no-setup" not in combo._block_argv(b, "acct2")
