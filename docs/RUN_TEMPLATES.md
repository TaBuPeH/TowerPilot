# Shipped run templates
> **Status:** Active
> **Type:** User guide
> **Updated:** 2026-10-22

Farm, Tournament, Shard farming and Dissonance are available above the run list in Control. Choose a template, name it, choose your tier and equipment, then add it. Advanced settings are expanded. Adding a run does not start it or schedule it.

The text-only library is `backend/player/run_templates.json`. `run_templates.py` copies a recipe into the existing profile blueprint schema, with independent policy names for each run. Existing runs, schedules, equipment and account observations are preserved. The starter remains read-only; create a personal profile through Setup first.

## Why a run could not be added

A page that adds a run sends the profile revision it read (`expected_revision`) and the emulator, account and profile it was composed for (`expected_context`). An add that carries either guard gets a stable `code` with every refusal that is known to have happened before anything was written, so the page can tell "refused, nothing changed" from "unknown, read the profile":

| `code` | Status | Meaning |
| --- | --- | --- |
| `revision_conflict` | 409 | The profile changed since the page read it. The answer carries the current `revision`. |
| `context_changed` | 409 | Another emulator, account or profile is in force. The answer carries `current_context`. |
| `invalid_request` | 400 | The guard itself is malformed. |
| `profile_unavailable` | 400 or 409 | No such profile, an unreadable or non-mapping file, a name that is not a profile name (400); the starter and a scan draft (409). |
| `id_taken` | 400 | The run identifier, or a policy that identifier would create, exists already. |
| `recipe_refused` | 400 | Not an identifier, an unknown template, or an unsupported or malformed choice. |
| `validation_failed` | 400 | The profile validator refused the result; `problems` lists why. |
| `write_failed_nothing_saved` | 400 | The write failed and the profile file is byte-identical to what it was before. |

A failure after which the profile differs, or cannot be read back, carries no code: the add may have happened, and a read of the profile settles it. A request without a guard is answered as it always was, with no code.

## Legacy behaviour retained

| Template | Behaviour |
| --- | --- |
| Farm | Coin runs, restart through Home, upgrade shopping every 90 seconds, reward collection. Optional legacy Chain Lightning timing: fleet-mark windows, off after 53–72 waves, pre-mark 5–25 waves, permanently on after wave 4080–4120. Optional Second Wind / wall rescue and fleet-window Nuke rules. |
| Tournament | One entry, shopping and reward collection; optional Chain Lightning activation at wave 500–550 and tournament wall rescue. The tournament runner never deliberately surrenders. |
| Shard farming | Existing Shard runner: cancel Intro Sprint at wave 100, Nuke at wave 101 with wave progress 0.10, then surrender the owned run and retry. Default batch: 100 runs. |
| Dissonance | Farm-style coin runs entered through the **Dissonant Run** dialog with one workshop tab disabled (default Utility), for the permanent Dissonant Boost. Its own `dissonance` loadout (cards, modules, guardians, bots and a workshop preset), **no upgrade shopping**, optional "every owned Ultimate Weapon on" (Chain Lightning always on) and optional tournament-style wall rescue. Restarts through Home and the same dialog. Shopping on the disabled tab is compiled out; disabling Ultimate Weapons also compiles the weapon toggles out. |

The legacy `normal_run`, `tournament` and `shard_farm` configurations provided the recipes. Existing profile compilation and runner implementations execute them; this is not a second execution engine.

## Account-specific choices

All templates ship at tier 1. Choose an appropriate unlocked tier; the scan's highest observed tier is advisory, not an artificial account limit. Farm initially keeps current equipment. Tournament and Shard use the local `tourney_1` and `shard_farm` loadouts; configure those with this account's equipment. Shard farming needs Intro Sprint, Nuke and the appropriate deck before use.

Weapon timing and wall rescue are offered but initially unbound because they depend on the account. Enabling them attaches the legacy policy; normal validation, capability warnings and launch recognition checks still apply. The library does not claim that the account owns any weapon or ability.

Tournament entry defaults to a zero-gem purchase cap, instead of the legacy 10-gem cap. Existing tickets and free ad entries remain available. A user can explicitly raise the cap. Tournament-only screens are mapped when available; the in-battle recognition is shared with ordinary runs.

No game artwork, personal preset names, learned coordinates or account snapshots are shipped with the recipes. Each user's locally captured recognition data remains separate.

## Diamond collection

Ad Gems and free Store gems are separate switches: `ad_gems` watches the six-gem CLAIM button on the battle HUD, while `free_store_gems` runs the Store visit. Store collection is off in the shipped recipes until selected. The HUD collector rechecks a fresh frame before tapping and confirms disappearance only while the battle remains visible. A dry run or a screen change is not a confirmed claim. It never opens the Store. A saved CLAIM image is optional: without one, the collector requires the CLAIM label and the six-gem amount on the same offer.

Rotating diamonds use the existing `gem_orbit` timed-click collector. Shipped recipes disable floating-image matching and enable timed orbit clicks. Account geometry can override the reference point through `center`, `radius`, `angle_deg`, `interval_sec`, `taps`, `jitter_frac`, and `spread_deg` in the gathering policy. No rotating-diamond PNG is required. Before a tap set, the collector checks that the game is in front and the arc is moving rather than covered by a static panel. Each tap also checks a fresh battle frame, and a screen change stops the remaining taps. Ad-gem claims share the foreground-window check. A `gem_orbit_tap` event proves an attempted tap, not a collected reward; verify a balance increase when tuning the orbit for an account.

Overview's Live control shows whether Ad Gems and floating gems are enabled for the selected run. **Start** uses both settings during farming. **Collect diamonds only** uses the same collection sequence on an existing battle without starting a battle or changing equipment. It requires learned wave digits, waits while the battle HUD is absent or a recognized dialog is open, and appears as **Diamond collector** in Live control. **Stop now** stops the driver without touching the game. **Finish current run, then stop** exits after an observed battle reaches its results or Home screen. Starting another automation requires stopping this collector first.

The orbit `center` is measured with the upgrade panel **open**. Closing that panel moves the tower down by half the HUD's layout shift; the collector follows that shift automatically and abandons a tap set if the layout changes while it is checking or tapping.

Ad-gem offers wait for a random delay from the gathering policy's `gem_delay_sec` range (default 3–10 seconds). Waiting never blocks farming; the next claim attempt re-verifies the live battle and offer. The policy's `ad_gem_options` controls polling, confirmation/retry cooldowns and button inset through [Settings](SETTINGS.md). Ad-gem taps sample across the freshly verified button rectangle, preserving the configured inset after shared position jitter and press drift. A rectangle too small for that margin is skipped. Press timing still uses the shared `tap` settings; other controls retain their existing position jitter. These are timing characteristics, not evidence that automation avoids telemetry detection or account flags.

## Dissonance and perk bans

A dissonance run is a coin run whose blueprint names `dissonant_tab` (`attack`,
`defense`, `utility` or `ultimate_weapons`). Every entry point - a person's
Start, the restart after a death and the day plan's handoff - enters it through
`flows/shard.enter_run`, which opens the game's Dissonant Run dialog, verifies
the red X sits on the wanted tab and taps the dialog's own BATTLE. Readiness
lists the dialog images for that tab (header, BATTLE, close, the tab's label);
capture them in Calibrate step 8 with the dialog open. They block only when the
run restarts via Home (the runner enters runs itself); a run you start by hand
that the runner adopts, with restart via Home off, plays without them.

`perk_bans` (coin and tournament run types) is the list of perks to keep
banned while that run type plays, as fragments of the perk's own wording:
`coins, but tower max health`. Numbers are ignored when matching because they
change with lab levels. Before a run is entered the app opens Home → Perks →
Ban Perks, reads the rows by OCR, taps only rows whose text it read, verifies
each toggle and the final banned section, and closes the dialog; any surprise
aborts with a `perk_bans_failed` event and screenshot and the run starts
anyway. The applied set is remembered per account, so the dialog is opened
again only when a run type wants a different list. `null` (the checkbox off)
never opens the dialog; an empty list clears every ban. The Perks screens are
mapped by Prepare recognition (`mapping/.../route_*.png` for "Perk settings",
"Banned perks" and "Perk settings close").

