import { afterEach, describe, expect, it, vi } from 'vitest';
import { settlePause } from '../../../src/core/harvestChoice.ts';
import { gateOf } from '../../../src/core/gate.ts';
import { scanFresh } from '../../../src/core/scan.ts';
import { sampleRecord, serverTimeOf, statusBody } from '../../support/fixture.ts';
import { readOf, rec } from '../../support/core.ts';

const answer = sampleRecord('post_harvest.off_ok_stopping');
const stopping = rec(rec(answer['body'])['stop'])['stopping'] as string[];
const off = sampleRecord('status.off');
const H = serverTimeOf(off);
/** The later complete Off read at a higher revision of S-020, with the given process_refresh. */
function laterOff(scan: unknown) {
  const harvest = sampleRecord('status.off');
  rec(harvest['desired'])['revision'] = 99;
  return readOf(statusBody(harvest, { scan }), 5);
}
function verdict(scan: unknown): string {
  const gate = gateOf(laterOff(scan), 4);
  return gate.kind === 'known' ? settlePause(stopping, gate.value).kind : 'not_confirmed';
}

afterEach(() => {
  vi.useRealTimers();
});

describe('S-023 scan freshness comes from the numbers, never from the fresh label', () => {
  const rejected: [string, Record<string, unknown>][] = [
    [
      '(a) last_success_at H+3600 with age -3600',
      { state: 'fresh', error: null, age_sec: -3600, last_success_at: H + 3600 },
    ],
    ['(b) age 180', { state: 'fresh', error: null, age_sec: 180, last_success_at: H - 180 }],
    ['(c) last_success_at missing', { state: 'fresh', error: null, age_sec: 0 }],
    ['(c) last_success_at "x"', { state: 'fresh', error: null, age_sec: 0, last_success_at: 'x' }],
    [
      '(c) a non-finite age',
      { state: 'fresh', error: null, age_sec: Number.NaN, last_success_at: H },
    ],
    ['(c) an age as a string', { state: 'fresh', error: null, age_sec: '0', last_success_at: H }],
    ['(d) age 0 with H-7', { state: 'fresh', error: null, age_sec: 0, last_success_at: H - 7 }],
    ['(e) age 0 with H-9', { state: 'fresh', error: null, age_sec: 0, last_success_at: H - 9 }],
    ['a stale label', { state: 'stale', error: null, age_sec: 0, last_success_at: H }],
    ['an error', { state: 'fresh', error: 'boom', age_sec: 0, last_success_at: H }],
  ];
  const accepted: [string, Record<string, unknown>][] = [
    ['(f) age 0 with H', { state: 'fresh', error: null, age_sec: 0, last_success_at: H }],
    ['(g) age 6 with H', { state: 'fresh', error: null, age_sec: 6, last_success_at: H }],
    ['(h) age 6 with H+2', { state: 'fresh', error: null, age_sec: 6, last_success_at: H + 2 }],
    ['an absent error', { state: 'fresh', age_sec: 1, last_success_at: H - 1 }],
  ];

  it.each(rejected)('S-023 rejects %s: not confirmed stopped', (_label, scan) => {
    expect(scanFresh(scan, H).kind).toBe('unknown');
    expect(verdict(scan)).toBe('not_confirmed');
  });

  it.each(accepted)('S-023 accepts %s: stopped', (_label, scan) => {
    expect(scanFresh(scan, H).kind).toBe('known');
    expect(verdict(scan)).toBe('stopped');
  });

  it.each([-86_400_000, 86_400_000])(
    'S-023 gives the same results with the browser clock shifted by %i ms',
    (shift) => {
      vi.useFakeTimers();
      vi.setSystemTime(H * 1000 + shift);
      expect(rejected.map(([, scan]) => verdict(scan))).toEqual(
        rejected.map(() => 'not_confirmed'),
      );
      expect(accepted.map(([, scan]) => verdict(scan))).toEqual(accepted.map(() => 'stopped'));
    },
  );
});
