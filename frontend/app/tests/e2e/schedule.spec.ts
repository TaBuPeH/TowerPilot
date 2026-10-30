import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { axeCounts, bodiesTo, expectAppStyles, posts, WCAG_AA_TAGS } from './helpers/helpers.ts';
import { dashboardPagesCase as pagesCase } from '../support/dashboardFixture.ts';
import { own, ownPath, entries } from '../../src/core/read.ts';
import { scheduleRace } from './helpers/schedule-race.ts';

function gate() {
  let release: (() => void) | undefined;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { pending, release: () => release?.() };
}

const OUT =
  process.env['TP_F6_SCREENS_DIR'] ?? path.resolve('playwright-report/f6-schedule-screens');
mkdirSync(OUT, { recursive: true });
const widths = [1360, 1054, 390];

/**
 * Open Schedule; unless `coldStart`, wait until the plan is read (Read saved plan is enabled
 * only once the target is known and its read finished).
 */
async function open(
  page: Page,
  width = 1360,
  options: Record<string, string> = {},
  coldStart = false,
) {
  await page.setViewportSize({ width, height: 900 });
  await page.request.get('/__fake/use?scenario=runs_saved');
  await page.request.get(`/__fake/schedule?${new URLSearchParams(options)}`);
  await page.goto(`/ui/app/?schedule-test=${encodeURIComponent(JSON.stringify(options))}#schedule`);
  await expect(
    page.getByRole('heading', { level: 1, name: 'Schedule', exact: true }),
  ).toBeVisible();
  if (!coldStart)
    await expect(page.getByRole('button', { name: 'Read saved plan and status' })).toBeEnabled();
}
const counts = (page: Page) => page.getByRole('textbox', { name: 'Runs/day (blank = unlimited)' });
const blocks = (page: Page) => page.getByRole('group', { name: /^Block / });
const save = (page: Page) => page.getByRole('button', { name: 'Save schedule', exact: true });
async function mapSunday(page: Page) {
  await page.getByText('Edit weekday mapping', { exact: true }).click();
  await page.getByLabel('Sunday', { exact: true }).fill('tourney_day');
}
async function screenshot(page: Page, name: string, width: number) {
  await page.screenshot({ path: path.join(OUT, `schedule-${name}-${width}.png`), fullPage: true });
}

for (const width of widths) {
  test(`S1 S2 S5 keyboard editing, day drafts and Runs/Profiles return at ${width}`, async ({
    page,
  }) => {
    await open(page, width);
    await expect(counts(page).first()).toHaveValue('100');
    await counts(page).first().fill('1.00');
    await page.getByRole('button', { name: 'tourney_day', exact: true }).click();
    await expect(counts(page).first()).toHaveValue('1');
    await page.getByRole('button', { name: 'farm_day', exact: true }).click();
    await expect(counts(page).first()).toHaveValue('1.00');
    const up = blocks(page).nth(1).getByRole('button', { name: 'Move up', exact: true });
    await up.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('combobox', { name: 'Saved run' }).first()).toHaveValue('farm');
    await expect(page.getByRole('combobox', { name: 'Saved run' }).first()).toBeFocused();
    const remove = blocks(page).first().getByRole('button', { name: 'Delete block' });
    await remove.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('combobox', { name: 'Saved run' }).first()).toBeFocused();
    await expect(counts(page).first()).toHaveValue('1.00');
    await blocks(page).first().getByRole('link', { name: 'Edit run in Runs' }).click();
    await expect(page).toHaveURL(/#runs\/saved\/shards$/u);
    await page.goBack();
    await expect(counts(page).first()).toHaveValue('1.00');
    await expect(
      blocks(page).first().getByRole('link', { name: 'Edit run in Runs' }),
    ).toBeFocused();
    await page.getByRole('link', { name: 'Open Profiles', exact: true }).click();
    await page.goBack();
    await expect(counts(page).first()).toHaveValue('1.00');
    await expect(page.getByRole('link', { name: 'Open Profiles', exact: true })).toBeFocused();
    expect(await posts(page)).toEqual([]);
    await screenshot(page, 'keyboard-draft', width);
  });
  test(`S2 S3 S4 invalid raw count and week mapping remain visible after refusal at ${width}`, async ({
    page,
  }) => {
    await open(page, width, { save: 'profile_patch.count_fraction' });
    await counts(page).first().fill('1.9999999999999999999');
    await page.getByText('Edit weekday mapping', { exact: true }).click();
    await page.getByLabel('Monday', { exact: true }).fill('unknown_day');
    await save(page).click();
    await expect(
      page.getByText('Schedule save refused. Your draft is kept.', { exact: true }),
    ).toBeVisible();
    await expect(counts(page).first()).toHaveValue('1.9999999999999999999');
    await expect(counts(page).first()).toHaveAttribute('aria-invalid', 'true');
    await expect(page.getByLabel('Monday', { exact: true })).toHaveValue('unknown_day');
    const sent = await bodiesTo(page, '/api/profile-patch');
    expect(sent).toHaveLength(1);
    // Supplementary raw invalid count/week journey: the fenced family has no invalid-plan
    // request. Preserve the complete recorded initial plan with only these user edits.
    const initial = pagesCase('profile_patch.read_before_the_first_save').body;
    const plan = ownPath(initial, 'profile', 'plan');
    const days = own(plan, 'days');
    const farm = own(days, 'farm_day') as readonly unknown[];
    expect(sent[0]).toEqual({
      expected_context: { instance: 'main', serial: 'fake:1', account: null, profile: 'mine' },
      expected_revision: own(initial, 'revision'),
      name: 'mine',
      path: ['plan'],
      value: {
        ...Object.fromEntries(entries(plan)),
        days: {
          ...Object.fromEntries(entries(days)),
          farm_day: [
            { ...Object.fromEntries(entries(farm[0])), count: '1.9999999999999999999' },
            ...farm.slice(1),
          ],
        },
        week: { ...Object.fromEntries(entries(own(plan, 'week'))), monday: 'unknown_day' },
      },
    });
    expect(await axeCounts(page, WCAG_AA_TAGS)).toEqual([]);
    await screenshot(page, 'refused', width);
  });
  test(`S3 unknown weekday mapping has an associated server error at ${width}`, async ({
    page,
  }) => {
    await open(page, width, { save: 'profile_patch.week_names_an_unknown_day_plan' });
    await page.getByText('Edit weekday mapping', { exact: true }).click();
    const sunday = page.getByLabel('Sunday', { exact: true });
    await sunday.fill('no_such_day');
    await save(page).click();
    await expect(sunday).toHaveValue('no_such_day');
    await expect(sunday).toHaveAttribute('aria-invalid', 'true');
    await expect(sunday).toHaveAccessibleDescription(/plan.week.sunday: unknown day plan/);
    await expect(page.getByLabel('Monday', { exact: true })).not.toHaveAttribute('aria-invalid');
    expect(await axeCounts(page, WCAG_AA_TAGS)).toEqual([]);
    await screenshot(page, 'week-refused', width);
  });
  test(`S3 S4 coherent week save, readback, duplicate protection and newer typing at ${width}`, async ({
    page,
  }) => {
    await open(page, width, {
      delay: '700',
      readback: 'profile_patch.read_after_the_stale_refusal_holds_the_other_clients_save',
    });
    await mapSunday(page);
    await save(page).dblclick();
    await expect(page.getByText(/Saving schedule/)).toBeVisible();
    await counts(page).first().fill('601');
    await expect(page.getByText(/Saved schedule\. This is not confirmation/)).toBeVisible();
    await expect(counts(page).first()).toHaveValue('601');
    await expect(page.getByText('Unsaved schedule draft', { exact: true })).toBeVisible();
    expect(await bodiesTo(page, '/api/profile-patch')).toHaveLength(1);
    await page.getByRole('button', { name: 'Read saved plan and status' }).click();
    await expect(page.getByRole('button', { name: 'Read saved plan and status' })).toBeEnabled();
    await expect(page.getByLabel('Sunday', { exact: true })).toHaveValue('tourney_day');
    await expect(counts(page).first()).toHaveValue('601');
    await screenshot(page, 'saved-newer-draft', width);
  });
  test(`S3 S4 F6-SCHEDULE-01 delayed pre-save read cannot undo an accepted save at ${width}`, async ({
    page,
  }) => {
    const startupEntered = gate();
    const releaseStartup = gate();
    await page.route('**/api/config', async (route) => {
      startupEntered.release();
      await releaseStartup.pending;
      await route.continue();
    });
    await open(
      page,
      width,
      { readback: 'profile_patch.read_after_the_stale_refusal_holds_the_other_clients_save' },
      true,
    );
    await startupEntered.pending;
    // Cold start: before the initial context/profile read the Read button is disabled
    // (nothing to read yet). Exercise that, then wait for the plan before arming the gates.
    await expect(page.getByText('Weekday mapping', { exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Read saved plan and status' })).toBeDisabled();
    releaseStartup.release();
    await mapSunday(page);
    await expect(page.getByRole('button', { name: 'Read saved plan and status' })).toBeEnabled();
    const saveEntered = gate();
    const releaseSave = gate();
    const readCaptured = gate();
    const releaseRead = gate();
    const readDelivered = gate();
    let captureNextRead = false;
    await page.route('**/api/profile-patch', async (route) => {
      saveEntered.release();
      await releaseSave.pending;
      await route.continue();
    });
    await page.route('**/api/profile-src/mine', async (route) => {
      if (!captureNextRead) {
        await route.continue();
        return;
      }
      captureNextRead = false;
      const captured = await route.fetch();
      const body: unknown = await captured.json();
      expect(body).toEqual(pagesCase('profile_patch.read_before_the_first_save').body);
      readCaptured.release();
      await releaseRead.pending;
      await route.fulfill({ response: captured });
      readDelivered.release();
    });
    await save(page).click();
    await saveEntered.pending;
    const read = page.getByRole('button', { name: 'Read saved plan and status' });
    captureNextRead = true;
    await read.click();
    await readCaptured.pending;
    await expect(read).toBeDisabled();
    releaseSave.release();
    await expect(page.getByText('No unsaved changes', { exact: true })).toBeVisible();
    const revision = String(
      own(pagesCase('profile_patch.multi_save_first_request_saved').body, 'revision'),
    );
    await expect(page.locator('.tp-schedule__revisions')).toContainText(revision);
    releaseRead.release();
    await readDelivered.pending;
    await expect(read).toBeEnabled();
    await expect(page.getByLabel('Sunday', { exact: true })).toHaveValue('tourney_day');
    await expect(page.locator('.tp-schedule__revisions')).toHaveText(
      new RegExp(`${revision}.*${revision}`),
    );
    await expect(page.getByText('No unsaved changes', { exact: true })).toBeVisible();
    await expect(page.getByText(/Saved schedule\. This is not confirmation/)).toBeVisible();
    const stored = await page.request.get('/api/profile-src/mine');
    expect(await stored.json()).toEqual(
      pagesCase('profile_patch.read_after_the_stale_refusal_holds_the_other_clients_save').body,
    );
    expect(await bodiesTo(page, '/api/profile-patch')).toHaveLength(1);
    await screenshot(page, 'accepted-save-read-fence', width);
  });
  test(`S4 F6-SCHEDULE-02 accepted save clears an earlier validator error at ${width}`, async ({
    page,
  }) => {
    await open(page, width, {
      profile: 'profile_reads.profile_src_validator_unavailable_is_reported_not_hidden',
    });
    await expect(
      page.getByText('RuntimeError: the validator could not be loaded', { exact: true }),
    ).toBeVisible();
    await mapSunday(page);
    await save(page).click();
    await expect(page.getByText(/Saved schedule\. This is not confirmation/)).toBeVisible();
    await expect(
      page.getByText('RuntimeError: the validator could not be loaded', { exact: true }),
    ).toHaveCount(0);
    await expect(page.getByText('No unsaved changes', { exact: true })).toBeVisible();
    expect(await bodiesTo(page, '/api/profile-patch')).toHaveLength(1);
    expect(await axeCounts(page, WCAG_AA_TAGS)).toEqual([]);
    await screenshot(page, 'recovered-validation', width);
  });
  test(`S6 observation, no fabricated quota, axe and no overflow at ${width}`, async ({ page }) => {
    await open(page, width);
    await expect(page.getByText('Farm', { exact: true })).toBeVisible();
    await expect(page.getByText('UTC', { exact: true })).toBeVisible();
    await expect(page.locator('time[datetime="2027-01-16T00:00:00.000Z"]')).toBeVisible();
    await page.getByText('Exact observation times', { exact: true }).click();
    await expect(page.getByText('2027-01-16T00:00:00+00:00', { exact: true })).toBeVisible();
    for (const label of ['Remaining quota', 'Next start']) {
      const facts = page
        .locator('.tp-facts__item')
        .filter({ has: page.locator('dt', { hasText: label }) });
      await expect(facts).toHaveCount(2);
      await expect(facts.nth(0).locator('dd')).toHaveText('Not reported');
      await expect(facts.nth(1).locator('dd')).toHaveText('Not reported');
    }
    await expectAppStyles(page);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    const short = await page
      .locator(
        '.tp-schedule button, .tp-schedule input, .tp-schedule select, .tp-schedule__actions a',
      )
      .evaluateAll((nodes) =>
        nodes
          .filter(
            (n) =>
              n.getBoundingClientRect().height > 0 &&
              n.getBoundingClientRect().height < (window.innerWidth <= 600 ? 43.5 : 31.5),
          )
          .map((n) => n.textContent),
      );
    expect(short).toEqual([]);
    expect(await axeCounts(page, WCAG_AA_TAGS)).toEqual([]);
    expect(await posts(page)).toEqual([]);
    await screenshot(page, 'observed', width);
  });
}

test('S1 missing plan has no fake hours; unreadable profile has an explicit read retry', async ({
  page,
}) => {
  await open(page, 390, {
    profile: 'schedule_plan.profile_without_a_plan_has_no_plan_member',
    status: 'schedule_plan.schedule_status_without_a_plan',
  });
  await expect(page.getByText(/This saved profile has no plan/)).toBeVisible();
  await expect(save(page)).toHaveCount(0);
  await screenshot(page, 'missing-plan', 390);
  await open(page, 390, { profile: 'profile_reads.profile_src_unreadable_yaml_400' });
  await expect(page.getByText(/Saved plan could not be read/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Read saved plan and status' })).toBeEnabled();
});
test('S1 with a slow context read, Read saved plan stays disabled with its reason until the plan is read', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await page.request.get('/__fake/use?scenario=runs_saved');
  await page.request.get('/__fake/schedule');
  const released = gate();
  await page.route('**/api/config', async (route) => {
    await released.pending;
    await route.continue();
  });
  await page.goto('/ui/app/?slow-context=1#schedule');
  const read = page.getByRole('button', { name: 'Read saved plan and status' });
  await expect(read).toBeDisabled();
  await expect(read).toHaveAccessibleDescription(
    'Select an emulator and profile before editing a schedule.',
  );
  await expect(blocks(page)).toHaveCount(0);
  released.release();
  await expect(read).toBeEnabled();
  // Enabled means read: the plan's blocks are there, so the S2 loop never starts empty.
  await expect(blocks(page)).toHaveCount(2);
});
test('S2 deleting the last block stays an invalid empty day and restores focus to Add', async ({
  page,
}) => {
  await open(page, 390, { save: 'profile_patch.day_emptied' });
  // The loop below must start from the read plan: with no blocks shown it would pass vacuously.
  await expect(blocks(page)).toHaveCount(2);
  while (await blocks(page).count())
    await blocks(page).first().getByRole('button', { name: 'Delete block' }).click();
  await expect(page.getByRole('button', { name: 'Add block' })).toBeFocused();
  await expect(page.getByText(/This day has no blocks/)).toBeVisible();
  await save(page).click();
  await expect(page.getByText(/Schedule save refused/)).toBeVisible();
  expect((await bodiesTo(page, '/api/profile-patch'))[0]).toMatchObject({
    expected_context: { instance: 'main', serial: 'fake:1', account: null, profile: 'mine' },
    value: { days: { farm_day: [] } },
  });
  await page.getByRole('button', { name: 'Add block' }).click();
  await expect(blocks(page)).toHaveCount(1);
});
test('S4 a lost answer settles by readback and preserves newer raw typing through reload', async ({
  page,
}) => {
  await open(page, 1054, {
    mode: 'drop',
    delay: '700',
    readback: 'profile_patch.read_after_the_stale_refusal_holds_the_other_clients_save',
  });
  await mapSunday(page);
  await save(page).click();
  // The click checks its context asynchronously; type only after the submitted
  // snapshot is frozen so this input is definitely newer than the request.
  await expect.poll(async () => (await bodiesTo(page, '/api/profile-patch')).length).toBe(1);
  await counts(page).first().fill('100.00');
  await expect(page.getByText(/Schedule save outcome unknown/)).toBeVisible();
  await page.reload();
  await expect(page.getByText(/Readback confirms the submitted plan is saved/)).toBeVisible();
  await expect(page.getByLabel('Sunday', { exact: true })).toHaveValue('tourney_day');
  await expect(counts(page).first()).toHaveValue('100.00');
  await expect(page.getByText('Unsaved schedule draft', { exact: true })).toBeVisible();
  await page.reload();
  await expect(counts(page).first()).toHaveValue('100.00');
  expect(await bodiesTo(page, '/api/profile-patch')).toHaveLength(1);
  await screenshot(page, 'lost-answer-readback', 1054);
});
test('S4 stale profile revision requires read, explicit reconcile and a new Save click', async ({
  page,
}) => {
  await open(page, 1360, {
    save: 'profile_patch.stale_revision_409',
    readback: 'profile_patch.read_after_a_partial_batch_holds_the_first_two',
  });
  await counts(page).first().fill('77');
  await save(page).click();
  await expect(page.getByText(/Schedule save refused/)).toBeVisible();
  await expect(save(page)).toBeDisabled();
  await expect(
    page.getByRole('button', { name: 'Use read revision; keep my draft' }),
  ).toBeDisabled();
  await page.getByRole('button', { name: 'Read saved plan and status' }).click();
  await expect(
    page.getByRole('button', { name: 'Use read revision; keep my draft' }),
  ).toBeEnabled();
  await page.getByRole('button', { name: 'Use read revision; keep my draft' }).click();
  await expect(counts(page).first()).toHaveValue('77');
  await expect(save(page)).toBeEnabled();
  expect(await bodiesTo(page, '/api/profile-patch')).toHaveLength(1);
  await screenshot(page, 'revision-reconciled', 1360);
});
// The two recorded families have different revision identities. These scenarios prove
// concurrent refusal recovery only; a coherent shared-revision fixture is still needed.
test('S4 concurrent Schedule/Runs refusal recovery keeps the losing Runs draft', async ({
  page,
  context,
}) => {
  await open(page, 1360, { race: 'true', delay: '800' });
  const runs = await context.newPage();
  await runs.goto('/ui/app/#runs/saved/bp_farm');
  const saved = runs.locator('[data-region="runsSaved"]');
  await expect(saved.getByRole('checkbox', { name: 'Ad gems On' })).toBeVisible();
  await saved.getByRole('checkbox', { name: 'Ad gems On' }).uncheck();
  await mapSunday(page);
  await save(page).click();
  await expect.poll(async () => (await bodiesTo(page, '/api/profile-patch')).length).toBe(1);
  const refusal = runs.waitForResponse(
    (response) =>
      response.url().endsWith('/api/run-controls') && response.request().method() === 'POST',
  );
  await saved.getByRole('button', { name: 'Save run settings' }).click();
  expect((await refusal).status()).toBe(409);
  await expect(saved.getByText('Not saved', { exact: true })).toBeVisible();
  await expect(saved.getByRole('checkbox', { name: 'Ad gems On' })).not.toBeChecked();
  expect(await bodiesTo(page, '/api/profile-patch')).toHaveLength(1);
  expect(await bodiesTo(page, '/api/run-controls')).toHaveLength(1);
  await expect(page.getByText(/Saved schedule\. This is not confirmation/)).toBeVisible();
  await runs.close();
});
test('S4 concurrent Runs/Schedule refusal recovery keeps the Schedule draft', async ({
  page,
  context,
}) => {
  await open(page, 1054, {
    race: 'true',
    delay: '700',
    readback: 'profile_patch.read_after_a_partial_batch_holds_the_first_two',
  });
  await counts(page).first().fill('77');
  const runs = await context.newPage();
  await runs.goto('/ui/app/#runs/saved/bp_farm');
  const saved = runs.locator('[data-region="runsSaved"]');
  await saved.getByRole('checkbox', { name: 'Ad gems On' }).uncheck();
  await saved.getByRole('button', { name: 'Save run settings' }).click();
  await expect.poll(async () => (await bodiesTo(page, '/api/run-controls')).length).toBe(1);
  const response = page.waitForResponse(
    (answer) => answer.url().endsWith('/api/profile-patch') && answer.request().method() === 'POST',
  );
  await save(page).click();
  expect((await response).status()).toBe(409);
  await expect(
    page.getByText('Schedule save refused. Your draft is kept.', { exact: true }),
  ).toBeVisible();
  await expect(counts(page).first()).toHaveValue('77');
  await expect(save(page)).toBeDisabled();
  await page.getByRole('button', { name: 'Read saved plan and status' }).click();
  await page.getByRole('button', { name: 'Use read revision; keep my draft' }).click();
  await expect(save(page)).toBeEnabled();
  await expect(counts(page).first()).toHaveValue('77');
  expect(await bodiesTo(page, '/api/run-controls')).toHaveLength(1);
  expect(await bodiesTo(page, '/api/profile-patch')).toHaveLength(1);
  await screenshot(page, 'runs-race-reconciled', 1054);
  await runs.close();
});
for (const first of ['schedule', 'runs'] as const) {
  test(`S4 shared profile revision race: ${first} saves first; explicit retry preserves both edits`, async ({
    page,
    context,
  }) => {
    const race = await scheduleRace(context, first);
    await open(page);
    await counts(page).first().fill('60');
    const runs = await context.newPage();
    await runs.goto('/ui/app/#runs/saved/bp_farm');
    const saved = runs.locator('[data-region="runsSaved"]');
    const adGems = saved.getByRole('checkbox', { name: 'Ad gems On' });
    await adGems.uncheck();
    const runSave = saved.getByRole('button', { name: 'Save run settings' });
    await (first === 'schedule' ? save(page) : runSave).click();
    await race.started;
    await (first === 'schedule' ? runSave : save(page)).click();
    await expect.poll(() => race.writes.length).toBe(2);
    expect(race.writes.map((write) => write.status)).toEqual([200, 409]);
    expect(own(race.writes[0]?.body, 'expected_revision')).toBe(
      own(race.writes[1]?.body, 'expected_revision'),
    );
    race.releaseFirst();
    if (first === 'schedule') {
      await expect(page.getByText('No unsaved changes', { exact: true })).toBeVisible();
      await expect(saved.getByText(/Not saved/)).toBeVisible();
      await saved.getByRole('button', { name: 'Check saved values' }).click();
      await expect(runSave).toBeEnabled();
      await expect(adGems).not.toBeChecked();
      expect(race.writes).toHaveLength(2);
      await runSave.click();
    } else {
      await expect(saved.getByText('Saved', { exact: true })).toBeVisible();
      await expect(page.getByText(/Schedule save refused/)).toBeVisible();
      await page.getByRole('button', { name: 'Read saved plan and status' }).click();
      await page.getByRole('button', { name: 'Use read revision; keep my draft' }).click();
      await expect(counts(page).first()).toHaveValue('60');
      await expect(save(page)).toBeEnabled();
      expect(race.writes).toHaveLength(2);
      await save(page).click();
    }
    await expect.poll(() => race.writes.length).toBe(3);
    expect(race.writes[2]?.status).toBe(200);
    expect(own(race.writes[2]?.body, 'expected_revision')).not.toBe(
      own(race.writes[0]?.body, 'expected_revision'),
    );
    const profile: unknown = await page.evaluate(async (): Promise<unknown> => {
      const body: unknown = await (await fetch('/api/profile-src/mine')).json();
      return body;
    });
    const run: unknown = await runs.evaluate(async (): Promise<unknown> => {
      const body: unknown = await (await fetch('/api/run-controls?blueprint=bp_farm')).json();
      return body;
    });
    expect(profile).toEqual(race.finalProfile);
    expect(run).toEqual(race.finalRun);
    await expect(counts(page).first()).toHaveValue('60');
    await expect(adGems).not.toBeChecked();
    expect(race.writes).toHaveLength(3);
    await runs.close();
  });
}
test('S6 a fresh observation expires in the browser without a fabricated next start', async ({
  page,
}) => {
  await page.clock.install();
  await open(page);
  await expect(page.getByText('Farm', { exact: true })).toBeVisible();
  await page.clock.fastForward(91000);
  const observation = page.getByRole('region', { name: 'Observed scheduler choice' });
  await expect(observation.getByText(/Last observation is stale/)).toBeVisible();
  await expect(
    observation.getByRole('definition').filter({ hasText: /^No next clock check reported$/ }),
  ).toBeVisible();
  await expect(observation.getByText('Previously observed choice', { exact: false })).toBeVisible();
  expect(await posts(page)).toEqual([]);
});
for (const status of [
  'available_no_winner',
  'scheduler_not_observed',
  'scheduler_not_running',
  'scheduler_observation_stale',
  'unknown_emulator',
]) {
  test(`S6 ${status} exposes a truthful observation state without sending game actions`, async ({
    page,
  }) => {
    await open(page, 390, { status: `schedule_status.${status}` });
    const observation = page.getByRole('region', { name: 'Observed scheduler choice' });
    await expect(observation.getByText('2027-01-16T00:00:00+00:00', { exact: true })).toHaveCount(
      0,
    );
    await expect(
      observation.getByText(
        /No eligible block reported|not reported running|Last observation is stale|unknown to this schedule report/,
      ),
    ).toBeVisible();
    expect(await posts(page)).toEqual([]);
    await screenshot(page, status, 390);
  });
}

for (const width of widths) {
  test(`RL8 F6-11-P2-01 a removal after another client switched the account sends nothing at ${width}`, async ({
    page,
  }) => {
    const plan = {
      profile: 'plan_section_delete.read_before_the_plan_section_was_deleted',
      save: 'plan_section_delete.saved_the_whole_plan_section_deleted',
      readback: 'plan_section_delete.read_after_the_plan_section_was_deleted',
    };
    await open(page, width, plan);
    await page.getByRole('button', { name: 'Remove the day plan' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByText(/The scheduler then uses its legacy rules/u)).toBeVisible();
    // Another client switches the account while the confirmation is open: from now on the
    // config read names account beta and profile theirs (recorded context_fence configuration).
    await page.request.get(
      `/__fake/schedule?${new URLSearchParams({
        ...plan,
        config: 'context_fence.read_config_after_another_client_switched_the_account',
      })}`,
    );
    await dialog.getByRole('button', { name: 'Remove day plan' }).click();
    await expect(
      page.locator('p[role="alert"]', {
        hasText: /^Not sent: the emulator, account or profile changed/u,
      }),
    ).toBeVisible();
    await page.waitForTimeout(1_000);
    expect(await bodiesTo(page, '/api/profile-patch')).toEqual([]);
    expect(await axeCounts(page, WCAG_AA_TAGS)).toEqual([]);
    await screenshot(page, 'remove-not-sent', width);
  });
}
