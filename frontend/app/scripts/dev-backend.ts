import type { Plugin, ProxyOptions } from 'vite';
import { createApiHandler, loadFixture } from '../tests/support/fake-backend/fake-backend.mjs';

/**
 * Where `npm run dev` sends /api requests.
 *
 * Default: the fixture-based fake backend, mounted in the dev server itself.
 * Opt-in: TOWER_PILOT_DEV_BACKEND=http://127.0.0.1:<port> proxies to a real
 * dashboard on this machine, and only GET and HEAD requests are forwarded;
 * every other method is refused before the proxy sees it.
 */
export const REAL_BACKEND_ENV = 'TOWER_PILOT_DEV_BACKEND';

const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '[::1]']);

/** The opt-in real backend URL, or null for the fake backend. Refuses anything off this machine. */
export function realBackendTarget(value: string | undefined): string | null {
  if (value === undefined || value.trim() === '') {
    return null;
  }
  const url = new URL(value);
  if (url.protocol !== 'http:' || !LOOPBACK_HOSTS.has(url.hostname)) {
    throw new Error(`${REAL_BACKEND_ENV} must be an http:// loopback URL, got ${value}`);
  }
  return url.origin;
}

export function devBackendProxy(target: string | null): Record<string, ProxyOptions> | undefined {
  return target === null ? undefined : { '/api': { target, changeOrigin: true } };
}

export function devBackend(target: string | null): Plugin {
  return {
    name: 'tower-pilot-dev-backend',
    apply: 'serve',
    configureServer(server) {
      if (target === null) {
        server.middlewares.use('/api', createApiHandler(loadFixture()));
        return;
      }
      server.middlewares.use('/api', (req, res, next) => {
        if (req.method === 'GET' || req.method === 'HEAD') {
          next();
          return;
        }
        res.statusCode = 405;
        res.setHeader('Allow', 'GET, HEAD');
        res.end(`${REAL_BACKEND_ENV} forwards GET and HEAD only.`);
      });
    },
  };
}
