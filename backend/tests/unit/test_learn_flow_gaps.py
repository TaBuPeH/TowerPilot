"""Two gaps left by the 2026-10-12 Learn-screens run (22 screens, 21 verified).

LEARN-1: the Guardian screen map stayed at 4/5 because guardian/chip_scout.png
was 'not_seen'. The run's own Guardian frame shows why: this account has not
unlocked Scout - its slot in the chip inventory is a padlock (the installed
'lock' sprite matched there by SIFT; nothing matched in an owned chip's slot).
A chip the game draws as a padlock is not a mapping miss: it is 'locked',
listed as an optional skip and 'unavailable' on the screen map. A chip that is
simply not found (no padlock where it belongs) stays 'needs_mapping'.

LEARN-2: Shard readiness demanded buttons/end_round.png AND
home/end_round_yes.png as BLOCKING. The run's two surrenders (its top tier, 16,
and Tier 1) cut buttons/exit_battle.png - flow_capture cuts that only when the
side menu reads EXIT BATTLE - then the Surrender dialog, and nothing more: no
END ROUND cut and no event saying why (the post-Surrender poll logged nothing
and saved no frame). shard.abandon_run can only tap an END ROUND / Yes it has a
template for, so the two images stay required - END ROUND as an ADVISORY row
that names what happens without it, the Yes BLOCKING because a confirm left up
gets the side-menu toggle tapped onto it - and the surrender now records why it
cut neither.
"""
import time

import cv2
import numpy as np
import pytest

from player import accounts, bootstrap, calibrate, readiness
from player import flow_capture as fc
from player.bootstrap_layout import manifest

SCOUT = "guardian/chip_scout.png"
# measured on the run's Guardian frame (logs/main/20261012_145207_...png):
# every owned chip sat 120 px above its reference_rect, and the installed
# 'lock' sprite matched at this rect - Scout's slot
LIVE_DY = -120
LOCK_IN_SCOUT_SLOT = [821, 1520, 98, 151]


def _chips():
    return [rel for rel, d in manifest()["asset_bindings"].items() if d["screen"] == "guardians"]


def _guardian_scanner(tmp_path, monkeypatch, lock_rect, *, lock_on_follow=True, owned_hits=True, owned_dy=None):
    """`owned_dy`: {chip rel: dy} limits the verified chips to those, each
    sitting dy px from its reference_rect (default: every owned chip at LIVE_DY)."""
    from player import asset_verify
    cal = calibrate.Calibration({"state": str(tmp_path / "state.json")}, False, bootstrap=True)
    sc = bootstrap.Scanner(cal, {}, grab=lambda: None, tap=lambda *a, **k: None, pause=lambda _: None)
    sc.asset_folder = tmp_path
    sc.asset_summary = {"version": "29.0.6"}
    sc.asset_index = {"images": [{"name": "lock", "type": "Sprite", "sha256": "lk", "id": "l",
                                  "file": "images/lock.png"}]}
    sc.asset_mappings = {rel: {"status": "mapped", "candidates": [], "screen": "guardians", "verification": "pending"}
                         for rel in _chips()}
    hits = {}
    for rel in _chips():
        x, y, w, h = manifest()["asset_bindings"][rel]["reference_rect"]
        if rel != SCOUT and owned_hits and (owned_dy is None or rel in owned_dy):
            hits[rel] = {"rect": [x, y + (LIVE_DY if owned_dy is None else owned_dy[rel]), w, h], "inliers": 30, "asset_id": rel,
                         "asset_name": rel, "asset_sha256": rel}
    sc.asset_hit = lambda rel, frame: hits.get(rel)
    sc.publish_assets = lambda: None
    sc.progress = lambda *a, **k: None
    sc.check_stop = lambda: None
    monkeypatch.setattr(sc.cal, "cut", lambda phase, rel, crop, fr, name, extra=None, *, unique=True:
                        sc.cal.entries.append({"rel": rel, "verified": True, "t": time.time()}) or {"verified": True})
    frame = np.random.default_rng(7).integers(0, 255, (2560, 1080, 3), np.uint8)
    follow = frame.copy()
    looked = []

    def best_match(folder, mapping, fr, definition):
        # the lock sprite is found only inside the region the caller searched
        looked.append(list(definition["search"]))
        if lock_rect is None or (fr is follow and not lock_on_follow):
            return None
        sx, sy, sw, sh = definition["search"]
        x, y, w, h = lock_rect
        if sx <= x and sy <= y and x + w <= sx + sw and y + h <= sy + sh:
            return {"rect": list(lock_rect), "inliers": 32, "asset_id": "l", "asset_name": "lock", "asset_sha256": "lk"}
        return None
    monkeypatch.setattr(asset_verify, "best_match", best_match)
    return sc, frame, follow, looked


def test_a_chip_drawn_as_a_padlock_in_its_slot_is_locked_not_a_mapping_gap(tmp_path, monkeypatch):
    sc, frame, follow, looked = _guardian_scanner(tmp_path, monkeypatch, LOCK_IN_SCOUT_SLOT)
    out = sc.verify_asset_rels(_chips(), frame, follow)
    assert out[SCOUT] == "locked"
    assert all(out[rel] == "verified" for rel in _chips() if rel != SCOUT)
    assert sc.asset_mappings[SCOUT]["verification"] == "locked"
    # the lock was searched for in Scout's own slot: its reference rect moved
    # by the offset the verified chips show, padded like best_match's nearby
    x, y, w, h = manifest()["asset_bindings"][SCOUT]["reference_rect"]
    assert looked and all(s == [x - 48, y + LIVE_DY - 48, w + 96, h + 96] for s in looked)
    skip = [s for s in sc.skipped if s["target"] == SCOUT]
    assert len(skip) == 1 and skip[0]["optional"] is True and "padlock" in skip[0]["reason"]
    sc.update_screen_map("guardians")
    row = sc.state["screen_map"]["guardians"]
    owned = len(_chips()) - 1
    assert row["status"] == "verified" and (row["verified"], row["total"]) == (owned, owned)
    assert {b["target"]: b["status"] for b in row["blocks"]}[SCOUT] == "unavailable"


def test_a_chip_without_a_padlock_where_it_belongs_stays_a_mapping_gap(tmp_path, monkeypatch):
    # no lock at all, a lock only on one frame, a lock in some other slot: a real miss
    for lock, on_follow in ((None, True), (LOCK_IN_SCOUT_SLOT, False), ([150, 1220, 98, 151], True)):
        sc, frame, follow, _ = _guardian_scanner(tmp_path, monkeypatch, lock, lock_on_follow=on_follow)
        out = sc.verify_asset_rels(_chips(), frame, follow)
        assert out[SCOUT] == "not_seen", lock
        assert not [s for s in sc.skipped if s["target"] == SCOUT]
        sc.update_screen_map("guardians")
        row = sc.state["screen_map"]["guardians"]
        assert row["status"] == "needs_mapping" and (row["verified"], row["total"]) == (len(_chips()) - 1, len(_chips()))


def test_without_a_verified_sibling_the_slot_is_unknown_and_nothing_is_called_locked(tmp_path, monkeypatch):
    sc, frame, follow, looked = _guardian_scanner(tmp_path, monkeypatch, LOCK_IN_SCOUT_SLOT, owned_hits=False)
    out = sc.verify_asset_rels(_chips(), frame, follow)
    assert set(out.values()) == {"not_seen"} and looked == []


ATTACK, ALLY = "guardian/chip_attack.png", "guardian/chip_ally.png"


def test_a_padlock_matching_no_sibling_offset_is_not_called_locked(tmp_path, monkeypatch):
    # two verified chips that sit 0 and 120 px off their reference rects prove no single
    # layout shift. Their median (60 px) is an offset no chip shows, and a padlock found
    # there (here: where neither chip's own offset puts Scout's slot) is not Scout's
    off_both = [821, 1590, 98, 151]
    sc, frame, follow, looked = _guardian_scanner(tmp_path, monkeypatch, off_both, owned_dy={ATTACK: -120, ALLY: 0})
    out = sc.verify_asset_rels(_chips(), frame, follow)
    assert out[SCOUT] == "not_seen" and looked == []
    assert not [s for s in sc.skipped if s["target"] == SCOUT]


def test_one_outlier_among_three_or_more_siblings_still_withholds_locked(tmp_path, monkeypatch):
    # the median offset (the agreeing majority's) would place the slot at the padlock;
    # the rule refuses anyway: the siblings show no single layout shift (fails safe)
    dys = {rel: LIVE_DY for rel in _chips() if rel != SCOUT}
    dys[ALLY] = 0
    assert len(dys) >= 3
    sc, frame, follow, looked = _guardian_scanner(tmp_path, monkeypatch, LOCK_IN_SCOUT_SLOT, owned_dy=dys)
    assert sc.verify_asset_rels(_chips(), frame, follow)[SCOUT] == "not_seen" and looked == []


@pytest.mark.parametrize("dys", [{ATTACK: -120, ALLY: -120}, {ATTACK: -120}], ids=["two agreeing", "one"])
def test_one_or_two_siblings_that_agree_still_place_the_padlock_slot(tmp_path, monkeypatch, dys):
    sc, frame, follow, _ = _guardian_scanner(tmp_path, monkeypatch, LOCK_IN_SCOUT_SLOT, owned_dy=dys)
    assert sc.verify_asset_rels(_chips(), frame, follow)[SCOUT] == "locked"


# ------------------------------------------------------------------ LEARN-2
def _config():
    return {"active_instance": "main", "instances": {"main": {"account": "alice"}},
            "loadouts": {}, "active_profile": "default", "accounts": {"alice": {"active_profile": "alice"}}}


END_ROUND_PAIR = ("buttons/end_round.png", "home/end_round_yes.png")
SURRENDERING_RUNS = ({"kind": "shard"}, {"kind": "coin", "max_wave": 3000})


@pytest.mark.parametrize("body", SURRENDERING_RUNS)
def test_the_end_round_images_are_their_own_rows_and_only_the_yes_blocks(body):
    # shard.abandon_run finds END ROUND / Yes only by template, so neither may
    # hide inside an alternative row that EXIT BATTLE (a CORE image) satisfies.
    # The Yes blocks: a confirm abandon_run cannot answer gets the side-menu
    # toggle tapped onto it by the next abandon_run (test below).
    rows = {tuple(r["alternatives"]): r for r in readiness.requirements({}, body)}
    for rel in END_ROUND_PAIR:
        assert [k for k in rows if rel in k] == [(rel,)]
        assert any("END ROUND" in reason for reason in rows[(rel,)]["reasons"])
    assert rows[("buttons/end_round.png",)]["blocking"] is False
    assert rows[("home/end_round_yes.png",)]["blocking"] is True
    assert rows[("home/surrender.png",)]["blocking"] is True


@pytest.mark.parametrize("body", SURRENDERING_RUNS)
def test_without_the_end_round_images_only_the_yes_holds_the_run(tmp_path, body):
    cfg = _config()
    for row in readiness.requirements(cfg, body):
        for rel in set(row["alternatives"]) - set(END_ROUND_PAIR):
            path = accounts.template_path(tmp_path, cfg, rel, write=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            assert cv2.imwrite(str(path), np.random.default_rng(4).integers(0, 255, (30, 50, 3), np.uint8))
    result = readiness.check(tmp_path, cfg, body)
    assert not result["ready"]
    assert [r["alternatives"] for r in result["missing"]] == [["home/end_round_yes.png"]]
    assert ["buttons/end_round.png"] in [r["alternatives"] for r in result["advisory"]]


def test_a_coin_run_whose_rules_surrender_needs_the_surrender_images_too():
    # a `surrender_retry` rule reaches shard.abandon_run (orchestrator._rule_surrender)
    # exactly like max_wave does, with no max_wave and not a surrendering kind
    # readiness reads COMPILED presets (CONFIG["presets"]): the action there is
    # {"kind": "surrender_retry"}, not the authoring key, so compile a real one
    from player import playerprofile
    from goldens import load_golden
    prof = load_golden()
    plain = {tuple(r["alternatives"]) for r in readiness.requirements({}, playerprofile.compile_preset(prof, "coin_default"))}
    assert ("home/surrender.png",) not in plain
    prof["policies"]["rescue_policies"]["high_tier_wall"]["rules"].append(
        {"when": {"wave_at_least": 100}, "do": {"surrender_retry": True}})
    body = playerprofile.compile_preset(prof, "coin_default")
    assert {"kind": "surrender_retry"} in [r["do"] for r in body["rules"]] and not body.get("max_wave")
    rows = {tuple(r["alternatives"]): r for r in readiness.requirements({}, body)}
    assert rows[("home/surrender.png",)]["blocking"] is True
    assert rows[("home/end_round_yes.png",)]["blocking"] is True
    assert rows[("buttons/end_round.png",)]["blocking"] is False


# The surrender subroutine on synthetic frames: what it can cut is exactly what
# the game shows. Screens by name; OCR answers for the screen last grabbed.
_TEXT = {
    "home": ["BATTLE", "Tier 1"],
    "run": ["Wave 1"],
    "menu_exit": ["EXIT BATTLE"],
    "menu_end": ["END ROUND"],
    "surrender": ["EXIT BATTLE", "What would you like to do?", "Surrender", "Go Home"],
    "end_round_q": ["END ROUND", "Are you sure you want to end the round?"],
    "dying": [],
    "stats": ["GAME STATS"],
}


def _screen():
    # dark texture: _row_buttons thresholds grey at 90, so only the buttons are bright
    f = np.random.default_rng(5).integers(0, 60, (2560, 1080, 3), np.uint8)
    # two bright side-by-side buttons in the confirm-dialog row
    cv2.rectangle(f, (200, 1410), (500, 1480), (255, 255, 255), -1)
    cv2.rectangle(f, (580, 1410), (880, 1480), (255, 255, 255), -1)
    return f


class _Cal:
    def __init__(self):
        self.cuts = []

    def cut(self, phase, rel, crop, frame, name, extra=None, **k):
        self.cuts.append(rel)
        return {"rel": rel, "verified": True}


class _Scanner:
    def __init__(self, screens):
        self.cal, self.skipped, self.taps, self.shots = _Cal(), [], [], []
        self.screens, self.now = list(screens), None
        self.frame = _screen()
        self.clock = 0.0                 # flow.poll's deadlines run on this
        self.state = {}

    def grab(self):
        self.now = self.screens.pop(0) if len(self.screens) > 1 else self.screens[0]
        return self.frame

    def tap(self, x, y, reason="", **k):
        self.taps.append(reason)

    def pause(self, seconds):
        self.clock += seconds

    def check_stop(self):
        pass

    def progress(self, message, **extra):
        pass


@pytest.fixture(autouse=True)
def _flow_calibration_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(_Cal, 'p', {'state': str(tmp_path / 'flow-state.json')}, raising=False)


def _surrender(monkeypatch, screens, have=(), sc=None):
    """`have`: templates already on disk (fc._have_templates, which reads the
    machine's own calibration folder, is stubbed so no test depends on it).
    `sc`: a scanner to run on (its skipped list carries over, as in a scan that
    surrenders twice); `screens` then replaces its remaining screens."""
    from types import SimpleNamespace
    from runtime import logger
    from vision import textocr
    sc = sc or _Scanner(screens)
    sc.screens = list(screens)
    # every line sits inside the exit-button and dialog regions (upscaled x2)
    monkeypatch.setattr(textocr, "read_lines", lambda bgr, scale=2.0: [(600, 680, t) for t in _TEXT[sc.now]])
    monkeypatch.setattr(logger, "event", lambda *a, **k: None)
    monkeypatch.setattr(logger, "shot", lambda frame, tag: sc.shots.append(tag) or f"{tag}.png")
    monkeypatch.setattr(fc, "time", SimpleNamespace(monotonic=lambda: sc.clock, time=lambda: sc.clock))
    def reached_home(flow):
        sc.screens = ['home']
        return fc._complete_setup_battle(flow)
    monkeypatch.setattr(fc, "capture_game_stats", reached_home)
    monkeypatch.setattr(fc, "_have_templates", lambda rels: {r for r in rels if r in have})
    # Setup battles start only under an accepted operation (its identity seals
    # the outcome in the ledger); this is that identity, as in the lifecycle tests.
    from runtime import operations
    monkeypatch.setattr(operations, "environment_operation", lambda: (None, "surrender-test-op"))
    flow = fc._Flow(sc)
    flow.in_tournament = lambda f: False
    flow.on_home = lambda f: sc.now == 'home'
    flow.wave = lambda f: None if sc.now in ('home', 'stats') else 1
    flow.side_menu = lambda f: sc.now in ('menu_exit', 'menu_end')
    # Exercise real ownership establishment; the end-screen controls below are
    # still the unit under test, while the fake stats handler supplies Home proof.
    sc.state.pop('setup_battle', None)
    sc.screens = ['home', 'run']
    fc._start_setup_run(flow, 'test setup battle')
    sc.taps.clear()
    sc.screens = list(screens)
    return sc, fc.end_run_and_capture(flow)


def _end_round_skips(sc):
    return {s["target"]: s for s in sc.skipped if "end_round" in s["target"]}


def _buttons_found_only_on(monkeypatch, sc, found_on):
    """fc._row_buttons finds the button pair only while `sc` shows one of the
    named screens (keyed on the screen, not on how many times it is called)."""
    real = fc._row_buttons
    monkeypatch.setattr(fc, "_row_buttons", lambda frame, *a, **k:
                        real(frame, *a, **k) if sc.now in found_on else (None, None))


def test_a_surrender_without_end_round_says_why_it_cut_neither(monkeypatch):
    # EXIT BATTLE, the Surrender dialog, then the run's end screen: what the
    # 2026-10-12 cuts are consistent with
    sc, home = _surrender(monkeypatch, ["menu_exit", "surrender", "dying", "dying", "stats"])
    assert home is True
    assert sc.cal.cuts == ["buttons/exit_battle.png", "home/exit_battle_dialog.png", "home/surrender.png"]
    assert sc.taps == ["flow: tap EXIT BATTLE", "flow: confirm exit (surrender)"]
    skips = _end_round_skips(sc)
    assert set(skips) == {"buttons/end_round.png", "home/end_round_dialog.png", "home/end_round_yes.png"}
    # the Yes blocks every surrendering run (readiness), so its absence is the
    # scan's to report; the other two only ever advise
    assert {t: s.get("optional", False) for t, s in skips.items()} == {
        "buttons/end_round.png": True, "home/end_round_dialog.png": True, "home/end_round_yes.png": False}
    assert "EXIT BATTLE" in skips["buttons/end_round.png"]["reason"]
    assert "GAME STATS" in skips["home/end_round_yes.png"]["reason"]
    assert sc.shots == []


def test_a_setup_that_saw_no_end_round_confirm_tells_the_person_how_to_unblock_the_run(tmp_path, monkeypatch):
    # Learn finishes with the Yes as its one non-optional skip (needs_attention),
    # and the greyed-out Shard row says what to do (a setup cuts the Yes only
    # from a confirm its own surrender brings up) and so does the run's message
    sc, home = _surrender(monkeypatch, ["menu_exit", "surrender", "dying", "dying", "stats"])
    assert [s["target"] for s in sc.skipped if not s.get("optional")] == ["home/end_round_yes.png"]
    cfg, body = _config(), {"kind": "shard"}
    for row in readiness.requirements(cfg, body):
        for rel in set(row["alternatives"]) - set(END_ROUND_PAIR):
            path = accounts.template_path(tmp_path, cfg, rel, write=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            assert cv2.imwrite(str(path), np.random.default_rng(4).integers(0, 255, (30, 50, 3), np.uint8))
    result = readiness.check(tmp_path, cfg, body)
    missing = result["missing"]
    assert [r["alternatives"] for r in missing] == [["home/end_round_yes.png"]]
    how = missing[0]["docs"][0]["how"]
    # both routes to the Yes are named, and neither promises what setup cannot know
    assert "EXIT BATTLE Surrender" in how and "crop Yes" in how and "will not" not in how
    # the run card gets the same text, not "click Prepare / Scan now"
    assert result["message"] == how and result["next_action"] == "crop_image"


def test_a_surrender_poll_that_times_out_keeps_the_frame(monkeypatch):
    # nothing recognisable after Surrender: an END ROUND confirm OCR missed and
    # a slow end screen look the same here, so the frame is saved to tell them apart
    sc, home = _surrender(monkeypatch, ["menu_exit", "surrender", "dying"])
    assert sc.shots[0] == "flow_no_end_round_confirm"
    skips = _end_round_skips(sc)
    assert "flow_no_end_round_confirm.png" in skips["home/end_round_yes.png"]["reason"]
    assert not skips["home/end_round_yes.png"].get("optional")


NO_CONFIRM = {"end screen": ["menu_exit", "surrender", "dying", "dying", "stats"],
              "timeout": ["menu_exit", "surrender", "dying"]}


@pytest.mark.parametrize("screens", NO_CONFIRM.values(), ids=NO_CONFIRM)
def test_a_yes_already_on_disk_is_not_reported_as_a_gap(monkeypatch, screens):
    # a person's crop, or an earlier setup on another tier: the Yes exists, the
    # run is not blocked on it, so the scan must not end needs_attention on it
    sc, home = _surrender(monkeypatch, screens, have={"home/end_round_yes.png"})
    assert "home/end_round_yes.png" not in _end_round_skips(sc)
    assert [s["target"] for s in sc.skipped if not s.get("optional")] == []


@pytest.mark.parametrize("screens", NO_CONFIRM.values(), ids=NO_CONFIRM)
def test_the_yes_skip_states_what_blocks_and_does_not_forecast_another_setup(monkeypatch, screens):
    # an END ROUND confirm OCR missed and a slow end screen look the same to the
    # poll, so a later setup may capture the Yes: the reason must not say it cannot
    sc, home = _surrender(monkeypatch, screens)
    reason = _end_round_skips(sc)["home/end_round_yes.png"]["reason"]
    assert "stay blocked until it exists" in reason and "will not capture" not in reason


def test_a_confirm_seen_without_a_locatable_yes_is_reported(monkeypatch):
    # the END ROUND confirm after Surrender is recognised, but _row_buttons finds
    # no button pair and no YES text is read: nothing is cut or tapped, and the
    # Yes readiness blocks on has to be reported by the scan
    sc = _Scanner(["menu_exit", "surrender", "end_round_q", "stats"])
    _buttons_found_only_on(monkeypatch, sc, {"surrender"})
    sc, home = _surrender(monkeypatch, sc.screens, sc=sc)
    assert "home/end_round_yes.png" not in sc.cal.cuts
    assert "flow: confirm END ROUND (yes)" not in sc.taps
    assert [s["target"] for s in sc.skipped if not s.get("optional")] == ["home/end_round_yes.png"]
    assert "Yes" in _end_round_skips(sc)["home/end_round_yes.png"]["reason"]


def test_a_direct_end_round_confirm_without_a_locatable_yes_is_reported(monkeypatch):
    # END ROUND opened the confirm itself: a missing button pair already ends the
    # scan needs_attention
    monkeypatch.setattr(fc, "_row_buttons", lambda frame, *a, **k: (None, None))
    sc, home = _surrender(monkeypatch, ["menu_end", "end_round_q", "stats"])
    assert home is False and "home/end_round_yes.png" not in sc.cal.cuts
    # ...under the image that is missing, not under the exit dialog (L2)
    skipped = [s for s in sc.skipped if not s.get("optional")]
    assert [s["target"] for s in skipped] == ["home/end_round_yes.png"]
    assert skipped[0]["reason"].startswith("end_round confirm button not locatable")


def test_a_surrender_confirm_without_a_locatable_button_is_reported_under_the_surrender_image(monkeypatch):
    # no button pair and no "Surrender" text on the dialog: nothing is tapped, and
    # the missing control is home/surrender.png (L2), not the exit dialog image
    monkeypatch.setitem(_TEXT, "surrender_unreadable", ["EXIT BATTLE", "What would you like to do?"])
    monkeypatch.setattr(fc, "_row_buttons", lambda frame, *a, **k: (None, None))
    sc, home = _surrender(monkeypatch, ["menu_exit", "surrender_unreadable", "stats"])
    assert home is False and sc.taps == ["flow: tap EXIT BATTLE"]
    assert [(s["target"], s["reason"]) for s in sc.skipped if not s.get("optional")] == [
        ("home/surrender.png", "surrender confirm button not locatable")]


@pytest.mark.parametrize("screens,image", [
    (["menu_end", "end_round_q", "stats"], "home/end_round_yes.png"),
    (["menu_exit", "surrender_unreadable", "stats"], "home/surrender.png"),
], ids=["END ROUND Yes", "Surrender"])
def test_a_confirm_that_cannot_be_answered_does_not_list_an_image_that_is_on_disk(monkeypatch, screens, image):
    # the dialog could not be answered, but nothing is missing: the image exists
    monkeypatch.setitem(_TEXT, "surrender_unreadable", ["EXIT BATTLE", "What would you like to do?"])
    monkeypatch.setattr(fc, "_row_buttons", lambda frame, *a, **k: (None, None))
    sc, home = _surrender(monkeypatch, screens, have={image})
    assert home is False and len(sc.taps) == 1             # only the exit tap: the confirm was not answered
    assert [s for s in sc.skipped if not s.get("optional")] == []


def test_a_direct_end_round_confirm_after_a_reported_yes_does_not_list_it_twice(monkeypatch):
    # pass 1 (Surrender, then the end screen) reports the Yes; pass 2 of the same
    # scan opens END ROUND itself and finds no Yes either
    sc, home = _surrender(monkeypatch, ["menu_exit", "surrender", "dying", "dying", "stats"])
    assert home is True
    monkeypatch.setattr(fc, "_row_buttons", lambda frame, *a, **k: (None, None))
    sc, home = _surrender(monkeypatch, ["menu_end", "end_round_q", "stats"], sc=sc)
    assert home is False
    assert [s["target"] for s in sc.skipped].count("home/end_round_yes.png") == 1


SURRENDER_SCREENS = ["menu_exit", "surrender", "dying", "dying", "stats"]


def test_the_advisory_end_round_rows_are_listed_once_when_a_setup_surrenders_twice(monkeypatch):
    # the top tier, then the Tier-1 digit pass: each EXIT BATTLE surrender finds the
    # same three images missing; the scan names each of them a single time
    sc, home = _surrender(monkeypatch, SURRENDER_SCREENS)
    sc, home = _surrender(monkeypatch, SURRENDER_SCREENS, sc=sc)
    assert home is True
    assert sorted(s["target"] for s in sc.skipped) == [
        "buttons/end_round.png", "home/end_round_dialog.png", "home/end_round_yes.png"]


def test_the_advisory_end_round_rows_do_not_list_images_that_are_on_disk(monkeypatch):
    # buttons/end_round.png learned on another tier, the dialog cut earlier: not missing
    sc, home = _surrender(monkeypatch, SURRENDER_SCREENS, have=set(END_ROUND_PAIR) | {"home/end_round_dialog.png"})
    assert home is True and sc.skipped == []


def _yes_by_text_only(monkeypatch, screens, *, first_row_buttons_real, have=()):
    """Both END ROUND routes with a Yes the button-pair finder misses but the
    dialog's text names (L1): while `first_row_buttons_real`, the Surrender
    dialog's buttons stay real (_row_buttons is real only on the "surrender" screen)."""
    monkeypatch.setitem(_TEXT, "end_round_q_yes", ["END ROUND", "Are you sure you want to end the round?", "No", "Yes"])
    sc = _Scanner(screens)
    _buttons_found_only_on(monkeypatch, sc, {"surrender"} if first_row_buttons_real else set())
    return _surrender(monkeypatch, screens, have=have, sc=sc)


@pytest.mark.parametrize("screens,real_first,tap", [
    (["menu_end", "end_round_q_yes", "stats"], False, "flow: confirm exit (end_round)"),
    (["menu_exit", "surrender", "end_round_q_yes", "stats"], True, "flow: confirm END ROUND (yes)"),
], ids=["direct END ROUND", "after Surrender"])
def test_a_yes_tapped_by_text_only_cuts_no_image_so_the_scan_reports_the_missing_yes(monkeypatch, screens, real_first, tap):
    # the Yes was located by OCR and tapped, but no template exists for it: Learn
    # must not finish clean while readiness blocks the runs on that image
    sc, home = _yes_by_text_only(monkeypatch, screens, first_row_buttons_real=real_first)
    assert home is True and sc.taps[-1] == tap
    assert "home/end_round_yes.png" not in sc.cal.cuts
    assert [s["target"] for s in sc.skipped if not s.get("optional")] == ["home/end_round_yes.png"]
    assert "found by text only" in _end_round_skips(sc)["home/end_round_yes.png"]["reason"]


@pytest.mark.parametrize("screens,real_first", [
    (["menu_end", "end_round_q_yes", "stats"], False),
    (["menu_exit", "surrender", "end_round_q_yes", "stats"], True),
], ids=["direct END ROUND", "after Surrender"])
def test_a_yes_found_by_text_only_is_not_reported_when_the_image_is_on_disk(monkeypatch, screens, real_first):
    sc, home = _yes_by_text_only(monkeypatch, screens, first_row_buttons_real=real_first,
                                 have={"home/end_round_yes.png"})
    assert home is True and [s for s in sc.skipped if not s.get("optional")] == []


def test_the_missing_yes_is_reported_once_however_many_runs_find_it_missing(monkeypatch):
    # a setup surrenders more than once (top tier, then Tier 1) and capture_battle_flow
    # adds its own report: the scan lists the image a single time
    from runtime import logger
    monkeypatch.setattr(logger, "event", lambda *a, **k: None)
    monkeypatch.setattr(fc, "_have_templates", lambda rels: set())
    flow = fc._Flow(_Scanner(["dying"]))
    for why in ("first run", "second run"):
        fc._skip_end_round_yes(flow, why)
    assert [s["target"] for s in flow.s.skipped] == ["home/end_round_yes.png"]
    assert "first run" in flow.s.skipped[0]["reason"]


def test_a_skip_an_earlier_pass_filed_is_withdrawn_when_a_later_pass_cuts_the_image(monkeypatch):
    # pass 1 (EXIT BATTLE, no confirm) files the Yes and both advisory rows; pass 2
    # of the same scan opens END ROUND and cuts all three: an image is cut or
    # reported, never both (a stale row would hold the scan in needs_attention)
    sc, home = _surrender(monkeypatch, SURRENDER_SCREENS)
    assert len(sc.skipped) == 3
    sc, home = _surrender(monkeypatch, ["menu_end", "surrender", "end_round_q", "stats"], sc=sc)
    assert home is True and "home/end_round_yes.png" in sc.cal.cuts
    assert sc.skipped == []


def test_a_yes_found_by_text_only_is_withdrawn_when_a_later_pass_cuts_it(monkeypatch):
    real = fc._row_buttons
    sc = _Scanner(["menu_end", "end_round_q_yes", "stats"])
    monkeypatch.setitem(_TEXT, "end_round_q_yes", ["END ROUND", "Are you sure you want to end the round?", "No", "Yes"])
    monkeypatch.setattr(fc, "_row_buttons", lambda frame, *a, **k: (None, None))
    sc, home = _surrender(monkeypatch, sc.screens, sc=sc)
    assert [s["target"] for s in sc.skipped if not s.get("optional")] == ["home/end_round_yes.png"]
    monkeypatch.setattr(fc, "_row_buttons", real)
    sc, home = _surrender(monkeypatch, ["menu_exit", "surrender", "end_round_q", "stats"], sc=sc)
    assert "home/end_round_yes.png" in sc.cal.cuts
    assert [s for s in sc.skipped if not s.get("optional")] == []


def test_an_unverified_yes_cut_is_listed_once_when_a_setup_surrenders_twice(monkeypatch):
    # Calibration.cut refuses the Yes both times; cut_crop's skip names the image once
    sc = _Scanner(["menu_exit", "surrender", "end_round_q", "stats"])
    sc.cal.cut = lambda phase, rel, *a, **k: {"rel": rel, "verified": rel != "home/end_round_yes.png",
                                              "reason": "not unique on the frame"}
    sc, home = _surrender(monkeypatch, sc.screens, sc=sc)
    sc, home = _surrender(monkeypatch, ["menu_exit", "surrender", "end_round_q", "stats"], sc=sc)
    assert [s["target"] for s in sc.skipped].count("home/end_round_yes.png") == 1


def test_a_blocking_skip_is_not_swallowed_by_an_earlier_optional_row_for_the_same_image(monkeypatch):
    from runtime import logger
    monkeypatch.setattr(logger, "event", lambda *a, **k: None)
    flow = fc._Flow(_Scanner(["dying"]))
    flow.skip("buttons/end_round.png", "EXIT BATTLE menu", optional=True)
    flow.skip("buttons/end_round.png", "advisory again", optional=True)
    flow.skip("buttons/end_round.png", "END ROUND crop refused")
    flow.skip("buttons/end_round.png", "refused again")
    assert [(s["reason"], bool(s.get("optional"))) for s in flow.s.skipped] == [
        ("EXIT BATTLE menu", True), ("END ROUND crop refused", False)]


def test_a_surrender_tapped_by_text_only_cuts_no_image_so_the_scan_reports_the_missing_surrender(monkeypatch):
    # readiness blocks the same runs on home/surrender.png: the Surrender button was
    # located by OCR and tapped (the END ROUND Yes is real here), nothing was cut
    sc = _Scanner(["menu_exit", "surrender", "end_round_q", "stats"])
    _buttons_found_only_on(monkeypatch, sc, {"end_round_q"})
    sc, home = _surrender(monkeypatch, sc.screens, sc=sc)
    assert home is True and sc.taps[:2] == ["flow: tap EXIT BATTLE", "flow: confirm exit (surrender)"]
    assert "home/surrender.png" not in sc.cal.cuts
    skipped = [s for s in sc.skipped if not s.get("optional")]
    assert [s["target"] for s in skipped] == ["home/surrender.png"]
    assert "found by text only" in skipped[0]["reason"]


def test_a_surrender_found_by_text_only_is_not_reported_when_the_image_is_on_disk(monkeypatch):
    sc = _Scanner(["menu_exit", "surrender", "end_round_q", "stats"])
    _buttons_found_only_on(monkeypatch, sc, {"end_round_q"})
    sc, home = _surrender(monkeypatch, sc.screens, have={"home/surrender.png"}, sc=sc)
    assert home is True and [s for s in sc.skipped if not s.get("optional")] == []


def _battle_flow(monkeypatch, *, have=(), filed=()):
    """capture_battle_flow over stubbed passes: every run 'ended on its own', so
    no pass reaches end_run_and_capture. `filed`: skips an earlier pass left."""
    from runtime import logger
    monkeypatch.setattr(logger, "event", lambda *a, **k: None)
    monkeypatch.setattr(fc, "can_start_battle", lambda **k: (True, ""))
    monkeypatch.setattr(fc, "read_tier", lambda f: 14)
    monkeypatch.setattr(fc, "set_tier", lambda flow, t: t)
    monkeypatch.setattr(fc, "_battle_pass", lambda flow, tier, label: None)
    monkeypatch.setattr(fc, "_hud_digit_pass", lambda flow: None)
    monkeypatch.setattr(fc, "_have_templates", lambda rels: {r for r in rels if r in have})
    flow = fc._Flow(_Scanner(["dying"]))
    flow.s.skipped.extend(filed)
    flow.on_home = lambda f: True
    flow.wave = lambda f: None
    flow.side_menu = flow.game_stats = flow.in_tournament = lambda f: False
    fc.capture_battle_flow(flow)
    return flow.s.skipped


def test_a_setup_whose_runs_all_ended_on_their_own_reports_the_missing_yes_once(monkeypatch):
    # run-ended-by-itself goes straight to capture_game_stats: no confirm ever came up
    skipped = _battle_flow(monkeypatch)
    assert [(s["target"], s.get("optional", False)) for s in skipped] == [("home/end_round_yes.png", False)]
    assert "no END ROUND confirm" in skipped[0]["reason"]


def test_the_end_of_setup_report_adds_nothing_when_the_yes_is_on_disk_or_already_listed(monkeypatch):
    assert _battle_flow(monkeypatch, have={"home/end_round_yes.png"}) == []
    earlier = [{"target": "home/end_round_yes.png", "reason": "earlier pass"}]
    assert _battle_flow(monkeypatch, filed=earlier) == earlier


def _interrupted_battle_scan(tmp_path, monkeypatch, broken, *, have=()):
    """The real Scanner._run_flows over a capture_battle_flow in which `broken`
    (_battle_pass, _hud_digit_pass raise; read_tier reads no tier) interrupts it:
    the bootstrap records needs_attention and re-raises without recovery input.
    Returns the scanner after observing that exception."""
    import socket
    from device import adbclient
    from runtime import logger
    # backstop: an adbclient call the stubs below miss meets a dead loopback
    # port, never the real adb server on 5037
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    monkeypatch.setattr(adbclient, "_HOST", ("127.0.0.1", dead.getsockname()[1]))
    dead.close()
    monkeypatch.setattr(logger, "event", lambda *a, **k: None)

    def boom(*a, **k):
        raise RuntimeError(f"{broken} failed")
    cal = calibrate.Calibration({"state": str(tmp_path / "state.json")}, False, bootstrap=True)
    sc = bootstrap.Scanner(cal, {}, flows=True, grab=lambda: None, tap=lambda *a, **k: None, pause=lambda _: None)
    sc.sweep = lambda: True
    sc.check_stop = lambda: None
    monkeypatch.setattr(fc, "capture_menu_extras", lambda flow: None)
    monkeypatch.setattr(fc, "_safe_home", lambda flow: pytest.fail('unexpected recovery'))
    monkeypatch.setattr(fc, "can_start_battle", lambda **k: (True, ""))
    monkeypatch.setattr(fc, "read_tier", (lambda f: None) if broken == "read_tier" else lambda f: 14)
    monkeypatch.setattr(fc, "set_tier", lambda flow, t: t)
    monkeypatch.setattr(fc, "_have_templates", lambda rels: {r for r in rels if r in have})
    monkeypatch.setattr(fc, "_battle_pass", boom if broken == "_battle_pass" else lambda flow, tier, label: None)
    monkeypatch.setattr(fc, "_hud_digit_pass", boom if broken == "_hud_digit_pass" else lambda flow: None)
    for name, value in (("on_home", True), ("wave", None), ("side_menu", False), ("game_stats", False),
                        ("in_tournament", False)):
        monkeypatch.setattr(fc._Flow, name, lambda self, f, value=value: value)
    with pytest.raises(RuntimeError):
        sc._run_flows()
    assert next(s for s in sc.steps if s["id"] == "flow_battle")["status"] == "needs_attention"
    return sc


# the raise paths of capture_battle_flow: a run that ended on its own whose HOME is
# not confirmed or an unselectable tier (_battle_pass), the Tier-1 digit pass, and
# an unreadable starting tier (before any run)
@pytest.mark.parametrize("broken,error", [("_battle_pass", "_battle_pass failed"), ("_hud_digit_pass", "_hud_digit_pass failed"),
                                          ("read_tier", "Could not read the original difficulty")])
def test_an_interrupted_battle_capture_still_reports_the_missing_yes(tmp_path, monkeypatch, broken, error):
    # the bootstrap closes the step needs_attention, but the scan's final status
    # reads only the skipped list: without the Yes there Learn ends 'done' while
    # readiness keeps Shard, quest and max-wave runs blocked on it
    sc = _interrupted_battle_scan(tmp_path, monkeypatch, broken)
    assert [(s["target"], bool(s.get("optional"))) for s in sc.skipped] == [("home/end_round_yes.png", False)]
    assert error in sc.skipped[0]["reason"]


def test_an_interrupted_battle_capture_does_not_report_a_yes_that_is_on_disk(tmp_path, monkeypatch):
    sc = _interrupted_battle_scan(tmp_path, monkeypatch, "_battle_pass", have={"home/end_round_yes.png"})
    assert sc.skipped == []


def test_end_round_images_are_cut_only_when_the_game_shows_them(monkeypatch):
    sc, home = _surrender(monkeypatch, ["menu_end", "surrender", "end_round_q", "stats"])
    assert home is True
    assert sc.cal.cuts == ["buttons/end_round.png", "home/exit_battle_dialog.png", "home/surrender.png",
                           "home/end_round_dialog.png", "home/end_round_yes.png"]
    assert sc.taps[-1] == "flow: confirm END ROUND (yes)"
    assert _end_round_skips(sc) == {} and sc.shots == []


# What the END ROUND pair decides in shard.abandon_run (every shard / quest /
# max-wave surrender), which taps an exit control or a confirm only where a
# template matched but taps the side-menu toggle whenever no side menu reads
# open. Without home/end_round_yes.png the END ROUND confirm that can follow
# Surrender is never answered: abandon_run Aborts and leaves it up, and the
# next abandon_run taps the toggle onto that dialog - so the Yes blocks.
# Without buttons/end_round.png an END ROUND menu is never tapped: abandon_run
# Aborts with the menu open, and the next one taps the toggle, which closes it.
_AFTER_TAP = {("battle", "open side menu"): "menu", ("menu", "exit battle"): "surrender_dialog",
              ("menu", "open side menu"): "battle",
              ("surrender_dialog", "surrender"): "end_round_q", ("end_round_q", "end round yes"): "stats",
              ("stats", "RETRY"): "battle"}


def _abandon(monkeypatch, have, menu="buttons/exit_battle.png"):
    """Run the real shard.abandon_run over a screen state machine; `find`
    hits only templates in `have` that the current screen shows."""
    import socket
    from types import SimpleNamespace
    from device import adbclient
    from flows import shard
    # backstop: an adbclient call the stubs below miss meets a dead loopback
    # port, never the real adb server on 5037
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    monkeypatch.setattr(adbclient, "_HOST", ("127.0.0.1", dead.getsockname()[1]))
    dead.close()          # nothing listens here now
    shows = {"menu": {menu}, "surrender_dialog": {"home/surrender.png"}, "end_round_q": {"home/end_round_yes.png"}}
    world = SimpleNamespace(screen="battle", taps=[], clock=0.0)

    def tap(x, y, reason="", **k):
        world.taps.append(reason)
        world.screen = _AFTER_TAP.get((world.screen, reason), world.screen)

    def find(frame, rel, thresh=0.0, own=True):
        return ((540, 1400), 0.99) if rel in have and rel in shows.get(frame, ()) else None

    def sleep(s):
        world.clock += s
    monkeypatch.setattr(shard.capture, "grab", lambda: world.screen)
    monkeypatch.setattr(shard.act, "tap", tap)
    monkeypatch.setattr(shard, "find", find)
    monkeypatch.setattr(shard.detect, "side_menu_open",
                        lambda f: bool(find(f, "buttons/exit_battle.png") or find(f, "buttons/end_round.png")))
    monkeypatch.setattr(shard.detect, "death_screen", lambda f: (True, (540, 2000)) if f == "stats" else (False, None))
    monkeypatch.setattr(shard.screen, "in_tournament", lambda f: False)
    monkeypatch.setattr(shard.logger, "shot", lambda *a, **k: "shot.png")
    monkeypatch.setattr(shard.logger, "event", lambda *a, **k: None)
    monkeypatch.setattr(shard, "time", SimpleNamespace(monotonic=lambda: world.clock, sleep=sleep))
    return shard, world


def test_the_abandon_harness_cannot_reach_the_real_adb_server(monkeypatch):
    # a path the stubs below miss must meet a dead loopback port, never 5037
    import socket
    from device import adbclient
    _abandon(monkeypatch, set())
    host, port = adbclient._HOST
    assert host == "127.0.0.1" and port != 5037
    with pytest.raises(OSError):
        socket.create_connection(adbclient._HOST, timeout=1).close()


def test_without_end_round_yes_the_confirm_after_surrender_is_never_tapped(monkeypatch):
    shard, world = _abandon(monkeypatch, {"buttons/exit_battle.png", "home/surrender.png"})
    with pytest.raises(shard.Abort, match="GAME STATS / RETRY never appeared"):
        shard.abandon_run()
    assert world.taps == ["open side menu", "exit battle", "surrender"]
    assert world.screen == "end_round_q"


def test_with_end_round_yes_the_confirm_after_surrender_is_answered(monkeypatch):
    shard, world = _abandon(monkeypatch, {"buttons/exit_battle.png", "home/surrender.png", "home/end_round_yes.png"})
    assert shard.abandon_run() is True
    assert world.taps == ["open side menu", "exit battle", "surrender", "end round yes", "RETRY"]


def test_without_end_round_png_a_menu_showing_end_round_is_never_tapped(monkeypatch):
    shard, world = _abandon(monkeypatch, {"buttons/exit_battle.png", "home/surrender.png", "home/end_round_yes.png"},
                            menu="buttons/end_round.png")
    with pytest.raises(shard.Abort, match="neither END ROUND nor EXIT BATTLE"):
        shard.abandon_run()
    assert world.taps == ["open side menu"] and world.screen == "menu"


def test_a_later_abandon_run_over_the_confirm_it_left_taps_the_toggle_onto_it(monkeypatch):
    # the Abort above leaves the END ROUND confirm up; an abandon_run called
    # over it (quest_sm adopts a readable wave, a surrender_retry rule) reads
    # no open side menu and taps the toggle point onto the dialog
    shard, world = _abandon(monkeypatch, {"buttons/exit_battle.png", "home/surrender.png"})
    world.screen = "end_round_q"
    with pytest.raises(shard.Abort, match="neither END ROUND nor EXIT BATTLE"):
        shard.abandon_run()
    assert world.taps == ["open side menu"] and world.screen == "end_round_q"
    # with the Yes the same call answers the dialog and never taps the toggle
    shard, world = _abandon(monkeypatch, {"buttons/exit_battle.png", "home/surrender.png", "home/end_round_yes.png"})
    world.screen = "end_round_q"
    assert shard.abandon_run() is True
    assert world.taps == ["end round yes", "RETRY"]


def test_a_later_abandon_run_over_the_end_round_menu_it_left_closes_it(monkeypatch):
    # the menu the END ROUND Abort left open reads shut without the template:
    # the toggle tap closes the bot's own menu and the call Aborts again
    shard, world = _abandon(monkeypatch, {"buttons/exit_battle.png", "home/surrender.png", "home/end_round_yes.png"},
                            menu="buttons/end_round.png")
    world.screen = "menu"
    with pytest.raises(shard.Abort, match="neither END ROUND nor EXIT BATTLE"):
        shard.abandon_run()
    assert world.taps == ["open side menu"] and world.screen == "battle"
