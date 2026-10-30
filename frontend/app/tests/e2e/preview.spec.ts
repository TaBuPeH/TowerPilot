import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import {
  axeViolations,
  expectAppStyles,
  openScenario,
  posts,
  WCAG_AA_TAGS,
} from './helpers/helpers.ts';
import { previewBody, previewSample } from '../support/previewFixture.ts';
import { record } from '../../scripts/generate-contract.mjs';
import { installPreviewRace, raceProbe, releasePreviewRace } from './helpers/preview-race.ts';

const SCREENS = path.resolve(
  process.env['TP_F6_SCREENS_DIR'] ?? '../../backend/logs/reviews/frontend-f6-screens',
);
mkdirSync(SCREENS, { recursive: true });
const region = (page: Page) => page.locator('.tp-preview');
/** The viewer settings sit behind a disclosure; open it (once) and return the panel. */
async function settings(page: Page) {
  const disclosure = region(page).locator('details.tp-preview__settings');
  if ((await disclosure.getAttribute('open')) === null) await disclosure.locator('summary').click();
  return region(page);
}
const verdict = (page: Page) => region(page).locator('[data-preview-verdict]');
const STREAM = '/api/stream.mjpg';
/**
 * Opens the Dashboard. The preview now attaches by itself, so by default this waits for that
 * session to be requested (or to fail), presses Stop and clears the request log. The tests below
 * then drive sessions by hand as before. `auto: true` leaves the automatic session running.
 */
async function open(page: Page, scenario = 'preview', width = 1360, auto = false) {
  const requests: string[] = [];
  const closed: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/stream') || url.pathname === '/api/frame.png')
      requests.push(url.pathname + url.search);
  });
  page.on('requestfailed', (request) => {
    if (request.url().includes('/api/stream.mjpg')) closed.push(request.url());
  });
  const errors = await openScenario(page, scenario, { width });
  if (!auto) {
    // The preview attaches only once the status read says the emulator is connected.
    await expect(page.getByText('Connected', { exact: true }).first()).toBeVisible();
    await expect
      .poll(
        async () =>
          requests.some((p) => p.startsWith(STREAM)) ||
          (await verdict(page).innerText()) === 'Preview failed',
      )
      .toBe(true);
    const streamed = requests.some((p) => p.startsWith(STREAM));
    await region(page).getByRole('button', { name: 'Stop preview', exact: true }).click();
    await expect(
      region(page).getByRole('button', { name: 'Start preview', exact: true }),
    ).toBeVisible();
    // A stream that already ended by itself (truncated, closed) has nothing left to abort.
    if (streamed)
      await expect
        .poll(() => closed.length, { timeout: 1500 })
        .toBe(1)
        .catch(() => undefined);
    requests.length = 0;
    closed.length = 0;
  }
  return { requests, closed, errors };
}
async function start(page: Page) {
  await region(page).getByRole('button', { name: 'Start preview', exact: true }).click();
}
async function layout(page: Page, width: number) {
  await expectAppStyles(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  expect(
    await region(page)
      .locator('button, select, summary, a')
      .evaluateAll(
        (nodes, width) =>
          nodes
            .filter((node) => {
              const r = node.getBoundingClientRect();
              return (
                r.width > 0 &&
                r.height > 0 &&
                (r.width < 24 || r.height < (width === 390 && node.tagName === 'BUTTON' ? 44 : 24))
              );
            })
            .map((node) => node.outerHTML),
        width,
      ),
  ).toEqual([]);
  const issues = await axeViolations(page, WCAG_AA_TAGS);
  expect(issues).toEqual([]);
  await expect(page.getByRole('heading', { name: 'Control', exact: true })).toBeVisible();
}
for (const width of [1360, 1054, 390]) {
  for (const state of ['off', 'receiving', 'fallback', 'failed', 'stale']) {
    test(`PV1 PV2 PV7 PV8 ${state} picture, axe, portrait, reading order and layout at ${width}`, async ({
      page,
    }) => {
      const scenario = ['fallback', 'failed', 'stale'].includes(state)
        ? `preview_${state}`
        : 'preview';
      const capture = await open(page, scenario, width);
      expect(capture.requests).toEqual([]);
      if (state !== 'off') await start(page);
      const expected = {
        off: 'Preview off',
        receiving: 'Receiving frames',
        fallback: 'Receiving via fallback',
        failed: 'Preview failed',
        stale: 'Frame stale',
      }[state];
      await expect(verdict(page)).toHaveText(expected ?? '');
      if (state !== 'off') {
        await expect
          .poll(() => capture.requests.filter((p) => p.startsWith('/api/stream.mjpg')).length)
          .toBe(1);
        const url = capture.requests.find((p) => p.startsWith('/api/stream.mjpg')) ?? '';
        expect(url).toMatch(
          /^\/api\/stream.mjpg\?instance=main&stream_id=pv_[A-Za-z0-9_-]{1,61}&fps=15&scale=0.65&q=85&backend=auto$/,
        );
        const streamId = new URL(url, 'http://fixture').searchParams.get('stream_id');
        expect(capture.requests).toContain(`/api/stream/status?stream_id=${streamId}`);
        const bounds = await region(page).locator('canvas').boundingBox();
        const natural = await region(page)
          .locator('canvas')
          .evaluate((canvas) =>
            canvas instanceof HTMLCanvasElement
              ? { width: canvas.width, height: canvas.height }
              : null,
          );
        expect(bounds).not.toBeNull();
        if (bounds !== null && natural !== null)
          expect(bounds.width / bounds.height).toBeCloseTo(natural.width / natural.height, 2);
      }
      expect(capture.requests.some((p) => p.startsWith('/api/frame.png'))).toBe(false);
      await page.screenshot({
        path: path.join(SCREENS, `preview-${state}-${width}.png`),
        fullPage: true,
      });
      await layout(page, width);
      expect(await posts(page)).toEqual([]);
      expect(capture.errors).toEqual([]);
      const order = await region(page).evaluate((node) => {
        const verdict = node.querySelector('[data-preview-verdict]');
        const canvas = node.querySelector('canvas');
        return (
          verdict !== null &&
          canvas !== null &&
          Boolean(verdict.compareDocumentPosition(canvas) & Node.DOCUMENT_POSITION_FOLLOWING)
        );
      });
      expect(order).toBe(true);
      // At these workspace widths, view-only preview follows every operations module.
      const previewBox = await region(page).boundingBox();
      const recentBox = await page.locator('[data-region="recent"]').boundingBox();
      expect(previewBox).not.toBeNull();
      expect(recentBox).not.toBeNull();
      expect(previewBox?.y ?? 0).toBeGreaterThanOrEqual(
        (recentBox?.y ?? Infinity) + (recentBox?.height ?? 0),
      );
    });
  }
}

test('FOCUS #5 the preview starts by itself, Stop is respected, hide and route leave reattach, reload starts again', async ({
  page,
}) => {
  const capture = await open(page, 'preview', 1360, true);
  const streams = () => capture.requests.filter((p) => p.startsWith(STREAM)).length;
  const reattached = async (count: number) => {
    await expect(verdict(page)).toHaveText('Receiving frames');
    await expect(region(page).locator('canvas')).toBeVisible();
    await expect.poll(streams).toBe(count);
  };
  await reattached(1);
  await region(page).getByRole('button', { name: 'Stop preview', exact: true }).click();
  await expect(verdict(page)).toHaveText('Preview off');
  expect(
    await region(page)
      .locator('canvas')
      .evaluate((c) => (c instanceof HTMLCanvasElement ? c.width : -1)),
  ).toBe(0);
  await expect.poll(() => capture.closed.length).toBe(1);
  // The person's Stop holds across dashboard reads, a route leave and a return.
  await page.waitForTimeout(3000);
  expect(streams()).toBe(1);
  await (
    await settings(page)
  )
    .getByRole('link', { name: 'Preview delivery settings on Emulators' })
    .click();
  await expect(region(page)).toHaveCount(0);
  await page.evaluate(() => {
    location.hash = 'dashboard';
  });
  await expect(verdict(page)).toHaveText('Preview off');
  expect(streams()).toBe(1);
  await start(page);
  await reattached(2);
  // Collapse closes the stream; showing the screen again reattaches without a press.
  await region(page).getByRole('button', { name: 'Hide screen', exact: true }).click();
  await expect.poll(() => capture.closed.length).toBe(2);
  await region(page).getByRole('button', { name: 'Show screen', exact: true }).click();
  await reattached(3);
  // Synthetic document lifecycle events: Chromium still drives the page; no device action.
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(
    region(page).getByRole('button', { name: 'Start preview', exact: true }),
  ).toBeVisible();
  expect(
    await region(page)
      .locator('canvas')
      .evaluate((c) => (c instanceof HTMLCanvasElement ? c.width : -1)),
  ).toBe(0);
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await reattached(4);
  // Leaving the Dashboard and coming back reattaches by itself.
  await (
    await settings(page)
  )
    .getByRole('link', { name: 'Preview delivery settings on Emulators' })
    .click();
  await expect(region(page)).toHaveCount(0);
  await page.evaluate(() => {
    location.hash = 'dashboard';
  });
  await reattached(5);
  // A reload is a new page session: the choice to stop does not persist, the preview starts again.
  await page.reload();
  await expect(verdict(page)).toHaveText('Receiving frames');
  await expect(region(page).locator('canvas')).toBeVisible();
  expect(streams()).toBeGreaterThanOrEqual(5);
});

for (const device of ['offline', 'unknown']) {
  test(`PV1 PV6 ${device} transport prevents capture despite locally available backends`, async ({
    page,
  }) => {
    // Recorded status response, replacing only which recorded connection read the fake answers.
    const { loadJsonWithIdentity, DASHBOARD_FIXTURE_PATH } =
      await import('../../scripts/contract-fixture.mjs');
    const cases = record(record(loadJsonWithIdentity(DASHBOARD_FIXTURE_PATH).fixture)['cases']);
    const body = record(record(cases['status'])[`device_state_${device}`])['body'];
    await page.route('**/api/status', (route) => route.fulfill({ json: body }));
    const capture = await open(page, 'preview', 1360, true);
    await expect(
      region(page).getByRole('button', { name: 'Start preview', exact: true }),
    ).toBeDisabled();
    await expect(
      region(page).getByText('Emulator offline; the preview starts when it is back.'),
    ).toBeVisible();
    // Several dashboard polls pass with the device offline and no stream is ever requested.
    await page.waitForTimeout(3000);
    await (
      await settings(page)
    )
      .getByRole('button', { name: 'Check availability', exact: true })
      .click();
    await expect(
      region(page).getByText('Prerequisites available', { exact: true }).first(),
    ).toBeVisible();
    expect(capture.requests.filter((p) => p.startsWith('/api/stream.mjpg'))).toEqual([]);
  });
}

test('PV3 PV4 PV5 PV6 saved fractional FPS and unsupported-combination request plumbing, all choices, exact URLs, persistence and reconnect IDs', async ({
  page,
}) => {
  const capture = await open(page, 'preview_saved');
  const fps = (await settings(page)).getByLabel('Target FPS', { exact: true });
  const source = (await settings(page)).getByLabel('Requested source', { exact: true });
  const quality = (await settings(page)).getByLabel('Picture quality', { exact: true });
  await expect(quality).toHaveValue('balanced');
  await (
    await settings(page)
  )
    .getByRole('button', { name: 'Check availability', exact: true })
    .click();
  await expect(fps.getByRole('option', { name: '12.5 FPS', exact: true })).toBeAttached();
  await expect(region(page).getByText('Saved default: 12.5 FPS', { exact: true })).toBeVisible();
  await expect(region(page).getByText(/Saved source:\s*scrcpy/)).toBeVisible();
  for (const value of ['0.5', '1', '2', '4', '10', '15', '30', '12.5']) {
    await fps.selectOption(value);
    expect(capture.requests.filter((p) => p.startsWith('/api/stream.mjpg'))).toHaveLength(0);
  }
  await start(page);
  await expect
    .poll(() => capture.requests.filter((p) => p.startsWith('/api/stream.mjpg')).length)
    .toBe(1);
  expect(capture.requests.at(-1)).toMatch(/fps=12.5&scale=0.65&q=85&backend=auto$/);
  for (const value of ['0.5', '1', '2', '4', '10', '15', '30', '12.5']) {
    const count = capture.requests.filter((p) => p.startsWith('/api/stream.mjpg')).length;
    await fps.selectOption(value);
    await expect
      .poll(() => capture.requests.filter((p) => p.startsWith('/api/stream.mjpg')).length)
      .toBe(count + 1);
    const last = capture.requests.filter((p) => p.startsWith('/api/stream.mjpg')).at(-1);
    expect(last).toContain(`&fps=${value}&`);
  }
  for (const value of ['native', 'balanced', 'low']) {
    const count = capture.requests.filter((p) => p.startsWith('/api/stream.mjpg')).length;
    await quality.selectOption(value);
    await expect
      .poll(() => capture.requests.filter((p) => p.startsWith('/api/stream.mjpg')).length)
      .toBe(count + 1);
    const pair = { native: 'scale=1&q=90', balanced: 'scale=0.65&q=85', low: 'scale=0.4&q=70' }[
      value
    ];
    expect(capture.requests.filter((p) => p.startsWith('/api/stream.mjpg')).at(-1)).toContain(pair);
  }
  for (const value of ['mumu', 'scrcpy', 'adb', 'auto']) {
    const count = capture.requests.filter((p) => p.startsWith('/api/stream.mjpg')).length;
    await source.selectOption(value);
    await expect
      .poll(() => capture.requests.filter((p) => p.startsWith('/api/stream.mjpg')).length)
      .toBe(count + 1);
    expect(capture.requests.filter((p) => p.startsWith('/api/stream.mjpg')).at(-1)).toContain(
      `backend=${value}`,
    );
  }
  const old = capture.requests.filter((p) => p.startsWith('/api/stream.mjpg')).at(-1);
  await region(page).getByRole('button', { name: 'Reconnect preview', exact: true }).click();
  await expect
    .poll(() => capture.requests.filter((p) => p.startsWith('/api/stream.mjpg')).at(-1))
    .not.toBe(old);
  await region(page).getByRole('button', { name: 'Stop preview', exact: true }).click();
  await page.reload();
  await expect(fps).toHaveValue('12.5');
  await expect(source).toHaveValue('auto');
  await expect(quality).toHaveValue('low');
  await region(page).getByRole('button', { name: 'Stop preview', exact: true }).click();
  await expect(verdict(page)).toHaveText('Preview off');
  const stored = await page.evaluate(
    () => JSON.parse(localStorage.getItem('tp.preview.preferences.v1') ?? '{}') as unknown,
  );
  expect(stored).toEqual({ fps: 12.5, source: 'auto', quality: 'low' });
  // These 12.5 FPS combinations are request-plumbing tests, not exact backend recordings.
  const replayRaw: unknown = await (await page.request.get('/__fake/preview')).json();
  const bindings = record(replayRaw)['bindings'];
  expect(
    Array.isArray(bindings) &&
      bindings.some((row: unknown) => record(row)['requestMode'] === 'synthetic-request-plumbing'),
  ).toBe(true);
  expect(await posts(page)).toEqual([]);
});

test('PV4 recorded source selections display requested and actual sources with session-bound status', async ({
  page,
}) => {
  await open(page);
  for (const [source, label] of [
    ['mumu', 'MuMu native'],
    ['scrcpy', 'scrcpy'],
    ['adb', 'ADB'],
    ['auto', 'MuMu native'],
  ]) {
    await (await settings(page)).getByLabel('Target FPS', { exact: true }).selectOption('15');
    await (
      await settings(page)
    )
      .getByLabel('Picture quality', { exact: true })
      .selectOption(source === 'auto' ? 'balanced' : 'native');
    await (
      await settings(page)
    )
      .getByLabel('Requested source', { exact: true })
      .selectOption(source ?? '');
    await start(page);
    await expect(verdict(page)).toHaveText('Receiving frames');
    await expect(
      region(page)
        .locator('p')
        .filter({ hasText: /^Displayed source:/ }),
    ).toContainText(label ?? '');
    const replayRaw: unknown = await (await page.request.get('/__fake/preview')).json();
    const bindings = record(replayRaw)['bindings'];
    if (!Array.isArray(bindings)) throw new Error('Replay bindings missing');
    expect(record(bindings.at(-1))['requestMode']).toBe('recorded');
    expect(record(replayRaw)['maxActive']).toBe(1);
    await region(page).getByRole('button', { name: 'Stop preview', exact: true }).click();
    await expect
      .poll(async () => {
        const raw: unknown = await (await page.request.get('/__fake/preview')).json();
        return record(raw)['activeCount'];
      })
      .toBe(0);
  }
  expect(await posts(page)).toEqual([]);
});

for (const [scenario, requested, actual] of [
  ['preview_fallback', 'auto', 'scrcpy'],
  ['preview_fallback_adb', 'auto', 'ADB'],
  ['preview_fallback_scrcpy_adb', 'scrcpy', 'ADB'],
  ['preview_fallback_requested_mumu', 'mumu', 'scrcpy'],
]) {
  test(`PV4 recorded fallback ${scenario} preserves requested ${requested} and displays actual ${actual}`, async ({
    page,
  }) => {
    await open(page, scenario);
    await (
      await settings(page)
    )
      .getByLabel('Picture quality', { exact: true })
      .selectOption('native');
    await (
      await settings(page)
    )
      .getByLabel('Requested source', { exact: true })
      .selectOption(requested ?? '');
    await start(page);
    await expect(verdict(page)).toHaveText('Receiving via fallback');
    await expect(
      (await settings(page)).getByLabel('Requested source', { exact: true }),
    ).toHaveValue(requested ?? '');
    await expect(
      region(page)
        .locator('p')
        .filter({ hasText: /^Displayed source:/ }),
    ).toContainText(actual ?? '');
    const raw: unknown = await (await page.request.get('/__fake/preview')).json();
    const bindings = record(raw)['bindings'];
    if (!Array.isArray(bindings)) throw new Error('Replay bindings missing');
    expect(record(bindings.at(-1))['requestMode']).toBe('recorded');
    expect(await posts(page)).toEqual([]);
  });
}

for (const [scenario, text] of [
  ['preview_package', 'scrcpy server missing or unreadable'],
  ['preview_decoder', 'PyAV missing'],
  ['preview_unavailable', 'fixture mumu unavailable'],
  ['preview_both_missing', 'PyAV missing'],
  ['preview_bad_settings', 'preview.fps must be between 0.5 and 30'],
]) {
  test(`PV6 recorded availability ${scenario}`, async ({ page }) => {
    const capture = await open(page, scenario);
    await (
      await settings(page)
    )
      .getByRole('button', { name: 'Check availability', exact: true })
      .click();
    await expect(
      region(page)
        .getByText(text ?? '', { exact: false })
        .first(),
    ).toBeVisible();
    expect(capture.requests).toEqual(['/api/stream/backends']);
    expect(await posts(page)).toEqual([]);
  });
}

test('PV1 PV4 view-only capture remains available while automation owns the screen', async ({
  page,
}) => {
  const capture = await open(page, 'preview_owned');
  await start(page);
  await expect(verdict(page)).toHaveText('Receiving frames');
  expect(capture.requests.filter((p) => p.startsWith('/api/stream.mjpg'))).toHaveLength(1);
  expect(await posts(page)).toEqual([]);
});

test('PV2 PV7 an error marker recovers in the same session with the recorded recovery report', async ({
  page,
}) => {
  const capture = await open(page, 'preview_marker');
  await start(page);
  await expect(verdict(page)).toHaveText('Preview failed');
  await page.request.get('/__fake/set?scenario=preview_recovered');
  await expect(verdict(page)).toHaveText('Receiving frames', { timeout: 10_000 });
  await expect(
    region(page)
      .locator('dt')
      .filter({ hasText: /^Server errors$/ })
      .locator('+ dd'),
  ).toHaveText('1');
  expect(capture.requests.filter((p) => p.startsWith('/api/stream.mjpg'))).toHaveLength(1);
  expect(await posts(page)).toEqual([]);
});
for (const scenario of [
  'preview_split',
  'preview_truncate',
  'preview_oversized',
  'preview_malformed',
  'preview_close',
  'preview_idle',
  'preview_first',
  'preview_404',
  'preview_closed',
  'preview_cleanup',
  'preview_marker',
]) {
  test(`PV2 PV7 multipart and status state ${scenario} has an honest verdict`, async ({ page }) => {
    const capture = await open(page, scenario);
    const missing =
      scenario === 'preview_404'
        ? page.waitForResponse(
            (response) =>
              new URL(response.url()).pathname === '/api/stream/status' &&
              response.status() === 404,
          )
        : null;
    await start(page);
    if (missing !== null) await missing;
    const expected =
      scenario === 'preview_split' || scenario === 'preview_first'
        ? 'Receiving frames'
        : scenario === 'preview_idle'
          ? 'Connecting'
          : scenario === 'preview_404'
            ? 'Delivery unknown'
            : 'Preview failed';
    await expect(verdict(page)).toHaveText(expected);
    if (scenario === 'preview_cleanup')
      await expect(
        region(page).getByText('Preview cleanup failed: fixture cleanup failed', { exact: true }),
      ).toBeVisible();
    await expect
      .poll(() => capture.requests.filter((p) => p.startsWith('/api/stream.mjpg')).length)
      .toBe(1);
    expect(capture.requests.some((p) => p.startsWith('/api/frame.png'))).toBe(false);
    expect(await posts(page)).toEqual([]);
  });
}
test('PV2 PV7 a healthy report with stalled pixels becomes stale; status read failure remains unknown', async ({
  page,
}) => {
  await open(page);
  await start(page);
  await expect(verdict(page)).toHaveText('Receiving frames');
  await expect(verdict(page)).toHaveText('Frame stale');
  await region(page).getByRole('button', { name: 'Stop preview', exact: true }).click();
  // Synthetic read transport failure; production JSON is not altered.
  await page.route('**/api/stream/status?*', (route) => route.abort('failed'));
  const failedRead = page.waitForEvent(
    'requestfailed',
    (request) => new URL(request.url()).pathname === '/api/stream/status',
  );
  await start(page);
  await failedRead;
  await expect(verdict(page)).toHaveText('Delivery unknown');
});
test('PV3 PV7 ADB target 15 reports effective 4; null measurements remain unknown', async ({
  page,
}) => {
  await open(page, 'preview_adb');
  await start(page);
  await expect(verdict(page)).toHaveText('Receiving frames');
  await expect(
    region(page)
      .locator('dt')
      .filter({ hasText: /^Target FPS$/ })
      .locator('+ dd'),
  ).toHaveText('15');
  await expect(
    region(page)
      .locator('dt')
      .filter({ hasText: /^Effective FPS$/ })
      .locator('+ dd'),
  ).toHaveText('4');
  await expect(region(page).getByText('Source age unknown', { exact: true })).toBeVisible();
  await region(page).getByRole('button', { name: 'Stop preview', exact: true }).click();
  await page.request.get('/__fake/set?scenario=preview_idle');
  await start(page);
  await expect(
    region(page)
      .locator('dt')
      .filter({ hasText: /^Server FPS$/ })
      .locator('+ dd'),
  ).toHaveText('unknown');
  await expect(
    region(page)
      .locator('dt')
      .filter({ hasText: /^Server errors$/ })
      .locator('+ dd'),
  ).toHaveText('0');
  await expect(
    region(page)
      .locator('dt')
      .filter({ hasText: /^Server dimensions$/ })
      .locator('+ dd'),
  ).toHaveText('unknown');
});
async function switchPreviewAccount(page: Page) {
  // Select another exact recorded context; the response body is unchanged.
  await page.route('**/api/config', (route) =>
    route.fulfill({ json: previewBody('config_state', 'effective_view_of_the_other_account') }),
  );
  await page.evaluate(() => {
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect
    .poll(() =>
      page.evaluate(() => JSON.parse(sessionStorage.getItem('tp.context.v1') ?? '{}') as unknown),
    )
    .toMatchObject({ account: 'beta', profile: 'theirs' });
}

/** Viewer preferences are read once at load; set them before the page starts. */
async function preferences(page: Page, value: { source: string; quality: string }) {
  await page.addInitScript((value) => {
    localStorage.setItem('tp.preview.preferences.v1', JSON.stringify({ fps: null, ...value }));
  }, value);
}

async function previewPixels(page: Page) {
  return region(page)
    .locator('canvas')
    .evaluate((canvas) => {
      if (!(canvas instanceof HTMLCanvasElement)) throw new Error('Missing preview canvas');
      return {
        width: canvas.width,
        height: canvas.height,
        pixel: [...(canvas.getContext('2d')?.getImageData(0, 0, 1, 1).data ?? [])],
      };
    });
}

const diagnostic = (page: Page, label: string) =>
  region(page).locator('dt').filter({ hasText: label }).locator('+ dd');

test('PV1 PV4 PV7 C-01 obsolete status and native decode cannot repaint a replacement account session', async ({
  page,
}) => {
  await installPreviewRace(page, 'status-decode');
  await preferences(page, { source: 'scrcpy', quality: 'native' });
  // The first session attaches by itself; the account switch attaches the replacement by itself.
  const capture = await open(page, 'preview', 1360, true);
  await expect.poll(async () => (await previewPixels(page)).width).toBe(12);
  await expect.poll(async () => (await raceProbe(page)).decodeHeld).toBe(true);
  await expect.poll(async () => (await raceProbe(page)).held).toBe(true);
  const oldUrl = capture.requests.find((p) => p.startsWith('/api/stream.mjpg')) ?? '';
  const oldId = new URL(oldUrl, 'http://fixture').searchParams.get('stream_id');
  await expect(region(page).getByText('Displayed source: scrcpy', { exact: true })).toBeVisible();
  await expect(
    region(page).getByText('Source age estimate at server encode: 50 ms', { exact: true }),
  ).toBeVisible();
  await switchPreviewAccount(page);
  await expect(verdict(page)).toHaveText('Receiving frames');
  const replacementUrl = capture.requests.filter((p) => p.startsWith('/api/stream.mjpg'))[1] ?? '';
  const replacementId = new URL(replacementUrl, 'http://fixture').searchParams.get('stream_id');
  expect(replacementId).not.toBe(oldId);
  const pixels = await previewPixels(page);
  expect(pixels).toMatchObject({ width: 12, height: 24 });
  const before = await raceProbe(page);
  // Each exact recording has two ordered parts, sequences 1/2. Image 2 is the
  // held old sequence 2; images 3/4 are the replacement's sequences 1/2.
  expect(before.draws.map((draw) => draw.image)).toEqual([1, 3, 4]);
  expect(before.decodeCancelled).toBe(true);
  expect(before.aborted).toContain(oldUrl);
  expect(before.aborted).toContain(`/api/stream/status?stream_id=${oldId}`);
  await expect.poll(() => capture.closed.length).toBe(1);
  const receiptBefore = await region(page)
    .getByText(/^Frame received /)
    .innerText();
  await releasePreviewRace(page);
  expect((await raceProbe(page)).released).toBe(true);
  expect((await raceProbe(page)).draws).toEqual(before.draws);
  expect(await previewPixels(page)).toEqual(pixels);
  await expect(verdict(page)).toHaveText('Receiving frames');
  await expect(region(page).getByText('Displayed source: scrcpy', { exact: true })).toBeVisible();
  await expect(
    region(page).getByText('Source age estimate at server encode: 50 ms', { exact: true }),
  ).toBeVisible();
  await expect(diagnostic(page, 'Server dimensions')).toHaveText('12 × 24');
  const receiptAfter = await region(page)
    .getByText(/^Frame received /)
    .innerText();
  expect(Number(receiptAfter.split(' ')[2])).toBeGreaterThanOrEqual(
    Number(receiptBefore.split(' ')[2]),
  );
  // Measure from completed release, not from an earlier replacement poll. A
  // replacement request at least one whole 2-second interval later proves an
  // obsolete poll scheduled by the released completion would already have run.
  await expect
    .poll(async () => {
      const probe = await raceProbe(page);
      const releasedAt = probe.releasedAt;
      return (
        releasedAt !== null &&
        probe.requests.some(
          (request) =>
            request.path === `/api/stream/status?stream_id=${replacementId}` &&
            request.at - releasedAt >= 2000,
        )
      );
    })
    .toBe(true);
  expect(
    capture.requests.filter((p) => p === `/api/stream/status?stream_id=${oldId}`),
  ).toHaveLength(1);
  expect(capture.requests.filter((p) => p.startsWith('/api/stream.mjpg'))).toHaveLength(2);
  expect(capture.requests.filter((p) => p === '/api/stream/backends')).toHaveLength(2);
  await expect(verdict(page)).toHaveText('Frame stale');
  expect(await previewPixels(page)).toEqual(pixels);
  expect((await raceProbe(page)).draws).toEqual(before.draws);
  await expect(region(page).getByText('Displayed source: scrcpy', { exact: true })).toBeVisible();
  await expect(
    region(page).getByText('Source age estimate at server encode: 50 ms', { exact: true }),
  ).toBeVisible();
  await expect(diagnostic(page, 'Server dimensions')).toHaveText('12 × 24');
  expect(capture.requests.filter((p) => p.startsWith('/api/stream/status'))).toEqual([
    `/api/stream/status?stream_id=${oldId}`,
    ...capture.requests.filter((p) => p === `/api/stream/status?stream_id=${replacementId}`),
  ]);
  const fake = record(await (await page.request.get('/__fake/preview')).json());
  expect(fake['activeCount']).toBe(1);
  expect(fake['maxActive']).toBe(1);
  expect(await posts(page)).toEqual([]);
  expect(capture.errors).toEqual([]);
});

test('PV1 PV4 C-01 delayed old-instance availability cannot open a stream after a replacement starts', async ({
  page,
}) => {
  await installPreviewRace(page, 'availability');
  // Exact recorded other-instance context and availability; only delivery is held.
  await page.route('**/api/config', (route) =>
    route.fulfill({
      json: previewBody('profile_target', 'read_config_after_editor_instance_switch'),
    }),
  );
  await page.route(
    '**/api/stream/backends',
    (route) => route.fulfill({ json: previewBody('preview_backends', 'other_instance') }),
    { times: 1 },
  );
  await preferences(page, { source: 'auto', quality: 'low' });
  // The first session attaches by itself and its availability answer is held; the account
  // switch attaches the replacement by itself.
  const capture = await open(page, 'preview', 1360, true);
  await expect
    .poll(() =>
      page.evaluate(() => JSON.parse(sessionStorage.getItem('tp.context.v1') ?? '{}') as unknown),
    )
    .toMatchObject({ instance: 'other' });
  await expect.poll(async () => (await raceProbe(page)).held).toBe(true);
  await switchPreviewAccount(page);
  await expect(verdict(page)).toHaveText('Receiving frames');
  const pixels = await previewPixels(page);
  expect(pixels).toMatchObject({ width: 432, height: 1024 });
  expect((await raceProbe(page)).aborted).toContain('/api/stream/backends');
  await releasePreviewRace(page);
  expect((await raceProbe(page)).released).toBe(true);
  await expect(verdict(page)).toHaveText('Receiving frames');
  expect(await previewPixels(page)).toEqual(pixels);
  await expect(diagnostic(page, 'Server dimensions')).toHaveText('432 × 1024');
  await expect
    .poll(() => capture.requests.filter((p) => p.startsWith('/api/stream/status')).length)
    .toBe(2);
  expect(capture.requests.filter((p) => p.startsWith('/api/stream.mjpg'))).toHaveLength(1);
  expect(capture.requests.filter((p) => p === '/api/stream/backends')).toHaveLength(2);
  const id = new URL(
    capture.requests.find((p) => p.startsWith('/api/stream.mjpg')) ?? '',
    'http://fixture',
  ).searchParams.get('stream_id');
  expect(capture.requests.filter((p) => p.startsWith('/api/stream/status'))).toEqual([
    `/api/stream/status?stream_id=${id}`,
    `/api/stream/status?stream_id=${id}`,
  ]);
  expect(await posts(page)).toEqual([]);
  expect(capture.errors).toEqual([]);
});

test('PV3 PV4 PV5 PV7 PV8 C-02 recorded Low receives a portrait picture with readable controls at 390', async ({
  page,
}) => {
  const low = previewSample('preview_stream', 'low');
  const recordedPath = String(record(low['request'])['path']);
  expect(recordedPath).toBe(
    '/api/stream.mjpg?instance=main&stream_id=pv1&fps=15&scale=0.4&q=70&backend=auto',
  );
  const capture = await open(page, 'preview', 390);
  await (await settings(page)).getByLabel('Picture quality', { exact: true }).selectOption('low');
  const response = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/stream.mjpg');
  await start(page);
  expect((await response).headers()['x-preview-fixture-mode']).toBe('recorded');
  await expect(verdict(page)).toHaveText('Receiving frames');
  const url = capture.requests.find((p) => p.startsWith('/api/stream.mjpg')) ?? '';
  const id = new URL(url, 'http://fixture').searchParams.get('stream_id');
  expect(id).toMatch(/^pv_[A-Za-z0-9_-]{1,61}$/);
  // Only session correlation differs from the exact recorded request.
  expect(url).toBe(recordedPath.replace('stream_id=pv1', `stream_id=${id}`));
  const pixels = await previewPixels(page);
  expect(pixels).toEqual({ width: 432, height: 1024, pixel: [128, 128, 128, 255] });
  await expect(diagnostic(page, 'Server dimensions')).toHaveText('432 × 1024');
  await expect(
    region(page).getByText('Displayed source: MuMu native', { exact: true }),
  ).toBeVisible();
  await expect(region(page).getByText('Source age unknown', { exact: true })).toBeVisible();
  await expect(region(page).getByText(/^Frame received \d+ s ago$/)).toBeVisible();
  const bounds = await region(page).locator('canvas').boundingBox();
  expect(bounds).not.toBeNull();
  if (bounds !== null) {
    expect(bounds.width / bounds.height).toBeCloseTo(432 / 1024, 3);
    expect(bounds.height).toBeGreaterThan(bounds.width);
  }
  for (const label of ['Target FPS', 'Requested source', 'Picture quality']) {
    const control = region(page).getByLabel(label, { exact: true });
    await control.scrollIntoViewIfNeeded();
    await expect(control).toBeVisible();
    expect(
      await control.evaluate((node) => Number.parseFloat(getComputedStyle(node).fontSize)),
    ).toBeGreaterThanOrEqual(14);
    expect(await control.evaluate((node) => node.clientWidth >= node.scrollWidth)).toBe(true);
  }
  await layout(page, 390);
  await expect(verdict(page)).toHaveText('Receiving frames');
  await page.screenshot({
    path: path.join(SCREENS, 'preview-low-receiving-390.png'),
    fullPage: true,
  });
  expect(capture.requests.filter((p) => p.startsWith('/api/stream.mjpg'))).toHaveLength(1);
  expect(capture.requests).toContain(`/api/stream/status?stream_id=${id}`);
  expect(await posts(page)).toEqual([]);
  expect(capture.errors).toEqual([]);
});
test('PV8 keyboard controls, visible focus and verdict-only polite announcements at 390', async ({
  page,
}) => {
  await openScenario(page, 'preview', { width: 390 });
  const show = region(page).locator('button[aria-controls="preview-content"]');
  await show.focus();
  // The screen is open by default: Enter hides it, Enter again shows it and it reattaches.
  await page.keyboard.press('Enter');
  await expect(show).toHaveAttribute('aria-expanded', 'false');
  await page.keyboard.press('Enter');
  await expect(show).toBeFocused();
  expect(await show.evaluate((node) => getComputedStyle(node).outlineStyle)).not.toBe('none');
  await expect(verdict(page)).toHaveText('Receiving frames');
  await page.keyboard.press('Tab');
  await expect(
    region(page).getByRole('button', { name: 'Stop preview', exact: true }),
  ).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(verdict(page)).toHaveText('Preview off');
  await region(page).getByRole('button', { name: 'Start preview', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(verdict(page)).toHaveText('Receiving frames');
  await expect(page.locator('[aria-live="polite"]')).toHaveCount(1);
  await expect(page.locator('[aria-live="polite"]')).toContainText('Receiving frames');
  expect(await region(page).locator('canvas').getAttribute('aria-live')).toBeNull();
  await region(page).getByRole('button', { name: 'Stop preview', exact: true }).focus();
  await page.keyboard.press('Enter');
  const fps = (await settings(page)).getByLabel('Target FPS', { exact: true });
  await fps.focus();
  await page.keyboard.press('ArrowDown');
  const source = (await settings(page)).getByLabel('Requested source', { exact: true });
  await source.focus();
  await page.keyboard.press('ArrowDown');
  await expect(source).toHaveValue('mumu');
  const quality = (await settings(page)).getByLabel('Picture quality', { exact: true });
  await quality.focus();
  await page.keyboard.press('ArrowDown');
  await expect(quality).toHaveValue('low');
  await layout(page, 390);
});
test('PV7 PV8 C-02 recorded cleanup error wraps at 390 with its exact JSON answer', async ({
  page,
}) => {
  // Keep the recorded cleanup answer exact; the fake only rebinds stream_id.
  await open(page, 'preview_cleanup', 390);
  await start(page);
  await expect(verdict(page)).toHaveText('Preview failed');
  await layout(page, 390);
});
