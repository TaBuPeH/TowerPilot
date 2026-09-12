#!/usr/bin/env node
/**
 * Per-machine installer for the usage-limit guard (OB-736).
 *
 * What it does (idempotent — running twice is a no-op):
 *   - Merges into the operator's USER-LEVEL ~/.claude/settings.json:
 *       statusLine        -> node "<this repo>/.cursor/hooks/usage-limit/statusline.js"
 *                            (refreshInterval 60)
 *       hooks.PreToolUse  -> matcher "Agent|Workflow" running pretooluse-gate.js
 *   - Recognizes entries it (or the pre-rollout prototype) wrote by the path
 *     fragment "usage-limit/statusline.js" / "usage-limit/pretooluse-gate.js"
 *     and rewrites them to this checkout's absolute paths (prototype migration).
 *   - REFUSES to touch a statusLine it did not write: the operator's own
 *     statusline is preserved and manual wiring instructions are printed; the
 *     PreToolUse gate is still installed (it fail-opens without sensor data).
 *   - Backs the settings file up to settings.json.bak before every write.
 *
 * Usage:
 *   node .cursor/hooks/usage-limit/setup-usage-limit-guard.js            # install/repair
 *   node .cursor/hooks/usage-limit/setup-usage-limit-guard.js --revert   # remove our entries
 *
 * Why user-level and not project settings: a statusLine committed to project
 * settings would override every teammate's personal statusline (user -> project
 * precedence), and a user-level hook cannot rely on $CLAUDE_PROJECT_DIR.
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const SETTINGS_PATH = path.join(os.homedir(), '.claude', 'settings.json');
const BAK_PATH = SETTINGS_PATH + '.bak';
// Forward slashes work in both the bash and powershell hook shells on Windows.
const HERE = __dirname.replace(/\\/g, '/');
const STATUSLINE_CMD = `node "${HERE}/statusline.js"`;
const GATE_CMD = `node "${HERE}/pretooluse-gate.js"`;
// Path fragments that identify entries written by this installer or the
// pre-rollout operator-0 prototype (which lived in ~/.claude/usage-limit/).
const SENSOR_MARK = /usage-limit[\\/]statusline\.js/;
const GATE_MARK = /usage-limit[\\/]pretooluse-gate\.js/;

const revert = process.argv.slice(2).includes('--revert');

function out(msg) {
  console.log(`[setup-usage-limit] ${msg}`);
}

function readJsonOrNull(p) {
  if (!fs.existsSync(p)) return null;
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch (e) {
    console.error(`[setup-usage-limit] ${p} is not valid JSON: ${e.message}`);
    process.exit(1);
  }
}

function writeJson(p, obj) {
  fs.writeFileSync(p, JSON.stringify(obj, null, 2) + '\n');
}

// True when a PreToolUse matcher group contains our gate (by path fragment).
function isOurGateGroup(group) {
  return Array.isArray(group.hooks) && group.hooks.some((h) => typeof h.command === 'string' && GATE_MARK.test(h.command));
}

const settings = readJsonOrNull(SETTINGS_PATH);
if (!settings) {
  console.error(`[setup-usage-limit] ${SETTINGS_PATH} not found — start Claude Code once first, then re-run.`);
  process.exit(1);
}
const before = JSON.stringify(settings);

if (revert) {
  if (settings.statusLine && SENSOR_MARK.test(settings.statusLine.command || '')) delete settings.statusLine;
  if (settings.hooks && Array.isArray(settings.hooks.PreToolUse)) {
    settings.hooks.PreToolUse = settings.hooks.PreToolUse.filter((g) => !isOurGateGroup(g));
    if (settings.hooks.PreToolUse.length === 0) delete settings.hooks.PreToolUse;
    if (Object.keys(settings.hooks).length === 0) delete settings.hooks;
  }
  if (JSON.stringify(settings) === before) {
    out('nothing to revert — no usage-limit entries found.');
    process.exit(0);
  }
  fs.copyFileSync(SETTINGS_PATH, BAK_PATH);
  writeJson(SETTINGS_PATH, settings);
  out(`reverted usage-limit entries in ${SETTINGS_PATH} (backup at ${BAK_PATH}).`);
  process.exit(0);
}

// --- statusLine (sensor) ---
let sensorInstalled = false;
const sl = settings.statusLine;
if (!sl) {
  settings.statusLine = {type: 'command', command: STATUSLINE_CMD, refreshInterval: 60};
  sensorInstalled = true;
} else if (SENSOR_MARK.test(sl.command || '')) {
  settings.statusLine = {type: 'command', command: STATUSLINE_CMD, refreshInterval: 60};
  sensorInstalled = true;
} else {
  out('SKIPPED statusLine: you already have a custom statusline this installer did not write.');
  out('  To arm the sensor manually, make your statusline script also pipe its stdin JSON to:');
  out(`  ${STATUSLINE_CMD}`);
  out('  (the gate stays fail-open — harmless but inert — until the sensor writes state).');
}

// --- hooks.PreToolUse (gate) ---
settings.hooks = settings.hooks || {};
settings.hooks.PreToolUse = settings.hooks.PreToolUse || [];
const ourGroups = settings.hooks.PreToolUse.filter(isOurGateGroup);
const gateGroup = {
  matcher: 'Agent|Workflow',
  hooks: [{type: 'command', command: GATE_CMD, timeout: 10}],
};
if (ourGroups.length === 0) {
  settings.hooks.PreToolUse.push(gateGroup);
} else {
  // Rewrite in place (covers prototype-path migration and repo moves).
  ourGroups.forEach((g) => {
    g.matcher = gateGroup.matcher;
    g.hooks = gateGroup.hooks;
  });
}

if (JSON.stringify(settings) === before) {
  out('already installed — nothing to do.');
  process.exit(0);
}

fs.copyFileSync(SETTINGS_PATH, BAK_PATH);
writeJson(SETTINGS_PATH, settings);
out(`updated ${SETTINGS_PATH} (backup at ${BAK_PATH}).`);
out(`gate: PreToolUse matcher Agent|Workflow -> ${GATE_CMD}`);
if (sensorInstalled) out(`sensor: statusLine -> ${STATUSLINE_CMD} (refresh 60s)`);
console.log(`
Next steps:
  1. Restart Claude Code (or reload the VSCode window) so the statusline starts.
  2. Verify: the statusline shows "5h N% ..." (or "usage n/a" before the first
     API response), and ~/.claude/usage-limit/state-<session>.json appears.
  3. The gate logs every Agent/Workflow dispatch decision to
     ~/.claude/usage-limit/gate.log — check it after your next agent spawn.
  4. To remove: node .cursor/hooks/usage-limit/setup-usage-limit-guard.js --revert
`);
