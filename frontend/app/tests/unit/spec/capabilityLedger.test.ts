// @vitest-environment node
import { REPO } from '../../support/paths.ts';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { capabilityFailures, WANTED_BASELINE } from './ledger.ts';
import { SPEC } from './specData.ts';

/**
 * Spec 9 item 1: every Wanted-baseline row of section 4 (74, section 4.1) has
 * a passing test whose title starts with its F-id. This file holds the list
 * and checks it against the spec's own table; whether each id has a PASSING
 * test is judged at the end of the same run by tests/unit/spec/ledgerReporter.ts
 * (it judges only runs that include this file).
 *
 * The committed list is WANTED_BASELINE (ledger.ts). The spec lives under the git-ignored
 * backend/logs/, so a clean checkout and release CI do not have it: there the list-versus-spec
 * comparison is skipped (it says so below), and wherever the spec is present the comparison runs
 * in full, so the committed list cannot drift from the spec.
 */

const SPEC_FILE = path.join(REPO, SPEC.name);
const HAVE_SPEC = existsSync(SPEC_FILE);
if (!HAVE_SPEC) {
  console.warn(
    `capability-ledger spec comparison skipped: ${SPEC_FILE} is not in this checkout (backend/logs is git-ignored); the committed WANTED_BASELINE is used`,
  );
}

function specBaselineIds(): string[] {
  const text = readFileSync(SPEC_FILE, 'utf8');
  return text
    .split('\n')
    .filter((line) => /^\| F-\d{3} \|/u.test(line))
    .filter((line) => line.split('|')[4]?.trim() === 'Wanted-baseline')
    .map((line) => line.split('|')[1]?.trim() ?? '');
}

describe('the capability ledger of spec section 4', () => {
  it('the committed list holds 74 distinct F-ids', () => {
    expect(WANTED_BASELINE).toHaveLength(74);
    expect(new Set(WANTED_BASELINE).size).toBe(74);
    expect(WANTED_BASELINE.every((id) => /^F-\d{3}$/u.test(id))).toBe(true);
  });

  it.skipIf(!HAVE_SPEC)(
    'lists exactly the 74 Wanted-baseline rows of the spec, in order (runs only where the git-ignored spec is in the checkout)',
    () => {
      expect(WANTED_BASELINE).toHaveLength(74);
      expect(WANTED_BASELINE).toEqual(specBaselineIds());
    },
  );

  it('the proof rule: an id is proved only by a title that starts with it (planted errors)', () => {
    expect(capabilityFailures(['F-999'], ['a test about F-999', 'F-9990: other'])).toEqual([
      'F-999: no passing test titled "F-999: ..."',
    ]);
    expect(capabilityFailures(['F-999'], ['F-999: proves it'])).toEqual([]);
    expect(capabilityFailures(['F-999'], ['F-999 proves it'])).toEqual([]);
    expect(capabilityFailures(['F-999'], [])).toHaveLength(1);
  });
});
