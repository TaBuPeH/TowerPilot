# Tower Pilot: manifest-driven local client

## 1. Product goal

A regular player connects their emulator, opens the game on Home, presses
**Map my game**, and receives a local driver that understands the available
screens and can run the automations they choose.

The player should not need to name images, draw crops, enter coordinates,
understand ADB, or recreate a developer's account. Those are developer/repair
capabilities, not the normal setup journey.

The application ships **descriptions of the interface, not the game's images**.
It extracts artwork from the user's own installed game and learns how that
artwork is rendered on their emulator. Account ownership, unlocked features,
presets, settings and equipment are discovered locally.

The core loop is:

**Observe the screen → identify it → locate its described controls → verify
them → perform one intended action → verify the resulting state.**

Every feature—from setup to collecting rewards to selecting perks—uses this
same model. We do not build a separate collection of hardcoded click scripts.

## 2. What the client experience should be

### First use

1. **Connect.** Detect supported running emulators. If there is one, select it;
   if there are several, show names and a live preview. Explain connection or
   display problems with a specific repair action.
2. **Identify this account.** Start with one local account. The player can name
   it for convenience. Bind discoveries to the account and rendering environment;
   never infer ownership from the installed game's asset collection.
3. **Open Home.** Show a preview and verify that it is the game's Home screen.
   An existing live battle is a different state; setup must not end it silently.
4. **Map my game.** Extract local artwork, verify Home, traverse supported safe
   menus, scan the visible collections, return to Home and save results.
5. **Choose what to automate.** Show available features in plain language and
   explain any specific missing verification. Enable only the user's choices.

Normal setup should require no feature checkboxes when availability can be
detected safely. Optional choices remain useful for excluding a scan or selecting
more invasive work, such as equipped-module inventory or a setup-owned battle.

### Later use

Show **Connected**, the recognized screen, and which chosen automations are
ready. Offer **Remap** after game/rendering changes or recognition failures.
Reuse current valid discoveries; do not erase preferences or working mappings
merely because one target needs repair.

If the application cannot map something automatically, explain exactly what is
needed: for example, “Open Tournament Heat once so I can learn this screen.”
Manual cropping is the final repair option, not the default instruction.

### Two distinct information cards

**What we know** contains verified screens, available features, local presets,
inventory discoveries, current equipment and evidence freshness. Unknown,
locked, empty, unseen and unavailable must remain distinct.

**What to do next** contains the next scan or automation scope, exclusions,
expected changes and missing prerequisites. Turning a scan switch off must
never erase or visually negate existing knowledge.

Keep “What to scan next” at the top. Use a vertical progress/history layout:
current screen, current action, completed steps, skipped features, failures and
recovery. Preserve selections across polling, reload and account changes.

Advanced controls remain expanded, as requested, but grouped below the main
journey. Repeats, timing, diagnostics, image repair and developer tools must not
obscure Connect, Map, Ready and Start/Stop.

## 3. Four separate kinds of data

| Data | Contents | Where it lives |
|---|---|---|
| Interface manifest | Screen identities, controls, artwork names, layout rules, state proofs, transitions, acquisition rules | Shipped, versioned, text only |
| Asset library | Artwork extracted from the installed game, source identifiers and local index | Local to installation/game version |
| Verified mapping | Rendered control locations/crops, screen and state, source provenance, confidence and validation evidence | Local to account and rendering environment |
| Player configuration/state | Owned features, presets, equipment, priorities, selected automations and limits | Local to account |

Do not mix these. A source sprite is not proof of ownership. A reference
rectangle is not a verified location. A saved crop is not proof the control is
currently visible. A known button is not authorization to press it.

Use one canonical manifest model. Existing bootstrap screens, HUD targets,
routes and catalogue descriptions must converge into it through references or
generated compatibility views. Do not maintain a second geometry table.

## 4. The manifest model

### Screens are states, not just pages

Each screen definition has:

- Stable identifier and user-facing name.
- Identity evidence: distinctive art, text anchors, structural layout or a
  combination. One generic Close button is insufficient screen identity.
- Parent/container relationship and possible overlays.
- Supported rendering/layout variants.
- Controls and repeated collections.
- Safe routes in and out, with destination proofs.
- Acquisition mode: automatic navigation, passive observation, or manual only.
- Optional capability conditions, without copying one account's unlocks.

Represent meaningful variants explicitly: Home with/without a claim tile,
in-battle menu, tournament information overlay, module detail for equipped versus
inventory items, reward reveal versus final collection, and live-run Home versus
ordinary Home where the game distinguishes them.

### Controls carry identity, geometry and intent separately

Each control describes:

- Semantic identity, such as `open_events`, `claim_quest`, `module_unequip`.
- Source artwork binding when available.
- Recognition region and stable visual features.
- Hit region or click point relative to the recognized element.
- Relevant states: enabled, disabled, selected, locked, owned, claimable, unknown.
- Visibility/capability conditions.
- Intended effect and preconditions.
- Expected postcondition, including whether it changes persistent state.
- Acquisition instructions and required verification.

The word CLAIM can be a recognition crop while the button is much wider.
Preserve that distinction. Detection coordinates must not automatically become
an oversized click target spanning neighboring controls.

### Four positioning modes

| Mode | Use | Rule |
|---|---|---|
| Fixed region | Stable HUD or fixed milestone positions | Search near a native reference after proving screen/layout |
| Relative to anchor | Shop sections, labels with adjacent settings | Find the anchor in the current frame, then search its dependent region |
| Container search | Reordered battle-menu icons, shifting Home column | Find identity anywhere in the verified container; do not assume row/order |
| Repeated collection | Quests, cards, modules, perks, reward tracks | Detect row/cell, identify item, then locate its controls relative to that item |

An anchor inside scrolling content moves with that content. “Static” means a
stable relationship or identifiable element, not necessarily a fixed screen y.

### Rendering portability

Native coordinates define reference geometry for a verified layout, not a
universal scaling formula. For another emulator, first resolve display,
resolution, DPI, game viewport and layout. Match local extracted art to rendered
appearance and learn the actual mapping.

Initial support can require a known native layout with an automatic supported
configuration path. Unsupported layouts should be identified honestly. Later
layout variants can be added from verified evidence. Never claim arbitrary
resolution support by multiplying every coordinate by a ratio.

## 5. Developer mapping and client mapping

### Developer/admin mapping pass

The developer walks the game once to build a reusable structural description.
The supplied narrated walkthrough is input to this work; the player should not
have to repeat it.

For each transition:

1. Capture source screen and identify the intended control.
2. Record the control's meaning and whether it is safe to navigate automatically.
3. Observe the click or gesture.
4. Capture destination and verify the transition.
5. Record layout changes, conditional elements, anchors and scrolling behavior.
6. Connect visible controls to local source art where possible.
7. Export only sanitized structural information to the shipped manifest.

Coordinates observed once remain reference evidence until identity and behavior
are verified. Account preset names, currently equipped modules and screenshot
pixels never become universal defaults.

### Client mapping pass

The client receives that manifest, extracts its own game artwork and uses the
manifest to reacquire the rendered controls. It must not receive the admin's
game screenshots or assume the admin's unlocks.

Mapping is not just cropping rectangles. It establishes the screen, locates
identity, checks state, verifies a second frame, and saves provenance. Text and
composed controls may need local rendered evidence where source art alone is
insufficient. OCR/structural analysis may assist acquisition; routine actions
should use the verified local mapping where possible.

### Community contributions

Players farther ahead can open screens the original developer cannot access.
An optional contribution tool records structural candidates and exports a
text-only package for review. No automatic upload is necessary.

New candidates are reviewed for screen identity, geometry, source binding,
account-data leakage, action effects and compatibility. Only reviewed definitions
enter a manifest release. The client never executes arbitrary contributed code
or unreviewed click routes.

## 6. Home-first automatic traversal

Home is the root of the navigation graph. Map all supported relevant entry
controls available on the current account before following them.

“Home 100% mapped” must mean every required and currently available control in
the declared Home contract is verified, with optional unknowns disclosed. It
must not mean three easy crops succeeded or that locked features were assumed.

Traversal proceeds as follows:

1. Verify Home identity and current layout.
2. Map its available controls and classify unresolved ones.
3. Choose an unvisited safe route whose source control is verified.
4. Recheck target immediately before clicking.
5. Observe the destination and prove its identity.
6. Map its controls and collections.
7. Traverse safe child screens or return through a verified exit.
8. Continue until the supported reachable graph is covered or a precise blocker
   is encountered. Save partial progress continuously.

Do not interpret absence after one frame as locked/unowned. Use positive locked
evidence where available; otherwise record unseen or unresolved.

Occasional information dialogs are recognized interrupt states. Known harmless
acknowledgments can be handled with verified postconditions. Unknown dialogs
stop navigation; they are not solved by repeatedly clicking a guessed Close.

### Acquisition groups

| Group | Automatic discovery scope | Exclusions during mapping |
|---|---|---|
| Home | Difficulty, battle/preset controls, available menu entries, shifting claim layout | Starting a run without the selected setup mode |
| Events | Missions, reward states, shop sections, owned bots and presets | Purchases and bot upgrades |
| Guild | Weekly reward states, guardians/chip details, shop sections | Purchases, equip changes merely to identify controls |
| Cards | Presets, active slots, full inventory and selection/lock/mastery states | Card purchases or deck changes |
| Modules | Slots, visible inventory and details; optional full transactional scan | Unlocks, level transfer, shatter or merge |
| Perks | First perk, bans, priority order/capacity and available items | Changing the player's preferences just to map |
| Themes/history | Owned items and selections; reports and result structure | Unrequested skin changes or deleting history |
| Labs/store | Adjustable-value entry path, Free gems location/state | Starting research or paid purchases |
| Battle/results | HUD, menu, temporary indicators, offers and reports | Ending a human/tournament run |
| Tournament | Passive observation and approved safe inspection | Entry purchases, ticket consumption, surrender |
| Workshop/options/etc. | Structural/manual-only identification where useful | Workshop upgrades/enhancements and excluded menus |

## 7. Scrolling and item identity

All scrollable screens use a shared collection scanner:

1. Identify the viewport and scroll direction.
2. Read visible item boundaries and identities, including partial rows.
3. Record item-relative controls and relevant state.
4. Scroll with overlap.
5. Confirm content moved and merge newly identified items.
6. Detect the end through repeated stable boundary evidence, with bounded retries.

A failed swipe is not proof of the end. A repeated icon is not proof of the same
item. Store logical identity/order separately from the currently visible rect.

Specific collections:

- Cards: active strip horizontal; inventory vertical with stable logical grid.
- Modules: inventory order may change after unequip/equip, merge or shatter.
- Quests: repeated cards with identity, tier, progress and claim state.
- Weekly rewards: horizontal track; identify threshold and state, not chest art
  alone.
- Shop: sections and item cards; controls relative to section/card anchors.
- Perks: selected slots, available list, ranked/unranked/locked/banned regions.
- Heat/Overheat: condition title, description and trigger wave in each block.
- Battle upgrades/UWs: stable logical arrangement, unknown initial scroll offset.

## 8. Complete module inventory as a recoverable transaction

Equipped modules are absent from inventory. Therefore the complete scan is:

**Snapshot all slots → save recovery journal → unequip → scan inventory →
restore original assignments → verify restoration.**

Before any unequip, identify every primary and assist slot. Distinguish occupied,
empty, locked and unresolved. Inspect occupied details to record module identity,
rarity and enough distinguishing information for duplicate copies. Save the
selected preset and original assignments locally.

Capture inventory icons before opening their descriptions; link the clicked
icon to the name/detail. Do not use a differently rendered detail portrait as
the inventory template.

The journal survives stop, crash, disconnect and restart. It is bound to account,
emulator and preset. If identity is insufficient to restore confidently, do not
begin unequipping. If recovery fails, keep the journal and expose a recovery
action; do not report success or discard the original state.

Completion requires every original slot verified. Stop may stop inventory work
but should finish restoration where possible. Show inventory and restoration
progress separately. Never buy assist slots or transfer levels for mapping.

## 9. Shared runtime action engine

An automation requests a semantic action, not an x/y click. The executor:

1. Verifies account, emulator, screen ownership and current screen/state.
2. Resolves the control within the appropriate anchor/container.
3. Checks unique identity, enabled state and policy preconditions.
4. Rechecks a fresh frame.
5. Sends one input.
6. Verifies the expected state change and records the result.

An uncertain input outcome is not automatically retried. The control may already
have consumed currency, changed equipment or selected a perk. Reconcile observed
state before any further action.

Use this engine for both setup navigation and runtime behavior. Migrate existing
flows incrementally; remove blind fallback coordinates only when callers handle
unavailable targets properly. Recognition failure is a useful stop condition,
not a reason to lower thresholds until anything matches.

### Runtime policies

- Collection: ad gems, Free store gems, missions and guild rewards after checking
  actual availability. Notification dots nominate a screen to inspect.
- Perks: manual, game Auto Pick, or independent app priority. The app priority
  may deliberately override the game's system. Read each new offer set and
  repeat while choices remain, including stacked choices after wave reduction.
- Skin quest: remember original, equip another owned skin, restore and verify.
- Lab values: adjust the requested +/− setting and close; never start research.
- Module merge/shatter, card purchases and shop items: explicit opt-in policies,
  limits and preview validation. They are not setup activities.
- Battle control: distinguish returning Home with an active run, surrender,
  results, Retry and starting a new run. Preserve run identity.
- Results: snapshot summary, selected perks and detailed report before navigation.
  Clipboard report capture is optional until verified on supported emulators.

### Timed and moving elements

Second Wind is an automatic trigger with a temporary indicator and countdown
border. Detect a stable glyph; track the stated 30 game-second immunity using
current speed, pauses and observation uncertainty. Do not assume 30 real seconds
or claim a timer proves the trigger occurred.

Orbiting gems require current visual/temporal tracking around the tower. An
observed pixel radius is reference evidence, not a formula derived from tower
range in meters. Avoid clicking a stale position from an old screenshot.

## 10. Evidence, readiness and remapping

Use one consistent evidence vocabulary throughout the application:

| State | Meaning |
|---|---|
| Described | Manifest explains the control but no local rendered proof exists |
| Source found | Installed artwork candidate exists |
| Observed | Candidate found in a screen frame, awaiting verification |
| Verified | Screen, control identity, rendering and required repeated-frame checks pass |
| Locked/unavailable | Positive current evidence establishes that state |
| Unseen/unknown | Insufficient evidence; do not infer ownership or absence |
| Needs remap | Previous evidence is incompatible or a later check failed |

Evidence records include manifest/layout/game/source versions, account/connection
binding, screen/state, native rect or anchor relationship, local hash and the
verification method. A later failed verification must not be hidden by an older
successful report.

Readiness is evaluated per requested automation. Optional unavailable features
must not block unrelated runs. Conversely, finishing a scan process cannot make
a flow ready when its necessary transition or image remains unverified.

Remap invalidates only affected evidence where possible. Game updates, resolution,
DPI, theme/rendering or account changes need explicit compatibility handling.
Keep the last valid mapping available for comparison/recovery without silently
using it in a now-incompatible context.

## 11. Implementation sequence and release gates

### Milestone 1 — Canonical interface model

Consolidate screen catalogue, bootstrap geometry, HUD and routes into one schema.
Convert the narrated evidence into control definitions, marking uncertain or
unobserved details honestly. Add validators for IDs, references, bounds,
positioning modes, state proofs and unsafe setup transitions.

**Gate:** no described screen can be called executable or complete solely because
it has a name. No duplicated authoritative geometry.

### Milestone 2 — Local art and reliable Home mapping

Finish asset binding, composite-control recognition, two-frame evidence and Home
variants. Resolve emulator display/layout and account context automatically.

**Gate:** a clean local mapping identifies supported available Home entry controls
without shipped images or hand crops. Claim-tile disappearance does not shift
the target identity. Unknown/locked/optional states are explicit.

### Milestone 3 — Shared navigation and collection scanning

Implement the guarded action executor and collection scanner. Migrate one complete
route at a time: source proof, target, input, destination proof, capture, return.
Expand Events, Guild, Cards, Perks, Themes, History, Labs and Store in that order
or by available verified dependencies.

**Gate:** every promoted route has synthetic regression tests and observed live
transition evidence. Duplicate icons, overlays and failed swipes cannot cause
unintended input or false list completion.

### Milestone 4 — Transactional modules

Complete snapshot, journal, inventory identity, restore and verification. Integrate
with shared executor and progress/recovery UI.

**Gate:** interruption at every phase preserves or restores the original setup;
locked/empty assists and duplicate modules are covered; restoration is part of
completion.

### Milestone 5 — Complete client setup UI

Deliver the Connect → Map → Ready journey and the two information cards, persistent
choices, vertical progress, actionable remaining checks and grouped advanced tools.
Remove contradictory status/count calculations.

**Gate:** a person can complete supported setup without template paths, coordinates
or developer instructions; polling/reload does not reset their choices.

### Milestone 6 — Battle acquisition and useful automations

Add passive battle observation and a distinct setup-owned normal battle mode.
Map HUD, dynamic menu, upgrade panels, perk offers, temporary indicators and results.
Migrate collection/perk/result flows first, then optional configuration/purchase
policies with appropriate limits and recovery.

**Gate:** no human/tournament run is ended by mapping; no fixed ordering assumption
for battle-menu icons; stacked perk choices and timing uncertainty are handled.

### Milestone 7 — Contributions and portability

Add sanitized structural export, review/promotion workflow and manifest version
migration. Run fresh-state trials on supported MuMu and BlueStacks layouts,
including low-progress and high-progress feature variations.

**Gate:** only text ships; each client extracts its own art; account state is
isolated; unsupported layouts are explained rather than guessed.

## 12. Test matrix

| Dimension | Required cases |
|---|---|
| Accounts | New/established, one/multiple presets, different ownership, switching accounts |
| Connection | Multiple emulators, secondary display, disconnect/reconnect, wrong foreground |
| Geometry | Supported layouts, shifted column, resized/unsupported viewport, partial clipping |
| Identity | Duplicate art/text, rarity variants, disabled/dimmed control, animated borders, badges |
| Collections | Horizontal/vertical panels, middle start, failed swipe, overlapping pages, true bottom |
| Navigation | Known occasional dialog, unknown overlay, target moves between frames, timeout after input |
| Recovery | Stop/reload/crash during scan and restoration, stale worker state, pending journal |
| Persistence | Replaced/deleted crop, newer failed evidence, game update, account/display mismatch |
| Gameplay | Stacked perks, game speed change/pause, returning Home with active run, final reward without Skip |
| Policy | Locked modules, escalating prices, already-owned item, purchase disabled, manual-only Workshop |
| Release | No bundled game images/assets/account state; malformed/unreviewed contribution rejected |

Use synthetic images and fake device adapters for deterministic tests. Use
isolated local calibration state for fresh-install trials; do not wipe the
player's working account. Follow with real UI click testing and native before/
after evidence. Record exactly which parts were verified.

## 13. Definition of done

The design is realized when a supported fresh client can connect, extract its
own artwork, map available Home controls, navigate the supported safe graph,
discover account-specific collections, and enable selected verified automations
with little manual work.

The client must explain unresolved features, preserve player settings/equipment,
recover interrupted work, show truthful progress, and remap after compatible
changes. Its behavior must come from the canonical manifest and shared executor,
with no shipped game pixels and no dependency on the developer's account.

A large manifest, a successful extraction, or passing isolated tests is not this
definition of done. The final gate is a repeatable fresh-client experience on
the supported emulators.
