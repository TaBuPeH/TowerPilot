# Tower Pilot: implementation handover and completion plan

Updated 2026-09-09. This is the primary handover for the next agent. Read it
before `HANDOVER_SCANNING.md`, which describes an earlier state.

## 1. The actual request and the honest state

Build a local, user-friendly driver for The Tower. The desired client journey is:

1. Connect to a supported emulator and select the user's account.
2. Extract artwork from the user's installed game into local storage.
3. Start on Home, identify controls using a shipped text manifest and the
   extracted artwork, and verify their rendered appearance on the emulator.
4. Navigate available screens one by one, identify their blocks, and save a
   local mapping for this account, connection, game version and rendering.
5. Offer automation only where the required controls and state transitions
   are verified. Remap should refresh the installation without manual cropping
   being the normal setup experience.

The user gave a long narrated walkthrough and left the emulator available for
testing. They authorized code, UI, flow and implementation changes without
routine questions. They subsequently lifted their earlier project-only/scope
restrictions. This does not change the product requirements: ship no game
images or personal account state; setup must not buy things or make arbitrary
gameplay changes. Use judgment, existing authorization and project guards.

**This goal is not complete.** The shipped manifest now has an `interface`
catalogue of 46 screens and 78 controls, but this is partly a specification.
Some screens have no individually mapped controls. The executable starter
scanner still covers 10 screen definitions. There is no complete automatic
traversal of all narrated screens, no verified fresh-install end-to-end pass
for the new catalogue, and no finished generalized client action engine.

Do not report “manifest ready” from the number of screen descriptions. Do not
report Home 100% from a small declared subset while important entry controls
have not been represented. Do not equate extracted artwork, captured images,
known account inventory and verified actionable controls.

## 2. Repository and execution state

- Project: `D:\projects\TowerPilot`.
- Branch: `codex/client-driven-autoclicker`.
- HEAD: `03efca3 Build account-local manifest scanning and remove shipped game images`.
- Previous commits: `fcc35a0`, then `619bf2d`.
- Substantial changes remain uncommitted, including untracked Python modules
  and tests. Preserve them. Do not stash, reset, clean or restore old game PNGs.
- The earlier handover's staged-deletion warning describes the state BEFORE
  the checkpoint commit. Check current `git status`; do not blindly recreate
  an old index state. The deletion of shipped artwork is already in HEAD.
- Read `CLAUDE.md` for project rules and `docs/ARCHITECTURE.md` for the layer map.
- Python used successfully: `C:\Python312\python.exe`.
- Dashboard: `http://127.0.0.1:8621/ui/index.html#calibrate`.
- Dashboard was restarted during the last implementation turn, using
  `TOWER_PILOT_PORT=8621` and `pythonw frontend/dashboard.py` from the repo root.
  Do not reuse a recorded PID. Re-identify the listener and command line.
- MuMu was the active emulator; native game capture is 1080 x 2560 at 360 dpi.
  The game uses a secondary Android display. Resolve it before sending input.
- Read current config to resolve account and instance rather than copying
  account identifiers into code or release defaults.
- Last project capture: `backend/captures/narrated_manifest/implementation_current.png`.
  It was saved successfully but NOT visually inspected in that turn. The
  current game screen is therefore unknown. Always capture anew before input.

### Current changed/new implementation areas

Tracked modifications include `backend/device/adbclient.py`, mission/store
interactions, artwork extraction/matching, bootstrap/calibrate/learned,
manifest and generic allowlist, scan plan, screen detection, recording tool,
dashboard and HTML, and tests. New modules include:

- `backend/player/clicker.py`
- `backend/player/interface_manifest.py`
- `backend/player/manifest_contribution.py`
- `backend/runtime/files.py`

New tests include atomic reports, clicker, coverage, contributions and manifest
screen detection. `git diff --stat` excludes untracked files; inspect both.

## 3. Implemented versus merely planned

| Area | What exists | Important limit |
|---|---|---|
| Account-local setup | Account/connection resolvers, local templates, extracted asset library, reports and learned manifest | Verify invalidation on changed display/game/account; no broad multi-emulator pass in this turn |
| Artwork extraction | Installed game assets can be extracted and matched to named bindings | Source art is not automatically the rendered control or proof of ownership |
| Starter scan | Home proof, routes through 10 screens, second-frame capture verification, vertical progress | Not all Home controls or narrated screens are supported |
| Cards | Existing starter inventory pass scrolls the list; prior live history reports 31 identified on this account | That is historical evidence, not a fresh regression pass or universal card count |
| Modules | Inventory/detail logic and snapshot/restore work already exist; walkthrough restoration evidence is local | Starter log opens the first inventory module only; that is not a complete module inventory or restoration test |
| Screen detection | Runtime header detection uses shared manifest rectangles | Remaining runtime search regions and actions are not all migrated |
| Learned positions | Verified cuts can record screen/rect/source; missing cuts can be reacquired | Fixed replay must not be applied to freely moving or scrolling controls |
| Contributions | Whitelisted text-only export; candidates separated from verified identities; reference promotion for declared identities | New reviewed routes and unfamiliar screens are not automatically executable |
| Action tester | Six bounded menu actions, preview, verify-and-click-once, watch-once, destination checks | Developer tool, not a general autonomous driver |
| New catalogue | Manifest v5 `interface`: 46 screens, 78 controls and workflow rules | Most new descriptions are not wired into scanner recognition/routes |
| New evidence display | Scan-plan API includes catalogue coverage; HTML renders documented/captured/verified controls | New UI was not successfully click-tested after restart; status model needs further work |
| Dynamic battle menu | Mission/store menu lookup now searches by current icon and returns unavailable on ambiguity/missing | Other call sites, learned captures and menu art bindings still need audit |

### Exact changes from the most recent implementation work

1. Expanded `bootstrap_manifest.json` to version 5 with `interface.screens`,
   controls, positioning rules, acquisition modes and workflow descriptions.
   Existing bootstrap targets are referenced rather than copying their rects.
2. Broadened side-menu search to `[840, 0, 240, 1100]`. This is a search region,
   not a guarantee about menu height across all accounts. Associated dynamic
   artwork definitions no longer prefer old per-icon reference rectangles.
3. Added `search=` to `player.clicker.locate`, allowing a container search while
   preserving confidence/ambiguity checks.
4. `interactions.missions.find_tile` requires a recognized open side menu,
   searches the current icon, and returns `None` instead of fixed fallback
   coordinates. Badge checks and mission/guild/store flows handle `None`.
   Legacy fallback parameters/constants remain and should be cleaned up only
   after auditing callers. Do not reinstate fallback taps to make a test pass.
5. Added `player.interface_manifest.coverage`, exposed through `scan_plan.plan`,
   and `renderInterfaceCoverage` in the dashboard HTML. Verification requires
   matching local file hash, a verified report entry and the expected screen.
   A later failed report entry supersedes an older success.
6. Added regression tests for moved/duplicate icon matching and evidence states.
7. Fixed one existing release-clean test to explicitly use an empty config,
   removing its dependence on the selected real account.

### Known shortcomings in those changes

- `interface` is an extra section in the SAME shipped JSON, not yet the single
  executable model. It must converge with `screens`, `hud`, `routes` and
  `asset_bindings`, not become another geometry table.
- Coverage currently indexes latest evidence by template rel, not a complete
  screen/state/version tuple. Shared controls seen on another screen can
  invalidate display of an earlier screen even with identical art. Improve
  evidence identity deliberately; do not weaken screen checks.
- Coverage checks hash presence but does not independently decode image data.
  It does not explicitly validate display/game version or all screen states.
- It counts all declared controls, including optional/manual ones. This is
  catalogue coverage, NOT permission to run or a universal completion gate.
- `documented` combines several different cases: no executable binding, no
  current capture, and absent local file. Split these for useful next actions.
- Empty control sets correctly do not count as verified, but give no actionable
  acquisition plan yet.
- The UI adds a 46-item vertical list and may make an already long page harder
  to use. Test layout and simplify primary setup; advanced tools should remain
  expanded per explicit user preference.
- `scan_plan.py`'s older evidence calculation filters verified entries before
  indexing and can retain an older success after a later failure. Align it with
  the new evidence policy; readiness and artwork verification must agree too.
- `asset_verify.best_match` chooses the strongest match; audit ambiguity across
  competing source assets and repeated occurrences, not just template matching.
- A high match threshold stopping safely is preferable to guessing, but test
  real icon crops, animation, badges and dimming before calling it reliable.

## 4. Source map and data boundaries

| File / component | Responsibility / next inspection |
|---|---|
| `backend/player/bootstrap_manifest.json` | Shipped text identity, layout, routes, source bindings, new interface catalogue |
| `backend/player/bootstrap_layout.py` | Cached loader, writable target allowlist, screen target lookup, scan steps |
| `backend/player/bootstrap.py` | Screen anchors, recognition, preflight, Scanner, routes, two-frame writes, observe |
| `backend/player/asset_library.py` | Local extraction/index and artwork binding |
| `backend/player/asset_verify.py` | SIFT/affine rendered-art location, search regions and candidate choice |
| `backend/player/calibrate.py` | Sanctioned template writer, evidence, basic/module phases, CLI |
| `backend/player/learned.py` | Local learned cut positions and provenance |
| `backend/player/flow_capture.py` | Setup-owned battle capture, HUD/UW labels, menu and result capture |
| `backend/player/scan_plan.py` | Acquisition plan and Control gate |
| `backend/player/readiness.py` | Requirements for selected flows and run readiness |
| `backend/player/interface_manifest.py` | New read-only catalogue/evidence projection |
| `backend/player/clicker.py` | Bounded preview/execute, double check, no uncertain retry |
| `backend/player/manifest_contribution.py` | Sanitized export and declared reference promotion |
| `backend/vision/screen.py`, `detect.py` | Runtime screen/template recognition |
| `backend/interactions/missions.py`, `store.py` | New no-fallback dynamic menu lookup; audit remaining navigation |
| `frontend/dashboard.py` | API, action lock, worker lifecycle, account binding, capture/readiness |
| `frontend/webui/index.html` | Setup, scan progress, coverage, account discoveries, advanced tester |

Useful symbols: `Scanner.harvest`, `update_screen_map`, `learned_cuts`,
`verify_asset_rels`, `recognize_screen`, `screen_matches`, `scan_steps`,
`clicker.inspect`, `clicker.perform`, `scan_plan.control_gate`.

Keep these distinctions:

- Shipped manifest: generic control semantics, source-art identifiers, native
  reference regions, verifiers, safe transitions, variable layout rules.
- Local extracted assets: game pixels, never committed or exported as manifest.
- Local learned mapping: rendered crop/hash/rect, screen/state, source version,
  timestamps and evidence. Account and emulator specific.
- Local player state: names, unlocked features, inventories, equipment,
  priorities, presets. Never turn the walkthrough account into release defaults.
- Contributions: sanitized structural candidates for human/developer review.
  Do not execute arbitrary supplied scripts, routes or coordinates.

## 5. Walkthrough evidence: use it instead of asking the user to repeat

Local evidence is under `backend/captures/narrated_manifest/`, including
`notes.jsonl`, before/after screenshots, `*_positions.json`, inventory notes
and sequences. These files are ignored and can contain personal account state.
Read and inspect them, but do not copy them wholesale into shipped files.
Some positions are approximate, and some destination labels are narrated
intent rather than independently clicked transitions. Check their evidence fields.

### Home and general rules

- Claim Ad Gems appears/disappears and shifts the LEFT column. Never encode a
  fixed row for the icons below it. The user described a paid-pack behavior and
  roughly 10-minute recurrence; their suggested daily cap was uncertain.
  Do not bake uncertain caps into behavior. Distinguish daily limits and event
  progress; both can exist.
- Red notification dots indicate pending attention/rewards in the walkthrough.
  They nominate a screen to inspect, not proof that a specific purchase/claim
  is available or authorized.
- Difficulty arrows flank the tier display. Verify the tier after input;
  cannot rely on a counted click if state did not change or at an endpoint.
- Battle may show `Preset: <name>` and an adjacent preset button only if
  presets are unlocked. The preset count/names vary.
- Dissonant Run opens selection of four disabled categories. Selected artwork
  changes (red X). Keep identity, selection state, effect and battle action
  separate. Do not start a run simply to learn the selector.
- Bottom navigation: Home, Workshop, Cards, Modules, Labs, Store.
- Options, Rankings, Mail and Key Vault are outside requested automation.
- Global Preset top selection changes lower assignments; changing one of the
  five lower sections edits that global preset. Counts vary; cards can have
  more options than other sections. Do not scan by altering assignments.
- Evidence: `home_walkthrough_index.json`, `home_difficulty_battle_positions.json`,
  `home_difficulty_and_dissonant_test.json`, `global_presets_positions.json`.

### Tournament and occasional dialogs

- Tournament entry can show an occasional informational dialog requiring OK.
  Model it as a recognized interrupt, not an always-required transition.
- Heat / Overheat / Close. Each scrollable condition block has a top-left title,
  up to two description lines and a trigger wave on the right. Same structure
  for Heat and Overheat; viewport rows are not fixed item identities.
- Tournament battle availability is conditional. Preset control and text are
  conditional too. Two presets were observed; up to five was user-reported,
  not a measured five-button layout.
- Never acquire imagery by purchasing entry/tickets or cancelling a tournament.
- Evidence: timestamped `tournament_*` and `conditions_*` captures.

### Events: Missions / Event Shop / Bots

- Reward progress has four relic positions: two ordinary and two boosted/locked
  in the observed layout. Claim replaces the threshold next to a relic.
- Quest cards scroll vertically. Three tiers award 10/15/20 medals; boosted
  rewards were described as 20/30/40. Read progress and reward state, don't
  mistake a partial scroll for missing quests.
- Claim BUTTON bounds are wider than the word CLAIM. Store separate recognition
  crop, full hit region and card-relative location. The user corrected this.
- Event shop sections move with scroll: anchor to the section/card in the
  current frame. A section title is not globally fixed in screen coordinates.
- Currencies and four module-shard types can have increasing prices; no default
  automatic purchases. Themes contain tower skin/background, owned or buyable.
- Two regular relics and four rotating relics were described. Normal blue/purple
  prices 200/500 medals; rotating rows 600/1200 diamonds. Treat these as observed
  product rules requiring current price/currency checks, never payment authority.
- Three songs were described as lifetime purchases; ownership still local.
- Bots: variable presets, up to five user-reported; one bot can be enabled or
  disabled with On/Off. User showed only some owned bots; other bots remain
  unobserved, not nonexistent. Don't buy them to complete setup.
- Evidence: timestamped events/shop/bots captures and quest screenshots.

### Guild: Members / Guardian / Store

- Members page has four fixed weekly contribution reward boxes. Distinguish
  already collected, available and locked. Observed thresholds are in
  `guild_weekly_reward_positions.json`.
- Reward reveal can have Next and Skip; last page has final collection behavior.
  For a single reward type Skip may not exist. Reobserve each step.
- Guardian: variable presets; equipped slots can be locked/unlocked/empty.
  Inventory chip identities have stable grid positions in the observed layout;
  verify identity and unlocks. Long press opens detail with three parameters.
- Guild store uses scrolling anchored sections, different currency, themes,
  relics and chips. Purchases are not part of calibration.
- Evidence: `guild_guardian_positions.json`, `guardian_chip_*`,
  `guild_reward_sequence.json`, `guild_reward_reveal_positions.json`,
  `guild_store_inventory_positions.json` and screenshots.

### Daily Missions

- Top weekly track has seven rewards and scrolls horizontally. Match threshold
  number and state; repeated chest icon alone cannot identify a reward.
- Lower mission cards/claim controls use a card-relative layout like Events.
- Numeric recognition must handle digit/font variants, not just current values.
- Evidence: `daily_missions_positions.json`, `weekly_reward_scroll_positions.json`.

### Perks, Themes, History

- Perks has First Perk, Ban Perks, Auto Pick. User normally chooses Perk Wave
  Requirement for first perk; this is preference, not a universal default.
- Ban slots range with unlocks (user described 1–8). Selected slots lie between
  Selected/Available headings. Green tick at lower right marks a selected
  available perk. Inventory scrolls; an empty selected slot has its own state.
- Auto Pick priority uses arrows to swap adjacent entries. Layout direction was
  described as right arrow moves up, left moves down: use the observed test and
  verify before/after order. Do not infer from icon direction alone.
- Unlocked rank slots, locked slots, unranked available perks and banned perks
  can coexist; banned ones were at the bottom. Observed unlocked capacity is
  local, never a fixed capacity for all accounts.
- Profile/Themes/Relics; Themes has Tower/Background/Music/Menus. Owned items
  scroll. Skin quest should remember original selection, equip another owned
  skin, restore original and verify, not leave the user's preference changed.
- Battle History entries open reports. Copy may provide structured stats, but
  clipboard extraction was not validated. Do not claim it works yet.
- Evidence: `perks_*`, `ban_perks_available_inventory.*`, `auto_pick_*`,
  `themes_tower_positions.json`, `battle_history_positions.json`,
  `battle_report_positions.json`.

### Workshop

- Out-of-run upgrades/enhancements are manual. The user explicitly excluded
  automation here, especially Ultimate Weapons. Navigation was inspected only.
- Distinguish this from in-battle upgrade automation, which may be desired.
- Evidence: `workshop_navigation_inspection.json`, `workshop_upgrades_positions.json`.

### Cards

- Preset count varies. Buy x1/x10 may be an opt-in quest action, never setup.
- Active slots scroll horizontally; inventory scrolls vertically. Do not send
  the same gesture into both panels.
- Inventory card logical row/column is stable for the observed game ordering.
  Name at top, icon in middle, stars at bottom; green check indicates equipped;
  star count/color shows level and mastery. Locked means cannot swap during a run.
- Scan every page with overlap/deduplication, detect actual bottom and preserve
  stable card identity. A repeated page may be a failed swipe, not list end.
- Counts from this account are evidence only. Never hardcode six cards or the
  observed 31 cards / 22 active slots into the general scanner.
- Evidence: `cards_walkthrough_positions.json`, `cards_scroll_*`, scan history.

### Modules: the important recovery contract

- Four module types distinguished by border shape (octagon/triangle/diamond/
  circle) and rarity color. Primary and smaller assist slots; assists may be
  purchased, locked, empty or occupied. Probe all slots carefully, never unlock.
- Equipped modules disappear from the inventory. This caused previous stale/
  missing classifications. Absence from inventory does not mean not owned.
- Correct full algorithm: snapshot every occupied primary/assist with identity,
  persist restoration journal, unequip, scan whole inventory, restore each
  saved assignment, then verify every slot. Inventory completion alone is NOT
  scan completion. Stop/cancel must preserve recovery and show restore pending.
- Capture icons from the visible inventory before opening detail, then link the
  clicked icon to the detail identity. Do not use a differently rendered detail
  portrait as the inventory template.
- Duplicates and rarity variants require per-copy identification; name alone
  can select the wrong copy. Never transfer levels to facilitate scanning.
- Primary/Assist choice may be a separate prompt. Evidence shows a manual
  restore of two modules; use it to understand the state machine, not to ship
  those particular module identities.
- Shatter: unlocked allowed-rarity modules, selected/unselected state, up to 12
  per batch. Lock means do not select. Verify summary/warnings before confirm.
  The user explicitly authorized ONE test batch of four types; this is not an
  evergreen policy to destroy modules during subsequent tests.
- Auto Merge: preview, verify proposed merges, confirm allowed batch, collect
  rewards. One explicit test was recorded; do not repeat destructively by default.
- Evidence: `modules_equipped_slot_snapshot.json`, `module_equipped_detail_positions.json`,
  `modules_restore_test.json`, `restore_*`, `modules_shatter_test.json`,
  `modules_auto_merge_test.json`, associated before/after images.

### Labs and Store

- Lab 1 opens research selection. History / Hide completed / range-filter icons
  occupy a row. Range filter shows skills with adjustable levels.
- Opening a skill reveals Set Values with +/−. Adjust only requested setting;
  do not press research/start/change research. Close back out when finished.
- Store: only Free gems is normal requested automation. Identify gem icon,
  x20 and Free/availability, with surrounding pack/section context. Limited
  offers above can shift it, so absolute y is unsafe. Countdown is not claimable.
- Evidence: `labs_range_entry_positions.json`, `labs_range_slider_positions.json`,
  `store_walkthrough_positions.json`.

### In-battle screens and temporary indicators

- Four bottom panels: Attack, Defense, Utility, Ultimate Weapons. Each scrolls
  below its header; panel may begin already scrolled. Identify rows in the
  current viewport. Stable row order does not mean stable screen y.
- Above panel: left player health/overheal, attack/regen/coin multiplier; right
  tier/wave/enemy health/attack. Optional Wall and its overheal above the left
  box. Optional ability cards above Wall; Claim Ad Gems above abilities.
- Intro Sprint is a clickable temporary indicator. Nuke/Demon Mode enabled and
  disabled appearances were captured. Do not trigger them merely to test lookup.
- Orbiting gem follows a circular path around the tower. About 168 native pixels
  radius was observed for one layout; it is NOT a game-meter scaling law. Use
  temporal matching and current center/geometry, never hardcode range ratio.
  Capture timestamps matter: nominal 0.5-second scheduling can drift.
- Second Wind: automatic trigger, wing-like icon above Nuke/Demon with declining
  border. User states 30 in-game seconds of immunity. Timer must account for
  speed changes, pause, late detection and uncertainty; cannot assume 30 real
  seconds or infer immunity without a trigger observation. Border animation
  should not be part of the stable glyph template.
- In-battle menu icons have NO fixed row, position or order. Availability changes
  with unlocks and run type. Find identity inside the current menu: Missions,
  Options, Cards, Research, Target Priority, Modules, Events, Orb Adjuster, Guild,
  Conditions, History, optional tournament trophy; Exit Battle is separate.
  Do not promote observed row numbers from screenshots into universal geometry.
- Orb Adjuster and Target Priority are settings dialogs. Target priority uses
  drag/drop; little automation requested. Preserve settings when just observing.
- Perk progress displays current/next threshold; New Perk opens offered choices.
  Four were observed but capacity may vary. A choice can expose another set
  immediately, especially after reducing wave requirements.
- IMPORTANT CORRECTION: the app may need its OWN configurable perk priority,
  independent of the game's Auto Pick priority. The user explicitly rejected
  the claim that the game's priority is always sufficient. Offer a deliberate
  game-auto/app-priority/manual mode, reidentify offers after every choice.
- Exit dialog offers surrender versus Home while preserving a run. These are
  different transitions. Results offers Retry same run and Home; capture stats,
  selected perks and report before leaving. Do not assume Home always ends a run.
- Evidence: `battle_*_positions.json`, `battle_list_scroll_observations.json`,
  `new_perk_choices.png`, `results_*`, `surrender_*`,
  `orbit_diamond_sequence/`, `second_wind_20260909_021610/`.

## 6. Completion plan, in dependency order

### A. Audit evidence and make the manifest executable

1. Inventory every narrated screen/control against current JSON. For each,
   record whether it is described, has reference evidence, has artwork binding,
   has rendered verification, has tested transition, or is still unobserved.
2. Inspect local position JSON and screenshots. Resolve approximate measurements,
   duplicate screen names and destination ambiguities. Example: battle-menu
   History destination must be verified rather than assumed to be report directly.
3. Define a schema with stable screen/state/control IDs, positioning modes
   (fixed, near-anchor, container search, repeated row/grid), optionality,
   entry/exit proof, scroll behavior, action effect and acquisition strategy.
4. Reuse existing geometry by reference. Add schema validation for native bounds,
   unknown references, duplicate IDs, unsafe setup actions and unreachable routes.
5. Support multiple rendering layouts explicitly. Either verify a supported
   native display configuration or decline with a useful remedy. Do not invent
   proportional scaling as a substitute for actual rendering verification.

Acceptance: every included screen has a clear acquisition/verification path or
an explicit unobserved/manual status; no empty screen reports complete; schema
validation fails bad references before any game input.

### B. Finish Home as the reliable root

1. Map all relevant Home entry controls from installed art or composed-control
   evidence, including shifting left column and optional right-side controls.
2. Separate mandatory root navigation from optional unlocked features. Home
   completion should state its denominator and observed capabilities, not require
   impossible locked features or declare them verified.
3. Verify a second frame and confirm the target is unobscured and actionable.
4. Capture Home variants with/without ad gem, preset controls, and resumed-run
   states. Route only from positively identified state.

Acceptance: fresh local mapping can identify available Home navigation without
shipped images or manual crops; ad-gem disappearance does not misdirect a tap;
missing optional features are clearly distinguished from unknown failures.

### C. One shared recognizer and action executor

1. Resolve control by screen and current anchor/container, not old screen rect
   alone. Require unique identity, good appearance and appropriate state.
2. Handle identical icons/text in different rows using parent/card identity.
3. Verify immediately before input; reobserve destination after input. Unknown
   result must stop for observation, never repeat a potentially executed action.
4. Model known occasional dialogs as explicit interrupt states with tested safe
   exits; unknown overlays block input.
5. Migrate mission/store and other interactions to this resolver; remove obsolete
   fallback coordinates and duplicated geometry after tests cover behavior.

Acceptance: synthetic and real tests for reordered icons, duplicate art, badge
overlays, disabled controls, wrong screen and movement between frames. No tap
occurs when identity or destination is uncertain.

### D. Expand the safe screen walk

Add routes in bounded groups, with a real before/after verification for each:

1. Home menus: Missions, Perks, Themes, Global Presets, History.
2. Events: Missions, Event Shop sections, Bots.
3. Guild: Members rewards (observe), Guardian inventory/details, Store sections.
4. Cards full inventory/active scroll; Modules inventory/detail/roles.
5. Labs range dialogs and Store free-claim region (observe settings/purchases).
6. Battle HUD/menu and result states using an explicitly setup-owned normal run
   or passive capture of a human run. Tournament remains observe-only acquisition.

For scrolling: detect viewport and row identity, overlap pages, dedupe by item,
verify movement, distinguish failed gesture from bottom, use bounded retries,
record partial clipping, restore entry screen/scroll when reasonably possible.

Acceptance: route progress is persistent and truthful; interrupted walks resume
from observed state; absent features are skipped with explanation; no purchases,
research changes, merges/shatters or human-run surrender during setup.

### E. Make module scanning transactional

Audit existing implementation before replacing it. Persist original assignments
before any unequip; survive app crash, emulator disconnect, stop and restart.
Tie journal to account/emulator/preset. Verify destination role and every final
slot. Wrong-account restore must be refused. Expose “restoring” and “restore
pending” separately from inventory progress, with a recovery action.

Acceptance: test zero/two/four assists, locked/empty assists, duplicate name/rarity,
inventory order changes, detail dialog mismatch, interrupted restoration, full
inventory and cancelled scan. Original equipment must be verified restored.

### F. Deliver the simple client UI

- Primary journey: Connect → Remap → Ready features / remaining checks.
- Keep “What to scan next” first. Show current screen, action, completed steps,
  vertical history, partial failure reason, stop/resume and restoration state.
- Separate “What we know” from “What the next scan should do.” Persist switches
  by account/connection; polling must never rebuild/reset the form.
- Readiness is per chosen automation, with dependencies and evidence states.
  Explain why a feature cannot start without forcing every optional image.
- Advanced tools stay expanded per user request, but organize them below the
  primary journey. Repeats/fine tuning belong in a configuration group.
- Avoid “everything complete” from a process finishing or a zero-item step.
- Preserve account/settings during remap; preview disruptive changes explicitly.

Acceptance: drive through actual UI as a new user; no manual knowledge of template
paths needed for normal setup, no accidental Control access before usable mapping,
no forever-running boot indicator, no contradictory counts between cards.

### G. Finish opt-in automations and recovery

Prioritize useful collection and run requirements after acquisition is solid:
ad/free gems, mission/guild claims, perk selection with independent priorities,
run ability state and result capture. Skin swap/restore, module shatter/merge,
card purchases, shop themes/relics and lab values need explicit per-feature
policies and limits. A narrated desirable feature is not blanket purchase policy.

Use one execution model with journals for reversible configuration changes,
bounded actions, positive success evidence and local event history. Avoid adding
separate ad hoc click loops for each new screen.

### H. Contribution and release workflow

1. Export generic structural evidence only; strip pixels, OCR account text,
   file paths, preset names, equipment and machine identifiers.
2. Candidate → review → two-frame/transition verification → versioned manifest.
3. Validate compatibility and unknown fields; do not execute arbitrary imports.
4. Test clean installation on MuMu and BlueStacks without existing template files,
   with low-progress and higher-progress fixtures. Do not erase the user's current
   working account to simulate fresh setup; use isolated local test state.
5. Confirm packaging excludes every game image, extracted bundle and account file.

Acceptance: a fresh client obtains its own assets and maps supported available
screens; community additions can describe higher-progress features without
leaking account data or becoming unreviewed click scripts.

## 7. Cross-cutting edge cases to test

- Emulator disconnected mid-capture/input; reconnect changes display ID.
- Wrong game foreground, desktop launcher, unknown overlay, another process
  controlling the same emulator, user clicks while scan is navigating.
- Account switch, resolution/DPI change, game update or theme change invalidates
  relevant evidence; one account's crops never silently satisfy another's gate.
- Two identical controls, repeated chest icons, scroll clipping, changing text,
  animated border, disabled/dimmed parent, badge obscures stable art.
- First and second frames disagree; input timeout after possible execution;
  dialog closes during observation; no confirmation screen appears.
- Missing source asset, multiple sprite names, same sprite in multiple contexts,
  transparent padding, composite controls, blank texture placeholders, font variants.
- Stale report versus replaced/deleted local image; atomic report writes;
  restarted worker with old state; browser reload/close during action/watch.
- Native rect versus displayed screenshot dimensions: use native coordinates,
  never multiply browser CSS coordinates into game input without proven mapping.
- Timer pause/speed change/late detection; stacked perks; run resumed from Home;
  setup-owned run identity lost after restart.
- Purchase affordability/price escalation/owned state and locked modules; a match
  identifies a button, it does not authorize consuming currency/items.

## 8. Validation actually performed and what remains unverified

Last focused backend run passed 32 tests:

```powershell
C:\Python312\python.exe -m pytest backend/tests/test_interface_coverage.py backend/tests/test_client_clicker.py backend/tests/test_bootstrap_manifest.py backend/tests/test_manifest_screen_detection.py backend/tests/test_release_clean.py -q
```

Frontend run passed 17 tests:

```powershell
node --test frontend/tests/*.test.cjs
```

Changed Python files were compiled before dashboard restart. The full backend
suite was NOT rerun in this implementation turn. Older docs mention 14 failures
with bound accounts; that number is historical, not a current result. One such
test was isolated this turn. Run the full suite and categorize actual failures;
fix fixture isolation instead of modifying the user's account or lowering guards.

Required additions: catalogue schema validation, per-screen evidence identity,
game/display invalidation, absent/ambiguous mission tile no-input tests, real
dynamic-menu matching, traversal recovery, transactional module restoration,
frontend coverage states and actual click-through setup.

### UI testing failure details — avoid repeating the same dead end

- In-app browser reading worked and exposed dashboard controls/history.
- A request to open a Chrome session through the browser connector returned
  `Browser is not available: chrome`.
- Windows computer-use initially found Chrome but returned a URL-confidence
  policy error and stopped that turn. This was tool-specific, not lack of user
  authorization and not proof that project code/ADB access was blocked.
- On a later user-requested attempt, existing Chrome window state WAS readable.
- Launching a new Chrome window with a shell command was rejected as
  `blocked by policy`; no more reason was provided. Do not assert a diagnosis.
- Clicking Setup through Windows computer-use then failed with
  `foreground window did not report a process id`.
- Refreshed window enumeration no longer showed Chrome. The user interrupted
  and requested this handover. Do not assume Chrome is still open or reuse IDs.
- There is no verified successful Chrome click in these attempts and no new
  controllable Chrome window was established. Do not claim UI tests passed.
- Respect tool blocks; do not disable protections or bypass them. Continue
  independent code/test work if a UI method is unavailable. New attempts should
  start with fresh tool documentation, current windows and current page state.

## 9. Practical emulator verification

Use the project's existing socket client. Do not spawn adb for each action.
Read-only example from the project root:

```python
import sys
sys.path.insert(0, 'backend')
import settings
from device import capture

# Resolve the configured instance; do not guess a display or copy account data.
settings.select_instance('main')  # verify this ID in current local config
capture.refresh_display()
frame = capture.grab()
```

Inspect a freshly saved local frame before any action. Use `device.act` for
authorized taps/swipes, log the reason, and capture after. New processes must
resolve display again. Browser dashboard screenshots are not native game frames.
If a game is in progress, do not surrender or switch equipment just to simplify
testing. Use passive verification or the established safe menu ownership rules.

## 10. First work session for the next agent

1. Read this document and project rules; inspect branch/status without changing
   user work. Read current config and runtime state privately.
2. Inspect the local walkthrough JSON/screenshots for Home and one next menu.
   Build the missing-control inventory and schema checks before adding routes.
3. Resolve the current coverage mismatch and evidence-state semantics. Wire one
   additional screen end to end through shared recognition, safe route,
   capture, local evidence and UI progress; test it through the dashboard if
   a supported UI tool is available.
4. Expand by the dependency groups above, with meaningful tests and live proof.
5. Run a fresh-state setup against isolated local calibration, preserve the
   user's live state, and document genuinely incomplete/optional features.

Do not stop after promising another overnight run. Work during the active task,
report actual progress, and never substitute a catalogue count for completion.
If work must stop, leave exact code/test/runtime state and the next concrete
action here. The user should not have to repeat the walkthrough or grant the
same implementation permission again.
