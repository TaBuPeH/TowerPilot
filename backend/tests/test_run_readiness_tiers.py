"""Per-run readiness: each run type is gated on ITS OWN images. Recognition
guards for screens setup cannot produce on demand (tournament, welcome-back,
reward skip) are advisory - listed, never blocking - except for the run kind
that drives that screen. The Control view opens once a scan has finished,
attention or not; the run picker greys out the runs that truly lack images."""
import json

import cv2
import numpy as np

from player import accounts, readiness, scan_plan


def _cfg():
    return {"active_instance": "main", "instances": {"main": {}}, "loadouts": {}}


def _coin():
    return {"kind": "coin", "loadout": None, "shopping": [], "gather": {
        "flying_gem": False, "ad_gems": False, "quests_8h": False,
        "quest_rewards": False, "guild": False}}


def _image(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.random.default_rng(7).integers(0, 256, (20, 30, 3), dtype=np.uint8))


def _rows(cfg, body):
    return {row["alternatives"][0]: row for row in readiness.requirements(cfg, body)}


def test_second_wind_only_required_by_explicit_timing_rule():
    body = _coin()
    body['rules'] = [{'when': {'wave': 1000}, 'fire': 'nuke'}]
    assert 'buttons/nuke.png' in _rows(_cfg(), body)
    assert 'floaters/second_wind.png' not in _rows(_cfg(), body)
    body['rules'][0]['when'] = {'second_wind': {'state': 'after_immunity'}}
    assert _rows(_cfg(), body)['floaters/second_wind.png']['blocking']


def test_wall_rescue_without_second_wind_does_not_require_badge():
    body = _coin()
    body['abilities'] = {'dm_below': .02, 'hold_until_second_wind': False}
    assert 'floaters/second_wind.png' not in _rows(_cfg(), body)
    body['abilities']['hold_until_second_wind'] = True
    assert 'floaters/second_wind.png' in _rows(_cfg(), body)


def test_tournament_guards_are_advisory_for_coin_and_blocking_for_tournament():
    rows = _rows(_cfg(), _coin())
    for rel in readiness.TOURNAMENT_GUARDS | {"home/welcome_back_dialog.png", "buttons/reward_skip.png"}:
        assert rows[rel]["blocking"] is False, rel
    for rel in readiness.CORE:
        assert rows[rel]["blocking"] is True, rel
    tourney = _rows(_cfg(), {"kind": "tournament", "loadout": None, "gather": {}})
    for rel in readiness.TOURNAMENT_GUARDS:
        assert tourney[rel]["blocking"] is True, rel
    # welcome-back stays advisory even there: nobody can force the resume dialog
    assert tourney["home/welcome_back_dialog.png"]["blocking"] is False


def test_coin_is_ready_without_tournament_screens_and_lists_them_as_advisory(tmp_path):
    cfg, body = _cfg(), _coin()
    for row in readiness.requirements(cfg, body):
        if row["blocking"]:
            _image(accounts.template_path(tmp_path, cfg, row["alternatives"][0], write=True))
    result = readiness.check(tmp_path, cfg, body)
    assert result["ready"] is True
    assert result["missing"] == []
    advisory = {r["alternatives"][0] for r in result["advisory"]}
    assert "tourney/in_tournament.png" in advisory and "home/welcome_back_resume.png" in advisory
    readiness.require(tmp_path, cfg, body)          # does not raise


def test_tournament_is_not_ready_without_its_own_screens(tmp_path):
    cfg = _cfg()
    coin_body, tourney_body = _coin(), {"kind": "tournament", "loadout": None, "gather": {}}
    for row in readiness.requirements(cfg, coin_body):
        if row["blocking"]:
            _image(accounts.template_path(tmp_path, cfg, row["alternatives"][0], write=True))
    assert readiness.check(tmp_path, cfg, coin_body)["ready"]
    result = readiness.check(tmp_path, cfg, tourney_body)
    assert result["ready"] is False
    missing = {r["alternatives"][0] for r in result["missing"]}
    assert "tourney/in_tournament.png" in missing


def test_control_gate_opens_after_a_scan_that_finished_with_attention(tmp_path):
    cfg = {"active_instance": "main"}
    folder = accounts.calibration_dir(tmp_path, cfg)
    folder.mkdir(parents=True)
    for rel in scan_plan.missing_navigation(tmp_path, cfg, []):
        _image(accounts.template_path(tmp_path, cfg, rel, write=True))
    state = folder / "calibrate_state.json"
    state.write_text(json.dumps({"phases": {"bootstrap": {"status": "needs_attention"}}}))
    gate = scan_plan.control_gate(tmp_path, cfg)
    assert gate["ready"] and gate["scan_complete"]
    state.write_text(json.dumps({"phases": {"bootstrap": {"status": "running"}}}))
    assert not scan_plan.control_gate(tmp_path, cfg)["ready"]


def test_api_runs_carries_per_run_readiness(monkeypatch):
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))
    import dashboard as dash
    runs = {"bp_coin": {"kind": "coin", "label": "Coin"},
            "bp_tourney": {"kind": "tournament", "label": "Tournament"},
            "combo": {"runner": "scheduling/combo.py"}}
    monkeypatch.setattr(dash, "load_config", lambda: {"active_instance": "main", "active_profile": "me"})
    monkeypatch.setattr(dash, "_compiled_runs", lambda cfg: runs)
    fake = {"bp_coin": {"ready": True, "missing": [], "advisory": [{"alternatives": ["tourney/in_tournament.png"], "reasons": []}]},
            "bp_tourney": {"ready": False, "missing": [{"alternatives": ["tourney/in_tournament.png"], "reasons": ["Tournament screens"]}], "advisory": []}}
    monkeypatch.setattr(readiness, "check", lambda root, cfg, body: fake[
        "bp_coin" if body["kind"] == "coin" else "bp_tourney"])
    with dash.app.test_client() as client:
        r = client.get("/api/runs").json
    coin = r["readiness"]["bp_coin"]
    assert (coin["ready"], coin["missing"], coin["advisory"]) == (True, [], ["tourney/in_tournament.png"])
    assert coin["details"] == []                       # one detail row per MISSING image only
    assert r["readiness"]["bp_tourney"]["ready"] is False
    assert r["readiness"]["bp_tourney"]["missing"] == ["tourney/in_tournament.png"]
    assert "combo" not in r["readiness"]
