/**
 * PreToolUse hook (matcher: Write|Edit|MultiEdit) — SKILL.md structure guard
 * (OB-439 D6 / skill-writing-guide Rules 9 + 10).
 *
 * The skill fleet re-bloated once after the 2026-05-05 sidecar split because
 * nothing pushed content back OUT of SKILL.md — amendments accreted as inline
 * `Decision NN` / `Amendment` sections and hot-path files grew past 70KB
 * (measured 2026-07-03: hot-path 8 = 284KB, forced into context every tracked
 * session). This hook is the deterministic regression stop.
 *
 * Applies ONLY to `SKILL.md` files under a `skills/<name>/` directory (both
 * the `.cursor/skills` tree and its `.claude` junction view). Sidecars (`reference.md`,
 * `history.md`, `procedures.md`, …) are exempt by design — they are exactly
 * where the content is supposed to go.
 *
 * Checks on the RESULTING file content (current file + proposed edit):
 *   1. HARD BLOCK — size ceiling: 14.4KB for the Rule 9 hot-path list,
 *      21.6KB for every other skill (skill-writing-guide Rule 10; 1.8x the
 *      original 8KB/12KB, corrected 2026-07-03 from a briefly-live 3x raise).
 *   2. HARD BLOCK — inline `Amendment(s)` / `Decision <N>` H2/H3 sections, or
 *      a `Change History` H2/H3 that contains table rows. A pointer-only
 *      `## Change History` (`See history.md`) is the allowed form.
 *   3. ADVISORY — fenced-code ratio > 25% (examples belong in sidecars per
 *      Rule 9). Injected as additionalContext, never blocks.
 *
 * Fail-open like every guard hook: parse/IO errors, un-computable resulting
 * content (e.g. old_string not found — the Edit tool itself will error), or
 * missing input → `{}` passthrough. The guard must never brick an edit flow;
 * worst-case degradation is "no structure enforcement for this one call".
 */
'use strict';

const fs = require('fs');

/** skill-writing-guide Rule 9 hot-path list — loaded almost every tracked session. */
const HOT_PATH_SKILLS = new Set([
  'plan-sync',
  'outline-knowledge-sync',
  'task-workflow',
  'git-workflow',
  'implementation-audit',
  'skill-compliance',
  'jira-agentic-flow',
  'pending-tasks',
]);
const HOT_PATH_CEILING_BYTES = Math.round(8 * 1024 * 1.8); /* 14,746 B */
const DEFAULT_CEILING_BYTES = Math.round(12 * 1024 * 1.8); /* 22,118 B */
const FENCE_RATIO_ADVISORY = 0.25;

/** Extract the skill name when the path is a SKILL.md under a skills tree; null otherwise. */
function targetSkillName(filePath) {
  const normalPath = String(filePath || '').replace(/\\/g, '/');
  const m = normalPath.match(/\/skills\/([^/]+)\/SKILL\.md$/i);
  return m ? m[1].toLowerCase() : null;
}

/**
 * Compute the file content AS IT WOULD BE after the tool call. Returns null
 * when the result cannot be determined (fail-open): Write without string
 * content, unreadable current file, old_string not present (the tool call is
 * going to fail on its own — no point guessing).
 */
function computeResultingContent(toolName, toolInput) {
  if (toolName === 'Write') {
    return typeof toolInput.content === 'string' ? toolInput.content : null;
  }
  const filePath = toolInput.file_path || toolInput.filePath;
  if (!filePath) return null;
  let current;
  try {
    current = fs.readFileSync(filePath, 'utf8');
  } catch {
    return null; /* new file via Edit is impossible; unreadable → fail-open */
  }
  const applyOne = (text, edit) => {
    if (!edit || typeof edit.old_string !== 'string' || typeof edit.new_string !== 'string') return null;
    if (!text.includes(edit.old_string)) return null;
    return edit.replace_all
      ? text.split(edit.old_string).join(edit.new_string)
      : text.replace(edit.old_string, edit.new_string);
  };
  if (toolName === 'Edit') return applyOne(current, toolInput);
  if (toolName === 'MultiEdit') {
    let text = current;
    const edits = Array.isArray(toolInput.edits) ? toolInput.edits : [];
    for (const edit of edits) {
      text = applyOne(text, edit);
      if (text === null) return null;
    }
    return text;
  }
  return null;
}

/**
 * Detect amendment-accretion violations (Rule 10). Returns a human-readable
 * description of the first violation, or null when clean:
 *   • any H2/H3 titled `Amendment(s)…` or `Decision <N>…`
 *   • a `Change History` H2/H3 whose section body contains a markdown table
 *     row (a pointer-only section — `See history.md` — is allowed)
 */
function findAmendmentViolation(content) {
  const lines = content.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const heading = lines[i].match(/^(#{2,3})\s+(.*)$/);
    if (!heading) continue;
    const title = heading[2].trim();
    if (/^(?:Amendments?\b|Decision\s+\d+)/i.test(title)) {
      return `inline "${heading[1]} ${title}" section — merge the change into the affected rule text and log the story in history.md`;
    }
    if (/^Change History\b/i.test(title)) {
      for (let j = i + 1; j < lines.length; j++) {
        if (/^#{1,6}\s+/.test(lines[j])) break;
        if (/^\s*\|/.test(lines[j])) {
          return 'inline "Change History" TABLE — move the rows to history.md; a pointer-only section ("See history.md") is the allowed form';
        }
      }
    }
  }
  return null;
}

/**
 * Approximate share of the content that sits inside ``` / ~~~ fences.
 * Nested 4-backtick-outer/3-backtick-inner fences miscount slightly — this
 * feeds an ADVISORY only, so the approximation is acceptable.
 */
function fencedRatio(content) {
  if (!content) return 0;
  const lines = content.split(/\r?\n/);
  let inFence = false;
  let fenceChars = 0;
  for (const line of lines) {
    if (/^\s*(?:`{3,}|~{3,})/.test(line)) {
      inFence = !inFence;
      fenceChars += line.length + 1;
      continue;
    }
    if (inFence) fenceChars += line.length + 1;
  }
  return fenceChars / (content.length || 1);
}

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => { input += chunk; });
process.stdin.on('end', () => {
  try {
    const data = JSON.parse(input || '{}');
    const toolName = typeof data.tool_name === 'string' ? data.tool_name : '';
    const toolInput = data.tool_input && typeof data.tool_input === 'object' ? data.tool_input : {};
    if (toolName !== 'Write' && toolName !== 'Edit' && toolName !== 'MultiEdit') {
      passthrough();
      return;
    }

    const filePath = String(toolInput.file_path || toolInput.filePath || '');
    const skillName = targetSkillName(filePath);
    if (!skillName) { passthrough(); return; }

    const resulting = computeResultingContent(toolName, toolInput);
    if (resulting === null) { passthrough(); return; }

    /* Check 1 — size ceiling (hard block). Measured LF-normalized: the ceiling
       budgets CONTENT, not platform line endings — core.autocrlf rewrites checkouts
       to CRLF (+1 byte per line, ~100+ B on a full SKILL.md), which would flip a
       ceiling-compliant skill to permanently blocked after a mere `git checkout`. */
    const ceiling = HOT_PATH_SKILLS.has(skillName) ? HOT_PATH_CEILING_BYTES : DEFAULT_CEILING_BYTES;
    const resultingBytes = Buffer.byteLength(resulting.replace(/\r\n/g, '\n'), 'utf8');
    if (resultingBytes > ceiling) {
      block([
        `BLOCKED: SKILL.md size ceiling exceeded for "${skillName}".`,
        '',
        `This edit would make the file ${resultingBytes} bytes; the ceiling is ${ceiling} bytes`
          + ` (${HOT_PATH_SKILLS.has(skillName) ? 'hot-path skill — 14.4KB' : '21.6KB'} per skill-writing-guide Rule 10).`,
        '',
        'Move examples / templates / reference tables / procedures to a sidecar file',
        '(reference.md, procedures.md, templates.md, examples.md) and keep only',
        'directly-actionable instructions in SKILL.md (Rule 9). Decision stories go to',
        'history.md with the rule text merged in place (Rule 10).',
      ]);
      return;
    }

    /* Check 2 — amendment accretion (hard block). */
    const violation = findAmendmentViolation(resulting);
    if (violation) {
      block([
        `BLOCKED: SKILL.md amendment-accretion violation in "${skillName}".`,
        '',
        `Found ${violation}.`,
        '',
        'SKILL.md is current-state-only (skill-writing-guide Rule 10): edit the affected',
        'rule IN PLACE and append the decision story (date, OB key, what changed, why)',
        'as a row in the skill\'s history.md sidecar instead.',
      ]);
      return;
    }

    /* Check 3 — fenced-code ratio (advisory only). */
    const ratio = fencedRatio(resulting);
    if (ratio > FENCE_RATIO_ADVISORY) {
      process.stdout.write(JSON.stringify({
        hookSpecificOutput: {
          hookEventName: 'PreToolUse',
          additionalContext: `SKILL.md advisory ("${skillName}"): ${(ratio * 100).toFixed(0)}% of the resulting file is fenced code `
            + '(> 25%). Per skill-writing-guide Rule 9, long examples belong in a sidecar (reference.md / examples.md) '
            + 'with an imperative pointer from SKILL.md. Not blocking — consider extracting.',
        },
      }));
      return;
    }

    passthrough();
  } catch {
    passthrough(); /* fail-open — a guard bug must never brick an edit */
  }
});

function block(lines) {
  process.stdout.write(JSON.stringify({ decision: 'block', reason: lines.join('\n') }));
}

function passthrough() {
  process.stdout.write('{}');
}
