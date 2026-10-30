import { expect, test, type Locator } from '@playwright/test';
import { openScenario } from './helpers/helpers.ts';

/**
 * F4-02 (product acceptance): vertical density on a short desktop window.
 * The routine pokes stay above the fold at 1360x700 and 1054x700, an empty
 * This battle is one line, a one-item Needs you is one line, and the routine
 * column labels appear once on wide layouts (each cell keeps its own label for
 * screen readers and for the narrow layout). Revocations and guard reasons
 * stay visible.
 */
const SHORT = 700;

async function bottomOf(target: Locator): Promise<number> {
  const box = await target.boundingBox();
  if (box === null) throw new Error('target is not rendered');
  return box.y + box.height;
}

async function topOf(target: Locator): Promise<number> {
  const box = await target.boundingBox();
  if (box === null) throw new Error('target is not rendered');
  return box.y;
}

for (const width of [1360, 1054]) {
  test(`F4-02: at ${String(width)}x${String(SHORT)} the three Run now buttons and every revocation are above the fold`, async ({
    page,
  }) => {
    await openScenario(page, 'active_battle_routines', { width });
    await page.setViewportSize({ width, height: SHORT });
    const runNow = page
      .locator('[data-region="thisBattle"]')
      .getByRole('button', { name: /^Run now/u });
    await expect(runNow).toHaveCount(3);
    // The settled page: a read still arriving under full-suite load can add a transient line
    // above for a moment (this failed once in a full run at 724 px and never in isolation).
    for (let index = 0; index < 3; index += 1) {
      await expect
        .poll(() => bottomOf(runNow.nth(index)), { message: `Run now ${String(index)}` })
        .toBeLessThanOrEqual(SHORT);
    }
    for (const [slot, name] of [
      ['automation', /^Stop/u],
      ['input', 'Turn input off'],
      ['gem', 'Pause harvesting'],
    ] as const) {
      const button = page.locator(`[data-slot="${slot}"]`).getByRole('button', { name });
      await expect(button).toBeVisible();
      await expect(button).not.toHaveAttribute('aria-disabled', 'true');
      expect(await bottomOf(button), slot).toBeLessThanOrEqual(SHORT);
    }
  });

  test(`F4-02: at ${String(width)} an empty This battle is one line beside its title and a one-item Needs you is one line`, async ({
    page,
  }) => {
    await openScenario(page, 'collecting', { width });
    const battle = page.locator('[data-region="thisBattle"]');
    const empty = battle.getByText('No tracked battle');
    await expect(empty).toBeVisible();
    const region = await battle.boundingBox();
    expect(region?.height ?? Infinity).toBeLessThanOrEqual(60);
    expect(
      Math.abs((await topOf(empty)) - (await topOf(battle.locator('h2')))),
    ).toBeLessThanOrEqual(8);
    const needs = page.locator('[data-region="needsYou"] .tp-need');
    await expect(needs).toHaveCount(1);
    const line = needs.locator('.tp-need__line');
    const action = needs.locator('.tp-need__actions a[href]').first();
    await expect(action).toBeVisible();
    // On a wide operations column, the resolution stays beside the issue.
    // Its accessible reading order remains headline, impact, then action.
    expect(Math.abs((await topOf(action)) - (await topOf(line)))).toBeLessThanOrEqual(8);
  });
}

test('F4-02: routine column labels show once on wide layouts and per row at 390; screen readers keep each cell label', async ({
  page,
}) => {
  await openScenario(page, 'active_battle_routines', { width: 1360 });
  const battle = page.locator('[data-region="thisBattle"]');
  const header = battle.locator('.tp-battle__columns');
  await expect(header).toBeVisible();
  await expect(battle.getByRole('table', { name: 'This battle' })).toBeVisible();
  await expect(header.getByRole('columnheader')).toHaveCount(5);
  await expect(header.getByRole('columnheader', { name: 'Last result' })).toBeVisible();
  const cellLabels = battle.locator('.tp-battle__routine .tp-battle__column-label');
  await expect(cellLabels).toHaveCount(9);
  for (const label of await cellLabels.all()) {
    const box = await label.boundingBox();
    expect(box?.width ?? 0).toBeLessThanOrEqual(1);
  }
  await expect(battle.locator('.tp-battle__routine').first()).toContainText('Last result');
  await page.setViewportSize({ width: 390, height: 900 });
  expect((await header.boundingBox())?.width ?? Infinity).toBeLessThanOrEqual(1);
  await expect(header.getByRole('columnheader')).toHaveCount(5);
  for (const label of await cellLabels.all()) await expect(label).toBeVisible();
});

test('F4-02: in the sketch scenario the guard reasons stay visible beside the compact layout', async ({
  page,
}) => {
  await openScenario(page, 'sketch', { width: 1360 });
  await page.setViewportSize({ width: 1360, height: SHORT });
  const turnOn = page.locator('[data-slot="gem"]').getByRole('button', { name: 'Turn on' });
  const reasonId = (await turnOn.getAttribute('aria-describedby')) ?? '';
  await expect(page.locator(`[id="${reasonId.split(' ')[0] ?? ''}"]`)).toBeVisible();
  const startNext = page.locator('[data-region="startNext"]');
  for (const name of ['Start playing', 'Collect gems only']) {
    const button = startNext.getByRole('button', { name });
    const described = (await button.getAttribute('aria-describedby')) ?? '';
    await expect(page.locator(`[id="${described.split(' ')[0] ?? ''}"]`)).toBeVisible();
  }
});

test('F4-02: at 390 no Control slot label is drawn over by its action row (sketch and x9 fallback)', async ({
  page,
}) => {
  for (const [scenario, faults] of [
    ['sketch', []],
    ['x9', ['slot:automation']],
  ] as const) {
    await openScenario(page, scenario, { width: 390, faults });
    for (const slot of ['automation', 'input', 'gem']) {
      const label = page.locator(`[data-slot="${slot}"] .tp-slot__label`);
      const fits = await label.evaluate((element) => element.scrollWidth <= element.clientWidth);
      expect(fits, `${scenario} ${slot}`).toBe(true);
    }
  }
});
