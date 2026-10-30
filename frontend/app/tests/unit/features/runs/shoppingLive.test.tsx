import { act, within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { LiveShopping } from '../../../../src/features/runs/LiveShopping.tsx';
import { entries, list, own, ownPath } from '../../../../src/core/read.ts';
import { selectShoppingLive } from '../../../../src/store/selectors/shoppingLive.ts';
import { selectRunsPage } from '../../../../src/store/selectors/runs.ts';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import { postBodies } from '../../../support/dataLayer.ts';
import { dashboardBody, dashboardPagesCase } from '../../../support/dashboardFixture.ts';
import { bounds, boundsDraft, numericBoundsCases } from '../../../support/shoppingBounds.ts';
import { addBody, addFailure, regionState, tick } from '../../store/regionHelpers.ts';
import { renderPage, renderRegion } from '../dashboard/harness.tsx';
const sample = (name: string) => dashboardPagesCase(`shopping_queue.${name}`);
/** A region state whose page context the config read has confirmed (the live editor needs it). */
const confirmedState = () => ({ ...regionState(), contextConfirmed: true });
const record = (value: unknown) => Object.fromEntries(entries(value));
/** The fixture records the read after admission. Undo only admission for the pre-click UI; keep its effective plan and report. */
function before(name = 'shopping_plan_read_requested') {
  const body = sample(name).body;
  return {
    ...record(body),
    battle: {
      ...record(own(body, 'battle')),
      control_revision: 0,
      applied_revision: 0,
      overrides: {},
      applied_overrides: {},
      commands: [],
    },
  };
}
const CONFIG = dashboardPagesCase(
  'run_templates_add_fenced.read_config_the_page_context_comes_from',
).body;
const root = () => {
  const element = document.querySelector<HTMLElement>('.tp-shopping-live');
  if (element === null) throw new Error('missing live shopping');
  return element;
};
async function open(readBody: () => unknown, key = 'sq-1') {
  window.location.hash = '#runs/live';
  const page = await renderPage({
    config: () => CONFIG,
    newKey: () => key,
    before: (fake) => {
      fake.route('GET', '/api/battles/active', () => ({ status: 200, body: readBody() }));
    },
  });
  await page.advance(100);
  return page;
}
function stage(page: Awaited<ReturnType<typeof open>>, caseName = 'shopping_plan_accepted') {
  const vm = selectShoppingLive(page.data.store.getSnapshot());
  const plan = ownPath(sample(caseName).request.body, 'changes', 'shopping_plan');
  act(() => {
    page.data.commands.editDraft(vm.scope, 'base', JSON.stringify(plan));
  });
}
beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  window.location.hash = '';
  vi.useRealTimers();
});
it('SQ7 SQ12 Save sends the full frozen plan once; acceptance clears the submitted draft while the editor keeps applied values until the matching applied read', async () => {
  let source: unknown = before();
  const page = await open(() => source);
  const accepted = sample('shopping_plan_accepted');
  page.fake.route('POST', accepted.request.path, () => {
    source = sample('shopping_plan_read_requested').body;
    return accepted;
  });
  stage(page);
  const scope = selectShoppingLive(page.data.store.getSnapshot()).scope;
  await page.user.click(within(root()).getByRole('button', { name: 'Save queue for this battle' }));
  await page.advance(500);
  expect(postBodies(page.fake, accepted.request.path)).toEqual([accepted.request.body]);
  expect(selectRunsPage(page.data.store.getSnapshot()).live.outcome?.code).toBe('live_recorded');
  expect(selectShoppingLive(page.data.store.getSnapshot()).base.plan?.raw['cash_reserve']).toBe(
    500,
  );
  // LIVE-INT-01: the receipt clears the submitted values (the read says the change waits).
  expect(Object.keys(page.data.store.getSnapshot().runDrafts[scope] ?? {})).toHaveLength(0);
  source = sample('shopping_plan_read_applied').body;
  act(() => {
    page.data.commands.checkBattle();
  });
  await page.advance(500);
  expect(selectRunsPage(page.data.store.getSnapshot()).live.outcome?.code).toBe('live_applied');
  expect(Object.keys(page.data.store.getSnapshot().runDrafts[scope] ?? {})).toHaveLength(0);
  expect(selectShoppingLive(page.data.store.getSnapshot()).base.plan?.raw['cash_reserve']).toBe(
    750.5,
  );
});
it('SQ12 Unknown survives failed reads and a key conflict; explicit resend reuses identical body and key; exact-key read settles it', async () => {
  let source: unknown = before('shopping_plan_unknown_read');
  const page = await open(() => source, 'sq-u1');
  const unknown = sample('shopping_plan_reply_unknown');
  page.fake.route('POST', unknown.request.path, () => unknown);
  stage(page, 'shopping_plan_reply_unknown');
  await page.user.click(within(root()).getByRole('button', { name: 'Save queue for this battle' }));
  await page.advance(500);
  expect(selectRunsPage(page.data.store.getSnapshot()).live.outcome?.word).toBe('unknown');
  const first = postBodies(page.fake, unknown.request.path)[0];
  await page.advance(4000);
  expect(postBodies(page.fake, unknown.request.path)).toHaveLength(1);
  // Use the recorded key-conflict refusal as a replay refusal: even a refused identical replay cannot settle the first unknown admission.
  page.fake.route('POST', unknown.request.path, () => sample('shopping_plan_key_conflict'));
  await page.user.click(
    within(root().closest('[data-region]') ?? root()).getByRole('button', {
      name: 'Send same request again',
    }),
  );
  await page.advance(500);
  expect(selectRunsPage(page.data.store.getSnapshot()).live.outcome?.word).toBe('unknown');
  page.fake.route('POST', unknown.request.path, () => sample('shopping_plan_identical_replay'));
  await page.user.click(
    within(root().closest('[data-region]') ?? root()).getByRole('button', {
      name: 'Send same request again',
    }),
  );
  await page.advance(500);
  expect(postBodies(page.fake, unknown.request.path)).toEqual([first, first, first]);
  source = sample('shopping_plan_unknown_read').body;
  act(() => {
    page.data.commands.checkBattle();
  });
  await page.advance(500);
  expect(selectRunsPage(page.data.store.getSnapshot()).live.outcome?.code).toBe('live_recorded');
});
it('SQ7 routine overrides stay visible and a plan save preserves them', async () => {
  const requested = sample('shopping_plan_requested_over_routine_overrides_read').body;
  const battle = own(requested, 'battle');
  // Undo only the second (plan) admission: the first applied routine command and its overrides stay intact.
  let source: unknown = {
    ...record(requested),
    battle: {
      ...record(battle),
      control_revision: 1,
      overrides: own(battle, 'applied_overrides'),
      commands: (list(own(battle, 'commands')) ?? []).slice(0, 1),
    },
  };
  const page = await open(() => source, 'sq-o2');
  const accepted = sample('shopping_plan_preserves_routine_overrides');
  page.fake.route('POST', accepted.request.path, () => {
    source = requested;
    return accepted;
  });
  const routineOverrides = () => {
    const routine = document.querySelector(
      '.tp-run-editor[data-scope-kind="live"] [data-routine="shopping"]',
    );
    if (routine === null) throw new Error('missing live shopping routine');
    expect(routine.querySelector('[data-field="enabled"] input')).not.toBeChecked();
    expect(routine.querySelector('[data-field="interval_sec"] input')).toHaveValue('60');
    expect(routine).toBeVisible();
  };
  routineOverrides();
  stage(page, 'shopping_plan_preserves_routine_overrides');
  await page.user.click(within(root()).getByRole('button', { name: 'Save queue for this battle' }));
  await page.advance(100);
  expect(postBodies(page.fake, accepted.request.path)).toEqual([accepted.request.body]);
  routineOverrides();
  expect(selectShoppingLive(page.data.store.getSnapshot()).base.plan?.raw['cash_reserve']).toBe(
    500,
  );
  source = sample('shopping_plan_routine_overrides_applied').body;
  act(() => {
    page.data.commands.checkBattle();
  });
  await page.advance(100);
  routineOverrides();
  expect(selectRunsPage(page.data.store.getSnapshot()).live.outcome?.code).toBe('live_applied');
  expect(textOutsideCatalogue(root())).toEqual([]);
});
it('SQ7 SQ12 drafts stay with battle generation, stale reports and unsupported legacy queues send nothing', () => {
  let state = addBody(confirmedState(), 'battlesActive', sample('shopping_plan_read_applied').body);
  const vm = selectShoppingLive(state);
  state = { ...state, runDrafts: { [vm.scope]: { reserve: '1234' } } };
  expect(selectShoppingLive(tick(state, 300)).reason).toBe('report_stale');
  // Change only generation to model a successor; it must not inherit this draft.
  const successor = {
    ...record(sample('shopping_plan_read_applied').body),
    battle: {
      ...record(own(sample('shopping_plan_read_applied').body, 'battle')),
      generation: 'successor',
    },
  };
  expect(selectShoppingLive(addBody(state, 'battlesActive', successor)).draft).toEqual({});
  const legacy = addBody(
    confirmedState(),
    'battlesActive',
    dashboardBody('battles_active.legacy_shopping_plan'),
  );
  const h = renderRegion(legacy, <LiveShopping />);
  expect(h.view.queryByRole('textbox')).toBeNull();
  expect(h.view.getByText(/Live queue editing needs/)).toBeVisible();
  expect(h.commands.saveLive).not.toHaveBeenCalled();
});
it('SQ7 live queue is axe clean and uses only catalogue or server text', async () => {
  vi.useRealTimers();
  const h = renderRegion(
    addBody(confirmedState(), 'battlesActive', sample('shopping_plan_read_applied').body),
    <LiveShopping />,
  );
  expect(textOutsideCatalogue(h.view.container)).toEqual([]);
  expect(await axe(h.view.container)).toHaveNoViolations();
});

it('SQ12 live raw drafts survive failed reads and a reload; newer typing stays after an applied read', async () => {
  let source: unknown = before();
  const page = await open(() => source);
  await page.user.clear(within(root()).getByRole('textbox', { name: 'Cash reserve' }));
  await page.user.type(within(root()).getByRole('textbox', { name: 'Cash reserve' }), 'bad');
  const scope = selectShoppingLive(page.data.store.getSnapshot()).scope;
  const snapshot = page.data.store.getSnapshot();
  const failed = addFailure(snapshot, 'battlesActive');
  expect(selectShoppingLive(failed).draft).toEqual(snapshot.runDrafts[scope]);
  expect(selectShoppingLive(failed).reason).toBe('report_stale');
  const storage = page.storage;
  page.view.unmount();
  page.data.stop();
  window.location.hash = '#runs/live';
  const reloaded = await renderPage({
    storage,
    config: () => CONFIG,
    before: (fake) => {
      fake.route('GET', '/api/battles/active', () => ({ status: 200, body: source }));
    },
  });
  await reloaded.advance(100);
  expect(within(root()).getByRole('textbox', { name: 'Cash reserve' })).toHaveValue('bad');
  stage(reloaded);
  act(() => {
    reloaded.data.commands.editDraft(scope, 'reserve', '750.5');
  });
  const accepted = sample('shopping_plan_accepted');
  reloaded.fake.route('POST', accepted.request.path, () => accepted);
  await reloaded.user.click(
    within(root()).getByRole('button', { name: 'Save queue for this battle' }),
  );
  await reloaded.advance(100);
  act(() => {
    reloaded.data.commands.editDraft(scope, 'reserve', '999');
  });
  source = sample('shopping_plan_read_applied').body;
  act(() => {
    reloaded.data.commands.checkBattle();
  });
  await reloaded.advance(100);
  expect(within(root()).getByRole('textbox', { name: 'Cash reserve' })).toHaveValue('999');
});

it('SQ12 a wrong-key read leaves Unknown; only the exact request key in the same battle settles the live command', async () => {
  let source: unknown = before('shopping_plan_unknown_read');
  const unknown = sample('shopping_plan_reply_unknown');
  const page = await open(() => source, 'sq-u1');
  page.fake.route('POST', unknown.request.path, () => unknown);
  stage(page, 'shopping_plan_reply_unknown');
  await page.user.click(within(root()).getByRole('button', { name: 'Save queue for this battle' }));
  await page.advance(100);
  const read = sample('shopping_plan_unknown_read').body;
  const battle = own(read, 'battle');
  // Only replace the command key to model a same-battle read that cannot identify our unknown request.
  source = {
    ...record(read),
    battle: {
      ...record(battle),
      commands: (list(own(battle, 'commands')) ?? []).map((command) => ({
        ...record(command),
        request_keys: ['another-key'],
      })),
    },
  };
  act(() => {
    page.data.commands.checkBattle();
  });
  await page.advance(100);
  expect(selectRunsPage(page.data.store.getSnapshot()).live.outcome?.word).toBe('unknown');
  source = read;
  act(() => {
    page.data.commands.checkBattle();
  });
  await page.advance(100);
  expect(selectRunsPage(page.data.store.getSnapshot()).live.outcome?.code).toBe('live_settled');
  // A read holding the exact request key settles it as recorded: its submitted values clear.
  expect(selectShoppingLive(page.data.store.getSnapshot()).discard).toBe(false);
  expect(postBodies(page.fake, unknown.request.path)).toHaveLength(1);
});

it('SQ12 live queue drafts do not transfer across context or operation', () => {
  const original = addBody(
    confirmedState(),
    'battlesActive',
    sample('shopping_plan_read_applied').body,
  );
  const vm = selectShoppingLive(original);
  const state = { ...original, runDrafts: { [vm.scope]: { reserve: '1234' } } };
  if (state.context === null) throw new Error('fixture context absent');
  expect(
    selectShoppingLive({ ...state, context: { ...state.context, profile: 'another-profile' } })
      .draft,
  ).toEqual({});
  const body = sample('shopping_plan_read_applied').body;
  // Replace operation only: an operation-bound draft cannot follow a reused battle/generation pair.
  const next = {
    ...record(body),
    battle: { ...record(own(body, 'battle')), operation_id: 'another-operation' },
  };
  expect(selectShoppingLive(addBody(state, 'battlesActive', next)).draft).toEqual({});
});
// LIVE-INT-01: a receipt clears the submitted values (as a routine change does), so a reload
// after acceptance has no orphan draft; the battle read says the change is requested.
it.each([false, true])(
  'SQ12 integration: acceptance then reload clears submitted fields and preserves newer typing (%s)',
  async (newer) => {
    let source: unknown = before();
    const accepted = sample('shopping_plan_accepted');
    const page = await open(() => source);
    page.fake.route('POST', accepted.request.path, () => {
      source = sample('shopping_plan_read_requested').body;
      return accepted;
    });
    stage(page);
    await page.user.click(
      within(root()).getByRole('button', { name: 'Save queue for this battle' }),
    );
    await page.advance(100);
    const scope = selectShoppingLive(page.data.store.getSnapshot()).scope;
    if (newer)
      act(() => {
        page.data.commands.editDraft(scope, 'reserve', '999');
      });
    const storage = page.storage;
    page.view.unmount();
    page.data.stop();
    source = sample('shopping_plan_read_applied').body;
    const reloaded = await renderPage({
      storage,
      config: () => CONFIG,
      before: (fake) => {
        fake.route('GET', '/api/battles/active', () => ({ status: 200, body: source }));
      },
    });
    await reloaded.advance(100);
    expect(
      selectShoppingLive(reloaded.data.store.getSnapshot()).base.plan?.raw['cash_reserve'],
    ).toBe(750.5);
    expect(reloaded.data.store.getSnapshot().runDrafts[scope] ?? {}).toEqual(
      newer ? { reserve: '999' } : {},
    );
  },
);

it('SQ11 SQ7 the live queue waits for the confirmed page context before it can be edited', () => {
  const read = addBody(
    confirmedState(),
    'battlesActive',
    sample('shopping_plan_read_applied').body,
  );
  const unconfirmed = selectShoppingLive({ ...read, contextConfirmed: false });
  expect(unconfirmed.supported).toBe(false);
  expect(unconfirmed.reason).toBe('context_unknown');
  expect(unconfirmed.command).toBeNull();
  const confirmed = selectShoppingLive({ ...read, contextConfirmed: true });
  expect(confirmed.supported).toBe(true);
  expect(confirmed.reason).not.toBe('context_unknown');
});

it.each(numericBoundsCases('live'))(
  'SQ5 live recorded bounds %s: exact POST, server refusal beside Save, raw draft or same-battle applied readback',
  async (name) => {
    let source = bounds('read_after_the_live_refusals_nothing_was_recorded').body;
    const answer = bounds(name);
    const key = own(answer.request.body, 'idempotency_key');
    if (typeof key !== 'string') throw new Error('missing recorded key');
    const page = await open(() => source, key);
    const vm = selectShoppingLive(page.data.store.getSnapshot());
    const draft = boundsDraft(name);
    act(() => {
      for (const [key, value] of Object.entries(draft))
        page.data.commands.editDraft(vm.scope, key, value);
    });
    page.fake.route('POST', answer.request.path, () => answer);
    await page.user.click(
      within(root()).getByRole('button', { name: 'Save queue for this battle' }),
    );
    await page.advance(100);
    expect(postBodies(page.fake, answer.request.path)).toEqual([answer.request.body]);
    if (answer.status === 400) {
      expect(
        root().querySelector('[data-queue-refusal] [data-server-text="message"]'),
      ).toHaveTextContent(String(own(answer.body, 'error')));
      expect(root().querySelector('[data-queue-refusal]')).toBeVisible();
      expect(selectShoppingLive(page.data.store.getSnapshot()).draft).toEqual(draft);
    } else {
      expect(selectRunsPage(page.data.store.getSnapshot()).live.outcome?.code).toBe(
        'live_recorded',
      );
      source = bounds('read_after_the_live_boundary_values_applied').body;
    }
    act(() => {
      page.data.commands.checkBattle();
    });
    await page.advance(100);
    expect(selectRunsPage(page.data.store.getSnapshot()).live.outcome?.code).toBe(
      answer.status === 400 ? 'live_not_recorded' : 'live_applied',
    );
    expect(selectShoppingLive(page.data.store.getSnapshot()).draft).toEqual(
      answer.status === 400 ? draft : {},
    );
    expect(postBodies(page.fake, answer.request.path)).toHaveLength(1);
  },
);
