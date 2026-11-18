"""The return strip is a target, not a coordinate (2026-11-18).

Every reward flow left its menu with `tap (540, 2455); next frame; still
open? tap again`. On a busy emulator the frame answering "still open?" was
seconds old, the second tap arrived after the menu had closed, and it landed
on the battle screen's upgrade panel - where game 29.2 opens an Ultimate
Weapon's info popup. Twice in one evening that popup then covered the battle
(the second time for 53 minutes) with the run untended.

Same day: the free-gems visit repeated every five minutes all day (92 store
visits) because a card already on cooldown never closed the day, and because
the store reopens where it was left while the flow only ever scrolled down.
"""
import numpy as np
import pytest

from device import displays
from interactions import missions
from vision import detect, textocr

STRIP = (65, 640)                     # buttons/return_to_game.png, native px
STRIP_AT = (2430, 220)                # where the game draws it (y, x)
FREE_AT = (1555, 245)                 # the FREE label on the store's top page


def _menu(strip=True):
    """A menu frame; `strip` paints the return strip where the game does."""
    frame = np.zeros((2560, 1080, 3), np.uint8)
    frame[:200] = 90                  # a header, so the frame is not empty
    if strip:
        y, x = STRIP_AT
        frame[y:y + STRIP[0], x:x + STRIP[1]] = _strip_tpl()
    return frame


def _strip_tpl():
    rng = np.random.default_rng(7)
    return rng.integers(0, 255, (*STRIP, 3), dtype=np.uint8)


@pytest.fixture
def world(monkeypatch):
    """Real matching against a synthetic strip; taps and frames recorded."""
    taps, events, clock = [], [], {"now": 1000.0}
    tpls = {missions.RETURN_TEMPLATE: _strip_tpl()}

    def tpl(rel):
        if rel not in tpls:
            raise detect.TemplateMissing(rel)
        return tpls[rel]
    monkeypatch.setattr(detect, "_tpl", tpl)
    monkeypatch.setattr(detect, "_MISSING", set(tpls) | {"icons/free_gems.png"})
    monkeypatch.setattr(missions, "_tap",
                        lambda x, y, reason, instant=True: taps.append((reason, x, y)))
    monkeypatch.setattr(missions.logger, "event",
                        lambda kind, **kw: events.append((kind, kw)))
    monkeypatch.setattr(missions.logger, "shot", lambda frame, tag: tag + ".png")
    monkeypatch.setattr(missions.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(missions, "_return_tap_at", 0.0)
    return taps, events, clock, tpls


def _drive(gen, frames, clock, step=0.7):
    """Feed frames the way the observe loop does; the flow's return value."""
    try:
        next(gen)
        for frame in frames:
            clock["now"] += step
            gen.send(frame)
    except StopIteration as done:
        return done.value
    pytest.fail("flow never finished")


# ------------------------------------------------------------ the strip
def test_the_strip_is_found_where_it_is_drawn_and_nowhere_on_a_battle(world):
    assert missions.return_strip(_menu()) == (540, 2462)
    assert missions.return_strip(_menu(strip=False)) is None


def test_a_missing_image_reads_the_words_instead(world, monkeypatch):
    _, _, _, tpls = world
    tpls.clear()
    monkeypatch.setattr(textocr, "read_lines",
                        lambda crop, scale: [(524, 470, "Tap To Return To Game")])
    assert missions.return_strip(_menu(strip=False)) == (540, 2458)
    monkeypatch.setattr(textocr, "read_lines", lambda crop, scale: [(10, 10, "490.1 47s")])
    assert missions.return_strip(_menu(strip=False)) is None


# ------------------------------------------------- one tap, then patience
def test_a_stale_menu_frame_never_buys_a_second_tap(world, monkeypatch):
    """The 2026-11-18 race: the loop's frames still show the store for two
    seconds after the tap, while a frame taken NOW shows the battle."""
    taps, events, clock, _ = world
    live = {"frame": _menu()}

    def grab():
        return live["frame"]
    monkeypatch.setattr(missions.capture, "grab", grab)

    def flow():
        frame = yield
        return (yield from missions.leave_menu(frame, "gem_store_return"))
    gen = flow()
    next(gen)
    clock["now"] += 0.7
    gen.send(_menu())                           # tap 1 goes out
    live["frame"] = _menu(strip=False)          # ...and the menu closes
    stale = [_menu()] * 5 + [_menu(strip=False)]
    done = None
    try:
        for frame in stale:                     # the loop is behind
            clock["now"] += 0.7
            gen.send(frame)
    except StopIteration as stop:
        done = stop.value
    assert done is True
    assert [r for r, _, _ in taps] == ["return_to_game"]
    assert taps[0][1:] == (540, 2462)
    assert events == []


def test_a_strip_that_survives_is_tapped_again_only_after_it_settles(world, monkeypatch):
    taps, events, clock, _ = world
    monkeypatch.setattr(missions.capture, "grab", _menu)

    def flow():
        frame = yield
        return (yield from missions.leave_menu(frame, "guild_return"))
    assert _drive(flow(), [_menu()] * 60, clock) is False
    assert len(taps) == missions.RETURN_TAPS
    assert [k for k, _ in events] == ["mission_error"]
    assert events[0][1]["stage"] == "guild_return"


def test_bail_does_not_touch_a_battle_screen(world, monkeypatch):
    taps, _, _, _ = world
    monkeypatch.setattr(missions.capture, "grab", lambda: _menu(strip=False))
    missions.bail(_menu(), "guild_open")        # the frame it was handed is old
    assert taps == []
    monkeypatch.setattr(missions.capture, "grab", _menu)
    missions.bail(_menu(), "guild_open")
    assert [r for r, _, _ in taps] == ["bail_guild_open"]


# ------------------------------------------------------------ free gems
@pytest.fixture
def store(world, monkeypatch):
    """The gem store: `page` 0 is the top (free card on it), 1+ are below."""
    taps, events, clock, _ = world
    state = {"page": 0, "open": True, "words": "16h 45m"}

    def frame():
        f = _menu(strip=state["open"])
        f[0, 0, 0] = state["page"] if state["open"] else 200
        return f

    def match(hay, rel, thr):
        tag = int(hay[0, 0, 0]) if hay.shape[0] == 2560 else None
        if rel == missions.RETURN_TEMPLATE:
            return state["open"], 1.0, (STRIP_AT[1], STRIP_AT[0] - int(2560 * 0.85))
        if rel == "icons/premium_store.png":
            return tag is not None and tag < 200, 1.0, (0, 0)
        if rel == "icons/free_gems.png":
            return tag == 0, 1.0, (FREE_AT[1], FREE_AT[0])
        return False, 0.0, (0, 0)

    def swipe(x0, y0, x1, y1, ms, reason=""):
        state["page"] = max(0, min(3, state["page"] + (1 if y1 < y0 else -1)))
        taps.append((reason, x0, y0))

    def tap(x, y, reason, instant=True):
        taps.append((reason, x, y))
        if reason == "return_to_game":
            state["open"] = False
    monkeypatch.setattr(detect, "_match", match)
    monkeypatch.setitem(detect._TPL_CACHE, "icons/free_gems.png",
                        np.zeros((48, 130, 3), np.uint8))
    monkeypatch.setattr(detect, "_tpl",
                        lambda rel: np.zeros((STRIP if "return" in rel else (48, 130)) + (3,), np.uint8))
    monkeypatch.setattr(missions, "find_tile", lambda frame, rel, fallback=None: (910, 65))
    monkeypatch.setattr(missions, "_tap", tap)
    monkeypatch.setattr(missions.act, "swipe", swipe)
    monkeypatch.setattr(missions.capture, "grab", frame)
    monkeypatch.setattr(textocr, "read_lines",
                        lambda crop, scale: [(5, 5, state["words"])])
    return state, frame, taps, events, clock


def _run_free_gems(store, on_success):
    state, frame, taps, events, clock = store
    gen = missions.free_gems_flow(on_success=on_success)
    next(gen)
    for _ in range(40):
        clock["now"] += 0.7
        try:
            gen.send(frame())
        except StopIteration:
            return
    pytest.fail("free gems flow never finished")


def test_a_card_on_cooldown_closes_the_day_instead_of_retrying(store):
    state, _, taps, events, _ = store
    closed = []
    _run_free_gems(store, lambda: closed.append(True))
    assert closed == [True]
    assert [r for r, _, _ in taps] == ["gem_store_open", "return_to_game"]
    unavailable = [kw for k, kw in events if k == "free_gems_unavailable"]
    assert unavailable[0]["cooldown"] is True and unavailable[0]["words"] == "16H 45M"


def test_an_unreadable_button_neither_claims_nor_closes_the_day(store):
    state, _, taps, _, _ = store
    state["words"] = "?"
    closed = []
    _run_free_gems(store, lambda: closed.append(True))
    assert closed == []
    assert "free_gems_claim" not in [r for r, _, _ in taps]


def test_a_store_left_scrolled_down_is_searched_from_the_top(store):
    state, _, taps, _, _ = store
    state["page"] = 3                           # where the last visit left it
    closed = []
    _run_free_gems(store, lambda: closed.append(True))
    assert closed == [True]                     # the card was found after all
    reasons = [r for r, _, _ in taps]
    assert reasons.count("gem store to top") == missions.FREE_SCROLL_STRIDES
    assert "gem store scroll" not in reasons    # and no walk past it


# ------------------------------------------------- which display is the game
SF = ('Display 4619827820427265280 (HWC display 0): port=0 pnpId=GGL displayName="mumuscreen000"\n'
      'Display 4619827767814508545 (HWC display 1): port=1 pnpId=GGL displayName="mumuscreen001"\n'
      'Display 4619826888814064386 (HWC display 2): port=2 pnpId=GGL displayName="mumuscreen002"\n')
DD = ("  mDisplayId=0\n    mPrimaryDisplayDevice=Built-in Screen(local:4619827820427265280)\n"
      "  mDisplayId=2\n    mPrimaryDisplayDevice=mumuscreen001(local:4619827767814508545)\n"
      "  mDisplayId=3\n    mPrimaryDisplayDevice=mumuscreen002(local:4619826888814064386)\n")
GAME = "com.TechTreeGames.TheTower"


def _windows(*rows):
    return "".join(
        f"  Window #{i} Window{{71ecdd{i} u0 {title}}}:\n"
        f"    mDisplayId={disp} taskId=12{i} mSession=Session{{c884c41 30665:u0a10052}}\n"
        for i, (title, disp) in enumerate(rows))


def _shell(windows):
    return lambda cmd: (SF if "SurfaceFlinger" in cmd else
                        DD if cmd == "dumpsys display" else windows)


def test_the_game_display_is_where_the_games_window_is():
    """2026-11-18: a Play Store tab held mumuscreen001, the relaunched game
    came up on mumuscreen002, and "the first secondary" was the store page."""
    wins = _windows((GAME + "/com.unity3d.player.UnityPlayerActivity", 3),
                    ("com.android.vending/com.google.android.finsky.activities.MainActivityPrivate", 2),
                    ("StatusBar", 0))
    assert displays.window_display(wins, GAME) == 3
    assert displays.game_display(_shell(wins), GAME) == ("4619826888814064386", 3)


@pytest.mark.parametrize("wins", [
    pytest.param(_windows(("app.lawnchair/app.lawnchair.LawnchairLauncher", 0)),
                 id="game-not-running"),
    pytest.param(_windows((GAME + "/com.unity3d.player.UnityPlayerActivity", 0)),
                 id="game-on-the-default-display"),
    pytest.param(_windows((GAME + "/com.unity3d.player.UnityPlayerActivity", 9)),
                 id="display-nobody-lists"),
])
def test_without_a_game_window_the_first_secondary_still_answers(wins):
    assert displays.game_display(_shell(wins), GAME) == ("4619827767814508545", 2)
