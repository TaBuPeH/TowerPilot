# Takeover golden answers

> **Status:** Active
> **Type:** Test fixtures
> **Created:** 2026-10-23
> **Updated:** 2026-10-24
> **Owner:** Roadmap item 3 (explicit tracked takeover), package T3-0

Recorded answers of the code as it was before the explicit takeover existed (checkout at HEAD f047c55, re-verified unchanged at HEAD 38e15ec after the `backend/dashboard.py` move and the test reorganization). The packages that add the takeover (T3a, T3b, T3c)
run the tests of `backend/tests/unit/test_takeover_golden_*.py` against these documents to show that ordinary, non-takeover behaviour did not change. Nothing here
touches a device, a process or a game image: every scenario runs over fakes and synthetic frames, and the recorded values hold no account data.

| Document | What it records |
|---|---|
| `dashboard_start.json` | `POST /api/control` action `start` through the real Flask route: with a collector (owner check, readiness, then guard and context check, the hold, the stop and release wait, the launch, `end_transfer`), without one, the identical retry, a changed body under the same key, every refusal that must leave the collector running, the stored request and the shape of the `battle_envelope`, the surviving harvest choice |
| `orchestrator_adoption.json` | `orchestrator.main()` over a battle already on screen: the startup calls in order, the death branch (stats, death rules, run flag, repeat check, RETRY or Home restart; with and without a pending `stop_after_run`), the confirmed backward-wave branch (old record finished, successor with a predecessor), the probation that holds small or unconfirmed regressions |
| `fleet_marks.json` | `scheduling/fleets.py` for each tier family and the legacy fallback, `orchestrator.marks()` and `run_tier()`, and what each of the five consumers of `marks()` does with them |
| `battle_session.json` | `BattleSession.from_environment`: the predecessor is linked only when `store.active()` returns it |
| `operation_worker.json` | The operation worker deletes the instance's `stop_after_run` flag for every `run` operation and for no other kind |
| `readiness_requirements.json` | `player/readiness.requirements` over a synthetic matrix of compiled runs (the flow registry and the perk templates are synthetic) |
| `screen_readers.json` | `vision/screen.in_tournament` with the template present and matching, present and not matching, missing and unreadable; `screen.identify` on a battle frame |

Each document has `recorded_on` (digests of the source files the answers come from, so a reader can tell which code recorded them), `rebased_on` (the later HEAD the unchanged answers
were re-verified on, with the digests of the pinned files there; `dashboard_start` was recorded on `frontend/dashboard.py`, now `backend/dashboard.py`) and `scenarios`. The files are written by
`backend/tests/support/fixture_json.py`; `test_takeover_golden_capture.py` fails when one is not that writer's text. The tests compare
and never write. A package that changes behaviour on purpose changes the answers it must change, by recording again on its code and reviewing the diff of the
document; a changed answer that the package did not mean to change is a regression.

Recording is an explicit developer command, skipped in every normal run:

    TP_REGENERATE_TAKEOVER_GOLDEN=1 python -m pytest backend/tests/unit/test_takeover_golden_capture.py

Run the whole file, not a subset. Recording twice on the same code gives identical files.
