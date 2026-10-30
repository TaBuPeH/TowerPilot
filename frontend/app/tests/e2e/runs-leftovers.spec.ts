import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { axeCounts, bodiesTo, expectAppStyles, posts, WCAG_AA_TAGS } from './helpers/helpers.ts';
import { dashboardCase, dashboardPagesCase } from '../support/dashboardFixture.ts';

/**
 * F6-11 Runs leftovers in a real browser on the fake backend
 * (backend/logs/reviews/ui-pages/f6-11-runs-leftovers-brief.md): RL1 reset one live
 * override, RL7 battle details and RL8 start a day plan, with axe, reflow, target sizes
 * and screenshots at 1360, 1054 and 390 (TP_F6_SCREENS_DIR, else test-results/f6-screens).
 */
const WIDTHS = [1360, 1054, 390] as const;
const OUT = process.env['TP_F6_SCREENS_DIR'] ?? path.resolve('test-results', 'f6-screens');
mkdirSync(OUT, { recursive: true });

/** No horizontal scroll and every visible control in main at least 24 px (WCAG 2.5.8). */
async function layoutProblems(page: Page): Promise<{ overflow: number; small: string[] }> {
  await expectAppStyles(page);
  return page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    small: [
      ...document.querySelectorAll<HTMLElement>(
        'main button, main input, main select, main summary, main a',
      ),
    ]
      .filter((element) => element.getClientRects().length > 0)
      .filter((element) => {
        const box = element.getBoundingClientRect();
        return box.height < 24 || box.width < 24;
      })
      .map((element) => element.outerHTML.slice(0, 80)),
  }));
}

async function openRunsLive(page: Page, scenario: string, width: number) {
  const problems: string[] = [];
  page.on('pageerror', (error) => {
    problems.push(error.message);
  });
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`/__fake/use?scenario=${scenario}`);
  await page.evaluate(() => {
    window.location.hash = '#runs/live';
  });
  await expect(page.getByRole('heading', { level: 1, name: 'Runs' })).toBeVisible();
  return problems;
}

const live = (page: Page) => page.locator('[data-region="runsLive"]');

for (const width of WIDTHS) {
  for (const outcome of ['applied', 'stale']) {
    test(`RL2 mixed reset ${outcome} at ${String(width)}: full list, exactly one POST, read settlement, axe and targets`, async ({
      page,
    }) => {
      const mixed = (name: string) =>
        dashboardPagesCase(`run_controls_live_reset_all_mixed.${name}`);
      let source = mixed(
        outcome === 'stale'
          ? 'read_after_the_refusal_both_kinds_of_override_remain'
          : 'read_before_the_reset_the_mixed_overrides_are_in_force',
      );
      const answer = mixed(
        outcome === 'stale'
          ? 'mixed_reset_with_a_stale_control_revision_refused'
          : 'accepted_null_for_shopping_plan_and_routine_overrides',
      );
      await page.route('**/api/battles/active', (route) =>
        route.fulfill({ status: source.status, json: source.body }),
      );
      await page.route('**/api/operations/*/live-controls', async (route) => {
        await route.fetch();
        await route.fulfill({ status: answer.status, json: answer.body });
      });
      const problems = await openRunsLive(page, 'runs_live_reset_all', width);
      const all = live(page).getByRole('button', {
        name: "Use the battle's starting values for all 3",
      });
      await expect(all).not.toHaveAttribute('aria-disabled', 'true');
      const items = live(page).locator('[data-reset-all-items]');
      await expect(items.getByRole('listitem')).toHaveCount(3);
      await expect(items.getByText('Shopping plan', { exact: true })).toBeVisible();
      if (outcome === 'stale')
        await live(page).getByRole('textbox', { name: 'Shopping Every', exact: true }).fill('77');
      await all.click();
      await expect(page.getByRole('dialog').getByRole('listitem')).toHaveCount(3);
      await page.getByRole('dialog').getByRole('button', { name: 'Cancel' }).click();
      expect(await posts(page)).toEqual([]);
      await all.click();
      await page
        .getByRole('dialog')
        .getByRole('button', { name: 'Use starting values', exact: true })
        .click();
      if (outcome === 'stale') {
        // Race replay: the genuine 409 used revision 1; the displayed source is already at 2.
        // The client must still send the current read's revision and preserve the complete draft/list.
        await expect(live(page).getByText('Not sent - nothing was recorded')).toBeVisible();
        await expect(items.getByRole('listitem')).toHaveCount(3);
        await expect(
          live(page).getByRole('textbox', { name: 'Shopping Every', exact: true }),
        ).toHaveValue('77');
        await expect(live(page).locator('[data-outcome]')).toContainText(
          'Control revision changed',
        );
      } else {
        await expect(live(page).getByText('Sent to this battle - waiting to apply')).toBeVisible();
        source = mixed('read_after_the_reset_was_accepted_before_it_applies');
        await live(page).getByRole('button', { name: 'Check this battle' }).click();
        await expect(live(page).getByText('Sent to this battle - waiting to apply')).toBeVisible();
        await expect(live(page).getByText('Applied to this battle', { exact: true })).toHaveCount(
          0,
        );
        source = mixed('read_after_the_mixed_reset_applied');
        await live(page).getByRole('button', { name: 'Check this battle' }).click();
        await expect(live(page).getByText('Applied to this battle', { exact: true })).toBeVisible();
        await expect(items).toHaveCount(0);
        await expect(live(page).locator('[data-path]')).toHaveCount(0);
      }
      expect(await bodiesTo(page, answer.request.path)).toEqual([
        {
          ...Object.fromEntries(Object.entries(answer.request.body as Record<string, unknown>)),
          expected_revision: 2,
          idempotency_key: expect.any(String),
        },
      ]);
      expect(await axeCounts(page, WCAG_AA_TAGS)).toEqual([]);
      expect(await layoutProblems(page)).toEqual({ overflow: 0, small: [] });
      if (width === 390)
        for (const button of await live(page).locator('.tp-run-resets button:visible').all())
          expect((await button.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
      await live(page).screenshot({
        path: path.join(OUT, `runs-leftovers-mixed-reset-${outcome}-${String(width)}.png`),
      });
      expect(problems).toEqual([]);
    });
  }

  test(`RL7 battle details at ${String(width)}: read-only, axe, reflow, targets and screenshot`, async ({
    page,
  }) => {
    const problems = await openRunsLive(page, 'active_battle_routines', width);
    const summary = live(page).getByText('Ad gems: battle details', { exact: true });
    await expect(summary).toBeVisible();
    await summary.click();
    const details = live(page).locator('[data-routine-details="ad_gems"]');
    await expect(details.getByText('Not seen yet', { exact: true })).toBeVisible();
    await expect(details.getByText(/^Window closes in \d+ s$/u)).toBeVisible();
    await expect(
      details.getByRole('link', { name: 'Events of this battle on Runtime' }),
    ).toHaveAttribute(
      'href',
      /^#runtime\?battle_id=.+&operation_id=.+&offer_id=battles_runtime-offer$/u,
    );
    await details.getByText('Reported values', { exact: true }).click();
    await expect(details.locator('[data-server-text="detail"]')).toContainText('"offer_id"');
    await live(page).getByText('Floating gems: battle details', { exact: true }).click();
    await expect(
      live(page).locator('[data-routine-details="floating_gems"]').getByText('None recorded'),
    ).toBeVisible();
    expect(await axeCounts(page, WCAG_AA_TAGS)).toEqual([]);
    expect(await layoutProblems(page)).toEqual({ overflow: 0, small: [] });
    await live(page).screenshot({
      path: path.join(OUT, `runs-leftovers-details-${String(width)}.png`),
    });
    // The Runtime link opens the Runtime page filtered to this battle; nothing is sent.
    await details.getByRole('link', { name: 'Events of this battle on Runtime' }).click();
    await expect(page).toHaveURL(/#runtime\?battle_id=/u);
    await expect(page.getByRole('heading', { level: 1, name: 'Runtime' })).toBeVisible();
    expect(await posts(page)).toEqual([]);
    expect(problems).toEqual([]);
  });

  test(`RL7 the recorded seen offer and Floating gems refusal at ${String(width)}: dates, failure, counts, axe and screenshot`, async ({
    page,
  }) => {
    const problems = await openRunsLive(page, 'runs_live_battle_details', width);
    await live(page).getByText('Ad gems: battle details', { exact: true }).click();
    const ad = live(page).locator('[data-routine-details="ad_gems"]');
    await expect(ad.getByText('Seen on the last battle screen', { exact: true })).toBeVisible();
    await expect(ad.getByText(/; 5 s between looks$/u)).toBeVisible();
    await expect(ad.getByText('offer_still_visible', { exact: true })).toBeVisible();
    for (const [label, value] of [
      ['Attempts', '1'],
      ['Confirmed', '0'],
      ['Skipped', '0'],
      ['Windows missed', '0'],
    ] as const) {
      const fact = ad
        .locator('.tp-facts__item')
        .filter({ has: page.getByText(label, { exact: true }) });
      await expect(fact.locator('dd')).toHaveText(value);
      await expect(fact).toBeVisible();
    }
    await expect(
      ad.getByRole('link', { name: 'Events of this battle on Runtime' }),
    ).toHaveAttribute('href', /offer_id=bd_ad_offer_failed-offer&since=1799999931$/u);
    expect(await axeCounts(page, WCAG_AA_TAGS)).toEqual([]);
    expect(await layoutProblems(page)).toEqual({ overflow: 0, small: [] });
    await live(page).screenshot({
      path: path.join(OUT, `runs-leftovers-details-seen-${String(width)}.png`),
    });
    await page.goto('/__fake/use?scenario=runs_live_floating_refusal');
    await page.evaluate(() => {
      window.location.hash = '#runs/live';
    });
    await live(page).getByText('Floating gems: battle details', { exact: true }).click();
    await expect(
      live(page)
        .locator('[data-routine-details="floating_gems"]')
        .getByText('invalid_orbit_arc', { exact: true }),
    ).toBeVisible();
    expect(await posts(page)).toEqual([]);
    expect(problems).toEqual([]);
  });

  test(`RL1 reset one live override at ${String(width)}: Cancel sends nothing, a confirmed reset sends null once`, async ({
    page,
  }) => {
    const problems = await openRunsLive(page, 'runs_live_applied', width);
    const resets = live(page).getByRole('button', { name: "Use the battle's starting value" });
    await expect(resets.first()).toBeVisible();
    const path0 = await live(page).locator('[data-path]').first().getAttribute('data-path');
    if (width === 390) {
      // Review F6-11-R1-05: at 390 each reset is a full-width button under its row's name.
      const row = live(page).locator('[data-path]').first();
      const rowBox = await row.boundingBox();
      const nameBox = await row.locator('.tp-run-reset__name').boundingBox();
      const buttonBox = await row
        .getByRole('button', { name: "Use the battle's starting value" })
        .boundingBox();
      expect(buttonBox?.y ?? 0).toBeGreaterThanOrEqual(
        (nameBox?.y ?? 0) + (nameBox?.height ?? 0) - 1,
      );
      expect(Math.abs((buttonBox?.width ?? 0) - (rowBox?.width ?? 0))).toBeLessThanOrEqual(1);
      expect(buttonBox?.height ?? 0).toBeGreaterThanOrEqual(44);
    }
    await resets.first().click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    expect(await axeCounts(page, WCAG_AA_TAGS)).toEqual([]);
    await page.screenshot({
      path: path.join(OUT, `runs-leftovers-reset-${String(width)}.png`),
      fullPage: false,
    });
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toHaveCount(0);
    expect(await posts(page)).toEqual([]);
    await resets.first().click();
    await page.getByRole('dialog').getByRole('button', { name: 'Use starting value' }).dblclick();
    await expect
      .poll(async () => (await posts(page)).filter((p) => p.path.endsWith('/live-controls')).length)
      .toBe(1);
    const [sent] = (await posts(page)).filter((p) => p.path.endsWith('/live-controls'));
    const body = JSON.parse(sent?.body ?? '{}') as { changes?: Record<string, unknown> };
    expect(body.changes).toEqual({ [path0 ?? '']: null });
    await page.waitForTimeout(2_000);
    expect((await posts(page)).filter((p) => p.path.endsWith('/live-controls'))).toHaveLength(1);
    expect(await layoutProblems(page)).toEqual({ overflow: 0, small: [] });
    expect(problems).toEqual([]);
  });

  test(`RL2 reset every override at ${String(width)}: the confirmation lists both, one POST with both paths null, then none left`, async ({
    page,
  }) => {
    const problems = await openRunsLive(page, 'runs_live_reset_all', width);
    const all = live(page).getByRole('button', {
      name: "Use the battle's starting values for all 2",
    });
    await expect(all).toBeVisible();
    if (width === 390) expect((await all.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
    await all.click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByRole('listitem')).toHaveCount(2);
    expect(await axeCounts(page, WCAG_AA_TAGS)).toEqual([]);
    await page.screenshot({
      path: path.join(OUT, `runs-leftovers-reset-all-${String(width)}.png`),
    });
    await dialog.getByRole('button', { name: 'Use starting values' }).click();
    await expect
      .poll(async () => (await posts(page)).filter((p) => p.path.endsWith('/live-controls')).length)
      .toBe(1);
    const [sent] = (await posts(page)).filter((p) => p.path.endsWith('/live-controls'));
    expect((JSON.parse(sent?.body ?? '{}') as { changes?: unknown }).changes).toEqual({
      'routines.ad_gems.interval_sec': null,
      'routines.floating_gems.enabled': null,
    });
    await live(page).getByRole('button', { name: 'Check this battle' }).click();
    await expect(live(page).getByText('Applied to this battle', { exact: true })).toBeVisible();
    await expect(live(page).locator('[data-path]')).toHaveCount(0);
    expect(await layoutProblems(page)).toEqual({ overflow: 0, small: [] });
    await page.waitForTimeout(2_000);
    expect((await posts(page)).filter((p) => p.path.endsWith('/live-controls'))).toHaveLength(1);
    expect(problems).toEqual([]);
  });

  test(`IR-01 a recorded live change reads "Applied to this battle" once its battle's read lists it applied, at ${String(width)}`, async ({
    page,
  }) => {
    const problems = await openRunsLive(page, 'runs_live_applied_after_change', width);
    await live(page).getByRole('checkbox', { name: 'Ad gems On' }).uncheck();
    await live(page).getByRole('button', { name: 'Save changes to this battle' }).click();
    await expect
      .poll(async () => (await posts(page)).filter((p) => p.path.endsWith('/live-controls')).length)
      .toBe(1);
    // The recorded answer (200) is followed by the recorded read listing the command applied.
    await live(page).getByRole('button', { name: 'Check this battle' }).click();
    await expect(live(page).getByText('Applied to this battle', { exact: true })).toBeVisible();
    await expect(live(page).getByText('Sent to this battle - waiting to apply')).toHaveCount(0);
    await expect(live(page).getByRole('checkbox', { name: 'Ad gems On' })).not.toBeChecked();
    expect(await axeCounts(page, WCAG_AA_TAGS)).toEqual([]);
    expect(await layoutProblems(page)).toEqual({ overflow: 0, small: [] });
    await live(page).screenshot({
      path: path.join(OUT, `runs-leftovers-applied-${String(width)}.png`),
    });
    await page.waitForTimeout(2_000);
    expect((await posts(page)).filter((p) => p.path.endsWith('/live-controls'))).toHaveLength(1);
    expect(problems).toEqual([]);
  });

  test(`RL8 remove the day plan at ${String(width)}: asks first, one delete with the read revision, then a new start is offered`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.request.get('/__fake/use?scenario=runs_saved');
    await page.request.get(
      `/__fake/schedule?${new URLSearchParams({
        profile: 'plan_section_delete.read_before_the_plan_section_was_deleted',
        save: 'plan_section_delete.saved_the_whole_plan_section_deleted',
        readback: 'plan_section_delete.read_after_the_plan_section_was_deleted',
      })}`,
    );
    await page.goto(`/ui/app/?rl8-remove=${String(width)}#schedule`);
    const remove = page.getByRole('button', { name: 'Remove the day plan' });
    await expect(remove).toBeVisible();
    if (width === 390) expect((await remove.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
    await remove.click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByText(/The scheduler then uses its legacy rules/u)).toBeVisible();
    expect(await axeCounts(page, WCAG_AA_TAGS)).toEqual([]);
    await page.screenshot({
      path: path.join(OUT, `runs-leftovers-schedule-remove-${String(width)}.png`),
    });
    await dialog.getByRole('button', { name: 'Keep editing' }).click();
    expect(await bodiesTo(page, '/api/profile-patch')).toEqual([]);
    await remove.click();
    await page.getByRole('dialog').getByRole('button', { name: 'Remove day plan' }).dblclick();
    await expect.poll(async () => (await bodiesTo(page, '/api/profile-patch')).length).toBe(1);
    expect(await bodiesTo(page, '/api/profile-patch')).toEqual([
      {
        name: 'mine',
        // Modified recorded plan request: the legacy journey now carries its runtime fence.
        expected_context: { instance: 'main', serial: 'fake:1', account: null, profile: 'mine' },
        expected_revision: '000000000000000000000000000000000000000000000000000000000000003c',
        path: ['plan'],
        op: 'delete',
      },
    ]);
    await expect(
      page
        .getByRole('status', { name: 'Profile validation and save details' })
        .getByText(/^Day plan removed\./u),
    ).toBeVisible();
    await expect(page.getByRole('button', { name: 'Start a day plan', exact: true })).toBeVisible();
    expect(await layoutProblems(page)).toEqual({ overflow: 0, small: [] });
    await page.waitForTimeout(2_000);
    expect(await bodiesTo(page, '/api/profile-patch')).toHaveLength(1);
  });

  test(`RL8 start a day plan at ${String(width)}: drafting sends nothing, Save sends the whole plan once`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.request.get('/__fake/use?scenario=runs_saved');
    await page.request.get(
      `/__fake/schedule?${new URLSearchParams({
        profile: 'schedule_plan.profile_without_a_plan_has_no_plan_member',
        save: 'profile_patch.a_plan_added_to_a_profile_that_has_none',
      })}`,
    );
    await page.goto(`/ui/app/?rl8=${String(width)}#schedule`);
    await expect(
      page.getByRole('heading', { level: 1, name: 'Schedule', exact: true }),
    ).toBeVisible();
    const start = page.getByRole('button', { name: 'Start a day plan', exact: true });
    await expect(start).toBeVisible();
    expect(await axeCounts(page, WCAG_AA_TAGS)).toEqual([]);
    expect(await layoutProblems(page)).toEqual({ overflow: 0, small: [] });
    if (width === 390) expect((await start.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
    await page.screenshot({
      path: path.join(OUT, `runs-leftovers-schedule-start-${String(width)}.png`),
      fullPage: true,
    });
    await page.getByRole('combobox', { name: 'Run for the first block' }).selectOption('farm');
    await start.click();
    const kind = page.getByRole('textbox', { name: 'Block kind' });
    await expect(kind).toBeFocused();
    expect(await bodiesTo(page, '/api/profile-patch')).toEqual([]);
    await kind.fill('coin');
    await page.getByRole('button', { name: 'Save schedule', exact: true }).dblclick();
    await expect.poll(async () => (await bodiesTo(page, '/api/profile-patch')).length).toBe(1);
    expect(await bodiesTo(page, '/api/profile-patch')).toEqual([
      {
        name: 'mine',
        // Modified recorded plan request: the legacy journey now carries its runtime fence.
        expected_context: { instance: 'main', serial: 'fake:1', account: null, profile: 'mine' },
        expected_revision: '000000000000000000000000000000000000000000000000000000000000004e',
        path: ['plan'],
        value: {
          days: { every_day: [{ block: 'coin', blueprint: 'farm' }] },
          week: { default: 'every_day' },
        },
      },
    ]);
    await page.waitForTimeout(2_000);
    expect(await bodiesTo(page, '/api/profile-patch')).toHaveLength(1);
  });
}

/**
 * A2-7 AC2: the live editor from the landed r17b projection reads (descriptor labels, units,
 * bounds; the stale and test-local read-only cases), with axe, reflow, targets and screenshots
 * a2-7-runs-<state>-<width>.png.
 */
const projection = (name: string) => dashboardPagesCase(`battle_actions_projection.${name}`);
const LIVE_WRITE = dashboardCase('battle_actions.live_controls_accepted_command_requested');

/** The duration notes that wrap onto a second line (root note: none at 390). */
async function wrappedDurations(page: Page): Promise<string[]> {
  return live(page)
    .locator('.tp-run-cell__duration')
    .evaluateAll((notes) =>
      notes
        .filter((note) => {
          const size = Number.parseFloat(getComputedStyle(note).fontSize);
          return note.getBoundingClientRect().height >= size * 2;
        })
        .map((note) => note.textContent),
    );
}

for (const width of WIDTHS) {
  test(`AC2 the live editor from the descriptors at ${String(width)}: labels, units, bounds, one exact POST, axe and screenshot`, async ({
    page,
  }) => {
    const read = projection('values_carry_every_field_in_force');
    await page.route('**/api/battles/active', (route) =>
      route.fulfill({ status: read.status, json: read.body }),
    );
    await page.route('**/api/operations/*/live-controls', async (route) => {
      // The fake backend records the request; the answer is the recorded receipt.
      await route.fetch();
      await route.fulfill({ status: LIVE_WRITE.status, json: LIVE_WRITE.body });
    });
    const problems = await openRunsLive(page, 'runs_live_reset_all', width);
    const tap = live(page).getByRole('textbox', {
      name: 'Floating gems Time between tap sets while seeking',
      exact: true,
    });
    await expect(tap).toHaveValue('5');
    await expect(
      live(page).getByText('Retry spacing after unconfirmed taps', { exact: true }),
    ).toBeVisible();
    await expect(live(page).locator('.tp-run-columns')).toHaveCount(0);
    await expect(live(page).locator('.tp-run-cell__duration')).not.toHaveCount(0);
    expect(await wrappedDurations(page)).toEqual([]);
    expect(await axeCounts(page, WCAG_AA_TAGS)).toEqual([]);
    expect(await layoutProblems(page)).toEqual({ overflow: 0, small: [] });
    if (width === 390)
      for (const button of await live(page).locator('.tp-run-editor__actions button:visible').all())
        expect((await button.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
    await live(page).screenshot({
      path: path.join(OUT, `a2-7-runs-descriptor-${String(width)}.png`),
    });
    // A value below the descriptor's 0.5 stays typed with its error and is never sent.
    await tap.fill('0.4');
    await expect(live(page).getByText('Below the allowed range', { exact: true })).toBeVisible();
    await expect(
      live(page).getByRole('button', { name: 'Save changes to this battle' }),
    ).toHaveAttribute('aria-disabled', 'true');
    await live(page).screenshot({
      path: path.join(OUT, `a2-7-runs-out-of-bounds-${String(width)}.png`),
    });
    expect(await posts(page)).toEqual([]);
    await tap.fill('5');
    await live(page).getByRole('checkbox', { name: 'Floating gems Enabled', exact: true }).check();
    await live(page).getByRole('button', { name: 'Save changes to this battle' }).click();
    await expect
      .poll(async () => (await posts(page)).filter((p) => p.path.endsWith('/live-controls')).length)
      .toBe(1);
    const [sent] = (await posts(page)).filter((p) => p.path.endsWith('/live-controls'));
    expect((JSON.parse(sent?.body ?? '{}') as { changes?: unknown }).changes).toEqual(
      (LIVE_WRITE.request.body as { changes: unknown }).changes,
    );
    await page.waitForTimeout(2_000);
    expect((await posts(page)).filter((p) => p.path.endsWith('/live-controls'))).toHaveLength(1);
    expect(problems).toEqual([]);
  });

  test(`AC2 old action readings at ${String(width)}: Save and resets closed with the visible reason, nothing sent`, async ({
    page,
  }) => {
    const read = projection('projection_fails_later_the_last_projection_stays_and_its_stamps_stop');
    await page.route('**/api/battles/active', (route) =>
      route.fulfill({ status: read.status, json: read.body }),
    );
    const contextRead = page.waitForResponse(
      (response) => new URL(response.url()).pathname === '/api/config',
    );
    const problems = await openRunsLive(page, 'runs_live_reset_all', width);
    expect((await contextRead).status()).toBe(200);
    // The first config response may follow a transport retry. Wait for the app to
    // publish its confirmed context, then a battle request STARTED in that context.
    // A response from a provisional request (or document networkidle) cannot prove this.
    await expect
      .poll(() => page.evaluate(() => sessionStorage.getItem('tp.context.v1')))
      .not.toBeNull();
    const battleRequest = await page.waitForRequest(
      (request) =>
        request.method() === 'GET' && new URL(request.url()).pathname === '/api/battles/active',
    );
    const battleResponse = await battleRequest.response();
    expect(battleResponse?.status()).toBe(200);
    await battleResponse?.finished();
    await expect(
      live(page)
        .getByRole('button', { name: /starting value/u })
        .first(),
    ).toBeVisible();
    const interval = live(page).getByRole('textbox', { name: 'Ad gems Interval', exact: true });
    await interval.fill('600');
    const save = live(page).getByRole('button', { name: 'Save changes to this battle' });
    await expect(save).toHaveAttribute('aria-disabled', 'true');
    await expect(
      live(page)
        .getByText('The action readings are old; Run now and changes wait for a current reading')
        .first(),
    ).toBeVisible();
    const resets = await live(page)
      .getByRole('button', { name: /starting value/u })
      .all();
    expect(resets.length).toBeGreaterThan(0);
    for (const reset of resets) await expect(reset).toHaveAttribute('aria-disabled', 'true');
    expect(await axeCounts(page, WCAG_AA_TAGS)).toEqual([]);
    expect(await layoutProblems(page)).toEqual({ overflow: 0, small: [] });
    await live(page).screenshot({ path: path.join(OUT, `a2-7-runs-stale-${String(width)}.png`) });
    await page.waitForTimeout(2_000);
    expect(await posts(page)).toEqual([]);
    expect(problems).toEqual([]);
  });

  test(`AC2 test-local read-only parts at ${String(width)}: an unaddressable item and an unknown field type, axe and screenshot`, async ({
    page,
  }) => {
    // Test-local: the recorded read with a second ad gems instance that names no key, and the
    // floating gems window described with a type this page does not know.
    const read = projection('values_carry_every_field_in_force');
    const items = (
      read.body as { battle: { runtime: { actions: { items: Record<string, unknown>[] } } } }
    ).battle.runtime.actions.items;
    const other = structuredClone(items[0] ?? {});
    delete other['key'];
    other['instance'] = 'ad_gems_two';
    items.push(other);
    const floating = items.find((item) => item['key'] === 'floating_gems') as {
      descriptor: { fields: Record<string, unknown>[] };
    };
    const window = floating.descriptor.fields.find((field) => field['path'] === 'window_sec');
    if (window !== undefined) window['type'] = 'duration';
    await page.route('**/api/battles/active', (route) =>
      route.fulfill({ status: read.status, json: read.body }),
    );
    const problems = await openRunsLive(page, 'runs_live_reset_all', width);
    await expect(live(page).locator('[data-unaddressable]')).toContainText(
      "Its settings can't be changed here",
    );
    await expect(live(page).locator('[data-unrecognised]')).toContainText('Not recognised');
    expect(await axeCounts(page, WCAG_AA_TAGS)).toEqual([]);
    expect(await layoutProblems(page)).toEqual({ overflow: 0, small: [] });
    await live(page).screenshot({
      path: path.join(OUT, `a2-7-runs-read-only-${String(width)}.png`),
    });
    expect(await posts(page)).toEqual([]);
    expect(problems).toEqual([]);
  });
}
