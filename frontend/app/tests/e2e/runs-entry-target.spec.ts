import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { axeIds, bodiesTo, posts, WCAG_AA_TAGS } from './helpers/helpers.ts';
import { own, ownPath } from '../../src/core/read.ts';
import { dashboardPagesCase, type DashboardCase } from '../support/dashboardFixture.ts';
import { more2Writes } from '../support/runEntryTargetMore2.ts';
import {
  moreWorld,
  openMore,
  moreCase,
  moreNames,
  moreSourceBefore,
  moreEntry,
  invalidSourceTests,
} from './helpers/profile-target-more.ts';

invalidSourceTests('runs', proof);

const OUT =
  process.env['TP_F6_SCREENS_DIR'] ??
  path.resolve('../../backend/logs/reviews/frontend-f6-screens');
mkdirSync(OUT, { recursive: true });
const sample = (name: string) => dashboardPagesCase(`profile_target.${name}`);

for (const width of [1360, 1054, 390])
  for (const { name, before, write, after } of more2Writes) {
    test(`PE6 F6-14c r12f ${name} exact request and saved readback at ${width}`, async ({
      page,
    }) => {
      let source = before;
      const sent: unknown[] = [];
      let reads = 0;
      await page.route('**/api/profile-src/second', async (route) => {
        reads++;
        await route.fulfill({ status: source.status, json: source.body });
      });
      await page.route('**/api/profile-patch', async (route) => {
        sent.push(route.request().postDataJSON());
        await route.fulfill({ status: write.status, json: write.body });
      });
      const body = record(write.request.body),
        path = body['path'] as string[];
      const key = path.slice(2).join('.');
      await openMore(page, width, `#runs/saved/${path[1]}`, 'profile_target_runs_saved');
      const row = moreEntry(page, key);
      await expect(row).toBeVisible();
      if (body['op'] === 'delete' && (key === 'label' || key === 'rides')) {
        await row
          .getByRole('button', {
            name: key === 'label' ? 'Clear label' : 'Use the quest default',
            exact: true,
          })
          .click();
      } else {
        if (await row.getByRole('combobox').count()) {
          if (key === 'dissonant_tab')
            await expect(
              row.getByRole('option', { name: 'Coin run (remove the disabled tab)', exact: true }),
            ).toHaveAttribute('value', '');
          await row
            .getByRole('combobox')
            .selectOption(body['op'] === 'delete' ? '' : String(body['value']));
        } else await row.getByRole('textbox').fill(String(body['value']));
        await row.getByRole('button', { name: 'Save field', exact: true }).click();
      }
      await expect.poll(() => sent.length).toBe(1);
      expect(sent).toEqual([write.request.body]);
      await expect(row.locator('[data-outcome="sending"]')).toBeVisible();
      await expect(row.locator('[data-outcome="saved"]')).toHaveCount(0);
      source = after;
      const count = reads;
      await editor(page).getByRole('button', { name: 'Read entry settings', exact: true }).click();
      await expect.poll(() => reads).toBeGreaterThan(count);
      await expect(row.locator('[data-outcome="saved"]')).toBeVisible();
      expect(sent).toEqual([write.request.body]);
      if (body['op'] === 'delete') expect(body).not.toHaveProperty('value');
      if (key === 'dissonant_tab' || key.startsWith('policies.'))
        await expect(row.getByRole('combobox')).toHaveValue('');
      else if (body['op'] !== 'delete') await expect(row.getByRole('textbox')).toHaveValue('2');
      await proof(page, `f6-14c-r12f-${name}`, width);
    });
  }

for (const width of [1360, 1054, 390])
  for (const name of [
    'inactive_run_entry_stale_revision_409',
    'inactive_run_entry_refused_after_account_switch',
    'inactive_perk_bans_thirty_one_entries_is_refused',
  ]) {
    test(`PE6 F6-14c ${name} keeps draft and refusal through reload at ${width}`, async ({
      page,
    }) => {
      const answer = moreCase(name),
        body = record(answer.request.body),
        path = body['path'] as string[];
      const world = await moreWorld(page, moreSourceBefore(answer));
      world.answer(answer);
      await openMore(page, width, `#runs/saved/${path[1]}`, 'profile_target_runs_saved');
      const row = moreEntry(page, path.slice(2).join('.'));
      const value = body['value'],
        raw = Array.isArray(value)
          ? value.join('\n')
          : typeof value === 'number' || typeof value === 'string'
            ? String(value)
            : '';
      await row.getByRole('textbox').fill(raw);
      await row.getByRole('button', { name: 'Save field', exact: true }).click();
      await expect(
        row
          .locator('[data-server-text="message"]')
          .filter({ hasText: String(own(answer.body, 'error')) })
          .first(),
      ).toBeVisible();
      expect(world.sent).toEqual([answer.request.body]);
      await page.reload();
      await expect(row.getByRole('textbox')).toHaveValue(raw);
      await expect(
        row
          .locator('[data-server-text="message"]')
          .filter({ hasText: String(own(answer.body, 'error')) })
          .first(),
      ).toBeVisible();
      expect(world.sent).toHaveLength(1);
      await proof(page, `f6-14c-${name}-reload`, width);
    });
  }

for (const width of [1360, 1054, 390])
  for (const name of moreNames.filter((name) => name.startsWith('inactive_saved_'))) {
    test(`PE6 F6-14c ${name} exact request and readback at ${width}`, async ({ page }) => {
      test.setTimeout(30_000);
      const entry = moreCase(name),
        body = record(entry.request.body);
      const path = body['path'] as string[],
        value = body['value'];
      const world = await moreWorld(page, moreSourceBefore(entry));
      world.answer(entry);
      await openMore(page, width, `#runs/saved/${path[1]}`, 'profile_target_runs_saved');
      const row = moreEntry(page, path.slice(2).join('.'));
      await expect(row).toBeVisible();
      if (body['op'] === 'delete' && path.at(-1) === 'perk_bans')
        await row.getByRole('checkbox').check();
      else if (await row.getByRole('combobox').count())
        await row.getByRole('combobox').selectOption(body['op'] === 'delete' ? '' : String(value));
      else if (typeof value === 'boolean') await row.getByRole('checkbox').setChecked(value);
      else
        await row
          .getByRole('textbox')
          .fill(
            Array.isArray(value)
              ? value.join('\n')
              : value === null
                ? ''
                : typeof value === 'number' || typeof value === 'string'
                  ? String(value)
                  : '',
          );
      await row.getByRole('button', { name: 'Save field', exact: true }).click();
      await expect(row.locator('[data-outcome="saved"]')).toBeVisible();
      expect(world.sent).toEqual([entry.request.body]);
      const count = world.reads();
      await editor(page).getByRole('button', { name: 'Read entry settings', exact: true }).click();
      await expect.poll(world.reads).toBeGreaterThan(count);
      if (
        [
          'inactive_saved_tier',
          'inactive_saved_shard_count_zero',
          'inactive_saved_tournament_gem_cap_zero_never_pays',
          'inactive_saved_uw_policy_deleted_means_no_uw_plan',
          'inactive_saved_perk_bans_deleted_leaves_the_games_bans_alone',
        ].includes(name)
      )
        await proof(page, `f6-14c-${name}`, width);
    });
  }
const editor = (page: Page) => page.locator('[data-run-entry]');
const field = (page: Page, key: string) => editor(page).locator(`[data-entry-field="${key}"]`);

async function open(page: Page, scenario: string, width: number, ready = true) {
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`/__fake/use?scenario=${scenario}`);
  await page.evaluate(() => {
    window.location.hash = '#profiles';
  });
  const picker = page.locator('[data-form="editing-target"]');
  await picker.getByRole('combobox').selectOption('second');
  await picker.getByRole('button', { name: 'Edit this profile', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(picker.locator('[data-editing-target="second"]')).toHaveAttribute(
    'data-target-blocked',
    '',
  );
  expect(await posts(page)).toEqual([]);
  await page.evaluate(() => {
    window.location.hash = '#runs/saved/farm';
  });
  if (!ready) return;
  await expect(field(page, 'label').getByLabel('Run label', { exact: true })).toHaveValue(
    'Second farm',
  );
  await expect(editor(page).locator('[data-editing-target="second"]')).toContainText(
    'Active profile:mine',
  );
}

async function proof(page: Page, state: string, width: number) {
  const violations = await axeIds(page, WCAG_AA_TAGS);
  expect(violations).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBe(0);
  const small = await page
    .locator('main button, main input, main select, main textarea, main summary')
    .evaluateAll(
      (elements, mobile) =>
        elements
          .filter((element) => element.getClientRects().length > 0)
          .filter((element) => {
            const rect = element.getBoundingClientRect();
            return (
              rect.width < 24 || rect.height < (mobile && element.tagName === 'BUTTON' ? 44 : 24)
            );
          })
          .map((element) => element.outerHTML.slice(0, 150)),
      width === 390,
    );
  expect(small).toEqual([]);
  await page.screenshot({
    path: path.join(OUT, `profile-target-runs-${state}-${width}.png`),
    fullPage: true,
  });
}

for (const width of [1360, 1054, 390]) {
  test(`PE6 target label/loadout exact bodies, active-only forms, return, keyboard and axe at ${width}`, async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await open(page, 'profile_target_runs_saved', width);
    const sections = page.locator('[data-runs-active-only]');
    expect(await sections.count()).toBeGreaterThanOrEqual(3);
    for (const section of await sections.all()) {
      await expect(section).toHaveAttribute('disabled', '');
      await expect(section).toContainText('Active profile:mine');
      await expect(section).toContainText('Return editing to the active profile to change these');
      for (const button of await section.getByRole('button').all())
        await expect(button).toBeDisabled();
    }
    await proof(page, 'picked', width);
    const label = field(page, 'label');
    await label.getByLabel('Run label', { exact: true }).fill('Offline farm');
    await label.getByRole('button', { name: 'Save field', exact: true }).focus();
    await page.keyboard.press('Enter');
    await expect(label.locator('[data-outcome="saved"]')).toBeVisible();
    await expect(label.getByRole('button', { name: 'Save field', exact: true })).toBeFocused();
    const loadout = field(page, 'loadout');
    await loadout.getByLabel('Run equipment', { exact: true }).selectOption('plain');
    await loadout.getByRole('button', { name: 'Save field', exact: true }).focus();
    await page.keyboard.press('Enter');
    await expect(loadout.locator('[data-outcome="saved"]')).toBeVisible();
    expect(await bodiesTo(page, '/api/profile-patch')).toEqual([
      sample('inactive_run_entry_label_saved').request.body,
      sample('inactive_run_entry_loadout_saved').request.body,
    ]);
    await expect(label.getByLabel('Run label', { exact: true })).toHaveValue('Offline farm');
    await expect(loadout.getByLabel('Run equipment', { exact: true })).toHaveValue('plain');
    await proof(page, 'saved', width);
    await editor(page)
      .getByRole('link', { name: 'Change the editing profile in Profiles' })
      .click();
    await page
      .locator('[data-form="editing-target"]')
      .getByRole('button', { name: 'Return editing to the active profile', exact: true })
      .focus();
    await page.keyboard.press('Enter');
    await page.evaluate(() => {
      window.location.hash = '#runs/saved/farm';
    });
    await expect(editor(page).locator('[data-editing-target="mine"]')).toContainText(
      'Editing the active profile:mine',
    );
    await expect(field(page, 'label').getByLabel('Run label', { exact: true })).toHaveValue('');
    await expect(page.locator('[data-runs-active-only][disabled]')).toHaveCount(0);
    expect(await posts(page)).toHaveLength(2);
    await proof(page, 'returned', width);
    expect(errors).toEqual([]);
  });

  test(`PE6 changed click-time context sends nothing and keeps second draft at ${width}`, async ({
    page,
  }) => {
    await open(page, 'profile_target_runs_context_changed', width);
    await field(page, 'label')
      .getByLabel('Run label', { exact: true })
      .fill('Keep this second draft');
    // Inject only the genuinely recorded config answer after drawing, without changing
    // the app's runtime epoch first; this exercises the click-time guard itself.
    const changed = sample('read_config_after_editor_active_profile_switch');
    await page.route('**/api/config', (route) =>
      route.fulfill({ status: changed.status, json: changed.body }),
    );
    await field(page, 'label').getByRole('button', { name: 'Save field', exact: true }).click();
    await expect(editor(page).getByRole('status')).toContainText('Nothing sent');
    await expect(field(page, 'label').getByLabel('Run label', { exact: true })).toHaveValue(
      'Keep this second draft',
    );
    expect(await posts(page)).toEqual([]);
    await proof(page, 'context-changed', width);
  });
}
const record = (value: unknown) => value as Record<string, unknown>;
async function targetRead(page: Page, initial = sample('same_account_inactive_target_source')) {
  let answer: DashboardCase = initial;
  let held = false;
  const releases: (() => void)[] = [];
  let count = 0;
  await page.route('**/api/profile-src/second', async (request) => {
    count++;
    if (held) await new Promise<void>((resolve) => releases.push(resolve));
    await request.fulfill({ status: answer.status, json: answer.body });
  });
  return {
    set: (next: DashboardCase) => (answer = next),
    hold: () => (held = true),
    release: () => {
      held = false;
      for (const release of releases.splice(0)) release();
    },
    count: () => count,
  };
}
const check = (page: Page) =>
  editor(page).getByRole('button', { name: 'Read entry settings', exact: true });
const labelInput = (page: Page) => field(page, 'label').getByLabel('Run label', { exact: true });
const saveField = (page: Page, key = 'label') =>
  field(page, key).getByRole('button', { name: 'Save field', exact: true });

for (const width of [1360, 1054, 390]) {
  test(`PE6 held inactive source loading then empty run library, keyboard and axe at ${width}`, async ({
    page,
  }) => {
    // Modified recorded case: remove only second's blueprints to exercise an
    // empty library while preserving the recorded source identity and revision.
    const empty = sample('same_account_inactive_target_source');
    record(own(empty.body, 'profile'))['blueprints'] = {};
    const source = await targetRead(page, empty);
    source.hold();
    await open(page, 'profile_target_runs_negative', width, false);
    await expect.poll(source.count).toBeGreaterThan(0);
    await expect(
      editor(page).getByText('Read this profile and its field vocabulary to edit entry settings.', {
        exact: true,
      }),
    ).toBeVisible();
    await expect(editor(page).locator('[data-entry-field]')).toHaveCount(0);
    await check(page).focus();
    await expect(check(page)).toBeFocused();
    expect(await posts(page)).toEqual([]);
    await proof(page, 'loading', width);
    source.release();
    await expect(
      editor(page).getByRole('combobox', { name: 'Entry run', exact: true }).locator('option'),
    ).toHaveCount(1);
    await expect(editor(page).locator('[data-entry-field]')).toHaveCount(0);
    await expect(check(page)).toBeFocused();
    // Explicit farm URL remains named/unlisted; it must never fall back to mine.
    await expect(
      editor(page).getByRole('combobox', { name: 'Entry run', exact: true }),
    ).toHaveValue('farm');
    await proof(page, 'empty', width);
    expect(await posts(page)).toEqual([]);
  });

  test(`PE6 loadout refusal associates exact server text with field outcome and keeps keyboard focus at ${width}`, async ({
    page,
  }) => {
    await open(page, 'profile_target_runs_refused', width);
    const row = field(page, 'loadout');
    await row.getByLabel('Run equipment', { exact: true }).selectOption('plain');
    // Modified recorded refusal: equipment existed when drawn, then vanished.
    // Replace only ghost with plain in error/problems and remove plain from the
    // remaining choices; preserve recorded HTTP status and error shape.
    const refusal = dashboardPagesCase('run_entry_patch.loadout_unknown_equipment');
    const error = String(own(refusal.body, 'error'))
      .replace("'ghost'", "'plain'")
      .replace('later, plain, spare', 'later, spare');
    record(refusal.body)['error'] = error;
    record(refusal.body)['problems'] = [error];
    await page.route('**/api/profile-patch', async (request) => {
      // Record the real request on the fixture server before replacing its answer.
      await request.fetch();
      await request.fulfill({ status: refusal.status, json: refusal.body });
    });
    await saveField(page, 'loadout').focus();
    await page.keyboard.press('Enter');
    const outcome = row.locator('[data-outcome="refused"]');
    await expect(outcome.locator('[data-refusal]')).toHaveText(error);
    await expect(outcome.locator('[data-server-text]').first()).toBeVisible();
    await expect(row.getByLabel('Run equipment', { exact: true })).toHaveValue('plain');
    await expect(saveField(page, 'loadout')).toBeFocused();
    expect(await bodiesTo(page, '/api/profile-patch')).toEqual([
      {
        ...record(sample('inactive_run_entry_loadout_saved').request.body),
        expected_revision: own(sample('same_account_inactive_target_source').body, 'revision'),
      },
    ]);
    await proof(page, 'refused', width);
  });

  test(`PE6 stale inactive revision retains label draft and keyboard focus at ${width}`, async ({
    page,
  }) => {
    await open(page, 'profile_target_runs_negative', width);
    await labelInput(page).fill('Offline farm');
    await saveField(page).focus();
    await page.keyboard.press('Enter');
    const outcome = field(page, 'label').locator('[data-outcome="refused"]');
    // Modified recorded case: plan-path stale refusal is revision-level and
    // returned unchanged for this inactive label patch.
    await expect(outcome.locator('[data-refusal]')).toHaveText(
      String(own(sample('inactive_target_stale_revision').body, 'error')),
    );
    await expect(labelInput(page)).toHaveValue('Offline farm');
    await expect(saveField(page)).toBeDisabled();
    await check(page).focus();
    await expect(check(page)).toBeFocused();
    expect(await bodiesTo(page, '/api/profile-patch')).toEqual([
      {
        ...record(sample('inactive_run_entry_label_saved').request.body),
        expected_revision: own(sample('same_account_inactive_target_source').body, 'revision'),
      },
    ]);
    await proof(page, 'stale', width);
  });

  for (const failure of ['failed', 'answer_failed', 'lost'] as const) {
    test(`PE6 ${failure} reply stays Unknown on unchanged read and settles original target advanced read at ${width}`, async ({
      page,
    }) => {
      const source = await targetRead(page, sample('inactive_target_after_write_failed'));
      await open(page, `profile_target_runs_${failure}`, width);
      await labelInput(page).fill('Offline farm');
      await saveField(page).focus();
      await page.keyboard.press('Enter');
      const unknown = field(page, 'label').locator('[data-outcome="unknown"]');
      await expect(unknown).toBeVisible();
      const count = source.count();
      await check(page).focus();
      await page.keyboard.press('Enter');
      await expect.poll(source.count).toBeGreaterThan(count);
      await expect(unknown).toBeVisible();
      await expect(saveField(page)).toBeDisabled();
      await expect(check(page)).toBeFocused();
      await proof(page, `unknown-${failure}`, width);
      // Modified recorded case: failure patches plan. Change only the advanced
      // read's farm label to the recorded inactive label-save value; retain
      // original target identity, advanced revision and remaining source data.
      const advanced = sample('inactive_target_after_written_then_answer_failed');
      record(ownPath(advanced.body, 'profile', 'blueprints', 'farm'))['label'] = 'Offline farm';
      source.set(advanced);
      await page.keyboard.press('Enter');
      await expect(field(page, 'label').locator('[data-outcome="applied"]')).toBeVisible();
      await expect(labelInput(page)).toHaveValue('Offline farm');
      await expect(check(page)).toBeFocused();
      expect(await posts(page)).toHaveLength(1);
      await proof(page, `settled-${failure}`, width);
    });
  }
}
