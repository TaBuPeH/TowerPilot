import { describe, expect, it } from 'vitest';
import { factValueOr, knownFact, unknownFact } from '../../../src/core/facts.ts';

describe('Fact', () => {
  it('is known with its observation time', () => {
    expect(knownFact('idle', 1_800_000_000)).toEqual({
      kind: 'known',
      value: 'idle',
      observedAt: 1_800_000_000,
    });
  });

  it('is unknown with a reason', () => {
    expect(unknownFact('emulator_offline')).toEqual({
      kind: 'unknown',
      reason: 'emulator_offline',
    });
    expect(unknownFact('  ')).toEqual({ kind: 'unknown', reason: 'unspecified' });
  });

  it('turns a non-finite observation time into an unknown fact', () => {
    for (const time of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY]) {
      expect(knownFact('collecting', time)).toEqual({
        kind: 'unknown',
        reason: 'observed_at_invalid',
      });
    }
  });

  it('is immutable', () => {
    expect(Object.isFrozen(knownFact(1, 1))).toBe(true);
    expect(Object.isFrozen(unknownFact('x'))).toBe(true);
  });

  it('falls back only for an unknown fact', () => {
    expect(factValueOr(knownFact(3, 1), 'none')).toBe(3);
    expect(factValueOr(unknownFact('x'), 'none')).toBe('none');
  });
});
