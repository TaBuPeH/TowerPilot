"""The consented action-capture flows (player/flow_capture.py).

Device I/O is faked: these prove the decision points a wrong reading could make
dangerous (start-battle gate, exit-dialog classification), the capture wiring
(OCR -> verified write via Calibration.cut), and that one flow failing does not
take the others down. The live taps themselves are watched on a real run.
"""
import types
import numpy as np
import pytest

from player import flow_capture as fc


# ------------------------------------------------------------- pure helpers
def test_exit_dialog_kind_classifies_each_confirm_and_refuses_the_ambiguous():
    assert fc.exit_dialog_kind(["EXIT BATTLE", "What would you like to do?",
                                "Surrender", "Go Home"]) == "surrender"
    assert fc.exit_dialog_kind(["END ROUND?", "Yes", "No"]) == "end_round"
    # the real END ROUND dialog: its Yes/No buttons are stylised text OCR misses,
    # so the QUESTION is what identifies it (seen live at Tier 19)
    assert fc.exit_dialog_kind(["END ROUND",
                                "Are you sure you want to end the round?"]) == "end_round"
    # a bare wave counter / unrelated screen must not be read as a dialog
    assert fc.exit_dialog_kind(["1234", "Coins"]) is None
    assert fc.exit_dialog_kind([]) is None
    # "END ROUND" as the side-menu button (no question, no Yes/No) is not the dialog
    assert fc.exit_dialog_kind(["END ROUND"]) is None


def test_can_start_battle_only_passes_a_clean_home():
    ok, _ = fc.can_start_battle(on_home=True, wave=None, side_menu=False,
                                game_stats=False, tournament=False)
    assert ok
    # every guard refuses, and never for the tournament reason unless it is one
    assert fc.can_start_battle(on_home=True, wave=None, side_menu=False,
                               game_stats=False, tournament=True)[0] is False
    assert fc.can_start_battle(on_home=True, wave=940, side_menu=False,
                               game_stats=False, tournament=False)[0] is False
    assert fc.can_start_battle(on_home=True, wave=None, side_menu=True,
                               game_stats=False, tournament=False)[0] is False
    assert fc.can_start_battle(on_home=True, wave=None, side_menu=False,
                               game_stats=True, tournament=False)[0] is False
    assert fc.can_start_battle(on_home=False, wave=None, side_menu=False,
                               game_stats=False, tournament=False)[0] is False


def test_has_text_is_case_and_space_insensitive():
    assert fc.has_text(["  game   stats "], "GAME STATS")
    assert fc.has_text(["Surrender"], "surrender")
    assert not fc.has_text(["Coins", "1234"], "SURRENDER")


def test_exit_label_reads_the_menu_open_signal():
    # the exit control's presence is how the flow knows the side menu is open,
    # so it never taps the toggle on an already-open menu (which would shut it)
    assert fc.exit_label(["END ROUND"]) == "END ROUND"
    assert fc.exit_label(["EXIT BATTLE"]) == "EXIT BATTLE"
    assert fc.exit_label(["Wave 1", "ATTACK UPGRADES"]) is None
    assert fc.exit_label([]) is None


# ------------------------------------------------------------- fakes
class _FakeCal:
    def __init__(self):
        self.cuts = []

    def cut(self, phase, rel, crop, frame, name, extra=None):
        self.cuts.append({"rel": rel, "name": name, "crop": crop, "extra": extra})
        return {"rel": rel, "verified": True}


class _FakeScanner:
    def __init__(self, frame):
        self._frame = frame
        self.cal = _FakeCal()
        self.skipped = []
        self.taps = []
        self.messages = []

    def grab(self):
        return self._frame

    def tap(self, x, y, reason=""):
        self.taps.append((x, y, reason))
        return {"x": x, "y": y}

    def pause(self, _):
        pass

    def check_stop(self):
        pass

    def progress(self, message, **extra):
        self.messages.append(message)


def _frame():
    # non-trivial texture so a real cut would pass; the fake ignores it anyway
    return np.random.default_rng(3).integers(0, 255, (2560, 1080, 3), np.uint8)


# ------------------------------------------------------------- capture wiring
def test_capture_writes_the_located_text_and_skips_the_absent(monkeypatch):
    from vision import textocr

    # one OCR line, in the upscaled coordinates read_lines returns
    monkeypatch.setattr(textocr, "read_lines",
                        lambda bgr, scale=2.0: [(40, 60, "SURRENDER")])
    scanner = _FakeScanner(_frame())
    flow = fc._Flow(scanner)

    entry = flow.capture("home/surrender.png", "SURRENDER", fc.R_DIALOG, (300, 110))
    assert entry and entry["rel"] == "home/surrender.png"
    assert scanner.cal.cuts[-1]["rel"] == "home/surrender.png"
    assert scanner.cal.cuts[-1]["extra"]["source"] == "flow_ocr"
    assert not scanner.skipped

    # text that is not on screen is skipped, never cut, never tapped
    missing = flow.capture("home/end_round_yes.png", "YES", fc.R_DIALOG, (250, 80))
    assert missing is None
    assert scanner.skipped and scanner.skipped[-1]["target"] == "home/end_round_yes.png"


def test_capture_reports_a_rejected_cut_as_skipped(monkeypatch):
    from vision import textocr
    monkeypatch.setattr(textocr, "read_lines",
                        lambda bgr, scale=2.0: [(40, 60, "SURRENDER")])
    scanner = _FakeScanner(_frame())
    # the cut is unverified (blank/ambiguous) -> capture must not claim success
    scanner.cal.cut = lambda *a, **k: {"rel": a[1], "verified": False,
                                       "reason": "blank"}
    flow = fc._Flow(scanner)
    assert flow.capture("home/surrender.png", "SURRENDER", fc.R_DIALOG, (300, 110)) is None
    assert scanner.skipped[-1]["target"] == "home/surrender.png"


# ------------------------------------------------------------- isolation
def test_run_flows_isolates_a_failing_stage(monkeypatch):
    from runtime import logger
    monkeypatch.setattr(logger, "event", lambda *a, **k: None)
    monkeypatch.setattr(fc, "_safe_home", lambda flow: None)
    calls = []
    monkeypatch.setattr(fc, "capture_menu_extras",
                        lambda flow: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(fc, "capture_battle_flow",
                        lambda flow, **kw: calls.append(("battle", kw)))

    flow = fc._Flow(_FakeScanner(_frame()))
    fc.run_flows(flow)                     # must not raise despite the menu boom
    assert calls == [("battle", {"do_lottery": False})]   # ran, lottery OFF by default


# ------------------------------------------- run-time UW-label subroutine
def test_capture_missing_uw_labels_writes_only_missing_never_overwrites(monkeypatch):
    from player import battle_capture as bc, accounts
    from vision import textocr
    from runtime import logger
    # everything is on disk EXCEPT smart_missiles
    monkeypatch.setattr(accounts, "template_path",
                        lambda root, cfg, rel, **k: types.SimpleNamespace(
                            is_file=lambda: rel != "uw/smart_missiles.png"))
    # the panel only shows the Smart Missiles label right now
    monkeypatch.setattr(bc, "capture_by_text",
                        lambda frame, text, region, size, read, anchor=None:
                        "CROP" if text == "Smart Missiles" else None)
    writes = []
    monkeypatch.setattr(bc, "write_template",
                        lambda rel, crop: (writes.append(rel), "written")[1])
    monkeypatch.setattr(textocr, "read_lines", lambda c, s=2: [])
    monkeypatch.setattr(logger, "event", lambda *a, **k: None)

    got = fc.capture_missing_uw_labels(_frame())
    assert got == ["uw/smart_missiles.png"]     # only the missing one
    assert writes == ["uw/smart_missiles.png"]  # the already-present ones untouched


def test_capture_missing_uw_labels_noop_when_all_known(monkeypatch):
    from player import accounts, battle_capture as bc
    monkeypatch.setattr(accounts, "template_path",
                        lambda root, cfg, rel, **k: types.SimpleNamespace(
                            is_file=lambda: True))
    # must not even look at the frame once every UW template exists
    def _boom(*a, **k):
        raise AssertionError("capture_by_text called though nothing is missing")
    monkeypatch.setattr(bc, "capture_by_text", _boom)
    assert fc.capture_missing_uw_labels(_frame()) == []


def test_setup_uw_lottery_is_opt_in_off_by_default(monkeypatch):
    # finding the unowned UW must NOT block setup: capture_battle_flow runs the
    # END ROUND pass but only touches the lottery when explicitly asked.
    monkeypatch.setattr(fc, "can_start_battle", lambda **k: (True, ""))
    monkeypatch.setattr(fc, "read_tier", lambda f: 14)
    monkeypatch.setattr(fc, "set_tier", lambda flow, t: t)
    monkeypatch.setattr(fc, "_battle_pass", lambda flow, tier, label: None)
    monkeypatch.setattr(fc, "_uw_remaining",
                        lambda: [("uw/smart_missiles.png", "Smart Missiles", (1, 1))])
    lot = []
    monkeypatch.setattr(fc, "_uw_lottery_pass",
                        lambda flow, max_minutes=None: lot.append(True))
    flow = fc._Flow(_FakeScanner(_frame()))
    flow.on_home = lambda f: True
    flow.wave = lambda f: None
    flow.side_menu = lambda f: False
    flow.game_stats = lambda f: False
    flow.in_tournament = lambda f: False

    fc.capture_battle_flow(flow)                      # default: lottery OFF
    assert lot == []
    fc.capture_battle_flow(flow, do_lottery=True)     # opt-in: lottery ON
    assert lot == [True]


def test_setup_starts_at_max_tier_before_missing_font_pass(monkeypatch):
    calls=[]
    monkeypatch.setattr(fc, 'can_start_battle', lambda **k: (True, ''))
    monkeypatch.setattr(fc, 'read_tier', lambda f: 14)
    monkeypatch.setattr(fc, '_battle_pass', lambda f,t,label: calls.append(t))
    monkeypatch.setattr(fc, '_hud_digit_pass', lambda f: calls.append('missing font'))
    monkeypatch.setattr(fc, '_have_templates', lambda rels: set(rels))
    flow=fc._Flow(_FakeScanner(_frame()))
    flow.on_home=lambda f: True
    flow.wave=lambda f: None
    flow.side_menu=flow.game_stats=flow.in_tournament=lambda f: False
    fc.capture_battle_flow(flow)
    assert calls == [fc.TIER_FOR_END_ROUND, 'missing font']


def test_optional_effect_observation_is_bounded(monkeypatch):
    from types import SimpleNamespace
    ticks=iter([0, 0, 1, 91])
    monkeypatch.setattr(fc.time, 'monotonic', lambda: next(ticks))
    captures=[]
    monkeypatch.setattr(fc, 'capture_artwork_targets', lambda *a,**k: captures.append(1))
    flow=SimpleNamespace(s=SimpleNamespace(check_stop=lambda:None), grab=lambda:None,
        game_stats=lambda f:False, on_home=lambda f:False, progress=lambda s:None,
        pause=lambda s:None)
    fc.observe_optional_effects(flow)
    assert captures == [1]


def test_setup_off_switch_capture_restores_on(tmp_path, monkeypatch):
    import settings
    from types import SimpleNamespace
    from interactions import shopper
    label=tmp_path/'chain_lightning.png'
    label.write_bytes(b'label')
    monkeypatch.setattr(settings,'template_path',lambda rel: label if rel=='uw/chain_lightning.png' else tmp_path/'missing')
    monkeypatch.setattr(fc.cv2,'imread',lambda p: _frame())
    monkeypatch.setattr(shopper,'_scroll_to_top',lambda:None)
    on=('on',[10,20,80,40],'uw/chain_lightning.png')
    off=('off',[10,20,80,40],'uw/chain_lightning.png')
    states=iter([[on],[on],[off],[on]])
    monkeypatch.setattr(fc,'find_uw_switches',lambda *a:next(states))
    captured=[]
    monkeypatch.setattr(fc,'capture_uw_switches',lambda *a:captured.append(True))
    taps=[]
    flow=SimpleNamespace(grab=lambda:_frame(),pause=lambda t:None,cal=object(),
        progress=lambda s:None,tap=lambda x,y,reason:taps.append(reason))
    fc.capture_missing_off_state(flow)
    assert captured == [True]
    assert taps == ['setup: capture OFF switch','setup: restore weapon ON']


# ------------------------------------------------------------- manifest contract
def test_every_flow_target_is_declared_writable():
    from player.bootstrap_layout import writable_targets
    w = writable_targets()
    battle = {rel for rel, _t, _s in fc.UW_WEAPONS}
    battle |= {"icons/intro_sprint.png", "home/intro_sprint_dialog.png",
               "home/intro_sprint_yes.png", "buttons/end_round.png",
               "home/exit_battle_dialog.png", "home/surrender.png",
               "home/end_round_dialog.png", "home/end_round_yes.png",
               "buttons/reward_skip.png", "icons/game_stats.png",
               "buttons/more_stats.png", "buttons/perks.png"}
    assert battle <= w, f"not writable: {sorted(battle - w)}"


def test_start_battle_refusal_message_never_says_tournament_for_a_normal_run():
    # a live coin run must be refused as "a run is in progress", not misattributed
    _, why = fc.can_start_battle(on_home=True, wave=1500, side_menu=False,
                                 game_stats=False, tournament=False)
    assert "tournament" not in why.lower()
