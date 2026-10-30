import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { own, ownPath } from '../../src/core/read.ts';
import {
  DASHBOARD_PAGES_FIXTURE_PATH,
  loadJsonWithIdentity,
} from '../../scripts/contract-fixture.mjs';
import { axeIds, bodiesTo, posts, WCAG_AA_TAGS } from './helpers/helpers.ts';
import { dashboardPagesCase, type DashboardCase } from '../support/dashboardFixture.ts';
import {
  moreWorld,
  openMore,
  moreCase,
  invalidSourceTests,
} from './helpers/profile-target-more.ts';

invalidSourceTests('abilities', proof);

const cases = own(loadJsonWithIdentity(DASHBOARD_PAGES_FIXTURE_PATH).fixture, 'cases');

for (const width of [1360, 1054, 390])
  for (const family of ['uw', 'rescue']) {
    test(`PE5 F6-14c inactive ${family} in-use deletion keeps refusal through reload at ${width}`, async ({
      page,
    }) => {
      const before = moreCase('read_before_the_inactive_policies_are_deleted');
      // Modified recording: hide consumers to model a reference added after this read.
      Object.assign(own(before.body, 'profile') as object, { blueprints: {} });
      const world = await moreWorld(page, before),
        answer = moreCase(`inactive_${family}_policy_deleted_while_a_run_uses_it_is_refused`);
      world.answer(answer);
      await openMore(page, width, '#abilities', 'profile_target_abilities_uw');
      if (family === 'uw')
        await page.getByRole('button', { name: 'Farm choreography', exact: true }).click();
      if (family === 'rescue') {
        await page
          .getByRole('combobox', { name: 'Saved plans', exact: true })
          .selectOption('rescue_policies');
        await page.getByRole('button', { name: 'Wall drain', exact: true }).click();
      }
      await page.getByRole('button', { name: 'Delete unused plan', exact: true }).click();
      await page
        .getByRole('dialog')
        .getByRole('button', { name: 'Delete unused plan', exact: true })
        .click();
      await expect(
        page.getByRole('link', { name: String(own(answer.body, 'error')), exact: true }),
      ).toBeVisible();
      expect(world.sent).toEqual([answer.request.body]);
      world.source(moreCase('read_after_the_in_use_deletes_were_refused_nothing_changed'));
      await page.getByRole('button', { name: 'Read saved result', exact: true }).click();
      await page.reload();
      await expect(
        page.getByRole('link', { name: String(own(answer.body, 'error')), exact: true }),
      ).toBeVisible();
      expect(world.sent).toHaveLength(1);
      await proof(page, `f6-14c-${family}-in-use-reload`, width);
    });
  }

for (const width of [1360, 1054, 390]) {
  test(`PE5 F6-14c unused UW/rescue deletes and whole-policy rule reorder at ${width}`, async ({
    page,
  }) => {
    const world = await moreWorld(page, moreCase('read_before_the_inactive_policies_are_deleted'));
    await openMore(page, width, '#abilities', 'profile_target_abilities_uw');
    for (const [family, id, name] of [
      ['uw_policies', 'Calm', 'inactive_unused_uw_policy_deleted'],
      ['rescue_policies', 'extra', 'inactive_unused_rescue_policy_deleted'],
    ]) {
      world.answer(moreCase(name ?? ''));
      await page
        .getByRole('combobox', { name: 'Saved plans', exact: true })
        .selectOption(family ?? '');
      await page.getByRole('button', { name: id ?? '', exact: true }).click();
      await page.getByRole('button', { name: 'Delete unused plan', exact: true }).click();
      await page
        .getByRole('dialog')
        .getByRole('button', { name: 'Delete unused plan', exact: true })
        .click();
      await expect(
        page.getByRole('status').filter({ hasText: 'Saved in the profile.' }),
      ).toBeVisible();
      // Let this read settle before the next draft: a revision that arrives after an edit
      // asks for review and blocks Save (abilities rules: a changed revision needs review).
      const reads = world.reads();
      await read(page).click();
      await expect.poll(world.reads).toBeGreaterThan(reads);
      await expect(read(page)).toBeEnabled();
    }
    await page.getByRole('button', { name: 'Wall drain', exact: true }).click();
    world.answer(moreCase('inactive_rescue_policy_saved_with_its_rules_in_another_order'));
    const rules = page.locator('[data-rule-key]');
    await rules.nth(2).getByRole('button', { name: 'Move up 3', exact: true }).click();
    await rules.nth(1).getByRole('button', { name: 'Move up 2', exact: true }).click();
    await page.getByRole('button', { name: 'Save plan', exact: true }).click();
    await expect(
      page.getByRole('status').filter({ hasText: 'Saved in the profile.' }),
    ).toBeVisible();
    expect(world.sent).toEqual(
      [
        'inactive_unused_uw_policy_deleted',
        'inactive_unused_rescue_policy_deleted',
        'inactive_rescue_policy_saved_with_its_rules_in_another_order',
      ].map((name) => moreCase(name).request.body),
    );
    await proof(page, 'f6-14c-deletes-reorder', width);
  });
}
const body = (name: string) => ownPath(cases, 'profile_target', name, 'request', 'body');
const OUT =
  process.env['TP_F6_SCREENS_DIR'] ??
  path.resolve('../../backend/logs/reviews/frontend-f6-screens');
mkdirSync(OUT, { recursive: true });

async function route(page: Page, hash: string) {
  await page.evaluate((hash) => {
    location.hash = hash;
  }, hash);
}
async function open(
  page: Page,
  width: number,
  scenario = 'profile_target_abilities_uw',
  ready = true,
) {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`/__fake/use?scenario=${scenario}`);
  await route(page, '#abilities');
  await expect(page.getByRole('textbox', { name: 'Plan label', exact: true })).toBeVisible();
  await page.getByRole('textbox', { name: 'Plan label', exact: true }).fill('Mine retained draft');
  await route(page, '#profiles');
  const picker = page.locator('[data-form="editing-target"]');
  await picker.getByRole('combobox').selectOption('second');
  await picker.getByRole('button', { name: 'Edit this profile', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(picker).toContainText('Editing:second(not the active profile)');
  await route(page, '#abilities');
  await expect(page.locator('main [data-editing-target="second"]')).toBeVisible();
  if (!ready) return errors;
  await expect(page.getByRole('textbox', { name: 'Plan label', exact: true })).toHaveValue(
    'Farm choreography',
  );
  await expect(
    page.getByRole('button', { name: 'Global Demon Mode timing', exact: true }),
  ).toBeDisabled();
  await expect(page.getByTestId('abilities-active-only')).toContainText('Active profile:mine');
  await expect(
    page.getByText('Return editing to the active profile to change these', { exact: true }),
  ).toBeVisible();
  expect(await posts(page)).toEqual([]);
  return errors;
}
async function proof(page: Page, state: string, width: number) {
  const violations = await axeIds(page, WCAG_AA_TAGS);
  expect(violations).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(
    await page.locator('main button, main input, main select, main summary').evaluateAll((all) =>
      all
        .filter((element) => element.getClientRects().length > 0)
        .filter((element) => {
          const box = element.getBoundingClientRect();
          return box.width < 24 || box.height < 24;
        })
        .map((element) => element.outerHTML.slice(0, 120)),
    ),
  ).toEqual([]);
  if (width === 390) {
    expect(
      await page
        .locator('main button')
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
    path: path.join(OUT, `profile-target-abilities-${state}-${width}.png`),
    fullPage: true,
  });
}
const save = (page: Page) => page.getByRole('button', { name: 'Save plan', exact: true });
const read = (page: Page) => page.getByRole('button', { name: 'Read saved result', exact: true });

for (const width of [1360, 1054, 390]) {
  test(`PE5 pick second, save exact UW/rescue bodies, active-only Demon Mode and restore mine draft at ${width}`, async ({
    page,
  }) => {
    const errors = await open(page, width);
    await proof(page, 'picked', width);
    await page.getByRole('textbox', { name: 'New plan ID', exact: true }).fill('calm');
    await page.getByRole('button', { name: 'Create draft', exact: true }).click();
    await page.getByRole('textbox', { name: 'Plan label', exact: true }).fill('Calm');
    const value = own(body('inactive_uw_policy_saved'), 'value');
    for (const [weapon, enabled] of Object.entries(
      own(value, 'baseline') as Record<string, boolean>,
    )) {
      await page
        .getByRole('combobox', { name: weapon.replaceAll('_', ' '), exact: true })
        .selectOption(String(enabled));
    }
    await save(page).focus();
    await page.keyboard.press('Enter');
    await expect(
      page.getByRole('status').filter({ hasText: 'Saved in the profile.' }),
    ).toContainText('second');
    expect(await bodiesTo(page, '/api/profile-patch')).toEqual([body('inactive_uw_policy_saved')]);
    await proof(page, 'uw-saved', width);
    await page.request.get('/__fake/set?scenario=profile_target_abilities_rescue');
    await read(page).click();
    await expect(read(page)).toBeEnabled();
    await page
      .getByRole('combobox', { name: 'Saved plans', exact: true })
      .selectOption('rescue_policies');
    await page.getByRole('button', { name: 'any_falling', exact: true }).click();
    await page.getByRole('textbox', { name: 'New plan ID', exact: true }).fill('extra');
    await page.getByRole('button', { name: 'Copy selected to draft', exact: true }).click();
    await save(page).click();
    await expect(
      page.getByRole('status').filter({ hasText: 'Saved in the profile.' }),
    ).toContainText('second');
    expect(await bodiesTo(page, '/api/profile-patch')).toEqual([
      body('inactive_uw_policy_saved'),
      body('inactive_rescue_policy_saved'),
    ]);
    await proof(page, 'rescue-saved', width);
    await route(page, '#profiles');
    await page
      .locator('[data-form="editing-target"]')
      .getByRole('button', { name: 'Return editing to the active profile', exact: true })
      .click();
    await route(page, '#abilities');
    await expect(page.getByRole('textbox', { name: 'Plan label', exact: true })).toHaveValue(
      'Mine retained draft',
    );
    await expect(page.locator('main [data-editing-target="mine"]')).toBeVisible();
    await proof(page, 'returned', width);
    expect(await posts(page)).toHaveLength(2);
    expect(errors).toEqual([]);
  });

  test(`PE5 changed runtime after drawing sends nothing and keeps second draft at ${width}`, async ({
    page,
  }) => {
    const errors = await open(page, width);
    await page
      .getByRole('textbox', { name: 'Plan label', exact: true })
      .fill('Second draft retained');
    await page.request.get('/__fake/set?scenario=profile_target_abilities_context_changed');
    await save(page).focus();
    await page.keyboard.press('Enter');
    await expect(
      page.getByText(
        'The runtime context or editing target changed. Nothing was sent; the draft is kept.',
        { exact: true },
      ),
    ).toBeVisible();
    expect(await posts(page)).toEqual([]);
    await expect(page.getByRole('textbox', { name: 'Plan label', exact: true })).toHaveValue(
      'Second draft retained',
    );
    await proof(page, 'context-changed', width);
    expect(errors).toEqual([]);
  });
}
const pt = (name: string) => dashboardPagesCase(`profile_target.${name}`);
const record = (value: unknown) => value as Record<string, unknown>;

// Transport control holds real recorded answers; it never fabricates a loading response.
async function targetRead(page: Page, initial = pt('same_account_inactive_target_source')) {
  let answer: DashboardCase = initial;
  let release = () => {};
  let held = false;
  let count = 0;
  await page.route('**/api/profile-src/second', async (request) => {
    count++;
    if (held) await new Promise<void>((resolve) => (release = resolve));
    await request.fulfill({ status: answer.status, json: answer.body });
  });
  return {
    set: (next: DashboardCase) => (answer = next),
    hold: () => (held = true),
    release: () => {
      held = false;
      release();
    },
    count: () => count,
  };
}
async function calmDraft(page: Page) {
  await page.getByRole('textbox', { name: 'New plan ID', exact: true }).fill('calm');
  await page.getByRole('button', { name: 'Create draft', exact: true }).focus();
  await page.keyboard.press('Enter');
  await page.getByRole('textbox', { name: 'Plan label', exact: true }).fill('Calm');
  for (const [weapon, enabled] of Object.entries(
    own(own(body('inactive_uw_policy_saved'), 'value'), 'baseline') as Record<string, boolean>,
  )) {
    await page
      .getByRole('combobox', { name: weapon.replaceAll('_', ' '), exact: true })
      .selectOption(String(enabled));
  }
}

for (const width of [1360, 1054, 390]) {
  test(`PE5 held inactive source loading then empty UW/rescue libraries, keyboard and axe at ${width}`, async ({
    page,
  }) => {
    // Modified recorded case: remove only the target's two policy libraries and their
    // run consumers to represent an empty, valid library; retain identity and revision.
    const empty = pt('same_account_inactive_target_source');
    const profile = record(own(empty.body, 'profile'));
    const policies = record(own(profile, 'policies'));
    policies['uw_policies'] = {};
    policies['rescue_policies'] = {};
    profile['blueprints'] = {};
    const source = await targetRead(page, empty);
    source.hold();
    await open(page, width, 'profile_target_abilities_negative', false);
    await expect.poll(source.count).toBeGreaterThan(0);
    await expect(page.getByRole('status').filter({ hasText: 'Reading policies' })).toBeVisible();
    await expect(save(page)).toHaveCount(0);
    const family = page.getByRole('combobox', { name: 'Saved plans', exact: true });
    await family.focus();
    await expect(family).toBeFocused();
    await proof(page, 'loading', width);
    source.release();
    await expect(page.getByRole('status').filter({ hasText: 'Reading policies' })).toHaveCount(0);
    await expect(
      page.getByText('No saved plans in this family. Enter a new plan ID to create one.', {
        exact: true,
      }),
    ).toBeVisible();
    await expect(family).toBeFocused();
    await page.keyboard.press('ArrowDown');
    await expect(family).toHaveValue('rescue_policies');
    await expect(
      page.getByText('No saved plans in this family. Enter a new plan ID to create one.', {
        exact: true,
      }),
    ).toBeVisible();
    expect(await posts(page)).toEqual([]);
    await proof(page, 'empty', width);
  });

  for (const family of ['uw', 'rescue'] as const) {
    test(`PE5 ${family} in-use delete refusal keeps draft, associates server text and restores keyboard focus at ${width}`, async ({
      page,
    }) => {
      // Modified recorded case: remove locally observed consumers. The server's
      // unchanged recorded in-use refusal models a consumer added after this read.
      // This older transport scenario retains its adapted active delete. The F6-14c
      // cases above compare the complete inactive requests and refusal readbacks.
      const before = pt('same_account_inactive_target_source');
      record(own(before.body, 'profile'))['blueprints'] = {};
      await targetRead(page, before);
      await open(page, width, `profile_target_abilities_${family}_refused`);
      if (family === 'rescue') {
        await page
          .getByRole('combobox', { name: 'Saved plans', exact: true })
          .selectOption('rescue_policies');
        await page.getByRole('button', { name: 'Wall drain', exact: true }).click();
      }
      const trigger = page.getByRole('button', { name: 'Delete unused plan', exact: true });
      await trigger.focus();
      await page.keyboard.press('Enter');
      const dialog = page.getByRole('dialog');
      await expect(dialog).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(trigger).toBeFocused();
      expect(await posts(page)).toEqual([]);
      await page.keyboard.press('Enter');
      await dialog.getByRole('button', { name: 'Delete unused plan', exact: true }).focus();
      await page.keyboard.press('Enter');
      const refusal = dashboardPagesCase(
        `ability_policies.${family === 'uw' ? 'uw' : 'rescue'}_policy_deleted_while_a_run_uses_it`,
      );
      const problem = String(own(refusal.body, 'error'));
      const summary = page.locator('.tp-abilities__errors');
      await expect(summary).toBeFocused();
      const link = summary.getByRole('link', { name: problem, exact: true });
      await expect(link).toBeVisible();
      await expect(
        page.getByRole('status').filter({ hasText: 'The backend refused this save.' }),
      ).toContainText('second');
      await link.focus();
      await page.keyboard.press('Enter');
      const destination = await link.getAttribute('href');
      expect(destination).not.toBeNull();
      if (destination === null) throw new Error('The refusal link has no target');
      await expect(page.locator(destination)).toBeFocused();
      const sent = await bodiesTo(page, '/api/profile-patch');
      expect(sent).toEqual([
        {
          ...record(refusal.request.body),
          name: 'second',
          expected_revision: own(before.body, 'revision'),
          expected_context: own(body('inactive_uw_policy_saved'), 'expected_context'),
        },
      ]);
      await proof(page, `${family}-refused`, width);
    });
  }

  test(`PE5 stale inactive revision retains whole-policy draft and keyboard focus at ${width}`, async ({
    page,
  }) => {
    await open(page, width, 'profile_target_abilities_negative');
    await calmDraft(page);
    await save(page).focus();
    await page.keyboard.press('Enter');
    await expect(
      page.getByRole('status').filter({ hasText: 'The saved revision changed' }),
    ).toContainText('second');
    await expect(page.getByRole('textbox', { name: 'Plan label', exact: true })).toHaveValue(
      'Calm',
    );
    await expect(save(page)).toBeDisabled();
    await read(page).focus();
    await expect(read(page)).toBeFocused();
    const sent = await bodiesTo(page, '/api/profile-patch');
    expect(sent).toEqual([
      {
        ...record(body('inactive_uw_policy_saved')),
        expected_revision: own(pt('same_account_inactive_target_source').body, 'revision'),
      },
    ]);
    // Modified recorded case: the plan-path revision refusal is path-independent;
    // apply its unchanged body to a whole-policy patch with the inactive envelope.
    await expect(page.locator('main')).toContainText(
      String(own(pt('inactive_target_stale_revision').body, 'error')),
    );
    await proof(page, 'stale', width);
  });

  for (const failure of ['failed', 'answer_failed', 'lost'] as const) {
    test(`PE5 ${failure} reply stays Unknown on unchanged read and settles original target advanced read at ${width}`, async ({
      page,
    }) => {
      const source = await targetRead(page, pt('inactive_target_after_write_failed'));
      await open(page, width, `profile_target_abilities_${failure}`);
      await calmDraft(page);
      await save(page).focus();
      await page.keyboard.press('Enter');
      const unknown = page.getByRole('status').filter({ hasText: 'Save outcome unknown.' });
      await expect(unknown).toContainText('second');
      const count = source.count();
      await read(page).focus();
      await page.keyboard.press('Enter');
      await expect.poll(source.count).toBeGreaterThan(count);
      await expect(read(page)).toBeEnabled();
      await expect(unknown).toBeVisible();
      await expect(save(page)).toBeDisabled();
      await test.info().attach('unknown-readback-focus', {
        body: JSON.stringify(
          await page.evaluate(() => ({
            tag: document.activeElement?.tagName,
            id: document.activeElement?.id,
            text: document.activeElement?.textContent.slice(0, 160),
          })),
        ),
        contentType: 'application/json',
      });
      await page.screenshot({
        path: path.join(OUT, `profile-target-abilities-unknown-${failure}-${width}.png`),
        fullPage: true,
      });
      await expect(read(page)).toBeFocused();
      await proof(page, `unknown-${failure}`, width);
      // Modified recorded case: failure recordings patch plan. Keep the recorded
      // original/advanced read identity+revision, adding only the exact recorded
      // calm policy, so this browser can prove whole-policy readback settlement.
      const advanced = pt('inactive_target_after_written_then_answer_failed');
      record(ownPath(advanced.body, 'profile', 'policies', 'uw_policies'))['calm'] = own(
        body('inactive_uw_policy_saved'),
        'value',
      );
      source.set(advanced);
      await page.keyboard.press('Enter');
      await expect(
        page.getByRole('status').filter({ hasText: 'A later read holds the submitted value.' }),
      ).toContainText('second');
      await expect(read(page)).toBeFocused();
      await expect(page.getByRole('textbox', { name: 'Plan label', exact: true })).toHaveValue(
        'Calm',
      );
      expect(await posts(page)).toHaveLength(1);
      await proof(page, `settled-${failure}`, width);
    });
  }
}
