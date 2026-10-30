# Dashboard and configuration
> **Status:** Active
> **Type:** User guide
> **Updated:** 2026-10-23

The **Pages** menu is one flat list of destinations; choosing a destination opens its content directly. Configuration sections appear on their functional page, with no second navigation tier.

## Pages

| Page | Purpose |
| --- | --- |
| Dashboard | Current automation, start/stop controls, live emulator and recent activity |
| Runs | Run definitions, tiers, policy assignments and shopping policies |
| Schedule | Day plans and scheduled run choices |
| Equipment | Equipment presets used by runs |
| Collection | Ad gems, orbit gems, gathering, global rewards and between-run chores |
| Abilities | Global abilities, Ultimate Weapon policies and rescue rules |
| Profiles | Local account profiles and setup progress |
| Emulators | Emulator selection, connection and connection tools |
| Recognition | Screen learning, calibration and recognition inspection |
| Runtime | Observation, tap timing, mission visits, fleet windows and logging |

On **Collection**, choose the **Shared collection policy** used by your run. That selection controls all three policy sections together: Ad Gems, Orbit and Gathering. Check each section's affected runs before saving. **Runs** assigns these shared policies to runs.

---

## Configuration groups

| Group | Controls | Scope |
| --- | --- | --- |
| Runtime: Tap timing | Position variation, delay before each tap and maximum tap rate | Computer |
| Runtime: Logging | Trigger screenshots and performance log interval | Computer |
| Runtime: Observation loop | Capture rate and logging-only dry run | Selected emulator |
| Runtime: Mission visits and fleet windows | Visit delays and fleet hold/post windows | Selected emulator |
| Runtime: Machine defaults | The same loop, mission-visit and fleet-window values as the fallback every emulator uses; an emulator's own override is kept | Computer |
| Emulators: Defaults | Label, default preset and start at the next tray start | Selected emulator |
| Emulators: Selected emulator | Which emulator every page shows, with its account and profile | Computer |
| Runs: Run defaults | Fallback preset used when neither the runner nor the emulator names one | Computer |
| Equipment: Standing tournament deck changes | Card template names dropped and added when a tournament is entered | Account of the selected emulator (the computer when no account is bound) |
| Collection: Global rewards | Shared reward behavior | Account profile |
| Abilities: Global abilities | Demon Mode behavior | Account profile |
| Collection: Gathering | Quest, guild and Store collection | Named gathering policy |
| Collection: Ad Gems | Enabled state, response delay, edge inset, polling and retry intervals | Named gathering policy |
| Collection: Orbit | Geometry, taps per set, retry timing, collection window and repeat interval | Named gathering policy |

---

## Saving and applying changes

Each module has **Save changes** and **Discard**. Drafts survive navigation between pages and shared policies. A policy can affect several runs. Settings edits are saved to the existing local machine configuration or account profile; they do not create a second configuration store.

Saved automation changes take effect when a driver next starts. Saving does not restart the driver or end a battle. Existing running operations retain their pinned configuration. Save or discard staged edits from the run/policy editors before saving a configuration module.

Numeric fields validate type, bounds and ordered ranges. Ad-gem response delays use whole seconds. The button inset cannot be reduced below its safety minimum. Shared definitions and their current defaults live in [gather_settings.py](../backend/player/gather_settings.py); all editable modules are declared in [settings_catalog.py](../backend/control/settings_catalog.py).

The restore values of an equipment loadout (the card preset and the modules a run puts back afterwards) are saved by Equipment's loadout editor and only for a loadout that a run of the active profile uses, because only then can they be checked against the account. For any other loadout, or when no profile is active, the save is refused and nothing is written; leaving the restore values as they are never blocks another edit of the loadout.

A list field (the tournament deck changes) is an ordered list of non-blank names, at most 50 long, saved exactly as typed: nothing is trimmed, reordered or removed. A default preset left empty means the computer's fallback preset.

Choosing another emulator in **Emulators: Selected emulator** changes what every page shows. It starts and stops nothing and saves only the selection: every account record stays exactly as saved, including accounts that store only some of their fields (nothing is filled in, re-sorted or copied from one account into the other).

Collection runs in scheduled windows: Ad Gems every 12 minutes and orbiting gems every 16 minutes, measured from the start of each window. Both windows last at most one minute and stop early on confirmed collection. These defaults follow the user's reported spawn intervals. A missed gem or safety refusal never extends the window; when it ends, collection pauses until the next scheduled window. New collectors start their first window immediately because a gem may already be present. Restarting a driver resets this in-memory schedule.

Orbit collection retries short tap sets at least five seconds apart by default. Only a two-gem HUD increase, stable across two frames after a real tap, counts as a confirmed orbit collection. An unreadable balance or changed screen uses the short backoff within the active window. The Ad Gem response delay is separate from its window interval. Collection requires the existing account-local digit font and a readable native-resolution gem counter; it does not require an image template of the moving gem.

---

## Implementation boundary

The dashboard serves the catalog through `GET /api/settings`. A save submits the module identifier, allowed field values, account/emulator/profile context and revision to `POST /api/settings`. The server checks those against current state, validates the complete patch before writing, and uses the existing backup and persistence functions. A stale revision or changed context is refused instead of overwriting newer settings.

The interface is the shared settings form, [SettingsModuleForm.tsx](../frontend/app/src/features/settings/SettingsModuleForm.tsx) with [settings.css](../frontend/app/src/features/settings/settings.css), used by each page that shows a settings module. Existing specialized run, rescue, shopping, weapon and equipment editors remain responsible for their structured configurations.
