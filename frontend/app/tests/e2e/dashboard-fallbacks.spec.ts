import { expect, test } from '@playwright/test';
import { bodiesTo, openScenario } from './helpers/helpers.ts';

/**
 * S-091 in a real browser (spec 3.5, 9.1 X4 to X6): every part that fails to
 * draw keeps Pause harvesting and Turn input off, rendered and enabled, and
 * never offers Start, Collect or Turn on; the fallback Pause sends the Pause
 * body.
 */
const PARTS: readonly [fault: string, fallback: string][] = [
  ['slot:automation', 'automation'],
  ['slot:input', 'input'],
  ['slot:gem', 'gem'],
  ['outcomes', 'outcomes'],
  ['needsYou', 'needsYou'],
  ['thisBattle', 'thisBattle'],
  ['startNext', 'startNext'],
  ['recent', 'recent'],
  ['topBar', 'topBar'],
];

for (const [fault, part] of PARTS) {
  for (const width of [1360, 390]) {
    test(`S-091 a render fault in ${fault} at ${String(width)} keeps Pause and Turn input off in its fallback`, async ({
      page,
    }) => {
      await openScenario(page, 'collecting', { faults: [fault], width });
      const fallback = page.locator(`[data-fallback="${part}"]`);
      await expect(fallback).toBeVisible();
      for (const name of ['Pause harvesting', 'Turn input off']) {
        const button = fallback.getByRole('button', { name });
        await expect(button).toBeVisible();
        await expect(button).not.toHaveAttribute('aria-disabled', 'true');
      }
      for (const name of ['Start playing', 'Collect gems only', 'Turn on']) {
        await expect(fallback.getByRole('button', { name })).toHaveCount(0);
      }
    });
  }
}

test('S-091 the fallback Pause sends exactly the Pause body once', async ({ page }) => {
  await openScenario(page, 'collecting', { faults: ['slot:gem'] });
  await page
    .locator('[data-fallback="gem"]')
    .getByRole('button', { name: 'Pause harvesting' })
    .click();
  await expect.poll(async () => (await bodiesTo(page, '/api/harvest')).length).toBe(1);
  const [body] = await bodiesTo(page, '/api/harvest');
  expect(body).toMatchObject({
    enabled: false,
    expected_context: { instance: 'main', serial: 'fake:1' },
  });
});
