# Tower Pilot: current focus

Updated 2026-10-26. This file is the single place that says what is being worked
on now, in what order, and what "done" means. Older plans and review evidence live
under the git-ignored `backend/logs/reviews/`; this file supersedes their ordering.

## Goal for this stretch

You can tell from the Dashboard alone that gems are being collected, and when one
is missed you can see that it was missed and why. The live preview is on whenever
the emulator is already up, and the dashboard keeps itself running.

## Order of work

One change at a time, smallest first. Items 1-12 below were committed to
`feat/dashboard-polish-20261020` and merged into `master` with PR #9 on 2026-10-26.

## How changes land from now on (2026-10-26)

- One feature, one branch off `master`, one pull request. The branch is named for the feature
  (`feat/...`, `fix/...`, `test/...`, `ci/...`, `docs/...`).
- A pull request merges as soon as its feature meets the definition of done below and CI is green.
  Small fixes found during review go into the same pull request; unrelated work gets its own.
- Size: well under 100,000 changed lines, fixtures included; aim for a few thousand. If a change
  would regenerate large recorded fixtures, land a fixture split or compaction as its own pull
  request first, or split the feature.
- At most two pull requests open at once, and only if they touch different files.
- Live changes (collector, dashboard) are deployed after merge, then checked with `tools/gem_health.py`.

| # | Change | Why | Status |
|---|---|---|---|
| 1 | Ad gem delay: keep the original delay when an offer moves, and clamp it to the time left in the window (person approved) | 2 of the 3 missed ad windows after r15d | done 5bc75004; live once the collector restarts between battles |
| 2 | Dashboard supervision: single-instance lock, restart when it dies, no landing kills the collector | 2 h 55 min of lost harvesting on 10-24 | 2a done f92ab0ae (live); 2b done d6da4246 (start report; live at next restart); 2c done (tools/collector_restart.py: safe-point exit, relaunched by harvest) |
| 3 | Gem outcomes on the Dashboard: confirmations seen, misses, and a cause line, all from the backend | "Collecting gems" today only means a collector is running | done; live at next restart |
| 4 | Gem-health signal: warn when frames arrive but the recognizer matches nothing | silent recognizer failures | done (backend; finder_suspected published, Dashboard line later); live at next collector restart |
| 5 | Live preview starts by itself when the emulator is already available, and reattaches when it returns; never starts the emulator | person's decision | done (signal = device online; live after the next UI rebuild) |
| 6 | Gem regression test that needs no screenshots | three broken orbit versions pass every test today | done 2b93e993 |
| 6b | Orbit taps 2-3 aim on a fresh frame captured after the tap-rate wait (council #3b) | taps were sent on a frame up to 0.7 s old | done (orbit fix commit); live at next collector restart |
| 7 | Hard rule 4 fix: the day plan must not tap over the person's screen (item 7 #10) | safety gap | done; live for the next day-plan run |

Source for 2-6: `backend/logs/reviews/quality-council-20261024/synthesis.md`, items 1-6.

## Next stretch (started 2026-10-26)

All seven items above are done and live. The next items, in order, one or two at a time:

| # | Change | Why | Status |
|---|---|---|---|
| 8 | Tests that defend hard rule 2 (tournament refusals in tourney.end_round, ensure_home, shard.abandon_run) | removing those refusals leaves every test green today (council #9a, BC-02) | done |
| 9 | CI gate on the fork: backend unit, contract drift, frontend typecheck/lint/unit, run first as non-required | the branch has no CI runs at all (council #8) | done; first run found 2 machine-only passes (live MuMu port in 12 tests, local time zone in the actions dump), fixed in b029eba3; green on a11b618c |
| 10 | Honest labels: a collector launch is unconfirmed until it reports; battle ad reads "offer gone after tap" | the history claims more than it knows (council #7) | done (69cb4f6d). Open follow-up: the Run now acknowledgement still carries the collector's own record (`confirmed: true`, reason offer_disappeared) in the Command evidence disclosure |
| 11 | Faster gem loop: the collector's single thread slows to one pass per ~6 s, so ad offer readings go stale (observations_lapsed, also before today's changes) | missed offers (council #5e, GC-07) | done (a8d4fb80, live 10:31): survives ledger locks, per-pass timers (gem_collector_stages), one read per frame, 1 s pace. Open: partial session writes not retried; backwards-wave lock loses previous link |
| 12 | Resume paused roadmap item 7 (r18e main-loop timers, r18h tests, F-1 latency gate port), then items 3, 9, 10 | roadmap | F-1 done (ce21f709); r18e done (0555570b: rule/demon cooldowns and UW backoff on catalog schedules, one history row per contact); r18h foundation tests next; combo handoff landed as #7 |

## Pull request queue (from 2026-10-26)

Each row is one pull request, in order. Paused state for items 3, 9 and 10 is in
`backend/logs/reviews/roadmap-item3-build/STATE.md`, `roadmap-item9-*/STATE.md` and `item10-p10*/STATE.md`.

| PR | Feature | Status |
|---|---|---|
| ci/pr-workflow | CI on pull requests to master; these rules | open |
| feat/action-lifecycle-foundation | Item 7 r18h: schedules refuse an unreadable clock; lifecycle tests (clock, restart, concurrency, coalescing, compat, history readback) | in progress |
| fix/run-now-ack-evidence | #10 follow-up: the Run now acknowledgement stops saying confirmed for a vanished offer | |
| fix/collector-partial-writes | #11 follow-ups: retry a partly written finish/prepare; keep the previous-record link after a backwards-wave lock | |
| feat/takeover-t3b | Item 3 T3b (three open Astra findings first) | |
| feat/takeover-t3c | Item 3 T3c | |
| feat/takeover-u3 | Item 3 U3 | |
| feat/item9-p9f, -p9a, -p9b | Item 9 packages, one pull request each (P9-F approved) | |
| docs/item10-p10e, feat/item10-p10a, -p10d | Item 10 packages, one pull request each | |

Needs the person: item 1 (a fresh live run), items 4 and 5 (visual acceptance), item 6 (a live run with
shopping on), and the ad gem offer that has not appeared since 08:05 on 10-26.

## Definition of done for one change

1. The change is small and has one purpose.
2. A test that fails before the change and passes after it, or a stated reason none is possible.
3. The focused tests pass, and the full backend suite passes (`python -m pytest backend/tests -q`)
   when the change touches shared code. Frontend changes run typecheck, lint and the affected tests.
4. Recorded fixtures that move are regenerated through their writers, and every moved value is explained.
5. One independent review of the final diff (Astra). Opus verifies only changes to the live
   collection path or safety rules.
6. Merged through its own pull request with CI green. The dashboard is restarted between battles when runtime files changed,
   and the result is checked on the live Dashboard.
7. Gem collection is checked after every change that reaches the live collector: run
   `python tools/gem_health.py` once the new collector has run a while and compare it with the previous
   run (saved under `backend/logs/reviews/gem-health/`). Ad and orbit successes must keep coming at their
   usual rate; a drop stops new work until it is explained or fixed.

No packages, zip files or landing scripts for ordinary changes.

## Working rules

- At most two changes in flight, and only if they touch different files.
- Never stop, restart or touch the emulator (MuMu) or adb. Only the dashboard and collector may be
  restarted, by root, between battles.
- Tests never reach live ports: `backend/tests/conftest.py` refuses connections to 5037, 8620 and 8630.
- The dashboard runs from the Scheduled Task "TowerPilot Dashboard" under a hidden console
  (`conhost.exe --headless ... python.exe backend/dashboard.py`).

## Paused (state saved, resume after this stretch)

| Item | Where its state is |
|---|---|
| 3 Tracked takeover (T3-0 and T3a landed; T3b, T3c, takeover UI remain) | `backend/logs/reviews/roadmap-item3-build/STATE.md` |
| 6 Reusable action module (needs one natural shopping sweep on a live run) | `backend/logs/reviews/roadmap-item6-declaration-audit/` |
| 7 Timer migration (r18d landed; r18e, r18h, F-1 port remain) | `backend/logs/reviews/roadmap-item7-*/STATE.md` |
| 9 Setup evidence and speed (P9-F approved; P9-A, P9-B remain) | `backend/logs/reviews/roadmap-item9-*/STATE.md` |
| 10 Packaging | `backend/logs/reviews/item10-*/STATE.md` |
| 1, 4, 5 | need the person: a fresh live run (1), visual acceptance (4, 5) |

## Open decisions for the person

From the council synthesis, section 5: whether golden test fixtures are exempt from hard rule 8,
whether to keep the strict schema layer (31% of the shipped JavaScript), whether recognition may use
native preview frames, how a leftover person's run differs from a scheduler handoff for surrender
rules, whether a learner may cut the ad claim template, and whether APK art may be a live matcher.
Decided: ad gem interval 10-11 min (600-660 s, farm run), tap cap 5/s, ad delay clamp yes, preview auto-start only when the emulator is up, MuMu
started by the person.