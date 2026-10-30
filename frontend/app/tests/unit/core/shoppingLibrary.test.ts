import { describe, expect, it } from 'vitest';
import { own, ownPath } from '../../../src/core/read.ts';
import {
  addDirective,
  librarySource,
  legacyPolicy,
  moveDirective,
  removeDirective,
  reviewLegacy,
  shoppingVocabulary,
} from '../../../src/core/shoppingLibrary.ts';
import { dashboardPagesCase } from '../../support/dashboardFixture.ts';

const sample = (name: string) => dashboardPagesCase(`shopping_queue.${name}`);
const before = sample('shopping_library_read_before_any_edit').body;
const policy = (name: string) => ownPath(before, 'profile', 'policies', 'shopping_lists', name);

describe('SQ9 shared shopping source', () => {
  it('SQ9 lists every policy and every referencing run from the recorded source', () => {
    const source = librarySource(before);
    expect(source?.policies.map((item) => [item.id, item.usedBy])).toEqual([
      ['legacy', ['farm_deep']],
      ['main_list', ['farm', 'other']],
      ['spare', []],
      ['unused', []],
    ]);
    expect(librarySource({ ok: false })).toBeNull();
    expect(
      librarySource(sample('shopping_library_read_after_the_edits').body)?.policies,
    ).toHaveLength(6);
  });
});

describe('SQ10 legacy policy rules', () => {
  it('SQ10 round-trips the recorded bare list and mapping without converting either', () => {
    const bare = policy('legacy');
    const mapping = own(sample('shopping_legacy_mapping_updated').request.body, 'value');
    expect(legacyPolicy(bare)?.kind).toBe('list');
    expect(legacyPolicy(mapping)?.kind).toBe('mapping');
    expect(reviewLegacy(bare, {}).value).toEqual(bare);
    expect(reviewLegacy(mapping, {}).value).toEqual(mapping);
    expect(legacyPolicy(policy('main_list'))).toBeNull();
  });

  it('SQ10 keeps hidden members and optional enabled flags while editing, adding, removing and moving', () => {
    const mapping = own(sample('shopping_legacy_mapping_updated').request.body, 'value');
    // Deliberately add future members: the editor must preserve fields it does not own.
    const extended = {
      ...Object.fromEntries(Object.entries(mapping as object)),
      future: { keep: true },
    };
    const added = addDirective(mapping, {});
    const moved = moveDirective(mapping, added, 'new:0', -1);
    expect(moved).not.toBeNull();
    const removed = removeDirective(mapping, moved ?? {}, 'new:0');
    expect(reviewLegacy(extended, removed).value).toEqual(extended);
    const changed = reviewLegacy(extended, { 'line:0:enabled': true, label: 'Edited' });
    expect(own(changed.value, 'future')).toEqual({ keep: true });
    expect(own(changed.value, 'label')).toBe('Edited');
    expect(ownPath(changed.value, 'directives')).toEqual([
      { enabled: true, tab: 'attack', stats: ['damage'], mode: 'repeat' },
      { tab: 'defense', stats: ['health'], mode: 'best_cost' },
      ...(policy('legacy') as unknown[]),
    ]);
  });

  it('SQ10 requires numeric clicks only in clicks mode and leaves range validation to the recorded server', () => {
    const invalid = own(sample('shopping_legacy_clicks_invalid').request.body, 'value');
    expect(reviewLegacy(invalid, {}).problems).toBe(0);
    expect(reviewLegacy(invalid, { 'line:0:clicks': '' }).problems).toBe(1);
    expect(reviewLegacy(invalid, { 'line:0:clicks': 'wrong' }).problems).toBe(1);
    const otherMode = reviewLegacy(invalid, { 'line:0:mode': 'once' });
    expect(ownPath(otherMode.value, 'directives')).toEqual([
      { tab: 'attack', stats: ['damage'], mode: 'once' },
    ]);
    expect(shoppingVocabulary(dashboardPagesCase('profile_reads.vocab_ok').body)?.modes).toEqual([
      'repeat',
      'once',
      'best_cost',
      'clicks',
    ]);
  });
});
