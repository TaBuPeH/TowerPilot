import { APP } from '../../support/paths.ts';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { isBrowserProof, isProvedHere, proofFailures } from './ledger.ts';
import { SAFETY_LEDGER, type TestReference } from './specData.ts';

/**
 * Spec section 5: the ledger's shape, the files it names, and the proof rule
 * (tests/unit/spec/ledger.ts) with planted errors. Whether each named file holds a
 * PASSING test titled with its rule id is judged at the end of the same run by
 * tests/unit/spec/ledgerReporter.ts, from the run's own results (F1-01: no title
 * grep, no second run of the files). Since F2 the feature references are
 * proved here too; the e2e references are proved by Playwright, and this file
 * checks that each named e2e spec holds a test titled with its rule id.
 */

const R6_LAYERS = new Set(['core', 'contract']);
const R6_DIRS = ['tests/unit/core/', 'tests/unit/contract/'];
const R8_DIRS = ['tests/unit/api/', 'tests/unit/store/'];

type Reference = TestReference;

function isR6(reference: Reference): boolean {
  return R6_DIRS.some((dir) => reference.file.startsWith(dir));
}

/** True when the e2e spec at file declares a test whose title starts with id (string or template title). */
function browserTitled(file: string, id: string): boolean {
  const text = readFileSync(path.join(APP, file), 'utf8');
  const pattern = new RegExp('\\btest\\(\\s*[\'"`]' + id + ' ', 'u');
  return pattern.test(text);
}

const r6Rules = SAFETY_LEDGER.filter((row) => R6_LAYERS.has(row.layer));
const r8Rules = SAFETY_LEDGER.filter((row) => row.layer === 'api' || row.layer === 'store');
describe('the safety ledger of spec section 5', () => {
  it('lists 75 rules, 47 of them core and none contract (spec self-check [2])', () => {
    expect(SAFETY_LEDGER).toHaveLength(75);
    expect(r6Rules).toHaveLength(47);
    expect(SAFETY_LEDGER.filter((row) => row.layer === 'contract')).toHaveLength(0);
  });

  it('names a tests/core or tests/contract proof for every core and contract rule', () => {
    const missing = r6Rules.filter((row) => !row.tests.some(isR6)).map((row) => row.id);
    expect(missing).toEqual([]);
  });

  it('names a tests/api or tests/store proof for every api and store rule (R8)', () => {
    expect(r8Rules).toHaveLength(19);
    const missing = r8Rules
      .filter((row) => !row.tests.some((ref) => R8_DIRS.some((dir) => ref.file.startsWith(dir))))
      .map((row) => row.id);
    expect(missing).toEqual([]);
  });

  it('proves 26 references in tests/api and tests/store and 14 in tests/features; Playwright proves the 6 e2e ones; none is pending', () => {
    const references = SAFETY_LEDGER.flatMap((row) =>
      row.tests.map((ref) => ({ id: row.id, ref })),
    );
    expect(
      references.filter(({ ref }) => R8_DIRS.some((dir) => ref.file.startsWith(dir))),
    ).toHaveLength(26);
    expect(
      references.filter(({ ref }) => ref.file.startsWith('tests/unit/features/')),
    ).toHaveLength(14);
    const browser = references.filter(({ ref }) => isBrowserProof(ref));
    expect(browser).toHaveLength(6);
    expect(references.filter(({ ref }) => !isProvedHere(ref) && !isBrowserProof(ref))).toEqual([]);
    // Each e2e reference names a spec that exists and declares a test titled with its rule id.
    const missing = browser
      .filter(
        ({ id, ref }) => !existsSync(path.join(APP, ref.file)) || !browserTitled(ref.file, id),
      )
      .map(({ id, ref }) => `${id}: ${ref.file}`);
    expect(missing).toEqual([]);
  });
  it('names type-level proofs that exist (run by the Vitest typecheck)', () => {
    const typeFiles = r6Rules.flatMap((row) =>
      row.tests.filter((ref) => isR6(ref) && ref.file.endsWith('.test-d.ts')),
    );
    expect(typeFiles.map((ref) => ref.file).sort()).toEqual([
      'tests/unit/core/guards.test-d.ts',
      'tests/unit/core/readable.test-d.ts',
    ]);
    for (const ref of typeFiles) {
      expect(existsSync(path.join(APP, ref.file)), ref.file).toBe(true);
    }
  });
});

describe('the proof rule itself (planted errors)', () => {
  const rule = { id: 'S-999', tests: [{ file: 'tests/unit/core/x.test.ts', ruleId: 'S-999' }] };

  it('reports a rule whose file passed no test titled with its id', () => {
    expect(proofFailures([rule], () => ['S-9990 other', 'a test about S-999'])).toEqual([
      'S-999: no passing test titled "S-999 ..." in tests/unit/core/x.test.ts',
    ]);
  });

  it('reports a file whose titled tests were all skipped, todo or commented out (none passed)', () => {
    expect(proofFailures([rule], () => [])).toHaveLength(1);
  });

  it('accepts a passing test titled with the id, and leaves files outside the run alone', () => {
    expect(proofFailures([rule], () => ['S-999 proves it'])).toEqual([]);
    expect(proofFailures([rule], () => ['S-999'])).toEqual([]);
    expect(proofFailures([rule], () => undefined)).toEqual([]);
  });
});

it('every file the ledger names under tests/ and e2e/ exists', () => {
  const missing = SAFETY_LEDGER.flatMap((row) =>
    row.tests
      .filter(
        (ref) =>
          (isProvedHere(ref) || isBrowserProof(ref)) && !existsSync(path.join(APP, ref.file)),
      )
      .map((ref) => `${row.id}: ${ref.file}`),
  );
  expect(missing).toEqual([]);
});
