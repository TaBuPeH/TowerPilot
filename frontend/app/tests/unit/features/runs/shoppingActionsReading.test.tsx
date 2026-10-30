import { act, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { copy } from '../../../../src/copy/catalogue.ts';
import { entries, own, ownPath } from '../../../../src/core/read.ts';
import { selectShoppingLive } from '../../../../src/store/selectors/shoppingLive.ts';
import { selectThisBattle } from '../../../../src/store/selectors/thisBattle.ts';
import { postBodies } from '../../../support/dataLayer.ts';
import { dashboardPagesCase } from '../../../support/dashboardFixture.ts';
import { addBody, regionState } from '../../store/regionHelpers.ts';
import { renderPage } from '../dashboard/harness.tsx';

const sample = (name: string) => dashboardPagesCase(`shopping_queue.${name}`);
const record = (value: unknown) => Object.fromEntries(entries(value));

/** Test-local composition: the recorded queue before admission plus the recorded action envelope. */
function reading(kind: 'old' | 'current' | 'absent') {
  const body = sample('shopping_plan_read_requested').body;
  const battle = own(body, 'battle');
  const projection = dashboardPagesCase(
    `battle_actions_projection.${kind === 'old' ? 'projection_fails_later_the_last_projection_stays_and_its_stamps_stop' : 'values_carry_every_field_in_force'}`,
  ).body;
  const runtime = record(own(battle, 'runtime'));
  // Keep the projection's recorded relative age, on the queue read's clock/context.
  if (kind !== 'absent') {
    const actions = record(ownPath(projection, 'battle', 'runtime', 'actions'));
    const age =
      Number(ownPath(projection, 'battle', 'runtime', 'server_time')) -
      Number(actions['observed_at']);
    runtime['actions'] = { ...actions, observed_at: Number(runtime['server_time']) - age };
  } else delete runtime['actions'];
  return {
    ...record(body),
    battle: {
      ...record(battle),
      runtime,
      control_revision: 0,
      applied_revision: 0,
      overrides: {},
      applied_overrides: {},
      commands: [],
    },
  };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  window.location.hash = '';
  vi.useRealTimers();
});

it.each(['current', 'absent'] as const)(
  'AC2 Q2 shopping %s readings retain command compatibility',
  (kind) => {
    let state = addBody(
      { ...regionState(), contextConfirmed: true },
      'battlesActive',
      reading(kind),
    );
    const vm = selectShoppingLive(state);
    state = { ...state, runDrafts: { [vm.scope]: { reserve: '00750.50' } } };
    expect(selectShoppingLive(state).command?.changes['shopping_plan']).toMatchObject({
      cash_reserve: 750.5,
    });
  },
);

it('AC2 Q2 shopping old-to-current keeps raw typing, sends nothing on refresh, then explicitly saves the exact draft', async () => {
  let source = reading('old');
  window.location.hash = '#runs/live';
  const accepted = sample('shopping_plan_accepted');
  const page = await renderPage({
    config: () =>
      dashboardPagesCase('run_templates_add_fenced.read_config_the_page_context_comes_from').body,
    before: (fake) => {
      fake.route('GET', '/api/battles/active', () => ({ status: 200, body: source }));
      fake.route('POST', accepted.request.path, () => accepted);
    },
  });
  await page.advance(100);
  const editor = page.view.container.querySelector<HTMLElement>('.tp-shopping-live');
  if (editor === null) throw new Error('missing shopping editor');
  const field = within(editor).getByRole('textbox', { name: 'Cash reserve' });
  const save = within(editor).getByRole('button', { name: 'Save queue for this battle' });
  expect(field).toBeEnabled();
  await page.user.clear(field);
  await page.user.type(field, 'not-a-number');
  expect(field).toHaveValue('not-a-number');
  expect(save).toHaveAccessibleDescription(copy.runs.reasons.action_report_old.short);
  await page.user.clear(field);
  await page.user.type(field, '00750.50');
  expect(field).toHaveValue('00750.50');
  expect(save).toHaveAttribute('aria-disabled', 'true');
  expect(save).toHaveAccessibleDescription(copy.runs.reasons.action_report_old.short);
  await page.user.click(save);
  expect(postBodies(page.fake)).toEqual([]);
  source = reading('current');
  act(() => {
    page.data.commands.checkBattle();
  });
  await page.advance(100);
  expect(field).toHaveValue('00750.50');
  expect(save).not.toHaveAttribute('aria-disabled', 'true');
  expect(postBodies(page.fake)).toEqual([]);
  const command = selectShoppingLive(page.data.store.getSnapshot()).command;
  expect(command).not.toBeNull();
  await page.user.click(save);
  await page.advance(100);
  expect(postBodies(page.fake, accepted.request.path)).toEqual([
    expect.objectContaining({ changes: command?.changes }),
  ]);
});

it('AC2 Q2 shopping current-to-old refuses a captured command and preserves the existing raw draft', async () => {
  let source = reading('current');
  window.location.hash = '#runs/live';
  const page = await renderPage({
    config: () =>
      dashboardPagesCase('run_templates_add_fenced.read_config_the_page_context_comes_from').body,
    before: (fake) => {
      fake.route('GET', '/api/battles/active', () => ({ status: 200, body: source }));
    },
  });
  await page.advance(100);
  const tryOnce = selectThisBattle(page.data.store.getSnapshot()).tryOnce.find(
    (item) => item.action === 'shopping',
  )?.button;
  expect(tryOnce?.enabled).toBe(true);
  const vm = selectShoppingLive(page.data.store.getSnapshot());
  act(() => {
    page.data.commands.editDraft(vm.scope, 'reserve', '00750.50');
  });
  const captured = selectShoppingLive(page.data.store.getSnapshot()).command;
  expect(captured).not.toBeNull();
  if (captured === null) throw new Error('missing current command');
  source = reading('old');
  act(() => {
    page.data.commands.checkBattle();
  });
  await page.advance(100);
  expect(selectShoppingLive(page.data.store.getSnapshot())).toMatchObject({
    command: null,
    reason: 'action_report_old',
    draft: { reserve: '00750.50' },
  });
  await act(async () => page.data.commands.saveLive(captured));
  const blockedTry = selectThisBattle(page.data.store.getSnapshot()).tryOnce.find(
    (item) => item.action === 'shopping',
  )?.button;
  expect(blockedTry?.enabled).toBe(false);
  expect(blockedTry?.reason).toBe('action_report_old');
  const capturedTry = tryOnce?.command;
  if (capturedTry === null || capturedTry === undefined)
    throw new Error('missing current Try once command');
  await act(async () => page.data.commands.runNow(capturedTry));
  expect(postBodies(page.fake)).toEqual([]);
  const editor = page.view.container.querySelector<HTMLElement>('.tp-shopping-live');
  if (editor === null) throw new Error('missing shopping editor');
  expect(within(editor).getByRole('textbox', { name: 'Cash reserve' })).toHaveValue('00750.50');
});
