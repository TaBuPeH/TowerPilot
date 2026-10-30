import type { TestReference } from './specData.ts';

/**
 * Spec section 5, the safety ledger, as one rule shared by the ledger test and
 * the ledger reporter: every test file the ledger names under tests/unit/core,
 * tests/unit/contract, tests/unit/api, tests/unit/store or tests/unit/features must hold, for each
 * rule that names it, a test titled with the rule id that PASSED in this run
 * (F1-01: a skipped, todo or commented-out test is no proof). References to
 * tests/e2e/ files are proved by Playwright (`npm run e2e`); this suite checks that
 * each names a test titled with its rule id (tests/unit/spec/safetyLedger.test.ts).
 */
const PROVED_DIRS = [
  'tests/unit/core/',
  'tests/unit/contract/',
  'tests/unit/api/',
  'tests/unit/store/',
  'tests/unit/features/',
];

/** A reference this suite proves: a tests/unit/core, tests/unit/contract, tests/unit/api, tests/unit/store or tests/unit/features file. */
export function isProvedHere(reference: TestReference): boolean {
  return PROVED_DIRS.some((dir) => reference.file.startsWith(dir));
}

/** A reference Playwright proves (a tests/e2e/ spec). */
export function isBrowserProof(reference: TestReference): boolean {
  return reference.file.startsWith('tests/e2e/');
}

/**
 * Spec 9 item 1: the 74 Wanted-baseline rows of section 4, each proved by a
 * passing test whose title starts with its F-id ("F-084: ..." or "F-084 ...").
 */
export const WANTED_BASELINE: readonly string[] = [
  ...['001', '002', '003', '004', '005'],
  ...Array.from({ length: 37 }, (_, n) => String(7 + n).padStart(3, '0')),
  '049',
  '050',
  ...['055', '056', '057', '058', '059'],
  ...['066', '067', '068', '069'],
  ...Array.from({ length: 13 }, (_, n) => String(77 + n).padStart(3, '0')),
  '098',
  ...Array.from({ length: 7 }, (_, n) => String(107 + n).padStart(3, '0')),
].map((digits) => `F-${digits}`);

/** True for a test title that starts with the capability id. */
export function titledWithCapability(title: string, id: string): boolean {
  return title === id || title.startsWith(`${id} `) || title.startsWith(`${id}:`);
}

/** The capability ids no passing test title starts with, given every passing title of a run. */
export function capabilityFailures(
  ids: readonly string[],
  passedTitles: readonly string[],
): string[] {
  return ids
    .filter((id) => !passedTitles.some((title) => titledWithCapability(title, id)))
    .map((id) => `${id}: no passing test titled "${id}: ..."`);
}

/** True for a test title that starts with the rule id. */
export function titledWith(title: string, id: string): boolean {
  return title === id || title.startsWith(`${id} `);
}

/**
 * What is missing, given the titles of the tests that passed per file in one
 * run (undefined: the file was not part of this run, as in a partial run).
 */
export function proofFailures(
  rules: readonly { readonly id: string; readonly tests: readonly TestReference[] }[],
  passedTitles: (file: string) => readonly string[] | undefined,
): string[] {
  const failures: string[] = [];
  for (const row of rules) {
    for (const ref of row.tests.filter((entry) => isProvedHere(entry) && entry.ruleId !== null)) {
      const passed = passedTitles(ref.file);
      if (passed === undefined) continue;
      if (!passed.some((title) => titledWith(title, row.id))) {
        failures.push(`${row.id}: no passing test titled "${row.id} ..." in ${ref.file}`);
      }
    }
  }
  return failures;
}
