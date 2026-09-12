'use strict';

/**
 * The workflow CLI's argv grammar — positional words vs named flags.
 *
 * WHY THIS IS A MODULE AND NOT A FUNCTION IN workflow.js
 * ------------------------------------------------------
 * `handleSkillsLoad` decides which arguments are SKILL SLUGS by taking
 * `parsed._.slice(2)` — the positionals this function produces. As of OB-492 the
 * PostToolUse recorder (`track-skill-load.js`, via `gate-escape.parseSkillsLoadSlugs`)
 * has to answer the same question about the same command string from a different
 * process, because the CLI may be running sandboxed and unable to write the cache
 * the gate reads.
 *
 * Two independent answers to "which of these tokens is a slug?" is the exact
 * divergence class `loaded-skills.js` and `gate-escape.js` were both created to
 * delete, and here it fails in the worst available direction: a recorder that
 * counted one token more than the CLI printed would record a skill nobody read,
 * turning an ACT ("the SKILL.md went past your eyes") back into an ASSERTION
 * ("trust me, I loaded it") — the design this whole mechanism refused.
 *
 * So the grammar lives here and both callers ask. `workflow.js` re-exports
 * `parseArgs` unchanged, so every existing importer is unaffected.
 */

/** Parse CLI arguments into positional values and repeatable named flags. */
function parseArgs(argv) {
  const parsed = { _: [] };

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (!argument.startsWith('--')) {
      parsed._.push(argument);
      continue;
    }

    const withoutPrefix = argument.slice(2);
    const [rawKey, inlineValue] = withoutPrefix.split('=');
    const nextArgument = argv[index + 1];
    const value = inlineValue !== undefined
      ? inlineValue
      : nextArgument && !nextArgument.startsWith('--')
        ? nextArgument
        : true;

    if (inlineValue === undefined && value !== true) {
      index += 1;
    }

    if (Object.prototype.hasOwnProperty.call(parsed, rawKey)) {
      parsed[rawKey] = Array.isArray(parsed[rawKey])
        ? [...parsed[rawKey], value]
        : [parsed[rawKey], value];
    } else {
      parsed[rawKey] = value;
    }
  }

  return parsed;
}

module.exports = { parseArgs };
