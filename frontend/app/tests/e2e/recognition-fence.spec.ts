import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { axeViolations, bodiesTo, openScenario } from './helpers/helpers.ts';

const OUT = path.resolve('../../backend/logs/reviews/frontend-f6-screens');
mkdirSync(OUT, { recursive: true });

async function accessibility(page: Page, width: number) {
  const violations = await axeViolations(page);
  expect(violations).toEqual([]);
  const faults = await page.evaluate((mobile) => {
    const controls = [
      ...document.querySelectorAll<HTMLElement>(
        'main button, main input, main select, main summary',
      ),
    ]
      .filter((element) => element.getClientRects().length > 0)
      .filter((element) => !element.closest('[data-region="recognitionAdvanced"]'));
    return {
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      small: controls
        .filter((element) => {
          const box = element.getBoundingClientRect();
          return (
            box.width < 24 ||
            box.height < 24 ||
            (mobile && element.tagName === 'BUTTON' && box.height < 44)
          );
        })
        .map((element) => element.outerHTML),
    };
  }, width === 390);
  expect(faults).toEqual({ overflow: 0, small: [] });
}

for (const width of [1360, 1054, 390]) {
  for (const refused of [true, false]) {
    test(`R-FENCE ${refused ? 'apply_saved_composed_for_alpha_is_refused_after_the_switch' : 'apply_saved_with_the_matching_context_is_applied'} at ${String(width)}`, async ({
      page,
    }) => {
      const errors = await openScenario(
        page,
        refused ? 'recognition_fence_saved_changed' : 'recognition_fence_saved_matching',
        { width },
      );
      await page.evaluate(() => {
        window.location.hash = '#recognition';
      });
      const region = page.locator('[data-region="recognitionOperation"]');
      const apply = region.getByRole('button', { name: 'Apply saved observations', exact: true });
      await apply.click();
      await page.getByRole('dialog').getByRole('button', { name: 'Apply', exact: true }).click();
      const outcome = region.locator('[data-kind="calibrate_apply"]');
      await expect(outcome).toHaveAttribute(
        'data-outcome',
        refused ? 'apply_context_changed' : 'applied',
      );
      if (refused) {
        await expect(outcome).toContainText(
          'Not applied: the emulator, account or profile changed',
        );
        await expect(outcome.locator('[data-current-context]')).toContainText('beta');
        await expect(outcome.locator('[data-current-context]')).toContainText('theirs');
        await expect(
          outcome.getByRole('button', { name: 'Send the same request again' }),
        ).toHaveCount(0);
      } else await expect(outcome).toContainText('Applied to this profile');
      await page.waitForTimeout(1_500);
      expect(await bodiesTo(page, '/api/calibrate/apply')).toEqual([
        {
          saved_observations: true,
          expected_context: {
            instance: 'main',
            account: 'alpha',
            profile: 'mine',
            serial: 'fake:1',
          },
        },
      ]);
      await accessibility(page, width);
      await outcome.screenshot({
        path: path.join(
          OUT,
          `recognition-fence-${refused ? 'refused' : 'matching'}-${String(width)}.png`,
        ),
      });
      expect(errors).toEqual([]);
    });
  }
}
