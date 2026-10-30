import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { SCENARIOS } from '../support/fake-backend/scenarios.mjs';

/**
 * Screenshots of the Dashboard against the fake backend (spec 9 item 6): one
 * per region at 1360 px, and the whole page at 1360, 1054 and 390 px. They are
 * machine output: written to TP_SCREENS_DIR when set, else under
 * test-results/. Each also checks the page drew without a console error.
 */
const OUT = process.env['TP_SCREENS_DIR'] ?? path.resolve('test-results', 'screens');
mkdirSync(OUT, { recursive: true });

async function open(page: Page, scenario: string, width: number): Promise<string[]> {
  const problems: string[] = [];
  page.on('console', (message) => {
    // Chrome logs a refused or cut answer itself; the scenarios ask for those on purpose.
    if (message.type() === 'error' && !message.text().startsWith('Failed to load resource'))
      problems.push(message.text());
  });
  page.on('pageerror', (error) => {
    problems.push(error.message);
  });
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`/__fake/use?scenario=${scenario}`);
  await expect(page.getByRole('heading', { level: 2, name: 'Control' })).toBeVisible();
  // Let the first reads answer and the page settle.
  await page.waitForTimeout(1_500);
  return problems;
}

const REGIONS: readonly [region: string, scenario: string][] = [
  ['control', 'sketch'],
  ['needsYou', 'setup_guard_block'],
  ['thisBattle', 'active_battle_routines'],
  ['startNext', 'sketch'],
  ['recent', 'active_battle_routines'],
];

for (const [region, scenario] of REGIONS) {
  test(`screenshot: ${region} at 1360 (${scenario})`, async ({ page }) => {
    const problems = await open(page, scenario, 1360);
    const element = page.locator(`[data-region="${region}"]`);
    await expect(element).toBeVisible();
    await element.screenshot({ path: path.join(OUT, `${region}-1360.png`) });
    expect(problems).toEqual([]);
  });
}

for (const [name, scenario] of [
  ['outcomes', 'dropped_writes'],
  ['ownerNotice', 'refusal'],
] as const) {
  test(`screenshot: ${name} at 1360 (${scenario}, after a Pause)`, async ({ page }) => {
    const problems = await open(page, scenario, 1360);
    await page
      .locator('[data-slot="gem"]')
      .getByRole('button', { name: 'Pause harvesting' })
      .click();
    await expect(page.locator('.tp-outcomes').first()).toBeVisible();
    await page.waitForTimeout(1_000);
    await page
      .locator('[data-region="control"]')
      .screenshot({ path: path.join(OUT, `${name}-1360.png`) });
    expect(problems).toEqual([]);
  });
}

test('screenshot: top bar at 1360 (idle)', async ({ page }) => {
  const problems = await open(page, 'idle', 1360);
  await page.locator('header.tp-topbar').screenshot({ path: path.join(OUT, 'topBar-1360.png') });
  expect(problems).toEqual([]);
});

for (const width of [1360, 1054, 390]) {
  test(`screenshot: the whole page at ${String(width)} (sketch)`, async ({ page }) => {
    const problems = await open(page, 'sketch', width);
    await page.screenshot({
      path: path.join(OUT, `page-sketch-${String(width)}.png`),
      fullPage: true,
    });
    expect(problems).toEqual([]);
  });
}

/**
 * Spec 9 item 6: every scenario at 1360, 1054 and 390, and a render fault per
 * slot and region. Run only when TP_SCREENS_MATRIX is set (machine output for
 * the report; the default e2e run keeps to the region shots above).
 */
const MATRIX_ON = process.env['TP_SCREENS_MATRIX'] !== undefined;
const MATRIX_DIR = path.join(OUT, 'matrix');
if (MATRIX_ON) mkdirSync(MATRIX_DIR, { recursive: true });

for (const scenario of Object.keys(SCENARIOS).filter((name) => !name.startsWith('e0_'))) {
  test(`screenshot matrix: ${scenario} at 1360, 1054 and 390`, async ({ page }) => {
    test.skip(!MATRIX_ON, 'TP_SCREENS_MATRIX not set');
    const failing = SCENARIOS[scenario]?.get?.['status'] === '502';
    await page.setViewportSize({ width: 1360, height: 900 });
    await page.goto(`/__fake/use?scenario=${scenario}`);
    await expect(page.getByRole('heading', { level: 2, name: 'Control' })).toBeVisible();
    if (!failing) await expect(page.locator('[data-region="control"] time')).toBeVisible();
    await page.waitForTimeout(800);
    for (const width of [1360, 1054, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.waitForTimeout(150);
      await page.screenshot({
        path: path.join(MATRIX_DIR, `${scenario}-${String(width)}.png`),
        fullPage: true,
      });
    }
  });
}

for (const fault of [
  'slot:automation',
  'slot:input',
  'slot:gem',
  'outcomes',
  'control',
  'needsYou',
  'thisBattle',
  'startNext',
  'recent',
  'topBar',
]) {
  test(`screenshot matrix: render fault in ${fault} at 1360`, async ({ page }) => {
    test.skip(!MATRIX_ON, 'TP_SCREENS_MATRIX not set');
    await page.addInitScript((part) => {
      window.sessionStorage.setItem('tp.faults', part);
    }, fault);
    await page.setViewportSize({ width: 1360, height: 900 });
    await page.goto('/__fake/use?scenario=collecting');
    await expect(page.locator('[data-fallback]').first()).toBeVisible();
    await page.waitForTimeout(800);
    await page.screenshot({
      path: path.join(MATRIX_DIR, `fault-${fault.replace(':', '-')}-1360.png`),
      fullPage: true,
    });
  });
}

/** F4-01: the Automation fallback keeps incomplete Stop targets, disabled with their reason. */
for (const scenario of ['x9', 'runner_created_null', 'stopping_no_revision']) {
  test(`screenshot: F4-01 Automation fallback for ${scenario} at 1360, 1054 and 390`, async ({
    page,
  }) => {
    await page.addInitScript(() => {
      window.sessionStorage.setItem('tp.faults', 'slot:automation');
    });
    const problems = await open(page, scenario, 1360);
    for (const width of [1360, 1054, 390]) {
      await page.setViewportSize({ width, height: 900 });
      const control = page.locator('[data-region="control"]');
      await expect(control.locator('[data-fallback="automation"]')).toBeVisible();
      await control.screenshot({
        path: path.join(OUT, `f4-${scenario}-automation-fallback-${String(width)}.png`),
      });
    }
    // The fault is injected on purpose; any other error still fails the shot.
    expect(problems.filter((problem) => !problem.includes('Injected fault'))).toEqual([]);
  });
}

/** F4-02: what a short desktop window shows first. */
for (const width of [1360, 1054]) {
  test(`screenshot: F4-02 active_battle_routines in a ${String(width)}x700 window`, async ({
    page,
  }) => {
    const problems = await open(page, 'active_battle_routines', width);
    await page.setViewportSize({ width, height: 700 });
    await page.screenshot({ path: path.join(OUT, `f4-active-${String(width)}x700.png`) });
    expect(problems).toEqual([]);
  });
}
