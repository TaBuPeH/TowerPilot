import { defineConfig } from '@playwright/test';

/** Source-CSS geometry checks need neither a build nor a backend. */
export default defineConfig({
  testDir: '..',
  testMatch: 'help-tip.spec.ts',
  forbidOnly: true,
  retries: 0,
  reporter: [['list']],
  use: { browserName: 'chromium', trace: 'off' },
  outputDir: '../../../test-results/help-tip-results',
});
