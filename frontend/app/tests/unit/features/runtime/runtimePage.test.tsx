import { act, fireEvent, screen, within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchTransport } from '../../../../src/api/transport.ts';
import {
  dashboardPagesCase,
  dashboardPagesCaseNames,
  type DashboardCase,
} from '../../../support/dashboardFixture.ts';
import { sample, statusBody } from '../../../support/fixture.ts';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import { postBodies } from '../../../support/dataLayer.ts';
import { renderPage, type PageOptions } from '../dashboard/harness.tsx';
import { RUNTIME_SAVES } from '../../../support/fake-backend/runtime-fixtures.mjs';

vi.mock('../../../../src/api/transport.ts', async (original) => ({
  ...(await original<typeof import('../../../../src/api/transport.ts')>()),
  fetchTransport: vi.fn(),
}));
const pages = dashboardPagesCase;
const identityDefault = 'runtime_state.idle_server_build_matches_the_source_on_disk';
const diagnosticsDefault = 'runtime_diagnostics.every_event_oldest_first_with_log_coordinates';
function answer(found: DashboardCase) {
  return { status: found.status, body: found.body };
}
async function openRuntime(
  options: PageOptions = {},
  identity = identityDefault,
  diagnostics = diagnosticsDefault,
  hash = '#runtime',
) {
  window.history.replaceState(null, '', hash);
  const page = await renderPage({
    status: () => statusBody(sample('status.off')),
    ...options,
    before: (fake) => {
      vi.mocked(fetchTransport).mockImplementation(fake.transport);
      fake.route('GET', '/api/config', () => answer(pages('config_state.read_before_the_save')));
      fake.route('GET', '/api/settings', () =>
        answer(pages('settings.populated_account_every_module')),
      );
      fake.route('GET', '/api/runtime', () => answer(pages(identity)));
      fake.route('GET', /^\/api\/diagnostics\/events/u, (request) => {
        const search = new URL(request.path, 'http://fixture').searchParams;
        search.sort();
        const name =
          search.size === 0
            ? undefined
            : dashboardPagesCaseNames('runtime_diagnostics').find((name) => {
                const query = new URL(
                  pages(`runtime_diagnostics.${name}`).request.path,
                  'http://fixture',
                ).searchParams;
                query.sort();
                return query.toString() === search.toString();
              });
        return answer(pages(name === undefined ? diagnostics : `runtime_diagnostics.${name}`));
      });
      options.before?.(fake);
    },
  });
  await page.advance(1000);
  return page;
}
type Page = Awaited<ReturnType<typeof openRuntime>>;
const eventsRequests = (page: Page) =>
  page.fake.requests.filter((r) => r.path.startsWith('/api/diagnostics/events'));
function moduleBox(id: string) {
  const box = document.querySelector<HTMLElement>(`[data-runtime-module="${id}"]`);
  if (box === null) throw new Error(`module ${id} missing`);
  return box;
}
function input(id: string, key: string) {
  const found = moduleBox(id).querySelector<HTMLInputElement>(
    `[data-part="${key}"], [data-focus-key$=":${key}"]`,
  );
  if (found === null) throw new Error(`field ${id}.${key} missing`);
  return found;
}
async function edit(page: Page, id: string, key: string, value: string) {
  await page.user.clear(input(id, key));
  if (value !== '') await page.user.type(input(id, key), value);
}
beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  window.history.replaceState(null, '', '#dashboard');
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe('T1 T2 shared Runtime settings', () => {
  it.each([
    ['loading', 'Loading settings…'],
    ['unreadable', 'Settings are unreadable. Saved values and scope cannot be verified.'],
    ['other_context', 'The settings read names another context. Read this context before editing.'],
    ['missing', 'This settings module was not reported.'],
  ])(
    'T1 T2 %s settings have an explicit state and never offer an enabled save',
    async (state, line) => {
      await openRuntime({
        before: (fake) => {
          fake.route('GET', '/api/settings', () => {
            if (state === 'loading') return 'hold';
            if (state === 'unreadable')
              return answer(pages('settings.profile_unreadable_yaml_400'));
            if (state === 'other_context')
              return answer(pages('settings.read_after_the_switch_names_the_new_emulator'));
            const found = pages('settings.populated_account_every_module');
            const body = found.body as { modules: { id: string }[] };
            // No recorded settings response omits one Runtime module; remove only loop to exercise compatibility.
            return {
              status: found.status,
              body: { ...body, modules: body.modules.filter((module) => module.id !== 'loop') },
            };
          });
        },
      });
      expect(moduleBox('loop')).toHaveTextContent(line);
      for (const button of within(moduleBox('loop')).queryAllByRole('button', { name: 'Save' }))
        expect(button).toHaveAttribute('aria-disabled', 'true');
    },
  );
  it('T1 T2 SR-1 opening only reads; all eight modules, scopes, units and next-start limits are visible', async () => {
    const page = await openRuntime();
    expect(document.querySelectorAll('[data-runtime-module]')).toHaveLength(8);
    const overrides = document.querySelector<HTMLElement>('[data-runtime-group="overrides"]');
    const machine = document.querySelector<HTMLElement>('[data-runtime-group="machine"]');
    if (overrides === null || machine === null) throw new Error('settings groups missing');
    expect(
      within(overrides).getByText(/This emulator · next time automation starts/),
    ).toBeVisible();
    expect(
      within(machine).getByText(/This computer · every emulator · next time automation starts/),
    ).toBeVisible();
    expect(screen.getAllByText(/This emulator · next time automation starts/)).toHaveLength(1);
    expect(
      screen.getAllByText(/This computer · every emulator · next time automation starts/),
    ).toHaveLength(1);
    expect(screen.queryByText(/Dry run is not an emergency stop/)).toBeNull();
    await page.user.hover(
      screen.getByRole('button', { name: 'About Settings safety and readback' }),
    );
    expect(screen.getByRole('tooltip')).toHaveTextContent(/Dry run is not an emergency stop/);
    expect(input('loop', 'fps')).toBeInTheDocument();
    await page.advance(10000);
    expect(page.fake.writes()).toEqual([]);
    expect(textOutsideCatalogue(page.view.container)).toEqual([]);
    vi.useRealTimers();
    expect(await axe(page.view.container)).toHaveNoViolations();
  });
  it.each([
    ['loop', [['fps', '3']], ['dry_run'], 'settings.saved_loop'],
    [
      'missions',
      [
        ['visit_delay_sec#0', '90'],
        ['visit_delay_sec#1', '400'],
      ],
      [],
      'settings.saved_missions',
    ],
    [
      'fleet',
      [
        ['hold_window', '120'],
        ['post_window', '160'],
      ],
      [],
      'settings.saved_fleet',
    ],
    [
      'tap',
      [
        ['jitter_ms#0', '20'],
        ['jitter_ms#1', '60'],
        ['jitter_px', '6'],
        ['max_rate_per_sec', '10.5'],
      ],
      [],
      'settings.saved_tap',
    ],
    [
      'logging',
      [['bench_interval_sec', '60']],
      ['screenshot_on_trigger'],
      'settings.saved_logging',
    ],
  ] as const)(
    'T1 T2 SR-5 SR-6 %s saves every edited field with its context and revision and reads back',
    async (module, values, toggles, savedName) => {
      let settings = RUNTIME_SAVES[module].before;
      const page = await openRuntime({
        before: (fake) => {
          fake.route('GET', '/api/settings', () => answer(pages(settings)));
        },
      });
      for (const [key, value] of values) await edit(page, module, key, value);
      for (const key of toggles) await page.user.click(input(module, key));
      const save = within(moduleBox(module)).getByRole('button', { name: 'Save' });
      await page.user.click(save);
      await page.advance();
      const saved = pages(savedName);
      expect(postBodies(page.fake, '/api/settings')).toHaveLength(1);
      const request = page.fake.writes().find((entry) => entry.path === '/api/settings');
      if (request === undefined) throw new Error('save request missing');
      expect({
        method: request.method,
        path: request.path,
        body: JSON.parse(request.body ?? '') as unknown,
      }).toEqual(saved.request);
      settings = savedName;
      page.fake.next('/api/settings', 'POST').respond(saved.status, saved.body);
      await page.advance(5000);
      expect(save).toHaveFocus();
      for (const [key, value] of values) expect(input(module, key)).toHaveValue(value);
      expect(
        page.fake.requests.filter((r) => r.path === '/api/settings' && r.method === 'GET').length,
      ).toBeGreaterThan(1);
      expect(postBodies(page.fake, '/api/settings')).toHaveLength(1);
    },
  );
  it('T1 T2 SR-5 reversed and empty raw ranges retain text and reason and never send', async () => {
    const page = await openRuntime();
    await edit(page, 'missions', 'visit_delay_sec#0', '999');
    await edit(page, 'missions', 'visit_delay_sec#1', '90');
    const save = within(moduleBox('missions')).getByRole('button', { name: 'Save' });
    expect(save).toHaveAttribute('aria-disabled', 'true');
    expect(input('missions', 'visit_delay_sec#0')).toHaveValue('999');
    expect(moduleBox('missions').querySelector('[aria-invalid="true"]')).not.toBeNull();
    await edit(page, 'missions', 'visit_delay_sec#0', '');
    expect(input('missions', 'visit_delay_sec#0')).toHaveValue('');
    await page.user.click(save);
    expect(page.fake.writes()).toEqual([]);
  });
  it('T1 SR-4 a context change hides the original dirty draft, sends nothing, and restores it only in its own context', async () => {
    let config = 'config_state.read_before_the_save';
    const page = await openRuntime({
      before: (fake) => {
        fake.route('GET', '/api/config', () => answer(pages(config)));
      },
    });
    await edit(page, 'loop', 'fps', '3');
    const drafts = page.data.store.getSnapshot().runDrafts;
    expect(Object.keys(drafts).length).toBeGreaterThan(0);
    config = 'config_state.effective_view_of_the_other_account';
    await act(async () => {
      page.data.poller.read('config');
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(moduleBox('loop')).toHaveTextContent('The settings read names another context');
    expect(moduleBox('loop').querySelector('[data-part="fps"]')).toBeNull();
    expect(page.data.store.getSnapshot().runDrafts).toEqual(drafts);
    expect(page.fake.writes()).toEqual([]);
    config = 'config_state.read_before_the_save';
    await act(async () => {
      page.data.poller.read('config');
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(input('loop', 'fps')).toHaveValue('3');
    expect(page.fake.writes()).toEqual([]);
  });
  it('T2 SR-6 lost save stays Unknown after a different read and is never resent automatically', async () => {
    const page = await openRuntime();
    await edit(page, 'tap', 'jitter_px', '6');
    await page.user.click(within(moduleBox('tap')).getByRole('button', { name: 'Save' }));
    page.fake.next('/api/settings', 'POST').lose();
    await page.advance(15000);
    expect(moduleBox('tap')).toHaveTextContent('Unknown');
    expect(input('tap', 'jitter_px')).toHaveValue('6');
    expect(postBodies(page.fake, '/api/settings')).toHaveLength(1);
  });
  it('T1 T2 F6T-R1-01 unresolved save stays fenced after unchanged read and reload until explicit acceptance', async () => {
    const page = await openRuntime();
    await edit(page, 'tap', 'jitter_px', '6');
    await page.user.click(within(moduleBox('tap')).getByRole('button', { name: 'Save' }));
    page.fake.next('/api/settings', 'POST').lose();
    await page.advance(5000);
    await edit(page, 'tap', 'jitter_px', '7');
    expect(within(moduleBox('tap')).getByRole('button', { name: 'Save' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    const firstRecord = page.data.store
      .getSnapshot()
      .intents.find((record) => record.kind === 'settings_save');
    if (firstRecord === undefined) throw new Error('save record missing');
    page.view.unmount();
    page.data.stop();
    const reloaded = await openRuntime({ storage: page.storage });
    expect(input('tap', 'jitter_px')).toHaveValue('7');
    expect(within(moduleBox('tap')).getByRole('button', { name: 'Save' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    expect(reloaded.fake.writes()).toEqual([]);
    await reloaded.user.click(
      within(moduleBox('tap')).getByRole('button', {
        name: 'Continue from the saved values as read',
      }),
    );
    expect(reloaded.fake.writes()).toEqual([]);
    expect(
      reloaded.data.store.getSnapshot().intents.find((record) => record.id === firstRecord.id),
    ).toMatchObject({ phase: 'unconfirmed', accepted: true });
    expect(moduleBox('tap')).toHaveTextContent('Unknown');
    expect(within(moduleBox('tap')).getByRole('button', { name: 'Save' })).not.toHaveAttribute(
      'aria-disabled',
    );
    expect(input('tap', 'jitter_px')).toHaveValue('7');
  });
  it('T2 SR-6 a recorded revision conflict retains the draft and server reason', async () => {
    const page = await openRuntime({
      before: (fake) => {
        fake.route('POST', '/api/settings', () =>
          answer(pages('settings.stale_revision_another_client_saved_first')),
        );
      },
    });
    await edit(page, 'tap', 'jitter_px', '6');
    await page.user.click(within(moduleBox('tap')).getByRole('button', { name: 'Save' }));
    await page.advance(5000);
    expect(input('tap', 'jitter_px')).toHaveValue('6');
    expect(moduleBox('tap')).toHaveTextContent(
      (pages('settings.stale_revision_another_client_saved_first').body as { error: string }).error,
    );
    expect(postBodies(page.fake, '/api/settings')).toHaveLength(1);
  });
  it('T1 T2 F6T-R1-03 text-500 stays Unknown until positive readback, preserves newer typing across reload, and never resends', async () => {
    let settings = 'settings.populated_account_every_module';
    const page = await openRuntime({
      before: (fake) => {
        fake.route('GET', '/api/settings', () => answer(pages(settings)));
      },
    });
    await edit(page, 'tap', 'jitter_px', '6');
    await page.user.click(within(moduleBox('tap')).getByRole('button', { name: 'Save' }));
    const failed = pages('settings.saved_then_the_answer_failed_500');
    const write = page.fake.writes()[0];
    expect({
      method: write?.method,
      path: write?.path,
      body: JSON.parse(write?.body ?? '') as unknown,
    }).toEqual(failed.request);
    await edit(page, 'tap', 'jitter_px', '7');
    page.fake
      .next('/api/settings', 'POST')
      .respond(failed.status, (failed.body as { text: string }).text);
    await page.advance(5000);
    expect(moduleBox('tap')).toHaveTextContent('Unknown');
    expect(within(moduleBox('tap')).getByRole('button', { name: 'Save' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    settings = 'settings.read_after_the_unanswered_save_holds_the_value';
    await page.user.click(screen.getByRole('button', { name: 'Read settings again' }));
    await page.advance();
    expect(moduleBox('tap')).toHaveTextContent('A later read shows it saved');
    expect(input('tap', 'jitter_px')).toHaveValue('7');
    expect(page.fake.writes()).toHaveLength(1);
    const record = page.data.store
      .getSnapshot()
      .intents.find((entry) => entry.kind === 'settings_save');
    expect(record?.readSettlement).toBe('applied');
    page.view.unmount();
    page.data.stop();
    const reloaded = await openRuntime({
      storage: page.storage,
      before: (fake) => {
        fake.route('GET', '/api/settings', () => answer(pages(settings)));
      },
    });
    expect(input('tap', 'jitter_px')).toHaveValue('7');
    expect(reloaded.fake.writes()).toEqual([]);
  });
});

it.each([
  [
    'server_started_before_the_source_changed_restart_needed',
    'Source changed since the server loaded',
  ],
  [
    'disk_source_cannot_be_read_so_freshness_is_unknown',
    'Disk source unreadable; comparison cannot be verified',
  ],
  ['worker_that_cannot_be_verified_is_unknown_not_dead', 'Liveness cannot be verified'],
  ['process_discovery_unreadable', 'the process table could not be read'],
  ['process_discovery_stale', 'stale'],
  ['no_snapshot_could_be_taken_at_start_nothing_to_compare', 'not reported'],
])('T3 %s remains explicit and no process action is offered', async (name, line) => {
  const page = await openRuntime({}, `runtime_state.${name}`);
  const box = document.querySelector('[data-region="runtimeIdentity"]');
  expect(box).toHaveTextContent(line);
  const actions = within(box as HTMLElement)
    .getAllByRole('button')
    .filter((button) => !(button.getAttribute('aria-label') ?? '').startsWith('About '));
  expect(actions).toHaveLength(1);
  expect(page.fake.writes()).toEqual([]);
});
it('T3 unavailable identity retains last evidence and read failure', async () => {
  const page = await openRuntime();
  page.fake.route('GET', '/api/runtime', () => 'hold');
  await page.user.click(screen.getByRole('button', { name: 'Refresh identity' }));
  page.fake.next('/api/runtime').lose();
  await page.advance();
  expect(document.querySelector('[data-region="runtimeIdentity"]')).toHaveTextContent(
    'last result remains visible and is stale',
  );
  expect(screen.getByText('server-session-0001')).toBeVisible();
});
it('T3 source comparison stays visible while full fingerprint and capability evidence can be expanded', async () => {
  const page = await openRuntime();
  const box = document.querySelector<HTMLElement>('[data-region="runtimeIdentity"]');
  if (box === null) throw new Error('identity missing');
  expect(within(box).getByText('Matches the source snapshot')).toBeVisible();
  for (const label of [
    'Source fingerprint at load',
    'Disk source fingerprint now',
    'Capabilities',
    'Persistence operations',
  ]) {
    const term = within(box).getByText(label, { selector: 'dt' });
    const disclosure = term.nextElementSibling?.querySelector('details');
    if (disclosure === undefined || disclosure === null)
      throw new Error(`details missing: ${label}`);
    expect(disclosure).not.toHaveAttribute('open');
    const value = disclosure.querySelector('[data-server-text="detail"]');
    expect(value?.textContent.length).toBeGreaterThan(0);
    const summary = disclosure.querySelector('summary');
    if (summary === null) throw new Error('summary missing');
    await page.user.click(summary);
    expect(value).toBeVisible();
  }
  expect(page.fake.writes()).toEqual([]);
});
it('T4 section navigation focuses diagnostics without changing the applied query', async () => {
  const page = await openRuntime(
    {},
    identityDefault,
    diagnosticsDefault,
    '#runtime?operation_id=op-1',
  );
  await page.user.click(
    within(screen.getByRole('navigation', { name: 'Runtime sections' })).getByRole('button', {
      name: 'Diagnostic events',
    }),
  );
  expect(screen.getByRole('heading', { name: 'Diagnostic events' })).toHaveFocus();
  expect(window.location.hash).toBe('#runtime?operation_id=op-1');
  expect(screen.queryByText('Filters match exact values.')).toBeNull();
  await page.user.hover(screen.getByRole('button', { name: 'About Event filters' }));
  expect(screen.getByRole('tooltip')).toHaveTextContent('Filters match exact values.');
  expect(page.fake.writes()).toEqual([]);
});
it('T1 T2 save context is visible before the forms and common module scope text appears once', async () => {
  const page = await openRuntime();
  const target = page.data.store.getSnapshot().context;
  const context = document.getElementById('runtime-settings-review')?.closest('section');
  if (target === null || context === null || context === undefined)
    throw new Error('settings context missing');
  for (const [label, value] of [
    ['Instance', target.instance],
    ['Account', target.account],
    ['Profile', target.profile],
  ]) {
    expect(
      within(context).getByText(label ?? '', { selector: 'dt' }).nextElementSibling,
    ).toHaveTextContent(value ?? 'not reported');
  }
  expect(
    screen.queryByText(
      'Fallback for every emulator on this machine. An emulator that carries its own override keeps it.',
    ),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText('Applies to the selected emulator when its runner next starts.'),
  ).not.toBeInTheDocument();
  expect(screen.getAllByText(/Machine fallback · next time automation starts/)).toHaveLength(1);
  expect(page.fake.writes()).toEqual([]);
});
it('T5 event timestamps and IDs have separate labels and keep literal values visible', async () => {
  await openRuntime();
  const details = document.querySelector<HTMLDetailsElement>('[data-event-key]');
  if (details === null || details.parentElement === null) throw new Error('event missing');
  const summary = details.querySelector('summary');
  if (summary === null) throw new Error('summary missing');
  expect(
    within(summary).getByText('Event time (epoch seconds)').nextElementSibling,
  ).toHaveTextContent('1800000001');
  expect(
    within(details.parentElement).getByText('Operation ID', { selector: 'dt' }).nextElementSibling,
  ).toHaveTextContent('op-1');
  expect(details).not.toHaveAttribute('open');
});
it('T3 F6T-R1-02 missing worker build identity stays not reported and the complete raw runtime response is available', async () => {
  await openRuntime({}, 'runtime_state.worker_alive_and_verified');
  const box = document.querySelector<HTMLElement>('[data-region="runtimeIdentity"]');
  if (box === null) throw new Error('identity missing');
  for (const label of [
    'Worker source revision',
    'Worker source fingerprint at load',
    'Worker source captured at (epoch seconds)',
  ]) {
    const term = within(box).getByText(label, { selector: 'dt' });
    expect(term.nextElementSibling).toHaveTextContent('not reported');
  }
  fireEvent.click(within(box).getByText('Retained raw runtime response'));
  expect(
    within(box).getByText('Retained raw runtime response').closest('details')?.querySelector('pre'),
  ).toHaveTextContent('"pid": 4400');
});
it.each(['network', 'http400'])(
  'T1 T2 F6T-R1-04 a %s settings refresh failure labels retained values stale and keeps the raw draft',
  async (failure) => {
    const page = await openRuntime();
    await edit(page, 'loop', 'fps', '3.5');
    const box = document.querySelector<HTMLElement>('[data-region="runtimeSettings"]');
    if (box === null) throw new Error('settings missing');
    page.fake.route('GET', '/api/settings', () =>
      failure === 'network' ? 'hold' : answer(pages('settings.profile_unreadable_yaml_400')),
    );
    await page.user.click(screen.getByRole('button', { name: 'Read settings again' }));
    if (failure === 'network') page.fake.next('/api/settings', 'GET').lose();
    await page.advance();
    expect(box).toHaveTextContent('Settings refresh failed. Retained settings are stale.');
    expect(box.querySelector('time')).toHaveTextContent('Last read:');
    expect(input('loop', 'fps')).toHaveValue('3.5');
    if (failure === 'http400')
      expect(
        box.querySelector('[data-settings-read] [data-server-text="message"]')?.textContent,
      ).toBe((pages('settings.profile_unreadable_yaml_400').body as { error: string }).error);
    else expect(box).toHaveTextContent('The server could not be reached.');
    expect(page.fake.writes()).toEqual([]);
  },
);
it('T4 restores applied hash filters and Refresh keeps unapplied typing and invalid limits', async () => {
  const page = await openRuntime(
    {},
    identityDefault,
    diagnosticsDefault,
    '#runtime?operation_id=op-1',
  );
  expect(screen.getByLabelText('Operation ID')).toHaveValue('op-1');
  expect(eventsRequests(page).at(-1)?.path).toBe('/api/diagnostics/events?operation_id=op-1');
  await page.user.type(screen.getByLabelText('Kind'), 'unapplied');
  await page.user.clear(screen.getByLabelText('Event limit (1–1000)'));
  await page.user.type(screen.getByLabelText('Event limit (1–1000)'), '1001');
  await page.user.click(screen.getByRole('button', { name: 'Refresh events' }));
  await page.advance();
  expect(eventsRequests(page).at(-1)?.path).toBe('/api/diagnostics/events?operation_id=op-1');
  expect(screen.getByLabelText('Kind')).toHaveValue('unapplied');
  expect(screen.getByLabelText('Event limit (1–1000)')).toHaveValue('1001');
  expect(screen.getByRole('button', { name: 'Apply filters' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
});
it('T4 Apply sends every exact filter and browser-local Since as epoch seconds', async () => {
  const page = await openRuntime();
  for (const label of [
    'Instance',
    'Operation ID',
    'Battle ID',
    'Command ID',
    'Trace ID',
    'Request ID',
    'Attempt ID',
    'Window ID',
    'Offer ID',
    'Kind',
  ])
    await page.user.type(screen.getByLabelText(label), label);
  fireEvent.change(screen.getByLabelText(/^Since/), { target: { value: '2027-01-15T12:00' } });
  await page.user.click(screen.getByLabelText('Failures only'));
  await page.user.click(screen.getByRole('button', { name: 'Apply filters' }));
  await page.advance();
  const query = new URL(eventsRequests(page).at(-1)?.path ?? '', 'http://fixture').searchParams;
  expect(query.size).toBe(12);
  expect(query.get('since')).toBe(String(new Date('2027-01-15T12:00').getTime() / 1000));
  expect(query.get('failures')).toBe('1');
  expect(window.location.hash).toContain('operation_id=Operation+ID');
});
it('T4 optional timer uses applied filters, stops on hide and unmount, and leaves focus and drafts alone', async () => {
  const page = await openRuntime();
  await page.user.click(screen.getByLabelText(/Auto-refresh every 5 seconds/));
  const field = screen.getByLabelText('Kind');
  await page.user.type(field, 'draft');
  const before = eventsRequests(page).length;
  await page.advance(5000);
  expect(eventsRequests(page)).toHaveLength(before + 1);
  expect(field).toHaveFocus();
  expect(field).toHaveValue('draft');
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
  fireEvent(document, new Event('visibilitychange'));
  await page.advance(10000);
  expect(eventsRequests(page)).toHaveLength(before + 1);
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  fireEvent(document, new Event('visibilitychange'));
  await page.advance(5000);
  expect(eventsRequests(page)).toHaveLength(before + 2);
  page.view.unmount();
  await page.advance(10000);
  expect(eventsRequests(page)).toHaveLength(before + 2);
});
it.each([
  ['no_log_folder_yet_nothing_scanned', 'This does not mean no events ever occurred'],
  ['malformed_lines_are_counted_and_a_half_written_last_line_is_truncation', 'Malformed records'],
  [
    'one_file_cannot_be_read_the_others_are_returned_and_the_error_is_listed',
    'the file is locked by another process',
  ],
  ['writer_health_reports_failed_writes', 'the log file is locked'],
])('T5 bounded diagnostics %s shows coverage and health beside results', async (name, line) => {
  await openRuntime({}, identityDefault, `runtime_diagnostics.${name}`);
  expect(document.querySelector('[data-region="runtimeDiagnostics"]')).toHaveTextContent(line);
  expect(screen.getByRole('heading', { name: 'Scan coverage' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Dashboard writer health' })).toBeVisible();
});
it('T5 event outcomes and failure evidence remain visible with raw JSON closed', async () => {
  const page = await openRuntime();
  const events = document.querySelector<HTMLElement>('[aria-labelledby="runtime-events"]');
  if (events === null) throw new Error('Event results missing');
  for (const text of [
    'accepted',
    'unconfirmed',
    'timeout',
    'completed',
    'skipped',
    'the device went away',
    'error',
  ]) {
    expect(within(events).getByText(text, { exact: true })).toBeVisible();
  }
  const raw = events.querySelectorAll('details[data-event-key]');
  expect(raw).toHaveLength(5);
  for (const details of raw) {
    expect(details).not.toHaveAttribute('open');
    expect(details.querySelector('pre')).not.toBeVisible();
  }
  expect(page.fake.writes()).toEqual([]);
});

it('T5 T6 raw JSON, disclosure and focused summary survive refresh and a 400 retains stale results with server text', async () => {
  const page = await openRuntime();
  const details = document.querySelector<HTMLDetailsElement>('[data-event-key]');
  if (details === null) throw new Error('event missing');
  const summary = details.querySelector('summary');
  if (summary === null) throw new Error('summary missing');
  await page.user.click(summary);
  expect(details).toHaveAttribute('open');
  summary.focus();
  await page.user.click(screen.getByLabelText(/Auto-refresh every 5 seconds/));
  summary.focus();
  await page.advance(5000);
  expect(details).toHaveAttribute('open');
  expect(summary).toHaveFocus();
  expect(details.querySelector('pre')).toHaveTextContent('"outcome": "accepted"');
  page.fake.route('GET', /^\/api\/diagnostics\/events/u, () =>
    answer(pages('runtime_diagnostics.unknown_filter_refused')),
  );
  await page.advance(5000);
  expect(details).toHaveAttribute('open');
  expect(summary).toHaveFocus();
  expect(screen.getByText('Unknown diagnostic filter: colour')).toBeVisible();
  expect(document.querySelector('[data-region="runtimeDiagnostics"]')).toHaveTextContent(
    'last result remains visible and is stale',
  );
});
it('T6 export downloads the last applied result with coverage and health, ignoring unsent draft filters', async () => {
  const page = await openRuntime(
    {},
    identityDefault,
    diagnosticsDefault,
    '#runtime?operation_id=op-1',
  );
  await page.user.type(screen.getByLabelText('Kind'), 'unsent');
  let blob: Blob | undefined;
  const create = vi.fn((value: Blob) => {
    blob = value;
    return 'blob:runtime';
  });
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: create });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
  await page.user.click(screen.getByRole('button', { name: 'Export last result as JSON' }));
  expect(create).toHaveBeenCalledTimes(1);
  expect(click).toHaveBeenCalledOnce();
  if (blob === undefined) throw new Error('no export');
  const exportedBlob = blob;
  vi.useRealTimers();
  const text = await new Promise<string>((resolve) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === 'string') resolve(reader.result);
    };
    reader.readAsText(exportedBlob);
  });
  const exported = JSON.parse(text) as {
    filters: unknown;
    coverage: unknown;
    writer_health: unknown;
    events: unknown;
  };
  const body = pages('runtime_diagnostics.filter_by_operation').body as {
    coverage: unknown;
    writer_health: unknown;
    events: unknown;
  };
  expect(exported).toMatchObject({ filters: { operation_id: 'op-1' }, ...body });
  expect(page.fake.writes()).toEqual([]);
  click.mockRestore();
});
