#!/usr/bin/env node
/**
 * Token tracker — single hook handling PostToolUse (accumulate) + Stop (emit + reset).
 *
 * Replaces the model-side "report token breakdown" rule in CLAUDE.md with a
 * deterministic hook so the summary surfaces every turn regardless of model
 * attention, conversation length, or sub-agent spawning.
 *
 * State: `<tmpdir>/claude-hooks-state/token-counter-<sessionId>.json`
 * Output: a 2-row markdown table via the Stop hook's `systemMessage` field.
 *
 * Approximation: 4 chars per token (typical English/code mix). Numbers are
 * directionally useful for spotting expensive calls — not metering-grade.
 */
const fs = require('fs');
const path = require('path');
const os = require('os');

const STATE_DIR = path.join(os.tmpdir(), 'claude-hooks-state');
const CHARS_PER_TOKEN = 4;

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => { input += chunk; });
process.stdin.on('end', () => {
  let data = {};
  try { data = JSON.parse(input); } catch { /* malformed input — emit nothing */ }

  const sessionId = data.session_id
    || process.env.CLAUDE_SESSION_ID
    || process.env.SESSION_ID
    || 'default';

  // PostToolUse payloads carry tool_name + tool_response; Stop payloads don't.
  const isToolEvent = !!(data.tool_name || data.tool_response);

  try {
    if (!fs.existsSync(STATE_DIR)) fs.mkdirSync(STATE_DIR, { recursive: true });
  } catch { /* race-safe; ignore */ }

  const counterPath = path.join(STATE_DIR, `token-counter-${sessionId}.json`);
  let counter = { turnIn: 0, turnOut: 0, sessionIn: 0, sessionOut: 0, lastUpdated: Date.now() };
  try { counter = { ...counter, ...JSON.parse(fs.readFileSync(counterPath, 'utf8')) }; } catch { /* fresh session */ }

  if (isToolEvent) {
    const inChars = data.tool_input ? JSON.stringify(data.tool_input).length : 0;
    const outChars = data.tool_response
      ? (typeof data.tool_response === 'string'
        ? data.tool_response.length
        : JSON.stringify(data.tool_response).length)
      : 0;
    const inTok = Math.ceil(inChars / CHARS_PER_TOKEN);
    const outTok = Math.ceil(outChars / CHARS_PER_TOKEN);
    counter.turnIn += inTok;
    counter.turnOut += outTok;
    counter.sessionIn += inTok;
    counter.sessionOut += outTok;
    counter.lastUpdated = Date.now();
    try { fs.writeFileSync(counterPath, JSON.stringify(counter, null, 2)); } catch { /* swallow */ }
    process.stdout.write('{}');
    return;
  }

  // Stop event — emit summary if there was any tool activity this turn.
  const turnTotal = counter.turnIn + counter.turnOut;
  if (turnTotal === 0) {
    process.stdout.write('{}');
    return;
  }
  const sessionTotal = counter.sessionIn + counter.sessionOut;

  const fmt = (n) => '~' + n.toLocaleString('en-US');
  const message = [
    '**Token breakdown (this turn):**',
    '',
    '| | Input | Output | Total |',
    '|---|---|---|---|',
    `| This turn subtotal | ${fmt(counter.turnIn)} | ${fmt(counter.turnOut)} | ${fmt(turnTotal)} |`,
    `| Session total (cumulative) | ${fmt(counter.sessionIn)} | ${fmt(counter.sessionOut)} | ${fmt(sessionTotal)} |`,
  ].join('\n');

  // Reset per-turn counters; session totals persist.
  counter.turnIn = 0;
  counter.turnOut = 0;
  counter.lastUpdated = Date.now();
  try { fs.writeFileSync(counterPath, JSON.stringify(counter, null, 2)); } catch { /* swallow */ }

  process.stdout.write(JSON.stringify({ systemMessage: message }));
});
