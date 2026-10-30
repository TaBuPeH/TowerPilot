import { act, fireEvent, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Outcomes } from '../../../../src/features/dashboard/outcomes/Outcomes.tsx';
import { ControlRegion } from '../../../../src/features/dashboard/control/ControlRegion.tsx';
import { copy } from '../../../../src/copy/catalogue.ts';
import { ownPath } from '../../../../src/core/read.ts';
import { selectOutcomes } from '../../../../src/store/selectors/outcomes.ts';
import { selectGemSlot } from '../../../../src/store/selectors/gemSlot.ts';
import { selectAutomationSlot } from '../../../../src/store/selectors/automationSlot.ts';
import { e0Config } from '../../../support/dataLayer.ts';
import { sample, sampleNames, sampleRecord, statusBody } from '../../../support/fixture.ts';
import { answerOf, storeHarness } from '../../../support/storeHarness.ts';
import { dashboardBody, dashboardCase } from '../../../support/dashboardFixture.ts';
import { reduceDashboard } from '../../../../src/store/dashboardStore.ts';
import { intent, regionState, tick } from '../../store/regionHelpers.ts';
import { renderPage, renderRegion } from './harness.tsx';
import { answerState, audit, shown } from './outcomes/helpers.tsx';

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const stopCases = [
  ...sampleNames('stop_operation.cancel').map((n) => `cancel.${n}`),
  ...sampleNames('stop_operation.conflicts').map((n) => `conflicts.${n}`),
];
const stopWording: Record<string, string> = {
  stopped: 'The answer reported this automation stopped',
  stopped_family: 'The answer reported this automation stopped',
  released: 'The automation ended; emulator ownership was released',
  stopping: 'Stop requested — waiting for the worker to end',
  ended: 'The automation had ended; this answer does not attribute its end',
  ended_after_termination: 'This Stop signalled processes; the automation has ended',
  active: 'The automation is still active',
  unverified_lease_held: 'Worker unverified — listed holders still hold the emulator',
  unverified_lease_held_after_stop: 'Processes signalled; another holder still holds the emulator',
  unverified_lease_unreadable: 'Worker unverified — emulator ownership cannot be read',
  unverified_lease_unreadable_after_stop: 'Processes signalled; emulator ownership cannot be read',
  unverified_lease_free: 'Worker unverified — the answer reported no emulator holder',
  operation_changed: 'Nothing was applied — the operation changed',
  operation_unknown: 'Nothing was applied — the operation is unknown',
  operation_required: 'Nothing was applied — an operation is required',
  not_cancellable: 'Nothing was applied — this operation cannot be cancelled',
  stop_outcome_partial: 'Stop partly applied — stopping is not confirmed',
};

describe('outcome receipts', () => {
  it('Run now outcomes belong to This battle and expose no second Control resend or focus target', async () => {
    const s = regionState();
    const battle = intent(s, { id: 'poke', kind: 'run_now', target: 'battle' });
    const { view, commands } = renderRegion({ ...s, intents: [battle] }, <Outcomes />);
    expect(view.container.querySelector('[data-outcome-id="poke"]')).toBeNull();
    expect(view.queryByRole('button', { name: 'Send same request again' })).toBeNull();
    expect(view.container.querySelector('.tp-outcomes__list')?.children).toHaveLength(0);
    for (const command of Object.values(commands)) expect(command).not.toHaveBeenCalled();
    await audit(view);
  });
  it('catalogue outcome wording avoids the section 6.4 internal terms', () => {
    const strings = (value: unknown): string[] =>
      typeof value === 'string'
        ? [value]
        : typeof value === 'object' && value !== null
          ? Object.values(value).flatMap(strings)
          : [];
    const values = [
      ...strings(copy.dashboard.outcomes),
      copy.dashboard.outcomes.failedStepName('lease_scan'),
    ];
    for (const value of values)
      expect(value).not.toMatch(
        /\b(driver|runner|lease|overlay|handoff|interrupted|diamond)\b|No battle running|Stop it(?! there)|Delivery unconfirmed|Live control|Failed to fetch|Open Runs/iu,
      );
  });
  it.each(['gone', 'stopped', 'pid_passed_to_another_runner', 'created_missing'])(
    'F-012: kill.%s never attributes a kill and never resends',
    async (name) => {
      const h = answerState('kill', `stop_operation.kill.${name}`);
      const { view, commands } = renderRegion(h.state(), <Outcomes />);
      const expected =
        name === 'gone' || name === 'stopped'
          ? 'The process is no longer running'
          : "Not stopped; couldn't prove it is the same process";
      expect(shown(view).getByText(expected)).toBeVisible();
      expect(view.container).not.toHaveTextContent(/killed/i);
      expect(shown(view).queryByRole('button', { name: 'Send same request again' })).toBeNull();
      expect(commands.resend).not.toHaveBeenCalled();
      await audit(view);
    },
  );

  it.each(['lost', '5xx'])(
    'F-014: a %s clear-flag answer may have been cancelled; a later flag read never attributes it',
    async (scenario) => {
      const h = storeHarness({ config: e0Config(false) });
      h.intent({ id: 'clear', kind: 'clear_flag', phase: 'unconfirmed', lostReason: 'network' });
      if (scenario === '5xx') {
        const a = dashboardCase(
          'control_unknown_outcomes.clear_flag_removed_then_the_answer_failed',
        );
        h.answer('clear', { status: a.status, body: a.body }, 'unconfirmed');
      }
      const { view, commands, setState } = renderRegion(h.state(), <ControlRegion />);
      expect(shown(view).getByText('May have been cancelled')).toBeVisible();
      expect(shown(view).getByText('Unknown')).toBeVisible();
      expect(shown(view).queryByRole('button', { name: 'Send same request again' })).toBeNull();
      const later = dashboardBody(
        'status.stop_flag_false_after_the_lost_clear_flag_answer',
      ) as Record<string, unknown>;
      const listed = dashboardBody('status.stop_flag_true_with_a_listed_run') as Record<
        string,
        unknown
      >;
      later['processes'] = (listed['processes'] as Record<string, unknown>[]).map((row) => ({
        ...row,
        stop_flag: false,
      }));
      h.read('status', later);
      setState(h.state());
      expect(view.getByText('Will not stop after this run')).toBeVisible();
      expect(shown(view).getByText('May have been cancelled')).toBeVisible();
      expect(shown(view).queryByText('Next-run flag cleared')).toBeNull();
      expect(commands.clearFlag).not.toHaveBeenCalled();
      expect(commands.resend).not.toHaveBeenCalled();
      fireEvent.click(shown(view).getByRole('button', { name: 'Dismiss' }));
      expect(commands.dismiss).toHaveBeenCalledExactlyOnceWith('clear');
      await audit(view);
    },
  );

  it('F-034: changing context removes Resend and explains that the request was not resent', async () => {
    const s = regionState();
    const state = { ...s, intents: [intent(s, { id: 'old', kind: 'pause' })] };
    const { view, setState, commands } = renderRegion(state, <Outcomes />);
    expect(view.getByRole('button', { name: 'Send same request again' })).toBeEnabled();
    const next = reduceDashboard(state, {
      type: 'context',
      epoch: 1,
      context: { instance: 'other', serial: 'fake:2', account: null, profile: 'mine' },
      confirmed: true,
      sessionContext: null,
    });
    setState(next);
    expect(view.queryByRole('button', { name: 'Send same request again' })).toBeNull();
    expect(view.getByText('Not resent in this context')).toBeVisible();
    expect(commands.resend).not.toHaveBeenCalled();
    await audit(view);
  });

  it('F-034: another request at the next revision settles Turn on as not applied', async () => {
    const h = storeHarness({ config: e0Config(false) });
    h.intent({
      id: 'on',
      kind: 'turn_on',
      phase: 'unconfirmed',
      lostReason: 'network',
      key: 'request-a',
      meta: { expectedRevision: 4 },
    });
    const { view, setState } = renderRegion(h.state(), <Outcomes />);
    expect(view.getByRole('button', { name: 'Send same request again' })).toBeEnabled();
    const harvest = sampleRecord('status.off');
    const desired = harvest['desired'] as Record<string, unknown>;
    desired['revision'] = 5;
    desired['request_key'] = 'request-b';
    h.read('status', statusBody(harvest));
    setState(h.state());
    expect(
      shown(view).getByText('Turn on was not applied; a different request changed the choice'),
    ).toBeVisible();
    expect(view.queryByRole('button', { name: 'Send same request again' })).toBeNull();
    expect(shown(view).queryByText('Unknown')).toBeNull();
    await audit(view);
  });

  it('F-015: two Stop-again controls name their own visible targets', async () => {
    const h = storeHarness({ config: e0Config(false) });
    const ids = ['00000001-0000-4000-8000-000000000000', '00000002-0000-4000-8000-000000000000'];
    for (const operationId of ids) {
      const a = sampleRecord('stop_operation.cancel.stopping');
      (ownPath(a, 'body', 'operation') as Record<string, unknown>)['id'] = operationId;
      h.intent({ id: operationId, kind: 'stop', meta: { operationId } });
      h.answer(operationId, answerOf(a));
    }
    const { view } = renderRegion(h.state(), <Outcomes />);
    for (const operationId of ids) {
      const button = view.getByRole('button', { name: `Stop again ${operationId}` });
      expect(button).toBeVisible();
      expect(button).toBeEnabled();
    }
    await audit(view);
  });

  it('F-015: a lost Stop is settled by the later read without inventing an answer', async () => {
    const h = storeHarness({ config: e0Config(false) });
    h.intent({
      id: 'stop',
      kind: 'stop',
      phase: 'unconfirmed',
      lostReason: 'network',
      meta: { operationId: '00000001-0000-4000-8000-000000000000' },
    });
    const { view, setState, commands } = renderRegion(h.state(), <Outcomes />);
    expect(shown(view).getByText('No reply — may or may not have stopped')).toBeVisible();
    h.read('status', statusBody(sample('status.off')));
    setState(h.state());
    expect(
      shown(view).getByText('A later fresh read confirmed this automation stopped'),
    ).toBeVisible();
    expect(shown(view).queryByText('The answer reported this automation stopped')).toBeNull();
    expect(commands.stop).not.toHaveBeenCalled();
    expect(commands.resend).not.toHaveBeenCalled();
    await audit(view);
  });
  it('F4-04 S-055: the partial Stop receipt states the operation state the reply returned, dated, and a later poll does not rewrite it', async () => {
    const h = answerState('stop', 'stop_operation.conflicts.stop_outcome_partial');
    h.clock(4000);
    const { view, setState } = renderRegion(h.state(), <Outcomes />);
    const line = view.container.querySelector('[data-reply-operation-state]');
    expect(line).toHaveAttribute('data-reply-operation-state', 'running');
    expect(line?.textContent).toMatch(/^At this reply \(\d+ s ago\): operation still running$/u);
    // A later read where the operation is gone changes the polled state, not this receipt line.
    h.read('status', statusBody(sample('status.off')));
    setState(h.state());
    expect(view.container.querySelector('[data-reply-operation-state]')?.textContent).toMatch(
      /^At this reply \(\d+ s ago\): operation still running$/u,
    );
    await audit(view);
  });

  it.each(stopCases)('F-015: %s keeps its own wording and per-request details', async (path) => {
    const h = answerState('stop', `stop_operation.${path}`);
    const { view, commands } = renderRegion(h.state(), <Outcomes />);
    const name = path.split('.').at(-1) ?? '';
    expect(stopWording[name], `expected wording for ${path}`).toBeDefined();
    expect(shown(view).getByText(stopWording[name] ?? '')).toBeVisible();
    const line = selectOutcomes(h.state()).lines[0];
    if (line?.detail.kind !== 'stop') throw new Error('Stop detail required');
    for (const pid of line.detail.effects?.signalled ?? [])
      expect(shown(view).getByText(String(pid))).toBeVisible();
    for (const holder of line.detail.holders)
      expect(shown(view).getByText(String(holder.pid))).toBeVisible();
    if (name === 'ended') expect(view.container).not.toHaveTextContent('this Stop stopped nothing');
    if (line.stopAgain !== null) {
      const again = view.getByRole('button', { name: /^Stop again / });
      expect(again).toBeEnabled();
      fireEvent.click(again);
      fireEvent.click(within(view.getByRole('dialog')).getByRole('button', { name: 'Stop again' }));
      expect(commands.stop).toHaveBeenCalledExactlyOnceWith(line.stopAgain);
    }
    if (line.detail.turnInputOff) {
      expect(view.getByRole('button', { name: 'Turn input off' })).toBeEnabled();
      fireEvent.click(view.getByRole('button', { name: 'Turn input off' }));
      expect(commands.turnInputOff).toHaveBeenCalledExactlyOnceWith();
    }
    expect(commands.resend).not.toHaveBeenCalled();
    await audit(view);
  });

  it.each([
    ['action_lock', 'Nothing was stopped; try again'],
    [null, 'No reply — may or may not have stopped'],
  ])('F-015: defensive Stop %s remains explicit', async (code, wording) => {
    const h = storeHarness();
    h.intent({ id: 's', kind: 'stop', meta: { operationId: 'A' } });
    h.answer(
      's',
      { status: code === null ? 500 : 409, body: code === null ? '<html>error</html>' : { code } },
      'unconfirmed',
    );
    const { view, commands } = renderRegion(h.state(), <Outcomes />);
    expect(shown(view).getByText(wording)).toBeVisible();
    expect(view.queryByRole('button', { name: /^Stop again / })).toBeNull();
    fireEvent.click(view.getByRole('button', { name: 'Check status' }));
    expect(commands.checkStatus).toHaveBeenCalledExactlyOnceWith();
    expect(commands.stop).not.toHaveBeenCalled();
    await audit(view);
  });

  it.each([
    'disable_input_stopped',
    'disable_driver_input_refused',
    'disable_nothing_running',
    'disable_not_enforced',
    'disable_marker_write_failed',
    'disable_lease_scan_failed',
  ])('F-019: %s renders its dated receipt and each failed step', async (name) => {
    const h = answerState('input_off', `automation_access.${name}`);
    const { view, commands } = renderRegion(h.state(), <Outcomes />);
    const line = selectOutcomes(h.state()).lines[0];
    if (line?.detail.kind !== 'input') throw new Error('Input detail required');
    expect(view.container.querySelector('.tp-notice__lead')).toHaveTextContent(
      'Game input — Turn input off, 0 s ago:',
    );
    expect(view.container.querySelector('.tp-notice')).not.toHaveAttribute('data-state', 'idle');
    if (['disable_input_stopped', 'disable_driver_input_refused'].includes(name))
      expect(shown(view).getByText('Turned off 0 s ago: automation input stopped')).toBeVisible();
    for (const step of line.detail.failedSteps)
      if (step.message !== null) expect(shown(view).getByText(String(step.message))).toBeVisible();
    for (const owner of line.detail.unfenced) {
      if (owner.operation !== null)
        expect(shown(view).getAllByText(String(owner.operation))[0]).toBeVisible();
      if (owner.recovery.kind === 'pause') {
        const pause = view.getByRole('button', { name: 'Pause harvesting' });
        expect(pause).toBeEnabled();
        fireEvent.click(pause);
        expect(commands.pause).toHaveBeenCalledExactlyOnceWith();
      }
    }
    await audit(view);
  });

  it.each([null, 'First server line.\nSecond server line.', 'x'.repeat(601)])(
    'S-043  marker failure preserves the complete message %s and never confirms',
    async (error) => {
      const h = storeHarness({ config: e0Config(false) });
      const answer = sampleRecord('automation_access.disable_marker_write_failed');
      const body = answer['body'] as Record<string, unknown>;
      if (error !== null) body['error'] = error;
      h.intent({ id: 'off', kind: 'input_off' });
      h.answer('off', answerOf(answer), 'unconfirmed');
      const { view, commands } = renderRegion(h.state(), <Outcomes />);
      const message = view.container.querySelector(
        '.tp-notice__server [data-server-text="message"]',
      );
      expect(message?.textContent).toBe(body['error']);
      expect(message).toBeVisible();
      expect(shown(view).getByText('Input is NOT revoked — turn off again')).toBeVisible();
      expect(view.container.querySelector('.tp-notice')).toHaveAttribute('data-state', 'failed');
      const retry = view.getByRole('button', { name: 'Turn off again' });
      expect(retry).toBeEnabled();
      fireEvent.click(retry);
      expect(commands.turnInputOff).toHaveBeenCalledExactlyOnceWith();
      expect(commands.resend).not.toHaveBeenCalled();
      await audit(view);
    },
  );

  it('F-019: an empty not-enforced answer names no owner', async () => {
    const a = sampleRecord('automation_access.disable_not_enforced');
    (ownPath(a, 'body', 'revocation') as Record<string, unknown>)['still_owned'] = [];
    const h = storeHarness();
    h.intent({ id: 'off', kind: 'input_off' });
    h.answer('off', answerOf(a));
    const { view } = renderRegion(h.state(), <Outcomes />);
    expect(
      shown(view).getByText('An unidentified holder of the emulator may still send input'),
    ).toBeVisible();
    expect(view.container.querySelector('[data-server-text="id"]')).toBeNull();
    await audit(view);
  });

  it('S-043  changing only body.error changes only the visible server-message node', async () => {
    const h = storeHarness({ config: e0Config(false) });
    const answer = sampleRecord('automation_access.disable_marker_write_failed');
    h.intent({ id: 'off', kind: 'input_off' });
    h.answer('off', answerOf(answer), 'unconfirmed');
    const { view, setState } = renderRegion(h.state(), <Outcomes />);
    const withoutMessage = () => {
      const strip = view.container.querySelector('.tp-outcomes')?.cloneNode(true);
      if (!(strip instanceof HTMLElement)) throw new Error('strip');
      const message = strip.querySelector(
        '.tp-notice__server > .tp-server [data-server-text="message"]',
      );
      if (message === null) throw new Error('message');
      message.textContent = '';
      return strip.innerHTML;
    };
    const original = withoutMessage();
    for (const error of ['First line\nSecond line', 'x'.repeat(601)]) {
      (answer['body'] as Record<string, unknown>)['error'] = error;
      h.answer('off', answerOf(answer), 'unconfirmed');
      setState(h.state());
      expect(
        view.container.querySelector('.tp-notice__server > .tp-server [data-server-text="message"]')
          ?.textContent,
      ).toBe(error);
      expect(withoutMessage()).toBe(original);
      await audit(view);
    }
  });

  it.each(['ok', 'fenced', 'latched', 'state', 'stopped', 'stopping'])(
    'F-019: an S-040 mutation of %s never gives the proving input-off line',
    async (field) => {
      const h = storeHarness({ config: e0Config(false) });
      const a = sampleRecord('automation_access.disable_input_stopped');
      const body = a['body'] as Record<string, unknown>,
        revocation = body['revocation'] as Record<string, unknown>;
      if (field === 'ok') body['ok'] = false;
      else if (field === 'fenced') revocation['fenced'] = false;
      else if (field === 'latched') revocation['latched'] = false;
      else if (field === 'state') revocation['state'] = 'unexpected';
      else if (field === 'stopped') revocation['stopped'] = [42];
      else revocation['stopping'] = [42];
      h.intent({ id: 'off', kind: 'input_off' });
      h.answer('off', answerOf(a), 'unconfirmed');
      const { view } = renderRegion(h.state(), <Outcomes />);
      expect(shown(view).queryByText('Turned off 0 s ago: automation input stopped')).toBeNull();
      expect(view.container.querySelector('.tp-notice')).toHaveAttribute('data-state', 'unknown');
      await audit(view);
    },
  );

  it('S-048  an earlier unresolved enable stays a warning, and a newer input fact makes the receipt history', async () => {
    const h = storeHarness({ config: e0Config(false) });
    h.intent({ id: 'on', kind: 'input_on', phase: 'unconfirmed', lostReason: 'network' });
    h.intent({ id: 'off', kind: 'input_off' });
    h.answer('off', answerOf(sampleRecord('automation_access.disable_input_stopped')));
    const { view, setState } = renderRegion(h.state(), <Outcomes />);
    expect(
      shown(view).getByText('Not fully confirmed — an earlier Allow game input has no answer'),
    ).toBeVisible();
    expect(view.container.querySelector('[data-outcome-id="off"] .tp-notice')).not.toHaveAttribute(
      'data-state',
      'idle',
    );
    h.read('config', e0Config(true));
    setState(h.state());
    expect(
      within(
        view.container.querySelector<HTMLElement>('[data-outcome-id="off"]') ?? view.container,
      ).getByText('Earlier result — newer facts are available'),
    ).toBeVisible();
    await audit(view);
  });

  it('F-021: stopping stays pending until a later complete Off read and becomes history after On', async () => {
    const h = answerState('input_off', 'automation_access.disable_stopping');
    const { view, setState } = renderRegion(h.state(), <Outcomes />);
    expect(shown(view).getByText('Stopping — waiting for the collector to end')).toBeVisible();
    await audit(view);
    const off = sampleRecord('status.off');
    off['input_revoked'] = true;
    h.read('status', statusBody(off));
    setState(h.state());
    expect(
      shown(view).getByText('A later fresh read confirmed the collector stopped'),
    ).toBeVisible();
    await audit(view);
    h.read('status', statusBody(sample('status.starting_due')));
    setState(h.state());
    expect(shown(view).getByText('Earlier result — newer facts are available')).toBeVisible();
    expect(
      shown(view).queryByText('A later fresh read confirmed the collector stopped'),
    ).toBeNull();
    await audit(view);
  });

  it('F-021: a later complete Off read confirms the named collector gone even when config is unreadable', async () => {
    const h = answerState('input_off', 'automation_access.disable_stopping');
    h.fail('config');
    const harvest = sampleRecord('status.off');
    harvest['input_revoked'] = true;
    h.read('status', statusBody(harvest));
    const { view } = renderRegion(h.state(), <Outcomes />);
    expect(
      shown(view).getByText('A later fresh read confirmed the collector stopped'),
    ).toBeVisible();
    await audit(view);
  });

  it.each(['off_ok_stopped', 'off_ok_stopping', 'off_ok_replayed'])(
    'F-035: %s preserves what Pause reported and becomes history at a newer revision',
    async (name) => {
      const h = answerState('pause', `post_harvest.${name}`);
      const { view, setState } = renderRegion(h.state(), <Outcomes />);
      const expected =
        name === 'off_ok_stopped'
          ? 'Pause stopped the collector'
          : name === 'off_ok_stopping'
            ? 'Pause accepted; the collector is still stopping'
            : 'Earlier Pause result replayed';
      expect(shown(view).getByText(expected)).toBeVisible();
      await audit(view);
      const next = sampleRecord('status.off');
      (next['desired'] as Record<string, unknown>)['revision'] =
        Number(ownPath(sampleRecord(`post_harvest.${name}`), 'body', 'desired', 'revision')) + 1;
      h.read('status', statusBody(next));
      setState(h.state());
      expect(shown(view).getByText('Earlier result — newer facts are available')).toBeVisible();
      await audit(view);
    },
  );

  it.each([
    { name: 'missing stop', stop: undefined },
    { name: 'missing desired', desiredMissing: true },
    { name: 'empty stop', stop: {} },
    { name: 'missing requested', stop: { stopped: [], stopping: [], errors: [] } },
    { name: 'non-id stopped', stop: { requested: true, stopped: [1], stopping: [], errors: [] } },
    {
      name: 'non-message error',
      stop: { requested: true, stopped: [], stopping: [], errors: [{}] },
    },
    { name: 'missing errors', stop: { requested: true, stopped: [], stopping: [] } },
    ...[null, {}, undefined, [7], [null]].map((stopping) => ({
      name: `stopping ${JSON.stringify(stopping)}`,
      stop: { requested: true, stopped: [], stopping, errors: [] },
    })),
  ])('F-035: malformed Pause %s keeps Pause and the exact Stop', async (mutation) => {
    const h = storeHarness({ config: e0Config(true) });
    h.read('status', statusBody(sample('status.collecting')));
    const a = sampleRecord('post_harvest.off_ok_stopped');
    const body = a['body'] as Record<string, unknown>;
    if ('desiredMissing' in mutation) delete body['desired'];
    else body['stop'] = mutation.stop;
    h.intent({ id: 'pause', kind: 'pause' });
    h.answer('pause', answerOf(a), 'unconfirmed');
    const { view, commands } = renderRegion(h.state(), <ControlRegion />);
    expect(shown(view).getByText("The answer didn't say what stopped")).toBeVisible();
    expect(selectGemSlot(h.state()).pause).toMatchObject({ shown: true, enabled: true });
    const pause = view.getByRole('button', { name: 'Pause harvesting' });
    expect(pause).toBeVisible();
    expect(pause).toBeEnabled();
    fireEvent.click(pause);
    expect(commands.pause).toHaveBeenCalledExactlyOnceWith();
    const stops = selectAutomationSlot(h.state()).stops;
    expect(stops.length).toBeGreaterThan(0);
    for (const stop of stops) {
      expect(stop).toMatchObject({ shown: true, enabled: true });
      expect(stop.command).toEqual({
        kind: 'cancel',
        epoch: 0,
        operationId: ownPath(sample('status.collecting'), 'operation', 'id'),
        expectedRevision: ownPath(sample('status.collecting'), 'operation', 'revision'),
      });
      const button = view.container.querySelector<HTMLElement>(
        `[data-focus-key="stop-button:${stop.key}"]`,
      );
      if (button === null) throw new Error('slot Stop');
      expect(button).toBeVisible();
      expect(button).toBeEnabled();
      fireEvent.click(button);
      fireEvent.click(within(view.getByRole('dialog')).getByRole('button', { name: 'Stop' }));
      expect(commands.stop).toHaveBeenLastCalledWith(stop.command);
    }
    await audit(view);
  });

  it.each(['pause', 'turn_on', 'handoff'] as const)(
    'F-034: %s resends only on a click, reconfirms handoff every time and dismisses locally',
    async (kind) => {
      const s = regionState();
      const state = { ...s, intents: [intent(s, { id: 'uncertain', kind })] };
      const { view, commands, setState } = renderRegion(state, <Outcomes />);
      expect(commands.resend).not.toHaveBeenCalled();
      for (let i = 0; i < 2; i++) {
        fireEvent.click(view.getByRole('button', { name: 'Send same request again' }));
        if (kind === 'handoff') {
          expect(commands.resend).toHaveBeenCalledTimes(i);
          fireEvent.click(
            within(view.getByRole('dialog')).getByRole('button', {
              name: 'Send same request again',
            }),
          );
          expect(commands.resend).toHaveBeenLastCalledWith('uncertain', true);
        } else expect(commands.resend).toHaveBeenLastCalledWith('uncertain');
      }
      const dismiss = view.getByRole('button', { name: 'Dismiss' });
      expect(dismiss).toHaveAccessibleDescription('Dismiss (does not cancel the request)');
      fireEvent.click(dismiss);
      expect(commands.dismiss).toHaveBeenCalledExactlyOnceWith('uncertain');
      setState({ ...state, intents: state.intents.map((x) => ({ ...x, dismissed: true })) });
      expect(view.queryByRole('button', { name: 'Send same request again' })).toBeNull();
      await audit(view);
    },
  );

  it('F-034: real resend sends byte-identical Pause body and Dismiss posts nothing', async () => {
    vi.useFakeTimers();
    const page = await renderPage({ status: () => statusBody(sample('status.status_failed')) });
    await act(async () => {
      await page.data.commands.pause();
    });
    await page.advance();
    const first = page.fake.next('/api/harvest', 'POST');
    first.lose();
    await page.advance();
    await page.advance(60000);
    expect(page.fake.writes()).toHaveLength(1);
    fireEvent.click(page.view.getByRole('button', { name: 'Send same request again' }));
    await page.advance();
    const writes = page.fake.writes().filter((r) => r.path === '/api/harvest');
    expect(writes).toHaveLength(2);
    expect(writes[1]?.body).toBe(writes[0]?.body);
    const before = page.fake.writes().length;
    fireEvent.click(page.view.getByRole('button', { name: 'Dismiss' }));
    await page.advance();
    expect(page.fake.writes()).toHaveLength(before);
    await audit(page.view);
    page.data.stop();
  });

  it.each(['start', 'start_gems'] as const)(
    'F-043: %s retains lost, HTML 5xx and busy outcomes but refuses other 4xx',
    async (kind) => {
      const h = storeHarness();
      h.intent({
        id: 'launch',
        kind,
        phase: 'unconfirmed',
        lostReason: 'network',
        meta: { preset: 'farm' },
      });
      const { view, setState, commands } = renderRegion(h.state(), <Outcomes />);
      for (const answer of [
        null,
        { status: 500, body: '<html>Internal server error</html>' },
        answerOf(sampleRecord('refusals.action_lock')),
      ]) {
        if (answer !== null) {
          h.answer('launch', answer, 'unconfirmed');
          setState(h.state());
        }
        expect(view.getByRole('button', { name: 'Send same request again' })).toBeEnabled();
        expect(view.container.querySelector('.tp-notice')).toHaveAttribute('data-state', 'unknown');
        expect(commands.start).not.toHaveBeenCalled();
        expect(commands.collect).not.toHaveBeenCalled();
        await audit(view);
      }
      h.answer('launch', answerOf(sampleRecord('refusals.reserved_fields')), 'refused');
      setState(h.state());
      expect(view.queryByRole('button', { name: 'Send same request again' })).toBeNull();
      await audit(view);
    },
  );

  it('F-043: a replayed accepted launch that failed shows its operation error, never Started', async () => {
    const h = storeHarness();
    h.intent({ id: 'launch', kind: 'start', meta: { preset: 'farm' } });
    h.answer('launch', {
      status: 200,
      body: {
        ok: true,
        operation_id: '00000001-0000-4000-8000-000000000000',
        operation: {
          id: '00000001-0000-4000-8000-000000000000',
          status: 'failed',
          revision: 5,
          error: { message: 'Launch could not spawn.\nSee server details.' },
        },
      },
    });
    const { view } = renderRegion(h.state(), <Outcomes />);
    expect(shown(view).getByText('The accepted launch failed')).toBeVisible();
    expect(view.container.querySelector('[data-server-text="message"]')?.textContent).toBe(
      'Launch could not spawn.\nSee server details.',
    );
    expect(view.container.querySelector('.tp-notice')).toHaveAttribute('data-state', 'failed');
    await audit(view);
  });

  it('dismiss moves focus to the next line, previous line, then Control heading; ticks never announce again', async () => {
    const s = regionState();
    let state = {
      ...s,
      intents: ['one', 'two', 'three'].map((id) => intent(s, { id, kind: 'pause' })),
    };
    const { view, commands, setState } = renderRegion(
      state,
      <>
        <h2 id="control-title" tabIndex={-1}>
          {copy.dashboard.shell.regions.control.short}
        </h2>
        <Outcomes />
      </>,
    );
    const live = view.getByRole('status');
    const original = live.firstChild;
    setState(tick(state, 5));
    expect(live.firstChild).toBe(original);
    for (const [id, expected] of [
      ['two', 'three'],
      ['three', 'one'],
      ['one', null],
    ]) {
      const row = view.container.querySelector(`[data-outcome-id="${id}"]`);
      if (row === null) throw new Error('row');
      const dismiss = within(row as HTMLElement).getByRole('button', { name: 'Dismiss' });
      dismiss.focus();
      fireEvent.click(dismiss);
      state = { ...state, intents: state.intents.filter((x) => x.id !== id) };
      setState(state);
      if (expected === null) expect(view.getByRole('heading', { name: 'Control' })).toHaveFocus();
      else
        expect(document.activeElement?.closest('[data-outcome-id]')).toHaveAttribute(
          'data-outcome-id',
          expected,
        );
    }
    expect(commands.dismiss).toHaveBeenCalledTimes(3);
    await audit(view);
  });

  it('a read removing the line focused by the previous dismissal moves focus to Control', async () => {
    const s = regionState();
    const records = ['one', 'two'].map((id) => intent(s, { id, kind: 'pause' }));
    const { view, setState } = renderRegion(
      { ...s, intents: records },
      <>
        <h2 id="control-title" tabIndex={-1}>
          {copy.dashboard.shell.regions.control.short}
        </h2>
        <Outcomes />
      </>,
    );
    const first = view.container.querySelector<HTMLElement>('[data-outcome-id="one"]');
    if (first === null) throw new Error('line');
    const button = within(first).getByRole('button', { name: 'Dismiss' });
    button.focus();
    fireEvent.click(button);
    setState({ ...s, intents: records.filter((record) => record.id === 'two') });
    expect(document.activeElement).toHaveTextContent('Check status');
    setState({ ...s, intents: [] });
    expect(view.getByRole('heading', { name: 'Control' })).toHaveFocus();
    await audit(view);
  });
});
