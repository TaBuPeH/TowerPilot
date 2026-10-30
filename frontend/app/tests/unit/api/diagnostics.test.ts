import { describe, expect, it } from 'vitest';
import { createDiagnostics, MAX_REPORTS } from '../../../src/api/diagnostics.ts';
import { createFakeTransport } from '../../support/fakeTransport.ts';

/** C-20: the one automatic POST, once per distinct fault per page session, never retried, bounded. */
describe('client diagnostics (C-20, M-20)', () => {
  it('sends one POST per distinct fault and never retries a failed one', async () => {
    const fake = createFakeTransport();
    const diagnostics = createDiagnostics(fake.transport);
    const event = {
      kind: 'javascript_error',
      page: 'dashboard',
      message: 'x'.repeat(5000),
    } as const;
    expect(diagnostics.report('gem-slot', event)).toBe(true);
    expect(diagnostics.report('gem-slot', event)).toBe(false);
    fake.next('/api/diagnostics/client').lose();
    await Promise.resolve();
    expect(diagnostics.report('gem-slot', event)).toBe(false);
    const posts = fake.requests.filter((entry) => entry.method === 'POST');
    expect(posts.map((entry) => entry.path)).toEqual(['/api/diagnostics/client']);
    const body = JSON.parse(posts[0]?.body ?? '{}') as Record<string, unknown>;
    expect(body).toMatchObject({ kind: 'javascript_error', page: 'dashboard' });
    expect(String(body['message'])).toHaveLength(2000);
    expect(fake.writes()).toEqual([]);
  });

  it(`sends at most ${MAX_REPORTS.toString()} diagnostics in a page session`, () => {
    const fake = createFakeTransport();
    const diagnostics = createDiagnostics(fake.transport);
    for (let index = 0; index < MAX_REPORTS + 10; index += 1) {
      diagnostics.report(`fault-${index.toString()}`, {
        kind: 'api_error',
        page: 'dashboard',
        message: 'm',
      });
    }
    expect(diagnostics.sent()).toBe(MAX_REPORTS);
    expect(fake.requests).toHaveLength(MAX_REPORTS);
  });
});
