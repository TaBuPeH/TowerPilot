# Takeover policy rules
> **Status:** Active
> **Type:** Rules
> **Created:** 2026-10-24
> **Updated:** 2026-10-24
> **Owner:** player/attach_policy.py
> **Specification:** [roadmap item 3](../logs/reviews/roadmap-item3/spec.md), package T3a

## Contract

[attach_policy.py](attach_policy.py) owns the rules for attaching a selected run to the battle that is already on screen: which compiled presets may be attached, what an
attached battle keeps, drops or cannot use without a tier, and the exact shape of the request and of the block the server stores in the envelope. It is pure: no device,
no screen, no web framework, no orchestrator, no configuration file. A test (`test_takeover_no_device_io_attach_policy_half`) fails if it imports one.

It does not decide that a battle is really on screen (the worker's entry gate does, from a capture), who owns the emulator (the server does) or what a person may do
(the dashboard does). The worker (T3b) and the server (T3c) call this module; neither writes its own copy of a rule below.
[battle_end.py](battle_end.py) is the one place that finds a surrender in rules: `surrender_rule_paths` (the compiled shape; `player/readiness.py` and this module both ask it) and
`legacy_surrender_rule_paths` (the older spellings, which only this module's fail-closed check needs).

---

## Public names

| Name | What it gives |
|---|---|
| `MODE`, `ATTESTATION` | `"current_battle"` and `"current_battle_takeover"` |
| `STRIPPED_KEYS` | The compiled-preset keys only a start from Home reads (table below) |
| `FORCED_OFF` | The control paths an attached battle never starts with: `routines.repeat.enabled`, `routines.rewards.enabled` |
| `compatibility(preset, preset_key=None)` | A list of `{code, field, detail}`; empty means the preset can be attached |
| `plan(preset)` | `{mode, applied, stripped, needs_tier, forced_off}` for the preview |
| `policy_revision(preset_key, preset)` | SHA-256 hex of the canonical JSON of what the takeover would apply |
| `restrict_envelope(preset, controls, frozen, selected, tier)` | The restricted `(preset, controls, frozen)` triple; refuses an incompatible preset |
| `unrestricted_keys(preset)` | The stripped keys a preset still carries; a worker refuses to start when this is not empty |
| `validate_request(attach)` | The normalized `attach` object of a request, or `AttachRequestInvalid(code, field)` |
| `build_envelope_attach(policy_revision, tier, stripped, predecessor)` | The server-derived `battle_envelope.attach` block |
| `validate_envelope_attach(obj)` | The same block, validated and normalized, or `AttachEnvelopeInvalid` |
| `run_tier(envelope_attach)` | The declared tier as an integer, or `None` when unknown; read it once, not per frame |

Errors are `ValueError` subclasses with a `code`: `AttachRequestInvalid` (`code` is `unknown_field`, `missing_field`, `wrong_type` or `bad_value`, plus `field`, a dotted
path such as `attach.tier.value`), `AttachEnvelopeInvalid` (`attach_envelope_invalid`, plus `field`) and `AttachNotCompatible` (`takeover_not_compatible`, plus
`reasons`).

---

## Refused: the preset cannot be attached

A preset that cannot be honored without ending or paying for a battle is refused, so nobody believes it applies. `compatibility` returns one reason per cause.

| Code | Field | When | Where the orchestrator acts on it |
|---|---|---|---|
| `tournament` | `kind` | The kind is `tournament` | `_tournament_locked`: `preset().get("kind") == "tournament"` |
| `tournament` | `tournament_setup` | The preset sets up a tournament entry | `_tournament_locked` and the tournament branch of `main()` |
| `tournament` | `preset` | The run is literally keyed `tournament` (pass `preset_key`) | `_tournament_locked`: `CONFIG.get("preset") == "tournament"` |
| `ends_battle` | `max_wave` | `max_wave` is not null (a cap of 0 counts) | `max_wave_reached` surrenders through `shard.abandon_run` |
| `ends_battle` | `rules[i].do` | A compiled rule whose action kind is `surrender_retry`, or (fail closed) the older spellings `{"action": "surrender_retry"}` and `{"surrender_retry": true}` that `_rule_do` also reads | `_rule_surrender` surrenders through `shard.abandon_run` |
| `ends_battle` | `kind` | The kind is `shard`, `cycle_quest` or `uw_grant_quest` | Their runner scripts end the battle they start (`battle_end.SURRENDER_KINDS`) |
| `unsupported_kind` | `kind` or `runner` | Any other kind, or no kind and a runner script, or a coin run whose runner is not `orchestrator.py` (the run command would start that script, which has no attach gate) | Only the orchestrator's own loop is continued |

The fourth tournament lock, the trophy badge on screen, is the worker's: it needs a capture. Its evidence is tri-state (see below).

---

## Stripped: entry and equipment the battle already passed

The battle is running, so these keys do nothing for it. The restricted preset does not carry them, and the preview lists the configured ones as "not applied". A key counts as
configured when its value is not null, false, or an empty string or container (0 is a value). The restriction removes every key in `STRIPPED_KEYS` whether or not it was
configured, and `unrestricted_keys` reports every one that is still present.

| Key | Read by | Why it goes |
|---|---|---|
| `loadout` | `_prepare_coin_start`: `loadout.apply(body["loadout"])` | Home-only equipment |
| `tier` | `_prepare_coin_start` and `restart_from_home`: `shard.set_tier(...)` | The tier selector is on Home. The attached tier is declared by the person (or unknown) and lives only in `battle_envelope.attach`; it is never written into the preset |
| `perk_bans` | `shard.enter_run`: the Perks dialog entry (bans read from `body["perk_bans"]`) | The Perks dialog is reachable only from Home |
| `dissonant_tab` | `shard.enter_run`, `restart_from_home` | The Dissonant Run dialog is on Home |
| `restart_via_home` | `main()`: the death restart | An attached battle ends at its end; it never restarts |
| `tournament_setup` | `main()`: the tournament entry | A tournament preset is refused; the key is removed anyway |
| `in_run_actions` | `run_in_run_actions` | Tournament card swaps; refused at runtime already |

Between-run chores are not a preset key: `restart_from_home` runs them, and an attached battle never reaches it. The `chores` table of `scheduling/chores.py` is not touched here.

---

## Forced off

`restrict_envelope` sets `routines.repeat.enabled` and `routines.rewards.enabled` to false in the controls it returns, creating the path when the input has none (the
session reads a missing gate as enabled). The person can switch rewards on live afterwards; the worker package (T3b) does not offer repeat for an attached battle. Nothing else in the controls
changes.

---

## Needs a tier

While the tier is unknown the worker's `marks()` returns no fleet waves (T3b), so these stay off. `plan(preset)["needs_tier"]` lists the ones this preset uses, as
`{code, field, detail}`. Each condition mirrors the orchestrator reader named here, and a test runs both so they cannot drift apart.

| Code | Field | Condition | Reader |
|---|---|---|---|
| `fleet_nuke` | `abilities.nuke_on_fleet` | The value is truthy | `ab.get("nuke_on_fleet")` in `watch_frame` and in `_fast_wall_watch`, both over `marks()` |
| `fleet_mark_rule` | `rules[i].when` | The trigger is `fleet_mark` (`kind`, `trigger` or the raw key) | `_trigger_fires` over `marks()`; `_rule_when` names the trigger |
| `chain_lightning_choreography` | `chain_lightning` | Not disabled, and both `pre_mark_waves` and `off_after_waves` are set | `cl_window` loops over `marks()` only then |
| `danger_window_shots` | none | Always | The danger-window screenshots in `main()` read `marks()` for every run |

---

## The request (spec 3.1)

`validate_request` accepts exactly this object and refuses an unknown key at every level. A bool is not an integer.

| Key | Value |
|---|---|
| `mode` | `"current_battle"` |
| `policy_revision` | 64 lowercase hexadecimal digits, from the preview |
| `expected_battle` | `{battle_id, generation, observed_wave}`: a non-empty string or null, a non-empty string or null, a non-negative integer or null |
| `tier` | `{value, provenance}`: provenance `declared` with a positive integer, or `unknown` with null |
| `attestation` | `"current_battle_takeover"` |

The generation is a string because the battle ledger stores one (`Battles.create` refuses anything else); the specification's "int" would never match a record.

---

## Policy revision

`policy_revision(preset_key, preset)` hashes the canonical JSON (sorted keys, no spaces, ASCII, no NaN) of the policy constants, the run key and the effective preset without
its `_source`. Pass the preset before restriction, as the preview and the admission both do. Key order never matters. Any change to a preset field, to `STRIPPED_KEYS` or to
`FORCED_OFF` changes it. `_source` is left out because the profile revision it carries changes when an unrelated run is edited and says nothing about this policy. A preset
that is not canonical JSON raises `AttachEnvelopeInvalid`.

---

## The restricted envelope (spec 3.3 step 9)

`restrict_envelope` never changes its inputs and returns copies that share nothing. For the selected run key it guarantees:

- `frozen["preset"]` is the run KEY `selected`. The worker restores the run by indexing `frozen["presets"]` with it (`effective_preset`, `battle_source.accepted_source`), and
  `envelope["preset_key"]` stays the key too.
- `frozen["presets"][selected]` is an object equal to the returned restricted preset, which is the resolved preset without `STRIPPED_KEYS`. Only these two places hold the object.
- The returned controls have every `FORCED_OFF` path false. Everything else in the controls, in `frozen` and in the other presets is unchanged.
- An incompatible preset raises `AttachNotCompatible` before anything is built.

`tier` is checked against the request shape and not written anywhere in the result.

---

## The block `battle_envelope.attach` (spec 3.3 step 9, 3.6)

Server-derived facts never go into the request, so an identical retry still matches its receipt. They live in this block.

| Key | Value |
|---|---|
| `mode` | `"current_battle"` |
| `policy_revision` | The revision the person confirmed |
| `tier` | `{value, provenance}` exactly as in the request |
| `stripped` | The stripped keys, in `STRIPPED_KEYS` order, no duplicates |
| `predecessor` | `{battle_id, generation, operation_id}` of the collector's record, or null when no collector held the emulator |

`build_envelope_attach` builds and validates it; `validate_envelope_attach` is what the worker runs on the restored envelope. The worker reports a malformed block, or a restored preset
for which `unrestricted_keys` is not empty, as `attach_envelope_invalid`.

---

## Tournament evidence and readiness (outside this module)

`vision/screen.py` answers `tournament_state(frame)`: `yes`, `no` or `unknown`. It is `unknown` exactly when `tourney/in_tournament.png` is missing or cannot be read
(`detect._tpl` raises `TemplateMissing` for both), and `no` only when the template loads and scores below the threshold `in_tournament` uses, over the same band. `in_tournament`
is unchanged and still reads false for a missing template, which is why it cannot be the takeover's evidence. The worker refuses on `unknown`.

`player/readiness.py` answers `requirements(cfg, body, attached=True)` (also `check` and `require`, keyword `attached`). The attached rows leave out the Home-side images:
equipment presets, cards and modules, perk bans, the dissonant dialog, the surrender and END ROUND images, and the BATTLE and GAME STATS HOME buttons
(`HOME_ONLY_FLOW_TEMPLATES`). They keep the in-battle ones, including `buttons/retry.png`, which `detect.death_screen` needs to see the death an attached battle ends on, and
`buttons/return_to_game.png`, the reward flows' way back. They add `tourney/in_tournament.png` as BLOCKING with the reason `TAKEOVER_TOURNAMENT_REASON`. With `attached=False`
the output is what it always was; a test compares it with a pinned copy of the module over a wide matrix.

---

## Enforcement

| Rule | Test |
|---|---|
| Refusal and strip table, over every blueprint of the golden account | `test_attach_policy.py::test_strip_and_refuse_matrix*` |
| Stripped keys exist in the compiler's output and are still read | `test_the_stripped_keys_are_keys_the_compiler_emits`, `test_every_stripped_key_is_still_read_where_the_rules_document_says` |
| Refusals match the orchestrator's tournament locks, surrender reading and cap | `test_the_refusals_agree_with_the_orchestrator` |
| Needs-tier conditions match the orchestrator | `test_needs_tier_agrees_with_the_orchestrator_readers` |
| Request and block shapes | `test_attach_policy.py` request and block cases |
| Restricted envelope, key versus object, no input change | `test_restrict_envelope_*`, `test_the_worker_restores_the_restricted_preset_from_the_frozen_configuration` |
| No device, screen, framework or orchestrator import | `test_takeover_no_device_io_attach_policy_half`, `test_importing_the_policy_loads_no_device_screen_or_framework_module` |
| Tri-state tournament evidence | `test_tournament_state.py` |
| Attached rows, the blocking badge, the unchanged unattached output | `test_takeover_readiness.py` |
