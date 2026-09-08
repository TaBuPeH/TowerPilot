"""What each recognition image IS, where it appears, and why a run needs it.

Text only - no pixels ship (CLAUDE.md, image-free release). The names the
code uses (`buttons/nuke.png`, `floaters/second_wind.png`) are obscure to
the person who has to go and find the thing on screen (user, 2026-09-08), so
every image a run can require gets a plain-language card here: `what` it is,
`where` in the game it shows, `how` to obtain it, and `art` - names of the
installed game's own artwork (the asset library Full setup extracts from the
APK) that show what to look for. Unknown names fall back to their folder.
"""
import re

# rel -> card. `art` lists asset-library names (case-insensitive, exact) in
# preference order; the dashboard serves the first one it finds locally.
DOCS = {
    # ---- HUD: the battle screen itself
    "buttons/demon_mode.png": {
        "label": "Demon Mode button",
        "what": "The Demon Mode ability button (winged red icon) in the ability row at the bottom-left of the battle HUD.",
        "where": "During a normal battle, bottom-left ability row, when the account has Demon Mode unlocked.",
        "how": "Full setup finds it on the HUD with the installed artwork during its Tier 1 battle. By hand: start a normal run, crop the button glyph.",
        "art": ["DemonMode"]},
    "buttons/nuke.png": {
        "label": "Nuke button",
        "what": "The Nuke ability button (mushroom-cloud icon) in the ability row of the battle HUD.",
        "where": "During a normal battle, bottom-left ability row, when the account has Nuke unlocked.",
        "how": "Full setup finds it on the HUD with the installed artwork during its Tier 1 battle. By hand: start a normal run, crop the button glyph.",
        "art": ["protector-nuke"]},
    "buttons/perks.png": {
        "label": "Perks button",
        "what": "The PERKS button that opens the perk choice during a run.",
        "where": "Battle HUD, near the top, once perks are unlocked.",
        "how": "Full setup captures it; otherwise crop it from a run.",
        "art": ["perks", "perk"]},
    "floaters/second_wind.png": {
        "label": "Second Wind badge",
        "what": "The winged-hexagon badge in a cyan ring that sits just above the ability row while Death Defied (Second Wind) holds; the rescue rules fire Demon Mode when it clears. The template is the glyph only - the ring is a countdown that erodes.",
        "where": "Battle HUD, above the Nuke / Demon Mode row, for the seconds after the tower would have died.",
        "how": "Automatic: Full setup's top-tier battle tries it as the tower dies, and 'Watch the screen' on Calibrate catches it during your own run (the game's SecondWind artwork finds it). By hand, crop the glyph without the ring.",
        "art": ["SecondWind"]},
    "icons/tile_cart.png": {
        "label": "Cart tile (top bar)",
        "what": "The gold cart tile that opens the store, used by the free-gems flow.",
        "where": "In-run HUD top bar, left of the hamburger (Home shows a chevron and the Missions box there instead).",
        "how": "Automatic: Full setup's Tier-1 run cuts it at its fixed position (gold outline); or 'Capture from the screen now' during a run.",
        "art": ["shopping-cart"]},
    "buttons/menu_closed_tile.png": {
        "label": "Hamburger (side menu shut)",
        "what": "The three-bar tile that opens the side menu; the runner taps it so the reward tiles are reachable during runs.",
        "where": "In-run HUD top bar, right corner, while the side menu is shut.",
        "how": "Automatic: Full setup's Tier-1 run cuts it (three white bars at the fixed position), then taps it to read the menu.",
        "art": ["MenuIcon", "menu-50"]},
    "buttons/menu_collapsed.png": {
        "label": "Side menu X (menu open)",
        "what": "The green X shown in the hamburger's place while the side menu is open.",
        "where": "In-run HUD top bar, right corner, while the side menu is open.",
        "how": "Automatic: Full setup's Tier-1 run cuts it after opening the side menu.",
        "art": ["close", "MenuClose"]},
    "icons/intro_sprint.png": {
        "label": "Intro sprint icon",
        "what": "The small fast-forward icon in the top-left corner while the intro sprint is running.",
        "where": "Battle HUD, top-left, in the first waves of a run.",
        "how": "Full setup captures it at the start of its own battle.",
        "art": ["sprint", "fastforward", "fast_forward"]},
    "icons/game_stats.png": {
        "label": "GAME STATS header",
        "what": "The 'GAME STATS' title of the results screen after a run ends.",
        "where": "After the tower dies or the run is surrendered.",
        "how": "Full setup captures it by ending its own battle.",
        "art": []},
    "buttons/retry.png": {
        "label": "RETRY button",
        "what": "The RETRY button on the GAME STATS results screen (restarts a run at the same tier).",
        "where": "GAME STATS screen, bottom row, left.",
        "how": "Full setup captures it by ending its own battle.",
        "art": []},
    "home/game_stats_home.png": {
        "label": "HOME button on results",
        "what": "The HOME button on the GAME STATS results screen.",
        "where": "GAME STATS screen, bottom row, right.",
        "how": "Full setup captures it by ending its own battle.",
        "art": []},
    "buttons/return_to_game.png": {
        "label": "RETURN TO GAME",
        "what": "The 'RETURN TO GAME' button on the pause / side menu during a run.",
        "where": "In-run side menu (top-right toggle).",
        "how": "Full setup captures it from its own battle.",
        "art": []},
    "buttons/end_round.png": {
        "label": "END ROUND button",
        "what": "The END ROUND (or EXIT BATTLE on low tiers) control on the in-run side menu.",
        "where": "In-run side menu.",
        "how": "Full setup captures it when it ends its own battle.",
        "art": []},
    "home/surrender.png": {
        "label": "Surrender button",
        "what": "The SURRENDER button on the EXIT BATTLE dialog (the one that really ends a run; Go Home only hides it).",
        "where": "After END ROUND / EXIT BATTLE on the side menu.",
        "how": "Full setup captures it when it ends its own battle.",
        "art": []},
    "home/end_round_dialog.png": {
        "label": "END ROUND dialog",
        "what": "The 'END ROUND - Are you sure?' confirmation.",
        "where": "After Surrender, on high tiers.",
        "how": "Full setup captures it when it ends its own battle.",
        "art": []},
    "home/end_round_yes.png": {
        "label": "END ROUND - Yes",
        "what": "The YES button on the END ROUND confirmation.",
        "where": "END ROUND dialog.",
        "how": "Full setup captures it when it ends its own battle.",
        "art": []},
    "home/exit_battle_dialog.png": {
        "label": "EXIT BATTLE dialog",
        "what": "The 'EXIT BATTLE - What would you like to do?' dialog with Surrender / Go Home.",
        "where": "After END ROUND / EXIT BATTLE on the side menu.",
        "how": "Full setup captures it when it ends its own battle.",
        "art": []},
    "home/intro_sprint_dialog.png": {
        "label": "Intro sprint dialog",
        "what": "The 'end the intro sprint?' prompt shown when you tap the sprint icon.",
        "where": "Battle HUD, first waves of a run.",
        "how": "Full setup captures it at the start of its own battle.",
        "art": []},
    "home/intro_sprint_yes.png": {
        "label": "Intro sprint - Yes",
        "what": "The YES button on the intro sprint prompt (ends the sprint).",
        "where": "Intro sprint dialog.",
        "how": "Full setup captures it at the start of its own battle.",
        "art": []},
    "buttons/reward_skip.png": {
        "label": "Reward SKIP button",
        "what": "The SKIP button under a reward animation (chest / ad reward).",
        "where": "Whenever a reward animation plays after a run or a claim.",
        "how": "Crop it the next time a reward animation shows - it cannot be produced on demand.",
        "art": ["skip", "Skip"]},
    "home/dissonant_run.png": {
        "label": "Dissonant run marker",
        "what": "The marker the Home screen shows when a Dissonant (achievement) run is armed.",
        "where": "Home screen, near the BATTLE button.",
        "how": "Full setup captures it from Home.",
        "art": ["dissonant", "Dissonant"]},
    "home/battle_btn.png": {
        "label": "BATTLE button",
        "what": "The big BATTLE button on Home that starts a normal run.",
        "where": "Home screen, centre.",
        "how": "Full setup captures it from Home.",
        "art": []},
    "home/tile_guild.png": {
        "label": "Guild tile",
        "what": "The Guild icon on the Home screen's left column.",
        "where": "Home screen, left icon column.",
        "how": "Full setup captures it from Home.",
        "art": ["Guild Icon"]},
    # ---- ultimate weapons: the in-run panel's UW tab
    "uw/toggle_on.png": {
        "label": "UW toggle - ON",
        "what": "The green ON state of the switch next to an Ultimate Weapon's name in the in-run panel.",
        "where": "During a run: open the bottom panel, ULTIMATE WEAPONS tab - each owned weapon has a switch.",
        "how": "Full setup cuts it from the UW tab with the installed artwork during its Tier 1 battle (one weapon must be ON). By hand: crop one ON switch (just the pill). The runner reads this to know a weapon's state before toggling it.",
        "art": ["switch-on"]},
    "uw/toggle_off.png": {
        "label": "UW toggle - OFF",
        "what": "The grey OFF state of the switch next to an Ultimate Weapon's name in the in-run panel.",
        "where": "During a run: bottom panel, ULTIMATE WEAPONS tab.",
        "how": "Full setup cuts it from the UW tab with the installed artwork during its Tier 1 battle (one weapon must be OFF). By hand: crop one OFF switch (just the pill).",
        "art": ["switch-off"]},
    "uw/chronofield_grant.png": {
        "label": "Chrono Field (perk grant) label",
        "what": "The Chrono Field name label as the UW panel renders it while a Tier-1 perk GRANTS the weapon for one run (an extra row, styled like the owned ones).",
        "where": "During a run that took the UW-grant perk: bottom panel, ULTIMATE WEAPONS tab.",
        "how": "The opt-in UW lottery pass / the quest runner captures it when a grant shows; by hand, crop the label while the grant is active.",
        "art": ["weapon_chronoField"]},
    # ---- menus and recognition guards
    "screens/hdr_battle.png": {"label": "BATTLE header", "what": "The word BATTLE as the Home screen's section header.", "where": "Home screen, top.", "how": "Full setup captures it.", "art": []},
    "screens/hdr_cards.png": {"label": "CARDS header", "what": "The CARDS screen title.", "where": "Cards menu, top.", "how": "Full setup captures it.", "art": []},
    "screens/hdr_guild.png": {"label": "GUILD header", "what": "The GUILD screen title.", "where": "Guild menu, top.", "how": "Full setup captures it.", "art": []},
    "screens/hdr_modules.png": {"label": "MODULES header", "what": "The MODULES screen title.", "where": "Modules menu, top.", "how": "Full setup captures it.", "art": []},
    "screens/hdr_tournament.png": {"label": "TOURNAMENT header", "what": "The TOURNAMENT screen title.", "where": "Tournament screen (trophy tile on Home).", "how": "Crop it when you open the tournament screen yourself; setup never enters tournaments.", "art": []},
    "home/tourney_open_dialog.png": {"label": "Tournament open popup", "what": "The popup announcing a tournament is open.", "where": "Home, when a tournament window opens.", "how": "Crop it when it appears.", "art": []},
    "home/welcome_back_dialog.png": {"label": "Welcome back dialog", "what": "The offline-earnings 'Welcome back' dialog.", "where": "On returning to the game after time away.", "how": "Crop it when it appears.", "art": []},
    "home/welcome_back_resume.png": {"label": "Welcome back - resume", "what": "The resume / claim button on the Welcome back dialog.", "where": "Welcome back dialog.", "how": "Crop it when it appears.", "art": []},
    "tourney/tournament_stats.png": {"label": "Tournament stats", "what": "The tournament results / stats screen title.", "where": "After a tournament run.", "how": "Crop it when it appears.", "art": []},
    "tourney/buy_ticket_title.png": {"label": "Buy ticket dialog", "what": "The tournament ticket purchase dialog title.", "where": "Tournament screen, when entering.", "how": "Crop it when you see it; never let setup tap it.", "art": []},
    "tourney/heat_tabs.png": {"label": "Tournament heat tabs", "what": "The heat / bracket tabs on the tournament screen.", "where": "Tournament screen.", "how": "Crop when the screen is open.", "art": []},
    "tourney/ticket_claim.png": {"label": "Ticket claim", "what": "The free tournament ticket claim control.", "where": "Tournament screen.", "how": "Crop when it appears.", "art": []},
    "tourney/in_tournament.png": {"label": "In-tournament marker", "what": "The HUD element that shows a run is a tournament run.", "where": "Battle HUD during a tournament run.", "how": "Crop during a tournament run you started yourself.", "art": []},
    "cards/active_label.png": {"label": "Card 'Active' label", "what": "The ACTIVE label on the card preset tabs.", "where": "Cards menu, preset tabs.", "how": "Full setup captures it.", "art": []},
    "presets/picker_icon.png": {"label": "Preset picker icon", "what": "The icon that opens the global preset picker.", "where": "Home screen, bottom-right.", "how": "Full setup captures it.", "art": ["GlobalPresets", "presets"]},
    "presets/picker_icon_large.png": {"label": "Preset picker icon (large)", "what": "The larger variant of the preset picker icon some layouts render.", "where": "Home screen, bottom-right.", "how": "Alternative to the normal picker icon - one of the two is enough.", "art": ["GlobalPresets", "presets"]},
    "presets/close_x.png": {"label": "Picker close X", "what": "The X that closes the preset picker.", "where": "Preset picker dialog.", "how": "Full setup captures it.", "art": ["close", "close-50"]},
    "presets/select_header.png": {"label": "Picker header", "what": "The preset picker's SELECT header.", "where": "Preset picker dialog.", "how": "Full setup captures it.", "art": []},
    "presets/gp_none.png": {"label": "Preset 'None' row", "what": "The 'None' entry of the global preset list.", "where": "Preset picker dialog.", "how": "Full setup captures it.", "art": []},
    "stats/max_label.png": {"label": "MAX label", "what": "The MAX marker shown on a maxed workshop stat.", "where": "In-run panel, workshop tabs.", "how": "Crop it from a maxed stat row during a run.", "art": []},
    "icons/premium_store.png": {"label": "Store icon", "what": "The premium store icon on Home.", "where": "Home screen.", "how": "Full setup captures it.", "art": []},
    "icons/free_gems.png": {"label": "Free gems", "what": "The store's free gems (ad) tile.", "where": "Store screen.", "how": "Full setup captures it from the Store.", "art": []},
    "buttons/gem_claim.png": {"label": "Gem claim", "what": "The CLAIM button on a free gems tile.", "where": "Store screen.", "how": "Full setup captures it from the Store.", "art": []},
    "icons/daily_missions.png": {"label": "Missions header", "what": "The 'Missions' header text the quests flow reads to confirm the Missions screen opened.", "where": "Missions screen (side menu > quests tile), top.", "how": "Crop the header text on the Missions screen.", "art": []},
    "icons/tile_quests.png": {"label": "Quests tile (side menu)", "what": "The checkbox tile in the side menu that opens the daily missions; a red number badge on it means rewards wait. The quests flow taps it.", "where": "In-run side menu column on the right, after opening the hamburger (during a run, not on Home).", "how": "Automatic: Full setup's Tier-1 run opens the side menu and finds it with the game's MissionsIcon artwork; or open the menu during your run and use 'Capture from the screen now'.", "art": ["MissionsIcon"]},
    "buttons/quest_claim.png": {"label": "Quest claim", "what": "The CLAIM button on a completed quest / mission.", "where": "Missions screen.", "how": "Crop when a mission is claimable.", "art": []},
    "icons/event_calendar.png": {"label": "Event calendar", "what": "The events icon on Home.", "where": "Home screen.", "how": "Full setup captures it.", "art": ["event icon"]},
    "icons/event_missions_tab.png": {"label": "Event missions tab", "what": "The MISSIONS tab on the event screen.", "where": "Event screen.", "how": "Full setup captures it from Events.", "art": []},
    "icons/guild_header.png": {"label": "Guild header", "what": "The guild screen header.", "where": "Guild menu.", "how": "Full setup captures it.", "art": []},
    "icons/guild_coin.png": {"label": "Guild coin", "what": "The guild coin claim icon.", "where": "Guild menu.", "how": "Full setup captures it.", "art": []},
    "buttons/guild_members_tab.png": {"label": "Guild members tab", "what": "The MEMBERS tab on the guild screen.", "where": "Guild menu.", "how": "Full setup captures it.", "art": []},
    "modules/buy_module.png": {"label": "Buy module", "what": "The buy-module control on the Modules screen.", "where": "Modules menu.", "how": "Full setup captures it.", "art": []},
    "modules/v29_dialog_close.png": {"label": "Module dialog close", "what": "The close control of a module's detail dialog.", "where": "Modules menu, module dialog.", "how": "Calibrate (Modules) captures it.", "art": []},
    "modules/v29_equip_btn.png": {"label": "Equip button", "what": "The EQUIP button in a module's dialog.", "where": "Modules menu, module dialog.", "how": "Calibrate (Modules) captures it.", "art": []},
    "modules/primary_btn.png": {"label": "Primary slot (prompt)", "what": "The PRIMARY button of the slot prompt some game versions show after Equip. v29 equips by category, so no run needs this; only the Modules scan's restore step may see it.", "where": "Modules menu, the Primary/Assist prompt after Equip (not the Assist tab).", "how": "Automatic: the Modules scan cuts it when the prompt appears while restoring your loadout.", "art": []},
    "modules/assist_btn.png": {"label": "Assist slot (prompt)", "what": "The ASSIST button of the slot prompt some game versions show after Equip. v29 equips by category, so no run needs this; only the Modules scan's restore step may see it.", "where": "Modules menu, the Primary/Assist prompt after Equip (not the Assist tab).", "how": "Automatic: the Modules scan cuts it when the prompt appears while restoring your loadout.", "art": []},
    "modules/transfer_yes.png": {"label": "Transfer - Yes", "what": "The YES on the module transfer confirmation.", "where": "Modules menu.", "how": "Calibrate (Modules) captures it.", "art": []},
    "dialogs/dissonant_header.png": {"label": "Dissonance dialog header", "what": "The header of the Dissonance (achievement run) dialog.", "where": "Home, dissonant run dialog.", "how": "Open the dialog and crop.", "art": []},
    "dialogs/dissonant_battle.png": {"label": "Dissonance BATTLE", "what": "The BATTLE button inside the Dissonance dialog.", "where": "Dissonant run dialog.", "how": "Open the dialog and crop.", "art": []},
    "dialogs/dissonant_x.png": {"label": "Dissonance close", "what": "The X that closes the Dissonance dialog.", "where": "Dissonant run dialog.", "how": "Open the dialog and crop.", "art": []},
}

FOLDERS = {
    "digits": ("Wave counter digit", "One digit of the wave counter font on the battle HUD.", "Battle HUD, wave counter.",
               "Full setup reads them from a Tier 1 run automatically.", []),
    "uw": ("Ultimate Weapon name", "The name label of an Ultimate Weapon in the in-run panel's UW tab.",
           "During a run: bottom panel, ULTIMATE WEAPONS tab.", "Full setup captures the owned weapons' labels.", []),
    "floaters": ("Flying gem", "A gem floating over the field during a run (one appearance variant).",
                 "Battle HUD, over the field.", "Crop one when it floats by; each variant is a separate image.", ["gem", "Gem"]),
    "presets": ("Preset row", "A named preset row in the preset picker (this account's own name).",
                "Preset picker dialog.", "Calibrate (Presets) cuts it from your picker.", []),
    "cards": ("Card preset tab", "One of this account's card preset tabs.", "Cards menu.", "Calibrate (Cards) cuts it.", []),
    "modules": ("Module icon", "The icon of one of this account's modules.", "Modules menu.", "Calibrate (Modules) cuts it.", []),
    "stats": ("Workshop stat label", "The label of a workshop stat in the in-run panel.", "In-run panel, workshop tabs.",
              "Crop it from the panel during a run.", []),
    "dialogs": ("Dialog control", "A control on an in-game dialog.", "See the run's flow.", "Crop it when the dialog shows.", []),
    "tourney": ("Tournament control", "A control on the tournament screen.", "Tournament screen.", "Crop it when you open the screen yourself.", []),
}


# Artwork names for icon targets the cards above do not spell out (the
# installed game's sprite names, matched case-insensitively).
ART = {
    "buttons/perks.png": ["PerkButton", "button_perk", "icon_perk"],
    "buttons/gem_claim.png": ["gem"],
    "buttons/menu_closed_tile.png": ["MenuIcon", "menu-50"],
    "buttons/menu_collapsed.png": ["MenuIcon", "menu-50"],
    "icons/free_gems.png": ["adGemStack3x", "2xAdGemStack", "gem"],
    "icons/premium_store.png": ["Store"],
    "icons/daily_missions.png": ["MissionsIcon"],
    "icons/tile_quests.png": ["MissionsIcon"],
    "icons/event_calendar.png": ["event icon"],
    "icons/guild_header.png": ["Guild Icon"],
    "icons/guild_coin.png": ["coin"],
    "icons/chest_lock.png": ["lock"],
    "icons/tile_cart.png": ["shopping-cart"],
    "icons/tile_guild.png": ["Guild Icon"],
    "icons/intro_sprint.png": ["Sprint"],
    "home/tile_event.png": ["event icon"],
    "home/dissonant_run.png": ["dissonance-icon"],
    "dialogs/dissonant_x.png": ["close", "MenuClose"],
    "overlays/ad_close_x.png": ["closeRed", "close"],
    "tourney/trophy_tile.png": ["trophy"],
    "tourney/heat_icon.png": ["heat", "heatBordered"],
    "tourney/heat_title.png": ["heat"],
    "tourney/ticket_claim.png": ["Ticket_tournament"],
    "tourney/buy_ticket_title.png": ["Ticket_tournament"],
    "tourney/ticket_skip.png": ["Ticket_tournament"],
    "presets/picker_icon.png": ["GlobalPresets", "presets"],
    "presets/picker_icon_large.png": ["GlobalPresets", "presets"],
    "presets/close_x.png": ["close", "close-50"],
    "modules/v29_dialog_close.png": ["close", "close-50"],
    "guardian/equipped_check.png": ["Checkmark", "icon_check"],
    "uw/black_hole.png": ["weapon_blackHole"],
    "uw/chain_lightning.png": ["weapon_chainLightning"],
    "uw/chronofield.png": ["weapon_chronoField"],
    "uw/chronofield_grant.png": ["weapon_chronoField"],
    "uw/death_wave.png": ["weapon_deathWave"],
    "uw/golden_tower.png": ["weapon_goldenTower"],
    "uw/inner_land_mines.png": ["weapon_landMines"],
    "uw/smart_missiles.png": ["weapon_smartMissilies", "weapon_smartMissiles"],
    "uw/spotlight.png": ["weapon_spotlight"],
    "uw/poison_swamp.png": ["weapon_swamp"],
    "floaters/second_wind.png": ["SecondWind"],
}
for _rel in ("gem_a", "gem_b", "gem_c", "gem_v29_orbit", "gem_v29_orbit_b", "gem_v29_orbit_b_lr", "gem_v29_orbit_b_ul", "gem_v29_orbit_b_ul2"):
    ART[f"floaters/{_rel}.png"] = ["gem"]

# Controls the game renders as TEXT on a generic button: there is no sprite
# to show, so the dashboard draws the expected words as a stand-in badge.
TEXT = {
    "buttons/retry.png": "RETRY", "buttons/return_to_game.png": "RETURN TO GAME", "buttons/end_round.png": "END ROUND",
    "buttons/exit_battle.png": "EXIT BATTLE", "buttons/more_stats.png": "MORE STATS", "buttons/reward_skip.png": "SKIP",
    "buttons/quest_claim.png": "CLAIM", "buttons/gem_claim.png": "CLAIM", "buttons/store_owned.png": "OWNED",
    "buttons/event_bots_tab.png": "BOTS", "buttons/guild_members_tab.png": "MEMBERS", "buttons/perks.png": "PERKS",
    "home/battle_btn.png": "BATTLE", "home/surrender.png": "SURRENDER", "home/end_round_yes.png": "YES",
    "home/intro_sprint_yes.png": "YES", "home/end_round_dialog.png": "END ROUND", "home/exit_battle_dialog.png": "EXIT BATTLE",
    "home/intro_sprint_dialog.png": "INTRO SPRINT", "home/game_stats_home.png": "HOME", "icons/game_stats.png": "GAME STATS",
    "home/welcome_back_dialog.png": "WELCOME BACK", "home/welcome_back_resume.png": "RESUME", "home/tourney_open_dialog.png": "TOURNAMENT",
    "home/speed_x5_mask.png": "x5.0", "icons/event_missions_tab.png": "MISSIONS", "icons/store_cosmetics.png": "COSMETICS",
    "icons/store_currencies.png": "CURRENCIES", "icons/store_relics.png": "RELICS", "icons/store_tower_guardian.png": "GUARDIAN",
    "icons/title_background.png": "BACKGROUND", "icons/title_banner.png": "BANNER", "icons/title_guardian.png": "GUARDIAN",
    "icons/title_menu.png": "MENU", "icons/title_tower.png": "TOWER",
    "dialogs/dissonant_header.png": "DISSONANCE", "dialogs/dissonant_battle.png": "BATTLE", "dialogs/dissonant_tile_utility.png": "UTILITY",
    "presets/select_header.png": "SELECT", "presets/gp_none.png": "None",
    "modules/buy_module.png": "Buy Module", "modules/equip_btn.png": "EQUIP", "modules/v29_equip_btn.png": "EQUIP",
    "modules/primary_btn.png": "PRIMARY", "modules/assist_btn.png": "ASSIST", "modules/transfer_yes.png": "YES",
    "modules/shatter_dialog.png": "SHATTER", "modules/shatter_rare_text.png": "RARE",
    "guardian/slot_empty.png": "EMPTY", "guardian/tab_guardian.png": "GUARDIAN",
    "tourney/header.png": "TOURNAMENT", "screens/hdr_tournament.png": "TOURNAMENT", "tourney/tournament_stats.png": "TOURNAMENT STATS",
    "tourney/tournament_stats_ok.png": "OK", "tourney/leaderboard.png": "LEADERBOARD", "tourney/in_tournament.png": "TOURNAMENT",
    "tourney/battle_btn.png": "BATTLE", "tourney/battle_btn_preset.png": "BATTLE", "tourney/battle_btn_preset_tryagain.png": "TRY AGAIN",
    "tourney/battle_btn_rerun.png": "RERUN", "tourney/buy_ticket_cancel.png": "CANCEL", "tourney/buy_ticket_video.png": "WATCH AD",
    "tourney/heat_tabs.png": "HEAT", "overlays/return_to_game_bar.png": "RETURN TO GAME",
    "screens/hdr_battle.png": "BATTLE", "screens/hdr_cards.png": "CARDS", "screens/hdr_guild.png": "GUILD", "screens/hdr_modules.png": "MODULES",
    "cards/active_label.png": "Active", "stats/max_label.png": "Max", "uw/toggle_on.png": "ON", "uw/toggle_off.png": "OFF",
    "valuefont/dollar.png": "$", "valuefont/dot.png": ".",
}


def text_for(rel: str) -> str | None:
    """The words a text-rendered control shows, or None for a pure icon."""
    if rel in TEXT:
        return TEXT[rel]
    folder, _, name = rel.partition("/")
    stem = name[:-4] if name.endswith(".png") else name
    if folder in ("digits", "valuefont", "storefont"):
        return stem
    if folder == "stats":
        return stem.replace("_", " ").title()
    if folder == "screens" and stem.startswith("hdr_"):
        return stem[4:].replace("_", " ").upper()
    return None


def describe(rel: str) -> dict:
    """The plain-language card for `rel` (always returns something)."""
    if rel.startswith("config:"):
        return {"rel": rel, "label": rel.split(":", 1)[1].strip(), "what": "A configuration value, not an image.",
                "where": "config.yaml", "how": "Full setup detects it; or set it in Configuration.", "art": [], "text": None}
    card = DOCS.get(rel)
    if card:
        card = dict(card, rel=rel)
        if not card.get("art") and rel in ART:
            card["art"] = list(ART[rel])
        card["text"] = text_for(rel)
        return card
    folder, _, name = rel.partition("/")
    stem = re.sub(r"\.png$", "", name).replace("_", " ")
    label, what, where, how, art = FOLDERS.get(folder, (stem, "A recognition image the runner matches on screen.",
                                                      "See the scan plan step it belongs to.", "Crop it in Calibrate.", []))
    return {"rel": rel, "label": f"{label}: {stem}" if folder in FOLDERS else stem,
            "what": what, "where": where, "how": how, "art": list(ART.get(rel) or art),
            "text": text_for(rel)}


def artwork_names(rel: str) -> list[str]:
    return list(describe(rel).get("art") or [])
