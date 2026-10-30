import { expect, test, type Page } from '@playwright/test';
import { bodiesTo, openScenario, posts } from './helpers/helpers.ts';
import { firstInstallConfig } from '../support/emulatorsFixtures.ts';

const runnerReason = 'Automation is running; stop it on the Dashboard first';

async function publishRunner(page: Page, scenario = 'emulators_runner_listed') {
  await page.request.get(`/__fake/set?scenario=${scenario}`);
}

for (const boundAccount of [false, true]) {
  test(`F6M-C-01 first-install Use names the selected instance and ${boundAccount ? 'bound account' : 'unbound account'}; shown target equals recorded POST target`, async ({
    page,
  }) => {
    // Explicitly authorized labelled first-install config modification; POSTs use recorded answers.
    const config = firstInstallConfig(boundAccount);
    await page.route('**/api/config', (route) =>
      route.fulfill({ status: config.status, json: config.body }),
    );
    const errors = await openScenario(page, 'emulators_placeholder');
    await page.evaluate(() => {
      window.location.hash = '#emulators';
    });
    const target = page.locator('[data-region="emulatorsTarget"]');
    await expect(target).toContainText('No address is configured for this instance yet');
    const use = page.locator('[data-row="MuMu:0"]').getByRole('button', { name: 'Use this one' });
    await use.click();
    const dialog = page.getByRole('dialog');
    const account = boundAccount ? 'alpha' : 'none chosen';
    const consent = `Instance main (account ${account}) will use 127.0.0.1:16384 instead of not set yet`;
    await expect(dialog).toContainText(consent);
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(dialog).toBeHidden();
    expect(await posts(page)).toEqual([]);
    await use.click();
    const shown = await dialog.innerText();
    await dialog.getByRole('button', { name: 'Use it', exact: true }).click();
    await expect.poll(async () => (await bodiesTo(page, '/api/wizard/adopt')).length).toBe(1);
    const sent = (await bodiesTo(page, '/api/wizard/adopt'))[0] as {
      expect_instance: string;
      serial: string;
    };
    expect(sent).toMatchObject({
      expect_instance: 'main',
      serial: '127.0.0.1:16384',
      expect_placeholder: true,
    });
    expect(config.body).toMatchObject({
      active_instance: sent.expect_instance,
      instances: {
        [sent.expect_instance]: { serial: '', ...(boundAccount ? { account: 'alpha' } : {}) },
      },
    });
    expect(shown).toContain(
      `Instance ${sent.expect_instance} (account ${account}) will use ${sent.serial}`,
    );
    expect(errors).toEqual([]);
  });
}

test('F6M-C-02 Use closes when a recorded status lists a runner while consent is open; no adoption is sent', async ({
  page,
}) => {
  const errors = await openScenario(page, 'emulators');
  await page.evaluate(() => {
    window.location.hash = '#emulators';
  });
  const use = page.locator('[data-row="MuMu:0"]').getByRole('button', { name: 'Use this one' });
  await use.click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await publishRunner(page);
  await expect(use).toHaveAccessibleDescription(runnerReason);
  await expect(page.getByRole('dialog')).toBeHidden();
  await expect(use).toHaveAttribute('aria-disabled', 'true');
  await use.click({ force: true });
  expect(await posts(page)).toEqual([]);
  expect(errors).toEqual([]);
});

for (const surface of ['Emulators', 'Dashboard'] as const) {
  test(`F6M-C-02 ${surface} Reconnect rechecks the recorded runner status after a held click-time config read; no reconnect POST`, async ({
    page,
  }) => {
    const errors = await openScenario(page, 'emulators');
    if (surface === 'Emulators')
      await page.evaluate(() => {
        window.location.hash = '#emulators';
      });
    const reconnect = (
      surface === 'Emulators'
        ? page.locator('[data-region="emulatorsTarget"]')
        : page.locator('header.tp-topbar')
    ).getByRole('button', { name: 'Reconnect', exact: true });
    await expect(reconnect).not.toHaveAttribute('aria-disabled', 'true');
    let entered!: () => void;
    let release!: () => void;
    let returned!: () => void;
    const preflight = new Promise<void>((resolve) => {
      entered = resolve;
    });
    const resume = new Promise<void>((resolve) => {
      release = resolve;
    });
    const completed = new Promise<void>((resolve) => {
      returned = resolve;
    });
    await page.route(
      '**/api/config',
      async (route) => {
        // Capture the actual fake-backend config before changing scenarios; hold only its delivery.
        const response = await route.fetch();
        entered();
        await resume;
        await route.fulfill({ response });
        returned();
      },
      { times: 1 },
    );
    await reconnect.click();
    await preflight;
    await publishRunner(page);
    if (surface === 'Emulators') {
      await expect(
        page.locator('[data-row="MuMu:0"]').getByRole('button', { name: 'Use this one' }),
      ).toHaveAccessibleDescription(runnerReason);
    } else {
      // A fresh runner read disables another idle-only action while Reconnect is pending.
      await expect(page.locator('[data-region="control"]')).toContainText('orchestrator');
    }
    release();
    await completed;
    if (surface === 'Emulators')
      await expect(page.locator('[data-not-sent="process_listed"]')).toBeVisible();
    else await expect(page.getByText('Request not sent', { exact: true })).toBeVisible();
    expect(await bodiesTo(page, '/api/wizard/reconnect')).toEqual([]);
    expect(await posts(page)).toEqual([]);
    expect(errors).toEqual([]);
  });
}
