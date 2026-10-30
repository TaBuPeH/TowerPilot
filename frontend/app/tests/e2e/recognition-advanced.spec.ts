import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { axeIds, bodiesTo, posts, WCAG_AA_TAGS } from './helpers/helpers.ts';
const OUT = process.env['TP_F6_SCREENS_DIR'] ?? path.resolve('test-results', 'f6-screens');
mkdirSync(OUT, { recursive: true });
const widths = [1360, 1054, 390] as const;
const analysis = (page: Page) => page.locator('[data-region="recognition-analysis"]');
const crop = (page: Page) => page.locator('[data-region="recognition-crop"]');
async function open(page: Page, scenario: string, width = 1360) {
  const errors: string[] = [];
  page.on('pageerror', (e) => {
    errors.push(e.message);
  });
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`/__fake/use?scenario=${scenario}`);
  await page.evaluate(() => {
    location.hash = '#recognition';
  });
  await expect(
    page.getByRole('heading', { name: 'Recognition', exact: true, level: 1 }),
  ).toBeVisible();
  await expect(page.locator('[data-region="recognitionAdvanced"]')).not.toContainText(
    '{"instance"',
  );
  await expect(crop(page).getByRole('button', { name: 'Take a frame' })).not.toHaveAttribute(
    'aria-disabled',
    'true',
  );
  return errors;
}
async function capture(page: Page) {
  await crop(page).getByRole('button', { name: 'Take a frame', exact: true }).click();
  const img = crop(page).getByRole('img', { name: 'Captured still frame for a native-pixel crop' });
  await expect(img).toBeVisible();
  await expect
    .poll(() => img.evaluate((el) => (el instanceof HTMLImageElement ? el.naturalWidth : 0)))
    .toBe(1080);
  await crop(page).getByLabel('Template target name').fill('buttons/retry.png');
  await crop(page).getByLabel('x (native pixels)', { exact: true }).fill('100');
  await crop(page).getByLabel('y (native pixels)', { exact: true }).fill('200');
  await crop(page).getByLabel('width (native pixels)', { exact: true }).fill('300');
  await crop(page).getByLabel('height (native pixels)', { exact: true }).fill('90');
  return img;
}
async function selectAnalysis(page: Page, id: string) {
  await analysis(page).getByLabel('Analysis operation ID').fill(id);
  await analysis(page).getByRole('button', { name: 'Read analysis status' }).click();
}
for (const width of widths) {
  test(`R9 R10 R11 SR-8 advanced repairs at ${width}: native crop, keyboard, axe, reflow and targets`, async ({
    page,
  }) => {
    const errors = await open(page, 'recognition', width);
    expect(await posts(page)).toEqual([]);
    const img = await capture(page);
    const viewport = crop(page).getByRole('region', {
      name: 'Captured still frame for a native-pixel crop',
    });
    await viewport.focus();
    await expect(viewport).toBeFocused();
    const scroll = await viewport.evaluate((el) => ({
      top: el.scrollTop,
      max: el.scrollHeight - el.clientHeight,
    }));
    if (scroll.max > 1) {
      await page.keyboard.press(scroll.top < scroll.max ? 'ArrowDown' : 'ArrowUp');
      await expect.poll(() => viewport.evaluate((el) => el.scrollTop)).not.toBe(scroll.top);
    }
    const src = await img.getAttribute('src');
    expect(src).toMatch(/^\/api\/frame\.png\?ts=.+/u);
    await crop(page).getByLabel('x (native pixels)', { exact: true }).focus();
    await page.keyboard.press('ArrowUp');
    await expect(crop(page).getByLabel('x (native pixels)', { exact: true })).toHaveValue('101');
    await page.keyboard.press('ArrowDown');
    const focus = crop(page).getByLabel('Template target name');
    await focus.focus();
    await page.waitForTimeout(2100);
    await expect(focus).toBeFocused();
    await expect(img).toHaveAttribute('src', src ?? '');
    const coverage = page.locator('[data-region="recognition-coverage"]');
    await expect(coverage.getByText('Grouped image library', { exact: true })).toBeVisible();
    expect(
      await coverage
        .getByText('Grouped image library', { exact: true })
        .evaluate((el) => el.parentElement instanceof HTMLDetailsElement && el.parentElement.open),
    ).toBe(false);
    await crop(page).getByRole('button', { name: 'Save crop', exact: true }).click();
    await expect(
      crop(page).getByText(
        'File written. This proves neither provenance nor recognition on this emulator.',
      ),
    ).toBeVisible();
    const saves = await bodiesTo(page, '/api/template/buttons/retry.png');
    expect(saves).toHaveLength(1);
    expect(saves[0]).toEqual({
      ts: new URL(src ?? '', 'http://fixture').searchParams.get('ts'),
      x: 100,
      y: 200,
      w: 300,
      h: 90,
    });
    const violations = await axeIds(page, WCAG_AA_TAGS);
    expect(violations).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    const small = await page
      .locator(
        '.tp-advanced button:visible,.tp-advanced summary:visible,.tp-advanced input:visible',
      )
      .evaluateAll(
        (elements, minButton) =>
          elements.flatMap((el) => {
            const b = el.getBoundingClientRect();
            const min = el.tagName === 'BUTTON' ? minButton : 24;
            return b.width < 24 || b.height < min
              ? [{ tag: el.tagName, width: b.width, height: b.height }]
              : [];
          }),
        width === 390 ? 44 : 24,
      );
    expect(small).toEqual([]);
    await page.screenshot({
      path: path.join(OUT, `recognition-advanced-crop-${width}.png`),
      fullPage: true,
    });
    expect(errors).toEqual([]);
  });
  test(`R9 SR-1 analysis is bound to selected operation at ${width}`, async ({ page }) => {
    await open(page, 'recognition_analysis', width);
    const id = '000000f8-0000-4000-8000-000000000000';
    await analysis(page).getByRole('button', { name: 'Read analysis status' }).click();
    const img = analysis(page).getByRole('img', {
      name: 'Still snapshot of this analysis operation',
    });
    await expect(img).toBeVisible();
    await expect(img).toHaveAttribute('src', new RegExp(`operation_id=${id}`));
    await expect(analysis(page).getByText('Ambiguous', { exact: true })).toBeVisible();
    await expect(
      analysis(page).getByText('No manifest role reported', { exact: true }),
    ).toBeVisible();
    await page.screenshot({
      path: path.join(OUT, `recognition-advanced-analysis-${width}.png`),
      fullPage: true,
    });
    await analysis(page).getByLabel('Analysis operation ID').fill('another-operation');
    await expect(img).toHaveCount(0);
    await analysis(page).getByRole('button', { name: 'Read analysis status' }).click();
    await expect(analysis(page).getByText('Unavailable', { exact: true })).toBeVisible();
    await expect(analysis(page).getByText('home', { exact: true })).toHaveCount(0);
    await expect(analysis(page).getByText('212', { exact: true })).toHaveCount(0);
    await expect(analysis(page).getByText('1799999900', { exact: true })).toHaveCount(0);
    await expect(analysis(page).getByText('Candidate screens', { exact: true })).toHaveCount(0);
    expect(await posts(page)).toEqual([]);
  });
}
test('R9 F6R10-R1-02 a failed read after success withdraws the complete operation result', async ({
  page,
}) => {
  await open(page, 'recognition_analysis');
  await analysis(page).getByRole('button', { name: 'Read analysis status' }).click();
  await expect(analysis(page).getByText('1799999900', { exact: true })).toBeVisible();
  await expect(analysis(page).getByRole('img')).toHaveCount(1);
  await page.route('**/api/screen-analysis/status*', (route) => route.abort('failed'));
  await analysis(page).getByRole('button', { name: 'Read analysis status' }).click();
  await expect(analysis(page).getByText('Unavailable', { exact: true })).toBeVisible();
  await expect(analysis(page).getByRole('img')).toHaveCount(0);
  await expect(analysis(page).getByText('home', { exact: true })).toHaveCount(0);
  await expect(analysis(page).getByText('212', { exact: true })).toHaveCount(0);
  await expect(analysis(page).getByText('1799999900', { exact: true })).toHaveCount(0);
  await expect(analysis(page).getByText('Candidate screens', { exact: true })).toHaveCount(0);
  await expect(analysis(page).getByLabel('Analysis operation ID')).toHaveValue(
    '000000f8-0000-4000-8000-000000000000',
  );
  expect(await posts(page)).toEqual([]);
});
test('R11 native pointer drag scales the displayed still and full-size view does not recapture', async ({
  page,
}) => {
  await open(page, 'recognition');
  let frames = 0;
  page.on('request', (r) => {
    if (new URL(r.url()).pathname === '/api/frame.png') frames++;
  });
  const img = await capture(page);
  await img.scrollIntoViewIfNeeded();
  const box = await img.boundingBox();
  if (box === null) throw new Error('No frame geometry');
  await page.mouse.move(box.x + box.width * 0.1, box.y + box.height * 0.1);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.4, box.y + box.height * 0.2);
  await page.mouse.up();
  await expect(crop(page).getByLabel('x (native pixels)', { exact: true })).toHaveValue('108');
  await expect(crop(page).getByLabel('width (native pixels)', { exact: true })).toHaveValue('324');
  await crop(page).getByRole('button', { name: 'Open this still frame at full size' }).click();
  await expect(img).toHaveAttribute('width', '1080');
  expect(frames).toBe(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
test('R11 SR-2 existing target requires a separately named replacement confirmation', async ({
  page,
}) => {
  await open(page, 'recognition_crop_exists');
  await capture(page);
  await crop(page).getByRole('button', { name: 'Save crop', exact: true }).click();
  await expect(crop(page).getByRole('button', { name: 'Replace existing image' })).toBeVisible();
  const opener = crop(page).getByRole('button', { name: 'Replace existing image' });
  await opener.click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('buttons/retry.png');
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();
  expect(await bodiesTo(page, '/api/template/buttons/retry.png')).toHaveLength(1);
  await opener.click();
  await dialog.getByRole('button', { name: 'Replace this file' }).click();
  await expect
    .poll(async () => (await bodiesTo(page, '/api/template/buttons/retry.png')).length)
    .toBe(2);
  const bodies = await bodiesTo(page, '/api/template/buttons/retry.png');
  expect(bodies).toHaveLength(2);
  expect(bodies[0]).not.toHaveProperty('overwrite');
  expect(bodies[1]).toMatchObject({ overwrite: true });
});
test('R11 expired frame refusal is visible with its code and exact draft retained', async ({
  page,
}) => {
  await open(page, 'recognition_crop_refused');
  await capture(page);
  await crop(page).getByRole('button', { name: 'Save crop', exact: true }).click();
  await expect(crop(page)).toContainText('frame_expired');
  await expect(crop(page).getByLabel('x (native pixels)', { exact: true })).toHaveValue('100');
  expect(await bodiesTo(page, '/api/template/buttons/retry.png')).toHaveLength(1);
});
test('R11 failed frame has no saveable token and offers explicit new capture', async ({ page }) => {
  await open(page, 'recognition_frame_failed');
  await crop(page).getByRole('button', { name: 'Take a frame', exact: true }).click();
  await expect(
    crop(page).getByText(
      'The frame could not be loaded. No frame token is available for saving. Take a frame to try a new capture.',
    ),
  ).toBeVisible();
  await expect(crop(page).getByRole('img')).toHaveCount(0);
  expect(await posts(page)).toEqual([]);
});
test('R9 failed analysis keeps server error and offers reads without a relaunch', async ({
  page,
}) => {
  await open(page, 'recognition_analysis_failed');
  await selectAnalysis(page, '000000fe-0000-4000-8000-000000000000');
  await expect(analysis(page)).toContainText('error');
  await expect(analysis(page).getByRole('img')).toHaveCount(0);
  expect(await posts(page)).toEqual([]);
});

test('R9 accepted failed launch follows its own operation and reads never relaunch', async ({
  page,
}) => {
  await open(page, 'recognition_analysis_launch_failed');
  await analysis(page).getByRole('button', { name: 'Analyze this screen' }).click();
  await expect(analysis(page).getByLabel('Analysis operation ID')).toHaveValue(
    '000000f3-0000-4000-8000-000000000000',
  );
  await analysis(page).getByRole('button', { name: 'Read analysis status' }).click();
  await expect(analysis(page)).toContainText('the interpreter could not be started');
  await expect(analysis(page).getByRole('img')).toHaveCount(0);
  expect(await bodiesTo(page, '/api/screen-analysis/start')).toHaveLength(1);
});
for (const scenario of ['recognition_analysis_no_matches', 'recognition_analysis_ambiguous_screen'])
  test(`R9 ${scenario} existing operation stays observational`, async ({ page }) => {
    await open(page, scenario);
    await analysis(page).getByRole('button', { name: 'Read analysis status' }).click();
    await expect(analysis(page).getByText('No matches reported.')).toBeVisible();
    if (scenario.endsWith('ambiguous_screen'))
      await expect(analysis(page)).toContainText('unknown');
    expect(await posts(page)).toEqual([]);
  });

test('R9 SR-6 a lost launch answer stays Unknown after unrelated reads and is never resent', async ({
  page,
}) => {
  await open(page, 'recognition');
  let sends = 0;
  await page.route('**/api/screen-analysis/start', async (route) => {
    sends++;
    await route.fetch();
    await route.abort('failed');
  });
  await analysis(page).getByRole('button', { name: 'Analyze this screen' }).click();
  await expect(
    analysis(page).getByText(
      'Unknown. The answer does not confirm whether the request was done. Nothing is resent.',
    ),
  ).toBeVisible();
  await analysis(page).getByRole('button', { name: 'Read analysis status' }).click();
  await page.waitForTimeout(2100);
  expect(sends).toBe(1);
  await expect(analysis(page).getByRole('button', { name: 'Analyze this screen' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  await expect(analysis(page).getByRole('img')).toHaveCount(0);
});
test('R11 SR-6 a lost crop answer stays Unknown even when the library later contains that filename', async ({
  page,
}) => {
  await open(page, 'recognition');
  await capture(page);
  let sends = 0;
  await page.route('**/api/template/**', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue();
      return;
    }
    sends++;
    await route.fetch();
    await route.abort('failed');
  });
  await crop(page).getByRole('button', { name: 'Save crop', exact: true }).click();
  await expect(
    crop(page).getByText(
      'Unknown. The answer does not confirm whether the request was done. Nothing is resent.',
    ),
  ).toBeVisible();
  await page
    .locator('[data-region="recognition-coverage"]')
    .getByRole('button', { name: 'Read coverage' })
    .click();
  await page.waitForTimeout(2100);
  expect(sends).toBe(1);
  await expect(
    crop(page).getByText(
      'Unknown. The answer does not confirm whether the request was done. Nothing is resent.',
    ),
  ).toBeVisible();
  await expect(crop(page).getByRole('button', { name: 'Take a frame' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
});

for (const width of widths)
  test(`R10 library, coverage and named repair target work at ${width}`, async ({ page }) => {
    await open(page, 'recognition', width);
    const coverage = page.locator('[data-region="recognition-coverage"]');
    await coverage.getByText('Mapping route coverage', { exact: true }).click();
    await expect(coverage).toContainText('Needs local verification');
    await coverage.getByText('Interface coverage', { exact: true }).click();
    await expect(coverage).toContainText('Incomplete');
    await coverage.getByText('Required images', { exact: true }).click();
    const row = coverage.locator('li').filter({ hasText: 'modules/black_hole_digestor.png' });
    await expect(row).toHaveCount(1);
    await row.getByRole('button', { name: 'Use as crop target' }).click();
    await expect(crop(page).getByLabel('Template target name')).toHaveValue(
      'modules/black_hole_digestor.png',
    );
    await expect(page.locator('#recognition-crop-title')).toBeFocused();
    await coverage.getByText('Module naming catalogue', { exact: true }).click();
    await expect(coverage).toContainText('Black Hole Digestor');
    await expect(coverage.locator('li').filter({ hasText: 'Black Hole Digestor' })).toContainText(
      'Not learned locally',
    );
    await coverage.getByText('Grouped image library', { exact: true }).click();
    await coverage.getByText('buttons', { exact: true }).click();
    const image = coverage.getByRole('img', { name: 'Grouped image library' });
    await expect(image).toBeVisible();
    await expect
      .poll(() => image.evaluate((el) => (el instanceof HTMLImageElement ? el.naturalWidth : 0)))
      .toBe(6);
    expect(await image.evaluate((el) => el.getBoundingClientRect().height <= 120)).toBe(true);
    const thumbnail = await image.getAttribute('src');
    await coverage.getByRole('button', { name: 'Read coverage' }).click();
    await expect(image).not.toHaveAttribute('src', thumbnail ?? '');
    await image.scrollIntoViewIfNeeded();
    await expect
      .poll(() => image.evaluate((el) => (el instanceof HTMLImageElement ? el.naturalWidth : 0)))
      .toBe(6);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    expect(await posts(page)).toEqual([]);
  });
