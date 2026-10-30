import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import {
  axeCounts,
  bodiesTo,
  expectAppStyles,
  navigateApp,
  openScenario,
  posts,
} from './helpers/helpers.ts';

/**
 * The Recognition page in a real browser on the fake backend (brief
 * backend/logs/reviews/ui-pages/screens-brief.md, R1 to R8, roadmap item 9): navigation from
 * the Dashboard, consented launches (one POST per confirmed click, never resent), refusals
 * as the backend sends them, the evidence and its not-reported metrics, axe, reflow and
 * target sizes at 1360, 1054 and 390, and the screenshots (TP_F6_SCREENS_DIR, else
 * test-results/f6-screens). Advanced repairs (R9 to R11) have their own spec.
 */
const WIDTHS = [1360, 1054, 390] as const;
const OUT = process.env['TP_F6_SCREENS_DIR'] ?? path.resolve('test-results', 'f6-screens');
mkdirSync(OUT, { recursive: true });

const CONTEXT = {
  account: null,
  instance: 'main',
  profile: 'mine',
  rendering: null,
  serial: 'fake:1',
};

async function openRecognition(page: Page, scenario: string, width = 1360): Promise<string[]> {
  const problems = await openScenario(page, scenario, { width });
  await page.evaluate(() => {
    window.location.hash = '#recognition';
  });
  await expect(page.getByRole('heading', { level: 1, name: 'Recognition' })).toBeVisible();
  await expect(
    region(page, 'recognitionTarget').locator('[data-status-read="ready"]'),
  ).toBeVisible();
  return problems;
}

const region = (page: Page, name: string) => page.locator(`[data-region="${name}"]`);

async function layoutProblems(page: Page, width: number): Promise<string[]> {
  await expectAppStyles(page);
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  const small = await page.evaluate(() =>
    [
      ...document.querySelectorAll<HTMLElement>(
        'main button, main input, main select, main summary',
      ),
    ]
      .filter((element) => element.getClientRects().length > 0)
      .filter((element) => !element.closest('[data-region="recognitionAdvanced"]'))
      .map((element) => ({
        label: element.outerHTML.slice(0, 60),
        box: element.getBoundingClientRect(),
      }))
      .filter((entry) => entry.box.height < 24 || entry.box.width < 24)
      .map((entry) => entry.label),
  );
  const short =
    width === 390
      ? await page.evaluate(() =>
          [...document.querySelectorAll<HTMLElement>('main button')]
            .filter((element) => element.getClientRects().length > 0)
            .filter((element) => !element.closest('[data-region="recognitionAdvanced"]'))
            .filter((element) => element.getBoundingClientRect().height < 44)
            .map((element) => element.textContent),
        )
      : [];
  return [...(overflow > 0 ? [`overflow ${String(overflow)}`] : []), ...small, ...short];
}

for (const width of WIDTHS) {
  for (const action of ['prepare', 'learn']) {
    test(`R17 Prepare ${action} preselects the Dashboard run at ${String(width)} without extra entry requests`, async ({
      page,
    }) => {
      const problems = await openScenario(page, 'profiles_setup', { width });
      const startNext = region(page, 'startNext');
      const run = startNext.getByRole('combobox', { name: 'Run to start' });
      await run.selectOption('bp_farm');
      await expect(run).toHaveValue('bp_farm');
      const link = startNext.locator(`[data-focus-key="startNext:${action}"]`);
      await expect(link).toHaveAttribute('href', '#recognition?run=bp_farm');
      const readiness: string[] = [];
      page.on('request', (request) => {
        if (new URL(request.url()).pathname === '/api/readiness') readiness.push(request.url());
      });
      await link.click();
      await expect(page.getByRole('heading', { level: 1, name: 'Recognition' })).toBeVisible();
      const picker = region(page, 'recognitionRun').getByRole('combobox', { name: 'Run' });
      await expect(picker).toHaveValue('bp_farm');
      await expect(
        region(page, 'recognitionRun').getByRole('button', { name: 'Prepare this run' }),
      ).toHaveAttribute('aria-disabled', 'true');
      await page.waitForTimeout(1_500);
      expect(readiness).toEqual([]);
      expect(await posts(page)).toEqual([]);
      expect(await layoutProblems(page, width)).toEqual([]);
      await page.screenshot({
        path: path.join(OUT, `r17-prepare-${action}-${String(width)}.png`),
        fullPage: true,
      });
      await page.evaluate(() => {
        window.location.hash = '#recognition?run=unknown';
      });
      await expect(picker).toHaveValue('');
      expect(readiness).toEqual([]);
      expect(await posts(page)).toEqual([]);
      expect(problems).toEqual([]);
    });
  }

  test(`R1 R2 R3 RM9 the page at ${String(width)}: evidence, not-reported metrics, no request on open, axe and targets`, async ({
    page,
  }) => {
    const problems = await openRecognition(page, 'recognition_setup_finished', width);
    const evidence = region(page, 'recognitionEvidence');
    await expect(
      evidence.locator('[data-group="range"] [data-entry-status="verified"]'),
    ).toBeVisible();
    await expect(evidence.locator('[data-group="module"]')).toContainText('Not in this report');
    await expect(evidence.locator('[data-not-reported="gaps"]')).toContainText(
      'Target gap summary: not reported',
    );
    await expect(evidence.locator('[data-not-reported="metrics"]')).toContainText('not reported');
    await expect(region(page, 'recognitionOperation').locator('[data-stage]')).toHaveAttribute(
      'data-stage',
      'awaiting_apply',
    );
    await page.waitForTimeout(1_500);
    expect(await posts(page)).toEqual([]);
    expect(await layoutProblems(page, width)).toEqual([]);
    expect(await axeCounts(page)).toEqual([]);
    await page.screenshot({
      path: path.join(OUT, `recognition-page-${String(width)}.png`),
      fullPage: true,
    });
    expect(problems).toEqual([]);
  });
}

test('R1 R7 cancelling the learn consent sends nothing; a confirmed learn sends one start, refused as the server sent it', async ({
  page,
}) => {
  await openRecognition(page, 'recognition_images_missing');
  const learn = region(page, 'recognitionLearn');
  await learn
    .getByRole('checkbox', { name: 'Also capture battle controls in one test battle' })
    .check();
  await learn.getByRole('button', { name: 'Learn screens' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('Starts one normal test battle and ends it itself');
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await page.waitForTimeout(1_000);
  expect(await posts(page)).toEqual([]);
  await learn.getByRole('button', { name: 'Learn screens' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Start learning' }).click();
  const line = region(page, 'recognitionOperation').locator('[data-kind="calibrate_start"]');
  await expect(line).toHaveAttribute('data-outcome', 'refused');
  await expect(line).toContainText('Capture the navigation images in the Scan plan');
  await expect(line).toContainText('screens/hdr_cards.png');
  const sent = await bodiesTo(page, '/api/calibrate/start');
  expect(sent).toHaveLength(1);
  expect(sent[0]).toMatchObject({
    bootstrap: true,
    flows: true,
    phases: 'c,m',
    fresh: true,
    overwrite: false,
    allow_navigation: true,
    expected_context: CONTEXT,
  });
  expect(Object.keys(sent[0] as object).sort()).toEqual([
    'allow_navigation',
    'bootstrap',
    'expected_context',
    'flows',
    'fresh',
    'idempotency_key',
    'overwrite',
    'phases',
  ]);
  await region(page, 'recognitionOperation').screenshot({
    path: path.join(OUT, 'recognition-learn-refused-1360.png'),
  });
});

test('R1 a start whose answer is lost stays Unknown and is never resent while reads go on', async ({
  page,
}) => {
  await openRecognition(page, 'recognition_start_lost');
  const learn = region(page, 'recognitionLearn');
  await learn.getByRole('button', { name: 'Learn screens' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Start learning' }).click();
  const line = region(page, 'recognitionOperation').locator('[data-kind="calibrate_start"]');
  await expect(line).toHaveAttribute('data-outcome', 'unknown', { timeout: 15_000 });
  await page.waitForTimeout(6_000);
  expect(await bodiesTo(page, '/api/calibrate/start')).toHaveLength(1);
  await expect(learn.getByRole('button', { name: 'Learn screens' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  await expect(line.getByRole('button', { name: 'Send the same request again' })).toBeVisible();
  await region(page, 'recognitionOperation').screenshot({
    path: path.join(OUT, 'recognition-start-unknown-1360.png'),
  });
});

test('R7 a tracked battle blocks starting setup with its reason; capture stays available', async ({
  page,
}) => {
  await openRecognition(page, 'recognition_live_battle');
  const learn = region(page, 'recognitionLearn');
  await expect(learn.getByRole('button', { name: 'Learn screens' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  await expect(learn).toContainText('A battle is tracked on this emulator');
  await expect(
    region(page, 'recognitionObserve').getByRole('button', { name: 'Capture now' }),
  ).not.toHaveAttribute('aria-disabled', 'true');
  // A disabled action stays focusable and says why; a forced click still sends nothing.
  await learn.getByRole('button', { name: 'Learn screens' }).click({ force: true });
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(await posts(page)).toEqual([]);
  await page.screenshot({ path: path.join(OUT, 'recognition-live-battle-1360.png') });
});

test('R2 R8 Stop asks first and sends one stop with the operation and revision; stopping is not stopped', async ({
  page,
}) => {
  await openRecognition(page, 'recognition_running');
  const operation = region(page, 'recognitionOperation');
  await expect(operation.locator('[data-stage]')).toHaveAttribute('data-stage', 'accepted');
  await operation.getByRole('button', { name: 'Stop this scan' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('does not restore equipment');
  await dialog.getByRole('button', { name: 'Ask it to stop' }).click();
  const line = operation.locator('[data-kind="calibrate_stop"]');
  await expect(line).toHaveAttribute('data-outcome', 'stopping');
  await expect(line).toContainText('Asked to stop; not stopped yet');
  expect(await bodiesTo(page, '/api/calibrate/stop')).toEqual([
    { operation_id: '000000a1-0000-4000-8000-000000000000', expected_revision: 2 },
  ]);
  await page.waitForTimeout(3_000);
  expect(await bodiesTo(page, '/api/calibrate/stop')).toHaveLength(1);
  await operation.screenshot({ path: path.join(OUT, 'recognition-stop-1360.png') });
});

test('R4 an operation awaiting apply is applied on a confirmed click with its id and revision; a run is checked once', async ({
  page,
}) => {
  await openRecognition(page, 'recognition_awaiting_apply');
  const run = region(page, 'recognitionRun');
  await run.getByRole('combobox', { name: 'Run' }).selectOption('bp_farm');
  await page.waitForTimeout(1_000);
  expect(await posts(page)).toEqual([]);
  await run.getByRole('button', { name: 'Check this run' }).click();
  await expect(run.locator('[data-readiness]')).toBeVisible();
  const operation = region(page, 'recognitionOperation');
  await operation.getByRole('button', { name: 'Apply discoveries to this profile' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Apply' }).click();
  await expect(operation.locator('[data-kind="calibrate_apply"]')).toHaveAttribute(
    'data-outcome',
    'applied',
  );
  expect(await bodiesTo(page, '/api/calibrate/apply')).toEqual([
    {
      operation_id: '00000090-0000-4000-8000-000000000000',
      expected_revision: 3,
      expected_context: { instance: 'main', account: null, profile: 'mine', serial: 'fake:1' },
    },
  ]);
  await page.screenshot({ path: path.join(OUT, 'recognition-apply-1360.png'), fullPage: true });
});

test('R5 a watch longer than 600 s stays as typed and sends nothing; 30 s sends exactly that', async ({
  page,
}) => {
  await openRecognition(page, 'recognition');
  const observe = region(page, 'recognitionObserve');
  const field = observe.getByRole('textbox', { name: 'Watch for (seconds, 1 to 600)' });
  await field.fill('601');
  await expect(field).toHaveValue('601');
  await expect(observe).toContainText('At most 600 seconds');
  await observe.getByRole('button', { name: 'Watch', exact: true }).click({ force: true });
  await page.waitForTimeout(500);
  expect(await posts(page)).toEqual([]);
  await field.fill('30');
  await observe.getByRole('button', { name: 'Watch', exact: true }).click();
  await expect(
    region(page, 'recognitionOperation').locator('[data-kind="calibrate_observe"]'),
  ).toHaveAttribute('data-outcome', 'accepted');
  const sent = await bodiesTo(page, '/api/calibrate/observe');
  expect(sent).toHaveLength(1);
  expect(sent[0]).toMatchObject({ watch: 30, expected_context: CONTEXT });
});

test('R8 restoration required blocks every launch and says the stop did not restore', async ({
  page,
}) => {
  await openRecognition(page, 'recognition_restore_pending');
  await expect(region(page, 'recognitionTarget').locator('[data-restore-pending]')).toBeVisible();
  await expect(
    region(page, 'recognitionLearn').getByRole('button', { name: 'Learn screens' }),
  ).toHaveAttribute('aria-disabled', 'true');
  await expect(
    region(page, 'recognitionObserve').getByRole('button', { name: 'Capture now' }),
  ).toHaveAttribute('aria-disabled', 'true');
  await expect(region(page, 'recognitionOperation')).toContainText(
    'Stopped; equipment must be restored',
  );
  expect(await posts(page)).toEqual([]);
  await page.screenshot({ path: path.join(OUT, 'recognition-restore-pending-1360.png') });
});

test('R1 app navigation opens Recognition; opening it sends nothing', async ({ page }) => {
  await openScenario(page, 'recognition');
  await navigateApp(page, 'Recognition');
  await expect(page).toHaveURL(/#recognition$/u);
  await expect(page.getByRole('heading', { level: 1, name: 'Recognition' })).toBeVisible();
  await page.waitForTimeout(2_000);
  expect(await posts(page)).toEqual([]);
});

for (const width of WIDTHS) {
  test(`R6 F6R10-R1-07 at ${String(width)}: scan choices by keyboard keep focus across polls and survive a reload`, async ({
    page,
  }) => {
    await openRecognition(page, 'recognition', width);
    const more = region(page, 'recognitionMore');
    await more.getByText('Basic scan', { exact: true }).click();
    const bots = more.getByRole('checkbox', { name: 'Bots' });
    await bots.focus();
    await page.keyboard.press('Space');
    await expect(bots).toBeChecked();
    await page.waitForTimeout(6_000);
    await expect(bots).toBeFocused();
    await expect(bots).toBeChecked();
    await more.screenshot({ path: path.join(OUT, `recognition-basic-scan-${String(width)}.png`) });
    await page.reload();
    await expect(page.getByRole('heading', { level: 1, name: 'Recognition' })).toBeVisible();
    await region(page, 'recognitionMore').getByText('Basic scan', { exact: true }).click();
    await expect(
      region(page, 'recognitionMore').getByRole('checkbox', { name: 'Bots' }),
    ).toBeChecked();
    await expect(
      region(page, 'recognitionMore').getByRole('checkbox', { name: 'Global presets' }),
    ).not.toBeChecked();
    expect(await posts(page)).toEqual([]);
  });
}
