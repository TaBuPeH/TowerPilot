"""Housekeeping retention sweep - root anchoring and fail-loud pins.

The 2026-08-24 package restructure moved housekeeping.py into scheduling/
and its script-dir ROOT silently swept the nonexistent scheduling/logs/
("removed 0, freed 0.0 MB, ok: true" daily) while logs/ grew to 118 GB.
These pins make both halves of that failure impossible to reintroduce:
the anchor must be the backend root, and a missing logs dir must raise
instead of returning a clean-looking zero result.
"""
import os
import time

import pytest

from scheduling import housekeeping


def test_root_is_backend_root():
    backend = os.path.dirname(os.path.dirname(os.path.abspath(
        housekeeping.__file__)))
    assert housekeeping.ROOT == backend


def test_missing_logs_dir_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(housekeeping, "ROOT", str(tmp_path / "nowhere"))
    with pytest.raises(RuntimeError, match="housekeeping root is wrong"):
        housekeeping.sweep()


def test_sweep_applies_retention(monkeypatch, tmp_path):
    inst = tmp_path / "logs" / "player1"
    inst.mkdir(parents=True)
    old = time.time() - 100 * 86400
    fresh_files = ["events_new.jsonl", "20260901_shot.png",
                   "20260901_danger_1.png"]
    stale_files = ["events_old.jsonl", "20260101_shot.png",
                   "20260101_danger_1.png"]
    for name in fresh_files + stale_files:
        p = inst / name
        p.write_bytes(b"x" * 10)
        if name in stale_files:
            os.utime(p, (old, old))
    monkeypatch.setattr(housekeeping, "ROOT", str(tmp_path))
    result = housekeeping.sweep()
    assert result["removed"] == {"danger": 1, "shot": 1, "events": 1}
    survivors = sorted(f.name for f in inst.iterdir())
    assert survivors == sorted(fresh_files)
