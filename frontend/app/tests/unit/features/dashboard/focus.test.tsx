import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { copy } from '../../../../src/copy/catalogue.ts';
import { Button } from '../../../../src/ui/Button.tsx';
import { ConfirmDialog } from '../../../../src/ui/ConfirmDialog.tsx';
import { dashboardBody } from '../../../support/dashboardFixture.ts';
import { sample, statusBody } from '../../../support/fixture.ts';
import { installDialogPolyfill, renderPage } from './harness.tsx';

/**
 * Spec 8.8 and 8.11 with S-083, S-084, S-094 and F-113: focus survives polls,
 * facts never freeze for focus, a replaced control hands focus to its
 * replacement by role, and a fallback is updated in place.
 */
beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('S-083 focus never freezes facts', () => {
  it('S-083 with focus on Pause harvesting, five status deliveries keep focus on the same element and update the facts', async () => {
    let name = 'status.collecting';
    const page = await renderPage({ status: () => statusBody(sample(name)) });
    const pause = screen.getByRole('button', { name: 'Pause harvesting' });
    pause.focus();
    name = 'status.starting_due';
    for (let poll = 0; poll < 5; poll += 1) await page.advance(600);
    expect(pause).toHaveFocus();
    expect(pause.isConnected).toBe(true);
    const gem = document.querySelector('[data-slot="gem"]');
    expect(gem?.querySelector('.tp-chip')).toHaveAttribute('data-state', 'waiting');
  });

  it('S-083 a failed status read while a control has focus updates the facts to last read, focus kept', async () => {
    let fail = false;
    const page = await renderPage({
      before: (fake) => {
        fake.route('GET', '/api/status', () =>
          fail
            ? { status: 502, body: 'Bad Gateway' }
            : { status: 200, body: statusBody(sample('status.collecting')) },
        );
      },
    });
    const turnOff = screen.getByRole('button', { name: 'Turn input off' });
    turnOff.focus();
    fail = true;
    for (let poll = 0; poll < 5; poll += 1) await page.advance(2_600);
    expect(turnOff).toHaveFocus();
    const gem = document.querySelector('[data-slot="gem"]');
    expect(gem?.querySelector('.tp-chip')).toHaveAttribute('data-state', 'unknown');
  });
});

describe('S-084 a replaced focused control hands focus to its replacement by role', () => {
  function Harness() {
    const [generation, setGeneration] = useState(0);
    const [open, setOpen] = useState(false);
    const label = copy.dashboard.shell.revocations.stop.short;
    return (
      <>
        {/* A look-alike with the same label and another role key, drawn first. */}
        <Button tier="halt" label={label} focusKey="stop:other" onClick={() => undefined} />
        <Button
          key={generation}
          tier="halt"
          label={label}
          focusKey="stop:operation:op-1"
          onClick={() => {
            setOpen(true);
          }}
        />
        <ConfirmDialog
          open={open}
          tier="halt"
          title={copy.dashboard.shell.confirmStop.title.short}
          body={copy.dashboard.shell.confirmStop.body.short}
          confirmLabel={copy.dashboard.shell.confirmStop.confirm.short}
          cancelLabel={copy.dashboard.shell.cancel.short}
          onConfirm={() => {
            setOpen(false);
          }}
          onCancel={() => {
            // A poll replaced the opener while the dialog was open.
            setGeneration((value) => value + 1);
            setOpen(false);
          }}
        />
      </>
    );
  }

  it('S-084 after the opener is replaced, Cancel returns focus to the element with the same focus key, never to the look-alike', async () => {
    vi.useRealTimers();
    installDialogPolyfill();
    render(<Harness />);
    const [, opener] = screen.getAllByRole('button', { name: 'Stop' });
    if (opener === undefined) throw new Error('no opener');
    await userEvent.click(opener);
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    const replacement = document.querySelector('[data-focus-key="stop:operation:op-1"]');
    expect(replacement).not.toBe(opener);
    expect(replacement).toHaveFocus();
    expect(document.querySelector('[data-focus-key="stop:other"]')).not.toHaveFocus();
  });
});

describe('S-094 a fallback is drawn once and updated in place', () => {
  it('S-094 with the automation slot failing on every poll, focus on its Turn input off survives five faulty polls', async () => {
    const page = await renderPage({
      status: () => statusBody(sample('status.collecting')),
      faults: ['slot:automation'],
    });
    const fallback = document.querySelector<HTMLElement>('[data-fallback="automation"]');
    if (fallback === null) throw new Error('no fallback');
    const turnOff = within(fallback).getByRole('button', { name: 'Turn input off' });
    turnOff.focus();
    for (let poll = 0; poll < 5; poll += 1) await page.advance(600);
    expect(turnOff).toHaveFocus();
    expect(document.querySelector('[data-fallback="automation"]')).toBe(fallback);
  });
});

describe('F-113 focus, selection and open disclosures survive later status', () => {
  it('F-113: with focus on a control, a help tip open and a run selected, five status deliveries keep all three while the facts update', async () => {
    let name = 'status.collecting';
    const runs = dashboardBody('runs.ready') as Record<string, Record<string, unknown>>;
    const farm = runs['runs']?.['bp_farm'];
    const page = await renderPage({
      status: () => statusBody(sample(name)),
      before: (fake) => {
        fake.route('GET', '/api/runs', () => ({
          status: 200,
          body: {
            ...runs,
            runs: { bp_farm: farm, bp_quest: { ...(farm as object), label: 'Quest' } },
          },
        }));
      },
    });
    const picker = screen.getByRole('combobox', { name: 'Run to start' });
    fireEvent.change(picker, { target: { value: 'bp_quest' } });
    await page.advance(0);
    expect(picker).toHaveValue('bp_quest');
    const input = document.querySelector<HTMLElement>('[data-slot="input"]');
    if (input === null) throw new Error('no input slot');
    const why = input.querySelector<HTMLElement>('.tp-help__trigger');
    if (why === null) throw new Error('no help icon');
    fireEvent.click(why);
    expect(why).toHaveAttribute('aria-expanded', 'true');
    // Pause stays offered in both states (choice On), so its focus can be kept.
    const gem = document.querySelector<HTMLElement>('[data-slot="gem"]');
    if (gem === null) throw new Error('no gem slot');
    const pause = within(gem).getByRole('button', { name: 'Pause harvesting' });
    pause.focus();
    const gemChip = () =>
      document.querySelector('[data-slot="gem"] .tp-chip')?.getAttribute('data-state');
    expect(gemChip()).toBe('running');
    name = 'status.starting_due';
    const before = page.fake.requests.filter((entry) => entry.path === '/api/status').length;
    // Idle cadence: one status read every 2.5 s (spec 7.2).
    for (let poll = 0; poll < 5; poll += 1) await page.advance(2_600);
    expect(
      page.fake.requests.filter((entry) => entry.path === '/api/status').length - before,
    ).toBeGreaterThanOrEqual(5);
    // The facts moved on; the person's focus, open help tip and selection did not.
    expect(gemChip()).toBe('waiting');
    expect(pause).toHaveFocus();
    expect(why.isConnected).toBe(true);
    expect(why).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Run to start' })).toHaveValue('bp_quest');
  });
});
