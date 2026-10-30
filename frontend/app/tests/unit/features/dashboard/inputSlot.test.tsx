import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { allowInputOffered, inputOffOffered, gameInputSeen } from '../../../../src/core/input.ts';
import { InputSlot } from '../../../../src/features/dashboard/control/InputSlot.tsx';
import { controlFacts } from '../../../../src/store/selectors/controlFacts.ts';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import { e0Config, postBodies } from '../../../support/dataLayer.ts';
import { dashboardBody } from '../../../support/dashboardFixture.ts';
import { sample, sampleRecord, statusBody } from '../../../support/fixture.ts';
import { answerOf, storeHarness, type StoreHarness } from '../../../support/storeHarness.ts';
import { factValue, renderPage, renderRegion } from './harness.tsx';

/**
 * Control / Game input as drawn (spec 3.6.3, INV-2; F-017, F-018, F-020,
 * F-022). The independent decision is core's (`inputOffOffered`,
 * `gameInputSeen`) on the same facts; the render must match it.
 */
function inputHarness(
  harvest: unknown,
  options: { readonly allowTaps?: unknown; readonly omitHarvest?: boolean } = {},
): StoreHarness {
  const h = storeHarness({ config: e0Config(options.allowTaps ?? false) });
  const body = statusBody(harvest);
  if (options.omitHarvest === true) delete body['harvest'];
  h.read('status', body);
  return h;
}

const turnOff = () => screen.queryByRole('button', { name: 'Turn input off' });
const allow = () => screen.queryByRole('button', { name: 'Allow game input' });

afterEach(() => {
  vi.useRealTimers();
});

describe('Game input capability rows', () => {
  it('F-017: the Permission fact reads Allowed, Off or Unknown from the config read', () => {
    for (const [allowTaps, word] of [
      [true, 'Allowed'],
      [false, 'Off'],
      [null, 'Unknown'],
      ['yes', 'Unknown'],
    ] as const) {
      const h = storeHarness({ config: e0Config(allowTaps) });
      h.read('status', statusBody(sample('status.off')));
      const { view } = renderRegion(h.state(), <InputSlot />);
      expect(factValue(view.container, 'Permission')).toHaveTextContent(word);
      expect(factValue(view.container, 'Observed')).toBeVisible();
      if (allowTaps === true) {
        const ids = turnOff()?.getAttribute('aria-describedby')?.split(/\s+/u) ?? [];
        expect(ids).toHaveLength(2);
        for (const id of ids) expect(document.getElementById(id)).toBeVisible();
        expect(turnOff()).toHaveAccessibleDescription(
          /Allowed Idle.*No automation is using game input/u,
        );
        expect(view.queryByText('Automation may tap this emulator')).toBeNull();
      }
      expect(textOutsideCatalogue(view.container)).toEqual([]);
      view.unmount();
    }
  });

  it('F-017: Allow game input appears only while the Setting reads Off and a fresh read shows no owner; one click calls it once', async () => {
    const free = renderRegion(inputHarness(sample('status.off')).state(), <InputSlot />);
    const button = allow();
    expect(button).not.toBeNull();
    expect(button).toHaveAttribute('data-tier', 'secondary');
    if (button === null) return;
    await userEvent.click(button);
    expect(free.commands.allowInput).toHaveBeenCalledTimes(1);
    free.view.unmount();
    for (const [name, allowTaps] of [
      ['collecting', false],
      ['worker_unverified', false],
      ['status_failed', false],
      ['off', true],
    ] as const) {
      const other = renderRegion(
        inputHarness(sample(`status.${name}`), { allowTaps }).state(),
        <InputSlot />,
      );
      expect(allow(), `${name} ${String(allowTaps)}`).toBeNull();
      other.view.unmount();
    }
  });

  it('F-018: each S-006 setup shows Turn input off (halt, enabled) with its reason and an Unknown chip, never Running', () => {
    for (const name of ['worker_unverified', 'status_failed']) {
      const h = inputHarness(sample(`status.${name}`));
      const facts = controlFacts(h.state()).input;
      const reason = inputOffOffered(facts, gameInputSeen(facts));
      expect(reason, name).not.toBeNull();
      const { view } = renderRegion(h.state(), <InputSlot />);
      const button = turnOff();
      expect(button, name).not.toBeNull();
      expect(button).toHaveAttribute('data-tier', 'halt');
      expect(button).not.toHaveAttribute('aria-disabled');
      const descriptionIds = button?.getAttribute('aria-describedby')?.split(/\s+/u) ?? [];
      expect(descriptionIds.length).toBeGreaterThan(0);
      for (const id of descriptionIds) {
        const described = document.getElementById(id);
        expect(described).toBeVisible();
        expect(described?.textContent).not.toBe('');
      }
      expect(view.container.querySelector('.tp-chip')).toHaveAttribute('data-state', 'unknown');
      expect(textOutsideCatalogue(view.container)).toEqual([]);
      view.unmount();
    }
  });

  it('F-018: the S-006 control cases (Off, or input_revoked, a fresh complete read, nothing listed) show no extra button', () => {
    const revoked = inputHarness(sample('status.input_revoked'));
    const revokedFacts = controlFacts(revoked.state()).input;
    expect(inputOffOffered(revokedFacts, gameInputSeen(revokedFacts))).toBeNull();
    const drawn = renderRegion(revoked.state(), <InputSlot />);
    expect(turnOff()).toBeNull();
    drawn.view.unmount();
    const h = inputHarness(sample('status.off'));
    const facts = controlFacts(h.state()).input;
    expect(inputOffOffered(facts, gameInputSeen(facts))).toBeNull();
    const { view } = renderRegion(h.state(), <InputSlot />);
    expect(turnOff()).toBeNull();
    expect(view.container.querySelector('.tp-chip')).toHaveAttribute('data-state', 'idle');
  });

  it('F-018: with no harvest status at all and taps off, the reason names the missing report and the chip reads Unknown, never Failed', () => {
    const { view } = renderRegion(
      inputHarness(sample('status.off'), { omitHarvest: true }).state(),
      <InputSlot />,
    );
    expect(turnOff()).not.toBeNull();
    expect(
      screen.getByText("This server doesn't report whether automation input is revoked"),
    ).toBeInTheDocument();
    expect(view.container.querySelector('.tp-chip')).toHaveAttribute('data-state', 'unknown');
  });

  it('F-018: one Turn input off click on the page sends exactly {enabled:false} to /api/automation-access, never queued', async () => {
    vi.useFakeTimers();
    const page = await renderPage({ status: () => statusBody(sample('status.worker_unverified')) });
    page.fake.route('POST', '/api/automation-access', () => 'hold');
    const button = await screen.findByRole('button', { name: 'Turn input off' });
    await page.user.click(button);
    await page.advance(0);
    expect(postBodies(page.fake, '/api/automation-access')).toEqual([{ enabled: false }]);
    expect(screen.getByRole('button', { name: 'Turn input off' })).not.toHaveAttribute(
      'aria-disabled',
    );
  });

  it('F-020: after a confirmed turn-off, a config read with allow_taps true makes the stopped line history at once', () => {
    const h = storeHarness({ config: e0Config(false) });
    h.intent({ id: 'off', kind: 'input_off' });
    h.answer('off', answerOf(sampleRecord('automation_access.disable_input_stopped')));
    const revoked = sampleRecord('status.off');
    revoked['input_revoked'] = true;
    h.read('status', statusBody(revoked));
    h.read('config', e0Config(false));
    const region = renderRegion(h.state(), <InputSlot />);
    expect(screen.getByText('Automation input stopped')).toBeInTheDocument();
    h.read('config', e0Config(true));
    region.setState(h.state());
    expect(screen.queryByText('Automation input stopped')).toBeNull();
    expect(factValue(region.view.container, 'Permission')).toHaveTextContent('Allowed');
    expect(factValue(region.view.container, 'Observed')).toBeVisible();
    expect(turnOff()).not.toBeNull();
  });

  it('F-020: with an earlier lost enable, the Off answer gives a warning and Turn input off stays', () => {
    const h = storeHarness({ config: e0Config(false) });
    h.intent({ id: 'enable', kind: 'input_on', phase: 'unconfirmed', lostReason: 'network' });
    h.intent({ id: 'off', kind: 'input_off' });
    h.answer('off', answerOf(sampleRecord('automation_access.disable_input_stopped')));
    const revoked = sampleRecord('status.off');
    revoked['input_revoked'] = true;
    h.read('status', statusBody(revoked));
    h.read('config', e0Config(false));
    renderRegion(h.state(), <InputSlot />);
    expect(
      screen.getByText('An earlier turn-on has no answer; taps may be allowed'),
    ).toBeInTheDocument();
    expect(turnOff()).not.toBeNull();
    expect(screen.queryByText('Automation input stopped')).toBeNull();
  });

  it('F-022: Turn input off answered input_stopped retires a held Turn on; its late enable_ok never shows "Turned on"', async () => {
    vi.useFakeTimers();
    const guard = (dashboardBody('status.guards_clear') as Record<string, unknown>)[
      'collection_guard'
    ];
    const page = await renderPage({
      // F-027: taps allowed and a connected emulator, so Turn on is offered.
      config: () => e0Config(true),
      status: () => ({
        ...statusBody(sample('status.off')),
        collection_guard: guard,
        device_state: 'device',
      }),
      before: (fake) => {
        fake.route('POST', '/api/harvest', () => 'hold');
        fake.route('POST', '/api/automation-access', () => 'hold');
      },
    });
    const turnOnButton = screen.queryByRole('button', { name: 'Turn on' });
    expect(turnOnButton).not.toHaveAttribute('aria-disabled');
    if (turnOnButton !== null && turnOnButton.getAttribute('aria-disabled') !== 'true') {
      await page.user.click(turnOnButton);
      await page.advance(0);
    }
    const off = screen.queryByRole('button', { name: 'Turn input off' });
    if (off !== null) {
      await page.user.click(off);
      await page.advance(0);
      page.fake
        .next('/api/automation-access', 'POST')
        .respond(200, sampleRecord('automation_access.disable_input_stopped')['body']);
      await page.advance(0);
    }
    const held = page.fake.held.find(
      (entry) => entry.request.path === '/api/harvest' && !entry.settled(),
    );
    held?.respond(200, sampleRecord('post_harvest.enable_ok')['body']);
    await page.advance(5_000);
    expect(document.body.textContent).not.toContain('Turned on');
    expect(postBodies(page.fake, '/api/harvest')).toHaveLength(1);
  });

  it.each([
    ['with', true],
    ['without', false],
  ] as const)(
    'F4-03 X10: %s a retained unidentified lease holder, Allow game input follows the EV-6 free proof; Turn input off stays',
    (_label, retained) => {
      const h = storeHarness({ config: e0Config(false) });
      h.read('status', statusBody(sample('status.off')));
      if (retained) {
        // The r6 device_owned_lease notice with holders [] (spec 9.1 X10): no read can resolve it.
        const notice = sampleRecord('refusals.device_owned_lease');
        const lease = (
          (notice['body'] as Record<string, unknown>)['owner'] as Record<string, unknown>
        )['lease'] as Record<string, unknown>;
        lease['holders'] = [];
        h.intent({ id: 'refused', kind: 'start' });
        h.answer('refused', answerOf(notice), 'refused');
      }
      h.read('status', statusBody(sample('status.starting_due')));
      const facts = controlFacts(h.state()).input;
      // The independent decision: core's own gate on the same facts.
      expect(allowInputOffered(facts)).toBe(!retained);
      expect(facts.leaseHolders).toHaveLength(retained ? 1 : 0);
      const { view } = renderRegion(h.state(), <InputSlot />);
      if (retained) expect(allow()).toBeNull();
      else expect(allow()).not.toBeNull();
      if (retained) {
        const off = turnOff();
        expect(off).not.toBeNull();
        expect(off).not.toHaveAttribute('aria-disabled');
        expect(off).toHaveAttribute('data-tier', 'halt');
      }
      expect(textOutsideCatalogue(view.container)).toEqual([]);
      view.unmount();
    },
  );
  it('has no axe violations', async () => {
    const { view } = renderRegion(
      inputHarness(sample('status.worker_unverified')).state(),
      <InputSlot />,
    );
    expect(await axe(view.container)).toHaveNoViolations();
  });
});
