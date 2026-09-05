"""Capability scanner: seed a player profile from what is on the account.

P1 of the multi-user configurability plan (2026-08-18). Composes EXISTING
read paths - tourney nav/find/greenness, loadout card tabs, module grid
scan, shopper UW panel machinery - into a read-only survey that writes
profiles/<instance>.draft.yaml for a human to confirm in the dashboard.

Phases (selectable, resumable):
  g  guardians   HOME: guild > guardian tab; slots + owned chips. NO taps
                 that change equips - slot occupancy is read, never cleared.
  c  cards       HOME: cards screen; which preset tabs exist (templates are
                 per-account: an unmatched tab becomes an evidence crop for
                 the harvest queue, not a silent skip).
  m  modules     HOME: equipped header + inventory grid presence; --deep
                 adds inventory.sweep() contact-sheet evidence.
  b  battle      OPT-IN (--battle): starts a THROWAWAY Tier-1 run with the
                 current loadout untouched, surveys the UW panel (owned /
                 state / unknown rows) and the ability row, then surrenders
                 to Home. A tournament on screen is a hard refusal.

Safety: refuses to start while any runner process is alive; requires the
HOME screen (or --adopt-battle for phase b); stop flag logs/<inst>/scan_stop
is honoured between steps; state in logs/<inst>/scan_state.json makes
--resume (default) skip completed phases. The draft profile is rewritten
after EVERY phase - a killed scan loses nothing.
"""
import argparse
import datetime
import glob
import json
import os
import time

import sys as _sys
from pathlib import Path as _Path
# Runnable as a script from the backend root (`python player/scan.py`):
# put that root on sys.path so package imports resolve.
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import settings


def _paths():
    from settings import CONFIG
    inst = CONFIG.get("active_instance", "main")
    logs = os.path.join("logs", inst)
    os.makedirs(logs, exist_ok=True)
    os.makedirs("profiles", exist_ok=True)
    return {
        "state": os.path.join(logs, "scan_state.json"),
        "stop": os.path.join(logs, "scan_stop"),
        "draft": os.path.join("profiles", f"{inst}.draft.yaml"),
        "evidence": os.path.join(logs, "scan_evidence"),
    }


def _state_load(p) -> dict:
    try:
        with open(p["state"], encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"phases": {}}


def _state_save(p, st) -> None:
    tmp = p["state"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=1)
    os.replace(tmp, p["state"])


def _stop_requested(p) -> bool:
    return os.path.exists(p["stop"])


def _write_draft(p, player: dict) -> None:
    import yaml
    doc = {"player": dict(player, scanned_at=datetime.datetime.now()
                          .isoformat(timespec="seconds"))}
    tmp = p["draft"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False, allow_unicode=True)
    os.replace(tmp, p["draft"])


def _evidence(p, frame, name: str) -> str:
    import cv2
    os.makedirs(p["evidence"], exist_ok=True)
    path = os.path.join(p["evidence"], f"{name}.png")
    cv2.imwrite(path, frame)
    return path


def _tpl_names(subdir: str, prefix: str = "") -> list[str]:
    """Slugs derived from the template library - the scanner's vocabulary
    is exactly what the engine can recognize, nothing invented."""
    out = []
    for f in glob.glob(os.path.join("templates", subdir, f"{prefix}*.png")):
        out.append(os.path.basename(f)[len(prefix):-4])
    return sorted(out)


def _dismiss_tourney_open() -> None:
    """Claim-dismiss the "new tournament is open, free ticket" home popup
    (screens row `tourney_open`). Template-verified before every tap, never
    blind; claiming the FREE ticket only banks it (hard rule 2 untouched -
    entering a tournament is a separate BATTLE flow)."""
    from device import capture
    from interactions import tourney
    for _ in range(2):
        if not tourney.find(capture.grab(), "home/tourney_open_dialog.png"):
            return
        tourney.tap_at((539, 1552), "claim free tourney ticket (dismiss popup)")
        time.sleep(1.5)


# ---------------------------------------------------------------- preflight
def preflight(adopt_battle: bool) -> None:
    from device import capture
    import psutil
    from vision import screen
    from vision import wave_reader
    mine = os.getpid()
    for pr in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cl = " ".join(pr.info["cmdline"] or [])
        except Exception:               # noqa: BLE001
            continue
        if pr.info["pid"] == mine or "python" not in (pr.info["name"] or "").lower():
            continue
        if any(f"{r}.py" in cl for r in ("orchestrator", "shard", "combo", "tourney",
                                         "quest_sm", "quest_ilm", "harness")):
            # P3 CLONE (2026-08-19): two trees may run side by side (main
            # farm in ../autopilot, this clone on its own instance). A
            # runner only conflicts with this scan if it can touch the SAME
            # tree or the SAME device: match by process cwd (each runner is
            # spawned with its tree as working directory). If cwd is
            # unreadable, stay conservative and refuse as before.
            try:
                cwd = os.path.normcase(psutil.Process(pr.info["pid"]).cwd())
            except Exception:               # noqa: BLE001
                cwd = None
            here = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))
            if cwd is not None and cwd != here:
                continue                    # foreign tree, different device
            raise SystemExit(f"REFUSED: runner alive (pid {pr.info['pid']}: "
                             f"{cl.strip()}) - stop it first")
    # Emulator ad overlays (com.mumu.store etc.) sit ABOVE the game and
    # would fail every identify below. Evidence-gated dismissal first;
    # an overlay that cannot be named or killed is a refusal, not a tap.
    from device import overlays
    if not overlays.clean():
        raise SystemExit("REFUSED: an overlay is covering the screen and "
                         "could not be dismissed - see the events log")
    frame = capture.grab()
    from interactions import tourney
    if screen.in_tournament(frame):
        raise SystemExit("REFUSED: tournament run on screen - never scan "
                         "over a tournament")
    sc = screen.identify(frame)
    # Known self-inflicted popups get resolved, not refused: the free-ticket
    # announcement over HOME (tourney_open, claim banks a free ticket - a
    # separate flow from entering, hard rule 2 untouched) and the reward
    # animation the claim leaves behind (ticket_reward, SKIP fast-forwards -
    # same handling as tourney.open_tournament). Both taps are verified by
    # the identify that named the screen; anything ELSE non-home still
    # refuses exactly as before.
    for _ in range(4):
        if sc.name == "tourney_open":
            _dismiss_tourney_open()
        elif sc.name == "ticket_reward":
            skip = tourney.find(frame, "tourney/ticket_skip.png")
            tourney.tap_at(skip[0] if skip else (538, 1966),
                           "ticket skip" if skip else "ticket claim")
            time.sleep(1.2)
        elif sc.name == "game_stats":
            # A stranded stats dialog is a bot flow's leftover (the battle
            # phase itself produces one when its exit dies mid-way). HOME is
            # template-verified; no match -> fall through to the refusal.
            hit = tourney.find(frame, "home/game_stats_home.png")
            if not hit:
                break
            tourney.tap_at(hit[0], "game stats: HOME (scan preflight)")
            time.sleep(1.5)
        else:
            break
        frame = capture.grab()
        sc = screen.identify(frame)
    if sc.name == "battle" or wave_reader.read_wave(frame) is not None:
        if not adopt_battle:
            raise SystemExit("REFUSED: a battle is live and ownership is "
                             "unknown (a human may be playing). Rerun with "
                             "--adopt-battle to scan over it.")
        return
    if sc.name != "home":
        raise SystemExit(f"REFUSED: screen is '{sc.name}', need HOME - "
                         "navigate there and rerun")


# ---------------------------------------------------------------- phases
def phase_guardians(p) -> dict:
    from device import capture
    from runtime import logger
    from interactions import tourney
    frame = capture.grab()
    hit = tourney.find(frame, "home/tile_guild.png")
    if not hit:
        raise tourney.Abort("guild tile not on the left rail")
    tourney.tap_at(hit[0], "scan: open guild")
    frame, _ = tourney.require("guardian/tab_guardian.png", "guild screen")
    tourney.tap_at(tourney.GUARDIAN_TAB, "scan: guardian tab")
    time.sleep(0.6)
    # MULTI-FRAME VOTE (2026-08-18, first live scan): a floating "+9 Defense
    # Ab..." bonus popup drifted across the Scout tile at capture time and
    # knocked its STRICT match from 0.997 to 0.937 - the scan reported 5 of
    # 6 chips. Transient occlusion is normal on this screen; three frames a
    # second apart and the best score per chip wins.
    frames = []
    for _ in range(3):
        frames.append(capture.grab())
        time.sleep(1.0)
    frame = frames[0]
    slots_used = sum(not tourney._slot_empty(frame, s)
                     for s in tourney.GUARDIAN_SLOTS)
    owned, equipped = [], []
    for chip in _tpl_names("guardian", "chip_"):
        best = None
        for f in frames:
            h = tourney.find_robust(f, f"guardian/chip_{chip}.png", tourney.STRICT)
            if h and (best is None or h[1] > best[1]):
                best = (h[0], h[1], f)
        if best is None:
            continue
        owned.append(chip)
        (cx, cy), _, f = best
        if tourney.greenness(f, cx + tourney.CHIP_CHECK_OFFSET[0],
                             cy + tourney.CHIP_CHECK_OFFSET[1]) > tourney.TICK_ON:
            equipped.append(chip)
    ev = _evidence(p, frame, "guardians")
    logger.event("scan_guardians", owned=owned, equipped=equipped,
                 slots_used=slots_used)
    tourney.return_to_game("guild")
    return {"guardians": owned, "guardians_equipped": equipped,
            "guardian_slots_used": slots_used, "guardians_evidence": ev}


def phase_cards(p) -> dict:
    from device import capture
    from interactions import loadout
    from runtime import logger
    from interactions import tourney
    tourney.open_nav("cards", "cards/active_label.png", "cards screen")
    frame = capture.grab()
    found = []
    for preset_name in _tpl_names("cards", "preset_"):
        if tourney.find_robust(frame, f"cards/preset_{preset_name}.png", 0.90):
            found.append(preset_name)
    current = loadout.current_cards(frame)
    # preset tab labels are USER-TYPED text: a preset with no template is
    # invisible to matching. The tab band goes to evidence so the harvest
    # queue can name it - never silently pretend the account has 5 presets.
    band = frame[330:520, 0:1080]
    ev = _evidence(p, band, "card_preset_band")
    logger.event("scan_cards", presets=found, current=current)
    tourney.return_to_game("cards")
    return {"card_presets": found, "cards_current": current,
            "cards_evidence": ev}


def phase_modules(p, deep: bool) -> dict:
    from device import capture
    from interactions import loadout
    from runtime import logger
    from interactions import tourney
    tourney.open_nav("modules", "modules/buy_module.png", "modules screen")
    frame = capture.grab()
    equipped = {}
    for slug in _tpl_names("modules/equipped"):
        hit = tourney._find_in_band(frame, f"modules/equipped/{slug}.png",
                                    *loadout.HEADER_BAND,
                                    loadout.EQUIPPED_THRESH)
        if hit:
            equipped[slug] = True
    grid_rels = [f"modules/{s}.png" for s in _tpl_names("modules")
                 if "/" not in s]
    present = tourney._scan_grid(grid_rels, 1100, 2200)
    inventory_slugs = sorted(os.path.basename(r)[:-4] for r in present)
    ev = _evidence(p, capture.grab(), "modules_screen")
    extra = {}
    if deep:
        from interactions import inventory
        out_dir = os.path.join(p["evidence"], "module_sweep")
        records = inventory.sweep(out_dir)
        inventory.contact_sheet(out_dir, records)
        extra = {"modules_deep_sweep": out_dir, "modules_tiles": len(records)}
    logger.event("scan_modules", equipped=sorted(equipped),
                 grid=inventory_slugs, **extra)
    tourney.return_to_game("modules")
    return {"modules_equipped": sorted(equipped),
            "modules_in_grid": inventory_slugs,
            "modules_evidence": ev, **extra}


def phase_battle(p, adopt: bool) -> dict:
    """Throwaway T1 run (or adopted battle): UW panel + ability row."""
    from device import capture
    from vision import detect
    from runtime import logger
    from flows import shard
    from interactions import shopper
    from vision import wave_reader
    from quest_sm import GRANT_UWS, KNOWN_UWS
    started_here = False
    _dismiss_tourney_open()   # can reappear on any home return - cheap check
    if wave_reader.read_wave(capture.grab()) is None:
        prev_tier = shard.read_tier(capture.grab())
        shard.set_tier(1)
        shard.start_battle()
        shard.wait_for_wave(1)
        started_here = True
    else:
        prev_tier = None
    # ---- UW panel survey (quest_sm.scan_grant's read logic, ownership
    # framing instead of grant verdicts)
    uws: dict[str, dict] = {}
    unknown_rows = 0
    if shopper._tap_tab("uw"):
        shopper._scroll_to_top()
        frames = []
        for _ in range(3):
            frames.append(capture.grab())
            shopper._swipe_panel_down()
            time.sleep(0.5)
        frames.append(capture.grab())
        for name in KNOWN_UWS + GRANT_UWS:
            rel = f"uw/{name}.png"
            if not os.path.exists(os.path.join("templates", rel)):
                uws.setdefault(name, {"owned": None})
                continue
            for i, f in enumerate(frames):
                hit, score, _ = detect._match(f, rel, 0.70)
                if hit:
                    state = shopper._uw_state(f, name)
                    uws[name] = {"owned": True,
                                 "on": state if state is not None else None}
                    break
            else:
                uws[name] = {"owned": False}
        for i, f in enumerate(frames):
            shot = _evidence(p, f, f"uw_panel_{i}")
    # ---- ability row
    frame = capture.grab()
    abilities = {}
    for btn in _tpl_names("buttons"):
        if btn in ("nuke", "demon_mode") or btn.startswith("ability_"):
            st = detect.button_state(frame, btn) if btn in ("nuke", "demon_mode") else None
            if st is not None and st.present:
                abilities[btn] = True
    _evidence(p, capture.roi(frame, "ability_row"), "ability_row")
    logger.event("scan_battle", uws={k: v.get("owned") for k, v in uws.items()},
                 abilities=sorted(abilities), unknown_rows=unknown_rows)
    if started_here:
        shard.abandon_run(to_home=True)
        # RESTORE IS CLEANUP, NOT DATA (learned live 2026-08-19: the very
        # first battle scan captured everything, then set_tier grabbed a
        # frame ~0.5s after the surrender - the home screen was still
        # settling, on_home said no, and the Abort voided a phase whose
        # results were already in hand). Wait for home to be real first,
        # and never let a restore failure discard the survey.
        try:
            from vision import screen as _screen
            for _ in range(10):
                if _screen.identify(capture.grab()).name == "home":
                    break
                time.sleep(1.0)
            if prev_tier is not None and prev_tier != 1:
                shard.set_tier(prev_tier)
        except Exception as e:                  # noqa: BLE001
            logger.event("scan_battle_tier_restore_failed", error=str(e)[:200],
                         wanted=prev_tier)
    return {"uws": uws, "abilities": sorted(abilities),
            "wall": True}       # wall presence: assume true, human corrects


PHASES = {"g": ("guardians", phase_guardians),
          "c": ("cards", phase_cards),
          "m": ("modules", phase_modules),
          "b": ("battle", phase_battle)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--instance", default="main")
    ap.add_argument("--preset", default=None, help="tray/dashboard parity")
    ap.add_argument("--phases", default="g,c,m",
                    help="comma list of g,c,m,b (b needs --battle)")
    ap.add_argument("--battle", action="store_true",
                    help="allow the throwaway Tier-1 battle phase")
    ap.add_argument("--adopt-battle", action="store_true",
                    help="scan over an already-live battle (phase b only)")
    ap.add_argument("--deep", action="store_true",
                    help="module inventory sweep with contact sheet")
    ap.add_argument("--fresh", action="store_true",
                    help="ignore previous scan state, redo all phases")
    a = ap.parse_args()
    settings.select_instance(a.instance, "normal_run")
    from runtime import logger
    p = _paths()
    if os.path.exists(p["stop"]):
        os.remove(p["stop"])
    st = {"phases": {}} if a.fresh else _state_load(p)
    preflight(a.adopt_battle)
    wanted = [s.strip() for s in a.phases.split(",") if s.strip()]
    if a.battle and "b" not in wanted:
        wanted.append("b")
    player: dict = st.get("player", {})
    logger.event("scan", stage="begin", phases=wanted, resume=not a.fresh)
    for key in wanted:
        name, fn = PHASES[key]
        rec = st["phases"].get(name, {})
        if rec.get("status") == "done":
            logger.event("scan", stage="skip_done", phase=name)
            continue
        if _stop_requested(p):
            logger.event("scan", stage="stopped", before=name)
            break
        if key == "b" and not (a.battle or a.adopt_battle):
            st["phases"][name] = {"status": "skipped",
                                  "error": "battle phase not opted in"}
            continue
        st["phases"][name] = {"status": "running",
                              "started": datetime.datetime.now().isoformat()}
        _state_save(p, st)
        try:
            if key == "m":
                result = fn(p, a.deep)
            elif key == "b":
                result = fn(p, a.adopt_battle)
            else:
                result = fn(p)
            player.update(result)
            st["phases"][name] = {"status": "done", "results": result}
        except SystemExit:
            raise
        except Exception as e:              # noqa: BLE001 - phase isolation:
            # one broken phase must not kill the survey; it is recorded and
            # the next phase runs. --resume retries it later.
            from interactions import tourney
            st["phases"][name] = {"status": "error", "error": str(e)[:300]}
            logger.event("scan", stage="phase_error", phase=name,
                         error=str(e)[:300])
            try:
                tourney.ensure_home()
            except Exception:               # noqa: BLE001
                pass
        st["player"] = player
        _state_save(p, st)
        _write_draft(p, player)
    logger.event("scan", stage="done",
                 phases={k: v.get("status") for k, v in st["phases"].items()},
                 draft=p["draft"])
    print(json.dumps({k: v.get("status") for k, v in st["phases"].items()}))


if __name__ == "__main__":
    main()
