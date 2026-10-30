import { fireEvent, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { afterEach, expect, it, vi } from 'vitest';
import { copy } from '../../../../../src/copy/catalogue.ts';
import { TopBar } from '../../../../../src/features/dashboard/topBar/TopBar.tsx';
import { reduceDashboard } from '../../../../../src/store/dashboardStore.ts';
import { selectTopBarVM } from '../../../../../src/store/selectors/topBar.ts';
import { textOutsideCatalogue } from '../../../copy/renderedText.ts';
import {
  addBody,
  addCase,
  addFailure,
  intent,
  regionState,
  tick,
} from '../../../store/regionHelpers.ts';
import { dashboardBody } from '../../../../support/dashboardFixture.ts';
import { e0Config } from '../../../../support/dataLayer.ts';
import { renderPage, renderRegion } from '../harness.tsx';

afterEach(() => {
  vi.useRealTimers();
});

function top(state = regionState()) {
  return renderRegion(state, <TopBar title={copy.dashboard.shell.pageName.short} />);
}

async function check(container: HTMLElement) {
  expect(textOutsideCatalogue(container)).toEqual([]);
  expect(await axe(container)).toHaveNoViolations();
}

it('F-001: renders exactly one h1 with the supplied page title', async () => {
  const page = top();
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
  expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Dashboard');
  await check(page.view.container);
});

it('F-002: renders the matching account through ServerValue and links Account details to Profiles', async () => {
  const page = top(
    addBody(regionState(), 'accounts', {
      active: 'a',
      accounts: [{ id: 'a', label: 'My Tower' }],
    }),
  );
  expect(page.view.container.querySelector('[data-server-text="name"]')).toHaveTextContent(
    'My Tower',
  );
  expect(screen.getByRole('link', { name: 'Account details' })).toHaveAttribute(
    'href',
    '#profiles',
  );
  await check(page.view.container);
});

it.each(['missing', null])(
  'F-002: no matching account (%s) says No active account',
  async (active) => {
    const page = top(
      addBody(regionState(), 'accounts', {
        active,
        accounts: [{ id: 'a', label: 'My Tower' }],
      }),
    );
    expect(screen.getByText('No active account')).toBeInTheDocument();
    await check(page.view.container);
  },
);

it('F-002: a failed account read retains its last value with the last-read age', async () => {
  const previous = addCase(regionState(), 'accounts', 'accounts.active_among_several');
  const page = top(addFailure(tick(previous, 12), 'accounts'));
  expect(screen.getByText('Alpha')).toBeInTheDocument();
  expect(screen.getByText('last read 12 s ago')).toBeInTheDocument();
  await check(page.view.container);
});

it('F-003: shows the emulator name and plain Connected for a device read no older than 10 s', async () => {
  const state = addBody(
    addCase(
      addCase(regionState(), 'status', 'status.device_state_device'),
      'wizardEmulators',
      'wizard_emulators.instances_with_names_and_ports',
    ),
    'config',
    {
      active_instance: 'main',
      instances: { main: { serial: '127.0.0.1:16384' } },
    },
  );
  const page = top(tick(state, 10));
  expect(screen.getByText('Main Tower')).toBeInTheDocument();
  // A confirmed connection is a green chip like every other state.
  expect(screen.getByText('Connected').closest('.tp-chip')).toHaveAttribute(
    'data-state',
    'running',
  );
  expect(screen.queryByText('Running')).not.toBeInTheDocument();
  page.setState(tick(state, 11));
  expect(screen.queryByText('Connected')).not.toBeInTheDocument();
  expect(screen.getByText('Unknown')).toBeInTheDocument();
  fireEvent.click(screen.getByText('Why'));
  expect(screen.getByText('last read 11 s ago')).toBeVisible();
  await check(page.view.container);
});

it.each([
  ['offline', 'adb: emulator offline'],
  ['unauthorized', 'adb: not authorized'],
  ['missing', 'Not found by adb'],
  ['no_server', 'Cannot connect to the adb server'],
  ['unconfigured', 'No emulator chosen'],
])('F-003: %s shows Blocked with its own adb connection wording', async (device_state, line) => {
  const page = top(
    addBody(regionState(), 'status', { device_state, connected: true, processes: [] }),
  );
  expect(screen.getByText('Blocked')).toBeInTheDocument();
  expect(screen.getByText(line)).toBeInTheDocument();
  expect(page.view.container).not.toHaveTextContent('not running');
  await check(page.view.container);
});

it.each(['refused', 'timed out'])(
  'F-003: a %s adb-server connect says Cannot connect to the adb server',
  async (cause) => {
    const page = top(
      addBody(regionState(), 'status', {
        device_state: 'no_server',
        connected: false,
        reconnect: { state: 'gave_up', attempts: 1, last_error: cause },
        processes: [],
      }),
    );
    expect(screen.getByText('Cannot connect to the adb server')).toBeInTheDocument();
    await check(page.view.container);
  },
);

it.each(['unknown', 'port-only', 'failed', 'unread'])(
  'F-003: %s connection evidence stays Unknown',
  async (kind) => {
    const state =
      kind === 'failed'
        ? addFailure(regionState(), 'status')
        : kind === 'unread'
          ? regionState()
          : addBody(
              regionState(),
              'status',
              kind === 'port-only'
                ? { connected: true, processes: [] }
                : { device_state: 'unknown', processes: [] },
            );
    const page = top(state);
    expect(screen.getByText('Unknown')).toBeInTheDocument();
    expect(screen.getByText('connection not confirmed')).toBeInTheDocument();
    expect(screen.queryByText('Connected')).not.toBeInTheDocument();
    await check(page.view.container);
  },
);

it('F-004: Reconnect calls the shared command exactly once per enabled click', async () => {
  const state = addCase(
    addCase(regionState(), 'config', 'config.taps_true'),
    'status',
    'status.processes_empty',
  );
  const page = top(state);
  const button = screen.getByRole('button', { name: 'Reconnect' });
  await userEvent.click(button);
  // F6M-02: the command names the context and the address the top bar shows.
  expect(page.commands.reconnect).toHaveBeenCalledExactlyOnceWith(
    selectTopBarVM(state).reconnect.command,
  );
  expect(selectTopBarVM(state).reconnect.command).toMatchObject({
    serial: 'fake:1',
    context: { serial: 'fake:1' },
  });
  await check(page.view.container);
});

it.each(['process', 'sending', 'unread'])(
  'F-004: Reconnect is disabled with a visible reason while %s',
  async (kind) => {
    let state = addCase(
      addCase(regionState(), 'config', 'config.taps_true'),
      'status',
      'status.processes_empty',
    );
    if (kind === 'process') state = addCase(state, 'status', 'status.processes_linked_collector');
    if (kind === 'sending')
      state = reduceDashboard(state, {
        type: 'intents',
        records: [intent(state, { kind: 'reconnect', phase: 'sending' })],
      });
    if (kind === 'unread') state = addFailure(state, 'status');
    const page = top(state);
    const button = screen.getByRole('button', { name: 'Reconnect' });
    expect(button).toHaveAttribute('aria-disabled', 'true');
    const reason = document.getElementById(button.getAttribute('aria-describedby') ?? '');
    expect(reason).toBeVisible();
    expect(reason?.textContent).toMatch(
      kind === 'process'
        ? /while automation runs/
        : kind === 'sending'
          ? /is sending/
          : /readable process list/,
    );
    await userEvent.click(button);
    expect(page.commands.reconnect).not.toHaveBeenCalled();
    await check(page.view.container);
  },
);

it('F-004: without a configured serial Choose emulator links to the Emulators page', async () => {
  const page = top();
  expect(screen.getByRole('link', { name: 'Choose emulator' })).toHaveAttribute(
    'href',
    '#emulators',
  );
  expect(screen.queryByRole('button', { name: 'Reconnect' })).not.toBeInTheDocument();
  await check(page.view.container);
});

it.each(['enabled', 'process', 'unread'])(
  'F-004: a focused Choose emulator link transfers focus to %s Reconnect without sending',
  async (availability) => {
    const page = top();
    const user = userEvent.setup();
    await user.tab();
    expect(screen.getByRole('link', { name: 'Account details' })).toHaveFocus();
    // The unconfirmed connection's Why sits beside its chip, before the recovery slot.
    await user.tab();
    expect(screen.getByText('Why')).toHaveFocus();
    await user.tab();
    expect(screen.getByRole('link', { name: 'Choose emulator' })).toHaveFocus();

    const state = addCase(
      addCase(regionState(), 'config', 'config.taps_true'),
      'status',
      availability === 'process' ? 'status.processes_linked_collector' : 'status.processes_empty',
    );
    page.setState(availability === 'unread' ? addFailure(state, 'status') : state);
    const reconnect = screen.getByRole('button', { name: 'Reconnect' });
    expect(screen.queryByRole('link', { name: 'Choose emulator' })).not.toBeInTheDocument();
    expect(reconnect).toHaveFocus();
    expect(page.commands.reconnect).not.toHaveBeenCalled();
    if (availability !== 'enabled') expect(reconnect).toHaveAttribute('aria-disabled', 'true');

    await user.keyboard('{Enter}');
    if (availability === 'enabled') {
      expect(page.commands.reconnect).toHaveBeenCalledExactlyOnceWith(
        selectTopBarVM(state).reconnect.command,
      );
    } else {
      await user.keyboard(' ');
      expect(page.commands.reconnect).not.toHaveBeenCalled();
    }
    await user.tab({ shift: true });
    expect(screen.getByText(availability === 'unread' ? 'Why' : 'Details')).toHaveFocus();
  },
);

it('F-004: a focused Reconnect transfers focus back to the Choose emulator anchor', async () => {
  const page = top(
    addCase(
      addCase(regionState(), 'config', 'config.taps_true'),
      'status',
      'status.processes_empty',
    ),
  );
  const user = userEvent.setup();
  await user.tab();
  await user.tab();
  expect(screen.getByText('Details')).toHaveFocus();
  await user.tab();
  expect(screen.getByRole('button', { name: 'Reconnect' })).toHaveFocus();
  page.setState(regionState());
  const choose = screen.getByRole('link', { name: 'Choose emulator' });
  expect(choose).toHaveAttribute('href', '#emulators');
  expect(choose).toHaveFocus();
  expect(page.commands.reconnect).not.toHaveBeenCalled();
  await user.tab({ shift: true });
  expect(screen.getByText('Why')).toHaveFocus();
});

it('F-004: replacing the slot in either direction preserves focus outside it', async () => {
  const page = top();
  const user = userEvent.setup();
  await user.tab();
  const account = screen.getByRole('link', { name: 'Account details' });
  expect(account).toHaveFocus();
  page.setState(
    addCase(
      addCase(regionState(), 'config', 'config.taps_true'),
      'status',
      'status.processes_empty',
    ),
  );
  expect(screen.getByRole('button', { name: 'Reconnect' })).toBeInTheDocument();
  expect(account).toHaveFocus();
  page.setState(regionState());
  expect(screen.getByRole('link', { name: 'Choose emulator' })).toBeInTheDocument();
  expect(account).toHaveFocus();
  expect(page.commands.reconnect).not.toHaveBeenCalled();
});

it('F-004: read-only top bars omit both slot controls across config changes', async () => {
  const page = renderRegion(
    regionState(),
    <TopBar title={copy.dashboard.shell.pageName.short} readOnly />,
  );
  const user = userEvent.setup();
  await user.tab();
  const account = screen.getByRole('link', { name: 'Account details' });
  page.setState(
    addCase(
      addCase(regionState(), 'config', 'config.taps_true'),
      'status',
      'status.processes_empty',
    ),
  );
  expect(account).toHaveFocus();
  expect(screen.queryByRole('button', { name: 'Reconnect' })).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'Choose emulator' })).not.toBeInTheDocument();
  expect(page.commands.reconnect).not.toHaveBeenCalled();
});

it('keeps input revocation on other pages even when connection controls are read-only', async () => {
  const state = addCase(regionState(), 'config', 'config.taps_true');
  const page = renderRegion(
    state,
    <TopBar title={copy.dashboard.shell.pageName.short} pageId="history" readOnly />,
  );
  await userEvent.click(screen.getByRole('button', { name: 'Turn input off' }));
  expect(page.commands.turnInputOff).toHaveBeenCalledExactlyOnceWith();
  expect(screen.queryByRole('button', { name: 'Reconnect' })).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'Choose emulator' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Allow input' })).not.toBeInTheDocument();
});

it('leaves Dashboard input revocation with the Control region', () => {
  top(addCase(regionState(), 'config', 'config.taps_true'));
  expect(screen.queryByRole('button', { name: 'Turn input off' })).not.toBeInTheDocument();
});

it('F-004 F6M-C-02: a runner listed while the click-time config read is in flight stops the Reconnect send', async () => {
  vi.useFakeTimers();
  let runner = false;
  let holdConfig = false;
  const page = await renderPage({
    status: () =>
      dashboardBody(runner ? 'status.processes_two_rows_same_runner' : 'status.processes_empty'),
    before: (fake) => {
      fake.route('GET', '/api/config', () =>
        holdConfig ? 'hold' : { status: 200, body: e0Config(true) },
      );
    },
  });
  holdConfig = true;
  await page.user.click(screen.getByRole('button', { name: 'Reconnect' }));
  await page.advance(0);
  runner = true;
  await page.advance(3_000);
  const held = page.fake.held.find(
    (entry) => entry.request.path === '/api/config' && !entry.settled(),
  );
  if (held === undefined) throw new Error('no held config read');
  held.respond(200, e0Config(true));
  await page.advance(1_000);
  expect(page.fake.writes()).toEqual([]);
  await page.advance(30_000);
  expect(page.fake.writes()).toEqual([]);
  page.data.stop();
  vi.useRealTimers();
});

it('F-004: a real click sends exactly one POST /api/wizard/reconnect and cannot resend while sending', async () => {
  vi.useFakeTimers();
  const page = await renderPage({
    status: () => dashboardBody('status.processes_empty'),
  });
  expect(page.fake.writes()).toEqual([]);
  const button = screen.getByRole('button', { name: 'Reconnect' });
  await page.user.click(button);
  await page.advance();
  // F6M-02: the body names the address shown, read again at the click (a config GET).
  expect(page.fake.writes()).toMatchObject([
    { path: '/api/wizard/reconnect', method: 'POST', body: '{"serial":"fake:1"}' },
  ]);
  expect(page.fake.writes()).toHaveLength(1);
  expect(button).toHaveAttribute('aria-disabled', 'true');
  await page.user.click(button);
  await page.advance(2000);
  expect(page.fake.writes()).toHaveLength(1);
  page.data.stop();
  vi.useRealTimers();
  // Only this region is this author's responsibility; other regions are authored concurrently.
  const header = page.view.container.querySelector('header');
  if (header === null) throw new Error('Missing top bar');
  await check(header);
});
