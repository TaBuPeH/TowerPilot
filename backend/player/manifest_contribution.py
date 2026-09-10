"""Export verified screen geometry, never account data or captured pixels.

Only identities already declared in the shipped manifest can be exported
automatically. New screens/routes remain an explicit developer review task.
"""
import copy


def export(manifest, report):
    known = {}
    for target in manifest.get('dynamic_targets', []):
        if target.get('screen'):
            known.setdefault(target['rel'], set()).add(target['screen'])
    for screen, definition in manifest.get("screens", {}).items():
        for target in definition.get("targets", []):
            known.setdefault(target["rel"], set()).add(screen)
    for screen, definition in manifest.get("hud", {}).items():
        if isinstance(definition, dict):
            for target in definition.get("targets", []):
                known.setdefault(target["rel"], set()).add(screen)
    for rel, binding in manifest.get("asset_bindings", {}).items():
        known.setdefault(rel, set()).add(binding["screen"])
    for route in manifest.get("routes", []):
        if route.get("icon", {}).get("rel"):
            known.setdefault(route["icon"]["rel"], set()).add(route["from"])
    targets = {}
    skipped = 0
    layout = manifest["layout"]
    for entry in report.get("entries", []):
        rel = entry.get("rel")
        screens = known.get(rel, set())
        if not entry.get("verified") or not screens or not entry.get("rect"):
            skipped += 1
            continue
        screen = entry.get("screen")
        # Older reports have no screen. Infer only an unambiguous, shipped
        # identity; never guess from an account's display label.
        if screen is None and len(screens) == 1:
            screen = next(iter(screens))
        rect = entry["rect"]
        if (screen not in screens or len(rect) != 4
                or any(type(v) is not int for v in rect)):
            skipped += 1
            continue
        x,y,w,h = rect
        if min(x,y)<0 or min(w,h)<=0 or x+w>layout["width"] or y+h>layout["height"]:
            skipped += 1
            continue
        row = {"target":rel, "screen":screen, "rect":rect,
               "verification":"two_frame_capture" if entry.get("confirmation", 0)>=.95 else "local_template_check"}
        binding = manifest.get("asset_bindings", {}).get(rel)
        if binding and entry.get("source_kind") == "installed_asset":
            row["artwork_names"] = list(binding["names"])
        targets[(screen,rel)] = row
    return {"format":"tower-pilot-manifest-contribution", "version":1,
            "base_manifest_version":manifest["version"], "layout":copy.deepcopy(layout),
            "requires_review":True, "targets":sorted(targets.values(), key=lambda r:(r["screen"],r["target"])),
            "excluded_entries":skipped}


def apply_reference(manifest, contribution):
    """Developer-only promotion into the ONE manifest, never new click routes."""
    result = copy.deepcopy(manifest)
    changed = 0
    for row in contribution["targets"]:
        if row["verification"] != "two_frame_capture":
            continue
        rel, screen, rect = row["target"], row["screen"], row["rect"]
        for target in result.get('dynamic_targets', []):
            if target['rel']==rel and target.get('screen')==screen:
                target['rect']=list(rect)
        for section in ("screens", "hud"):
            definition = result.get(section, {}).get(screen, {})
            for target in definition.get("targets", []):
                if target["rel"] == rel:
                    target["rect"] = list(rect)
        for route in result.get("routes", []):
            if route.get("from") == screen and route.get("icon", {}).get("rel") == rel:
                route["icon"]["rect"] = list(rect)
        binding = result.get("asset_bindings", {}).get(rel)
        if binding and binding["screen"] == screen and row.get("artwork_names"):
            binding["reference_rect"] = list(rect)
            changed += 1
    return result, changed


def discovery_candidates(manifest, analysis, asset_names):
    """Unmapped screens contribute artwork/geometry for review, not routes.

    Identity must occur in the extracted game's index. Never export OCR text.
    Unknown screen names become 'unclassified' until a developer names them.
    """
    if analysis.get('status') != 'done':
        return []
    screen = analysis.get('screen')
    if screen not in manifest.get('screens', {}) and screen not in manifest.get('hud', {}):
        screen = 'unclassified'
    out = []
    for hit in analysis.get('matches', []):
        names = sorted(set(hit.get('names', [])) & set(asset_names))
        rect = hit.get('rect', [])
        if not names or hit.get('ambiguous') or hit.get('inliers', 0)<8 or len(rect)!=4:
            continue
        if any(type(v) is not int for v in rect):
            continue
        x,y,w,h=rect
        if min(x,y)<0 or min(w,h)<=0 or x+w>manifest['layout']['width'] or y+h>manifest['layout']['height']:
            continue
        out.append({'screen':screen,'artwork_names':names,'rect':rect,
                    'verification':'single_frame_candidate','requires_review':True})
    return out


if __name__ == "__main__":
    import argparse
    import json
    from pathlib import Path
    ap = argparse.ArgumentParser(description="Build a text-only contribution from a local mapping report")
    ap.add_argument("--report", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--promote-reference", action="store_true", help="Developer only: output updated manifest instead of contribution")
    args = ap.parse_args()
    m = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    contribution = export(m, report)
    output, count = apply_reference(m, contribution) if args.promote_reference else (contribution, len(contribution["targets"]))
    Path(args.output).write_text(json.dumps(output, indent=2)+"\n", encoding="utf-8")
    print(f"Exported {count} mapping references; no account fields or images included")
