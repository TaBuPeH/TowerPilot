import { mkdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import {
  axeViolations,
  expectAppStyles,
  navigateApp,
  openScenario,
  posts,
  WCAG_AA_TAGS,
} from './helpers/helpers.ts';
import {
  loadJsonWithIdentity,
  DASHBOARD_PAGES_FIXTURE_PATH,
} from '../../scripts/contract-fixture.mjs';
import { record } from '../../scripts/generate-contract.mjs';

const SCREENS = path.resolve(
  process.env['TP_F6_SCREENS_DIR'] ?? '../../backend/logs/reviews/frontend-f6-screens',
);
mkdirSync(SCREENS, { recursive: true });
const CASES = record(record(loadJsonWithIdentity(DASHBOARD_PAGES_FIXTURE_PATH).fixture)['cases']);
const diagnosticCase = (name: string) => record(record(CASES['runtime_diagnostics'])[name]);
const settingsCase = (name: string) => record(record(CASES['settings'])[name]);
async function open(page: Page, width: number, scenario = 'runtime') {
  const problems = await openScenario(page, scenario, { width });
  await navigateApp(page, 'Runtime');
  await expect(page.getByRole('heading', { name: 'Runtime', exact: true })).toBeVisible();
  await expect(page.locator('[data-runtime-module="loop"] input').first()).toBeVisible();
  await expect(page.locator('[data-source-state]')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Scan coverage' })).toBeVisible();
  return problems;
}
const shot = (page: Page, name: string) =>
  page.screenshot({ path: path.join(SCREENS, `runtime-${name}.png`), fullPage: true });
async function layout(page: Page, width: number) {
  await expectAppStyles(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  const small = await page
    .locator('.tp-runtime button, .tp-runtime summary, .tp-runtime input')
    .evaluateAll(
      (nodes, width) =>
        nodes
          .filter((node) => {
            const rect = node.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return false;
            const floor = width === 390 && node.tagName === 'BUTTON' ? 44 : 24;
            return rect.width < floor - 1 || rect.height < floor - 1;
          })
          .map((node) => ({
            tag: node.tagName,
            text: node.textContent.slice(0, 100),
            width: node.getBoundingClientRect().width,
            height: node.getBoundingClientRect().height,
          })),
      width,
    );
  expect(small).toEqual([]);
  const issues = await axeViolations(page, WCAG_AA_TAGS);
  expect(issues).toEqual([]);
}
for (const width of [1360, 1054, 390]) {
  test(`T1 T2 T3 T4 T5 T6 Runtime shell, keyboard, a11y, reflow and settings scope at ${width}`, async ({
    page,
  }) => {
    const problems = await open(page, width);
    await expect(
      page
        .locator('[data-runtime-group="overrides"]')
        .getByText(/This emulator · next time automation starts/),
    ).toBeVisible();
    await expect(
      page
        .locator('[data-runtime-group="machine"]')
        .getByText(/This computer · every emulator · next time automation starts/),
    ).toBeVisible();
    await page.getByRole('button', { name: 'About Settings safety and readback' }).hover();
    await expect(page.getByRole('tooltip')).toContainText(/Dry run is not an emergency stop/);
    expect(await posts(page)).toEqual([]);
    await shot(page, `page-${width}`);
    await layout(page, width);
    const fps = page.locator('[data-runtime-module="loop"] [data-part="fps"]');
    await fps.fill('');
    await expect(fps).toHaveAttribute('aria-invalid', 'true');
    const save = page
      .locator('[data-runtime-module="loop"]')
      .getByRole('button', { name: 'Save', exact: true });
    await expect(save).toHaveAttribute('aria-disabled', 'true');
    await fps.fill('3');
    await fps.press('Tab');
    await expect(page.locator(':focus')).not.toHaveCount(0);
    const filters = page.locator('.tp-runtime__filters > summary');
    await filters.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByLabel('Kind')).not.toBeVisible();
    await page.keyboard.press('Enter');
    await expect(page.getByLabel('Kind')).toBeVisible();
    await expect(filters).toBeFocused();
    const limit = page.getByLabel('Event limit (1–1000)');
    await limit.fill('1001');
    await expect(limit).toHaveValue('1001');
    await expect(limit).toHaveAttribute('aria-invalid', 'true');
    await expect(page.getByRole('button', { name: 'Apply filters' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    await shot(page, `invalid-limit-${width}`);
    await limit.fill('200');
    await page.getByLabel('Kind').fill('unapplied');
    const details = page.locator('[data-event-key]').first();
    const summary = details.locator('summary');
    await summary.focus();
    await page.keyboard.press('Enter');
    await expect(details).toHaveAttribute('open');
    const read = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/diagnostics/events',
    );
    await page.getByRole('button', { name: 'Refresh events' }).click();
    await read;
    await expect(details).toHaveAttribute('open');
    await expect(page.getByLabel('Kind')).toHaveValue('unapplied');
    await page.getByLabel(/Auto-refresh every 5 seconds/).check();
    await summary.focus();
    await page.waitForResponse((r) => new URL(r.url()).pathname === '/api/diagnostics/events');
    await expect(summary).toBeFocused();
    await expect(details).toHaveAttribute('open');
    await page.getByLabel(/Auto-refresh every 5 seconds/).uncheck();
    await page.route('**/api/diagnostics/events*', (route) =>
      route.fulfill({ status: 400, json: diagnosticCase('unknown_filter_refused')['body'] }),
    );
    const failed = page.waitForResponse((r) => r.status() === 400);
    await page.getByRole('button', { name: 'Refresh events' }).click();
    await failed;
    await expect(page.getByText('Unknown diagnostic filter: colour')).toBeVisible();
    await expect(page.getByText(/last result remains visible and is stale/)).toBeVisible();
    await expect(details).toHaveAttribute('open');
    await shot(page, `failed-refresh-${width}`);
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Export last result as JSON' }).click();
    const downloaded = await download;
    const filename = await downloaded.path();
    const exported = JSON.parse(readFileSync(filename, 'utf8')) as {
      filters: unknown;
      coverage: unknown;
      writer_health: unknown;
      events: unknown;
    };
    expect(exported.filters).toEqual({});
    expect(exported).toMatchObject(
      record(diagnosticCase('every_event_oldest_first_with_log_coordinates')['body']),
    );
    expect(await posts(page)).toEqual([]);
    expect(problems).toEqual([]);
  });
  test(`T3 T5 source changed, unknown workers, bounded errors and writer health at ${width}`, async ({
    page,
  }) => {
    for (const [scenario, name, line] of [
      ['runtime_source_changed', 'source-changed', 'Source changed since the server loaded'],
      ['runtime_worker_unknown', 'unknown-worker', 'Liveness cannot be verified'],
      ['runtime_truncated', 'diagnostics-truncated', 'Malformed records'],
      ['runtime_read_errors', 'diagnostics-errors', 'the file is locked by another process'],
      ['runtime_writer_failure', 'writer-failure', 'the log file is locked'],
    ] as const) {
      await open(page, width, scenario);
      await expect(page.getByText(line, { exact: false }).first()).toBeVisible();
      if (scenario === 'runtime_worker_unknown') {
        for (const label of [
          'Worker source revision',
          'Worker source fingerprint at load',
          'Worker source captured at (epoch seconds)',
        ])
          await expect(
            page.getByText(label, { exact: true }).locator('..').locator('dd'),
          ).toHaveText('not reported');
        await page.getByText('Retained raw runtime response', { exact: true }).click();
        await expect(
          page
            .getByText('Retained raw runtime response', { exact: true })
            .locator('..')
            .locator('pre'),
        ).toContainText('"alive": null');
        const raw = page.getByRole('region', {
          name: 'Retained raw runtime response',
          exact: true,
        });
        await raw.focus();
        await expect(raw).toBeFocused();
        await page.keyboard.press('PageDown');
        await expect.poll(() => raw.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
      }
      await shot(page, `${name}-${width}`);
      await layout(page, width);
      expect(await posts(page)).toEqual([]);
    }
  });
  test(`T4 T6 applied filters survive reload and export ignores unapplied draft at ${width}`, async ({
    page,
  }) => {
    await open(page, width);
    await page.getByLabel('Operation ID').fill('op-1');
    const read = page.waitForResponse((r) =>
      r.url().includes('/api/diagnostics/events?operation_id=op-1'),
    );
    await page.getByRole('button', { name: 'Apply filters' }).click();
    await read;
    await expect(page).toHaveURL(/#runtime\?operation_id=op-1$/);
    await page.reload();
    await expect(page.getByLabel('Operation ID')).toHaveValue('op-1');
    await expect(page.locator('[data-event-key]')).toHaveCount(3);
    await page.getByLabel('Kind').fill('unsent');
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Export last result as JSON' }).click();
    const file = await (await download).path();
    const exported = JSON.parse(readFileSync(file, 'utf8')) as { filters: unknown };
    expect(exported.filters).toEqual({ operation_id: 'op-1' });
    expect(await posts(page)).toEqual([]);
  });
  test(`T1 T2 SR-5 SR-6 instance and machine settings save explicitly and read back at ${width}`, async ({
    page,
  }) => {
    for (const [scenario, module] of [
      ['runtime_save_loop', 'loop'],
      ['runtime_save_tap', 'tap'],
    ] as const) {
      await open(page, width, scenario);
      const form = page.locator(`[data-runtime-module="${module}"]`);
      await form.screenshot({
        path: path.join(SCREENS, `runtime-settings-scope-${module}-${width}.png`),
      });
      if (module === 'loop') {
        await form.locator('[data-part="fps"]').fill('3');
        await form.getByRole('checkbox').check();
      } else {
        for (const [key, value] of [
          ['jitter_ms#0', '20'],
          ['jitter_ms#1', '60'],
          ['jitter_px', '6'],
          ['max_rate_per_sec', '10.5'],
        ] as const)
          await form.locator(`[data-part="${key}"]`).fill(value);
      }
      const save = form.getByRole('button', { name: 'Save', exact: true });
      const response = page.waitForResponse(
        (r) => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/settings',
      );
      await save.click();
      expect((await response).status()).toBe(200);
      await expect(form).toContainText('Saved');
      await expect(save).toBeFocused();
      const recorded = record(
        record(CASES['settings'])[module === 'loop' ? 'saved_loop' : 'saved_tap'],
      );
      const writes = (await posts(page)).filter((entry) => entry.path === '/api/settings');
      expect(writes).toHaveLength(1);
      expect({
        method: writes[0]?.method,
        path: writes[0]?.path,
        body: JSON.parse(writes[0]?.body ?? '') as unknown,
      }).toEqual(recorded['request']);
      await page.getByRole('button', { name: 'Read settings again' }).click();
      await expect(
        form.locator(module === 'loop' ? '[data-part="fps"]' : '[data-part="jitter_px"]'),
      ).toHaveValue(module === 'loop' ? '3' : '6');
      expect((await posts(page)).filter((entry) => entry.path === '/api/settings')).toHaveLength(1);
    }
  });
  test(`T1 T2 F6T-R1-01 F6T-R1-04 failed settings save stays fenced until acceptance; failed reads label retained drafts at ${width}`, async ({
    page,
  }) => {
    await open(page, width);
    const attempts: { method: string; path: string; body: unknown }[] = [];
    let failedRead = false;
    await page.route('**/api/settings', async (route) => {
      if (route.request().method() === 'POST') {
        attempts.push({
          method: 'POST',
          path: '/api/settings',
          body: route.request().postDataJSON() as unknown,
        });
        const errorText = record(settingsCase('saved_then_the_answer_failed_500')['body'])['text'];
        if (typeof errorText !== 'string') throw new Error('recorded text 500 missing');
        await route.fulfill({
          status: 500,
          contentType: 'text/html',
          body: errorText,
        });
      } else {
        const recorded = settingsCase(
          failedRead ? 'profile_unreadable_yaml_400' : 'populated_account_every_module',
        );
        await route.fulfill({ status: Number(recorded['status']), json: recorded['body'] });
      }
    });
    const form = page.locator('[data-runtime-module="tap"]');
    await form.locator('[data-part="jitter_px"]').fill('6');
    await form.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(form).toContainText('Unknown');
    await form.locator('[data-part="jitter_px"]').fill('7');
    await expect(form.getByRole('button', { name: 'Save', exact: true })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    await expect(
      form.getByRole('button', { name: 'Continue from the saved values as read' }),
    ).toBeVisible();
    expect(attempts).toEqual([settingsCase('saved_then_the_answer_failed_500')['request']]);
    await form.screenshot({ path: path.join(SCREENS, `runtime-r1-unknown-save-${width}.png`) });
    failedRead = true;
    await page.getByRole('button', { name: 'Read settings again' }).click();
    await expect(
      page.getByText('Settings refresh failed. Retained settings are stale.'),
    ).toBeVisible();
    await expect(page.locator('[data-settings-read]')).toContainText('blueprints: [unclosed');
    await expect(form.locator('[data-part="jitter_px"]')).toHaveValue('7');
    await expect(
      form.getByRole('button', { name: 'Continue from the saved values as read' }),
    ).toHaveCount(0);
    await page
      .locator('[data-region="runtimeSettings"]')
      .screenshot({ path: path.join(SCREENS, `runtime-r1-stale-settings-${width}.png`) });
    failedRead = false;
    await page.getByRole('button', { name: 'Read settings again' }).click();
    await form.getByRole('button', { name: 'Continue from the saved values as read' }).click();
    await expect(form.getByRole('button', { name: 'Save', exact: true })).not.toHaveAttribute(
      'aria-disabled',
    );
    await expect(form).toContainText('earlier outcome remains unknown');
    expect(attempts).toHaveLength(1);
    await layout(page, width);
  });
}
