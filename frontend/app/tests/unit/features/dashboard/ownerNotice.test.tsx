import { act, fireEvent, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Outcomes } from '../../../../src/features/dashboard/outcomes/Outcomes.tsx';
import { ControlRegion } from '../../../../src/features/dashboard/control/ControlRegion.tsx';
import * as outcomeSelectors from '../../../../src/store/selectors/outcomes.ts';
import { selectGemSlot } from '../../../../src/store/selectors/gemSlot.ts';
import { selectInputSlot } from '../../../../src/store/selectors/inputSlot.ts';
import { selectAutomationSlot } from '../../../../src/store/selectors/automationSlot.ts';
import { e0Config } from '../../../support/dataLayer.ts';
import { createMemoryStorage, type FakeTransport } from '../../../support/fakeTransport.ts';
import { sample, sampleNames, sampleRecord, statusBody } from '../../../support/fixture.ts';
import { ownPath } from '../../../../src/core/read.ts';
import { renderPage, renderRegion } from './harness.tsx';
import { answerState, audit } from './outcomes/helpers.tsx';

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function ownerRow(container: HTMLElement): HTMLElement {
  const row = container.querySelector<HTMLElement>('[data-owner-notice]');
  if (row === null) throw new Error('Owner notice required');
  return row;
}

describe('the shared owner notice', () => {
  it.each(sampleNames('refusals'))(
    'F-085: refusals.%s follows the body class and names the refused action',
    async (name) => {
      const h = answerState('start', `refusals.${name}`);
      const notice = outcomeSelectors.selectOutcomes(h.state()).ownerNotice;
      const { view, commands } = renderRegion(h.state(), <Outcomes />);
      if (notice === null) {
        expect(view.container.querySelector('[data-owner-notice]')).toBeNull();
        expect(view.queryByRole('button', { name: 'Pause harvesting' })).toBeNull();
        if (name === 'reserved_fields')
          expect(view.container.querySelector('.tp-notice')).toHaveAttribute(
            'data-state',
            'failed',
          );
      } else {
        const row = ownerRow(view.container);
        expect(row).toHaveTextContent("Couldn't Start playing:");
        if (notice.operation !== null)
          expect(within(row).getAllByText(String(notice.operation))[0]).toBeVisible();
        if (notice.starting) expect(row).toHaveTextContent('An operation is starting (accepted)');
        if (notice.noticeClass === 'lease_holder') {
          if (notice.holders.length === 0)
            expect(row).toHaveTextContent('An unidentified process holds the emulator');
          for (const holder of notice.holders) {
            expect(row).toHaveTextContent(String(holder.pid));
            if (holder.role !== null) expect(row).toHaveTextContent(String(holder.role));
          }
        }
        const why = within(row).getByText('Why');
        fireEvent.click(why);
        if (notice.error !== null)
          expect(within(row).getByText(String(notice.error))).toBeVisible();
      }
      for (const command of Object.values(commands)) expect(command).not.toHaveBeenCalled();
      await audit(view);
    },
  );

  it('S-072  unreadable owner offers only a plain retry; slot revocations keep their exact commands', async () => {
    const h = answerState('reconnect', 'refusals.device_owner_unreadable');
    h.read('config', e0Config(true));
    h.read('status', statusBody(sample('status.collecting')));
    const state = h.state();
    const notice = outcomeSelectors.selectOutcomes(state).ownerNotice;
    expect(notice).toMatchObject({
      noticeClass: 'owner_unreadable',
      operation: null,
      pid: null,
      holders: [],
      recovery: { kind: 'retry' },
      canRetry: false,
    });
    const gem = selectGemSlot(state),
      input = selectInputSlot(state),
      automation = selectAutomationSlot(state);
    expect(gem.pause).toMatchObject({ shown: true, enabled: true, command: null });
    expect(input.turnOff).toMatchObject({ shown: true, enabled: true, command: null });
    expect(automation.stops.length).toBeGreaterThan(0);
    const { view, commands } = renderRegion(state, <ControlRegion />);
    const row = ownerRow(view.container);
    expect(row.querySelector('.tp-notice')).toHaveAttribute('data-state', 'unknown');
    expect(row).toHaveTextContent("Who holds the emulator can't be read");
    expect(row.querySelector('[data-server-text="id"]')).toBeNull();
    expect(row).not.toHaveTextContent(/free|has stopped/i);
    expect(within(row).queryByRole('button', { name: /Pause|Stop/ })).toBeNull();
    const retry = within(row).getByRole('button', { name: 'Try again' });
    expect(retry).toBeEnabled();
    fireEvent.click(retry);
    // F6M-02: the retry reconnects the address the refused request named, in its context.
    expect(commands.reconnect).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ serial: 'fake:1' }),
    );
    expect(commands.resend).not.toHaveBeenCalled();
    const pause = view.getByRole('button', { name: 'Pause harvesting' });
    expect(pause).toBeVisible();
    expect(pause).toBeEnabled();
    fireEvent.click(pause);
    expect(commands.pause).toHaveBeenCalledExactlyOnceWith();
    const off = view.getByRole('button', { name: 'Turn input off' });
    expect(off).toBeVisible();
    expect(off).toBeEnabled();
    fireEvent.click(off);
    expect(commands.turnInputOff).toHaveBeenCalledExactlyOnceWith();
    for (const stop of automation.stops) {
      expect(stop).toMatchObject({ shown: true, enabled: true });
      expect(stop.command).toEqual({
        kind: 'cancel',
        operationId: ownPath(sample('status.collecting'), 'operation', 'id'),
        expectedRevision: ownPath(sample('status.collecting'), 'operation', 'revision'),
        epoch: state.epoch,
      });
      const button = view.container.querySelector<HTMLElement>(
        `[data-focus-key="stop-button:${stop.key}"]`,
      );
      expect(button).toBeDefined();
      expect(button).toBeVisible();
      expect(button).toBeEnabled();
      if (button === null) throw new Error('Stop button');
      fireEvent.click(button);
      fireEvent.click(within(view.getByRole('dialog')).getByRole('button', { name: 'Stop' }));
      expect(commands.stop).toHaveBeenLastCalledWith(stop.command);
    }
    await audit(view);
  });

  it.each([
    'device_owned_collector',
    'collector_running_manual',
    'device_owned_pre_spawn',
    'device_owned_process',
    'device_owned_lease',
  ])(
    'F-086: %s binds exactly the server recovery and never repeats the refused request',
    async (name) => {
      const h = answerState('start', `refusals.${name}`);
      const { view, commands } = renderRegion(h.state(), <Outcomes />);
      const notice = outcomeSelectors.selectOutcomes(h.state()).ownerNotice;
      if (notice === null) throw new Error('notice');
      const row = ownerRow(view.container);
      if (notice.recovery.kind === 'pause') {
        const pause = within(row).getByRole('button', { name: 'Pause harvesting' });
        expect(pause).toBeEnabled();
        fireEvent.click(pause);
        expect(commands.pause).toHaveBeenCalledExactlyOnceWith();
      } else if (notice.recovery.kind === 'stop') {
        const stop = within(row).getByRole('button', { name: /^Stop / });
        expect(stop).toBeEnabled();
        fireEvent.click(stop);
        fireEvent.click(within(view.getByRole('dialog')).getByRole('button', { name: 'Stop' }));
        expect(commands.stop).toHaveBeenCalledExactlyOnceWith(notice.recovery.command);
        expect(notice.recovery.command).toMatchObject({
          kind: 'cancel',
          operationId: ownPath(
            sampleRecord(`refusals.${name}`),
            'body',
            'recovery',
            'operation_id',
          ),
          expectedRevision: ownPath(
            sampleRecord(`refusals.${name}`),
            'body',
            'recovery',
            'expected_revision',
          ),
          epoch: 0,
        });
      } else expect(within(row).queryByRole('button', { name: /Pause|Stop/ })).toBeNull();
      if (notice.foreign) expect(row).toHaveTextContent('stop it there');
      expect(commands.start).not.toHaveBeenCalled();
      expect(commands.resend).not.toHaveBeenCalled();
      await audit(view);
    },
  );

  it('F-086: recovery Stop uses a newer known revision for that exact operation', async () => {
    const h = answerState('start', 'refusals.device_owned_pre_spawn');
    const n = outcomeSelectors.selectOutcomes(h.state()).ownerNotice;
    if (n?.recovery.kind !== 'stop' || n.recovery.command?.kind !== 'cancel')
      throw new Error('Stop');
    const expected = n.recovery.command;
    const body = statusBody(sample('status.collecting'));
    const harvest = body['harvest'] as Record<string, unknown>;
    const operation = harvest['operation'] as Record<string, unknown>;
    operation['id'] = expected.operationId;
    operation['revision'] = expected.expectedRevision + 20;
    operation['harvest_managed'] = false;
    h.read('status', body);
    const vm = outcomeSelectors.selectOutcomes(h.state()).ownerNotice;
    expect(vm?.recovery).toMatchObject({
      kind: 'stop',
      command: { ...expected, expectedRevision: expected.expectedRevision + 20 },
    });
    const { view, commands } = renderRegion(h.state(), <Outcomes />);
    fireEvent.click(within(ownerRow(view.container)).getByRole('button', { name: /^Stop / }));
    fireEvent.click(within(view.getByRole('dialog')).getByRole('button', { name: 'Stop' }));
    if (vm?.recovery.kind !== 'stop') throw new Error('Stop');
    expect(commands.stop).toHaveBeenCalledExactlyOnceWith(vm.recovery.command);
    await audit(view);
  });

  it.each(['off', 'owned_by_run_gathers', 'status_failed'])(
    'F-087: a later %s read proves free, names the replacement, or keeps Pause unresolved',
    async (name) => {
      const h = answerState('start', 'refusals.device_owned_collector');
      const harvest = sampleRecord(`status.${name}`);
      if (name === 'owned_by_run_gathers') {
        const off = sampleRecord('status.off');
        harvest['desired'] = off['desired'];
      }
      h.read('status', statusBody(harvest));
      const { view, commands } = renderRegion(h.state(), <Outcomes />);
      const row = ownerRow(view.container);
      const vm = outcomeSelectors.selectOutcomes(h.state()).ownerNotice;
      if (name === 'off') {
        expect(row).toHaveTextContent('The emulator is free; you can try again');
        expect(within(row).queryByRole('button', { name: /Pause|Stop/ })).toBeNull();
      }
      if (name === 'owned_by_run_gathers') {
        expect(row).toHaveTextContent('has stopped; the emulator is now used by');
        expect(vm?.currentOperation).not.toBeNull();
        expect(row).toHaveTextContent(String(vm?.currentOperation));
        const stop = within(row).getByRole('button', { name: /^Stop / });
        expect(stop).toBeEnabled();
        fireEvent.click(stop);
        fireEvent.click(within(view.getByRole('dialog')).getByRole('button', { name: 'Stop' }));
        if (vm?.recovery.kind !== 'stop') throw new Error('replacement Stop');
        expect(commands.stop).toHaveBeenCalledExactlyOnceWith(vm.recovery.command);
      }
      if (name === 'status_failed') {
        expect(row).not.toHaveTextContent('free');
        expect(within(row).getByRole('button', { name: 'Pause harvesting' })).toBeEnabled();
      }
      await audit(view);
    },
  );

  it('F-088: a recovery survives a shared-storage reload; Dismiss sends nothing and another context hides it', async () => {
    vi.useFakeTimers();
    const storage = createMemoryStorage();
    const options = {
      storage,
      status: () => statusBody(sample('status.status_failed')),
      before: (fake: FakeTransport) => {
        const a = sampleRecord('refusals.device_owned_collector');
        fake.route('POST', '/api/automation-access', () => ({
          status: Number(a['status']),
          body: a['body'],
        }));
      },
    };
    const first = await renderPage(options);
    await act(async () => {
      await first.data.commands.allowInput();
    });
    await first.advance();
    const notice = ownerRow(first.view.container);
    fireEvent.click(within(notice).getByRole('button', { name: 'Pause harvesting' }));
    await first.advance();
    first.fake.next('/api/harvest', 'POST').lose();
    await first.advance();
    expect(first.view.getByRole('button', { name: 'Send same request again' })).toBeEnabled();
    expect(first.fake.writes().filter((r) => r.path === '/api/harvest')).toHaveLength(1);
    first.view.unmount();
    first.data.stop();
    const second = await renderPage(options);
    expect(second.view.getByRole('button', { name: 'Send same request again' })).toBeEnabled();
    expect(second.fake.writes()).toHaveLength(0);
    fireEvent.click(
      within(
        second.view.container.querySelector<HTMLElement>('.tp-outcomes') ?? second.view.container,
      ).getByRole('button', { name: 'Dismiss' }),
    );
    await second.advance();
    expect(second.view.container.querySelector('[data-owner-notice]')).toBeNull();
    expect(second.fake.writes()).toHaveLength(0);
    await audit(second.view);
    second.view.unmount();
    second.data.stop();
    vi.useFakeTimers();
    const other = await renderPage({ storage, config: () => e0Config(false, 'other', 'fake:2') });
    expect(other.view.queryByRole('button', { name: 'Send same request again' })).toBeNull();
    expect(other.view.container.querySelector('[data-owner-notice]')).toBeNull();
    await audit(other.view);
    other.data.stop();
  });

  it('F-089: a throwing owner field keeps Pause, Turn input off and Dismiss without naming an owner', async () => {
    const h = answerState('start', 'refusals.device_owned_collector');
    const vm = outcomeSelectors.selectOutcomes(h.state());
    if (vm.ownerNotice === null) throw new Error('notice');
    const broken = {
      ...vm.ownerNotice,
      get operation(): never {
        throw new Error('owner field failed');
      },
    };
    vi.spyOn(outcomeSelectors, 'selectOutcomes').mockReturnValue({ ...vm, ownerNotice: broken });
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const { view, commands, reportFault } = renderRegion(h.state(), <Outcomes />);
    const row = ownerRow(view.container);
    expect(row).toHaveTextContent("Couldn't Start playing: who holds the emulator can't be shown");
    expect(row.querySelector('[data-server-text]')).toBeNull();
    const pause = within(row).getByRole('button', { name: 'Pause harvesting' });
    expect(pause).toBeEnabled();
    fireEvent.click(pause);
    expect(commands.pause).toHaveBeenCalledExactlyOnceWith();
    const off = within(row).getByRole('button', { name: 'Turn input off' });
    expect(off).toBeEnabled();
    fireEvent.click(off);
    expect(commands.turnInputOff).toHaveBeenCalledExactlyOnceWith();
    fireEvent.click(within(row).getByRole('button', { name: 'Dismiss' }));
    expect(commands.dismiss).toHaveBeenCalledExactlyOnceWith('result');
    expect(reportFault).toHaveBeenCalledTimes(1);
    await audit(view);
  });
});
