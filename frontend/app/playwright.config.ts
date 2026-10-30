import { defineConfig, devices } from '@playwright/test';
import { E2E_NODE } from './tests/e2e/helpers/runtime.ts';

const PORT = 4174;

/** Real-page checks against the built app, served with the fixture-based fake backend. */
export default defineConfig({
  testDir: './tests/e2e',
  forbidOnly: true,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `${E2E_NODE} tests/support/fake-backend/fake-backend.mjs --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/ui/app/`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
