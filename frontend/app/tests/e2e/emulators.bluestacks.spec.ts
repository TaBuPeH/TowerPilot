import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { axeIds, posts } from './helpers/helpers.ts';
import { blueStacksCase, stoppedBlueStacks } from '../support/emulatorsFixtures.ts';

const OUT = path.resolve('../../backend/logs/reviews/frontend-f6-screens');
mkdirSync(OUT, { recursive: true });

async function expectAxeClean(page: Page) {
  const violations = await axeIds(page);
  expect(violations).toEqual([]);
}

for (const width of [1360, 1054, 390]) {
  test(`M4 modified all-stopped BlueStacks at ${String(width)}: browser consent discloses host-file effects; Cancel sends nothing and Prepare sends the recorded target once`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    // modified: no recorded case has every instance stopped (before Prepare is needed).
    // The user authorized this same labelled unit fixture in the browser; responses stay verbatim r11.
    let prepared = false;
    await page.route('**/api/wizard/emulators', async (route) => {
      if (prepared) await route.continue();
      else {
        const scan = stoppedBlueStacks();
        await route.fulfill({ status: scan.status, json: scan.body });
      }
    });
    await page.goto('/__fake/use?scenario=emulators_bluestacks_prepare_modified');
    await page.evaluate(() => {
      window.location.hash = '#emulators';
    });
    const stopped = page.locator('[data-row="BlueStacks:Pie64"]');
    const prepare = stopped.getByRole('button', { name: 'Prepare', exact: true });
    await prepare.click();
    const dialog = page.getByRole('dialog');
    for (const effect of [
      'bluestacks.conf',
      'Android Debug Bridge on for every BlueStacks instance',
      "selected instance's display 2560 x 1080 landscape at 360 dpi",
      'custom resolution selected',
      'A backup of the file is kept',
      'Every BlueStacks window must be closed first',
    ])
      await expect(dialog).toContainText(effect);
    await expectAxeClean(page);
    await expect(dialog.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused();
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(prepare).toBeFocused();
    expect(await posts(page)).toEqual([]);
    await prepare.click();
    prepared = true;
    await dialog.getByRole('button', { name: 'Write bluestacks.conf', exact: true }).click();
    const outcome = stopped.locator('[data-kind="prepare"]');
    await expect(outcome).toHaveAttribute('data-outcome', 'done');
    const answer = blueStacksCase('prepare_the_stopped_instance_once_nothing_runs');
    await expect(outcome).toContainText(answer.body.message);
    await expect(outcome).toContainText(answer.body.backup);
    expect(await posts(page)).toEqual([
      expect.objectContaining({
        path: answer.request.path,
        body: JSON.stringify(answer.request.body),
      }),
    ]);
    await page.getByRole('button', { name: 'Scan again', exact: true }).click();
    await expect(page.locator('[data-bluestacks-adb]')).toHaveText(
      'BlueStacks ADB: on for every instance',
    );
    await expect(stopped.getByRole('button', { name: 'Prepare', exact: true })).toHaveCount(0);
  });
}

test('F6M-C-02 modified all-stopped BlueStacks: Prepare consent closes on a recorded runner status and sends nothing', async ({
  page,
}) => {
  // modified: no recorded case has every instance stopped (before Prepare is needed).
  await page.route('**/api/wizard/emulators', (route) => {
    const scan = stoppedBlueStacks();
    return route.fulfill({ status: scan.status, json: scan.body });
  });
  await page.goto('/__fake/use?scenario=emulators_bluestacks');
  await page.evaluate(() => {
    window.location.hash = '#emulators';
  });
  const prepare = page
    .locator('[data-row="BlueStacks:Pie64"]')
    .getByRole('button', { name: 'Prepare', exact: true });
  await prepare.click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.request.get('/__fake/set?scenario=emulators_bluestacks_runner_listed');
  await expect(prepare).toHaveAccessibleDescription(
    'Automation is running; stop it on the Dashboard first',
  );
  await expect(page.getByRole('dialog')).toBeHidden();
  await prepare.click({ force: true });
  expect(await posts(page)).toEqual([]);
});

for (const width of [1360, 1054, 390]) {
  test(`M4 r11 BlueStacks at ${String(width)}: Prepare disabled while any instance runs, one global ADB setting, axe and targets`, async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/__fake/use?scenario=emulators_bluestacks');
    await page.evaluate(() => {
      window.location.hash = '#emulators';
    });
    const detected = page.locator('[data-region="emulatorsDetected"]');
    const stopped = detected.locator('[data-row="BlueStacks:Pie64"]');
    const running = detected.locator('[data-row="BlueStacks:Nougat64"]');
    await expect(stopped).toContainText('off');
    await expect(running).toContainText('running');
    const prepare = stopped.getByRole('button', { name: 'Prepare', exact: true });
    await expect(prepare).toHaveAttribute('aria-disabled', 'true');
    await expect(prepare).toHaveAccessibleDescription('Close every BlueStacks window first');
    await expect(
      detected.getByText('Close every BlueStacks window first', { exact: true }),
    ).toBeVisible();
    await expect(detected.locator('[data-bluestacks-adb]')).toHaveCount(1);
    await expect(detected.locator('[data-bluestacks-adb]')).toHaveText(
      'BlueStacks ADB: off for every instance',
    );
    await expect(running.getByRole('button', { name: 'Prepare', exact: true })).toHaveCount(0);
    await prepare.focus();
    await page.keyboard.press('Enter');
    await prepare.click({ force: true });
    await expect(page.getByRole('dialog')).toBeHidden();
    await expectAxeClean(page);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      ),
    ).toBeLessThanOrEqual(0);
    const small = await page
      .locator('main button, main input, main select, main summary')
      .evaluateAll(
        (elements, mobile) =>
          elements
            .filter((element) => element.getClientRects().length > 0)
            .filter((element) => {
              const box = element.getBoundingClientRect();
              return (
                box.width < 24 || box.height < (mobile && element.tagName === 'BUTTON' ? 44 : 24)
              );
            })
            .map((element) => element.outerHTML.slice(0, 100)),
        width === 390,
      );
    expect(small).toEqual([]);
    await page.screenshot({
      path: path.join(OUT, `emulators-bluestacks-${String(width)}.png`),
      fullPage: true,
    });
    expect(await posts(page)).toEqual([]);
    expect(errors).toEqual([]);
  });
}
