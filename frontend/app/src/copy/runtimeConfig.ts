import { entry } from './entry.ts';

export const runtimeConfig = {
  defaults: entry('Machine defaults - shared fallback values'),
  overrides: entry('Overrides for '),
  openId: entry(' ('),
  closeId: entry(')'),
  machine: entry('Machine tap and logging settings'),
  groupNotices: {
    defaults: entry(
      'Machine fallback · next time automation starts. Existing emulator overrides are kept.',
    ),
    overrides: entry(
      "This emulator · next time automation starts. Saving creates or replaces this emulator's override.",
    ),
    machine: entry('This computer · every emulator · next time automation starts.'),
  },
  fallback: entry('Saved defaults / Machine fallback'),
  instancePreset: entry("This emulator's saved default preset: "),
  fallbackNotice: entry(
    "An emulator's own default preset takes precedence over this machine fallback. Saving never starts a run, changes the run picker, or rewrites a blueprint.",
  ),
  modules: {
    'loop-defaults': entry('Machine defaults · Observation and dry run'),
    'missions-defaults': entry('Machine defaults · Mission timing'),
    'fleet-defaults': entry('Machine defaults · Fleet windows'),
    loop: entry('This emulator override · Observation and dry run'),
    missions: entry('This emulator override · Mission timing'),
    fleet: entry('This emulator override · Fleet windows'),
    tap: entry('This computer · Tap timing and rate'),
    logging: entry('This computer · Diagnostic logging'),
    'run-defaults': entry('Machine fallback · Run defaults'),
  },
};
