import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { dashboardPagesCase } from '../support/dashboardFixture.ts';
import { axeIds, bodiesTo, posts } from './helpers/helpers.ts';
import { undersizedControlTargets } from './helpers/target-box.ts';

const OUT = process.env['TP_F6_SCREENS_DIR'] ?? path.resolve('test-results', 'f6-screens');
mkdirSync(OUT, { recursive: true });

async function pick(page: Page, scenario: string, width: number, policy = 'all_on') {
  const errors: string[] = [];
  page.on('pageerror', (error) => {
    errors.push(error.message);
  });
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`/__fake/use?scenario=profile_target_collection_${scenario}`);
  await page.evaluate(() => {
    window.location.hash = '#profiles';
  });
  const picker = page.locator('[data-form="editing-target"]');
  await expect(picker.getByRole('combobox')).toBeVisible();
  await picker.getByRole('combobox').selectOption('second');
  await picker.getByRole('button', { name: 'Edit this profile' }).focus();
  await page.keyboard.press('Enter');
  await expect(picker).toContainText('Editing:second(not the active profile)');
  expect(await posts(page)).toEqual([]);
  await page.evaluate((id) => {
    window.location.hash = `#collection/policy/${id}`;
  }, policy);
  await expect(
    page.locator('[data-region="collectionPolicies"] [data-editing-target="second"]'),
  ).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Name shown', exact: true })).toBeVisible();
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
  const small = await undersizedControlTargets(page.locator('main'), { width: 24, height: 24 });
  expect(small).toEqual([]);
  if (width === 390)
    expect(
      await page
        .locator('main button')
        .evaluateAll((elements) =>
          elements
            .filter(
              (element) =>
                element.getClientRects().length > 0 && element.getBoundingClientRect().height < 44,
            )
            .map((element) => element.textContent),
        ),
    ).toEqual([]);
  await page.screenshot({
    path: path.join(OUT, `profile-target-collection-${state}-${width}.png`),
    fullPage: true,
  });
}

const cases = {
  label: 'inactive_gather_label_saved',
  copy: 'inactive_gather_copy_saved',
  create: 'inactive_gather_template_created',
  delete: 'inactive_unused_gather_deleted',
  chores: 'inactive_chores_saved',
};

for (const width of [1360, 1054, 390]) {
  for (const operation of ['label', 'copy', 'create', 'delete', 'chores'] as const) {
    test(`PE4 ${operation}: one exact recorded inactive POST, active settings blocked, axe and targets at ${width}`, async ({
      page,
    }) => {
      const errors = await pick(
        page,
        operation,
        width,
        operation === 'delete' ? 'spare' : 'all_on',
      );
      const settings = page.locator('[data-active-profile="true"]');
      const scope = page.getByRole('region', { name: 'Saved settings', exact: true });
      await expect(scope).toContainText('Active profile:mine');
      await expect(settings.first().getByRole('checkbox').first()).toBeVisible();
      await expect(settings.first().getByRole('checkbox').first()).toBeDisabled();
      await expect(scope).toContainText('Return editing to the active profile to change these');
      for (const save of await settings.getByRole('button', { name: 'Save', exact: true }).all())
        await expect(save).toBeDisabled();
      if (operation === 'label') {
        await page.getByRole('textbox', { name: 'Name shown', exact: true }).fill('Offline');
        await page.getByRole('button', { name: /Save name/u }).focus();
        await page.keyboard.press('Enter');
      } else if (operation === 'copy') {
        await page.getByRole('textbox', { name: 'New identifier', exact: true }).fill('spare');
        await page.getByRole('button', { name: /Save copy/u }).click();
      } else if (operation === 'create') {
        await page
          .getByRole('combobox', { name: 'Template gathering default' })
          .selectOption(JSON.stringify(['farm', 'all_on']));
        await page.getByRole('textbox', { name: 'New policy identifier' }).fill('fresh');
        await page.getByRole('button', { name: /Save policy/u }).click();
      } else if (operation === 'delete') {
        const trigger = page.getByRole('button', { name: /Delete this policy/u });
        await trigger.focus();
        await page.keyboard.press('Enter');
        await expect(page.getByRole('button', { name: 'Delete', exact: true })).toBeFocused();
        expect(await posts(page)).toEqual([]);
        await proof(page, 'delete-confirm', width);
        await page.getByRole('button', { name: 'Cancel', exact: true }).click();
        await expect(trigger).toBeFocused();
        expect(await posts(page)).toEqual([]);
        await page.getByRole('button', { name: /Delete this policy/u }).click();
        await page.getByRole('button', { name: 'Delete', exact: true }).click();
      } else {
        await page.getByRole('checkbox', { name: 'Quest scan', exact: true }).uncheck();
        await page.getByRole('button', { name: /Save chores/u }).click();
      }
      await expect.poll(async () => (await bodiesTo(page, '/api/profile-patch')).length).toBe(1);
      expect(await bodiesTo(page, '/api/profile-patch')).toEqual([
        dashboardPagesCase(`profile_target.${cases[operation]}`).request.body,
      ]);
      expect(await bodiesTo(page, '/api/settings')).toEqual([]);
      await expect(page.locator('[data-outcome="saved"]')).toBeVisible();
      await proof(page, `${operation}-saved`, width);
      expect((await posts(page)).map((entry) => entry.path)).toEqual(['/api/profile-patch']);
      expect(errors).toEqual([]);
    });
  }

  test(`PE4 context changed after click sends nothing, keeps draft; return to active at ${width}`, async ({
    page,
  }) => {
    const errors = await pick(page, 'label', width);
    await page.getByRole('textbox', { name: 'Name shown', exact: true }).fill('Kept draft');
    // Hold only the click-time config read so the warm scenario change cannot race a poll.
    await page.route('**/api/config', async (route) => {
      const changed = dashboardPagesCase(
        'profile_target.read_config_after_editor_active_profile_switch',
      );
      await route.fulfill({ status: changed.status, json: changed.body });
    });
    await page.getByRole('button', { name: /Save name/u }).click();
    await expect(page.locator('[data-not-sent="context_changed"]')).toBeVisible();
    expect(await posts(page)).toEqual([]);
    await expect(page.getByRole('textbox', { name: 'Name shown', exact: true })).toHaveValue(
      'Kept draft',
    );
    await page.unroute('**/api/config');
    await proof(page, 'context-changed', width);
    await page.evaluate(() => {
      window.location.hash = '#profiles';
    });
    await page
      .locator('[data-form="editing-target"]')
      .getByRole('button', { name: 'Return editing to the active profile' })
      .click();
    await page.evaluate(() => {
      window.location.hash = '#collection';
    });
    await expect(
      page.locator('[data-region="collectionPolicies"] [data-editing-target="mine"]'),
    ).toBeVisible();
    await expect(page.getByRole('textbox', { name: 'Name shown', exact: true })).not.toHaveValue(
      'Kept draft',
    );
    await proof(page, 'active', width);
    expect(await posts(page)).toEqual([]);
    expect(errors).toEqual([]);
  });

  for (const state of ['stale', 'unknown'] as const) {
    test(`PE4 ${state} retains draft or settles only from second, never resends at ${width}`, async ({
      page,
    }) => {
      const errors = await pick(page, state, width);
      await page.getByRole('textbox', { name: 'Name shown', exact: true }).fill('Offline');
      await page.getByRole('button', { name: /Save name/u }).click();
      await expect(
        page.locator(`[data-outcome="${state === 'stale' ? 'refused' : 'applied'}"]`),
      ).toBeVisible();
      if (state === 'stale')
        await expect(page.getByRole('textbox', { name: 'Name shown', exact: true })).toHaveValue(
          'Offline',
        );
      await proof(page, state, width);
      expect(await bodiesTo(page, '/api/profile-patch')).toHaveLength(1);
      expect(await bodiesTo(page, '/api/settings')).toEqual([]);
      expect(errors).toEqual([]);
    });
  }
}
