"""The dashboard UI's write shapes, proven against the real validator.

The glass UI never invents shapes ad hoc: every value an editor writes when a
control is switched to a new kind/mode lives in ONE JSON block inside
frontend/webui/index.html (<script type="application/json" id="ui-seeds">).
These tests parse that exact block and prove every shape against
playerprofile.validate on the shipped default profile - so "changing the
Chain Lightning mode to off_until_wave errors immediately" (2026-08-25) is a
class of bug that cannot silently return: a seed the validator refuses is a
red test, not a broken editor.

The API-level tests drive the same /api/profile-patch endpoint the UI calls,
against a throwaway copy of the default profile, and pin the refusals the
staged editors rely on (a mode without its required ranges, a dict arm.on:
always, an empty label).
"""
import copy
import glob
import importlib.util
import json
import os
import re
import shutil

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(os.path.dirname(BACKEND), "frontend", "webui",
                     "index.html")


def _load_seeds() -> dict:
    with open(INDEX, encoding="utf-8") as fh:
        html = fh.read()
    m = re.search(r'<script type="application/json" id="ui-seeds">\s*'
                  r'(\{.*?\})\s*</script>', html, re.S)
    assert m, "ui-seeds block missing from index.html"
    return json.loads(m.group(1))


SEEDS = _load_seeds()


@pytest.fixture()
def profile():
    from goldens import load_golden
    return copy.deepcopy(load_golden())


def _validate(profile) -> list:
    from player import playerprofile as pp
    return pp.validate(profile)


# ---------------------------------------------------------------- seeds
SECTIONS = ("gather", "uw_policies", "rescue_policies", "shopping_lists")


@pytest.mark.parametrize("sec", SECTIONS)
def test_create_template_validates(profile, sec):
    """The '+ create' seed of every family is a valid policy as-is."""
    profile["policies"][sec]["zz_seed"] = copy.deepcopy(
        SEEDS["templates"][sec])
    assert _validate(profile) == []


def test_seed_sections_cover_all_families():
    assert set(SEEDS["templates"]) == set(SECTIONS)


@pytest.mark.parametrize("trig", sorted(SEEDS["triggers"]))
def test_trigger_seed_validates(profile, trig):
    """Every WHEN the trigger dropdown can write is valid immediately.

    death_screen rules only accept stop_after_run; everything else is
    paired with a neutral toggle_uw - the dropdown's own default action.
    """
    do = ({"stop_after_run": True} if trig == "death_screen"
          else copy.deepcopy(SEEDS["actions"]["toggle_uw"]))
    profile["policies"]["rescue_policies"]["zz_seed"] = {
        "arm": "always", "end_sprint_after_sw": False,
        "rules": [{"when": copy.deepcopy(SEEDS["triggers"][trig]), "do": do}]}
    assert _validate(profile) == []


# every action seed, in the arm context the UI writes it under
_ACTION_CASES = [
    ("burst", "always"),          # Tier B burst: no retaps
    ("burst_tier_a", "second_wind"),   # Tier A burst: retaps legal
    ("fire", "always"),
    ("toggle_uw", "always"),
    ("cancel_sprint", "always"),
    ("surrender_retry", "always"),
    ("stop_after_run", "always"),
]


@pytest.mark.parametrize("act,arm", _ACTION_CASES)
def test_action_seed_validates(profile, act, arm):
    when = (copy.deepcopy(SEEDS["triggers"]["bar"])
            if act.startswith("burst")
            else copy.deepcopy(SEEDS["triggers"]["wave_at_least"]))
    profile["policies"]["rescue_policies"]["zz_seed"] = {
        "arm": copy.deepcopy(SEEDS["arm"][arm]),
        "end_sprint_after_sw": False,
        "rules": [{"when": when, "do": copy.deepcopy(SEEDS["actions"][act])}]}
    assert _validate(profile) == []


def test_action_seeds_cover_the_offered_dropdown():
    """The action dropdown offers everything in vocab minus switch_cards
    (refused everywhere); each must have a seed."""
    from player import playerprofile as pp
    offered = set(pp.vocab()["rule_actions"]["fields"]) - {"switch_cards"}
    assert offered <= set(SEEDS["actions"])


@pytest.mark.parametrize("mode", sorted(SEEDS["cl_modes"]))
def test_cl_mode_switch_validates(profile, mode):
    """Switching the Chain Lightning mode writes mode + the seeded required
    ranges - the exact object p2clMode builds - and must always validate."""
    cl = {"mode": mode}
    cl.update(copy.deepcopy(SEEDS["cl_modes"][mode]))
    profile["policies"]["uw_policies"]["zz_seed"] = {
        "baseline": {}, "chain_lightning": cl}
    assert _validate(profile) == []


def test_cl_modes_cover_the_vocab():
    from player import playerprofile as pp
    assert set(SEEDS["cl_modes"]) == set(pp.vocab()["cl_modes"]["values"])


def test_cl_mode_without_seed_is_refused(profile):
    """REGRESSION (2026-08-25): a bare mode switch to off_until_wave was
    written without on_above and refused. The UI now seeds the range; this
    pins the refusal that makes the seeding load-bearing."""
    profile["policies"]["uw_policies"]["zz_seed"] = {
        "baseline": {}, "chain_lightning": {"mode": "off_until_wave"}}
    problems = _validate(profile)
    assert problems and "on_above" in problems[0]


def test_fleet_marks_without_windows_is_refused(profile):
    profile["policies"]["uw_policies"]["zz_seed"] = {
        "baseline": {}, "chain_lightning": {"mode": "fleet_marks"}}
    problems = _validate(profile)
    assert problems
    assert any("pre_mark_waves" in p or "off_after_waves" in p
               for p in problems)


def test_arm_always_is_a_string_not_a_mapping(profile):
    """REGRESSION: the editor once wrote {on: "always"}, which the schema
    refuses - `arm: "always"` is a bare string."""
    assert SEEDS["arm"]["always"] == "always"
    pol = {"arm": {"on": "always"}, "end_sprint_after_sw": False, "rules": []}
    profile["policies"]["rescue_policies"]["zz_seed"] = pol
    problems = _validate(profile)
    assert problems and "unknown arm trigger" in problems[0]


def test_retaps_is_second_wind_only(profile):
    """Pins why the UI seeds burst WITHOUT retaps under arm: always - a
    main-loop (Tier B) burst refuses the Tier-A-only knob."""
    profile["policies"]["rescue_policies"]["zz_seed"] = {
        "arm": "always", "end_sprint_after_sw": False,
        "rules": [{"when": copy.deepcopy(SEEDS["triggers"]["bar"]),
                   "do": copy.deepcopy(SEEDS["actions"]["burst_tier_a"])}]}
    problems = _validate(profile)
    assert problems and "retaps" in problems[0]


def test_new_rule_and_directive_seeds(profile):
    profile["policies"]["rescue_policies"]["zz_seed"] = {
        "arm": "always", "end_sprint_after_sw": False,
        "rules": [copy.deepcopy(SEEDS["new_rule"])]}
    profile["policies"]["shopping_lists"]["zz_shop"] = {
        "enabled": True, "directives": [copy.deepcopy(SEEDS["new_directive"])]}
    assert _validate(profile) == []


# ---------------------------------------------------------------- labels
@pytest.mark.parametrize("sec", SECTIONS)
def test_label_accepted_everywhere(profile, sec):
    body = copy.deepcopy(SEEDS["templates"][sec])
    body["label"] = "A Human Readable Name"
    profile["policies"][sec]["zz_seed"] = body
    assert _validate(profile) == []


@pytest.mark.parametrize("bad", ["", "   ", 7, True])
def test_bad_labels_refused(profile, bad):
    body = copy.deepcopy(SEEDS["templates"]["gather"])
    body["label"] = bad
    profile["policies"]["gather"]["zz_seed"] = body
    problems = _validate(profile)
    assert problems and ".label" in problems[0]


# ------------------------------------------------------------ api contract
@pytest.fixture(scope="module")
def dash():
    """The real dashboard app, driven exactly like the browser does."""
    path = os.path.join(os.path.dirname(BACKEND), "frontend", "dashboard.py")
    spec = importlib.util.spec_from_file_location("tp_dashboard", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def ui_profile(dash):
    """A throwaway copy of the default profile for real patch round-trips."""
    src = os.path.join(BACKEND, "tests", "fixtures", "golden_profile.yaml")
    dst = os.path.join(BACKEND, "profiles", "zz_uitest.yaml")
    shutil.copyfile(src, dst)
    yield "zz_uitest"
    for f in glob.glob(os.path.join(BACKEND, "profiles", "zz_uitest.yaml*")):
        os.remove(f)


def _patch(dash, name, path, value=None, op=None):
    body = {"name": name, "path": path, "value": value}
    if op:
        body["op"] = op
    with dash.app.test_client() as c:
        return c.post("/api/profile-patch", json=body)


def test_api_create_edit_label_delete_roundtrip(dash, ui_profile):
    for sec in SECTIONS:
        r = _patch(dash, ui_profile, ["policies", sec, "zz_new"],
                   copy.deepcopy(SEEDS["templates"][sec]))
        assert r.status_code == 200, (sec, r.get_json())
    r = _patch(dash, ui_profile,
               ["policies", "uw_policies", "zz_new", "label"],
               "Chain Lightning Farming Choreography")
    assert r.status_code == 200
    assert (r.get_json()["profile"]["policies"]["uw_policies"]["zz_new"]
            ["label"] == "Chain Lightning Farming Choreography")
    for sec in SECTIONS:
        r = _patch(dash, ui_profile, ["policies", sec, "zz_new"], op="delete")
        assert r.status_code == 200, (sec, r.get_json())


def test_api_staged_mode_switch_succeeds_bare_fails(dash, ui_profile):
    """The staged editor writes the WHOLE chain_lightning object (mode +
    seeded ranges) in one patch - accepted. The pre-staging shape (mode
    alone) is refused with the exact on_above verdict the user saw."""
    whole = {"mode": "off_until_wave"}
    whole.update(copy.deepcopy(SEEDS["cl_modes"]["off_until_wave"]))
    r = _patch(dash, ui_profile,
               ["policies", "uw_policies", "farm_cl_choreo",
                "chain_lightning"], whole)
    assert r.status_code == 200, r.get_json()
    r = _patch(dash, ui_profile,
               ["policies", "uw_policies", "farm_cl_choreo",
                "chain_lightning"], {"mode": "off_until_wave"})
    assert r.status_code == 400
    assert "on_above" in r.get_json()["error"]


def test_api_empty_label_refused(dash, ui_profile):
    r = _patch(dash, ui_profile,
               ["policies", "gather", "all_on", "label"], "")
    assert r.status_code == 400
    assert ".label" in r.get_json()["error"]


# ------------------------------------------------------ runner start argv
class _FakeChild:
    pid = 424242

    def poll(self):
        return None         # "still alive" - a successful start


@pytest.fixture()
def start_cmds(dash, monkeypatch):
    """Capture the argv /api/control action=start would spawn, spawn nothing."""
    cmds = []

    def fake_popen(cmd, **kw):
        cmds.append(cmd)
        return _FakeChild()
    monkeypatch.setattr(dash.subprocess, "Popen", fake_popen)
    monkeypatch.setattr("time.sleep", lambda s: None)   # skip the 1.5s probe
    return cmds


def _start(dash, preset):
    with dash.app.test_client() as c:
        return c.post("/api/control", json={"action": "start",
                                            "preset": preset})


def test_start_flow_under_its_legacy_preset_omits_preset_argv(dash,
                                                              start_cmds):
    """Flow runners take `--preset bp_<name>` ONLY; under their own legacy
    config preset they bind it themselves (FLOW legacy_preset). Passing the
    legacy name made quest starts die at argparse - the UI's 'runner
    failed' (user, 2026-08-27)."""
    r = _start(dash, "quest_inner_land_mines")
    assert r.status_code == 200, r.get_json()
    # _procs_refresh Popens a process listing too - pick the runner spawn
    cmd = next(c for c in start_cmds if "--instance" in c)
    assert "--preset" not in cmd
    assert any(c.endswith("quest_ilm.py") for c in cmd)
    assert cmd[-2:] == ["--cycles", "40"]       # runner_args still travel


def test_start_engine_preset_keeps_preset_argv(dash, start_cmds):
    """orchestrator.py presets keep the legacy --preset argv unchanged."""
    r = _start(dash, "normal_run")
    assert r.status_code == 200, r.get_json()
    cmd = next(c for c in start_cmds if "--instance" in c)
    assert cmd[cmd.index("--preset") + 1] == "normal_run"
    assert any(c.endswith("orchestrator.py") for c in cmd)
