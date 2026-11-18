"""The fleet Nuke fires on SIGHT of the fleet pentagon, blue or not (user,
2026-10-18: a 10-wave hold lost runs at 3753 and 4498 x2 to the fleet's
Scatter swarm). Fleets are big outlined pentagons, one random type per
spawn: saboteur red, commander orange, overcharge blue. Frames here are
synthetic; no game image is involved."""
import cv2
import numpy as np
import pytest

import orchestrator
from vision import fleet_scan

FIELD = (0, 300, 1080, 1330)
HUE = {"saboteur": 177, "commander": 15, "overcharge": 127}


def _poly(img, centre, radius, hue, sides, thick=14):
    pts = np.array([[centre[0] + radius * np.sin(2 * np.pi * i / sides),
                     centre[1] - radius * np.cos(2 * np.pi * i / sides)]
                    for i in range(sides)], np.int32)
    col = cv2.cvtColor(np.uint8([[[hue, 230, 255]]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()
    cv2.polylines(img, [pts], True, col, thick, cv2.LINE_AA)


def _frame():
    return np.zeros((2560, 1080, 3), np.uint8)


@pytest.mark.parametrize("radius", [30, 45, 70])
@pytest.mark.parametrize("kind", sorted(HUE))
def test_each_fleet_pentagon_is_counted_under_its_own_type_only(kind, radius):
    f = _frame()
    _poly(f, (400, 900), radius, HUE[kind], 5)
    got = fleet_scan.scan(f, FIELD)
    assert got == {k: int(k == kind) for k in HUE}


def test_triangles_and_squares_sharing_a_hue_are_not_fleets():
    f = _frame()
    for hue in HUE.values():
        _poly(f, (300, 700), 45, hue, 3)
        _poly(f, (600, 900), 45, hue, 4)
    assert fleet_scan.scan(f, FIELD) == {k: 0 for k in HUE}


def test_small_ordinary_enemies_are_below_the_fleet_size():
    f = _frame()
    _poly(f, (300, 700), 12, HUE["saboteur"], 5, thick=5)
    assert fleet_scan.scan(f, FIELD)["saboteur"] == 0


def test_side_menu_column_is_excluded():
    f = _frame()
    _poly(f, (950, 600), 45, HUE["commander"], 5)
    assert fleet_scan.scan(f, FIELD)["commander"] == 1
    assert fleet_scan.scan(f, FIELD, exclude=(840, 0, 1080, 1100))["commander"] == 0


@pytest.fixture
def gate(monkeypatch):
    monkeypatch.setattr(orchestrator.logger, "event", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.logger, "shot", lambda *a, **k: "")
    monkeypatch.setitem(orchestrator.CONFIG["rois"], "field", list(FIELD))
    monkeypatch.setitem(orchestrator.CONFIG, "fleet", {})
    return orchestrator.RunState()


def test_blue_fleet_fires_at_once(gate):
    f = _frame()
    _poly(f, (400, 900), 45, HUE["overcharge"], 5)
    assert orchestrator.fleet_gate(gate, f, 1495, 1498) == "blue_fleet"


@pytest.mark.parametrize("kind", ["saboteur", "commander"])
def test_other_fleets_fire_on_sight_too(gate, kind):
    f = _frame()
    _poly(f, (400, 900), 45, HUE[kind], 5)
    assert orchestrator.fleet_gate(gate, f, 4495, 4498) == f"fleet_{kind}"


def test_sighting_sticks_after_the_pentagon_leaves_the_frame(gate):
    f = _frame()
    _poly(f, (400, 900), 45, HUE["commander"], 5)
    assert orchestrator.fleet_gate(gate, f, 3745, 3748) == "fleet_commander"
    assert orchestrator.fleet_gate(gate, _frame(), 3745, 3750) == "fleet_commander"


def test_no_sighting_never_fires(gate):
    assert orchestrator.fleet_gate(gate, _frame(), 1495, 1520) is None


def test_gate_can_be_switched_off_for_the_plain_schedule(gate, monkeypatch):
    monkeypatch.setitem(orchestrator.CONFIG, "fleet", {"sighting": {"enabled": False}})
    assert orchestrator.fleet_gate(gate, _frame(), 1495, 1498) == "schedule"


def test_fire_skip_is_logged_once_per_mark_with_the_button_state(gate, monkeypatch):
    events = []
    monkeypatch.setattr(orchestrator.logger, "event",
                        lambda kind, **kw: events.append((kind, kw)))

    class St:
        present, ready, score, center = False, False, 0.0, None
    monkeypatch.setattr(orchestrator.detect, "button_state", lambda *a, **k: St())
    for wave in (1498, 1503):
        orchestrator.fleet_fire_skipped(gate, _frame(), 1495, wave, "blue_fleet")
    assert [e[0] for e in events] == ["fleet_fire_skipped"]
    assert events[0][1]["mark"] == 1495 and events[0][1]["present"] is False
