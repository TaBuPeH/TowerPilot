import { expect, test } from '@playwright/test';
import { axeCounts, openScenario, posts } from './helpers/helpers.ts';

test('application shell: every destination keeps one main and the current page', async ({
  page,
}) => {
  await openScenario(page, 'idle');
  const nav = page.getByRole('navigation', { name: 'Navigation' });
  const destinations = await nav.getByRole('link').evaluateAll((links) =>
    links.map((link) => ({
      label: link.textContent,
      href: link.getAttribute('href') ?? '',
    })),
  );
  expect(destinations).toHaveLength(11);
  for (const destination of destinations) {
    await nav.getByRole('link', { name: destination.label, exact: true }).click();
    await expect(page.locator('main')).toHaveCount(1);
    await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);
    await expect(nav.locator('[aria-current="page"]')).toHaveAttribute('href', destination.href);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  }
  expect(await posts(page)).toEqual([]);
});

test('application shell: mobile navigation closes by Escape and link with focus return', async ({
  page,
}) => {
  await openScenario(page, 'collecting', { width: 390 });
  const trigger = page.getByRole('button', { name: 'Navigation', exact: true });
  const nav = page.getByRole('navigation', { name: 'Navigation' });
  await expect(nav).toBeHidden();
  await trigger.click();
  await expect(nav).toBeVisible();
  await nav.getByRole('link', { name: 'Runs', exact: true }).focus();
  await page.keyboard.press('Escape');
  await expect(nav).toBeHidden();
  await expect(trigger).toBeFocused();
  await trigger.click();
  await nav.getByRole('link', { name: 'Runs', exact: true }).click();
  await expect(nav).toBeHidden();
  await expect(trigger).toBeFocused();
  await expect(
    page.locator('header').getByRole('button', { name: 'Turn input off' }),
  ).toBeVisible();
  expect(await posts(page)).toEqual([]);
  expect(await axeCounts(page)).toEqual([]);
});

test('application shell: focus survives both navigation breakpoints without stealing page focus', async ({
  page,
}) => {
  await openScenario(page, 'collecting', { width: 390 });
  const trigger = page.getByRole('button', { name: 'Navigation', exact: true });
  await trigger.focus();
  await page.setViewportSize({ width: 1360, height: 900 });
  await expect(page.getByRole('link', { name: 'Dashboard', exact: true })).toBeFocused();
  await page.setViewportSize({ width: 390, height: 900 });
  await expect(trigger).toBeFocused();

  const off = page.getByRole('button', { name: 'Turn input off', exact: true });
  await off.focus();
  await page.setViewportSize({ width: 1360, height: 900 });
  await expect(off).toBeFocused();
  await page.setViewportSize({ width: 390, height: 900 });
  await expect(off).toBeFocused();
  expect(await posts(page)).toEqual([]);
});

test('application shell: page failure keeps navigation and safe revocations', async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.setItem('tp.faults', 'page');
  });
  await page.goto('/__fake/use?scenario=collecting');
  await expect(page.locator('[data-fallback="page"]')).toBeVisible();
  const nav = page.getByRole('navigation', { name: 'Navigation' });
  await expect(nav).toBeVisible();
  await expect(page.getByRole('button', { name: 'Turn input off' })).toBeVisible();
  await nav.getByRole('link', { name: 'Runs', exact: true }).click();
  await expect(nav.getByRole('link', { name: 'Runs', exact: true })).toHaveAttribute(
    'aria-current',
    'page',
  );
  await expect(page).toHaveURL(/#runs$/u);
  // The injected page fault applies to every routed feature; navigation must
  // still change routes while the fallback preserves revocation controls.
  await expect(page.locator('[data-fallback="page"]')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Turn input off' })).toBeVisible();
  await expect(page.locator('main')).toHaveCount(1);
  expect(await posts(page)).toEqual([]);
});
