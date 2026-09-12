# Observe, Never Await — Reference

Sidecar for [SKILL.md](SKILL.md): the watcher script template and a worked example.

## Watcher script template (bash, run via `run_in_background`)

Every watcher follows this shape — bounded, tri-state, entity-locked, self-describing:

```bash
#!/usr/bin/env bash
# WATCHER: <what condition, for which entity>
# deadline: <N>s · poll: <M>s · exits: CONDITION_MET | FAILURE_SIGNATURE | TIMEOUT
TARGET="<file / log source / probe command>"
SUCCESS_SIG="<entity-locked success pattern>"     # e.g. "run_id=abc123 .* status=ok"
FAILURE_SIG="<entity-locked failure pattern>"     # e.g. "crash-loop|Traceback|ECONNREFUSED"
DEADLINE=150; POLL=5; START=$(date +%s)

while true; do
  ELAPSED=$(( $(date +%s) - START ))
  if grep -qE "$FAILURE_SIG" "$TARGET" 2>/dev/null; then
    echo "FAILURE_SIGNATURE after ${ELAPSED}s:"; grep -E "$FAILURE_SIG" "$TARGET" | tail -3; exit 2
  fi
  if grep -qE "$SUCCESS_SIG" "$TARGET" 2>/dev/null; then
    echo "CONDITION_MET after ${ELAPSED}s:"; grep -E "$SUCCESS_SIG" "$TARGET" | tail -3; exit 0
  fi
  if [ "$ELAPSED" -ge "$DEADLINE" ]; then
    echo "TIMEOUT after ${ELAPSED}s — last state:"; tail -5 "$TARGET" 2>/dev/null; exit 3
  fi
  sleep "$POLL"
done
```

Adaptation notes:

- **Log-source watchers** substitute `TARGET` reads with the log-dump command piped to grep (re-run per iteration — log files rotate).
- **Data-state watchers** replace the greps with an entity-locked query/read and test the result.
- **Probe watchers** (HTTP health, `gh run view`) put the probe command in the loop and match on its stdout.
- Output file (when the watcher itself produces one) goes to the session scratchpad or a temp dir, named by task: `temp/<topic>/watch-<slug>.out`.

## Worked example — background CLI run health-check

**Situation:** a long background CLI run had a known failure mode from its previous attempt — a sandbox issue hangs every spawned command, producing a run that LOOKS alive (stderr grows) but does no real work. Asking the operator to "watch it and tell me if it hangs" was the temptation; a foreground `sleep && grep` chain wastes the turn.

**Watcher:** a background script with a 150s deadline that each cycle (a) counted progress events in the run's stderr stream (progress = the count grows), and (b) grepped for the hang signatures from the failed run. Exits: growing progress count + no hang signature → healthy (`CONDITION_MET`); hang signature → kill and relaunch with the documented workaround (`FAILURE_SIGNATURE`); deadline with neither → report last state (`TIMEOUT`).

**Outcome:** the healthy signal arrived without a single human check-in or wasted foreground turn. The general lesson is Rule 2's tri-state: the watcher watched for the FAILURE signature explicitly — progress-only watching would have burned the full deadline before noticing a hang.

## Operator-action example (Rule 6 shape)

Wrong: "Do the manual step and tell me when it's done."

Right: arm first, then hand off —

1. Start a watcher on the downstream effect of the manual step, entity-locked to the expected identifier, with a freshness guard so old state can't satisfy it.
2. THEN say: "Do the step whenever you like — I'm watching for the effect and will continue on my own."
3. The watcher's `CONDITION_MET` (or `TIMEOUT`) drives the next step; the operator never reports back.
