"""Which compiled-preset settings end a battle: the one place that walks compiled rules for the surrender action.

Pure: no device, no screen, no imports. `player/readiness.py` (which images a run needs) and `player/attach_policy.py` (which presets a takeover refuses) both
ask this module, so the rule shape (`do = {"kind": "surrender_retry"}`, compiled by `playerprofile._compile_action`) and the kinds whose runner surrenders are
written once. The orchestrator's own guard for the same action is `orchestrator._tournament_locked`; this module only finds the rules.

Two readings, because they answer different questions. `surrender_rule_paths` is the compiled shape readiness has always listed images for (its output must not
change). `legacy_surrender_rule_paths` adds the older spellings `orchestrator._rule_do` also executes, which only a takeover (that fails closed) needs.
"""

SURRENDER_ACTION = "surrender_retry"
# Blueprint kinds whose runner script ends the battle it started (shard farming, the quest cycles): `readiness.requirements` lists the surrender images for them.
SURRENDER_KINDS = ("shard", "cycle_quest", "uw_grant_quest")


def surrender_rule_paths(rules) -> list[str]:
    """Where `rules` (a compiled preset's `rules`) names the surrender action: one path per mapping with `kind == "surrender_retry"`, e.g. `rules[2].do`.

    Any mapping at any depth counts, as the readiness walk always matched; an empty list means no rule surrenders."""
    found: list[str] = []

    def walk(value, path):
        if isinstance(value, dict):
            if value.get("kind") == SURRENDER_ACTION:
                found.append(path)
            for key, item in value.items():
                walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(rules or [], "rules")
    return found


def legacy_surrender_rule_paths(rules) -> list[str]:
    """Where a rule's `do` names the surrender action in a spelling the orchestrator executes but the compiler never emits (`orchestrator._rule_do`):
    `{"action": "surrender_retry"}` or the raw key `{"surrender_retry": true}`. One path per rule (`rules[2].do`)."""
    found: list[str] = []
    for index, rule in enumerate(rules if isinstance(rules, list) else []):
        do = rule.get("do") if isinstance(rule, dict) else None
        if isinstance(do, dict) and (do.get("action") == SURRENDER_ACTION or SURRENDER_ACTION in do):
            found.append(f"rules[{index}].do")
    return found
