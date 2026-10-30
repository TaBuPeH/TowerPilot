"""shard.run()'s stop-flag branch: the run is closed to HOME, then the
cards_restore puts the farming deck back.

When that close raises Abort the screen is not what the code expected
(CLAUDE.md hard rule 7: stop and log, never blind-tap). The stop branch used
to log the Abort, break, and run cards_restore anyway - loadout.apply_cards
-> tourney.open_nav -> a tap on the Cards nav icon over that unknown screen.
The real shard.run and loadout.apply_cards run here; only the screen-facing
leaves (abandon_run, act.tap) are stubbed, so nothing reaches adb.
"""

import socket

import pytest


@pytest.fixture()
def stop_requested(monkeypatch):
    from device import act, adbclient
    from flows import shard
    from interactions import loadout

    # an adbclient call a stub below misses meets a dead loopback port, never
    # the real adb server on 5037
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    monkeypatch.setattr(adbclient, "_HOST", s.getsockname())
    s.close()
    seen = {"taps": [], "events": [], "cards": []}
    monkeypatch.setattr(shard.runflag, "requested", lambda: "phase switch")
    monkeypatch.setattr(shard, "GemWatch", lambda **kw: None)
    monkeypatch.setattr(shard, "configured_loadout", lambda: "shard_farm")
    monkeypatch.setattr(loadout, "spec", lambda name: {"cards_restore": "farm"})
    monkeypatch.setattr(shard.logger, "event", lambda kind, **kw: seen["events"].append((kind, kw)))

    def tap(x, y, reason="", **kw):
        seen["taps"].append((x, y, reason))
        raise act.TapRefused("test: no taps reach a device")

    monkeypatch.setattr(act, "tap", tap)
    return seen


def test_an_aborted_stop_close_never_taps_into_the_unknown_screen(stop_requested, monkeypatch):
    from flows import shard

    def abandon_run(to_home=False):
        raise shard.Abort("no exit confirm dialog after tapping the exit button")

    monkeypatch.setattr(shard, "abandon_run", abandon_run)
    assert shard.run(loops=None, do_setup=False) == 0
    assert stop_requested["taps"] == []
    kinds = [k for k, _ in stop_requested["events"]]
    assert "shard_stop_close_fail" in kinds
    assert "shard_cards_restore" not in kinds


def test_a_closed_stop_still_restores_the_cards(stop_requested, monkeypatch):
    from flows import shard
    from interactions import loadout

    closed = []
    monkeypatch.setattr(shard, "abandon_run", lambda to_home=False: closed.append(to_home) or True)
    monkeypatch.setattr(loadout, "apply_cards", lambda preset: stop_requested["cards"].append(preset) or "already")
    assert shard.run(loops=None, do_setup=False) == 0
    assert closed == [True]
    assert stop_requested["cards"] == ["farm"]
    assert ("shard_cards_restore", {"preset": "farm", "result": "already"}) in stop_requested["events"]
