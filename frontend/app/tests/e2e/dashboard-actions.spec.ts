import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { axeCounts, expectAppStyles, openScenario, posts } from './helpers/helpers.ts';

/**
 * A2-7 AC1, AC3 and AC5 in a real browser against the fake backend: This battle rendered from the
 * landed r17b's `runtime.actions` recordings (tests/support/fake-backend/scenarios.mjs `a2_7_actions_*`). Each state
 * at 1360, 1054 and 390: axe clean, targets at least 24 px (Run now 44 px at 390), no horizontal
 * page scroll, and a screenshot `a2-7-dashboard-<state>-<width>.png` written to
 * TP_F6_SCREENS_DIR when set, else under test-results/.
 */
const OUT = process.env['TP_F6_SCREENS_DIR'] ?? path.resolve('test-results', 'screens');
mkdirSync(OUT, { recursive: true });
const WIDTHS = [1360, 1054, 390] as const;
const STATES = ['idle', 'finished', 'running', 'run_now', 'stale', 'newer'] as const;

function region(page: Page) {
  return page.locator('[data-region="thisBattle"]');
}

async function openState(page: Page, state: string, width: number): Promise<string[]> {
  const problems = await openScenario(page, `a2_7_actions_${state}`, { width });
  await expect(region(page).locator('.tp-battle__routine')).toHaveCount(3);
  await expectAppStyles(page);
  return problems;
}

for (const state of STATES) {
  for (const width of WIDTHS) {
    test(`AC1 AC3 AC5 ${state} at ${String(width)}: axe clean, targets sized, no page scroll, screenshot`, async ({
      page,
    }) => {
      const problems = await openState(page, state, width);
      // Open each Settings disclosure so the shot and the checks include the descriptor fields.
      for (const summary of await region(page).getByText('Settings', { exact: true }).all())
        await summary.click();
      expect(await axeCounts(page)).toEqual([]);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(0);
      for (const button of await region(page)
        .getByRole('button', { name: /^Run now/u })
        .all()) {
        const box = await button.boundingBox();
        expect(box?.height ?? 0).toBeGreaterThanOrEqual(width === 390 ? 44 : 24);
        expect(box?.width ?? 0).toBeGreaterThanOrEqual(24);
      }
      await region(page).screenshot({
        path: path.join(OUT, `a2-7-dashboard-${state}-${String(width)}.png`),
      });
      expect(problems).toEqual([]);
    });
  }
}

test('AC1 idle: rows in server order with the blocker, Due and the countdown', async ({ page }) => {
  await openState(page, 'idle', 1360);
  await expect(region(page).locator('.tp-battle__name')).toHaveText([
    'Ad gems',
    'Floating gems',
    'Shopping',
  ]);
  const rows = region(page).locator('.tp-battle__routine');
  await expect(rows.nth(0)).toContainText('Not seen on screen yet');
  await expect(rows.nth(1).locator('.tp-battle__next')).toContainText('Due');
  await expect(rows.nth(2).locator('.tp-battle__next')).toContainText(/in \d+ s/u);
});

test('AC3 Run now sends the item key once, with the battle fence', async ({ page }) => {
  await openState(page, 'idle', 1360);
  await region(page).getByRole('button', { name: 'Run now Floating gems' }).click();
  await expect
    .poll(async () => (await posts(page)).filter((entry) => entry.path.endsWith('/actions')))
    .toHaveLength(1);
  const [sent] = (await posts(page)).filter((entry) => entry.path.endsWith('/actions'));
  expect(JSON.parse(sent?.body ?? '{}')).toMatchObject({
    action: 'floating_gems',
    expected_revision: 0,
  });
});

test('AC3 a completed Run now shows the outcome of the execution it started', async ({ page }) => {
  await openState(page, 'run_now', 1360);
  const command = region(page).locator('.tp-battle__command').first();
  await expect(command).toContainText('Done');
  await expect(command).toContainText('Offer gone after tap');
  await expect(command).not.toContainText('Effect confirmed');
  await expect(command).not.toContainText('Claimed');
});

test('AC5 stale readings: last known on every row, no Due, Run now off with one reason', async ({
  page,
}) => {
  await openState(page, 'stale', 1360);
  await expect(region(page).getByText(/^Last known, \d+ s ago$/u)).toHaveCount(3);
  await expect(region(page).getByText('Due', { exact: true })).toHaveCount(0);
  await expect(
    region(page).getByText(
      'The action readings are old; Run now and changes wait for a current reading',
    ),
  ).toHaveCount(1);
  const runNow = region(page).getByRole('button', { name: /^Run now/u });
  for (const button of await runNow.all()) {
    await expect(button).toHaveAttribute('aria-disabled', 'true');
    await expect(button).toHaveAttribute('aria-describedby', 'battle-run-now-reason');
  }
  // A disabled Run now sends nothing even when its click event fires.
  await runNow.first().dispatchEvent('click');
  await region(page).getByRole('button', { name: 'Check this battle' }).click();
  expect((await posts(page)).filter((entry) => entry.path.endsWith('/actions'))).toEqual([]);
});
