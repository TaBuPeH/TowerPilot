"""Text-only acquisition plan. No bundled pixels and no game input actions."""
import json
from pathlib import Path
from player import accounts

STEPS = {
    "home": ("1. Home and safe navigation", "Open Home yourself. Capture each named control or header while unobscured. Do not press Battle. Open and close the preset picker yourself to capture its header, close control and None row."),
    "cards": ("2. Cards", "Open Cards from Home. Capture the screen header, active label and named card art without changing the deck. Preset names are discovered by the basic scan after navigation is prepared."),
    "modules": ("3. Modules", "Open Modules, then open a module's details. Capture the named header, close and Equip controls without activating them. Primary/Assist prompts are observed only during an explicitly requested equipment change; never unlock, transfer levels or shatter to collect an image."),
    "guild": ("4. Guild and Guardians", "Open Guild, Members and Guardian pages yourself. Capture the named tab, header, chip or empty/equipped marker. Skip unavailable chips; do not buy or equip one for the scan."),
    "events": ("5. Events and missions", "Open Events, Bots and missions yourself. Capture tabs and claim controls when naturally available. Do not spend currency to produce a claim."),
    "store": ("6. Store", "Open the store yourself and visit the named section. Capture headers, free-claim controls and owned labels. For storefont, crop one occurrence of the named digit from the actual store font. Do not buy anything."),
    "battle": ("7. Observe a normal battle", "Full setup can do this for you: it starts a normal run, reads the wave digits, upgrade labels and Ultimate Weapon names, opens the Intro Sprint prompt and cancels it. Otherwise you start or continue a normal battle and the scan only watches. For digits, use the wave counter; for valuefont, use the value display. Capture each literal character separately at native size. Never touch a tournament run."),
    "results": ("8. Observe results and dialogs", "Full setup captures these by surrendering the run it started itself (exit dialog, GAME STATS, reward skip). Manual capture only watches: do not surrender someone else's run, trigger abilities, transfer levels or shatter modules to obtain a missing image."),
    "tournament": ("9. Observe tournament screens", "You control tournament entry and all confirmations. Capture the named entry, ticket, heat, leaderboard or result control only when it is naturally visible. Setup never buys a ticket, watches an entry ad, enters or ends a tournament."),
    "overlays": ("10. Observe overlays", "Wait for this overlay during normal use. Capture only the named close or return control. Setup must not click an unknown ad or overlay."),
}

def step_for(rel):
    folder, name = rel.split("/", 1)
    if folder == "tourney" or "tourney" in name or "tournament" in name:
        return "tournament"
    if folder == "overlays": return "overlays"
    if folder == "dialogs" or any(t in name for t in ("end_round", "exit_battle", "surrender", "intro_sprint_yes", "intro_sprint_dialog", "shatter", "transfer_yes", "welcome_back", "game_stats_home")):
        return "results"
    if folder == "guardian" or "guild" in name: return "guild"
    if any(t in name for t in ("event", "quest", "daily_missions")): return "events"
    if folder == "storefont" or any(t in name for t in ("store", "title_", "premium", "free_gems", "gem_claim", "tile_cart")): return "store"
    if folder == "cards" or name == "hdr_cards.png": return "cards"
    if folder == "modules" or name == "hdr_modules.png": return "modules"
    if folder in ("digits", "valuefont", "uw", "stats", "floaters") or name in ("ad_gems_claim.png", "demon_mode.png", "nuke.png", "perks.png", "more_stats.png", "intro_sprint.png", "game_stats.png", "speed_x5_mask.png", "menu_closed_tile.png", "menu_collapsed.png", "hdr_battle.png"):
        return "battle"
    if name in ("retry.png", "return_to_game.png", "reward_skip.png"): return "results"
    return "home"

def targets():
    rows = json.loads(Path(__file__).with_name("scan_targets.json").read_text(encoding="utf-8"))
    known = {row['rel'] for row in rows}
    # New manifest targets must not disappear from the repair screen merely
    # because the older reference-size catalogue has not acquired a size yet.
    rows.extend({'rel': rel} for rel in sorted(accounts.generic_names() - known))
    return rows

def plan(root, cfg, requirements=()):
    import cv2
    import hashlib
    from player.bootstrap_layout import manifest, writable_targets, scan_steps
    try:
        report = json.loads((accounts.calibration_dir(root,cfg)/"calibrate_report.json").read_text(encoding="utf-8"))
    except (OSError,ValueError):
        report = {}
    evidence = {e["rel"]:e for e in report.get("entries",[]) if e.get("rel")}
    from player import learned
    learned_rows = learned.known({"state": str(accounts.calibration_dir(root,cfg)/"calibrate_state.json")})
    automatic = writable_targets()
    required = {r for row in requirements for r in row.get("alternatives", [])}
    groups = {key: {"id":key, "title":v[0], "instructions":v[1], "mode":"You navigate; capture only", "targets":[]} for key,v in STEPS.items()}
    for item in targets():
        rel = item["rel"]
        path = accounts.template_path(root, cfg, rel)
        image = cv2.imread(str(path)) if path.is_file() else None
        proof = evidence.get(rel,{})
        verified = bool(proof.get('verified')) and image is not None and proof.get("image_sha256") == hashlib.sha256(path.read_bytes()).hexdigest()
        status = "Verified on this emulator" if verified else "Captured; recognition not yet verified" if image is not None else "Missing"
        groups[step_for(rel)]["targets"].append(dict(item, automatic=rel in automatic or rel in learned_rows, label=Path(rel).stem.replace("_", " "),
            required=rel in required, status=status, learned=learned_rows.get(rel)))
    from player.interface_manifest import coverage
    from player.manifest_driver import plan as mapping_plan
    interface = coverage(lambda rel: accounts.template_path(root, cfg, rel), report.get("entries", []))
    return {"mapping":mapping_plan(), "interface":interface, "native_frame":[1080,2560], "bootstrap":{"steps":scan_steps(), "version":manifest()["version"], "screens":list(manifest()["screens"]), "targets":sorted(automatic)}, "steps":list(groups.values()),
        "captured":sum(t["status"] != "Missing" for g in groups.values() for t in g["targets"]),
        "total":sum(len(g["targets"]) for g in groups.values()),
        "note":"All game images are captured locally from your game. Reference sizes are guidance, not scaling instructions. Variants are alternatives; only capture appearances your emulator actually shows. Capturing an image is not proof that recognition works."}

# Navigation may start only once these local images exist. A missing baseline
# never triggers a speculative tap. Human-driven capture has no such dependency.
BASE = ("home/battle_btn.png", "buttons/return_to_game.png")
PHASE_IMAGES = {
    "c": ("screens/hdr_cards.png", "cards/active_label.png"),
    "m": ("modules/buy_module.png", "modules/v29_dialog_close.png"),
    "g": ("presets/picker_icon.png", "presets/select_header.png", "presets/close_x.png"),
    "u": ("home/tile_guild.png", "guardian/tab_guardian.png"),
    "b": ("home/tile_event.png", "buttons/event_bots_tab.png"),
    "w": ("stats/damage.png",),
    "e": ("modules/buy_module.png", "modules/v29_dialog_close.png", "modules/v29_equip_btn.png"),
}

def missing_navigation(root,cfg,phases, *, include_wave=True):
    import cv2
    names = set(BASE)
    for phase in phases: names.update(PHASE_IMAGES.get(phase, ()))
    # Wave recognition distinguishes a battle from menus before navigation.
    if include_wave:
        names.update(f"digits/{d}.png" for d in range(10))
    missing = []
    for rel in sorted(names):
        alternatives = [rel]
        if rel == "home/battle_btn.png": alternatives += ["tourney/battle_btn.png", "tourney/battle_btn_preset.png"]
        if rel == "presets/picker_icon.png": alternatives += ["presets/picker_icon_large.png"]
        if not any((p:=accounts.template_path(root,cfg,r)).is_file() and cv2.imread(str(p)) is not None for r in alternatives): missing.append(rel)
    return missing


def control_gate(root, cfg):
    """Connection setup and completed recognition setup are separate gates."""
    folder = accounts.calibration_dir(root, cfg)
    try:
        state = json.loads((folder / "calibrate_state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    phases = state.get("phases") or {}
    # A scan that FINISHED counts, whether or not it flagged captures for
    # another pass: "needs_attention" is how Full setup ends whenever a screen
    # it cannot produce on demand (a live tournament, the welcome-back resume)
    # was left for the cropper. Locking Control on that hid every runnable
    # run behind screens only one run type needs; per-run readiness
    # (player/readiness.check) is what greys out the runs that truly lack
    # images. A scan still running (or never run) keeps the gate shut.
    scanned = bool(phases) and all(p.get("status") in ("done", "needs_attention")
                                   for p in phases.values())
    pending = (folder / "module_restore.json").exists()
    # Control includes run configuration. Battle-only recognition is checked
    # when starting the individual run, not when opening its settings.
    missing = missing_navigation(root, cfg, [], include_wave=False)
    return {"ready": scanned and not pending and not missing, "scan_complete":scanned,
            "missing":missing, "restore_pending":pending}
