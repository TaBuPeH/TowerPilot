"""Takeover policy: which presets may be attached to the battle on screen, what an attached battle keeps, strips or needs a tier for, and the request and
envelope shapes the worker (T3b) and the server (T3c) both read. Pure: no device, no screen, no web framework, no orchestrator. Rules: attach_policy.rules.md.

Nothing here decides whether a battle is really on screen (the worker's entry gate does, from a capture) or who owns the emulator (the server does); this module
only answers questions about a compiled preset and validates plain values.
"""
import copy
import hashlib
import json
import re

from player import battle_end

MODE = "current_battle"
ATTESTATION = "current_battle_takeover"
TIER_PROVENANCES = ("declared", "unknown")

# Compiled-preset keys only a start from Home reads (entry and equipment). The battle is already running, so an attached run drops them from the preset it is
# handed and lists them as "not applied". Each is read by the orchestrator (or by the entry code it calls) at the place named here; see attach_policy.rules.md.
STRIPPED_KEYS = (
    "loadout",            # _prepare_coin_start: loadout.apply(body["loadout"])
    "tier",               # _prepare_coin_start / restart_from_home: shard.set_tier(...); the attached tier is declared, never taken from here
    "perk_bans",          # the Perks dialog entry in shard.enter_run (bans read from body["perk_bans"])
    "dissonant_tab",      # shard.enter_run / restart_from_home: the Dissonant Run dialog
    "restart_via_home",   # the death restart through Home
    "tournament_setup",   # the tournament entry (a tournament preset is refused anyway)
    "in_run_actions",     # tournament in-run card swaps (disabled at runtime; a tournament preset is refused anyway)
)
# Controls an attached battle never starts with: the battle must not repeat into another one, and the reward flows leave the screen. Paths in the shape
# `towerpilot_workflows.control_settings.flatten` and BattleSession use for a live control.
FORCED_OFF = ("routines.repeat.enabled", "routines.rewards.enabled")
# Compiled keys that describe the run rather than configure what it does; `plan()["applied"]` leaves them out.
_DESCRIPTIVE_KEYS = ("_source", "label", "kind", "runner", "runner_args", "retired_rules")

_REVISION = re.compile(r"[0-9a-f]{64}")


# ------------------------------------------------------------------ errors
class AttachPolicyError(ValueError):
    """A takeover value that is refused. `code` names the refusal, `field` the offending value (dotted path) when there is one."""

    def __init__(self, code, field=None, detail=None):
        self.code, self.field, self.detail = code, field, detail
        super().__init__(" ".join(part for part in (code, field and f"[{field}]", detail) if part))


class AttachRequestInvalid(AttachPolicyError):
    """The `attach` object of a request is not the exact shape of spec 3.1. `code` is unknown_field, missing_field, wrong_type or bad_value."""


class AttachEnvelopeInvalid(AttachPolicyError):
    """A server-derived value (the `envelope.attach` block, a restricted-envelope input) is malformed. The worker reports it as `attach_envelope_invalid`."""
    code = "attach_envelope_invalid"

    def __init__(self, field=None, detail=None):
        super().__init__(self.code, field, detail)


class AttachNotCompatible(AttachPolicyError):
    """The preset cannot be attached; `reasons` is the list `compatibility` returned. The server maps it to 409 `takeover_not_compatible`."""
    code = "takeover_not_compatible"

    def __init__(self, reasons):
        self.reasons = copy.deepcopy(reasons)
        super().__init__(self.code, None, "; ".join(f"{r['code']}:{r['field']}" for r in reasons))


def _request_fail(code, field, detail=None):
    raise AttachRequestInvalid(code, field, detail)


def _envelope_fail(code, field, detail=None):
    raise AttachEnvelopeInvalid(field, f"{code}: {detail}" if detail else code)


# ------------------------------------------------------------------ shape checks (shared by the request and the envelope block)
def _keys(obj, path, keys, fail):
    """`obj` is an object with exactly `keys`: an unknown key is refused first, then a missing one."""
    if not isinstance(obj, dict):
        fail("wrong_type", path, "expected an object")
    for key in obj:
        if key not in keys:
            fail("unknown_field", f"{path}.{key}", "not part of the contract")
    for key in keys:
        if key not in obj:
            fail("missing_field", f"{path}.{key}", "required")


def _int(value, path, fail, *, minimum):
    # `type(...) is int`: a bool is not an integer here, and neither is a float
    if type(value) is not int:
        fail("wrong_type", path, "expected an integer")
    if value < minimum:
        fail("bad_value", path, f"must be at least {minimum}")
    return value


def _text(value, path, fail):
    if not isinstance(value, str):
        fail("wrong_type", path, "expected a string")
    if not value:
        fail("bad_value", path, "must not be empty")
    return value


def _text_or_none(value, path, fail):
    return None if value is None else _text(value, path, fail)


def _revision(value, path, fail):
    if not isinstance(value, str):
        fail("wrong_type", path, "expected a string")
    if not _REVISION.fullmatch(value):
        fail("bad_value", path, "expected 64 lowercase hex digits")
    return value


def _tier(obj, path, fail):
    """`{value, provenance}`: declared with a positive integer, or unknown with a null value."""
    _keys(obj, path, ("value", "provenance"), fail)
    provenance = obj["provenance"]
    if not isinstance(provenance, str):
        fail("wrong_type", f"{path}.provenance", "expected a string")
    if provenance not in TIER_PROVENANCES:
        fail("bad_value", f"{path}.provenance", f"expected one of {', '.join(TIER_PROVENANCES)}")
    value = obj["value"]
    if provenance == "unknown":
        if value is not None:
            fail("bad_value", f"{path}.value", "an unknown tier carries no value")
    else:
        if value is None:
            fail("bad_value", f"{path}.value", "a declared tier carries its value")
        _int(value, f"{path}.value", fail, minimum=1)
    return {"value": value, "provenance": provenance}


# ------------------------------------------------------------------ the request (spec 3.1)
def validate_request(attach) -> dict:
    """The normalized `attach` object of a `takeover` request, or `AttachRequestInvalid(code, field)`.

    The exact shape: mode, policy_revision (64 lowercase hex), expected_battle {battle_id str|null, generation str|null, observed_wave non-negative int|null},
    tier {value, provenance}, attestation. An unknown key is refused at every level; a bool is not an integer. The generation is a string because that is what
    the battle ledger stores (`Battles.create` refuses a non-string)."""
    fail = _request_fail
    _keys(attach, "attach", ("mode", "policy_revision", "expected_battle", "tier", "attestation"), fail)
    if attach["mode"] != MODE:
        fail("bad_value", "attach.mode", f"expected {MODE}")
    revision = _revision(attach["policy_revision"], "attach.policy_revision", fail)
    expected = attach["expected_battle"]
    _keys(expected, "attach.expected_battle", ("battle_id", "generation", "observed_wave"), fail)
    battle_id = _text_or_none(expected["battle_id"], "attach.expected_battle.battle_id", fail)
    generation = _text_or_none(expected["generation"], "attach.expected_battle.generation", fail)
    observed = expected["observed_wave"]
    if observed is not None:
        _int(observed, "attach.expected_battle.observed_wave", fail, minimum=0)
    tier = _tier(attach["tier"], "attach.tier", fail)
    if attach["attestation"] != ATTESTATION:
        fail("bad_value", "attach.attestation", "not the takeover attestation")
    return {"mode": MODE, "policy_revision": revision,
            "expected_battle": {"battle_id": battle_id, "generation": generation, "observed_wave": observed},
            "tier": tier, "attestation": ATTESTATION}


# ------------------------------------------------------------------ the server-derived block `battle_envelope.attach` (spec 3.3 step 9, 3.6)
def _stripped(value, path):
    if not isinstance(value, (list, tuple)):
        _envelope_fail("wrong_type", path, "expected a list")
    for key in value:
        if key not in STRIPPED_KEYS:
            _envelope_fail("bad_value", path, f"{key!r} is not a stripped key")
    if len(set(value)) != len(value):
        _envelope_fail("bad_value", path, "duplicate keys")
    return [key for key in STRIPPED_KEYS if key in value]       # one order, whoever listed them


def _predecessor(value, path):
    if value is None:
        return None
    _keys(value, path, ("battle_id", "generation", "operation_id"), _envelope_fail)
    return {name: _text(value[name], f"{path}.{name}", _envelope_fail) for name in ("battle_id", "generation", "operation_id")}


def validate_envelope_attach(obj) -> dict:
    """The normalized `battle_envelope.attach` block, or `AttachEnvelopeInvalid`: {mode, policy_revision, tier, stripped, predecessor}."""
    _keys(obj, "attach", ("mode", "policy_revision", "tier", "stripped", "predecessor"), _envelope_fail)
    if obj["mode"] != MODE:
        _envelope_fail("bad_value", "attach.mode", f"expected {MODE}")
    return {
        "mode": MODE,
        "policy_revision": _revision(obj["policy_revision"], "attach.policy_revision", _envelope_fail),
        "tier": _tier(obj["tier"], "attach.tier", _envelope_fail),
        "stripped": _stripped(obj["stripped"], "attach.stripped"),
        "predecessor": _predecessor(obj["predecessor"], "attach.predecessor"),
    }


def build_envelope_attach(policy_revision, tier, stripped, predecessor) -> dict:
    """The block the server stores in `battle_envelope.attach`: the revision the person confirmed, the tier as declared (or unknown), the preset keys that were
    stripped, and the collector's record the worker must find again (`{battle_id, generation, operation_id}`) or None when no collector held the emulator."""
    return validate_envelope_attach({"mode": MODE, "policy_revision": policy_revision, "tier": tier,
                                     "stripped": stripped, "predecessor": predecessor})


def run_tier(envelope_attach) -> int | None:
    """The tier an attached battle runs under: the declared integer, or None when it is unknown. Only for a battle that has an `attach` block (a malformed or
    missing block raises); a wrong declaration is the person's, which is why provenance is kept beside it. Read it once, not per frame."""
    return validate_envelope_attach(envelope_attach)["tier"]["value"]


# ------------------------------------------------------------------ the compiled preset
def _mapping(preset, name="preset"):
    if not isinstance(preset, dict):
        raise AttachEnvelopeInvalid(name, "expected a compiled preset (an object)")
    return preset


def _reason(code, field, detail):
    return {"code": code, "field": field, "detail": detail}


def compatibility(preset, preset_key=None) -> list[dict]:
    """Why this compiled preset cannot be attached: a list of `{code, field, detail}`, empty when it can.

    `tournament`: the kind, `tournament_setup`, or a legacy run literally keyed `tournament` (the three configuration locks of
    `orchestrator._tournament_locked`; the fourth, the trophy badge, is the worker's). `ends_battle`: `max_wave`, a `surrender_retry` rule, or a kind whose runner
    surrenders. `unsupported_kind`: a kind (or, with no kind, a runner script; with kind coin, any runner other than the orchestrator) the battle loop does not drive."""
    preset = _mapping(preset)
    kind = preset.get("kind")
    reasons = []
    if kind == "tournament":
        reasons.append(_reason("tournament", "kind", "a tournament run is never taken over: it is never cancelled and its entry is paid for"))
    if preset.get("tournament_setup"):
        reasons.append(_reason("tournament", "tournament_setup", "the preset sets up a tournament entry"))
    if preset_key == "tournament":
        reasons.append(_reason("tournament", "preset", "the legacy tournament preset"))
    if kind in battle_end.SURRENDER_KINDS:
        reasons.append(_reason("ends_battle", "kind", f"a {kind} run ends the battle it started"))
    elif kind not in (None, "coin", "tournament"):
        reasons.append(_reason("unsupported_kind", "kind", f"the {kind} runner is not the battle loop a takeover continues"))
    elif preset.get("runner") and (kind is None or (kind == "coin" and preset["runner"] != "orchestrator.py")):
        reasons.append(_reason("unsupported_kind", "runner", f"a preset driven by the {preset['runner']} script is not the battle loop a takeover continues"))
    if preset.get("max_wave") is not None:
        reasons.append(_reason("ends_battle", "max_wave", f"the run ends at wave {preset['max_wave']}"))
    rules = preset.get("rules")
    surrender_paths = battle_end.surrender_rule_paths(rules)
    surrender_paths += [path for path in battle_end.legacy_surrender_rule_paths(rules) if path not in surrender_paths]
    for path in surrender_paths:
        reasons.append(_reason("ends_battle", path, "a rule surrenders the run"))
    return reasons


def _configured(value) -> bool:
    """A value that would have done something: not None, False, or an empty string or container (0 is configured)."""
    return not (value is None or value is False or (isinstance(value, (str, list, tuple, dict)) and not value))


def _needs_tier(preset) -> list[dict]:
    """Features whose waves come from the tier's fleet table (`orchestrator.marks()`), where the orchestrator reads them."""
    found = []
    abilities = preset.get("abilities")
    if isinstance(abilities, dict) and abilities.get("nuke_on_fleet"):
        found.append(_reason("fleet_nuke", "abilities.nuke_on_fleet", "the Nuke fires on fleet waves"))
    rules = preset.get("rules")
    for index, rule in enumerate(rules if isinstance(rules, list) else []):
        when = rule.get("when") if isinstance(rule, dict) else None
        if isinstance(when, dict) and "fleet_mark" in (when.get("kind"), when.get("trigger"), *when):
            found.append(_reason("fleet_mark_rule", f"rules[{index}].when", "a rule fires on fleet waves"))
    cl = preset.get("chain_lightning")
    if (isinstance(cl, dict) and cl.get("enabled", True) is not False
            and cl.get("pre_mark_waves") is not None and cl.get("off_after_waves") is not None):
        found.append(_reason("chain_lightning_choreography", "chain_lightning", "Chain Lightning is switched around fleet waves"))
    found.append(_reason("danger_window_shots", None, "screenshots are taken in the fleet danger windows"))
    return found


def plan(preset) -> dict:
    """What a takeover with this preset does, for the preview: `applied` (the configured keys the battle keeps, sorted), `stripped` (the configured Home-only keys
    it drops, in STRIPPED_KEYS order), `needs_tier` (`{code, field, detail}` features that stay off while the tier is unknown) and `forced_off` (control paths)."""
    preset = _mapping(preset)
    return {
        "mode": MODE,
        "applied": sorted(key for key, value in preset.items()
                          if key not in STRIPPED_KEYS and key not in _DESCRIPTIVE_KEYS and _configured(value)),
        "stripped": [key for key in STRIPPED_KEYS if key in preset and _configured(preset[key])],
        "needs_tier": _needs_tier(preset),
        "forced_off": list(FORCED_OFF),
    }


def unrestricted_keys(preset) -> list[str]:
    """The stripped keys a preset still carries (set or not): empty for a restricted preset. A worker whose restored preset returns any refuses to start."""
    preset = _mapping(preset)
    return [key for key in STRIPPED_KEYS if key in preset]


def policy_revision(preset_key, preset) -> str:
    """SHA-256 (lowercase hex) of the canonical JSON of what a takeover with this run would apply: the run key, the resolved preset without its `_source`
    (the profile revision it was compiled from says nothing about the policy) and the policy constants. Key order never matters; any change to a preset field or
    to STRIPPED_KEYS or FORCED_OFF changes it. Pass the effective preset BEFORE restriction, as the preview and the admission both do."""
    preset = _mapping(preset)
    if not isinstance(preset_key, str) or not preset_key:
        raise AttachEnvelopeInvalid("preset_key", "expected the run key")
    payload = {"mode": MODE, "stripped_keys": list(STRIPPED_KEYS), "forced_off": list(FORCED_OFF),
               "preset_key": preset_key, "preset": {key: value for key, value in preset.items() if key != "_source"}}
    try:
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise AttachEnvelopeInvalid("preset", f"not canonical JSON: {exc}") from exc
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _force_false(controls, path):
    *parents, leaf = path.split(".")
    node = controls
    for name in parents:
        node = node.setdefault(name, {})
        if not isinstance(node, dict):
            raise AttachEnvelopeInvalid("controls", f"{name} is not an object")
    node[leaf] = False


def restrict_envelope(preset, controls, frozen, selected, tier):
    """The restricted `(preset, controls, frozen)` triple a takeover launches with; the inputs are never changed.

    `preset` is the effective preset of the run `selected`, `controls` its resolved controls, `frozen` the configuration copy the envelope carries
    (`frozen["presets"][selected]` is the run). An incompatible preset raises `AttachNotCompatible`. The result is mutually consistent:
    `frozen["preset"]` stays the run KEY `selected` (the worker restores the run by indexing `presets` with it), `frozen["presets"][selected]` is an object equal to
    the returned restricted preset (the preset without STRIPPED_KEYS), and `controls` has every FORCED_OFF path false. `tier` is checked against the request shape
    and not written anywhere here: a declared tier belongs in `battle_envelope.attach` (`build_envelope_attach`), never in the preset."""
    preset = _mapping(preset)
    reasons = compatibility(preset, selected)
    if reasons:
        raise AttachNotCompatible(reasons)
    _tier(tier, "tier", _envelope_fail)
    if not isinstance(selected, str) or not selected:
        raise AttachEnvelopeInvalid("selected", "expected the run key")
    if not isinstance(controls, dict):
        raise AttachEnvelopeInvalid("controls", "expected the resolved controls (an object)")
    if not isinstance(frozen, dict) or not isinstance(frozen.get("presets"), dict) or selected not in frozen["presets"]:
        raise AttachEnvelopeInvalid("frozen.presets", f"the run {selected!r} is absent from the frozen configuration")
    restricted = {key: copy.deepcopy(value) for key, value in preset.items() if key not in STRIPPED_KEYS}
    restricted_controls = copy.deepcopy(controls)
    for path in FORCED_OFF:
        _force_false(restricted_controls, path)
    restricted_frozen = copy.deepcopy(frozen)
    restricted_frozen["preset"] = selected
    restricted_frozen["presets"][selected] = copy.deepcopy(restricted)
    return restricted, restricted_controls, restricted_frozen
