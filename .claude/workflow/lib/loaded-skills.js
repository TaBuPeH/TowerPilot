'use strict';

/**
 * The per-session loaded-skills cache — read and write, in one place.
 *
 * WHY THIS EXISTS
 * ---------------
 * This cache is what `plan-skills-required-block.js` (OB-288) compares
 * `marker.requiredSkills` against, so it is the sole release mechanism for a gate
 * that hard-blocks Edit/Write/MultiEdit/Bash. Until OB-492 Phase 4 it had exactly
 * one writer (`track-skill-load.js`, PostToolUse matcher `Skill`) and one reader
 * (the gate), each with its own inline copy of the file layout.
 *
 * That was survivable with two copies. It is not survivable with three: the CLI
 * writer added below runs in a different process from both, and a divergence
 * between what it writes and what the gate reads would not fail loudly — it would
 * present as "the gate never opens", which is indistinguishable from the bug this
 * whole module exists to fix. So the layout lives here and the callers ask.
 *
 * (This is the same lesson as `evaluateProbeGate` in `memory-lifecycle.js`, found
 * by the same Phase 4 audit: three hand-rolled copies of one rule had already
 * drifted, and only the shared function made the drift impossible rather than
 * merely fixed.)
 *
 * WHY tmpdir
 * ----------
 * Session-scoped and deliberately non-durable: a skill load is context state, and
 * context does not survive the session. `os.tmpdir()` resolves identically for the
 * hook process and any CLI process on the same machine and user, which is what
 * makes the cross-process handshake work at all.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');

const STATE_DIR = path.join(os.tmpdir(), 'claude-hooks-state');

/**
 * What a skill slug may look like — the canonical definition.
 *
 * Two independent jobs ride on this one pattern, which is why it is here rather
 * than inline at either call site:
 *
 *   1. TRAVERSAL GUARD. Every consumer joins the slug onto
 *      `.cursor/skills/<slug>/SKILL.md`, so an unconstrained slug turns a skill
 *      loader into an arbitrary-path prober. `handleSkillsLoad` rejects on shape
 *      BEFORE touching the filesystem for exactly this reason.
 *   2. CACHE-KEY AGREEMENT. The gate compares against lowercase slugs from
 *      `marker.requiredSkills`. Recording `Plan-Sync` (which resolves on a
 *      case-insensitive filesystem) is worse than failing: it can never match,
 *      so the gate stays shut with nothing to explain why.
 *
 * A looser copy at any one call site silently defeats both for that caller, and
 * neither failure announces itself — so there is one copy.
 */
const SKILL_SLUG_PATTERN = /^[a-z0-9][a-z0-9-]*$/;

/** Absolute path to a session's loaded-skills state file. */
function stateFilePath(sessionId) {
  return path.join(STATE_DIR, `loaded-skills-${sessionId}.json`);
}

/**
 * Read the slugs recorded for a session. Returns `[]` on a missing or corrupt
 * file — the conservative default, because this feeds a gate whose failure mode
 * on empty is "block", not "allow".
 */
function readLoadedSkills(sessionId) {
  try {
    const state = JSON.parse(fs.readFileSync(stateFilePath(sessionId), 'utf8'));
    if (state && Array.isArray(state.skills)) {
      return state.skills.filter((slug) => typeof slug === 'string');
    }
  } catch { /* no file / corrupt — treat as empty */ }
  return [];
}

/**
 * Record a slug as loaded for a session. Idempotent: re-recording an already
 * present slug is a no-op on content and only refreshes `lastUpdated`.
 *
 * Returns the full slug list after the write so callers can report progress
 * ("14/18 loaded") without a second read that could race another process.
 */
function recordSkillLoad(sessionId, slug) {
  if (!fs.existsSync(STATE_DIR)) {
    fs.mkdirSync(STATE_DIR, { recursive: true });
  }

  const skills = readLoadedSkills(sessionId);
  if (!skills.includes(slug)) {
    skills.push(slug);
  }

  fs.writeFileSync(
    stateFilePath(sessionId),
    JSON.stringify({ skills, lastUpdated: Date.now() }, null, 2),
  );

  return skills;
}

const RELOAD_FLAG_MAX_AGE_MS = 30 * 60 * 1000;

/**
 * If a post-compaction reload flag exists, record this skill as reloaded
 * AFTER compaction. When all required skills are accounted for, mark the
 * flag as reloaded (which unblocks code edits).
 *
 * The key insight: the loaded-skills-{session}.json file contains skills
 * from BEFORE compaction too, so we can't use it to verify post-compaction
 * reloads. Instead, we track reloads in `flag.reloadedSkills[]` — only
 * skills loaded AFTER the flag was created count.
 *
 * This lived inside `track-skill-load.js` until OB-492. It moved here for the
 * same reason `recordSkillLoad` did: it is now called from two hooks, and a
 * convergence rule with two implementations converges on two different answers.
 */
function trackPostCompactReload(sessionId, skillName) {
  const now = Date.now();

  // Find the session-scoped reload flag. Session-scoped only — a session-agnostic
  // fallback previously leaked prior-session reload state into new sessions within
  // the 30-min TTL.
  let flagPath = null;
  let flag = null;

  const sessionFlag = path.join(STATE_DIR, `post-compact-pending-reload-${sessionId}.json`);
  try {
    if (fs.existsSync(sessionFlag)) {
      const parsed = JSON.parse(fs.readFileSync(sessionFlag, 'utf8'));
      if (parsed.timestamp && (now - parsed.timestamp) < RELOAD_FLAG_MAX_AGE_MS && !parsed.reloaded) {
        flagPath = sessionFlag;
        flag = parsed;
      }
    }
  } catch { /* no flag */ }

  if (!flag || !flagPath) return;

  // Initialize reloadedSkills array if not present
  if (!Array.isArray(flag.reloadedSkills)) {
    flag.reloadedSkills = [];
  }

  // Record this skill as reloaded post-compaction
  if (!flag.reloadedSkills.includes(skillName)) {
    flag.reloadedSkills.push(skillName);
  }

  // Check if all required skills have been reloaded
  const requiredSkills = flag.skills || [];
  if (requiredSkills.length === 0) {
    // No specific skills were tracked before compaction — clear after ANY skill reload
    flag.reloaded = true;
    flag.reloadedAt = now;
  } else {
    // Clear only when the FULL set has been reloaded. This is achievable without an
    // infinite loop because post-compact-reload.js carries reloadedSkills forward
    // across compactions (monotonic progress), so the accumulated set converges on
    // the full list instead of resetting to empty on every compaction.
    const allReloaded = requiredSkills.every((s) => flag.reloadedSkills.includes(s));
    if (allReloaded) {
      flag.reloaded = true;
      flag.reloadedAt = now;
    }
  }

  fs.writeFileSync(flagPath, JSON.stringify(flag));
}

/**
 * Record the skills that a `workflow.js skills load` Bash command really loaded.
 *
 * WHY A HOOK RECORDS WHAT THE CLI RAN (OB-492)
 * --------------------------------------------
 * A Claude host loads a skill with the `Skill` tool. A Codex host has no such
 * tool and runs `workflow.js skills load <slug>` in its shell instead. That CLI
 * used to record the load itself, and on a read-only Codex worker it CANNOT:
 * the model's shell is sandboxed, so writing STATE_DIR gets `EPERM` and the
 * process died before recording anything. The worker then stayed blocked no
 * matter how many times it complied.
 *
 * Two properties of hook subprocesses fix that, and only hooks have both:
 *
 *   • Hooks are NOT sandboxed. Measured in read-only Codex session
 *     `019fae48-a8ed-7cb2-879a-6900e33fb6a0`: hooks wrote `skills-reminder-*.json`,
 *     `token-counter-*.json` and `audit-stop-blocked-*.json` into STATE_DIR while
 *     the model's own Bash command was denied it.
 *   • Hooks receive the session id the GATE reads. Codex passes its own UUIDv7 as
 *     `data.session_id`; that is NOT the operator's `--session` string, and the
 *     worker has no way to discover it. A CLI that COULD write would still key the
 *     cache under an id nothing reads. Passing the hook's own `sessionId` in here
 *     makes the key correct by construction rather than by convention.
 *
 * EVIDENCE-LEVEL PARITY — this is not a weaker bar than the `Skill` tool path.
 * `track-skill-load.js` records from `tool_input.skill` and never inspects the
 * tool's output; it trusts that the harness, asked for that skill, put it in
 * front of the model. Recording from `tool_input.command` is the identical bar.
 * It is in fact strictly stricter: the command must first satisfy the same
 * shape matcher the PreToolUse gate uses to ADMIT it, and every slug must have a
 * real `.cursor/skills/<slug>/SKILL.md` on disk. That on-disk check is what keeps
 * this an act rather than an assertion — you cannot record a skill the repo does
 * not have — and it mirrors the identical refusal in `handleSkillsLoad`.
 *
 * THROWS on an I/O failure rather than swallowing — the same posture as
 * `recordSkillLoad`, which it wraps. Its only caller is a hook with its own
 * "never break the tool flow" contract, and that caller wraps this in a guard of
 * its own; a library that silently ate write errors would deny the caller the
 * choice and hide the sandbox case from `handleSkillsLoad`, which needs to see
 * it to report honestly.
 *
 * SHAPE CHECK BEFORE I/O — load-bearing, not incidental. Every path that decides
 * "this is not a skills load" returns before touching the filesystem, so this
 * function is a no-op on the overwhelming majority of Bash commands and cannot
 * throw on them. That is what lets it be hosted inside an unrelated hook without
 * putting that hook's behaviour at risk. Adding eager I/O above the parse would
 * quietly transfer a read-only worker's `EPERM` onto the host's output path; the
 * host's guard absorbs it, and `hooks/tests/phase-merge-detect.test.js` pins
 * that pairing.
 *
 * Returns the slugs it recorded (possibly empty).
 */
function recordSkillsLoadFromBash({ command, sessionId, workspaceRoot }) {
  /* Required lazily: `gate-escape.js` imports SKILL_SLUG_PATTERN from this
   * module, so a top-level import here would close a require cycle. The
   * dependency direction that matters is gate-escape → loaded-skills; this call
   * is the one place it runs the other way. */
  const { parseSkillsLoadSlugs } = require('./gate-escape.js');

  if (!sessionId) {
    return [];
  }

  const recorded = [];
  for (const slug of parseSkillsLoadSlugs(command)) {
    if (!fs.existsSync(path.join(workspaceRoot, '.cursor', 'skills', slug, 'SKILL.md'))) {
      continue;
    }
    recordSkillLoad(sessionId, slug);
    /* The CLI path needs this as much as the Skill path does: on a host with no
     * Skill tool it is the ONLY way a post-compaction reload can ever converge. */
    trackPostCompactReload(sessionId, slug);
    recorded.push(slug);
  }

  return recorded;
}

module.exports = {
  SKILL_SLUG_PATTERN,
  STATE_DIR,
  readLoadedSkills,
  recordSkillLoad,
  recordSkillsLoadFromBash,
  stateFilePath,
  trackPostCompactReload,
};
