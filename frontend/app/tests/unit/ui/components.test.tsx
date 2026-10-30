import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { describe, expect, it, vi } from 'vitest';
import { copy } from '../../../src/copy/catalogue.ts';
import { Button, type ButtonTier } from '../../../src/ui/Button.tsx';
import { Chip, type ChipState } from '../../../src/ui/Chip.tsx';
import { CopyLine } from '../../../src/ui/CopyLine.tsx';
import { Pill } from '../../../src/ui/Pill.tsx';

const STATES: ChipState[] = [
  'running',
  'waiting',
  'paused',
  'idle',
  'stopping',
  'unknown',
  'blocked',
  'failed',
];
const TIERS: ButtonTier[] = ['primary', 'secondary', 'halt', 'quiet'];

describe('Chip (spec 3.1)', () => {
  it.each(STATES)('names %s in text, with its shape hidden from assistive tech', async (state) => {
    const { container } = render(<Chip state={state} />);
    expect(screen.getByText(copy.states[state].short)).toBeInTheDocument();
    expect(container.querySelector('.tp-chip')).toHaveAttribute('data-state', state);
    expect(container.querySelector('.tp-chip__shape')).toHaveAttribute('aria-hidden', 'true');
    expect(await axe(container)).toHaveNoViolations();
  });

  it('gives each of the eight words its own shape', () => {
    const shapes = STATES.map((state) => {
      const { container } = render(<Chip state={state} />);
      return container.querySelector('.tp-chip__shape')?.innerHTML ?? '';
    });
    expect(new Set(shapes).size).toBe(STATES.length);
  });
});

describe('Pill (spec 3.2)', () => {
  it('is a square setting mark with its prefix, never a chip', () => {
    const { container } = render(
      <Pill prefix={copy.states.idle.short} value={copy.states.paused.short} />,
    );
    expect(container.querySelector('.tp-pill')).not.toBeNull();
    expect(container.querySelector('.tp-chip')).toBeNull();
  });
});

describe('Button', () => {
  it.each(TIERS)('%s tier is a named button that never submits a form', async (tier) => {
    const onClick = vi.fn();
    const { container } = render(
      <Button tier={tier} label={copy.dashboard.openCurrent.short} onClick={onClick} />,
    );
    const button = screen.getByRole('button', { name: 'Open the current Dashboard' });
    expect(button).toHaveAttribute('type', 'button');
    expect(button).toHaveAttribute('data-tier', tier);
    await userEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(await axe(container)).toHaveNoViolations();
  });

  it('does not act while disabled, stays focusable and names its visible reason', async () => {
    const onClick = vi.fn();
    render(
      <>
        <Button
          tier="primary"
          label={copy.routes.backToDashboard.short}
          onClick={onClick}
          disabled
          reasonId="why-off"
        />
        <span id="why-off">{copy.routes.dashboard.short}</span>
      </>,
    );
    const button = screen.getByRole('button', { name: 'Open the Dashboard' });
    await userEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
    expect(button).toHaveAttribute('aria-disabled', 'true');
    expect(button).not.toBeDisabled();
    expect(button).toHaveAccessibleDescription('Dashboard');
  });
});

describe('CopyLine', () => {
  it('keeps the short line visible and opens its background from a help icon', async () => {
    const { container } = render(<CopyLine entry={copy.errors.regionFailed} />);
    expect(screen.getByText('This part of the page could not be drawn.')).toBeVisible();
    const help = screen.getByRole('button', {
      name: 'About This part of the page could not be drawn.',
    });
    expect(screen.queryByRole('tooltip')).toBeNull();
    expect(await axe(container)).toHaveNoViolations();
    await userEvent.click(help);
    expect(screen.getByRole('tooltip')).toHaveTextContent(copy.errors.regionFailed.why ?? '');
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('has no help icon without background', () => {
    render(<CopyLine entry={copy.routes.dashboard} />);
    expect(screen.queryByRole('button')).toBeNull();
  });
});
