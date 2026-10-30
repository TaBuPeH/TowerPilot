import { withRecordedFence } from '../support/profilePatchFenced.ts';
import type { DashboardCase } from '../support/dashboardFixture.ts';
import { contextFromConfig } from '../../src/api/context.ts';
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page, type Locator } from '@playwright/test';
import { own, ownPath, entries } from '../../src/core/read.ts';
import { loadJsonWithIdentity } from '../../scripts/contract-fixture.mjs';
import { ABILITIES_FIXTURE_PATH } from '../support/abilities-fixtures.mjs';
import { axeCounts, posts, WCAG_AA_TAGS } from './helpers/helpers.ts';

const CASES = own(loadJsonWithIdentity(ABILITIES_FIXTURE_PATH).fixture, 'cases');
const fixture = (view: string, suffix = 'saved') => {
  // Supplementary legacy mode/rule variants have no profile_patch_fenced recording.
  // Keep their exact recorded bodies plus the runtime fence; the new-family suite binds UW/rescue.
  const found = ownPath(CASES, 'abilities_samples', `${view}_${suffix}`) as DashboardCase;
  const context = contextFromConfig(ownPath(CASES, 'abilities_samples', `${view}_config`, 'body'));
  return context === null ? found : withRecordedFence(found, context);
};
const OUT = path.resolve('../../backend/logs/reviews/frontend-f6-screens');
mkdirSync(OUT, { recursive: true });
const widths = [1360, 1054, 390];
let opens = 0;
async function open(page: Page, width: number, options: Record<string, string> = {}) {
  // Stop the previous document's poller before replacing this browser session's fixture world.
  await page.goto('about:blank');
  await page.addInitScript(() => {
    if (location.origin === 'null') return;
    const key = 'tp.abilities-e2e-case';
    if (sessionStorage.getItem(key) !== location.search) {
      sessionStorage.clear();
      sessionStorage.setItem(key, location.search);
    }
  });
  await page.setViewportSize({ width, height: 900 });
  await page.request.get('/__fake/use?scenario=runs_saved');
  await page.request.get(`/__fake/abilities?${new URLSearchParams(options)}`);
  await page.goto(`/ui/app/?abilities-test=${++opens}#abilities`);
  await expect(
    page.getByRole('heading', { level: 1, name: 'Abilities', exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Plan label', exact: true })).toBeVisible();
}
const save = (page: Page) => page.getByRole('button', { name: 'Save plan', exact: true });
const read = (page: Page) => page.getByRole('button', { name: 'Read saved result', exact: true });
async function world(page: Page, options: Record<string, string>) {
  await page.request.get(`/__fake/abilities?${new URLSearchParams(options)}`);
}
async function targetWorld(page: Page, options: Record<string, string>, profile: string) {
  await world(page, options);
  // Config has no periodic read. Becoming visible explicitly refreshes its target.
  await page.evaluate(() => {
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(page.locator('.tp-abilities__target')).toContainText(profile);
}
const review = (page: Page) =>
  page
    .getByRole('checkbox', { name: 'I reviewed the mode or rule change and its removed fields' })
    .check();
async function rescue(page: Page) {
  await page
    .getByRole('combobox', { name: 'Saved plans', exact: true })
    .selectOption('rescue_policies');
  await page.getByRole('button', { name: 'Wall drain', exact: true }).click();
}
async function shot(page: Page, label: string, width: number) {
  await page.screenshot({
    path: path.join(OUT, `abilities-${label}-${width}.png`),
    fullPage: true,
  });
}
async function accessibility(page: Page) {
  const result = await axeCounts(page, WCAG_AA_TAGS);
  expect(result).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(
    await page.locator('.tp-abilities').evaluate((root) =>
      [...root.querySelectorAll('button,select,input,summary')]
        .filter((node) => {
          const r = node.getBoundingClientRect();
          return r.width > 0 && r.height > 0 && (r.width < 24 || r.height < 24);
        })
        .map((n) => n.id),
    ),
  ).toEqual([]);
}
async function submitted(page: Page, view: string) {
  await expect(
    page.getByText('Saved in the profile. No game action was performed.', { exact: true }),
  ).toBeVisible();
  const writes = await posts(page);
  expect(writes).toHaveLength(1);
  expect(JSON.parse(writes[0]?.body ?? '')).toEqual(ownPath(fixture(view), 'request', 'body'));
  await read(page).click();
  await expect(read(page)).toBeEnabled();
}
async function fillSpec(
  root: Page | Locator,
  label: string,
  spec: unknown,
  value: unknown,
): Promise<void> {
  const type = own(spec, 'type');
  if (type === 'object') {
    const group = root.getByRole('group', { name: label, exact: true });
    for (const [key, field] of entries(own(spec, 'fields')))
      if (own(value, key) !== undefined)
        await fillSpec(group, key.replaceAll('_', ' '), field, own(value, key));
  } else if (type === 'list') {
    const group = root.getByRole('group', { name: label, exact: true });
    const values = Array.isArray(value) ? value : [];
    await group.getByRole('textbox', { name: /^Minimum/ }).fill(String(values[0]));
    await group.getByRole('textbox', { name: /^Maximum/ }).fill(String(values[1]));
  } else {
    const field = root.getByRole(type === 'bool' || type === 'enum' ? 'combobox' : 'textbox', {
      name: new RegExp(`^${label}(?:\\s*Required)?$`),
    });
    if (type === 'bool' || type === 'enum') await field.selectOption(String(value));
    else await field.fill(String(value));
  }
}
for (const width of widths) {
  test(`A2 A5 F6A-R1-02 label-only read, rule reorder and reload preserve hidden raw input at ${width}`, async ({
    page,
  }) => {
    await open(page, width, {
      view: 'raw_rule_refresh',
      saves: 'abilities_samples.raw_rule_refresh_committed',
    });
    await rescue(page);
    const last = page.locator('[data-rule-key]').nth(2);
    await last.getByRole('combobox', { name: /condition$/ }).selectOption('wave_at_least');
    await last.getByRole('textbox', { name: 'wave at least' }).fill('0007');
    await last.getByRole('combobox', { name: /condition$/ }).selectOption('wave_between');
    await last.getByRole('textbox', { name: /^Minimum/ }).fill('0010');
    await review(page);
    await save(page).click();
    await expect(
      page.getByText('Saved in the profile. No game action was performed.', { exact: true }),
    ).toBeVisible();
    await read(page).click();
    await expect(page.getByRole('textbox', { name: 'Plan label' })).toHaveValue('External label');
    await world(page, {
      view: 'raw_rule_refresh',
      profile: 'abilities_samples.raw_rule_refresh_reordered',
      settings: 'abilities_samples.raw_rule_refresh_settings_after',
    });
    await read(page).click();
    const first = page.locator('[data-rule-key]').first();
    await expect(first.getByRole('textbox', { name: /^Minimum/ })).toHaveValue('0010');
    await page.reload();
    await expect(page.getByRole('textbox', { name: 'Plan label' })).toHaveValue('External label');
    await first.getByRole('combobox', { name: /condition$/ }).selectOption('wave_at_least');
    await expect(first.getByRole('textbox', { name: 'wave at least' })).toHaveValue('0007');
    expect(await posts(page)).toHaveLength(1);
    await shot(page, 'r1-raw-rule-history', width);
  });
  test(`A2 F6A-R1-03 unfinished IDs remain under their target and family through navigation and reload at ${width}`, async ({
    page,
  }) => {
    await open(page, width);
    const id = page.getByRole('textbox', { name: 'New plan ID' });
    await id.fill('alpha_uw');
    await page
      .getByRole('combobox', { name: 'Saved plans', exact: true })
      .selectOption('rescue_policies');
    await expect(id).toHaveValue('');
    await id.fill('alpha_rescue');
    await targetWorld(
      page,
      {
        config: 'config_state.effective_view_of_the_other_account',
        profile: 'profile_reads.profile_src_of_the_other_accounts_profile_is_not_refused',
        settings: 'settings.other_accounts_profile_active_profile_modules_read_only',
      },
      'theirs',
    );
    await expect(id).toHaveValue('');
    await id.fill('beta_rescue');
    await targetWorld(page, { view: 'label' }, 'mine');
    await expect(id).toHaveValue('alpha_rescue');
    await page.evaluate(() => {
      location.hash = 'history';
    });
    await expect(id).toHaveCount(0);
    await page.evaluate(() => {
      location.hash = 'abilities';
    });
    await expect(id).toHaveValue('alpha_uw');
    await page.reload();
    await expect(id).toHaveValue('alpha_uw');
    await page
      .getByRole('combobox', { name: 'Saved plans', exact: true })
      .selectOption('rescue_policies');
    await expect(id).toHaveValue('alpha_rescue');
    expect(await posts(page)).toHaveLength(0);
    await shot(page, 'r1-id-scopes', width);
  });
  test(`A3 F6A-R1-04 optional threshold resets to null and restores with explicit bounds at ${width}`, async ({
    page,
  }) => {
    await open(page, width, { view: 'cl_unset' });
    const explicit = page.getByRole('checkbox', { name: 'Use explicit wave bounds' });
    await explicit.uncheck();
    await review(page);
    await save(page).click();
    await submitted(page, 'cl_unset');
    await expect(explicit).not.toBeChecked();
    await world(page, { view: 'cl_restore' });
    await read(page).click();
    await expect(read(page)).toBeEnabled();
    await explicit.check();
    await review(page);
    await save(page).click();
    await expect(
      page.getByText('Saved in the profile. No game action was performed.', { exact: true }),
    ).toBeVisible();
    const writes = await posts(page);
    expect(writes).toHaveLength(2);
    expect(JSON.parse(writes[1]?.body ?? '')).toEqual(
      ownPath(fixture('cl_restore'), 'request', 'body'),
    );
    await read(page).click();
    await expect(read(page)).toBeEnabled();
    await accessibility(page);
    await shot(page, 'r1-optional-threshold', width);
  });
  for (const view of ['rules_absent', 'rules_null'])
    test(`A5 F6A-R1-05 ${view} adds the first rule by keyboard at ${width}`, async ({ page }) => {
      await open(page, width, { view });
      await rescue(page);
      const add = page.getByRole('button', { name: 'Add rule', exact: true });
      await add.focus();
      await page.keyboard.press('Enter');
      const rule = page.locator('[data-rule-key]').first();
      await rule.getByRole('combobox', { name: /condition$/ }).selectOption('wave_at_least');
      await rule.getByRole('textbox', { name: 'wave at least' }).fill('1000');
      await rule.getByRole('combobox', { name: /action$/ }).selectOption('cancel_sprint');
      await review(page);
      await save(page).click();
      await submitted(page, view);
      await expect(page.locator('[data-rule-key]')).toHaveCount(1);
      await accessibility(page);
      await shot(page, `r1-${view}`, width);
    });
  test(`A1 A2 stale, loading and conflict states retain the exact draft at ${width}`, async ({
    page,
  }) => {
    await page.clock.install();
    await open(page, width, {
      view: 'global',
      saves: 'settings.stale_revision_is_judged_before_the_values',
    });
    await page.getByRole('button', { name: 'Global Demon Mode timing', exact: true }).click();
    const choice = page.getByRole('checkbox', {
      name: 'Use Demon Mode whenever ready',
      exact: true,
    });
    await choice.check();
    await page.getByRole('button', { name: 'Save global override', exact: true }).click();
    await expect(
      page.getByText(/The saved revision changed or the target is read-only/),
    ).toBeVisible();
    await expect(choice).toBeChecked();
    expect(await posts(page)).toHaveLength(1);
    await shot(page, 'global-conflict', width);
    await open(page, width, { view: 'label' });
    await page.getByRole('textbox', { name: 'Plan label', exact: true }).fill('Kept while stale');
    await page.clock.fastForward(31_000);
    await expect(save(page)).toBeDisabled();
    await read(page).click();
    await expect(read(page)).toBeEnabled();
    await expect(page.getByRole('textbox', { name: 'Plan label', exact: true })).toHaveValue(
      'Kept while stale',
    );
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route('**/api/profile-src/mine', async (route) => {
      await gate;
      await route.continue();
    });
    await read(page).click();
    await expect(
      page.getByText('Reading policies, settings and vocabulary…', { exact: true }),
    ).toBeVisible();
    await expect(save(page)).toBeDisabled();
    await shot(page, 'loading-draft', width);
    release?.();
    await expect(read(page)).toBeEnabled();
  });
  test(`A1 global override is scoped, explicit and never fires an ability at ${width}`, async ({
    page,
  }) => {
    await open(page, width, { view: 'global' });
    await page.getByRole('button', { name: 'Global Demon Mode timing', exact: true }).click();
    await page
      .getByRole('checkbox', { name: 'Use Demon Mode whenever ready', exact: true })
      .check();
    await page.getByRole('button', { name: 'Save global override', exact: true }).click();
    await submitted(page, 'global');
    await accessibility(page);
    await shot(page, 'global', width);
  });
  test(`A2 create copy label cancel delete and consumer protection at ${width}`, async ({
    page,
  }) => {
    await open(page, width, { view: 'copy_uw' });
    await expect(
      page.getByRole('button', { name: 'Delete unused plan', exact: true }),
    ).toBeDisabled();
    await page.getByRole('textbox', { name: 'New plan ID', exact: true }).fill('new_plan');
    await page.getByRole('button', { name: 'Copy selected to draft', exact: true }).click();
    await save(page).click();
    await submitted(page, 'copy_uw');
    await shot(page, 'copy', width);
    await open(page, width, { view: 'create_rescue' });
    await page
      .getByRole('combobox', { name: 'Saved plans', exact: true })
      .selectOption('rescue_policies');
    await page.getByRole('textbox', { name: 'New plan ID', exact: true }).fill('new_plan');
    await page.getByRole('button', { name: 'Create draft', exact: true }).click();
    await save(page).click();
    await submitted(page, 'create_rescue');
    await open(page, width, { view: 'label' });
    await page.getByRole('textbox', { name: 'Plan label', exact: true }).fill('Temporary');
    await page.getByRole('button', { name: 'Cancel edits', exact: true }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('button', { name: 'Cancel edits', exact: true })).toBeFocused();
    await page.getByRole('button', { name: 'Cancel edits', exact: true }).click();
    await page
      .getByRole('dialog')
      .getByRole('button', { name: 'Cancel edits', exact: true })
      .click();
    await expect(page.getByRole('textbox', { name: 'Plan label', exact: true })).toHaveValue(
      'Farm choreography',
    );
    await open(page, width, { view: 'delete' });
    await page.getByRole('button', { name: 'unused', exact: true }).click();
    await page.getByRole('button', { name: 'Delete unused plan', exact: true }).click();
    await page
      .getByRole('dialog')
      .getByRole('button', { name: 'Delete unused plan', exact: true })
      .click();
    await submitted(page, 'delete');
    await shot(page, 'deleted', width);
  });
  test(`A2 late save preserves typing, lost answer reads back without resend at ${width}`, async ({
    page,
  }) => {
    await open(page, width, { view: 'label', delay: '800' });
    const label = page.getByRole('textbox', { name: 'Plan label', exact: true });
    await label.fill('New label');
    await save(page).click();
    await label.fill('Typed after save');
    await expect(
      page.getByText('Saved in the profile. No game action was performed.', { exact: true }),
    ).toBeVisible();
    await expect(label).toHaveValue('Typed after save');
    expect(await posts(page)).toHaveLength(1);
    await shot(page, 'newer-typing', width);
    await open(page, width, { view: 'label', mode: 'drop' });
    await label.fill('New label');
    await save(page).click();
    await expect(page.getByText(/Save outcome unknown/)).toBeVisible();
    await read(page).click();
    await expect(
      page.getByText('A later read holds the submitted value. Newer typing is kept.', {
        exact: true,
      }),
    ).toBeVisible();
    expect(await posts(page)).toHaveLength(1);
    await shot(page, 'lost-readback', width);
  });
  for (const [view, mode] of [
    ['cl_always', 'always_on'],
    ['cl_wave', 'off_until_wave'],
    ['cl_off', 'off'],
    ['cl_fleet', 'fleet_marks'],
  ] as const)
    test(`A3 ${mode} ranges round-trip at ${width}`, async ({ page }) => {
      await open(page, width, { view });
      await page
        .getByRole('combobox', { name: 'Chain Lightning mode', exact: true })
        .selectOption(mode);
      const cl = ownPath(fixture(view), 'request', 'body', 'value', 'chain_lightning');
      const labels: Record<string, string> = {
        on_above: 'Turn on above wave',
        pre_mark_waves: 'Waves before fleet mark',
        off_after_waves: 'Waves after fleet mark',
        always_on_above: 'Always on above wave',
      };
      for (const [key, value] of entries(cl))
        if (key !== 'mode') await fillSpec(page, labels[key] ?? key, { type: 'list' }, value);
      await review(page);
      await save(page).click();
      await submitted(page, view);
      await shot(page, view, width);
    });
  test(`A3 unowned choice is kept, reversed waves refused, malformed raw kept at ${width}`, async ({
    page,
  }) => {
    await open(page, width, { view: 'uw_unowned' });
    await page.getByRole('combobox', { name: 'smart missiles', exact: true }).selectOption('true');
    await expect(page.getByText(/Ownership not recorded — skipped here/).first()).toBeVisible();
    await save(page).click();
    await submitted(page, 'uw_unowned');
    await open(page, width, { view: 'cl_reversed' });
    await page
      .getByRole('combobox', { name: 'Chain Lightning mode', exact: true })
      .selectOption('off_until_wave');
    await fillSpec(page, 'Turn on above wave', { type: 'list' }, [550, 500]);
    await review(page);
    await save(page).click();
    await expect(page.getByText(/The backend refused this save/)).toBeVisible();
    await expect(page.getByRole('textbox', { name: /^Minimum/ }).first()).toHaveValue('550');
    const summary = page.getByRole('region', { name: 'Backend validation problems' });
    await expect(summary).toBeFocused();
    await summary.getByRole('link').first().click();
    await expect(page.getByRole('textbox', { name: /^Minimum/ }).first()).toBeFocused();
    await expect(page.getByRole('textbox', { name: /^Minimum/ }).first()).toHaveAttribute(
      'aria-invalid',
      'true',
    );
    await expect(page.getByRole('textbox', { name: /^Maximum/ }).first()).toHaveAttribute(
      'aria-invalid',
      'true',
    );
    await expect(
      page.getByRole('textbox', { name: /^Minimum/ }).first(),
    ).toHaveAccessibleDescription(/low bound 550 is above high bound 500/);
    await shot(page, 'range-refused', width);
    await page
      .getByRole('textbox', { name: /^Minimum/ })
      .first()
      .fill('invalid');
    await expect(page.getByRole('textbox', { name: /^Minimum/ }).first()).toHaveAttribute(
      'aria-invalid',
      'true',
    );
    await expect(save(page)).toBeDisabled();
    await accessibility(page);
    await shot(page, 'raw-invalid', width);
  });
  test(`A2 F6A-SOL-01 deleted plan keeps later typing through read and reload at ${width}`, async ({
    page,
  }) => {
    for (const mode of ['answer', 'drop']) {
      await open(page, width, { view: 'delete', mode, delay: '1200' });
      await page.getByRole('button', { name: 'unused', exact: true }).click();
      await page.getByRole('button', { name: 'Delete unused plan', exact: true }).click();
      await page
        .getByRole('dialog')
        .getByRole('button', { name: 'Delete unused plan', exact: true })
        .click();
      const label = page.getByRole('textbox', { name: 'Plan label', exact: true });
      await label.fill('Typed after delete');
      if (mode === 'drop') {
        await expect(page.getByText(/Save outcome unknown/)).toBeVisible();
        await read(page).click();
      }
      await expect(
        page.getByText(/The saved plan was deleted. Newer typing remains here as a draft/),
      ).toBeVisible();
      await expect(label).toHaveValue('Typed after delete');
      await read(page).click();
      await expect(read(page)).toBeEnabled();
      await page.reload();
      await expect(label).toHaveValue('Typed after delete');
      await expect(save(page)).toBeEnabled();
      expect(await posts(page)).toHaveLength(1);
      await shot(page, `deleted-newer-${mode}`, width);
    }
  });
  test(`A3 A4 F6A-SOL-02 hidden input survives Save, read and reload at ${width}`, async ({
    page,
  }) => {
    await open(page, width, { view: 'cl_off' });
    const pair = page.getByRole('group', { name: 'Waves before fleet mark', exact: true });
    await pair.getByRole('textbox', { name: /^Minimum/ }).fill('00bad');
    await page
      .getByRole('combobox', { name: 'Chain Lightning mode', exact: true })
      .selectOption('off');
    await review(page);
    await save(page).click();
    await submitted(page, 'cl_off');
    await page.reload();
    await page
      .getByRole('combobox', { name: 'Chain Lightning mode', exact: true })
      .selectOption('fleet_marks');
    await expect(pair.getByRole('textbox', { name: /^Minimum/ })).toHaveValue('00bad');
    expect(await posts(page)).toHaveLength(1);
    await shot(page, 'hidden-after-save', width);
  });
  for (const view of ['cl_fractional', 'mixed_bars'])
    test(`A3 A5 F6A-SOL-04 ${view} refusal focuses linked backend errors at ${width}`, async ({
      page,
    }) => {
      await open(page, width, { view });
      if (view === 'cl_fractional') {
        await page
          .getByRole('combobox', { name: 'Chain Lightning mode', exact: true })
          .selectOption('off_until_wave');
        await fillSpec(page, 'Turn on above wave', { type: 'list' }, [4.5, 5]);
        await review(page);
      } else {
        await rescue(page);
        await page.getByRole('button', { name: 'Add rule', exact: true }).click();
        const rule = page.locator('[data-rule-key]').last();
        await rule.getByRole('combobox', { name: /condition$/ }).selectOption('bar');
        await fillSpec(
          rule,
          'bar',
          ownPath(fixture(view, 'vocab'), 'body', 'vocab', 'rule_triggers', 'fields', 'bar'),
          { bar: 'hp', below: 0.2 },
        );
        await rule.getByRole('combobox', { name: /action$/ }).selectOption('fire');
        await fillSpec(
          rule,
          'fire',
          ownPath(fixture(view, 'vocab'), 'body', 'vocab', 'rule_actions', 'fields', 'fire'),
          { button: 'demon_mode' },
        );
        await review(page);
      }
      await save(page).click();
      const summary = page.getByRole('region', { name: 'Backend validation problems' });
      await expect(summary).toBeFocused();
      await summary.getByRole('link').first().click();
      if (view === 'cl_fractional') {
        const min = page
          .getByRole('group', { name: 'Turn on above wave' })
          .getByRole('textbox', { name: /^Minimum/ });
        await expect(min).toBeFocused();
        await expect(min).toHaveAttribute('aria-invalid', 'true');
        await expect(min).toHaveAccessibleDescription(/both bounds must be integers/);
        await expect(min).toHaveValue('4.5');
      } else await expect(page.locator('#abilities-field-policy')).toBeFocused();
      expect(await posts(page)).toHaveLength(1);
      await accessibility(page);
      await shot(page, `${view}-errors`, width);
    });
  for (const view of ['arm_always', 'arm_durations', 'end_sprint'])
    test(`A4 ${view} and visible arming review at ${width}`, async ({ page }) => {
      await open(page, width, { view });
      await rescue(page);
      if (view === 'arm_always') {
        await page.getByRole('combobox', { name: 'Arm scope', exact: true }).selectOption('always');
        await expect(save(page)).toBeDisabled();
        await review(page);
      }
      if (view === 'arm_durations') {
        await page.getByRole('textbox', { name: /^Watch duration/ }).fill('45');
        await page.getByRole('textbox', { name: /^Immunity duration/ }).fill('12');
      }
      if (view === 'end_sprint')
        await page
          .getByRole('checkbox', {
            name: 'End the intro sprint after the Second Wind window',
            exact: true,
          })
          .check();
      await save(page).click();
      await submitted(page, view);
      await shot(page, view, width);
    });
  test(`A5 keyboard reorder delete and focus stay with the rule at ${width}`, async ({ page }) => {
    await open(page, width, { view: 'reorder' });
    await rescue(page);
    const up = page.getByRole('button', { name: 'Move up 3', exact: true });
    await up.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('button', { name: 'Move up 2', exact: true })).toBeFocused();
    await page.keyboard.press('Enter');
    await save(page).click();
    await submitted(page, 'reorder');
    await accessibility(page);
    await shot(page, 'rules-reordered', width);
    await open(page, width, { view: 'rule_delete' });
    await rescue(page);
    await page.getByRole('button', { name: 'Delete rule 3', exact: true }).focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('button', { name: 'Delete rule 2', exact: true })).toBeFocused();
    await save(page).click();
    await submitted(page, 'rule_delete');
  });
  test(`A4 A7 unsupported rule inspection and authoritative readiness at ${width}`, async ({
    page,
  }) => {
    await open(page, width, {
      profile: 'ability_policies.a_rule_with_an_action_this_version_does_not_support',
    });
    await rescue(page);
    await expect(page.getByText(/This rule is read-only/)).toBeVisible();
    await expect(page.getByRole('combobox', { name: 'rule 4 action' })).toHaveCount(0);
    await accessibility(page);
    await shot(page, 'unsupported', width);
    await open(page, width, {
      profile:
        'ability_policies.abilities_the_account_does_not_own_are_bound_to_a_run_is_a_problem',
    });
    await expect(page.getByText(/which the player does not have/).first()).toBeVisible();
    await shot(page, 'readiness-problems', width);
  });
}
test('A2 F6A-R1-01 a received held save, target roundtrip and unchanged read never claim a second send', async ({
  page,
}) => {
  let release = () => {},
    received = 0;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/profile-patch', async (route) => {
    const answer = await route.fetch();
    if (++received === 1) await held;
    await route.fulfill({ response: answer });
  });
  await open(page, 1360, { view: 'label' });
  const label = page.getByRole('textbox', { name: 'Plan label' });
  await label.fill('New label');
  await save(page).click();
  await expect.poll(() => received).toBe(1);
  await targetWorld(
    page,
    {
      config: 'config_state.effective_view_of_the_other_account',
      profile: 'profile_reads.profile_src_of_the_other_accounts_profile_is_not_refused',
      settings: 'settings.other_accounts_profile_active_profile_modules_read_only',
    },
    'theirs',
  );
  await targetWorld(page, { view: 'label' }, 'mine');
  await read(page).click();
  await expect(read(page)).toBeEnabled();
  await expect(page.getByRole('button', { name: /I reviewed the saved values/ })).toBeDisabled();
  await label.fill('Newer never sent');
  await expect(save(page)).toBeDisabled();
  await expect(
    page.getByText('Saving once… Newer typing stays in this draft.', { exact: true }),
  ).toHaveCount(0);
  expect(await posts(page)).toHaveLength(1);
  const delivered = page.waitForResponse('**/api/profile-patch');
  release();
  await delivered;
  await world(page, {
    view: 'label',
    profile: 'abilities_samples.label_after',
    settings: 'abilities_samples.label_settings_after',
  });
  await read(page).click();
  await expect(save(page)).toBeEnabled();
  await save(page).click();
  await expect.poll(async () => (await posts(page)).length).toBe(2);
  expect(ownPath(JSON.parse((await posts(page))[1]?.body ?? ''), 'value', 'label')).toBe(
    'Newer never sent',
  );
});
test('A2 reload settles an uncertain original-target save and preserves newer typing', async ({
  page,
}) => {
  await open(page, 390, { view: 'label', mode: 'drop' });
  const label = page.getByRole('textbox', { name: 'Plan label', exact: true });
  await label.fill('New label');
  await save(page).click();
  await expect(page.getByText(/Save outcome unknown/)).toBeVisible();
  await label.fill('Newer after loss');
  await page.reload();
  await expect(label).toHaveValue('Newer after loss');
  await expect(
    page.getByText('A later read holds the submitted value. Newer typing is kept.', {
      exact: true,
    }),
  ).toBeVisible();
  expect(await posts(page)).toHaveLength(1);
  await shot(page, 'reload-draft', 390);
});
test('A2 hostile labels stay raw, readable and within the narrow layout', async ({ page }) => {
  await open(page, 390);
  const input = page.getByRole('textbox', { name: 'Plan label', exact: true });
  for (const value of ['', 'x', 'A long policy label '.repeat(20), 'Unbreakable'.repeat(200)]) {
    await input.fill(value);
    await expect(input).toHaveValue(value);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
  }
  await shot(page, 'long-draft', 390);
  expect(await posts(page)).toHaveLength(0);
});
for (const view of [
  'trigger_bar',
  'trigger_wall_collapse',
  'trigger_fleet_mark',
  'trigger_wave_at_least',
  'trigger_wave_between',
  'trigger_second_wind',
  'action_burst',
  'action_fire',
  'action_fire_refire_guard',
  'action_toggle_uw',
  'action_cancel_sprint',
])
  test(`A5 ${view} authoring follows backend vocabulary and round-trips parameters`, async ({
    page,
  }) => {
    await open(page, 1360, { view });
    await page
      .getByRole('combobox', { name: 'Saved plans', exact: true })
      .selectOption('rescue_policies');
    await page.getByRole('textbox', { name: 'New plan ID', exact: true }).fill('new_plan');
    await page.getByRole('button', { name: 'Create draft', exact: true }).click();
    await page.getByRole('button', { name: 'Add rule', exact: true }).click();
    const value = ownPath(fixture(view), 'request', 'body', 'value'),
      rules = own(value, 'rules');
    const rule: unknown = Array.isArray(rules) ? rules[0] : undefined;
    const vocab = ownPath(fixture(view, 'vocab'), 'body', 'vocab');
    const when = own(rule, 'when'),
      trigger = own(when, 'bar') !== undefined ? 'bar' : (entries(when)[0]?.[0] ?? '');
    const action = entries(own(rule, 'do'))[0]?.[0] ?? '';
    await page
      .getByRole('combobox', { name: 'rule 1 condition', exact: true })
      .selectOption(trigger);
    await fillSpec(
      page,
      trigger.replaceAll('_', ' '),
      ownPath(vocab, 'rule_triggers', 'fields', trigger),
      trigger === 'bar' ? when : own(when, trigger),
    );
    await page.getByRole('combobox', { name: 'rule 1 action', exact: true }).selectOption(action);
    await fillSpec(
      page,
      action.replaceAll('_', ' '),
      ownPath(vocab, 'rule_actions', 'fields', action),
      ownPath(rule, 'do', action),
    );
    if (own(rule, 'repeat') !== undefined)
      await page
        .getByRole('combobox', { name: 'Repeat this rule', exact: true })
        .selectOption(String(own(rule, 'repeat')));
    if (own(rule, 'refire_sec') !== undefined)
      await page
        .getByRole('textbox', { name: 'Rule cooldown floor (seconds)', exact: true })
        .fill(String(own(rule, 'refire_sec')));
    await review(page);
    await save(page).click();
    await submitted(page, view);
    if (view === 'action_toggle_uw') await shot(page, 'toggle-uw-want-on', 1360);
  });
