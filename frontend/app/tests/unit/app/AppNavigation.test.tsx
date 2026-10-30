import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { afterEach, expect, it, vi } from 'vitest';
import { AppNavigation } from '../../../src/app/AppNavigation.tsx';
import { copy } from '../../../src/copy/catalogue.ts';
import { PAGE_IDS } from '../../../src/ui/routes.ts';
import { textOutsideCatalogue } from '../copy/renderedText.ts';

function viewport(wide: boolean) {
  vi.stubGlobal('matchMedia', () => ({
    matches: wide,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
}

afterEach(() => vi.unstubAllGlobals());

it('F-005: shows every destination once in a persistent desktop sidebar and marks the current page', async () => {
  viewport(true);
  const view = render(<AppNavigation currentPage="runs" />);
  const nav = screen.getByRole('navigation', { name: 'Navigation' });
  expect(screen.queryByRole('button', { name: 'Navigation' })).toBeNull();
  expect(
    within(nav)
      .getAllByRole('heading')
      .map((heading) => heading.textContent),
  ).toEqual(['Operate', 'Configure', 'System']);
  const links = within(nav).getAllByRole('link');
  expect(links).toHaveLength(PAGE_IDS.length);
  expect(links.map((link) => link.getAttribute('href')).sort()).toEqual(
    PAGE_IDS.map((id) => `#${id}`).sort(),
  );
  for (const page of copy.dashboard.topBar.appPages) {
    expect(within(nav).getByRole('link', { name: page.label.short })).toHaveAttribute(
      'href',
      `#${page.id}`,
    );
  }
  expect(within(nav).getByRole('link', { current: 'page' })).toHaveTextContent('Runs');
  expect(textOutsideCatalogue(view.container)).toEqual([]);
  expect(await axe(view.container)).toHaveNoViolations();
});

it('F-005: opens mobile navigation by keyboard, closes on Escape and returns focus to its trigger', async () => {
  viewport(false);
  const view = render(<AppNavigation currentPage="dashboard" />);
  const user = userEvent.setup();
  const trigger = screen.getByRole('button', { name: 'Navigation' });
  expect(screen.queryByRole('navigation')).toBeNull();
  await user.tab();
  expect(trigger).toHaveFocus();
  await user.keyboard('{Enter}');
  expect(trigger).toHaveAttribute('aria-expanded', 'true');
  await user.tab();
  expect(screen.getByRole('link', { name: 'Dashboard' })).toHaveFocus();
  await user.keyboard('{Escape}');
  expect(trigger).toHaveFocus();
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('navigation')).toBeNull();
  expect(await axe(view.container)).toHaveNoViolations();
});

it('closes mobile navigation when a destination is selected, including the current page', async () => {
  viewport(false);
  render(<AppNavigation currentPage="dashboard" />);
  const user = userEvent.setup();
  const trigger = screen.getByRole('button', { name: 'Navigation' });
  for (const name of ['Dashboard', 'Runs']) {
    await user.click(trigger);
    const link = screen.getByRole('link', { name });
    link.addEventListener('click', (event) => {
      event.preventDefault();
    });
    await user.click(link);
    expect(trigger).toHaveFocus();
    expect(screen.queryByRole('navigation')).toBeNull();
  }
});

it('closes on history/hash changes without moving focus from page controls', async () => {
  viewport(false);
  render(
    <>
      <AppNavigation currentPage="dashboard" />
      <button type="button">Page action</button>
    </>,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'Navigation' }));
  const action = screen.getByRole('button', { name: 'Page action' });
  await user.click(action);
  fireEvent(window, new HashChangeEvent('hashchange'));
  expect(action).toHaveFocus();
  expect(screen.queryByRole('navigation')).toBeNull();
});

it('moves focus out of the mobile panel before a history change closes it', async () => {
  viewport(false);
  render(<AppNavigation currentPage="dashboard" />);
  const user = userEvent.setup();
  const trigger = screen.getByRole('button', { name: 'Navigation' });
  await user.click(trigger);
  await user.tab();
  act(() => {
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
  expect(trigger).toHaveFocus();
  expect(screen.queryByRole('navigation')).toBeNull();
});

it('keeps keyboard focus visible when the desktop sidebar becomes the mobile disclosure', async () => {
  let wide = true;
  let notify = () => {};
  vi.stubGlobal('matchMedia', () => ({
    matches: wide,
    addEventListener: (_name: string, callback: () => void) => {
      notify = callback;
    },
    removeEventListener: vi.fn(),
  }));
  render(<AppNavigation currentPage="dashboard" />);
  const user = userEvent.setup();
  await user.tab();
  expect(screen.getByRole('link', { name: 'Dashboard' })).toHaveFocus();
  act(() => {
    wide = false;
    notify();
  });
  expect(screen.getByRole('button', { name: 'Navigation' })).toHaveFocus();
  expect(screen.queryByRole('navigation')).toBeNull();
});
