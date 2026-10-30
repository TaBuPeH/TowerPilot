import { existsSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
  DATA_PATH,
  extractSpecData,
  generateSpecData,
  SPEC_PATH,
} from '../../../scripts/spec-data.mjs';
import { MATRIX, SAFETY_LEDGER, W_ROWS, X_ROWS } from './specData.ts';

/**
 * tests/unit/spec/specData.ts is generated from the approved spec (scripts/spec-data.mjs).
 * The spec lives under the git-ignored backend/logs/, so this drift check runs
 * only where the spec is in the checkout; elsewhere it says so on the console,
 * and the committed data is used. The spec's hash is not pinned (F1 trim): the
 * data is compared with a fresh extraction, so only ledger or matrix changes matter.
 */
const HAVE_SPEC = existsSync(SPEC_PATH);

describe('the spec data the R6 tests run on', () => {
  it('holds the 75 safety rules, 40 matrix rows, 12 scenario rows and 13 warm rows', () => {
    expect([SAFETY_LEDGER.length, MATRIX.length, X_ROWS.length, W_ROWS.length]).toEqual([
      75, 40, 12, 13,
    ]);
  });

  it('equals a fresh extraction of the spec whenever the spec is in the checkout (drift fails here)', async () => {
    if (!HAVE_SPEC) {
      console.warn(
        `spec-data drift check not run: ${SPEC_PATH} is not in this checkout (backend/logs is git-ignored); the committed data is used`,
      );
      return;
    }
    const text = readFileSync(SPEC_PATH, 'utf8');
    const generated = await generateSpecData(text);
    expect(readFileSync(DATA_PATH, 'utf8')).toBe(generated);
  });

  it('fails loudly on a spec whose ledger is malformed (planted error)', () => {
    expect(() => extractSpecData('no ledger here')).toThrow(/lacks the section/);
    const broken = [
      '<!-- ledger:safety:start -->',
      '| S-001 | too few cells |',
      '<!-- ledger:safety:end -->',
      '### 9.1 Revocation scenario matrix',
      '',
      '## 10. Decisions',
    ].join('\n');
    expect(() => extractSpecData(broken)).toThrow(/S-001 has 2 cells, expected 7/);
  });
});
