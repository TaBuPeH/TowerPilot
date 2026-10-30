import { act, within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { entries, own, ownPath } from '../../../../src/core/read.ts';
import { selectShoppingSaved } from '../../../../src/store/selectors/shoppingSaved.ts';
import { contextFromConfig } from '../../../../src/api/context.ts';
import { createMemoryStorage } from '../../../support/fakeTransport.ts';
import { postBodies } from '../../../support/dataLayer.ts';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import {
  dashboardPagesCase,
  dashboardCase,
  type DashboardCase,
} from '../../../support/dashboardFixture.ts';
import { bounds, boundsDraft, numericBoundsCases } from '../../../support/shoppingBounds.ts';
import { renderPage, type PageOptions } from '../dashboard/harness.tsx';

const sample = (name: string) => dashboardPagesCase(`shopping_queue.${name}`);
const CONFIG = dashboardPagesCase('config_state.read_before_the_save').body;
const PATH = '/api/run-controls?blueprint=farm';
function root() {
  const box = document.querySelector<HTMLElement>('[data-shopping-saved]');
  if (box === null) throw new Error('saved shopping editor missing');
  return box;
}
const form = () => within(root());
const saveButton = () => form().getByRole('button', { name: 'Save queue' });
async function open(
  source: () => DashboardCase = () => sample('shopping_read_before_any_save'),
  options: PageOptions = {},
) {
  window.location.hash = '#runs/saved/farm';
  const page = await renderPage({
    ...options,
    config: options.config ?? (() => CONFIG),
    before: (fake) => {
      fake.route('GET', PATH, () => source());
      options.before?.(fake);
    },
  });
  await page.advance(100);
  return page;
}
async function fill(
  page: Awaited<ReturnType<typeof open>>,
  label: string,
  value: string,
  parent = root(),
) {
  const input = within(parent).getByRole('textbox', { name: label });
  await page.user.clear(input);
  await page.user.type(input, value);
}
function entry(id: string) {
  const box = root().querySelector<HTMLElement>(`.tp-shop__entry[data-upgrade="${id}"]`);
  if (box === null) throw new Error(`missing entry ${id}`);
  return box;
}
const posts = (page: Awaited<ReturnType<typeof open>>) =>
  postBodies(page.fake, '/api/run-controls').map((body) => ({ body }));

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  window.location.hash = '';
  vi.useRealTimers();
});

it.each(['cold', 'same', 'changed'])(
  'SQ5 saved queue edits wait for the confirmed context (%s), including routine edits',
  async (startup) => {
    const context = contextFromConfig(CONFIG);
    const page = await open(undefined, {
      storage: createMemoryStorage(
        startup === 'cold'
          ? {}
          : {
              'tp.context.v1': JSON.stringify({
                ...context,
                ...(startup === 'changed' ? { serial: 'old:1' } : {}),
              }),
            },
      ),
      before: (fake) => {
        fake.route('GET', '/api/config', () => 'hold');
      },
    });
    expect(page.data.store.getSnapshot().contextConfirmed).toBe(false);
    expect(form().queryByRole('textbox', { name: 'Cash reserve' })).toBeNull();
    expect(root()).toHaveTextContent('Waiting for the emulator, account and profile to be read.');
    expect(document.querySelector('[data-scope-kind="saved"] input')).toBeNull();
    expect(page.data.store.getSnapshot().runDrafts).toEqual({});
    act(() => {
      page.fake.next('/api/config').respond(200, CONFIG);
    });
    await page.advance(100);
    expect(page.data.store.getSnapshot().contextConfirmed).toBe(true);
    await fill(page, 'Cash reserve', '12345');
    const scope = selectShoppingSaved(page.data.store.getSnapshot()).scope;
    act(() => {
      page.data.commands.checkRun();
    });
    await page.advance(100);
    expect(selectShoppingSaved(page.data.store.getSnapshot()).scope).toBe(scope);
    expect(form().getByRole('textbox', { name: 'Cash reserve' })).toHaveValue('12345');
    expect(saveButton()).not.toHaveAttribute('aria-disabled', 'true');
  },
);

it('SQ5 saved queue edits wait for the run catalogue to confirm the draft scope', async () => {
  const page = await open(undefined, {
    before: (fake) => {
      fake.route('GET', '/api/runs', () => 'hold');
    },
  });
  expect(page.data.store.getSnapshot().contextConfirmed).toBe(true);
  const before = selectShoppingSaved(page.data.store.getSnapshot());
  expect(before.scope).toMatch(/:farm:shopping_plan$/u);
  expect(form().queryByRole('textbox', { name: 'Cash reserve' })).toBeNull();
  expect(root()).toHaveTextContent('Reading the saved settings');
  expect(document.querySelector('[data-scope-kind="saved"] input')).toBeNull();
  act(() => {
    page.fake.last('/api/runs').respond(200, dashboardCase('runs.ready').body);
  });
  await page.advance(100);
  const after = selectShoppingSaved(page.data.store.getSnapshot());
  expect(after.scope).toMatch(/:bp_farm:shopping_plan$/u);
  await fill(page, 'Cash reserve', '12345');
  act(() => {
    page.data.commands.checkRun();
  });
  await page.advance(100);
  expect(form().getByRole('textbox', { name: 'Cash reserve' })).toHaveValue('12345');
  expect(saveButton()).not.toHaveAttribute('aria-disabled', 'true');
});

it('SQ5 failed startup run catalogue keeps drafts gated and offers a working retry', async () => {
  const page = await open(undefined, {
    before: (fake) => {
      fake.route('GET', '/api/runs', () => 'hold');
    },
  });
  act(() => {
    page.fake.last('/api/runs').lose();
  });
  await page.advance(100);
  expect(form().queryByRole('textbox', { name: 'Cash reserve' })).toBeNull();
  expect(root()).toHaveTextContent(
    'The run list could not be read. Check it again before editing saved settings.',
  );
  expect(root()).not.toHaveTextContent('Reading the saved settings');
  const reads = () =>
    page.fake.requests.filter((read) => read.method === 'GET' && read.path === '/api/runs').length;
  const before = reads();
  page.fake.route('GET', '/api/runs', () => dashboardCase('runs.ready'));
  await page.user.click(within(document.body).getByRole('button', { name: 'Check runs' }));
  await page.advance(100);
  expect(reads()).toBe(before + 1);
  await fill(page, 'Cash reserve', '12345');
  expect(selectShoppingSaved(page.data.store.getSnapshot()).scope).toMatch(
    /:bp_farm:shopping_plan$/u,
  );
  expect(saveButton()).not.toHaveAttribute('aria-disabled', 'true');
});

it('SQ1 Create stages an empty queue and requires explicit reserve, pattern and cadence before one save', async () => {
  const recorded = dashboardPagesCase('run_controls_save.read_before_the_plan_save');
  // Align only the old no-plan read's revision and shopping routine to the newer empty-save
  // recording, matching the browser fake's projection and allowing an exact receipt check.
  const before = sample('shopping_read_before_any_save');
  const initial = {
    ...recorded,
    body: {
      ...Object.fromEntries(entries(recorded.body)),
      profile_revision: own(before.body, 'profile_revision'),
      settings: {
        ...Object.fromEntries(entries(own(recorded.body, 'settings'))),
        routines: {
          ...Object.fromEntries(entries(ownPath(recorded.body, 'settings', 'routines'))),
          shopping: {
            ...Object.fromEntries(
              entries(ownPath(recorded.body, 'settings', 'routines', 'shopping')),
            ),
            interval_sec: ownPath(before.body, 'settings', 'routines', 'shopping', 'interval_sec'),
          },
        },
      },
    },
  };
  const saved = sample('shopping_empty_queue_saved');
  let source: DashboardCase = initial;
  const page = await open(() => source, {
    before: (fake) => {
      fake.route('POST', '/api/run-controls', () => {
        source = saved;
        return saved;
      });
    },
  });
  await page.user.click(form().getByRole('button', { name: 'Create a queue' }));
  expect(posts(page)).toHaveLength(0);
  expect(saveButton()).toHaveAttribute('aria-disabled', 'true');
  await fill(page, 'Cash reserve', '0');
  await page.user.selectOptions(form().getByLabelText('Purchase pattern'), 'highest_affordable');
  await page.user.selectOptions(form().getByLabelText('Cadence'), 'single_attempt');
  expect(saveButton()).not.toHaveAttribute('aria-disabled');
  await page.user.click(saveButton());
  await page.advance(100);
  expect(posts(page).map((call) => call.body)).toEqual([saved.request.body]);
  expect(document.querySelector('[data-outcome="saved"]')).not.toBeNull();
  expect(posts(page)).toHaveLength(1);
});

it('SQ2 Review shows every server warning and full entry/exclusion values; Use reviewed queue stages only, Save installs exact reviewed plan', async () => {
  const initial = sample('shopping_legacy_list_preview');
  const saved = sample('shopping_reviewed_conversion_saved');
  let source: DashboardCase = initial;
  const page = await open(() => source, {
    before: (fake) => {
      fake.route('POST', '/api/run-controls', () => {
        source = sample('shopping_read_after_the_reviewed_conversion');
        return saved;
      });
    },
  });
  const conversion = own(initial.body, 'legacy_conversion');
  for (const warning of own(conversion, 'warnings') as string[])
    expect(form().getByText(warning)).toBeVisible();
  expect(root().querySelectorAll('[data-conversion-entry]')).toHaveLength(2);
  expect(root().querySelector('[data-conversion-entry="damage"]')).toHaveTextContent('Damage');
  expect(root().querySelector('[data-conversion-entry="health"]')).toHaveTextContent('Unlimited');
  expect(form().getByText('Defense Absolute', { exact: true })).toBeVisible();
  expect(posts(page)).toHaveLength(0);
  // F6-12-C-02: the unset recorded reserve blocks Use reviewed queue until one is typed.
  const use = form().getByRole('button', { name: 'Use reviewed queue' });
  expect(use).toHaveAttribute('aria-disabled', 'true');
  expect(use).toHaveAccessibleDescription(
    'Enter a cash reserve first (0 allows spending all cash).',
  );
  await page.user.click(use);
  expect(root().querySelector('[data-shopping-conversion]')).not.toBeNull();
  await fill(page, 'Cash reserve', '0');
  await page.user.click(form().getByRole('button', { name: 'Use reviewed queue' }));
  expect(posts(page)).toHaveLength(0);
  // Staging carries the reviewed reserve: Save is the next and only sending step.
  expect(form().getByRole('textbox', { name: 'Cash reserve' })).toHaveValue('0');
  expect(saveButton()).not.toHaveAttribute('aria-disabled');
  await page.user.click(saveButton());
  await page.advance(100);
  expect(posts(page).map((call) => call.body)).toEqual([saved.request.body]);
  expect(document.querySelector('[data-outcome="saved"]')).not.toBeNull();
});

it('SQ3 SQ5 SQ6 SQ7 saved changes preserve hidden values and send the full reordered plan exactly once', async () => {
  const saved = sample('shopping_weighted_cadence_order_saved');
  let source = sample('shopping_read_before_any_save');
  const page = await open(() => source, {
    before: (fake) => {
      fake.route('POST', '/api/run-controls', () => {
        source = sample('shopping_read_after_save');
        return saved;
      });
    },
  });
  await fill(page, 'Cash reserve', '750.5');
  await page.user.selectOptions(form().getByLabelText('Purchase pattern'), 'weighted_split');
  await page.user.selectOptions(form().getByLabelText('Cadence'), 'while_affordable');
  const cap = within(entry('damage')).getByRole('textbox', {
    name: 'Tap attempts this battle',
  });
  await page.user.clear(cap);
  await fill(page, 'Weight', '3', entry('health'));
  await fill(page, 'Until wave (exclusive)', '20', entry('health'));
  const up = within(entry('health')).getByRole('button', { name: /^Move up/u });
  up.focus();
  await page.user.keyboard('{Enter}');
  expect(up).toHaveFocus();
  expect(
    [...root().querySelectorAll('.tp-shop__entry')].map((box) => box.getAttribute('data-upgrade')),
  ).toEqual(['health', 'damage']);
  await page.user.click(saveButton());
  await page.advance(100);
  expect(posts(page).map((call) => call.body)).toEqual([saved.request.body]);
  expect(selectShoppingSaved(page.data.store.getSnapshot()).discard).toBe(false);
});

it('SQ4 saved picker deselection preserves caps and order; selecting excluded upgrade removes its exclusion', async () => {
  const page = await open();
  await page.user.click(
    within(entry('damage')).getByRole('checkbox', { name: 'Included in queue' }),
  );
  expect(selectShoppingSaved(page.data.store.getSnapshot()).editor.review.plan).toMatchObject({
    queue: [{ id: 'damage', enabled: false, tap_limit: 3 }, { id: 'health' }],
  });
  const picker = root().querySelector<HTMLElement>(
    '.tp-shop__pick[data-upgrade="defense_absolute"]',
  );
  if (picker === null) throw new Error('missing picker');
  await page.user.click(within(picker).getByRole('checkbox', { name: 'Defense Absolute' }));
  const review = selectShoppingSaved(page.data.store.getSnapshot()).editor.review;
  expect(review.excluded).not.toContain('defense_absolute');
  expect(review.entries.at(-1)).toMatchObject({ id: 'defense_absolute', enabled: true });
});

it.each(numericBoundsCases('saved'))(
  'SQ5 saved recorded bounds %s: exact POST, server refusal beside Save, retained raw draft or saved readback',
  async (name) => {
    let source = bounds('read_before_the_refusals');
    const answer = bounds(name);
    const page = await open(() => source, {
      before: (fake) => {
        fake.route('POST', '/api/run-controls', () => answer);
      },
    });
    const vm = selectShoppingSaved(page.data.store.getSnapshot());
    if (vm.scope === null) throw new Error('missing shopping scope');
    const draft = boundsDraft(name);
    act(() => {
      for (const [key, value] of Object.entries(draft))
        page.data.commands.editDraft(vm.scope ?? '', key, value);
    });
    await page.user.click(saveButton());
    await page.advance(100);
    expect(posts(page).map((call) => call.body)).toEqual([answer.request.body]);
    if (answer.status === 400) {
      expect(
        root().querySelector('[data-queue-refusal] [data-server-text="message"]'),
      ).toHaveTextContent(String(own(answer.body, 'error')));
      expect(root().querySelector('[data-queue-refusal]')).toBeVisible();
      expect(document.querySelector('[data-outcome="saved"]')).toBeNull();
      expect(selectShoppingSaved(page.data.store.getSnapshot()).draft).toEqual(draft);
      source = bounds('read_after_the_refusals_nothing_changed');
    } else {
      source = bounds('read_after_the_boundary_values_were_saved');
    }
    act(() => {
      page.data.commands.checkRun();
    });
    await page.advance(100);
    const current = selectShoppingSaved(page.data.store.getSnapshot());
    expect(current.draft).toEqual(answer.status === 400 ? draft : {});
    expect(current.base.plan?.raw).toEqual(ownPath(source.body, 'settings', 'shopping_plan'));
    expect(posts(page)).toHaveLength(1);
  },
);

it('SQ12 saved Unknown is not resent and only a later matching same-context read settles it', async () => {
  let source = sample('shopping_read_before_the_unanswered_save');
  const failed = sample('shopping_save_reply_failed');
  const page = await open(() => source, {
    before: (fake) => {
      fake.route('POST', '/api/run-controls', () => failed);
    },
  });
  // Stage the exact recorded request as a raw draft to isolate recovery from editing coverage.
  const vm = selectShoppingSaved(page.data.store.getSnapshot());
  if (vm.scope === null) throw new Error('no scope');
  act(() => {
    page.data.commands.editDraft(
      vm.scope ?? '',
      'base',
      JSON.stringify(ownPath(failed.request.body, 'changes', 'shopping_plan')),
    );
  });
  await page.user.click(saveButton());
  await page.advance(100);
  expect(document.querySelector('[data-outcome="unconfirmed_save"]')).not.toBeNull();
  expect(saveButton()).toHaveAttribute('aria-disabled', 'true');
  await page.user.click(saveButton());
  await page.advance(5_000);
  expect(posts(page)).toHaveLength(1);
  source = sample('shopping_read_after_unknown_save');
  page.data.commands.checkRun();
  await page.advance(100);
  expect(document.querySelector('[data-outcome="applied"]')).not.toBeNull();
  expect(selectShoppingSaved(page.data.store.getSnapshot()).discard).toBe(false);
  expect(posts(page)).toHaveLength(1);
});

it('SQ11 scoped raw drafts survive polls, failed reads, navigation and reload; Discard leaves routine draft untouched', async () => {
  let source = sample('shopping_read_before_any_save');
  const page = await open(() => source);
  await fill(page, 'Cash reserve', '725.00');
  const scope = selectShoppingSaved(page.data.store.getSnapshot()).scope;
  if (scope === null) throw new Error('no scope');
  act(() => {
    page.data.commands.editDraft(
      scope.replace(':shopping_plan', ''),
      'routines.shopping.interval_sec',
      '90',
    );
  });
  for (let n = 0; n < 3; n += 1) await page.advance(2_500);
  // Recorded refusal used as a failed GET to exercise an unavailable run read, never as a successful answer.
  const focused = form().getByRole('textbox', { name: 'Cash reserve' });
  focused.focus();
  source = sample('shopping_invalid_weight_refused');
  page.data.commands.checkRun();
  await page.advance(100);
  expect(page.data.store.getSnapshot().runDrafts[scope]?.['reserve']).toBe('725.00');
  expect(form().getByRole('textbox', { name: 'Cash reserve' })).toBe(focused);
  expect(focused).toHaveFocus();
  expect(focused).toHaveValue('725.00');
  expect(saveButton()).toHaveAttribute('aria-disabled', 'true');
  source = sample('shopping_read_before_any_save');
  page.data.commands.checkRun();
  await page.advance(100);
  act(() => {
    page.data.commands.openRuns(true, 'other');
  });
  await page.advance(100);
  act(() => {
    page.data.commands.openRuns(true, 'farm');
  });
  await page.advance(100);
  expect(form().getByRole('textbox', { name: 'Cash reserve' })).toHaveValue('725.00');
  const storage = page.storage;
  page.view.unmount();
  page.data.stop();
  const reloaded = await open(() => source, { storage });
  expect(form().getByRole('textbox', { name: 'Cash reserve' })).toHaveValue('725.00');
  await reloaded.user.click(form().getByRole('button', { name: 'Discard queue draft' }));
  expect(form().getByRole('textbox', { name: 'Cash reserve' })).toHaveValue('500');
  expect(reloaded.data.store.getSnapshot().runDrafts[scope.replace(':shopping_plan', '')]).toEqual({
    'routines.shopping.interval_sec': '90',
  });
});

it('SQ7 SQ11 a newer edit after Save stays dirty when its receipt arrives', async () => {
  const saved = sample('shopping_weighted_cadence_order_saved');
  const page = await open(undefined, {
    before: (fake) => {
      fake.route('POST', '/api/run-controls', () => 'hold');
    },
  });
  const vm = selectShoppingSaved(page.data.store.getSnapshot());
  if (vm.scope === null) throw new Error('no scope');
  act(() => {
    page.data.commands.editDraft(
      vm.scope ?? '',
      'base',
      JSON.stringify(ownPath(saved.request.body, 'changes', 'shopping_plan')),
    );
  });
  await page.user.click(saveButton());
  await fill(page, 'Cash reserve', '999');
  act(() => {
    page.fake.next('/api/run-controls', 'POST').respond(saved.status, saved.body);
  });
  await page.advance(100);
  expect(form().getByRole('textbox', { name: 'Cash reserve' })).toHaveValue('999');
  expect(selectShoppingSaved(page.data.store.getSnapshot()).discard).toBe(true);
});

it('SQ12 a read without shopping_plan capability offers no queue Save or editor', async () => {
  const recorded = sample('shopping_other_run_after_private_save');
  // Remove only the capability from the recorded GET to represent an unsupported run kind;
  // the recording family contains a refusal but no GET for a kind lacking the capability.
  const body = {
    ...Object.fromEntries(entries(recorded.body)),
    blueprint: 'farm',
    capabilities: { actions: [], controls: ['routines.shopping.enabled'] },
  };
  await open(() => ({ ...recorded, body }));
  expect(form().queryByRole('button', { name: 'Save queue' })).toBeNull();
  expect(form().getByText(/This run does not support a shopping queue/u)).toBeVisible();
});

it('SQ3 SQ7 saved queue markup is axe clean and every rendered word is catalogue or ServerValue text', async () => {
  await open();
  expect(textOutsideCatalogue(document.body)).toEqual([]);
  vi.useRealTimers();
  expect(await axe(root())).toHaveNoViolations();
  vi.useFakeTimers();
});

it('SQ11 SQ12 Unknown survives reload and a matching plan from another context cannot settle it', async () => {
  let source = sample('shopping_read_before_the_unanswered_save');
  const failed = sample('shopping_save_reply_failed');
  const page = await open(() => source, {
    before: (fake) => {
      fake.route('POST', '/api/run-controls', () => failed);
    },
  });
  const scope = selectShoppingSaved(page.data.store.getSnapshot()).scope;
  if (scope === null) throw new Error('missing scope');
  act(() => {
    page.data.commands.editDraft(
      scope,
      'base',
      JSON.stringify(ownPath(failed.request.body, 'changes', 'shopping_plan')),
    );
  });
  await page.user.click(saveButton());
  await page.advance(100);
  page.view.unmount();
  page.data.stop();
  const reloaded = await open(() => source, { storage: page.storage });
  expect(document.querySelector('[data-outcome="unconfirmed_save"]')).not.toBeNull();
  expect(saveButton()).toHaveAttribute('aria-disabled', 'true');
  const correct = sample('shopping_read_after_unknown_save');
  // Only the read context is changed: positive values from another account prove nothing.
  source = {
    ...correct,
    body: {
      ...Object.fromEntries(entries(correct.body)),
      context: { instance: 'main', account: 'another', profile: 'mine' },
    },
  };
  reloaded.data.commands.checkRun();
  await reloaded.advance(100);
  expect(reloaded.data.store.getSnapshot().runDrafts[scope]).toBeDefined();
  expect(document.querySelector('[data-outcome="applied"]')).toBeNull();
  source = correct;
  reloaded.data.commands.checkRun();
  await reloaded.advance(100);
  expect(document.querySelector('[data-outcome="applied"]')).not.toBeNull();
  expect(reloaded.data.store.getSnapshot().runDrafts[scope]).toBeUndefined();
  expect(posts(reloaded)).toHaveLength(0);
});

it('SQ12 lost saved answer keeps draft, never resends, and permits explicit continue from a later unchanged read', async () => {
  const page = await open(undefined, {
    before: (fake) => {
      fake.route('POST', '/api/run-controls', () => 'hold');
    },
  });
  await fill(page, 'Cash reserve', '700');
  await page.user.click(saveButton());
  act(() => {
    page.fake.next('/api/run-controls', 'POST').lose();
  });
  await page.advance(100);
  expect(document.querySelector('[data-outcome="unknown_save"]')).not.toBeNull();
  page.data.commands.checkRun();
  await page.advance(100);
  expect(saveButton()).toHaveAttribute('aria-disabled', 'true');
  await page.user.click(
    within(document.body).getByRole('button', { name: 'Continue from the saved values as read' }),
  );
  expect(saveButton()).not.toHaveAttribute('aria-disabled');
  expect(form().getByRole('textbox', { name: 'Cash reserve' })).toHaveValue('700');
  expect(posts(page)).toHaveLength(1);
});

it('SQ2 legacy mapping retains disabled master and every warning and exclusion while staging', async () => {
  const recorded = sample('shopping_legacy_mapping_preview');
  const page = await open(() => recorded);
  expect(root().querySelectorAll('[data-conversion-entry]')).toHaveLength(2);
  expect(form().getByText('Off', { exact: true })).toBeVisible();
  expect(textOutsideCatalogue(document.body)).toEqual([]);
  await fill(page, 'Cash reserve', '0');
  await page.user.click(form().getByRole('button', { name: 'Use reviewed queue' }));
  await fill(page, 'Cash reserve', '0');
  expect(selectShoppingSaved(page.data.store.getSnapshot()).editor.review.plan).toMatchObject({
    enabled: false,
    excluded_upgrades: ['defense_absolute'],
    queue: [
      { id: 'damage', tap_limit: 1 },
      { id: 'health', tap_limit: null, weight: 3 },
    ],
  });
  expect(posts(page)).toHaveLength(0);
});

it('SQ7 saved farm queue never edits the other run or its scope', async () => {
  const other = sample('shopping_other_run_after_private_save');
  const page = await open(undefined, {
    before: (fake) => {
      fake.route('GET', '/api/run-controls?blueprint=other', () => other);
    },
  });
  await fill(page, 'Cash reserve', '999');
  const farmScope = selectShoppingSaved(page.data.store.getSnapshot()).scope;
  act(() => {
    page.data.commands.openRuns(true, 'other');
  });
  await page.advance(100);
  expect(form().getByRole('textbox', { name: 'Cash reserve' })).toHaveValue('500');
  expect(selectShoppingSaved(page.data.store.getSnapshot()).discard).toBe(false);
  expect(page.data.store.getSnapshot().runDrafts[farmScope ?? '']?.['reserve']).toBe('999');
  expect(posts(page)).toHaveLength(0);
});

it('SQ11 a profile context switch isolates and restores the queue draft', async () => {
  let config = CONFIG;
  const page = await open(undefined, { config: () => config });
  await fill(page, 'Cash reserve', '725.00');
  const scope = selectShoppingSaved(page.data.store.getSnapshot()).scope;
  // Change only the config recording's selected profile to exercise the epoch/storage fence.
  // The old run read remains tied to mine and cannot become an editor for theirs.
  config = { ...Object.fromEntries(entries(CONFIG)), active_profile: 'theirs' };
  page.data.poller.read('config');
  await page.advance(100);
  const changed = selectShoppingSaved(page.data.store.getSnapshot());
  expect(changed.scope).not.toBe(scope);
  expect(changed.available).toBe(false);
  expect(page.data.store.getSnapshot().runDrafts[scope ?? '']?.['reserve']).toBe('725.00');
  config = CONFIG;
  page.data.poller.read('config');
  await page.advance(100);
  page.data.commands.checkRun();
  await page.advance(100);
  expect(form().getByRole('textbox', { name: 'Cash reserve' })).toHaveValue('725.00');
  expect(posts(page)).toHaveLength(0);
});

it('SQ2 conversion review includes disabled entries without filtering their caps or place', async () => {
  const recorded = sample('shopping_legacy_list_preview');
  const conversion = own(recorded.body, 'legacy_conversion');
  const plan = own(conversion, 'plan');
  // This recording has no disabled line. Disable only its first converted entry to verify
  // review visibility and staging preservation; keep all other recorded values unchanged.
  const rawQueue = own(plan, 'queue') as Record<string, unknown>[];
  const modifiedPlan = {
    ...Object.fromEntries(entries(plan)),
    queue: rawQueue.map((item, index) => (index === 0 ? { ...item, enabled: false } : item)),
  };
  const modified = {
    ...recorded,
    body: {
      ...Object.fromEntries(entries(recorded.body)),
      legacy_conversion: { ...Object.fromEntries(entries(conversion)), plan: modifiedPlan },
    },
  };
  const page = await open(() => modified);
  const disabled = root().querySelector<HTMLElement>('[data-conversion-entry="damage"]');
  if (disabled === null) throw new Error('disabled entry missing');
  expect(within(disabled).getByText('Left out of queue')).toBeVisible();
  expect([...disabled.querySelectorAll('dd')].map((node) => node.textContent)).toEqual([
    '1',
    '1',
    '1',
    'No last wave',
  ]);
  await fill(page, 'Cash reserve', '0');
  await page.user.click(form().getByRole('button', { name: 'Use reviewed queue' }));
  await fill(page, 'Cash reserve', '0');
  expect(selectShoppingSaved(page.data.store.getSnapshot()).editor.review.plan).toMatchObject({
    queue: [
      { id: 'damage', enabled: false, tap_limit: 1 },
      { id: 'health', weight: 3 },
    ],
  });
  expect(posts(page)).toHaveLength(0);
});

it('SQ3 SQ7 staged conversion preserves an interval subsequently saved through routine controls', async () => {
  const initial = sample('shopping_legacy_list_preview');
  const routine = ownPath(initial.body, 'settings', 'routines', 'shopping');
  const converted = own(initial.body, 'legacy_conversion');
  // The recordings do not contain a routine interval save between review and installation.
  // Change only its interval and revision in this recorded read/receipt envelope to model
  // that intervening routine save, leaving the reviewed queue, caps, order and warnings intact.
  const updated: DashboardCase = {
    ...initial,
    body: {
      ...Object.fromEntries(entries(initial.body)),
      profile_revision: own(sample('shopping_reviewed_conversion_saved').body, 'profile_revision'),
      settings: {
        ...Object.fromEntries(entries(own(initial.body, 'settings'))),
        routines: {
          ...Object.fromEntries(entries(ownPath(initial.body, 'settings', 'routines'))),
          shopping: { ...Object.fromEntries(entries(routine)), interval_sec: 60 },
        },
      },
      legacy_conversion: {
        ...Object.fromEntries(entries(converted)),
        plan: {
          ...Object.fromEntries(entries(own(converted, 'plan'))),
          interval_sec: 60,
        },
      },
    },
  };
  let source = initial;
  const page = await open(() => source, {
    before: (fake) => {
      fake.route('POST', '/api/run-controls', (request) => {
        const body: unknown = JSON.parse(request.body ?? 'null');
        if (ownPath(body, 'changes', 'routines.shopping.interval_sec') === 60) {
          source = updated;
          return updated;
        }
        return 'hold';
      });
    },
  });
  await fill(page, 'Cash reserve', '0');
  await page.user.click(form().getByRole('button', { name: 'Use reviewed queue' }));
  await fill(page, 'Cash reserve', '0');
  const interval = within(document.body).getByRole('textbox', { name: 'Shopping Every' });
  await page.user.clear(interval);
  await page.user.type(interval, '60');
  await page.user.click(within(document.body).getByRole('button', { name: 'Save run settings' }));
  await page.advance(100);
  expect(interval).toHaveValue('60');
  expect(selectShoppingSaved(page.data.store.getSnapshot()).editor.review.plan).toMatchObject({
    interval_sec: 60,
  });
  await page.user.click(saveButton());
  expect(ownPath(posts(page)[1]?.body, 'changes', 'shopping_plan')).toEqual({
    ...Object.fromEntries(entries(own(converted, 'plan'))),
    cash_reserve: 0,
    interval_sec: 60,
  });
  expect(posts(page)).toHaveLength(2);
});

/** The recorded no-plan read with the newer empty-save world's revision and interval (as SQ1). */
function noPlanRead(): DashboardCase {
  const recorded = dashboardPagesCase('run_controls_save.read_before_the_plan_save');
  const before = sample('shopping_read_before_any_save');
  return {
    ...recorded,
    body: {
      ...Object.fromEntries(entries(recorded.body)),
      profile_revision: own(before.body, 'profile_revision'),
      settings: {
        ...Object.fromEntries(entries(own(recorded.body, 'settings'))),
        routines: {
          ...Object.fromEntries(entries(ownPath(recorded.body, 'settings', 'routines'))),
          shopping: {
            ...Object.fromEntries(
              entries(ownPath(recorded.body, 'settings', 'routines', 'shopping')),
            ),
            interval_sec: ownPath(before.body, 'settings', 'routines', 'shopping', 'interval_sec'),
          },
        },
      },
    },
  };
}

it.each([
  ['create', 'shopping_empty_queue_saved'],
  ['conversion', 'shopping_reviewed_conversion_saved'],
])(
  'SQ11 F6-12-C-01 a newer reserve typed during a staged %s save keeps its queue after the receipt, a failed read and a reload',
  async (kind, savedCase) => {
    const saved = sample(savedCase);
    const first = kind === 'create' ? noPlanRead() : sample('shopping_legacy_list_preview');
    const page = await open(() => first, {
      before: (fake) => {
        fake.route('POST', '/api/run-controls', () => 'hold');
      },
    });
    if (kind === 'create') {
      await page.user.click(form().getByRole('button', { name: 'Create a queue' }));
      await fill(page, 'Cash reserve', '0');
      await page.user.selectOptions(
        form().getByLabelText('Purchase pattern'),
        'highest_affordable',
      );
      await page.user.selectOptions(form().getByLabelText('Cadence'), 'single_attempt');
    } else {
      await fill(page, 'Cash reserve', '0');
      await page.user.click(form().getByRole('button', { name: 'Use reviewed queue' }));
    }
    await page.user.click(saveButton());
    await fill(page, 'Cash reserve', '5');
    // The follow-up read of the run fails: no read holds the saved plan yet.
    page.fake.route('GET', PATH, () => 'hold');
    act(() => {
      page.fake.next('/api/run-controls', 'POST').respond(saved.status, saved.body);
    });
    await page.advance(100);
    for (const held of page.fake.held) if (!held.settled()) held.lose();
    await page.advance(100);
    const scope = selectShoppingSaved(page.data.store.getSnapshot()).scope ?? '';
    expect(page.data.store.getSnapshot().runDrafts[scope]?.['base']).toBeDefined();
    expect(form().getByRole('textbox', { name: 'Cash reserve' })).toHaveValue('5');
    const storage = page.storage;
    page.view.unmount();
    page.data.stop();
    const reloaded = await open(() => first, { storage });
    reloaded.fake.route('GET', PATH, () => 'hold');
    await reloaded.advance(100);
    expect(form().getByRole('textbox', { name: 'Cash reserve' })).toHaveValue('5');
    expect(posts(reloaded)).toHaveLength(0);
  },
);

it('SQ3 F6-12-C-03 an unknown restored pattern stays shown with an error and sends nothing', async () => {
  const page = await open();
  const scope = selectShoppingSaved(page.data.store.getSnapshot()).scope ?? '';
  // A restored draft value the editor does not offer (for example from an older app).
  act(() => {
    page.data.commands.editDraft(scope, 'mode', 'cheapest_first');
    page.data.commands.editDraft(scope, 'reserve', '600');
  });
  const pattern = form().getByRole('combobox', { name: 'Purchase pattern' });
  expect(pattern).toHaveValue('cheapest_first');
  expect(pattern).toHaveAttribute('aria-invalid', 'true');
  expect(pattern).toHaveAccessibleDescription(/Not a choice this editor can send/u);
  expect(saveButton()).toHaveAttribute('aria-disabled', 'true');
  await page.user.click(saveButton());
  await page.advance(100);
  expect(posts(page)).toHaveLength(0);
  await page.user.selectOptions(pattern, 'round_robin');
  expect(saveButton()).not.toHaveAttribute('aria-disabled');
});

it('SQ7 F6-12-C-05 the saved queue folds by its named summary and keeps its draft', async () => {
  const page = await open();
  await fill(page, 'Cash reserve', '321');
  const details = root().querySelector('details.tp-saved-shopping__details');
  if (!(details instanceof HTMLDetailsElement)) throw new Error('no saved queue disclosure');
  const summary = within(details).getByText('Edit the saved queue');
  expect(details.open).toBe(true);
  // jsdom toggles a summary on click; the browser spec covers the keyboard toggle.
  await page.user.click(summary);
  expect(details.open).toBe(false);
  await page.user.click(summary);
  expect(details.open).toBe(true);
  expect(form().getByRole('textbox', { name: 'Cash reserve' })).toHaveValue('321');
});
