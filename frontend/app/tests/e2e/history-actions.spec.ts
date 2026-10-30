import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import { expect, test, type Locator, type Page } from '@playwright/test';
import { axeIds, expectAppStyles, openScenario, posts, WCAG_AA_TAGS } from './helpers/helpers.ts';

/**
 * A2-7 AC4: "Actions in this battle" in the History detail at 1360, 1054 and 390, served by the
 * fake backend from the landed r17b recordings (tests/support/history-fixtures.mjs actionsCase). The
 * "mixed" screen joins rows of several recordings (see mixedDetail there). Screenshots land in
 * TP_F6_SCREENS_DIR as a2-7-history-<state>-<width>.png.
 */
const WIDTHS = [1360, 1054, 390] as const;
const SCREENS =
  process.env['TP_F6_SCREENS_DIR'] === undefined
    ? new URL('../../../../backend/logs/reviews/frontend-f6-screens/', import.meta.url)
    : pathToFileURL(path.resolve(process.env['TP_F6_SCREENS_DIR']) + path.sep);
const STARTED = 'action_executions.battle_detail_of_a_started_battle_lists_its_executions';
const TRUNCATED = 'action_executions_more.battle_detail_truncated_at_fifty';
const CURSOR = '1800000110.0:00000000000000000000000000000065';

async function open(page: Page, width: number, name: string) {
  await openScenario(page, 'idle', { width });
  await page.request.get(`/__fake/history?case=${encodeURIComponent(`actions:${name}`)}`);
  await page.getByRole('main').getByRole('link', { name: 'Run history', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Run history', exact: true })).toBeVisible();
  const row = page.locator('.tp-history__record');
  await expect(row).toHaveCount(1);
  await row.click();
  const section = page.getByRole('region', { name: 'Actions in this battle' });
  await expect(section).toBeVisible();
  await expectAppStyles(page);
  return section;
}

async function screenshot(page: Page, section: Locator, state: string, width: number) {
  await section.scrollIntoViewIfNeeded();
  const file = fileURLToPath(new URL(`a2-7-history-${state}-${String(width)}.png`, SCREENS));
  await page.screenshot({ path: file, fullPage: true });
  return file;
}

async function accessible(page: Page, width: number) {
  expect(await axeIds(page, WCAG_AA_TAGS)).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const small = await page
    .locator('main button:visible, main select:visible, main summary:visible, main a:visible')
    .evaluateAll(
      (elements, wide) =>
        elements
          .filter((element) => {
            const box = element.getBoundingClientRect();
            return (
              box.width < 24 ||
              box.height < (wide === 390 && element.tagName === 'BUTTON' ? 44 : 24)
            );
          })
          .map((element) => `${element.tagName}: ${element.textContent}`),
      width,
    );
  expect(small).toEqual([]);
}

const hashes: Record<string, string> = {};
test.afterAll(() => {
  // The screenshots' SHA-256 for the report; printed, never asserted.
  console.log(`AC4-SCREENSHOTS ${JSON.stringify(hashes)}`);
});
function remember(file: string) {
  hashes[path.basename(file)] = createHash('sha256').update(readFileSync(file)).digest('hex');
}

for (const width of WIDTHS) {
  test(`AC4 started battle: rows newest first with trigger, state, effect and duration at ${String(width)}`, async ({
    page,
  }) => {
    const section = await open(page, width, STARTED);
    const items = section.getByRole('listitem');
    await expect(items).toHaveCount(2);
    await expect(items.nth(0)).toContainText('Floating gems');
    await expect(items.nth(0)).toContainText('Checked, nothing to do');
    await expect(items.nth(0)).toContainText('No game effect');
    await expect(items.nth(1)).toContainText('Ad gems');
    await expect(items.nth(1)).toContainText('Succeeded');
    await expect(items.nth(1)).toContainText('Offer gone after tap');
    await expect(items.nth(1)).toContainText('4 s');
    await accessible(page, width);
    remember(await screenshot(page, section, 'started', width));
    expect(await posts(page)).toEqual([]);
  });

  test(`AC4 adopted battle says its coverage starts at the adoption at ${String(width)}`, async ({
    page,
  }) => {
    const section = await open(
      page,
      width,
      'action_executions.battle_detail_of_an_adopted_battle_lists_its_executions_covered_since_the_adoption',
    );
    await expect(section.getByText('Since this automation adopted the battle')).toHaveCount(2);
    await accessible(page, width);
    remember(await screenshot(page, section, 'adopted', width));
  });

  for (const [state, name, sentence] of [
    [
      'newer-schema',
      'action_executions.battle_detail_on_a_ledger_a_newer_build_wrote_is_not_interpreted',
      'Written by a newer version of Tower Pilot, so it is not shown. This does not mean that no action ran.',
    ],
    [
      'before-history',
      'action_executions.battle_detail_recorded_before_the_action_store_existed',
      'Recorded before action history existed. This does not mean that no action ran.',
    ],
  ] as const) {
    test(`AC4 ${state}: items null never reads as nothing ran at ${String(width)}`, async ({
      page,
    }) => {
      const section = await open(page, width, name);
      await expect(section.getByText(sentence, { exact: true })).toBeVisible();
      await expect(section.getByText(/No action executions/)).toHaveCount(0);
      await expect(section.getByRole('listitem')).toHaveCount(0);
      await accessible(page, width);
      remember(await screenshot(page, section, state, width));
    });
  }

  test(`AC4 a refresher-settled row reads as stopped early with no finish time at ${String(width)}`, async ({
    page,
  }) => {
    const section = await open(
      page,
      width,
      'action_executions_more.battle_detail_after_the_refresher_settled_an_open_row',
    );
    const row = section.getByRole('listitem').first();
    await expect(row).toContainText('Stopped before it finished');
    await expect(row).toContainText('Effect unknown');
    await expect(row).toContainText('Not recorded: stopped before it finished');
    await row.getByText('Why', { exact: true }).click();
    await expect(row).toContainText('operation_ended');
    await accessible(page, width);
    remember(await screenshot(page, section, 'settled', width));
  });

  test(`AC4 mixed states: open, failed, unconfirmed, Run now, harvest launch and a null label at ${String(width)}`, async ({
    page,
  }) => {
    const section = await open(page, width, 'mixed');
    await expect(section.getByText('Open when read')).toBeVisible();
    // The unconfirmed claim and the harvest launch (a launch starts a process; nothing proves the collector ran).
    await expect(section.getByText('Finished, effect not confirmed')).toHaveCount(2);
    await expect(section.getByText('Manual (Run now)')).toBeVisible();
    const runNow = section.getByRole('listitem').filter({ hasText: 'Manual (Run now)' });
    // Pin the current whole-second copy: a stale pre-fix bundle rendered "23.3 s" here.
    await expect(runNow.getByText('23 s', { exact: true })).toBeVisible();
    await expect(runNow.getByText('23.3 s', { exact: true })).toHaveCount(0);
    await expect(section.getByText('Harvest collector launch')).toHaveCount(2);
    await expect(section.locator('strong code.tp-history__code')).toHaveText('ad_gems');
    await expect(section.getByText(/running now/i)).toHaveCount(0);
    // Newest first: each row starts no later than the one above it.
    const starts = await section.locator('.tp-history__execution-head time').allTextContents();
    expect(starts.length).toBeGreaterThan(2);
    expect([...starts].sort().reverse()).toEqual(starts);
    await accessible(page, width);
    remember(await screenshot(page, section, 'mixed', width));
    expect(await posts(page)).toEqual([]);
  });

  test(`AC4 truncated: older executions follow next_before to the end by keyboard at ${String(width)}`, async ({
    page,
  }) => {
    const section = await open(page, width, TRUNCATED);
    await expect(section.getByRole('listitem')).toHaveCount(50);
    await expect(
      section.getByText('Older executions exist. Only the newest ones are shown.'),
    ).toBeVisible();
    await accessible(page, width);
    remember(await screenshot(page, section, 'truncated', width));
    const older: string[] = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (url.pathname === '/api/actions/executions') older.push(url.search);
    });
    const button = section.getByRole('button', { name: 'Older executions' });
    await button.focus();
    await page.keyboard.press('Enter');
    await expect(section.getByRole('listitem')).toHaveCount(51);
    expect(older).toEqual([
      `?battle_id=0000019e-0000-4000-8000-000000000000&before=${encodeURIComponent(CURSOR)}&instance=main`,
    ]);
    await expect(section.getByRole('button', { name: /older executions/iu })).toHaveCount(0);
    await expect(
      section.getByText('All recorded executions for this battle are shown.'),
    ).toBeVisible();
    await expect(section.getByRole('listitem').nth(50)).toBeFocused();
    await accessible(page, width);
    remember(await screenshot(page, section, 'truncated-complete', width));
    expect(await posts(page)).toEqual([]);
  });

  test(`AC4 a failed older read keeps the rows and offers a retry at ${String(width)}`, async ({
    page,
  }) => {
    const section = await open(page, width, TRUNCATED);
    let fail = true;
    await page.route('**/api/actions/executions?*', async (route) => {
      if (fail) await route.fulfill({ status: 500, json: { ok: false, error: 'unreadable' } });
      else await route.fallback();
    });
    const button = section.getByRole('button', { name: 'Older executions' });
    await button.focus();
    await page.keyboard.press('Enter');
    await expect(section.getByRole('button', { name: 'Retry older executions' })).toBeFocused();
    await expect(
      section.getByText(/The server did not return a readable history result/),
    ).toBeVisible();
    await expect(section.getByRole('listitem')).toHaveCount(50);
    await accessible(page, width);
    remember(await screenshot(page, section, 'older-failed', width));
    fail = false;
    await page.keyboard.press('Enter');
    await expect(section.getByRole('listitem')).toHaveCount(51);
  });
}
