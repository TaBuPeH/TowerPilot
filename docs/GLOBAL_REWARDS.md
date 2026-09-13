# Global reward behaviors

Global behaviors are shared by all run types, under `policies.global_behaviors`:

- `free_store_gems`: one confirmed Store claim per UTC day, scheduled at 01:00 UTC plus a stable daily random offset of up to ten minutes. If automation was offline, the next active run catches up. Only a visible Claim Rewards button on the free-gems card is clicked; observing the resulting cooldown records success. This is independent of HUD Ad Gems and rotating diamonds.
- `daily_missions`: inspect the Daily Missions badge every five minutes during a run; collect completed daily cards and weekly chests.
- `event_missions`: inspect the separate Event badge every five minutes. Locate complete quest-card rectangles, verify Claim inside each card, scroll the list, and then return to the top to collect available free and already-unlocked paid-track rewards. No boost or shop purchase is authorized by this switch.
- `guild_progress`: inspect the Guild badge every five minutes; collect only visibly claimable weekly progress boxes.
- `demon_mode_always`: fire Demon Mode whenever its button reads ready, on every run, whatever the rescue policy does. For the Demon Mode kill quests: a Demon Mode held back for a wall rescue earns no kills. It never fires during the intro sprint (the sprint locks the ability row), every tap is confirmed by the button's cooldown dim, and attempts are spaced 15 seconds apart. The trade-off is a rescue that may find the button cooling down. Requires `buttons/demon_mode.png` and an account that owns Demon Mode.

The independent clocks persist across runner restarts and are keyed by account. Checks wait for active shopping or another reward flow to finish. Global Daily Missions and Guild switches supersede their legacy per-run gathering flags. The UI saves changes through the normal profile editor; an already-running worker loads them on its next start.

Game artwork and captures remain account-local. `bootstrap_manifest.json` contains only names, regions, source-crop metadata and control meanings. The Event menu star is identified from the extracted calendar artwork inside the side-menu region; its row is not assumed.

Live verification on MuMu, 2026-09-10: five event quest claims and Galactic Beverage relic; free Store gems increased the balance by 20; Guild progress went from one claimable box to zero. Daily Missions had no completed cards and correctly supplied no claim target. Paid-track claims were unavailable on this account and were tested with synthetic text cases only. UTC timing and separate five-minute clocks have automated tests.
