import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { entries, own } from '../../src/core/read.ts';
import { dashboardPagesCase as recorded, type DashboardCase } from '../support/dashboardFixture.ts';
import { parityCase, paritySourceBefore } from '../support/runEntryParity.ts';
import { withRecordedFence, fencedRuntime } from '../support/profilePatchFenced.ts';
import { axeIds, WCAG_AA_TAGS } from './helpers/helpers.ts';

const OUT = process.env['TP_F6_SCREENS_DIR'] ?? path.resolve('test-results/f6-screens');
mkdirSync(OUT, { recursive: true });
const field = (page: Page, key: string) => page.locator(`[data-entry-field="${key}"]`);
async function sourceRoute(page: Page, read: () => DashboardCase) {
  await page.route('**/api/profile-src/mine', (route) =>
    route.fulfill({ status: 200, json: read().body }),
  );
}
async function proof(page: Page, width: number, state: string) {
  const violations = await axeIds(page, WCAG_AA_TAGS);
  expect(violations).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBe(0);
  expect(
    await page
      .locator(
        '[data-run-entry] button, [data-run-entry] input, [data-run-entry] select, [data-run-entry] a',
      )
      .evaluateAll(
        (elements, mobile) =>
          elements
            .filter((e) => e.getClientRects().length > 0)
            .filter((e) => {
              const box = e.getBoundingClientRect();
              return box.width < 24 || box.height < (mobile && e.tagName === 'BUTTON' ? 44 : 24);
            })
            .map((e) => e.outerHTML),
        width === 390,
      ),
  ).toEqual([]);
  await page.screenshot({
    path: path.join(OUT, `runs-entry-parity-${state}-${width}.png`),
    fullPage: true,
  });
}
async function start(page: Page, run: string, initial: DashboardCase, reply: () => DashboardCase) {
  let source = initial;
  const writes: unknown[] = [];
  await sourceRoute(page, () => source);
  await page.route('**/api/profile-patch', async (route) => {
    writes.push(route.request().postDataJSON());
    const entry = reply();
    // Intermediate GETs are absent: return the recorded receipt's verbatim profile/revision
    // in the recorded source envelope, preserving all values without simulating mutations.
    if (entry.status === 200)
      source = {
        ...source,
        body: {
          ...Object.fromEntries(entries(source.body)),
          profile: own(entry.body, 'profile'),
          revision: own(entry.body, 'revision'),
        },
      };
    await route.fulfill({ status: entry.status, json: entry.body });
  });
  await page.goto('/__fake/use?scenario=runs_entry_saved');
  await page.evaluate((run) => {
    window.location.hash = `#runs/saved/${run}`;
  }, run);
  await expect(field(page, 'label').getByRole('textbox')).toBeVisible();
  return writes;
}
for (const width of [1360, 1054, 390]) {
  test(`RL11 pre-save Chain Lightning reason and specific rescue policy at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const source = recorded(
      'ability_policies.abilities_the_account_does_not_own_are_bound_to_a_run_is_a_problem',
    );
    const writes = await start(page, 'farm_deep', source, () => parityCase('saved_label'));
    await expect(field(page, 'policies.uw')).toContainText(
      'This policy needs Chain Lightning, which the player does not own.',
    );
    await proof(page, width, 'uw-reason');
    await field(page, 'policies.rescue').getByRole('link').click();
    await expect(page).toHaveURL(/#abilities\/rescue_policies\/wall_drain$/);
    await expect(page.locator('#abilities-editor')).toContainText('wall_drain');
    await expect(page.getByRole('combobox', { name: 'Saved plans', exact: true })).toHaveValue(
      'rescue_policies',
    );
    // RL11 review regression: a bare-page visit and another selection must not
    // suppress opening the same specific policy again.
    await page.evaluate(() => {
      window.location.hash = '#abilities';
    });
    await page
      .getByRole('combobox', { name: 'Saved plans', exact: true })
      .selectOption('uw_policies');
    await page.getByRole('button', { name: 'Farm choreography', exact: true }).click();
    await expect(page.locator('#abilities-editor')).toContainText('farm_cl');
    await page.evaluate(() => {
      window.location.hash = '#abilities/rescue_policies/wall_drain';
    });
    await expect(page.locator('#abilities-editor')).toContainText('wall_drain');
    await expect(page.getByRole('combobox', { name: 'Saved plans', exact: true })).toHaveValue(
      'rescue_policies',
    );
    await page.evaluate(() => {
      window.location.hash = '#abilities/uw_policies/farm_cl';
    });
    await expect(page.locator('#abilities-editor')).toContainText('farm_cl');
    await expect(page.getByRole('combobox', { name: 'Saved plans', exact: true })).toHaveValue(
      'uw_policies',
    );
    expect(writes).toEqual([]);
  });
  test(`RL9 RL10 RL11 label clear, statistics notice and exact Abilities targets at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const entry = parityCase('label_deleted_the_run_id_shows');
    const writes = await start(page, 'farm', paritySourceBefore(entry), () => entry);
    await field(page, 'label').getByRole('button', { name: 'Clear label', exact: true }).focus();
    await page.keyboard.press('Enter');
    await expect(field(page, 'label').locator('[data-outcome="saved"]')).toBeVisible();
    await expect(field(page, 'label').getByRole('textbox')).toHaveValue('');
    await expect(
      page.getByRole('combobox', { name: 'Entry run', exact: true }).locator('option:checked'),
    ).toHaveText('farm');
    // The parity case predates expected_context; only that runtime fence is added.
    expect(writes).toEqual([withRecordedFence(entry, fencedRuntime).request.body]);
    await expect(page.locator('[data-entry-statistics] dt')).toHaveText('Statistics');
    await expect(page.locator('[data-entry-statistics] dd')).toHaveText('Always on');
    await expect(page.locator('[data-entry-statistics] input')).toHaveCount(0);
    await proof(page, width, 'label-cleared');
    await page.getByRole('combobox', { name: 'Entry run', exact: true }).selectOption('farm_deep');
    await expect(field(page, 'policies.rescue').getByRole('link')).toHaveAttribute(
      'href',
      '#abilities/rescue_policies/wall_drain',
    );
    await field(page, 'policies.uw').getByRole('link').click();
    await expect(page).toHaveURL(/#abilities\/uw_policies\/farm_cl$/);
    await expect(page.locator('#abilities-editor')).toContainText('farm_cl');
    await expect(
      page.getByRole('button', { name: 'Farm choreography', exact: true }),
    ).toHaveAttribute('aria-pressed', 'true');
  });
  test(`RL12 switch dissonance tabs and delete to switch back at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    let entry = parityCase('coin_run_switched_to_dissonance');
    const writes = await start(page, 'farm', paritySourceBefore(entry), () => entry);
    for (const [name, value] of [
      ['coin_run_switched_to_dissonance', 'attack'],
      ['dissonance_run_retargeted_to_another_tab', 'defense'],
      ['dissonance_run_switched_back_to_coin_by_deleting_the_tab', ''],
    ]) {
      entry = parityCase(name ?? '');
      await field(page, 'dissonant_tab')
        .getByRole('combobox')
        .selectOption(value ?? '');
      await field(page, 'dissonant_tab')
        .getByRole('button', { name: 'Save field', exact: true })
        .click();
      await expect
        .poll(() => writes.length)
        .toBe(
          name === 'coin_run_switched_to_dissonance'
            ? 1
            : name === 'dissonance_run_retargeted_to_another_tab'
              ? 2
              : 3,
        );
      await expect(
        field(page, 'dissonant_tab').getByRole('button', { name: 'Save field', exact: true }),
      ).toHaveAttribute('aria-disabled', 'true');
      expect(writes.at(-1)).toEqual(withRecordedFence(entry, fencedRuntime).request.body);
    }
    await expect(field(page, 'dissonant_tab').getByRole('combobox')).toHaveValue('');
    await proof(page, width, 'coin-restored');
  });
  for (const key of ['rides', 'cycles']) {
    test(`RL13 ${key} save and exact refusal retains the draft at ${width}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      let entry = parityCase(`saved_${key}`);
      const writes = await start(
        page,
        key === 'rides' ? 'quest_sm' : 'quest_ilm',
        paritySourceBefore(entry),
        () => entry,
      );
      await field(page, key)
        .getByRole('textbox')
        .fill(String(own(entry.request.body, 'value')));
      await field(page, key).getByRole('button', { name: 'Save field', exact: true }).click();
      await expect(field(page, key).locator('[data-outcome="saved"]')).toBeVisible();
      expect(writes[0]).toEqual(withRecordedFence(entry, fencedRuntime).request.body);
      // The refusal was recorded after the sequence of successful saves. Read that
      // unchanged source before editing, retaining the refusal's exact CAS revision.
      const refused = parityCase(`${key}_fraction_refused`);
      await sourceRoute(page, () => paritySourceBefore(refused));
      await page.getByRole('button', { name: 'Read entry settings', exact: true }).click();
      await expect(field(page, key).getByRole('textbox')).toHaveValue(key === 'rides' ? '' : '1');
      entry = refused;
      await field(page, key)
        .getByRole('textbox')
        .fill(String(own(entry.request.body, 'value')));
      await field(page, key).getByRole('button', { name: 'Save field', exact: true }).click();
      await expect(field(page, key).locator('[data-refusal]')).toHaveText(
        String(own(entry.body, 'error')),
      );
      await expect(field(page, key).getByRole('textbox')).toHaveValue(
        String(own(entry.request.body, 'value')),
      );
      expect(writes[1]).toEqual(withRecordedFence(entry, fencedRuntime).request.body);
      await proof(page, width, `${key}-refused`);
    });
  }
  test(`RL8 plan removal refreshes Runs and adding it back restores the catalogue at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const plan = (name: string) => recorded(`plan_removal_effects.${name}`);
    let source = plan('read_profile_before_the_removal'),
      removed = false,
      readsAfterRemoval = 0;
    await sourceRoute(page, () => source);
    await page.route('**/api/runs', (route) => {
      if (removed) readsAfterRemoval++;
      return route.fulfill({
        status: 200,
        json: plan(
          removed
            ? 'read_runs_after_the_removal_the_day_plan_run_is_gone'
            : 'read_runs_before_the_removal_the_day_plan_run_is_listed',
        ).body,
      });
    });
    const writes: unknown[] = [];
    let release = () => {};
    const receipt = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route('**/api/profile-patch', async (route) => {
      writes.push(route.request().postDataJSON());
      await receipt;
      removed = true;
      source = plan('read_profile_after_the_removal');
      await route.fulfill({ status: 200, json: plan('plan_section_removed').body });
    });
    await page.goto('/__fake/use?scenario=runs_entry_saved');
    await page.evaluate(() => {
      window.location.hash = '#schedule';
    });
    await page.getByRole('button', { name: 'Remove the day plan', exact: true }).click();
    await page
      .getByRole('dialog')
      .getByRole('button', { name: 'Remove day plan', exact: true })
      .click();
    await expect.poll(() => writes.length).toBe(1);
    // Leave while the write is pending: the cached Schedule owner must still
    // refresh Runs when its answer arrives after the route unmounts.
    await page.evaluate(() => {
      window.location.hash = '#runs';
    });
    await expect(
      page.getByText('Day plans choose a run for each block; edit them on Schedule', {
        exact: true,
      }),
    ).toBeVisible();
    release();
    await expect.poll(() => readsAfterRemoval).toBeGreaterThan(0);
    await page.evaluate(() => {
      window.location.hash = '#runs';
    });
    const picker = page.getByRole('combobox', { name: 'Run', exact: true });
    const gone = own(plan('read_runs_after_the_removal_the_day_plan_run_is_gone').body, 'runs');
    await expect
      .poll(async () => picker.locator('option').allTextContents())
      .toEqual(
        Object.values(Object.fromEntries(entries(gone)))
          .filter((r) => own(r, 'label') !== undefined)
          .map((r) => own(r, 'label')),
      );
    expect(writes).toHaveLength(1);
    expect(writes[0]).toEqual(
      withRecordedFence(plan('plan_section_removed'), fencedRuntime).request.body,
    );
    await expect(
      page.getByText('Day plans choose a run for each block; edit them on Schedule', {
        exact: true,
      }),
    ).toHaveCount(0);
    await proof(page, width, 'plan-removed');
    removed = false;
    source = plan('read_profile_before_the_removal');
    await page.route('**/api/runs', (route) =>
      route.fulfill({
        status: 200,
        json: plan('read_runs_after_the_plan_was_added_back_the_day_plan_run_is_listed').body,
      }),
    );
    await page.reload();
    const listed = entries(
      own(plan('read_runs_after_the_plan_was_added_back_the_day_plan_run_is_listed').body, 'runs'),
    ).filter(([key]) => key !== 'combo');
    await expect(picker.locator('option')).toHaveCount(listed.length);
    await expect(
      page.getByText('Day plans choose a run for each block; edit them on Schedule', {
        exact: true,
      }),
    ).toBeVisible();
  });
}
