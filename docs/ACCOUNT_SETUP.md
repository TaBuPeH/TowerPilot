# Account setup and calibration

1. Open **Setup**, create an account, and connect its emulator. A new
   account starts with one coin run using the equipment already in the game.
   Import existing settings only when they belong to this account; importing
   copies the profile, templates and calibration history and keeps the originals.
2. Set the highest tier you have unlocked. Each run has its own farming tier.
3. Check the display. The captured game frame must be **1080 Ã— 2560 at
   360 dpi**. BlueStacks uses a **2560 Ã— 1080 landscape panel**, which the game
   rotates. MuMu's game may use a secondary display. Arbitrary resolutions
   and DPI scaling are not supported.
4. Open **Calibrate → Scan plan**, capture the navigation images, then choose the run you want to prepare. Its image
   requirements exclude unused equipment and disabled features. Screen
   recognition guards are shared because a dialog can cover any run.
5. Select only unlocked features. Allow menu navigation for this calibration
   session, then **Calibrate selected features**. Leave the emulator alone
   while it navigates. **Resume unfinished steps** skips completed phases.
6. Review the detected names and use **Use discovered presets in my profile**.
   This updates account observations without replacing run settings or schedules.
   Failed and interrupted phases preserve the previous account observations.
7. In **Control**, select equipment and a tier. Use **Check setup** before
   starting. Enable automation's tap permission when ready to let it control
   the emulator. Calibration's temporary permission does not enable runners.

Repeat counts, rescue rules, weapon timing, shopping policies and scheduling
are in expanded Advanced sections. A tournament's entry spending limit remains
visible. Manual image cropping remains available under Advanced repair.

## Storage and compatibility

- Emulator connections stay in `backend/config.yaml`.
- Each `accounts.<id>` entry owns its loadouts and selected profile.
- An emulator instance binds an account with `instances.<instance>.account`.
- Locally captured game images and scan/calibration history live under
  `backend/accounts/<account>/calibration/<instance>-<device fingerprint>/`.
  The fingerprint includes the emulator address and adb executable. Repointing
  an instance at another emulator starts a separate calibration context.
- Profiles remain local YAML files; fresh accounts receive
  `backend/profiles/account_<id>.yaml`. Multiple profiles can belong to one
  account. Scan drafts use the account identifier.
- Unbound legacy installations retain their original paths. No migration is
  performed automatically and importing never deletes the originals.
- Switching accounts is refused while automation is running. It changes the
  bot's configuration; it does **not** log into another game account. Open the
  matching account in the emulator yourself.

All account storage is git-ignored. Runtime template readers share one resolver.
No game images are bundled and there is no shared-image fallback. Every image,
including buttons, digits and headers, is obtained from the running game on this
installation. Start with the [Scan plan](SCAN_PLAN.md). The JSON catalogues contain
only image names and reference sizes, never game pixels.

## Calibration quality and remaining work

Automatic preset cuts require texture, a strong self-match and separation from
other matches. Unreadable or colliding names are not invented. Existing images
that no longer match are marked stale; missing, corrupt or known-stale required
images refuse a run before its first preparation action. Module duplicates are
allowed because their names are independently read from the detail panel.

Image availability is not a live accuracy guarantee. Verify recognition on the
actual emulator before unattended use. These changes have offline regression
coverage; cross-emulator live calibration still needs verification.

Automatic navigation needs locally captured navigation images first. The Scan
plan and live-frame cropper work with no templates. Captured images still need
recognition checks; there is no claim of automatic, template-free navigation.
Rare states require human-driven observation. Other emulator/account combinations
still need live validation.

## Two-stage module calibration

Use **Phase 1: basic scan** first. It observes equipped modules and the inventory
without changing equipment. Then **Phase 2: scan and restore equipped modules**
first opens all eight slots and saves an equipment manifest with names, rarities,
levels, effects, screenshots, and equipped/empty/locked status. Unknown slots block
all equipment changes. It then unequips occupied slots, indexes the whole inventory
once, and restores from that index. A durable journal precedes the first Unequip.
All original slots, levels and the selected preset must match before completion.
Stop requests switch to restoring the saved setup. Existing images are replaced only when the existing
**overwrite existing cuts** option is selected.

Assists display scaled effect values and inventory copies have no slot level.
Copy identity uses native rarity/star/name imagery and ordered effect types and
rarities; original slot levels are checked after restoration. No level transfer,
upgrade, unlock, merge or purchase is part of calibration.

If the emulator disconnects, `module_restore.json` remains in the current
account/emulator calibration folder. Run readiness blocks other automation.
Return the game to Home and resume calibration to restore the saved module
before any further calibration phase. Never delete this journal to bypass the
block. Missing/corrupt recovery evidence must be repaired, not silently ignored.

Primary and assist round trips were live-verified on the current BlueStacks
installation. Other emulators retain the native-layout preflight checks; they
have not yet been live-tested with this stage.
