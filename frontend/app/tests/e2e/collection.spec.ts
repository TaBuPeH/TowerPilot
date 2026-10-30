import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { axeCounts, bodiesTo, navigateApp, openScenario, posts } from './helpers/helpers.ts';
import { dashboardPagesCase } from '../support/dashboardFixture.ts';
import { own, ownPath, entries } from '../../src/core/read.ts';

/**
 * The Collection page in a real browser on the fake backend (brief
 * backend/logs/reviews/ui-pages/collection-brief.md, C1 to C6): navigation from the
 * Dashboard, the settings and profile saves (one POST per click, never resent), typed
 * refusals that keep the raw text, a lost save, axe, reflow and target sizes at 1360,
 * 1054 and 390, and the screenshots (TP_F6_SCREENS_DIR, else test-results/f6-screens).
 */
const WIDTHS = [1360, 1054, 390] as const;
const OUT = process.env['TP_F6_SCREENS_DIR'] ?? path.resolve('test-results', 'f6-screens');
mkdirSync(OUT, { recursive: true });

async function openCollection(page: Page, scenario: string, width = 1360, hash = '#collection') {
  const problems: string[] = [];
  page.on('pageerror', (error) => {
    problems.push(error.message);
  });
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`/__fake/use?scenario=${scenario}`);
  await page.evaluate((target) => {
    window.location.hash = target;
  }, hash);
  await expect(page.getByRole('heading', { level: 1, name: 'Collection' })).toBeVisible();
  await expect(page.locator('[data-module="rewards"]')).toBeVisible();
  await expect(page.locator('[data-region="collectionChores"] [data-chore]').first()).toBeVisible();
  return problems;
}

const rewards = (page: Page) => page.locator('[data-module="rewards"]');

test('C1 app navigation opens Collection; two reward switches save once by keyboard and read back Saved; nothing else is sent', async ({
  page,
}) => {
  const problems = await openScenario(page, 'collection');
  await navigateApp(page, 'Collection');
  await expect(page).toHaveURL(/#collection$/u);
  const box = rewards(page);
  await expect(box.getByRole('checkbox', { name: 'Daily missions' })).toBeVisible();
  await box.getByRole('checkbox', { name: 'Daily missions' }).focus();
  await page.keyboard.press('Space');
  await box.getByRole('checkbox', { name: 'Free Store gems' }).uncheck();
  expect(await posts(page)).toEqual([]);
  await box.getByRole('button', { name: 'Save', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect.poll(async () => (await bodiesTo(page, '/api/settings')).length).toBe(1);
  expect(await bodiesTo(page, '/api/settings')).toEqual([
    expect.objectContaining({
      module: 'rewards',
      values: { daily_missions: true, free_store_gems: false },
    }),
  ]);
  await expect(box.locator('[data-outcome="saved"]')).toBeVisible();
  await expect(box.getByRole('checkbox', { name: 'Daily missions' })).toBeChecked();
  const quota = page
    .locator('[data-region="collectionRewards"] .tp-facts__item')
    .filter({ has: page.getByText('Daily quota and reset time', { exact: true }) });
  await expect(quota.locator('dt')).toHaveText('Daily quota and reset time');
  await expect(quota.locator('dd')).toContainText('Not reported');
  await box.screenshot({ path: path.join(OUT, 'collection-rewards-saved-1360.png') });
  await page.waitForTimeout(3_000);
  expect((await posts(page)).map((entry) => entry.path)).toEqual(['/api/settings']);
  expect(problems).toEqual([]);
});

test('C1 SR-7 F6C-R1-05 a settings read naming another profile (here the starter, while the config names mine) shows none of its values and saves nothing', async ({
  page,
}) => {
  const problems: string[] = [];
  page.on('pageerror', (error) => {
    problems.push(error.message);
  });
  await page.setViewportSize({ width: 1360, height: 900 });
  await page.goto('/__fake/use?scenario=collection_starter');
  await page.evaluate(() => {
    window.location.hash = '#collection';
  });
  const region = page.locator('[data-region="collectionRewards"]');
  await expect(region).toContainText(
    'The last settings read was for another emulator, account or profile',
  );
  const box = rewards(page);
  await expect(box).toContainText(
    'The last read was for another emulator, account or profile; check again',
  );
  await expect(box.getByRole('checkbox')).toHaveCount(0);
  await region.screenshot({ path: path.join(OUT, 'collection-other-context-1360.png') });
  await page.waitForTimeout(2_000);
  expect(await posts(page)).toEqual([]);
  expect(problems).toEqual([]);
});

test('C1 C2 C5 SR-2 F6C-R1-01 a reload keeps valid, invalid, switch and copy drafts exactly as typed; nothing is sent', async ({
  page,
}) => {
  const problems = await openCollection(page, 'collection');
  const ad = page.locator('[data-module="ad:all_on"]');
  await ad.locator('[data-part="gem_delay_sec#0"]').fill('007');
  await ad.locator('[data-part="gem_delay_sec#1"]').fill('30.5');
  await rewards(page).getByRole('checkbox', { name: 'Event missions' }).check();
  const copy = page.locator('[data-region="collectionPolicies"] [data-form="copy"]');
  await copy.getByRole('textbox', { name: /^New identifier/u }).fill('kept_copy');
  await copy.getByRole('textbox', { name: /^Name shown/u }).fill('Kept label');
  await page.reload();
  await expect(page.locator('[data-module="rewards"]')).toBeVisible();
  await expect(ad.locator('[data-part="gem_delay_sec#0"]')).toHaveValue('007');
  await expect(ad.locator('[data-part="gem_delay_sec#1"]')).toHaveValue('30.5');
  await expect(ad.locator('[data-part="gem_delay_sec#1"]')).toHaveAccessibleDescription(
    'Use a whole number',
  );
  await expect(rewards(page).getByRole('checkbox', { name: 'Event missions' })).toBeChecked();
  await expect(copy.getByRole('textbox', { name: /^New identifier/u })).toHaveValue('kept_copy');
  await expect(copy.getByRole('textbox', { name: /^Name shown/u })).toHaveValue('Kept label');
  await page.locator('[data-region="collectionPolicies"]').screenshot({
    path: path.join(OUT, 'collection-drafts-reloaded-1360.png'),
  });
  expect(await posts(page)).toEqual([]);
  expect(problems).toEqual([]);
});

test('C2 C3 SR-5 a reversed delay, a fractional whole-second bound and an off-screen orbit center stay as typed with their reasons; nothing is sent', async ({
  page,
}) => {
  const problems = await openCollection(page, 'collection');
  const ad = page.locator('[data-module="ad:all_on"]');
  const delayLow = ad.locator('[data-part="gem_delay_sec#0"]');
  await delayLow.fill('20');
  await expect(delayLow).toHaveAccessibleDescription(
    'The first value must not be larger than the second',
  );
  const delayHigh = ad.locator('[data-part="gem_delay_sec#1"]');
  await delayHigh.fill('30.5');
  await expect(delayHigh).toHaveAccessibleDescription('Use a whole number');
  await expect(ad.getByRole('button', { name: 'Save', exact: true })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  const orbit = page.locator('[data-module="orbit:all_on"]');
  await orbit.getByText('Tap geometry (native screen pixels)').click();
  const centerX = orbit.locator('[data-part="gem_orbit.center#0"]');
  await centerX.fill('2600');
  await expect(centerX).toHaveValue('2600');
  await expect(centerX).toHaveAttribute('aria-invalid', 'true');
  await expect(centerX).toHaveAccessibleDescription('Above the highest allowed value');
  await page.locator('[data-region="collectionPolicies"]').screenshot({
    path: path.join(OUT, 'collection-policy-invalid-1360.png'),
  });
  await page.waitForTimeout(2_000);
  expect(await posts(page)).toEqual([]);
  expect(problems).toEqual([]);
});

test('C2 SR-4 a settings save whose answer is lost stays Unknown with no Dismiss; it is never resent', async ({
  page,
}) => {
  const problems = await openCollection(page, 'collection_save_lost');
  const box = rewards(page);
  await box.getByRole('checkbox', { name: 'Event missions' }).check();
  await box.getByRole('button', { name: 'Save', exact: true }).click();
  await expect.poll(async () => (await bodiesTo(page, '/api/settings')).length).toBe(1);
  await expect(box.locator('[data-outcome="unknown"]')).toContainText(
    'No confirmed reply - it may or may not have saved',
  );
  await expect(box.getByRole('button', { name: 'Dismiss' })).toHaveCount(0);
  await box.screenshot({ path: path.join(OUT, 'collection-save-unknown-1360.png') });
  await page.waitForTimeout(3_000);
  expect(await bodiesTo(page, '/api/settings')).toHaveLength(1);
  expect(problems).toEqual([]);
});

test('C5 an in-use policy says why it cannot be deleted; a copy keeps the source fields, sends one patch and reads Saved', async ({
  page,
}) => {
  const problems = await openCollection(
    page,
    'collection_policy_added',
    1360,
    '#collection/policy/gems_only',
  );
  const policies = page.locator('[data-region="collectionPolicies"]');
  await expect(page.locator('#collection-editor-title')).toBeFocused();
  const remove = policies.getByRole('button', { name: 'Delete this policy' });
  await expect(remove).toHaveAttribute('aria-disabled', 'true');
  await expect(remove).toHaveAccessibleDescription(
    'Used by a run; give those runs another policy in Runs first',
  );
  const copy = policies.locator('[data-form="copy"]');
  await copy.getByRole('textbox', { name: /^New identifier/u }).fill('weekend');
  await copy.getByRole('textbox', { name: /^Name shown/u }).fill('Weekend gathering');
  await copy.getByRole('button', { name: 'Save copy' }).dblclick();
  await expect.poll(async () => (await bodiesTo(page, '/api/profile-patch')).length).toBe(1);
  expect(await bodiesTo(page, '/api/profile-patch')).toEqual([
    // Supplementary legacy weekend copy: no fenced recording has this identifier/label.
    // Use the complete recorded source policy and revision, preserving every hidden field.
    {
      name: 'mine',
      expected_context: { instance: 'main', serial: 'fake:1', account: null, profile: 'mine' },
      expected_revision: own(
        dashboardPagesCase('collection_policies.read_before_the_first_save').body,
        'revision',
      ),
      path: ['policies', 'gather', 'weekend'],
      value: {
        ...Object.fromEntries(
          entries(
            ownPath(
              dashboardPagesCase('collection_policies.read_before_the_first_save').body,
              'profile',
              'policies',
              'gather',
              'gems_only',
            ),
          ),
        ),
        label: 'Weekend gathering',
      },
    },
  ]);
  await expect(copy.locator('[data-outcome="saved"]')).toBeVisible();
  await policies.screenshot({ path: path.join(OUT, 'collection-policy-copy-1360.png') });
  await page.waitForTimeout(2_000);
  expect(await bodiesTo(page, '/api/profile-patch')).toHaveLength(1);
  expect(problems).toEqual([]);
});

test('C6 shatter is said to destroy modules; switching quest scan off saves the whole list once and runs nothing', async ({
  page,
}) => {
  const problems = await openCollection(page, 'collection');
  const chores = page.locator('[data-region="collectionChores"]');
  await expect(chores.locator('[data-chore="shatter"]')).toContainText(
    'Destroys modules when it runs',
  );
  await chores.getByRole('checkbox', { name: 'Quest scan' }).uncheck();
  await chores.getByRole('button', { name: 'Save chores' }).click();
  await expect.poll(async () => (await bodiesTo(page, '/api/profile-patch')).length).toBe(1);
  expect(await bodiesTo(page, '/api/profile-patch')).toEqual([
    // Supplementary legacy chore revision; the fenced chore request has its own browser suite.
    {
      name: 'mine',
      expected_revision: own(
        dashboardPagesCase('collection_policies.read_before_the_first_save').body,
        'revision',
      ),
      expected_context: { instance: 'main', serial: 'fake:1', account: null, profile: 'mine' },
      path: ['policies', 'chores'],
      value: [{ name: 'quest_scan', enabled: false }],
    },
  ]);
  await expect(chores.locator('[data-outcome="saved"]')).toBeVisible();
  await chores.screenshot({ path: path.join(OUT, 'collection-chores-saved-1360.png') });
  expect((await posts(page)).map((entry) => entry.path)).toEqual(['/api/profile-patch']);
  expect(problems).toEqual([]);
});

for (const width of WIDTHS) {
  test(`C1-C6 axe, reflow and targets of the Collection page at ${String(width)}, and its screenshot`, async ({
    page,
  }) => {
    const problems = await openCollection(page, 'collection', width);
    await page.waitForTimeout(500);
    expect(await axeCounts(page)).toEqual([]);
    const ad = await page.locator('[data-module="ad:all_on"]').boundingBox();
    const orbit = await page.locator('[data-module="orbit:all_on"]').boundingBox();
    const create = await page.locator('[data-form="create"]').boundingBox();
    const copy = await page.locator('[data-form="copy"]').boundingBox();
    if (ad === null || orbit === null || create === null || copy === null)
      throw new Error('Collection settings family or creation form missing');
    expect(ad.width).toBeCloseTo(orbit.width, 0);
    expect(ad.y + ad.height).toBeLessThanOrEqual(orbit.y);
    expect(create.y + create.height).toBeLessThanOrEqual(copy.y);
    const uses = page.locator('[data-policy="gems_only"] .tp-collection__uses > li');
    await expect(uses).toHaveText(['farm_deep', 'shards']);
    const firstUse = await uses.nth(0).boundingBox();
    const nextUse = await uses.nth(1).boundingBox();
    if (firstUse === null || nextUse === null) throw new Error('Policy run names missing');
    expect(
      nextUse.x >= firstUse.x + firstUse.width || nextUse.y >= firstUse.y + firstUse.height,
    ).toBe(true);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
    const small = await page.evaluate(() =>
      [
        ...document.querySelectorAll<HTMLElement>(
          'main button, main input, main select, main summary',
        ),
      ]
        .filter((element) => element.getClientRects().length > 0)
        .map((element) => ({
          label: element.outerHTML.slice(0, 60),
          box: element.getBoundingClientRect(),
        }))
        .filter((entry) => entry.box.height < 24 || entry.box.width < 24)
        .map((entry) => entry.label),
    );
    expect(small).toEqual([]);
    if (width === 390) {
      const buttons = await page.locator('main button').evaluateAll((all) =>
        all
          .filter((element) => element.getClientRects().length > 0)
          .map((element) => ({
            label: element.textContent,
            height: element.getBoundingClientRect().height,
          }))
          .filter((entry) => entry.height < 44)
          .map((entry) => entry.label),
      );
      expect(buttons).toEqual([]);
    }
    await page.screenshot({
      path: path.join(OUT, `collection-page-${String(width)}.png`),
      fullPage: true,
    });
    expect(await posts(page)).toEqual([]);
    expect(problems).toEqual([]);
  });
}
