import { describe, expectTypeOf, it } from 'vitest';
import {
  collectionPermit,
  setupPermit,
  type CollectionGuardPermit,
  type SetupBattleGuardPermit,
} from '../../../src/core/guards.ts';

/** S-100: the two launch permits are distinct brands; one cannot be passed as the other. */
function requiresCollection(permit: CollectionGuardPermit): CollectionGuardPermit {
  return permit;
}
function requiresSetup(permit: SetupBattleGuardPermit): SetupBattleGuardPermit {
  return permit;
}

describe('S-100 launch permits', () => {
  it('S-100 a setup permit is not a collection permit, and the reverse', () => {
    const setup = setupPermit({ blocks_launch: false, scope: 'run' }, 1);
    const collection = collectionPermit({ blocks_launch: false, scope: 'collection' }, 1);
    if (setup.kind === 'known' && collection.kind === 'known') {
      // @ts-expect-error a SetupBattleGuardPermit cannot stand for a CollectionGuardPermit
      requiresCollection(setup.value);
      // @ts-expect-error a CollectionGuardPermit cannot stand for a SetupBattleGuardPermit
      requiresSetup(collection.value);
      expectTypeOf(requiresSetup(setup.value)).toEqualTypeOf<SetupBattleGuardPermit>();
      expectTypeOf(requiresCollection(collection.value)).toEqualTypeOf<CollectionGuardPermit>();
    }
  });

  it('S-100 a permit cannot be written as an object literal', () => {
    // @ts-expect-error a permit carries a brand an object literal cannot have
    const forged: CollectionGuardPermit = {};
    expectTypeOf(forged).toEqualTypeOf<CollectionGuardPermit>();
  });
});
