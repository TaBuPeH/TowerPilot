"""Pure effective battle settings. Saved profiles and CONFIG are never mutated.

Numbers here describe configuration only; driver-owned clocks and counters must
survive edits. A null override removes that override and inherits the envelope.

What each action's saved settings mean is the action's own saved adapter (`player.saved_controls`), found through the action
catalog; nothing here names an action.
"""
import copy

from player import saved_controls
from player.saved_controls import REWARD_GATHER_KEYS, REWARD_GLOBAL_KEYS
from towerpilot_workflows.control_settings import FIELDS, flatten, validate_patch, merge_patch


def effective_preset(config: dict, key: str | None = None) -> dict:
    """Resolve the driver's existing single-level inheritance into an owned copy."""
    preset = config['presets'][config['preset'] if key is None else key]
    if 'base' not in preset:
        return copy.deepcopy(preset)
    base = config['presets'][preset['base']]
    merged = {}
    for name, value in base.items():
        merged[name] = ({**value, **preset.get(name, {})} if isinstance(value, dict)
                        else preset.get(name, value))
    for name, value in preset.items():
        if name not in merged and name != 'base':
            merged[name] = value
    return copy.deepcopy(merged)


def resolve(preset: dict, config: dict) -> dict:
    """The effective controls of a compiled preset, from the saved adapter of every action in the catalog.

    A range is retained in the frozen preset; the normalized scalar is its lower bound until an explicit numeric override
    replaces both endpoints."""
    return saved_controls.resolve(preset, config)


def apply_to_preset(preset: dict, settings: dict) -> dict:
    """Materialize supported aliases in a copy; runtime gates own enablement.

    In particular, disabling shopping must not erase the frozen plan/counters.
    Only driver gates interpret abilities/rescue/repeat.
    """
    return saved_controls.apply_to_preset(preset, settings)