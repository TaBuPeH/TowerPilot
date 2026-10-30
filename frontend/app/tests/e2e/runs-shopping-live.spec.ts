import { fillBoundsDraft, expectBoundsDraft } from './helpers/shopping-bounds.ts';
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { entries, own, ownPath } from '../../src/core/read.ts';
import { dashboardPagesCase } from '../support/dashboardFixture.ts';
import { copy } from '../../src/copy/catalogue.ts';
import { bounds } from '../support/shoppingBounds.ts';
import { axeIds, bodiesTo, posts } from './helpers/helpers.ts';
const OUT = process.env['TP_F6_SCREENS_DIR'] ?? path.resolve('test-results', 'f6-screens');
mkdirSync(OUT, { recursive: true });
const sample = (name: string) => dashboardPagesCase(`shopping_queue.${name}`);
const record = (value: unknown) => Object.fromEntries(entries(value));
test('SQ8 startup context replaces an early queue and the bound upgrade sends once', async ({
  page,
}) => {
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/config', async (route) => {
    await gate;
    await route.continue();
  });
  await page.route('**/api/battles/active', (route) =>
    route.fulfill({ status: 200, json: before('shopping_upgrade_requested') }),
  );
  await open(page, 'shopping_live_upgrade', 1054, false);
  const action = page.getByRole('button', { name: 'Try once Damage', exact: true });
  await expect(
    page.getByRole('button', { name: 'Try once Damage', exact: true, includeHidden: true }),
  ).toBeEnabled();
  expect(await page.evaluate(() => sessionStorage.getItem('tp.context.v1'))).toBeNull();
  await page.getByText('Queue for this battle', { exact: true }).click();
  await expect(action).toBeVisible();
  release();
  await expect
    .poll(() => page.evaluate(() => sessionStorage.getItem('tp.context.v1')))
    .not.toBeNull();
  await expect(page.locator('.tp-battle__queue details').first()).not.toHaveAttribute('open', '');
  await expect(action).not.toBeVisible();
  await openUpgradeQueue(page, 'Damage');
  await action.click();
  await expect(
    page.locator('.tp-battle__command').filter({ hasText: 'shopping:attack:damage' }),
  ).toBeVisible();
  const accepted = sample('shopping_upgrade_accepted');
  expect(await bodiesTo(page, accepted.request.path)).toEqual([
    expect.objectContaining({
      ...record(accepted.request.body),
      idempotency_key: expect.any(String),
    }),
  ]);
});
/** Undo admission for a pre-click read. Write receipts have no read envelope: reuse the recorded catalogue and runtime time, keeping their own battle identity. */
function before(name: string) {
  const body = sample(name).body;
  return {
    ...record(sample('shopping_upgrade_requested').body),
    ...record(body),
    server_time: ownPath(body, 'battle', 'runtime', 'server_time'),
    battle: {
      ...record(own(body, 'battle')),
      control_revision: 0,
      applied_revision: 0,
      overrides: {},
      applied_overrides: {},
      commands: [],
    },
  };
}
async function open(page: Page, scenario: string, width: number, runs = true) {
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`/__fake/use?scenario=${scenario}`);
  if (runs)
    await page.evaluate(() => {
      location.hash = '#runs/live';
    });
  await expect(
    page.getByRole('heading', { level: 1, name: runs ? 'Runs' : 'Dashboard' }),
  ).toBeVisible();
}
async function openUpgradeQueue(page: Page, name: string) {
  // A battle can arrive before config. Establishing the context drops those
  // early reads and replaces the queue, so await the published context first.
  await expect
    .poll(() => page.evaluate(() => sessionStorage.getItem('tp.context.v1')))
    .not.toBeNull();
  // Then await the battle read belonging to that context before opening it.
  await expect(
    page.getByRole('button', { name: `Try once ${name}`, exact: true, includeHidden: true }),
  ).toBeEnabled();
  await page.getByText('Queue for this battle', { exact: true }).click();
}
async function check(page: Page, width: number, state: string) {
  const violations = await axeIds(page, ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']);
  expect(violations).toEqual([]);
  const layout = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    small: [
      ...document.querySelectorAll<HTMLElement>(
        'main button, main input, main select, main summary, main a',
      ),
    ]
      .filter((el) => el.getClientRects().length > 0)
      .filter((el) => {
        const box = el.getBoundingClientRect();
        return box.height < 24 || box.width < 24;
      })
      .map((el) => el.outerHTML.slice(0, 150)),
  }));
  expect(layout).toEqual({ overflow: 0, small: [] });
  if (width === 390)
    for (const button of await page
      .locator(
        '.tp-shopping-live button:visible, .tp-shopping-try button:visible, .tp-battle__queue-entry button:visible',
      )
      .all()) {
      const box = await button.boundingBox();
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
      expect(box?.width ?? 0).toBeGreaterThanOrEqual(44);
    }
  await page.screenshot({
    path: path.join(OUT, `shopping-live-${state}-${String(width)}.png`),
    fullPage: true,
  });
}
async function edit(page: Page) {
  const editor = page.locator('.tp-shopping-live');
  await editor.getByRole('textbox', { name: 'Cash reserve', exact: true }).fill('750.5');
  await editor.getByLabel('Purchase pattern', { exact: true }).selectOption('weighted_split');
  await editor.getByLabel('Cadence', { exact: true }).selectOption('while_affordable');
  const health = editor.locator('.tp-shop__entry[data-upgrade="health"]');
  await health.getByRole('button', { name: /Move up/ }).click();
  await health.getByRole('textbox', { name: 'Weight', exact: true }).fill('3');
  await health.getByRole('textbox', { name: 'Until wave (exclusive)', exact: true }).fill('20');
  await editor
    .locator('.tp-shop__entry[data-upgrade="damage"]')
    .getByRole('textbox', { name: 'Tap attempts this battle', exact: true })
    .fill('');
}

/** Test-local: a pre-admission shopping read with the recorded action projection and its relative age. */
function shoppingReading(kind: 'old' | 'current') {
  const body = before('shopping_plan_read_requested');
  const projection = dashboardPagesCase(
    `battle_actions_projection.${kind === 'old' ? 'projection_fails_later_the_last_projection_stays_and_its_stamps_stop' : 'values_carry_every_field_in_force'}`,
  ).body;
  const actions = record(ownPath(projection, 'battle', 'runtime', 'actions'));
  const age =
    Number(ownPath(projection, 'battle', 'runtime', 'server_time')) -
    Number(actions['observed_at']);
  const runtime = record(ownPath(body, 'battle', 'runtime'));
  return {
    ...body,
    battle: {
      ...body.battle,
      runtime: {
        ...runtime,
        actions: { ...actions, observed_at: Number(runtime['server_time']) - age },
      },
    },
  };
}

for (const width of [1360, 1054, 390]) {
  for (const initial of ['old', 'current'] as const) {
    test(`AC2 Q2 shopping ${initial}-old-current retains raw draft and requires explicit save at ${String(width)}`, async ({
      page,
    }, testInfo) => {
      let source = shoppingReading(initial);
      await page.route('**/api/battles/active', (route) =>
        route.fulfill({ status: 200, json: source }),
      );
      await open(page, 'shopping_live_save', width);
      const editor = page.locator('.tp-shopping-live');
      const field = editor.getByRole('textbox', { name: 'Cash reserve', exact: true });
      const save = editor.getByRole('button', { name: 'Save queue for this battle', exact: true });
      await expect(field).toBeEnabled();
      await field.fill('00750.50');
      if (initial === 'current') await expect(save).not.toHaveAttribute('aria-disabled', 'true');
      source = shoppingReading('old');
      await page.getByRole('button', { name: 'Check this battle', exact: true }).click();
      await expect(save).toHaveAttribute('aria-disabled', 'true');
      await expect(save).toHaveAccessibleDescription(copy.runs.reasons.action_report_old.short);
      await expect(field).toHaveValue('00750.50');
      await save.focus();
      await page.keyboard.press('Enter');
      const accepted = sample('shopping_plan_accepted');
      const oldPosts = await posts(page);
      expect(oldPosts).toEqual([]);
      await check(page, width, `q2-${initial}-old`);
      source = shoppingReading('current');
      await page.getByRole('button', { name: 'Check this battle', exact: true }).click();
      await expect(save).not.toHaveAttribute('aria-disabled', 'true');
      await expect(field).toHaveValue('00750.50');
      const refreshPosts = await posts(page);
      expect(refreshPosts).toEqual([]);
      const expectedPlan = {
        ...record(ownPath(source, 'battle', 'effective', 'shopping_plan')),
        cash_reserve: 750.5,
      };
      await save.click();
      await expect(page.locator('[data-outcome="live_recorded"]')).toBeVisible();
      const sent = await bodiesTo(page, accepted.request.path);
      expect(sent).toEqual([
        {
          ...record(accepted.request.body),
          changes: { shopping_plan: expectedPlan },
          idempotency_key: expect.any(String),
        },
      ]);
      await testInfo.attach('q2-shopping-gate', {
        body: JSON.stringify({ initial, width, oldPosts, refreshPosts, sent }, null, 2),
        contentType: 'application/json',
      });
      await check(page, width, `q2-${initial}-saved`);
    });
  }
}
for (const width of [1360, 1054, 390]) {
  for (const name of [
    'live_weight_zero_refused',
    'live_wave_range_reversed_refused',
    'live_boundary_values_accepted',
  ]) {
    test(`SQ5 ${name} at ${String(width)}: exact body, server refusal beside Save or boundary applied read, axe and targets`, async ({
      page,
    }) => {
      let source = bounds('read_after_the_live_refusals_nothing_was_recorded');
      const answer = bounds(name);
      await page.route('**/api/battles/active', (route) =>
        route.fulfill({ status: source.status, json: source.body }),
      );
      await page.route('**/api/operations/*/live-controls', async (route) => {
        await route.fetch();
        await route.fulfill({ status: answer.status, json: answer.body });
      });
      await open(page, 'shopping_live_save', width);
      const editor = page.locator('.tp-shopping-live');
      await fillBoundsDraft(editor, name);
      await editor.getByRole('button', { name: 'Save queue for this battle', exact: true }).click();
      if (answer.status === 400) {
        const refusal = editor.locator('[data-queue-refusal] [data-server-text="message"]');
        await expect(refusal).toBeVisible();
        await expect(refusal).toHaveText(String(own(answer.body, 'error')));
        await expect(page.locator('[data-outcome="live_applied"]')).toHaveCount(0);
      } else {
        await expect(page.locator('[data-outcome="live_recorded"]')).toBeVisible();
        await expect(page.locator('[data-outcome="live_applied"]')).toHaveCount(0);
        source = bounds('read_after_the_live_boundary_values_applied');
      }
      await page.getByRole('button', { name: 'Check this battle', exact: true }).click();
      if (answer.status === 400) {
        await expect(page.locator('[data-outcome="live_not_recorded"]')).toBeVisible();
        await expectBoundsDraft(editor, name);
      } else await expect(page.locator('[data-outcome="live_applied"]')).toBeVisible();
      expect(await bodiesTo(page, answer.request.path)).toEqual([
        { ...record(answer.request.body), idempotency_key: expect.any(String) },
      ]);
      await check(page, width, `bounds-${name}`);
    });
  }
  test(`SQ7 SQ12 live save requested then applied at ${String(width)}`, async ({ page }) => {
    let source: unknown = before('shopping_plan_read_requested');
    await page.route('**/api/battles/active', (route) =>
      route.fulfill({ status: 200, json: source }),
    );
    await open(page, 'shopping_live_save', width);
    await edit(page);
    await page
      .locator('.tp-shopping-live')
      .getByRole('button', { name: 'Save queue for this battle' })
      .click();
    await expect(page.locator('[data-outcome="live_recorded"]')).toBeVisible();
    const path = sample('shopping_plan_accepted').request.path;
    expect(await bodiesTo(page, path)).toEqual([
      expect.objectContaining({
        ...record(sample('shopping_plan_accepted').request.body),
        idempotency_key: expect.any(String),
      }),
    ]);
    source = sample('shopping_plan_read_requested').body;
    await page.getByRole('button', { name: 'Check this battle', exact: true }).click();
    await expect(page.locator('[data-outcome="live_recorded"]')).toBeVisible();
    await check(page, width, 'requested');
    source = sample('shopping_plan_read_applied').body;
    await page.getByRole('button', { name: 'Check this battle', exact: true }).click();
    await expect(page.locator('[data-outcome="live_applied"]')).toBeVisible();
    await expect(
      page.locator('.tp-shopping-live').getByRole('button', { name: 'Discard queue edits' }),
    ).toHaveCount(0);
    expect(await bodiesTo(page, path)).toHaveLength(1);
    await check(page, width, 'applied');
  });
  test(`SQ12 Unknown then identical explicit resend at ${String(width)}`, async ({ page }) => {
    let source: unknown = before('shopping_plan_unknown_read');
    await page.route('**/api/battles/active', (route) =>
      route.fulfill({ status: 200, json: source }),
    );
    await open(page, 'shopping_live_unknown', width);
    await edit(page);
    await page
      .locator('.tp-shopping-live')
      .getByRole('button', { name: 'Save queue for this battle' })
      .click();
    await expect(page.locator('[data-outcome="live_unconfirmed"]')).toBeVisible();
    const path = sample('shopping_plan_reply_unknown').request.path;
    const first = await bodiesTo(page, path);
    await check(page, width, 'unknown');
    await page.request.get('/__fake/set?scenario=shopping_live_replay');
    await page.getByRole('button', { name: 'Send same request again', exact: true }).click();
    await expect(page.locator('[data-outcome="live_recorded"]')).toBeVisible();
    expect(await bodiesTo(page, path)).toEqual([first[0], first[0]]);
    source = sample('shopping_plan_unknown_read').body;
    await check(page, width, 'replayed');
  });
  for (const [kind, name, action] of [
    ['whole', 'Shopping', 'shopping'],
    ['attack', 'Attack', 'shopping:attack'],
    ['defense', 'Defense', 'shopping:defense'],
    ['utility', 'Utility', 'shopping:utility'],
    ['upgrade', 'Damage', 'shopping:attack:damage'],
  ]) {
    test(`SQ8 Try once ${kind ?? ''} sends one exact action with outcome at ${String(width)}`, async ({
      page,
    }) => {
      const caseName =
        kind === 'upgrade' ? 'shopping_upgrade_requested' : `shopping_${kind ?? ''}_accepted`;
      let source: unknown = before(caseName);
      await page.route('**/api/battles/active', (route) =>
        route.fulfill({ status: 200, json: source }),
      );
      await open(page, `shopping_live_${kind ?? ''}`, width, false);
      if (kind === 'upgrade') {
        await openUpgradeQueue(page, name ?? '');
        const row = page.locator('.tp-battle__queue ol > li').filter({
          has: page.getByRole('button', { name: `Try once ${name ?? ''}`, exact: true }),
        });
        await expect(row).toHaveCount(1);
        await expect(row.getByText(name ?? '', { exact: true })).toBeVisible();
        await expect(row).toContainText(/Tap cap:|No tap cap/);
      }
      await page.getByRole('button', { name: `Try once ${name ?? ''}`, exact: true }).click();
      const accepted = sample(`shopping_${kind ?? ''}_accepted`);
      await expect(
        page.locator('.tp-battle__command').filter({ hasText: action ?? '' }),
      ).toBeVisible();
      expect(await bodiesTo(page, accepted.request.path)).toEqual([
        expect.objectContaining({
          ...record(accepted.request.body),
          idempotency_key: expect.any(String),
        }),
      ]);
      if (kind === 'upgrade') {
        source = sample('shopping_upgrade_started').body;
        await page.evaluate(() => {
          document.dispatchEvent(new Event('visibilitychange'));
        });
        await expect(
          page.locator('.tp-battle__command').filter({ hasText: 'Started' }),
        ).toBeVisible();
        source = sample('shopping_upgrade_completed_without_purchase').body;
        await page.evaluate(() => {
          document.dispatchEvent(new Event('visibilitychange'));
        });
        await expect(
          page.getByText('Completed; no purchase is implied', { exact: true }),
        ).toBeVisible();
        await expect(
          page.locator('.tp-battle__command').filter({ hasText: 'unknown_wave' }),
        ).toBeVisible();
      }
      expect(await bodiesTo(page, accepted.request.path)).toHaveLength(1);
      await check(page, width, `try-${kind ?? ''}`);
    });
  }
  test(`SQ7 SQ8 gates send nothing at ${String(width)}`, async ({ page }) => {
    await open(page, 'shopping_live_gated', width);
    const save = page
      .locator('.tp-shopping-live')
      .getByRole('button', { name: 'Save queue for this battle' });
    await expect(save).toHaveAttribute('aria-disabled', 'true');
    await save.focus();
    await page.keyboard.press('Enter');
    await page.getByRole('link', { name: 'Try once in Dashboard This battle' }).click();
    const action = page.getByRole('button', { name: 'Try once Shopping', exact: true });
    await expect(action).toHaveAttribute('aria-disabled', 'true');
    await action.focus();
    await page.keyboard.press('Enter');
    expect(await posts(page)).toEqual([]);
    await check(page, width, 'gated');
  });
}
