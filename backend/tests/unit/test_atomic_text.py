"""config.yaml and profiles are replaced atomically (review R7): a reader -
settings.py parsing config.yaml at runner import, a dashboard GET tick - sees
the complete old file or the complete new one, never a truncated one.
_save_yaml_backup used to open the target with "w" (truncate) and write in
place; the draft-promotion writer did the same."""

import paths as test_paths

import builtins
import importlib.util
import os
from pathlib import Path

import pytest
import yaml

from runtime import files

BACKEND = test_paths.BACKEND


@pytest.fixture()
def reader(monkeypatch):
    """Read `target` right after every file is opened for writing, after each
    write to it and when it closes - whichever writer runs (builtins.open or
    os.fdopen) - and record what a concurrent reader would have seen."""
    real_open, real_fdopen = builtins.open, os.fdopen
    state = {"target": None, "seen": []}

    def look():
        if state["target"] and os.path.exists(state["target"]):
            with real_open(state["target"], encoding="utf-8") as fh:
                state["seen"].append(fh.read())

    class Spy:
        def __init__(self, f):
            self.f = f
            look()

        def write(self, s):
            n = self.f.write(s)
            self.f.flush()
            look()
            return n

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            out = self.f.__exit__(*exc)
            look()
            return out

        def __getattr__(self, name):
            return getattr(self.f, name)

    def spy_open(file, mode="r", *a, **k):
        f = real_open(file, mode, *a, **k)
        return Spy(f) if "w" in mode else f

    def spy_fdopen(fd, mode="r", *a, **k):
        f = real_fdopen(fd, mode, *a, **k)
        return Spy(f) if "w" in mode else f

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(os, "fdopen", spy_fdopen)
    return state


def test_atomic_text_publishes_whole_text_and_leaves_no_temp(tmp_path, reader):
    path = tmp_path / "config.yaml"
    path.write_text("old: 1\n", encoding="utf-8")
    reader["target"] = str(path)
    files.atomic_text(path, "new: 2\n")
    assert path.read_text(encoding="utf-8") == "new: 2\n"
    assert set(reader["seen"]) == {"old: 1\n"}
    assert not list(tmp_path.glob("*.tmp"))


@pytest.fixture()
def dash():
    path = test_paths.DASHBOARD
    spec = importlib.util.spec_from_file_location("tp_dashboard_atomic", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_yaml_save_never_shows_a_reader_a_partial_file(dash, tmp_path, reader):
    path = tmp_path / "config.yaml"
    old = yaml.safe_dump({"rows": list(range(2000))}, sort_keys=False)
    path.write_text(old, encoding="utf-8")
    reader["target"] = str(path)
    backup = dash._save_yaml_backup(str(path), {"rows": list(range(3000))})
    new = path.read_text(encoding="utf-8")
    assert yaml.safe_load(new) == {"rows": list(range(3000))}
    assert reader["seen"] and set(reader["seen"]) <= {old, new}
    # backup first: it holds the old file
    assert (tmp_path / backup).read_text(encoding="utf-8") == old


def test_promoted_profile_never_shows_a_reader_a_partial_file(dash, tmp_path, monkeypatch, reader):
    pdir = tmp_path / "profiles"
    pdir.mkdir()
    (pdir / "base.yaml").write_text(yaml.safe_dump({"blueprints": {}}), encoding="utf-8")
    (pdir / "acct.draft.yaml").write_text(yaml.safe_dump({"player": {"max_tier": 3}}), encoding="utf-8")
    old = "# previous profile\nplayer: {max_tier: 1}\n"
    (pdir / "mine.yaml").write_text(old, encoding="utf-8")
    monkeypatch.setattr(dash, "_profiles_dir", lambda: str(pdir))
    reader["target"] = str(pdir / "mine.yaml")
    with dash.app.test_client() as c:
        r = c.post(
            "/api/profile-promote",
            json={"draft": "acct", "name": "mine", "base": "base", "overwrite": True},
        )
    assert r.status_code == 200, r.get_json()
    new = (pdir / "mine.yaml").read_text(encoding="utf-8")
    assert yaml.safe_load(new)["player"]["max_tier"] == 3
    assert reader["seen"] and set(reader["seen"]) <= {old, new}
