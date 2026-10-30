import { isolatedConfig } from './isolated-config.ts';

/**
 * The F6-11 Runs leftovers browser checks on their own port and build, so they can run beside
 * other authors' Playwright runs (the default config runs them too).
 * Build first: npx vite build --outDir playwright-report/runs-leftovers-build --emptyOutDir
 */
export default isolatedConfig({
  port: Number(process.env['TP_RUNS_LEFTOVERS_E2E_PORT'] ?? '4216'),
  testMatch: 'runs-leftovers.spec.ts',
  buildDir: 'playwright-report/runs-leftovers-build',
  outputDir: '../playwright-report/runs-leftovers-e2e',
});
