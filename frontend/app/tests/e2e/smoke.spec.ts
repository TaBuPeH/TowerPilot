import { expect, test } from '@playwright/test';

test('the built app opens the Dashboard with no console errors', async ({ page }) => {
  const problems: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error' || message.type() === 'warning') {
      problems.push(`${message.type()}: ${message.text()}`);
    }
  });
  page.on('pageerror', (error) => {
    problems.push(`pageerror: ${error.message}`);
  });
  const failedRequests: string[] = [];
  page.on('requestfailed', (request) => {
    // A read of the old context aborted when the first config answer names the context (S-014).
    if (request.failure()?.errorText !== 'net::ERR_ABORTED') failedRequests.push(request.url());
  });
  page.on('response', (response) => {
    if (response.status() >= 400) {
      failedRequests.push(`${String(response.status())} ${response.url()}`);
    }
  });

  await page.goto('/__fake/use?scenario=idle');
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible();
  await expect(page).toHaveTitle('Dashboard - Tower Pilot');
  for (const name of ['Control', 'Needs you', 'This battle', 'Start next', 'Plan and history']) {
    await expect(page.getByRole('heading', { level: 2, name })).toBeVisible();
  }
  await expect(page.locator('[aria-live]')).toHaveCount(1);
  await page.waitForTimeout(1_000);

  expect(problems).toEqual([]);
  expect(failedRequests).toEqual([]);
});

test('an unknown route shows the not-found page', async ({ page }) => {
  await page.goto('/ui/app/#nowhere');
  await expect(page.getByRole('heading', { level: 1, name: 'Page not found' })).toBeVisible();
  await page.getByRole('link', { name: 'Open the Dashboard' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible();
});
