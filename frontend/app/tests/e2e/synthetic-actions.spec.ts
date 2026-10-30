import { expect, test, type Locator, type Page } from '@playwright/test';
import { copy } from '../../src/copy/catalogue.ts';
import { ownPath } from '../../src/core/read.ts';
import {
  axeCounts,
  expectAppStyles,
  openScenario,
  posts,
  WCAG_AA_TAGS,
} from './helpers/helpers.ts';
import { syntheticCase } from '../support/synthetic-action-fixtures.mjs';

const WIDTHS = [1360, 1054, 390] as const;
const battle = (page: Page) => page.locator('[data-region="thisBattle"]');
const live = (page: Page) => page.locator('[data-region="runsLive"]');
const run = (page: Page) =>
  battle(page).getByRole('button', { name: 'Run now Synthetic probe', exact: true }).first();

/** A keyboard activation starts with focus and dispatches Enter through the browser. */
async function enter(control: Locator) {
  await control.focus();
  await expect(control).toBeFocused();
  await control.press('Enter');
}

async function open(page: Page, scenario: string, width: number, key: string) {
  await page.addInitScript((value) => {
    // Only the idempotency source is deterministic; no command, parser or store is replaced.
    Object.defineProperty(crypto, 'randomUUID', { value: () => value });
  }, key);
  const config = page.waitForResponse(
    (response) => new URL(response.url()).pathname === '/api/config',
  );
  const problems = await openScenario(page, scenario, { width });
  expect((await config).status()).toBe(200);
  await expect
    .poll(() => page.evaluate(() => sessionStorage.getItem('tp.context.v1')))
    .not.toBeNull();
  // Request STARTED after the confirmed context was stored, as AC2's startup fence does.
  const reading = await page.waitForRequest(
    (request) =>
      request.method() === 'GET' && new URL(request.url()).pathname === '/api/battles/active',
  );
  const answer = await reading.response();
  expect(answer?.status()).toBe(200);
  await answer?.finished();
  await expect(
    battle(page).getByRole('button', { name: 'Run now Synthetic probe', exact: true }),
  ).toHaveCount(2);
  await expectAppStyles(page);
  return problems;
}

async function check(page: Page) {
  await expectAppStyles(page);
  expect(await axeCounts(page, WCAG_AA_TAGS)).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBeLessThanOrEqual(0);
}

async function readState(page: Page, scenario: string, region: Locator) {
  expect((await page.request.get(`/__fake/set?scenario=${scenario}`)).status()).toBe(200);
  const isDashboard = (await region.getAttribute('data-region')) === 'thisBattle';
  if (isDashboard) await enter(battle(page).locator('[data-focus-key="battle-edit"]').first());
  const response = page.waitForResponse(
    (answer) => new URL(answer.url()).pathname === '/api/battles/active',
  );
  await enter(live(page).getByRole('button', { name: 'Check this battle', exact: true }).first());
  expect((await response).status()).toBe(200);
  if (isDashboard) {
    const navigation = page.getByRole('navigation', { name: 'Navigation', includeHidden: true });
    if (!(await navigation.isVisible())) {
      await enter(page.getByRole('button', { name: 'Navigation', exact: true }));
    }
    await enter(navigation.getByRole('link', { name: 'Dashboard', exact: true }));
  }
}

async function exactPost(page: Page, control: Locator, name: string) {
  const recorded = syntheticCase(name);
  const response = page.waitForResponse(
    (answer) =>
      new URL(answer.url()).pathname === recorded.request.path &&
      answer.request().method() === 'POST',
  );
  await enter(control);
  const answer = await response;
  expect(answer.status()).toBe(recorded.status);
  expect(answer.request().postDataJSON()).toEqual(recorded.request.body);
  // B1 records method/path/body, not headers; these are fetchTransport's public JSON contract.
  expect(answer.request().headers()).toMatchObject({
    accept: 'application/json',
    'content-type': 'application/json',
  });
  expect(
    (await posts(page)).map(({ method, path, body }) => ({
      method,
      path,
      body: JSON.parse(body) as unknown,
    })),
  ).toEqual([recorded.request]);
}

for (const width of WIDTHS) {
  test(`AC7 live field exact fence, receipt/read settlement, keyboard and axe at ${String(width)}`, async ({
    page,
  }) => {
    const problems = await open(page, 'a2_7_b1_live', width, 'burst-1');
    await check(page);
    await enter(battle(page).locator('[data-focus-key="battle-edit"]').first());
    const input = live(page)
      .getByRole('textbox', { name: 'Synthetic probe Burst', exact: true })
      .first();
    await input.focus();
    await input.press('ControlOrMeta+A');
    await input.press('7');
    await expect(input).toHaveValue('7');
    const save = live(page).getByRole('button', {
      name: 'Save changes to this battle',
      exact: true,
    });
    await expect(save).not.toHaveAttribute('aria-disabled', 'true');
    await exactPost(page, save, 'synthetic_live_field_accepted');
    await expect(
      live(page).getByText('Sent to this battle - waiting to apply', { exact: true }),
    ).toBeVisible();
    await readState(page, 'a2_7_b1_applied', live(page));
    await expect(live(page).getByText('Applied to this battle', { exact: true })).toBeVisible();
    await expect(input).toHaveValue('7');
    await check(page);
    expect(await posts(page)).toHaveLength(1);
    expect(problems).toEqual([]);
  });

  test(`AC7 Run now exact fence, receipt/started/completed settlement, keyboard and axe at ${String(width)}`, async ({
    page,
  }) => {
    const problems = await open(page, 'a2_7_b1_run', width, 'run-1');
    await expect(run(page)).not.toHaveAttribute('aria-disabled', 'true');
    await exactPost(page, run(page), 'synthetic_run_now_accepted');
    const command = battle(page)
      .locator('.tp-battle__command')
      .filter({
        hasText: String(
          ownPath(syntheticCase('synthetic_run_now_accepted').request.body, 'action'),
        ),
      });
    await expect(command).toContainText('sent; waiting for the automation to pick it up');
    await readState(page, 'a2_7_b1_started', battle(page));
    await expect(command).toContainText('started; waiting for its result');
    await readState(page, 'a2_7_b1_completed', battle(page));
    await expect(command).toContainText('Done');
    await expect(command).toContainText('Finished, effect not confirmed');
    await check(page);
    expect(await posts(page)).toHaveLength(1);
    expect(problems).toEqual([]);
  });

  for (const [scenario, reason] of [
    ['a2_7_b1_stale', 'action_context_mismatch'],
    ['a2_7_b1_newer', 'action_schema_newer'],
  ] as const) {
    test(`AC7 ${reason} refuses both controls, keyboard and axe at ${String(width)}`, async ({
      page,
    }) => {
      const problems = await open(page, scenario, width, 'never-sent');
      await expect(run(page)).toHaveAttribute('aria-disabled', 'true');
      await expect(run(page)).toHaveAccessibleDescription(copy.actions.blocked[reason].short);
      await enter(run(page));
      await check(page);
      await enter(battle(page).locator('[data-focus-key="battle-edit"]').first());
      const input = live(page)
        .getByRole('textbox', { name: 'Synthetic probe Burst', exact: true })
        .first();
      await input.focus();
      await input.press('ControlOrMeta+A');
      await input.press('7');
      await expect(input).toHaveValue('7');
      const save = live(page).getByRole('button', {
        name: 'Save changes to this battle',
        exact: true,
      });
      await expect(save).toHaveAttribute('aria-disabled', 'true');
      await expect(save).toHaveAccessibleDescription(copy.actions.blocked[reason].short);
      await enter(save);
      await check(page);
      expect(await posts(page)).toEqual([]);
      expect(problems).toEqual([]);
    });
  }

  test(`AC7 unknown reply refuses further Run now until matching read, keyboard and axe at ${String(width)}`, async ({
    page,
  }) => {
    const problems = await open(page, 'a2_7_b1_unknown', width, 'run-5');
    await expect(run(page)).not.toHaveAttribute('aria-disabled', 'true');
    await exactPost(page, run(page), 'synthetic_run_now_reply_unknown');
    const outcome = battle(page).locator('[data-battle-outcome]');
    await expect(outcome).toContainText('No reply — outcome unknown');
    await expect(run(page)).toHaveAttribute('aria-disabled', 'true');
    await enter(run(page));
    await check(page);
    expect(await posts(page)).toHaveLength(1);
    await readState(page, 'a2_7_b1_unknown_read', battle(page));
    await expect(outcome).toHaveCount(0);
    await expect(
      battle(page)
        .locator('.tp-battle__command')
        .filter({
          hasText: String(
            ownPath(syntheticCase('synthetic_run_now_reply_unknown').request.body, 'action'),
          ),
        }),
    ).toContainText('sent; waiting for the automation to pick it up');
    expect(await posts(page)).toHaveLength(1);
    expect(problems).toEqual([]);
  });
}
