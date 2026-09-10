# Shipped run templates

Farm, Tournament and Shard farming are available above the run list in Control. Choose a template, name it, choose your tier and equipment, then add it. Advanced settings are expanded. Adding a run does not start it or schedule it.

The text-only library is `backend/player/run_templates.json`. `run_templates.py` copies a recipe into the existing profile blueprint schema, with independent policy names for each run. Existing runs, schedules, equipment and account observations are preserved. The starter remains read-only; create a personal profile through Setup first.

## Legacy behaviour retained

| Template | Behaviour |
| --- | --- |
| Farm | Coin runs, restart through Home, upgrade shopping every 90 seconds, reward collection. Optional legacy Chain Lightning timing: fleet-mark windows, off after 53–72 waves, pre-mark 5–25 waves, permanently on after wave 4080–4120. Optional Second Wind / wall rescue and fleet-window Nuke rules. |
| Tournament | One entry, shopping and reward collection; optional Chain Lightning activation at wave 500–550 and tournament wall rescue. The tournament runner never deliberately surrenders. |
| Shard farming | Existing Shard runner: cancel Intro Sprint at wave 100, Nuke at wave 101 with wave progress 0.10, then surrender the owned run and retry. Default batch: 100 runs. |

The legacy `normal_run`, `tournament` and `shard_farm` configurations provided the recipes. Existing profile compilation and runner implementations execute them; this is not a second execution engine.

## Account-specific choices

All templates ship at tier 1. Choose an appropriate unlocked tier; the scan's highest observed tier is advisory, not an artificial account limit. Farm initially keeps current equipment. Tournament and Shard use the local `tourney_1` and `shard_farm` loadouts; configure those with this account's equipment. Shard farming needs Intro Sprint, Nuke and the appropriate deck before use.

Weapon timing and wall rescue are offered but initially unbound because they depend on the account. Enabling them attaches the legacy policy; normal validation, capability warnings and launch recognition checks still apply. The library does not claim that the account owns any weapon or ability.

Tournament entry defaults to a zero-gem purchase cap, instead of the legacy 10-gem cap. Existing tickets and free ad entries remain available. A user can explicitly raise the cap. Tournament-only screens are mapped when available; the in-battle recognition is shared with ordinary runs.

No game artwork, personal preset names, learned coordinates or account snapshots are shipped with the recipes. Each user's locally captured recognition data remains separate.

## Diamond collection

Ad Gems and free Store gems are separate switches: `ad_gems` watches the six-gem CLAIM button on the battle HUD, while `free_store_gems` runs the Store visit. Store collection is off in the shipped recipes until selected. The HUD collector rechecks a fresh frame before tapping and records whether the offer disappears; it never opens the Store.

Rotating diamonds use the existing `gem_orbit` timed-click collector. Shipped recipes disable floating-image matching and enable timed orbit clicks. Account geometry can override the reference point. MuMu was verified at native point (420, 685), every roughly two seconds: both the Ad Gems claim and rotating diamond disappeared and the gem balance rose from 1398 to 1406 in the live test. The point is derived from center (540,805), radius 170 and angle 135 degrees; local settings preserve it separately from shipped templates. No rotating-diamond PNG is required for this mode.

Control also offers **Collect diamonds only**. This keeps the HUD Ad Gems watcher and the selected run's timed orbit clicks active without starting a battle or changing equipment. It waits while the battle HUD is absent and appears as **Diamond collector** in Runner; **Stop now** stops it. Starting another automation requires stopping this collector first.
