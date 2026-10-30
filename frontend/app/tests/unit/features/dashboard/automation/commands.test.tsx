import { act, within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, expect, it, vi } from 'vitest';
import * as automationSelector from '../../../../../src/store/selectors/automationSlot.ts';
import { textOutsideCatalogue } from '../../../copy/renderedText.ts';
import { dashboardBody } from '../../../../support/dashboardFixture.ts';
import { sample, sampleRecord, statusBody } from '../../../../support/fixture.ts';
import { renderPage, type PageHarness } from '../harness.tsx';

let page: PageHarness | undefined;
afterEach(async () => {
  vi.restoreAllMocks();
  const current = page;
  page = undefined;
  if (current !== undefined) {
    current.data.stop();
    try {
      expect(textOutsideCatalogue(current.view.container)).toEqual([]);
      vi.useRealTimers();
      expect(await axe(current.view.container)).toHaveNoViolations();
    } finally {
      current.view.unmount();
    }
  }
  vi.useRealTimers();
});

function object(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value))
    throw new Error('Expected fixture object');
  return value as Record<string, unknown>;
}

function slot(h: PageHarness) {
  const element = h.view.container.querySelector('[data-slot="automation"]');
  if (!(element instanceof HTMLElement)) throw new Error('Missing automation slot');
  return within(element);
}

it('F-011: double confirmation click sends one cancel POST at the highest revision while polls continue', async () => {
  vi.useFakeTimers();
  const harvest = sampleRecord('status.collecting');
  const operation = object(harvest['operation']);
  const id = String(operation['id']);
  const body = statusBody(harvest, {
    processes: [
      {
        pid: 401,
        created: 1800000000,
        runner: 'gem_collector',
        operation_id: id,
        operation_revision: 19,
      },
    ],
  });
  page = await renderPage({ status: () => body });
  const h = page;
  await h.user.click(slot(h).getByRole('button', { name: /^Stop Gem collector/ }));
  const dialog = h.view.getByRole('dialog');
  expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();
  expect(h.fake.writes()).toEqual([]);
  await h.user.dblClick(within(dialog).getByRole('button', { name: 'Stop' }));
  await h.advance();
  const writes = h.fake.writes();
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({ method: 'POST', path: `/api/operations/${id}/cancel` });
  expect(JSON.parse(writes[0]?.body ?? 'null')).toEqual({ expected_revision: 19 });
  const held = h.fake.next(`/api/operations/${id}/cancel`, 'POST');
  const before = h.fake.requests.filter((r) => r.path === '/api/status').length;
  await h.advance(2500);
  expect(held.settled()).toBe(false);
  expect(h.fake.requests.filter((r) => r.path === '/api/status').length).toBeGreaterThan(before);
  expect(h.fake.writes()).toHaveLength(1);
});

it('F-010: malformed Details still posts the exact operation cancel', async () => {
  vi.useFakeTimers();
  const harvest = sampleRecord('status.collecting');
  const operation = object(harvest['operation']);
  const id = String(operation['id']);
  const revision = Number(operation['revision']);
  page = await renderPage({
    status: () =>
      statusBody(harvest, {
        processes: [
          {
            pid: 402,
            created: 1800000000,
            runner: 'gem_collector',
            operation_id: id,
            operation_revision: revision,
            cmdline: { toString: null },
          },
        ],
      }),
  });
  const h = page;
  await h.user.click(slot(h).getByText('Details'));
  expect(slot(h).getByText('Details unavailable')).toBeVisible();
  await h.user.click(slot(h).getByRole('button', { name: /^Stop Gem collector/ }));
  await h.user.click(within(h.view.getByRole('dialog')).getByRole('button', { name: 'Stop' }));
  expect(h.fake.writes()).toHaveLength(1);
  expect(h.fake.writes()[0]).toMatchObject({
    path: `/api/operations/${id}/cancel`,
    body: JSON.stringify({ expected_revision: revision }),
  });
});

it.each([
  ['gone', 'no longer running'],
  ['pid_passed_to_another_runner', "not stopped; couldn't prove it is the same process"],
])('F-012: exact kill POST and %s outcome are never resent', async (answerName, line) => {
  vi.useFakeTimers();
  const row = { pid: 4101, created: 1799990001.25, runner: 'orchestrator' };
  page = await renderPage({ status: () => statusBody(sample('status.off'), { processes: [row] }) });
  const h = page;
  await h.user.click(slot(h).getByRole('button', { name: /Stop process/ }));
  await h.user.click(
    within(h.view.getByRole('dialog')).getByRole('button', { name: 'Stop process' }),
  );
  expect(h.fake.writes()).toHaveLength(1);
  expect(JSON.parse(h.fake.writes()[0]?.body ?? 'null')).toEqual({
    action: 'kill',
    pid: row.pid,
    created: row.created,
  });
  const answer = object(sample(`stop_operation.kill.${answerName}`));
  act(() => {
    h.fake.next('/api/control', 'POST').respond(Number(answer['status']), answer['body']);
  });
  await h.advance();
  expect(h.view.container.textContent.toLowerCase()).toContain(line);
  expect(h.view.container.textContent.toLowerCase()).not.toContain('killed');
  await h.advance(5000);
  expect(h.fake.writes()).toHaveLength(1);
});

it.each(['lost', '5xx'])(
  'F-014: %s scheduled-stop answer stays Unknown with Dismiss, never resends, and later flag false is current state',
  async (failure) => {
    vi.useFakeTimers();
    const fixture = object(dashboardBody('status.stop_flag_true_with_a_listed_run'));
    const rows = fixture['processes'] as Record<string, unknown>[];
    let body = statusBody(sample('status.off'), { processes: rows });
    page = await renderPage({ status: () => body });
    const h = page;
    await h.user.click(slot(h).getByRole('button', { name: 'Cancel scheduled stop' }));
    const operation = object(rows[0]?.['operation']);
    expect(h.fake.writes()).toHaveLength(1);
    expect(JSON.parse(h.fake.writes()[0]?.body ?? 'null')).toEqual({
      action: 'clear_flag',
      operation_id: operation['id'],
      expected_revision: operation['revision'],
    });
    act(() => {
      const held = h.fake.next('/api/control', 'POST');
      if (failure === 'lost') held.lose();
      else held.respond(502, { error: 'uncertain' });
    });
    await h.advance();
    const outcome = h.view.container.querySelector('[data-outcome-id]');
    expect(outcome).not.toBeNull();
    if (!(outcome instanceof HTMLElement)) throw new Error('Missing scheduled-stop outcome');
    expect(within(outcome).getByText('Unknown')).toBeVisible();
    expect(within(outcome).getByRole('button', { name: 'Dismiss' })).toBeVisible();
    body = statusBody(sample('status.off'), {
      processes: rows.map((row) => ({ ...row, stop_flag: false })),
    });
    await h.advance(30000);
    expect(slot(h).getByText('Will not stop after this run')).toBeVisible();
    expect(h.fake.writes()).toHaveLength(1);
  },
);

it.each(['cold', 'warm'])(
  'F-016: %s throwing automation VM getter keeps per-operation Stops, Turn input off and one diagnostic',
  async (temperature) => {
    vi.useFakeTimers();
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const select = automationSelector.selectAutomationSlot;
    let fault = temperature === 'cold';
    vi.spyOn(automationSelector, 'selectAutomationSlot').mockImplementation((state) => {
      const vm = select(state);
      return fault
        ? {
            ...vm,
            get rows(): typeof vm.rows {
              throw new Error('automation getter fault');
            },
          }
        : vm;
    });
    const harvest = sampleRecord('status.collecting');
    const id = object(harvest['operation'])['id'];
    page = await renderPage({ status: () => statusBody(harvest) });
    const h = page;
    if (!fault) {
      expect(slot(h).getByText('Gem collector')).toBeVisible();
      fault = true;
      await h.advance(2500);
    }
    expect(slot(h).getByText("Can't show automation state")).toBeVisible();
    expect(slot(h).getByText('Unknown')).toBeVisible();
    expect(slot(h).getByRole('button', { name: 'Turn input off' })).toBeEnabled();
    expect(slot(h).getByRole('button', { name: `Stop Operation:${String(id)}` })).toBeEnabled();
    expect(slot(h).queryByRole('button', { name: /Start/ })).toBeNull();
    await h.advance(5000);
    expect(h.fake.requests.filter((r) => r.path === '/api/diagnostics/client')).toHaveLength(1);
    await h.user.click(slot(h).getByRole('button', { name: `Stop Operation:${String(id)}` }));
    await h.user.click(within(h.view.getByRole('dialog')).getByRole('button', { name: 'Stop' }));
    expect(
      h.fake.writes().filter((r) => r.path === `/api/operations/${String(id)}/cancel`),
    ).toHaveLength(1);
  },
);

it('F-016: injected slot fault keeps the cold first-read Stop and no fallback Start', async () => {
  vi.useFakeTimers();
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
  page = await renderPage({
    faults: ['slot:automation'],
    status: () => statusBody(sample('status.collecting')),
  });
  expect(slot(page).getByText("Can't show automation state")).toBeVisible();
  expect(slot(page).getByRole('button', { name: 'Turn input off' })).toBeEnabled();
  expect(slot(page).getAllByRole('button', { name: /^Stop/ })).toHaveLength(1);
  expect(slot(page).queryByRole('button', { name: /Start/ })).toBeNull();
  expect(page.fake.requests.filter((r) => r.path === '/api/diagnostics/client')).toHaveLength(1);
});
