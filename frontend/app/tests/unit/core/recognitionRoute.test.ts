import { describe, expect, it } from 'vitest';
import { recognitionHref, recognitionRunFromHash } from '../../../src/core/recognition.ts';

describe('R17 Prepare Recognition run query', () => {
  it.each(['bp_farm', 'a/b ?&=+% 雪'])('round trips the exact run id %s', (run) => {
    expect(recognitionRunFromHash(recognitionHref(run))).toBe(run);
  });

  it.each([
    '#recognition',
    '#recognition?run=',
    '#recognition?run=%E0%A4%A',
    '#recognition?other=bp_farm',
    '#recognition?run=bp_farm&other=1',
    '#runtime?run=bp_farm',
  ])('leaves the picker for %s', (hash) => {
    expect(recognitionRunFromHash(hash)).toBeNull();
  });

  it('uses the normal Recognition page when no run is selected', () => {
    expect(recognitionHref(null)).toBe('#recognition');
    expect(recognitionRunFromHash('#/recognition?run=bp_farm')).toBe('bp_farm');
  });
});
