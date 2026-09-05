"""Coin farming - the default, run-forever money loop.

This flow has NO script of its own: the generic engine (orchestrator.py)
drives it, and everything that makes a coin run a coin run - tier, loadout,
shopping sweeps, ultimate-weapon choreography, rescue policy - lives in the
preset/blueprint the engine reads. The FLOW spec below is what tells the
scheduler and the compiler that.

Runnable directly all the same: `python flows/coin.py --instance <key>`
starts the engine on the coin preset, so "run the coin flow" works the same
way for every flow file in this folder.
"""

FLOW = {
    "kind": "coin",
    "label": "Coin farming",
    # None = the orchestrator engine runs the compiled preset directly;
    # there is no separate process to spawn for this kind.
    "runner": None,
    # The scheduler equips the blueprint's loadout and sets its tier before
    # handing the engine a battle - the engine itself never equips anything.
    "handoff": "loadout",
    # The constant-era preset this flow ran under before profiles existed.
    "legacy_preset": "normal_run",
}


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import argparse
    import settings
    import orchestrator

    ap = argparse.ArgumentParser(description="Coin farming flow (delegates "
                                             "to the orchestrator engine).")
    ap.add_argument("--instance", help="instance key from config.yaml "
                                       "(default: active_instance)")
    ap.add_argument("--preset", default=FLOW["legacy_preset"],
                    help="preset key (default: %(default)s)")
    args = ap.parse_args()
    settings.select_instance(
        args.instance or settings.CONFIG["active_instance"], args.preset)
    orchestrator.main()
