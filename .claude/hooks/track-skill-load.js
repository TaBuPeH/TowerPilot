/**
 * PostToolUse hook (matcher: Skill) — tracks which skills get loaded during a session.
 * Writes to a state file so the PostCompact hook knows what to reload.
 *
 * Also clears the post-compaction reload flag once all required skills have been
 * reloaded AFTER compaction (tracked via reloadedSkills array on the flag itself,
 * not the pre-compaction loaded-skills list).
 *
 * This hook covers the CLAUDE host only, because `Skill` is a Claude tool. The
 * Codex equivalent — `workflow.js skills load` in a Bash call — is recorded by
 * `phase-merge-detect.js`, which is the PostToolUse/Bash hook that already
 * exists on both hosts. See `recordSkillsLoadFromBash` in
 * `../workflow/lib/loaded-skills.js` for why the recording happens in a hook at
 * all, and the docblock in `phase-merge-detect.js` for why it happens in THAT
 * hook rather than a new one.
 */
/* The loaded-skills file layout is shared with the OB-288 gate that reads it and
 * with `workflow.js skills load`, which writes it from a separate process
 * wherever that process is permitted to. Multiple writers of one format is
 * exactly where a silent divergence would present as "the gate never opens", so
 * the format lives in one module and all of them ask.
 *
 * `trackPostCompactReload` moved into that module in OB-492 for the same reason:
 * the Codex-side recorder needs the identical convergence rule, and a rule with
 * two implementations converges on two different answers. */
const { recordSkillLoad, trackPostCompactReload } = require('../workflow/lib/loaded-skills.js');

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => { input += chunk; });
process.stdin.on('end', () => {
  try {
    const data = JSON.parse(input);
    const sessionId = data.session_id
      || process.env.CLAUDE_SESSION_ID
      || process.env.SESSION_ID
      || 'default';

    // Extract skill name from the tool input
    const skillName = data.tool_input?.skill
      || data.input?.skill
      || null;

    if (!skillName) {
      process.stdout.write('{}');
      return;
    }

    // Track the skill in the session's loaded-skills list (creates STATE_DIR).
    recordSkillLoad(sessionId, skillName);

    // --- Check if post-compaction reload flag exists ---
    // Track this skill as reloaded AFTER compaction (on the flag itself)
    trackPostCompactReload(sessionId, skillName);

  } catch { /* never fail */ }

  process.stdout.write('{}');
});
