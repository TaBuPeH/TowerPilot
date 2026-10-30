import { describe, expect, it } from 'vitest';
import {
  checkField,
  settingsModule,
  settingsView,
  stringListRows,
  nextStringListKey,
} from '../../../src/core/settingsFields.ts';
import { own, ownPath } from '../../../src/core/read.ts';
import { initialDashboardState, reduceDashboard } from '../../../src/store/dashboardStore.ts';
import {
  DASHBOARD_PAGES_FIXTURE_PATH,
  loadJsonWithIdentity,
} from '../../../scripts/contract-fixture.mjs';

const cases = own(loadJsonWithIdentity(DASHBOARD_PAGES_FIXTURE_PATH).fixture, 'cases');
function field(path: string, family = 'settings', key = 'populated_account_every_module') {
  const view = settingsView(ownPath(cases, family, key, 'body'));
  const found = settingsModule(view, 'tournament-cards')?.fields.find(
    (entry) => entry.path === path,
  );
  if (found === undefined) throw new Error(`Missing recorded tournament field ${path}`);
  return found;
}
describe('shared string lists', () => {
  it('C15-6 C15-7 r12f adding after a later empty server read cannot reuse a retained original row key', () => {
    // Controlled ordering of two complete recordings: a retained malformed source
    // remains authoritative when a later recorded empty-list read arrives.
    const f = field(
      'drop',
      'settings_modules_more',
      'read_after_saved_tournament_cards_empty_lists',
    );
    const draft = {
      drop: true,
      'drop#list-source': JSON.stringify(
        field('drop', 'settings_modules_more_2', 'read_with_malformed_tournament_cards').value,
      ),
      'drop#list:0': 'fixed',
    };
    const key = nextStringListKey(f, draft);
    expect(key).toBe('drop#list:3');
    const added = { ...draft, [key]: 'added' };
    expect(stringListRows(f, added).map((row) => row.raw)).toEqual(['fixed', 7, 'keep', 'added']);
    expect(checkField(f, added)).toMatchObject({ kind: 'invalid' });
    expect(checkField(f, { ...added, 'drop#list:1': false })).toEqual({
      kind: 'valid',
      value: ['fixed', 'keep', 'added'],
    });
  });
  it('C15-6 C15-7 r12f malformed saved false/numeric members remain raw until explicitly changed or removed', () => {
    const f = field('drop', 'settings_modules_more_2', 'read_with_malformed_tournament_cards');
    expect(stringListRows(f, {}).map((row) => row.raw)).toEqual([false, 7, 'keep']);
    const draft = {
      drop: true,
      'drop#list-source': JSON.stringify(f.value),
      'drop#list:2': 'edited',
    };
    expect(stringListRows(f, draft).map((row) => row.raw)).toEqual([false, 7, 'edited']);
    expect(checkField(f, draft)).toMatchObject({ kind: 'invalid' });
    expect(checkField(f, { ...draft, 'drop#list:0': false, 'drop#list:1': 'fixed' })).toEqual({
      kind: 'valid',
      value: ['fixed', 'edited'],
    });
  });
  it('C15-6 C15-7 r12f an older save clears a list atomically and retains every row when newer typing exists', () => {
    const sent = {
      drop: true,
      'drop#list:0': false,
      'drop#list:1': 'cash',
      'drop#list:2': 'extra_orb',
    };
    const scope = 'settings:tournament-cards:test';
    const state = {
      ...initialDashboardState(),
      runDrafts: { [scope]: { ...sent, 'drop#list:2': 'newer raw name' } },
    };
    const next = reduceDashboard(state, { type: 'draft_discard', scope, only: sent });
    expect(next.runDrafts[scope]).toEqual(state.runDrafts[scope]);
    const saved = field('drop', 'settings_modules', 'read_after_saved_tournament_cards');
    expect(checkField(saved, next.runDrafts[scope] ?? {})).toEqual({
      kind: 'valid',
      value: ['cash', 'newer raw name'],
    });
    const settled = reduceDashboard(
      { ...state, runDrafts: { [scope]: sent } },
      { type: 'draft_discard', scope, only: sent },
    );
    expect(settled.runDrafts[scope]).toBeUndefined();
  });
  it('C15-6 preserves raw names, unsupported names and order without coercion', () => {
    const f = field('drop');
    const draft = { drop: true, 'drop#list:0': ' unknown ', 'drop#list:1': 'second' };
    expect(checkField(f, draft)).toEqual({ kind: 'valid', value: [' unknown ', 'second'] });
    expect(checkField(f, { drop: true, 'drop#list:0': '  ' })).toMatchObject({ kind: 'invalid' });
    expect(checkField(f, { drop: 'not a list' })).toMatchObject({ kind: 'invalid' });
  });
  it('C15-7 r12f empty lists, explicit removal and the server maxItems are authoritative', () => {
    const f = field('add');
    expect(checkField(f, { add: true, 'add#list:0': false })).toEqual({ kind: 'unchanged' });
    expect(
      stringListRows(f, {
        add: true,
        'add#list:0': 'first',
        'add#list:1': 'second',
        'add#list:2': false,
      }).map((row) => row.raw),
    ).toEqual(['first', 'second']);
    expect(f.maxItems).not.toBeNull();
    const rows = Object.fromEntries(
      Array.from({ length: (f.maxItems ?? 0) + 1 }, (_, i) => [`add#list:${String(i)}`, 'raw']),
    );
    expect(checkField(f, { add: true, ...rows })).toMatchObject({
      kind: 'invalid',
      reason: 'above_max',
    });
    expect(
      checkField(field('add', 'settings_modules', 'read_after_saved_tournament_cards'), {
        add: true,
        'add#list:0': false,
      }),
    ).toEqual({ kind: 'valid', value: [] });
  });
});
