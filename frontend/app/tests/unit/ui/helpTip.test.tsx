import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { describe, expect, it, vi } from 'vitest';
import { copy } from '../../../src/copy/catalogue.ts';
import { helpTipName } from '../../../src/copy/helpTip.ts';
import { fromServer } from '../../../src/copy/server.ts';
import { HelpTip } from '../../../src/ui/HelpTip.tsx';
import { ServerValue } from '../../../src/ui/ServerValue.tsx';
import { textOutsideCatalogue } from '../copy/renderedText.ts';

// Catalogue text only: a subject for the icon, and a background for its tip.
const SUBJECT = copy.routes.dashboard.short;
const BACKGROUND = copy.routes.notFound.why ?? copy.routes.notFound.short;
const OTHER = copy.routes.runs.short;
const NAME = helpTipName(SUBJECT);

function icon(): HTMLElement {
  return screen.getByRole('button', { name: NAME });
}

function renderTip() {
  return render(
    <p>
      <span>{OTHER}</span>
      <HelpTip label={SUBJECT}>
        <span>{BACKGROUND}</span>
      </HelpTip>
    </p>,
  );
}

describe('HelpTip', () => {
  it('names server-labelled help from its visible subject without converting server text into copy', async () => {
    const user = userEvent.setup();
    const subject = fromServer('name', 'Default preset');
    if (subject === null) throw new Error('Missing test subject');
    const { container } = render(
      <p>
        <span id="setting-subject">
          <ServerValue kind="name" value={subject} />
        </span>
        <HelpTip label={SUBJECT} labelledBy="setting-subject">
          <span>{BACKGROUND}</span>
        </HelpTip>
      </p>,
    );
    const button = screen.getByRole('button', { name: 'About Default preset' });
    expect(button).not.toHaveAttribute('aria-label');
    expect(textOutsideCatalogue(container)).toEqual([]);
    expect(await axe(container)).toHaveNoViolations();
    await user.tab();
    expect(button).toHaveFocus();
    expect(button).toHaveAccessibleDescription(BACKGROUND);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('tooltip')).toBeNull();
    expect(button).toHaveFocus();
  });

  it('is one small icon button named for its subject, closed, with no tip drawn', () => {
    const { container } = renderTip();
    const button = icon();
    expect(button).toHaveAttribute('type', 'button');
    expect(button).toHaveAttribute('aria-expanded', 'false');
    expect(button).not.toHaveAttribute('aria-describedby');
    expect(button.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
    expect(button).not.toHaveAttribute('title');
    expect(screen.queryByRole('tooltip')).toBeNull();
    expect(screen.queryByText(BACKGROUND)).toBeNull();
    expect(container.querySelectorAll('button')).toHaveLength(1);
  });

  it('opens on hover with the exact background, described to the icon, and closes when left', async () => {
    const user = userEvent.setup();
    renderTip();
    await user.hover(icon());
    const tip = await screen.findByRole('tooltip');
    expect(tip).toHaveTextContent(BACKGROUND);
    expect(icon()).toHaveAttribute('aria-describedby', tip.id);
    expect(icon()).toHaveAttribute('aria-expanded', 'true');
    expect(icon()).toHaveAccessibleDescription(BACKGROUND);
    await user.unhover(icon());
    await waitFor(() => {
      expect(screen.queryByRole('tooltip')).toBeNull();
    });
    expect(icon()).not.toHaveAttribute('aria-describedby');
  });

  it('stays open while the pointer crosses from the icon onto the tip (hoverable)', async () => {
    const user = userEvent.setup();
    renderTip();
    await user.hover(icon());
    const tip = await screen.findByRole('tooltip');
    await user.unhover(icon());
    await user.hover(tip);
    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(screen.getByRole('tooltip')).toBe(tip);
    await user.unhover(tip);
    await waitFor(() => {
      expect(screen.queryByRole('tooltip')).toBeNull();
    });
  });

  it('opens on keyboard focus, closes on Escape without moving focus, and reopens on refocus', async () => {
    const user = userEvent.setup();
    renderTip();
    await user.tab();
    expect(icon()).toHaveFocus();
    expect(await screen.findByRole('tooltip')).toHaveTextContent(BACKGROUND);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('tooltip')).toBeNull();
    expect(icon()).toHaveFocus();
    expect(icon()).not.toHaveAttribute('aria-describedby');
    await user.tab();
    await user.tab({ shift: true });
    expect(icon()).toHaveFocus();
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
  });

  it('closes when focus leaves the icon for another control', async () => {
    const user = userEvent.setup();
    render(
      <>
        <HelpTip label={SUBJECT}>
          <span>{BACKGROUND}</span>
        </HelpTip>
        <button type="button">{OTHER}</button>
      </>,
    );
    await user.tab();
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    await user.tab();
    expect(screen.getByRole('button', { name: OTHER })).toHaveFocus();
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('a click pins the tip past the pointer leaving; a second click closes it', async () => {
    const user = userEvent.setup();
    renderTip();
    await user.click(icon());
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    await user.unhover(icon());
    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    await user.click(icon());
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('a press outside dismisses a pinned tip; a press on the tip does not', async () => {
    const user = userEvent.setup();
    render(
      <>
        <HelpTip label={SUBJECT}>
          <span>{BACKGROUND}</span>
        </HelpTip>
        <p>{OTHER}</p>
      </>,
    );
    await user.click(icon());
    await user.click(screen.getByRole('tooltip'));
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    await user.click(screen.getByText(OTHER));
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('a keyboard press on an icon opened by focus closes it rather than pinning', async () => {
    const user = userEvent.setup();
    renderTip();
    await user.tab();
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    await user.keyboard('{Enter}');
    expect(screen.queryByRole('tooltip')).toBeNull();
    await user.keyboard('{Enter}');
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
  });

  it('a touch tap opens it (no hover on touch) and a tap elsewhere closes it', () => {
    renderTip();
    fireEvent.pointerOver(icon(), { pointerType: 'touch' });
    fireEvent.pointerEnter(icon(), { pointerType: 'touch' });
    expect(screen.queryByRole('tooltip')).toBeNull();
    fireEvent.pointerDown(icon(), { pointerType: 'touch' });
    fireEvent.click(icon(), { detail: 1 });
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    fireEvent.pointerDown(document.body, { pointerType: 'touch' });
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('never submits a form or reaches a parent action', async () => {
    const user = userEvent.setup();
    const submit = vi.fn((event: { preventDefault: () => void }) => {
      event.preventDefault();
    });
    const parent = vi.fn();
    render(
      <form onSubmit={submit}>
        <div role="presentation" onClick={parent}>
          <HelpTip label={SUBJECT}>
            <span>{BACKGROUND}</span>
          </HelpTip>
        </div>
      </form>,
    );
    await user.click(icon());
    fireEvent.click(screen.getByRole('tooltip'));
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    await user.keyboard('{Enter}');
    expect(screen.queryByRole('tooltip')).toBeNull();
    await user.keyboard('{Enter}');
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    expect(submit).not.toHaveBeenCalled();
    expect(parent).not.toHaveBeenCalled();
  });

  it('draws the tip in the page body, outside the icon parent, so no ancestor clips it', async () => {
    const user = userEvent.setup();
    const { container } = renderTip();
    await user.click(icon());
    const tip = screen.getByRole('tooltip');
    expect(container.contains(tip)).toBe(false);
    expect(tip.parentElement).toBe(document.body);
  });

  it('draws inside an open dialog when the icon sits in one (top layer)', async () => {
    const user = userEvent.setup();
    render(
      <dialog open>
        <HelpTip label={SUBJECT}>
          <span>{BACKGROUND}</span>
        </HelpTip>
      </dialog>,
    );
    await user.click(icon());
    expect(screen.getByRole('tooltip').parentElement).toBe(document.querySelector('dialog'));
  });

  it('removes its tip with the icon', async () => {
    const user = userEvent.setup();
    const { unmount } = renderTip();
    await user.click(icon());
    unmount();
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('shows only catalogue text and has no accessibility violations, open or closed', async () => {
    const user = userEvent.setup();
    const { container } = renderTip();
    expect(textOutsideCatalogue(container)).toEqual([]);
    expect(await axe(container)).toHaveNoViolations();
    await user.click(icon());
    expect(textOutsideCatalogue(document.body)).toEqual([]);
    // The page's landmarks are the app shell's; this fragment has none to be contained by.
    expect(
      await axe(document.body, { rules: { region: { enabled: false } } }),
    ).toHaveNoViolations();
  });
});

describe('HelpTip placement', () => {
  /** An icon at a box in a viewport of the given size, and a tip of the given size. */
  async function openAt(
    box: { left: number; top: number },
    tip: { width: number; height: number },
    view: { width: number; height: number },
  ) {
    const user = userEvent.setup();
    const innerHeight = window.innerHeight;
    vi.spyOn(document.documentElement, 'clientWidth', 'get').mockReturnValue(view.width);
    window.innerHeight = view.height;
    vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(tip.width);
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(tip.height);
    renderTip();
    const button = icon();
    vi.spyOn(button, 'getBoundingClientRect').mockReturnValue(
      new DOMRect(box.left, box.top, 24, 24),
    );
    try {
      await user.click(button);
      const element = screen.getByRole('tooltip');
      return {
        left: Number.parseFloat(element.style.left),
        top: Number.parseFloat(element.style.top),
      };
    } finally {
      window.innerHeight = innerHeight;
    }
  }

  it('centres under the icon when there is room', async () => {
    const at = await openAt(
      { left: 400, top: 100 },
      { width: 200, height: 80 },
      { width: 1000, height: 800 },
    );
    expect(at).toEqual({ left: 312, top: 128 });
  });

  it('keeps the tip inside the left and right viewport edges', async () => {
    const left = await openAt(
      { left: 0, top: 100 },
      { width: 300, height: 80 },
      { width: 390, height: 800 },
    );
    expect(left.left).toBe(8);
    cleanup();
    const right = await openAt(
      { left: 366, top: 100 },
      { width: 300, height: 80 },
      { width: 390, height: 800 },
    );
    expect(right.left).toBe(390 - 300 - 8);
  });

  it('opens above the icon near the bottom edge, and stays in the viewport when neither side fits', async () => {
    const above = await openAt(
      { left: 100, top: 700 },
      { width: 200, height: 120 },
      { width: 1000, height: 760 },
    );
    expect(above.top).toBe(700 - 4 - 120);
    cleanup();
    const tall = await openAt(
      { left: 100, top: 100 },
      { width: 200, height: 780 },
      { width: 1000, height: 800 },
    );
    expect(tall.top).toBeGreaterThanOrEqual(8);
    expect(tall.top + 780).toBeLessThanOrEqual(800 - 8);
  });
});
