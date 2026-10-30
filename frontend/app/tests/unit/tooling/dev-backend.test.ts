// @vitest-environment node
import type { IncomingMessage, ServerResponse } from 'node:http';
import { describe, expect, it, vi } from 'vitest';
import { devBackend, devBackendProxy, realBackendTarget } from '../../../scripts/dev-backend.ts';

type Middleware = (req: IncomingMessage, res: ServerResponse, next: () => void) => void;

/** Run the plugin's configureServer against a stand-in server and return what it mounted. */
function mounted(target: string | null): [string, Middleware][] {
  const used: [string, Middleware][] = [];
  const plugin = devBackend(target);
  const hook = plugin.configureServer;
  const handler = typeof hook === 'function' ? hook : hook?.handler;
  const server = { middlewares: { use: (path: string, fn: Middleware) => used.push([path, fn]) } };
  void (handler as unknown as (server: unknown) => unknown)(server);
  return used;
}

function call(middleware: Middleware, method: string) {
  const res = { statusCode: 200, setHeader: vi.fn(), end: vi.fn() };
  const next = vi.fn();
  middleware({ method, url: '/status' } as IncomingMessage, res as unknown as ServerResponse, next);
  return { res, next };
}

describe('dev backend', () => {
  it('uses the fake backend unless a real one is asked for', () => {
    expect(realBackendTarget(undefined)).toBeNull();
    expect(realBackendTarget('')).toBeNull();
    expect(devBackendProxy(null)).toBeUndefined();
    expect(mounted(null).map(([path]) => path)).toEqual(['/api']);
  });

  it('accepts only an http loopback URL for a real backend', () => {
    expect(realBackendTarget('http://127.0.0.1:8620/')).toBe('http://127.0.0.1:8620');
    expect(realBackendTarget('http://localhost:8620')).toBe('http://localhost:8620');
    expect(() => realBackendTarget('http://192.168.1.5:8620')).toThrow(/loopback/);
    expect(() => realBackendTarget('https://127.0.0.1:8620')).toThrow(/loopback/);
    expect(devBackendProxy('http://127.0.0.1:8620')).toEqual({
      '/api': { target: 'http://127.0.0.1:8620', changeOrigin: true },
    });
  });

  it.each(['POST', 'PUT', 'PATCH', 'DELETE'])(
    'refuses %s before the proxy to a real backend',
    (method) => {
      const [entry] = mounted('http://127.0.0.1:8620');
      expect(entry?.[0]).toBe('/api');
      const { res, next } = call(entry?.[1] ?? (() => undefined), method);
      expect(next).not.toHaveBeenCalled();
      expect(res.statusCode).toBe(405);
    },
  );

  it.each(['GET', 'HEAD'])('lets %s through to the proxy', (method) => {
    const [entry] = mounted('http://127.0.0.1:8620');
    const { next } = call(entry?.[1] ?? (() => undefined), method);
    expect(next).toHaveBeenCalledTimes(1);
  });
});
