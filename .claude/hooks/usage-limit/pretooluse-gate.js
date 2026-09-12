/**
 * PreToolUse hook (matcher: Agent|Workflow) — usage-limit dispatch gate (OB-736).
 *
 * Wired into ~/.claude/settings.json (user-level) by setup-usage-limit-guard.js.
 * Reads the rate-limit snapshot that statusline.js persisted and DENIES new
 * agent/workflow dispatches when a usage window is >= THRESHOLD (95%) and its
 * reset time is still ahead. In-flight subagents are untouched (their tool
 * calls don't match this matcher), and every non-dispatch tool stays available
 * so the orchestrator can wind down: drain agents, write the resume brief, and
 * register the scheduled resume — the deny reason carries those exact steps.
 *
 * Fail-open cases (each produces an allow, i.e. stdout "{}"):
 *   - state file missing or unparseable for both the per-session and global paths
 *   - snapshot older than STALE_AFTER_SEC (30 min)
 *   - percentages null (no rate_limits from the harness — API-key auth etc.)
 *   - reset time already passed (window rolled over)
 * A sensor problem therefore degrades to pre-guard behavior, never to a brick.
 *
 * When both windows bind, the deny cites the one with the LATEST resets_at —
 * resuming after the 5h reset is pointless if the 7d window is also spent.
 * Every invocation appends one line to ~/.claude/usage-limit/gate.log.
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const STATE_DIR = path.join(os.homedir(), '.claude', 'usage-limit');
const REGISTER_SCRIPT = path.resolve(__dirname, 'register-resume.ps1');
const THRESHOLD = 95;
const STALE_AFTER_SEC = 30 * 60;

// Return the first parseable snapshot in priority order (per-session file,
// then the global last-writer fallback); null when neither exists/parses.
// Timestamps are NOT compared here — staleness is the caller's 30-min check.
function readState(sessionId) {
  const candidates = [];
  if (sessionId) candidates.push(path.join(STATE_DIR, `state-${sessionId}.json`));
  candidates.push(path.join(STATE_DIR, 'state.json'));
  for (const f of candidates) {
    try {
      return JSON.parse(fs.readFileSync(f, 'utf8'));
    } catch {
      // try next candidate
    }
  }
  return null;
}

function log(line) {
  try {
    fs.mkdirSync(STATE_DIR, {recursive: true});
    fs.appendFileSync(path.join(STATE_DIR, 'gate.log'), `${new Date().toISOString()} ${line}\n`);
  } catch {
    // logging is best-effort
  }
}

function fmtTime(epochSec) {
  const d = new Date(epochSec * 1000);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

let raw = '';
process.stdin.on('data', (c) => (raw += c));
process.stdin.on('end', () => {
  let input = {};
  try {
    input = JSON.parse(raw);
  } catch {}
  const sessionId = input.session_id || 'unknown';
  const state = readState(input.session_id);
  const now = Math.floor(Date.now() / 1000);

  if (!state || !state.updatedAt || now - state.updatedAt > STALE_AFTER_SEC) {
    log(`session=${sessionId} tool=${input.tool_name || '?'} decision=allow reason=no-or-stale-state`);
    process.stdout.write('{}');
    return;
  }

  // A window "binds" when it is over threshold AND its reset is still ahead.
  const windows = [
    {label: 'five-hour', pct: state.fiveHourPct, resetsAt: state.fiveHourResetsAt},
    {label: 'seven-day', pct: state.sevenDayPct, resetsAt: state.sevenDayResetsAt},
  ].filter((w) => typeof w.pct === 'number' && w.pct >= THRESHOLD && w.resetsAt && now < w.resetsAt);

  if (windows.length === 0) {
    log(`session=${sessionId} tool=${input.tool_name || '?'} decision=allow 5h=${state.fiveHourPct} 7d=${state.sevenDayPct}`);
    process.stdout.write('{}');
    return;
  }

  const worst = windows.reduce((a, b) => (b.resetsAt > a.resetsAt ? b : a));
  const minsLeft = Math.max(1, Math.round((worst.resetsAt - now) / 60));
  const briefPath = path.join(STATE_DIR, `resume-brief-${sessionId}.md`);
  // cwd from the hook input is the session's project dir — the resume must
  // relaunch claude from there so --resume finds the right project sessions.
  const projectDir = input.cwd || process.cwd();
  const registerCmd =
    `powershell -ExecutionPolicy Bypass -File "${REGISTER_SCRIPT}" -SessionId ${sessionId} -ProjectDir "${projectDir}"`;
  const reason =
    `USAGE-LIMIT GATE: the ${worst.label} usage window is at ${worst.pct.toFixed(1)}% (>= ${THRESHOLD}%), ` +
    `resets at ${fmtTime(worst.resetsAt)} (~${minsLeft} min). New Agent/Workflow dispatches are paused. ` +
    `Wind-down protocol: (1) dispatch NOTHING new; (2) let in-flight agents finish — their outputs are saved to files; ` +
    `(3) write a resume brief (remaining tasks, agent-output file locations, exact next steps) to ${briefPath}; ` +
    `(4) register the scheduled resume by running: ${registerCmd} ; ` +
    `(5) end the turn with a short status summary for the user. ` +
    `The scheduled task runs "claude --resume" 5 minutes after the window resets and instructs the session to read the brief and restart tasking.`;

  log(`session=${sessionId} tool=${input.tool_name || '?'} decision=deny window=${worst.label} pct=${worst.pct} resetsAt=${worst.resetsAt}`);
  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: 'PreToolUse',
        permissionDecision: 'deny',
        permissionDecisionReason: reason,
      },
    }),
  );
});
