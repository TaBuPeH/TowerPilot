import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { own, ownPath } from '../../src/core/read.ts';
import { fencedCase } from '../support/profilePatchFenced.ts';
import {
  FENCED_WRITES,
  profilePatchFencedRead,
} from '../support/fake-backend/profile-patch-fenced-fixtures.mjs';
import { axeIds, bodiesTo, posts, WCAG_AA_TAGS } from './helpers/helpers.ts';

const OUT = process.env['TP_F6_SCREENS_DIR'] ?? path.resolve('test-results/f6-screens');
mkdirSync(OUT, { recursive: true });
const PATCH = '/api/profile-patch';
const REFUSAL = 'active_patch_refused_after_account_switch';

function pageName(name: string) {
  if (name.includes('plan_')) return 'Schedule';
  if (name.includes('_uw_')) return 'Abilities UW';
  if (name.includes('_rescue_')) return 'Abilities rescue';
  if (name.includes('_run_entry_')) return 'Runs entry';
  return 'Collection';
}
function hash(name: string) {
  const page = pageName(name);
  if (page === 'Schedule') return '#schedule';
  if (page.startsWith('Abilities')) return '#abilities';
  if (page === 'Runs entry') return '#runs/saved/farm';
  return `#collection/policy/${name === 'active_unused_gather_deleted' ? 'spare' : 'all_on'}`;
}
function screenshotPage(name: string) {
  const base = pageName(name).toLowerCase().replaceAll(' ', '-');
  if (name.endsWith('deleted')) return `${base}-delete`;
  if (name === 'active_gather_copy_saved') return `${base}-copy`;
  if (name === 'active_gather_template_created') return `${base}-template`;
  if (name === 'active_chores_saved') return `${base}-chores`;
  return base;
}
async function send(page: Page, name: string) {
  if (name === 'active_plan_saved') {
    await page.getByRole('textbox', { name: 'Runs/day (blank = unlimited)' }).fill('');
    await page.getByRole('button', { name: 'Save schedule', exact: true }).click();
  } else if (name === 'active_plan_deleted') {
    await page.getByRole('button', { name: 'Remove the day plan', exact: true }).click();
    await page
      .getByRole('dialog')
      .getByRole('button', { name: 'Remove day plan', exact: true })
      .click();
  } else if (name === 'active_gather_label_saved') {
    await page.getByRole('textbox', { name: 'Name shown', exact: true }).fill('Offline');
    await page.getByRole('button', { name: 'Save name', exact: true }).click();
  } else if (name === 'active_gather_copy_saved') {
    await page.getByRole('textbox', { name: 'New identifier', exact: true }).fill('spare');
    await page.getByRole('button', { name: 'Save copy', exact: true }).click();
  } else if (name === 'active_gather_template_created') {
    await page
      .getByRole('combobox', { name: 'Template gathering default' })
      .selectOption(JSON.stringify(['farm', 'all_on']));
    await page.getByRole('textbox', { name: 'New policy identifier' }).fill('fresh');
    await page.getByRole('button', { name: 'Save policy', exact: true }).click();
  } else if (name === 'active_unused_gather_deleted') {
    await page.getByRole('button', { name: 'Delete this policy', exact: true }).click();
    await page
      .getByRole('group', { name: 'Delete this policy', exact: true })
      .getByRole('button', { name: 'Delete', exact: true })
      .click();
  } else if (name === 'active_chores_saved') {
    await page.getByRole('checkbox', { name: 'Quest scan', exact: true }).uncheck();
    await page.getByRole('button', { name: 'Save chores', exact: true }).click();
  } else if (name.includes('_run_entry_')) {
    const field = name.endsWith('label_saved') ? 'label' : 'perk_bans';
    const row = page.locator(`[data-entry-field="${field}"]`);
    if (field === 'label') await row.getByLabel('Run label', { exact: true }).fill('Offline farm');
    else await row.getByLabel('Leave the game’s perk bans alone', { exact: true }).check();
    await row.getByRole('button', { name: 'Save field', exact: true }).click();
  } else {
    const uw = name.includes('_uw_');
    await page
      .getByRole('combobox', { name: 'Saved plans', exact: true })
      .selectOption(uw ? 'uw_policies' : 'rescue_policies');
    if (name.endsWith('deleted')) {
      await page.getByRole('button', { name: uw ? 'Calm' : 'extra', exact: true }).click();
      await page.getByRole('button', { name: 'Delete unused plan', exact: true }).click();
      await page
        .getByRole('dialog')
        .getByRole('button', { name: 'Delete unused plan', exact: true })
        .click();
    } else {
      await page
        .getByRole('button', { name: uw ? 'Farm choreography' : 'any_falling', exact: true })
        .click();
      await page
        .getByRole('textbox', { name: 'New plan ID', exact: true })
        .fill(uw ? 'calm' : 'extra');
      await page.getByRole('button', { name: 'Copy selected to draft', exact: true }).click();
      if (uw) {
        await page.getByRole('textbox', { name: 'Plan label', exact: true }).fill('Calm');
        await page.getByRole('combobox', { name: 'spotlight', exact: true }).selectOption('false');
        await page
          .getByRole('combobox', { name: 'Chain Lightning mode', exact: true })
          .selectOption('absent');
        await page
          .getByRole('checkbox', {
            name: 'I reviewed the mode or rule change and its removed fields',
          })
          .check();
      }
      await page.getByRole('button', { name: 'Save plan', exact: true }).click();
    }
  }
}
async function check(page: Page, width: number) {
  const violations = await axeIds(page, WCAG_AA_TAGS);
  expect(violations).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBe(0);
  expect(
    await page.locator('main').evaluate(
      (root, mobile) =>
        [...root.querySelectorAll<HTMLElement>('button,input,select,textarea,summary')]
          .filter((element) => element.getClientRects().length > 0)
          .filter((element) => {
            const r = element.getBoundingClientRect();
            return r.width < 24 || r.height < (mobile && element.tagName === 'BUTTON' ? 44 : 24);
          })
          .map((element) => element.outerHTML),
      width === 390,
    ),
  ).toEqual([]);
}
async function draft(page: Page) {
  return page.locator('main input, main select, main textarea').evaluateAll((fields) =>
    fields.map((field) => ({
      id: field.id,
      value: (field as HTMLInputElement).value,
      checked: (field as HTMLInputElement).checked,
    })),
  );
}
async function read(page: Page, name: string) {
  const label =
    pageName(name) === 'Schedule'
      ? 'Read saved plan and status'
      : pageName(name).startsWith('Abilities')
        ? 'Read saved result'
        : pageName(name) === 'Runs entry'
          ? 'Read entry settings'
          : 'Read the saved settings again';
  await page.getByRole('button', { name: label, exact: true }).click();
}
async function completedRead(page: Page, name: string) {
  const surface = pageName(name);
  if (surface === 'Schedule') {
    await expect(page.getByRole('button', { name: 'Read saved plan and status' })).toBeEnabled();
    await expect(page.getByText('Reading saved plan…', { exact: true })).toHaveCount(0);
    await expect(
      page.getByText('Saved plan could not be read for this target. The draft is kept.', {
        exact: true,
      }),
    ).toHaveCount(0);
  } else if (surface.startsWith('Abilities')) {
    // refresh() performs a second config read AFTER profile/settings/vocabulary. Its
    // busy state ends only after that last context check and editor reconciliation.
    await expect(
      page.getByRole('button', { name: 'Read saved result', exact: true }),
    ).toBeEnabled();
    await expect(page.locator('#abilities-read-reason')).toHaveCount(0);
    await expect(
      page.getByText(
        'The target or its saved data could not be read. Drafts are kept; saving is blocked.',
        { exact: true },
      ),
    ).toHaveCount(0);
  } else {
    // These poller-backed pages keep the last answered read visible during refresh
    // and expose no busy control. Drain the finite read batch before checking the
    // source-backed values below; the fixture's idle periodic poll is 2.5 seconds.
    await page.waitForLoadState('networkidle');
    if (surface === 'Collection') {
      await expect(page.getByText('Reading the saved settings', { exact: true })).toHaveCount(0);
      await expect(page.getByText("The saved settings can't be read", { exact: true })).toHaveCount(
        0,
      );
      await expect(
        page.getByText('The last settings read was for another emulator, account or profile', {
          exact: true,
        }),
      ).toHaveCount(0);
    } else {
      await expect(
        page.getByText('Read this profile and its field vocabulary to edit entry settings.', {
          exact: true,
        }),
      ).toHaveCount(0);
      await expect(page.locator('[data-entry-field="label"]')).toBeVisible();
    }
  }
}
async function savedReadback(page: Page, name: string) {
  const source = fencedCase(`read_after_${name}`).body;
  const surface = pageName(name);
  if (surface === 'Schedule') {
    await expect(page.getByRole('button', { name: 'spare_day', exact: true })).toHaveCount(
      name.endsWith('deleted') ? 0 : 1,
    );
    if (name.endsWith('deleted')) {
      await expect(
        page.getByRole('button', { name: 'Start a day plan', exact: true }),
      ).toBeVisible();
    } else {
      await page.getByText('Compare the last read saved plan', { exact: true }).click();
      await expect(
        page.getByText(String(own(source, 'revision')), { exact: true }).first(),
      ).toBeVisible();
      const rendered = await page.locator('.tp-schedule__compare pre').textContent();
      expect(JSON.parse(rendered ?? '')).toEqual(ownPath(source, 'profile', 'plan'));
    }
  } else if (surface.startsWith('Abilities')) {
    const uw = name.includes('_uw_');
    const id = uw ? 'calm' : 'extra';
    const button = page.getByRole('button', { name: uw ? 'Calm' : 'extra', exact: true });
    if (name.endsWith('deleted')) await expect(button).toHaveCount(0);
    else {
      await expect(button).toBeVisible();
      await expect(page.getByText('Saved configuration', { exact: true })).toBeVisible();
      // The comparison panel reads the saved source, independently of the draft node.
      await page.getByText('Review saved and proposed values', { exact: true }).click();
      const latest = page
        .getByRole('heading', { name: 'Latest saved value', exact: true })
        .locator('..')
        .locator('pre');
      expect(JSON.parse((await latest.textContent()) ?? '')).toEqual(
        ownPath(source, 'profile', 'policies', uw ? 'uw_policies' : 'rescue_policies', id),
      );
    }
  } else if (surface === 'Collection') {
    const policies = page.locator('[data-region="collectionPolicies"]');
    if (name === 'active_unused_gather_deleted')
      await expect(policies.locator('a[href="#collection/policy/spare"]')).toHaveCount(0);
    else if (name === 'active_gather_copy_saved' || name === 'active_gather_template_created')
      await expect(
        policies.locator(
          `a[href="#collection/policy/${name.includes('copy') ? 'spare' : 'fresh'}"]`,
        ),
      ).toBeVisible();
    else if (name === 'active_gather_label_saved')
      await expect(policies.locator('a[href="#collection/policy/all_on"]')).toHaveText('Offline');
    else
      await expect(
        page.getByRole('checkbox', { name: 'Quest scan', exact: true }),
      ).not.toBeChecked();
    await expect(page.locator('[data-outcome="saved"]').first()).toBeVisible();
  } else {
    const row = page.locator(
      `[data-entry-field="${name.endsWith('deleted') ? 'perk_bans' : 'label'}"]`,
    );
    if (name.endsWith('deleted')) {
      await expect(row.getByRole('textbox')).toHaveValue('');
      await expect(row.getByRole('checkbox')).not.toBeChecked();
    } else {
      await expect(row.getByRole('textbox')).toHaveValue('Offline farm');
      await expect(row.getByText('No change to save.', { exact: true })).toBeVisible();
    }
    await expect(row.locator('[data-outcome="saved"]')).toBeVisible();
  }
}
for (const width of [1360, 1054, 390]) {
  for (const name of FENCED_WRITES) {
    for (const refused of [false, true]) {
      test(`F6-14b C-02 ${pageName(name)} ${name} ${refused ? 'refusal' : 'save and read-after'} at ${width}`, async ({
        page,
      }) => {
        const problems: string[] = [];
        page.on('pageerror', (error) => problems.push(error.message));
        await page.setViewportSize({ width, height: 900 });
        await page.clock.install();
        await page.goto(
          `/__fake/use?scenario=profile_patch_fenced_${name}${refused ? '_refused' : ''}`,
        );
        await page.evaluate((target) => {
          location.hash = target;
        }, hash(name));
        await expect(
          page.getByRole('heading', {
            level: 1,
            name: pageName(name).startsWith('Abilities')
              ? 'Abilities'
              : pageName(name) === 'Runs entry'
                ? 'Runs'
                : pageName(name),
            exact: true,
          }),
        ).toBeVisible();
        let submittedDraft: Awaited<ReturnType<typeof draft>> = [];
        await page.route('**/api/profile-patch', async (route) => {
          // Observe the proposed form before the recorded response is allowed through.
          submittedDraft = await draft(page);
          await route.continue();
        });
        const receipt = page.waitForResponse(
          (response) => new URL(response.url()).pathname === PATCH,
        );
        await send(page, name);
        const answer = await receipt;
        expect(answer.status()).toBe(refused ? 409 : 200);
        expect(await answer.json()).toEqual(fencedCase(refused ? REFUSAL : name).body);
        expect(await bodiesTo(page, PATCH)).toEqual([fencedCase(name).request.body]);
        if (name.endsWith('deleted'))
          expect((await bodiesTo(page, PATCH))[0]).not.toHaveProperty('value');
        const source = page.waitForResponse(
          (response) => new URL(response.url()).pathname === '/api/profile-src/mine',
        );
        if (refused) {
          await expect(
            page.getByText('Not saved: the account or profile changed', { exact: true }),
          ).toBeVisible();
          const context = page.locator('main pre').filter({ hasText: '"account": "beta"' });
          await expect(context.first()).toContainText('theirs');
          expect(await draft(page)).toEqual(submittedDraft);
          const before = await draft(page);
          await read(page, name);
          expect(await (await source).json()).toEqual(
            name === 'active_plan_saved'
              ? fencedCase(`read_after_${REFUSAL}`).body
              : profilePatchFencedRead(`profile_patch_fenced_before:${name}`)?.body,
          );
          await completedRead(page, name);
          await expect(
            page.getByText('Not saved: the account or profile changed', { exact: true }),
          ).toBeVisible();
          await expect(context.first()).toContainText('theirs');
          expect(await draft(page)).toEqual(before);
          // Advance the page's polling/retry horizon without a wall-clock sleep.
          await page.clock.fastForward(60_000);
          expect(await bodiesTo(page, PATCH)).toEqual([fencedCase(name).request.body]);
          await expect(
            page.getByText('Not saved: the account or profile changed', { exact: true }),
          ).toBeVisible();
          await expect(context.first()).toContainText('theirs');
          expect(await draft(page)).toEqual(before);
        } else {
          if (pageName(name) === 'Schedule')
            await expect(
              page
                .getByText(name.endsWith('deleted') ? /^Day plan removed\./ : /^Saved schedule\./)
                .first(),
            ).toBeVisible();
          else if (pageName(name).startsWith('Abilities'))
            await expect(
              page.getByText('Saved in the profile. No game action was performed.', {
                exact: true,
              }),
            ).toBeVisible();
          else await expect(page.locator('[data-outcome="saved"]').first()).toBeVisible();
          await read(page, name);
          const readAfter = await source;
          expect(await readAfter.json()).toEqual(fencedCase(`read_after_${name}`).body);
          await completedRead(page, name);
          await savedReadback(page, name);
        }
        await check(page, width);
        await page.screenshot({
          path: path.join(
            OUT,
            `profile-patch-fenced-${screenshotPage(name)}-${refused ? 'refusal' : 'save'}-${width}.png`,
          ),
          fullPage: true,
        });
        expect((await posts(page)).map((post) => post.path)).toEqual([PATCH]);
        expect(problems).toEqual([]);
      });
    }
  }
}

test('RunEntry refused draft survives failed source reread and recovery without another POST', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1054, height: 900 });
  await page.clock.install();
  await page.goto('/__fake/use?scenario=profile_patch_fenced_active_run_entry_label_saved_refused');
  await page.evaluate(() => {
    location.hash = '#runs/saved/farm';
  });
  const row = page.locator('[data-entry-field="label"]');
  const input = row.getByLabel('Run label', { exact: true });
  const refusal = page.getByText('Not saved: the account or profile changed', { exact: true });
  await send(page, 'active_run_entry_label_saved');
  await expect(refusal).toBeVisible();
  await read(page, 'active_run_entry_label_saved');
  await completedRead(page, 'active_run_entry_label_saved');
  let failedReads = 0;
  await page.route('**/api/profile-src/mine', async (route) => {
    failedReads += 1;
    await route.abort('failed');
  });
  await read(page, 'active_run_entry_label_saved');
  await expect(page.getByText('The server could not be reached.', { exact: true })).toBeVisible();
  expect(failedReads).toBeGreaterThan(0);
  await expect(input).toHaveValue('Offline farm');
  await expect(input).toBeDisabled();
  await expect(page.getByText('network', { exact: true })).toHaveCount(0);
  await expect(refusal).toBeVisible();
  await expect(
    page.locator('main pre').filter({ hasText: '"account": "beta"' }).first(),
  ).toContainText('theirs');
  for (const button of await page
    .locator('[data-run-entry]')
    .getByRole('button', { name: 'Save field', exact: true })
    .all()) {
    await expect(button).toBeDisabled();
  }
  await expect(row.getByRole('button', { name: 'Clear label', exact: true })).toBeDisabled();
  await expect(
    row.getByRole('button', { name: 'Discard field draft', exact: true }),
  ).toBeDisabled();
  await check(page, 1054);
  await page.clock.fastForward(60_000);
  expect(await bodiesTo(page, PATCH)).toEqual([
    fencedCase('active_run_entry_label_saved').request.body,
  ]);
  const failure = {
    value: await input.inputValue(),
    disabled: await input.isDisabled(),
    refusalVisible: await refusal.isVisible(),
    posts: await bodiesTo(page, PATCH),
    failedReads,
  };
  await page.screenshot({
    path: path.join(OUT, 'runentry-refusal-read-failed.png'),
    fullPage: true,
  });
  await page.unroute('**/api/profile-src/mine');
  await read(page, 'active_run_entry_label_saved');
  await completedRead(page, 'active_run_entry_label_saved');
  await expect(input).toHaveValue('Offline farm');
  await expect(input).toBeEnabled();
  await expect(row.getByRole('button', { name: 'Save field', exact: true })).toBeEnabled();
  await expect(refusal).toBeVisible();
  await expect(page.getByText('The server could not be reached.', { exact: true })).toHaveCount(0);
  await page.clock.fastForward(60_000);
  expect(await bodiesTo(page, PATCH)).toEqual([
    fencedCase('active_run_entry_label_saved').request.body,
  ]);
  writeFileSync(
    path.join(OUT, 'runentry-refusal-read-recovery.json'),
    JSON.stringify(
      {
        failure,
        recovered: {
          value: await input.inputValue(),
          disabled: await input.isDisabled(),
          refusalVisible: await refusal.isVisible(),
          posts: await bodiesTo(page, PATCH),
        },
      },
      null,
      2,
    ),
  );
  await page.screenshot({
    path: path.join(OUT, 'runentry-refusal-read-recovered.png'),
    fullPage: true,
  });
});

test('RunEntry current source without a revision stays editable with Save blocked', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1054, height: 900 });
  await page.route('**/api/profile-src/mine', async (route) => {
    const recorded = profilePatchFencedRead(
      'profile_patch_fenced_before:active_run_entry_label_saved',
    );
    if (recorded === undefined) throw new Error('missing recorded entry source');
    await route.fulfill({
      status: 200,
      json: Object.fromEntries(Object.entries(recorded.body).filter(([key]) => key !== 'revision')),
    });
  });
  await page.goto('/__fake/use?scenario=profile_patch_fenced_active_run_entry_label_saved_refused');
  await page.evaluate(() => {
    location.hash = '#runs/saved/farm';
  });
  const row = page.locator('[data-entry-field="label"]');
  const input = row.getByLabel('Run label', { exact: true });
  await expect(input).toBeEnabled();
  await input.fill('Keep editing');
  await expect(input).toHaveValue('Keep editing');
  await expect(row.getByRole('button', { name: 'Save field', exact: true })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  expect(await bodiesTo(page, PATCH)).toEqual([]);
  await page.screenshot({
    path: path.join(OUT, 'runentry-current-source-no-revision.png'),
    fullPage: true,
  });
  writeFileSync(
    path.join(OUT, 'runentry-current-source-no-revision.json'),
    JSON.stringify({
      value: await input.inputValue(),
      disabled: await input.isDisabled(),
      saveDisabled: await row
        .getByRole('button', { name: 'Save field', exact: true })
        .getAttribute('aria-disabled'),
      posts: await bodiesTo(page, PATCH),
    }),
  );
});
