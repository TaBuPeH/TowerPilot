import { expect, test, type Locator, type Page } from '@playwright/test';
import { measureVisibleWords, openGlanceScenario } from './helpers/glance.ts';
import { posts } from './helpers/helpers.ts';

test.use({ locale: 'en-US', timezoneId: 'UTC', colorScheme: 'dark', reducedMotion: 'reduce' });

test('glance sampler is invariant to extra inline spans and block wrappers', async ({ page }) => {
  for (const content of [
    '<p>Collecting gems</p><p>≥2 claimed · ≥0 missed</p>',
    '<div><p><span>Col</span><span><b>lect</b>ing</span> <span>gems</span></p><div><p><span>≥2</span> claimed · <span>≥0</span> missed</p></div></div>',
  ]) {
    await page.setContent(`<main><section data-region="control">${content}</section></main>`);
    const words = await page.evaluate(measureVisibleWords, 'main');
    expect(words.total).toBe(6);
    expect(words.regions).toEqual({ control: 6 });
    expect(words.nodes.map((node) => node.text)).toEqual([
      'Collecting gems',
      '≥2 claimed · ≥0 missed',
    ]);
  }
});

test('glance sampler includes visible server text and selected values, excludes hidden text', async ({
  page,
}) => {
  await page.setContent(`
    <style>.sr-only { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0,0,0,0); }</style>
    <main><section data-region="control">
      <p>State <span aria-hidden="true">Running</span></p>
      <p><bdi data-server-text="name">Farm Ω</bdi></p>
      <label>Run <select><option>Not selected</option><option selected>Farm Ω</option></select></label>
      <label>Delay <input value="20 seconds"></label>
      <textarea>Visible note</textarea>
      <details><summary>Details</summary>Closed body <details open><summary>Nested secret</summary>Still closed</details></details>
      <span hidden>Hidden words</span><span style="display:none">Display hidden</span>
      <span style="visibility:hidden">Visibility hidden</span><span style="opacity:0">Transparent words</span>
      <select style="visibility:hidden"><option selected>Invisible choice</option></select>
      <span class="sr-only"><b>Screen reader only</b></span>
      <p title="Not drawn" aria-label="Not drawn either">End</p>
    </section></main>`);
  const words = await page.evaluate(measureVisibleWords, 'main');
  expect(words.total).toBe(14);
  expect(words.nodes.map((node) => node.text).join(' ')).toBe(
    'State Running Farm Ω Run Farm Ω Delay 20 seconds Visible note Details End',
  );
});

test('glance sampler retains below-fold and clipped copy, and reports clipping', async ({
  page,
}) => {
  await page.setContent(`
    <main><section data-region="control">
      <p style="width:20px;overflow:hidden;white-space:nowrap">Still counted despite clipping</p>
      <p style="margin-top:2000px">Below fold still counted</p>
      <details open><summary>Details</summary><p>Open body counted</p></details>
    </section></main>`);
  const words = await page.evaluate(measureVisibleWords, 'main');
  expect(words.total).toBe(12);
  expect(words.clipped).toEqual([{ element: 'p', text: 'Still counted despite clipping' }]);
});

/**
 * Local candidate regression ceilings measured from the fixed-fixture revision,
 * with every native disclosure closed, en-US/UTC and GLANCE_TIME. These are not
 * universal readability targets. New distinct facts require preservation tests
 * and an explicitly reviewed ceiling change, never an exclusion from the total.
 *
 * Ignored before/after inventories and build/fixture hashes:
 * backend/logs/reviews/council-tiles/revamp/density/manifest.json.
 * The preserved baseline build has no verified exact source-commit provenance.
 */
const CASES = [
  { scenario: 'collecting', ceiling: { control: 65, needsYou: 18, startNext: 45 } },
  { scenario: 'sketch', ceiling: { control: 82, needsYou: 35, startNext: 48 } },
  { scenario: 'off', ceiling: { control: 54, needsYou: 18, startNext: 20 } },
  { scenario: 'status_failed', ceiling: { control: 51, needsYou: 18, startNext: 29 } },
] as const;

async function visibleReason(page: Page, control: Locator): Promise<Locator> {
  await expect(control).toHaveAttribute('aria-disabled', 'true');
  const ids = (await control.getAttribute('aria-describedby'))?.trim().split(/\s+/u) ?? [];
  expect(ids.length, 'Disabled control has a linked reason').toBeGreaterThan(0);
  for (const id of ids) {
    const reason = page.locator(`[id="${id}"]`);
    await expect(reason).toBeVisible();
    await expect(reason).not.toHaveText('');
  }
  return page.locator(`[id="${ids[0] ?? ''}"]`);
}

async function revocation(slot: Locator, name: string | RegExp): Promise<void> {
  const button = slot.getByRole('button', { name });
  await expect(button).toBeVisible();
  await expect(button).toBeEnabled();
}

for (const width of [1360, 390]) {
  for (const { scenario, ceiling } of CASES) {
    test(`Dashboard glance ${scenario} at ${width}: measured copy and preserved decisions`, async ({
      page,
    }, testInfo) => {
      await openGlanceScenario(page, scenario, width);
      const inventory = await page.evaluate(measureVisibleWords, 'main');
      await testInfo.attach('visible-word-inventory', {
        body: JSON.stringify({ scenario, width, inventory }, null, 2),
        contentType: 'application/json',
      });
      for (const [region, maximum] of Object.entries(ceiling)) {
        const count = inventory.regions[region];
        expect(count, `${region} must be present`).toBeGreaterThan(0);
        expect(count, `${scenario}/${width}/${region}`).toBeLessThanOrEqual(maximum);
      }
      expect(inventory.clipped, 'Clipping cannot earn a word reduction').toEqual([]);
      const control = page.locator('[data-region="control"]');
      const gem = control.locator('[data-slot="gem"]');
      const input = control.locator('[data-slot="input"]');
      const automation = control.locator('[data-slot="automation"]');
      const start = page.locator('[data-region="startNext"]');
      const needs = page.locator('[data-region="needsYou"]');
      await revocation(input, 'Turn input off');
      await expect(input.locator('[data-fact="permission"]')).toHaveText('Allowed');
      await expect(input.getByText('Observed', { exact: true })).toBeVisible();
      // Removed restatements must be deleted, not merely hidden from the counter.
      await expect(
        input.getByText('Automation may tap this emulator', { exact: true }),
      ).toHaveCount(0);
      for (const disabled of await page.locator('main button[aria-disabled="true"]').all()) {
        await visibleReason(page, disabled);
      }
      if (scenario === 'collecting' || scenario === 'sketch') {
        await revocation(automation, /^Stop/u);
        await expect(automation.getByText('Ends automation, not the battle')).toBeVisible();
        await expect(gem.getByText('Collecting gems', { exact: true })).toBeVisible();
        await expect(gem.getByText('Running', { exact: true })).toHaveCount(0);
        await expect(gem.locator('[data-gem-counts="orbit"]')).toHaveText('≥0 picked up');
        await expect(gem.locator('[data-gem-counts="orbit"]')).toBeVisible();
      }
      if (scenario === 'collecting') {
        await revocation(gem, 'Pause harvesting');
        await expect(gem.locator('[data-gem-counts="ad"]')).toHaveText('≥2 claimed · ≥1 missed');
        await expect(gem.locator('[data-gem-counts="ad"]')).toBeVisible();
        // The unique missed-window evidence remains reachable under its named disclosure.
        await gem.getByText('Details', { exact: true }).click();
        await expect(gem.locator('[data-gem-cause]')).toHaveText('No offer seen, 1 m ago');
        await expect(gem.locator('[data-gem-cause]')).toBeVisible();
      } else if (scenario === 'sketch') {
        await expect(gem.locator('[data-fact="setting"]')).toHaveText('Not set');
        const harvestReason = await visibleReason(
          page,
          gem.getByRole('button', { name: 'Turn on', exact: true }),
        );
        const startReason = await visibleReason(
          page,
          start.getByRole('button', { name: 'Start playing', exact: true }),
        );
        await expect(harvestReason).toHaveText('Starting collection is blocked');
        await expect(startReason).toContainText(
          "Setup's test battle wasn't confirmed back on Home",
        );
        expect(await harvestReason.textContent()).not.toBe(await startReason.textContent());
        // One visible shared impact, with its distinct Start and collection values.
        await expect(needs.locator('.tp-need__impact')).toHaveCount(1);
        await expect(needs.locator('.tp-need__impact')).toContainText('StartOff');
        await expect(needs.locator('.tp-need__impact')).toContainText(
          'Gem collectionStarting collection is blocked',
        );
      } else if (scenario === 'status_failed') {
        await revocation(gem, 'Pause harvesting');
        await expect(gem.locator('[data-fact="setting"]')).toHaveText('Unknown');
        await expect(gem.getByText("Harvesting state can't be read")).toBeVisible();
        await expect(gem.locator('[data-gem-counts]')).toHaveCount(0);
        await expect(gem.getByText('Collecting gems', { exact: true })).toHaveCount(0);
      } else {
        await expect(gem.locator('[data-fact="setting"]')).toHaveText('Off');
        await expect(gem.getByText('No gem collection running')).toBeVisible();
        await expect(gem.getByRole('combobox', { name: 'Gem settings from' })).toHaveValue(
          'bp_farm',
        );
      }
      // A source identity links to the exact saved run, including when a picker replaces its label.
      const source = gem.locator('a[href="#runs/saved/bp_farm"]');
      await expect(source).toBeVisible();
      await source.click();
      await expect(page).toHaveURL(/#runs\/saved\/bp_farm$/u);
      await expect(
        page.locator('[data-region="runsSaved"] select[data-focus-key="runs:run"]'),
      ).toHaveValue('bp_farm');
      expect(await posts(page)).toEqual([]);
    });
  }

  test(`Dashboard glance stale collector at ${width}: intent remains distinct from observed uncertainty`, async ({
    page,
  }) => {
    await openGlanceScenario(page, 'stale_collector', width);
    const gem = page.locator('[data-slot="gem"]');
    await revocation(gem, 'Pause harvesting');
    await expect(gem.locator('[data-fact="setting"]')).toHaveText('On');
    await expect(gem.getByText('Collector state unknown')).toBeVisible();
    await expect(gem.getByText('Collecting gems', { exact: true })).toHaveCount(0);
    await expect(gem.locator('[data-gem-counts]')).toHaveCount(0);
    await gem.getByRole('button', { name: /^About / }).click();
    await expect(
      page.getByRole('tooltip').getByText(/its report is stale, invalid/u),
    ).toBeVisible();
    expect(await posts(page)).toEqual([]);
  });

  test(`Dashboard glance recognition warning at ${width} remains visible without opening details`, async ({
    page,
  }) => {
    await openGlanceScenario(page, 'working_collector_finder_suspected', width);
    const gem = page.locator('[data-slot="gem"]');
    await expect(gem.locator('[data-gem-finder-suspected]')).toBeVisible();
    await expect(gem.locator('[data-gem-finder-suspected]')).toHaveText(
      'Orbit gems: frames are being seen but none is recognized; the recognizer may need attention.',
    );
    await revocation(gem, 'Pause harvesting');
    expect(await posts(page)).toEqual([]);
  });
}
