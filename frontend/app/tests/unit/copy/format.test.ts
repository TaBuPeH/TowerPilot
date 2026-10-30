import { describe, expect, it } from 'vitest';
import { durationText, isoText } from '../../../src/copy/format.ts';

/** The one rules for an epoch time as an ISO instant (A2-7 AC4: History wrote it three times). */
describe('isoText', () => {
  it('names the UTC instant of a millisecond epoch', () => {
    expect(isoText(0)).toBe('1970-01-01T00:00:00.000Z');
    expect(isoText(1800000520 * 1000)).toBe(new Date(1800000520 * 1000).toISOString());
  });

  it.each([Number.NaN, Number.POSITIVE_INFINITY, 8.64e15 + 1, -8.64e15 - 1])(
    'names no instant for %s, which a date cannot hold',
    (ms) => {
      expect(isoText(ms)).toBeNull();
    },
  );
});

describe('durationText', () => {
  it('reads a minute or more as minutes and seconds', () => {
    expect(durationText(4)).toBe('4 s');
    expect(durationText(65)).toBe('1 m 05 s');
  });
});
