import { fireEvent, screen, within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { describe, expect, it } from 'vitest';
import { HeaderInputControl } from '../../../../../src/features/dashboard/topBar/HeaderInputControl.tsx';
import { e0Config } from '../../../../support/dataLayer.ts';
import { sample, sampleRecord, statusBody } from '../../../../support/fixture.ts';
import { answerOf, storeHarness } from '../../../../support/storeHarness.ts';
import { textOutsideCatalogue } from '../../../copy/renderedText.ts';
import { renderRegion } from '../harness.tsx';

function inputState(allowTaps: unknown, status: string) {
  const h = storeHarness({ config: e0Config(allowTaps) });
  h.read('status', statusBody(sample(`status.${status}`)));
  return h;
}

describe('non-dashboard input control', () => {
  it.each([
    [false, 'off', false],
    [true, 'off', true],
    [null, 'off', true],
    [false, 'worker_unverified', true],
    [false, 'status_failed', true],
    [false, 'collecting', true],
  ])('preserves selector offering for setting %s / status %s', (setting, status, offered) => {
    const { view } = renderRegion(inputState(setting, status).state(), <HeaderInputControl />);
    const control = screen.queryByRole('button', { name: 'Turn input off' });
    expect(control !== null).toBe(offered);
    expect(screen.queryByRole('button', { name: 'Allow game input' })).toBeNull();
    if (control !== null) {
      expect(control).not.toHaveAttribute('aria-disabled');
      expect(control).toHaveAttribute('data-focus-key', 'topbar:input:turn-off');
      expect(document.getElementById(control.getAttribute('aria-describedby') ?? '')).toBeVisible();
    }
    expect(textOutsideCatalogue(view.container)).toEqual([]);
  });

  it('keeps sending revocations enabled and calls exactly turnInputOff on each click', () => {
    const h = inputState(true, 'worker_unverified');
    h.intent({ id: 'off', kind: 'input_off' });
    const { commands } = renderRegion(h.state(), <HeaderInputControl />);
    const control = screen.getByRole('button', { name: 'Turn input off' });
    expect(control).toHaveAttribute('aria-busy', 'true');
    expect(control).not.toHaveAttribute('aria-disabled');
    expect(screen.getByText('Waiting for a reply')).toBeVisible();
    fireEvent.click(control);
    fireEvent.click(control);
    expect(commands.turnInputOff).toHaveBeenCalledTimes(2);
    expect(commands.turnInputOff).toHaveBeenNthCalledWith(1);
    expect(commands.turnInputOff).toHaveBeenNthCalledWith(2);
    expect(commands.allowInput).not.toHaveBeenCalled();
    expect(commands.resend).not.toHaveBeenCalled();
  });

  it('keeps a lost reply visible beside observed idle evidence', () => {
    const h = inputState(false, 'off');
    h.intent({ id: 'lost-off', kind: 'input_off', phase: 'unconfirmed', lostReason: 'network' });
    renderRegion(h.state(), <HeaderInputControl />);
    const group = within(screen.getByRole('group', { name: 'Game input' }));
    expect(group.getByText('No automation is using game input')).toBeVisible();
    expect(group.getByText('No reply — outcome unknown')).toBeVisible();
    expect(group.queryByRole('button', { name: 'Allow game input' })).toBeNull();
  });

  it('preserves earlier unresolved enables and offers revocation', () => {
    const h = inputState(false, 'off');
    h.intent({ id: 'lost-on', kind: 'input_on', phase: 'unconfirmed', lostReason: 'network' });
    renderRegion(h.state(), <HeaderInputControl />);
    expect(screen.getByText('An earlier turn-on has no answer; taps may be allowed')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Turn input off' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Allow game input' })).toBeNull();
  });

  it('shows failed revocation receipt and complete server error beside its still-enabled action', () => {
    const h = inputState(true, 'worker_unverified');
    h.intent({ id: 'failed-off', kind: 'input_off' });
    h.answer(
      'failed-off',
      answerOf(sampleRecord('automation_access.disable_marker_write_failed')),
      'unconfirmed',
    );
    const { view } = renderRegion(h.state(), <HeaderInputControl />);
    const group = within(screen.getByRole('group', { name: 'Game input' }));
    expect(group.getByText('Input is NOT revoked — turn off again')).toBeVisible();
    expect(view.container.querySelector('.tp-notice')).toHaveAttribute('data-state', 'failed');
    expect(view.container.querySelector('[data-server-text="message"]')).toBeVisible();
    expect(group.getByRole('button', { name: 'Turn input off' })).not.toHaveAttribute(
      'aria-disabled',
    );
  });

  it('keeps the focused button through reads and focuses its labelled group when it is no longer offered', () => {
    const h = inputState(true, 'off');
    const region = renderRegion(h.state(), <HeaderInputControl />);
    const control = screen.getByRole('button', { name: 'Turn input off' });
    control.focus();
    for (let i = 0; i < 3; i += 1) {
      h.read('status', statusBody(sample('status.off')));
      region.setState(h.state());
      expect(screen.getByRole('button', { name: 'Turn input off' })).toBe(control);
      expect(control).toHaveFocus();
    }
    h.read('config', e0Config(false));
    region.setState(h.state());
    expect(screen.queryByRole('button', { name: 'Turn input off' })).toBeNull();
    expect(screen.getByRole('group', { name: 'Game input' })).toHaveFocus();
  });

  it('returns focus to the group when a receipt settles while revocation remains offered', () => {
    const h = inputState(true, 'off');
    h.intent({
      id: 'earlier-enable',
      kind: 'input_on',
      phase: 'unconfirmed',
      lostReason: 'network',
    });
    const region = renderRegion(h.state(), <HeaderInputControl />);
    within(screen.getByRole('list', { name: 'Request results' }))
      .getByRole('button', { name: 'About this message' })
      .focus();
    const control = screen.getByRole('button', { name: 'Turn input off' });

    h.answer('earlier-enable', answerOf(sampleRecord('automation_access.enable_ok')));
    region.setState(h.state());

    expect(screen.queryByRole('list', { name: 'Request results' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Turn input off' })).toBe(control);
    expect(screen.getByRole('group', { name: 'Game input' })).toHaveFocus();
  });

  it('keeps confirmed input status compact and does not repeat settled receipts', () => {
    const h = inputState(true, 'off');
    h.intent({ id: 'off', kind: 'input_off' });
    h.answer('off', answerOf(sampleRecord('automation_access.disable_input_stopped')));
    const revoked = sampleRecord('status.off');
    revoked['input_revoked'] = true;
    h.read('status', statusBody(revoked));
    h.read('config', e0Config(false));
    renderRegion(h.state(), <HeaderInputControl />);
    expect(screen.getByText('Automation input stopped')).toBeVisible();
    expect(screen.queryByRole('list', { name: 'Request results' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Turn input off' })).toBeNull();
  });

  it('has no axe violations', async () => {
    const { view } = renderRegion(
      inputState(true, 'worker_unverified').state(),
      <HeaderInputControl />,
    );
    expect(await axe(view.container)).toHaveNoViolations();
  });
});
