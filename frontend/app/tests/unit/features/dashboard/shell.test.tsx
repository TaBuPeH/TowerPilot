import { act, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { dashboardBody } from '../../../support/dashboardFixture.ts';
import { sample, statusBody } from '../../../support/fixture.ts';
import { renderPage, type PageHarness } from './harness.tsx';

/**
 * The app shell's capability rows (spec 7.2; F-107 to F-112): reads keep
 * their cadences on the real poller, the one-second clock moves ages without
 * a request or an announcement, and faults are reported once.
 */
beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const gets = (page: PageHarness, path: string | RegExp) =>
  page.fake.requests.filter(
    (entry) =>
      entry.method === 'GET' &&
      (typeof path === 'string' ? entry.path === path : path.test(entry.path)),
  ).length;

const idle = () => statusBody(sample('status.off'));

describe('App shell capability rows', () => {
  it('F-107: a page that fails to draw shows "Couldn\'t load the Dashboard" with Try again (and its revocations); Try again draws it again', async () => {
    const faults = new Set(['page']);
    const page = await renderPage({ status: idle, faults });
    expect(screen.getByText("Couldn't load the Dashboard")).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Pause harvesting' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Turn input off' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
    expect(document.querySelectorAll('[role="alert"]')).toHaveLength(0);
    faults.delete('page');
    await page.user.click(screen.getByRole('button', { name: 'Try again' }));
    await page.advance(0);
    expect(screen.queryByText("Couldn't load the Dashboard")).toBeNull();
    expect(screen.getByRole('heading', { level: 2, name: 'Control' })).toBeInTheDocument();
    expect(page.fake.writes()).toEqual([]);
  });

  it('F-107: one diagnostic per distinct fault per page session, never retried, never a write; failures never toast', async () => {
    const page = await renderPage({ status: idle });
    act(() => {
      for (let n = 0; n < 3; n += 1) {
        window.dispatchEvent(
          new ErrorEvent('error', {
            message: 'boom',
            filename: 'x.js',
            lineno: 1,
            error: new Error('boom'),
          }),
        );
      }
    });
    await page.advance(0);
    const diagnostics = page.fake.requests.filter(
      (entry) => entry.method === 'POST' && entry.path === '/api/diagnostics/client',
    );
    expect(diagnostics).toHaveLength(1);
    expect(JSON.parse(diagnostics[0]?.body ?? '{}')).toMatchObject({
      kind: 'javascript_error',
      page: 'dashboard',
    });
    await page.advance(30_000);
    expect(
      page.fake.requests.filter((entry) => entry.path === '/api/diagnostics/client'),
    ).toHaveLength(1);
    expect(page.fake.writes()).toEqual([]);
    expect(document.querySelector('[data-toast]')).toBeNull();
  });

  it('F-108: status is read every 2.5 s while idle, every 10 s on a hidden tab with battle reads stopped, and once at once when the tab is visible again', async () => {
    const page = await renderPage({ status: idle });
    await page.advance(1_000);
    const before = gets(page, '/api/status');
    await page.advance(10_000);
    const idleReads = gets(page, '/api/status') - before;
    expect(idleReads).toBeGreaterThanOrEqual(3);
    expect(idleReads).toBeLessThanOrEqual(5);
    act(() => {
      page.visibility.set(true);
    });
    await page.advance(0);
    const hiddenStart = gets(page, '/api/status');
    const battlesStart = gets(page, '/api/battles/active');
    await page.advance(30_000);
    const hiddenReads = gets(page, '/api/status') - hiddenStart;
    expect(hiddenReads).toBeGreaterThanOrEqual(2);
    expect(hiddenReads).toBeLessThanOrEqual(4);
    expect(gets(page, '/api/battles/active')).toBe(battlesStart);
    const visibleStart = gets(page, '/api/status');
    act(() => {
      page.visibility.set(false);
    });
    await page.advance(0);
    expect(gets(page, '/api/status')).toBe(visibleStart + 1);
  });

  it('F-108: a failing status read backs off (1 s first, then doubling) and never sends a write', async () => {
    const page = await renderPage({
      before: (fake) => {
        fake.route('GET', '/api/status', () => ({ status: 502, body: 'Bad Gateway' }));
      },
    });
    const start = gets(page, '/api/status');
    await page.advance(10_000);
    const reads = gets(page, '/api/status') - start;
    // 1 s, 2 s, 4 s, 8 s ... : at most five reads in ten seconds, far fewer than the 500 ms cadence.
    expect(reads).toBeGreaterThanOrEqual(2);
    expect(reads).toBeLessThanOrEqual(5);
    expect(page.fake.writes()).toEqual([]);
  });

  it('F-109: the schedule and recent battles are read on entry, and not again while nothing makes them relevant', async () => {
    const page = await renderPage({ status: idle });
    // Entry: the first config answer establishes the context, which reads each once in it.
    const schedule = gets(page, /^\/api\/schedule\/status/u);
    const battles = gets(page, /^\/api\/battles\?limit=3/u);
    expect(schedule).toBeGreaterThanOrEqual(1);
    expect(battles).toBeGreaterThanOrEqual(1);
    await page.advance(60_000);
    expect(gets(page, /^\/api\/schedule\/status/u)).toBe(schedule);
    expect(gets(page, /^\/api\/battles\?limit=3/u)).toBe(battles);
  });

  it('F-110: config, accounts, runs and the wizard reads happen on entry, and again when the tab becomes visible', async () => {
    const page = await renderPage({ status: idle });
    const slow = [
      '/api/config',
      '/api/accounts',
      '/api/runs',
      '/api/wizard/tools',
      '/api/wizard/state',
    ];
    const entry = new Map(slow.map((path) => [path, gets(page, path)]));
    for (const path of slow) expect(entry.get(path), path).toBeGreaterThanOrEqual(1);
    await page.advance(60_000);
    for (const path of slow) expect(gets(page, path), path).toBe(entry.get(path));
    act(() => {
      page.visibility.set(true);
    });
    await page.advance(1_000);
    act(() => {
      page.visibility.set(false);
    });
    await page.advance(0);
    for (const path of slow) expect(gets(page, path), path).toBe((entry.get(path) ?? 0) + 1);
  });

  it('F-111: saved run previews are read once per run and context, never per poll, and again only on a picker change', async () => {
    const page = await renderPage({ status: idle });
    expect(gets(page, /^\/api\/run-controls/u)).toBe(1);
    await page.advance(60_000);
    expect(gets(page, /^\/api\/run-controls/u)).toBe(1);
  });

  it('F-112: the one-second clock advances ages from the last read with no request and no announcement', async () => {
    const page = await renderPage({
      status: idle,
      before: (fake) => {
        // Entry answers (before and after the context is established); every later read is held.
        let answered = 0;
        fake.route('GET', '/api/status', () => {
          if (answered < 2) {
            answered += 1;
            return { status: 200, body: idle() };
          }
          return 'hold';
        });
      },
    });
    const age = () => document.querySelector('[data-region="control"] time')?.textContent ?? '';
    const requests = page.fake.requests.length;
    await page.advance(1_000);
    const one = age();
    await page.advance(1_000);
    const two = age();
    expect(one).toMatch(/^last read \d+ s ago$/u);
    expect(two).not.toBe(one);
    // The held read is the only status request; the clock itself asked for nothing.
    expect(
      page.fake.requests.filter((entry) => entry.path === '/api/status').length,
    ).toBeLessThanOrEqual(2);
    expect(page.fake.requests.length - requests).toBeLessThanOrEqual(1);
    expect(screen.getByRole('status')).toBeEmptyDOMElement();
  });

  it('F-082: Retry on unavailable setup progress reads /api/calibrate/status once at once, and sends nothing', async () => {
    const page = await renderPage({
      status: idle,
      before: (fake) => {
        fake.route('GET', '/api/calibrate/status', () => ({ status: 502, body: 'Bad Gateway' }));
      },
    });
    const region = document.querySelector<HTMLElement>('[data-region="needsYou"]');
    if (region === null) throw new Error('no Needs you region');
    const retry = within(region).getByRole('button', { name: 'Retry' });
    const before = gets(page, '/api/calibrate/status');
    await page.user.click(retry);
    await page.advance(0);
    expect(gets(page, '/api/calibrate/status')).toBe(before + 1);
    expect(page.fake.writes()).toEqual([]);
  });

  it('F-107: an applied low-stakes result (Reconnect accepted) toasts once through the live region and stays at least 8 s; a failed one never toasts', async () => {
    const page = await renderPage({
      status: idle,
      before: (fake) => {
        fake.route('POST', '/api/wizard/reconnect', () => ({
          status: 200,
          body: dashboardBody('reconnect.accepted_device_state_device'),
        }));
      },
    });
    const reconnect = screen.getByRole('button', { name: 'Reconnect' });
    await page.user.click(reconnect);
    await page.advance(0);
    const toast = document.querySelector('[data-toast="reconnect"]');
    expect(toast).toHaveTextContent('Reconnect started');
    expect(screen.getByRole('status')).toHaveTextContent('Reconnect started');
    await page.advance(7_000);
    expect(document.querySelector('[data-toast="reconnect"]')).not.toBeNull();
    await page.advance(2_000);
    expect(document.querySelector('[data-toast="reconnect"]')).toBeNull();
    expect(
      page.fake.writes().filter((entry) => entry.path === '/api/wizard/reconnect'),
    ).toHaveLength(1);
  });

  it('F-107: a refused or failed Reconnect never toasts', async () => {
    const page = await renderPage({
      status: idle,
      before: (fake) => {
        fake.route('POST', '/api/wizard/reconnect', () => ({
          status: 200,
          body: dashboardBody('reconnect.answered_offline_after_the_retries'),
        }));
      },
    });
    await page.user.click(screen.getByRole('button', { name: 'Reconnect' }));
    await page.advance(0);
    expect(document.querySelector('[data-toast]')).toBeNull();
  });

  it('F-007: a Control word change is announced once through the one live region; repeated failed reads and age ticks never announce again', async () => {
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
    await page.advance(3_000);
    const region = screen.getByRole('status');
    expect(region).toBeEmptyDOMElement();
    const announced: string[] = [];
    const observer = new MutationObserver(() => {
      const text = region.textContent;
      if (text !== '') announced.push(text);
    });
    observer.observe(region, { childList: true, subtree: true, characterData: true });
    fail = true;
    await page.advance(3_000);
    await page.advance(60_000);
    observer.disconnect();
    expect(announced).toHaveLength(1);
    expect(announced[0]).toContain('Gem harvesting: Unknown');
    expect(announced[0]).toContain('Automation: Unknown');
    expect(document.querySelectorAll('[aria-live]')).toHaveLength(1);
  });
});
