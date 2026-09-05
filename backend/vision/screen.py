"""Which screen are we looking at?

The autopilot's single most important safety rule is "tower on screen or hands
off", and that rule is only as good as the answer to this question. Getting it
wrong is not a cosmetic bug: the stuck-popup recovery once fired while the user
was on the CARDS screen and switched their active card preset, because the code
knew "not in battle" but nothing more.

So this module answers with a NAME, not a boolean, and every caller that taps
anything can state which screen it expects to be on.

How it decides, in order:

  1. OVERLAYS first. A dialog sits ON TOP of a base screen - the game stats
     panel, the death dialog, a reward listing, the tournament heat sheet - so
     the base screen is still matchable underneath and would win otherwise.
  2. The HEADER WORD. Every menu paints its name in a purple bar at the top
     (y 104-166): TOURNAMENT, GUILD, CARDS, MODULES, BATTLE. One region, one
     template each, and they cannot collide with anything in the playfield.
  3. BATTLE vs HOME. Both wear the "BATTLE" header, so they are separated by
     what is under it: a readable wave counter means a run is on screen; the
     Dissonant Run button means the pre-run home screen.
  4. Anything else is UNKNOWN, and unknown means hands off. A screen this
     module cannot name is never a screen it is safe to click on.

Deliberately NOT used: header-bar colour. Measured across the known screens the
bands overlap almost completely (battle 84,48,50 vs tournament 91,47,52 vs
cards 87,40,48) - it separates nothing.
"""
from dataclasses import dataclass, field

import cv2

from vision import detect
from vision import wave_reader

HEADER_BAND = (104, 166)        # y range of the menu title bar
HEADER_X = (0, 620)             # titles are left-aligned within this
HDR_THRESH = 0.85
OVERLAY_THRESH = 0.90

# Checked BEFORE the header, because a dialog covers it. ORDER MATTERS - the
# entries are not mutually exclusive and the first hit wins:
#   * ticket_reward before reward_listing: the ticket screen carries the very
#     same SKIP pill, so the generic listing would swallow it.
#   * heat is matched on the Heat|Overheat TAB PAIR, not the dialog title: the
#     title changes to "OVERHEAT" on the second tab and the dialog would fall
#     through to plain 'tournament'.
# 'game_stats' covers both a death and a manual end-of-round - they are the
# same panel (Wave / Tier / RETRY / HOME); how it was reached is the caller's
# business, not this module's.
#
# Each carries the y BAND it can appear in. That is not a micro-optimisation:
# searching six templates over the full 1080x2560 frame costs ~900ms, and this
# has to run inside a 2 fps loop with a 500ms budget. Bands are generous
# (+-150px around the measured position) so a shifted dialog is still found.
OVERLAYS = [
    # A tournament run does NOT end on the farm run's GAME STATS panel. It ends
    # on TOURNAMENT STATS (league / wave / cause of death / rank) whose only
    # button is OK - there is no RETRY, because a re-entry costs a ticket.
    # Before this entry the screen came back 'unknown' and the orchestrator went
    # hands-off on it, which was safe but left the dialog sitting there.
    ("tournament_stats",  "tourney/tournament_stats.png", 0.90, (600,  800)),
    ("game_stats",       "icons/game_stats.png",        0.80, (620,  940)),
    ("exit_battle",      "home/exit_battle_dialog.png", 0.90, (940, 1260)),
    ("end_round",        "home/end_round_dialog.png",   0.90, (930, 1230)),
    # band widened 2026-09-05: on the "Try again" tournament layout the
    # dialog sits 5px higher (title top at y=895) and the 900-1050 band cut
    # it to 0.68 while the full-frame match was 1.0 - start_battle then never
    # saw the BUY TICKET dialog it had just opened.
    ("buy_ticket",       "tourney/buy_ticket_title.png",0.90, (860, 1060)),
    ("intro_sprint_end", "home/intro_sprint_dialog.png",0.90, (940, 1180)),
    ("tournament_heat",  "tourney/heat_tabs.png",       0.80, (640,  940)),
    ("ticket_reward",    "tourney/ticket_claim.png",    0.90, (1800, 2130)),
    ("reward_listing",   "buttons/reward_skip.png",     0.90, (250,  500)),
    # "A new tournament is open, take a free ticket..." - announcement popup
    # over HOME (first seen on acct2, 2026-08-19: it parked over the
    # Difficulty selector and read_tier aborted). Claiming the FREE ticket
    # only banks it - entering a tournament is a separate BATTLE flow, so
    # hard rule 2 is not in play. Not in RECOVERABLE: its button is CLAIM,
    # not the SKIP pill orchestrator's recovery looks for; flows that meet it
    # (scan.phase_battle) dismiss it themselves, template-verified.
    ("tourney_open",     "home/tourney_open_dialog.png", 0.90, (1100, 1400)),
    # "WELCOME BACK - Resume previous round?" after a game/emulator restart
    # with a run live (first seen 2026-08-28, wave 5686 survived the MuMu
    # restart). The user's ruling: ALWAYS resume - boot.py taps Resume
    # (home/welcome_back_resume.png); runners meeting it hold as usual and
    # at least name it in the log.
    ("welcome_back",     "home/welcome_back_dialog.png", 0.90, (542, 902)),
]

HEADERS = ["tournament", "guild", "cards", "modules", "battle"]

# The ONLY screens the stuck-popup recovery may touch. A reward flow that ends
# badly leaves the game parked on one of these; anything else off the battle
# screen is either a menu the human opened or a place we do not understand, and
# both mean hands off. This list is the difference between "dismiss our own
# leftover popup" and "click around in whatever happens to be on screen".
RECOVERABLE = {"reward_listing", "ticket_reward"}


@dataclass
class Screen:
    name: str
    score: float = 0.0
    wave: int | None = None
    evidence: dict = field(default_factory=dict)

    @property
    def in_battle(self) -> bool:
        return self.name == "battle"

    def __str__(self) -> str:
        w = f" wave={self.wave}" if self.wave is not None else ""
        return f"{self.name}({self.score:.2f}){w}"


def _match(frame, rel: str, region=None) -> tuple[float, tuple[int, int]]:
    """Score a template, optionally only inside a band. Missing templates
    score 0 rather than raising - an uncalibrated screen must degrade to
    'unknown' (hands off), never to a crash mid-run."""
    try:
        tpl = detect._tpl(rel)
    except detect.TemplateMissing:
        return 0.0, (0, 0)
    hay = frame
    ox = oy = 0
    if region:
        (y0, y1), (x0, x1) = region
        hay = frame[y0:y1, x0:x1]
        ox, oy = x0, y0
    if hay.shape[0] < tpl.shape[0] or hay.shape[1] < tpl.shape[1]:
        return 0.0, (0, 0)
    res = cv2.matchTemplate(hay, tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    return float(score), (ox + loc[0] + tpl.shape[1] // 2,
                          oy + loc[1] + tpl.shape[0] // 2)


def identify(frame) -> Screen:
    ev = {}
    for name, rel, thresh, (y0, y1) in OVERLAYS:
        s, _ = _match(frame, rel, ((y0, y1), (0, 1080)))
        ev[name] = round(s, 3)
        if s >= thresh:
            return Screen(name, s, evidence=ev)

    band = ((HEADER_BAND[0], HEADER_BAND[1]), (HEADER_X[0], HEADER_X[1]))
    best, best_s = None, 0.0
    for name in HEADERS:
        s, _ = _match(frame, f"screens/hdr_{name}.png", band)
        ev[f"hdr_{name}"] = round(s, 3)
        if s > best_s:
            best, best_s = name, s

    if best_s < HDR_THRESH:
        # no header at all is the normal look of a run in progress
        wave = wave_reader.read_wave(frame)
        ev["wave"] = wave
        if wave is not None:
            return Screen("battle", 1.0, wave=wave, evidence=ev)
        return Screen("unknown", best_s, evidence=ev)

    if best != "battle":
        return Screen(best, best_s, evidence=ev)

    # "BATTLE" is worn by both the live run and the pre-run home screen
    wave = wave_reader.read_wave(frame)
    ev["wave"] = wave
    if wave is not None:
        return Screen("battle", best_s, wave=wave, evidence=ev)
    home, _ = _match(frame, "home/dissonant_run.png", ((2100, 2300), (0, 1080)))
    ev["dissonant_run"] = round(home, 3)
    if home >= OVERLAY_THRESH:
        return Screen("home", home, evidence=ev)
    return Screen("unknown", best_s, evidence=ev)


def in_tournament(frame) -> bool:
    """Trophy badge in front of the Tier readout - only a tournament run."""
    s, _ = _match(frame, "tourney/in_tournament.png", ((1590, 1740), (450, 750)))
    return s >= OVERLAY_THRESH


def require(frame, *names: str) -> Screen:
    """Assert the screen before tapping. Callers that act should use this so a
    mis-navigation surfaces as a refusal instead of a tap somewhere unknown."""
    sc = identify(frame)
    if sc.name not in names:
        raise WrongScreen(f"expected {' or '.join(names)}, on {sc}")
    return sc


class WrongScreen(RuntimeError):
    pass


if __name__ == "__main__":
    # "which screen am I on?" - answerable at any moment, on any instance
    import argparse
    import settings
    ap = argparse.ArgumentParser(description="Name the screen currently shown.")
    ap.add_argument("--instance", default="main")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print every template score behind the decision")
    a = ap.parse_args()
    settings.select_instance(a.instance)
    from device import capture
    sc = identify(capture.grab())
    print(f"{settings.CONFIG['active_instance']}: {sc}")
    print(f"  in_battle={sc.in_battle}  recovery_allowed={sc.name in RECOVERABLE}")
    if a.verbose:
        for k, v in sc.evidence.items():
            print(f"    {k:20s} {v}")
