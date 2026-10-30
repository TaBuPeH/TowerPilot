import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page, type Route, type Locator } from '@playwright/test';
import { axeCounts, posts, WCAG_AA_TAGS } from './helpers/helpers.ts';
import { undersizedControlTargets } from './helpers/target-box.ts';
import {
  DASHBOARD_PAGES_FIXTURE_PATH,
  loadJsonWithIdentity,
} from '../../scripts/contract-fixture.mjs';
import { entries, list, own, ownPath } from '../../src/core/read.ts';

const OUT =
  process.env['TP_F6_SCREENS_DIR'] ??
  path.resolve('../../backend/logs/reviews/frontend-f6-screens');
mkdirSync(OUT, { recursive: true });
const cases = own(loadJsonWithIdentity(DASHBOARD_PAGES_FIXTURE_PATH).fixture, 'cases');
const body = (family: string, key: string) => ownPath(cases, family, key, 'body');
const tournamentModule = (
  list(own(body('settings_modules', 'read_after_saved_run_defaults'), 'modules')) ?? []
).find((module) => own(module, 'id') === 'tournament-cards');
const fieldLabel = (path: string) =>
  String(
    own(
      (list(own(tournamentModule, 'fields')) ?? []).find((field) => own(field, 'path') === path),
      'label',
    ),
  );
const tournament = (page: Page) => page.locator('[data-module="tournament-cards"]');
const save = (page: Page) => page.getByRole('button', { name: 'Save selection', exact: true });
async function open(page: Page, width: number, scenario: string, loadout = 'plain') {
  if (page.url().startsWith('http'))
    await page.evaluate(() => {
      sessionStorage.clear();
    });
  await page.goto('about:blank');
  await page.setViewportSize({ width, height: 900 });
  await page.request.get(`/__fake/use?scenario=config_fields_equipment_${scenario}`);
  await page.goto('/ui/app/#equipment');
  await expect(
    page.getByRole('heading', { level: 1, name: 'Equipment', exact: true }),
  ).toBeVisible();
  if (
    !scenario.startsWith('lists') &&
    !scenario.startsWith('empty') &&
    !['invalid', 'stale', 'malformed'].includes(scenario)
  )
    await page.getByRole('button', { name: loadout, exact: true }).click();
}
async function add(page: Page, field: string, values: string[]) {
  const root = tournament(page).locator(`[data-field="${field}"]`);
  for (const value of values) {
    await root.getByRole('button', { name: /^Add entry/ }).focus();
    await page.keyboard.press('Enter');
    const input = root.getByRole('textbox').last();
    await input.focus();
    await page.keyboard.type(value);
  }
}
async function clearLists(page: Page) {
  // The recorded alpha account already has unsupported saved names. Remove them
  // explicitly before entering the exact recorded replacement request.
  for (const field of ['drop', 'add']) {
    const root = tournament(page).locator(`[data-field="${field}"]`);
    await expect(root.getByRole('button', { name: /^Add entry/ })).toBeVisible();
    while (await root.getByRole('button', { name: /^Remove entry/ }).count()) {
      await root
        .getByRole('button', { name: /^Remove entry/ })
        .first()
        .focus();
      await page.keyboard.press('Enter');
    }
  }
}
async function lists(page: Page) {
  await clearLists(page);
  await add(page, 'drop', ['cash', 'extra_orb']);
  await add(page, 'add', ['golden_orb']);
}
async function screenshot(page: Page, width: number, state: string) {
  await page.screenshot({
    path: path.join(OUT, `config-fields-equipment-${String(width)}-${state}.png`),
    fullPage: true,
  });
}

/** Replay a complete recorded answer. Only transport timing/loss is changed in these journeys. */
async function reply(route: Route, reference: string) {
  const [family = '', key = ''] = reference.split('.');
  const status = own(ownPath(cases, family, key), 'status');
  if (typeof status !== 'number') throw new Error(`Missing recording ${reference}`);
  await route.fulfill({ status, json: body(family, key) });
}
async function enter(page: Page, locator: Locator) {
  // Establish keyboard modality before programmatic focus (focus-visible follows modality).
  await page.keyboard.press('Tab');
  await locator.focus();
  await expect(locator).toBeFocused();
  expect(
    await locator.evaluate((node) => {
      const style = getComputedStyle(node);
      return (
        (style.outlineStyle !== 'none' && style.outlineWidth !== '0px') ||
        style.boxShadow !== 'none'
      );
    }),
  ).toBe(true);
  await page.keyboard.press('Enter');
}
const restoreUnknown = (page: Page) =>
  page.getByText(
    'The save outcome is unknown. Do not send it again. Read saved result for the original target and loadout.',
    { exact: true },
  );
const listsUnknown = (page: Page) => tournament(page).locator('[data-outcome="unknown"]');
const readRestore = (page: Page) =>
  page.getByRole('button', { name: 'Read saved result', exact: true });
const readLists = (page: Page) =>
  page.getByRole('button', { name: 'Read tournament settings', exact: true });
async function restoreDraft(page: Page) {
  await page.getByRole('combobox', { name: 'Cards', exact: true }).selectOption('main_farm');
  await page
    .getByRole('combobox', { name: 'Restore cards preset', exact: true })
    .selectOption('no_card');
}
async function accessibility(page: Page, width: number) {
  const violations = await axeCounts(page, WCAG_AA_TAGS);
  expect(violations).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(
    await undersizedControlTargets(page.locator('.tp-equipment'), {
      width: 24,
      height: 24,
      buttonHeight: width === 390 ? 44 : 24,
    }),
  ).toEqual([]);
}

for (const width of [1360, 1054, 390]) {
  for (const field of ['add', 'drop']) {
    test(`C15-6 C15-7 C-03 emptying only ${field} preserves the other list at ${String(width)}`, async ({
      page,
    }) => {
      await open(page, width, `empty_${field}`);
      const root = tournament(page).locator(`[data-field="${field}"]`);
      const sibling = tournament(page).locator(
        `[data-field="${field === 'add' ? 'drop' : 'add'}"]`,
      );
      await expect(root.getByRole('textbox').first()).toBeVisible();
      await expect(sibling.getByRole('textbox').first()).toBeVisible();
      const before = await sibling
        .getByRole('textbox')
        .evaluateAll((nodes) => nodes.map((node) => (node as HTMLInputElement).value));
      while (await root.getByRole('button', { name: /^Remove entry/ }).count()) {
        await enter(page, root.getByRole('button', { name: /^Remove entry/ }).first());
      }
      await enter(page, tournament(page).getByRole('button', { name: 'Save', exact: true }));
      await expect(tournament(page).locator('[data-outcome="saved"]')).toBeVisible();
      const suffix =
        field === 'add'
          ? 'saved_tournament_cards_with_only_the_add_list_empty_the_drop_list_is_kept'
          : 'saved_tournament_cards_with_only_the_drop_list_empty_the_add_list_is_kept';
      expect(JSON.parse((await posts(page))[0]?.body ?? '')).toEqual(
        ownPath(cases, 'settings_modules_more', suffix, 'request', 'body'),
      );
      await enter(page, readLists(page));
      await expect(root.getByRole('textbox')).toHaveCount(0);
      expect(
        await sibling
          .getByRole('textbox')
          .evaluateAll((nodes) => nodes.map((node) => (node as HTMLInputElement).value)),
      ).toEqual(before);
      expect(await posts(page)).toHaveLength(1);
      await accessibility(page, width);
      await screenshot(page, width, `only-${field}-empty`);
    });
  }
  for (const path of ['lists', 'cards']) {
    test(`C15-6 C15-7 C15-8 C15-9 C-03 C-04 ${path} loading and unavailable reads keep drafts and visible reasons at ${String(width)}`, async ({
      page,
    }) => {
      const pattern = path === 'lists' ? '**/api/settings' : '**/api/config';
      let release = () => {};
      const gate = new Promise<void>((resolve) => {
        release = resolve;
      });
      let held = false;
      const hold = async (route: Route) => {
        held = true;
        await gate;
        await route.continue();
      };
      if (path === 'cards') {
        await open(page, width, path);
        await restoreDraft(page);
      }
      await page.route(pattern, hold);
      if (path === 'lists') await open(page, width, path);
      const read = path === 'lists' ? readLists(page) : readRestore(page);
      const button =
        path === 'lists'
          ? tournament(page).getByRole('button', { name: 'Save', exact: true })
          : save(page);
      try {
        if (path === 'cards') await enter(page, read);
        await expect.poll(() => held).toBe(true);
        if (path === 'cards') {
          await expect(button).toBeDisabled();
          await expect(page.getByText('Reading saved equipment…', { exact: true })).toBeVisible();
        } else
          await expect(
            page.getByText(
              'Tournament settings have not been read for this context. Kept drafts stay with their original account.',
              { exact: true },
            ),
          ).toBeVisible();
        await accessibility(page, width);
        await screenshot(page, width, `${path}-loading`);
      } finally {
        release();
      }
      if (path === 'lists') await lists(page);
      await expect(button).toBeEnabled();
      await page.unroute(pattern, hold);
      await page.route(pattern, (route) => route.abort('failed'));
      await enter(page, read);
      if (path === 'lists') {
        await expect(
          page.getByText(
            'The latest tournament settings read failed. Read again before saving; your draft is kept.',
            { exact: true },
          ),
        ).toBeVisible();
        await expect(
          tournament(page).locator('[data-field="drop"]').getByRole('textbox').first(),
        ).toHaveValue('cash');
      } else {
        await expect(page.getByText(/Saved equipment could not be read/)).toBeVisible();
        await expect(
          page.getByRole('combobox', { name: 'Restore cards preset', exact: true }),
        ).toHaveValue('no_card');
      }
      await expect(button).toBeDisabled();
      await accessibility(page, width);
      await screenshot(page, width, `${path}-unavailable`);
      expect(await posts(page)).toHaveLength(0);
    });
    test(`C15-6 C15-7 C15-8 C15-9 C-03 C-04 ${path} context changes during Save send nothing and keep the old draft at ${String(width)}`, async ({
      page,
    }) => {
      await open(page, width, path);
      if (path === 'lists') await lists(page);
      else await restoreDraft(page);
      let release = () => {};
      const gate = new Promise<void>((resolve) => {
        release = resolve;
      });
      let held = false;
      let replied = false;
      await page.route(
        '**/api/config',
        async (route) => {
          held = true;
          await gate;
          await reply(route, 'settings_modules.read_config_after_saved_active_instance');
          replied = true;
        },
        { times: 1 },
      );
      try {
        await enter(
          page,
          path === 'lists'
            ? tournament(page).getByRole('button', { name: 'Save', exact: true })
            : save(page),
        );
        await expect.poll(() => held).toBe(true);
        expect(await posts(page)).toHaveLength(0);
        release();
        if (path === 'cards')
          await expect(
            page.getByText('Not saved: the account or profile changed', { exact: true }),
          ).toBeVisible();
        else {
          await expect.poll(() => replied).toBe(true);
          // The shared settings owner reports no-send through its command journal;
          // Equipment keeps its form and raw draft available for a later explicit Save.
          await page.evaluate(
            () =>
              new Promise<void>((resolve) =>
                requestAnimationFrame(() => {
                  resolve();
                }),
              ),
          );
        }
        expect(await posts(page)).toHaveLength(0);
        if (path === 'lists')
          await expect(
            tournament(page).locator('[data-field="drop"]').getByRole('textbox').first(),
          ).toHaveValue('cash');
        else
          await expect(
            page.getByRole('combobox', { name: 'Restore cards preset', exact: true }),
          ).toHaveValue('no_card');
        await accessibility(page, width);
        await screenshot(page, width, `${path}-context-changed-no-send`);
      } finally {
        release();
      }
    });
  }
  for (const failed of [false, true]) {
    const failure = failed ? 'failed' : 'lost';
    test(`C15-6 C15-7 C-03 C-04 ${failure} reply stays Unknown for unchanged and wrong-target reads, then positive readback preserves newer raw edits at ${String(width)}`, async ({
      page,
    }) => {
      await open(page, width, `lists_${failure}`);
      let reference = 'settings_modules.read_after_saved_run_defaults';
      await page.route('**/api/settings', async (route) => {
        if (route.request().method() === 'GET') await reply(route, reference);
        else await route.continue();
      });
      await lists(page);
      await enter(page, tournament(page).getByRole('button', { name: 'Save', exact: true }));
      await expect(listsUnknown(page)).toBeVisible();
      expect(await posts(page)).toHaveLength(1);
      const raw = tournament(page).locator('[data-field="drop"]').getByRole('textbox').first();
      await raw.fill('  newer raw list  ');
      await enter(page, readLists(page));
      await expect(listsUnknown(page)).toBeVisible();
      await expect(raw).toHaveValue('  newer raw list  ');
      await screenshot(page, width, `lists-${failure}-unchanged-unknown`);
      reference = 'settings_modules.read_after_saved_active_instance';
      await enter(page, readLists(page));
      await expect(tournament(page).getByRole('textbox')).toHaveCount(0);
      await expect(listsUnknown(page)).toBeVisible();
      await expect(
        tournament(page).getByRole('button', { name: 'Continue from the saved values as read' }),
      ).toHaveCount(0);
      await accessibility(page, width);
      await screenshot(page, width, `lists-${failure}-wrong-target-unknown`);
      reference = 'settings_modules.read_after_saved_tournament_cards';
      await enter(page, readLists(page));
      await expect(tournament(page).locator('[data-outcome="applied"]')).toBeVisible();
      await expect(raw).toHaveValue('  newer raw list  ');
      expect(await posts(page)).toHaveLength(1);
      await accessibility(page, width);
      await screenshot(page, width, `lists-${failure}-positive-newer-raw`);
    });
    test(`C15-8 C15-9 C-03 C-04 ${failure} restore reply stays Unknown for unchanged and wrong-target reads, then positive readback preserves raw rows at ${String(width)}`, async ({
      page,
    }) => {
      await open(page, width, `cards_${failure}`);
      let reference = 'loadout_patch.read_before_the_first_save';
      let wrongNext = false;
      await page.route('**/api/config', async (route) => {
        const selected = wrongNext
          ? 'settings_modules.read_config_after_saved_active_instance'
          : reference;
        wrongNext = false;
        await reply(route, selected);
      });
      await restoreDraft(page);
      await enter(page, save(page));
      await expect(restoreUnknown(page)).toBeVisible();
      expect(await posts(page)).toHaveLength(1);
      await enter(page, page.getByRole('button', { name: 'Add restore module', exact: true }));
      const raw = page.getByRole('textbox', { name: /Module name/ });
      await raw.fill('  newer unfinished module  ');
      await enter(page, readRestore(page));
      await expect(readRestore(page)).toBeEnabled();
      await expect(restoreUnknown(page)).toBeVisible();
      await expect(raw).toHaveValue('  newer unfinished module  ');
      await screenshot(page, width, `restore-${failure}-unchanged-unknown`);
      // Give only this explicit read the unrelated context; the global poll keeps its target.
      wrongNext = true;
      await enter(page, readRestore(page));
      await expect(page.getByText(/Saved equipment could not be read/)).toBeVisible();
      await expect(restoreUnknown(page)).toBeVisible();
      await expect(
        page.getByRole('button', { name: 'Keep draft against this read revision', exact: true }),
      ).toBeDisabled();
      await accessibility(page, width);
      await screenshot(page, width, `restore-${failure}-wrong-target-unknown`);
      reference = 'loadout_restore.read_after_saved_cards_restore';
      await enter(page, readRestore(page));
      await expect(page.getByText(/saved read contains the submitted selection/)).toBeVisible();
      await expect(raw).toHaveValue('  newer unfinished module  ');
      await expect(page.getByRole('textbox', { name: /Slot/ })).toHaveValue('');
      await expect(raw).toHaveAttribute('aria-invalid', 'true');
      expect(await posts(page)).toHaveLength(1);
      await accessibility(page, width);
      await screenshot(page, width, `restore-${failure}-positive-newer-raw`);
    });
  }
  test(`C15-6 C15-7 C-03 C-04 explicit acceptance keeps the earlier outcome Unknown and requires a separate Save at ${String(width)}`, async ({
    page,
  }) => {
    await open(page, width, 'lists_lost');
    await lists(page);
    await enter(page, tournament(page).getByRole('button', { name: 'Save', exact: true }));
    await expect(listsUnknown(page)).toBeVisible();
    await enter(page, readLists(page));
    await enter(
      page,
      tournament(page).getByRole('button', {
        name: 'Continue from the saved values as read',
        exact: true,
      }),
    );
    await expect(listsUnknown(page).locator('[data-accepted="true"]')).toBeVisible();
    await expect(
      tournament(page).locator('[data-outcome="applied"], [data-outcome="saved"]'),
    ).toHaveCount(0);
    expect(await posts(page)).toHaveLength(1);
    await accessibility(page, width);
    await screenshot(page, width, 'lists-accepted-unknown');
  });
  test(`C15-8 C15-9 C-03 C-04 explicit acceptance keeps Unknown history with no resend at ${String(width)}`, async ({
    page,
  }) => {
    await open(page, width, 'cards_lost');
    await restoreDraft(page);
    await enter(page, save(page));
    await expect(restoreUnknown(page)).toBeVisible();
    await enter(page, readRestore(page));
    await expect(readRestore(page)).toBeEnabled();
    await enter(
      page,
      page.getByRole('button', { name: 'Keep draft against this read revision', exact: true }),
    );
    await expect(
      page.getByText(
        'Continuing from the saved values as read. The earlier save outcome remains unknown. Saving requires a separate click.',
        { exact: true },
      ),
    ).toBeVisible();
    await expect(
      page.getByText(
        /Selection saved in configuration|saved read contains the submitted selection/,
      ),
    ).toHaveCount(0);
    await enter(page, readRestore(page));
    await expect(readRestore(page)).toBeEnabled();
    await expect(page.getByText(/earlier save outcome remains unknown/)).toBeVisible();
    expect(await posts(page)).toHaveLength(1);
    await accessibility(page, width);
    await screenshot(page, width, 'restore-accepted-unknown');
  });
  test(`C15-6 C15-7 C-03 explicit empty lists save as arrays and read back empty at ${String(width)}`, async ({
    page,
  }) => {
    await open(page, width, 'empty');
    await clearLists(page);
    await tournament(page).getByRole('button', { name: 'Save', exact: true }).click();
    await expect(tournament(page).getByText('Saved', { exact: true })).toBeVisible();
    const request: unknown = JSON.parse((await posts(page))[0]?.body ?? '');
    expect(request).toEqual(
      ownPath(
        cases,
        'settings_modules_more',
        'saved_tournament_cards_empty_lists',
        'request',
        'body',
      ),
    );
    await page.getByRole('button', { name: 'Read tournament settings', exact: true }).click();
    await expect(tournament(page).getByRole('textbox')).toHaveCount(0);
    // Emptying again changes nothing: keep Save disabled rather than automatically
    // replaying saved_the_same_empty_lists_again_keeps_the_revision.
    await expect(
      tournament(page).getByRole('button', { name: 'Save', exact: true }),
    ).toBeDisabled();
    await expect(tournament(page).getByText('No change to save', { exact: true })).toBeVisible();
    expect(await posts(page)).toHaveLength(1);
    await accessibility(page, width);
    await screenshot(page, width, 'lists-empty');
  });
  test(`C15-6 C15-7 C-03 tournament lists save and read back in order at ${String(width)}`, async ({
    page,
  }) => {
    await open(page, width, 'lists');
    await expect(
      page
        .locator('[data-region="equipment-tournament"]')
        .getByText('Tournament settings scope', { exact: true }),
    ).toBeVisible();
    await lists(page);
    const button = tournament(page).getByRole('button', { name: 'Save', exact: true });
    await button.focus();
    await page.keyboard.press('Enter');
    await expect(tournament(page).getByText('Saved', { exact: true })).toBeVisible();
    expect(JSON.parse((await posts(page))[0]?.body ?? '')).toEqual(
      ownPath(cases, 'settings_modules', 'saved_tournament_cards', 'request', 'body'),
    );
    await page.getByRole('button', { name: 'Read tournament settings', exact: true }).click();
    await expect(
      tournament(page).getByRole('textbox', { name: `${fieldLabel('drop')} Entry 2`, exact: true }),
    ).toHaveValue('extra_orb');
    expect(await posts(page)).toHaveLength(1);
    await accessibility(page, width);
    await screenshot(page, width, 'lists-saved');
  });
  test(`C15-6 C15-7 C-03 invalid list refusal keeps raw entries and server error at ${String(width)}`, async ({
    page,
  }) => {
    await open(page, width, 'invalid');
    await lists(page);
    await tournament(page).getByRole('button', { name: 'Save', exact: true }).click();
    await expect(
      tournament(page).getByText(
        String(own(body('settings_modules', 'invalid_string_list_tournament_cards'), 'error')),
        { exact: true },
      ),
    ).toBeVisible();
    await expect(
      tournament(page).getByRole('textbox', { name: `${fieldLabel('add')} Entry 1`, exact: true }),
    ).toHaveValue('golden_orb');
    expect(await posts(page)).toHaveLength(1);
    await accessibility(page, width);
    await screenshot(page, width, 'lists-invalid');
  });
  test(`C15-6 C15-7 C-03 stale refusal retains ordered draft at ${String(width)}`, async ({
    page,
  }) => {
    await open(page, width, 'stale');
    await lists(page);
    await tournament(page).getByRole('button', { name: 'Save', exact: true }).click();
    await expect(
      tournament(page).getByText(
        String(own(body('settings_modules', 'stale_revision_tournament_cards'), 'error')),
        { exact: true },
      ),
    ).toBeVisible();
    await expect(
      tournament(page).getByRole('textbox', { name: `${fieldLabel('drop')} Entry 1`, exact: true }),
    ).toHaveValue('cash');
    await screenshot(page, width, 'lists-stale');
  });
  test(`C15-8 C-03 restore cards save and readback at ${String(width)}`, async ({ page }) => {
    await open(page, width, 'cards');
    await page.getByRole('combobox', { name: 'Cards', exact: true }).selectOption('main_farm');
    await page
      .getByRole('combobox', { name: 'Restore cards preset', exact: true })
      .selectOption('no_card');
    await save(page).focus();
    await page.keyboard.press('Enter');
    await expect(page.getByText(/Selection saved in configuration/)).toBeVisible();
    expect(JSON.parse((await posts(page))[0]?.body ?? '')).toEqual(
      ownPath(cases, 'loadout_restore', 'saved_cards_restore', 'request', 'body'),
    );
    await page.getByRole('button', { name: 'Read saved result', exact: true }).click();
    await expect(page.getByText(/saved read contains the submitted selection/)).toBeVisible();
    await expect(
      page.getByRole('combobox', { name: 'Restore cards preset', exact: true }),
    ).toHaveValue('no_card');
    await accessibility(page, width);
    await screenshot(page, width, 'cards-saved');
  });
  test(`C15-9 r12f exact restore-only first save, warning and readback at ${String(width)}`, async ({
    page,
  }) => {
    await open(page, width, 'modules');
    await enter(page, page.getByRole('button', { name: 'Add restore module', exact: true }));
    const row = page.locator('.tp-equipment__restore-row');
    await row.getByRole('textbox', { name: /Module name/ }).fill('black_hole_digestor');
    await row.getByRole('textbox', { name: /Slot/ }).fill('assist');
    await save(page).click();
    await expect(page.getByText(/Selection saved in configuration/)).toBeVisible();
    const request = JSON.parse((await posts(page))[0]?.body ?? '{}') as { body: unknown };
    expect(request).toEqual(
      ownPath(cases, 'settings_modules_more_2', 'saved_modules_restore_only', 'request', 'body'),
    );
    for (const warning of list(
      own(body('settings_modules_more_2', 'saved_modules_restore_only'), 'warnings'),
    ) ?? []) {
      await expect(page.getByText(String(warning), { exact: true })).toBeVisible();
    }
    expect(own(request.body, 'modules')).toBeUndefined();
    await enter(page, readRestore(page));
    await expect(page.getByText(/saved read contains the submitted selection/)).toBeVisible();
    await expect(row.getByRole('textbox', { name: /Module name/ })).toHaveValue(
      'black_hole_digestor',
    );
    await expect(row.getByRole('textbox', { name: /Slot/ })).toHaveValue('assist');
    expect(await posts(page)).toHaveLength(1);
    await accessibility(page, width);
    await screenshot(page, width, 'r12f-modules-saved');
  });
  test(`C15-8 C15-9 F6-15-R1 empty blueprints disable restore Save with a visible reason and zero POSTs at ${String(width)}`, async ({
    page,
  }) => {
    await open(page, width, 'with_an_empty_blueprints_mapping');
    await page.getByRole('combobox', { name: 'Cards', exact: true }).selectOption('main_farm');
    await page
      .getByRole('combobox', { name: 'Restore cards preset', exact: true })
      .selectOption('no_card');
    await expect(save(page)).toHaveAttribute('aria-disabled', 'true');
    const reasonId = await save(page).getAttribute('aria-describedby');
    expect(reasonId).not.toBeNull();
    const reason = page.locator(`[id="${reasonId ?? ''}"]`);
    await expect(reason).toBeVisible();
    await expect(reason).toHaveText(
      'Restore changes cannot be saved: use this loadout in a run of the active profile first.',
    );
    // The genuine empty-map refusal records restore_loadout_unused. Its server
    // message is not local copy; the existing catalogue reason explains this block.
    expect(
      own(
        body(
          'settings_modules_more_2',
          'restore_refused_for_a_profile_with_an_empty_blueprints_mapping',
        ),
        'code',
      ),
    ).toBe('restore_loadout_unused');
    await enter(page, save(page));
    expect(await posts(page)).toHaveLength(0);
    await expect(
      page.getByRole('combobox', { name: 'Restore cards preset', exact: true }),
    ).toHaveValue('no_card');
    await accessibility(page, width);
    await screenshot(page, width, 'F6-15-R1-empty-blueprints-blocked');
  });
  for (const shape of ['without_blueprints', 'with_blueprints_null', 'with_blueprints_a_list']) {
    test(`C15-8 C15-9 F6-15-R1 ${shape} defers, refuses verbatim and keeps draft at ${String(width)}`, async ({
      page,
    }) => {
      await open(page, width, shape);
      await page.getByRole('combobox', { name: 'Cards', exact: true }).selectOption('main_farm');
      await page
        .getByRole('combobox', { name: 'Restore cards preset', exact: true })
        .selectOption('no_card');
      await expect(save(page)).toBeEnabled();
      await enter(page, save(page));
      const key = `restore_refused_for_a_profile_${shape}`;
      await expect(
        page
          .getByText(String(own(body('settings_modules_more_2', key), 'error')), { exact: true })
          .first(),
      ).toBeVisible();
      expect(JSON.parse((await posts(page))[0]?.body ?? '')).toEqual(
        ownPath(cases, 'settings_modules_more_2', key, 'request', 'body'),
      );
      await enter(page, readRestore(page));
      await expect(
        page.getByRole('combobox', { name: 'Restore cards preset', exact: true }),
      ).toHaveValue('no_card');
      await expect(
        page
          .getByText(String(own(body('settings_modules_more_2', key), 'error')), { exact: true })
          .first(),
      ).toBeVisible();
      expect(await posts(page)).toHaveLength(1);
      await accessibility(page, width);
      await screenshot(page, width, `r12f-${shape}-refused`);
    });
  }
  test(`C15-6 C15-7 r12f raw malformed list survives sibling save and explicit repair settles at ${String(width)}`, async ({
    page,
  }) => {
    await open(page, width, 'malformed');
    const drop = tournament(page).locator('[data-field="drop"]');
    await expect(drop.getByRole('textbox').nth(0)).toHaveValue('false');
    await expect(drop.getByRole('textbox').nth(1)).toHaveValue('7');
    await expect(drop.getByRole('textbox').nth(2)).toHaveValue('keep');
    await add(page, 'add', ['golden_orb']);
    const button = tournament(page).getByRole('button', { name: 'Save', exact: true });
    await enter(page, button);
    await expect(tournament(page).getByText('Saved', { exact: true })).toBeVisible();
    expect(JSON.parse((await posts(page))[0]?.body ?? '')).toEqual(
      ownPath(
        cases,
        'settings_modules_more_2',
        'saved_tournament_cards_add_list_only_the_malformed_drop_list_stays',
        'request',
        'body',
      ),
    );
    await page.getByRole('button', { name: 'Read tournament settings', exact: true }).click();
    await expect(drop.getByRole('textbox').nth(0)).toHaveValue('false');
    await expect(drop.getByRole('textbox').nth(1)).toHaveValue('7');
    await screenshot(page, width, 'r12f-malformed-sibling-saved');
    // Full recorded replies; only select which recorded transition follows the next click.
    const repairRequests: unknown[] = [];
    await page.route('**/api/settings', async (route) => {
      if (route.request().method() === 'POST') repairRequests.push(route.request().postDataJSON());
      await reply(
        route,
        `settings_modules_more_2.${route.request().method() === 'POST' ? 'saved_tournament_cards_repairing_the_malformed_drop_list' : repairRequests.length === 0 ? 'read_after_the_add_list_was_saved_the_malformed_drop_list_remains' : 'read_after_the_malformed_drop_list_was_repaired'}`,
      );
    });
    for (let i = 0; i < 2; i += 1)
      await enter(page, drop.getByRole('button', { name: /^Remove entry/ }).first());
    await enter(page, button);
    await expect(tournament(page).getByText('Saved', { exact: true })).toBeVisible();
    await expect
      .poll(() => repairRequests)
      .toEqual([
        ownPath(
          cases,
          'settings_modules_more_2',
          'saved_tournament_cards_repairing_the_malformed_drop_list',
          'request',
          'body',
        ),
      ]);
    await page.getByRole('button', { name: 'Read tournament settings', exact: true }).click();
    await expect(drop.getByRole('textbox')).toHaveCount(1);
    await expect(drop.getByRole('textbox')).toHaveValue('keep');
    await expect(button).toBeDisabled();
    // The intercepted repair is counted separately from the fake-backend POST log.
    expect(await posts(page)).toHaveLength(1);
    expect(repairRequests).toEqual([
      ownPath(
        cases,
        'settings_modules_more_2',
        'saved_tournament_cards_repairing_the_malformed_drop_list',
        'request',
        'body',
      ),
    ]);
    await accessibility(page, width);
    await screenshot(page, width, 'r12f-malformed-repaired');
  });
  test(`C15-9 C-03 editing recorded restore rows preserves manual fields and a lost answer stays Unknown at ${String(width)}`, async ({
    page,
  }) => {
    await open(page, width, 'modules_manual');
    await page.getByRole('button', { name: /Remove restore module/ }).click();
    await page.getByRole('button', { name: 'Add restore module', exact: true }).click();
    const row = page.locator('.tp-equipment__restore-row');
    await row.getByRole('textbox', { name: /Module name/ }).fill('black_hole_digestor');
    await row.getByRole('textbox', { name: /Slot/ }).fill('primary');
    await enter(page, save(page));
    await expect(restoreUnknown(page)).toBeVisible();
    expect(JSON.parse((await posts(page))[0]?.body ?? '')).toEqual({
      ...Object(ownPath(cases, 'loadout_restore', 'saved_modules_restore', 'request', 'body')),
      expected_revision: own(
        body('loadout_restore', 'read_after_saved_modules_restore'),
        'config_revision',
      ),
      body: {
        ...Object.fromEntries(
          entries(
            ownPath(cases, 'loadout_restore', 'saved_modules_restore', 'request', 'body', 'body'),
          ),
        ),
        modules_restore: [['black_hole_digestor', 'primary']],
      },
    });
    await enter(page, readRestore(page));
    await expect(restoreUnknown(page)).toBeVisible();
    await expect(page.getByText(/Manual equipment selections are preserved here/)).toBeVisible();
    expect(await posts(page)).toHaveLength(1);
    await accessibility(page, width);
    await screenshot(page, width, 'modules-manual-preserved');
  });
  test(`C15-9 C-03 restore refused verbatim without losing the raw module at ${String(width)}`, async ({
    page,
  }) => {
    await open(page, width, 'refused');
    await page.getByRole('textbox', { name: /Module name/ }).fill('ghost_module');
    await save(page).click();
    await expect(page.getByRole('textbox', { name: /Module name/ })).toHaveValue('ghost_module');
    await expect(
      page
        .getByText(String(own(body('loadout_restore', 'refused_modules_restore'), 'error')), {
          exact: true,
        })
        .first(),
    ).toBeVisible();
    expect(JSON.parse((await posts(page))[0]?.body ?? '')).toEqual(
      ownPath(cases, 'loadout_restore', 'refused_modules_restore', 'request', 'body'),
    );
    await accessibility(page, width);
    await screenshot(page, width, 'restore-refused');
  });
  test(`C15-8 C15-9 C-03 unused loadout and clearing restore are disabled with a reason at ${String(width)}`, async ({
    page,
  }) => {
    for (const scenario of ['unused', 'clear_unused']) {
      await open(page, width, scenario, 'spare');
      await page
        .getByRole('combobox', { name: 'Restore cards preset', exact: true })
        .selectOption(scenario === 'unused' ? 'main_farm' : '');
      await expect(save(page), await page.locator('main').innerText()).toBeDisabled();
      await expect(
        page
          .getByText(
            'Restore changes cannot be saved: use this loadout in a run of the active profile first.',
            { exact: true },
          )
          .first(),
      ).toBeVisible();
      await save(page).focus();
      await page.keyboard.press('Enter');
      expect(await posts(page)).toHaveLength(0);
      await accessibility(page, width);
      await screenshot(
        page,
        width,
        scenario === 'unused' ? 'unused-disabled' : 'clear-unused-disabled',
      );
    }
  });
  test(`C15-8 C-03 no active profile keeps restore save disabled at ${String(width)}`, async ({
    page,
  }) => {
    await open(page, width, 'no_profile');
    await expect(save(page)).toBeDisabled();
    await expect(
      page.getByText(
        'Restore changes cannot be saved: activate a profile that uses this loadout first.',
        { exact: true },
      ),
    ).toBeVisible();
    expect(await posts(page)).toHaveLength(0);
    await accessibility(page, width);
    await screenshot(page, width, 'no-profile-disabled');
  });
}
