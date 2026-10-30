import { describe, expect, it } from 'vitest';
import { DRAFTS_KEY, restoreDrafts, saveDrafts } from '../../../src/api/drafts.ts';
import { createMemoryStorage } from '../../support/fakeTransport.ts';

/**
 * The raw-draft storage boundary (api/drafts.ts; Runs F6R-02, Collection review F6C-R1-01):
 * every page draft scope comes back raw under its own scope; malformed entries are skipped.
 */
const ALPHA = JSON.stringify(['main', 'fake:1', null, 'mine']);
const BETA = JSON.stringify(['main', 'fake:1', 'beta', 'theirs']);

describe('F6C-R1-01 page drafts survive a reload under their own scope', () => {
  it('restores settings, Collection, Abilities, Profiles and Runs scopes raw, each under its own key', () => {
    const storage = createMemoryStorage();
    const drafts = {
      [`settings:ad:all_on:${ALPHA}`]: { 'gem_delay_sec#0': '007', 'gem_delay_sec#1': '30.5' },
      [`settings:rewards:${BETA}`]: { event_missions: true },
      [`collection:copy:${ALPHA}`]: { from: 'all_on', id: 'kept_copy', label: 'Kept label' },
      [`abilities:new_uw_policies:${ALPHA}`]: { id: 'unfinished_copy' },
      [`abilities:new_rescue_policies:${BETA}`]: { id: 'beta_only' },
      [`profiles:account:${JSON.stringify(['main', 'fake:1'])}`]: { id: 'alpha' },
      'saved:ctx:farm': { 'routines.ad_gems.interval_sec': '030' },
    };
    saveDrafts(storage, drafts);
    expect(restoreDrafts(storage)).toEqual(drafts);
  });

  it('skips unknown, context-less and malformed scopes and non-raw values, never repairing them', () => {
    const storage = createMemoryStorage();
    storage.setItem(
      DRAFTS_KEY,
      JSON.stringify({
        [`settings:rewards:${ALPHA}`]: { event_missions: true, count: 3, nested: {} },
        'settings:rewards': { event_missions: true },
        'settings:rewards:["main"]': { event_missions: true },
        'settings:rewards:[1,2,3,4]': { event_missions: true },
        'collection:copy:not-json': { id: 'x' },
        [`unknown_page:x:${ALPHA}`]: { id: 'x' },
        'abilities:new_uw_policies:not-json': { id: 'x' },
        [`collection:copy:${ALPHA}`]: 'not a draft',
      }),
    );
    expect(restoreDrafts(storage)).toEqual({
      [`settings:rewards:${ALPHA}`]: { event_missions: true },
    });
  });
});
