'use strict';

/**
 * Provable read-only classification for shell command strings.
 *
 * WHY THIS EXISTS
 * ---------------
 * `plan-skills-required-block.js` (OB-288) and `post-compact-block.js` both gate
 * Bash because Bash is the primary code-mutation surface. On Claude Code that is
 * a papercut — read-only work has dedicated Read / Grep / Glob tools, so the gate
 * costs nothing real. On a Codex worker it is a DEADLOCK: shell is that host's
 * only file-reading surface, and the gate's sole release mechanism is the `Skill`
 * tool, which Codex does not expose. A gate whose unblock condition is
 * unsatisfiable on the host is not a gate, it is a wall.
 *
 * That wall broke a MANDATORY project rule. `.cursor/CLAUDE.md` requires audits to
 * run on fresh-context Codex workers in read-only mode, while the hook stack made
 * those workers unable to read anything (OB-492 Phase 4: both auditors correctly
 * refused to issue a verdict rather than fabricate one from an empty read set).
 *
 * The fix is the one this hook's own author already specified in
 * `plan-skills-required-block.js`: "If false-blocks on read-only Bash become
 * annoying, add a whitelist of read-only commands as a follow-up."
 *
 * ENFORCEMENT, NOT TRUST
 * ----------------------
 * The rejected alternative was an env-var bypass (`WORKFLOW_READONLY_WORKER=1`)
 * set by the orchestrator when dispatching a read-only worker. That is the Easy
 * Fix: it TRUSTS a declaration, so a mis-set flag silently disarms the gate for a
 * mutating worker. This module ENFORCES the property instead — a command is
 * allowed because it provably cannot write, not because someone said so. No flag,
 * no trust, and the gate stays fully armed for every mutating command on every
 * host.
 *
 * DEFAULT-DENY
 * ------------
 * "Cannot prove read-only" always returns false. This is a gate-release
 * predicate: a false negative costs an agent one blocked call it can rephrase; a
 * false positive lets unskilled code mutation through, which is the exact failure
 * OB-288 exists to prevent. When in doubt, deny. Interpreters that can write via
 * language features rather than shell redirection (`node -e`, `python -c`, `awk`
 * with `print > "f"`, `perl`, `ruby`, `xargs`, `tee`, `dd`) are deliberately NOT
 * whitelisted, even though many of their invocations would in fact be read-only.
 */

/**
 * Shell metacharacters that can redirect output, substitute a command, or
 * background a job. Any occurrence disqualifies the whole string — we do not
 * try to parse around them.
 *
 * `$` is rejected wholesale (not just `$(`): `$CMD args` would place an
 * arbitrary attacker- or drift-supplied program in command position, and no
 * read-only audit needs shell expansion to name a literal path.
 *
 * `;`, `|`, `&&` and `||` are NOT here. They are segment separators handled by
 * `splitSegments`, all on the identical footing: every segment must independently
 * prove read-only, so `ls; rm -rf /` is denied by `rm` rather than by the
 * punctuation. Rejecting them wholesale was a false-deny that mattered, twice.
 * PowerShell has no `&&` idiom, so a Codex worker batching reads writes
 * `Get-Content a; Get-Content b` — the string that blocked the OB-492 Phase 4
 * auditor from reading the seven files it opened with. And `&` being fatal made
 * `cd <repo> && <anything>` unprovable, which in a 165-repo monorepo is how every
 * command gets written; that is what left a compacted session with no issuable
 * call at all, including the loader the block message told it to run.
 *
 * A `&` that SURVIVES the split is still fatal — see `hasResidualAmpersand`. So
 * backgrounding (`cmd &`) and fd-duplication (`2>&1`) stay rejected; only the
 * chaining idiom is admitted, and only because each side is then judged on what
 * it actually runs.
 */
const FORBIDDEN_SHELL_CONSTRUCTS = ['>', '<', '`', '$', '\n', '\r'];

/** Commands that cannot write to the filesystem, with per-command flag rules below. */
const READ_ONLY_COMMANDS = new Set([
  'basename', 'cat', 'cd', 'cksum', 'column', 'cut', 'date', 'diff', 'dirname', 'du',
  'echo', 'file', 'find', 'git', 'grep', 'head', 'jq', 'ls', 'md5sum', 'nl',
  'od', 'printf', 'pwd', 'readlink', 'realpath', 'rg', 'sed', 'sha1sum',
  'sha256sum', 'sort', 'stat', 'strings', 'tail', 'tr', 'true', 'type',
  'wc', 'which', 'whoami', 'xxd',
]);

/* `cd` earns its place on the same terms as every other entry: it cannot write.
 * It moves the shell's own working directory and touches no file, spawns no
 * process. It is listed only because segmentation now admits `cd <repo> && …`,
 * and without the name here that leading segment fails the whitelist and denies
 * the whole string — which was the deadlock. Note it is admitted as a SEGMENT,
 * so `cd x && rm -rf /` is still denied by `rm`; `cd` buys the chain nothing. */

/* `uniq` is deliberately ABSENT despite being a classic reader. GNU's synopsis is
 * `uniq [OPTION]... [INPUT [OUTPUT]]` — the second positional is a file it WRITES,
 * so `uniq in.txt pwned.txt` mutates the filesystem with no flag to ban. Every
 * other writer in this module announces itself with a flag, which is why
 * FORBIDDEN_FLAGS_BY_COMMAND can catch it; a positional write cannot be caught by
 * that mechanism, and inventing a second mechanism for one command is worse than
 * losing the command. `sort -u` covers the same pipeline need and its only write
 * surface (`-o`) IS a flag. Found by the Codex F7 proof audit, 2026-07-28. */

/**
 * PowerShell read-only cmdlets, lowercased. Matched case-insensitively because
 * PowerShell itself is — `GET-CONTENT` and `Get-Content` are the same cmdlet, and
 * a case-sensitive miss here would deny a provably read-only command.
 *
 * This set is why the predicate works on a Windows Codex worker at all. The POSIX
 * whitelist above describes a shell that host does not use: the worker's actual
 * first blocked call was `Get-Content -Raw -LiteralPath '.agents/AGENTS.md'`,
 * denied purely because the name was absent, not because anything about it could
 * write.
 *
 * Kept to cmdlets with NO write surface at all, so no per-cmdlet flag rules are
 * needed. Every writer is denied by simple absence: `Set-Content`, `Add-Content`,
 * `Out-File`, `Tee-Object`, `New-Item`, `Remove-Item`, `Copy-Item`, `Move-Item`,
 * `Export-Csv`, `Invoke-Expression`, `Start-Process`. `Where-Object` and
 * `ForEach-Object` are also absent deliberately — they take a script block, which
 * is arbitrary code, and their `$_` spelling is rejected by `$` regardless.
 */
const POWERSHELL_READ_ONLY_CMDLETS = new Set([
  'compare-object', 'convertfrom-json', 'convertto-json', 'format-list',
  'format-table', 'get-childitem', 'get-content', 'get-filehash', 'get-item',
  'get-location', 'get-unique', 'join-path', 'measure-object', 'out-string',
  'resolve-path', 'select-object', 'select-string', 'set-location', 'sort-object',
  'split-path', 'test-path', 'write-output',
]);

/* `set-location` is here for the same reason `cd` is in the POSIX set, and it has
 * to be here for that reason to hold: admitting the chain-prefix on one shell and
 * not the other would fix the deadlock for a bash worker and leave a PowerShell
 * worker — which on Windows is the Codex default — stuck on the identical string.
 * A guard added to one twin and not the other is the exact defect this Epic keeps
 * finding; the POSIX `cd` alias resolves through the case-sensitive set above. */

/**
 * `git` subcommands that only read. Deliberately EXCLUDES ambiguous ones whose
 * read/write behaviour depends on flags — `config` (`--global x y` writes),
 * `branch` (`-D` deletes), `tag` (`-d` deletes), `remote` (`add` mutates),
 * `stash` (`list` reads but `push` mutates). An auditor can read all of those
 * through `show` / `rev-parse` / `ls-remote` instead.
 */
const GIT_READ_ONLY_SUBCOMMANDS = new Set([
  'blame', 'cat-file', 'count-objects', 'describe', 'diff', 'diff-tree',
  'log', 'ls-files', 'ls-remote', 'ls-tree', 'merge-base', 'name-rev',
  'rev-list', 'rev-parse', 'shortlog', 'show', 'show-ref', 'status',
  'symbolic-ref', 'var', 'verify-commit', 'whatchanged',
]);

/**
 * Per-command flags that turn an otherwise read-only tool into a writer.
 * Matched as a prefix so clustered / valued forms are caught too: `-i.bak`
 * matches `-i`, `--output=x` matches `--output`.
 */
const FORBIDDEN_FLAGS_BY_COMMAND = {
  // `find -exec rm` / `-delete` / `-fprintf out` all mutate.
  find: ['-exec', '-execdir', '-ok', '-okdir', '-delete', '-fprint', '-fprintf', '-fls'],
  // In-place edit. `-i`, `-i.bak`, `--in-place` all rewrite the file.
  sed: ['-i', '--in-place'],
  // `--pre` / `--pre-glob` run an arbitrary preprocessor binary per file.
  rg: ['--pre', '--hostname-bin'],
  // `git diff --output=FILE` and `git show --output=FILE` write.
  git: ['--output'],
  // `diff` can emit an ed script, but only redirection would persist it; the
  // real writer is GNU diff's rarely-used `--to-file` pairing with patch.
  diff: ['--to-file', '--from-file'],
  /* `sort -o FILE` / `--output=FILE` writes. Every OTHER sort positional is an
   * INPUT file, so the flag ban is complete for this command — unlike `uniq`,
   * whose write target is positional and which is therefore off the whitelist
   * entirely. This hole PREDATES the `;` work: `sort -o out in` was admitted as a
   * bare command and through a pipe from the day the exemption shipped. Found by
   * the Codex F7 proof audit, 2026-07-28. */
  sort: ['-o', '--output'],
  /* `date -s STRING` / `--set` writes the SYSTEM CLOCK. Not a filesystem write,
   * which is why it slipped a filesystem-shaped review, but a machine-state
   * mutation is exactly what a read-only gate exists to withhold. */
  date: ['-s', '--set'],
};

/**
 * Splits a command string into segments on `&&`, `||`, `|` and `;`.
 *
 * Every separator gets identical treatment because the safety argument is
 * identical: whatever the separator means at runtime, the string can only run
 * whitelisted readers if EVERY segment is one. That is what lets a caller write
 * `git log | grep fix`, `Get-Content a; Get-Content b`, and `cd <repo> && cat f`
 * without this module knowing anything about pipes, statement sequencing, or
 * conditional chaining — and it is why `cd x && rm -rf /` is denied by `rm`
 * rather than by the punctuation.
 *
 * `&&` and `||` must be listed BEFORE the single-character class in the
 * alternation. `String.prototype.split` takes the leftmost-longest alternative
 * at each position, so `||` reaching `[|;]` first would split a logical-OR into
 * two empty-separated fragments instead of two real ones.
 *
 * This is the shared contract, not a local convenience: `gate-escape.js` splits
 * with this same function. It used to forbid `;` and `|` outright while this
 * module segmented on them, and the two spellings of "how does a shell compose
 * commands?" drifted apart exactly as this Epic keeps finding elsewhere — an
 * invariant enforced per-caller instead of by the contract they share.
 *
 * A separator inside a quoted argument (`grep 'a;b' file`) splits wrongly and
 * the fragment fails the name check. That is a false DENY, which this module's
 * asymmetry explicitly accepts — it costs a rephrase, not a guarantee.
 */
function splitSegments(command) {
  return command.split(/&&|\|\||[|;]/).map((segment) => segment.trim());
}


/**
 * Tokenizes a single pipeline segment on whitespace. Quoting is NOT interpreted:
 * we only need the command name (first token) and a flag scan, and any construct
 * that could hide a writer behind quotes (`$`, backtick, redirection) has already
 * been rejected wholesale by `FORBIDDEN_SHELL_CONSTRUCTS`.
 */
function tokenize(segment) {
  return segment.split(/\s+/).filter(Boolean);
}

/** Does any token start with one of the command's forbidden flag prefixes? */
function hasForbiddenFlag(commandName, tokens) {
  const forbidden = FORBIDDEN_FLAGS_BY_COMMAND[commandName];
  if (!forbidden) return false;
  return tokens.some((token) => forbidden.some((flag) => token.startsWith(flag)));
}

/**
 * Git global flags that consume the NEXT token as their value. Without skipping
 * that value, `git -C some/dir log` would resolve its subcommand to `some/dir`
 * and get denied. `-c` takes a single `key=value` token, so it is included here
 * only for the malformed `-c key value` spelling; both resolve safely.
 */
const GIT_GLOBAL_FLAGS_WITH_VALUE = new Set(['-C', '-c', '--git-dir', '--work-tree', '--namespace', '--exec-path']);

/**
 * Resolves the git subcommand: the first token that is neither a global flag nor
 * the value consumed by one. Returns null when there is no subcommand at all
 * (bare `git`, or `git --version`), which the caller denies.
 */
function resolveGitSubcommand(tokens) {
  for (let i = 1; i < tokens.length; i += 1) {
    const token = tokens[i];
    if (GIT_GLOBAL_FLAGS_WITH_VALUE.has(token)) {
      i += 1; // skip the flag's value
      continue;
    }
    if (token.startsWith('-')) continue;
    return token;
  }
  return null;
}

/**
 * Validates one pipeline segment. Every segment of a pipe must independently be
 * a whitelisted reader — that is what makes `git log | grep x` safe without
 * teaching the classifier anything about pipes.
 */
function isReadOnlySegment(segment) {
  const tokens = tokenize(segment);
  if (tokens.length === 0) return false;

  /* A `&` that survived `splitSegments` is backgrounding (`cmd &`) or fd
   * duplication (`2>&1`) — never the chaining idiom, which the split consumed.
   * Neither is needed to read a file, and both hide work from the token scan
   * below, which only ever inspects `tokens[0]`: `cat a & rm -rf b` is ONE
   * segment whose first token is a whitelisted reader.
   *
   * This lives HERE, in the segment validator, rather than as a post-split scan
   * each caller runs. `gate-escape.js` calls this function to clear the
   * non-opening segments of a chain, and a guard it had to remember to call
   * separately is a guard that eventually only one of the two callers has —
   * which is the defect this Epic keeps finding in its own subject matter. */
  if (segment.includes('&')) return false;

  const commandName = tokens[0];

  /* The whitelist is a Set of BARE names, and that property is load-bearing in
   * two ways beyond the obvious one. A path form (`/usr/bin/git`, `./script.sh`,
   * `bin/cat`) can never match it, so path invocations are denied without a
   * separate check. Writers and interpreters (`tee`, `xargs`, `sudo`, `env`,
   * `node`, `awk`) are denied by simple absence. Explicit guards for either were
   * dead code — mutation-tested and removed. Keep this a Set of bare names: a
   * future loosening to a regex or a prefix match would silently reopen both. */
  /* Two whitelists, two case rules — because the two shells have two case rules.
   * POSIX command names are case-SENSITIVE (`CAT` is not `cat`), so that lookup
   * stays exact. PowerShell cmdlet names are case-INSENSITIVE, so that one
   * lowercases first. Collapsing them into one case-insensitive check would
   * silently widen the POSIX set; keeping them separate is why it does not. */
  const isPosixReader = READ_ONLY_COMMANDS.has(commandName);
  const isPowerShellReader = POWERSHELL_READ_ONLY_CMDLETS.has(commandName.toLowerCase());
  if (!isPosixReader && !isPowerShellReader) return false;
  if (hasForbiddenFlag(commandName, tokens)) return false;

  // `git` carries its write surface in the subcommand, so it needs a second gate.
  if (commandName === 'git') {
    const subcommand = resolveGitSubcommand(tokens);
    if (!subcommand) return false;
    if (!GIT_READ_ONLY_SUBCOMMANDS.has(subcommand)) return false;
  }

  return true;
}

/**
 * Is this shell command string PROVABLY read-only?
 *
 * Returns false for anything it cannot prove — an unknown command, a path-form
 * invocation, any redirection / chaining / substitution, or a whitelisted
 * command carrying a mutating flag. Callers use this to release a gate, so the
 * asymmetry is deliberate: under-approximating costs a retry, over-approximating
 * costs an ungated mutation.
 */
function isReadOnlyCommand(command) {
  if (typeof command !== 'string') return false;
  const trimmed = command.trim();
  if (trimmed.length === 0) return false;

  for (const construct of FORBIDDEN_SHELL_CONSTRUCTS) {
    if (trimmed.includes(construct)) return false;
  }

  return splitSegments(trimmed).every((segment) => isReadOnlySegment(segment));
}

module.exports = {
  FORBIDDEN_SHELL_CONSTRUCTS,
  GIT_READ_ONLY_SUBCOMMANDS,
  POWERSHELL_READ_ONLY_CMDLETS,
  READ_ONLY_COMMANDS,
  isReadOnlyCommand,
  isReadOnlySegment,
  splitSegments,
};
