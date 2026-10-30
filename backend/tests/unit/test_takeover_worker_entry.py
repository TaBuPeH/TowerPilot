"""Takeover worker: the accepted operation, the entry gate and the durable predecessor (roadmap item 3, T3b). Offline: no device, no screen."""
import copy

import pytest

from takeover_world import RUN, World, frame, readers
from player import attach_policy
from runtime import attach_worker
from runtime.battle_session import BattleSession


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = World(tmp_path)
    monkeypatch.setenv("TOWER_OPERATION_DB", w.operations.path)
    monkeypatch.delenv("TOWER_BATTLE_ID", raising=False)
    monkeypatch.delenv("TOWER_BATTLE_GENERATION", raising=False)
    return w


def attach_of(world, **kwargs):
    op = world.attach_operation(**kwargs)
    restricted = op["request"]["battle_envelope"]["preset"]
    return attach_worker.from_operation(world.operations.get(op["id"]), restricted, world.cfg)


# ------------------------------------------------------------------ the accepted operation
def test_an_ordinary_run_has_no_attach(world):
    op, _ = world.operations.accept("run", world.context, {"battle_envelope": {"preset_key": RUN}})
    assert attach_worker.from_operation(op, {}, world.cfg) is None


def test_a_valid_takeover_reads_tier_and_predecessor(world):
    attach = attach_of(world, tier=18)
    assert attach.tier == 18 and attach.predecessor is None
    assert attach.block["tier"] == {"value": 18, "provenance": "declared"}


def test_an_unknown_tier_is_none_not_the_presets(world):
    assert world.cfg["presets"][RUN]["tier"] == 14
    assert attach_of(world).tier is None


@pytest.mark.parametrize("break_it", [
    lambda r: r.pop("attach"),                                           # envelope block without the request
    lambda r: r["battle_envelope"].pop("attach"),                        # request without the envelope block
    lambda r: (r["battle_envelope"]["config"]["presets"].update(other=dict(r["battle_envelope"]["preset"])), r["battle_envelope"].update(preset_key="other")),
    lambda r: r["battle_envelope"]["attach"].update(policy_revision="0" * 64),
    lambda r: r["battle_envelope"]["preset"].update(loadout={"cards": "x"}),   # still carries a stripped key
    lambda r: r["battle_envelope"]["config"]["presets"][RUN].update(perk_bans=["a"]),     # only the frozen configuration still carries one
    lambda r: r["battle_envelope"]["controls"]["routines"]["repeat"].update(enabled=True),
    lambda r: r["battle_envelope"]["controls"]["routines"]["rewards"].update(enabled=True),
    lambda r: r["battle_envelope"]["attach"].update(unexpected=1),
    lambda r: r["attach"].update(attestation="no"),
], ids=["no_request_attach", "no_envelope_attach", "other_run", "revision_disagrees", "stripped_key", "frozen_config_stripped_key", "repeat_on", "rewards_on", "unknown_field", "attestation"])
def test_an_untrustworthy_operation_is_refused(world, break_it):
    op = world.attach_operation(mutate=break_it)
    restricted = op["request"]["battle_envelope"]["preset"]
    with pytest.raises(attach_worker.AttachRefused) as refusal:
        attach_worker.from_operation(world.operations.get(op["id"]), restricted, world.cfg)
    assert refusal.value.code == "attach_envelope_invalid"


def test_only_a_run_operation_may_attach(world):
    op = world.attach_operation()
    world.operations.transition(op["id"], op["revision"], "failed", error={"code": "x", "message": "x"})
    gems, _ = world.operations.accept("gems", world.context, op["request"])
    with pytest.raises(attach_worker.AttachRefused) as refusal:
        attach_worker.from_operation(gems, op["request"]["battle_envelope"]["preset"], world.cfg)
    assert refusal.value.code == "attach_envelope_invalid"


def test_a_delegated_child_cannot_attach(world):
    op = world.attach_operation()
    with pytest.raises(attach_worker.AttachRefused) as refusal:
        attach_worker.from_operation(world.operations.get(op["id"]), op["request"]["battle_envelope"]["preset"], world.cfg, delegated=True)
    assert refusal.value.code == "attach_envelope_invalid"


# ------------------------------------------------------------------ the entry gate
def gate(attach, value, **kwargs):
    revoked = kwargs.pop("revoked", False)
    return attach_worker.entry_gate(attach, value, identify=lambda f: f["screen"], read_wave=lambda f: f["wave"],
                                    tournament_state=lambda f: f["tournament"], input_revoked=lambda: revoked)


def code_of(attach, value, **kwargs):
    with pytest.raises(attach_worker.AttachRefused) as refusal:
        gate(attach, value, **kwargs)
    return refusal.value.code


def test_a_battle_with_a_readable_wave_and_no_trophy_passes(world):
    assert gate(attach_of(world), frame(wave=77)) == 77


@pytest.mark.parametrize("value,code", [
    (frame(screen="home"), "attach_no_battle"),
    (frame(screen="unknown"), "attach_no_battle"),
    (frame(wave=None), "attach_no_battle"),
    (frame(tournament="yes"), "attach_tournament"),
    (frame(tournament="unknown"), "attach_tournament_unknown"),
    (frame(tournament="maybe"), "attach_tournament_unknown"),
])
def test_the_gate_refuses_with_its_code(world, value, code):
    assert code_of(attach_of(world), value) == code


def test_a_revoked_input_refuses(world):
    assert code_of(attach_of(world), frame(), revoked=True) == "attach_input_revoked"


def test_the_wave_floor_is_the_confirmed_wave_minus_the_margin(world):
    attach = attach_of(world, observed_wave=500)
    assert gate(attach, frame(wave=400)) == 400                           # exactly the margin: still that battle
    assert code_of(attach, frame(wave=399)) == "attach_wave_mismatch"
    assert gate(attach, frame(wave=9000)) == 9000                         # a battle that moved on is the same battle


def test_no_confirmed_wave_means_no_floor(world):
    assert gate(attach_of(world), frame(wave=1)) == 1


@pytest.mark.parametrize("wave,stored", [(0, 0), (77, 77), (None, None), (-1, None), (True, None), (12.5, None), ("40", None)])
def test_the_entry_record_keeps_only_a_real_wave_and_otherwise_says_unknown(wave, stored):
    assert attach_worker.entry_record(wave) == {"observed_wave": stored, "source": "entry_gate"}


# ------------------------------------------------------------------ the durable predecessor
def settle(world, attach_pred):
    from towerpilot_workflows.operations import Operations
    return attach_worker.settle_predecessor(world.store, Operations(world.operations.path), attach_pred, world.context)


def test_no_collector_means_no_predecessor(world):
    assert settle(world, None) is None


def test_a_live_record_of_an_ended_collector_is_closed_and_linked(world):
    old = world.collector()
    assert settle(world, world.predecessor(old)) == old["id"]
    done = world.store.get(old["id"])
    assert (done["status"], done["reason"]) == ("interrupted", "driver_operation_ended")


def test_a_record_the_refresher_already_interrupted_is_still_linked(world):
    old = world.collector(finish="reconcile")
    assert settle(world, world.predecessor(old)) == old["id"]


def test_a_collector_still_running_is_not_closed(world):
    old = world.collector(owner=None)
    with pytest.raises(attach_worker.AttachRefused) as refusal:
        settle(world, world.predecessor(old))
    assert refusal.value.code == "attach_predecessor_mismatch"
    assert world.store.get(old["id"])["status"] == "active"


def test_a_conflicting_finalization_counts_as_finalized(world, monkeypatch):
    """The refresher finishes the record between our read and our finish, with another reason: the store's own Conflict is finalization, not failure."""
    old = world.collector()
    real = type(world.store).finish

    def raced(self, battle_id, generation, **kwargs):
        real(self, battle_id, generation, reason="driver_observation_lost", status="interrupted")
        return real(self, battle_id, generation, **kwargs)

    monkeypatch.setattr(type(world.store), "finish", raced)
    assert settle(world, world.predecessor(old)) == old["id"]
    assert world.store.get(old["id"])["reason"] == "driver_observation_lost"


@pytest.mark.parametrize("break_it", [
    lambda p, b: p.update(battle_id="missing"),
    lambda p, b: p.update(operation_id="another-operation"),
    lambda p, b: p.update(generation="another-generation"),
], ids=["unknown_record", "wrong_operation", "wrong_generation"])
def test_a_wrong_named_record_refuses_and_closes_nothing(world, break_it):
    old = world.collector()
    named = world.predecessor(old)
    break_it(named, old)
    with pytest.raises(attach_worker.AttachRefused) as refusal:
        settle(world, named)
    assert refusal.value.code == "attach_predecessor_mismatch"
    assert world.store.get(old["id"])["status"] == "active"


def test_another_account_or_emulator_refuses(world):
    old = world.collector()
    world.context["account"] = "someone-else"
    with pytest.raises(attach_worker.AttachRefused):
        settle(world, world.predecessor(old))
    assert world.store.get(old["id"])["status"] == "active"


def test_a_record_that_ended_for_another_reason_is_not_continuity(world):
    old = world.collector()
    world.store.finish(old["id"], old["generation"], reason="observed_results")
    with pytest.raises(attach_worker.AttachRefused):
        settle(world, world.predecessor(old))


# ------------------------------------------------------------------ the session
def session_for(world, attach, monkeypatch):
    monkeypatch.setenv("TOWER_OPERATION_ID", world.op["id"])
    preset = world.op["request"]["battle_envelope"]["preset"]
    return BattleSession.from_environment(preset, world.cfg, origin="adopted", attach=attach)


def test_the_successor_links_exactly_the_named_collector_record(world, monkeypatch):
    old = world.collector(finish="reconcile")           # the refresher got there first: store.active() no longer finds it
    attach = attach_of(world, tier=18, predecessor=world.predecessor(old))
    session = session_for(world, attach, monkeypatch)
    assert session.battle["previous_battle_id"] == old["id"] and session.battle["origin"] == "partial_adoption"
    assert session.attach["tier"]["value"] == 18
    assert session.battle["envelope"]["attach"]["predecessor"]["battle_id"] == old["id"]


def test_the_worker_before_the_refresher_gives_the_same_link(world, monkeypatch):
    old = world.collector()                            # still live when the worker starts
    attach = attach_of(world, predecessor=world.predecessor(old))
    session = session_for(world, attach, monkeypatch)
    assert session.battle["previous_battle_id"] == old["id"]
    assert world.store.get(old["id"])["status"] == "interrupted"


def test_no_collector_creates_an_unlinked_adopted_record(world, monkeypatch):
    attach = attach_of(world)
    session = session_for(world, attach, monkeypatch)
    assert session.battle["previous_battle_id"] is None and session.battle["origin"] == "adopted"


def test_a_record_found_by_rediscovery_is_never_linked_to_a_takeover(world, monkeypatch):
    stray = world.collector()                          # a record whose operation ended, but the server named none
    attach = attach_of(world)
    session = session_for(world, attach, monkeypatch)
    assert session.battle["previous_battle_id"] is None
    assert world.store.get(stray["id"])["status"] == "interrupted"      # still closed, as for any ended owner


def test_a_failed_predecessor_check_creates_no_record(world, monkeypatch):
    old = world.collector()
    named = world.predecessor(old)
    named["operation_id"] = "another-operation"
    attach = attach_of(world, predecessor=named)
    before = len(world.records())
    with pytest.raises(attach_worker.AttachRefused):
        session_for(world, attach, monkeypatch)
    assert len(world.records()) == before


def test_repeat_is_not_offered_to_an_attached_battle(world, monkeypatch):
    from types import SimpleNamespace
    attach = attach_of(world)
    session = session_for(world, attach, monkeypatch)
    rs = SimpleNamespace(shop=SimpleNamespace(plan=None), repeat_enabled=False, rewards_enabled=False)
    bindings = session.bindings(rs)
    assert "repeat" not in bindings and "rewards" in bindings
    assert "routines.repeat.enabled" not in session.capabilities(rs)["controls"]


def test_an_ordinary_session_still_offers_repeat(world, monkeypatch):
    from types import SimpleNamespace
    op, _ = world.operations.accept("run", world.context, {})
    world.operations.transition(op["id"], op["revision"], "running", worker={"pid": 3, "creation_time": 3})
    monkeypatch.setenv("TOWER_OPERATION_ID", op["id"])
    session = BattleSession.from_environment(copy.deepcopy(world.cfg["presets"][RUN]), world.cfg)
    assert session.attach is None
    assert "repeat" in session.bindings(SimpleNamespace(shop=SimpleNamespace(plan=None), repeat_enabled=False, rewards_enabled=False))


def test_refuse_records_the_code_and_writes_no_battle(world):
    op = world.attach_operation()
    attach_worker.refuse(world.operations, op["id"], attach_worker.AttachRefused("attach_no_battle", "x"))
    done = world.operations.get(op["id"])
    assert done["status"] == "failed" and done["error"]["code"] == "attach_no_battle"
    assert world.records() == []
