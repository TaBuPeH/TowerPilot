/**
 * Statusline sensor for the usage-limit guard (OB-736) — pairs with pretooluse-gate.js.
 *
 * Claude Code invokes this as the user's statusLine command (wired by
 * setup-usage-limit-guard.js into ~/.claude/settings.json with refreshInterval 60).
 * The statusline stdin JSON is the ONLY surface where the subscription usage
 * windows are exposed: rate_limits.five_hour / rate_limits.seven_day, each with
 * used_percentage (0-100) and resets_at (epoch seconds). Fields appear only on
 * subscription plans and only after the session's first API response.
 *
 * What this script ACTUALLY does on every invocation:
 *   1. Parses stdin JSON (malformed input degrades to an empty object).
 *   2. Writes a snapshot to ~/.claude/usage-limit/state-<sessionId>.json AND
 *      state.json (global last-writer fallback for readers without a session id).
 *      Percentages/resets are null when rate_limits is absent.
 *   3. Prints a one-line meter: "5h 42.5% (resets 14:30) | 7d 12.1%", prefixed
 *      with "⛔ PAUSE " when either window is >= THRESHOLD, or "usage n/a" when
 *      no rate-limit data exists. A failed state write never breaks the render.
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const STATE_DIR = path.join(os.homedir(), '.claude', 'usage-limit');
// Display-only copy of the trigger; the enforcing copy lives in pretooluse-gate.js.
const THRESHOLD = 95;

// Format an epoch-seconds timestamp as local HH:MM, or '?' when absent.
function fmtTime(epochSec) {
  if (!epochSec) return '?';
  const d = new Date(epochSec * 1000);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

let raw = '';
process.stdin.on('data', (c) => (raw += c));
process.stdin.on('end', () => {
  let input = {};
  try {
    input = JSON.parse(raw);
  } catch {
    // Malformed stdin: still render something so the statusline never errors.
  }
  const rl = input.rate_limits || {};
  const fh = rl.five_hour || {};
  const sd = rl.seven_day || {};
  const state = {
    sessionId: input.session_id || null,
    fiveHourPct: typeof fh.used_percentage === 'number' ? fh.used_percentage : null,
    fiveHourResetsAt: fh.resets_at || null,
    sevenDayPct: typeof sd.used_percentage === 'number' ? sd.used_percentage : null,
    sevenDayResetsAt: sd.resets_at || null,
    updatedAt: Math.floor(Date.now() / 1000),
  };
  const json = JSON.stringify(state);
  try {
    fs.mkdirSync(STATE_DIR, {recursive: true});
    if (state.sessionId) fs.writeFileSync(path.join(STATE_DIR, `state-${state.sessionId}.json`), json);
    fs.writeFileSync(path.join(STATE_DIR, 'state.json'), json);
  } catch {
    // State persistence is best-effort; the meter still renders.
  }

  if (state.fiveHourPct === null && state.sevenDayPct === null) {
    process.stdout.write('usage n/a');
    return;
  }
  const parts = [];
  if (state.fiveHourPct !== null) parts.push(`5h ${state.fiveHourPct.toFixed(1)}% (resets ${fmtTime(state.fiveHourResetsAt)})`);
  if (state.sevenDayPct !== null) parts.push(`7d ${state.sevenDayPct.toFixed(1)}%`);
  const over = (state.fiveHourPct ?? 0) >= THRESHOLD || (state.sevenDayPct ?? 0) >= THRESHOLD;
  process.stdout.write((over ? '⛔ PAUSE ' : '') + parts.join(' | '));
});
