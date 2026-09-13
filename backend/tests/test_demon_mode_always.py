"""`policies.global_behaviors.demon_mode_always`: fire Demon Mode whenever it
is ready, on every run, whatever the rescue policy does (user, 2026-09-14:
"we need to be able to click and kill demon mode enemies so we can fulfil a
quest"). Global like the reward clocks, in-battle unlike them."""
import types

import pytest

from scheduling import global_rewards


def test_key_is_a_global_behaviour_but_not_a_reward_clock():
    assert "demon_mode_always" in global_rewards.KEYS
    assert "demon_mode_always" not in global_rewards.REWARD_KEYS
    assert set(global_rewards.REWARD_KEYS) == {"free_store_gems", "daily_missions",
                                              "event_missions", "guild_progress"}
    assert global_rewards.enabled({"global_behaviors": {"demon_mode_always": True}}, "demon_mode_always")
    assert not global_rewards.enabled({"global_behaviors": {}}, "demon_mode_always")


def test_profile_validation_accepts_the_switch_and_refuses_a_non_bool():
    from player import playerprofile as pp
    prof = pp.load("default")
    prof["policies"]["global_behaviors"]["demon_mode_always"] = True
    assert not [p for p in pp.validate(prof) if "global_behaviors" in p]
    prof["policies"]["global_behaviors"]["demon_mode_always"] = "yes"
    assert any("demon_mode_always" in p for p in pp.validate(prof))


def test_compiler_requires_demon_mode_on_the_account_when_switched_on():
    from player import playerprofile as pp
    compiled = {"abilities": {"rescue_bar": None}, "rules": [],
                "global_behaviors": {"demon_mode_always": True}}
    assert "demon_mode" in pp.required_capabilities(compiled)["abilities"]
    compiled["global_behaviors"]["demon_mode_always"] = False
    assert "demon_mode" not in pp.required_capabilities(compiled)["abilities"]


def test_readiness_needs_the_button_image_only_when_switched_on():
    from player import readiness
    body = {"kind": "coin", "gather": {}, "global_behaviors": {"demon_mode_always": True}}
    rows = {tuple(r["alternatives"]): r for r in readiness.requirements({}, body)}
    assert rows[("buttons/demon_mode.png",)]["blocking"] is True
    assert "Demon Mode on cooldown (global behaviour)" in rows[("buttons/demon_mode.png",)]["reasons"]
    body["global_behaviors"]["demon_mode_always"] = False
    rows = {tuple(r["alternatives"]): r for r in readiness.requirements({}, body)}
    assert ("buttons/demon_mode.png",) not in rows


@pytest.fixture()
def orch(monkeypatch):
    import orchestrator
    fired, events = [], []
    monkeypatch.setattr(orchestrator, "preset", lambda: {"global_behaviors": {"demon_mode_always": True}})
    monkeypatch.setattr(orchestrator, "fire_button",
                        lambda frame, name, reason, require_ready=True: fired.append((name, reason, require_ready)) or True)
    monkeypatch.setattr(orchestrator.logger, "event", lambda kind, **kw: events.append((kind, kw)))
    monkeypatch.setattr(orchestrator.detect, "intro_sprint_active", lambda frame: False)
    rs = types.SimpleNamespace(dm_always_try_at=0.0, dm_always_count=0, sprint_ended=False)
    return orchestrator, rs, fired, events


def test_fires_when_ready_confirms_and_spaces_attempts(orch):
    orchestrator, rs, fired, events = orch
    assert orchestrator.demon_mode_always(rs, "F", 2500, 100.0) is True
    assert fired == [("demon_mode", "demon_mode_always", True)]
    assert events[-1] == ("demon_mode_always", {"wave": 2500, "count": 1})
    assert orchestrator.demon_mode_always(rs, "F", 2501, 105.0) is False     # inside the 15 s grain
    assert len(fired) == 1
    assert orchestrator.demon_mode_always(rs, "F", 2600, 100.0 + orchestrator.DM_ALWAYS_RETRY_SEC) is True
    assert rs.dm_always_count == 2


def test_never_during_the_intro_sprint_and_never_without_a_wave(orch, monkeypatch):
    orchestrator, rs, fired, events = orch
    monkeypatch.setattr(orchestrator.detect, "intro_sprint_active", lambda frame: True)
    assert orchestrator.demon_mode_always(rs, "F", 40, 100.0) is False and fired == []
    rs.sprint_ended = True                                   # the run ended its sprint: the row is free
    assert orchestrator.demon_mode_always(rs, "F", 41, 200.0) is True
    assert orchestrator.demon_mode_always(rs, "F", None, 400.0) is False     # no readable wave: not a battle


def test_switched_off_never_touches_the_button(orch, monkeypatch):
    orchestrator, rs, fired, events = orch
    monkeypatch.setattr(orchestrator, "preset", lambda: {"global_behaviors": {"demon_mode_always": False}})
    assert orchestrator.demon_mode_always(rs, "F", 2500, 100.0) is False and fired == []
    monkeypatch.setattr(orchestrator, "preset", lambda: {})
    assert orchestrator.demon_mode_always(rs, "F", 2500, 200.0) is False and fired == []


def test_unconfirmed_tap_is_retried_later_not_counted(orch, monkeypatch):
    orchestrator, rs, fired, events = orch
    monkeypatch.setattr(orchestrator, "fire_button", lambda *a, **k: False)
    assert orchestrator.demon_mode_always(rs, "F", 2500, 100.0) is False
    assert rs.dm_always_count == 0 and rs.dm_always_try_at == 100.0 + orchestrator.DM_ALWAYS_RETRY_SEC


def test_the_switch_alone_does_not_force_the_side_menu_open():
    """Only the reward clocks want the side menu; Demon Mode is on the HUD."""
    g = {"demon_mode_always": True}
    assert not any(g.get(k) is True for k in global_rewards.REWARD_KEYS)
