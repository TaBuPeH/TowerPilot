import { withRecordedFence } from '../support/profilePatchFenced.ts';
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { firstPolicyCase } from '../support/collectionFirstPolicy.ts';
import { axeIds, bodiesTo, posts, WCAG_AA_TAGS } from './helpers/helpers.ts';

const OUT = path.resolve('../../backend/logs/reviews/frontend-f6-screens');
const WIDTHS = [1360, 1054, 390] as const;
mkdirSync(OUT, { recursive: true });

async function open(page: Page, width: number) {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.setViewportSize({ width, height: 900 });
  await page.goto('/__fake/use?scenario=collection_first_policy');
  await page.evaluate(() => {
    window.location.hash = '#collection';
  });
  await expect(page.getByRole('heading', { name: 'Collection', exact: true })).toBeVisible();
  const form = page.locator('[data-form="create"]');
  await expect(form.getByRole('combobox')).toHaveValue('["farm","all_on"]');
  await expect(
    page.getByText('This profile has no gathering policies', { exact: true }),
  ).toBeVisible();
  return { form, errors };
}

async function axe(page: Page) {
  const violations = await axeIds(page, WCAG_AA_TAGS);
  expect(violations).toEqual([]);
}

for (const width of WIDTHS) {
  test(`C-01 C5 F6C-R1-04 create/cancel/reload/taken id, exact patch and unbound readback; axe and targets at ${width}`, async ({
    page,
  }) => {
    const { form, errors } = await open(page, width);
    const id = form.getByRole('textbox', { name: 'New policy identifier' });
    await form.getByRole('combobox').selectOption('["shard","gems_only"]');
    await id.fill('007 bad');
    await expect(id).toHaveAttribute('aria-invalid', 'true');
    await page.reload();
    await expect(id).toHaveValue('007 bad');
    await expect(form.getByRole('combobox')).toHaveValue('["shard","gems_only"]');
    await form.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(id).toHaveValue('');
    await expect(form.getByRole('combobox')).toHaveValue('["farm","all_on"]');
    expect(await posts(page)).toEqual([]);
    await id.fill('farm_default');
    await axe(page);
    const button = form.getByRole('button', { name: 'Save policy' });
    await button.focus();
    await page.keyboard.press('Enter');
    await expect(form.locator('[data-outcome="saved"]')).toBeVisible();
    await expect(page.locator('.tp-collection__editor [data-used-by]')).toContainText(
      'Not used by any run',
    );
    await expect(id).toHaveValue('');
    expect(await bodiesTo(page, '/api/profile-patch')).toEqual([
      withRecordedFence(firstPolicyCase('first_policy_created_from_the_farm_template_default'), {
        instance: 'main',
        serial: 'fake:1',
        account: null,
        profile: 'mine',
      }).request.body,
    ]);
    const readback = await page.request.get('/api/profile-src/mine');
    expect(await readback.json()).toEqual(
      firstPolicyCase('read_after_the_first_policy_holds_it_unbound').body,
    );
    await id.fill('farm_default');
    await expect(id).toHaveAttribute('aria-invalid', 'true');
    await expect(button).toHaveAccessibleDescription('A policy with this identifier exists');
    await button.click({ force: true });
    expect(await bodiesTo(page, '/api/profile-patch')).toHaveLength(1);
    await axe(page);
    const geometry = await page.evaluate(() => {
      const controls = [
        ...document.querySelectorAll<HTMLElement>(
          '[data-form="create"] button, [data-form="create"] select, [data-form="create"] input',
        ),
      ];
      return {
        width: window.innerWidth,
        overflow: document.documentElement.scrollWidth > window.innerWidth,
        small: controls
          .filter((control) => {
            const rect = control.getBoundingClientRect();
            return (
              rect.width < 24 ||
              rect.height < 24 ||
              (window.innerWidth === 390 && control.tagName === 'BUTTON' && rect.height < 44)
            );
          })
          .map((control) => control.outerHTML),
      };
    });
    expect(geometry).toEqual({ width, overflow: false, small: [] });
    await page.locator('[data-region="collectionPolicies"]').screenshot({
      path: path.join(OUT, `collection-first-policy-${width}.png`),
    });
    await page.getByRole('button', { name: 'Read the saved settings again', exact: true }).click();
    expect((await posts(page)).map((entry) => entry.path)).toEqual(['/api/profile-patch']);
    expect(errors).toEqual([]);
  });

  test(`C-01 C5 F6C-R1-04 lost first save stays unknown across reload until positive readback, never resent at ${width}`, async ({
    page,
  }) => {
    const { form, errors } = await open(page, width);
    const oldRead = firstPolicyCase('read_a_profile_with_no_gathering_policies');
    await page.route('**/api/profile-src/mine', async (route) => {
      await route.fulfill({ status: oldRead.status, json: oldRead.body });
    });
    await page.route('**/api/profile-patch', async (route) => {
      // The fake server commits the recorded case, then the caller loses its answer.
      await route.fetch();
      await route.abort('failed');
    });
    await form.getByRole('textbox', { name: 'New policy identifier' }).fill('farm_default');
    await form.getByRole('button', { name: 'Save policy' }).click();
    await expect(form.locator('[data-outcome="unknown"]')).toBeVisible();
    await page.reload();
    await expect(form.locator('[data-outcome="unknown"]')).toBeVisible();
    await expect(form.getByRole('textbox', { name: 'New policy identifier' })).toHaveValue(
      'farm_default',
    );
    await expect(form.getByRole('button', { name: 'Dismiss', exact: true })).toHaveCount(0);
    expect(await bodiesTo(page, '/api/profile-patch')).toHaveLength(1);
    await page.unroute('**/api/profile-src/mine');
    await page.getByRole('button', { name: 'Read the saved settings again', exact: true }).click();
    await expect(form.locator('[data-outcome="applied"]')).toBeVisible();
    await expect(page.locator('.tp-collection__editor [data-used-by]')).toContainText(
      'Not used by any run',
    );
    expect(await bodiesTo(page, '/api/profile-patch')).toHaveLength(1);
    expect(errors).toEqual([]);
  });
}
