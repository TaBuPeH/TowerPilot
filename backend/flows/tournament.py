"""Tournament - one paid entry, played to the death, never cancelled.

Like coin, this flow is driven by the generic engine (orchestrator.py); the
tournament-specific behaviour (entry via tourney.setup(), gem cap, its own
ability policy) lives in the preset/blueprint. The scheduler's handoff for
this kind only clears the way Home - tourney.setup() owns all equipping and
the entry taps itself.

HARD RULE: a tournament run is never cancelled once entered - the ticket
escalates in gem cost. The guards live in tourney.end_round / ensure_home.
"""

FLOW = {
    "templates": ['buttons/retry.png',
     'home/game_stats_home.png',
     'home/battle_btn.png',
     'buttons/return_to_game.png',
     'tourney/header.png',
     'tourney/trophy_tile.png',
     'tourney/tournament_stats_ok.png',
     ('tourney/battle_btn.png',
      'tourney/battle_btn_preset.png',
      'tourney/battle_btn_preset_tryagain.png',
      'tourney/battle_btn_rerun.png')],
    "kind": "tournament",
    "label": "Tournament",
    # None = the orchestrator engine runs the compiled preset directly.
    "runner": None,
    # tourney.setup() does its own equipping and entry; the scheduler only
    # walks the game Home first.
    "handoff": "home_only",
    "legacy_preset": "tournament",
}


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import argparse
    import settings
    import orchestrator

    ap = argparse.ArgumentParser(description="Tournament flow (delegates to "
                                             "the orchestrator engine).")
    ap.add_argument("--instance", help="instance key from config.yaml "
                                       "(default: active_instance)")
    ap.add_argument("--preset", default=FLOW["legacy_preset"],
                    help="preset key (default: %(default)s)")
    args = ap.parse_args()
    settings.select_instance(
        args.instance or settings.CONFIG["active_instance"], args.preset)
    orchestrator.main()
