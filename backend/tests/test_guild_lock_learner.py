"""The guild flow must never strand a run on the Guild screen (2026-09-14:
guild_claimables raised TemplateMissing for the padlock image right after the
Members tab opened, the generator died, stuck recovery refuses the Guild screen
by design, and the run sat there from 01:35 to 09:13 while the lit badge
reopened it every five minutes). Three guards: the padlock is LEARNED from the
locked boxes on the track (missing only, repeated-glyph verified), a missing
padlock skips the claim instead of raising, and a flow that raises anyway is
dropped with the return strip taken."""
import numpy as np
import pytest

from interactions import missions
from vision import detect

BG = (40, 20, 60)                     # dark purple guild panel (BGR)
GLYPH = (32, 43)                      # measured padlock glyph, native px
GLYPH_Y = 706                         # measured glyph center on the track
# measured glyph centers per slot (the 100 box sits 40 px right of its slot x)
GLYPH_X = dict(zip(missions.GUILD_SLOTS, (181, 375, 695, 1015)))


def _padlock(frame, cx, cy=GLYPH_Y, keyhole=True):
    w, h = GLYPH
    x0, y0 = cx - w // 2, cy - h // 2
    frame[y0:y0 + h, x0:x0 + w] = 255
    if keyhole:
        frame[y0 + 18:y0 + 34, x0 + 12:x0 + 20] = BG


def _track(slots=missions.GUILD_SLOTS):
    frame = np.zeros((2560, 1080, 3), np.uint8)
    frame[:] = BG
    for x in slots:
        _padlock(frame, GLYPH_X[x])
    return frame


@pytest.fixture
def learner(tmp_path, monkeypatch):
    import settings
    from player import battle_capture as bc
    from runtime import logger
    events, written = [], {}
    monkeypatch.setattr(logger, "event", lambda kind, **kw: events.append((kind, kw)))
    monkeypatch.setattr(settings, "template_path",
                        lambda rel, **k: tmp_path / rel)
    monkeypatch.setattr(bc, "write_template",
                        lambda rel, crop, **k: written.__setitem__(rel, crop) or "written")
    monkeypatch.delitem(detect._TPL_CACHE, missions.LOCK_TEMPLATE, raising=False)
    return events, written, tmp_path / missions.LOCK_TEMPLATE


# ------------------------------------------------------------- learner
def test_learner_cuts_the_padlock_from_a_locked_track_missing_only(learner):
    events, written, target = learner
    frame = _track()
    assert missions.learn_lock_template(frame) is True
    assert written[missions.LOCK_TEMPLATE].shape == (55, 60, 3)
    kinds = [k for k, _ in events]
    assert kinds == ["chest_lock_captured"]
    assert events[0][1]["slots"] == 4
    # every value in the event must be JSON-native (the logger has no default=)
    import json
    json.dumps(events[0][1])
    # on disk now: one stat, no write, no event
    target.parent.mkdir(parents=True)
    target.write_bytes(b"png")
    assert missions.learn_lock_template(frame) is False
    assert len(written) == 1 and len(events) == 1


def test_learner_refuses_a_single_padlock(learner):
    events, written, _ = learner
    frame = _track(slots=[missions.GUILD_SLOTS[0]])
    assert missions.learn_lock_template(frame) is False
    assert written == {}
    assert events == [("guild_lock_unlearned",
                       {"reason": "fewer than two padlocks on the track", "glyphs": 1})]


def test_learner_refuses_a_cut_that_does_not_repeat(learner):
    events, written, _ = learner
    frame = np.zeros((2560, 1080, 3), np.uint8)
    frame[:] = BG
    # two white blobs of padlock size that are NOT the same glyph
    _padlock(frame, GLYPH_X[missions.GUILD_SLOTS[0]])
    x1 = GLYPH_X[missions.GUILD_SLOTS[1]]
    w, h = GLYPH
    frame[GLYPH_Y - h // 2:GLYPH_Y + h // 2, x1 - w // 2:x1 + w // 2] = 255
    frame[GLYPH_Y - h // 2 + 4:GLYPH_Y + h // 2 - 4, x1 - w // 2 + 4:x1 + w // 2 - 4] = BG
    assert missions.learn_lock_template(frame) is False
    assert written == {}
    assert events[0][0] == "guild_lock_unlearned"
    assert events[0][1]["reason"] == "padlock cut does not repeat"


def test_learner_ignores_a_blank_frame(learner):
    events, written, _ = learner
    assert missions.learn_lock_template(np.zeros((2560, 1080, 3), np.uint8)) is False
    assert written == {}


# ------------------------------------------------------ claimables guard
def test_guild_claimables_skips_without_the_padlock_image(monkeypatch):
    from runtime import logger
    events = []
    monkeypatch.setattr(logger, "event", lambda kind, **kw: events.append((kind, kw)))

    def missing(rel):
        raise detect.TemplateMissing(rel)
    monkeypatch.setattr(detect, "_tpl", missing)
    assert missions.guild_claimables(_track()) == []
    assert events == [("guild_claims_skipped", {"reason": "missing chest lock recognition"})]


def test_guild_claimables_uses_the_learned_padlock(learner, monkeypatch):
    events, written, _ = learner
    frame = _track()
    assert missions.learn_lock_template(frame) is True
    monkeypatch.setitem(detect._TPL_CACHE, missions.LOCK_TEMPLATE, written[missions.LOCK_TEMPLATE])
    assert missions.guild_claimables(frame) == []           # all four locked
    # unlock the second box: no padlock, magenta glow -> claimable
    x = missions.GUILD_SLOTS[1]
    frame[missions.GUILD_CELL[0]:missions.GUILD_CELL[1], x - 70:x + 70] = (200, 40, 220)
    assert missions.guild_claimables(frame) == [(x, 708)]


# ------------------------------------------------------- crash safety
def test_mission_step_drops_a_raising_flow_and_takes_the_exit(monkeypatch):
    from runtime import logger
    events, bails = [], []
    monkeypatch.setattr(logger, "event", lambda kind, **kw: events.append((kind, kw)))
    monkeypatch.setattr(logger, "shot", lambda frame, tag: tag + ".png")
    monkeypatch.setattr(missions, "bail", lambda frame, reason: bails.append(reason))

    def broken_flow():
        yield
        raise RuntimeError("template gone")
    m = missions.Mission()
    m.start(broken_flow)
    frame = np.zeros((2560, 1080, 3), np.uint8)
    m.step(frame)                                # must not raise
    assert m.active is False
    assert bails == ["mission_crash"]
    assert events[0][0] == "mission_crash"
    assert events[0][1]["flow"] == "broken_flow"
    assert "template gone" in events[0][1]["trace"]
    assert events[0][1]["shot"] == "mission_crash.png"
    m.step(frame)                                # dropped: a no-op now


def test_guild_flow_returns_to_the_game_when_the_padlock_is_unknown(learner, monkeypatch):
    """The 2026-09-14 regression end to end: padlock image missing, single
    locked box on the track (so it cannot be learned either) - the flow must
    still walk to its return taps and finish."""
    events, written, _ = learner
    from runtime import logger
    taps = []
    monkeypatch.setattr(logger, "shot", lambda frame, tag: tag + ".png")
    monkeypatch.setattr(missions, "find_tile", lambda frame, rel, fallback=None: (911, 589))
    monkeypatch.setattr(missions, "_tap", lambda x, y, reason, instant=True: taps.append(reason))
    monkeypatch.setattr(detect, "_match",
                        lambda frame, rel, thr: ("return_to_game" not in taps, 1.0, (0, 0)))

    def missing(rel):
        raise detect.TemplateMissing(rel)
    monkeypatch.setattr(detect, "_tpl", missing)
    frame = _track(slots=[missions.GUILD_SLOTS[0]])
    gen = missions.guild_flow()
    next(gen)
    for _ in range(20):
        try:
            gen.send(frame)
        except StopIteration:
            break
    else:
        pytest.fail("guild flow never finished")
    assert taps == ["guild_open", "guild_members_tab", "return_to_game"]
    kinds = [k for k, _ in events]
    # claimables are read before and after the (empty) claim pass
    assert kinds == ["guild_lock_unlearned", "guild_claims_skipped",
                     "guild_claims_skipped", "guild_done"]
    assert written == {}
