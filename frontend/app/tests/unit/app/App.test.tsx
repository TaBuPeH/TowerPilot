import { act, screen, within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderPage } from '../features/dashboard/harness.tsx';

function goTo(hash: string) {
  act(() => {
    window.location.hash = hash;
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  window.location.hash = '';
  vi.useRealTimers();
});

describe('App shell', () => {
  it('F-001: opens the Dashboard by default: tab title, product and page name from the catalogue', async () => {
    await renderPage();
    expect(screen.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument();
    expect(screen.getByText('Tower Pilot')).toBeInTheDocument();
    expect(document.title).toBe('Dashboard - Tower Pilot');
    for (const name of ['Control', 'Needs you', 'This battle', 'Start next', 'Plan and history']) {
      expect(screen.getByRole('heading', { level: 2, name })).toBeInTheDocument();
    }
  });

  it('F-007: has exactly one live region, silent on first load', async () => {
    await renderPage();
    expect(document.querySelectorAll('[aria-live]')).toHaveLength(1);
    expect(document.querySelectorAll('[role="alert"]')).toHaveLength(0);
    expect(screen.getByRole('status')).toBeEmptyDOMElement();
  });

  it('F-007: the first Tab stop is "Skip to content", which focuses main', async () => {
    const page = await renderPage();
    await page.user.tab();
    const skip = screen.getByRole('link', { name: 'Skip to content' });
    expect(skip).toHaveFocus();
    await page.user.click(skip);
    expect(screen.getByRole('main')).toHaveFocus();
    expect(window.location.hash).toBe('');
  });

  it('shows a not-found page for an unknown route and announces returning to the Dashboard', async () => {
    await renderPage();
    goTo('#nowhere');
    expect(screen.getByRole('heading', { level: 1, name: 'Page not found' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Page not found');
    goTo('#dashboard');
    expect(screen.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Dashboard');
  });

  it('has no axe violations', async () => {
    const page = await renderPage();
    vi.useRealTimers();
    expect(await axe(page.view.container)).toHaveNoViolations();
  });

  it.each(['page', 'topBar'])(
    'keeps navigation available when %s rendering fails',
    async (fault) => {
      const page = await renderPage({ faults: [fault] });
      await page.user.click(screen.getByRole('button', { name: 'Navigation' }));
      const nav = screen.getByRole('navigation', { name: 'Navigation' });
      expect(within(nav).getByRole('link', { name: 'Runtime' })).toHaveAttribute(
        'href',
        '#runtime',
      );
      expect(screen.getAllByRole('main')).toHaveLength(1);
      goTo('#nowhere');
      await page.advance(0);
      expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument();
      await page.user.click(screen.getByRole('button', { name: 'Navigation' }));
      expect(screen.getByRole('navigation', { name: 'Navigation' })).toBeVisible();
      expect(screen.getAllByRole('main')).toHaveLength(1);
      expect(page.fake.writes()).toEqual([]);
    },
  );
});
