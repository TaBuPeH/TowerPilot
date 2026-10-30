import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { SRC } from '../support/paths.ts';

/**
 * Synthetic markup, source CSS: the HelpTip icon and tip as src/ui/HelpTip.tsx draws them
 * (placement is unit-tested; this checks what only a browser can: hit boxes, footprint, wrapping).
 */
const LONG = 'Background that explains how a setting works and why it is so. '.repeat(40);

async function page_(page: Page) {
  await page.setContent(`<!doctype html><html lang="en"><head><title>Help tip</title></head><body>
    <p id="row" style="width: 14rem; margin: 2rem"><span id="word">Run speed</span><button class="tp-help__trigger" type="button" aria-label="About Run speed"><svg class="tp-help__glyph" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.6" /><line x1="8" y1="7.2" x2="8" y2="11.4" /><circle cx="8" cy="4.9" r="0.6" class="tp-help__dot" /></svg></button></p>
    <div id="tip" class="tp-help__tip" role="tooltip" style="left: 8px; top: 8px">${LONG}</div>
  </body></html>`);
  for (const name of ['tokens.css', 'base.css', 'helpTip.css']) {
    await page.addStyleTag({ path: path.join(SRC, 'ui', name) });
  }
}

/** The icon's border box and the box it takes in the line (border box plus margins). */
async function iconBoxes(page: Page) {
  return page.locator('.tp-help__trigger').evaluate((button) => {
    const box = button.getBoundingClientRect();
    const style = getComputedStyle(button);
    const glyph = button.querySelector('svg')?.getBoundingClientRect();
    const margin = (side: string) => Number.parseFloat(style.getPropertyValue(`margin-${side}`));
    return {
      width: box.width,
      height: box.height,
      glyph: glyph === undefined ? null : [glyph.width, glyph.height],
      footprintWidth: box.width + margin('left') + margin('right'),
      footprintHeight: box.height + margin('top') + margin('bottom'),
    };
  });
}

for (const scenario of [
  { name: 'fine pointer', hasTouch: false, side: 24 },
  { name: 'touch', hasTouch: true, side: 44 },
]) {
  test.describe(scenario.name, () => {
    test.use({ viewport: { width: 1280, height: 800 }, hasTouch: scenario.hasTouch });

    test('adjacent help buttons have separate hit areas', async ({ page }) => {
      await page_(page);
      await page.locator('#row').evaluate((row) => {
        const trigger = row.querySelector('button');
        if (trigger === null) throw new Error('Missing help button');
        row.append(trigger.cloneNode(true));
      });
      const boxes = await page.locator('.tp-help__trigger').evaluateAll((buttons) =>
        buttons.map((button) => {
          const box = button.getBoundingClientRect();
          return { left: box.left, right: box.right, top: box.top, bottom: box.bottom };
        }),
      );
      expect(boxes).toHaveLength(2);
      const [first, second] = boxes;
      if (first === undefined || second === undefined) throw new Error('Missing help buttons');
      expect(first.right <= second.left || first.bottom <= second.top).toBe(true);
    });

    test(`the icon reserves its ${String(scenario.side)}px hit box with a 14px glyph`, async ({
      page,
    }) => {
      await page_(page);
      const boxes = await iconBoxes(page);
      expect(boxes.width).toBe(scenario.side);
      expect(boxes.height).toBe(scenario.side);
      expect(boxes.glyph).toEqual([14, 14]);
      expect(boxes.footprintHeight).toBe(scenario.side);
      // The full target plus the 4px gap, without overlap with neighbouring controls.
      expect(boxes.footprintWidth).toBe(scenario.side + 4);
      const line = await page.locator('#row').evaluate((row) => row.getBoundingClientRect().height);
      expect(line).toBeGreaterThanOrEqual(scenario.side);
    });
  });
}

test.describe('the tip on a small screen', () => {
  test.use({ viewport: { width: 320, height: 480 }, hasTouch: true });

  test('wraps inside the viewport and scrolls when taller than it', async ({ page }) => {
    await page_(page);
    const tip = await page.locator('#tip').evaluate((element) => {
      const box = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return {
        left: box.left,
        right: box.right,
        top: box.top,
        bottom: box.bottom,
        position: style.position,
        zIndex: style.zIndex,
        scrolls: element.scrollHeight > element.clientHeight,
        overflowY: style.overflowY,
      };
    });
    expect(tip.position).toBe('fixed');
    expect(Number(tip.zIndex)).toBeGreaterThan(10);
    expect(tip.left).toBeGreaterThanOrEqual(0);
    expect(tip.right).toBeLessThanOrEqual(320 - 8);
    expect(tip.bottom).toBeLessThanOrEqual(480);
    expect(tip.scrolls).toBe(true);
    expect(tip.overflowY).toBe('auto');
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
  });
});
