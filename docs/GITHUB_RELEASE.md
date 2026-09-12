## Recommended: Windows portable app

Download **TowerPilot-0.3-beta-windows-x64.zip** below. Extract the whole ZIP into
a writable folder and double-click **Start Tower Pilot.cmd**. Your browser opens
Setup. Python and dependencies are included—no Python installation or terminal
commands are required. Keep the launcher window open while using the app.

## Source distribution / developers

Download **TowerPilot-0.3-beta-source.zip** below, or clone the repository and
check out `v0.3-beta`. Install Python 3.12, then double-click **Start Tower
Pilot.cmd**; the source launcher creates its environment and installs dependencies.
Alternatively run `py -3.12 -m pip install -r requirements.txt`, then
`py -3.12 frontend/dashboard.py` and open http://127.0.0.1:8620/ui/index.html#setup.

GitHub's automatically generated “Source code” downloads are also source-only;
choose **windows-x64.zip** if you do not want to install Python.

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

Local validation: **1,161 Python tests passed, 3 skipped; 25 frontend tests passed**.
The release workflow checks package boundaries and the dashboard before building.
SHA-256 sidecars accompany both ZIPs; each ZIP also contains per-file checksums.
