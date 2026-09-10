# Manifest-driven setup

The client workflow is Connect → open Home → Remap. The client extracts
artwork from the installed game, verifies the declared Home blocks, follows
the screen routes, and saves rendered recognition images locally. No game
images are shipped. Home must pass its declared block checks before any
navigation begins. This is coverage of declared blocks, not a claim that every
visible part of Home has already been described in the manifest.

`backend/player/bootstrap_manifest.json` is the shared reference: screen
anchors, target rectangles, source artwork names and navigation routes.
An artwork binding's `reference_rect` is tried first; its declared `search`
region remains a fallback. A second frame must confirm the match before a
capture is accepted. Text/composed controls use the manifest's visual checks
when the source sprite alone does not describe their rendered appearance.

Runtime header detection now reads the same manifest rectangles. Other
runtime search regions still need migration; the entire runtime is not yet
manifest-driven. Home's declared blocks also need expansion to cover all
navigation controls. Missing and locked features must not be silently counted
as mapped.

## Developer and community mapping

Run Remap on a supported account. For an unlocked screen that is not in the
route map, open it manually and use **Analyze this screen**. Then use
**Export text-only manifest contribution** in Advanced developer tools.
The export contains known verified target positions and, where available,
unambiguous installed-artwork matches from the latest screen analysis.
Unknown screens are exported as unclassified candidates for developer review.

The exporter whitelists fields; it excludes screenshots, OCR text, local
paths, player names, equipment, inventories, account IDs and image hashes.
Original artwork names are checked against the extracted asset index.
Single-frame discoveries remain candidates, never verified navigation rules.
The export is downloaded locally. Nothing is uploaded or submitted automatically.

Reviewers must name new screens, add anchors and route semantics, and verify
them on two frames before promoting them. Clients do not load arbitrary
contribution files or execute submitted click routes.

A developer can prepare a contribution with `player/manifest_contribution.py`
using `--report`, `--manifest` and `--output`. `--promote-reference` writes an
updated manifest from already-declared, verified identities; it does not add
new executable routes. Review the diff before committing it.

## Experimental action tester

Advanced developer tools also contain a bounded client-driven action tester.
Preview sends no taps. Watch requests checks while the page stays visible,
then requests at most one click. Execution rechecks two frames, the screen,
and the target position. It never retries an uncertain click. Stopping the
browser loop prevents future requests; an already-submitted click may finish.
This is a developer verification tool, not the normal setup workflow.
