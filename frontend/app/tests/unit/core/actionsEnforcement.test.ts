// @vitest-environment node
import { SRC } from '../../support/paths.ts';
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, it } from 'vitest';

/**
 * A2-7 enforcement (brief backend/logs/reviews/ui-pages/a2-7-actions-brief.md section 7): a quoted
 * routine id ('ad_gems', 'floating_gems', 'shopping') may appear in src only in core/actions.ts
 * (the one owner, with the S5 compatibility list) and in the consumers that stay specific, each
 * with its exact count. A new hardcoded routine list, or a new quoted id in an allowed file,
 * fails here. This supports the behavioural proof (AC7); it does not replace it.
 */

const OWNER = 'core/actions.ts';
const QUOTED_ID = /['"`](?:ad_gems|floating_gems|shopping)['"`:]/g;

const ALLOWED: Readonly<Record<string, { readonly count: number; readonly why: string }>> = {
  // S1: gem-only collection authority and evidence (spec 2.2 item 8, risk row 353).
  'core/overlay.ts': { count: 4, why: 'S1 gem-only overlay' },
  'core/collection.ts': { count: 4, why: 'S1 gem-only collection authority' },
  'core/gemSource.ts': { count: 2, why: 'S1 gem-only gem source' },
  'core/readiness.ts': { count: 2, why: 'S1 gem-only readiness' },
  'core/history.ts': { count: 2, why: 'S1 gem confirmation evidence' },
  'features/dashboard/control/GemSlot.tsx': {
    count: 6,
    why: 'S1 consumes the gem overlay and compares its reported targets with timing labels',
  },
  // S2: the shopping queue, its reads and Try once tokens (F6-12 SQ8).
  'core/routines.ts': { count: 2, why: 'S2 shoppingView reads runtime.routines.shopping' },
  'store/selectors/thisBattle.ts': { count: 7, why: 'S2 Try once shopping tokens' },
  'store/selectors/shoppingLive.ts': { count: 4, why: 'S2 live shopping switch and interval' },
  'store/selectors/shoppingSaved.ts': { count: 2, why: 'S2 saved shopping interval' },
  'store/selectors/shoppingLibrary.ts': { count: 1, why: 'S2 shopping library interval' },
  // S3: the RL7 details of each collector's own bookkeeping.
  'store/selectors/runs.ts': { count: 2, why: 'S3 Ad gems and Floating gems details' },
  // Brief 2.3: the blueprint's `shopping` policy key, not a routine list.
  'core/runEntry.ts': { count: 3, why: 'blueprint shopping policy key' },
  'core/shoppingLibrary.ts': { count: 1, why: 'blueprint shopping policy key' },
  'features/runs/RunEntry.tsx': { count: 1, why: 'blueprint shopping policy field display group' },
};

function sourceFiles(folder: string): readonly string[] {
  return readdirSync(folder, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(folder, entry.name);
    if (entry.isDirectory())
      return path.relative(SRC, full).replaceAll('\\', '/') === 'contract/generated'
        ? []
        : sourceFiles(full);
    return /\.tsx?$/.test(entry.name) ? [full] : [];
  });
}

it('A2-7 enforcement: no quoted routine id in src outside core/actions.ts and the S1 to S4 allowlist', () => {
  const found: Record<string, number> = {};
  for (const file of sourceFiles(SRC)) {
    const relative = path.relative(SRC, file).replaceAll('\\', '/');
    if (relative === OWNER) continue;
    const count = readFileSync(file, 'utf8').match(QUOTED_ID)?.length ?? 0;
    if (count > 0) found[relative] = count;
  }
  expect(found).toEqual(
    Object.fromEntries(Object.entries(ALLOWED).map(([file, { count }]) => [file, count])),
  );
});

it('A2-7 enforcement: the owner holds the compatibility routines', () => {
  expect(readFileSync(path.join(SRC, OWNER), 'utf8').match(QUOTED_ID)?.length ?? 0).toBeGreaterThan(
    0,
  );
});
