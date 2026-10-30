import { describe, expect, it } from 'vitest';
import {
  DASHBOARD_FIXTURE_PATH,
  DASHBOARD_PAGES_FIXTURE_PATH,
  loadJsonWithIdentity,
} from '../../../scripts/contract-fixture.mjs';
import {
  actionName,
  actionsView,
  compatClaimLine,
  COMPAT_FIELDS,
  COMPAT_ROUTINES,
  effectWord,
  fieldLabel,
  hasLiveBounds,
  isShoppingAction,
  isShoppingVariant,
  liveConstraints,
  liveFieldOrder,
  orderedFields,
} from '../../../src/core/actions.ts';
import { controlGroups } from '../../../src/core/runControls.ts';
import { entries, own, ownPath } from '../../../src/core/read.ts';
import { dashboardPagesCase } from '../../support/dashboardFixture.ts';

type Cases = Readonly<
  Record<
    string,
    Readonly<
      Record<string, { request?: { method: string; path: string }; status: number; body: unknown }>
    >
  >
>;

function recorded(file: string, tag: string) {
  const cases = Reflect.get(Object(loadJsonWithIdentity(file).fixture), 'cases') as Cases;
  return Object.entries(cases).flatMap(([family, rows]) =>
    Object.entries(rows).map(([name, found]) => ({ name: `${tag}:${family}.${name}`, found })),
  );
}

const ALL = [
  ...recorded(DASHBOARD_FIXTURE_PATH, 'D'),
  ...recorded(DASHBOARD_PAGES_FIXTURE_PATH, 'P'),
];
const battles = ALL.filter(
  ({ found }) =>
    found.request?.method === 'GET' && found.request.path.startsWith('/api/battles/active'),
);
const savedConstraints = ALL.flatMap(({ name, found }) => {
  const constraints = own(found.body, 'constraints');
  return found.request?.method === 'GET' &&
    found.request.path.startsWith('/api/run-controls') &&
    entries(constraints).length > 0
    ? [{ name, constraints }]
    : [];
});

describe('A2-7 core/actions projection views', () => {
  it('R1 reads every recorded projection into items with their keys and descriptor fields', () => {
    const projected = battles.filter(
      ({ found }) => actionsView(own(found.body, 'battle')) !== null,
    );
    // Landed pages: 13 projection cases; the absent-actions case exercises compatibility.
    expect(projected.length).toBe(13);
    for (const { found } of projected) {
      const view = actionsView(own(found.body, 'battle'));
      const items = view?.items ?? [];
      expect(items.map((item) => item.id)).toEqual(['ad_gems', 'floating_gems', 'shopping']);
      for (const item of items) {
        expect(item.key).toBe(item.id);
        expect(item.fields.map((field) => field.path)[0]).toBe('enabled');
      }
    }
  });

  it('R1 a battle without the actions key has no projection (the S5 compatibility rows apply)', () => {
    const found = dashboardPagesCase(
      'battle_actions_projection.projection_never_built_the_actions_key_is_absent',
    );
    expect(actionsView(own(found.body, 'battle'))).toBeNull();
    expect(COMPAT_ROUTINES).toEqual(['ad_gems', 'floating_gems', 'shopping']);
  });

  it('R1 the published key wins; without one only a single-instance item is addressable (G2)', () => {
    const view = actionsView({
      runtime: {
        actions: {
          items: [
            { id: 'probe', instance: 'probe_a', descriptor: { fields: [] } },
            { id: 'probe', instance: 'probe_b', key: 'probe@probe_b', descriptor: { fields: [] } },
            { id: 'solo', instance: 'solo', descriptor: { fields: [] } },
            { instance: 'unreadable' },
          ],
        },
      },
    });
    expect(view?.items.map((item) => item.key)).toEqual([null, 'probe@probe_b', 'solo']);
  });

  it('R1 field order: descriptor live order when the routine has an item, else the compatibility order', () => {
    const found = dashboardPagesCase(
      'battle_actions_projection.idle_ad_and_floating_gems_due_shopping_waiting',
    );
    const view = actionsView(own(found.body, 'battle'));
    expect(liveFieldOrder(view, 'ad_gems')).toEqual(['enabled', 'interval_sec', 'window_sec']);
    expect(liveFieldOrder(view, 'rewards')).toBe(COMPAT_FIELDS);
    expect(liveFieldOrder(null, 'ad_gems')).toBe(COMPAT_FIELDS);
    const fields = ['extra', 'window_sec', 'enabled'].map((field) => ({ field }));
    expect(orderedFields(fields, COMPAT_FIELDS).map((f) => f.field)).toEqual([
      'enabled',
      'window_sec',
      'extra',
    ]);
  });

  it('R1 shopping predicates: the action and its colon variants only', () => {
    expect(['shopping', 'shopping:attack', 'shopping:attack:damage'].every(isShoppingAction)).toBe(
      true,
    );
    expect(['ad_gems', 'shoppingx', 'shopping_plan'].some(isShoppingAction)).toBe(false);
    expect(isShoppingVariant('shopping')).toBe(false);
    expect(isShoppingVariant('shopping:utility')).toBe(true);
  });

  it('R1 name and label lookups read own keys only', () => {
    const names = { ad_gems: 'Ad gems' };
    expect(actionName(names, 'ad_gems', 'Other')).toBe('Ad gems');
    expect(actionName(names, 'constructor', 'Other')).toBe('Other');
    expect(fieldLabel({ interval_sec: 'Every' }, 'toString', 'Setting')).toBe('Setting');
  });
});

describe('A2-7 R2 live bounds source', () => {
  it('R2 descriptor bounds equal the saved constraints for every recorded pair (checkout and r17b)', () => {
    expect(savedConstraints.length).toBeGreaterThan(0);
    let pairs = 0;
    for (const battle of battles) {
      const view = actionsView(own(battle.found.body, 'battle'));
      if (view === null) continue;
      const fromDescriptor = entries(liveConstraints(view, undefined));
      expect(fromDescriptor.length).toBe(6);
      for (const saved of savedConstraints)
        for (const [path, bounds] of fromDescriptor) {
          const recordedBounds = own(saved.constraints, path);
          if (recordedBounds === undefined) continue;
          pairs += 1;
          expect(
            { min: own(recordedBounds, 'min'), max: own(recordedBounds, 'max') },
            `${battle.name} ${saved.name} ${path}`,
          ).toEqual(bounds);
        }
    }
    expect(pairs).toBeGreaterThan(0);
  });

  it('R2 descriptor bounds apply without a saved read; no projection keeps the saved constraints', () => {
    const found = dashboardPagesCase(
      'battle_actions_projection.idle_ad_and_floating_gems_due_shopping_waiting',
    );
    const battle = own(found.body, 'battle');
    const controls = ownPath(battle, 'capabilities', 'controls');
    const live = controlGroups(
      controls,
      own(battle, 'effective'),
      liveConstraints(actionsView(battle), undefined),
    );
    const tap = live
      .flatMap((group) => group.fields)
      .find((field) => field.path === 'routines.floating_gems.tap_interval_sec');
    expect(tap).toMatchObject({ kind: 'number', min: 0.5, max: 30 });
    const enabled = live
      .flatMap((group) => group.fields)
      .find((field) => field.path === 'routines.ad_gems.enabled');
    expect(enabled).toMatchObject({ kind: 'toggle', min: null, max: null });
    const saved = { 'routines.ad_gems.interval_sec': { min: 1, max: 3600 } };
    expect(hasLiveBounds(actionsView(battle))).toBe(true);
    expect(hasLiveBounds(null)).toBe(false);
    expect(liveConstraints(null, saved)).toBe(saved);
    expect(liveConstraints(null, undefined)).toBeUndefined();
  });
});

describe('the words of an ad claim whose only proof is that the offer was gone after the tap', () => {
  it('reads an unconfirmed effect with that reason as offer gone, and nothing else', () => {
    expect(effectWord('unconfirmed', 'offer_disappeared')).toBe('offer_gone');
    expect(effectWord('confirmed', 'offer_disappeared')).toBe('confirmed');
    expect(effectWord('confirmed', 'balance_increased')).toBe('confirmed');
    expect(effectWord('unconfirmed', 'offer_still_visible')).toBe('unconfirmed');
    expect(effectWord('unconfirmed', null)).toBe('unconfirmed');
  });

  it('words a compatibility ad claim by its reason; an orbit pickup is a balance rise', () => {
    expect(compatClaimLine('ad_gems', 'offer_disappeared')).toBe('claim_offer_gone');
    expect(compatClaimLine('ad_gems', 'balance_increased')).toBe('claim_confirmed');
    expect(compatClaimLine('floating_gems', 'balance_increased')).toBe('floating_claim_confirmed');
  });
});
