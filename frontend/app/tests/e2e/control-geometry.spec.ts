import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { SRC } from '../support/paths.ts';
import { undersizedControlTargets } from './helpers/target-box.ts';

/** Synthetic controls only: source CSS, no app server, backend or game commands. */
async function controls(page: Page) {
  await page.setContent(`<!doctype html><html lang="en"><head><title>Control geometry</title></head><body>
    <div class="tp-control-row" id="controls">
      <button class="tp-button" data-tier="primary"><span>Start</span></button>
      <button class="tp-button" data-tier="secondary"><span>Save</span></button>
      <button class="tp-button" data-tier="halt"><svg class="tp-button__glyph" viewBox="0 0 10 10" aria-hidden="true"><rect width="8" height="8" /></svg><span>Stop</span></button>
      <button class="tp-button" data-tier="quiet"><span>Cancel</span></button>
      <button class="tp-button" aria-pressed="true"><span>Selected</span></button>
      <button class="tp-button" data-tier="secondary" aria-disabled="true"><span>Unavailable</span></button>
      <a class="tp-button" data-tier="quiet" href="#destination"><span>Details</span></a>
      <input class="tp-field-control" aria-label="Name" value="Example" />
      <select class="tp-field-control" aria-label="Tier"><option>Tier 1</option></select>
    </div>
  </body></html>`);
  for (const name of ['tokens.css', 'base.css', 'ui.css']) {
    await page.addStyleTag({ path: path.join(SRC, 'ui', name) });
  }
}

for (const scenario of [
  { name: 'desktop', width: 1440, hasTouch: false, height: 32 },
  { name: 'narrow', width: 390, hasTouch: false, height: 44 },
  { name: 'touch desktop', width: 1440, hasTouch: true, height: 44 },
]) {
  test.describe(scenario.name, () => {
    test.use({ viewport: { width: scenario.width, height: 900 }, hasTouch: scenario.hasTouch });

    test('buttons, fields and link-buttons share height and row alignment', async ({ page }) => {
      await controls(page);
      const geometry = await page.locator('#controls > *').evaluateAll((elements) =>
        elements.map((element) => {
          const box = element.getBoundingClientRect();
          const style = getComputedStyle(element);
          return {
            name: element.getAttribute('aria-label') ?? element.textContent,
            top: box.top,
            height: box.height,
            fontSize: style.fontSize,
            marginTop: style.marginTop,
            marginBottom: style.marginBottom,
            overflow: element.scrollWidth > element.clientWidth,
          };
        }),
      );
      expect(geometry).toHaveLength(9);
      for (const control of geometry) {
        expect(control.height, `${control.name} height`).toBe(scenario.height);
        expect(control.marginTop, `${control.name} top margin`).toBe('0px');
        expect(control.marginBottom, `${control.name} bottom margin`).toBe('0px');
        expect(control.overflow, `${control.name} overflow`).toBe(false);
      }
      // Native selects retain platform line-height; typography and outer geometry agree.
      expect(new Set(geometry.map((control) => control.fontSize)).size).toBe(1);
      if (scenario.width === 1440) {
        expect(new Set(geometry.map((control) => control.top)).size).toBe(1);
        const labelTops = await page
          .locator('.tp-button > span')
          .evaluateAll((labels) => labels.map((label) => label.getBoundingClientRect().top));
        expect(new Set(labelTops).size).toBe(1);
      }
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
    });

    test('disclosures, wrapping labels and navigation links have operable targets', async ({
      page,
    }) => {
      await controls(page);
      await page.locator('body').evaluate((body) => {
        body.insertAdjacentHTML(
          'beforeend',
          `<main class="tp-main">
          <details><summary>Evidence</summary><p>Full evidence</p></details>
          <details class="tp-disclosure"><summary>Details</summary><p>Background</p></details>
          <details class="tp-copy-line__why"><summary>Why</summary><p>Explanation</p></details>
          <label class="tp-check-field"><input type="checkbox" />Include</label>
          <a href="#details">Edit</a>
        </main>`,
        );
      });
      const minimum = scenario.height === 44 ? 44 : 24;
      expect(
        await undersizedControlTargets(page.locator('main'), { width: minimum, height: minimum }),
      ).toEqual([]);
      if (minimum === 44) {
        const link = await page.getByRole('link', { name: 'Edit', exact: true }).boundingBox();
        expect(link?.width).toBeGreaterThanOrEqual(minimum);
        expect(link?.height).toBeGreaterThanOrEqual(minimum);
      }
    });
  });
}

test('long action labels and enlarged text grow without clipping', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await controls(page);
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '200%';
    const button = document.querySelector<HTMLButtonElement>('.tp-button[data-tier="halt"]');
    const label = button?.querySelector('span');
    if (button === null || label === undefined || label === null) {
      throw new Error('Missing synthetic halt control');
    }
    button.style.width = '8rem';
    label.textContent = 'Stop the selected automation after this run';
  });
  const geometry = await page.locator('.tp-button[data-tier="halt"]').evaluate((button) => {
    const label = button.querySelector('span');
    if (label === null) throw new Error('Missing action label');
    const box = button.getBoundingClientRect();
    const text = label.getBoundingClientRect();
    return {
      height: box.height,
      labelFits:
        text.left >= box.left &&
        text.right <= box.right &&
        text.top >= box.top &&
        text.bottom <= box.bottom,
      widthFits: button.scrollWidth <= button.clientWidth,
      heightFits: button.scrollHeight <= button.clientHeight,
    };
  });
  expect(geometry.height).toBeGreaterThan(88);
  expect(geometry.labelFits).toBe(true);
  expect(geometry.widthFits).toBe(true);
  expect(geometry.heightFits).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});
