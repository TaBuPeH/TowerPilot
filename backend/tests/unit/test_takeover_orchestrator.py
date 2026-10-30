"""Takeover worker through the real orchestrator entry and loop (roadmap item 3, T3b): zero input on every refusal and boundary. Offline: the screen
readers, the lease and every input call are replaced; the ledger, the battle store, `attach_worker`, `BattleSession` and `orchestrator.main` are real."""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import paths as test_paths
from takeover_world import RUN, World, frame, readers

ORCHESTRATOR = test_paths.BACKEND / "orchestrator.py"


class Stop(BaseException):
    """The test's own end of the observe loop."""


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def drive(world, monkeypatch, tmp_path, frames, statuses=(), *, ordinary=False, revoked=False, not_ready=False, delegated=False, **attach_kwargs):
    """Run orchestrator.main() against scripted frames. `statuses` are what `watch_frame` answers per observed frame; running out ends the loop."""
    import orchestrator as orch
    import settings
    from player import readiness
    from runtime import device_lease, logger
    taps, events, calls = [], [], []
    if ordinary:
        op, _ = world.operations.accept("run", world.context, {})
        world.op = world.operations.transition(op["id"], op["revision"], "running", worker={"pid": 2, "creation_time": 2})
    else:
        world.attach_operation(**attach_kwargs)
    monkeypatch.setenv("TOWER_OPERATION_ID", world.op["id"])
    monkeypatch.setenv("TOWER_OPERATION_DB", world.operations.path)
    for name in ("TOWER_BATTLE_ID", "TOWER_BATTLE_GENERATION", "TOWER_DEVICE_DELEGATION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "ROOT", tmp_path)
    monkeypatch.setattr(orch, "CONFIG", world.cfg)
    for name in ("_attach", "_battle_session", "_action_session", "_rewards_history"):
        monkeypatch.setattr(orch, name, None)
    state, Exhausted = readers(monkeypatch, frames, taps)
    monkeypatch.setattr(device_lease, "ensure_current", lambda: SimpleNamespace(key="lease-key"))
    monkeypatch.setattr(device_lease, "revocation", lambda key: "revoked" if revoked else None)
    def require(*args, **kwargs):
        calls.append(("readiness", kwargs))
        if not_ready:
            raise RuntimeError("Calibration needed before this run: tourney/in_tournament.png")

    monkeypatch.setattr(readiness, "require", require)
    if delegated:
        monkeypatch.setenv("TOWER_BATTLE_ID", "some-battle")
        monkeypatch.setenv("TOWER_BATTLE_GENERATION", "some-generation")
    monkeypatch.setattr(logger, "event", lambda kind, **fields: events.append((kind, fields)))
    monkeypatch.setattr(logger, "shot", lambda *args, **kwargs: "shot")
    pending = list(statuses)

    def watch(rs, observed, observation=None):
        calls.append(("watch", None))
        if not pending:
            raise Stop
        status = pending.pop(0)
        if isinstance(status, Exception):
            raise status                       # what a helper's own guard raises mid-monitor
        return status

    monkeypatch.setattr(orch, "watch_frame", watch)
    monkeypatch.setattr(orch.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(orch.psutil, "Process", lambda: SimpleNamespace(cpu_percent=lambda: 0))
    for name, result in (("collect", None), ("run_death_rules", False)):
        target = orch.runlog if name == "collect" else orch
        monkeypatch.setattr(target, name, lambda *a, _n=name, _r=result, **k: calls.append((_n, None)) or _r)
    monkeypatch.setattr(orch.runflag, "requested", lambda: calls.append(("runflag.requested", None)) or "combo -> next")
    monkeypatch.setattr(orch, "restart_from_home", lambda *a, **k: calls.append(("restart_from_home", None)) or True)
    monkeypatch.setattr(orch.detect, "_match", lambda *a, **k: (True, None))
    if ordinary:
        monkeypatch.setattr(orch, "_prepare_coin_start", lambda: None)
    try:
        orch.main()
        outcome = "returned"
    except Stop:
        outcome = "looping"
    except Exhausted:
        outcome = "exhausted"
    return SimpleNamespace(outcome=outcome, taps=taps, events=events, calls=calls, frames_read=state.index + 1, orch=orch)


def names(run):
    return [kind for kind, _ in run.events]


def fields(run, kind):
    return [f for k, f in run.events if k == kind]


# ------------------------------------------------------------------ the entry gate through main()
REFUSALS = [
    (dict(frames=[frame(screen="home", wave=None)]), "attach_no_battle"),
    (dict(frames=[frame(wave=None)]), "attach_no_battle"),
    (dict(frames=[frame(tournament="yes")]), "attach_tournament"),
    (dict(frames=[frame(tournament="unknown")]), "attach_tournament_unknown"),
    (dict(frames=[frame(wave=50)], observed_wave=500), "attach_wave_mismatch"),
    (dict(frames=[frame()], revoked=True), "attach_input_revoked"),
]


@pytest.mark.parametrize("case,code", REFUSALS, ids=[c for _, c in REFUSALS[:5]] + ["input_revoked"])
def test_entry_refuses_with_its_code_zero_taps_and_no_record(world, monkeypatch, tmp_path, case, code):
    old = world.collector()
    run = drive(world, monkeypatch, tmp_path, case.pop("frames"), predecessor=world.predecessor(old), **case)
    done = world.operations.get(world.op["id"])
    assert run.outcome == "returned" and run.taps == []
    assert (done["status"], done["error"]["code"]) == ("failed", code)
    assert fields(run, "attach_refused")[0]["code"] == code
    assert [b["id"] for b in world.records()] == [old["id"]]                 # no record created
    assert world.store.get(old["id"])["status"] == "active"                  # and the collector's record is not closed
    assert run.frames_read == 1 and "watch" not in [name for name, _ in run.calls]


def test_a_refused_entry_after_the_setup_guard_not_before(world, monkeypatch, tmp_path):
    """The guard runs first: the gate is the next step, so a refused takeover reads exactly one frame."""
    run = drive(world, monkeypatch, tmp_path, [frame(screen="home", wave=None)])
    assert run.frames_read == 1 and names(run).index("attach_refused") >= 0


def test_a_live_battle_enters_with_the_declared_tier_and_no_input(world, monkeypatch, tmp_path):
    run = drive(world, monkeypatch, tmp_path, [frame(wave=120), frame(wave=121)], tier=18, observed_wave=100)
    assert run.outcome == "looping" and run.taps == []
    entry = fields(run, "attach_entry")[0]
    assert (entry["wave"], entry["tier"], entry["tier_provenance"]) == (120, 18, "declared")
    assert "loadout" in entry["stripped"]
    (record,) = world.records()
    assert (record["origin"], record["previous_battle_id"]) == ("adopted", None)
    assert [call for call in run.calls if call[0] == "readiness"] == [("readiness", {"attached": True})]
    assert run.frames_read == 2                       # the start preparation did not capture a Home frame
    assert world.operations.get(world.op["id"])["status"] == "running"


def test_the_entry_wave_is_stored_with_the_record_and_read_back(world, monkeypatch, tmp_path):
    run = drive(world, monkeypatch, tmp_path, [frame(wave=120), frame(wave=125)], observed_wave=100)
    (record,) = world.records()
    stored = world.store.get(record["id"])["runtime"]["attach_entry"]
    assert stored == {"observed_wave": 120, "source": "entry_gate"}            # the gate's frame, not the later one and not the confirmed 100
    assert fields(run, "attach_entry")[0]["wave"] == 120


def test_a_later_status_publish_does_not_overwrite_the_entry_wave(world, monkeypatch, tmp_path):
    drive(world, monkeypatch, tmp_path, [frame(wave=120), frame(wave=125)])
    (record,) = world.records()
    world.store.update_status(record["id"], record["generation"], {"observed_wave": 130, "routines": {}})
    assert world.store.get(record["id"])["runtime"]["attach_entry"]["observed_wave"] == 120


def test_an_ordinary_run_has_no_entry_wave(world, monkeypatch, tmp_path):
    drive(world, monkeypatch, tmp_path, [frame(wave=120), frame(wave=120)], ordinary=True)
    (record,) = world.records()
    assert "attach_entry" not in world.store.get(record["id"])["runtime"]


def test_the_collector_record_is_linked_after_the_gate(world, monkeypatch, tmp_path):
    old = world.collector(finish="reconcile")
    run = drive(world, monkeypatch, tmp_path, [frame(), frame()], predecessor=world.predecessor(old))
    newest = [b for b in world.records() if b["id"] != old["id"]]
    assert len(newest) == 1 and newest[0]["previous_battle_id"] == old["id"] and newest[0]["origin"] == "partial_adoption"
    assert run.taps == []


def test_a_wrong_predecessor_ends_before_any_record_or_input(world, monkeypatch, tmp_path):
    old = world.collector()
    named = world.predecessor(old)
    named["operation_id"] = "elsewhere"
    run = drive(world, monkeypatch, tmp_path, [frame()], predecessor=named)
    assert world.operations.get(world.op["id"])["error"]["code"] == "attach_predecessor_mismatch"
    assert [b["id"] for b in world.records()] == [old["id"]] and run.taps == []


def test_a_missing_calibration_refuses_before_the_first_capture(world, monkeypatch, tmp_path):
    run = drive(world, monkeypatch, tmp_path, [frame()], not_ready=True)
    done = world.operations.get(world.op["id"])
    assert (done["status"], done["error"]["code"]) == ("failed", "attach_not_ready")
    assert run.frames_read == 0 and run.taps == [] and world.records() == []


def test_a_delegated_child_never_attaches(world, monkeypatch, tmp_path):
    run = drive(world, monkeypatch, tmp_path, [frame()], delegated=True)
    assert world.operations.get(world.op["id"])["error"]["code"] == "attach_envelope_invalid"
    assert run.frames_read == 0 and world.records() == []


def test_an_untrusted_envelope_refuses_before_the_first_capture(world, monkeypatch, tmp_path):
    run = drive(world, monkeypatch, tmp_path, [frame()], mutate=lambda r: r["battle_envelope"]["preset"].update(loadout={"cards": "x"}))
    assert world.operations.get(world.op["id"])["error"]["code"] == "attach_envelope_invalid"
    assert run.frames_read == 0 and run.taps == [] and world.records() == []


# ------------------------------------------------------------------ the terminal branches
@pytest.mark.parametrize("pending_flag", [False, True])
def test_death_ends_the_operation_with_no_input_collection_rules_or_restart(world, monkeypatch, tmp_path, pending_flag):
    flag = tmp_path / "stop_after_run"
    if pending_flag:
        flag.write_text("combo -> shards", encoding="utf-8")
    run = drive(world, monkeypatch, tmp_path, [frame(wave=300), frame(wave=300), frame(screen="death", wave=None)], ["refresh", "dead"])
    assert run.outcome == "returned" and run.taps == []
    assert [name for name, _ in run.calls if name != "watch" and name != "readiness"] == []     # no collect, rules, flag read or restart
    (record,) = world.records()
    assert (record["status"], record["reason"]) == ("ended", "observed_results")
    assert fields(run, "attach_boundary") == [dict(reason="observed_results", wave=300)]
    assert flag.exists() is pending_flag and (not pending_flag or flag.read_text(encoding="utf-8") == "combo -> shards")
    assert "retry" not in names(run) and "stop_after_run" not in names(run)


def test_an_ordinary_run_keeps_todays_death_branch(world, monkeypatch, tmp_path):
    death = frame(screen="death", wave=None)
    run = drive(world, monkeypatch, tmp_path, [frame(wave=300), frame(wave=300), death, death], ["refresh", "dead"], ordinary=True)
    assert run.outcome == "returned"
    assert [name for name, _ in run.calls if name in ("collect", "run_death_rules", "runflag.requested")] == ["collect", "run_death_rules", "runflag.requested"]
    assert "attach_boundary" not in names(run)


def test_a_confirmed_replacement_ends_without_a_successor_or_input(world, monkeypatch, tmp_path):
    frames = [frame(wave=500), frame(wave=500), frame(wave=10), frame(wave=10), frame(wave=10, tournament="yes")]
    run = drive(world, monkeypatch, tmp_path, frames, ["refresh"])
    assert run.outcome == "returned" and run.taps == []
    (record,) = world.records()                                      # no successor was prepared
    assert (record["status"], record["reason"]) == ("ended", "wave_went_backwards")
    assert fields(run, "attach_boundary") == [dict(reason="wave_went_backwards", previous_wave=500, wave=10)]
    assert run.frames_read == len(frames) and [name for name, _ in run.calls].count("watch") == 1


def test_an_unconfirmed_regression_keeps_probation_and_the_battle(world, monkeypatch, tmp_path):
    run = drive(world, monkeypatch, tmp_path, [frame(wave=500), frame(wave=500), frame(wave=10), frame(wave=10)], ["refresh"])
    assert run.outcome == "exhausted" and run.taps == []
    (record,) = world.records()
    assert record["status"] == "active" and "attach_boundary" not in names(run)


def test_an_ordinary_run_still_starts_a_successor_on_a_replaced_battle(world, monkeypatch, tmp_path):
    frames = [frame(wave=500), frame(wave=500), frame(wave=10), frame(wave=10), frame(wave=10), frame(wave=10)]
    run = drive(world, monkeypatch, tmp_path, frames, ["refresh", "refresh"], ordinary=True)
    assert len(world.records()) == 2 and "attach_boundary" not in names(run)


def test_a_tournament_results_dialog_ends_a_takeover_with_no_input(world, monkeypatch, tmp_path):
    frames = [frame(wave=300), frame(wave=300), frame(screen="tournament_stats", wave=None), frame(screen="home", wave=None)]
    run = drive(world, monkeypatch, tmp_path, frames, ["refresh", "menu"])
    assert run.outcome == "returned" and run.taps == []
    (record,) = world.records()
    assert (record["status"], record["reason"]) == ("ended", "observed_tournament_results")
    assert fields(run, "attach_boundary") == [dict(reason="observed_tournament_results", wave=300)]
    assert run.frames_read == 3                       # it never looked again after the dialog: no OK press, no wait for the dialog to close


def test_an_ordinary_run_still_presses_ok_on_the_tournament_results_dialog(world, monkeypatch, tmp_path):
    frames = [frame(wave=300), frame(wave=300), frame(screen="tournament_stats", wave=None), frame(screen="home", wave=None)]
    run = drive(world, monkeypatch, tmp_path, frames, ["refresh", "menu"], ordinary=True)
    assert run.outcome == "returned" and [(kind, args) for kind, args, _ in run.taps] == [("tap", (538, 1793))]
    assert "attach_boundary" not in names(run)


def test_the_entry_wave_is_the_first_boundary_baseline(world, monkeypatch, tmp_path):
    """A different battle that replaced the confirmed one between the gate and the first loop frame is a boundary like any later replacement."""
    run = drive(world, monkeypatch, tmp_path, [frame(wave=500), frame(wave=10), frame(wave=10), frame(wave=10)], ["refresh", "refresh", "refresh"])
    assert run.outcome == "returned" and run.taps == []
    (record,) = world.records()
    assert (record["status"], record["reason"]) == ("ended", "wave_went_backwards")
    assert fields(run, "attach_boundary") == [dict(reason="wave_went_backwards", previous_wave=500, wave=10)]
    assert [name for name, _ in run.calls].count("watch") == 0          # nothing acted on the new battle


def test_the_baseline_waits_for_the_tracker_to_confirm_and_ignores_small_moves(world, monkeypatch, tmp_path):
    run = drive(world, monkeypatch, tmp_path, [frame(wave=500), frame(wave=10), frame(wave=10)], ["refresh"])
    assert run.outcome == "exhausted" and fields(run, "attach_boundary") == []          # two reads are probation, not a boundary
    run = drive(World(tmp_path / "second"), monkeypatch, tmp_path / "second", [frame(wave=500), frame(wave=450), frame(wave=450)], ["refresh", "refresh"])
    assert fields(run, "attach_boundary") == []                                         # inside the 100-wave margin


def test_an_ordinary_run_has_no_baseline_before_its_first_frame(world, monkeypatch, tmp_path):
    run = drive(world, monkeypatch, tmp_path, [frame(wave=500), frame(wave=10), frame(wave=10), frame(wave=10)], ["refresh", "refresh", "refresh"], ordinary=True)
    # the ordinary tracker accepts the first read it sees, so the run keeps going on the 10 (all frames read, never ended)
    assert "attach_boundary" not in names(run) and run.outcome == "exhausted" and run.frames_read == 5 and world.records()[0]["status"] == "active"


# ------------------------------------------------------------------ nothing may end a battle
def test_every_surrender_path_asks_the_one_lock_and_an_attached_battle_is_locked(monkeypatch):
    import orchestrator as orch
    from vision import screen
    monkeypatch.setattr(orch, "preset", lambda: {"kind": "coin", "max_wave": 50})
    monkeypatch.setattr(orch, "CONFIG", {"preset": RUN})
    monkeypatch.setattr(screen, "in_tournament", lambda f: False)
    monkeypatch.setattr(orch, "_attach", None)
    assert orch._tournament_locked(object()) is None
    monkeypatch.setattr(orch, "_attach", SimpleNamespace(tier=None))
    assert orch._tournament_locked(object()) == "battle not started by this run"


def test_max_wave_and_the_surrender_rule_refuse_on_an_attached_battle(monkeypatch):
    import orchestrator as orch
    from flows import shard
    from vision import screen
    events = []
    monkeypatch.setattr(orch, "preset", lambda: {"kind": "coin", "max_wave": 50})
    monkeypatch.setattr(orch, "CONFIG", {"preset": RUN})
    monkeypatch.setattr(orch, "_attach", SimpleNamespace(tier=None))
    monkeypatch.setattr(screen, "in_tournament", lambda f: False)
    monkeypatch.setattr(orch.logger, "event", lambda kind, **f: events.append(kind))
    monkeypatch.setattr(orch.logger, "shot", lambda *a, **k: "shot")
    monkeypatch.setattr(shard, "abandon_run", lambda **k: pytest.fail("a takeover surrendered the battle"))
    monkeypatch.setattr(orch, "_rule_retire", lambda rs, i: None)
    rs = SimpleNamespace(max_wave_done=False, tracker=SimpleNamespace(last=60))
    assert orch.max_wave_reached(rs, object(), 60) is False
    assert orch._rule_surrender(rs, object(), 0, "r") == (False, False)
    assert events == ["max_wave_refused", "rule_refused"]


def test_a_takeover_never_starts_from_home(monkeypatch):
    import orchestrator as orch
    from device import capture
    from interactions import loadout
    from flows import shard
    monkeypatch.setattr(orch, "preset", lambda: {"kind": "coin", "tier": 14, "loadout": {"cards": "x"}})
    monkeypatch.setattr(orch, "_attach", SimpleNamespace(tier=None))
    monkeypatch.setattr(capture, "grab", lambda: pytest.fail("start preparation captured a frame"))
    monkeypatch.setattr(loadout, "apply", lambda *a, **k: pytest.fail("equipment changed"))
    monkeypatch.setattr(shard, "set_tier", lambda *a, **k: pytest.fail("tier dialled"))
    assert orch._prepare_coin_start() is None


# ------------------------------------------------------------------ tier
def test_an_unknown_tier_has_no_fleet_marks_but_a_declared_one_uses_the_table(monkeypatch):
    import orchestrator as orch
    monkeypatch.setattr(orch, "preset", lambda: {"kind": "coin", "tier": 14})
    monkeypatch.setattr(orch, "_battle_session", None)
    monkeypatch.setattr(orch, "CONFIG", {"preset": RUN, "fleet": {"first_wave": 750, "interval": 750}})
    monkeypatch.setattr(orch, "_attach", None)
    assert orch.run_tier() == 14 and orch.marks()                                    # the ordinary run reads its preset
    monkeypatch.setattr(orch, "_attach", SimpleNamespace(tier=None))
    assert orch.run_tier() is None and orch.marks() == []                            # unknown: no legacy fallback either
    monkeypatch.setattr(orch, "_attach", SimpleNamespace(tier=18))
    assert orch.run_tier() == 18 and 95 in orch.marks() and 195 in orch.marks()


def test_every_fleet_consumer_reads_marks_and_nothing_else_names_a_schedule():
    source = ORCHESTRATOR.read_text(encoding="utf-8")
    assert sorted(set(re.findall(r"fleets\.(\w+)", source))) == ["marks", "schedule"]
    assert len(re.findall(r"in marks\(\)", source)) == 5                             # the five loops, none reading a table of its own
    assert len(re.findall(r"fleets\.schedule\(", source)) == 1                       # the startup log, guarded for an unknown tier


def test_the_startup_log_names_no_timetable_for_an_unknown_tier(world, monkeypatch, tmp_path):
    run = drive(world, monkeypatch, tmp_path, [frame(), frame()])
    assert fields(run, "fleet_schedule") == [{"tier": None, "source": "none"}]


# ------------------------------------------------------------------ the live controls and the run flag
def test_an_attached_battle_never_repeats_and_follows_the_frozen_rewards_control(world, monkeypatch, tmp_path):
    run = drive(world, monkeypatch, tmp_path, [frame(), frame()])
    assert run.orch._attach is not None
    rs = run.orch.RunState()
    assert rs.repeat_enabled is False and rs.rewards_enabled is False


def test_the_operation_worker_keeps_the_scheduler_stop_flag_for_a_takeover(world, monkeypatch, tmp_path):
    import sys
    from runtime import device_lease, operation_worker
    flag = Path(world.operations.path).parent / "main" / "stop_after_run"
    flag.parent.mkdir(parents=True)
    world.attach_operation(running=False)
    plain, _ = world.operations.accept("run", {**world.context, "device_key": "other-endpoint"}, {"battle_envelope": {"preset_key": RUN}})
    for op, kept in ((world.op, True), (plain, False)):
        flag.write_text("combo -> shards", encoding="utf-8")
        monkeypatch.setenv("TOWER_OPERATION_ID", op["id"])
        monkeypatch.setenv("TOWER_OPERATION_DB", world.operations.path)
        monkeypatch.setattr(device_lease, "acquire", lambda *a: None)
        monkeypatch.setattr(operation_worker, "restore_worker_context", lambda settings: None)
        monkeypatch.setattr(operation_worker.runpy, "run_path", lambda path, run_name: None)
        monkeypatch.setattr(sys, "argv", ["operation_worker", "fake_target.py"])
        operation_worker.main()
        assert flag.exists() is kept


# ------------------------------------------------------------------ frames a helper captured for itself
def test_the_attach_identity_guard_holds_input_below_the_confirmed_battle_only_for_a_takeover(world, monkeypatch, tmp_path):
    run = drive(world, monkeypatch, tmp_path, [frame(), frame()])
    orch = run.orch
    rs = SimpleNamespace(wave_hwm=500)
    guard = orch._attach_identity_guard(rs)
    assert guard(frame(wave=400), "battle")["wave"] == 400                              # exactly the margin: still that battle
    assert guard(frame(wave=None), "battle")["wave"] is None                            # unreadable: the screen checks decide, not this guard
    assert guard(frame(wave=10), "home")["wave"] == 10                                  # only a battle frame has a wave to compare
    with pytest.raises(orch.AttachReplaced):
        guard(frame(wave=399), "battle")
    rs.wave_hwm = None
    assert guard(frame(wave=10), "battle")["wave"] == 10                                # no baseline yet: nothing to compare
    monkeypatch.setattr(orch, "_attach", None)
    rs.wave_hwm = 500
    assert guard(frame(wave=10), "battle")["wave"] == 10                                # an ordinary run owns every battle it meets


def test_the_gem_collectors_of_a_takeover_carry_the_guard_and_hold_a_replaced_battle(world, monkeypatch, tmp_path):
    run = drive(world, monkeypatch, tmp_path, [frame(), frame()])
    rs = run.orch.RunState()
    rs.wave_hwm = 500
    assert rs.gems.guard is not None and rs.gems.ad_gems.guard is rs.gems.guard and rs.gems.orbit.guard is rs.gems.guard
    with pytest.raises(run.orch.AttachReplaced):
        rs.gems.poll(frame(wave=10))
    assert run.taps == []
    plain = drive(World(tmp_path / "ordinary"), monkeypatch, tmp_path / "ordinary", [frame(), frame()], ordinary=True)
    assert plain.orch.RunState().gems.guard is None


def watch_setup(monkeypatch, orch, rs, wall):
    """The real `_fast_wall_watch` over scripted wall readings: `wall` is the (extent, state) of each frame."""
    monkeypatch.setattr(orch, "automation_enabled", lambda key: True)
    monkeypatch.setattr(orch.detect, "wall_overheal", lambda f: f["wall"])
    monkeypatch.setattr(orch.detect, "find_intro_sprint", lambda f: pytest.fail("the burst started"))
    monkeypatch.setattr(orch.detect, "button_state", lambda f, name: pytest.fail("the burst started"))
    monkeypatch.setattr(orch, "marks", lambda: [])
    rs.post_sw_until = float("inf")
    return [{**frame(wave=500), "wall": wall[0]}, {**frame(wave=10), "wall": wall[1]}]


def test_the_fast_wall_watch_sends_no_burst_into_a_replaced_battle(world, monkeypatch, tmp_path):
    from device import capture
    run = drive(world, monkeypatch, tmp_path, [frame(), frame()])
    orch = run.orch
    rs = orch.RunState()
    rs.wave_hwm = 500
    frames = watch_setup(monkeypatch, orch, rs, [(1.0, "normal"), (0.0, "rebuilding")])    # a full wall, then the broken banner: a collapse the burst answers
    script = iter(frames)
    monkeypatch.setattr(capture, "grab", lambda: next(script))
    assert orch._fast_wall_watch(rs, {"dm_below": 0.2}) == "boundary_pending"
    assert run.taps == []


def test_the_fast_wall_watch_follows_the_waves_it_confirms_for_a_takeover_only(world, monkeypatch, tmp_path):
    from device import capture
    run = drive(world, monkeypatch, tmp_path, [frame(), frame()])
    orch = run.orch
    rs = orch.RunState()
    rs.wave_hwm = 500
    rs.tracker.confirm = 1
    watch_setup(monkeypatch, orch, rs, [(1.0, "immune"), (1.0, "immune")])
    monkeypatch.setattr(orch.time, "monotonic", lambda: 0.0)
    for kind, attach in (("takeover", True), ("ordinary", False)):
        monkeypatch.setattr(orch, "_attach", SimpleNamespace(tier=None) if attach else None)
        rs.wave_hwm = 500
        script = iter([{**frame(wave=900), "wall": (1.0, "normal")}] * 7 + [{**frame(wave=900), "wall": (1.0, "immune")}])
        monkeypatch.setattr(capture, "grab", lambda: next(script))
        assert orch._fast_wall_watch(rs, {"dm_below": 0.2}) == "immune"
        assert rs.wave_hwm == (900 if attach else 500), kind


@pytest.mark.parametrize("outcome", ["boundary_pending", "off", "fired", "immune", "timeout"])
def test_a_takeover_discards_the_frame_a_fast_watch_outlived_before_any_other_input(world, monkeypatch, tmp_path, outcome):
    import orchestrator
    real_watch_frame = orchestrator.watch_frame                                         # `drive` replaces it with the scripted monitor
    run = drive(world, monkeypatch, tmp_path, [frame(), frame()])
    orch = run.orch
    monkeypatch.setattr(orch, "watch_frame", real_watch_frame)
    rs = orch.RunState()
    rs.gems.poll = lambda *a, **k: pytest.fail("a gem poll ran on a frame older than the fast watch's")
    monkeypatch.setattr(orch.detect, "death_screen", lambda f: (False, None))
    monkeypatch.setattr(orch.detect, "second_wind_badge", lambda f: (False, 0.0))
    monkeypatch.setattr(orch, "preset", lambda: {"abilities": {"rescue_bar": "wall", "hold_until_second_wind": False, "dm_below": 0.2}})
    monkeypatch.setattr(orch, "demon_mode_always", lambda *a: None)
    monkeypatch.setattr(orch, "marks", lambda: [])
    monkeypatch.setattr(orch, "automation_enabled", lambda key: True)
    monkeypatch.setitem(orch.CONFIG, "rois", {"wall_bar": None})
    monkeypatch.setattr(orch, "_fast_wall_watch", lambda rs, ab: outcome)
    assert orch.watch_frame(rs, frame(wave=500), observation={"raw_wave": 500}) == "refresh"
    polled = []
    rs.gems.poll = lambda f, reads=None: polled.append(f) or f
    monkeypatch.setattr(orch, "_attach", None)                                          # an ordinary run keeps today's order: the result is ignored
    assert orch.watch_frame(rs, frame(wave=500), observation={"raw_wave": 500}) is False and polled


def test_a_lower_wave_read_by_the_fast_watch_never_lowers_the_boundary_baseline(world, monkeypatch, tmp_path):
    from device import capture
    run = drive(world, monkeypatch, tmp_path, [frame(), frame()])
    orch = run.orch
    rs = orch.RunState()
    rs.wave_hwm = 500
    rs.tracker.last = 500
    rs.tracker.confirm = 1                                                              # the tracker accepts the drop at once; the baseline must still stay
    watch_setup(monkeypatch, orch, rs, [(1.0, "normal"), (1.0, "normal")])
    script = iter([{**frame(wave=500), "wall": (1.0, "normal")}] * 7 + [{**frame(wave=10), "wall": (1.0, "normal")}])
    monkeypatch.setattr(capture, "grab", lambda: next(script))
    assert orch._fast_wall_watch(rs, {"dm_below": 0.2}) == "boundary_pending"
    assert rs.wave_hwm == 500 and run.taps == []


def test_the_ad_gem_collector_verification_capture_is_judged_by_the_guard_and_sends_no_tap(world, monkeypatch, tmp_path):
    import time
    from device import capture
    run = drive(world, monkeypatch, tmp_path, [frame(), frame()])
    orch = run.orch
    rs = orch.RunState()
    rs.wave_hwm = 500
    ad = rs.gems.ad_gems
    ad._attempt_started_at, ad._attempt_timing = time.monotonic(), {}
    monkeypatch.setattr(capture, "grab", lambda: frame(wave=10))                         # the battle was replaced after the main loop's frame (wave 500)
    with pytest.raises(orch.AttachReplaced):
        ad._claim(frame(wave=500), (100, 100), 0.0, None)                                # an Abort for the collector: ownership lost, re-raised to the main loop
    assert run.taps == [] and ad.last_outcome["reason"] == "ownership_lost"


def test_input_held_by_the_guard_is_logged_and_the_next_frame_is_judged_not_a_crash(world, monkeypatch, tmp_path):
    import orchestrator
    run = drive(world, monkeypatch, tmp_path, [frame(wave=500), frame(wave=500), frame(wave=500)], [orchestrator.AttachReplaced("wave below the confirmed battle"), "refresh"])
    assert fields(run, "attach_input_held") == [{"reason": "wave below the confirmed battle"}]
    assert "crash" not in names(run) and run.taps == [] and run.frames_read >= 3
