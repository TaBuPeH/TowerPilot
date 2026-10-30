import { screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { sample, statusBody } from '../../../support/fixture.ts';
import { renderPage } from './harness.tsx';

/**
 * S-090, S-093: each part draws in isolation; a throw shows Unknown for that
 * part only, with one diagnostic per distinct fault (C-20: the one automatic
 * POST, never a person's write); a Start next fault cannot take down the
 * current activity parts.
 */
beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const collecting = () => statusBody(sample('status.collecting'));
const diagnostics = (page: Awaited<ReturnType<typeof renderPage>>) =>
  page.fake.requests.filter(
    (entry) => entry.method === 'POST' && entry.path === '/api/diagnostics/client',
  );

describe('S-090 each part draws in isolation', () => {
  it('S-090 a throw in the gem slot shows Unknown for that slot only; the other slots and regions draw normally', async () => {
    await renderPage({ status: collecting, faults: ['slot:gem'] });
    const gem = document.querySelector<HTMLElement>('[data-slot="gem"]');
    const input = document.querySelector<HTMLElement>('[data-slot="input"]');
    expect(gem?.querySelector('[data-fallback="gem"]')).not.toBeNull();
    expect(input?.querySelector('[data-fallback]')).toBeNull();
    expect(
      within(input as HTMLElement).getByRole('button', { name: 'Turn input off' }),
    ).toBeInTheDocument();
    for (const region of ['needsYou', 'thisBattle', 'startNext', 'recent']) {
      expect(document.querySelector(`[data-region="${region}"] [data-fallback]`)).toBeNull();
    }
  });

  it('S-090 one diagnostic per distinct fault, never repeated across polls, never counted as a write', async () => {
    const page = await renderPage({ status: collecting, faults: ['slot:gem', 'recent'] });
    await page.advance(30_000);
    const sent = diagnostics(page).map(
      (entry) => JSON.parse(entry.body ?? '{}') as Record<string, unknown>,
    );
    expect(sent).toHaveLength(2);
    expect(sent.map((body) => body['kind'])).toEqual(['javascript_error', 'javascript_error']);
    expect(
      sent
        .map((body) =>
          String(body['message']).startsWith('slot:gem')
            ? 'slot:gem'
            : String(body['message']).split(':')[0],
        )
        .sort(),
    ).toEqual(['recent', 'slot:gem']);
    expect(page.fake.writes()).toEqual([]);
  });
});

describe('S-093 a Start next fault cannot take down the current activity part', () => {
  it('S-093 with Start next failing, the Control slots and This battle keep their normal content and controls', async () => {
    await renderPage({ status: collecting, faults: ['startNext'] });
    expect(
      document.querySelector('[data-region="startNext"] [data-fallback="startNext"]'),
    ).not.toBeNull();
    expect(document.querySelector('[data-region="control"] [data-fallback]')).toBeNull();
    expect(document.querySelector('[data-region="thisBattle"] [data-fallback]')).toBeNull();
    const gem = document.querySelector<HTMLElement>('[data-slot="gem"]');
    expect(gem?.querySelector('.tp-chip')).toHaveAttribute('data-state', 'running');
    expect(
      within(gem as HTMLElement).getByRole('button', { name: 'Pause harvesting' }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Start playing' })).toBeNull();
  });
});
