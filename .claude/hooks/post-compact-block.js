/**
 * PreToolUse [.*] — Post-compaction HARD GATE (force immediate skill reload).
 *
 * After context compaction, skill content loaded earlier in the session is lost.
 * This hook BLOCKS every state-changing / expensive tool until the model has
 * reloaded the skills that were loaded before compaction — making reload the FIRST
 * action after a compaction, not an afterthought.
 *
 * It is the SINGLE owner of post-compaction gating. The previous gate lived in
 * skill-check-pre-edit.js and matched only Write|Edit, so Bash / MultiEdit / Agent /
 * mcp__* — the entire autonomous path — slipped past it: a compaction during a
 * Bash-heavy run was never enforced (the model kept working on stale skill memory).
 * This hook closes that gap.
 *
 * Exempt (always allowed — needed to reload + orient, or side-effect-free):
 *   - Skill            : the reload mechanism itself (NEVER block, or the gate deadlocks)
 *   - Read/Grep/Glob   : read-only orientation
 *   - TodoWrite        : progress tracking, no external effect
 *   - Bash, but ONLY when the command is provably read-only (readonly-command.js).
 *     Read-only orientation is already policy here — Read/Grep/Glob are exempt
 *     above — so blocking `cat` / `git log` / `grep` was an inconsistency in this
 *     hook's own rule, not a safety property. The predicate is default-deny and
 *     mutation-tested; anything it cannot PROVE read-only stays blocked. Hosts
 *     whose only read surface is shell (Codex) would otherwise be unable to
 *     orient at all — and cannot clear this gate, having no `Skill` tool.
 *   - Write/Edit/MultiEdit on infra/doc paths (.claude/hooks/, .cursor/, *.md|json|yml|…)
 *     so this hook itself + docs stay fixable while a reload is pending.
 * Everything else (mutating Bash, Agent, NotebookEdit, mcp__*, Write/Edit to source) is BLOCKED.
 *
 * Fail-open: any parse/IO error, a missing/corrupt/empty flag, or an empty skill list
 * → ALLOW. A broken flag must never brick the session; the 30-min TTL is the backstop.
 * The required-skill list is filtered to LOADABLE skills at write time
 * (post-compact-reload.js), so "omit if not found" holds and a stale/aliased name
 * (e.g. a renamed "database-usage") can never wedge the gate shut.
 */
const fs = require('fs');
const path = require('path');
const { STATE_DIR } = require('./_hook-state');
const { isReadOnlyCommand } = require('../workflow/lib/readonly-command.js');
const {
  extractCommand,
  isGateEscape,
  isShellTool,
} = require('../workflow/lib/gate-escape.js');

const RELOAD_FLAG_MAX_AGE_MS = 30 * 60 * 1000; // 30 min — matches post-compact-reload.js TTL

/* Tools that must run even with a reload pending (reload + read-only orientation).
 *
 * These are Claude Code's names. That was invisible for as long as Claude was
 * the only host with a `Skill` tool, and fatal the moment it wasn't: Codex has
 * none of them — no `Skill`, and it reads through its shell rather than a
 * `Read` tool — so this set matched nothing there while the block message
 * cheerfully advertised "Read/Grep/Glob stay available". The host-neutral half
 * of the answer now lives in `gate-escape.js` (`isGateEscape` + `isShellTool`);
 * this set is retained per the note below, and the shell paths carry Codex. */
const ALWAYS_ALLOW = new Set(['Skill', 'Read', 'Grep', 'Glob', 'TodoWrite']);
// Edit-family tools are allowed only on infra/doc paths (matched on file_path below).
const EDIT_TOOLS = new Set(['Write', 'Edit', 'MultiEdit']);

/**
 * Returns the pending-reload flag if a reload is genuinely outstanding for this
 * session (flag present, within TTL, not yet marked reloaded); null otherwise.
 * Session-scoped only — a cross-session fallback would leak a prior session's
 * reload state into unrelated new sessions inside the 30-min window.
 */
function checkPendingReload(sessionId) {
  const now = Date.now();
  const flagPath = path.join(STATE_DIR, `post-compact-pending-reload-${sessionId}.json`);
  try {
    if (!fs.existsSync(flagPath)) return null;
    const flag = JSON.parse(fs.readFileSync(flagPath, 'utf8'));
    if (flag && flag.timestamp && (now - flag.timestamp) < RELOAD_FLAG_MAX_AGE_MS && !flag.reloaded) {
      return flag;
    }
  } catch {
    /* corrupt / empty flag → fail-open (return null) */
  }
  return null;
}

/**
 * Has the post-compaction reload requirement been satisfied for this flag?
 * The bar is the FULL set — every pre-compaction skill must be genuinely reloaded
 * (an excerpt in a system-reminder is NOT a reload). The reason this is now
 * achievable without an infinite loop is post-compact-reload.js carries
 * `reloadedSkills` forward across compactions, so progress is monotonic: each window
 * loads as many as fit before the next compaction, and the accumulated set converges
 * on the full list instead of resetting to empty every time.
 */
function reloadSatisfied(flag) {
  const required = Array.isArray(flag.skills) ? flag.skills : [];
  if (required.length === 0) return true;
  if (flag.reloaded) return true;
  const reloaded = Array.isArray(flag.reloadedSkills) ? flag.reloadedSkills : [];
  return required.every((s) => reloaded.includes(s));
}

/** Infra/doc paths the gate must NOT block — so this hook and docs stay editable. */
function isPathExempt(filePath) {
  const p = (filePath || '').replace(/\\/g, '/');
  return p.includes('.claude/hooks/') || p.includes('.cursor/') || /\.(md|json|yml|yaml|toml|ini|cfg)$/i.test(p);
}

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (c) => {
  input += c;
});
process.stdin.on('end', () => {
  try {
    const data = JSON.parse(input);
    const toolName = typeof data.tool_name === 'string' ? data.tool_name : '';
    const sessionId = data.session_id || process.env.CLAUDE_SESSION_ID || process.env.SESSION_ID || 'default';

    /* This gate's own reload mechanism, plus every OTHER gate's opener, plus
     * read-only orientation tools. `ALWAYS_ALLOW` is retained because it is this
     * hook's local statement of the same idea and the two must not drift; the
     * shared predicate is what makes the gates composable with each other.
     *
     * Without the shared half, a compacted session that ALSO had an unacknowledged
     * recall could not run `memory recall-ack` (blocked here, as mutating shell)
     * and could not run `Skill` (blocked by the recall gate) — the deadlock that
     * stranded the OB-492 iteration-03 audit run. */
    /* Read the command through the shared extractor, not `tool_input.command`.
     * Codex carries shell text as `cmd`, and reading only Claude's spelling
     * yielded `''` — which every predicate below correctly refuses, so a parse
     * miss was indistinguishable from a genuine refusal and the gate shut on a
     * host that had done nothing wrong. */
    const command = extractCommand(data);
    if (ALWAYS_ALLOW.has(toolName) || isGateEscape(toolName, command)) {
      process.stdout.write('{}');
      return;
    }

    /* Read-only shell is orientation, same as Read/Grep/Glob above. Proven, not
     * declared — see readonly-command.js. Mutating shell still falls through.
     *
     * PowerShell is accepted alongside Bash because `isReadOnlyCommand` grew a
     * PowerShell cmdlet set for exactly this reason and Windows hosts reach for
     * the PowerShell tool by default — gating only `Bash` denied a command the
     * predicate had already proven safe, purely on which tool carried it. */
    if (isShellTool(toolName) && isReadOnlyCommand(command)) {
      process.stdout.write('{}');
      return;
    }

    const flag = checkPendingReload(sessionId);
    const skills = flag && Array.isArray(flag.skills) ? flag.skills : [];
    /* Progress so far. `post-compact-reload.js` carries this forward across
     * compaction generations, so after a second compaction the message asks
     * only for what is still missing instead of re-listing the full set — which
     * is what makes a long reload converge rather than restart. */
    const reloadedList = flag && Array.isArray(flag.reloadedSkills) ? flag.reloadedSkills : [];
    // No pending reload, nothing loadable to reload, or the reload requirement is
    // already satisfied (quorum / full-set / backstop — see reloadSatisfied) → allow.
    if (!flag || skills.length === 0 || reloadSatisfied(flag)) {
      process.stdout.write('{}');
      return;
    }

    // Edit-family on infra/doc paths is allowed (so this hook + docs stay fixable).
    if (EDIT_TOOLS.has(toolName)) {
      const filePath = (data.tool_input && (data.tool_input.file_path || data.tool_input.filePath)) || '';
      if (isPathExempt(filePath)) {
        process.stdout.write('{}');
        return;
      }
    }

    /* Everything else (mutating shell, Agent, mcp__*, Write/Edit-to-source, …)
     * is blocked until reload.
     *
     * The message names BOTH loaders, and does not try to guess the host.
     * It used to say only "use the Skill tool", which on Codex names a tool
     * that does not exist — and the outstanding list is exactly what a stuck
     * caller needs, so an unusable instruction attached to it is worse than
     * none. Sniffing the host from `toolName` was the obvious alternative and
     * is wrong for the same reason it is wrong in `plan-skills-required-block`:
     * the hook cannot see the caller's tool inventory, and a wrong guess hides
     * the only option that works.
     *
     * The CLI form carries `--session <id>` already substituted. That is not
     * cosmetic — the flag is what lets the loader write the cache this gate
     * reads, and a Codex worker cannot discover its own session id (no env var
     * exposes it, nothing prints it). Naming a command the reader cannot
     * complete is the same dead end one layer in. */
    const remaining = skills.filter((s) => !reloadedList.includes(s));
    const skillList = remaining.map((s) => `  - ${s}`).join('\n');
    const progress = skills.length - remaining.length;
    process.stdout.write(
      JSON.stringify({
        decision: 'block',
        reason: [
          `BLOCKED: POST-COMPACTION SKILL RELOAD REQUIRED (tool: ${toolName || 'unknown'}).`,
          '',
          'Context was compacted — skill content loaded earlier is GONE. Reload it BEFORE',
          'running any further tools. Still outstanding',
          `(${progress}/${skills.length} already reloaded; omit any that no longer exist):`,
          '',
          skillList,
          '',
          'Reload with whichever your host has:',
          '  • Skill tool (Claude Code): call it once per slug above.',
          '  • No Skill tool (e.g. Codex) — this prints each SKILL.md and records it:',
          `      node .cursor/workflow/workflow.js skills load ${remaining.join(' ')} --session ${sessionId}`,
          '',
          'Note the subcommand is `skills load` (plural), and it may be split across',
          'several calls — progress accumulates and survives further compactions.',
          '',
          'A `cd <dir> &&` prefix and a pipe into a reader (`| head`) are both fine.',
          'Redirection (`>`, `<`, `2>&1`), backgrounding (`&`), command substitution',
          '(`$(…)`, backticks) and any chained command that is not provably read-only',
          'are what get a command refused — rephrase without them rather than retrying.',
          '',
          'Meanwhile: provably read-only shell passes on every host (so you can read',
          'SKILL.md / AGENTS.md directly), as do Read / Grep / Glob / TodoWrite where',
          'they exist. Only state-changing or expensive calls are blocked.',
        ].join('\n'),
      }),
    );
  } catch {
    process.stdout.write('{}'); // fail-open — never brick the session on a hook error
  }
});
