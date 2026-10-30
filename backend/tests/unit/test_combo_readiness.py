"""Combo's Start gate (review R10): Combo runs every blueprint its day plan
names, so it is ready only when every one of those runs is - on every day of
the week, not just today - and an unready one is named with its block.
Before, api_control skipped the check for combo and /api/runs left it out, so
an unready block surfaced only at the handoff (combo.py readiness.require)."""

import paths as test_paths

import importlib.util
import os

import pytest
import yaml

from player import readiness

BACKEND = str(test_paths.BACKEND)

PROFILE = {
    "blueprints": {"coin": {"kind": "coin"}, "shard": {"kind": "shard"}},
    "plan": {
        "week": {"default": "normal", "saturday": "weekend"},
        "days": {
            "normal": [{"blueprint": "coin", "block": "farm"}],
            "weekend": [{"blueprint": "shard", "block": "night"}],
        },
    },
}
RUNS = {
    "bp_coin": {"kind": "coin"},
    "bp_shard": {"kind": "shard"},
    "combo": {"runner": "scheduling/combo.py"},
}


def _row(rel, why):
    return {"alternatives": [rel], "reasons": [why], "blocking": True}


def _fake_check(missing_by_kind):
    def check(root, cfg, body):
        missing = [_row(rel, "Screen recognition") for rel in missing_by_kind.get(body["kind"], [])]
        return {"ready": not missing, "required": missing, "missing": missing, "advisory": []}

    return check


def test_check_plan_names_the_unready_block_from_any_day(monkeypatch):
    from player import playerprofile

    week = playerprofile.compile_plan(PROFILE)["week"]
    monkeypatch.setattr(readiness, "check", _fake_check({"shard": ["digits/0.png"]}))
    result = readiness.check_plan(BACKEND, {}, RUNS, week)
    assert result["ready"] is False
    # only Saturday runs the shard block, and it still holds Combo back
    assert [row["alternatives"] for row in result["missing"]] == [["digits/0.png"]]
    assert result["missing"][0]["runs"] == ["bp_shard (night)"]
    assert result["missing"][0]["reasons"] == ["bp_shard (night): Screen recognition"]


def test_check_plan_merges_one_image_missing_for_two_runs(monkeypatch):
    from player import playerprofile

    week = playerprofile.compile_plan(PROFILE)["week"]
    monkeypatch.setattr(
        readiness, "check", _fake_check({"coin": ["digits/0.png"], "shard": ["digits/0.png"]})
    )
    missing = readiness.check_plan(BACKEND, {}, RUNS, week)["missing"]
    assert len(missing) == 1
    assert missing[0]["runs"] == ["bp_coin (farm)", "bp_shard (night)"]


def test_check_plan_is_ready_when_every_plan_run_is(monkeypatch):
    from player import playerprofile

    week = playerprofile.compile_plan(PROFILE)["week"]
    monkeypatch.setattr(readiness, "check", _fake_check({}))
    assert readiness.check_plan(BACKEND, {}, RUNS, week)["ready"] is True


@pytest.fixture()
def dash(tmp_path, monkeypatch):
    path = str(test_paths.DASHBOARD)
    spec = importlib.util.spec_from_file_location("tp_dashboard_combo", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    root = tmp_path / "backend"
    (root / "profiles").mkdir(parents=True)
    (root / "profiles" / "me.yaml").write_text(yaml.safe_dump(PROFILE), encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", str(root))
    monkeypatch.setattr(
        mod,
        "load_config",
        lambda: {"active_instance": "main", "active_profile": "me", "instances": {"main": {}}},
    )
    monkeypatch.setattr(mod, "_compiled_runs", lambda cfg: dict(RUNS))
    monkeypatch.setattr(readiness, "check", _fake_check({"shard": ["digits/0.png"]}))
    return mod


def test_api_runs_reports_combo_not_ready_with_the_block(dash):
    with dash.app.test_client() as c:
        combo = c.get("/api/runs").get_json()["readiness"]["combo"]
    assert combo["ready"] is False
    assert combo["missing"] == ["bp_shard (night): digits/0.png"]


def test_start_combo_is_refused_while_a_plan_run_is_unready(dash, monkeypatch):
    monkeypatch.setattr(dash, "_procs", lambda: [])

    def no_spawn(*a, **k):
        raise AssertionError("combo must not be spawned")

    monkeypatch.setattr(dash.subprocess, "Popen", no_spawn)
    with dash.app.test_client() as c:
        r = c.post("/api/control", json={"action": "start", "preset": "combo"})
    assert r.status_code == 409
    assert "bp_shard (night)" in r.get_json()["error"]
