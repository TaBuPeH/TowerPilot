import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { axeCounts, posts, WCAG_AA_TAGS } from './helpers/helpers.ts';
import { loadJsonWithIdentity } from '../../scripts/contract-fixture.mjs';
import { EQUIPMENT_FIXTURE_PATH } from '../support/equipment-fixtures.mjs';
import { own, ownPath, record } from '../../src/core/read.ts';
import { contextFromConfig } from '../../src/api/context.ts';
import { undersizedControlTargets } from './helpers/target-box.ts';

const OUT = path.resolve('../../backend/logs/reviews/frontend-f6-screens');
mkdirSync(OUT, { recursive: true });
const SAMPLES = own(loadJsonWithIdentity(EQUIPMENT_FIXTURE_PATH).fixture, 'cases');
const widths = [1360, 1054, 390];
async function open(page: Page, width: number, options: Record<string, string> = {}) {
  await page.setViewportSize({ width, height: 900 });
  await page.request.get('/__fake/use?scenario=runs_saved');
  await page.request.get(`/__fake/equipment?${new URLSearchParams(options)}`);
  await page.goto('/ui/app/#equipment');
  await expect(
    page.getByRole('heading', { level: 1, name: 'Equipment', exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Read saved result', exact: true })).toBeEnabled();
  if (options['view'] !== 'empty')
    await page.getByRole('button', { name: 'plain', exact: true }).click();
}
const save = (page: Page) => page.getByRole('button', { name: 'Save selection', exact: true });
async function global(page: Page) {
  await page.getByRole('combobox', { name: 'Selection mode', exact: true }).selectOption('global');
  await page
    .getByRole('combobox', { name: 'Global preset selection', exact: true })
    .selectOption('Farm Run');
}
async function shot(page: Page, label: string, width: number) {
  await page.screenshot({
    path: path.join(OUT, `equipment-${label}-${width}.png`),
    fullPage: true,
  });
}
async function accessibility(page: Page) {
  const violations = await axeCounts(page, WCAG_AA_TAGS);
  expect(violations).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(
    await undersizedControlTargets(page.locator('.tp-equipment'), { width: 24, height: 24 }),
  ).toEqual([]);
}

for (const width of widths) {
  test(`E4 r10 matching context saves once and reads the persisted result at ${width}`, async ({
    page,
  }) => {
    await open(page, width, {
      config: 'equipment_samples.context_config',
      profile: 'equipment_samples.context_profile',
      saves: 'equipment_samples.context_match_saved',
    });
    await global(page);
    await save(page).click();
    await expect(page.getByText(/Selection saved in configuration/)).toBeVisible();
    const sent = await posts(page);
    expect(sent).toHaveLength(1);
    expect(JSON.parse(sent[0]?.body ?? '')).toEqual(
      ownPath(SAMPLES, 'equipment_samples', 'context_match_saved', 'request', 'body'),
    );
    await page.getByRole('button', { name: 'Read saved result', exact: true }).click();
    await expect(page.getByText(/saved read contains the submitted selection/)).toBeVisible();
    expect(await posts(page)).toHaveLength(1);
    await accessibility(page);
    await shot(page, 'r10-context-match', width);
  });
  test(`E4 r10 malformed context refusals keep the raw draft and never resend at ${width}`, async ({
    page,
  }) => {
    for (const name of [
      'null',
      'missing_fields',
      'extra_field',
      'wrong_value_type',
      'not_an_object',
    ]) {
      await open(page, width, {
        config: 'equipment_samples.context_config',
        profile: 'equipment_samples.context_profile',
        saves: `equipment_samples.context_malformed_${name}`,
      });
      await global(page);
      await save(page).click();
      await expect(
        page.getByText('The backend refused this save. Your draft is kept.', { exact: true }),
      ).toBeVisible();
      await expect(
        page.getByRole('combobox', { name: 'Global preset selection', exact: true }),
      ).toHaveValue('Farm Run');
      expect(await posts(page)).toHaveLength(1);
    }
    await shot(page, 'r10-malformed-context', width);
  });
  test(`E4 r10 a backend context change after preflight shows the current target and keeps the draft at ${width}`, async ({
    page,
  }) => {
    await open(page, width, {
      config: 'equipment_samples.context_changed_config',
      profile: 'equipment_samples.context_changed_profile',
      saves: 'equipment_samples.context_changed_refused',
      readback: 'equipment_samples.context_changed_readback',
    });
    await page
      .getByRole('combobox', { name: 'Selection mode', exact: true })
      .selectOption('category');
    await page.getByRole('combobox', { name: 'Cards', exact: true }).selectOption('main_farm');
    let posted = false;
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route('**/api/config', async (route) => {
      if (posted) await gate;
      await route.continue();
    });
    await page.route('**/api/loadout-patch', async (route) => {
      posted = true;
      await route.continue();
    });
    try {
      await save(page).click();
      await expect(
        page.getByText('Not saved: the account or profile changed', { exact: true }),
      ).toBeVisible();
      await expect(
        page
          .locator('.tp-equipment__result')
          .filter({ hasText: 'Current context reported by the server' }),
      ).toContainText('theirs');
      await expect(page.getByRole('combobox', { name: 'Cards', exact: true })).toHaveValue(
        'main_farm',
      );
      await expect(save(page)).toBeDisabled();
      const sent = await posts(page);
      expect(sent).toHaveLength(1);
      expect(JSON.parse(sent[0]?.body ?? '')).toEqual(
        ownPath(SAMPLES, 'equipment_samples', 'context_changed_refused', 'request', 'body'),
      );
      await accessibility(page);
      await shot(page, 'r10-context-changed', width);
    } finally {
      release?.();
      await page.unroute('**/api/config');
    }
  });
  test(`E4 r10 an unavailable current context is explicit and keeps the original draft at ${width}`, async ({
    page,
  }) => {
    // A genuine shared fence response recorded on the tier route, replayed here
    // to check Equipment's presentation without inventing a backend answer.
    await open(page, width, {
      config: 'equipment_samples.context_changed_config',
      profile: 'equipment_samples.context_changed_profile',
      saves: 'equipment_samples.context_unknown_refused',
    });
    await page
      .getByRole('combobox', { name: 'Selection mode', exact: true })
      .selectOption('category');
    await page.getByRole('combobox', { name: 'Cards', exact: true }).selectOption('main_farm');
    await save(page).click();
    await expect(
      page.getByText('Not saved: the account or profile changed', { exact: true }),
    ).toBeVisible();
    const reported = page
      .locator('.tp-equipment__result')
      .filter({ hasText: 'Current context reported by the server' });
    await expect(reported).toContainText('Current context reported by the server');
    await expect(reported.locator('pre')).toHaveText('null');
    await expect(page.getByRole('combobox', { name: 'Cards', exact: true })).toHaveValue(
      'main_farm',
    );
    await expect(save(page)).toBeDisabled();
    expect(await posts(page)).toHaveLength(1);
    await accessibility(page);
    await shot(page, 'r10-context-unavailable', width);
    await page.reload();
    await expect(page.locator('.tp-equipment__result pre')).toHaveText('null');
    await expect(page.getByRole('combobox', { name: 'Cards', exact: true })).toHaveValue(
      'main_farm',
    );
    expect(await posts(page)).toHaveLength(1);
  });
  test(`E4 F6-EQUIPMENT-01 newer read survives a delayed older save answer at ${width}`, async ({
    page,
  }) => {
    await open(page, width, {
      delay: '1000',
      readback: 'loadout_patch.read_after_the_saves_holds_every_loadout',
    });
    await global(page);
    await save(page).click();
    await expect.poll(() => posts(page)).toHaveLength(1);
    await page.getByRole('button', { name: 'Read saved result', exact: true }).click();
    await expect(page.getByText(/saved configuration changed\. Read saved result/)).toBeVisible();
    await expect(
      page.getByRole('combobox', { name: 'Global preset selection', exact: true }),
    ).toHaveValue('Farm Run');
    await expect(save(page)).toBeDisabled();
    await page.getByText('Review before and after', { exact: true }).click();
    await expect(page.locator('.tp-equipment__compare')).toContainText('Full Upgraded');
    expect(await posts(page)).toHaveLength(1);
    await shot(page, 'newer-read-late-answer', width);
  });
  test(`E4 loading and stale reads keep the draft and block Save at ${width}`, async ({ page }) => {
    await page.clock.install();
    await open(page, width);
    await page.getByRole('combobox', { name: 'Cards', exact: true }).selectOption('main_farm');
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route('**/api/profile-src/mine', async (route) => {
      await gate;
      await route.continue();
    });
    await page.getByRole('button', { name: 'Read saved result', exact: true }).click();
    await expect(page.getByText('Reading saved equipment…', { exact: true })).toBeVisible();
    await expect(save(page)).toBeDisabled();
    await shot(page, 'loading-read', width);
    release?.();
    await expect(
      page.getByRole('button', { name: 'Read saved result', exact: true }),
    ).toBeEnabled();
    await page.unroute('**/api/profile-src/mine');
    await page.clock.fastForward(31_001);
    await expect(page.getByText(/This read is more than 30 seconds old/)).toBeVisible();
    await expect(save(page)).toBeDisabled();
    await expect(page.getByRole('combobox', { name: 'Cards', exact: true })).toHaveValue(
      'main_farm',
    );
    await shot(page, 'stale-read', width);
    expect(await posts(page)).toHaveLength(0);
  });
  test(`E1 E5 mixed rows, keyboard help, explicit target and axe at ${width}`, async ({ page }) => {
    await open(page, width, { view: 'mixed' });
    await expect(page.getByText('Placeholder — equips nothing')).toBeVisible();
    await expect(page.getByRole('combobox', { name: 'Selection mode', exact: true })).toHaveValue(
      'manual',
    );
    await expect(page.getByRole('link', { name: 'farm_deep', exact: true })).toHaveAttribute(
      'href',
      '#runs/saved/farm_deep',
    );
    const help = page.getByRole('button', { name: 'About equipment selection', exact: true });
    await help.focus();
    await expect(
      page.getByRole('tooltip').getByText(/A global preset selects the game/),
    ).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('tooltip')).toHaveCount(0);
    await expect(help).toBeFocused();
    await accessibility(page);
    await shot(page, 'mixed-help', width);
    expect(await posts(page)).toHaveLength(0);
  });
  test(`E1 E2 empty and missing discoveries stay explicit without launch at ${width}`, async ({
    page,
  }) => {
    await open(page, width, { view: 'empty' });
    await expect(page.getByText(/No saved loadouts/)).toBeVisible();
    await accessibility(page);
    await shot(page, 'empty', width);
    await page.request.get('/__fake/equipment?view=missing');
    await page.reload();
    await page.getByRole('button', { name: 'plain', exact: true }).click();
    await expect(page.getByRole('combobox', { name: 'Cards', exact: true })).toHaveValue(
      'undiscovered_saved_choice',
    );
    await expect(page.getByRole('combobox', { name: 'Cards', exact: true })).toHaveAttribute(
      'aria-invalid',
      'true',
    );
    await expect(save(page)).toBeDisabled();
    const region = page.getByRole('region', { name: /^Selected loadout/ });
    await expect(region.getByRole('link', { name: 'Open Recognition' })).toHaveAttribute(
      'href',
      '#recognition',
    );
    await accessibility(page);
    await shot(page, 'missing-discovery', width);
    expect(await posts(page)).toHaveLength(0);
  });
  test(`E2 E3 all category saves and reviewed manual conversion use exact route bodies at ${width}`, async ({
    page,
  }) => {
    await open(page, width, { view: 'manual' });
    await global(page);
    await expect(save(page)).toBeDisabled();
    await expect(
      page.getByRole('heading', { name: 'Fields removed by this selection' }),
    ).toBeVisible();
    await page.getByLabel('I reviewed the removed manual fields').check();
    await page.getByLabel('I reviewed the removed manual fields').uncheck();
    await expect(save(page)).toBeDisabled();
    await page.getByLabel('I reviewed the removed manual fields').check();
    await accessibility(page);
    await shot(page, 'conversion-review', width);
    await save(page).focus();
    await page.keyboard.press('Enter');
    await expect(page.getByText(/Selection saved in configuration/)).toBeVisible();
    await page
      .getByRole('combobox', { name: 'Selection mode', exact: true })
      .selectOption('category');
    await page.getByRole('combobox', { name: 'Cards', exact: true }).selectOption('');
    await page.getByRole('combobox', { name: 'Modules', exact: true }).selectOption('Farm');
    await page.getByRole('combobox', { name: 'Guardians', exact: true }).selectOption('Farm');
    await save(page).click();
    await expect(page.getByText(/Selection saved in configuration/)).toBeVisible();
    await page.getByRole('combobox', { name: 'Modules', exact: true }).selectOption('');
    await page.getByRole('combobox', { name: 'Guardians', exact: true }).selectOption('');
    await page.getByRole('combobox', { name: 'Cards', exact: true }).selectOption('main_farm');
    await save(page).click();
    await expect(page.getByText(/Selection saved in configuration/)).toBeVisible();
    await page.getByRole('combobox', { name: 'Cards', exact: true }).selectOption('');
    await expect(save(page)).toBeDisabled();
    await page
      .getByRole('combobox', { name: 'Workshop', exact: true })
      .selectOption('Full Upgraded');
    await page.getByRole('combobox', { name: 'Bots', exact: true }).selectOption('Farm');
    await save(page).click();
    await expect(page.getByText(/Selection saved in configuration/)).toBeVisible();
    const sent = await posts(page);
    const currentConfig = await page.request.get('/api/config');
    const context = contextFromConfig(await currentConfig.json());
    expect(context).not.toBeNull();
    expect(sent).toHaveLength(4);
    ['global', 'categories', 'cards', 'workshop_bots'].forEach((step, index) => {
      const expected = record(
        ownPath(SAMPLES, 'equipment_samples', `manual_${step}_saved`, 'request', 'body'),
      );
      if (expected === undefined) throw new Error(`Missing recorded request ${step}`);
      expect(JSON.parse(sent[index]?.body ?? '')).toEqual({
        ...expected,
        expected_context: context,
      });
    });
    await page.getByRole('button', { name: 'Read saved result', exact: true }).click();
    await expect(page.getByText(/saved read contains the submitted selection/)).toBeVisible();
    await expect(page.getByRole('combobox', { name: 'Workshop', exact: true })).toHaveValue(
      'Full Upgraded',
    );
    await shot(page, 'categories-saved-read', width);
    expect(await posts(page)).toHaveLength(4);
  });
  test(`E3 E5 Cancel restores manual body, modal traps/restores focus and Escape keeps edits at ${width}`, async ({
    page,
  }) => {
    await open(page, width, { view: 'manual' });
    await global(page);
    const cancel = page.getByRole('button', { name: 'Cancel edits', exact: true });
    await cancel.focus();
    await page.keyboard.press('Enter');
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByRole('button', { name: 'Keep editing' })).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(dialog.getByRole('button', { name: 'Cancel edits' })).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(dialog.getByRole('button', { name: 'Keep editing' })).toBeFocused();
    for (let step = 0; step < 4; step += 1) {
      await page.keyboard.press('Tab');
      expect(
        await page.evaluate(
          () =>
            document.activeElement === document.body ||
            document.querySelector('dialog[open]')?.contains(document.activeElement),
        ),
      ).toBe(true);
    }
    await page.keyboard.press('Escape');
    await expect(cancel).toBeFocused();
    await expect(
      page.getByRole('combobox', { name: 'Global preset selection', exact: true }),
    ).toHaveValue('Farm Run');
    await cancel.click();
    await dialog.getByRole('button', { name: 'Cancel edits' }).click();
    await expect(page.getByRole('combobox', { name: 'Selection mode', exact: true })).toHaveValue(
      'manual',
    );
    await page.getByText('Review before and after', { exact: true }).click();
    await expect(page.locator('.tp-equipment__compare pre').first()).toContainText(
      'amplifying_strike',
    );
    expect(await posts(page)).toHaveLength(0);
    await shot(page, 'manual-cancel', width);
  });
  test(`E4 late save answer preserves newer edits, navigation and focused controls at ${width}`, async ({
    page,
  }) => {
    await open(page, width, { delay: '700' });
    await global(page);
    await save(page).click();
    await expect(save(page)).toBeDisabled();
    await page
      .getByRole('combobox', { name: 'Global preset selection', exact: true })
      .selectOption('Tournament');
    await page.getByRole('combobox', { name: 'Global preset selection', exact: true }).focus();
    await expect(page.getByText(/Selection saved in configuration/)).toBeVisible();
    await expect(
      page.getByRole('combobox', { name: 'Global preset selection', exact: true }),
    ).toBeFocused();
    await expect(
      page.getByRole('combobox', { name: 'Global preset selection', exact: true }),
    ).toHaveValue('Tournament');
    await page.getByRole('button', { name: 'spare', exact: true }).click();
    await expect(page.getByText(/Selection saved in configuration/)).toHaveCount(0);
    await page.getByRole('button', { name: 'plain', exact: true }).click();
    await expect(
      page.getByRole('combobox', { name: 'Global preset selection', exact: true }),
    ).toHaveValue('Tournament');
    await page.getByRole('link', { name: 'farm_deep', exact: true }).click();
    await expect(page).toHaveURL(/#runs\/saved\/farm_deep$/);
    await page.goBack();
    await expect(
      page.getByRole('combobox', { name: 'Global preset selection', exact: true }),
    ).toHaveValue('Tournament');
    expect(await posts(page)).toHaveLength(1);
    await shot(page, 'late-answer-newer-draft', width);
  });
  test(`E4 lost answer and failed readback resolve by read after reload with one POST at ${width}`, async ({
    page,
  }) => {
    await open(page, width, {
      mode: 'drop',
      saves: 'loadout_patch.written_then_the_answer_failed_500',
      readback: 'config_state.file_missing_and_no_example_500',
    });
    await page.getByRole('combobox', { name: 'Cards', exact: true }).selectOption('main_farm');
    await save(page).click();
    await expect(page.getByText(/save outcome is unknown/)).toBeVisible();
    await page.getByRole('combobox', { name: 'Cards', exact: true }).selectOption('no_card');
    await page.getByRole('button', { name: 'Read saved result', exact: true }).click();
    await expect(page.getByText(/Saved equipment could not be read/)).toBeVisible();
    await expect(page.getByRole('combobox', { name: 'Cards', exact: true })).toHaveValue('no_card');
    await accessibility(page);
    await shot(page, 'unknown-failed-read', width);
    await page.request.get(
      '/__fake/equipment?config=loadout_patch.read_after_an_error_that_followed_the_write_holds_the_change',
    );
    await page.reload();
    await expect(page.getByText(/saved read contains the submitted selection/)).toBeVisible();
    await expect(page.getByRole('combobox', { name: 'Cards', exact: true })).toHaveValue('no_card');
    expect(await posts(page)).toHaveLength(1);
    await shot(page, 'lost-answer-read-settled', width);
  });
  test(`E4 stale revision and validator failure keep exact drafts and never resend at ${width}`, async ({
    page,
  }) => {
    await open(page, width, { saves: 'loadout_patch.stale_revision_409' });
    await page.getByRole('combobox', { name: 'Cards', exact: true }).selectOption('main_farm');
    await save(page).click();
    await expect(page.getByText(/saved configuration changed\. Read saved result/)).toBeVisible();
    await expect(save(page)).toBeDisabled();
    await shot(page, 'revision-conflict', width);
    await page.getByRole('button', { name: 'Read saved result', exact: true }).click();
    await page.getByRole('button', { name: 'Keep draft against this read revision' }).click();
    await expect(page.getByRole('combobox', { name: 'Cards', exact: true })).toHaveValue(
      'main_farm',
    );
    await page.request.get('/__fake/equipment?saves=loadout_patch.validator_unavailable_500');
    await save(page).click();
    await expect(page.getByText(/save outcome is unknown/)).toBeVisible();
    await expect(page.getByText(/validator unavailable:/)).toBeVisible();
    expect(await posts(page)).toHaveLength(2);
    await shot(page, 'validator-unknown', width);
  });
  test(`E1 E5 long names wrap and keyboard navigation stays usable at ${width}`, async ({
    page,
  }) => {
    await open(page, width, { view: 'long' });
    await expect(
      page.getByRole('link', { name: 'unbreakable'.repeat(40), exact: true }),
    ).toBeVisible();
    await accessibility(page);
    await shot(page, 'long-content', width);
    expect(await posts(page)).toHaveLength(0);
  });
}
