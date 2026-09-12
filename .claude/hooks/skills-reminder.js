/**
 * UserPromptSubmit hook — injects skills reminder and post-compaction recovery.
 *
 * Three modes:
 * 1. POST-COMPACTION RECOVERY: If a pending-reload flag exists (set by PostCompact hook),
 *    inject a BLOCKING instruction listing the exact skills to reload. The flag includes
 *    the session brief and skill list from before compaction.
 * 2. FIRST MESSAGE / 15-MIN REFRESH: Inject the standard skills reminder.
 * 3. ALREADY REMINDED: Return empty (no injection).
 */
const fs = require('fs');
const path = require('path');
const os = require('os');

const REFRESH_INTERVAL_MS = 15 * 60 * 1000; // 15 minutes
const STATE_DIR = path.join(os.tmpdir(), 'claude-hooks-state');
const RELOAD_FLAG_MAX_AGE_MS = 30 * 60 * 1000; // 30 minutes — ignore stale flags

// Advisory note — kept short and non-imperative so it cannot be mistaken for a
// turn-starting instruction. The full trigger map lives in CLAUDE.md; the hook's
// job is just to remind once, not to re-specify the conventions.
const REMINDER = "Advisory: skill triggers — CLAUDE.md → Skill & Knowledge Trigger Map. Load relevant skills before code edits if not loaded this session.";

/**
 * Try to find and read the pending-reload flag file.
 * Session-scoped only — a session-agnostic fallback previously leaked
 * prior-session reload state into new sessions within the 30-min TTL.
 * Returns the parsed flag data or null.
 */
function readReloadFlag(sessionId) {
  const now = Date.now();

  const sessionFlag = path.join(STATE_DIR, `post-compact-pending-reload-${sessionId}.json`);
  try {
    if (fs.existsSync(sessionFlag)) {
      const flag = JSON.parse(fs.readFileSync(sessionFlag, 'utf8'));
      if (flag.timestamp && (now - flag.timestamp) < RELOAD_FLAG_MAX_AGE_MS && !flag.reloaded) {
        flag._path = sessionFlag;
        return flag;
      }
      // Stale or already reloaded — clean up
      try { fs.unlinkSync(sessionFlag); } catch { /* ok */ }
    }
  } catch { /* no flag */ }

  return null;
}

/**
 * Build the post-compaction recovery message.
 * This message is BLOCKING — it tells Claude to reload skills before doing anything else.
 */
function buildRecoveryMessage(flag) {
  const required = Array.isArray(flag.skills) ? flag.skills : [];
  // Progress carried across compactions by post-compact-reload.js. List only what is
  // still MISSING — re-listing all N every window made the model re-load already-counted
  // skills, wasting context and pushing toward the next compaction before the set ever
  // completed. Pending-only + monotonic carry-forward is what makes the FULL set reachable.
  const reloaded = Array.isArray(flag.reloadedSkills) ? flag.reloadedSkills : [];
  const pending = required.filter((s) => !reloaded.includes(s));

  const lines = [
    '=== CONTEXT COMPACTION OCCURRED — MANDATORY SKILL RELOAD ===',
    '',
    'Your context was just compacted. Skill content that was loaded earlier in this session',
    'has been TRUNCATED or LOST. The skill names may still appear in system reminders but',
    'their actual rules and conventions are GONE. You MUST reload them before doing any work.',
    '',
    'CLAUDE.md does NOT need to be re-read — it is re-injected fresh by the system after compaction.',
    '',
  ];

  if (required.length === 0) {
    lines.push('## No skill list available from before compaction');
    lines.push('');
    lines.push('The skill tracker did not capture which skills were loaded.');
    lines.push('Check CLAUDE.md trigger map and reload skills relevant to the current task.');
  } else if (pending.length === 0) {
    lines.push('## All tracked skills already reloaded — proceed with the task.');
  } else {
    if (reloaded.length > 0) {
      lines.push(`## ${reloaded.length}/${required.length} skills already reloaded (carried across compactions). Reload the remaining ${pending.length} NOW`);
    } else {
      lines.push(`## MANDATORY: Reload these ${pending.length} skills NOW`);
    }
    lines.push('');
    lines.push('Use the Skill tool to reload each one. Do this BEFORE answering the user or editing any files:');
    lines.push('');
    pending.forEach(s => lines.push(`  - Skill: "${s}"`));
    lines.push('');
    lines.push('Progress is preserved across compactions: load what fits this window — if a');
    lines.push('compaction interrupts, the NEXT reminder lists only what is still missing, so the');
    lines.push('full set always converges. DO NOT skip any — the truncated versions are unreliable.');
  }

  lines.push('');
  lines.push('## ENFORCEMENT');
  lines.push('- DO NOT edit files until skills are reloaded');
  lines.push('- DO NOT assume truncated skill content is correct');
  lines.push('- DO NOT proceed with code changes relying on "remembered" conventions');
  lines.push('- The PreToolUse hook WILL BLOCK Write/Edit calls until skills are reloaded');
  lines.push('');

  // Inject the (large) session brief only on the FIRST recovery message — i.e. before
  // any skill has been reloaded this generation. Re-injecting it every prompt while the
  // reload is in progress would itself accelerate the next compaction.
  if (flag.briefContent && reloaded.length === 0) {
    lines.push('## Session Brief (from before compaction)');
    lines.push('');
    lines.push(flag.briefContent);
  }

  return lines.join('\n');
}

/**
 * Load the set of plan titles that reached "Executed" status in this session
 * (written by plan-edit-tracker.js). Mirrors loadExecutedPlans in audit-stop-check.js
 * — the UserPromptSubmit advisory uses the same session-scoping rule so it only
 * reminds about plans the user actually executed THIS session, not every historical
 * entry in the ledger. Duplicated here rather than required() because
 * audit-stop-check.js has top-level stdin side effects that run on require.
 */
function loadExecutedPlansForSession(sessionId) {
  try {
    const stateFile = path.join(os.tmpdir(), 'claude-hooks-state', `plan-edits-${sessionId}.json`);
    const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
    return new Set(Array.isArray(state.executedPlans) ? state.executedPlans : []);
  } catch {
    return new Set();
  }
}

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => { input += chunk; });
process.stdin.on('end', () => {
  try {
    const data = JSON.parse(input);
    // Session id comes from the hook's stdin JSON ONLY. The env-var fallbacks
    // (CLAUDE_SESSION_ID / SESSION_ID) were removed per task-workflow's
    // multi-slot model anti-pattern: fallbacks cross-session-leak hook state
    // and teammates copying this pattern carry the violation into new hooks.
    // If session_id is absent, we degrade to a literal 'no-session' label —
    // the hook can still emit the generic reminder, but workset-pointer
    // resolution below will correctly no-op because no pointer file matches.
    const sessionId = typeof data.session_id === 'string' && data.session_id.trim()
      ? data.session_id
      : 'no-session';

    // Gate: only inject for actual user-submitted prompts. Claude Code fires
    // UserPromptSubmit on both human submissions (prompt field set) and on
    // system/tool-result re-entries where the field is empty or missing. When
    // advisory context lands mid-task it looks like a new instruction and can
    // derail the agent's in-flight work. Early-exit for non-human entries so
    // the advisory only attaches to genuine user turns.
    const promptText = typeof data.prompt === 'string' ? data.prompt.trim() : '';
    if (!promptText) {
      process.stdout.write('{}');
      return;
    }

    // (AstraBit's session-handoff log was removed in the tower copy - no
    // workflow runtime here needs to resolve session ids.)

    if (!fs.existsSync(STATE_DIR)) {
      fs.mkdirSync(STATE_DIR, { recursive: true });
    }

    // --- Priority 1: Check for post-compaction reload flag ---
    const reloadFlag = readReloadFlag(sessionId);
    if (reloadFlag) {
      const recoveryMessage = buildRecoveryMessage(reloadFlag);

      // Update the reminder state so we don't double-inject the standard reminder
      const stateFile = path.join(STATE_DIR, `skills-reminder-${sessionId}.json`);
      fs.writeFileSync(stateFile, JSON.stringify({ lastInjected: Date.now() }));

      process.stdout.write(JSON.stringify({
        hookSpecificOutput: {
          hookEventName: "UserPromptSubmit",
          additionalContext: recoveryMessage
        }
      }));
      return;
    }

    // --- Priority 2: Standard skills reminder (first message or 15-min refresh) ---
    const stateFile = path.join(STATE_DIR, `skills-reminder-${sessionId}.json`);
    let shouldInject = true;
    try {
      const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
      shouldInject = (Date.now() - state.lastInjected) > REFRESH_INTERVAL_MS;
    } catch {
      // No state file — first run for this session
    }

    if (shouldInject) {
      fs.writeFileSync(stateFile, JSON.stringify({ lastInjected: Date.now() }));

      // (AstraBit's workset + audit-ledger advisories were removed in the
      // tower copy - this project has no workflow runtime or audit ledger.)
      process.stdout.write(JSON.stringify({
        hookSpecificOutput: {
          hookEventName: "UserPromptSubmit",
          additionalContext: REMINDER
        }
      }));
    } else {
      process.stdout.write('{}');
    }
  } catch {
    process.stdout.write(JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "UserPromptSubmit",
        additionalContext: REMINDER
      }
    }));
  }
});
