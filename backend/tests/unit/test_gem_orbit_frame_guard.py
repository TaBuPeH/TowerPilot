"""The 19:18 orbit-detection landing through the r11d merge: the tapper sends no input from a frame nobody verified.

battlefield_observation returns (ok, why, verified_frame); the tapper reads that frame instead of capturing another. test_gem_orbit_cycle.py asserts
the refusals (not live, static) and that the verified frame is used; nothing there asserts the defensive half of the check - an observation that
says live but carries no frame - so a merge that dropped it would have gone unnoticed.
"""
from flows import shard
from test_gem_orbit_cycle import cycle    # noqa: F401  (the tapper fixture of that file)


def test_a_live_observation_without_a_frame_sends_no_input_and_says_why(cycle, monkeypatch):
    state, tapper = cycle
    monkeypatch.setattr(shard, 'battlefield_observation', lambda *a: (True, 'live', None))
    tapper.poll(200)
    assert state.taps == []
    assert tapper.status()['last_refusal']['reason'] == 'battle_frame_unreadable'
