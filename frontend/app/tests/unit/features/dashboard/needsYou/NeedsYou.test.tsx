import { fireEvent, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { afterEach, expect, it, vi } from 'vitest';
import { own, record } from '../../../../../src/core/read.ts';
import { NeedsYou } from '../../../../../src/features/dashboard/needsYou/NeedsYou.tsx';
import { reduceDashboard } from '../../../../../src/store/dashboardStore.ts';
import { selectNeedsYou } from '../../../../../src/store/selectors/needsYou.ts';
import type { DashboardState, ResourceId } from '../../../../../src/store/types.ts';
import { textOutsideCatalogue } from '../../../copy/renderedText.ts';
import { dashboardBody } from '../../../../support/dashboardFixture.ts';
import { postBodies } from '../../../../support/dataLayer.ts';
import { sample, statusBody } from '../../../../support/fixture.ts';
import { addBody, addCase, addFailure, regionState, tick } from '../../../store/regionHelpers.ts';
import { factValue, renderPage, renderRegion, type RegionHarness } from '../harness.tsx';

const BLOCKED = 'status.guards_setup_and_collection_blocking_the_same_operation';
const RELEASED = 'status.guards_setup_blocking_collection_released_by_handoff';
const FAILED_SETUP = 'calibrate_status.failed_setup_unverified_final_screen';

/** The blocking guards as /api/status sends them: with its harvest record (the dashboard fixture omits it). */
function blockedBody(): Record<string, unknown> {
  return { ...record(dashboardBody(BLOCKED)), harvest: sample('status.off') };
}

function changedEvidence() {
  return {
    ...blockedBody(),
    setup_battle_guard: {
      ...record(own(dashboardBody(BLOCKED), 'setup_battle_guard')),
      evidence_revision: 'new-evidence-revision',
    },
  };
}

function readyState(): DashboardState {
  let state = addBody(regionState(), 'status', statusBody(sample('status.off')));
  for (const [source, name] of [
    ['calibrate', 'calibrate_status.idle'],
    ['wizardTools', 'wizard_tools.installed'],
    ['accounts', 'accounts.active_among_several'],
    ['config', 'config.taps_true'],
    ['runs', 'runs.ready'],
    ['wizardState', 'wizard_state.all_sections'],
  ] satisfies readonly (readonly [ResourceId, string])[]) {
    state = addCase(state, source, name);
  }
  return state;
}

function item(h: RegionHarness, code: string): HTMLElement {
  const found = h.view.container.querySelector<HTMLElement>(`[data-need="${code}"]`);
  if (found === null) throw new Error(`Missing item ${code}`);
  return found;
}

async function verify(container: HTMLElement): Promise<void> {
  expect(textOutsideCatalogue(container)).toEqual([]);
  expect(await axe(container)).toHaveNoViolations();
}

afterEach(() => {
  vi.useRealTimers();
});

it('F-084: renderPage leaves the live region empty on first draw, initial reads and age ticks', async () => {
  vi.useFakeTimers();
  const page = await renderPage();
  try {
    expect(page.view.getByRole('status')).toBeEmptyDOMElement();
    await page.advance(1000);
    expect(page.view.getByRole('status')).toBeEmptyDOMElement();
    expect(page.fake.writes()).toEqual([]);
    expect(textOutsideCatalogue(page.view.container)).toEqual([]);
  } finally {
    page.data.stop();
    vi.useRealTimers();
  }
  await verify(page.view.container);
});

it('F-033: every confirmation sends the exact VM command once; Cancel, render and polls send nothing', async () => {
  const state = addBody(readyState(), 'status', blockedBody());
  const h = renderRegion(state, <NeedsYou />);
  const user = userEvent.setup();
  const target = selectNeedsYou(state).items.find((need) => need.code === 'launch_guard');
  expect(target?.handoffCommand).toEqual({
    ...target?.handoffTarget,
    attestation: 'new_manual_battle',
  });
  expect(h.commands.handoff).not.toHaveBeenCalled();
  const open = h.view.getByRole('button', { name: 'Confirm a new manual battle...' });
  await user.click(open);
  const dialog = h.view.getByRole('dialog', { name: 'Confirm a new manual battle' });
  expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();
  expect(within(dialog).getByText(target?.handoffCommand?.operationId ?? '')).toBeVisible();
  expect(within(dialog).getByText(target?.handoffCommand?.setupBattleId ?? '')).toBeVisible();
  expect(
    within(dialog).getByText(/permits gem collection only; Start stays blocked/),
  ).toBeVisible();
  await verify(h.view.container);
  await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
  expect(h.commands.handoff).not.toHaveBeenCalled();
  await waitFor(() => {
    expect(open).toHaveFocus();
  });
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    await user.click(open);
    expect(h.view.getByRole('dialog')).toBeVisible();
    await user.click(within(dialog).getByRole('button', { name: 'Confirm' }));
    expect(h.commands.handoff).toHaveBeenCalledTimes(attempt);
    expect(h.commands.handoff).toHaveBeenLastCalledWith(target?.handoffCommand);
    h.setState(tick(state, attempt));
    expect(h.commands.handoff).toHaveBeenCalledTimes(attempt);
  }
  await verify(h.view.container);
});

it('F-033: changed evidence needs fresh confirmation and collection release leaves Start off', async () => {
  let state = addBody(readyState(), 'status', blockedBody());
  const h = renderRegion(state, <NeedsYou />);
  const user = userEvent.setup();
  await user.click(h.view.getByRole('button', { name: 'Confirm a new manual battle...' }));
  state = addBody(state, 'status', changedEvidence());
  h.setState(state);
  expect(h.view.queryByRole('dialog')).not.toBeInTheDocument();
  expect(h.view.getByRole('button', { name: 'Confirm a new manual battle...' })).toHaveFocus();
  expect(h.commands.handoff).not.toHaveBeenCalled();
  await user.click(h.view.getByRole('button', { name: 'Confirm a new manual battle...' }));
  await user.click(within(h.view.getByRole('dialog')).getByRole('button', { name: 'Confirm' }));
  expect(h.commands.handoff).toHaveBeenCalledExactlyOnceWith(
    selectNeedsYou(state).items.find((need) => need.code === 'launch_guard')?.handoffCommand,
  );
  h.setState(addCase(state, 'status', RELEASED));
  expect(factValue(h.view.container, 'Start')).toHaveTextContent('Off');
  expect(factValue(h.view.container, 'Gem collection')).toHaveTextContent(
    'Allowed: you confirmed a new manual battle',
  );
  expect(factValue(h.view.container, 'Start')).toBeVisible();
  expect(factValue(h.view.container, 'Gem collection')).toBeVisible();
  expect(h.view.queryByRole('button', { name: 'Confirm a new manual battle...' })).toBeNull();
  expect(
    within(item(h, 'launch_guard')).getByRole('link', { name: 'Review setup result' }),
  ).toHaveFocus();
  await verify(h.view.container);
});

it('F-033: a focused confirmation opener survives an evidence revision poll', async () => {
  const state = addBody(readyState(), 'status', blockedBody());
  const h = renderRegion(state, <NeedsYou />);
  const old = h.view.getByRole('button', { name: 'Confirm a new manual battle...' });
  old.focus();
  h.setState(addBody(state, 'status', changedEvidence()));
  expect(old.isConnected).toBe(false);
  expect(h.view.getByRole('button', { name: 'Confirm a new manual battle...' })).toHaveFocus();
  expect(h.view.queryByRole('dialog')).toBeNull();
  expect(h.commands.handoff).not.toHaveBeenCalled();
  await verify(h.view.container);
});

it.each([
  ['a process is listed', { processes: [{ pid: 123 }] }],
  [
    'the operation differs',
    {
      collection_guard: {
        blocks_launch: true,
        code: 'setup_battle_unresolved',
        operation_id: 'other',
      },
    },
  ],
  ['one guard is missing', { collection_guard: null }],
  // F4-R2-01: "no process is listed" needs a fresh scan; an empty list from a stale one proves nothing.
  [
    'the empty process list comes from a stale scan',
    {
      process_refresh: {
        ...record(own(dashboardBody(BLOCKED), 'process_refresh')),
        state: 'stale',
      },
    },
  ],
])('F-033: confirmation is absent when %s', async (_label, patch) => {
  const state = addBody(readyState(), 'status', { ...blockedBody(), ...patch });
  const h = renderRegion(state, <NeedsYou />);
  expect(h.view.queryByRole('button', { name: 'Confirm a new manual battle...' })).toBeNull();
  expect(h.commands.handoff).not.toHaveBeenCalled();
  await verify(h.view.container);
});

it('F-033: a confirmed page click sends exactly one POST and subsequent polling sends none', async () => {
  vi.useFakeTimers();
  const page = await renderPage({
    status: () => blockedBody(),
    before: (fake) => {
      fake.route('POST', '/api/setup-battle/collection-handoff', () => 'hold');
    },
  });
  try {
    expect(page.fake.writes()).toEqual([]);
    await page.user.click(
      page.view.getByRole('button', { name: 'Confirm a new manual battle...' }),
    );
    await page.user.click(
      within(page.view.getByRole('dialog')).getByRole('button', { name: 'Cancel' }),
    );
    expect(page.fake.writes()).toEqual([]);
    await page.user.click(
      page.view.getByRole('button', { name: 'Confirm a new manual battle...' }),
    );
    await page.user.click(
      within(page.view.getByRole('dialog')).getByRole('button', { name: 'Confirm' }),
    );
    await page.advance();
    const target = selectNeedsYou(page.data.store.getSnapshot()).items.find(
      (need) => need.code === 'launch_guard',
    )?.handoffCommand;
    expect(postBodies(page.fake)).toEqual([
      {
        operation_id: target?.operationId,
        setup_battle_id: target?.setupBattleId,
        evidence_revision: target?.evidenceRevision,
        expected_context: { instance: 'main', account: null, serial: 'fake:1' },
        attestation: 'new_manual_battle',
      },
    ]);
    const reads = page.fake.requests.filter((request) => request.method === 'GET').length;
    await page.advance(60000);
    expect(page.fake.requests.filter((request) => request.method === 'GET').length).toBeGreaterThan(
      reads,
    );
    expect(page.fake.writes()).toHaveLength(1);
    expect(textOutsideCatalogue(page.view.container)).toEqual([]);
  } finally {
    page.data.stop();
    vi.useRealTimers();
  }
  const region = page.view.container.querySelector<HTMLElement>('[data-region="needsYou"]');
  if (region === null) throw new Error('Missing Needs you region');
  await verify(region);
});

it.each([
  ['failed status read', 'No live status', 'failed'],
  ['stale scan', "Can't tell what's running", 'status.process_refresh_stale'],
  ['failed scan', "Can't tell what's running", 'status.process_refresh_error'],
])(
  'F-081: %s offers a forced Retry status read and names the last successful read',
  async (_label, line, scenario) => {
    const prior = tick(readyState(), 17);
    const state =
      scenario === 'failed' ? addFailure(prior, 'status') : addCase(prior, 'status', scenario);
    const h = renderRegion(state, <NeedsYou />);
    const row = within(item(h, 'status_need'));
    expect(row.getByText(line)).toBeVisible();
    expect(row.getByText('Unknown')).toBeVisible();
    const user = userEvent.setup();
    await user.click(row.getByRole('button', { name: 'Retry status' }));
    expect(h.commands.checkStatus).toHaveBeenCalledExactlyOnceWith();
    expect(h.commands.handoff).not.toHaveBeenCalled();
    await user.click(row.getByText('Details'));
    expect(
      row.getByText(scenario === 'failed' ? 'last read 17 s ago' : 'last read 0 s ago'),
    ).toBeVisible();
    await verify(h.view.container);
  },
);

it('F-082: unavailable setup progress offers Retry and Review setup result', async () => {
  const h = renderRegion(addFailure(readyState(), 'calibrate'), <NeedsYou />);
  const row = within(item(h, 'setup_outcome'));
  expect(row.getByText('Setup status unavailable')).toBeVisible();
  await userEvent.setup().click(row.getByRole('button', { name: 'Retry' }));
  // Retry reads the setup progress again (a forced calibration read), never the status or a write.
  expect(h.commands.checkSetupStatus).toHaveBeenCalledExactlyOnceWith();
  expect(h.commands.checkStatus).not.toHaveBeenCalled();
  expect(row.getByRole('link', { name: 'Review setup result' })).toHaveAttribute(
    'href',
    '#recognition',
  );
  await verify(h.view.container);
});

it('F-082: pending equipment restoration links to setup and the exact operation events', async () => {
  const operation = own(dashboardBody(FAILED_SETUP), 'operation');
  const state = addBody(readyState(), 'calibrate', {
    ...record(dashboardBody('calibrate_status.restore_pending')),
    operation,
  });
  const h = renderRegion(state, <NeedsYou />);
  const row = within(item(h, 'setup_outcome'));
  expect(row.getByText('Equipment not restored yet — other automation is on hold')).toBeVisible();
  expect(row.getByRole('link', { name: 'Review setup result' })).toHaveAttribute(
    'href',
    '#recognition',
  );
  const id = own(operation, 'id');
  expect(row.getByRole('link', { name: 'Events for this operation' })).toHaveAttribute(
    'href',
    `#runtime?operation_id=${String(id)}`,
  );
  await verify(h.view.container);
});

it.each([
  ['both blocked', true, true, 'Off', 'Starting collection is blocked', undefined],
  ['only Start blocked', true, false, 'Off', 'Allowed by the collection guard', undefined],
  [
    'only collection blocked',
    false,
    true,
    'Allowed by the setup guard',
    'Starting collection is blocked',
    undefined,
  ],
  [
    'collection released by confirmation',
    true,
    false,
    'Off',
    'Allowed: you confirmed a new manual battle',
    'collection_handoff',
  ],
  [
    'guards unreadable',
    null,
    null,
    'Permission not confirmed',
    'Permission not confirmed',
    undefined,
  ],
])(
  'F-083: failed setup states each guard impact independently: %s',
  async (_label, start, collection, startLine, collectionLine, evidence) => {
    let state = addBody(readyState(), 'status', {
      ...blockedBody(),
      setup_battle_guard: { scope: 'run', blocks_launch: start, code: 'setup_battle_unresolved' },
      collection_guard: {
        scope: 'collection',
        blocks_launch: collection,
        code: 'setup_battle_unresolved',
        evidence,
      },
    });
    state = addCase(state, 'calibrate', FAILED_SETUP);
    const h = renderRegion(state, <NeedsYou />);
    const row = within(item(h, 'setup_outcome'));
    expect(row.getByText(/^Setup failed — /)).toBeVisible();
    // Matching guard impacts are stated once for the whole issue group.
    const startFact = factValue(h.view.container, 'Start');
    const collectionFact = factValue(h.view.container, 'Gem collection');
    expect(startFact).toHaveTextContent(startLine);
    expect(collectionFact).toHaveTextContent(collectionLine);
    expect(startFact).toBeVisible();
    expect(collectionFact).toBeVisible();
    if (start !== true) expect(startFact).not.toHaveTextContent('Off');
    const detector = row.getByText("No fresh matching control for 'open intro sprint dialog'");
    expect(detector).not.toBeVisible();
    expect(detector).toHaveAttribute('data-server-text', 'message');
    await userEvent.setup().click(row.getByText('Details'));
    expect(detector).toBeVisible();
    expect(detector.closest('details')).toHaveAttribute('open');
    expect(row.getByText('Final screen not verified')).toBeVisible();
    await verify(h.view.container);
  },
);

it.each([
  ['cancelled', null, 'Setup tracking stopped'],
  ['interrupted', null, 'Setup tracking stopped'],
  ['succeeded', { status: 'unknown' }, 'Setup partly finished'],
])('F-083: recorded %s setup outcomes remain reviewable', async (status, setupBattle, line) => {
  const state = addBody(readyState(), 'calibrate', {
    operation: {
      id: 'setup-stopped',
      status,
      updated_at: 1800000000,
      error: { message: 'Recorded setup error\nSecond line' },
    },
    state:
      setupBattle === null
        ? {}
        : { setup_battle: { ...setupBattle, operation_id: 'setup-stopped' } },
  });
  const h = renderRegion(state, <NeedsYou />);
  const row = within(item(h, 'setup_outcome'));
  expect(row.getByText((text) => text.startsWith(line))).toBeVisible();
  const error = row.getByText(/Recorded setup error/);
  expect(error).not.toBeVisible();
  await userEvent.setup().click(row.getByText('Details'));
  expect(error).toBeVisible();
  expect(error.textContent).toBe('Recorded setup error\nSecond line');
  await verify(h.view.container);
});

it('F-084: at most three items show and Show all (n) expands the rest in place across polls', async () => {
  let state = addCase(regionState(), 'status', BLOCKED);
  state = addCase(state, 'calibrate', FAILED_SETUP);
  state = addCase(state, 'runs', 'runs.combo_entry');
  const h = renderRegion(state, <NeedsYou />);
  const vm = selectNeedsYou(state);
  expect(vm.count).toBeGreaterThan(3);
  expect(h.view.container.querySelectorAll('[data-need]')).toHaveLength(3);
  expect(h.view.container.querySelector('.tp-region__count')).toHaveTextContent(String(vm.count));
  const user = userEvent.setup();
  await user.click(h.view.getByRole('button', { name: `Show all (${String(vm.count)})` }));
  expect(h.view.container.querySelectorAll('[data-need]')).toHaveLength(vm.count);
  expect(h.view.queryByRole('button', { name: /Show all/ })).toBeNull();
  const why = within(item(h, 'setup_outcome')).getByText('Details');
  await user.click(why);
  const summary = why.closest('summary');
  summary?.focus();
  h.setState(tick(state, 1));
  expect(summary).toHaveFocus();
  expect(why.closest('details')).toHaveAttribute('open');
  expect(h.view.container.querySelectorAll('[data-need]')).toHaveLength(vm.count);
  expect(h.view.getByRole('link', { name: 'All runtime events' })).toHaveAttribute(
    'href',
    '#runtime',
  );
  await verify(h.view.container);
});

it('F-084: three or fewer items have no Show all and detector reports stay under Details', async () => {
  const h = renderRegion(addBody(readyState(), 'status', blockedBody()), <NeedsYou />);
  expect(h.view.queryByRole('button', { name: /Show all/ })).toBeNull();
  const row = within(item(h, 'launch_guard'));
  expect(row.getByRole('link', { name: 'Events for this operation' })).not.toBeVisible();
  await userEvent.setup().click(row.getByText('Details'));
  expect(row.getByRole('link', { name: 'Events for this operation' })).toHaveAttribute(
    'href',
    `#runtime?operation_id=${String(own(own(dashboardBody(BLOCKED), 'setup_battle_guard'), 'operation_id'))}`,
  );
  for (const node of item(h, 'launch_guard').querySelectorAll('[data-server-text="message"]')) {
    expect(node.closest('details')).toHaveAttribute('open');
  }
  await verify(h.view.container);
});

it('F-084: the empty state names every checked source and its age without claiming health', async () => {
  const h = renderRegion(tick(readyState(), 9), <NeedsYou />);
  expect(h.view.container.querySelectorAll('[data-need]')).toHaveLength(0);
  expect(h.view.getByText('No unresolved issues reported')).toBeVisible();
  await userEvent.setup().click(h.view.getByText('Checks and freshness'));
  for (const source of [
    'Live status',
    'Setup progress',
    'Runs and readiness',
    'Connection tools',
    'Setup steps',
    'Configuration',
    'Profiles',
  ]) {
    expect(h.view.getByText(`${source}: answered - checked 9 s ago`)).toBeVisible();
  }
  expect(h.view.queryByRole('button', { name: /Show all/ })).toBeNull();
  expect(h.view.getByRole('link', { name: 'All runtime events' })).toHaveAttribute(
    'href',
    '#runtime',
  );
  await verify(h.view.container);
});

it('F-084: an empty issue list preserves a failed runs refresh in the checked sources', async () => {
  const h = renderRegion(addFailure(tick(readyState(), 9), 'runs'), <NeedsYou />);
  expect(h.view.container.querySelectorAll('[data-need]')).toHaveLength(0);
  expect(h.view.getByText('Runs and readiness: unavailable - checked 0 s ago')).toBeVisible();
  await verify(h.view.container);
});

it.each(['next', 'previous', 'heading'])(
  'F-084: a settled issue moves focus to the %s target',
  async (target) => {
    let state = addBody(readyState(), 'status', {
      ...blockedBody(),
      harvest: { server_time: 1800000000 },
    });
    state = addCase(state, 'calibrate', FAILED_SETUP);
    const h = renderRegion(state, <NeedsYou />);
    if (target === 'previous') {
      within(item(h, 'setup_outcome')).getByRole('link', { name: 'Review setup result' }).focus();
      h.setState(addCase(state, 'calibrate', 'calibrate_status.idle'));
      expect(
        within(item(h, 'launch_guard')).getByRole('button', {
          name: 'Confirm a new manual battle...',
        }),
      ).toHaveFocus();
    } else {
      within(item(h, 'launch_guard'))
        .getByRole('button', { name: 'Confirm a new manual battle...' })
        .focus();
      const cleared = addBody(state, 'status', statusBody(sample('status.off')));
      h.setState(
        target === 'heading' ? addCase(cleared, 'calibrate', 'calibrate_status.idle') : cleared,
      );
      if (target === 'heading')
        expect(h.view.getByRole('heading', { name: 'Needs you' })).toHaveFocus();
      else
        expect(
          within(item(h, 'setup_outcome')).getByRole('link', { name: 'Review setup result' }),
        ).toHaveFocus();
    }
    await verify(h.view.container);
  },
);

it.each([
  [
    'tools',
    'wizardTools',
    'wizard_tools.not_installed',
    'install connection tools',
    'Install connection tools',
    '#emulators',
  ],
  [
    'profile',
    'accounts',
    'accounts.none_active',
    'create or choose a profile',
    'Choose a profile',
    '#profiles',
  ],
  ['connection', 'config', null, 'connect an emulator', 'Connect an emulator', '#emulators'],
  [
    'screens',
    'runs',
    'runs.combo_entry',
    'learn screens for a run',
    'Learn screens',
    '#recognition',
  ],
] satisfies readonly (readonly [string, ResourceId, string | null, string, string, string])[])(
  'F-098: first missing %s step links to the legacy page that completes it',
  async (_step, resource, fixture, line, label, href) => {
    const state =
      fixture === null
        ? addBody(readyState(), resource, { active_instance: 'main', instances: { main: {} } })
        : addCase(readyState(), resource, fixture);
    const h = renderRegion(state, <NeedsYou />);
    const row = within(item(h, 'finish_setup'));
    expect(row.getByText(`Finish setup: ${line}`)).toBeVisible();
    expect(row.getByRole('link', { name: label })).toHaveAttribute('href', href);
    await verify(h.view.container);
    h.setState(readyState());
    expect(h.view.queryByText(/^Finish setup:/)).toBeNull();
    await verify(h.view.container);
  },
);

it('F-098: a cold install offers only its first missing step', async () => {
  const h = renderRegion(regionState(), <NeedsYou />);
  const row = within(item(h, 'finish_setup'));
  expect(row.getByText('Finish setup: install connection tools')).toBeVisible();
  expect(row.queryByText('Finish setup: create or choose a profile')).toBeNull();
  await verify(h.view.container);
});

it('F-033: Escape cancels and changing context clears any pending confirmation', async () => {
  const state = addBody(readyState(), 'status', blockedBody());
  const h = renderRegion(state, <NeedsYou />);
  const user = userEvent.setup();
  await user.click(h.view.getByRole('button', { name: 'Confirm a new manual battle...' }));
  fireEvent(h.view.getByRole('dialog'), new Event('cancel', { cancelable: true }));
  expect(h.view.queryByRole('dialog')).toBeNull();
  expect(h.commands.handoff).not.toHaveBeenCalled();
  await user.click(h.view.getByRole('button', { name: 'Confirm a new manual battle...' }));
  h.setState(
    reduceDashboard(state, {
      type: 'context',
      context: null,
      epoch: state.epoch + 1,
      confirmed: false,
      sessionContext: null,
    }),
  );
  expect(h.view.queryByRole('dialog')).toBeNull();
  expect(h.commands.handoff).not.toHaveBeenCalled();
  await verify(h.view.container);
});
