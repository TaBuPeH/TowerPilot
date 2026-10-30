import { describe, expectTypeOf, it } from 'vitest';
import { collectionEvidence } from '../../../src/core/collection.ts';
import { readOwnership } from '../../../src/core/ownership.ts';
import { readHarvest, type ReadableHarvest } from '../../../src/core/readability.ts';

/** S-031: collection and ownership helpers accept only a ReadableHarvest, which only D-1 issues. */
describe('S-031 ReadableHarvest', () => {
  it('S-031 raw data cannot reach the collection and ownership helpers', () => {
    const raw: unknown = { version: 1 };
    // @ts-expect-error a raw record is not a ReadableHarvest
    collectionEvidence(raw);
    // @ts-expect-error a plain object of the same shape is not one either
    readOwnership([], { version: 1, server_time: 1 });
    const fact = readHarvest(raw);
    if (fact.kind === 'known') {
      expectTypeOf(collectionEvidence(fact.value)).not.toBeNever();
    }
  });

  it('S-031 a ReadableHarvest cannot be written as an object literal', () => {
    // @ts-expect-error a ReadableHarvest carries a brand an object literal cannot have
    const forged: ReadableHarvest = {};
    expectTypeOf(forged).toEqualTypeOf<ReadableHarvest>();
  });
});
