import { isolatedConfig } from './isolated-config.ts';

/**
 * The F6-12 live shopping browser checks on their own port and build, so they can run beside
 * other authors' Playwright runs (the default config runs them too).
 * Build first: npx vite build --outDir playwright-report/runs-shopping-live-build --emptyOutDir
 */
export default isolatedConfig({
  port: Number(process.env['TP_SHOPPING_LIVE_E2E_PORT'] ?? '4221'),
  testMatch: 'runs-shopping-live.spec.ts',
  buildDir: 'playwright-report/runs-shopping-live-build',
  outputDir: '../playwright-report/runs-shopping-live-e2e',
  jsonReport: 'playwright-report/shopping-live-browser.json',
});
