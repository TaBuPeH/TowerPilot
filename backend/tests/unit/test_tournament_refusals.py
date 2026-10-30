"""Hard rule 2: automation never cancels a tournament run.

The three chokepoints that could end a run each refuse a tournament run:
`tourney.end_round`, `tourney.ensure_home` and `flows.shard.abandon_run`.
These tests drive the REAL functions over a small fake screen (fake clock, no
adb) and assert what must hold for a tournament run: no tap or other input is
dispatched, the refusal is logged, and the call ends at once. Each has a
control that runs the same world as an ordinary run, so the refusal is shown
to be specific to the tournament badge.

What the code logs on a refusal is a trigger screenshot tag
(`logger.shot(frame, "<tag>")`), not a `logger.event`: the tags are
tourney_end_round_refused, tourney_ensure_home_refused and
shard_abandon_refused. The tests read them from the recording logger.

`ensure_home` over a tournament run used to be "covered" by a test that passed
by running into its 8 h hold ceiling and catching the same `Abort`. Here the
fake clock must not advance and no `tourney_home_hold` event may exist: that
event is logged only when the refusal is gone.
"""
from types import SimpleNamespace

import pytest

import interactions.tourney as tourney
from _fakes import FakeLogger
from device import act
from flows import shard
from tap_recorder import TapRecorder
from vision import detect, screen, wave_reader

START = 1000.0
BADGE = "tourney/in_tournament.png"
RUN_SCREENS = ("battle", "menu", "dialog")        # a run is behind all of these

# The screens a surrender walks through, and what each one shows.
SHOWS = {
    "battle": set(),
    "menu": {"buttons/exit_battle.png"},
    "dialog": {"home/exit_battle_dialog.png", "home/surrender.png"},
    "stats": {"home/game_stats_home.png"},
    "home": {"home/dissonant_run.png"},
}
# (screen, tap reason) -> next screen. Any other tap leaves the screen as it is.
AFTER_TAP = {
    ("battle", "open side menu"): "menu",
    ("menu", "exit battle"): "dialog",
    ("dialog", "exit battle: confirm"): "stats",
    ("dialog", "surrender"): "stats",
    ("stats", "game stats: HOME"): "home",
    ("stats", "RETRY"): "battle",
}


class _Taps(TapRecorder):
    """The shared tap recorder, answering like a sent tap and moving the world."""

    def __init__(self, world):
        super().__init__()
        self.world = world

    def __call__(self, x, y, reason="", **kwargs):
        super().__call__(x, y, reason=reason, **kwargs)
        self.world.screen = AFTER_TAP.get((self.world.screen, reason), self.world.screen)
        return {"dry_run": False, "x": x, "y": y}

    @property
    def reasons(self):
        return [kwargs["reason"] for _, kwargs in self.calls]


class World:
    """One screen at a time; frames are the screen's name. `run_ends_at` is the
    fake-clock time a running battle dies by itself (never, when None)."""

    def __init__(self, screen_name, *, tournament, run_ends_at=None):
        self.screen = screen_name
        self.tournament = tournament
        self.run_ends_at = run_ends_at
        self.clock = START
        self.other_inputs = []        # act._input / act.swipe: no tap path may reach them

    def sleep(self, seconds):
        self.clock += seconds
        if (self.run_ends_at is not None and self.screen == "battle"
                and self.clock >= self.run_ends_at):
            self.screen = "stats"

    def find(self, frame, rel, thresh=0.0, own=True):
        shown = set(SHOWS[frame])
        if self.tournament and frame in RUN_SCREENS:
            shown.add(BADGE)
        return ((540, 1400), 0.99) if rel in shown else None


@pytest.fixture
def wired(monkeypatch):
    """Both modules over one World, one tap recorder and one recording logger."""
    def build(screen_name, *, tournament, run_ends_at=None):
        world = World(screen_name, tournament=tournament, run_ends_at=run_ends_at)
        taps = _Taps(world)
        log = FakeLogger()
        fake_time = SimpleNamespace(monotonic=lambda: world.clock, sleep=world.sleep)
        monkeypatch.setattr(act, "tap", taps)
        monkeypatch.setattr(act, "swipe", lambda *a, **k: world.other_inputs.append(("swipe", a)))
        monkeypatch.setattr(act, "_input", lambda *a, **k: world.other_inputs.append(("_input", a)))
        monkeypatch.setattr(tourney.capture, "grab", lambda: world.screen)
        for module in (tourney, shard):
            monkeypatch.setattr(module, "find", world.find)
            monkeypatch.setattr(module, "time", fake_time)
            monkeypatch.setattr(module, "logger", log)
        monkeypatch.setattr(screen, "in_tournament", lambda f: world.find(f, BADGE) is not None)
        monkeypatch.setattr(detect, "side_menu_open", lambda f: f == "menu")
        monkeypatch.setattr(detect, "death_screen",
                            lambda f: (True, (540, 2000)) if f == "stats" else (False, None))
        monkeypatch.setattr(detect, "_match", lambda *a, **k: (False, 0.0, (0, 0)))
        monkeypatch.setattr(wave_reader, "read_wave", lambda f: 2224 if f in RUN_SCREENS else None)
        return world, taps, log
    return build


def _assert_refused(world, taps, log, tag):
    assert taps.calls == [], f"a tournament run was tapped: {taps.reasons}"
    assert world.other_inputs == []
    assert log.shots == [tag]
    assert world.screen in RUN_SCREENS            # the run is still there


# ------------------------------------------------------------- end_round

@pytest.mark.parametrize("where", RUN_SCREENS)
def test_end_round_refuses_a_tournament_run_and_taps_nothing(wired, where):
    world, taps, log = wired(where, tournament=True)
    with pytest.raises(tourney.Abort, match="TOURNAMENT"):
        tourney.end_round()
    _assert_refused(world, taps, log, "tourney_end_round_refused")
    assert "tourney_end_round" not in log.names()


def test_end_round_still_ends_an_ordinary_run(wired):
    world, taps, log = wired("battle", tournament=False)
    tourney.end_round()
    assert taps.reasons == ["open side menu", "exit battle", "exit battle: confirm",
                            "game stats: HOME"]
    assert world.screen == "home"
    assert "tourney_end_round_refused" not in log.shots
    assert ("tourney_end_round", {"reached_home": True}) in log.events


# ----------------------------------------------------------- ensure_home

def test_ensure_home_refuses_a_tournament_run_at_once(wired):
    world, taps, log = wired("battle", tournament=True)
    with pytest.raises(tourney.Abort, match="TOURNAMENT"):
        tourney.ensure_home()
    _assert_refused(world, taps, log, "tourney_ensure_home_refused")
    # it refused on the first look: not by holding until the 8 h ceiling
    assert world.clock == START
    assert not [k for k in log.names() if k.startswith("tourney_home_hold")]


def test_ensure_home_holds_an_ordinary_run_until_it_ends_by_itself(wired):
    world, taps, log = wired("battle", tournament=False, run_ends_at=START + 60)
    tourney.ensure_home()
    assert world.screen == "home"
    assert log.shots.count("tourney_ensure_home_refused") == 0
    assert log.names().count("tourney_home_hold") == 1
    assert "tourney_home_hold_release" in log.names()
    assert world.clock >= START + 60                  # it waited for the run
    assert taps.reasons == ["game stats: HOME"]       # only banking the finished run
    assert world.other_inputs == []


# ---------------------------------------------------------- abandon_run

@pytest.mark.parametrize("where", RUN_SCREENS)
def test_abandon_run_refuses_a_tournament_run_and_taps_nothing(wired, where):
    world, taps, log = wired(where, tournament=True)
    with pytest.raises(shard.Abort, match="TOURNAMENT"):
        shard.abandon_run()
    _assert_refused(world, taps, log, "shard_abandon_refused")
    assert "shard_retry" not in log.names()


def test_abandon_run_still_surrenders_an_ordinary_run(wired):
    world, taps, log = wired("battle", tournament=False)
    assert shard.abandon_run() is True
    assert taps.reasons == ["open side menu", "exit battle", "surrender", "RETRY"]
    assert "shard_abandon_refused" not in log.shots
    assert ("shard_retry", {"ok": True}) in log.events