"""The Modules walk reuses the frame next_page() already settled on.

next_page() settles, drags, settles again and returns only the measured delta;
_walk_grid then settled a THIRD time at the top of the next page (another
>= 0.3 s sleep and two screencaps) for a frame it already held. inventory now
keeps the settled frame (take_page_frame) and the walk uses it - but only when
the glide really ended (two grabs alike); a grid still gliding after SETTLE_S
is never handed out and the walk settles itself as before. Synthetic frames,
a fake clock and a fake screencap: no device, no game images.
"""
import functools

import cv2
import numpy as np
import pytest

COLS = (126, 329, 533, 736, 939)


@functools.lru_cache(maxsize=None)
def _canvas(seed=1):
    rng = np.random.default_rng(seed)
    canvas = np.zeros((2560 + 2000, 1080, 3), np.uint8)
    for row in range(14):
        cy = 1198 + row * 203
        for cx in COLS:
            tile = rng.integers(0, 256, (150, 150, 3), dtype=np.uint8)
            tile[..., 0] = 255
            tile[..., 1] //= 3
            canvas[cy - 75:cy + 75, cx - 75:cx + 75] = tile
    return canvas


def _frame(shift=0, seed=1):
    """Rows of five random tiles scrolled up by `shift` px (what a settled grid
    frame looks like to the row projection); every distinct shift is a distinct
    frame, which is what lets a fake glide be told apart from a settled grid."""
    frame = _canvas(seed)[shift:shift + 2560].copy()
    frame[2260:] = 0
    return frame


class _Device:
    """A fake screencap + clock. Before the first swipe the grid sits at
    `pages[0]`; each swipe starts a glide: the next `glide` grabs are frames in
    motion (all different), then the grid is steady at the next page."""

    def __init__(self, monkeypatch, pages, glide=0):
        from interactions import inventory
        self.pages = pages
        self.glide_len = glide
        self.swipes = 0
        self.grabs_since_swipe = 0
        self.sleeps = []
        self.clock = 0.0
        self.glide_shifts = []
        monkeypatch.setattr(inventory.capture, "grab", self._grab)
        monkeypatch.setattr(inventory.act, "swipe", self._swipe)
        monkeypatch.setattr(inventory, "time", type("T", (), {"sleep": self._sleep, "monotonic": lambda s: self.clock})())

    def _swipe(self, *a, **k):
        self.swipes += 1
        self.grabs_since_swipe = 0

    def _sleep(self, s):
        self.sleeps.append(s)
        self.clock += s

    def _grab(self, *a, **k):
        if self.swipes == 0:
            return self.pages[0]
        self.grabs_since_swipe += 1
        if self.swipes == 1 and self.grabs_since_swipe <= self.glide_len:
            # a frame mid-glide: between the pages, different on every grab
            self.glide_shifts.append(40 + 7 * self.grabs_since_swipe)
            return _frame(self.glide_shifts[-1])
        return self.pages[min(self.swipes, len(self.pages) - 1)]


@pytest.fixture
def inventory(monkeypatch):
    from interactions import inventory as inv
    monkeypatch.setattr(inv, "_page_frame", None, raising=False)       # state is per test, never leaks
    return inv


def test_next_page_still_returns_the_measured_delta_and_drags_once(monkeypatch, inventory):
    dev = _Device(monkeypatch, [_frame(0), _frame(794), _frame(794)])
    swipes = []
    monkeypatch.setattr(inventory.act, "swipe", lambda *a, **k: swipes.append((a, k)) or dev._swipe())
    assert inventory.next_page() == 794
    y0, y1, ms = inventory.PAGE_DRAG
    assert swipes == [((inventory.SWIPE_X, y0, inventory.SWIPE_X, y1, ms), {"reason": "inventory page"})]
    assert inventory.next_page() == 0                                  # the grid did not move: the end of the list


def test_next_page_keeps_its_signature_for_sweep_and_module_roundtrip():
    """inventory.sweep and player/module_roundtrip.py (three call sites) call
    next_page() with no argument and only read its return value."""
    import inspect
    from interactions import inventory
    assert not inspect.signature(inventory.next_page).parameters


def test_next_page_reports_a_lost_overlap_as_none(monkeypatch, inventory):
    _Device(monkeypatch, [_frame(0), _frame(1900)])
    assert inventory.next_page() is None


def test_the_settled_frame_is_handed_out_once(monkeypatch, inventory):
    steady = _frame(794)
    _Device(monkeypatch, [_frame(0), steady])
    inventory.next_page()
    got = inventory.take_page_frame()
    assert got is not None and np.array_equal(got, steady)
    assert inventory.take_page_frame() is None


def test_a_grid_still_gliding_is_never_handed_out(monkeypatch, inventory):
    """Two grabs alike or nothing: the glide outlasts SETTLE_S, so there is no
    page frame for the walk to use."""
    dev = _Device(monkeypatch, [_frame(0), _frame(794)], glide=10_000)
    events = []
    import runtime.logger as lg
    monkeypatch.setattr(lg, "event", lambda kind, **kw: events.append(kind))
    inventory.next_page()
    assert inventory.take_page_frame() is None
    assert "inventory_unsettled" in events
    assert dev.glide_shifts                                            # the grid really was gliding


def test_a_glide_that_ends_in_time_hands_out_the_steady_frame_not_a_glide_frame(monkeypatch, inventory):
    steady = _frame(794)
    dev = _Device(monkeypatch, [_frame(0), steady], glide=6)
    inventory.next_page()
    got = inventory.take_page_frame()
    assert np.array_equal(got, steady)
    assert not any(np.array_equal(got, _frame(g)) for g in dev.glide_shifts)


# ----------------------------------------------------------- through the walk
class _WalkRun:
    """The real calibrate._walk_grid and the real inventory.settle/next_page
    over the fake device. The first page is parked (no scroll), the second page
    is the same grid moved up 794 px, the third does not move: the end."""

    def __init__(self, tmp_path, monkeypatch, glide=0, pages=(0, 794, 794)):
        from interactions import inventory
        from player import calibrate
        import runtime.logger as lg
        self.events = []
        self.dev = _Device(monkeypatch, [_frame(shift) for shift in pages], glide=glide)
        monkeypatch.setattr(inventory, "_close_panel", lambda tries=4: True)
        monkeypatch.setattr(inventory, "park_top", lambda tries=5: self.events.append("park_top"))
        monkeypatch.setattr(calibrate, "_inspect", lambda cx, cy, icon=None: (f"Module {cx}x{cy}", "epic"))
        monkeypatch.setattr(calibrate, "module_slug", lambda name: name.lower().replace(" ", "_"))
        monkeypatch.setattr(calibrate.Calibration, "cut", lambda s, phase, rel, crop, frame, name, extra=None: {"rel": rel})
        monkeypatch.setattr(lg, "event", lambda kind, **kw: self.events.append(kind))
        self.tmp = tmp_path
        p = {"state": "", "stop": str(tmp_path / "stop"), "report": "", "evidence": str(tmp_path)}
        calibrate._walk_grid(calibrate.Calibration(p, overwrite=False))

    def evidence(self, page):
        return cv2.imread(str(self.tmp / f"modules_grid_{page}.png"))


def test_the_walk_settles_once_for_page_zero_and_not_again_for_a_frame_it_holds(tmp_path, monkeypatch, inventory):
    run = _WalkRun(tmp_path, monkeypatch)
    # every settle of a still grid is one 0.3 s sleep: 1 for page 0 (after park_top)
    # + 2 inside each of the 2 next_page() calls (before and after the drag) = 5.
    # The walk used to add one more at the top of page 1.
    assert run.dev.sleeps.count(0.3) == 5
    assert run.events.index("park_top") < run.events.index("calibrate_grid_page")
    assert np.array_equal(run.evidence(0), _frame(0))
    assert np.array_equal(run.evidence(1), _frame(794))


def test_every_page_of_a_longer_walk_is_read_off_its_own_frame(tmp_path, monkeypatch, inventory):
    """Three moving pages: the frame held for page 2 is the one next_page() ended
    page 1 on, never page 1's again (a held frame that is not replaced)."""
    run = _WalkRun(tmp_path, monkeypatch, pages=(0, 794, 1588, 1588))
    assert [np.array_equal(run.evidence(i), _frame(shift)) for i, shift in enumerate((0, 794, 1588))] == [True] * 3
    assert run.events.count("calibrate_grid_page") == 3
    assert run.dev.sleeps.count(0.3) == 7                              # 1 for page 0 + 2 in each of 3 next_page() calls


def test_the_walk_never_reads_tiles_off_a_gliding_page_frame(tmp_path, monkeypatch, inventory):
    run = _WalkRun(tmp_path, monkeypatch, glide=6)
    assert np.array_equal(run.evidence(1), _frame(794))                # the steady frame, not a mid-glide one
    assert not any(np.array_equal(run.evidence(1), _frame(g)) for g in run.dev.glide_shifts)


def test_when_the_glide_outlasts_the_settle_budget_the_walk_settles_itself(tmp_path, monkeypatch, inventory):
    run = _WalkRun(tmp_path, monkeypatch, glide=30)
    assert "inventory_unsettled" in run.events                         # next_page gave up waiting ...
    assert np.array_equal(run.evidence(1), _frame(794))                # ... and the walk's own settle waited it out
    assert not any(np.array_equal(run.evidence(1), _frame(g)) for g in run.dev.glide_shifts)


def test_sweep_still_settles_at_the_top_of_every_page_and_pages_with_next_page(tmp_path, monkeypatch, inventory):
    """inventory.sweep is not the walk: it settles itself at the top of each page
    and only reads next_page()'s return value, whether or not a settled frame is
    held. Two pages (the second the same grid moved up 794 px, the third does
    not move): 1 settle + 2 inside next_page() per page = 6 sleeps of 0.3 s, one
    drag per page, a page image per page. No panel ever opens (every tile is
    tapped, found no panel and skipped), so no record is written."""
    dev = _Device(monkeypatch, [_frame(0), _frame(794), _frame(794)])
    taps, events = [], []
    import runtime.logger as lg
    monkeypatch.setattr(lg, "event", lambda kind, **kw: events.append((kind, kw)))
    monkeypatch.setattr(inventory.act, "tap", lambda *a, **k: taps.append(a))
    monkeypatch.setattr(inventory, "_close_panel", lambda tries=4: True)
    monkeypatch.setattr(inventory, "park_top", lambda tries=5: None)
    monkeypatch.setattr(inventory, "_panel_open", lambda frame: False)
    assert inventory.sweep(str(tmp_path)) == []
    assert dev.sleeps.count(0.3) == 6
    assert dev.swipes == 2
    assert len(taps) > 0
    assert [kw["moved"] for kind, kw in events if kind == "inventory_page"] == [794, 0]
    assert np.array_equal(cv2.imread(str(tmp_path / "page0_grid.png")), _frame(0))
    assert np.array_equal(cv2.imread(str(tmp_path / "page1_grid.png")), _frame(794))
