import { expect, test, type Locator, type Page } from '@playwright/test';
import { MATRIX } from '../unit/spec/specData.ts';
import { bodiesTo, openScenario, posts } from './helpers/helpers.ts';

/**
 * Spec 9 item 3(a) in a real browser: the revocation matrix of 9.1. Each
 * sample row under E0 (allow_taps false, fresh scan, no processes) at 1360,
 * 1054 and 390: Pause (INV-1) and Turn input off (INV-2) rendered, enabled and
 * sending exactly the named request, or absent; the Stop targets (INV-3) with
 * the exact request; the Gem, Input and Automation words. E1 (allow_taps
 * true) shows Turn input off on every row. The X rows the browser can
 * stage follow. Expected cells are the spec's (tests/unit/spec/specData.ts).
 */
const WIDTHS = [1360, 1054, 390] as const;
const E0_CONTEXT = { instance: 'main', serial: 'fake:1' };

const slot = (page: Page, id: string): Locator => page.locator(`[data-slot="${id}"]`);

/** Every width shows `button` rendered and enabled (true) or absent (false). */
async function acrossWidths(page: Page, button: Locator, shown: boolean): Promise<void> {
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 900 });
    if (shown) {
      await expect(button, `at ${String(width)}`).toBeVisible();
      await expect(button, `at ${String(width)}`).not.toHaveAttribute('aria-disabled', 'true');
    } else {
      await expect(button, `at ${String(width)}`).toHaveCount(0);
    }
  }
}

/** The word a slot shows first (its chip), as the spec writes it. */
async function slotWord(page: Page, id: string): Promise<string> {
  const chip = slot(page, id).locator('.tp-slot__facts .tp-chip, .tp-chip').first();
  const state = await chip.getAttribute('data-state');
  return state === null ? 'plain text' : state.charAt(0).toUpperCase() + state.slice(1);
}

for (const row of MATRIX) {
  test(`S-001 ${row.sample}: Pause harvesting ${row.pause} at 1360, 1054 and 390 (9.1 under E0, INV-1)`, async ({
    page,
  }) => {
    const problems = await openScenario(page, `e0_${row.sample}`);
    const pause = slot(page, 'gem').getByRole('button', { name: 'Pause harvesting' });
    await acrossWidths(page, pause, row.pause === 'shown');
    if (row.pause === 'shown') {
      await pause.click();
      await expect.poll(async () => (await bodiesTo(page, '/api/harvest')).length).toBe(1);
      const [body] = await bodiesTo(page, '/api/harvest');
      expect(body).toMatchObject({ enabled: false, expected_context: E0_CONTEXT });
      expect(Object.keys(body as object).sort()).toEqual([
        'enabled',
        'expected_context',
        'idempotency_key',
      ]);
    }
    expect(problems).toEqual([]);
  });

  test(`S-006 ${row.sample}: Turn input off ${row.turnInputOff} at 1360, 1054 and 390 (9.1 under E0, INV-2)`, async ({
    page,
  }) => {
    const problems = await openScenario(page, `e0_${row.sample}`);
    const turnOff = slot(page, 'input').getByRole('button', { name: 'Turn input off' });
    await acrossWidths(page, turnOff, row.turnInputOff === 'shown');
    if (row.turnInputOff === 'shown') {
      await turnOff.click();
      await expect
        .poll(async () => bodiesTo(page, '/api/automation-access'))
        .toEqual([{ enabled: false }]);
    }
    expect(problems).toEqual([]);
  });

  test(`S-006 ${row.sample} under E1 (allow_taps true): Turn input off shown and enabled`, async ({
    page,
  }) => {
    await openScenario(page, row.sample);
    const turnOff = slot(page, 'input').getByRole('button', { name: 'Turn input off' });
    await expect(turnOff).toBeVisible();
    await expect(turnOff).not.toHaveAttribute('aria-disabled', 'true');
  });

  test(`INV-3 ${row.sample}: Stop targets ${row.stopTargets}; words Gem ${row.gem}, Input ${row.input}, Automation ${row.automation} (9.1 under E0)`, async ({
    page,
  }) => {
    await openScenario(page, `e0_${row.sample}`);
    // The config read can answer after the first status read; wait for the words to settle.
    await expect.poll(async () => slotWord(page, 'gem')).toBe(row.gem);
    await expect.poll(async () => slotWord(page, 'input')).toBe(row.input);
    await expect.poll(async () => slotWord(page, 'automation')).toBe(row.automation);
    const stops = slot(page, 'automation').getByRole('button', { name: /^Stop/u });
    if (row.stopTargets === 'none') {
      await expect(stops).toHaveCount(0);
      return;
    }
    const [prefix, revision] = row.stopTargets.split('@');
    await acrossWidths(page, stops, true);
    await expect(stops).toHaveCount(1);
    await stops.click();
    const dialog = page.locator('dialog[open]');
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Stop', exact: true }).click();
    await expect
      .poll(async () =>
        (await posts(page)).filter((entry) =>
          /^\/api\/operations\/[^/]+\/cancel$/u.test(entry.path),
        ),
      )
      .toHaveLength(1);
    const [cancel] = (await posts(page)).filter((entry) => entry.path.endsWith('/cancel'));
    expect(cancel?.path.startsWith(`/api/operations/${prefix ?? ''}`)).toBe(true);
    expect(JSON.parse(cancel?.body ?? '{}')).toEqual({ expected_revision: Number(revision) });
  });
}

test('S-091 X1 (cold, the first status answers 502): Pause and Turn input off shown and enabled at every width; no Turn on, Start or Collect', async ({
  page,
}) => {
  await openScenario(page, 'cold_502', { waitForRead: false });
  await page.waitForTimeout(1_500);
  const control = page.locator('[data-region="control"]');
  await acrossWidths(page, control.getByRole('button', { name: 'Pause harvesting' }).first(), true);
  await acrossWidths(page, control.getByRole('button', { name: 'Turn input off' }).first(), true);
  await expect(page.getByRole('button', { name: 'Turn on' })).toHaveCount(0);
  for (const name of ['Start playing', 'Collect gems only']) {
    const button = page.getByRole('button', { name });
    if ((await button.count()) > 0) await expect(button).toHaveAttribute('aria-disabled', 'true');
  }
  await expect(slot(page, 'automation').getByRole('button', { name: /^Stop/u })).toHaveCount(0);
});

test('S-091 X2a: with status and config failing, a Pause click reads the context at the click and sends one Pause with it', async ({
  page,
}) => {
  await openScenario(page, 'x2a', { waitForRead: false });
  await page.waitForTimeout(1_500);
  await page
    .locator('[data-region="control"]')
    .getByRole('button', { name: 'Pause harvesting' })
    .first()
    .click();
  await expect.poll(async () => (await bodiesTo(page, '/api/harvest')).length).toBe(1);
  const [body] = await bodiesTo(page, '/api/harvest');
  expect(body).toMatchObject({ enabled: false, expected_context: E0_CONTEXT });
});

test('S-091 X2b: when no read names the emulator, Pause sends nothing, says so, invents no context, and Turn input off stays enabled', async ({
  page,
}) => {
  await openScenario(page, 'x2b', { waitForRead: false });
  await page.waitForTimeout(1_500);
  const control = page.locator('[data-region="control"]');
  await control.getByRole('button', { name: 'Pause harvesting' }).first().click();
  await expect(control).toContainText("Pause not sent: the server didn't name the active emulator");
  await page.waitForTimeout(1_000);
  expect(await bodiesTo(page, '/api/harvest')).toEqual([]);
  const turnOff = control.getByRole('button', { name: 'Turn input off' }).first();
  await expect(turnOff).not.toHaveAttribute('aria-disabled', 'true');
  await turnOff.click();
  await expect
    .poll(async () => bodiesTo(page, '/api/automation-access'))
    .toEqual([{ enabled: false }]);
});

test('S-091 X3: a warm page whose status read starts failing keeps Pause, Turn input off and the retained Stop, every word Unknown', async ({
  page,
}) => {
  await openScenario(page, 'e0_collecting');
  // The premise is a warm page: the status read naming the collector (its Stop target) has
  // arrived before reads start failing. openScenario waits for the first read only, which under
  // load may be another resource's (this test failed intermittently in full runs).
  await expect(slot(page, 'automation').getByRole('button', { name: /^Stop/u })).toHaveCount(1);
  await page.request.get('/__fake/set?scenario=cold_502');
  await expect(slot(page, 'gem').locator('.tp-chip').first()).toHaveAttribute(
    'data-state',
    'unknown',
    {
      timeout: 10_000,
    },
  );
  await expect(slot(page, 'gem').getByRole('button', { name: 'Pause harvesting' })).toBeEnabled();
  await expect(slot(page, 'input').getByRole('button', { name: 'Turn input off' })).toBeVisible();
  await expect(slot(page, 'automation').getByRole('button', { name: /^Stop/u })).toHaveCount(1);
  await expect(slot(page, 'automation').locator('.tp-chip').first()).toHaveAttribute(
    'data-state',
    'unknown',
  );
});

for (const [name, part] of [
  ['X4', 'automation'],
  ['X5', 'gem'],
] as const) {
  test(`S-091 ${name}: a warm page whose ${part} slot starts failing keeps Pause, Turn input off and the Stop of the collector`, async ({
    page,
  }) => {
    await openScenario(page, 'e0_collecting');
    await page.evaluate((fault) => {
      window.sessionStorage.setItem('tp.faults', fault);
    }, `slot:${part}`);
    const fallback = page.locator(`[data-fallback="${part}"]`);
    await expect(fallback).toBeVisible({ timeout: 10_000 });
    const region = page.locator('[data-region="control"]');
    await expect(region.getByRole('button', { name: 'Pause harvesting' }).first()).toBeVisible();
    await expect(region.getByRole('button', { name: 'Turn input off' }).first()).toBeVisible();
    await expect(region.getByRole('button', { name: /^Stop/u })).toHaveCount(1);
    await expect(fallback.getByRole('button', { name: 'Turn on' })).toHaveCount(0);
  });
}

for (const [name, fault] of [
  ['X6', 'slot:automation'],
  ['the Control card', 'control'],
] as const) {
  test(`S-091 ${name}: a cold first read drawn by a failing ${fault} keeps the Stop built from the read, Pause and Turn input off`, async ({
    page,
  }) => {
    // A failing Control card has no header age to wait for; its fallback is the signal.
    await openScenario(page, 'e0_collecting', { faults: [fault], waitForRead: false });
    const fallback = page.locator('[data-fallback="automation"]');
    await expect(fallback).toBeVisible();
    await expect(fallback.getByRole('button', { name: /^Stop/u })).toHaveCount(1);
    await expect(fallback.getByRole('button', { name: 'Pause harvesting' })).toBeVisible();
    await expect(fallback.getByRole('button', { name: 'Turn input off' })).toBeVisible();
    await expect(fallback.getByRole('button', { name: 'Start playing' })).toHaveCount(0);
  });
}

const INCOMPLETE = [
  ['x9', "Can't identify the operation"],
  ['runner_created_null', "Can't prove it is the same process"],
  ['stopping_no_revision', 'Waiting for fresh status'],
] as const;

for (const [scenario, reason] of INCOMPLETE) {
  for (const mode of ['cold', 'warm'] as const) {
    test(`F4-01 ${scenario} ${mode}: the Automation fallback keeps the disabled Stop with its visible reason beside an enabled Turn input off at 1360, 1054 and 390, and sends nothing`, async ({
      page,
    }) => {
      if (mode === 'cold') {
        await openScenario(page, scenario, { faults: ['slot:automation'] });
      } else {
        await openScenario(page, scenario);
        const normal = slot(page, 'automation').getByRole('button', { name: /^Stop/u });
        await expect(normal).toHaveAttribute('aria-disabled', 'true');
        await page.evaluate(() => {
          window.sessionStorage.setItem('tp.faults', 'slot:automation');
        });
      }
      const fallback = page.locator('[data-fallback="automation"]');
      await expect(fallback).toBeVisible({ timeout: 10_000 });
      const stop = fallback.getByRole('button', { name: /^Stop/u });
      for (const width of WIDTHS) {
        await page.setViewportSize({ width, height: 900 });
        await expect(stop, `at ${String(width)}`).toHaveCount(1);
        await expect(stop, `at ${String(width)}`).toBeVisible();
        await expect(stop, `at ${String(width)}`).toHaveAttribute('aria-disabled', 'true');
        const described = (await stop.getAttribute('aria-describedby')) ?? '';
        const reasonLine = page.locator(`[id="${described.split(' ')[0] ?? ''}"]`);
        await expect(reasonLine, `at ${String(width)}`).toBeVisible();
        await expect(reasonLine, `at ${String(width)}`).toContainText(reason);
        const inputOff = fallback.getByRole('button', { name: 'Turn input off' });
        await expect(inputOff, `at ${String(width)}`).toBeVisible();
        await expect(inputOff, `at ${String(width)}`).not.toHaveAttribute('aria-disabled', 'true');
        // The slot label and its fallback never overlap (the narrow slot grid).
        const label = await slot(page, 'automation').locator('.tp-slot__label').boundingBox();
        const drawn = await fallback.boundingBox();
        if (label === null || drawn === null) throw new Error('label and fallback must render');
        expect(
          drawn.y,
          `fallback starts below the slot label at ${String(width)}`,
        ).toBeGreaterThanOrEqual(label.y + label.height - 0.5);
      }
      await stop.click({ force: true });
      await expect(page.locator('dialog[open]')).toHaveCount(0);
      expect(await posts(page)).toEqual([]);
    });
  }
}

test('S-091 X8: two untracked runners: Pause hidden, Turn input off shown, row 0 kills its exact pid and created; a runner without its start time cannot be killed and says why', async ({
  page,
}) => {
  await openScenario(page, 'two_runners');
  await expect(slot(page, 'gem').getByRole('button', { name: 'Pause harvesting' })).toHaveCount(0);
  await expect(slot(page, 'input').getByRole('button', { name: 'Turn input off' })).toBeVisible();
  const kills = slot(page, 'automation').getByRole('button', { name: /^Stop process/u });
  await expect(kills).toHaveCount(2);
  const names = await kills.evaluateAll((buttons) =>
    buttons.map((button) => button.getAttribute('aria-labelledby') ?? ''),
  );
  expect(new Set(names).size).toBe(2);
  await kills.first().click();
  await page.locator('dialog[open]').getByRole('button', { name: 'Stop process' }).click();
  await expect
    .poll(async () => bodiesTo(page, '/api/control'))
    .toEqual([{ action: 'kill', pid: 4511, created: 1799990001.25 }]);

  await openScenario(page, 'runner_created_null');
  const disabled = slot(page, 'automation').getByRole('button', { name: /^Stop/u });
  await expect(disabled).toHaveAttribute('aria-disabled', 'true');
  const reason = await disabled.getAttribute('aria-describedby');
  await expect(page.locator(`[id="${reason ?? ''}"]`)).toContainText(
    "Can't prove it is the same process",
  );
  await expect(slot(page, 'input').getByRole('button', { name: 'Turn input off' })).toBeVisible();
});

for (const scenario of ['x9', 'x9_taps']) {
  test(`F4-R2-01 F-027 ${scenario}: Turn on is shown disabled with the D-4 reason at 1360, 1054 and 390; Turn input off and the disabled Stop with its identity reason stay; a click sends nothing`, async ({
    page,
  }) => {
    await openScenario(page, scenario);
    const gem = slot(page, 'gem');
    const turnOn = gem.getByRole('button', { name: 'Turn on' });
    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: 900 });
      await expect(turnOn, `at ${String(width)}`).toBeVisible();
      await expect(turnOn, `at ${String(width)}`).toHaveAttribute('aria-disabled', 'true');
      const described = (await turnOn.getAttribute('aria-describedby')) ?? '';
      const reason = page.locator(`[id="${described.split(' ')[0] ?? ''}"]`);
      await expect(reason, `at ${String(width)}`).toBeVisible();
      await expect(reason, `at ${String(width)}`).toHaveText('Waiting for a complete status read');
      const inputOff = slot(page, 'input').getByRole('button', { name: 'Turn input off' });
      await expect(inputOff, `at ${String(width)}`).toBeVisible();
      await expect(inputOff, `at ${String(width)}`).not.toHaveAttribute('aria-disabled', 'true');
      const stop = slot(page, 'automation').getByRole('button', { name: /^Stop/u });
      await expect(stop, `at ${String(width)}`).toHaveAttribute('aria-disabled', 'true');
      const stopReason = (await stop.getAttribute('aria-describedby')) ?? '';
      await expect(page.locator(`[id="${stopReason.split(' ')[0] ?? ''}"]`)).toContainText(
        "Can't identify the operation",
      );
    }
    await turnOn.click({ force: true });
    await page.waitForTimeout(300);
    expect(await bodiesTo(page, '/api/harvest')).toEqual([]);
  });
}

test('F4-R2-01 F-027 control (idle: a D-4 read, connected, taps allowed, a clear guard): Turn on is enabled and one click sends the exact compare-and-set body once', async ({
  page,
}) => {
  await openScenario(page, 'idle');
  const turnOn = slot(page, 'gem').getByRole('button', { name: 'Turn on' });
  await expect(turnOn).toBeVisible();
  await expect(turnOn).not.toHaveAttribute('aria-disabled', 'true');
  await turnOn.click();
  await expect.poll(async () => (await bodiesTo(page, '/api/harvest')).length).toBe(1);
  const [body] = (await bodiesTo(page, '/api/harvest')) as Record<string, unknown>[];
  expect(Object.keys(body ?? {}).sort()).toEqual([
    'enabled',
    'expected_context',
    'expected_revision',
    'idempotency_key',
    'preset',
  ]);
  expect(body).toMatchObject({ enabled: true, preset: 'bp_farm', expected_revision: 2 });
  expect(Object.keys(body?.['expected_context'] as object).sort()).toEqual([
    'account',
    'instance',
    'profile',
    'serial',
  ]);
  expect(body?.['expected_context']).toMatchObject({ instance: 'main', serial: 'fake:1' });
  expect(typeof body?.['idempotency_key']).toBe('string');
  await page.waitForTimeout(3_000);
  expect(await bodiesTo(page, '/api/harvest')).toHaveLength(1);
});

test('F4-05: in the sketch scenario the running manual collector reads Running, while the guard wording is scoped to starting collection', async ({
  page,
}) => {
  await openScenario(page, 'sketch');
  const gem = slot(page, 'gem');
  await expect(gem.locator('.tp-chip').first()).toHaveAttribute('data-state', 'running');
  await expect(gem).toContainText('Collecting gems');
  const turnOn = gem.getByRole('button', { name: 'Turn on' });
  await expect(turnOn).toHaveAttribute('aria-disabled', 'true');
  const reasonId = (await turnOn.getAttribute('aria-describedby')) ?? '';
  await expect(page.locator(`[id="${reasonId.split(' ')[0] ?? ''}"]`)).toHaveText(
    'Starting collection is blocked',
  );
  const needs = page.locator('[data-region="needsYou"]');
  await expect(needs.getByText('Starting collection is blocked')).toBeVisible();
  await expect(page.getByText('Gem collection is on hold')).toHaveCount(0);
});

test('F-038: in the sketch scenario Start playing and Collect gems only are disabled, drawn without their tier fill, name their visible reason, and send nothing', async ({
  page,
}) => {
  await openScenario(page, 'sketch');
  const region = page.locator('[data-region="startNext"]');
  for (const name of ['Start playing', 'Collect gems only']) {
    const button = region.getByRole('button', { name });
    await expect(button).toHaveAttribute('aria-disabled', 'true');
    const look = await button.evaluate((element) => {
      const style = getComputedStyle(element);
      return { background: style.backgroundColor, border: style.borderTopStyle };
    });
    // Not the primary fill (#2969b5); a dim solid edge (dashed edges belong to Unknown alone).
    expect(look.background).not.toBe('rgb(41, 105, 181)');
    expect(look.border).toBe('solid');
    const reasonId = await button.getAttribute('aria-describedby');
    await expect(page.locator(`[id="${reasonId ?? ''}"]`)).toContainText('Blocked');
    // Playwright treats aria-disabled as disabled; force the click a person could still make.
    await button.click({ force: true });
  }
  await expect(region.getByText('Ready', { exact: true })).toHaveCount(0);
  await page.waitForTimeout(500);
  expect(await bodiesTo(page, '/api/control')).toEqual([]);
});
