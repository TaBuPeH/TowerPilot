import { editTargetKey } from '../../../../src/core/editingTarget.ts';
import {
  editorSource,
  selectEditingTarget,
} from '../../../../src/store/selectors/editingTarget.ts';
import { selectReadPlan } from '../../../../src/store/selectors/readPlan.ts';
import { within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { contextScopeKey } from '../../../../src/store/intents.ts';
import { selectProfilesPage } from '../../../../src/store/selectors/profiles.ts';
import { savedScope } from '../../../../src/store/selectors/runs.ts';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import { E0_CONTEXT, postBodies } from '../../../support/dataLayer.ts';
import {
  dashboardCase,
  dashboardPagesCase,
  type DashboardCase,
} from '../../../support/dashboardFixture.ts';
import { sample, statusBody } from '../../../support/fixture.ts';
import { renderPage, type PageOptions } from '../dashboard/harness.tsx';
import { profilesContextCase } from '../../../support/profiles-context.ts';

/**
 * The Profiles page (backend/logs/reviews/ui-pages/profiles-brief.md, P1 to
 * P5) on the whole wired app with a fake transport. Every answer is a recorded
 * case of the pages contract (dashboard_contract_pages_v1.json, families
 * accounts_write, profile_files_write, profile_reads, config_state) or of the
 * Dashboard contract; where a test needs a profile's source under another
 * name, it renames a recorded body and says so.
 */
const pages = (path: string) => dashboardPagesCase(path);

function answerOf(found: DashboardCase): { readonly status: number; readonly body: unknown } {
  const body = found.body as Record<string, unknown>;
  return {
    status: found.status,
    body: Object.keys(body).length === 1 && typeof body['text'] === 'string' ? body['text'] : body,
  };
}

type Reads = Partial<Record<'config' | 'accounts' | 'files' | 'source', () => DashboardCase>>;

/** The page on `reads` (each a function, so a test can change what the next read answers). */
async function openProfiles(reads: Reads = {}, options: PageOptions = {}) {
  window.location.hash = '#profiles';
  const read = (name: keyof Reads, fallback: () => DashboardCase) => () =>
    answerOf((reads[name] ?? fallback)());
  const page = await renderPage({
    status: () => statusBody(sample('status.off')),
    ...options,
    before: (fake) => {
      fake.route(
        'GET',
        '/api/config',
        read('config', () => pages('config_state.read_before_the_save')),
      );
      fake.route(
        'GET',
        '/api/accounts',
        read('accounts', () => dashboardCase('accounts.none_active')),
      );
      fake.route(
        'GET',
        '/api/profile-files',
        read('files', () => pages('profile_reads.profile_files_with_a_draft_and_the_starter')),
      );
      fake.route(
        'GET',
        /^\/api\/profile-src\//u,
        read('source', () => pages('profile_reads.profile_src_valid')),
      );
      options.before?.(fake);
    },
  });
  await page.advance(1_000);
  return page;
}

type Page = Awaited<ReturnType<typeof openProfiles>>;

describe('F6PE Profiles review regressions', () => {
  it('F6P-R2-01 a held tier 01 stays Unknown with its raw draft and journal across a reload with accounts fallback 1; only the original profile source can settle it', async () => {
    let accounts = () => pages('accounts_write.read_after_the_tier');
    let source = () =>
      pages('accounts_write.profile_source_after_the_tier_records_the_operator_as_provenance');
    const reads: Reads = {
      config: () => pages('config_state.populated_accounts_and_loadouts'),
      accounts: () => accounts(),
      source: () => source(),
    };
    const page = await openProfiles(reads);
    const input = () =>
      within(region('profilesTier')).getByRole('textbox', { name: /^Highest unlocked tier/u });
    expect(input()).toHaveValue('12');
    await page.user.clear(input());
    await page.user.type(input(), '01');
    await page.user.click(
      within(region('profilesTier')).getByRole('button', { name: 'Save highest tier' }),
    );
    await page.advance(0);
    expect(writesTo(page, '/api/account-tier')).toHaveLength(1);
    const held = page.fake.next('/api/account-tier', 'POST');
    page.view.unmount();
    page.data.stop();
    accounts = () => pages('accounts_write.read_after_the_failed_tier_write_unchanged');
    source = () => pages('profile_reads.profile_src_unknown_404');
    const again = await openProfiles(reads, { storage: page.storage });
    const record = () =>
      again.data.machine.snapshot().find((entry) => entry.kind === 'account_tier');
    expect(record()?.phase).toBe('unconfirmed');
    expect(record()?.readSettlement).toBeNull();
    expect(input()).toHaveValue('01');
    expect(region('profilesTier')).toHaveTextContent('Saved tier unavailable');
    expect(region('profilesTier')).toHaveTextContent('No confirmed reply');
    expect(
      Object.values(again.storage.entries?.() ?? {}).some((value) =>
        value.includes('account_tier'),
      ),
    ).toBe(true);
    // A readable source without an explicit saved tier is still only a default.
    source = () => pages('profile_reads.profile_src_valid');
    again.data.poller.read('profileSource');
    await again.advance(1_000);
    expect(record()?.readSettlement).toBeNull();
    expect(input()).toHaveValue('01');
    // A matching value under a different profile name cannot settle Alpha/mine.
    const explicit = (name: string): DashboardCase => {
      const found = pages(
        'accounts_write.profile_source_after_the_tier_records_the_operator_as_provenance',
      );
      const body = found.body as Record<string, unknown>;
      const profile = body['profile'] as Record<string, unknown>;
      return {
        ...found,
        body: {
          ...body,
          name,
          profile: { ...profile, player: { max_tier: 1, max_tier_verified_by: 'operator' } },
        },
      };
    };
    source = () => explicit('theirs');
    again.data.poller.read('profileSource');
    await again.advance(1_000);
    expect(record()?.readSettlement).toBeNull();
    expect(input()).toHaveValue('01');
    // Complete the original server write; readback, rather than a second POST, confirms it.
    source = () => explicit('mine');
    held.respond(200, { ok: true, max_tier: 1 });
    again.data.poller.read('profileSource');
    await again.advance(1_000);
    expect(record()?.readSettlement).toBe('applied');
    expect(region('profilesTier')).toHaveTextContent('A later read shows the requested result');
    expect(input()).toHaveValue('1');
    await again.advance(60_000);
    expect(writesTo(page, '/api/account-tier')).toHaveLength(1);
    expect(writesTo(again, '/api/account-tier')).toEqual([]);
  }, 30_000);

  it('F6PE-P01 Alpha create/import stays with its full source through Beta and back', async () => {
    let config = () => pages('config_state.populated_accounts_and_loadouts');
    const page = await openProfiles({ config: () => config() });
    const identifier = () => within(form('create')).getByRole('textbox', { name: /^Identifier/u });
    const consent = () => within(form('create')).getByRole('checkbox');
    await page.user.type(identifier(), 'alpha_clone');
    await page.user.type(
      within(form('create')).getByRole('textbox', { name: /^Name shown/u }),
      'Alpha copy',
    );
    await page.user.click(consent());
    const alphaScope = Object.keys(page.data.store.getSnapshot().runDrafts).find((key) =>
      key.startsWith('profiles:create:'),
    );
    await page.user.type(
      within(form('copy')).getByRole('textbox', { name: 'New profile name' }),
      'alpha_file',
    );
    const tierInput = within(region('profilesTier')).getByRole('textbox', {
      name: /^Highest unlocked tier/u,
    });
    await page.user.clear(tierInput);
    await page.user.type(tierInput, '21');
    await page.user.selectOptions(
      within(region('profilesAccount')).getByRole('combobox', { name: 'Local game account' }),
      'beta',
    );
    await page.user.click(
      within(region('profilesAccount')).getByRole('button', { name: 'Use this account' }),
    );
    expect(region('profilesAccount').querySelector('[data-switch-confirm]')).toHaveTextContent(
      '3 unsaved edits',
    );
    await page.user.click(
      within(region('profilesAccount')).getByRole('button', { name: 'Switch and keep them there' }),
    );
    await page.advance(0);
    expect(writesTo(page, '/api/accounts')).toEqual([
      {
        id: 'beta',
        expected_context: { instance: 'main', serial: 'fake:1', account: 'alpha', profile: 'mine' },
      },
    ]);
    config = () => pages('config_state.effective_view_of_the_other_account');
    page.fake.next('/api/accounts', 'POST').respond(200, { ok: true, active: 'beta' });
    await page.advance(1_000);
    expect(identifier()).toHaveValue('');
    expect(consent()).not.toBeChecked();
    await page.user.type(identifier(), 'beta_clone');
    expect(consent()).not.toBeChecked();
    await page.user.click(consent());
    config = () => pages('config_state.populated_accounts_and_loadouts');
    page.data.poller.read('config');
    await page.advance(1_000);
    expect(identifier()).toHaveValue('alpha_clone');
    expect(within(form('create')).getByRole('textbox', { name: /^Name shown/u })).toHaveValue(
      'Alpha copy',
    );
    expect(consent()).toBeChecked();
    expect(within(form('copy')).getByRole('textbox', { name: 'New profile name' })).toHaveValue(
      'alpha_file',
    );
    expect(
      within(region('profilesTier')).getByRole('textbox', { name: /^Highest unlocked tier/u }),
    ).toHaveValue('21');
    expect(page.data.store.getSnapshot().runDrafts[alphaScope ?? '']).toEqual({
      id: 'alpha_clone',
      label: 'Alpha copy',
      import_current: true,
    });
    expect(writesTo(page, '/api/accounts')).toHaveLength(1);
  }, 30_000);

  it('F6PE-P03 a received create held across reload and an absent read keeps its journal until the later commit, without resending', async () => {
    let accounts = () => pages('accounts_write.read_before_any_account');
    const reads: Reads = { accounts: () => accounts() };
    const page = await openProfiles(reads);
    await page.user.type(
      within(form('create')).getByRole('textbox', { name: /^Identifier/u }),
      'Alpha',
    );
    await page.user.click(within(form('create')).getByRole('button', { name: 'Create account' }));
    await page.advance(0);
    expect(writesTo(page, '/api/accounts')).toHaveLength(1);
    const held = page.fake.next('/api/accounts', 'POST');
    page.view.unmount();
    page.data.stop();
    const again = await openProfiles(reads, { storage: page.storage });
    const unresolved = () =>
      again.data.machine.snapshot().find((entry) => entry.kind === 'account_create');
    expect(unresolved()?.phase).toBe('unconfirmed');
    expect(unresolved()?.readSettlement).toBeNull();
    expect(
      Object.keys(page.storage.entries?.() ?? {}).some((key) => key.startsWith('tp.intents.v1:')),
    ).toBe(true);
    expect(region('profilesAccount')).not.toHaveTextContent('A later read shows it was not done');
    expect(region('profilesAccount')).toHaveTextContent('No confirmed reply');
    expect(within(form('create')).getByRole('textbox', { name: /^Identifier/u })).toHaveValue(
      'Alpha',
    );
    // The original server-side request completes only now. The reloaded page learns it by GET.
    accounts = () => pages('accounts_write.read_after_create_names_the_account_active');
    const receipt = pages('accounts_write.create_a_fresh_account');
    held.respond(receipt.status, receipt.body);
    again.data.poller.read('accounts');
    await again.advance(1_000);
    expect(region('profilesAccount')).toHaveTextContent('A later read shows the requested result');
    await again.advance(60_000);
    expect(writesTo(page, '/api/accounts')).toHaveLength(1);
    expect(writesTo(again, '/api/accounts')).toEqual([]);
  }, 30_000);

  it('F6PE-P04 a missing active source never calls the accounts fallback a saved tier and keeps raw input', async () => {
    let source = () => pages('profile_reads.profile_src_valid');
    const page = await openProfiles({ source: () => source() });
    const input = () =>
      within(region('profilesTier')).getByRole('textbox', { name: /^Highest unlocked tier/u });
    expect(region('profilesTier').querySelector('[data-tier-saved]')).toHaveTextContent('Default:');
    const defaultValue = within(region('profilesTier'))
      .getByText('Default:')
      .closest('.tp-facts__item');
    expect(defaultValue?.querySelector('dd')).toHaveTextContent(/^1$/u);
    const recorded = within(region('profilesTier'))
      .getByText('Profile tier')
      .closest('.tp-facts__item');
    expect(recorded?.querySelector('dd')).toHaveTextContent(/^not recorded in the profile$/u);
    await page.user.clear(input());
    await page.user.type(input(), '021');
    source = () => ({
      ...pages('profile_reads.profile_src_valid'),
      status: 404,
      body: { ok: false, error: 'mine.yaml unavailable' },
    });
    page.data.poller.read('profileSource');
    await page.advance(1_000);
    const saved = region('profilesTier').querySelector('[data-tier-saved]');
    expect(saved).toHaveTextContent('Saved tier unavailable');
    expect(saved).not.toHaveTextContent('1');
    expect(input()).toHaveValue('021');
    expect(page.fake.writes()).toEqual([]);
  }, 30_000);

  it('F6PE-P02 context_changed after preflight is Not saved, with the exact raw draft kept and no resend', async () => {
    const page = await openProfiles({
      config: () => pages('config_state.populated_accounts_and_loadouts'),
    });
    const input = within(region('profilesTier')).getByRole('textbox', {
      name: /^Highest unlocked tier/u,
    });
    await page.user.clear(input);
    await page.user.type(input, '07');
    await page.user.click(
      within(region('profilesTier')).getByRole('button', { name: 'Save highest tier' }),
    );
    await page.advance(0);
    const conflict = profilesContextCase(
      'account_tier_composed_for_alpha_is_refused_after_the_switch',
    );
    expect(writesTo(page, '/api/account-tier')).toEqual([conflict.request.body]);
    page.fake.next('/api/account-tier', 'POST').respond(conflict.status, conflict.body);
    await page.advance(1_000);
    expect(region('profilesTier')).toHaveTextContent('Not saved: the account or profile changed');
    expect(input).toHaveValue('07');
    expect(region('profilesTier').querySelector('[data-current-context]')).toHaveTextContent(
      'Current emulatormainCurrent accountbetaCurrent profiletheirsCurrent serialfake:1',
    );
    await page.advance(60_000);
    expect(writesTo(page, '/api/account-tier')).toHaveLength(1);
  }, 30_000);
});

function region(name: 'profilesAccount' | 'profilesTier' | 'profilesFiles' | 'profilesSetup') {
  const found = document.querySelector<HTMLElement>(`[data-region="${name}"]`);
  if (found === null) throw new Error(`${name} missing`);
  return found;
}

function form(name: 'create' | 'activate' | 'copy' | 'promote' | 'editing-target'): HTMLElement {
  const found = document.querySelector<HTMLElement>(`[data-form="${name}"]`);
  if (found === null) throw new Error(`form ${name} missing`);
  return found;
}

const reviewed = (body: unknown) => ({
  ...(body as Record<string, unknown>),
  expected_context: E0_CONTEXT,
});

const writesTo = (page: Page, path: string) => postBodies(page.fake, path);

/** A recorded profile source answer under another profile's name (the route answers the name asked). */
function sourceNamed(path: string, name: string): DashboardCase {
  const found = pages(path);
  return { ...found, body: { ...(found.body as Record<string, unknown>), name } };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  window.location.hash = '';
  vi.useRealTimers();
});

describe('P1 the local game account of this emulator', () => {
  it.each([
    ['accounts_write.read_before_any_account', 'No local accounts yet - create one below', 0],
    ['accounts.none_active', 'No local account is chosen for this emulator', 2],
    ['accounts.active_among_several', 'In use on this emulator:', 2],
  ])('P1 %s: the account state is said plainly, never detected', async (path, line, count) => {
    const found = path.startsWith('accounts_write.')
      ? () => pages(path)
      : () => dashboardCase(path);
    await openProfiles({ accounts: found });
    const box = region('profilesAccount');
    expect(within(box).getByText(line)).toBeVisible();
    expect(within(box).queryAllByRole('option').length).toBeGreaterThanOrEqual(count);
    expect(
      within(box).getByText('A name you choose on this computer for one game account'),
    ).toBeVisible();
    expect(textOutsideCatalogue(document.body)).toEqual([]);
  });

  it('P1 SR-2 choosing Alpha with an unsaved Runs edit asks first; the switch sends the recorded body once, the config then names Alpha (a new context) and the edit stays with the account before', async () => {
    let config = () => pages('config_state.read_before_the_save');
    let accounts = () => dashboardCase('accounts.none_active');
    const page = await openProfiles({ config: () => config(), accounts: () => accounts() });
    const before = page.data.store.getSnapshot();
    const oldScope = savedScope(before, 'bp_farm');
    page.data.commands.editDraft(oldScope, 'routines.ad_gems.interval_sec', '030');
    const box = () => region('profilesAccount');
    await page.user.selectOptions(
      within(box()).getByRole('combobox', { name: 'Local game account' }),
      'alpha',
    );
    await page.user.click(within(box()).getByRole('button', { name: 'Use this account' }));
    await page.advance(0);
    expect(page.fake.writes()).toEqual([]);
    const confirm = box().querySelector('[data-switch-confirm="true"]');
    expect(confirm).toHaveTextContent('1 unsaved edit and 0 requests without an answer stay');
    const recorded = pages('accounts_write.select_an_existing_account');
    await page.user.click(
      within(box()).getByRole('button', { name: 'Switch and keep them there' }),
    );
    await page.advance(0);
    expect(writesTo(page, '/api/accounts')).toEqual([reviewed(recorded.request.body)]);
    // Only after the POST left do the reads change: the click-time config read
    // still named the account before, as it must for the switch to be sent.
    config = () => pages('config_state.populated_accounts_and_loadouts');
    accounts = () => pages('accounts_write.read_after_select');
    page.fake.next('/api/accounts', 'POST').respond(recorded.status, recorded.body);
    await page.advance(1_000);
    const after = page.data.store.getSnapshot();
    expect(after.context?.account).toBe('alpha');
    expect(after.epoch).toBeGreaterThan(before.epoch);
    // The edit is kept under the account it was made for, never shown for Alpha.
    expect(after.runDrafts[oldScope]).toEqual({ 'routines.ad_gems.interval_sec': '030' });
    expect(after.runDrafts[savedScope(after, 'bp_farm')]).toBeUndefined();
    expect(within(box()).getByText('This emulator now uses the account')).toBeVisible();
    expect(box().querySelector('[data-active-account="true"]')).toHaveTextContent('Alpha tower');
    await page.advance(60_000);
    expect(writesTo(page, '/api/accounts')).toHaveLength(1);
  });

  it('P1 SR-2 the switch question takes focus; Cancel and Escape close it, give focus back to Use this account and send nothing', async () => {
    const page = await openProfiles();
    const scope = savedScope(page.data.store.getSnapshot(), 'bp_farm');
    page.data.commands.editDraft(scope, 'routines.ad_gems.interval_sec', '030');
    const box = () => region('profilesAccount');
    const confirm = () => within(box()).getByRole('button', { name: 'Switch and keep them there' });
    const use = () => within(box()).getByRole('button', { name: 'Use this account' });
    await page.user.selectOptions(
      within(box()).getByRole('combobox', { name: 'Local game account' }),
      'alpha',
    );
    await page.user.click(use());
    await page.advance(0);
    expect(confirm()).toHaveFocus();
    await page.user.click(within(box()).getByRole('button', { name: 'Cancel' }));
    expect(box().querySelector('[data-switch-confirm="true"]')).toBeNull();
    expect(use()).toHaveFocus();
    await page.user.keyboard('{Enter}');
    await page.advance(0);
    expect(confirm()).toHaveFocus();
    await page.user.keyboard('{Escape}');
    expect(box().querySelector('[data-switch-confirm="true"]')).toBeNull();
    expect(use()).toHaveFocus();
    await page.advance(60_000);
    expect(page.fake.writes()).toEqual([]);
    expect(page.data.store.getSnapshot().runDrafts[scope]).toEqual({
      'routines.ad_gems.interval_sec': '030',
    });
  });

  it('P1 a read of the accounts started before the switch never replaces it', async () => {
    let accounts: () => DashboardCase | 'hold' = () => dashboardCase('accounts.none_active');
    let config = () => pages('config_state.read_before_the_save');
    const page = await openProfiles(
      { config: () => config() },
      {
        before: (fake) => {
          fake.route('GET', '/api/accounts', () => {
            const found = accounts();
            return found === 'hold' ? 'hold' : answerOf(found);
          });
        },
      },
    );
    const box = () => region('profilesAccount');
    await page.user.selectOptions(
      within(box()).getByRole('combobox', { name: 'Local game account' }),
      'alpha',
    );
    // An accounts read starts and is held: it was asked before the switch.
    accounts = () => 'hold';
    page.data.poller.read('accounts');
    await page.advance(0);
    await page.user.click(within(box()).getByRole('button', { name: 'Use this account' }));
    await page.advance(0);
    const recorded = pages('accounts_write.select_an_existing_account');
    page.fake.next('/api/accounts', 'POST').respond(recorded.status, recorded.body);
    await page.advance(0);
    // The old read answers late with the account list before the switch.
    page.fake.next('/api/accounts', 'GET').respond(200, dashboardCase('accounts.none_active').body);
    await page.advance(0);
    expect(within(box()).queryByText('No local account is chosen for this emulator')).toBeNull();
    accounts = () => pages('accounts_write.read_after_select');
    config = () => pages('config_state.populated_accounts_and_loadouts');
    page.data.poller.read('config');
    await page.advance(1_000);
    expect(box().querySelector('[data-active-account="true"]')).toHaveTextContent('Alpha tower');
  });

  it('P1 a refusal while a runner owns the device says so with the server reason; the pick stays and nothing is resent', async () => {
    const page = await openProfiles();
    const box = () => region('profilesAccount');
    const picker = within(box()).getByRole('combobox', { name: 'Local game account' });
    await page.user.selectOptions(picker, 'alpha');
    await page.user.click(within(box()).getByRole('button', { name: 'Use this account' }));
    await page.advance(0);
    const refused = pages('accounts_write.refused_while_a_runner_owns_the_device');
    page.fake.next('/api/accounts', 'POST').respond(refused.status, refused.body);
    await page.advance(1_000);
    expect(within(box()).getByText('Not done')).toBeVisible();
    expect(box()).toHaveTextContent('Stop automation before changing accounts.');
    expect(within(box()).getByRole('combobox', { name: 'Local game account' })).toHaveValue(
      'alpha',
    );
    expect(page.data.store.getSnapshot().context?.account).toBeNull();
    await page.advance(60_000);
    expect(writesTo(page, '/api/accounts')).toHaveLength(1);
  });
});

describe('P2 creating a local account', () => {
  it('P2 account import exposes its same-account condition before consent without opening Why', async () => {
    const page = await openProfiles({
      accounts: () => pages('accounts_write.read_before_any_account'),
    });
    const box = form('create');
    expect(within(box).getByText('Only for this same game account.')).toBeVisible();
    expect(within(box).getByRole('checkbox')).toHaveAccessibleDescription(
      'Only for this same game account.',
    );
    expect(page.fake.writes()).toEqual([]);
  });

  async function create(page: Page, id: string, label = '', importCurrent = false) {
    const box = form('create');
    await page.user.type(within(box).getByRole('textbox', { name: /^Identifier/u }), id);
    if (label !== '')
      await page.user.type(within(box).getByRole('textbox', { name: /^Name shown/u }), label);
    if (importCurrent) await page.user.click(within(box).getByRole('checkbox'));
    await page.user.click(within(box).getByRole('button', { name: 'Create account' }));
    await page.advance(0);
  }

  it.each([
    ['create_a_fresh_account', 'Alpha', 'Alpha tower', false],
    ['create_an_account_importing_the_current_one', 'beta', '', true],
  ] as const)(
    'P2 %s sends the recorded body once; the answer creates it and the form clears',
    async (name, id, label, importCurrent) => {
      const page = await openProfiles({
        accounts: () => pages('accounts_write.read_before_any_account'),
      });
      await create(page, id, label, importCurrent);
      const recorded = pages(`accounts_write.${name}`);
      expect(writesTo(page, '/api/accounts')).toEqual([reviewed(recorded.request.body)]);
      page.fake.next('/api/accounts', 'POST').respond(recorded.status, recorded.body);
      await page.advance(1_000);
      expect(
        within(region('profilesAccount')).getByText('Account created and in use'),
      ).toBeVisible();
      expect(within(form('create')).getByRole('textbox', { name: /^Identifier/u })).toHaveValue('');
    },
  );

  it('P2 a creation refused after its profile file was written says Not created without claiming nothing changed; the input stays, the files read shows what exists, nothing is resent', async () => {
    let files = () => pages('profile_reads.profile_files_active_and_patchable');
    let accounts = () => pages('accounts_write.read_before_any_account');
    const page = await openProfiles({ files: () => files(), accounts: () => accounts() });
    await create(page, 'alpha');
    files = () => pages('accounts_write.profile_files_after_the_failed_create_list_the_orphan');
    accounts = () => pages('accounts_write.read_after_the_failed_create_no_account');
    const failed = pages(
      'accounts_write.create_then_the_config_save_failed_leaves_the_profile_file',
    );
    expect(writesTo(page, '/api/accounts')).toEqual([reviewed(failed.request.body)]);
    page.fake.next('/api/accounts', 'POST').respond(failed.status, failed.body);
    await page.advance(1_000);
    const box = region('profilesAccount');
    expect(within(box).getByText('Not created')).toBeVisible();
    expect(box).toHaveTextContent('config.yaml could not be written');
    expect(box).not.toHaveTextContent(/nothing changed/iu);
    expect(within(form('create')).getByRole('textbox', { name: /^Identifier/u })).toHaveValue(
      'alpha',
    );
    expect(region('profilesFiles').querySelector('[data-profile="account_alpha"]')).not.toBeNull();
    await page.advance(60_000);
    expect(writesTo(page, '/api/accounts')).toHaveLength(1);
  });

  it.each([
    [
      'accounts_write.read_after_the_failed_create_no_account',
      'No confirmed reply - it may or may not have been done',
      'Alpha',
    ],
    [
      'accounts_write.read_after_create_names_the_account_active',
      'A later read shows the requested result',
      '',
    ],
  ])(
    'P2 SR-4 a creation without an answer at a reload is kept and never resent; the read after it (%s) confirms the result or keeps it unknown',
    async (read, line, kept) => {
      let accounts = () => pages('accounts_write.read_before_any_account');
      const reads: Reads = { accounts: () => accounts() };
      const page = await openProfiles(reads);
      await create(page, 'Alpha', 'Alpha tower');
      const recorded = pages('accounts_write.create_a_fresh_account');
      expect(writesTo(page, '/api/accounts')).toEqual([reviewed(recorded.request.body)]);
      // The page goes before the answer; a new page starts on the same session storage.
      page.view.unmount();
      page.data.stop();
      accounts = () => pages(read);
      const again = await openProfiles(reads, { storage: page.storage });
      const box = region('profilesAccount');
      const outcome = box.querySelector('[data-kind="account_create"]');
      expect(outcome).toHaveTextContent('kept from before the page was reloaded');
      expect(outcome).toHaveTextContent(line);
      expect(outcome).toHaveTextContent('Alpha');
      expect(within(form('create')).getByRole('textbox', { name: /^Identifier/u })).toHaveValue(
        kept,
      );
      await again.advance(60_000);
      expect(writesTo(again, '/api/accounts')).toEqual([]);
      expect(within(box).queryByRole('button', { name: /again/iu })).toBeNull();
    },
  );

  it('P2 an empty identifier sends nothing; a duplicate is refused with the server reason and kept', async () => {
    const page = await openProfiles();
    const save = within(form('create')).getByRole('button', { name: 'Create account' });
    expect(save).toHaveAttribute('aria-disabled', 'true');
    expect(save).toHaveAccessibleDescription('Enter an identifier');
    await page.user.click(save);
    await page.advance(0);
    expect(page.fake.writes()).toEqual([]);
    await create(page, 'alpha');
    const duplicate = pages('accounts_write.create_a_duplicate_id');
    expect(writesTo(page, '/api/accounts')).toEqual([reviewed(duplicate.request.body)]);
    page.fake.next('/api/accounts', 'POST').respond(duplicate.status, duplicate.body);
    await page.advance(1_000);
    expect(region('profilesAccount')).toHaveTextContent(
      'An account with that identifier already exists',
    );
    expect(within(form('create')).getByRole('textbox', { name: /^Identifier/u })).toHaveValue(
      'alpha',
    );
  });
});

describe('P3 the highest unlocked tier', () => {
  const tierInput = () =>
    within(region('profilesTier')).getByRole('textbox', { name: /^Highest unlocked tier/u });
  const tierSave = () =>
    within(region('profilesTier')).getByRole('button', { name: 'Save highest tier' });

  it.each([
    ['', 'Enter a tier'],
    ['0', 'Use a tier from 1 to 20'],
    ['21', 'Use a tier from 1 to 20'],
    ['1.5', 'Use a whole number, like 12'],
    ['abc', 'Use a whole number, like 12'],
  ])(
    'P3 %j stays as typed with its reason; Save is disabled and nothing is sent',
    async (typed, reason) => {
      const page = await openProfiles();
      await page.user.clear(tierInput());
      if (typed !== '') await page.user.type(tierInput(), typed);
      expect(tierInput()).toHaveValue(typed);
      expect(tierInput()).toHaveAttribute('aria-invalid', 'true');
      expect(tierInput()).toHaveAccessibleDescription(expect.stringContaining(reason));
      expect(tierSave()).toHaveAttribute('aria-disabled', 'true');
      await page.user.click(tierSave());
      await page.advance(0);
      expect(page.fake.writes()).toEqual([]);
    },
  );

  it('P3 a valid tier sends the recorded body once; the reads show it saved as entered by the operator', async () => {
    let accounts = () => pages('accounts_write.read_before_the_tier');
    let source = () => pages('profile_reads.profile_src_valid');
    const page = await openProfiles({ accounts: () => accounts(), source: () => source() });
    await page.user.clear(tierInput());
    await page.user.type(tierInput(), '12');
    await page.user.click(tierSave());
    await page.advance(0);
    const recorded = pages('accounts_write.tier_saved_by_the_operator');
    expect(writesTo(page, '/api/account-tier')).toEqual([reviewed(recorded.request.body)]);
    accounts = () => pages('accounts_write.read_after_the_tier');
    source = () =>
      pages('accounts_write.profile_source_after_the_tier_records_the_operator_as_provenance');
    page.fake.next('/api/account-tier', 'POST').respond(recorded.status, recorded.body);
    await page.advance(1_000);
    const saved = region('profilesTier').querySelector('[data-tier-saved="true"]');
    expect(saved).toHaveTextContent('12');
    expect(saved).toHaveTextContent('entered by you, not read from the game');
    expect(within(region('profilesTier')).getByText('Saved')).toBeVisible();
    await page.advance(60_000);
    expect(writesTo(page, '/api/account-tier')).toHaveLength(1);
  });

  it('P1 P3 SR-6 one Profiles request per emulator: a second click while the first is still at its click-time config read sends nothing and says why; the first sends once', async () => {
    let hold = false;
    const page = await openProfiles(
      {},
      {
        before: (fake) => {
          fake.route('GET', '/api/config', () =>
            hold ? 'hold' : answerOf(pages('config_state.read_before_the_save')),
          );
        },
      },
    );
    await page.user.clear(tierInput());
    await page.user.type(tierInput(), '12');
    hold = true;
    await page.user.click(tierSave());
    await page.advance(0);
    const box = () => region('profilesAccount');
    await page.user.selectOptions(
      within(box()).getByRole('combobox', { name: 'Local game account' }),
      'alpha',
    );
    await page.user.click(within(box()).getByRole('button', { name: 'Use this account' }));
    await page.advance(0);
    // The tier typed above is still unsaved: the switch asks first, then is not sent.
    await page.user.click(
      within(box()).getByRole('button', { name: 'Switch and keep them there' }),
    );
    await page.advance(0);
    expect(document.querySelector('[data-not-sent]')).toHaveAttribute(
      'data-not-sent',
      'profile_write_in_flight',
    );
    page.fake
      .next('/api/config', 'GET')
      .respond(200, pages('config_state.read_before_the_save').body);
    await page.advance(0);
    expect(writesTo(page, '/api/account-tier')).toEqual([
      reviewed(pages('accounts_write.tier_saved_by_the_operator').request.body),
    ]);
    expect(writesTo(page, '/api/accounts')).toEqual([]);
  });

  it('P3 SR-7 F6P-01 a config read already in flight at the click is not the click-time check: the read started after the click names another account and nothing is sent', async () => {
    let config: () => DashboardCase | 'hold' = () => pages('config_state.read_before_the_save');
    const page = await openProfiles(
      {},
      {
        before: (fake) => {
          fake.route('GET', '/api/config', () => {
            const found = config();
            return found === 'hold' ? 'hold' : answerOf(found);
          });
        },
      },
    );
    await page.user.clear(tierInput());
    await page.user.type(tierInput(), '12');
    // A read of the page's own, started before the click, is held with the old binding.
    config = () => 'hold';
    page.data.poller.read('config');
    await page.advance(0);
    // Meanwhile the server binds another account; the click's own read sees it.
    config = () => pages('config_state.effective_view_of_the_other_account');
    await page.user.click(tierSave());
    await page.advance(0);
    page.fake
      .next('/api/config', 'GET')
      .respond(200, pages('config_state.read_before_the_save').body);
    await page.advance(1_000);
    expect(writesTo(page, '/api/account-tier')).toEqual([]);
    expect(document.querySelector('[data-not-sent]')).toHaveAttribute(
      'data-not-sent',
      'context_changed',
    );
  });

  it.each([
    [
      'an HTTP 200 body without the emulator (a recorded read with active_instance removed)',
      'no_instance',
    ],
    ['config_state.file_corrupt', 'recorded'],
  ])(
    'P3 SR-7 F6P-02 a click-time config read that names no context (%s) sends nothing: Not sent, context unconfirmed',
    async (path, kind) => {
      let config = (): DashboardCase => pages('config_state.read_before_the_save');
      const page = await openProfiles({ config: () => config() });
      await page.user.clear(tierInput());
      await page.user.type(tierInput(), '12');
      if (kind === 'no_instance') {
        const found = pages('config_state.read_before_the_save');
        const body = { ...(found.body as Record<string, unknown>) };
        delete body['active_instance'];
        config = () => ({ ...found, body });
      } else {
        config = () => pages(path);
      }
      await page.user.click(tierSave());
      await page.advance(1_000);
      expect(writesTo(page, '/api/account-tier')).toEqual([]);
      expect(document.querySelector('[data-not-sent]')).toHaveAttribute(
        'data-not-sent',
        'context_unconfirmed',
      );
      // The page itself still names the account it showed; nothing was sent for it either.
      expect(page.data.store.getSnapshot().context?.profile).toBe('mine');
    },
  );

  it('P3 SR-4 F6P-03 a tier whose answer is lost is judged only under its own account and profile: a read under another one, whatever its tier, settles nothing and keeps the draft', async () => {
    let config = (): DashboardCase => pages('config_state.read_before_the_save');
    let accounts = (): DashboardCase => pages('accounts_write.read_before_the_tier');
    const page = await openProfiles({ config: () => config(), accounts: () => accounts() });
    const oldContext = page.data.store.getSnapshot().context;
    await page.user.clear(tierInput());
    await page.user.type(tierInput(), '12');
    await page.user.click(tierSave());
    await page.advance(0);
    expect(writesTo(page, '/api/account-tier')).toHaveLength(1);
    // While the answer is on its way the server binds another account and profile
    // (beta/theirs), whose saved tier happens to be 12 too.
    config = () => pages('config_state.effective_view_of_the_other_account');
    accounts = () => pages('accounts_write.read_after_the_tier');
    page.data.poller.read('config');
    await page.advance(0);
    expect(page.data.store.getSnapshot().context?.account).toBe('beta');
    page.fake.next('/api/account-tier', 'POST').lose();
    await page.advance(1_000);
    const state = page.data.store.getSnapshot();
    expect(state.reads.accounts.latest?.outcome.kind).toBe('answered');
    const record = page.data.machine.snapshot().find((entry) => entry.kind === 'account_tier');
    expect(record?.phase).toBe('unconfirmed');
    expect(record?.readSettlement).toBeNull();
    expect(state.readSettlements[record?.id ?? '']).toBeUndefined();
    const scope = `profiles:tier:${contextScopeKey(oldContext)}`;
    expect(state.runDrafts[scope]).toEqual({ max_tier: '12' });
    await page.advance(60_000);
    expect(writesTo(page, '/api/account-tier')).toHaveLength(1);
  });

  it('P3 SR-7 the tier route acts on whatever profile is active: a click whose fresh config read names another account sends nothing', async () => {
    let config = () => pages('config_state.read_before_the_save');
    const page = await openProfiles({ config: () => config() });
    await page.user.clear(tierInput());
    await page.user.type(tierInput(), '12');
    config = () => pages('config_state.effective_view_of_the_other_account');
    await page.user.click(tierSave());
    await page.advance(1_000);
    expect(page.fake.writes()).toEqual([]);
    expect(document.querySelector('[data-not-sent]')).toHaveAttribute(
      'data-not-sent',
      'context_changed',
    );
  });
});

describe('P4 setup state', () => {
  it.each([
    [
      'wizard_tools.not_installed',
      'calibrate_status.restore_pending',
      'runs.not_ready_image_row',
      'Not installed',
      'Original modules are waiting to be restored',
      '1',
    ],
    [
      'wizard_tools.installed',
      'calibrate_status.running_with_steps',
      'runs.ready',
      'Installed',
      'A setup step is running',
      '0',
    ],
  ])(
    'P4 %s, %s, %s stay distinct; the links open their pages and nothing is sent',
    async (tools, calibrate, runs, toolsLine, calibrationLine, notReady) => {
      const page = await openProfiles(
        {},
        {
          before: (fake) => {
            fake.route('GET', '/api/wizard/tools', () => answerOf(dashboardCase(tools)));
            fake.route('GET', '/api/calibrate/status', () => answerOf(dashboardCase(calibrate)));
            fake.route('GET', '/api/runs', () => answerOf(dashboardCase(runs)));
          },
        },
      );
      const box = region('profilesSetup');
      expect(within(box).getByText(toolsLine)).toBeVisible();
      expect(within(box).getByText(calibrationLine)).toBeVisible();
      expect(
        within(box).getByText('Connection tools').closest('.tp-facts__item')?.querySelector('dd'),
      ).toContainElement(within(box).getByText(toolsLine));
      expect(
        within(box).getByText('Setup progress').closest('.tp-facts__item')?.querySelector('dd'),
      ).toContainElement(within(box).getByText(calibrationLine));
      const readiness = box.querySelector('[data-setup="runs"]') as HTMLElement;
      const ready = within(readiness).getByText('Runs ready to start').closest('.tp-facts__item');
      const blocked = within(readiness).getByText('Not ready').closest('.tp-facts__item');
      expect(ready).toBeVisible();
      expect(ready?.querySelector('dd')).toHaveTextContent(/^1$/u);
      expect(blocked).toBeVisible();
      expect(blocked?.querySelector('dd')).toHaveTextContent(notReady);
      expect(within(box).getByRole('link', { name: 'Recognition' })).toHaveAttribute(
        'href',
        '#recognition',
      );
      expect(within(box).getByRole('link', { name: 'Emulators' })).toHaveAttribute(
        'href',
        '#emulators',
      );
      await page.advance(10_000);
      expect(page.fake.writes()).toEqual([]);
    },
  );
});

describe('P5 automation profiles', () => {
  const pickToActivate = async (page: Page, name: string) => {
    await page.user.selectOptions(
      within(form('activate')).getByRole('combobox', { name: 'Profile to activate' }),
      name,
    );
    await page.advance(1_000);
  };
  const activate = () => within(form('activate')).getByRole('button', { name: 'Activate' });

  it('P5 a profile with problems cannot be activated: its check lists them, and nothing is sent', async () => {
    const page = await openProfiles({
      source: () => sourceNamed('profile_reads.profile_src_with_problems', 'theirs'),
    });
    await pickToActivate(page, 'theirs');
    expect(activate()).toHaveAttribute('aria-disabled', 'true');
    expect(activate()).toHaveAccessibleDescription(
      'This profile has problems; fix them before activating it',
    );
    expect(form('activate')).toHaveTextContent('1 problem');
    await page.user.click(activate());
    await page.advance(0);
    expect(page.fake.writes()).toEqual([]);
  });

  it('P5 a scan draft is not offered for activation', async () => {
    const page = await openProfiles();
    await pickToActivate(page, 'mine.draft');
    expect(activate()).toHaveAccessibleDescription(
      'A scan draft is not runnable; save it as a profile first',
    );
    expect(page.fake.writes()).toEqual([]);
  });

  it("P5 a checked profile is activated with the recorded body after a fresh check; another account's profile is refused by the server and said so", async () => {
    const page = await openProfiles({
      source: () => sourceNamed('profile_reads.profile_src_valid', 'theirs'),
    });
    await pickToActivate(page, 'theirs');
    expect(form('activate')).toHaveTextContent('No problems found');
    const sourceReads = () =>
      page.fake.requests.filter((r) => r.path === '/api/profile-src/theirs').length;
    const before = sourceReads();
    await page.user.click(activate());
    await page.advance(0);
    expect(sourceReads()).toBeGreaterThan(before);
    const refused = pages('profile_files_write.activate_another_accounts_profile_is_refused');
    expect(writesTo(page, '/api/profile-activate')).toEqual([reviewed(refused.request.body)]);
    page.fake.next('/api/profile-activate', 'POST').respond(refused.status, refused.body);
    await page.advance(1_000);
    expect(form('activate')).toHaveTextContent('This profile belongs to another account');
    expect(within(form('activate')).getByText('Not done')).toBeVisible();
  });

  it('P5 copying sends the recorded body once and does not activate; a listed name needs the explicit Replace tick', async () => {
    const page = await openProfiles();
    const box = () => form('copy');
    await page.user.selectOptions(
      within(box()).getByRole('combobox', { name: 'Copy from' }),
      'mine',
    );
    await page.user.type(
      within(box()).getByRole('textbox', { name: 'New profile name' }),
      'theirs',
    );
    const copyButton = () => within(box()).getByRole('button', { name: 'Copy' });
    expect(copyButton()).toHaveAccessibleDescription(
      'A profile with this name exists; tick Replace to overwrite it',
    );
    await page.user.clear(within(box()).getByRole('textbox', { name: 'New profile name' }));
    await page.user.type(within(box()).getByRole('textbox', { name: 'New profile name' }), 'spare');
    await page.user.click(copyButton());
    await page.advance(0);
    const recorded = pages('profile_files_write.copy_one_of_your_own_profiles');
    expect(writesTo(page, '/api/profile-copy')).toEqual([reviewed(recorded.request.body)]);
    page.fake.next('/api/profile-copy', 'POST').respond(recorded.status, recorded.body);
    await page.advance(1_000);
    expect(within(region('profilesFiles')).getByText('Copied - not active')).toBeVisible();
    expect(region('profilesFiles').querySelector('[data-active-profile="true"]')).toHaveTextContent(
      'mine',
    );
    await page.advance(60_000);
    expect(writesTo(page, '/api/profile-copy')).toHaveLength(1);
  });

  it.each(['copy', 'promote'] as const)(
    'P5 F6P-04 a %s onto the active profile is refused before any send, even with Replace ticked',
    async (which) => {
      const page = await openProfiles();
      const box = () => form(which);
      const name = which === 'copy' ? 'New profile name' : 'Profile name';
      await page.user.type(within(box()).getByRole('textbox', { name }), 'mine');
      const replace = within(box()).queryByRole('checkbox');
      if (replace !== null) await page.user.click(replace);
      const submit = within(box()).getByRole('button', {
        name: which === 'copy' ? 'Copy' : 'Save as profile',
      });
      expect(submit).toHaveAttribute('aria-disabled', 'true');
      expect(submit).toHaveAccessibleDescription('This is the active profile; choose another name');
      await page.user.click(submit);
      await page.advance(1_000);
      expect(page.fake.writes()).toEqual([]);
    },
  );

  it('P5 F6P-04 the click-time read is the last word: a copy onto the profile it names active is not sent', async () => {
    // The list read says "second" is active; the click-time config names "mine" active.
    const page = await openProfiles({
      files: () => pages('profile_files_write.files_after_the_activation_name_the_active_profile'),
    });
    const box = () => form('copy');
    await page.user.type(within(box()).getByRole('textbox', { name: 'New profile name' }), 'mine');
    await page.user.click(within(box()).getByRole('checkbox'));
    await page.user.click(within(box()).getByRole('button', { name: 'Copy' }));
    await page.advance(1_000);
    expect(writesTo(page, '/api/profile-copy')).toEqual([]);
    expect(document.querySelector('[data-not-sent]')).toHaveAttribute(
      'data-not-sent',
      'destination_active',
    );
  });

  it('P5 F6P-05 a Replace tick is consent for one source and destination: changing either asks again, and only then is overwrite sent', async () => {
    const page = await openProfiles();
    const box = () => form('copy');
    const copyButton = () => within(box()).getByRole('button', { name: 'Copy' });
    await page.user.selectOptions(
      within(box()).getByRole('combobox', { name: 'Copy from' }),
      'mine',
    );
    await page.user.type(
      within(box()).getByRole('textbox', { name: 'New profile name' }),
      'theirs',
    );
    await page.user.click(within(box()).getByRole('checkbox'));
    expect(copyButton()).not.toHaveAttribute('aria-disabled', 'true');
    // Another source: the tick does not carry over.
    await page.user.selectOptions(
      within(box()).getByRole('combobox', { name: 'Copy from' }),
      'default',
    );
    expect(within(box()).getByRole('checkbox')).not.toBeChecked();
    expect(copyButton()).toHaveAccessibleDescription(
      'A profile with this name exists; tick Replace to overwrite it',
    );
    // Back to the reviewed pair, a cleared and retyped name also asks again.
    await page.user.selectOptions(
      within(box()).getByRole('combobox', { name: 'Copy from' }),
      'mine',
    );
    await page.user.clear(within(box()).getByRole('textbox', { name: 'New profile name' }));
    await page.user.type(
      within(box()).getByRole('textbox', { name: 'New profile name' }),
      'theirs',
    );
    expect(within(box()).getByRole('checkbox')).toBeChecked();
    await page.user.selectOptions(
      within(box()).getByRole('combobox', { name: 'Copy from' }),
      'default',
    );
    await page.user.click(copyButton());
    await page.advance(0);
    expect(page.fake.writes()).toEqual([]);
    await page.user.click(within(box()).getByRole('checkbox'));
    await page.user.click(copyButton());
    await page.advance(0);
    expect(writesTo(page, '/api/profile-copy')).toEqual([
      reviewed({ from: 'default', name: 'theirs', overwrite: true }),
    ]);
  });

  it('P5 a scan draft saved with problems is a profile but not active; its problems are shown', async () => {
    let files = () => pages('profile_reads.profile_files_with_a_draft_and_the_starter');
    const page = await openProfiles({ files: () => files() });
    const box = form('promote');
    await page.user.type(within(box).getByRole('textbox', { name: 'Profile name' }), 'promoted');
    await page.user.click(within(box).getByRole('button', { name: 'Save as profile' }));
    await page.advance(0);
    const recorded = pages(
      'profile_files_write.promotion_returns_the_problems_it_does_not_enforce_them',
    );
    expect(writesTo(page, '/api/profile-promote')).toEqual([
      reviewed({ draft: 'mine.draft.yaml', name: 'promoted', base: 'default' }),
    ]);
    files = () => pages('profile_files_write.files_after_the_promotion_activate_nothing');
    page.fake.next('/api/profile-promote', 'POST').respond(recorded.status, recorded.body);
    await page.advance(1_000);
    const files_ = region('profilesFiles');
    expect(within(files_).getByText('Saved as a profile - not active')).toBeVisible();
    expect(files_).toHaveTextContent('Saved with 3 problems; fix them before activating it');
    expect(files_).toHaveTextContent('Abilities not verified');
    expect(files_.querySelector('[data-profile="promoted"]')).toHaveAttribute(
      'data-active',
      'false',
    );
  });

  it('P5 SR-4 F6P-06 a copy whose connection fails reads the profiles again by itself, never resends, and the read listing the new name settles it', async () => {
    let files = () => pages('profile_reads.profile_files_with_a_draft_and_the_starter');
    const page = await openProfiles({ files: () => files() });
    const box = form('copy');
    await page.user.type(within(box).getByRole('textbox', { name: 'New profile name' }), 'fresh');
    await page.user.click(within(box).getByRole('button', { name: 'Copy' }));
    await page.advance(0);
    const fileReads = () =>
      page.fake.requests.filter((r) => r.path === '/api/profile-files').length;
    const before = fileReads();
    // The server copied; the answer is lost on the way. Nothing here asks for a read.
    files = () =>
      pages('profile_files_write.files_after_the_copy_list_it_and_copy_activates_nothing');
    page.fake.next('/api/profile-copy', 'POST').lose();
    await page.advance(1_000);
    expect(fileReads()).toBeGreaterThan(before);
    expect(
      within(region('profilesFiles')).getByText('A later read shows the requested result'),
    ).toBeVisible();
    await page.advance(60_000);
    expect(writesTo(page, '/api/profile-copy')).toHaveLength(1);
  });

  it('P5 SR-4 a copy whose answer is lost stays Unknown while the reads cannot tell, and is never resent', async () => {
    let files: () => DashboardCase | 'hold' = () =>
      pages('profile_reads.profile_files_with_a_draft_and_the_starter');
    const page = await openProfiles(
      {},
      {
        before: (fake) => {
          fake.route('GET', '/api/profile-files', () => {
            const found = files();
            return found === 'hold' ? 'hold' : answerOf(found);
          });
        },
      },
    );
    const box = form('copy');
    await page.user.type(within(box).getByRole('textbox', { name: 'New profile name' }), 'fresh');
    await page.user.click(within(box).getByRole('button', { name: 'Copy' }));
    await page.advance(0);
    files = () => 'hold';
    page.fake.next('/api/profile-copy', 'POST').lose();
    await page.advance(1_000);
    expect(
      within(region('profilesFiles')).getByText(
        'No confirmed reply - it may or may not have been done',
      ),
    ).toBeVisible();
    await page.advance(60_000);
    expect(writesTo(page, '/api/profile-copy')).toHaveLength(1);
  });

  it('P5 has no axe violations and only catalogue or server text', async () => {
    await openProfiles();
    vi.useRealTimers();
    expect(await axe(document.body)).toHaveNoViolations();
    expect(textOutsideCatalogue(document.body)).toEqual([]);
  });
});

describe('F6-14 PE1 use no profile (landed profile_target, brief f6-14-profiles-brief.md)', () => {
  const target = (name: string) => dashboardPagesCase(`profile_target.${name}`);
  // The synthetic world of profile_target: Alpha on main/fake:1 with mine active, second inactive.
  const world: Reads = {
    config: () => target('inactive_editor_context_before_save'),
    files: () => target('same_account_inactive_target_listed'),
  };
  const unbindForm = () => {
    const found = document.querySelector<HTMLElement>('[data-form="unbind"]');
    if (found === null) throw new Error('unbind form missing');
    return found;
  };
  const useNone = () => within(unbindForm()).getByRole('button', { name: 'Use no profile' });
  const openDialog = () => {
    const found = document.querySelector<HTMLElement>('dialog[open]');
    if (found === null) throw new Error('no open dialog');
    return found;
  };
  async function confirmUnbind(page: Page) {
    await page.user.click(useNone());
    await page.user.click(within(openDialog()).getByRole('button', { name: 'Use no profile' }));
    await page.advance(0);
  }
  async function answer(page: Page, name: string) {
    const found = target(name);
    page.fake.next('/api/profile-activate', 'POST').respond(found.status, found.body);
    await page.advance(1_000);
  }

  it.each(['hold', 'fail', 'contradict'] as const)(
    'PE1 C-01 a 200 unbind is accepted while config reads %s; only D9 proof completes it',
    async (mode) => {
      const page = await openProfiles(world);
      await confirmUnbind(page);
      page.fake.route('GET', '/api/config', () =>
        mode === 'contradict' ? answerOf(target('inactive_editor_context_before_save')) : 'hold',
      );
      await answer(page, 'unbind_with_matching_account_context');
      if (mode === 'fail') {
        page.fake.next('/api/config', 'GET').lose();
        await page.advance(0);
      }
      expect(unbindForm()).toHaveTextContent('Accepted, waiting for the configuration to confirm');
      expect(unbindForm()).not.toHaveTextContent('No profile is active -');
      expect(page.data.machine.snapshot().at(-1)?.readSettlement).toBeNull();
      const proof = target('read_after_unbind_account_context');
      page.fake.route('GET', '/api/config', () => answerOf(proof));
      if (mode === 'hold') page.fake.next('/api/config', 'GET').respond(proof.status, proof.body);
      else page.data.poller.read('config');
      await page.advance(1_000);
      expect(unbindForm()).toHaveTextContent('No profile is active -');
      expect(page.data.machine.snapshot().at(-1)?.readSettlement).toBe('applied');
      expect(writesTo(page, '/api/profile-activate')).toHaveLength(1);
    },
  );

  it.each(['unbind_failed_before_write', 'lost', 'unbind_with_matching_account_context'])(
    'PE1 C-02 %s blocks a second confirmation and reload until D9 proof',
    async (receipt) => {
      const page = await openProfiles(world);
      const command = selectProfilesPage(page.data.store.getSnapshot()).profiles.unbind?.command
        .command;
      if (command == null) throw new Error('no unbind command');
      await confirmUnbind(page);
      if (receipt === 'lost') {
        page.fake.next('/api/profile-activate', 'POST').lose();
        await page.advance(1_000);
      } else await answer(page, receipt);
      const reason = 'The previous unbind is unresolved; waiting for the configuration to confirm';
      expect(useNone()).toHaveAttribute('aria-disabled', 'true');
      expect(useNone()).toHaveAccessibleDescription(reason);
      expect(within(unbindForm()).getByText(reason)).toBeVisible();
      await page.user.click(useNone());
      expect(document.querySelector('dialog[open]')).toBeNull();
      // Reuse the reviewed confirm command to exercise the send boundary independently of the UI.
      await page.data.commands.profileWrite(command);
      await page.advance(0);
      expect(writesTo(page, '/api/profile-activate')).toHaveLength(1);
      page.view.unmount();
      page.data.stop();
      let config = () => target('inactive_editor_context_before_save');
      const again = await openProfiles(
        { ...world, config: () => config() },
        { storage: page.storage },
      );
      const restored = selectProfilesPage(again.data.store.getSnapshot()).profiles.unbind?.outcome;
      expect(restored?.restored).toBe(true);
      expect(useNone()).toHaveAccessibleDescription(reason);
      const current = again.data.store.getSnapshot();
      if (current.context === null) throw new Error('no current context');
      await again.data.commands.profileWrite({
        ...command,
        epoch: current.epoch,
        context: current.context,
      });
      await again.advance(0);
      expect(writesTo(again, '/api/profile-activate')).toHaveLength(0);
      config = () => target('read_after_unbind_account_context');
      again.data.poller.read('config');
      await again.advance(1_000);
      expect(useNone()).toHaveAccessibleDescription('No profile is active');
      expect(unbindForm()).not.toHaveTextContent(reason);
      expect(unbindForm()).toHaveTextContent('No profile is active -');
      // A subsequent recorded active context offers the action again: the old journal no longer blocks it.
      config = () => target('inactive_editor_context_before_save');
      again.data.poller.read('config');
      await again.advance(1_000);
      expect(useNone()).not.toHaveAttribute('aria-disabled', 'true');
      expect(
        writesTo(page, '/api/profile-activate').length +
          writesTo(again, '/api/profile-activate').length,
      ).toBe(1);
    },
  );

  it.each([undefined, null, '', 7])(
    'PE1 C-01 accepted unbind rejects a config_revision of %s',
    async (revision) => {
      let config = () => target('inactive_editor_context_before_save');
      const page = await openProfiles({ ...world, config: () => config() });
      await confirmUnbind(page);
      // Modified recorded case: only the config revision is malformed.
      const proof = target('read_after_unbind_account_context');
      config = () => ({ ...proof, body: { ...(proof.body as object), config_revision: revision } });
      await answer(page, 'unbind_with_matching_account_context');
      expect(unbindForm()).toHaveTextContent('Accepted, waiting for the configuration to confirm');
      expect(page.data.machine.snapshot().at(-1)?.readSettlement).toBeNull();
      config = () => proof;
      page.data.poller.read('config');
      await page.advance(1_000);
      expect(unbindForm()).toHaveTextContent('No profile is active -');
    },
  );

  it('PE1 C-01 a config read started before the receipt cannot verify an accepted unbind', async () => {
    const page = await openProfiles(world);
    await confirmUnbind(page);
    page.fake.route('GET', '/api/config', () => 'hold');
    page.data.poller.read('config');
    await page.advance(0);
    const early = page.fake.next('/api/config', 'GET');
    await answer(page, 'unbind_with_matching_account_context');
    const proof = target('read_after_unbind_account_context');
    early.respond(proof.status, proof.body);
    await page.advance(0);
    expect(unbindForm()).toHaveTextContent('Accepted, waiting for the configuration to confirm');
    expect(page.data.machine.snapshot().at(-1)?.readSettlement).toBeNull();
    page.fake.route('GET', '/api/config', () => answerOf(proof));
    page.fake.last('/api/config', 'GET').respond(proof.status, proof.body);
    page.data.poller.read('config');
    await page.advance(1_000);
    expect(unbindForm()).toHaveTextContent('No profile is active -');
  });

  it('PE1 offered beside Activate while a profile is active; Cancel sends nothing; a confirmed unbind sends the recorded body once', async () => {
    let config = () => target('inactive_editor_context_before_save');
    const page = await openProfiles({ ...world, config: () => config() });
    expect(unbindForm()).toHaveTextContent('Active profile:mine');
    expect(
      within(unbindForm()).getByText(
        'Unbinds the active profile; automation uses legacy rules from its next start. Nothing starts or stops now.',
      ),
    ).toBeVisible();
    await page.user.click(useNone());
    expect(within(openDialog()).getByText(/legacy rules/u)).toBeVisible();
    await page.user.click(within(openDialog()).getByRole('button', { name: 'Cancel' }));
    await page.advance(1_000);
    expect(writesTo(page, '/api/profile-activate')).toEqual([]);
    await confirmUnbind(page);
    expect(writesTo(page, '/api/profile-activate')).toEqual([
      target('unbind_with_matching_account_context').request.body,
    ]);
    await answer(page, 'unbind_with_matching_account_context');
    expect(unbindForm()).toHaveTextContent('Accepted, waiting for the configuration to confirm');
    config = () => target('read_after_unbind_account_context');
    page.data.poller.read('config');
    await page.advance(1_000);
    expect(unbindForm()).toHaveTextContent(
      'No profile is active - the scheduler runs its legacy rules from its next start',
    );
    // Never an empty option of the Activate picker; the Activate line is not this one's.
    expect(form('activate')).not.toHaveTextContent('No profile is active');
    await page.advance(30_000);
    expect(writesTo(page, '/api/profile-activate')).toHaveLength(1);
  });

  it('PE1 a runner owning the device refuses the unbind with its reason; nothing is resent', async () => {
    let config = () => target('inactive_editor_context_before_save');
    const page = await openProfiles({ ...world, config: () => config() });
    await confirmUnbind(page);
    config = () => target('read_after_unbind_runner_refusal');
    await answer(page, 'unbind_refused_while_runner_listed');
    expect(within(unbindForm()).getByText('Not done')).toBeVisible();
    expect(unbindForm()).toHaveTextContent('runners alive - stop them first');
    await page.advance(30_000);
    expect(writesTo(page, '/api/profile-activate')).toHaveLength(1);
  });

  it.each([
    ['unbind_refused_after_account_switch', ['main', 'beta', 'theirs', 'fake:1']],
    ['unbind_refused_after_active_profile_switch', ['main', 'alpha', 'second', 'fake:1']],
  ])('PE1 %s: not saved, the current context is named, nothing resent', async (name, current) => {
    const page = await openProfiles(world);
    await confirmUnbind(page);
    await answer(page, name);
    expect(
      within(unbindForm()).getByText('Not saved: the account or profile changed'),
    ).toBeVisible();
    const shown = unbindForm().querySelector('[data-current-context="true"]');
    for (const value of current) expect(shown).toHaveTextContent(value);
    expect(writesTo(page, '/api/profile-activate')).toHaveLength(1);
  });

  it.each([
    ['unbind_failed_before_write', 'read_after_unbind_failed_before_write', false],
    ['unbind_written_then_answer_failed', 'read_after_unbind_written_then_answer_failed', true],
  ])(
    'PE1 %s + %s: only the later config can settle; never resent',
    async (write, readback, applied) => {
      let config = () => target('inactive_editor_context_before_save');
      const page = await openProfiles({ ...world, config: () => config() });
      await confirmUnbind(page);
      await answer(page, write);
      expect(within(unbindForm()).getByText(/^No confirmed reply/u)).toBeVisible();
      // Supporting file-list evidence never settles an unbind, even when it says none active.
      page.fake.route('GET', '/api/profile-files', () =>
        answerOf(pages('profile_files_write.files_after_unbinding_have_no_active_profile')),
      );
      page.data.poller.read('profileFiles');
      await page.advance(1_000);
      expect(within(unbindForm()).getByText(/^No confirmed reply/u)).toBeVisible();
      config = () => target(readback);
      page.data.poller.read('config');
      await page.advance(1_000);
      expect(
        page.data.machine.snapshot().find((entry) => entry.kind === 'profile_activate')
          ?.readSettlement,
      ).toBe(applied ? 'applied' : null);
      if (applied) {
        expect(
          within(unbindForm()).getByText(
            'No profile is active - the scheduler runs its legacy rules from its next start',
          ),
        ).toBeVisible();
        expect(useNone()).toHaveAccessibleDescription('No profile is active');
      } else expect(within(unbindForm()).getByText(/^No confirmed reply/u)).toBeVisible();
      await page.advance(30_000);
      expect(writesTo(page, '/api/profile-activate')).toHaveLength(1);
    },
  );

  it.each([
    ['unbind_with_matching_account_context', 'read_after_unbind_account_context', 'alpha'],
    [
      'unbind_with_matching_legacy_context',
      'read_after_unbind_legacy_context',
      'this installation',
    ],
  ])(
    'PE1 %s + %s: reviewed identity, lost receipt and fresh config settlement',
    async (write, readback, account) => {
      let config = () =>
        write === 'unbind_with_matching_legacy_context'
          ? pages('config_state.read_before_the_save')
          : target('inactive_editor_context_before_save');
      const page = await openProfiles({ ...world, config: () => config() });
      await page.user.click(useNone());
      expect(openDialog()).toHaveTextContent(account);
      expect(openDialog()).toHaveTextContent('main');
      expect(openDialog()).toHaveTextContent('mine');
      await page.user.click(within(openDialog()).getByRole('button', { name: 'Use no profile' }));
      await page.advance(0);
      expect(writesTo(page, '/api/profile-activate')).toEqual([target(write).request.body]);
      // The recorded successful request committed; transport loses its reply.
      config = () => target(readback);
      page.fake.next('/api/profile-activate', 'POST').lose();
      await page.advance(1_000);
      expect(
        within(unbindForm()).getByText(
          'No profile is active - the scheduler runs its legacy rules from its next start',
        ),
      ).toBeVisible();
      await page.advance(30_000);
      expect(writesTo(page, '/api/profile-activate')).toHaveLength(1);
    },
  );

  it('PE1 the unbind and its confirmation use catalogue words and pass axe', async () => {
    const page = await openProfiles(world);
    await page.user.click(useNone());
    expect(textOutsideCatalogue(page.view.container)).toEqual([]);
    vi.useRealTimers();
    expect(await axe(page.view.container)).toHaveNoViolations();
  });
});

describe('F6-14 PE2 editing target foundation', () => {
  const target = (name: string) => pages(`profile_target.${name}`);
  const world: Reads = {
    config: () => target('inactive_editor_context_before_save'),
    files: () => target('same_account_inactive_target_listed'),
  };
  const box = () => form('editing-target');
  async function pickSecond(page: Page) {
    await page.user.selectOptions(within(box()).getByRole('combobox'), 'second');
    await page.user.click(within(box()).getByRole('button', { name: 'Edit this profile' }));
  }
  const returnButton = () =>
    within(box()).getByRole('button', { name: 'Return editing to the active profile' });

  it('PE1 F6-14c failed profile list keeps second and offers a read-only retry', async () => {
    let files = target('same_account_inactive_target_listed');
    const page = await openProfiles({ ...world, files: () => files });
    await pickSecond(page);
    files = pages('profile_target_more.profile_files_read_failed_500');
    page.data.poller.read('profileFiles');
    await page.advance(0);
    expect(selectEditingTarget(page.data.store.getSnapshot()).name).toBe('second');
    expect(
      within(box()).getByText('The profile list could not be read. Your editing profile is kept.'),
    ).toBeVisible();
    files = pages('profile_target_more.profile_files_read_after_the_failure_answers_again');
    await page.user.click(within(box()).getByRole('button', { name: 'Retry profile list' }));
    await page.advance(0);
    expect(selectEditingTarget(page.data.store.getSnapshot())).toMatchObject({
      name: 'second',
      blocked: null,
    });
    expect(page.fake.writes()).toEqual([]);
  });

  it('PE2 pick second while mine is active sends no request of any kind; generation increases on pick and return', async () => {
    const page = await openProfiles(world);
    const before = page.fake.requests.length;
    const generation = page.data.store.getSnapshot().editTargetGeneration;
    await pickSecond(page);
    expect(page.fake.requests).toHaveLength(before);
    expect(selectEditingTarget(page.data.store.getSnapshot())).toMatchObject({
      name: 'second',
      active: 'mine',
      pinned: true,
      inactive: true,
      blocked: null,
      generation: generation + 1,
    });
    expect(box()).toHaveTextContent('Editing:second(not the active profile)');
    await page.user.click(returnButton());
    expect(page.fake.requests).toHaveLength(before);
    expect(selectEditingTarget(page.data.store.getSnapshot())).toMatchObject({
      name: 'mine',
      pinned: false,
      generation: generation + 2,
    });
  });

  it('PE2 the pick persists per account identity and reload restores permission only after the files read lists it', async () => {
    const page = await openProfiles(world);
    await pickSecond(page);
    page.view.unmount();
    page.data.stop();
    let files: DashboardCase | 'hold' = 'hold';
    const again = await openProfiles(world, {
      storage: page.storage,
      before: (fake) => {
        fake.route('GET', '/api/profile-files', () =>
          files === 'hold' ? 'hold' : answerOf(files),
        );
      },
    });
    expect(selectEditingTarget(again.data.store.getSnapshot())).toMatchObject({
      name: 'second',
      pinned: true,
      blocked: 'files_unread',
    });
    expect(editorSource(again.data.store.getSnapshot())).toBeNull();
    files = target('same_account_inactive_target_listed');
    again.fake.next('/api/profile-files').respond(files.status, files.body);
    await again.advance(0);
    expect(selectEditingTarget(again.data.store.getSnapshot())).toMatchObject({
      name: 'second',
      blocked: null,
    });
    expect(again.fake.writes()).toEqual([]);
  });

  it('PE2 inactive_target_list_after_deletion stays pinned and blocked not_listed without fallback', async () => {
    let files = () => target('same_account_inactive_target_listed');
    const page = await openProfiles({ ...world, files: () => files() });
    await pickSecond(page);
    files = () => target('inactive_target_list_after_deletion');
    page.data.poller.read('profileFiles');
    await page.advance(0);
    expect(selectEditingTarget(page.data.store.getSnapshot())).toMatchObject({
      name: 'second',
      active: 'mine',
      pinned: true,
      blocked: 'not_listed',
    });
    expect(box()).toHaveTextContent('This profile is no longer listed');
    expect(page.fake.writes()).toEqual([]);
  });

  it('PE2 profile_files_with_a_draft_and_the_starter never offers starter or draft', async () => {
    await openProfiles({
      ...world,
      files: () => pages('profile_reads.profile_files_with_a_draft_and_the_starter'),
    });
    const options = within(box()).getAllByRole('option');
    expect(options.map((option) => option.getAttribute('value'))).not.toContain('default');
    expect(options.map((option) => option.getAttribute('value'))).not.toContain('mine.draft');
  });

  it('PE2 another account identity does not see the pick; return clears only this identity', async () => {
    let config = () => target('inactive_editor_context_before_save');
    const page = await openProfiles({ ...world, config: () => config() });
    await pickSecond(page);
    const context = page.data.store.getSnapshot().context;
    if (context === null) throw new Error('missing context');
    const alphaKey = editTargetKey(context);
    config = () => pages('config_state.effective_view_of_the_other_account');
    page.data.poller.read('config');
    await page.advance(0);
    expect(selectEditingTarget(page.data.store.getSnapshot())).toMatchObject({
      name: 'theirs',
      pinned: false,
    });
    await page.user.click(returnButton());
    expect(page.data.store.getSnapshot().editTargets[alphaKey]?.name).toBe('second');
    config = () => target('inactive_editor_context_before_save');
    page.data.poller.read('config');
    await page.advance(0);
    expect(selectEditingTarget(page.data.store.getSnapshot()).name).toBe('second');
    await page.user.click(returnButton());
    expect(page.data.store.getSnapshot().editTargets[alphaKey]).toBeUndefined();
    expect(page.fake.writes()).toEqual([]);
  });

  it('PE2 targetSource reads /api/profile-src/second; editorSource accepts only a body named second', async () => {
    const page = await openProfiles(world);
    await pickSecond(page);
    expect(selectReadPlan(page.data.store.getSnapshot(), 'targetSource').path).toBeNull();
    // An editor opens. The initial recorded mine body deliberately fails the name fence.
    page.data.commands.openCollection(true);
    await page.advance(0);
    expect(page.fake.requests.some((request) => request.path === '/api/profile-src/second')).toBe(
      true,
    );
    expect(selectReadPlan(page.data.store.getSnapshot(), 'profileSource').path).toBe(
      '/api/profile-src/mine',
    );
    expect(editorSource(page.data.store.getSnapshot())).toBeNull();
    page.fake.route('GET', '/api/profile-src/second', () =>
      answerOf(target('same_account_inactive_target_source')),
    );
    page.data.poller.read('targetSource');
    await page.advance(0);
    const read = editorSource(page.data.store.getSnapshot());
    expect(read?.path).toBe('/api/profile-src/second');
    expect(read?.outcome.kind === 'answered' ? read.outcome.body : null).toEqual(
      target('same_account_inactive_target_source').body,
    );
    expect(page.fake.writes()).toEqual([]);
  });
});
