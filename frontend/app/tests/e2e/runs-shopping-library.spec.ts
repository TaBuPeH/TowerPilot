import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Locator, type Page } from '@playwright/test';
import { entries, ownPath } from '../../src/core/read.ts';
import { dashboardPagesCase } from '../support/dashboardFixture.ts';
import { axeViolations, bodiesTo, posts, WCAG_AA_TAGS } from './helpers/helpers.ts';

const OUT = process.env['TP_F6_SCREENS_DIR'] ?? path.resolve('test-results', 'f6-screens');
mkdirSync(OUT, { recursive: true });
const sample = (name: string) => dashboardPagesCase(`shopping_queue.${name}`);
const library = (page: Page) => page.locator('[data-shopping-library]');
const policy = (page: Page, id: string) => library(page).locator(`[data-library-policy="${id}"]`);
async function open(page: Page, scenario: string, width: number) {
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`/__fake/use?scenario=shopping_library_${scenario}`);
  await page.evaluate(() => {
    window.location.hash = '#runs/saved/farm';
  });
  await expect
    .poll(() => page.evaluate(() => sessionStorage.getItem('tp.context.v1')))
    .not.toBeNull();
  await library(page).getByText('Manage policies', { exact: true }).click();
  await expect(policy(page, 'main_list')).toBeVisible();
}
async function edit(page: Page, id = 'main_list') {
  const form = policy(page, id);
  await form.getByText('Edit shared policy', { exact: true }).click();
  return form;
}
async function weighted(page: Page) {
  const form = await edit(page);
  await form.getByLabel('Cash reserve', { exact: true }).fill('750.5');
  await form.getByLabel('Purchase pattern', { exact: true }).selectOption('weighted_split');
  await form.getByLabel('Cadence', { exact: true }).selectOption('while_affordable');
  const health = form.locator('li[data-upgrade="health"]');
  await health.getByLabel('Weight', { exact: true }).fill('3');
  await health.getByLabel('Until wave (exclusive)', { exact: true }).fill('20');
  await form
    .locator('li[data-upgrade="damage"]')
    .getByLabel('Tap attempts this battle', { exact: true })
    .fill('');
  const up = health.getByRole('button', { name: /Move up/ });
  await up.focus();
  await page.keyboard.press('Enter');
  await expect(up).toBeFocused();
  return form;
}
async function check(page: Page, width: number, state: string, box: Locator = library(page)) {
  const violations = (await axeViolations(page, WCAG_AA_TAGS)).map((item) => ({
    id: item.id,
    targets: item.nodes.map((node) => node.target),
  }));
  expect(
    violations,
    JSON.stringify(
      await page.locator('li:nth-child(1) > a[href$="#runs/saved/farm"]').evaluateAll((elements) =>
        elements.map((element) => ({
          html: element.outerHTML,
          parent: element.parentElement?.parentElement?.outerHTML.slice(0, 300),
          library: element.closest('[data-shopping-library]') !== null,
          width: element.getBoundingClientRect().width,
          height: element.getBoundingClientRect().height,
        })),
      ),
    ),
  ).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBe(0);
  expect(
    await box.evaluate(
      (root, mobile) =>
        [...root.querySelectorAll<HTMLElement>('button,input,select,textarea,a,summary')]
          .filter((element) => element.getClientRects().length > 0)
          .filter((element) => {
            const rect = element.getBoundingClientRect();
            return (
              rect.width < 24 || rect.height < (mobile && element.tagName === 'BUTTON' ? 44 : 24)
            );
          })
          .map((element) => element.outerHTML),
      width === 390,
    ),
  ).toEqual([]);
  await box.screenshot({ path: path.join(OUT, `shopping-library-${state}-${width}.png`) });
}

for (const width of [1360, 1054, 390]) {
  test(`SQ9 create a shared v2 queue at ${width}: explicit reserve and one fenced save`, async ({
    page,
  }) => {
    await open(page, 'create', width);
    await library(page).getByRole('button', { name: 'Create a shared queue' }).click();
    const form = library(page).locator('[data-library-create]');
    await form.getByLabel('New policy ID').fill('new_queue');
    await expect(form.getByRole('button', { name: 'Save shared policy' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    await form.getByLabel('Cash reserve', { exact: true }).fill('0');
    await form.getByLabel('Purchase pattern', { exact: true }).selectOption('highest_affordable');
    await form.getByLabel('Cadence', { exact: true }).selectOption('single_attempt');
    expect(await posts(page)).toHaveLength(0);
    await check(page, width, 'create');
    await form.getByRole('button', { name: 'Save shared policy' }).click();
    await expect(library(page).locator('[data-outcome="saved"]')).toBeVisible();
    expect(await bodiesTo(page, '/api/profile-patch')).toEqual([
      sample('shopping_policy_created').request.body,
    ]);
  });

  test(`SQ9 copy a policy at ${width}: independent copy, bindings unchanged`, async ({ page }) => {
    await open(page, 'copy', width);
    await policy(page, 'main_list').getByRole('button', { name: 'Copy policy' }).click();
    const form = library(page).locator('[data-library-create]');
    await form.getByLabel('New policy ID').fill('main_list_copy');
    await form.getByRole('button', { name: 'Save shared policy' }).click();
    await expect(library(page).locator('[data-outcome="saved"]')).toBeVisible();
    expect(await bodiesTo(page, '/api/profile-patch')).toEqual([
      sample('shopping_policy_copied').request.body,
    ]);
    await check(page, width, 'copy');
  });

  test(`SQ9 edit v2 and reorder by keyboard at ${width}: exact full replacement`, async ({
    page,
  }) => {
    await open(page, 'v2', width);
    const form = await weighted(page);
    await expect(form.getByRole('link', { name: 'farm', exact: true })).toBeVisible();
    await expect(form.getByRole('link', { name: 'other', exact: true })).toBeVisible();
    await check(page, width, 'v2');
    await form.getByRole('button', { name: 'Save shared policy' }).click();
    await expect(form.locator('[data-outcome="saved"]')).toBeVisible();
    expect(await bodiesTo(page, '/api/profile-patch')).toEqual([
      sample('shopping_shared_v2_updated').request.body,
    ]);
  });

  test(`SQ10 legacy clicks refusal at ${width}: raw values, warning and retained order`, async ({
    page,
  }) => {
    await open(page, 'legacy', width);
    const form = await edit(page, 'legacy');
    await expect(form.getByText('Legacy shopping has no cash reserve protection.')).toBeVisible();
    await form.getByLabel('Policy enabled').uncheck();
    await form.getByLabel('Policy enabled').check();
    await form.getByLabel('Clicks', { exact: true }).fill('bad');
    await form.getByRole('button', { name: 'Save shared policy' }).focus();
    await page.keyboard.press('Enter');
    expect(await posts(page)).toHaveLength(0);
    await form.getByLabel('Clicks', { exact: true }).fill('0');
    await form.getByRole('button', { name: 'Save shared policy' }).click();
    await expect(form.locator('[data-outcome="refused"]')).toBeVisible();
    await expect(form.getByLabel('Clicks', { exact: true })).toHaveValue('0');
    expect(await bodiesTo(page, '/api/profile-patch')).toEqual([
      sample('shopping_legacy_clicks_invalid').request.body,
    ]);
    await form.getByRole('button', { name: 'Add directive' }).click();
    const added = form.locator('[data-library-directive="new:0"]');
    const up = added.getByRole('button', { name: 'Up', exact: true });
    await up.focus();
    await page.keyboard.press('Enter');
    await expect(up).toBeFocused();
    await added.getByRole('button', { name: 'Remove directive' }).click();
    await check(page, width, 'legacy-refused');
  });

  test(`SQ9 delete unused at ${width}: used deletion disabled, confirmation required`, async ({
    page,
  }) => {
    await open(page, 'delete', width);
    const used = policy(page, 'main_list').getByRole('button', {
      name: 'Delete policy',
      exact: true,
    });
    await expect(used).toHaveAttribute('aria-disabled', 'true');
    await used.focus();
    await page.keyboard.press('Enter');
    const form = policy(page, 'unused');
    await form.getByRole('button', { name: 'Delete policy', exact: true }).click();
    expect(await posts(page)).toHaveLength(0);
    await check(page, width, 'delete-confirm');
    await form.getByRole('button', { name: 'Confirm policy deletion' }).click();
    await expect
      .poll(async () => bodiesTo(page, '/api/profile-patch'))
      .toEqual([sample('shopping_policy_deleted_unused').request.body]);
    await expect(policy(page, 'unused')).toHaveCount(0);
  });

  test(`SQ9 Unknown save at ${width}: reload retains draft and positive read settles without resend`, async ({
    page,
  }) => {
    await open(page, 'unknown', width);
    const form = await weighted(page);
    await form.getByRole('button', { name: 'Save shared policy' }).click();
    await expect(form.locator('[data-outcome="unknown"]')).toBeVisible();
    await page.reload();
    await library(page).getByText('Manage policies', { exact: true }).click();
    const restored = await edit(page);
    await expect(restored.getByLabel('Cash reserve', { exact: true })).toHaveValue('750.5');
    await expect(restored.locator('[data-outcome="unknown"]')).toBeVisible();
    await check(page, width, 'unknown');
    await page.request.get('/__fake/set?scenario=shopping_library_readback');
    await library(page).getByRole('button', { name: 'Check shared policies' }).click();
    await expect(restored.locator('[data-outcome="applied"]')).toBeVisible();
    expect(await bodiesTo(page, '/api/profile-patch')).toEqual([
      sample('shopping_shared_reply_failed').request.body,
    ]);
    await check(page, width, 'unknown-settled');
  });

  test(`SQ9 context changed at ${width}: click-time fence sends nothing`, async ({ page }) => {
    await open(page, 'context', width);
    const form = await edit(page);
    await form.getByLabel('Cash reserve', { exact: true }).fill('751');
    // Frontend race before the write: change only the recorded config's account binding.
    const config = dashboardPagesCase('config_state.read_before_the_save').body;
    const switched = {
      ...Object.fromEntries(entries(config)),
      instances: {
        main: {
          ...Object.fromEntries(entries(ownPath(config, 'instances', 'main'))),
          account: ownPath(
            sample('shopping_shared_context_changed').body,
            'current_context',
            'account',
          ),
        },
      },
    };
    await page.route('**/api/config', (route) => route.fulfill({ status: 200, json: switched }));
    await form.getByRole('button', { name: 'Save shared policy' }).click();
    await expect(
      page.getByText(/context changed|account or profile changed/i).first(),
    ).toBeVisible();
    expect(await bodiesTo(page, '/api/profile-patch')).toEqual([]);
    await check(page, width, 'context-changed');
  });
}
