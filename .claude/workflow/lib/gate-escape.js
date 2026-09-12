/**
 * Shared escape-hatch contract for every PreToolUse gate.
 *
 * WHY THIS MODULE EXISTS
 *
 * Each gate used to carry its own private notion of "what must always pass".
 * Individually every one of those lists was correct; the union was not closed
 * under composition, and two armed gates wedged the session permanently:
 *
 *   - `session-classify-block` (recall gate) allowed only AskUserQuestion, the
 *     `mcp__openbrain__*` tools, and `workflow.js memory recall-*`. It blocked
 *     `workflow.js skills load`.
 *   - `plan-skills-required-block` (skills gate) allowed only read-only Bash and
 *     `workflow.js skills load`. It blocked `workflow.js memory recall-ack`.
 *
 * So the command that opens gate A was blocked by gate B and vice versa. With
 * both armed there was no ordering that could open either — not a rare race, a
 * guaranteed deadlock. It cost the OB-492 iteration-03 audit run: BOTH Codex
 * auditors (Phase 5 and Phase 6) hit it, read zero scoped files, and correctly
 * refused to fabricate findings rather than report an unverified verdict. The
 * orchestrating Claude session hit the identical wall on the same slot and could
 * not clear it from inside either — a human had to run the ack from a terminal.
 *
 * This is the same defect this Epic keeps finding in its own subject matter: an
 * invariant enforced by each caller separately instead of by the contract they
 * share. The fix is the same shape too — one funnel, and the copies deleted.
 *
 * THE RULE, IN ONE SENTENCE
 *
 * Every gate allows (a) anything that can open ANY gate, and (b) anything
 * provably side-effect-free; everything else it is entitled to block.
 *
 * Clause (a) is what makes the gates composable: a gate that blocks another
 * gate's key is a gate that can deadlock. Clause (b) is the read-only
 * orientation policy `post-compact-block` already articulated — you cannot
 * violate any gate's mandate by reading a file, so no gate has cause to stop it.
 *
 * WHY THIS IS NOT A HOLE
 *
 * Nothing here widens what a MUTATING call may do. The escape set is a fixed,
 * shape-matched list of `workflow.js` subcommands whose entire purpose is to
 * satisfy a gate, plus tools that cannot mutate the tree. `skills load` prints
 * SKILL.md files. `memory recall-ack` records an acknowledgement in the workset
 * lifecycle — it writes no source and can only report an outcome the caller
 * already had. Each still enforces its own arguments; the gate merely stops
 * standing in front of the door it is asking the caller to walk through.
 */

'use strict';

const {
  FORBIDDEN_SHELL_CONSTRUCTS,
  isReadOnlySegment,
  splitSegments,
} = require('./readonly-command.js');
const { SKILL_SLUG_PATTERN } = require('./loaded-skills.js');
const { parseArgs } = require('./parse-args.js');

/**
 * Tools that must pass every gate.
 *
 * The first two open a gate directly (`AskUserQuestion` answers the classifier
 * picker; `Skill` is how a Claude host satisfies both the plan-skills gate and
 * the post-compaction reload). The rest are read-only orientation — clause (b).
 * `mcp__openbrain__*` is matched by prefix below rather than enumerated, because
 * the recall gate's own instruction is to call `brain_context` first and the
 * tool list moves independently of this file.
 */
const ESCAPE_TOOLS = new Set([
  'AskUserQuestion',
  'Skill',
  'Read',
  'Grep',
  'Glob',
  'TodoWrite',
  /* Codex's plan-scratchpad tool — the `TodoWrite` of that host. It mutates no
   * file and blocking it buys a gate nothing, exactly as for `TodoWrite`. */
  'update_plan',
]);

/**
 * Tool names that carry a shell command, across every host we run on.
 *
 * WHY THIS LIST EXISTS AND WHY IT IS NOT THE SECURITY BOUNDARY
 *
 * Until OB-492 (2026-07-30) the gates asked `toolName === 'Bash' || 'PowerShell'`
 * before looking at a command at all. Those are Claude Code's names. Codex's
 * shell tool is not called either of them, so on Codex every command-based
 * escape returned false — including `skills load`, the one command whose entire
 * job is to open the gate. Combined with `ESCAPE_TOOLS` also being Claude-only
 * names (Codex has no `Read`/`Grep`/`Glob`; it reads through the shell), a
 * compacted Codex session had NO permitted tool at all. Not strict — insoluble.
 * A human had to clear the flag from outside the session.
 *
 * The names below are the ones observed on this machine, recorded in the
 * 2026-07-14 team agent-usage survey: this Codex build emits `exec_command`,
 * with the shell text in `arguments.cmd`. The spec-documented `shell` / `exec` /
 * `shell_command` spellings are included too, because the survey's own finding
 * was that the build disagreed with the spec — so pinning either one alone is
 * how this breaks again on the next Codex release.
 *
 * Crucially this list is a HINT, never the guarantee. The guarantee for a
 * gate-opening command is `matchEscapeSubcommand`, which proves the string is
 * `node <…>/workflow.js <known subcommand> <inert args>` with every shell
 * construct rejected; the guarantee for read-only is `isReadOnlyCommand`, which
 * is default-deny and mutation-tested. Neither becomes truer because the
 * carrier is spelled `Bash`. That is why `isGateEscape` below no longer
 * consults this list at all — a missing name must degrade to "cannot prove it
 * is read-only", never to "cannot open the gate".
 */
const SHELL_TOOLS = new Set([
  'Bash',
  'PowerShell',
  'exec_command',
  'shell',
  'shell_command',
  'exec',
  'local_shell',
]);

/** True when the tool is a known shell-command carrier on any supported host. */
function isShellTool(toolName) {
  return typeof toolName === 'string' && SHELL_TOOLS.has(toolName);
}

/**
 * Tools that carry no shell command and must never open a gate, whatever a
 * `command`-shaped field in their payload happens to contain.
 *
 * This is a DENY list on purpose, and the direction is the whole fix.
 *
 * The escape used to be gated on an ALLOW list of shell names, so an unknown
 * tool — every tool on a host we had not enumerated — defaulted to "cannot open
 * the gate". Applied to Codex that meant no tool could open it and the session
 * was unrecoverable from the inside.
 *
 * Denying the known non-shell tools keeps the property the allow-list was
 * actually protecting (a `skills load` string smuggled into an `Edit` payload
 * is not a skills load) while making the DEFAULT for an unrecognised tool
 * "judge the command on its merits" instead of "refuse". An unknown tool that
 * really does carry that exact command runs a skills load, which is the
 * outcome the gate is asking for; an unknown tool that carries anything else
 * still fails the shape match. Wrong guesses now cost a permitted call rather
 * than a deadlock.
 */
const NON_SHELL_TOOLS = new Set([
  'Edit',
  'Write',
  'MultiEdit',
  'NotebookEdit',
  'Agent',
  'Task',
  'apply_patch',
]);

/**
 * Pull the shell command out of a hook payload, whichever host wrote it.
 *
 * Claude Code puts it at `tool_input.command`. The Codex build measured here
 * carries shell text as `cmd`, and its payloads have been seen keyed under
 * `arguments` rather than `tool_input`. Reading one spelling and defaulting the
 * rest to `''` is indistinguishable, downstream, from "the user ran an empty
 * command" — which every predicate correctly refuses, so the gate slams shut
 * with no way to tell that it was a parse miss rather than a real refusal.
 *
 * Returns `''` when no command-shaped field is present.
 */
function extractCommand(data) {
  if (!data || typeof data !== 'object') return '';
  const containers = [data.tool_input, data.arguments, data.input, data];
  for (const container of containers) {
    if (!container || typeof container !== 'object') continue;
    for (const key of ['command', 'cmd']) {
      if (typeof container[key] === 'string' && container[key].trim()) {
        return container[key];
      }
    }
  }
  return '';
}

/** True for any tool that must never be blocked by a gate. */
function isGateEscapeTool(toolName) {
  if (typeof toolName !== 'string' || !toolName) {
    return false;
  }
  if (ESCAPE_TOOLS.has(toolName)) {
    return true;
  }
  return /^mcp__openbrain__/.test(toolName);
}

/* Argument classes.
 *
 * BARE admits only slugs and unquoted flags. It is what `skills load` has always
 * used and what the F7 audit signed off on, so it stays exactly that strict:
 * `skills load` has no argument that needs a space, and widening it would spend
 * an audited guarantee to buy nothing.
 *
 * QUOTED additionally admits a quoted value, because `--reason "<what happened>"`
 * and `--title "<one-line>"` are the documented spellings of commands in this
 * set — a matcher that rejected them would deny the exact invocation the block
 * message tells the caller to run, which is how the deadlock started.
 *
 * Admitting quotes is safe because a segment is matched only AFTER the string has
 * been split on every composition operator and stripped of `>`, `<`, backtick,
 * `$`, newline and carriage return. Quoting cannot hide a second command from
 * that: `--reason "a && rm -rf /"` splits mid-quote into two fragments, and both
 * must independently pass — the first fails the trailing-quote arg class, so the
 * whole string is denied. Splitting makes quoted arguments STRICTER, not looser.
 * The guarantee comes from the split plus the scan, never from the shape of the
 * regex; that distinction is exactly what the F7 hole taught. */
const BARE_ARG = '[\\w.=/:@+-]+';
const QUOTED_ARG = `(?:${BARE_ARG}|"[^"]*"|'[^']*')`;
const BARE_ARGS_REQUIRED = `(?: +${BARE_ARG})+`;
const QUOTED_ARGS_OPTIONAL = `(?: +${QUOTED_ARG})*`;

/**
 * Every `workflow.js` subcommand whose purpose is to open a gate.
 *
 * Adding a gate means adding its opener here — that is the whole maintenance
 * contract, and it is why this is a table rather than a regex per hook.
 */
/* Held by reference, not re-declared, because `parseSkillsLoadSlugs` identifies
 * its row by identity against the very object the matcher returned. A string
 * comparison on `words` would be a second spelling of "is this a skills load?"
 * living one function away from the first — the shape of drift this file exists
 * to prevent, in miniature. */
const SKILLS_LOAD = { words: ['skills', 'load'], args: BARE_ARGS_REQUIRED };

const ESCAPE_SUBCOMMANDS = [
  // Classifier picker (B-Decisions 6 + 7) — the user's way out of `pending`.
  { words: ['classify'], args: QUOTED_ARGS_OPTIONAL },
  { words: ['upgrade'], args: QUOTED_ARGS_OPTIONAL },
  // Plan-skills gate + post-compaction reload on a host with no `Skill` tool.
  SKILLS_LOAD,
  // Recall gate (OB-492 Phase 4).
  { words: ['memory', 'recall-ack'], args: QUOTED_ARGS_OPTIONAL },
  { words: ['memory', 'recall-request'], args: QUOTED_ARGS_OPTIONAL },
  { words: ['memory', 'probe-record'], args: QUOTED_ARGS_OPTIONAL },
  { words: ['memory', 'probe-show'], args: QUOTED_ARGS_OPTIONAL },
  { words: ['memory', 'show'], args: QUOTED_ARGS_OPTIONAL },
];

/**
 * True only for `node <…>workflow.js <gate-opening subcommand> <arg…>`.
 *
 * Shape-matched, never substring-matched: an `includes('recall-ack')` test would
 * pass `rm -rf x && echo recall-ack`. This pins command position (`node`), script
 * identity (a path ENDING in `workflow.js`, so the `.cursor/` and `.claude/`
 * mirrors and absolute Windows paths all resolve), the exact subcommand words,
 * and an argument class that admits nothing executable.
 *
 * The metacharacter rejection is a SEPARATE, EXPLICIT step and not something the
 * regex is trusted to imply — `\s` matches `\n`, so a trailing newline plus a
 * second command satisfied the pre-F7 spelling of this check. Two of the three
 * matchers this function replaces still had that hole when it was written:
 * `isRecallCommand` and `isClassifyOrUpgradeCommand` were bare `\b`-anchored
 * `.test()` calls with no scan at all, so `node w.js memory recall-ack && <anything>`
 * rode straight into an armed gate. Hardening one of three call sites is how that
 * survived; there is one call site now.
 */
function matchEscapeSegment(segment) {
  const head = /^node +([^ ]+) +(.+)$/.exec(segment);
  if (!head) {
    return null;
  }

  const [, scriptPath, rest] = head;
  if (scriptPath !== 'workflow.js' && !scriptPath.endsWith('/workflow.js')) {
    return null;
  }

  const row = ESCAPE_SUBCOMMANDS.find(
    ({ words, args }) => new RegExp(`^${words.join(' +')}${args} *$`).test(rest),
  );

  return row ? { row, rest } : null;
}

/**
 * Every escape-subcommand match in a composed command, or `null` if the string
 * is not a gate opener.
 *
 * WHY THIS SEGMENTS INSTEAD OF DEMANDING ONE BARE COMMAND (OB-492, 2026-07-30)
 *
 * This used to reject `;`, `|` and `&` outright and match the whole string, on
 * the reasoning that "an escape-hatch invocation is ONE command and has no
 * segments". That is true of the invocation and false of what callers type. In a
 * 165-repo monorepo the loader is written `cd <repo> && node .cursor/…`, or piped
 * to `head` to keep the output readable — and every one of those was refused,
 * with a block message that never said punctuation was the problem. So the
 * caller retried variants of a command that could not be made to work. Measured
 * on Claude Code: the bare form passed, `cd <repo> && <same command>` did not.
 *
 * The deadlock was therefore never host-specific. It reproduced on the host with
 * a `Skill` tool as readily as on the one without; Codex hit it first only
 * because Codex has no second way to load a skill.
 *
 * The rule is now the one `readonly-command.js` already applied to `;` and `|`,
 * via that module's own splitter so the two cannot drift again: split on every
 * composition operator, then require that at least one segment opens a gate and
 * that EVERY other segment is provably read-only. `node w.js skills load x && rm -rf /`
 * is still refused — by `rm`, on its merits, exactly as the 2026-07-28 F7 audit
 * demanded — rather than by the presence of an ampersand.
 */
function matchEscapeSubcommand(command) {
  if (typeof command !== 'string' || !command) {
    return null;
  }

  const normalized = command.trim().replace(/\\/g, '/');

  for (const construct of FORBIDDEN_SHELL_CONSTRUCTS) {
    if (normalized.includes(construct)) {
      return null;
    }
  }

  const matches = [];
  for (const segment of splitSegments(normalized)) {
    const match = matchEscapeSegment(segment);
    if (match) {
      matches.push(match);
      continue;
    }
    /* A non-opener segment rides along only if it could not mutate anything.
     * That is what makes `cd <repo> &&` and `| head -50` free while keeping the
     * chain's safety a property of what it runs, not of how it is punctuated. */
    if (!isReadOnlySegment(segment)) {
      return null;
    }
  }

  return matches.length > 0 ? matches : null;
}

/** True only when some segment is `node <…>workflow.js <gate-opening subcommand> <arg…>`. */
function isGateEscapeCommand(command) {
  return matchEscapeSubcommand(command) !== null;
}

/**
 * The skill slugs a `skills load` command would actually load — `[]` for
 * anything else.
 *
 * WHY THE RECORDER PARSES THE COMMAND RATHER THAN THE CLI RECORDING ITSELF
 *
 * `workflow.js skills load` runs in the model's own shell. On a read-only Codex
 * worker that shell is sandboxed: writing `os.tmpdir()/claude-hooks-state/` gets
 * `EPERM`, so the CLI could not record the load even though it printed every
 * SKILL.md correctly. Hook subprocesses are NOT sandboxed on the same host —
 * measured in read-only session `019fae48-a8ed-7cb2-879a-6900e33fb6a0`, where
 * hooks wrote into that directory while the model's own Bash call was denied it.
 *
 * The hook also knows something the CLI cannot: the session id the gate reads.
 * A Codex worker is invoked with an operator-chosen `--session` string, but the
 * hook payload carries Codex's own UUIDv7, and the worker has no way to discover
 * it. So even a CLI that COULD write would key the cache wrongly. Recording from
 * the hook keys it correctly by construction and deletes that failure class
 * rather than working around it.
 *
 * This is why the parse reuses `matchEscapeSubcommand` rather than sniffing for
 * `skills load` on its own: the gate and the recorder must agree on what counts
 * as a load. A looser recognizer would record slugs off a command the gate never
 * admitted; a stricter one would leave the gate shut after a load it did admit.
 * One matcher, so neither is expressible.
 */
function parseSkillsLoadSlugs(command) {
  const matches = matchEscapeSubcommand(command);
  if (!matches) {
    return [];
  }

  /* EVERY `skills load` segment counts, not just the first. Now that a command
   * may compose, `… skills load a && … skills load b` genuinely loads both, and
   * recording only one would leave the gate shut after a load it admitted —
   * the precise asymmetry the shared matcher exists to make inexpressible. */
  const slugs = [];
  for (const match of matches) {
    if (match.row !== SKILLS_LOAD) continue;

    /* `rest` is the post-`node <script>` remainder, already proven free of every
     * shell construct and of quoting (BARE_ARGS_REQUIRED admits neither), so a
     * whitespace split is a faithful argv. `parseArgs` — the CLI's own grammar —
     * then drops `--session <id>` and every other flag/value pair, leaving exactly
     * the positionals `handleSkillsLoad` treats as slugs. */
    const argv = match.rest.split(/ +/).filter(Boolean).slice(SKILLS_LOAD.words.length);
    for (const value of parseArgs(argv)._) {
      if (SKILL_SLUG_PATTERN.test(value) && !slugs.includes(value)) slugs.push(value);
    }
  }

  return slugs;
}

/**
 * The single check a gate runs before blocking.
 *
 * Takes the raw hook payload fields so every gate spells the question the same
 * way and none of them can drift into matching a tool name the others do not.
 *
 * THE TOOL NAME IS DELIBERATELY NOT CONSULTED FOR THE COMMAND CASE (OB-492).
 *
 * This used to require `toolName === 'Bash' || 'PowerShell'` before it would
 * even look at the command. That check did no security work: by the time
 * `isGateEscapeCommand` returns true the string has been proven to be
 * `node <…>/workflow.js <known subcommand> <inert args>` with `&`, `;`, `|`,
 * newline, backtick, `$`, `>` and `<` all rejected. Nothing about that proof
 * depends on what the harness labelled the carrier. The name test encoded only
 * "am I on Claude Code", and its failure mode was total: on a host that spells
 * its shell differently, the command that opens the gate is the command the
 * gate refuses, and the session is stuck until a human intervenes from outside.
 *
 * A gate that cannot be opened is not a strict gate; it is a broken one. So the
 * rule is now what this module's own docblock always claimed it was — anything
 * that can open ANY gate passes EVERY gate — with no host qualifier attached.
 *
 * This does not widen what a mutating call may do. The known file-mutating and
 * agent-spawning tools are denied outright via `NON_SHELL_TOOLS`, so the
 * property the old allow-list protected — a `skills load` string smuggled into
 * an `Edit` payload is not a skills load — still holds. What changed is only
 * the default for a tool we have never heard of: it is now judged on its
 * command instead of refused on its name.
 */
function isGateEscape(toolName, command) {
  if (isGateEscapeTool(toolName)) {
    return true;
  }
  if (typeof toolName === 'string' && NON_SHELL_TOOLS.has(toolName)) {
    return false;
  }
  return isGateEscapeCommand(command);
}

/**
 * `isGateEscape` for callers that hold the whole payload rather than two fields.
 *
 * Preferred over the two-argument form, because it also finds the command on
 * hosts that do not key it at `tool_input.command` — the miss that made the
 * two-argument form host-specific in the first place.
 */
function isGateEscapePayload(data) {
  const toolName = data && typeof data.tool_name === 'string' ? data.tool_name : '';
  return isGateEscape(toolName, extractCommand(data));
}

module.exports = {
  ESCAPE_SUBCOMMANDS,
  ESCAPE_TOOLS,
  NON_SHELL_TOOLS,
  SHELL_TOOLS,
  extractCommand,
  isGateEscape,
  isGateEscapeCommand,
  isGateEscapePayload,
  isGateEscapeTool,
  isShellTool,
  parseSkillsLoadSlugs,
};
