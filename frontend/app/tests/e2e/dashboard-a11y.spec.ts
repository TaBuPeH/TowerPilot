import { expect, test, type Locator, type Page } from '@playwright/test';
import { loadFixture } from '../../scripts/contract-fixture.mjs';
import { axeCounts, bodiesTo, openScenario, posts } from './helpers/helpers.ts';

/**
 * Spec 8 in a real browser: axe in the main scenarios at 1360, 1054 and 390
 * (8.10); one live region (8.2); focus visible on every revocation (8.1);
 * tab order follows the region order with the revocations first (8.3); target
 * sizes and link spacing (8.4); dialog focus (8.7); focus and open
 * disclosures survive counted polls (8.8); reflow at 320 and long server
 * text at 390 (8.9); Dismiss by keyboard never drops focus to the body (8.11).
 */
const WIDTHS = [1360, 1054, 390] as const;

for (const scenario of [
  'sketch',
  'idle',
  'cold_502',
  'active_battle_routines',
  'setup_guard_block',
]) {
  for (const width of WIDTHS) {
    test(`axe is clean: ${scenario} at ${String(width)}`, async ({ page }) => {
      await openScenario(page, scenario, { width, waitForRead: scenario !== 'cold_502' });
      await page.waitForTimeout(500);
      expect(await axeCounts(page)).toEqual([]);
      await expect(page.locator('[aria-live]')).toHaveCount(1);
      await expect(page.locator('[role="alert"]')).toHaveCount(0);
    });
  }
}

test('axe is clean with a render fault in every Control slot', async ({ page }) => {
  await openScenario(page, 'collecting', { faults: ['slot:automation', 'slot:input', 'slot:gem'] });
  expect(await axeCounts(page)).toEqual([]);
});

/** Region rank of the focused element in the spec 8.3 order. */
const RANK = [
  'skip',
  'navigation',
  'header',
  'automation',
  'input',
  'gem',
  'outcomes',
  'needsYou',
  'thisBattle',
  'startNext',
  'recent',
  // The screen holds view-only controls: its own column, last in tab order.
  'preview',
] as const;

async function focusOrder(
  page: Page,
  stops: number,
  afterTab?: () => Promise<void>,
): Promise<string[]> {
  const order: string[] = [];
  for (let n = 0; n < stops; n += 1) {
    await page.keyboard.press('Tab');
    await afterTab?.();
    const where = await page.evaluate(() => {
      const active = document.activeElement;
      if (active === null || active === document.body) return 'body';
      if (active.classList.contains('tp-skip-link')) return 'skip';
      if (active.closest('.tp-app-navigation') !== null) return 'navigation';
      if (active.closest('header') !== null) return 'header';
      const slotId = active.closest('[data-slot]')?.getAttribute('data-slot');
      if (slotId !== undefined && slotId !== null) return slotId;
      if (active.closest('.tp-outcomes') !== null) return 'outcomes';
      return active.closest('[data-region]')?.getAttribute('data-region') ?? 'other';
    });
    if (where === 'body') break;
    order.push(where);
  }
  return order;
}

for (const [scenario, faults] of [
  ['sketch', []],
  ['idle', []],
  ['collecting', ['slot:gem']],
] as const) {
  for (const width of [1360, 390]) {
    for (const heldConfig of [false, true]) {
      test(`8.3: tab order follows the region order, revocations first (${scenario}${faults.length > 0 ? ', gem fallback' : ''} at ${String(width)})${heldConfig ? ' with config arriving on focused Choose emulator' : ''}`, async ({
        page,
      }) => {
        let releaseConfig: (() => void) | undefined;
        let configHeld = false;
        const replacementFocused: boolean[] = [];
        if (heldConfig) {
          const released = new Promise<void>((resolve) => {
            releaseConfig = resolve;
          });
          // Control only the first response boundary; keep its fixture and all polling unchanged.
          await page.route('**/api/config', async (route) => {
            if (configHeld) {
              await route.continue();
              return;
            }
            configHeld = true;
            const response = await route.fetch();
            await released;
            await route.fulfill({ response });
          });
        }
        try {
          await openScenario(page, scenario, { width, faults: [...faults] });
          const order = await focusOrder(
            page,
            80,
            heldConfig
              ? async () => {
                  if (replacementFocused.length > 0) return;
                  const choose = page.getByRole('link', { name: 'Choose emulator', exact: true });
                  if (await choose.evaluate((element) => element === document.activeElement)) {
                    releaseConfig?.();
                    await expect(choose).toHaveCount(0);
                    replacementFocused.push(
                      await page
                        .getByRole('button', { name: 'Reconnect', exact: true })
                        .evaluate((element) => element === document.activeElement),
                    );
                    expect(await posts(page)).toEqual([]);
                  }
                }
              : undefined,
          );
          expect(order[0]).toBe('skip');
          const ranks = order.map((where) => RANK.indexOf(where as (typeof RANK)[number]));
          expect(
            ranks.every((rank) => rank >= 0),
            order.join(' '),
          ).toBe(true);
          for (let n = 1; n < ranks.length; n += 1) {
            expect(ranks[n], `${order[n - 1] ?? ''} -> ${order[n] ?? ''}`).toBeGreaterThanOrEqual(
              ranks[n - 1] ?? 0,
            );
          }
          // The first stop inside main is a Control slot's revocation, never a region link.
          const firstMain = order.find(
            (where) => where !== 'skip' && where !== 'navigation' && where !== 'header',
          );
          expect(['automation', 'input', 'gem']).toContain(firstMain);
          if (heldConfig) {
            expect(replacementFocused).toEqual([true]);
            expect(await posts(page)).toEqual([]);
          }
        } finally {
          releaseConfig?.();
        }
      });
    }
  }
}

async function focusRingChanges(page: Page, target: Locator): Promise<boolean> {
  await target.scrollIntoViewIfNeeded();
  const box = await target.boundingBox();
  if (box === null) throw new Error('target has no box');
  const clip = {
    x: Math.max(0, box.x - 6),
    y: Math.max(0, box.y - 6),
    width: box.width + 12,
    height: box.height + 12,
  };
  await page.evaluate(() => {
    (document.activeElement as HTMLElement | null)?.blur();
  });
  const before = await page.screenshot({ clip });
  await page.keyboard.press('Shift');
  await target.focus();
  const after = await page.screenshot({ clip });
  return !before.equals(after);
}

for (const width of WIDTHS) {
  test(`8.1: Stop, Turn input off and Pause show a visible focus ring at ${String(width)}`, async ({
    page,
  }) => {
    await openScenario(page, 'e0_collecting', { width });
    for (const target of [
      page.locator('[data-slot="automation"]').getByRole('button', { name: /^Stop/u }),
      page.locator('[data-slot="input"]').getByRole('button', { name: 'Turn input off' }),
      page.locator('[data-slot="gem"]').getByRole('button', { name: 'Pause harvesting' }),
    ]) {
      expect(await focusRingChanges(page, target)).toBe(true);
    }
  });

  test(`8.4: targets at ${String(width)}: buttons, pickers, disclosures and links (F4-06) at least 24 px; halt and primary 44 px at 390; inline links 8 px apart and never taller than their line`, async ({
    page,
  }) => {
    for (const scenario of ['sketch', 'active_battle_routines']) {
      await openScenario(page, scenario, { width });
      const boxes = await page.evaluate(() =>
        [...document.querySelectorAll<HTMLElement>('button, a[href], select, summary')]
          .filter((element) =>
            element.checkVisibility({ visibilityProperty: true, opacityProperty: true }),
          )
          .map((element) => {
            const box = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            const parent =
              element.parentElement === null ? null : getComputedStyle(element.parentElement);
            return {
              display: style.display,
              marginBox: box.height + parseFloat(style.marginTop) + parseFloat(style.marginBottom),
              lineHeight: parent === null ? 0 : parseFloat(parent.lineHeight),
              tag: element.tagName.toLowerCase(),
              tier: element.dataset['tier'] ?? null,
              label: element.textContent.trim().slice(0, 40),
              parent: element.parentElement === null ? '' : element.parentElement.className,
              x: box.x,
              y: box.y,
              width: box.width,
              height: box.height,
            };
          }),
      );
      // The skip link is the one exception: it is off screen until focused (F-007).
      for (const box of boxes.filter((entry) => entry.y >= 0)) {
        expect(box.height, `${scenario} ${box.label}`).toBeGreaterThanOrEqual(24);
        expect(box.width, `${scenario} ${box.label}`).toBeGreaterThanOrEqual(24);
      }
      // F4-06: a link on a text line takes no more block space than the line it sits on.
      for (const box of boxes.filter(
        (entry) => entry.tag === 'a' && entry.display === 'inline-block',
      )) {
        if (Number.isNaN(box.lineHeight)) continue;
        expect(box.marginBox, `${scenario} ${box.label}`).toBeLessThanOrEqual(box.lineHeight + 0.5);
      }
      if (width === 390) {
        for (const box of boxes.filter(
          (entry) => entry.tier === 'halt' || entry.tier === 'primary',
        )) {
          expect(box.width, box.label).toBeGreaterThanOrEqual(44);
          expect(box.height, box.label).toBeGreaterThanOrEqual(44);
        }
      }
      const links = boxes.filter((entry) => entry.tag === 'a');
      for (const a of links) {
        for (const b of links) {
          if (a === b || Math.abs(a.y - b.y) > 2 || b.x <= a.x) continue;
          const gap = b.x - (a.x + a.width);
          if (gap >= 0 && gap < 40)
            expect(gap, `${a.label} | ${b.label}`).toBeGreaterThanOrEqual(8);
        }
      }
    }
  });

  test(`8.7: the Stop dialog at ${String(width)}: Cancel focused, Tab stays inside, Escape closes with no POST, focus returns to Stop`, async ({
    page,
  }) => {
    await openScenario(page, 'e0_collecting', { width });
    const stop = page.locator('[data-slot="automation"]').getByRole('button', { name: /^Stop/u });
    await stop.click();
    const dialog = page.locator('dialog[open]');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
    for (const key of ['Tab', 'Tab', 'Shift+Tab', 'Shift+Tab', 'Shift+Tab']) {
      await page.keyboard.press(key);
      const inside = await page.evaluate(() => {
        const active = document.activeElement;
        return active === document.body || active?.closest('dialog[open]') !== null;
      });
      expect(inside, key).toBe(true);
    }
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(stop).toBeFocused();
    await page.waitForTimeout(300);
    expect(
      (await bodiesTo(page, '/api/operations/00000003-0000-4000-8000-000000000000/cancel')).length,
    ).toBe(0);
  });
}

test('8.8: with focus on Turn input off and a help tip open, five counted status deliveries keep both', async ({
  page,
}) => {
  let deliveries = 0;
  page.on('response', (response) => {
    if (response.url().endsWith('/api/status')) deliveries += 1;
  });
  await openScenario(page, 'collecting');
  const input = page.locator('[data-slot="input"]');
  const why = input.getByRole('button', { name: /^About / }).first();
  // Hover opens it without moving focus; the pointer stays on the icon while focus sits on Turn off.
  await why.hover();
  await expect(why).toHaveAttribute('aria-expanded', 'true');
  const turnOff = input.getByRole('button', { name: 'Turn input off' });
  await turnOff.focus();
  const start = deliveries;
  await expect.poll(() => deliveries - start, { timeout: 20_000 }).toBeGreaterThanOrEqual(5);
  await expect(turnOff).toBeFocused();
  await expect(why).toHaveAttribute('aria-expanded', 'true');
});

test('8.11: Dismiss by keyboard moves focus to the next line or the Control heading, never the body', async ({
  page,
}) => {
  await openScenario(page, 'dropped_writes');
  await page.locator('[data-slot="gem"]').getByRole('button', { name: 'Pause harvesting' }).click();
  const dismiss = page
    .locator('.tp-outcomes')
    .getByRole('button', { name: /^Dismiss/u })
    .first();
  await expect(dismiss).toBeVisible();
  await dismiss.focus();
  await page.keyboard.press('Enter');
  const where = await page.evaluate(() => {
    const active = document.activeElement;
    if (active === null || active === document.body) return 'body';
    return active.id === 'control-title' || active.closest('[data-region="control"]') !== null
      ? 'control'
      : 'elsewhere';
  });
  expect(where).toBe('control');
});

test('8.9: no horizontal scroll at 320 px', async ({ page }) => {
  await openScenario(page, 'sketch', { width: 320 });
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});

test('8.9: a failed turn-off with its server error stays inside its outcome line at 390, shown in full', async ({
  page,
}) => {
  await openScenario(page, 'marker_failed', { width: 390 });
  await page.locator('[data-slot="input"]').getByRole('button', { name: 'Turn input off' }).click();
  const server = page.locator('.tp-outcomes [data-server-text="message"]').first();
  const fixture = loadFixture() as unknown as {
    readonly automation_access: Record<string, { readonly body: { readonly error: string } }>;
  };
  const error = fixture.automation_access['disable_marker_write_failed']?.body.error ?? '';
  expect(error.length).toBeGreaterThan(0);
  await expect(server).toHaveText(error);
  const overflow = await page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>('.tp-outcomes li, .tp-outcomes .tp-notice')].map(
      (element) => element.scrollWidth - element.clientWidth,
    ),
  );
  for (const value of overflow) expect(value).toBeLessThanOrEqual(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBeLessThanOrEqual(0);
});
