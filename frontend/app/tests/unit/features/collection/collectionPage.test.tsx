import { assertProfilePatchContexts } from '../../../support/profilePatchAssertions.ts';
import { screen, within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import { postBodies } from '../../../support/dataLayer.ts';
import { dashboardPagesCase, type DashboardCase } from '../../../support/dashboardFixture.ts';
import { sample, statusBody } from '../../../support/fixture.ts';
import { renderPage, type PageOptions } from '../dashboard/harness.tsx';

/**
 * The Collection page (backend/logs/reviews/ui-pages/collection-brief.md, C1 to C6) on the
 * whole wired app with a fake transport. Every answer is a recorded case of the pages
 * contract (families settings, collection_policies, profile_reads, config_state). Where a
 * request the page builds differs from a recorded request (another revision, another
 * policy), the test answers with the recorded answer of the same route and kind, and says so.
 */
const fenceChecks: (() => void)[] = [];
afterEach(() => {
  fenceChecks.splice(0).forEach((check) => {
    check();
  });
});

const pages = (path: string) => dashboardPagesCase(path);

function answerOf(found: DashboardCase): { readonly status: number; readonly body: unknown } {
  const body = found.body as Record<string, unknown>;
  return {
    status: found.status,
    body: Object.keys(body).length === 1 && typeof body['text'] === 'string' ? body['text'] : body,
  };
}

type Reads = Partial<
  Record<'config' | 'settings' | 'source' | 'vocab', () => DashboardCase | 'hold'>
>;

async function openCollection(reads: Reads = {}, options: PageOptions = {}, hash = '#collection') {
  window.location.hash = hash;
  const read = (name: keyof Reads, fallback: () => DashboardCase) => () => {
    const found = (reads[name] ?? fallback)();
    return found === 'hold' ? 'hold' : answerOf(found);
  };
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
        '/api/settings',
        read('settings', () => pages('settings.populated_account_every_module')),
      );
      fake.route(
        'GET',
        /^\/api\/profile-src\//u,
        read('source', () => pages('collection_policies.read_before_the_first_save')),
      );
      fake.route(
        'GET',
        '/api/vocab',
        read('vocab', () => pages('profile_reads.vocab_ok')),
      );
      options.before?.(fake);
    },
  });
  await page.advance(1_000);
  assertProfilePatchContexts(page.data.machine);
  fenceChecks.push(() => {
    assertProfilePatchContexts(page.data.machine);
  });
  return page;
}

type Page = Awaited<ReturnType<typeof openCollection>>;

function module(id: string): HTMLElement {
  const found = document.querySelector<HTMLElement>(`[data-module="${id}"]`);
  if (found === null) throw new Error(`module ${id} missing`);
  return found;
}

function region(name: 'collectionRewards' | 'collectionPolicies' | 'collectionChores') {
  const found = document.querySelector<HTMLElement>(`[data-region="${name}"]`);
  if (found === null) throw new Error(`${name} missing`);
  return found;
}

const settingsPosts = (page: Page) => postBodies(page.fake, '/api/settings');
const patchPosts = (page: Page) => postBodies(page.fake, '/api/profile-patch');
const READ_REVISION = (
  pages('settings.populated_account_every_module').body as { revision: string }
).revision;
const SOURCE_REVISION = (
  pages('collection_policies.read_before_the_first_save').body as {
    revision: string;
  }
).revision;

/** A module's switch by its field path (its accessible name is the server label; see the axe test). */
function check(box: HTMLElement, path: string): HTMLInputElement {
  const found = box.querySelector<HTMLInputElement>(
    `[data-field="${path}"] input[type="checkbox"]`,
  );
  if (found === null) throw new Error(`switch ${path} missing`);
  return found;
}

function saveOf(box: HTMLElement): HTMLButtonElement {
  const found = [...box.querySelectorAll<HTMLButtonElement>('button')].find(
    (button) => button.textContent === 'Save',
  );
  if (found === undefined) throw new Error('Save missing');
  return found;
}

function part(moduleId: string, key: string): HTMLInputElement {
  const found = module(moduleId).querySelector<HTMLInputElement>(`[data-part="${key}"]`);
  if (found === null) throw new Error(`${moduleId} ${key} missing`);
  return found;
}

// A whole page per test: settle the first one's module loading too.
vi.setConfig({ testTimeout: 20_000 });

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  window.location.hash = '';
  vi.useRealTimers();
});

describe('C1 global rewards', () => {
  it('C1 SR-6 two reward switches save once with the read revision; the answer reads Saved; no claim is sent and nothing is resent', async () => {
    let settings = () => pages('settings.populated_account_every_module');
    const page = await openCollection({ settings: () => settings() });
    const box = module('rewards');
    await page.user.click(check(box, 'daily_missions'));
    await page.user.click(check(box, 'free_store_gems'));
    expect(page.fake.writes()).toEqual([]);
    await page.user.dblClick(saveOf(box));
    await page.advance(0);
    const recorded = pages('settings.saved_rewards');
    // The recorded save was made from another revision; the page sends the one it read.
    expect(settingsPosts(page)).toEqual([
      { ...(recorded.request.body as object), revision: READ_REVISION },
    ]);
    settings = () => pages('settings.read_after_every_module_was_saved');
    page.fake.next('/api/settings', 'POST').respond(recorded.status, recorded.body);
    await page.advance(1_000);
    expect(within(module('rewards')).getByText('Saved')).toBeVisible();
    expect(check(module('rewards'), 'daily_missions')).toBeChecked();
    await page.advance(60_000);
    expect(page.fake.writes().map((write) => write.path)).toEqual(['/api/settings']);
    const quota = within(region('collectionRewards')).getByText('Daily quota and reset time');
    expect(quota.tagName).toBe('DT');
    expect(quota.nextElementSibling).toHaveTextContent('Not reported');
  });

  it('C1 SR-4 F6T-R1-01 a lost save and unchanged read keep Unknown; a new save needs explicit acceptance of the values read', async () => {
    let settings = () => pages('settings.populated_account_every_module');
    const page = await openCollection({ settings: () => settings() });
    const box = () => module('rewards');
    await page.user.click(check(box(), 'event_missions'));
    await page.user.click(saveOf(box()));
    await page.advance(0);
    const reads = () => page.fake.requests.filter((r) => r.path === '/api/settings').length;
    const before = reads();
    // The next read is at another revision and does not hold the sent value: it cannot tell.
    settings = () => pages('settings.read_after_the_unanswered_save_holds_the_value');
    page.fake.next('/api/settings', 'POST').lose();
    await page.advance(1_000);
    expect(reads()).toBeGreaterThan(before);
    expect(box().querySelector('[data-outcome]')).toHaveAttribute('data-outcome', 'unknown');
    expect(within(box()).queryByRole('button', { name: 'Dismiss' })).toBeNull();
    await page.advance(60_000);
    expect(settingsPosts(page)).toHaveLength(1);
    // The draft and Unknown record stay. An inconclusive read does not open Save.
    expect(check(box(), 'event_missions')).toBeChecked();
    expect(saveOf(box())).toHaveAttribute('aria-disabled', 'true');
    await page.user.click(
      within(box()).getByRole('button', { name: 'Continue from the saved values as read' }),
    );
    expect(settingsPosts(page)).toHaveLength(1);
    expect(box()).toHaveTextContent('Unknown');
    // C15 C-01: accepting the Unknown outcome does not silently rebase its raw draft.
    expect(saveOf(box())).toHaveAttribute('aria-disabled', 'true');
    await page.user.click(
      within(box()).getByRole('button', { name: 'Use the read revision; keep my draft' }),
    );
    expect(saveOf(box())).not.toHaveAttribute('aria-disabled', 'true');
  });

  it('C1 SR-4 F6C-01 a read started while the save was in flight never settles it: after the commit and a lost reply, only the read after the loss does', async () => {
    let settings: () => DashboardCase | 'hold' = () =>
      pages('settings.populated_account_every_module');
    const page = await openCollection({ settings: () => settings() });
    const box = () => module('rewards');
    await page.user.click(check(box(), 'daily_missions'));
    await page.user.click(check(box(), 'free_store_gems'));
    await page.user.click(saveOf(box()));
    await page.advance(0);
    // While the POST is held, the person reads again: the old values, the old revision.
    page.data.commands.checkCollection();
    await page.advance(0);
    expect(box().querySelector('[data-outcome]')).toHaveAttribute('data-outcome', 'sending');
    // The server commits; the reply is lost; the read after the loss holds the values.
    settings = () => pages('settings.read_after_every_module_was_saved');
    page.fake.next('/api/settings', 'POST').lose();
    await page.advance(1_000);
    expect(box().querySelector('[data-outcome]')).toHaveAttribute('data-outcome', 'applied');
    await page.advance(60_000);
    expect(settingsPosts(page)).toHaveLength(1);
  });

  it('C1 SR-5 F6C-03 a keyboard Save keeps focus on the same button while the save is in flight and after it settles', async () => {
    const page = await openCollection();
    const box = () => module('rewards');
    await page.user.click(check(box(), 'daily_missions'));
    const save = saveOf(box());
    save.focus();
    await page.user.keyboard('{Enter}');
    await page.advance(0);
    expect(settingsPosts(page)).toHaveLength(1);
    expect(saveOf(box())).toBe(save);
    expect(save).toHaveFocus();
    expect(save).toHaveAttribute('aria-disabled', 'true');
    const recorded = pages('settings.saved_rewards');
    page.fake.next('/api/settings', 'POST').respond(recorded.status, recorded.body);
    await page.advance(1_000);
    expect(saveOf(box())).toBe(save);
    expect(save).toHaveFocus();
  });

  it('C1 SR-7 the starter profile: every profile module is read-only with its reason and offers no Save', async () => {
    // No recorded config read names the starter; this one is read_before_the_save naming the
    // profile the recorded settings read names (F6C-R1-05: values show only for their context).
    const config = pages('config_state.read_before_the_save');
    await openCollection({
      config: () => ({
        ...config,
        body: { ...(config.body as object), active_profile: 'default' },
      }),
      settings: () => pages('settings.starter_profile_active_profile_modules_read_only'),
    });
    const box = module('rewards');
    expect(box.querySelector('[data-read-only="starter"]')).not.toBeNull();
    expect(within(box).queryByRole('button', { name: 'Save' })).toBeNull();
    expect(check(box, 'daily_missions')).toBeDisabled();
  });

  it('C1 SR-5 a starter refusal keeps the draft and the reason; nothing is resent', async () => {
    const page = await openCollection();
    const box = () => module('rewards');
    await page.user.click(check(box(), 'free_store_gems'));
    await page.user.click(saveOf(box()));
    await page.advance(0);
    const refused = pages('settings.starter_profile_active_a_profile_module_is_refused');
    page.fake.next('/api/settings', 'POST').respond(refused.status, refused.body);
    await page.advance(1_000);
    expect(within(box()).getByText('Not saved')).toBeVisible();
    expect(box()).toHaveTextContent(
      'Create and activate a personal profile before editing these settings.',
    );
    expect(check(box(), 'free_store_gems')).not.toBeChecked();
    await page.advance(60_000);
    expect(settingsPosts(page)).toHaveLength(1);
  });
});

describe('C2 ad gems of a policy', () => {
  it.each([
    ['gem_delay_sec#0', '3.5', 'Use a whole number'],
    ['gem_delay_sec#1', '', 'Enter a value'],
    ['gem_delay_sec#0', '20', 'The first value must not be larger than the second'],
    ['ad_gem_options.confirmed_cooldown_sec#1', 'NaN', 'Use a number, like 12 or 2.5'],
    ['ad_gem_options.attempt_timeout_sec', '61', 'Above the highest allowed value'],
  ])(
    'C2 SR-5 %s typed %j stays as typed with its reason; Save is disabled and nothing is sent',
    async (key, typed, reason) => {
      const page = await openCollection();
      const input = part('ad:all_on', key);
      await page.user.clear(input);
      if (typed !== '') await page.user.type(input, typed);
      expect(part('ad:all_on', key)).toHaveValue(typed);
      expect(part('ad:all_on', key)).toHaveAttribute('aria-invalid', 'true');
      expect(part('ad:all_on', key)).toHaveAccessibleDescription(reason);
      const save = saveOf(module('ad:all_on'));
      expect(save).toHaveAttribute('aria-disabled', 'true');
      await page.user.click(save);
      await page.advance(0);
      expect(page.fake.writes()).toEqual([]);
    },
  );

  it('C2 the timers say start to start and keep the server labels (cadence, window, retry); a valid edit sends whole seconds', async () => {
    const page = await openCollection();
    const box = module('ad:all_on');
    expect(box).toHaveTextContent('Interval between collection windows');
    // The server's explanation of the cadence is field help, opened on demand.
    const cadence = within(box)
      .getByText('Interval between collection windows')
      .closest('[data-field]');
    if (!(cadence instanceof HTMLElement)) throw new Error('cadence field missing');
    await page.user.click(
      within(cadence).getByRole('button', {
        name: 'About Interval between collection windows',
      }),
    );
    expect(screen.getByRole('tooltip')).toHaveTextContent(
      'Time from the start of one collection window to the next',
    );
    await page.user.keyboard('{Escape}');
    expect(box).toHaveTextContent('Maximum collection window');
    expect(box).toHaveTextContent('After an unconfirmed claim');
    await page.user.clear(part('ad:all_on', 'gem_delay_sec#0'));
    await page.user.type(part('ad:all_on', 'gem_delay_sec#0'), '4');
    await page.user.click(saveOf(box));
    await page.advance(0);
    expect(settingsPosts(page)).toEqual([
      {
        context: { account: null, instance: 'main', profile: 'mine' },
        module: 'ad:all_on',
        revision: READ_REVISION,
        values: { gem_delay_sec: [4, 10] },
      },
    ]);
  });
});

describe('C3 timed orbit taps', () => {
  it.each([
    ['gem_orbit.center#0', '2560', 'Above the highest allowed value'],
    ['gem_orbit.center#1', '1,2', 'Use a number, like 12 or 2.5'],
    ['gem_orbit.interval_sec', '0', 'Below the lowest allowed value'],
    ['gem_orbit.taps', '2.5', 'Use a whole number'],
  ])(
    'C3 SR-5 %s typed %j is refused without clamping; editing sends nothing',
    async (key, typed, reason) => {
      const page = await openCollection();
      const input = part('orbit:all_on', key);
      await page.user.clear(input);
      await page.user.type(input, typed);
      expect(part('orbit:all_on', key)).toHaveValue(typed);
      expect(part('orbit:all_on', key)).toHaveAccessibleDescription(reason);
      await page.advance(10_000);
      expect(page.fake.writes()).toEqual([]);
    },
  );

  it('C3 the geometry (center, radius, angle, spread) sits in its native-pixel disclosure; no test tap exists', async () => {
    await openCollection();
    const box = module('orbit:all_on');
    expect(within(box).getByText('Tap geometry (native screen pixels)')).toBeVisible();
    expect(box.querySelector('details [data-field="gem_orbit.center"]')).not.toBeNull();
    expect(box.querySelector('details [data-field="gem_orbit.interval_sec"]')).toBeNull();
    expect(
      within(box)
        .queryAllByRole('button', { name: /tap/iu })
        .filter((button) => !button.classList.contains('tp-help__trigger')),
    ).toEqual([]);
  });
});

describe('C4 gathering choices', () => {
  it('C4 guild Store spending is said beside the switch once it is on, and saves once through settings only', async () => {
    const page = await openCollection();
    const box = () => module('gather:all_on');
    expect(box().querySelector('[data-note="guild_store"]')).toBeNull();
    await page.user.click(check(box(), 'guild_store'));
    expect(box().querySelector('[data-note="guild_store"]')).toHaveTextContent(
      'Spending: lets automation spend guild currency in the guild Store',
    );
    await page.user.click(saveOf(box()));
    await page.advance(0);
    expect(settingsPosts(page)).toEqual([
      expect.objectContaining({ module: 'gather:all_on', values: { guild_store: true } }),
    ]);
    expect(patchPosts(page)).toEqual([]);
  });

  it('C4 SR-2 a read or a save never overwrites a draft of another policy', async () => {
    let settings = () => pages('settings.populated_account_every_module');
    const page = await openCollection(
      { settings: () => settings() },
      {},
      '#collection/policy/gems_only',
    );
    await page.user.click(check(module('gather:gems_only'), 'guild'));
    // Another policy's save and the reads after it.
    window.location.hash = '#collection/policy/all_on';
    await page.advance(0);
    await page.user.click(check(module('gather:all_on'), 'guild_store'));
    await page.user.click(saveOf(module('gather:all_on')));
    await page.advance(0);
    const saved = pages('settings.saved_gather_all_on');
    settings = () => pages('settings.read_after_every_module_was_saved');
    page.fake.next('/api/settings', 'POST').respond(saved.status, saved.body);
    await page.advance(60_000);
    window.location.hash = '#collection/policy/gems_only';
    await page.advance(0);
    expect(check(module('gather:gems_only'), 'guild')).toBeChecked();
    expect(settingsPosts(page)).toHaveLength(1);
  });
});

describe('C5 policies: list, label, copy, delete', () => {
  it('C5 each policy names the runs that use it; an in-use policy cannot be deleted and says why', async () => {
    await openCollection();
    const box = region('collectionPolicies');
    expect(box.querySelector('[data-policy="gems_only"]')).toHaveTextContent('farm_deep');
    expect(box.querySelector('[data-policy="gems_only"]')).toHaveTextContent('shards');
    const uses = box.querySelector<HTMLElement>('[data-policy="gems_only"] .tp-collection__uses');
    if (uses === null) throw new Error('policy uses list missing');
    expect(
      within(uses)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual(['farm_deep', 'shards']);
    const remove = within(box).getByRole('button', { name: 'Delete this policy' });
    expect(remove).toHaveAttribute('aria-disabled', 'true');
    expect(remove).toHaveAccessibleDescription(
      'Used by a run; give those runs another policy in Runs first',
    );
  });

  it('C5 SR-6 an unused policy is deleted after an explicit confirmation, with one delete patch from the read revision', async () => {
    const source = pages(
      'collection_policies.a_run_bound_to_a_missing_policy_is_a_problem_the_page_must_show',
    );
    const page = await openCollection({ source: () => source }, {}, '#collection/policy/all_on');
    const box = region('collectionPolicies');
    expect(box.querySelector('[data-missing="true"]')).toHaveTextContent('ghost');
    await page.user.click(within(box).getByRole('button', { name: 'Delete this policy' }));
    expect(page.fake.writes()).toEqual([]);
    await page.user.click(within(box).getByRole('button', { name: 'Delete' }));
    await page.advance(0);
    expect(patchPosts(page)).toEqual([
      {
        expected_context: { instance: 'main', serial: 'fake:1', account: null, profile: 'mine' },
        expected_revision: (source.body as { revision: string }).revision,
        name: 'mine',
        op: 'delete',
        path: ['policies', 'gather', 'all_on'],
      },
    ]);
  });

  it('C5 the copy keeps every field of its source with the new name; one patch; the answer reads Saved', async () => {
    const page = await openCollection();
    const form = () =>
      region('collectionPolicies').querySelector<HTMLElement>('[data-form="copy"]');
    const copy = form();
    if (copy === null) throw new Error('copy form missing');
    await page.user.type(
      within(copy).getByRole('textbox', { name: /^New identifier/u }),
      'weekend',
    );
    await page.user.type(
      within(copy).getByRole('textbox', { name: /^Name shown/u }),
      'Weekend gathering',
    );
    await page.user.click(within(copy).getByRole('button', { name: 'Save copy' }));
    await page.advance(0);
    const source = pages('collection_policies.read_before_the_first_save').body as {
      profile: { policies: { gather: Record<string, unknown> } };
    };
    expect(patchPosts(page)).toEqual([
      {
        expected_context: { instance: 'main', serial: 'fake:1', account: null, profile: 'mine' },
        expected_revision: SOURCE_REVISION,
        name: 'mine',
        path: ['policies', 'gather', 'weekend'],
        value: {
          ...(source.profile.policies.gather['all_on'] as object),
          label: 'Weekend gathering',
        },
      },
    ]);
    const added = pages('collection_policies.policy_added');
    page.fake.next('/api/profile-patch', 'POST').respond(added.status, added.body);
    await page.advance(1_000);
    const line = form()?.querySelector('[data-outcome]');
    expect(line).toHaveAttribute('data-outcome', 'saved');
  });

  it('C5 SR-4 F6C-R1-02 a copy whose answer is lost reads the profile again by itself, stays unknown while the read lacks it, and is never resent', async () => {
    const page = await openCollection();
    const copy = region('collectionPolicies').querySelector<HTMLElement>('[data-form="copy"]');
    if (copy === null) throw new Error('copy form missing');
    await page.user.type(
      within(copy).getByRole('textbox', { name: /^New identifier/u }),
      'weekend',
    );
    await page.user.click(within(copy).getByRole('button', { name: 'Save copy' }));
    await page.advance(0);
    const reads = () =>
      page.fake.requests.filter((r) => r.path.startsWith('/api/profile-src/')).length;
    const before = reads();
    page.fake.next('/api/profile-patch', 'POST').lose();
    await page.advance(1_000);
    expect(reads()).toBeGreaterThan(before);
    // The read is still at the revision the copy was made from and lacks it: that cannot
    // exclude a commit still to come, so the copy stays unknown with its journal.
    expect(copy.querySelector('[data-outcome]')).toHaveAttribute('data-outcome', 'unknown');
    expect(
      page.data.machine.snapshot().find((entry) => entry.kind === 'profile_patch')?.phase,
    ).toBe('unconfirmed');
    await page.advance(60_000);
    expect(patchPosts(page)).toHaveLength(1);
  });

  it('C5 SR-7 a click-time config read naming another account sends no patch', async () => {
    let config = () => pages('config_state.read_before_the_save');
    const page = await openCollection({ config: () => config() });
    const copy = region('collectionPolicies').querySelector<HTMLElement>('[data-form="copy"]');
    if (copy === null) throw new Error('copy form missing');
    await page.user.type(
      within(copy).getByRole('textbox', { name: /^New identifier/u }),
      'weekend',
    );
    config = () => pages('config_state.effective_view_of_the_other_account');
    await page.user.click(within(copy).getByRole('button', { name: 'Save copy' }));
    await page.advance(1_000);
    expect(patchPosts(page)).toEqual([]);
    expect(document.querySelector('[data-not-sent]')).toHaveAttribute(
      'data-not-sent',
      'context_changed',
    );
  });

  it('C5 SR-6 F6C-02 a settings Save clicked while a profile patch waits for its click-time read is not sent; only the first save sends', async () => {
    let config: () => DashboardCase | 'hold' = () => pages('config_state.read_before_the_save');
    const page = await openCollection({ config: () => config() });
    const copy = region('collectionPolicies').querySelector<HTMLElement>('[data-form="copy"]');
    if (copy === null) throw new Error('copy form missing');
    await page.user.type(
      within(copy).getByRole('textbox', { name: /^New identifier/u }),
      'weekend',
    );
    await page.user.click(check(module('rewards'), 'event_missions'));
    config = () => 'hold';
    await page.user.click(within(copy).getByRole('button', { name: 'Save copy' }));
    await page.advance(0);
    await page.user.click(saveOf(module('rewards')));
    await page.advance(0);
    expect(document.querySelector('[data-not-sent]')).toHaveAttribute(
      'data-not-sent',
      'collection_write_in_flight',
    );
    page.fake
      .next('/api/config', 'GET')
      .respond(200, pages('config_state.read_before_the_save').body);
    await page.advance(0);
    expect(patchPosts(page)).toHaveLength(1);
    expect(settingsPosts(page)).toEqual([]);
  });

  it('C5 C7 F6C-04 Cancel clears a copy draft and restores a policy name; nothing is sent', async () => {
    const page = await openCollection();
    const copy = () => {
      const found = region('collectionPolicies').querySelector<HTMLElement>('[data-form="copy"]');
      if (found === null) throw new Error('copy form missing');
      return found;
    };
    await page.user.type(
      within(copy()).getByRole('textbox', { name: /^New identifier/u }),
      'weekend',
    );
    await page.user.type(within(copy()).getByRole('textbox', { name: /^Name shown/u }), 'Weekend');
    await page.user.click(within(copy()).getByRole('button', { name: 'Cancel' }));
    expect(within(copy()).getByRole('textbox', { name: /^New identifier/u })).toHaveValue('');
    expect(within(copy()).getByRole('textbox', { name: /^Name shown/u })).toHaveValue('');
    const label = () => {
      const found = region('collectionPolicies').querySelector<HTMLElement>('[data-form="label"]');
      if (found === null) throw new Error('label form missing');
      return found;
    };
    await page.user.type(
      within(label()).getByRole('textbox', { name: 'Name shown' }),
      'Everything',
    );
    await page.user.click(within(label()).getByRole('button', { name: 'Cancel' }));
    expect(within(label()).getByRole('textbox', { name: 'Name shown' })).toHaveValue('');
    await page.advance(10_000);
    expect(page.fake.writes()).toEqual([]);
  });

  it('C5 the label is saved with one patch of its own path', async () => {
    const page = await openCollection();
    const row = region('collectionPolicies').querySelector<HTMLElement>('[data-form="label"]');
    if (row === null) throw new Error('label form missing');
    await page.user.type(within(row).getByRole('textbox', { name: 'Name shown' }), 'Everything');
    await page.user.click(within(row).getByRole('button', { name: 'Save name' }));
    await page.advance(0);
    expect(patchPosts(page)).toEqual([
      {
        expected_context: { instance: 'main', serial: 'fake:1', account: null, profile: 'mine' },
        expected_revision: SOURCE_REVISION,
        name: 'mine',
        path: ['policies', 'gather', 'all_on', 'label'],
        value: 'Everything',
      },
    ]);
  });
});

describe('C6 between-run chores', () => {
  it('C6 shatter is said to destroy modules; switching quest scan off saves the whole list once and runs nothing', async () => {
    const page = await openCollection();
    const box = region('collectionChores');
    expect(box.querySelector('[data-chore="shatter"]')).toHaveTextContent(
      'Destroys modules when it runs',
    );
    expect(box).toHaveTextContent('On lets a chore run between runs when it is due');
    await page.user.click(within(box).getByRole('checkbox', { name: 'Quest scan' }));
    await page.user.click(within(box).getByRole('button', { name: 'Save chores' }));
    await page.advance(0);
    expect(patchPosts(page)).toEqual([
      {
        expected_context: { instance: 'main', serial: 'fake:1', account: null, profile: 'mine' },
        expected_revision: SOURCE_REVISION,
        name: 'mine',
        path: ['policies', 'chores'],
        value: [{ name: 'quest_scan', enabled: false }],
      },
    ]);
    expect(page.fake.writes().map((write) => write.path)).toEqual(['/api/profile-patch']);
  });

  it('C1-C6 has no axe violations and only catalogue or server text', async () => {
    await openCollection();
    vi.useRealTimers();
    expect(await axe(document.body)).toHaveNoViolations();
    expect(textOutsideCatalogue(document.body)).toEqual([]);
  });
});

describe('Collection review round 1 (F6C-R1)', () => {
  const drafts = (page: Page): Record<string, Record<string, unknown>> => {
    const raw = page.storage.entries?.()['tp.runDrafts.v1'];
    return raw === undefined ? {} : (JSON.parse(raw) as Record<string, Record<string, unknown>>);
  };
  const journal = (page: Page, kind: string) =>
    page.data.machine.snapshot().filter((entry) => entry.kind === kind);
  const copyForm = () => {
    const found = region('collectionPolicies').querySelector<HTMLElement>('[data-form="copy"]');
    if (found === null) throw new Error('copy form missing');
    return found;
  };
  const reload = async (page: Page, reads: Reads, hash = '#collection') => {
    page.view.unmount();
    page.data.stop();
    return openCollection(reads, { storage: page.storage }, hash);
  };

  it('F6C-R1-01 SR-2 a reload restores valid, invalid, switch and copy drafts raw, only under their own emulator, account and profile', async () => {
    let config = () => pages('config_state.read_before_the_save');
    const reads: Reads = { config: () => config() };
    const page = await openCollection(reads);
    await page.user.clear(part('ad:all_on', 'gem_delay_sec#0'));
    await page.user.type(part('ad:all_on', 'gem_delay_sec#0'), '007');
    await page.user.clear(part('orbit:all_on', 'gem_orbit.center#0'));
    await page.user.type(part('orbit:all_on', 'gem_orbit.center#0'), '2560');
    await page.user.click(check(module('rewards'), 'event_missions'));
    await page.user.type(
      within(copyForm()).getByRole('textbox', { name: /^New identifier/u }),
      'kept_copy',
    );
    await page.user.type(
      within(copyForm()).getByRole('textbox', { name: /^Name shown/u }),
      'Kept label',
    );
    await page.advance(0);
    expect(page.fake.writes()).toEqual([]);

    // Another account's context: none of these drafts appear there.
    config = () => pages('config_state.populated_accounts_and_loadouts');
    const elsewhere = await reload(page, reads);
    // Account alpha: the settings read names no account, so no values (and no draft) show.
    expect(module('rewards').querySelector('input')).toBeNull();
    expect(within(copyForm()).getByRole('textbox', { name: /^New identifier/u })).toHaveValue('');

    config = () => pages('config_state.read_before_the_save');
    const again = await reload(elsewhere, reads);
    expect(part('ad:all_on', 'gem_delay_sec#0')).toHaveValue('007');
    expect(part('orbit:all_on', 'gem_orbit.center#0')).toHaveValue('2560');
    expect(part('orbit:all_on', 'gem_orbit.center#0')).toHaveAccessibleDescription(
      'Above the highest allowed value',
    );
    expect(check(module('rewards'), 'event_missions')).toBeChecked();
    expect(within(copyForm()).getByRole('textbox', { name: /^New identifier/u })).toHaveValue(
      'kept_copy',
    );
    expect(within(copyForm()).getByRole('textbox', { name: /^Name shown/u })).toHaveValue(
      'Kept label',
    );
    await again.advance(60_000);
    expect(again.fake.writes()).toEqual([]);
  });

  it('F6C-R1-02 SR-4 a held settings save across a reload: an unchanged read keeps it unknown with its journal and draft; a read holding the values settles it; never resent', async () => {
    let settings = () => pages('settings.populated_account_every_module');
    const reads: Reads = { settings: () => settings() };
    const page = await openCollection(reads);
    await page.user.click(check(module('rewards'), 'daily_missions'));
    await page.user.click(check(module('rewards'), 'free_store_gems'));
    await page.user.click(saveOf(module('rewards')));
    await page.advance(0);
    expect(settingsPosts(page)).toHaveLength(1);
    page.fake.next('/api/settings', 'POST'); // received and held: no answer ever reaches this page
    const again = await reload(page, reads);
    await again.advance(5_000);
    const line = () => module('rewards').querySelector('[data-outcome]');
    expect(line()).toHaveAttribute('data-outcome', 'unknown');
    expect(journal(again, 'settings_save').map((entry) => entry.phase)).toEqual(['unconfirmed']);
    expect(check(module('rewards'), 'daily_missions')).toBeChecked();
    expect(Object.keys(drafts(again)).some((scope) => scope.startsWith('settings:rewards:'))).toBe(
      true,
    );
    // The original write commits; the next read holds its values.
    settings = () => pages('settings.read_after_every_module_was_saved');
    await again.user.click(
      within(region('collectionRewards')).getByRole('button', {
        name: 'Read the saved settings again',
      }),
    );
    await again.advance(1_000);
    expect(line()).toHaveAttribute('data-outcome', 'applied');
    expect(Object.keys(drafts(again)).some((scope) => scope.startsWith('settings:rewards:'))).toBe(
      false,
    );
    await again.advance(60_000);
    expect(settingsPosts(again)).toEqual([]);
  });

  it('F6C-R1-02 SR-4 a held policy copy across a reload: the unchanged source read keeps it unknown with its journal and its draft; never resent', async () => {
    const page = await openCollection();
    await page.user.type(
      within(copyForm()).getByRole('textbox', { name: /^New identifier/u }),
      'weekend',
    );
    await page.user.click(within(copyForm()).getByRole('button', { name: 'Save copy' }));
    await page.advance(0);
    expect(patchPosts(page)).toHaveLength(1);
    page.fake.next('/api/profile-patch', 'POST');
    const again = await reload(page, {});
    await again.advance(5_000);
    expect(copyForm().querySelector('[data-outcome]')).toHaveAttribute('data-outcome', 'unknown');
    expect(journal(again, 'profile_patch').map((entry) => entry.phase)).toEqual(['unconfirmed']);
    expect(within(copyForm()).getByRole('textbox', { name: /^New identifier/u })).toHaveValue(
      'weekend',
    );
    await again.advance(60_000);
    expect(patchPosts(again)).toEqual([]);
  });

  it('F6C-R1-05 SR-7 a read of another profile holding the sent values neither settles the save nor shows those values; the draft stays; only this context settles it', async () => {
    let settings = () => pages('settings.read_after_every_module_was_saved');
    const page = await openCollection({ settings: () => settings() });
    await page.user.click(check(module('rewards'), 'free_store_gems'));
    await page.user.click(check(module('rewards'), 'daily_missions'));
    await page.user.click(saveOf(module('rewards')));
    await page.advance(0);
    expect(settingsPosts(page)).toEqual([
      expect.objectContaining({
        context: { account: null, instance: 'main', profile: 'mine' },
        values: { free_store_gems: true, daily_missions: false },
      }),
    ]);
    // Account alpha, profile theirs: its rewards hold exactly the sent values.
    settings = () => pages('settings.other_accounts_profile_active_profile_modules_read_only');
    const failed = pages('settings.saved_then_the_answer_failed_500');
    page.fake.next('/api/settings', 'POST').respond(failed.status, answerOf(failed).body);
    await page.advance(1_000);
    const box = () => module('rewards');
    expect(box().querySelector('[data-outcome]')).toHaveAttribute('data-outcome', 'unknown');
    expect(box().querySelector('input')).toBeNull();
    expect(box()).toHaveTextContent(
      'The last read was for another emulator, account or profile; check again',
    );
    expect(region('collectionRewards')).toHaveTextContent(
      'The last settings read was for another emulator, account or profile',
    );
    expect(journal(page, 'settings_save').map((entry) => entry.phase)).toEqual(['unconfirmed']);
    expect(Object.keys(drafts(page)).some((scope) => scope.startsWith('settings:rewards:'))).toBe(
      true,
    );
    // A read of this context holding the values settles it.
    settings = () => pages('settings.populated_account_every_module');
    await page.user.click(
      within(region('collectionRewards')).getByRole('button', {
        name: 'Read the saved settings again',
      }),
    );
    await page.advance(1_000);
    expect(box().querySelector('[data-outcome]')).toHaveAttribute('data-outcome', 'applied');
    expect(settingsPosts(page)).toHaveLength(1);
  });

  it("F6C-R1-03 C5 navigating to another policy never changes an unfinished copy's source", async () => {
    const page = await openCollection();
    const source = pages('collection_policies.read_before_the_first_save').body as {
      profile: { policies: { gather: Record<string, object> } };
    };
    await page.user.type(
      within(copyForm()).getByRole('textbox', { name: /^New identifier/u }),
      'source_probe',
    );
    window.location.hash = '#collection/policy/gems_only';
    await page.advance(1_000);
    expect(
      region('collectionPolicies').querySelector('[data-focus-key="collection:copy:from"]'),
    ).toHaveValue('all_on');
    window.location.hash = '#collection/policy/hands_off';
    await page.advance(1_000);
    await page.user.click(within(copyForm()).getByRole('button', { name: 'Save copy' }));
    await page.advance(0);
    expect(patchPosts(page)).toEqual([
      expect.objectContaining({
        path: ['policies', 'gather', 'source_probe'],
        value: source.profile.policies.gather['all_on'],
      }),
    ]);
  });

  it('C-01 C5 F6C-R1-04 a profile with no gathering policies and unreadable templates explains unavailable creation; nothing is invented or sent', async () => {
    // The recorded settings read of such a profile, and the recorded source read with its
    // gathering map emptied (no recorded source read has none).
    const read = pages('collection_policies.read_before_the_first_save');
    const body = read.body as { profile: { policies: object; blueprints: object } };
    const page = await openCollection({
      settings: () => pages('settings.profile_without_gathering_policies_has_no_gather_modules'),
      source: () => ({
        ...read,
        body: {
          ...body,
          profile: {
            ...body.profile,
            blueprints: {},
            policies: { ...body.profile.policies, gather: {} },
          },
        },
      }),
    });
    const policies = region('collectionPolicies');
    expect(policies).toHaveTextContent('This profile has no gathering policies');
    expect(policies.querySelector('[data-first-policy="unavailable"]')).toHaveTextContent(
      'No template gathering defaults can be read',
    );
    const save = within(copyForm()).getByRole('button', { name: 'Save copy' });
    expect(save).toHaveAttribute('aria-disabled', 'true');
    expect(save).toHaveAccessibleDescription('No saved policy to copy; create one from a template');
    await page.user.click(save);
    await page.advance(10_000);
    expect(patchPosts(page)).toEqual([]);
  });
});
