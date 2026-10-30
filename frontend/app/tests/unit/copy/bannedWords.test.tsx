import { afterEach, describe, expect, it, vi } from 'vitest';
import { copy, observeCopy } from '../../../src/copy/catalogue.ts';
import { renderPage } from '../features/dashboard/harness.tsx';
import { sample, statusBody } from '../../support/fixture.ts';

/**
 * Spec 6.4: words banned from shipped catalogue strings, checked over the
 * WHOLE catalogue (every entry) and over every formatter output a full page
 * makes in the main scenarios. Two phrases the spec itself quotes are
 * exempt, each where the spec quotes it: "Stop it there" (section 7.5 and
 * F-086, an owner of another install) and "couldn't check the device lease"
 * (3.6.3 row I3, the failed `lease_state` step).
 */
const BANNED =
  /\b(driver|runner|lease|overlay|handoff|diamond)\b|No battle running|\bInterrupted\b|Stop it(?! there)|Delivery unconfirmed|Live control|Failed to fetch|Open Runs/iu;
const SPEC_QUOTED = ["turn-off not confirmed: couldn't check the device lease"];

function strings(value: unknown): string[] {
  if (typeof value === 'string') return [value];
  if (typeof value === 'object' && value !== null) return Object.values(value).flatMap(strings);
  return [];
}

function banned(text: string): boolean {
  return !SPEC_QUOTED.includes(text) && BANNED.test(text);
}

afterEach(() => {
  vi.useRealTimers();
});

describe('spec 6.4 banned words', () => {
  it('appear in no catalogue entry', () => {
    const found = strings(copy).filter(banned);
    expect(found).toEqual([]);
  });

  it('the check is discriminating (planted words are found; the quoted exceptions are not)', () => {
    expect(banned('the lease holder')).toBe(true);
    expect(banned('Interrupted')).toBe(true);
    expect(banned('Stop it now')).toBe(true);
    expect(banned('Another install owns it - stop it there')).toBe(false);
    expect(banned("turn-off not confirmed: couldn't check the device lease")).toBe(false);
  });

  it('appear in no formatter output of the page in its main scenarios', async () => {
    const made: string[] = [];
    const stop = observeCopy((text) => {
      made.push(text);
    });
    try {
      for (const name of [
        'collecting',
        'off',
        'status_failed',
        'foreign_process',
        'owned_by_run_gathers',
      ]) {
        vi.useFakeTimers();
        const page = await renderPage({ status: () => statusBody(sample(`status.${name}`)) });
        await page.advance(3_000);
        page.view.unmount();
        vi.useRealTimers();
      }
    } finally {
      stop();
    }
    expect(made.length).toBeGreaterThan(0);
    expect(made.filter(banned)).toEqual([]);
  });
});
