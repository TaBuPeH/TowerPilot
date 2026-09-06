"""The calibrator: structural pill detection, the cut policy, the naming, and
the dashboard wiring - everything but the emulator, which is faked."""
import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent


def _pill_frame(labels, y=400, x0=20, w=190, h=75, gap=22, active=0):
    """A synthetic tab row in the game's grammar: dark fill, cyan outline,
    the active one green, white label text."""
    frame = np.full((2560, 1080, 3), (30, 18, 40), np.uint8)
    rects = []
    for i, label in enumerate(labels):
        x = x0 + i * (w + gap)
        colour = (0, 255, 128) if i == active else (255, 230, 0)   # BGR green / cyan
        cv2.rectangle(frame, (x, y), (x + w, y + h), colour, 4)
        cv2.rectangle(frame, (x + 4, y + 4), (x + w - 4, y + h - 4), (8, 4, 16), -1)
        cv2.putText(frame, label, (x + 22, y + h - 24), cv2.FONT_HERSHEY_SIMPLEX,
                    0.9, (255, 255, 255), 2, cv2.LINE_AA)
        rects.append((x, y, w, h))
    return frame, rects


@pytest.fixture()
def dash():
    path = REPO / "frontend" / "dashboard.py"
    spec = importlib.util.spec_from_file_location("tp_dashboard_cal", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- pills
def test_pills_finds_every_tab_with_its_state_in_reading_order():
    from vision import pills
    frame, rects = _pill_frame(["Alpha", "Beta", "Gamma", "Delta"], active=2)
    found = pills.pills(frame, 330, 560)
    assert [p["state"] for p in found] == ["cyan", "cyan", "green", "cyan"]
    for p, (x, y, w, h) in zip(found, rects):
        px, py, pw, ph = p["rect"]
        assert x < px < x + 12 and y < py < y + 12 and abs(pw - w) < 16 and abs(ph - h) < 16
    assert pills.pills(frame, 600, 900) == []          # nothing below the row
    assert len(pills.rows_of(found)) == 1 and len(pills.rows_of(found)[0]) == 4


def test_text_crop_is_the_label_and_matches_the_other_state():
    from vision import pills
    frame, rects = _pill_frame(["Farm", "Tourney"], active=0)
    other, _ = _pill_frame(["Farm", "Tourney"], active=1)   # states swapped
    found = pills.pills(frame, 330, 560)
    for p in found:
        crop, trect = pills.text_crop(frame, p["rect"])
        assert crop is not None and crop.shape[0] < 60 and crop.shape[1] < 150
        best, centre, second = pills.match(other, crop)
        assert best > 0.95 and second < 0.8
        assert abs(centre[0] - (trect[0] + trect[2] // 2)) < 6


def test_header_slots_report_occupancy_by_ring():
    from vision import pills
    frame = np.full((2560, 1080, 3), (30, 18, 40), np.uint8)
    cx, cy = pills.HEADER_LARGE[0]
    cv2.circle(frame, (cx, cy), pills.HEADER_RADIUS["large"], (0, 255, 128), 8)
    slots = pills.header_slots(frame)
    assert len(slots) == 8
    occupied = [s for s in slots if s["occupied"]]
    assert [s["centre"] for s in occupied] == [(cx, cy)]
    assert all(s["kind"] in ("large", "small") and s["half"] > 30 for s in slots)


def test_grid_rows_reads_tile_rows_off_the_frame():
    from vision import pills
    frame = np.zeros((2560, 1080, 3), np.uint8)
    for cy in (1192, 1400, 1601):
        for cx in (126, 329, 533, 736, 939):
            cv2.circle(frame, (cx, cy), 70, (0, 255, 128), 6)
    assert pills.grid_rows(frame) == [1192, 1400, 1601]


# ------------------------------------------------------------ calibrate
def test_only_account_specific_names_can_be_written(tmp_path, monkeypatch):
    from player import calibrate
    monkeypatch.setattr(calibrate.settings, "ROOT", tmp_path)
    crop = np.full((20, 40, 3), 200, np.uint8)
    assert calibrate.is_account_rel("cards/preset_farm_deck.png")
    assert calibrate.is_account_rel("presets/gp_farm_build.png")
    assert calibrate.is_account_rel("presets/workshop_devo.png")
    assert calibrate.is_account_rel("modules/equipped/space_displacer.png")
    assert calibrate.is_account_rel("modules/space_displacer.png")
    for generic in ("modules/buy_module.png", "buttons/nuke.png", "cards/cash.png",
                    "presets/picker_icon.png", "screens/hdr_cards.png", "modules/nope.png",
                    "../x.png"):
        assert not calibrate.is_account_rel(generic), generic
        assert calibrate.write_template(generic, crop) == "refused"
    assert calibrate.write_template("cards/preset_farm_deck.png", crop) == "written"
    assert (tmp_path / "templates" / "cards" / "preset_farm_deck.png").exists()
    assert calibrate.write_template("cards/preset_farm_deck.png", crop) == "exists"
    assert calibrate.write_template("cards/preset_farm_deck.png", crop, overwrite=True) == "written"


def test_module_names_and_rarities_survive_ocr_slips():
    from player import calibrate
    assert calibrate.resolve_module("Amplifying Strike") == "amplifying_strike"
    assert calibrate.resolve_module("Amplifyng Strke") == "amplifying_strike"
    assert calibrate.resolve_module("MVN") == "multiverse_nexus"
    assert calibrate.resolve_module("Tower Damage") is None
    assert calibrate.resolve_module("") is None
    assert calibrate.parse_rarity("ANCESTRAL") == "ancestral"
    assert calibrate.parse_rarity("-?NC?STRAL") == "ancestral"
    assert calibrate.parse_rarity("Mythic+") == "mythic+"
    assert calibrate.parse_rarity("Amplifying Strike") is None


def test_harvest_row_cuts_names_and_records_states(tmp_path, monkeypatch):
    from player import calibrate
    from vision import textocr
    monkeypatch.setattr(calibrate.settings, "ROOT", tmp_path)
    frame, _ = _pill_frame(["Farm Deck", "Tourney P1"], active=1)
    readings = iter(["Farm Deck", "Tourney P1"])
    monkeypatch.setattr(textocr, "read_text", lambda crop: next(readings))
    events = []
    import runtime.logger as lg
    monkeypatch.setattr(lg, "event", lambda kind, **kw: events.append(kind))
    p = {"state": str(tmp_path / "s.json"), "stop": str(tmp_path / "stop"),
         "report": str(tmp_path / "r.json"), "evidence": str(tmp_path / "ev")}
    cal = calibrate.Calibration(p, overwrite=False)
    out = calibrate.harvest_row(cal, frame, (330, 560), "cards/preset_", "cards")
    assert out == [("Farm Deck", "farm_deck", "cyan"), ("Tourney P1", "tourney_p1", "green")]
    assert (tmp_path / "templates" / "cards" / "preset_farm_deck.png").exists()
    assert (tmp_path / "templates" / "cards" / "preset_tourney_p1.png").exists()
    assert [e["status"] for e in cal.entries] == ["written", "written"]
    assert all(e["self"] > 0.95 for e in cal.entries)
    cal.save_report()
    rep = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert [e["rel"] for e in rep["entries"]] == ["cards/preset_farm_deck.png",
                                                  "cards/preset_tourney_p1.png"]
    assert events.count("calibrate_cut") == 2


def test_unreadable_label_gets_a_positional_name(tmp_path, monkeypatch):
    from player import calibrate
    from vision import textocr
    monkeypatch.setattr(calibrate.settings, "ROOT", tmp_path)
    frame, _ = _pill_frame(["Farm"], active=0)
    monkeypatch.setattr(textocr, "read_text", lambda crop: "")
    import runtime.logger as lg
    monkeypatch.setattr(lg, "event", lambda kind, **kw: None)
    p = {"state": "", "stop": str(tmp_path / "stop"), "report": "", "evidence": str(tmp_path)}
    cal = calibrate.Calibration(p, overwrite=False)
    out = calibrate.harvest_row(cal, frame, (330, 560), "presets/bots_", "bots")
    assert out == [("tab1", "tab1", "green")]
    assert (tmp_path / "templates" / "presets" / "bots_tab1.png").exists()


def test_catalogue_learns_module_names_the_shipped_table_lacks(tmp_path, monkeypatch):
    from player import catalogue
    monkeypatch.setattr(catalogue, "LOCAL", tmp_path / "catalogue_local.yaml")
    catalogue._local_cache.update(mtime=None, data={})
    assert catalogue.learn("Shrink Ray") == "shrink_ray"          # shipped: nothing written
    assert not (tmp_path / "catalogue_local.yaml").exists()
    assert catalogue.learn("Sentry Protocol") == "sentry_protocol"
    assert (tmp_path / "catalogue_local.yaml").exists()
    assert catalogue.resolve("sentry protocol") == "sentry_protocol"
    assert catalogue.resolve("Sentry Protocol") == "sentry_protocol"
    assert catalogue.display("sentry_protocol") == "Sentry Protocol"
    assert "sentry_protocol" in catalogue.all_modules() and "sentry_protocol" not in catalogue.MODULES
    assert catalogue.learn("Sentry Protocol") == "sentry_protocol"   # idempotent
    text = (tmp_path / "catalogue_local.yaml").read_text(encoding="utf-8")
    assert text.count("sentry_protocol") == 1
    with pytest.raises(KeyError):
        catalogue.resolve("no such module")


def test_calibrator_learns_plausible_names_and_refuses_effect_lines(tmp_path, monkeypatch):
    from player import calibrate, catalogue
    monkeypatch.setattr(catalogue, "LOCAL", tmp_path / "catalogue_local.yaml")
    catalogue._local_cache.update(mtime=None, data={})
    import runtime.logger as lg
    monkeypatch.setattr(lg, "event", lambda kind, **kw: None)
    assert calibrate.module_slug("Amplifying Strike") == "amplifying_strike"
    assert calibrate.module_slug("Swiftstrike Blitzer") == "swiftstrike_blitzer"
    assert calibrate.is_account_rel("modules/swiftstrike_blitzer.png")
    # an OCR slip of a LEARNED name resolves to it instead of being learned twice
    assert calibrate.module_slug("Swiftstrlke Blitzer") == "swiftstrike_blitzer"
    assert "swiftstrlke_blitzer" not in catalogue.all_modules()
    for garbage in ("x14.48 Tower Damage", "ANCESTRAL", "Lv. 184 /260", "", None, "ab"):
        assert calibrate.module_slug(garbage) is None, garbage


def test_ocr_fixes_the_l_for_one_slip():
    from vision import textocr
    assert textocr.fix_l1("Tourney Pl") == "Tourney P1"
    assert textocr.fix_l1("Tourney PI") == "Tourney P1"
    assert textocr.fix_l1("Full Upgraded") == "Full Upgraded"
    assert textocr.fix_l1("Disco") == "Disco"


def test_required_list_explains_an_equipped_modules_missing_tile(dash, tmp_path, monkeypatch):
    monkeypatch.setattr(dash, "ROOT", str(tmp_path))
    (tmp_path / "templates" / "modules" / "equipped").mkdir(parents=True)
    (tmp_path / "templates" / "modules" / "equipped" / "dimension_core.png").write_bytes(b"png")
    cfg = {"loadouts": {"t": {"modules": [["dimension_core", "assist"]]}}}
    prof = {"player": {"modules_equipped": ["dimension_core"]}}
    rows = {r["rel"]: r for r in dash._required_templates(cfg, prof)}
    assert rows["modules/dimension_core.png"]["have"] is False
    assert "equipped right now" in rows["modules/dimension_core.png"]["note"]
    assert rows["modules/equipped/dimension_core.png"]["have"] is True
    assert "note" not in rows["modules/equipped/dimension_core.png"]


def test_fresh_redoes_phases_but_keeps_the_report(tmp_path):
    from player import calibrate
    p = {"state": str(tmp_path / "calibrate_state.json")}
    calibrate._state_save(p, {"phases": {"cards": {"status": "done"}},
                              "entries": [{"rel": "cards/preset_a.png"}],
                              "player": {"card_presets": ["a"]}})
    st = calibrate._state_load(p)
    st["phases"] = {}                          # what --fresh does in main()
    assert st["entries"] == [{"rel": "cards/preset_a.png"}]
    assert st["player"] == {"card_presets": ["a"]}


# ---------------------------------------------------------- dashboard
def test_calibrate_endpoints_guard_taps_and_report_state(dash, tmp_path, monkeypatch):
    monkeypatch.setattr(dash, "_procs", lambda: [])
    monkeypatch.setattr(dash, "_procs_cached", lambda: [])
    monkeypatch.setattr(dash, "ROOT", str(tmp_path))
    cfg = {"active_instance": "main", "instances": {"main": {"allow_taps": False}}}
    monkeypatch.setattr(dash, "load_config", lambda: cfg)
    with dash.app.test_client() as c:
        r = c.post("/api/calibrate/start", json={})
        assert r.status_code == 409 and "allow_taps" in r.get_json()["error"]
        st = c.get("/api/calibrate/status").get_json()
        assert st["running"] is False and st["taps_allowed"] is False
        (tmp_path / "logs" / "main").mkdir(parents=True)
        (tmp_path / "logs" / "main" / "calibrate_report.json").write_text(
            json.dumps({"entries": [{"rel": "cards/preset_x.png", "status": "written"}]}),
            encoding="utf-8")
        st = c.get("/api/calibrate/status").get_json()
        assert st["report"]["entries"][0]["rel"] == "cards/preset_x.png"
        assert c.post("/api/calibrate/stop", json={}).status_code == 200
        assert (tmp_path / "logs" / "main" / "calibrate_stop").exists()
    monkeypatch.setattr(dash, "_procs", lambda: [{"runner": "orchestrator", "pid": 1}])
    cfg["instances"]["main"]["allow_taps"] = True
    with dash.app.test_client() as c:
        r = c.post("/api/calibrate/start", json={})
        assert r.status_code == 409 and "runners alive" in r.get_json()["error"]


def test_calibrator_counts_as_a_runner_for_the_process_guards():
    src = (REPO / "frontend" / "dashboard.py").read_text(encoding="utf-8")
    assert src.count("scan|calibrate|boot|dashboard") == 2
    scan_src = (BACKEND / "player" / "scan.py").read_text(encoding="utf-8")
    assert '"calibrate"' in scan_src


def test_ui_offers_the_calibrate_button_and_report():
    html = (REPO / "frontend" / "webui" / "index.html").read_text(encoding="utf-8")
    for needle in ("/api/calibrate/start", "/api/calibrate/status", "async function startCalibrate",
                   "function calibReport", "Calibrate now", "id=\"calib-over\"",
                   "fresh: true"):
        assert needle in html, needle


def test_navigation_landmarks_are_generic_not_account_templates():
    src = (BACKEND / "interactions" / "presets.py").read_text(encoding="utf-8")
    for account in ("presets/guardians_farm.png", "presets/bots_farm.png",
                    "presets/workshop_full_upgraded.png", "presets/workshop_devo.png"):
        assert account not in src, account
    assert "guardian/tab_guardian.png" in src and "TAB_BANDS" in src


# ------------------------------------------------------ grid walk parking
def test_grid_row_spans_and_at_top_tell_a_scrolled_grid_apart():
    from interactions import inventory
    from vision import pills
    frame = np.zeros((2560, 1080, 3), np.uint8)
    cv2.rectangle(frame, (0, 1000), (1080, 1079), (200, 80, 120), -1)     # the lit tab bar
    for cy in (1198, 1400):
        for cx in (126, 329, 533, 736, 939):
            cv2.circle(frame, (cx, cy), 70, (0, 255, 128), 6)
    spans = pills.grid_row_spans(frame)
    assert spans[0] == (1000, 1080) and len(spans) == 3
    assert inventory.at_top(frame)                                         # a gap under the bar
    assert pills.grid_rows(frame) == [1198, 1400]
    behind_bar = frame.copy()
    for cx in (126, 329, 533, 736, 939):
        cv2.circle(behind_bar, (cx, 2244), 70, (0, 255, 128), 6)          # half under "All Types"
    assert pills.grid_rows(behind_bar) == [1198, 1400]                     # not a row to read
    scrolled = np.zeros((2560, 1080, 3), np.uint8)
    cv2.rectangle(scrolled, (0, 1000), (1080, 1079), (200, 80, 120), -1)
    for cx in (126, 329, 533, 736, 939):                                     # a cut-off row touching it
        cv2.circle(scrolled, (cx, 1120), 70, (0, 255, 128), 6)
    assert not inventory.at_top(scrolled)


def test_fling_touches_tiles_not_the_tab_bar(monkeypatch):
    from interactions import inventory
    swipes = []
    monkeypatch.setattr(inventory.act, "swipe",
                        lambda x0, y0, x1, y1, ms, reason="": swipes.append((y0, y1)))
    monkeypatch.setattr(inventory.time, "sleep", lambda s: None)
    inventory._fling(*inventory.GRID_BAND)
    inventory._fling(*inventory.GRID_BAND[::-1])
    lo, hi = inventory.GRID_TOUCH
    assert swipes == [(lo, hi), (hi, lo)]
    assert lo > 1080                    # below the Inventory / Merge tab bar


def test_park_top_flings_until_the_gap_shows_and_refuses_otherwise(monkeypatch):
    from interactions import inventory
    seen = iter([False, False, True])
    flings = []
    monkeypatch.setattr(inventory, "settle", lambda *a, **k: None)
    monkeypatch.setattr(inventory, "at_top", lambda frame=None: next(seen))
    monkeypatch.setattr(inventory, "_fling", lambda y0, y1, ms=180: flings.append((y0, y1)))
    inventory.park_top()
    assert len(flings) == 2 and all(y0 < y1 for y0, y1 in flings)         # drags downward = scrolls up
    monkeypatch.setattr(inventory, "at_top", lambda frame=None: False)
    with pytest.raises(RuntimeError):
        inventory.park_top(tries=2)


def test_walk_grid_stop_flag_raises_stopped_instead_of_finishing(tmp_path, monkeypatch):
    from interactions import inventory
    from player import calibrate
    monkeypatch.setattr(inventory, "_close_panel", lambda tries=4: True)
    monkeypatch.setattr(inventory, "park_top", lambda tries=5: None)
    import device.capture as cap
    monkeypatch.setattr(cap, "grab", lambda *a, **k: np.zeros((2560, 1080, 3), np.uint8))
    p = {"state": "", "stop": str(tmp_path / "stop"), "report": "", "evidence": str(tmp_path)}
    (tmp_path / "stop").write_text("")
    cal = calibrate.Calibration(p, overwrite=False)
    with pytest.raises(calibrate.Stopped):
        calibrate._walk_grid(cal)


def _tile_frame(shift=0, seed=1):
    """Rows of five saturated random tiles (distinct rows), scrolled up by
    `shift` px: what a settled grid frame looks like to the projection."""
    rng = np.random.default_rng(seed)
    canvas = np.zeros((2560 + 2000, 1080, 3), np.uint8)
    for row in range(14):
        cy = 1198 + row * 203
        for cx in (126, 329, 533, 736, 939):
            tile = rng.integers(0, 256, (150, 150, 3), dtype=np.uint8)
            tile[..., 0] = 255                       # blue-saturated: lit to the projection
            tile[..., 1] //= 3
            canvas[cy - 75:cy + 75, cx - 75:cx + 75] = tile
    frame = canvas[shift:shift + 2560].copy()
    frame[2260:] = 0
    return frame


def test_scroll_delta_measures_the_move_and_refuses_to_guess():
    from interactions import inventory
    prev = _tile_frame(0)
    assert inventory.scroll_delta(prev, prev) == 0                         # did not move: the end
    assert inventory.scroll_delta(prev, _tile_frame(794)) == 794           # the measured page step
    assert inventory.scroll_delta(prev, _tile_frame(1900)) is None         # past a page: no overlap
    assert inventory.scroll_delta(prev, _tile_frame(0, seed=7)) is None    # another grid entirely


def test_walk_grid_counts_each_physical_tile_once_across_overlapping_pages(tmp_path, monkeypatch):
    """Pages overlap by two rows (the drag is measured, not assumed); a tile
    seen on both pages is one tile, its icon's copies are counted where they
    stand, and the walk stops when the grid stops moving."""
    from interactions import inventory
    from player import calibrate
    monkeypatch.setattr(inventory, "_close_panel", lambda tries=4: True)
    monkeypatch.setattr(inventory, "park_top", lambda tries=5: None)
    pages = [_tile_frame(0), _tile_frame(794), _tile_frame(794)]
    moves = iter([794, 0])
    frames = iter(pages)
    monkeypatch.setattr(inventory, "settle", lambda *a, **k: next(frames))
    monkeypatch.setattr(inventory, "next_page", lambda: next(moves))
    names = {}
    def inspect(cx, cy):
        return f"Module {names.setdefault((cx, cy), len(names) + 1)}", "rare"
    monkeypatch.setattr(calibrate, "_inspect", inspect)
    monkeypatch.setattr(calibrate, "module_slug", lambda name: name.lower().replace(" ", "_"))
    cut = []

    def fake_cut(self, phase, rel, crop, frame, name, extra=None):
        cut.append({"rel": rel, **(extra or {})})
        return cut[-1]
    monkeypatch.setattr(calibrate.Calibration, "cut", fake_cut)
    import runtime.logger as lg
    events = []
    monkeypatch.setattr(lg, "event", lambda kind, **kw: events.append((kind, kw)))
    p = {"state": "", "stop": str(tmp_path / "stop"), "report": "", "evidence": str(tmp_path)}
    cal = calibrate.Calibration(p, overwrite=False)
    slugs, copies = calibrate._walk_grid(cal)
    # page 0 shows rows 0-4 whole (row 5 is behind the filter bar: 25 tiles);
    # page 1 (794 px on) shows rows 4-8 whole - row 4 repeats, rows 5-8 are new
    assert len(copies) == 45 and len(slugs) == 45 and len(cut) == 45
    assert "copies_on_page" not in cut[0]                     # every tile its own module here
    pages_logged = [kw for k, kw in events if k == "calibrate_grid_page"]
    assert [pg["tiles"] for pg in pages_logged] == [25, 45]
    assert [pg["moved"] for pg in pages_logged] == [794, 0]


def test_existing_template_reports_the_fresh_cuts_score(tmp_path, monkeypatch):
    """An `exists` entry scores the file on disk (what the runs use) and
    carries the fresh cut's score beside it, so the report can say that
    Overwrite would fix a template cut from the wrong copy."""
    from player import calibrate
    from vision import pills
    monkeypatch.setattr(calibrate.settings, "ROOT", tmp_path)
    import runtime.logger as lg
    monkeypatch.setattr(lg, "event", lambda kind, **kw: None)
    frame, rects = _pill_frame(["Farm Deck"], active=0)
    p = {"state": "", "stop": str(tmp_path / "stop"), "report": "", "evidence": str(tmp_path)}
    cal = calibrate.Calibration(p, overwrite=False)
    crop, _ = pills.text_crop(frame, rects[0])
    first = cal.cut("cards", "cards/preset_farm_deck.png", crop, frame, "Farm Deck")
    assert first["status"] == "written" and "fresh" not in first
    other, orects = _pill_frame(["Tourney"], active=0)          # a different label, same file name
    ocrop, _ = pills.text_crop(other, orects[0])
    again = cal.cut("cards", "cards/preset_farm_deck.png", ocrop, other, "Tourney")
    assert again["status"] == "exists" and again["fresh"] > 0.95 and again["self"] < again["fresh"]
    cal.overwrite = True
    assert cal.cut("cards", "cards/preset_farm_deck.png", ocrop, other, "Tourney")["status"] == "written"
