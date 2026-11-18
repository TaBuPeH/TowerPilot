## Recommended: Windows portable app

Download **TowerPilot-0.6-beta-windows-x64.zip** below. Extract the whole ZIP into
a writable folder and double-click **Start Tower Pilot.cmd**. Your browser opens
Setup. Python and dependencies are included—no Python installation or terminal
commands are required. No console window opens: Tower Pilot sits in the
notification area near the clock. Click that icon to reopen the dashboard,
right-click it to quit.

## Source distribution / developers

Download **TowerPilot-0.6-beta-source.zip** below, or clone the repository and
check out `v0.6-beta`. Install Python 3.12, then double-click **Start Tower
Pilot.cmd**; the source launcher creates its environment and installs dependencies.
Alternatively run `py -3.12 -m pip install -r requirements.txt`, then
`py -3.12 frontend/dashboard.py` and open http://127.0.0.1:8620/ui/index.html#setup.

GitHub's automatically generated “Source code” downloads are also source-only;
choose **windows-x64.zip** if you do not want to install Python.

## What changed in 0.6-beta

- **Leaving a menu no longer touches the battle screen.** Since game 29.2 a
  touched Ultimate Weapon card opens its info popup; a late second "return to
  game" tap used to land there and leave the popup over the battle. The
  return strip is now tapped only where it is seen, never twice in a row.
- **The free-gems Store visit stops once the day is spent**, instead of
  reopening the Store every five minutes when the card is already on cooldown.
- **The app follows the game to its own emulator display** when another app
  tab (a Play Store page) holds the display the game used to be on.
- **A sighted fleet is nuked on sight**, found by shape and colour.
- **The orbiting-diamond taps must prove the battlefield first**: battle
  screen, no other app in front, and a moving field.
- **"Finish current run, then stop" is removed**; its flag could stop the next
  day's farm. Stop now never touches the game.
- **The daily shatter chore identifies the Shatter tab by its text.**
- Details and the beta limits: `docs/RELEASE_0.6_BETA.md`.

## First setup

1. Use Windows 10/11 x64. Install MuMu or BlueStacks, install The Tower, and sign in.
2. Recommended game frame: **1080 × 2560 portrait, 360 DPI**. For BlueStacks use
   a 2560 × 1080 panel; the game rotates it to portrait.
3. In Setup, install connection tools (ADB) and connect your emulator. This download
   needs internet access and acceptance of the Android SDK terms.
4. End existing battles and leave the game on Home. Run Remap and leave the emulator
   untouched. Allow **20–30 minutes**, plus installation/download time.
5. Open Control, choose Coin farming, set your farming tier and equipment, prepare
   any missing controls, then Start when Ready.

Farm, Tournament and Shard recipes and text manifests are included. Game artwork
is extracted and verified locally; no game images or personal account data ship.

## Beta scope

Tower Pilot's code and shipped configuration/manifest files use the **MIT
license**. You may extend and redistribute them under its terms. Bundled
dependencies retain their own licenses; game artwork is not covered.

- Native-resolution MuMu coin farming has live validation. BlueStacks has needed
  scan retries and targeted repairs. Other resolution/DPI profiles are experimental.
- The portable dashboard and worker commands were tested with system Python absent
  from PATH. A pristine Windows-machine full scan remains to be validated.
- Optional/locked controls can remain unverified. Ready does not mean every emergency
  ability has been exercised. Please report the failing step and emulator/display.
- Stop now stops automation, not the live battle. Stop automation before manual use.
- Keep your existing app folder/account data when upgrading; don't replace local
  configuration with someone else's settings.

Local validation: **1,295 Python tests passed, 3 skipped; 25 frontend tests passed**.
The release workflow checks package boundaries and the dashboard before building.
SHA-256 sidecars accompany both ZIPs; each ZIP also contains per-file checksums.
