import { expect, test, type Locator, type Page } from '@playwright/test';
import { axeCounts, posts, WCAG_AA_TAGS } from './helpers/helpers.ts';
import { measureVisibleWords } from './helpers/glance.ts';

/** Exercise the mounted React component in real pages, using only the fixture backend. */
async function openPage(page: Page, destination: 'equipment' | 'abilities' | 'profiles') {
  const scenario = destination === 'profiles' ? 'profiles' : 'runs_saved';
  await page.request.get(`/__fake/use?scenario=${scenario}`);
  if (destination !== 'profiles') await page.request.get(`/__fake/${destination}`);
  await page.goto(`/ui/app/#${destination}`);
  if (destination === 'equipment')
    await page.getByRole('button', { name: 'plain', exact: true }).click();
  if (destination === 'abilities')
    await expect(page.getByRole('textbox', { name: 'Plan label', exact: true })).toBeVisible();
}

async function checkTip(page: Page, trigger: Locator, touch: boolean, content: string) {
  const tip = page.getByRole('tooltip');
  await expect(trigger).toBeVisible();
  const target = await trigger.boundingBox();
  expect(target).not.toBeNull();
  expect(target?.width).toBeGreaterThanOrEqual(touch ? 44 : 24);
  expect(target?.height).toBeGreaterThanOrEqual(touch ? 44 : 24);

  if (touch) await trigger.tap();
  else await trigger.focus();
  await expect(tip).toContainText(content);
  await expect(tip).toBeVisible();
  const tipId = await tip.getAttribute('id');
  if (tipId === null) throw new Error('Missing tooltip id');
  await expect(trigger).toHaveAttribute('aria-describedby', tipId);
  const box = await tip.boundingBox();
  const viewport = page.viewportSize();
  if (box === null || viewport === null) throw new Error('Missing tooltip or viewport geometry');
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
  expect(box.y + box.height).toBeLessThanOrEqual(viewport.height);
  expect(await axeCounts(page, WCAG_AA_TAGS)).toEqual([]);

  await page.keyboard.press('Escape');
  await expect(tip).toHaveCount(0);
  await expect(trigger).toBeFocused();
  if (touch) {
    await trigger.tap();
    await expect(tip).toBeVisible();
    await trigger.tap();
    await expect(tip).toHaveCount(0);
    await trigger.tap();
  } else {
    await trigger.hover();
    await expect(tip).toBeVisible();
    await tip.hover();
    // Exercise the pointer bridge for longer than its leave grace period.
    await page.waitForTimeout(200);
    await expect(tip).toBeVisible();
    await expect(trigger).toHaveAttribute('aria-expanded', 'true');
  }
  await page.getByRole('heading', { level: 1 }).click();
  await expect(tip).toHaveCount(0);
  expect(await posts(page)).toEqual([]);
}

test('long help remains readable with the keyboard in a short viewport', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 240 });
  await openPage(page, 'equipment');
  const trigger = page.getByRole('button', { name: 'About equipment selection', exact: true });
  await trigger.focus();
  const tip = page.getByRole('tooltip');
  await expect(tip).toBeVisible();
  expect(await tip.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
  await page.keyboard.press('PageDown');
  await expect.poll(() => tip.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  await expect(trigger).toBeFocused();
  await page.keyboard.press('Home');
  await expect.poll(() => tip.evaluate((element) => element.scrollTop)).toBe(0);
  await page.keyboard.press('End');
  await expect
    .poll(() =>
      tip.evaluate((element) => element.scrollHeight - element.clientHeight - element.scrollTop),
    )
    .toBeLessThanOrEqual(1);
  expect(await axeCounts(page, WCAG_AA_TAGS)).toEqual([]);
  await tip.focus();
  await page.keyboard.press('Home');
  await expect.poll(() => tip.evaluate((element) => element.scrollTop)).toBe(0);
  await page.keyboard.press('PageDown');
  await expect.poll(() => tip.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  await page.keyboard.press('Escape');
  await expect(tip).toHaveCount(0);
  await expect(trigger).toBeFocused();
  expect(await posts(page)).toEqual([]);
});

for (const { name, touch, width } of [
  { name: 'keyboard and pointer help in pages', touch: false, width: 1360 },
  { name: 'narrow touch help in pages', touch: true, width: 390 },
  { name: 'wide touch help in pages', touch: true, width: 1360 },
]) {
  test.describe(name, () => {
    test.use({ viewport: { width, height: 900 }, hasTouch: touch });

    test('Equipment keeps exact revisions and save reasons outside help', async ({ page }) => {
      await openPage(page, 'equipment');
      const editor = page.locator('[data-region="equipment-editor"]');
      const save = editor.getByRole('button', { name: 'Save selection', exact: true });
      await expect(save).toHaveAttribute('aria-disabled', 'true');
      const reason = await save.getAttribute('aria-describedby');
      if (reason === null) throw new Error('Missing save reason');
      await expect(page.locator(`[id="${reason}"]`)).toBeVisible();
      await checkTip(
        page,
        page.getByRole('button', { name: 'About equipment selection', exact: true }),
        touch,
        'Save',
      );
      const revisions = editor.locator('.tp-equipment__revision');
      await expect(revisions).toHaveCount(2);
      for (const revision of await revisions.all()) {
        await revision.getByText('Full value', { exact: true }).click();
        await expect(revision.locator('code')).toHaveText(/^[a-f0-9]{64}$/u);
        await expect(revision.locator('code')).toBeVisible();
      }
      expect((await page.evaluate(measureVisibleWords, 'main')).clipped).toEqual([]);
      expect(await posts(page)).toEqual([]);
    });

    test('Abilities keeps readiness and save scope visible while explaining evidence', async ({
      page,
    }) => {
      await openPage(page, 'abilities');
      await expect(page.getByText('Not checked by this editor', { exact: true })).toBeVisible();
      await expect(
        page.getByText(
          'Saving changes future plans; it does not fire abilities or change a live battle.',
        ),
      ).toBeVisible();
      await checkTip(
        page,
        page.getByRole('button', { name: 'About Recorded ownership and readiness', exact: true }),
        touch,
        'Policy choices do not verify abilities or buttons.',
      );
    });

    test('Profiles keeps activation timing visible while explaining the file library', async ({
      page,
    }) => {
      await openPage(page, 'profiles');
      await expect(
        page
          .locator('[data-form="activate"]')
          .getByText('Automation uses this profile from its next start.'),
      ).toBeVisible();
      await checkTip(
        page,
        page.getByRole('button', { name: 'About Profile files', exact: true }),
        touch,
        'Copying a profile or saving a scan draft only writes a file.',
      );
    });

    test('Runs checkbox labels provide full targets without sending a save', async ({ page }) => {
      await page.request.get('/__fake/use?scenario=runs_entry_saved');
      await page.goto('/ui/app/#runs/saved/farm_deep');
      for (const field of ['restart_via_home', 'cancel_sprint', 'perk_bans']) {
        const label = page.locator(`[data-entry-field="${field}"] .tp-check-field`);
        const checkbox = label.getByRole('checkbox');
        await expect(checkbox).toBeVisible();
        const wasChecked = await checkbox.isChecked();
        const box = await label.boundingBox();
        if (box === null) throw new Error(`Missing target for ${field}`);
        expect(box.width).toBeGreaterThanOrEqual(touch ? 44 : 24);
        expect(box.height).toBeGreaterThanOrEqual(touch ? 44 : 24);
        const position = { x: box.width - 4, y: box.height / 2 };
        if (touch) await label.tap({ position });
        else await label.click({ position });
        await expect(checkbox).toBeChecked({ checked: !wasChecked });
        if (touch) await label.tap({ position });
        else await label.click({ position });
        await expect(checkbox).toBeChecked({ checked: wasChecked });
      }
      expect(await posts(page)).toEqual([]);
    });

    test('Collection keeps quota state and shared-run links visible with usable targets', async ({
      page,
    }) => {
      await page.request.get('/__fake/use?scenario=collection');
      await page.goto('/ui/app/#collection');
      const quota = page.locator('[data-region="collectionRewards"] .tp-facts__item').filter({
        has: page.getByText('Daily quota and reset time', { exact: true }),
      });
      await expect(quota.locator('dd')).toContainText('Not reported');
      await checkTip(
        page,
        quota.getByRole('button', { name: 'About Daily quota and reset time', exact: true }),
        touch,
        'The server does not report how many rewards are left today or when they reset',
      );
      const links = page.locator('[data-used-by] a');
      await expect(links.first()).toBeVisible();
      for (const link of await links.all()) {
        const box = await link.boundingBox();
        if (box === null) throw new Error('Missing shared-run link');
        expect(box.width).toBeGreaterThanOrEqual(touch ? 44 : 24);
        expect(box.height).toBeGreaterThanOrEqual(touch ? 44 : 24);
      }
      const destination = await links.first().getAttribute('href');
      if (destination === null) throw new Error('Missing shared-run destination');
      await links.first().click();
      await expect(page).toHaveURL((url) => url.hash === destination);
      expect(await posts(page)).toEqual([]);
    });

    test('Shopping help explains choices without changing the draft or hiding field errors', async ({
      page,
    }) => {
      await page.request.get('/__fake/use?scenario=shopping_library_v2');
      await page.goto('/ui/app/#runs/saved/farm');
      const library = page.locator('[data-shopping-library]');
      await library.getByText('Manage policies', { exact: true }).click();
      const form = library.locator('[data-library-policy="main_list"]');
      await form.getByText('Edit shared policy', { exact: true }).click();
      const pattern = form.getByLabel('Purchase pattern', { exact: true });
      const selected = await pattern.inputValue();
      await checkTip(
        page,
        form.getByRole('button', { name: 'About Purchase pattern', exact: true }),
        touch,
        'Each turn buys the first upgrade in the queue that is affordable.',
      );
      await expect(pattern).toHaveValue(selected);
      const reserve = form.getByLabel('Cash reserve', { exact: true });
      await expect(form.getByText('Type 0 to keep none back.', { exact: true })).toBeVisible();
      await reserve.fill('invalid');
      await expect(reserve).toHaveAttribute('aria-invalid', 'true');
      await expect(
        form.getByText(
          'Not a number. Use digits, an optional minus and an optional decimal point.',
          { exact: true },
        ),
      ).toBeVisible();
      expect(await posts(page)).toEqual([]);
    });

    test('Runtime keeps event evidence visible and bounds expanded raw JSON', async ({ page }) => {
      await page.request.get('/__fake/use?scenario=runtime');
      await page.goto('/ui/app/#runtime');
      const events = page.getByRole('region', { name: 'Returned events', exact: true });
      for (const text of ['unconfirmed', 'timeout', 'the device went away']) {
        await expect(events.getByText(text, { exact: true })).toBeVisible();
      }
      const details = events.locator('details[data-event-key]');
      await expect(details).toHaveCount(5);
      await expect(events.locator('details[open]')).toHaveCount(0);
      await checkTip(
        page,
        events.getByRole('button', { name: 'About Returned events', exact: true }),
        touch,
        'The failure flag is a filter aid',
      );
      await details.first().locator('summary').click();
      const raw = details.first().locator('pre');
      await expect(raw).toContainText('"outcome": "accepted"');
      const geometry = await raw.evaluate((node) => ({
        height: node.getBoundingClientRect().height,
        limit: Number.parseFloat(getComputedStyle(document.documentElement).fontSize) * 20,
        overflow: getComputedStyle(node).overflowY,
      }));
      expect(geometry.height).toBeLessThanOrEqual(geometry.limit + 1);
      expect(geometry.overflow).toBe('auto');
      expect(await posts(page)).toEqual([]);
    });

    test('Emulators names settings help and shows a suggestion once', async ({ page }) => {
      await page.request.get('/__fake/use?scenario=emulators');
      await page.goto('/ui/app/#emulators');
      const suggestion = page.locator('[data-suggestion]');
      await expect(suggestion).toHaveCount(1);
      await expect(suggestion).toHaveAttribute('data-row', 'MuMu:0');
      await expect(
        suggestion.getByRole('button', { name: 'Use this one', exact: true }),
      ).toHaveCount(1);
      await checkTip(
        page,
        page.getByRole('button', { name: 'About Video preview settings', exact: true }),
        touch,
        'preview',
      );
      expect(await posts(page)).toEqual([]);
    });
  });
}
