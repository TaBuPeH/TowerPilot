import { describe, expect, it } from 'vitest';
import {
  collectionGuard,
  collectionPermit,
  setupGuard,
  setupPermit,
} from '../../../src/core/guards.ts';
import { sampleRecord } from '../../support/fixture.ts';
import { rec } from '../../support/core.ts';

const blocked = rec(sampleRecord('status.setup_guard_block')['collection_guard']);
const clearSetup = { blocks_launch: false, scope: 'run', code: null, message: null };
const clearCollection = { blocks_launch: false, scope: 'collection', code: null, message: null };

describe('S-100 Start uses setup_battle_guard, Collect and Turn on use collection_guard', () => {
  it('S-100 setup_guard_block: Collect and Turn on are judged by collection_guard only, which blocks', () => {
    expect(blocked['scope']).toBe('collection');
    expect(collectionGuard(blocked, 1)).toMatchObject({
      kind: 'refused',
      refusal: { kind: 'blocks', code: 'setup_battle_unresolved' },
    });
    // Start follows only its own guard: a clear setup guard permits Start whatever collection says.
    expect(setupGuard(clearSetup, 1).kind).toBe('permit');
  });

  it('S-100 a blocking setup guard blocks Start and leaves Turn on to the collection guard', () => {
    const setupBlock = { ...blocked, scope: 'run' };
    expect(setupGuard(setupBlock, 1).kind).toBe('refused');
    expect(collectionGuard(clearCollection, 1).kind).toBe('permit');
  });

  it('S-100 only an explicit blocks_launch === false permits; a missing field or guard is unconfirmed', () => {
    for (const guard of [
      undefined,
      null,
      {},
      { scope: 'collection' },
      { scope: 'collection', blocks_launch: 'false' },
      { scope: 'collection', blocks_launch: 0 },
    ]) {
      expect(collectionGuard(guard, 1).kind).toBe('refused');
      expect(collectionPermit(guard, 1).kind).toBe('unknown');
    }
    expect(collectionPermit(undefined, 1)).toEqual({ kind: 'unknown', reason: 'guard_missing' });
  });

  it('S-100 the guards are never combined: one scope cannot stand in for the other', () => {
    expect(setupGuard(clearCollection, 1)).toMatchObject({
      kind: 'refused',
      refusal: { kind: 'unconfirmed', reason: 'guard_scope_mismatch' },
    });
    expect(collectionGuard(clearSetup, 1)).toMatchObject({
      kind: 'refused',
      refusal: { kind: 'unconfirmed', reason: 'guard_scope_mismatch' },
    });
    expect(setupPermit(clearSetup, 1).kind).toBe('known');
    expect(collectionPermit(clearCollection, 1).kind).toBe('known');
  });
});
