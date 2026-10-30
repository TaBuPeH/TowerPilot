"""Pure gathering configuration shared by editors, validation and collectors."""
import copy
import math

ORBIT_TIMING_FIELDS = {
    "interval_sec": {"default": 960, "min": 1, "max": 3600,
        "label": "Retry spacing after unconfirmed taps",
        "description": "Minimum time between attempt starts after taps were sent but collection was not confirmed. An attempt that sent no taps retries after the safety backoff. A recognized gem starts the first attempt; a confirmed claim resumes watching after tap pacing. Defaults to 16 minutes from the previous attempt's start; this is not a gem spawn period."},
    "retry_interval_sec": {"default": 5, "min": 0.5, "max": 30,
        "label": "Time between tap sets while seeking",
        "description": "Minimum time between tap sets during a collection attempt. Farming and confirmation continue between sets. A tap near the input cutoff keeps this much time for stable exact +2 confirmation; later observations do not extend that cutoff."},
    "attempt_timeout_sec": {"default": 60, "min": 5, "max": 60,
        "label": "Maximum floating gem claim attempt",
        "description": "Maximum time to send taps for a recognized gem, or to seek one after Run now. Input stops at this cutoff. The last dispatched tap retains one tap-set interval for stable exact +2 confirmation. Refusals do not extend either cutoff."},
    "retry_backoff_sec": {"default": 15, "min": 1, "max": 300,
        "label": "Safety backoff during a floating gem attempt",
        "description": "Wait after an input safety refusal, and at least this long after an attempt times out. Unknown balances or gems are checked again while keeping the same tap spacing and claim deadline."},
}


def validate_orbit_timing(body: dict, path: str = "gem_orbit") -> list[str]:
    errors = []
    for key, spec in ORBIT_TIMING_FIELDS.items():
        if key not in body:
            continue
        value = body[key]
        if (type(value) not in (int, float) or not math.isfinite(value)
                or not spec['min'] <= value <= spec['max']):
            errors.append(f"{path}.{key}: must be a finite number within {spec['min']}–{spec['max']}")
    return errors


AD_GEM_FIELDS = {
    "edge_inset_px": {
        "type": "int", "default": 5, "min": 5, "max": 40,
        "label": "Button edge inset", "unit": "px",
        "description": "Minimum distance from every button edge, including tap jitter and press drift.",
    },
    "poll_sec": {
        "type": "range", "default": [2, 4], "min": 0.5, "max": 60,
        "label": "Offer check interval", "unit": "sec",
        "description": "Random interval between checks for an ad-gem offer.",
    },
    "confirmed_cooldown_sec": {
        "type": "range", "default": [720, 720], "min": 1, "max": 3600,
        "label": "Interval between collection windows", "unit": "sec",
        "description": "Time from the start of one collection window to the next, even when no gem is collected. Defaults to 12 minutes.",
    },
    "attempt_timeout_sec": {
        "type": "number", "default": 60, "min": 5, "max": 60,
        "label": "Maximum collection window", "unit": "sec",
        "description": "Stop attempts after this duration, or immediately after a confirmed claim. Farming continues between checks.",
    },
    "retry_cooldown_sec": {
        "type": "range", "default": [4, 7], "min": 1, "max": 120,
        "label": "After an unconfirmed claim", "unit": "sec",
        "description": "Random wait before checking again; a visible offer then gets its response delay.",
    },
}


def validate_ad_gem_options(body: object, path: str = "ad_gem_options") -> list[str]:
    """Validate partial overrides without loading settings or device modules."""
    if not isinstance(body, dict):
        return [f"{path}: must be a mapping"]
    errors = []
    for key, value in body.items():
        field = AD_GEM_FIELDS.get(key)
        if field is None:
            errors.append(f"{path}.{key}: unknown setting")
            continue
        if field["type"] == "int":
            valid = type(value) is int and field["min"] <= value <= field["max"]
        elif field["type"] == "number":
            valid = (type(value) in (int, float) and math.isfinite(value)
                     and field["min"] <= value <= field["max"])
        else:
            valid = (
                isinstance(value, (list, tuple)) and len(value) == 2
                and all(type(v) in (int, float) and math.isfinite(v) for v in value)
                and field["min"] <= value[0] <= value[1] <= field["max"]
            )
        if not valid:
            shape = {"int": "an integer", "number": "a finite number",
                     "range": "an ordered [min, max] pair"}[field["type"]]
            errors.append(f"{path}.{key}: must be {shape} within {field['min']}–{field['max']}")
    return errors


def resolve_ad_gem_options(body: dict | None = None) -> dict:
    """Return independent effective values; invalid saved settings fail closed."""
    overrides = {} if body is None else body
    errors = validate_ad_gem_options(overrides)
    if errors:
        raise ValueError("; ".join(errors))
    return {key: copy.deepcopy(overrides.get(key, field["default"]))
            for key, field in AD_GEM_FIELDS.items()}
