import { fireEvent, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { sample, statusBody } from '../../../support/fixture.ts';
import { renderPage } from './harness.tsx';

/**
 * S-081 and S-082 with the in-app Runs page (F-048, Runs brief RUN-12): the
 * links to saved settings carry their run (`#runs/saved/<run>`) and the live
 * editor link opens `#runs/live`; following them is plain hash navigation, so
 * nothing waits, is deferred or held by the page (S-082). The Runs page opens
 * exactly the run named, or says it is not listed, never another run
 * (tests/unit/features/runs/runsPage.test.tsx RUN-12).
 */
beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});
describe('S-081 and S-082 edit links on the in-app Runs page', () => {
  it('S-081 RUN-12 every link to saved settings carries its exact run on the new Runs route', async () => {
    await renderPage({ status: () => statusBody(sample('status.off')) });
    const links = [...document.querySelectorAll<HTMLAnchorElement>('a[href]')];
    const saved = screen.getAllByRole('link', { name: 'Edit run' });
    expect(saved.length).toBeGreaterThan(0);
    for (const link of saved) expect(link.getAttribute('href')).toBe('#runs/saved/bp_farm');
    // No link to the previous Runs page claims a run identity it cannot open.
    for (const link of links.filter((l) => l.getAttribute('href')?.startsWith('/ui/') === true))
      expect(link.getAttribute('href')).not.toMatch(/runs.+/u);
  });

  it('S-082 no Edit waits: following "Edit run" is plain navigation, never deferred or held by the page', async () => {
    await renderPage({ status: () => statusBody(sample('status.off')) });
    for (const link of screen.getAllByRole('link', { name: 'Edit run' })) {
      // fireEvent returns false only when a handler prevented the navigation.
      expect(fireEvent.click(link)).toBe(true);
    }
  });
});
