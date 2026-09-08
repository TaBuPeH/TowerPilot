"""Describe calibration evidence without confusing module copies with identity."""
from copy import deepcopy


def describe_report(report):
    """A different-rarity inventory copy cannot invalidate an equipped copy.

    Keep it unverified: absence is neither evidence of damage nor a successful
    template check. This also corrects reports from older running workers.
    """
    report = deepcopy(report)
    entries = report.get("entries", [])
    equipped = set((report.get("player") or {}).get("modules_equipped") or [])
    header = {e["rel"]: e for e in entries if e.get("rel", "").startswith("modules/equipped/")}
    for entry in entries:
        rel = entry.get("rel", "")
        if entry.get("status") != "stale" or not rel.startswith("modules/") or rel.count("/") != 1:
            continue
        slug = rel[len("modules/"):-len(".png")]
        other = header.get(f"modules/equipped/{slug}.png", {})
        if (slug in equipped and other.get("verified") and entry.get("rarity")
                and other.get("rarity") and entry["rarity"] != other["rarity"]):
            entry["status"] = "unverified_copy"
            entry["verified"] = False
            entry["reason"] = (
                f"A {other['rarity']} copy is equipped; the inventory shows a {entry['rarity']} copy. "
                "The saved inventory image could not be verified against the same appearance. "
                "Keep it until that copy is visible in inventory; do not replace it merely because a different copy is visible.")
    return report


def owned_uws(entries) -> dict:
    """Ultimate Weapon ownership as the calibration evidence proves it.

    The in-run UW panel lists ONLY the weapons the account owns, and the
    consented battle pass cuts each weapon's name label from that panel
    (`uw/<name>.png`, verified self-match). A verified label therefore IS
    proof of ownership - the same fact `player.uws` records and the compiler
    gates on (a Chain Lightning choreography bound to an account without
    Chain Lightning is refused at the blueprint). Only positives are returned:
    a weapon with no label may simply have scrolled out of the panel frame,
    so nothing here ever marks one unowned.
    """
    out = {}
    for entry in entries or []:
        rel = str(entry.get("rel") or "")
        if rel.startswith("uw/") and rel.endswith(".png") and entry.get("verified"):
            out[rel[len("uw/"):-len(".png")]] = True
    return out
