import { fireEvent, screen, within, type RenderResult } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AutomationSlot } from '../../../../../src/features/dashboard/control/AutomationSlot.tsx';
import { ControlRegion } from '../../../../../src/features/dashboard/control/ControlRegion.tsx';
import { copy } from '../../../../../src/copy/catalogue.ts';
import { selectAutomationSlot } from '../../../../../src/store/selectors/automationSlot.ts';
import { selectInputSlot } from '../../../../../src/store/selectors/inputSlot.ts';
import { textOutsideCatalogue } from '../../../copy/renderedText.ts';
import { MATRIX } from '../../../spec/specData.ts';
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
import {
  freshScan,
  sample,
  sampleRecord,
  serverTimeOf,
  statusBody,
} from '../../../../support/fixture.ts';
import { factValue, renderRegion } from '../harness.tsx';

let view: RenderResult | undefined;
afterEach(async () => {
  vi.restoreAllMocks();
  const current = view;
  view = undefined;
  if (current === undefined) return;
  try {
    expect(textOutsideCatalogue(current.container)).toEqual([]);
    expect(await axe(current.container)).toHaveNoViolations();
  } finally {
    current.unmount();
  }
});

function draw(state = regionState(), whole = false) {
  const harness = renderRegion(state, whole ? <ControlRegion /> : <AutomationSlot />);
  view = harness.view;
  return harness;
}

function status(harvest: unknown = sample('status.off'), processes: unknown = []) {
  return addBody(regionState(), 'status', statusBody(harvest, { processes }));
}

function object(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value))
    throw new Error('Expected fixture object');
  return value as Record<string, unknown>;
}

function processes(name: string) {
  return object(dashboardBody(`status.${name}`))['processes'];
}

describe('Automation decisions', () => {
  it.each(MATRIX.flatMap((row) => [false, true].map((allow) => ({ ...row, allow }))))(
    'F-008: section 9.1 Automation word for $sample with allow_taps $allow',
    ({ sample: name, automation, allow }) => {
      const h = draw(addBody(status(sample(`status.${name}`)), 'config', e0Config(allow)));
      expect(h.view.container.querySelector('.tp-chip')).toHaveAttribute(
        'data-state',
        automation.toLowerCase(),
      );
    },
  );

  it('F-008: a listed process is Running and browser clock shifts change nothing', () => {
    const state = status(sample('status.off'), processes('processes_two_rows_same_runner'));
    const h = draw(state);
    const initial = h.view.container.textContent;
    const now = Date.now();
    for (const offset of [-86400000, 86400000]) {
      vi.spyOn(Date, 'now').mockReturnValue(now + offset);
      h.setState({ ...state });
      expect(h.view.container.textContent).toBe(initial);
      expect(h.view.container.querySelectorAll('[data-state="running"]')).toHaveLength(2);
    }
  });

  it.each([
    ['harvest missing', undefined],
    ['harvest null', null],
    ['wrong version', { ...sampleRecord('status.stopping'), version: 2 }],
    ['failed', sample('status.status_failed')],
    ['unreadable', sample('status.state_unreadable')],
  ])('F-008: S-027 %s shows Unknown and Why', (_name, harvest) => {
    const body = statusBody(harvest);
    if (_name === 'harvest missing') delete body['harvest'];
    const h = draw(addBody(regionState(), 'status', body));
    expect(h.view.container.querySelector('.tp-chip')).toHaveAttribute('data-state', 'unknown');
    // The background is a help icon, never a Why disclosure; opened, it says why nothing is proved.
    expect(h.view.queryByText('Why')).toBeNull();
    const icon = h.view.getAllByRole('button', { name: /^About / })[0];
    if (icon === undefined) throw new Error('no help icon');
    fireEvent.click(icon);
    expect(screen.getByRole('tooltip')).toHaveTextContent(
      copy.dashboard.automation.lines.cant_tell_whats_running.why ?? '',
    );
  });

  it('F-008: stale scan keeps Last known idle; failed read keeps Stops', () => {
    let state = status();
    const h = draw(state);
    state = tick(state, 30);
    state = addBody(
      state,
      'status',
      statusBody(sample('status.off'), { scan: freshScan(serverTimeOf(sample('status.off')), 30) }),
    );
    h.setState(state);
    expect(h.view.getByText('Last known: idle, 30 s ago')).toBeVisible();
    state = addBody(state, 'status', statusBody(sample('status.collecting')));
    state = addFailure(state, 'status');
    h.setState(state);
    expect(h.view.getByRole('button', { name: /^Stop Gem collector/ })).toBeEnabled();
    expect(h.view.container.querySelector('.tp-chip')).toHaveAttribute('data-state', 'unknown');
    expect(h.view.getByText('last read 0 s ago')).toBeVisible();
  });

  it('F-009: purpose names use the run label and Screen learning; setup progress is separate', () => {
    const rows = [
      { pid: 1, created: 1799999000, runner: 'gem_collector' },
      {
        pid: 2,
        created: 1799999001,
        runner: 'orchestrator',
        operation: { kind: 'run', context: { selected_run: 'bp_farm' } },
      },
      { pid: 3, created: 1799999002, runner: 'calibrate' },
    ];
    let state = addCase(status(sample('status.off'), rows), 'runs', 'runs.ready');
    state = addCase(state, 'calibrate', 'calibrate_status.running_with_steps');
    const h = draw(state);
    expect(h.view.getByText('Gem collector')).toBeVisible();
    expect(h.view.getByText('Farm')).toBeVisible();
    expect(h.view.getByText('Screen learning')).toBeVisible();
    expect(factValue(h.view.container, 'Setup running')).toBeVisible();
    expect(
      h.view.getByText('Cards: page 2 read; 8 cards identified; scrolling down'),
    ).toHaveAttribute('data-server-text', 'detail');
  });

  it.each([
    ['collecting', 9396, 0, 'Wave', '9,396'],
    ['worker_unverified', 9396, 0, 'Last wave', '9,396, 0 s ago'],
    ['collecting', null, 0, 'Battle', 'Wave not reported yet'],
    ['collecting', 9396, 30, 'Last wave', '9,396, 30 s ago'],
  ])('F-009: %s wave %s age %s reads %s', (name, wave, age, label, value) => {
    const harvest = sampleRecord(`status.${name}`);
    const body = object(dashboardBody('battles_active.runtime_ad_orbit_shopping'));
    const battle = object(body['battle']);
    battle['operation_id'] = object(harvest['operation'])['id'];
    const runtime = object(battle['runtime']);
    runtime['observed_wave'] = wave;
    runtime['server_time'] = Number(body['server_time']) - age;
    const state = addBody(status(harvest), 'battlesActive', body);
    const h = draw(state);
    expect(factValue(h.view.container, label)).toHaveTextContent(value);
    expect(factValue(h.view.container, label)).toBeVisible();
    expect(h.view.container.textContent).not.toMatch(/No battle running|battle is running/i);
  });

  it.each([
    ['waiting_for_battle', 'Waiting to track a battle'],
    ['collecting', 'Unknown'],
  ])('F-009: %s retains the operation when battle status is unavailable', (name, line) => {
    const h = draw(addFailure(status(sample(`status.${name}`)), 'battlesActive'));
    expect(factValue(h.view.container, 'Battle')).toHaveTextContent(line);
    expect(h.view.getByRole('button', { name: /^Stop Gem collector/ })).toBeEnabled();
  });

  it('F-009: null battle says No tracked battle without a game claim', () => {
    const h = draw(
      addCase(
        status(sample('status.collecting')),
        'battlesActive',
        'battles_active.null_no_driver',
      ),
    );
    expect(h.view.getByText('No tracked battle')).toBeVisible();
    expect(h.view.container.textContent).not.toMatch(/No battle running/);
  });

  it('F-009: an owner run uses its matching battle blueprint label while a linked collector keeps its purpose', () => {
    const harvest = sampleRecord('status.owned_by_run_gathers');
    const body = object(dashboardBody('battles_active.runtime_ad_orbit_shopping'));
    object(body['battle'])['operation_id'] = object(harvest['owner'])['operation_id'];
    const ownerState = addBody(
      addCase(status(harvest), 'runs', 'runs.ready'),
      'battlesActive',
      body,
    );
    const h = draw(ownerState);
    expect(h.view.getByText('Farm')).toBeVisible();
    h.setState(
      addCase(
        status(sample('status.off'), processes('processes_linked_collector')),
        'runs',
        'runs.ready',
      ),
    );
    expect(h.view.getByText('Gem collector')).toBeVisible();
    expect(h.view.queryByText('Farm')).toBeNull();
  });

  it('F-010: duplicate purposes keep distinct start times, PIDs, server command lines and Details open for five polls', () => {
    const state = status(sample('status.off'), processes('processes_two_rows_same_runner'));
    const h = draw(state);
    expect(h.view.getByText(/Started .*, PID 4511/)).not.toBeVisible();
    expect(h.view.getByText(/Started .*, PID 4512/)).not.toBeVisible();
    const rows = h.view.container.querySelectorAll<HTMLElement>('[data-operation-key]');
    const starts = Array.from(rows, (row) => factValue(row, 'Started'));
    expect(starts).toHaveLength(2);
    for (const started of starts) expect(started).toBeVisible();
    expect(starts[0]?.textContent).not.toBe(starts[1]?.textContent);
    const details = h.view.getAllByText('Details')[0]?.closest('details');
    expect(details).not.toBeNull();
    if (details === null || details === undefined) throw new Error('Missing Details');
    fireEvent.click(within(details).getByText('Details'));
    expect(details).toHaveAttribute('open');
    expect(factValue(details, 'PID')).toHaveTextContent('4511');
    for (let poll = 1; poll <= 5; poll += 1) {
      h.setState(
        addBody(
          tick(state, poll),
          'status',
          statusBody(sample('status.off'), {
            processes: processes('processes_two_rows_same_runner'),
          }),
        ),
      );
      expect(h.view.getAllByText('Details')[0]?.closest('details')).toBe(details);
      expect(details).toHaveAttribute('open');
    }
    expect(details.querySelector('[data-server-text="detail"]')).toHaveTextContent(
      'C:/Python/pythonw.exe',
    );
    expect(h.view.getAllByRole('button', { name: /Stop process/ })).toHaveLength(2);
    const stops = h.view.getAllByRole('button', { name: /Stop process/ });
    expect(stops[0]).toHaveAccessibleName(/^Stop process Run Started .*, PID 4511$/);
    expect(stops[1]).toHaveAccessibleName(/^Stop process Run Started .*, PID 4512$/);
  });

  it('F-010: missing start time is explicit and malformed cmdline leaves Stop usable', () => {
    const rows = [
      {
        pid: 42,
        created: null,
        runner: 'gem_collector',
        cmdline: { toString: null },
        operation_id: 'operation-42',
        operation_revision: 7,
      },
    ];
    const h = draw(status(sample('status.off'), rows));
    expect(h.view.getByText('Start time not recorded, PID 42')).not.toBeVisible();
    expect(factValue(h.view.container, 'Started')).toHaveTextContent('Not recorded');
    expect(factValue(h.view.container, 'Started')).toBeVisible();
    fireEvent.click(h.view.getByText('Details'));
    expect(h.view.getByText('Details unavailable')).toBeVisible();
    expect(factValue(h.view.container, 'PID')).toHaveTextContent('42');
    expect(factValue(h.view.container, 'PID')).toBeVisible();
    fireEvent.click(h.view.getByRole('button', { name: /^Stop Gem collector/ }));
    fireEvent.click(within(h.view.getByRole('dialog')).getByRole('button', { name: 'Stop' }));
    expect(h.commands.stop).toHaveBeenCalledExactlyOnceWith(
      selectAutomationSlot(h.store.getSnapshot()).rows[0]?.stop.command,
    );
  });

  it('F-011: confirmation sends the exact VM command with the highest known revision', () => {
    const state = status(sample('status.collecting'));
    const operationId = object(sampleRecord('status.collecting')['operation'])['id'];
    const newer = intent(state, {
      kind: 'stop',
      target: `operation:${String(operationId)}`,
      meta: { operationId: String(operationId), expectedRevision: 12 },
    });
    const h = draw({ ...state, intents: [newer] });
    fireEvent.click(h.view.getByRole('button', { name: /^Stop Gem collector/ }));
    const dialog = h.view.getByRole('dialog');
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();
    expect(h.commands.stop).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Stop' }));
    expect(h.commands.stop).toHaveBeenCalledExactlyOnceWith({
      kind: 'cancel',
      operationId,
      expectedRevision: 12,
      epoch: state.epoch,
    });
  });

  it.each(['nested process', 'owner recovery', 'Stop conflict'])(
    'F-011: highest valid revision from %s wins for the exact id',
    (source) => {
      const harvest = sampleRecord('status.collecting');
      const operation = object(harvest['operation']);
      const id = String(operation['id']);
      const row = {
        pid: 73,
        created: 100,
        runner: 'gem_collector',
        operation_id: id,
        operation_revision: operation['revision'],
        operation: { id, revision: source === 'nested process' ? 17 : -1 },
      };
      if (source === 'owner recovery')
        harvest['owner'] = {
          kind: 'operation',
          operation_id: id,
          operation_kind: 'gems',
          status: 'running',
          recovery: { action: 'stop_operation', operation_id: id, expected_revision: 17 },
        };
      let state = status(harvest, [row]);
      if (source === 'Stop conflict')
        state = {
          ...state,
          intents: [
            intent(state, {
              kind: 'stop',
              target: `operation:${id}`,
              answer: {
                status: 409,
                body: { code: 'operation_changed', operation: { id, revision: 17 } },
              },
            }),
          ],
        };
      const h = draw(state);
      fireEvent.click(h.view.getByRole('button', { name: /^Stop Gem collector/ }));
      fireEvent.click(within(h.view.getByRole('dialog')).getByRole('button', { name: 'Stop' }));
      expect(h.commands.stop).toHaveBeenCalledExactlyOnceWith({
        kind: 'cancel',
        operationId: id,
        expectedRevision: 17,
        epoch: state.epoch,
      });
    },
  );

  it('F-012: kill uses the exact row identity; missing creation time disables Stop and Game input offers Turn input off', () => {
    const rows = [
      { pid: 91, created: 1800000000.25, runner: 'orchestrator' },
      { pid: 92, created: null, runner: 'orchestrator' },
    ];
    const state = addBody(status(sample('status.off'), rows), 'config', e0Config());
    const h = draw(state, true);
    const stops = h.view.getAllByRole('button', { name: /^Stop / });
    expect(stops[1]).toHaveAttribute('aria-disabled', 'true');
    expect(stops[1]).toHaveAccessibleDescription(
      expect.stringContaining("Can't prove it is the same process"),
    );
    expect(selectInputSlot(state).turnOff).toMatchObject({ shown: true, enabled: true });
    const inputSlot = h.view.container.querySelector('[data-slot="input"]');
    if (!(inputSlot instanceof HTMLElement)) throw new Error('Missing Game input slot');
    expect(within(inputSlot).getByRole('button', { name: 'Turn input off' })).toBeEnabled();
    if (stops[0] === undefined) throw new Error('Missing first Stop');
    fireEvent.click(stops[0]);
    fireEvent.click(
      within(h.view.getByRole('dialog')).getByRole('button', { name: 'Stop process' }),
    );
    expect(h.commands.stop).toHaveBeenCalledExactlyOnceWith({
      kind: 'kill',
      pid: 91,
      created: 1800000000.25,
      epoch: state.epoch,
    });
  });

  it.each([0, 3600, 86400])(
    'F-013: retained stopping holder keeps a working Stop after %i seconds',
    (elapsed) => {
      const harvest = sampleRecord('status.stopping');
      harvest['server_time'] = serverTimeOf(harvest) + elapsed;
      const h = draw(status(harvest));
      expect(h.view.getByText('Stopping')).toBeVisible();
      expect(h.view.getByText('Stop not confirmed')).toBeVisible();
      expect(h.view.getByRole('button', { name: /^Stop Gem collector/ })).toBeEnabled();
    },
  );

  it('F-013: unknown revision disables Stop and Retry status performs only a read', () => {
    const harvest = sampleRecord('status.worker_unverified');
    object(harvest['operation'])['revision'] = null;
    const h = draw(status(harvest));
    expect(h.view.container.querySelector('.tp-chip[data-state="unknown"]')).toBeVisible();
    expect(h.view.getByRole('button', { name: /^Stop Gem collector/ })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    expect(h.view.getByText('Waiting for fresh status')).toBeVisible();
    fireEvent.click(h.view.getByRole('button', { name: 'Retry status' }));
    expect(h.commands.checkStatus).toHaveBeenCalledExactlyOnceWith();
    expect(h.commands.stop).not.toHaveBeenCalled();
  });

  it('F-013: an operation listed as a process is not repeated', () => {
    const harvest = sampleRecord('status.collecting');
    const op = object(harvest['operation']);
    const row = {
      pid: 43,
      created: 100,
      runner: 'gem_collector',
      operation_id: op['id'],
      operation_revision: op['revision'],
    };
    const h = draw(status(harvest, [row, { ...row, pid: 44 }]));
    expect(h.view.getAllByText('Gem collector')).toHaveLength(1);
    expect(h.view.getAllByRole('button', { name: /^Stop Gem collector/ })).toHaveLength(1);
  });

  it('F-013: different recorded operation ids retain independent chips and Stops', () => {
    const harvest = sampleRecord('status.collecting');
    harvest['owner'] = object(sampleRecord('status.owned_by_run_gathers')['owner']);
    const state = status(harvest);
    const h = draw(state);
    const targets = selectAutomationSlot(state).rows;
    expect(targets).toHaveLength(2);
    expect(h.view.container.querySelectorAll('[data-operation-key]')).toHaveLength(2);
    expect(h.view.container.querySelectorAll('.tp-chip')).toHaveLength(2);
    for (const name of ['Gem collector', 'Run']) {
      fireEvent.click(h.view.getByRole('button', { name: new RegExp(`^Stop ${name}`) }));
      fireEvent.click(within(h.view.getByRole('dialog')).getByRole('button', { name: 'Stop' }));
    }
    expect(h.commands.stop.mock.calls.map(([command]) => command)).toEqual(
      targets.map((row) => row.stop.command),
    );
  });

  it('F-014: listed run shows scheduled stop and sends its fenced ClearFlagCommand once', () => {
    const rows = processes('stop_flag_true_with_a_listed_run');
    const state = addCase(status(sample('status.off'), rows), 'runs', 'runs.ready');
    const h = draw(state);
    expect(h.view.getByText('Waiting')).toBeVisible();
    expect(h.view.getByText('Will stop after this run')).toBeVisible();
    fireEvent.click(h.view.getByRole('button', { name: 'Cancel scheduled stop' }));
    expect(h.commands.clearFlag).toHaveBeenCalledExactlyOnceWith(
      selectAutomationSlot(state).rows[0]?.clearFlag,
    );
    const listed = (rows as Record<string, unknown>[])[0];
    h.setState(status(sample('status.off'), [{ ...listed, stop_flag: false }]));
    expect(h.view.getByText('Will not stop after this run')).toBeVisible();
    expect(h.view.queryByRole('button', { name: 'Cancel scheduled stop' })).toBeNull();
    h.setState(status());
    expect(h.view.queryByText('Will not stop after this run')).toBeNull();
    expect(h.commands.clearFlag).toHaveBeenCalledTimes(1);
  });
});
