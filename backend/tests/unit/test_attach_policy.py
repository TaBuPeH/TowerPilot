"""Takeover policy (roadmap item 3, T3a): the pure rules player/attach_policy.py owns.

Real compiled presets come from the golden profile fixture (the compiler's regression account); the rest are hand-made mappings. No device, no screen."""
import ast
import copy
import os
import random
import re
import subprocess
import sys
from pathlib import Path

import pytest

import paths as test_paths
from goldens import load_golden
from player import attach_policy as policy, battle_end, playerprofile, run_controls

BACKEND = test_paths.BACKEND
GOOD_REVISION = "ab" * 32


def compiled(name, edit=None):
    profile = load_golden()
    if edit:
        edit(profile)
    return playerprofile.compile_preset(profile, name)


def add_rescue_rule(rule):
    return lambda profile: profile["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(rule)


def codes(reasons):
    return [(r["code"], r["field"]) for r in reasons]


# ------------------------------------------------------------------ compatibility, strip, needs-tier
@pytest.mark.parametrize("name, expected", [
    ("coin_default", []),
    ("coin_dissonance", []),
    ("coin_t18_legend", []),
    ("tourney_main", [("tournament", "kind"), ("tournament", "tournament_setup")]),
    ("tourney_assist", [("tournament", "kind"), ("tournament", "tournament_setup")]),
    ("shard_daily", [("ends_battle", "kind")]),
    ("quest_sm", [("ends_battle", "kind")]),
    ("quest_ilm", [("ends_battle", "kind")]),
])
def test_strip_and_refuse_matrix_compiled_runs(name, expected):
    """Every blueprint of the golden account, compiled by the real compiler, is judged the way the rules document says."""
    assert codes(policy.compatibility(compiled(name))) == expected


def test_strip_and_refuse_matrix():
    """One table of the refusal classes: compiled variants and hand-made mappings side by side."""
    max_wave = compiled("coin_default", lambda p: p["blueprints"]["coin_default"].update(max_wave=700))
    surrender = compiled("coin_default", add_rescue_rule({"when": {"wave_at_least": 5000}, "do": {"surrender_retry": True}}))
    cases = [
        # (preset, preset_key, expected reasons)
        (max_wave, None, [("ends_battle", "max_wave")]),
        (surrender, None, [("ends_battle", "rules[0].do")]),
        ({"kind": "coin", "max_wave": 0}, None, [("ends_battle", "max_wave")]),         # a cap of wave 0 still ends the run (orchestrator tests `limit is None`)
        ({"kind": "coin", "max_wave": None, "rules": []}, None, []),
        ({"kind": "coin", "tournament_setup": True}, None, [("tournament", "tournament_setup")]),
        ({"kind": "coin"}, "tournament", [("tournament", "preset")]),                  # orchestrator._tournament_locked: CONFIG["preset"] == "tournament"
        ({"kind": "tournament", "max_wave": 9}, None, [("tournament", "kind"), ("ends_battle", "max_wave")]),
        ({"kind": "weird_extension"}, None, [("unsupported_kind", "kind")]),
        ({"runner": "flows/shard.py"}, None, [("unsupported_kind", "runner")]),        # no kind, but a script drives it (readiness reads such a body as a shard run)
        ({"kind": "coin", "runner": None}, None, []),
        ({"kind": "coin", "runner": "orchestrator.py"}, None, []),
        ({"kind": "coin", "runner": "interactions/shatter.py"}, None, [("unsupported_kind", "runner")]),   # the run command would start that script: no entry gate, no restricted controls
        ({}, None, []),                                                                 # a legacy preset without a kind is a coin run
        ({"rules": [{"do": {"kind": "fire"}}, {"when": {}, "do": {"kind": "surrender_retry"}}]}, None, [("ends_battle", "rules[1].do")]),
        # the older spellings the orchestrator also executes (`_rule_do`): a takeover fails closed on them, readiness never listed images for them
        ({"rules": [{"do": {"surrender_retry": True}}]}, None, [("ends_battle", "rules[0].do")]),
        ({"rules": [{"do": {"action": "surrender_retry"}}, {"do": {"fire": "nuke"}}]}, None, [("ends_battle", "rules[0].do")]),
        ({"rules": [{"do": {"kind": "surrender_retry", "surrender_retry": True}}]}, None, [("ends_battle", "rules[0].do")]),          # one reason per rule, not per spelling
    ]
    for preset, key, expected in cases:
        assert codes(policy.compatibility(preset, key)) == expected, preset
    reason = policy.compatibility(max_wave)[0]                  # the reasons name what to fix
    assert set(reason) == {"code", "field", "detail"} and "700" in reason["detail"]


def test_compatibility_does_not_change_its_input():
    preset = compiled("coin_default", add_rescue_rule({"when": {"wave_at_least": 5000}, "do": {"surrender_retry": True}}))
    before = copy.deepcopy(preset)
    policy.compatibility(preset, "bp_x")
    policy.plan(preset)
    policy.unrestricted_keys(preset)
    assert preset == before


def test_the_stripped_keys_are_keys_the_compiler_emits():
    """A renamed or retired compiled key must fail here, not silently stop being stripped."""
    emitted = set()
    for name in load_golden()["blueprints"]:
        emitted |= set(compiled(name))
    assert set(policy.STRIPPED_KEYS) <= emitted, sorted(set(policy.STRIPPED_KEYS) - emitted)


def test_every_stripped_key_is_still_read_where_the_rules_document_says():
    """The entry code that reads each stripped key. If one of these reads disappears the key no longer belongs in the table."""
    sources = {rel: (BACKEND / rel).read_text(encoding="utf-8") for rel in ("orchestrator.py", "flows/shard.py")}
    for key in policy.STRIPPED_KEYS:
        quoted = re.compile(rf"""[\[(]\s*["']{key}["']""")
        assert any(quoted.search(text) for text in sources.values()), key


def test_the_stripped_keys_are_the_contract_the_rules_document_lists():
    """T3b and T3c code against this table: a key added or removed is a decision, and the rules document must say so in the same change."""
    assert policy.STRIPPED_KEYS == ("loadout", "tier", "perk_bans", "dissonant_tab", "restart_via_home", "tournament_setup", "in_run_actions")
    assert policy.FORCED_OFF == ("routines.repeat.enabled", "routines.rewards.enabled")
    document = (BACKEND / "player" / "attach_policy.rules.md").read_text(encoding="utf-8")
    for key in policy.STRIPPED_KEYS:
        assert f"| `{key}` |" in document, key
    for path in policy.FORCED_OFF:
        assert f"`{path}`" in document, path


def test_the_forced_off_paths_are_real_controls():
    from towerpilot_workflows.control_settings import FIELDS, flatten
    controls = run_controls.resolve(compiled("coin_default"), {"repeat": True})
    assert set(policy.FORCED_OFF) <= set(flatten(controls))
    for path in policy.FORCED_OFF:
        _, routine, field = path.split(".")
        assert field in FIELDS[routine]


def test_plan_lists_only_what_the_preset_configured():
    plan = policy.plan(compiled("coin_dissonance"))
    assert plan["mode"] == policy.MODE
    assert plan["stripped"] == ["loadout", "tier", "dissonant_tab", "restart_via_home"]      # perk_bans is null here: nothing to list
    assert set(plan["stripped"]).isdisjoint(plan["applied"])
    assert plan["applied"] == sorted(plan["applied"])
    assert {"abilities", "gather", "shopping", "uw_wanted"} <= set(plan["applied"])
    assert not set(plan["applied"]) & {"_source", "label", "kind", "runner", "runner_args"}
    assert plan["forced_off"] == list(policy.FORCED_OFF)
    assert "perk_bans" in policy.plan({"perk_bans": ["a"]})["stripped"]
    assert "perk_bans" not in policy.plan({"perk_bans": None})["stripped"]
    assert "restart_via_home" not in policy.plan({"restart_via_home": False})["stripped"]
    assert "tier" in policy.plan({"tier": 0})["stripped"]          # 0 is a value, not an absence
    # the restriction removes a stripped key whether or not it was configured
    assert policy.unrestricted_keys({"perk_bans": None, "kind": "coin"}) == ["perk_bans"]


def needs(preset):
    return [(r["code"], r["field"]) for r in policy.plan(preset)["needs_tier"]]


def test_needs_tier_for_the_features_that_read_the_fleet_table():
    assert needs(compiled("coin_default")) == [("fleet_nuke", "abilities.nuke_on_fleet"), ("chain_lightning_choreography", "chain_lightning"),
                                               ("danger_window_shots", None)]
    assert needs(compiled("coin_dissonance")) == [("danger_window_shots", None)]            # off-until-wave Chain Lightning and no fleet Nuke
    rule = {"id": "r#1", "when": {"kind": "fleet_mark", "after_waves": 3, "window_waves": 60}, "do": {"kind": "fire", "button": "nuke"}}
    assert ("fleet_mark_rule", "rules[1].when") in needs({"rules": [{"when": {"kind": "wave_at_least", "wave": 5}}, rule]})
    assert ("fleet_mark_rule", "rules[0].when") in needs({"rules": [{"when": {"fleet_mark": {"after_waves": 1}}}]})      # the raw spelling the interpreter also reads
    assert ("fleet_mark_rule", "rules[0].when") in needs({"rules": [{"when": {"trigger": "fleet_mark"}}]})


def test_needs_tier_agrees_with_the_orchestrator_readers(monkeypatch):
    """The conditions in `_needs_tier` mirror two places in orchestrator.py; run both with a stand-in for marks() so a change there fails here."""
    import orchestrator
    from types import SimpleNamespace

    def cl_asks_for_marks(cl):
        asked = []
        monkeypatch.setattr(orchestrator, "preset", lambda: {"chain_lightning": cl})
        monkeypatch.setattr(orchestrator, "marks", lambda: asked.append(True) or [])
        orchestrator.cl_window(SimpleNamespace(cl_always_above=None, cl_offsets={}), 100)
        return bool(asked)

    choreography = ("chain_lightning_choreography", "chain_lightning")
    for cl in ({"enabled": False, "always_on": False, "pre_mark_waves": [5, 25], "off_after_waves": [53, 72]},
               {"always_on": False, "always_on_above": [6080, 6120], "pre_mark_waves": [5, 25], "off_after_waves": [53, 72]},
               {"always_on": False, "always_on_above": None, "pre_mark_waves": [5, 25], "off_after_waves": None},
               {"always_on": False, "always_on_above": None, "pre_mark_waves": None, "off_after_waves": [5, 25]},
               {"always_on": True},
               {"always_on": False, "always_on_above": [300, 330], "pre_mark_waves": None, "off_after_waves": None},
               {"always_on": False, "pre_mark_waves": [5, 25], "off_after_waves": [53, 72]}):
        assert cl_asks_for_marks(cl) == (choreography in needs({"chain_lightning": cl})), cl

    for rule in ({"when": {"kind": "fleet_mark", "after_waves": 3, "window_waves": 60}},
                 {"when": {"fleet_mark": {"after_waves": 1}}},
                 {"when": {"trigger": "fleet_mark"}},
                 {"when": {"kind": "wave_at_least", "wave": 7}},
                 {"when": {"wave_at_least": 7}},
                 {"when": {"bar": "wall", "below": 0.3}},
                 {"do": {"kind": "fire"}}):
        reads_fleet_marks = orchestrator._rule_when(rule)[0] == "fleet_mark"
        assert reads_fleet_marks == (("fleet_mark_rule", "rules[0].when") in needs({"rules": [rule]})), rule


def test_the_refusals_agree_with_the_orchestrator(monkeypatch):
    """The tournament locks and the surrender action are read where the orchestrator reads them: run `_tournament_locked`, `_rule_do` and `max_wave_reached`."""
    import orchestrator
    from types import SimpleNamespace
    monkeypatch.setattr(orchestrator.screen, "in_tournament", lambda frame: False)           # the badge is the worker's lock; the other three are configuration

    def locked(preset, key=None):
        monkeypatch.setattr(orchestrator, "preset", lambda: preset)
        monkeypatch.setattr(orchestrator, "CONFIG", {"preset": key})
        return orchestrator._tournament_locked(None) is not None

    for preset, key in (({"kind": "tournament"}, "bp_x"), ({"kind": "coin", "tournament_setup": True}, "bp_x"), ({"kind": "coin"}, "tournament"),
                        ({"kind": "coin"}, "bp_x"), ({}, None), ({"kind": "coin", "tournament_setup": False}, "bp_coin")):
        says = any(r["code"] == "tournament" for r in policy.compatibility(preset, key))
        assert says == locked(preset, key), (preset, key)

    for rule in ({"do": {"kind": "surrender_retry"}}, {"do": {"action": "surrender_retry"}}, {"do": {"surrender_retry": True}},
                 {"do": {"kind": "fire", "button": "nuke"}}, {"do": {"fire": "nuke"}}, {"do": {"kind": "cancel_sprint"}}, {"do": {}}, {"when": {}}):
        executes = orchestrator._rule_do(rule)[0] == "surrender_retry"
        says = any(r["code"] == "ends_battle" for r in policy.compatibility({"rules": [rule]}))
        assert says == executes, rule

    monkeypatch.setattr(orchestrator, "_tournament_locked", lambda frame: "lock")            # the cap is "armed" when the call gets as far as asking the lock
    monkeypatch.setattr(orchestrator.logger, "event", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.logger, "shot", lambda *a, **k: None)
    for limit in (None, 0, 1, 700):
        preset = {"kind": "coin", "max_wave": limit}
        monkeypatch.setattr(orchestrator, "preset", lambda preset=preset: preset)
        rs = SimpleNamespace(max_wave_done=False)
        orchestrator.max_wave_reached(rs, None, 700 if limit is None else limit)
        assert rs.max_wave_done == (limit is not None) == any(r["field"] == "max_wave" for r in policy.compatibility(preset)), limit


# ------------------------------------------------------------------ policy revision
def mixed_order(value, rng):
    if isinstance(value, dict):
        items = [(k, mixed_order(v, rng)) for k, v in value.items()]
        rng.shuffle(items)
        return dict(items)
    if isinstance(value, list):
        return [mixed_order(v, rng) for v in value]
    return value


def test_policy_revision_is_a_deterministic_sha256_independent_of_key_order():
    preset = compiled("coin_default")
    revision = policy.policy_revision("bp_coin_default", preset)
    assert re.fullmatch(r"[0-9a-f]{64}", revision)
    assert revision == policy.policy_revision("bp_coin_default", copy.deepcopy(preset))
    for seed in range(5):
        assert policy.policy_revision("bp_coin_default", mixed_order(preset, random.Random(seed))) == revision


@pytest.mark.parametrize("edit", [
    lambda p: p.update(label="Renamed"),
    lambda p: p.update(tier=15),
    lambda p: p.update(loadout="other"),
    lambda p: p["abilities"].update(dm_below=0.5),
    lambda p: p["abilities"]["nuke_on_fleet"].update(after_waves=9),
    lambda p: p["gather"].update(guild=False),
    lambda p: p["chain_lightning"].update(always_on=True),
    lambda p: p.update(max_wave=900),
    lambda p: p.update(extra_key=1),
    lambda p: p["shopping"].pop(),
    lambda p: p.update(shop_interval_sec=p["shop_interval_sec"] + 1),
    lambda p: p.pop("restart_via_home"),
])
def test_policy_revision_changes_with_every_policy_field(edit):
    preset = compiled("coin_default")
    base = policy.policy_revision("bp_coin_default", preset)
    changed = copy.deepcopy(preset)
    edit(changed)
    assert changed != preset
    assert policy.policy_revision("bp_coin_default", changed) != base


def test_policy_revision_binds_the_run_key_and_the_policy_constants(monkeypatch):
    preset = compiled("coin_default")
    base = policy.policy_revision("bp_coin_default", preset)
    assert policy.policy_revision("bp_other", preset) != base
    monkeypatch.setattr(policy, "STRIPPED_KEYS", policy.STRIPPED_KEYS + ("extra",))
    assert policy.policy_revision("bp_coin_default", preset) != base
    monkeypatch.undo()
    monkeypatch.setattr(policy, "FORCED_OFF", policy.FORCED_OFF[:1])
    assert policy.policy_revision("bp_coin_default", preset) != base


def test_policy_revision_ignores_the_profile_revision_it_was_compiled_from():
    preset = compiled("coin_default")
    stamped = copy.deepcopy(preset)
    stamped["_source"] = dict(preset["_source"], profile_revision="f" * 64)
    assert policy.policy_revision("k", stamped) == policy.policy_revision("k", preset)


@pytest.mark.parametrize("bad", [{"x": {1, 2}}, {"x": float("nan")}, {"x": object()}])
def test_policy_revision_refuses_a_preset_that_is_not_canonical_json(bad):
    with pytest.raises(policy.AttachEnvelopeInvalid) as raised:
        policy.policy_revision("k", bad)
    assert raised.value.code == "attach_envelope_invalid" and raised.value.field == "preset"


@pytest.mark.parametrize("key", ["", None, 5])
def test_policy_revision_needs_the_run_key(key):
    with pytest.raises(policy.AttachEnvelopeInvalid):
        policy.policy_revision(key, {})


# ------------------------------------------------------------------ the request
def request(**changes):
    base = {"mode": "current_battle", "policy_revision": GOOD_REVISION,
            "expected_battle": {"battle_id": "b-1", "generation": "g-1", "observed_wave": 120},
            "tier": {"value": 14, "provenance": "declared"}, "attestation": "current_battle_takeover"}
    base.update(changes)
    return base


def refused(attach):
    with pytest.raises(policy.AttachRequestInvalid) as raised:
        policy.validate_request(attach)
    return raised.value.code, raised.value.field


def test_a_valid_request_is_returned_normalized_and_independent():
    original = request()
    got = policy.validate_request(original)
    assert got == original and got is not original
    got["expected_battle"]["battle_id"] = "changed"
    got["tier"]["value"] = 1
    assert original == request()


def test_the_no_collector_unknown_tier_request_is_valid():
    attach = request(expected_battle={"battle_id": None, "generation": None, "observed_wave": None},
                     tier={"value": None, "provenance": "unknown"})
    assert policy.validate_request(attach) == attach
    zero = request(expected_battle={"battle_id": None, "generation": None, "observed_wave": 0})
    assert policy.validate_request(zero)["expected_battle"]["observed_wave"] == 0


@pytest.mark.parametrize("attach", [None, [], "current_battle", 5, ()])
def test_the_attach_object_must_be_an_object(attach):
    assert refused(attach) == ("wrong_type", "attach")


def test_an_unknown_key_is_refused_at_every_level():
    assert refused(dict(request(), extra=1)) == ("unknown_field", "attach.extra")
    nested = {"battle_id": None, "generation": None, "observed_wave": None, "extra": 1}
    assert refused(request(expected_battle=nested)) == ("unknown_field", "attach.expected_battle.extra")
    assert refused(request(tier={"value": 3, "provenance": "declared", "extra": 1})) == ("unknown_field", "attach.tier.extra")
    assert refused(dict(request(), battle_envelope={})) == ("unknown_field", "attach.battle_envelope")
    assert refused(dict(request(), harvest=True)) == ("unknown_field", "attach.harvest")


@pytest.mark.parametrize("missing", ["mode", "policy_revision", "expected_battle", "tier", "attestation"])
def test_every_top_level_key_is_required(missing):
    attach = request()
    del attach[missing]
    assert refused(attach) == ("missing_field", f"attach.{missing}")


def test_every_nested_key_is_required():
    assert refused(request(expected_battle={"battle_id": None, "generation": None})) == ("missing_field", "attach.expected_battle.observed_wave")
    assert refused(request(expected_battle={})) == ("missing_field", "attach.expected_battle.battle_id")
    assert refused(request(tier={"value": 3})) == ("missing_field", "attach.tier.provenance")
    assert refused(request(tier={"provenance": "unknown"})) == ("missing_field", "attach.tier.value")


@pytest.mark.parametrize("mode", ["", "next_battle", "CURRENT_BATTLE", None, 1])
def test_the_mode_is_current_battle(mode):
    assert refused(request(mode=mode)) == ("bad_value", "attach.mode")


@pytest.mark.parametrize("revision, code", [
    ("A" * 64, "bad_value"), ("a" * 63, "bad_value"), ("a" * 65, "bad_value"), ("g" * 64, "bad_value"), ("a" * 64 + "\n", "bad_value"),
    (" " + "a" * 63, "bad_value"), ("", "bad_value"), (None, "wrong_type"), (12, "wrong_type"), (["a"] * 64, "wrong_type"),
])
def test_the_policy_revision_is_64_lowercase_hex(revision, code):
    assert refused(request(policy_revision=revision)) == (code, "attach.policy_revision")


@pytest.mark.parametrize("field, value, code", [
    ("battle_id", "", "bad_value"), ("battle_id", 7, "wrong_type"), ("battle_id", True, "wrong_type"), ("battle_id", ["b"], "wrong_type"),
    ("generation", "", "bad_value"), ("generation", 3, "wrong_type"),               # the ledger's generation is a string: an integer could never match
    ("generation", True, "wrong_type"),
    ("observed_wave", -1, "bad_value"), ("observed_wave", True, "wrong_type"), ("observed_wave", False, "wrong_type"),
    ("observed_wave", 1.5, "wrong_type"), ("observed_wave", 3.0, "wrong_type"), ("observed_wave", "3", "wrong_type"),
])
def test_expected_battle_values(field, value, code):
    expected = {"battle_id": "b", "generation": "g", "observed_wave": 5}
    expected[field] = value
    assert refused(request(expected_battle=expected)) == (code, f"attach.expected_battle.{field}")


@pytest.mark.parametrize("tier, expected", [
    ({"value": None, "provenance": "declared"}, ("bad_value", "attach.tier.value")),             # declared means a number
    ({"value": 5, "provenance": "unknown"}, ("bad_value", "attach.tier.value")),                 # unknown means no number
    ({"value": 0, "provenance": "declared"}, ("bad_value", "attach.tier.value")),
    ({"value": -2, "provenance": "declared"}, ("bad_value", "attach.tier.value")),
    ({"value": True, "provenance": "declared"}, ("wrong_type", "attach.tier.value")),
    ({"value": 2.5, "provenance": "declared"}, ("wrong_type", "attach.tier.value")),
    ({"value": 14.0, "provenance": "declared"}, ("wrong_type", "attach.tier.value")),
    ({"value": "14", "provenance": "declared"}, ("wrong_type", "attach.tier.value")),
    ({"value": 14, "provenance": "observed"}, ("bad_value", "attach.tier.provenance")),
    ({"value": 14, "provenance": "Declared"}, ("bad_value", "attach.tier.provenance")),
    ({"value": 14, "provenance": None}, ("wrong_type", "attach.tier.provenance")),
    ({"value": None, "provenance": 3}, ("wrong_type", "attach.tier.provenance")),
    (None, ("wrong_type", "attach.tier")),
    (14, ("wrong_type", "attach.tier")),
])
def test_the_tier_is_declared_with_a_positive_integer_or_unknown_with_none(tier, expected):
    assert refused(request(tier=tier)) == expected


@pytest.mark.parametrize("attestation", ["", "current_battle", "CURRENT_BATTLE_TAKEOVER", None, True, 1])
def test_the_attestation_is_the_takeover_sentence(attestation):
    assert refused(request(attestation=attestation)) == ("bad_value", "attach.attestation")


def test_the_error_names_the_code_and_the_field():
    with pytest.raises(policy.AttachRequestInvalid) as raised:
        policy.validate_request(request(tier={"value": 0, "provenance": "declared"}))
    error = raised.value
    assert isinstance(error, ValueError) and (error.code, error.field) == ("bad_value", "attach.tier.value")
    assert "attach.tier.value" in str(error)


def test_validation_does_not_change_the_request():
    attach = request()
    before = copy.deepcopy(attach)
    policy.validate_request(attach)
    assert attach == before


# ------------------------------------------------------------------ the server-derived block
PREDECESSOR = {"battle_id": "b-1", "generation": "g-1", "operation_id": "op-1"}


def test_the_envelope_attach_block_is_built_normalized_and_round_trips():
    built = policy.build_envelope_attach(GOOD_REVISION, {"value": 14, "provenance": "declared"}, ["restart_via_home", "loadout"], PREDECESSOR)
    assert built == {"mode": "current_battle", "policy_revision": GOOD_REVISION, "tier": {"value": 14, "provenance": "declared"},
                     "stripped": ["loadout", "restart_via_home"],                        # always in STRIPPED_KEYS order
                     "predecessor": PREDECESSOR}
    assert policy.validate_envelope_attach(built) == built
    assert policy.validate_envelope_attach(copy.deepcopy(built)) is not built
    unlinked = policy.build_envelope_attach(GOOD_REVISION, {"value": None, "provenance": "unknown"}, (), None)
    assert unlinked["predecessor"] is None and unlinked["stripped"] == []


def block(**changes):
    base = policy.build_envelope_attach(GOOD_REVISION, {"value": 3, "provenance": "declared"}, ["tier"], PREDECESSOR)
    base.update(changes)
    return base


def envelope_refusal(obj):
    with pytest.raises(policy.AttachEnvelopeInvalid) as raised:
        policy.validate_envelope_attach(obj)
    assert raised.value.code == "attach_envelope_invalid" and isinstance(raised.value, ValueError)
    return raised.value.field


def test_the_envelope_attach_block_has_exactly_its_keys():
    assert envelope_refusal(None) == "attach"
    assert envelope_refusal(dict(block(), extra=1)) == "attach.extra"
    for key in ("mode", "policy_revision", "tier", "stripped", "predecessor"):
        incomplete = block()
        del incomplete[key]
        assert envelope_refusal(incomplete) == f"attach.{key}"


@pytest.mark.parametrize("changes, field", [
    ({"mode": "next_battle"}, "attach.mode"),
    ({"policy_revision": "x"}, "attach.policy_revision"),
    ({"tier": {"value": 3, "provenance": "unknown"}}, "attach.tier.value"),
    ({"tier": {"value": None, "provenance": "declared"}}, "attach.tier.value"),
    ({"stripped": ["tier", "tier"]}, "attach.stripped"),
    ({"stripped": ["tier", "bogus"]}, "attach.stripped"),
    ({"stripped": "tier"}, "attach.stripped"),
    ({"stripped": None}, "attach.stripped"),
    ({"predecessor": {"battle_id": "b", "generation": "g"}}, "attach.predecessor.operation_id"),
    ({"predecessor": {"battle_id": "b", "generation": "g", "operation_id": "o", "x": 1}}, "attach.predecessor.x"),
    ({"predecessor": {"battle_id": "", "generation": "g", "operation_id": "o"}}, "attach.predecessor.battle_id"),
    ({"predecessor": {"battle_id": "b", "generation": 4, "operation_id": "o"}}, "attach.predecessor.generation"),
    ({"predecessor": {"battle_id": "b", "generation": "g", "operation_id": None}}, "attach.predecessor.operation_id"),
    ({"predecessor": "b-1"}, "attach.predecessor"),
])
def test_the_envelope_attach_block_values(changes, field):
    assert envelope_refusal(block(**changes)) == field


def test_building_the_block_refuses_what_validating_it_refuses():
    with pytest.raises(policy.AttachEnvelopeInvalid):
        policy.build_envelope_attach("bad", {"value": None, "provenance": "unknown"}, [], None)
    with pytest.raises(policy.AttachEnvelopeInvalid):
        policy.build_envelope_attach(GOOD_REVISION, {"value": None, "provenance": "unknown"}, ["nope"], None)


def test_run_tier_is_the_declared_integer_or_none():
    assert policy.run_tier(block()) == 3
    unknown = policy.build_envelope_attach(GOOD_REVISION, {"value": None, "provenance": "unknown"}, [], None)
    assert policy.run_tier(unknown) is None
    for bad in (None, {}, dict(unknown, tier={"value": 2, "provenance": "unknown"})):
        with pytest.raises(policy.AttachEnvelopeInvalid):
            policy.run_tier(bad)


# ------------------------------------------------------------------ the restricted envelope (spec 3.3 step 9, R3-01)
SELECTED = "bp_coin_default"
UNKNOWN_TIER = {"value": None, "provenance": "unknown"}


def launch_inputs(name="coin_default"):
    """What `_launch_operation` holds before it restricts: the configuration copy, the effective preset and the resolved controls."""
    cfg = {"preset": "bp_other", "repeat": True, "rois": {"field": [0, 300, 1080, 1330]},
           "presets": {f"bp_{name}": compiled(name), "bp_other": {"kind": "coin", "loadout": "kept", "tier": 3}}}
    selected = f"bp_{name}"
    preset = run_controls.effective_preset(cfg, selected)
    frozen = copy.deepcopy(cfg)
    frozen["preset"] = selected
    controls = run_controls.resolve(preset, frozen)
    return preset, controls, frozen, selected


def test_restrict_envelope_keeps_the_key_and_puts_one_restricted_object_in_two_places():
    preset, controls, frozen, selected = launch_inputs()
    assert controls["routines"]["repeat"]["enabled"] is True and controls["routines"]["rewards"]["enabled"] is True
    restricted, restricted_controls, restricted_frozen = policy.restrict_envelope(preset, controls, frozen, selected, {"value": 14, "provenance": "declared"})
    # R3-01: the selector stays a KEY (the worker indexes `presets` with it), the object lives in `presets[selected]` and equals the returned preset
    assert restricted_frozen["preset"] == selected and isinstance(restricted_frozen["preset"], str)
    assert restricted_frozen["presets"][selected] == restricted
    assert restricted_frozen["presets"][selected] is not restricted
    for key in policy.STRIPPED_KEYS:
        assert key not in restricted and key not in restricted_frozen["presets"][selected]
    assert restricted == {k: v for k, v in preset.items() if k not in policy.STRIPPED_KEYS}
    assert policy.unrestricted_keys(restricted) == [] and policy.unrestricted_keys(preset) != []
    assert restricted_controls["routines"]["repeat"]["enabled"] is False
    assert restricted_controls["routines"]["rewards"]["enabled"] is False


def test_restrict_envelope_changes_nothing_else():
    preset, controls, frozen, selected = launch_inputs()
    _, restricted_controls, restricted_frozen = policy.restrict_envelope(preset, controls, frozen, selected, UNKNOWN_TIER)
    expected_controls = copy.deepcopy(controls)
    expected_controls["routines"]["repeat"]["enabled"] = False
    expected_controls["routines"]["rewards"]["enabled"] = False
    assert restricted_controls == expected_controls
    others = {key: value for key, value in restricted_frozen.items() if key not in ("preset", "presets")}
    assert others == {key: value for key, value in frozen.items() if key not in ("preset", "presets")}
    assert restricted_frozen["presets"]["bp_other"] == frozen["presets"]["bp_other"]          # another run is not this takeover's business
    assert set(restricted_frozen["presets"]) == set(frozen["presets"])


def test_restrict_envelope_never_changes_its_inputs():
    preset, controls, frozen, selected = launch_inputs()
    snapshots = [copy.deepcopy(x) for x in (preset, controls, frozen)]
    restricted, restricted_controls, restricted_frozen = policy.restrict_envelope(preset, controls, frozen, selected, UNKNOWN_TIER)
    assert [preset, controls, frozen] == snapshots
    # and the outputs share nothing with the inputs or with each other
    restricted["abilities"]["dm_below"] = "changed"
    restricted_controls["routines"]["shopping"]["enabled"] = "changed"
    restricted_frozen["presets"]["bp_other"]["loadout"] = "changed"
    restricted_frozen["rois"]["field"][0] = 99
    assert [preset, controls, frozen] == snapshots
    assert restricted_frozen["presets"][selected]["abilities"].get("dm_below") != "changed"


def test_the_worker_restores_the_restricted_preset_from_the_frozen_configuration():
    """What `accepted_source` plus `effective_preset` do with the envelope: the key picks the object, and it is the restricted one."""
    preset, controls, frozen, selected = launch_inputs()
    restricted, _, restricted_frozen = policy.restrict_envelope(preset, controls, frozen, selected, UNKNOWN_TIER)
    cfg = copy.deepcopy(restricted_frozen)
    cfg["preset"] = selected                        # battle_source.accepted_source: cfg["preset"] = selected
    assert run_controls.effective_preset(cfg) == restricted
    assert policy.unrestricted_keys(run_controls.effective_preset(cfg)) == []


def test_restrict_envelope_sets_the_forced_off_controls_even_when_the_input_has_none():
    preset = {"kind": "coin", "loadout": "x"}
    restricted, controls, frozen = policy.restrict_envelope(preset, {}, {"presets": {"r": preset}}, "r", UNKNOWN_TIER)
    assert controls == {"routines": {"repeat": {"enabled": False}, "rewards": {"enabled": False}}}
    assert restricted == {"kind": "coin"} and frozen == {"preset": "r", "presets": {"r": {"kind": "coin"}}}
    with pytest.raises(policy.AttachEnvelopeInvalid):
        policy.restrict_envelope(preset, {"routines": {"repeat": True}}, {"presets": {"r": preset}}, "r", UNKNOWN_TIER)


@pytest.mark.parametrize("name, code", [("tourney_main", "tournament"), ("shard_daily", "ends_battle")])
def test_restrict_envelope_refuses_an_incompatible_run(name, code):
    preset, controls, frozen, selected = launch_inputs(name)
    snapshots = [copy.deepcopy(x) for x in (preset, controls, frozen)]
    with pytest.raises(policy.AttachNotCompatible) as raised:
        policy.restrict_envelope(preset, controls, frozen, selected, UNKNOWN_TIER)
    assert raised.value.code == "takeover_not_compatible" and isinstance(raised.value, ValueError)
    assert raised.value.reasons[0]["code"] == code
    assert [preset, controls, frozen] == snapshots


def test_restrict_envelope_refuses_a_run_that_ends_the_battle_through_a_rule_or_a_cap():
    for edit in (lambda p: p["blueprints"]["coin_default"].update(max_wave=700),
                 add_rescue_rule({"when": {"wave_at_least": 5000}, "do": {"surrender_retry": True}})):
        preset = compiled("coin_default", edit)
        with pytest.raises(policy.AttachNotCompatible):
            policy.restrict_envelope(preset, {}, {"presets": {SELECTED: preset}}, SELECTED, UNKNOWN_TIER)


def test_restrict_envelope_refuses_a_malformed_input():
    preset = {"kind": "coin"}
    frozen = {"presets": {"r": preset}}
    for args in ((preset, {}, frozen, "missing"), (preset, {}, frozen, ""), (preset, {}, frozen, None), (preset, [], frozen, "r"),
                 (preset, {}, {"presets": []}, "r"), (preset, {}, {}, "r"), (None, {}, frozen, "r"), (preset, {}, None, "r")):
        with pytest.raises(policy.AttachEnvelopeInvalid):
            policy.restrict_envelope(*args, UNKNOWN_TIER)
    with pytest.raises(policy.AttachEnvelopeInvalid):                                     # the tier is checked against the request shape
        policy.restrict_envelope(preset, {}, frozen, "r", {"value": 3, "provenance": "unknown"})
    with pytest.raises(policy.AttachEnvelopeInvalid):
        policy.restrict_envelope(preset, {}, frozen, "r", None)


def test_the_declared_tier_is_not_written_into_the_preset_or_the_controls():
    preset, controls, frozen, selected = launch_inputs()
    restricted, restricted_controls, restricted_frozen = policy.restrict_envelope(preset, controls, frozen, selected, {"value": 18, "provenance": "declared"})
    assert "tier" not in restricted and "tier" not in restricted_frozen["presets"][selected] and "tier" not in restricted_controls


def test_the_legacy_tournament_key_is_refused_by_the_restriction():
    with pytest.raises(policy.AttachNotCompatible):
        policy.restrict_envelope({"kind": "coin"}, {}, {"presets": {"tournament": {"kind": "coin"}}}, "tournament", UNKNOWN_TIER)


# ------------------------------------------------------------------ the shared surrender walk
def test_the_surrender_walk_finds_the_action_wherever_a_mapping_names_it():
    rules = [{"do": {"kind": "fire"}}, {"when": {"x": 1}, "do": {"kind": "surrender_retry"}},
             {"nested": [{"deep": {"kind": "surrender_retry"}}]}, "text", None]
    assert battle_end.surrender_rule_paths(rules) == ["rules[1].do", "rules[2].nested[0].deep"]
    assert battle_end.surrender_rule_paths(None) == [] and battle_end.surrender_rule_paths([]) == []
    assert battle_end.surrender_rule_paths([{"do": {"surrender_retry": True}}]) == []         # the raw spelling is a profile rule, never a compiled one
    assert battle_end.legacy_surrender_rule_paths([{"do": {"surrender_retry": True}}, {"do": {"action": "surrender_retry"}}, {"do": {"kind": "surrender_retry"}},
                                                   {"do": {"fire": "nuke"}}, "text", {"do": None}]) == ["rules[0].do", "rules[1].do"]
    assert battle_end.legacy_surrender_rule_paths(None) == [] and battle_end.legacy_surrender_rule_paths({"do": {"surrender_retry": True}}) == []
    assert battle_end.SURRENDER_KINDS == ("shard", "cycle_quest", "uw_grant_quest")


# ------------------------------------------------------------------ purity (spec 3.10 rule 1)
FORBIDDEN_ROOTS = {"device", "flask", "orchestrator", "dashboard", "act", "capture", "settings", "vision", "runtime", "interactions", "flows",
                   "scheduling", "cv2", "numpy", "adbclient", "psutil", "requests", "yaml"}


def imported_roots(path):
    roots = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
            if node.module == "player":
                roots |= {f"player.{alias.name}" for alias in node.names}
    return roots


def test_takeover_no_device_io_attach_policy_half():
    """player/attach_policy.py (and the walk it shares) import nothing that reaches a device, a screen, a web framework or the orchestrator."""
    for rel in ("player/attach_policy.py", "player/battle_end.py"):
        roots = imported_roots(BACKEND / rel)
        assert not roots & FORBIDDEN_ROOTS, (rel, sorted(roots & FORBIDDEN_ROOTS))
    assert imported_roots(BACKEND / "player/attach_policy.py") <= {"copy", "hashlib", "json", "re", "player", "player.battle_end"}
    assert imported_roots(BACKEND / "player/battle_end.py") == set()


def test_importing_the_policy_loads_no_device_screen_or_framework_module():
    """The same, by effect: a fresh interpreter that imports the policy and the readiness rows holds none of those modules."""
    code = ("import sys; from player import attach_policy, battle_end, readiness; "
            "print('LOADED', sorted(m for m in sys.modules if m.split('.')[0] in %r))" % (sorted(FORBIDDEN_ROOTS),))
    env = {"PYTHONPATH": str(BACKEND), "PYTHONDONTWRITEBYTECODE": "1", "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
    done = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "LOADED []", done.stdout


def test_the_public_names_the_other_packages_code_against_exist():
    names = ("MODE", "ATTESTATION", "STRIPPED_KEYS", "FORCED_OFF", "compatibility", "plan", "policy_revision", "restrict_envelope", "unrestricted_keys",
             "validate_request", "build_envelope_attach", "validate_envelope_attach", "run_tier",
             "AttachPolicyError", "AttachRequestInvalid", "AttachEnvelopeInvalid", "AttachNotCompatible")
    assert all(hasattr(policy, name) for name in names)
    assert (policy.MODE, policy.ATTESTATION) == ("current_battle", "current_battle_takeover")
