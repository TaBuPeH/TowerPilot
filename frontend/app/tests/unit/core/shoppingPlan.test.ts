import { describe, expect, it } from 'vitest';
import { sameJson } from '../../../src/core/json.ts';
import { own, ownPath } from '../../../src/core/read.ts';
import {
  entryKey,
  includeUpgrade,
  leaveOutUpgrade,
  legacyConversion,
  moveUpgrade,
  newQueue,
  planHolds,
  queuePlan,
  reviewQueue,
  setExcluded,
  shopCatalog,
  stagePlan,
  type QueueBase,
  type QueueDraft,
} from '../../../src/core/shoppingPlan.ts';
import { dashboardPagesCase, dashboardPagesCaseNames } from '../../support/dashboardFixture.ts';
import {
  shoppingRunReadAnswerSchema,
  shoppingRunSaveAnswerSchema,
} from '../../../src/contract/shopping.ts';

const SQ = (name: string) => dashboardPagesCase(`shopping_queue.${name}`);
const sentPlan = (name: string) => ownPath(SQ(name).request.body, 'changes', 'shopping_plan');
function baseOf(name: string): QueueBase {
  const body = SQ(name).body;
  return {
    plan: queuePlan(ownPath(body, 'settings', 'shopping_plan')),
    catalog: shopCatalog(own(body, 'catalog')),
  };
}
const P1 = baseOf('shopping_read_before_any_save');

describe('F6-12 core shopping plan (pages fixture shopping_queue)', () => {
  it('SQ7 every recorded run-controls read and save parses with the generated schemas', () => {
    for (const name of dashboardPagesCaseNames('shopping_queue')) {
      const recorded = SQ(name);
      if (!recorded.request.path.startsWith('/api/run-controls')) continue;
      const schema =
        recorded.request.method === 'GET'
          ? shoppingRunReadAnswerSchema
          : shoppingRunSaveAnswerSchema;
      expect(schema.safeParse({ status: recorded.status, body: recorded.body }).success, name).toBe(
        true,
      );
    }
  });

  it('SQ7 an untouched draft sends nothing new: the recorded plan is held and unchanged', () => {
    const review = reviewQueue(P1, {});
    expect(review.changed).toBe(false);
    expect(review.problems).toBe(0);
    expect(
      planHolds(
        ownPath(SQ('shopping_read_before_any_save').body, 'settings', 'shopping_plan'),
        review.plan,
      ),
    ).toBe(true);
    expect(review.entries.map((entry) => [entry.id, entry.position])).toEqual([
      ['damage', 1],
      ['health', 2],
    ]);
  });

  it('SQ3 SQ5 SQ6 the recorded weighted, while-affordable, reordered save is exactly what the edits build', () => {
    let draft: QueueDraft = {
      reserve: '750.5',
      mode: 'weighted_split',
      cadence: 'while_affordable',
    };
    draft = moveUpgrade(P1, draft, 'health', -1) ?? draft;
    draft = {
      ...draft,
      [entryKey('health', 'weight')]: '3',
      [entryKey('health', 'until_wave')]: '20',
      [entryKey('damage', 'tap_limit')]: '',
    };
    const review = reviewQueue(P1, draft);
    expect(review.problems).toBe(0);
    expect(review.changed).toBe(true);
    expect(sameJson(review.plan, sentPlan('shopping_weighted_cadence_order_saved'))).toBe(true);
    // The save's answer and the read after it hold every sent value.
    const saved = ownPath(
      SQ('shopping_weighted_cadence_order_saved').body,
      'settings',
      'shopping_plan',
    );
    expect(planHolds(saved, review.plan)).toBe(true);
    expect(
      planHolds(
        ownPath(SQ('shopping_read_after_save').body, 'settings', 'shopping_plan'),
        review.plan,
      ),
    ).toBe(true);
    // The read before the save does not.
    expect(
      planHolds(
        ownPath(SQ('shopping_read_before_any_save').body, 'settings', 'shopping_plan'),
        review.plan,
      ),
    ).toBe(false);
  });

  it('SQ5 text that is not a number stays typed and blocks the plan; range rules are the server?s', () => {
    const review = reviewQueue(P1, {
      [entryKey('damage', 'tap_limit')]: '1e3',
      [entryKey('health', 'weight')]: '101',
      [entryKey('health', 'from_wave')]: '-1',
    });
    expect(review.entries[0]?.fields.tap_limit).toEqual({ raw: '1e3', problem: 'not_a_number' });
    expect(review.plan).toBeNull();
    const ranged = reviewQueue(P1, { [entryKey('health', 'weight')]: '101' });
    // weight 101 is the server's to refuse (recorded shopping_invalid_weight_refused).
    expect(ranged.problems).toBe(0);
    expect(ownPath(ranged.plan, 'queue')).toEqual([
      expect.objectContaining({ id: 'damage' }),
      expect.objectContaining({ id: 'health', weight: 101 }),
    ]);
  });

  it('SQ5 blank tap limit and last wave mean none; blank weight and first wave leave the server default', () => {
    const review = reviewQueue(P1, {
      [entryKey('damage', 'tap_limit')]: '',
      [entryKey('health', 'until_wave')]: '',
      [entryKey('health', 'weight')]: '',
      [entryKey('health', 'from_wave')]: ' ',
    });
    const queue = ownPath(review.plan, 'queue');
    expect(own((queue as unknown[])[0], 'tap_limit')).toBeNull();
    const health = (queue as unknown[])[1];
    expect(own(health, 'until_wave')).toBeNull();
    expect(Object.keys(health as object)).not.toContain('weight');
    expect(Object.keys(health as object)).not.toContain('from_wave');
  });

  it('SQ4 including from the catalogue appends once, re-includes in place and removes an exclusion explicitly', () => {
    let draft = includeUpgrade(P1, {}, 'attack_speed');
    draft = includeUpgrade(P1, draft, 'attack_speed');
    expect(reviewQueue(P1, draft).entries.map((entry) => entry.id)).toEqual([
      'damage',
      'health',
      'attack_speed',
    ]);
    draft = leaveOutUpgrade({ ...draft, [entryKey('damage', 'tap_limit')]: '9' }, 'damage');
    let review = reviewQueue(P1, draft);
    expect(review.entries[0]).toMatchObject({ id: 'damage', enabled: false, position: 1 });
    draft = includeUpgrade(P1, draft, 'damage');
    review = reviewQueue(P1, draft);
    expect(review.entries[0]).toMatchObject({ id: 'damage', enabled: true, position: 1 });
    expect(review.entries[0]?.fields.tap_limit.raw).toBe('9');
    expect(review.excluded).toEqual(['defense_absolute']);
    draft = includeUpgrade(P1, draft, 'defense_absolute');
    review = reviewQueue(P1, draft);
    expect(review.excluded).toEqual([]);
    expect(review.entries.at(-1)?.id).toBe('defense_absolute');
    expect(reviewQueue(P1, setExcluded(draft, 'defense_absolute', true)).excluded).toEqual([
      'defense_absolute',
    ]);
  });

  it('SQ6 moving stops at either end and keeps every field', () => {
    expect(moveUpgrade(P1, {}, 'damage', -1)).toBeNull();
    expect(moveUpgrade(P1, {}, 'health', 1)).toBeNull();
    const moved = moveUpgrade(P1, {}, 'damage', 1) ?? {};
    expect(reviewQueue(P1, moved).entries.map((entry) => entry.id)).toEqual(['health', 'damage']);
  });

  it('SQ2 a reviewed legacy conversion is staged with every warning; an unset reserve blocks it until typed', () => {
    const conversion = legacyConversion(
      own(SQ('shopping_legacy_list_preview').body, 'legacy_conversion'),
    );
    expect(conversion?.warnings).toHaveLength(5);
    expect(conversion?.requiresReview).toBe(true);
    const base: QueueBase = {
      plan: null,
      catalog: shopCatalog(own(SQ('shopping_legacy_list_preview').body, 'catalog')),
    };
    const staged = stagePlan(conversion?.plan.raw ?? {});
    expect(reviewQueue(base, staged).reserve).toEqual({ raw: '', problem: 'empty' });
    expect(reviewQueue(base, staged).plan).toBeNull();
    const review = reviewQueue(base, { ...staged, reserve: '0' });
    expect(sameJson(review.plan, sentPlan('shopping_reviewed_conversion_saved'))).toBe(true);
    expect(
      planHolds(
        ownPath(
          SQ('shopping_read_after_the_reviewed_conversion').body,
          'settings',
          'shopping_plan',
        ),
        review.plan,
      ),
    ).toBe(true);
  });

  it('SQ1 a new queue needs an explicit reserve, mode and cadence; 0 is a reserve', () => {
    const read = dashboardPagesCase('run_controls_save.read_before_the_plan_save').body;
    const catalog = shopCatalog(own(read, 'catalog'));
    const base: QueueBase = { plan: null, catalog };
    const created = newQueue(catalog, 30);
    const staged = stagePlan(created);
    const empty = reviewQueue(base, staged);
    expect([empty.reserve.problem, empty.modeProblem, empty.cadenceProblem]).toEqual([
      'empty',
      'empty',
      'empty',
    ]);
    expect(empty.excluded).toEqual(['defense_absolute']);
    const review = reviewQueue(base, {
      ...staged,
      reserve: '0',
      mode: 'highest_affordable',
      cadence: 'single_attempt',
    });
    // The recorded empty-queue save (P0) is exactly this plan.
    expect(sameJson(review.plan, sentPlan('shopping_empty_queue_saved'))).toBe(true);
  });
});

describe('F6-12 compliance round 1 (F6-12-C-03, F6-12-C-04)', () => {
  it.each([
    ['mode', 'cheapest_first'],
    ['cadence', 'forever'],
  ])('SQ3 F6-12-C-03 an unknown %s stays shown and blocks the plan', (field, value) => {
    const review = reviewQueue(P1, { [field]: value });
    expect(field === 'mode' ? review.mode : review.cadence).toBe(value);
    expect(field === 'mode' ? review.modeProblem : review.cadenceProblem).toBe('not_a_choice');
    expect(review.plan).toBeNull();
  });

  it.each(
    ['highest_affordable', 'round_robin', 'weighted_split'].flatMap((mode) =>
      ['single_attempt', 'while_affordable'].map((cadence) => [mode, cadence] as const),
    ),
  )('SQ3 F6-12-C-04 pattern %s with cadence %s round-trips into the plan', (mode, cadence) => {
    const review = reviewQueue(P1, { mode, cadence });
    expect(review.plan).toMatchObject({ queue_mode: mode, cadence });
    expect(review.changed).toBe(!(mode === 'highest_affordable' && cadence === 'single_attempt'));
  });

  it('SQ3 F6-12-C-04 the recorded round-robin save is exactly what the edits build', () => {
    let draft: QueueDraft = { reserve: '750.5', mode: 'round_robin', cadence: 'while_affordable' };
    draft = moveUpgrade(P1, draft, 'health', -1) ?? draft;
    draft = {
      ...draft,
      [entryKey('health', 'weight')]: '3',
      [entryKey('health', 'until_wave')]: '20',
      [entryKey('damage', 'tap_limit')]: '',
    };
    const review = reviewQueue(P1, draft);
    expect(sameJson(review.plan, sentPlan('shopping_round_robin_saved'))).toBe(true);
    expect(
      planHolds(
        ownPath(SQ('shopping_round_robin_saved').body, 'settings', 'shopping_plan'),
        review.plan,
      ),
    ).toBe(true);
  });

  it.each([
    ['reserve 0 stays 0', { reserve: '0' }, ['cash_reserve'], 0],
    ['decimal reserve', { reserve: '12.75' }, ['cash_reserve'], 12.75],
    ['cap 0', { [entryKey('damage', 'tap_limit')]: '0' }, ['queue', 0, 'tap_limit'], 0],
    ['integer cap', { [entryKey('damage', 'tap_limit')]: '7' }, ['queue', 0, 'tap_limit'], 7],
    [
      'blank cap is unlimited',
      { [entryKey('damage', 'tap_limit')]: '' },
      ['queue', 0, 'tap_limit'],
      null,
    ],
    ['weight 1', { [entryKey('health', 'weight')]: '1' }, ['queue', 1, 'weight'], 1],
    ['weight 100', { [entryKey('health', 'weight')]: '100' }, ['queue', 1, 'weight'], 100],
    [
      'exclusive last wave',
      { [entryKey('health', 'until_wave')]: '6' },
      ['queue', 1, 'until_wave'],
      6,
    ],
    // Out-of-range values are the server's to refuse: they are sent exactly as typed.
    ['negative cap', { [entryKey('damage', 'tap_limit')]: '-1' }, ['queue', 0, 'tap_limit'], -1],
    [
      'fractional cap',
      { [entryKey('damage', 'tap_limit')]: '1.5' },
      ['queue', 0, 'tap_limit'],
      1.5,
    ],
    ['weight 0', { [entryKey('health', 'weight')]: '0' }, ['queue', 1, 'weight'], 0],
    ['weight 101', { [entryKey('health', 'weight')]: '101' }, ['queue', 1, 'weight'], 101],
    ['reversed range', { [entryKey('health', 'until_wave')]: '2' }, ['queue', 1, 'until_wave'], 2],
  ] as const)(
    'SQ5 F6-12-C-04 %s: the typed value is sent as typed and its raw text kept',
    (_name, draft, path, sent) => {
      const review = reviewQueue(P1, draft);
      expect(review.problems).toBe(0);
      let value: unknown = review.plan;
      for (const step of path)
        value = typeof step === 'number' ? (value as unknown[])[step] : own(value, step);
      expect(value).toBe(sent);
      const [key, raw] = Object.entries(draft)[0] ?? ['', ''];
      const shown =
        key === 'reserve'
          ? review.reserve.raw
          : review.entries.find((entry) => key.startsWith(`entry:${entry.id}:`))?.fields[
              key.split(':')[2] as 'tap_limit' | 'weight' | 'from_wave' | 'until_wave'
            ].raw;
      expect(shown).toBe(raw);
    },
  );

  it('SQ5 F6-12-C-04 a hidden weight and wave range survive an edit of another field', () => {
    const review = reviewQueue(P1, { [entryKey('damage', 'tap_limit')]: '9' });
    const health = (ownPath(review.plan, 'queue') as unknown[])[1];
    expect(health).toMatchObject({ id: 'health', weight: 1, from_wave: 5, until_wave: null });
  });
});
