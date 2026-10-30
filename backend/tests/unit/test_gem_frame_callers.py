"""Collection can observe a newer modal: old battle decisions must be discarded."""
from types import SimpleNamespace

import pytest


def test_harness_never_calls_action_or_stop_after_collection_sees_modal(monkeypatch):
    from runtime import harness
    battle, modal = object(), object()
    frames = iter([battle, modal])
    callbacks = []
    monkeypatch.setattr(harness.capture, "grab", lambda: next(frames))
    monkeypatch.setattr(harness.wave_reader, "read_wave", lambda frame: 100 if frame is battle else None)
    monkeypatch.setattr(harness.shard, "GemWatch", lambda **kw: SimpleNamespace(poll=lambda frame: modal))
    monkeypatch.setattr(harness.shard, "_in_run", lambda frame: False)
    monkeypatch.setattr(harness, "OFF_BATTLE_FRAMES", 1)
    monkeypatch.setattr(harness.logger, "shot", lambda *a: None)
    run = harness.Run("synthetic", in_run=lambda frame, wave: callbacks.append("action"),
                      stop=lambda frame, wave: callbacks.append("stop") or True)
    with pytest.raises(harness.Abort, match="left the battle screen"):
        run.watch()
    assert callbacks == []


def test_harness_keeps_callbacks_for_unchanged_frame(monkeypatch):
    from runtime import harness
    battle = object()
    callbacks = []
    monkeypatch.setattr(harness.capture, "grab", lambda: battle)
    monkeypatch.setattr(harness.wave_reader, "read_wave", lambda frame: 100)
    monkeypatch.setattr(harness.shard, "GemWatch", lambda **kw: SimpleNamespace(poll=lambda frame: frame))
    run = harness.Run("synthetic", in_run=lambda frame, wave: callbacks.append((frame, wave)),
                      stop=lambda frame, wave: True)
    assert run.watch() == 100
    assert callbacks == [(battle, 100)]


def test_sm_ride_rechecks_target_after_collection_changes_screen(monkeypatch):
    from flows import quest_sm, shard
    from device import capture
    from vision import wave_reader
    from runtime import logger
    stale, modal, fresh = object(), object(), object()
    frames = iter([stale, fresh])
    events = []
    monkeypatch.setattr(capture, "grab", lambda: next(frames))
    monkeypatch.setattr(wave_reader, "read_wave", lambda frame: 100 if frame is stale else 105)
    monkeypatch.setattr(shard, "cancel_sprint", lambda **kw: None)
    monkeypatch.setattr(shard, "GemWatch", lambda **kw: SimpleNamespace(
        poll=lambda frame: modal if frame is stale else frame))
    monkeypatch.setattr(logger, "event", lambda kind, **kw: events.append((kind, kw)))
    run = SimpleNamespace(guard=lambda f, *a: f, check=lambda f: f,
                          observe=lambda f: f, pause=lambda seconds: None)
    quest_sm.ride_to(100, run)
    assert events == [("sm_ride", {"wave": 105, "stage": "target reached"})]
