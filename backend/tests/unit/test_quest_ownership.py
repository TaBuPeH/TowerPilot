"""Quest entrypoints and real shared exit helpers over an offline screen machine."""
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from flows import quest_ilm, quest_sm, shard
from flows._quest_run import QuestRun
from interactions import loadout, shopper, tourney
from device import act, capture
from vision import detect, screen, wave_reader


@dataclass
class Frame:
    name: str
    wave: int | None = None
    menu: bool = False
    tournament: bool = False


@pytest.fixture
def game(monkeypatch):
    from player import readiness
    from runtime import device_lease, logger
    state = SimpleNamespace(frame=Frame("home"), inputs=[], setup=[], on_tap=None,
                            grabs=None, dry=False)

    def grab():
        if state.grabs is not None:
            state.frame = next(state.grabs)
        return state.frame

    def tap(*args, reason="", **kwargs):
        state.inputs.append(reason)
        if state.on_tap:
            state.on_tap(reason)
        elif reason == "start battle" or reason == "RETRY":
            state.frame = Frame("battle", 1)
        elif reason == "open side menu":
            state.frame = Frame("battle", state.frame.wave, menu=True)
        elif reason == "exit battle":
            state.frame = Frame("exit_battle")
        elif reason == "surrender":
            state.frame = Frame("game_stats")
        elif reason == "game stats: HOME":
            state.frame = Frame("home")
        return {"dry_run": state.dry, "x": 1, "y": 1}

    def find(frame, rel, *args):
        yes = ((rel in ("buttons/exit_battle.png", "buttons/end_round.png") and frame.menu)
               or (rel == "home/surrender.png" and frame.name == "exit_battle")
               or (rel == "home/end_round_yes.png" and frame.name == "end_round")
               or (rel == "home/game_stats_home.png" and frame.name == "game_stats"))
        return ((1, 1), 1.0) if yes else None

    monkeypatch.setattr(capture, "grab", grab)
    monkeypatch.setattr(act, "tap", tap)
    monkeypatch.setattr(act, "swipe", lambda *a, **k: state.inputs.append("swipe"))
    monkeypatch.setattr(screen, "identify", lambda f: SimpleNamespace(name=f.name))
    monkeypatch.setattr(screen, "in_tournament", lambda f: f.tournament)
    monkeypatch.setattr(wave_reader, "read_wave", lambda f: f.wave)
    monkeypatch.setattr(detect, "side_menu_open", lambda f: f.menu)
    monkeypatch.setattr(detect, "death_screen", lambda f: (f.name == "game_stats", (1, 1)))
    monkeypatch.setattr(detect, "find_intro_sprint", lambda f: None)
    monkeypatch.setattr(shard, "find", find)
    monkeypatch.setattr(shard, "_speed_maxed", lambda f: True)
    monkeypatch.setattr(shard, "GemWatch", lambda **kw: SimpleNamespace(poll=lambda f: f))
    monkeypatch.setattr(shard, "set_tier", lambda t: state.setup.append("tier"))
    monkeypatch.setattr(loadout, "apply", lambda n: state.setup.append("loadout"))
    monkeypatch.setattr(loadout, "spec", lambda n: {})
    monkeypatch.setattr(logger, "shot", lambda *a: None)
    monkeypatch.setattr(logger, "event", lambda *a, **k: None)
    monkeypatch.setattr(device_lease, "prepare_instance", lambda *a: None)
    monkeypatch.setattr(readiness, "require", lambda *a: None)
    # No real waits, but a steadily progressing clock bounds every failure.
    clock = iter(i / 100 for i in range(100000))
    monkeypatch.setattr(shard.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(shard.time, "sleep", lambda *a: None)
    for q in (quest_ilm, quest_sm):
        monkeypatch.setattr(q, "_bind_preset", lambda *a: None)
        monkeypatch.setattr(q, "_preset", lambda: {"cycle_sec": .1, "ride_to_wave": 1})
    monkeypatch.setattr(quest_ilm, "_cli", lambda: SimpleNamespace(instance="i", cycles=2, preset=None))
    monkeypatch.setattr(quest_sm, "_cli", lambda: SimpleNamespace(instance="i", rides=2, preset=None, adopt_ride=False))
    monkeypatch.setattr(quest_ilm, "ensure_uws_off", lambda guard: guard(grab()))
    monkeypatch.setattr(quest_sm, "uw_setup", lambda guard: guard(grab()))
    monkeypatch.setattr(quest_sm, "SCAN_EVERY_SEC", .02)
    monkeypatch.setattr(quest_sm, "scan_grant", lambda *a, **k: ("smart_missiles", None))
    return state


@pytest.mark.parametrize("quest", [quest_ilm, quest_sm])
@pytest.mark.parametrize("initial", [Frame("battle", 500), Frame("battle", 1, tournament=True),
    Frame("unknown"), Frame("cards"), Frame("game_stats"), Frame("exit_battle"),
    Frame("end_round"), Frame("home", menu=True), Frame("home", wave=2)])
def test_actual_entrypoints_refuse_foreign_state(game, quest, initial):
    game.frame = initial
    with pytest.raises(tourney.Abort):
        quest.main()
    assert game.inputs == []
    assert game.setup == []


@pytest.mark.parametrize("quest", [quest_ilm, quest_sm])
def test_actual_entrypoint_completes_owned_cycles(game, quest):
    quest.main()
    assert game.setup == ["loadout", "tier"]
    assert game.inputs == ["start battle", "open side menu", "exit battle", "surrender",
                           "RETRY", "open side menu", "exit battle", "surrender", "game stats: HOME"]
    assert game.frame.name == "home"


def test_adopted_sm_target_never_ends_or_restarts_run(game, monkeypatch):
    game.frame = Frame("battle", 100)
    monkeypatch.setattr(quest_sm, "_cli", lambda: SimpleNamespace(instance="i", rides=3, preset=None, adopt_ride=True))
    quest_sm.main()
    assert game.inputs == []
    assert game.setup == []
    assert game.frame.wave == 100


def owned(game):
    run = QuestRun()
    run.start()
    game.inputs.clear()
    return run


def test_adoption_clears_any_previous_cycle_authority(game):
    run = owned(game)
    run.adopt(Frame("battle", 20))
    with pytest.raises(tourney.Abort, match="did not start"):
        run.abandon()
    assert game.inputs == []


@pytest.mark.parametrize("stage", ["open side menu", "exit battle", "surrender"])
@pytest.mark.parametrize("foreign", [Frame("cards"), Frame("unknown"), Frame("battle", 1, tournament=True)])
def test_human_frame_inside_real_exit_stops_all_later_input(game, stage, foreign):
    run = owned(game)
    original_tap = act.tap

    def tap(*a, reason="", **kw):
        result = original_tap(*a, reason=reason, **kw)
        if reason == stage:
            game.frame = foreign
        return result

    # Monkeypatch fixture does cleanup through its owning fixture instance.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(act, "tap", tap)
        with pytest.raises(tourney.Abort):
            run.abandon()
    assert game.inputs[-1] == stage
    assert not run.owned


@pytest.mark.parametrize("foreign", [Frame("home"), Frame("game_stats"), Frame("battle", 1)])
def test_observed_replacement_revokes_surrender_permanently(game, foreign):
    run = owned(game)
    run.check(Frame("battle", 50))
    game.frame = foreign
    with pytest.raises(tourney.Abort):
        run.observe(game.frame)
    game.frame = Frame("battle", 60)
    with pytest.raises(tourney.Abort):
        run.abandon()
    assert game.inputs == []


def test_unreadable_battle_waits_without_input_then_requires_readable_wave(game):
    run = owned(game)
    game.grabs = iter([Frame("battle", 4)])
    result = run.check(Frame("battle"))
    assert result.wave == 4
    assert game.inputs == []


def test_start_retry_checks_new_home_and_refuses_foreign_screen(game):
    game.on_tap = lambda reason: setattr(game, "frame", Frame("unknown"))
    run = QuestRun()
    with pytest.raises(tourney.Abort, match="clean Home"):
        run.start()
    assert game.inputs == ["start battle"]
    assert not run.owned


def test_dry_start_cannot_earn_ownership(game):
    game.dry = True
    run = QuestRun()
    with pytest.raises(tourney.Abort, match="not dispatched"):
        run.start()
    assert not run.owned


def test_retry_home_never_calls_unsafe_home_recovery(game):
    run = owned(game)
    original = act.tap
    with pytest.MonkeyPatch.context() as patch:
        def tap(*a, reason="", **kw):
            ev = original(*a, reason=reason, **kw)
            if reason == "RETRY":
                game.frame = Frame("home")
            return ev
        patch.setattr(act, "tap", tap)
        with pytest.raises(tourney.Abort, match="RETRY returned Home"):
            run.abandon()
    assert game.inputs[-1] == "RETRY"
    assert "start battle" not in game.inputs
    assert not run.owned


def test_decision_wait_observes_human_replacement(game, monkeypatch, tmp_path):
    run = owned(game)
    run.check(Frame("battle", 90))
    game.grabs = iter([Frame("battle", 91), Frame("battle", 1)])
    monkeypatch.setattr(quest_sm, "_decision_path", lambda: str(tmp_path / "decision"))
    with pytest.raises(tourney.Abort, match="rollback"):
        quest_sm.await_decision(run)
    assert game.inputs == []


@pytest.mark.parametrize("method", ["speed", "tab", "scroll", "sprint"])
def test_nested_helper_observation_revokes_on_foreign_frame(game, method):
    run = owned(game)
    game.frame = Frame("cards")
    with pytest.raises(tourney.Abort):
        if method == "speed":
            shard.ensure_max_speed(guard=run.guard)
        elif method == "tab":
            shopper._tap_tab("uw", guard=run.guard)
        elif method == "scroll":
            shopper._swipe_panel_down(guard=run.guard)
        else:
            shard.cancel_sprint(guard=run.guard)
    assert game.inputs == []


@pytest.mark.parametrize("mode", ["wave", "wrong", "restart", "timeout"])
def test_actual_sm_rerolls_owned_cycles_for_each_reason(game, monkeypatch, tmp_path, mode):
    if mode == "wave":
        speed = shard.ensure_max_speed
        calls = []
        def ensure(**kw):
            speed(**kw)
            if not calls:
                game.frame = Frame("battle", 1000)
            calls.append(True)
        monkeypatch.setattr(shard, "ensure_max_speed", ensure)
    else:
        verdicts = iter([("wrong" if mode == "wrong" else "unknown", "inner_land_mines"),
                         ("smart_missiles", None), ("smart_missiles", None)])
        monkeypatch.setattr(quest_sm, "scan_grant", lambda *a, **kw: next(verdicts))
        decision = tmp_path / "decision"
        monkeypatch.setattr(quest_sm, "_decision_path", lambda: str(decision))
        if mode == "restart":
            decision.write_text("restart", encoding="utf-8")
        elif mode == "timeout":
            monkeypatch.setattr(quest_sm, "DECISION_TIMEOUT_SEC", .02)
    quest_sm.main()
    assert game.inputs.count("RETRY") == 2
    assert game.inputs.count("surrender") == 3
    assert game.frame.name == "home"


def test_sm_final_completion_requires_current_continuity(game, monkeypatch):
    def ride(target, run):
        run.check(Frame("battle", 50))
        game.frame = Frame("battle", 1)
    monkeypatch.setattr(quest_sm, "ride_to", ride)
    with pytest.raises(tourney.Abort, match="rollback"):
        quest_sm.main()
    assert game.inputs == ["start battle"]


def test_ilm_poll_revokes_before_completion(game, monkeypatch):
    run = owned(game)
    monkeypatch.setattr(shard, "GemWatch", lambda **kw: SimpleNamespace(poll=lambda f: Frame("cards")))
    with pytest.raises(tourney.Abort):
        quest_ilm.one_cycle(2, last=True, run=run)
    assert game.inputs == []


def test_human_closing_observed_side_menu_revokes_exit(game, monkeypatch):
    run = owned(game)
    game.grabs = iter([Frame("battle", 5), Frame("battle", 5, menu=True), Frame("battle", 5)])
    monkeypatch.setattr(shard, "find", lambda *a: None)
    with pytest.raises(tourney.Abort, match="menu was closed"):
        run.abandon()
    assert game.inputs == ["open side menu"]


def test_stats_destination_retires_the_prior_exit_dialog(game, monkeypatch):
    run = owned(game)
    find = shard.find
    def hide_home(frame, rel, *a):
        if rel == "home/game_stats_home.png":
            game.frame = Frame("exit_battle")
            return None
        return find(frame, rel, *a)
    monkeypatch.setattr(shard, "find", hide_home)
    with pytest.raises(tourney.Abort, match="foreign screen during exit"):
        run.abandon(to_home=True)
    assert game.inputs == ["open side menu", "exit battle", "surrender"]


def test_owned_confirm_can_retry_swallowed_input(game, monkeypatch):
    run = owned(game)
    original = act.tap
    swallowed = []
    def tap(*a, reason="", **kw):
        ev = original(*a, reason=reason, **kw)
        if reason == "surrender" and not swallowed:
            swallowed.append(True)
            game.frame = Frame("exit_battle")
        return ev
    monkeypatch.setattr(act, "tap", tap)
    run.abandon()
    assert game.inputs.count("surrender") == 2
    assert run.owned and run.phase == "live" and run.wave == 1


def test_swallowed_start_retry_still_requires_home_then_observed_battle(game, monkeypatch):
    original = act.tap
    attempts = []
    def tap(*a, reason="", **kw):
        ev = original(*a, reason=reason, **kw)
        if reason == "start battle" and not attempts:
            attempts.append(True)
            game.frame = Frame("home")
        return ev
    monkeypatch.setattr(act, "tap", tap)
    run = QuestRun()
    run.start()
    assert game.inputs == ["start battle", "start battle"]
    assert run.owned and run.phase == "live"


def test_dry_retry_cannot_earn_successor(game):
    run = owned(game)
    original = act.tap
    with pytest.MonkeyPatch.context() as patch:
        def tap(*a, reason="", **kw):
            ev = original(*a, reason=reason, **kw)
            if reason == "RETRY":
                ev["dry_run"] = True
            return ev
        patch.setattr(act, "tap", tap)
        with pytest.raises(tourney.Abort, match="not dispatched"):
            run.abandon()
    assert not run.owned and run.phase == "revoked"


def test_sprint_expected_fades_wait_without_extra_input(game, monkeypatch):
    run = owned(game)
    game.grabs = iter([Frame("battle", 5), Frame("battle", 5), Frame("unknown"), Frame("intro_sprint_end"),
                       Frame("unknown"), Frame("battle", 6)])
    monkeypatch.setattr(detect, "find_intro_sprint", lambda f: (1, 1) if f.wave == 5 else None)
    monkeypatch.setattr(screen, "_match", lambda *a: (1.0, (1, 1)))
    assert shard.cancel_sprint(guard=run.guard)
    assert game.inputs == ["intro sprint indicator", "intro sprint: yes"]
    assert run.phase == "live" and run.wave == 6


def test_swallowed_sprint_indicator_leaves_live_authority_without_confirmation(game, monkeypatch):
    run = owned(game)
    game.frame = Frame("battle", 5)
    monkeypatch.setattr(detect, "find_intro_sprint", lambda f: (1, 1))
    assert shard.cancel_sprint(guard=run.guard) is False
    assert game.inputs == ["intro sprint indicator"]
    assert run.phase == "live" and run.owned


def test_hidden_motion_probe_frame_revokes_quest_authority(game, monkeypatch):
    run = owned(game)
    from device import overlays
    monkeypatch.setattr(overlays, "windows", lambda *a, **k: [])
    monkeypatch.setattr(overlays, "foreground_blocker", lambda wins: None)
    game.frame = Frame("cards")
    with pytest.raises(tourney.Abort):
        shard.battlefield_live([(1, 1)], Frame("battle", 2), guard=run.guard)
    assert not run.owned
    assert game.inputs == []


def test_ad_gem_capture_guard_cannot_be_swallowed_as_collection_error(game, monkeypatch):
    from interactions import ad_gems
    from runtime import logger
    run = owned(game)
    events = []
    monkeypatch.setattr(logger, "event", lambda name, **data: events.append((name, data)))
    monkeypatch.setattr(ad_gems, "find_claim_rect", lambda f, **kwargs: (1, 1, 100, 100))
    collector = ad_gems.AdGemCollector(delay=(0, 0), guard=run.guard)
    game.frame = Frame("cards")
    with pytest.raises(tourney.Abort):
        collector.poll(Frame("battle", 2))
    starts = [data for name, data in events if name == "ad_gems_attempt"]
    ends = [data for name, data in events if name == "ad_gems_attempt_finished"]
    assert len(starts) == len(ends) == 1
    assert starts[0]["claim_attempt_id"] == ends[0]["claim_attempt_id"]
    assert ends[0]["outcome"] == "interrupted" and ends[0]["reason"] == "ownership_lost"
    assert ends[0]["error_type"] == "Abort" and "traceback" in ends[0]
    assert game.inputs == []


@pytest.mark.parametrize("initial", [Frame("battle", 1, tournament=True), Frame("cards"),
                                    Frame("game_stats"), Frame("exit_battle"), Frame("unknown")])
def test_adopt_ride_refuses_foreign_or_tournament_entry(game, monkeypatch, initial):
    game.frame = initial
    monkeypatch.setattr(quest_sm, "_cli", lambda: SimpleNamespace(instance="i", rides=1, preset=None, adopt_ride=True))
    with pytest.raises(tourney.Abort):
        quest_sm.main()
    assert game.inputs == game.setup == []


@pytest.mark.parametrize("verdict", ["wrong", "unknown", "smart_missiles"])
def test_sm_grant_result_cannot_hide_foreign_navigation(game, monkeypatch, verdict):
    def scan(*a, **kw):
        game.frame = Frame("cards")
        return verdict, None
    monkeypatch.setattr(quest_sm, "scan_grant", scan)
    with pytest.raises(tourney.Abort):
        quest_sm.main()
    assert game.inputs == ["start battle"]


@pytest.mark.parametrize("failed_input", ["start battle", "open side menu", "exit battle", "surrender", "RETRY"])
def test_transport_failure_revokes_authority_before_any_later_attempt(game, monkeypatch, failed_input):
    run = QuestRun() if failed_input == "start battle" else owned(game)
    original = act.tap
    def tap(*a, reason="", **kw):
        if reason == failed_input:
            game.inputs.append(reason)
            raise OSError("synthetic transport failure")
        return original(*a, reason=reason, **kw)
    monkeypatch.setattr(act, "tap", tap)
    with pytest.raises(OSError, match="synthetic transport"):
        if failed_input == "start battle":
            run.start()
        else:
            run.abandon()
    assert not run.owned and run.phase == "revoked"
    before = game.inputs.copy()
    with pytest.raises(tourney.Abort):
        run.abandon()
    assert game.inputs == before
