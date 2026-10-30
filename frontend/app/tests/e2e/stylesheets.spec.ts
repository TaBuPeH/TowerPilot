import { createServer, type ServerResponse } from 'node:http';
import type { AddressInfo } from 'node:net';
import { expect, test, type Page, type Request } from '@playwright/test';
import { axeCounts, expectAppStyles } from './helpers/helpers.ts';

const CSS = '**/ui/app/assets/*.css';

/** Real HTTP responses and server-side hit counts, without Playwright network observers. */
async function serveStylesheets(
  stylesheets: Readonly<Record<string, (res: ServerResponse) => void>>,
) {
  const hits: Record<string, number> = {};
  const server = createServer((req, res) => {
    const url = req.url ?? '/';
    hits[url] = (hits[url] ?? 0) + 1;
    if (url === '/ui/app/') {
      res.writeHead(200, { 'Content-Type': 'text/html' });
      res.end(
        `<!doctype html><head>${Object.keys(stylesheets)
          .map((href) => `<link rel="stylesheet" href="${href}">`)
          .join('')}</head><body>stylesheet diagnostic</body>`,
      );
    } else if (stylesheets[url] !== undefined) {
      stylesheets[url](res);
    } else {
      res.writeHead(404);
      res.end();
    }
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  return {
    url: `http://127.0.0.1:${String((server.address() as AddressInfo).port)}/ui/app/`,
    hits,
    close: async () => {
      server.closeAllConnections();
      await new Promise<void>((resolve, reject) =>
        server.close((error) => {
          if (error) reject(error);
          else resolve();
        }),
      );
    },
  };
}

async function diagnostic(page: Page): Promise<string> {
  try {
    await expectAppStyles(page);
  } catch (error) {
    return String(error);
  }
  throw new Error('Expected stylesheet readiness to fail');
}

for (const truncated of [false, true]) {
  test(`no-listener diagnostics preserve ${truncated ? 'truncated' : 'complete'} CSS resource evidence without new requests`, async ({
    page,
  }, info) => {
    const css = 'body{margin:0}';
    const href = '/ui/app/assets/observed.css';
    const declaredBytes = truncated ? 1000 : Buffer.byteLength(css);
    const server = await serveStylesheets({
      [href]: (res) => {
        res.writeHead(200, {
          'Content-Type': 'text/css',
          'Content-Length': String(declaredBytes),
          Connection: 'close',
        });
        // Closing a response shorter than Content-Length is a real network failure.
        res.end(css);
      },
      '/empty.css': (res) => {
        res.writeHead(200, { 'Content-Type': 'text/css', 'Content-Length': '0' });
        res.end();
      },
    });
    try {
      // Deliberately no interception, waitForResponse or network event listeners.
      await page.goto(server.url);
      const before = { ...server.hits };
      const error = await diagnostic(page);
      await info.attach('no-listener-diagnostic', { body: error, contentType: 'text/plain' });
      expect(error).toContain('app stylesheet not loaded:');
      expect(error).toContain(`"content-length":"${String(declaredBytes)}"`);
      expect(error).toContain('request failure/completion events: not observed');
      expect(error).not.toContain('"failure":');
      expect(error).not.toContain('"responseEnd":-1');
      expect(error).not.toContain('"bodyBytes"');
      const state = JSON.parse(error.slice(error.indexOf('{'), error.indexOf('; retained'))) as {
        clocks: { timeOrigin: number; now: number; dateNow: number };
        sheets: { href: string; rules: number }[];
        resources: {
          name: string;
          decodedBodySize: number;
          responseStatus: number;
          responseEnd: number;
        }[];
      };
      const resource = state.resources.find((entry) => entry.name.endsWith(href));
      expect(resource?.decodedBodySize).toBe(Buffer.byteLength(css));
      expect(resource?.responseStatus).toBe(200);
      expect(resource?.responseEnd).toBeGreaterThan(0);
      expect(state.clocks.timeOrigin).toBeGreaterThan(0);
      expect(
        Math.abs(state.clocks.dateNow - (state.clocks.timeOrigin + state.clocks.now)),
      ).toBeLessThan(1000);
      if (truncated) {
        expect(resource?.decodedBodySize).toBeLessThan(declaredBytes);
        expect(state.sheets.find((sheet) => sheet.href.endsWith(href))?.rules).toBe(0);
      }
      // A browser round trip after diagnostics also gives any accidental loads a chance to arrive.
      await page.evaluate(() => document.readyState);
      expect(server.hits).toEqual(before);
      expect(server.hits[href]).toBe(1);
    } finally {
      await server.close();
    }
  });
}

test('incomplete response has metadata but no invented completion or body evidence', async ({
  page,
}, info) => {
  let release: (() => void) | undefined;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  const href = '/ui/app/assets/streaming.css';
  const server = await serveStylesheets({
    [href]: (res) => {
      res.writeHead(200, { 'Content-Type': 'text/css' });
      res.write('body{');
      void held.then(() => res.end('margin:0}'));
    },
  });
  try {
    await page.goto(server.url, { waitUntil: 'domcontentloaded' });
    const error = await diagnostic(page);
    await info.attach('incomplete-diagnostic', { body: error, contentType: 'text/plain' });
    expect(error).toContain('"status":200');
    expect(error).toContain('"resources":[]');
    expect(error).toContain('Missing entries do not prove no request or pending I/O');
    expect(error).not.toContain('"bodyBytes"');
    expect(error).not.toContain('"responseEnd":-1');
    release?.();
    await page.waitForLoadState('load');
    await expectAppStyles(page);
    expect(server.hits[href]).toBe(1);
  } finally {
    release?.();
    await server.close();
  }
});

test('unavailable request and resource evidence preserves the primary stylesheet failure', async ({
  page,
}, info) => {
  await page.route(CSS, (route) => route.fulfill({ contentType: 'text/css', body: '' }));
  await page.goto('/ui/app/');
  const retained = await page.requests();
  const css = retained.find((request) => /\/ui\/app\/assets\/[^/]+\.css$/u.test(request.url()));
  expect(css).toBeDefined();
  if (css === undefined) throw new Error('Expected retained CSS request');
  const unavailable = new Proxy(css, {
    get(target, property) {
      if (property === 'response') return () => Promise.reject(new Error('response unavailable'));
      const value: unknown = Reflect.get(target, property);
      return typeof value === 'function' ? (value.bind(target) as unknown) : value;
    },
  });
  const withRequests = (requests: () => Promise<Request[]>) =>
    new Proxy(page, {
      get(target, property) {
        if (property === 'requests') return requests;
        const value: unknown = Reflect.get(target, property);
        return typeof value === 'function' ? (value.bind(target) as unknown) : value;
      },
    });
  await page.evaluate(() => {
    performance.clearResourceTimings();
  });
  const error = await diagnostic(withRequests(() => Promise.resolve([unavailable, css])));
  expect(error).toContain('app stylesheet not loaded:');
  expect(error).toContain('response unavailable');
  expect(error).toContain('"status":200');
  expect(error).toContain('"resources":[]');
  expect(error).toContain('entries may be absent, evicted or cleared');
  const lookupError = await diagnostic(
    withRequests(() => Promise.reject(new Error('requests unavailable'))),
  );
  expect(lookupError).toContain('app stylesheet not loaded:');
  expect(lookupError).toContain('"lookupError":"Error: requests unavailable"');
  await info.attach('unavailable-diagnostic', {
    body: `${error}\n${lookupError}`,
    contentType: 'text/plain',
  });
});

test('stylesheet readiness accepts the real built CSS and its applied layout', async ({
  page,
}, testInfo) => {
  await page.goto('/ui/app/');
  await testInfo.attach('real-stylesheet-resources', {
    body: JSON.stringify({
      node: process.version,
      browser: page.context().browser()?.version(),
      ...(await page.evaluate(() => ({
        timeOrigin: performance.timeOrigin,
        resources: performance
          .getEntriesByType('resource')
          .map((entry) => entry.toJSON() as unknown),
      }))),
    }),
    contentType: 'application/json',
  });
  await expectAppStyles(page);
  await expect(page.locator('body')).toHaveCSS('margin', '0px');
});

for (const sample of [
  { name: 'empty', status: 200, contentType: 'text/css', body: '' },
  { name: 'missing', status: 404, contentType: 'text/css', body: '' },
  {
    name: 'wrong MIME',
    status: 200,
    contentType: 'text/html',
    body: '<!doctype html><title>Not CSS</title>',
  },
  {
    name: 'malformed',
    status: 200,
    contentType: 'text/css',
    body: 'not a stylesheet',
  },
]) {
  test(`stylesheet readiness rejects ${sample.name} CSS with transport evidence`, async ({
    page,
  }, testInfo) => {
    await page.route(CSS, (route) =>
      route.fulfill({
        status: sample.status,
        contentType: sample.contentType,
        body: sample.body,
      }),
    );
    await page.goto('/ui/app/');
    const error = await expectAppStyles(page).catch((reason: unknown) => String(reason));
    expect(error).toContain('app stylesheet not loaded:');
    expect(error).toContain('"rules":0');
    await testInfo.attach('stylesheet-diagnostic', {
      body: String(error),
      contentType: 'text/plain',
    });
    expect(error).toContain(`"status":${String(sample.status)}`);
    expect(error).toContain(`"content-type":"${sample.contentType}"`);
    expect(error).toContain('response bodies: not read');
    expect(error).not.toContain('"bodyBytes"');
    expect(error).not.toContain('"failure":');
  });
}

test('stylesheet readiness rejects a blocked request without inventing failure evidence', async ({
  page,
}) => {
  await page.route(CSS, (route) => route.abort('failed'));
  await page.goto('/ui/app/');
  const error = await expectAppStyles(page).catch((reason: unknown) => String(reason));
  expect(error).toContain('app stylesheet not loaded:');
  expect(error).toContain('request failure/completion events: not observed');
  expect(error).not.toContain('"failure":');
  expect(error).not.toContain('"responseEnd":-1');
});

test('an empty stylesheet fails before axe is injected', async ({ page }) => {
  await page.route(CSS, (route) => route.fulfill({ contentType: 'text/css', body: '' }));
  await page.goto('/ui/app/');
  await expect(axeCounts(page)).rejects.toThrow('app stylesheet not loaded:');
  expect(await page.evaluate(() => Reflect.has(window, 'axe'))).toBe(false);
});

test('stylesheet readiness rejects absent app links even if inline CSS remains', async ({
  page,
}) => {
  await page.goto('/ui/app/');
  await page.evaluate(() => {
    document.querySelectorAll('link[rel~="stylesheet"]').forEach((link) => {
      link.remove();
    });
    const style = document.createElement('style');
    style.textContent = 'body { margin: 0 }';
    document.head.append(style);
  });
  await expect(expectAppStyles(page)).rejects.toThrow('app stylesheet not loaded:');
});

test('stylesheet readiness still requires every linked sheet to have rules', async ({ page }) => {
  await page.goto('/ui/app/');
  await expectAppStyles(page);
  await page.route('**/empty-extra.css', (route) =>
    route.fulfill({ contentType: 'text/css', body: '' }),
  );
  await page.evaluate(async () => {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/empty-extra.css';
    const loaded = new Promise<void>((resolve) => {
      link.addEventListener('load', () => {
        resolve();
      });
    });
    document.head.append(link);
    await loaded;
  });
  await expect(expectAppStyles(page)).rejects.toThrow('"rules":0');
});

test('stylesheet diagnostics do not wait for or retry a pending CSS request', async ({ page }) => {
  let release: (() => void) | undefined;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let requests = 0;
  await page.route(CSS, async (route) => {
    requests++;
    await held;
    await route.continue();
  });
  try {
    await page.goto('/ui/app/', { waitUntil: 'commit' });
    await expect(page.locator('link[rel~="stylesheet"]')).toBeAttached();
    await expect.poll(() => requests).toBe(1);
    const error = await expectAppStyles(page).catch((reason: unknown) => String(reason));
    expect(error).toContain('app stylesheet not loaded:');
    expect(error).toContain('request failure/completion events: not observed');
    expect(error).not.toContain('"responseEnd":-1');
    expect(error).not.toContain('"bodyBytes"');
  } finally {
    release?.();
  }
  await page.waitForLoadState('load');
  await expectAppStyles(page);
  expect(requests).toBe(1);
});
