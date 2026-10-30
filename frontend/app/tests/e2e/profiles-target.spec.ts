import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { axeIds, bodiesTo, posts } from './helpers/helpers.ts';
import { moreCase, openMore, invalidSourceTests } from './helpers/profile-target-more.ts';

invalidSourceTests('collection', proof);

const OUT = process.env['TP_F6_SCREENS_DIR'] ?? path.resolve('test-results/f6-screens');
mkdirSync(OUT, { recursive: true });

for (const width of [1360, 1054, 390]) {
  test(`PE1 F6-14c list failure retains target and retries without a write at ${width}`, async ({
    page,
  }) => {
    await openMore(page, width, '#profiles', 'profile_target_runs_saved');
    let answer = moreCase('profile_files_read_failed_500');
    await page.route('**/api/profile-files', async (route) => {
      const body = answer.body as Record<string, unknown>;
      await route.fulfill(
        typeof body['text'] === 'string'
          ? { status: answer.status, body: body['text'], contentType: 'text/html' }
          : { status: answer.status, json: answer.body },
      );
    });
    // Leaving/reopening Profiles starts a fresh list read without changing its selection.
    await page.evaluate(() => {
      location.hash = '#runs/saved/farm';
    });
    await expect(page.locator('[data-run-entry]')).toBeVisible();
    await page.evaluate(() => {
      location.hash = '#profiles';
    });
    const picker = page.locator('[data-form="editing-target"]');
    await expect(picker).toContainText(
      'The profile list could not be read. Your editing profile is kept.',
    );
    await expect(picker).toContainText('Editing:second');
    answer = moreCase('profile_files_read_after_the_failure_answers_again');
    await picker.getByRole('button', { name: 'Retry profile list', exact: true }).click();
    await expect(picker.locator('[data-editing-target="second"]')).toHaveAttribute(
      'data-target-blocked',
      '',
    );
    expect(await posts(page)).toEqual([]);
    await proof(page, 'f6-14c-list-retry', width);
  });
}

async function open(page: Page, scenario: string, width: number) {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`/__fake/use?scenario=${scenario}`);
  await page.evaluate(() => {
    window.location.hash = '#profiles';
  });
  await expect(page.getByRole('heading', { level: 1, name: 'Profiles' })).toBeVisible();
  await expect(page.locator('[data-form="unbind"]')).toContainText('mine');
  return errors;
}

async function proof(page: Page, state: string, width: number) {
  const violations = await axeIds(page);
  expect(violations).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBeLessThanOrEqual(0);
  const small = await page
    .locator('main button, main input, main select, main summary, dialog[open] button')
    .evaluateAll((all) =>
      all
        .filter((element) => element.getClientRects().length > 0)
        .filter((element) => {
          const box = element.getBoundingClientRect();
          return box.width < 24 || box.height < 24;
        })
        .map((element) => element.outerHTML.slice(0, 120)),
    );
  expect(small).toEqual([]);
  if (width === 390) {
    expect(
      await page
        .locator('main button, dialog[open] button')
        .evaluateAll((all) =>
          all
            .filter(
              (element) =>
                element.getClientRects().length > 0 && element.getBoundingClientRect().height < 44,
            )
            .map((element) => element.textContent),
        ),
    ).toEqual([]);
  }
  await page.screenshot({
    path: path.join(OUT, `profile-target-foundation-${state}-${width}.png`),
    fullPage: true,
  });
}

for (const width of [1360, 1054, 390]) {
  test(`PE1 confirm/cancel, identity, keyboard focus return and config after unbind at ${width}`, async ({
    page,
  }) => {
    const errors = await open(page, 'profile_target_unbind', width);
    const trigger = page
      .locator('[data-form="unbind"]')
      .getByRole('button', { name: 'Use no profile' });
    await trigger.focus();
    await page.keyboard.press('Enter');
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('Account:alpha');
    await expect(dialog).toContainText('Emulator:main');
    await expect(dialog).toContainText('Current profile:mine');
    await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(dialog.getByRole('button', { name: 'Use no profile' })).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
    await proof(page, 'confirm', width);
    await page.keyboard.press('Escape');
    await expect(trigger).toBeFocused();
    expect(await posts(page)).toEqual([]);
    await page.keyboard.press('Enter');
    // Keep the genuine recorded config read in flight after the recorded receipt.
    let verifying = false;
    let releaseConfig: () => void = () => undefined;
    const configGate = new Promise<void>((resolve) => {
      releaseConfig = resolve;
    });
    await page.route('**/api/profile-activate', async (route) => {
      verifying = true;
      await route.continue();
    });
    await page.route('**/api/config', async (route) => {
      if (verifying) await configGate;
      await route.continue();
    });
    await dialog.getByRole('button', { name: 'Use no profile' }).click();
    await expect(trigger).toBeFocused();
    await expect(trigger).toHaveAttribute('aria-disabled', 'true');
    const unbind = page.locator('[data-form="unbind"]');
    await expect(unbind).toContainText('Accepted, waiting for the configuration to confirm');
    await expect(unbind).not.toContainText('No profile is active -');
    await expect(trigger).toHaveAccessibleDescription(
      'The previous unbind is unresolved; waiting for the configuration to confirm',
    );
    await proof(page, 'accepted', width);
    releaseConfig();
    await expect(unbind).toContainText('No profile is active -');
    await expect(page.locator('[data-form="unbind"]')).toContainText('No profile is active');
    expect(await bodiesTo(page, '/api/profile-activate')).toEqual([
      {
        name: null,
        expected_context: { instance: 'main', account: 'alpha', profile: 'mine', serial: 'fake:1' },
      },
    ]);
    await proof(page, 'unbound', width);
    expect(errors).toEqual([]);
  });

  test(`PE1 written_then_answer_failed settles by config; runner refusal leaves mine; never resent at ${width}`, async ({
    page,
  }) => {
    for (const scenario of ['profile_target_unbind_lost', 'profile_target_runner_refusal']) {
      const errors = await open(page, scenario, width);
      await page
        .locator('[data-form="unbind"]')
        .getByRole('button', { name: 'Use no profile' })
        .click();
      await page.getByRole('dialog').getByRole('button', { name: 'Use no profile' }).click();
      const lost = scenario === 'profile_target_unbind_lost';
      await expect(page.locator('[data-form="unbind"]')).toContainText(
        lost ? 'No profile is active -' : 'runners alive - stop them first',
      );
      if (!lost)
        await expect(page.locator('[data-form="unbind"]')).toContainText('Active profile:mine');
      await proof(page, lost ? 'settled' : 'refused', width);
      expect(await bodiesTo(page, '/api/profile-activate')).toHaveLength(1);
      expect(errors).toEqual([]);
    }
  });

  test(`PE2 pick second, restore after reload and return with zero POSTs at ${width}`, async ({
    page,
  }) => {
    const errors = await open(page, 'profile_target_pick', width);
    const box = page.locator('[data-form="editing-target"]');
    await box.getByRole('combobox').selectOption('second');
    await box.getByRole('button', { name: 'Edit this profile' }).focus();
    await page.keyboard.press('Enter');
    await expect(box).toContainText('Editing:second(not the active profile)');
    await expect(page.locator('[data-form="unbind"]')).toContainText('Active profile:mine');
    await proof(page, 'picked', width);
    await page.reload();
    await expect(box.locator('[data-editing-target="second"]')).toHaveAttribute(
      'data-target-blocked',
      '',
    );
    await box.getByRole('button', { name: 'Return editing to the active profile' }).focus();
    await page.keyboard.press('Enter');
    await expect(box).toContainText('Editing the active profile:mine');
    await proof(page, 'returned', width);
    expect(await posts(page)).toEqual([]);
    expect(errors).toEqual([]);
  });
}

test('PE2 the editing picker offers no profile before the page context is read, and a pick made after it holds', async ({
  page,
}) => {
  // Hold the page's first genuine config read so the recorded profile list answers first.
  let releaseConfig: () => void = () => undefined;
  const configGate = new Promise<void>((resolve) => {
    releaseConfig = resolve;
  });
  let configReads = 0;
  await page.route('**/api/config', async (route) => {
    configReads++;
    if (configReads === 1) await configGate;
    await route.continue();
  });
  await page.setViewportSize({ width: 1360, height: 900 });
  const listed = page.waitForResponse('**/api/profile-files');
  await page.goto('/__fake/use?scenario=profile_target_abilities_uw');
  await page.evaluate(() => {
    location.hash = '#profiles';
  });
  await listed;
  const picker = page.locator('[data-form="editing-target"]');
  await expect(page.locator('[data-profile="second"]')).toBeVisible();
  await expect(picker.locator('option[value="second"]')).toHaveCount(0);
  await expect(
    picker.getByRole('button', { name: 'Edit this profile', exact: true }),
  ).toBeDisabled();
  releaseConfig();
  await picker.getByRole('combobox').selectOption('second');
  await picker.getByRole('button', { name: 'Edit this profile', exact: true }).click();
  await expect(picker).toContainText('Editing:second');
  expect(await posts(page)).toEqual([]);
});
