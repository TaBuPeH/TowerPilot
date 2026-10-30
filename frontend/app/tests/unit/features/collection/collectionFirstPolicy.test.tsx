import { assertProfilePatchContexts } from '../../../support/profilePatchAssertions.ts';
import { withRecordedFence } from '../../../support/profilePatchFenced.ts';
import { within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import { firstPolicyCase as rawFirstPolicyCase } from '../../../support/collectionFirstPolicy.ts';
import { dashboardPagesCase, type DashboardCase } from '../../../support/dashboardFixture.ts';
import { postBodies } from '../../../support/dataLayer.ts';
import type { KeyValueStorage } from '../../../../src/api/storage.ts';
import { renderPage } from '../dashboard/harness.tsx';

// Each case renders a whole page; under the full parallel suite 5 s is not enough.
vi.setConfig({ testTimeout: 30_000 });

const fenceChecks: (() => void)[] = [];
afterEach(() => {
  fenceChecks.splice(0).forEach((check) => {
    check();
  });
});

const firstPolicyCase = (name: string) =>
  withRecordedFence(rawFirstPolicyCase(name), {
    instance: 'main',
    account: null,
    profile: 'mine',
    serial: 'fake:1',
  });

const EMPTY = 'read_a_profile_with_no_gathering_policies';
const CREATED = 'first_policy_created_from_the_farm_template_default';
const READBACK = 'read_after_the_first_policy_holds_it_unbound';
const TEMPLATES = 'read_the_run_templates_that_carry_the_default_gathering_policies';

const answer = (found: DashboardCase) => ({ status: found.status, body: found.body });

async function open(
  options: {
    readonly source?: () => DashboardCase;
    readonly settings?: () => DashboardCase;
    readonly storage?: KeyValueStorage;
    readonly templates?: () => DashboardCase | 'hold';
  } = {},
) {
  window.location.hash = '#collection';
  const page = await renderPage({
    ...(options.storage === undefined ? {} : { storage: options.storage }),
    before: (fake) => {
      fake.route('GET', '/api/config', () =>
        answer(dashboardPagesCase('config_state.read_before_the_save')),
      );
      fake.route('GET', '/api/settings', () =>
        answer(
          options.settings?.() ??
            dashboardPagesCase('settings.profile_without_gathering_policies_has_no_gather_modules'),
        ),
      );
      fake.route('GET', /^\/api\/profile-src\//u, () =>
        answer(options.source?.() ?? firstPolicyCase(EMPTY)),
      );
      fake.route('GET', '/api/vocab', () => answer(dashboardPagesCase('profile_reads.vocab_ok')));
      fake.route('GET', '/api/run-templates', () => {
        const found = options.templates?.() ?? firstPolicyCase(TEMPLATES);
        return found === 'hold' ? 'hold' : answer(found);
      });
    },
  });
  await page.advance(1_000);
  assertProfilePatchContexts(page.data.machine);
  fenceChecks.push(() => {
    assertProfilePatchContexts(page.data.machine);
  });
  return page;
}

function form() {
  const found = document.querySelector<HTMLElement>('[data-form="create"]');
  if (found === null) throw new Error('Create form missing');
  return found;
}
const idField = () => within(form()).getByRole('textbox', { name: 'New policy identifier' });
const save = () => within(form()).getByRole('button', { name: 'Save policy' });
const patches = (page: Awaited<ReturnType<typeof open>>) =>
  postBodies(page.fake, '/api/profile-patch');

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  window.location.hash = '';
  vi.useRealTimers();
});

it.each([
  [EMPTY, CREATED],
  ['read_a_profile_with_no_gather_key_at_all', 'first_policy_creates_the_missing_gather_map'],
])(
  'C-01 C5 F6C-R1-04 creates once from %s with the exact default and revision, then reads it back unbound',
  async (readName, writeName) => {
    let source = readName;
    const page = await open({ source: () => firstPolicyCase(source) });
    expect(document.body).toHaveTextContent('This profile has no gathering policies');
    expect(form().querySelector('[data-first-policy="unavailable"]')).toBeNull();
    await page.user.type(idField(), 'farm_default');
    const button = save();
    button.focus();
    await page.user.keyboard('{Enter}');
    await page.advance(0);
    expect(patches(page)).toEqual([firstPolicyCase(writeName).request.body]);
    expect(
      page.data.machine.snapshot().filter((entry) => entry.kind === 'profile_patch'),
    ).toHaveLength(1);
    expect(save()).toBe(button);
    expect(button).toHaveFocus();
    source = readName === EMPTY ? READBACK : 'read_after_the_gather_map_was_created';
    const recorded = firstPolicyCase(writeName);
    page.fake.next('/api/profile-patch', 'POST').respond(recorded.status, recorded.body);
    await page.advance(1_000);
    const policy = document.querySelector('[data-policy="farm_default"]');
    expect(policy).toHaveTextContent('farm_default');
    expect(document.querySelector('.tp-collection__editor [data-used-by]')).toHaveTextContent(
      'Not used by any run',
    );
    expect(form().querySelector('[data-outcome="saved"]')).toBeVisible();
    expect(
      within(document.querySelector<HTMLElement>('[data-form="copy"]') ?? document.body).getByRole(
        'combobox',
      ),
    ).toHaveValue('farm_default');
    expect(idField()).toHaveValue('');
    await page.advance(60_000);
    expect(patches(page)).toHaveLength(1);
  },
);

it('C-01 C5 F6C-R1-04 selecting another template sends that exact default without adding a label', async () => {
  const page = await open();
  await page.user.selectOptions(within(form()).getByRole('combobox'), '["shard","gems_only"]');
  await page.user.type(idField(), 'gems_only_copy');
  await page.user.click(save());
  await page.advance(0);
  const recorded = firstPolicyCase('a_second_policy_from_another_template').request.body as Record<
    string,
    unknown
  >;
  const first = firstPolicyCase(CREATED).request.body as Record<string, unknown>;
  // The second-template recording follows a prior binding; this click uses the empty library's revision.
  expect(patches(page)).toEqual([{ ...recorded, expected_revision: first['expected_revision'] }]);
});

it.each([
  ['source', 'farm_default'],
  ['settings', 'all_on'],
])(
  'C-01 C5 F6C-R1-04 a taken id listed by %s is visibly refused before any send',
  async (where, id) => {
    const page = await open({
      source: () => firstPolicyCase(where === 'source' ? READBACK : EMPTY),
      ...(where === 'settings'
        ? { settings: () => dashboardPagesCase('settings.populated_account_every_module') }
        : {}),
    });
    await page.user.type(idField(), id);
    expect(idField()).toHaveAttribute('aria-invalid', 'true');
    expect(idField()).toHaveAccessibleDescription(/A policy with this identifier exists/u);
    expect(save()).toHaveAttribute('aria-disabled', 'true');
    expect(save()).toHaveAccessibleDescription('A policy with this identifier exists');
    await page.user.click(save());
    await page.advance(10_000);
    expect(patches(page)).toEqual([]);
  },
);

it('C-01 C5 F6C-R1-04 Cancel clears the id and selected template without sending', async () => {
  const page = await open();
  await page.user.selectOptions(within(form()).getByRole('combobox'), '["shard","gems_only"]');
  await page.user.type(idField(), 'unsaved');
  await page.user.click(within(form()).getByRole('button', { name: 'Cancel' }));
  expect(idField()).toHaveValue('');
  expect(within(form()).getByRole('combobox')).toHaveValue('["farm","all_on"]');
  expect(patches(page)).toEqual([]);
});

it('C-01 C5 F6C-R1-04 lost answer stays unknown on an unchanged read; only a later positive read settles it without resend', async () => {
  let source = EMPTY;
  const page = await open({ source: () => firstPolicyCase(source) });
  await page.user.type(idField(), 'farm_default');
  await page.user.click(save());
  await page.advance(0);
  page.fake.next('/api/profile-patch', 'POST').lose();
  await page.advance(10_000);
  expect(form().querySelector('[data-outcome="unknown"]')).toBeVisible();
  expect(idField()).toHaveValue('farm_default');
  expect(within(form()).queryByRole('button', { name: 'Dismiss' })).toBeNull();
  expect(patches(page)).toHaveLength(1);
  source = READBACK;
  page.data.commands.checkCollection();
  await page.advance(1_000);
  expect(form().querySelector('[data-outcome="applied"]')).toBeVisible();
  expect(idField()).toHaveValue('');
  expect(document.querySelector('.tp-collection__editor [data-used-by]')).toHaveTextContent(
    'Not used by any run',
  );
  await page.advance(60_000);
  expect(patches(page)).toHaveLength(1);
});

it.each([
  'first_policy_that_is_empty_is_refused',
  'first_policy_with_an_unknown_key_is_refused',
  'first_policy_with_a_stale_revision_is_refused',
])('C-01 C5 F6C-R1-04 %s keeps the draft and never retries', async (name) => {
  const page = await open();
  await page.user.type(idField(), 'farm_default');
  await page.user.click(save());
  await page.advance(0);
  const recorded = firstPolicyCase(name);
  // These recorded refusal bodies exercise receipt handling for this form; the UI sends the valid template.
  page.fake.next('/api/profile-patch', 'POST').respond(recorded.status, recorded.body);
  await page.advance(1_000);
  expect(form().querySelector('[data-outcome="refused"]')).toBeVisible();
  expect(idField()).toHaveValue('farm_default');
  await page.advance(60_000);
  expect(patches(page)).toHaveLength(1);
});

it('C-01 C5 F6C-R1-04 raw id and template survive reload and Cancel discards the persisted draft', async () => {
  const page = await open();
  await page.user.selectOptions(within(form()).getByRole('combobox'), '["shard","gems_only"]');
  await page.user.type(idField(), '007 bad');
  page.view.unmount();
  page.data.stop();
  const again = await open({ storage: page.storage });
  expect(idField()).toHaveValue('007 bad');
  expect(within(form()).getByRole('combobox')).toHaveValue('["shard","gems_only"]');
  expect(patches(again)).toEqual([]);
  await again.user.click(within(form()).getByRole('button', { name: 'Cancel' }));
  again.view.unmount();
  again.data.stop();
  await open({ storage: again.storage });
  expect(idField()).toHaveValue('');
});

it('C-01 C5 F6C-R1-04 unreadable templates block creation, then a later read offers the form; catalogue and axe stay clean', async () => {
  let readable = false;
  const page = await open({ templates: () => (readable ? firstPolicyCase(TEMPLATES) : 'hold') });
  expect(form()).toHaveTextContent('No template gathering defaults can be read');
  expect(save()).toHaveAttribute('aria-disabled', 'true');
  expect(patches(page)).toEqual([]);
  page.fake.next('/api/run-templates', 'GET').lose();
  await page.advance(0);
  readable = true;
  page.data.commands.checkCollection();
  await page.advance(1_000);
  expect(within(form()).getByRole('combobox')).toBeVisible();
  expect(textOutsideCatalogue(document.body)).toEqual([]);
  vi.useRealTimers();
  expect(await axe(document.body)).toHaveNoViolations();
});
