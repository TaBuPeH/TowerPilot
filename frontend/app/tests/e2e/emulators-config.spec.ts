import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { emulatorConfigCase } from '../support/fake-backend/emulators-config-fixtures.mjs';
import { axeIds, bodiesTo, posts, WCAG_AA_TAGS } from './helpers/helpers.ts';

const OUT = process.env['TP_F6_SCREENS_DIR'] ?? path.resolve('test-results/f6-screens');
mkdirSync(OUT, { recursive: true });
const WIDTHS = [1360, 1054, 390] as const;
const defaults = (page: Page) => page.locator('[data-module="instance-defaults"]');
const selection = (page: Page) => page.locator('[data-module="active-instance"]');
const saved = emulatorConfigCase('settings_modules.saved_instance_defaults');
const switchSave = emulatorConfigCase('settings_modules.saved_active_instance');
const values = saved.request.body?.['values'] as Record<string, unknown>;
const modules = saved.body['modules'] as {
  id: string;
  fields: { path: string; label: string }[];
}[];
function label(field: string) {
  const result = modules
    .find((module) => module.id === 'instance-defaults')
    ?.fields.find((entry) => entry.path === field)?.label;
  if (result === undefined) throw new Error(`No recorded field ${field}`);
  return result;
}
async function open(page: Page, scenario: string, width: number) {
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`/__fake/use?scenario=config_fields_emulators_${scenario}`);
  await page.evaluate(() => {
    window.location.hash = '#emulators';
  });
  await expect(defaults(page)).toBeVisible();
}
async function shot(page: Page, width: number, state: string) {
  await page.screenshot({
    path: path.join(OUT, `config-fields-emulators-${String(width)}-${state}.png`),
    fullPage: true,
  });
}
async function accessible(page: Page, width: number) {
  const violations = await axeIds(page, WCAG_AA_TAGS);
  expect(violations).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const small = await page
    .locator('button:visible, input:visible, select:visible, a:visible, summary:visible')
    .evaluateAll(
      (elements, width) =>
        elements
          .filter((element) => {
            const box = element.getBoundingClientRect();
            return (
              box.width < 24 ||
              box.height < (width === 390 && element.tagName === 'BUTTON' ? 44 : 24)
            );
          })
          .map((element) => `${element.tagName}: ${element.textContent}`),
      width,
    );
  expect(small).toEqual([]);
}
for (const width of WIDTHS) {
  test(`C15-1 C15-2 C15-3 C-03 an untouched unlisted preset is omitted while label and tray-start save at ${String(width)}`, async ({
    page,
  }) => {
    await open(page, 'invalid', width);
    const partial = emulatorConfigCase(
      'settings_modules_more.instance_defaults_saved_without_the_preset_field_keeps_the_unlisted_preset',
    );
    await expect(defaults(page).getByRole('combobox')).toHaveValue('ghost');
    await defaults(page).getByRole('textbox').fill('Tower');
    await defaults(page).getByRole('checkbox').check();
    await defaults(page).getByRole('button', { name: 'Save', exact: true }).focus();
    await page.keyboard.press('Enter');
    await expect(defaults(page).locator('[data-outcome="saved"]')).toBeVisible();
    expect(await bodiesTo(page, '/api/settings')).toEqual([partial.request.body]);
    await page.getByRole('button', { name: 'Read saved settings again' }).click();
    await expect(defaults(page).getByRole('combobox')).toHaveValue('ghost');
    await expect(defaults(page).getByRole('textbox')).toHaveValue('Tower');
    await expect(defaults(page).getByRole('checkbox')).toBeChecked();
    expect(await bodiesTo(page, '/api/settings')).toHaveLength(1);
    await accessible(page, width);
    await shot(page, width, 'unlisted-partial-saved');
  });
  for (const scenario of ['fallback_off', 'unlisted_fallback']) {
    test(`C15-2 C15-3 C-03 ${scenario} saves only explicit fallback and tray-start choices at ${String(width)}`, async ({
      page,
    }) => {
      await open(page, scenario, width);
      const recorded = emulatorConfigCase(
        scenario === 'fallback_off'
          ? 'settings_modules_more.saved_instance_defaults_with_fallback_and_autostart_off'
          : 'settings_modules_more.instance_defaults_choosing_the_machine_fallback_replaces_the_unlisted_preset',
      );
      await defaults(page).getByRole('combobox').selectOption('');
      if (scenario === 'fallback_off') await defaults(page).getByRole('checkbox').uncheck();
      await defaults(page).getByRole('button', { name: 'Save', exact: true }).click();
      await expect(defaults(page).locator('[data-outcome="saved"]')).toBeVisible();
      expect(await bodiesTo(page, '/api/settings')).toEqual([recorded.request.body]);
      await page.getByRole('button', { name: 'Read saved settings again' }).click();
      await expect(defaults(page).getByRole('combobox')).toHaveValue('');
      await expect(defaults(page).getByRole('checkbox')).toBeChecked({
        checked: scenario !== 'fallback_off',
      });
      expect(await bodiesTo(page, '/api/settings')).toHaveLength(1);
      await accessible(page, width);
      await shot(page, width, `${scenario}-saved`);
    });
  }
  test(`C15-2 C-03 no runnable presets retain the saved empty fallback without a write at ${String(width)}`, async ({
    page,
  }) => {
    await open(page, 'no_runnable', width);
    await expect(defaults(page).getByRole('combobox')).toHaveValue('');
    await expect(defaults(page).getByRole('combobox').getByRole('option')).toHaveCount(1);
    await expect(defaults(page).getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
    expect(await posts(page)).toEqual([]);
    await accessible(page, width);
    await shot(page, width, 'no-runnable-kept');
  });
  test(`C15-4 C-03 read-again recovers failed config readback without resend at ${String(width)}`, async ({
    page,
  }) => {
    await open(page, 'switch', width);
    let submitted = false;
    let recover = false;
    const failure = emulatorConfigCase('settings.saved_then_the_answer_failed_500');
    page.on('request', (request) => {
      if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/settings')
        submitted = true;
    });
    await page.route('**/api/config', async (route) => {
      // Reuse the genuine recorded 500 as a config-read fault, then allow the
      // scenario's genuine new-context config read. Only transport ordering changes.
      if (submitted && !recover)
        await route.fulfill({
          status: failure.status,
          contentType: 'application/json',
          body: JSON.stringify(failure.body),
        });
      else await route.continue();
    });
    await selection(page).getByRole('combobox').selectOption('other');
    await selection(page).getByRole('button', { name: 'Save', exact: true }).click();
    const failedRead = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === '/api/config' && response.status() === failure.status,
    );
    await page.getByRole('dialog').getByRole('button', { name: 'Use emulator' }).click();
    await failedRead;
    await expect(selection(page).locator('[data-outcome]')).toHaveAttribute(
      'data-outcome',
      'unknown',
    );
    await expect(
      selection(page).getByRole('button', { name: 'Continue from the saved values as read' }),
    ).toHaveCount(0);
    recover = true;
    await page.getByRole('button', { name: 'Read saved settings again' }).click();
    await expect(selection(page).locator('[data-outcome]')).toHaveAttribute(
      'data-outcome',
      'applied',
    );
    await expect(selection(page).getByRole('combobox')).toHaveValue('other');
    expect(await bodiesTo(page, '/api/settings')).toEqual([switchSave.request.body]);
    await accessible(page, width);
    await shot(page, width, 'switch-config-recovered');
  });
  test(`C15-4 C-03 lost answer settles only from config without resend at ${String(width)}`, async ({
    page,
  }) => {
    await open(page, 'switch_lost', width);
    await selection(page).getByRole('combobox').selectOption('other');
    await selection(page).getByRole('button', { name: 'Save', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Use emulator' }).click();
    await expect(selection(page).locator('[data-outcome]')).toHaveAttribute(
      'data-outcome',
      'applied',
    );
    await expect(selection(page).getByRole('combobox')).toHaveValue('other');
    expect(await bodiesTo(page, '/api/settings')).toEqual([switchSave.request.body]);
    await accessible(page, width);
    await shot(page, width, 'switch-lost-readback');
  });
  test(`C15-4 C-03 settings receipt and settings read alone leave the original context at ${String(width)}`, async ({
    page,
  }) => {
    await open(page, 'receipt_unverified', width);
    await selection(page).getByRole('combobox').selectOption('other');
    await selection(page).getByRole('button', { name: 'Save', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Use emulator' }).click();
    await expect(selection(page).locator('[data-outcome]')).toHaveAttribute(
      'data-outcome',
      'unknown',
    );
    await expect(page.locator('[data-region="emulatorsTarget"]')).toContainText('alpha');
    expect(await bodiesTo(page, '/api/settings')).toEqual([switchSave.request.body]);
    await accessible(page, width);
    await shot(page, width, 'switch-receipt-unverified');
  });
  test(`C15-1 C15-2 C15-3 C-03 defaults save and readback, keyboard, axe and targets at ${String(width)}`, async ({
    page,
  }) => {
    await open(page, 'defaults', width);
    expect(await posts(page)).toEqual([]);
    await defaults(page)
      .getByRole('textbox', { name: label('label') })
      .fill(String(values['label']));
    await defaults(page)
      .getByRole('combobox', { name: label('preset') })
      .selectOption(String(values['preset']));
    await defaults(page)
      .getByRole('checkbox', { name: label('autostart') })
      .check();
    await defaults(page).getByRole('button', { name: 'Save', exact: true }).focus();
    await page.keyboard.press('Enter');
    await expect(defaults(page).locator('[data-outcome]')).toHaveAttribute('data-outcome', 'saved');
    expect(await bodiesTo(page, '/api/settings')).toEqual([saved.request.body]);
    expect((await posts(page)).map((entry) => entry.path)).toEqual(['/api/settings']);
    await accessible(page, width);
    await shot(page, width, 'defaults-saved');
  });
  test(`C15-1 C15-2 C-03 invalid kept and stale refused at ${String(width)}`, async ({ page }) => {
    await open(page, 'invalid', width);
    await expect(defaults(page).getByRole('combobox')).toHaveValue('ghost');
    await expect(defaults(page).locator('[aria-invalid="true"]')).toBeVisible();
    expect(await posts(page)).toEqual([]);
    await accessible(page, width);
    await shot(page, width, 'invalid-kept');
    await open(page, 'refused', width);
    await defaults(page).getByRole('textbox').fill('  raw invalid  ');
    await defaults(page).getByRole('button', { name: 'Save', exact: true }).click();
    const invalid = emulatorConfigCase('settings_modules.invalid_string_instance_defaults');
    await expect(defaults(page).locator('[data-refusal]')).toHaveText(
      String(invalid.body['error']),
    );
    await expect(defaults(page).getByRole('textbox')).toHaveValue('  raw invalid  ');
    expect(await bodiesTo(page, '/api/settings')).toHaveLength(1);
    await accessible(page, width);
    await shot(page, width, 'invalid-refused');
    await open(page, 'stale', width);
    await defaults(page)
      .getByRole('textbox', { name: label('label') })
      .fill('  Stale  ');
    await defaults(page).getByRole('button', { name: 'Save', exact: true }).click();
    const refusal = emulatorConfigCase('settings_modules.stale_revision_instance_defaults');
    await expect(defaults(page).locator('[data-refusal]')).toHaveText(
      String(refusal.body['error']),
    );
    await expect(defaults(page).getByRole('textbox')).toHaveValue('  Stale  ');
    expect(await bodiesTo(page, '/api/settings')).toHaveLength(1);
    await shot(page, width, 'stale-refused');
  });
  test(`C15-4 C-03 switch cancel, focus restore, confirm once and verified context at ${String(width)}`, async ({
    page,
  }) => {
    await open(page, 'switch', width);
    await selection(page).getByRole('combobox').selectOption('other');
    const save = selection(page).getByRole('button', { name: 'Save', exact: true });
    await save.focus();
    await page.keyboard.press('Enter');
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(dialog.getByRole('button', { name: 'Use emulator' })).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(dialog.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused();
    await accessible(page, width);
    await shot(page, width, 'switch-confirmation');
    await page.keyboard.press('Escape');
    await expect(save).toBeFocused();
    expect(await posts(page)).toEqual([]);
    await page.keyboard.press('Enter');
    await dialog.getByRole('button', { name: 'Use emulator' }).focus();
    await page.keyboard.press('Enter');
    await expect.poll(async () => (await bodiesTo(page, '/api/settings')).length).toBe(1);
    expect(await bodiesTo(page, '/api/settings')).toEqual([switchSave.request.body]);
    await expect(selection(page).getByRole('combobox')).toHaveValue('other');
    await expect(selection(page).locator('[data-outcome]')).toHaveAttribute(
      'data-outcome',
      'applied',
    );
    await expect(page.locator('[data-region="emulatorsTarget"]')).toContainText('beta');
    await accessible(page, width);
    await shot(page, width, 'switch-applied');
  });
  test(`C15-4 C-03 unchanged config keeps Unknown and explicit acceptance separate at ${String(width)}`, async ({
    page,
  }) => {
    await open(page, 'switch_unchanged', width);
    await selection(page).getByRole('combobox').selectOption('other');
    await selection(page).getByRole('button', { name: 'Save', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Use emulator' }).click();
    await expect(selection(page).locator('[data-outcome]')).toHaveAttribute(
      'data-outcome',
      'unknown',
    );
    await expect(
      selection(page).getByRole('button', { name: 'Continue from the saved values as read' }),
    ).toBeVisible();
    await expect(selection(page).getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
    expect(await bodiesTo(page, '/api/settings')).toHaveLength(1);
    await accessible(page, width);
    await shot(page, width, 'switch-unknown');
    await selection(page)
      .getByRole('button', { name: 'Continue from the saved values as read' })
      .click();
    await expect(selection(page).locator('[data-accepted]')).toBeVisible();
    expect(await bodiesTo(page, '/api/settings')).toHaveLength(1);
  });
}
