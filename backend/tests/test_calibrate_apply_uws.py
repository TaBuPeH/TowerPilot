"""Ultimate Weapon ownership comes off the calibration evidence.

The in-run UW panel lists only owned weapons and the consented battle pass
cuts each name label from it (`uw/<name>.png`). A verified label proves the
account owns that weapon - the fact `player.uws` records and the compiler
gates on when a blueprint binds a Chain Lightning plan. Without this bridge a
freshly set-up account keeps its starter "owns nothing" inventory while its
own templates say otherwise, and every weapon plan is refused (live,
2026-09-08: "mode 'fleet_marks' needs Chain Lightning, which the player does
not own" for an account whose uw/chain_lightning.png was verified).
"""
import copy
import glob
import importlib.util
import json
import os
import shutil

import pytest
import yaml

from player.calibration_report import owned_uws

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_owned_uws_reports_only_verified_labels_and_never_marks_unowned():
    entries = [
        {"rel": "uw/chain_lightning.png", "verified": True},
        {"rel": "uw/black_hole.png", "verified": True, "status": "exists"},
        {"rel": "uw/spotlight.png", "verified": False},           # failed cut
        {"rel": "buttons/retry.png", "verified": True},           # not a weapon
        {"verified": True},                                       # malformed row
    ]
    assert owned_uws(entries) == {"chain_lightning": True, "black_hole": True}
    assert owned_uws(None) == {}
    assert owned_uws([]) == {}


@pytest.fixture(scope="module")
def dash():
    path = os.path.join(os.path.dirname(BACKEND), "frontend", "dashboard.py")
    spec = importlib.util.spec_from_file_location("tp_dashboard_apply", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def bound(dash, tmp_path, monkeypatch):
    """A throwaway copy of the golden profile whose player owns NO Chain
    Lightning, and a calibration state whose verified cuts prove it does."""
    name = "zz_uwapply"
    src = os.path.join(BACKEND, "tests", "fixtures", "golden_profile.yaml")
    dst = os.path.join(BACKEND, "profiles", f"{name}.yaml")
    with open(src, encoding="utf-8") as fh:
        profile = yaml.safe_load(fh)
    profile["player"]["uws"]["chain_lightning"] = False
    with open(dst, "w", encoding="utf-8") as fh:
        yaml.safe_dump(profile, fh, sort_keys=False)
    state = {"phases": {"bootstrap": {"status": "needs_attention"}},
             "player": {"card_presets": ["main_farm"]},          # no uws recorded
             "entries": [{"rel": "uw/chain_lightning.png", "verified": True},
                         {"rel": "uw/black_hole.png", "verified": False}]}
    (tmp_path / "calibrate_state.json").write_text(json.dumps(state))
    cfg = {"active_profile": name, "active_instance": "main", "instances": {"main": {}},
           # the golden's blueprints bind these machine loadouts; the validator
           # only needs them to exist (the bound test account's config has none)
           "loadouts": {k: {} for k in ("coin_farm", "tourney_1", "dissonance",
                                        "shard_farm", "inner_land_mines_quest")}}
    monkeypatch.setattr(dash, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(dash, "_procs", lambda: [])
    monkeypatch.setattr(dash, "_calibration_dir", lambda cfg: str(tmp_path))
    yield name, dst, profile["player"]["uws"]
    for f in glob.glob(os.path.join(BACKEND, "profiles", f"{name}.yaml*")):
        os.remove(f)


def test_apply_writes_proven_ownership_into_the_profile(dash, bound):
    name, path, before = bound
    with dash.app.test_client() as c:
        r = c.post("/api/calibrate/apply", json={})
    assert r.status_code == 200, r.get_json()
    with open(path, encoding="utf-8") as fh:
        profile = yaml.safe_load(fh)
    uws = profile["player"]["uws"]
    assert uws["chain_lightning"] is True          # proven by the verified label
    assert uws["black_hole"] == before["black_hole"]   # an unverified cut proves nothing
    assert set(uws) == set(before)                     # the inventory is merged, not replaced
    assert profile["player"]["card_presets"] == ["main_farm"]
    # the golden binds farm_cl_choreo (fleet_marks) to its coin run: it now compiles
    from player import playerprofile as pp
    profile["_name"] = name
    assert not [p for p in pp.validate(profile) if "Chain Lightning" in p]


def test_scanner_records_proven_ownership_in_the_state_it_writes(tmp_path):
    """The starter scan writes player.uws from its own verified UW cuts, so the
    calibrate state Apply reads already carries the ownership - no second
    pass over the entries needed for states written from now on."""
    import numpy as np
    from player import bootstrap, calibrate
    cal = calibrate.Calibration({"state": str(tmp_path / "state.json")}, False, bootstrap=True)
    cal.entries = [{"rel": "uw/chain_lightning.png", "verified": True},
                   {"rel": "uw/spotlight.png", "verified": False}]
    scanner = bootstrap.Scanner(cal, {}, grab=lambda: np.zeros((2560, 1080, 3), np.uint8),
                                read=lambda f: [], tap=lambda *a, **k: None, pause=lambda _: None)
    scanner.progress("checking")
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["player"]["uws"] == {"chain_lightning": True}
    assert scanner.cal.player["uws"] == {"chain_lightning": True}
