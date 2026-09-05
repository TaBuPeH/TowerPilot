"""Daily OBSERVATION of the quest board. Reads, never acts.

Runs once a day, immediately after a run ends (past 3 AM). That timing is not
arbitrary: a run has just ended, so the tower is not on screen, nothing is at
risk, and the bot already owns the navigation - the same reasoning that lets
runlog.collect() block on the death screen.

Phase 1 is deliberately just a CAMERA. It opens the board, photographs every
page and files the images by date; it does not classify anything and it does
not tap a single quest. Recognising a quest means matching its literal
rendered wording (the trick that made the shatter confirmation safe), and
those templates have to be cut from real captures of the board - which is
exactly what this produces. Guessing at quest text from a cropped screenshot
would bake in coordinates that are probably wrong.

What it will grow into, once there are captures to cut templates from:
  * an ALLOWLIST of known quests - anything unrecognised is ignored, never
    guessed at, because the whole point is to trigger a run and the wrong run
    costs an hour
  * the "25 / 250" progress read with the existing digit templates
  * a hand-off to whichever conditional run matches
"""
import datetime
import time

import cv2

from device import act
from device import capture
from runtime import logger
from interactions import missions
from interactions import tourney
from settings import ROOT

EVENT_ICON = "icons/event_calendar.png"
# The Missions TAB, not the "EVENT - JURASSIC" header: the event name changes
# every few days, the tab label does not.
MISSIONS_TAB = "icons/event_missions_tab.png"

MAX_PAGES = 8           # the board is a few screens; this is a runaway guard
OPEN_TRIES = 8          # ~4s for the missions screen to draw
FLING_MS = 180          # the user flings lists, they do not drag them
STILL = 1.0             # mean abs diff below which a fling moved nothing


def _shots_dir() -> "object":
    d = ROOT / "captures" / "quests" / datetime.date.today().isoformat()
    d.mkdir(parents=True, exist_ok=True)
    return d


def scan() -> int:
    """Photograph the quest board. Returns the number of pages captured.

    Returns 0 rather than raising on any hitch - this is a nice-to-have
    observation wedged into the gap between two runs, and it must never be the
    reason a farm loop stops.
    """
    try:
        frame = capture.grab()
        if not tourney.on_home(frame):
            tourney.ensure_home()
            frame = capture.grab()

        # The EVENT icon on the left rail - the blue calendar, NOT the daily
        # quests tile, which is a different board entirely. Located by
        # template because that rail SLIDES as timed events start and end
        # (tourney.py measured a 156px shift inside one recording), so any
        # fixed coordinate here is wrong within days.
        hit = tourney.find(frame, EVENT_ICON, 0.85)
        if not hit:
            logger.event("questscan", ok=False, reason="event icon not on home",
                         shot=logger.shot(frame, "questscan_no_icon"))
            return 0
        act.tap(*hit[0], reason="questscan_open_event", instant=False)

        for _ in range(OPEN_TRIES):
            time.sleep(0.5)
            frame = capture.grab()
            if tourney.find(frame, MISSIONS_TAB, 0.85):
                break
        else:
            logger.event("questscan", ok=False, reason="board never opened",
                         shot=logger.shot(frame, "questscan_open_fail"))
            missions.bail(frame, "questscan")
            return 0

        out = _shots_dir()
        pages = 0
        prev = None
        for i in range(MAX_PAGES):
            frame = capture.grab()
            cv2.imwrite(str(out / f"page{i:02d}.png"), frame)
            pages += 1
            if prev is not None and float(
                    cv2.absdiff(prev, frame).mean()) < STILL:
                break                       # bottom of the board
            prev = frame
            act.swipe(538, 2000, 538, 900, FLING_MS,
                      reason="questscan page down")
            time.sleep(0.6)

        logger.event("questscan", ok=True, pages=pages, dir=str(out))
        act.tap(*missions.RETURN_STRIP, reason="questscan_return",
                instant=True)
        time.sleep(1.0)
        return pages
    except Exception as e:                  # noqa: BLE001 - see docstring
        logger.event("questscan", ok=False, error=repr(e))
        try:
            missions.bail(capture.grab(), "questscan")
        except Exception:                   # noqa: BLE001
            pass
        return 0
