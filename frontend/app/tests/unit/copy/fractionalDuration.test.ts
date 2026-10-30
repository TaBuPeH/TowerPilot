import { describe, expect, it } from 'vitest';
import { durationText, fractionalDurationText } from '../../../src/copy/format.ts';
import { copy } from '../../../src/copy/catalogue.ts';

/**
 * AC2 (hard invariant 11): the Runs appearance gap ("N s between looks") reads through
 * copy/format.ts. The rule moved unchanged from copy/runs.ts gapText: whole seconds read as
 * durationText, a fraction as seconds to one decimal.
 */
const BEFORE = (seconds: number): string =>
  Number.isInteger(seconds) ? durationText(seconds) : `${seconds.toFixed(1)} s`;

describe('AC2 fractionalDurationText', () => {
  it.each([0, 2, 5, 59, 60, 65, 3600, 0.5, 2.5, 2.25, 59.94, 75.5, 1e-7])(
    'AC2 reads %s exactly as the Runs gap rule did before the move',
    (seconds) => {
      expect(fractionalDurationText(seconds)).toBe(BEFORE(seconds));
    },
  );

  it('AC2 keeps the recorded gaps of the Runs battle details (5 s and 2.5 s)', () => {
    const line = (gap: number) => copy.runs.live.details.appearedBetween(1, null, 2, null, gap);
    expect(line(5)).toMatch(/; 5 s between looks$/u);
    expect(line(2.5)).toMatch(/; 2\.5 s between looks$/u);
  });

  it('AC2 leaves durationText whole seconds (History reads it unchanged)', () => {
    expect(durationText(2.5)).toBe('2 s');
  });
});
