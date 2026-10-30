import { afterEach, describe, expect, it, vi } from 'vitest';
import { z } from 'zod';
import { readJson } from '../../../src/api/client.ts';

const schema = z.strictObject({ value: z.number() });

function stubFetch(outcome: { readonly response: Response } | { readonly error: Error }) {
  const fetchMock = vi.fn<typeof fetch>(() =>
    'response' in outcome ? Promise.resolve(outcome.response) : Promise.reject(outcome.error),
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const respondWith = (response: Response) => stubFetch({ response });
const failWith = (error: Error) => stubFetch({ error });

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('readJson', () => {
  it('GETs the path without caching and returns contract-checked data', async () => {
    const fetchMock = respondWith(Response.json({ value: 7 }));
    await expect(readJson('/api/status', schema)).resolves.toEqual({
      ok: true,
      data: { value: 7 },
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [path, init] = fetchMock.mock.calls[0] ?? [];
    expect(path).toBe('/api/status');
    expect(init).toMatchObject({ method: 'GET', cache: 'no-store', credentials: 'same-origin' });
  });

  it('reports an HTTP failure', async () => {
    respondWith(new Response('nope', { status: 503 }));
    await expect(readJson('/api/status', schema)).resolves.toEqual({
      ok: false,
      error: { kind: 'http', status: 503 },
    });
  });

  it('reports a body that breaks the contract instead of returning it', async () => {
    respondWith(Response.json({ value: 'seven' }));
    const result = await readJson('/api/status', schema);
    expect(result.ok).toBe(false);
    expect(!result.ok && result.error.kind).toBe('contract');
  });

  it('reports a body that is not JSON', async () => {
    respondWith(new Response('<html>', { status: 200 }));
    const result = await readJson('/api/status', schema);
    expect(!result.ok && result.error.kind).toBe('contract');
  });

  it('reports a network failure and an abort', async () => {
    failWith(new TypeError('Failed to fetch'));
    await expect(readJson('/api/status', schema)).resolves.toEqual({
      ok: false,
      error: { kind: 'network', detail: 'TypeError: Failed to fetch' },
    });
    const controller = new AbortController();
    controller.abort();
    failWith(new DOMException('aborted', 'AbortError'));
    await expect(readJson('/api/status', schema, { signal: controller.signal })).resolves.toEqual({
      ok: false,
      error: { kind: 'aborted' },
    });
  });
});
