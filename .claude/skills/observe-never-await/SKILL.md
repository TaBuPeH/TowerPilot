---
name: observe-never-await
description: >-
  Autonomous observation and interaction discipline. Triggers when progress depends on an
  observable condition — a log line, a data-file change, a process exit, a background
  run (build/CI/long script), an operator action, or an app state — and you are
  tempted to ask the user to watch something, click something, or "tell me when you're ready".
  Also triggers before any `sleep`-and-poll loop or any hand-off of a drivable interaction.
---

# Observe, Never Await

**When progress depends on an observable condition, never ask the user to watch for it, click for it, or announce readiness. Write a bounded background watcher that detects the condition yourself — and when the triggering interaction is drivable (API calls, CLI commands, automated inputs), drive it yourself too. The user hands you goals, not readiness signals.**

## Rules

1. **Observable condition → background watcher, not a question.** If the thing you're waiting for leaves a trace anywhere you can read — a log file, a data row, a growing output file, a process table, an HTTP probe — write a small script that polls for it and run it via `Bash` `run_in_background`. "Let me know when it's done" / "tell me when you're ready" / "ping me after you click" are violations when the condition is observable.
2. **Every watcher is bounded and tri-state.** Hard deadline + poll interval + THREE distinguishable exits: `CONDITION_MET` (success signature seen), `FAILURE_SIGNATURE` (known-bad pattern seen — watch for failure explicitly, not just success), `TIMEOUT` (deadline hit, state unknown). Print which one fired and the evidence line. An infinite loop or a success-only grep is forbidden.
3. **Watch the narrowest signal.** Grep for the entity-locked marker (the exact run ID, entity key, or log signature), never a broad log tail — broad watching mistakes unrelated concurrent activity for your condition (same failure mode as [verify-never-infer](../verify-never-infer/SKILL.md) Rule 3).
4. **Prefer harness-native waiting over polling.** Work the harness already tracks (`run_in_background` Bash tasks, background `Agent` runs) re-invokes you on completion — do NOT poll those. Watcher scripts are for signals the harness canNOT track: external service logs, data-file state, remote CI, another process's output file.
5. **Drive the interaction yourself when it's drivable.** An API/CLI trigger is a `Bash` call; an automatable UI action goes through the available automation surface. Hand an action to the user ONLY when it genuinely requires their credentials/2FA, a device you can't reach, judgment only they have, or an account you are barred from touching.
6. **When the user MUST act, start the watcher FIRST.** If only the operator can perform the step, arm the watcher for the resulting effect BEFORE telling them the single action needed — then they act whenever they act, and you detect it; they never have to report back or announce "I'm ready".
7. **No foreground sleeps, no sleep-chains.** `sleep 45 && check` in the main shell wastes the turn. Short sleeps live INSIDE the background watcher's loop; the main conversation continues or ends the turn while the watcher runs.
8. **Clean up.** Watchers exit on their own bound; kill any still-running watcher once its question is answered. Outputs go to the scratchpad or a temp dir, never into a repo.

## Decision table

| You're waiting for | Do this |
|---|---|
| Background Bash / Agent task you started | Nothing — the harness notifies you (Rule 4) |
| A service/process to log a signature / crash / come online | Watcher script grepping the log for the entity-locked line |
| A data-file / state change | Watcher looping an entity-locked read and testing the result |
| Another process's output file to grow / show success or hang | Watcher stat-ing the file + grepping success AND failure signatures |
| An app state after an automated action | Drive the action yourself, then assert the state from the observed output |
| An operator-only action (credential-gated) | Arm the watcher for the downstream effect FIRST, then tell the user the one action needed (Rule 6) |
| Remote CI / deploy the harness can't see | Watcher polling the status endpoint / `gh run view` at a sane interval |

## Anti-Patterns

| Wrong | Right | Why |
|---|---|---|
| "Let me know when it's done / when you've clicked it" | Watcher on the observable effect; drive the click yourself | The user becomes your polling loop — slowest, most expensive poller available |
| `sleep 45 && grep …` in the foreground | `run_in_background` script with the sleep inside its loop | Blocks the turn |
| Watcher greps only the success line | Grep success AND known failure signatures, tri-state exit | A hang or crash looks identical to "not done yet" — you wait the full deadline for nothing |
| `while true; do …; done` with no deadline | Hard deadline with `TIMEOUT` exit and evidence of last state | Unbounded watchers leak and never report the unknown-state case |
| Tail the whole log and eyeball it | Entity-locked grep (exact signature) | Broad watching matches unrelated concurrent activity |
| Poll a `run_in_background` task's output file every 30s | End the turn; the harness re-invokes you on completion | Polling harness-tracked work burns tokens for a notification you get free |
| Ask the user to run a command / click a button you could drive | Drive it yourself; hand over ONLY credential-gated actions — with the watcher already armed | Every avoidable hand-off adds a human round-trip to the loop |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Watcher always exits `TIMEOUT` though the condition happened | Signature too narrow / wrong file / wrong entity id | Reproduce the expected line once manually, copy it verbatim into the grep |
| Watcher fires `CONDITION_MET` on the wrong event | Signature too broad (not entity-locked) | Add the entity id / exact marker to the pattern |
| Two watchers report each other's output | Shared output file in scratchpad | One output file per watcher, named by task id |
| User acted but nothing detected | Watcher armed AFTER the action, or watching the wrong store | Arm before hand-off (Rule 6); watch the terminal store, not an intermediate |

## Verification

Before ending a turn that involves waiting:

1. No sentence in your reply asks the user to watch, poll, click, or announce readiness for something observable/drivable — if one does, replace it with a watcher or a driven interaction.
2. Every watcher you started states its deadline, poll interval, and all three exit signatures in its output header.
3. Harness-tracked background work has NO watcher on it.
4. When an operator action was required, the watcher was armed before the hand-off sentence.
5. Finished watchers are dead; their outputs live under the scratchpad / temp dir, not a repo.

## See Also

- [reference.md](reference.md) — watcher script template
- [verify-never-infer](../verify-never-infer/SKILL.md) — the sibling discipline: this skill gets you the evidence without asking; that one forbids claiming without it
