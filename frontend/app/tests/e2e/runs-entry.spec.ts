import { withRecordedFence, fencedRuntime } from '../support/profilePatchFenced.ts';
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { axeIds, bodiesTo, posts, WCAG_AA_TAGS } from './helpers/helpers.ts';
import { own } from '../../src/core/read.ts';
import { dashboardPagesCase } from '../support/dashboardFixture.ts';

const OUT = process.env['TP_F6_SCREENS_DIR'] ?? path.resolve('test-results', 'f6-screens');
mkdirSync(OUT, { recursive: true });
async function check(page: Page, width: number) {
  const violations = await axeIds(page, WCAG_AA_TAGS);
  expect(violations).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBe(0);
  expect(
    await page.locator('[data-run-entry]').evaluate(
      (root, mobile) =>
        [...root.querySelectorAll<HTMLElement>('button,input,select,textarea,a')]
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
}
for (const width of [1360, 1054, 390]) {
  test(`RL3 RL6 saved entry at ${width}: one field, links, axe, reflow and targets`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/__fake/use?scenario=runs_entry_saved');
    await page.evaluate(() => {
      window.location.hash = '#runs/saved/farm';
    });
    const editor = page.locator('[data-run-entry]');
    const row = editor.locator('[data-entry-field="label"]');
    await row.getByLabel('Run label', { exact: true }).fill('Daily coin farm');
    await row.getByRole('button', { name: 'Save field', exact: true }).focus();
    await page.keyboard.press('Enter');
    await expect(row.locator('[data-outcome="saved"]')).toBeVisible();
    await expect(row.getByRole('button', { name: 'Save field', exact: true })).toBeFocused();
    expect(await bodiesTo(page, '/api/profile-patch')).toEqual([
      // Supplementary legacy label/value/revision: the fenced recording uses Offline farm.
      // Exact new-family writes at every width live in profile-patch-fenced.spec.ts.
      withRecordedFence(dashboardPagesCase('run_entry_patch.saved_label'), fencedRuntime).request
        .body,
    ]);
    await expect(editor.locator('[data-entry-field="policies.gather"] a')).toHaveAttribute(
      'href',
      '#collection/policy/gems_only',
    );
    await expect(editor.locator('[data-entry-field="policies.rescue"] a')).toHaveAttribute(
      'href',
      '#abilities/rescue_policies/wall_drain',
    );
    await check(page, width);
    await editor.screenshot({ path: path.join(OUT, `runs-entry-saved-${width}.png`) });
    await editor.locator('[data-entry-field="policies.gather"] a').click();
    await expect(page).toHaveURL(/#collection\/policy\/gems_only$/u);
    await expect(page.getByRole('heading', { level: 1, name: 'Collection' })).toBeVisible();
    expect(await posts(page)).toHaveLength(1);
  });
  test(`RL3 refusal at ${width}: draft, server text, axe and screenshot`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/__fake/use?scenario=runs_entry_refused');
    await page.evaluate(() => {
      window.location.hash = '#runs/saved/farm';
    });
    const editor = page.locator('[data-run-entry]');
    const row = editor.locator('[data-entry-field="tier"]');
    await row.getByLabel('Run tier', { exact: true }).fill('3');
    await row.getByRole('button', { name: 'Save field', exact: true }).click();
    await expect(row.locator('[data-outcome="refused"]')).toBeVisible();
    await expect(row.getByLabel('Run tier', { exact: true })).toHaveValue('3');
    await expect(row.locator('[data-refusal]')).toHaveText(
      String(own(dashboardPagesCase('run_entry_patch.unknown_profile_404').body, 'error')),
    );
    await check(page, width);
    await editor.screenshot({ path: path.join(OUT, `runs-entry-refused-${width}.png`) });
    expect(await posts(page)).toHaveLength(1);
  });
}

for (const width of [1360, 1054, 390]) {
  test(`RL13 F6-11-P3-03 quest default has a visible accessible reason at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const source = dashboardPagesCase(
      'run_entry_patch_parity.read_after_the_limit_refusals_nothing_changed',
    );
    await page.route('**/api/profile-src/mine', async (route) => {
      await route.fulfill({ status: source.status, json: source.body });
    });
    await page.goto('/__fake/use?scenario=runs_entry_saved');
    await page.evaluate(() => {
      window.location.hash = '#runs/saved/quest_sm';
    });
    const editor = page.locator('[data-run-entry]');
    const action = editor
      .locator('[data-entry-field="rides"]')
      .getByRole('button', { name: 'Use the quest default' });
    await expect(action).toHaveAttribute('aria-disabled', 'true');
    const reasonId = await action.getAttribute('aria-describedby');
    expect(reasonId).toBeTruthy();
    const reason = page.locator(`[id="${reasonId}"]`);
    await expect(reason).toHaveCount(1);
    await expect(reason).toBeVisible();
    await expect(reason).toHaveText('This quest already uses its default.');
    await check(page, width);
    await editor.screenshot({ path: path.join(OUT, `runs-entry-quest-default-${width}.png`) });
    expect(await posts(page)).toHaveLength(0);
  });
}
