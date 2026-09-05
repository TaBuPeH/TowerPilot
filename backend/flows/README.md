# Adding a Flow

> **Status:** Active
> **Type:** Knowledge
> **Created:** 2026-08-24
> **Updated:** 2026-08-24
> **Tags:** flows, extension, registry

A **flow** is one type of run the autopilot can drive - coin farming,
tournament, shard farming, the event quests. Each lives in exactly one
file in this folder, and each file declares a `FLOW` spec that the
registry ([\_\_init\_\_.py](__init__.py)) reads **without importing the
module** (pure-literal `ast` parse, so a broken flow can never take down
the dashboard or the compiler).

Everything that consumes run types reads the registry: the day scheduler
(`combo.py`), the profile compiler (`playerprofile.py` - blueprint kinds,
field legality, compiled `runner`/`runner_args`, the dashboard vocabulary)
and the tray. **Dropping a file here is the whole integration.**

## The FLOW spec

```python
FLOW = {
    "kind": "my_kind",            # blueprint `kind` that selects this flow
    "label": "My farming mode",   # human name for menus and UIs
    "runner": "flows/my_kind.py", # its own script, or None (see below)
    "handoff": "none",            # screen prep the scheduler does first
    "count_arg": None,            # e.g. "--loops" for a counted flow
    "blueprint_args": [           # blueprint fields -> command line
        {"flag": "--widgets", "fields": ["widgets"], "default": 2},
    ],
    "blueprint_fields": {         # extra legal blueprint fields (extension
        "widgets": {              # kinds only - builtins are typed in the
            "type": "int",        # compiler itself)
            "doc": "how many widgets per run",
        },
    },
}
```

| Key | Meaning |
|---|---|
| `kind` | The blueprint `kind` that selects this flow. Must be unique across the folder - the registry refuses duplicates. |
| `label` | Human-readable name. |
| `runner` | `None` = the generic engine (`orchestrator.py`) runs the compiled preset directly - all variance lives in the preset. A path = this flow's own script drives the run. |
| `handoff` | `"loadout"` - the scheduler equips the blueprint's loadout and sets its tier first. `"home_only"` - it only walks the game Home. `"none"` - the runner owns its setup end to end. |
| `count_arg` | A flag that receives the scheduler's remaining daily quota (`0` = unbounded). `None` for uncounted flows. |
| `blueprint_args` | How blueprint fields become argv: first non-`None` field in `fields` wins, else `default`; omitted entirely when everything is `None`. Used identically at compile time and at spawn time, so the two can never drift. |
| `blueprint_fields` | For extension kinds: the fields a blueprint of this kind may carry, with `type`/`doc` (and optional `span`/`values`) for the dashboard's typed editor. |
| `legacy_preset` | The `config.yaml` preset this flow runs under when no profile names a blueprint. |

> [!important] The spec must be a pure literal
> No names, calls, f-strings or imports inside the dict - it is read with
> `ast.literal_eval`. A file that starts with `_` is skipped entirely.

---

## The runner contract

A flow that ships its own script (like [shard.py](shard.py)) must:

1. **Be runnable from the backend root**: start with the `sys.path`
   bootstrap the existing flows use, so `python flows/<name>.py` resolves
   sibling modules.
2. **Accept `--instance <key>`** and bind it via
   `settings.select_instance` before the first capture.
3. **Accept `--preset bp_<name>` and ONLY compiled blueprints of its own
   kind** - refuse anything else before the first tap (copy the
   `_bp_arg`/`_bind_preset` pattern from [quest_sm.py](quest_sm.py)).
   A blueprint of the wrong kind reaching a runner is how a tournament
   gets surrendered.
4. **Adopt in-progress state on startup** rather than restarting: a live
   battle is joined, a stats dialog is RETRY-tapped. Runners are killed
   and relaunched to deploy; adoption is what makes that safe.
5. **Never tap outside menus it opened itself**, and stop with a logged
   Abort when the screen is not what it expected.

A flow whose behaviour fits the generic engine needs **no script at all**:
set `"runner": None` and express the variance in the preset/blueprint -
that is exactly how [coin.py](coin.py) and [tournament.py](tournament.py)
work, and their `__main__` blocks show how to hand a preset to the engine
directly.

---

## Checklist

1. Create `flows/<name>.py` with the `FLOW` literal (and the runner code,
   if any).
2. `python -c "import flows; print(flows.kinds())"` - your kind appears.
3. `python -m pytest backend/tests -q` from the backend root - still green.
4. Write a blueprint of your kind in a profile, or a preset with
   `runner: flows/<name>.py`, and it is schedulable from the dashboard,
   the tray and the day plan.
