import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { historyCase, HISTORY_RECORDS } from '../support/history-fixtures.mjs';
import { axeIds, navigateApp, openScenario, posts, WCAG_AA_TAGS } from './helpers/helpers.ts';
import { loadHistoryFixture } from '../../scripts/history-fixture.mjs';
import { record } from '../../scripts/generate-contract.mjs';

const ROWS = '.tp-history__record';
const WIDTHS = [1360, 1054, 390] as const;
const SCREENS =
  process.env['TP_F6_SCREENS_DIR'] === undefined
    ? new URL('../../../../backend/logs/reviews/frontend-f6-screens/', import.meta.url)
    : pathToFileURL(path.resolve(process.env['TP_F6_SCREENS_DIR']) + path.sep);

async function openHistory(page: Page, width: number) {
  await openScenario(page, 'idle', { width });
  await page.request.get('/__fake/history');
  await page.getByRole('main').getByRole('link', { name: 'Run history', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Run history', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Reconnect', exact: true })).toHaveCount(0);
  await expect(page.locator(ROWS)).toHaveCount(20);
}

async function selectCase(page: Page, name: string) {
  await page.request.get(`/__fake/history?case=${encodeURIComponent(name)}`);
  // The fixture case must retain the selected record's identity.
  const answer = historyCase('history_detail', name);
  const battle = record(answer.body['battle']);
  const id = battle['id'];
  if (typeof id !== 'string') throw new Error('Fixture battle has no ID');
  const index = HISTORY_RECORDS.findIndex((row) => row.battle['id'] === id);
  const pager = page.getByRole('button', { name: index >= 20 ? 'Next page' : 'Previous page' });
  if (await pager.isEnabled()) await pager.click();
  const row = page.locator(ROWS).filter({ hasText: id });
  const alreadySelected = (await row.getAttribute('aria-pressed')) === 'true';
  const read = page.waitForResponse(
    (response) => new URL(response.url()).pathname === `/api/battles/${id}`,
  );
  await row.click();
  if (alreadySelected) await page.getByRole('button', { name: 'Refresh detail' }).click();
  await (await read).finished();
  if (!alreadySelected)
    await expect(page.getByRole('heading', { name: 'Selected record' })).toBeFocused();
  await expect(page.locator('.tp-history__evidence')).toBeVisible();
  await expect(
    page
      .locator('.tp-history__evidence')
      .getByText('Recorded status', { exact: true })
      .locator('..')
      .locator('dd'),
  ).toHaveText(String(battle['status']));
}

for (const width of WIDTHS) {
  test(`H3 F6-HISTORY-01: every recorded stats lifecycle remains distinct at ${width}`, async ({
    page,
  }) => {
    await openHistory(page, width);
    for (const [name, expected, shot] of [
      ['stats_capture_verified', 'Verified for this record', 'verified'],
      ['stats_capture_unverified_not_fresh', 'Unverified', 'unverified'],
      ['stats_capture_failed_with_artifact', 'Capture failed', 'failed-with-artifact'],
      ['stats_capture_failed_no_artifact', 'Capture failed', 'failed-no-artifact'],
      ['stats_capture_pending', 'Capture pending', 'pending'],
      ['stats_capture_missing_after_the_battle_ended', 'Missing / not recorded', 'missing-ended'],
      ['stats_capture_missing_while_the_battle_is_live', 'Missing / not recorded', 'missing-live'],
    ] as const) {
      await selectCase(page, name);
      const stats = page.getByRole('region', { name: 'Stats capture', exact: true });
      await expect(stats.getByText(expected, { exact: true })).toBeVisible();
      if (expected !== 'Unverified')
        await expect(stats.getByText('Unverified', { exact: true })).toHaveCount(0);
      if (name === 'stats_capture_failed_with_artifact')
        await expect(
          stats.getByText('tap refused during runlog (runlog_copy)', { exact: true }),
        ).toBeVisible();
      await stats.scrollIntoViewIfNeeded();
      await page.screenshot({
        path: fileURLToPath(new URL(`history-${width}-stats-${shot}.png`, SCREENS)),
      });
    }
    expect(await posts(page)).toEqual([]);
  });

  test(`H4: Back during a delayed detail read preserves records focus and scroll at ${width}`, async ({
    page,
  }) => {
    await openHistory(page, width);
    const first = HISTORY_RECORDS[0];
    if (first === undefined) throw new Error('Missing fixture record');
    let release: () => void = () => {
      throw new Error('Gate not initialized');
    };
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(`**/api/battles/${String(first.battle['id'])}?*`, async (route) => {
      await gate;
      const answer = historyCase('history_detail', first.name);
      await route.fulfill({ status: answer.status, json: answer.body });
    });
    await page.locator(ROWS).first().click();
    await expect(page.getByRole('heading', { name: 'Selected record' })).toBeFocused();
    await page.getByRole('button', { name: 'Back to records' }).click();
    const records = page.getByRole('heading', { name: 'Records', exact: true });
    await expect(records).toBeFocused();
    const before = await page.evaluate(() => scrollY);
    release();
    await expect(page.locator('.tp-history__evidence')).toBeVisible();
    await expect(records).toBeFocused();
    expect(await page.evaluate(() => scrollY)).toBe(before);
    expect(await posts(page)).toEqual([]);
  });

  test(`H2: empty, short, long and unbroken names, zero/null and unknown enums reflow at ${width}`, async ({
    page,
  }) => {
    const template = historyCase('history_list', 'first_page');
    const original = HISTORY_RECORDS[0]?.battle;
    if (original === undefined) throw new Error('Missing history record');
    // Deliberate hostile content on real response shapes, not additional backend evidence cases.
    let label = '';
    await page.route('**/api/battles?*', async (route) => {
      await route.fulfill({
        json: {
          ...template.body,
          battles: [
            {
              ...original,
              label,
              status: 'future_record_state',
              tier: 0,
              started_at: null,
              final_wave: 0,
            },
          ],
        },
      });
    });
    await openScenario(page, 'idle', { width });
    await page.request.get('/__fake/history');
    await page.getByRole('main').getByRole('link', { name: 'Run history', exact: true }).click();
    for (const value of ['', 'A', 'Recorded battle '.repeat(30), 'x'.repeat(1200)]) {
      label = value;
      await page.getByRole('button', { name: 'Refresh records' }).click();
      const row = page.locator(ROWS).locator('..');
      await expect(row).toContainText(value || 'Unnamed battle');
      await expect(row.getByText('Tier', { exact: true }).locator('..').locator('dd')).toHaveText(
        '0',
      );
      await expect(row.locator('time')).toHaveText('Unknown / not recorded');
      await expect(
        row.getByText('Recorded status', { exact: true }).locator('..').locator('dd'),
      ).toHaveText('future_record_state');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
    }
    expect(await posts(page)).toEqual([]);
  });

  test(`H2: record workspace keeps labeled facts in view without sideways scrolling at ${width}`, async ({
    page,
  }) => {
    await openHistory(page, width);
    const workspace = page.getByRole('region', { name: 'Recorded battles', exact: true });
    const row = workspace.locator('li').first();
    for (const label of ['Recorded status', 'Run', 'Tier', 'Last observed wave', 'Final wave']) {
      await expect(row.getByText(label, { exact: true })).toBeVisible();
      await expect(row.getByText(label, { exact: true }).locator('..').locator('dd')).toBeVisible();
    }
    await expect(row.getByText('Recorded start', { exact: true })).toBeVisible();
    await expect(row.locator('time')).toBeVisible();
    expect(
      await workspace.evaluate((element) => ({
        bounded: element.scrollHeight > element.clientHeight,
        fits: element.scrollWidth <= element.clientWidth + 1,
        height: element.getBoundingClientRect().height,
      })),
    ).toEqual({ bounded: true, fits: true, height: expect.any(Number) });
    const box = await workspace.boundingBox();
    expect(box?.height).toBeLessThanOrEqual((page.viewportSize()?.height ?? 900) * 0.65 + 1);
    const scrollHint = page.getByText(
      'More records on this page are below; scroll the list to see them.',
      { exact: true },
    );
    await expect(scrollHint).toBeVisible();
    await expect(workspace).toHaveAccessibleDescription(await scrollHint.innerText());
    await workspace.focus();
    await page.keyboard.press('PageDown');
    await expect.poll(() => workspace.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    // Before a selection the list has the page: no empty Selected record pane beside it.
    await expect(page.locator('[data-region="history-detail-region"]')).toHaveCount(0);
    if (width >= 1100) {
      const records = await page.locator('[data-region="history-records-region"]').boundingBox();
      const main = await page.getByRole('main').boundingBox();
      expect(records?.width).toBeGreaterThan((main?.width ?? 0) * 0.9);
    }
    await page.screenshot({
      path: fileURLToPath(new URL(`history-workspace-${width}.png`, SCREENS)),
      fullPage: true,
    });
    const id = await row.locator('[data-server-text="id"]').first().textContent();
    const instant = await row.locator('time').getAttribute('title');
    await row.locator(ROWS).click();
    const detail = page.locator('.tp-history__evidence');
    await expect(detail).toBeVisible();
    await expect(
      detail.getByText('Record ID', { exact: true }).locator('..').locator('dd'),
    ).toHaveText(id ?? '');
    await expect(
      detail.getByText('Recorded start', { exact: true }).locator('..').locator('dd'),
    ).toHaveText(instant ?? '');
    if (width >= 1100) {
      const records = await page.locator('[data-region="history-records-region"]').boundingBox();
      const detailPane = await page.locator('[data-region="history-detail-region"]').boundingBox();
      expect(detailPane?.y).toBe(records?.y);
      expect(detailPane?.x).toBeGreaterThan((records?.x ?? 0) + (records?.width ?? 0));
    }
    await page.screenshot({
      path: fileURLToPath(new URL(`history-workspace-selected-${width}.png`, SCREENS)),
      fullPage: true,
    });
    expect(await posts(page)).toEqual([]);
  });

  test(`H1 H2: paging, ended/interrupted filters and keyboard navigation at ${width}`, async ({
    page,
  }) => {
    await openHistory(page, width);
    await page.getByRole('button', { name: 'Next page' }).click();
    await expect(page.getByText('Page 2', { exact: true })).toBeVisible();
    await expect(page.locator(ROWS)).toHaveCount(HISTORY_RECORDS.length - 20);
    await expect(page.getByRole('button', { name: 'Next page' })).toBeDisabled();
    await page.getByRole('combobox', { name: 'Recorded status' }).selectOption('ended');
    await expect(page.getByText('Page 1', { exact: true })).toBeVisible();
    await expect(page.locator(ROWS)).toHaveCount(
      HISTORY_RECORDS.filter((row) => row.battle['status'] === 'ended').length,
    );
    await page.getByRole('combobox', { name: 'Recorded status' }).selectOption('interrupted');
    await expect(page.locator(ROWS)).toHaveCount(
      HISTORY_RECORDS.filter((row) => row.battle['status'] === 'interrupted').length,
    );
    await page.locator(ROWS).first().focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { name: 'Selected record' })).toBeFocused();
    await expect(page.locator('.tp-history__evidence')).toBeVisible();
    if (width === 390)
      expect(
        (await page.getByRole('heading', { name: 'Selected record' }).boundingBox())?.y,
      ).toBeLessThan(100);
    await expect(page.getByText(/does not establish defeat/)).toBeVisible();
    await page.getByRole('button', { name: 'Back to records' }).click();
    await expect(page.getByRole('heading', { name: 'Records', exact: true })).toBeFocused();
    expect(await posts(page)).toEqual([]);
  });

  test(`H3 H4: evidence, focus, stale detail, disclosures, axe and screenshot at ${width}`, async ({
    page,
  }) => {
    await openHistory(page, width);
    await selectCase(page, 'collection_sampled_confirmations_ended');
    await page.screenshot({
      path: fileURLToPath(new URL(`history-${width}.png`, SCREENS)),
    });
    const snapshot = page.getByText('Configuration snapshot', { exact: true });
    await snapshot.click();
    await page.getByText('Timestamped activity', { exact: true }).click();
    const refresh = page.getByRole('button', { name: 'Refresh detail' });
    await refresh.click();
    await expect(refresh).toBeFocused();
    await expect(page.locator('details').filter({ has: snapshot })).toHaveAttribute('open', '');
    await page.request.get('/__fake/history?case=detail_ledger_unreadable_500');
    await refresh.click();
    await expect(page.getByRole('button', { name: 'Retry detail' })).toBeFocused();
    await expect(page.locator('.tp-history__evidence')).toBeVisible();
    await expect(page.getByText(/Previously loaded evidence/)).toBeVisible();
    const violations = await axeIds(page, WCAG_AA_TAGS);
    expect(violations).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.getByRole('heading', { name: 'Run history', exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({
      path: fileURLToPath(new URL(`history-${width}-expanded-stale.png`, SCREENS)),
      fullPage: true,
    });
    await page.request.get('/__fake/history?case=detail_of_the_other_accounts_record_is_a_404');
    await page.getByRole('button', { name: 'Retry detail' }).click();
    await expect(
      page.getByRole('main').getByText(/selected record is missing or does not belong/),
    ).toBeVisible();
    await expect(page.locator('details').filter({ has: snapshot })).toHaveAttribute('open', '');
    expect(await posts(page)).toEqual([]);
  });

  test(`H3: verified, degraded, partial, unverified, gaps and unknown evidence at ${width}`, async ({
    page,
  }) => {
    await openHistory(page, width);
    for (const name of [
      'setup_verified',
      'setup_degraded_module_not_equipped',
      'active_adopted_partial_observation',
      'stats_capture_unverified_wrong_tier',
      'ended_after_an_observation_gap',
      'interrupted_before_any_observation_everything_unknown',
    ]) {
      await selectCase(page, name);
      const setup = page.getByRole('region', { name: 'Setup evidence' });
      await expect(setup).toContainText(
        name === 'setup_verified'
          ? 'verified'
          : name === 'setup_degraded_module_not_equipped'
            ? 'degraded'
            : 'unknown',
      );
      if (name === 'active_adopted_partial_observation')
        await expect(page.getByText(/Partial history:/)).toBeVisible();
      if (name === 'stats_capture_unverified_wrong_tier')
        await expect(
          page
            .getByRole('region', { name: 'Stats capture' })
            .getByText('Unverified', { exact: true }),
        ).toBeVisible();
      if (name === 'ended_after_an_observation_gap')
        await expect(page.getByRole('region', { name: 'Observation gaps' })).toContainText(
          '1800000058',
        );
      await page.getByRole('heading', { name: 'Selected record' }).scrollIntoViewIfNeeded();
      await page.screenshot({
        path: fileURLToPath(new URL(`history-${width}-${name}.png`, SCREENS)),
      });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
    }
    expect(await posts(page)).toEqual([]);
  });
}

test('H2: an account switch clears both records and selected detail before the new target is shown', async ({
  page,
}) => {
  await openHistory(page, 1054);
  await selectCase(page, 'setup_verified');
  const cases = loadHistoryFixture();
  const config = record(record(cases['config_state'])['effective_view_of_the_other_account']);
  const newList = historyCase('history_list', 'beta_sees_only_its_own_record');
  await page.route('**/api/config', async (route) => {
    await route.fulfill({ json: config['body'] });
  });
  await page.route('**/api/battles?*', async (route) => {
    await route.fulfill({ status: newList.status, json: newList.body });
  });
  await page.evaluate(() => {
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(page.locator(ROWS)).toHaveCount(1);
  await expect(page.locator('.tp-history__evidence')).toHaveCount(0);
  await expect(page.getByText('Choose a record to inspect its evidence.')).toBeVisible();
  await expect(page.locator(ROWS)).not.toContainText(String(HISTORY_RECORDS[1]?.battle['id']));
  expect(await posts(page)).toEqual([]);
});

test('H1: failed list refresh retains rows, focus and last-loaded time; independent retry and empty page', async ({
  page,
}) => {
  await openHistory(page, 1360);
  await page.request.get('/__fake/history?case=list:ledger_unreadable_500');
  await page.getByRole('button', { name: 'Refresh records' }).click();
  await expect(page.getByRole('button', { name: 'Retry records' })).toBeFocused();
  await expect(page.locator(ROWS)).toHaveCount(20);
  await page.request.get('/__fake/history?case=list:past_the_end_empty');
  await page.getByRole('button', { name: 'Retry records' }).click();
  await expect(page.getByText('No records on this page.')).toBeVisible();
  expect(await posts(page)).toEqual([]);
});

test('H2: selecting B while A is delayed never renders A as B; app navigation opens history', async ({
  page,
}) => {
  await openHistory(page, 390);
  const first = HISTORY_RECORDS[0];
  if (first === undefined) throw new Error('Missing fixture record');
  await page.route(`**/api/battles/${String(first.battle['id'])}?*`, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 500));
    const answer = historyCase('history_detail', first.name);
    await route.fulfill({ status: answer.status, json: answer.body });
  });
  await page.locator(ROWS).nth(0).click();
  await page.locator(ROWS).nth(1).click();
  await expect(page.getByRole('region', { name: 'Setup evidence' })).toContainText('verified');
  await page.waitForTimeout(600);
  await expect(page.getByRole('region', { name: 'Setup evidence' })).toContainText('verified');
  await navigateApp(page, 'Dashboard');
  await navigateApp(page, 'Run history');
  await expect(page.getByRole('heading', { name: 'Run history', exact: true })).toBeVisible();
});
