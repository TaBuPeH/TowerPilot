import { isolatedConfig } from './isolated-config.ts';

/**
 * A2-7 AC1/AC3/AC5: This battle from the action projection, plus the Dashboard a11y checks, on an
 * isolated port and build (`vite build --outDir node_modules/.cache/actions-build`) so concurrent
 * authors' builds and Playwright runs never replace the page under test.
 */
export default isolatedConfig({
  port: Number(process.env['TP_ACTIONS_E2E_PORT'] ?? '4188'),
  testMatch: ['dashboard-actions.spec.ts', 'dashboard-a11y.spec.ts'],
  buildDir: 'node_modules/.cache/actions-build',
  outputDir: '../test-results/actions-e2e',
});
