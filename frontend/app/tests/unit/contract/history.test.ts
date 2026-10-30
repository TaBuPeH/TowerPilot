// @vitest-environment node
import { expect, it } from 'vitest';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import {
  historyDetailAnswerSchema,
  historyListAnswerSchema,
} from '../../../src/contract/history.ts';
import {
  generateHistory,
  HISTORY_DESCRIPTOR,
} from '../../../scripts/generate-history-contract.mjs';
import { familyIdentity, record } from '../../../scripts/generate-contract.mjs';
import { HISTORY_FIXTURE_PATH, loadHistoryFixture } from '../../../scripts/history-fixture.mjs';

it('H1–H4 generated history schemas parse every real-route family answer', () => {
  const cases = loadHistoryFixture();
  for (const [family, schema] of [
    ['history_list', historyListAnswerSchema],
    ['history_detail', historyDetailAnswerSchema],
  ] as const) {
    const rows = record(cases[family]);
    for (const [name, raw] of Object.entries(rows)) {
      const row = Object(raw) as { status: number; body: unknown };
      expect(
        schema.safeParse({ status: row.status, body: row.body }).success,
        `${family}.${name}`,
      ).toBe(true);
    }
  }
});
// Regenerating and formatting the contract can exceed five seconds under full-suite contention.
it('H4 generated files have no drift; unrelated appended page families do not change the hash', async () => {
  await generateHistory(true);
  const folder = mkdtempSync(path.resolve('node_modules/.cache/history-contract-'));
  try {
    const appended = path.join(folder, 'appended.json');
    writeFileSync(
      appended,
      readFileSync(HISTORY_FIXTURE_PATH, 'utf8').replace(
        '"cases": {',
        '"cases": {"unrelated_page": {},',
      ),
    );
    expect(familyIdentity(appended, HISTORY_DESCRIPTOR).sha256).toBe(
      familyIdentity(HISTORY_FIXTURE_PATH, HISTORY_DESCRIPTOR).sha256,
    );
  } finally {
    rmSync(folder, { recursive: true });
  }
}, 30_000);
