import { screen, within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import { postBodies } from '../../../support/dataLayer.ts';
import { dashboardBody } from '../../../support/dashboardFixture.ts';
import { sample, statusBody } from '../../../support/fixture.ts';
import { renderPage } from './harness.tsx';

/**
 * S-091, S-002 (fallback path), F-036, X4 to X6 of 9.1: every part that
 * fails to draw keeps Pause harvesting and Turn input off, rendered and
 * enabled, never Start, Collect or Turn on; the fallback Pause sends the same
 * body as the slot's (S-002).
 */
const PARTS: readonly [fault: string, fallback: string][] = [
  ['slot:automation', 'automation'],
  ['slot:input', 'input'],
  ['slot:gem', 'gem'],
  ['outcomes', 'outcomes'],
  ['control', 'automation'],
  ['needsYou', 'needsYou'],
  ['thisBattle', 'thisBattle'],
  ['startNext', 'startNext'],
  ['recent', 'recent'],
  ['topBar', 'topBar'],
];

const collecting = () => statusBody(sample('status.collecting'));

function fallbackOf(part: string): HTMLElement {
  const found = document.querySelector<HTMLElement>(`[data-fallback="${part}"]`);
  if (found === null) throw new Error(`no fallback for ${part}`);
  return found;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('S-091 every fallback keeps Pause harvesting and Turn input off', () => {
  it.each(PARTS)(
    'S-091 a fault in %s draws its fallback with Pause and Turn input off, enabled, and no Start, Collect or Turn on',
    async (fault, part) => {
      await renderPage({ status: collecting, faults: [fault] });
      const fallback = within(fallbackOf(part));
      for (const name of ['Pause harvesting', 'Turn input off']) {
        const button = fallback.getByRole('button', { name });
        expect(button).toHaveAttribute('data-tier', 'halt');
        expect(button).not.toHaveAttribute('aria-disabled');
      }
      for (const name of ['Start playing', 'Collect gems only', 'Turn on', 'Allow game input']) {
        expect(fallback.queryByRole('button', { name })).toBeNull();
      }
      expect(textOutsideCatalogue(document.body)).toEqual([]);
    },
  );

  it('S-091 with every part failing at once, each keeps its own revocations and the page still works', async () => {
    const page = await renderPage({ status: collecting, faults: PARTS.map(([fault]) => fault) });
    for (const part of ['automation', 'needsYou', 'thisBattle', 'startNext', 'recent', 'topBar']) {
      expect(
        within(fallbackOf(part)).getByRole('button', { name: 'Pause harvesting' }),
      ).toBeInTheDocument();
    }
    expect(screen.queryByRole('button', { name: 'Start playing' })).toBeNull();
    await page.advance(10_000);
    expect(page.fake.writes()).toEqual([]);
  });

  it('S-091 has no axe violations with the gem slot failing', async () => {
    const page = await renderPage({ status: collecting, faults: ['slot:gem'] });
    vi.useRealTimers();
    expect(await axe(page.view.container)).toHaveNoViolations();
  });
});

describe('S-002 the fallback Pause sends exactly the Pause body', () => {
  it('S-002 a click on the gem fallback Pause posts {enabled:false, expected_context:{instance, serial}, idempotency_key} and nothing else', async () => {
    const page = await renderPage({ status: collecting, faults: ['slot:gem'] });
    page.fake.route('POST', '/api/harvest', () => 'hold');
    await page.user.click(
      within(fallbackOf('gem')).getByRole('button', { name: 'Pause harvesting' }),
    );
    await page.advance(0);
    const bodies = postBodies(page.fake, '/api/harvest');
    expect(bodies).toHaveLength(1);
    const body = bodies[0] as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual(['enabled', 'expected_context', 'idempotency_key']);
    expect(body['enabled']).toBe(false);
    expect(body['expected_context']).toEqual({ instance: 'main', serial: 'fake:1' });
    expect(typeof body['idempotency_key']).toBe('string');
  });
});

describe('F-036 the gem part that cannot draw keeps Pause', () => {
  it('F-036: a throw in the gem slot reads "Unknown - Harvesting may be on" with a working Pause and no Turn on, on a cold page', async () => {
    const page = await renderPage({
      status: () => statusBody(sample('status.off')),
      faults: ['slot:gem'],
    });
    const fallback = fallbackOf('gem');
    expect(fallback.querySelector('.tp-chip')).toHaveAttribute('data-state', 'unknown');
    expect(within(fallback).getByText('Harvesting may be on')).toBeInTheDocument();
    expect(within(fallback).queryByRole('button', { name: 'Turn on' })).toBeNull();
    page.fake.route('POST', '/api/harvest', () => 'hold');
    await page.user.click(within(fallback).getByRole('button', { name: 'Pause harvesting' }));
    await page.advance(0);
    expect(postBodies(page.fake, '/api/harvest')).toHaveLength(1);
  });

  it('F-036: a cold load whose first status is HTTP 502 still draws the gem fallback with Pause', async () => {
    const page = await renderPage({
      faults: ['slot:gem'],
      before: (fake) => {
        fake.route('GET', '/api/status', () => ({ status: 502, body: 'Bad Gateway' }));
      },
    });
    await page.advance(1_000);
    expect(
      within(fallbackOf('gem')).getByRole('button', { name: 'Pause harvesting' }),
    ).toBeInTheDocument();
  });

  it('F-036: a warm page whose gem part starts failing keeps a focused Pause across five polls', async () => {
    const faults = new Set<string>();
    const page = await renderPage({ status: collecting });
    page.view.unmount();
    const { App } = await import('../../../../src/app/App.tsx');
    const { render } = await import('@testing-library/react');
    render(<App data={page.data} faults={faults} />);
    await page.advance(0);
    expect(screen.getAllByRole('button', { name: 'Pause harvesting' })).toHaveLength(1);
    faults.add('slot:gem');
    await page.advance(3_000);
    const fallbackPause = within(fallbackOf('gem')).getByRole('button', {
      name: 'Pause harvesting',
    });
    fallbackPause.focus();
    for (let poll = 0; poll < 5; poll += 1) await page.advance(2_600);
    expect(fallbackPause).toHaveFocus();
    expect(fallbackPause.isConnected).toBe(true);
  });
});

describe('F4-01 the Automation fallback keeps incomplete Stop targets with their reason', () => {
  const CASES: readonly [name: string, status: () => unknown, reason: string, warm: boolean][] = [
    [
      'X9 malformed linked row, cold',
      () => statusBody(sample('status.off'), { processes: [{ pid: 9009, operation_id: 7 }] }),
      "Can't identify the operation",
      false,
    ],
    [
      'X9 malformed linked row, warm',
      () => statusBody(sample('status.off'), { processes: [{ pid: 9009, operation_id: 7 }] }),
      "Can't identify the operation",
      true,
    ],
    [
      'created null, cold',
      () => dashboardBody('status.processes_untracked_created_null'),
      "Can't prove it is the same process",
      false,
    ],
    [
      'created null, warm',
      () => dashboardBody('status.processes_untracked_created_null'),
      "Can't prove it is the same process",
      true,
    ],
  ];

  it.each(CASES)(
    'F4-01 %s: the fallback Stop is shown disabled, described by its visible reason, beside an enabled Turn input off; a click sends nothing',
    async (_name, status, reason, warm) => {
      const faults = new Set<string>(warm ? [] : ['slot:automation']);
      const page = await renderPage({ status, faults });
      if (warm) {
        faults.add('slot:automation');
        await page.advance(3_000);
      }
      const fallback = within(fallbackOf('automation'));
      const stop = fallback.getByRole('button', { name: /^Stop/ });
      expect(stop).toHaveAttribute('aria-disabled', 'true');
      expect(stop.getAttribute('aria-describedby')).not.toBeNull();
      expect(stop).toHaveAccessibleDescription(expect.stringMatching(new RegExp(`^${reason}`)));
      expect(fallback.getByText(reason)).toBeVisible();
      const inputOff = fallback.getByRole('button', { name: 'Turn input off' });
      expect(inputOff).not.toHaveAttribute('aria-disabled');
      await page.user.click(stop);
      await page.advance(0);
      expect(page.fake.writes()).toEqual([]);
      expect(document.querySelector('dialog[open]')).toBeNull();
      expect(textOutsideCatalogue(document.body)).toEqual([]);
    },
  );

  it('F4-01 control: a valid operation keeps its enabled Stop in the fallback, and its click asks before one exact cancel', async () => {
    const page = await renderPage({ status: collecting, faults: ['slot:automation'] });
    const stop = within(fallbackOf('automation')).getByRole('button', { name: /^Stop/ });
    expect(stop).not.toHaveAttribute('aria-disabled');
    await page.user.click(stop);
    await page.advance(0);
    expect(page.fake.writes()).toEqual([]);
    expect(document.querySelector('dialog[open]')).not.toBeNull();
  });
});
