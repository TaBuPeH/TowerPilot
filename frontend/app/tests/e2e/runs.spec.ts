import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import {
  DASHBOARD_PAGES_FIXTURE_PATH,
  loadJsonWithIdentity,
} from '../../scripts/contract-fixture.mjs';
import { axeCounts, bodiesTo, navigateApp, openScenario, posts } from './helpers/helpers.ts';

/**
 * The Runs page in a real browser on the fake backend (brief
 * backend/logs/reviews/ui-pages/runs-brief.md): navigation from the Dashboard,
 * the saved and live saves (one POST per click, never resent), axe, reflow and
 * target sizes at 1360, 1054 and 390, and the screenshots
 * (TP_F6_SCREENS_DIR, else test-results/f6-screens).
 */
const WIDTHS = [1360, 1054, 390] as const;
const OUT = process.env['TP_F6_SCREENS_DIR'] ?? path.resolve('test-results', 'f6-screens');
mkdirSync(OUT, { recursive: true });

async function openRuns(page: Page, scenario: string, hash: string, width = 1360) {
  const problems: string[] = [];
  page.on('pageerror', (error) => {
    problems.push(error.message);
  });
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`/__fake/use?scenario=${scenario}`);
  await page.evaluate((target) => {
    window.location.hash = target;
  }, hash);
  await expect(page.getByRole('heading', { level: 1, name: 'Runs' })).toBeVisible();
  await expect(page.locator('[data-region="runsSaved"] input[type="text"]').first()).toBeVisible();
  return problems;
}

test('RUN-12 app navigation opens the new Runs page, and the Dashboard links open its exact targets', async ({
  page,
}) => {
  await openScenario(page, 'active_battle_routines');
  await navigateApp(page, 'Runs');
  await expect(page).toHaveURL(/#runs$/u);
  await expect(page.getByRole('heading', { level: 1, name: 'Runs' })).toBeVisible();
  // A battle with overrides names "Changed for this battle" with its scoped links.
  await openScenario(page, 'runs_live_applied');
  await page.getByRole('link', { name: 'Edit this battle in Runs' }).first().click();
  await expect(page).toHaveURL(/#runs\/live$/u);
  await expect(page.locator('#runsLive-title')).toBeFocused();
  await page.goBack();
  await openScenario(page, 'idle');
  await page.locator('[data-slot="gem"]').getByRole('link', { name: 'Edit run' }).click();
  await expect(page).toHaveURL(/#runs\/saved\/bp_farm$/u);
  await expect(page.locator('#runsSaved-title')).toBeFocused();
  // The run picker itself: the saved section now also holds Add a run and entry-settings selects.
  await expect(
    page.locator('[data-region="runsSaved"] select[data-focus-key="runs:run"]'),
  ).toHaveValue('bp_farm');
  expect(await posts(page)).toEqual([]);
});

/** A recorded case of the pages contract fixture (run_controls_save, run_controls_live). */
function pagesCase(path: string): { request: { path: string; body: unknown }; body: unknown } {
  const [family = '', name = ''] = path.split('.');
  const cases = (
    loadJsonWithIdentity(DASHBOARD_PAGES_FIXTURE_PATH).fixture as {
      cases: Record<
        string,
        Record<string, { request: { path: string; body: unknown }; body: unknown }>
      >;
    }
  ).cases;
  const found = cases[family]?.[name];
  if (found === undefined) throw new Error(`No pages case ${path}`);
  return found;
}

test('RUN-05 SR-4 one Save click (a double click) sends exactly the recorded body once; the 200 reads Saved and nothing more is sent', async ({
  page,
}) => {
  const problems = await openRuns(page, 'runs_saved', '#runs');
  const region = page.locator('[data-region="runsSaved"]');
  await region.getByRole('checkbox', { name: 'Ad gems On' }).uncheck();
  await region.getByRole('button', { name: 'Save run settings' }).dblclick();
  await expect.poll(async () => (await bodiesTo(page, '/api/run-controls')).length).toBe(1);
  expect(await bodiesTo(page, '/api/run-controls')).toEqual([
    pagesCase('run_controls_save.saved_switch_off').request.body,
  ]);
  await expect(region.getByText('Saved', { exact: true })).toBeVisible();
  await expect(region.getByRole('checkbox', { name: 'Ad gems On' })).not.toBeChecked();
  await page.waitForTimeout(3_000);
  expect(await bodiesTo(page, '/api/run-controls')).toHaveLength(1);
  expect(problems).toEqual([]);
});

test('RUN-07 SR-4 SR-8 a save whose answer is lost reads Unknown with no resend; Check saved values reads the run that holds it', async ({
  page,
}) => {
  const problems = await openRuns(page, 'runs_saved_lost', '#runs');
  const region = page.locator('[data-region="runsSaved"]');
  await region.getByRole('checkbox', { name: 'Ad gems On' }).uncheck();
  await region.getByRole('button', { name: 'Save run settings' }).click();
  await expect(region.getByText('No reply - it may or may not have saved')).toBeVisible();
  await region.screenshot({ path: path.join(OUT, 'runs-saved-unknown-1360.png') });
  await expect(region.getByRole('button', { name: 'Send same request again' })).toHaveCount(0);
  await expect(region.getByRole('button', { name: 'Dismiss' })).toHaveCount(0);
  await region.getByRole('button', { name: 'Check saved values' }).click();
  await expect(region.getByText('The saved settings now have your changes')).toBeVisible();
  await page.waitForTimeout(3_000);
  expect(await bodiesTo(page, '/api/run-controls')).toHaveLength(1);
  expect(problems).toEqual([]);
});

test('RUN-03 SR-2 SR-4 F6R-02 F6C-R1-02 a reload while a save is in flight keeps the raw 030 in the field, sends nothing more; the unchanged read keeps it Unknown and only the explicit continue opens Save', async ({
  page,
}) => {
  const problems = await openRuns(page, 'runs_saved_held', '#runs');
  const region = page.locator('[data-region="runsSaved"]');
  const every = region.getByRole('textbox', { name: 'Ad gems Every' });
  await every.fill('030');
  await region.getByRole('button', { name: 'Save run settings' }).click();
  await expect.poll(async () => (await bodiesTo(page, '/api/run-controls')).length).toBe(1);
  await page.reload();
  await expect(page.getByRole('heading', { level: 1, name: 'Runs' })).toBeVisible();
  await expect(every).toHaveValue('030');
  await expect(region.getByText('No reply - it may or may not have saved')).toBeVisible();
  await expect(region.getByRole('button', { name: 'Discard' })).toBeVisible();
  const saveRun = region.getByRole('button', { name: 'Save run settings' });
  await expect(saveRun).toHaveAttribute('aria-disabled', 'true');
  await region.getByRole('button', { name: 'Continue from the saved values as read' }).click();
  await expect(saveRun).not.toHaveAttribute('aria-disabled');
  await expect(region.getByText('No reply - it may or may not have saved')).toBeVisible();
  await region.screenshot({ path: path.join(OUT, 'runs-saved-continued-1360.png') });
  await page.waitForTimeout(3_000);
  expect(await bodiesTo(page, '/api/run-controls')).toHaveLength(1);
  expect(problems).toEqual([]);
});

test('RUN-08 SR-1 a live change sends the recorded body at control_revision; recorded, it then waits to apply; the saved run is untouched', async ({
  page,
}) => {
  await openRuns(page, 'runs_live', '#runs/live');
  const live = page.locator('[data-region="runsLive"]');
  await live.getByRole('checkbox', { name: 'Ad gems On' }).uncheck();
  await live.getByRole('button', { name: 'Save changes to this battle' }).click();
  await expect.poll(async () => (await posts(page)).length).toBe(1);
  const [write] = await posts(page);
  const recorded = pagesCase('run_controls_live.accepted_switch_off');
  expect(write?.path).toBe(recorded.request.path);
  expect(JSON.parse(write?.body ?? '{}')).toEqual({
    ...(recorded.request.body as Record<string, unknown>),
    idempotency_key: expect.any(String) as unknown,
  });
  await expect(live.getByText('Sent to this battle - waiting to apply')).toBeVisible();
  await expect(
    live.getByRole('button', { name: 'Save changes to this battle' }),
  ).toHaveAccessibleDescription('Waiting for the last change to apply');
  expect(await bodiesTo(page, '/api/run-controls')).toEqual([]);
});

test('RUN-10 SR-4 SR-8 a live change whose read-back failed reads unconfirmed; Dismiss posts nothing and keeps live Save closed', async ({
  page,
}) => {
  await openRuns(page, 'runs_live_unknown', '#runs/live');
  const live = page.locator('[data-region="runsLive"]');
  await live.getByRole('checkbox', { name: 'Ad gems On' }).uncheck();
  await live.getByRole('button', { name: 'Save changes to this battle' }).click();
  await expect(
    live.getByText("The reply didn't confirm the change - it may have been recorded"),
  ).toBeVisible();
  await expect(live.getByRole('button', { name: 'Send same request again' })).toBeVisible();
  await live.screenshot({ path: path.join(OUT, 'runs-live-unconfirmed-1360.png') });
  await live.getByRole('button', { name: 'Dismiss' }).click();
  await expect(page.locator('#runsLive-title')).toBeFocused();
  await expect(
    live.getByRole('button', { name: 'Save changes to this battle' }),
  ).toHaveAccessibleDescription('An earlier save has no answer yet; check the values first');
  await page.waitForTimeout(3_000);
  expect(await posts(page)).toHaveLength(1);
});

test('RUN-10 SR-4 F6R-04 the battle ends while a live change is unknown: its line stays, names that battle and keeps its resend; the explicit retry is answered', async ({
  page,
}) => {
  await openRuns(page, 'runs_live_unknown', '#runs/live');
  const live = page.locator('[data-region="runsLive"]');
  await live.getByRole('checkbox', { name: 'Ad gems On' }).uncheck();
  await live.getByRole('button', { name: 'Save changes to this battle' }).click();
  await expect(
    live.getByText("The reply didn't confirm the change - it may have been recorded"),
  ).toBeVisible();
  // The battle ends: the next reads answer with no tracked battle (the same session's writes).
  await page.request.get('/__fake/set?scenario=idle');
  await live.getByRole('button', { name: 'Check this battle' }).click();
  await expect(
    live.getByText(
      'No tracked battle - live settings appear here while automation watches a battle',
    ),
  ).toBeVisible();
  await expect(live.locator('[data-earlier-battle="true"]')).toContainText(
    'Change to another battle:',
  );
  await live.screenshot({ path: path.join(OUT, 'runs-live-earlier-1360.png') });
  await page.waitForTimeout(3_000);
  expect(await posts(page)).toHaveLength(1);
  await live.getByRole('button', { name: 'Send same request again' }).click();
  await expect(live.getByText('That battle recorded the change')).toBeVisible();
  const writes = await posts(page);
  expect(writes).toHaveLength(2);
  expect(writes[1]?.body).toBe(writes[0]?.body);
});

test('RUN-09 an unapplied change keeps live Save disabled with its reason', async ({ page }) => {
  await openRuns(page, 'runs_live_overrides', '#runs/live');
  const live = page.locator('[data-region="runsLive"]');
  await expect(live.getByText('Waiting to apply').first()).toBeVisible();
  const save = live.getByRole('button', { name: 'Save changes to this battle' });
  await expect(save).toHaveAttribute('aria-disabled', 'true');
  await expect(save).toHaveAccessibleDescription('Waiting for the last change to apply');
});

for (const width of WIDTHS) {
  test(`RUN-02 axe, reflow and targets of the Runs page at ${String(width)}, and its screenshots`, async ({
    page,
  }) => {
    const problems = await openRuns(page, 'runs_live_applied', '#runs', width);
    await page.waitForTimeout(500);
    expect(await axeCounts(page)).toEqual([]);
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
      for (const name of ['Save run settings', 'Save changes to this battle']) {
        const box = await page.getByRole('button', { name }).boundingBox();
        expect(box?.height ?? 0, name).toBeGreaterThanOrEqual(44);
      }
      // F6R-08: each number on its own line; the editor's buttons stacked, one per line.
      for (const region of ['runsSaved', 'runsLive']) {
        const tops = await page
          .locator(`[data-region="${region}"] [data-routine="ad_gems"] input[type="text"]`)
          .evaluateAll((inputs) => inputs.map((input) => input.getBoundingClientRect().top));
        expect(tops.length, region).toBeGreaterThan(1);
        expect(new Set(tops.map((top) => Math.round(top))).size, region).toBe(tops.length);
      }
      const saved = page.locator('[data-region="runsSaved"]');
      const save = await saved.getByRole('button', { name: 'Save run settings' }).boundingBox();
      const check = await saved.getByRole('button', { name: 'Check saved values' }).boundingBox();
      expect(check?.y ?? 0).toBeGreaterThanOrEqual((save?.y ?? 0) + (save?.height ?? 0));
      expect(Math.abs((check?.x ?? 0) - (save?.x ?? 0))).toBeLessThanOrEqual(1);
    }
    await page.screenshot({
      path: path.join(OUT, `runs-page-${String(width)}.png`),
      fullPage: true,
    });
    // An edited, an invalid and a disabled-Save state, side by side with the saved values.
    const region = page.locator('[data-region="runsSaved"]');
    await region.getByRole('textbox', { name: 'Ad gems Every' }).fill('30');
    await region.getByRole('textbox', { name: 'Ad gems Window' }).fill('99');
    await region.screenshot({ path: path.join(OUT, `runs-saved-draft-${String(width)}.png`) });
    expect(problems).toEqual([]);
  });
}
