"""Explain the calibration required by one compiled run, without device I/O."""
from pathlib import Path
import json

from player import accounts

# Recognition guards every run needs: the menu headers and dialogs a runner
# must tell apart from the battle beneath them, and the wave font that is the
# "tower on screen" proof every tap rail rests on. Full setup captures all of
# these by itself. Missing one BLOCKS the run.
CORE = [
    "buttons/exit_battle.png", "buttons/menu_closed_tile.png",
    "screens/hdr_battle.png", "screens/hdr_cards.png", "screens/hdr_guild.png",
    "screens/hdr_modules.png",
    "icons/game_stats.png", "home/exit_battle_dialog.png", "home/dissonant_run.png",
] + [f"digits/{d}.png" for d in range(10)]

# Recognition guards for screens setup CANNOT produce on demand: a live
# tournament (rule 2 - setup never buys a ticket), the offline-earnings
# resume, a reward animation. vision/screen._match scores a missing template
# 0, so an unrecognised overlay degrades to "unknown" -> hands off, never a
# crash mid-run. They are ADVISORY for every run (listed, not blocking) and
# BLOCKING only for the run kind that drives that screen (tournament) - a
# coin farm must not be greyed out by a tournament nobody has opened yet.
ADVISORY = [
    "home/end_round_dialog.png", "home/intro_sprint_dialog.png",
    "home/tourney_open_dialog.png", "home/welcome_back_dialog.png",
    "home/welcome_back_resume.png", "buttons/reward_skip.png",
    "screens/hdr_tournament.png", "tourney/tournament_stats.png",
    "tourney/buy_ticket_title.png", "tourney/heat_tabs.png",
    "tourney/ticket_claim.png", "tourney/in_tournament.png",
]
TOURNAMENT_GUARDS = {
    "home/tourney_open_dialog.png", "screens/hdr_tournament.png",
    "tourney/tournament_stats.png", "tourney/buy_ticket_title.png",
    "tourney/heat_tabs.png", "tourney/ticket_claim.png", "tourney/in_tournament.png",
}
ADVISORY_REASON = "Recognition guard (optional: capture when the screen appears)"


def slug(name):
    return "".join(c if c.isalnum() else "_" for c in str(name).strip().lower())


# A requirement that is CONFIG, not an image: the wall bar region the wall
# watch reads (`capture.roi(frame, "wall_bar")` raises without it). Rows
# carrying it are rendered as text, never as a crop button.
WALL_BAR_REQUIREMENT = "config: rois.wall_bar"


def wall_bar_roi(cfg):
    """The active instance's wall bar ROI (instance override, else global)."""
    inst = (cfg.get("instances") or {}).get(cfg.get("active_instance", "main")) or {}
    return ((inst.get("rois") or {}).get("wall_bar")
            or (cfg.get("rois") or {}).get("wall_bar"))


def requirements(cfg, body):
    """Groups of alternatives with reasons; only enabled features contribute.
    Each row carries `blocking`: False for the advisory recognition guards a
    run can start without (see ADVISORY)."""
    import flows
    groups = {}

    def need(paths, reason, blocking=True):
        paths = (paths,) if isinstance(paths, str) else tuple(paths)
        row = groups.setdefault(paths, {"reasons": set(), "blocking": False})
        row["reasons"].add(reason)
        row["blocking"] = row["blocking"] or blocking

    kind = body.get("kind") or next((spec["kind"] for spec in flows.flows().values()
                                    if spec.get("runner") and spec["runner"] == body.get("runner")), None)
    kind = kind or ("tournament" if body.get("tournament_setup") else "coin")
    for rel in CORE:
        need(rel, "Screen recognition and safe recovery")
    for rel in ADVISORY:
        if kind == "tournament" and rel in TOURNAMENT_GUARDS:
            need(rel, "Tournament screens")
        else:
            need(rel, ADVISORY_REASON, blocking=False)
    for rel in flows.flow(kind).get("templates", []):
        need(rel, flows.flow(kind)["label"])
    gather = body.get("gather") or {}
    gather = dict(gather)
    global_rewards = body.get('global_behaviors', {})
    if 'free_store_gems' in global_rewards:
        gather['free_store_gems'] = global_rewards['free_store_gems']
    if 'daily_missions' in global_rewards:
        gather['quests_8h'] = global_rewards['daily_missions']
        gather['quest_rewards'] = global_rewards['daily_missions']
    if 'guild_progress' in global_rewards:
        gather['guild'] = global_rewards['guild_progress']
    if global_rewards.get('event_missions'):
        need('icons/event_missions_tab.png', 'Event Missions')
    generic = accounts.generic_names()
    for key, names in {
        "ad_gems": ["buttons/ad_gems_claim.png"],
        "free_store_gems": ["icons/premium_store.png", "icons/free_gems.png"],
        "quests_8h": ["icons/daily_missions.png", "icons/tile_quests.png", "buttons/quest_claim.png", "icons/chest_lock.png"],
        "quest_rewards": ["icons/daily_missions.png", "icons/tile_quests.png", "buttons/quest_claim.png"],
        "guild": ["home/tile_guild.png", "icons/guild_header.png", "buttons/guild_members_tab.png"],
        "guild_store": ["icons/guild_coin.png"],
    }.items():
        if gather.get(key, key not in ("free_store_gems", "guild_store")):
            for rel in names:
                need(rel, key.replace("_", " ").capitalize(), blocking=key != 'ad_gems' and rel != 'buttons/quest_claim.png')
    for directive in body.get("shopping") or []:
        if not directive.get("enabled", True):
            continue
        for stat in directive.get("stats") or []:
            need(f"stats/{stat}.png", "Workshop shopping")
        need("stats/max_label.png", "Workshop shopping")
    for name in (body.get("uw_wanted") or {}):
        need(f"uw/{name}.png", "Ultimate weapon settings")
        need("uw/toggle_on.png", "Ultimate weapon settings")
        need("uw/toggle_off.png", "Ultimate weapon settings")
    # Read compiled rules, not every policy in the library.
    def walk(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k in ("button", "fire") and isinstance(v, str) and v in ("nuke", "demon_mode"):
                    need(f"buttons/{v}.png", "Rescue ability")
                if k == "second_wind" or (k == "on" and v == "second_wind"):
                    need("floaters/second_wind.png", "Rescue timing")
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
    walk(body.get("rules") or [])
    abilities = body.get("abilities") or {}
    for key, button in (("dm_below", "demon_mode"), ("nuke_below", "nuke"), ("nuke_on_fleet", "nuke")):
        if abilities.get(key) is not None:
            need(f"buttons/{button}.png", "Rescue ability")
    if body.get("cancel_sprint") or abilities.get("rescue_bar"):
        need("icons/intro_sprint.png", "Intro sprint detection")
        need("home/intro_sprint_yes.png", "End intro sprint")
    if any(abilities.get(k) is not None for k in ('dm_below', 'nuke_below', 'nuke_on_fleet')) and abilities.get('hold_until_second_wind', True):
        need('floaters/second_wind.png', 'Rescue timing')
    if abilities.get("rescue_bar") == "wall":
        # Not an image: the wall watch reads config rois.wall_bar (per
        # instance) and capture.roi raises without it. Full setup detects the
        # wall bar from the battle HUD and Apply writes the region.
        need(WALL_BAR_REQUIREMENT, "Wall bar region (Full setup detects it from the battle HUD)")
    if body.get("max_wave") or kind in ("shard", "cycle_quest", "uw_grant_quest"):
        for rel in ("home/surrender.png", "buttons/end_round.png", "home/end_round_yes.png"):
            need(rel, "End a run started by this flow")
    if body.get("dissonant_tab"):
        for rel in ("dialogs/dissonant_header.png", "dialogs/dissonant_battle.png", "dialogs/dissonant_x.png",
                    f"dialogs/dissonant_tile_{body['dissonant_tab']}.png"):
            need(rel, "Achievement run")
    lo = (cfg.get("loadouts") or {}).get(body.get("loadout")) or {}
    if lo.get("global_preset"):
        need(f"presets/gp_{slug(lo['global_preset'])}.png", "Equipment preset")
    categories = {"module_preset": "modules", "guardian_preset": "guardians",
                  "workshop_preset": "workshop", "bot_preset": "bots"}
    for key, cat in categories.items():
        if lo.get(key):
            need(f"presets/{cat}_{slug(lo[key])}.png", f"{cat.capitalize()} preset")
    if lo.get("global_preset") or any(lo.get(k) for k in categories):
        need(("presets/picker_icon.png", "presets/picker_icon_large.png"), "Open preset picker")
        for rel in ("presets/close_x.png", "presets/select_header.png", "presets/gp_none.png"):
            need(rel, "Preset picker")
    for key in ("cards", "cards_restore"):
        if lo.get(key):
            need(f"cards/preset_{lo[key]}.png", "Card preset")
            need("cards/active_label.png", "Card preset")
    for key in ("modules", "modules_restore"):
        for entry in lo.get(key) or []:
            name = entry[0] if isinstance(entry, (tuple, list)) else entry
            need(f"modules/{name}.png", "Equipment verification")
            # v29 equips by category from the module dialog (loadout._apply_modules_v29):
            # no PRIMARY/ASSIST buttons in the runtime path, so none are required here.
            for rel in ("modules/buy_module.png", "modules/v29_dialog_close.png", "modules/v29_equip_btn.png",
                        "modules/transfer_yes.png"):
                need(rel, "Equip modules")
    return [{"alternatives": list(paths), "reasons": sorted(row["reasons"]),
             "blocking": row["blocking"]}
            for paths, row in sorted(groups.items())]


def _docs(alternatives):
    """Plain-language cards for a row's images: what each one is, where it
    shows, how to get it, and which scan-plan step it belongs to - the names
    alone (`floaters/second_wind.png`) tell a person nothing (2026-09-08)."""
    from player import scan_plan, template_docs
    out = []
    for rel in alternatives:
        card = template_docs.describe(rel)
        if not rel.startswith("config:"):
            step = scan_plan.step_for(rel)
            title, instructions = scan_plan.STEPS.get(step, (step, ""))
            card["step"] = {"id": step, "title": title, "instructions": instructions}
        out.append(card)
    return out


def check(root, cfg, body):
    """`ready` when every BLOCKING row has a valid, non-stale image. `missing`
    lists the blocking gaps (what greys a run out); `advisory` the optional
    recognition guards still to capture (shown, never blocking)."""
    import cv2
    if (accounts.calibration_dir(root, cfg) / "module_restore.json").exists():
        blocked = {"alternatives": [], "reasons": ["Restore the original modules: return to Home and resume calibration"],
                   "blocking": True, "have": False, "matched_files": []}
        return {"ready": False, "required": [blocked], "missing": [blocked], "advisory": [],
                "calibration_url": "/ui/index.html#calibrate"}
    try:
        report = json.loads((accounts.calibration_dir(root, cfg) / "calibrate_report.json").read_text(encoding="utf-8"))
        from player.calibration_report import describe_report
        stale = {e["rel"] for e in describe_report(report).get("entries", [])
                 if e.get("status") in ("stale", "unverified_copy")}
    except (OSError, ValueError, KeyError):
        stale = set()
    from player import learned
    learned_rows = learned.known({"state": str(accounts.calibration_dir(root, cfg) / "calibrate_state.json")})
    rows = []
    for row in requirements(cfg, body):
        good = []
        for rel in row["alternatives"]:
            if rel == WALL_BAR_REQUIREMENT:
                if wall_bar_roi(cfg):
                    good.append(rel)
                continue
            if rel == 'floaters/second_wind.png':
                from vision.installed_art import _images
                try:
                    if _images(str(accounts.calibration_dir(root,cfg)), rel):
                        good.append(rel)
                        continue
                except (OSError, ValueError, KeyError):
                    pass
            if rel in stale:
                continue
            path = accounts.template_path(root, cfg, rel)
            if path.is_file():
                img = cv2.imread(str(path))
                if img is not None and img.size and img.shape[0] <= 2560 and img.shape[1] <= 1080:
                    good.append(rel)
        rows.append(dict(row, have=bool(good), matched_files=good, docs=_docs(row["alternatives"]),
                         learned={rel: learned_rows[rel] for rel in row["alternatives"] if rel in learned_rows}))
    missing = [r for r in rows if not r["have"] and r["blocking"]]
    advisory = [r for r in rows if not r["have"] and not r["blocking"]]
    result = {"ready": not missing, "required": rows, "missing": missing, "advisory": advisory,
              "calibration_url": "/ui/index.html#calibrate"}
    if missing and all(r['alternatives'] == ['floaters/second_wind.png'] for r in missing):
        result.update(next_action='edit_run', action_label='Choose rescue timing',
                      message='This run waits for Second Wind. Choose rescue timing that does not wait for it, or capture its indicator during normal play. Another setup scan will not help.')
    return result


def require(root, cfg, body):
    result = check(root, cfg, body)
    if not result["ready"]:
        names = [" or ".join(r["alternatives"]) or "; ".join(r["reasons"]) for r in result["missing"]]
        raise RuntimeError("Calibration needed before this run: " + ", ".join(names)
                           + ". Open Calibrate in the dashboard.")
    return result
