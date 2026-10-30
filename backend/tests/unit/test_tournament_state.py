"""`screen.tournament_state`: tournament evidence is yes / no / unknown (roadmap item 3, spec 3.7, R3-03).

`in_tournament` cannot say "no" with evidence, because a template that was never captured scores 0. `tournament_state` says "unknown" for exactly that case. Frames
and templates are synthetic noise; no game image is involved."""
import cv2
import numpy as np
import pytest

from player import accounts
from vision import detect, screen

REL = "tourney/in_tournament.png"
(Y0, Y1), (X0, X1) = screen.IN_TOURNAMENT_REGION


def noise(shape, seed):
    return np.random.default_rng(seed).integers(0, 256, shape, dtype=np.uint8)


@pytest.fixture
def templates(tmp_path, monkeypatch):
    """The folder `detect._tpl` reads templates from, empty, with a fresh template cache."""
    monkeypatch.setattr(accounts, "template_path", lambda root, cfg, rel, **kw: tmp_path / rel)
    monkeypatch.setattr(detect, "_TPL_CACHE", {})
    return tmp_path


def save_badge(folder, badge):
    path = folder / REL
    path.parent.mkdir(parents=True, exist_ok=True)
    assert cv2.imwrite(str(path), badge)


def frame_with(badge=None, at=(Y0 + 20, X0 + 30)):
    frame = noise((2560, 1080, 3), 1)
    if badge is not None:
        frame[at[0]:at[0] + badge.shape[0], at[1]:at[1] + badge.shape[1]] = badge
    return frame


BADGE = noise((48, 72, 3), 7)


def test_a_missing_template_is_unknown_and_in_tournament_keeps_reading_false(templates):
    frame = frame_with(BADGE)
    assert not (templates / REL).exists()
    assert screen.tournament_state(frame) == "unknown"
    assert screen.in_tournament(frame) is False           # unchanged: this is the blind spot the third state exists for


def test_an_unreadable_template_is_unknown(templates):
    (templates / "tourney").mkdir()
    (templates / REL).write_text("not a PNG", encoding="utf-8")
    assert screen.tournament_state(frame_with(BADGE)) == "unknown"
    assert screen.in_tournament(frame_with(BADGE)) is False


def test_an_empty_template_file_is_unknown(templates):
    (templates / "tourney").mkdir()
    (templates / REL).write_bytes(b"")
    assert screen.tournament_state(frame_with()) == "unknown"


def test_a_template_that_scores_below_the_threshold_is_no(templates):
    save_badge(templates, BADGE)
    frame = frame_with()                                    # the badge is not on this frame
    assert screen.tournament_state(frame) == "no"
    assert screen.in_tournament(frame) is False


def test_a_template_that_scores_above_the_threshold_is_yes(templates):
    save_badge(templates, BADGE)
    frame = frame_with(BADGE)
    assert screen.tournament_state(frame) == "yes"
    assert screen.in_tournament(frame) is True


def test_the_badge_outside_the_search_band_is_no(templates):
    """The band is the one `in_tournament` has always searched: a badge elsewhere on the frame is not the tournament badge."""
    save_badge(templates, BADGE)
    assert screen.tournament_state(frame_with(BADGE, at=(Y1 + 100, X0 + 30))) == "no"
    assert screen.tournament_state(frame_with(BADGE, at=(Y0 + 20, X1 + 50))) == "no"


def test_the_template_appears_after_the_first_unknown_reading(templates):
    frame = frame_with(BADGE)
    assert screen.tournament_state(frame) == "unknown"
    save_badge(templates, BADGE)                            # the person captures it through Recognition
    assert screen.tournament_state(frame) == "yes"


@pytest.mark.parametrize("score, expected", [(0.90, "yes"), (0.95, "yes"), (0.8999, "no"), (0.0, "no")])
def test_the_state_shares_the_threshold_and_the_region_of_in_tournament(templates, monkeypatch, score, expected):
    save_badge(templates, BADGE)
    asked = []

    def match(frame, rel, region=None):
        asked.append((rel, region))
        return score, (0, 0)

    monkeypatch.setattr(screen, "_match", match)
    assert screen.tournament_state(None) == expected
    assert screen.in_tournament(None) is (expected == "yes")
    assert asked == [(screen.IN_TOURNAMENT_TEMPLATE, screen.IN_TOURNAMENT_REGION)] * 2
    assert screen.IN_TOURNAMENT_TEMPLATE == REL and screen.OVERLAY_THRESH == 0.90


def test_in_tournament_is_what_it_always_was(templates, monkeypatch):
    """The legacy reading: the same template, the same band, a score at or above OVERLAY_THRESH; nothing else."""
    seen = []
    monkeypatch.setattr(screen, "_match", lambda frame, rel, region=None: seen.append((rel, region)) or (screen.OVERLAY_THRESH, (0, 0)))
    assert screen.in_tournament(None) is True
    assert seen == [("tourney/in_tournament.png", ((1590, 1740), (450, 750)))]
    monkeypatch.setattr(screen, "_match", lambda frame, rel, region=None: (screen.OVERLAY_THRESH - 1e-6, (0, 0)))
    assert screen.in_tournament(None) is False
