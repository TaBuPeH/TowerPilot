"""Saved collector settings must validate and reach the actual runtime."""
import pytest

from player.gather_settings import resolve_ad_gem_options, validate_ad_gem_options


@pytest.mark.parametrize("options", [
    {"edge_inset_px": 4}, {"edge_inset_px": True}, {"edge_inset_px": 6.5},
    {"poll_sec": [0, 4]}, {"poll_sec": [3, 2]}, {"poll_sec": [True, 4]},
    {"poll_sec": [2, float("inf")]}, {"poll_sec": [float("nan"), 4]},
    {"retry_cooldown_sec": [2]}, {"confirmed_cooldown_sec": [2, 3601]},
    {"attempt_timeout_sec": True}, {"attempt_timeout_sec": 4},
    {"attempt_timeout_sec": 61}, {"attempt_timeout_sec": [5, 60]},
    {"attempt_timeout_sec": float("nan")}, {"attempt_timeout_sec": float("inf")},
    {"unknown": 1}, [],
])
def test_invalid_options_rejected_by_validator_and_runtime(options):
    assert validate_ad_gem_options(options)
    with pytest.raises(ValueError):
        resolve_ad_gem_options(options)


def test_partial_options_do_not_share_mutable_defaults():
    first = resolve_ad_gem_options({"edge_inset_px": 9})
    first["poll_sec"][0] = 30
    second = resolve_ad_gem_options()
    assert first["edge_inset_px"] == 9
    assert second["edge_inset_px"] == 5
    assert second["poll_sec"] == [2, 4]


def test_compiled_gather_settings_reach_live_collector():
    from player import playerprofile
    from flows.shard import GemWatch
    from test_playerprofile import _base_profile
    profile = _base_profile()
    options = {"edge_inset_px": 9, "poll_sec": [1.5, 3],
               "confirmed_cooldown_sec": [40, 50], "retry_cooldown_sec": [8, 9],
               "attempt_timeout_sec": 25.5}
    profile["policies"]["gather"]["all_on"]["ad_gem_options"] = options
    gather = playerprofile.compile_preset(profile, "coin_default")["gather"]
    assert GemWatch(gather).ad_gems.options == options
    assert not playerprofile._validate_gather("gather", gather)
    gather["ad_gem_options"]["edge_inset_px"] = 1
    assert any("edge_inset_px" in e for e in playerprofile._validate_gather("gather", gather))


@pytest.mark.parametrize('duration', [5, 25.5, 60])
def test_window_duration_accepts_finite_scalar_boundaries(duration):
    assert validate_ad_gem_options({'attempt_timeout_sec': duration}) == []
    assert resolve_ad_gem_options({'attempt_timeout_sec': duration})['attempt_timeout_sec'] == duration


def test_default_window_schedule():
    options = resolve_ad_gem_options()
    assert options['confirmed_cooldown_sec'] == [720, 720]
    assert options['attempt_timeout_sec'] == 60
