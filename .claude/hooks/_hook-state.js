/**
 * Shared state utility for Claude Code hooks.
 *
 * Provides session-scoped acknowledgement tracking so blocking hooks
 * can fire once per session (to force skill loading) and then pass
 * through on subsequent calls.
 *
 * State files are stored in os.tmpdir() alongside skills-reminder state.
 */
const fs = require('fs');
const path = require('path');
const os = require('os');

const STATE_DIR = path.join(os.tmpdir(), 'claude-hooks-state');

/** Ensure the state directory exists. */
function ensureDir() {
  if (!fs.existsSync(STATE_DIR)) {
    fs.mkdirSync(STATE_DIR, { recursive: true });
  }
}

/**
 * Build the ack filename for a given session + category.
 * Categories: "plan-file", "pending-task", "audit-artefact", "outline-sync"
 */
function ackPath(sessionId, category) {
  return path.join(STATE_DIR, `hook-ack-${sessionId}-${category}.json`);
}

/**
 * Check if an ack file exists for this session + category.
 * Returns true if the skill/workflow was already acknowledged.
 *
 * When sessionId is 'shared', checks ALL ack files for the given category
 * created within the last 2 hours. This allows parallel agents (which get
 * different session IDs) to share ack state within a conversation window.
 */
function hasAck(sessionId, category) {
  if (sessionId === 'shared') {
    return hasSharedAck(category);
  }
  return fs.existsSync(ackPath(sessionId, category));
}

/**
 * Check if ANY ack file for this category exists within the time window.
 * Used when sessionId is 'shared' to support parallel agent ack sharing.
 */
function hasSharedAck(category, maxAgeMs = 2 * 60 * 60 * 1000) {
  try {
    ensureDir();
    const now = Date.now();
    const suffix = `-${category}.json`;
    const files = fs.readdirSync(STATE_DIR);
    for (const file of files) {
      if (!file.startsWith('hook-ack-') || !file.endsWith(suffix)) continue;
      const filePath = path.join(STATE_DIR, file);
      const stat = fs.statSync(filePath);
      if (now - stat.mtimeMs < maxAgeMs) return true;
    }
  } catch { /* Non-critical */ }
  return false;
}

/**
 * Create an ack file for this session + category.
 * Called after the first block fires — subsequent calls will pass through.
 */
function setAck(sessionId, category) {
  ensureDir();
  fs.writeFileSync(ackPath(sessionId, category), JSON.stringify({
    sessionId,
    category,
    createdAt: Date.now(),
  }));
}

/**
 * Extract session ID from hook input data.
 * Falls back to env vars and then 'default'.
 */
function getSessionId(data) {
  return data.session_id
    || process.env.CLAUDE_SESSION_ID
    || process.env.SESSION_ID
    || 'default';
}

/**
 * Delete stale state files.
 * - hook-ack-* older than maxAgeHours (default 24h)
 * - post-compact-pending-reload-* older than 30min (readers' TTL — anything
 *   older is an orphan from a dead session and can never fire usefully)
 * Runs on every hook invocation to prevent unbounded growth.
 */
function cleanStaleFiles(maxAgeHours = 24) {
  try {
    ensureDir();
    const ackMaxAgeMs = maxAgeHours * 60 * 60 * 1000;
    const reloadMaxAgeMs = 30 * 60 * 1000;
    const now = Date.now();
    const files = fs.readdirSync(STATE_DIR);
    for (const file of files) {
      const filePath = path.join(STATE_DIR, file);
      let maxAge = null;
      if (file.startsWith('hook-ack-')) maxAge = ackMaxAgeMs;
      else if (file.startsWith('post-compact-pending-reload-')) maxAge = reloadMaxAgeMs;
      if (maxAge === null) continue;
      try {
        const stat = fs.statSync(filePath);
        if (now - stat.mtimeMs > maxAge) fs.unlinkSync(filePath);
      } catch { /* per-file error — skip */ }
    }
  } catch {
    // Non-critical — silently ignore cleanup errors
  }
}

module.exports = { hasAck, setAck, getSessionId, cleanStaleFiles, STATE_DIR };
