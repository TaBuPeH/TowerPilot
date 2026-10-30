import { expect, test } from '@playwright/test';
import { bodiesTo, openScenario, posts } from './helpers/helpers.ts';

/**
 * Spec 9 item 3(d) in a real browser: no write is ever resent on its own
 * (S-010), an unknown outcome survives a reload, and the collection-only
 * handoff asks for confirmation on every attempt (S-101).
 */
test('S-010 a Pause whose answer is lost is sent once: 60 s of polls and a reload send nothing more, and the unknown outcome stays', async ({
  page,
}) => {
  await page.clock.install();
  await openScenario(page, 'dropped_writes');
  await page.locator('[data-slot="gem"]').getByRole('button', { name: 'Pause harvesting' }).click();
  await expect.poll(async () => (await bodiesTo(page, '/api/harvest')).length).toBe(1);
  const outcomes = page.locator('.tp-outcomes li');
  await expect(outcomes.first()).toBeVisible();
  for (let second = 0; second < 60; second += 5) await page.clock.runFor(5_000);
  expect(await bodiesTo(page, '/api/harvest')).toHaveLength(1);
  const before = (await posts(page)).length;

  await page.reload();
  await expect(page.getByRole('heading', { level: 2, name: 'Control' })).toBeVisible();
  await expect(page.locator('.tp-outcomes li').first()).toBeVisible();
  for (let second = 0; second < 20; second += 5) await page.clock.runFor(5_000);
  expect(await posts(page)).toHaveLength(before);
  expect(await bodiesTo(page, '/api/harvest')).toHaveLength(1);
});

test('S-010 Turn input off whose answer is lost is sent once and never again on its own', async ({
  page,
}) => {
  await page.clock.install();
  await openScenario(page, 'dropped_writes');
  await page.locator('[data-slot="input"]').getByRole('button', { name: 'Turn input off' }).click();
  await expect
    .poll(async () => bodiesTo(page, '/api/automation-access'))
    .toEqual([{ enabled: false }]);
  for (let second = 0; second < 60; second += 5) await page.clock.runFor(5_000);
  expect(await bodiesTo(page, '/api/automation-access')).toEqual([{ enabled: false }]);
});

test('S-101 the collection-only handoff opens its dialog on every attempt; Cancel sends nothing; Confirm sends exactly one', async ({
  page,
}) => {
  await openScenario(page, 'setup_guard_block');
  const confirmItem = page
    .locator('[data-region="needsYou"]')
    .getByRole('button', { name: /^Confirm a new manual battle/u });
  const dialog = page.locator('dialog[open]');
  for (let attempt = 0; attempt < 2; attempt += 1) {
    await confirmItem.click();
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toHaveCount(0);
  }
  await page.keyboard.press('Escape');
  expect(await bodiesTo(page, '/api/setup-battle/collection-handoff')).toEqual([]);
  await confirmItem.click();
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: /^Confirm/u }).click();
  await expect
    .poll(async () => (await bodiesTo(page, '/api/setup-battle/collection-handoff')).length)
    .toBe(1);
  // The remembered consent of an earlier attempt never sends again.
  await page.waitForTimeout(3_000);
  expect(await bodiesTo(page, '/api/setup-battle/collection-handoff')).toHaveLength(1);
});
