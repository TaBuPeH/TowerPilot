---
name: verify-never-infer
description: >-
  Never present an unverified claim as a verified fact. Triggers on every factual
  statement to the user — values, counts, file paths, line numbers, behaviors,
  states, version numbers, port numbers, env vars, "X works" / "X returns Y" /
  "X is at Z" assertions, verification tables, audit cells, status reports, and
  any sentence whose truth depends on the current state of the codebase, data
  files, or running processes. Loads automatically; never opt-out.
---

# Verify, Never Infer

**Never present an unverified claim as a verified fact. For every factual statement, you must either (a) have a tool result in THIS session that proves it, or (b) explicitly prefix the statement with `UNVERIFIED:` / `INFERENCE:` / `EXPECTED (not checked):`. There is no third option.**

## Rules

1. **Evidence-or-flag, no third path.** Every value, count, ID, file path, line number, behavior, state, name, version, port, env var, or aggregate that you state as fact must trace to a tool call (`Read` / `Grep` / `Glob` / `Bash` / `mcp__*`) executed in the current session. If no such tool call exists, prefix the statement with `UNVERIFIED:` or `INFERENCE:` or `EXPECTED (not checked):` — or remove the statement. "Should be" / "probably" / "I'm pretty sure" / "looks like" / "typically" without a prefix is a violation.

2. **Verify, never ask.** When uncertain about a fact you can check yourself (file content, function signature, config value, data row, log line), the first move is a tool call — never a question to the user. Asking "is X true?" when you can `Grep` for X IS the drift signal.

3. **Entity-lock every number.** Every count, ID, timestamp, or aggregate in a report must trace to a query or search with the exact subject filter — the specific run ID, file, entity key, or log marker. Broad-time-window counts that include unrelated concurrent activity are forbidden. "Looks like N" / "around N" / "approximately" without an entity-locked query is forbidden.

4. **"Works" requires live evidence.** "The feature works" / "the fix is good" / "the script runs" requires a tool result showing the actual working behavior — a live run, an output file with the expected values, a log line from the current invocation. **A clean build / lint / green mocked tests is NOT evidence the feature works.** A long-running process serves stale code until restarted — even after an edit.

5. **No reconstructing from memory.** Even if you "remember" a file path, function name, port, env var, constant, or schema column from a prior session, verify with `Read` / `Grep` in the current session before quoting it. Cross-session memory is a recall index, never a citation source. Line numbers drift constantly — never quote `file:N` from memory.

6. **Count starts AND ends.** When counting logged operations, walk BOTH the start event and the end event for each subject. Counting only start lines hides failure tails. End status (ok vs error) is part of the evidence — a start without a matching successful end is a different finding than a successful operation.

7. **Cross-check writes against stored state.** When a log line says "write happened", the report must also show the matching read of the written state (file content, data row). Log-only evidence for a persisted write is half-evidence — the write may have failed, hit a different target, or never flushed.

8. **Pre-flight every report cell.** Before sending a verification table, status report, audit summary, or any artefact with cells / checkmarks / counts, re-read each cell and answer "what query / row / log / file read in THIS session proves this exact value?" If you cannot answer for any cell, that cell is inferred — fix it with a verifying tool call, or mark it `UNVERIFIED:`, or remove it.

9. **"Plausible" narratives are inference.** "This happens because of caching" / "the duplicate is the retry path" — narratives that smooth over a verification gap with plausibility are forbidden. Either prove the cause with concrete file / log / output evidence, or omit the narrative.

10. **Restate, don't paraphrase, tool output that you cite.** When you quote a value from a tool result ("the function returned 7"), quote the literal value the tool produced. Paraphrasing ("about 7" / "a few") drops information and becomes inference.

## Procedure — before stating any fact

1. Ask yourself: "what tool call in THIS session produced the evidence for this statement?"
2. If yes → state the fact; cite the tool output if the user would benefit from seeing the source.
3. If no → choose ONE:
   - Run the tool now and verify.
   - Prefix with `UNVERIFIED:` / `INFERENCE:` / `EXPECTED (not checked):` and state the assumption explicitly.
   - Decline to state it.

There is no fourth path. "I'll just say it" is the violation.

## Anti-Patterns

| Wrong | Why it fails | Right |
|---|---|---|
| "The function returns X" without reading the file | Function may have changed; cross-session memory is stale | `Read` the file, quote the actual return |
| "Y is at `file.py:42`" from memory | Line numbers drift on every edit; the wrong line is worse than no citation | `Grep` for the symbol, quote the current line |
| "Z works" after a clean build / green tests | Mocked tests + clean build ≠ live working; a running process serves stale code until restart | Live run / output check / log line from the current invocation |
| "Count is N" from a broad time window | Includes unrelated concurrent activity in the window | Entity-lock the filter (run ID / entity key / marker) and re-run |
| "Looks like the code handles X" without grep | Pattern-matched against training data, not the current code | `Grep` for the handler, cite the file |
| "Should be working — I'd expect it to" presented as status | Expectation is not evidence; presenting it as status corrupts the report | Run the check, OR prefix with `EXPECTED (not checked):` |
| "User probably wants X" — proceeding without confirming | Inference about intent presented as agreement | Ask, OR do the minimum reversible action and verify |
| "N happens because of caching" without proof | Plausibility narrative smoothing over a verification gap | Either prove with concrete evidence, OR omit the narrative |
| "About 7" / "a few" / "several" after a tool returned an exact integer | Paraphrase drops precision and becomes inference | Quote the literal value: "7" |
| "X is in state Y" because `git log` shows a commit that touches X | Commits ≠ running state; commit ≠ build ≠ restart | Check the running state — log line, output, live behavior |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| User says "you keep inferring" / "you're guessing" / "did you actually check?" | Stating facts without a tool call in the current session backing them | Re-read every factual claim against the tool log; add `UNVERIFIED:` to any that don't trace |
| Report numbers don't match the user's spot-check | Counts came from broad time window, not entity-locked subject | Rebuild with the exact subject filter; cross-check against the stored state |
| "Feature works" was wrong after an edit | Claimed completion from build/test signal, not live behavior — process still running stale code | After the edit: restart → live smoke check before claiming done |
| `file:line` citation broken | Quoted line number from cross-session memory | Always `Grep` the symbol in the current session before quoting line numbers |
| "Plausible" narrative in a report turned out to be wrong | Inference smoothed over a verification gap | Either prove the cause with concrete evidence, OR remove the narrative |
| Two report cells contradict each other | Both inferred from different plausibility chains | Each cell needs its own tool-call source; if they disagree, the data, not the cell, is wrong |
| Audit table looks complete but user pushes back on one cell | A single inferred cell next to verified cells looks identical | Mark cells with source: "[log:...]" / "[file:path:line]" — make the source visible |

## Verification

Before sending any response containing factual claims, run this checklist:

1. **Enumerate.** List every factual statement in the response — numbers, IDs, file paths, behaviors, states, names, versions.
2. **Source.** For each statement, name the tool call in THIS session that produced the evidence.
3. **Triage gaps.** For every statement without an in-session tool call, choose: verify now / prefix `UNVERIFIED:` / remove.
4. **Re-read tables.** Every cell — checkmarks, ✅, "N times", "works", "passed" — gets the same check. No cell ships without an answer to "what proves this?"
5. **Source markers in tables.** For audit / verification artefacts, add a source marker per cell (`[log:<ref>]` / `[file:<path>:<line>]`) so the user can see provenance at a glance.

## Sub-Agent Enforcement

Sub-agents do not inherit loaded skills — every `Agent` spawn producing factual reports should name this skill's rules in its prompt.

## See Also

- [correct-fix-over-easy-fix](../correct-fix-over-easy-fix/SKILL.md) — sibling discipline: don't shortcut investigation
- [source-code-first](../source-code-first/SKILL.md) — read source, never compiled/vendored copies, when verifying
- [single-source-of-truth](../single-source-of-truth/SKILL.md) — facts live in one canonical place; memory is recall, never citation
