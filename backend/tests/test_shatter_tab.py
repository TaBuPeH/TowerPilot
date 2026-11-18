"""The shatter chore must never tap a grid it has not identified (2026-09-16).

It tapped the Shatter tab at a remembered point that the game had moved out
from under it (y=923; the bar is at y 1000-1100), decided it had arrived
because one band of the frame was bright enough, and then blind-tapped twelve
inventory tiles - where the first opens a module detail panel and the rest
land on that panel's buttons. The saved frame is the game asking "Equip Matrix
Sim to the primary slot or assist slot?".

These tests hold the three properties that make that impossible: the tab is
READ and tapped where it was read, an unreadable bar costs zero taps, and the
first tile has to be seen to stage before the other eleven are tapped."""
import types

import numpy as np
import pytest

from interactions import shatter
from player.bootstrap_layout import manifest

TAB_TOP, TAB_BOTTOM = manifest()["module_inventory"]["inventory_tab"]
# How the four labels actually read, measured 2026-09-16 over six of the
# player's own Modules frames, as OCR rows (y, x, text) inside the band crop.
# The Shatter label lands at (589, 13) there, so its tap point is (609, 1023)
# - the remembered point that caused the incident was (682, 923).
BAR = [(13, 18, "Inventory"), (13, 332, "Merge"),
       (13, 589, "Shatter"), (12, 873, "Assist")]
# The same bar while a module detail panel covered it, measured off the frame
# saved at the 2026-09-16 abort - the case that used to pass on brightness.
COVERED = [(13, 18, "Inv"), (15, 989, "it")]


def _frame():
    return np.zeros((2560, 1080, 3), np.uint8)


class Bot:
    """Every device edge the flow touches, recorded rather than performed."""

    def __init__(self, monkeypatch, bar=BAR, panel=False, closes=True):
        self.taps, self.events, self.shots = [], [], []
        self.bar, self.panel, self.closes = bar, panel, closes
        self.closed = 0
        self.frames = []
        self.screen_name = "modules"
        monkeypatch.setattr(shatter.capture, "grab", lambda *a, **k: self._grab())
        monkeypatch.setattr(shatter.act, "tap", self._tap)
        monkeypatch.setattr(shatter, "tap_at",
                            lambda pt, reason: self._tap(pt[0], pt[1], reason=reason))
        monkeypatch.setattr(shatter.screen, "identify",
                            lambda f: types.SimpleNamespace(name=self.screen_name))
        monkeypatch.setattr(shatter.textocr, "read_lines", self._ocr)
        monkeypatch.setattr(shatter.pills, "pills", lambda f, y0, y1: [])
        monkeypatch.setattr(shatter.inventory, "_panel_open",
                            lambda f=None: self.panel)
        monkeypatch.setattr(shatter.inventory, "_close_panel", self._close)
        monkeypatch.setattr(shatter.inventory, "settle",
                            lambda *a, **k: self._grab())
        monkeypatch.setattr(shatter.inventory, "park_top", lambda *a, **k: None)
        monkeypatch.setattr(shatter.logger, "event",
                            lambda kind, **kw: self.events.append((kind, kw)))
        monkeypatch.setattr(shatter.logger, "shot",
                            lambda f, tag: self.shots.append(tag) or tag)
        monkeypatch.setattr(shatter.time, "sleep", lambda s: None)

    # -- device edges
    def _grab(self):
        frame = _frame()
        self.frames.append(frame)
        return frame

    def _tap(self, x, y, reason="", instant=False):
        self.taps.append((x, y, reason))
        return {"x": x, "y": y}

    def _ocr(self, crop, scale=1.0):
        return self.bar if scale == 1.0 else [(0, 0, "Select modules to shatter")]

    def _close(self, tries=4):
        self.closed += 1
        if self.closes:
            self.panel = False
        return self.closes

    # -- readers
    def kinds(self):
        return [k for k, _ in self.events]

    def event(self, kind):
        return next(kw for k, kw in self.events if k == kind)


@pytest.fixture
def bot(monkeypatch):
    return Bot(monkeypatch)


# ------------------------------------------------------------- reading the bar
def test_the_bar_is_read_and_a_name_it_reads_twice_is_dropped(bot):
    bot.bar = BAR + [(13, 700, "Shatter")]
    points = shatter.tab_points(_frame())
    assert "shatter" not in points                 # ambiguous: never guessed
    assert points["merge"] == (352, TAB_TOP + 23)  # x+20, y+top+10


def test_a_covered_bar_reads_no_tab_at_all(bot):
    bot.bar = COVERED
    assert shatter.tab_points(_frame()) == {}


# ------------------------------------------------------------ reaching the tab
def test_the_tab_is_tapped_where_it_was_read(bot):
    shatter.open_shatter()
    assert bot.taps == [(609, TAB_TOP + 23, "Shatter tab")]
    assert bot.event("shatter_tab_tap") == {"x": 609, "y": TAB_TOP + 23}
    assert bot.event("shatter_tab")["tabs"] == ["assist", "inventory", "merge",
                                                "shatter"]


def test_an_unreadable_bar_aborts_with_zero_taps(bot):
    bot.bar = COVERED
    with pytest.raises(shatter.Abort):
        shatter.open_shatter()
    assert bot.taps == []
    assert bot.shots == ["shatter_tab_unreadable"]


def test_a_panel_is_closed_before_the_bar_is_trusted(monkeypatch):
    bot = Bot(monkeypatch, panel=True)
    shatter.open_shatter()
    assert bot.closed == 1
    assert bot.taps == [(609, TAB_TOP + 23, "Shatter tab")]


def test_a_panel_that_will_not_close_aborts_with_zero_taps(monkeypatch):
    bot = Bot(monkeypatch, panel=True, closes=False)
    with pytest.raises(shatter.Abort):
        shatter.open_shatter()
    assert bot.taps == []
    assert bot.shots == ["shatter_panel_stuck"]


def test_another_screen_is_reached_through_the_manifest_nav_point(bot):
    bot.screen_name = "home"
    with pytest.raises(shatter.Abort):
        shatter.open_shatter()
    assert bot.taps == [(*shatter.nav_modules(), "nav modules")]
    assert bot.shots == ["shatter_not_modules"]
    assert shatter.nav_modules() == tuple(manifest()["navigation"]["modules"])


# --------------------------------------------------------------- the batch
def _tiles(monkeypatch, n=15):
    """n blue tiles on the grid frame only: a tapped tile takes its green
    check, so it is no longer blue on any frame grabbed afterwards."""
    grid = _frame()
    points = [(126 + 203 * (i % 5), 1082 + 203 * (i // 5)) for i in range(n)]
    monkeypatch.setattr(shatter, "visible_blue",
                        lambda f: points if f is grid else [])
    monkeypatch.setattr(shatter, "tile_is_blue",
                        lambda f, x, y: f is grid and (x, y) in points)
    return grid, points


def test_twelve_at_most_and_every_tap_is_logged(bot, monkeypatch):
    grid, points = _tiles(monkeypatch)
    assert shatter.select_batch(grid) == shatter.BATCH_MAX
    assert [(x, y) for x, y, _ in bot.taps] == points[:shatter.BATCH_MAX]
    taps = [kw for k, kw in bot.events if k == "shatter_tap"]
    assert [(t["x"], t["y"]) for t in taps] == points[:shatter.BATCH_MAX]
    assert [t["n"] for t in taps] == list(range(1, shatter.BATCH_MAX + 1))
    assert bot.event("shatter_select") == {"staged": shatter.BATCH_MAX}


def test_a_tap_that_opens_a_panel_ends_the_batch_at_one_tap(bot, monkeypatch):
    grid, points = _tiles(monkeypatch)
    real_grab = bot._grab

    def grab_opens_panel():
        bot.panel = True
        return real_grab()
    monkeypatch.setattr(shatter.capture, "grab", lambda *a, **k: grab_opens_panel())
    with pytest.raises(shatter.Abort) as err:
        shatter.select_batch(grid)
    assert len(bot.taps) == 1                       # not twelve
    assert bot.closed == 1                          # and nothing left open
    assert bot.shots == ["shatter_panel_opened"]
    assert "not the Shatter tab" in str(err.value)


def test_a_first_tile_that_does_not_stage_stops_the_other_eleven(bot, monkeypatch):
    grid, points = _tiles(monkeypatch)
    monkeypatch.setattr(shatter, "tile_is_blue", lambda f, x, y: True)
    monkeypatch.setattr(shatter, "STAGE_WAIT", 0.0)
    with pytest.raises(shatter.Abort) as err:
        shatter.select_batch(grid)
    assert len(bot.taps) == 1
    assert bot.shots == ["shatter_tile_not_staged"]
    assert "did not stage" in str(err.value)


def test_nothing_blue_taps_nothing(bot, monkeypatch):
    monkeypatch.setattr(shatter, "visible_blue", lambda f: [])
    assert shatter.select_batch(_frame()) == 0
    assert bot.taps == []


# ---------------------------------------------------------------- the grid
def test_the_lattice_comes_from_the_frame_and_the_manifest(bot, monkeypatch):
    rows = [1082, 1285]
    monkeypatch.setattr(shatter.pills, "grid_rows", lambda f: rows)
    monkeypatch.setattr(shatter, "tile_is_blue", lambda f, x, y: True)
    assert shatter.visible_blue(_frame()) == [(x, y) for y in rows
                                              for x in shatter.inventory.COL_X]


@pytest.mark.parametrize("name", ["SHATTER_TAB", "GRID_COLS", "GRID_TOP",
                                  "GRID_PITCH", "GRID_BOTTOM", "FLING_TOP",
                                  "NAV_MODULES", "ensure_top",
                                  "staged_count_visible", "on_shatter_tab"])
def test_the_stale_private_geometry_is_gone(name):
    assert not hasattr(shatter, name), f"{name} is measured elsewhere now"


# -------------------------------------------------------------- confirming
def test_confirm_refuses_while_a_panel_covers_the_button(monkeypatch):
    bot = Bot(monkeypatch, panel=True, closes=True)
    with pytest.raises(shatter.Abort):
        shatter.confirm_dialog()
    assert bot.taps == []
    assert bot.shots == ["shatter_panel_before_confirm"]
    assert bot.closed == 1


def test_a_missing_dialog_leaves_no_modal_behind(bot, monkeypatch):
    monkeypatch.setattr(shatter, "find", lambda *a, **k: None)
    monkeypatch.setattr(shatter.time, "monotonic",
                        iter([0.0, 0.0, 99.0]).__next__)
    with pytest.raises(shatter.Abort):
        shatter.confirm_dialog()
    assert bot.taps == [(*shatter.CONFIRM_SHATTER, "Confirm Shatter")]
    assert bot.closed == 1
    assert bot.shots == ["shatter_no_dialog"]
