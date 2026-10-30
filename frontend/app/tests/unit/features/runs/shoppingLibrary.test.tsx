import { act, within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { entries, own, ownPath } from '../../../../src/core/read.ts';
import { contextFromConfig } from '../../../../src/api/context.ts';
import { createMemoryStorage } from '../../../support/fakeTransport.ts';
import { selectShoppingLibrary } from '../../../../src/store/selectors/shoppingLibrary.ts';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import {
  dashboardPagesCase,
  dashboardPagesCaseNames,
  type DashboardCase,
} from '../../../support/dashboardFixture.ts';
import { postBodies } from '../../../support/dataLayer.ts';
import { renderPage, type PageHarness, type PageOptions } from '../dashboard/harness.tsx';

const sample = (name: string) => dashboardPagesCase(`shopping_queue.${name}`);
const CONFIG = dashboardPagesCase(
  'run_templates_add_fenced.read_config_the_page_context_comes_from',
).body;
const INITIAL = sample('shopping_library_read_before_any_edit');
const SOURCE = '/api/profile-src/mine';
const PATCH = '/api/profile-patch';
// Whole-App catalogue/axe and interaction tests share CPU with the other page suites.
vi.setConfig({ testTimeout: 30_000 });
const family = dashboardPagesCaseNames('shopping_queue').map(sample);
function sourceBefore(entry: DashboardCase): DashboardCase {
  const revision = own(entry.request.body, 'expected_revision');
  const found = family.find(
    (item) => own(item.body, 'revision') === revision && own(item.body, 'profile') !== undefined,
  );
  // Intermediate GETs are not recorded: wrap the preceding recorded receipt's exact profile
  // and revision in the source envelope. This only supplies the request's starting snapshot.
  return found === undefined
    ? INITIAL
    : {
        ...INITIAL,
        body: {
          ...Object.fromEntries(entries(INITIAL.body)),
          profile: own(found.body, 'profile'),
          revision,
        },
      };
}
function root() {
  const element = document.querySelector<HTMLElement>('[data-shopping-library]');
  if (element === null) throw new Error('missing library');
  return element;
}
function policy(id: string) {
  const element = root().querySelector<HTMLElement>(`[data-library-policy="${id}"]`);
  if (element === null) throw new Error(`missing policy ${id}`);
  return element;
}
function create() {
  const element = root().querySelector<HTMLElement>('[data-library-create]');
  if (element === null) throw new Error('missing create form');
  return within(element);
}
async function open(source: () => DashboardCase = () => INITIAL, options: PageOptions = {}) {
  window.location.hash = '#runs/saved/farm';
  const page = await renderPage({
    ...options,
    config: options.config ?? (() => CONFIG),
    before: (fake) => {
      fake.route('GET', SOURCE, () => source());
      fake.route('GET', '/api/vocab', () => dashboardPagesCase('profile_reads.vocab_ok'));
      fake.route('GET', /^\/api\/run-controls/, () => sample('shopping_read_before_any_save'));
      fake.route('GET', '/api/settings', () =>
        dashboardPagesCase('settings.populated_account_every_module'),
      );
      options.before?.(fake);
    },
  });
  await page.advance(100);
  await page.user.click(within(root()).getByText('Manage policies', { exact: true }));
  return page;
}
async function edit(page: PageHarness, id = 'main_list') {
  await page.user.click(within(policy(id)).getByText('Edit shared policy', { exact: true }));
  return within(policy(id));
}
async function weighted(page: PageHarness) {
  const form = await edit(page);
  const reserve = form.getByLabelText('Cash reserve', { exact: true });
  await page.user.clear(reserve);
  await page.user.type(reserve, '750.5');
  await page.user.selectOptions(form.getByLabelText('Purchase pattern'), 'weighted_split');
  await page.user.selectOptions(form.getByLabelText('Cadence'), 'while_affordable');
  const health = policy('main_list').querySelector<HTMLElement>('li[data-upgrade="health"]');
  const damage = policy('main_list').querySelector<HTMLElement>('li[data-upgrade="damage"]');
  if (health === null || damage === null) throw new Error('missing upgrades');
  await page.user.clear(within(health).getByLabelText('Weight', { exact: true }));
  await page.user.type(within(health).getByLabelText('Weight', { exact: true }), '3');
  await page.user.type(
    within(health).getByLabelText('Until wave (exclusive)', { exact: true }),
    '20',
  );
  await page.user.clear(within(damage).getByLabelText('Tap attempts this battle', { exact: true }));
  const up = within(health).getByRole('button', { name: /Move up/ });
  up.focus();
  await page.user.keyboard('{Enter}');
  expect(document.activeElement).toBe(up);
  return form;
}
async function send(page: PageHarness, id = 'main_list') {
  await page.user.click(within(policy(id)).getByRole('button', { name: 'Save shared policy' }));
  await page.advance(100);
}
beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  window.location.hash = '';
});

describe('SQ9 shared shopping policies', () => {
  it('SQ9 closed summaries retain invalid-field signals while exact errors remain beside the open fields', async () => {
    const page = await open();
    const form = await edit(page);
    const reserve = form.getByLabelText('Cash reserve', { exact: true });
    await page.user.clear(reserve);
    await page.user.type(reserve, 'nope');
    const summary = root().querySelector(':scope > details > summary');
    const localSummary = policy('main_list').querySelector('details > summary');
    expect(summary).toHaveTextContent('Needs attention');
    expect(summary).toHaveTextContent('1 problem');
    expect(localSummary).toHaveTextContent('Needs attention');
    expect(reserve).toHaveAttribute('aria-invalid', 'true');
    expect(reserve).toHaveAccessibleDescription(expect.stringContaining('Use digits'));
    await page.user.click(within(root()).getByText('Manage policies', { exact: true }));
    expect(summary).toBeVisible();
    expect(reserve).not.toBeVisible();
    expect(root().querySelector(':scope > p')).toBeVisible();
    expect(root().querySelector(':scope > p')).toHaveTextContent(
      'Shared saves affect these runs in future battles',
    );
    expect(page.fake.writes()).toEqual([]);
  });

  it('RL3 invalid machine fallback settings are signalled before their disclosure opens', async () => {
    // The default settings read is valid; this recorded read saves a preset the machine no longer lists.
    const page = await open(() => INITIAL, {
      before: (fake) => {
        fake.route('GET', '/api/settings', () =>
          dashboardPagesCase(
            'settings_modules_more.read_with_an_unlisted_saved_run_defaults_preset',
          ),
        );
      },
    });
    const defaults = document.querySelector<HTMLElement>('[data-run-defaults]');
    if (defaults === null) throw new Error('missing saved defaults');
    const summary = defaults.querySelector('summary');
    expect(summary).toHaveTextContent('Needs attention');
    expect(summary).toHaveTextContent('1 problem');
    expect(within(defaults).getByText('Choose one of the listed options')).not.toBeVisible();
    await page.user.click(
      within(defaults).getByText('Saved defaults / Machine fallback', { exact: true }),
    );
    expect(within(defaults).getByText('Choose one of the listed options')).toBeVisible();
    expect(page.fake.writes()).toEqual([]);
  });

  it('SQ9 shows all policy usages, names affected runs, forbids used deletion and passes catalogue/axe', async () => {
    const page = await open();
    const main = within(policy('main_list'));
    expect(main.getByRole('link', { name: 'farm' })).toHaveAttribute('href', '#runs/saved/farm');
    expect(main.getByRole('link', { name: 'other' })).toHaveAttribute('href', '#runs/saved/other');
    expect(main.getByRole('button', { name: 'Delete policy' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    await page.user.click(main.getByRole('button', { name: 'Delete policy' }));
    expect(postBodies(page.fake)).toEqual([]);
    await edit(page, 'legacy');
    expect(textOutsideCatalogue(root())).toEqual([]);
    vi.useRealTimers();
    expect(await axe(root())).toHaveNoViolations();
  });

  it('SQ9 creates an empty v2 queue only after explicit reserve, pattern and cadence, then copies independently', async () => {
    let source = INITIAL;
    const created = sample('shopping_policy_created');
    const copied = sample('shopping_policy_copied');
    const page = await open(() => source, {
      before: (fake) => {
        fake.route('POST', PATCH, (request) => {
          const copying =
            ownPath(JSON.parse(request.body ?? '{}'), 'path')
              ?.toString()
              .includes('copy') === true;
          const answer = copying ? copied : created;
          // Recorded receipts provide the exact saved source after each isolated write.
          source = {
            ...INITIAL,
            body: {
              ...Object.fromEntries(entries(INITIAL.body)),
              profile: own(answer.body, 'profile'),
              revision: own(answer.body, 'revision'),
            },
          };
          return answer;
        });
      },
    });
    await page.user.click(within(root()).getByRole('button', { name: 'Create a shared queue' }));
    await page.user.type(create().getByLabelText('New policy ID'), 'new_queue');
    expect(create().getByRole('button', { name: 'Save shared policy' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    await page.user.type(create().getByLabelText('Cash reserve', { exact: true }), '0');
    await page.user.selectOptions(
      create().getByLabelText('Purchase pattern'),
      'highest_affordable',
    );
    await page.user.selectOptions(create().getByLabelText('Cadence'), 'single_attempt');
    await page.user.click(create().getByRole('button', { name: 'Save shared policy' }));
    await page.advance(100);
    expect(postBodies(page.fake, PATCH)).toEqual([created.request.body]);
    await page.user.click(within(policy('main_list')).getByRole('button', { name: 'Copy policy' }));
    await page.user.type(create().getByLabelText('New policy ID'), 'main_list_copy');
    await page.user.click(create().getByRole('button', { name: 'Save shared policy' }));
    await page.advance(100);
    expect(postBodies(page.fake, PATCH)).toEqual([created.request.body, copied.request.body]);
  });

  it('SQ9 edits a full shared v2 policy with keyboard reorder and the exact fenced recorded body', async () => {
    const answer = sample('shopping_shared_v2_updated');
    const page = await open(() => sourceBefore(answer), {
      before: (fake) => {
        fake.route('POST', PATCH, () => answer);
      },
    });
    await weighted(page);
    await send(page);
    expect(postBodies(page.fake, PATCH)).toEqual([answer.request.body]);
    expect(policy('main_list').querySelector('[data-outcome="saved"]')).not.toBeNull();
    expect(textOutsideCatalogue(root())).toEqual([]);
  });

  it('SQ9 deletes an unused policy only after confirmation and retains its receipt after readback removes the row', async () => {
    const deleted = sample('shopping_policy_deleted_unused');
    let source = INITIAL;
    const page = await open(() => source, {
      before: (fake) => {
        fake.route('POST', PATCH, () => {
          // The successful recorded receipt is the exact subsequent source profile/revision.
          source = {
            ...INITIAL,
            body: {
              ...Object.fromEntries(entries(INITIAL.body)),
              profile: own(deleted.body, 'profile'),
              revision: own(deleted.body, 'revision'),
            },
          };
          return deleted;
        });
      },
    });
    const box = within(policy('unused'));
    await page.user.click(box.getByRole('button', { name: 'Delete policy' }));
    expect(postBodies(page.fake)).toEqual([]);
    await page.user.click(box.getByRole('button', { name: 'Cancel deletion' }));
    expect(box.getByRole('button', { name: 'Delete policy' })).toHaveFocus();
    await page.user.click(box.getByRole('button', { name: 'Delete policy' }));
    await page.user.click(box.getByRole('button', { name: 'Confirm policy deletion' }));
    await page.advance(100);
    expect(postBodies(page.fake, PATCH)).toEqual([deleted.request.body]);
    expect(root().querySelector('[data-library-policy="unused"]')).toBeNull();
    expect(
      root().querySelector('[data-library-earlier="unused"] [data-outcome="saved"]'),
    ).not.toBeNull();
  });

  it('SQ9 shows the recorded used-policy server refusal if usage changed after the displayed source', async () => {
    const refused = sample('shopping_policy_delete_used_refused');
    // Simulate stale usage: only the displayed source omits the two main_list bindings.
    // The server's refusal is the unchanged recorded case and the POST uses its exact revision.
    const profile = own(INITIAL.body, 'profile');
    const blueprints = Object.fromEntries(
      entries(own(profile, 'blueprints')).map(([key, value]) => [
        key,
        Object.fromEntries(entries(value).filter(([name]) => name !== 'shopping')),
      ]),
    );
    const source = {
      ...INITIAL,
      body: {
        ...Object.fromEntries(entries(INITIAL.body)),
        revision: own(refused.request.body, 'expected_revision'),
        profile: { ...Object.fromEntries(entries(profile)), blueprints },
      },
    };
    const page = await open(() => source, {
      before: (fake) => {
        fake.route('POST', PATCH, () => refused);
      },
    });
    const box = within(policy('main_list'));
    await page.user.click(box.getByRole('button', { name: 'Delete policy' }));
    await page.user.click(box.getByRole('button', { name: 'Confirm policy deletion' }));
    await page.advance(100);
    expect(postBodies(page.fake, PATCH)).toEqual([refused.request.body]);
    expect(policy('main_list').querySelector('[data-refusal]')).toHaveTextContent(
      String(own(refused.body, 'error')),
    );
  });

  it.each(['shopping_shared_revision_conflict', 'shopping_shared_context_changed'])(
    'SQ9 %s retains the full draft and server error',
    async (name) => {
      const answer = sample(name);
      const page = await open(() => INITIAL, {
        before: (fake) => {
          fake.route('POST', PATCH, () => answer);
        },
      });
      await weighted(page);
      await send(page);
      expect(postBodies(page.fake, PATCH)).toEqual([answer.request.body]);
      expect(
        within(policy('main_list')).getByLabelText('Cash reserve', { exact: true }),
      ).toHaveValue('750.5');
      expect(policy('main_list').querySelector('[data-refusal]')).toHaveTextContent(
        String(own(answer.body, 'error')),
      );
    },
  );

  it('SQ9 an Unknown save is never resent, survives reload and settles only after a later matching profile read', async () => {
    let source = INITIAL;
    const answer = sample('shopping_shared_reply_failed');
    const page = await open(() => source, {
      before: (fake) => {
        fake.route('POST', PATCH, () => answer);
      },
    });
    await weighted(page);
    await send(page);
    expect(policy('main_list').querySelector('[data-outcome="unknown"]')).not.toBeNull();
    await page.advance(60_000);
    expect(postBodies(page.fake, PATCH)).toEqual([answer.request.body]);
    page.view.unmount();
    page.data.stop();
    const reloaded = await open(() => source, { storage: page.storage });
    expect(policy('main_list').querySelector('[data-outcome="unknown"]')).not.toBeNull();
    await edit(reloaded);
    expect(within(policy('main_list')).getByLabelText('Cash reserve', { exact: true })).toHaveValue(
      '750.5',
    );
    source = sample('shopping_shared_read_after_unknown');
    await reloaded.user.click(
      within(root()).getByRole('button', { name: 'Check shared policies' }),
    );
    await reloaded.advance(100);
    expect(policy('main_list').querySelector('[data-outcome="applied"]')).not.toBeNull();
    expect(postBodies(reloaded.fake, PATCH)).toEqual([]);
  });

  it('SQ9 a changed click-time context sends nothing and keeps the original account draft', async () => {
    let changed = false;
    // Change the recorded config's binding only: this is a frontend fence race, before a POST.
    const switched = {
      ...Object.fromEntries(entries(CONFIG)),
      instances: {
        main: {
          ...Object.fromEntries(entries(ownPath(CONFIG, 'instances', 'main'))),
          account: 'beta',
        },
      },
    };
    const page = await open(() => INITIAL, { config: () => (changed ? switched : CONFIG) });
    await edit(page);
    await page.user.type(
      within(policy('main_list')).getByLabelText('Cash reserve', { exact: true }),
      '1',
    );
    changed = true;
    await send(page);
    expect(postBodies(page.fake, PATCH)).toEqual([]);
    expect(
      Object.values(page.data.store.getSnapshot().runDrafts).some(
        (draft) => draft['reserve'] === '5001',
      ),
    ).toBe(true);
  });

  it('SQ9 an unconfirmed click-time context sends nothing and explains why', async () => {
    const page = await open();
    await edit(page);
    await page.user.type(
      within(policy('main_list')).getByLabelText('Cash reserve', { exact: true }),
      '1',
    );
    page.fake.route('GET', '/api/config', () => 'hold');
    await page.user.click(
      within(policy('main_list')).getByRole('button', { name: 'Save shared policy' }),
    );
    await page.advance(6_000);
    expect(postBodies(page.fake, PATCH)).toEqual([]);
    expect(page.view.container).toHaveTextContent("couldn't be confirmed");
    expect(within(policy('main_list')).getByLabelText('Cash reserve', { exact: true })).toHaveValue(
      '5001',
    );
  });

  it('SQ9 pins the original revision across polls and requires explicit reconciliation after a conflict', async () => {
    let source = INITIAL;
    const answer = sample('shopping_shared_revision_conflict');
    const page = await open(() => source, {
      before: (fake) => {
        fake.route('POST', PATCH, () => answer);
      },
    });
    await weighted(page);
    await send(page);
    expect(
      within(policy('main_list')).getByRole('button', { name: 'Save shared policy' }),
    ).toHaveAttribute('aria-disabled', 'true');
    // Wrap the recorded creation receipt as a source: a different revision while
    // main_list still differs from the submitted draft, so readback cannot settle it.
    const created = sample('shopping_policy_created');
    source = {
      ...INITIAL,
      body: {
        ...Object.fromEntries(entries(INITIAL.body)),
        profile: own(created.body, 'profile'),
        revision: own(created.body, 'revision'),
      },
    };
    await page.user.click(within(root()).getByRole('button', { name: 'Check shared policies' }));
    await page.advance(100);
    expect(within(policy('main_list')).getByLabelText('Cash reserve', { exact: true })).toHaveValue(
      '750.5',
    );
    expect(
      within(policy('main_list')).getByRole('button', { name: 'Save shared policy' }),
    ).toHaveAttribute('aria-disabled', 'true');
    await page.user.click(
      within(policy('main_list')).getByRole('button', {
        name: 'Use the latest revision for this draft',
      }),
    );
    const vm = selectShoppingLibrary(page.data.store.getSnapshot()).policies.find(
      (item) => item.id === 'main_list',
    );
    expect(vm?.save?.revision).toBe(own(source.body, 'revision'));
    expect(postBodies(page.fake, PATCH)).toHaveLength(1);
  });

  it('SQ9 invalid text stays focused through five reads, a failed read and reload under its original policy', async () => {
    let source = INITIAL;
    const page = await open(() => source);
    await edit(page);
    const input = within(policy('main_list')).getByLabelText('Cash reserve', { exact: true });
    await page.user.clear(input);
    await page.user.type(input, 'not yet');
    for (let index = 0; index < 5; index += 1) {
      act(() => {
        page.data.commands.checkCollection();
      });
      await page.advance(100);
      expect(input).toHaveFocus();
    }
    // A recorded 500 body on a profile read exercises unavailable-state retention.
    source = sample('shopping_shared_reply_failed');
    act(() => {
      page.data.commands.checkCollection();
    });
    await page.advance(100);
    expect(within(policy('main_list')).getByLabelText('Cash reserve', { exact: true })).toHaveValue(
      'not yet',
    );
    page.view.unmount();
    page.data.stop();
    const reloaded = await open(() => INITIAL, { storage: page.storage });
    await edit(reloaded);
    expect(within(policy('main_list')).getByLabelText('Cash reserve', { exact: true })).toHaveValue(
      'not yet',
    );
    expect(postBodies(page.fake, PATCH)).toEqual([]);
    expect(postBodies(reloaded.fake, PATCH)).toEqual([]);
  });

  it('SQ9 later typing survives the positive receipt of the earlier submitted policy', async () => {
    const answer = sample('shopping_shared_v2_updated');
    let source = sourceBefore(answer);
    const page = await open(() => source, {
      before: (fake) => {
        fake.route('POST', PATCH, () => 'hold');
      },
    });
    await weighted(page);
    await page.user.click(
      within(policy('main_list')).getByRole('button', { name: 'Save shared policy' }),
    );
    await page.advance(10);
    const input = within(policy('main_list')).getByLabelText('Cash reserve', { exact: true });
    await page.user.clear(input);
    await page.user.type(input, '801');
    // Exact recorded receipt becomes the next source; only the newer typing must remain.
    source = {
      ...INITIAL,
      body: {
        ...Object.fromEntries(entries(INITIAL.body)),
        profile: own(answer.body, 'profile'),
        revision: own(answer.body, 'revision'),
      },
    };
    act(() => {
      page.fake.next(PATCH, 'POST').respond(answer.status, answer.body);
    });
    await page.advance(100);
    expect(input).toHaveValue('801');
    expect(postBodies(page.fake, PATCH)).toEqual([answer.request.body]);
  });
});

describe('SQ10 legacy shopping directives', () => {
  it('SQ10 keeps a newer directive edit attached to its original identity after a reordered save, readback and reload', async () => {
    const recorded = sample('shopping_legacy_list_updated');
    let source = sourceBefore(recorded);
    const page = await open(() => source, {
      before: (fake) => {
        fake.route('POST', PATCH, () => 'hold');
      },
    });
    await edit(page, 'legacy');
    const health = policy('legacy').querySelector<HTMLElement>('[data-library-directive="line:1"]');
    if (health === null) throw new Error('missing health directive');
    await page.user.click(within(health).getByRole('button', { name: 'Up' }));
    await page.user.click(
      within(policy('legacy')).getByRole('button', { name: 'Save shared policy' }),
    );
    await page.advance(10);
    await page.user.click(within(health).getByLabelText('Directive enabled'));
    await page.user.clear(within(health).getByLabelText('Clicks', { exact: true }));
    await page.user.type(within(health).getByLabelText('Clicks', { exact: true }), '5');
    const sent = postBodies(page.fake, PATCH)[0];
    // This race needs a reordered successful receipt absent from the recording. Change only
    // the recorded receipt's legacy value to the exact submitted order; keep its entire
    // response envelope, profile siblings and revision. The backend answer is not invented.
    const profile = own(recorded.body, 'profile');
    const policies = own(profile, 'policies');
    const response = {
      ...Object.fromEntries(entries(recorded.body)),
      profile: {
        ...Object.fromEntries(entries(profile)),
        policies: {
          ...Object.fromEntries(entries(policies)),
          shopping_lists: {
            ...Object.fromEntries(entries(own(policies, 'shopping_lists'))),
            legacy: own(sent, 'value'),
          },
        },
      },
    };
    source = {
      ...INITIAL,
      body: {
        ...Object.fromEntries(entries(INITIAL.body)),
        profile: own(response, 'profile'),
        revision: own(response, 'revision'),
      },
    };
    act(() => {
      page.fake.next(PATCH, 'POST').respond(recorded.status, response);
    });
    await page.advance(100);
    const check = () => {
      const vm = selectShoppingLibrary(page.data.store.getSnapshot()).policies.find(
        (item) => item.id === 'legacy',
      );
      expect(
        vm?.legacy?.lines.map((line) => ({
          stats: line.stats,
          enabled: line.enabled,
          clicks: line.clicks,
        })),
      ).toEqual([
        { stats: ['health'], enabled: false, clicks: '5' },
        { stats: ['damage'], enabled: true, clicks: '' },
      ]);
    };
    check();
    const vm = selectShoppingLibrary(page.data.store.getSnapshot()).policies.find(
      (item) => item.id === 'legacy',
    );
    expect(vm?.reason).toBe('revision');
    page.view.unmount();
    page.data.stop();
    const reloaded = await open(() => source, { storage: page.storage });
    await edit(reloaded, 'legacy');
    const restored = selectShoppingLibrary(reloaded.data.store.getSnapshot()).policies.find(
      (item) => item.id === 'legacy',
    );
    expect(restored?.legacy?.lines[0]).toMatchObject({
      stats: ['health'],
      enabled: false,
      clicks: '5',
    });
    expect(postBodies(reloaded.fake, PATCH)).toEqual([]);
  });

  it('SQ10 preserves a bare list, offers vocabulary choices, and keeps keyboard focus through add/move/remove', async () => {
    const answer = sample('shopping_legacy_list_updated');
    const page = await open(() => sourceBefore(answer), {
      before: (fake) => {
        fake.route('POST', PATCH, () => answer);
      },
    });
    const box = await edit(page, 'legacy');
    await page.user.click(box.getByRole('button', { name: 'Add directive' }));
    let lines = policy('legacy').querySelectorAll<HTMLElement>('[data-library-directive]');
    const added = lines[lines.length - 1];
    if (added === undefined) throw new Error('missing new directive');
    const up = within(added).getByRole('button', { name: 'Up' });
    up.focus();
    await page.user.keyboard('{Enter}');
    expect(document.activeElement).toBe(up);
    await page.user.click(within(added).getByRole('button', { name: 'Remove directive' }));
    lines = policy('legacy').querySelectorAll<HTMLElement>('[data-library-directive]');
    expect(lines).toHaveLength(2);
    await send(page, 'legacy');
    expect(postBodies(page.fake, PATCH)).toEqual([answer.request.body]);
    expect(box.getByText('Legacy shopping has no cash reserve protection.')).toBeVisible();
  });

  it('SQ10 edits a legacy mapping and retains all optional and hidden members', async () => {
    const answer = sample('shopping_legacy_mapping_updated');
    const page = await open(() => sourceBefore(answer), {
      before: (fake) => {
        fake.route('POST', PATCH, () => answer);
      },
    });
    const box = await edit(page, 'legacy');
    // Build the exact recorded mapping from the preceding bare list only by supplying a
    // recorded mapping source for this shape-specific editor test.
    const state = page.data.store.getSnapshot();
    const vm = selectShoppingLibrary(state).policies.find((item) => item.id === 'legacy');
    if (vm === undefined) throw new Error('missing legacy model');
    act(() => {
      page.data.commands.editDraft(
        vm.scope,
        'library_base',
        JSON.stringify(own(answer.request.body, 'value')),
      );
      page.data.commands.editDraft(
        vm.scope,
        'library_revision',
        String(own(answer.request.body, 'expected_revision')),
      );
    });
    await page.user.click(box.getByLabelText('Policy enabled'));
    await page.user.click(box.getByLabelText('Policy enabled'));
    await page.user.clear(box.getByLabelText('Policy label'));
    await page.user.type(box.getByLabelText('Policy label'), 'Legacy');
    const line = policy('legacy').querySelector<HTMLElement>('[data-library-directive="line:0"]');
    if (line === null) throw new Error('missing directive');
    const directive = within(line);
    await page.user.click(directive.getByLabelText('Directive enabled'));
    await page.user.click(directive.getByLabelText('Directive enabled'));
    await page.user.selectOptions(directive.getByLabelText('Shop tab'), 'utility');
    await page.user.selectOptions(directive.getByLabelText('Shop tab'), 'attack');
    await page.user.click(directive.getByLabelText('health', { exact: true }));
    await page.user.click(directive.getByLabelText('health', { exact: true }));
    for (const mode of ['once', 'best_cost', 'clicks', 'repeat']) {
      await page.user.selectOptions(directive.getByLabelText('Shopping mode'), mode);
    }
    await send(page, 'legacy');
    expect(postBodies(page.fake, PATCH)).toEqual([answer.request.body]);
    expect(textOutsideCatalogue(root())).toEqual([]);
  });

  it('SQ10 clicks zero reaches the recorded 400 refusal, empty/non-numeric clicks stay raw and drafts survive failed reads', async () => {
    let source = INITIAL;
    const answer = sample('shopping_legacy_clicks_invalid');
    const page = await open(() => source, {
      before: (fake) => {
        fake.route('POST', PATCH, () => answer);
      },
    });
    await edit(page, 'legacy');
    const vm = selectShoppingLibrary(page.data.store.getSnapshot()).policies.find(
      (item) => item.id === 'legacy',
    );
    if (vm === undefined) throw new Error('missing legacy model');
    act(() => {
      page.data.commands.editDraft(
        vm.scope,
        'library_base',
        JSON.stringify(own(answer.request.body, 'value')),
      );
      page.data.commands.editDraft(
        vm.scope,
        'library_revision',
        String(own(INITIAL.body, 'revision')),
      );
    });
    const input = within(policy('legacy')).getByLabelText('Clicks', { exact: true });
    await page.user.clear(input);
    await page.user.type(input, 'bad');
    await send(page, 'legacy');
    expect(postBodies(page.fake, PATCH)).toEqual([]);
    await page.user.clear(input);
    await page.user.type(input, '0');
    await send(page, 'legacy');
    // This refusal case starts later in the recorded revision sequence; only revision differs.
    expect(postBodies(page.fake, PATCH)).toEqual([
      {
        ...Object.fromEntries(entries(answer.request.body)),
        expected_revision: own(INITIAL.body, 'revision'),
      },
    ]);
    expect(input).toHaveValue('0');
    expect(policy('legacy').querySelector('[data-refusal]')).toHaveTextContent(
      String(own(answer.body, 'error')),
    );
    // Reuse the recorded 500 as an unavailable profile read; its body is unchanged.
    source = sample('shopping_shared_reply_failed');
    await page.user.click(within(root()).getByRole('button', { name: 'Check shared policies' }));
    await page.advance(100);
    expect(within(policy('legacy')).getByLabelText('Clicks', { exact: true })).toHaveValue('0');
  });
});

it.each(['same', 'changed'])(
  'SQ5 shopping library edits wait for the confirmed context (%s)',
  async (startup) => {
    const page = await open(() => INITIAL, {
      storage: createMemoryStorage({
        'tp.context.v1': JSON.stringify({
          ...contextFromConfig(CONFIG),
          ...(startup === 'changed' ? { serial: 'old:1' } : {}),
        }),
      }),
      before: (fake) => {
        fake.route('GET', '/api/config', () => 'hold');
      },
    });
    const before = policy('main_list');
    await page.user.click(within(before).getByText('Edit shared policy', { exact: true }));
    expect(within(before).queryByRole('textbox', { name: 'Cash reserve' })).toBeNull();
    expect(before).toHaveTextContent('Waiting for the emulator, account and profile to be read.');
    expect(page.data.store.getSnapshot().runDrafts).toEqual({});
    act(() => {
      page.fake.next('/api/config').respond(200, CONFIG);
    });
    await page.advance(100);
    const current = policy('main_list');
    const toggle = within(current).getByText('Edit shared policy', { exact: true });
    if (!current.querySelector('details[open]')) await page.user.click(toggle);
    const reserve = within(current).getByRole('textbox', { name: 'Cash reserve' });
    await page.user.clear(reserve);
    await page.user.type(reserve, '12345');
    expect(
      selectShoppingLibrary(page.data.store.getSnapshot()).policies.find(
        (policy) => policy.id === 'main_list',
      )?.draft['reserve'],
    ).toBe('12345');
  },
);
