"""Setup can discover ownership before run-profile validation is possible."""
import pytest
import settings
from player import playerprofile


def test_setup_binds_unscanned_account_but_runs_still_validate(monkeypatch):
    cfg = {
        "active_instance": "main", "active_profile": "unscanned",
        "instances": {"main": {"account": "new", "serial": "127.0.0.1:16416",
                                "preset": "missing_run", "rois": {"test": [1, 2, 3, 4]}}},
        "accounts": {"new": {"active_profile": "unscanned", "loadouts": {"farm": {}}}},
        "rois": {}, "presets": {}, "preset": "missing_run",
    }
    monkeypatch.setattr(settings, "CONFIG", cfg)
    monkeypatch.setattr(settings, "_DEFAULTS", {"rois": {}})
    monkeypatch.setattr(settings, "_ACCOUNT_DEFAULTS", {})
    calls = []
    def reject(name):
        calls.append(name)
        raise ValueError("ownership not scanned")
    monkeypatch.setattr(playerprofile, "select_profile", reject)
    settings.bind_device("main")
    assert calls == []
    assert cfg["active_profile"] == "unscanned"
    assert cfg["rois"]["test"] == [1, 2, 3, 4]
    assert cfg["loadouts"] == {"farm": {}}
    with pytest.raises(ValueError, match="ownership not scanned"):
        settings.select_instance("main")
    assert calls == ["unscanned"]


def test_setup_rejects_unknown_device(monkeypatch):
    monkeypatch.setattr(settings, "CONFIG", {"instances": {}})
    with pytest.raises(KeyError, match="unknown instance"):
        settings.bind_device("missing")
