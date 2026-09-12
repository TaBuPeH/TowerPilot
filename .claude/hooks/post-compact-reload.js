/**
 * PostCompact hook — fires after context compaction.
 *
 * PostCompact hooks CANNOT inject additionalContext (only UserPromptSubmit,
 * PostToolUse, and PreToolUse support hookSpecificOutput with additionalContext).
 *
 * Strategy:
 * 1. Read the list of skills that were loaded before compaction (from track-skill-load.js)
 * 2. Read the session brief from PreCompact
 * 3. Write a "pending-reload" flag file with the skill list + brief
 * 4. The UserPromptSubmit hook (skills-reminder.js) detects this flag on the
 *    next user message and injects the skill reload instructions
 * 5. The PreToolUse hook (skill-check-pre-edit.js) BLOCKS edits until the
 *    flag is cleared (cleared after skills are reloaded)
 */
const fs = require('fs');
const path = require('path');
const os = require('os');

const STATE_DIR = path.join(os.tmpdir(), 'claude-hooks-state');

// Project root — used to check which skill names are actually loadable.
const PROJECT_DIR = process.env.CLAUDE_PROJECT_DIR || path.resolve(__dirname, '..', '..');

/**
 * A skill name is "loadable" only if it resolves to a skill directory or a command
 * markdown under .claude/ or .cursor/. The pre-compaction loaded-skills list can carry
 * stale or aliased names (e.g. a renamed "database-usage" that no longer exists) — and
 * the clear logic in track-skill-load.js requires EVERY listed skill to be reloaded, so
 * one unloadable name wedges the gate shut forever. Filtering to loadable names at write
 * time is the deterministic form of "load one by one, omit if not found".
 */
function isLoadableSkill(name) {
  const candidates = [
    path.join(PROJECT_DIR, '.claude', 'skills', name),
    path.join(PROJECT_DIR, '.cursor', 'skills', name),
    path.join(PROJECT_DIR, '.claude', 'commands', `${name}.md`),
    path.join(PROJECT_DIR, '.cursor', 'commands', `${name}.md`),
  ];
  return candidates.some((p) => {
    try {
      return fs.existsSync(p);
    } catch {
      return false;
    }
  });
}

/**
 * Atomic JSON write — write to a temp sibling then rename. A non-atomic write lets a
 * concurrent reader observe a half-written or empty flag, which parses as empty →
 * fail-open → the post-compaction gate silently disables itself (observed in the wild).
 */
function writeJsonAtomic(filePath, obj) {
  const tmp = `${filePath}.tmp-${process.pid}`;
  fs.writeFileSync(tmp, JSON.stringify(obj));
  fs.renameSync(tmp, filePath);
}

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

    if (!fs.existsSync(STATE_DIR)) {
      fs.mkdirSync(STATE_DIR, { recursive: true });
    }

    // --- 1. Read tracked skills ---
    let skills = [];
    const skillsFile = path.join(STATE_DIR, `loaded-skills-${sessionId}.json`);
    try {
      const state = JSON.parse(fs.readFileSync(skillsFile, 'utf8'));
      if (state.skills && state.skills.length > 0) {
        skills = state.skills;
      }
    } catch { /* no skills tracked */ }

    // --- 2. Read the session brief from PreCompact ---
    let briefContent = '';
    const pointerPath = path.join(STATE_DIR, `session-brief-pointer-${sessionId}.json`);
    try {
      const pointer = JSON.parse(fs.readFileSync(pointerPath, 'utf8'));
      if (pointer.briefPath && fs.existsSync(pointer.briefPath)) {
        briefContent = fs.readFileSync(pointer.briefPath, 'utf8');
      }
    } catch { /* no brief available */ }

    // Fallback: try most recent brief if session ID didn't match
    if (!briefContent) {
      try {
        const files = fs.readdirSync(STATE_DIR)
          .filter(f => f.startsWith('session-brief-') && f.endsWith('.md'))
          .map(f => ({ name: f, mtime: fs.statSync(path.join(STATE_DIR, f)).mtimeMs }))
          .sort((a, b) => b.mtime - a.mtime);
        if (files.length > 0) {
          briefContent = fs.readFileSync(path.join(STATE_DIR, files[0].name), 'utf8');
        }
      } catch { /* fallback failed */ }
    }

    // --- 3. Write the pending-reload flag (session-scoped only) ---
    // This flag is the key mechanism: it persists until skills are reloaded.
    // Intentionally session-scoped only — no session-agnostic fallback, because
    // a cross-session fallback leaked a prior session's pending-reload state
    // into new sessions that happened to start within the 30-min TTL.
    // Only require skills that actually resolve — a stale/aliased name would otherwise
    // wedge the clear logic (which needs EVERY listed skill reloaded) shut forever.
    const loadableSkills = skills.filter(isLoadableSkill);
    const reloadFlag = path.join(STATE_DIR, `post-compact-pending-reload-${sessionId}.json`);

    // Carry forward post-compaction reload progress. Without this, every compaction
    // zeroed `reloadedSkills`, so a long reload run that itself triggered another
    // compaction never accumulated — the gate could never clear. Preserving the prior
    // flag's reloadedSkills makes progress monotonic across compaction generations.
    let carriedReloaded = [];
    try {
      const prev = JSON.parse(fs.readFileSync(reloadFlag, 'utf8'));
      if (Array.isArray(prev.reloadedSkills)) {
        carriedReloaded = prev.reloadedSkills.filter((s) => loadableSkills.includes(s));
      }
    } catch { /* no prior flag → start fresh */ }

    writeJsonAtomic(reloadFlag, {
      sessionId,
      timestamp: Date.now(),
      skills: loadableSkills,
      reloadedSkills: carriedReloaded,
      briefContent,
      reloaded: false
    });

    // Return valid empty output — PostCompact doesn't support additionalContext
    process.stdout.write('{}');
  } catch {
    // If we can't identify the session, we cannot safely write a reload flag —
    // a session-agnostic fallback would leak into unrelated sessions.
    process.stdout.write('{}');
  }
});
