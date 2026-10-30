import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { axeCounts, bodiesTo, navigateApp, openScenario, posts } from './helpers/helpers.ts';

/**
 * The Emulators page in a real browser on the fake backend (brief
 * backend/logs/reviews/ui-pages/emulators-brief.md, M1 to M8): navigation from the
 * Dashboard, the guarded device requests (one POST per confirmed click, never resent),
 * refusals as the backend sends them, a lost Start, the click-only screen-size check,
 * the preview settings, axe, reflow and target sizes at 1360, 1054 and 390, and the
 * screenshots (TP_F6_SCREENS_DIR, else test-results/f6-screens).
 */
const WIDTHS = [1360, 1054, 390] as const;
const OUT = process.env['TP_F6_SCREENS_DIR'] ?? path.resolve('test-results', 'f6-screens');
mkdirSync(OUT, { recursive: true });

interface Opened {
  readonly problems: string[];
  /** Every GET path the page asked for, in order. */
  readonly reads: string[];
}

async function openEmulators(page: Page, scenario: string, width = 1360): Promise<Opened> {
  const problems: string[] = [];
  const reads: string[] = [];
  page.on('pageerror', (error) => {
    problems.push(error.message);
  });
  page.on('request', (request) => {
    if (request.method() === 'GET') reads.push(new URL(request.url()).pathname);
  });
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`/__fake/use?scenario=${scenario}`);
  await page.evaluate(() => {
    window.location.hash = '#emulators';
  });
  await expect(page.getByRole('heading', { level: 1, name: 'Emulators' })).toBeVisible();
  await expect(detected(page).locator('[data-row]').first()).toBeVisible();
  return { problems, reads };
}

const target = (page: Page) => page.locator('[data-region="emulatorsTarget"]');
const detected = (page: Page) => page.locator('[data-region="emulatorsDetected"]');
const tools = (page: Page) => page.locator('[data-region="emulatorsTools"]');
const preview = (page: Page) => page.locator('[data-region="emulatorsPreview"]');
const row = (page: Page, key: string) => page.locator(`[data-row="${key}"]`);

test('M1 M2 SR-1 SR-6 app navigation opens Emulators with no request; Use this one asks first by keyboard, Cancel sends nothing, Use it sends one adopt that reads back saved and connected', async ({
  page,
}) => {
  const problems = await openScenario(page, 'emulators');
  const reads: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'GET') reads.push(new URL(request.url()).pathname);
  });
  await navigateApp(page, 'Emulators');
  await expect(page).toHaveURL(/#emulators$/u);
  await expect(detected(page).locator('[data-suggestion]')).toBeVisible();
  await page.waitForTimeout(2_000);
  expect(await posts(page)).toEqual([]);
  expect(reads.filter((read) => read.startsWith('/api/wizard/resolution'))).toEqual([]);
  const suggestion = detected(page).locator('[data-suggestion]');
  await suggestion.getByRole('button', { name: 'Use this one' }).focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText(
    'Instance main (account none chosen) will use 127.0.0.1:16384 instead of fake:1',
  );
  await dialog.getByRole('button', { name: 'Cancel' }).focus();
  await page.keyboard.press('Enter');
  await expect(dialog).toBeHidden();
  expect(await posts(page)).toEqual([]);
  await suggestion.getByRole('button', { name: 'Use this one' }).focus();
  await page.keyboard.press('Enter');
  await page.getByRole('dialog').getByRole('button', { name: 'Use it' }).focus();
  await page.keyboard.press('Enter');
  await expect.poll(async () => (await bodiesTo(page, '/api/wizard/adopt')).length).toBe(1);
  expect(await bodiesTo(page, '/api/wizard/adopt')).toEqual([
    {
      serial: '127.0.0.1:16384',
      adb: '<root>/program-files/Netease/MuMuPlayer/nx_main/adb.exe',
      expect_instance: 'main',
    },
  ]);
  const line = target(page).locator('[data-kind="adopt"]');
  await expect(line).toHaveAttribute('data-outcome', 'saved');
  await expect(line).toContainText('Its connect attempt: connected');
  await expect(target(page).locator('[data-configured-serial]')).toContainText('127.0.0.1:16384');
  await target(page).screenshot({ path: path.join(OUT, 'emulators-adopt-saved-1360.png') });
  await page.waitForTimeout(3_000);
  expect((await posts(page)).map((entry) => entry.path)).toEqual(['/api/wizard/adopt']);
  expect(problems).toEqual([]);
});

test('M2 SR-3 a saved address that did not connect shows both facts and the server message', async ({
  page,
}) => {
  const { problems } = await openEmulators(page, 'emulators_not_connected');
  await detected(page)
    .locator('[data-suggestion]')
    .getByRole('button', { name: 'Use this one' })
    .click();
  await page.getByRole('dialog').getByRole('button', { name: 'Use it' }).click();
  const line = target(page).locator('[data-kind="adopt"]');
  await expect(line).toContainText('Saved as the address of this instance');
  await expect(line).toContainText('Its connect attempt: not connected');
  await expect(line).toContainText('refused');
  await target(page).screenshot({ path: path.join(OUT, 'emulators-adopt-not-connected-1360.png') });
  expect(await bodiesTo(page, '/api/wizard/adopt')).toHaveLength(1);
  expect(problems).toEqual([]);
});

test('M1 M2 SR-7 two running VMs: no automatic choice, a foreign adb is not probed, and a stale adoption is refused with the server reason, never resent', async ({
  page,
}) => {
  const { problems } = await openEmulators(page, 'emulators_two_running');
  await expect(detected(page).locator('[data-suggestion]')).toHaveCount(0);
  await expect(detected(page)).toContainText('Not probed:');
  await row(page, 'MuMu:1').getByRole('button', { name: 'Use this one' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Use it' }).click();
  const line = target(page).locator('[data-kind="adopt"]');
  await expect(line).toHaveAttribute('data-outcome', 'refused');
  await expect(line).toContainText('active instance changed since the scan');
  await page.screenshot({
    path: path.join(OUT, 'emulators-two-running-refused-1360.png'),
    fullPage: true,
  });
  await page.waitForTimeout(3_000);
  expect(await bodiesTo(page, '/api/wizard/adopt')).toHaveLength(1);
  expect(problems).toEqual([]);
});

test('M3 M8 SR-7 a Start refused while a runner owns the device shows the backend owner and reason; nothing is resent', async ({
  page,
}) => {
  const { problems } = await openEmulators(page, 'emulators_owned');
  await row(page, 'MuMu:3').getByRole('button', { name: 'Start it' }).click();
  await expect(row(page, 'MuMu:3').locator('[data-kind="launch"]')).toContainText(
    'runners alive - stop them first',
  );
  await expect(target(page).locator('[data-owner-notice]')).toBeVisible();
  await target(page).screenshot({ path: path.join(OUT, 'emulators-owned-refused-1360.png') });
  await page.waitForTimeout(3_000);
  expect(await bodiesTo(page, '/api/wizard/launch')).toEqual([{ emulator: 'MuMu', index: 3 }]);
  expect(problems).toEqual([]);
});

test('M2 M3 M5 SR-7 while the status read lists a runner, Use, Start, Install and Reconnect say why and send nothing', async ({
  page,
}) => {
  const { problems } = await openEmulators(page, 'emulators_runner_listed');
  const reason = 'Automation is running; stop it on the Dashboard first';
  await tools(page)
    .getByRole('checkbox', { name: /Android SDK terms/u })
    .check();
  const buttons = [
    row(page, 'MuMu:0').getByRole('button', { name: 'Use this one' }),
    row(page, 'MuMu:3').getByRole('button', { name: 'Start it' }),
    tools(page).getByRole('button', { name: 'Install connection tools' }),
  ];
  for (const button of buttons) {
    await expect(button).toHaveAttribute('aria-disabled', 'true');
    await expect(button).toHaveAccessibleDescription(reason);
    await button.click({ force: true });
  }
  await expect(target(page).getByRole('button', { name: 'Reconnect' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  await expect(page.getByRole('dialog')).toBeHidden();
  await page.screenshot({
    path: path.join(OUT, 'emulators-runner-listed-1360.png'),
    fullPage: true,
  });
  await page.waitForTimeout(2_000);
  expect(await posts(page)).toEqual([]);
  expect(problems).toEqual([]);
});

test('M3 SR-6 Start it sends one launch; the accepted line is not a game screen and Start waits for a new scan', async ({
  page,
}) => {
  const { problems } = await openEmulators(page, 'emulators');
  const box = row(page, 'MuMu:3');
  await box.getByRole('button', { name: 'Start it' }).dblclick();
  await expect(box.locator('[data-kind="launch"]')).toHaveAttribute('data-outcome', 'accepted');
  await expect(box).toContainText('Start requested');
  const start = box.getByRole('button', { name: 'Start it' });
  await expect(start).toHaveAttribute('aria-disabled', 'true');
  await expect(start).toHaveAccessibleDescription(
    'Start requested; scan again before starting it again',
  );
  await start.click({ force: true });
  await detected(page).screenshot({ path: path.join(OUT, 'emulators-start-requested-1360.png') });
  await page.waitForTimeout(3_000);
  expect(await bodiesTo(page, '/api/wizard/launch')).toEqual([{ emulator: 'MuMu', index: 3 }]);
  expect(problems).toEqual([]);
});

test('M3 SR-4 a Start whose answer is lost is Unknown with no Dismiss and is never resent', async ({
  page,
}) => {
  const { problems } = await openEmulators(page, 'emulators_launch_lost');
  const box = row(page, 'MuMu:3');
  await box.getByRole('button', { name: 'Start it' }).click();
  const line = box.locator('[data-kind="launch"]');
  await expect(line).toHaveAttribute('data-outcome', 'unknown');
  await expect(line.getByRole('button', { name: /Dismiss/u })).toHaveCount(0);
  await detected(page).screenshot({ path: path.join(OUT, 'emulators-start-unknown-1360.png') });
  await page.waitForTimeout(4_000);
  expect(await bodiesTo(page, '/api/wizard/launch')).toHaveLength(1);
  expect(problems.filter((problem) => !problem.includes('Failed to fetch'))).toEqual([]);
});

test('M5 SR-6 Install waits for the license tick; one install starts it and the progress reads show it installed', async ({
  page,
}) => {
  const { problems } = await openEmulators(page, 'emulators');
  const install = tools(page).getByRole('button', { name: 'Install connection tools' });
  await expect(install).toHaveAccessibleDescription('Accept the Android SDK terms first');
  await install.click({ force: true });
  expect(await posts(page)).toEqual([]);
  await tools(page)
    .getByRole('checkbox', { name: /Android SDK terms/u })
    .check();
  await install.click();
  await expect.poll(async () => (await bodiesTo(page, '/api/wizard/tools/install')).length).toBe(1);
  await expect(tools(page)).toContainText('Install started');
  await expect(tools(page).locator('[data-tools="installed"]')).toBeVisible({ timeout: 10_000 });
  await tools(page).screenshot({ path: path.join(OUT, 'emulators-tools-installed-1360.png') });
  await page.waitForTimeout(2_000);
  expect(await bodiesTo(page, '/api/wizard/tools/install')).toHaveLength(1);
  expect(problems).toEqual([]);
});

test('M6 the screen size is read only on a click, for the configured address, with the raw size and its meaning', async ({
  page,
}) => {
  const { problems, reads } = await openEmulators(page, 'emulators_resolution_landscape');
  await page.waitForTimeout(1_500);
  expect(reads.filter((read) => read.startsWith('/api/wizard/resolution'))).toEqual([]);
  const checks: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname === '/api/wizard/resolution') checks.push(url.search);
  });
  await target(page).getByRole('button', { name: 'Check screen size' }).click();
  await expect(target(page).locator('[data-resolution]')).toContainText('2560 x 1080');
  await expect(target(page)).toContainText('Not portrait');
  await target(page).getByRole('button', { name: 'About Screen size' }).hover();
  await expect(page.getByRole('tooltip')).toContainText('A size is not recognition');
  expect(checks).toEqual(['?serial=fake%3A1']);
  await target(page).screenshot({ path: path.join(OUT, 'emulators-screen-size-1360.png') });
  expect(await posts(page)).toEqual([]);
  expect(problems).toEqual([]);
});

test('M7 SR-5 an invalid frame rate keeps the raw text with its reason and nothing is sent; the settings say they are not a live video', async ({
  page,
}) => {
  const { problems } = await openEmulators(page, 'emulators');
  await preview(page)
    .getByRole('button', { name: 'About Video preview settings', exact: true })
    .hover();
  await expect(page.getByRole('tooltip')).toContainText(
    'Saved settings for the preview, not a live video',
  );
  await page.keyboard.press('Escape');
  const fps = preview(page).locator('[data-part="fps"]');
  await fps.fill('0.2');
  await expect(fps).toHaveValue('0.2');
  await expect(fps).toHaveAttribute('aria-invalid', 'true');
  await expect(fps).toHaveAccessibleDescription('Below the lowest allowed value');
  await preview(page).screenshot({ path: path.join(OUT, 'emulators-preview-invalid-1360.png') });
  await page.waitForTimeout(2_000);
  expect(await posts(page)).toEqual([]);
  expect(problems).toEqual([]);
});

test('M8 Reconnect is one click; an offline answer says so with the device state and is not resent', async ({
  page,
}) => {
  const { problems } = await openEmulators(page, 'emulators_reconnect_offline');
  await target(page).getByRole('button', { name: 'Reconnect' }).click();
  const line = target(page).locator('[data-kind="reconnect"]');
  await expect(line).toContainText('Not connected: the adb server reports');
  await expect(line).toContainText('offline');
  await target(page).screenshot({ path: path.join(OUT, 'emulators-reconnect-offline-1360.png') });
  await page.waitForTimeout(3_000);
  expect((await posts(page)).map((entry) => entry.path)).toEqual(['/api/wizard/reconnect']);
  // F6M-02: the body names the address the page showed.
  expect(await bodiesTo(page, '/api/wizard/reconnect')).toEqual([{ serial: 'fake:1' }]);
  expect(problems).toEqual([]);
});

for (const width of WIDTHS) {
  test(`M1-M8 axe, reflow and targets of the Emulators page at ${String(width)}, and its screenshot`, async ({
    page,
  }) => {
    const { problems } = await openEmulators(page, 'emulators', width);
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
      path: path.join(OUT, `emulators-page-${String(width)}.png`),
      fullPage: true,
    });
    expect(await posts(page)).toEqual([]);
    expect(problems).toEqual([]);
  });
}
