import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import {
  DASHBOARD_PAGES_FIXTURE_PATH,
  loadJsonWithIdentity,
} from '../../scripts/contract-fixture.mjs';
import { axeCounts, bodiesTo, navigateApp, openScenario, posts } from './helpers/helpers.ts';
import { profilesContextCase } from '../support/profiles-context.ts';

/**
 * The Profiles page in a real browser on the fake backend (brief
 * backend/logs/reviews/ui-pages/profiles-brief.md, P1 to P5): navigation from
 * the Dashboard, the account switch, a failed creation and an owned device
 * (one POST per click, never resent), the tier's typed refusals, axe, reflow
 * and target sizes at 1360, 1054 and 390, and the screenshots
 * (TP_F6_SCREENS_DIR, else test-results/f6-screens).
 */
const WIDTHS = [1360, 1054, 390] as const;
const OUT = process.env['TP_F6_SCREENS_DIR'] ?? path.resolve('test-results', 'f6-screens');
mkdirSync(OUT, { recursive: true });

async function openProfiles(page: Page, scenario: string, width = 1360) {
  const problems: string[] = [];
  page.on('pageerror', (error) => {
    problems.push(error.message);
  });
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`/__fake/use?scenario=${scenario}`);
  await page.evaluate(() => {
    window.location.hash = '#profiles';
  });
  await expect(page.getByRole('heading', { level: 1, name: 'Profiles' })).toBeVisible();
  // The accounts were read: the region says the state (none, none chosen, or the one in use).
  await expect(account(page).locator('[data-active-account="true"]')).toBeVisible();
  return problems;
}

const reviewed = (body: unknown) => ({
  ...(body as Record<string, unknown>),
  expected_context: { instance: 'main', serial: 'fake:1', account: null, profile: 'mine' },
});

const account = (page: Page) => page.locator('[data-region="profilesAccount"]');

/** A recorded case of the pages contract fixture (accounts_write, profile_files_write). */
function pagesCase(path: string): {
  request: { path: string; body: unknown };
  status: number;
  body: unknown;
} {
  const [family = '', name = ''] = path.split('.');
  const cases = (
    loadJsonWithIdentity(DASHBOARD_PAGES_FIXTURE_PATH).fixture as {
      cases: Record<
        string,
        Record<string, { request: { path: string; body: unknown }; status: number; body: unknown }>
      >;
    }
  ).cases;
  const found = cases[family]?.[name];
  if (found === undefined) throw new Error(`No pages case ${path}`);
  return found;
}

for (const width of WIDTHS) {
  test(`F6P-R2-01 held tier 01 survives source 404 and accounts fallback at ${width}; later original source settles without resend`, async ({
    page,
  }) => {
    let accounts = pagesCase('accounts_write.read_after_the_tier').body;
    let source = pagesCase(
      'accounts_write.profile_source_after_the_tier_records_the_operator_as_provenance',
    );
    const sent: unknown[] = [];
    let release: (() => void) | undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route('**/api/config', (route) =>
      route.fulfill({ json: pagesCase('config_state.populated_accounts_and_loadouts').body }),
    );
    await page.route('**/api/accounts', (route) => route.fulfill({ json: accounts }));
    await page.route('**/api/profile-src/mine', (route) =>
      route.fulfill({ status: source.status, json: source.body }),
    );
    await page.route('**/api/account-tier', async (route) => {
      sent.push(route.request().postDataJSON() as unknown);
      await held;
      await route.fulfill({ json: { ok: true, max_tier: 1 } }).catch(() => undefined);
    });
    const problems = await openProfiles(page, 'profiles', width);
    const tier = page.locator('[data-region="profilesTier"]');
    const input = tier.getByRole('textbox', { name: /^Highest unlocked tier/u });
    await expect(input).toHaveValue('12');
    await input.fill('01');
    await tier.getByRole('button', { name: 'Save highest tier' }).click();
    await expect.poll(() => sent.length).toBe(1);
    expect(sent).toEqual([
      {
        max_tier: 1,
        expected_context: { instance: 'main', account: 'alpha', profile: 'mine', serial: 'fake:1' },
      },
    ]);
    source = pagesCase('profile_reads.profile_src_unknown_404');
    accounts = pagesCase('accounts_write.read_after_the_failed_tier_write_unchanged').body;
    await page.reload();
    await expect(tier.locator('[data-tier-saved] dd')).toHaveText('Saved tier unavailable');
    await expect(
      tier.getByText('No confirmed reply - it may or may not have been done'),
    ).toBeVisible();
    await expect(tier.getByText('A later read shows the requested result')).toHaveCount(0);
    await expect(input).toHaveValue('01');
    expect(
      await page.evaluate(() =>
        Object.keys(sessionStorage).some(
          (key) =>
            key.startsWith('tp.intents.v1:') &&
            sessionStorage.getItem(key)?.includes('account_tier'),
        ),
      ),
    ).toBe(true);
    await tier.screenshot({ path: path.join(OUT, `profiles-tier-fallback-pending-${width}.png`) });
    const saved = pagesCase(
      'accounts_write.profile_source_after_the_tier_records_the_operator_as_provenance',
    );
    const body = saved.body as Record<string, unknown>;
    const profile = body['profile'] as Record<string, unknown>;
    source = {
      ...saved,
      body: {
        ...body,
        profile: { ...profile, player: { max_tier: 1, max_tier_verified_by: 'operator' } },
      },
    };
    release?.();
    await page.reload();
    await expect(tier.getByText('A later read shows the requested result')).toBeVisible();
    await expect(input).toHaveValue('1');
    await expect(tier.locator('[data-tier-saved]')).toContainText('entered by you');
    await tier.screenshot({ path: path.join(OUT, `profiles-tier-source-confirmed-${width}.png`) });
    expect(sent).toHaveLength(1);
    expect(problems).toEqual([]);
  });
}

test('F6PE-P01 create/import consent remains with Alpha across a Beta round trip', async ({
  page,
}) => {
  let config = pagesCase('config_state.populated_accounts_and_loadouts').body;
  await page.route('**/api/config', (route) => route.fulfill({ json: config }));
  await openProfiles(page, 'profiles');
  const create = page.locator('[data-form="create"]');
  const identifier = create.getByRole('textbox', { name: /^Identifier/u });
  const consent = create.getByRole('checkbox');
  await identifier.fill('alpha_clone');
  await consent.check();
  config = pagesCase('config_state.effective_view_of_the_other_account').body;
  await page.reload();
  await expect(identifier).toHaveValue('');
  await expect(consent).not.toBeChecked();
  await identifier.fill('beta_clone');
  await expect(consent).not.toBeChecked();
  await create.screenshot({ path: path.join(OUT, 'profiles-import-beta-fresh-consent-1360.png') });
  config = pagesCase('config_state.populated_accounts_and_loadouts').body;
  await page.reload();
  await expect(identifier).toHaveValue('alpha_clone');
  await expect(consent).toBeChecked();
  await create.screenshot({ path: path.join(OUT, 'profiles-import-alpha-restored-1360.png') });
  expect(await posts(page)).toEqual([]);
});

test('F6PE-P03 a held create survives reload and absent readback until its later commit, without resending', async ({
  page,
}) => {
  let accounts = pagesCase('accounts_write.read_before_any_account').body;
  let received = 0;
  let release: (() => void) | undefined;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/accounts', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ json: accounts });
      return;
    }
    received += 1;
    await held;
    // Reload can close the original connection; committing is independent of that connection.
    await route
      .fulfill({ json: pagesCase('accounts_write.create_a_fresh_account').body })
      .catch(() => undefined);
  });
  await openProfiles(page, 'profiles');
  const create = page.locator('[data-form="create"]');
  await create.getByRole('textbox', { name: /^Identifier/u }).fill('Alpha');
  await create.getByRole('button', { name: 'Create account' }).click();
  await expect.poll(() => received).toBe(1);
  await page.reload();
  await expect(
    account(page).getByText('No confirmed reply - it may or may not have been done'),
  ).toBeVisible();
  await expect(account(page)).not.toContainText('A later read shows it was not done');
  expect(
    await page.evaluate(() =>
      Object.keys(sessionStorage).some(
        (key) =>
          key.startsWith('tp.intents.v1:') &&
          sessionStorage.getItem(key)?.includes('account_create'),
      ),
    ),
  ).toBe(true);
  await account(page).screenshot({ path: path.join(OUT, 'profiles-create-pending-1360.png') });
  accounts = pagesCase('accounts_write.read_after_create_names_the_account_active').body;
  release?.();
  await page.reload();
  await expect(account(page).getByText('A later read shows the requested result')).toBeVisible();
  expect(received).toBe(1);
});

test('F6PE-P04 unreadable active profile has no saved tier claim and retains the raw draft', async ({
  page,
}) => {
  await openProfiles(page, 'profiles');
  const tier = page.locator('[data-region="profilesTier"]');
  const input = tier.getByRole('textbox', { name: /^Highest unlocked tier/u });
  await expect(tier.locator('[data-tier-saved]')).toContainText('Default:');
  await input.fill('021');
  await page.route('**/api/profile-src/mine', (route) =>
    route.fulfill({ status: 404, json: { ok: false, error: 'mine.yaml unavailable' } }),
  );
  await page.reload();
  await expect(tier.locator('[data-tier-saved] dd')).toHaveText('Saved tier unavailable');
  await expect(input).toHaveValue('021');
  await tier.screenshot({ path: path.join(OUT, 'profiles-tier-unavailable-1360.png') });
  expect(await posts(page)).toEqual([]);
});

test('F6PE-P02 recorded r10 switch after preflight refuses the reviewed context and keeps the tier draft', async ({
  page,
}) => {
  let sent: unknown;
  await page.route('**/api/account-tier', async (route) => {
    sent = route.request().postDataJSON() as unknown;
    await route.fulfill({
      status: profilesContextCase('account_tier_composed_for_alpha_is_refused_after_the_switch')
        .status,
      json: profilesContextCase('account_tier_composed_for_alpha_is_refused_after_the_switch').body,
    });
  });
  await page.route('**/api/config', (route) =>
    route.fulfill({ json: pagesCase('config_state.populated_accounts_and_loadouts').body }),
  );
  await openProfiles(page, 'profiles');
  const tier = page.locator('[data-region="profilesTier"]');
  const input = tier.getByRole('textbox', { name: /^Highest unlocked tier/u });
  // Start from the completed source read; a startup value must not arrive during fill.
  await expect(tier.locator('[data-tier-saved]')).toContainText('Default:');
  await input.fill('07');
  await expect(input).toHaveValue('07');
  await tier.getByRole('button', { name: 'Save highest tier' }).click();
  await expect(tier.getByText('Not saved: the account or profile changed')).toBeVisible();
  expect(sent).toEqual(
    profilesContextCase('account_tier_composed_for_alpha_is_refused_after_the_switch').request.body,
  );
  await expect(input).toHaveValue('07');
  await expect(tier.locator('[data-current-context]')).toContainText('beta');
  await expect(tier.locator('[data-current-context]')).toContainText('theirs');
  await expect(tier.locator('[data-current-context]')).toContainText('fake:1');
  await tier.screenshot({ path: path.join(OUT, 'profiles-context-changed-1360.png') });
  expect(await posts(page)).toEqual([]);
});

test('P1 app navigation opens Profiles; Use this account sends the recorded body once and the page then names Alpha', async ({
  page,
}) => {
  const problems = await openScenario(page, 'profiles');
  await navigateApp(page, 'Profiles');
  await expect(page).toHaveURL(/#profiles$/u);
  await expect(page.getByRole('heading', { level: 1, name: 'Profiles' })).toBeVisible();
  const box = account(page);
  await expect(box.getByText('No local account is chosen for this emulator')).toBeVisible();
  await box.getByRole('combobox', { name: 'Local game account' }).selectOption('alpha');
  await box.getByRole('button', { name: 'Use this account' }).dblclick();
  await expect.poll(async () => (await bodiesTo(page, '/api/accounts')).length).toBe(1);
  expect(await bodiesTo(page, '/api/accounts')).toEqual([
    reviewed(pagesCase('accounts_write.select_an_existing_account').request.body),
  ]);
  await expect(box.getByText('This emulator now uses the account')).toBeVisible();
  await expect(box.locator('[data-active-account="true"]')).toContainText('Alpha tower');
  await box.screenshot({ path: path.join(OUT, 'profiles-account-switched-1360.png') });
  await page.waitForTimeout(3_000);
  expect(await bodiesTo(page, '/api/accounts')).toHaveLength(1);
  expect(problems).toEqual([]);
});

test('P2 SR-4 a creation refused after its profile file was written says Not created, keeps the identifier, lists the left-behind file and is never resent', async ({
  page,
}) => {
  const problems = await openProfiles(page, 'profiles_create_failed');
  const create = page.locator('[data-form="create"]');
  await create.getByRole('textbox', { name: /^Identifier/u }).fill('alpha');
  await create.getByRole('button', { name: 'Create account' }).click();
  await expect.poll(async () => (await bodiesTo(page, '/api/accounts')).length).toBe(1);
  expect(await bodiesTo(page, '/api/accounts')).toEqual([
    reviewed(
      pagesCase('accounts_write.create_then_the_config_save_failed_leaves_the_profile_file').request
        .body,
    ),
  ]);
  const box = account(page);
  await expect(box.getByText('Not created')).toBeVisible();
  await expect(box).toContainText('config.yaml could not be written');
  await expect(box).not.toContainText(/nothing changed/iu);
  await expect(create.getByRole('textbox', { name: /^Identifier/u })).toHaveValue('alpha');
  await expect(
    page.locator('[data-region="profilesFiles"] [data-profile="account_alpha"]'),
  ).toBeVisible();
  await box.screenshot({ path: path.join(OUT, 'profiles-create-failed-1360.png') });
  await page.waitForTimeout(3_000);
  expect(await bodiesTo(page, '/api/accounts')).toHaveLength(1);
  expect(problems).toEqual([]);
});

test('P1 a refusal while a runner owns the device shows the server reason; the pick stays and nothing is resent', async ({
  page,
}) => {
  const problems = await openProfiles(page, 'profiles_owned');
  const box = account(page);
  await box.getByRole('combobox', { name: 'Local game account' }).selectOption('alpha');
  await box.getByRole('button', { name: 'Use this account' }).click();
  await expect(box.getByText('Not done')).toBeVisible();
  await expect(box).toContainText('Stop automation before changing accounts.');
  await expect(box.getByRole('combobox', { name: 'Local game account' })).toHaveValue('alpha');
  await expect(box.getByText('No local account is chosen for this emulator')).toBeVisible();
  await page.waitForTimeout(3_000);
  expect(await bodiesTo(page, '/api/accounts')).toHaveLength(1);
  expect(problems).toEqual([]);
});

test('P3 SR-3 0, 21 and 1.5 stay as typed with their reason and send nothing; 12 sends the recorded body once and reads back as entered by you', async ({
  page,
}) => {
  const problems = await openProfiles(page, 'profiles');
  const tier = page.locator('[data-region="profilesTier"]');
  const input = tier.getByRole('textbox', { name: /^Highest unlocked tier/u });
  const save = tier.getByRole('button', { name: 'Save highest tier' });
  for (const [typed, reason] of [
    ['0', 'Use a tier from 1 to 20'],
    ['21', 'Use a tier from 1 to 20'],
    ['1.5', 'Use a whole number, like 12'],
  ] as const) {
    await input.fill(typed);
    await expect(input).toHaveValue(typed);
    await expect(input).toHaveAttribute('aria-invalid', 'true');
    await expect(input).toHaveAccessibleDescription(new RegExp(reason.replace('.', '\\.'), 'u'));
    await expect(save).toHaveAttribute('aria-disabled', 'true');
    await save.click({ force: true });
  }
  await tier.screenshot({ path: path.join(OUT, 'profiles-tier-invalid-1360.png') });
  expect(await posts(page)).toEqual([]);
  await input.fill('12');
  await save.click();
  await expect.poll(async () => (await bodiesTo(page, '/api/account-tier')).length).toBe(1);
  expect(await bodiesTo(page, '/api/account-tier')).toEqual([
    reviewed(pagesCase('accounts_write.tier_saved_by_the_operator').request.body),
  ]);
  await expect(tier.locator('[data-tier-saved="true"]')).toContainText(
    'entered by you, not read from the game',
  );
  await page.waitForTimeout(3_000);
  expect(await bodiesTo(page, '/api/account-tier')).toHaveLength(1);
  expect(problems).toEqual([]);
});

test('P1 SR-2 keyboard: the switch question takes focus; Escape and Cancel give it back to Use this account; Enter on Switch sends the recorded body once', async ({
  page,
}) => {
  const problems = await openProfiles(page, 'profiles');
  const box = account(page);
  // An unsaved tier stays with the account before: the switch asks first.
  await page
    .locator('[data-region="profilesTier"]')
    .getByRole('textbox', { name: /^Highest unlocked tier/u })
    .fill('12');
  const picker = box.getByRole('combobox', { name: 'Local game account' });
  await picker.focus();
  await picker.selectOption('alpha');
  await page.keyboard.press('Tab');
  const use = box.getByRole('button', { name: 'Use this account' });
  await expect(use).toBeFocused();
  await page.keyboard.press('Enter');
  const confirm = box.getByRole('button', { name: 'Switch and keep them there' });
  await expect(confirm).toBeFocused();
  await expect(box.getByRole('group', { name: /^1 unsaved edit and 0 requests/u })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(confirm).toHaveCount(0);
  await expect(use).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(confirm).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(box.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(use).toBeFocused();
  expect(await posts(page)).toEqual([]);
  await page.keyboard.press('Enter');
  await expect(confirm).toBeFocused();
  await page.keyboard.press('Enter');
  await expect.poll(async () => (await bodiesTo(page, '/api/accounts')).length).toBe(1);
  expect(await bodiesTo(page, '/api/accounts')).toEqual([
    reviewed(pagesCase('accounts_write.select_an_existing_account').request.body),
  ]);
  await expect(box.getByText('This emulator now uses the account')).toBeVisible();
  await page.waitForTimeout(3_000);
  expect(await bodiesTo(page, '/api/accounts')).toHaveLength(1);
  expect(await bodiesTo(page, '/api/account-tier')).toEqual([]);
  expect(problems).toEqual([]);
});

test('P4 tools missing, modules waiting to be restored and a run not ready stay distinct; the Emulators link opens its page and nothing is sent', async ({
  page,
}) => {
  const problems = await openProfiles(page, 'profiles_setup');
  const setup = page.locator('[data-region="profilesSetup"]');
  await expect(setup.getByText('Not installed', { exact: true })).toBeVisible();
  await expect(setup.getByText('Original modules are waiting to be restored')).toBeVisible();
  const readiness = setup.locator('[data-setup="runs"]');
  const ready = readiness.locator('.tp-facts__item').filter({ hasText: 'Runs ready to start' });
  const blocked = readiness.locator('.tp-facts__item').filter({ hasText: 'Not ready' });
  await expect(ready).toBeVisible();
  await expect(ready.locator('dd')).toHaveText('1');
  await expect(blocked).toBeVisible();
  await expect(blocked.locator('dd')).toHaveText('1');
  await expect(setup).toContainText('A saved account does not mean its screens are learned');
  await expect(setup.getByRole('link', { name: 'Recognition' })).toHaveAttribute(
    'href',
    '#recognition',
  );
  await setup.screenshot({ path: path.join(OUT, 'profiles-setup-1360.png') });
  expect(await axeCounts(page)).toEqual([]);
  await setup.getByRole('link', { name: 'Emulators' }).click();
  await expect(page).toHaveURL(/\/ui\/app\/#emulators$/u);
  expect(await posts(page)).toEqual([]);
  expect(problems).toEqual([]);
});

for (const width of WIDTHS) {
  test(`P5 at ${String(width)}: a profile with problems cannot be activated; the checked starter is activated once; a scan draft saved with problems is not active and lists them; axe`, async ({
    page,
  }) => {
    const problems = await openProfiles(page, 'profiles', width);
    const activate = page.locator('[data-form="activate"]');
    const picker = activate.getByRole('combobox', { name: 'Profile to activate' });
    const button = activate.getByRole('button', { name: 'Activate' });
    await picker.selectOption('theirs');
    await expect(activate).toContainText('2 problems');
    await expect(button).toHaveAttribute('aria-disabled', 'true');
    await expect(button).toHaveAccessibleDescription(
      'This profile has problems; fix them before activating it',
    );
    await button.click({ force: true });
    await picker.selectOption('default');
    await expect(activate.getByText('No problems found')).toBeVisible();
    await button.focus();
    await page.keyboard.press('Enter');
    await expect.poll(async () => (await bodiesTo(page, '/api/profile-activate')).length).toBe(1);
    expect(await bodiesTo(page, '/api/profile-activate')).toEqual([
      reviewed(pagesCase('profile_files_write.activate_the_starter').request.body),
    ]);
    await expect(
      activate.getByText('Activated - automation uses it from its next start'),
    ).toBeVisible();
    const promote = page.locator('[data-form="promote"]');
    await promote.getByRole('textbox', { name: 'Profile name' }).fill('promoted');
    await promote.getByRole('button', { name: 'Save as profile' }).click();
    await expect.poll(async () => (await bodiesTo(page, '/api/profile-promote')).length).toBe(1);
    expect(await bodiesTo(page, '/api/profile-promote')).toEqual([
      reviewed({ draft: 'mine.draft.yaml', name: 'promoted', base: 'default' }),
    ]);
    const files = page.locator('[data-region="profilesFiles"]');
    await expect(files.getByText('Saved as a profile - not active')).toBeVisible();
    await expect(files).toContainText('Saved with 3 problems; fix them before activating it');
    await expect(files.locator('[data-profile="promoted"]')).toHaveAttribute(
      'data-active',
      'false',
    );
    expect(await axeCounts(page)).toEqual([]);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
    await files.screenshot({
      path: path.join(OUT, `profiles-files-outcomes-${String(width)}.png`),
    });
    await page.waitForTimeout(2_000);
    expect(await bodiesTo(page, '/api/profile-activate')).toHaveLength(1);
    expect(await bodiesTo(page, '/api/profile-promote')).toHaveLength(1);
    expect(problems).toEqual([]);
  });
}

test('P5 copying sends the recorded body once and activates nothing; Replace is consent for one source and name; the active profile is never a destination', async ({
  page,
}) => {
  const problems = await openProfiles(page, 'profiles');
  const copy = page.locator('[data-form="copy"]');
  const from = copy.getByRole('combobox', { name: 'Copy from' });
  const name = copy.getByRole('textbox', { name: 'New profile name' });
  const button = copy.getByRole('button', { name: 'Copy' });
  await from.selectOption('mine');
  await name.fill('mine');
  await expect(button).toHaveAccessibleDescription(
    'This is the active profile; choose another name',
  );
  await name.fill('theirs');
  const replace = copy.getByRole('checkbox', {
    name: 'Replace the existing profile with this name',
  });
  await expect(replace).not.toBeChecked();
  await expect(button).toHaveAttribute('aria-disabled', 'true');
  await replace.check();
  await expect(button).not.toHaveAttribute('aria-disabled', 'true');
  await from.selectOption('default');
  await expect(replace).not.toBeChecked();
  await expect(button).toHaveAccessibleDescription(
    'A profile with this name exists; tick Replace to overwrite it',
  );
  await copy.screenshot({ path: path.join(OUT, 'profiles-copy-consent-1360.png') });
  expect(await posts(page)).toEqual([]);
  await from.selectOption('mine');
  await name.fill('spare');
  await button.dblclick();
  await expect.poll(async () => (await bodiesTo(page, '/api/profile-copy')).length).toBe(1);
  expect(await bodiesTo(page, '/api/profile-copy')).toEqual([
    reviewed(pagesCase('profile_files_write.copy_one_of_your_own_profiles').request.body),
  ]);
  const files = page.locator('[data-region="profilesFiles"]');
  await expect(files.getByText('Copied - not active')).toBeVisible();
  await expect(files.locator('[data-active-profile="true"]')).toContainText('mine');
  await page.waitForTimeout(2_000);
  expect(await bodiesTo(page, '/api/profile-copy')).toHaveLength(1);
  expect(problems).toEqual([]);
});

test('P5 SR-4 a copy whose answer is lost reads the profile list again by itself, says the read shows it was done, and is never resent', async ({
  page,
}) => {
  const problems = await openProfiles(page, 'profiles_copy_lost');
  const copy = page.locator('[data-form="copy"]');
  await copy.getByRole('textbox', { name: 'New profile name' }).fill('fresh');
  await copy.getByRole('button', { name: 'Copy' }).click();
  await expect.poll(async () => (await bodiesTo(page, '/api/profile-copy')).length).toBe(1);
  const files = page.locator('[data-region="profilesFiles"]');
  await expect(files.getByText('A later read shows the requested result')).toBeVisible();
  await expect(files.locator('[data-profile="fresh"]')).toBeVisible();
  await files.screenshot({ path: path.join(OUT, 'profiles-copy-lost-1360.png') });
  await page.waitForTimeout(3_000);
  expect(await bodiesTo(page, '/api/profile-copy')).toHaveLength(1);
  expect(problems).toEqual([]);
});

for (const width of WIDTHS) {
  test(`P1-P5 axe, reflow and targets of the Profiles page at ${String(width)}, and its screenshot`, async ({
    page,
  }) => {
    const problems = await openProfiles(page, 'profiles', width);
    await expect(
      page.locator('[data-region="profilesFiles"] [data-profile]').first(),
    ).toBeVisible();
    await page.waitForTimeout(500);
    expect(await axeCounts(page)).toEqual([]);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
    const small = await page.evaluate(() =>
      [
        ...document.querySelectorAll<HTMLElement>(
          'main button, main input, main select, main summary',
        ),
      ]
        .filter((element) => element.getClientRects().length > 0)
        .map((element) => ({
          label: element.outerHTML.slice(0, 60),
          box: element.getBoundingClientRect(),
        }))
        .filter((entry) => entry.box.height < 24 || entry.box.width < 24)
        .map((entry) => entry.label),
    );
    expect(small).toEqual([]);
    if (width === 390) {
      const draft = page.locator('[data-profile="mine.draft"]');
      const name = draft.locator('.tp-profiles__file-name [data-server-text="name"]');
      await expect(name).toHaveText('mine.draft');
      expect(await name.evaluate((element) => element.getClientRects().length)).toBe(1);
      const nameBox = await name.boundingBox();
      const modifiedBox = await draft.locator('.tp-profiles__file-modified').boundingBox();
      expect(nameBox).not.toBeNull();
      expect(modifiedBox).not.toBeNull();
      if (nameBox !== null && modifiedBox !== null) {
        expect(modifiedBox.y).toBeGreaterThan(nameBox.y + nameBox.height);
      }
      await expect(draft.locator('.tp-profiles__help')).toBeVisible();
      const buttons = await page.locator('main button').evaluateAll((all) =>
        all
          .filter((element) => element.getClientRects().length > 0)
          .map((element) => ({
            label: element.textContent,
            height: element.getBoundingClientRect().height,
          }))
          .filter((entry) => entry.height < 44)
          .map((entry) => entry.label),
      );
      expect(buttons).toEqual([]);
    }
    await page.screenshot({
      path: path.join(OUT, `profiles-page-${String(width)}.png`),
      fullPage: true,
    });
    expect(await posts(page)).toEqual([]);
    expect(problems).toEqual([]);
  });
}
