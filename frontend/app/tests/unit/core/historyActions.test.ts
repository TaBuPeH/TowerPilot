import { describe, expect, it } from 'vitest';
import { ACTION_EFFECTS, ACTION_RESULTS, ACTION_STATES } from '../../../src/core/actions.ts';
import {
  executionRow,
  executionsView,
  olderPage,
  type OlderExecutions,
} from '../../../src/core/historyActions.ts';
import { objectOf, r17bBody, rowsOf, without } from '../../support/historyExecutions.ts';

/**
 * A2-7 AC4: the rules that read the `executions` block of a battle detail and a page of the generic
 * route, over the landed r17b recordings (backend/tests/fixtures/dashboard_contract_pages_v1.json). A case changed from
 * its recording says so in a comment on the line that changes it.
 */
const NONE: OlderExecutions = {
  loaded: false,
  rows: [],
  cursor: null,
  truncated: false,
  unusable: 0,
};
const block = (name: string) => r17bBody(name)['executions'];
const GENERIC =
  'action_executions_more.battle_detail_truncated_continued_through_the_generic_route';
const TRUNCATED = 'action_executions_more.battle_detail_truncated_at_fifty';

describe('AC4 shares the action vocabulary without changing History semantics', () => {
  it.each(['running', ...ACTION_RESULTS])(
    'keeps %s as a known execution state and result',
    (code) => {
      // Synthetic vocabulary cases: History has always accepted running in either field.
      expect(executionRow({ state: code }, 0).state).toEqual({ kind: code, code });
      expect(executionRow({ state: 'future', outcome: { result: code } }, 0).result).toEqual({
        kind: code,
        code,
      });
      expect(executionRow({ state: code, outcome: { result: code } }, 0).result).toBeNull();
    },
  );

  it.each(ACTION_EFFECTS)('keeps %s as a known effect only after the execution closes', (code) => {
    expect(executionRow({ state: 'succeeded', outcome: { effect: code } }, 0).effect).toEqual({
      kind: code,
      code,
    });
    expect(executionRow({ state: 'running', outcome: { effect: code } }, 0).effect).toBeNull();
  });

  it('does not treat live action states as execution states or results', () => {
    for (const code of ACTION_STATES.filter(
      (state) => state !== 'running' && state !== 'unknown',
    )) {
      expect(executionRow({ state: code }, 0).state).toEqual({ kind: 'other', code });
      expect(executionRow({ state: 'future', outcome: { result: code } }, 0).result).toEqual({
        kind: 'other',
        code,
      });
    }
  });

  it('keeps an empty string as a reported, unrecognised code', () => {
    const row = executionRow({ state: '', outcome: { result: '', effect: '' } }, 0);
    expect(row.state).toEqual({ kind: 'other', code: '' });
    expect(row.result).toBeNull();
    expect(row.effect).toEqual({ kind: 'other', code: '' });
  });

  it.each([undefined, null, 42])('keeps an unreported code %s distinct from unknown', (code) => {
    const row = executionRow({ state: code, outcome: { result: code, effect: code } }, 0);
    expect(row.state).toEqual({ kind: 'unreported', code: null });
    expect(row.result).toBeNull();
    expect(row.effect).toEqual({ kind: 'unreported', code: null });
  });
});

describe('AC4 the executions block of a battle detail', () => {
  it('AC4 a started battle lists its rows newest first with the recorded words and numbers', () => {
    const view = executionsView(
      block('action_executions.battle_detail_of_a_started_battle_lists_its_executions'),
      NONE,
    );
    if (view.kind !== 'rows') throw new Error('expected rows');
    expect(
      view.rows.map((row) => [row.action, row.label, row.state.kind, row.effect?.kind]),
    ).toEqual([
      ['floating_gems', 'Floating gems', 'no_op', 'none'],
      ['ad_gems', 'Ad gems', 'succeeded', 'offer_gone'],
    ]);
    expect(view.rows.map((row) => row.durationMs)).toEqual([null, 4000]);
    expect(view.rows[0]?.aggregate).toEqual({ count: 1, firstAt: 1800000520, lastAt: 1800000520 });
    expect(view.rows[1]?.aggregate).toBeNull();
    expect(view.rows.map((row) => row.trigger.kind)).toEqual(['automatic', 'automatic']);
    expect(view.truncated).toBe(false);
    expect(view.cursor).toBeNull();
  });

  it('AC4 an adopted battle keeps its since_adoption coverage on every row', () => {
    const view = executionsView(
      block(
        'action_executions.battle_detail_of_an_adopted_battle_lists_its_executions_covered_since_the_adoption',
      ),
      NONE,
    );
    if (view.kind !== 'rows') throw new Error('expected rows');
    expect(view.rows.map((row) => row.coverage.kind)).toEqual(['since_adoption', 'since_adoption']);
  });

  it.each([
    [
      'action_executions.battle_detail_on_a_ledger_a_newer_build_wrote_is_not_interpreted',
      'newer_schema',
    ],
    ['action_executions.battle_detail_recorded_before_the_action_store_existed', 'before_history'],
  ] as const)('AC4 items null is never "none": %s is %s', (name, kind) => {
    expect(executionsView(block(name), NONE)).toEqual({ kind });
  });

  it('AC4 items null with an unrecognised reason stays unreadable and keeps the reason', () => {
    // Modified recorded case: the reason of battle_detail_recorded_before_the_action_store_existed
    // replaced by a code no recording carries.
    const changed = { items: null, reason: 'future_reason', truncated: false };
    expect(executionsView(changed, NONE)).toEqual({ kind: 'unreadable', reason: 'future_reason' });
  });

  it('AC4 an answer with no executions member is not reported, never "none"', () => {
    expect(executionsView(undefined, NONE)).toEqual({ kind: 'not_reported' });
    expect(executionsView('x', NONE)).toEqual({ kind: 'not_reported' });
  });

  it('AC4 an empty items list is a recorded empty history', () => {
    // Modified recorded case: the items of battle_detail_of_a_started_battle_lists_its_executions
    // emptied; reason stays null as the recording has it.
    expect(executionsView({ items: [], reason: null, truncated: false }, NONE)).toEqual({
      kind: 'empty',
    });
  });

  it('AC4 a truncated block offers the recorded next_before cursor', () => {
    const view = executionsView(block(TRUNCATED), NONE);
    if (view.kind !== 'rows') throw new Error('expected rows');
    expect(view.rows).toHaveLength(50);
    expect(view.truncated).toBe(true);
    expect(view.cursor).toBe('1800000110.0:00000000000000000000000000000065');
  });

  it('AC4 a truncated block with no cursor is still truncated and offers no read', () => {
    // Modified recorded case: next_before removed from battle_detail_truncated_at_fifty.
    const rest = without(objectOf(block(TRUNCATED)), 'next_before');
    const view = executionsView(rest, NONE);
    if (view.kind !== 'rows') throw new Error('expected rows');
    expect(view.truncated).toBe(true);
    expect(view.cursor).toBeNull();
  });

  it('AC4 a refresher-settled row is interrupted with no finish time and no duration', () => {
    const view = executionsView(
      block('action_executions_more.battle_detail_after_the_refresher_settled_an_open_row'),
      NONE,
    );
    if (view.kind !== 'rows') throw new Error('expected rows');
    const row = view.rows[0];
    expect(row?.state.kind).toBe('interrupted');
    expect(row?.finishedAt).toBeNull();
    expect(row?.durationMs).toBeNull();
    expect(row?.effect?.kind).toBe('unknown');
    expect(row?.reason).toBe('operation_ended');
  });

  it('AC4 a non-monotonic row suppresses even a numeric duration', () => {
    const recorded = objectOf(
      block('action_executions_more.battle_detail_after_the_refresher_settled_an_open_row'),
    );
    const [row] = rowsOf(recorded['items']);
    expect(row?.['duration_basis']).toBe('none');
    expect(row?.['duration_ms']).toBeNull();
    // Modified recorded case: give this settled, non-aggregate duration_basis "none" row
    // a positive duration_ms; only the monotonic-basis guard should suppress the number.
    const view = executionsView({ ...recorded, items: [{ ...row, duration_ms: 4000 }] }, NONE);
    if (view.kind !== 'rows') throw new Error('expected rows');
    expect(view.rows).toHaveLength(1);
    expect(view.rows[0]).toMatchObject({
      open: false,
      aggregate: null,
      showsDuration: true,
      durationMs: null,
    });
  });

  it('AC4 a Run now row keeps its manual trigger and its monotonic duration', () => {
    const view = executionsView(
      block('action_executions_more.battle_detail_lists_the_run_now_execution'),
      NONE,
    );
    if (view.kind !== 'rows') throw new Error('expected rows');
    expect(view.rows[0]).toMatchObject({
      trigger: { kind: 'manual' },
      state: { kind: 'succeeded' },
    });
    expect(view.rows[0]?.durationMs).toBeCloseTo(23349.99990463257, 6);
  });

  it('AC4 an open row has no effect and no duration: nothing is claimed before it ends', () => {
    // Modified recorded case: generic-route rows wrapped as a battle detail block (items) here and in
    // the failed-row test below, because no battle detail recording holds an open or failed row.
    const open = r17bBody(
      'action_executions_more.read_an_open_row_before_the_refresher_settles_it',
    );
    const view = executionsView(
      { items: open['executions'], reason: null, truncated: false },
      NONE,
    );
    if (view.kind !== 'rows') throw new Error('expected rows');
    expect(view.rows[0]).toMatchObject({
      state: { kind: 'running' },
      effect: null,
      durationMs: null,
      finishedAt: null,
    });
  });

  it('AC4 failed rows keep the unconfirmed effect; refused and harvest_launch rows read as recorded', () => {
    const page = r17bBody('action_executions.read_a_bare_start_time_returns_strictly_older_rows');
    const failed = executionsView(
      { items: page['executions'], reason: null, truncated: false },
      NONE,
    );
    if (failed.kind !== 'rows') throw new Error('expected rows');
    const row = failed.rows.find((candidate) => candidate.state.kind === 'failed');
    expect(row).toMatchObject({ effect: { kind: 'unconfirmed' }, reason: 'attempt_timeout' });
    expect(row?.durationMs).toBe(15000);
    const harvest = r17bBody('action_executions_more.read_harvest_launch_accepted');
    // Modified recorded case: the harvest_launch rows of the generic route placed in a battle
    // detail block, because no recording lists them there.
    const launched = executionsView(
      { items: harvest['executions'], reason: null, truncated: false },
      NONE,
    );
    if (launched.kind !== 'rows') throw new Error('expected rows');
    expect(
      launched.rows.map((candidate) => [
        candidate.action,
        candidate.state.kind,
        candidate.durationMs,
      ]),
    ).toEqual([
      ['harvest_launch', 'succeeded', null],
      ['harvest_launch', 'refused', null],
    ]);
  });

  it('AC4 a null label stays null (the page then shows the code) and a vocabulary it does not know stays visible', () => {
    // Modified recorded case: label null on a started-battle row (no recording has a null label),
    // a state, an effect and a trigger no recording carries.
    const original = r17bBody(
      'action_executions.battle_detail_of_a_started_battle_lists_its_executions',
    );
    const items = rowsOf(objectOf(original['executions'])['items']).map((item) => ({
      ...item,
      label: null,
      state: 'paused_by_future',
      trigger: 'schedule',
      outcome: { ...objectOf(item['outcome']), effect: 'partial', result: 'mixed' },
    }));
    const view = executionsView({ items, reason: null, truncated: false }, NONE);
    if (view.kind !== 'rows') throw new Error('expected rows');
    expect(view.rows[0]).toMatchObject({
      label: null,
      action: 'floating_gems',
      state: { kind: 'other', code: 'paused_by_future' },
      trigger: { kind: 'other', code: 'schedule' },
      effect: { kind: 'other', code: 'partial' },
      result: { kind: 'other', code: 'mixed' },
    });
  });
});

describe('AC4 older executions through the generic route', () => {
  const battleId = '0000019e-0000-4000-8000-000000000000';

  it('AC4 the recorded continuation is the last page: one older row, no cursor', () => {
    const result = olderPage(r17bBody(GENERIC), battleId);
    if (result.kind !== 'page') throw new Error('expected a page');
    const page = result.page;
    expect(page.rows).toHaveLength(1);
    expect(page.cursor).toBeNull();
    expect(page.truncated).toBe(false);
    expect(page.unusable).toBe(0);
  });

  it('AC4 the view joins the older rows after the block rows and drops the notice at the end', () => {
    const result = olderPage(r17bBody(GENERIC), battleId);
    if (result.kind !== 'page') throw new Error('expected a page');
    const page = result.page;
    const older: OlderExecutions = { loaded: true, ...page };
    const view = executionsView(block(TRUNCATED), older);
    if (view.kind !== 'rows') throw new Error('expected rows');
    expect(view.rows).toHaveLength(51);
    expect(view.rows.at(-1)?.key).toBe('00000000000000000000000000000066');
    expect(view.truncated).toBe(false);
    expect(view.cursor).toBeNull();
  });

  it('AC4 a row listed twice is shown once', () => {
    const view = executionsView(block(TRUNCATED), {
      loaded: true,
      rows: rowsOf(objectOf(block(TRUNCATED))['items']).slice(0, 2),
      cursor: null,
      truncated: false,
      unusable: 0,
    });
    if (view.kind !== 'rows') throw new Error('expected rows');
    expect(view.rows).toHaveLength(50);
  });

  it('AC4 a page naming another battle, another shape or no list is refused', () => {
    expect(olderPage(r17bBody(GENERIC), 'another-battle')).toEqual({
      kind: 'refused',
      why: 'other_battle',
    });
    expect(olderPage({ ...r17bBody(GENERIC), executions: 'x' }, battleId)).toEqual({
      kind: 'refused',
      why: 'no_list',
    });
    expect(olderPage(undefined, battleId)).toEqual({ kind: 'refused', why: 'no_list' });
  });

  it('AC4 a ledger a newer build wrote answers an empty page with its reason, which is not a complete history', () => {
    const recorded = r17bBody(
      'action_executions.read_a_ledger_a_newer_build_wrote_is_not_interpreted',
    );
    expect(olderPage(recorded, battleId)).toEqual({
      kind: 'refused',
      why: 'reason',
      reason: 'newer_schema',
    });
  });

  it('AC4 a middle page keeps its cursor and counts the damaged rows the server skipped', () => {
    // Modified recorded case: the continuation with truncated true, a next_before and unusable 2.
    const body = {
      ...r17bBody(GENERIC),
      truncated: true,
      next_before: '1800000100.0:00000000000000000000000000000066',
      unusable: 2,
    };
    expect(olderPage(body, battleId)).toMatchObject({
      kind: 'page',
      page: {
        cursor: '1800000100.0:00000000000000000000000000000066',
        truncated: true,
        unusable: 2,
      },
    });
  });
});

describe('AC4 what a row says it is, decided once', () => {
  it('AC4 an open row says it is open and shows no duration; an aggregate row shows none either', () => {
    // Modified recorded case: the open row of the generic route wrapped as a battle detail block.
    const open = r17bBody(
      'action_executions_more.read_an_open_row_before_the_refresher_settles_it',
    );
    const opened = executionsView(
      { items: open['executions'], reason: null, truncated: false },
      NONE,
    );
    if (opened.kind !== 'rows') throw new Error('expected rows');
    expect(opened.rows[0]).toMatchObject({ open: true, showsDuration: false });
    const started = executionsView(
      block('action_executions.battle_detail_of_a_started_battle_lists_its_executions'),
      NONE,
    );
    if (started.kind !== 'rows') throw new Error('expected rows');
    expect(started.rows.map((row) => [row.action, row.open, row.showsDuration])).toEqual([
      ['floating_gems', false, false],
      ['ad_gems', false, true],
    ]);
  });

  it('AC4 a settled row shows its duration line even when none was recorded', () => {
    const view = executionsView(
      block('action_executions_more.battle_detail_after_the_refresher_settled_an_open_row'),
      NONE,
    );
    if (view.kind !== 'rows') throw new Error('expected rows');
    expect(view.rows[0]).toMatchObject({ open: false, showsDuration: true, durationMs: null });
  });
});

describe('AC4 a refused older page keeps its reason', () => {
  it('AC4 a page with a reason is refused and names the code it carried', () => {
    // Modified recorded case: the recorded continuation answered as a newer ledger answers it.
    const page = { ...r17bBody(GENERIC), executions: [], reason: 'newer_schema' };
    expect(olderPage(page, '0000019e-0000-4000-8000-000000000000')).toEqual({
      kind: 'refused',
      why: 'reason',
      reason: 'newer_schema',
    });
  });
});
